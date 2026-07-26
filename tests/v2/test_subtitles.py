import json
import tempfile
import unittest
from pathlib import Path

from backend.v2.jobs.subtitles import (
    PlatformSubtitleFetcher,
    SubtitleSegment,
    _choose_track,
    _parse_subtitle_file,
    _preference_codes,
)


class PreferenceCodeTests(unittest.TestCase):
    def test_simplified_chinese_prefers_simplified_tracks(self):
        self.assertEqual(
            _preference_codes("zh-Hans"), ("zh-Hans", "zh-CN", "zh-SG", "zh")
        )
        self.assertEqual(
            _preference_codes("zh-CN"), ("zh-Hans", "zh-CN", "zh-SG", "zh")
        )

    def test_traditional_chinese_prefers_traditional_tracks(self):
        self.assertEqual(
            _preference_codes("zh-Hant"), ("zh-Hant", "zh-TW", "zh-HK")
        )
        self.assertEqual(
            _preference_codes("zh-TW"), ("zh-Hant", "zh-TW", "zh-HK")
        )

    def test_legacy_generic_chinese_accepts_both_scripts(self):
        self.assertEqual(
            _preference_codes("zh"),
            ("zh-Hans", "zh-CN", "zh-SG", "zh", "zh-Hant", "zh-TW", "zh-HK"),
        )

    def test_other_languages_fall_back_to_base_and_prefix(self):
        self.assertEqual(_preference_codes("en-US"), ("en-US", "en", "en-"))
        self.assertEqual(_preference_codes("ja"), ("ja", "ja-"))
        self.assertEqual(_preference_codes(""), ())


class ChooseTrackTests(unittest.TestCase):
    def test_manual_track_beats_automatic_for_same_language(self):
        info = {
            "subtitles": {"en": []},
            "automatic_captions": {"en": []},
        }
        choice = _choose_track(info, "en")
        self.assertEqual((choice.language, choice.automatic), ("en", False))

    def test_simplified_choice_never_picks_traditional_track(self):
        info = {
            "subtitles": {"zh-Hant": []},
            "automatic_captions": {"zh-Hant": []},
        }
        self.assertIsNone(_choose_track(info, "zh-Hans"))

    def test_traditional_choice_accepts_regional_traditional_codes(self):
        info = {"subtitles": {"zh-TW": []}, "automatic_captions": {}}
        choice = _choose_track(info, "zh-Hant")
        self.assertEqual((choice.language, choice.automatic), ("zh-TW", False))

    def test_generic_chinese_accepts_traditional_after_simplified_missing(self):
        info = {"subtitles": {"zh-Hant": []}, "automatic_captions": {}}
        choice = _choose_track(info, "zh")
        self.assertEqual((choice.language, choice.automatic), ("zh-Hant", False))

    def test_automatic_captions_used_when_no_manual_track(self):
        info = {"subtitles": {}, "automatic_captions": {"en": []}}
        choice = _choose_track(info, "en")
        self.assertEqual((choice.language, choice.automatic), ("en", True))

    def test_prefix_matching_covers_regional_variants(self):
        info = {"subtitles": {"en-US": []}, "automatic_captions": {}}
        choice = _choose_track(info, "en")
        self.assertEqual((choice.language, choice.automatic), ("en-US", False))

    def test_no_match_returns_none(self):
        info = {"subtitles": {"fr": []}, "automatic_captions": {"de": []}}
        self.assertIsNone(_choose_track(info, "en"))


class ParseSubtitleFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def write(self, content: str) -> Path:
        path = self.directory / "subtitle.json3"
        path.write_text(content, encoding="utf-8")
        return path

    def test_json3_events_are_clamped_to_next_start(self):
        path = self.write(json.dumps({
            "events": [
                {"tStartMs": 0, "dDurationMs": 8000,
                 "segs": [{"utf8": "We're no strangers to"}]},
                {"tStartMs": 3000, "segs": [{"utf8": "\n"}]},
                {"tStartMs": 3000, "dDurationMs": 4000,
                 "segs": [{"utf8": "love.\nYou know"}]},
            ]
        }))
        segments = _parse_subtitle_file(path)
        self.assertEqual(
            [(s.start_sec, s.end_sec, s.text) for s in segments],
            [
                (0.0, 3.0, "We're no strangers to"),
                (3.0, 7.0, "love. You know"),
            ],
        )

    def test_json3_event_without_duration_gets_default_end(self):
        path = self.write(json.dumps({
            "events": [{"tStartMs": 1000, "segs": [{"utf8": "hello"}]}]
        }))
        segments = _parse_subtitle_file(path)
        self.assertEqual(
            [(s.start_sec, s.end_sec, s.text) for s in segments],
            [(1.0, 3.0, "hello")],
        )

    def test_bilibili_body_format(self):
        path = self.write(json.dumps({
            "body": [
                {"from": 0.5, "to": 2.0, "content": "你好"},
                {"from": 2.0, "to": 4.5, "content": "世界"},
            ]
        }))
        segments = _parse_subtitle_file(path)
        self.assertEqual(
            [(s.start_sec, s.end_sec, s.text) for s in segments],
            [(0.5, 2.0, "你好"), (2.0, 4.5, "世界")],
        )

    def test_webvtt_cues_strip_tags_and_settings(self):
        path = self.write(
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:03.500 align:start position:0%\n"
            "<c>hello</c> world\n\n"
            "00:00:04.000 --> 00:00:05.000\n"
            "second line\n"
        )
        segments = _parse_subtitle_file(path)
        self.assertEqual(
            [(s.start_sec, s.end_sec, s.text) for s in segments],
            [(1.0, 3.5, "hello world"), (4.0, 5.0, "second line")],
        )

    def test_srt_cues_with_index_lines(self):
        path = self.write(
            "1\n00:00:01,000 --> 00:00:02,500\nfirst\n\n"
            "2\n00:00:03,000 --> 00:00:04,000\nsecond\n"
        )
        segments = _parse_subtitle_file(path)
        self.assertEqual(
            [(s.start_sec, s.end_sec, s.text) for s in segments],
            [(1.0, 2.5, "first"), (3.0, 4.0, "second")],
        )

    def test_invalid_content_returns_empty(self):
        self.assertEqual(_parse_subtitle_file(self.write("not subtitles")), ())
        self.assertEqual(_parse_subtitle_file(self.write("{}")), ())
        self.assertEqual(_parse_subtitle_file(self.write("{broken")), ())


class FakeValidator:
    def __init__(self):
        self.validated = []

    async def validate_url(self, url, cancel_check):
        self.validated.append(url)


class FakeProxy:
    proxy_url = "http://proxy.example"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class FakeYoutubeDL:
    """Two-pass fake: lists tracks, then writes the chosen subtitle file."""

    instances = []

    def __init__(self, options):
        self.options = options
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def extract_info(self, url, download=False):
        if not download:
            return {
                "subtitles": {"zh-Hant": []},
                "automatic_captions": {"zh-Hans": [], "en": []},
            }
        directory = Path(self.options["outtmpl"]).parent
        language = self.options["subtitleslangs"][0]
        automatic = self.options["writeautomaticsub"]
        (directory / f"subtitle.{language}.json3").write_text(
            json.dumps({
                "events": [
                    {"tStartMs": 0, "dDurationMs": 1500,
                     "segs": [{"utf8": f"auto={automatic}"}]},
                ]
            }),
            encoding="utf-8",
        )
        return {}


async def inline_runner(function, *args, **kwargs):
    return function(*args, **kwargs)


class PlatformSubtitleFetcherTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        FakeYoutubeDL.instances = []

    def make_fetcher(self) -> PlatformSubtitleFetcher:
        return PlatformSubtitleFetcher(
            FakeValidator(),
            proxy_factory=lambda cancel_check: FakeProxy(),
            youtube_dl_factory=FakeYoutubeDL,
            thread_runner=inline_runner,
        )

    async def test_non_platform_url_returns_none_without_network(self):
        validator = FakeValidator()
        fetcher = PlatformSubtitleFetcher(
            validator,
            proxy_factory=lambda cancel_check: FakeProxy(),
            youtube_dl_factory=FakeYoutubeDL,
            thread_runner=inline_runner,
        )
        result = await fetcher.fetch(
            "https://media.example.com/clip.mp4", "en",
            Path(self.temp.name) / "subs", lambda: False,
        )
        self.assertIsNone(result)
        self.assertEqual(validator.validated, [])
        self.assertEqual(FakeYoutubeDL.instances, [])

    async def test_fetches_track_matching_user_language(self):
        fetcher = self.make_fetcher()
        result = await fetcher.fetch(
            "https://www.youtube.com/watch?v=x", "zh-Hans",
            Path(self.temp.name) / "subs", lambda: False,
        )
        self.assertIsNotNone(result)
        # zh-Hans has no manual track, so the automatic zh-Hans track wins
        # over the manual zh-Hant track (wrong script for the user).
        self.assertEqual((result.language, result.automatic), ("zh-Hans", True))
        self.assertEqual(
            [(s.start_sec, s.end_sec, s.text) for s in result.segments],
            [(0.0, 1.5, "auto=True")],
        )

    async def test_traditional_choice_uses_manual_track(self):
        fetcher = self.make_fetcher()
        result = await fetcher.fetch(
            "https://www.youtube.com/watch?v=x", "zh-Hant",
            Path(self.temp.name) / "subs", lambda: False,
        )
        self.assertIsNotNone(result)
        self.assertEqual((result.language, result.automatic), ("zh-Hant", False))

    async def test_subtitle_directory_is_cleaned_after_fetch(self):
        fetcher = self.make_fetcher()
        target = Path(self.temp.name) / "subs"
        await fetcher.fetch(
            "https://www.youtube.com/watch?v=x", "zh-Hans", target, lambda: False
        )
        self.assertEqual(list(target.iterdir()), [])

    async def test_extractor_failure_falls_back_to_none(self):
        class BrokenYoutubeDL(FakeYoutubeDL):
            def extract_info(self, url, download=False):
                raise RuntimeError("extractor detail")

        fetcher = PlatformSubtitleFetcher(
            FakeValidator(),
            proxy_factory=lambda cancel_check: FakeProxy(),
            youtube_dl_factory=BrokenYoutubeDL,
            thread_runner=inline_runner,
        )
        result = await fetcher.fetch(
            "https://www.youtube.com/watch?v=x", "en",
            Path(self.temp.name) / "subs", lambda: False,
        )
        self.assertIsNone(result)

    async def test_egress_proxy_bypasses_ssrf_tunnel(self):
        def forbidden_factory(cancel_check):
            raise AssertionError("SSRF tunnel must not start in egress mode")

        fetcher = PlatformSubtitleFetcher(
            FakeValidator(),
            proxy_factory=forbidden_factory,
            youtube_dl_factory=FakeYoutubeDL,
            thread_runner=inline_runner,
            egress_proxy="http://127.0.0.1:7897",
        )
        result = await fetcher.fetch(
            "https://www.youtube.com/watch?v=x", "zh-Hant",
            Path(self.temp.name) / "subs", lambda: False,
        )
        self.assertIsNotNone(result)
        for instance in FakeYoutubeDL.instances:
            self.assertEqual(instance.options["proxy"], "http://127.0.0.1:7897")


if __name__ == "__main__":
    unittest.main()
