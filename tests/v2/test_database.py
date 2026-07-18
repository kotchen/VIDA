import sqlite3
import tempfile
import unittest
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

from backend.v2.database import Database
from backend.v2.domain import EpisodeRecord, EpisodeStatus, JobRecord, JobType


NOW = "2026-07-18T00:00:00Z"


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "vida.sqlite3")
        self.db.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_initial_migration_creates_required_tables(self):
        with self.db.connect() as conn:
            names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertTrue(
            {
                "episodes",
                "jobs",
                "transcript_segments",
                "summaries",
                "chapters",
                "provider_profiles",
                "provider_profile_revisions",
                "schema_migrations",
            }.issubset(names)
        )

    def test_initial_migration_is_idempotent_and_preserves_data(self):
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO provider_profiles"
                "(id,name,active_revision_id,created_at,updated_at) VALUES(?,?,?,?,?)",
                ("p1", "Primary", None, NOW, NOW),
            )
        self.db.initialize()
        with self.db.connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM provider_profiles WHERE id='p1'"
            ).fetchone()[0]
            migrations = conn.execute(
                "SELECT version FROM schema_migrations"
            ).fetchall()
        self.assertEqual(count, 1)
        self.assertEqual([row[0] for row in migrations], [1])

    def test_every_connection_uses_required_pragmas(self):
        with self.db.connect() as conn:
            pragmas = (
                conn.execute("PRAGMA journal_mode").fetchone()[0],
                conn.execute("PRAGMA foreign_keys").fetchone()[0],
                conn.execute("PRAGMA busy_timeout").fetchone()[0],
                conn.execute("PRAGMA synchronous").fetchone()[0],
            )
        self.assertEqual(pragmas, ("wal", 1, 5000, 1))

    def test_schema_has_media_warning_language_fields_and_required_indexes(self):
        with self.db.connect() as conn:
            episode_columns = {
                row[1]: (row[2], row[3], row[4])
                for row in conn.execute("PRAGMA table_info(episodes)")
            }
            job_indexes = {
                row[1]: row[0]
                for row in conn.execute(
                    "SELECT type,name FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='jobs'"
                )
            }
            episode_indexes = {
                row[1] for row in conn.execute("PRAGMA index_list(episodes)")
            }
        self.assertTrue(
            {
                "source_path",
                "source_content_type",
                "media_path",
                "media_content_type",
                "poster_path",
                "poster_content_type",
                "summary_language",
                "warnings_json",
            }.issubset(episode_columns)
        )
        self.assertEqual(episode_columns["warnings_json"][1:], (1, "'[]'"))
        self.assertIn("uq_jobs_one_active_per_episode", job_indexes)
        self.assertIn("idx_jobs_status_submitted_id", job_indexes)
        self.assertIn("idx_episodes_created_at", episode_indexes)

    def test_only_one_active_job_is_allowed_per_episode(self):
        with self.db.transaction() as conn:
            self._insert_profile_episode(conn)
            conn.execute(
                "INSERT INTO jobs"
                "(id,episode_id,type,attempt,status,provider_profile_revision_id,"
                "submitted_at,progress,message) VALUES(?,?,?,?,?,?,?,?,?)",
                ("j1", "e1", "process_episode", 1, "queued", "r1", NOW, 0, "Queued"),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO jobs"
                    "(id,episode_id,type,attempt,status,provider_profile_revision_id,"
                    "submitted_at,progress,message) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        "j2",
                        "e1",
                        "process_episode",
                        2,
                        "processing",
                        "r1",
                        "2026-07-18T00:00:01Z",
                        1,
                        "Processing",
                    ),
                )

    def test_profile_active_revision_must_belong_to_that_profile(self):
        with self.db.transaction() as conn:
            conn.executemany(
                "INSERT INTO provider_profiles"
                "(id,name,active_revision_id,created_at,updated_at) VALUES(?,?,?,?,?)",
                [
                    ("p1", "Primary", None, NOW, NOW),
                    ("p2", "Secondary", None, NOW, NOW),
                ],
            )
            conn.execute(
                "INSERT INTO provider_profile_revisions"
                "(id,profile_id,version,base_url,model_id,temperature,encrypted_api_key,"
                "encryption_nonce,encryption_format_version,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    "r1", "p1", 1, "https://api.example/v1", "model-a", 0.1,
                    "ciphertext", "nonce", 1, NOW,
                ),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE provider_profiles SET active_revision_id=? WHERE id=?",
                    ("r1", "p2"),
                )

    def test_episode_current_job_must_belong_to_that_episode(self):
        with self.db.transaction() as conn:
            self._insert_profile_episode(conn)
            conn.execute(
                "INSERT INTO episodes"
                "(id,title,source_type,source_url,status,progress,message,summary_language,"
                "provider_profile_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "e2", "Other", "url", "https://example.test/other", "queued", 0,
                    "Queued", "zh", "p1", NOW, NOW,
                ),
            )
            conn.execute(
                "INSERT INTO jobs"
                "(id,episode_id,type,attempt,status,provider_profile_revision_id,"
                "submitted_at,progress,message) VALUES(?,?,?,?,?,?,?,?,?)",
                ("j1", "e1", "process_episode", 1, "queued", "r1", NOW, 0, "Queued"),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE episodes SET current_job_id=? WHERE id=?", ("j1", "e2")
                )

    def test_schema_rejects_invalid_states_progress_and_source_shapes(self):
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO provider_profiles"
                "(id,name,active_revision_id,created_at,updated_at) VALUES(?,?,?,?,?)",
                ("p1", "Primary", None, NOW, NOW),
            )
            invalid_rows = [
                ("e1", "bad", "url", "unknown", 0, None),
                ("e2", "bad", "url", "queued", 101, "https://example.test/v"),
                ("e3", "bad", "upload", "queued", 0, None),
            ]
            for episode_id, title, source_type, status, progress, source_url in invalid_rows:
                with self.subTest(episode_id=episode_id):
                    with self.assertRaises(sqlite3.IntegrityError):
                        conn.execute(
                            "INSERT INTO episodes"
                            "(id,title,source_type,source_url,status,progress,message,"
                            "summary_language,provider_profile_id,created_at,updated_at) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                episode_id,
                                title,
                                source_type,
                                source_url,
                                status,
                                progress,
                                "Queued",
                                "zh",
                                "p1",
                                NOW,
                                NOW,
                            ),
                        )

    def test_foreign_keys_are_enabled(self):
        with self.db.transaction() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO jobs"
                    "(id,episode_id,type,attempt,status,provider_profile_revision_id,"
                    "submitted_at,progress,message) VALUES(?,?,?,?,?,?,?,?,?)",
                    ("j1", "missing", "process_episode", 1, "queued", "missing", NOW, 0, "Queued"),
                )

    def test_transaction_commits_and_rolls_back(self):
        with self.db.transaction(immediate=True) as conn:
            conn.execute(
                "INSERT INTO provider_profiles"
                "(id,name,active_revision_id,created_at,updated_at) VALUES(?,?,?,?,?)",
                ("kept", "Kept", None, NOW, NOW),
            )
        with self.assertRaises(RuntimeError):
            with self.db.transaction() as conn:
                conn.execute(
                    "INSERT INTO provider_profiles"
                    "(id,name,active_revision_id,created_at,updated_at) VALUES(?,?,?,?,?)",
                    ("rolled-back", "Rolled Back", None, NOW, NOW),
                )
                raise RuntimeError("abort")
        with self.db.connect() as conn:
            ids = {
                row[0] for row in conn.execute("SELECT id FROM provider_profiles")
            }
        self.assertEqual(ids, {"kept"})

    @staticmethod
    def _insert_profile_episode(conn):
        conn.execute(
            "INSERT INTO provider_profiles"
            "(id,name,active_revision_id,created_at,updated_at) VALUES(?,?,?,?,?)",
            ("p1", "Primary", None, NOW, NOW),
        )
        conn.execute(
            "INSERT INTO provider_profile_revisions"
            "(id,profile_id,version,base_url,model_id,temperature,encrypted_api_key,"
            "encryption_nonce,encryption_format_version,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "r1",
                "p1",
                1,
                "https://api.example/v1",
                "model-a",
                0.1,
                "ciphertext",
                "nonce",
                1,
                NOW,
            ),
        )
        conn.execute(
            "UPDATE provider_profiles SET active_revision_id=? WHERE id=?", ("r1", "p1")
        )
        conn.execute(
            "INSERT INTO episodes"
            "(id,title,source_type,source_url,status,progress,message,summary_language,"
            "provider_profile_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                "e1",
                "Episode",
                "url",
                "https://example.test/video",
                "queued",
                0,
                "Queued",
                "zh",
                "p1",
                NOW,
                NOW,
            ),
        )


class DomainTests(unittest.TestCase):
    def test_enums_use_exact_serialized_values(self):
        self.assertEqual([status.value for status in EpisodeStatus], [
            "queued", "processing", "completed", "failed", "canceled"
        ])
        self.assertEqual([job_type.value for job_type in JobType], [
            "process_episode", "regenerate_summary", "regenerate_chapters"
        ])

    def test_records_are_frozen_and_match_database_columns(self):
        episode_names = {field.name for field in fields(EpisodeRecord)}
        job_names = {field.name for field in fields(JobRecord)}
        self.assertTrue({"id", "summary_language", "warnings_json", "media_path"}.issubset(episode_names))
        self.assertTrue({"id", "episode_id", "type", "status", "worker_id"}.issubset(job_names))
        record = EpisodeRecord(
            id="e1", title="Episode", source_type="url", source_path=None,
            source_url="https://example.test/video", source_content_type=None,
            media_path=None, media_content_type=None, poster_path=None,
            poster_content_type=None, duration_sec=None, resolution=None,
            status="queued", language=None, summary_language="zh", progress=0,
            message="Queued", warnings_json="[]", error_code=None,
            error_message=None, current_job_id=None, provider_profile_id="p1",
            created_at=NOW, updated_at=NOW, completed_at=None,
        )
        with self.assertRaises(FrozenInstanceError):
            record.status = "failed"


if __name__ == "__main__":
    unittest.main()
