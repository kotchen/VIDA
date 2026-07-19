from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from ..domain import JobRecord


class JobCanceled(Exception):
    """Signal that a cooperative user cancellation reached a safe boundary."""


class TranscriptionFailed(RuntimeError):
    """A sanitized core-pipeline transcription failure."""


class PipelinePersistenceError(RuntimeError):
    """A sanitized infrastructure failure from pipeline state persistence."""


class JobExecutor(Protocol):
    def execute(
        self, job: JobRecord, cancel_check: Callable[[], bool]
    ) -> Awaitable[None]: ...
