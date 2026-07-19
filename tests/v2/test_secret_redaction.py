from __future__ import annotations

import io
import logging
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.v2.bootstrap import build_test_runtime


class SecretRedactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name)
        self.runtime = build_test_runtime(self.data_dir, b"s" * 32)
        app = FastAPI()
        self.runtime.install(app)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_plaintext_provider_key_never_appears_in_database_logs_or_api(self):
        secret = "super-secret-provider-key"
        captured = io.StringIO()
        handler = logging.StreamHandler(captured)
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            created = self.client.post(
                "/api/v2/provider-profiles",
                json={
                    "name": "Primary",
                    "baseUrl": "https://api.example/v1?token=query-secret",
                    "apiKey": secret,
                    "modelId": "model",
                    "temperature": 0.1,
                },
            )
            self.assertEqual(created.status_code, 201, created.text)

            async def fail_with_secret(credentials):
                raise RuntimeError(f"upstream rejected {credentials.api_key}")

            self.runtime.provider_tester = fail_with_secret
            failed = self.client.post(
                f"/api/v2/provider-profiles/{created.json()['id']}/test"
            )
            listed = self.client.get("/api/v2/provider-profiles")
        finally:
            root.removeHandler(handler)
            handler.close()

        database_bytes = self.runtime.settings.database_path.read_bytes()
        response_text = created.text + failed.text + listed.text
        self.assertNotIn(secret.encode("utf-8"), database_bytes)
        self.assertNotIn(secret, captured.getvalue())
        self.assertNotIn(secret, response_text)
        self.assertNotIn("query-secret", captured.getvalue())
        self.assertEqual(failed.json()["error"]["code"], "provider_connection_failed")

    def test_startup_log_reports_only_safe_operational_settings(self):
        secret = "log-secret-that-must-not-appear"
        profile = self.runtime.require_provider_profiles().create_profile(
            "Primary", "https://api.example/v1?token=url-secret", secret, "model", 0.1
        )
        captured = io.StringIO()
        handler = logging.StreamHandler(captured)
        logger = logging.getLogger("backend.v2.container")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            self.runtime.initialize()
            self.runtime.log_startup_settings()
        finally:
            logger.removeHandler(handler)
            handler.close()

        output = captured.getvalue()
        self.assertIn(str(self.data_dir), output)
        self.assertIn("workers=2", output)
        self.assertIn("upload_limit_bytes=5368709120", output)
        self.assertNotIn(secret, output)
        self.assertNotIn("url-secret", output)
        self.assertNotIn(profile.active_revision_id, output)
        self.assertNotIn(str(self.runtime.settings.master_key), output)

    def test_unhandled_v2_error_logs_sanitized_traceback_metadata(self):
        secret = "exception-message-secret"

        @self.client.app.get("/api/v2/test-unhandled-error")
        def fail():
            raise RuntimeError(secret)

        captured = io.StringIO()
        handler = logging.StreamHandler(captured)
        logger = logging.getLogger("backend.v2.errors")
        logger.addHandler(handler)
        logger.setLevel(logging.ERROR)
        try:
            response = self.client.get("/api/v2/test-unhandled-error")
        finally:
            logger.removeHandler(handler)
            handler.close()

        output = captured.getvalue()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"]["code"], "internal_error")
        self.assertIn("Unhandled v2 request error", output)
        self.assertIn("RuntimeError", output)
        self.assertIn("fail@", output)
        self.assertNotIn(secret, output)
        self.assertNotIn(secret, response.text)


if __name__ == "__main__":
    unittest.main()
