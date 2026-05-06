# -*- coding: utf-8 -*-
"""对端经 SSH 安装 OpenVPN：发行版识别与安装脚本与 ``InitWizard`` / ``installer`` 同源。"""
from __future__ import annotations

import logging
import shlex
import time
from typing import Any

from app.services.openvpn.detector import detect_distro_family, parse_os_release_text
from app.services.openvpn.installer import build_peer_openvpn_install_script
from app.services.peer_instance.peer_ssh_connect import connect_peer_ssh_client_from_row
from app.services.peer_instance.remote_peer_iptables import _detect_sudo_prefix, _exec_ssh
from app.utils.shutdown import is_shutdown_requested

logger = logging.getLogger("peer.remote")


def _append_stream_lines(prefix: str, text: str, line_buffer: str) -> str:
    """把 SSH 流输出按行写入 peer.remote，返回未成行的尾巴。"""
    if not text:
        return line_buffer
    line_buffer += text
    parts = line_buffer.splitlines(keepends=True)
    pending = ""
    for part in parts:
        if part.endswith(("\n", "\r")):
            line = part.rstrip("\r\n")
            if line:
                logger.info("%s: %s", prefix, line)
        else:
            pending = part
    return pending


def _exec_ssh_stream_to_log(client, command: str, *, timeout: int) -> tuple[str, str, int]:
    """执行远端命令，并把 stdout/stderr 实时写入 ``peer.remote`` 日志。"""
    _stdin, stdout, _stderr = client.exec_command(command, timeout=timeout)
    channel = stdout.channel
    start = time.monotonic()
    out_chunks: list[bytes] = []
    err_chunks: list[bytes] = []
    out_pending = ""
    err_pending = ""

    while True:
        if is_shutdown_requested():
            channel.close()
            raise RuntimeError("进程正在退出，已取消对端 OpenVPN 安装任务")
        progressed = False
        if channel.recv_ready():
            data = channel.recv(4096)
            if data:
                progressed = True
                out_chunks.append(data)
                out_pending = _append_stream_lines(
                    "对端安装 stdout",
                    data.decode("utf-8", errors="replace"),
                    out_pending,
                )
        if channel.recv_stderr_ready():
            data = channel.recv_stderr(4096)
            if data:
                progressed = True
                err_chunks.append(data)
                err_pending = _append_stream_lines(
                    "对端安装 stderr",
                    data.decode("utf-8", errors="replace"),
                    err_pending,
                )
        if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
            break
        if time.monotonic() - start > timeout:
            raise RuntimeError(f"远端安装 OpenVPN 超时（>{timeout}s）")
        if not progressed:
            time.sleep(0.2)

    if out_pending.strip():
        logger.info("对端安装 stdout: %s", out_pending.strip())
    if err_pending.strip():
        logger.info("对端安装 stderr: %s", err_pending.strip())
    code = channel.recv_exit_status()
    return (
        b"".join(out_chunks).decode("utf-8", errors="replace"),
        b"".join(err_chunks).decode("utf-8", errors="replace"),
        code,
    )


def install_openvpn_on_peer_via_ssh(row: dict, *, exec_timeout: int = 1200) -> dict[str, Any]:
    """读取远端 ``/etc/os-release``，按族执行与初始化向导一致的安装脚本（须 ``sudo -n`` 或 root）。

    Raises:
        ValueError: 参数无效
        RuntimeError: 无法识别发行版或安装命令失败
    """
    client = connect_peer_ssh_client_from_row(row, connect_timeout=30)
    try:
        rel, err_rel, c_rel = _exec_ssh(
            client,
            "cat /etc/os-release 2>/dev/null || true",
            timeout=30,
        )
        if c_rel != 0:
            raise RuntimeError(f"读取远端 os-release 失败: {err_rel.strip()}")
        info = parse_os_release_text(rel)
        family = detect_distro_family(info)
        vid = str(info.get("version_id") or "").strip()
        if not family:
            raise RuntimeError(
                "无法识别远端发行版族（仅支持 debian/ubuntu 与 rhel/centos/rocky 等），"
                f"id={info.get('id')!r} id_like={info.get('id_like')!r}"
            )
        logger.info(
            "对端 OpenVPN 安装：family=%s version_id=%s id=%s",
            family,
            vid,
            info.get("id"),
        )
        script = build_peer_openvpn_install_script(family, vid)
        sp = _detect_sudo_prefix(client, timeout=15)
        wrapped = f"{sp}bash -lc {shlex.quote(script)}"
        logger.info("开始执行对端 OpenVPN 安装脚本（输出实时写入 peer-remote.log）")
        out, err, code = _exec_ssh_stream_to_log(client, wrapped, timeout=exec_timeout)
        if code != 0:
            raise RuntimeError(f"远端安装 OpenVPN 失败（退出码 {code}）: {err.strip() or out[-2000:]}")
        return {
            "ok": True,
            "distro_family": family,
            "version_id": vid,
            "distro_id": str(info.get("id") or ""),
            "pretty_name": str(info.get("pretty_name") or ""),
        }
    finally:
        client.close()
