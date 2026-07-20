from __future__ import annotations

import concurrent.futures
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.v2.bootstrap import build_test_runtime
from backend.v2.domain import ChapterRecord


class ChapterApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = build_test_runtime(Path(self.temp.name), b"c" * 32)
        profile = self.runtime.require_provider_profiles().create_profile(
            "Primary", "https://api.test/v1", "secret", "model", 0.1
        )
        self.episode_id = self.runtime.require_episodes().submit_url(
            "https://example.test/one", profile.id, "en", "One"
        ).episode.id
        self.other_id = self.runtime.require_episodes().submit_url(
            "https://example.test/two", profile.id, "en", "Two"
        ).episode.id
        with self.runtime.database.transaction() as conn:
            conn.execute(
                "UPDATE episodes SET duration_sec=100,poster_path='episodes/poster.jpg' "
                "WHERE id IN (?,?)", (self.episode_id, self.other_id)
            )
        self.runtime.chapter_repository.replace_generated(self.episode_id, [
            ChapterRecord("generated", self.episode_id, 50, "Generated", 50, None, False, "generated")
        ])
        app = FastAPI()
        self.runtime.install(app)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_create_manual_derives_duration_orders_and_uses_episode_poster(self):
        response = self.client.post(
            f"/api/v2/episodes/{self.episode_id}/chapters",
            json={"startSec": 20, "title": "Manual"},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["durationSec"], 30)
        self.assertFalse(response.json()["bookmarked"])
        self.assertEqual(
            response.json()["thumbnailUrl"],
            f"/api/v2/episodes/{self.episode_id}/poster",
        )
        listed = self.client.get(f"/api/v2/episodes/{self.episode_id}/chapters").json()
        self.assertEqual([row["title"] for row in listed], ["Manual", "Generated"])

    def test_create_validates_finite_episode_bounds_and_nonempty_title(self):
        for payload in (
            {"startSec": -1, "title": "bad"},
            {"startSec": 101, "title": "bad"},
            {"startSec": "NaN", "title": "bad"},
            {"startSec": 1, "title": "   "},
        ):
            with self.subTest(payload=payload):
                response = self.client.post(
                    f"/api/v2/episodes/{self.episode_id}/chapters", json=payload
                )
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["error"]["code"], "validation_error")

    def test_manual_update_delete_enforce_episode_ownership_and_preserve_generated(self):
        created = self.client.post(
            f"/api/v2/episodes/{self.episode_id}/chapters",
            json={"startSec": 20, "title": "Manual"},
        ).json()
        wrong = self.client.patch(
            f"/api/v2/episodes/{self.other_id}/chapters/{created['id']}",
            json={"title": "Stolen"},
        )
        self.assertEqual((wrong.status_code, wrong.json()["error"]["code"]), (404, "chapter_not_found"))
        updated = self.client.patch(
            f"/api/v2/episodes/{self.episode_id}/chapters/{created['id']}",
            json={"startSec": 60, "title": "Moved"},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["durationSec"], 40)
        generated_delete = self.client.delete(
            f"/api/v2/episodes/{self.episode_id}/chapters/generated"
        )
        self.assertEqual((generated_delete.status_code, generated_delete.json()["error"]["code"]), (409, "generated_chapter_immutable"))
        deleted = self.client.delete(
            f"/api/v2/episodes/{self.episode_id}/chapters/{created['id']}"
        )
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(
            [row["id"] for row in self.client.get(f"/api/v2/episodes/{self.episode_id}/chapters").json()],
            ["generated"],
        )

    def test_concurrent_creates_are_atomic_and_return_consistent_durations(self):
        starts = (10, 20, 30, 40)

        def create(start):
            return self.client.post(
                f"/api/v2/episodes/{self.episode_id}/chapters",
                json={"startSec": start, "title": f"M{start}"},
            ).status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            statuses = list(pool.map(create, starts))
        self.assertEqual(statuses, [201] * 4)
        listed = self.client.get(f"/api/v2/episodes/{self.episode_id}/chapters").json()
        self.assertEqual([row["startSec"] for row in listed], [10, 20, 30, 40, 50])
        self.assertEqual([row["durationSec"] for row in listed], [10, 10, 10, 10, 50])

    def test_create_update_delete_persist_neighbor_duration_changes(self):
        first = self.client.post(
            f"/api/v2/episodes/{self.episode_id}/chapters",
            json={"startSec": 10, "title": "First"},
        ).json()
        second = self.client.post(
            f"/api/v2/episodes/{self.episode_id}/chapters",
            json={"startSec": 20, "title": "Second"},
        ).json()
        self.assertEqual(self._stored_durations(), {
            first["id"]: 10, second["id"]: 30, "generated": 50,
        })

        self.client.patch(
            f"/api/v2/episodes/{self.episode_id}/chapters/{second['id']}",
            json={"startSec": 70},
        )
        self.assertEqual(self._stored_durations(), {
            first["id"]: 40, "generated": 20, second["id"]: 30,
        })

        self.client.delete(
            f"/api/v2/episodes/{self.episode_id}/chapters/{second['id']}"
        )
        self.assertEqual(self._stored_durations(), {
            first["id"]: 40, "generated": 50,
        })

    def _stored_durations(self):
        with self.runtime.database.connect() as conn:
            rows = conn.execute(
                "SELECT id,duration_sec FROM chapters WHERE episode_id=?",
                (self.episode_id,),
            ).fetchall()
        return {row["id"]: row["duration_sec"] for row in rows}


if __name__ == "__main__":
    unittest.main()
