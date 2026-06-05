CREATE TABLE IF NOT EXISTS upload_sessions (
    upload_session_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL UNIQUE,
    job_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    account_id TEXT NOT NULL DEFAULT '',
    archive_id TEXT NOT NULL,
    archive_seq INTEGER NOT NULL,
    archive_sha256 TEXT NOT NULL,
    archive_md5 TEXT NOT NULL,
    archive_size INTEGER NOT NULL,
    archive_type TEXT NOT NULL,
    local_archive_path TEXT NOT NULL,
    remote_archive_path TEXT NOT NULL UNIQUE,
    remote_meta_path TEXT NOT NULL,
    remote_job_index_path TEXT NOT NULL,
    part_size INTEGER NOT NULL,
    total_parts INTEGER NOT NULL,
    block_md5s_json TEXT NOT NULL,
    uploadid TEXT NOT NULL DEFAULT '',
    upload_status TEXT NOT NULL DEFAULT 'planned',
    meta_status TEXT NOT NULL DEFAULT 'pending',
    job_index_status TEXT NOT NULL DEFAULT 'pending',
    fs_id INTEGER,
    remote_md5 TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
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
    CHECK(archive_seq >= 1),
    CHECK(archive_size >= 0),
    CHECK(part_size >= 4194304),
    CHECK(total_parts >= 1),
    CHECK(upload_status IN (
        'planned',
        'precreated',
        'uploading',
        'parts_uploaded',
        'remote_created',
        'failed_retryable',
        'failed_terminal'
    )),
    CHECK(meta_status IN ('pending', 'uploaded', 'failed_retryable', 'failed_terminal')),
    CHECK(job_index_status IN ('pending', 'uploaded', 'failed_retryable', 'failed_terminal')),
    CHECK(sync_status IN (
        'local_committed',
        'sync_pending',
        'syncing',
        'synced',
        'sync_conflict',
        'sync_failed_retryable'
    ))
);

CREATE INDEX IF NOT EXISTS idx_upload_sessions_job
    ON upload_sessions(job_id, archive_seq);

CREATE INDEX IF NOT EXISTS idx_upload_sessions_status
    ON upload_sessions(upload_status, updated_at);

CREATE INDEX IF NOT EXISTS idx_upload_sessions_archive_sha256
    ON upload_sessions(archive_sha256);

CREATE TABLE IF NOT EXISTS upload_parts (
    upload_part_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL UNIQUE,
    upload_session_id TEXT NOT NULL,
    partseq INTEGER NOT NULL,
    offset INTEGER NOT NULL,
    size INTEGER NOT NULL,
    md5 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    uploaded_at TEXT,
    confirmed_at TEXT,
    last_error TEXT NOT NULL DEFAULT '',
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
    FOREIGN KEY(upload_session_id) REFERENCES upload_sessions(upload_session_id) ON DELETE CASCADE,
    UNIQUE(upload_session_id, partseq),
    CHECK(partseq >= 0),
    CHECK(offset >= 0),
    CHECK(size >= 0),
    CHECK(attempt_count >= 0),
    CHECK(status IN (
        'pending',
        'uploading',
        'uploaded',
        'confirmed',
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

CREATE INDEX IF NOT EXISTS idx_upload_parts_session_status
    ON upload_parts(upload_session_id, status, partseq);

CREATE TABLE IF NOT EXISTS remote_objects (
    remote_object_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL UNIQUE,
    object_type TEXT NOT NULL,
    job_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    archive_id TEXT NOT NULL DEFAULT '',
    archive_sha256 TEXT NOT NULL DEFAULT '',
    remote_path TEXT NOT NULL UNIQUE,
    size_bytes INTEGER NOT NULL,
    md5 TEXT NOT NULL DEFAULT '',
    sha256 TEXT NOT NULL DEFAULT '',
    fs_id INTEGER,
    status TEXT NOT NULL DEFAULT 'remote_created',
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
    CHECK(size_bytes >= 0),
    CHECK(object_type IN ('archive', 'archive_meta', 'job_index')),
    CHECK(status IN ('remote_created', 'remote_missing', 'remote_mismatch', 'deleted')),
    CHECK(sync_status IN (
        'local_committed',
        'sync_pending',
        'syncing',
        'synced',
        'sync_conflict',
        'sync_failed_retryable'
    ))
);

CREATE INDEX IF NOT EXISTS idx_remote_objects_job
    ON remote_objects(job_id, object_type);

CREATE INDEX IF NOT EXISTS idx_remote_objects_archive_sha256
    ON remote_objects(archive_sha256);
