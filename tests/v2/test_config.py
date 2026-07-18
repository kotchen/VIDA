import base64
import unittest
from pathlib import Path

from backend.v2.config import V2Settings


class V2SettingsTests(unittest.TestCase):
    def setUp(self):
        self.key = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")

    def test_defaults_and_paths(self):
        settings = V2Settings.from_environ(
            {"VIDA_PROFILE_MASTER_KEY": self.key}, Path("D:/vida")
        )
        self.assertEqual(settings.max_concurrent_jobs, 2)
        self.assertEqual(settings.upload_max_bytes, 5 * 1024**3)
        self.assertEqual(settings.data_dir, Path("D:/vida/data/v2"))
        self.assertEqual(settings.master_key, b"k" * 32)

    def test_rejects_invalid_worker_count_and_master_key(self):
        with self.assertRaisesRegex(ValueError, "V2_MAX_CONCURRENT_JOBS"):
            V2Settings.from_environ(
                {"VIDA_PROFILE_MASTER_KEY": self.key, "V2_MAX_CONCURRENT_JOBS": "0"},
                Path("."),
            )
        with self.assertRaisesRegex(ValueError, "VIDA_PROFILE_MASTER_KEY"):
            V2Settings.from_environ({"VIDA_PROFILE_MASTER_KEY": "bad"}, Path("."))
