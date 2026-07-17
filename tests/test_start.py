import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import start


class StartupOptionsTests(unittest.TestCase):
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
