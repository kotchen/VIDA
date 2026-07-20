from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.v2.bootstrap import build_test_runtime
from backend.v2.domain import ChapterRecord, SummaryRecord, TranscriptSegmentRecord


class BlockingExecutor:
    def __init__(self) -> None:
        self.started_ids: list[str] = []
        self.active = 0
        self.max_active = 0
        self._changed = asyncio.Condition()
        self._release = asyncio.Event()

    async def execute(self, job, cancel_check) -> None:
        async with self._changed:
            self.started_ids.append(job.id)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self._changed.notify_all()
        try:
            await self._release.wait()
        finally:
            async with self._changed:
                self.active -= 1
                self._changed.notify_all()

    async def wait_until_started(self, count: int) -> None:
        async with self._changed:
            await asyncio.wait_for(
                self._changed.wait_for(lambda: len(self.started_ids) >= count), 2
            )


class SuccessfulExecutor:
    def __init__(self) -> None:
        self.started_ids: list[str] = []
        self.active = 0
        self.max_active = 0
        self._lock = asyncio.Lock()

    async def execute(self, job, cancel_check) -> None:
        async with self._lock:
            self.started_ids.append(job.id)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0)
        async with self._lock:
            self.active -= 1


class QueueRestartAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_five_projects_run_two_at_a_time_and_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            first_executor = BlockingExecutor()
            first_runtime = build_test_runtime(
                data_dir, b"q" * 32, max_concurrent_jobs=2, executor=first_executor
            )
            profile = first_runtime.require_provider_profiles().create_profile(
                "Primary", "https://api.example/v1", "provider-secret", "model", 0.1
            )
            await first_runtime.start()
            submissions = [
                first_runtime.require_episodes().submit_url(
                    f"https://example.test/media/{index}", profile.id, "zh", f"Episode {index}"
                )
                for index in range(5)
            ]
            first_runtime.scheduler.notify()
            await first_executor.wait_until_started(2)

            self.assertEqual(first_executor.max_active, 2)
            with first_runtime.database.connect() as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM jobs WHERE status='processing'"
                    ).fetchone()[0],
                    2,
                )
            job_ids = [submission.episode.current_job_id for submission in submissions]
            await first_runtime.stop(grace_period_sec=0)

            second_executor = SuccessfulExecutor()
            second_runtime = build_test_runtime(
                data_dir, b"q" * 32, max_concurrent_jobs=2, executor=second_executor
            )
            await second_runtime.start()
            await second_runtime.scheduler.wait_until_idle(timeout_sec=5)
            try:
                statuses = [
                    second_runtime.require_episodes().get_episode(submission.episode.id).status
                    for submission in submissions
                ]
                self.assertEqual(statuses, ["completed"] * 5)
                self.assertEqual(second_executor.started_ids, job_ids)
                self.assertLessEqual(second_executor.max_active, 2)
                with second_runtime.database.connect() as connection:
                    rows = connection.execute("SELECT id FROM jobs").fetchall()
                self.assertEqual({row["id"] for row in rows}, set(job_ids))
            finally:
                await second_runtime.stop(grace_period_sec=1)


class QueueApiAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name)
        self.runtime = build_test_runtime(self.data_dir, b"a" * 32)
        self.runtime.scheduler = Mock(start=Mock(), stop=Mock(), notify=Mock())
        app = FastAPI()
        self.runtime.install(app)
        self.client = TestClient(app)
        created = self.client.post(
            "/api/v2/provider-profiles",
            json={
                "name": "Primary",
                "baseUrl": "https://api.example/v1",
                "apiKey": "provider-secret",
                "modelId": "model",
                "temperature": 0.1,
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.profile_id = created.json()["id"]

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_submit_cancel_retry_process_read_regenerate_and_deliver(self):
        submitted = self.client.post(
            "/api/v2/episodes",
            json={
                "sourceUrl": "https://example.test/audio.mp3",
                "providerProfileId": self.profile_id,
                "summaryLanguage": "en",
                "title": "Acceptance Episode",
            },
        )
        self.assertEqual(submitted.status_code, 202, submitted.text)
        episode_id = submitted.json()["id"]
        first_job_id = self.runtime.require_episodes().get_episode(episode_id).current_job_id
        self.assertEqual(self.client.get(f"/api/v2/jobs/{first_job_id}").json()["status"], "queued")

        canceled = self.client.post(f"/api/v2/episodes/{episode_id}/cancel")
        self.assertEqual((canceled.status_code, canceled.json()["status"]), (200, "canceled"))
        retried = self.client.post(f"/api/v2/episodes/{episode_id}/retry")
        self.assertEqual((retried.status_code, retried.json()["attempt"]), (202, 2))
        second_job_id = retried.json()["id"]

        claimed = self.runtime.job_repository.claim_next("acceptance-worker", "2026-07-20T00:00:00Z")
        self.assertEqual(claimed.id, second_job_id)
        media_relative = Path("episodes") / episode_id / "artifacts" / "audio.mp3"
        poster_relative = Path("episodes") / episode_id / "poster" / "poster.jpg"
        media_path = self.data_dir / media_relative
        poster_path = self.data_dir / poster_relative
        media_path.parent.mkdir(parents=True)
        poster_path.parent.mkdir(parents=True)
        media_path.write_bytes(b"0123456789")
        poster_path.write_bytes(b"jpeg")
        transcript = TranscriptSegmentRecord(
            "segment-1", episode_id, 0, 0.0, 10.0, "Host", "Hello VIDA"
        )
        self.runtime.episode_repository.commit_core_output(
            episode_id,
            [transcript],
            language="en",
            media_path=media_relative.as_posix(),
            media_content_type="audio/mpeg",
            poster_path=poster_relative.as_posix(),
            poster_content_type="image/jpeg",
            duration_sec=10.0,
            resolution=None,
            updated_at="2026-07-20T00:01:00Z",
        )
        self.runtime.episode_repository.replace_summary(
            episode_id, SummaryRecord(episode_id, "Summary text", 1, 1, 95, "VIDA")
        )
        self.runtime.chapter_repository.replace_generated(
            episode_id,
            [ChapterRecord("generated-1", episode_id, 0.0, "Opening", 10.0, None, False, "generated")],
        )
        self.runtime.job_repository.complete(second_job_id, "2026-07-20T00:02:00Z")

        manual = self.client.post(
            f"/api/v2/episodes/{episode_id}/chapters",
            json={"startSec": 5, "title": "Manual note"},
        )
        self.assertEqual(manual.status_code, 201, manual.text)
        self.assertEqual(self.client.get(f"/api/v2/episodes/{episode_id}").json()["status"], "completed")
        self.assertEqual(len(self.client.get(f"/api/v2/episodes/{episode_id}/transcript").json()), 1)
        self.assertEqual(self.client.get(f"/api/v2/episodes/{episode_id}/summary").json()["confidence"], 95)
        self.assertEqual(len(self.client.get(f"/api/v2/episodes/{episode_id}/chapters").json()), 2)
        self.assertEqual(self.client.get("/api/v2/dashboard").json()["currentEpisode"]["id"], episode_id)
        self.assertEqual(self.client.get("/api/v2/episodes?limit=12&offset=0").json()[0]["id"], episode_id)

        for export_format, content_type in (
            ("txt", "text/plain"),
            ("srt", "application/x-subrip"),
            ("md", "text/markdown"),
        ):
            response = self.client.get(
                f"/api/v2/episodes/{episode_id}/export?format={export_format}"
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.headers["content-type"].startswith(content_type))
            self.assertIn("attachment", response.headers["content-disposition"])

        partial = self.client.get(
            f"/api/v2/episodes/{episode_id}/media", headers={"Range": "bytes=2-5"}
        )
        self.assertEqual((partial.status_code, partial.content), (206, b"2345"))
        self.assertEqual(partial.headers["content-range"], "bytes 2-5/10")

        summary_job = self.client.post(f"/api/v2/episodes/{episode_id}/summary/regenerate")
        self.assertEqual(summary_job.status_code, 202, summary_job.text)
        self.assertEqual(
            self.client.post(f"/api/v2/jobs/{summary_job.json()['id']}/cancel").json()["status"],
            "canceled",
        )
        chapters_job = self.client.post(f"/api/v2/episodes/{episode_id}/chapters/regenerate")
        self.assertEqual(chapters_job.status_code, 202, chapters_job.text)
        self.client.post(f"/api/v2/jobs/{chapters_job.json()['id']}/cancel")
        chapter_titles = [
            row["title"]
            for row in self.client.get(f"/api/v2/episodes/{episode_id}/chapters").json()
        ]
        self.assertEqual(chapter_titles, ["Opening", "Manual note"])


if __name__ == "__main__":
    unittest.main()
