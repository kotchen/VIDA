import asyncio
import tempfile
import unittest
from pathlib import Path

from backend.transcriber import StructuredTranscription, TranscriptSegment
from backend.v2.database import Database
from backend.v2.bootstrap import build_test_runtime
from backend.v2.crypto import CredentialCipher
from backend.v2.domain import (
    ChapterRecord, EpisodeRecord, JobRecord, SummaryRecord,
)
from backend.v2.jobs.models import JobCanceled, TranscriptionFailed
from backend.v2.jobs.pipeline import EpisodePipeline
from backend.v2.jobs.scheduler import Scheduler
from backend.v2.jobs.source_ingest import PreparedSource
from backend.v2.repositories.chapters import ChapterRepository
from backend.v2.repositories.episodes import EpisodeRepository
from backend.v2.repositories.jobs import JobRepository
from backend.v2.repositories.provider_profiles import ProviderProfileRepository
from backend.v2.services.provider_profiles import ProviderRevisionCredentials


NOW = "2026-07-19T00:00:00.000Z"
CREDENTIALS = ProviderRevisionCredentials(
    "revision-1", "profile-1", 1, "https://provider.example/v1",
    "secret", "model-1", 0.2,
)


class Profiles:
    def __init__(self, credentials):
        self.requested = []
        self.credentials = credentials

    def get_revision_credentials(self, revision_id):
        self.requested.append(revision_id)
        return self.credentials


class Source:
    async def prepare(self, episode, attempt_dir, cancel_check, progress=None):
        attempt_dir.mkdir(parents=True, exist_ok=True)
        media = attempt_dir / "media.mp3"
        poster = attempt_dir / "poster.png"
        media.write_bytes(b"media")
        poster.write_bytes(b"poster")
        return PreparedSource(media, 30.0, None, "audio/mpeg", poster, "image/png")


class Media:
    def commit_file(self, staged, final):
        final.parent.mkdir(parents=True, exist_ok=True)
        staged.replace(final)


class Transcriber:
    def __init__(self, error=None, gate=None, segments=None):
        self.error = error
        self.gate = gate
        self.segments = segments

    async def transcribe_segments(self, path, language=None):
        if self.gate is not None:
            await self.gate.wait()
        if self.error:
            raise self.error
        rows = self.segments if self.segments is not None else (
            TranscriptSegment(0, 2, "Hello"), TranscriptSegment(2, 5, "world")
        )
        return StructuredTranscription("en", 0.9, tuple(rows))


class AI:
    def __init__(self, optimize_error=None, summary_error=None, chapter_error=None):
        self.optimize_error = optimize_error
        self.summary_error = summary_error
        self.chapter_error = chapter_error

    async def optimize(self, text):
        if self.optimize_error:
            raise self.optimize_error
        return "Optimized transcript"

    async def summarize(self, episode_id, transcript, language, title):
        if self.summary_error:
            raise self.summary_error
        return SummaryRecord(episode_id, "Summary", 1, 2, 91, "VIDA")

    async def generate_chapters(self, episode_id, transcript, duration, poster):
        if self.chapter_error:
            raise self.chapter_error
        return [ChapterRecord(
            "chapter-1", episode_id, 0, "Opening", duration, poster, False, "generated"
        )]


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.database = Database(self.data / "db.sqlite3")
        self.database.initialize()
        self.episodes = EpisodeRepository(self.database)
        self.jobs = JobRepository(self.database)
        self.chapters = ChapterRepository(self.database)
        profile = ProviderProfileRepository(self.database).create(
            "Profile", "https://provider.example/v1",
            CredentialCipher(b"x" * 32).encrypt("secret"), "model-1", 0.2,
        )
        self.credentials = replace_credentials(profile.id, profile.active_revision_id)
        self.profiles = Profiles(self.credentials)
        self.episode = self.episodes.create(EpisodeRecord(
            "episode-1", "Title", "upload", "episodes/episode-1/source/a.mp3",
            None, "audio/mpeg", None, None, None, None, None, None, "queued",
            None, "zh", 0, "Queued", "[]", None, None, None, profile.id,
            NOW, NOW, None,
        ))
        queued = JobRecord(
            "job-1", "episode-1", "process_episode", 1, "queued", profile.active_revision_id,
            NOW, None, None, None, None, None, 0, "Queued", None, None,
        )
        self.jobs.enqueue(queued)
        self.job = self.jobs.claim_next("worker-1", NOW)

    def make_pipeline(self, ai, *, transcriber=None, heartbeat=5.0):
        captured = []

        def ai_factory(credentials):
            captured.append(credentials)
            return ai

        pipeline = EpisodePipeline(
            data_dir=self.data,
            profiles=self.profiles,
            episodes=self.episodes,
            chapters=self.chapters,
            jobs=self.jobs,
            media=Media(),
            source=Source(),
            transcriber=transcriber or Transcriber(),
            ai_factory=ai_factory,
            heartbeat_interval_sec=heartbeat,
            now=lambda: NOW,
        )
        return pipeline, captured

    async def test_optional_ai_failures_complete_with_sanitized_warnings(self):
        pipeline, captured = self.make_pipeline(AI(
            RuntimeError("optimize secret"), RuntimeError("summary secret"),
            RuntimeError("chapter secret"),
        ))
        await pipeline.execute(self.job, lambda: False)

        episode = self.episodes.get("episode-1")
        self.assertEqual(episode.status, "completed")
        self.assertEqual(self.jobs.get_required("job-1").status, "completed")
        self.assertEqual({warning.stage for warning in episode.warnings}, {
            "optimization", "summary", "chapters"
        })
        self.assertTrue(all("secret" not in warning.message for warning in episode.warnings))
        self.assertEqual(len(self.episodes.get_transcript("episode-1")), 2)
        self.assertEqual(captured, [self.credentials])
        self.assertEqual(self.profiles.requested, [self.credentials.id])

    async def test_success_commits_media_summary_chapters_and_metadata_before_completion(self):
        pipeline, _ = self.make_pipeline(AI())
        await pipeline.execute(self.job, lambda: False)

        episode = self.episodes.get("episode-1")
        self.assertEqual((episode.language, episode.duration_sec), ("en", 30.0))
        self.assertEqual(episode.media_content_type, "audio/mpeg")
        self.assertTrue((self.data / episode.media_path).is_file())
        self.assertTrue((self.data / episode.poster_path).is_file())
        self.assertEqual(self.episodes.get_summary("episode-1").generated_by, "VIDA")
        self.assertEqual([row.title for row in self.chapters.list("episode-1")], ["Opening"])

    async def test_transcription_failure_is_a_core_pipeline_error(self):
        pipeline, _ = self.make_pipeline(
            AI(), transcriber=Transcriber(RuntimeError("provider secret"))
        )
        with self.assertRaisesRegex(TranscriptionFailed, "Transcription failed") as caught:
            await pipeline.execute(self.job, lambda: False)
        self.assertNotIn("secret", str(caught.exception))
        self.assertEqual(self.jobs.get_required("job-1").status, "processing")
        self.assertEqual(self.episodes.get_transcript("episode-1"), [])
        self.assertFalse(
            (self.data / "episodes" / "episode-1" / "attempts" / "job-1").exists()
        )

    async def test_empty_transcription_is_a_core_pipeline_error(self):
        pipeline, _ = self.make_pipeline(AI(), transcriber=Transcriber(segments=[]))
        with self.assertRaises(TranscriptionFailed):
            await pipeline.execute(self.job, lambda: False)
        self.assertEqual(self.jobs.get_required("job-1").status, "processing")

    async def test_heartbeat_refreshes_during_long_transcription_and_cancel_is_checked_after(self):
        gate = asyncio.Event()
        pipeline, _ = self.make_pipeline(
            AI(), transcriber=Transcriber(gate=gate), heartbeat=0.01
        )
        canceled = False
        task = asyncio.create_task(pipeline.execute(self.job, lambda: canceled))
        await asyncio.sleep(0.04)
        heartbeat = self.jobs.get_required("job-1").heartbeat_at
        canceled = True
        gate.set()
        with self.assertRaises(JobCanceled):
            await task
        self.assertEqual(heartbeat, NOW)
        self.assertEqual(self.jobs.get_required("job-1").status, "processing")

    async def test_cancel_after_source_cleans_uncommitted_attempt(self):
        pipeline, _ = self.make_pipeline(AI())
        checks = 0

        def canceled():
            nonlocal checks
            checks += 1
            return checks >= 3

        with self.assertRaises(JobCanceled):
            await pipeline.execute(self.job, canceled)
        self.assertFalse(
            (self.data / "episodes" / "episode-1" / "attempts" / "job-1").exists()
        )

    async def test_runtime_wires_real_pipeline_without_live_provider_calls(self):
        runtime = build_test_runtime(self.data / "runtime", b"z" * 32)
        self.assertIsInstance(runtime.job_executor, EpisodePipeline)
        self.assertIs(runtime.scheduler._executor, runtime.job_executor)

    async def test_scheduler_accepts_pipeline_owned_terminal_completion(self):
        pipeline, _ = self.make_pipeline(AI())
        scheduler = Scheduler(self.jobs, pipeline, 1, 1)
        await scheduler._execute(self.job)
        self.assertEqual(self.jobs.get_required("job-1").status, "completed")


if __name__ == "__main__":
    unittest.main()


def replace_credentials(profile_id, revision_id):
    return ProviderRevisionCredentials(
        revision_id, profile_id, 1, CREDENTIALS.base_url, CREDENTIALS.api_key,
        CREDENTIALS.model_id, CREDENTIALS.temperature,
    )
