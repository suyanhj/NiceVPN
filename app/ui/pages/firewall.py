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
from app.ui.pages.firewall_imports import FirewallImportMixin
from app.ui.pages.firewall_batch import FirewallBatchMixin
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


class FirewallPage(FirewallCenterGridMixin, FirewallRemoteMixin, FirewallImportMixin, FirewallBatchMixin):
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
