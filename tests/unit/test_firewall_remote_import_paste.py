# -*- coding: utf-8 -*-
"""对端链「导入」：iptables 粘贴与混合解析单元测试。"""

import json
import pytest

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
    with pytest.raises(ValueError, match="不属于当前对端链"):
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
