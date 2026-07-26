from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import math
import mimetypes
import os
import re
import secrets
import shutil
import socket
import ssl
import yt_dlp
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import urljoin, urlsplit

from ..domain import EpisodeRecord
from .blocking import run_owned_to_thread
from .models import JobCanceled


CancelCheck = Callable[[], bool]
ProgressCallback = Callable[[int], None]


@dataclass(frozen=True)
class TimeoutPolicy:
    total_sec: float = 300.0
    dns_sec: float = 5.0
    connect_sec: float = 15.0
    headers_sec: float = 15.0
    body_idle_sec: float = 30.0
    drain_sec: float = 5.0
    close_sec: float = 5.0
    recheck_sec: float = 5.0
    probe_sec: float = 30.0
    poster_sec: float = 60.0
    extract_sec: float = 120.0

    def __post_init__(self) -> None:
        if any(value <= 0 for value in self.__dict__.values()):
            raise ValueError("timeouts must be positive")


_SAFE_INPUTS = {
    ".mp3": ("mp3", "audio/mpeg"),
    ".mp4": ("mov", "video/mp4"),
    ".m4a": ("mov", "audio/mp4"),
    ".mov": ("mov", "video/quicktime"),
    ".wav": ("wav", "audio/wav"),
    ".webm": ("matroska,webm", "video/webm"),
    ".mkv": ("matroska,webm", "video/x-matroska"),
    ".ogg": ("ogg", "audio/ogg"),
    ".oga": ("ogg", "audio/ogg"),
    ".ogv": ("ogg", "video/ogg"),
    ".flac": ("flac", "audio/flac"),
}
_MANIFEST_FORMATS = frozenset({"hls", "applehttp", "dash", "smoothstreaming", "m3u", "m3u8"})
_ALLOWED_PROBE_FORMATS = {
    ".mp3": {"mp3"},
    ".mp4": {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"},
    ".m4a": {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"},
    ".mov": {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"},
    ".wav": {"wav"},
    ".webm": {"matroska", "webm"},
    ".mkv": {"matroska", "webm"},
    ".ogg": {"ogg"}, ".oga": {"ogg"}, ".ogv": {"ogg"},
    ".flac": {"flac"},
}


class SourceIngestError(RuntimeError):
    """A sanitized media-ingestion failure safe for a job error message."""


class DownloadError(SourceIngestError):
    pass


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class PreparedSource:
    media_path: Path
    duration_sec: float
    resolution: str | None
    media_content_type: str
    poster_path: Path
    poster_content_type: str
    source_title: str | None = None


@dataclass(frozen=True)
class DownloadedMedia:
    path: Path
    title: str | None = None


class ProcessRunner(Protocol):
    def run(
        self, command: Sequence[str], cancel_check: CancelCheck,
        timeout_sec: float | None = None,
        env: Mapping[str, str] | None = None,
    ) -> Awaitable[ProcessResult]: ...


class Downloader(Protocol):
    def download(
        self,
        url: str,
        directory: Path,
        cancel_check: CancelCheck,
        progress: ProgressCallback | None = None,
    ) -> Awaitable[Path]: ...


@runtime_checkable
class MetadataDownloader(Protocol):
    """Optional downloader extension that also reports the remote page title."""

    def download_with_metadata(
        self,
        url: str,
        directory: Path,
        cancel_check: CancelCheck,
        progress: ProgressCallback | None = None,
    ) -> Awaitable[DownloadedMedia]: ...


class Resolver(Protocol):
    def resolve(self, host: str, port: int) -> Awaitable[Sequence[str]]: ...


class HTTPResponse(Protocol):
    status: int
    headers: dict[str, str]
    peer_ip: str

    def iter_bytes(self, max_bytes: int | None = None) -> AsyncIterator[bytes]: ...
    def close(self) -> Awaitable[None]: ...


class HTTPClient(Protocol):
    def open(self, url: str, connect_ip: str) -> Awaitable[HTTPResponse]: ...


class AsyncioProcessRunner:
    def __init__(
        self,
        process_factory=asyncio.create_subprocess_exec,
        *,
        poll_interval_sec: float = 0.1,
        terminate_timeout_sec: float = 5.0,
        kill_timeout_sec: float = 5.0,
        runtime_timeout_sec: float = 120.0,
        max_output_bytes: int = 1024 * 1024,
    ):
        self._factory = process_factory
        self._poll_interval = max(0, poll_interval_sec)
        self._terminate_timeout = max(0, terminate_timeout_sec)
        self._kill_timeout = max(0, kill_timeout_sec)
        self._runtime_timeout = runtime_timeout_sec
        self._max_output_bytes = max_output_bytes

    async def run(
        self, command: Sequence[str], cancel_check: CancelCheck,
        timeout_sec: float | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ProcessResult:
        if not command or not all(isinstance(value, str) for value in command):
            raise ValueError("command must contain string arguments")
        runtime_timeout = timeout_sec or self._runtime_timeout
        started = asyncio.get_running_loop().time()
        creation = asyncio.create_task(
            self._factory(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=min(self._max_output_bytes, 64 * 1024),
                env=None if env is None else dict(env),
            )
        )
        try:
            process = await _await_phase(
                creation,
                cancel_check,
                runtime_timeout,
            )
        except asyncio.TimeoutError:
            raise SourceIngestError("Media tool timed out") from None
        except asyncio.CancelledError:
            if creation.done() and not creation.cancelled():
                process = creation.result()
                communication = asyncio.create_task(
                    _bounded_communicate(process, self._max_output_bytes)
                )
                await self._terminate(process, communication)
            raise
        communication = asyncio.create_task(
            _bounded_communicate(process, self._max_output_bytes)
        )
        deadline = started + runtime_timeout
        try:
            while not communication.done():
                if cancel_check():
                    await self._terminate(process, communication)
                    raise JobCanceled
                if asyncio.get_running_loop().time() >= deadline:
                    await self._terminate(process, communication)
                    raise SourceIngestError("Media tool timed out")
                await asyncio.wait(
                    (communication,), timeout=min(
                        self._poll_interval,
                        max(0, deadline - asyncio.get_running_loop().time()),
                    ),
                    return_when=asyncio.FIRST_COMPLETED,
                )
            stdout, stderr = communication.result()
        except asyncio.CancelledError:
            try:
                await self._terminate(process, communication)
            except SourceIngestError:
                pass
            raise
        return ProcessResult(
            int(process.returncode),
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )

    async def _terminate(self, process, communication: asyncio.Task) -> None:
        if process.returncode is None:
            process.terminate()
        try:
            await asyncio.wait_for(asyncio.shield(communication), self._terminate_timeout)
        except asyncio.TimeoutError:
            if process.returncode is None:
                process.kill()
            try:
                await asyncio.wait_for(
                    asyncio.shield(communication), self._kill_timeout
                )
            except asyncio.TimeoutError:
                communication.cancel()
                await asyncio.gather(communication, return_exceptions=True)
                raise SourceIngestError("Media tool cleanup timed out") from None


class SourceIngestor:
    def __init__(
        self,
        runner: ProcessRunner,
        downloader: Downloader,
        *,
        data_dir: Path | None = None,
        audio_poster: Path | None = None,
        timeouts: TimeoutPolicy | None = None,
    ):
        self._runner = runner
        self._downloader = downloader
        self._data_dir = Path(data_dir or Path("data") / "v2").resolve()
        self._audio_poster = Path(audio_poster or Path("static") / "sipsip.png").resolve()
        self._timeouts = timeouts or TimeoutPolicy()

    async def prepare(
        self,
        episode: EpisodeRecord,
        attempt_dir: Path,
        cancel_check: CancelCheck,
        progress: ProgressCallback | None = None,
    ) -> PreparedSource:
        attempt = Path(attempt_dir).absolute()
        expected_attempt = (
            self._data_dir / "episodes" / episode.id / "attempts"
            / (episode.current_job_id or "")
        ).absolute()
        if (
            not episode.current_job_id
            or attempt != expected_attempt
            or _has_symlink_component(attempt, self._data_dir)
        ):
            raise SourceIngestError("Invalid attempt path")
        self._bounded_path(attempt, "attempt path")
        downloaded = episode.source_type == "url"
        source_title: str | None = None
        try:
            _report(progress, 5)
            if downloaded:
                _raise_if_canceled(cancel_check)
                if not episode.source_url:
                    raise SourceIngestError("URL source is missing")
                if isinstance(self._downloader, MetadataDownloader):
                    fetched = await self._downloader.download_with_metadata(
                        episode.source_url, attempt / "source", cancel_check
                    )
                    media = fetched.path
                    source_title = fetched.title
                else:
                    media = await self._downloader.download(
                        episode.source_url, attempt / "source", cancel_check
                    )
                media = self._bounded_path(media, "download path")
            elif episode.source_type == "upload":
                if not episode.source_path:
                    raise SourceIngestError("Upload source path is missing")
                raw = Path(episode.source_path)
                if raw.is_absolute():
                    raise SourceIngestError("Upload source path is invalid")
                media = self._bounded_path(self._data_dir / raw, "source path")
                if not media.is_file() or media.is_symlink():
                    raise SourceIngestError("Upload source path is invalid")
            else:
                raise SourceIngestError("Source type is invalid")
            if downloaded:
                _raise_if_canceled(cancel_check)
            suffix = media.suffix.lower()
            if suffix not in _SAFE_INPUTS:
                raise SourceIngestError("Unsupported media format")
            demuxer = _SAFE_INPUTS[suffix][0]
            _report(progress, 12)
            result = await self._runner.run(
                (
                    "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
                    "-f", demuxer, "-print_format", "json",
                    "-show_format", "-show_streams", str(media),
                ),
                cancel_check,
                timeout_sec=self._timeouts.probe_sec,
            )
            if result.returncode != 0:
                raise SourceIngestError("Media processing failed")
            metadata = _probe_metadata(result.stdout, media)
            poster = attempt / ("poster.jpg" if metadata[2] else "poster.png")
            await run_owned_to_thread(poster.parent.mkdir, parents=True, exist_ok=True)
            if metadata[2]:
                result = await self._runner.run(
                    (
                        "ffmpeg", "-nostdin", "-y", "-protocol_whitelist", "file,pipe",
                        "-f", demuxer, "-ss", "0", "-i", str(media),
                        "-frames:v", "1", "-q:v", "2", str(poster),
                    ),
                    cancel_check,
                    timeout_sec=self._timeouts.poster_sec,
                )
                if (
                    result.returncode != 0
                    or not await run_owned_to_thread(_valid_jpeg, poster)
                ):
                    raise SourceIngestError("Media poster is invalid")
                poster_type = "image/jpeg"
            else:
                if not self._audio_poster.is_file():
                    raise SourceIngestError("Audio poster is unavailable")
                await run_owned_to_thread(shutil.copyfile, self._audio_poster, poster)
                poster_type = "image/png"
            _raise_if_canceled(cancel_check)
            _report(progress, 20)
            return PreparedSource(
                media, metadata[0], metadata[1], metadata[3], poster, poster_type,
                source_title,
            )
        except (JobCanceled, asyncio.CancelledError, SourceIngestError):
            await _remove_attempt(attempt, expected_attempt, self._data_dir)
            raise
        except Exception:
            await _remove_attempt(attempt, expected_attempt, self._data_dir)
            raise SourceIngestError("Media processing failed") from None

    def _bounded_path(self, path: Path, label: str) -> Path:
        resolved = Path(path).resolve()
        if resolved == self._data_dir or self._data_dir not in resolved.parents:
            raise SourceIngestError(f"Invalid {label}")
        return resolved


class SystemResolver:
    async def resolve(self, host: str, port: int) -> Sequence[str]:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )
        return tuple(dict.fromkeys(info[4][0] for info in infos))


class PinnedHTTPClient:
    """Small HTTP/1.1 client that connects to the already-vetted IP address."""

    def __init__(
        self,
        connection_factory=asyncio.open_connection,
        *,
        header_limit_bytes: int = 32 * 1024,
        timeouts: TimeoutPolicy | None = None,
    ):
        self._connection_factory = connection_factory
        self._header_limit = header_limit_bytes
        self._timeouts = timeouts or TimeoutPolicy()

    async def open(self, url: str, connect_ip: str) -> HTTPResponse:
        parsed, port = _validated_remote_url(url)
        tls = parsed.scheme == "https"
        kwargs = {}
        if tls:
            kwargs = {"ssl": ssl.create_default_context(), "server_hostname": parsed.hostname}
        try:
            reader, writer = await asyncio.wait_for(
                self._connection_factory(connect_ip, port, **kwargs),
                self._timeouts.connect_sec,
            )
        except asyncio.TimeoutError:
            raise DownloadError("Remote connection timed out") from None
        try:
            default_port = 443 if tls else 80
            host_name = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
            host = host_name if port == default_port else f"{host_name}:{port}"
            target = parsed.path or "/"
            if parsed.query:
                target += "?" + parsed.query
            request = (
                f"GET {target} HTTP/1.1\r\nHost: {host}\r\nAccept: audio/*, video/*\r\n"
                "Accept-Encoding: identity\r\nConnection: close\r\nUser-Agent: VIDA/2\r\n\r\n"
            ).encode("ascii")
            writer.write(request)
            await asyncio.wait_for(writer.drain(), self._timeouts.drain_sec)
            status_line = await asyncio.wait_for(
                _read_http_line(reader, self._header_limit), self._timeouts.headers_sec
            )
            pieces = status_line.decode("iso-8859-1").rstrip("\r\n").split(" ", 2)
            if len(pieces) < 2 or not pieces[0].startswith("HTTP/"):
                raise DownloadError("Remote server response is invalid")
            status = int(pieces[1])
            headers: dict[str, str] = {}
            consumed = len(status_line)
            while True:
                line = await asyncio.wait_for(
                    _read_http_line(reader, self._header_limit - consumed),
                    self._timeouts.headers_sec,
                )
                consumed += len(line)
                if line == b"\r\n":
                    break
                if b":" not in line:
                    raise DownloadError("Remote server response is invalid")
                name, value = line.split(b":", 1)
                key = name.decode("ascii").strip().lower()
                if not key or key in headers:
                    raise DownloadError("Remote server response is invalid")
                headers[key] = value.decode("iso-8859-1").strip()
            peer = writer.get_extra_info("peername")
            if not peer:
                raise DownloadError("Remote address changed")
            return _PinnedHTTPResponse(
                reader, writer, status, headers, str(peer[0]), self._timeouts.close_sec
            )
        except BaseException as exc:
            writer.close()
            await _bounded_writer_close(writer, self._timeouts.close_sec)
            if isinstance(exc, asyncio.TimeoutError):
                raise DownloadError("Remote request timed out") from None
            raise


class _PinnedHTTPResponse:
    def __init__(self, reader, writer, status, headers, peer_ip, close_timeout):
        self._reader = reader
        self._writer = writer
        self.status = status
        self.headers = headers
        self.peer_ip = peer_ip
        self._close_timeout = close_timeout
        self._closed = False

    async def iter_bytes(self, max_bytes: int | None = None) -> AsyncIterator[bytes]:
        budget = max_bytes
        transfer = self.headers.get("transfer-encoding", "").lower()
        if transfer:
            if transfer != "chunked":
                raise DownloadError("Remote transfer encoding is unsupported")
            while True:
                line = await _read_http_line(self._reader, 1024)
                try:
                    size = int(line.split(b";", 1)[0].strip(), 16)
                except ValueError:
                    raise DownloadError("Remote server response is invalid") from None
                if size == 0:
                    while await _read_http_line(self._reader, 8192) != b"\r\n":
                        pass
                    return
                if budget is not None and size > budget:
                    raise DownloadError("Remote media exceeds configured limit")
                remaining = size
                while remaining:
                    piece_size = min(64 * 1024, remaining)
                    try:
                        chunk = await self._reader.readexactly(piece_size)
                    except asyncio.IncompleteReadError:
                        raise DownloadError("Remote server response is incomplete") from None
                    remaining -= len(chunk)
                    if budget is not None:
                        budget -= len(chunk)
                    yield chunk
                try:
                    terminator = await self._reader.readexactly(2)
                except asyncio.IncompleteReadError:
                    raise DownloadError("Remote server response is incomplete") from None
                if terminator != b"\r\n":
                    raise DownloadError("Remote server response is invalid")
            return
        length = _content_length(self.headers.get("content-length"))
        if length is not None:
            if budget is not None and length > budget:
                raise DownloadError("Remote media exceeds configured limit")
            remaining = length
            while remaining:
                chunk = await self._reader.read(min(64 * 1024, remaining))
                if not chunk:
                    raise DownloadError("Remote server response is incomplete")
                remaining -= len(chunk)
                if budget is not None:
                    budget -= len(chunk)
                yield chunk
            return
        while True:
            request_size = 64 * 1024 if budget is None else min(64 * 1024, budget + 1)
            chunk = await self._reader.read(request_size)
            if not chunk:
                return
            if budget is not None:
                if len(chunk) > budget:
                    raise DownloadError("Remote media exceeds configured limit")
                budget -= len(chunk)
            yield chunk

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._writer.close()
        try:
            await asyncio.wait_for(self._writer.wait_closed(), self._close_timeout)
        except asyncio.TimeoutError:
            raise DownloadError("Remote close timed out") from None


class SecureDownloader:
    _SUFFIXES = {
        "video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov",
        "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/wav": ".wav",
        "audio/x-wav": ".wav", "audio/ogg": ".ogg", "video/ogg": ".ogv",
        "audio/flac": ".flac", "video/x-matroska": ".mkv",
    }

    def __init__(
        self,
        resolver: Resolver | None = None,
        client: HTTPClient | None = None,
        *,
        max_bytes: int = 5 * 1024**3,
        max_redirects: int = 5,
        cancel_poll_interval_sec: float = 0.1,
        timeouts: TimeoutPolicy | None = None,
    ):
        if max_bytes < 1 or max_redirects < 0 or cancel_poll_interval_sec <= 0:
            raise ValueError("download bounds must be non-negative")
        self._timeouts = timeouts or TimeoutPolicy()
        self._resolver = resolver or SystemResolver()
        self._client = client or PinnedHTTPClient(timeouts=self._timeouts)
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._cancel_poll_interval = cancel_poll_interval_sec

    async def validate_url(self, url: str, cancel_check: CancelCheck) -> None:
        parsed, port = _validated_remote_url(url)
        _raise_if_canceled(cancel_check)
        await self._public_addresses(parsed.hostname, port, cancel_check, self._timeouts.dns_sec)

    async def download(
        self,
        url: str,
        directory: Path,
        cancel_check: CancelCheck,
        progress: ProgressCallback | None = None,
    ) -> Path:
        current = url
        response: HTTPResponse | None = None
        part: Path | None = None
        final: Path | None = None
        committed = False
        deadline = asyncio.get_running_loop().time() + self._timeouts.total_sec
        try:
            for redirects in range(self._max_redirects + 1):
                _raise_if_canceled(cancel_check)
                parsed, port = _validated_remote_url(current)
                _check_deadline(deadline)
                addresses = await self._public_addresses(
                    parsed.hostname, port, cancel_check,
                    _phase_timeout(deadline, self._timeouts.dns_sec),
                )
                connect_ip = addresses[0]
                response = await _acquire_owned_resource(
                    self._client.open(current, connect_ip), cancel_check,
                    _phase_timeout(deadline, self._timeouts.connect_sec),
                    lambda acquired: _bounded_response_close(
                        acquired, self._timeouts.close_sec
                    ),
                )
                rebound = await self._public_addresses(
                    parsed.hostname, port, cancel_check,
                    _phase_timeout(deadline, self._timeouts.recheck_sec),
                )
                try:
                    peer = str(ipaddress.ip_address(response.peer_ip))
                except ValueError:
                    raise DownloadError("Remote address changed") from None
                if peer != str(ipaddress.ip_address(connect_ip)) or connect_ip not in rebound:
                    raise DownloadError("Remote address changed")
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location or redirects >= self._max_redirects:
                        raise DownloadError("Too many redirects")
                    current = urljoin(current, location)
                    await _await_phase(
                        response.close(), cancel_check,
                        _phase_timeout(deadline, self._timeouts.close_sec),
                    )
                    response = None
                    continue
                if response.status < 200 or response.status >= 300:
                    raise DownloadError("Media download failed")
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type not in self._SUFFIXES:
                    raise DownloadError("Unsupported remote media type")
                length = _content_length(response.headers.get("content-length"))
                if length is not None and length > self._max_bytes:
                    raise DownloadError("Remote media exceeds configured limit")
                suffix = self._SUFFIXES.get(content_type) or mimetypes.guess_extension(content_type) or ".media"
                final = Path(directory) / f"media{suffix}"
                part = final.with_name(final.name + ".part")
                await run_owned_to_thread(final.parent.mkdir, parents=True, exist_ok=True)
                received = 0
                output = await run_owned_to_thread(part.open, "xb")
                try:
                    async for chunk in _cancelable_chunks(
                        response.iter_bytes(self._max_bytes), cancel_check,
                        self._cancel_poll_interval,
                        self._timeouts.body_idle_sec, deadline,
                    ):
                        received += len(chunk)
                        if received > self._max_bytes:
                            raise DownloadError("Remote media exceeds configured limit")
                        await run_owned_to_thread(output.write, chunk)
                        _report(progress, received)
                finally:
                    await run_owned_to_thread(output.close)
                if received == 0:
                    raise DownloadError("Remote media is empty")
                _raise_if_canceled(cancel_check)
                await run_owned_to_thread(part.replace, final)
                committed = True
                return final
            raise DownloadError("Too many redirects")
        except asyncio.TimeoutError:
            raise DownloadError("Media download timed out") from None
        finally:
            if response is not None:
                try:
                    await asyncio.wait_for(response.close(), self._timeouts.close_sec)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
            if part is not None:
                await run_owned_to_thread(part.unlink, missing_ok=True)
            if not committed and final is not None:
                await run_owned_to_thread(final.unlink, missing_ok=True)

    async def _public_addresses(
        self, host: str, port: int, cancel_check: CancelCheck,
        timeout_sec: float,
    ) -> tuple[str, ...]:
        try:
            raw = await _await_phase(
                self._resolver.resolve(host, port), cancel_check, timeout_sec
            )
            addresses = tuple(str(ipaddress.ip_address(value)) for value in raw)
        except asyncio.TimeoutError:
            raise DownloadError("Remote resolution timed out") from None
        except JobCanceled:
            raise
        except Exception:
            raise DownloadError("Remote address is not allowed") from None
        if not addresses or any(not _is_public_unicast(value) for value in addresses):
            raise DownloadError("Remote address is not allowed")
        return addresses


class URLValidator(Protocol):
    def validate_url(self, url: str, cancel_check: CancelCheck) -> Awaitable[None]: ...


class SSRFProxy:
    """Authenticated loopback CONNECT proxy with per-tunnel DNS and peer pinning."""

    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        connector=asyncio.open_connection,
        cancel_check: CancelCheck = lambda: False,
        timeouts: TimeoutPolicy | None = None,
        max_tunnel_bytes: int = 64 * 1024**2,
    ):
        self._resolver = resolver or SystemResolver()
        self._connector = connector
        self._cancel_check = cancel_check
        self._timeouts = timeouts or TimeoutPolicy()
        self._max_tunnel_bytes = max_tunnel_bytes
        self._username = secrets.token_urlsafe(24)
        self._password = secrets.token_urlsafe(24)
        self._server = None
        self._handlers: set[asyncio.Task] = set()
        self._closing = False
        self.proxy_url = ""

    async def __aenter__(self):
        self._closing = False
        self._server = await asyncio.start_server(
            self._on_client, "127.0.0.1", 0, limit=32 * 1024
        )
        port = self._server.sockets[0].getsockname()[1]
        self.proxy_url = (
            f"http://{self._username}:{self._password}@127.0.0.1:{port}"
        )
        return self

    async def __aexit__(self, *args):
        self._closing = True
        if self._server is not None:
            self._server.close()
        # Accept callbacks already queued by the event loop may run just after
        # close().  Require two empty loop turns so those callbacks are
        # registered and joined too.
        empty_turns = 0
        while empty_turns < 2:
            await asyncio.sleep(0)
            tasks = tuple(self._handlers)
            if not tasks:
                empty_turns += 1
                continue
            empty_turns = 0
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._server is not None:
            await asyncio.wait_for(
                self._server.wait_closed(), self._timeouts.close_sec
            )

    def _on_client(self, reader, writer) -> None:
        coroutine = (
            self._close_rejected_client(writer)
            if self._closing
            else self._accept(reader, writer)
        )
        task = asyncio.create_task(coroutine)
        self._handlers.add(task)
        task.add_done_callback(self._handlers.discard)

    async def _close_rejected_client(self, writer) -> None:
        writer.close()
        await _bounded_writer_close(writer, self._timeouts.close_sec)

    async def connect_target(
        self, host: str, port: int, cancel_check: CancelCheck
    ):
        if port not in {80, 443}:
            raise DownloadError("Remote URL is invalid")
        raw = await _await_phase(
            self._resolver.resolve(host, port), cancel_check, self._timeouts.dns_sec
        )
        addresses = tuple(str(ipaddress.ip_address(value)) for value in raw)
        if not addresses or any(not _is_public_unicast(value) for value in addresses):
            raise DownloadError("Remote address is not allowed")
        selected = addresses[0]
        reader, writer = await _acquire_owned_resource(
            self._connector(selected, port), cancel_check,
            self._timeouts.connect_sec,
            lambda acquired: _close_connected_writer(
                acquired, self._timeouts.close_sec
            ),
        )
        try:
            peer = writer.get_extra_info("peername")
            rebound = await _await_phase(
                self._resolver.resolve(host, port), cancel_check,
                self._timeouts.recheck_sec,
            )
            rebound_addresses = tuple(
                str(ipaddress.ip_address(value)) for value in rebound
            )
            if (
                not peer or str(ipaddress.ip_address(peer[0])) != selected
                or selected not in rebound_addresses
                or any(not _is_public_unicast(value) for value in rebound_addresses)
            ):
                raise DownloadError("Remote address changed")
            return reader, writer
        except BaseException:
            writer.close()
            await _bounded_writer_close(writer, self._timeouts.close_sec)
            raise

    async def _accept(self, reader, writer) -> None:
        upstream = None
        pumps: tuple[asyncio.Task, ...] = ()
        try:
            header = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"), self._timeouts.headers_sec
            )
            if len(header) > 32 * 1024:
                raise DownloadError("Proxy request headers are too large")
            host, port = _parse_connect_request(
                header, self._username, self._password
            )
            upstream_reader, upstream = await self.connect_target(
                host, port, self._cancel_check
            )
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await asyncio.wait_for(writer.drain(), self._timeouts.drain_sec)
            pumps = (
                asyncio.create_task(self._pump(reader, upstream)),
                asyncio.create_task(self._pump(upstream_reader, writer)),
            )
            _, pending = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
            for pending_task in pending:
                pending_task.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
        except Exception:
            try:
                writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
                await asyncio.wait_for(writer.drain(), self._timeouts.drain_sec)
            except Exception:
                pass
        finally:
            for pump in pumps:
                if not pump.done():
                    pump.cancel()
            if pumps:
                await asyncio.gather(*pumps, return_exceptions=True)
            if upstream is not None:
                upstream.close()
                await _bounded_writer_close(upstream, self._timeouts.close_sec)
            writer.close()
            await _bounded_writer_close(writer, self._timeouts.close_sec)

    async def _pump(self, reader, writer) -> None:
        transferred = 0
        while True:
            _raise_if_canceled(self._cancel_check)
            chunk = await asyncio.wait_for(
                reader.read(64 * 1024), self._timeouts.body_idle_sec
            )
            if not chunk:
                return
            transferred += len(chunk)
            if transferred > self._max_tunnel_bytes:
                raise DownloadError("Proxy transfer limit exceeded")
            writer.write(chunk)
            await asyncio.wait_for(writer.drain(), self._timeouts.drain_sec)


class YtDlpDownloader:
    """Resolve trusted platform pages, then fetch the selected media through SSRF controls."""

    _PLATFORM_HOSTS = (
        "youtube.com", "youtu.be", "bilibili.com", "b23.tv",
        "tiktok.com", "soundcloud.com",
    )

    def __init__(
        self,
        page_validator: URLValidator,
        *,
        max_bytes: int = 5 * 1024**3,
        timeout_sec: float = 120.0,
        proxy_factory=None,
        youtube_dl_factory=None,
        thread_runner=run_owned_to_thread,
    ):
        self._validator = page_validator
        self._max_bytes = max_bytes
        self._timeout = timeout_sec
        self._proxy_factory = proxy_factory or (
            lambda cancel_check: SSRFProxy(cancel_check=cancel_check)
        )
        self._youtube_dl_factory = youtube_dl_factory or yt_dlp.YoutubeDL
        self._thread_runner = thread_runner

    async def download(
        self, url: str, directory: Path, cancel_check: CancelCheck,
        progress: ProgressCallback | None = None,
    ) -> Path:
        fetched = await self.download_with_metadata(
            url, directory, cancel_check, progress
        )
        return fetched.path

    async def download_with_metadata(
        self, url: str, directory: Path, cancel_check: CancelCheck,
        progress: ProgressCallback | None = None,
    ) -> DownloadedMedia:
        parsed, _ = _validated_remote_url(url)
        host = parsed.hostname.lower().rstrip(".")
        if not any(host == allowed or host.endswith("." + allowed) for allowed in self._PLATFORM_HOSTS):
            raise DownloadError("Unsupported page URL")
        await self._validator.validate_url(url, cancel_check)
        target = Path(directory)
        await self._thread_runner(target.mkdir, parents=True, exist_ok=True)
        try:
            async with self._proxy_factory(cancel_check) as proxy:
                title = await self._thread_runner(
                    self._download_sync,
                    url,
                    target,
                    proxy.proxy_url,
                    cancel_check,
                    progress,
                )
            path = await self._thread_runner(
                self._validate_single_output, target
            )
            return DownloadedMedia(path, title)
        except (JobCanceled, asyncio.CancelledError):
            await self._thread_runner(_remove_directory_contents, target)
            raise
        except Exception:
            await self._thread_runner(_remove_directory_contents, target)
            raise DownloadError("Unable to download media page") from None

    def _download_sync(
        self,
        url: str,
        directory: Path,
        proxy_url: str,
        cancel_check: CancelCheck,
        progress: ProgressCallback | None,
    ) -> str | None:
        def progress_hook(state) -> None:
            if cancel_check():
                raise JobCanceled
            if progress is None or state.get("status") != "downloading":
                return
            downloaded = state.get("downloaded_bytes")
            if isinstance(downloaded, (int, float)) and math.isfinite(downloaded):
                progress(max(0, min(self._max_bytes, int(downloaded))))

        options = {
            "format": "bestaudio/best",
            "outtmpl": str(directory / "media.%(ext)s"),
            "proxy": proxy_url,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "cachedir": False,
            "socket_timeout": min(15.0, self._timeout),
            "max_filesize": self._max_bytes,
            "retries": 1,
            "fragment_retries": 1,
            "extractor_retries": 1,
            "overwrites": False,
            "progress_hooks": [progress_hook],
        }
        with self._youtube_dl_factory(options) as downloader:
            info = downloader.extract_info(url, download=True)
        return _sanitize_title(info.get("title") if isinstance(info, dict) else None)

    def _validate_single_output(self, directory: Path) -> Path:
        root = directory.resolve()
        children = list(directory.iterdir())
        if len(children) != 1:
            raise DownloadError("Media download output is invalid")
        output = children[0]
        if (
            output.is_symlink()
            or not output.is_file()
            or output.suffix.lower() not in _SAFE_INPUTS
            or output.stat().st_size > self._max_bytes
        ):
            raise DownloadError("Media download output is invalid")
        resolved = output.resolve()
        if resolved.parent != root:
            raise DownloadError("Media download output is invalid")
        return resolved


def _remove_directory_contents(directory: Path) -> None:
    if not directory.exists():
        return
    for child in directory.iterdir():
        try:
            if child.is_symlink() or not child.is_dir():
                child.unlink()
            else:
                shutil.rmtree(child)
        except FileNotFoundError:
            pass


_TITLE_MAX_LENGTH = 200
_TITLE_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]+")


def _sanitize_title(value: object) -> str | None:
    """Normalize an extractor-reported title into a safe Episode title."""
    if not isinstance(value, str):
        return None
    cleaned = " ".join(_TITLE_CONTROL_CHARS.sub(" ", value).split())
    if not cleaned:
        return None
    return cleaned[:_TITLE_MAX_LENGTH].rstrip() or None


def _validated_remote_url(url: str):
    try:
        parsed = urlsplit(url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        raise DownloadError("Remote URL is invalid") from None
    if (
        any(ord(character) <= 32 or ord(character) == 127 for character in url)
        or "\\" in url
        or parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in {80, 443}
    ):
        raise DownloadError("Remote URL is invalid")
    return parsed, port


_HTTP_TOKEN = re.compile(rb"[!#$%&'*+\-.^_`|~0-9A-Za-z]+\Z")
_CONNECT_LINE = re.compile(rb"CONNECT ([^ ]+) HTTP/(1\.[01])\Z")
_DNS_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")


def _parse_connect_request(
    header: bytes, username: str, password: str
) -> tuple[str, int]:
    if len(header) > 32 * 1024 or not header.endswith(b"\r\n\r\n"):
        raise DownloadError("Proxy request is invalid")
    if b"\x00" in header or b"\n" in header.replace(b"\r\n", b""):
        raise DownloadError("Proxy request is invalid")
    lines = header[:-4].split(b"\r\n")
    if not lines or len(lines) > 65:
        raise DownloadError("Proxy request is invalid")
    match = _CONNECT_LINE.fullmatch(lines[0])
    if match is None:
        raise DownloadError("Proxy permits CONNECT only")
    try:
        authority = match.group(1).decode("ascii")
    except UnicodeDecodeError:
        raise DownloadError("Proxy target is invalid") from None
    fields: dict[bytes, bytes] = {}
    for line in lines[1:]:
        if not line or b":" not in line:
            raise DownloadError("Proxy request is invalid")
        name, value = line.split(b":", 1)
        key = name.lower()
        if _HTTP_TOKEN.fullmatch(name) is None or key in fields:
            raise DownloadError("Proxy request is invalid")
        value = value.strip(b" \t")
        if any(byte < 32 and byte != 9 or byte == 127 for byte in value):
            raise DownloadError("Proxy request is invalid")
        fields[key] = value
    expected = b"Basic " + base64.b64encode(
        f"{username}:{password}".encode("ascii")
    )
    if not secrets.compare_digest(
        fields.get(b"proxy-authorization", b""), expected
    ):
        raise DownloadError("Proxy authentication failed")
    return _split_connect_authority(authority)


def _split_connect_authority(authority: str) -> tuple[str, int]:
    if (
        not authority
        or any(ord(character) <= 32 or ord(character) == 127 for character in authority)
        or any(character in authority for character in "/?#@\\")
    ):
        raise DownloadError("Proxy target is invalid")
    if authority.startswith("["):
        match = re.fullmatch(r"\[([^\]]+)\]:(80|443)", authority)
        if match is None:
            raise DownloadError("Proxy target is invalid")
        try:
            host = str(ipaddress.IPv6Address(match.group(1)))
        except ValueError:
            raise DownloadError("Proxy target is invalid") from None
        return host, int(match.group(2))
    if authority.count(":") != 1:
        raise DownloadError("Proxy target is invalid")
    host, raw_port = authority.rsplit(":", 1)
    if raw_port not in {"80", "443"} or not host or "[" in host or "]" in host:
        raise DownloadError("Proxy target is invalid")
    try:
        parsed_ip = ipaddress.ip_address(host)
    except ValueError:
        labels = host.split(".")
        if len(host) > 253 or any(_DNS_LABEL.fullmatch(label) is None for label in labels):
            raise DownloadError("Proxy target is invalid") from None
    else:
        if parsed_ip.version != 4:
            raise DownloadError("Proxy target is invalid")
        host = str(parsed_ip)
    return host, int(raw_port)


async def _bounded_writer_close(writer, timeout_sec: float) -> None:
    try:
        await asyncio.wait_for(writer.wait_closed(), timeout_sec)
    except (asyncio.TimeoutError, ConnectionError, OSError):
        pass


def _clean_subprocess_env() -> dict[str, str]:
    blocked = {
        "http_proxy", "https_proxy", "all_proxy", "no_proxy",
        "ytdlp_plugin_dir", "ytdlp_plugins",
    }
    cleaned = {key: value for key, value in os.environ.items() if key.lower() not in blocked}
    cleaned["YTDLP_NO_PLUGINS"] = "1"
    return cleaned


async def _cancelable_chunks(
    iterator, cancel_check, poll_interval, idle_timeout=30.0, deadline=None
):
    source = iterator.__aiter__()
    while True:
        _raise_if_canceled(cancel_check)
        pending = asyncio.create_task(anext(source))
        idle_deadline = asyncio.get_running_loop().time() + idle_timeout
        try:
            while not pending.done():
                if cancel_check():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                    raise JobCanceled
                now = asyncio.get_running_loop().time()
                if now >= idle_deadline or (deadline is not None and now >= deadline):
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                    raise asyncio.TimeoutError
                await asyncio.wait((pending,), timeout=poll_interval)
            try:
                yield pending.result()
            except StopAsyncIteration:
                return
        except asyncio.CancelledError:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            raise


async def _await_phase(awaitable, cancel_check, timeout_sec):
    operation = awaitable if isinstance(awaitable, asyncio.Task) else asyncio.create_task(awaitable)
    deadline = asyncio.get_running_loop().time() + timeout_sec
    try:
        while not operation.done():
            if cancel_check():
                operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
                raise JobCanceled
            if asyncio.get_running_loop().time() >= deadline:
                operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
                raise asyncio.TimeoutError
            await asyncio.wait((operation,), timeout=min(0.05, timeout_sec))
        return operation.result()
    except asyncio.CancelledError:
        operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)
        raise


async def _acquire_owned_resource(
    awaitable, cancel_check, timeout_sec, release
):
    """Transfer a completed resource or release it before propagating failure."""
    operation = asyncio.ensure_future(awaitable)
    deadline = asyncio.get_running_loop().time() + timeout_sec
    try:
        while not operation.done():
            if cancel_check():
                raise JobCanceled
            if asyncio.get_running_loop().time() >= deadline:
                raise asyncio.TimeoutError
            await asyncio.wait((operation,), timeout=min(0.05, timeout_sec))
        return operation.result()
    except BaseException:
        operation.cancel()
        await _settle_task_uninterruptibly(operation)
        resource = None
        if not operation.cancelled() and operation.exception() is None:
            resource = operation.result()
        if resource is not None:
            try:
                cleanup = asyncio.ensure_future(release(resource))
            except BaseException:
                cleanup = None
            if cleanup is not None:
                await _settle_task_uninterruptibly(cleanup)
        raise


async def _settle_task_uninterruptibly(task: asyncio.Future) -> None:
    current = asyncio.current_task()
    while not task.done():
        try:
            await asyncio.wait((task,), timeout=0.05)
        except asyncio.CancelledError:
            # Preserve the original exception at the ownership boundary, but
            # do not let repeated cancellation interrupt resource cleanup.
            if current is not None:
                current.uncancel()
    if not task.cancelled():
        task.exception()


async def _bounded_response_close(response, timeout_sec: float) -> None:
    try:
        await asyncio.wait_for(response.close(), timeout_sec)
    except Exception:
        pass


async def _close_connected_writer(acquired, timeout_sec: float) -> None:
    _, writer = acquired
    writer.close()
    await _bounded_writer_close(writer, timeout_sec)


def _check_deadline(deadline: float) -> None:
    if asyncio.get_running_loop().time() >= deadline:
        raise asyncio.TimeoutError


def _phase_timeout(deadline: float, configured: float) -> float:
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise asyncio.TimeoutError
    return min(configured, remaining)


def _is_public_unicast(raw: str) -> bool:
    address = ipaddress.ip_address(raw)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return (
        address.is_global
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_private
    )


async def _bounded_communicate(process, limit: int) -> tuple[bytes, bytes]:
    if not hasattr(process, "stdout") or not hasattr(process, "stderr"):
        stdout, stderr = await process.communicate()
        return stdout[:limit], stderr[:limit]

    async def drain(stream) -> bytes:
        captured = bytearray()
        while True:
            chunk = await stream.read(64 * 1024)
            if not chunk:
                return bytes(captured)
            if len(captured) < limit:
                captured.extend(chunk[: limit - len(captured)])

    stdout_task = asyncio.create_task(drain(process.stdout))
    stderr_task = asyncio.create_task(drain(process.stderr))
    try:
        await process.wait()
        return await stdout_task, await stderr_task
    except asyncio.CancelledError:
        stdout_task.cancel()
        stderr_task.cancel()
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        raise


async def _read_http_line(reader, limit: int) -> bytes:
    if limit < 2:
        raise DownloadError("Remote response headers are too large")
    try:
        line = await reader.readline()
    except Exception:
        raise DownloadError("Remote server response is invalid") from None
    if not line or len(line) > limit or not line.endswith(b"\r\n"):
        raise DownloadError("Remote server response is invalid")
    return line


def _probe_metadata(payload: str, media: Path):
    try:
        document = json.loads(payload)
        streams = document["streams"]
        duration = float(document["format"]["duration"])
        if not math.isfinite(duration) or duration <= 0 or not isinstance(streams, list):
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise SourceIngestError("Media metadata is invalid") from None
    formats = {
        value.strip().lower()
        for value in str(document.get("format", {}).get("format_name", "")).split(",")
        if value.strip()
    }
    if not formats or formats & _MANIFEST_FORMATS:
        raise SourceIngestError("Media format is not allowed")
    if not formats & _ALLOWED_PROBE_FORMATS.get(media.suffix.lower(), set()):
        raise SourceIngestError("Media format is not allowed")
    video = next(
        (
            stream for stream in streams
            if stream.get("codec_type") == "video"
            and not bool(stream.get("disposition", {}).get("attached_pic"))
        ),
        None,
    )
    if video is not None:
        try:
            height = int(video["height"])
            width = int(video["width"])
            if height < 1 or width < 1:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise SourceIngestError("Media metadata is invalid") from None
        resolution = f"{height}p"
        content_type = _media_type(media, document, video=True)
    elif any(stream.get("codec_type") == "audio" for stream in streams):
        resolution = None
        content_type = _media_type(media, document, video=False)
    else:
        raise SourceIngestError("Media metadata is invalid")
    return duration, resolution, video is not None, content_type


def _media_type(media: Path, document: dict, *, video: bool) -> str:
    suffix = media.suffix.lower()
    known = {
        ".mp4": "video/mp4" if video else "audio/mp4", ".mov": "video/quicktime",
        ".webm": "video/webm" if video else "audio/webm", ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4", ".wav": "audio/wav", ".ogg": "video/ogg" if video else "audio/ogg",
    }
    if suffix in known:
        return known[suffix]
    formats = str(document.get("format", {}).get("format_name", "")).split(",")
    for name in formats:
        if name in {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}:
            return "video/mp4" if video else "audio/mp4"
        if name == "mp3":
            return "audio/mpeg"
        if name == "wav":
            return "audio/wav"
        if name == "webm":
            return "video/webm" if video else "audio/webm"
    raise SourceIngestError("Media format is not allowed")


def _content_length(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        raise DownloadError("Remote content length is invalid") from None
    if value < 0:
        raise DownloadError("Remote content length is invalid")
    return value


def _valid_jpeg(path: Path) -> bool:
    if (
        not path.is_file() or path.is_symlink()
        or not 16 <= path.stat().st_size <= 20 * 1024**2
    ):
        return False
    data = path.read_bytes()
    if not data.startswith(b"\xff\xd8"):
        return False
    offset = 2
    saw_frame = False
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            return False
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            return False
        marker = data[offset]
        offset += 1
        if marker == 0xD9:
            return False
        if marker == 0xDA:
            if not saw_frame or offset + 2 > len(data):
                return False
            length = int.from_bytes(data[offset:offset + 2], "big")
            if length < 6 or offset + length > len(data):
                return False
            offset += length
            while offset + 1 < len(data):
                marker_at = data.find(b"\xff", offset)
                if marker_at < 0 or marker_at + 1 >= len(data):
                    return False
                following = data[marker_at + 1]
                if following == 0x00 or 0xD0 <= following <= 0xD7:
                    offset = marker_at + 2
                    continue
                return following == 0xD9
            return False
        if marker in {0x01, *range(0xD0, 0xD8)} or offset + 2 > len(data):
            return False
        length = int.from_bytes(data[offset:offset + 2], "big")
        if length < 2 or offset + length > len(data):
            return False
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            if length < 8:
                return False
            saw_frame = True
        offset += length
    return False


def _raise_if_canceled(cancel_check: CancelCheck) -> None:
    if cancel_check():
        raise JobCanceled


def _report(callback, value: int) -> None:
    if callback is not None:
        callback(value)


def _has_symlink_component(path: Path, root: Path) -> bool:
    candidate = Path(path).absolute()
    base = Path(root).absolute()
    try:
        relative = candidate.relative_to(base)
    except ValueError:
        return True
    current = base
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


async def _remove_attempt(attempt: Path, expected: Path, data_dir: Path) -> None:
    candidate = Path(attempt).absolute()
    if candidate != Path(expected).absolute():
        return
    if candidate.is_symlink():
        await run_owned_to_thread(candidate.unlink, missing_ok=True)
        return
    if _has_symlink_component(candidate, data_dir):
        return
    try:
        resolved = candidate.resolve()
        root = Path(data_dir).resolve()
    except OSError:
        return
    if resolved == root or root not in resolved.parents or resolved != candidate:
        return
    if candidate.is_dir():
        await run_owned_to_thread(shutil.rmtree, candidate)
