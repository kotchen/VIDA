from __future__ import annotations

import json
from dataclasses import asdict
from sqlite3 import Connection, Row
from typing import Iterable

from ..database import Database
from ..domain import (
    EpisodeRecord,
    ProcessingWarning,
    SummaryRecord,
    TranscriptSegmentRecord,
)


class EpisodeRepository:
    def __init__(self, database: Database):
        self._database = database

    def create(self, episode: EpisodeRecord) -> EpisodeRecord:
        with self._database.transaction() as conn:
            conn.execute(
                f"INSERT INTO episodes ({', '.join(_EPISODE_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in _EPISODE_COLUMNS)})",
                tuple(getattr(episode, column) for column in _EPISODE_COLUMNS),
            )
        return episode

    def get(self, episode_id: str) -> EpisodeRecord | None:
        with self._database.connect() as conn:
            row = _select_episode(conn, episode_id)
        return None if row is None else _episode_from_row(row)

    def list_projects(self, limit: int = 12, offset: int = 0) -> list[EpisodeRecord]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        selected = ["NULL AS source_path" if name == "source_path" else name
                    for name in _EPISODE_COLUMNS]
        with self._database.connect() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(selected)} FROM episodes "
                "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [_episode_from_row(row) for row in rows]

    def latest_completed(self) -> EpisodeRecord | None:
        with self._database.connect() as conn:
            row = conn.execute(
                f"SELECT {', '.join(_EPISODE_COLUMNS)} FROM episodes "
                "WHERE status='completed' ORDER BY created_at DESC, id DESC LIMIT 1"
            ).fetchone()
        return None if row is None else _episode_from_row(row)

    def replace_transcript(
        self, episode_id: str, segments: Iterable[TranscriptSegmentRecord]
    ) -> None:
        records = list(segments)
        with self._database.transaction(immediate=True) as conn:
            _require_episode(conn, episode_id)
            if any(record.episode_id != episode_id for record in records):
                raise ValueError("transcript segment belongs to another episode")
            conn.execute("DELETE FROM transcript_segments WHERE episode_id=?", (episode_id,))
            conn.executemany(
                "INSERT INTO transcript_segments"
                "(id,episode_id,ordinal,start_sec,end_sec,speaker,text) VALUES(?,?,?,?,?,?,?)",
                [
                    (row.id, row.episode_id, row.ordinal, row.start_sec, row.end_sec,
                     row.speaker, row.text)
                    for row in sorted(records, key=lambda item: (item.ordinal, item.id))
                ],
            )

    def get_transcript(self, episode_id: str) -> list[TranscriptSegmentRecord]:
        with self._database.connect() as conn:
            rows = conn.execute(
                "SELECT id,episode_id,ordinal,start_sec,end_sec,speaker,text "
                "FROM transcript_segments WHERE episode_id=? "
                "ORDER BY start_sec, ordinal, id",
                (episode_id,),
            ).fetchall()
        return [TranscriptSegmentRecord(*tuple(row)) for row in rows]

    def replace_summary(self, episode_id: str, summary: SummaryRecord) -> None:
        with self._database.transaction(immediate=True) as conn:
            _require_episode(conn, episode_id)
            if summary.episode_id != episode_id:
                raise ValueError("summary belongs to another episode")
            _validate_summary(summary)
            conn.execute("DELETE FROM summaries WHERE episode_id=?", (episode_id,))
            conn.execute(
                "INSERT INTO summaries"
                "(episode_id,content,read_time_min,key_points,confidence,generated_by) "
                "VALUES(?,?,?,?,?,?)",
                (
                    summary.episode_id,
                    summary.content,
                    summary.read_time_min,
                    summary.key_points,
                    summary.confidence,
                    summary.generated_by,
                ),
            )

    def get_summary(self, episode_id: str) -> SummaryRecord | None:
        with self._database.connect() as conn:
            row = conn.execute(
                "SELECT episode_id,content,read_time_min,key_points,confidence,generated_by "
                "FROM summaries WHERE episode_id=?",
                (episode_id,),
            ).fetchone()
        if row is None:
            return None
        return SummaryRecord(
            row["episode_id"], row["content"], row["read_time_min"], row["key_points"],
            row["confidence"], row["generated_by"],
        )

    def set_warnings(
        self, episode_id: str, warnings: Iterable[ProcessingWarning]
    ) -> None:
        records = list(warnings)
        if not all(isinstance(warning, ProcessingWarning) for warning in records):
            raise TypeError("warnings must contain ProcessingWarning records")
        payload = json.dumps(
            [asdict(warning) for warning in records], ensure_ascii=False, separators=(",", ":")
        )
        with self._database.transaction() as conn:
            cursor = conn.execute(
                "UPDATE episodes SET warnings_json=? WHERE id=?", (payload, episode_id)
            )
            if cursor.rowcount == 0:
                raise KeyError(episode_id)

    def get_warnings(self, episode_id: str) -> list[ProcessingWarning]:
        episode = self.get(episode_id)
        if episode is None:
            raise KeyError(episode_id)
        return episode.warnings


_EPISODE_COLUMNS = (
    "id", "title", "source_type", "source_path", "source_url",
    "source_content_type", "media_path", "media_content_type", "poster_path",
    "poster_content_type", "duration_sec", "resolution", "status", "language",
    "summary_language", "progress", "message", "warnings_json", "error_code",
    "error_message", "current_job_id", "provider_profile_id", "created_at",
    "updated_at", "completed_at",
)


def _select_episode(conn: Connection, episode_id: str) -> Row | None:
    return conn.execute(
        f"SELECT {', '.join(_EPISODE_COLUMNS)} FROM episodes WHERE id=?", (episode_id,)
    ).fetchone()


def _require_episode(conn: Connection, episode_id: str) -> None:
    if conn.execute("SELECT 1 FROM episodes WHERE id=?", (episode_id,)).fetchone() is None:
        raise KeyError(episode_id)


def _episode_from_row(row: Row) -> EpisodeRecord:
    return EpisodeRecord(*(row[column] for column in _EPISODE_COLUMNS))


def _validate_summary(summary: SummaryRecord) -> None:
    if type(summary.key_points) is not int:
        raise TypeError("summary key_points must be an integer")
    if summary.key_points < 0:
        raise ValueError("summary key_points must be non-negative")
    if type(summary.confidence) is not int:
        raise TypeError("summary confidence must be an integer")
    if not 0 <= summary.confidence <= 100:
        raise ValueError("summary confidence must be between 0 and 100")
