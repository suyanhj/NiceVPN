# -*- coding: utf-8 -*-
"""调用 shell 同步已有客户端 OVPN 的连接地址。"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping

from app.core.constants import OVPN_PROFILES_DIR, SCRIPTS_DIR


_SYNC_SCRIPT = SCRIPTS_DIR / "system" / "sync-ovpn-connection.sh"


def _connection_tuple(config: Mapping[str, object]) -> tuple[str, int, str]:
    host = str(config.get("server_ip") or "").strip()
    port = int(config.get("port") or 1194)
    proto = str(config.get("proto") or "udp").strip().lower()
    return host, port, proto


def sync_existing_ovpn_connection(
    previous: Mapping[str, object],
    current: Mapping[str, object],
) -> dict[str, int]:
    """当 server_ip/port/proto 变化时调用 shell 批量同步现有 OVPN。"""

    old_host, old_port, old_proto = _connection_tuple(previous)
    new_host, new_port, new_proto = _connection_tuple(current)

    if (old_host, old_port, old_proto) == (new_host, new_port, new_proto):
        return {"total": 0, "changed": 0}

    proc = subprocess.run(
        [
            "bash",
            str(_SYNC_SCRIPT),
            str(OVPN_PROFILES_DIR),
            old_host,
            str(old_port),
            old_proto,
            new_host,
            str(new_port),
            new_proto,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            f"同步历史 OVPN 连接地址失败(exit={proc.returncode}): {detail}"
        )

    result = {"total": 0, "changed": 0}
    for raw in (proc.stdout or "").splitlines():
        key, sep, value = raw.partition("=")
        if not sep:
            continue
        if key == "TOTAL":
            result["total"] = int(value)
        elif key == "CHANGED":
            result["changed"] = int(value)
    return result
