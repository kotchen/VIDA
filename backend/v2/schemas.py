from __future__ import annotations

from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
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
