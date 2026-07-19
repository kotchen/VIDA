import types
import unittest
from unittest.mock import patch

from backend.transcriber import StructuredTranscription, Transcriber


class FakeModel:
    def __init__(self, segments, language="en", probability=0.91):
        self._segments = segments
        self._info = types.SimpleNamespace(
            language=language, language_probability=probability
        )

    def transcribe(self, *args, **kwargs):
        return iter(self._segments), self._info


class StructuredTranscriberTests(unittest.IsolatedAsyncioTestCase):
    async def test_segments_keep_whisper_timestamps_and_are_immutable(self):
        transcriber = Transcriber()
        transcriber.model = FakeModel(
            [
                types.SimpleNamespace(start=0.0, end=2.5, text=" Hello "),
                types.SimpleNamespace(start=2.5, end=5.0, text="world"),
            ]
        )
        with patch("backend.transcriber.os.path.exists", return_value=True):
            result = await transcriber.transcribe_segments("audio.wav")

        self.assertIsInstance(result, StructuredTranscription)
        self.assertEqual(result.language, "en")
        self.assertEqual(result.language_probability, 0.91)
        self.assertEqual(
            [(s.start_sec, s.end_sec, s.text) for s in result.segments],
            [(0.0, 2.5, "Hello"), (2.5, 5.0, "world")],
        )
        with self.assertRaises((AttributeError, TypeError)):
            result.segments[0].text = "changed"

    async def test_generator_is_materialized_in_worker_thread(self):
        transcriber = Transcriber()
        transcriber.model = FakeModel(
            [types.SimpleNamespace(start=0.0, end=1.0, text="one")]
        )
        calls = []

        async def inline_to_thread(function, *args, **kwargs):
            calls.append(function)
            return function(*args, **kwargs)

        with patch("backend.transcriber.os.path.exists", return_value=True), patch(
            "backend.transcriber.asyncio.to_thread", side_effect=inline_to_thread
        ):
            result = await transcriber.transcribe_segments("audio.wav")

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(result.segments), 1)


if __name__ == "__main__":
    unittest.main()
