import asyncio
import tempfile
import unittest
from pathlib import Path

from backend.v2.database import Database
from backend.v2.domain import JobRecord
from backend.v2.jobs.models import JobCanceled
from backend.v2.jobs.scheduler import Scheduler
from backend.v2.repositories.jobs import JobRepository


NOW = "2026-07-18T00:00:00Z"


class BlockingExecutor:
    def __init__(self):
        self.started_ids: list[str] = []
        self.active = 0
        self.max_active = 0
        self._changed = asyncio.Condition()
        self._release = asyncio.Event()
        self.canceled = asyncio.Event()

    async def execute(self, job, cancel_check):
        async with self._changed:
            self.started_ids.append(job.id)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self._changed.notify_all()
        try:
            await self._release.wait()
        except asyncio.CancelledError:
            self.canceled.set()
            raise
        finally:
            async with self._changed:
                self.active -= 1
                self._changed.notify_all()

    async def wait_until_started(self, count: int) -> None:
        async with self._changed:
            await asyncio.wait_for(
                self._changed.wait_for(lambda: len(self.started_ids) >= count), 1
            )

    def release_all(self) -> None:
        self._release.set()


class FailingThenRecordingExecutor:
    def __init__(self):
        self.started_ids: list[str] = []

    async def execute(self, job, cancel_check):
        self.started_ids.append(job.id)
        if len(self.started_ids) == 1:
            raise RuntimeError("upstream secret detail")


class CooperativeCancelExecutor:
    def __init__(self):
        self.started = asyncio.Event()
        self.check = asyncio.Event()

    async def execute(self, job, cancel_check):
        self.started.set()
        await self.check.wait()
        if cancel_check():
            raise JobCanceled()


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "scheduler.sqlite3")
        self.database.initialize()
        self.jobs = JobRepository(self.database)
        self._seed_profile()
        self.scheduler: Scheduler | None = None

    async def asyncTearDown(self):
        if self.scheduler is not None:
            await self.scheduler.stop(0)
        self.temp.cleanup()

    async def test_never_exceeds_configured_concurrency_and_starts_fifo(self):
        executor = BlockingExecutor()
        self.scheduler = Scheduler(self.jobs, executor, max_workers=2, poll_interval_sec=1)
        await self.scheduler.start()
        for index in range(5):
            await asyncio.to_thread(self.enqueue, index)

        self.scheduler.notify()
        await executor.wait_until_started(2)

        self.assertEqual(executor.max_active, 2)
        self.assertEqual(self.count_status("processing"), 2)
        executor.release_all()
        await self.scheduler.wait_until_idle(timeout_sec=2)
        self.assertEqual(executor.started_ids, [f"j{index}" for index in range(5)])
        self.assertEqual(self.count_status("completed"), 5)

    async def test_notify_wakes_workers_after_an_empty_queue(self):
        executor = BlockingExecutor()
        self.scheduler = Scheduler(self.jobs, executor, max_workers=1, poll_interval_sec=30)
        await self.scheduler.start()
        await self.scheduler.wait_until_idle(timeout_sec=1)

        self.enqueue(0)
        self.scheduler.notify()

        await executor.wait_until_started(1)
        executor.release_all()
        await self.scheduler.wait_until_idle(timeout_sec=1)
        self.assertEqual(executor.started_ids, ["j0"])

    async def test_job_failure_is_sanitized_and_worker_continues(self):
        executor = FailingThenRecordingExecutor()
        self.enqueue(0)
        self.enqueue(1)
        self.scheduler = Scheduler(self.jobs, executor, max_workers=1, poll_interval_sec=1)

        await self.scheduler.start()
        await self.scheduler.wait_until_idle(timeout_sec=2)

        failed = self.jobs.get_required("j0")
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.error_code, "job_execution_failed")
        self.assertEqual(failed.error_message, "Job execution failed")
        self.assertNotIn("secret", failed.error_message)
        self.assertEqual(self.jobs.get_required("j1").status, "completed")
        self.assertEqual(executor.started_ids, ["j0", "j1"])

    async def test_cooperative_cancel_marks_job_canceled(self):
        executor = CooperativeCancelExecutor()
        self.enqueue(0)
        self.scheduler = Scheduler(self.jobs, executor, max_workers=1, poll_interval_sec=1)
        await self.scheduler.start()
        await asyncio.wait_for(executor.started.wait(), 1)
        self.jobs.request_cancel("j0", "2026-07-18T00:01:00Z")

        executor.check.set()
        await self.scheduler.wait_until_idle(timeout_sec=1)

        self.assertEqual(self.jobs.get_required("j0").status, "canceled")

    async def test_zero_grace_stop_cancels_executor_but_leaves_row_processing(self):
        executor = BlockingExecutor()
        self.enqueue(0)
        self.enqueue(1)
        self.scheduler = Scheduler(self.jobs, executor, max_workers=1, poll_interval_sec=1)
        await self.scheduler.start()
        await executor.wait_until_started(1)

        await self.scheduler.stop(0)

        await asyncio.wait_for(executor.canceled.wait(), 1)
        self.assertEqual(self.jobs.get_required("j0").status, "processing")
        self.assertEqual(self.jobs.get_required("j1").status, "queued")

    async def test_graceful_stop_allows_an_active_job_to_finish(self):
        executor = BlockingExecutor()
        self.enqueue(0)
        self.scheduler = Scheduler(self.jobs, executor, max_workers=1, poll_interval_sec=1)
        await self.scheduler.start()
        await executor.wait_until_started(1)

        stop = asyncio.create_task(self.scheduler.stop(1))
        executor.release_all()
        await stop

        self.assertEqual(self.jobs.get_required("j0").status, "completed")
        self.assertEqual(self.scheduler.worker_count, 0)

    def enqueue(self, index: int) -> None:
        episode_id = f"e{index}"
        job_id = f"j{index}"
        with self.database.transaction() as conn:
            conn.execute(
                "INSERT INTO episodes"
                "(id,title,source_type,source_url,status,progress,message,summary_language,"
                "provider_profile_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (episode_id, episode_id, "url", f"https://test/{index}", "queued", 0,
                 "Queued", "zh", "p1", NOW, NOW),
            )
        self.jobs.enqueue(
            JobRecord(job_id, episode_id, "process_episode", 1, "queued", "r1",
                      f"2026-07-18T00:00:{index:02d}Z", None, None, None, None,
                      None, 0, "Queued", None, None)
        )

    def count_status(self, status: str) -> int:
        with self.database.connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status=?", (status,)
            ).fetchone()[0]

    def _seed_profile(self) -> None:
        with self.database.transaction() as conn:
            conn.execute(
                "INSERT INTO provider_profiles"
                "(id,name,active_revision_id,created_at,updated_at) VALUES(?,?,?,?,?)",
                ("p1", "Primary", None, NOW, NOW),
            )
            conn.execute(
                "INSERT INTO provider_profile_revisions"
                "(id,profile_id,version,base_url,model_id,temperature,encrypted_api_key,"
                "encryption_nonce,encryption_format_version,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("r1", "p1", 1, "https://api.test/v1", "model", 0.1, "cipher", "nonce", 1, NOW),
            )
            conn.execute("UPDATE provider_profiles SET active_revision_id='r1' WHERE id='p1'")


if __name__ == "__main__":
    unittest.main()
