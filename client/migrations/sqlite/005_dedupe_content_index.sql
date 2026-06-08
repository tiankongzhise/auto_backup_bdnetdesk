CREATE TABLE IF NOT EXISTS content_objects (
    content_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL UNIQUE,
    file_sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    md5 TEXT NOT NULL DEFAULT '',
    reference_count INTEGER NOT NULL DEFAULT 0,
    payload_reference_count INTEGER NOT NULL DEFAULT 0,
    duplicate_reference_count INTEGER NOT NULL DEFAULT 0,
    cloud_candidate_status TEXT NOT NULL DEFAULT 'not_checked',
    cloud_latest_entity_id TEXT NOT NULL DEFAULT '',
    cloud_checked_at TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
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
    CHECK(reference_count >= 0),
    CHECK(payload_reference_count >= 0),
    CHECK(duplicate_reference_count >= 0),
    CHECK(length(content_id) = 64),
    CHECK(length(file_sha256) = 64),
    CHECK(md5 = '' OR length(md5) = 32),
    CHECK(cloud_candidate_status IN (
        'not_checked',
        'missing',
        'cloud_duplicate_candidate',
        'hash_mismatch',
        'retryable_error'
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

CREATE UNIQUE INDEX IF NOT EXISTS idx_content_objects_sha256_size
    ON content_objects(file_sha256, size_bytes);

CREATE INDEX IF NOT EXISTS idx_content_objects_cloud_candidate
    ON content_objects(cloud_candidate_status, last_seen_at);

CREATE TABLE IF NOT EXISTS content_references (
    content_reference_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL UNIQUE,
    content_id TEXT NOT NULL,
    file_item_id TEXT NOT NULL UNIQUE,
    backup_job_id TEXT NOT NULL,
    backup_source_id TEXT NOT NULL,
    source_seq INTEGER NOT NULL,
    device_id TEXT NOT NULL,
    local_path TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    display_name TEXT NOT NULL,
    path_sha256 TEXT NOT NULL,
    relative_path_sha256 TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    md5 TEXT NOT NULL DEFAULT '',
    reference_role TEXT NOT NULL,
    dedupe_status TEXT NOT NULL,
    archive_id TEXT NOT NULL DEFAULT '',
    archive_sha256 TEXT NOT NULL DEFAULT '',
    archive_member_path TEXT NOT NULL DEFAULT '',
    cleanup_status TEXT NOT NULL DEFAULT 'not_cleaned',
    restore_status TEXT NOT NULL DEFAULT 'not_restored',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by_device_id TEXT NOT NULL,
    FOREIGN KEY(content_id) REFERENCES content_objects(content_id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(file_item_id) REFERENCES file_items(file_item_id) ON DELETE CASCADE,
    FOREIGN KEY(backup_job_id) REFERENCES backup_jobs(backup_job_id) ON DELETE CASCADE,
    FOREIGN KEY(backup_source_id) REFERENCES backup_sources(backup_source_id) ON DELETE CASCADE,
    CHECK(source_seq >= 1),
    CHECK(size_bytes >= 0),
    CHECK(length(content_id) = 64),
    CHECK(length(file_sha256) = 64),
    CHECK(md5 = '' OR length(md5) = 32),
    CHECK(archive_sha256 = '' OR length(archive_sha256) = 64),
    CHECK(reference_role IN ('payload_source', 'local_duplicate', 'cloud_duplicate_candidate')),
    CHECK(dedupe_status IN (
        'needs_payload',
        'local_duplicate',
        'cloud_duplicate_candidate',
        'archive_assigned'
    )),
    CHECK(cleanup_status IN ('not_cleaned', 'cleanup_pending', 'cleaned', 'cleanup_failed')),
    CHECK(restore_status IN ('not_restored', 'restore_pending', 'restored', 'restore_failed'))
);

CREATE INDEX IF NOT EXISTS idx_content_references_content
    ON content_references(content_id, backup_job_id);

CREATE INDEX IF NOT EXISTS idx_content_references_job
    ON content_references(backup_job_id, source_seq, relative_path);

CREATE INDEX IF NOT EXISTS idx_content_references_dedupe
    ON content_references(dedupe_status, backup_job_id);
