from __future__ import annotations

import os
import asyncio
import hashlib
import tempfile
import unittest
from email.utils import parsedate_to_datetime
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.v2.bootstrap import build_test_runtime
from backend.v2.api import episodes as episode_api
from backend.v2.services.media import MediaService, UnsafeMediaPath


class MediaApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = build_test_runtime(self.root, b"r" * 32)
        profile = self.runtime.require_provider_profiles().create_profile(
            "Primary", "https://api.test/v1", "secret", "model", 0.1
        )
        submission = self.runtime.require_episodes().submit_url(
            "https://example.test/media", profile.id, "en", "Media"
        )
        self.episode_id = submission.episode.id
        media_rel = Path("episodes") / self.episode_id / "artifacts" / "media.mp4"
        poster_rel = Path("episodes") / self.episode_id / "poster" / "poster.jpg"
        (self.root / media_rel).parent.mkdir(parents=True)
        (self.root / poster_rel).parent.mkdir(parents=True)
        (self.root / media_rel).write_bytes(b"0123456789")
        (self.root / poster_rel).write_bytes(b"jpeg")
        with self.runtime.database.transaction() as conn:
            conn.execute(
                "UPDATE episodes SET media_path=?,media_content_type='video/mp4',"
                "poster_path=?,poster_content_type='image/jpeg' WHERE id=?",
                (media_rel.as_posix(), poster_rel.as_posix(), self.episode_id),
            )
        app = FastAPI()
        self.runtime.install(app)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    @property
    def url(self):
        return f"/api/v2/episodes/{self.episode_id}/media"

    def test_full_get_and_head_are_bounded_and_cacheable(self):
        response = self.client.get(self.url)
        head = self.client.head(self.url)
        self.assertEqual((response.status_code, response.content), (200, b"0123456789"))
        self.assertEqual(response.headers["accept-ranges"], "bytes")
        self.assertEqual(response.headers["content-length"], "10")
        self.assertEqual(response.headers["content-type"], "video/mp4")
        self.assertTrue(response.headers["etag"].startswith('"'))
        parsedate_to_datetime(response.headers["last-modified"])
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.content, b"")
        self.assertEqual(head.headers["content-length"], "10")

    def test_single_ranges_support_closed_open_and_suffix_forms(self):
        cases = (
            ("bytes=2-5", b"2345", "bytes 2-5/10"),
            ("bytes=7-", b"789", "bytes 7-9/10"),
            ("bytes=-3", b"789", "bytes 7-9/10"),
        )
        for header, expected, content_range in cases:
            with self.subTest(header=header):
                response = self.client.get(self.url, headers={"Range": header})
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response.content, expected)
                self.assertEqual(response.headers["content-range"], content_range)
                self.assertEqual(response.headers["content-length"], str(len(expected)))

        upper = self.client.get(self.url, headers={"Range": "BYTES=2-5"})
        self.assertEqual((upper.status_code, upper.content), (206, b"2345"))

    def test_invalid_unsatisfiable_and_multiple_ranges_use_416_envelope(self):
        for header in ("items=0-1", "bytes=", "bytes=5-2", "bytes=50-60", "bytes=0-1,4-5"):
            with self.subTest(header=header):
                response = self.client.get(self.url, headers={"Range": header})
                self.assertEqual(response.status_code, 416)
                self.assertEqual(response.headers["content-range"], "bytes */10")
                self.assertEqual(response.json()["error"]["code"], "range_not_satisfiable")

    def test_conditional_requests_and_if_range_follow_validator_precedence(self):
        initial = self.client.get(self.url)
        etag = initial.headers["etag"]
        modified = initial.headers["last-modified"]
        self.assertEqual(
            self.client.get(self.url, headers={"If-None-Match": etag}).status_code, 304
        )
        self.assertEqual(
            self.client.get(self.url, headers={"If-Modified-Since": modified}).status_code, 304
        )
        ranged = self.client.get(
            self.url, headers={"Range": "bytes=0-1", "If-Range": etag}
        )
        stale = self.client.get(
            self.url, headers={"Range": "bytes=0-1", "If-Range": '"stale"'}
        )
        self.assertEqual((ranged.status_code, ranged.content), (206, b"01"))
        self.assertEqual((stale.status_code, stale.content), (200, b"0123456789"))
        weak = f"W/{etag}"
        self.assertEqual(
            self.client.get(self.url, headers={"If-None-Match": weak}).status_code, 304
        )
        weak_range = self.client.get(
            self.url, headers={"Range": "bytes=0-1", "If-Range": weak}
        )
        self.assertEqual((weak_range.status_code, weak_range.content), (200, b"0123456789"))

    def test_strong_etag_changes_when_same_size_content_changes_at_same_mtime(self):
        target = self.root / "episodes" / self.episode_id / "artifacts" / "media.mp4"
        before = target.stat()
        first = self.client.get(self.url)
        target.write_bytes(b"abcdefghij")
        os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))

        second = self.client.get(self.url)

        self.assertEqual(second.content, b"abcdefghij")
        self.assertNotEqual(first.headers["etag"], second.headers["etag"])
        self.assertEqual(
            self.client.get(
                self.url, headers={"If-Match": first.headers["etag"]}
            ).status_code,
            412,
        )
        stale_range = self.client.get(
            self.url,
            headers={"Range": "bytes=0-1", "If-Range": first.headers["etag"]},
        )
        self.assertEqual((stale_range.status_code, stale_range.content), (200, b"abcdefghij"))

    def test_digest_and_body_reads_are_bounded_and_all_early_paths_close(self):
        target = self.root / "episodes" / self.episode_id / "artifacts" / "media.mp4"
        payload = b"x" * (200 * 1024 + 3)
        target.write_bytes(payload)
        original = self.runtime.media_service.open_owned_file
        trackers = []

        def tracked_open(*args, **kwargs):
            opened = original(*args, **kwargs)
            tracker = _TrackingFile(opened.file)
            opened.file = tracker
            trackers.append(tracker)
            return opened

        with patch.object(self.runtime.media_service, "open_owned_file", tracked_open):
            full = self.client.get(self.url)
            self.assertEqual(
                full.headers["etag"], f'"sha256-{hashlib.sha256(payload).hexdigest()}"'
            )
            self.assertTrue(trackers[-1].closed)
            for method, headers, expected in (
                ("head", {}, 200),
                ("get", {"If-None-Match": full.headers["etag"]}, 304),
                ("get", {"If-Match": '"stale"'}, 412),
                ("get", {"Range": "bytes=999999-"}, 416),
            ):
                response = getattr(self.client, method)(self.url, headers=headers)
                self.assertEqual(response.status_code, expected)
                self.assertTrue(trackers[-1].closed)

        self.assertTrue(trackers)
        self.assertTrue(all(size <= 64 * 1024 for row in trackers for size in row.read_sizes))

    def test_failed_if_match_and_if_unmodified_since_preconditions_return_412(self):
        initial = self.client.get(self.url)
        stale_date = "Thu, 01 Jan 1970 00:00:00 GMT"
        for headers in (
            {"If-Match": '"stale"'},
            {"If-Match": f"W/{initial.headers['etag']}"},
            {"If-Unmodified-Since": stale_date},
        ):
            with self.subTest(headers=headers):
                response = self.client.get(self.url, headers=headers)
                self.assertEqual(response.status_code, 412)
                self.assertEqual(response.json()["error"]["code"], "precondition_failed")
        self.assertEqual(
            self.client.get(self.url, headers={"If-Match": "*"}).status_code, 200
        )

    def test_poster_is_controlled_and_missing_files_use_v2_errors(self):
        poster = self.client.get(f"/api/v2/episodes/{self.episode_id}/poster")
        self.assertEqual((poster.status_code, poster.content), (200, b"jpeg"))
        self.assertEqual(poster.headers["content-type"], "image/jpeg")
        (self.root / "episodes" / self.episode_id / "poster" / "poster.jpg").unlink()
        missing = self.client.get(f"/api/v2/episodes/{self.episode_id}/poster")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["error"]["code"], "media_not_found")

    def test_absolute_traversal_and_symlink_paths_are_never_delivered(self):
        outside = self.root.parent / f"outside-{self.episode_id}.mp4"
        outside.write_bytes(b"secret")
        try:
            for stored in (str(outside), "../outside.mp4"):
                with self.runtime.database.transaction() as conn:
                    conn.execute("UPDATE episodes SET media_path=? WHERE id=?", (stored, self.episode_id))
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()["error"]["code"], "media_not_found")
                self.assertNotIn(b"secret", response.content)
            link = self.root / "episodes" / self.episode_id / "artifacts" / "link.mp4"
            try:
                os.symlink(outside, link)
            except OSError:
                self.skipTest("symlinks unavailable")
            with self.runtime.database.transaction() as conn:
                conn.execute(
                    "UPDATE episodes SET media_path=? WHERE id=?",
                    ((Path("episodes") / self.episode_id / "artifacts" / "link.mp4").as_posix(), self.episode_id),
                )
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()["error"]["code"], "media_not_found")
        finally:
            outside.unlink(missing_ok=True)

    def test_symlinked_parent_directory_cannot_redirect_to_external_media(self):
        episode_root = self.root / "episodes" / self.episode_id
        artifacts = episode_root / "artifacts"
        original = episode_root / "artifacts-original"
        outside = self.root.parent / f"outside-dir-{self.episode_id}"
        artifacts.rename(original)
        outside.mkdir()
        (outside / "media.mp4").write_bytes(b"external-secret")
        try:
            try:
                os.symlink(outside, artifacts, target_is_directory=True)
            except OSError:
                self.skipTest("directory symlinks unavailable")
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()["error"]["code"], "media_not_found")
            self.assertNotIn(b"external-secret", response.content)
        finally:
            if artifacts.is_symlink():
                artifacts.unlink()
            outside.joinpath("media.mp4").unlink(missing_ok=True)
            outside.rmdir()
            original.rename(artifacts)


class MediaOpenRaceTests(unittest.TestCase):
    def test_post_open_identity_race_is_normalized_to_unsafe_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            relative = Path("episodes") / "e1" / "artifacts" / "media.mp4"
            target = root / relative
            target.parent.mkdir(parents=True)
            target.write_bytes(b"data")
            service = MediaService(root, [])

            with patch(
                "backend.v2.services.media.os.fstat",
                side_effect=FileNotFoundError("simulated replacement"),
            ):
                with self.assertRaises(UnsafeMediaPath):
                    service.open_owned_file("e1", relative.as_posix(), "media")

    @unittest.skipUnless(os.name == "nt", "Windows native handle walk test")
    def test_windows_parent_swap_during_walk_is_blocked_or_stays_on_pinned_parent(self):
        import ctypes

        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as external:
            root = Path(temporary)
            outside = Path(external)
            relative = Path("episodes") / "e1" / "artifacts" / "media.mp4"
            artifacts = (root / relative).parent
            artifacts.mkdir(parents=True)
            (root / relative).write_bytes(b"internal")
            (outside / "media.mp4").write_bytes(b"external-secret")
            moved = artifacts.with_name("artifacts-pinned")
            real_win_dll = ctypes.WinDLL
            real_ntdll = real_win_dll("ntdll")
            real_nt_create = real_ntdll.NtCreateFile
            calls = 0
            attempted = False
            blocked = False

            class InterceptedNtCreate:
                @property
                def argtypes(self):
                    return real_nt_create.argtypes

                @argtypes.setter
                def argtypes(self, value):
                    real_nt_create.argtypes = value

                @property
                def restype(self):
                    return real_nt_create.restype

                @restype.setter
                def restype(self, value):
                    real_nt_create.restype = value

                def __call__(self, *args):
                    nonlocal calls, attempted, blocked
                    result = real_nt_create(*args)
                    calls += 1
                    if calls == 3 and result == 0:
                        attempted = True
                        try:
                            artifacts.rename(moved)
                            os.symlink(outside, artifacts, target_is_directory=True)
                        except OSError:
                            blocked = True
                    return result

            class NtdllProxy:
                NtCreateFile = InterceptedNtCreate()

            def load_library(name, *args, **kwargs):
                if name == "ntdll":
                    return NtdllProxy()
                return real_win_dll(name, *args, **kwargs)

            try:
                with patch("ctypes.WinDLL", side_effect=load_library):
                    opened = MediaService(root, []).open_owned_file(
                        "e1", relative.as_posix(), "media"
                    )
                try:
                    self.assertEqual(opened.file.read(), b"internal")
                finally:
                    opened.close()
                self.assertTrue(attempted)
                self.assertTrue(blocked or moved.exists())
            finally:
                if artifacts.is_symlink():
                    artifacts.unlink()
                if moved.exists():
                    moved.rename(artifacts)


class OwnedStreamingResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_closes_when_response_start_send_fails_before_iteration(self):
        response_class = getattr(episode_api, "OwnedStreamingResponse", None)
        self.assertIsNotNone(response_class)
        owner = _CountingOwner()

        async def body():
            yield b"data"

        response = response_class(body(), owner=owner)

        async def send(message):
            if message["type"] == "http.response.start":
                raise RuntimeError("start failed")

        with self.assertRaisesRegex(RuntimeError, "start failed"):
            await response(_scope(), _never_receive, send)
        self.assertEqual(owner.close_count, 1)

    async def test_owner_closes_on_body_send_failure_and_disconnect(self):
        response_class = getattr(episode_api, "OwnedStreamingResponse", None)
        self.assertIsNotNone(response_class)
        owner = _CountingOwner()

        async def body():
            yield b"data"

        async def failing_send(message):
            if message["type"] == "http.response.body":
                raise RuntimeError("body failed")

        with self.assertRaises(BaseException):
            await response_class(body(), owner=owner)(
                _scope(), _never_receive, failing_send
            )
        self.assertEqual(owner.close_count, 1)

        disconnected_owner = _CountingOwner()

        async def disconnected_receive():
            return {"type": "http.disconnect"}

        async def collect(_message):
            return None

        await response_class(body(), owner=disconnected_owner)(
            _scope(), disconnected_receive, collect
        )
        self.assertEqual(disconnected_owner.close_count, 1)

    async def test_owner_closes_once_on_body_send_failure_and_repeated_cancel(self):
        response_class = getattr(episode_api, "OwnedStreamingResponse", None)
        self.assertIsNotNone(response_class)
        owner = _CountingOwner()

        async def body():
            yield b"one"
            await asyncio.Event().wait()

        response = response_class(body(), owner=owner)
        body_started = asyncio.Event()

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                body_started.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(response(_scope(), _never_receive, send))
        await asyncio.wait_for(body_started.wait(), 1)
        task.cancel()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(owner.close_count, 1)


class _CountingOwner:
    def __init__(self):
        self.close_count = 0

    def close(self):
        self.close_count += 1


class _TrackingFile:
    def __init__(self, wrapped):
        self._wrapped = wrapped
        self.read_sizes = []

    @property
    def closed(self):
        return self._wrapped.closed

    def read(self, size=-1):
        self.read_sizes.append(size)
        return self._wrapped.read(size)

    def seek(self, *args):
        return self._wrapped.seek(*args)

    def tell(self):
        return self._wrapped.tell()

    def close(self):
        return self._wrapped.close()


def _scope():
    return {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "scheme": "http", "path": "/", "raw_path": b"/",
        "query_string": b"", "headers": [], "client": ("127.0.0.1", 1),
        "server": ("test", 80),
    }


async def _never_receive():
    await asyncio.Event().wait()


if __name__ == "__main__":
    unittest.main()
