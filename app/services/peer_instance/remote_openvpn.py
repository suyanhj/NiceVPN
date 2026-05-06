# -*- coding: utf-8 -*-
"""经 SSH 在远端 Linux 检测 OpenVPN 客户端：路径与版本逻辑对齐本机 ``detector``，不涉及 PKI/服务端。"""
from __future__ import annotations

import io
import logging
import re
import shlex
from pathlib import Path
from typing import Any

import paramiko
from paramiko import ECDSAKey, Ed25519Key, RSAKey
from paramiko.ssh_exception import SSHException
from packaging.version import Version

from app.core.constants import OPENVPN_MIN_VERSION, OPENVPN_SEARCH_PATHS

logger = logging.getLogger("peer.remote")


def _build_openvpn_probe_shell(*, preferred_binary: str | None = None) -> str:
    """生成在远端 bash 执行的探测脚本。preferred_binary 非空时最先尝试该路径（须可执行），再与标准列表及 command -v 一致。"""
    lines: list[str] = ["set -e"]
    pref = str(preferred_binary or "").strip()
    if pref:
        q = shlex.quote(pref)
        lines.append(
            f"if [ -x {q} ]; then printf 'BIN:%s\\n' {q}; {q} --version 2>&1 | head -1; exit 0; fi"
        )
    for p in OPENVPN_SEARCH_PATHS:
        q = shlex.quote(p)
        lines.append(f"if [ -x {q} ]; then printf 'BIN:%s\\n' {q}; {q} --version 2>&1 | head -1; exit 0; fi")
    lines.append("if command -v openvpn >/dev/null 2>&1; then")
    lines.append("  p=$(command -v openvpn)")
    lines.append('  echo "BIN:$p"')
    lines.append('  "$p" --version 2>&1 | head -1')
    lines.append("  exit 0")
    lines.append("fi")
    lines.append("exit 1")
    return "\n".join(lines)


def _parse_probe_stdout(stdout: str) -> tuple[str | None, str | None]:
    bin_path: str | None = None
    version: str | None = None
    for line in stdout.splitlines():
        s = line.strip()
        if s.startswith("BIN:"):
            bin_path = s[4:].strip().strip('"').strip("'")
        elif s and not s.startswith("BIN:") and version is None:
            m = re.search(r"(\d+\.\d+\.\d+)", s)
            if m:
                version = m.group(1)
    return bin_path, version


def load_private_key_from_pem(pem: str, passphrase: str | None = None) -> paramiko.PKey:
    """从 PEM 文本加载 Paramiko 私钥（支持常见 OpenSSH 格式）。

    Raises:
        ValueError: 无法解析或口令错误
    """
    raw = str(pem or "").strip()
    if not raw:
        raise ValueError("SSH 私钥 PEM 为空")
    pwd: str | bytes | None = None
    if passphrase is not None and str(passphrase).strip():
        pwd = str(passphrase).strip()
    bio = io.StringIO(raw)
    last_err: Exception | None = None
    for cls in (Ed25519Key, ECDSAKey, RSAKey):
        bio.seek(0)
        try:
            return cls.from_private_key(bio, password=pwd)
        except SSHException as exc:
            last_err = exc
            continue
    msg = str(last_err) if last_err else "unknown"
    raise ValueError(f"无法解析 SSH 私钥 PEM 或口令不正确: {msg}")


def _parse_remote_os_release(text: str) -> dict[str, str]:
    info: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        k = key.strip().lower()
        v = value.strip().strip('"').strip("'")
        info[k] = v
    return {
        "id": info.get("id", ""),
        "pretty_name": info.get("pretty_name", ""),
        "name": info.get("name", ""),
    }


def _exec_ssh(
    client: paramiko.SSHClient,
    command: str,
    *,
    timeout: int,
) -> tuple[str, str, int]:
    _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    out_b = stdout.read()
    err_b = stderr.read()
    code = stdout.channel.recv_exit_status()
    return out_b.decode("utf-8", errors="replace"), err_b.decode("utf-8", errors="replace"), code


def detect_openvpn_via_ssh(
    host: str,
    port: int,
    username: str,
    *,
    password: str | None = None,
    key_filename: str | None = None,
    private_key_text: str | None = None,
    private_key_passphrase: str | None = None,
    connect_timeout: int = 20,
    exec_timeout: int = 45,
    openvpn_binary: str | None = None,
) -> dict[str, Any]:
    """SSH 登录远端后检测 OpenVPN 是否安装及版本是否达到 ``OPENVPN_MIN_VERSION``。

    Args:
        host: 远端主机名或 IP
        port: SSH 端口
        username: SSH 登录名
        password: 密码（与私钥二选一）
        key_filename: 本机私钥文件路径（与 password、private_key_text 互斥优先见实现）
        private_key_text: 私钥 PEM 全文（优先于 key_filename 与 password）
        private_key_passphrase: PEM 加密口令（可选）
        connect_timeout: TCP/SSH 握手超时（秒）
        exec_timeout: 远端命令超时（秒）
        openvpn_binary: 远端 openvpn 绝对路径（可选）；非空时探测脚本优先尝试，失败再按标准路径与 command -v

    Returns:
        ``connected``、``ssh_error``、``installed``、``path``、``version``、
        ``meets_requirement``、``remote_distro``（id/pretty_name/name）

    Raises:
        ValueError: 参数不合法（如缺少认证方式）
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

    result: dict[str, Any] = {
        "connected": False,
        "ssh_error": None,
        "installed": False,
        "path": None,
        "version": None,
        "meets_requirement": False,
        "remote_distro": {},
    }

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    logger.debug(
        "SSH %s:%s 使用 AutoAddPolicy；生产环境建议在 known_hosts 中固定主机密钥",
        host,
        port,
    )

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
    except paramiko.AuthenticationException as exc:
        err = f"SSH 认证失败: {exc}"
        logger.error("SSH 连接失败: %s@%s:%s error=%s", username, host, int(port), exc)
        result["ssh_error"] = err
        return result
    except (paramiko.SSHException, OSError, TimeoutError) as exc:
        err = f"SSH 连接失败: {exc}"
        logger.error("SSH 连接失败: %s@%s:%s error=%s", username, host, int(port), exc)
        result["ssh_error"] = err
        return result

    logger.info("SSH 连接成功: %s@%s:%s", username, host, int(port))
    result["connected"] = True
    try:
        rel, _, _ = _exec_ssh(
            client,
            "cat /etc/os-release 2>/dev/null || true",
            timeout=min(15, exec_timeout),
        )
        result["remote_distro"] = _parse_remote_os_release(rel)

        pref = str(openvpn_binary or "").strip() or None
        if pref:
            logger.info("远端 OpenVPN 探测优先自定义路径 host=%s path=%s", host, pref)
        body = _build_openvpn_probe_shell(preferred_binary=pref)
        cmd = f"bash -lc {shlex.quote(body)}"
        out, err, code = _exec_ssh(client, cmd, timeout=exec_timeout)
        if err.strip():
            logger.debug("远端探测 stderr: %s", err.strip()[:500])
        if code != 0:
            logger.info("远端未找到 OpenVPN 可执行文件 host=%s exit=%s", host, code)
            return result

        bpath, ver = _parse_probe_stdout(out)
        if bpath:
            result["installed"] = True
            result["path"] = bpath
        if ver:
            result["version"] = ver
            try:
                result["meets_requirement"] = Version(ver) >= Version(OPENVPN_MIN_VERSION)
            except Exception:
                result["meets_requirement"] = False
        logger.info(
            "远端 OpenVPN 检测成功 host=%s path=%s version=%s meets=%s",
            host,
            bpath,
            ver,
            result["meets_requirement"],
        )
        return result
    finally:
        client.close()
