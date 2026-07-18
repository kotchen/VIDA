import json
import sqlite3
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace

from backend.v2.database import Database
from backend.v2.domain import (
    EpisodeRecord,
    ProcessingWarning,
    SummaryRecord,
    TranscriptSegmentRecord,
)
from backend.v2.errors import V2Error
from backend.v2.repositories.episodes import EpisodeRepository
from backend.v2.services.episodes import EpisodeService


NOW = "2026-07-18T00:00:00Z"


class EpisodeRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "db.sqlite3")
        self.db.initialize()
        self._insert_profile()
        self.repo = EpisodeRepository(self.db)
        self.episode = make_episode()

    def tearDown(self):
        self.temp.cleanup()

    def _insert_profile(self):
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO provider_profiles"
                "(id,name,active_revision_id,created_at,updated_at) VALUES(?,?,?,?,?)",
                ("p1", "Primary", None, NOW, NOW),
            )
            conn.execute(
                "INSERT INTO provider_profile_revisions"
                "(id,profile_id,version,base_url,model_id,temperature,encrypted_api_key,"
                "encryption_nonce,encryption_format_version,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("r1", "p1", 1, "https://api.example/v1", "model", 0.1,
                 "cipher", "nonce", 1, NOW),
            )
            conn.execute(
                "UPDATE provider_profiles SET active_revision_id='r1' WHERE id='p1'"
            )

    def test_create_and_get_round_trip_every_episode_field(self):
        expected = replace(
            self.episode,
            source_content_type="video/mp4",
            media_path="episodes/e1/artifacts/media.mp4",
            media_content_type="video/mp4",
            poster_path="episodes/e1/artifacts/poster.jpg",
            poster_content_type="image/jpeg",
            duration_sec=42.5,
            resolution="1920x1080",
            language="en",
            completed_at="2026-07-18T00:01:00Z",
        )
        self.repo.create(expected)
        self.assertEqual(self.repo.get("e1"), expected)
        self.assertIsNone(self.repo.get("missing"))

    def test_replace_transcript_is_atomic_and_sorted_by_ordinal(self):
        self.repo.create(self.episode)
        self.repo.replace_transcript("e1", [
            TranscriptSegmentRecord("s2", "e1", 1, 5.0, 8.0, "B", "second"),
            TranscriptSegmentRecord("s1", "e1", 0, 0.0, 5.0, "A", "first"),
        ])
        self.assertEqual(
            [row.id for row in self.repo.get_transcript("e1")], ["s1", "s2"]
        )
        with self.assertRaises(ValueError):
            self.repo.replace_transcript("e1", [
                TranscriptSegmentRecord("replacement", "e1", 0, 0, 1, None, "new"),
                TranscriptSegmentRecord("wrong", "other", 1, 1, 2, None, "wrong"),
            ])
        self.assertEqual(
            [row.id for row in self.repo.get_transcript("e1")], ["s1", "s2"]
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.replace_transcript("e1", [
                TranscriptSegmentRecord("duplicate-1", "e1", 0, 0, 1, None, "one"),
                TranscriptSegmentRecord("duplicate-2", "e1", 0, 1, 2, None, "two"),
            ])
        self.assertEqual(
            [row.id for row in self.repo.get_transcript("e1")], ["s1", "s2"]
        )

    def test_transcript_reads_are_chronological_when_ordinals_disagree(self):
        self.repo.create(self.episode)
        self.repo.replace_transcript("e1", [
            TranscriptSegmentRecord("later", "e1", 0, 5.0, 8.0, None, "later"),
            TranscriptSegmentRecord("earlier-b", "e1", 2, 0.0, 2.0, None, "b"),
            TranscriptSegmentRecord("earlier-a", "e1", 1, 0.0, 1.0, None, "a"),
        ])

        self.assertEqual(
            [row.id for row in self.repo.get_transcript("e1")],
            ["earlier-a", "earlier-b", "later"],
        )

    def test_content_replacement_validates_episode_before_mutating(self):
        with self.assertRaises(KeyError):
            self.repo.replace_transcript("missing", [])
        with self.assertRaises(KeyError):
            self.repo.replace_summary(
                "missing", SummaryRecord("missing", "summary", 1, 1, 80, "VIDA")
            )

    def test_replace_summary_is_normalized_and_failed_replacement_keeps_old_value(self):
        self.repo.create(self.episode)
        old = SummaryRecord("e1", "old", 2, 2, 80, "VIDA")
        self.repo.replace_summary("e1", old)
        self.assertEqual(self.repo.get_summary("e1"), old)

        invalid = SummaryRecord("other", "new", 1, 1, 90, "VIDA")
        with self.assertRaises(ValueError):
            self.repo.replace_summary("e1", invalid)
        self.assertEqual(self.repo.get_summary("e1"), old)
        with self.db.connect() as conn:
            stored = conn.execute(
                "SELECT key_points,confidence,generated_by FROM summaries"
            ).fetchone()
        self.assertEqual(tuple(stored), (2, 80, "VIDA"))

    def test_summary_record_enforces_integer_count_and_required_percentage(self):
        for key_points, confidence in ((0, 0), (3, 100)):
            with self.subTest(key_points=key_points, confidence=confidence):
                record = SummaryRecord("e1", "ok", 1, key_points, confidence, "VIDA")
                self.assertEqual((record.key_points, record.confidence), (key_points, confidence))

        invalid_values = (
            (None, 50), (True, 50), (1.5, 50), ("1", 50), (-1, 50),
            (1, None), (1, True), (1, 50.5), (1, "50"), (1, -1), (1, 101),
        )
        for key_points, confidence in invalid_values:
            with self.subTest(key_points=key_points, confidence=confidence):
                with self.assertRaises((TypeError, ValueError)):
                    SummaryRecord("e1", "bad", 1, key_points, confidence, "VIDA")

    def test_summary_repository_and_database_reject_invalid_numeric_storage(self):
        self.repo.create(self.episode)
        valid = SummaryRecord("e1", "valid", 1, 1, 50, "VIDA")
        self.repo.replace_summary("e1", valid)

        for field, value in (("key_points", None), ("key_points", 1.5),
                             ("confidence", None),
                             ("confidence", 50.5)):
            with self.subTest(field=field, value=value):
                with self.assertRaises(sqlite3.IntegrityError):
                    with self.db.transaction() as conn:
                        conn.execute(
                            f"UPDATE summaries SET {field}=? WHERE episode_id='e1'",
                            (value,),
                        )
        self.assertEqual(self.repo.get_summary("e1"), valid)

    def test_summary_repository_validates_even_at_the_persistence_boundary(self):
        self.repo.create(self.episode)
        invalid_records = (
            SimpleNamespace(episode_id="e1", content="bad", read_time_min=1,
                            key_points=True, confidence=50, generated_by="VIDA"),
            SimpleNamespace(episode_id="e1", content="bad", read_time_min=1,
                            key_points="1", confidence=50, generated_by="VIDA"),
            SimpleNamespace(episode_id="e1", content="bad", read_time_min=1,
                            key_points=1, confidence=None, generated_by="VIDA"),
            SimpleNamespace(episode_id="e1", content="bad", read_time_min=1,
                            key_points=1, confidence=True, generated_by="VIDA"),
            SimpleNamespace(episode_id="e1", content="bad", read_time_min=1,
                            key_points=1, confidence=50.5, generated_by="VIDA"),
        )
        for record in invalid_records:
            with self.subTest(record=record):
                with self.assertRaises((TypeError, ValueError)):
                    self.repo.replace_summary("e1", record)
        self.assertIsNone(self.repo.get_summary("e1"))

    def test_warnings_are_typed_and_json_is_the_only_denormalized_content(self):
        self.repo.create(self.episode)
        warnings = [
            ProcessingWarning("summary", "provider_timeout", "Summary unavailable"),
            ProcessingWarning("chapters", "invalid_output", "Chapters unavailable"),
        ]
        self.repo.set_warnings("e1", warnings)
        self.assertEqual(self.repo.get_warnings("e1"), warnings)
        self.assertEqual(self.repo.get("e1").warnings, warnings)
        with self.db.connect() as conn:
            raw = conn.execute(
                "SELECT warnings_json FROM episodes WHERE id='e1'"
            ).fetchone()[0]
        self.assertEqual(json.loads(raw), [
            {"stage": "summary", "code": "provider_timeout", "message": "Summary unavailable"},
            {"stage": "chapters", "code": "invalid_output", "message": "Chapters unavailable"},
        ])
        with self.assertRaises(FrozenInstanceError):
            warnings[0].code = "changed"
        with self.assertRaises(KeyError):
            self.repo.set_warnings("missing", warnings)

    def test_invalid_stored_warning_fails_typed_parsing(self):
        self.repo.create(self.episode)
        invalid_payloads = (
            '[{"stage":"summary","code":"missing-message"}]',
            '[{"stage":"summary","code":42,"message":"wrong type"}]',
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.db.transaction() as conn:
                    conn.execute(
                        "UPDATE episodes SET warnings_json=? WHERE id='e1'", (payload,)
                    )
                with self.assertRaises((TypeError, ValueError)):
                    self.repo.get_warnings("e1")

    def test_latest_completed_and_project_pagination_are_deterministic(self):
        rows = [
            replace(self.episode, id="completed-old", status="completed", created_at="2026-07-17T00:00:00Z"),
            replace(self.episode, id="failed-new", status="failed", created_at="2026-07-19T00:00:00Z"),
            replace(self.episode, id="canceled-new", status="canceled", created_at="2026-07-20T00:00:00Z"),
            replace(self.episode, id="same-a", status="completed", created_at="2026-07-21T00:00:00Z"),
            replace(self.episode, id="same-b", status="completed", created_at="2026-07-21T00:00:00Z"),
        ]
        for row in rows:
            self.repo.create(row)

        self.assertEqual(self.repo.latest_completed().id, "same-b")
        projects = self.repo.list_projects(limit=2, offset=1)
        self.assertEqual([row.id for row in projects], ["same-a", "canceled-new"])
        self.assertTrue(all(row.source_path is None for row in projects))

    def test_project_pagination_rejects_unbounded_values(self):
        for limit, offset in ((0, 0), (101, 0), (1, -1)):
            with self.subTest(limit=limit, offset=offset):
                with self.assertRaises(ValueError):
                    self.repo.list_projects(limit, offset)


class EpisodeServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "service.sqlite3")
        self.db.initialize()
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO provider_profiles"
                "(id,name,active_revision_id,created_at,updated_at) VALUES(?,?,?,?,?)",
                ("p1", "Primary", None, NOW, NOW),
            )
        self.repo = EpisodeRepository(self.db)
        self.episode = make_episode()
        self.service = EpisodeService(self.repo)

    def tearDown(self):
        self.temp.cleanup()

    def test_missing_episode_and_summary_use_contract_errors(self):
        with self.assertRaises(V2Error) as missing_episode:
            self.service.get_episode("missing")
        self.assertEqual(
            (missing_episode.exception.code, missing_episode.exception.status_code),
            ("episode_not_found", 404),
        )

        self.repo.create(self.episode)
        with self.assertRaises(V2Error) as missing_summary:
            self.service.get_summary("e1")
        self.assertEqual(
            (missing_summary.exception.code, missing_summary.exception.status_code),
            ("summary_not_found", 404),
        )


def make_episode() -> EpisodeRecord:
    return EpisodeRecord(
        id="e1", title="Episode", source_type="url", source_path=None,
        source_url="https://example.test/video", source_content_type=None,
        media_path=None, media_content_type=None, poster_path=None,
        poster_content_type=None, duration_sec=None, resolution=None,
        status="queued", language=None, summary_language="zh", progress=0,
        message="Queued", warnings_json="[]", error_code=None, error_message=None,
        current_job_id=None, provider_profile_id="p1", created_at=NOW,
        updated_at=NOW, completed_at=None,
    )


if __name__ == "__main__":
    unittest.main()
