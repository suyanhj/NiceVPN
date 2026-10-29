# -*- coding: utf-8 -*-
"""Firewall 页面纯 UI/数据转换辅助。

这里仅放不依赖 NiceGUI 页面实例状态的逻辑，便于独立测试并降低
`app.ui.pages.firewall.FirewallPage` 的单文件复杂度。页面类保留兼容包装方法，
避免本轮结构重构改变既有调用接口或业务行为。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.core.constants import CCD_DIR
from app.services.firewall.simple_rule_import import (
    peer_rests_from_simplified_line,
    try_parse_simplified_line,
)
from app.ui.adapters.firewall import parse_remote_rule_hints
from app.utils.cidr import validate_cidr
from app.ui.components.data_grid_renderers import (
    ACTION_BUTTON_RENDERER,
    STATUS_PILL_RENDERER,
)

logger = logging.getLogger(__name__)


def read_ccd_vpn_ips(usernames: list[str]) -> list[str]:
    """从多用户 CCD 读取 ifconfig-push 的隧道 IP（各用户至多一条）。"""

    ips: list[str] = []
    for username in usernames:
        path = CCD_DIR / str(username)
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[0] == "ifconfig-push":
                ips.append(parts[1])
                break
    return ips


def parse_user_source_for_create(
    text: str,
) -> tuple[str | None, list[str] | None]:
    """用户模式手填源解析为单 CIDR 或 IP 列表。"""

    value = (text or "").strip()
    if not value:
        return None, None
    chunks = [item.strip() for item in value.split(",") if item.strip()]
    if not chunks:
        return None, None
    cidrs = [item for item in chunks if validate_cidr(item)]
    if len(cidrs) == len(chunks) and len(chunks) == 1:
        return cidrs[0], None
    for item in chunks:
        if not validate_cidr(item) and re.match(r"^\d{1,3}(\.\d{1,3}){3}$", item):
            return None, list(chunks)
    raise ValueError("用户源手填需为单段 CIDR，或为逗号分隔的仅数字 IP 列表。")


def remote_row_to_edit_form_dict(row: dict) -> dict:
    """将远端工作副本一行转换为共享规则编辑表单字段。"""

    rest = str(row.get("rest") or "").strip()
    description = str(row.get("description") or "").strip()
    enabled = bool(row.get("enabled", True))
    spec = try_parse_simplified_line(rest) if rest else None
    if spec:
        return {
            "action": spec.action,
            "protocol": (spec.protocol or "all").lower(),
            "source_subnet": (spec.source or "").strip() or None,
            "source_ips": None,
            "dest_ip": (spec.dest or "").strip() or None,
            "dest_port": (spec.dest_port or "").strip() or None,
            "description": description,
            "enabled": enabled,
        }

    hints = parse_remote_rule_hints(rest)
    jump = (hints.get("action") or "ACCEPT").upper()
    action = {
        "ACCEPT": "accept",
        "DROP": "drop",
        "REJECT": "reject",
    }.get(jump, "accept")
    protocol = (hints.get("proto") or "all").strip()
    if protocol.upper() in ("ALL", "ANY", "0", ""):
        protocol = "all"
    else:
        protocol = protocol.lower()

    return {
        "action": action,
        "protocol": protocol,
        "source_subnet": (hints.get("source") or "").strip() or None,
        "source_ips": None,
        "dest_ip": (hints.get("dest") or "").strip() or None,
        "dest_port": (hints.get("dport") or "").strip() or None,
        "description": description,
        "enabled": enabled,
    }


def parse_source_text_for_remote_save(
    text: str,
) -> tuple[str | None, list[str] | None]:
    """远端保存时解析源字段，保持与中心创建规则一致。"""

    value = (text or "").strip()
    if not value:
        return None, None
    if "," in value:
        try:
            return parse_user_source_for_create(value)
        except ValueError:
            return value, None
    return value, None


def rows_from_remote_import_payload(raw: dict) -> list[dict]:
    """从导入 JSON 根对象解析远端规则工作副本 rows。"""

    if not isinstance(raw, dict):
        raise ValueError("JSON 根须为 object")
    if raw.get("rows") and isinstance(raw["rows"], list):
        out: list[dict] = []
        for value in raw["rows"]:
            if not isinstance(value, dict):
                continue
            rest = str(value.get("rest", "")).strip()
            if not rest:
                continue
            if "\n" in rest or "\r" in rest:
                raise ValueError("规则片段不得含换行")
            item: dict[str, Any] = {
                "rest": rest,
                "enabled": bool(value.get("enabled", True)),
            }
            description = str(value.get("description") or "").strip()
            if description:
                item["description"] = description
            if value.get("priority") is not None:
                try:
                    item["priority"] = int(value.get("priority"))
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"规则优先级无效: {value.get('priority')!r}"
                    ) from exc
            out.append(item)
        return out

    rests = [
        str(value).strip()
        for value in (raw.get("rests") or [])
        if str(value).strip()
    ]
    return [{"rest": rest, "enabled": True} for rest in rests]


def rows_from_iptables_paste(text: str, chain: str) -> list[dict]:
    """从 iptables 文本得到远端工作副本 rows。"""

    current_chain = (chain or "").strip()
    if not current_chain:
        raise ValueError("链名为空")
    prefix = f"-A {current_chain} "
    rows: list[dict] = []

    for line_number, line in enumerate(text.splitlines(), start=1):
        source = line.strip()
        if not source or source.startswith("#"):
            continue

        if source.startswith("-A "):
            if not source.startswith(prefix):
                raise ValueError(
                    f"第 {line_number} 行不属于当前对端链 {current_chain}，"
                    f"请只贴本链规则：{source[:120]}"
                )
            rest = source[len(prefix) :].strip()
            if not rest:
                continue
            if "\n" in rest or "\r" in rest:
                raise ValueError("规则片段不得含换行")
            rows.append({"rest": rest, "enabled": True})
            continue

        spec = try_parse_simplified_line(source)
        if spec is not None:
            for rest in peer_rests_from_simplified_line(spec):
                if "\n" in rest or "\r" in rest:
                    raise ValueError("规则片段不得含换行")
                rows.append({"rest": rest, "enabled": True})
        else:
            if "\n" in source or "\r" in source:
                raise ValueError("规则片段不得含换行")
            rows.append({"rest": source, "enabled": True})

    if not rows:
        raise ValueError(
            "未解析到任何规则。请贴入 ``iptables -S <链名>`` 输出，"
            "或每行一条规则片段（-s/-j/...）。"
        )
    return rows


def rows_from_remote_import_text_mixed(
    text: str,
    peer_id: str,
    chain_name: str,
) -> tuple[list[dict], str, bool]:
    """JSON 备份和 iptables 文本共用的远端导入入口。"""

    source = (text or "").strip()
    if source.startswith("{"):
        try:
            raw = json.loads(source)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 解析失败: {exc}") from exc

        incoming_peer_id = (
            str(raw.get("peer_id") or "").strip()
            if isinstance(raw, dict)
            else ""
        )
        if incoming_peer_id and incoming_peer_id != str(peer_id).strip():
            raise ValueError("JSON 中 peer_id 与当前所选对端不一致。")
        if not isinstance(raw, dict):
            raise ValueError("JSON 根须为 object")

        rows = rows_from_remote_import_payload(raw)
        chain = str(raw.get("chain") or chain_name)
        chain_exists = bool(raw.get("chain_exists", True))
        return rows, chain, chain_exists

    return rows_from_iptables_paste(source, chain_name), chain_name, True


def short_peer_id(peer_id: str) -> str:
    """用于紧凑 UI 的对端 ID。"""

    value = str(peer_id or "")
    if len(value) > 8:
        return f"{value[:8]}…"
    return value or "—"


def move_order(items: list[Any], old_index: int, new_index: int) -> list[Any]:
    """按拖拽前后索引生成新顺序，不修改调用方列表。"""

    if (
        not items
        or not 0 <= old_index < len(items)
        or not 0 <= new_index < len(items)
    ):
        logger.error(
            "拖拽索引越界: old=%s new=%s count=%s",
            old_index,
            new_index,
            len(items),
        )
        return []

    result = list(items)
    result.insert(new_index, result.pop(old_index))
    return result


def second_line_comment_text(
    data: dict,
    *,
    title_text: str,
    is_center_peer: bool = False,
    is_remote_row: bool = False,
    iptables_comment: str = "",
) -> str:
    """规则第二行只显示非重复的注释/说明。"""

    if is_center_peer:
        description = str(data.get("description") or "").strip()
        tag = str(data.get("_center_tag_line") or "").strip()
        if description and tag:
            return f"{description} | {tag}"
        return description or tag

    if is_remote_row:
        description = str(data.get("description") or "").strip()
        comment = str(iptables_comment or "").strip()
        return description or comment

    title = (title_text or "").strip()
    comment = str(data.get("comment") or "").strip()
    if comment:
        return comment
    description = str(data.get("description") or "").strip()
    if description and description != title:
        return description
    return ""


def remote_grid_columns(*, can_reorder: bool = False) -> list[dict[str, Any]]:
    """对端链规则列定义。"""

    number_column: dict[str, Any] = {
        "field": "row_number",
        "headerName": "#",
        "width": 85,
        "pinned": "left",
        "filter": False,
        "rowDrag": bool(can_reorder),
    }

    return [
        number_column,
        {
            "field": "status_label",
            "headerName": "状态",
            "width": 95,
            ":cellRenderer": STATUS_PILL_RENDERER,
            ":filter": "window.NiceVpnSetFilter",
            "filterParams": {
                "searchPlaceholder": "搜索状态...",
                "selectAllLabel": "(全选)",
                "showValuesFromOtherFilters": False,
                "noMatchesLabel": "没有匹配状态",
                "valueOrder": ["启用", "停用"],
            },
            "cellClass": "vpn-grid-renderer-cell",
        },
        {
            "field": "priority",
            "headerName": "优先级",
            "width": 105,
        },
        {
            "field": "protocol",
            "headerName": "协议",
            "width": 90,
            ":filter": "window.NiceVpnSetFilter",
            "filterParams": {
                "searchPlaceholder": "搜索协议...",
                "selectAllLabel": "(全选)",
                "showValuesFromOtherFilters": False,
                "noMatchesLabel": "没有匹配协议",
                "valueOrder": ["ALL", "TCP", "UDP", "ICMP"],
            },
        },
        {
            "field": "action_label",
            "headerName": "动作",
            "width": 105,
            ":cellRenderer": STATUS_PILL_RENDERER,
            ":filter": "window.NiceVpnSetFilter",
            "filterParams": {
                "searchPlaceholder": "搜索动作...",
                "selectAllLabel": "(全选)",
                "showValuesFromOtherFilters": False,
                "noMatchesLabel": "没有匹配动作",
                "valueOrder": ["ACCEPT", "DROP", "REJECT"],
            },
            "cellClass": "vpn-grid-renderer-cell",
        },
        {
            "field": "source",
            "headerName": "来源",
            "minWidth": 170,
            "flex": 1,
        },
        {
            "field": "dest",
            "headerName": "目标",
            "minWidth": 160,
            "flex": 1,
        },
        {
            "field": "dest_port",
            "headerName": "端口",
            "minWidth": 105,
        },
        {
            "field": "description",
            "headerName": "说明",
            "minWidth": 200,
            "flex": 1,
        },
        {
            "field": "raw_rest",
            "headerName": "原始规则",
            "minWidth": 320,
            "flex": 2,
        },
        {
            "field": "actions",
            "headerName": "操作",
            "width": 92,
            "minWidth": 92,
            "maxWidth": 92,
            "sortable": False,
            "filter": False,
            "resizable": False,
            "pinned": "right",
            "lockPinned": True,
            ":cellRenderer": ACTION_BUTTON_RENDERER,
            "cellClass": "vpn-grid-renderer-cell vpn-grid-action-cell",
        },
    ]


def center_grid_columns(
    *,
    can_reorder: bool = False,
    reorder_mode: bool = False,
) -> list[dict[str, Any]]:
    """中心策略列定义。"""

    priority_column: dict[str, Any] = {
        "field": "priority",
        "headerName": "优先级",
        "width": 105,
        "pinned": "left",
        "filter": False,
        "rowDrag": bool(can_reorder),
    }

    columns = [
        priority_column,
        {
            "field": "status_label",
            "headerName": "状态",
            "width": 100,
            ":cellRenderer": STATUS_PILL_RENDERER,
            ":filter": "window.NiceVpnSetFilter",
            "filterParams": {
                "searchPlaceholder": "搜索状态...",
                "selectAllLabel": "(全选)",
                "showValuesFromOtherFilters": False,
                "noMatchesLabel": "没有匹配状态",
                "valueOrder": ["启用", "停用"],
            },
            "cellClass": "vpn-grid-renderer-cell",
        },
        {
            "field": "owner_label",
            "headerName": "归属",
            "minWidth": 160,
            "flex": 1,
            ":filter": "window.NiceVpnSetFilter",
            "filterParams": {
                "searchPlaceholder": "搜索归属...",
                "selectAllLabel": "(全选)",
                "showValuesFromOtherFilters": False,
                "noMatchesLabel": "没有匹配归属",
            },
        },
        {
            "field": "kind_label",
            "headerName": "部署",
            "minWidth": 135,
            ":filter": "window.NiceVpnSetFilter",
            "filterParams": {
                "searchPlaceholder": "搜索部署类型...",
                "selectAllLabel": "(全选)",
                "showValuesFromOtherFilters": False,
                "noMatchesLabel": "没有匹配部署类型",
                "valueOrder": ["中心节点", "对端仅标识", "对端·中心放行"],
            },
        },
        {
            "field": "protocol",
            "headerName": "协议",
            "width": 95,
            ":filter": "window.NiceVpnSetFilter",
            "filterParams": {
                "searchPlaceholder": "搜索协议...",
                "selectAllLabel": "(全选)",
                "showValuesFromOtherFilters": False,
                "noMatchesLabel": "没有匹配协议",
                "valueOrder": [
                    "ALL",
                    "TCP",
                    "UDP",
                    "ICMP",
                    "SCTP",
                    "UDPLITE",
                    "ESP",
                    "AH",
                ],
            },
        },
        {
            "field": "action_label",
            "headerName": "动作",
            "width": 105,
            ":cellRenderer": STATUS_PILL_RENDERER,
            ":filter": "window.NiceVpnSetFilter",
            "filterParams": {
                "searchPlaceholder": "搜索动作...",
                "selectAllLabel": "(全选)",
                "showValuesFromOtherFilters": False,
                "noMatchesLabel": "没有匹配动作",
                "valueOrder": ["ACCEPT", "DROP", "REJECT", "UNKNOWN"],
            },
            "cellClass": "vpn-grid-renderer-cell",
        },
        {
            "field": "source",
            "headerName": "来源",
            "minWidth": 180,
            "flex": 1,
            "filter": False,
        },
        {
            "field": "dest",
            "headerName": "目标",
            "minWidth": 160,
            "flex": 1,
            "filter": False,
        },
        {
            "field": "dest_port",
            "headerName": "端口",
            "minWidth": 110,
            "filter": False,
        },
        {
            "field": "description",
            "headerName": "说明",
            "minWidth": 220,
            "flex": 1,
            "filter": False,
        },
        {
            "field": "id",
            "headerName": "规则 ID",
            "minWidth": 210,
            "filter": False,
        },
        {
            "field": "actions",
            "headerName": "操作",
            "width": 92,
            "minWidth": 92,
            "maxWidth": 92,
            "sortable": False,
            "filter": False,
            "resizable": False,
            "pinned": "right",
            "lockPinned": True,
            ":cellRenderer": ACTION_BUTTON_RENDERER,
            "cellClass": "vpn-grid-renderer-cell vpn-grid-action-cell",
        },
    ]

    if reorder_mode:
        for column in columns:
            column["sortable"] = False
            column["filter"] = False
            column.pop(":filter", None)
            column.pop("filterParams", None)
            if column.get("field") == "actions":
                column["hide"] = True

    return columns
