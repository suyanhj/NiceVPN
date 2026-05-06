# -*- coding: utf-8 -*-
"""对端 filter 链 ``iptables -S`` 解析（与 ``remote_peer_iptables`` 一致）。"""

from app.services.peer_instance import remote_peer_iptables as rpi


def test_filter_chain_rests_from_s_parses_appends() -> None:
    out = """
-A VPN_PEER_abcd12345678 -i tun0 -m comment --comment 'x' -j ACCEPT
-A VPN_PEER_abcd12345678 -s 10.0.0.0/8 -j DROP
""".strip()
    rests = rpi._filter_chain_rests_from_iptables_s(out, "VPN_PEER_abcd12345678")
    assert len(rests) == 2
    assert "-i tun0" in rests[0]
    assert "-s 10.0.0.0/8" in rests[1]


def test_forward_lines_jumping_to_chain() -> None:
    s = """
-A FORWARD -i eth0 -j VPN_PEER_abcd12345678
-A FORWARD -j DROP
""".strip()
    lines = rpi._forward_lines_jumping_to_chain(s, "VPN_PEER_abcd12345678")
    assert len(lines) == 1
    assert "FORWARD" in lines[0]
