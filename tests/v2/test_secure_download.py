from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from backend.v2.jobs.models import JobCanceled
from backend.v2.jobs.source_ingest import (
    DownloadError, PinnedHTTPClient, SecureDownloader, TimeoutPolicy,
)


class FakeResolver:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    async def resolve(self, host, port):
        self.calls.append((host, port))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return tuple(answer)


class FakeResponse:
    def __init__(self, *, status=200, headers=None, chunks=(), peer_ip="8.8.8.8"):
        self.status = status
        self.headers = headers or {"content-type": "video/mp4"}
        self.chunks = list(chunks)
        self.peer_ip = peer_ip
        self.closed = False

    async def iter_bytes(self, max_bytes=None):
        for chunk in self.chunks:
            yield chunk

    async def close(self):
        self.closed = True


class BlockingResponse(FakeResponse):
    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()

    async def iter_bytes(self, max_bytes=None):
        self.started.set()
        await asyncio.Event().wait()
        yield b"unreachable"


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def open(self, url, connect_ip):
        self.calls.append((url, connect_ip))
        return self.responses.pop(0)


class SecureDownloadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    async def test_streams_to_part_with_bound_and_atomically_renames(self):
        response = FakeResponse(headers={"content-type": "video/mp4", "content-length": "4"}, chunks=[b"ab", b"cd"])
        downloader = SecureDownloader(FakeResolver([["8.8.8.8"]]), FakeClient([response]), max_bytes=4)
        progress = []
        path = await downloader.download("https://example.com/a", self.directory, lambda: False, progress.append)
        self.assertEqual(path.name, "media.mp4")
        self.assertEqual(path.read_bytes(), b"abcd")
        self.assertFalse((self.directory / "media.mp4.part").exists())
        self.assertEqual(progress, [2, 4])
        self.assertTrue(response.closed)

    async def test_rejects_private_loopback_link_local_and_mixed_dns_answers_before_connect(self):
        for addresses in (
            ["127.0.0.1"], ["169.254.1.1"], ["224.0.0.1"], ["240.0.0.1"],
            ["::ffff:127.0.0.1"], ["8.8.8.8", "10.0.0.1"],
        ):
            with self.subTest(addresses=addresses):
                client = FakeClient([])
                with self.assertRaisesRegex(DownloadError, "not allowed"):
                    await SecureDownloader(FakeResolver([addresses]), client).download(
                        "https://example.com/a", self.directory, lambda: False
                    )
                self.assertEqual(client.calls, [])

    async def test_redirect_target_is_revalidated_and_private_redirect_rejected(self):
        redirect = FakeResponse(status=302, headers={"location": "http://localhost/private"})
        client = FakeClient([redirect])
        resolver = FakeResolver([["8.8.8.8"], ["8.8.8.8"], ["127.0.0.1"]])
        with self.assertRaises(DownloadError):
            await SecureDownloader(resolver, client).download(
                "https://example.com/a", self.directory, lambda: False
            )
        self.assertEqual(len(client.calls), 1)
        self.assertTrue(redirect.closed)

    async def test_detects_dns_rebinding_and_peer_mismatch(self):
        for resolver, response in (
            (FakeResolver([["8.8.8.8"], ["1.1.1.1"]]), FakeResponse()),
            (FakeResolver([["8.8.8.8"]]), FakeResponse(peer_ip="1.1.1.1")),
        ):
            with self.subTest(peer=response.peer_ip):
                with self.assertRaisesRegex(DownloadError, "address changed"):
                    await SecureDownloader(resolver, FakeClient([response])).download(
                        "https://example.com/a", self.directory, lambda: False
                    )
                self.assertTrue(response.closed)

    async def test_overflow_and_cancel_remove_partial_file(self):
        checks = iter((False, False, True))
        cases = (([b"abc", b"de"], 4, lambda: False), ([b"abc"], 4, lambda: next(checks)))
        for chunks, max_bytes, cancel in cases:
            with self.subTest(chunks=chunks):
                response = FakeResponse(chunks=chunks)
                downloader = SecureDownloader(FakeResolver([["8.8.8.8"]]), FakeClient([response]), max_bytes=max_bytes)
                expected = JobCanceled if chunks == [b"abc"] else DownloadError
                with self.assertRaises(expected):
                    await downloader.download("https://example.com/a", self.directory, cancel)
                self.assertFalse(any(self.directory.glob("*.part")))
                if expected is DownloadError:
                    self.assertTrue(response.closed)

    async def test_cooperative_cancel_interrupts_a_stalled_response_and_cleans_part(self):
        response = BlockingResponse()
        canceled = False
        downloader = SecureDownloader(
            FakeResolver([["8.8.8.8"]]), FakeClient([response]),
            cancel_poll_interval_sec=0.001,
        )
        task = asyncio.create_task(
            downloader.download("https://example.com/a", self.directory, lambda: canceled)
        )
        await response.started.wait()
        canceled = True
        with self.assertRaises(JobCanceled):
            await asyncio.wait_for(task, 1)
        self.assertTrue(response.closed)
        self.assertFalse(any(self.directory.glob("*.part")))

    async def test_rejects_credentials_bad_scheme_excess_redirects_and_html(self):
        cases = [
            "file:///etc/passwd",
            "https://user:pass@example.com/a",
            "https://example.com/a\r\nX-Injected: yes",
        ]
        for url in cases:
            with self.subTest(url=url), self.assertRaises(DownloadError):
                await SecureDownloader(FakeResolver([["8.8.8.8"]]), FakeClient([])).download(
                    url, self.directory, lambda: False
                )
        redirects = [FakeResponse(status=302, headers={"location": "/again"}) for _ in range(2)]
        with self.assertRaisesRegex(DownloadError, "redirect"):
            await SecureDownloader(
                FakeResolver([["8.8.8.8"]]), FakeClient(redirects), max_redirects=1
            ).download("https://example.com/a", self.directory, lambda: False)
        html = FakeResponse(headers={"content-type": "text/html"}, chunks=[b"no"])
        with self.assertRaisesRegex(DownloadError, "media type"):
            await SecureDownloader(FakeResolver([["8.8.8.8"]]), FakeClient([html])).download(
                "https://example.com/a", self.directory, lambda: False
            )
        manifest = FakeResponse(headers={"content-type": "audio/x-mpegurl"}, chunks=[b"http://127.0.0.1/"])
        with self.assertRaisesRegex(DownloadError, "media type"):
            await SecureDownloader(FakeResolver([["8.8.8.8"]]), FakeClient([manifest])).download(
                "https://example.com/a", self.directory, lambda: False
            )

    async def test_only_standard_web_ports_are_allowed(self):
        with self.assertRaisesRegex(DownloadError, "URL"):
            await SecureDownloader(FakeResolver([["8.8.8.8"]]), FakeClient([])).download(
                "https://example.com:8443/a", self.directory, lambda: False
            )


class FakeWriter:
    def __init__(self):
        self.request = bytearray()
        self.closed = False

    def write(self, data):
        self.request.extend(data)

    async def drain(self):
        pass

    def get_extra_info(self, name):
        return ("8.8.8.8", 443) if name == "peername" else None

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


class PinnedHTTPClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_connects_to_pinned_ip_with_tls_hostname_and_streams_response(self):
        reader = asyncio.StreamReader()
        reader.feed_data(
            b"HTTP/1.1 200 OK\r\nContent-Type: video/mp4\r\n"
            b"Content-Length: 4\r\n\r\nabcd"
        )
        reader.feed_eof()
        writer = FakeWriter()
        calls = []

        async def connection_factory(host, port, **kwargs):
            calls.append((host, port, kwargs))
            return reader, writer

        response = await PinnedHTTPClient(connection_factory).open(
            "https://example.com/media?q=1", "8.8.8.8"
        )
        body = b"".join([chunk async for chunk in response.iter_bytes()])
        await response.close()
        self.assertEqual(body, b"abcd")
        self.assertEqual(calls[0][0:2], ("8.8.8.8", 443))
        self.assertEqual(calls[0][2]["server_hostname"], "example.com")
        self.assertIn(b"GET /media?q=1 HTTP/1.1\r\n", writer.request)
        self.assertIn(b"Host: example.com\r\n", writer.request)
        self.assertTrue(writer.closed)

    async def test_ipv6_host_header_is_bracketed(self):
        reader = asyncio.StreamReader()
        reader.feed_data(b"HTTP/1.1 204 OK\r\nContent-Length: 0\r\n\r\n")
        reader.feed_eof()
        writer = FakeWriter()

        async def factory(*args, **kwargs):
            return reader, writer

        response = await PinnedHTTPClient(factory).open(
            "https://[2606:4700:4700::1111]/a", "2606:4700:4700::1111"
        )
        await response.close()
        self.assertIn(b"Host: [2606:4700:4700::1111]\r\n", writer.request)

    async def test_header_and_drain_timeouts_are_sanitized_and_close_socket(self):
        class StalledReader:
            async def readline(self):
                await asyncio.Event().wait()

        class StalledWriter(FakeWriter):
            def __init__(self, stall_drain):
                super().__init__()
                self.stall_drain = stall_drain

            async def drain(self):
                if self.stall_drain:
                    await asyncio.Event().wait()

        for stall_drain in (False, True):
            with self.subTest(stall_drain=stall_drain):
                writer = StalledWriter(stall_drain)

                async def factory(*args, **kwargs):
                    return StalledReader(), writer

                policy = TimeoutPolicy(headers_sec=0.001, drain_sec=0.001)
                with self.assertRaisesRegex(DownloadError, "timed out"):
                    await PinnedHTTPClient(factory, timeouts=policy).open(
                        "https://example.com/a", "8.8.8.8"
                    )
                self.assertTrue(writer.closed)

    async def test_close_timeout_is_bounded_and_sanitized(self):
        reader = asyncio.StreamReader()
        reader.feed_data(b"HTTP/1.1 204 OK\r\nContent-Length: 0\r\n\r\n")
        reader.feed_eof()

        class Writer(FakeWriter):
            async def wait_closed(self):
                await asyncio.Event().wait()

        writer = Writer()

        async def factory(*args, **kwargs):
            return reader, writer

        response = await PinnedHTTPClient(
            factory, timeouts=TimeoutPolicy(close_sec=0.001)
        ).open("https://example.com/a", "8.8.8.8")
        with self.assertRaisesRegex(DownloadError, "timed out"):
            await response.close()
