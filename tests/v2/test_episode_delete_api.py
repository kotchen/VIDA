from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.v2.bootstrap import build_test_runtime
from backend.v2.domain import ChapterRecord, SummaryRecord, TranscriptSegmentRecord


class EpisodeDeleteApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name)
        self.runtime = build_test_runtime(self.data_dir, b"x" * 32)
        self.profile = self.runtime.require_provider_profiles().create_profile(
            "Primary", "https://api.test/v1", "secret", "model", 0.1
        )
        app = FastAPI()
        self.runtime.install(app)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_terminal_episode_delete_cascades_rows_and_removes_controlled_tree(self):
        for status in ("completed", "failed", "canceled"):
            with self.subTest(status=status):
                episode_id = self._terminal_episode(status)
                episode_dir = self.data_dir / "episodes" / episode_id
                episode_dir.mkdir(parents=True)
                (episode_dir / "artifact.bin").write_bytes(b"media")

                response = self.client.delete(f"/api/v2/episodes/{episode_id}")

                self.assertEqual(response.status_code, 204)
                self.assertIsNone(self.runtime.episode_repository.get(episode_id))
                self.assertEqual(self._count("jobs", episode_id), 0)
                self.assertEqual(self._count("transcript_segments", episode_id), 0)
                self.assertEqual(self._count("summaries", episode_id), 0)
                self.assertEqual(self._count("chapters", episode_id), 0)
                self.assertFalse(episode_dir.exists())

    def test_active_episode_delete_is_rejected_without_mutation(self):
        for status in ("queued", "processing"):
            with self.subTest(status=status):
                submission = self.runtime.require_episodes().submit_url(
                    f"https://media.test/{status}",
                    self.profile.id,
                    "en",
                    status,
                )
                episode_id = submission.episode.id
                with self.runtime.database.transaction(immediate=True) as conn:
                    conn.execute(
                        "UPDATE episodes SET status=? WHERE id=?",
                        (status, episode_id),
                    )
                    conn.execute(
                        "UPDATE jobs SET status=? WHERE episode_id=?",
                        (status, episode_id),
                    )

                response = self.client.delete(f"/api/v2/episodes/{episode_id}")

                self.assertEqual(response.status_code, 409)
                self.assertEqual(
                    response.json()["error"]["code"], "invalid_episode_state"
                )
                self.assertIsNotNone(self.runtime.episode_repository.get(episode_id))

    def test_missing_episode_delete_uses_v2_error(self):
        response = self.client.delete("/api/v2/episodes/missing")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "episode_not_found")

    def _terminal_episode(self, status: str) -> str:
        submission = self.runtime.require_episodes().submit_url(
            f"https://media.test/{status}", self.profile.id, "en", status
        )
        episode_id = submission.episode.id
        with self.runtime.database.transaction(immediate=True) as conn:
            conn.execute(
                "UPDATE episodes SET status=?,duration_sec=10 WHERE id=?",
                (status, episode_id),
            )
            conn.execute(
                "UPDATE jobs SET status=? WHERE episode_id=?",
                (status, episode_id),
            )
        self.runtime.episode_repository.replace_transcript(
            episode_id,
            [
                TranscriptSegmentRecord(
                    f"segment-{episode_id}", episode_id, 0, 0, 1, "Speaker", "Text"
                )
            ],
        )
        self.runtime.episode_repository.replace_summary(
            episode_id, SummaryRecord(episode_id, "Summary", 1, 1, 90, "VIDA")
        )
        self.runtime.chapter_repository.replace_generated(
            episode_id,
            [
                ChapterRecord(
                    f"chapter-{episode_id}",
                    episode_id,
                    0,
                    "Opening",
                    10,
                    None,
                    False,
                    "generated",
                )
            ],
        )
        return episode_id

    def _count(self, table: str, episode_id: str) -> int:
        with self.runtime.database.connect() as conn:
            return conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE episode_id=?",
                (episode_id,),
            ).fetchone()[0]


if __name__ == "__main__":
    unittest.main()
