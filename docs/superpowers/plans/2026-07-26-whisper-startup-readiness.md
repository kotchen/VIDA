# Whisper Startup Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make VIDA download and load one shared Whisper model before FastAPI becomes ready, using regular Hugging Face HTTPS instead of Xet.

**Architecture:** A small environment module establishes Hugging Face transport defaults before `faster_whisper` is imported. One configurable `Transcriber` instance is shared by VIDA 1.x and v2, while the v2 FastAPI startup sequence awaits its sanitized, time-bounded readiness callback before starting scheduler workers.

**Tech Stack:** Python 3.12, FastAPI startup handlers, faster-whisper 1.2.1, huggingface-hub 1.24.0, stdlib `asyncio` and `unittest`.

## Global Constraints

- Set `HF_HUB_DISABLE_XET=1` before importing Hugging Face or faster-whisper modules, but preserve explicit operator values with `setdefault`.
- Default `WHISPER_MODEL_SIZE` to `base` and `WHISPER_MODEL_LOAD_TIMEOUT_SEC` to 300 seconds.
- Default `HF_HUB_ETAG_TIMEOUT` to 10 seconds and `HF_HUB_DOWNLOAD_TIMEOUT` to 60 seconds.
- Do not start v2 scheduler workers or report FastAPI startup complete before model readiness succeeds.
- Share one `Transcriber` instance between VIDA 1.x and v2 production pipelines.
- Never expose raw Hugging Face errors, proxy values, URLs, cache paths, credentials, or master keys.
- Do not change public API schemas, database layout, Episode progress values, or the completed SSRF/yt-dlp path.
- Do not delete Hugging Face cache entries in application code or recovery steps.
- Follow root `AGENTS.md`: create `docs/devnote/2026-07-0726/whisper-startup-readiness.md` with development, learning, and rollback sections.
- Use TDD for every production behavior change and run the complete backend suite before completion.

---

### Task 1: Hugging Face transport defaults and bounded Transcriber preload

**Files:**
- Create: `backend/whisper_environment.py`
- Modify: `backend/transcriber.py:1-57`
- Modify: `tests/test_transcriber.py:1-115`

**Interfaces:**
- Produces: `configure_huggingface_environment(environ: MutableMapping[str, str] | None = None) -> None`
- Produces: `Transcriber(model_size: str | None = None, model_load_timeout_sec: float | None = None)`
- Produces: `Transcriber.preload() -> Awaitable[None]`
- Preserves: `Transcriber._load_model() -> Awaitable[None]` constructs at most one model.

- [ ] **Step 1: Write failing environment and configuration tests**

Add tests that import the new module and construct `Transcriber` with controlled
environment dictionaries:

```python
def test_huggingface_defaults_preserve_explicit_operator_values(self):
    from backend.whisper_environment import configure_huggingface_environment

    environ = {"HF_HUB_DISABLE_XET": "0", "HF_HUB_DOWNLOAD_TIMEOUT": "90"}
    configure_huggingface_environment(environ)

    self.assertEqual(environ["HF_HUB_DISABLE_XET"], "0")
    self.assertEqual(environ["HF_HUB_DOWNLOAD_TIMEOUT"], "90")
    self.assertEqual(environ["HF_HUB_ETAG_TIMEOUT"], "10")

def test_transcriber_reads_model_and_timeout_from_environment(self):
    with patch.dict(
        os.environ,
        {
            "WHISPER_MODEL_SIZE": "small",
            "WHISPER_MODEL_LOAD_TIMEOUT_SEC": "42",
        },
        clear=False,
    ):
        transcriber = module.Transcriber()

    self.assertEqual(transcriber.model_size, "small")
    self.assertEqual(transcriber.model_load_timeout_sec, 42.0)
```

Add an import-order assertion by placing a fake `faster_whisper` module in
`sys.modules`; its `WhisperModel` accessor must observe
`HF_HUB_DISABLE_XET == "1"` when `backend.transcriber` imports it.

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
source venv/bin/activate
python -m unittest \
  tests.test_transcriber.TranscriberAsyncTests.test_huggingface_defaults_preserve_explicit_operator_values \
  tests.test_transcriber.TranscriberAsyncTests.test_transcriber_reads_model_and_timeout_from_environment -v
```

Expected: FAIL because `backend.whisper_environment` and the configurable
constructor do not exist.

- [ ] **Step 3: Implement transport defaults and constructor validation**

Create `backend/whisper_environment.py`:

```python
from __future__ import annotations

import os
from collections.abc import MutableMapping

_DEFAULTS = {
    "HF_HUB_DISABLE_XET": "1",
    "HF_HUB_ETAG_TIMEOUT": "10",
    "HF_HUB_DOWNLOAD_TIMEOUT": "60",
}

def configure_huggingface_environment(
    environ: MutableMapping[str, str] | None = None,
) -> None:
    target = os.environ if environ is None else environ
    for name, value in _DEFAULTS.items():
        target.setdefault(name, value)
```

In `backend/transcriber.py`, call the helper before importing
`faster_whisper`. Make `None` select the environment model and timeout:

```python
configure_huggingface_environment()
from faster_whisper import WhisperModel

def __init__(self, model_size=None, model_load_timeout_sec=None):
    self.model_size = model_size or os.getenv("WHISPER_MODEL_SIZE", "base")
    raw_timeout = (
        model_load_timeout_sec
        if model_load_timeout_sec is not None
        else os.getenv("WHISPER_MODEL_LOAD_TIMEOUT_SEC", "300")
    )
    self.model_load_timeout_sec = _positive_timeout(raw_timeout)
```

`_positive_timeout` accepts finite positive numbers only and raises
`ValueError("WHISPER_MODEL_LOAD_TIMEOUT_SEC must be a positive number")`.

- [ ] **Step 4: Run configuration tests and verify GREEN**

Run:

```bash
source venv/bin/activate
python -m unittest tests.test_transcriber.TranscriberAsyncTests -v
```

Expected: all existing and new Transcriber tests PASS.

- [ ] **Step 5: Write failing preload timeout and sanitization tests**

Add tests using a blocking fake constructor:

```python
async def test_preload_timeout_is_sanitized_and_event_loop_remains_responsive(self):
    release = threading.Event()

    class SlowModel:
        def __init__(self, *args, **kwargs):
            release.wait(timeout=1)

    module.WhisperModel = SlowModel
    transcriber = module.Transcriber(model_load_timeout_sec=0.02)
    try:
        with self.assertRaisesRegex(
            RuntimeError, "^Whisper model initialization timed out$"
        ):
            await transcriber.preload()
    finally:
        release.set()
```

Add a constructor failure containing a proxy URL and cache path; assert the
public error is exactly `Whisper model initialization failed` and contains
neither sensitive value.

- [ ] **Step 6: Run preload tests and verify RED**

Run:

```bash
source venv/bin/activate
python -m unittest tests.test_transcriber.TranscriberAsyncTests -v
```

Expected: FAIL because `preload()` does not exist and `_load_model()` still
includes raw constructor text in its exception.

- [ ] **Step 7: Implement sanitized preload**

Keep `_load_model()` as the single-construction primitive. Add:

```python
async def preload(self) -> None:
    try:
        await asyncio.wait_for(
            asyncio.shield(self._load_model()),
            timeout=self.model_load_timeout_sec,
        )
    except TimeoutError:
        logger.error("Whisper model initialization timed out")
        raise RuntimeError("Whisper model initialization timed out") from None
    except Exception:
        logger.error("Whisper model initialization failed")
        raise RuntimeError("Whisper model initialization failed") from None
```

Remove raw exception interpolation from `_load_model()`. It may log only the
stable stage message and exception type through the existing safe logging
pattern.

- [ ] **Step 8: Run Transcriber tests and verify GREEN**

Run:

```bash
source venv/bin/activate
python -m unittest tests.test_transcriber tests.v2.test_structured_transcriber -v
```

Expected: all selected tests PASS, including concurrent single construction.

- [ ] **Step 9: Commit Task 1**

```bash
git add backend/whisper_environment.py backend/transcriber.py tests/test_transcriber.py
git commit -m "fix: bound Whisper model initialization"
```

### Task 2: Shared model and FastAPI readiness ordering

**Files:**
- Modify: `backend/main.py:45-66`
- Modify: `backend/v2/bootstrap.py:40-135`
- Modify: `backend/v2/container.py:20-125`
- Modify: `tests/v2/test_bootstrap_config.py`
- Modify: `tests/v2/test_job_recovery.py:300-335`

**Interfaces:**
- Consumes: `Transcriber.preload() -> Awaitable[None]`
- Produces: `install_v2(app: FastAPI, project_root: Path, transcriber: Transcriber | None = None) -> V2Runtime`
- Produces: `V2Runtime._readiness: Callable[[], Awaitable[None]] | None`
- Preserves: `build_test_runtime(...)` does not download or load a real model.

- [ ] **Step 1: Write failing runtime readiness-order tests**

Create a runtime with a fake readiness callback and scheduler. Assert the
callback completes before scheduler startup:

```python
async def test_runtime_awaits_readiness_before_starting_scheduler(self):
    order = []

    async def readiness():
        order.append("ready")

    scheduler = AsyncMock()
    scheduler.start.side_effect = lambda *args: order.append("scheduler")
    runtime = V2Runtime(
        provider_profile_service=object(),
        scheduler=scheduler,
        _readiness=readiness,
    )

    await runtime.start()

    self.assertEqual(order, ["ready", "scheduler"])
```

Add a readiness callback that raises
`RuntimeError("Whisper model initialization failed")`; assert
`scheduler.start` is not awaited.

- [ ] **Step 2: Run runtime tests and verify RED**

Run:

```bash
source venv/bin/activate
python -m unittest \
  tests.v2.test_job_recovery.JobRecoveryTests.test_runtime_awaits_readiness_before_starting_scheduler \
  tests.v2.test_job_recovery.JobRecoveryTests.test_runtime_does_not_start_scheduler_when_readiness_fails -v
```

Expected: FAIL because `V2Runtime` has no `_readiness` field and `start()` does
not await readiness.

- [ ] **Step 3: Implement the readiness callback boundary**

Add the callback field to `V2Runtime`, copy it from the lazy initialized
runtime in `initialize()`, and update startup ordering:

```python
async def start(self) -> None:
    self.initialize()
    if self._readiness is not None:
        await self._readiness()
    self.log_startup_settings()
    if self.scheduler is None:
        raise RuntimeError("v2 runtime has no scheduler")
    await self.scheduler.start(...)
```

Do not catch or transform the already sanitized readiness error in the
container.

- [ ] **Step 4: Run runtime tests and verify GREEN**

Run:

```bash
source venv/bin/activate
python -m unittest tests.v2.test_job_recovery -v
```

Expected: all recovery and lifespan tests PASS.

- [ ] **Step 5: Write failing shared-instance bootstrap test**

Patch `EpisodePipeline` with a recording fake, pass a fake transcriber to
`install_v2`/environment runtime construction, initialize the runtime, and
assert:

```python
self.assertIs(captured["transcriber"], shared_transcriber)
self.assertEqual(runtime._readiness, shared_transcriber.preload)
```

Also assert `build_test_runtime(..., executor=fake_executor)` has no readiness
callback and never calls the real model.

- [ ] **Step 6: Run bootstrap test and verify RED**

Run:

```bash
source venv/bin/activate
python -m unittest tests.v2.test_bootstrap_config -v
```

Expected: FAIL because `install_v2` cannot accept a transcriber and bootstrap
constructs its own `Transcriber`.

- [ ] **Step 7: Inject one shared Transcriber**

Extend the production bootstrap call chain with an optional transcriber. In
`_build_runtime`, select:

```python
speech_transcriber = transcriber or Transcriber()
job_executor = EpisodePipeline(
    ...,
    transcriber=speech_transcriber,
    ...,
)
readiness = speech_transcriber.preload if preload_model else None
```

Production environment runtime sets `preload_model=True`; test runtime keeps
it false. Return `V2Runtime(..., _readiness=readiness)`.

In `backend/main.py`, construct before v2 installation:

```python
transcriber = Transcriber()
v2_runtime = install_v2(app, PROJECT_ROOT, transcriber=transcriber)
```

Remove the later duplicate `transcriber = Transcriber()` assignment.

- [ ] **Step 8: Run bootstrap, recovery, and legacy tests**

Run:

```bash
source venv/bin/activate
python -m unittest \
  tests.v2.test_bootstrap_config \
  tests.v2.test_job_recovery \
  tests.test_transcriber \
  tests.v2.test_structured_transcriber -v
```

Expected: all selected tests PASS without downloading a model.

- [ ] **Step 9: Commit Task 2**

```bash
git add backend/main.py backend/v2/bootstrap.py backend/v2/container.py \
  tests/v2/test_bootstrap_config.py tests/v2/test_job_recovery.py
git commit -m "fix(v2): preload shared Whisper model at startup"
```

### Task 3: Startup configuration and operator documentation

**Files:**
- Modify: `start.py:120-190`
- Modify: `.env.example`
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`
- Modify: `AGENTS.md`
- Modify: `backend/v2/AGENTS.md`
- Modify: `tests/test_start.py`

**Interfaces:**
- Consumes: `configure_huggingface_environment(environ) -> None`
- Produces: Uvicorn child environment containing Hugging Face regular-HTTP defaults.

- [ ] **Step 1: Write failing launcher environment test**

Patch `subprocess.run`, execute `start.main()` with dependency checks patched
successful, and inspect the Uvicorn call:

```python
child_environ = mocked_run.call_args.kwargs["env"]
self.assertEqual(child_environ["HF_HUB_DISABLE_XET"], "1")
self.assertEqual(child_environ["HF_HUB_ETAG_TIMEOUT"], "10")
self.assertEqual(child_environ["HF_HUB_DOWNLOAD_TIMEOUT"], "60")
```

Add a case with explicit `HF_HUB_DOWNLOAD_TIMEOUT=120` and assert it is
preserved.

- [ ] **Step 2: Run launcher tests and verify RED**

Run:

```bash
source venv/bin/activate
python -m unittest tests.test_start -v
```

Expected: FAIL because `start.py` does not configure the child Hugging Face
environment.

- [ ] **Step 3: Configure the launcher before dependency imports**

Import `configure_huggingface_environment` without importing
`faster_whisper`, call it near the beginning of `main()`, and let
`child_environ = os.environ.copy()` inherit the values. Do not log environment
values.

- [ ] **Step 4: Run launcher tests and verify GREEN**

Run:

```bash
source venv/bin/activate
python -m unittest tests.test_start -v
```

Expected: all launcher tests PASS.

- [ ] **Step 5: Update environment and startup documentation**

Add the five documented Whisper/Hugging Face values to `.env.example`,
`Dockerfile`, and `docker-compose.yml`. Update both AGENTS files to state:

- startup waits for the configured Whisper model;
- first startup downloads approximately 145 MB for `base`;
- startup failure means the service is not ready and scheduler workers did not
  start;
- Xet remains disabled unless an operator explicitly overrides it;
- the cache should be persisted in production when practical.

Do not add any secret or machine-specific proxy value.

- [ ] **Step 6: Run documentation and configuration checks**

Run:

```bash
git diff --check
rg -n "HF_HUB_DISABLE_XET|WHISPER_MODEL_LOAD_TIMEOUT_SEC|145 MB" \
  .env.example Dockerfile docker-compose.yml AGENTS.md backend/v2/AGENTS.md
```

Expected: no whitespace errors and every required deployment surface contains
the startup contract.

- [ ] **Step 7: Commit Task 3**

```bash
git add start.py .env.example Dockerfile docker-compose.yml AGENTS.md \
  backend/v2/AGENTS.md tests/test_start.py
git commit -m "docs: configure Whisper startup readiness"
```

### Task 4: Devnote, complete verification, and live recovery

**Files:**
- Create: `docs/devnote/2026-07-0726/whisper-startup-readiness.md`

**Interfaces:**
- Consumes: completed startup readiness behavior.
- Produces: repository-required development record and verified running service.

- [ ] **Step 1: Write the required devnote**

Create the three required top-level sections:

1. `开发内容`: root cause evidence, regular HTTP transport, shared startup
   preload, configuration, tests, and live result.
2. `学习与可沉淀经验`: coarse stage progress is not transfer progress,
   heartbeat is not proof of forward progress, model assets belong in service
   readiness, and native transport extensions need explicit proxy testing.
3. `回滚操作`: stop services, preserve `VIDA_PROFILE_MASTER_KEY`, revert the
   implementation commit range, note no migration, keep model cache, restart,
   and verify health.

Do not include the user's URL, proxy value, signed URLs, credentials, database
rows, or master key.

- [ ] **Step 2: Run focused regression suites**

Run:

```bash
source venv/bin/activate
python -m unittest \
  tests.test_start \
  tests.test_transcriber \
  tests.v2.test_structured_transcriber \
  tests.v2.test_bootstrap_config \
  tests.v2.test_job_recovery \
  tests.v2.test_pipeline \
  tests.v2.test_secret_redaction -v
```

Expected: all selected tests PASS.

- [ ] **Step 3: Run the complete backend suite**

Run:

```bash
source venv/bin/activate
python -c "import tempfile, unittest; tempfile.tempdir='/private/tmp'; suite=unittest.defaultTestLoader.discover('tests'); result=unittest.TextTestRunner(verbosity=1).run(suite); raise SystemExit(not result.wasSuccessful())"
```

Expected: every test passes with only intentional skips.

- [ ] **Step 4: Commit the devnote**

```bash
git diff --check
git add docs/devnote/2026-07-0726/whisper-startup-readiness.md
git commit -m "docs: record Whisper startup readiness"
```

- [ ] **Step 5: Cancel the currently stuck Job**

Use the existing Episode cancel endpoint, wait until the Job is persisted as
`canceled`, and confirm no second queued/processing Job exists for that Episode.
Do not delete the Episode or its attempt directory manually.

- [ ] **Step 6: Restart safely and wait for readiness**

Read the running process's existing `VIDA_PROFILE_MASTER_KEY` into the new
process environment without printing it. Stop the old backend, activate
`venv`, confirm `python` and `yt-dlp` resolve inside it, and start:

```bash
python start.py --prod
```

Observe until `Whisper model initialization` completes and Uvicorn reports
application startup complete. Verify the cached `model.bin` is 145,217,532
bytes and no `.incomplete` file remains for it.

- [ ] **Step 7: Retry and observe the Episode**

Retry the canceled Episode. Poll through the API until progress exceeds 20 or
the Job reaches a terminal state. If it fails later, report the new sanitized
stage/error separately; do not conflate a Provider or AI-stage error with
Whisper readiness.

- [ ] **Step 8: Run final health and repository checks**

Run:

```bash
curl --fail http://127.0.0.1:8000/api/v2/dashboard
curl --fail http://127.0.0.1:7100/api/v2/dashboard
git status --short
git diff --check
```

Expected: both endpoints return JSON, the repository is clean, and the backend
and frontend processes remain running.

- [ ] **Step 9: Apply completion gates**

Use `superpowers:verification-before-completion`, then
`superpowers:finishing-a-development-branch`. Report exact test totals, model
readiness result, retried Job state, service URLs, and whether commits are local
or pushed.
