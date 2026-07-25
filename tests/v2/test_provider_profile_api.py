import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.v2.bootstrap import build_test_runtime, fetch_provider_models
from backend.v2.bootstrap import test_provider_connection as run_provider_connection_test
from backend.v2.errors import V2Error
from backend.v2.services.provider_profiles import ProviderRevisionCredentials


class ProviderProfileApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = build_test_runtime(Path(self.temp.name), b"m" * 32)
        self.runtime.provider_tester = AsyncMock(
            return_value=(True, 15, True, "Connection successful")
        )
        app = FastAPI()
        self.runtime.install(app)
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
        self.assert_profile_not_found(response)

    def test_patch_rejects_explicit_null_without_creating_revision(self):
        profile = self.create_profile()
        for field in ("name", "baseUrl", "apiKey", "modelId", "temperature"):
            with self.subTest(field=field):
                response = self.client.patch(
                    f"/api/v2/provider-profiles/{profile['id']}", json={field: None}
                )
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["error"]["code"], "validation_error")
                current = self.client.get(
                    f"/api/v2/provider-profiles/{profile['id']}"
                ).json()
                self.assertEqual(current["revision"], 1)

    def test_patch_rejects_blank_values_without_creating_revision(self):
        profile = self.create_profile()
        cases = {
            "name": "   ",
            "baseUrl": "   ",
            "apiKey": "   ",
            "modelId": "   ",
            "temperature": "",
        }
        for field, value in cases.items():
            with self.subTest(field=field):
                response = self.client.patch(
                    f"/api/v2/provider-profiles/{profile['id']}", json={field: value}
                )
                self.assertEqual(response.status_code, 422, response.text)
                current = self.client.get(
                    f"/api/v2/provider-profiles/{profile['id']}"
                ).json()
                self.assertEqual(current["revision"], 1)

    def test_patch_omitted_key_preserves_key_and_revisions_are_monotonic(self):
        created = self.create_profile()
        first_mask = created["apiKeyMasked"]
        first = self.client.patch(
            f"/api/v2/provider-profiles/{created['id']}", json={"modelId": "model-b"}
        ).json()
        second = self.client.patch(
            f"/api/v2/provider-profiles/{created['id']}", json={"temperature": 0.2}
        ).json()

        self.assertEqual([created["revision"], first["revision"], second["revision"]], [1, 2, 3])
        self.assertEqual(first["apiKeyMasked"], first_mask)
        self.assertEqual(second["apiKeyMasked"], first_mask)
        credentials = self.runtime.provider_profile_service.get_revision_credentials(
            second["activeRevisionId"]
        )
        self.assertEqual(credentials.api_key, "secret-key")

    def test_missing_and_deleted_mutations_and_tests_return_not_found(self):
        created = self.create_profile()
        self.assertEqual(
            self.client.delete(f"/api/v2/provider-profiles/{created['id']}").status_code,
            204,
        )
        cases = (
            self.client.patch(
                "/api/v2/provider-profiles/missing", json={"modelId": "model-b"}
            ),
            self.client.delete("/api/v2/provider-profiles/missing"),
            self.client.post("/api/v2/provider-profiles/missing/test"),
            self.client.patch(
                f"/api/v2/provider-profiles/{created['id']}",
                json={"modelId": "model-b"},
            ),
            self.client.delete(f"/api/v2/provider-profiles/{created['id']}"),
            self.client.post(f"/api/v2/provider-profiles/{created['id']}/test"),
        )
        for response in cases:
            with self.subTest(url=response.request.url):
                self.assertEqual(response.status_code, 404)
                self.assert_profile_not_found(response)

    def test_connection_test_uses_active_immutable_revision(self):
        created = self.create_profile()
        updated = self.client.patch(
            f"/api/v2/provider-profiles/{created['id']}",
            json={"apiKey": "new-secret", "modelId": "model-b"},
        ).json()

        response = self.client.post(f"/api/v2/provider-profiles/{created['id']}/test")

        self.assertEqual(response.status_code, 200)
        credentials = self.runtime.provider_tester.await_args.args[0]
        self.assertEqual(credentials.id, updated["activeRevisionId"])
        self.assertEqual(credentials.revision, 2)
        self.assertEqual(credentials.api_key, "new-secret")
        self.assertEqual(credentials.model_id, "model-b")

    def assert_profile_not_found(self, response):
        error = response.json()["error"]
        self.assertEqual(
            set(error), {"code", "message", "details", "requestId"}, response.text
        )
        self.assertEqual(error["code"], "provider_profile_not_found")
        self.assertEqual(error["details"], {})
        self.assertEqual(response.headers["X-Request-ID"], error["requestId"])


class ProviderConnectionTesterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.credentials = ProviderRevisionCredentials(
            id="revision-id",
            profile_id="profile-id",
            revision=1,
            base_url="https://api.example/v1",
            api_key="secret-key",
            model_id="model-a",
            temperature=0.1,
        )

    async def test_client_construction_failure_is_sanitized(self):
        with patch(
            "backend.v2.bootstrap.OpenAI",
            side_effect=RuntimeError("upstream body containing secret-key"),
        ):
            with self.assertRaises(V2Error) as caught:
                await run_provider_connection_test(self.credentials)

        self.assertEqual(caught.exception.code, "provider_connection_failed")
        self.assertEqual(caught.exception.status_code, 502)
        self.assertNotIn("secret-key", str(caught.exception))

    async def test_models_list_runs_in_thread_and_monotonic_latency_matches_model(self):
        client = self.fake_client(SimpleNamespace(data=[SimpleNamespace(id="model-a")]))
        calls = []

        async def run_in_thread(function):
            calls.append(function)
            return function()

        with (
            patch("backend.v2.bootstrap.OpenAI", return_value=client) as openai_cls,
            patch("backend.v2.bootstrap.asyncio.to_thread", side_effect=run_in_thread),
            patch(
                "backend.v2.bootstrap.time",
                new=SimpleNamespace(monotonic=Mock(side_effect=[10.0, 10.123])),
            ),
        ):
            result = await run_provider_connection_test(self.credentials)

        self.assertEqual(result, (True, 123, True, "Connection successful"))
        openai_cls.assert_called_once_with(
            api_key="secret-key", base_url="https://api.example/v1"
        )
        self.assertEqual(calls, [client.models.list, client.close])

    async def test_missing_model_is_reported_without_failing_connection(self):
        client = self.fake_client(SimpleNamespace(data=[SimpleNamespace(id="model-z")]))
        with (
            patch("backend.v2.bootstrap.OpenAI", return_value=client),
            patch(
                "backend.v2.bootstrap.time",
                new=SimpleNamespace(monotonic=Mock(side_effect=[1.0, 1.001])),
            ),
        ):
            result = await run_provider_connection_test(self.credentials)

        self.assertEqual(result[0:3], (True, 1, False))
        self.assertIn("not found", result[3])
        client.close.assert_called_once_with()

    async def test_list_parse_and_close_failures_are_sanitized_and_close_when_possible(self):
        list_client = self.fake_client()
        list_client.models.list.side_effect = RuntimeError("list upstream secret-key")
        parse_client = self.fake_client(SimpleNamespace(data=[object()]))
        close_client = self.fake_client(SimpleNamespace(data=[]))
        close_client.close.side_effect = RuntimeError("close upstream secret-key")

        for stage, client in (
            ("list", list_client),
            ("parse", parse_client),
            ("close", close_client),
        ):
            with self.subTest(stage=stage):
                with patch("backend.v2.bootstrap.OpenAI", return_value=client):
                    with self.assertRaises(V2Error) as caught:
                        await run_provider_connection_test(self.credentials)
                self.assertEqual(caught.exception.code, "provider_connection_failed")
                self.assertEqual(caught.exception.details, {})
                self.assertNotIn("secret-key", str(caught.exception))
                client.close.assert_called_once_with()

    @staticmethod
    def fake_client(response=None):
        client = SimpleNamespace()
        client.models = SimpleNamespace(list=Mock(return_value=response))
        client.close = Mock()
        return client


class ProviderModelFetcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_normalizes_models_and_closes_client(self):
        client = ProviderConnectionTesterTests.fake_client(
            SimpleNamespace(
                data=[
                    SimpleNamespace(id="z-model", name="Zed"),
                    SimpleNamespace(id="A-model", name=""),
                    SimpleNamespace(id="z-model", name="Duplicate"),
                    SimpleNamespace(id="  ", name="Blank"),
                    SimpleNamespace(id="x" * 513, name="Too long"),
                ]
            )
        )
        calls = []

        async def run_in_thread(function):
            calls.append(function)
            return function()

        with (
            patch("backend.v2.bootstrap.OpenAI", return_value=client) as openai_cls,
            patch("backend.v2.bootstrap.asyncio.to_thread", side_effect=run_in_thread),
            patch(
                "backend.v2.bootstrap.time",
                new=SimpleNamespace(monotonic=Mock(side_effect=[10.0, 10.125])),
            ),
        ):
            result = await fetch_provider_models(
                "https://api.example/v1", "secret-key"
            )

        self.assertEqual(
            result,
            ([("A-model", "A-model"), ("z-model", "Zed")], 125),
        )
        openai_cls.assert_called_once_with(
            api_key="secret-key",
            base_url="https://api.example/v1",
            timeout=15.0,
        )
        self.assertEqual(calls, [client.models.list, client.close])

    async def test_caps_normalized_models_at_two_thousand(self):
        client = ProviderConnectionTesterTests.fake_client(
            SimpleNamespace(
                data=[
                    SimpleNamespace(id=f"model-{index:04d}", name=f"Model {index}")
                    for index in range(2001)
                ]
            )
        )
        with patch("backend.v2.bootstrap.OpenAI", return_value=client):
            models, _ = await fetch_provider_models(
                "https://api.example/v1", "secret-key"
            )

        self.assertEqual(len(models), 2000)
        self.assertEqual(models[0], ("model-0000", "Model 0"))
        self.assertEqual(models[-1], ("model-1999", "Model 1999"))

    async def test_list_failure_still_closes_client(self):
        client = ProviderConnectionTesterTests.fake_client()
        client.models.list.side_effect = RuntimeError("provider unavailable")

        with patch("backend.v2.bootstrap.OpenAI", return_value=client):
            with self.assertRaises(RuntimeError):
                await fetch_provider_models(
                    "https://api.example/v1", "secret-key"
                )

        client.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
