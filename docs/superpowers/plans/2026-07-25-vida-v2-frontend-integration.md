# VIDA 2.0 Frontend Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect the VIDA 2.0 React application to the complete `/api/v2` backend, add SSE-driven invalidation, fill the agreed API gaps, and serve the production SPA at `/v2` without changing VIDA 1.x behavior.

**Architecture:** Keep SQLite and REST as the source of truth. A process-local, thread-safe `V2EventBroker` sends lightweight invalidation events over one browser `EventSource`; feature hooks refetch typed REST resources. The React application is split by feature and uses native React state, while FastAPI serves the compiled SPA under `/v2`.

**Tech Stack:** Python 3.12, FastAPI, SQLite, React 19, TypeScript 6, Vite 8, Vitest, Testing Library, Tailwind CSS 4.

## Global Constraints

- VIDA 1.x `/`, `/static/*`, and `/api/*` behavior must remain unchanged.
- The React application base and router basename are `/v2`; APIs remain under `/api/v2`.
- Production uses one FastAPI/Uvicorn process; do not add multi-process event distribution.
- REST remains authoritative; SSE payloads only invalidate cached/page state.
- Do not expose API keys, encrypted credentials, source URLs, filesystem paths, or raw upstream errors in SSE, UI errors, or logs.
- Do not add a global state library, TanStack Query, WebSocket, authentication, bulk operations, or server-side full-text search.
- queued/processing Episodes must be canceled before deletion.
- All chapters may update `bookmarked`; only manual chapters may update `startSec`/`title` or be deleted.
- Every implementation task follows test-first development and ends with an independently reviewable commit.

---

## File Map

### Backend files

- `backend/v2/events.py`: thread-safe event model, broker, subscriptions, heartbeat stream formatting.
- `backend/v2/api/events.py`: `/events` SSE route.
- `backend/v2/container.py`: owns the broker and lifecycle wiring.
- `backend/v2/router.py`: includes the events router.
- `backend/v2/repositories/jobs.py`: emits committed Job/Episode state changes.
- `backend/v2/repositories/episodes.py`: deletes an Episode transactionally.
- `backend/v2/repositories/chapters.py`: updates bookmarks independently of chapter content.
- `backend/v2/services/episodes.py`: enforces delete/bookmark rules and cleans Episode files.
- `backend/v2/services/provider_profiles.py`: emits profile invalidation after committed writes.
- `backend/v2/api/episodes.py`: exposes delete and bookmarked PATCH.
- `backend/v2/services/frontend.py`: safely serves `/v2` build files and SPA fallback.
- `docs/api/v2-api-contract.md`: documents the three agreed API additions.

### Frontend files

- `frontend/src/api/types.ts`: exact v2 public models.
- `frontend/src/api/client.ts`: JSON/204/blob client and normalized `ApiError`.
- `frontend/src/api/episodes.ts`: Dashboard, Episode, Job, Chapter, export, and submission methods.
- `frontend/src/api/profiles.ts`: Provider Profile CRUD/test methods.
- `frontend/src/api/events.ts`: EventSource transport and event parsing.
- `frontend/src/features/events/EventProvider.tsx`: one connection and resource subscriptions.
- `frontend/src/features/profiles/*`: profile forms, preferences, and hooks.
- `frontend/src/features/submission/*`: URL/file form and upload progress.
- `frontend/src/features/episode/*`: Episode state/content/actions.
- `frontend/src/features/library/*`: list pagination/filter state.
- `frontend/src/pages/*.tsx`: six real v2 pages.
- Existing dashboard components become nullable, state-aware, and callback-driven.

---

### Task 1: Add the process-local v2 event broker and SSE endpoint

**Files:**
- Create: `backend/v2/events.py`
- Create: `backend/v2/api/events.py`
- Modify: `backend/v2/container.py`
- Modify: `backend/v2/router.py`
- Test: `tests/v2/test_events.py`

**Interfaces:**
- Produces: `V2Event(type, data, id)`, `V2EventBroker.publish(type, data)`, `V2EventBroker.subscribe()`, and `create_event_router(runtime)`.
- Consumes: only the running asyncio loop of each subscriber; publishers may run on worker threads.

- [ ] **Step 1: Write broker tests**

```python
class EventBrokerTests(unittest.IsolatedAsyncioTestCase):
    async def test_publish_from_thread_reaches_subscriber(self):
        broker = V2EventBroker(queue_size=2)
        async with broker.subscribe() as queue:
            await asyncio.to_thread(
                broker.publish,
                "episode.updated",
                {"episodeId": "e1", "status": "processing", "progress": 20},
            )
            event = await asyncio.wait_for(queue.get(), 1)
        self.assertEqual(event.type, "episode.updated")
        self.assertEqual(event.data["episodeId"], "e1")

    async def test_slow_subscriber_keeps_newest_event(self):
        broker = V2EventBroker(queue_size=1)
        async with broker.subscribe() as queue:
            broker.publish("episode.updated", {"episodeId": "old"})
            broker.publish("episode.updated", {"episodeId": "new"})
            event = await asyncio.wait_for(queue.get(), 1)
        self.assertEqual(event.data["episodeId"], "new")
```

- [ ] **Step 2: Run the tests and verify failure**

Run: `python -m unittest tests.v2.test_events -v`

Expected: FAIL because `backend.v2.events` does not exist.

- [ ] **Step 3: Implement the broker**

Use a `threading.Lock` around subscriber registration and the event counter. Store `(loop, asyncio.Queue)` per subscription. `publish()` allocates the next string ID and uses `loop.call_soon_threadsafe()` to drop the oldest queued event when full and enqueue the newest event.

```python
@dataclass(frozen=True)
class V2Event:
    id: str
    type: str
    data: dict[str, object]


class V2EventBroker:
    def __init__(self, queue_size: int = 64):
        self._queue_size = queue_size
        self._lock = threading.Lock()
        self._next_id = 1
        self._subscribers: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = {}

    def publish(self, event_type: str, data: dict[str, object] | None = None) -> V2Event:
        with self._lock:
            event = V2Event(str(self._next_id), event_type, dict(data or {}))
            self._next_id += 1
            subscribers = tuple(self._subscribers.values())
        for loop, queue in subscribers:
            loop.call_soon_threadsafe(_offer_latest, queue, event)
        return event
```

`subscribe()` must be an `@asynccontextmanager` that always unregisters in `finally`.

- [ ] **Step 4: Add SSE route tests**

Test `_encode_sse(V2Event("7", "episode.updated", {"episodeId": "e1"}))` exactly:

```python
self.assertEqual(
    _encode_sse(event),
    'id: 7\nevent: episode.updated\ndata: {"episodeId":"e1"}\n\n',
)
```

Also assert `/api/v2/events` returns `text/event-stream`, `Cache-Control: no-cache`, and `X-Accel-Buffering: no`.

- [ ] **Step 5: Implement the SSE router**

The async generator waits at most 15 seconds for a broker event. A timeout yields:

```text
event: heartbeat
data: {}

```

On connection, first yield `retry: 3000\n\n`, then broker events encoded with compact JSON. Do not log event payloads.

- [ ] **Step 6: Wire broker ownership**

Add `events: V2EventBroker = field(default_factory=V2EventBroker)` to `V2Runtime`, preserve it during `initialize()`, and include `create_event_router(runtime)` before resource routers.

- [ ] **Step 7: Run tests and commit**

Run:

```bash
python -m unittest tests.v2.test_events tests.v2.test_error_contract -v
git add backend/v2/events.py backend/v2/api/events.py backend/v2/container.py backend/v2/router.py tests/v2/test_events.py
git commit -m "feat(v2): add SSE event broker"
```

Expected: all selected tests PASS.

---

### Task 2: Publish committed Job, Episode, and Profile changes

**Files:**
- Modify: `backend/v2/repositories/jobs.py`
- Modify: `backend/v2/services/episodes.py`
- Modify: `backend/v2/services/provider_profiles.py`
- Modify: `backend/v2/bootstrap.py`
- Test: `tests/v2/test_event_publication.py`

**Interfaces:**
- Consumes: `Callable[[str, dict[str, object]], object]` event publisher.
- Produces: committed `job.updated`, `episode.updated`, `dashboard.invalidated`, and `profiles.invalidated` events.

- [ ] **Step 1: Write publication tests**

Create a recording publisher:

```python
class RecordingPublisher:
    def __init__(self):
        self.events = []

    def __call__(self, event_type, data):
        self.events.append((event_type, data))
```

Test that submit emits `episode.updated`, Job progress emits both `job.updated` and `episode.updated`, terminal completion invalidates Dashboard, and create/update/delete Profile emits `profiles.invalidated`. Assert event dictionaries contain IDs, status, and progress but never `source_url`, `api_key`, `encrypted_credential`, or `error_message`.

- [ ] **Step 2: Run and verify failure**

Run: `python -m unittest tests.v2.test_event_publication -v`

Expected: FAIL because constructors do not accept publishers and no events are recorded.

- [ ] **Step 3: Add optional publisher dependencies**

Use a no-op default to preserve existing isolated repository/service tests:

```python
EventPublisher = Callable[[str, dict[str, object]], object]

def _ignore_event(_event_type: str, _data: dict[str, object]) -> None:
    return None
```

`JobRepository` publishes only after its transaction context exits. Refactor each mutating method to retain the resulting `JobRecord`, then call one shared `_publish_job(job)` helper.

- [ ] **Step 4: Publish safe state changes**

`_publish_job(job)` emits:

```python
{
    "jobId": job.id,
    "episodeId": job.episode_id,
    "status": job.status,
    "progress": job.progress,
}
```

For `process_episode`, also read the public Episode state after commit and emit only `episodeId`, `status`, and `progress`. Emit `dashboard.invalidated` when a process Job reaches a terminal state.

Episode submission/control service methods publish after successful return. Provider service methods publish only `profiles.invalidated` after repository writes.

- [ ] **Step 5: Inject the runtime broker**

In `_build_runtime`, construct repositories/services with `events.publish`. Ensure `build_test_runtime()` returns a runtime whose broker receives the same events.

- [ ] **Step 6: Run regression tests and commit**

Run:

```bash
python -m unittest tests.v2.test_event_publication tests.v2.test_scheduler tests.v2.test_end_to_end_queue tests.v2.test_provider_profile_api -v
git add backend/v2/repositories/jobs.py backend/v2/services/episodes.py backend/v2/services/provider_profiles.py backend/v2/bootstrap.py tests/v2/test_event_publication.py
git commit -m "feat(v2): publish resource change events"
```

Expected: PASS with no secret values in recorded events.

---

### Task 3: Implement terminal Episode deletion

**Files:**
- Modify: `backend/v2/repositories/episodes.py`
- Modify: `backend/v2/services/episodes.py`
- Modify: `backend/v2/api/episodes.py`
- Modify: `backend/v2/services/media.py`
- Test: `tests/v2/test_episode_delete_api.py`

**Interfaces:**
- Produces: `EpisodeRepository.delete_terminal(id) -> EpisodeRecord`.
- Produces: `EpisodeService.delete_episode(id) -> None`.
- Produces: `DELETE /api/v2/episodes/{episodeId}` returning 204.

- [ ] **Step 1: Write API tests for allowed and rejected states**

For `completed`, `failed`, and `canceled`, create an Episode with associated Job, transcript, summary, chapter, and files. Delete it and assert:

```python
self.assertEqual(response.status_code, 204)
self.assertIsNone(self.runtime.episode_repository.get(episode_id))
self.assertEqual(self._count("jobs", episode_id), 0)
self.assertEqual(self._count("transcript_segments", episode_id), 0)
self.assertEqual(self._count("summaries", episode_id), 0)
self.assertEqual(self._count("chapters", episode_id), 0)
self.assertFalse(episode_dir.exists())
```

For queued/processing assert `(409, "invalid_episode_state")`; for a missing ID assert `(404, "episode_not_found")`.

- [ ] **Step 2: Run and verify failure**

Run: `python -m unittest tests.v2.test_episode_delete_api -v`

Expected: FAIL with 405 or missing service method.

- [ ] **Step 3: Implement repository deletion**

Within `BEGIN IMMEDIATE`, select the Episode, require status in
`{"completed", "failed", "canceled"}`, then execute `DELETE FROM episodes WHERE id=?`.
Foreign keys perform the cascade. Return the pre-delete `EpisodeRecord` so the service can resolve its controlled directory.

- [ ] **Step 4: Implement safe file cleanup**

Add `MediaService.remove_episode_tree(episode_id)` that resolves
`data_dir / "episodes" / episode_id`, verifies its parent is exactly the controlled Episodes root, rejects symlinks, and uses the existing guarded tree removal helper.

The service commits database deletion first, then removes the tree. A cleanup exception is logged by exception class only and does not restore the deleted database record; startup orphan reconciliation remains the recovery path.

- [ ] **Step 5: Add route and events**

```python
@router.delete("/{episode_id}", status_code=204)
def delete_episode(episode_id: str):
    runtime.require_episodes().delete_episode(episode_id)
    return Response(status_code=204)
```

After success publish `episode.deleted` with only `episodeId`, followed by `dashboard.invalidated`.

- [ ] **Step 6: Run tests and commit**

Run:

```bash
python -m unittest tests.v2.test_episode_delete_api tests.v2.test_file_recovery tests.v2.test_episode_api -v
git add backend/v2/repositories/episodes.py backend/v2/services/episodes.py backend/v2/api/episodes.py backend/v2/services/media.py tests/v2/test_episode_delete_api.py
git commit -m "feat(v2): delete terminal episodes"
```

---

### Task 4: Add bookmark mutation without weakening generated chapter immutability

**Files:**
- Modify: `backend/v2/schemas.py`
- Modify: `backend/v2/repositories/chapters.py`
- Modify: `backend/v2/services/episodes.py`
- Modify: `backend/v2/api/episodes.py`
- Test: `tests/v2/test_chapter_api.py`

**Interfaces:**
- Changes: `ChapterUpdate` adds `bookmarked: bool | None`.
- Produces: `ChapterRepository.update_bookmark(episode_id, chapter_id, bookmarked)`.

- [ ] **Step 1: Add failing API cases**

```python
def test_bookmark_updates_generated_and_manual_chapters(self):
    generated = self.client.patch(
        f"/api/v2/episodes/{self.episode_id}/chapters/generated",
        json={"bookmarked": True},
    )
    self.assertEqual(generated.status_code, 200)
    self.assertTrue(generated.json()["bookmarked"])

def test_generated_content_remains_immutable_when_bookmark_is_present(self):
    response = self.client.patch(
        f"/api/v2/episodes/{self.episode_id}/chapters/generated",
        json={"bookmarked": True, "title": "Changed"},
    )
    self.assertEqual((response.status_code, response.json()["error"]["code"]),
                     (409, "generated_chapter_immutable"))
```

Also test `{}`, `{"bookmarked": null}`, wrong Episode ownership, and persistence.

- [ ] **Step 2: Run and verify failure**

Run: `python -m unittest tests.v2.test_chapter_api.ChapterApiTests -v`

Expected: bookmark request fails validation.

- [ ] **Step 3: Extend schema and repository**

`ChapterUpdate.require_change()` continues rejecting empty and null fields. Add an atomic repository method that updates only `bookmarked` for either source and returns the complete row.

- [ ] **Step 4: Split content and bookmark rules in the service**

When `startSec` or `title` is present, call the existing manual-only update. When `bookmarked` is present, update the bookmark after the content update. A generated chapter with content fields must fail before changing its bookmark, so mixed invalid requests are atomic from the caller's perspective.

- [ ] **Step 5: Run tests and commit**

Run:

```bash
python -m unittest tests.v2.test_chapter_api tests.v2.test_chapter_repository -v
git add backend/v2/schemas.py backend/v2/repositories/chapters.py backend/v2/services/episodes.py backend/v2/api/episodes.py tests/v2/test_chapter_api.py
git commit -m "feat(v2): update chapter bookmarks"
```

---

### Task 5: Update and lock the public v2 contract

**Files:**
- Modify: `docs/api/v2-api-contract.md`
- Modify: `tests/v2/test_error_contract.py`
- Test: `tests/v2/test_legacy_api_contract.py`

**Interfaces:**
- Documents the exact contract consumed by all later frontend tasks.

- [ ] **Step 1: Add contract assertions**

Assert OpenAPI includes:

```python
paths = client.get("/openapi.json").json()["paths"]
self.assertIn("/api/v2/events", paths)
self.assertIn("delete", paths["/api/v2/episodes/{episode_id}"])
bookmark = paths["/api/v2/episodes/{episode_id}/chapters/{chapter_id}"]["patch"]
self.assertIn("bookmarked", json.dumps(bookmark))
```

- [ ] **Step 2: Run and verify current documentation test failure**

Run: `python -m unittest tests.v2.test_error_contract -v`

- [ ] **Step 3: Update contract sections**

Document headers and event schemas for `/events`, deletion status/error rules, bookmarked PATCH semantics, and the single-process/no-replay constraint. Keep all existing endpoint text unchanged unless superseded by the approved design.

- [ ] **Step 4: Run API and legacy contract tests, then commit**

Run:

```bash
python -m unittest tests.v2.test_error_contract tests.v2.test_legacy_api_contract -v
git add docs/api/v2-api-contract.md tests/v2/test_error_contract.py
git commit -m "docs(v2): extend frontend integration contract"
```

---

### Task 6: Create the typed frontend API foundation

**Files:**
- Create: `frontend/src/api/types.ts`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/api/episodes.ts`
- Create: `frontend/src/api/profiles.ts`
- Modify: `frontend/package.json`
- Modify: `frontend/src/data/mock.ts`
- Test: `frontend/src/test/api-client.test.ts`
- Test: `frontend/src/test/api-types.test.ts`

**Interfaces:**
- Produces: `apiRequest<T>()`, `apiBlob()`, `ApiError`, `episodesApi`, `profilesApi`.
- Consumes: exact camelCase models from Task 5.

- [ ] **Step 1: Install the lockfile baseline and expose a deterministic test script**

Run: `npm ci`

Add:

```json
"test": "vitest run"
```

Run and record the true baseline:

```bash
npm test
npm run lint
npm run build
```

Do not fix unrelated pre-existing failures in this task; record them in the task notes before proceeding.

- [ ] **Step 2: Write client tests**

Mock `globalThis.fetch` and verify:

```ts
await expect(apiRequest("/api/v2/missing")).rejects.toMatchObject({
  httpStatus: 404,
  code: "episode_not_found",
  requestId: "req-1",
})
```

Also test JSON success, 204 returning `undefined`, malformed error fallback, network failure, and `apiBlob()` preserving `Content-Disposition`.

- [ ] **Step 3: Define exact public types**

Include:

```ts
export type EpisodeStatus =
  | "queued" | "processing" | "completed" | "failed" | "canceled"

export interface DashboardData {
  currentEpisode: Episode | null
  summary: Summary | null
  transcript: TranscriptSegment[]
  chapters: Chapter[]
  recentProjects: Project[]
}
```

Episode media/poster/resolution, Chapter thumbnail, and Project thumbnail are nullable. Add complete `Job`, `ProviderProfile`, `ProviderConnectionTest`, `ProcessingWarning`, request payload, and `V2Event` types.

- [ ] **Step 4: Implement `ApiError` and request helpers**

Use relative URLs only. Always read `X-Request-ID`; parse the standard error envelope; never include request bodies or complete URLs in error strings.

- [ ] **Step 5: Implement API modules**

Expose exact method names:

```ts
episodesApi.getDashboard()
episodesApi.list({ limit, offset })
episodesApi.get(id)
episodesApi.getTranscript(id)
episodesApi.getSummary(id)
episodesApi.getChapters(id)
episodesApi.submitUrl(input)
episodesApi.cancel(id)
episodesApi.retry(id)
episodesApi.regenerateSummary(id)
episodesApi.regenerateChapters(id)
episodesApi.createChapter(id, input)
episodesApi.updateChapter(id, chapterId, input)
episodesApi.deleteChapter(id, chapterId)
episodesApi.delete(id)
episodesApi.exportUrl(id, format)
```

Provider methods cover list/create/update/delete/test. Encode all path segments with `encodeURIComponent`.

- [ ] **Step 6: Convert mock data to typed test fixture**

Import types from `@/api/types`, add newly required fields, and use `null` where the contract permits. Remove no image assets yet.

- [ ] **Step 7: Test and commit**

Run:

```bash
npm test -- src/test/api-client.test.ts src/test/api-types.test.ts
npm run build
git add frontend/package.json frontend/package-lock.json frontend/src/api frontend/src/data/mock.ts frontend/src/test/api-client.test.ts frontend/src/test/api-types.test.ts
git commit -m "feat(frontend): add typed v2 API client"
```

---

### Task 7: Add the single EventSource provider and fallback invalidation

**Files:**
- Create: `frontend/src/api/events.ts`
- Create: `frontend/src/features/events/EventProvider.tsx`
- Create: `frontend/src/features/events/useV2Events.ts`
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/components/layout/TopBar.tsx`
- Test: `frontend/src/test/events.test.tsx`

**Interfaces:**
- Produces: `useV2Events(filter, callback)` and connection state `connecting | open | closed`.

- [ ] **Step 1: Write EventSource lifecycle tests**

With a fake EventSource, assert StrictMode still leaves one live connection, matching events call subscribed handlers, unsubscribe removes handlers, `open` triggers a `reconnected` invalidation, and `error` exposes closed state.

- [ ] **Step 2: Run and verify failure**

Run: `npm test -- src/test/events.test.tsx`

- [ ] **Step 3: Implement transport and provider**

Create one `EventSource("/api/v2/events")` inside provider effect cleanup. Parse named event `MessageEvent.data` as JSON, validate `type` and required string IDs before dispatch, and ignore malformed events without logging payloads.

Use a subscriber registry stored in `useRef`. `useV2Events` registers a stable callback for an optional predicate.

- [ ] **Step 4: Add 30-second fallback behavior**

Expose `useFallbackRefresh(callback, active)` that schedules only when SSE is not open and `document.visibilityState === "visible"`. Visibility returning to visible performs one immediate refresh.

- [ ] **Step 5: Display connection status**

TopBar shows a small “Reconnecting…” indicator only while the connection is closed/connecting after the first successful open. It must not block navigation or mutations.

- [ ] **Step 6: Test and commit**

Run:

```bash
npm test -- src/test/events.test.tsx
npm run build
git add frontend/src/api/events.ts frontend/src/features/events frontend/src/main.tsx frontend/src/components/layout/TopBar.tsx frontend/src/test/events.test.tsx
git commit -m "feat(frontend): subscribe to v2 events"
```

---

### Task 8: Serve the React SPA safely at `/v2`

**Files:**
- Create: `backend/v2/services/frontend.py`
- Modify: `backend/main.py`
- Modify: `frontend/vite.config.ts`
- Modify: `frontend/src/main.tsx`
- Modify: `Dockerfile`
- Test: `tests/v2/test_frontend_hosting.py`
- Test: `frontend/src/test/router-base.test.tsx`

**Interfaces:**
- Produces: `/v2`, `/v2/dashboard`, and `/v2/episodes/<id>` SPA responses.
- Preserves: `/`, `/static/*`, `/api/*`, `/api/v2/*`.

- [ ] **Step 1: Write hosting tests**

Build a temporary dist containing `index.html` and `assets/app.js`. Assert SPA routes return the index, asset traversal is rejected, API 404 remains JSON rather than SPA HTML, and the legacy root still returns the old page.

- [ ] **Step 2: Run and verify failure**

Run: `python -m unittest tests.v2.test_frontend_hosting -v`

- [ ] **Step 3: Configure Vite base and proxy**

```ts
export default defineConfig({
  base: "/v2/",
  server: {
    port: 7100,
    host: true,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
})
```

Set `<BrowserRouter basename="/v2">`.

- [ ] **Step 4: Implement guarded production hosting**

Register `/v2/assets` with `StaticFiles` only when `frontend/dist/assets` exists. Add explicit `/v2` and `/v2/{client_path:path}` GET routes returning `index.html`; never use the client path to open arbitrary files. Install these routes after APIs and before no catch-all that could shadow them.

- [ ] **Step 5: Add Docker multi-stage build**

First stage uses a Node image, `npm ci`, and `npm run build`. Python stage copies `/frontend/dist` into `/app/frontend/dist`. Do not install Node in the runtime image.

- [ ] **Step 6: Verify and commit**

Run:

```bash
python -m unittest tests.v2.test_frontend_hosting tests.v2.test_legacy_api_contract -v
cd frontend && npm test -- src/test/router-base.test.tsx && npm run build
git add backend/v2/services/frontend.py backend/main.py frontend/vite.config.ts frontend/src/main.tsx Dockerfile tests/v2/test_frontend_hosting.py frontend/src/test/router-base.test.tsx
git commit -m "feat: host VIDA v2 frontend"
```

---

### Task 9: Implement Provider Profile settings and preferences

**Files:**
- Create: `frontend/src/features/profiles/preferences.ts`
- Create: `frontend/src/features/profiles/useProfiles.ts`
- Create: `frontend/src/features/profiles/ProfileForm.tsx`
- Create: `frontend/src/pages/SettingsPage.tsx`
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/test/settings-page.test.tsx`

**Interfaces:**
- Produces: `getSubmissionPreferences()`, `setSubmissionPreferences()`, and a complete Settings page.

- [ ] **Step 1: Write settings behavior tests**

Test list rendering, create payload, edit without sending empty `apiKey`, connection result, delete confirmation, and clearing a deleted selected profile from localStorage.

- [ ] **Step 2: Run and verify failure**

Run: `npm test -- src/test/settings-page.test.tsx`

- [ ] **Step 3: Implement preferences**

Store one JSON object under `vida.v2.submissionPreferences`:

```ts
interface SubmissionPreferences {
  providerProfileId: string | null
  summaryLanguage: string
}
```

Invalid JSON or invalid field types fall back to `{ providerProfileId: null, summaryLanguage: "zh" }`.

- [ ] **Step 4: Implement profile hooks and form**

The create form requires name, base URL, API key, model ID, and temperature 0–2. Edit mode renders API key blank and omits it from PATCH when blank. Disable destructive controls during mutation and normalize all failures through `ApiError`.

- [ ] **Step 5: Replace the Settings placeholder route**

Render list, create/edit panel, Test Connection action, masked key, revision, and explicit delete confirmation copy.

- [ ] **Step 6: Test and commit**

Run:

```bash
npm test -- src/test/settings-page.test.tsx
npm run build
git add frontend/src/features/profiles frontend/src/pages/SettingsPage.tsx frontend/src/App.tsx frontend/src/test/settings-page.test.tsx
git commit -m "feat(frontend): manage provider profiles"
```

---

### Task 10: Implement file/URL submission and upload progress

**Files:**
- Create: `frontend/src/features/submission/upload.ts`
- Create: `frontend/src/features/submission/SubmissionForm.tsx`
- Create: `frontend/src/pages/TranscribePage.tsx`
- Modify: `frontend/src/components/dashboard/UploadCard.tsx`
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/test/submission-form.test.tsx`

**Interfaces:**
- Produces: `uploadEpisode(input, onProgress, signal) -> Promise<Episode>`.
- Consumes: Profile preferences from Task 9 and `episodesApi.submitUrl`.

- [ ] **Step 1: Write submission tests**

Test source mode exclusivity, allowed extensions, required Profile/language, URL JSON body, multipart field names, XHR progress calculation, error envelope parsing, and navigation to `/episodes/{id}` on 202.

- [ ] **Step 2: Run and verify failure**

Run: `npm test -- src/test/submission-form.test.tsx`

- [ ] **Step 3: Implement XHR upload**

Append exactly `file`, `providerProfileId`, `summaryLanguage`, and optional nonblank `title`. Convert `xhr.upload.onprogress` to an integer 0–100. Parse the same standard error envelope and `X-Request-ID` as `apiRequest`.

- [ ] **Step 4: Implement the full Transcribe form**

Support `.mp3 .mp4 .m4a .wav .webm .mkv .ogg .flac`; do not advertise MOV or AVI. Separate labels “Uploading to server” and “Processing on server”. After the upload request completes, navigate immediately; EpisodePage owns processing state.

- [ ] **Step 5: Make Dashboard UploadCard callback-driven**

`UploadCard` accepts `onFileSelected(file)` and disabled/help state. It supports click selection and drag/drop without directly importing API modules.

- [ ] **Step 6: Test and commit**

Run:

```bash
npm test -- src/test/submission-form.test.tsx src/test/upload-card.test.tsx
npm run build
git add frontend/src/features/submission frontend/src/pages/TranscribePage.tsx frontend/src/components/dashboard/UploadCard.tsx frontend/src/App.tsx frontend/src/test/submission-form.test.tsx frontend/src/test/upload-card.test.tsx
git commit -m "feat(frontend): submit v2 episodes"
```

---

### Task 11: Implement Episode lifecycle and content loading

**Files:**
- Create: `frontend/src/features/episode/useEpisode.ts`
- Create: `frontend/src/features/episode/EpisodeStatusPanel.tsx`
- Create: `frontend/src/pages/EpisodePage.tsx`
- Modify: `frontend/src/components/dashboard/PlayerCard.tsx`
- Modify: `frontend/src/components/dashboard/TranscriptCard.tsx`
- Modify: `frontend/src/components/dashboard/SummaryCard.tsx`
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/test/episode-page.test.tsx`

**Interfaces:**
- Produces: `useEpisode(id)` with `episode`, conditional content, `refresh`, `cancel`, `retry`.

- [ ] **Step 1: Write state matrix tests**

For each status assert:

```text
queued      progress + queue position + cancel
processing  progress + message + cancel
failed      retry + delete
canceled    retry + delete
completed   media + transcript + summary + chapters + export controls
```

Also test `episode.updated` refresh, 404 redirect prompt, Summary 404 as a local empty state, and warnings grouped by stage.

- [ ] **Step 2: Run and verify failure**

Run: `npm test -- src/test/episode-page.test.tsx`

- [ ] **Step 3: Implement conditional loading hook**

Always request Episode first. Only when status is completed, load transcript, summary, and chapters with `Promise.allSettled`; treat only `summary_not_found` as an expected nullable Summary. Abort stale requests on ID change/unmount.

- [ ] **Step 4: Implement status actions**

Cancel calls Episode cancel and refreshes. Retry stores returned Job state and refreshes. On 409, refresh before showing the conflict. Listen only for matching Episode/Job events, plus reconnect invalidation.

- [ ] **Step 5: Make content cards real**

PlayerCard uses `episode.mediaUrl` in a native `<video controls>` and nullable poster fallback. Transcript supports local text filtering. Summary accepts `Summary | null`, loading, and regenerate callback.

- [ ] **Step 6: Route and commit**

Run:

```bash
npm test -- src/test/episode-page.test.tsx src/test/player-card.test.tsx src/test/transcript-card.test.tsx src/test/summary-card.test.tsx
npm run build
git add frontend/src/features/episode frontend/src/pages/EpisodePage.tsx frontend/src/components/dashboard/PlayerCard.tsx frontend/src/components/dashboard/TranscriptCard.tsx frontend/src/components/dashboard/SummaryCard.tsx frontend/src/App.tsx frontend/src/test/episode-page.test.tsx
git commit -m "feat(frontend): show v2 episode lifecycle"
```

---

### Task 12: Complete chapters, regeneration, exports, and deletion

**Files:**
- Create: `frontend/src/features/episode/ChapterEditor.tsx`
- Create: `frontend/src/features/episode/DeleteEpisodeDialog.tsx`
- Modify: `frontend/src/features/episode/useEpisode.ts`
- Modify: `frontend/src/components/dashboard/ChaptersCard.tsx`
- Modify: `frontend/src/components/dashboard/ExportCard.tsx`
- Modify: `frontend/src/pages/EpisodePage.tsx`
- Test: `frontend/src/test/episode-actions.test.tsx`

**Interfaces:**
- Adds: create/update/delete/bookmark Chapter, regenerate Summary/Chapters, export, and delete Episode.

- [ ] **Step 1: Write action tests**

Assert generated chapter content controls are absent, bookmark works for both sources, manual edit sends only changed fields, delete confirmation is required, regeneration disables duplicate actions, and export URL has encoded ID and format.

- [ ] **Step 2: Run and verify failure**

Run: `npm test -- src/test/episode-actions.test.tsx`

- [ ] **Step 3: Implement ChapterEditor**

Validate finite `startSec` in `0..episode.durationSec` and nonblank title. Submit exact API fields. Optimistic bookmark updates may be used, but roll back and refetch on any error.

- [ ] **Step 4: Implement regeneration**

Store the returned Job ID for the current browser session. Matching `job.updated` events refresh Summary/Chapters when terminal. After reload, a 409 `job_already_active` triggers a resource refresh and an “operation already running” message.

- [ ] **Step 5: Implement export and deletion**

Exports use normal `<a href>` navigation to the same-origin download endpoint so the browser honors `Content-Disposition`. Deletion requires typing/confirming the Episode title, calls DELETE, and navigates to `/library` only after 204.

- [ ] **Step 6: Test and commit**

Run:

```bash
npm test -- src/test/episode-actions.test.tsx src/test/chapters-card.test.tsx src/test/export-card.test.tsx
npm run build
git add frontend/src/features/episode frontend/src/components/dashboard/ChaptersCard.tsx frontend/src/components/dashboard/ExportCard.tsx frontend/src/pages/EpisodePage.tsx frontend/src/test/episode-actions.test.tsx
git commit -m "feat(frontend): add episode content actions"
```

---

### Task 13: Connect Dashboard, Library, and Summaries

**Files:**
- Create: `frontend/src/features/dashboard/useDashboard.ts`
- Create: `frontend/src/features/library/useLibrary.ts`
- Create: `frontend/src/pages/LibraryPage.tsx`
- Create: `frontend/src/pages/SummariesPage.tsx`
- Modify: `frontend/src/pages/DashboardPage.tsx`
- Modify: `frontend/src/components/dashboard/RecentProjectsCard.tsx`
- Modify: `frontend/src/App.tsx`
- Delete: `frontend/src/data/provider.ts`
- Test: `frontend/src/test/dashboard-page.test.tsx`
- Test: `frontend/src/test/library-page.test.tsx`
- Test: `frontend/src/test/summaries-page.test.tsx`

**Interfaces:**
- Produces: real Dashboard aggregate, offset pagination, loaded-page filtering, and selected Summary loading.

- [ ] **Step 1: Rewrite Dashboard tests against mocked API**

Cover normal aggregate, `currentEpisode: null`, request failure/retry, quick upload, recent queued progress, and SSE invalidation. Remove assertions tied to “AI Podcast Episode 12”.

- [ ] **Step 2: Write Library and Summaries tests**

Library tests cover `limit=12&offset=0`, load-more offset, local status/title filters, cancel, terminal delete, and link to detail. Summaries tests verify that the page does not request every Summary; it requests only the selected completed Episode.

- [ ] **Step 3: Run and verify failure**

Run:

```bash
npm test -- src/test/dashboard-page.test.tsx src/test/library-page.test.tsx src/test/summaries-page.test.tsx
```

- [ ] **Step 4: Implement Dashboard hook and nullable layout**

Retain the approved visual grid when a current completed Episode exists. For an empty Dashboard, show UploadCard plus a single explanatory empty panel. Use relative API media URLs directly and fallback artwork only when the server returns null.

- [ ] **Step 5: Implement Library**

Append pages without duplicate IDs. Applying a filter never changes server query semantics. `episode.updated` replaces a matching loaded Project or refreshes the first page; `episode.deleted` removes it immediately then refreshes.

- [ ] **Step 6: Implement Summaries**

Render completed Projects. Selecting one calls `getSummary(id)`; handle `summary_not_found` locally. Regeneration uses the same action behavior as Episode Detail.

- [ ] **Step 7: Remove runtime MockProvider and commit**

Keep `mock.ts` only for component fixtures. Delete `provider.ts` and its old provider test.

Run:

```bash
npm test
npm run lint
npm run build
git add frontend/src/features/dashboard frontend/src/features/library frontend/src/pages frontend/src/components/dashboard/RecentProjectsCard.tsx frontend/src/App.tsx frontend/src/test
git rm frontend/src/data/provider.ts frontend/src/test/provider.test.ts
git commit -m "feat(frontend): connect v2 workspace pages"
```

---

### Task 14: Full verification, documentation, and release hardening

**Files:**
- Modify: `frontend/README.md`
- Modify: `README.md`
- Modify: `README_ZH.md`
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Test: all backend and frontend suites

**Interfaces:**
- Produces: documented local/Docker startup and a verified single-port release.

- [ ] **Step 1: Update operational documentation**

Document:

```text
Legacy UI: http://localhost:8000/
VIDA 2.0:  http://localhost:8000/v2/
Vite dev:  http://localhost:7100/v2/
```

Explain `VIDA_PROFILE_MASTER_KEY`, data backup, single-process requirement, frontend build, and SSE proxy buffering requirements.

- [ ] **Step 2: Verify frontend from a clean install**

Run:

```bash
cd frontend
npm ci
npm test
npm run lint
npm run build
```

Expected: every command exits 0.

- [ ] **Step 3: Verify all Python tests**

Run from repository root:

```bash
python -m unittest discover -s tests -v
```

Expected: all v1 and v2 tests PASS.

- [ ] **Step 4: Build and run Docker**

Run:

```bash
docker compose build ai-video-transcriber
docker compose up -d ai-video-transcriber
curl -f http://localhost:8000/
curl -f http://localhost:8000/v2/
curl -f http://localhost:8000/api/v2/dashboard
curl -N --max-time 20 http://localhost:8000/api/v2/events
```

Expected: legacy and v2 HTML return 200, Dashboard returns JSON, SSE returns retry/heartbeat framing.

- [ ] **Step 5: Perform the end-to-end smoke flow**

Through `/v2`:

1. Create and test a Provider Profile.
2. Submit one small supported media file.
3. Observe upload progress, then SSE processing progress.
4. Open completed media, transcript, Summary, and Chapters.
5. Bookmark a generated Chapter and create/edit/delete a manual Chapter.
6. Regenerate Summary and Chapters.
7. Download TXT, SRT, and Markdown.
8. Delete the terminal Episode and verify it disappears from all pages.
9. Stop SSE temporarily and verify fallback refresh recovers state.

- [ ] **Step 6: Inspect for sensitive output**

Search browser console, network event payloads, server logs, and test output for the test API key, provider URL query, encrypted credential, and filesystem paths. Any occurrence is a release blocker.

- [ ] **Step 7: Commit release documentation**

```bash
git add frontend/README.md README.md README_ZH.md docker-compose.yml .env.example
git commit -m "docs: document VIDA v2 frontend deployment"
```

Expected: clean verification output and no unstaged implementation files.

