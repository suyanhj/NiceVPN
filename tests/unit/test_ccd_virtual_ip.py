# -*- coding: utf-8 -*-
"""CCD 虚拟 IP 扫描。"""

from pathlib import Path

from app.services.user.crud import UserService


def test_write_ccd_ifconfig_uses_global_netmask(monkeypatch, tmp_path: Path):
    """global_subnet 为 /16 时 ifconfig-push 第二段为全局掩码，与 server 行一致，避免 Linux via 失敗。"""
    from box import Box
    from app.services.user import crud as mod

    monkeypatch.setattr(mod, "CCD_DIR", tmp_path)
    monkeypatch.setattr(mod, "USERS_DIR", tmp_path / "users")
    (tmp_path / "users").mkdir()
    monkeypatch.setattr(
        "app.services.user.crud.load_config",
        lambda: Box({"global_subnet": "10.224.0.0/16"}),
    )
    monkeypatch.setattr(mod, "fix_path_for_openvpn_shared_data", lambda p: None)

    mod.UserService()._write_ccd("u1", {"id": "g1", "subnet": "10.224.200.0/24"})
    line = (tmp_path / "u1").read_text(encoding="utf-8").strip()
    assert line == "ifconfig-push 10.224.200.2 255.255.0.0"


def test_list_ccd_virtual_ipv4_by_username(monkeypatch, tmp_path: Path):
    from app.services.user import crud as mod

    ccd = tmp_path / "ccd"
    ccd.mkdir()
    (ccd / "alice").write_text("ifconfig-push 10.8.1.10 255.255.255.0\n", encoding="utf-8")
    (ccd / "bob").write_text("# empty\n", encoding="utf-8")
    monkeypatch.setattr(mod, "CCD_DIR", ccd)
    m = UserService().list_ccd_virtual_ipv4_by_username()
    assert m["alice"] == "10.8.1.10"
    assert "bob" not in m
