"""系统配置：根目录 config.yml；data/ 只存运行数据。"""
# -*- coding: utf-8 -*-
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
    LEGACY_CONFIG_FILE,
    ensure_openvpn_runtime_dirs,
)
from app.models.config import SystemConfig

_OPTIONAL_STRING_FIELDS = {
    "admin_username", "admin_password", "admin_password_hash",
    "global_subnet", "openvpn_bin", "openvpn_install_mode",
    "easyrsa_dir", "easyrsa_install_mode", "pki_dir",
    "vpn_instance_id", "server_ip", "dingtalk_webhook",
    "dingtalk_secret", "wework_webhook", "rocketchat_webhook",
    "rocketchat_channel", "rocketchat_username", "rocketchat_icon_emoji",
    "rocketchat_title", "download_base_url", "global_ssh_private_key",
    "global_ssh_private_key_passphrase", "created_at", "updated_at",
}


def _ensure_data_dirs() -> None:
    """运行时数据按需生成；YAML 配置存项目根目录。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        ensure_openvpn_runtime_dirs()


def _normalize_config_data(data: dict) -> dict:
    normalized = dict(data or {})
    for field in _OPTIONAL_STRING_FIELDS:
        if normalized.get(field) == {}:
            normalized[field] = None
    return normalized


def _read_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"配置文件应为 YAML 字典: {path}")
    return raw


def save_config(config: SystemConfig | dict | Box) -> None:
    """校验后原子写入根目录 config.yml，限制为当前服务用户可读。"""
    _ensure_data_dirs()
    if isinstance(config, Box):
        data = config.to_dict()
    elif isinstance(config, dict):
        data = dict(config)
    else:
        data = config.model_dump()

    validated = SystemConfig(**_normalize_config_data(data)).model_dump()
    validated["updated_at"] = datetime.now(timezone.utc).isoformat()

    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=CONFIG_FILE.parent)
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(validated, f, allow_unicode=True, sort_keys=False, width=100)
        os.replace(tmp_name, CONFIG_FILE)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


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
    """首次开启时处理用户设置的明文密码或生成随机初始密码。"""
    if not config.admin_auth_enabled or config.admin_password_hash:
        return config

    from app.services.auth import AdminAuthService

    if config.admin_password:
        password = config.admin_password
        use_generated = False
    elif ADMIN_INITIAL_PASSWORD_FILE.exists():
        password = ADMIN_INITIAL_PASSWORD_FILE.read_text(encoding="utf-8").strip()
        use_generated = True
    else:
        password = secrets.token_urlsafe(24)
        _write_initial_password(password)
        use_generated = True

    config.admin_password_hash = AdminAuthService().hash_password(password)
    config.admin_password = None
    save_config(config)
    if not use_generated:
        ADMIN_INITIAL_PASSWORD_FILE.unlink(missing_ok=True)
    return config


def load_config() -> Box:
    """读取 config.yml；旧环境在 YAML 不存在时自动迁移 data/config.json。"""
    _ensure_data_dirs()
    if CONFIG_FILE.exists():
        data = _read_yaml(CONFIG_FILE)
        if LEGACY_CONFIG_FILE.exists() and data.get("initialized") is False:
            old = json.loads(LEGACY_CONFIG_FILE.read_text(encoding="utf-8"))
            if old.get("initialized"):
                raise ValueError(
                    "旧 data/config.json 已初始化，新 config.yml 为未初始化模板；"
                    "请备份并移走 config.yml，以便启动时迁移旧配置"
                )
    elif LEGACY_CONFIG_FILE.exists():
        data = json.loads(LEGACY_CONFIG_FILE.read_text(encoding="utf-8"))
        save_config(_normalize_config_data(data))
    else:
        data = {}

    config = SystemConfig(**_normalize_config_data(data))
    _prepare_admin_password(config)
    return Box(config.model_dump(), default_box=True, default_box_attr=None)
