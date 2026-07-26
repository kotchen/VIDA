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
        self.app = FastAPI()
        runtime.install(self.app)
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_validation_errors_use_v2_envelope(self):
        response = self.client.post("/api/v2/provider-profiles", json={})
        self.assertEqual(response.status_code, 422)
        error = self.assert_v2_envelope(response, "validation_error")
        self.assertIsInstance(error["details"]["errors"], list)

    def test_request_id_is_preserved(self):
        response = self.client.post(
            "/api/v2/provider-profiles",
            json={},
            headers={"X-Request-ID": "caller-request-id"},
        )
        self.assertEqual(response.json()["error"]["requestId"], "caller-request-id")
        self.assertEqual(response.headers["X-Request-ID"], "caller-request-id")

    def test_router_generated_404_and_405_use_v2_envelope(self):
        cases = (
            (self.client.get("/api/v2/not-a-route"), 404, "not_found"),
            (self.client.put("/api/v2/provider-profiles"), 405, "method_not_allowed"),
        )
        for response, status_code, code in cases:
            with self.subTest(status_code=status_code):
                self.assertEqual(response.status_code, status_code)
                self.assert_v2_envelope(response, code)

    def test_unexpected_v2_failure_is_sanitized(self):
        @self.app.get("/api/v2/explode")
        def explode():
            raise RuntimeError("upstream body containing secret-key")

        response = self.client.get(
            "/api/v2/explode", headers={"X-Request-ID": "explode-id"}
        )

        self.assertEqual(response.status_code, 500)
        error = self.assert_v2_envelope(response, "internal_error")
        self.assertEqual(error["message"], "Internal server error")
        self.assertEqual(error["details"], {})
        self.assertEqual(error["requestId"], "explode-id")
        self.assertNotIn("secret-key", response.text)

    def test_v2_path_matching_uses_an_exact_segment_boundary(self):
        for path in ("/api/v1/not-a-route", "/api/v20", "/api/v2x"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json(), {"detail": "Not Found"})
                self.assertNotIn("X-Request-ID", response.headers)

    def test_openapi_exposes_frontend_integration_contract(self):
        schema = self.client.get("/openapi.json").json()
        paths = schema["paths"]

        self.assertIn("/api/v2/events", paths)
        self.assertIn("delete", paths["/api/v2/episodes/{episode_id}"])
        patch = paths[
            "/api/v2/episodes/{episode_id}/chapters/{chapter_id}"
        ]["patch"]
        body = patch["requestBody"]["content"]["application/json"]["schema"]
        reference = body["$ref"].removeprefix("#/").split("/")
        chapter_update = schema
        for part in reference:
            chapter_update = chapter_update[part]
        self.assertIn("bookmarked", chapter_update["properties"])

    def assert_v2_envelope(self, response, code):
        self.assertEqual(response.headers.get("content-type"), "application/json")
        self.assertEqual(set(response.json()), {"error"})
        error = response.json()["error"]
        self.assertEqual(
            set(error), {"code", "message", "details", "requestId"}, response.text
        )
        self.assertEqual(error["code"], code)
        self.assertIsInstance(error["message"], str)
        self.assertIsInstance(error["details"], dict)
        self.assertTrue(error["requestId"])
        self.assertEqual(response.headers["X-Request-ID"], error["requestId"])
        return error


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
