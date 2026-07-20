import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.v2.bootstrap import build_test_runtime


class EpisodeSubmissionApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name)
        self.runtime = build_test_runtime(self.data_dir, b"m" * 32)
        self.runtime.scheduler.notify = Mock()
        app = FastAPI()
        self.runtime.install(app)
        self.client = TestClient(app)
        self.profile = self.create_profile()

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

    def test_upload_is_queued_only_after_complete_write(self):
        response = self.client.post(
            "/api/v2/episodes",
            data={
                "providerProfileId": self.profile["id"],
                "summaryLanguage": "zh",
            },
            files={"file": ("clip.mp3", b"hello world", "audio/mpeg")},
        )

        self.assertEqual(response.status_code, 202, response.text)
        body = response.json()
        self.assertEqual(body["status"], "queued")
        self.assertEqual(body["queuePosition"], 1)
        self.assertEqual(body["sourceType"], "upload")
        self.assertEqual(response.headers["Location"], f"/api/v2/episodes/{body['id']}")
        source = self.data_dir / "episodes" / body["id"] / "source" / "clip.mp3"
        self.assertEqual(source.read_bytes(), b"hello world")
        self.assertEqual(list(self.data_dir.rglob("*.part")), [])
        with self.runtime.database.connect() as conn:
            episode = conn.execute(
                "SELECT source_path,current_job_id,provider_profile_id FROM episodes WHERE id=?",
                (body["id"],),
            ).fetchone()
            job = conn.execute(
                "SELECT id,status,provider_profile_revision_id FROM jobs WHERE episode_id=?",
                (body["id"],),
            ).fetchone()
        self.assertEqual(episode["source_path"], f"episodes/{body['id']}/source/clip.mp3")
        self.assertEqual(episode["provider_profile_id"], self.profile["id"])
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["provider_profile_revision_id"], self.profile["activeRevisionId"])
        self.assertEqual(episode["current_job_id"], job["id"])
        self.runtime.scheduler.notify.assert_called_once_with()

    def test_multipart_request_does_not_use_uploadfile_spool(self):
        with patch(
            "starlette.requests.Request.form",
            side_effect=AssertionError("request.form must not be called"),
        ):
            response = self.client.post(
                "/api/v2/episodes",
                data={
                    "providerProfileId": self.profile["id"],
                    "summaryLanguage": "zh",
                },
                files={"file": ("clip.mp3", b"hello", "audio/mpeg")},
            )

        self.assertEqual(response.status_code, 202, response.text)

    def test_json_url_submission_uses_same_queue_and_normalizes_url(self):
        response = self.client.post(
            "/api/v2/episodes",
            json={
                "sourceUrl": "https://example.com/video",
                "providerProfileId": self.profile["id"],
                "summaryLanguage": "en",
            },
        )

        self.assertEqual(response.status_code, 202, response.text)
        body = response.json()
        self.assertEqual(body["sourceType"], "url")
        self.assertEqual(body["status"], "queued")
        self.assertEqual(body["queuePosition"], 1)
        self.assertEqual(response.headers["Location"], f"/api/v2/episodes/{body['id']}")
        with self.runtime.database.connect() as conn:
            row = conn.execute(
                "SELECT source_url,source_path FROM episodes WHERE id=?", (body["id"],)
            ).fetchone()
        self.assertEqual(row["source_url"], "https://example.com/video")
        self.assertIsNone(row["source_path"])
        self.assertEqual(list((self.data_dir / "episodes").glob("*")), [])
        self.runtime.scheduler.notify.assert_called_once_with()

    def test_oversized_upload_removes_partial_file_and_creates_no_rows(self):
        self.runtime.settings = replace(self.runtime.settings, upload_max_bytes=4)
        response = self.client.post(
            "/api/v2/episodes",
            data={
                "providerProfileId": self.profile["id"],
                "summaryLanguage": "zh",
            },
            files={"file": ("large.mp3", b"12345", "audio/mpeg")},
        )

        self.assert_error(response, 413, "file_too_large")
        self.assertEqual(list(self.data_dir.rglob("*.part")), [])
        self.assertEqual(list(self.data_dir.rglob("large.mp3")), [])
        self.assert_database_counts(0, 0)
        self.runtime.scheduler.notify.assert_not_called()

    def test_empty_upload_is_rejected_and_cleaned(self):
        response = self.client.post(
            "/api/v2/episodes",
            data={"providerProfileId": self.profile["id"], "summaryLanguage": "zh"},
            files={"file": ("empty.mp3", b"", "audio/mpeg")},
        )

        self.assert_error(response, 400, "empty_file")
        self.assertEqual(list(self.data_dir.rglob("*.part")), [])
        self.assert_database_counts(0, 0)
        self.runtime.scheduler.notify.assert_not_called()

    def test_sanitizes_basename_and_rejects_unapproved_names(self):
        for filename in ("../escape.mp3", "folder\\escape.mp3"):
            with self.subTest(filename=filename):
                response = self.client.post(
                    "/api/v2/episodes",
                    data={
                        "providerProfileId": self.profile["id"],
                        "summaryLanguage": "zh",
                    },
                    files={"file": (filename, b"hello", "audio/mpeg")},
                )
                self.assertEqual(response.status_code, 202, response.text)
                source = (
                    self.data_dir
                    / "episodes"
                    / response.json()["id"]
                    / "source"
                    / "escape.mp3"
                )
                self.assertEqual(source.read_bytes(), b"hello")
        for filename in ("clip.exe", ".txt"):
            with self.subTest(filename=filename):
                response = self.client.post(
                    "/api/v2/episodes",
                    data={
                        "providerProfileId": self.profile["id"],
                        "summaryLanguage": "zh",
                    },
                    files={"file": (filename, b"hello", "text/plain")},
                )
                self.assert_error(response, 415, "unsupported_media_type")
        self.assertEqual(list(self.data_dir.rglob("*.part")), [])
        self.assert_database_counts(2, 2)

    def test_sanitizes_windows_reserved_filename_characters(self):
        for filename, safe_name in (
            ("bad:name.mp3", "bad_name.mp3"),
            ("CON.mp3", "_CON.mp3"),
        ):
            with self.subTest(filename=filename):
                response = self.client.post(
                    "/api/v2/episodes",
                    data={
                        "providerProfileId": self.profile["id"],
                        "summaryLanguage": "zh",
                    },
                    files={"file": (filename, b"hello", "audio/mpeg")},
                )
                self.assertEqual(response.status_code, 202, response.text)
                source = (
                    self.data_dir
                    / "episodes"
                    / response.json()["id"]
                    / "source"
                    / safe_name
                )
                self.assertEqual(source.read_bytes(), b"hello")

    def test_stream_read_failure_removes_partial_file_and_creates_no_rows(self):
        class FailingUpload:
            filename = "clip.mp3"
            content_type = "audio/mpeg"

            def __init__(self):
                self.calls = 0

            async def read(self, size):
                self.calls += 1
                if self.calls == 1:
                    self.requested_size = size
                    return b"partial"
                raise ConnectionError("client disconnected")

        upload = FailingUpload()
        self.assertTrue(
            hasattr(self.runtime, "require_episodes"),
            "runtime must expose the episode submission service",
        )
        with self.assertRaises(ConnectionError):
            asyncio.run(
                self.runtime.require_episodes().submit_upload(
                    upload,
                    self.profile["id"],
                    "zh",
                    None,
                    self.runtime.settings.upload_max_bytes,
                )
            )

        self.assertEqual(upload.requested_size, 1024 * 1024)
        self.assertEqual(list(self.data_dir.rglob("*.part")), [])
        self.assertEqual(list(self.data_dir.rglob("clip.mp3")), [])
        self.assert_database_counts(0, 0)

    def test_rejects_wrong_request_content_type_and_invalid_url(self):
        plain = self.client.post(
            "/api/v2/episodes", content=b"hello", headers={"Content-Type": "text/plain"}
        )
        invalid_url = self.client.post(
            "/api/v2/episodes",
            json={
                "sourceUrl": "file:///etc/passwd",
                "providerProfileId": self.profile["id"],
                "summaryLanguage": "zh",
            },
        )

        self.assert_error(plain, 415, "unsupported_media_type")
        self.assert_error(invalid_url, 422, "validation_error")
        self.assert_database_counts(0, 0)
        self.runtime.scheduler.notify.assert_not_called()

    def test_text_upload_is_rejected_as_unsupported_media(self):
        response = self.client.post(
            "/api/v2/episodes",
            data={
                "providerProfileId": self.profile["id"],
                "summaryLanguage": "zh",
            },
            files={"file": ("notes.txt", b"not playable", "text/plain")},
        )
        self.assert_error(response, 415, "unsupported_media_type")
        self.assert_database_counts(0, 0)

    def test_missing_or_deleted_profile_is_inactive_and_leaves_no_source(self):
        deleted = self.create_profile()
        self.assertEqual(
            self.client.delete(f"/api/v2/provider-profiles/{deleted['id']}").status_code,
            204,
        )
        for profile_id in ("missing", deleted["id"]):
            with self.subTest(profile_id=profile_id):
                response = self.client.post(
                    "/api/v2/episodes",
                    data={"providerProfileId": profile_id, "summaryLanguage": "zh"},
                    files={"file": ("clip.mp3", b"hello", "audio/mpeg")},
                )
                self.assert_error(response, 422, "provider_profile_inactive")
        self.assertEqual(list(self.data_dir.rglob("clip.mp3")), [])
        self.assertEqual(list(self.data_dir.rglob("*.part")), [])
        self.assert_database_counts(0, 0)
        self.runtime.scheduler.notify.assert_not_called()

    def test_database_failure_after_rename_removes_orphan_and_does_not_notify(self):
        with patch.object(
            self.runtime.database,
            "transaction",
            side_effect=RuntimeError("database unavailable"),
        ):
            response = self.client.post(
                "/api/v2/episodes",
                data={
                    "providerProfileId": self.profile["id"],
                    "summaryLanguage": "zh",
                },
                files={"file": ("clip.mp3", b"hello", "audio/mpeg")},
            )

        self.assert_error(response, 500, "internal_error")
        self.assertEqual(list(self.data_dir.rglob("clip.mp3")), [])
        self.assertEqual(list(self.data_dir.rglob("*.part")), [])
        self.assert_database_counts(0, 0)
        self.runtime.scheduler.notify.assert_not_called()

    def assert_database_counts(self, episodes, jobs):
        with self.runtime.database.connect() as conn:
            actual = (
                conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
            )
        self.assertEqual(actual, (episodes, jobs))

    def assert_error(self, response, status_code, code):
        self.assertEqual(response.status_code, status_code, response.text)
        error = response.json()["error"]
        self.assertEqual(error["code"], code)
        self.assertEqual(set(error), {"code", "message", "details", "requestId"})
        self.assertEqual(response.headers["X-Request-ID"], error["requestId"])


if __name__ == "__main__":
    unittest.main()
