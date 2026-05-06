# -*- coding: utf-8 -*-
"""设备绑定 iv_plat 展示文案。"""

import json
from pathlib import Path

from app.services.user.device_bind import DeviceBindingService, format_iv_plat_display


def test_format_iv_plat_known():
    assert format_iv_plat_display("android") == "安卓"
    assert format_iv_plat_display("macosx") == "Mac"
    assert format_iv_plat_display("IOS") == "iOS"


def test_format_iv_plat_empty():
    assert format_iv_plat_display(None) == ""
    assert format_iv_plat_display("") == ""
    assert format_iv_plat_display("   ") == ""


def test_format_iv_plat_unknown():
    assert format_iv_plat_display("weird-os") == "其他(weird-os)"


def test_build_iv_plat_display_map_once(monkeypatch, tmp_path: Path):
    """单次目录扫描生成 username -> 展示文案。"""
    from app.services.user import device_bind as mod

    d = tmp_path / "bindings"
    d.mkdir()
    (d / "a.json").write_text(
        json.dumps(
            {"username": "u1", "iv_plat": "android", "id": "x", "fingerprint": "f", "fingerprint_source": "IV_HWADDR"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "DEVICE_BINDINGS_DIR", d)
    m = DeviceBindingService().build_user_binding_aux()
    assert m["u1"]["device_label"] == "安卓"
    assert m["u1"]["last_connected_since"] == ""

    (d / "b.json").write_text(
        json.dumps(
            {
                "username": "u2",
                "iv_plat": "ios",
                "last_connected_since": "2026-04-09 18:23:52",
                "id": "y",
                "fingerprint": "g",
                "fingerprint_source": "IV_HWADDR",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    m2 = DeviceBindingService().build_user_binding_aux()
    assert m2["u2"]["last_connected_since"] == "2026-04-09 18:23:52"
