CREATE TABLE IF NOT EXISTS archives (
    archive_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL UNIQUE,
    job_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    archive_seq INTEGER NOT NULL,
    archive_sha256 TEXT NOT NULL,
    archive_md5 TEXT NOT NULL,
    archive_size INTEGER NOT NULL,
    archive_type TEXT NOT NULL,
    manifest_id TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    manifest_size INTEGER NOT NULL,
    manifest_item_count INTEGER NOT NULL,
    payload_member_count INTEGER NOT NULL,
    reference_member_count INTEGER NOT NULL,
    local_archive_path TEXT NOT NULL,
    remote_path TEXT NOT NULL DEFAULT '',
    verify_status TEXT NOT NULL,
    standard_verified_at TEXT,
    strict_verify_status TEXT NOT NULL DEFAULT 'not_requested',
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
    FOREIGN KEY(job_id) REFERENCES backup_jobs(backup_job_id) ON DELETE CASCADE,
    UNIQUE(job_id, archive_seq),
    CHECK(archive_seq >= 1),
    CHECK(archive_size >= 0),
    CHECK(manifest_size >= 0),
    CHECK(manifest_item_count >= 0),
    CHECK(payload_member_count >= 0),
    CHECK(reference_member_count >= 0),
    CHECK(length(archive_sha256) = 64),
    CHECK(length(archive_md5) = 32),
    CHECK(length(manifest_sha256) = 64),
    CHECK(archive_type IN ('payload', 'manifest_only', 'mixed')),
    CHECK(verify_status IN (
        'not_started',
        'standard_test_started',
        'standard_test_passed',
        'failed'
    )),
    CHECK(strict_verify_status IN (
        'not_requested',
        'strict_extract_started',
        'strict_extract_hash_checked',
        'strict_extract_cleanup_done',
        'failed'
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

CREATE UNIQUE INDEX IF NOT EXISTS idx_archives_sha256
    ON archives(archive_sha256);

CREATE INDEX IF NOT EXISTS idx_archives_job
    ON archives(job_id, archive_seq);

CREATE TABLE IF NOT EXISTS archive_members (
    archive_member_id TEXT PRIMARY KEY,
    archive_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    content_reference_id TEXT NOT NULL DEFAULT '',
    file_item_id TEXT NOT NULL DEFAULT '',
    folder_item_id TEXT NOT NULL DEFAULT '',
    content_id TEXT NOT NULL DEFAULT '',
    member_type TEXT NOT NULL,
    member_path TEXT NOT NULL,
    file_sha256 TEXT NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    referenced_archive_id TEXT NOT NULL DEFAULT '',
    referenced_archive_remote_path TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY(archive_id) REFERENCES archives(archive_id) ON DELETE CASCADE,
    FOREIGN KEY(job_id) REFERENCES backup_jobs(backup_job_id) ON DELETE CASCADE,
    CHECK(size_bytes >= 0),
    CHECK(content_id = '' OR length(content_id) = 64),
    CHECK(file_sha256 = '' OR length(file_sha256) = 64),
    CHECK(member_type IN ('manifest', 'payload', 'reference', 'folder'))
);

CREATE INDEX IF NOT EXISTS idx_archive_members_archive
    ON archive_members(archive_id, member_type);

CREATE INDEX IF NOT EXISTS idx_archive_members_content
    ON archive_members(content_id, job_id);
