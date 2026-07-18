from __future__ import annotations

import json
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
class ProcessingWarning:
    stage: str
    code: str
    message: str

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) for value in (self.stage, self.code, self.message)):
            raise TypeError("warning stage, code, and message must be strings")
        if self.stage not in {"optimization", "summary", "chapters"}:
            raise ValueError(f"invalid warning stage: {self.stage}")


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

    @property
    def warnings(self) -> list[ProcessingWarning]:
        values = json.loads(self.warnings_json)
        if not isinstance(values, list):
            raise ValueError("episode warnings must be a list")
        return [ProcessingWarning(**value) for value in values]


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


@dataclass(frozen=True)
class TranscriptSegmentRecord:
    id: str
    episode_id: str
    ordinal: int
    start_sec: float
    end_sec: float
    speaker: str | None
    text: str


@dataclass(frozen=True)
class SummaryRecord:
    episode_id: str
    content: str
    read_time_min: int
    key_points: int
    confidence: int
    generated_by: str

    def __post_init__(self) -> None:
        if type(self.key_points) is not int:
            raise TypeError("summary key_points must be an integer")
        if self.key_points < 0:
            raise ValueError("summary key_points must be non-negative")
        if type(self.confidence) is not int:
            raise TypeError("summary confidence must be an integer")
        if not 0 <= self.confidence <= 100:
            raise ValueError("summary confidence must be between 0 and 100")
        if self.generated_by != "VIDA":
            raise ValueError("summary generated_by must be VIDA")


@dataclass(frozen=True)
class ChapterRecord:
    id: str
    episode_id: str
    start_sec: float
    title: str
    duration_sec: float | None
    thumbnail_path: str | None
    bookmarked: bool
    source: str
