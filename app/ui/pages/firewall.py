# -*- coding: utf-8 -*-
"""防火墙：中心 ``VPN_FORWARD`` 与对端机 ``VPN_PEER_*`` 链管理。

对端远端与数据一致性（须全体遵守）:

- **事实源在对端机**：以 SSH 上读到的用户链为唯一事实；切换策略 Tab 到「对端远端」或更换对端节点时，必须
  先 ``fetch_remote_peer_filter_chain_snapshot`` 再 ``record_from_fetch`` 落库（
  见 :mod:`app.services.peer_instance.remote_chain_cache` 与 :func:`_remote_fetch_chain_via_ssh`）。
- **本机文件是工作副本**：落库后一切列表、编辑、批处理、排序、导入均只以本地 JSON 为准，不得假定内存或旧文件
  与对端已一致而跳过拉取。
- **回写仅经 SSH 下发**：对端上链的增删改序仅通过
  :meth:`app.services.peer_instance.service.PeerService.apply_remote_peer_filter_chain_rests` 等路径
  把当前工作副本推上去，以保障「先拉真值 → 再改库 → 再推对端」。
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from nicegui import context, run, ui
from pydantic import ValidationError
from nicegui.functions.notify import ARG_MAP as _NICEGUI_NOTIFY_ARG_MAP

from app.core.constants import CCD_DIR
from app.models.firewall import FirewallRule
from app.services.firewall.iptables_mgr import IptablesManager
from app.services.firewall.rule_service import CE_PEER_RULE_PREFIX, FirewallRuleService
from app.services.firewall.simple_rule_import import (
    center_rule_payload_from_simplified,
    is_center_backup_json_text,
    parse_center_simplified_lines,
    remote_rests_from_create_fields,
    resolve_center_owner_type,
    source_fields_for_center,
    try_parse_simplified_line,
)
from app.services.group.crud import GroupService
from app.services.peer_instance import remote_chain_cache
from app.services.peer_instance.service import PeerService
from app.services.user.crud import UserService
from app.ui.adapters.firewall import (
    format_remote_cache_timestamp,
    parse_remote_rule_hints,
)
from app.ui.firewall_helpers import second_line_comment_text
from app.ui.pages.firewall_center import FirewallCenterGridMixin
from app.ui.pages.firewall_remote import FirewallRemoteMixin
from app.ui.components import confirm_dialog
from app.ui.components.actions import (
    create_action,
    danger_action,
    primary_action,
    secondary_action,
)
from app.ui.components.form_dialog import create_form_dialog
from app.ui.components.firewall_rule_form import render_firewall_rule_create_fields
from app.ui.components.toolbars import render_bulk_action_bar
from app.ui.copy_clipboard import copy_text_to_clipboard
from app.utils.cidr import validate_cidr

logger = logging.getLogger(__name__)


def _read_ccd_vpn_ips(usernames: list[str]) -> list[str]:
    """从多用户 CCD 读取 ifconfig-push 的隧道 IP（各用户至多取一条）。"""
    ips: list[str] = []
    for uname in usernames:
        p = CCD_DIR / str(uname)
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[0] == "ifconfig-push":
                ips.append(parts[1])
                break
    return ips


def _parse_lan_cidrs_text(raw: str) -> list[str]:
    """多行/逗号分隔的 CIDR 拆成列表。"""
    if not raw or not str(raw).strip():
        return []
    parts = re.split(r"[\s,;]+", str(raw).strip())
    return [p.strip() for p in parts if p.strip()]


def _hints_from_iptables_rest_line(rest: str) -> dict[str, str]:
    """兼容旧调用入口；解析逻辑统一由纯适配层维护。"""

    return parse_remote_rule_hints(rest)


def _remote_row_to_edit_form_dict(row: dict) -> dict:
    """将工作副本一行转为与中心「编辑中心规则」表单同形字段（无 id/owner）。"""
    rest = str(row.get("rest") or "").strip()
    desc = str(row.get("description") or "").strip()
    en = bool(row.get("enabled", True))
    spec = try_parse_simplified_line(rest) if rest else None
    if spec:
        return {
            "action": spec.action,
            "protocol": (spec.protocol or "all").lower(),
            "source_subnet": (spec.source or "").strip() or None,
            "source_ips": None,
            "dest_ip": (spec.dest or "").strip() or None,
            "dest_port": (spec.dest_port or "").strip() or None,
            "description": desc,
            "enabled": en,
        }
    h = _hints_from_iptables_rest_line(rest)
    j = (h.get("action") or "ACCEPT").upper()
    act = {"ACCEPT": "accept", "DROP": "drop", "REJECT": "reject"}.get(j, "accept")
    proto = (h.get("proto") or "all").strip()
    pu = proto.upper()
    if pu in ("ALL", "ANY", "0", ""):
        proto = "all"
    else:
        proto = proto.lower()
    return {
        "action": act,
        "protocol": proto,
        "source_subnet": (h.get("source") or "").strip() or None,
        "source_ips": None,
        "dest_ip": (h.get("dest") or "").strip() or None,
        "dest_port": (h.get("dport") or "").strip() or None,
        "description": desc,
        "enabled": en,
    }


def _parse_source_text_for_remote_save(t: str) -> tuple[str | None, list[str] | None]:
    """与中心保存时源解析一致：逗号优先走用户 IP 列表规则，否则整段为子网。"""
    t = (t or "").strip()
    if not t:
        return None, None
    if "," in t:
        try:
            return _parse_user_source_for_create(t)
        except ValueError:
            return t, None
    return t, None


# 中国标准时间（上海）：与 IANA Asia/Shanghai 当前一致，无夏令时；不依赖系统 tz 数据库
_TZ_UTC_PLUS_8 = timezone(timedelta(hours=8))


def _format_peer_cache_timestamp(raw: str | None) -> str:
    """兼容旧调用入口；缓存时间格式化统一由纯适配层维护。"""

    return format_remote_cache_timestamp(raw)


def _firewall_export_json_filename(stem: str) -> str:
    """下载用 JSON 文件名：``stem`` + ``_YYYYMMDD_HHMMSS``，避免连续导出覆盖。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r'[<>:"/\\|?*]', "_", (stem or "export").strip()) or "export"
    return f"{safe}_{ts}.json"


def _parse_user_source_for_create(text: str) -> tuple[str | None, list[str] | None]:
    """用户模式手填源：逗号分隔的 CIDR 或单 IP 列表。返回 (source_subnet, source_ips) 之一。"""
    t = (text or "").strip()
    if not t:
        return None, None
    chunks = [x.strip() for x in t.split(",") if x.strip()]
    if not chunks:
        return None, None
    cidrs = [c for c in chunks if validate_cidr(c)]
    if len(cidrs) == len(chunks) and len(chunks) == 1:
        return cidrs[0], None
    for c in chunks:
        if not validate_cidr(c) and re.match(r"^\d{1,3}(\.\d{1,3}){3}$", c):
            return None, list(chunks)
    raise ValueError("用户源手填需为单段 CIDR，或为逗号分隔的仅数字 IP 列表。")


class FirewallPage(FirewallCenterGridMixin, FirewallRemoteMixin):
    """中心 JSON 规则、中心侧对端虚拟行、以及对端机 filter 用户链的本地工作副本与 UI。

    对端机链的交互顺序见模块 docstring；本页在「对端远端」下通过 :meth:`_remote_fetch_chain_via_ssh` 与
    :meth:`_apply_remote_cache_to_peer` 落实「先拉后改再推」。"""

    def __init__(self) -> None:
        self.rule_service = FirewallRuleService()
        self._peer_service = PeerService()
        self.current_owner_id: str = ""
        self.rules_container: ui.column | None = None
        self.selected_rule_ids: set = set()
        # 对端链 ``rows`` 下标，与中心 ``selected_rule_ids`` 互斥使用（按页签清理）
        self._selected_remote_indices: set[int] = set()
        self._visible_center_rule_ids: list[str] = []
        self._visible_remote_indices: list[int] = []
        # 与 ``list_unified_flat`` 的全局排序一致：留空 = 全量
        self._is_unified_list: bool = True
        self._center_tab_active: bool = True
        self._strat_tabs: ui.tabs | None = None
        self._remote_peer_select: ui.select | None = None
        # 对端机链可滚动列表区，与中心 ``rules_container`` 同构
        self._remote_list_container: ui.column | None = None
        self._remote_tab_shell: ui.column | None = None
        # 供后台 asyncio Task 发 JS/通知：无当前 slot 栈时不能依赖 context.client
        self._ng_client: Any = None
        # 导入：上传 .json/.txt 时的分支；与当前框内文本一致时保留，手改后清空
        self._import_source_file_mode: str | None = None
        self._import_last_uploaded_text: str | None = None
        self._center_import_running = False
        self._center_import_result: dict[str, Any] | None = None
        self._center_import_timer = None
        self._center_grid_container = None
        self._center_grid_view = None
        self._center_search_controls = None
        self._center_search_input = None
        # 归属筛选移入 AG Grid 列头，避免工具栏与表格重复。
        self._pending_center_owner_id = ""
        self._center_filter_model: dict[str, Any] = {}
        self._center_grid_sort_active = False
        self._center_all_rows: list[dict[str, Any]] = []
        self._center_display_rows: list[dict[str, Any]] = []
        self._center_raw_by_id: dict[str, dict[str, Any]] = {}
        self._center_reorder_allowed = False
        self._center_reorder_mode = False
        self._pending_center_order: list[str] = []
        self._center_reorder_original_order: list[str] = []
        self._center_reorder_button = None
        self._center_save_reorder_button = None
        self._center_cancel_reorder_button = None
        self._remote_fetch_generation = 0
        self._remote_fetch_running_peer: str | None = None
        self._remote_fetch_error_by_peer: dict[str, str] = {}
        self._remote_push_running_peer: str | None = None
        self._remote_status_container = None
        self._remote_cache_status_container = None
        self._remote_cache_meta_label = None
        self._remote_pull_button = None
        self._remote_push_button = None
        self._remote_grid_view = None
        self._remote_grid_rows: list[dict[str, Any]] = []
        self._remote_filter_model: dict[str, Any] = {}
        self._remote_grid_sort_active = False
        self._remote_reorder_allowed = False
        self._action_bar = None

    def _page_client(self) -> Any:
        """当前页面浏览器会话。优先 render 时缓存的 client，否则从列表区容器解析。"""
        c = self._ng_client
        if c is not None:
            return c
        for cont in (self._remote_list_container, self.rules_container):
            if cont is not None:
                cl = getattr(cont, "client", None)
                if cl is not None:
                    return cl
        return None

    def _notify_for_page(
        self,
        message: Any,
        *,
        type: str | None = None,  # noqa: A001 与 Quasar / nicegui 一致
        position: str = "bottom",
        close_button: bool | str = False,
        color: str | None = None,
        multi_line: bool = False,
        **kwargs: Any,
    ) -> None:
        """在任意协程中向本页发通知；与 :func:`nicegui.ui.notify` 等效，不依赖当前任务的 slot 栈。"""
        c = self._page_client()
        if c is None:
            logger.error("firewall: 无 page client，无法弹通知: %s", message)
            return
        options = {
            _NICEGUI_NOTIFY_ARG_MAP.get(key, key): value
            for key, value in locals().items()
            if key not in ("self", "c", "message", "kwargs") and value is not None
        }
        options["message"] = str(message)
        options.update(kwargs)
        c.outbox.enqueue_message("notify", options, c.id)

    def _require_center_strategy_tab(self) -> bool:
        if not self._center_tab_active:
            self._notify_for_page("请先切到「中心策略」Tab。", type="warning")
            return False
        return True

    def _require_remote_strategy_tab(self) -> bool:
        if self._center_tab_active:
            self._notify_for_page("请先切到「对端远端」Tab。", type="warning")
            return False
        return True

    def render(self) -> None:
        """使用 V2 管理页结构渲染中心与对端策略入口。"""

        try:
            self._ng_client = context.client
        except RuntimeError:
            self._ng_client = None

        def action_icon(
            label: str,
            icon: str,
            callback,
            *,
            danger: bool = False,
        ):
            factory = danger_action if danger else secondary_action
            button = factory(
                label,
                icon=icon,
                on_click=callback,
                icon_only=True,
                tooltip=label,
            )
            button.props("round dense")
            classes = "firewall-bar-icon-action"
            if danger:
                classes += " firewall-bar-icon-action--danger"
            button.classes(add=classes)
            return button

        def bulk_actions() -> None:
            create_button = create_action(
                "新建规则",
                icon="add",
                on_click=self._on_new_rule_click,
                icon_only=True,
                tooltip="新建规则",
            )
            create_button.props("round dense")
            create_button.classes(
                add="firewall-bar-icon-action firewall-bar-icon-action--create"
            )
            action_icon(
                "导入",
                "upload_file",
                self._show_import_dialog,
            )
            action_icon(
                "导出",
                "download",
                self._export_backup,
            )
            ui.element("span").classes("firewall-action-separator")
            self._center_reorder_button = action_icon(
                "调整优先级",
                "swap_vert",
                self._enter_center_reorder_mode,
            )
            self._center_save_reorder_button = action_icon(
                "保存排序",
                "save",
                self._save_center_reorder,
            )
            self._center_cancel_reorder_button = action_icon(
                "取消排序",
                "close",
                self._cancel_center_reorder_mode,
            )
            self._center_save_reorder_button.set_visibility(False)
            self._center_cancel_reorder_button.set_visibility(False)
            ui.element("span").classes("firewall-action-separator")
            action_icon(
                "批量启用",
                "toggle_on",
                self._batch_enable,
            )
            action_icon(
                "批量停用",
                "toggle_off",
                self._batch_disable,
            )
            action_icon(
                "批量删除",
                "delete",
                self._batch_delete,
                danger=True,
            )

        with ui.column().classes(
            "page-shell mgmt-page firewall-page--v2 page-shell--firewall"
        ):
            with ui.element("div").classes("firewall-tab-shell"):
                with ui.tabs(
                    value="center",
                    on_change=self._on_strat_change,
                ).classes("firewall-compact-tabs w-full") as self._strat_tabs:
                    ui.tab("center", label="中心策略")
                    ui.tab("remote", label="对端远端")

            self._action_bar = render_bulk_action_bar(
                0,
                actions=bulk_actions,
            )
            self._action_bar.container.classes(add="firewall-bulk-bar")

            with ui.element("section").classes(
                "firewall-filter-bar"
            ) as self._center_search_controls:
                with ui.row().classes(
                    "firewall-filter-row w-full items-center gap-2 no-wrap"
                ):
                    self._center_search_input = (
                        ui.input(
                            placeholder="搜索 IP / 端口 / 规则 ID",
                            on_change=lambda _event: self._refresh_rules(),
                        )
                        .props("outlined dense clearable debounce=250")
                        .classes("firewall-search-input flex-1 min-w-0")
                    )

            with ui.tab_panels(
                self._strat_tabs,
                value="center",
                keep_alive=False,
            ).classes(
                "w-full h-full min-h-0 flex-1 "
                "firewall-tabpanels firewall-tabpanels--v2"
            ):
                with ui.tab_panel("center").classes(
                    "h-full min-h-0 flex-1 firewall-center-panel"
                ):
                    with ui.column().classes(
                        "firewall-center-canvas w-full min-h-0 flex-1 gap-0"
                    ):
                        with ui.element("section").classes(
                            "mgmt-panel mgmt-panel-list firewall-center-shell "
                            "w-full min-h-0 flex-1"
                        ):
                            self._center_grid_container = ui.column().classes(
                                "firewall-center-data-region "
                                "w-full min-h-0 flex-1"
                            )
                            self.rules_container = self._center_grid_container

                with ui.tab_panel("remote").classes(
                    "h-full min-h-0 flex-1 firewall-remote-panel"
                ):
                    self._remote_tab_shell = ui.column().classes(
                        "firewall-peer-remote firewall-remote-canvas "
                        "w-full min-h-0 flex-1 gap-0"
                    )
                    with self._remote_tab_shell:
                        self._build_remote_tab_static()

        self._refresh_rules()

    def _sync_action_bar_count(self) -> None:
        """让中心/远端共用操作栏始终反映当前页签选择数量。"""

        if self._action_bar is None:
            return
        count = (
            len(self.selected_rule_ids)
            if self._center_tab_active
            else len(self._selected_remote_indices)
        )
        self._action_bar.set_selected_count(count)

    def _on_strat_change(self, e) -> None:
        v = str(getattr(e, "value", None) or "center")
        if v != "center" and self._center_reorder_mode:
            # Leaving the center tab discards an unsaved preview, never silently saves it.
            self._center_reorder_mode = False
            self._pending_center_order = []
            self._center_reorder_original_order = []
            self._center_reorder_allowed = False
            if self._center_search_input is not None:
                self._center_search_input.enable()
        self._center_tab_active = v == "center"
        logger.debug("firewall 策略页签切换为 %s", v)
        if self._center_search_controls is not None:
            self._center_search_controls.set_visibility(self._center_tab_active)
        self._sync_center_reorder_controls()
        if self._center_tab_active:
            self._remote_fetch_generation += 1
            self._remote_fetch_running_peer = None
            self._selected_remote_indices.clear()
            self._sync_action_bar_count()
            self._refresh_rules()
        else:
            self.selected_rule_ids.clear()
            self._sync_action_bar_count()
            self._on_enter_remote_tab()

    def _on_new_rule_click(self) -> None:
        if self._center_tab_active:
            self._show_create_dialog()
        else:
            self._show_create_remote_line_dialog()

    def _peer_options_for_select(self) -> dict[str, str]:
        """对端下拉的 ``value->label``，与 `PeerService.list_all` 一致。"""
        opts: dict[str, str] = {}
        for p in self._peer_service.list_all():
            pid = str(p.get("id") or "").strip()
            if not pid:
                continue
            opts[pid] = f"{p.get('name', '')} ({pid[:8]}…)"
        return opts

    def _switch_owner(self, owner_id: str) -> None:
        """兼容旧跳转入口：把归属条件写入 Grid 的多选筛选模型。"""

        normalized = str(owner_id or "").strip()
        self.current_owner_id = normalized
        self._pending_center_owner_id = normalized
        if not normalized:
            self._center_filter_model.pop("owner_label", None)
        self._refresh_rules()

    async def _copy_text_to_clipboard(self, text: str) -> None:
        """将文本写入浏览器剪贴板并轻提示；供归属等可点击区域使用。"""
        t = (text or "").strip()
        if not t:
            return
        if await copy_text_to_clipboard(t):
            self._notify_for_page("已复制", type="positive")

    @staticmethod
    def _second_line_comment_text(
        data: dict,
        *,
        title_text: str,
        is_center_peer: bool = False,
        is_remote_row: bool = False,
        iptables_comment: str = "",
    ) -> str:
        return second_line_comment_text(
            data,
            title_text=title_text,
            is_center_peer=is_center_peer,
            is_remote_row=is_remote_row,
            iptables_comment=iptables_comment,
        )

    def _show_create_dialog(self) -> None:
        self._show_create_center_dialog()

    def _show_create_center_dialog(self) -> None:
        """使用共享规则字段创建中心规则。"""

        if not self._require_center_strategy_tab():
            return

        groups = GroupService().list_all()
        users = UserService().list_all()

        from app.core.config import load_config

        config = load_config()
        instances = list((config or {}).get("instances", {}).keys()) if config else []
        auto_instance = instances[0] if instances else "server"

        group_ids = {
            str(group.get("id") or "")
            for group in groups
            if str(group.get("id") or "").strip()
        }
        current = str(self.current_owner_id or "").strip()
        default_group_id = (
            current
            if current in group_ids
            else (
                str(groups[0].get("id") or "")
                if groups
                else None
            )
        )
        holder: dict[str, Any] = {}

        def load_peer_presets(group_id: str):
            if not group_id:
                return []
            return (
                self._peer_service
                .list_peer_lan_firewall_presets_for_center_form()
            )

        def body() -> None:
            ui.label("归属与来源").classes("section-caption")
            holder["fields"] = render_firewall_rule_create_fields(
                groups=groups,
                users=users,
                peer_presets_loader=load_peer_presets,
                ccd_reader=_read_ccd_vpn_ips,
                parse_user_source=_parse_user_source_for_create,
                default_group_id=default_group_id,
                owner_type_label="归属类型",
                peer_lan_label="对端内网 CIDR（任选；与上项二选一）",
                source_placeholder="组可自动填子网；用户可从 CCD 读取",
                user_hint_text=(
                    "多选用户时可预填 CCD 虚拟 IP，或留空从 CCD 读。"
                ),
                dest_ip_placeholder="子网或多 IP；空=不限",
                dest_port_placeholder="空=不限制",
            )

        def submit() -> bool:
            fields = holder["fields"]
            try:
                values = fields.read_values()
                priority = self._resolve_priority(
                    values["priority_mode"],
                    values["priority_value"],
                )
            except (ValueError, TypeError) as exc:
                self._notify_for_page(str(exc), type="negative")
                return False

            payload = {
                "owner_type": values["owner_type"],
                "owner_id": values["owner_id"],
                "instance": auto_instance,
                "deployment_target": "center",
                "action": values["action"],
                "protocol": values["protocol"],
                "source_subnet": values["source_subnet"],
                "source_ips": values["source_ips"],
                "dest_ip": values["dest_ip"],
                "dest_port": values["dest_port"],
                "priority": priority,
                "description": values["description"],
                "enabled": bool(values["enabled"]),
            }
            return self._create_center_rule(payload)

        form = create_form_dialog(
            "新建中心规则",
            body=body,
            on_submit=submit,
            submit_label="创建",
            close_on_success=True,
        )
        form.dialog.props("position=right maximized")
        form.card.classes(
            "firewall-rule-drawer w-[min(100vw,30rem)] h-full no-wrap overflow-y-auto"
        )
        form.open()

    def _create_center_rule(self, payload: dict[str, Any]) -> bool:
        """创建中心规则；失败时让统一表单保留输入。"""

        try:
            normalized = dict(payload)
            if str(normalized.get("protocol") or "").lower() in {
                "",
                "any",
                "all",
            }:
                normalized["protocol"] = "all"
            self.rule_service.create(normalized)
        except Exception as exc:
            logger.error("create center rule: %s", exc)
            self._notify_for_page(f"创建失败: {exc}", type="negative")
            return False

        self._notify_for_page("规则创建成功", type="positive")
        self._refresh_rules()
        return True

    def _resolve_priority(self, mode: str, input_value, *, base: list[dict] | None = None) -> int:
        if base is None:
            if self._is_unified_list:
                base = self.rule_service.list_unified_flat()
            else:
                oid = (self.current_owner_id or "").strip()
                base = self.rule_service.list_by_owner(oid) if oid else []
        existing = {r.get("priority", 0) for r in base} if base else set()
        if mode == "insert":
            if input_value is not None and input_value != "":
                return int(input_value)
            if not existing:
                return 10
            m = min(existing)
            cand = max(1, m - 10)
            while cand in existing:
                cand += 1
            return cand
        if not existing:
            return 10
        return max(r.get("priority", 0) for r in base) + 10

    def _show_edit_dialog(self, rule: dict) -> None:
        """使用统一表单编辑中心 JSON 规则或 center_peer 虚拟行。"""

        if not self._require_center_strategy_tab():
            return

        rule = dict(rule)
        rule_id = str(rule.get("id") or "").strip()
        is_center_peer = (
            rule_id.startswith(CE_PEER_RULE_PREFIX)
            or rule.get("_row_kind") == "center_peer"
        )
        source_subnet = str(rule.get("source_subnet") or "").strip()
        source_ips = [
            str(value).strip()
            for value in (rule.get("source_ips") or [])
            if str(value).strip()
        ]
        source_value = source_subnet or ",".join(source_ips)
        fields: dict[str, Any] = {}

        def body() -> None:
            if is_center_peer:
                ui.label(
                    "此行对应对端 center_forward_*；仅支持 ACCEPT。"
                    " 修改 LAN 会同步影响中心 VPN_FORWARD。"
                ).classes("section-caption")

            fields["action"] = ui.select(
                (
                    {"accept": "放行 (ACCEPT)"}
                    if is_center_peer
                    else {
                        "accept": "放行 (ACCEPT)",
                        "drop": "丢弃 (DROP)",
                        "reject": "拒绝 (REJECT)",
                    }
                ),
                label="动作",
                value=(
                    "accept"
                    if is_center_peer
                    else str(rule.get("action") or "accept").lower()
                ),
            ).classes("w-full")

            fields["protocol"] = ui.select(
                {
                    "all": "不限 (ALL)",
                    "tcp": "TCP",
                    "udp": "UDP",
                    "icmp": "ICMP",
                },
                label="协议",
                value=str(rule.get("protocol") or "all").lower().replace("any", "all"),
                with_input=True,
                new_value_mode="add-unique",
            ).classes("w-full")

            fields["source"] = ui.input(
                "源子网 CIDR 或源 IP 列表",
                value=source_value,
            ).classes("w-full")
            fields["dest_ip"] = ui.input(
                "目标地址",
                value=str(rule.get("dest_ip") or ""),
            ).classes("w-full")
            fields["dest_port"] = ui.input(
                "目标端口",
                value=str(rule.get("dest_port") or ""),
            ).classes("w-full")
            fields["description"] = ui.input(
                "规则描述",
                value=str(rule.get("description") or ""),
            ).classes("w-full")
            fields["enabled"] = ui.switch(
                "启用",
                value=bool(rule.get("enabled", True)),
            )

            if not is_center_peer:
                ui.label(
                    f"归属：{rule.get('owner_type', 'group')} / "
                    f"{rule.get('_owner_id') or rule.get('owner_id') or '—'}"
                ).classes("section-caption")
                fields["priority"] = ui.number(
                    "优先级（全局唯一）",
                    value=int(rule.get("priority") or 10),
                    min=1,
                    step=1,
                ).props("outlined dense").classes("w-full")
                ui.label(
                    "修改单条规则可直接输入优先级；整体调整建议使用列表的拖动排序模式。"
                ).classes("section-caption")

        def submit() -> bool:
            return self._save_center_rule_edit(
                rule,
                action=str(fields["action"].value or "accept"),
                protocol=str(fields["protocol"].value or "all"),
                source_input=str(fields["source"].value or ""),
                dest_ip=str(fields["dest_ip"].value or "").strip() or None,
                dest_port=str(fields["dest_port"].value or "").strip() or None,
                description=str(fields["description"].value or "").strip() or None,
                enabled=bool(fields["enabled"].value),
                priority=(
                    int(fields["priority"].value or 0)
                    if "priority" in fields
                    else None
                ),
            )

        form = create_form_dialog(
            "编辑对端·中心放行" if is_center_peer else "编辑中心规则",
            body=body,
            on_submit=submit,
            submit_label="保存",
            close_on_success=True,
        )
        form.dialog.props("position=right maximized")
        form.card.classes(
            "firewall-rule-drawer w-[min(100vw,30rem)] h-full no-wrap overflow-y-auto"
        )
        form.open()

    def _save_center_rule_edit(
        self,
        original: dict,
        *,
        action: str,
        protocol: str,
        source_input: str,
        dest_ip,
        dest_port,
        description,
        enabled: bool,
        priority: int | None = None,
    ) -> bool:
        """保存中心规则；失败返回 False，让统一表单保留输入。"""

        source_text = str(source_input or "").strip()
        rule_id = str(original.get("id") or "").strip()
        is_center_peer = (
            rule_id.startswith(CE_PEER_RULE_PREFIX)
            or original.get("_row_kind") == "center_peer"
        )

        normalized_protocol = str(protocol or "all").strip().lower()
        if normalized_protocol in {"", "any"}:
            normalized_protocol = "all"

        if is_center_peer:
            peer_id = str(original.get("_peer_id") or "").strip()
            if not peer_id and rule_id.startswith(CE_PEER_RULE_PREFIX):
                peer_id = rule_id[len(CE_PEER_RULE_PREFIX):].strip()
            if not peer_id:
                self._notify_for_page("对端 id 无效", type="negative")
                return False
            if not self._peer_service.get(peer_id):
                self._notify_for_page("对端不存在", type="negative")
                return False
            if str(action or "").lower() != "accept":
                self._notify_for_page(
                    "对端·中心放行仅支持 ACCEPT",
                    type="negative",
                )
                return False

            lan_cidrs = _parse_lan_cidrs_text(source_text)
            for cidr in lan_cidrs:
                if not validate_cidr(cidr):
                    self._notify_for_page(
                        f"无效 CIDR: {cidr}",
                        type="negative",
                    )
                    return False

            port_text = str(dest_port or "").strip()
            try:
                FirewallRule(
                    owner_type="group",
                    owner_id="_",
                    action="accept",
                    priority=1,
                    protocol=normalized_protocol,
                    dest_ip=str(dest_ip or "").strip() or None,
                    dest_port=port_text or None,
                )
            except (ValidationError, ValueError) as exc:
                logger.error("对端·中心放行字段校验失败: %s", exc)
                self._notify_for_page(str(exc), type="negative")
                return False

            try:
                self._peer_service.update(
                    peer_id,
                    {
                        "lan_cidrs": lan_cidrs,
                        "center_forward_enabled": bool(enabled),
                        "center_forward_dest_ip": str(dest_ip or "").strip(),
                        "center_forward_dest_port": port_text,
                        "center_forward_protocol": normalized_protocol,
                        "center_forward_rule_description": str(
                            description or ""
                        ).strip(),
                    },
                )
            except Exception as exc:
                logger.error(
                    "对端·中心放行保存失败 peer=%s: %s",
                    peer_id,
                    exc,
                )
                self._notify_for_page(
                    f"更新失败: {exc}",
                    type="negative",
                )
                return False

            self._notify_for_page("已更新", type="positive")
            if os.name == "nt":
                self._notify_for_page(
                    "当前为 Windows，本机未落 iptables；请在 Linux 网关核对。",
                    type="warning",
                )
            self._refresh_rules()
            return True

        owner_type = str(original.get("owner_type") or "group")
        source_subnet = None
        source_ips = None
        if owner_type == "user" and source_text:
            try:
                source_subnet, source_ips = _parse_user_source_for_create(
                    source_text
                )
            except ValueError as exc:
                self._notify_for_page(str(exc), type="negative")
                return False
        else:
            source_subnet = source_text or None

        new_rule_data = {
            **original,
            "action": str(action or "accept").lower(),
            "protocol": normalized_protocol,
            "source_subnet": source_subnet,
            "source_ips": source_ips,
            "dest_ip": dest_ip,
            "dest_port": dest_port,
            "description": description,
            "enabled": bool(enabled),
            "priority": int(
                priority if priority is not None else original.get("priority") or 0
            ),
            "updated_at": datetime.now().isoformat(),
        }
        for key in ("_owner_id", "_row_kind", "_peer_id", "_center_tag_line"):
            new_rule_data.pop(key, None)

        try:
            self.rule_service.update_by_id(rule_id, new_rule_data)
        except Exception as exc:
            logger.error("编辑中心规则失败 rule=%s: %s", rule_id, exc)
            self._notify_for_page(
                f"更新失败: {exc}",
                type="negative",
            )
            return False

        self._notify_for_page("已更新", type="positive")
        self._refresh_rules()
        return True

    def _do_edit(
        self,
        dialog,
        original: dict,
        *,
        action: str,
        protocol: str,
        source_input: str,
        dest_ip,
        dest_port,
        description,
        enabled: bool,
    ) -> bool:
        """兼容旧测试/调用入口。"""

        success = self._save_center_rule_edit(
            original,
            action=action,
            protocol=protocol,
            source_input=source_input,
            dest_ip=dest_ip,
            dest_port=dest_port,
            description=description,
            enabled=enabled,
        )
        if success and dialog is not None:
            dialog.close()
        return success

    def _confirm_delete(self, rule_id: str) -> None:
        raw = self._center_raw_by_id.get(str(rule_id), {})
        is_center_peer = (
            str(rule_id).startswith(CE_PEER_RULE_PREFIX)
            or raw.get("_row_kind") == "center_peer"
        )
        impact = (
            "会清空该对端的 LAN CIDR，并移除中心 VPN_FORWARD 对端放行。"
            if is_center_peer
            else "会删除该规则并立即刷新中心 VPN_FORWARD。"
        )
        confirm_dialog.show(
            f"确定删除规则 {str(rule_id)[:12]}？",
            impact=impact,
            object_count=1,
            danger_level="danger",
            on_confirm=lambda: self._do_delete(rule_id),
        )

    def _do_delete(self, rule_id: str) -> None:
        try:
            self.rule_service.delete(rule_id)
            self._notify_for_page("已删除", type="positive")
            self._refresh_rules()
        except Exception as exc:
            logger.error("delete: %s", exc)
            self._notify_for_page(str(exc), type="negative")

    def _export_backup(self) -> None:
        if self._center_tab_active:
            try:
                s = self.rule_service.backup()
                ui.download(
                    s.encode("utf-8"),
                    _firewall_export_json_filename("firewall-rules-backup"),
                )
                self._notify_for_page(
                    "已导出（中心 JSON + 对端中心侧策略）",
                    type="positive",
                )
            except Exception as exc:
                self._notify_for_page(str(exc), type="negative")
            return
        if not self._require_remote_strategy_tab():
            return
        pid = self._active_remote_peer_id()
        if not pid:
            self._notify_for_page("请先选择对端", type="warning")
            return
        cache = remote_chain_cache.read_remote_chain_cache(pid)
        cname = IptablesManager.peer_chain_name_for_id(pid)
        if cache is None:
            payload: dict = {
                "version": 1,
                "peer_id": pid,
                "chain": cname,
                "chain_exists": True,
                "rows": [],
            }
        else:
            payload = {k: cache[k] for k in cache if k in cache}
        try:
            s = json.dumps(payload, ensure_ascii=False, indent=2)
        except (TypeError, ValueError) as exc:
            self._notify_for_page(f"序列化失败: {exc}", type="negative")
            return
        short = pid[:8] if len(pid) > 8 else pid
        ui.download(
            s.encode("utf-8"),
            _firewall_export_json_filename(f"firewall-remote-peer-{short}"),
        )
        self._notify_for_page("已导出对端副本", type="positive")

    def _on_import_textarea_value_change(self, ta) -> None:
        """与上传内容不一致时，不再按「.json 整库 / .txt 简写」走文件分支。"""
        cur = (ta.value or "").strip()
        ref = (self._import_last_uploaded_text or "").strip()
        if ref and cur == ref:
            return
        self._import_source_file_mode = None
        self._import_last_uploaded_text = None

    def _prepare_center_import(
        self,
        text: str,
        *,
        file_mode: str | None = None,
    ) -> dict[str, Any]:
        """识别并预检查中心导入内容，不产生任何写入。"""

        normalized = str(text or "").strip().lstrip("\ufeff")
        if not normalized:
            raise ValueError("无内容")

        mode = str(file_mode or "").strip() or None
        backup_candidate = mode == "json_overwrite" or (
            mode is None and normalized.startswith("{")
        )

        if backup_candidate:
            if not is_center_backup_json_text(normalized):
                raise ValueError("须为导出 backup（含 rules_by_owner）。")
            try:
                payload = json.loads(normalized)
            except json.JSONDecodeError as exc:
                raise ValueError(f"backup JSON 解析失败: {exc}") from exc
            rules_by_owner = payload.get("rules_by_owner") or {}
            if not isinstance(rules_by_owner, dict):
                raise ValueError("backup 中 rules_by_owner 必须为对象")
            rule_count = sum(
                len(items)
                for items in rules_by_owner.values()
                if isinstance(items, list)
            )
            peer_items = payload.get("peers_center_forward") or []
            peer_count = len(peer_items) if isinstance(peer_items, list) else 0
            return {
                "kind": "backup",
                "text": normalized,
                "count": rule_count + peer_count,
                "rule_count": rule_count,
                "peer_count": peer_count,
            }

        if mode == "json_overwrite":
            raise ValueError("须为导出 backup（含 rules_by_owner）。")

        owner_id = str(self.current_owner_id or "").strip()
        if self._is_unified_list or not owner_id:
            raise ValueError("简写导入请先在归属筛选中选择一个归属。")

        try:
            specs = parse_center_simplified_lines(normalized)
        except ValueError as exc:
            raise ValueError(f"简写解析失败: {exc}") from exc
        if not specs:
            raise ValueError("没有可导入的简写规则")

        payloads = self._build_center_simplified_payloads(specs)
        return {
            "kind": "simplified",
            "text": normalized,
            "count": len(payloads),
            "owner_id": owner_id,
            "payloads": payloads,
        }

    def _build_center_simplified_payloads(self, specs) -> list[dict[str, Any]]:
        """把已解析简写转换为中心规则 payload，失败时给出行号。"""

        from app.core.config import load_config

        owner_id = str(self.current_owner_id or "").strip()
        if not owner_id:
            raise ValueError("简写导入缺少归属")

        try:
            owner_type = resolve_center_owner_type(owner_id)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

        config = load_config()
        instance_keys = list((config or {}).get("instances", {}).keys()) if config else []
        instance = instance_keys[0] if instance_keys else "server"

        payloads: list[dict[str, Any]] = []
        for index, spec in enumerate(specs, start=1):
            try:
                source_subnet, source_ips = source_fields_for_center(
                    spec,
                    owner_type,
                )
            except ValueError as exc:
                raise ValueError(f"第 {index} 行: {exc}") from exc

            if owner_type == "user" and not source_subnet and not source_ips:
                source_ips = _read_ccd_vpn_ips([owner_id])
                if not source_ips:
                    raise ValueError(
                        f"第 {index} 行缺 -s 且 CCD 无虚拟 IP。"
                    )

            payload = center_rule_payload_from_simplified(
                spec,
                owner_type=owner_type,
                owner_id=owner_id,
                instance=instance,
                source_subnet=source_subnet,
                source_ips=source_ips,
            )
            if str(payload.get("protocol") or "").lower() in {"", "any", "all"}:
                payload["protocol"] = "all"
            payloads.append(payload)
        return payloads

    async def _execute_center_import(
        self,
        form,
        status_label,
        prepared: dict[str, Any],
    ) -> bool:
        """异步执行已预检查导入；失败保留表单与原始输入。"""

        if self._center_import_running:
            self._notify_for_page("中心规则正在导入，请稍候", type="warning")
            return False

        self._center_import_running = True
        form.set_busy(True)
        status_label.text = "导入中…正在刷新 VPN_FORWARD"
        kind = str(prepared.get("kind") or "")
        total = int(prepared.get("count") or 0)

        try:
            if kind == "backup":
                ok = await run.io_bound(
                    self.rule_service.restore,
                    str(prepared.get("text") or ""),
                )
                if not ok:
                    raise RuntimeError("backup 恢复失败")
                success = total
            elif kind == "simplified":
                payloads = [
                    dict(item)
                    for item in (prepared.get("payloads") or [])
                ]
                result = await run.io_bound(
                    self._run_center_simplified_import,
                    payloads,
                )
                if not result.get("ok"):
                    raise RuntimeError("简写导入失败")
                success = int(result.get("count") or 0)
            else:
                raise RuntimeError("未知导入类型")
        except Exception as exc:
            logger.exception("中心规则导入失败 kind=%s", kind)
            status_label.text = f"导入失败：{exc}"
            self._notify_for_page(
                f"导入失败：成功 0，跳过 0，失败 {max(total, 1)}；{exc}",
                type="negative",
                multi_line=True,
            )
            return False
        finally:
            self._center_import_running = False
            form.set_busy(False)

        status_label.text = (
            f"导入完成：成功 {success}，跳过 0，失败 0"
        )
        self._notify_for_page(
            f"导入完成：成功 {success}，跳过 0，失败 0",
            type="positive",
        )
        form.close()
        self._refresh_rules()
        return True

    def _show_center_import_dialog(self) -> None:
        """统一中心规则导入表单：上传/粘贴、自动识别、预检查和异步执行。"""

        state: dict[str, Any] = {
            "file_mode": None,
            "uploaded_text": None,
        }
        refs: dict[str, Any] = {}
        holder: dict[str, Any] = {}

        def body() -> None:
            ui.label(
                ".json = 整库 backup；.txt / 粘贴文本 = 简写追加到当前归属。"
                " 纯粘贴以 { 开头时自动识别为 backup。"
            ).classes("section-caption")
            refs["textarea"] = (
                ui.textarea("规则内容")
                .classes("w-full")
                .props("rows=16")
            )
            refs["status"] = ui.label("尚未执行预检查").classes(
                "section-caption"
            )

            def on_text_change() -> None:
                current = str(refs["textarea"].value or "").strip()
                uploaded = str(state.get("uploaded_text") or "").strip()
                if uploaded and current == uploaded:
                    return
                state["file_mode"] = None
                state["uploaded_text"] = None
                refs["status"].text = "内容已修改，将按粘贴内容自动识别格式"

            refs["textarea"].on_value_change(lambda _e: on_text_change())

            async def on_upload(event) -> None:
                try:
                    raw = await event.file.read()
                    text = raw.decode("utf-8", errors="replace")
                    name = str(getattr(event.file, "name", "") or "").strip()
                    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                    if ext == "json":
                        mode = "json_overwrite"
                    elif ext in {"txt", "text"}:
                        mode = "simplified_text"
                    else:
                        mode = None
                    state["file_mode"] = mode
                    state["uploaded_text"] = str(text or "").strip()
                    refs["textarea"].set_value(text)
                    refs["status"].text = (
                        f"已载入 {name}；"
                        + (
                            "识别为 backup 覆盖"
                            if mode == "json_overwrite"
                            else (
                                "识别为简写追加"
                                if mode == "simplified_text"
                                else "将自动识别格式"
                            )
                        )
                    )
                except Exception as exc:
                    filename = str(
                        getattr(event.file, "name", "") or ""
                    ).strip()
                    logger.exception("中心防火墙导入读文件失败: %s", filename)
                    refs["status"].text = f"读取失败：{exc}"
                    self._notify_for_page(
                        f"读文件失败 ({filename}): {exc}",
                        type="negative",
                    )
                    raise RuntimeError(f"读文件失败: {exc}") from exc

            ui.upload(
                label="从文件加载（.json / .txt）",
                on_upload=on_upload,
                auto_upload=True,
                max_files=1,
            ).props(
                'accept=".json,.txt,.text,application/json,text/plain"'
            ).classes("w-full")

        def submit() -> bool:
            try:
                prepared = self._prepare_center_import(
                    str(refs["textarea"].value or ""),
                    file_mode=state.get("file_mode"),
                )
            except ValueError as exc:
                refs["status"].text = f"预检查失败：{exc}"
                self._notify_for_page(str(exc), type="negative")
                return False

            count = int(prepared.get("count") or 0)
            if prepared["kind"] == "backup":
                refs["status"].text = (
                    f"预检查通过：backup 共 {count} 项，等待覆盖确认"
                )
                confirm_dialog.show(
                    "用 backup 覆盖全部中心规则？",
                    impact=(
                        f"将恢复 {prepared.get('rule_count', 0)} 条 JSON 规则和 "
                        f"{prepared.get('peer_count', 0)} 条对端中心策略；"
                        "服务层会在写入前生成回滚备份。"
                    ),
                    object_count=count,
                    danger_level="danger",
                    on_confirm=lambda: self._execute_center_import(
                        holder["form"],
                        refs["status"],
                        prepared,
                    ),
                )
            else:
                refs["status"].text = (
                    f"预检查通过：将向 {prepared['owner_id']} 追加 {count} 条"
                )
                confirm_dialog.show(
                    f"向当前归属追加 {count} 条规则？",
                    impact="将分配新的全局优先级并刷新中心 VPN_FORWARD。",
                    object_count=count,
                    danger_level="warning",
                    on_confirm=lambda: self._execute_center_import(
                        holder["form"],
                        refs["status"],
                        prepared,
                    ),
                )
            return False

        form = create_form_dialog(
            "导入中心规则",
            body=body,
            on_submit=submit,
            submit_label="预检查并导入",
            close_on_success=False,
        )
        holder["form"] = form
        form.card.classes("w-full max-w-3xl")
        form.open()

    def _show_import_dialog(self) -> None:
        self._import_source_file_mode = None
        self._import_last_uploaded_text = None
        if self._center_tab_active:
            self._show_center_import_dialog()
        else:
            self._show_remote_import_dialog()

    def _do_import(self, dialog, text: str) -> None:
        if not (text or "").strip():
            self._notify_for_page("无内容", type="negative")
            return
        t_raw = (text or "").strip()
        t = t_raw.lstrip("\ufeff")
        fmode = self._import_source_file_mode
        if self._center_tab_active:
            # 上传 .json：整库覆盖；上传 .txt：仅简写追加；与纯粘贴的「{ 开头 / 简写」并存
            if fmode == "json_overwrite":
                if not is_center_backup_json_text(t):
                    self._notify_for_page(
                        "须为导出 backup（含 rules_by_owner）。",
                        type="negative",
                    )
                    return
                confirm_dialog.show(
                    "用 backup 覆盖全部中心规则。确定？",
                    on_confirm=lambda: self._import_ok_center_backup(dialog, t),
                )
                return
            if fmode == "simplified_text":
                if self._is_unified_list or not (self.current_owner_id or "").strip():
                    self._notify_for_page("简写导入请先关键词选中归属。", type="negative")
                    return
                try:
                    specs = parse_center_simplified_lines(t)
                except ValueError as exc:
                    logger.error("中心 .txt 简写解析失败: %s", exc)
                    self._notify_for_page(str(exc), type="negative")
                    return
                n = len(specs)
                confirm_dialog.show(
                    f"向当前归属追加 {n} 条简写。确定？",
                    on_confirm=lambda: self._import_ok_center_simplified(dialog, specs),
                )
                return
            if t.startswith("{"):
                if is_center_backup_json_text(t):
                    confirm_dialog.show(
                        "用 backup 覆盖全部中心规则。确定？",
                        on_confirm=lambda: self._import_ok_center_backup(dialog, t),
                    )
                else:
                    self._notify_for_page(
                        "`{` 开头须为导出 backup（含 rules_by_owner）。",
                        type="negative",
                    )
                return
            if self._is_unified_list or not (self.current_owner_id or "").strip():
                self._notify_for_page("简写导入请先关键词选中归属。", type="negative")
                return
            try:
                specs = parse_center_simplified_lines(t)
            except ValueError as exc:
                logger.error("中心简写解析失败: %s", exc)
                self._notify_for_page(
                    f"{exc} 整库恢复请贴 `{{` 开头的 backup 或上传 .json。",
                    type="negative",
                )
                return
            n = len(specs)
            confirm_dialog.show(
                f"向当前归属追加 {n} 条简写。确定？",
                on_confirm=lambda: self._import_ok_center_simplified(dialog, specs),
            )
            return
        else:
            if not self._require_remote_strategy_tab():
                return
            confirm_dialog.show(
                "覆盖对端本地副本并写回。确定？",
                on_confirm=lambda: self._schedule_remote_coro(self._import_ok_remote_async(dialog, t)),
            )

    def _import_ok_center_backup(self, dialog, text: str) -> None:
        self._import_source_file_mode = None
        self._import_last_uploaded_text = None
        if self._center_import_running:
            self._notify_for_page("中心规则正在导入，请稍候", type="warning")
            return
        dialog.close()
        self._start_center_import_worker(
            "backup 恢复",
            lambda: {"ok": bool(self.rule_service.restore(text)), "count": 0},
            "backup 已恢复",
        )

    def _import_ok_center_simplified(self, dialog, specs) -> None:
        """简写多行：逐条 create，优先级取全局未占用值。"""
        from app.core.config import load_config

        self._import_source_file_mode = None
        self._import_last_uploaded_text = None
        if self._center_import_running:
            self._notify_for_page("中心规则正在导入，请稍候", type="warning")
            return
        oid = (self.current_owner_id or "").strip()
        payloads: list[dict] = []
        try:
            owner_type = resolve_center_owner_type(oid)
        except ValueError as exc:
            logger.error("中心简写导入归属失败: %s", exc)
            self._notify_for_page(str(exc), type="negative")
            return
        config = load_config()
        inst_keys = list((config or {}).get("instances", {}).keys()) if config else []
        instance = inst_keys[0] if inst_keys else "server"
        for i, spec in enumerate(specs):
            try:
                src_sub, src_ips = source_fields_for_center(spec, owner_type)
            except ValueError as exc:
                logger.error("中心简写第 %s 行源字段失败: %s", i + 1, exc)
                self._notify_for_page(f"第 {i + 1} 行: {exc}", type="negative")
                return
            if owner_type == "user" and not src_sub and not src_ips:
                src_ips = _read_ccd_vpn_ips([oid])
                if not src_ips:
                    self._notify_for_page(
                        f"第 {i + 1} 行缺 -s 且 CCD 无虚拟 IP。",
                        type="negative",
                    )
                    return
            payload = center_rule_payload_from_simplified(
                spec,
                owner_type=owner_type,
                owner_id=oid,
                instance=instance,
                source_subnet=src_sub,
                source_ips=src_ips,
            )
            if str(payload.get("protocol") or "").lower() in ("any", "all", ""):
                payload["protocol"] = "all"
            payloads.append(payload)
        dialog.close()
        self._start_center_import_worker(
            "中心简写导入",
            lambda: self._run_center_simplified_import(payloads),
            f"简写已追加 {len(payloads)} 条",
        )

    def _start_center_import_worker(self, label: str, worker_fn, success_message: str) -> None:
        """后台执行中心规则导入，避免 iptables 刷新阻塞页面。"""
        self._center_import_running = True
        self._center_import_result = None
        self._notify_for_page(f"{label}已开始，请稍候", type="info")
        logger.info("%s后台任务已启动", label)

        def worker() -> None:
            try:
                result = worker_fn()
                if not result.get("ok"):
                    self._center_import_result = {
                        "ok": False,
                        "message": f"{label}失败",
                    }
                    return
                self._center_import_result = {
                    "ok": True,
                    "message": success_message,
                    "count": result.get("count", 0),
                }
            except Exception as exc:
                logger.exception("%s后台任务失败", label)
                self._center_import_result = {
                    "ok": False,
                    "message": f"{label}失败: {exc}",
                }

        threading.Thread(target=worker, daemon=True, name="firewall-center-import").start()
        self._center_import_timer = ui.timer(0.3, self._poll_center_import_progress)

    def _run_center_simplified_import(self, payloads: list[dict]) -> dict[str, Any]:
        """为简写导入分配优先级后批量写入，只刷新一次 VPN_FORWARD。"""
        all_r = self.rule_service.list_all_flat()
        used = {int(r.get("priority", 0) or 0) for r in all_r}
        mx = max(used) if used else 0
        for payload in payloads:
            cand = mx + 10
            while cand in used:
                cand += 1
            payload["priority"] = int(cand)
            used.add(cand)
            mx = cand
        created = self.rule_service.create_many(payloads)
        return {"ok": True, "count": len(created)}

    def _poll_center_import_progress(self) -> None:
        """在 UI 线程收尾中心导入后台任务。"""
        if self._center_import_result is None:
            return
        if self._center_import_timer is not None:
            if hasattr(self._center_import_timer, "deactivate"):
                self._center_import_timer.deactivate()
            elif hasattr(self._center_import_timer, "active"):
                self._center_import_timer.active = False
            self._center_import_timer = None

        result = self._center_import_result
        self._center_import_result = None
        self._center_import_running = False
        if result.get("ok"):
            self._notify_for_page(str(result.get("message") or "导入完成"), type="positive")
            self._refresh_rules()
        else:
            self._notify_for_page(str(result.get("message") or "导入失败"), type="negative")

    def _batch_delete(self) -> None:
        if self._center_tab_active:
            if not self._require_center_strategy_tab():
                return
            if not self.selected_rule_ids:
                self._notify_for_page("未勾选", type="warning")
                return
            count = len(self.selected_rule_ids)
            confirm_dialog.show(
                f"批量删除所选 {count} 条中心规则？",
                impact="删除后会逐条刷新中心策略；若其中某条失败，其余成功项不会自动回滚。",
                object_count=count,
                danger_level="danger",
                on_confirm=self._do_batch_delete_center,
            )
            return
        if not self._require_remote_strategy_tab():
            return
        pid = self._active_remote_peer_id()
        if not pid:
            self._notify_for_page("请先选择对端", type="warning")
            return
        if not self._selected_remote_indices:
            self._notify_for_page("未勾选", type="warning")
            return
        count = len(self._selected_remote_indices)
        confirm_dialog.show(
            f"删除所选 {count} 条对端规则？",
            impact=(
                "会一次性重写当前对端链；失败时整批按 prepush 备份恢复。"
            ),
            object_count=count,
            danger_level="danger",
            on_confirm=lambda: self._schedule_remote_coro(
                self._do_batch_remote_delete_async()
            ),
        )

    def _do_batch_delete_center(self) -> None:
        success = 0
        failed = 0
        for rid in list(self.selected_rule_ids):
            try:
                self.rule_service.delete(rid)
                success += 1
            except Exception as exc:
                failed += 1
                logger.error("批量删除中心规则失败 rule=%s: %s", rid, exc)
        self.selected_rule_ids.clear()
        if failed:
            self._notify_for_page(
                f"批量删除完成：成功 {success}，失败 {failed}",
                type="warning" if success else "negative",
            )
        else:
            self._notify_for_page(f"已删除 {success} 条规则", type="positive")
        self._refresh_rules()

    def _batch_enable(self) -> None:
        self._batch_set_enabled(True)

    def _batch_disable(self) -> None:
        self._batch_set_enabled(False)

    def _batch_set_enabled(self, en: bool) -> None:
        if self._center_tab_active:
            if not self._require_center_strategy_tab():
                return
            if not self.selected_rule_ids:
                self._notify_for_page("未勾选", type="warning")
                return
            success = 0
            skipped = 0
            failed = 0
            for rid in list(self.selected_rule_ids):
                raw = self._center_raw_by_id.get(str(rid), {})
                if raw and bool(raw.get("enabled", True)) == bool(en):
                    skipped += 1
                    continue
                try:
                    self.rule_service.set_enabled(rid, en)
                    success += 1
                except Exception as exc:
                    failed += 1
                    logger.error(
                        "批量切换中心规则状态失败 rule=%s enabled=%s: %s",
                        rid,
                        en,
                        exc,
                    )
            self.selected_rule_ids.clear()
            label = "启用" if en else "停用"
            message = (
                f"批量{label}完成：成功 {success}，跳过 {skipped}，失败 {failed}"
            )
            self._notify_for_page(
                message,
                type="warning" if failed else "positive",
            )
            self._refresh_rules()
            return
        if not self._require_remote_strategy_tab():
            return
        pid = self._active_remote_peer_id()
        if not pid:
            self._notify_for_page("请先选择对端", type="warning")
            return
        if not self._selected_remote_indices:
            self._notify_for_page("未勾选", type="warning")
            return
        self._schedule_remote_coro(self._do_batch_remote_set_enabled_async(en))
