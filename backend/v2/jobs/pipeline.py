from __future__ import annotations

import asyncio
import inspect
import math
import shutil
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from ..domain import JobRecord, ProcessingWarning, TranscriptSegmentRecord
from .blocking import consume_current_cancellation, owned_to_thread, run_owned_to_thread
from .models import JobCanceled, PipelinePersistenceError, TranscriptionFailed
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
        if job.type in {"regenerate_summary", "regenerate_chapters"}:
            await self._execute_regeneration(job, cancel_check)
            return
        if job.type != "process_episode":
            raise RuntimeError("Unsupported job type")
        self._check_cancel(cancel_check)
        episode = await run_owned_to_thread(self._episodes.get, job.episode_id)
        if episode is None or episode.current_job_id != job.id:
            raise RuntimeError("Episode is unavailable")
        credentials = await run_owned_to_thread(
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
            validated_segments = _validate_segments(transcription.segments)
        except (JobCanceled, asyncio.CancelledError):
            await _remove_attempt(attempt, attempt, self._data_dir)
            raise
        except PipelinePersistenceError:
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
            for index, row in enumerate(validated_segments)
        ]
        core_committed = False
        try:
            media_path, poster_path = await self._commit_core_files(
                episode.id, job.id, attempt, prepared, cancel_check
            )
            task_canceled, user_canceled = await self._commit_core_output(
                job,
                cancel_check,
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
            )
            core_committed = True
            if task_canceled:
                raise asyncio.CancelledError
            if user_canceled:
                raise JobCanceled
        except BaseException:
            if not core_committed and "media_path" in locals():
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
        except PipelinePersistenceError:
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
            await self._stage(
                job, 82, "Saving summary", cancel_check,
                run_owned_to_thread(self._episodes.replace_summary, episode.id, summary),
            )
        except (JobCanceled, asyncio.CancelledError):
            raise
        except PipelinePersistenceError:
            raise
        except Exception:
            warnings.append(_warning("summary"))

        try:
            generated = await self._stage(
                job, 85, "Generating chapters", cancel_check,
                ai.generate_chapters(
                    episode.id, optimized, prepared.duration_sec, poster_path
                ),
            )
            await self._stage(
                job, 94, "Saving chapters", cancel_check,
                run_owned_to_thread(
                    self._chapters.replace_generated, episode.id, generated
                ),
            )
        except (JobCanceled, asyncio.CancelledError):
            raise
        except PipelinePersistenceError:
            raise
        except Exception:
            warnings.append(_warning("chapters"))

        await self._stage(
            job, 95, "Finalizing", cancel_check,
            run_owned_to_thread(self._episodes.set_warnings, episode.id, warnings),
        )
        self._check_cancel(cancel_check)
        try:
            await run_owned_to_thread(self._jobs.complete, job.id, self._now())
        except asyncio.CancelledError:
            raise
        except Exception:
            raise PipelinePersistenceError("Pipeline persistence failed") from None

    async def _execute_regeneration(self, job: JobRecord, cancel_check) -> None:
        self._check_cancel(cancel_check)
        episode = await run_owned_to_thread(self._episodes.get, job.episode_id)
        if episode is None or episode.status != "completed":
            raise RuntimeError("Completed episode is unavailable")
        segments = await run_owned_to_thread(
            self._episodes.get_transcript, episode.id
        )
        if not segments:
            raise RuntimeError("Episode transcript is unavailable")
        credentials = await run_owned_to_thread(
            self._profiles.get_revision_credentials,
            job.provider_profile_revision_id,
        )
        ai = self._ai_factory(credentials)
        transcript = "\n".join(row.text for row in segments)
        optimized = await self._stage(
            job, 20, "Optimizing transcript", cancel_check, ai.optimize(transcript)
        )
        if job.type == "regenerate_summary":
            summary = await self._stage(
                job, 60, "Generating summary", cancel_check,
                ai.summarize(
                    episode.id, optimized, episode.summary_language, episode.title
                ),
            )
            await self._complete_regeneration(
                job,
                "Saving summary",
                cancel_check,
                self._jobs.complete_regeneration_summary,
                job.id,
                summary,
                self._now(),
            )
        else:
            generated = await self._stage(
                job, 60, "Generating chapters", cancel_check,
                ai.generate_chapters(
                    episode.id,
                    optimized,
                    episode.duration_sec or 0,
                    episode.poster_path,
                ),
            )
            await self._complete_regeneration(
                job,
                "Saving chapters",
                cancel_check,
                self._jobs.complete_regeneration_chapters,
                job.id,
                generated,
                self._now(),
            )

    async def _complete_regeneration(
        self, job, message: str, cancel_check, operation, *args
    ) -> None:
        self._check_cancel(cancel_check)
        try:
            canceled, _ = await self._update_progress(job.id, 90, message)
            if canceled:
                raise asyncio.CancelledError
            self._check_cancel(cancel_check)
            await run_owned_to_thread(operation, *args)
        except JobCanceled:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise PipelinePersistenceError("Pipeline persistence failed") from None

    async def _commit_core_files(
        self, episode_id, job_id, attempt, prepared, cancel_check
    ) -> tuple[str, str]:
        self._check_cancel(cancel_check)
        media_staged = Path(prepared.media_path)
        try:
            media_staged.resolve().relative_to(attempt.resolve())
        except ValueError:
            copied = attempt / f"committed-source{media_staged.suffix.lower()}"
            await run_owned_to_thread(copied.parent.mkdir, parents=True, exist_ok=True)
            await run_owned_to_thread(shutil.copyfile, media_staged, copied)
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
            canceled, _ = await owned_to_thread(
                self._media.commit_file, media_staged, final_media
            )
            committed.append(final_media)
            if canceled:
                raise asyncio.CancelledError
            self._check_cancel(cancel_check)
            canceled, _ = await owned_to_thread(
                self._media.commit_file, poster_staged, final_poster
            )
            committed.append(final_poster)
            if canceled:
                raise asyncio.CancelledError
            self._check_cancel(cancel_check)
        except BaseException:
            await self._unlink_paths(committed)
            raise
        return (
            final_media.relative_to(self._data_dir).as_posix(),
            final_poster.relative_to(self._data_dir).as_posix(),
        )

    async def _commit_core_output(
        self, job, cancel_check, episode_id, records, **metadata
    ) -> tuple[bool, bool]:
        self._check_cancel(cancel_check)
        canceled, _ = await self._update_progress(
            job.id, 60, "Saving transcript"
        )
        if canceled:
            raise asyncio.CancelledError
        operation = asyncio.create_task(
            run_owned_to_thread(
                self._episodes.commit_core_output,
                episode_id,
                records,
                **metadata,
            )
        )
        task_canceled = False
        user_canceled = False
        while not operation.done():
            try:
                await asyncio.wait_for(
                    asyncio.shield(operation), self._heartbeat_interval
                )
                done = True
            except asyncio.TimeoutError:
                done = False
            except asyncio.CancelledError:
                task_canceled = True
                consume_current_cancellation()
                continue
            if cancel_check():
                user_canceled = True
        try:
            operation.result()
        except BaseException:
            if task_canceled:
                raise asyncio.CancelledError from None
            raise
        return task_canceled, user_canceled or cancel_check()

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

        canceled, _ = await owned_to_thread(remove)
        if canceled:
            raise asyncio.CancelledError

    async def _stage(
        self, job, progress: int, message: str, cancel_check, awaitable
    ):
        try:
            self._check_cancel(cancel_check)
            canceled, _ = await self._update_progress(job.id, progress, message)
            if canceled:
                raise asyncio.CancelledError
        except BaseException:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            raise
        operation = asyncio.ensure_future(awaitable)
        task_canceled = False
        stage_error = None
        while not operation.done():
            try:
                done, _ = await asyncio.wait(
                    (operation,), timeout=self._heartbeat_interval
                )
            except asyncio.CancelledError:
                task_canceled = True
                consume_current_cancellation()
                continue
            if done or stage_error is not None:
                continue
            try:
                canceled, _ = await self._update_progress(job.id, progress, message)
                task_canceled = task_canceled or canceled
            except asyncio.CancelledError:
                task_canceled = True
            except BaseException as exc:
                stage_error = exc
        operation_error = None
        result = None
        try:
            result = operation.result()
        except BaseException as exc:
            operation_error = exc
        if task_canceled:
            raise asyncio.CancelledError from None
        if stage_error is not None:
            raise stage_error from None
        if operation_error is not None:
            raise operation_error
        self._check_cancel(cancel_check)
        return result

    async def _update_progress(self, job_id: str, progress: int, message: str):
        try:
            return await owned_to_thread(
                self._jobs.update_progress, job_id, progress, message, self._now()
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            raise PipelinePersistenceError("Pipeline persistence failed") from None

    @staticmethod
    def _check_cancel(cancel_check) -> None:
        if cancel_check():
            raise JobCanceled


def _warning(stage: str) -> ProcessingWarning:
    return ProcessingWarning(stage, "ai_stage_failed", f"{stage.title()} unavailable")


def _validate_segments(segments):
    validated = []
    previous_end = 0.0
    for index, segment in enumerate(segments):
        start = segment.start_sec
        end = segment.end_sec
        text = segment.text
        if (
            type(start) not in {int, float}
            or type(end) not in {int, float}
            or not math.isfinite(float(start))
            or not math.isfinite(float(end))
            or float(start) < 0
            or float(end) <= float(start)
            or (index > 0 and float(start) < previous_end)
            or not isinstance(text, str)
        ):
            raise ValueError("invalid transcript segment")
        normalized_text = text.strip()
        if not normalized_text or len(normalized_text) > 100_000:
            raise ValueError("invalid transcript segment")
        validated.append(
            TranscriptSegmentRecord(
                "", "", index, float(start), float(end), None, normalized_text
            )
        )
        previous_end = float(end)
    return validated
