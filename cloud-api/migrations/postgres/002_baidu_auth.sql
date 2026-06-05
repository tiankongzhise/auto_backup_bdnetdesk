CREATE TABLE IF NOT EXISTS baidu_accounts (
    account_id TEXT PRIMARY KEY,
    baidu_uid TEXT NOT NULL UNIQUE,
    baidu_uk TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT '',
    token_expires_at TIMESTAMPTZ NOT NULL,
    encryption_method TEXT NOT NULL,
    encrypted_token_json JSONB NOT NULL,
    private_key_hint TEXT NOT NULL DEFAULT '',
    token_version BIGINT NOT NULL DEFAULT 1,
    last_verified_at TIMESTAMPTZ,
    last_verify_status TEXT NOT NULL DEFAULT 'unknown',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_baidu_accounts_expires_at ON baidu_accounts(token_expires_at);
CREATE INDEX IF NOT EXISTS idx_baidu_accounts_verify_status ON baidu_accounts(last_verify_status);

CREATE TABLE IF NOT EXISTS baidu_auth_sessions (
    session_id TEXT PRIMARY KEY,
    flow TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_by_device_id TEXT NOT NULL REFERENCES devices(device_id),
    state TEXT NOT NULL UNIQUE,
    scope TEXT NOT NULL DEFAULT '',
    encryption_method TEXT NOT NULL,
    rsa_public_key_pem TEXT NOT NULL DEFAULT '',
    private_key_hint TEXT NOT NULL DEFAULT '',
    device_code TEXT NOT NULL DEFAULT '',
    user_code TEXT NOT NULL DEFAULT '',
    verification_url TEXT NOT NULL DEFAULT '',
    qrcode_url TEXT NOT NULL DEFAULT '',
    auth_url TEXT NOT NULL DEFAULT '',
    authorization_code TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT '',
    error_description TEXT NOT NULL DEFAULT '',
    expires_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    account_id TEXT REFERENCES baidu_accounts(account_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_baidu_auth_sessions_state ON baidu_auth_sessions(state);
CREATE INDEX IF NOT EXISTS idx_baidu_auth_sessions_device ON baidu_auth_sessions(requested_by_device_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_baidu_auth_sessions_status ON baidu_auth_sessions(status);

CREATE TABLE IF NOT EXISTS baidu_account_device_bindings (
    account_id TEXT NOT NULL REFERENCES baidu_accounts(account_id) ON DELETE CASCADE,
    device_id TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    selected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, device_id)
);

CREATE INDEX IF NOT EXISTS idx_baidu_account_device_bindings_device ON baidu_account_device_bindings(device_id);

CREATE TABLE IF NOT EXISTS baidu_token_refresh_leases (
    account_id TEXT PRIMARY KEY REFERENCES baidu_accounts(account_id) ON DELETE CASCADE,
    lease_id TEXT NOT NULL,
    holder_device_id TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
