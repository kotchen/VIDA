from __future__ import annotations

import os
import tempfile
import unittest
from email.utils import parsedate_to_datetime
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.v2.bootstrap import build_test_runtime
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


if __name__ == "__main__":
    unittest.main()
