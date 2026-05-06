# -*- coding: utf-8 -*-
"""vpn_ops 前缀匹配辅助逻辑单测"""

from unittest.mock import MagicMock

from app.api.vpn_ops import _usernames_matching_prefix


def test_usernames_matching_prefix_order():
    svc = MagicMock()
    svc.list_all.return_value = [
        {"username": "lisi_2", "status": "active"},
        {"username": "lisi", "status": "active"},
        {"username": "lisi_10", "status": "active"},
        {"username": "lisi_1", "status": "active"},
        {"username": "lisi_other", "status": "active"},
        {"username": "xlisi", "status": "active"},
    ]
    out = _usernames_matching_prefix("lisi", svc)
    assert out == ["lisi", "lisi_1", "lisi_2", "lisi_10"]
