import asyncio
import importlib
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class TranscriberAsyncTests(unittest.IsolatedAsyncioTestCase):
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
