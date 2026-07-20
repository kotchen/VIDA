BEGIN IMMEDIATE;

ALTER TABLE summaries RENAME TO summaries_v1;

CREATE TABLE summaries (
    episode_id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    read_time_min INTEGER NOT NULL CHECK (read_time_min >= 0),
    key_points INTEGER NOT NULL DEFAULT 0
        CHECK (typeof(key_points) = 'integer' AND key_points >= 0),
    confidence INTEGER NOT NULL
        CHECK (typeof(confidence) = 'integer' AND confidence BETWEEN 0 AND 100),
    generated_by TEXT NOT NULL CHECK (generated_by = 'VIDA'),
    FOREIGN KEY (episode_id) REFERENCES episodes(id) ON DELETE CASCADE
);

INSERT INTO summaries(
    episode_id, content, read_time_min, key_points, confidence, generated_by
)
SELECT
    episode_id,
    content,
    CAST(read_time_min AS INTEGER),
    CASE
        WHEN typeof(key_points) = 'integer' THEN MAX(key_points, 0)
        ELSE json_array_length(key_points)
    END,
    CASE
        WHEN confidence IS NULL THEN 0
        WHEN typeof(confidence) = 'integer' THEN MIN(MAX(confidence, 0), 100)
        ELSE MIN(MAX(CAST(ROUND(confidence * 100) AS INTEGER), 0), 100)
    END,
    'VIDA'
FROM summaries_v1;

DROP TABLE summaries_v1;

INSERT INTO schema_migrations(version, applied_at)
VALUES (2, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

COMMIT;
