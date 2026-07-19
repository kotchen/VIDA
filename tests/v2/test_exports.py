from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.v2.bootstrap import build_test_runtime
from backend.v2.domain import ChapterRecord, SummaryRecord, TranscriptSegmentRecord


class ExportApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = build_test_runtime(Path(self.temp.name), b"e" * 32)
        profile = self.runtime.require_provider_profiles().create_profile(
            "Primary", "https://api.test/v1", "secret", "model", 0.1
        )
        submission = self.runtime.require_episodes().submit_url(
            "https://example.test/media", profile.id, "en", 'Demo\r\nX-Evil: yes / 你好'
        )
        self.episode_id = submission.episode.id
        self.runtime.episode_repository.replace_transcript(self.episode_id, [
            TranscriptSegmentRecord("s2", self.episode_id, 1, 2.5, 65.125, None, "Second\r\nline"),
            TranscriptSegmentRecord("s1", self.episode_id, 0, 0, 2.5, "Alice", "=formula"),
        ])
        self.runtime.episode_repository.replace_summary(
            self.episode_id, SummaryRecord(self.episode_id, "Summary", 1, 2, 90, "VIDA")
        )
        self.runtime.chapter_repository.replace_generated(self.episode_id, [
            ChapterRecord("c1", self.episode_id, 0, "Intro", 65.125, None, False, "generated")
        ])
        app = FastAPI()
        self.runtime.install(app)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def export(self, fmt):
        return self.client.get(f"/api/v2/episodes/{self.episode_id}/export?format={fmt}")

    def test_txt_is_utf8_deterministic_and_has_safe_attachment_name(self):
        response = self.export("txt")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/plain; charset=utf-8")
        disposition = response.headers["content-disposition"]
        self.assertTrue(disposition.startswith("attachment;"))
        self.assertNotIn("\r", disposition)
        self.assertNotIn("\n", disposition)
        self.assertIn("filename*=UTF-8''", disposition)
        self.assertEqual(
            response.content.decode("utf-8"),
            "[00:00:00] Alice: =formula\n[00:00:02] Speaker 1: Second line\n",
        )

    def test_srt_uses_sequential_indexes_comma_milliseconds_and_normalized_text(self):
        response = self.export("srt")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/x-subrip")
        self.assertEqual(
            response.content.decode("utf-8"),
            "1\n00:00:00,000 --> 00:00:02,500\n=formula\n\n"
            "2\n00:00:02,500 --> 00:01:05,125\nSecond line\n",
        )

    def test_markdown_contains_escaped_title_summary_ordered_chapters_and_transcript(self):
        response = self.export("md")
        self.assertEqual(response.headers["content-type"], "text/markdown; charset=utf-8")
        text = response.content.decode("utf-8")
        self.assertIn("# Demo X-Evil: yes / 你好", text)
        self.assertIn("## Summary\n\nSummary", text)
        self.assertIn("## Chapters\n\n- [00:00:00] Intro", text)
        self.assertIn("## Transcript\n\n[00:00:00] **Alice:** =formula", text)

    def test_unknown_format_and_missing_episode_use_v2_envelopes(self):
        invalid = self.export("pdf")
        missing = self.client.get("/api/v2/episodes/missing/export?format=txt")
        self.assertEqual((invalid.status_code, invalid.json()["error"]["code"]), (400, "invalid_format"))
        self.assertEqual((missing.status_code, missing.json()["error"]["code"]), (404, "episode_not_found"))


if __name__ == "__main__":
    unittest.main()
