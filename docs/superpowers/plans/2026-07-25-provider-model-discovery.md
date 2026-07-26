# Provider Model Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add secure v2 Provider model discovery so Settings automatically fetches models after a 900ms debounce and lets users select the Profile model from a dropdown.

**Architecture:** Add an injected backend model fetcher and a draft-credential v2 endpoint that can either use a submitted API Key or securely reuse an existing Profile active revision. Add a focused React hook for debounce, cancellation, stale-response protection, and status; keep the Profile form responsible only for field state and rendering.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, OpenAI Python SDK, unittest, React 19, TypeScript 6, Vite 8, Vitest, Testing Library.

## Global Constraints

- Keep SQLite and REST as the source of truth.
- Do not persist draft Base URL, API Key, or fetched model lists.
- Never return or log API Keys, upstream bodies, sensitive URL queries, headers, or raw SDK errors.
- New endpoint: `POST /api/v2/provider-profiles/models`.
- Base URL is required; requests without both `profileId` and `apiKey` return 422.
- An explicit draft API Key overrides the stored active-revision key.
- Use a 15-second Provider client timeout and always close the client.
- Normalize at most 2000 unique models; ignore blank or over-512-character IDs/names.
- Frontend auto-fetch delay is exactly 900ms and a manual Fetch models button remains available.
- A fetched list never auto-saves or creates a Profile revision.
- Preserve current saved models that are not returned by the Provider.
- Update `docs/api/v2-api-contract.md` and add `docs/devnote/2026-07-0725/provider-model-discovery.md`.
- Follow root `AGENTS.md` and `backend/v2/AGENTS.md`.

---

### Task 1: Add the model fetcher runtime boundary and normalization

**Files:**
- Modify: `backend/v2/container.py`
- Modify: `backend/v2/bootstrap.py`
- Test: `tests/v2/test_provider_profile_api.py`

**Interfaces:**
- Produces: `ProviderModelOption = tuple[str, str]`
- Produces: `ProviderModelFetchResult = tuple[list[ProviderModelOption], int]`
- Produces: `ProviderModelFetcher = Callable[[str, str], Awaitable[ProviderModelFetchResult]]`
- Produces: `fetch_provider_models(base_url: str, api_key: str) -> ProviderModelFetchResult`
- Produces: `V2Runtime.provider_model_fetcher`

- [ ] **Step 1: Write failing normalization and client-lifecycle tests**

Append a `ProviderModelFetcherTests(unittest.IsolatedAsyncioTestCase)` class to
`tests/v2/test_provider_profile_api.py`. Import
`fetch_provider_models` from `backend.v2.bootstrap`.

Use a fake client whose model response contains:

```python
SimpleNamespace(
    data=[
        SimpleNamespace(id="z-model", name="Zed"),
        SimpleNamespace(id="A-model", name=""),
        SimpleNamespace(id="z-model", name="Duplicate"),
        SimpleNamespace(id="  ", name="Blank"),
        SimpleNamespace(id="x" * 513, name="Too long"),
    ]
)
```

Patch:

```python
patch("backend.v2.bootstrap.OpenAI", return_value=client)
patch(
    "backend.v2.bootstrap.asyncio.to_thread",
    side_effect=lambda function: asyncio.sleep(0, result=function()),
)
patch(
    "backend.v2.bootstrap.time",
    new=SimpleNamespace(monotonic=Mock(side_effect=[10.0, 10.125])),
)
```

Assert:

```python
self.assertEqual(
    await fetch_provider_models("https://api.example/v1", "secret-key"),
    ([("A-model", "A-model"), ("z-model", "Zed")], 125),
)
openai_cls.assert_called_once_with(
    api_key="secret-key",
    base_url="https://api.example/v1",
    timeout=15.0,
)
client.close.assert_called_once_with()
```

Add separate tests proving:

```python
self.assertEqual(len(models), 2000)
```

for 2001 unique valid inputs, that `models.list` failure still closes the
client, and that `close` is attempted exactly once. API-boundary secret
sanitization is tested in Task 2, where raw helper exceptions are converted to
the v2 error envelope.

- [ ] **Step 2: Run the focused tests and verify red**

Run:

```bash
venv/bin/python -m unittest \
  tests.v2.test_provider_profile_api.ProviderModelFetcherTests -v
```

Expected: import failure because `fetch_provider_models` does not exist.

- [ ] **Step 3: Add runtime types and propagation**

In `backend/v2/container.py`, add:

```python
ProviderModelOption = tuple[str, str]
ProviderModelFetchResult = tuple[list[ProviderModelOption], int]
ProviderModelFetcher = Callable[
    [str, str],
    Awaitable[ProviderModelFetchResult],
]
```

Add to `V2Runtime`:

```python
provider_model_fetcher: ProviderModelFetcher | None = None
```

In `initialize()`, preserve test overrides and otherwise copy the initialized
runtime value:

```python
if self.provider_model_fetcher is None:
    self.provider_model_fetcher = initialized.provider_model_fetcher
```

In `backend/v2/bootstrap.py`, import the new result type, set:

```python
provider_model_fetcher=fetch_provider_models,
```

when constructing `V2Runtime`.

- [ ] **Step 4: Implement normalization and Provider calling**

In `backend/v2/bootstrap.py`, add:

```python
async def fetch_provider_models(
    base_url: str,
    api_key: str,
) -> ProviderModelFetchResult:
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=15.0)
    try:
        started = time.monotonic()
        response = await asyncio.to_thread(client.models.list)
        latency_ms = max(0, round((time.monotonic() - started) * 1000))
        models = _normalize_provider_models(response.data)
    finally:
        await asyncio.to_thread(client.close)
    return models, latency_ms
```

Add:

```python
def _normalize_provider_models(rows) -> list[tuple[str, str]]:
    unique: dict[str, tuple[str, str]] = {}
    for row in rows:
        model_id = getattr(row, "id", None)
        if not isinstance(model_id, str):
            continue
        model_id = model_id.strip()
        if not model_id or len(model_id) > 512 or model_id in unique:
            continue
        raw_name = getattr(row, "name", None)
        name = raw_name.strip() if isinstance(raw_name, str) else ""
        if not name:
            name = model_id
        if len(name) > 512:
            continue
        unique[model_id] = (model_id, name)
        if len(unique) == 2000:
            break
    return sorted(unique.values(), key=lambda item: (item[0].casefold(), item[0]))
```

Do not log `base_url`, `api_key`, `response`, or caught exceptions.

- [ ] **Step 5: Run focused tests and verify green**

Run:

```bash
venv/bin/python -m unittest \
  tests.v2.test_provider_profile_api.ProviderModelFetcherTests -v
```

Expected: all `ProviderModelFetcherTests` pass.

- [ ] **Step 6: Commit**

```bash
git add backend/v2/container.py backend/v2/bootstrap.py \
  tests/v2/test_provider_profile_api.py
git commit -m "feat(v2): fetch provider model lists"
```

### Task 2: Add secure draft model discovery API

**Files:**
- Modify: `backend/v2/schemas.py`
- Modify: `backend/v2/api/provider_profiles.py`
- Test: `tests/v2/test_provider_profile_api.py`

**Interfaces:**
- Consumes: `V2Runtime.provider_model_fetcher`
- Produces: `ProviderModelDiscoveryRequest`
- Produces: `ProviderModelOptionResponse`
- Produces: `ProviderModelDiscoveryResponse`
- Produces: `POST /api/v2/provider-profiles/models`

- [ ] **Step 1: Write failing API tests**

In `ProviderProfileApiTests.setUp`, add:

```python
self.runtime.provider_model_fetcher = AsyncMock(
    return_value=([("model-a", "Model A"), ("model-b", "Model B")], 24)
)
```

Add these concrete tests:

```python
def test_model_discovery_uses_draft_credentials(self):
    response = self.client.post(
        "/api/v2/provider-profiles/models",
        json={
            "baseUrl": "https://draft.example/v1",
            "apiKey": "draft-secret",
        },
    )
    self.assertEqual(response.status_code, 200, response.text)
    self.assertEqual(
        response.json(),
        {
            "models": [
                {"id": "model-a", "name": "Model A"},
                {"id": "model-b", "name": "Model B"},
            ],
            "latencyMs": 24,
        },
    )
    self.runtime.provider_model_fetcher.assert_awaited_once_with(
        "https://draft.example/v1", "draft-secret"
    )

def test_model_discovery_reuses_active_key_with_draft_url(self):
    created = self.create_profile()
    response = self.client.post(
        "/api/v2/provider-profiles/models",
        json={
            "profileId": created["id"],
            "baseUrl": "https://changed.example/v1",
        },
    )
    self.assertEqual(response.status_code, 200, response.text)
    self.runtime.provider_model_fetcher.assert_awaited_once_with(
        "https://changed.example/v1", "secret-key"
    )
```

Add tests for:

- request `apiKey="override-secret"` winning over the saved key;
- `{ "baseUrl": "https://api.example/v1" }` returning 422;
- unknown request fields returning 422;
- missing and deleted `profileId` returning `provider_profile_not_found`;
- fetcher raising `RuntimeError("upstream draft-secret")` returning 502 with
  `provider_models_fetch_failed`, empty details, matching `X-Request-ID`, and no
  secret/upstream text in the serialized body.

- [ ] **Step 2: Run the API tests and verify red**

Run:

```bash
venv/bin/python -m unittest \
  tests.v2.test_provider_profile_api.ProviderProfileApiTests -v
```

Expected: `/api/v2/provider-profiles/models` is not implemented.

- [ ] **Step 3: Add request and response schemas**

In `backend/v2/schemas.py`, add:

```python
class ProviderModelDiscoveryRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: ProviderProfileIdString | None = None
    base_url: HttpUrl
    api_key: SecretStr | None = None

    @field_validator("api_key", mode="before")
    @classmethod
    def validate_api_key(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("apiKey must not be empty")
        return value

    @model_validator(mode="after")
    def require_credentials(self) -> "ProviderModelDiscoveryRequest":
        if self.profile_id is None and self.api_key is None:
            raise ValueError("profileId or apiKey is required")
        return self


class ProviderModelOptionResponse(ApiModel):
    id: str
    name: str


class ProviderModelDiscoveryResponse(ApiModel):
    models: list[ProviderModelOptionResponse]
    latency_ms: int = Field(ge=0)
```

- [ ] **Step 4: Implement the route before dynamic profile routes**

In `backend/v2/api/provider_profiles.py`, import the new schemas and register
`POST /models` before `GET /{profile_id}`:

```python
@router.post("/models", response_model=ProviderModelDiscoveryResponse)
async def discover_models(
    payload: ProviderModelDiscoveryRequest,
) -> ProviderModelDiscoveryResponse:
    service = runtime.require_provider_profiles()
    saved_key: str | None = None
    if payload.profile_id is not None:
        profile = service.get_profile(payload.profile_id)
        if profile is None:
            raise _not_found()
        saved_key = service.get_revision_credentials(
            profile.active_revision_id
        ).api_key
    api_key = (
        payload.api_key.get_secret_value()
        if payload.api_key is not None
        else saved_key
    )
    if api_key is None:
        raise RuntimeError("validated request has no provider credentials")
    fetcher = runtime.provider_model_fetcher
    if fetcher is None:
        raise RuntimeError("provider model fetcher is not configured")
    try:
        models, latency_ms = await fetcher(str(payload.base_url), api_key)
    except V2Error:
        raise
    except Exception:
        raise V2Error(
            "provider_models_fetch_failed",
            "Unable to fetch provider models",
            502,
            {},
        ) from None
    return ProviderModelDiscoveryResponse(
        models=[
            ProviderModelOptionResponse(id=model_id, name=name)
            for model_id, name in models
        ],
        latency_ms=latency_ms,
    )
```

- [ ] **Step 5: Run API and secret-redaction tests**

Run:

```bash
venv/bin/python -m unittest \
  tests.v2.test_provider_profile_api.ProviderProfileApiTests \
  tests.v2.test_secret_redaction -v
```

Expected: all tests pass and no secret appears in captured API/log output.

- [ ] **Step 6: Commit**

```bash
git add backend/v2/schemas.py backend/v2/api/provider_profiles.py \
  tests/v2/test_provider_profile_api.py
git commit -m "feat(v2): discover provider models"
```

### Task 3: Extend the public API contract

**Files:**
- Modify: `docs/api/v2-api-contract.md`
- Modify: `tests/v2/test_legacy_api_contract.py`

**Interfaces:**
- Consumes: Task 2 route and schemas.
- Produces: approved OpenAPI route set and human-readable public contract.

- [ ] **Step 1: Add the route to the failing contract tests**

In `test_openapi_has_unique_operations_and_exact_approved_v2_routes`, add:

```python
"/api/v2/provider-profiles/models": {"post"},
```

In `test_contract_document_lists_every_approved_route`, add:

```python
"POST /api/v2/provider-profiles/models",
```

- [ ] **Step 2: Run contract tests and verify red**

Run:

```bash
venv/bin/python -m unittest tests.v2.test_legacy_api_contract -v
```

Expected: the documentation assertion fails until the contract text is updated.

- [ ] **Step 3: Document the exact API**

In `docs/api/v2-api-contract.md`, add the route to the Provider Profile route
list and document:

```json
{
  "profileId": "optional-existing-profile-id",
  "baseUrl": "https://api.example/v1",
  "apiKey": "optional-draft-api-key"
}
```

and:

```json
{
  "models": [
    { "id": "model-a", "name": "Model A" }
  ],
  "latencyMs": 24
}
```

State the precedence, no-persistence rule, 2000/512 limits, 15-second timeout,
404/422 behavior, and sanitized `provider_models_fetch_failed` 502 response.

- [ ] **Step 4: Run contract tests and verify green**

Run:

```bash
venv/bin/python -m unittest tests.v2.test_legacy_api_contract -v
```

Expected: all contract tests pass.

- [ ] **Step 5: Commit**

```bash
git add docs/api/v2-api-contract.md tests/v2/test_legacy_api_contract.py
git commit -m "docs(v2): define provider model discovery"
```

### Task 4: Add typed frontend model discovery API

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/profiles.ts`
- Test: `frontend/src/test/api-client.test.ts`

**Interfaces:**
- Produces: `ProviderModelDiscoveryInput`
- Produces: `ProviderModelOption`
- Produces: `ProviderModelDiscovery`
- Produces: `profilesApi.discoverModels(input, signal?)`

- [ ] **Step 1: Write a failing API serialization test**

In `frontend/src/test/api-client.test.ts`, import `profilesApi`, then add:

```ts
it("posts draft credentials to provider model discovery", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ models: [], latencyMs: 12 }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  )
  vi.stubGlobal("fetch", fetchMock)
  const controller = new AbortController()

  await profilesApi.discoverModels(
    {
      profileId: "profile-1",
      baseUrl: "https://api.example/v1",
      apiKey: "draft-secret",
    },
    controller.signal,
  )

  expect(fetchMock).toHaveBeenCalledWith(
    "/api/v2/provider-profiles/models",
    expect.objectContaining({
      method: "POST",
      signal: controller.signal,
      body: JSON.stringify({
        profileId: "profile-1",
        baseUrl: "https://api.example/v1",
        apiKey: "draft-secret",
      }),
    }),
  )
})
```

- [ ] **Step 2: Run the focused test and verify red**

Run:

```bash
cd frontend
npm test -- src/test/api-client.test.ts
```

Expected: `discoverModels` does not exist.

- [ ] **Step 3: Add types and API method**

In `frontend/src/api/types.ts`, add:

```ts
export interface ProviderModelDiscoveryInput {
  profileId?: string
  baseUrl: string
  apiKey?: string
}

export interface ProviderModelOption {
  id: string
  name: string
}

export interface ProviderModelDiscovery {
  models: ProviderModelOption[]
  latencyMs: number
}
```

In `frontend/src/api/profiles.ts`, import these types and add:

```ts
discoverModels(
  input: ProviderModelDiscoveryInput,
  signal?: AbortSignal,
): Promise<ProviderModelDiscovery> {
  return apiRequest("/api/v2/provider-profiles/models", {
    method: "POST",
    body: input,
    signal,
  })
},
```

- [ ] **Step 4: Run the API test and verify green**

Run:

```bash
cd frontend
npm test -- src/test/api-client.test.ts
```

Expected: test passes.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/api/profiles.ts \
  frontend/src/test/api-client.test.ts
git commit -m "feat(frontend): add provider model API"
```

### Task 5: Build the debounced model discovery hook

**Files:**
- Create: `frontend/src/features/profiles/useProviderModels.ts`
- Create: `frontend/src/test/provider-models.test.tsx`

**Interfaces:**
- Consumes: `profilesApi.discoverModels`
- Produces:

```ts
type ProviderModelsStatus = "idle" | "loading" | "success" | "error"

interface UseProviderModelsInput {
  profileId?: string
  baseUrl: string
  apiKey: string
}

interface UseProviderModelsResult {
  models: ProviderModelOption[]
  status: ProviderModelsStatus
  message: string
  canFetch: boolean
  refresh: () => void
}
```

- [ ] **Step 1: Write failing debounce and edit-credential tests**

Create `frontend/src/test/provider-models.test.tsx`. Mock
`@/api/profiles`, enable `vi.useFakeTimers()` in `beforeEach`, restore real
timers in `afterEach`, and use `renderHook`.

Test new Profile debounce:

```ts
const { result } = renderHook(() =>
  useProviderModels({
    baseUrl: "https://api.example/v1",
    apiKey: "draft-secret",
  }),
)
expect(profilesApi.discoverModels).not.toHaveBeenCalled()
await act(async () => vi.advanceTimersByTimeAsync(899))
expect(profilesApi.discoverModels).not.toHaveBeenCalled()
await act(async () => vi.advanceTimersByTimeAsync(1))
expect(profilesApi.discoverModels).toHaveBeenCalledWith(
  {
    baseUrl: "https://api.example/v1",
    apiKey: "draft-secret",
  },
  expect.any(AbortSignal),
)
```

Test edit mode with an empty API Key:

```ts
renderHook(() =>
  useProviderModels({
    profileId: "profile-1",
    baseUrl: "https://api.example/v1",
    apiKey: "",
  }),
)
await act(async () => vi.advanceTimersByTimeAsync(900))
expect(profilesApi.discoverModels).toHaveBeenCalledWith(
  {
    profileId: "profile-1",
    baseUrl: "https://api.example/v1",
  },
  expect.any(AbortSignal),
)
```

Add concrete tests for invalid/missing inputs staying idle, `refresh()` skipping
the timer, success/empty/error messages, request abort on changed input/unmount,
and a deferred old promise not overwriting a newer response.

- [ ] **Step 2: Run hook tests and verify red**

Run:

```bash
cd frontend
npm test -- src/test/provider-models.test.tsx
```

Expected: module not found.

- [ ] **Step 3: Implement eligibility and safe request input**

Create `useProviderModels.ts` with:

```ts
function validProviderUrl(value: string): boolean {
  try {
    const url = new URL(value)
    return url.protocol === "http:" || url.protocol === "https:"
  } catch {
    return false
  }
}
```

Build request input without an empty API Key:

```ts
const requestInput = {
  ...(profileId ? { profileId } : {}),
  baseUrl: baseUrl.trim(),
  ...(apiKey.trim() ? { apiKey: apiKey.trim() } : {}),
}
```

`canFetch` is true only when the URL is valid and either `profileId` or a
trimmed API Key exists.

- [ ] **Step 4: Implement debounce, manual refresh, and stale protection**

Use refs:

```ts
const controllerRef = useRef<AbortController | null>(null)
const requestTokenRef = useRef(0)
```

Before every request:

```ts
controllerRef.current?.abort()
const controller = new AbortController()
controllerRef.current = controller
const token = ++requestTokenRef.current
setStatus("loading")
setMessage("Fetching models…")
```

Only update state when:

```ts
if (!controller.signal.aborted && token === requestTokenRef.current)
```

Map success messages exactly:

```ts
models.length === 0
  ? "No models returned"
  : `Loaded ${models.length} models · ${latencyMs} ms`
```

Map `ApiError` to its safe message and other failures to
`"Unable to fetch provider models"`. Preserve the previous model list during
loading or failure. The effect uses `setTimeout(execute, 900)` and cleanup
clears the timer; unmount also aborts the controller.

- [ ] **Step 5: Run hook tests and verify green**

Run:

```bash
cd frontend
npm test -- src/test/provider-models.test.tsx
```

Expected: all hook tests pass with fake timers and no act warnings.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/profiles/useProviderModels.ts \
  frontend/src/test/provider-models.test.tsx
git commit -m "feat(frontend): fetch provider models"
```

### Task 6: Replace Model ID input with the model selector

**Files:**
- Modify: `frontend/src/features/profiles/ProfileForm.tsx`
- Modify: `frontend/src/test/settings-page.test.tsx`

**Interfaces:**
- Consumes: `useProviderModels`
- Produces: API Key row with Fetch models button and status line.
- Produces: native `select` labeled `Model ID`.

- [ ] **Step 1: Update mocks and write failing Settings tests**

Extend the `profilesApi` mock:

```ts
discoverModels: vi.fn(),
```

Reset it in `beforeEach`:

```ts
vi.mocked(profilesApi.discoverModels).mockReset().mockResolvedValue({
  models: [
    { id: "model-1", name: "Model One" },
    { id: "model-2", name: "Model Two" },
  ],
  latencyMs: 18,
})
```

Use fake timers only in the model-discovery tests. Add a new Profile test:

```ts
fireEvent.click(screen.getByRole("button", { name: "New profile" }))
fireEvent.change(screen.getByLabelText("Base URL"), {
  target: { value: "https://second.example/v1" },
})
fireEvent.change(screen.getByLabelText("API key"), {
  target: { value: "secret-key" },
})
await act(async () => vi.advanceTimersByTimeAsync(900))
expect(await screen.findByText("Loaded 2 models · 18 ms")).toBeInTheDocument()
fireEvent.change(screen.getByLabelText("Model ID"), {
  target: { value: "model-2" },
})
```

Assert the Create call contains `modelId: "model-2"`.

Add tests proving:

- `Fetch models` immediately calls discovery and is disabled while loading;
- edit opening sends `profileId` and omits empty API Key;
- current `"legacy-model"` appears as
  `"Current: legacy-model — not returned by provider"`;
- no model selection keeps Create disabled;
- an empty result displays `No models returned`.

- [ ] **Step 2: Run Settings tests and verify red**

Run:

```bash
cd frontend
npm test -- src/test/settings-page.test.tsx
```

Expected: Model ID is still a text input and no Fetch models button exists.

- [ ] **Step 3: Integrate the hook and model options**

In `ProfileForm`, call:

```ts
const {
  models,
  status: modelsStatus,
  message: modelsMessage,
  canFetch,
  refresh,
} = useProviderModels({
  ...(profile ? { profileId: profile.id } : {}),
  baseUrl,
  apiKey,
})
```

Build options without duplicating the current selected model:

```ts
const currentModelMissing =
  Boolean(modelId) && !models.some((model) => model.id === modelId)
const modelOptions = !currentModelMissing
  ? models
  : [{ id: modelId, name: modelId }, ...models]
```

Render API Key and button in one flex row:

```tsx
<div className="mt-1 flex gap-2">
  <input aria-label="API key" type="password" ... />
  <Button
    type="button"
    variant="outline"
    disabled={!canFetch || modelsStatus === "loading"}
    onClick={refresh}
  >
    {modelsStatus === "loading" ? "Fetching…" : "Fetch models"}
  </Button>
</div>
```

Render `modelsMessage` below the row with `role="status"` for non-error and
`role="alert"` for error.

Replace the Model ID input with:

```tsx
<select
  aria-label="Model ID"
  className={fieldClass}
  value={modelId}
  onChange={(event) => setModelId(event.target.value)}
  required
>
  <option value="">Fetch models to choose</option>
  {modelOptions.map((model) => (
    <option key={model.id} value={model.id}>
      {currentModelMissing && model.id === modelId
        ? `Current: ${modelId} — not returned by provider`
        : model.name === model.id
          ? model.id
          : `${model.name} (${model.id})`}
    </option>
  ))}
</select>
```

Disable Create/Save when submitting or `modelId` is empty. Do not clear a
saved `modelId` while fetching.

- [ ] **Step 4: Run Settings and hook tests**

Run:

```bash
cd frontend
npm test -- src/test/settings-page.test.tsx src/test/provider-models.test.tsx
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/profiles/ProfileForm.tsx \
  frontend/src/test/settings-page.test.tsx
git commit -m "feat(frontend): select provider models"
```

### Task 7: Document the development and run full verification

**Files:**
- Create: `docs/devnote/2026-07-0725/provider-model-discovery.md`
- Verify: all files changed in Tasks 1–6.

**Interfaces:**
- Consumes: completed backend API and frontend UI.
- Produces: required devnote and release evidence.

- [ ] **Step 1: Write the required devnote**

Create:

```markdown
# Provider 模型自动发现开发记录

## 1. 开发内容
## 2. 学习与可沉淀经验
## 3. 回滚操作
```

Under development content, record the v2 draft endpoint, active-revision key
reuse, 900ms debounce, manual refresh, stale request protection, dropdown, API
contract, and actual test results.

Under reusable experience, explain:

- discovery is non-persistent and must not create Profile revisions;
- stored secrets are reused only server-side;
- request cancellation plus a monotonic token is required because cancellation
  alone does not guarantee stale results cannot resolve;
- Provider errors must be normalized at the API boundary.

Under rollback, list the feature commits from this plan in reverse `git revert`
order, state that no database migration is involved, and verify the previous
manual Model ID input and existing Profile CRUD after rollback.

- [ ] **Step 2: Run focused backend suites**

Run:

```bash
venv/bin/python -m unittest \
  tests.v2.test_provider_profile_api \
  tests.v2.test_secret_redaction \
  tests.v2.test_legacy_api_contract -v
```

Expected: all focused backend tests pass.

- [ ] **Step 3: Run the complete backend suite**

Run:

```bash
venv/bin/python -c \
  "import tempfile, unittest; tempfile.tempdir='/private/tmp'; suite=unittest.defaultTestLoader.discover('tests'); result=unittest.TextTestRunner(verbosity=1).run(suite); raise SystemExit(not result.wasSuccessful())"
```

Expected: zero failures and zero errors.

- [ ] **Step 4: Run frontend tests, lint, and build**

Run:

```bash
cd frontend
npm test
npm run lint
npm run build
```

Expected: tests pass, lint has no errors, and Vite production build succeeds.
The two existing Fast Refresh warnings may remain if unchanged.

- [ ] **Step 5: Verify documentation and secret hygiene**

Run:

```bash
git diff --check
rg -n "POST /api/v2/provider-profiles/models" docs/api/v2-api-contract.md
rg -n "^## (1\\. 开发内容|2\\. 学习与可沉淀经验|3\\. 回滚操作)$" \
  docs/devnote/2026-07-0725/provider-model-discovery.md
rg -n "draft-secret|secret-key" backend frontend docs \
  -g '!**/test*' -g '!docs/superpowers/**'
```

Expected: formatting passes, contract/devnote markers exist, and no production
or user documentation file contains test secrets.

- [ ] **Step 6: Commit**

```bash
git add docs/devnote/2026-07-0725/provider-model-discovery.md
git commit -m "docs: record provider model discovery"
```

- [ ] **Step 7: Inspect final branch**

Run:

```bash
git status --short --branch
git log --oneline --decorate -8
```

Expected: clean feature branch with the implementation and devnote commits.
