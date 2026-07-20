# VIDA 2.0 Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a backward-compatible `/api/v2` FastAPI backend with encrypted Provider Profiles, SQLite-backed projects, a durable FIFO job queue, bounded background concurrency, restart recovery, structured transcripts, summaries, chapters, media serving, and exports.

**Architecture:** Keep the current 1.x routes and `temp/` data untouched. Add a focused `backend/v2/` modular monolith: API routes call services, services call explicit-SQL repositories, and a single-process scheduler runs a configurable number of workers against SQLite as the queue source of truth. Store structured metadata in SQLite and large media/artifacts under `data/v2/`.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLite (`sqlite3`, WAL), `cryptography` AES-256-GCM, asyncio, Faster-Whisper, yt-dlp, FFmpeg/ffprobe, stdlib `unittest`, FastAPI `TestClient`.

## Global Constraints

- Preserve `/`, `/static/*`, every existing `/api/*` response, `temp/`, and `temp/tasks.json`; v2 uses only `/api/v2/*` and `data/v2/`.
- Do not import or migrate 1.x history; v2 manages only projects created after this implementation.
- Run one Uvicorn process. Internal workers enforce `V2_MAX_CONCURRENT_JOBS`, default `2`, and the value must be a positive integer.
- `V2_UPLOAD_MAX_GB` defaults to `5`; it must not change the existing `UPLOAD_MAX_MB=200` behavior.
- `VIDA_PROFILE_MASTER_KEY` must decode from URL-safe Base64 to exactly 32 bytes; never persist or log the plaintext master key or Provider API keys.
- Public JSON uses camelCase, ISO 8601 UTC timestamps, seconds for `*Sec`, and `{ "error": { "code", "message", "details", "requestId" } }` for every v2 error.
- Public states are exactly `queued`, `processing`, `completed`, `failed`, and `canceled`; `cancel_requested_at` remains an internal control field.
- Queue order is global FIFO by `(submitted_at, id)`; retries create a new attempt at the tail; `queuePosition` is computed, not stored.
- On startup, interrupted `processing` jobs return to `queued` and restart the pipeline from the beginning.
- Transcript success is the completion threshold. Optimization, summary, or generated-chapter failure produces a warning without failing the Episode.
- Generated chapter replacement must preserve manual chapters and previously committed artifacts until the replacement transaction succeeds.
- Use TDD for every task. Run the focused failing test before implementation, then the focused passing test, then the complete existing suite before committing.

## File Map

```text
backend/v2/
├── __init__.py
├── bootstrap.py                 # Runtime construction and FastAPI lifecycle
├── config.py                    # V2Settings validation
├── container.py                 # Explicit dependency container
├── database.py                  # Connections, migrations, transactions
├── domain.py                    # Internal dataclasses and state enums
├── errors.py                    # V2Error and exception handlers
├── router.py                    # /api/v2 router aggregation
├── schemas.py                   # Public Pydantic models
├── crypto.py                    # AES-256-GCM credential envelope
├── migrations/001_initial.sql   # Complete initial schema
├── api/provider_profiles.py
├── api/episodes.py
├── api/jobs.py
├── repositories/provider_profiles.py
├── repositories/episodes.py
├── repositories/chapters.py
├── repositories/jobs.py
├── services/provider_profiles.py
├── services/episodes.py
├── services/dashboard.py
├── services/exports.py
├── services/media.py
└── jobs/
    ├── models.py                # Pipeline input/result dataclasses
    ├── scheduler.py             # Worker pool and lifecycle
    ├── source_ingest.py         # Upload/URL media preparation
    ├── ai.py                    # Profile-bound optimization/summary/chapters
    └── pipeline.py              # End-to-end orchestration
```

---

### Task 1: Freeze 1.x Compatibility and Add Validated v2 Startup Settings

**Files:**
- Modify: `start.py:17-38`
- Modify: `start.py:105-141`
- Modify: `requirements.txt`
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Create: `backend/v2/__init__.py`
- Create: `backend/v2/config.py`
- Create: `tests/v2/__init__.py`
- Create: `tests/v2/test_config.py`
- Modify: `tests/test_start.py`

**Interfaces:**
- Produces: `V2Settings.from_environ(environ: Mapping[str, str], project_root: Path) -> V2Settings`.
- Produces: `StartupOptions.max_concurrent_jobs: int`, `StartupOptions.v2_upload_max_gb: int`, and `StartupOptions.profile_master_key: str`.
- Consumes: existing `parse_startup_options()` precedence rule: CLI overrides environment, environment overrides defaults.

- [ ] **Step 1: Write failing startup and settings tests**

```python
# tests/v2/test_config.py
import base64
import unittest
from pathlib import Path

from backend.v2.config import V2Settings


class V2SettingsTests(unittest.TestCase):
    def setUp(self):
        self.key = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")

    def test_defaults_and_paths(self):
        settings = V2Settings.from_environ(
            {"VIDA_PROFILE_MASTER_KEY": self.key}, Path("D:/vida")
        )
        self.assertEqual(settings.max_concurrent_jobs, 2)
        self.assertEqual(settings.upload_max_bytes, 5 * 1024**3)
        self.assertEqual(settings.data_dir, Path("D:/vida/data/v2"))
        self.assertEqual(settings.master_key, b"k" * 32)

    def test_rejects_invalid_worker_count_and_master_key(self):
        with self.assertRaisesRegex(ValueError, "V2_MAX_CONCURRENT_JOBS"):
            V2Settings.from_environ(
                {"VIDA_PROFILE_MASTER_KEY": self.key, "V2_MAX_CONCURRENT_JOBS": "0"},
                Path("."),
            )
        with self.assertRaisesRegex(ValueError, "VIDA_PROFILE_MASTER_KEY"):
            V2Settings.from_environ({"VIDA_PROFILE_MASTER_KEY": "bad"}, Path("."))
```

Add to `tests/test_start.py`:

```python
def test_v2_cli_values_override_environment(self):
    key = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")
    options = start.parse_startup_options(
        ["--max-concurrent-jobs", "3", "--profile-master-key", key],
        {
            "V2_MAX_CONCURRENT_JOBS": "2",
            "V2_UPLOAD_MAX_GB": "7",
            "VIDA_PROFILE_MASTER_KEY": "environment-key",
        },
    )
    self.assertEqual(options.max_concurrent_jobs, 3)
    self.assertEqual(options.v2_upload_max_gb, 7)
    self.assertEqual(options.profile_master_key, key)
```

- [ ] **Step 2: Run the tests and verify the missing module/fields fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.v2.test_config tests.test_start -v
```

Expected: FAIL because `backend.v2.config` and the three `StartupOptions` fields do not exist.

- [ ] **Step 3: Implement settings and startup propagation**

```python
# backend/v2/config.py
from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class V2Settings:
    data_dir: Path
    database_path: Path
    max_concurrent_jobs: int
    upload_max_bytes: int
    master_key: bytes

    @classmethod
    def from_environ(cls, environ: Mapping[str, str], project_root: Path) -> "V2Settings":
        try:
            workers = int(environ.get("V2_MAX_CONCURRENT_JOBS", "2"))
        except ValueError as exc:
            raise ValueError("V2_MAX_CONCURRENT_JOBS must be a positive integer") from exc
        if workers < 1:
            raise ValueError("V2_MAX_CONCURRENT_JOBS must be a positive integer")
        try:
            upload_gb = int(environ.get("V2_UPLOAD_MAX_GB", "5"))
        except ValueError as exc:
            raise ValueError("V2_UPLOAD_MAX_GB must be a positive integer") from exc
        if upload_gb < 1:
            raise ValueError("V2_UPLOAD_MAX_GB must be a positive integer")
        encoded = environ.get("VIDA_PROFILE_MASTER_KEY", "")
        try:
            master_key = base64.urlsafe_b64decode(encoded.encode("ascii"))
        except Exception as exc:
            raise ValueError("VIDA_PROFILE_MASTER_KEY must be URL-safe Base64") from exc
        if len(master_key) != 32:
            raise ValueError("VIDA_PROFILE_MASTER_KEY must decode to exactly 32 bytes")
        data_dir = project_root / "data" / "v2"
        return cls(data_dir, data_dir / "vida-v2.sqlite3", workers, upload_gb * 1024**3, master_key)
```

Extend `StartupOptions`, add `--max-concurrent-jobs` and `--profile-master-key`, resolve the three v2 values, then place them into the child Uvicorn environment as `V2_MAX_CONCURRENT_JOBS`, `V2_UPLOAD_MAX_GB`, and `VIDA_PROFILE_MASTER_KEY`. Add `import base64` to `tests/test_start.py`. Add `cryptography>=42.0.0` and `httpx>=0.27.0` to `requirements.txt`. Document the three environment variables in `.env.example` and pass them through `docker-compose.yml` without changing `UPLOAD_MAX_MB`.

- [ ] **Step 4: Run focused and full baseline suites**

Run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest tests.v2.test_config tests.test_start -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: dependency installation exits 0; both test commands PASS; the full command reports at least the existing 21 tests plus the new settings tests.

- [ ] **Step 5: Commit the startup boundary**

```powershell
git add start.py requirements.txt .env.example docker-compose.yml backend/v2/__init__.py backend/v2/config.py tests/v2/__init__.py tests/v2/test_config.py tests/test_start.py
git commit -m "feat(v2): add validated backend startup settings"
```

---

### Task 2: Add the SQLite Foundation and Initial Schema

**Files:**
- Create: `backend/v2/database.py`
- Create: `backend/v2/migrations/001_initial.sql`
- Create: `backend/v2/domain.py`
- Create: `tests/v2/test_database.py`

**Interfaces:**
- Consumes: `V2Settings.database_path` from Task 1.
- Produces: `Database.initialize()`, `Database.connect()`, `Database.transaction(immediate: bool = False)`.
- Produces: `EpisodeStatus`, `JobType`, `JobRecord`, and `EpisodeRecord` in `backend.v2.domain`.

- [ ] **Step 1: Write failing migration and constraint tests**

```python
# tests/v2/test_database.py
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.v2.database import Database


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "vida.sqlite3")
        self.db.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_initial_migration_creates_required_tables(self):
        with self.db.connect() as conn:
            names = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        self.assertTrue(
            {
                "episodes", "jobs", "transcript_segments", "summaries", "chapters",
                "provider_profiles", "provider_profile_revisions", "schema_migrations",
            }.issubset(names)
        )

    def test_only_one_active_job_is_allowed_per_episode(self):
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO provider_profiles(id,name,active_revision_id,created_at,updated_at) VALUES(?,?,?,?,?)",
                ("p1", "Primary", None, "2026-07-18T00:00:00Z", "2026-07-18T00:00:00Z"),
            )
            conn.execute(
                "INSERT INTO provider_profile_revisions(id,profile_id,version,base_url,model_id,temperature,encrypted_api_key,encryption_nonce,encryption_format_version,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("r1", "p1", 1, "https://api.example/v1", "model-a", 0.1, "ciphertext", "nonce", 1, "2026-07-18T00:00:00Z"),
            )
            conn.execute(
                "UPDATE provider_profiles SET active_revision_id=? WHERE id=?", ("r1", "p1")
            )
            conn.execute(
                "INSERT INTO episodes(id,title,source_type,status,progress,message,summary_language,provider_profile_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("e1", "Episode", "url", "queued", 0, "Queued", "zh", "p1", "2026-07-18T00:00:00Z", "2026-07-18T00:00:00Z"),
            )
            conn.execute(
                "INSERT INTO jobs(id,episode_id,type,attempt,status,provider_profile_revision_id,submitted_at,progress,message) VALUES(?,?,?,?,?,?,?,?,?)",
                ("j1", "e1", "process_episode", 1, "queued", "r1", "2026-07-18T00:00:00Z", 0, "Queued"),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO jobs(id,episode_id,type,attempt,status,provider_profile_revision_id,submitted_at,progress,message) VALUES(?,?,?,?,?,?,?,?,?)",
                    ("j2", "e1", "process_episode", 2, "processing", "r1", "2026-07-18T00:00:01Z", 1, "Processing"),
                )
```

- [ ] **Step 2: Run the focused test and verify it fails**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.v2.test_database -v
```

Expected: FAIL because `Database` and the schema do not exist.

- [ ] **Step 3: Implement the database wrapper, domain enums, and migration**

```python
# backend/v2/database.py
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    @contextmanager
    def transaction(self, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        migration = Path(__file__).parent / "migrations" / "001_initial.sql"
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.executescript(migration.read_text(encoding="utf-8"))
```

The SQL migration must create every table from the approved design, including `warnings_json TEXT NOT NULL DEFAULT '[]'`, `summary_language`, media MIME/path fields, timestamps, foreign keys, state `CHECK` constraints, indexes on `(status, submitted_at, id)` and `(created_at DESC)`, and this active-job constraint:

```sql
CREATE UNIQUE INDEX uq_jobs_one_active_per_episode
ON jobs(episode_id)
WHERE status IN ('queued', 'processing');
```

Define string enums in `domain.py` with exact serialized values and frozen dataclasses whose field names match the database columns after snake_case conversion:

```python
class EpisodeStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class JobType(str, Enum):
    PROCESS_EPISODE = "process_episode"
    REGENERATE_SUMMARY = "regenerate_summary"
    REGENERATE_CHAPTERS = "regenerate_chapters"
```

- [ ] **Step 4: Verify idempotent migration and the whole suite**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.v2.test_database -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: PASS; calling `Database.initialize()` twice must not change data or raise an error.

- [ ] **Step 5: Commit the persistence foundation**

```powershell
git add backend/v2/database.py backend/v2/domain.py backend/v2/migrations/001_initial.sql tests/v2/test_database.py
git commit -m "feat(v2): add SQLite schema and transaction layer"
```

---

### Task 3: Encrypt Provider Credentials and Version Provider Profiles

**Files:**
- Create: `backend/v2/crypto.py`
- Create: `backend/v2/repositories/__init__.py`
- Create: `backend/v2/repositories/provider_profiles.py`
- Create: `backend/v2/services/__init__.py`
- Create: `backend/v2/services/provider_profiles.py`
- Create: `tests/v2/test_provider_profile_repository.py`

**Interfaces:**
- Consumes: `Database` and `V2Settings.master_key`.
- Produces: `CredentialCipher.encrypt(plaintext: str) -> EncryptedCredential` and `decrypt(envelope: EncryptedCredential) -> str`.
- Produces: `ProviderProfileService.create_profile()`, `update_profile()`, `delete_profile()`, `get_profile()`, `list_profiles()`, and `get_revision_credentials()`.

- [ ] **Step 1: Write failing encryption and immutable-revision tests**

```python
# tests/v2/test_provider_profile_repository.py
import tempfile
import unittest
from pathlib import Path

from backend.v2.crypto import CredentialCipher
from backend.v2.database import Database
from backend.v2.repositories.provider_profiles import ProviderProfileRepository
from backend.v2.services.provider_profiles import ProviderProfileService


class ProviderProfileRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "db.sqlite3")
        self.db.initialize()
        self.service = ProviderProfileService(
            ProviderProfileRepository(self.db), CredentialCipher(b"m" * 32)
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_key_is_encrypted_and_update_creates_revision(self):
        created = self.service.create_profile(
            "Primary", "https://api.example/v1", "secret-key", "model-a", 0.1
        )
        updated = self.service.update_profile(created.id, model_id="model-b")
        self.assertEqual(created.revision, 1)
        self.assertEqual(updated.revision, 2)
        first = self.service.get_revision_credentials(created.active_revision_id)
        second = self.service.get_revision_credentials(updated.active_revision_id)
        self.assertEqual(first.api_key, "secret-key")
        self.assertEqual(second.api_key, "secret-key")
        with self.db.connect() as conn:
            stored = conn.execute(
                "SELECT encrypted_api_key FROM provider_profile_revisions WHERE id=?",
                (created.active_revision_id,),
            ).fetchone()[0]
        self.assertNotIn("secret-key", stored)

    def test_delete_hides_profile_but_keeps_revision(self):
        created = self.service.create_profile(
            "Primary", "https://api.example/v1", "secret-key", "model-a", 0.1
        )
        self.service.delete_profile(created.id)
        self.assertEqual(self.service.list_profiles(), [])
        credentials = self.service.get_revision_credentials(created.active_revision_id)
        self.assertEqual(credentials.model_id, "model-a")
```

- [ ] **Step 2: Run the focused test and verify imports fail**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.v2.test_provider_profile_repository -v
```

Expected: FAIL because crypto, repository, and service modules do not exist.

- [ ] **Step 3: Implement AES-GCM and revision transactions**

```python
# backend/v2/crypto.py
import base64
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


@dataclass(frozen=True)
class EncryptedCredential:
    ciphertext: str
    nonce: str
    format_version: int = 1


class CredentialCipher:
    _AAD = b"vida-provider-profile:v1"

    def __init__(self, master_key: bytes):
        if len(master_key) != 32:
            raise ValueError("master key must contain exactly 32 bytes")
        self._aes = AESGCM(master_key)

    def encrypt(self, plaintext: str) -> EncryptedCredential:
        if not plaintext:
            raise ValueError("apiKey must not be empty")
        nonce = os.urandom(12)
        ciphertext = self._aes.encrypt(nonce, plaintext.encode("utf-8"), self._AAD)
        return EncryptedCredential(
            base64.b64encode(ciphertext).decode("ascii"),
            base64.b64encode(nonce).decode("ascii"),
        )

    def decrypt(self, envelope: EncryptedCredential) -> str:
        if envelope.format_version != 1:
            raise ValueError("unsupported credential format")
        plaintext = self._aes.decrypt(
            base64.b64decode(envelope.nonce),
            base64.b64decode(envelope.ciphertext),
            self._AAD,
        )
        return plaintext.decode("utf-8")
```

Implement repository transactions so create inserts profile plus revision 1, update copies the prior encrypted credential when `api_key` is omitted, every valid update creates the next integer revision, delete sets `deleted_at`, and revision lookup ignores profile deletion. Return immutable service DTOs with `api_key_masked = "••••••••" + api_key[-4:]` and never include the plaintext key in profile DTOs.

- [ ] **Step 4: Run security-focused and full tests**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.v2.test_provider_profile_repository -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: PASS; SQL inspection confirms no plaintext key.

- [ ] **Step 5: Commit Provider persistence**

```powershell
git add backend/v2/crypto.py backend/v2/repositories backend/v2/services tests/v2/test_provider_profile_repository.py
git commit -m "feat(v2): encrypt and version provider profiles"
```

---

### Task 4: Mount the v2 API and Implement Provider Profile CRUD/Test

**Files:**
- Create: `backend/v2/errors.py`
- Create: `backend/v2/schemas.py`
- Create: `backend/v2/container.py`
- Create: `backend/v2/router.py`
- Create: `backend/v2/api/__init__.py`
- Create: `backend/v2/api/provider_profiles.py`
- Create: `backend/v2/bootstrap.py`
- Modify: `backend/main.py:1-45`
- Create: `tests/v2/test_provider_profile_api.py`
- Create: `tests/v2/test_error_contract.py`

**Interfaces:**
- Consumes: `ProviderProfileService` from Task 3.
- Produces: `install_v2(app: FastAPI, project_root: Path) -> V2Runtime`.
- Produces: `build_test_runtime(data_dir: Path, master_key: bytes) -> V2Runtime` without reading process environment.
- Produces: Provider CRUD routes and `POST /api/v2/provider-profiles/{id}/test`.
- Produces: `V2Error(code: str, message: str, status_code: int, details: dict)`.

- [ ] **Step 1: Write failing API contract tests**

```python
# tests/v2/test_provider_profile_api.py
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.v2.bootstrap import build_test_runtime


class ProviderProfileApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        runtime = build_test_runtime(Path(self.temp.name), b"m" * 32)
        runtime.provider_tester = AsyncMock(return_value=(True, 15, True, "Connection successful"))
        app = FastAPI()
        runtime.install(app)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_crud_never_returns_plaintext_key(self):
        response = self.client.post("/api/v2/provider-profiles", json={
            "name": "Primary", "baseUrl": "https://api.example/v1",
            "apiKey": "secret-key", "modelId": "model-a", "temperature": 0.1,
        })
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertNotIn("apiKey", body)
        self.assertTrue(body["hasApiKey"])
        self.assertTrue(body["apiKeyMasked"].endswith("-key"))
        profile_id = body["id"]
        patched = self.client.patch(
            f"/api/v2/provider-profiles/{profile_id}", json={"modelId": "model-b"}
        ).json()
        self.assertEqual(patched["revision"], 2)
        self.assertEqual(self.client.delete(f"/api/v2/provider-profiles/{profile_id}").status_code, 204)

    def test_connection_test_is_sanitized(self):
        created = self.client.post("/api/v2/provider-profiles", json={
            "name": "Primary", "baseUrl": "https://api.example/v1",
            "apiKey": "secret-key", "modelId": "model-a", "temperature": 0.1,
        }).json()
        response = self.client.post(f"/api/v2/provider-profiles/{created['id']}/test")
        self.assertEqual(response.json(), {
            "ok": True, "latencyMs": 15, "modelAvailable": True,
            "message": "Connection successful",
        })
```

```python
# tests/v2/test_error_contract.py
def test_validation_errors_use_v2_envelope(self):
    response = self.client.post("/api/v2/provider-profiles", json={})
    self.assertEqual(response.status_code, 422)
    self.assertEqual(response.json()["error"]["code"], "validation_error")
    self.assertIn("requestId", response.json()["error"])
```

- [ ] **Step 2: Run the API tests and verify missing bootstrap fails**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.v2.test_provider_profile_api tests.v2.test_error_contract -v
```

Expected: FAIL because runtime, routes, schemas, and v2 handlers do not exist.

- [ ] **Step 3: Implement schemas, handlers, runtime wiring, and routes**

Use a shared Pydantic base with aliases:

```python
class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
```

Implement request models with `HttpUrl`, temperature `ge=0, le=2`, and non-empty stripped strings. Implement `V2Error` plus handlers for `V2Error` and `RequestValidationError`; add a request-ID middleware that preserves an incoming `X-Request-ID` or creates a UUID. The Provider connection tester must instantiate an OpenAI client from the selected immutable revision, call `models.list` in `asyncio.to_thread`, compare `model_id` to returned IDs, measure monotonic latency, and convert all upstream failures to `provider_connection_failed` without returning the upstream body.

`backend/main.py` receives only these v2 composition changes:

```python
from v2.bootstrap import install_v2

# after PROJECT_ROOT is defined and the FastAPI app exists
v2_runtime = install_v2(app, PROJECT_ROOT)
```

Do not move or rename any 1.x route.

- [ ] **Step 4: Verify v2 API and 1.x regression**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.v2.test_provider_profile_api tests.v2.test_error_contract -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: PASS; Profile responses never contain an `apiKey` property; existing 1.x tests remain green.

- [ ] **Step 5: Commit the API foundation**

```powershell
git add backend/main.py backend/v2/errors.py backend/v2/schemas.py backend/v2/container.py backend/v2/router.py backend/v2/api backend/v2/bootstrap.py tests/v2/test_provider_profile_api.py tests/v2/test_error_contract.py
git commit -m "feat(v2): add provider profile API"
```

---

### Task 5: Persist Episodes, Transcripts, Summaries, Chapters, and Warnings

**Files:**
- Create: `backend/v2/repositories/episodes.py`
- Create: `backend/v2/repositories/chapters.py`
- Create: `backend/v2/services/episodes.py`
- Create: `tests/v2/test_episode_repository.py`
- Create: `tests/v2/test_chapter_repository.py`

**Interfaces:**
- Consumes: `Database` and domain dataclasses.
- Produces: `EpisodeRepository.create()`, `get()`, `list_projects()`, `replace_transcript()`, `replace_summary()`, `set_warnings()`, and `latest_completed()`.
- Produces: `ChapterRepository.list()`, `create_manual()`, and `replace_generated()`.

- [ ] **Step 1: Write failing atomic-content tests**

```python
# tests/v2/test_episode_repository.py
def test_replace_transcript_is_atomic_and_sorted(self):
    self.repo.create(self.episode)
    self.repo.replace_transcript("e1", [
        TranscriptSegmentRecord("s2", "e1", 1, 5.0, 8.0, "B", "second"),
        TranscriptSegmentRecord("s1", "e1", 0, 0.0, 5.0, "A", "first"),
    ])
    rows = self.repo.get_transcript("e1")
    self.assertEqual([row.id for row in rows], ["s1", "s2"])

def test_latest_completed_ignores_failed_and_canceled(self):
    self.repo.create(self.completed_old)
    self.repo.create(self.failed_new)
    self.assertEqual(self.repo.latest_completed().id, self.completed_old.id)
```

```python
# tests/v2/test_chapter_repository.py
def test_regeneration_preserves_manual_chapters(self):
    self.repo.create_manual("e1", 12.0, "Manual", 18.0, "/poster", False)
    self.repo.replace_generated("e1", [
        ChapterRecord("g1", "e1", 0.0, "Opening", 12.0, "/poster", False, "generated")
    ])
    self.repo.replace_generated("e1", [
        ChapterRecord("g2", "e1", 0.0, "New opening", 12.0, "/poster", False, "generated")
    ])
    chapters = self.repo.list("e1")
    self.assertEqual({c.title for c in chapters}, {"Manual", "New opening"})
```

- [ ] **Step 2: Run focused tests and verify repository imports fail**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.v2.test_episode_repository tests.v2.test_chapter_repository -v
```

Expected: FAIL because Episode and Chapter repositories do not exist.

- [ ] **Step 3: Implement explicit-SQL repositories and service guards**

Implement every multi-row replacement inside one transaction: validate the Episode first, delete only the target content class, insert ordered records, then commit. `replace_generated()` must execute `DELETE FROM chapters WHERE episode_id=? AND source='generated'`. `list_projects(limit, offset)` must validate `0 <= offset`, `1 <= limit <= 100`, sort `created_at DESC, id DESC`, and return no source path. `EpisodeService` converts missing rows to `episode_not_found` and incomplete summaries to `summary_not_found`.

Use JSON encoding only for the small `ProcessingWarning[]` field; transcript, summary, and chapter records remain normalized rows. Parse warnings through a typed dataclass before returning them.

- [ ] **Step 4: Verify atomic persistence and full regression**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.v2.test_episode_repository tests.v2.test_chapter_repository -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: PASS; generated replacement keeps manual rows and transcript order is deterministic.

- [ ] **Step 5: Commit content repositories**

```powershell
git add backend/v2/repositories/episodes.py backend/v2/repositories/chapters.py backend/v2/services/episodes.py tests/v2/test_episode_repository.py tests/v2/test_chapter_repository.py
git commit -m "feat(v2): persist episode content and chapters"
```

---

### Task 6: Implement Durable FIFO Job Claims and State Transitions

**Files:**
- Create: `backend/v2/repositories/jobs.py`
- Create: `tests/v2/test_job_repository.py`

**Interfaces:**
- Consumes: `Database`, `JobRecord`, and Episode status rules.
- Produces: `enqueue()`, `claim_next(worker_id: str, now: str)`, `update_progress()`, `request_cancel()`, `complete()`, `fail()`, `mark_canceled()`, `recover_interrupted()`, and `queue_position()`.

- [ ] **Step 1: Write failing FIFO, uniqueness, and recovery tests**

```python
# tests/v2/test_job_repository.py
def test_claims_fifo_once_across_concurrent_callers(self):
    self.enqueue_episode("e1", "j1", "2026-07-18T00:00:00Z")
    self.enqueue_episode("e2", "j2", "2026-07-18T00:00:01Z")
    claimed = []
    barrier = threading.Barrier(3)

    def claim(worker):
        barrier.wait()
        job = self.repo.claim_next(worker, "2026-07-18T00:01:00Z")
        claimed.append(job.id if job else None)

    threads = [threading.Thread(target=claim, args=(f"w{i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    self.assertEqual(set(claimed), {"j1", "j2"})

def test_recovery_requeues_processing_without_new_job(self):
    self.enqueue_episode("e1", "j1", "2026-07-18T00:00:00Z")
    self.repo.claim_next("worker", "2026-07-18T00:00:01Z")
    self.assertEqual(self.repo.recover_interrupted("Restarted"), 1)
    self.assertEqual(self.repo.get("j1").status, "queued")
    self.assertEqual(self.repo.queue_position("j1"), 1)
```

- [ ] **Step 2: Run the focused tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.v2.test_job_repository -v
```

Expected: FAIL because `JobRepository` does not exist.

- [ ] **Step 3: Implement transactional claims and legal transitions**

The claim operation must be one `BEGIN IMMEDIATE` transaction:

```python
def claim_next(self, worker_id: str, now: str) -> JobRecord | None:
    with self.db.transaction(immediate=True) as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status='queued' ORDER BY submitted_at,id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        changed = conn.execute(
            "UPDATE jobs SET status='processing',started_at=?,heartbeat_at=?,worker_id=?,progress=1,message='Preparing' WHERE id=? AND status='queued'",
            (now, now, worker_id, row["id"]),
        ).rowcount
        if changed != 1:
            return None
        if row["type"] == "process_episode":
            conn.execute(
                "UPDATE episodes SET status='processing',progress=1,message='Preparing',updated_at=? WHERE id=?",
                (now, row["episode_id"]),
            )
        return self._get_with_connection(conn, row["id"])
```

Every terminal transition must include `WHERE status='processing'`; invalid transitions raise a domain `InvalidJobState` that the service maps to HTTP 409. `recover_interrupted()` updates the same existing jobs to queued, clears worker/start/heartbeat fields, resets progress to zero, updates matching process Episodes, and returns the affected count. Queue position counts queued jobs with an earlier `(submitted_at, id)` pair and returns one-based position.

- [ ] **Step 4: Run repository concurrency tests repeatedly**

```powershell
1..10 | ForEach-Object { .\.venv\Scripts\python.exe -m unittest tests.v2.test_job_repository -q; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: all ten focused runs and the full suite PASS; no duplicate claim occurs.

- [ ] **Step 5: Commit durable queue primitives**

```powershell
git add backend/v2/repositories/jobs.py tests/v2/test_job_repository.py
git commit -m "feat(v2): add durable FIFO job repository"
```

---

### Task 7: Run a Bounded Scheduler and Recover Jobs at Startup

**Files:**
- Create: `backend/v2/jobs/__init__.py`
- Create: `backend/v2/jobs/models.py`
- Create: `backend/v2/jobs/scheduler.py`
- Modify: `backend/v2/bootstrap.py`
- Modify: `backend/v2/container.py`
- Create: `tests/v2/test_scheduler.py`
- Create: `tests/v2/test_job_recovery.py`

**Interfaces:**
- Consumes: `JobRepository.claim_next()` and a `JobExecutor` protocol.
- Produces: `Scheduler.start()`, `notify()`, and `stop(grace_period_sec: float)`.
- Produces: `JobExecutor.execute(job: JobRecord, cancel_check: Callable[[], bool]) -> Awaitable[None]`.

- [ ] **Step 1: Write failing bounded-concurrency and restart tests**

```python
# tests/v2/test_scheduler.py
async def test_never_exceeds_configured_concurrency(self):
    executor = BlockingExecutor()
    scheduler = Scheduler(self.jobs, executor, max_workers=2, poll_interval_sec=0.01)
    await scheduler.start()
    for index in range(5):
        self.enqueue(index)
    scheduler.notify()
    await executor.wait_until_started(2)
    self.assertEqual(executor.max_active, 2)
    self.assertEqual(self.count_status("processing"), 2)
    executor.release_all()
    await scheduler.wait_until_idle(timeout_sec=2)
    await scheduler.stop(0.5)
    self.assertEqual(executor.started_ids, ["j0", "j1", "j2", "j3", "j4"])
```

```python
# tests/v2/test_job_recovery.py
async def test_start_recovers_processing_before_workers_claim(self):
    self.create_processing_job("j1")
    scheduler = Scheduler(self.jobs, RecordingExecutor(), 1, 0.01)
    await scheduler.start()
    await scheduler.wait_until_idle(timeout_sec=2)
    await scheduler.stop(0.5)
    self.assertEqual(self.executor.started_ids, ["j1"])
```

- [ ] **Step 2: Run focused async tests and verify missing scheduler fails**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.v2.test_scheduler tests.v2.test_job_recovery -v
```

Expected: FAIL because Scheduler and JobExecutor do not exist.

- [ ] **Step 3: Implement the scheduler lifecycle**

Create exactly `max_workers` long-lived asyncio worker tasks. Each loop claims one job in `asyncio.to_thread`, waits on an `asyncio.Event` plus a short polling timeout when empty, executes one job, catches domain `JobCanceled` and calls `jobs.mark_canceled()`, catches task-level `asyncio.CancelledError` without changing the database row, catches all other exceptions and calls `jobs.fail()`, then immediately tries the next job. `start()` must call `recover_interrupted("Service restarted; job requeued")` before creating workers. `stop()` stops claims, waits up to the grace period, cancels remaining worker coroutines without setting the user cancellation flag, terminates their owned subprocesses through coroutine cancellation, and leaves unfinished rows as processing for the next startup recovery.

Wire `V2Runtime.start()` and `.stop()` into FastAPI startup/shutdown event handlers. Do not create Scheduler tasks at module import time.

- [ ] **Step 4: Verify bounded execution and full regression**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.v2.test_scheduler tests.v2.test_job_recovery -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: PASS; the Fake executor records a maximum of two active jobs and FIFO start order.

- [ ] **Step 5: Commit scheduler lifecycle**

```powershell
git add backend/v2/jobs backend/v2/bootstrap.py backend/v2/container.py tests/v2/test_scheduler.py tests/v2/test_job_recovery.py
git commit -m "feat(v2): run bounded persistent job scheduler"
```

---

### Task 8: Accept Streaming Upload and URL Episode Submissions

**Files:**
- Create: `backend/v2/api/episodes.py`
- Modify: `backend/v2/router.py`
- Modify: `backend/v2/schemas.py`
- Modify: `backend/v2/services/episodes.py`
- Create: `tests/v2/test_episode_submission_api.py`

**Interfaces:**
- Consumes: active Provider Profile revision, Episode repository, Job repository, Scheduler.notify(), and upload size settings.
- Produces: `POST /api/v2/episodes` for multipart and JSON content types.
- Produces: `EpisodeService.submit_upload()` and `submit_url()` returning queued Episode DTOs.

- [ ] **Step 1: Write failing submission and cleanup tests**

```python
# tests/v2/test_episode_submission_api.py
def test_upload_is_queued_only_after_complete_write(self):
    profile = self.create_profile()
    response = self.client.post(
        "/api/v2/episodes",
        data={"providerProfileId": profile["id"], "summaryLanguage": "zh"},
        files={"file": ("clip.txt", b"hello world", "text/plain")},
    )
    self.assertEqual(response.status_code, 202)
    body = response.json()
    self.assertEqual(body["status"], "queued")
    self.assertEqual(body["queuePosition"], 1)
    self.assertTrue((self.data_dir / "episodes" / body["id"] / "source" / "clip.txt").is_file())

def test_json_url_submission_uses_same_queue(self):
    profile = self.create_profile()
    response = self.client.post("/api/v2/episodes", json={
        "sourceUrl": "https://example.com/video",
        "providerProfileId": profile["id"], "summaryLanguage": "en",
    })
    self.assertEqual(response.status_code, 202)
    self.assertEqual(response.headers["Location"], f"/api/v2/episodes/{response.json()['id']}")

def test_oversized_upload_removes_partial_file(self):
    self.runtime.settings = replace(self.runtime.settings, upload_max_bytes=4)
    response = self.client.post(
        "/api/v2/episodes", data={"providerProfileId": self.profile_id, "summaryLanguage": "zh"},
        files={"file": ("large.txt", b"12345", "text/plain")},
    )
    self.assertEqual(response.status_code, 413)
    self.assertEqual(list(self.data_dir.rglob("*.part")), [])
```

- [ ] **Step 2: Run submission tests and verify route failure**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.v2.test_episode_submission_api -v
```

Expected: FAIL with 404 for `POST /api/v2/episodes`.

- [ ] **Step 3: Implement content-type dispatch and atomic enqueue**

Use `Request.headers["content-type"]` to select multipart or JSON parsing. Reject all other content types with `unsupported_media_type`. Stream upload chunks of exactly 1 MiB to `{filename}.part`, increment size before writing, unlink on disconnect/error/overflow, reject empty files, sanitize the basename, validate the approved extension set, atomically rename to the final source path, and then create Episode plus first Job in one database transaction. If the database transaction fails after rename, unlink the orphan source. URL submissions validate `HttpUrl` and store the normalized URL without downloading it in the request.

Snapshot `active_revision_id` during the enqueue transaction. A deleted/missing Profile yields `provider_profile_inactive` with HTTP 422. Return 202 plus Location and notify the Scheduler only after commit.

- [ ] **Step 4: Verify both source types and full regression**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.v2.test_episode_submission_api -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: PASS; oversize and interrupted uploads leave no `.part` file or Episode row.

- [ ] **Step 5: Commit submission endpoints**

```powershell
git add backend/v2/api/episodes.py backend/v2/router.py backend/v2/schemas.py backend/v2/services/episodes.py tests/v2/test_episode_submission_api.py
git commit -m "feat(v2): queue upload and URL episodes"
```

---

### Task 9: Ingest Media, Probe Metadata, Generate Posters, and Reconcile Files

**Files:**
- Create: `backend/v2/jobs/source_ingest.py`
- Create: `backend/v2/services/media.py`
- Modify: `backend/v2/bootstrap.py`
- Create: `tests/v2/test_source_ingest.py`
- Create: `tests/v2/test_file_recovery.py`

**Interfaces:**
- Consumes: Episode source metadata and a cancellation callback.
- Produces: `SourceIngestor.prepare(episode: EpisodeRecord, attempt_dir: Path, cancel_check) -> PreparedSource`.
- Produces: `MediaService.commit_file(staged: Path, final: Path)` and `reconcile_orphans()`.

- [ ] **Step 1: Write failing subprocess and orphan-recovery tests**

```python
# tests/v2/test_source_ingest.py
async def test_upload_is_probed_and_poster_is_generated(self):
    runner = FakeProcessRunner(
        ffprobe_stdout='{"format":{"duration":"12.5"},"streams":[{"codec_type":"video","width":1920,"height":1080}]}',
        generated_files={"poster.jpg": b"jpeg"},
    )
    prepared = await SourceIngestor(runner, FakeDownloader()).prepare(
        self.upload_episode, self.attempt_dir, lambda: False
    )
    self.assertEqual(prepared.duration_sec, 12.5)
    self.assertEqual(prepared.resolution, "1080p")
    self.assertEqual(prepared.media_content_type, "video/mp4")
    self.assertTrue(prepared.poster_path.name.endswith(".jpg"))

async def test_cancel_terminates_running_process(self):
    runner = BlockingProcessRunner()
    task = asyncio.create_task(
        SourceIngestor(runner, FakeDownloader()).prepare(
            self.upload_episode, self.attempt_dir, lambda: True
        )
    )
    with self.assertRaises(JobCanceled):
        await task
    self.assertTrue(runner.terminated)
```

```python
# tests/v2/test_file_recovery.py
def test_reconcile_removes_unreferenced_attempts_not_committed_artifacts(self):
    orphan = self.data_dir / "episodes/e1/attempts/orphan/file.tmp"
    committed = self.data_dir / "episodes/e1/artifacts/transcript.json"
    orphan.parent.mkdir(parents=True)
    committed.parent.mkdir(parents=True)
    orphan.write_bytes(b"orphan")
    committed.write_bytes(b"keep")
    self.media.reconcile_orphans()
    self.assertFalse(orphan.exists())
    self.assertTrue(committed.exists())
```

- [ ] **Step 2: Run focused tests and verify missing ingestor fails**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.v2.test_source_ingest tests.v2.test_file_recovery -v
```

Expected: FAIL because SourceIngestor and MediaService do not exist.

- [ ] **Step 3: Implement cancellable source preparation**

Define `ProcessRunner` and `Downloader` protocols for deterministic tests. Upload sources stay in their source directory. URL sources use yt-dlp to download a stable playable media file into the attempt directory. Invoke ffprobe with JSON output and no shell, derive duration and resolution, and invoke FFmpeg to generate one JPEG poster for video. Audio-only sources reuse the existing `static/sipsip.png` asset and omit resolution. Every subprocess must be created with asyncio subprocess APIs, terminate on cancellation, wait for exit, and raise a sanitized domain error on non-zero status.

`MediaService.commit_file()` creates the destination parent and uses `Path.replace()` only when staged and final paths resolve beneath `data/v2`. Reconciliation removes stale `.part` files and unreferenced attempt directories, but obtains committed relative paths from repositories before deletion. Call `reconcile_orphans()` in `V2Runtime.start()` after database migration/recovery and before Scheduler workers begin claiming jobs.

- [ ] **Step 4: Verify source preparation and path safety**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.v2.test_source_ingest tests.v2.test_file_recovery -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: PASS; cancellation terminates the fake subprocess and committed files survive reconciliation.

- [ ] **Step 5: Commit media ingestion**

```powershell
git add backend/v2/jobs/source_ingest.py backend/v2/services/media.py backend/v2/bootstrap.py tests/v2/test_source_ingest.py tests/v2/test_file_recovery.py
git commit -m "feat(v2): prepare and reconcile episode media"
```

---

### Task 10: Produce Structured Transcripts, Summaries, Warnings, and Chapters

**Files:**
- Modify: `backend/transcriber.py:1-125`
- Create: `backend/v2/jobs/ai.py`
- Create: `backend/v2/jobs/pipeline.py`
- Modify: `backend/v2/jobs/models.py`
- Modify: `backend/v2/container.py`
- Create: `tests/v2/test_structured_transcriber.py`
- Create: `tests/v2/test_pipeline.py`
- Modify: `tests/test_transcriber.py`

**Interfaces:**
- Consumes: `PreparedSource`, immutable Provider revision credentials, repositories, and MediaService.
- Produces: `Transcriber.transcribe_segments(audio_path: str, language: str | None) -> StructuredTranscription` while preserving existing `transcribe()` output.
- Produces: `EpisodePipeline.execute(job, cancel_check)` for `process_episode`.
- Produces: `AIProcessor.optimize()`, `summarize()`, and `generate_chapters()`.

- [ ] **Step 1: Write failing structured-output and partial-success tests**

```python
# tests/v2/test_structured_transcriber.py
async def test_segments_keep_whisper_timestamps(self):
    transcriber = self.make_transcriber([
        FakeSegment(0.0, 2.5, "Hello"), FakeSegment(2.5, 5.0, "world")
    ], language="en")
    result = await transcriber.transcribe_segments("audio.wav")
    self.assertEqual(result.language, "en")
    self.assertEqual([(s.start_sec, s.end_sec, s.text) for s in result.segments], [
        (0.0, 2.5, "Hello"), (2.5, 5.0, "world"),
    ])
```

```python
# tests/v2/test_pipeline.py
async def test_optional_ai_failures_complete_with_warnings(self):
    ai = FakeAI(
        optimize_error=RuntimeError("optimize failed"),
        summary_error=RuntimeError("summary failed"),
        chapter_error=RuntimeError("chapters failed"),
    )
    pipeline = self.make_pipeline(ai)
    await pipeline.execute(self.job, lambda: False)
    episode = self.episodes.get(self.job.episode_id)
    self.assertEqual(episode.status, "completed")
    self.assertEqual({w.stage for w in episode.warnings}, {"optimization", "summary", "chapters"})
    self.assertEqual(len(self.episodes.get_transcript(episode.id)), 2)

async def test_transcription_failure_is_a_core_pipeline_error(self):
    pipeline = self.make_pipeline(FakeAI(), transcriber=FailingTranscriber())
    with self.assertRaises(TranscriptionFailed):
        await pipeline.execute(self.job, lambda: False)
```

- [ ] **Step 2: Run focused tests and verify new interfaces fail**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.v2.test_structured_transcriber tests.v2.test_pipeline -v
```

Expected: FAIL because structured transcription and EpisodePipeline do not exist.

- [ ] **Step 3: Refactor Transcriber without changing 1.x output and implement pipeline stages**

Materialize Faster-Whisper segments once inside `asyncio.to_thread`. Return frozen segment dataclasses from `transcribe_segments()`. Make existing `transcribe()` call the shared raw transcription helper and format the exact same Markdown currently produced; add a characterization assertion in `tests/test_transcriber.py` for headings, timestamps, and detected language.

`AIProcessor` creates request-scoped `Summarizer`/OpenAI clients from the decrypted revision. Summary content is normalized to plain text, `read_time_min = max(1, ceil(character_count / 400))`, `key_points` is the validated number of generated key points, confidence is an integer clamped to 0–100, and `generated_by` is `VIDA`. Chapter generation requests strict JSON objects with `startSec` and `title`, validates monotonic non-negative starts within duration, derives each duration from the next start or Episode end, and returns no rows when validation fails.

The Pipeline updates progress at the approved boundaries, checks cancellation before and after every stage, commits transcript/media as core output, catches each optional AI error separately into typed warnings, atomically replaces summary/generated chapters, commits final file pointers, and calls `jobs.complete()` only after repository commits succeed.

- [ ] **Step 4: Verify structured processing and legacy transcription**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.v2.test_structured_transcriber tests.v2.test_pipeline tests.test_transcriber -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: PASS; optional failures complete with warnings, core transcription errors propagate to Scheduler for the failed transition, and 1.x Markdown output remains unchanged.

- [ ] **Step 5: Commit the processing pipeline**

```powershell
git add backend/transcriber.py backend/v2/jobs/ai.py backend/v2/jobs/pipeline.py backend/v2/jobs/models.py backend/v2/container.py tests/v2/test_structured_transcriber.py tests/v2/test_pipeline.py tests/test_transcriber.py
git commit -m "feat(v2): process episodes into structured content"
```

---

### Task 11: Expose Episode Reads, Cancel, Retry, and Regeneration Jobs

**Files:**
- Modify: `backend/v2/api/episodes.py`
- Create: `backend/v2/api/jobs.py`
- Modify: `backend/v2/router.py`
- Modify: `backend/v2/schemas.py`
- Modify: `backend/v2/services/episodes.py`
- Create: `tests/v2/test_episode_api.py`
- Create: `tests/v2/test_job_control_api.py`

**Interfaces:**
- Consumes: Episode/Job/Chapter repositories and Scheduler notification.
- Produces: all Episode detail/content routes, Job detail, cancel, retry, summary regeneration, and chapter regeneration.

- [ ] **Step 1: Write failing read/control API tests**

```python
# tests/v2/test_job_control_api.py
def test_cancel_queued_is_immediate_and_idempotent(self):
    episode = self.create_queued_episode()
    first = self.client.post(f"/api/v2/episodes/{episode.id}/cancel")
    second = self.client.post(f"/api/v2/episodes/{episode.id}/cancel")
    self.assertEqual(first.status_code, 200)
    self.assertEqual(second.status_code, 200)
    self.assertEqual(second.json()["status"], "canceled")

def test_retry_creates_attempt_at_queue_tail(self):
    episode = self.create_failed_episode(attempt=1)
    response = self.client.post(f"/api/v2/episodes/{episode.id}/retry")
    self.assertEqual(response.status_code, 202)
    self.assertEqual(response.json()["status"], "queued")
    self.assertEqual(self.jobs.get_for_episode(episode.id)[-1].attempt, 2)

def test_regenerate_summary_does_not_change_completed_episode_status(self):
    episode = self.create_completed_episode()
    response = self.client.post(f"/api/v2/episodes/{episode.id}/summary/regenerate")
    self.assertEqual(response.status_code, 202)
    self.assertEqual(response.json()["type"], "regenerate_summary")
    self.assertEqual(self.episodes.get(episode.id).status, "completed")

def test_regenerate_chapters_queues_the_exact_job_type(self):
    episode = self.create_completed_episode()
    response = self.client.post(f"/api/v2/episodes/{episode.id}/chapters/regenerate")
    self.assertEqual(response.status_code, 202)
    self.assertEqual(response.json()["type"], "regenerate_chapters")
    self.assertEqual(self.episodes.get(episode.id).status, "completed")
```

```python
# tests/v2/test_episode_api.py
def test_incomplete_summary_returns_contract_error(self):
    episode = self.create_completed_episode(with_summary=False)
    response = self.client.get(f"/api/v2/episodes/{episode.id}/summary")
    self.assertEqual(response.status_code, 404)
    self.assertEqual(response.json()["error"]["code"], "summary_not_found")
```

- [ ] **Step 2: Run control tests and verify missing routes fail**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.v2.test_episode_api tests.v2.test_job_control_api -v
```

Expected: FAIL because read/control/regeneration routes are incomplete.

- [ ] **Step 3: Implement state-guarded endpoints and regeneration execution**

Add exact routes from the approved design. Episode retry accepts only failed/canceled; completed/queued/processing returns `invalid_episode_state` 409. Queued cancellation transitions immediately. Processing cancellation sets `cancel_requested_at`, returns 202, and Worker/Pipeline completes the transition. Job cancel follows the same rules. Job responses compute queue position from JobRepository.

Regeneration creates a Job using the Episode's current active Profile revision selection at submission time, participates in the same FIFO queue, and does not alter Episode status. Add executor dispatch by `JobType`; successful summary replacement and generated-chapter replacement occur transactionally, failures retain old content and mark only the regeneration Job failed.

- [ ] **Step 4: Verify API state machine and complete suite**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.v2.test_episode_api tests.v2.test_job_control_api -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: PASS; illegal transitions return 409, repeated canceled cancellation is idempotent, and regeneration leaves Episode completed.

- [ ] **Step 5: Commit control APIs**

```powershell
git add backend/v2/api/episodes.py backend/v2/api/jobs.py backend/v2/router.py backend/v2/schemas.py backend/v2/services/episodes.py tests/v2/test_episode_api.py tests/v2/test_job_control_api.py
git commit -m "feat(v2): add episode and job controls"
```

---

### Task 12: Add Dashboard, Media Range Serving, Chapters, and Exports

**Files:**
- Create: `backend/v2/services/dashboard.py`
- Create: `backend/v2/services/exports.py`
- Modify: `backend/v2/services/media.py`
- Modify: `backend/v2/api/episodes.py`
- Modify: `backend/v2/schemas.py`
- Create: `tests/v2/test_dashboard_api.py`
- Create: `tests/v2/test_media_api.py`
- Create: `tests/v2/test_exports.py`
- Create: `tests/v2/test_chapter_api.py`

**Interfaces:**
- Consumes: Episode/Chapter repositories and committed media paths.
- Produces: dashboard aggregate, manual chapter creation, TXT/SRT/MD exports, media/poster endpoints with Range support.

- [ ] **Step 1: Write failing aggregate, Range, and export tests**

```python
# tests/v2/test_dashboard_api.py
def test_empty_dashboard_matches_contract(self):
    response = self.client.get("/api/v2/dashboard")
    self.assertEqual(response.json(), {
        "currentEpisode": None, "summary": None, "transcript": [],
        "chapters": [], "recentProjects": [],
    })

def test_dashboard_uses_latest_completed_episode(self):
    old = self.create_completed("2026-07-17T00:00:00Z")
    current = self.create_completed("2026-07-18T00:00:00Z")
    self.create_failed("2026-07-19T00:00:00Z")
    body = self.client.get("/api/v2/dashboard").json()
    self.assertEqual(body["currentEpisode"]["id"], current.id)
```

```python
# tests/v2/test_media_api.py
def test_media_supports_byte_range(self):
    episode = self.create_episode_with_media(b"0123456789", "video/mp4")
    response = self.client.get(
        f"/api/v2/episodes/{episode.id}/media", headers={"Range": "bytes=2-5"}
    )
    self.assertEqual(response.status_code, 206)
    self.assertEqual(response.content, b"2345")
    self.assertEqual(response.headers["Content-Range"], "bytes 2-5/10")
```

```python
# tests/v2/test_exports.py
def test_srt_uses_standard_timestamps_and_attachment_header(self):
    episode = self.create_episode_with_segments()
    response = self.client.get(f"/api/v2/episodes/{episode.id}/export?format=srt")
    self.assertEqual(response.status_code, 200)
    self.assertIn("attachment", response.headers["Content-Disposition"])
    self.assertIn("00:00:00,000 --> 00:00:02,500", response.text)
```

- [ ] **Step 2: Run focused tests and verify endpoints fail**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.v2.test_dashboard_api tests.v2.test_media_api tests.v2.test_exports tests.v2.test_chapter_api -v
```

Expected: FAIL because aggregate/media/export/manual-chapter behavior is absent.

- [ ] **Step 3: Implement deterministic aggregation and response generation**

Dashboard selects the most recently created completed Episode, loads its summary/transcript/chapters, and independently loads 12 most recent projects. Empty data returns the exact null/empty object above. Manual chapter creation validates `0 <= startSec <= episode.durationSec`, derives duration from the next chapter or Episode end, sets `bookmarked=false`, `source=manual`, and uses the Episode poster URL.

Implement a bounded path resolver that accepts only repository-owned relative paths under `data/v2`. Use Starlette `FileResponse` for full files and explicit single-range parsing for `bytes=start-end`, returning 206, `Accept-Ranges: bytes`, `Content-Range`, correct media type, and 416 for invalid/unsatisfiable ranges. Poster uses regular FileResponse.

TXT includes `[HH:MM:SS] Speaker: text`; SRT uses sequential indexes and comma millisecond timestamps; Markdown contains title, summary, ordered chapters, and full transcript. Reject unknown formats with `invalid_format` 400. Sanitize the download filename and set the exact content types from the API contract.

- [ ] **Step 4: Verify aggregate, content delivery, and full regression**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.v2.test_dashboard_api tests.v2.test_media_api tests.v2.test_exports tests.v2.test_chapter_api -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: PASS; Range returns 206, invalid Range returns 416, exports have attachment headers, and empty Dashboard matches the contract exactly.

- [ ] **Step 5: Commit user-facing read APIs**

```powershell
git add backend/v2/services/dashboard.py backend/v2/services/exports.py backend/v2/services/media.py backend/v2/api/episodes.py backend/v2/schemas.py tests/v2/test_dashboard_api.py tests/v2/test_media_api.py tests/v2/test_exports.py tests/v2/test_chapter_api.py
git commit -m "feat(v2): add dashboard media chapters and exports"
```

---

### Task 13: Update the Contract, Add End-to-End Queue Verification, and Harden Delivery

**Files:**
- Modify: `docs/api/v2-api-contract.md`
- Modify: `README.md`
- Modify: `README_ZH.md`
- Create: `tests/v2/test_end_to_end_queue.py`
- Create: `tests/v2/test_legacy_api_contract.py`
- Create: `tests/v2/test_secret_redaction.py`

**Interfaces:**
- Consumes: every public interface from Tasks 1–12.
- Produces: an executable end-to-end acceptance suite and updated operator/API documentation.

- [ ] **Step 1: Write final failing acceptance tests before editing docs**

```python
# tests/v2/test_end_to_end_queue.py
async def test_five_projects_run_two_at_a_time_and_survive_restart(self):
    first_runtime = self.make_runtime(max_workers=2, executor=BlockingExecutor())
    await first_runtime.start()
    episode_ids = [self.submit_url(first_runtime, index) for index in range(5)]
    await first_runtime.executor.wait_until_started(2)
    self.assertEqual(first_runtime.executor.max_active, 2)
    await first_runtime.stop(grace_period_sec=0)

    second_runtime = self.make_runtime(max_workers=2, executor=SuccessfulExecutor())
    await second_runtime.start()
    await second_runtime.scheduler.wait_until_idle(timeout_sec=5)
    statuses = [second_runtime.episodes.get(episode_id).status for episode_id in episode_ids]
    self.assertEqual(statuses, ["completed"] * 5)
    self.assertEqual(len({job.id for job in second_runtime.jobs.list_all()}), 5)
    await second_runtime.stop(grace_period_sec=1)
```

```python
# tests/v2/test_secret_redaction.py
def test_plaintext_provider_key_never_appears_in_database_logs_or_api(self):
    secret = "super-secret-provider-key"
    profile = self.create_profile(secret)
    self.run_failed_provider_test(profile["id"])
    database_bytes = self.database_path.read_bytes()
    self.assertNotIn(secret.encode("utf-8"), database_bytes)
    self.assertNotIn(secret, self.captured_logs.getvalue())
    self.assertNotIn(secret, self.last_response.text)
```

`test_legacy_api_contract.py` must mount the real `backend/main.py` with processor dependencies stubbed and assert representative 1.x routes retain their current status and top-level response keys: `/`, `/api/files`, `/api/tasks/active`, missing `/api/task-status/{id}`, and invalid `/api/download/{filename}`.

- [ ] **Step 2: Run final acceptance tests and record the real failure**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.v2.test_end_to_end_queue tests.v2.test_legacy_api_contract tests.v2.test_secret_redaction -v
```

Expected: any uncovered integration or redaction gap FAILS here; do not edit assertions to hide product defects.

- [ ] **Step 3: Close integration gaps and update operator/API documentation**

Fix only failures exposed by Step 2, preserving all approved interfaces. Update `docs/api/v2-api-contract.md` with the five states, progress fields, upload/URL bodies, Profile CRUD/test, Job endpoints, cancel/retry/regeneration, Range endpoints, warnings, errors, and startup settings. Update both READMEs with single-process deployment, `V2_MAX_CONCURRENT_JOBS=2`, `V2_UPLOAD_MAX_GB=5`, secure generation/storage of `VIDA_PROFILE_MASTER_KEY`, `data/v2/` backup requirements, and the warning that losing the master key makes Provider credentials unrecoverable.

Add a startup log line that reports the data directory, worker count, and upload limit but never the master key, Profile key, encrypted key, URL query values, or database contents.

- [ ] **Step 4: Run fresh complete verification**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
git diff --check
git status --short
```

Expected: all tests PASS with zero failures and `git diff --check` exits 0. Inspect `git status --short`, preserve unrelated user files, and stage only the files listed in Task 13. Also run the frontend contract test explicitly:

```powershell
node --test tests/settings-store.test.js
```

Expected: PASS, proving 1.x settings behavior remains intact.

- [ ] **Step 5: Commit the complete v2 backend delivery**

```powershell
git add docs/api/v2-api-contract.md README.md README_ZH.md tests/v2/test_end_to_end_queue.py tests/v2/test_legacy_api_contract.py tests/v2/test_secret_redaction.py backend/v2 backend/main.py start.py requirements.txt .env.example docker-compose.yml
git commit -m "docs(v2): finalize backend contract and operations"
```

## Final Acceptance Checklist

- [ ] Submitting five jobs with a configured maximum of two never records more than two processing jobs.
- [ ] FIFO order is deterministic and retries enter the queue tail.
- [ ] Restart recovers existing processing Job IDs instead of creating duplicate attempts.
- [ ] queued cancellation is immediate; processing cancellation is cooperative; canceled retry creates the next attempt.
- [ ] Provider updates create immutable revisions and logical deletion blocks only new submissions.
- [ ] Plaintext Provider API keys are absent from SQLite bytes, API responses, Job records, and captured logs.
- [ ] Upload overflow/disconnect leaves no `.part` file and no Episode row.
- [ ] Transcript failure fails the Episode; optional AI failure completes it with typed warnings.
- [ ] Generated chapter regeneration preserves manual chapters and prior generated chapters on failure.
- [ ] Dashboard, media Range, TXT, SRT, and Markdown behavior matches `docs/api/v2-api-contract.md`.
- [ ] Existing 1.x Python and JavaScript tests pass without reading or changing `temp/` history.
- [ ] Production documentation requires one Uvicorn process and explains all v2 startup settings and backup requirements.
