from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path
from collections.abc import AsyncIterable

from fastapi import APIRouter, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from ..container import V2Runtime
from ..errors import V2Error
from ..schemas import (
    ChapterResponse,
    ChapterCreate,
    ChapterUpdate,
    DashboardResponse,
    EpisodeResponse,
    EpisodeSubmissionResponse,
    EpisodeUrlSubmission,
    JobResponse,
    ProjectResponse,
    SummaryResponse,
    TranscriptSegmentResponse,
)
from ..services.episodes import EpisodeSubmission
from ..services.dashboard import DashboardService
from ..services.exports import ExportService
from ..services.media import UnsafeMediaPath


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
        service = runtime.require_episodes()
        episode = service.get_episode(episode_id)
        return _chapter_responses(episode, service.get_chapters(episode_id))

    @router.post(
        "/{episode_id}/chapters", response_model=ChapterResponse, status_code=201
    )
    def create_chapter(episode_id: str, payload: ChapterCreate):
        service = runtime.require_episodes()
        created = service.create_chapter(episode_id, payload.start_sec, payload.title)
        episode = service.get_episode(episode_id)
        rows = service.get_chapters(episode_id)
        return next(row for row in _chapter_responses(episode, rows) if row.id == created.id)

    @router.patch("/{episode_id}/chapters/{chapter_id}", response_model=ChapterResponse)
    def update_chapter(episode_id: str, chapter_id: str, payload: ChapterUpdate):
        service = runtime.require_episodes()
        updated = service.update_chapter(
            episode_id, chapter_id, start_sec=payload.start_sec, title=payload.title
        )
        episode = service.get_episode(episode_id)
        rows = service.get_chapters(episode_id)
        return next(row for row in _chapter_responses(episode, rows) if row.id == updated.id)

    @router.delete("/{episode_id}/chapters/{chapter_id}", status_code=204)
    def delete_chapter(episode_id: str, chapter_id: str):
        runtime.require_episodes().delete_chapter(episode_id, chapter_id)
        return Response(status_code=204)

    @router.get("/{episode_id}/export")
    def export_episode(episode_id: str, format: str):
        service = runtime.require_episodes()
        episode = service.get_episode(episode_id)
        if runtime.episode_repository is None or runtime.chapter_repository is None:
            raise RuntimeError("export repositories are unavailable")
        document = ExportService(
            runtime.episode_repository, runtime.chapter_repository
        ).render(episode, format)
        return Response(
            document.content,
            media_type=None,
            headers={
                "Content-Type": document.content_type,
                "Content-Disposition": document.content_disposition,
            },
        )

    @router.api_route("/{episode_id}/media", methods=["GET", "HEAD"])
    async def get_media(episode_id: str, request: Request):
        return await _deliver_file(runtime, episode_id, request, "media")

    @router.api_route("/{episode_id}/poster", methods=["GET", "HEAD"])
    async def get_poster(episode_id: str, request: Request):
        return await _deliver_file(runtime, episode_id, request, "poster")

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


def create_dashboard_router(runtime: V2Runtime) -> APIRouter:
    router = APIRouter(tags=["dashboard"])

    @router.get("/dashboard", response_model=DashboardResponse)
    def dashboard():
        if runtime.episode_repository is None or runtime.chapter_repository is None:
            raise RuntimeError("dashboard repositories are unavailable")
        data = DashboardService(
            runtime.episode_repository, runtime.chapter_repository
        ).get()
        if data.current_episode is None:
            return DashboardResponse(
                current_episode=None, summary=None, transcript=[], chapters=[],
                recent_projects=[_project_response(row) for row in data.recent_projects],
            )
        service = runtime.require_episodes()
        return DashboardResponse(
            current_episode=_episode_response(
                data.current_episode,
                service.queue_position(data.current_episode.current_job_id)
                if data.current_episode.current_job_id else None,
            ),
            summary=(
                None if data.summary is None
                else SummaryResponse.model_validate(data.summary, from_attributes=True)
            ),
            transcript=[_transcript_response(row) for row in data.transcript],
            chapters=_chapter_responses(data.current_episode, data.chapters),
            recent_projects=[_project_response(row) for row in data.recent_projects],
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
        finished_at=job.finished_at,
        progress=job.progress, message=job.message, queue_position=queue_position,
        error_code=job.error_code, error_message=job.error_message,
    )


def _regenerate(runtime: V2Runtime, episode_id: str, job_type: str) -> JobResponse:
    service = runtime.require_episodes()
    job = service.regenerate(episode_id, job_type)
    runtime.scheduler.notify()
    return _job_response(job, service.queue_position(job.id))


def _transcript_response(row) -> TranscriptSegmentResponse:
    return TranscriptSegmentResponse(
        id=row.id, start_sec=row.start_sec, end_sec=row.end_sec,
        speaker=row.speaker or "Speaker 1", text=row.text,
    )


def _chapter_responses(episode, rows) -> list[ChapterResponse]:
    ordered = sorted(rows, key=lambda row: (row.start_sec, row.id))
    result: list[ChapterResponse] = []
    episode_end = episode.duration_sec or 0
    for index, row in enumerate(ordered):
        next_start = (
            ordered[index + 1].start_sec if index + 1 < len(ordered) else episode_end
        )
        duration = max(0, next_start - row.start_sec)
        result.append(ChapterResponse(
            id=row.id, start_sec=row.start_sec, title=row.title,
            duration_sec=duration,
            thumbnail_url=(
                None if episode.poster_path is None
                else f"/api/v2/episodes/{episode.id}/poster"
            ),
            bookmarked=row.bookmarked,
        ))
    return result


def _project_response(episode) -> ProjectResponse:
    return ProjectResponse(
        id=episode.id,
        title=episode.title,
        created_at=episode.created_at,
        duration_sec=episode.duration_sec or 0,
        status=episode.status,
        thumbnail_url=(
            None if episode.poster_path is None
            else f"/api/v2/episodes/{episode.id}/poster"
        ),
    )


async def _deliver_file(
    runtime: V2Runtime, episode_id: str, request: Request, kind: str
):
    episode = runtime.require_episodes().get_episode(episode_id)
    if runtime.media_service is None:
        raise RuntimeError("media service is unavailable")
    stored_path = episode.media_path if kind == "media" else episode.poster_path
    content_type = (
        episode.media_content_type if kind == "media" else episode.poster_content_type
    ) or "application/octet-stream"
    try:
        opened = runtime.media_service.open_owned_file(episode_id, stored_path, kind)
    except UnsafeMediaPath:
        raise V2Error("media_not_found", "Media file not found", 404, {}) from None

    etag = f'"{opened.mtime_ns:x}-{opened.size:x}"'
    last_modified = formatdate(opened.mtime, usegmt=True)
    common_headers = {
        "Accept-Ranges": "bytes",
        "ETag": etag,
        "Last-Modified": last_modified,
    }
    try:
        if _precondition_failed(request, etag, opened.mtime):
            opened.close()
            raise V2Error(
                "precondition_failed", "Request precondition failed", 412, {},
                common_headers,
            )
        if _not_modified(request, etag, opened.mtime):
            opened.close()
            return Response(status_code=304, headers=common_headers)

        start, end = 0, max(0, opened.size - 1)
        partial = False
        range_header = request.headers.get("range") if request.method == "GET" else None
        if range_header and _if_range_allows(request.headers.get("if-range"), etag, opened.mtime):
            try:
                start, end = _parse_single_range(range_header, opened.size)
            except ValueError:
                opened.close()
                raise V2Error(
                    "range_not_satisfiable", "Requested byte range is not satisfiable",
                    416, {}, {"Content-Range": f"bytes */{opened.size}", "Accept-Ranges": "bytes"},
                ) from None
            partial = True

        length = 0 if opened.size == 0 else end - start + 1
        headers = dict(common_headers)
        headers["Content-Length"] = str(length)
        headers["Content-Type"] = content_type
        if partial:
            headers["Content-Range"] = f"bytes {start}-{end}/{opened.size}"
        if request.method == "HEAD":
            opened.close()
            return Response(status_code=200, headers=headers)

        async def chunks():
            remaining = length
            try:
                await asyncio.to_thread(opened.file.seek, start)
                while remaining:
                    if await request.is_disconnected():
                        break
                    chunk = await asyncio.to_thread(
                        opened.file.read, min(64 * 1024, remaining)
                    )
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk
            finally:
                opened.close()

        return StreamingResponse(
            chunks(), status_code=206 if partial else 200, headers=headers
        )
    except BaseException:
        if not opened.file.closed:
            opened.close()
        raise


def _parse_single_range(value: str, size: int) -> tuple[int, int]:
    if size <= 0 or not value.startswith("bytes=") or "," in value:
        raise ValueError("invalid range")
    spec = value[6:].strip()
    if not spec or "-" not in spec:
        raise ValueError("invalid range")
    first, last = (part.strip() for part in spec.split("-", 1))
    if not first:
        if not last.isdecimal() or int(last) <= 0:
            raise ValueError("invalid suffix range")
        suffix = int(last)
        return max(0, size - suffix), size - 1
    if not first.isdecimal() or (last and not last.isdecimal()):
        raise ValueError("invalid range")
    start = int(first)
    if start >= size:
        raise ValueError("unsatisfiable range")
    end = size - 1 if not last else min(int(last), size - 1)
    if end < start:
        raise ValueError("invalid range")
    return start, end


def _not_modified(request: Request, etag: str, mtime: float) -> bool:
    if_none_match = request.headers.get("if-none-match")
    if if_none_match is not None:
        return any(
            token.strip() == "*"
            or token.strip().removeprefix("W/") == etag
            for token in if_none_match.split(",")
        )
    modified_since = request.headers.get("if-modified-since")
    if not modified_since:
        return False
    parsed = _http_date(modified_since)
    return parsed is not None and int(mtime) <= int(parsed.timestamp())


def _precondition_failed(request: Request, etag: str, mtime: float) -> bool:
    if_match = request.headers.get("if-match")
    if if_match is not None:
        return not any(
            token.strip() == "*" or token.strip() == etag
            for token in if_match.split(",")
        )
    unmodified_since = request.headers.get("if-unmodified-since")
    if not unmodified_since:
        return False
    parsed = _http_date(unmodified_since)
    return parsed is not None and int(mtime) > int(parsed.timestamp())


def _if_range_allows(value: str | None, etag: str, mtime: float) -> bool:
    if not value:
        return True
    value = value.strip()
    if value.startswith('"') or value.startswith("W/"):
        return value == etag
    parsed = _http_date(value)
    return parsed is not None and int(mtime) <= int(parsed.timestamp())


def _http_date(value: str) -> datetime | None:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
