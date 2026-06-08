CREATE TABLE IF NOT EXISTS backup_jobs (
    backup_job_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL UNIQUE,
    device_id TEXT NOT NULL,
    job_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    source_count INTEGER NOT NULL,
    started_at TEXT,
    paused_at TEXT,
    canceled_at TEXT,
    completed_at TEXT,
    schema_version INTEGER NOT NULL,
    data_version INTEGER NOT NULL,
    revision_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by_device_id TEXT NOT NULL,
    sync_status TEXT NOT NULL DEFAULT 'sync_pending',
    deleted_at TEXT,
    canonical_record_sha256 TEXT NOT NULL,
    last_synced_revision_id TEXT,
    created_at TEXT NOT NULL,
    CHECK(source_count >= 1),
    CHECK(status IN (
        'queued',
        'running',
        'paused',
        'canceled',
        'completed',
        'failed_retryable',
        'failed_terminal'
    )),
    CHECK(sync_status IN (
        'local_committed',
        'sync_pending',
        'syncing',
        'synced',
        'sync_conflict',
        'sync_failed_retryable'
    ))
);

CREATE INDEX IF NOT EXISTS idx_backup_jobs_status_updated
    ON backup_jobs(status, updated_at);

CREATE TABLE IF NOT EXISTS backup_sources (
    backup_source_id TEXT PRIMARY KEY,
    backup_job_id TEXT NOT NULL,
    source_seq INTEGER NOT NULL,
    source_type TEXT NOT NULL,
    local_path TEXT NOT NULL,
    display_name TEXT NOT NULL,
    path_sha256 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(backup_job_id) REFERENCES backup_jobs(backup_job_id) ON DELETE CASCADE,
    UNIQUE(backup_job_id, local_path),
    UNIQUE(backup_job_id, source_seq),
    CHECK(source_seq >= 1),
    CHECK(source_type IN ('file', 'directory')),
    CHECK(status IN ('pending', 'ready', 'missing', 'unreadable', 'removed'))
);

CREATE INDEX IF NOT EXISTS idx_backup_sources_job
    ON backup_sources(backup_job_id, source_seq);
