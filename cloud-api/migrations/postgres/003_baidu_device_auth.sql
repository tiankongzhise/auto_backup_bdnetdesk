ALTER TABLE baidu_account_device_bindings
    ADD COLUMN IF NOT EXISTS token_expires_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS encryption_method TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS encrypted_token_json JSONB,
    ADD COLUMN IF NOT EXISTS private_key_hint TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS token_version BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_verified_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_verify_status TEXT NOT NULL DEFAULT 'unknown',
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

UPDATE baidu_account_device_bindings b
SET
    token_expires_at = a.token_expires_at,
    encryption_method = a.encryption_method,
    encrypted_token_json = a.encrypted_token_json,
    private_key_hint = a.private_key_hint,
    token_version = a.token_version,
    last_verified_at = a.last_verified_at,
    last_verify_status = a.last_verify_status,
    updated_at = now()
FROM baidu_accounts a
WHERE b.account_id = a.account_id
  AND b.encrypted_token_json IS NULL
  AND a.encrypted_token_json IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_baidu_account_device_bindings_token_expires_at
    ON baidu_account_device_bindings(token_expires_at);

CREATE INDEX IF NOT EXISTS idx_baidu_account_device_bindings_verify_status
    ON baidu_account_device_bindings(last_verify_status);

CREATE TABLE IF NOT EXISTS baidu_device_token_refresh_leases (
    account_id TEXT NOT NULL REFERENCES baidu_accounts(account_id) ON DELETE CASCADE,
    device_id TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    lease_id TEXT NOT NULL,
    holder_device_id TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, device_id)
);
