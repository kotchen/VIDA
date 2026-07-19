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
        self, command: Sequence[str], cancel_check: CancelCheck
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

    def iter_bytes(self) -> AsyncIterator[bytes]: ...
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
    ):
        self._factory = process_factory
        self._poll_interval = max(0, poll_interval_sec)
        self._terminate_timeout = max(0, terminate_timeout_sec)

    async def run(self, command: Sequence[str], cancel_check: CancelCheck) -> ProcessResult:
        if not command or not all(isinstance(value, str) for value in command):
            raise ValueError("command must contain string arguments")
        process = await self._factory(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        communication = asyncio.create_task(process.communicate())
        try:
            while not communication.done():
                if cancel_check():
                    await self._terminate(process, communication)
                    raise JobCanceled
                await asyncio.wait(
                    (communication,), timeout=self._poll_interval,
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
    ):
        self._runner = runner
        self._downloader = downloader
        self._data_dir = Path(data_dir or Path("data") / "v2").resolve()
        self._audio_poster = Path(audio_poster or Path("static") / "sipsip.png").resolve()

    async def prepare(
        self,
        episode: EpisodeRecord,
        attempt_dir: Path,
        cancel_check: CancelCheck,
        progress: ProgressCallback | None = None,
    ) -> PreparedSource:
        attempt = self._bounded_path(attempt_dir, "attempt path")
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
            _report(progress, 12)
            result = await self._runner.run(
                (
                    "ffprobe", "-v", "error", "-print_format", "json",
                    "-show_format", "-show_streams", str(media),
                ),
                cancel_check,
            )
            if result.returncode != 0:
                raise SourceIngestError("Media processing failed")
            metadata = _probe_metadata(result.stdout, media)
            poster = attempt / ("poster.jpg" if metadata[2] else "poster.png")
            poster.parent.mkdir(parents=True, exist_ok=True)
            if metadata[2]:
                result = await self._runner.run(
                    (
                        "ffmpeg", "-nostdin", "-y", "-ss", "0", "-i", str(media),
                        "-frames:v", "1", "-q:v", "2", str(poster),
                    ),
                    cancel_check,
                )
                if result.returncode != 0 or not poster.is_file():
                    raise SourceIngestError("Media processing failed")
                poster_type = "image/jpeg"
            else:
                if not self._audio_poster.is_file():
                    raise SourceIngestError("Audio poster is unavailable")
                shutil.copyfile(self._audio_poster, poster)
                poster_type = "image/png"
            _raise_if_canceled(cancel_check)
            _report(progress, 20)
            return PreparedSource(
                media, metadata[0], metadata[1], metadata[3], poster, poster_type
            )
        except (JobCanceled, asyncio.CancelledError, SourceIngestError):
            _remove_attempt(attempt)
            raise
        except Exception:
            _remove_attempt(attempt)
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
    ):
        self._connection_factory = connection_factory
        self._header_limit = header_limit_bytes

    async def open(self, url: str, connect_ip: str) -> HTTPResponse:
        parsed, port = _validated_remote_url(url)
        tls = parsed.scheme == "https"
        kwargs = {}
        if tls:
            kwargs = {"ssl": ssl.create_default_context(), "server_hostname": parsed.hostname}
        reader, writer = await self._connection_factory(connect_ip, port, **kwargs)
        default_port = 443 if tls else 80
        host = parsed.hostname if port == default_port else f"{parsed.hostname}:{port}"
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        request = (
            f"GET {target} HTTP/1.1\r\nHost: {host}\r\nAccept: audio/*, video/*\r\n"
            "Accept-Encoding: identity\r\nConnection: close\r\nUser-Agent: VIDA/2\r\n\r\n"
        ).encode("ascii")
        writer.write(request)
        await writer.drain()
        try:
            status_line = await _read_http_line(reader, self._header_limit)
            pieces = status_line.decode("iso-8859-1").rstrip("\r\n").split(" ", 2)
            if len(pieces) < 2 or not pieces[0].startswith("HTTP/"):
                raise DownloadError("Remote server response is invalid")
            status = int(pieces[1])
            headers: dict[str, str] = {}
            consumed = len(status_line)
            while True:
                line = await _read_http_line(reader, self._header_limit - consumed)
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
            return _PinnedHTTPResponse(reader, writer, status, headers, str(peer[0]))
        except Exception:
            writer.close()
            await writer.wait_closed()
            raise


class _PinnedHTTPResponse:
    def __init__(self, reader, writer, status, headers, peer_ip):
        self._reader = reader
        self._writer = writer
        self.status = status
        self.headers = headers
        self.peer_ip = peer_ip
        self._closed = False

    async def iter_bytes(self) -> AsyncIterator[bytes]:
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
                try:
                    chunk = await self._reader.readexactly(size)
                    if await self._reader.readexactly(2) != b"\r\n":
                        raise DownloadError("Remote server response is invalid")
                except asyncio.IncompleteReadError:
                    raise DownloadError("Remote server response is incomplete") from None
                yield chunk
            return
        length = _content_length(self.headers.get("content-length"))
        if length is not None:
            remaining = length
            while remaining:
                chunk = await self._reader.read(min(64 * 1024, remaining))
                if not chunk:
                    raise DownloadError("Remote server response is incomplete")
                remaining -= len(chunk)
                yield chunk
            return
        while True:
            chunk = await self._reader.read(64 * 1024)
            if not chunk:
                return
            yield chunk

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._writer.close()
        await self._writer.wait_closed()


class SecureDownloader:
    _SUFFIXES = {
        "video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov",
        "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/wav": ".wav",
        "audio/x-wav": ".wav", "audio/ogg": ".ogg", "video/ogg": ".ogv",
    }

    def __init__(
        self,
        resolver: Resolver | None = None,
        client: HTTPClient | None = None,
        *,
        max_bytes: int = 5 * 1024**3,
        max_redirects: int = 5,
        cancel_poll_interval_sec: float = 0.1,
    ):
        if max_bytes < 1 or max_redirects < 0 or cancel_poll_interval_sec <= 0:
            raise ValueError("download bounds must be non-negative")
        self._resolver = resolver or SystemResolver()
        self._client = client or PinnedHTTPClient()
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._cancel_poll_interval = cancel_poll_interval_sec

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
        try:
            for redirects in range(self._max_redirects + 1):
                _raise_if_canceled(cancel_check)
                parsed, port = _validated_remote_url(current)
                addresses = await self._public_addresses(parsed.hostname, port)
                connect_ip = addresses[0]
                response = await self._client.open(current, connect_ip)
                rebound = await self._public_addresses(parsed.hostname, port)
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
                    await response.close()
                    response = None
                    continue
                if response.status < 200 or response.status >= 300:
                    raise DownloadError("Media download failed")
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if not (content_type.startswith("audio/") or content_type.startswith("video/")):
                    raise DownloadError("Unsupported remote media type")
                length = _content_length(response.headers.get("content-length"))
                if length is not None and length > self._max_bytes:
                    raise DownloadError("Remote media exceeds configured limit")
                suffix = self._SUFFIXES.get(content_type) or mimetypes.guess_extension(content_type) or ".media"
                final = Path(directory) / f"media{suffix}"
                part = final.with_name(final.name + ".part")
                final.parent.mkdir(parents=True, exist_ok=True)
                received = 0
                with part.open("xb") as output:
                    async for chunk in _cancelable_chunks(
                        response.iter_bytes(), cancel_check, self._cancel_poll_interval
                    ):
                        received += len(chunk)
                        if received > self._max_bytes:
                            raise DownloadError("Remote media exceeds configured limit")
                        output.write(chunk)
                        _report(progress, received)
                if received == 0:
                    raise DownloadError("Remote media is empty")
                _raise_if_canceled(cancel_check)
                part.replace(final)
                committed = True
                return final
            raise DownloadError("Too many redirects")
        finally:
            if response is not None:
                await response.close()
            if part is not None:
                part.unlink(missing_ok=True)
            if not committed and final is not None:
                final.unlink(missing_ok=True)

    async def _public_addresses(self, host: str, port: int) -> tuple[str, ...]:
        try:
            raw = await self._resolver.resolve(host, port)
            addresses = tuple(str(ipaddress.ip_address(value)) for value in raw)
        except Exception:
            raise DownloadError("Remote address is not allowed") from None
        if not addresses or any(not ipaddress.ip_address(value).is_global for value in addresses):
            raise DownloadError("Remote address is not allowed")
        return addresses


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
    ):
        raise DownloadError("Remote URL is invalid")
    return parsed, port


async def _cancelable_chunks(iterator, cancel_check, poll_interval):
    source = iterator.__aiter__()
    while True:
        _raise_if_canceled(cancel_check)
        pending = asyncio.create_task(anext(source))
        try:
            while not pending.done():
                if cancel_check():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                    raise JobCanceled
                await asyncio.wait((pending,), timeout=poll_interval)
            try:
                yield pending.result()
            except StopAsyncIteration:
                return
        except asyncio.CancelledError:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
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
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
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
    return "video/octet-stream" if video else "audio/octet-stream"


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


def _raise_if_canceled(cancel_check: CancelCheck) -> None:
    if cancel_check():
        raise JobCanceled


def _report(callback, value: int) -> None:
    if callback is not None:
        callback(value)


def _remove_attempt(attempt: Path) -> None:
    if attempt.exists():
        shutil.rmtree(attempt)
