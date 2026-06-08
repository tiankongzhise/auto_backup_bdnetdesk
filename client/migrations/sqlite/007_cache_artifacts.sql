CREATE TABLE IF NOT EXISTS cache_artifacts (
    artifact_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL DEFAULT '',
    artifact_type TEXT NOT NULL,
    artifact_path TEXT NOT NULL UNIQUE,
    path_sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    required_until_stage TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL DEFAULT 'active',
    deletable INTEGER NOT NULL DEFAULT 1,
    remote_confirmed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    last_accessed_at TEXT NOT NULL,
    deleted_at TEXT,
    CHECK(size_bytes >= 0),
    CHECK(deletable IN (0, 1)),
    CHECK(remote_confirmed IN (0, 1)),
    CHECK(artifact_type IN (
        'archive',
        'manifest_plain',
        'staging',
        'verify',
        'upload_temp',
        'download',
        'restore',
        'tmp'
    )),
    CHECK(lifecycle_status IN ('active', 'deleted', 'missing')),
    CHECK(required_until_stage IN (
        'packaged',
        'verified',
        'uploaded',
        'remote_confirmed',
        'completed',
        'strict_verified',
        'restore_completed'
    ))
);

CREATE INDEX IF NOT EXISTS idx_cache_artifacts_job
    ON cache_artifacts(job_id, artifact_type);

CREATE INDEX IF NOT EXISTS idx_cache_artifacts_status
    ON cache_artifacts(lifecycle_status, deletable, required_until_stage);
