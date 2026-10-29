# -*- coding: utf-8 -*-
"""验证可选单管理员认证配置。"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.config import SystemConfig


VALID_ARGON2ID_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$"
    "c29tZS1maXhlZC1zYWx0$"
    "c29tZS1maXhlZC1oYXNoLXZhbHVl"
)


def test_admin_auth_is_disabled_by_default() -> None:
    """两项凭据都缺失时保持现有无登录保护行为。"""

    config = SystemConfig()
    assert config.admin_username is None
    assert config.admin_password_hash is None
    assert config.admin_auth_enabled is False
    assert config.admin_session_lifetime_minutes == 480
    assert config.admin_reconnect_timeout_seconds == 300


def test_complete_admin_credentials_enable_auth() -> None:
    """完整且有效的管理员凭据显式启用登录保护。"""

    config = SystemConfig(
        admin_username="admin",
        admin_password_hash=VALID_ARGON2ID_HASH,
        admin_session_lifetime_minutes=60,
    )
    assert config.admin_auth_enabled is True
    assert config.admin_username == "admin"
    assert config.admin_session_lifetime_minutes == 60


@pytest.mark.parametrize(
    "values",
    (
        {"admin_username": "admin"},
        {"admin_password_hash": VALID_ARGON2ID_HASH},
        {"admin_username": "admin", "admin_password_hash": "$argon2id$broken"},
        {"admin_username": "bad name", "admin_password_hash": VALID_ARGON2ID_HASH},
    ),
)
def test_partial_or_invalid_admin_credentials_fail_validation(values: dict) -> None:
    """不完整或非法凭据必须阻止启动，而不是回退到无认证模式。"""

    with pytest.raises(ValidationError):
        SystemConfig(**values)


@pytest.mark.parametrize("timeout", (0, 2, 3601))
def test_admin_reconnect_timeout_has_safe_bounds(timeout: int) -> None:
    """断线页面保留窗口必须处于明确范围内。"""

    with pytest.raises(ValidationError):
        SystemConfig(admin_reconnect_timeout_seconds=timeout)


@pytest.mark.parametrize("lifetime", (0, 4, 10081))
def test_admin_session_lifetime_has_safe_bounds(lifetime: int) -> None:
    """会话有效期必须处于明确范围内。"""

    with pytest.raises(ValidationError):
        SystemConfig(admin_session_lifetime_minutes=lifetime)


def test_load_config_fails_for_incomplete_admin_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """配置文件中的部分凭据在加载阶段直接失败。"""

    from app.core import config as config_module

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    config_file = data_dir / "config.json"
    config_file.write_text(
        json.dumps({"admin_username": "admin"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(config_module, "CONFIG_FILE", config_file)

    with pytest.raises(ValidationError, match="必须同时配置或同时留空"):
        config_module.load_config()


def test_blank_admin_credentials_are_normalized_to_disabled() -> None:
    """两个空白值按未配置处理，不会意外开启认证。"""

    config = SystemConfig(admin_username="  ", admin_password_hash="\t")
    assert config.admin_auth_enabled is False
