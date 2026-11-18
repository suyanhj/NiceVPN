# -*- coding: utf-8 -*-
"""系统配置分层：根目录 config.yml 只读部署参数，data/config.json 存程序状态。"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml
from box import Box

from app.core.constants import (
    ADMIN_INITIAL_PASSWORD_FILE,
    CONFIG_FILE,
    DATA_DIR,
    RUNTIME_CONFIG_FILE,
    ensure_openvpn_runtime_dirs,
)
from app.models.config import SystemConfig

# 每个配置项只能有一个归属；Web/CLI 保存不得覆盖人工维护的 YAML。
DEPLOYMENT_CONFIG_FIELDS = frozenset({
    "admin_login_enabled",
    "admin_username",
    "admin_password",  # 仅首次初始化管理员密码时使用
    "admin_session_lifetime_minutes",
    "admin_reconnect_timeout_seconds",
})

_OPTIONAL_STRING_FIELDS = {
    "admin_username", "admin_password", "admin_password_hash",
    "global_subnet", "openvpn_bin", "openvpn_install_mode",
    "easyrsa_dir", "easyrsa_install_mode", "pki_dir", "vpn_instance_id",
    "server_ip", "dingtalk_webhook", "dingtalk_secret", "wework_webhook",
    "rocketchat_webhook", "rocketchat_channel", "rocketchat_username",
    "rocketchat_icon_emoji", "rocketchat_title", "download_base_url",
    "global_ssh_private_key", "global_ssh_private_key_passphrase",
    "created_at", "updated_at",
}


def _ensure_data_dirs() -> None:
    """运行数据由程序创建；YAML 由部署人员管理。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        ensure_openvpn_runtime_dirs()


def _normalize_config_data(data: dict) -> dict:
    normalized = dict(data or {})
    for field in _OPTIONAL_STRING_FIELDS:
        if normalized.get(field) == {}:
            normalized[field] = None
    return normalized


def _read_deployment_config() -> dict:
    """只允许明确定义的部署字段出现在 config.yml 中。"""
    if not CONFIG_FILE.exists():
        return {}
    with CONFIG_FILE.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("config.yml 必须是 YAML 对象")
    unknown = set(data) - DEPLOYMENT_CONFIG_FIELDS
    if unknown:
        raise ValueError(
            "config.yml 只能放部署参数，运行设置应由管理端保存到 data/config.json："
            + ", ".join(sorted(str(k) for k in unknown))
        )
    return data


def _atomic_write(path: Path, content: str) -> None:
    """原子写入 0600 文件，失败时保留原内容。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent)
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _read_runtime_config() -> dict:
    if not RUNTIME_CONFIG_FILE.exists():
        return {}
    with RUNTIME_CONFIG_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("data/config.json 必须是 JSON 对象")
    return data


def _combine_config(runtime: dict, deployment: dict) -> dict:
    """只读合并：人工部署字段优先，但已设置的哈希永远优先于初始明文密码。"""
    combined = {**runtime, **deployment}
    if combined.get("admin_password_hash"):
        combined.pop("admin_password", None)
    return _normalize_config_data(combined)


def restore_legacy_deployment_config(runtime: dict) -> None:
    """从历史完整配置中提取 YAML 部署参数；供首次迁移和旧备份恢复共用。"""
    old = dict(runtime)
    if old.get("admin_password_hash") and not old.get("admin_username"):
        old["admin_username"] = "admin"
    model = SystemConfig(**_normalize_config_data(old))
    deployment = {
        "admin_login_enabled": bool(model.admin_auth_enabled),
        "admin_username": model.admin_username or "admin",
        "admin_password": None,
        "admin_session_lifetime_minutes": model.admin_session_lifetime_minutes,
        "admin_reconnect_timeout_seconds": model.admin_reconnect_timeout_seconds,
    }
    _atomic_write(
        CONFIG_FILE,
        yaml.safe_dump(deployment, allow_unicode=True, sort_keys=False),
    )


def _migrate_legacy_deployment(runtime: dict) -> bool:
    """老环境首次读取时生成独立 YAML，之后原子精简旧 JSON。"""
    if CONFIG_FILE.exists() or not RUNTIME_CONFIG_FILE.exists():
        return False
    restore_legacy_deployment_config(runtime)
    return True


def _write_initial_password(secret: str) -> None:
    ADMIN_INITIAL_PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(
        str(ADMIN_INITIAL_PASSWORD_FILE),
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(secret + "\n")


def _prepare_admin_password(config: SystemConfig) -> SystemConfig:
    """仅当登录已启用而运行配置缺少哈希时，初始化一次管理员口令。"""
    if not config.admin_auth_enabled or config.admin_password_hash:
        return config

    from app.services.auth import AdminAuthService

    if config.admin_password:
        password = config.admin_password
        generated = False
    elif ADMIN_INITIAL_PASSWORD_FILE.exists():
        password = ADMIN_INITIAL_PASSWORD_FILE.read_text(encoding="utf-8").strip()
        generated = True
    else:
        password = secrets.token_urlsafe(24)
        _write_initial_password(password)
        generated = True

    config.admin_password_hash = AdminAuthService().hash_password(password)
    config.admin_password = None
    # 仅保存到 data/config.json；不修改 config.yml，也不会覆盖注释。
    save_config(config)
    if not generated:
        ADMIN_INITIAL_PASSWORD_FILE.unlink(missing_ok=True)
    return config


def load_config() -> Box:
    """返回与旧版一致的扁平配置视图，来源由字段归属决定。"""
    _ensure_data_dirs()
    runtime = _read_runtime_config()
    migrated = _migrate_legacy_deployment(runtime)
    deployment = _read_deployment_config()

    config = SystemConfig(**_combine_config(runtime, deployment))
    config = _prepare_admin_password(config)
    if migrated:
        # 迁移后 JSON 不再保存部署字段，但所有运行状态原样保留。
        save_config(config)
    return Box(config.model_dump(), default_box=True, default_box_attr=None)


def save_config(config: SystemConfig | dict | Box) -> None:
    """只持久化程序管理的配置字段，不写回根目录 YAML。"""
    _ensure_data_dirs()
    if isinstance(config, Box):
        incoming = config.to_dict()
    elif isinstance(config, dict):
        incoming = dict(config)
    else:
        incoming = config.model_dump()

    # 原有调用方传完整合并视图；以磁盘上的部署配置为准，不接受写入方覆盖。
    deployment = _read_deployment_config()
    validated = SystemConfig(**_combine_config(incoming, deployment)).model_dump()
    runtime = {
        key: value
        for key, value in validated.items()
        if key not in DEPLOYMENT_CONFIG_FIELDS
    }
    runtime["updated_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_write(
        RUNTIME_CONFIG_FILE,
        json.dumps(runtime, ensure_ascii=False, indent=2) + "\n",
    )
