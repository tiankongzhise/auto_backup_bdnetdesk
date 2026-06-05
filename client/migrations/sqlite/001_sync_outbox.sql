CREATE TABLE IF NOT EXISTS sync_outbox (
    event_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    retry_count INTEGER NOT NULL DEFAULT 0,
    next_retry_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(entity_id, revision_id),
    CHECK(status IN (
        'pending',
        'syncing',
        'synced',
        'sync_conflict',
        'retryable',
        'failed_terminal'
    )),
    CHECK(operation IN ('upsert', 'delete'))
);

CREATE INDEX IF NOT EXISTS idx_sync_outbox_status_next_retry
    ON sync_outbox(status, next_retry_at, created_at);

CREATE INDEX IF NOT EXISTS idx_sync_outbox_entity
    ON sync_outbox(entity_type, entity_id);

