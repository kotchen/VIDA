# Whisper Model Load Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover the stalled Whisper base-model download and prevent model initialization from blocking the FastAPI event loop.

**Architecture:** Keep the existing `Transcriber` interface and task pipeline, but make `_load_model` asynchronous and construct `WhisperModel` through `asyncio.to_thread`. Recover the environment separately by replacing only the incomplete model cache, downloading through the user's temporary local proxy with Xet disabled, and restarting the existing port 8001 service.

**Tech Stack:** Python 3.11, asyncio, unittest, faster-whisper, huggingface-hub, FastAPI/Uvicorn, PowerShell.

## Global Constraints

- Preserve all pre-existing uncommitted changes in the repository.
- Use `http://127.0.0.1:7897` only for the one-time model recovery download.
- Do not persist proxy configuration or change the Whisper model size.
- Do not redesign the UI or change API-key handling.

---

### Task 1: Non-blocking Whisper Model Initialization

**Files:**
- Create: `tests/test_transcriber.py`
- Modify: `backend/transcriber.py:1-63`

**Interfaces:**
- Consumes: `Transcriber.transcribe(audio_path: str, language: Optional[str] = None) -> str`
- Produces: `Transcriber._load_model() -> Awaitable[None]`; `transcribe` awaits it before transcription.

- [ ] **Step 1: Write the failing regression test**

```python
import asyncio
import importlib
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class TranscriberAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_initialization_does_not_block_event_loop(self):
        fake_faster_whisper = types.ModuleType("faster_whisper")
        fake_faster_whisper.WhisperModel = object
        with patch.dict(sys.modules, {"faster_whisper": fake_faster_whisper}):
            sys.modules.pop("backend.transcriber", None)
            module = importlib.import_module("backend.transcriber")

        constructor_started = threading.Event()
        release_constructor = threading.Event()

        class SlowModel:
            def __init__(self, *args, **kwargs):
                constructor_started.set()
                release_constructor.wait(timeout=1)

            def transcribe(self, *args, **kwargs):
                info = types.SimpleNamespace(language="en", language_probability=1.0)
                return [], info

        module.WhisperModel = SlowModel
        transcriber = module.Transcriber()

        with tempfile.TemporaryDirectory() as temp_dir:
            audio = Path(temp_dir) / "audio.m4a"
            audio.write_bytes(b"audio")
            started_at = time.monotonic()
            task = asyncio.create_task(transcriber.transcribe(str(audio)))
            started = await asyncio.wait_for(
                asyncio.to_thread(constructor_started.wait), timeout=1.5
            )
            self.assertTrue(started)
            release_constructor.set()
            await asyncio.wait_for(task, timeout=0.5)

        self.assertLess(time.monotonic() - started_at, 0.5)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `\.venv\Scripts\python.exe -m unittest tests.test_transcriber -v`

Expected: FAIL because the synchronous constructor blocks the event loop until the one-second timeout, so the measured duration exceeds 0.5 seconds.

- [ ] **Step 3: Move model construction off the event loop**

```python
import asyncio

async def _load_model(self):
    if self.model is None:
        logger.info(f"正在加载Whisper模型: {self.model_size}")
        try:
            self.model = await asyncio.to_thread(
                WhisperModel,
                self.model_size,
                device="cpu",
                compute_type="int8",
            )
            logger.info("模型加载完成")
        except Exception as e:
            logger.error(f"模型加载失败: {str(e)}")
            raise Exception(f"模型加载失败: {str(e)}")
```

Replace the call in `transcribe` with:

```python
await self._load_model()
```

- [ ] **Step 4: Run focused and full tests**

Run: `\.venv\Scripts\python.exe -m unittest tests.test_transcriber -v`

Expected: PASS.

Run: `\.venv\Scripts\python.exe -m unittest discover -v`

Expected: all tests PASS.

- [ ] **Step 5: Commit the regression fix**

```powershell
git add -- backend/transcriber.py tests/test_transcriber.py
git commit -m "fix: keep whisper model loading off event loop"
```

### Task 2: Recover Model Cache And Restart Service

**Files:**
- Runtime cache: `C:\Users\gsgame\.cache\huggingface\hub\models--Systran--faster-whisper-base`
- Runtime lock: `C:\Users\gsgame\.cache\huggingface\hub\.locks\models--Systran--faster-whisper-base`
- Verify: `server-8001.err.log`, `temp/tasks.json`

**Interfaces:**
- Consumes: Hugging Face repository `Systran/faster-whisper-base`.
- Produces: a complete local model snapshot used transparently by `WhisperModel("base")`.

- [ ] **Step 1: Stop the blocked Uvicorn worker**

Use `tasklist`, targeted process command-line inspection, and `taskkill /T /F` to stop only the Python process tree running `start.py --port 8001`.

- [ ] **Step 2: Validate and remove only incomplete model artifacts**

Resolve both cache paths and verify they remain under `C:\Users\gsgame\.cache\huggingface\hub` before removing the incomplete model directory and its matching lock directory.

- [ ] **Step 3: Pre-download through the temporary proxy**

```powershell
$env:HTTP_PROXY = 'http://127.0.0.1:7897'
$env:HTTPS_PROXY = 'http://127.0.0.1:7897'
$env:HF_HUB_DISABLE_XET = '1'
& '.\.venv\Scripts\python.exe' -c "from huggingface_hub import snapshot_download; print(snapshot_download('Systran/faster-whisper-base'))"
```

Expected: command prints a snapshot path containing `model.bin`, `config.json`, `tokenizer.json`, and `vocabulary.txt`, with no `.incomplete` files.

- [ ] **Step 4: Restart port 8001 with the project virtual environment**

Start `E:\AI-Video-Transcriber\.venv\Scripts\python.exe start.py --port 8001` in a hidden background process with stdout and stderr redirected to the existing server log files. Do not pass the temporary proxy variables to the server process.

- [ ] **Step 5: Verify service and target workflow**

Verify `GET http://127.0.0.1:8001/` returns HTTP 200 and the model can be instantiated from cache. Resubmit the target Bilibili URL through the existing UI/API credentials and confirm progress moves beyond Whisper initialization to a terminal success or actionable error state. Do not log or persist the API key.

