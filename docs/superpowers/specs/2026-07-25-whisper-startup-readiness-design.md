# Whisper startup readiness design

## Problem

VIDA v2 currently enters the public `Transcribing` stage at 20% before
`faster-whisper` has loaded its model. On a machine without a cached model,
`WhisperModel("base")` downloads `Systran/faster-whisper-base` lazily from
Hugging Face.

The observed production process downloaded the submitted Bilibili audio
successfully, then remained at 20% while `hf_xet` waited on a Hugging Face CAS
request. The CAS TLS handshake failed through the macOS system proxy and left a
145,217,532-byte `model.bin` as a zero-byte `.incomplete` file. Job heartbeats
continued, so the UI could not distinguish model initialization from active
audio transcription.

## Goals

- Disable the Hugging Face Xet transport before Hugging Face modules are
  imported, so model files use the regular HTTPS download path.
- Download and load the configured Whisper model during FastAPI startup.
- Do not expose the HTTP service or start the v2 scheduler until the model is
  ready.
- Share one loaded `Transcriber` between VIDA 1.x and v2.
- Bound model initialization and return a safe startup error without leaking
  proxy details, cache paths, or upstream response text.
- Keep local and Docker startup behavior aligned.

## Non-goals

- Do not change Episode, Job, SSE, or public API schemas.
- Do not add a database migration.
- Do not add a model-management UI or runtime model switching.
- Do not delete or rewrite valid Hugging Face cache entries.
- Do not make Whisper model files part of the Git repository or Docker image.
- Do not change the Bilibili/SSRF ingest path completed in the preceding work.

## Architecture

### Transport configuration

The application will set safe defaults before importing `faster_whisper`:

- `HF_HUB_DISABLE_XET=1`;
- a bounded Hugging Face metadata timeout;
- a bounded Hugging Face file-download timeout.

Explicit operator values remain authoritative. The application uses
`setdefault` semantics instead of overwriting deployment configuration.
`start.py` will place the defaults in the Uvicorn child environment, and
`backend/transcriber.py` will establish the same defaults before importing
`WhisperModel`. The module-level guard covers direct Uvicorn and test imports
that bypass `start.py`.

### Shared transcriber

`backend/main.py` will create one `Transcriber` before installing v2. That
instance remains the existing VIDA 1.x transcriber and is injected into
`install_v2`. Production v2 runtime construction passes the same instance to
`EpisodePipeline`; test runtimes may continue to supply a fake executor without
loading a real model.

The `Transcriber` constructor reads `WHISPER_MODEL_SIZE` when no explicit model
size is supplied. It also accepts or derives a positive model-load timeout from
`WHISPER_MODEL_LOAD_TIMEOUT_SEC`, defaulting to 300 seconds.

### Startup readiness

`Transcriber.preload()` is the public async readiness operation. It uses the
existing single-construction lock and blocking-thread boundary, so concurrent
callers still construct at most one model and the event loop remains
responsive.

The v2 runtime stores an optional async readiness callback. Its FastAPI startup
sequence is:

1. construct repositories, services, the shared transcriber, and pipeline;
2. await the model readiness callback;
3. log safe operational settings;
4. reconcile storage and start scheduler workers;
5. let FastAPI/Uvicorn report application startup complete.

If readiness fails or times out, scheduler workers do not start and Uvicorn
startup fails. The blocking model operation is also constrained by the regular
Hugging Face per-request timeouts. The public exception and logs identify only
that Whisper model initialization failed; raw network errors, proxy values,
cache paths, and URLs are not propagated.

## Configuration

The documented defaults are:

```dotenv
WHISPER_MODEL_SIZE=base
WHISPER_MODEL_LOAD_TIMEOUT_SEC=300
HF_HUB_DISABLE_XET=1
HF_HUB_ETAG_TIMEOUT=10
HF_HUB_DOWNLOAD_TIMEOUT=60
```

`HF_HUB_DOWNLOAD_TIMEOUT` is longer than the metadata timeout because a regular
HTTP model download may transfer roughly 145 MB for the default base model.
Operators can raise these values for slow links. Invalid or non-positive model
load timeout values fail startup with a configuration error rather than
silently falling back.

`.env.example`, Docker configuration, the root `AGENTS.md`, and
`backend/v2/AGENTS.md` will describe the same startup contract. Secrets remain
outside these values.

## Recovery of the current task

The current processing Job must be canceled before restarting because its
blocking `hf_xet` call cannot be safely redirected in place. The existing
zero-byte `.incomplete` cache file will not be manually deleted; the regular
Hugging Face downloader owns cache recovery.

After implementation:

1. preserve the running process's `VIDA_PROFILE_MASTER_KEY` without printing it;
2. cancel the stuck Episode Job and wait for its persisted state;
3. restart the backend with the documented environment;
4. wait for the startup model download/load to complete;
5. verify the backend and Vite proxy health endpoints;
6. retry the Episode and observe it beyond 20% or to a new terminal error.

The frontend process does not need rebuilding or restarting because no frontend
contract changes.

## Testing

TDD coverage will include:

- Hugging Face defaults exist before the fake `faster_whisper` module import,
  while explicit environment values are preserved.
- `Transcriber()` selects `WHISPER_MODEL_SIZE`; an explicit constructor value
  still wins.
- `preload()` remains non-blocking and concurrent calls construct one model.
- model initialization timeout and constructor failures yield a sanitized,
  stable error.
- production bootstrap injects the same transcriber into the pipeline and
  exposes its preload callback to runtime startup.
- runtime startup awaits readiness before scheduler startup and does not start
  the scheduler when readiness fails.
- legacy transcription and the complete backend suite remain green.

Final runtime verification records the model cache size, successful startup
readiness, service health, and retried Job progress without logging user data or
credentials.

## Rollback

The implementation will not alter the database. Rollback consists of reverting
the implementation commits, restoring the prior startup documentation, and
restarting with the same `VIDA_PROFILE_MASTER_KEY`.

After rollback, Whisper returns to lazy per-process loading and may again remain
at 20% during model download. Existing valid model cache entries can remain in
place. Do not delete `data/v2/` or rotate the Provider Profile master key as part
of rollback.
