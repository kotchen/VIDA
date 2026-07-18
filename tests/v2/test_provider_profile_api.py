import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.v2.bootstrap import build_test_runtime
from backend.v2.bootstrap import test_provider_connection as run_provider_connection_test
from backend.v2.errors import V2Error
from backend.v2.services.provider_profiles import ProviderRevisionCredentials


class ProviderProfileApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        runtime = build_test_runtime(Path(self.temp.name), b"m" * 32)
        runtime.provider_tester = AsyncMock(
            return_value=(True, 15, True, "Connection successful")
        )
        app = FastAPI()
        runtime.install(app)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def create_profile(self):
        response = self.client.post(
            "/api/v2/provider-profiles",
            json={
                "name": "Primary",
                "baseUrl": "https://api.example/v1",
                "apiKey": "secret-key",
                "modelId": "model-a",
                "temperature": 0.1,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_crud_never_returns_plaintext_key(self):
        body = self.create_profile()
        self.assertNotIn("apiKey", body)
        self.assertTrue(body["hasApiKey"])
        self.assertTrue(body["apiKeyMasked"].endswith("-key"))
        profile_id = body["id"]

        listed = self.client.get("/api/v2/provider-profiles").json()
        self.assertEqual([item["id"] for item in listed], [profile_id])
        self.assertNotIn("apiKey", listed[0])
        fetched = self.client.get(f"/api/v2/provider-profiles/{profile_id}").json()
        self.assertEqual(fetched["id"], profile_id)
        self.assertNotIn("apiKey", fetched)

        patched_response = self.client.patch(
            f"/api/v2/provider-profiles/{profile_id}", json={"modelId": "model-b"}
        )
        self.assertEqual(patched_response.status_code, 200, patched_response.text)
        patched = patched_response.json()
        self.assertEqual(patched["revision"], 2)
        self.assertEqual(patched["modelId"], "model-b")
        self.assertNotIn("apiKey", patched)

        deleted = self.client.delete(f"/api/v2/provider-profiles/{profile_id}")
        self.assertEqual(deleted.status_code, 204, deleted.text)
        self.assertEqual(deleted.content, b"")

    def test_connection_test_is_sanitized(self):
        created = self.create_profile()
        response = self.client.post(
            f"/api/v2/provider-profiles/{created['id']}/test"
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(),
            {
                "ok": True,
                "latencyMs": 15,
                "modelAvailable": True,
                "message": "Connection successful",
            },
        )

    def test_missing_profile_uses_v2_error_envelope(self):
        response = self.client.get("/api/v2/provider-profiles/missing")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "provider_profile_not_found")
        self.assertIn("requestId", response.json()["error"])


class ProviderConnectionTesterTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_construction_failure_is_sanitized(self):
        credentials = ProviderRevisionCredentials(
            id="revision-id",
            profile_id="profile-id",
            revision=1,
            base_url="https://api.example/v1",
            api_key="secret-key",
            model_id="model-a",
            temperature=0.1,
        )
        with patch(
            "backend.v2.bootstrap.OpenAI",
            side_effect=RuntimeError("upstream body containing secret-key"),
        ):
            with self.assertRaises(V2Error) as caught:
                await run_provider_connection_test(credentials)

        self.assertEqual(caught.exception.code, "provider_connection_failed")
        self.assertEqual(caught.exception.status_code, 502)
        self.assertNotIn("secret-key", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
