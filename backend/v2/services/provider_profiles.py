from __future__ import annotations

from dataclasses import dataclass

from ..crypto import CredentialCipher
from ..events import EventPublisher, ignore_event
from ..repositories.provider_profiles import (
    ProviderProfileRecord,
    ProviderProfileRepository,
)


@dataclass(frozen=True)
class ProviderProfile:
    id: str
    name: str
    base_url: str
    model_id: str
    temperature: float
    revision: int
    active_revision_id: str
    api_key_masked: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ProviderRevisionCredentials:
    id: str
    profile_id: str
    revision: int
    base_url: str
    api_key: str
    model_id: str
    temperature: float


class ProviderProfileService:
    def __init__(
        self,
        repository: ProviderProfileRepository,
        cipher: CredentialCipher,
        publish: EventPublisher = ignore_event,
    ):
        self._repository = repository
        self._cipher = cipher
        self._publish = publish

    def create_profile(
        self,
        name: str,
        base_url: str,
        api_key: str,
        model_id: str,
        temperature: float,
    ) -> ProviderProfile:
        record = self._repository.create(
            name, base_url, self._cipher.encrypt(api_key), model_id, temperature
        )
        profile = self._to_profile(record, api_key)
        self._publish("profiles.invalidated", {})
        return profile

    def update_profile(
        self,
        profile_id: str,
        *,
        name: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        model_id: str | None = None,
        temperature: float | None = None,
    ) -> ProviderProfile:
        credential = None if api_key is None else self._cipher.encrypt(api_key)
        record = self._repository.update(
            profile_id,
            name=name,
            base_url=base_url,
            credential=credential,
            model_id=model_id,
            temperature=temperature,
        )
        plaintext = api_key or self._cipher.decrypt(record.encrypted_credential)
        profile = self._to_profile(record, plaintext)
        self._publish("profiles.invalidated", {})
        return profile

    def delete_profile(self, profile_id: str) -> None:
        self._repository.delete(profile_id)
        self._publish("profiles.invalidated", {})

    def get_profile(self, profile_id: str) -> ProviderProfile | None:
        record = self._repository.get(profile_id)
        if record is None:
            return None
        return self._to_profile(record, self._cipher.decrypt(record.encrypted_credential))

    def list_profiles(self) -> list[ProviderProfile]:
        return [
            self._to_profile(record, self._cipher.decrypt(record.encrypted_credential))
            for record in self._repository.list()
        ]

    def get_revision_credentials(self, revision_id: str) -> ProviderRevisionCredentials:
        record = self._repository.get_revision(revision_id)
        if record is None:
            raise KeyError(revision_id)
        return ProviderRevisionCredentials(
            record.id,
            record.profile_id,
            record.revision,
            record.base_url,
            self._cipher.decrypt(record.encrypted_credential),
            record.model_id,
            record.temperature,
        )

    def _to_profile(self, record: ProviderProfileRecord, api_key: str) -> ProviderProfile:
        return ProviderProfile(
            record.id,
            record.name,
            record.base_url,
            record.model_id,
            record.temperature,
            record.revision,
            record.active_revision_id,
            _mask_key(api_key),
            record.created_at,
            record.updated_at,
        )


def _mask_key(api_key: str) -> str:
    mask = "\u2022" * 8
    if len(api_key) <= 4:
        return mask
    return mask + api_key[-4:]
