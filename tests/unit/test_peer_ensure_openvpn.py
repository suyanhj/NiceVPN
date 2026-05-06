# -*- coding: utf-8 -*-
"""PeerService.ensure_openvpn_on_peer_via_ssh：先探测再按需安装"""

from pathlib import Path
from unittest.mock import patch

from app.services.peer_instance.service import PeerService


def test_ensure_openvpn_skips_when_version_ok():
    peer_id = "p1"
    row = {"id": peer_id, "ssh_host": "h", "ssh_port": 22, "ssh_username": "u", "ssh_password": "x"}
    probe_ok = {
        "connected": True,
        "ssh_error": None,
        "installed": True,
        "path": "/usr/sbin/openvpn",
        "version": "2.6.9",
        "meets_requirement": True,
        "remote_distro": {"pretty_name": "Debian GNU/Linux 12 (bookworm)"},
    }
    with patch.object(PeerService, "get", return_value=row):
        with patch.object(PeerService, "probe_openvpn_via_ssh", return_value=probe_ok):
            with patch.object(PeerService, "deploy_peer_site_firewall_via_ssh") as m_fw:
                svc = PeerService()
                out = svc.ensure_openvpn_on_peer_via_ssh(peer_id)
    m_fw.assert_not_called()
    assert out["ok"] is True
    assert out["skipped_install"] is True
    assert out["install"] is None
    assert out["probe"] == probe_ok
    assert out.get("ovpn_push") is None
    assert out.get("systemd_client") is None
    assert out["peer_firewall"] is None


def test_ensure_openvpn_runs_install_when_not_installed():
    peer_id = "p2"
    row = {"id": peer_id, "ssh_host": "h", "ssh_port": 22, "ssh_username": "u", "ssh_password": "x"}
    probe_need = {
        "connected": True,
        "ssh_error": None,
        "installed": False,
        "path": None,
        "version": None,
        "meets_requirement": False,
        "remote_distro": {},
    }
    probe_after = {**probe_need, "installed": True, "path": "/opt/openvpn/sbin/openvpn", "version": "2.7.0", "meets_requirement": True}
    install_ret = {
        "ok": True,
        "distro_family": "debian",
        "version_id": "12",
        "distro_id": "debian",
        "pretty_name": "Debian 12",
    }
    with patch.object(PeerService, "get", return_value=row):
        with patch.object(PeerService, "probe_openvpn_via_ssh", side_effect=[probe_need, probe_after]):
            with patch.object(
                PeerService,
                "deploy_peer_site_firewall_via_ssh",
                return_value={"ok": True},
            ) as m_fw:
                with patch(
                    "app.services.peer_instance.remote_peer_install.install_openvpn_on_peer_via_ssh",
                    return_value=install_ret,
                ) as m_inst:
                    svc = PeerService()
                    out = svc.ensure_openvpn_on_peer_via_ssh(peer_id)
    m_inst.assert_called_once_with(row)
    m_fw.assert_called_once_with(peer_id)
    assert out["ok"] is True
    assert out["skipped_install"] is False
    assert out["install"] == install_ret
    assert out.get("ovpn_push") is None
    assert out.get("systemd_client") is None
    assert out["peer_firewall"] == {"ok": True}


def test_ensure_openvpn_pushes_ovpn_after_install_when_center_file_exists(tmp_path: Path):
    peer_id = "p4"
    row = {
        "id": peer_id,
        "ssh_host": "h",
        "ssh_port": 22,
        "ssh_username": "u",
        "ssh_password": "x",
        "bound_username": "alice",
    }
    probe_need = {
        "connected": True,
        "ssh_error": None,
        "installed": False,
        "path": None,
        "version": None,
        "meets_requirement": False,
        "remote_distro": {},
    }
    probe_after = {**probe_need, "installed": True, "path": "/opt/openvpn/sbin/openvpn", "version": "2.7.0", "meets_requirement": True}
    install_ret = {
        "ok": True,
        "distro_family": "debian",
        "version_id": "12",
        "distro_id": "debian",
        "pretty_name": "Debian 12",
    }
    push_ret = {"ok": True, "remote_path": "/etc/openvpn/client/client.conf", "bytes": 3}
    sd_ret = {
        "ok": True,
        "unit_source": "official",
        "service": "openvpn-client@client.service",
        "config_path": "/etc/openvpn/client/client.conf",
    }
    ovpn_dir = tmp_path / "ovpn"
    ovpn_dir.mkdir()
    (ovpn_dir / "alice.ovpn").write_text("abc", encoding="utf-8")
    with patch.object(PeerService, "get", return_value=row):
        with patch.object(PeerService, "probe_openvpn_via_ssh", side_effect=[probe_need, probe_after]):
            with patch.object(
                PeerService,
                "deploy_peer_site_firewall_via_ssh",
                return_value={"ok": True, "applied": True},
            ) as m_fw:
                with patch(
                    "app.services.peer_instance.remote_peer_install.install_openvpn_on_peer_via_ssh",
                    return_value=install_ret,
                ):
                    with patch("app.services.peer_instance.service.OVPN_PROFILES_DIR", ovpn_dir):
                        with patch(
                            "app.services.peer_instance.remote_peer_ovpn.upload_bound_user_ovpn_via_ssh",
                            return_value=push_ret,
                        ) as m_push:
                            with patch(
                                "app.services.peer_instance.remote_peer_ovpn.deploy_openvpn_client_systemd_via_ssh",
                                return_value=sd_ret,
                            ) as m_sd:
                                svc = PeerService()
                                out = svc.ensure_openvpn_on_peer_via_ssh(peer_id)
    m_push.assert_called_once()
    m_sd.assert_called_once()
    m_fw.assert_called_once_with(peer_id)
    assert out["ovpn_push"] == push_ret
    assert out["systemd_client"] == sd_ret
    assert out["peer_firewall"] == {"ok": True, "applied": True}


def test_ensure_openvpn_raises_when_ssh_fails():
    peer_id = "p3"
    row = {"id": peer_id}
    probe_fail = {"connected": False, "ssh_error": "SSH 认证失败: x", "installed": False}
    with patch.object(PeerService, "get", return_value=row):
        with patch.object(PeerService, "probe_openvpn_via_ssh", return_value=probe_fail):
            svc = PeerService()
            try:
                svc.ensure_openvpn_on_peer_via_ssh(peer_id)
            except RuntimeError as exc:
                assert "SSH" in str(exc)
            else:
                raise AssertionError("expected RuntimeError")


def test_probe_openvpn_uses_global_ssh_key_when_peer_key_empty():
    peer_id = "p5"
    row = {"id": peer_id, "ssh_host": "h", "ssh_port": 22, "ssh_username": "u", "ssh_password": ""}
    cfg = {"global_ssh_private_key": "GLOBAL_PEM", "global_ssh_private_key_passphrase": "pp"}
    probe_ret = {"connected": True, "installed": True, "meets_requirement": True}
    with patch.object(PeerService, "get", return_value=row):
        with patch("app.core.config.load_config", return_value=cfg):
            with patch(
                "app.services.peer_instance.remote_openvpn.detect_openvpn_via_ssh",
                return_value=probe_ret,
            ) as m_detect:
                svc = PeerService()
                out = svc.probe_openvpn_via_ssh(peer_id)

    assert out == probe_ret
    assert m_detect.call_args.kwargs["private_key_text"] == "GLOBAL_PEM"
    assert m_detect.call_args.kwargs["private_key_passphrase"] == "pp"
