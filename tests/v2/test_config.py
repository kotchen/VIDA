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
        self.assertIsNone(settings.platform_egress_proxy)

    def test_platform_egress_proxy_is_optional_and_validated(self):
        settings = V2Settings.from_environ(
            {
                "VIDA_PROFILE_MASTER_KEY": self.key,
                "V2_PLATFORM_EGRESS_PROXY": "http://127.0.0.1:7897",
            },
            Path("."),
        )
        self.assertEqual(settings.platform_egress_proxy, "http://127.0.0.1:7897")

        for invalid in (
            "127.0.0.1:7897",
            "socks5://127.0.0.1:7897",
            "http://user:pass@127.0.0.1:7897",
            "http://127.0.0.1:99999",
            "http://127.0.0.1",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "V2_PLATFORM_EGRESS_PROXY"):
                    V2Settings.from_environ(
                        {
                            "VIDA_PROFILE_MASTER_KEY": self.key,
                            "V2_PLATFORM_EGRESS_PROXY": invalid,
                        },
                        Path("."),
                    )

    def test_rejects_invalid_worker_count_and_master_key(self):
        with self.assertRaisesRegex(ValueError, "V2_MAX_CONCURRENT_JOBS"):
            V2Settings.from_environ(
                {"VIDA_PROFILE_MASTER_KEY": self.key, "V2_MAX_CONCURRENT_JOBS": "0"},
                Path("."),
            )
        with self.assertRaisesRegex(ValueError, "VIDA_PROFILE_MASTER_KEY"):
            V2Settings.from_environ({"VIDA_PROFILE_MASTER_KEY": "bad"}, Path("."))

    def test_rejects_non_urlsafe_and_noncanonical_master_keys(self):
        standard_base64_key = base64.b64encode(b"\xfb" * 32).decode("ascii")
        self.assertRegex(standard_base64_key, r"[+/]")

        for invalid_key in (
            standard_base64_key,
            self.key + "!",
            self.key + "=",
        ):
            with self.subTest(invalid_key=invalid_key):
                with self.assertRaisesRegex(
                    ValueError, "VIDA_PROFILE_MASTER_KEY must be URL-safe Base64"
                ):
                    V2Settings.from_environ(
                        {"VIDA_PROFILE_MASTER_KEY": invalid_key}, Path(".")
                    )
