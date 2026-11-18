# -*- coding: utf-8 -*-
"""OpenVPN 安全升级与 preflight 测试。"""

from __future__ import annotations

import os
import subprocess
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
    assert "apply_openvpn_release_upgrade" in source
    assert "install_openvpn_server_systemd_from_staged_source" not in source
    assert "create_full_backup" not in source
    assert ".nicevpn-upgrade-transaction" not in source


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


def test_apply_staged_upgrade_stops_before_shell_switch_when_preflight_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    current_binary = _write_openvpn_binary(tmp_path / "current" / "sbin" / "openvpn", "2.7.8")
    stage_binary = _write_openvpn_binary(tmp_path / "stage" / "sbin" / "openvpn", "9.9.9")
    monkeypatch.setattr(
        mod,
        "detect_openvpn",
        lambda: {
            "install_mode": "source",
            "path": str(current_binary),
            "version": "2.7.8",
        },
    )
    monkeypatch.setattr(
        mod,
        "preflight_upgrade_candidate",
        lambda _stage, _output=None: {"ok": False, "error": "客户端配置不兼容"},
    )
    monkeypatch.setattr(
        "app.services.openvpn.installer.apply_openvpn_release_upgrade",
        lambda **_kwargs: pytest.fail("preflight 失败时不能切换 release"),
    )

    with pytest.raises(RuntimeError, match="客户端配置不兼容"):
        mod.apply_staged_upgrade(
            {
                "binary": str(stage_binary),
                "prefix": str(stage_binary.parent.parent),
                "version": "9.9.9",
                "base_binary_realpath": str(current_binary.resolve()),
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



def _write_openvpn_binary(path: Path, version: str = "2.7.8") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"#!/bin/sh\nprintf 'OpenVPN {version}\n'\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _run_openvpn_upgrade_shell(
    tmp_path: Path,
    *,
    legacy_prefix: bool = False,
    fail_first_restart: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path]:
    root = tmp_path / "opt" / "openvpn"
    prefix = root / "current"
    old_release = root / "2.7.8"
    root.mkdir(parents=True)
    if legacy_prefix:
        old_root = prefix
    else:
        old_root = old_release
        prefix.symlink_to(old_release.name)
    current_binary = _write_openvpn_binary(old_root / "sbin" / "openvpn", "2.7.8")
    stage_prefix = tmp_path / "candidate"
    _write_openvpn_binary(stage_prefix / "sbin" / "openvpn", "2.8.0")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log_path = tmp_path / "systemctl.log"
    systemctl = fake_bin / "systemctl"
    systemctl.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        '  list-units) printf "%s\\n" "openvpn-server@server.service loaded active running OpenVPN Server" ;;\n'
        '  show) printf "0\\n" ;;\n'
        '  restart) count=0; [ ! -f "$SYSTEMCTL_LOG" ] || count=$(wc -l < "$SYSTEMCTL_LOG"); '
        'echo "$2" >> "$SYSTEMCTL_LOG"; '
        'if [ "$FAIL_FIRST_RESTART" = "1" ] && [ "$count" -eq 0 ]; then exit 1; fi ;;\n'
        '  is-active) exit 0 ;;\n'
        '  *) exit 0 ;;\n'
        'esac\n',
        encoding="utf-8",
    )
    systemctl.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env.get('PATH', '')}",
            "OPENVPN_ROOT_DIR": str(root),
            "OPENVPN_PREFIX": str(prefix),
            "OPENVPN_BIN": str(prefix / "sbin" / "openvpn"),
            "OPENVPN_STAGE_PREFIX": str(stage_prefix),
            "OPENVPN_EXPECTED_VERSION": "2.8.0",
            "OPENVPN_EXPECTED_CURRENT_BINARY": str(current_binary.resolve()),
            "INSTALL_SERVER_SYSTEMD": "1",
            "INSTALL_CLIENT_SYSTEMD": "0",
            "INSTALL_SERVER_OVERRIDE": "0",
            "INSTALL_EASYRSA": "0",
            "SYSTEMCTL_LOG": str(log_path),
            "FAIL_FIRST_RESTART": "1" if fail_first_restart else "0",
        }
    )
    script = Path(mod.__file__).resolve().parents[2] / "scripts" / "install" / "openvpn-upgrade.sh"
    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return result, prefix, root, log_path


def test_shell_upgrade_switches_current_and_keeps_previous_version(
    tmp_path: Path,
) -> None:
    result, prefix, root, log_path = _run_openvpn_upgrade_shell(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert prefix.is_symlink()
    assert prefix.resolve().parent == root
    assert prefix.readlink() == Path("2.8.0")
    assert (root / "2.7.8" / "sbin" / "openvpn").is_file()
    assert (root / "2.8.0" / "sbin" / "openvpn").is_file()
    assert not (root / "sbin").exists() and not (root / "sbin").is_symlink()
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        "openvpn-server@server.service"
    ]


def test_upgrade_rejects_legacy_plain_current_directory(
    tmp_path: Path,
) -> None:
    result, prefix, root, _ = _run_openvpn_upgrade_shell(
        tmp_path,
        legacy_prefix=True,
    )

    assert result.returncode != 0
    assert prefix.is_dir() and not prefix.is_symlink()
    assert (prefix / "sbin" / "openvpn").is_file()
    assert "OpenVPN current 必须是软链接" in result.stderr


def test_shell_upgrade_failure_switches_back_and_restarts_old_instance(
    tmp_path: Path,
) -> None:
    result, prefix, root, log_path = _run_openvpn_upgrade_shell(
        tmp_path,
        fail_first_restart=True,
    )

    assert result.returncode != 0
    assert prefix.readlink() == Path("2.7.8")
    assert (root / "2.7.8" / "sbin" / "openvpn").is_file()
    assert len(log_path.read_text(encoding="utf-8").splitlines()) == 2
    assert "切回旧版本" in result.stderr


def test_shell_upgrade_does_not_rewrite_systemd_or_vpn_configuration() -> None:
    script = (
        Path(mod.__file__).resolve().parents[2]
        / "scripts"
        / "install"
        / "openvpn-upgrade.sh"
    ).read_text(encoding="utf-8")
    assert "/etc/systemd/system" not in script
    assert "/usr/local/sbin/openvpn" not in script
    assert "systemctl daemon-reload" not in script
    assert "create_full_backup" not in script


def test_package_install_is_not_offered_source_upgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mod,
        "detect_openvpn",
        lambda: {
            "installed": True,
            "path": "/usr/sbin/openvpn",
            "version": "2.7.8",
            "install_mode": "package",
        },
    )
    monkeypatch.setattr(
        "app.services.openvpn.installer.resolve_latest_openvpn_release",
        lambda **_kwargs: pytest.fail("系统包安装不应触发源码 release 查询"),
    )

    result = mod.inspect_upgrade()

    assert result["upgrade_available"] is False
    assert result["release"] is None
    assert "包管理器" in result["reason"]


def test_upgrade_rejects_candidate_staged_from_a_different_current_release(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    current_binary = _write_openvpn_binary(tmp_path / "current" / "sbin" / "openvpn", "2.7.8")
    stage_binary = _write_openvpn_binary(tmp_path / "stage" / "sbin" / "openvpn", "2.8.0")
    other_binary = _write_openvpn_binary(tmp_path / "other" / "sbin" / "openvpn", "2.7.8")
    monkeypatch.setattr(
        mod,
        "detect_openvpn",
        lambda: {
            "install_mode": "source",
            "version": "2.7.8",
            "path": str(other_binary),
        },
    )
    monkeypatch.setattr(
        mod,
        "preflight_upgrade_candidate",
        lambda *_args, **_kwargs: pytest.fail("过期候选不应进入 preflight"),
    )

    with pytest.raises(RuntimeError, match="候选 OpenVPN 已过期"):
        mod._apply_staged_upgrade_locked(
            {
                "binary": str(stage_binary),
                "prefix": str(stage_binary.parent.parent),
                "version": "2.8.0",
                "base_binary_realpath": str(current_binary.resolve()),
            }
        )
