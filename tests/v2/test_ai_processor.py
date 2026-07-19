import asyncio
import json
import threading
import unittest

from backend.v2.jobs.ai import AIExecutionError, AIProcessor, OpenAICompletionRunner
from backend.v2.services.provider_profiles import ProviderRevisionCredentials


CREDENTIALS = ProviderRevisionCredentials(
    "revision-1", "profile-1", 3, "https://provider.example/v1",
    "secret-key", "model-3", 0.25,
)


class RecordingRunner:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    async def complete(self, credentials, messages, response_format):
        self.calls.append((credentials, messages, response_format))
        value = self.outputs.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class AIProcessorTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_provider_timeout_closes_request_client(self):
        closed = asyncio.Event()

        class Completions:
            async def create(self, **kwargs):
                await asyncio.Event().wait()

        class Client:
            def __init__(self, **kwargs):
                self.chat = type("Chat", (), {"completions": Completions()})()

            async def close(self):
                closed.set()

        runner = OpenAICompletionRunner(0.02, client_factory=Client)
        with self.assertRaisesRegex(AIExecutionError, "AI request failed"):
            await AIProcessor(
                CREDENTIALS, runner=runner, timeout_sec=0.02
            ).summarize("episode-1", "text", "en", "Title")
        self.assertTrue(closed.is_set())

    async def test_optimization_cancellation_closes_client_and_joins_worker(self):
        started = threading.Event()
        release = threading.Event()
        closed = threading.Event()

        class Client:
            def close(self):
                closed.set()
                release.set()

        class Summarizer:
            def __init__(self, **kwargs):
                self.client = Client()

            async def optimize_transcript(self, transcript):
                started.set()
                release.wait(timeout=2)
                return transcript

        task = asyncio.create_task(
            AIProcessor(CREDENTIALS, summarizer_factory=Summarizer).optimize("raw")
        )
        self.assertTrue(await asyncio.to_thread(started.wait, 1))
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(closed.is_set())

    async def test_optimization_requests_strict_failure_and_closes_request_client(self):
        captured = {}

        class Client:
            closed = False

            def close(self):
                self.closed = True

        class Summarizer:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.client = Client()

            async def optimize_transcript(self, transcript):
                raise RuntimeError("secret upstream detail")

        with self.assertRaisesRegex(AIExecutionError, "AI request failed"):
            await AIProcessor(
                CREDENTIALS, summarizer_factory=Summarizer
            ).optimize("raw")
        self.assertTrue(captured["raise_on_error"])

    async def test_summary_normalizes_content_and_clamps_confidence(self):
        runner = RecordingRunner([
            json.dumps({
                "content": "# Overview\n\n**Useful** [link](https://example.com)",
                "keyPoints": 4,
                "confidence": 137.6,
            })
        ])
        summary = await AIProcessor(CREDENTIALS, runner=runner).summarize(
            "episode-1", "transcript", "zh", "Title"
        )

        self.assertEqual(summary.content, "Overview\n\nUseful link")
        self.assertEqual(summary.read_time_min, 1)
        self.assertEqual(summary.key_points, 4)
        self.assertEqual(summary.confidence, 100)
        self.assertEqual(summary.generated_by, "VIDA")
        self.assertIs(runner.calls[0][0], CREDENTIALS)
        self.assertEqual(runner.calls[0][2], {"type": "json_object"})

    async def test_summary_rejects_invalid_structured_output_without_secret_leak(self):
        runner = RecordingRunner(["not-json"])
        with self.assertRaisesRegex(AIExecutionError, "Summary output is invalid") as caught:
            await AIProcessor(CREDENTIALS, runner=runner).summarize(
                "episode-1", "transcript", "en", "Title"
            )
        self.assertNotIn("not-json", str(caught.exception))
        self.assertNotIn(CREDENTIALS.api_key, str(caught.exception))

    async def test_chapters_validate_bounds_and_derive_durations(self):
        runner = RecordingRunner([
            json.dumps({"chapters": [
                {"startSec": 0, "title": "Opening"},
                {"startSec": 12.5, "title": "Deep dive"},
            ]})
        ])
        chapters = await AIProcessor(CREDENTIALS, runner=runner).generate_chapters(
            "episode-1", "transcript", 30.0, "episodes/episode-1/poster/p.jpg"
        )

        self.assertEqual([row.start_sec for row in chapters], [0.0, 12.5])
        self.assertEqual([row.duration_sec for row in chapters], [12.5, 17.5])
        self.assertTrue(all(row.source == "generated" for row in chapters))
        self.assertTrue(all(not row.bookmarked for row in chapters))

    async def test_invalid_chapters_return_no_rows(self):
        invalid_documents = [
            {"chapters": [{"startSec": -1, "title": "Bad"}]},
            {"chapters": [
                {"startSec": 5, "title": "Later"},
                {"startSec": 5, "title": "Duplicate"},
            ]},
            {"chapters": [{"startSec": 31, "title": "Past end"}]},
        ]
        for document in invalid_documents:
            with self.subTest(document=document):
                rows = await AIProcessor(
                    CREDENTIALS, runner=RecordingRunner([json.dumps(document)])
                ).generate_chapters("episode-1", "text", 30.0, None)
                self.assertEqual(rows, [])

    async def test_provider_errors_and_timeouts_are_sanitized(self):
        for failure in (RuntimeError("secret-key upstream exploded"), asyncio.TimeoutError()):
            with self.subTest(failure=failure):
                with self.assertRaisesRegex(AIExecutionError, "AI request failed") as caught:
                    await AIProcessor(
                        CREDENTIALS, runner=RecordingRunner([failure]), timeout_sec=0.1
                    ).summarize("episode-1", "text", "en", "Title")
                self.assertNotIn("secret-key", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
