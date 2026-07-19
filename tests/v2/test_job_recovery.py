import asyncio
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
