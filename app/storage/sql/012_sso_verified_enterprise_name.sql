-- Keep a provider-verified organization name with the existing SSO session.
-- Existing CorpId-only sessions stay valid; their name remains empty.
ALTER TABLE sso_auth_sessions ADD COLUMN enterprise_name TEXT NOT NULL DEFAULT '';
