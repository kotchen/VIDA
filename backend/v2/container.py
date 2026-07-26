from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from fastapi import FastAPI

from .config import V2Settings
from .database import Database
from .errors import install_v2_error_contract
from .events import V2EventBroker
from .jobs.models import JobExecutor
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


logger = logging.getLogger(__name__)


ProviderTestResult = tuple[bool, int, bool, str]
ProviderTester = Callable[[ProviderRevisionCredentials], Awaitable[ProviderTestResult]]
ProviderModelOption = tuple[str, str]
ProviderModelFetchResult = tuple[list[ProviderModelOption], int]
ProviderModelFetcher = Callable[
    [str, str],
    Awaitable[ProviderModelFetchResult],
]


@dataclass
class V2Runtime:
    events: V2EventBroker = field(default_factory=V2EventBroker)
    settings: V2Settings | None = None
    database: Database | None = None
    job_repository: JobRepository | None = None
    job_executor: JobExecutor | None = None
    scheduler: Scheduler | None = None
    provider_profile_repository: ProviderProfileRepository | None = None
    provider_profile_service: ProviderProfileService | None = None
    episode_repository: EpisodeRepository | None = None
    chapter_repository: ChapterRepository | None = None
    episode_service: EpisodeService | None = None
    media_service: MediaService | None = None
    provider_tester: ProviderTester | None = None
    provider_model_fetcher: ProviderModelFetcher | None = None
    _readiness: Callable[[], Awaitable[None]] | None = field(
        default=None, repr=False
    )
    _initializer: Callable[[], "V2Runtime"] | None = field(default=None, repr=False)

    def initialize(self) -> None:
        if self.provider_profile_service is not None:
            return
        if self._initializer is None:
            raise RuntimeError("v2 runtime has no initializer")
        initialized = self._initializer()
        self.events = initialized.events
        self.settings = initialized.settings
        self.database = initialized.database
        self.job_repository = initialized.job_repository
        self.job_executor = initialized.job_executor
        self.scheduler = initialized.scheduler
        self.provider_profile_repository = initialized.provider_profile_repository
        self.provider_profile_service = initialized.provider_profile_service
        self.episode_repository = initialized.episode_repository
        self.chapter_repository = initialized.chapter_repository
        self.episode_service = initialized.episode_service
        self.media_service = initialized.media_service
        if self.provider_tester is None:
            self.provider_tester = initialized.provider_tester
        if self.provider_model_fetcher is None:
            self.provider_model_fetcher = initialized.provider_model_fetcher
        if self._readiness is None:
            self._readiness = initialized._readiness

    def require_provider_profiles(self) -> ProviderProfileService:
        if self.provider_profile_service is None:
            raise RuntimeError("v2 runtime has not started")
        return self.provider_profile_service

    def require_episodes(self) -> EpisodeService:
        if self.episode_service is None:
            raise RuntimeError("v2 runtime has not started")
        return self.episode_service

    async def start(self) -> None:
        self.initialize()
        if self._readiness is not None:
            await self._readiness()
        self.log_startup_settings()
        if self.scheduler is None:
            raise RuntimeError("v2 runtime has no scheduler")
        async def reconcile() -> None:
            if self.media_service is not None:
                await asyncio.to_thread(self.media_service.reconcile_orphans)

        await self.scheduler.start(reconcile if self.media_service is not None else None)

    def log_startup_settings(self) -> None:
        if self.settings is None:
            return
        logger.info(
            "VIDA v2 startup data_dir=%s workers=%d upload_limit_bytes=%d",
            self.settings.data_dir,
            self.settings.max_concurrent_jobs,
            self.settings.upload_max_bytes,
        )

    async def stop(self, grace_period_sec: float = 30.0) -> None:
        if self.scheduler is not None:
            await self.scheduler.stop(grace_period_sec)

    def install(self, app: FastAPI) -> None:
        from .router import create_v2_router

        install_v2_error_contract(app)
        app.include_router(create_v2_router(self), prefix="/api/v2")
        app.router.add_event_handler("startup", self.start)
        app.router.add_event_handler("shutdown", self.stop)
