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

    result = entry.cmd_admin_password_hash(argparse.Namespace())
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

    result = entry.cmd_admin_password_hash(argparse.Namespace())
    captured = capsys.readouterr()

    assert result == 1
    assert "不一致" in captured.err
    assert "first-secret" not in captured.err
    assert "second-secret" not in captured.err


def test_cli_parser_registers_nested_admin_password_hash_command() -> None:
    """凭据生成命令通过分层 CLI 入口可达。"""

    args = entry.build_parser().parse_args(["admin", "password", "hash"])
    assert args.func is entry.cmd_admin_password_hash


def test_cli_admin_password_reset_writes_runtime_only_and_preserves_yaml(
    monkeypatch, tmp_path, capsys,
) -> None:
    from app.core import config as cfg

    data = tmp_path / "data"
    data.mkdir()
    root_config = tmp_path / "config.yml"
    root_text = "# 人工设置不修改\nadmin_login_enabled: true\nadmin_username: operator\n"
    root_config.write_text(root_text, encoding="utf-8")
    runtime_config = data / "config.json"
    secret_file = data / "admin_initial_password"
    secret_file.write_text("old-secret", encoding="utf-8")
    (data / "admin_session_secret").write_text("old-session", encoding="utf-8")

    monkeypatch.setattr(cfg, "CONFIG_FILE", root_config)
    monkeypatch.setattr(cfg, "RUNTIME_CONFIG_FILE", runtime_config)
    monkeypatch.setattr(cfg, "DATA_DIR", data)
    monkeypatch.setattr(cfg, "ADMIN_INITIAL_PASSWORD_FILE", secret_file)
    monkeypatch.setattr(cfg, "ensure_openvpn_runtime_dirs", lambda: None)
    monkeypatch.setattr(entry, "ADMIN_INITIAL_PASSWORD_FILE", secret_file)
    monkeypatch.setattr(entry, "ADMIN_SESSION_SECRET_FILE", data / "admin_session_secret")
    answers = iter(("new-safe-password-123", "new-safe-password-123"))
    monkeypatch.setattr(entry.getpass, "getpass", lambda _: next(answers))

    assert entry.cmd_admin_password_reset(argparse.Namespace()) == 0
    import json
    stored = json.loads(runtime_config.read_text(encoding="utf-8"))
    assert root_config.read_text(encoding="utf-8") == root_text
    assert "admin_login_enabled" not in stored
    assert "admin_username" not in stored
    assert stored["admin_password_hash"].startswith("$argon2id$")
    assert "new-safe-password-123" not in runtime_config.read_text(encoding="utf-8")
    assert cfg.load_config().admin_username == "operator"
    assert not secret_file.exists()
    assert not (data / "admin_session_secret").exists()
    assert "已更新" in capsys.readouterr().out


def test_cli_admin_password_reset_parser_is_nested() -> None:
    args = entry.build_parser().parse_args(["admin", "password", "reset"])
    assert args.func is entry.cmd_admin_password_reset


def _setup_admin_reset_test(monkeypatch, tmp_path):
    from app.core import config as cfg

    data = tmp_path / "data"
    data.mkdir()
    deployment = tmp_path / "config.yml"
    deployment.write_text(
        "admin_login_enabled: true\nadmin_username: operator\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "CONFIG_FILE", deployment)
    monkeypatch.setattr(cfg, "RUNTIME_CONFIG_FILE", data / "config.json")
    monkeypatch.setattr(cfg, "DATA_DIR", data)
    monkeypatch.setattr(cfg, "ADMIN_INITIAL_PASSWORD_FILE", data / "admin_initial_password")
    monkeypatch.setattr(cfg, "ensure_openvpn_runtime_dirs", lambda: None)
    monkeypatch.setattr(entry, "ADMIN_INITIAL_PASSWORD_FILE", data / "admin_initial_password")
    monkeypatch.setattr(entry, "ADMIN_SESSION_SECRET_FILE", data / "admin_session_secret")
    # 提前建立原管理员凭据，以便验证拒绝修改时不会改变已有哈希。
    previous_hash = cfg.load_config().admin_password_hash
    return cfg, deployment, data, previous_hash


def test_cli_admin_reset_accepts_explicit_username_and_password(
    monkeypatch, tmp_path, capsys,
) -> None:
    import json

    cfg, deployment, data, previous_hash = _setup_admin_reset_test(monkeypatch, tmp_path)
    password = "my-custom-admin-secret"
    monkeypatch.setattr(
        entry.getpass, "getpass",
        lambda _: (_ for _ in ()).throw(AssertionError("指定密码时不应交互输入")),
    )

    args = entry.build_parser().parse_args(
        ["admin", "password", "reset", "operator", password],
    )
    assert entry.cmd_admin_password_reset(args) == 0
    state = json.loads((data / "config.json").read_text(encoding="utf-8"))
    assert state["admin_password_hash"] != previous_hash
    assert entry.AdminAuthService().verify_credentials(
        submitted_username="operator",
        submitted_password=password,
        configured_username="operator",
        configured_password_hash=state["admin_password_hash"],
    )
    assert password not in capsys.readouterr().out
    assert "admin_username: operator" in deployment.read_text(encoding="utf-8")


def test_cli_admin_reset_generates_random_password_for_username(
    monkeypatch, tmp_path, capsys,
) -> None:
    import json
    import re

    cfg, deployment, data, previous_hash = _setup_admin_reset_test(monkeypatch, tmp_path)
    monkeypatch.setattr(
        entry.getpass, "getpass",
        lambda _: (_ for _ in ()).throw(AssertionError("随机密码不应弹出交互输入")),
    )
    args = entry.build_parser().parse_args(
        ["admin", "password", "reset", "operator"],
    )
    assert entry.cmd_admin_password_reset(args) == 0
    output = capsys.readouterr().out
    match = re.search(r"随机生成的密码（只显示本次，请妥善保存）：(\S+)", output)
    assert match
    password = match.group(1)
    assert len(password) >= 24
    state = json.loads((data / "config.json").read_text(encoding="utf-8"))
    assert state["admin_password_hash"] != previous_hash
    assert entry.AdminAuthService().verify_credentials(
        submitted_username="operator",
        submitted_password=password,
        configured_username="operator",
        configured_password_hash=state["admin_password_hash"],
    )
    assert not (data / "admin_initial_password").exists()
    assert "admin_username: operator" in deployment.read_text(encoding="utf-8")


def test_cli_admin_reset_rejects_wrong_username_without_modifying_hash(
    monkeypatch, tmp_path, capsys,
) -> None:
    import json

    _, deployment, data, previous_hash = _setup_admin_reset_test(monkeypatch, tmp_path)
    args = entry.build_parser().parse_args(
        ["admin", "password", "reset", "other", "test-secret"],
    )
    assert entry.cmd_admin_password_reset(args) == 1
    state = json.loads((data / "config.json").read_text(encoding="utf-8"))
    assert state["admin_password_hash"] == previous_hash
    assert "账号不匹配" in capsys.readouterr().err
    assert "admin_username: operator" in deployment.read_text(encoding="utf-8")


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
