from __future__ import annotations

import asyncio
import logging
import math
import os
from dataclasses import dataclass
from typing import Optional

from faster_whisper import WhisperModel


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TranscriptSegment:
    start_sec: float
    end_sec: float
    text: str


@dataclass(frozen=True)
class StructuredTranscription:
    language: str
    language_probability: float
    segments: tuple[TranscriptSegment, ...]


class Transcriber:
    """Transcribe audio with Faster-Whisper."""

    def __init__(self, model_size: str = "base"):
        self.model_size = model_size
        self.model = None
        self._model_lock = asyncio.Lock()
        self.last_detected_language = None

    async def _load_model(self) -> None:
        if self.model is not None:
            return
        async with self._model_lock:
            if self.model is not None:
                return
            logger.info("正在加载Whisper模型")
            try:
                self.model = await asyncio.to_thread(
                    WhisperModel,
                    self.model_size,
                    device="cpu",
                    compute_type="int8",
                )
            except Exception as exc:
                logger.error("模型加载失败")
                raise Exception(f"模型加载失败: {exc}") from None
            logger.info("模型加载完成")

    async def transcribe(self, audio_path: str, language: Optional[str] = None) -> str:
        """Return the exact legacy Markdown transcript format."""
        try:
            result = await self._raw_transcription(audio_path, language)
            lines = [
                "# Video Transcription",
                "",
                f"**Detected Language:** {result.language}",
                f"**Language Probability:** {result.language_probability:.2f}",
                "",
                "## Transcription Content",
                "",
            ]
            for segment in result.segments:
                lines.extend(
                    [
                        f"**[{self._format_time(segment.start_sec)} - "
                        f"{self._format_time(segment.end_sec)}]**",
                        "",
                        segment.text,
                        "",
                    ]
                )
            logger.info("转录完成")
            return "\n".join(lines)
        except Exception as exc:
            logger.error("转录失败")
            raise Exception(f"转录失败: {exc}") from None

    async def transcribe_segments(
        self, audio_path: str, language: Optional[str] = None
    ) -> StructuredTranscription:
        """Return immutable timestamped segments for v2 persistence."""
        try:
            return await self._raw_transcription(audio_path, language)
        except Exception:
            logger.error("Structured transcription failed")
            raise Exception("Transcription failed") from None

    async def _raw_transcription(
        self, audio_path: str, language: Optional[str]
    ) -> StructuredTranscription:
        if not os.path.exists(audio_path):
            raise Exception(f"Audio file not found: {audio_path}")
        await self._load_model()
        logger.info("Starting audio transcription")

        def materialize() -> StructuredTranscription:
            segments, info = self.model.transcribe(
                audio_path,
                language=language,
                beam_size=5,
                best_of=5,
                temperature=[0.0, 0.2, 0.4],
                vad_filter=True,
                vad_parameters={
                    "min_silence_duration_ms": 900,
                    "speech_pad_ms": 300,
                },
                no_speech_threshold=0.7,
                compression_ratio_threshold=2.3,
                log_prob_threshold=-1.0,
                condition_on_previous_text=False,
            )
            rows = tuple(
                TranscriptSegment(
                    _strict_timestamp(segment.start),
                    _strict_timestamp(segment.end),
                    segment.text.strip(),
                )
                for segment in segments
            )
            return StructuredTranscription(
                str(info.language), float(info.language_probability), rows
            )

        result = await asyncio.to_thread(materialize)
        self.last_detected_language = result.language
        return result

    @staticmethod
    def _format_time(seconds: float) -> str:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        whole_seconds = int(seconds % 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}"
        return f"{minutes:02d}:{whole_seconds:02d}"

    @staticmethod
    def get_supported_languages() -> list[str]:
        return [
            "zh", "en", "ja", "ko", "es", "fr", "de", "it", "pt", "ru",
            "ar", "hi", "th", "vi", "tr", "pl", "nl", "sv", "da", "no",
        ]

    def get_detected_language(
        self, transcript_text: Optional[str] = None
    ) -> Optional[str]:
        if self.last_detected_language:
            return self.last_detected_language
        if transcript_text and "**Detected Language:**" in transcript_text:
            for line in transcript_text.split("\n"):
                if "**Detected Language:**" in line:
                    language = line.split(":")[-1].strip()
                    return language or None
        return None


def _strict_timestamp(value) -> float:
    if type(value) not in {int, float} or not math.isfinite(float(value)):
        raise ValueError("invalid transcript timestamp")
    return float(value)
