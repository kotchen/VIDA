from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import mimetypes
import shutil
import socket
import ssl
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin, urlsplit

from ..domain import EpisodeRecord
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


class ProcessRunner(Protocol):
    def run(
        self, command: Sequence[str], cancel_check: CancelCheck,
        timeout_sec: float | None = None,
    ) -> Awaitable[ProcessResult]: ...


class Downloader(Protocol):
    def download(
        self,
        url: str,
        directory: Path,
        cancel_check: CancelCheck,
        progress: ProgressCallback | None = None,
    ) -> Awaitable[Path]: ...


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
        runtime_timeout_sec: float = 120.0,
        max_output_bytes: int = 1024 * 1024,
    ):
        self._factory = process_factory
        self._poll_interval = max(0, poll_interval_sec)
        self._terminate_timeout = max(0, terminate_timeout_sec)
        self._runtime_timeout = runtime_timeout_sec
        self._max_output_bytes = max_output_bytes

    async def run(
        self, command: Sequence[str], cancel_check: CancelCheck,
        timeout_sec: float | None = None,
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
            await self._terminate(process, communication)
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
            await asyncio.shield(communication)


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
        attempt = self._bounded_path(attempt_dir, "attempt path")
        expected_attempt = (
            self._data_dir / "episodes" / episode.id / "attempts"
            / (episode.current_job_id or "")
        ).resolve()
        if not episode.current_job_id or attempt != expected_attempt:
            raise SourceIngestError("Invalid attempt path")
        downloaded = episode.source_type == "url"
        try:
            _report(progress, 5)
            if downloaded:
                _raise_if_canceled(cancel_check)
                if not episode.source_url:
                    raise SourceIngestError("URL source is missing")
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
            await asyncio.to_thread(poster.parent.mkdir, parents=True, exist_ok=True)
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
                    or not await asyncio.to_thread(_valid_jpeg, poster)
                ):
                    raise SourceIngestError("Media poster is invalid")
                poster_type = "image/jpeg"
            else:
                if not self._audio_poster.is_file():
                    raise SourceIngestError("Audio poster is unavailable")
                await asyncio.to_thread(shutil.copyfile, self._audio_poster, poster)
                poster_type = "image/png"
            _raise_if_canceled(cancel_check)
            _report(progress, 20)
            return PreparedSource(
                media, metadata[0], metadata[1], metadata[3], poster, poster_type
            )
        except (JobCanceled, asyncio.CancelledError, SourceIngestError):
            await _remove_attempt(attempt)
            raise
        except Exception:
            await _remove_attempt(attempt)
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
        try:
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
        except Exception as exc:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), self._timeouts.close_sec)
            except asyncio.TimeoutError:
                pass
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
                response = await _await_phase(
                    self._client.open(current, connect_ip), cancel_check,
                    _phase_timeout(deadline, self._timeouts.connect_sec),
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
                await asyncio.to_thread(final.parent.mkdir, parents=True, exist_ok=True)
                received = 0
                output = await asyncio.to_thread(part.open, "xb")
                try:
                    async for chunk in _cancelable_chunks(
                        response.iter_bytes(self._max_bytes), cancel_check,
                        self._cancel_poll_interval,
                        self._timeouts.body_idle_sec, deadline,
                    ):
                        received += len(chunk)
                        if received > self._max_bytes:
                            raise DownloadError("Remote media exceeds configured limit")
                        await asyncio.to_thread(output.write, chunk)
                        _report(progress, received)
                finally:
                    await asyncio.to_thread(output.close)
                if received == 0:
                    raise DownloadError("Remote media is empty")
                _raise_if_canceled(cancel_check)
                await asyncio.to_thread(part.replace, final)
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
                await asyncio.to_thread(part.unlink, missing_ok=True)
            if not committed and final is not None:
                await asyncio.to_thread(final.unlink, missing_ok=True)

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


class YtDlpDownloader:
    """Resolve trusted platform pages, then fetch the selected media through SSRF controls."""

    _PLATFORM_HOSTS = (
        "youtube.com", "youtu.be", "bilibili.com", "b23.tv",
        "tiktok.com", "soundcloud.com",
    )

    def __init__(
        self,
        runner: ProcessRunner,
        media_downloader: Downloader,
        page_validator: URLValidator,
        *,
        max_bytes: int = 5 * 1024**3,
        timeout_sec: float = 120.0,
    ):
        self._runner = runner
        self._media = media_downloader
        self._validator = page_validator
        self._max_bytes = max_bytes
        self._timeout = timeout_sec

    async def download(
        self, url: str, directory: Path, cancel_check: CancelCheck,
        progress: ProgressCallback | None = None,
    ) -> Path:
        parsed, _ = _validated_remote_url(url)
        host = parsed.hostname.lower().rstrip(".")
        if not any(host == allowed or host.endswith("." + allowed) for allowed in self._PLATFORM_HOSTS):
            raise DownloadError("Unsupported page URL")
        await self._validator.validate_url(url, cancel_check)
        result = await self._runner.run(
            (
                "yt-dlp", "--no-config", "--no-playlist", "--skip-download",
                "--dump-single-json", "--no-warnings", "--socket-timeout", "15",
                "--max-filesize", str(self._max_bytes), "--", url,
            ),
            cancel_check,
            timeout_sec=self._timeout,
        )
        if result.returncode != 0:
            raise DownloadError("Unable to resolve media page")
        try:
            document = json.loads(result.stdout)
            media_url = document["url"]
            if not isinstance(media_url, str):
                raise TypeError
        except (json.JSONDecodeError, KeyError, TypeError):
            raise DownloadError("Unable to resolve media page") from None
        _validated_remote_url(media_url)
        return await self._media.download(
            media_url, directory, cancel_check, progress
        )


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
    if not path.is_file() or path.is_symlink() or path.stat().st_size < 4:
        return False
    with path.open("rb") as stream:
        head = stream.read(3)
        stream.seek(-2, 2)
        tail = stream.read(2)
    return head == b"\xff\xd8\xff" and tail == b"\xff\xd9"


def _raise_if_canceled(cancel_check: CancelCheck) -> None:
    if cancel_check():
        raise JobCanceled


def _report(callback, value: int) -> None:
    if callback is not None:
        callback(value)


async def _remove_attempt(attempt: Path) -> None:
    if attempt.exists():
        await asyncio.to_thread(shutil.rmtree, attempt)
