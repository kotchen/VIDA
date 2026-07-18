from __future__ import annotations

from ..domain import EpisodeRecord, SummaryRecord, TranscriptSegmentRecord
from ..errors import V2Error
from ..repositories.episodes import EpisodeRepository


class EpisodeService:
    def __init__(self, repository: EpisodeRepository):
        self._repository = repository

    def get_episode(self, episode_id: str) -> EpisodeRecord:
        episode = self._repository.get(episode_id)
        if episode is None:
            raise _episode_not_found()
        return episode

    def list_projects(self, limit: int = 12, offset: int = 0) -> list[EpisodeRecord]:
        return self._repository.list_projects(limit, offset)

    def get_transcript(self, episode_id: str) -> list[TranscriptSegmentRecord]:
        self.get_episode(episode_id)
        return self._repository.get_transcript(episode_id)

    def get_summary(self, episode_id: str) -> SummaryRecord:
        self.get_episode(episode_id)
        summary = self._repository.get_summary(episode_id)
        if summary is None:
            raise V2Error("summary_not_found", "Episode summary not found", 404, {})
        return summary


def _episode_not_found() -> V2Error:
    return V2Error("episode_not_found", "Episode not found", 404, {})
