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
