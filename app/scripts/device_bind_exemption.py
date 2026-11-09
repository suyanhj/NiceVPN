# -*- coding: utf-8 -*-
"""OpenVPN client-connect 读取原子发布的豁免快照，只依赖标准库。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def exemption_source(path: Path, username: str) -> str | None:
    """精确匹配 CN；无文件或不在名单中时必须执行原有设备绑定逻辑。"""
    if not username or not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("设备豁免文件版本不正确")
    users = payload.get("users")
    if not isinstance(users, dict):
        raise ValueError("设备豁免文件结构不正确")
    source = users.get(username)
    return source if isinstance(source, str) and source.startswith(("tag:", "group:", "group_tag:")) else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    parser.add_argument("--username", required=True)
    args = parser.parse_args()
    try:
        source = exemption_source(Path(args.file), args.username)
    except (OSError, ValueError, UnicodeError) as exc:
        print(f"设备豁免策略读取失败: {exc}", file=sys.stderr)
        return 2
    if not source:
        return 1
    print(source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
