-- v7: Add human-readable organization labels for multi-company SSO.
-- The display_name column is added conditionally by schema_manager.
-- Pre-release certificate-level exceptions are removed: group/tag opt-in only.
DROP TABLE IF EXISTS sso_certificate_exemptions;
CREATE INDEX IF NOT EXISTS idx_sso_auth_providers_display_name
    ON sso_auth_providers(display_name);
