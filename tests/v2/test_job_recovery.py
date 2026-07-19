import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI

from backend.v2.bootstrap import build_test_runtime
from backend.v2.database import Database
from backend.v2.domain import JobRecord
from backend.v2.jobs.scheduler import Scheduler
from backend.v2.repositories.jobs import JobRepository


NOW = "2026-07-18T00:00:00Z"


class RecordingExecutor:
    def __init__(self):
        self.started_ids: list[str] = []
        self.cancel_checks: list[bool] = []

    async def execute(self, job, cancel_check):
        self.started_ids.append(job.id)
        self.cancel_checks.append(cancel_check())


class CancellationBlockingExecutor:
    def __init__(self):
        self.started = asyncio.Event()
        self.restarted = asyncio.Event()
        self.canceled = asyncio.Event()
        self.started_count = 0

    async def execute(self, job, cancel_check):
        self.started_count += 1
        self.started.set()
        if self.started_count == 2:
            self.restarted.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.canceled.set()
            raise


class DelayedCancellationExecutor:
    def __init__(self):
        self.started = asyncio.Event()
        self.cancellation_cleanup_started = asyncio.Event()
        self.release_cancellation_cleanup = asyncio.Event()
        self.restarted = asyncio.Event()
        self.started_count = 0

    async def execute(self, job, cancel_check):
        self.started_count += 1
        self.started.set()
        if self.started_count == 2:
            self.restarted.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancellation_cleanup_started.set()
            await self.release_cancellation_cleanup.wait()
            raise


class CountingJobRepository(JobRepository):
    def __init__(self, database):
        super().__init__(database)
        self.recovery_calls = 0

    def recover_interrupted(self, message):
        self.recovery_calls += 1
        return super().recover_interrupted(message)


class BlockingClaimRepository(JobRepository):
    def __init__(self, database):
        super().__init__(database)
        self.claim_entered = threading.Event()
        self.release_claim = threading.Event()
        self.claim_finished = threading.Event()
        self._claim_count = 0

    def claim_next(self, worker_id, now):
        self._claim_count += 1
        if self._claim_count == 1:
            self.claim_entered.set()
            self.release_claim.wait()
            try:
                return super().claim_next(worker_id, now)
            finally:
                self.claim_finished.set()
        return super().claim_next(worker_id, now)


class BlockingRecoveryRepository(JobRepository):
    def __init__(self, database):
        super().__init__(database)
        self.recovery_entered = threading.Event()
        self.release_recovery = threading.Event()
        self.recovery_finished = threading.Event()
        self.recovery_calls = 0

    def recover_interrupted(self, message):
        self.recovery_calls += 1
        if self.recovery_calls == 1:
            self.recovery_entered.set()
            self.release_recovery.wait()
            try:
                return super().recover_interrupted(message)
            finally:
                self.recovery_finished.set()
        return super().recover_interrupted(message)


class JobRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "recovery.sqlite3")
        self.database.initialize()
        self.jobs = JobRepository(self.database)
        self._seed()
        self.scheduler: Scheduler | None = None

    async def asyncTearDown(self):
        if self.scheduler is not None:
            await self.scheduler.stop(0)
        self.temp.cleanup()

    async def test_start_recovers_processing_job_before_workers_claim(self):
        self.jobs.claim_next("dead-worker", "2026-07-18T00:01:00Z")
        self.jobs.request_cancel("j0", "2026-07-18T00:02:00Z")
        executor = RecordingExecutor()
        self.scheduler = Scheduler(self.jobs, executor, 1, 30)

        await self.scheduler.start()
        await self.scheduler.wait_until_idle(timeout_sec=1)

        recovered = self.jobs.get_required("j0")
        self.assertEqual(executor.started_ids, ["j0"])
        self.assertEqual(executor.cancel_checks, [False])
        self.assertEqual(recovered.status, "completed")
        self.assertNotEqual(recovered.worker_id, "dead-worker")
        with self.database.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1)

    async def test_start_and_stop_are_idempotent(self):
        executor = RecordingExecutor()
        self.scheduler = Scheduler(self.jobs, executor, 2, 30)

        await self.scheduler.start()
        await self.scheduler.start()
        self.assertEqual(self.scheduler.worker_count, 2)
        await self.scheduler.stop(0.5)
        await self.scheduler.stop(0.5)
        self.assertEqual(self.scheduler.worker_count, 0)

    async def test_concurrent_start_recovers_once_and_creates_one_worker_pool(self):
        executor = RecordingExecutor()
        self.scheduler = Scheduler(self.jobs, executor, 2, 30)
        real_to_thread = asyncio.to_thread
        recovery_entered = asyncio.Event()
        release_recovery = asyncio.Event()
        recovery_calls = 0

        async def controlled_to_thread(function, *args):
            nonlocal recovery_calls
            if function.__name__ == "recover_interrupted":
                recovery_calls += 1
                recovery_entered.set()
                await release_recovery.wait()
            if function.__name__ == "claim_next":
                return None
            return await real_to_thread(function, *args)

        with patch("backend.v2.jobs.scheduler.asyncio.to_thread", controlled_to_thread):
            starts = [asyncio.create_task(self.scheduler.start()) for _ in range(2)]
            await asyncio.wait_for(recovery_entered.wait(), 1)
            await asyncio.sleep(0)
            release_recovery.set()
            await asyncio.gather(*starts)
            live_before_stop = self._live_scheduler_workers()
            await self.scheduler.stop(0.5)
            live_after_stop = self._live_scheduler_workers()

        await self._cancel_tasks(live_after_stop)
        self.assertEqual(recovery_calls, 1)
        self.assertEqual(len(live_before_stop), 2)
        self.assertEqual(live_after_stop, [])

    async def test_stop_waits_for_in_progress_start_then_cleans_the_pool(self):
        self.scheduler = Scheduler(self.jobs, RecordingExecutor(), 2, 30)
        real_to_thread = asyncio.to_thread
        recovery_entered = asyncio.Event()
        release_recovery = asyncio.Event()

        async def controlled_to_thread(function, *args):
            if function.__name__ == "recover_interrupted":
                recovery_entered.set()
                await release_recovery.wait()
            if function.__name__ == "claim_next":
                return None
            return await real_to_thread(function, *args)

        with patch("backend.v2.jobs.scheduler.asyncio.to_thread", controlled_to_thread):
            start = asyncio.create_task(self.scheduler.start())
            await asyncio.wait_for(recovery_entered.wait(), 1)
            stop = asyncio.create_task(self.scheduler.stop(0.5))
            await asyncio.sleep(0)
            release_recovery.set()
            await asyncio.gather(start, stop)
            leaked = self._live_scheduler_workers()

        await self._cancel_tasks(leaked)
        self.assertEqual(self.scheduler.worker_count, 0)
        self.assertEqual(leaked, [])

    async def test_canceled_stop_finishes_cleanup_and_allows_restart(self):
        executor = CancellationBlockingExecutor()
        self.scheduler = Scheduler(self.jobs, executor, 1, 30)
        await self.scheduler.start()
        await asyncio.wait_for(executor.started.wait(), 1)

        stop = asyncio.create_task(self.scheduler.stop(30))
        await asyncio.sleep(0)
        stop.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await stop

        await asyncio.wait_for(executor.canceled.wait(), 1)
        self.assertEqual(self.scheduler.worker_count, 0)
        self.assertEqual(self.jobs.get_required("j0").status, "processing")
        await self.scheduler.start()
        self.assertEqual(self.scheduler.worker_count, 1)
        await asyncio.wait_for(executor.restarted.wait(), 1)

    async def test_repeated_cancel_during_forced_join_still_cleans_and_restarts(self):
        jobs = CountingJobRepository(self.database)
        executor = DelayedCancellationExecutor()
        self.scheduler = Scheduler(jobs, executor, 1, 30)
        await self.scheduler.start()
        await asyncio.wait_for(executor.started.wait(), 1)

        stop = asyncio.create_task(self.scheduler.stop(0))
        await asyncio.wait_for(executor.cancellation_cleanup_started.wait(), 1)
        stop.cancel()
        await asyncio.sleep(0)
        stop.cancel()
        executor.release_cancellation_cleanup.set()
        with self.assertRaises(asyncio.CancelledError):
            await stop

        self.assertEqual(self.scheduler.worker_count, 0)
        self.assertEqual(self._live_scheduler_workers(), [])
        self.assertEqual(jobs.recovery_calls, 1)
        await self.scheduler.start()
        self.assertEqual(jobs.recovery_calls, 2)
        self.assertEqual(self.scheduler.worker_count, 1)
        await asyncio.wait_for(executor.restarted.wait(), 1)

    async def test_canceled_stop_joins_claim_thread_before_restart_recovery(self):
        jobs = BlockingClaimRepository(self.database)
        executor = CancellationBlockingExecutor()
        self.scheduler = Scheduler(jobs, executor, 1, 30)
        await self.scheduler.start()
        self.assertTrue(await asyncio.to_thread(jobs.claim_entered.wait, 1))

        stop = asyncio.create_task(self.scheduler.stop(30))
        await asyncio.sleep(0)
        stop.cancel()
        await self._yield_turns(20)
        finished_before_claim = stop.done()
        jobs.release_claim.set()
        with self.assertRaises(asyncio.CancelledError):
            await stop
        self.assertTrue(await asyncio.to_thread(jobs.claim_finished.wait, 1))

        self.assertFalse(finished_before_claim)
        self.assertEqual(self.jobs.get_required("j0").status, "processing")
        await self.scheduler.start()
        await asyncio.wait_for(executor.started.wait(), 1)
        self.assertEqual(self.jobs.get_required("j0").status, "processing")

    async def test_canceled_start_joins_recovery_before_replacement_start(self):
        jobs = BlockingRecoveryRepository(self.database)
        executor = CancellationBlockingExecutor()
        self.scheduler = Scheduler(jobs, executor, 1, 30)
        canceled_start = asyncio.create_task(self.scheduler.start())
        self.assertTrue(await asyncio.to_thread(jobs.recovery_entered.wait, 1))

        canceled_start.cancel()
        replacement_start = asyncio.create_task(self.scheduler.start())
        await self._yield_turns(20)
        canceled_finished_before_recovery = canceled_start.done()
        replacement_started_before_recovery = self.scheduler.worker_count
        jobs.release_recovery.set()
        with self.assertRaises(asyncio.CancelledError):
            await canceled_start
        await replacement_start
        self.assertTrue(await asyncio.to_thread(jobs.recovery_finished.wait, 1))
        await asyncio.wait_for(executor.started.wait(), 1)

        self.assertFalse(canceled_finished_before_recovery)
        self.assertEqual(replacement_started_before_recovery, 0)
        self.assertEqual(jobs.recovery_calls, 2)
        self.assertEqual(self.jobs.get_required("j0").status, "processing")

    async def test_runtime_uses_configured_workers_and_fastapi_lifespan(self):
        executor = RecordingExecutor()
        runtime = build_test_runtime(
            Path(self.temp.name) / "runtime", b"m" * 32,
            max_concurrent_jobs=3, executor=executor,
        )
        app = FastAPI()
        runtime.install(app)

        self.assertEqual(runtime.scheduler.worker_count, 0)
        async with app.router.lifespan_context(app):
            self.assertEqual(runtime.scheduler.worker_count, 3)
        self.assertEqual(runtime.scheduler.worker_count, 0)

    def _seed(self) -> None:
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
            conn.execute(
                "INSERT INTO episodes"
                "(id,title,source_type,source_url,status,progress,message,summary_language,"
                "provider_profile_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("e0", "Episode", "url", "https://test/0", "queued", 0, "Queued",
                 "zh", "p1", NOW, NOW),
            )
        self.jobs.enqueue(
            JobRecord("j0", "e0", "process_episode", 1, "queued", "r1", NOW,
                      None, None, None, None, None, 0, "Queued", None, None)
        )

    @staticmethod
    def _live_scheduler_workers() -> list[asyncio.Task]:
        return [
            task for task in asyncio.all_tasks()
            if task.get_name().startswith("vida-v2-worker-") and not task.done()
        ]

    @staticmethod
    async def _cancel_tasks(tasks: list[asyncio.Task]) -> None:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    async def _yield_turns(count: int) -> None:
        for _ in range(count):
            await asyncio.sleep(0)


if __name__ == "__main__":
    unittest.main()
