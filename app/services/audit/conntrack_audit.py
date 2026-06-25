# -*- coding: utf-8 -*-
"""conntrack 连接审计监听服务。

启动 ``conntrack -E -o timestamp`` 子进程，按 VPN 全局网段过滤连接事件，
将 NEW / DESTROY 事件格式化为 JSONL，供 Fluent Bit 采集到 VictoriaLogs。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import TextIO

from app.core.config import load_config
from app.core.constants import VPN_CONNECTION_AUDIT_LOG_FILE
from app.services.user.crud import UserService

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = "vpn_audit_v1"
_EVENT_SOURCE = "conntrack"
_EVENT_DATASET = "vpn.connection"
_EVENT_MAP = {
    "NEW": "vpn_connection_new",
    "DESTROY": "vpn_connection_end",
}
_SUPPORTED_PROTO = {"tcp", "udp", "icmp"}
_BRACKET_RE = re.compile(r"\[([^\]]+)\]")
_EPOCH_RE = re.compile(r"^\d+(?:\.\d+)?$")
_singleton: "ConntrackAuditService | None" = None


def _utc_now() -> str:
    """返回毫秒精度 UTC 时间。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_timestamp(raw: str | None) -> str:
    """将 conntrack timestamp 转为 UTC ISO8601；无法解析时使用当前时间。"""
    if not raw:
        return _utc_now()
    value = raw.strip()
    try:
        if _EPOCH_RE.match(value):
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat(
                timespec="milliseconds"
            ).replace("+00:00", "Z")
        normalized = value.replace("Z", "+00:00")
        if re.search(r"[+-]\d{4}$", normalized):
            normalized = f"{normalized[:-2]}:{normalized[-2:]}"
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except ValueError:
        logger.warning("无法解析 conntrack timestamp，使用当前时间: %s", raw)
        return _utc_now()


def _safe_int(raw: str | None) -> int | None:
    """转换整数，空值返回 None，非法值抛出 ValueError。"""
    if raw is None or raw == "":
        return None
    return int(raw)


def _extract_first_tuple(tokens: list[str]) -> dict[str, str]:
    """提取 conntrack 原始方向的第一组 key=value。"""
    result: dict[str, str] = {}
    for token in tokens:
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        if key in result:
            # 第二次出现 src/dst/sport/dport 时已经进入 reply 方向。
            if key in {"src", "dst", "sport", "dport"}:
                break
            continue
        result[key] = value
    return result


def parse_conntrack_event(
    line: str,
    vpn_network: IPv4Network,
    username_by_ip: dict[str, str],
    vpn_instance: str,
) -> dict[str, object] | None:
    """解析单行 conntrack 事件。

    Args:
        line: ``conntrack -E -o timestamp`` 输出的一行。
        vpn_network: VPN 全局网段，仅记录源地址属于该网段的原始方向连接。
        username_by_ip: CCD 固定虚拟 IP 到用户名的映射。
        vpn_instance: OpenVPN 实例名。

    Returns:
        匹配 VPN 出站连接时返回 JSONL 事件 dict；其它事件返回 None。

    Raises:
        ValueError: IP 或整数字段格式非法。
    """
    text = line.strip()
    if not text:
        return None

    bracket_values = _BRACKET_RE.findall(text)
    conntrack_timestamp = ""
    conntrack_event = ""
    for value in bracket_values:
        if value in _EVENT_MAP:
            conntrack_event = value
            break
        if not conntrack_timestamp:
            conntrack_timestamp = value

    if conntrack_event not in _EVENT_MAP:
        return None

    cleaned = _BRACKET_RE.sub(" ", text)
    tokens = cleaned.split()
    proto = next((token for token in tokens if token in _SUPPORTED_PROTO), "")
    fields = _extract_first_tuple(tokens)
    src_ip = fields.get("src", "")
    if not src_ip:
        return None
    if IPv4Address(src_ip) not in vpn_network:
        return None

    dst_ip = fields.get("dst", "")
    vpn_username = username_by_ip.get(src_ip, "")
    return {
        "schema_version": _SCHEMA_VERSION,
        "event_time": _parse_timestamp(conntrack_timestamp),
        "event_source": _EVENT_SOURCE,
        "event_dataset": _EVENT_DATASET,
        "event_type": _EVENT_MAP[conntrack_event],
        "vpn_instance": vpn_instance,
        "vpn_username": vpn_username,
        "vpn_virtual_ip": src_ip,
        "src_ip": src_ip,
        "src_port": _safe_int(fields.get("sport")),
        "dst_ip": dst_ip,
        "dst_port": _safe_int(fields.get("dport")),
        "transport_proto": proto,
        "network_proto": "ipv4",
        "packet_count": _safe_int(fields.get("packets")),
        "byte_count": _safe_int(fields.get("bytes")),
        "conntrack_event": conntrack_event,
        "conntrack_timestamp": conntrack_timestamp,
    }


class ConntrackAuditService:
    """conntrack 连接审计后台监听服务。"""

    def __init__(
        self,
        log_file: Path = VPN_CONNECTION_AUDIT_LOG_FILE,
        restart_delay_seconds: int = 5,
        mapping_refresh_seconds: int = 60,
    ) -> None:
        self._log_file = log_file
        self._restart_delay_seconds = restart_delay_seconds
        self._mapping_refresh_seconds = mapping_refresh_seconds
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._process: subprocess.Popen[str] | None = None
        self._username_by_ip: dict[str, str] = {}
        self._last_mapping_load = 0.0

    def start(self) -> None:
        """启动后台监听线程。"""
        if os.name == "nt":
            logger.info("Windows 环境跳过 conntrack 连接审计监听")
            return
        if self._thread and self._thread.is_alive():
            logger.debug("conntrack 连接审计监听已在运行")
            return
        if shutil.which("conntrack") is None:
            logger.error("未找到 conntrack 命令，请安装 conntrack-tools 后重启项目")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_forever,
            daemon=True,
            name="conntrack-audit",
        )
        self._thread.start()
        logger.info("conntrack 连接审计监听已启动")

    def stop(self) -> None:
        """停止后台监听线程与 conntrack 子进程。"""
        self._stop_event.set()
        if self._process and self._process.poll() is None:
            self._process.terminate()

    def _run_forever(self) -> None:
        """循环拉起 conntrack 子进程，异常退出后延迟重启。"""
        while not self._stop_event.is_set():
            try:
                self._run_once()
            except Exception as exc:
                logger.exception("conntrack 连接审计监听异常: %s", exc)
            if not self._stop_event.wait(self._restart_delay_seconds):
                logger.warning("conntrack 连接审计监听准备重启")

    def _run_once(self) -> None:
        """启动一次 conntrack 子进程并消费 stdout。"""
        cfg = load_config()
        raw_subnet = str(cfg.get("global_subnet") or "").strip()
        if not raw_subnet:
            logger.error("global_subnet 未配置，无法启动 conntrack 连接审计")
            return
        vpn_network = IPv4Network(raw_subnet, strict=False)
        vpn_instance = _resolve_vpn_instance(cfg)
        self._refresh_username_mapping(force=True)
        self._log_file.parent.mkdir(parents=True, exist_ok=True)

        cmd = ["conntrack", "-E", "-o", "timestamp"]
        logger.info("启动 conntrack 连接审计子进程: %s", " ".join(cmd))
        with subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        ) as proc:
            self._process = proc
            stdout = proc.stdout
            if stdout is None:
                raise RuntimeError("conntrack stdout 未打开")
            self._consume_stdout(stdout, vpn_network, vpn_instance)
            code = proc.poll()
            if code is None:
                proc.terminate()
                code = proc.wait(timeout=5)
            if code != 0 and not self._stop_event.is_set():
                raise RuntimeError(f"conntrack 子进程异常退出，exit_code={code}")

    def _consume_stdout(self, stdout: TextIO, vpn_network: IPv4Network, vpn_instance: str) -> None:
        """逐行解析 conntrack 输出并写入 JSONL。"""
        for line in stdout:
            if self._stop_event.is_set():
                return
            self._refresh_username_mapping(force=False)
            try:
                event = parse_conntrack_event(line, vpn_network, self._username_by_ip, vpn_instance)
            except ValueError as exc:
                logger.error("解析 conntrack 事件失败: %s line=%r", exc, line.strip())
                raise
            if event is None:
                continue
            self._append_event(event)

    def _refresh_username_mapping(self, force: bool) -> None:
        """按固定 CCD 虚拟 IP 刷新 IP -> 用户名映射。"""
        now = time.monotonic()
        if not force and now - self._last_mapping_load < self._mapping_refresh_seconds:
            return
        ccd_map = UserService().list_ccd_virtual_ipv4_by_username()
        self._username_by_ip = {ip: username for username, ip in ccd_map.items()}
        self._last_mapping_load = now

    def _append_event(self, event: dict[str, object]) -> None:
        """追加写入连接审计 JSONL。"""
        with self._log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")


def _resolve_vpn_instance(config) -> str:
    """解析当前 VPN 实例名，作为低基数字段写入日志。"""
    explicit = str(config.get("vpn_instance_id") or "").strip()
    if explicit:
        return explicit
    instances = config.get("instances") or {}
    if isinstance(instances, dict) and len(instances) == 1:
        return str(next(iter(instances.keys())))
    return "server"


def start_conntrack_audit_listener() -> ConntrackAuditService:
    """启动全局 conntrack 连接审计监听器。"""
    global _singleton
    if _singleton is None:
        _singleton = ConntrackAuditService()
    _singleton.start()
    return _singleton
