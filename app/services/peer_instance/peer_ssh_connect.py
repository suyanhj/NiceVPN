# -*- coding: utf-8 -*-
"""对端 SSH：从落库凭据建立连接（供探测、iptables 下发等复用）。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import paramiko

from app.core.config import load_config
from app.services.peer_instance.remote_openvpn import load_private_key_from_pem

logger = logging.getLogger("peer.remote")


def effective_ssh_private_key_for_peer(row: dict, cfg: Any) -> tuple[str | None, str | None]:
    """解析 SSH 私钥：对端 ``ssh_private_key`` 非空优先，否则使用系统 ``global_ssh_private_key``。

    Args:
        row: 对端实例字典。
        cfg: ``load_config()`` 结果（支持 ``.get``）。

    Returns:
        ``(pem_text, passphrase_or_none)``；均无则 ``(None, None)``，由调用方回退为密码认证。
    """
    pem_row = str(row.get("ssh_private_key") or "").strip()
    if pem_row:
        pp = str(row.get("ssh_private_key_passphrase") or "").strip()
        return pem_row, (pp or None)
    pem_g = str(cfg.get("global_ssh_private_key") or "").strip()
    if pem_g:
        pp_g = str(cfg.get("global_ssh_private_key_passphrase") or "").strip()
        return pem_g, (pp_g or None)
    return None, None


def peer_row_has_usable_ssh_auth(row: dict) -> bool:
    """是否可 SSH 登录：对端密码、对端私钥或系统全局私钥，三者其一即可。"""
    if str(row.get("ssh_password") or "").strip():
        return True
    if str(row.get("ssh_private_key") or "").strip():
        return True
    cfg = load_config()
    if str(cfg.get("global_ssh_private_key") or "").strip():
        return True
    return False

def connect_peer_ssh_client(
    host: str,
    port: int,
    username: str,
    *,
    password: str | None = None,
    private_key_text: str | None = None,
    private_key_passphrase: str | None = None,
    key_filename: str | None = None,
    connect_timeout: int = 25,
) -> paramiko.SSHClient:
    """连接远端 SSH，成功返回已 connect 的 ``SSHClient``（调用方负责 ``close()``）。

    Raises:
        ValueError: 参数或认证材料不合法
        paramiko.AuthenticationException: 认证失败
        paramiko.SSHException: SSH 协议错误
        OSError: 网络错误
    """
    host = str(host or "").strip()
    username = str(username or "").strip()
    if not host:
        raise ValueError("SSH 主机不能为空")
    if not username:
        raise ValueError("SSH 用户名不能为空")

    pem = str(private_key_text).strip() if private_key_text is not None else ""
    pkey: paramiko.PKey | None = None
    if pem:
        pkey = load_private_key_from_pem(pem, private_key_passphrase)

    key_path: str | None = None
    if pkey is None and key_filename and str(key_filename).strip():
        kp = Path(str(key_filename).strip()).expanduser()
        try:
            kp = kp.resolve()
        except OSError as exc:
            raise ValueError(f"SSH 私钥路径无效: {key_filename}") from exc
        if not kp.is_file():
            raise ValueError(f"SSH 私钥文件不存在: {kp}")
        key_path = str(kp)

    pw = str(password).strip() if password is not None else ""
    if pkey is None and not key_path and not pw:
        raise ValueError("请提供 SSH 密码、私钥 PEM 或本机私钥文件路径（择一）")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    conn_kw: dict[str, Any] = {
        "hostname": host,
        "port": int(port),
        "username": username,
        "timeout": connect_timeout,
        "banner_timeout": connect_timeout,
        "auth_timeout": connect_timeout,
    }
    if pkey is not None:
        conn_kw["pkey"] = pkey
    elif key_path:
        conn_kw["key_filename"] = key_path
    else:
        conn_kw["password"] = pw

    try:
        client.connect(**conn_kw)
    except Exception as exc:
        logger.error("SSH 连接失败: %s@%s:%s error=%s", username, host, int(port), exc)
        client.close()
        raise
    logger.info("SSH 连接成功: %s@%s:%s", username, host, int(port))
    return client


def connect_peer_ssh_client_from_row(row: dict, *, connect_timeout: int = 25) -> paramiko.SSHClient:
    """从对端实例字典与系统配置建立 SSH 连接；私钥对端优先，无则使用全局 `global_ssh_private_key`。"""
    host = str(row.get("ssh_host") or "").strip()
    port = int(row.get("ssh_port") or 22)
    user = str(row.get("ssh_username") or "").strip()
    if not host or not user:
        raise ValueError("请先填写 SSH 主机与用户名")
    cfg = load_config()
    pw = str(row.get("ssh_password") or "").strip()
    pem, pp = effective_ssh_private_key_for_peer(row, cfg)
    return connect_peer_ssh_client(
        host,
        port,
        user,
        password=pw or None,
        private_key_text=pem,
        private_key_passphrase=pp,
        connect_timeout=connect_timeout,
    )
