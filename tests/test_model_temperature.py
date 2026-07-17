import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from model_settings import validate_temperature
from summarizer import Summarizer
from translator import Translator


class FakeCompletions:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return object()


class FakeClient:
    def __init__(self):
        self.chat = type("Chat", (), {"completions": FakeCompletions()})()


class TemperatureValidationTests(unittest.TestCase):
    def test_default_and_boundary_values_are_accepted(self):
        self.assertEqual(validate_temperature(None), 0.1)
        self.assertEqual(validate_temperature(""), 0.1)
        self.assertEqual(validate_temperature(0), 0.0)
        self.assertEqual(validate_temperature("1"), 1.0)
        self.assertEqual(validate_temperature(2), 2.0)

    def test_invalid_values_are_rejected(self):
        for value in (-0.1, 2.1, "nan", "inf", "-inf", "invalid"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_temperature(value)


class CompletionTemperatureTests(unittest.TestCase):
    def test_summarizer_completion_uses_configured_temperature(self):
        service = Summarizer(temperature=1)
        service.client = FakeClient()

        service._create_completion(
            model="test",
            messages=[],
            temperature=0.25,
        )

        self.assertEqual(service.client.chat.completions.kwargs["temperature"], 1.0)

    def test_translator_completion_uses_configured_temperature(self):
        service = Translator(temperature=1)
        service.client = FakeClient()

        service._create_completion(
            model="test",
            messages=[],
            temperature=0.25,
        )

        self.assertEqual(service.client.chat.completions.kwargs["temperature"], 1.0)

    def test_all_text_completions_use_the_wrapper(self):
        for filename in ("summarizer.py", "translator.py"):
            with self.subTest(filename=filename):
                source = (BACKEND / filename).read_text(encoding="utf-8")
                self.assertEqual(source.count("self.client.chat.completions.create("), 1)
                self.assertNotRegex(source, r"temperature\s*=\s*(?:0\.05|0\.1|0\.25)")


if __name__ == "__main__":
    unittest.main()
