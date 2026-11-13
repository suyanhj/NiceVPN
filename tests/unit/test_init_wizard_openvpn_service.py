# -*- coding: utf-8 -*-
"""初始化向导检测 OpenVPN 时补齐 systemd 服务单元的回归测试。"""

from app.core import init_wizard as wizard_module
from app.core.init_wizard import InitWizard, WizardStep


class Config(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value


class Audit:
    def log(self, *_args, **_kwargs) -> None:
        pass


def _wizard() -> InitWizard:
    wizard = InitWizard.__new__(InitWizard)
    wizard.config = Config()
    wizard.audit = Audit()
    return wizard


def test_detect_openvpn_ensures_server_unit_before_continuing(
    monkeypatch,
) -> None:
    binary = "/opt/openvpn/sbin/openvpn"
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        wizard_module,
        "detect_openvpn",
        lambda: {
            "installed": True,
            "path": binary,
            "version": "2.7.8",
            "meets_requirement": True,
            "distro": {},
        },
    )
    monkeypatch.setattr(wizard_module, "find_easyrsa", lambda _path: "/opt/easy-rsa/current/easyrsa")
    monkeypatch.setattr(wizard_module, "save_config", lambda _config: None)

    from app.services.openvpn import installer as installer_module

    monkeypatch.setattr(
        installer_module,
        "ensure_openvpn_runtime_dependencies",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        installer_module,
        "ensure_openvpn_server_systemd_unit",
        lambda path, openvpn_version=None, on_output=None: calls.append((path, openvpn_version, on_output)) or "openvpn-server@.service",
    )

    wizard = _wizard()
    logs: list[str] = []
    result = wizard._detect_openvpn({"on_output": logs.append})

    assert result.success is True
    assert result.next_step == WizardStep.CONFIG_PKI
    assert calls == [(binary, "2.7.8", logs.append)]
    assert wizard.config.openvpn_bin == binary


def test_detect_openvpn_stops_when_server_unit_cannot_be_installed(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        wizard_module,
        "detect_openvpn",
        lambda: {
            "installed": True,
            "path": "/usr/local/sbin/openvpn",
            "version": "2.7.8",
            "meets_requirement": True,
            "distro": {},
        },
    )
    monkeypatch.setattr(wizard_module, "find_easyrsa", lambda _path: None)
    monkeypatch.setattr(
        "app.services.openvpn.installer.ensure_openvpn_runtime_dependencies",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.openvpn.installer.ensure_openvpn_server_systemd_unit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("权限不足")),
    )

    wizard = _wizard()
    result = wizard._detect_openvpn({})

    assert result.success is False
    assert "服务文件检查/安装失败" in result.message
    assert "权限不足" in result.message
    assert wizard.config.get("openvpn_bin") is None
