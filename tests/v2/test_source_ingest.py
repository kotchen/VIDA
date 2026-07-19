from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from backend.v2.domain import EpisodeRecord
from backend.v2.jobs.models import JobCanceled
from backend.v2.jobs.source_ingest import (
    AsyncioProcessRunner,
    ProcessResult,
    SourceIngestError,
    SourceIngestor,
)


class FakeRunner:
    def __init__(self, probe: dict, *, poster: bytes = b"jpeg", returncode: int = 0):
        self.probe = probe
        self.poster = poster
        self.returncode = returncode
        self.commands: list[tuple[str, ...]] = []

    async def run(self, command, cancel_check):
        self.commands.append(tuple(command))
        if command[0] == "ffprobe":
            return ProcessResult(self.returncode, json.dumps(self.probe), "secret stderr")
        Path(command[-1]).write_bytes(self.poster)
        return ProcessResult(self.returncode, "", "secret stderr")


class FakeDownloader:
    def __init__(self, payload: bytes = b"download"):
        self.payload = payload
        self.calls = []

    async def download(self, url, directory, cancel_check, progress=None):
        self.calls.append((url, Path(directory)))
        target = Path(directory) / "download.mp4"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.payload)
        return target


class SourceIngestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data" / "v2"
        self.source = self.data / "episodes" / "e1" / "source" / "clip.mp4"
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b"video")
        self.attempt = self.data / "episodes" / "e1" / "attempts" / "j1"
        self.asset = self.root / "static" / "sipsip.png"
        self.asset.parent.mkdir()
        self.asset.write_bytes(b"png")
        self.episode = _episode(source_path="episodes/e1/source/clip.mp4")

    def tearDown(self):
        self.temp.cleanup()

    async def test_upload_is_handed_off_in_place_probed_and_given_video_poster(self):
        runner = FakeRunner({
            "format": {"duration": "12.5", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
            "streams": [{"codec_type": "video", "width": 1920, "height": 1080}],
        })
        progress = []
        prepared = await SourceIngestor(
            runner, FakeDownloader(), data_dir=self.data, audio_poster=self.asset
        ).prepare(self.episode, self.attempt, lambda: False, progress.append)

        self.assertEqual(prepared.media_path, self.source)
        self.assertEqual(prepared.duration_sec, 12.5)
        self.assertEqual(prepared.resolution, "1080p")
        self.assertEqual(prepared.media_content_type, "video/mp4")
        self.assertEqual(prepared.poster_content_type, "image/jpeg")
        self.assertEqual(prepared.poster_path.read_bytes(), b"jpeg")
        self.assertEqual([command[0] for command in runner.commands], ["ffprobe", "ffmpeg"])
        self.assertTrue(all(isinstance(command, tuple) for command in runner.commands))
        self.assertEqual(progress, [5, 12, 20])

    async def test_audio_uses_bundled_poster_and_has_no_resolution(self):
        (self.source.parent / "clip.mp3").write_bytes(b"audio")
        runner = FakeRunner({
            "format": {"duration": "3.25", "format_name": "mp3"},
            "streams": [{"codec_type": "audio"}],
        })
        prepared = await SourceIngestor(
            runner, FakeDownloader(), data_dir=self.data, audio_poster=self.asset
        ).prepare(replace(self.episode, source_path="episodes/e1/source/clip.mp3"), self.attempt, lambda: False)

        self.assertIsNone(prepared.resolution)
        self.assertEqual(prepared.media_content_type, "audio/mpeg")
        self.assertEqual(prepared.poster_content_type, "image/png")
        self.assertEqual(prepared.poster_path.read_bytes(), b"png")
        self.assertEqual([command[0] for command in runner.commands], ["ffprobe"])

    async def test_url_download_stays_in_attempt_and_is_cleaned_on_probe_failure(self):
        runner = FakeRunner({"streams": []}, returncode=1)
        downloader = FakeDownloader()
        episode = replace(self.episode, source_type="url", source_path=None, source_url="https://media.example/a")
        with self.assertRaisesRegex(SourceIngestError, "Media processing failed"):
            await SourceIngestor(
                runner, downloader, data_dir=self.data, audio_poster=self.asset
            ).prepare(episode, self.attempt, lambda: False)
        self.assertEqual(downloader.calls[0][1], self.attempt / "source")
        self.assertFalse(self.attempt.exists())

    async def test_invalid_probe_json_is_sanitized_and_attempt_is_cleaned(self):
        class InvalidRunner:
            async def run(self, command, cancel_check):
                return ProcessResult(0, "not json", "do not leak")

        with self.assertRaisesRegex(SourceIngestError, "Media metadata is invalid") as raised:
            await SourceIngestor(
                InvalidRunner(), FakeDownloader(), data_dir=self.data, audio_poster=self.asset
            ).prepare(self.episode, self.attempt, lambda: False)
        self.assertNotIn("do not leak", str(raised.exception))

    async def test_rejects_upload_path_outside_data_root(self):
        outside = self.root / "outside.mp4"
        outside.write_bytes(b"x")
        episode = replace(self.episode, source_path=str(outside))
        with self.assertRaisesRegex(SourceIngestError, "source path"):
            await SourceIngestor(
                FakeRunner({}), FakeDownloader(), data_dir=self.data, audio_poster=self.asset
            ).prepare(episode, self.attempt, lambda: False)

    async def test_upload_cancellation_is_delegated_to_running_probe_for_termination(self):
        class CancelAwareRunner:
            def __init__(self):
                self.terminated = False

            async def run(self, command, cancel_check):
                if cancel_check():
                    self.terminated = True
                    raise JobCanceled
                return ProcessResult(0, "{}", "")

        runner = CancelAwareRunner()
        with self.assertRaises(JobCanceled):
            await SourceIngestor(
                runner, FakeDownloader(), data_dir=self.data, audio_poster=self.asset
            ).prepare(self.episode, self.attempt, lambda: True)
        self.assertTrue(runner.terminated)


class FakeProcess:
    def __init__(self):
        self.returncode = None
        self.terminated = False
        self.killed = False
        self.waiting = asyncio.Event()

    async def communicate(self):
        await self.waiting.wait()
        return b"", b""

    def terminate(self):
        self.terminated = True
        self.returncode = -15
        self.waiting.set()

    def kill(self):
        self.killed = True
        self.returncode = -9
        self.waiting.set()


class ProcessRunnerCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cooperative_cancel_terminates_and_waits_for_process(self):
        process = FakeProcess()
        checks = 0

        async def factory(*args, **kwargs):
            return process

        def canceled():
            nonlocal checks
            checks += 1
            return checks > 1

        with self.assertRaises(JobCanceled):
            await AsyncioProcessRunner(factory, poll_interval_sec=0).run(("ffprobe", "x"), canceled)
        self.assertTrue(process.terminated)
        self.assertIsNotNone(process.returncode)

    async def test_task_cancel_terminates_process_and_reraises(self):
        process = FakeProcess()

        async def factory(*args, **kwargs):
            return process

        task = asyncio.create_task(
            AsyncioProcessRunner(factory, poll_interval_sec=30).run(("ffmpeg", "x"), lambda: False)
        )
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(process.terminated)


def _episode(*, source_path: str | None) -> EpisodeRecord:
    return EpisodeRecord(
        "e1", "Episode", "upload", source_path, None, "video/mp4", None, None,
        None, None, None, None, "processing", None, "English", 5, "Preparing",
        "[]", None, None, "j1", "p1", "2026-01-01T00:00:00Z",
        "2026-01-01T00:00:00Z", None,
    )
