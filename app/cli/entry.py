# -*- coding: utf-8 -*-
"""命令行子命令：添加组 / 用户 / 从 iptables 文本导入防火墙规则。"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from app.cli.iptables_parse import iter_iptables_file, parse_iptables_line
from app.core.config import load_config
from app.services.firewall.rule_service import FirewallRuleService
from app.services.group.crud import GroupService
from app.services.user.crud import UserService
from app.utils.cidr import is_subnet_of, validate_cidr
from app.utils.logging_setup import setup_logging
from app.models.firewall import FirewallRule
from app.services.group.subnet import check_subnet_conflict

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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cli.py",
        description=(
            "OpenVPN 管理端命令行（核心操作）；亦可用: python main.py cli …\n\n"
            "试运行：在命令行**任意位置**加入 --dry-run（可多次，效果相同）。"
            "与正式命令使用同一套参数解析与业务校验，全部通过后仅打印 JSON，不写库、不签证书、不重建 iptables。\n"
            "Tab 补全：pip install argcomplete 后运行 deploy/shell/install-ovpn-cli-symlink.sh（见脚本内说明）。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # 与 _extract_dry_run 配合：真实 argv 会先剥离 --dry-run，此处仅供 argcomplete 识别根级补全
    p.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run_root",
        help=argparse.SUPPRESS,
    )
    p.set_defaults(dry_run_root=False)
    sub = p.add_subparsers(dest="command", required=True)

    g = sub.add_parser("add-group", help="添加用户组")
    g.add_argument("--name", required=True, help="组名称")
    g.add_argument("--subnet", required=True, help="子网 CIDR，如 10.8.1.0/24")
    g.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="试运行：校验通过后仅打印 JSON，不落库",
    )
    g.set_defaults(func=cmd_add_group, dry_run=False)

    u = sub.add_parser("add-user", help="添加 VPN 用户（需已初始化 PKI）")
    u.add_argument("--username", required=True, help="用户名")
    u.add_argument("--group-id", default=None, help="组 UUID")
    u.add_argument("--group-name", default=None, help="组名（与 --group-id 二选一）")
    u.add_argument("--password", default=None, help="若设置则启用账号密码认证")
    u.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="试运行：校验通过后仅打印 JSON，不落库",
    )
    u.set_defaults(func=cmd_add_user, dry_run=False)

    f = sub.add_parser(
        "add-firewall",
        help="添加防火墙规则；可从 iptables 命令文本文件导入",
    )
    f.add_argument(
        "--owner-type",
        required=True,
        choices=("group", "user"),
        help="规则归属类型",
    )
    f.add_argument("--owner-id", default=None, help="组 UUID（owner-type=group 时）")
    f.add_argument("--group-name", default=None, help="组名（与 owner-id 二选一）")
    f.add_argument("--username", default=None, help="用户名（owner-type=user 时）")
    iptables_file = f.add_argument(
        "--iptables-file",
        default=None,
        help="每行一条完整命令，如 iptables -A INPUT -p udp --dport 1194 -j ACCEPT",
    )
    f.add_argument(
        "--iptables-line",
        default=None,
        help="单条 iptables 命令（与文件二选一或同时使用，先 line 后 file）",
    )
    f.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="试运行：校验通过后仅打印 JSON，不落库",
    )
    f.set_defaults(func=cmd_add_firewall, dry_run=False)
    if FilesCompleter is not None:
        iptables_file.completer = FilesCompleter()

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
