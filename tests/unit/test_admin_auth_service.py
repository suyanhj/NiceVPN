# -*- coding: utf-8 -*-
"""验证单管理员认证服务和凭据生成命令。"""

import argparse
import logging

from app.cli import entry
from app.models.config import SystemConfig
from app.services.auth import AdminAuthService
from app.services.auth import AdminLoginManager, LoginAttemptLimiter


def test_password_hash_is_argon2id_and_matches_config_model() -> None:
    """服务生成标准 Argon2id 哈希，配置模型可直接接受。"""

    service = AdminAuthService()
    password_hash = service.hash_password("correct horse battery staple")
    config = SystemConfig(
        admin_username="admin",
        admin_password_hash=password_hash,
    )

    assert password_hash.startswith("$argon2id$")
    assert config.admin_auth_enabled is True
    assert service.verify_credentials(
        submitted_username="admin",
        submitted_password="correct horse battery staple",
        configured_username=config.admin_username,
        configured_password_hash=config.admin_password_hash,
    )


def test_wrong_username_and_wrong_password_share_false_result() -> None:
    """错误用户名与错误密码只返回统一失败结果。"""

    service = AdminAuthService()
    password_hash = service.hash_password("expected-password")
    common = {
        "configured_username": "admin",
        "configured_password_hash": password_hash,
    }

    wrong_username = service.verify_credentials(
        submitted_username="other",
        submitted_password="expected-password",
        **common,
    )
    wrong_password = service.verify_credentials(
        submitted_username="admin",
        submitted_password="wrong-password",
        **common,
    )
    assert wrong_username is False
    assert wrong_password is False


def test_invalid_hash_returns_false_without_leaking_credentials() -> None:
    """异常哈希仅返回失败，不把提交值写入日志。"""

    service = AdminAuthService()
    assert service.verify_credentials(
        submitted_username="admin",
        submitted_password="do-not-log-this",
        configured_username="admin",
        configured_password_hash="invalid",
    ) is False


def test_cli_outputs_only_hash_and_never_plaintext(
    monkeypatch,
    capsys,
    caplog,
) -> None:
    """交互命令输出哈希，终端和日志都不回显明文密码。"""

    plaintext = "never-print-this-password"
    answers = iter((plaintext, plaintext))
    monkeypatch.setattr(entry.getpass, "getpass", lambda _: next(answers))
    caplog.set_level(logging.INFO)

    result = entry.cmd_hash_admin_password(argparse.Namespace())
    captured = capsys.readouterr()

    assert result == 0
    assert captured.out.strip().startswith("$argon2id$")
    assert plaintext not in captured.out
    assert plaintext not in captured.err
    assert plaintext not in caplog.text
    assert captured.out.strip() not in caplog.text


def test_cli_rejects_confirmation_mismatch_without_echoing_passwords(
    monkeypatch,
    capsys,
) -> None:
    """两次输入不一致时仅显示通用错误。"""

    answers = iter(("first-secret", "second-secret"))
    monkeypatch.setattr(entry.getpass, "getpass", lambda _: next(answers))

    result = entry.cmd_hash_admin_password(argparse.Namespace())
    captured = capsys.readouterr()

    assert result == 1
    assert "不一致" in captured.err
    assert "first-secret" not in captured.err
    assert "second-secret" not in captured.err


def test_cli_parser_registers_hash_admin_password_command() -> None:
    """凭据生成命令通过现有 CLI 入口可达。"""

    args = entry.build_parser().parse_args(["hash-admin-password"])
    assert args.func is entry.cmd_hash_admin_password


def test_login_failure_limit_recovers_after_window() -> None:
    """同一来源达到失败阈值后受限，窗口结束后自动恢复。"""

    current = [100.0]
    limiter = LoginAttemptLimiter(
        max_failures=3,
        window_seconds=60,
        clock=lambda: current[0],
    )
    for _ in range(3):
        assert limiter.allows_attempt("10.0.0.1") is True
        limiter.record_failure("10.0.0.1")
    assert limiter.allows_attempt("10.0.0.1") is False
    assert limiter.allows_attempt("10.0.0.2") is True

    current[0] = 161.0
    assert limiter.allows_attempt("10.0.0.1") is True


def test_login_manager_establishes_minimal_expiring_session() -> None:
    """正确凭据建立最小会话，成功后清除来源失败记录。"""

    service = AdminAuthService()
    password_hash = service.hash_password("expected-password")
    current = [50.0]
    limiter = LoginAttemptLimiter(
        max_failures=2,
        window_seconds=60,
        clock=lambda: current[0],
    )
    manager = AdminLoginManager(
        configured_username="admin",
        configured_password_hash=password_hash,
        session_lifetime_seconds=3600,
        auth_service=service,
        limiter=limiter,
        wall_clock=lambda: 1000.0,
    )
    session = {"redirect_after_login": "/users", "unrelated": "remove"}

    failed = manager.attempt_login(
        session,
        submitted_username="admin",
        submitted_password="wrong",
        source="10.0.0.1",
    )
    assert failed.success is False
    assert session == {"redirect_after_login": "/users", "unrelated": "remove"}

    success = manager.attempt_login(
        session,
        submitted_username="admin",
        submitted_password="expected-password",
        source="10.0.0.1",
    )
    assert success.success is True
    assert session == {
        "authenticated": True,
        "username": "admin",
        "expires_at": 4600.0,
    }
    assert limiter.allows_attempt("10.0.0.1") is True
