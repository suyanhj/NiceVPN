"""系统配置加载与持久化"""
# -*- coding: utf-8 -*-
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from box import Box

from app.core.constants import CONFIG_FILE, DATA_DIR, ensure_openvpn_runtime_dirs
from app.models.config import SystemConfig

_OPTIONAL_STRING_FIELDS = {
    "global_subnet",
    "openvpn_bin",
    "easyrsa_dir",
    "pki_dir",
    "vpn_instance_id",
    "server_ip",
    "dingtalk_webhook",
    "dingtalk_secret",
    "wework_webhook",
    "download_base_url",
    "global_ssh_private_key",
    "global_ssh_private_key_passphrase",
    "created_at",
    "updated_at",
}


def _ensure_data_dirs():
    """确保管理端 data/ 目录存在；OpenVPN 状态目录在 /etc/openvpn（见 ensure_openvpn_runtime_dirs）。"""
    for subdir in ["groups", "users", "firewall", "peers", "download_links", "audit", "logs"]:
        (DATA_DIR / subdir).mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        ensure_openvpn_runtime_dirs()


def _normalize_config_data(data: dict) -> dict:
    """规范化配置数据，避免 Box(default_box=True) 的空对象落成字段值。"""
    normalized = dict(data or {})
    for field in _OPTIONAL_STRING_FIELDS:
        value = normalized.get(field)
        if value == {}:
            normalized[field] = None
    return normalized


def load_config() -> Box:
    """
    加载系统配置。
    若配置文件不存在则返回默认未初始化配置。
    返回 python-box Box 对象，支持点号访问。
    """
    _ensure_data_dirs()

    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = _normalize_config_data(json.load(f))
        # 用 Pydantic 模型校验后转为 Box
        config = SystemConfig(**data)
    else:
        config = SystemConfig()

    return Box(config.model_dump(), default_box=True, default_box_attr=None)


def save_config(config: SystemConfig | dict | Box):
    """
    保存系统配置到 JSON 文件。
    支持传入 SystemConfig、dict 或 Box 对象。
    使用临时文件 + rename 确保原子写入。
    """
    _ensure_data_dirs()

    if isinstance(config, Box):
        data = config.to_dict()
    elif isinstance(config, dict):
        data = config
    else:
        data = config.model_dump()

    data = SystemConfig(**_normalize_config_data(data)).model_dump()

    # 更新时间戳
    data["updated_at"] = datetime.now(timezone.utc).isoformat()

    # 原子写入：先写临时文件再 rename
    tmp_path = CONFIG_FILE.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    tmp_path.replace(CONFIG_FILE)
    # 配置含敏感路径与密钥信息，仅 root 可读
    if os.name != "nt":
        try:
            os.chmod(CONFIG_FILE, 0o600)
        except OSError:
            pass
