from __future__ import annotations

from dataclasses import dataclass

from ..domain import (
    ChapterRecord,
    EpisodeRecord,
    SummaryRecord,
    TranscriptSegmentRecord,
)
from ..repositories.chapters import ChapterRepository
from ..repositories.episodes import EpisodeRepository


@dataclass(frozen=True)
class DashboardData:
    current_episode: EpisodeRecord | None
    summary: SummaryRecord | None
    transcript: tuple[TranscriptSegmentRecord, ...]
    chapters: tuple[ChapterRecord, ...]
    recent_projects: tuple[EpisodeRecord, ...]


class DashboardService:
    def __init__(self, episodes: EpisodeRepository, chapters: ChapterRepository):
        self._episodes = episodes
        self._chapters = chapters

    def get(self) -> DashboardData:
        current = self._episodes.latest_completed()
        projects = tuple(self._episodes.list_projects(12, 0))
        if current is None:
            return DashboardData(None, None, (), (), projects)
        return DashboardData(
            current,
            self._episodes.get_summary(current.id),
            tuple(self._episodes.get_transcript(current.id)),
            tuple(self._chapters.list(current.id)),
            projects,
        )
