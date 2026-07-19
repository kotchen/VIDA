from __future__ import annotations

import json
from collections.abc import AsyncIterable

from fastapi import APIRouter, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ..container import V2Runtime
from ..errors import V2Error
from ..schemas import (
    ChapterResponse,
    EpisodeResponse,
    EpisodeSubmissionResponse,
    EpisodeUrlSubmission,
    JobResponse,
    SummaryResponse,
    TranscriptSegmentResponse,
)
from ..services.episodes import EpisodeSubmission


JSON_BODY_MAX_BYTES = 16 * 1024


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
            submission = await service.submit_multipart(
                request.stream(),
                request.headers.get("content-type", ""),
                _content_length(request),
                runtime.settings.upload_max_bytes,
            )
        elif media_type == "application/json":
            raw_payload = await _bounded_json_body(
                request.stream(), _content_length(request)
            )
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

    @router.get("/{episode_id}", response_model=EpisodeResponse)
    def get_episode(episode_id: str):
        service = runtime.require_episodes()
        episode = service.get_episode(episode_id)
        return _episode_response(episode, service.queue_position(episode.current_job_id))

    @router.get("/{episode_id}/transcript", response_model=list[TranscriptSegmentResponse])
    def get_transcript(episode_id: str):
        return [
            TranscriptSegmentResponse(
                id=row.id, start_sec=row.start_sec, end_sec=row.end_sec,
                speaker=row.speaker or "Speaker 1", text=row.text,
            )
            for row in runtime.require_episodes().get_transcript(episode_id)
        ]

    @router.get("/{episode_id}/summary", response_model=SummaryResponse)
    def get_summary(episode_id: str):
        return SummaryResponse.model_validate(
            runtime.require_episodes().get_summary(episode_id), from_attributes=True
        )

    @router.get("/{episode_id}/chapters", response_model=list[ChapterResponse])
    def get_chapters(episode_id: str):
        return [
            ChapterResponse(
                id=row.id, start_sec=row.start_sec, title=row.title,
                duration_sec=row.duration_sec or 0,
                thumbnail_url=(
                    None if row.thumbnail_path is None
                    else f"/api/v2/episodes/{episode_id}/poster"
                ),
                bookmarked=row.bookmarked,
            )
            for row in runtime.require_episodes().get_chapters(episode_id)
        ]

    @router.post("/{episode_id}/cancel", response_model=JobResponse)
    def cancel_episode(episode_id: str):
        service = runtime.require_episodes()
        job = service.cancel_episode(episode_id)
        return JSONResponse(
            status_code=200 if job.status == "canceled" else 202,
            content=_job_response(job, service.queue_position(job.id)).model_dump(by_alias=True),
        )

    @router.post("/{episode_id}/retry", response_model=JobResponse, status_code=202)
    def retry_episode(episode_id: str):
        service = runtime.require_episodes()
        job = service.retry(episode_id)
        runtime.scheduler.notify()
        return _job_response(job, service.queue_position(job.id))

    @router.post(
        "/{episode_id}/summary/regenerate", response_model=JobResponse, status_code=202
    )
    def regenerate_summary(episode_id: str):
        return _regenerate(runtime, episode_id, "regenerate_summary")

    @router.post(
        "/{episode_id}/chapters/regenerate", response_model=JobResponse, status_code=202
    )
    def regenerate_chapters(episode_id: str):
        return _regenerate(runtime, episode_id, "regenerate_chapters")

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


def _content_length(request: Request) -> int | None:
    value = request.headers.get("content-length")
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        raise V2Error("invalid_source", "Invalid Content-Length", 400, {}) from None
    if parsed < 0:
        raise V2Error("invalid_source", "Invalid Content-Length", 400, {})
    return parsed


async def _bounded_json_body(
    stream: AsyncIterable[bytes], content_length: int | None
):
    if content_length is not None and content_length > JSON_BODY_MAX_BYTES:
        raise V2Error("file_too_large", "JSON request body is too large", 413, {})
    body = bytearray()
    async for chunk in stream:
        if len(body) + len(chunk) > JSON_BODY_MAX_BYTES:
            raise V2Error("file_too_large", "JSON request body is too large", 413, {})
        body.extend(chunk)
    try:
        return json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise _validation_error("body", "Request body must be valid JSON") from None


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


def _episode_response(episode, queue_position: int | None) -> EpisodeResponse:
    return EpisodeResponse(
        id=episode.id, title=episode.title, source_type=episode.source_type,
        media_url=(None if episode.media_path is None else f"/api/v2/episodes/{episode.id}/media"),
        poster_url=(None if episode.poster_path is None else f"/api/v2/episodes/{episode.id}/poster"),
        duration_sec=episode.duration_sec or 0, resolution=episode.resolution,
        status=episode.status, language=episode.language or episode.summary_language,
        created_at=episode.created_at, progress=episode.progress, message=episode.message,
        queue_position=queue_position, provider_profile_id=episode.provider_profile_id,
        warnings=episode.warnings,
    )


def _job_response(job, queue_position: int | None) -> JobResponse:
    return JobResponse(
        id=job.id, episode_id=job.episode_id, type=job.type, attempt=job.attempt,
        status=job.status,
        provider_profile_revision_id=job.provider_profile_revision_id,
        submitted_at=job.submitted_at, started_at=job.started_at,
        finished_at=job.finished_at, cancel_requested_at=job.cancel_requested_at,
        progress=job.progress, message=job.message, queue_position=queue_position,
        error_code=job.error_code, error_message=job.error_message,
    )


def _regenerate(runtime: V2Runtime, episode_id: str, job_type: str) -> JobResponse:
    service = runtime.require_episodes()
    job = service.regenerate(episode_id, job_type)
    runtime.scheduler.notify()
    return _job_response(job, service.queue_position(job.id))
