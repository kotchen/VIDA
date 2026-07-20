import tempfile
import sqlite3
import unittest
from pathlib import Path

from backend.v2.database import Database
from backend.v2.domain import ChapterRecord
from backend.v2.repositories.chapters import ChapterRepository
from tests.v2.test_episode_repository import make_episode
from backend.v2.repositories.episodes import EpisodeRepository


NOW = "2026-07-18T00:00:00Z"


class ChapterRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "db.sqlite3")
        self.db.initialize()
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO provider_profiles"
                "(id,name,active_revision_id,created_at,updated_at) VALUES(?,?,?,?,?)",
                ("p1", "Primary", None, NOW, NOW),
            )
        EpisodeRepository(self.db).create(make_episode())
        self.repo = ChapterRepository(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def test_regeneration_preserves_manual_chapters_and_orders_by_start_then_id(self):
        manual = self.repo.create_manual(
            "e1", 12.0, "Manual", 18.0, "/poster", False
        )
        self.assertEqual(manual.source, "manual")
        self.repo.replace_generated("e1", [
            ChapterRecord("g1", "e1", 0.0, "Opening", 12.0, "/poster", False, "generated"),
            ChapterRecord("g3", "e1", 30.0, "End", 5.0, None, False, "generated"),
        ])
        self.repo.replace_generated("e1", [
            ChapterRecord("g2", "e1", 0.0, "New opening", 12.0, "/poster", True, "generated")
        ])
        chapters = self.repo.list("e1")
        self.assertEqual([chapter.title for chapter in chapters], ["New opening", "Manual"])
        self.assertEqual({chapter.source for chapter in chapters}, {"manual", "generated"})
        self.assertTrue(chapters[0].bookmarked)

    def test_failed_generated_replacement_rolls_back_old_generated_chapters(self):
        original = ChapterRecord(
            "g1", "e1", 0.0, "Opening", 12.0, None, False, "generated"
        )
        self.repo.replace_generated("e1", [original])
        with self.assertRaises(ValueError):
            self.repo.replace_generated("e1", [
                ChapterRecord("g2", "e1", 0.0, "New", 12.0, None, False, "generated"),
                ChapterRecord("wrong", "other", 12.0, "Wrong", 1.0, None, False, "generated"),
            ])
        self.assertEqual(self.repo.list("e1"), [original])
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.replace_generated("e1", [
                ChapterRecord("duplicate", "e1", 0, "One", 1, None, False, "generated"),
                ChapterRecord("duplicate", "e1", 1, "Two", 1, None, False, "generated"),
            ])
        self.assertEqual(self.repo.list("e1"), [original])

    def test_generated_replacement_rejects_manual_records_and_missing_episode(self):
        with self.assertRaises(ValueError):
            self.repo.replace_generated("e1", [
                ChapterRecord("m1", "e1", 0.0, "Manual", 1.0, None, False, "manual")
            ])
        with self.assertRaises(KeyError):
            self.repo.replace_generated("missing", [])
        with self.assertRaises(KeyError):
            self.repo.create_manual("missing", 0, "Missing", None, None, False)


if __name__ == "__main__":
    unittest.main()
