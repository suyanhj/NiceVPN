-- v6: Enterprise SSO policy and pending-auth metadata.
-- Existing users/groups/firewall rules remain untouched.
-- Tag sso_required column is added conditionally by schema_manager
-- to support re-running migrations against partially upgraded databases.

-- Secrets must not be stored in this table as plaintext.
-- Use a private secret manager when implementing the OAuth adapter.
CREATE TABLE IF NOT EXISTS sso_auth_providers (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('dingtalk', 'wecom')),
    enterprise_id TEXT NOT NULL,
    app_id TEXT NOT NULL,
    secret_ref TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(kind, enterprise_id, app_id)
);

CREATE TABLE IF NOT EXISTS sso_login_transactions (
    token_hash TEXT PRIMARY KEY,
    state_hash TEXT NOT NULL UNIQUE,
    instance_id TEXT NOT NULL,
    client_id INTEGER NOT NULL,
    key_id INTEGER NOT NULL,
    common_name TEXT NOT NULL,
    provider_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'denied', 'expired')),
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    completed_at INTEGER,
    CHECK (expires_at > created_at)
);
CREATE INDEX IF NOT EXISTS idx_sso_login_transactions_expiry
    ON sso_login_transactions(status, expires_at);

CREATE TABLE IF NOT EXISTS sso_auth_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_time TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    common_name TEXT NOT NULL,
    provider_kind TEXT NOT NULL DEFAULT '',
    enterprise_id TEXT NOT NULL DEFAULT '',
    external_user_id TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL,
    outcome TEXT NOT NULL,
    reason_code TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sso_auth_audit_cn_time
    ON sso_auth_audit(common_name, event_time DESC);
