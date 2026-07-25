from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from backend.v2.bootstrap import build_test_runtime
from backend.v2.domain import SummaryRecord


class EventPublicationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = build_test_runtime(Path(self.temp.name), b"e" * 32)

    def tearDown(self):
        self.temp.cleanup()

    async def test_profile_write_publishes_only_invalidation(self):
        async with self.runtime.events.subscribe() as queue:
            self.runtime.require_provider_profiles().create_profile(
                "Primary", "https://api.test/v1", "secret-value", "model", 0.1
            )
            event = await asyncio.wait_for(queue.get(), 0.1)

        self.assertEqual(event.type, "profiles.invalidated")
        self.assertEqual(event.data, {})

    async def test_submission_publishes_safe_episode_state(self):
        profile = self.runtime.require_provider_profiles().create_profile(
            "Primary", "https://api.test/v1", "secret-value", "model", 0.1
        )

        async with self.runtime.events.subscribe() as queue:
            submission = self.runtime.require_episodes().submit_url(
                "https://media.test/secret?token=hidden",
                profile.id,
                "zh",
                "Episode",
            )
            event = await asyncio.wait_for(queue.get(), 0.1)

        self.assertEqual(event.type, "episode.updated")
        self.assertEqual(
            event.data,
            {
                "episodeId": submission.episode.id,
                "status": "queued",
                "progress": 0,
            },
        )
        self.assertNotIn("media.test", repr(event))
        self.assertNotIn("secret-value", repr(event))

    async def test_job_progress_and_completion_publish_committed_state(self):
        profile = self.runtime.require_provider_profiles().create_profile(
            "Primary", "https://api.test/v1", "secret-value", "model", 0.1
        )
        submission = self.runtime.require_episodes().submit_url(
            "https://media.test/one", profile.id, "en", "Episode"
        )
        job_id = submission.episode.current_job_id
        self.assertIsNotNone(job_id)

        async with self.runtime.events.subscribe() as queue:
            self.runtime.job_repository.claim_next(
                "worker", "2026-07-25T00:00:00Z"
            )
            await _drain(queue, 2)
            self.runtime.job_repository.update_progress(
                job_id, 40, "Transcribing", "2026-07-25T00:00:01Z"
            )
            progress_events = await _drain(queue, 2)
            self.runtime.job_repository.complete(
                job_id, "2026-07-25T00:00:02Z"
            )
            completion_events = await _drain(queue, 3)

        self.assertEqual(
            {(event.type, event.data["progress"]) for event in progress_events},
            {("job.updated", 40), ("episode.updated", 40)},
        )
        self.assertIn(
            ("dashboard.invalidated", ()),
            [(event.type, tuple(event.data.items())) for event in completion_events],
        )
        self.assertTrue(
            all("errorMessage" not in event.data for event in completion_events)
        )

    async def test_cancel_and_retry_publish_terminal_then_queued_state(self):
        profile = self.runtime.require_provider_profiles().create_profile(
            "Primary", "https://api.test/v1", "secret-value", "model", 0.1
        )
        submission = self.runtime.require_episodes().submit_url(
            "https://media.test/one", profile.id, "en", "Episode"
        )

        async with self.runtime.events.subscribe() as queue:
            self.runtime.require_episodes().cancel_episode(submission.episode.id)
            canceled = await _drain(queue, 3)
            self.runtime.require_episodes().retry(submission.episode.id)
            retried = await _drain(queue, 2)

        self.assertEqual(
            [event.type for event in canceled],
            ["job.updated", "episode.updated", "dashboard.invalidated"],
        )
        self.assertEqual(
            {(event.type, event.data["status"]) for event in retried},
            {("job.updated", "queued"), ("episode.updated", "queued")},
        )

    async def test_regeneration_completion_invalidates_content(self):
        profile = self.runtime.require_provider_profiles().create_profile(
            "Primary", "https://api.test/v1", "secret-value", "model", 0.1
        )
        submission = self.runtime.require_episodes().submit_url(
            "https://media.test/one", profile.id, "en", "Episode"
        )
        primary_job_id = submission.episode.current_job_id
        self.runtime.job_repository.claim_next(
            "worker", "2026-07-25T00:00:00Z"
        )
        self.runtime.job_repository.complete(
            primary_job_id, "2026-07-25T00:00:01Z"
        )

        regeneration = self.runtime.require_episodes().regenerate(
            submission.episode.id, "regenerate_summary"
        )
        self.runtime.job_repository.claim_next(
            "worker", "2026-07-25T00:00:02Z"
        )

        async with self.runtime.events.subscribe() as queue:
            self.runtime.job_repository.complete_regeneration_summary(
                regeneration.id,
                SummaryRecord(
                    submission.episode.id, "Summary", 1, 2, 90, "VIDA"
                ),
                "2026-07-25T00:00:03Z",
            )
            events = await _drain(queue, 2)

        self.assertEqual(
            [event.type for event in events],
            ["job.updated", "dashboard.invalidated"],
        )

    async def test_episode_delete_publishes_deleted_then_dashboard_invalidation(self):
        profile = self.runtime.require_provider_profiles().create_profile(
            "Primary", "https://api.test/v1", "secret-value", "model", 0.1
        )
        submission = self.runtime.require_episodes().submit_url(
            "https://media.test/one", profile.id, "en", "Episode"
        )
        with self.runtime.database.transaction(immediate=True) as conn:
            conn.execute(
                "UPDATE episodes SET status='canceled' WHERE id=?",
                (submission.episode.id,),
            )
            conn.execute(
                "UPDATE jobs SET status='canceled' WHERE episode_id=?",
                (submission.episode.id,),
            )

        async with self.runtime.events.subscribe() as queue:
            self.runtime.require_episodes().delete_episode(submission.episode.id)
            events = await _drain(queue, 2)

        self.assertEqual(
            [(event.type, event.data) for event in events],
            [
                ("episode.deleted", {"episodeId": submission.episode.id}),
                ("dashboard.invalidated", {}),
            ],
        )

    async def test_chapter_mutations_publish_episode_update(self):
        profile = self.runtime.require_provider_profiles().create_profile(
            "Primary", "https://api.test/v1", "secret-value", "model", 0.1
        )
        submission = self.runtime.require_episodes().submit_url(
            "https://media.test/one", profile.id, "en", "Episode"
        )
        episode_id = submission.episode.id
        with self.runtime.database.transaction(immediate=True) as conn:
            conn.execute(
                "UPDATE episodes SET duration_sec=100 WHERE id=?",
                (episode_id,),
            )
        service = self.runtime.require_episodes()

        async with self.runtime.events.subscribe() as queue:
            chapter = service.create_chapter(episode_id, 10, "Manual")
            created = await asyncio.wait_for(queue.get(), 0.1)
            service.update_chapter(
                episode_id,
                chapter.id,
                start_sec=None,
                title=None,
                bookmarked=True,
            )
            updated = await asyncio.wait_for(queue.get(), 0.1)
            service.delete_chapter(episode_id, chapter.id)
            deleted = await asyncio.wait_for(queue.get(), 0.1)

        self.assertEqual(
            [(event.type, event.data) for event in (created, updated, deleted)],
            [
                (
                    "episode.updated",
                    {"episodeId": episode_id, "status": "queued", "progress": 0},
                )
            ]
            * 3,
        )


async def _drain(queue, count):
    return [
        await asyncio.wait_for(queue.get(), 0.1)
        for _ in range(count)
    ]


if __name__ == "__main__":
    unittest.main()
