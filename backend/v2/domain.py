from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EpisodeStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class JobType(str, Enum):
    PROCESS_EPISODE = "process_episode"
    REGENERATE_SUMMARY = "regenerate_summary"
    REGENERATE_CHAPTERS = "regenerate_chapters"


@dataclass(frozen=True)
class EpisodeRecord:
    id: str
    title: str
    source_type: str
    source_path: str | None
    source_url: str | None
    source_content_type: str | None
    media_path: str | None
    media_content_type: str | None
    poster_path: str | None
    poster_content_type: str | None
    duration_sec: float | None
    resolution: str | None
    status: str
    language: str | None
    summary_language: str
    progress: int
    message: str
    warnings_json: str
    error_code: str | None
    error_message: str | None
    current_job_id: str | None
    provider_profile_id: str
    created_at: str
    updated_at: str
    completed_at: str | None


@dataclass(frozen=True)
class JobRecord:
    id: str
    episode_id: str
    type: str
    attempt: int
    status: str
    provider_profile_revision_id: str
    submitted_at: str
    started_at: str | None
    finished_at: str | None
    cancel_requested_at: str | None
    heartbeat_at: str | None
    worker_id: str | None
    progress: int
    message: str
    error_code: str | None
    error_message: str | None
