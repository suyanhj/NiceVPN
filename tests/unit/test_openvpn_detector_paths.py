# -*- coding: utf-8 -*-
"""OpenVPN/EasyRSA 共用可执行文件查找逻辑的回归测试。"""

from pathlib import Path

import pytest

from app.services.openvpn import detector


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_openvpn_preferred_path_does_not_invoke_path_lookup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    binary = _touch(tmp_path / "opt" / "openvpn" / "sbin" / "openvpn")
    monkeypatch.setattr(detector, "OPENVPN_SEARCH_PATHS", (str(binary),))
    monkeypatch.setattr(
        detector.shutil, "which",
        lambda _cmd: (_ for _ in ()).throw(AssertionError("不应执行 PATH 查找")),
    )

    assert detector._find_openvpn_binary() == str(binary.resolve())


def test_openvpn_uses_shutil_which_instead_of_subprocess(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    binary = _touch(tmp_path / "usr" / "local" / "sbin" / "openvpn")
    monkeypatch.setattr(detector, "OPENVPN_SEARCH_PATHS", ())
    monkeypatch.setattr(
        detector.shutil, "which", lambda name: str(binary) if name == "openvpn" else None,
    )
    monkeypatch.setattr(
        detector.subprocess, "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("检测 PATH 不应启动外部命令")
        ),
    )

    assert detector._find_openvpn_binary() == str(binary.resolve())


def test_easyrsa_checks_neighboring_openvpn_prefix_before_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    binary = _touch(tmp_path / "opt" / "openvpn" / "sbin" / "openvpn")
    easyrsa = _touch(tmp_path / "opt" / "openvpn" / "share" / "easy-rsa" / "easyrsa")
    monkeypatch.setattr(detector, "EASYRSA_SEARCH_PATHS", ())
    monkeypatch.setattr(
        detector.shutil, "which",
        lambda _cmd: (_ for _ in ()).throw(AssertionError("应优先使用相邻 EasyRSA")),
    )

    assert detector.find_easyrsa(str(binary)) == str(easyrsa.resolve())


def test_easyrsa_uses_same_path_lookup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    easyrsa = _touch(tmp_path / "bin" / "easyrsa")
    monkeypatch.setattr(detector, "EASYRSA_SEARCH_PATHS", ())
    monkeypatch.setattr(
        detector.shutil, "which",
        lambda name: str(easyrsa) if name == "easyrsa" else None,
    )

    assert detector.find_easyrsa() == str(easyrsa.resolve())


def test_nonexistent_path_lookup_does_not_return_invalid_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(detector, "OPENVPN_SEARCH_PATHS", ())
    monkeypatch.setattr(detector, "EASYRSA_SEARCH_PATHS", ())
    monkeypatch.setattr(
        detector.shutil, "which",
        lambda _cmd: str(tmp_path / "missing"),
    )

    assert detector._find_openvpn_binary() is None
    assert detector.find_easyrsa() is None


def test_preferred_paths_require_executable_permission(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    openvpn = tmp_path / "openvpn"
    easyrsa = tmp_path / "easyrsa"
    openvpn.write_text("#!/bin/sh\n", encoding="utf-8")
    easyrsa.write_text("#!/bin/sh\n", encoding="utf-8")
    openvpn.chmod(0o644)
    easyrsa.chmod(0o644)

    monkeypatch.setattr(detector, "OPENVPN_SEARCH_PATHS", (str(openvpn),))
    monkeypatch.setattr(detector, "EASYRSA_SEARCH_PATHS", (str(easyrsa),))
    monkeypatch.setattr(detector.shutil, "which", lambda _cmd: None)

    assert detector._find_openvpn_binary() is None
    assert detector.find_easyrsa() is None
