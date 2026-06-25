# -*- coding: utf-8 -*-
"""OpenVPN 会话审计 JSONL 输出脚本。

由 client-connect/client-disconnect 钩子调用，仅依赖 Python 标准库。
输出字段保持扁平，便于 Fluent Bit JSON parser 解析后写入 VictoriaLogs。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

_SCHEMA_VERSION = "vpn_audit_v1"
_EVENT_DATASET = "vpn.session"
_EVENT_SOURCE = "openvpn"


def _utc_now() -> str:
    """返回毫秒精度 UTC 时间，统一作为 event_time。"""
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _env(name: str) -> str:
    """读取 OpenVPN 注入的环境变量，缺失时返回空字符串。"""
    return (os.environ.get(name) or "").strip()


def _int_env(name: str) -> int | None:
    """读取整数环境变量，空值返回 None，非法值直接报错。"""
    raw = _env(name)
    if not raw:
        return None
    return int(raw)


def _instance_name() -> str:
    """优先使用 server.conf setenv 注入的实例名，缺失时从 config 路径推导。"""
    configured = _env("VPN_INSTANCE_NAME")
    if configured:
        return configured

    config_path = _env("config")
    if not config_path:
        return ""
    return Path(config_path).stem


def build_event(event_type: str) -> dict[str, object]:
    """构造统一 VPN 会话审计事件。

    Args:
        event_type: ``vpn_session_start`` 或 ``vpn_session_end``。

    Returns:
        可直接写入 JSONL 的扁平 dict。

    Raises:
        ValueError: event_type 非法。
        ValueError: 整数型 OpenVPN 环境变量格式非法。
    """
    if event_type not in {"vpn_session_start", "vpn_session_end"}:
        raise ValueError(f"不支持的会话审计事件类型: {event_type}")

    return {
        "schema_version": _SCHEMA_VERSION,
        "event_time": _utc_now(),
        "event_source": _EVENT_SOURCE,
        "event_dataset": _EVENT_DATASET,
        "event_type": event_type,
        "vpn_instance": _instance_name(),
        "vpn_username": _env("common_name"),
        "vpn_virtual_ip": _env("ifconfig_pool_remote_ip"),
        "client_real_ip": _env("trusted_ip"),
        "client_real_port": _int_env("trusted_port"),
        "transport_proto": _env("proto_1") or _env("proto"),
        "bytes_received": _int_env("bytes_received"),
        "bytes_sent": _int_env("bytes_sent"),
        "duration_seconds": _int_env("time_duration"),
        "openvpn_time_ascii": _env("time_ascii"),
        "openvpn_time_unix": _int_env("time_unix"),
        "openvpn_script_type": _env("script_type"),
        "openvpn_config": _env("config"),
        "openvpn_daemon_pid": _int_env("daemon_pid"),
    }


def append_event(log_file: Path, event: dict[str, object]) -> None:
    """追加写入一条 JSONL 审计事件。"""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="写入 OpenVPN 会话审计 JSONL")
    parser.add_argument("--event-type", required=True, choices=["vpn_session_start", "vpn_session_end"])
    parser.add_argument("--log-file", required=True)
    args = parser.parse_args()

    try:
        append_event(Path(args.log_file), build_event(args.event_type))
    except (OSError, ValueError) as exc:
        print(f"写入 VPN 会话审计失败: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
