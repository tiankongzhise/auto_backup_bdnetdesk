ALTER TABLE backup_sources
ADD COLUMN entity_id TEXT NOT NULL DEFAULT '';

ALTER TABLE backup_sources
ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 1;

ALTER TABLE backup_sources
ADD COLUMN data_version INTEGER NOT NULL DEFAULT 1;

ALTER TABLE backup_sources
ADD COLUMN revision_id TEXT NOT NULL DEFAULT '';

ALTER TABLE backup_sources
ADD COLUMN updated_by_device_id TEXT NOT NULL DEFAULT '';

ALTER TABLE backup_sources
ADD COLUMN sync_status TEXT NOT NULL DEFAULT 'sync_pending';

ALTER TABLE backup_sources
ADD COLUMN deleted_at TEXT;

ALTER TABLE backup_sources
ADD COLUMN canonical_record_sha256 TEXT NOT NULL DEFAULT '';

ALTER TABLE backup_sources
ADD COLUMN last_synced_revision_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_backup_sources_entity
    ON backup_sources(entity_id)
    WHERE entity_id <> '';
