CREATE TABLE IF NOT EXISTS file_items (
    file_item_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL UNIQUE,
    backup_job_id TEXT NOT NULL,
    backup_source_id TEXT NOT NULL,
    source_seq INTEGER NOT NULL,
    parent_folder_item_id TEXT NOT NULL DEFAULT '',
    local_path TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    display_name TEXT NOT NULL,
    path_sha256 TEXT NOT NULL,
    relative_path_sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    ctime_ns INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    atime_ns INTEGER NOT NULL,
    file_attrs INTEGER NOT NULL DEFAULT 0,
    quick_fingerprint TEXT NOT NULL,
    quick_sample_count INTEGER NOT NULL,
    quick_sample_size INTEGER NOT NULL,
    sample_plan_json TEXT NOT NULL,
    md5 TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    content_id TEXT NOT NULL,
    scan_status TEXT NOT NULL DEFAULT 'full_hashed',
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
    FOREIGN KEY(backup_source_id) REFERENCES backup_sources(backup_source_id) ON DELETE CASCADE,
    UNIQUE(backup_job_id, backup_source_id, relative_path),
    CHECK(source_seq >= 1),
    CHECK(size_bytes >= 0),
    CHECK(quick_sample_count >= 1),
    CHECK(quick_sample_size >= 0),
    CHECK(length(md5) = 32),
    CHECK(length(sha256) = 64),
    CHECK(length(content_id) = 64),
    CHECK(scan_status IN ('full_hashed', 'changed_during_scan')),
    CHECK(sync_status IN (
        'local_committed',
        'sync_pending',
        'syncing',
        'synced',
        'sync_conflict',
        'sync_failed_retryable'
    ))
);

CREATE INDEX IF NOT EXISTS idx_file_items_job_source
    ON file_items(backup_job_id, backup_source_id, relative_path);

CREATE INDEX IF NOT EXISTS idx_file_items_content_id
    ON file_items(content_id);

CREATE INDEX IF NOT EXISTS idx_file_items_sha256_size
    ON file_items(sha256, size_bytes);

CREATE TABLE IF NOT EXISTS folder_items (
    folder_item_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL UNIQUE,
    backup_job_id TEXT NOT NULL,
    backup_source_id TEXT NOT NULL,
    source_seq INTEGER NOT NULL,
    parent_folder_item_id TEXT NOT NULL DEFAULT '',
    local_path TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    display_name TEXT NOT NULL,
    path_sha256 TEXT NOT NULL,
    relative_path_sha256 TEXT NOT NULL,
    ctime_ns INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    atime_ns INTEGER NOT NULL,
    file_attrs INTEGER NOT NULL DEFAULT 0,
    child_file_count INTEGER NOT NULL DEFAULT 0,
    child_folder_count INTEGER NOT NULL DEFAULT 0,
    total_file_count INTEGER NOT NULL DEFAULT 0,
    total_folder_count INTEGER NOT NULL DEFAULT 0,
    folder_content_hash TEXT NOT NULL,
    folder_manifest_hash TEXT NOT NULL,
    scan_status TEXT NOT NULL DEFAULT 'scanned',
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
    FOREIGN KEY(backup_source_id) REFERENCES backup_sources(backup_source_id) ON DELETE CASCADE,
    UNIQUE(backup_job_id, backup_source_id, relative_path),
    CHECK(source_seq >= 1),
    CHECK(child_file_count >= 0),
    CHECK(child_folder_count >= 0),
    CHECK(total_file_count >= 0),
    CHECK(total_folder_count >= 0),
    CHECK(length(folder_content_hash) = 64),
    CHECK(length(folder_manifest_hash) = 64),
    CHECK(scan_status IN ('scanned', 'partial')),
    CHECK(sync_status IN (
        'local_committed',
        'sync_pending',
        'syncing',
        'synced',
        'sync_conflict',
        'sync_failed_retryable'
    ))
);

CREATE INDEX IF NOT EXISTS idx_folder_items_job_source
    ON folder_items(backup_job_id, backup_source_id, relative_path);

CREATE INDEX IF NOT EXISTS idx_folder_items_content_hash
    ON folder_items(folder_content_hash);

CREATE TABLE IF NOT EXISTS scan_issues (
    scan_issue_id TEXT PRIMARY KEY,
    backup_job_id TEXT NOT NULL,
    backup_source_id TEXT NOT NULL,
    local_path TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    display_name TEXT NOT NULL,
    path_sha256 TEXT NOT NULL,
    issue_type TEXT NOT NULL,
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY(backup_job_id) REFERENCES backup_jobs(backup_job_id) ON DELETE CASCADE,
    FOREIGN KEY(backup_source_id) REFERENCES backup_sources(backup_source_id) ON DELETE CASCADE,
    CHECK(issue_type IN (
        'missing_source',
        'skipped_symlink',
        'skipped_junction',
        'skipped_shortcut',
        'unreadable_file',
        'unreadable_directory',
        'unsupported_source'
    ))
);

CREATE INDEX IF NOT EXISTS idx_scan_issues_job_source
    ON scan_issues(backup_job_id, backup_source_id, issue_type);
