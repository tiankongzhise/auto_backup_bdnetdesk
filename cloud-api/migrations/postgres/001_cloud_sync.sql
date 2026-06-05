CREATE TABLE IF NOT EXISTS devices (
    device_id TEXT PRIMARY KEY,
    device_token_hash TEXT NOT NULL UNIQUE,
    device_name TEXT NOT NULL,
    hostname TEXT NOT NULL DEFAULT '',
    os_version TEXT NOT NULL DEFAULT '',
    client_version TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS cloud_entities (
    entity_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    data_version BIGINT NOT NULL,
    revision_id TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    updated_by_device_id TEXT NOT NULL REFERENCES devices(device_id),
    sync_status TEXT NOT NULL DEFAULT 'synced',
    deleted_at TIMESTAMPTZ,
    canonical_record_sha256 CHAR(64) NOT NULL,
    payload_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cloud_entities_type ON cloud_entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_cloud_entities_updated_at ON cloud_entities(updated_at);

CREATE TABLE IF NOT EXISTS entity_revisions (
    event_id TEXT NOT NULL UNIQUE,
    entity_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    data_version BIGINT NOT NULL,
    revision_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    payload_json JSONB NOT NULL,
    canonical_record_sha256 CHAR(64) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    updated_by_device_id TEXT NOT NULL REFERENCES devices(device_id),
    deleted_at TIMESTAMPTZ,
    apply_status TEXT NOT NULL,
    conflict_of_revision_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (entity_id, revision_id)
);

CREATE INDEX IF NOT EXISTS idx_entity_revisions_entity ON entity_revisions(entity_id, data_version DESC);
CREATE INDEX IF NOT EXISTS idx_entity_revisions_status ON entity_revisions(apply_status);

CREATE TABLE IF NOT EXISTS content_objects (
    content_id TEXT PRIMARY KEY,
    file_sha256 CHAR(64) NOT NULL,
    size_bytes BIGINT NOT NULL,
    latest_entity_id TEXT NOT NULL REFERENCES cloud_entities(entity_id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS archive_objects (
    archive_sha256 CHAR(64) PRIMARY KEY,
    archive_size BIGINT NOT NULL DEFAULT 0,
    remote_path TEXT NOT NULL DEFAULT '',
    remote_verified BOOLEAN NOT NULL DEFAULT false,
    latest_entity_id TEXT NOT NULL REFERENCES cloud_entities(entity_id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

