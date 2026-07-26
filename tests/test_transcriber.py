import asyncio
import importlib
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class TranscriberAsyncTests(unittest.IsolatedAsyncioTestCase):
    def test_huggingface_defaults_preserve_explicit_operator_values(self):
        from backend.whisper_environment import configure_huggingface_environment

        environ = {
            "HF_HUB_DISABLE_XET": "0",
            "HF_HUB_DOWNLOAD_TIMEOUT": "90",
        }
        configure_huggingface_environment(environ)

        self.assertEqual(environ["HF_HUB_DISABLE_XET"], "0")
        self.assertEqual(environ["HF_HUB_DOWNLOAD_TIMEOUT"], "90")
        self.assertEqual(environ["HF_HUB_ETAG_TIMEOUT"], "10")

    def test_transcriber_reads_model_and_timeout_from_environment(self):
        fake_faster_whisper = types.ModuleType("faster_whisper")
        fake_faster_whisper.WhisperModel = object
        with patch.dict(sys.modules, {"faster_whisper": fake_faster_whisper}), patch.dict(
            os.environ,
            {
                "WHISPER_MODEL_SIZE": "small",
                "WHISPER_MODEL_LOAD_TIMEOUT_SEC": "42",
            },
            clear=False,
        ):
            sys.modules.pop("backend.transcriber", None)
            module = importlib.import_module("backend.transcriber")
            transcriber = module.Transcriber()

        self.assertEqual(transcriber.model_size, "small")
        self.assertEqual(transcriber.model_load_timeout_sec, 42.0)

    def test_huggingface_defaults_are_set_before_whisper_import(self):
        observed = []

        class ObservingModule(types.ModuleType):
            def __getattribute__(self, name):
                if name == "WhisperModel":
                    observed.append(os.environ.get("HF_HUB_DISABLE_XET"))
                return super().__getattribute__(name)

        fake_faster_whisper = ObservingModule("faster_whisper")
        fake_faster_whisper.WhisperModel = object
        with patch.dict(sys.modules, {"faster_whisper": fake_faster_whisper}), patch.dict(
            os.environ, {}, clear=False
        ):
            os.environ.pop("HF_HUB_DISABLE_XET", None)
            sys.modules.pop("backend.transcriber", None)
            importlib.import_module("backend.transcriber")

        self.assertEqual(observed, ["1"])

    def test_transcriber_rejects_invalid_model_load_timeouts(self):
        fake_faster_whisper = types.ModuleType("faster_whisper")
        fake_faster_whisper.WhisperModel = object
        with patch.dict(sys.modules, {"faster_whisper": fake_faster_whisper}):
            sys.modules.pop("backend.transcriber", None)
            module = importlib.import_module("backend.transcriber")

        for invalid in (0, -1, float("nan"), float("inf"), "invalid"):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                ValueError,
                "^WHISPER_MODEL_LOAD_TIMEOUT_SEC must be a positive number$",
            ):
                module.Transcriber(model_load_timeout_sec=invalid)

    async def test_preload_timeout_is_sanitized(self):
        fake_faster_whisper = types.ModuleType("faster_whisper")
        fake_faster_whisper.WhisperModel = object
        with patch.dict(sys.modules, {"faster_whisper": fake_faster_whisper}):
            sys.modules.pop("backend.transcriber", None)
            module = importlib.import_module("backend.transcriber")

        release = threading.Event()

        class SlowModel:
            def __init__(self, *args, **kwargs):
                release.wait(timeout=1)

        module.WhisperModel = SlowModel
        transcriber = module.Transcriber(model_load_timeout_sec=0.02)
        try:
            with self.assertRaisesRegex(
                RuntimeError, "^Whisper model initialization timed out$"
            ):
                await transcriber.preload()
        finally:
            release.set()
            await asyncio.sleep(0.05)

    async def test_preload_constructor_failure_is_sanitized(self):
        fake_faster_whisper = types.ModuleType("faster_whisper")
        fake_faster_whisper.WhisperModel = object
        with patch.dict(sys.modules, {"faster_whisper": fake_faster_whisper}):
            sys.modules.pop("backend.transcriber", None)
            module = importlib.import_module("backend.transcriber")

        class FailingModel:
            def __init__(self, *args, **kwargs):
                raise RuntimeError(
                    "https://proxy.example/secret /private/cache/model.bin"
                )

        module.WhisperModel = FailingModel
        transcriber = module.Transcriber()
        with self.assertRaisesRegex(
            RuntimeError, "^Whisper model initialization failed$"
        ) as caught:
            await transcriber.preload()

        self.assertNotIn("proxy.example", str(caught.exception))
        self.assertNotIn("/private/cache", str(caught.exception))

    async def test_legacy_markdown_keeps_headings_timestamps_and_language(self):
        fake_faster_whisper = types.ModuleType("faster_whisper")
        fake_faster_whisper.WhisperModel = object
        with patch.dict(sys.modules, {"faster_whisper": fake_faster_whisper}):
            sys.modules.pop("backend.transcriber", None)
            module = importlib.import_module("backend.transcriber")

        class Model:
            def transcribe(self, *args, **kwargs):
                segments = [types.SimpleNamespace(start=1.2, end=65.9, text=" Hello ")]
                info = types.SimpleNamespace(language="en", language_probability=0.876)
                return iter(segments), info

        transcriber = module.Transcriber()
        transcriber.model = Model()
        with tempfile.TemporaryDirectory() as temp_dir:
            audio = Path(temp_dir) / "audio.wav"
            audio.write_bytes(b"audio")
            result = await transcriber.transcribe(str(audio))

        self.assertEqual(
            result,
            "# Video Transcription\n\n"
            "**Detected Language:** en\n"
            "**Language Probability:** 0.88\n\n"
            "## Transcription Content\n\n"
            "**[00:01 - 01:05]**\n\nHello\n",
        )
        self.assertEqual(transcriber.get_detected_language(), "en")

    async def test_model_initialization_does_not_block_event_loop(self):
        fake_faster_whisper = types.ModuleType("faster_whisper")
        fake_faster_whisper.WhisperModel = object
        with patch.dict(sys.modules, {"faster_whisper": fake_faster_whisper}):
            sys.modules.pop("backend.transcriber", None)
            module = importlib.import_module("backend.transcriber")

        constructor_started = threading.Event()
        release_constructor = threading.Event()

        class SlowModel:
            def __init__(self, *args, **kwargs):
                constructor_started.set()
                release_constructor.wait(timeout=1)

            def transcribe(self, *args, **kwargs):
                info = types.SimpleNamespace(language="en", language_probability=1.0)
                return [], info

        module.WhisperModel = SlowModel
        transcriber = module.Transcriber()

        with tempfile.TemporaryDirectory() as temp_dir:
            audio = Path(temp_dir) / "audio.m4a"
            audio.write_bytes(b"audio")
            started_at = time.monotonic()
            task = asyncio.create_task(transcriber.transcribe(str(audio)))
            started = await asyncio.wait_for(
                asyncio.to_thread(constructor_started.wait), timeout=1.5
            )
            self.assertTrue(started)
            release_constructor.set()
            await asyncio.wait_for(task, timeout=0.5)

        self.assertLess(time.monotonic() - started_at, 0.5)

    async def test_concurrent_model_initialization_constructs_once(self):
        fake_faster_whisper = types.ModuleType("faster_whisper")
        fake_faster_whisper.WhisperModel = object
        with patch.dict(sys.modules, {"faster_whisper": fake_faster_whisper}):
            sys.modules.pop("backend.transcriber", None)
            module = importlib.import_module("backend.transcriber")

        constructor_started = threading.Event()
        release_constructor = threading.Event()
        constructor_calls = 0

        class SlowModel:
            def __init__(self, *args, **kwargs):
                nonlocal constructor_calls
                constructor_calls += 1
                constructor_started.set()
                release_constructor.wait(timeout=1)

        module.WhisperModel = SlowModel
        transcriber = module.Transcriber()

        first = asyncio.create_task(transcriber._load_model())
        started = await asyncio.wait_for(
            asyncio.to_thread(constructor_started.wait), timeout=0.5
        )
        self.assertTrue(started)
        second = asyncio.create_task(transcriber._load_model())
        await asyncio.sleep(0.05)
        release_constructor.set()
        await asyncio.gather(first, second)

        self.assertEqual(constructor_calls, 1)


if __name__ == "__main__":
    unittest.main()
