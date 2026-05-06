# -*- coding: utf-8 -*-
"""对端 LAN 防火墙下拉候选单元测试"""

from unittest.mock import patch

from app.services.peer_instance.service import PeerService


def test_list_peer_lan_firewall_presets_for_center_form_lists_all_no_group_in_label():
    peers = [
        {
            "id": "p1",
            "name": "站点A",
            "bound_username": "alice",
            "lan_cidrs": ["10.10.0.0/24"],
        },
        {
            "id": "p2",
            "name": "站点B",
            "bound_username": "bob",
            "lan_cidrs": ["10.20.0.0/24"],
        },
    ]
    with patch.object(PeerService, "list_all", return_value=peers):
        svc = PeerService()
        allp = svc.list_peer_lan_firewall_presets_for_center_form()
    assert len(allp) == 2
    cidrs = {x["cidr"] for x in allp}
    assert cidrs == {"10.10.0.0/24", "10.20.0.0/24"}
    for x in allp:
        assert "对端：" in x["label"]
        assert "归组" not in x["label"]
