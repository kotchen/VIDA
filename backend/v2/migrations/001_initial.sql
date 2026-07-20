BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    active_revision_id TEXT,
    deleted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (id, active_revision_id)
        REFERENCES provider_profile_revisions(profile_id, id)
);

CREATE TABLE IF NOT EXISTS provider_profile_revisions (
    id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    base_url TEXT NOT NULL,
    model_id TEXT NOT NULL,
    temperature REAL NOT NULL CHECK (temperature >= 0),
    encrypted_api_key TEXT NOT NULL,
    encryption_nonce TEXT NOT NULL,
    encryption_format_version INTEGER NOT NULL CHECK (encryption_format_version >= 1),
    created_at TEXT NOT NULL,
    FOREIGN KEY (profile_id) REFERENCES provider_profiles(id),
    UNIQUE (profile_id, id),
    UNIQUE (profile_id, version)
);

CREATE TABLE IF NOT EXISTS episodes (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('upload', 'url')),
    source_path TEXT,
    source_url TEXT,
    source_content_type TEXT,
    media_path TEXT,
    media_content_type TEXT,
    poster_path TEXT,
    poster_content_type TEXT,
    duration_sec REAL CHECK (duration_sec IS NULL OR duration_sec >= 0),
    resolution TEXT,
    status TEXT NOT NULL CHECK (status IN ('queued', 'processing', 'completed', 'failed', 'canceled')),
    language TEXT,
    summary_language TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    message TEXT NOT NULL,
    warnings_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(warnings_json) AND json_type(warnings_json) = 'array'),
    error_code TEXT,
    error_message TEXT,
    current_job_id TEXT,
    provider_profile_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY (id, current_job_id) REFERENCES jobs(episode_id, id),
    FOREIGN KEY (provider_profile_id) REFERENCES provider_profiles(id),
    CHECK (
        (source_type = 'upload' AND source_path IS NOT NULL AND source_url IS NULL)
        OR (source_type = 'url' AND source_url IS NOT NULL AND source_path IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('process_episode', 'regenerate_summary', 'regenerate_chapters')),
    attempt INTEGER NOT NULL CHECK (attempt >= 1),
    status TEXT NOT NULL CHECK (status IN ('queued', 'processing', 'completed', 'failed', 'canceled')),
    provider_profile_revision_id TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    cancel_requested_at TEXT,
    heartbeat_at TEXT,
    worker_id TEXT,
    progress INTEGER NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    message TEXT NOT NULL,
    error_code TEXT,
    error_message TEXT,
    FOREIGN KEY (episode_id) REFERENCES episodes(id) ON DELETE CASCADE,
    FOREIGN KEY (provider_profile_revision_id) REFERENCES provider_profile_revisions(id),
    UNIQUE (episode_id, id),
    UNIQUE (episode_id, attempt)
);

CREATE TABLE IF NOT EXISTS transcript_segments (
    id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    start_sec REAL NOT NULL CHECK (start_sec >= 0),
    end_sec REAL NOT NULL CHECK (end_sec >= start_sec),
    speaker TEXT,
    text TEXT NOT NULL,
    FOREIGN KEY (episode_id) REFERENCES episodes(id) ON DELETE CASCADE,
    UNIQUE (episode_id, ordinal)
);

CREATE TABLE IF NOT EXISTS summaries (
    episode_id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    read_time_min INTEGER NOT NULL CHECK (read_time_min >= 0),
    key_points TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(key_points) AND json_type(key_points) = 'array'),
    confidence REAL CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    generated_by TEXT NOT NULL,
    FOREIGN KEY (episode_id) REFERENCES episodes(id) ON DELETE CASCADE,
    FOREIGN KEY (generated_by) REFERENCES provider_profile_revisions(id)
);

CREATE TABLE IF NOT EXISTS chapters (
    id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL,
    start_sec REAL NOT NULL CHECK (start_sec >= 0),
    title TEXT NOT NULL,
    duration_sec REAL CHECK (duration_sec IS NULL OR duration_sec >= 0),
    thumbnail_path TEXT,
    thumbnail_content_type TEXT,
    bookmarked INTEGER NOT NULL DEFAULT 0 CHECK (bookmarked IN (0, 1)),
    source TEXT NOT NULL CHECK (source IN ('generated', 'manual')),
    created_at TEXT NOT NULL,
    FOREIGN KEY (episode_id) REFERENCES episodes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_provider_profile_revisions_profile
ON provider_profile_revisions(profile_id, version DESC);

CREATE INDEX IF NOT EXISTS idx_episodes_created_at
ON episodes(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_jobs_status_submitted_id
ON jobs(status, submitted_at, id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_jobs_one_active_per_episode
ON jobs(episode_id)
WHERE status IN ('queued', 'processing');

CREATE INDEX IF NOT EXISTS idx_transcript_segments_episode_ordinal
ON transcript_segments(episode_id, ordinal);

CREATE INDEX IF NOT EXISTS idx_chapters_episode_start
ON chapters(episode_id, start_sec);

INSERT OR IGNORE INTO schema_migrations(version, applied_at)
VALUES (1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

COMMIT;
