# -*- coding: utf-8 -*-
"""CCD 虚拟 IP 扫描。"""

from pathlib import Path

from app.services.user.crud import UserService


def test_write_ccd_ifconfig_uses_global_netmask(monkeypatch, tmp_path: Path):
    """global_subnet 为 /16 时 ifconfig-push 第二段为全局掩码，与 server 行一致，避免 Linux via 失敗。"""
    from box import Box
    from app.services.user import crud as mod

    monkeypatch.setattr(mod, "CCD_DIR", tmp_path)
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


def test_group_pool_full_rejected_before_certificate_generation(monkeypatch, tmp_path):
    """地址池满员时在 PKI / .ovpn 创建之前拒绝新用户。"""
    from app.services.user import crud as mod
    from app.storage.sqlite_store import SqliteStore
    import pytest

    store = SqliteStore(tmp_path / "store.sqlite")
    service = UserService(store=store)
    monkeypatch.setattr(service, "_user_exists", lambda name: False)
    monkeypatch.setattr(
        service, "_load_group",
        lambda gid: {"id": gid, "subnet": "192.168.1.0/30"},
    )
    monkeypatch.setattr(
        service, "_allocate_virtual_ip_for_group",
        lambda group: (_ for _ in ()).throw(ValueError("没有可用 IP")),
    )
    with pytest.raises(ValueError, match="没有可用 IP"):
        service._create_locked("new-user", "test-group")


def test_ip_allocation_reserves_first_host_and_exhausts_without_overflow(monkeypatch, tmp_path):
    from app.services.user import crud as mod
    from app.storage.sqlite_store import SqliteStore
    import pytest
    from box import Box

    store = SqliteStore(tmp_path / "store.sqlite")
    monkeypatch.setattr(
        mod, "load_config", lambda: Box({"global_subnet": "192.168.1.0/24"}),
    )
    service = UserService(store=store)
    # /30 中 .1 保留，只有 .2 可以分配。
    group = {"id": "g", "subnet": "192.168.1.0/30"}
    monkeypatch.setattr(service, "_collect_used_ips_in_group", lambda *_: set())
    ip, _ = service._allocate_virtual_ip_for_group(group)
    assert ip == "192.168.1.2"
    monkeypatch.setattr(
        service, "_collect_used_ips_in_group", lambda *_: {"192.168.1.2"}
    )
    with pytest.raises(ValueError, match="所有地址已分配"):
        service._allocate_virtual_ip_for_group(group)
