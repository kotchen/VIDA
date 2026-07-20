from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.v2.bootstrap import build_test_runtime
from backend.v2.domain import ChapterRecord, SummaryRecord, TranscriptSegmentRecord


NOW = "2026-07-18T00:00:00Z"


class EpisodeApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = build_test_runtime(Path(self.temp.name), b"k" * 32)
        self.profile = self.runtime.require_provider_profiles().create_profile(
            "Primary", "https://api.test/v1", "secret", "model", 0.1
        )
        self.submission = self.runtime.require_episodes().submit_url(
            "https://example.test/media", self.profile.id, "zh", "Episode"
        )
        self.episode_id = self.submission.episode.id
        app = FastAPI()
        self.runtime.install(app)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_detail_reports_current_job_position_and_warnings_in_camel_case(self):
        with self.runtime.database.transaction() as conn:
            conn.execute(
                "UPDATE episodes SET warnings_json=? WHERE id=?",
                ('[{"stage":"summary","code":"unavailable","message":"No summary"}]', self.episode_id),
            )
        response = self.client.get(f"/api/v2/episodes/{self.episode_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["queuePosition"], 1)
        self.assertEqual(response.json()["warnings"][0]["code"], "unavailable")
        self.assertNotIn("queue_position", response.json())

    def test_content_reads_are_sorted_and_summary_missing_has_contract_error(self):
        self.runtime.episode_repository.replace_transcript(self.episode_id, [
            TranscriptSegmentRecord("late", self.episode_id, 0, 5, 6, None, "late"),
            TranscriptSegmentRecord("early", self.episode_id, 1, 1, 2, "Alice", "early"),
        ])
        self.runtime.chapter_repository.replace_generated(self.episode_id, [
            ChapterRecord("c2", self.episode_id, 10, "Later", 2, None, False, "generated"),
            ChapterRecord("c1", self.episode_id, 2, "Earlier", 3, None, True, "generated"),
        ])
        transcript = self.client.get(f"/api/v2/episodes/{self.episode_id}/transcript")
        chapters = self.client.get(f"/api/v2/episodes/{self.episode_id}/chapters")
        summary = self.client.get(f"/api/v2/episodes/{self.episode_id}/summary")
        self.assertEqual([row["id"] for row in transcript.json()], ["early", "late"])
        self.assertEqual(transcript.json()[0]["speaker"], "Alice")
        self.assertEqual([row["id"] for row in chapters.json()], ["c1", "c2"])
        self.assertEqual(summary.status_code, 404)
        self.assertEqual(summary.json()["error"]["code"], "summary_not_found")

    def test_summary_and_missing_episode_envelopes(self):
        self.runtime.episode_repository.replace_summary(
            self.episode_id, SummaryRecord(self.episode_id, "Body", 2, 3, 92, "VIDA")
        )
        summary = self.client.get(f"/api/v2/episodes/{self.episode_id}/summary")
        missing = self.client.get("/api/v2/episodes/missing/transcript")
        self.assertEqual(summary.json(), {
            "episodeId": self.episode_id, "content": "Body", "readTimeMin": 2,
            "keyPoints": 3, "confidence": 92, "generatedBy": "VIDA",
        })
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["error"]["code"], "episode_not_found")


if __name__ == "__main__":
    unittest.main()
