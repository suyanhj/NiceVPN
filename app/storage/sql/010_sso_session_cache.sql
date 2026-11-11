-- v10: persistent enterprise login sessions for OpenVPN reconnects.
-- OpenVPN verifies client certificates; NiceVPN records only the verified
-- fingerprint provided by the trusted Management channel.
CREATE TABLE IF NOT EXISTS sso_auth_sessions (
    instance_id TEXT NOT NULL,
    common_name TEXT NOT NULL,
    cert_fingerprint TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    provider_app_id TEXT NOT NULL,
    enterprise_id TEXT NOT NULL,
    issued_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    last_used_at INTEGER NOT NULL,
    PRIMARY KEY (instance_id, common_name, cert_fingerprint, provider_id),
    CHECK (expires_at > issued_at)
);
CREATE INDEX IF NOT EXISTS idx_sso_auth_sessions_expiry
    ON sso_auth_sessions(expires_at);
