# -*- coding: utf-8 -*-
"""验证管理后台会话签名密钥生命周期。"""

import os
import stat
from pathlib import Path

from box import Box

import main
from app.utils.session_secret import load_or_create_session_secret


def test_session_secret_is_generated_once_and_survives_restart(tmp_path: Path) -> None:
    """首次生成后重复加载保持同一密钥，避免重启使会话全部失效。"""

    path = tmp_path / "data" / "admin_session_secret"
    first = load_or_create_session_secret(path)
    second = load_or_create_session_secret(path)

    assert first == second
    assert len(first) >= 64
    assert path.read_text(encoding="utf-8").strip() == first
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0
    assert list(path.parent.glob(".admin_session_secret_*.tmp")) == []


def test_invalid_existing_session_secret_fails_without_replacement(tmp_path: Path) -> None:
    """损坏的密钥文件应明确失败，不能静默轮换并破坏有效会话。"""

    path = tmp_path / "admin_session_secret"
    path.write_text("too-short\n", encoding="utf-8")

    try:
        load_or_create_session_secret(path)
    except RuntimeError as exc:
        assert "会话签名密钥文件无效" in str(exc)
    else:
        raise AssertionError("无效会话密钥未触发异常")

    assert path.read_text(encoding="utf-8") == "too-short\n"


def test_auth_disabled_does_not_initialize_session_storage(monkeypatch) -> None:
    """未配置认证时不生成会话密钥，也不为 NiceGUI 启用会话存储。"""

    run_arguments: dict[str, object] = {}
    monkeypatch.setattr(main, "setup_logging", lambda: None)
    monkeypatch.setattr(main, "set_listen_http_base", lambda *args: None)
    monkeypatch.setattr(
        main,
        "init",
        lambda: Box(
            {"admin_username": None, "admin_password_hash": None},
            default_box=True,
            default_box_attr=None,
        ),
    )
    monkeypatch.setattr(
        main,
        "load_or_create_session_secret",
        lambda: (_ for _ in ()).throw(AssertionError("不应生成会话密钥")),
    )
    monkeypatch.setattr(main.ui, "run", lambda **kwargs: run_arguments.update(kwargs))

    main.run_web()

    assert run_arguments["storage_secret"] is None
    assert run_arguments["session_middleware_kwargs"] is None
    assert run_arguments["reconnect_timeout"] == 300.0


def test_auth_enabled_uses_persistent_session_secret(monkeypatch) -> None:
    """完整认证配置为 NiceGUI 注入独立的持久会话密钥。"""

    run_arguments: dict[str, object] = {}
    monkeypatch.setattr(main, "setup_logging", lambda: None)
    monkeypatch.setattr(main, "set_listen_http_base", lambda *args: None)
    monkeypatch.setattr(
        main,
        "init",
        lambda: Box(
            {
                "admin_username": "admin",
                "admin_password_hash": "$argon2id$valid",
                "admin_session_lifetime_minutes": 60,
            },
            default_box=True,
            default_box_attr=None,
        ),
    )
    monkeypatch.setattr(main, "load_or_create_session_secret", lambda: "stable-secret")
    monkeypatch.setattr(main.ui, "run", lambda **kwargs: run_arguments.update(kwargs))

    main.run_web()

    assert run_arguments["storage_secret"] == "stable-secret"
    assert run_arguments["session_middleware_kwargs"] == {
        "max_age": 3600,
        "same_site": "lax",
        "https_only": False,
    }
    assert run_arguments["reconnect_timeout"] == 300.0
