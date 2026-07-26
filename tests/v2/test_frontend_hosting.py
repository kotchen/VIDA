from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.v2.services.frontend import install_v2_frontend


class V2FrontendHostingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dist = Path(self.temp.name)
        (self.dist / "assets").mkdir()
        (self.dist / "index.html").write_text(
            "<!doctype html><title>VIDA V2 TEST</title>",
            encoding="utf-8",
        )
        (self.dist / "assets" / "app.js").write_text(
            "window.VIDA_V2 = true",
            encoding="utf-8",
        )
        app = FastAPI()

        @app.get("/")
        def legacy_root():
            return "legacy-root"

        install_v2_frontend(app, self.dist)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_spa_routes_return_only_the_built_index(self):
        for path in ("/v2", "/v2/dashboard", "/v2/episodes/episode-1"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn("VIDA V2 TEST", response.text)

        head = self.client.head("/v2/dashboard")
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.content, b"")

    def test_assets_are_static_and_traversal_or_file_fallback_is_rejected(self):
        asset = self.client.get("/v2/assets/app.js")
        self.assertEqual(asset.status_code, 200)
        self.assertEqual(asset.text, "window.VIDA_V2 = true")

        for path in (
            "/v2/app.js",
            "/v2/assets/missing.js",
            "/v2/assets/%2e%2e/index.html",
            "/v2/../static/index.html",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("VIDA V2 TEST", response.text)

    def test_api_and_legacy_routes_are_not_shadowed(self):
        api = self.client.get("/api/v2/not-a-route")
        self.assertEqual(api.status_code, 404)
        self.assertTrue(api.headers["content-type"].startswith("application/json"))
        self.assertNotIn("VIDA V2 TEST", api.text)

        legacy = self.client.get("/")
        self.assertEqual(legacy.status_code, 200)
        self.assertEqual(legacy.json(), "legacy-root")


if __name__ == "__main__":
    unittest.main()
