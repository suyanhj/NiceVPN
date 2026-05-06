# -*- coding: utf-8 -*-
"""简写规则解析单元测试。"""

import pytest

from app.services.firewall.simple_rule_import import (
    parse_center_simplified_lines,
    remote_rests_from_create_fields,
    peer_rests_from_simplified_line,
    try_parse_simplified_line,
)
from app.utils.cidr import validate_iptables_addr_or_cidr


def test_try_parse_simplified_basic():
    s = try_parse_simplified_line("-s 10.0.0.0/8 -d 192.168.0.0/16")
    assert s is not None
    assert s.source == "10.0.0.0/8"
    assert s.dest == "192.168.0.0/16"
    assert s.protocol == "all"
    assert s.dest_port is None
    assert s.action == "accept"


def test_try_parse_with_proto_dport():
    s = try_parse_simplified_line("-p tcp -s 1.1.1.1 -d 2.2.2.2 --dport 443")
    assert s is not None
    assert s.protocol == "tcp"
    assert s.dest_port == "443"


def test_try_parse_rejects_asa_start():
    assert try_parse_simplified_line("-A FOO -j ACCEPT") is None


def test_peer_rests_all_proto_multiport():
    from app.services.firewall.simple_rule_import import SimplifiedLine

    r = peer_rests_from_simplified_line(
        SimplifiedLine("10.0.0.0/8", "2.2.2.2", "all", "80,443", "accept")
    )
    assert len(r) == 2
    assert "multiport" in r[0] and "tcp" in r[0]
    assert "udp" in r[1]


def test_parse_center_multiline():
    t = """
# 备注
-s 1.1.1.0/24 -d 2.2.2.0/24
-p udp --dport 53 -s 0.0.0.0/0 -d 8.8.8.8
"""
    lines = parse_center_simplified_lines(t)
    assert len(lines) == 2


def test_parse_center_rejects_asa():
    with pytest.raises(ValueError, match="-A"):
        parse_center_simplified_lines("-A VPN_FORWARD -j ACCEPT")


def test_reject_five_dot_decimal_octets():
    """0.0.0.0.0 等非法点分表示须在落库/SSH 前拒绝，避免对端 -F 后追加失败、链被清空。"""
    with pytest.raises(ValueError, match="非法 IP"):
        validate_iptables_addr_or_cidr("0.0.0.0.0")
    with pytest.raises(ValueError):
        remote_rests_from_create_fields(
            source_subnet="10.0.0.0/24",
            source_ips=None,
            action="accept",
            protocol="all",
            dest_ip="0.0.0.0.0",
            dest_port=None,
        )
