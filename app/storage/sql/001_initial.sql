CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS storage_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    group_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_group_status
    ON users(group_id, status);
CREATE INDEX IF NOT EXISTS idx_users_status_username
    ON users(status, username COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS groups (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_groups_name
    ON groups(name);
CREATE INDEX IF NOT EXISTS idx_groups_created_at
    ON groups(created_at, id);

CREATE TABLE IF NOT EXISTS peers (
    id TEXT PRIMARY KEY,
    bound_username TEXT NOT NULL,
    center_forward_priority INTEGER NOT NULL DEFAULT 500000,
    center_forward_enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_peers_bound_username
    ON peers(bound_username);
CREATE INDEX IF NOT EXISTS idx_peers_priority
    ON peers(center_forward_priority, id);

CREATE TABLE IF NOT EXISTS firewall_rules (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    owner_type TEXT NOT NULL,
    priority INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_firewall_owner_priority
    ON firewall_rules(owner_id, priority, id);
CREATE INDEX IF NOT EXISTS idx_firewall_priority
    ON firewall_rules(priority, id);
CREATE INDEX IF NOT EXISTS idx_firewall_owner_type
    ON firewall_rules(owner_type, owner_id);
