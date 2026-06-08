ALTER TABLE file_items
    ADD COLUMN file_volume_serial TEXT NOT NULL DEFAULT '';

ALTER TABLE file_items
    ADD COLUMN file_index TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS source_cleanup_records (
    source_cleanup_record_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL UNIQUE,
    backup_job_id TEXT NOT NULL,
    content_reference_id TEXT NOT NULL,
    file_item_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    original_path TEXT NOT NULL,
    original_path_sha256 TEXT NOT NULL,
    display_name TEXT NOT NULL,
    cleanup_status TEXT NOT NULL,
    cleanup_method TEXT NOT NULL,
    cleanup_operator TEXT NOT NULL,
    cleanup_time TEXT,
    pre_cleanup_size INTEGER NOT NULL,
    pre_cleanup_sha256 TEXT NOT NULL,
    pre_cleanup_mtime_ns INTEGER NOT NULL,
    pre_cleanup_volume_serial TEXT NOT NULL,
    pre_cleanup_file_index TEXT NOT NULL,
    observed_size INTEGER,
    observed_mtime_ns INTEGER,
    observed_volume_serial TEXT NOT NULL DEFAULT '',
    observed_file_index TEXT NOT NULL DEFAULT '',
    quarantine_path TEXT NOT NULL DEFAULT '',
    quarantine_path_sha256 TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
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
    FOREIGN KEY(backup_job_id) REFERENCES backup_jobs(backup_job_id) ON DELETE CASCADE,
    FOREIGN KEY(content_reference_id) REFERENCES content_references(content_reference_id) ON DELETE CASCADE,
    FOREIGN KEY(file_item_id) REFERENCES file_items(file_item_id) ON DELETE CASCADE,
    CHECK(pre_cleanup_size >= 0),
    CHECK(observed_size IS NULL OR observed_size >= 0),
    CHECK(length(pre_cleanup_sha256) = 64),
    CHECK(cleanup_status IN (
        'requested',
        'moved_to_recycle_bin',
        'moved_to_quarantine',
        'permanently_deleted',
        'failed'
    )),
    CHECK(cleanup_method IN ('recycle_bin', 'quarantine', 'permanent_delete')),
    CHECK(sync_status IN (
        'local_committed',
        'sync_pending',
        'syncing',
        'synced',
        'sync_conflict',
        'sync_failed_retryable'
    ))
);

CREATE INDEX IF NOT EXISTS idx_source_cleanup_records_job
    ON source_cleanup_records(backup_job_id, cleanup_status, updated_at);

CREATE INDEX IF NOT EXISTS idx_source_cleanup_records_reference
    ON source_cleanup_records(content_reference_id, data_version);
