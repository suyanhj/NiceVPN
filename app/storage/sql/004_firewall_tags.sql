CREATE TABLE IF NOT EXISTS tags (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_tags (
    tag_id TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    PRIMARY KEY(tag_id, username)
);
CREATE INDEX IF NOT EXISTS idx_user_tags_username ON user_tags(username);
CREATE TABLE IF NOT EXISTS group_tags (
    tag_id TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    PRIMARY KEY(tag_id, group_id)
);
CREATE INDEX IF NOT EXISTS idx_group_tags_group ON group_tags(group_id);
