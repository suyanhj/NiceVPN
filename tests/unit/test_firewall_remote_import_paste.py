# -*- coding: utf-8 -*-
"""对端链「导入」：iptables 粘贴与混合解析单元测试。"""

import json
import pytest

from app.services.firewall.iptables_mgr import IptablesManager
from app.ui.pages.firewall import FirewallPage

_CHAIN = "VPN_PEER_abc123456789"


def test_rows_from_iptables_paste_asa_lines():
    text = f"""# comment
-A {_CHAIN} -j ACCEPT
-A {_CHAIN} -s 10.0.0.0/8 -d 192.168.1.1 -j DROP
"""
    rows = FirewallPage._rows_from_iptables_paste(text, _CHAIN)
    assert rows == [
        {"rest": "-j ACCEPT", "enabled": True},
        {"rest": "-s 10.0.0.0/8 -d 192.168.1.1 -j DROP", "enabled": True},
    ]


def test_rows_from_iptables_paste_rest_only_lines():
    text = """-j RETURN
-s 1.1.1.1/32 -j ACCEPT
"""
    rows = FirewallPage._rows_from_iptables_paste(text, _CHAIN)
    assert rows == [
        {"rest": "-j RETURN", "enabled": True},
        {"rest": "-s 1.1.1.1/32 -j ACCEPT", "enabled": True},
    ]


def test_rows_from_iptables_paste_wrong_chain_raises():
    with pytest.raises(ValueError, match=r"第 1 行.*不属于当前对端链"):
        FirewallPage._rows_from_iptables_paste("-A OTHER_CHAIN -j ACCEPT", _CHAIN)


def test_rows_from_iptables_paste_empty_raises():
    with pytest.raises(ValueError, match="未解析到任何规则"):
        FirewallPage._rows_from_iptables_paste("  # only\n", _CHAIN)


def test_rows_from_remote_import_text_mixed_json_branch():
    pid = "peer-a"
    payload = {
        "peer_id": pid,
        "chain": _CHAIN,
        "chain_exists": True,
        "rows": [{"rest": "-j ACCEPT", "enabled": False}],
    }
    t = json.dumps(payload, ensure_ascii=False)
    rows, chn, cex = FirewallPage._rows_from_remote_import_text_mixed(t, pid, "ignored")

    assert rows == [{"rest": "-j ACCEPT", "enabled": False}]
    assert chn == _CHAIN
    assert cex is True


def test_rows_from_remote_import_text_mixed_json_peer_mismatch():
    t = json.dumps(
        {"peer_id": "other", "rows": [{"rest": "-j ACCEPT", "enabled": True}]},
        ensure_ascii=False,
    )
    with pytest.raises(ValueError, match="peer_id"):
        FirewallPage._rows_from_remote_import_text_mixed(t, "me", _CHAIN)


def test_rows_from_remote_import_text_mixed_iptables_branch():
    pid = "any"
    text = f"-A {_CHAIN} -j RETURN\n"
    rows, chn, cex = FirewallPage._rows_from_remote_import_text_mixed(text, pid, _CHAIN)
    assert rows == [{"rest": "-j RETURN", "enabled": True}]
    assert chn == _CHAIN
    assert cex is True


def test_rows_from_remote_backup_preserves_description_and_priority():
    payload = {
        "peer_id": "peer-a",
        "chain": _CHAIN,
        "chain_exists": True,
        "rows": [
            {
                "rest": "-s 10.0.0.0/8 -j ACCEPT",
                "enabled": False,
                "priority": 35,
                "description": "keep metadata",
            }
        ],
    }

    rows = FirewallPage._rows_from_remote_import_payload(payload)

    assert rows == [
        {
            "rest": "-s 10.0.0.0/8 -j ACCEPT",
            "enabled": False,
            "priority": 35,
            "description": "keep metadata",
        }
    ]


def test_rows_from_remote_backup_rejects_invalid_priority():
    payload = {
        "rows": [
            {
                "rest": "-j ACCEPT",
                "enabled": True,
                "priority": "not-number",
            }
        ]
    }
    with pytest.raises(ValueError, match="优先级无效"):
        FirewallPage._rows_from_remote_import_payload(payload)


def test_prepare_remote_import_classifies_backup_iptables_and_mixed():
    page = FirewallPage()

    backup = json.dumps(
        {
            "peer_id": "peer-a",
            "chain": _CHAIN,
            "chain_exists": True,
            "rows": [{"rest": "-j ACCEPT", "enabled": True}],
        },
        ensure_ascii=False,
    )
    prepared_backup = page._prepare_remote_import(
        backup,
        peer_id="peer-a",
    )
    assert prepared_backup["kind"] == "backup"
    assert prepared_backup["count"] == 1

    current_chain = IptablesManager.peer_chain_name_for_id("peer-a")
    prepared_iptables = page._prepare_remote_import(
        f"-A {current_chain} -j ACCEPT\n",
        peer_id="peer-a",
    )
    assert prepared_iptables["kind"] == "iptables"
    assert prepared_iptables["rows"] == [
        {"rest": "-j ACCEPT", "enabled": True}
    ]

    prepared_mixed = page._prepare_remote_import(
        "-p tcp -s 10.0.0.0/8 --dport 443 -j ACCEPT",
        peer_id="peer-a",
    )
    assert prepared_mixed["kind"] == "mixed"
    assert prepared_mixed["count"] >= 1
