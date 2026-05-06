# -*- coding: utf-8 -*-
"""iptables 行解析单元测试。"""
from app.cli.iptables_parse import parse_iptables_line


def test_parse_udp_dport_accept():
    line = "iptables -A INPUT -p udp --dport 1194 -j ACCEPT"
    d = parse_iptables_line(line)
    assert d is not None
    assert d["action"] == "accept"
    assert d["protocol"] == "udp"
    assert d["dest_port"] == "1194"
    assert d["_chain"] == "INPUT"


def test_parse_forward_with_source():
    line = "iptables -A FORWARD -s 10.8.0.0/24 -d 192.168.1.1 -p tcp --dport 443 -j ACCEPT"
    d = parse_iptables_line(line)
    assert d is not None
    assert d["source_subnet"] == "10.8.0.0/24"
    assert d["dest_ip"] == "192.168.1.1"
    assert d["dest_port"] == "443"
    assert d["action"] == "accept"


def test_parse_single_ip_source_normalized():
    line = "iptables -A FORWARD -s 10.0.0.1 -j DROP"
    d = parse_iptables_line(line)
    assert d is not None
    assert d["source_subnet"] == "10.0.0.1/32"
    assert d["action"] == "drop"


def test_skip_masquerade():
    assert parse_iptables_line("iptables -t nat -A POSTROUTING -j MASQUERADE") is None
