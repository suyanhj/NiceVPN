# -*- coding: utf-8 -*-
"""PeerService.ensure_openvpn_on_peer_via_ssh：幂等一键装配。"""

from unittest.mock import patch

import pytest

from app.services.peer_instance.service import PeerService


_ROW = {
    "id": "peer-1",
    "ssh_host": "host",
    "ssh_port": 22,
    "ssh_username": "root",
    "ssh_password": "secret",
    "bound_username": "alice",
}

_PROBE_OK = {
    "connected": True,
    "ssh_error": None,
    "installed": True,
    "path": "/usr/sbin/openvpn",
    "version": "2.7.0",
    "meets_requirement": True,
    "remote_distro": {"pretty_name": "Debian 12"},
}

_PUSH_OK = {
    "ok": True,
    "remote_path": "/etc/openvpn/client/client.conf",
    "bytes": 1024,
}

_SYSTEMD_OK = {
    "ok": True,
    "unit_source": "official",
    "service": "openvpn-client@client.service",
    "config_path": "/etc/openvpn/client/client.conf",
}

_FIREWALL_OK = {"ok": True, "already_current": False}

_VERIFY_OK = {
    "ok": True,
    "exists": True,
    "service": "openvpn-client@client.service",
    "config_path": "/etc/openvpn/client/client.conf",
    "config_exists": True,
    "active_state": "active",
    "is_active": "active",
    "is_enabled": "enabled",
}


def _run_ensure(
    *,
    probe_side_effect,
    install_result=None,
    verification=None,
):
    with patch.object(PeerService, "get", return_value=dict(_ROW)):
        with patch.object(
            PeerService,
            "probe_openvpn_via_ssh",
            side_effect=probe_side_effect,
        ):
            with patch.object(
                PeerService,
                "deploy_peer_site_firewall_via_ssh",
                return_value=dict(_FIREWALL_OK),
            ) as m_fw:
                with patch(
                    "app.services.peer_instance.remote_peer_install."
                    "install_openvpn_on_peer_via_ssh",
                    return_value=install_result,
                ) as m_install:
                    with patch(
                        "app.services.peer_instance.remote_peer_ovpn."
                        "upload_bound_user_ovpn_via_ssh",
                        return_value=dict(_PUSH_OK),
                    ) as m_push:
                        with patch(
                            "app.services.peer_instance.remote_peer_ovpn."
                            "deploy_openvpn_client_systemd_via_ssh",
                            return_value=dict(_SYSTEMD_OK),
                        ) as m_systemd:
                            with patch(
                                "app.services.peer_instance.remote_peer_ovpn."
                                "fetch_openvpn_client_service_status_via_ssh",
                                return_value=dict(
                                    verification
                                    if verification is not None
                                    else _VERIFY_OK
                                ),
                            ) as m_verify:
                                out = PeerService().ensure_openvpn_on_peer_via_ssh(
                                    _ROW["id"]
                                )
    return out, m_install, m_push, m_systemd, m_fw, m_verify


def test_ensure_openvpn_existing_install_still_ensures_full_configuration():
    out, m_install, m_push, m_systemd, m_fw, m_verify = _run_ensure(
        probe_side_effect=[dict(_PROBE_OK)],
    )

    m_install.assert_not_called()
    m_push.assert_called_once_with(dict(_ROW), remote_path=None)
    m_systemd.assert_called_once_with(
        dict(_ROW),
        config_path="/etc/openvpn/client/client.conf",
    )
    m_fw.assert_called_once_with(_ROW["id"])
    m_verify.assert_called_once_with(dict(_ROW))

    assert out["ok"] is True
    assert out["skipped_install"] is True
    assert out["install"] is None
    assert out["ovpn_push"]["ok"] is True
    assert out["systemd_client"]["ok"] is True
    assert out["peer_firewall"]["ok"] is True
    assert out["verification"]["is_active"] == "active"


def test_ensure_openvpn_installs_when_needed_then_runs_same_configuration():
    probe_need = {
        **_PROBE_OK,
        "installed": False,
        "path": None,
        "version": None,
        "meets_requirement": False,
    }
    install_result = {
        "ok": True,
        "distro_family": "debian",
        "version_id": "12",
        "distro_id": "debian",
        "pretty_name": "Debian 12",
    }

    out, m_install, m_push, m_systemd, m_fw, m_verify = _run_ensure(
        probe_side_effect=[probe_need, dict(_PROBE_OK)],
        install_result=install_result,
    )

    m_install.assert_called_once_with(dict(_ROW))
    m_push.assert_called_once()
    m_systemd.assert_called_once()
    m_fw.assert_called_once_with(_ROW["id"])
    m_verify.assert_called_once()

    assert out["skipped_install"] is False
    assert out["install"] == install_result
    assert out["probe"]["meets_requirement"] is True


def test_ensure_openvpn_rejects_inactive_final_service():
    inactive = {
        **_VERIFY_OK,
        "active_state": "failed",
        "is_active": "failed",
    }

    with pytest.raises(RuntimeError, match="未处于 active"):
        _run_ensure(
            probe_side_effect=[dict(_PROBE_OK)],
            verification=inactive,
        )


def test_ensure_openvpn_propagates_firewall_failure():
    with patch.object(PeerService, "get", return_value=dict(_ROW)):
        with patch.object(
            PeerService,
            "probe_openvpn_via_ssh",
            return_value=dict(_PROBE_OK),
        ):
            with patch(
                "app.services.peer_instance.remote_peer_ovpn."
                "upload_bound_user_ovpn_via_ssh",
                return_value=dict(_PUSH_OK),
            ):
                with patch(
                    "app.services.peer_instance.remote_peer_ovpn."
                    "deploy_openvpn_client_systemd_via_ssh",
                    return_value=dict(_SYSTEMD_OK),
                ):
                    with patch.object(
                        PeerService,
                        "deploy_peer_site_firewall_via_ssh",
                        side_effect=RuntimeError("iptables denied"),
                    ):
                        with pytest.raises(RuntimeError, match="iptables"):
                            PeerService().ensure_openvpn_on_peer_via_ssh(
                                _ROW["id"]
                            )


def test_ensure_openvpn_raises_when_ssh_fails():
    probe_fail = {
        "connected": False,
        "ssh_error": "SSH 认证失败: x",
        "installed": False,
    }
    with patch.object(PeerService, "get", return_value=dict(_ROW)):
        with patch.object(
            PeerService,
            "probe_openvpn_via_ssh",
            return_value=probe_fail,
        ):
            with pytest.raises(RuntimeError, match="SSH"):
                PeerService().ensure_openvpn_on_peer_via_ssh(_ROW["id"])


def test_probe_openvpn_uses_global_ssh_key_when_peer_key_empty():
    peer_id = "p5"
    row = {
        "id": peer_id,
        "ssh_host": "h",
        "ssh_port": 22,
        "ssh_username": "u",
        "ssh_password": "",
    }
    cfg = {
        "global_ssh_private_key": "GLOBAL_PEM",
        "global_ssh_private_key_passphrase": "pp",
    }
    probe_ret = {
        "connected": True,
        "installed": True,
        "meets_requirement": True,
    }
    with patch.object(PeerService, "get", return_value=row):
        with patch("app.core.config.load_config", return_value=cfg):
            with patch(
                "app.services.peer_instance.remote_openvpn."
                "detect_openvpn_via_ssh",
                return_value=probe_ret,
            ) as m_detect:
                out = PeerService().probe_openvpn_via_ssh(peer_id)

    assert out == probe_ret
    assert m_detect.call_args.kwargs["private_key_text"] == "GLOBAL_PEM"
    assert m_detect.call_args.kwargs["private_key_passphrase"] == "pp"
