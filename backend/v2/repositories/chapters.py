from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable
from uuid import uuid4

from ..database import Database
from ..domain import ChapterRecord


class ChapterRepository:
    def __init__(self, database: Database):
        self._database = database

    def list(self, episode_id: str) -> list[ChapterRecord]:
        with self._database.connect() as conn:
            rows = conn.execute(
                "SELECT id,episode_id,start_sec,title,duration_sec,thumbnail_path,"
                "bookmarked,source FROM chapters WHERE episode_id=? "
                "ORDER BY start_sec, id",
                (episode_id,),
            ).fetchall()
        return [
            ChapterRecord(
                row["id"], row["episode_id"], row["start_sec"], row["title"],
                row["duration_sec"], row["thumbnail_path"], bool(row["bookmarked"]),
                row["source"],
            )
            for row in rows
        ]

    def create_manual(
        self,
        episode_id: str,
        start_sec: float,
        title: str,
        duration_sec: float | None,
        thumbnail_path: str | None,
        bookmarked: bool,
    ) -> ChapterRecord:
        chapter = ChapterRecord(
            str(uuid4()), episode_id, start_sec, title, duration_sec, thumbnail_path,
            bookmarked, "manual",
        )
        with self._database.transaction(immediate=True) as conn:
            _require_episode(conn, episode_id)
            _insert_chapter(conn, chapter, _utc_now())
        return chapter

    def replace_generated(
        self, episode_id: str, chapters: Iterable[ChapterRecord]
    ) -> None:
        records = list(chapters)
        with self._database.transaction(immediate=True) as conn:
            _require_episode(conn, episode_id)
            if any(row.episode_id != episode_id for row in records):
                raise ValueError("chapter belongs to another episode")
            if any(row.source != "generated" for row in records):
                raise ValueError("generated replacement accepts only generated chapters")
            conn.execute(
                "DELETE FROM chapters WHERE episode_id=? AND source='generated'",
                (episode_id,),
            )
            now = _utc_now()
            for row in sorted(records, key=lambda item: (item.start_sec, item.id)):
                _insert_chapter(conn, row, now)


def _require_episode(conn, episode_id: str) -> None:
    if conn.execute("SELECT 1 FROM episodes WHERE id=?", (episode_id,)).fetchone() is None:
        raise KeyError(episode_id)


def _insert_chapter(conn, chapter: ChapterRecord, created_at: str) -> None:
    conn.execute(
        "INSERT INTO chapters"
        "(id,episode_id,start_sec,title,duration_sec,thumbnail_path,"
        "thumbnail_content_type,bookmarked,source,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            chapter.id, chapter.episode_id, chapter.start_sec, chapter.title,
            chapter.duration_sec, chapter.thumbnail_path, None,
            int(chapter.bookmarked), chapter.source, created_at,
        ),
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
