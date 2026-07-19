import asyncio
import math
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from backend.transcriber import StructuredTranscription, TranscriptSegment
from backend.transcriber import Transcriber as ProductionTranscriber
from backend.v2.database import Database
from backend.v2.bootstrap import build_test_runtime
from backend.v2.crypto import CredentialCipher
from backend.v2.domain import (
    ChapterRecord, EpisodeRecord, JobRecord, SummaryRecord,
)
from backend.v2.jobs.models import JobCanceled, TranscriptionFailed
from backend.v2.jobs.ai import AIProcessor
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


class BlockingWhisperModel:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()

    def transcribe(self, *args, **kwargs):
        def segments():
            self.started.set()
            self.release.wait(timeout=2)
            self.finished.set()
            yield SimpleNamespace(start=0.0, end=1.0, text="text")

        info = SimpleNamespace(language="en", language_probability=0.9)
        return segments(), info


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

    def make_pipeline(
        self, ai, *, transcriber=None, heartbeat=5.0, episodes=None, chapters=None,
        media=None,
    ):
        captured = []

        def ai_factory(credentials):
            captured.append(credentials)
            return ai

        pipeline = EpisodePipeline(
            data_dir=self.data,
            profiles=self.profiles,
            episodes=episodes or self.episodes,
            chapters=chapters or self.chapters,
            jobs=self.jobs,
            media=media or Media(),
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

    async def test_cancel_immediately_after_core_commit_keeps_new_references(self):
        self._seed_old_media()
        canceled = False
        original = self.episodes.commit_core_output

        def commit(*args, **kwargs):
            nonlocal canceled
            original(*args, **kwargs)
            canceled = True

        self.episodes.commit_core_output = commit
        pipeline, _ = self.make_pipeline(AI())
        with self.assertRaises(JobCanceled):
            await pipeline.execute(self.job, lambda: canceled)

        episode = self.episodes.get("episode-1")
        self.assertNotEqual(episode.media_path, "episodes/episode-1/artifacts/old.mp3")
        self.assertTrue((self.data / episode.media_path).is_file())
        self.assertTrue((self.data / episode.poster_path).is_file())
        self.assertTrue((self.data / "episodes/episode-1/artifacts/old.mp3").is_file())

    async def test_cancel_during_blocked_core_commit_waits_and_keeps_references(self):
        started = threading.Event()
        release = threading.Event()
        canceled = False
        original = self.episodes.commit_core_output

        def commit(*args, **kwargs):
            started.set()
            release.wait(timeout=2)
            original(*args, **kwargs)

        self.episodes.commit_core_output = commit
        pipeline, _ = self.make_pipeline(AI(), heartbeat=0.01)
        task = asyncio.create_task(pipeline.execute(self.job, lambda: canceled))
        self.assertTrue(await asyncio.to_thread(started.wait, 1))
        canceled = True
        release.set()
        with self.assertRaises(JobCanceled):
            await task

        episode = self.episodes.get("episode-1")
        self.assertTrue((self.data / episode.media_path).is_file())
        self.assertTrue((self.data / episode.poster_path).is_file())

    async def test_repeated_task_cancel_during_core_commit_joins_before_propagating(self):
        started = threading.Event()
        release = threading.Event()
        original = self.episodes.commit_core_output

        def commit(*args, **kwargs):
            started.set()
            release.wait(timeout=2)
            original(*args, **kwargs)

        self.episodes.commit_core_output = commit
        pipeline, _ = self.make_pipeline(AI(), heartbeat=0.01)
        task = asyncio.create_task(pipeline.execute(self.job, lambda: False))
        self.assertTrue(await asyncio.to_thread(started.wait, 1))
        task.cancel()
        await asyncio.sleep(0.02)
        self.assertFalse(task.done())
        task.cancel()
        await asyncio.sleep(0.02)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task

        episode = self.episodes.get("episode-1")
        self.assertTrue((self.data / episode.media_path).is_file())
        self.assertTrue((self.data / episode.poster_path).is_file())

    async def test_repeated_cancel_during_file_commit_joins_and_removes_orphans(self):
        started = threading.Event()
        release = threading.Event()

        class BlockingMedia(Media):
            def __init__(self):
                self.calls = 0
                self.destinations = []

            def commit_file(inner_self, staged, final):
                inner_self.calls += 1
                inner_self.destinations.append(final)
                super(BlockingMedia, inner_self).commit_file(staged, final)
                if inner_self.calls == 2:
                    started.set()
                    release.wait(timeout=2)

        media = BlockingMedia()
        pipeline, _ = self.make_pipeline(AI(), media=media)
        task = asyncio.create_task(pipeline.execute(self.job, lambda: False))
        self.assertTrue(await asyncio.to_thread(started.wait, 1))
        task.cancel()
        await asyncio.sleep(0.02)
        self.assertFalse(task.done())
        task.cancel()
        await asyncio.sleep(0.02)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertEqual(len(media.destinations), 2)
        self.assertTrue(all(not path.exists() for path in media.destinations))
        self.assertFalse(
            (self.data / "episodes" / "episode-1" / "attempts" / "job-1").exists()
        )
        self.assertIsNone(self.episodes.get("episode-1").media_path)

    async def test_repeated_cancel_joins_whisper_before_attempt_cleanup(self):
        model = BlockingWhisperModel()
        transcriber = ProductionTranscriber()
        transcriber.model = model
        pipeline, _ = self.make_pipeline(AI(), transcriber=transcriber, heartbeat=0.01)

        task = asyncio.create_task(pipeline.execute(self.job, lambda: False))
        self.assertTrue(await asyncio.to_thread(model.started.wait, 1))
        attempt = self.data / "episodes" / "episode-1" / "attempts" / "job-1"
        task.cancel()
        await asyncio.sleep(0.02)
        done_after_first_cancel = task.done()
        attempt_after_first_cancel = attempt.exists()
        task.cancel()
        await asyncio.sleep(0.02)
        done_after_second_cancel = task.done()
        attempt_after_second_cancel = attempt.exists()
        model.release.set()
        outcome = (await asyncio.gather(task, return_exceptions=True))[0]

        self.assertFalse(done_after_first_cancel)
        self.assertFalse(done_after_second_cancel)
        self.assertTrue(attempt_after_first_cancel)
        self.assertTrue(attempt_after_second_cancel)
        self.assertTrue(model.finished.is_set())
        self.assertIsInstance(outcome, asyncio.CancelledError)
        self.assertFalse(attempt.exists())

    async def test_scheduler_stop_waits_for_canceled_whisper_then_marks_job_canceled(self):
        with self.database.transaction() as conn:
            conn.execute(
                "UPDATE jobs SET status='queued',started_at=NULL,finished_at=NULL,"
                "cancel_requested_at=NULL,heartbeat_at=NULL,worker_id=NULL,progress=0,"
                "message='Queued' WHERE id='job-1'"
            )
        model = BlockingWhisperModel()
        transcriber = ProductionTranscriber()
        transcriber.model = model
        pipeline, _ = self.make_pipeline(AI(), transcriber=transcriber, heartbeat=0.01)
        scheduler = Scheduler(self.jobs, pipeline, 1, 1)
        await scheduler.start()
        self.assertTrue(await asyncio.to_thread(model.started.wait, 1))
        self.jobs.request_cancel("job-1", NOW)
        self.jobs.request_cancel("job-1", NOW)
        stop = asyncio.create_task(scheduler.stop(5))
        await asyncio.sleep(0.02)
        stopped_while_reading = stop.done()
        attempt = self.data / "episodes" / "episode-1" / "attempts" / "job-1"
        attempt_while_reading = attempt.exists()
        model.release.set()
        await stop

        self.assertFalse(stopped_while_reading)
        self.assertTrue(attempt_while_reading)
        self.assertTrue(model.finished.is_set())
        self.assertFalse(attempt.exists())
        self.assertEqual(self.jobs.get_required("job-1").status, "canceled")

    async def test_cancel_joins_optional_summary_write_before_returning(self):
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        original = self.episodes.replace_summary

        def replace_summary(*args, **kwargs):
            started.set()
            release.wait(timeout=2)
            original(*args, **kwargs)
            finished.set()

        self.episodes.replace_summary = replace_summary
        pipeline, _ = self.make_pipeline(AI(), heartbeat=0.01)
        task = asyncio.create_task(pipeline.execute(self.job, lambda: False))
        self.assertTrue(await asyncio.to_thread(started.wait, 1))
        task.cancel()
        await asyncio.sleep(0.02)
        done_after_first_cancel = task.done()
        task.cancel()
        await asyncio.sleep(0.02)
        done_after_second_cancel = task.done()
        write_finished_before_release = finished.is_set()
        release.set()
        outcome = (await asyncio.gather(task, return_exceptions=True))[0]
        persisted_after_return = self.episodes.get_summary("episode-1")

        self.assertFalse(done_after_first_cancel)
        self.assertFalse(done_after_second_cancel)
        self.assertFalse(write_finished_before_release)
        self.assertTrue(finished.is_set())
        self.assertIsInstance(outcome, asyncio.CancelledError)
        self.assertEqual(persisted_after_return.content, "Summary")

    async def test_summary_write_failure_warns_and_preserves_previous_summary(self):
        old = SummaryRecord("episode-1", "Old summary", 1, 1, 80, "VIDA")
        self.episodes.replace_summary("episode-1", old)

        def fail(*args, **kwargs):
            raise RuntimeError("database detail")

        self.episodes.replace_summary = fail
        pipeline, _ = self.make_pipeline(AI())
        await pipeline.execute(self.job, lambda: False)

        self.assertEqual(self.episodes.get_summary("episode-1"), old)
        self.assertEqual(self.jobs.get_required("job-1").status, "completed")
        self.assertEqual([w.stage for w in self.episodes.get("episode-1").warnings], ["summary"])

    async def test_chapter_write_failure_warns_and_preserves_previous_generated(self):
        old = ChapterRecord(
            "old-chapter", "episode-1", 0, "Old chapter", 30, None, False, "generated"
        )
        self.chapters.replace_generated("episode-1", [old])

        def fail(*args, **kwargs):
            raise RuntimeError("database detail")

        self.chapters.replace_generated = fail
        pipeline, _ = self.make_pipeline(AI())
        await pipeline.execute(self.job, lambda: False)

        self.assertEqual(self.chapters.list("episode-1"), [old])
        self.assertEqual(self.jobs.get_required("job-1").status, "completed")
        self.assertEqual([w.stage for w in self.episodes.get("episode-1").warnings], ["chapters"])

    async def test_malformed_chapters_warn_and_preserve_previous_generated(self):
        old = ChapterRecord(
            "old-chapter", "episode-1", 0, "Old chapter", 30, None, False, "generated"
        )
        self.chapters.replace_generated("episode-1", [old])

        class Runner:
            async def complete(inner_self, credentials, messages, response_format):
                return "not-json"

        processor = AIProcessor(self.credentials, runner=Runner())

        class MalformedChapterAI(AI):
            async def generate_chapters(inner_self, episode_id, transcript, duration, poster):
                return await processor.generate_chapters(
                    episode_id, transcript, duration, poster
                )

        pipeline, _ = self.make_pipeline(MalformedChapterAI())
        await pipeline.execute(self.job, lambda: False)

        self.assertEqual(self.chapters.list("episode-1"), [old])
        self.assertEqual([w.stage for w in self.episodes.get("episode-1").warnings], ["chapters"])

    async def test_explicit_empty_chapters_replace_previous_generated(self):
        old = ChapterRecord(
            "old-chapter", "episode-1", 0, "Old chapter", 30, None, False, "generated"
        )
        self.chapters.replace_generated("episode-1", [old])

        class Runner:
            async def complete(inner_self, credentials, messages, response_format):
                return '{"chapters": []}'

        processor = AIProcessor(self.credentials, runner=Runner())

        class EmptyChapterAI(AI):
            async def generate_chapters(inner_self, episode_id, transcript, duration, poster):
                return await processor.generate_chapters(
                    episode_id, transcript, duration, poster
                )

        pipeline, _ = self.make_pipeline(EmptyChapterAI())
        await pipeline.execute(self.job, lambda: False)

        self.assertEqual(self.chapters.list("episode-1"), [])
        self.assertEqual(self.episodes.get("episode-1").warnings, [])

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

    async def test_invalid_structured_segments_are_core_failures(self):
        invalid = [
            [SimpleNamespace(start_sec=True, end_sec=1, text="text")],
            [SimpleNamespace(start_sec=0, end_sec=math.nan, text="text")],
            [SimpleNamespace(start_sec=0, end_sec=math.inf, text="text")],
            [SimpleNamespace(start_sec=-1, end_sec=1, text="text")],
            [SimpleNamespace(start_sec=2, end_sec=1, text="text")],
            [SimpleNamespace(start_sec=0, end_sec=1, text="   ")],
            [SimpleNamespace(start_sec=0, end_sec=1, text="x" * 100_001)],
            [
                SimpleNamespace(start_sec=2, end_sec=3, text="later"),
                SimpleNamespace(start_sec=0, end_sec=1, text="earlier"),
            ],
            [
                SimpleNamespace(start_sec=0, end_sec=2, text="one"),
                SimpleNamespace(start_sec=1, end_sec=3, text="overlap"),
            ],
        ]
        for segments in invalid:
            with self.subTest(segments=segments):
                pipeline, _ = self.make_pipeline(
                    AI(), transcriber=Transcriber(segments=segments)
                )
                with self.assertRaises(TranscriptionFailed):
                    await pipeline.execute(self.job, lambda: False)

    async def test_adjacent_finite_segments_and_maximum_text_are_valid(self):
        segments = [
            SimpleNamespace(start_sec=0, end_sec=1, text="x" * 100_000),
            SimpleNamespace(start_sec=1, end_sec=2, text="boundary"),
        ]
        pipeline, _ = self.make_pipeline(AI(), transcriber=Transcriber(segments=segments))
        await pipeline.execute(self.job, lambda: False)
        persisted = self.episodes.get_transcript("episode-1")
        self.assertEqual([(row.ordinal, row.start_sec, row.end_sec) for row in persisted], [
            (0, 0.0, 1.0), (1, 1.0, 2.0)
        ])

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

    def _seed_old_media(self):
        media = self.data / "episodes/episode-1/artifacts/old.mp3"
        poster = self.data / "episodes/episode-1/poster/old.png"
        media.parent.mkdir(parents=True, exist_ok=True)
        poster.parent.mkdir(parents=True, exist_ok=True)
        media.write_bytes(b"old-media")
        poster.write_bytes(b"old-poster")
        with self.database.transaction() as conn:
            conn.execute(
                "UPDATE episodes SET media_path=?,media_content_type=?,poster_path=?,"
                "poster_content_type=? WHERE id='episode-1'",
                (
                    "episodes/episode-1/artifacts/old.mp3", "audio/mpeg",
                    "episodes/episode-1/poster/old.png", "image/png",
                ),
            )


if __name__ == "__main__":
    unittest.main()


def replace_credentials(profile_id, revision_id):
    return ProviderRevisionCredentials(
        revision_id, profile_id, 1, CREDENTIALS.base_url, CREDENTIALS.api_key,
        CREDENTIALS.model_id, CREDENTIALS.temperature,
    )
