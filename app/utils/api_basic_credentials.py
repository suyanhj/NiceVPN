# -*- coding: utf-8 -*-
"""VPN 公网 API 使用的 HTTP Basic 凭据：初始化时生成并写入 data，不覆盖已有文件。"""

import logging
import secrets
from typing import Tuple

from app.core.constants import API_BASIC_CREDENTIALS_FILE, DATA_DIR
from app.utils.file_lock import read_json, write_json_atomic

logger = logging.getLogger(__name__)

API_BASIC_USERNAME = "vpn"
# token_urlsafe(18) 经 Base64 编码后长度约为 24
_API_PASSWORD_BYTES = 18


def ensure_api_basic_credentials_file() -> None:
    """
    若 data/api_basic_credentials.json 不存在则创建：固定用户名 vpn + 随机口令。
    已存在则绝不覆盖，避免公网调用方集体失效。
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if API_BASIC_CREDENTIALS_FILE.exists():
        return
    password = secrets.token_urlsafe(_API_PASSWORD_BYTES)
    payload = {"username": API_BASIC_USERNAME, "password": password}
    write_json_atomic(API_BASIC_CREDENTIALS_FILE, payload)
    logger.info(
        "VPN API Basic 首次生成（后续启动不重复打印）：用户名=%s 密码=%s 文件=%s",
        API_BASIC_USERNAME,
        password,
        API_BASIC_CREDENTIALS_FILE,
    )


def load_api_basic_credentials() -> Tuple[str, str]:
    """
    读取 Basic 用户名与口令。

    Returns:
        (username, password)

    Raises:
        FileNotFoundError: 凭据文件不存在
        ValueError: JSON 缺字段
    """
    if not API_BASIC_CREDENTIALS_FILE.exists():
        raise FileNotFoundError(str(API_BASIC_CREDENTIALS_FILE))
    data = read_json(API_BASIC_CREDENTIALS_FILE)
    if not data:
        raise ValueError("API Basic 凭据文件为空或无法解析")
    user = str(data.get("username") or "").strip()
    pwd = str(data.get("password") or "").strip()
    if not user or not pwd:
        raise ValueError("API Basic 凭据缺少 username 或 password")
    return user, pwd
