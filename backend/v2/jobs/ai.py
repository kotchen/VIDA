from __future__ import annotations

import asyncio
import json
import math
import re
import inspect
from collections.abc import Mapping, Sequence
from typing import Protocol
from uuid import uuid4

from openai import AsyncOpenAI

if __package__ == "v2.jobs":
    from llm_sanitize import strip_llm_artifacts
    from summarizer import Summarizer
else:
    from ...llm_sanitize import strip_llm_artifacts
    from ...summarizer import Summarizer

from ..domain import ChapterRecord, SummaryRecord
from ..services.provider_profiles import ProviderRevisionCredentials


class AIExecutionError(RuntimeError):
    """A sanitized AI-stage failure safe to persist as a warning."""


class CompletionRunner(Protocol):
    async def complete(
        self,
        credentials: ProviderRevisionCredentials,
        messages: Sequence[Mapping[str, str]],
        response_format: Mapping[str, str],
    ) -> str: ...


class OpenAICompletionRunner:
    """Create and close one provider client for each request."""

    def __init__(self, timeout_sec: float = 300.0, client_factory=AsyncOpenAI):
        self._timeout_sec = timeout_sec
        self._client_factory = client_factory

    async def complete(self, credentials, messages, response_format) -> str:
        client = self._client_factory(
            api_key=credentials.api_key,
            base_url=credentials.base_url,
            timeout=self._timeout_sec,
            max_retries=0,
        )
        error = None
        response = None
        canceled = False
        try:
            response = await client.chat.completions.create(
                model=credentials.model_id,
                temperature=credentials.temperature,
                messages=list(messages),
                response_format=dict(response_format),
            )
        except asyncio.CancelledError as exc:
            error = exc
            canceled = True
            _consume_current_cancellation()
        except BaseException as exc:
            error = exc
        close_task = asyncio.create_task(_call_close(client))
        canceled, _ = await _await_task_safely(close_task, canceled)
        if canceled:
            raise asyncio.CancelledError from None
        if error is not None:
            raise error
        return response.choices[0].message.content or ""


class AIProcessor:
    def __init__(
        self,
        credentials: ProviderRevisionCredentials,
        *,
        runner: CompletionRunner | None = None,
        summarizer_factory=Summarizer,
        timeout_sec: float = 300.0,
    ):
        if timeout_sec <= 0:
            raise ValueError("timeout_sec must be positive")
        self._credentials = credentials
        self._runner = runner or OpenAICompletionRunner(timeout_sec)
        self._summarizer_factory = summarizer_factory
        self._timeout_sec = timeout_sec

    async def optimize(self, transcript: str) -> str:
        credentials = self._credentials
        try:
            summarizer = self._summarizer_factory(
                api_key=credentials.api_key,
                base_url=credentials.base_url,
                model=credentials.model_id,
                temperature=credentials.temperature,
                raise_on_error=True,
                request_timeout_sec=self._timeout_sec,
            )
        except Exception:
            raise AIExecutionError("AI request failed") from None

        def run() -> str:
            return asyncio.run(summarizer.optimize_transcript(transcript))

        operation = asyncio.create_task(asyncio.to_thread(run))
        try:
            result = await asyncio.wait_for(
                asyncio.shield(operation), timeout=self._timeout_sec
            )
        except asyncio.CancelledError:
            _consume_current_cancellation()
            canceled = await _close_summarizer(summarizer, True)
            canceled, _ = await _await_task_safely(operation, canceled)
            raise asyncio.CancelledError from None
        except asyncio.TimeoutError:
            canceled = await _close_summarizer(summarizer, False)
            canceled, _ = await _await_task_safely(operation, canceled)
            if canceled:
                raise asyncio.CancelledError from None
            raise AIExecutionError("AI request failed") from None
        except Exception:
            canceled = await _close_summarizer(summarizer, False)
            if canceled:
                raise asyncio.CancelledError from None
            raise AIExecutionError("AI request failed") from None
        canceled = await _close_summarizer(summarizer, False)
        if canceled:
            raise asyncio.CancelledError
        return result

    async def summarize(
        self,
        episode_id: str,
        transcript: str,
        target_language: str,
        title: str,
    ) -> SummaryRecord:
        messages = (
            {
                "role": "system",
                "content": (
                    "Return one strict JSON object with content (plain-text summary), "
                    "keyPoints (non-negative integer), and confidence (numeric 0-100)."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Language: {_language_name(target_language)}\n"
                    f"Title: {title}\n\n{transcript}"
                ),
            },
        )
        payload = await self._request(messages)
        try:
            document = json.loads(payload)
            if not isinstance(document, dict) or set(document) != {
                "content", "keyPoints", "confidence"
            }:
                raise ValueError
            content = _plain_text(document.get("content"))
            key_points = document.get("keyPoints")
            confidence = document.get("confidence")
            if type(key_points) is not int or key_points < 0:
                raise ValueError
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                raise ValueError
            if not math.isfinite(float(confidence)):
                raise ValueError
            confidence_value = max(0, min(100, round(float(confidence))))
        except (TypeError, ValueError, json.JSONDecodeError):
            raise AIExecutionError("Summary output is invalid") from None
        return SummaryRecord(
            episode_id=episode_id,
            content=content,
            read_time_min=max(1, math.ceil(len(content) / 400)),
            key_points=key_points,
            confidence=confidence_value,
            generated_by="VIDA",
        )

    async def generate_chapters(
        self,
        episode_id: str,
        transcript: str,
        duration_sec: float,
        thumbnail_path: str | None,
        title: str = "",
        language: str = "",
    ) -> list[ChapterRecord]:
        duration_hint = max(0, int(duration_sec))
        messages = (
            {
                "role": "system",
                "content": (
                    "You split a video transcript into chapters. Return one strict "
                    "JSON object with a chapters array and no other keys. Each item "
                    "must contain only startSec and title, ordered by startSec. "
                    "Rules: startSec is seconds from the video start; the first "
                    "chapter starts at 0; every startSec is smaller than the video "
                    "duration; create about one chapter per 3 to 8 minutes of video; "
                    "each title is a short descriptive phrase of at most 40 "
                    f"characters written in {_language_name(language) or 'the transcript language'}; "
                    "never return an empty chapters array. Example for a 600 second "
                    'video: {"chapters": [{"startSec": 0, "title": "Opening '
                    'remarks"}, {"startSec": 245, "title": "Key argument"}, '
                    '{"startSec": 502, "title": "Wrap-up"}]}'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Video title: {title}\n"
                    f"Video duration: {duration_hint} seconds\n\n"
                    f"Transcript:\n{transcript}"
                ),
            },
        )
        payload = await self._request(messages)
        try:
            document = json.loads(payload)
            if not isinstance(document, dict) or set(document) != {"chapters"}:
                raise ValueError
            values = document["chapters"]
            if not isinstance(values, list):
                raise ValueError
            starts: list[float] = []
            titles: list[str] = []
            for value in values:
                if not isinstance(value, dict) or set(value) != {"startSec", "title"}:
                    raise ValueError
                raw_start = value["startSec"]
                if isinstance(raw_start, bool) or not isinstance(raw_start, (int, float)):
                    raise ValueError
                start = float(raw_start)
                title = _one_line_title(value["title"])
                if (
                    not math.isfinite(start)
                    or start < 0
                    or start > duration_sec
                    or (starts and start <= starts[-1])
                ):
                    raise ValueError
                starts.append(start)
                titles.append(title)
            if not starts:
                raise ValueError
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise AIExecutionError("Chapter output is invalid") from None
        return [
            ChapterRecord(
                id=str(uuid4()),
                episode_id=episode_id,
                start_sec=start,
                title=titles[index],
                duration_sec=(
                    starts[index + 1] - start
                    if index + 1 < len(starts)
                    else duration_sec - start
                ),
                thumbnail_path=thumbnail_path,
                bookmarked=False,
                source="generated",
            )
            for index, start in enumerate(starts)
        ]

    async def _request(self, messages) -> str:
        try:
            return await asyncio.wait_for(
                self._runner.complete(
                    self._credentials, messages, {"type": "json_object"}
                ),
                timeout=self._timeout_sec,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            raise AIExecutionError("AI request failed") from None


def _plain_text(value) -> str:
    if not isinstance(value, str):
        raise ValueError
    text = strip_llm_artifacts(value).strip()
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"!?\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"(?m)^\s*(?:[-*+] |\d+[.)] )", "", text)
    text = re.sub(r"[*_~`]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        raise ValueError
    return text


_LANGUAGE_NAMES = {
    "zh": "Chinese",
    "zh-hans": "Simplified Chinese",
    "zh-cn": "Simplified Chinese",
    "zh-sg": "Simplified Chinese",
    "zh-hant": "Traditional Chinese",
    "zh-tw": "Traditional Chinese",
    "zh-hk": "Traditional Chinese",
    "en": "English",
    "ja": "Japanese",
    "es": "Spanish",
}


def _language_name(code: str) -> str:
    """Map a submission language code to the name an LLM understands."""
    normalized = (code or "").strip().lower()
    return _LANGUAGE_NAMES.get(normalized, code or "")


def _one_line_title(value) -> str:
    title = _plain_text(value)
    title = " ".join(title.split())
    if not title:
        raise ValueError
    return title


async def _close_summarizer(summarizer, canceled: bool) -> bool:
    client = getattr(summarizer, "client", None)
    close = getattr(client, "close", None)
    if close is None:
        return canceled
    task = asyncio.create_task(asyncio.to_thread(close))
    canceled, _ = await _await_task_safely(task, canceled)
    return canceled


async def _call_close(client) -> None:
    result = client.close()
    if inspect.isawaitable(result):
        await result


async def _await_task_safely(task: asyncio.Task, canceled: bool = False):
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            canceled = True
            _consume_current_cancellation()
    try:
        result = task.result()
    except BaseException:
        if canceled:
            raise asyncio.CancelledError from None
        raise
    return canceled, result


def _consume_current_cancellation() -> None:
    current = asyncio.current_task()
    if current is not None:
        current.uncancel()
