from __future__ import annotations

import json

from fastapi import APIRouter, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from ..container import V2Runtime
from ..errors import V2Error
from ..schemas import (
    EpisodeSubmissionResponse,
    EpisodeUploadSubmission,
    EpisodeUrlSubmission,
)
from ..services.episodes import EpisodeSubmission


def create_episode_router(runtime: V2Runtime) -> APIRouter:
    router = APIRouter(prefix="/episodes", tags=["episodes"])

    @router.post(
        "",
        response_model=EpisodeSubmissionResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def submit_episode(request: Request):
        media_type = (
            request.headers.get("content-type", "")
            .split(";", 1)[0]
            .strip()
            .lower()
        )
        service = runtime.require_episodes()
        if media_type == "multipart/form-data":
            async with request.form() as form:
                file = form.get("file")
                if not isinstance(file, UploadFile):
                    raise _validation_error("file", "Upload file is required")
                metadata = _validate(
                    EpisodeUploadSubmission,
                    {
                        "providerProfileId": form.get("providerProfileId"),
                        "summaryLanguage": form.get("summaryLanguage"),
                        "title": form.get("title"),
                    },
                )
                submission = await service.submit_upload(
                    file,
                    metadata.provider_profile_id,
                    metadata.summary_language,
                    metadata.title,
                    runtime.settings.upload_max_bytes,
                )
        elif media_type == "application/json":
            try:
                raw_payload = await request.json()
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise _validation_error("body", "Request body must be valid JSON") from None
            payload = _validate(EpisodeUrlSubmission, raw_payload)
            submission = service.submit_url(
                str(payload.source_url),
                payload.provider_profile_id,
                payload.summary_language,
                payload.title,
            )
        else:
            raise V2Error(
                "unsupported_media_type",
                "Content-Type must be multipart/form-data or application/json",
                415,
                {},
            )
        runtime.scheduler.notify()
        response = _response(submission)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=response.model_dump(by_alias=True),
            headers={"Location": f"/api/v2/episodes/{submission.episode.id}"},
        )

    return router


def _validate(model, value):
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from None


def _validation_error(field: str, message: str) -> RequestValidationError:
    return RequestValidationError(
        [{"type": "value_error", "loc": ("body", field), "msg": message, "input": None}]
    )


def _response(submission: EpisodeSubmission) -> EpisodeSubmissionResponse:
    episode = submission.episode
    return EpisodeSubmissionResponse(
        id=episode.id,
        title=episode.title,
        source_type=episode.source_type,
        media_url=None,
        poster_url=None,
        duration_sec=episode.duration_sec or 0,
        resolution=episode.resolution,
        status=episode.status,
        language=episode.language or episode.summary_language,
        created_at=episode.created_at,
        progress=episode.progress,
        message=episode.message,
        queue_position=submission.queue_position,
        provider_profile_id=episode.provider_profile_id,
        warnings=[],
    )
