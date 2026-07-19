from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from ..domain import JobRecord
from ..repositories.jobs import JobRepository
from .models import JobCanceled, JobExecutor


class Scheduler:
    def __init__(
        self,
        jobs: JobRepository,
        executor: JobExecutor,
        max_workers: int,
        poll_interval_sec: float,
    ):
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        if poll_interval_sec <= 0:
            raise ValueError("poll_interval_sec must be positive")
        self._jobs = jobs
        self._executor = executor
        self._max_workers = max_workers
        self._poll_interval_sec = poll_interval_sec
        self._worker_prefix = uuid.uuid4().hex
        self._workers: list[asyncio.Task[None]] = []
        self._wakeup = asyncio.Event()
        self._idle = asyncio.Event()
        self._claim_lock = asyncio.Lock()
        self._start_condition = asyncio.Condition()
        self._lifecycle_lock = asyncio.Lock()
        self._claimed_sequence = 0
        self._next_start_sequence = 0
        self._waiting_workers: set[int] = set()
        self._active_jobs = 0
        self._accepting_claims = False

    @property
    def worker_count(self) -> int:
        return sum(not worker.done() for worker in self._workers)

    @property
    def max_workers(self) -> int:
        return self._max_workers

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._workers:
                return
            await asyncio.to_thread(
                self._jobs.recover_interrupted, "Service restarted; job requeued"
            )
            self._accepting_claims = True
            self._idle.clear()
            self._waiting_workers.clear()
            self._active_jobs = 0
            self._claimed_sequence = 0
            self._next_start_sequence = 0
            self._workers = [
                asyncio.create_task(self._worker(index), name=f"vida-v2-worker-{index}")
                for index in range(self._max_workers)
            ]

    def notify(self) -> None:
        self._idle.clear()
        self._wakeup.set()

    async def wait_until_idle(self, timeout_sec: float) -> None:
        await asyncio.wait_for(self._idle.wait(), timeout=max(0, timeout_sec))

    async def stop(self, grace_period_sec: float) -> None:
        async with self._lifecycle_lock:
            if not self._workers:
                return
            self._accepting_claims = False
            self._wakeup.set()
            cancellation_requested = False
            try:
                _, pending = await asyncio.wait(
                    self._workers, timeout=max(0, grace_period_sec)
                )
            except asyncio.CancelledError:
                cancellation_requested = True
                _consume_current_cancellation()
                pending = self._workers
            cleanup = asyncio.create_task(self._finish_stop(pending))
            cancellation_requested = await _await_cancellation_safe(
                cleanup, cancellation_requested
            )
            if cancellation_requested:
                raise asyncio.CancelledError

    async def _finish_stop(self, workers) -> None:
        try:
            await self._cancel_and_join(workers)
        finally:
            self._clear_workers()

    @staticmethod
    async def _cancel_and_join(workers) -> None:
        pending = list(workers)
        for worker in pending:
            worker.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def _clear_workers(self) -> None:
        self._workers.clear()
        self._waiting_workers.clear()
        self._active_jobs = 0

    async def _worker(self, index: int) -> None:
        worker_id = f"{self._worker_prefix}-{index}"
        while self._accepting_claims:
            self._waiting_workers.discard(index)
            try:
                async with self._claim_lock:
                    job = await asyncio.to_thread(
                        self._jobs.claim_next, worker_id, _utc_now()
                    )
                    sequence = self._claimed_sequence
                    if job is not None:
                        self._claimed_sequence += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                await self._wait_for_work(index)
                continue
            if job is None:
                await self._wait_for_work(index)
                continue
            await self._wait_for_start_turn(sequence)
            self._idle.clear()
            self._active_jobs += 1
            try:
                await self._execute(job)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A database/state failure for one job must not terminate the worker loop.
                pass
            finally:
                self._active_jobs -= 1

    async def _wait_for_start_turn(self, sequence: int) -> None:
        async with self._start_condition:
            await self._start_condition.wait_for(
                lambda: sequence == self._next_start_sequence
            )
            self._next_start_sequence += 1
            self._start_condition.notify_all()

    async def _wait_for_work(self, index: int) -> None:
        self._waiting_workers.add(index)
        if (
            self._active_jobs == 0
            and len(self._waiting_workers) == self._max_workers
        ):
            self._idle.set()
        if not self._accepting_claims:
            return
        try:
            await asyncio.wait_for(
                self._wakeup.wait(), timeout=self._poll_interval_sec
            )
        except asyncio.TimeoutError:
            pass
        finally:
            self._wakeup.clear()

    async def _execute(self, job: JobRecord) -> None:
        try:
            await self._executor.execute(job, lambda: self._cancel_requested(job.id))
        except asyncio.CancelledError:
            raise
        except JobCanceled:
            await asyncio.to_thread(self._jobs.mark_canceled, job.id, _utc_now())
        except Exception:
            await asyncio.to_thread(
                self._jobs.fail,
                job.id,
                "job_execution_failed",
                "Job execution failed",
                _utc_now(),
            )
        else:
            await asyncio.to_thread(self._jobs.complete, job.id, _utc_now())

    def _cancel_requested(self, job_id: str) -> bool:
        current = self._jobs.get(job_id)
        return current is not None and current.cancel_requested_at is not None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


async def _await_cancellation_safe(
    cleanup: asyncio.Task[None], cancellation_requested: bool
) -> bool:
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            cancellation_requested = True
            _consume_current_cancellation()
    cleanup.result()
    return cancellation_requested


def _consume_current_cancellation() -> None:
    current = asyncio.current_task()
    if current is not None:
        current.uncancel()
