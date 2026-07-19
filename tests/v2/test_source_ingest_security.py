from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from backend.v2.jobs.models import JobCanceled
from backend.v2.jobs.source_ingest import (
    AsyncioProcessRunner,
    DownloadError,
    ProcessResult,
    SecureDownloader,
    SourceIngestError,
    SourceIngestor,
    TimeoutPolicy,
    YtDlpDownloader,
)
from tests.v2.test_source_ingest import FakeDownloader, FakeRunner, _episode
from tests.v2.test_secure_download import FakeClient, FakeResolver, FakeResponse


class SourceBoundarySecurityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data/v2"
        self.source_dir = self.data / "episodes/e1/source"
        self.source_dir.mkdir(parents=True)
        self.attempt = self.data / "episodes/e1/attempts/j1"
        self.poster = self.root / "static/sipsip.png"
        self.poster.parent.mkdir()
        self.poster.write_bytes(b"png")

    def tearDown(self):
        self.temp.cleanup()

    async def test_manifest_sources_are_rejected_before_any_media_tool_runs(self):
        for suffix, body in (
            (".m3u", "file:///etc/passwd"),
            (".m3u8", "http://127.0.0.1/private"),
        ):
            with self.subTest(suffix=suffix):
                path = self.source_dir / f"attack{suffix}"
                path.write_text(body)
                runner = FakeRunner({})
                with self.assertRaisesRegex(SourceIngestError, "Unsupported media"):
                    await SourceIngestor(
                        runner, FakeDownloader(), data_dir=self.data,
                        audio_poster=self.poster,
                    ).prepare(_episode(source_path=path.relative_to(self.data).as_posix()), self.attempt, lambda: False)
                self.assertEqual(runner.commands, [])

    async def test_attempt_directory_must_match_episode_and_current_job(self):
        source = self.source_dir / "clip.mp3"
        source.write_bytes(b"audio")
        ingestor = SourceIngestor(
            FakeRunner({}), FakeDownloader(), data_dir=self.data,
            audio_poster=self.poster,
        )
        for attempt in (
            self.data / "episodes/other/attempts/j1",
            self.data / "episodes/e1/attempts/other",
        ):
            with self.subTest(attempt=attempt), self.assertRaisesRegex(SourceIngestError, "attempt"):
                await ingestor.prepare(
                    _episode(source_path=source.relative_to(self.data).as_posix()),
                    attempt, lambda: False,
                )

    async def test_probe_and_poster_force_local_protocol_and_expected_demuxer(self):
        source = self.source_dir / "clip.mp4"
        source.write_bytes(b"video")
        runner = FakeRunner({
            "format": {"duration": "1", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
            "streams": [{"codec_type": "video", "width": 10, "height": 10}],
        })
        await SourceIngestor(
            runner, FakeDownloader(), data_dir=self.data, audio_poster=self.poster,
        ).prepare(_episode(source_path=source.relative_to(self.data).as_posix()), self.attempt, lambda: False)
        for command in runner.commands:
            joined = " ".join(command)
            self.assertIn("-protocol_whitelist file,pipe", joined)
            self.assertIn("-f mov", joined)

    async def test_probe_rejects_manifest_format_and_attached_picture_is_not_video(self):
        source = self.source_dir / "clip.mp3"
        source.write_bytes(b"audio")
        runner = FakeRunner({
            "format": {"duration": "1", "format_name": "hls"},
            "streams": [{"codec_type": "video", "disposition": {"attached_pic": 1}, "width": 10, "height": 10}, {"codec_type": "audio"}],
        })
        with self.assertRaisesRegex(SourceIngestError, "format"):
            await SourceIngestor(
                runner, FakeDownloader(), data_dir=self.data, audio_poster=self.poster,
            ).prepare(_episode(source_path=source.relative_to(self.data).as_posix()), self.attempt, lambda: False)
        self.assertEqual([cmd[0] for cmd in runner.commands], ["ffprobe"])

    async def test_probe_rejects_non_media_format_even_with_mp4_extension(self):
        source = self.source_dir / "fake.mp4"
        source.write_bytes(b"not media")
        runner = FakeRunner({
            "format": {"duration": "1", "format_name": "image2"},
            "streams": [{"codec_type": "video", "width": 10, "height": 10}],
        })
        with self.assertRaisesRegex(SourceIngestError, "format"):
            await SourceIngestor(
                runner, FakeDownloader(), data_dir=self.data, audio_poster=self.poster,
            ).prepare(_episode(source_path=source.relative_to(self.data).as_posix()), self.attempt, lambda: False)

    async def test_empty_generated_poster_is_rejected(self):
        source = self.source_dir / "clip.mp4"
        source.write_bytes(b"video")
        runner = FakeRunner({
            "format": {"duration": "1", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
            "streams": [{"codec_type": "video", "width": 10, "height": 10}],
        }, poster=b"")
        with self.assertRaisesRegex(SourceIngestError, "poster"):
            await SourceIngestor(
                runner, FakeDownloader(), data_dir=self.data, audio_poster=self.poster,
            ).prepare(_episode(source_path=source.relative_to(self.data).as_posix()), self.attempt, lambda: False)

    async def test_non_jpeg_generated_poster_is_rejected(self):
        source = self.source_dir / "clip.mp4"
        source.write_bytes(b"video")
        runner = FakeRunner({
            "format": {"duration": "1", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
            "streams": [{"codec_type": "video", "width": 10, "height": 10}],
        }, poster=b"not-a-jpeg")
        with self.assertRaisesRegex(SourceIngestError, "poster"):
            await SourceIngestor(
                runner, FakeDownloader(), data_dir=self.data, audio_poster=self.poster,
            ).prepare(_episode(source_path=source.relative_to(self.data).as_posix()), self.attempt, lambda: False)


class YtDlpAcquisitionTests(unittest.IsolatedAsyncioTestCase):
    async def test_supported_pages_resolve_once_then_use_secure_downloader(self):
        class Runner:
            def __init__(self):
                self.commands = []

            async def run(self, command, cancel_check, timeout_sec=None):
                self.commands.append(tuple(command))
                return ProcessResult(0, json.dumps({"url": "https://cdn.example/media.mp4"}), "")

        class Policy:
            async def validate_url(self, url, cancel_check):
                return None

        class Media:
            def __init__(self):
                self.calls = []

            async def download(self, url, directory, cancel_check, progress=None):
                self.calls.append(url)
                target = Path(directory) / "media.mp4"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"x")
                return target

        for url in (
            "https://www.youtube.com/watch?v=x", "https://www.bilibili.com/video/x",
            "https://www.tiktok.com/@a/video/1", "https://soundcloud.com/a/b",
        ):
            with self.subTest(url=url):
                runner, media = Runner(), Media()
                path = await YtDlpDownloader(runner, media, Policy(), max_bytes=123).download(
                    url, Path(tempfile.mkdtemp()), lambda: False
                )
                command = " ".join(runner.commands[0])
                self.assertIn("--no-playlist", command)
                self.assertIn("--no-config", command)
                self.assertIn("--max-filesize 123", command)
                self.assertIn("--skip-download", command)
                self.assertEqual(media.calls, ["https://cdn.example/media.mp4"])
                self.assertTrue(path.exists())

    async def test_unsupported_page_and_extractor_failure_are_sanitized(self):
        class Runner:
            async def run(self, command, cancel_check, timeout_sec=None):
                return ProcessResult(1, "", "secret")

        class Policy:
            async def validate_url(self, url, cancel_check):
                pass

        downloader = YtDlpDownloader(Runner(), FakeDownloader(), Policy())
        with self.assertRaises(DownloadError):
            await downloader.download("https://unknown.example/page", Path(tempfile.mkdtemp()), lambda: False)
        with self.assertRaisesRegex(DownloadError, "resolve") as raised:
            await downloader.download("https://youtube.com/watch?v=x", Path(tempfile.mkdtemp()), lambda: False)
        self.assertNotIn("secret", str(raised.exception))


class TimeoutSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_stalled_process_creation_obeys_runtime_timeout(self):
        async def factory(*args, **kwargs):
            await asyncio.Event().wait()

        with self.assertRaisesRegex(SourceIngestError, "timed out"):
            await AsyncioProcessRunner(
                factory, runtime_timeout_sec=0.001
            ).run(("ffprobe", "x"), lambda: False)

    async def test_stalled_process_is_terminated_at_runtime_deadline(self):
        from tests.v2.test_source_ingest import FakeProcess
        process = FakeProcess()

        async def factory(*args, **kwargs):
            return process

        with self.assertRaisesRegex(SourceIngestError, "timed out"):
            await asyncio.wait_for(
                AsyncioProcessRunner(
                    factory, poll_interval_sec=30, runtime_timeout_sec=0.001
                ).run(("ffprobe", "x"), lambda: False),
                0.2,
            )
        self.assertTrue(process.terminated)

    async def test_stalled_dns_and_connect_are_bounded_and_sanitized(self):
        class StallResolver:
            async def resolve(self, host, port):
                await asyncio.Event().wait()

        policy = TimeoutPolicy(total_sec=0.05, dns_sec=0.001, connect_sec=0.001)
        with self.assertRaisesRegex(DownloadError, "timed out"):
            await SecureDownloader(StallResolver(), FakeClient([]), timeouts=policy).download(
                "https://example.com/a", Path(tempfile.mkdtemp()), lambda: False
            )
        with self.assertRaisesRegex(DownloadError, "timed out"):
            await asyncio.wait_for(
                SecureDownloader(
                    StallResolver(), FakeClient([]),
                    timeouts=TimeoutPolicy(total_sec=0.001),
                ).download("https://example.com/a", Path(tempfile.mkdtemp()), lambda: False),
                0.2,
            )

        class StallClient:
            async def open(self, url, connect_ip):
                await asyncio.Event().wait()

        with self.assertRaisesRegex(DownloadError, "timed out"):
            await SecureDownloader(
                FakeResolver([["8.8.8.8"]]), StallClient(), timeouts=policy
            ).download("https://example.com/a", Path(tempfile.mkdtemp()), lambda: False)

    async def test_stalled_body_hits_idle_timeout_and_closes_response(self):
        from tests.v2.test_secure_download import BlockingResponse
        response = BlockingResponse()
        policy = TimeoutPolicy(total_sec=1, body_idle_sec=0.001)
        with self.assertRaisesRegex(DownloadError, "timed out"):
            await SecureDownloader(
                FakeResolver([["8.8.8.8"]]), FakeClient([response]), timeouts=policy
            ).download("https://example.com/a", Path(tempfile.mkdtemp()), lambda: False)
        self.assertTrue(response.closed)


class ChunkBoundTests(unittest.IsolatedAsyncioTestCase):
    async def test_huge_declared_chunk_is_read_in_bounded_pieces(self):
        class RecordingReader(asyncio.StreamReader):
            def __init__(self):
                super().__init__()
                self.requests = []

            async def readexactly(self, n):
                self.requests.append(n)
                raise asyncio.IncompleteReadError(b"", n)

        reader = RecordingReader()
        reader.feed_data(b"HTTP/1.1 200 OK\r\nContent-Type: video/mp4\r\nTransfer-Encoding: chunked\r\n\r\n100000000\r\n")
        writer = type("Writer", (), {
            "write": lambda self, data: None, "drain": lambda self: asyncio.sleep(0),
            "get_extra_info": lambda self, name: ("8.8.8.8", 443),
            "close": lambda self: None, "wait_closed": lambda self: asyncio.sleep(0),
        })()

        async def factory(*args, **kwargs):
            return reader, writer

        response = await __import__("backend.v2.jobs.source_ingest", fromlist=["PinnedHTTPClient"]).PinnedHTTPClient(factory).open(
            "https://example.com/a", "8.8.8.8"
        )
        with self.assertRaises(DownloadError):
            _ = [chunk async for chunk in response.iter_bytes(max_bytes=4)]
        self.assertEqual(reader.requests, [])
