from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.v2.bootstrap import build_test_runtime
from backend.v2.domain import ChapterRecord, SummaryRecord, TranscriptSegmentRecord


class DashboardApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = build_test_runtime(Path(self.temp.name), b"d" * 32)
        self.profile = self.runtime.require_provider_profiles().create_profile(
            "Primary", "https://api.test/v1", "secret", "model", 0.1
        )
        app = FastAPI()
        self.runtime.install(app)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def _episode(self, title: str, created_at: str, status: str):
        submission = self.runtime.require_episodes().submit_url(
            f"https://example.test/{title}", self.profile.id, "zh", title
        )
        episode_id = submission.episode.id
        with self.runtime.database.transaction() as conn:
            conn.execute(
                "UPDATE episodes SET status=?,created_at=?,duration_sec=42,language='zh',"
                "progress=100,message='Completed' WHERE id=?",
                (status, created_at, episode_id),
            )
            conn.execute("UPDATE jobs SET status='completed' WHERE episode_id=?", (episode_id,))
        return episode_id

    def test_empty_dashboard_matches_contract_exactly(self):
        response = self.client.get("/api/v2/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "currentEpisode": None,
            "summary": None,
            "transcript": [],
            "chapters": [],
            "recentProjects": [],
        })

    def test_dashboard_uses_latest_completed_and_independent_recent_projects(self):
        old = self._episode("old", "2026-07-17T00:00:00Z", "completed")
        current = self._episode("current", "2026-07-18T00:00:00Z", "completed")
        failed = self._episode("failed", "2026-07-19T00:00:00Z", "failed")
        self.runtime.episode_repository.replace_summary(
            current, SummaryRecord(current, "Summary", 2, 3, 91, "VIDA")
        )
        self.runtime.episode_repository.replace_transcript(current, [
            TranscriptSegmentRecord("s1", current, 0, 0, 2.5, "Alice", "Hello")
        ])
        self.runtime.chapter_repository.replace_generated(current, [
            ChapterRecord("c1", current, 0, "Opening", 42, None, False, "generated")
        ])

        body = self.client.get("/api/v2/dashboard").json()

        self.assertEqual(body["currentEpisode"]["id"], current)
        self.assertEqual(body["summary"]["content"], "Summary")
        self.assertEqual(body["transcript"][0]["speaker"], "Alice")
        self.assertEqual(body["chapters"][0]["title"], "Opening")
        self.assertEqual(
            [row["id"] for row in body["recentProjects"]], [failed, current, old]
        )


if __name__ == "__main__":
    unittest.main()
