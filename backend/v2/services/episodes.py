from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit
from uuid import uuid4

import aiofiles

from ..database import Database
from ..domain import EpisodeRecord, JobRecord, SummaryRecord, TranscriptSegmentRecord
from ..errors import V2Error
from ..repositories.episodes import EpisodeRepository
from ..repositories.jobs import JobRepository


UPLOAD_CHUNK_BYTES = 1024 * 1024
_ALLOWED_UPLOAD_EXTENSIONS = frozenset(
    {".txt", ".mp3", ".mp4", ".m4a", ".wav", ".webm", ".mkv", ".ogg", ".flac"}
)
_CANONICAL_CONTENT_TYPES = {
    ".txt": "text/plain",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}
_WINDOWS_RESERVED_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)


class StreamingUpload(Protocol):
    filename: str | None
    content_type: str | None

    async def read(self, size: int = -1) -> bytes: ...


@dataclass(frozen=True)
class EpisodeSubmission:
    episode: EpisodeRecord
    queue_position: int


class EpisodeService:
    def __init__(
        self,
        repository: EpisodeRepository,
        jobs: JobRepository | None = None,
        database: Database | None = None,
        data_dir: Path | None = None,
    ):
        self._repository = repository
        self._jobs = jobs
        self._database = database
        self._data_dir = None if data_dir is None else Path(data_dir)

    def get_episode(self, episode_id: str) -> EpisodeRecord:
        episode = self._repository.get(episode_id)
        if episode is None:
            raise _episode_not_found()
        return episode

    def list_projects(self, limit: int = 12, offset: int = 0) -> list[EpisodeRecord]:
        return self._repository.list_projects(limit, offset)

    def get_transcript(self, episode_id: str) -> list[TranscriptSegmentRecord]:
        self.get_episode(episode_id)
        return self._repository.get_transcript(episode_id)

    def get_summary(self, episode_id: str) -> SummaryRecord:
        self.get_episode(episode_id)
        summary = self._repository.get_summary(episode_id)
        if summary is None:
            raise V2Error("summary_not_found", "Episode summary not found", 404, {})
        return summary

    async def submit_upload(
        self,
        upload: StreamingUpload,
        provider_profile_id: str,
        summary_language: str,
        title: str | None,
        max_bytes: int,
    ) -> EpisodeSubmission:
        self._require_submission_dependencies()
        filename = _safe_upload_name(upload.filename)
        extension = Path(filename).suffix.lower()
        if extension not in _ALLOWED_UPLOAD_EXTENSIONS or not Path(filename).stem:
            raise V2Error(
                "unsupported_media_type", "Unsupported upload file type", 415, {}
            )
        episode_id = str(uuid4())
        relative_source = Path("episodes") / episode_id / "source" / filename
        final_path = self._resolve_storage_path(relative_source)
        part_path = final_path.with_name(final_path.name + ".part")
        final_path.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        renamed = False
        try:
            async with aiofiles.open(part_path, "xb") as output:
                while True:
                    chunk = await upload.read(UPLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise V2Error(
                            "file_too_large", "Upload exceeds configured limit", 413, {}
                        )
                    await output.write(chunk)
            if total == 0:
                raise V2Error("empty_file", "Uploaded file is empty", 400, {})
            os.replace(part_path, final_path)
            renamed = True
            return self._enqueue(
                episode_id=episode_id,
                title=title or Path(filename).stem,
                source_type="upload",
                source_path=relative_source.as_posix(),
                source_url=None,
                source_content_type=_CANONICAL_CONTENT_TYPES[extension],
                provider_profile_id=provider_profile_id,
                summary_language=summary_language,
            )
        except BaseException:
            part_path.unlink(missing_ok=True)
            if renamed:
                final_path.unlink(missing_ok=True)
            _remove_empty_parents(final_path.parent, self._data_dir)
            raise

    def submit_url(
        self,
        source_url: str,
        provider_profile_id: str,
        summary_language: str,
        title: str | None,
    ) -> EpisodeSubmission:
        self._require_submission_dependencies()
        parsed = urlsplit(source_url)
        fallback_title = parsed.hostname or "URL episode"
        return self._enqueue(
            episode_id=str(uuid4()),
            title=title or fallback_title,
            source_type="url",
            source_path=None,
            source_url=source_url,
            source_content_type=None,
            provider_profile_id=provider_profile_id,
            summary_language=summary_language,
        )

    def _enqueue(
        self,
        *,
        episode_id: str,
        title: str,
        source_type: str,
        source_path: str | None,
        source_url: str | None,
        source_content_type: str | None,
        provider_profile_id: str,
        summary_language: str,
    ) -> EpisodeSubmission:
        now = _utc_now()
        job_id = str(uuid4())
        episode = EpisodeRecord(
            id=episode_id,
            title=title,
            source_type=source_type,
            source_path=source_path,
            source_url=source_url,
            source_content_type=source_content_type,
            media_path=None,
            media_content_type=None,
            poster_path=None,
            poster_content_type=None,
            duration_sec=None,
            resolution=None,
            status="queued",
            language=None,
            summary_language=summary_language,
            progress=0,
            message="Queued",
            warnings_json="[]",
            error_code=None,
            error_message=None,
            current_job_id=None,
            provider_profile_id=provider_profile_id,
            created_at=now,
            updated_at=now,
            completed_at=None,
        )
        with self._database.transaction(immediate=True) as conn:
            profile = conn.execute(
                "SELECT active_revision_id FROM provider_profiles "
                "WHERE id=? AND deleted_at IS NULL AND active_revision_id IS NOT NULL",
                (provider_profile_id,),
            ).fetchone()
            if profile is None:
                raise V2Error(
                    "provider_profile_inactive",
                    "Provider profile is missing or inactive",
                    422,
                    {},
                )
            revision_id = profile["active_revision_id"]
            conn.execute(
                "INSERT INTO episodes "
                "(id,title,source_type,source_path,source_url,source_content_type,"
                "media_path,media_content_type,poster_path,poster_content_type,duration_sec,"
                "resolution,status,language,summary_language,progress,message,warnings_json,"
                "error_code,error_message,current_job_id,provider_profile_id,created_at,"
                "updated_at,completed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                tuple(getattr(episode, field) for field in _EPISODE_FIELDS),
            )
            job = JobRecord(
                id=job_id,
                episode_id=episode_id,
                type="process_episode",
                attempt=1,
                status="queued",
                provider_profile_revision_id=revision_id,
                submitted_at=now,
                started_at=None,
                finished_at=None,
                cancel_requested_at=None,
                heartbeat_at=None,
                worker_id=None,
                progress=0,
                message="Queued",
                error_code=None,
                error_message=None,
            )
            conn.execute(
                "INSERT INTO jobs "
                "(id,episode_id,type,attempt,status,provider_profile_revision_id,submitted_at,"
                "started_at,finished_at,cancel_requested_at,heartbeat_at,worker_id,progress,"
                "message,error_code,error_message) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                tuple(getattr(job, field) for field in _JOB_FIELDS),
            )
            conn.execute(
                "UPDATE episodes SET current_job_id=? WHERE id=?", (job_id, episode_id)
            )
            position = conn.execute(
                "SELECT COUNT(*) + 1 FROM jobs WHERE status='queued' AND "
                "(submitted_at < ? OR (submitted_at = ? AND id < ?))",
                (now, now, job_id),
            ).fetchone()[0]
        return EpisodeSubmission(replace(episode, current_job_id=job_id), position)

    def _resolve_storage_path(self, relative_path: Path) -> Path:
        base = self._data_dir.resolve()
        resolved = (base / relative_path).resolve()
        if base not in resolved.parents:
            raise V2Error("invalid_source", "Invalid upload path", 400, {})
        return resolved

    def _require_submission_dependencies(self) -> None:
        if self._database is None or self._jobs is None or self._data_dir is None:
            raise RuntimeError("episode submission service is not configured")


def _episode_not_found() -> V2Error:
    return V2Error("episode_not_found", "Episode not found", 404, {})


_EPISODE_FIELDS = (
    "id", "title", "source_type", "source_path", "source_url", "source_content_type",
    "media_path", "media_content_type", "poster_path", "poster_content_type",
    "duration_sec", "resolution", "status", "language", "summary_language", "progress",
    "message", "warnings_json", "error_code", "error_message", "current_job_id",
    "provider_profile_id", "created_at", "updated_at", "completed_at",
)
_JOB_FIELDS = (
    "id", "episode_id", "type", "attempt", "status", "provider_profile_revision_id",
    "submitted_at", "started_at", "finished_at", "cancel_requested_at", "heartbeat_at",
    "worker_id", "progress", "message", "error_code", "error_message",
)


def _safe_upload_name(raw_name: str | None) -> str:
    name = (raw_name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    name = re.sub(r"[<>:\"|?*\x00-\x1f\x7f]", "_", name).rstrip(". ")
    if not name or name in {".", ".."}:
        raise V2Error("invalid_source", "Upload filename is invalid", 400, {})
    path = Path(name)
    if path.stem.upper() in _WINDOWS_RESERVED_STEMS:
        name = "_" + name
    return name


def _remove_empty_parents(directory: Path, stop: Path | None) -> None:
    if stop is None:
        return
    stop = stop.resolve()
    current = directory
    while current != stop and stop in current.resolve().parents:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )
