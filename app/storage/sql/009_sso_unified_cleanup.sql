-- v9: SSO audit is file-backed; the old table is exported before this DDL.
-- An active unified DingTalk app uses only the dingtalk_unified row.
-- Retain sso_auth_providers and sso_login_transactions for credentials and state.
DROP INDEX IF EXISTS idx_sso_auth_audit_cn_time;
DROP TABLE IF EXISTS sso_auth_audit;

-- These indexes served the removed multi-company settings / profile panels.
DROP INDEX IF EXISTS idx_sso_auth_providers_display_name;
DROP INDEX IF EXISTS idx_sso_provider_profile_enabled;
