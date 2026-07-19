from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from fastapi import FastAPI
from openai import OpenAI

from .config import V2Settings
from .container import ProviderTestResult, V2Runtime
from .crypto import CredentialCipher
from .database import Database
from .errors import V2Error
from .domain import JobRecord
from .jobs.models import JobExecutor
from .jobs.scheduler import Scheduler
from .repositories.jobs import JobRepository
from .repositories.provider_profiles import ProviderProfileRepository
from .services.provider_profiles import (
    ProviderProfileService,
    ProviderRevisionCredentials,
)


def install_v2(app: FastAPI, project_root: Path) -> V2Runtime:
    root = Path(project_root)
    runtime = V2Runtime(_initializer=lambda: _build_environment_runtime(root))
    runtime.install(app)
    return runtime


def build_test_runtime(
    data_dir: Path,
    master_key: bytes,
    *,
    max_concurrent_jobs: int = 2,
    executor: JobExecutor | None = None,
) -> V2Runtime:
    directory = Path(data_dir)
    settings = V2Settings(
        data_dir=directory,
        database_path=directory / "vida-v2.sqlite3",
        max_concurrent_jobs=max_concurrent_jobs,
        upload_max_bytes=5 * 1024**3,
        master_key=master_key,
    )
    return _build_runtime(settings, executor)


def _build_environment_runtime(project_root: Path) -> V2Runtime:
    return _build_runtime(V2Settings.from_environ(os.environ, project_root))


def _build_runtime(
    settings: V2Settings, executor: JobExecutor | None = None
) -> V2Runtime:
    database = Database(settings.database_path)
    database.initialize()
    repository = ProviderProfileRepository(database)
    service = ProviderProfileService(repository, CredentialCipher(settings.master_key))
    job_repository = JobRepository(database)
    job_executor = executor or _UnavailableJobExecutor()
    scheduler = Scheduler(
        job_repository,
        job_executor,
        max_workers=settings.max_concurrent_jobs,
        poll_interval_sec=1.0,
    )
    return V2Runtime(
        settings=settings,
        database=database,
        job_repository=job_repository,
        job_executor=job_executor,
        scheduler=scheduler,
        provider_profile_repository=repository,
        provider_profile_service=service,
        provider_tester=test_provider_connection,
    )


class _UnavailableJobExecutor:
    async def execute(self, job: JobRecord, cancel_check) -> None:
        raise RuntimeError("v2 job executor is not configured")


async def test_provider_connection(
    credentials: ProviderRevisionCredentials,
) -> ProviderTestResult:
    try:
        client = OpenAI(api_key=credentials.api_key, base_url=credentials.base_url)
        try:
            started = time.monotonic()
            models = await asyncio.to_thread(client.models.list)
            latency_ms = max(0, round((time.monotonic() - started) * 1000))
        finally:
            await asyncio.to_thread(client.close)
        available = any(model.id == credentials.model_id for model in models.data)
    except Exception:
        raise V2Error(
            "provider_connection_failed",
            "Unable to connect to provider",
            502,
            {},
        ) from None
    message = (
        "Connection successful"
        if available
        else "Connection successful, but configured model was not found"
    )
    return True, latency_ms, available, message
