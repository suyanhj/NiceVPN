-- v8: read_user_profile column is added conditionally by schema_manager.
-- Index helps identify providers with optional delegated user scope enabled.
CREATE INDEX IF NOT EXISTS idx_sso_provider_profile_enabled
    ON sso_auth_providers(kind, enabled, read_user_profile);
