from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path

from backend.v2.database import Database
from backend.v2.domain import InvalidJobState, JobRecord
from backend.v2.repositories.jobs import JobRepository


NOW = "2026-07-18T00:00:00Z"


class JobRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "jobs.sqlite3")
        self.db.initialize()
        self.repo = JobRepository(self.db)
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
                ("r1", "p1", 1, "https://api.test/v1", "model", 0.1,
                 "cipher", "nonce", 1, NOW),
            )
            conn.execute(
                "UPDATE provider_profiles SET active_revision_id='r1' WHERE id='p1'"
            )

    def tearDown(self):
        self.temp.cleanup()

    def test_enqueue_persists_attempt_revision_and_owns_process_episode(self):
        self._episode("e1", status="failed", progress=67, message="Old error")
        job = self._job("e1", "j1", attempt=2, submitted_at="2026-07-18T01:00:00Z")

        returned = self.repo.enqueue(job)

        canonical_job = replace(job, submitted_at="2026-07-18T01:00:00.000000Z")
        self.assertEqual(returned, canonical_job)
        self.assertEqual(self.repo.get("j1"), canonical_job)
        episode = self._episode_row("e1")
        self.assertEqual(
            (episode["current_job_id"], episode["status"], episode["progress"],
             episode["message"], episode["error_code"], episode["error_message"]),
            ("j1", "queued", 0, "Queued", None, None),
        )

    def test_enqueue_regeneration_does_not_change_completed_episode(self):
        self._episode("e1", status="completed", progress=100, message="Completed")
        job = replace(self._job("e1", "j1"), type="regenerate_summary")

        self.repo.enqueue(job)

        episode = self._episode_row("e1")
        self.assertEqual(
            (episode["current_job_id"], episode["status"], episode["progress"], episode["message"]),
            (None, "completed", 100, "Completed"),
        )

    def test_enqueue_rejects_nonfresh_state_without_mutating_episode(self):
        stale_jobs = (
            replace(self._job("e1", "processing"), status="processing",
                    started_at=NOW, heartbeat_at=NOW, worker_id="worker", progress=1,
                    message="Preparing"),
            replace(self._job("e1", "completed"), status="completed",
                    finished_at=NOW, progress=100, message="Completed"),
            replace(self._job("e1", "stale-queued"), started_at=NOW),
            replace(self._job("e1", "errored-queued"), error_code="old", error_message="old"),
        )
        for job in stale_jobs:
            with self.subTest(job=job.id):
                self._reset_database()
                self._episode("e1", status="failed", progress=50, message="Old error")

                with self.assertRaises((InvalidJobState, ValueError)):
                    self.repo.enqueue(job)

                self.assertIsNone(self.repo.get(job.id))
                episode = self._episode_row("e1")
                self.assertEqual(
                    (episode["current_job_id"], episode["status"], episode["progress"],
                     episode["message"]),
                    (None, "failed", 50, "Old error"),
                )

    def test_enqueue_rolls_back_job_when_episode_state_update_fails(self):
        self._episode("e1")
        with self.db.transaction() as conn:
            conn.execute("DROP TRIGGER IF EXISTS reject_episode_update")
            conn.execute(
                "CREATE TRIGGER reject_episode_update BEFORE UPDATE ON episodes "
                "BEGIN SELECT RAISE(ABORT, 'reject'); END"
            )

        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.enqueue(self._job("e1", "j1"))

        self.assertIsNone(self.repo.get("j1"))

    def test_database_rejects_two_active_jobs_and_duplicate_attempts(self):
        self._episode("e1")
        self.repo.enqueue(self._job("e1", "j1"))
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.enqueue(self._job("e1", "j2", attempt=2))

        self.repo.claim_next("worker", "2026-07-18T00:01:00Z")
        self.repo.fail("j1", "failed", "Failed", "2026-07-18T00:02:00Z")
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.enqueue(self._job("e1", "j3", attempt=1))

    def test_claims_fifo_by_submitted_time_then_id(self):
        for episode_id, job_id, submitted_at in (
            ("e3", "j3", "2026-07-18T00:00:01Z"),
            ("e2", "j2", NOW),
            ("e1", "j1", NOW),
        ):
            self._episode(episode_id)
            self.repo.enqueue(self._job(episode_id, job_id, submitted_at=submitted_at))

        claimed = [
            self.repo.claim_next("worker", f"2026-07-18T00:01:0{index}Z").id
            for index in range(3)
        ]

        self.assertEqual(claimed, ["j1", "j2", "j3"])
        self.assertIsNone(self.repo.claim_next("worker", "2026-07-18T00:02:00Z"))

    def test_fifo_normalizes_mixed_precision_and_equal_utc_instants(self):
        cases = (
            ("e-later", "j-later", "2026-07-18T00:00:00.900000Z"),
            ("e-equal-z", "j-a", "2026-07-18T00:00:00Z"),
            ("e-equal-offset", "j-z", "2026-07-18T00:00:00.000000+00:00"),
        )
        for episode_id, job_id, submitted_at in cases:
            self._episode(episode_id)
            self.repo.enqueue(self._job(episode_id, job_id, submitted_at=submitted_at))

        self.assertEqual(
            [self.repo.queue_position(job_id) for job_id in ("j-a", "j-z", "j-later")],
            [1, 2, 3],
        )
        claimed = [
            self.repo.claim_next("worker", f"2026-07-18T00:01:0{index}Z").id
            for index in range(3)
        ]
        self.assertEqual(claimed, ["j-a", "j-z", "j-later"])
        self.assertEqual(
            [self.repo.get_required(job_id).submitted_at for job_id in claimed],
            [
                "2026-07-18T00:00:00.000000Z",
                "2026-07-18T00:00:00.000000Z",
                "2026-07-18T00:00:00.900000Z",
            ],
        )

    def test_enqueue_rejects_malformed_or_non_utc_submission_time(self):
        invalid_values = (
            "not-a-time",
            "2026-07-18T00:00:00",
            "2026-07-18T08:00:00+08:00",
        )
        for index, submitted_at in enumerate(invalid_values):
            with self.subTest(submitted_at=submitted_at):
                self._reset_database()
                self._episode("e1", status="failed", progress=50, message="Old error")
                job = self._job("e1", f"j{index}", submitted_at=submitted_at)

                with self.assertRaisesRegex(ValueError, "submitted_at"):
                    self.repo.enqueue(job)

                self.assertIsNone(self.repo.get(job.id))
                episode = self._episode_row("e1")
                self.assertEqual(
                    (episode["current_job_id"], episode["status"], episode["progress"]),
                    (None, "failed", 50),
                )

    def test_enqueue_rejects_submicrosecond_precision_without_mutation(self):
        for index, submitted_at in enumerate((
            "2026-07-18T00:00:00.1234567Z",
            "2026-07-18T00:00:00.123456789+00:00",
        )):
            with self.subTest(submitted_at=submitted_at):
                self._reset_database()
                self._episode("e1", status="failed", progress=50, message="Old error")
                job = self._job("e1", f"j{index}", submitted_at=submitted_at)

                with self.assertRaisesRegex(ValueError, "submitted_at"):
                    self.repo.enqueue(job)

                self.assertIsNone(self.repo.get(job.id))
                episode = self._episode_row("e1")
                self.assertEqual(
                    (episode["current_job_id"], episode["status"], episode["progress"],
                     episode["message"]),
                    (None, "failed", 50, "Old error"),
                )

    def test_claims_fifo_once_across_concurrent_callers(self):
        self._episode("e1")
        self._episode("e2")
        self.repo.enqueue(self._job("e1", "j1", submitted_at=NOW))
        self.repo.enqueue(self._job("e2", "j2", submitted_at="2026-07-18T00:00:01Z"))
        claimed: list[str | None] = []
        errors: list[BaseException] = []
        barrier = threading.Barrier(3)

        def claim(worker: str) -> None:
            try:
                barrier.wait()
                job = self.repo.claim_next(worker, "2026-07-18T00:01:00Z")
                claimed.append(job.id if job else None)
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=claim, args=(f"w{i}",)) for i in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(set(claimed), {"j1", "j2"})

    def test_claim_sets_worker_stage_timestamps_and_process_episode(self):
        self._episode("e1")
        self.repo.enqueue(self._job("e1", "j1"))

        job = self.repo.claim_next("worker-1", "2026-07-18T00:01:00Z")

        self.assertEqual(
            (job.status, job.started_at, job.heartbeat_at, job.worker_id,
             job.progress, job.message),
            ("processing", "2026-07-18T00:01:00Z", "2026-07-18T00:01:00Z",
             "worker-1", 1, "Preparing"),
        )
        episode = self._episode_row("e1")
        self.assertEqual(
            (episode["status"], episode["progress"], episode["message"], episode["updated_at"]),
            ("processing", 1, "Preparing", "2026-07-18T00:01:00Z"),
        )

    def test_process_transitions_require_current_episode_ownership_and_roll_back(self):
        self._episode("e1")
        self.repo.enqueue(self._job("e1", "j1"))
        with self.db.transaction() as conn:
            conn.execute("UPDATE episodes SET current_job_id=NULL WHERE id='e1'")

        with self.assertRaises(InvalidJobState):
            self.repo.claim_next("worker", "2026-07-18T00:01:00Z")
        self.assertEqual(self.repo.get_required("j1").status, "queued")

        with self.db.transaction() as conn:
            conn.execute("UPDATE episodes SET current_job_id='j1' WHERE id='e1'")
        self.repo.claim_next("worker", "2026-07-18T00:01:00Z")
        with self.db.transaction() as conn:
            conn.execute("UPDATE episodes SET current_job_id=NULL WHERE id='e1'")

        with self.assertRaises(InvalidJobState):
            self.repo.complete("j1", "2026-07-18T00:02:00Z")
        self.assertEqual(self.repo.get_required("j1").status, "processing")

    def test_update_progress_refreshes_heartbeat_and_process_episode(self):
        self._episode("e1")
        self.repo.enqueue(self._job("e1", "j1"))
        self.repo.claim_next("worker", "2026-07-18T00:01:00Z")

        updated = self.repo.update_progress(
            "j1", 42, "Transcribing", "2026-07-18T00:02:00Z"
        )

        self.assertEqual((updated.progress, updated.message, updated.heartbeat_at),
                         (42, "Transcribing", "2026-07-18T00:02:00Z"))
        episode = self._episode_row("e1")
        self.assertEqual((episode["progress"], episode["message"], episode["updated_at"]),
                         (42, "Transcribing", "2026-07-18T00:02:00Z"))

    def test_regeneration_progress_does_not_change_episode(self):
        self._episode("e1", status="completed", progress=100, message="Completed")
        self.repo.enqueue(replace(self._job("e1", "j1"), type="regenerate_chapters"))
        self.repo.claim_next("worker", "2026-07-18T00:01:00Z")
        self.repo.update_progress("j1", 50, "Generating", "2026-07-18T00:02:00Z")

        episode = self._episode_row("e1")
        self.assertEqual((episode["status"], episode["progress"], episode["message"]),
                         ("completed", 100, "Completed"))

    def test_request_cancel_queued_is_immediate_and_idempotent(self):
        self._episode("e1")
        self.repo.enqueue(self._job("e1", "j1"))

        first = self.repo.request_cancel("j1", "2026-07-18T00:01:00Z")
        second = self.repo.request_cancel("j1", "2026-07-18T00:02:00Z")

        self.assertEqual(first.status, "canceled")
        self.assertEqual(first.finished_at, "2026-07-18T00:01:00Z")
        self.assertEqual(second, first)
        episode = self._episode_row("e1")
        self.assertEqual((episode["status"], episode["message"], episode["completed_at"]),
                         ("canceled", "Canceled", "2026-07-18T00:01:00Z"))

    def test_request_cancel_processing_is_cooperative(self):
        self._episode("e1")
        self.repo.enqueue(self._job("e1", "j1"))
        self.repo.claim_next("worker", "2026-07-18T00:01:00Z")

        job = self.repo.request_cancel("j1", "2026-07-18T00:02:00Z")

        self.assertEqual(
            (job.status, job.cancel_requested_at, job.finished_at, job.message),
            ("processing", "2026-07-18T00:02:00Z", None, "Canceling"),
        )
        episode = self._episode_row("e1")
        self.assertEqual((episode["status"], episode["message"]),
                         ("processing", "Canceling"))

        repeated = self.repo.request_cancel("j1", "2026-07-18T00:03:00Z")
        self.assertEqual(repeated.cancel_requested_at, "2026-07-18T00:02:00Z")

    def test_terminal_transitions_update_job_and_owning_episode(self):
        cases = (
            ("completed", lambda: self.repo.complete("j1", "2026-07-18T00:03:00Z"),
             100, "Completed", None, None),
            ("failed", lambda: self.repo.fail(
                "j1", "transcription_failed", "boom", "2026-07-18T00:03:00Z"
            ), 42, "boom", "transcription_failed", "boom"),
            ("canceled", lambda: self.repo.mark_canceled(
                "j1", "2026-07-18T00:03:00Z"
            ), 42, "Canceled", None, None),
        )
        for status, transition, progress, message, code, error in cases:
            with self.subTest(status=status):
                self._reset_database()
                self._episode("e1")
                self.repo.enqueue(self._job("e1", "j1"))
                self.repo.claim_next("worker", "2026-07-18T00:01:00Z")
                self.repo.update_progress("j1", 42, "Working", "2026-07-18T00:02:00Z")
                if status == "canceled":
                    self.repo.request_cancel("j1", "2026-07-18T00:02:30Z")

                job = transition()

                self.assertEqual(
                    (job.status, job.progress, job.message, job.error_code,
                     job.error_message, job.finished_at, job.heartbeat_at),
                    (status, progress, message, code, error,
                     "2026-07-18T00:03:00Z", "2026-07-18T00:03:00Z"),
                )
                episode = self._episode_row("e1")
                self.assertEqual(
                    (episode["status"], episode["progress"], episode["message"],
                     episode["error_code"], episode["error_message"],
                     episode["completed_at"], episode["updated_at"]),
                    (status, progress, message, code, error,
                     "2026-07-18T00:03:00Z", "2026-07-18T00:03:00Z"),
                )

    def test_mark_canceled_requires_prior_processing_cancel_request(self):
        self._episode("e1")
        self.repo.enqueue(self._job("e1", "j1"))
        self.repo.claim_next("worker", "2026-07-18T00:01:00Z")

        with self.assertRaises(InvalidJobState):
            self.repo.mark_canceled("j1", "2026-07-18T00:02:00Z")

        job = self.repo.get_required("j1")
        episode = self._episode_row("e1")
        self.assertEqual(
            (job.status, job.cancel_requested_at, job.finished_at, job.message),
            ("processing", None, None, "Preparing"),
        )
        self.assertEqual(
            (episode["status"], episode["message"], episode["completed_at"]),
            ("processing", "Preparing", None),
        )

        self.repo.request_cancel("j1", "2026-07-18T00:02:30Z")
        canceled = self.repo.mark_canceled("j1", "2026-07-18T00:03:00Z")
        self.assertEqual(
            (canceled.status, canceled.cancel_requested_at, canceled.finished_at),
            ("canceled", "2026-07-18T00:02:30Z", "2026-07-18T00:03:00Z"),
        )

    def test_illegal_progress_terminal_and_cancel_transitions_raise_domain_error(self):
        self._episode("e1")
        self.repo.enqueue(self._job("e1", "j1"))
        operations = (
            lambda: self.repo.update_progress("j1", 10, "Working", NOW),
            lambda: self.repo.complete("j1", NOW),
            lambda: self.repo.fail("j1", "code", "error", NOW),
            lambda: self.repo.mark_canceled("j1", NOW),
        )
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaises(InvalidJobState):
                operation()

        self.repo.claim_next("worker", NOW)
        self.repo.complete("j1", "2026-07-18T00:01:00Z")
        with self.assertRaises(InvalidJobState):
            self.repo.request_cancel("j1", "2026-07-18T00:02:00Z")

    def test_missing_job_raises_key_error(self):
        operations = (
            lambda: self.repo.get_required("missing"),
            lambda: self.repo.update_progress("missing", 10, "Working", NOW),
            lambda: self.repo.request_cancel("missing", NOW),
            lambda: self.repo.complete("missing", NOW),
            lambda: self.repo.fail("missing", "code", "error", NOW),
            lambda: self.repo.mark_canceled("missing", NOW),
        )
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaises(KeyError):
                operation()

    def test_recovery_requeues_processing_without_new_job_or_attempt(self):
        self._episode("e1")
        self.repo.enqueue(self._job("e1", "j1", attempt=3))
        self.repo.claim_next("worker", "2026-07-18T00:00:01Z")
        self.repo.request_cancel("j1", "2026-07-18T00:00:02Z")

        self.assertEqual(self.repo.recover_interrupted("Restarted"), 1)

        job = self.repo.get_required("j1")
        self.assertEqual(
            (job.id, job.attempt, job.status, job.started_at, job.finished_at,
             job.cancel_requested_at, job.heartbeat_at, job.worker_id, job.progress,
             job.message, job.error_code, job.error_message),
            ("j1", 3, "queued", None, None, None, None, None, 0,
             "Restarted", None, None),
        )
        self.assertEqual(self.repo.queue_position("j1"), 1)
        episode = self._episode_row("e1")
        self.assertEqual(
            (episode["current_job_id"], episode["status"], episode["progress"],
             episode["message"], episode["completed_at"]),
            ("j1", "queued", 0, "Restarted", None),
        )

    def test_recovery_rolls_back_when_process_job_no_longer_owns_episode(self):
        self._episode("e1")
        self.repo.enqueue(self._job("e1", "j1"))
        self.repo.claim_next("worker", "2026-07-18T00:01:00Z")
        with self.db.transaction() as conn:
            conn.execute("UPDATE episodes SET current_job_id=NULL WHERE id='e1'")

        with self.assertRaises(InvalidJobState):
            self.repo.recover_interrupted("Restarted")

        self.assertEqual(self.repo.get_required("j1").status, "processing")

    def test_recovery_and_terminal_transitions_leave_regeneration_episode_completed(self):
        self._episode("e1", status="completed", progress=100, message="Completed")
        self.repo.enqueue(replace(self._job("e1", "j1"), type="regenerate_summary"))
        self.repo.claim_next("worker", "2026-07-18T00:01:00Z")
        self.assertEqual(self.repo.recover_interrupted("Restarted"), 1)
        self.repo.claim_next("worker", "2026-07-18T00:02:00Z")
        self.repo.fail("j1", "summary_failed", "boom", "2026-07-18T00:03:00Z")

        episode = self._episode_row("e1")
        self.assertEqual((episode["status"], episode["progress"], episode["message"]),
                         ("completed", 100, "Completed"))

    def test_queue_position_is_one_based_only_for_queued_jobs(self):
        for episode_id, job_id, submitted_at in (
            ("e1", "j2", NOW), ("e2", "j1", NOW),
            ("e3", "j3", "2026-07-18T00:00:01Z"),
        ):
            self._episode(episode_id)
            self.repo.enqueue(self._job(episode_id, job_id, submitted_at=submitted_at))

        self.assertEqual([self.repo.queue_position(job) for job in ("j1", "j2", "j3")],
                         [1, 2, 3])
        self.repo.claim_next("worker", "2026-07-18T00:01:00Z")
        self.assertIsNone(self.repo.queue_position("j1"))
        self.assertEqual(self.repo.queue_position("j2"), 1)
        self.assertIsNone(self.repo.queue_position("missing"))

    def test_queue_position_reads_target_and_count_in_one_sql_snapshot(self):
        self._episode("e1")
        self.repo.enqueue(self._job("e1", "j1"))
        statements: list[str] = []
        database = TracingDatabase(self.db.path, statements)

        self.assertEqual(JobRepository(database).queue_position("j1"), 1)

        selects = [statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]
        self.assertEqual(len(selects), 1)

    def _episode(self, episode_id: str, *, status: str = "queued", progress: int = 0,
                 message: str = "Queued") -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO episodes"
                "(id,title,source_type,source_url,status,progress,message,summary_language,"
                "error_code,error_message,provider_profile_id,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (episode_id, episode_id, "url", f"https://test/{episode_id}", status,
                 progress, message, "zh", "old_error" if status == "failed" else None,
                 "Old error" if status == "failed" else None, "p1", NOW, NOW),
            )

    def _job(self, episode_id: str, job_id: str, *, attempt: int = 1,
             submitted_at: str = NOW) -> JobRecord:
        return JobRecord(
            id=job_id, episode_id=episode_id, type="process_episode", attempt=attempt,
            status="queued", provider_profile_revision_id="r1",
            submitted_at=submitted_at, started_at=None, finished_at=None,
            cancel_requested_at=None, heartbeat_at=None, worker_id=None, progress=0,
            message="Queued", error_code=None, error_message=None,
        )

    def _episode_row(self, episode_id: str):
        with self.db.connect() as conn:
            return conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()

    def _reset_database(self) -> None:
        self.temp.cleanup()
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "jobs.sqlite3")
        self.db.initialize()
        self.repo = JobRepository(self.db)
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
                ("r1", "p1", 1, "https://api.test/v1", "model", 0.1,
                 "cipher", "nonce", 1, NOW),
            )
            conn.execute("UPDATE provider_profiles SET active_revision_id='r1' WHERE id='p1'")


class TracingDatabase(Database):
    def __init__(self, path: Path, statements: list[str]):
        super().__init__(path)
        self._statements = statements

    def connect(self):
        connection = super().connect()
        connection.set_trace_callback(self._statements.append)
        return connection


if __name__ == "__main__":
    unittest.main()
