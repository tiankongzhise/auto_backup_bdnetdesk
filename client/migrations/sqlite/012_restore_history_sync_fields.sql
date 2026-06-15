ALTER TABLE content_references
ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 1;

ALTER TABLE content_references
ADD COLUMN data_version INTEGER NOT NULL DEFAULT 1;

ALTER TABLE content_references
ADD COLUMN revision_id TEXT NOT NULL DEFAULT '';

ALTER TABLE content_references
ADD COLUMN sync_status TEXT NOT NULL DEFAULT 'sync_pending';

ALTER TABLE content_references
ADD COLUMN deleted_at TEXT;

ALTER TABLE content_references
ADD COLUMN canonical_record_sha256 TEXT NOT NULL DEFAULT '';

ALTER TABLE content_references
ADD COLUMN last_synced_revision_id TEXT;

ALTER TABLE archive_members
ADD COLUMN entity_id TEXT NOT NULL DEFAULT '';

ALTER TABLE archive_members
ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 1;

ALTER TABLE archive_members
ADD COLUMN data_version INTEGER NOT NULL DEFAULT 1;

ALTER TABLE archive_members
ADD COLUMN revision_id TEXT NOT NULL DEFAULT '';

ALTER TABLE archive_members
ADD COLUMN updated_at TEXT NOT NULL DEFAULT '';

ALTER TABLE archive_members
ADD COLUMN updated_by_device_id TEXT NOT NULL DEFAULT '';

ALTER TABLE archive_members
ADD COLUMN sync_status TEXT NOT NULL DEFAULT 'sync_pending';

ALTER TABLE archive_members
ADD COLUMN deleted_at TEXT;

ALTER TABLE archive_members
ADD COLUMN canonical_record_sha256 TEXT NOT NULL DEFAULT '';

ALTER TABLE archive_members
ADD COLUMN last_synced_revision_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_archive_members_entity
    ON archive_members(entity_id)
    WHERE entity_id <> '';
