import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FrontendSettingsContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        cls.app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

    def test_ai_settings_exposes_profile_and_temperature_controls(self):
        for element_id in (
            "providerProfileSelect",
            "addProfileBtn",
            "saveProfileBtn",
            "deleteProfileBtn",
            "profileNameInput",
            "temperatureInput",
        ):
            self.assertIn(f'id="{element_id}"', self.html)

        self.assertIn('/static/settings-store.js', self.html)
        self.assertLess(
            self.html.index('/static/settings-store.js'),
            self.html.index('/static/app.js'),
        )

    def test_icon_actions_are_named_and_have_tooltips(self):
        for element_id in ("addProfileBtn", "saveProfileBtn", "deleteProfileBtn"):
            match = re.search(rf'<button[^>]*id="{element_id}"[^>]*>', self.html)
            self.assertIsNotNone(match)
            self.assertIn('aria-label=', match.group(0))
            self.assertIn('data-tooltip=', match.group(0))

    def test_temperature_input_has_numeric_constraints(self):
        match = re.search(r'<input[^>]*id="temperatureInput"[^>]*>', self.html)
        self.assertIsNotNone(match)
        temperature = match.group(0)
        for attribute in ('min="0"', 'max="2"', 'step="0.1"', 'value="0.1"'):
            self.assertIn(attribute, temperature)

    def test_url_and_upload_requests_include_temperature(self):
        self.assertGreaterEqual(self.app_js.count("fd.append('temperature'"), 2)

    def test_model_fetches_ignore_stale_profile_responses(self):
        self.assertIn("const fetchToken = ++this._modelFetchToken", self.app_js)
        self.assertIn("fetchToken !== this._modelFetchToken", self.app_js)
        self.assertNotIn("this._savedModel", self.app_js)


if __name__ == "__main__":
    unittest.main()
