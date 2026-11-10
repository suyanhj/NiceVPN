-- v11: bind a verified OAuth lease to the OpenVPN connection reported by
-- Management status.  Heartbeats UPDATE the existing session row; no event
-- or heartbeat history rows are inserted.
ALTER TABLE sso_auth_sessions ADD COLUMN active_client_id INTEGER;
ALTER TABLE sso_auth_sessions ADD COLUMN active_connected_at INTEGER;
