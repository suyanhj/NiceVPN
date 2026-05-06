# -*- coding: utf-8 -*-
"""
设备绑定 JSON 读写（供 OpenVPN client-connect 的 device-bind.sh 调用）。

仅依赖标准库；与管理系统 DeviceBinding 字段约定一致。
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
import uuid
from pathlib import Path


def _sanitize_printable(raw: str | None, max_len: int) -> str:
    """仅保留 ASCII 可打印字符，防注入与换行破坏 JSON。"""
    s = (raw or "").replace("\r", "").replace("\n", "")
    s = re.sub(r"[^\x20-\x7e]", "", s)
    return s[:max_len]


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cmd_write_new(args: argparse.Namespace) -> int:
    """新建一条绑定记录（新 UUID 文件名）。"""
    d = Path(args.bindings_dir)
    d.mkdir(parents=True, exist_ok=True)
    plat = _sanitize_printable(args.iv_plat, 128)
    ts = _sanitize_printable(args.time_ascii, 80)
    now = _utc_now_iso()
    bid = str(uuid.uuid4())
    obj: dict = {
        "id": bid,
        "username": args.username,
        "fingerprint": args.fingerprint,
        "fingerprint_source": args.fingerprint_source,
        "bound_at": now,
        "last_seen_at": now,
    }
    if plat:
        obj["iv_plat"] = plat
    if ts:
        obj["last_connected_since"] = ts
    out = d / f"{bid}.json"
    try:
        out.write_text(json.dumps(obj, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"写入失败: {out}: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    """更新已有绑定文件：last_seen_at、iv_plat、last_connected_since。"""
    p = Path(args.file)
    if not p.is_file():
        print(f"文件不存在: {p}", file=sys.stderr)
        return 1
    plat = _sanitize_printable(args.iv_plat, 128)
    ts = _sanitize_printable(args.time_ascii, 80)
    now = _utc_now_iso()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"读取或解析失败: {p}: {exc}", file=sys.stderr)
        return 1
    data["last_seen_at"] = now
    if plat:
        data["iv_plat"] = plat
    else:
        data.pop("iv_plat", None)
    if ts:
        data["last_connected_since"] = ts
    try:
        p.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"写入失败: {p}: {exc}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="设备绑定 JSON 工具（device-bind.sh 调用）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("write-new", help="新建绑定 JSON")
    w.add_argument("--bindings-dir", required=True, help="device_bindings 目录")
    w.add_argument("--username", required=True, help="common_name")
    w.add_argument("--fingerprint", required=True)
    w.add_argument("--fingerprint-source", required=True, dest="fingerprint_source")
    w.add_argument("--iv-plat", default="", help="IV_PLAT，可空")
    w.add_argument("--time-ascii", default="", dest="time_ascii", help="OpenVPN time_ascii，可空")
    w.set_defaults(func=cmd_write_new)

    u = sub.add_parser("update", help="更新已有绑定 JSON")
    u.add_argument("--file", required=True, help="绑定文件绝对路径")
    u.add_argument("--iv-plat", default="", help="IV_PLAT，空则删除字段")
    u.add_argument("--time-ascii", default="", dest="time_ascii", help="time_ascii，空则不更新该字段")
    u.set_defaults(func=cmd_update)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
