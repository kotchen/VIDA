from __future__ import annotations

import asyncio
import json
import base64
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
    SSRFProxy,
)
import backend.v2.jobs.source_ingest as source_ingest
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

    async def test_attempt_symlink_cannot_delete_cross_episode_victim(self):
        source = self.source_dir / "clip.mp3"
        source.write_bytes(b"audio")
        victim = self.data / "episodes/e2/attempts/victim"
        victim.mkdir(parents=True)
        sentinel = victim / "keep.bin"
        sentinel.write_bytes(b"keep")
        self.attempt.parent.mkdir(parents=True)
        try:
            self.attempt.symlink_to(victim, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlink unavailable: {exc}")
        with self.assertRaisesRegex(SourceIngestError, "attempt"):
            await SourceIngestor(
                FakeRunner({}), FakeDownloader(), data_dir=self.data,
                audio_poster=self.poster,
            ).prepare(_episode(source_path=source.relative_to(self.data).as_posix()), self.attempt, lambda: False)
        self.assertEqual(sentinel.read_bytes(), b"keep")

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

    async def test_fake_soi_eoi_without_jpeg_structure_is_rejected(self):
        source = self.source_dir / "clip.mp4"
        source.write_bytes(b"video")
        runner = FakeRunner({
            "format": {"duration": "1", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
            "streams": [{"codec_type": "video", "width": 10, "height": 10}],
        }, poster=b"\xff\xd8\xff\xd9")
        with self.assertRaisesRegex(SourceIngestError, "poster"):
            await SourceIngestor(
                runner, FakeDownloader(), data_dir=self.data, audio_poster=self.poster,
            ).prepare(_episode(source_path=source.relative_to(self.data).as_posix()), self.attempt, lambda: False)


class YtDlpAcquisitionTests(unittest.IsolatedAsyncioTestCase):
    async def test_supported_pages_resolve_once_then_use_secure_downloader(self):
        class Runner:
            def __init__(self):
                self.commands = []

            async def run(self, command, cancel_check, timeout_sec=None, env=None):
                self.commands.append(tuple(command))
                self.env = env
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

        class Proxy:
            proxy_url = "http://secret@127.0.0.1:32123"

            def __init__(self):
                self.entered = self.exited = False

            async def __aenter__(self):
                self.entered = True
                return self

            async def __aexit__(self, *args):
                self.exited = True

        for url in (
            "https://www.youtube.com/watch?v=x", "https://www.bilibili.com/video/x",
            "https://www.tiktok.com/@a/video/1", "https://soundcloud.com/a/b",
        ):
            with self.subTest(url=url):
                runner, media, proxy = Runner(), Media(), Proxy()
                path = await YtDlpDownloader(
                    runner, media, Policy(), max_bytes=123,
                    proxy_factory=lambda cancel_check: proxy,
                ).download(
                    url, Path(tempfile.mkdtemp()), lambda: False
                )
                command = " ".join(runner.commands[0])
                self.assertIn("--no-playlist", command)
                self.assertIn("--no-config", command)
                self.assertIn("--max-filesize 123", command)
                self.assertIn("--skip-download", command)
                self.assertIn("--no-plugin-dirs", command)
                self.assertIn("--proxy http://secret@127.0.0.1:32123", command)
                self.assertEqual(media.calls, ["https://cdn.example/media.mp4"])
                self.assertTrue(proxy.entered and proxy.exited)
                self.assertFalse(any(key.lower() in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"} for key in runner.env))
                self.assertEqual(runner.env.get("YTDLP_NO_PLUGINS"), "1")
                self.assertTrue(path.exists())

    async def test_unsupported_page_and_extractor_failure_are_sanitized(self):
        class Runner:
            async def run(self, command, cancel_check, timeout_sec=None, env=None):
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


class ControlledProxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_and_rebinding_targets_are_rejected_without_fallback(self):
        class Connector:
            def __init__(self):
                self.calls = []

            async def __call__(self, host, port):
                self.calls.append((host, port))
                writer = type("Writer", (), {
                    "get_extra_info": lambda self, name: (host, port),
                    "close": lambda self: None,
                    "wait_closed": lambda self: asyncio.sleep(0),
                })()
                return object(), writer

        connector = Connector()
        with self.assertRaises(DownloadError):
            await SSRFProxy(
                resolver=FakeResolver([["127.0.0.1"]]), connector=connector
            ).connect_target("example.com", 443, lambda: False)
        self.assertEqual(connector.calls, [])

        connector = Connector()
        with self.assertRaisesRegex(DownloadError, "changed"):
            await SSRFProxy(
                resolver=FakeResolver([["8.8.8.8"], ["1.1.1.1"]]),
                connector=connector,
            ).connect_target("example.com", 443, lambda: False)
        self.assertEqual(connector.calls, [("8.8.8.8", 443)])

    async def test_writer_closes_when_recheck_times_out_or_is_canceled(self):
        class Writer:
            def __init__(self):
                self.closed = False

            def get_extra_info(self, name):
                return ("8.8.8.8", 443)

            def close(self):
                self.closed = True

            async def wait_closed(self):
                pass

        class Resolver:
            def __init__(self):
                self.calls = 0
                self.rechecking = asyncio.Event()

            async def resolve(self, host, port):
                self.calls += 1
                if self.calls == 1:
                    return ("8.8.8.8",)
                self.rechecking.set()
                await asyncio.Event().wait()

        for canceled in (False, True):
            with self.subTest(canceled=canceled):
                writer = Writer()

                async def connector(host, port):
                    return object(), writer

                flag = False
                proxy = SSRFProxy(
                    resolver=Resolver(), connector=connector,
                    timeouts=TimeoutPolicy(recheck_sec=0.001),
                )
                task = asyncio.create_task(
                    proxy.connect_target("example.com", 443, lambda: flag)
                )
                await proxy._resolver.rechecking.wait()
                if canceled:
                    flag = True
                    with self.assertRaises(JobCanceled):
                        await task
                else:
                    with self.assertRaises(asyncio.TimeoutError):
                        await task
                self.assertTrue(writer.closed)

    def test_connect_authority_and_headers_are_strict(self):
        valid_token = "token"
        auth = base64.b64encode(b"token:").decode()
        for authority, expected in (
            ("example.com:443", ("example.com", 443)),
            ("[2606:4700:4700::1111]:443", ("2606:4700:4700::1111", 443)),
        ):
            request = (
                f"CONNECT {authority} HTTP/1.1\r\n"
                f"Proxy-Authorization: Basic {auth}\r\nHost: {authority}\r\n\r\n"
            ).encode()
            self.assertEqual(source_ingest._parse_connect_request(request, valid_token), expected)
        malformed = (
            "example.com", "example.com:8443", "example.com:443/path",
            "example.com:443?x", "user@example.com:443", "2001:db8::1:443",
            "[2606:4700::1]", "[2606:4700::1]:443/x", "example.com:x",
            "example.com:443\x00",
        )
        for authority in malformed:
            with self.subTest(authority=authority), self.assertRaises(DownloadError):
                source_ingest._parse_connect_request(
                    f"CONNECT {authority} HTTP/1.1\r\nProxy-Authorization: Basic {auth}\r\n\r\n".encode(),
                    valid_token,
                )
        malformed_requests = (
            f"GET example.com:443 HTTP/1.1\r\nProxy-Authorization: Basic {auth}\r\n\r\n",
            f"CONNECT example.com:443 HTTP/2\r\nProxy-Authorization: Basic {auth}\r\n\r\n",
            f"CONNECT  example.com:443 HTTP/1.1\r\nProxy-Authorization: Basic {auth}\r\n\r\n",
            f"CONNECT example.com:443 HTTP/1.1\r\nBad Header: x\r\nProxy-Authorization: Basic {auth}\r\n\r\n",
            f"CONNECT example.com:443 HTTP/1.1\r\nBroken\r\nProxy-Authorization: Basic {auth}\r\n\r\n",
            f"CONNECT example.com:443 HTTP/1.1\r\nProxy-Authorization: Basic {auth}\r\nProxy-Authorization: Basic {auth}\r\n\r\n",
        )
        for request in malformed_requests:
            with self.subTest(request=request), self.assertRaises(DownloadError):
                source_ingest._parse_connect_request(request.encode(), valid_token)

    async def test_real_loopback_rejects_auth_and_shutdown_joins_active_handler(self):
        class UpstreamWriter:
            def __init__(self):
                self.closed = False

            def get_extra_info(self, name):
                return ("8.8.8.8", 443)

            def write(self, data):
                pass

            async def drain(self):
                pass

            def close(self):
                self.closed = True

            async def wait_closed(self):
                pass

        upstream_reader = asyncio.StreamReader()
        upstream = UpstreamWriter()

        async def connector(host, port):
            return upstream_reader, upstream

        proxy = SSRFProxy(
            resolver=FakeResolver([["8.8.8.8"]]), connector=connector
        )
        await proxy.__aenter__()
        parsed = __import__("urllib.parse", fromlist=["urlsplit"]).urlsplit(proxy.proxy_url)
        reader, writer = await asyncio.open_connection("127.0.0.1", parsed.port)
        writer.write(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
        await writer.drain()
        self.assertIn(
            b"403", await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 1)
        )
        writer.close()
        await asyncio.wait_for(writer.wait_closed(), 1)

        token = parsed.username
        auth = base64.b64encode(f"{token}:".encode()).decode()
        reader, writer = await asyncio.open_connection("127.0.0.1", parsed.port)
        writer.write(
            f"CONNECT example.com:443 HTTP/1.1\r\nProxy-Authorization: Basic {auth}\r\n\r\n".encode()
        )
        await writer.drain()
        self.assertIn(
            b"200", await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 1)
        )
        await asyncio.wait_for(proxy.__aexit__(None, None, None), 1)
        self.assertEqual(proxy._handlers, set())
        self.assertTrue(upstream.closed)
        writer.close()
        await asyncio.wait_for(writer.wait_closed(), 1)


class TimeoutSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_stuck_post_kill_join_is_bounded_and_leaves_no_capture_task(self):
        finished = asyncio.Event()

        class Process:
            returncode = None

            async def communicate(self):
                try:
                    await asyncio.Event().wait()
                finally:
                    finished.set()

            def terminate(self):
                pass

            def kill(self):
                pass

        async def factory(*args, **kwargs):
            return Process()

        with self.assertRaisesRegex(SourceIngestError, "cleanup timed out"):
            await AsyncioProcessRunner(
                factory, runtime_timeout_sec=0.001,
                terminate_timeout_sec=0.001, kill_timeout_sec=0.001,
            ).run(("ffprobe", "x"), lambda: False)
        self.assertTrue(finished.is_set())
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
