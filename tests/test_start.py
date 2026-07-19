import base64
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import start


class StartupOptionsTests(unittest.TestCase):
    def test_uvicorn_access_log_is_disabled_to_avoid_query_secret_leaks(self):
        options = start.StartupOptions(False, 8000, 2, 5, "master-key")
        with (
            patch.object(start, "parse_startup_options", return_value=options),
            patch.object(start, "configure_ffmpeg_path"),
            patch.object(start, "check_dependencies", return_value=True),
            patch.object(start, "check_ffmpeg", return_value=True),
            patch.object(start, "setup_environment", return_value=True),
            patch.object(start.os, "chdir"),
            patch.object(start.subprocess, "run") as run,
        ):
            start.main()

        command = run.call_args.args[0]
        self.assertIn("--no-access-log", command)

    def test_v2_cli_values_override_environment(self):
        key = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")
        options = start.parse_startup_options(
            ["--max-concurrent-jobs", "3", "--profile-master-key", key],
            {
                "V2_MAX_CONCURRENT_JOBS": "2",
                "V2_UPLOAD_MAX_GB": "7",
                "VIDA_PROFILE_MASTER_KEY": "environment-key",
            },
        )
        self.assertEqual(options.max_concurrent_jobs, 3)
        self.assertEqual(options.v2_upload_max_gb, 7)
        self.assertEqual(options.profile_master_key, key)

    def test_cli_port_overrides_environment_port(self):
        options = start.parse_startup_options(
            ["--port", "8001"],
            {"PORT": "8000"},
        )

        self.assertEqual(options.port, 8001)

    def test_port_uses_environment_then_default(self):
        env_options = start.parse_startup_options([], {"PORT": "8010"})
        default_options = start.parse_startup_options([], {})

        self.assertEqual(env_options.port, 8010)
        self.assertEqual(default_options.port, 8000)

    def test_local_ffmpeg_bin_is_added_to_path(self):
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            bin_dir = project_root / "tools" / "ffmpeg" / "bin"
            bin_dir.mkdir(parents=True)
            (bin_dir / "ffmpeg.exe").touch()
            (bin_dir / "ffprobe.exe").touch()
            environ = {"PATH": "C:\\Windows\\System32"}

            configured = start.configure_ffmpeg_path(environ, project_root)

            self.assertEqual(configured, bin_dir)
            self.assertTrue(environ["PATH"].startswith(str(bin_dir) + os.pathsep))

    def test_explicit_ffmpeg_location_is_added_to_path(self):
        with TemporaryDirectory() as temp_dir:
            bin_dir = Path(temp_dir)
            (bin_dir / "ffmpeg.exe").touch()
            (bin_dir / "ffprobe.exe").touch()
            environ = {"PATH": "", "FFMPEG_LOCATION": str(bin_dir)}

            configured = start.configure_ffmpeg_path(environ, Path("unused"))

            self.assertEqual(configured, bin_dir)
            self.assertEqual(environ["PATH"], str(bin_dir))


if __name__ == "__main__":
    unittest.main()
