# -*- coding: utf-8 -*-
"""防火墙对端策略展示适配测试。"""

from app.ui.adapters.firewall import (
    adapt_remote_cache_status,
    adapt_remote_peer_capability,
    adapt_remote_rule_row,
    adapt_remote_rule_rows,
    format_remote_cache_timestamp,
    parse_remote_rule_hints,
)


def test_parse_remote_rule_hints_covers_protocol_flow_port_action_comment() -> None:
    hints = parse_remote_rule_hints(
        '-p tcp -s 10.8.0.0/24 -d 172.16.0.10 --dport 443 '
        '-m comment --comment "allow api" -j ACCEPT'
    )

    assert hints == {
        "proto": "TCP",
        "action": "ACCEPT",
        "source": "10.8.0.0/24",
        "dest": "172.16.0.10",
        "dport": "443",
        "comment": "allow api",
    }


def test_remote_rule_adapter_keeps_raw_and_parsed_traceability() -> None:
    row = adapt_remote_rule_row(
        {
            "rest": "-p udp -s 192.168.10.0/24 -d 10.0.0.53 --dport 53 -j ACCEPT",
            "enabled": True,
            "priority": 40,
            "description": "dns",
        },
        index=2,
        peer_id="peer-gz",
    )

    assert row["id"] == "peer-gz:2"
    assert row["row_number"] == 3
    assert row["priority"] == 40
    assert row["protocol"] == "UDP"
    assert row["action_label"] == "ACCEPT"
    assert row["action_tone"] == "success"
    assert row["source"] == "192.168.10.0/24"
    assert row["dest"] == "10.0.0.53"
    assert row["dest_port"] == "53"
    assert row["description"] == "dns"
    assert row["raw_rest"].startswith("-p udp")
    assert row["parsed_hints"]["dport"] == "53"
    assert row["can_edit"] is True


def test_remote_rule_adapter_fallbacks_for_raw_unparsed_rule() -> None:
    row = adapt_remote_rule_row(
        {
            "rest": "-m conntrack --ctstate ESTABLISHED,RELATED -j RETURN",
            "enabled": False,
        },
        index=0,
        peer_id="peer-1",
    )

    assert row["priority"] == 10
    assert row["protocol"] == "ALL"
    assert row["action_label"] == "RETURN"
    assert row["action_tone"] == "unknown"
    assert row["source"] == "*"
    assert row["dest"] == "*"
    assert row["dest_port"] == "*"
    assert row["status_label"] == "停用"
    assert row["raw_rest"] == "-m conntrack --ctstate ESTABLISHED,RELATED -j RETURN"


def test_remote_rule_rows_keep_index_identity_and_priority_fallback() -> None:
    rows = adapt_remote_rule_rows(
        [
            {"rest": "-j ACCEPT", "enabled": True},
            {"rest": "-j DROP", "enabled": True, "priority": 90},
        ],
        peer_id="peer-a",
    )

    assert [row["id"] for row in rows] == ["peer-a:0", "peer-a:1"]
    assert [row["priority"] for row in rows] == [10, 90]
    assert [row["action_label"] for row in rows] == ["ACCEPT", "DROP"]


def test_remote_cache_status_matrix() -> None:
    synced = adapt_remote_cache_status(
        {
            "chain": "VPN_PEER_abc",
            "chain_exists": True,
            "rows": [{"rest": "-j ACCEPT"}],
            "pending_sync": False,
            "last_sync_error": None,
            "last_pulled_at": "2026-10-20T01:00:00Z",
            "last_pushed_at": "2026-10-20T02:00:00Z",
        }
    )
    pending = adapt_remote_cache_status(
        {
            "chain_exists": True,
            "rows": [],
            "pending_sync": True,
        }
    )
    failed = adapt_remote_cache_status(
        {
            "chain_exists": True,
            "rows": [],
            "pending_sync": True,
            "last_sync_error": "sudo failed",
        }
    )
    missing = adapt_remote_cache_status(None)
    fetching = adapt_remote_cache_status(None, fetch_running=True)

    assert synced["status_label"] == "已与远端同步"
    assert synced["status_tone"] == "success"
    assert synced["row_count"] == 1
    assert synced["last_pulled_at"] == "2026-10-20 09:00:00"
    assert synced["last_pushed_at"] == "2026-10-20 10:00:00"

    assert pending["status_label"] == "本地有待推送变更"
    assert pending["status_tone"] == "warning"
    assert failed["status_label"] == "上次写回失败"
    assert failed["status_tone"] == "failed"
    assert failed["last_sync_error"] == "sudo failed"
    assert missing["status_label"] == "尚无本地副本"
    assert fetching["status_label"] == "正在拉取远端真值"


def test_remote_peer_capability_requires_peer_identity_and_auth() -> None:
    peer = {
        "id": "peer-1",
        "name": "广州站点",
        "ssh_host": "203.0.113.8",
        "ssh_username": "root",
    }

    ready = adapt_remote_peer_capability(peer, has_ssh_auth=True)
    no_auth = adapt_remote_peer_capability(peer, has_ssh_auth=False)
    no_ssh = adapt_remote_peer_capability(
        {**peer, "ssh_host": ""},
        has_ssh_auth=True,
    )
    fetching = adapt_remote_peer_capability(
        peer,
        has_ssh_auth=True,
        fetch_running=True,
    )

    assert ready["remote_ready"] is True
    assert ready["status_label"] == "SSH 可用"
    assert ready["can_fetch"] is True
    assert ready["can_push"] is True

    assert no_auth["remote_ready"] is False
    assert no_auth["status_label"] == "SSH 缺少凭据"
    assert "全局私钥" in no_auth["reason"]

    assert no_ssh["status_label"] == "SSH 未配置"
    assert no_ssh["can_fetch"] is False

    assert fetching["status_label"] == "正在拉取"
    assert fetching["can_fetch"] is False


def test_format_remote_cache_timestamp_handles_invalid_and_empty() -> None:
    assert format_remote_cache_timestamp(None) == "—"
    assert format_remote_cache_timestamp("2026-10-20T00:00:00Z") == "2026-10-20 08:00:00"
    assert format_remote_cache_timestamp("not-a-date") == "not-a-date"
