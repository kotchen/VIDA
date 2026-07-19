from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from ..domain import JobRecord


class JobCanceled(Exception):
    """Signal that a cooperative user cancellation reached a safe boundary."""


class JobExecutor(Protocol):
    def execute(
        self, job: JobRecord, cancel_check: Callable[[], bool]
    ) -> Awaitable[None]: ...
