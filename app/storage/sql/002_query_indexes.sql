-- v2: indexes supporting paginated peer and firewall queries.
CREATE INDEX IF NOT EXISTS idx_peers_center_enabled_priority
    ON peers(has_lan_cidrs, center_forward_enabled, center_forward_priority, id);
CREATE INDEX IF NOT EXISTS idx_firewall_filter
    ON firewall_rules(enabled, protocol, action, owner_type, owner_id);
