# -*- coding: utf-8 -*-
"""vpn_ops 已存在用户预检"""

from app.api.vpn_ops import _already_active_usernames


def test_already_active_filters_deleted():
    class Svc:
        def get(self, username):
            if username == "a":
                return {"status": "active"}
            if username == "b":
                return {"status": "deleted"}
            return None

    assert _already_active_usernames(Svc(), ["a", "b", "c"]) == ["a"]
