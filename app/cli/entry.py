# -*- coding: utf-8 -*-
"""命令行子命令：添加组 / 用户 / 从 iptables 文本导入防火墙规则。"""
from __future__ import annotations

import argparse
import getpass
import json
import logging
import shutil
import sys
from pathlib import Path

from app.cli.iptables_parse import iter_iptables_file, parse_iptables_line
from app.core.config import load_config
from app.services.firewall.rule_service import FirewallRuleService
from app.services.group.crud import GroupService
from app.services.user.crud import UserService
from app.services.user.device_bind import DeviceBindingService
from app.utils.cidr import is_subnet_of, validate_cidr
from app.utils.logging_setup import setup_logging
from app.models.firewall import FirewallRule
from app.services.group.subnet import check_subnet_conflict
from app.services.auth import AdminAuthService

logger = logging.getLogger(__name__)

try:
    import argcomplete
    from argcomplete.completers import FilesCompleter
except ImportError:
    argcomplete = None
    FilesCompleter = None  # type: ignore[misc, assignment]


def _extract_dry_run(argv: list[str]) -> tuple[list[str], bool]:
    """
    从参数列表中取出所有 ``--dry-run``（任意位置均可），供标准 argparse 解析其余项。

    语义：与正式命令走**同一套**参数解析与业务校验；仅在全部通过后，
    根据是否曾出现 ``--dry-run`` 决定只打印预览还是执行落库。
    """
    out: list[str] = []
    dry = False
    for tok in argv:
        if tok == "--dry-run":
            dry = True
        else:
            out.append(tok)
    return out, dry


def _require_initialized() -> None:
    cfg = load_config()
    if not cfg.get("initialized"):
        raise SystemExit("系统未初始化：请先通过 Web 向导完成初始化。")
    if not (cfg.get("easyrsa_dir") and cfg.get("pki_dir")):
        raise SystemExit("配置缺少 easyrsa_dir / pki_dir，无法创建用户。")


def _resolve_group_id(
    gs: GroupService,
    *,
    group_id: str | None,
    group_name: str | None,
) -> str:
    if group_id and group_id.strip():
        gid = group_id.strip()
        for g in gs.list_all():
            if g.get("id") == gid:
                return gid
        raise SystemExit(f"组 ID 不存在: {gid}")
    if group_name and group_name.strip():
        name = group_name.strip()
        for g in gs.list_all():
            if g.get("name") == name:
                return str(g["id"])
        raise SystemExit(f"组名不存在: {name}")
    raise SystemExit("请指定 --group-id 或 --group-name。")


def _resolve_firewall_owner(
    gs: GroupService,
    *,
    owner_type: str,
    owner_id: str | None,
    group_name: str | None,
    username: str | None,
) -> str:
    ot = owner_type.strip().lower()
    if ot == "group":
        if owner_id and owner_id.strip():
            oid = owner_id.strip()
            for g in gs.list_all():
                if g.get("id") == oid:
                    return oid
            raise SystemExit(f"组 ID 不存在: {oid}")
        if group_name and group_name.strip():
            for g in gs.list_all():
                if g.get("name") == group_name.strip():
                    return str(g["id"])
            raise SystemExit(f"组名不存在: {group_name}")
        raise SystemExit("组类型规则请指定 --owner-id 或 --group-name。")
    if ot == "user":
        if not (username and username.strip()):
            raise SystemExit("用户类型规则请指定 --username。")
        return username.strip()
    raise SystemExit("owner-type 须为 group 或 user。")


def _next_priority(fw: FirewallRuleService) -> int:
    flat = fw.list_all_flat()
    if not flat:
        return 10
    return max(int(r.get("priority") or 0) for r in flat) + 10


def _validate_group_create(gs: GroupService, name: str, subnet: str) -> None:
    """与 GroupService.create 相同的校验逻辑（不落盘）。"""
    for g in gs.list_all():
        if g.get("name") == name:
            raise ValueError(f"组名已存在: {name}")

    existing = gs.list_all()
    if existing:
        root_group = existing[0]
        root_subnet = root_group.get("subnet", "")
        if root_subnet:
            if not validate_cidr(subnet):
                raise ValueError(f"子网格式不合法: {subnet}")
            if not is_subnet_of(subnet, root_subnet):
                raise ValueError(
                    f"子网 {subnet} 必须是根组「{root_group['name']}」"
                    f"子网 {root_subnet} 的子网"
                )

    config = load_config()
    global_subnet = config.global_subnet or ""
    conflicts = check_subnet_conflict(subnet, global_subnet, existing)
    if conflicts:
        raise ValueError("子网冲突: " + "; ".join(conflicts))


def cmd_add_group(args: argparse.Namespace) -> int:
    gs = GroupService()
    name = args.name.strip()
    subnet = args.subnet.strip()
    # ---------- 以下与是否试运行无关：与正式创建前相同的校验 ----------
    try:
        _validate_group_create(gs, name, subnet)
    except ValueError as e:
        logger.error("%s", e)
        print(str(e), file=sys.stderr)
        return 1

    # ---------- 最后一步：试运行仅输出；否则落库 ----------
    if args.dry_run:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "action": "add-group",
                    "name": name,
                    "subnet": subnet,
                    "note": "校验已通过；未写入 data/groups，未生成 UUID",
                },
                ensure_ascii=False,
            )
        )
        return 0

    try:
        g = gs.create(name=name, subnet=subnet)
    except ValueError as e:
        logger.error("%s", e)
        print(str(e), file=sys.stderr)
        return 1
    print(g["id"])
    logger.info("已创建组 %s id=%s subnet=%s", g["name"], g["id"], g["subnet"])
    return 0


def cmd_add_user(args: argparse.Namespace) -> int:
    # ---------- 与是否试运行无关：环境与参数校验 ----------
    _require_initialized()
    gs = GroupService()
    us = UserService()
    gid = _resolve_group_id(gs, group_id=args.group_id, group_name=args.group_name)
    pwd_en = bool(args.password)
    username = args.username.strip()
    if us._user_exists(username):
        msg = f"用户名已存在: {username}"
        logger.error("%s", msg)
        print(msg, file=sys.stderr)
        return 1

    # ---------- 最后一步：试运行仅输出；否则落库 ----------
    if args.dry_run:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "action": "add-user",
                    "username": username,
                    "group_id": gid,
                    "password_enabled": pwd_en,
                    "note": "校验已通过；未生成证书、未写用户 JSON、未更新组计数",
                },
                ensure_ascii=False,
            )
        )
        return 0

    try:
        u = us.create(
            username=username,
            group_id=gid,
            password_enabled=pwd_en,
            password=args.password if pwd_en else None,
        )
    except ValueError as e:
        logger.error("%s", e)
        print(str(e), file=sys.stderr)
        return 1
    except Exception as e:
        logger.exception("创建用户失败")
        print(str(e), file=sys.stderr)
        return 1
    print(u.username)
    logger.info("已创建用户 %s group_id=%s", u.username, gid)
    return 0


def cmd_add_firewall(args: argparse.Namespace) -> int:
    # ---------- 与是否试运行无关：解析 iptables、组装规则、模型与 priority 校验 ----------
    gs = GroupService()
    fw = FirewallRuleService()
    owner_id = _resolve_firewall_owner(
        gs,
        owner_type=args.owner_type,
        owner_id=args.owner_id,
        group_name=args.group_name,
        username=args.username,
    )

    specs: list[dict] = []
    if args.iptables_line:
        p = parse_iptables_line(args.iptables_line)
        if not p:
            print("无法解析 --iptables-line", file=sys.stderr)
            return 1
        specs.append(p)
    if args.iptables_file:
        try:
            specs.extend(iter_iptables_file(args.iptables_file))
        except OSError as e:
            print(str(e), file=sys.stderr)
            return 1

    if not specs:
        print("请指定 --iptables-file 或 --iptables-line。", file=sys.stderr)
        return 1

    pri = _next_priority(fw)
    payload_rows: list[dict] = []
    for spec in specs:
        row = dict(spec)
        chain = row.pop("_chain", "?")
        row.pop("_raw", None)
        if row.get("source_subnet") and not validate_cidr(row["source_subnet"]):
            print(f"跳过无效源 CIDR（{row.get('source_subnet')}）链={chain}", file=sys.stderr)
            pri += 10
            continue

        rule_data = {
            "owner_type": args.owner_type.strip().lower(),
            "owner_id": owner_id,
            **row,
            "priority": pri,
        }
        pri += 10
        try:
            FirewallRule(**rule_data)
        except Exception as e:
            logger.error("规则模型校验失败: %s", e)
            print(str(e), file=sys.stderr)
            return 1
        payload_rows.append(rule_data)

    if not payload_rows:
        return 1

    taken_pri = {int(r.get("priority", 0)) for r in fw.list_all_flat()}
    for rd in payload_rows:
        p = int(rd["priority"])
        if p in taken_pri:
            msg = f"优先级 {p} 已被占用（与现有规则冲突）"
            logger.error("%s", msg)
            print(msg, file=sys.stderr)
            return 1
        taken_pri.add(p)

    # ---------- 最后一步：试运行仅输出；否则落库并重建 iptables ----------
    if args.dry_run:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "action": "add-firewall",
                    "rules": payload_rows,
                    "note": "校验已通过；未写入 data/firewall，未执行 iptables 重建",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        logger.info("试运行预览防火墙规则 %d 条 owner=%s", len(payload_rows), owner_id)
        return 0

    created = 0
    for rule_data in payload_rows:
        try:
            rid = fw.create(rule_data)
            print(rid["id"])
            created += 1
        except ValueError as e:
            logger.error("创建规则失败: %s", e)
            print(str(e), file=sys.stderr)
            return 1

    logger.info("防火墙规则已导入 %d 条 owner=%s", created, owner_id)
    return 0 if created else 1


def cmd_hash_admin_password(args: argparse.Namespace) -> int:
    """交互读取管理员密码并只输出 Argon2id 哈希。"""

    del args
    password = getpass.getpass("管理员密码: ")
    confirmation = getpass.getpass("再次输入管理员密码: ")
    if password != confirmation:
        print("两次输入的管理员密码不一致。", file=sys.stderr)
        return 1

    try:
        password_hash = AdminAuthService().hash_password(password)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(password_hash)
    logger.info("已生成管理员密码哈希")
    return 0



def _print_json(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _print_rows(rows: list[dict], columns: list[tuple[str, str]]) -> None:
    """无第三方依赖的紧凑表格输出；脚本消费可改用 --json。"""
    if not rows:
        print("（无数据）")
        return
    values: list[list[str]] = []
    widths = [len(title) for _, title in columns]
    for row in rows:
        rendered = [str(row.get(key, "") if row.get(key) is not None else "") for key, _ in columns]
        values.append(rendered)
        for i, value in enumerate(rendered):
            widths[i] = max(widths[i], len(value))
    header = "  ".join(title.ljust(widths[i]) for i, (_, title) in enumerate(columns))
    print(header)
    print("  ".join("-" * w for w in widths))
    for rendered in values:
        print("  ".join(value.ljust(widths[i]) for i, value in enumerate(rendered)))


def _resolve_group(
    gs: GroupService,
    *,
    group_id: str | None = None,
    group_name: str | None = None,
) -> dict:
    gid = _resolve_group_id(gs, group_id=group_id, group_name=group_name)
    group = gs.get(gid)
    if not group:
        raise SystemExit(f"组不存在: {gid}")
    return group


def _require_user(us: UserService, username: str) -> dict:
    user = us.get(username.strip())
    if not user or user.get("status") == "deleted":
        raise SystemExit(f"用户不存在或已删除: {username}")
    return user


def _dry_run_result(action: str, **payload: object) -> int:
    _print_json({"dry_run": True, "action": action, **payload})
    return 0


# -------------------- group --------------------

def cmd_group_list(args: argparse.Namespace) -> int:
    gs = GroupService()
    rows = gs.list_all()
    if args.json:
        _print_json(rows)
    else:
        _print_rows(
            rows,
            [
                ("id", "ID"),
                ("name", "名称"),
                ("subnet", "子网"),
                ("status", "状态"),
                ("user_count", "用户数"),
            ],
        )
    return 0


def cmd_group_show(args: argparse.Namespace) -> int:
    group = _resolve_group(
        GroupService(),
        group_id=args.group_id,
        group_name=args.group_name,
    )
    _print_json(group)
    return 0


def cmd_group_add(args: argparse.Namespace) -> int:
    return cmd_add_group(args)


def cmd_group_update(args: argparse.Namespace) -> int:
    gs = GroupService()
    current = _resolve_group(gs, group_id=args.group_id, group_name=args.group_name)
    name = str(args.name if args.name is not None else current.get("name") or "").strip()
    subnet = str(args.subnet if args.subnet is not None else current.get("subnet") or "").strip()
    if args.dry_run:
        return _dry_run_result(
            "group.update",
            group_id=current["id"],
            current={"name": current.get("name"), "subnet": current.get("subnet")},
            requested={"name": name, "subnet": subnet},
            note="未写入数据库，未同步根组相关 OpenVPN/防火墙配置",
        )
    try:
        updated = gs.update_details(str(current["id"]), name=name, subnet=subnet)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _print_json(updated)
    return 0


def cmd_group_delete(args: argparse.Namespace) -> int:
    gs = GroupService()
    group = _resolve_group(gs, group_id=args.group_id, group_name=args.group_name)
    if args.dry_run:
        return _dry_run_result(
            "group.delete",
            group_id=group["id"],
            name=group.get("name"),
            subnet=group.get("subnet"),
            note="未删除用户组",
        )
    try:
        gs.delete(str(group["id"]))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _print_json({"deleted": True, "id": group["id"], "name": group.get("name")})
    return 0


def _cmd_group_status(args: argparse.Namespace, desired: str) -> int:
    gs = GroupService()
    group = _resolve_group(gs, group_id=args.group_id, group_name=args.group_name)
    if args.dry_run:
        return _dry_run_result(
            f"group.{desired}",
            group_id=group["id"],
            from_status=group.get("status"),
            to_status="active" if desired == "enable" else "disabled",
        )
    try:
        updated = gs.enable(str(group["id"])) if desired == "enable" else gs.disable(str(group["id"]))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _print_json(updated)
    return 0


def cmd_group_enable(args: argparse.Namespace) -> int:
    return _cmd_group_status(args, "enable")


def cmd_group_disable(args: argparse.Namespace) -> int:
    return _cmd_group_status(args, "disable")


# -------------------- user --------------------

def _user_view_rows(us: UserService, rows: list[dict]) -> list[dict]:
    ip_map = us.list_ccd_virtual_ipv4_by_username()
    return [
        {
            **row,
            "virtual_ip": ip_map.get(str(row.get("username") or ""), ""),
        }
        for row in rows
    ]


def cmd_user_list(args: argparse.Namespace) -> int:
    us = UserService()
    rows = us.list_all()
    if args.group_id or args.group_name:
        gid = _resolve_group_id(
            GroupService(),
            group_id=args.group_id,
            group_name=args.group_name,
        )
        rows = [row for row in rows if str(row.get("group_id") or "") == gid]
    if args.status:
        rows = [row for row in rows if str(row.get("status") or "") == args.status]
    rows = _user_view_rows(us, rows)
    if args.json:
        _print_json(rows)
    else:
        _print_rows(
            rows,
            [
                ("username", "用户名"),
                ("group_id", "组ID"),
                ("virtual_ip", "VPN IP"),
                ("status", "状态"),
                ("cert_serial", "证书序列号"),
            ],
        )
    return 0


def cmd_user_show(args: argparse.Namespace) -> int:
    us = UserService()
    user = _require_user(us, args.username)
    user = dict(user)
    user["virtual_ip"] = us.list_ccd_virtual_ipv4_by_username().get(args.username, "")
    _print_json(user)
    return 0


def cmd_user_add(args: argparse.Namespace) -> int:
    return cmd_add_user(args)


def cmd_user_delete(args: argparse.Namespace) -> int:
    _require_initialized()
    us = UserService()
    user = _require_user(us, args.username)
    if args.dry_run:
        return _dry_run_result(
            "user.delete",
            username=args.username,
            group_id=user.get("group_id"),
            note="未吊销证书、未更新 CRL、未删除 OVPN/CCD/设备绑定",
        )
    try:
        us.delete(args.username)
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _print_json({"deleted": True, "username": args.username})
    return 0


def _cmd_user_status(args: argparse.Namespace, desired: str) -> int:
    us = UserService()
    user = _require_user(us, args.username)
    target = "active" if desired == "enable" else "disabled"
    current = str(user.get("status") or "")
    if args.dry_run:
        return _dry_run_result(
            f"user.{desired}",
            username=args.username,
            from_status=current,
            to_status=target,
        )
    if current != target:
        try:
            current = us.toggle_status(args.username)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    _print_json({"username": args.username, "status": current})
    return 0


def cmd_user_enable(args: argparse.Namespace) -> int:
    return _cmd_user_status(args, "enable")


def cmd_user_disable(args: argparse.Namespace) -> int:
    return _cmd_user_status(args, "disable")


def cmd_user_move(args: argparse.Namespace) -> int:
    _require_initialized()
    us = UserService()
    user = _require_user(us, args.username)
    gid = _resolve_group_id(
        GroupService(),
        group_id=args.group_id,
        group_name=args.group_name,
    )
    if args.dry_run:
        return _dry_run_result(
            "user.move",
            username=args.username,
            old_group_id=user.get("group_id"),
            new_group_id=gid,
            note="未修改 CCD、未重新分配 VPN IP、未踢当前会话",
        )
    try:
        result = us.change_group(args.username, gid)
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _print_json(result)
    return 0


def cmd_user_regenerate_ovpn(args: argparse.Namespace) -> int:
    _require_initialized()
    us = UserService()
    _require_user(us, args.username)
    if args.dry_run:
        return _dry_run_result(
            "user.regenerate-ovpn",
            username=args.username,
            note="未重建 OVPN 文件",
        )
    try:
        path = us.regenerate_ovpn(args.username)
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _print_json({"username": args.username, "ovpn_file_path": path})
    return 0


def cmd_user_kick(args: argparse.Namespace) -> int:
    us = UserService()
    _require_user(us, args.username)
    if args.dry_run:
        return _dry_run_result(
            "user.kick",
            username=args.username,
            note="未通过 OpenVPN Management Interface 踢下线",
        )
    us.kick_offline(args.username)
    _print_json({"username": args.username, "kicked": True})
    return 0



def cmd_user_device_show(args: argparse.Namespace) -> int:
    us = UserService()
    _require_user(us, args.username)
    binding = DeviceBindingService().get_binding(args.username)
    if binding is None:
        _print_json({"username": args.username, "bound": False, "binding": None})
        return 0
    _print_json(
        {
            "username": args.username,
            "bound": True,
            "binding": binding.model_dump(),
        }
    )
    return 0


def cmd_user_device_reset(args: argparse.Namespace) -> int:
    us = UserService()
    _require_user(us, args.username)
    binding_service = DeviceBindingService()
    binding = binding_service.get_binding(args.username)
    if args.dry_run:
        return _dry_run_result(
            "user.device.reset",
            username=args.username,
            bound=binding is not None,
            note="未删除设备绑定；下次连接不会因此重新绑定",
        )
    removed = binding_service.reset_binding(args.username)
    _print_json(
        {
            "username": args.username,
            "reset": removed,
            "message": "设备绑定已重置" if removed else "当前没有设备绑定",
        }
    )
    return 0


def _user_ovpn_path(us: UserService, username: str) -> Path:
    user = _require_user(us, username)
    raw = str(user.get("ovpn_file_path") or "").strip()
    if not raw:
        raise SystemExit(f"用户未记录 OVPN 文件路径: {username}")
    return Path(raw)


def cmd_user_ovpn_show(args: argparse.Namespace) -> int:
    us = UserService()
    path = _user_ovpn_path(us, args.username)
    _print_json(
        {
            "username": args.username,
            "ovpn_file_path": str(path),
            "exists": path.is_file(),
            "size": path.stat().st_size if path.is_file() else None,
        }
    )
    return 0


def cmd_user_ovpn_export(args: argparse.Namespace) -> int:
    us = UserService()
    source = _user_ovpn_path(us, args.username)
    if not source.is_file():
        print(f"OVPN 文件不存在: {source}", file=sys.stderr)
        return 1

    output = Path(args.output).expanduser()
    if output.exists() and output.is_dir():
        output = output / source.name
    elif str(args.output).endswith("/"):
        output.mkdir(parents=True, exist_ok=True)
        output = output / source.name

    if args.dry_run:
        return _dry_run_result(
            "user.ovpn.export",
            username=args.username,
            source=str(source),
            output=str(output),
            note="未复制包含客户端私钥的 OVPN 文件",
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output)
    try:
        output.chmod(0o600)
    except OSError:
        logger.warning("无法将导出的 OVPN 权限设置为 0600: %s", output)

    _print_json(
        {
            "username": args.username,
            "source": str(source),
            "exported_to": str(output),
            "size": output.stat().st_size,
        }
    )
    return 0


def cmd_user_ovpn_regenerate(args: argparse.Namespace) -> int:
    return cmd_user_regenerate_ovpn(args)

def _add_group_selector(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--group-id", default=None, help="组 UUID")
    parser.add_argument("--group-name", default=None, help="组名称（与 --group-id 二选一）")


def _add_dry_run(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="试运行：完成解析/定位后仅打印预览，不执行修改",
    )
    parser.set_defaults(dry_run=False)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cli.py",
        description=(
            "NiceVPN 命令行管理工具；亦可用: python main.py cli …\n\n"
            "推荐使用分层命令：group / user / add-firewall。"
            "旧命令 add-group / add-user 继续兼容。\n"
            "试运行：在命令行任意位置加入 --dry-run。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--dry-run", action="store_true", dest="dry_run_root", help=argparse.SUPPRESS)
    p.set_defaults(dry_run_root=False)
    sub = p.add_subparsers(dest="command", required=True)

    # ---------- group ----------
    group = sub.add_parser("group", help="用户组管理")
    group_sub = group.add_subparsers(dest="group_command", required=True)

    gl = group_sub.add_parser("list", help="列出用户组")
    gl.add_argument("--json", action="store_true", help="输出 JSON")
    gl.set_defaults(func=cmd_group_list, dry_run=False)

    gs = group_sub.add_parser("show", help="查看用户组详情")
    _add_group_selector(gs)
    gs.set_defaults(func=cmd_group_show, dry_run=False)

    ga = group_sub.add_parser("add", help="添加用户组")
    ga.add_argument("--name", required=True, help="组名称")
    ga.add_argument("--subnet", required=True, help="子网 CIDR，如 10.8.1.0/24")
    _add_dry_run(ga)
    ga.set_defaults(func=cmd_group_add)

    gu = group_sub.add_parser("update", help="修改用户组名称或子网")
    _add_group_selector(gu)
    gu.add_argument("--name", default=None, help="新组名称；省略则保持不变")
    gu.add_argument("--subnet", default=None, help="新子网 CIDR；省略则保持不变")
    _add_dry_run(gu)
    gu.set_defaults(func=cmd_group_update)

    gd = group_sub.add_parser("delete", help="删除用户组")
    _add_group_selector(gd)
    _add_dry_run(gd)
    gd.set_defaults(func=cmd_group_delete)

    ge = group_sub.add_parser("enable", help="启用用户组")
    _add_group_selector(ge)
    _add_dry_run(ge)
    ge.set_defaults(func=cmd_group_enable)

    gx = group_sub.add_parser("disable", help="停用用户组")
    _add_group_selector(gx)
    _add_dry_run(gx)
    gx.set_defaults(func=cmd_group_disable)

    # ---------- user ----------
    user = sub.add_parser("user", help="VPN 用户管理")
    user_sub = user.add_subparsers(dest="user_command", required=True)

    ul = user_sub.add_parser("list", help="列出用户")
    _add_group_selector(ul)
    ul.add_argument("--status", choices=("active", "disabled"), default=None, help="按状态筛选")
    ul.add_argument("--json", action="store_true", help="输出 JSON")
    ul.set_defaults(func=cmd_user_list, dry_run=False)

    ushow = user_sub.add_parser("show", help="查看用户详情")
    ushow.add_argument("username", help="用户名")
    ushow.set_defaults(func=cmd_user_show, dry_run=False)

    ua = user_sub.add_parser("add", help="添加 VPN 用户（需已初始化 PKI）")
    ua.add_argument("--username", required=True, help="用户名")
    _add_group_selector(ua)
    ua.add_argument("--password", default=None, help="客户端私钥加密密码；只传给 EasyRSA，不保存")
    _add_dry_run(ua)
    ua.set_defaults(func=cmd_user_add)

    ud = user_sub.add_parser("delete", help="删除用户并吊销证书")
    ud.add_argument("username", help="用户名")
    _add_dry_run(ud)
    ud.set_defaults(func=cmd_user_delete)

    ue = user_sub.add_parser("enable", help="启用用户")
    ue.add_argument("username", help="用户名")
    _add_dry_run(ue)
    ue.set_defaults(func=cmd_user_enable)

    ux = user_sub.add_parser("disable", help="停用用户并踢下线")
    ux.add_argument("username", help="用户名")
    _add_dry_run(ux)
    ux.set_defaults(func=cmd_user_disable)

    um = user_sub.add_parser("move", help="切换用户所属组并重新分配 VPN IP")
    um.add_argument("username", help="用户名")
    _add_group_selector(um)
    _add_dry_run(um)
    um.set_defaults(func=cmd_user_move)

    ur = user_sub.add_parser("regenerate-ovpn", help="按当前 PKI/组配置重建用户 OVPN")
    ur.add_argument("username", help="用户名")
    _add_dry_run(ur)
    ur.set_defaults(func=cmd_user_regenerate_ovpn)

    uk = user_sub.add_parser("kick", help="踢掉用户当前在线会话")
    uk.add_argument("username", help="用户名")
    _add_dry_run(uk)
    uk.set_defaults(func=cmd_user_kick)


    device = user_sub.add_parser("device", help="用户设备绑定管理")
    device_sub = device.add_subparsers(dest="user_device_command", required=True)

    ds = device_sub.add_parser("show", help="查看用户当前设备绑定")
    ds.add_argument("username", help="用户名")
    ds.set_defaults(func=cmd_user_device_show, dry_run=False)

    dr = device_sub.add_parser("reset", help="重置用户设备绑定")
    dr.add_argument("username", help="用户名")
    _add_dry_run(dr)
    dr.set_defaults(func=cmd_user_device_reset)

    ovpn = user_sub.add_parser("ovpn", help="用户 OVPN 配置管理")
    ovpn_sub = ovpn.add_subparsers(dest="user_ovpn_command", required=True)

    os = ovpn_sub.add_parser("show", help="查看 OVPN 文件路径和状态")
    os.add_argument("username", help="用户名")
    os.set_defaults(func=cmd_user_ovpn_show, dry_run=False)

    oe = ovpn_sub.add_parser("export", help="导出 OVPN 到本机指定路径")
    oe.add_argument("username", help="用户名")
    oe.add_argument("--output", "-o", required=True, help="目标文件或目录")
    _add_dry_run(oe)
    oe.set_defaults(func=cmd_user_ovpn_export)

    org = ovpn_sub.add_parser("regenerate", help="按当前 PKI/组配置重建 OVPN")
    org.add_argument("username", help="用户名")
    _add_dry_run(org)
    org.set_defaults(func=cmd_user_ovpn_regenerate)

    # ---------- backward-compatible flat commands ----------
    g = sub.add_parser("add-group", help="兼容旧命令：添加用户组")
    g.add_argument("--name", required=True, help="组名称")
    g.add_argument("--subnet", required=True, help="子网 CIDR，如 10.8.1.0/24")
    _add_dry_run(g)
    g.set_defaults(func=cmd_add_group)

    u = sub.add_parser("add-user", help="兼容旧命令：添加 VPN 用户")
    u.add_argument("--username", required=True, help="用户名")
    _add_group_selector(u)
    u.add_argument("--password", default=None, help="客户端私钥加密密码；仅传给 EasyRSA，不保存")
    _add_dry_run(u)
    u.set_defaults(func=cmd_add_user)

    # ---------- firewall (保持原行为) ----------
    fw = sub.add_parser("add-firewall", help="添加防火墙规则；可从 iptables 命令文本文件导入")
    fw.add_argument("--owner-type", required=True, choices=("group", "user"), help="规则归属类型")
    fw.add_argument("--owner-id", default=None, help="组 UUID（owner-type=group 时）")
    fw.add_argument("--group-name", default=None, help="组名（与 owner-id 二选一）")
    fw.add_argument("--username", default=None, help="用户名（owner-type=user 时）")
    iptables_file = fw.add_argument(
        "--iptables-file",
        default=None,
        help="每行一条完整命令，如 iptables -A INPUT -p udp --dport 1194 -j ACCEPT",
    )
    fw.add_argument("--iptables-line", default=None, help="单条 iptables 命令")
    _add_dry_run(fw)
    fw.set_defaults(func=cmd_add_firewall)
    if FilesCompleter is not None:
        iptables_file.completer = FilesCompleter()

    auth = sub.add_parser("hash-admin-password", help="交互生成管理后台使用的 Argon2id 密码哈希")
    auth.set_defaults(func=cmd_hash_admin_password, dry_run=False)

    return p

def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    argv, dry_run = _extract_dry_run(argv)

    parser = build_parser()
    # Tab 补全：见 deploy/shell/install-ovpn-cli-symlink.sh（固定命令 + register-python-argcomplete）
    if argcomplete is not None:
        argcomplete.autocomplete(parser)
    setup_logging()
    args = parser.parse_args(argv)
    args.dry_run = dry_run
    return int(args.func(args))
