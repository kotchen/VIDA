from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from backend.v2.container import V2Runtime
from backend.v2.jobs.scheduler import Scheduler
from backend.v2.services.media import MediaService, UnsafeMediaPath


class ReferenceRepository:
    def __init__(self, paths=()):
        self.paths = tuple(paths)

    def committed_paths(self):
        return self.paths


class FileRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name) / "data" / "v2"
        self.data.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_commit_file_is_atomic_and_bounded_to_data_root(self):
        staged = self.data / "episodes/e1/attempts/j1/media.mp4"
        final = self.data / "episodes/e1/artifacts/media-j1.mp4"
        staged.parent.mkdir(parents=True)
        staged.write_bytes(b"media")
        service = MediaService(self.data, [])
        service.commit_file(staged, final)
        self.assertFalse(staged.exists())
        self.assertEqual(final.read_bytes(), b"media")
        outside = Path(self.temp.name) / "outside.mp4"
        with self.assertRaises(UnsafeMediaPath):
            service.commit_file(final, outside)
        cross_episode = self.data / "episodes/e2/artifacts/media.mp4"
        with self.assertRaises(UnsafeMediaPath):
            service.commit_file(final, cross_episode)
        source_staged = self._write("episodes/e1/source/not-an-attempt.mp4", b"x")
        with self.assertRaises(UnsafeMediaPath):
            service.commit_file(source_staged, self.data / "episodes/e1/artifacts/x.mp4")

    def test_reconcile_removes_parts_attempts_and_unreferenced_artifacts_but_preserves_references_and_sources(self):
        source = self._write("episodes/e1/source/upload.mp4", b"source")
        committed = self._write("episodes/e1/artifacts/media.mp4", b"keep")
        poster = self._write("episodes/e1/poster/poster.jpg", b"keep")
        orphan_artifact = self._write("episodes/e1/artifacts/orphan.json", b"orphan")
        attempt = self._write("episodes/e1/attempts/j1/file.tmp", b"attempt")
        partial = self._write("episodes/e1/source/failed.part", b"part")
        service = MediaService(
            self.data,
            [ReferenceRepository(["episodes/e1/source/upload.mp4", "episodes/e1/artifacts/media.mp4", "episodes/e1/poster/poster.jpg"])],
        )
        service.reconcile_orphans()
        self.assertTrue(source.exists())
        self.assertTrue(committed.exists())
        self.assertTrue(poster.exists())
        self.assertFalse(orphan_artifact.exists())
        self.assertFalse(attempt.exists())
        self.assertFalse(partial.exists())

    def test_reconciliation_ignores_malicious_repository_paths_and_symlink_escapes(self):
        outside = Path(self.temp.name) / "outside.txt"
        outside.write_bytes(b"outside")
        service = MediaService(self.data, [ReferenceRepository(["../outside.txt", str(outside)])])
        service.reconcile_orphans()
        self.assertEqual(outside.read_bytes(), b"outside")

    def test_reconciliation_deletes_unreferenced_symlink_even_when_target_is_referenced(self):
        target = self._write("episodes/e1/artifacts/media.mp4", b"keep")
        alias = self.data / "episodes/e1/artifacts/alias.mp4"
        try:
            alias.symlink_to(target)
        except OSError as exc:
            self.skipTest(f"symlink unavailable: {exc}")
        MediaService(
            self.data, [ReferenceRepository(["episodes/e1/artifacts/media.mp4"])]
        ).reconcile_orphans()
        self.assertTrue(target.exists())
        self.assertFalse(alias.exists())

    def test_reconciliation_unlinks_symlinked_managed_roots_without_traversal(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        sentinel = outside / "sentinel.part"
        sentinel.write_bytes(b"keep")
        episode = self.data / "episodes/e1"
        episode.mkdir(parents=True)
        links = []
        try:
            for name in ("attempts", "artifacts", "poster"):
                link = episode / name
                link.symlink_to(outside, target_is_directory=True)
                links.append(link)
        except OSError as exc:
            self.skipTest(f"symlink unavailable: {exc}")
        MediaService(self.data, []).reconcile_orphans()
        self.assertTrue(sentinel.exists())
        self.assertTrue(all(not link.exists() for link in links))

    def _write(self, relative, value):
        path = self.data / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        return path


class RuntimeRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_reconciles_before_scheduler_starts(self):
        events = []

        class Media:
            def reconcile_orphans(self):
                events.append("reconcile")

        class Scheduler:
            async def start(self, before_workers=None):
                events.append("recover")
                if before_workers is not None:
                    await before_workers()
                events.append("scheduler")

            async def stop(self, grace):
                pass

        runtime = V2Runtime(
            provider_profile_service=object(), media_service=Media(), scheduler=Scheduler()
        )
        await runtime.start()
        self.assertEqual(events, ["recover", "reconcile", "scheduler"])

    async def test_cancel_during_reconciliation_joins_cleanup_before_restart(self):
        class Jobs:
            def recover_interrupted(self, message):
                pass

            def claim_next(self, worker_id, now):
                return None

        class Executor:
            async def execute(self, job, cancel_check):
                pass

        scheduler = Scheduler(Jobs(), Executor(), 1, 30)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def reconcile():
            entered.set()
            await release.wait()

        start = asyncio.create_task(scheduler.start(reconcile))
        await entered.wait()
        start.cancel()
        await asyncio.sleep(0)
        self.assertFalse(start.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await start
        await scheduler.start()
        self.assertEqual(scheduler.worker_count, 1)
        await scheduler.stop(0)
