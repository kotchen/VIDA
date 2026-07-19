from __future__ import annotations

import io
import logging
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.v2.bootstrap import build_test_runtime
from backend.v2.jobs.ai import AIProcessor
from backend.v2.services.provider_profiles import ProviderRevisionCredentials


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

    def test_real_ai_processor_and_summarizer_never_log_provider_failure_details(self):
        secrets = (
            "ai-processor-api-key-secret",
            "query-token-secret",
            "authorization-header-secret",
            "provider-payload-secret",
        )
        credentials = ProviderRevisionCredentials(
            "revision", "profile", 1,
            f"https://provider.example/v1?token={secrets[1]}",
            secrets[0], "model", 0.1,
        )

        class Completions:
            def create(self, **kwargs):
                raise RuntimeError(
                    f"Authorization: Bearer {secrets[2]} url={credentials.base_url} "
                    f"body={secrets[3]} key={credentials.api_key}"
                )

        class Client:
            def __init__(self):
                self.chat = type("Chat", (), {"completions": Completions()})()

            def close(self):
                pass

        captured, handler, root, old_level = self._capture_all_logs()
        try:
            with patch("backend.summarizer.openai.OpenAI", return_value=Client()):
                result = asyncio.run(AIProcessor(credentials).optimize("raw transcript"))
        finally:
            self._stop_capture(handler, root, old_level)

        output = captured.getvalue()
        self.assertEqual(result, "raw transcript")
        self.assertIn("单块文本优化失败", output)
        for secret in secrets:
            self.assertNotIn(secret, output)
        self.assertNotIn("Authorization", output)

    def test_real_translator_and_transcriber_log_safe_stage_context_only(self):
        from backend.transcriber import Transcriber
        from backend.translator import Translator

        secrets = (
            "translator-api-key-secret",
            "translator-url-token-secret",
            "transcriber-provider-payload-secret",
        )

        class TranslatorCompletions:
            def create(self, **kwargs):
                raise RuntimeError(
                    f"Authorization Bearer {secrets[0]} "
                    f"https://provider.example/v1?token={secrets[1]}"
                )

        class TranslatorClient:
            def __init__(self, **kwargs):
                self.chat = type(
                    "Chat", (), {"completions": TranslatorCompletions()}
                )()

        class TranscriberModel:
            def transcribe(self, *args, **kwargs):
                raise RuntimeError(f"provider body={secrets[2]}")

        captured, handler, root, old_level = self._capture_all_logs()
        try:
            with patch("backend.translator.OpenAI", TranslatorClient):
                translated = asyncio.run(
                    Translator(
                        api_key=secrets[0],
                        base_url=f"https://provider.example/v1?token={secrets[1]}",
                    ).translate_text("hello", "zh", "en")
                )
            transcriber = Transcriber()
            transcriber.model = TranscriberModel()
            with tempfile.NamedTemporaryFile() as audio:
                with self.assertRaises(Exception):
                    asyncio.run(transcriber.transcribe(audio.name))
        finally:
            self._stop_capture(handler, root, old_level)

        output = captured.getvalue()
        self.assertEqual(translated, "hello")
        self.assertIn("单文本翻译失败", output)
        self.assertIn("转录失败", output)
        for secret in secrets:
            self.assertNotIn(secret, output)
        self.assertNotIn("Authorization", output)

    def test_video_source_logging_omits_credentials_path_and_query(self):
        from backend.video_processor import VideoProcessor

        source = "https://user:password-secret@media.example/private-token/video?token=query-secret"

        class YoutubeDL:
            def __init__(self, options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def extract_info(self, url, download):
                return {"title": "provider-title-secret", "subtitles": {}, "automatic_captions": {}}

        captured, handler, root, old_level = self._capture_all_logs()
        try:
            with patch("backend.video_processor.yt_dlp.YoutubeDL", YoutubeDL):
                result = asyncio.run(
                    VideoProcessor().fetch_subtitles(source, self.data_dir)
                )
        finally:
            self._stop_capture(handler, root, old_level)

        output = captured.getvalue()
        self.assertEqual(result[0], None)
        self.assertIn("视频无可用字幕", output)
        self.assertIn("media.example", output)
        for secret in ("password-secret", "private-token", "query-secret", "provider-title-secret"):
            self.assertNotIn(secret, output)

    @staticmethod
    def _capture_all_logs():
        captured = io.StringIO()
        handler = logging.StreamHandler(captured)
        root = logging.getLogger()
        old_level = root.level
        root.setLevel(logging.INFO)
        root.addHandler(handler)
        return captured, handler, root, old_level

    @staticmethod
    def _stop_capture(handler, root, old_level):
        root.removeHandler(handler)
        root.setLevel(old_level)
        handler.close()


if __name__ == "__main__":
    unittest.main()
