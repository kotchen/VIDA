# AI Provider Profiles And Temperature Design

## Goal

Extend AI Settings so users can save multiple provider connections, switch
between them without re-entering credentials, and persist a temperature for
each model. The implementation must preserve the existing compact visual style
and restart the local service after verification.

## Scope

This design covers browser-local provider profiles, model-specific temperature
settings, request propagation, backend validation, and OpenAI-compatible text
operations. It does not add user accounts, server-side secret storage, remote
profile sync, or changes to Faster-Whisper decoding temperatures.

## User Decisions

- Use the profile-toolbar layout at the top of AI Settings.
- A profile represents a provider connection, not one individual model.
- Each profile stores its API URL and API Key, plus its last selected model.
- Temperature is remembered per provider profile and model.
- New models default to `0.1`; model ID `k3` defaults to `1`.
- Browser-local plaintext API Key storage is accepted.
- UI styling must remain consistent with the current application.

## UI Design

AI Settings keeps its existing collapsible panel and `settings-card`. A compact
provider toolbar is added above the API URL field:

- Provider profile select.
- Add icon button.
- Save icon button.
- Delete icon button.

The buttons use the project's existing Font Awesome dependency and include
hover tooltips. The add action temporarily exposes a compact profile-name input
using the existing input styling. Delete requires confirmation and leaves at
least one blank default profile.

The existing API URL, API Key, Fetch, and Model controls remain in the same
grid. A numeric Temperature control is placed beside Model with these
constraints:

- Minimum: `0`
- Maximum: `2`
- Step: `0.1`
- General default: `0.1`
- First selection of model ID `k3`, case-insensitive: `1`

All new controls reuse the current CSS variables, border treatment, compact
type scale, control heights, spacing, and responsive one-column behavior. No
new page-level cards, color theme, oversized headings, or explanatory text are
introduced.

## Browser Storage

The existing `vt_settings` localStorage key becomes a versioned document:

```json
{
  "version": 2,
  "activeProfileId": "provider-id",
  "summaryLang": "zh",
  "profiles": [
    {
      "id": "provider-id",
      "name": "Kimi",
      "baseUrl": "https://api.kimi.com/coding/v1",
      "apiKey": "saved-api-key",
      "lastModel": "k3",
      "modelTemperatures": {
        "k3": 1
      }
    }
  ]
}
```

Profile IDs are generated locally and remain stable. Profile names are entered
when creating a profile. Switching profiles restores URL, API Key, fetched
model selection, and the active model's temperature. The current profile is
saved before switching so edits are not lost; the Save action also commits the
visible fields immediately.

When a model changes, the UI restores its saved value from
`modelTemperatures`. If no value exists, it initializes `k3` to `1` and every
other model to `0.1`. Manual temperature edits update the current model entry.

### Legacy Migration

If `vt_settings` contains the old single-profile shape, the loader creates one
profile named `Default` with the old `baseUrl`, `apiKey`, and `model` values.
Its selected model receives the appropriate default temperature. The existing
summary language remains global. Migration is written back as version 2 after
successful parsing. Invalid storage falls back to one blank profile without
throwing during page startup.

## Request And Backend Flow

Both URL processing and file upload append `temperature` to their existing
multipart requests. The backend adds a temperature form field with a default of
`0.1` and validates that it is a finite number in the inclusive range `0..2`.
Invalid values return HTTP 400 with an actionable message.

The validated temperature is passed through both processing paths into the
request-scoped `Summarizer` and `Translator`. All OpenAI-compatible transcript
optimization, translation, summary chunking, and final combination calls use
the request temperature instead of their current hard-coded values. This fixes
Kimi `k3`, which requires temperature `1`, while preserving `0.1` as the normal
default for other models.

The API Key and full profile document are never logged. Existing server-side
environment defaults remain available when no browser API Key is provided.

## Error Handling

- Invalid profile storage: create a blank default profile.
- Invalid API URL or API Key: retain the saved profile and show the existing
  model-fetch error status.
- Invalid temperature: reject before starting a background task.
- Deleted active profile: select the next profile or create a blank default.
- Missing selected model: keep the provider profile and use the server default
  model with temperature `0.1`.
- Provider model list failure: retain the last saved model ID and temperature
  so the user can retry without re-entering values.

## Testing

Frontend behavior tests cover:

- Legacy `vt_settings` migration.
- Creating, saving, switching, and deleting provider profiles.
- Restoring API URL, API Key, and last selected model.
- Per-model temperature restoration.
- `k3` defaulting to `1` and other models to `0.1`.
- URL and upload requests including temperature.

Backend tests cover:

- Default temperature `0.1`.
- Accepted boundary values `0` and `2`.
- Rejection of non-finite and out-of-range values.
- Temperature propagation into both `Summarizer` and `Translator`.
- Kimi-compatible value `1` reaching optimization, translation, and summary
  calls without being replaced by hard-coded temperatures.

After automated tests, restart port 8001 and manually verify one DeepSeek
profile at `0.1` and one Kimi `k3` profile at `1`, including switching away and
back after a page reload.

