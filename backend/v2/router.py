from fastapi import APIRouter

from .api.provider_profiles import create_provider_profile_router
from .container import V2Runtime


def create_v2_router(runtime: V2Runtime) -> APIRouter:
    router = APIRouter()
    router.include_router(create_provider_profile_router(runtime))
    return router
