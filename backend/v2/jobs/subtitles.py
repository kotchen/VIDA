"""Fetch platform-hosted subtitle tracks for URL episodes.

When a video page already carries subtitle tracks (uploader-provided or
platform auto-captions), downloading them is seconds-fast compared with a
full Whisper transcription. This module resolves the available tracks with
yt-dlp, picks the best track for the user's chosen language (Simplified and
Traditional Chinese are distinct preferences), downloads it through the same
SSRF controls as media downloads, and parses it into transcript segments.

The fetcher returns None when no suitable track exists so the pipeline can
fall back to Whisper.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import yt_dlp
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .blocking import run_owned_to_thread
from .models import JobCanceled
from .source_ingest import (
    CancelCheck,
    SSRFProxy,
    URLValidator,
    YtDlpDownloader,
    _validated_remote_url,
)


logger = logging.getLogger(__name__)

_MAX_SUBTITLE_BYTES = 16 * 1024**2
# Simplified and Traditional Chinese are separate user choices; each maps to
# the platform language codes that carry that script. The legacy generic "zh"
# prefers Simplified tracks but still accepts Traditional ones.
_LANGUAGE_PREFERENCES: Mapping[str, Sequence[str]] = {
    "zh-hans": ("zh-Hans", "zh-CN", "zh-SG", "zh"),
    "zh-cn": ("zh-Hans", "zh-CN", "zh-SG", "zh"),
    "zh-sg": ("zh-Hans", "zh-CN", "zh-SG", "zh"),
    "zh-hant": ("zh-Hant", "zh-TW", "zh-HK"),
    "zh-tw": ("zh-Hant", "zh-TW", "zh-HK"),
    "zh-hk": ("zh-Hant", "zh-TW", "zh-HK"),
    "zh": ("zh-Hans", "zh-CN", "zh-SG", "zh", "zh-Hant", "zh-TW", "zh-HK"),
}


@dataclass(frozen=True)
class SubtitleSegment:
    start_sec: float
    end_sec: float
    text: str


@dataclass(frozen=True)
class SubtitleTranscript:
    """Structurally compatible with the transcriber's StructuredTranscription."""

    language: str
    automatic: bool
    segments: tuple[SubtitleSegment, ...]


@dataclass(frozen=True)
class _TrackChoice:
    language: str
    automatic: bool


class PlatformSubtitleFetcher:
    """Resolve and download platform subtitle tracks under SSRF controls."""

    def __init__(
        self,
        page_validator: URLValidator,
        *,
        timeout_sec: float = 90.0,
        proxy_factory=None,
        youtube_dl_factory=None,
        thread_runner=run_owned_to_thread,
        egress_proxy: str | None = None,
    ):
        self._validator = page_validator
        self._timeout = timeout_sec
        self._proxy_factory = proxy_factory or (
            lambda cancel_check: SSRFProxy(cancel_check=cancel_check)
        )
        self._youtube_dl_factory = youtube_dl_factory or yt_dlp.YoutubeDL
        self._thread_runner = thread_runner
        self._egress_proxy = egress_proxy

    async def fetch(
        self,
        url: str,
        language: str,
        directory: Path,
        cancel_check: CancelCheck,
    ) -> SubtitleTranscript | None:
        parsed, _ = _validated_remote_url(url)
        host = parsed.hostname.lower().rstrip(".")
        if not any(
            host == allowed or host.endswith("." + allowed)
            for allowed in YtDlpDownloader._PLATFORM_HOSTS
        ):
            return None
        await self._validator.validate_url(url, cancel_check)
        target = Path(directory)
        await self._thread_runner(target.mkdir, parents=True, exist_ok=True)
        try:
            if self._egress_proxy is not None:
                return await self._thread_runner(
                    self._fetch_sync, url, language, target, self._egress_proxy,
                    cancel_check,
                )
            async with self._proxy_factory(cancel_check) as proxy:
                return await self._thread_runner(
                    self._fetch_sync, url, language, target, proxy.proxy_url,
                    cancel_check,
                )
        except (JobCanceled, asyncio.CancelledError):
            raise
        except Exception:
            logger.warning("Platform subtitle fetch failed; falling back", exc_info=True)
            return None
        finally:
            await self._thread_runner(_remove_contents, target)

    def _fetch_sync(
        self,
        url: str,
        language: str,
        directory: Path,
        proxy_url: str,
        cancel_check: CancelCheck,
    ) -> SubtitleTranscript | None:
        base_options = {
            "proxy": proxy_url,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "cachedir": False,
            "socket_timeout": min(15.0, self._timeout),
            "retries": 1,
            "extractor_retries": 1,
            # The android client lists and serves YouTube caption tracks
            # without a PO token; the web client gets them discarded.
            "extractor_args": {"youtube": {"player_client": ["android"]}},
        }
        with self._youtube_dl_factory(dict(base_options)) as downloader:
            info = downloader.extract_info(url, download=False)
        _raise_if_canceled(cancel_check)
        if not isinstance(info, dict):
            return None
        choice = _choose_track(info, language)
        if choice is None:
            return None
        download_options = {
            **base_options,
            "skip_download": True,
            "writesubtitles": not choice.automatic,
            "writeautomaticsub": choice.automatic,
            "subtitleslangs": [choice.language],
            "subtitlesformat": "json3/vtt/srt/best",
            "outtmpl": str(directory / "subtitle.%(ext)s"),
        }
        with self._youtube_dl_factory(download_options) as downloader:
            downloader.extract_info(url, download=True)
        _raise_if_canceled(cancel_check)
        written = _subtitle_files(directory)
        if len(written) != 1:
            return None
        segments = _parse_subtitle_file(written[0])
        if not segments:
            return None
        return SubtitleTranscript(choice.language, choice.automatic, segments)


def _raise_if_canceled(cancel_check: CancelCheck) -> None:
    if cancel_check():
        raise JobCanceled


def _remove_contents(directory: Path) -> None:
    try:
        for child in directory.iterdir():
            if child.is_file() and not child.is_symlink():
                child.unlink(missing_ok=True)
    except OSError:
        pass


def _subtitle_files(directory: Path) -> list[Path]:
    root = directory.resolve()
    outputs = []
    for child in directory.iterdir():
        if not child.is_file() or child.is_symlink():
            continue
        if child.resolve().parent != root:
            continue
        if child.stat().st_size > _MAX_SUBTITLE_BYTES:
            continue
        outputs.append(child)
    return outputs


def _preference_codes(language: str) -> tuple[str, ...]:
    normalized = (language or "").strip()
    mapped = _LANGUAGE_PREFERENCES.get(normalized.lower())
    if mapped is not None:
        return tuple(mapped)
    if not normalized:
        return ()
    base = normalized.lower().split("-", 1)[0]
    preferences = [normalized]
    if base and base != normalized.lower():
        preferences.append(base)
    if base:
        preferences.append(f"{base}-")
    return tuple(preferences)


def _match_language(available: Sequence[str], preference: str) -> str | None:
    if preference.endswith("-"):
        for code in available:
            if code.lower().startswith(preference.lower()):
                return code
        return None
    for code in available:
        if code.lower() == preference.lower():
            return code
    return None


def _choose_track(info: dict, language: str) -> _TrackChoice | None:
    """Pick the best track for the user's language; manual beats automatic."""
    manual = info.get("subtitles")
    automatic = info.get("automatic_captions")
    manual_codes = sorted(manual) if isinstance(manual, dict) else []
    automatic_codes = sorted(automatic) if isinstance(automatic, dict) else []
    preferences = _preference_codes(language)
    for preference in preferences:
        matched = _match_language(manual_codes, preference)
        if matched is not None:
            return _TrackChoice(matched, False)
    for preference in preferences:
        matched = _match_language(automatic_codes, preference)
        if matched is not None:
            return _TrackChoice(matched, True)
    return None


_TIMESTAMP = re.compile(
    r"(?P<start>\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*"
    r"(?P<end>\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})"
)
_TAG = re.compile(r"<[^>]+>")


def _parse_subtitle_file(path: Path) -> tuple[SubtitleSegment, ...]:
    raw = path.read_bytes()
    if len(raw) > _MAX_SUBTITLE_BYTES:
        return ()
    text = raw.decode("utf-8-sig", errors="replace").strip()
    if not text:
        return ()
    if text.startswith("{"):
        return _parse_json_subtitles(text)
    if "-->" in text:
        return _parse_cue_subtitles(text)
    return ()


def _parse_json_subtitles(text: str) -> tuple[SubtitleSegment, ...]:
    try:
        document = json.loads(text)
    except ValueError:
        return ()
    if not isinstance(document, dict):
        return ()
    body = document.get("body")
    if isinstance(body, list):
        return _parse_bilibili_body(body)
    events = document.get("events")
    if isinstance(events, list):
        return _parse_json3_events(events)
    return ()


def _parse_bilibili_body(body: list) -> tuple[SubtitleSegment, ...]:
    cues: list[tuple[float, float, str]] = []
    for entry in body:
        if not isinstance(entry, dict):
            continue
        content = entry.get("content")
        start, end = entry.get("from"), entry.get("to")
        if not isinstance(content, str) or not content.strip():
            continue
        if not _finite(start) or not _finite(end) or float(end) <= float(start):
            continue
        cues.append((float(start), float(end), _clean_text(content)))
    return _segments_from_cues(cues)


def _parse_json3_events(events: list) -> tuple[SubtitleSegment, ...]:
    cues: list[tuple[float, float, str]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        segs = event.get("segs")
        if not isinstance(segs, list):
            continue
        text = "".join(
            piece.get("utf8", "") for piece in segs if isinstance(piece, dict)
        )
        if not text.strip():
            continue
        start_ms = event.get("tStartMs")
        if not _finite(start_ms):
            continue
        start = float(start_ms) / 1000
        duration_ms = event.get("dDurationMs")
        end = start + float(duration_ms) / 1000 if _finite(duration_ms) else None
        cues.append((start, end if end is not None else -1.0, _clean_text(text)))
    # YouTube events frequently outlast their spoken words and overlap the
    # next cue; clamp every end to the next cue's start.
    clamped: list[tuple[float, float, str]] = []
    for index, (start, end, text) in enumerate(cues):
        next_start = cues[index + 1][0] if index + 1 < len(cues) else None
        resolved_end = end if end >= 0 else start + 2.0
        if next_start is not None:
            resolved_end = min(resolved_end, next_start)
        clamped.append((start, resolved_end, text))
    return _segments_from_cues(clamped)


def _parse_cue_subtitles(text: str) -> tuple[SubtitleSegment, ...]:
    """Parse WebVTT/SRT cue blocks, tolerating settings after the end time."""
    cues: list[tuple[float, float, str]] = []
    blocks = re.split(r"\r?\n\s*\r?\n", text)
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timestamp_index = next(
            (i for i, line in enumerate(lines) if _TIMESTAMP.search(line)), None
        )
        if timestamp_index is None:
            continue
        match = _TIMESTAMP.search(lines[timestamp_index])
        assert match is not None
        start = _cue_seconds(match.group("start"))
        end = _cue_seconds(match.group("end"))
        content = _clean_text(" ".join(lines[timestamp_index + 1:]))
        if not content or end <= start:
            continue
        cues.append((start, end, content))
    return _segments_from_cues(cues)


def _cue_seconds(value: str) -> float:
    hours, minutes, rest = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(rest)


def _clean_text(value: str) -> str:
    return " ".join(_TAG.sub(" ", value).split())


def _finite(value) -> bool:
    return type(value) in {int, float} and math.isfinite(float(value))


def _segments_from_cues(
    cues: Sequence[tuple[float, float, str]],
) -> tuple[SubtitleSegment, ...]:
    segments = []
    previous_end = 0.0
    for start, end, text in sorted(cues):
        if not text or end <= start or start < previous_end:
            continue
        segments.append(SubtitleSegment(start, end, text))
        previous_end = end
    return tuple(segments)
