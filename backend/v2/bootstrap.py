from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI
from openai import OpenAI

if __package__ == "v2":
    from transcriber import Transcriber
else:
    from ..transcriber import Transcriber

from .config import V2Settings
from .container import ProviderModelFetchResult, ProviderTestResult, V2Runtime
from .crypto import CredentialCipher
from .database import Database
from .errors import V2Error
from .events import V2EventBroker
from .jobs.models import JobExecutor
from .jobs.ai import AIProcessor
from .jobs.pipeline import EpisodePipeline
from .jobs.source_ingest import (
    AsyncioProcessRunner,
    SecureDownloader,
    SourceIngestor,
    YtDlpDownloader,
)
from .jobs.scheduler import Scheduler
from .repositories.episodes import EpisodeRepository
from .repositories.chapters import ChapterRepository
from .repositories.jobs import JobRepository
from .repositories.provider_profiles import ProviderProfileRepository
from .services.episodes import EpisodeService
from .services.media import MediaService
from .services.provider_profiles import (
    ProviderProfileService,
    ProviderRevisionCredentials,
)


def install_v2(app: FastAPI, project_root: Path) -> V2Runtime:
    root = Path(project_root)
    events = V2EventBroker()
    runtime = V2Runtime(
        events=events,
        _initializer=lambda: _build_environment_runtime(root, events),
    )
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


def _build_environment_runtime(
    project_root: Path, events: V2EventBroker | None = None
) -> V2Runtime:
    return _build_runtime(V2Settings.from_environ(os.environ, project_root), events=events)


def _build_runtime(
    settings: V2Settings,
    executor: JobExecutor | None = None,
    events: V2EventBroker | None = None,
) -> V2Runtime:
    event_broker = events or V2EventBroker()
    database = Database(settings.database_path)
    database.initialize()
    repository = ProviderProfileRepository(database)
    service = ProviderProfileService(
        repository, CredentialCipher(settings.master_key), event_broker.publish
    )
    job_repository = JobRepository(database, event_broker.publish)
    episode_repository = EpisodeRepository(database)
    chapter_repository = ChapterRepository(database)
    media_service = MediaService(settings.data_dir, [episode_repository])
    episode_service = EpisodeService(
        episode_repository,
        job_repository,
        database,
        settings.data_dir,
        chapter_repository,
        event_broker.publish,
        media_service,
    )
    if executor is None:
        _disable_yt_dlp_plugins()
        process_runner = AsyncioProcessRunner()
        secure_downloader = SecureDownloader(max_bytes=settings.upload_max_bytes)
        page_downloader = YtDlpDownloader(
            secure_downloader,
            max_bytes=settings.upload_max_bytes,
        )
        source_ingestor = SourceIngestor(
            process_runner,
            _RoutingDownloader(secure_downloader, page_downloader),
            data_dir=settings.data_dir,
            audio_poster=Path(__file__).resolve().parents[2] / "static" / "sipsip.png",
        )
        job_executor = EpisodePipeline(
            data_dir=settings.data_dir,
            profiles=service,
            episodes=episode_repository,
            chapters=chapter_repository,
            jobs=job_repository,
            media=media_service,
            source=source_ingestor,
            transcriber=Transcriber(),
            ai_factory=AIProcessor,
            now=_utc_now,
        )
    else:
        job_executor = executor
    scheduler = Scheduler(
        job_repository,
        job_executor,
        max_workers=settings.max_concurrent_jobs,
        poll_interval_sec=1.0,
    )
    return V2Runtime(
        events=event_broker,
        settings=settings,
        database=database,
        job_repository=job_repository,
        job_executor=job_executor,
        scheduler=scheduler,
        provider_profile_repository=repository,
        provider_profile_service=service,
        episode_repository=episode_repository,
        chapter_repository=chapter_repository,
        episode_service=episode_service,
        media_service=media_service,
        provider_tester=test_provider_connection,
        provider_model_fetcher=fetch_provider_models,
    )


def _disable_yt_dlp_plugins() -> None:
    from yt_dlp.globals import plugin_dirs

    plugin_dirs.value = []


class _RoutingDownloader:
    def __init__(self, direct, page):
        self._direct = direct
        self._page = page

    async def download(self, url, directory, cancel_check, progress=None):
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
        platform = any(
            host == allowed or host.endswith("." + allowed)
            for allowed in YtDlpDownloader._PLATFORM_HOSTS
        )
        downloader = self._page if platform else self._direct
        return await downloader.download(url, directory, cancel_check, progress)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


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


async def fetch_provider_models(
    base_url: str,
    api_key: str,
) -> ProviderModelFetchResult:
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=15.0)
    try:
        started = time.monotonic()
        response = await asyncio.to_thread(client.models.list)
        latency_ms = max(0, round((time.monotonic() - started) * 1000))
        models = _normalize_provider_models(response.data)
    finally:
        await asyncio.to_thread(client.close)
    return models, latency_ms


def _normalize_provider_models(rows) -> list[tuple[str, str]]:
    unique: dict[str, tuple[str, str]] = {}
    for row in rows:
        model_id = getattr(row, "id", None)
        if not isinstance(model_id, str):
            continue
        model_id = model_id.strip()
        if not model_id or len(model_id) > 512 or model_id in unique:
            continue
        raw_name = getattr(row, "name", None)
        name = raw_name.strip() if isinstance(raw_name, str) else ""
        if not name:
            name = model_id
        if len(name) > 512:
            continue
        unique[model_id] = (model_id, name)
        if len(unique) == 2000:
            break
    return sorted(unique.values(), key=lambda item: (item[0].casefold(), item[0]))
