# -*- coding: utf-8 -*-
"""EasyRSA PKI 自动检测、复用与防覆盖测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.init_wizard import InitWizard
from app.services.easyrsa.wrapper import (
    EasyRSAError,
    EasyRSAWrapper,
    find_existing_easyrsa_pki,
    inspect_easyrsa_pki,
)


def _write_ready_pki(pki: Path) -> None:
    (pki / "private").mkdir(parents=True, exist_ok=True)
    (pki / "issued").mkdir(parents=True, exist_ok=True)
    (pki / "reqs").mkdir(parents=True, exist_ok=True)
    (pki / "ca.crt").write_text("ca-cert", encoding="utf-8")
    (pki / "private" / "ca.key").write_text("ca-key", encoding="utf-8")
    (pki / "index.txt").write_text("", encoding="utf-8")
    (pki / "serial").write_text("01\n", encoding="utf-8")


def test_inspect_easyrsa_pki_distinguishes_states(tmp_path: Path) -> None:
    pki = tmp_path / "pki"
    assert inspect_easyrsa_pki(str(pki))["state"] == "missing"

    pki.mkdir()
    (pki / "openssl-easyrsa.cnf").write_text("config", encoding="utf-8")
    assert inspect_easyrsa_pki(str(pki))["state"] == "initialized"

    (pki / "ca.crt").write_text("ca-cert", encoding="utf-8")
    state = inspect_easyrsa_pki(str(pki))
    assert state["state"] == "partial"
    assert "private/ca.key" in state["missing"]

    (pki / "private").mkdir(exist_ok=True)
    (pki / "private" / "ca.key").write_text("ca-key", encoding="utf-8")
    (pki / "index.txt").write_text("", encoding="utf-8")
    (pki / "serial").write_text("01\n", encoding="utf-8")
    assert inspect_easyrsa_pki(str(pki))["state"] == "ready"


def test_find_existing_easyrsa_pki_supports_common_openvpn_layout(
    tmp_path: Path,
) -> None:
    pki = tmp_path / "easy-rsa" / "pki"
    _write_ready_pki(pki)

    assert find_existing_easyrsa_pki(str(tmp_path)) == str(pki)


def test_wrapper_init_pki_does_not_touch_ready_pki(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pki = tmp_path / "pki"
    _write_ready_pki(pki)
    wrapper = EasyRSAWrapper("/fake/easyrsa", str(pki))

    calls: list[list[str]] = []
    monkeypatch.setattr(
        wrapper,
        "_run",
        lambda args, **_kwargs: calls.append(list(args)),
    )

    assert wrapper.init_pki() is True
    assert wrapper.build_ca() is True
    assert calls == []


def test_core_wizard_reuses_complete_pki_without_easyrsa_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pki = tmp_path / "pki"
    _write_ready_pki(pki)
    (pki / "dh.pem").write_text("dh", encoding="utf-8")
    (pki / "issued" / "server.crt").write_text(
        "server-cert",
        encoding="utf-8",
    )
    (pki / "private" / "server.key").write_text(
        "server-key",
        encoding="utf-8",
    )
    (pki / "crl.pem").write_text("crl", encoding="utf-8")
    (tmp_path / "tc2-server.key").write_text("tc2", encoding="utf-8")

    class Config(dict):
        __getattr__ = dict.get

        def __setattr__(self, key, value):
            self[key] = value

    class Audit:
        def log(self, *_args, **_kwargs) -> None:
            pass

    wizard = InitWizard.__new__(InitWizard)
    wizard.config = Config(
        easyrsa_dir="/fake/easyrsa",
        pki_dir=str(pki),
    )
    wizard.audit = Audit()

    monkeypatch.setattr(
        "app.services.openvpn.installer.ensure_easyrsa_runtime_dependencies",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.core.init_wizard.save_config",
        lambda _config: None,
    )
    monkeypatch.setattr(
        "app.core.init_wizard.sync_openvpn_runtime_permissions_from_config",
        lambda: None,
    )
    monkeypatch.setattr(
        EasyRSAWrapper,
        "_run",
        lambda *_args, **_kwargs: pytest.fail(
            "完整 PKI 不应调用 EasyRSA 命令"
        ),
    )
    monkeypatch.setattr(
        EasyRSAWrapper,
        "gen_tls_crypt_v2_server",
        lambda *_args, **_kwargs: pytest.fail(
            "已有 tc2-server.key 不应重新生成"
        ),
    )

    result = wizard._config_pki({})

    assert result.success is True
    assert result.message == "已复用现有 PKI"
    assert result.data["reused_pki"] is True
    assert result.data["generated"] == []


def test_core_wizard_auto_installs_missing_easyrsa_then_reuses_pki(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pki = tmp_path / "pki"
    _write_ready_pki(pki)
    (pki / "dh.pem").write_text("dh", encoding="utf-8")
    (pki / "issued" / "server.crt").write_text("server-cert", encoding="utf-8")
    (pki / "private" / "server.key").write_text("server-key", encoding="utf-8")
    (pki / "crl.pem").write_text("crl", encoding="utf-8")
    (tmp_path / "tc2-server.key").write_text("tc2", encoding="utf-8")

    easyrsa_bin = tmp_path / "easy-rsa" / "easyrsa"
    easyrsa_bin.parent.mkdir(parents=True)
    easyrsa_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    easyrsa_bin.chmod(0o755)

    class Config(dict):
        __getattr__ = dict.get

        def __setattr__(self, key, value):
            self[key] = value

    class Audit:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        def log(self, *args, **_kwargs) -> None:
            self.calls.append(args)

    wizard = InitWizard.__new__(InitWizard)
    wizard.config = Config(
        easyrsa_dir=None,
        openvpn_bin="/usr/sbin/openvpn",
        pki_dir=str(pki),
    )
    wizard.audit = Audit()

    install_calls: list[dict] = []

    def fake_install(*, on_output=None) -> str:
        install_calls.append({})
        if on_output:
            on_output("[完成] EasyRSA 安装成功")
        return str(easyrsa_bin)

    monkeypatch.setattr(
        "app.core.init_wizard.find_easyrsa",
        lambda _openvpn_bin=None: None,
    )
    monkeypatch.setattr(
        "app.services.openvpn.installer.install_easyrsa",
        fake_install,
    )
    monkeypatch.setattr(
        "app.services.openvpn.installer.ensure_easyrsa_runtime_dependencies",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.core.init_wizard.save_config",
        lambda _config: None,
    )
    monkeypatch.setattr(
        "app.core.init_wizard.sync_openvpn_runtime_permissions_from_config",
        lambda: None,
    )

    logs: list[str] = []
    result = wizard._config_pki({"on_output": logs.append})

    assert result.success is True
    assert result.message == "已复用现有 PKI"
    assert install_calls == [{}]
    assert wizard.config.easyrsa_dir == str(easyrsa_bin)
    assert any("未找到 EasyRSA，开始自动安装" in line for line in logs)
    assert any("已检测到 EasyRSA" in line for line in logs)


def test_core_wizard_reports_missing_easyrsa_install_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Config(dict):
        __getattr__ = dict.get

        def __setattr__(self, key, value):
            self[key] = value

    class Audit:
        def log(self, *_args, **_kwargs) -> None:
            pass

    wizard = InitWizard.__new__(InitWizard)
    wizard.config = Config(
        easyrsa_dir=None,
        openvpn_bin="/usr/sbin/openvpn",
        pki_dir=str(tmp_path / "pki"),
    )
    wizard.audit = Audit()

    monkeypatch.setattr(
        "app.core.init_wizard.find_easyrsa",
        lambda _openvpn_bin=None: None,
    )
    monkeypatch.setattr(
        "app.services.openvpn.installer.install_easyrsa",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("download failed")),
    )

    logs: list[str] = []
    result = wizard._config_pki({"on_output": logs.append})

    assert result.success is False
    assert "EasyRSA 自动安装失败" in result.message
    assert "download failed" in result.message
    assert any("EasyRSA 自动安装失败" in line for line in logs)


def test_standalone_easyrsa_installer_uses_common_release_helper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.services.openvpn import installer as installer_mod

    install_root = tmp_path / "easy-rsa"
    monkeypatch.setattr(installer_mod, "EASYRSA_INSTALL_DIR", str(install_root))
    monkeypatch.setattr(installer_mod, "find_easyrsa", lambda: None)
    monkeypatch.setattr(
        installer_mod,
        "_resolve_latest_release_asset",
        lambda **_kwargs: {
            "tag_name": "v3.2.4",
            "asset_name": "EasyRSA-3.2.4.tgz",
            "download_url": "https://example.invalid/EasyRSA-3.2.4.tgz",
        },
    )
    monkeypatch.setattr(
        installer_mod,
        "_build_github_candidate_urls",
        lambda url: [url],
    )

    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(cmd, _output, *, env=None, cwd=None):
        calls.append((cmd, dict(env or {})))
        current = install_root / "current"
        current.mkdir(parents=True)
        easyrsa = current / "easyrsa"
        easyrsa.write_text("#!/bin/sh\\nexit 0\\n", encoding="utf-8")
        easyrsa.chmod(0o755)

    monkeypatch.setattr(installer_mod, "_run_command", fake_run)

    installed = installer_mod.install_easyrsa()

    assert installed == str(install_root / "current" / "easyrsa")
    assert len(calls) == 1
    assert calls[0][0][0] == "bash"
    assert calls[0][0][1].endswith("easyrsa-install.sh")
    assert calls[0][1]["INSTALL_EASYRSA"] == "1"
    assert calls[0][1]["EASYRSA_TAG"] == "v3.2.4"
    assert "OPENVPN_TAG" not in calls[0][1]


def test_standalone_easyrsa_shell_entry_does_not_install_openvpn() -> None:
    from app.services.openvpn.installer import _read_install_script_text

    script = _read_install_script_text("easyrsa-install.sh")
    assert "source" in script and "openvpn-install-common.sh" in script
    assert "easyrsa_download_and_install" in script
    assert "openvpn_compile_install" not in script
    assert "openvpn-install-debian.sh" not in script


def test_core_wizard_rejects_partial_ca_instead_of_overwriting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pki = tmp_path / "pki"
    pki.mkdir()
    (pki / "ca.crt").write_text("ca-cert", encoding="utf-8")

    class Config(dict):
        __getattr__ = dict.get

        def __setattr__(self, key, value):
            self[key] = value

    class Audit:
        def log(self, *_args, **_kwargs) -> None:
            pass

    wizard = InitWizard.__new__(InitWizard)
    wizard.config = Config(
        easyrsa_dir="/fake/easyrsa",
        pki_dir=str(pki),
    )
    wizard.audit = Audit()

    monkeypatch.setattr(
        EasyRSAWrapper,
        "_run",
        lambda *_args, **_kwargs: pytest.fail(
            "残缺 CA 不应被自动覆盖"
        ),
    )

    result = wizard._config_pki({})

    assert result.success is False
    assert "不完整的 EasyRSA PKI" in result.message
    assert "private/ca.key" in result.message



def test_wrapper_rejects_partial_ca_before_running_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pki = tmp_path / "pki"
    pki.mkdir()
    (pki / "ca.crt").write_text("ca-cert", encoding="utf-8")
    wrapper = EasyRSAWrapper("/fake/easyrsa", str(pki))

    monkeypatch.setattr(
        wrapper,
        "_run",
        lambda *_args, **_kwargs: pytest.fail(
            "残缺 PKI 必须在执行 EasyRSA 前被拦截"
        ),
    )

    with pytest.raises(EasyRSAError, match="不完整的 EasyRSA PKI"):
        wrapper.init_pki()


def test_core_wizard_rejects_relative_custom_pki_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Config(dict):
        __getattr__ = dict.get

    wizard = InitWizard.__new__(InitWizard)
    wizard.config = Config(easyrsa_dir="/fake/easyrsa")
    result = wizard._config_pki({"pki_dir": "relative/pki"})
    assert result.success is False
    assert "绝对路径" in result.message


def test_pki_path_status_supports_manual_directories(tmp_path: Path) -> None:
    from app.ui.pages.init_page import InitPage

    pki = tmp_path / "custom-pki"
    _write_ready_pki(pki)
    assert "复用 CA" in InitPage._pki_path_status(str(pki))
    assert "绝对路径" in InitPage._pki_path_status("relative/pki")
