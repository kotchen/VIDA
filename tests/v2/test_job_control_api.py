from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.v2.bootstrap import build_test_runtime


class _SchedulerSpy:
    def __init__(self):
        self.notifications = 0
    def notify(self):
        self.notifications += 1
    async def start(self, reconcile=None): pass
    async def stop(self, grace_period_sec=30): pass


class JobControlApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = build_test_runtime(Path(self.temp.name), b"k" * 32)
        self.profile = self.runtime.require_provider_profiles().create_profile(
            "Primary", "https://api.test/v1", "secret", "model", 0.1
        )
        self.spy = _SchedulerSpy()
        self.runtime.scheduler = self.spy
        app = FastAPI()
        self.runtime.install(app)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def submit(self):
        return self.runtime.require_episodes().submit_url(
            "https://example.test/media", self.profile.id, "zh", "Episode"
        )

    def test_cancel_queued_is_immediate_idempotent_and_updates_positions(self):
        first, second = self.submit(), self.submit()
        response = self.client.post(f"/api/v2/episodes/{first.episode.id}/cancel")
        repeated = self.client.post(f"/api/v2/episodes/{first.episode.id}/cancel")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.json()["status"], "canceled")
        self.assertEqual(self.runtime.job_repository.queue_position(second.episode.current_job_id), 1)
        self.assertEqual(self.spy.notifications, 0)

    def test_processing_cancel_is_accepted_and_job_cancel_has_historical_detail(self):
        submitted = self.submit()
        job_id = submitted.episode.current_job_id
        self.runtime.job_repository.claim_next("worker", "2026-07-18T00:01:00Z")
        response = self.client.post(f"/api/v2/jobs/{job_id}/cancel")
        detail = self.client.get(f"/api/v2/jobs/{job_id}")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(detail.json()["cancelRequestedAt"], response.json()["cancelRequestedAt"])
        self.assertEqual(detail.json()["attempt"], 1)
        self.assertIsNone(detail.json()["queuePosition"])

    def test_retry_uses_new_attempt_current_revision_and_notifies_after_commit(self):
        submitted = self.submit()
        old_job = submitted.episode.current_job_id
        self.runtime.job_repository.claim_next("worker", "2026-07-18T00:01:00Z")
        self.runtime.job_repository.fail(old_job, "failed", "Failed", "2026-07-18T00:02:00Z")
        updated = self.runtime.require_provider_profiles().update_profile(
            self.profile.id, model_id="new-model"
        )
        response = self.client.post(f"/api/v2/episodes/{submitted.episode.id}/retry")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["attempt"], 2)
        self.assertEqual(response.json()["providerProfileRevisionId"], updated.active_revision_id)
        self.assertEqual(self.runtime.episode_repository.get(submitted.episode.id).current_job_id, response.json()["id"])
        self.assertEqual(self.runtime.job_repository.get_required(old_job).status, "failed")
        self.assertEqual(self.spy.notifications, 1)

    def test_retry_invalid_state_and_concurrent_retry_have_one_winner(self):
        submitted = self.submit()
        invalid = self.client.post(f"/api/v2/episodes/{submitted.episode.id}/retry")
        self.assertEqual(invalid.status_code, 409)
        self.assertEqual(invalid.json()["error"]["code"], "invalid_episode_state")
        self.client.post(f"/api/v2/episodes/{submitted.episode.id}/cancel")
        statuses = []
        barrier = threading.Barrier(3)
        def retry():
            with TestClient(self.client.app) as client:
                barrier.wait()
                statuses.append(client.post(f"/api/v2/episodes/{submitted.episode.id}/retry").status_code)
        threads = [threading.Thread(target=retry) for _ in range(2)]
        for thread in threads: thread.start()
        barrier.wait()
        for thread in threads: thread.join()
        self.assertEqual(sorted(statuses), [202, 409])

    def test_regeneration_targets_only_completed_content_and_conflicts_when_active(self):
        submitted = self.submit()
        self.client.post(f"/api/v2/episodes/{submitted.episode.id}/cancel")
        self.client.post(f"/api/v2/episodes/{submitted.episode.id}/retry")
        job = self.runtime.job_repository.get_required(
            self.runtime.episode_repository.get(submitted.episode.id).current_job_id
        )
        self.runtime.job_repository.claim_next("worker", "2026-07-18T00:03:00Z")
        self.runtime.job_repository.complete(job.id, "2026-07-18T00:04:00Z")
        summary = self.client.post(f"/api/v2/episodes/{submitted.episode.id}/summary/regenerate")
        conflict = self.client.post(f"/api/v2/episodes/{submitted.episode.id}/chapters/regenerate")
        self.assertEqual(summary.status_code, 202)
        self.assertEqual(summary.json()["type"], "regenerate_summary")
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"]["code"], "job_already_active")
        self.assertEqual(self.runtime.episode_repository.get(submitted.episode.id).status, "completed")

    def test_missing_job_and_terminal_cancel_have_exact_errors(self):
        missing = self.client.get("/api/v2/jobs/missing")
        submitted = self.submit()
        self.client.post(f"/api/v2/episodes/{submitted.episode.id}/cancel")
        self.client.post(f"/api/v2/episodes/{submitted.episode.id}/retry")
        old = submitted.episode.current_job_id
        terminal = self.client.post(f"/api/v2/jobs/{old}/cancel")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["error"]["code"], "job_not_found")
        self.assertEqual(terminal.status_code, 200)  # canceled is idempotent historically


if __name__ == "__main__":
    unittest.main()
