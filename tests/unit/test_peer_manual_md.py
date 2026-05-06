# -*- coding: utf-8 -*-
"""对端手动部署 Markdown 生成单元测试"""

from app.services.peer_instance.peer_manual_md import build_peer_site_manual_context, build_peer_site_manual_markdown


def test_build_peer_site_manual_contains_essentials():
    md = build_peer_site_manual_markdown(
        peer_name="站点甲",
        peer_id="abc-uuid",
        bound_username="u1",
        lan_cidrs=["192.168.1.0/24"],
        global_subnet="10.255.0.0/16",
        masquerade_on_peer=False,
    )
    assert "站点甲" in md
    assert "abc-uuid" in md
    assert "peer=abc-uuid" in md
    assert "ovpn-mgmt-peer" in md
    assert "role=fwd-global" in md
    assert "10.255.0.0/16" in md
    assert "192.168.1.0/24" in md
    assert "u1" in md
    assert "/etc/openvpn/client/client.conf" in md
    assert "openvpn-client@client.service" in md
    assert "openvpn-client.service" in md
    assert "FORWARD" in md
    assert "ovpn-mgmt-peer" in md
    assert "role=fwd-global" in md


def test_build_peer_site_manual_context_has_copyable_commands():
    ctx = build_peer_site_manual_context(
        peer_name="站点甲",
        peer_id="abc-uuid",
        bound_username="u1",
        lan_cidrs=["192.168.1.0/24"],
        global_subnet="10.255.0.0/16",
        masquerade_on_peer=True,
    )
    commands = ctx["commands"]
    joined = "\n".join(commands)
    assert ctx["overview"]["client_config_path"] == "/etc/openvpn/client/client.conf"
    assert len(commands) >= 4
    assert "peer=abc-uuid" in joined
    assert "role=masq" in joined
    assert "-i tun0" not in joined
