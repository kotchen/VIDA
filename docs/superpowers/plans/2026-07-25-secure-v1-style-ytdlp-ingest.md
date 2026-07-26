# Secure v1-style yt-dlp ingest implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make v2 platform URL Episodes use Python `yt_dlp.YoutubeDL` to download audio through the existing SSRF boundary, including Bilibili split-format media.

**Architecture:** Keep `SourceIngestor`, `SecureDownloader`, and `SSRFProxy` as the security and media-validation boundaries. Change the proxy to short-lived random non-empty Basic credentials, then replace the metadata-only yt-dlp CLI adapter with an owned Python `YoutubeDL` call that selects and downloads one audio file into the current attempt directory. The existing FFprobe/FFmpeg pipeline remains responsible for validating and normalizing the downloaded file.

**Tech Stack:** Python 3.12, asyncio, yt-dlp Python API, stdlib `unittest`, FastAPI v2 job pipeline.

## Global constraints

- Preserve `backend/v2/AGENTS.md`: yt-dlp network access must remain behind the controlled SSRF proxy.
- Reject credentials URLs, unsafe schemes/ports, private or non-public IPs, DNS rebinding, unsafe redirects, symlink/path escape, oversized transfers, playlists, configuration files, and plugins.
- Do not log or return proxy credentials, signed media URLs, raw upstream errors, cookies, headers, API keys, or master keys.
- Keep one regular downloaded file inside the supplied attempt directory; cleanup owns all partial and unexpected files.
- Do not add a database migration or change the public v2 API contract.
- Use TDD for every behavior change and run the full backend suite before completion.

---

## File map

- Modify `backend/v2/jobs/source_ingest.py`: authenticated proxy credentials and Python yt-dlp adapter.
- Modify `backend/v2/bootstrap.py`: construct the new adapter and disable plugin discovery before workers start.
- Modify `tests/v2/test_source_ingest_security.py`: proxy, adapter, split-format, cleanup, cancellation, and redaction regression tests.
- Modify `tests/v2/test_bootstrap_config.py`: bootstrap plugin-isolation and adapter wiring test.
- Modify `docs/devnote/2026-07-0725/provider-model-discovery.md`: record the URL ingest fix, lessons, verification, and rollback.

### Task 1: Non-empty ephemeral proxy credentials

**Files:**
- Modify: `backend/v2/jobs/source_ingest.py:678-707`
- Modify: `backend/v2/jobs/source_ingest.py:932-962`
- Test: `tests/v2/test_source_ingest_security.py:250-455`

**Interfaces:**
- Consumes: `_parse_connect_request(header: bytes, username: str, password: str) -> tuple[str, int]`
- Produces: `SSRFProxy.proxy_url` containing non-empty URL-safe username and password; exact Basic authentication accepted by `_parse_connect_request`.

- [ ] **Step 1: Write failing parser and loopback tests**

Update `ControlledProxyTests` so the parser receives both credentials:

```python
def test_connect_authority_and_headers_are_strict(self):
    username, password = "user-token", "password-token"
    auth = base64.b64encode(f"{username}:{password}".encode()).decode()
    request = (
        "CONNECT example.com:443 HTTP/1.1\r\n"
        f"Proxy-Authorization: Basic {auth}\r\n"
        "Host: example.com:443\r\n\r\n"
    ).encode()
    self.assertEqual(
        source_ingest._parse_connect_request(
            request, username, password
        ),
        ("example.com", 443),
    )
```

In the real-loopback test, assert `urlsplit(proxy.proxy_url).username` and
`.password` are both non-empty, missing/wrong credentials receive 403, and the exact
pair receives 200. Assert neither credential appears in a raised `DownloadError`.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
source venv/bin/activate
python -m unittest \
  tests.v2.test_source_ingest_security.ControlledProxyTests.test_connect_authority_and_headers_are_strict \
  tests.v2.test_source_ingest_security.ControlledProxyTests.test_real_loopback_rejects_auth_and_shutdown_joins_active_handler -v
```

Expected: FAIL because `_parse_connect_request` accepts one token and the generated
proxy password is empty.

- [ ] **Step 3: Implement non-empty credentials**

Change proxy state and parser calls:

```python
class SSRFProxy:
    def __init__(...):
        ...
        self._username = secrets.token_urlsafe(24)
        self._password = secrets.token_urlsafe(24)

    async def __aenter__(self):
        ...
        self.proxy_url = (
            f"http://{self._username}:{self._password}@127.0.0.1:{port}"
        )

    async def _accept(self, reader, writer):
        ...
        host, port = _parse_connect_request(
            header, self._username, self._password
        )
```

Update the parser:

```python
def _parse_connect_request(
    header: bytes, username: str, password: str
) -> tuple[str, int]:
    ...
    expected = b"Basic " + base64.b64encode(
        f"{username}:{password}".encode("ascii")
    )
    if not secrets.compare_digest(
        fields.get(b"proxy-authorization", b""), expected
    ):
        raise DownloadError("Proxy authentication failed")
```

Keep credentials URL-safe so no percent-decoding ambiguity is introduced.

- [ ] **Step 4: Run focused proxy security tests**

Run:

```bash
source venv/bin/activate
python -m unittest tests.v2.test_source_ingest_security.ControlledProxyTests -v
```

Expected: all controlled proxy tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/v2/jobs/source_ingest.py tests/v2/test_source_ingest_security.py
git commit -m "fix(v2): authenticate yt-dlp proxy"
```

### Task 2: Python YoutubeDL audio acquisition

**Files:**
- Modify: `backend/v2/jobs/source_ingest.py:854-905`
- Modify: `tests/v2/test_source_ingest_security.py:145-249`

**Interfaces:**
- Consumes:
  - `YtDlpDownloader(validator, *, max_bytes=5 * 1024**3, timeout_sec=120.0, proxy_factory=None, youtube_dl_factory=None, thread_runner=run_owned_to_thread)`
  - `URLValidator.validate_url(url, cancel_check) -> Awaitable[None]`
  - `SSRFProxy.proxy_url`
- Produces:
  - `YtDlpDownloader.download(url, directory, cancel_check, progress=None) -> Awaitable[Path]`
  - one regular safe media file inside `directory`

- [ ] **Step 1: Replace the old CLI test with a failing Python API test**

Define a fake factory that captures options and emulates Bilibili split formats without
a top-level `url`:

```python
class FakeYoutubeDL:
    def __init__(self, options):
        captured.update(options)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def extract_info(self, url, download):
        self.params["progress_hooks"][0](
            {"status": "downloading", "downloaded_bytes": 4, "total_bytes": 8}
        )
        (target / "media.m4a").write_bytes(b"audio")
        return {
            "requested_formats": [
                {"vcodec": "h264", "acodec": "none"},
                {"vcodec": "none", "acodec": "aac"},
            ],
            "http_headers": {"Referer": url},
        }
```

The test must assert:

```python
self.assertEqual(captured["format"], "bestaudio/best")
self.assertEqual(captured["proxy"], proxy.proxy_url)
self.assertEqual(captured["outtmpl"], str(target / "media.%(ext)s"))
self.assertTrue(captured["noplaylist"])
self.assertFalse(captured["cachedir"])
self.assertEqual(path, target / "media.m4a")
```

Also assert the submitted page is validated before the factory is constructed and the
proxy enters/exits exactly once.

- [ ] **Step 2: Add failing output and cancellation tests**

Add separate tests for:

- two final media files -> sanitized `DownloadError`;
- `.part`, `.ytdl`, symlink, directory, or unsupported extension -> rejected and
  cleaned;
- yt-dlp exception text containing a signed URL or proxy credential -> public
  exception contains neither;
- `cancel_check()` becoming true inside the progress hook -> `JobCanceled`;
- `progress` receives only bounded integer updates and cannot exceed the downloader's
  assigned range;
- unknown platform page -> rejected before `YoutubeDL` construction.

Use a dedicated `TemporaryDirectory` per case and never make network requests.

- [ ] **Step 3: Run adapter tests and verify RED**

Run:

```bash
source venv/bin/activate
python -m unittest tests.v2.test_source_ingest_security.YtDlpAcquisitionTests -v
```

Expected: FAIL because the current adapter requires a process runner, runs
`--dump-single-json`, and expects `document["url"]`.

- [ ] **Step 4: Implement the Python adapter**

Import `yt_dlp` and its public download exception type. Replace the constructor with
the interface above. Keep the existing platform host allowlist and page validation.

The async boundary is:

```python
async def download(self, url, directory, cancel_check, progress=None):
    parsed, _ = _validated_remote_url(url)
    self._require_supported_host(parsed.hostname)
    await self._validator.validate_url(url, cancel_check)
    target = Path(directory)
    await run_owned_to_thread(target.mkdir, parents=True, exist_ok=True)
    async with self._proxy_factory(cancel_check) as proxy:
        try:
            await self._thread_runner(
                self._download_sync,
                url,
                target,
                proxy.proxy_url,
                cancel_check,
                progress,
            )
        except JobCanceled:
            raise
        except Exception:
            raise DownloadError("Unable to download media page") from None
    return await self._validate_single_output(target)
```

The synchronous method uses:

```python
options = {
    "format": "bestaudio/best",
    "outtmpl": str(directory / "media.%(ext)s"),
    "proxy": proxy_url,
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "cachedir": False,
    "socket_timeout": 15,
    "max_filesize": self._max_bytes,
    "retries": 1,
    "fragment_retries": 1,
    "extractor_retries": 1,
    "progress_hooks": [progress_hook],
}
with self._youtube_dl_factory(options) as ydl:
    ydl.extract_info(url, download=True)
```

The progress hook checks cancellation before reporting progress. Do not configure
postprocessors, shell commands, cookies, configuration locations, or output names
derived from remote metadata.

Output validation must resolve every child, reject symlinks and any path escaping the
target directory, remove temporary/unexpected entries, and accept exactly one file
whose suffix exists in `_SAFE_INPUTS`.

- [ ] **Step 5: Run adapter and existing source-ingest tests**

Run:

```bash
source venv/bin/activate
python -m unittest \
  tests.v2.test_source_ingest_security.YtDlpAcquisitionTests \
  tests.v2.test_source_ingest -v
```

Expected: all selected tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/v2/jobs/source_ingest.py tests/v2/test_source_ingest_security.py
git commit -m "fix(v2): download platform audio with yt-dlp"
```

### Task 3: Bootstrap isolation and security regression

**Files:**
- Modify: `backend/v2/bootstrap.py:1-125`
- Modify: `backend/v2/bootstrap.py:135-155`
- Modify: `tests/v2/test_bootstrap_config.py`
- Modify: `tests/v2/test_source_ingest_security.py`

**Interfaces:**
- Consumes: `YtDlpDownloader(validator, *, max_bytes=...)`
- Produces: `_disable_yt_dlp_plugins() -> None`, called before job workers are
  constructed.

- [ ] **Step 1: Write failing bootstrap tests**

Patch `yt_dlp.globals.plugin_dirs.value` to `["default"]`, call the runtime builder,
and assert it becomes `[]` before a patched `YtDlpDownloader` is constructed:

```python
observed = []

class Downloader:
    def __init__(self, validator, **kwargs):
        observed.append(tuple(plugin_dirs.value))

with patch("backend.v2.bootstrap.YtDlpDownloader", Downloader):
    runtime = build_test_runtime(data_dir, master_key)

self.assertEqual(observed, [()])
runtime.close()
```

Update constructor assertions to prove the same `SecureDownloader` remains the page
validator, while the old `ProcessRunner` and secondary media downloader are no longer
passed to `YtDlpDownloader`.

- [ ] **Step 2: Run bootstrap tests and verify RED**

Run:

```bash
source venv/bin/activate
python -m unittest tests.v2.test_bootstrap_config -v
```

Expected: FAIL because plugin isolation and the new constructor wiring do not exist.

- [ ] **Step 3: Implement bootstrap isolation and wiring**

Add:

```python
def _disable_yt_dlp_plugins() -> None:
    from yt_dlp.globals import plugin_dirs
    plugin_dirs.value = []
```

Call it in `_build_runtime` before constructing `YtDlpDownloader`. Wire:

```python
page_downloader = YtDlpDownloader(
    secure_downloader,
    max_bytes=settings.upload_max_bytes,
)
```

Do not mutate plugin globals from request threads.

- [ ] **Step 4: Run security and recovery suites**

Run:

```bash
source venv/bin/activate
python -m unittest \
  tests.v2.test_bootstrap_config \
  tests.v2.test_source_ingest_security \
  tests.v2.test_secret_redaction \
  tests.v2.test_job_cancellation \
  tests.v2.test_job_recovery -v
```

Expected: all selected suites PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/v2/bootstrap.py tests/v2/test_bootstrap_config.py tests/v2/test_source_ingest_security.py
git commit -m "fix(v2): isolate Python yt-dlp runtime"
```

### Task 4: Devnote, full verification, and live Bilibili check

**Files:**
- Modify: `docs/devnote/2026-07-0725/provider-model-discovery.md`

**Interfaces:**
- Consumes: completed proxy and downloader behavior.
- Produces: repository-required development record and verified running service.

- [ ] **Step 1: Update the existing same-topic devnote**

Add to all three required sections:

1. `开发内容`: describe the proxy authentication bug, split-format incompatibility,
   Python `YoutubeDL` adapter, security boundaries, tests, and actual verification.
2. `学习与可沉淀经验`: record that empty Basic passwords are not portable, extractor
   metadata cannot be assumed to contain one URL, and Python interpreter selection is
   separate from CLI PATH activation.
3. `回滚操作`: identify the new commit range, state there is no migration, preserve
   `VIDA_PROFILE_MASTER_KEY`, restart the backend, and retry only failed Episodes.

Do not record the submitted media's signed URLs, Provider values, or credentials.

- [ ] **Step 2: Run documentation and diff checks**

Run:

```bash
git diff --check
rg -n "SSRF|YoutubeDL|回滚" \
  docs/devnote/2026-07-0725/provider-model-discovery.md \
  docs/superpowers/specs/2026-07-25-secure-v1-style-ytdlp-ingest-design.md
```

Expected: no whitespace errors and all required topics are present.

- [ ] **Step 3: Run the complete backend suite**

Run:

```bash
source venv/bin/activate
python -c "import tempfile, unittest; tempfile.tempdir='/private/tmp'; suite=unittest.defaultTestLoader.discover('tests'); result=unittest.TextTestRunner(verbosity=1).run(suite); raise SystemExit(not result.wasSuccessful())"
```

Expected: all tests PASS, with only intentional skips.

- [ ] **Step 4: Commit the devnote**

```bash
git add docs/devnote/2026-07-0725/provider-model-discovery.md
git commit -m "docs: record secure yt-dlp ingest"
```

- [ ] **Step 5: Restart the backend safely**

Before restart, verify Provider Profile count. Preserve the current
`VIDA_PROFILE_MASTER_KEY`; do not generate a new key when profiles exist.

Start from the repository root:

```bash
source venv/bin/activate
set -a
source .env
set +a
python start.py --prod
```

If no persistent `.env` exists but the running process owns the only usable key,
inherit that key without printing it, as documented in the current operational
procedure. Confirm `command -v yt-dlp` resolves inside `venv/bin`.

- [ ] **Step 6: Run a live Bilibili integration check**

Use the existing failed Episode retry endpoint or create one URL Episode for:

```text
https://www.bilibili.com/video/BV1fp4y1X7JJ/
```

This external request is authorized by the user's submitted URL. Observe until the
job passes the 5% acquisition boundary and reaches at least media probing/transcription,
or reaches a new terminal error. Never print signed media URLs or request headers.

Verify:

```bash
curl --fail http://127.0.0.1:8000/api/v2/dashboard
curl --fail http://127.0.0.1:7100/api/v2/dashboard
```

Expected: both endpoints return JSON and the Bilibili task no longer fails with proxy
403 or missing top-level URL.

- [ ] **Step 7: Apply completion gates**

Use `superpowers:verification-before-completion`, then
`superpowers:finishing-a-development-branch`. Report the exact test totals, live-check
result, known limitations, service URLs, and whether changes remain local or were
pushed.
