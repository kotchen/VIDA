from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable

from fastapi import FastAPI

from .config import V2Settings
from .database import Database
from .errors import install_v2_error_contract
from .repositories.provider_profiles import ProviderProfileRepository
from .services.provider_profiles import (
    ProviderProfileService,
    ProviderRevisionCredentials,
)


ProviderTestResult = tuple[bool, int, bool, str]
ProviderTester = Callable[[ProviderRevisionCredentials], Awaitable[ProviderTestResult]]


@dataclass
class V2Runtime:
    settings: V2Settings | None = None
    database: Database | None = None
    provider_profile_repository: ProviderProfileRepository | None = None
    provider_profile_service: ProviderProfileService | None = None
    provider_tester: ProviderTester | None = None
    _initializer: Callable[[], "V2Runtime"] | None = field(default=None, repr=False)

    def initialize(self) -> None:
        if self.provider_profile_service is not None:
            return
        if self._initializer is None:
            raise RuntimeError("v2 runtime has no initializer")
        initialized = self._initializer()
        self.settings = initialized.settings
        self.database = initialized.database
        self.provider_profile_repository = initialized.provider_profile_repository
        self.provider_profile_service = initialized.provider_profile_service
        if self.provider_tester is None:
            self.provider_tester = initialized.provider_tester

    def require_provider_profiles(self) -> ProviderProfileService:
        if self.provider_profile_service is None:
            raise RuntimeError("v2 runtime has not started")
        return self.provider_profile_service

    def install(self, app: FastAPI) -> None:
        from .router import create_v2_router

        install_v2_error_contract(app)
        app.include_router(create_v2_router(self), prefix="/api/v2")
        if self._initializer is not None:
            app.router.add_event_handler("startup", self.initialize)
