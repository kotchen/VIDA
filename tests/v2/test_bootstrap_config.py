from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yt_dlp.globals import plugin_dirs

from backend.v2.bootstrap import _build_runtime, build_test_runtime
from backend.v2.config import V2Settings
from backend.v2.jobs.source_ingest import SecureDownloader


class BootstrapYtDlpTests(unittest.TestCase):
    def test_runtime_uses_shared_transcriber_and_its_readiness(self):
        captured = {}

        class SharedTranscriber:
            def __init__(self):
                self.preload_calls = 0

            async def preload(self):
                self.preload_calls += 1

        class Pipeline:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        shared = SharedTranscriber()
        with tempfile.TemporaryDirectory() as temporary, patch(
            "backend.v2.bootstrap.EpisodePipeline", Pipeline
        ):
            directory = Path(temporary)
            settings = V2Settings(
                data_dir=directory,
                database_path=directory / "vida-v2.sqlite3",
                max_concurrent_jobs=2,
                upload_max_bytes=5 * 1024**3,
                master_key=b"k" * 32,
            )
            runtime = _build_runtime(
                settings,
                transcriber=shared,
                preload_model=True,
            )

        self.assertIs(captured["transcriber"], shared)
        asyncio.run(runtime._readiness())
        self.assertEqual(shared.preload_calls, 1)

    def test_injected_test_executor_does_not_preload_real_model(self):
        executor = object()
        with tempfile.TemporaryDirectory() as temporary:
            runtime = build_test_runtime(
                Path(temporary), b"k" * 32, executor=executor
            )

        self.assertIs(runtime.job_executor, executor)
        self.assertIsNone(runtime._readiness)

    def test_plugins_are_disabled_before_downloader_construction(self):
        observed = []
        original = list(plugin_dirs.value)

        class Downloader:
            _PLATFORM_HOSTS = ()

            def __init__(self, validator, **kwargs):
                observed.append(tuple(plugin_dirs.value))

        try:
            plugin_dirs.value = ["default"]
            with tempfile.TemporaryDirectory() as temporary, patch(
                "backend.v2.bootstrap.YtDlpDownloader", Downloader
            ):
                build_test_runtime(Path(temporary), b"k" * 32)
        finally:
            plugin_dirs.value = original

        self.assertEqual(observed, [()])

    def test_downloader_uses_secure_validator_and_upload_limit(self):
        observed = []

        class Downloader:
            _PLATFORM_HOSTS = ()

            def __init__(self, validator, **kwargs):
                observed.append((validator, kwargs))

        with tempfile.TemporaryDirectory() as temporary, patch(
            "backend.v2.bootstrap.YtDlpDownloader", Downloader
        ):
            build_test_runtime(Path(temporary), b"k" * 32)

        self.assertEqual(len(observed), 1)
        validator, kwargs = observed[0]
        self.assertIsInstance(validator, SecureDownloader)
        self.assertEqual(kwargs, {"max_bytes": 5 * 1024**3})


if __name__ == "__main__":
    unittest.main()
