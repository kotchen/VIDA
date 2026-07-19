from fastapi import APIRouter

from .api.episodes import create_episode_router
from .api.provider_profiles import create_provider_profile_router
from .container import V2Runtime


def create_v2_router(runtime: V2Runtime) -> APIRouter:
    router = APIRouter()
    router.include_router(create_provider_profile_router(runtime))
    router.include_router(create_episode_router(runtime))
    return router
