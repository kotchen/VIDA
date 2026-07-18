import tempfile
import subprocess
import sys
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.v2.bootstrap import build_test_runtime


class V2ErrorContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        runtime = build_test_runtime(Path(self.temp.name), b"m" * 32)
        app = FastAPI()
        runtime.install(app)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_validation_errors_use_v2_envelope(self):
        response = self.client.post("/api/v2/provider-profiles", json={})
        self.assertEqual(response.status_code, 422)
        error = response.json()["error"]
        self.assertEqual(error["code"], "validation_error")
        self.assertIn("requestId", error)
        self.assertIsInstance(error["details"]["errors"], list)

    def test_request_id_is_preserved(self):
        response = self.client.post(
            "/api/v2/provider-profiles",
            json={},
            headers={"X-Request-ID": "caller-request-id"},
        )
        self.assertEqual(response.json()["error"]["requestId"], "caller-request-id")
        self.assertEqual(response.headers["X-Request-ID"], "caller-request-id")


class V2BootstrapImportTests(unittest.TestCase):
    def test_top_level_v2_import_works_with_only_backend_on_sys_path(self):
        backend_dir = Path(__file__).resolve().parents[2] / "backend"
        code = (
            "import sys; "
            f"sys.path.insert(0, {str(backend_dir)!r}); "
            "from v2.bootstrap import install_v2; "
            "print(install_v2.__name__)"
        )
        with tempfile.TemporaryDirectory() as cwd:
            completed = subprocess.run(
                [sys.executable, "-c", code],
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "install_v2")


if __name__ == "__main__":
    unittest.main()
