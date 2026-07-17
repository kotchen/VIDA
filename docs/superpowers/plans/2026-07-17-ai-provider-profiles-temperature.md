# AI Provider Profiles And Temperature Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add browser-local provider profiles to AI Settings and propagate a validated, model-specific temperature through every OpenAI-compatible text operation.

**Architecture:** Put versioned profile state and migration rules in a dependency-free browser/CommonJS module so they can be tested with `node:test`. Keep DOM rendering and request assembly in the existing application controller. Add a pure backend temperature validator and centralize completion creation in `Summarizer` and `Translator`, then thread the validated value through both URL and upload task pipelines.

**Tech Stack:** Vanilla HTML/CSS/JavaScript, browser localStorage, Node.js built-in test runner, Python 3.11 unittest, FastAPI, OpenAI Python client.

## Global Constraints

- Preserve all pre-existing uncommitted repository changes.
- Provider profiles stay in browser localStorage under versioned `vt_settings` data.
- API Keys are never persisted by the backend or logged.
- General temperature default is `0.1`; case-insensitive model ID `k3` defaults to `1`.
- Temperature is finite and within inclusive range `0..2`.
- Do not change Faster-Whisper decoding temperatures.
- Reuse the current compact dark UI, CSS variables, control heights, spacing, and responsive behavior.
- Restart the local service on port 8001 after verification.

---

### Task 1: Versioned Provider Profile Store

**Files:**
- Create: `static/settings-store.js`
- Create: `tests/settings-store.test.js`

**Interfaces:**
- Produces: `AIProfileStore.defaultTemperature(modelId) -> number`
- Produces: `AIProfileStore.load(rawSettings) -> SettingsV2`
- Produces: `AIProfileStore.createProfile(name) -> ProviderProfile`
- Produces: `AIProfileStore.captureProfile(profile, fields) -> ProviderProfile`
- Produces: `AIProfileStore.temperatureFor(profile, modelId) -> number`
- Produces: `AIProfileStore.setModelTemperature(profile, modelId, value) -> ProviderProfile`

- [ ] **Step 1: Write failing Node tests for migration and defaults**

Create `tests/settings-store.test.js` using `node:test` and `node:assert/strict`. Cover these exact assertions:

```javascript
const test = require('node:test');
const assert = require('node:assert/strict');
const Store = require('../static/settings-store.js');

test('migrates legacy settings into one Default provider profile', () => {
  const settings = Store.load(JSON.stringify({
    baseUrl: 'https://api.example/v1',
    apiKey: 'secret',
    model: 'model-a',
    summaryLang: 'en',
  }));
  assert.equal(settings.version, 2);
  assert.equal(settings.summaryLang, 'en');
  assert.equal(settings.profiles.length, 1);
  assert.equal(settings.profiles[0].name, 'Default');
  assert.equal(settings.profiles[0].baseUrl, 'https://api.example/v1');
  assert.equal(settings.profiles[0].apiKey, 'secret');
  assert.equal(settings.profiles[0].lastModel, 'model-a');
  assert.equal(settings.profiles[0].modelTemperatures['model-a'], 0.1);
});

test('uses model-specific temperature defaults', () => {
  assert.equal(Store.defaultTemperature('k3'), 1);
  assert.equal(Store.defaultTemperature('K3'), 1);
  assert.equal(Store.defaultTemperature('deepseek-v4-flash'), 0.1);
});

test('falls back to a blank profile for invalid storage', () => {
  const settings = Store.load('{bad json');
  assert.equal(settings.version, 2);
  assert.equal(settings.profiles.length, 1);
  assert.equal(settings.profiles[0].name, 'Default');
});
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
& 'C:\Users\gsgame\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test tests\settings-store.test.js
```

Expected: FAIL because `static/settings-store.js` does not exist.

- [ ] **Step 3: Implement the profile store module**

Use a UMD-style wrapper that exposes the same API as `window.AIProfileStore` in the browser and `module.exports` in Node. Implement version 2 settings with this shape:

```javascript
{
  version: 2,
  activeProfileId: 'stable-local-id',
  summaryLang: 'zh',
  profiles: [{
    id: 'stable-local-id',
    name: 'Default',
    baseUrl: '',
    apiKey: '',
    lastModel: '',
    modelTemperatures: {},
  }],
}
```

Generate IDs with `crypto.randomUUID()` when available and an ASCII timestamp/random fallback otherwise. Normalize base URLs by removing trailing slashes. Clamp neither stored nor entered temperature silently; return the model default for missing or non-finite stored values and let backend validation reject invalid submitted values.

- [ ] **Step 4: Add tests for capture and per-model restoration**

Add tests proving that `captureProfile` saves URL, Key, and selected model; `setModelTemperature` remembers separate values for `k3` and another model; and `temperatureFor` restores each value after switching.

- [ ] **Step 5: Run Node tests and verify GREEN**

Run the Step 2 command. Expected: all profile store tests PASS.

- [ ] **Step 6: Commit the profile store**

```powershell
git add -- static/settings-store.js tests/settings-store.test.js
git commit -m "feat: add browser provider profile store"
```

### Task 2: AI Settings Profile Toolbar And Temperature Control

**Files:**
- Modify: `static/index.html`
- Modify: `static/app.js`
- Create: `tests/test_frontend_settings_contract.py`

**Interfaces:**
- Consumes: `window.AIProfileStore` from Task 1.
- Produces DOM IDs: `providerProfileSelect`, `addProfileBtn`, `saveProfileBtn`, `deleteProfileBtn`, `profileNameRow`, `profileNameInput`, `temperatureInput`.
- Produces multipart fields: `temperature` on URL and upload requests.

- [ ] **Step 1: Write failing frontend contract tests**

Create Python unittest tests that read `static/index.html` and `static/app.js` and assert:

```python
for element_id in (
    "providerProfileSelect", "addProfileBtn", "saveProfileBtn",
    "deleteProfileBtn", "profileNameInput", "temperatureInput",
):
    self.assertIn(f'id="{element_id}"', html)
self.assertIn('/static/settings-store.js', html)
self.assertGreaterEqual(app_js.count("fd.append('temperature'"), 2)
```

Also assert that icon buttons have `aria-label` and `data-tooltip`, and that the temperature input contains `min="0"`, `max="2"`, `step="0.1"`, and `value="0.1"`.

- [ ] **Step 2: Run contract tests and verify RED**

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m unittest tests.test_frontend_settings_contract -v
```

Expected: FAIL because profile and temperature controls are absent.

- [ ] **Step 3: Add the toolbar and temperature UI**

Load `/static/settings-store.js` before `app.js`. Add a full-width provider toolbar above Base URL using the existing `s-input`, `s-select`, and button visual tokens. Add Font Awesome plus, save, and trash icons with tooltips and accessible labels. Add a hidden compact name row used only during profile creation. Place Temperature beside Model in the existing two-column grid. Extend the current mobile media query so toolbar actions wrap and the settings panel expands without clipping.

- [ ] **Step 4: Integrate profile behavior in `app.js`**

Replace the single-settings persistence methods with these controller responsibilities:

```javascript
_loadSettings()
_persistSettings()
_renderProfileOptions()
_captureActiveProfile()
_applyProfile(profileId, { fetchModels = true } = {})
_beginAddProfile()
_saveActiveProfile()
_deleteActiveProfile()
_restoreModelTemperature()
```

Before switching profiles, capture the current fields. On apply, restore URL/Key, rebuild the model selector, fetch models silently when credentials exist, restore `lastModel`, then restore its temperature. Model and temperature change events update `lastModel` and `modelTemperatures`. Preserve `summaryLang` globally. Add the current temperature to both URL and upload FormData.

- [ ] **Step 5: Run frontend tests and inspect responsive UI**

Run the Step 2 test and the Task 1 Node tests. Expected: all PASS. Start the app on an unused port and inspect AI Settings at desktop and 390px width, verifying no overlap, clipped text, nested cards, or control-induced layout shift.

- [ ] **Step 6: Commit the UI integration**

```powershell
git add -- static/index.html static/app.js tests/test_frontend_settings_contract.py
git commit -m "feat: add AI provider profile controls"
```

### Task 3: Centralized Backend Temperature

**Files:**
- Create: `backend/model_settings.py`
- Modify: `backend/summarizer.py`
- Modify: `backend/translator.py`
- Create: `tests/test_model_temperature.py`

**Interfaces:**
- Produces: `validate_temperature(value, default=0.1) -> float`, raising `ValueError` for non-finite or out-of-range values.
- Produces: `Summarizer(..., temperature: float = 0.1)`.
- Produces: `Translator(..., temperature: float = 0.1)`.
- Produces: `_create_completion(**kwargs)` on both classes, always applying `self.temperature`.

- [ ] **Step 1: Write failing validation and client-call tests**

Use Python unittest to assert default `0.1`, accepted `0`, `1`, and `2`, and rejection of `-0.1`, `2.1`, `nan`, `inf`, and non-numeric strings. Instantiate Summarizer and Translator with fake completion clients and assert `_create_completion(model="test", messages=[])` sends exactly the configured temperature `1`.

- [ ] **Step 2: Run backend tests and verify RED**

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m unittest tests.test_model_temperature -v
```

Expected: FAIL because `model_settings` and constructor temperature arguments do not exist.

- [ ] **Step 3: Implement validation and centralized calls**

Implement `validate_temperature` with `float(value)`, `math.isfinite`, and inclusive bounds. Store the validated float on each AI class. Add `_create_completion` wrappers that overwrite any incoming `temperature` with `self.temperature`, then call the OpenAI client. Replace every direct `self.client.chat.completions.create` in Summarizer and Translator with the wrapper and remove all hard-coded OpenAI request temperatures. Do not alter `backend/transcriber.py`.

- [ ] **Step 4: Verify centralized use**

Add a source contract assertion that all completion creation in each class goes through one wrapper and that no numeric `temperature=` literals remain in `summarizer.py` or `translator.py`.

- [ ] **Step 5: Run tests and verify GREEN**

Run the Step 2 command and complete Python unittest discovery. Expected: all PASS.

- [ ] **Step 6: Commit backend temperature support**

```powershell
git add -- backend/model_settings.py backend/summarizer.py backend/translator.py tests/test_model_temperature.py
git commit -m "feat: centralize model temperature"
```

### Task 4: Temperature Request Propagation And Final Verification

**Files:**
- Modify: `backend/main.py`
- Create: `tests/test_temperature_pipeline.py`

**Interfaces:**
- Consumes: `validate_temperature`, `Summarizer(..., temperature=...)`, and `Translator(..., temperature=...)` from Task 3.
- Extends: URL and upload processing functions with `temperature: float`.

- [ ] **Step 1: Write failing pipeline tests**

Test a small helper `_temperature_or_400(value)` for valid/default values and HTTP 400 on invalid input. Patch `Summarizer` and `Translator` constructors while calling the request-scoped setup path, and assert both receive the same validated temperature. Test both `_enqueue_upload_job` and URL task argument plumbing so `1` reaches `process_upload_task` and `process_video_task`.

- [ ] **Step 2: Run pipeline tests and verify RED**

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m unittest tests.test_temperature_pipeline -v
```

Expected: FAIL because request temperature is not accepted or propagated.

- [ ] **Step 3: Thread temperature through both pipelines**

Add `temperature: str = Form(default="0.1")` to `/api/process-video` and `/api/process-upload`, validate before creating a task, and pass the float through `_enqueue_upload_job`, `process_video_task`, `process_upload_task`, and `_run_post_extract_pipeline`. Construct request-scoped Summarizer and Translator with the same temperature. Keep global environment-backed instances at default `0.1`.

- [ ] **Step 4: Run all automated verification**

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m unittest discover -v
& 'C:\Users\gsgame\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test tests\settings-store.test.js
& '.\.venv\Scripts\python.exe' -m py_compile backend\main.py backend\summarizer.py backend\translator.py backend\model_settings.py
git diff --check
```

Expected: all tests PASS, compilation exits 0, and diff check is clean.

- [ ] **Step 5: Restart and browser-verify port 8001**

Restart from the main project virtual environment. Verify HTTP 200, profile creation and switching after reload, DeepSeek at `0.1`, Kimi `k3` at `1`, URL and upload FormData, and responsive layout. Confirm no API Key is printed in server logs.

- [ ] **Step 6: Commit request propagation**

```powershell
git add -- backend/main.py tests/test_temperature_pipeline.py
git commit -m "feat: propagate model temperature"
```

