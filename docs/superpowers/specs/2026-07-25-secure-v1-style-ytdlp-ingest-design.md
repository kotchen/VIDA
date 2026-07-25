# Secure v1-style yt-dlp ingest design

Date: 2026-07-25

## Problem

VIDA v2 resolves platform URLs by launching the `yt-dlp` CLI through an authenticated
loopback CONNECT proxy, reading `--dump-single-json`, and then downloading one
`document["url"]` with its own HTTP client.

Two incompatibilities prevent the submitted Bilibili URL from working:

1. the proxy URL contains a random username but an empty password, so current yt-dlp
   does not send `Proxy-Authorization` and the proxy returns 403;
2. Bilibili returns separate video and audio formats, required request headers, and no
   top-level media URL, while the v2 downloader assumes one `document["url"]`.

VIDA v1 avoids both issues by letting Python `yt_dlp.YoutubeDL` select and download the
media. Copying v1 literally would remove v2's SSRF protection and is out of scope.

## Goals

- Use the v1-style Python `yt_dlp.YoutubeDL` download path for supported platform
  pages.
- Let yt-dlp handle extractor-specific formats, split streams, redirects, cookies
  created during extraction, and required headers such as Bilibili `Referer`.
- Preserve the v2 URL, SSRF, resource, path, cancellation, and error-redaction
  boundaries.
- Make the submitted public Bilibili URL reach the existing media probe/transcription
  pipeline.

## Non-goals

- Do not restore v1's unrestricted direct network access.
- Do not add browser-cookie import, login, playlists, custom yt-dlp configuration,
  plugins, or arbitrary postprocessors.
- Do not change Provider Profile, transcription, summary, chapter, or frontend APIs.
- Do not expose raw yt-dlp output or signed media URLs to the client or logs.

## Design

### Authenticated SSRF proxy

Each proxy instance generates independent URL-safe random username and password
values. Its URL is:

```text
http://<username>:<password>@127.0.0.1:<ephemeral-port>
```

The CONNECT parser requires the exact Basic credential pair. Credentials stay in
memory, are never logged, and expire when the proxy context exits. A non-empty
password makes yt-dlp send `Proxy-Authorization`; all existing CONNECT target,
port, DNS, peer-pinning, rebinding, timeout, and byte-limit checks remain active.

### Python yt-dlp adapter

`YtDlpDownloader` will use `yt_dlp.YoutubeDL` inside the existing owned blocking-work
boundary instead of launching a metadata-only CLI subprocess.

The adapter:

- validates the submitted page URL before yt-dlp starts;
- opens one authenticated `SSRFProxy` for extraction and download;
- selects `bestaudio/best`, because VIDA transcribes audio and does not need split
  video output;
- uses a fixed output template inside the current job attempt directory;
- disables playlists, user config effects, plugins, cache, and console output;
- supplies the proxy URL, socket timeout, maximum file size, and bounded retry
  settings;
- uses a progress hook to check cooperative job cancellation and report bounded
  progress;
- performs no yt-dlp postprocessing or shell command execution;
- accepts only one regular, non-symlink output file with an existing safe media
  extension, then returns it to `SourceIngestor` for the existing FFprobe/FFmpeg
  validation;
- removes partial and unexpected files on failure or cancellation.

`run_owned_to_thread` owns the blocking Python call until it exits. Cancellation is
signaled through yt-dlp's progress hook; the async owner does not abandon a live
thread or its files.

### Plugin and environment isolation

V2 bootstrap disables yt-dlp plugin discovery once, before scheduler workers start;
request execution does not mutate process-global plugin state. The Python API does not
parse CLI configuration. The adapter does not pass application secrets, Provider
credentials, proxy environment variables, or user cookie stores to yt-dlp. Tests
verify bootstrap ordering and effective options rather than relying only on defaults.

### Errors and observability

Known extraction, download, cancellation, validation, and size failures are converted
to existing sanitized ingest errors. The scheduler continues returning stable public
error codes. Server logs may record the safe stage and exception type, but never raw
stderr, full query strings, proxy credentials, signed media URLs, headers, cookies, or
filesystem paths outside the existing safe conventions.

## Data flow

1. API stores a validated public HTTP(S) URL and enqueues the Episode.
2. `SourceIngestor` reports 5% and delegates the URL to `YtDlpDownloader`.
3. The downloader validates the page URL and starts the short-lived authenticated
   loopback proxy.
4. Python `YoutubeDL` extracts the page and downloads the selected audio through that
   proxy into the attempt directory.
5. The adapter validates and returns the single downloaded file.
6. Existing `SourceIngestor` probes media, creates a poster, and advances the normal
   transcription pipeline.
7. Any failure closes the proxy and removes the attempt directory.

## Testing

Unit and security tests will cover:

- username and password are both non-empty and are never included in error text;
- CONNECT without or with incorrect credentials is rejected;
- exact non-empty Basic credentials are accepted;
- yt-dlp receives the authenticated proxy and required safe options;
- Bilibili-style split-format metadata is handled by direct `YoutubeDL` download
  rather than a top-level `url` assumption;
- only one bounded regular output file is accepted;
- private targets, unsafe ports, redirects, DNS rebinding, oversized transfers,
  partial files, symlinks, cancellation, timeouts, plugins, and environment proxies
  remain rejected or cleaned up;
- existing source-ingest, secret-redaction, cancellation, recovery, and full backend
  suites remain green.

One opt-in local integration check may use the submitted public Bilibili URL, but the
automated suite must remain deterministic and must not require external network
access.

## Rollback

The change does not alter SQLite schema or persisted Episode fields. Rollback is a
code revert followed by a backend restart with the same `VIDA_PROFILE_MASTER_KEY`.
Existing failed Episodes remain valid records and can be retried after either version
is restored. No data migration or Provider credential rotation is required.
