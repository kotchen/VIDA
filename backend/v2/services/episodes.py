from __future__ import annotations

import os
import re
from collections.abc import AsyncIterable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit
from uuid import uuid4

import aiofiles
from python_multipart import MultipartParser
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import parse_options_header

from ..database import Database
from ..domain import EpisodeRecord, JobRecord, SummaryRecord, TranscriptSegmentRecord
from ..errors import V2Error
from ..repositories.episodes import EpisodeRepository
from ..repositories.jobs import JobRepository


UPLOAD_CHUNK_BYTES = 1024 * 1024
MAX_UPLOAD_FILENAME_BYTES = 240
MAX_MULTIPART_OVERHEAD_BYTES = 64 * 1024
_ALLOWED_UPLOAD_EXTENSIONS = frozenset(
    {".mp3", ".mp4", ".m4a", ".wav", ".webm", ".mkv", ".ogg", ".flac"}
)
_CANONICAL_CONTENT_TYPES = {
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


@dataclass(frozen=True)
class _ParsedMultipartUpload:
    episode_id: str
    filename: str
    relative_source: Path
    final_path: Path
    part_path: Path
    source_content_type: str
    fields: dict[str, str]


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

    async def submit_multipart(
        self,
        stream: AsyncIterable[bytes],
        content_type: str,
        content_length: int | None,
        max_bytes: int,
    ) -> EpisodeSubmission:
        self._require_submission_dependencies()
        request_limit = max_bytes + MAX_MULTIPART_OVERHEAD_BYTES
        if content_length is not None and content_length > request_limit:
            raise V2Error("file_too_large", "Multipart request is too large", 413, {})
        writer = _MultipartUploadWriter(self, max_bytes)
        parser = _multipart_parser(content_type, writer)
        received = 0
        renamed = False
        committed = False
        try:
            async for chunk in stream:
                received += len(chunk)
                if received > request_limit:
                    raise V2Error(
                        "file_too_large", "Multipart request is too large", 413, {}
                    )
                parser.write(chunk)
            parser.finalize()
            parsed = writer.finish()
            metadata = _validated_multipart_fields(parsed.fields)
            os.replace(parsed.part_path, parsed.final_path)
            renamed = True
            submission = self._enqueue(
                episode_id=parsed.episode_id,
                title=metadata["title"] or Path(parsed.filename).stem,
                source_type="upload",
                source_path=parsed.relative_source.as_posix(),
                source_url=None,
                source_content_type=parsed.source_content_type,
                provider_profile_id=metadata["providerProfileId"],
                summary_language=metadata["summaryLanguage"],
            )
            committed = True
            return submission
        except V2Error:
            raise
        except MultipartParseError:
            raise V2Error("invalid_source", "Malformed multipart request", 400, {}) from None
        finally:
            writer.close(remove_final=renamed and not committed)

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
    if len(name.encode("utf-8")) > MAX_UPLOAD_FILENAME_BYTES:
        raise V2Error("invalid_source", "Upload filename is too long", 400, {})
    return name


class _MultipartUploadWriter:
    _FIELD_BYTE_LIMITS = {
        "providerProfileId": 128,
        "summaryLanguage": 32,
        "title": 800,
    }
    _ALLOWED_PARTS = frozenset({"file", *_FIELD_BYTE_LIMITS})

    def __init__(self, service: EpisodeService, max_bytes: int):
        self._service = service
        self._max_bytes = max_bytes
        self._episode_id = str(uuid4())
        self._part_count = 0
        self._seen_parts: set[str] = set()
        self._fields: dict[str, str] = {}
        self._file_count = 0
        self._file_size = 0
        self._file_complete = False
        self._ended = False
        self._filename: str | None = None
        self._relative_source: Path | None = None
        self._final_path: Path | None = None
        self._part_path: Path | None = None
        self._source_content_type: str | None = None
        self._file = None
        self._current_header_name = bytearray()
        self._current_header_value = bytearray()
        self._content_disposition = b""
        self._current_name: str | None = None
        self._current_is_file = False
        self._current_data = bytearray()

    def on_part_begin(self) -> None:
        self._part_count += 1
        if self._part_count > 4:
            raise V2Error("invalid_source", "Too many multipart parts", 400, {})
        self._content_disposition = b""
        self._current_name = None
        self._current_is_file = False
        self._current_data = bytearray()

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._current_header_name.extend(data[start:end])

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._current_header_value.extend(data[start:end])

    def on_header_end(self) -> None:
        if bytes(self._current_header_name).lower() == b"content-disposition":
            self._content_disposition = bytes(self._current_header_value)
        self._current_header_name.clear()
        self._current_header_value.clear()

    def on_headers_finished(self) -> None:
        disposition, options = parse_options_header(self._content_disposition)
        if disposition != b"form-data" or b"name" not in options:
            raise V2Error("invalid_source", "Invalid multipart disposition", 400, {})
        name = _decode_multipart_option(options[b"name"])
        if name not in self._ALLOWED_PARTS or name in self._seen_parts:
            raise V2Error("invalid_source", "Unexpected multipart part", 400, {})
        self._seen_parts.add(name)
        self._current_name = name
        filename_option = options.get(b"filename")
        if filename_option is None:
            if name == "file":
                raise V2Error("invalid_source", "Upload file is required", 400, {})
            return
        if name != "file":
            raise V2Error("invalid_source", "Unexpected file part", 400, {})
        self._file_count += 1
        if self._file_count != 1:
            raise V2Error("invalid_source", "Exactly one file is required", 400, {})
        self._current_is_file = True
        filename = _safe_upload_name(_decode_multipart_option(filename_option))
        extension = Path(filename).suffix.lower()
        if extension not in _ALLOWED_UPLOAD_EXTENSIONS or not Path(filename).stem:
            raise V2Error(
                "unsupported_media_type", "Unsupported upload file type", 415, {}
            )
        relative_source = Path("episodes") / self._episode_id / "source" / filename
        if len(relative_source.as_posix().encode("utf-8")) > 512:
            raise V2Error("invalid_source", "Upload path is too long", 400, {})
        final_path = self._service._resolve_storage_path(relative_source)
        part_path = final_path.with_name(final_path.name + ".part")
        final_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = part_path.open("xb")
        self._filename = filename
        self._relative_source = relative_source
        self._final_path = final_path
        self._part_path = part_path
        self._source_content_type = _CANONICAL_CONTENT_TYPES[extension]

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        value = data[start:end]
        if self._current_is_file:
            self._file_size += len(value)
            if self._file_size > self._max_bytes:
                raise V2Error(
                    "file_too_large", "Upload exceeds configured limit", 413, {}
                )
            self._file.write(value)
            return
        if self._current_name is None:
            raise V2Error("invalid_source", "Malformed multipart part", 400, {})
        limit = self._FIELD_BYTE_LIMITS[self._current_name]
        if len(self._current_data) + len(value) > limit:
            raise _multipart_validation_error("Multipart field is too long")
        self._current_data.extend(value)

    def on_part_end(self) -> None:
        if self._current_is_file:
            self._close_file()
            self._file_complete = True
            return
        if self._current_name is None:
            raise V2Error("invalid_source", "Malformed multipart part", 400, {})
        try:
            value = self._current_data.decode("utf-8")
        except UnicodeDecodeError:
            raise _multipart_validation_error("Multipart fields must be UTF-8") from None
        self._fields[self._current_name] = value

    def on_end(self) -> None:
        self._ended = True

    def finish(self) -> _ParsedMultipartUpload:
        self._close_file()
        if not self._ended or self._file_count != 1 or not self._file_complete:
            raise V2Error("invalid_source", "Incomplete multipart upload", 400, {})
        if self._file_size == 0:
            raise V2Error("empty_file", "Uploaded file is empty", 400, {})
        return _ParsedMultipartUpload(
            self._episode_id,
            self._filename,
            self._relative_source,
            self._final_path,
            self._part_path,
            self._source_content_type,
            dict(self._fields),
        )

    def close(self, *, remove_final: bool) -> None:
        self._close_file()
        if self._part_path is not None:
            self._part_path.unlink(missing_ok=True)
        if remove_final and self._final_path is not None:
            self._final_path.unlink(missing_ok=True)
        if self._final_path is not None:
            _remove_empty_parents(self._final_path.parent, self._service._data_dir)

    def _close_file(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


def _multipart_parser(content_type: str, writer: _MultipartUploadWriter) -> MultipartParser:
    media_type, options = parse_options_header(content_type)
    boundary = options.get(b"boundary")
    if media_type != b"multipart/form-data" or not boundary:
        raise V2Error("invalid_source", "Multipart boundary is required", 400, {})
    if not 1 <= len(boundary) <= 70:
        raise V2Error("invalid_source", "Multipart boundary is invalid", 400, {})
    callbacks = {
        "on_part_begin": writer.on_part_begin,
        "on_part_data": writer.on_part_data,
        "on_part_end": writer.on_part_end,
        "on_header_field": writer.on_header_field,
        "on_header_value": writer.on_header_value,
        "on_header_end": writer.on_header_end,
        "on_headers_finished": writer.on_headers_finished,
        "on_end": writer.on_end,
    }
    return MultipartParser(
        boundary,
        callbacks,
        max_header_count=8,
        max_header_size=1024,
    )


def _decode_multipart_option(value: bytes | str) -> str:
    if isinstance(value, str):
        return value
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        raise V2Error("invalid_source", "Invalid multipart header encoding", 400, {}) from None


def _validated_multipart_fields(fields: dict[str, str]) -> dict[str, str]:
    if not {"providerProfileId", "summaryLanguage"}.issubset(fields):
        raise _multipart_validation_error("Required multipart fields are missing")
    provider_profile_id = fields["providerProfileId"].strip()
    summary_language = fields["summaryLanguage"].strip()
    title = fields.get("title", "").strip()
    if not provider_profile_id or len(provider_profile_id) > 128:
        raise _multipart_validation_error("providerProfileId is invalid")
    if not summary_language or len(summary_language) > 32:
        raise _multipart_validation_error("summaryLanguage is invalid")
    if "title" in fields and (not title or len(title) > 200):
        raise _multipart_validation_error("title is invalid")
    return {
        "providerProfileId": provider_profile_id,
        "summaryLanguage": summary_language,
        "title": title,
    }


def _multipart_validation_error(message: str) -> V2Error:
    return V2Error("validation_error", message, 422, {})


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
