# -*- coding: utf-8 -*-
"""OpenVPN 安全升级与 preflight 测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.openvpn import upgrade as mod


def test_upgrade_module_has_no_distribution_package_install_logic() -> None:
    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "_install_build_dependencies" not in source
    assert '["apt", "install"' not in source
    assert 'shutil.which("dnf")' not in source
    assert "openvpn-install-common.sh" not in source
    assert "_write_candidate_systemd_unit" not in source
    assert 'write_text("[Service]' not in source
    assert "ReadWritePaths=/etc/openvpn" not in source
    assert "stage_openvpn_source_build" in source
    assert "install_openvpn_server_systemd_from_staged_source" in source


def test_upgrade_installer_output_is_mirrored_to_upgrade_logger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logged: list[str] = []
    forwarded: list[str] = []

    monkeypatch.setattr(mod.logger, "info", lambda message: logged.append(str(message)))
    sink = mod._mirror_installer_output(forwarded.append)
    sink("compile-line")

    assert logged == ["compile-line"]
    assert forwarded == ["compile-line"]


def test_preflight_output_state_requires_explicit_marker_and_fatal_wins() -> None:
    client_ready = "UDPv4 link remote: [AF_INET]127.0.0.1:12345"
    assert mod._preflight_output_state(
        client_ready,
        mod._CLIENT_PREFLIGHT_SUCCESS_MARKERS,
    ) == (True, False)

    assert mod._preflight_output_state(
        "OpenVPN started and waiting",
        mod._SERVER_PREFLIGHT_SUCCESS_MARKERS,
    ) == (False, False)

    assert mod._preflight_output_state(
        client_ready + "\nExiting due to fatal error",
        mod._CLIENT_PREFLIGHT_SUCCESS_MARKERS,
    ) == (True, True)

    assert mod._preflight_output_state(
        "Initialization Sequence Completed",
        mod._SERVER_PREFLIGHT_SUCCESS_MARKERS,
    ) == (True, False)


def test_rewrite_config_keeps_inline_material_and_disables_side_effects() -> None:
    source = """client
remote vpn.example.com 1194
dev tun
<cert>
-----BEGIN CERTIFICATE-----
remote should-not-be-rewritten
-----END CERTIFICATE-----
</cert>
log-append /var/log/openvpn.log
"""
    result = mod._rewrite_config(
        source,
        disabled_directives={"remote", "dev", "log-append"},
        replacements={},
        append_lines=["dev tunpf123", "remote 127.0.0.1 9"],
    )
    assert "# NiceVPN preflight disabled: remote vpn.example.com 1194" in result
    assert "# NiceVPN preflight disabled: dev tun" in result
    assert "# NiceVPN preflight disabled: log-append /var/log/openvpn.log" in result
    assert "remote should-not-be-rewritten" in result
    assert result.endswith("dev tunpf123\nremote 127.0.0.1 9\n")


def test_preflight_upgrade_requires_every_server_and_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    (server_dir / "a.conf").write_text("server 10.8.0.0 255.255.255.0\n", encoding="utf-8")
    (server_dir / "b.conf").write_text("server 10.9.0.0 255.255.255.0\n", encoding="utf-8")
    binary = tmp_path / "openvpn"
    binary.write_text("fake", encoding="utf-8")

    monkeypatch.setattr(mod, "load_config", lambda: {"openvpn_conf_dir": str(tmp_path)})
    monkeypatch.setattr(
        mod,
        "preflight_server_config",
        lambda _binary, path: {
            "ok": Path(path).name != "b.conf",
            "path": str(path),
        },
    )
    client_called = []
    monkeypatch.setattr(
        mod,
        "preflight_client_profile",
        lambda _binary: client_called.append(True) or {"ok": True},
    )

    result = mod.preflight_upgrade_candidate({"binary": str(binary)})
    assert result["ok"] is False
    assert result["error"] == "服务端配置不兼容: b.conf"
    assert len(result["server_results"]) == 2
    assert client_called == []


def test_preflight_upgrade_reports_temporary_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    (server_dir / "server.conf").write_text("server 10.8.0.0 255.255.255.0\n", encoding="utf-8")
    binary = tmp_path / "openvpn"
    binary.write_text("fake", encoding="utf-8")

    monkeypatch.setattr(mod, "load_config", lambda: {"openvpn_conf_dir": str(tmp_path)})
    monkeypatch.setattr(
        mod,
        "preflight_server_config",
        lambda _binary, path: {"ok": True, "path": str(path)},
    )
    monkeypatch.setattr(
        mod,
        "preflight_client_profile",
        lambda _binary: {
            "ok": True,
            "profile": "/tmp/test.ovpn",
            "generated": True,
            "source": "temporary",
        },
    )

    result = mod.preflight_upgrade_candidate({"binary": str(binary)})
    assert result["ok"] is True
    assert result["client_result"]["generated"] is True
    assert result["client_result"]["source"] == "temporary"


def test_apply_staged_upgrade_stops_before_backup_when_preflight_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        mod,
        "preflight_upgrade_candidate",
        lambda _stage, _output=None: {"ok": False, "error": "客户端配置不兼容"},
    )

    with pytest.raises(RuntimeError, match="客户端配置不兼容"):
        mod.apply_staged_upgrade(
            {
                "binary": str(tmp_path / "openvpn"),
                "version": "9.9.9",
            }
        )


def test_client_preflight_always_generates_temporary_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    generated = tmp_path / "generated.ovpn"
    generated.write_text("client\n", encoding="utf-8")
    calls: list[Path] = []

    def fake_generate(_binary: str, root: Path) -> Path:
        calls.append(root)
        target = root / "generated.ovpn"
        target.write_text("client\n", encoding="utf-8")
        return target

    monkeypatch.setattr(mod, "_generate_ephemeral_client_profile", fake_generate)
    monkeypatch.setattr(mod, "_free_local_port", lambda: 12345)
    monkeypatch.setattr(
        mod,
        "_run_foreground_preflight",
        lambda _binary, _config, **kwargs: (
            kwargs.get("success_markers") == mod._CLIENT_PREFLIGHT_SUCCESS_MARKERS,
            "UDPv4 link remote: [AF_INET]127.0.0.1:12345",
            True,
        ),
    )

    result = mod.preflight_client_profile("/tmp/candidate-openvpn")

    assert len(calls) == 1
    assert result["ok"] is True
    assert result["generated"] is True
    assert result["source"] == "temporary"


def test_upgrade_module_does_not_scan_existing_client_profiles() -> None:
    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "OVPN_PROFILES_DIR" not in source
    assert "_find_existing_client_profile" not in source
    assert "glob(\"*.ovpn\")" not in source
