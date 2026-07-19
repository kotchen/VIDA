from __future__ import annotations

import importlib
import io
import sys
import tempfile
import types
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


def _processor_module(name: str, class_name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    setattr(module, class_name, type(class_name, (), {}))
    return module


class LegacyApiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        stubs = {
            "backend.video_processor": _processor_module("backend.video_processor", "VideoProcessor"),
            "backend.transcriber": _processor_module("backend.transcriber", "Transcriber"),
            "backend.summarizer": _processor_module("backend.summarizer", "Summarizer"),
            "backend.translator": _processor_module("backend.translator", "Translator"),
        }
        settings = types.ModuleType("backend.model_settings")
        settings.validate_temperature = lambda value: float(value)
        stubs["backend.model_settings"] = settings
        sys.modules.pop("backend.main", None)
        original_exists = Path.exists

        def isolated_exists(path: Path) -> bool:
            if path.name == "tasks.json":
                return False
            return original_exists(path)

        with patch.dict(sys.modules, stubs), patch.object(Path, "exists", isolated_exists):
            cls.main = importlib.import_module("backend.main")
        cls.temp = tempfile.TemporaryDirectory()
        cls.main.TEMP_DIR = Path(cls.temp.name)
        cls.main.TASKS_FILE = cls.main.TEMP_DIR / "tasks.json"
        cls.main.tasks = {}
        cls.main.active_tasks = {}
        cls.main.processing_urls = set()
        cls.client = TestClient(cls.main.app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.temp.cleanup()

    def test_backend_main_is_package_importable_with_processor_dependencies_stubbed(self):
        self.assertEqual(self.main.__name__, "backend.main")

    def test_representative_v1_routes_keep_status_and_top_level_shape(self):
        root = self.client.get("/")
        files = self.client.get("/api/files")
        active = self.client.get("/api/tasks/active")
        missing = self.client.get("/api/task-status/missing")
        invalid_download = self.client.get("/api/download/not-markdown.txt")

        self.assertEqual(root.status_code, 200)
        self.assertTrue(root.headers["content-type"].startswith("text/html"))
        self.assertEqual((files.status_code, set(files.json())), (200, {"groups"}))
        self.assertEqual(
            (active.status_code, set(active.json())),
            (200, {"active_tasks", "processing_urls", "task_ids"}),
        )
        self.assertEqual((missing.status_code, set(missing.json())), (404, {"detail"}))
        self.assertEqual(
            (invalid_download.status_code, set(invalid_download.json())), (400, {"detail"})
        )

    def test_openapi_has_unique_operations_and_exact_approved_v2_routes(self):
        expected = {
            "/api/v2/dashboard": {"get"},
            "/api/v2/provider-profiles": {"get", "post"},
            "/api/v2/provider-profiles/{profile_id}": {"delete", "get", "patch"},
            "/api/v2/provider-profiles/{profile_id}/test": {"post"},
            "/api/v2/episodes": {"get", "post"},
            "/api/v2/episodes/{episode_id}": {"get"},
            "/api/v2/episodes/{episode_id}/transcript": {"get"},
            "/api/v2/episodes/{episode_id}/summary": {"get"},
            "/api/v2/episodes/{episode_id}/chapters": {"get", "post"},
            "/api/v2/episodes/{episode_id}/chapters/{chapter_id}": {"delete", "patch"},
            "/api/v2/episodes/{episode_id}/export": {"get"},
            "/api/v2/episodes/{episode_id}/media": {"get", "head"},
            "/api/v2/episodes/{episode_id}/poster": {"get", "head"},
            "/api/v2/episodes/{episode_id}/cancel": {"post"},
            "/api/v2/episodes/{episode_id}/retry": {"post"},
            "/api/v2/episodes/{episode_id}/summary/regenerate": {"post"},
            "/api/v2/episodes/{episode_id}/chapters/regenerate": {"post"},
            "/api/v2/jobs/{job_id}": {"get"},
            "/api/v2/jobs/{job_id}/cancel": {"post"},
        }
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            schema = self.main.app.openapi()
        actual = {
            path: {method for method in operations if method != "parameters"}
            for path, operations in schema["paths"].items()
            if path.startswith("/api/v2/")
        }
        self.assertEqual(actual, expected)
        operation_ids = [
            operation["operationId"]
            for path, operations in schema["paths"].items()
            if path.startswith("/api/v2/")
            for method, operation in operations.items()
            if method != "parameters"
        ]
        self.assertEqual(len(operation_ids), len(set(operation_ids)))
        submit_body = schema["paths"]["/api/v2/episodes"]["post"]["requestBody"]
        self.assertEqual(
            set(submit_body["content"]), {"application/json", "multipart/form-data"}
        )

    def test_contract_document_lists_every_approved_route(self):
        contract = Path("docs/api/v2-api-contract.md").read_text(encoding="utf-8")
        for route in (
            "GET /api/v2/episodes",
            "POST /api/v2/provider-profiles/{id}/test",
            "POST /api/v2/episodes/{id}/summary/regenerate",
            "POST /api/v2/episodes/{id}/chapters/regenerate",
            "GET /api/v2/jobs/{jobId}",
            "HEAD /api/v2/episodes/{id}/media",
        ):
            with self.subTest(route=route):
                self.assertIn(route, contract)


if __name__ == "__main__":
    unittest.main()
