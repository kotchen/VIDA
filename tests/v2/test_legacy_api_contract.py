from __future__ import annotations

import importlib
import json
import io
import os
import subprocess
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
            "/api/v2/events": {"get"},
            "/api/v2/dashboard": {"get"},
            "/api/v2/provider-profiles": {"get", "post"},
            "/api/v2/provider-profiles/models": {"post"},
            "/api/v2/provider-profiles/{profile_id}": {"delete", "get", "patch"},
            "/api/v2/provider-profiles/{profile_id}/test": {"post"},
            "/api/v2/episodes": {"get", "post"},
            "/api/v2/episodes/{episode_id}": {"delete", "get"},
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
        self._assert_all_local_refs_resolve(schema)

    def _assert_all_local_refs_resolve(self, document):
        def resolve(reference):
            self.assertTrue(reference.startswith("#/"), reference)
            current = document
            for raw_part in reference[2:].split("/"):
                part = raw_part.replace("~1", "/").replace("~0", "~")
                self.assertIn(part, current, reference)
                current = current[part]

        def walk(value):
            if isinstance(value, dict):
                if "$ref" in value:
                    resolve(value["$ref"])
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(document)

    def test_contract_document_lists_every_approved_route(self):
        contract = Path("docs/api/v2-api-contract.md").read_text(encoding="utf-8")
        for route in (
            "GET /api/v2/events",
            "GET /api/v2/episodes",
            "DELETE /api/v2/episodes/{id}",
            "POST /api/v2/provider-profiles/models",
            "POST /api/v2/provider-profiles/{id}/test",
            "POST /api/v2/episodes/{id}/summary/regenerate",
            "POST /api/v2/episodes/{id}/chapters/regenerate",
            "GET /api/v2/jobs/{jobId}",
            "HEAD /api/v2/episodes/{id}/media",
        ):
            with self.subTest(route=route):
                self.assertIn(route, contract)

class RealBackendImportTests(unittest.TestCase):
    def test_real_backend_main_imports_from_repo_root_and_v1_routes_respond(self):
        script = r'''
import json, tempfile
import logging
from pathlib import Path
from fastapi.testclient import TestClient
import backend.main as main
with tempfile.TemporaryDirectory() as directory:
    main.TEMP_DIR = Path(directory)
    main.TASKS_FILE = main.TEMP_DIR / "tasks.json"
    main.tasks = {}
    main.active_tasks = {}
    main.processing_urls = set()
    client = TestClient(main.app)
    result = {
        "module": main.__name__,
        "files": [client.get("/api/files").status_code, sorted(client.get("/api/files").json())],
        "active": [client.get("/api/tasks/active").status_code, sorted(client.get("/api/tasks/active").json())],
        "missing": client.get("/api/task-status/missing").status_code,
        "invalid": client.get("/api/download/not-markdown.txt").status_code,
        "sensitiveLoggersDisabled": {
            name: logging.getLogger(name).disabled
            for name in ("httpx", "httpcore", "openai", "urllib3")
        },
    }
    print(json.dumps(result))
'''
        result = self._run_import(script, Path.cwd())
        payload = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(payload["module"], "backend.main")
        self.assertEqual(payload["files"], [200, ["groups"]])
        self.assertEqual(
            payload["active"], [200, ["active_tasks", "processing_urls", "task_ids"]]
        )
        self.assertEqual((payload["missing"], payload["invalid"]), (404, 400))
        self.assertTrue(all(payload["sensitiveLoggersDisabled"].values()))

    def test_real_main_imports_from_backend_working_directory(self):
        result = self._run_import(
            "import main; print(main.__name__, len(main.app.routes))",
            Path.cwd() / "backend",
        )
        self.assertRegex(result.stdout, r"main \d+")

    @staticmethod
    def _run_import(script, cwd):
        environment = os.environ.copy()
        environment.update(
            OPENAI_API_KEY="offline-import-key",
            OPENAI_BASE_URL="https://provider.example/v1",
        )
        return subprocess.run(
            [sys.executable, "-c", script],
            cwd=cwd,
            env=environment,
            text=True,
            capture_output=True,
            timeout=30,
            check=True,
        )


if __name__ == "__main__":
    unittest.main()
