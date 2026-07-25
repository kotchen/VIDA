from __future__ import annotations

from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    UrlConstraints,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)

from .services.provider_profiles import ProviderProfile


def to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ProviderProfileIdString = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
]
SummaryLanguageString = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)
]
EpisodeTitleString = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
ChapterTitleString = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
EpisodeSourceUrl = Annotated[HttpUrl, UrlConstraints(max_length=2048)]


class ProviderProfileCreate(ApiModel):
    name: NonEmptyString
    base_url: HttpUrl
    api_key: SecretStr
    model_id: NonEmptyString
    temperature: float = Field(ge=0, le=2)

    @field_validator("api_key", mode="before")
    @classmethod
    def validate_api_key(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("apiKey must not be empty")
        return value


class ProviderProfileUpdate(ApiModel):
    name: NonEmptyString | None = None
    base_url: HttpUrl | None = None
    api_key: SecretStr | None = None
    model_id: NonEmptyString | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)

    @field_validator("api_key", mode="before")
    @classmethod
    def validate_api_key(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("apiKey must not be empty")
        return value

    @model_validator(mode="after")
    def require_change(self) -> "ProviderProfileUpdate":
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("update fields must not be null")
        return self


class ProviderProfileResponse(ApiModel):
    id: str
    name: str
    base_url: str
    model_id: str
    temperature: float
    revision: int
    active_revision_id: str
    api_key_masked: str
    has_api_key: bool = True
    created_at: str
    updated_at: str

    @classmethod
    def from_profile(cls, profile: ProviderProfile) -> "ProviderProfileResponse":
        return cls.model_validate(profile, from_attributes=True)


class ProviderConnectionTestResponse(ApiModel):
    ok: bool
    latency_ms: int = Field(ge=0)
    model_available: bool
    message: str


class EpisodeUrlSubmission(ApiModel):
    model_config = ConfigDict(extra="forbid")

    source_url: EpisodeSourceUrl
    provider_profile_id: ProviderProfileIdString
    summary_language: SummaryLanguageString
    title: EpisodeTitleString | None = None


class EpisodeUploadSubmission(ApiModel):
    model_config = ConfigDict(extra="forbid")

    provider_profile_id: ProviderProfileIdString
    summary_language: SummaryLanguageString
    title: EpisodeTitleString | None = None


class EpisodeSubmissionResponse(ApiModel):
    id: str
    title: str
    source_type: str
    media_url: str | None = None
    poster_url: str | None = None
    duration_sec: float = 0
    resolution: str | None = None
    status: str
    language: str
    created_at: str
    progress: int
    message: str
    queue_position: int | None
    provider_profile_id: str
    warnings: list[dict[str, str]]


class ProcessingWarningResponse(ApiModel):
    stage: str
    code: str
    message: str


class EpisodeResponse(EpisodeSubmissionResponse):
    warnings: list[ProcessingWarningResponse]


class TranscriptSegmentResponse(ApiModel):
    id: str
    start_sec: float
    end_sec: float
    speaker: str
    text: str


class SummaryResponse(ApiModel):
    episode_id: str
    content: str
    read_time_min: int
    key_points: int
    confidence: int
    generated_by: str


class ChapterResponse(ApiModel):
    id: str
    start_sec: float
    title: str
    duration_sec: float
    thumbnail_url: str | None
    bookmarked: bool


class ChapterCreate(ApiModel):
    model_config = ConfigDict(extra="forbid")

    start_sec: float = Field(ge=0, allow_inf_nan=False)
    title: ChapterTitleString


class ChapterUpdate(ApiModel):
    model_config = ConfigDict(extra="forbid")

    start_sec: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    title: ChapterTitleString | None = None
    bookmarked: bool | None = None

    @model_validator(mode="after")
    def require_change(self) -> "ChapterUpdate":
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("update fields must not be null")
        return self


class ProjectResponse(ApiModel):
    id: str
    title: str
    created_at: str
    duration_sec: float
    status: str
    thumbnail_url: str | None


class DashboardResponse(ApiModel):
    current_episode: EpisodeResponse | None
    summary: SummaryResponse | None
    transcript: list[TranscriptSegmentResponse]
    chapters: list[ChapterResponse]
    recent_projects: list[ProjectResponse]


class JobResponse(ApiModel):
    id: str
    episode_id: str
    type: str
    attempt: int
    status: str
    provider_profile_revision_id: str
    submitted_at: str
    started_at: str | None
    finished_at: str | None
    progress: int
    message: str
    queue_position: int | None
    error_code: str | None
    error_message: str | None
