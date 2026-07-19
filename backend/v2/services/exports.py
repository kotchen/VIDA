from __future__ import annotations

import math
import re
from dataclasses import dataclass
from urllib.parse import quote

from ..domain import ChapterRecord, EpisodeRecord, SummaryRecord, TranscriptSegmentRecord
from ..errors import V2Error
from ..repositories.chapters import ChapterRepository
from ..repositories.episodes import EpisodeRepository


@dataclass(frozen=True)
class ExportDocument:
    content: bytes
    content_type: str
    content_disposition: str


class ExportService:
    _FORMATS = {
        "txt": "text/plain; charset=utf-8",
        "srt": "application/x-subrip",
        "md": "text/markdown; charset=utf-8",
    }

    def __init__(self, episodes: EpisodeRepository, chapters: ChapterRepository):
        self._episodes = episodes
        self._chapters = chapters

    def render(self, episode: EpisodeRecord, format_name: str) -> ExportDocument:
        if format_name not in self._FORMATS:
            raise V2Error("invalid_format", "Export format must be txt, srt, or md", 400, {})
        segments = self._episodes.get_transcript(episode.id)
        if format_name == "txt":
            text = "".join(
                f"[{_clock(row.start_sec)}] {_inline(row.speaker or 'Speaker 1')}: "
                f"{_inline(row.text)}\n"
                for row in segments
            )
        elif format_name == "srt":
            text = "\n\n".join(
                f"{index}\n{_srt_time(row.start_sec)} --> {_srt_time(row.end_sec)}\n"
                f"{_inline(row.text)}"
                for index, row in enumerate(segments, 1)
            )
            if text:
                text += "\n"
        else:
            text = self._markdown(episode, segments)
        return ExportDocument(
            text.encode("utf-8"), self._FORMATS[format_name],
            _attachment_header(episode.title, format_name),
        )

    def _markdown(
        self, episode: EpisodeRecord, segments: list[TranscriptSegmentRecord]
    ) -> str:
        summary: SummaryRecord | None = self._episodes.get_summary(episode.id)
        chapters: list[ChapterRecord] = self._chapters.list(episode.id)
        sections = [f"# {_inline(episode.title)}"]
        sections.append("## Summary\n\n" + ("" if summary is None else summary.content.strip()))
        chapter_lines = "\n".join(
            f"- [{_clock(row.start_sec)}] {_inline(row.title)}" for row in chapters
        )
        sections.append("## Chapters\n\n" + chapter_lines)
        transcript_lines = "\n\n".join(
            f"[{_clock(row.start_sec)}] **{_inline(row.speaker or 'Speaker 1')}:** "
            f"{_inline(row.text)}"
            for row in segments
        )
        sections.append("## Transcript\n\n" + transcript_lines)
        return "\n\n".join(sections).rstrip() + "\n"


def _inline(value: str) -> str:
    return " ".join(str(value).replace("\x00", "").split())


def _milliseconds(seconds: float) -> int:
    if not math.isfinite(seconds) or seconds < 0:
        return 0
    return int(seconds * 1000 + 0.5)


def _clock(seconds: float) -> str:
    total = _milliseconds(seconds) // 1000
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _srt_time(seconds: float) -> str:
    total = _milliseconds(seconds)
    hours, remainder = divmod(total, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _attachment_header(title: str, extension: str) -> str:
    clean = _inline(title)
    clean = re.sub(r'[\\/:*?"<>|;]', "_", clean).strip(" .") or "episode"
    clean = clean[:120].rstrip(" .") or "episode"
    unicode_name = f"{clean}.{extension}"
    ascii_stem = clean.encode("ascii", "ignore").decode("ascii")
    ascii_stem = re.sub(r"[^A-Za-z0-9._ -]", "_", ascii_stem).strip(" .") or "episode"
    ascii_name = f"{ascii_stem}.{extension}"
    return (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(unicode_name, safe='')}"
    )
