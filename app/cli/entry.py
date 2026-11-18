# -*- coding: utf-8 -*-
"""NiceVPN CLI 命令实现与参数解析。"""
from __future__ import annotations

import argparse
import getpass
import json
import logging
import shutil
import sys
from pathlib import Path

from app.cli.iptables_parse import iter_iptables_file, parse_iptables_line
from app.core.config import load_config, save_config
from app.core.constants import ADMIN_INITIAL_PASSWORD_FILE
from app.services.firewall.rule_service import FirewallRuleService
from app.services.firewall.tag_service import TagService
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


def _resolve_group_ref(gs: GroupService, group_ref: str) -> str:
    ref = str(group_ref or "").strip()
    for group in gs.list_all():
        if str(group.get("id") or "") == ref or str(group.get("name") or "") == ref:
            return str(group["id"])
    raise SystemExit(f"用户组不存在: {ref}")


def _resolve_tag_ref(ts: TagService, tag_ref: str) -> dict:
    ref = str(tag_ref or "").strip()
    for tag in ts.list_all():
        if str(tag.get("id") or "") == ref or str(tag.get("name") or "") == ref:
            return tag
    raise SystemExit(f"标签不存在: {ref}")


def _resolve_firewall_owner(
    gs: GroupService,
    ts: TagService,
    *,
    owner_type: str,
    owner: str,
) -> str:
    ot = str(owner_type or "").strip().lower()
    value = str(owner or "").strip()
    if not value:
        raise SystemExit("请指定规则归属对象 --owner。")
    if ot == "group":
        return _resolve_group_ref(gs, value)
    if ot == "user":
        user = UserService().get(value)
        if not user or user.get("status") == "deleted":
            raise SystemExit(f"用户不存在或已删除: {value}")
        return value
    if ot == "tag":
        return str(_resolve_tag_ref(ts, value)["id"])
    raise SystemExit("owner-type 须为 group、user 或 tag。")


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


def cmd_group_add(args: argparse.Namespace) -> int:
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
                    "action": "group.add",
                    "name": name,
                    "subnet": subnet,
                    "note": "校验已通过；未写入 SQLite，未生成 UUID",
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


def cmd_user_add(args: argparse.Namespace) -> int:
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
                    "action": "user.add",
                    "username": username,
                    "group_id": gid,
                    "password_enabled": pwd_en,
                    "note": "校验已通过；未生成证书、未写用户元数据、未更新组计数",
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


def cmd_firewall_rule_add(args: argparse.Namespace) -> int:
    # ---------- 与是否试运行无关：解析 iptables、组装规则、模型与 priority 校验 ----------
    gs = GroupService()
    fw = FirewallRuleService()
    ts = TagService()
    owner_id = _resolve_firewall_owner(
        gs, ts, owner_type=args.owner_type, owner=args.owner,
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
                    "action": "firewall.rule.add",
                    "rules": payload_rows,
                    "note": "校验已通过；未写入 SQLite，未执行 iptables 重建",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        logger.info("试运行预览防火墙规则 %d 条 owner=%s", len(payload_rows), owner_id)
        return 0

    try:
        created_rows = fw.create_many(payload_rows)
    except (ValueError, RuntimeError) as exc:
        logger.error("创建防火墙规则失败: %s", exc)
        print(str(exc), file=sys.stderr)
        return 1
    for row in created_rows:
        print(row["id"])
    logger.info("防火墙规则已导入 %d 条 owner=%s", len(created_rows), owner_id)
    return 0 if created_rows else 1


def cmd_admin_password_hash(args: argparse.Namespace) -> int:
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



def cmd_admin_password_reset(args: argparse.Namespace) -> int:
    """交互重置后台密码，写入根目录 YAML 中的 Argon2id 哈希。"""

    del args
    password = getpass.getpass("新管理员密码: ")
    confirmation = getpass.getpass("再次输入新密码: ")
    if password != confirmation:
        print("两次输入的新密码不一致。", file=sys.stderr)
        return 1
    try:
        new_hash = AdminAuthService().hash_password(password)
        config = load_config()
        config["admin_username"] = str(config.get("admin_username") or "admin")
        config["admin_login_enabled"] = True
        config["admin_password"] = None
        config["admin_password_hash"] = new_hash
        save_config(config)
        ADMIN_INITIAL_PASSWORD_FILE.unlink(missing_ok=True)
    except (OSError, ValueError) as exc:
        print(f"重置失败: {exc}", file=sys.stderr)
        return 1
    print("后台密码已更新且登录保护已开启，请重启 NiceVPN 生效。")
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


# -------------------- tag --------------------

def _resolve_tag(ts: TagService, tag_ref: str) -> dict:
    return _resolve_tag_ref(ts, tag_ref)


def _tag_member_payload(ts: TagService, tag: dict) -> dict:
    tag_id = str(tag["id"])
    members = ts.store.get_tag_members(tag_id)
    groups_by_id = {str(row["id"]): row for row in GroupService().list_all()}
    return {
        "tag": tag,
        "users": members.get("usernames") or [],
        "groups": [
            {"id": gid, "name": str(groups_by_id.get(gid, {}).get("name") or "")}
            for gid in (members.get("group_ids") or [])
        ],
        "center_firewall_rule_count": ts.rule_count(tag_id),
        "peer_references": ts.list_peer_tag_references(tag_id),
    }


def cmd_tag_list(args: argparse.Namespace) -> int:
    ts = TagService()
    counts = ts.store.list_tag_counts()
    rows = []
    for tag in ts.list_all():
        count = counts.get(str(tag["id"]), {})
        rows.append({
            **tag,
            "user_count": int(count.get("users", 0)),
            "group_count": int(count.get("groups", 0)),
            "rule_count": int(count.get("rules", 0)),
        })
    if args.json:
        _print_json(rows)
    else:
        _print_rows(rows, [
            ("name", "标签"), ("id", "ID"), ("description", "说明"),
            ("user_count", "用户"), ("group_count", "用户组"),
            ("rule_count", "中心规则"), ("device_binding_exempt", "设备豁免"),
            ("sso_required", "SSO"),
        ])
    return 0


def cmd_tag_show(args: argparse.Namespace) -> int:
    ts = TagService()
    _print_json(_tag_member_payload(ts, _resolve_tag(ts, args.tag_ref)))
    return 0


def cmd_tag_add(args: argparse.Namespace) -> int:
    ts = TagService()
    name = str(args.name or "").strip()
    if not name:
        print("标签名称不能为空。", file=sys.stderr)
        return 1
    if any(str(tag.get("name") or "") == name for tag in ts.list_all()):
        print(f"标签名称已存在: {name}", file=sys.stderr)
        return 1
    payload = {
        "name": name,
        "description": args.description or "",
        "device_binding_exempt": args.device_binding_exempt,
        "sso_required": args.sso_required,
    }
    if args.dry_run:
        return _dry_run_result("tag.add", **payload)
    try:
        tag = ts.create(
            name,
            description=payload["description"],
            device_binding_exempt=payload["device_binding_exempt"],
            sso_required=payload["sso_required"],
        )
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _print_json(tag)
    return 0


def cmd_tag_update(args: argparse.Namespace) -> int:
    ts = TagService()
    current = _resolve_tag(ts, args.tag_ref)
    name = str(args.name if args.name is not None else current.get("name") or "").strip()
    description = str(
        args.description if args.description is not None else current.get("description") or ""
    )
    if not name:
        print("标签名称不能为空。", file=sys.stderr)
        return 1
    if any(
        str(tag.get("id") or "") != str(current["id"])
        and str(tag.get("name") or "") == name
        for tag in ts.list_all()
    ):
        print(f"标签名称已存在: {name}", file=sys.stderr)
        return 1
    if (
        args.name is None and args.description is None
        and args.device_binding_exempt is None and args.sso_required is None
    ):
        print("请至少指定一个需要修改的标签属性。", file=sys.stderr)
        return 2
    if args.dry_run:
        return _dry_run_result(
            "tag.update", tag_id=current["id"],
            current={
                "name": current.get("name"), "description": current.get("description"),
                "device_binding_exempt": current.get("device_binding_exempt", False),
                "sso_required": current.get("sso_required", False),
            },
            requested={
                "name": name, "description": description,
                "device_binding_exempt": args.device_binding_exempt,
                "sso_required": args.sso_required,
            },
        )
    try:
        updated = ts.update(
            str(current["id"]), name=name, description=description,
            device_binding_exempt=args.device_binding_exempt,
            sso_required=args.sso_required,
        )
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _print_json(updated)
    return 0


def cmd_tag_delete(args: argparse.Namespace) -> int:
    ts = TagService()
    tag = _resolve_tag(ts, args.tag_ref)
    members = ts.store.get_tag_members(str(tag["id"]))
    peer_refs = ts.list_peer_tag_references(str(tag["id"]))
    rule_count = ts.rule_count(str(tag["id"]))
    blockers = {
        "users": len(members.get("usernames") or []),
        "groups": len(members.get("group_ids") or []),
        "center_firewall_rules": rule_count,
        "peer_references": peer_refs,
    }
    if args.dry_run:
        return _dry_run_result(
            "tag.delete", tag_id=tag["id"], name=tag.get("name"),
            blockers=blockers,
            deletable=not (
                blockers["users"] or blockers["groups"]
                or blockers["center_firewall_rules"] or blockers["peer_references"]
            ),
        )
    try:
        ts.delete(str(tag["id"]))
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _print_json({"deleted": True, "id": tag["id"], "name": tag.get("name")})
    return 0


def cmd_tag_member_list(args: argparse.Namespace) -> int:
    ts = TagService()
    tag = _resolve_tag(ts, args.tag_ref)
    members = ts.store.get_tag_members(str(tag["id"]))
    groups_by_id = {str(row["id"]): row for row in GroupService().list_all()}
    _print_json({
        "tag_id": tag["id"],
        "tag_name": tag.get("name"),
        "users": members.get("usernames") or [],
        "groups": [
            {"id": gid, "name": str(groups_by_id.get(gid, {}).get("name") or "")}
            for gid in (members.get("group_ids") or [])
        ],
    })
    return 0


def cmd_tag_member_change(args: argparse.Namespace) -> int:
    ts = TagService()
    tag = _resolve_tag(ts, args.tag_ref)
    operation = args.operation
    current = ts.store.get_tag_members(str(tag["id"]))
    current_users = set(current.get("usernames") or [])
    current_groups = set(current.get("group_ids") or [])
    requested_users = {
        str(value).strip() for value in (args.usernames or []) if str(value).strip()
    }
    gs = GroupService()
    requested_groups = {_resolve_group_ref(gs, value) for value in (args.groups or [])}
    if operation != "clear" and not requested_users and not requested_groups:
        print("请至少指定一个 --user 或 --group；清空关联请使用 tag member clear。", file=sys.stderr)
        return 2
    us = UserService()
    for username in requested_users:
        user = us.get(username)
        if not user or user.get("status") == "deleted":
            print(f"用户不存在或已删除: {username}", file=sys.stderr)
            return 1
    if operation == "add":
        desired_users, desired_groups = current_users | requested_users, current_groups | requested_groups
    elif operation == "remove":
        desired_users, desired_groups = current_users - requested_users, current_groups - requested_groups
    elif operation == "set":
        desired_users, desired_groups = requested_users, requested_groups
    elif operation == "clear":
        desired_users, desired_groups = set(), set()
    else:
        raise SystemExit(f"未知标签成员操作: {operation}")
    before = {"users": sorted(current_users), "groups": sorted(current_groups)}
    after = {"users": sorted(desired_users), "groups": sorted(desired_groups)}
    if args.dry_run:
        return _dry_run_result(
            f"tag.member.{operation}", tag_id=tag["id"], before=before, after=after,
            note="未修改 SQLite 标签关联，未刷新中心/对端防火墙和 Mesh 路由",
        )
    try:
        ts.set_members(str(tag["id"]), usernames=after["users"], group_ids=after["groups"])
    except (ValueError, RuntimeError, OSError) as exc:
        logger.exception("更新标签成员失败 tag=%s", tag["id"])
        print(str(exc), file=sys.stderr)
        return 1
    _print_json({
        "tag_id": tag["id"], "tag_name": tag.get("name"),
        "changed": before != after, "members": after,
    })
    return 0


# -------------------- firewall rules --------------------

def cmd_firewall_rule_list(args: argparse.Namespace) -> int:
    rows = FirewallRuleService().list_all_flat()
    if args.owner_type:
        rows = [row for row in rows if str(row.get("owner_type") or "") == args.owner_type]
    if args.owner_id:
        rows = [row for row in rows if str(row.get("owner_id") or "") == args.owner_id]
    rows.sort(key=lambda row: (int(row.get("priority") or 0), str(row.get("id") or "")))
    if args.json:
        _print_json(rows)
    else:
        _print_rows(rows, [
            ("id", "规则 ID"), ("owner_type", "归属类型"), ("owner_id", "归属 ID"),
            ("priority", "优先级"), ("action", "动作"), ("source_subnet", "源网段"),
            ("dest_ip", "目标 IP"), ("dest_port", "目标端口"), ("protocol", "协议"),
            ("enabled", "启用"), ("description", "说明"),
        ])
    return 0


def cmd_firewall_rule_delete(args: argparse.Namespace) -> int:
    fw = FirewallRuleService()
    rule = next((row for row in fw.list_all_flat() if str(row.get("id")) == args.rule_id), None)
    if rule is None:
        print(f"防火墙规则不存在: {args.rule_id}", file=sys.stderr)
        return 1
    if args.dry_run:
        return _dry_run_result("firewall.rule.delete", rule=rule)
    try:
        fw.delete(args.rule_id)
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _print_json({"deleted": True, "id": args.rule_id})
    return 0


def _cmd_firewall_rule_status(args: argparse.Namespace, enabled: bool) -> int:
    fw = FirewallRuleService()
    rule = next((row for row in fw.list_all_flat() if str(row.get("id")) == args.rule_id), None)
    if rule is None:
        print(f"防火墙规则不存在: {args.rule_id}", file=sys.stderr)
        return 1
    if args.dry_run:
        return _dry_run_result(
            "firewall.rule.enable" if enabled else "firewall.rule.disable",
            rule_id=args.rule_id, from_enabled=bool(rule.get("enabled", True)),
            to_enabled=enabled,
        )
    try:
        fw.set_enabled(args.rule_id, enabled)
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _print_json({"id": args.rule_id, "enabled": enabled})
    return 0


def cmd_firewall_rule_enable(args: argparse.Namespace) -> int:
    return _cmd_firewall_rule_status(args, True)


def cmd_firewall_rule_disable(args: argparse.Namespace) -> int:
    return _cmd_firewall_rule_status(args, False)


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
        prog=Path(sys.argv[0]).name,
        description=(
            "NiceVPN 命令行管理工具\n\n"
            "按资源分组：group / user / tag / firewall / admin。\n"
            "修改操作可在任意位置加入 --dry-run 预览。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--dry-run", action="store_true", dest="dry_run_root", help=argparse.SUPPRESS)
    p.set_defaults(dry_run_root=False)
    sub = p.add_subparsers(dest="command", required=True)

    group = sub.add_parser("group", help="用户组管理")
    group_sub = group.add_subparsers(dest="group_command", required=True)
    gl = group_sub.add_parser("list", help="列出用户组")
    gl.add_argument("--json", action="store_true", help="输出 JSON")
    gl.set_defaults(func=cmd_group_list, dry_run=False)
    gs = group_sub.add_parser("show", help="查看用户组详情")
    _add_group_selector(gs)
    gs.set_defaults(func=cmd_group_show, dry_run=False)
    ga = group_sub.add_parser("add", help="创建用户组")
    ga.add_argument("--name", required=True, help="组名称")
    ga.add_argument("--subnet", required=True, help="子网 CIDR，如 10.8.1.0/24")
    _add_dry_run(ga)
    ga.set_defaults(func=cmd_group_add)
    gu = group_sub.add_parser("update", help="修改用户组名称或子网")
    _add_group_selector(gu)
    gu.add_argument("--name", default=None, help="新组名称")
    gu.add_argument("--subnet", default=None, help="新子网 CIDR")
    _add_dry_run(gu)
    gu.set_defaults(func=cmd_group_update)
    for action, help_text, func in (
        ("delete", "删除用户组", cmd_group_delete),
        ("enable", "启用用户组", cmd_group_enable),
        ("disable", "停用用户组", cmd_group_disable),
    ):
        gp = group_sub.add_parser(action, help=help_text)
        _add_group_selector(gp)
        _add_dry_run(gp)
        gp.set_defaults(func=func)

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
    ua = user_sub.add_parser("add", help="创建 VPN 用户（需已初始化 PKI）")
    ua.add_argument("--username", required=True, help="用户名")
    _add_group_selector(ua)
    ua.add_argument("--password", default=None, help="客户端私钥加密密码；只传给 EasyRSA，不保存")
    _add_dry_run(ua)
    ua.set_defaults(func=cmd_user_add)
    for action, help_text, func in (
        ("delete", "删除用户并吊销证书", cmd_user_delete),
        ("enable", "启用用户", cmd_user_enable),
        ("disable", "停用用户并踢下线", cmd_user_disable),
        ("kick", "踢掉用户当前在线会话", cmd_user_kick),
    ):
        up = user_sub.add_parser(action, help=help_text)
        up.add_argument("username", help="用户名")
        _add_dry_run(up)
        up.set_defaults(func=func)
    um = user_sub.add_parser("move", help="切换用户所属组并重新分配 VPN IP")
    um.add_argument("username", help="用户名")
    _add_group_selector(um)
    _add_dry_run(um)
    um.set_defaults(func=cmd_user_move)
    device = user_sub.add_parser("device", help="用户设备绑定管理")
    device_sub = device.add_subparsers(dest="user_device_command", required=True)
    ds = device_sub.add_parser("show", help="查看用户当前设备绑定")
    ds.add_argument("username", help="用户名")
    ds.set_defaults(func=cmd_user_device_show, dry_run=False)
    dr = device_sub.add_parser("reset", help="重置用户设备绑定")
    dr.add_argument("username", help="用户名")
    _add_dry_run(dr)
    dr.set_defaults(func=cmd_user_device_reset)
    ovpn = user_sub.add_parser("ovpn", help="用户客户端配置管理")
    ovpn_sub = ovpn.add_subparsers(dest="user_ovpn_command", required=True)
    os = ovpn_sub.add_parser("show", help="查看 OVPN 文件路径和状态")
    os.add_argument("username", help="用户名")
    os.set_defaults(func=cmd_user_ovpn_show, dry_run=False)
    oe = ovpn_sub.add_parser("export", help="导出 OVPN 到本机指定路径")
    oe.add_argument("username", help="用户名")
    oe.add_argument("--output", "-o", required=True, help="目标文件或目录")
    _add_dry_run(oe)
    oe.set_defaults(func=cmd_user_ovpn_export)
    org = ovpn_sub.add_parser("regenerate", help="按当前 PKI/组配置重建用户 OVPN")
    org.add_argument("username", help="用户名")
    _add_dry_run(org)
    org.set_defaults(func=cmd_user_ovpn_regenerate)

    tag = sub.add_parser("tag", help="标签管理（含成员关联）")
    tag_sub = tag.add_subparsers(dest="tag_command", required=True)
    tl = tag_sub.add_parser("list", help="列出标签及关联数量")
    tl.add_argument("--json", action="store_true", help="输出 JSON")
    tl.set_defaults(func=cmd_tag_list, dry_run=False)
    tshow = tag_sub.add_parser("show", help="查看标签、成员和引用详情")
    tshow.add_argument("tag_ref", metavar="TAG", help="标签名称或 ID")
    tshow.set_defaults(func=cmd_tag_show, dry_run=False)
    ta = tag_sub.add_parser("add", help="创建标签")
    ta.add_argument("--name", required=True, help="标签名称")
    ta.add_argument("--description", default="", help="标签说明")
    ta.add_argument("--device-binding-exempt", action="store_true", help="该标签成员豁免设备绑定")
    ta.add_argument("--sso-required", action="store_true", help="该标签成员必须通过企业 SSO")
    _add_dry_run(ta)
    ta.set_defaults(func=cmd_tag_add)
    tu = tag_sub.add_parser("update", help="修改标签属性")
    tu.add_argument("tag_ref", metavar="TAG", help="标签名称或 ID")
    tu.add_argument("--name", default=None, help="新标签名称")
    tu.add_argument("--description", default=None, help="新标签说明")
    ex = tu.add_mutually_exclusive_group()
    ex.add_argument("--device-binding-exempt", dest="device_binding_exempt", action="store_true")
    ex.add_argument("--no-device-binding-exempt", dest="device_binding_exempt", action="store_false")
    sso = tu.add_mutually_exclusive_group()
    sso.add_argument("--sso-required", dest="sso_required", action="store_true")
    sso.add_argument("--no-sso-required", dest="sso_required", action="store_false")
    tu.set_defaults(device_binding_exempt=None, sso_required=None)
    _add_dry_run(tu)
    tu.set_defaults(func=cmd_tag_update)
    td = tag_sub.add_parser("delete", help="删除未被使用的标签")
    td.add_argument("tag_ref", metavar="TAG", help="标签名称或 ID")
    _add_dry_run(td)
    td.set_defaults(func=cmd_tag_delete)
    member = tag_sub.add_parser("member", help="管理标签关联的用户和用户组")
    member_sub = member.add_subparsers(dest="tag_member_command", required=True)
    tml = member_sub.add_parser("list", help="列出标签成员")
    tml.add_argument("tag_ref", metavar="TAG", help="标签名称或 ID")
    tml.set_defaults(func=cmd_tag_member_list, dry_run=False)
    for action, help_text in (
        ("add", "向标签添加用户或用户组"),
        ("remove", "从标签移除用户或用户组"),
        ("set", "整体替换标签成员"),
        ("clear", "清空标签的全部成员"),
    ):
        tm = member_sub.add_parser(action, help=help_text)
        tm.add_argument("tag_ref", metavar="TAG", help="标签名称或 ID")
        if action != "clear":
            tm.add_argument("--user", dest="usernames", action="append", default=[], help="用户名，可重复指定")
            tm.add_argument("--group", dest="groups", action="append", default=[], help="用户组名称或 ID，可重复指定")
        else:
            tm.set_defaults(usernames=[], groups=[])
        _add_dry_run(tm)
        tm.set_defaults(func=cmd_tag_member_change, operation=action)

    firewall = sub.add_parser("firewall", help="中心防火墙规则管理")
    firewall_sub = firewall.add_subparsers(dest="firewall_command", required=True)
    rule = firewall_sub.add_parser("rule", help="中心防火墙规则")
    rule_sub = rule.add_subparsers(dest="firewall_rule_command", required=True)
    frl = rule_sub.add_parser("list", help="列出防火墙规则")
    frl.add_argument("--owner-type", choices=("group", "user", "tag"), default=None)
    frl.add_argument("--owner-id", default=None, help="按归属对象 ID 筛选")
    frl.add_argument("--json", action="store_true", help="输出 JSON")
    frl.set_defaults(func=cmd_firewall_rule_list, dry_run=False)
    fra = rule_sub.add_parser("add", help="从 iptables 命令创建中心防火墙规则")
    fra.add_argument("--owner-type", required=True, choices=("group", "user", "tag"), help="规则归属类型")
    fra.add_argument("--owner", required=True, help="归属对象名称或 ID")
    iptables_file = fra.add_argument(
        "--iptables-file", default=None,
        help="每行一条完整 iptables 命令，如 iptables -A INPUT -p udp --dport 1194 -j ACCEPT",
    )
    fra.add_argument("--iptables-line", default=None, help="单条 iptables 命令")
    _add_dry_run(fra)
    fra.set_defaults(func=cmd_firewall_rule_add)
    if FilesCompleter is not None:
        iptables_file.completer = FilesCompleter()
    for action, help_text, func in (
        ("delete", "删除防火墙规则", cmd_firewall_rule_delete),
        ("enable", "启用防火墙规则", cmd_firewall_rule_enable),
        ("disable", "停用防火墙规则", cmd_firewall_rule_disable),
    ):
        fp = rule_sub.add_parser(action, help=help_text)
        fp.add_argument("rule_id", help="规则 ID")
        _add_dry_run(fp)
        fp.set_defaults(func=func)

    admin = sub.add_parser("admin", help="管理后台工具")
    admin_sub = admin.add_subparsers(dest="admin_command", required=True)
    password = admin_sub.add_parser("password", help="管理员密码管理")
    password_sub = password.add_subparsers(dest="admin_password_command", required=True)
    password_hash = password_sub.add_parser("hash", help="交互生成 Argon2id 密码哈希")
    password_hash.set_defaults(func=cmd_admin_password_hash, dry_run=False)
    password_reset = password_sub.add_parser("reset", help="交互重置管理后台密码并启用登录")
    password_reset.set_defaults(func=cmd_admin_password_reset, dry_run=False)
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
