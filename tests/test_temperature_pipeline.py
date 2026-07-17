import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


class StubVideoProcessor:
    pass


class StubTranscriber:
    pass


video_processor_module = types.ModuleType("video_processor")
video_processor_module.VideoProcessor = StubVideoProcessor
transcriber_module = types.ModuleType("transcriber")
transcriber_module.Transcriber = StubTranscriber
sys.modules["video_processor"] = video_processor_module
sys.modules["transcriber"] = transcriber_module

import main


class FakeUpload:
    filename = "clip.txt"

    def __init__(self):
        self._chunks = [b"hello", b""]

    async def read(self, _size):
        return self._chunks.pop(0)


class TemperaturePipelineTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        main.processing_urls.clear()
        main.active_tasks.clear()

    def test_temperature_or_400_accepts_default_and_valid_values(self):
        self.assertEqual(main._temperature_or_400(""), 0.1)
        self.assertEqual(main._temperature_or_400("1"), 1.0)

    def test_temperature_or_400_rejects_invalid_values(self):
        for value in ("nan", "-0.1", "2.1", "invalid"):
            with self.subTest(value=value):
                with self.assertRaises(main.HTTPException) as raised:
                    main._temperature_or_400(value)
                self.assertEqual(raised.exception.status_code, 400)

    def test_request_services_receive_the_same_temperature(self):
        with (
            patch.object(main, "Summarizer") as summarizer_cls,
            patch.object(main, "Translator") as translator_cls,
        ):
            main._create_request_ai_services(
                api_key="secret",
                model_base_url="https://api.example/v1/",
                model_id="k3",
                temperature=1,
            )

        expected = {
            "api_key": "secret",
            "base_url": "https://api.example/v1",
            "model": "k3",
            "temperature": 1.0,
        }
        summarizer_cls.assert_called_once_with(**expected)
        translator_cls.assert_called_once_with(**expected)

    async def test_upload_job_passes_temperature_to_background_task(self):
        sentinel = object()
        task_handle = object()
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(main, "TEMP_DIR", Path(temp_dir)),
            patch.object(main, "save_tasks"),
            patch.object(main, "process_upload_task", Mock(return_value=sentinel)) as process_task,
            patch.object(main.asyncio, "create_task", Mock(return_value=task_handle)),
        ):
            result = await main._enqueue_upload_job(
                FakeUpload(), "zh", "secret", "https://api.example/v1", "k3", 1
            )

        self.assertIn("task_id", result)
        self.assertEqual(process_task.call_args.args[-1], 1)

    async def test_url_job_passes_validated_temperature_to_background_task(self):
        sentinel = object()
        task_handle = object()
        with (
            patch.object(main, "save_tasks"),
            patch.object(main, "process_video_task", Mock(return_value=sentinel)) as process_task,
            patch.object(main.asyncio, "create_task", Mock(return_value=task_handle)),
        ):
            result = await main.process_video(
                url="https://example.com/video",
                summary_language="zh",
                api_key="secret",
                model_base_url="https://api.example/v1",
                model_id="k3",
                temperature="1",
                file=None,
            )

        self.assertIn("task_id", result)
        self.assertEqual(process_task.call_args.args[-1], 1.0)


if __name__ == "__main__":
    unittest.main()
