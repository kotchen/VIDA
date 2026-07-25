from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yt_dlp.globals import plugin_dirs

from backend.v2.bootstrap import build_test_runtime
from backend.v2.jobs.source_ingest import SecureDownloader


class BootstrapYtDlpTests(unittest.TestCase):
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
