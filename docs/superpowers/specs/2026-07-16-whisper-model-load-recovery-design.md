# Whisper Model Load Recovery Design

## Problem

The Bilibili audio download completes, but the first `faster-whisper` model
download stalls with a zero-byte Hugging Face cache entry. `WhisperModel(...)`
is also constructed synchronously inside an async request task, which blocks the
FastAPI event loop. The browser therefore keeps showing an old progress state
and the status endpoints stop responding.

## Chosen Approach

1. Stop the currently blocked worker and remove only the incomplete
   `Systran/faster-whisper-base` cache artifacts and stale lock.
2. Pre-download the model with Hugging Face Xet disabled. Use the user's local
   proxy at `http://127.0.0.1:7897` only for this recovery download; do not store
   it in application configuration.
3. Move model construction into `asyncio.to_thread` so model download and load
   cannot block the FastAPI event loop.
4. Restart the service on port 8001 and resubmit the same Bilibili URL.

## Alternatives Considered

- Recovery download only: fastest, but a later cold model load can freeze the
  server again.
- Switch to the smaller `tiny` model: reduces download size but lowers
  transcription accuracy and changes user-visible behavior.

## Data Flow And Errors

The task still progresses from audio download to transcription. During the
transcription stage, model initialization runs in a worker thread. Exceptions
return to the existing task error handler, which persists an error state and
broadcasts it to the browser while the API remains responsive.

## Testing And Verification

- Add an async regression test proving a slow model constructor does not block
  another coroutine on the event loop.
- Run the focused test, then the complete test suite.
- Verify the model snapshot is complete and contains the model weights.
- Verify `/api/task-status` remains responsive during model initialization.
- Verify the target Bilibili task advances beyond transcription and reaches a
  terminal success or actionable error state.

## Scope

No UI redesign, model-size change, API-key handling change, or persistent proxy
configuration is included.
