# -*- coding: utf-8 -*-
"""对端链缓存时间显示为东八区（上海）。"""

from app.ui.pages.firewall import _format_peer_cache_timestamp


def test_format_peer_cache_z_to_shanghai():
    # 与 remote_chain_cache._iso_now 同形：UTC Z
    assert _format_peer_cache_timestamp("2026-04-23T02:12:00Z") == "2026-04-23 10:12:00"


def test_format_peer_cache_offset_preserved():
    assert _format_peer_cache_timestamp("2026-04-23T10:12:00+00:00") == "2026-04-23 18:12:00"


def test_format_peer_cache_empty():
    assert _format_peer_cache_timestamp(None) == "—"
    assert _format_peer_cache_timestamp("") == "—"
