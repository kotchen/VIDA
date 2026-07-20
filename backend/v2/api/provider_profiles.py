from __future__ import annotations

from fastapi import APIRouter, Response, status

from ..container import V2Runtime
from ..errors import V2Error
from ..schemas import (
    ProviderConnectionTestResponse,
    ProviderProfileCreate,
    ProviderProfileResponse,
    ProviderProfileUpdate,
)


def create_provider_profile_router(runtime: V2Runtime) -> APIRouter:
    router = APIRouter(prefix="/provider-profiles", tags=["provider-profiles"])

    @router.post(
        "",
        response_model=ProviderProfileResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_profile(payload: ProviderProfileCreate) -> ProviderProfileResponse:
        profile = runtime.require_provider_profiles().create_profile(
            payload.name,
            str(payload.base_url),
            payload.api_key.get_secret_value(),
            payload.model_id,
            payload.temperature,
        )
        return ProviderProfileResponse.from_profile(profile)

    @router.get("", response_model=list[ProviderProfileResponse])
    def list_profiles() -> list[ProviderProfileResponse]:
        return [
            ProviderProfileResponse.from_profile(profile)
            for profile in runtime.require_provider_profiles().list_profiles()
        ]

    @router.get("/{profile_id}", response_model=ProviderProfileResponse)
    def get_profile(profile_id: str) -> ProviderProfileResponse:
        profile = runtime.require_provider_profiles().get_profile(profile_id)
        if profile is None:
            raise _not_found()
        return ProviderProfileResponse.from_profile(profile)

    @router.patch("/{profile_id}", response_model=ProviderProfileResponse)
    def update_profile(
        profile_id: str, payload: ProviderProfileUpdate
    ) -> ProviderProfileResponse:
        values = payload.model_dump(exclude_unset=True)
        if "base_url" in values and values["base_url"] is not None:
            values["base_url"] = str(values["base_url"])
        if "api_key" in values and values["api_key"] is not None:
            values["api_key"] = values["api_key"].get_secret_value()
        try:
            profile = runtime.require_provider_profiles().update_profile(
                profile_id, **values
            )
        except KeyError:
            raise _not_found() from None
        return ProviderProfileResponse.from_profile(profile)

    @router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_profile(profile_id: str) -> Response:
        try:
            runtime.require_provider_profiles().delete_profile(profile_id)
        except KeyError:
            raise _not_found() from None
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post(
        "/{profile_id}/test",
        response_model=ProviderConnectionTestResponse,
    )
    async def test_profile(profile_id: str) -> ProviderConnectionTestResponse:
        service = runtime.require_provider_profiles()
        profile = service.get_profile(profile_id)
        if profile is None:
            raise _not_found()
        tester = runtime.provider_tester
        if tester is None:
            raise RuntimeError("provider tester is not configured")
        credentials = service.get_revision_credentials(profile.active_revision_id)
        try:
            ok, latency_ms, model_available, message = await tester(credentials)
        except V2Error:
            raise
        except Exception:
            raise V2Error(
                "provider_connection_failed",
                "Unable to connect to provider",
                502,
                {},
            ) from None
        return ProviderConnectionTestResponse(
            ok=ok,
            latency_ms=latency_ms,
            model_available=model_available,
            message=message,
        )

    return router


def _not_found() -> V2Error:
    return V2Error(
        "provider_profile_not_found",
        "Provider profile not found",
        404,
        {},
    )
