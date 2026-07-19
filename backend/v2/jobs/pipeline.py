from __future__ import annotations

import asyncio
import inspect
import shutil
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from ..domain import JobRecord, ProcessingWarning, TranscriptSegmentRecord
from .models import JobCanceled, TranscriptionFailed
from .source_ingest import _remove_attempt


class EpisodePipeline:
    def __init__(
        self,
        *,
        data_dir: Path,
        profiles,
        episodes,
        chapters,
        jobs,
        media,
        source,
        transcriber,
        ai_factory,
        heartbeat_interval_sec: float = 5.0,
        now: Callable[[], str],
    ):
        if heartbeat_interval_sec <= 0:
            raise ValueError("heartbeat_interval_sec must be positive")
        self._data_dir = Path(data_dir).resolve()
        self._profiles = profiles
        self._episodes = episodes
        self._chapters = chapters
        self._jobs = jobs
        self._media = media
        self._source = source
        self._transcriber = transcriber
        self._ai_factory = ai_factory
        self._heartbeat_interval = heartbeat_interval_sec
        self._now = now

    async def execute(self, job: JobRecord, cancel_check) -> None:
        if job.type != "process_episode":
            raise RuntimeError("Unsupported job type")
        self._check_cancel(cancel_check)
        episode = await asyncio.to_thread(self._episodes.get, job.episode_id)
        if episode is None or episode.current_job_id != job.id:
            raise RuntimeError("Episode is unavailable")
        credentials = await asyncio.to_thread(
            self._profiles.get_revision_credentials,
            job.provider_profile_revision_id,
        )
        ai = self._ai_factory(credentials)
        attempt = self._data_dir / "episodes" / episode.id / "attempts" / job.id

        try:
            prepared = await self._stage(
                job, 5, "Preparing source", cancel_check,
                self._source.prepare(episode, attempt, cancel_check),
            )
        except (JobCanceled, asyncio.CancelledError):
            await _remove_attempt(attempt, attempt, self._data_dir)
            raise
        except Exception:
            await _remove_attempt(attempt, attempt, self._data_dir)
            raise
        try:
            transcription = await self._stage(
                job, 20, "Transcribing", cancel_check,
                self._transcriber.transcribe_segments(str(prepared.media_path)),
            )
            if not transcription.segments:
                raise ValueError("empty transcription")
        except (JobCanceled, asyncio.CancelledError):
            await _remove_attempt(attempt, attempt, self._data_dir)
            raise
        except Exception:
            await _remove_attempt(attempt, attempt, self._data_dir)
            raise TranscriptionFailed("Transcription failed") from None

        records = [
            TranscriptSegmentRecord(
                str(uuid4()), episode.id, index, row.start_sec, row.end_sec,
                None, row.text,
            )
            for index, row in enumerate(transcription.segments)
        ]
        try:
            media_path, poster_path = await self._commit_core_files(
                episode.id, job.id, attempt, prepared, cancel_check
            )
            await self._stage(
                job, 60, "Saving transcript", cancel_check,
                asyncio.to_thread(
                    self._episodes.commit_core_output,
                    episode.id,
                    records,
                    language=transcription.language,
                    media_path=media_path,
                    media_content_type=prepared.media_content_type,
                    poster_path=poster_path,
                    poster_content_type=prepared.poster_content_type,
                    duration_sec=prepared.duration_sec,
                    resolution=prepared.resolution,
                    updated_at=self._now(),
                ),
            )
        except BaseException:
            if "media_path" in locals():
                await self._remove_uncommitted_files(media_path, poster_path)
            raise
        finally:
            await _remove_attempt(attempt, attempt, self._data_dir)

        transcript_text = "\n".join(row.text for row in records)
        warnings: list[ProcessingWarning] = []
        optimized = transcript_text
        try:
            optimized = await self._stage(
                job, 65, "Optimizing transcript", cancel_check,
                ai.optimize(transcript_text),
            )
        except (JobCanceled, asyncio.CancelledError):
            raise
        except Exception:
            warnings.append(_warning("optimization"))

        try:
            summary = await self._stage(
                job, 72, "Generating summary", cancel_check,
                ai.summarize(
                    episode.id, optimized, episode.summary_language, episode.title
                ),
            )
        except (JobCanceled, asyncio.CancelledError):
            raise
        except Exception:
            warnings.append(_warning("summary"))
        else:
            await self._stage(
                job, 82, "Saving summary", cancel_check,
                asyncio.to_thread(self._episodes.replace_summary, episode.id, summary),
            )

        try:
            generated = await self._stage(
                job, 85, "Generating chapters", cancel_check,
                ai.generate_chapters(
                    episode.id, optimized, prepared.duration_sec, poster_path
                ),
            )
        except (JobCanceled, asyncio.CancelledError):
            raise
        except Exception:
            warnings.append(_warning("chapters"))
        else:
            await self._stage(
                job, 94, "Saving chapters", cancel_check,
                asyncio.to_thread(
                    self._chapters.replace_generated, episode.id, generated
                ),
            )

        await self._stage(
            job, 95, "Finalizing", cancel_check,
            asyncio.to_thread(self._episodes.set_warnings, episode.id, warnings),
        )
        self._check_cancel(cancel_check)
        await asyncio.to_thread(self._jobs.complete, job.id, self._now())

    async def _commit_core_files(
        self, episode_id, job_id, attempt, prepared, cancel_check
    ) -> tuple[str, str]:
        self._check_cancel(cancel_check)
        media_staged = Path(prepared.media_path)
        try:
            media_staged.resolve().relative_to(attempt.resolve())
        except ValueError:
            copied = attempt / f"committed-source{media_staged.suffix.lower()}"
            await asyncio.to_thread(copied.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(shutil.copyfile, media_staged, copied)
            media_staged = copied
        artifact_id = uuid4().hex
        final_media = (
            self._data_dir / "episodes" / episode_id / "artifacts"
            / f"{artifact_id}{media_staged.suffix.lower()}"
        )
        poster_staged = Path(prepared.poster_path)
        final_poster = (
            self._data_dir / "episodes" / episode_id / "poster"
            / f"{artifact_id}{poster_staged.suffix.lower()}"
        )
        committed: list[Path] = []
        try:
            await asyncio.to_thread(self._media.commit_file, media_staged, final_media)
            committed.append(final_media)
            self._check_cancel(cancel_check)
            await asyncio.to_thread(self._media.commit_file, poster_staged, final_poster)
            committed.append(final_poster)
            self._check_cancel(cancel_check)
        except BaseException:
            await self._unlink_paths(committed)
            raise
        return (
            final_media.relative_to(self._data_dir).as_posix(),
            final_poster.relative_to(self._data_dir).as_posix(),
        )

    async def _remove_uncommitted_files(self, *relative_paths: str) -> None:
        candidates = [self._data_dir / Path(value) for value in relative_paths]
        await self._unlink_paths(candidates)

    async def _unlink_paths(self, paths) -> None:
        root = self._data_dir

        def remove() -> None:
            for raw in paths:
                candidate = Path(raw)
                try:
                    resolved = candidate.resolve()
                except OSError:
                    continue
                if root not in resolved.parents or candidate.is_symlink():
                    continue
                if resolved.is_file():
                    resolved.unlink(missing_ok=True)

        await asyncio.to_thread(remove)

    async def _stage(
        self, job, progress: int, message: str, cancel_check, awaitable
    ):
        try:
            self._check_cancel(cancel_check)
            await asyncio.to_thread(
                self._jobs.update_progress, job.id, progress, message, self._now()
            )
        except BaseException:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            raise
        operation = asyncio.ensure_future(awaitable)
        try:
            while not operation.done():
                done, _ = await asyncio.wait(
                    (operation,), timeout=self._heartbeat_interval
                )
                if done:
                    break
                await asyncio.to_thread(
                    self._jobs.update_progress,
                    job.id,
                    progress,
                    message,
                    self._now(),
                )
            result = operation.result()
        except BaseException:
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            raise
        self._check_cancel(cancel_check)
        return result

    @staticmethod
    def _check_cancel(cancel_check) -> None:
        if cancel_check():
            raise JobCanceled


def _warning(stage: str) -> ProcessingWarning:
    return ProcessingWarning(stage, "ai_stage_failed", f"{stage.title()} unavailable")
