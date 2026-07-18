from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from sqlite3 import Connection, Row
from uuid import uuid4

from backend.v2.crypto import EncryptedCredential
from backend.v2.database import Database


@dataclass(frozen=True)
class ProviderProfileRecord:
    id: str
    name: str
    active_revision_id: str
    revision: int
    base_url: str
    model_id: str
    temperature: float
    encrypted_credential: EncryptedCredential
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ProviderProfileRevisionRecord:
    id: str
    profile_id: str
    revision: int
    base_url: str
    model_id: str
    temperature: float
    encrypted_credential: EncryptedCredential
    created_at: str


class ProviderProfileRepository:
    def __init__(self, database: Database):
        self._database = database

    def create(
        self,
        name: str,
        base_url: str,
        credential: EncryptedCredential,
        model_id: str,
        temperature: float,
    ) -> ProviderProfileRecord:
        profile_id = str(uuid4())
        revision_id = str(uuid4())
        now = _utc_now()
        with self._database.transaction(immediate=True) as conn:
            conn.execute(
                """INSERT INTO provider_profiles
                   (id, name, active_revision_id, deleted_at, created_at, updated_at)
                   VALUES (?, ?, NULL, NULL, ?, ?)""",
                (profile_id, name, now, now),
            )
            _insert_revision(
                conn,
                revision_id,
                profile_id,
                1,
                base_url,
                model_id,
                temperature,
                credential,
                now,
            )
            conn.execute(
                "UPDATE provider_profiles SET active_revision_id=? WHERE id=?",
                (revision_id, profile_id),
            )
            row = _select_profile(conn, profile_id)
        return _profile_from_row(row)

    def update(
        self,
        profile_id: str,
        *,
        name: str | None = None,
        base_url: str | None = None,
        credential: EncryptedCredential | None = None,
        model_id: str | None = None,
        temperature: float | None = None,
    ) -> ProviderProfileRecord:
        now = _utc_now()
        revision_id = str(uuid4())
        with self._database.transaction(immediate=True) as conn:
            current = _select_profile(conn, profile_id)
            if current is None:
                raise KeyError(profile_id)
            next_revision = current["version"] + 1
            selected_credential = credential or _credential_from_row(current)
            _insert_revision(
                conn,
                revision_id,
                profile_id,
                next_revision,
                current["base_url"] if base_url is None else base_url,
                current["model_id"] if model_id is None else model_id,
                current["temperature"] if temperature is None else temperature,
                selected_credential,
                now,
            )
            conn.execute(
                """UPDATE provider_profiles
                   SET name=?, active_revision_id=?, updated_at=?
                   WHERE id=? AND deleted_at IS NULL""",
                (current["name"] if name is None else name, revision_id, now, profile_id),
            )
            row = _select_profile(conn, profile_id)
        return _profile_from_row(row)

    def delete(self, profile_id: str) -> None:
        now = _utc_now()
        with self._database.transaction(immediate=True) as conn:
            cursor = conn.execute(
                """UPDATE provider_profiles SET deleted_at=?, updated_at=?
                   WHERE id=? AND deleted_at IS NULL""",
                (now, now, profile_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(profile_id)

    def get(self, profile_id: str) -> ProviderProfileRecord | None:
        with self._database.connect() as conn:
            row = _select_profile(conn, profile_id)
        return None if row is None else _profile_from_row(row)

    def list(self) -> list[ProviderProfileRecord]:
        with self._database.connect() as conn:
            rows = conn.execute(_PROFILE_SELECT + " ORDER BY p.created_at, p.id").fetchall()
        return [_profile_from_row(row) for row in rows]

    def get_revision(self, revision_id: str) -> ProviderProfileRevisionRecord | None:
        with self._database.connect() as conn:
            row = conn.execute(
                """SELECT id, profile_id, version, base_url, model_id, temperature,
                          encrypted_api_key, encryption_nonce,
                          encryption_format_version, created_at
                   FROM provider_profile_revisions WHERE id=?""",
                (revision_id,),
            ).fetchone()
        return None if row is None else _revision_from_row(row)


_PROFILE_SELECT = """SELECT p.id, p.name, p.active_revision_id, p.created_at, p.updated_at,
                             r.version, r.base_url, r.model_id, r.temperature,
                             r.encrypted_api_key, r.encryption_nonce,
                             r.encryption_format_version
                      FROM provider_profiles p
                      JOIN provider_profile_revisions r
                        ON r.profile_id=p.id AND r.id=p.active_revision_id
                      WHERE p.deleted_at IS NULL"""


def _select_profile(conn: Connection, profile_id: str) -> Row | None:
    return conn.execute(_PROFILE_SELECT + " AND p.id=?", (profile_id,)).fetchone()


def _insert_revision(
    conn: Connection,
    revision_id: str,
    profile_id: str,
    revision: int,
    base_url: str,
    model_id: str,
    temperature: float,
    credential: EncryptedCredential,
    created_at: str,
) -> None:
    conn.execute(
        """INSERT INTO provider_profile_revisions
           (id, profile_id, version, base_url, model_id, temperature,
            encrypted_api_key, encryption_nonce, encryption_format_version, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            revision_id,
            profile_id,
            revision,
            base_url,
            model_id,
            temperature,
            credential.ciphertext,
            credential.nonce,
            credential.format_version,
            created_at,
        ),
    )


def _credential_from_row(row: Row) -> EncryptedCredential:
    return EncryptedCredential(
        row["encrypted_api_key"],
        row["encryption_nonce"],
        row["encryption_format_version"],
    )


def _profile_from_row(row: Row) -> ProviderProfileRecord:
    return ProviderProfileRecord(
        row["id"],
        row["name"],
        row["active_revision_id"],
        row["version"],
        row["base_url"],
        row["model_id"],
        row["temperature"],
        _credential_from_row(row),
        row["created_at"],
        row["updated_at"],
    )


def _revision_from_row(row: Row) -> ProviderProfileRevisionRecord:
    return ProviderProfileRevisionRecord(
        row["id"],
        row["profile_id"],
        row["version"],
        row["base_url"],
        row["model_id"],
        row["temperature"],
        _credential_from_row(row),
        row["created_at"],
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
