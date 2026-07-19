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

    def create_manual_derived(
        self,
        episode_id: str,
        start_sec: float,
        title: str,
        thumbnail_path: str | None,
    ) -> ChapterRecord:
        with self._database.transaction(immediate=True) as conn:
            duration = _duration_to_next_or_end(conn, episode_id, start_sec)
            chapter = ChapterRecord(
                str(uuid4()), episode_id, start_sec, title, duration,
                thumbnail_path, False, "manual",
            )
            _insert_chapter(conn, chapter, _utc_now())
            _recompute_durations(conn, episode_id)
            duration = conn.execute(
                "SELECT duration_sec FROM chapters WHERE id=?", (chapter.id,)
            ).fetchone()[0]
        return ChapterRecord(
            chapter.id, chapter.episode_id, chapter.start_sec, chapter.title,
            duration, chapter.thumbnail_path, chapter.bookmarked, chapter.source,
        )

    def update_manual(
        self,
        episode_id: str,
        chapter_id: str,
        *,
        start_sec: float | None = None,
        title: str | None = None,
    ) -> ChapterRecord:
        with self._database.transaction(immediate=True) as conn:
            _require_episode(conn, episode_id)
            row = conn.execute(
                "SELECT id,episode_id,start_sec,title,duration_sec,thumbnail_path,"
                "bookmarked,source FROM chapters WHERE id=? AND episode_id=?",
                (chapter_id, episode_id),
            ).fetchone()
            if row is None:
                raise KeyError(chapter_id)
            if row["source"] != "manual":
                raise PermissionError(chapter_id)
            new_start = row["start_sec"] if start_sec is None else start_sec
            new_title = row["title"] if title is None else title
            duration = _duration_to_next_or_end(
                conn, episode_id, new_start, excluding_id=chapter_id
            )
            conn.execute(
                "UPDATE chapters SET start_sec=?,title=?,duration_sec=? "
                "WHERE id=? AND episode_id=? AND source='manual'",
                (new_start, new_title, duration, chapter_id, episode_id),
            )
            _recompute_durations(conn, episode_id)
            duration = conn.execute(
                "SELECT duration_sec FROM chapters WHERE id=?", (chapter_id,)
            ).fetchone()[0]
            return ChapterRecord(
                chapter_id, episode_id, new_start, new_title, duration,
                row["thumbnail_path"], bool(row["bookmarked"]), "manual",
            )

    def delete_manual(self, episode_id: str, chapter_id: str) -> None:
        with self._database.transaction(immediate=True) as conn:
            _require_episode(conn, episode_id)
            row = conn.execute(
                "SELECT source FROM chapters WHERE id=? AND episode_id=?",
                (chapter_id, episode_id),
            ).fetchone()
            if row is None:
                raise KeyError(chapter_id)
            if row["source"] != "manual":
                raise PermissionError(chapter_id)
            conn.execute(
                "DELETE FROM chapters WHERE id=? AND episode_id=? AND source='manual'",
                (chapter_id, episode_id),
            )
            _recompute_durations(conn, episode_id)

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


def _duration_to_next_or_end(
    conn, episode_id: str, start_sec: float, excluding_id: str | None = None
) -> float:
    episode = conn.execute(
        "SELECT duration_sec FROM episodes WHERE id=?", (episode_id,)
    ).fetchone()
    if episode is None:
        raise KeyError(episode_id)
    end = episode["duration_sec"]
    if end is None or start_sec > end:
        raise ValueError("chapter start is outside episode duration")
    query = (
        "SELECT start_sec FROM chapters WHERE episode_id=? AND start_sec>?"
        + (" AND id<>?" if excluding_id is not None else "")
        + " ORDER BY start_sec,id LIMIT 1"
    )
    parameters = (
        (episode_id, start_sec, excluding_id)
        if excluding_id is not None else (episode_id, start_sec)
    )
    following = conn.execute(query, parameters).fetchone()
    next_start = end if following is None else min(end, following["start_sec"])
    return max(0.0, next_start - start_sec)


def _recompute_durations(conn, episode_id: str) -> None:
    episode = conn.execute(
        "SELECT duration_sec FROM episodes WHERE id=?", (episode_id,)
    ).fetchone()
    if episode is None:
        raise KeyError(episode_id)
    episode_end = episode["duration_sec"]
    if episode_end is None:
        return
    rows = conn.execute(
        "SELECT id,start_sec FROM chapters WHERE episode_id=? ORDER BY start_sec,id",
        (episode_id,),
    ).fetchall()
    for index, row in enumerate(rows):
        following = rows[index + 1]["start_sec"] if index + 1 < len(rows) else episode_end
        conn.execute(
            "UPDATE chapters SET duration_sec=? WHERE id=?",
            (max(0.0, min(episode_end, following) - row["start_sec"]), row["id"]),
        )


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
