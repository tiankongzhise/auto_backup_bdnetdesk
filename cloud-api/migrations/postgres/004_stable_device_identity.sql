ALTER TABLE devices
ADD COLUMN IF NOT EXISTS device_fingerprint_hash CHAR(64) NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS device_tokens (
    device_token_hash TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ
);

INSERT INTO device_tokens (device_token_hash, device_id, last_seen_at, revoked_at)
SELECT device_token_hash, device_id, last_seen_at, revoked_at
FROM devices
ON CONFLICT (device_token_hash) DO NOTHING;
