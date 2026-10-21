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

from nicegui import background_tasks, context, run, ui
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
    peer_rests_from_simplified_line,
    remote_rests_from_create_fields,
    resolve_center_owner_type,
    source_fields_for_center,
    try_parse_simplified_line,
)
from app.services.group.crud import GroupService
from app.services.peer_instance import remote_chain_cache
from app.services.peer_instance.peer_ssh_connect import peer_row_has_usable_ssh_auth
from app.services.peer_instance.service import PeerService
from app.services.user.crud import UserService
from app.ui.adapters.firewall import (
    adapt_center_rule_rows,
    adapt_remote_cache_status,
    adapt_remote_peer_capability,
    adapt_remote_rule_rows,
    format_remote_cache_timestamp,
    parse_remote_rule_hints,
)
from app.ui.components import confirm_dialog
from app.ui.components.actions import (
    MenuAction,
    danger_action,
    more_actions,
    primary_action,
    secondary_action,
)
from app.ui.components.data_grid import render_data_grid
from app.ui.components.form_dialog import create_form_dialog
from app.ui.components.firewall_rule_form import render_firewall_rule_create_fields
from app.ui.components.states import empty_state, error_state, loading_state, no_match_state
from app.ui.components.status import status_badge
from app.ui.components.toolbars import render_page_toolbar
from app.ui.components.list_selection import (
    apply_row_checkboxes,
    sync_select_all_checkbox,
    update_visible_selection,
)
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


class FirewallPage:
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
        self._center_select_all_checkbox = None
        self._remote_select_all_checkbox = None
        self._visible_center_rule_ids: list[str] = []
        self._visible_remote_indices: list[int] = []
        self._center_rule_checkboxes: dict[str, Any] = {}
        self._remote_row_checkboxes: dict[int, Any] = {}
        # 与 ``list_unified_flat`` 的全局排序一致：留空 = 全量
        self._is_unified_list: bool = True
        self._center_tab_active: bool = True
        self._strat_tabs: ui.tabs | None = None
        self._rules_total_footer: ui.label | None = None
        self._remote_peer_select: ui.select | None = None
        # 对端机链可滚动列表区，与中心 ``rules_container`` 同构
        self._remote_list_container: ui.column | None = None
        self._remote_rules_total_footer: ui.label | None = None
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
        self._center_owner_filter = None
        self._center_all_rows: list[dict[str, Any]] = []
        self._center_display_rows: list[dict[str, Any]] = []
        self._center_raw_by_id: dict[str, dict[str, Any]] = {}
        self._center_reorder_allowed = False
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
        self._remote_reorder_allowed = False

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
        """使用共享后台外壳渲染中心与对端策略入口。"""

        try:
            self._ng_client = context.client
        except RuntimeError:
            self._ng_client = None

        def search_region() -> None:
            with ui.row().classes("items-center gap-2 flex-wrap") as self._center_search_controls:
                self._center_search_input = (
                    ui.input(
                        placeholder="搜索归属 / IP / 协议 / 动作 / 规则 ID",
                        on_change=lambda _event: self._refresh_rules(),
                    )
                    .props("outlined dense clearable debounce=250")
                    .classes("min-w-[18rem] flex-1")
                )
                self._center_owner_filter = (
                    ui.select(
                        {"": "全部归属"},
                        value="",
                        label="归属",
                        on_change=lambda _event: self._refresh_rules(),
                    )
                    .props('outlined dense clearable behavior=menu popup-content-class="vpn-owner-select-menu"')
                    .classes("min-w-[12rem]")
                )

        def primary_region() -> None:
            primary_action(
                "新建规则",
                icon="add",
                on_click=self._on_new_rule_click,
            )

        def secondary_region() -> None:
            secondary_action("批量启用", on_click=self._batch_enable)
            secondary_action("批量停用", on_click=self._batch_disable)
            danger_action("批量删除", on_click=self._batch_delete)
            more_actions(
                (
                    MenuAction("导入", icon="upload_file", on_click=self._show_import_dialog),
                    MenuAction("导出", icon="download", on_click=self._export_backup),
                ),
                label="更多",
            )

        with ui.column().classes("page-shell mgmt-page w-full h-full min-h-0 page-shell--firewall"):
            with ui.tabs(
                value="center",
                on_change=self._on_strat_change,
            ).classes("firewall-compact-tabs w-full") as self._strat_tabs:
                ui.tab("center", label="中心策略")
                ui.tab("remote", label="对端远端")

            render_page_toolbar(
                search=search_region,
                primary=primary_region,
                secondary=secondary_region,
            )

            with ui.tab_panels(
                self._strat_tabs,
                value="center",
                keep_alive=False,
            ).classes("w-full h-full min-h-0 flex-1 firewall-tabpanels"):
                with ui.tab_panel("center").classes("h-full min-h-0 flex-1"):
                    with ui.column().classes(
                        "firewall-center-canvas w-full min-h-0 flex-1 gap-0"
                    ):
                        with ui.element("section").classes(
                            "mgmt-panel mgmt-panel-list firewall-center-shell w-full min-h-0 flex-1"
                        ):
                            self._center_grid_container = ui.column().classes(
                                "w-full min-h-0 flex-1"
                            )
                            self.rules_container = self._center_grid_container

                        with ui.element("div").classes(
                            "mgmt-page-foot w-full mt-auto flex-shrink-0"
                        ):
                            with ui.row().classes(
                                "mgmt-page-footer-row w-full items-center justify-between text-grey-6"
                            ):
                                ui.label(
                                    "排序请在无搜索/无归属筛选时操作"
                                ).classes("uppercase tracking-widest")
                                self._rules_total_footer = ui.label("0 RULES TOTAL").classes(
                                    "uppercase tracking-widest"
                                )

                with ui.tab_panel("remote").classes("h-full min-h-0 flex-1"):
                    self._remote_tab_shell = ui.column().classes(
                        "firewall-peer-remote firewall-remote-canvas w-full min-h-0 flex-1 gap-0"
                    )
                    with self._remote_tab_shell:
                        self._build_remote_tab_static()

        self._refresh_rules()

    def _on_strat_change(self, e) -> None:
        v = str(getattr(e, "value", None) or "center")
        self._center_tab_active = v == "center"
        logger.debug("firewall 策略页签切换为 %s", v)
        if self._center_search_controls is not None:
            self._center_search_controls.set_visibility(self._center_tab_active)
        if self._center_tab_active:
            self._remote_fetch_generation += 1
            self._remote_fetch_running_peer = None
            self._selected_remote_indices.clear()
            self._refresh_rules()
        else:
            self.selected_rule_ids.clear()
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

    def _on_enter_remote_tab(self) -> None:
        """进入「对端远端」：同步下拉后必须先 SSH 取对端真值落库，再谈编辑（与换节点时一致）。"""
        self._ensure_remote_tab_synced_with_peers()
        self._refresh_remote_view()
        self._schedule_remote_fetch_via_ssh()

    def _ensure_remote_tab_synced_with_peers(self) -> None:
        """根据 :meth:`PeerService.list_all` 重建或更新对端下拉的选项（避免首屏/新建对端后 UI 不刷新）。"""
        if not self._remote_tab_shell:
            return
        opts = self._peer_options_for_select()
        if opts and self._remote_peer_select is None:
            self._remote_tab_shell.clear()
            with self._remote_tab_shell:
                self._build_remote_tab_static()
            return
        if not opts and self._remote_peer_select is not None:
            self._remote_tab_shell.clear()
            with self._remote_tab_shell:
                self._build_remote_tab_static()
            return
        if self._remote_peer_select is not None and opts:
            self._remote_peer_select.options = opts
            cur = str(self._remote_peer_select.value or "").strip()
            if cur not in opts:
                self._remote_peer_select.value = next(iter(opts))
            self._remote_peer_select.update()

    def _on_remote_peer_select_change(self) -> None:
        """对端节点变化：以新机为准，必须重新 SSH 拉取该对端链并落库，再展示。"""
        self._remote_fetch_generation += 1
        self._remote_fetch_running_peer = None
        self._refresh_remote_view()
        self._schedule_remote_fetch_via_ssh()

    def _schedule_remote_fetch_via_ssh(self) -> None:
        """在「对端远端」对当前选中的对端：SSH 拉取 → ``record_from_fetch`` 落库；失败 ``notify`` + ``logger``。"""
        if self._center_tab_active:
            return
        target = self._active_remote_peer_id()
        if not target:
            return
        if self._remote_fetch_running_peer == target:
            logger.info("对端链正在拉取，跳过重复请求 peer=%s", target)
            return
        self._remote_fetch_generation += 1
        generation = self._remote_fetch_generation
        self._remote_fetch_running_peer = target
        self._remote_fetch_error_by_peer.pop(target, None)
        self._refresh_remote_toolbar_state()
        try:
            background_tasks.create(
                self._remote_fetch_chain_via_ssh(target, generation),
                name="firewall-remote-fetch",
            )
        except RuntimeError:
            if self._remote_fetch_running_peer == target:
                self._remote_fetch_running_peer = None
            self._refresh_remote_toolbar_state()
            return

    async def _remote_fetch_chain_via_ssh(self, target: str, generation: int) -> None:
        """用对端上当前链覆盖本地工作副本（``record_from_fetch``）；不判断此前是否已有缓存，保证与对端一致。"""
        if self._center_tab_active or generation != self._remote_fetch_generation:
            return
        try:
            snap = await run.io_bound(
                self._peer_service.fetch_remote_peer_filter_chain_snapshot, target
            )
        except Exception as exc:
            if self._center_tab_active or generation != self._remote_fetch_generation:
                return
            logger.error("SSH 拉取对端链失败 peer=%s: %s", target, exc)
            self._remote_fetch_error_by_peer[target] = str(exc)
            self._notify_for_page(f"从对端拉取失败: {exc}", type="negative")
            self._refresh_remote_toolbar_state()
            return
        finally:
            if self._remote_fetch_running_peer == target:
                self._remote_fetch_running_peer = None
            self._refresh_remote_toolbar_state()
        if self._center_tab_active or generation != self._remote_fetch_generation:
            return
        remote_chain_cache.record_from_fetch(snap, target)
        self._remote_fetch_error_by_peer.pop(target, None)
        show = target[:8] if len(target) > 8 else target
        logger.debug("对端链已拉取并落库 peer=%s", show)
        if self._active_remote_peer_id() != target:
            return
        self._refresh_remote_view()

    def _build_remote_tab_static(self) -> None:
        """构建对端选择、SSH/缓存状态、拉取推送工具栏和规则视图容器。"""

        opts = self._peer_options_for_select()

        with ui.element("section").classes("mgmt-panel w-full"):
            with ui.row().classes("w-full items-end gap-3 flex-wrap"):
                if opts:
                    first_id = next(iter(opts))
                    self._remote_peer_select = (
                        ui.select(
                            opts,
                            label="对端节点",
                            value=first_id,
                            with_input=True,
                        )
                        .classes("min-w-[18rem] flex-1")
                        .props("outlined dense")
                    )
                    self._remote_peer_select.on_value_change(
                        lambda _event: self._on_remote_peer_select_change()
                    )
                else:
                    self._remote_peer_select = None
                    ui.label("暂无对端站点").classes("mgmt-section-title")

                self._remote_status_container = ui.row().classes(
                    "items-center gap-2"
                )
                self._remote_cache_status_container = ui.row().classes(
                    "items-center gap-2"
                )

                self._remote_pull_button = secondary_action(
                    "从对端拉取",
                    icon="cloud_download",
                    on_click=self._schedule_remote_fetch_via_ssh,
                    disabled=not bool(opts),
                )
                self._remote_push_button = primary_action(
                    "推送到对端",
                    icon="cloud_upload",
                    on_click=self._confirm_remote_push,
                    disabled=True,
                )

            self._remote_cache_meta_label = ui.label(
                "拉取：— · 推送：—"
            ).classes("section-caption q-mt-sm")

        self._remote_list_container = ui.column().classes(
            "firewall-remote-list-stretch w-full min-h-0 gap-0 h-full"
        )

        with ui.element("div").classes(
            "mgmt-page-foot w-full mt-auto flex-shrink-0"
        ):
            with ui.row().classes(
                "mgmt-page-footer-row w-full items-center justify-between text-grey-6"
            ):
                ui.label(
                    "远端链以 SSH 真值为准；修改后立即写回"
                ).classes("uppercase tracking-widest")
                self._remote_rules_total_footer = ui.label(
                    "0 RULES TOTAL"
                ).classes("uppercase tracking-widest")

        self._refresh_remote_toolbar_state()

    def _selected_remote_peer_row(self) -> dict[str, Any] | None:
        """返回当前对端记录。"""

        peer_id = self._active_remote_peer_id()
        if not peer_id:
            return None
        try:
            row = self._peer_service.get(peer_id)
        except Exception:
            logger.exception("读取当前对端失败 peer=%s", peer_id)
            return None
        return dict(row) if row else None

    def _refresh_remote_toolbar_state(self) -> None:
        """按 SSH 条件、拉取忙碌和缓存状态刷新远端工具栏。"""

        peer_id = self._active_remote_peer_id()
        peer = self._selected_remote_peer_row()
        fetch_running = bool(
            peer_id and self._remote_fetch_running_peer == peer_id
        )
        try:
            has_auth = bool(
                peer and peer_row_has_usable_ssh_auth(peer)
            )
        except Exception:
            logger.exception("判断对端 SSH 凭据失败 peer=%s", peer_id)
            has_auth = False

        capability = adapt_remote_peer_capability(
            peer,
            has_ssh_auth=has_auth,
            fetch_running=fetch_running,
        )
        cache = (
            remote_chain_cache.read_remote_chain_cache(peer_id)
            if peer_id
            else None
        )
        cache_view = adapt_remote_cache_status(
            cache,
            fetch_running=fetch_running,
        )
        fetch_error = str(
            self._remote_fetch_error_by_peer.get(peer_id, "")
        ).strip()

        if self._remote_status_container is not None:
            self._remote_status_container.clear()
            with self._remote_status_container:
                status_badge(
                    (
                        "failed"
                        if fetch_error
                        else str(capability["status_tone"])
                    ),
                    label=(
                        "拉取失败"
                        if fetch_error
                        else str(capability["status_label"])
                    ),
                    tooltip=fetch_error or str(capability["reason"] or ""),
                )

        if self._remote_cache_status_container is not None:
            self._remote_cache_status_container.clear()
            with self._remote_cache_status_container:
                status_badge(
                    str(cache_view["status_tone"]),
                    label=str(cache_view["status_label"]),
                    tooltip=str(cache_view["last_sync_error"] or ""),
                )

        if self._remote_cache_meta_label is not None:
            self._remote_cache_meta_label.text = (
                f"拉取：{cache_view['last_pulled_at']} · "
                f"推送：{cache_view['last_pushed_at']} · "
                f"本地 {cache_view['row_count']} 条"
            )

        if self._remote_pull_button is not None:
            if capability["can_fetch"]:
                self._remote_pull_button.enable()
            else:
                self._remote_pull_button.disable()
            if fetch_running:
                self._remote_pull_button.props(add="loading")
            else:
                self._remote_pull_button.props(remove="loading")

        if self._remote_push_button is not None:
            any_push_running = self._remote_push_running_peer is not None
            push_running = bool(
                peer_id and self._remote_push_running_peer == peer_id
            )
            can_push = (
                bool(capability["can_push"])
                and bool(cache_view["can_push"])
                and not any_push_running
            )
            if can_push:
                self._remote_push_button.enable()
            else:
                self._remote_push_button.disable()
            if push_running:
                self._remote_push_button.props(add="loading")
            else:
                self._remote_push_button.props(remove="loading")

    def _active_remote_peer_id(self) -> str:
        if not self._remote_peer_select:
            return ""
        return str(self._remote_peer_select.value or "").strip()

    async def _on_remote_pull(self) -> None:
        if not self._require_remote_strategy_tab():
            return
        pid = self._active_remote_peer_id()
        if not pid:
            self._notify_for_page("请选择对端", type="warning")
            return
        try:
            snap = await run.io_bound(self._peer_service.fetch_remote_peer_filter_chain_snapshot, pid)
        except Exception as exc:
            logger.error("拉取对端链失败 peer=%s: %s", pid, exc)
            self._notify_for_page(f"拉取失败: {exc}", type="negative")
            return
        remote_chain_cache.record_from_fetch(snap, pid)
        self._notify_for_page("已拉取", type="positive")
        self._refresh_remote_view()

    async def _apply_remote_cache_to_peer(self, peer_id: str) -> bool:
        """将当前本地工作副本（库内 ``rows``）经 SSH 写回对端机链；与拉取成对，保「真值→改库→推对端」。

        本方法不调用 :func:`mark_pending_apply`（避免覆盖「改前」备份）。除
        :meth:`_on_remote_push` 在发起 SSH 前会自行 ``mark_pending`` 外，其余调用方须在
        改库后、调用本方法前已对该 peer 执行过 ``mark_pending_apply`` 且 ``rows`` 与库内一致。
        成功返回 True；失败时落库错误并 :meth:`_notify_for_page`。"""
        cache = remote_chain_cache.read_remote_chain_cache(peer_id) or {}
        chain = str(cache.get("chain") or IptablesManager.peer_chain_name_for_id(peer_id))
        ce = bool(cache.get("chain_exists"))
        rows = list(cache.get("rows") or [])
        rests = remote_chain_cache.rests_to_push_list(rows)  # type: ignore[arg-type]
        try:
            await run.io_bound(self._peer_service.apply_remote_peer_filter_chain_rests, peer_id, rests)
            remote_chain_cache.record_after_apply(peer_id, chain, bool(ce), rows, True, None)
        except Exception as exc:
            logger.error("写回对端链失败 peer=%s: %s", peer_id, exc)
            remote_chain_cache.record_after_apply(peer_id, chain, bool(ce), rows, False, str(exc))
            self._notify_for_page(f"写回失败: {exc}", type="negative")
            return False
        return True

    def _confirm_remote_push(self) -> None:
        """确认后推送当前工作副本，避免误覆盖远端链。"""

        if not self._require_remote_strategy_tab():
            return
        peer_id = self._active_remote_peer_id()
        if not peer_id:
            self._notify_for_page("请选择对端", type="warning")
            return
        if self._remote_push_running_peer is not None:
            self._notify_for_page(
                "已有对端写回正在执行，请勿重复提交",
                type="warning",
            )
            return
        cache = remote_chain_cache.read_remote_chain_cache(peer_id)
        if not cache:
            self._notify_for_page(
                "尚无本地工作副本，请先从对端拉取或导入",
                type="warning",
            )
            return
        rows = list(cache.get("rows") or [])
        confirm_dialog.show(
            f"推送当前 {len(rows)} 条规则到对端？",
            impact=(
                "将通过 SSH 重写当前对端用户链；"
                "失败时本地工作副本保留错误状态并按 prepush 备份恢复。"
            ),
            object_count=len(rows),
            danger_level="warning",
            on_confirm=lambda: self._schedule_remote_coro(
                self._on_remote_push()
            ),
        )

    async def _on_remote_push(self) -> bool:
        """推送当前工作副本，带页面级重复提交保护。"""

        if not self._require_remote_strategy_tab():
            return False
        peer_id = self._active_remote_peer_id()
        if not peer_id:
            self._notify_for_page("请选择对端", type="warning")
            return False
        if self._remote_push_running_peer is not None:
            self._notify_for_page(
                "已有对端写回正在执行，请勿重复提交",
                type="warning",
            )
            return False

        cache = remote_chain_cache.read_remote_chain_cache(peer_id)
        if not cache:
            self._notify_for_page(
                "尚无本地工作副本，请先从对端拉取或导入",
                type="warning",
            )
            return False

        self._remote_push_running_peer = peer_id
        self._refresh_remote_toolbar_state()
        chain = str(
            cache.get("chain")
            or IptablesManager.peer_chain_name_for_id(peer_id)
        )
        chain_exists = bool(cache.get("chain_exists"))
        rows = list(cache.get("rows") or [])

        try:
            remote_chain_cache.mark_pending_apply(
                peer_id,
                chain,
                chain_exists,
                rows,
            )
            ok = await self._apply_remote_cache_to_peer(peer_id)
            if ok:
                self._notify_for_page("已写回对端", type="positive")
            return ok
        finally:
            if self._remote_push_running_peer == peer_id:
                self._remote_push_running_peer = None
            self._refresh_remote_toolbar_state()
            self._refresh_remote_view()

    async def _on_remote_row_enabled_change(self, idx: int, e) -> None:
        """兼容旧开关入口。"""

        await self._set_remote_row_enabled(idx, bool(e.value))

    @staticmethod
    def _rows_from_remote_import_payload(raw: dict) -> list[dict]:
        """从导入 JSON 根对象解析 ``rows``，与 ``remote_chain_cache`` 工作副本同形。"""
        if not isinstance(raw, dict):
            raise ValueError("JSON 根须为 object")
        if raw.get("rows") and isinstance(raw["rows"], list):
            out: list[dict] = []
            for x in raw["rows"]:
                if not isinstance(x, dict):
                    continue
                r = str(x.get("rest", "")).strip()
                if not r:
                    continue
                if "\n" in r or "\r" in r:
                    raise ValueError("规则片段不得含换行")
                item: dict[str, Any] = {
                    "rest": r,
                    "enabled": bool(x.get("enabled", True)),
                }
                description = str(x.get("description") or "").strip()
                if description:
                    item["description"] = description
                if x.get("priority") is not None:
                    try:
                        item["priority"] = int(x.get("priority"))
                    except (TypeError, ValueError):
                        raise ValueError(
                            f"规则优先级无效: {x.get('priority')!r}"
                        )
                out.append(item)
            return out
        rests = [str(s).strip() for s in (raw.get("rests") or []) if str(s).strip()]
        return [{"rest": s, "enabled": True} for s in rests]

    @staticmethod
    def _rows_from_iptables_paste(text: str, chain: str) -> list[dict]:
        """从粘贴的 iptables 文本得到 ``rows``，与对端 ``iptables -S <链>`` 解析结果同形。

        支持：1) 每行 ``-A <链名> <rest>`` 与 SSH 拉取一致；2) 无 ``-A`` 前缀时每行整段作为 ``rest``。
        """
        ch = (chain or "").strip()
        if not ch:
            raise ValueError("链名为空")
        prefix = f"-A {ch} "
        rows: list[dict] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.startswith("-A "):
                if not s.startswith(prefix):
                    raise ValueError(
                        f"第 {line_number} 行不属于当前对端链 {ch}，"
                        f"请只贴本链规则：{s[:120]}"
                    )
                rest = s[len(prefix) :].strip()
                if not rest:
                    continue
                if "\n" in rest or "\r" in rest:
                    raise ValueError("规则片段不得含换行")
                rows.append({"rest": rest, "enabled": True})
                continue
            spec = try_parse_simplified_line(s)
            if spec is not None:
                for r in peer_rests_from_simplified_line(spec):
                    if "\n" in r or "\r" in r:
                        raise ValueError("规则片段不得含换行")
                    rows.append({"rest": r, "enabled": True})
            else:
                rest = s
                if not rest:
                    continue
                if "\n" in rest or "\r" in rest:
                    raise ValueError("规则片段不得含换行")
                rows.append({"rest": rest, "enabled": True})
        if not rows:
            raise ValueError(
                "未解析到任何规则。请贴入 ``iptables -S <链名>`` 输出，或每行一条规则片段（-s/-j/...）。"
            )
        return rows

    @staticmethod
    def _rows_from_remote_import_text_mixed(
        text: str, peer_id: str, cname: str
    ) -> tuple[list[dict], str, bool]:
        """对端导入：以 ``{`` 开头则按历史 JSON 工作副本解析，否则按 iptables 文本解析。"""
        t = (text or "").strip()
        if t.startswith("{"):
            try:
                raw = json.loads(t)
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSON 解析失败: {exc}") from exc
            in_pid = str(raw.get("peer_id") or "").strip() if isinstance(raw, dict) else ""
            if in_pid and in_pid != str(peer_id).strip():
                raise ValueError("JSON 中 peer_id 与当前所选对端不一致。")
            if not isinstance(raw, dict):
                raise ValueError("JSON 根须为 object")
            rows = FirewallPage._rows_from_remote_import_payload(raw)
            chn = str(raw.get("chain") or cname)
            cex = bool(raw.get("chain_exists", True))
            return rows, chn, cex
        rows = FirewallPage._rows_from_iptables_paste(t, cname)
        return rows, cname, True

    def _toggle_remote_row_selection(self, idx: int, checked: bool) -> None:
        if checked:
            self._selected_remote_indices.add(int(idx))
        else:
            self._selected_remote_indices.discard(int(idx))
        self._sync_remote_select_all()

    def _toggle_remote_select_all(self, checked: bool) -> None:
        """选择或取消选择当前对端的全部可见规则。"""
        update_visible_selection(
            self._selected_remote_indices,
            self._visible_remote_indices,
            checked,
        )
        apply_row_checkboxes(self._remote_row_checkboxes, self._selected_remote_indices)
        self._sync_remote_select_all()

    def _sync_remote_select_all(self) -> None:
        """同步对端规则列表全选复选框。"""
        if self._remote_select_all_checkbox is not None:
            sync_select_all_checkbox(
                self._remote_select_all_checkbox,
                self._visible_remote_indices,
                self._selected_remote_indices,
            )

    def _schedule_remote_coro(self, coro) -> None:
        try:
            asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            pass

    @staticmethod
    def _remote_grid_columns(*, can_reorder: bool = False) -> list[dict[str, Any]]:
        """对端链规则核心列定义，原始 rest 始终保留。"""

        return [
            {
                "field": "row_number",
                "headerName": "#",
                "width": 85,
                "pinned": "left",
                "checkboxSelection": True,
                "headerCheckboxSelection": True,
                "rowDrag": bool(can_reorder),
            },
            {
                "field": "status_label",
                "headerName": "状态",
                "width": 95,
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
            },
            {
                "field": "action_label",
                "headerName": "动作",
                "width": 105,
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
                "width": 100,
                "sortable": False,
                "filter": False,
                "pinned": "right",
            },
        ]

    def _refresh_remote_view(self) -> None:
        """按当前对端缓存刷新 AG Grid；拉取失败时保留可用旧缓存。"""

        if not self._remote_list_container:
            return

        self._refresh_remote_toolbar_state()
        self._remote_list_container.clear()
        self._remote_grid_view = None
        self._visible_remote_indices = []

        peer_id = self._active_remote_peer_id()
        if not peer_id:
            self._remote_grid_rows = []
            self._selected_remote_indices.clear()
            if self._remote_rules_total_footer is not None:
                self._remote_rules_total_footer.set_text("0 RULES TOTAL")
            with self._remote_list_container:
                empty_state(
                    "请先选择对端",
                    description="选择对端后会自动尝试通过 SSH 拉取远端链真值。",
                    icon="hub",
                )
            return

        cache = remote_chain_cache.read_remote_chain_cache(peer_id)
        fetch_running = self._remote_fetch_running_peer == peer_id
        fetch_error = str(
            self._remote_fetch_error_by_peer.get(peer_id, "")
        ).strip()

        if cache is None:
            self._remote_grid_rows = []
            self._selected_remote_indices.clear()
            if self._remote_rules_total_footer is not None:
                self._remote_rules_total_footer.set_text("0 RULES TOTAL")
            with self._remote_list_container:
                if fetch_running:
                    loading_state("正在从对端拉取规则…")
                elif fetch_error:
                    error_state(
                        fetch_error,
                        title="拉取对端规则失败",
                        on_retry=self._schedule_remote_fetch_via_ssh,
                    )
                else:
                    empty_state(
                        "尚无本地工作副本",
                        description="点击“从对端拉取”读取真实链；新建或导入后也可以建立工作副本。",
                        action_label="从对端拉取",
                        on_action=self._schedule_remote_fetch_via_ssh,
                        icon="cloud_download",
                    )
            return

        raw_rows = list(cache.get("rows") or [])
        rows = adapt_remote_rule_rows(raw_rows, peer_id=peer_id)
        for row in rows:
            row["actions"] = "操作"
        self._remote_grid_rows = rows
        self._visible_remote_indices = [int(row["index"]) for row in rows]

        valid_indices = set(self._visible_remote_indices)
        self._selected_remote_indices.intersection_update(valid_indices)

        if self._remote_rules_total_footer is not None:
            self._remote_rules_total_footer.set_text(
                f"{len(rows)} RULES TOTAL"
            )

        with self._remote_list_container:
            if fetch_error:
                with ui.element("div").classes("w-full q-mb-sm"):
                    error_state(
                        fetch_error,
                        title="本次拉取失败，当前仍显示上次缓存",
                        on_retry=self._schedule_remote_fetch_via_ssh,
                    )

            if not rows:
                empty_state(
                    "远端链暂无规则",
                    description="当前工作副本为空；可以新建、导入或再次从对端拉取。",
                    action_label="新建规则",
                    on_action=self._show_create_remote_line_dialog,
                    icon="shield",
                )
                return

            selected_ids = {
                f"{peer_id}:{index}"
                for index in self._selected_remote_indices
            }
            peer = self._selected_remote_peer_row()
            try:
                has_auth = bool(peer and peer_row_has_usable_ssh_auth(peer))
            except Exception:
                logger.exception("判断对端排序 SSH 条件失败 peer=%s", peer_id)
                has_auth = False
            self._remote_reorder_allowed = bool(
                has_auth
                and not fetch_running
                and not fetch_error
                and self._remote_push_running_peer is None
            )

            self._remote_grid_view = render_data_grid(
                self._remote_grid_columns(
                    can_reorder=self._remote_reorder_allowed
                ),
                rows,
                row_id="id",
                page_size=50,
                selectable=True,
                selected_ids=selected_ids,
                on_cell_click=self._on_remote_grid_cell_click,
            )
            self._remote_grid_view.grid.on(
                "selectionChanged",
                self._on_remote_grid_selection_changed,
            )
            if self._remote_reorder_allowed:
                self._remote_grid_view.grid.options["rowDragManaged"] = True
                self._remote_grid_view.grid.options[
                    "suppressMoveWhenRowDragging"
                ] = False
                self._remote_grid_view.grid.on(
                    "rowDragEnd",
                    self._on_remote_grid_row_drag_end,
                )
                self._remote_grid_view.grid.update()

    async def _on_remote_grid_selection_changed(self, *_args) -> None:
        """同步 AG Grid 选择到现有远端批量操作索引集合。"""

        if self._remote_grid_view is None:
            self._selected_remote_indices.clear()
            return
        selected = await self._remote_grid_view.get_selected_rows()
        self._selected_remote_indices = {
            int(row.get("index"))
            for row in selected
            if isinstance(row.get("index"), int)
        }

    async def _on_remote_grid_cell_click(self, event) -> None:
        """复制原始/结构化字段，操作列暂复用现有编辑表单。"""

        args = getattr(event, "args", None) or {}
        if not isinstance(args, dict):
            return
        data = args.get("data") or {}
        if not isinstance(data, dict):
            return

        col_id = str(args.get("colId") or "")
        index = data.get("index")
        if col_id == "actions" and isinstance(index, int):
            self._show_remote_rule_actions(index)
            return

        value = ""
        if col_id == "raw_rest":
            value = str(data.get("raw_rest") or "")
        elif col_id in {"source", "dest", "dest_port"}:
            value = str(data.get(col_id) or "")
        if value and value not in {"—", "*"}:
            await self._copy_text_to_clipboard(value)

    def _show_remote_rule_actions(self, index: int) -> None:
        """远端单行操作面板；编辑路径继续使用现有写回逻辑。"""

        peer_id = self._active_remote_peer_id()
        cache = (
            remote_chain_cache.read_remote_chain_cache(peer_id)
            if peer_id
            else None
        )
        rows = list(cache.get("rows") or []) if cache else []
        if not (0 <= index < len(rows)):
            self._notify_for_page(
                "本地工作副本已变化，请刷新后重试",
                type="warning",
            )
            return
        row = dict(rows[index])
        enabled = bool(row.get("enabled", True))

        with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
            ui.label(f"对端规则 #{index + 1}").classes("text-h6")
            ui.label(str(row.get("rest") or "")).classes(
                "font-mono text-caption break-all"
            )
            with ui.column().classes("w-full gap-2 q-mt-sm"):
                secondary_action(
                    "编辑",
                    icon="edit",
                    on_click=lambda: (
                        dialog.close(),
                        self._show_edit_remote_line_dialog(index, row),
                    ),
                )
                secondary_action(
                    "停用" if enabled else "启用",
                    icon="pause_circle" if enabled else "play_circle",
                    on_click=lambda: (
                        dialog.close(),
                        self._schedule_remote_coro(
                            self._set_remote_row_enabled(index, not enabled)
                        ),
                    ),
                )
                secondary_action(
                    "复制原始规则",
                    icon="content_copy",
                    on_click=lambda: self._schedule_remote_text_copy(
                        str(row.get("rest") or "")
                    ),
                )
                danger_action(
                    "删除",
                    icon="delete",
                    on_click=lambda: (
                        dialog.close(),
                        self._confirm_delete_remote_line(index),
                    ),
                )
            secondary_action("关闭", on_click=dialog.close)

        dialog.open()

    def _schedule_remote_text_copy(self, text: str) -> None:
        """从同步菜单安全调度原始规则复制。"""

        try:
            background_tasks.create(
                self._copy_text_to_clipboard(str(text or "")),
                name="firewall-copy-remote-rule",
            )
        except RuntimeError as exc:
            logger.error("调度对端规则复制失败: %s", exc)
            self._notify_for_page("复制任务未能启动", type="negative")

    async def _set_remote_row_enabled(
        self,
        index: int,
        enabled: bool,
    ) -> bool:
        """按显式目标状态切换单行并立即写回。"""

        if not self._require_remote_strategy_tab():
            return False
        peer_id = self._active_remote_peer_id()
        if not peer_id:
            self._notify_for_page("请选择对端", type="warning")
            return False

        cache = remote_chain_cache.read_remote_chain_cache(peer_id) or {}
        rows = list(cache.get("rows") or [])
        if not (0 <= index < len(rows)):
            self._notify_for_page(
                "本地数据已变化，请重拉或切换对端",
                type="warning",
            )
            self._refresh_remote_view()
            return False

        if bool(rows[index].get("enabled", True)) == bool(enabled):
            return True

        rows[index]["enabled"] = bool(enabled)
        chain = str(
            cache.get("chain")
            or IptablesManager.peer_chain_name_for_id(peer_id)
        )
        remote_chain_cache.mark_pending_apply(
            peer_id,
            chain,
            bool(cache.get("chain_exists", True)),
            rows,
        )
        ok = await self._apply_remote_cache_to_peer(peer_id)
        if ok:
            self._notify_for_page(
                "已启用并写回对端"
                if enabled
                else "已停用并写回对端",
                type="positive",
            )
        self._refresh_remote_view()
        return ok

    def _peer_id_short_label(self, pid: str) -> str:
        p = str(pid or "")
        if len(p) > 8:
            return f"{p[:8]}…"
        return p or "—"

    @staticmethod
    def _move_order(items: list[Any], old_index: int, new_index: int) -> list[Any]:
        """按拖拽前后索引生成新顺序，不修改调用方列表。"""

        if not items or not 0 <= old_index < len(items) or not 0 <= new_index < len(items):
            logger.error("拖拽索引越界: old=%s new=%s count=%s", old_index, new_index, len(items))
            return []
        result = list(items)
        result.insert(new_index, result.pop(old_index))
        return result

    def _on_remote_grid_row_drag_end(self, event) -> None:
        """AG Grid 拖动结束后按缓存索引生成新顺序并立即写回。"""

        if not self._remote_reorder_allowed:
            self._notify_for_page(
                "当前正在拉取/推送、SSH 不可用或远端真值未知，不能调整顺序",
                type="warning",
            )
            self._refresh_remote_view()
            return

        args = getattr(event, "args", None) or {}
        if not isinstance(args, dict):
            self._refresh_remote_view()
            return

        data = args.get("data")
        if not isinstance(data, dict):
            node = args.get("node")
            data = node.get("data") if isinstance(node, dict) else None
        row_index = (data or {}).get("index")
        if not isinstance(row_index, int):
            self._refresh_remote_view()
            return
        if row_index not in self._visible_remote_indices:
            self._refresh_remote_view()
            return

        old_position = self._visible_remote_indices.index(row_index)
        try:
            new_position = int(
                args.get("overIndex", args.get("rowIndex", old_position))
            )
        except (TypeError, ValueError):
            new_position = old_position
        new_position = max(
            0,
            min(new_position, len(self._visible_remote_indices) - 1),
        )
        new_order = self._move_order(
            self._visible_remote_indices,
            old_position,
            new_position,
        )
        if new_order and new_order != self._visible_remote_indices:
            self._schedule_remote_coro(
                self._do_remote_reorder_async(new_order)
            )
        else:
            self._refresh_remote_view()

    def _on_remote_sort_end(self, event) -> None:
        """兼容旧测试入口；页面已改用 AG Grid rowDragEnd。"""

        old_index = getattr(event, "old_index", None)
        new_index = getattr(event, "new_index", None)
        if not isinstance(old_index, int) or not isinstance(new_index, int):
            self._refresh_remote_view()
            return
        new_order = self._move_order(
            self._visible_remote_indices,
            old_index,
            new_index,
        )
        if new_order:
            self._schedule_remote_coro(
                self._do_remote_reorder_async(new_order)
            )

    async def _do_remote_reorder_async(self, new_order: list[int]) -> None:
        if not self._require_remote_strategy_tab():
            return
        pid = self._active_remote_peer_id()
        if not pid:
            return
        c2 = remote_chain_cache.read_remote_chain_cache(pid) or {}
        cname = IptablesManager.peer_chain_name_for_id(pid)
        rws = list(c2.get("rows") or [])
        n = len(rws)
        if n < 1 or len(new_order) != n or set(new_order) != set(range(n)):
            logger.error("对端重排与本地行数不一致 n=%s order=%s", n, new_order)
            self._refresh_remote_view()
            return
        reordered = [rws[i] for i in new_order]
        remote_chain_cache.mark_pending_apply(
            pid,
            str(c2.get("chain") or cname),
            bool(c2.get("chain_exists", True)),
            reordered,
        )
        if await self._apply_remote_cache_to_peer(pid):
            self._selected_remote_indices.clear()
            self._notify_for_page("规则顺序已更新", type="positive")
        self._refresh_remote_view()

    def _render_remote_rule_card(
        self, idx: int, row: dict, *, row_index_1based: int, n_rows: int
    ) -> None:
        rest = str(row.get("rest", "") or "")
        en = bool(row.get("enabled", True))
        hints = _hints_from_iptables_rest_line(rest)
        protocol = (hints.get("proto") or "all").upper()
        if protocol == "ANY":
            protocol = "ALL"
        action = (hints.get("action") or "—").upper()
        source = hints.get("source") or ""
        if not source:
            source = "*"
        dest = str(hints.get("dest") or "*")
        dest_port = str(hints.get("dport") or "*")
        flow_text = f"{source} → {dest}:{dest_port}"
        pri = int(row.get("priority", 0) or 0) or (row_index_1based * 10)
        peer_id = (self._active_remote_peer_id() or "").strip()
        # 与中心卡「规则 id 短码」对位：本页用链内行号
        title_text = f"#{row_index_1based}"
        second_line = FirewallPage._second_line_comment_text(
            row,
            title_text=title_text,
            is_center_peer=False,
            is_remote_row=True,
            iptables_comment=str(hints.get("comment") or ""),
        )
        # 对端行无归属字段时用当前对端 id，与中心「对端·中心放行」卡一致
        owner_tag = peer_id
        with ui.element("div").classes(
            "firewall-rule-card" + ("" if en else " opacity-60")
        ).props(f'data-remote-row-index="{idx}"'):
            with ui.element("div").classes("firewall-rule-card-head"):
                with ui.row().classes("items-center gap-2 flex-1 min-w-0"):
                    self._remote_row_checkboxes[idx] = ui.checkbox(
                        value=idx in self._selected_remote_indices,
                        on_change=lambda e, j=idx: self._toggle_remote_row_selection(j, e.value),
                    ).classes("mgmt-checkbox")
                    ui.icon("drag_indicator").classes("firewall-rule-drag cursor-move text-grey-6")
                    with ui.element("div").classes("mgmt-record-copy"):
                        with ui.row().classes("items-center gap-2 flex-wrap min-w-0"):
                            ui.label(title_text).classes("mgmt-record-title")
                            ui.label("对端站点").classes("user-session-badge")
                        if second_line:
                            with ui.row().classes("w-full min-w-0"):
                                ui.label(second_line).classes("text-caption text-grey-6 line-clamp-1")
                        with ui.element("div").classes("mgmt-record-meta"):
                            if (owner_tag or "").strip():
                                ot = (owner_tag or "").strip()
                                with ui.row().classes("items-center gap-1 flex-wrap min-w-0"):
                                    ui.label("归属").classes("text-grey-6 text-caption shrink-0")
                                    o = (
                                        ui.label(ot)
                                        .classes("meta-badge cursor-pointer min-w-0 break-all")
                                        .tooltip("点击复制")
                                    )
                                    o.on("click", lambda t=ot: self._copy_text_to_clipboard(t))
                            for lab, val in [
                                ("协议", protocol),
                                ("动作", action),
                                ("优先级", str(pri)),
                            ]:
                                with ui.element("span").classes("mgmt-meta-item"):
                                    ui.label(f"{lab} {val}")
                            with ui.element("span").classes("mgmt-meta-item mgmt-meta-flow"):
                                ui.label(flow_text)
                with ui.element("div").classes("mgmt-actions").classes("row items-center gap-0"):
                    ui.switch(value=en, on_change=lambda e, j=idx: self._schedule_remote_coro(
                        self._on_remote_row_enabled_change(j, e)
                    )).props("dense")
                    ui.button(
                        icon="edit",
                        on_click=lambda j=idx, r=dict(row): self._show_edit_remote_line_dialog(j, r),
                    ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn").tooltip("编辑")
                    ui.button(
                        icon="delete",
                        on_click=lambda j=idx: self._confirm_delete_remote_line(j),
                    ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn is-delete").tooltip("删除")

    def _show_edit_remote_line_dialog(self, idx: int, row: dict) -> None:
        """统一表单编辑对端规则；可结构化编辑，也可直接修改原始 rest。"""

        if not self._require_remote_strategy_tab():
            return
        peer_id = self._active_remote_peer_id()
        if not peer_id:
            self._notify_for_page("请选择对端", type="warning")
            return

        edit = _remote_row_to_edit_form_dict(row)
        source_subnet = edit.get("source_subnet") or ""
        source_ips = edit.get("source_ips")
        source_value = source_subnet or (
            ",".join(source_ips) if source_ips else ""
        )
        original_rest = str(row.get("rest") or "").strip()
        chain_name = IptablesManager.peer_chain_name_for_id(peer_id)
        fields: dict[str, Any] = {}

        def body() -> None:
            ui.label(
                f"链 {chain_name}；保存后立即 SSH 写回。"
                " 原始 rest 改动优先于结构化字段。"
            ).classes("section-caption")
            fields["action"] = ui.select(
                {
                    "accept": "放行 (ACCEPT)",
                    "drop": "丢弃 (DROP)",
                    "reject": "拒绝 (REJECT)",
                },
                label="动作",
                value=edit.get("action", "accept"),
            ).classes("w-full")
            fields["protocol"] = ui.select(
                {
                    "all": "不限 (ALL)",
                    "tcp": "TCP",
                    "udp": "UDP",
                    "icmp": "ICMP",
                },
                label="协议",
                value=str(edit.get("protocol") or "all").replace("any", "all"),
                with_input=True,
                new_value_mode="add-unique",
            ).classes("w-full")
            fields["source"] = ui.input(
                "源子网 CIDR 或源 IP 列表",
                value=source_value,
            ).classes("w-full")
            fields["dest_ip"] = ui.input(
                "目标地址",
                value=edit.get("dest_ip") or "",
            ).classes("w-full")
            fields["dest_port"] = ui.input(
                "目标端口",
                value=edit.get("dest_port") or "",
            ).classes("w-full")
            fields["description"] = ui.input(
                "规则描述",
                value=edit.get("description") or "",
            ).classes("w-full")
            fields["enabled"] = ui.switch(
                "启用",
                value=bool(edit.get("enabled", True)),
            )

            ui.separator().classes("q-my-sm")
            ui.label("高级：原始 iptables rest").classes("section-caption")
            fields["raw"] = (
                ui.textarea(
                    "原始 rest",
                    value=original_rest,
                )
                .classes("w-full font-mono")
                .props("rows=4")
            )
            fields["parse_status"] = ui.label("").classes("section-caption")

            def update_parse_status() -> None:
                raw = str(fields["raw"].value or "").strip()
                if not raw:
                    fields["parse_status"].text = "原始规则不能为空"
                    return
                if "\n" in raw or "\r" in raw:
                    fields["parse_status"].text = "单条规则不能包含换行"
                    return
                hints = parse_remote_rule_hints(raw)
                parsed = [
                    f"协议 {hints.get('proto') or 'ALL'}",
                    f"动作 {hints.get('action') or '—'}",
                    f"源 {hints.get('source') or '*'}",
                    f"目标 {hints.get('dest') or '*'}:{hints.get('dport') or '*'}",
                ]
                fields["parse_status"].text = " · ".join(parsed)

            fields["raw"].on_value_change(lambda _event: update_parse_status())
            update_parse_status()

        async def submit() -> bool:
            raw_value = str(fields["raw"].value or "").strip()
            if raw_value != original_rest:
                return await self._save_remote_raw_edit(
                    peer_id,
                    idx,
                    raw_value,
                    bool(fields["enabled"].value),
                )

            block_rows = self._build_remote_structured_block(
                peer_id,
                idx,
                action=str(fields["action"].value or "accept"),
                protocol=str(fields["protocol"].value or "all"),
                source_input=str(fields["source"].value or ""),
                dest_ip=str(fields["dest_ip"].value or "").strip() or None,
                dest_port=str(fields["dest_port"].value or "").strip() or None,
                description=str(fields["description"].value or "").strip() or None,
                enabled=bool(fields["enabled"].value),
            )
            if block_rows is None:
                return False
            return await self._save_remote_block_replace_and_push(
                peer_id,
                idx,
                block_rows,
            )

        form = create_form_dialog(
            f"编辑对端规则 #{idx + 1}",
            body=body,
            on_submit=submit,
            submit_label="保存并写回",
            close_on_success=True,
        )
        form.dialog.props("position=right maximized")
        form.card.classes(
            "w-[min(100vw,30rem)] h-full no-wrap overflow-y-auto"
        )
        form.open()

    def _build_remote_structured_block(
        self,
        peer_id: str,
        idx: int,
        *,
        action: str,
        protocol: str,
        source_input: str,
        dest_ip,
        dest_port,
        description,
        enabled: bool,
    ) -> list[dict[str, Any]] | None:
        """把结构化表单生成可替换的工作副本规则块。"""

        source_text = str(source_input or "").strip()
        source_subnet, source_ips = _parse_source_text_for_remote_save(
            source_text
        )
        port_text = str(dest_port or "").strip()
        normalized_action = str(action or "accept").lower()
        if normalized_action not in {"accept", "drop", "reject"}:
            normalized_action = "accept"
        normalized_protocol = str(protocol or "all").strip().lower()
        if normalized_protocol in {"", "any"}:
            normalized_protocol = "all"

        try:
            FirewallRule(
                owner_type="group",
                owner_id="_",
                action=normalized_action,  # type: ignore[arg-type]
                priority=1,
                protocol=normalized_protocol,
                dest_ip=str(dest_ip or "").strip() or None,
                dest_port=port_text or None,
            )
        except (ValidationError, ValueError) as exc:
            self._notify_for_page(str(exc), type="negative")
            return None

        try:
            rests = remote_rests_from_create_fields(
                source_subnet=source_subnet,
                source_ips=source_ips,
                action=normalized_action,
                protocol=normalized_protocol,
                dest_ip=str(dest_ip or "").strip() or None,
                dest_port=port_text or None,
            )
        except (ValueError, ValidationError) as exc:
            logger.error("对端编辑生成 rest 失败: %s", exc)
            self._notify_for_page(str(exc), type="negative")
            return None

        cache = remote_chain_cache.read_remote_chain_cache(peer_id) or {}
        rows = list(cache.get("rows") or [])
        priority = (
            int(rows[idx].get("priority", 0) or 0) or ((idx + 1) * 10)
            if 0 <= idx < len(rows)
            else ((idx + 1) * 10)
        )
        desc = str(description or "").strip()
        return [
            {
                "rest": rest,
                "enabled": bool(enabled),
                "priority": priority,
                **({"description": desc} if desc else {}),
            }
            for rest in rests
        ]

    async def _save_remote_raw_edit(
        self,
        peer_id: str,
        idx: int,
        raw_rest: str,
        enabled: bool,
    ) -> bool:
        """保存原始 rest，并经统一工作副本事务立即写回。"""

        body = str(raw_rest or "").strip()
        if not body:
            self._notify_for_page("原始规则不能为空", type="negative")
            return False
        if "\n" in body or "\r" in body:
            self._notify_for_page("单条规则不能含换行", type="negative")
            return False
        if self._active_remote_peer_id() != peer_id:
            self._notify_for_page("对端已切换，已取消写回", type="warning")
            return False

        cache = remote_chain_cache.read_remote_chain_cache(peer_id) or {}
        rows = list(cache.get("rows") or [])
        if not (0 <= idx < len(rows)):
            self._notify_for_page("本地数据已变化，请重试", type="warning")
            return False

        rows[idx] = {
            **rows[idx],
            "rest": body,
            "enabled": bool(enabled),
        }
        return await self._save_remote_rows_and_push(peer_id, rows)

    async def _save_remote_block_replace_and_push(
        self,
        peer_id: str,
        at_idx: int,
        new_block: list[dict[str, Any]],
    ) -> bool:
        """用一段规则替换当前行，并经统一工作副本事务写回。"""

        if not self._require_remote_strategy_tab():
            return False
        if self._active_remote_peer_id() != peer_id:
            self._notify_for_page("对端已切换，已取消写回", type="warning")
            return False

        cache = remote_chain_cache.read_remote_chain_cache(peer_id) or {}
        rows = list(cache.get("rows") or [])
        if not (0 <= at_idx < len(rows)):
            self._notify_for_page("本地数据已变化，请重试", type="warning")
            return False
        if not new_block:
            self._notify_for_page("没有可保存的规则", type="negative")
            return False

        rows[at_idx : at_idx + 1] = [
            dict(item)
            for item in new_block
        ]
        return await self._save_remote_rows_and_push(peer_id, rows)

    async def _save_remote_rows_and_push(
        self,
        peer_id: str,
        rows: list[dict[str, Any]],
    ) -> bool:
        """统一对端工作副本写事务：prepush 备份 → pending → SSH → 成功/回滚。"""

        cache = remote_chain_cache.read_remote_chain_cache(peer_id) or {}
        chain = str(
            cache.get("chain")
            or IptablesManager.peer_chain_name_for_id(peer_id)
        )
        chain_exists = bool(cache.get("chain_exists", True))

        try:
            remote_chain_cache.mark_pending_apply(
                peer_id,
                chain,
                chain_exists,
                rows,
            )
        except Exception as exc:
            logger.error("对端工作副本预写失败 peer=%s: %s", peer_id, exc)
            self._notify_for_page(f"本地工作副本保存失败: {exc}", type="negative")
            return False

        ok = await self._apply_remote_cache_to_peer(peer_id)
        if ok:
            self._notify_for_page("已写回对端", type="positive")
        self._refresh_remote_view()
        return ok

    def _do_edit_remote_line_form(
        self,
        dialog,
        peer_id: str,
        idx: int,
        *,
        action: str,
        protocol: str,
        source_input: str,
        dest_ip,
        dest_port,
        description,
        enabled: bool,
    ) -> None:
        """兼容旧同步调用入口。"""

        block_rows = self._build_remote_structured_block(
            peer_id,
            idx,
            action=action,
            protocol=protocol,
            source_input=source_input,
            dest_ip=dest_ip,
            dest_port=dest_port,
            description=description,
            enabled=enabled,
        )
        if block_rows is None:
            return
        self._schedule_remote_coro(
            self._do_edit_remote_block_replace_and_push(
                dialog,
                peer_id,
                idx,
                block_rows,
            )
        )

    async def _do_edit_remote_line_raw_apply(
        self,
        dialog,
        peer_id: str,
        idx: int,
        body: str,
        enabled: bool,
    ) -> bool:
        """兼容旧异步调用入口。"""

        ok = await self._save_remote_raw_edit(
            peer_id,
            idx,
            body,
            enabled,
        )
        if ok and dialog is not None:
            dialog.close()
        return ok

    async def _do_edit_remote_block_replace_and_push(
        self,
        dialog,
        peer_id: str,
        at_idx: int,
        new_block: list[dict],
    ) -> bool:
        """兼容旧异步调用入口。"""

        ok = await self._save_remote_block_replace_and_push(
            peer_id,
            at_idx,
            new_block,
        )
        if ok and dialog is not None:
            dialog.close()
        return ok

    def _confirm_delete_remote_line(self, idx: int) -> None:
        confirm_dialog.show(
            f"确定删除对端规则 #{idx + 1}？",
            impact=(
                "删除后会立即通过 SSH 重写当前对端链；"
                "若写回失败，工作副本会从 prepush 备份恢复并标记待同步。"
            ),
            object_count=1,
            danger_level="danger",
            on_confirm=lambda: self._schedule_remote_coro(
                self._do_delete_remote_line_async(idx)
            ),
        )

    async def _do_delete_remote_line_async(self, idx: int) -> None:
        if not self._require_remote_strategy_tab():
            return
        pid = self._active_remote_peer_id()
        if not pid:
            return
        c2 = remote_chain_cache.read_remote_chain_cache(pid) or {}
        cname = IptablesManager.peer_chain_name_for_id(pid)
        rws = list(c2.get("rows") or [])
        if not (0 <= idx < len(rws)):
            self._notify_for_page("本地数据已变化，请重试", type="warning")
            return
        rws.pop(idx)
        remote_chain_cache.mark_pending_apply(
            pid,
            str(c2.get("chain") or cname),
            bool(c2.get("chain_exists", True)),
            rws,
        )
        if await self._apply_remote_cache_to_peer(pid):
            self._reindex_selected_after_remote_delete(idx)
            self._notify_for_page("已写回对端", type="positive")
        self._refresh_remote_view()

    def _reindex_selected_after_remote_delete(self, deleted_idx: int) -> None:
        next_set: set[int] = set()
        for j in self._selected_remote_indices:
            if j < deleted_idx:
                next_set.add(j)
            elif j > deleted_idx:
                next_set.add(j - 1)
        self._selected_remote_indices = next_set

    def _show_create_remote_line_dialog(self) -> None:
        """使用共享规则字段新建对端规则并立即 SSH 写回。"""

        if not self._require_remote_strategy_tab():
            return
        peer_id = self._active_remote_peer_id()
        if not peer_id:
            self._notify_for_page("请先选择对端", type="warning")
            return
        peer = self._selected_remote_peer_row()
        if not peer:
            self._notify_for_page("对端不存在", type="negative")
            return
        try:
            if not peer_row_has_usable_ssh_auth(peer):
                self._notify_for_page(
                    "请先配置对端 SSH 凭据",
                    type="warning",
                )
                return
        except Exception as exc:
            logger.error("检查对端 SSH 凭据失败: %s", exc)
            self._notify_for_page("无法确认 SSH 凭据", type="negative")
            return

        groups = GroupService().list_all()
        users = UserService().list_all()
        default_group_id = (
            str(groups[0].get("id") or "")
            if groups
            else None
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
            ui.label(
                f"对端：{peer.get('name') or peer_id} · "
                f"链 {IptablesManager.peer_chain_name_for_id(peer_id)}；"
                "创建成功后立即写回。"
            ).classes("section-caption")
            holder["fields"] = render_firewall_rule_create_fields(
                groups=groups,
                users=users,
                peer_presets_loader=load_peer_presets,
                ccd_reader=_read_ccd_vpn_ips,
                parse_user_source=_parse_user_source_for_create,
                default_group_id=default_group_id,
                owner_type_label="来源类型",
                peer_lan_label="对端内网 CIDR（可选）",
                source_placeholder="可自动填组子网 / CCD，或手工填写",
                user_hint_text="用户来源可从 CCD 预填虚拟 IP。",
                dest_ip_placeholder="空=不限",
                dest_port_placeholder="空=不限",
            )

        async def submit() -> bool:
            fields = holder["fields"]
            try:
                values = fields.read_values()
                priority = self._resolve_priority(
                    values["priority_mode"],
                    values["priority_value"],
                    base=self._remote_priority_base_list(peer_id),
                )
                rests = remote_rests_from_create_fields(
                    source_subnet=values["source_subnet"],
                    source_ips=values["source_ips"],
                    action=values["action"],
                    protocol=values["protocol"],
                    dest_ip=values["dest_ip"],
                    dest_port=values["dest_port"],
                )
            except (ValueError, ValidationError, TypeError) as exc:
                self._notify_for_page(str(exc), type="negative")
                return False

            description = str(
                values.get("description") or ""
            ).strip() or None
            new_rows = [
                {
                    "rest": line,
                    "enabled": bool(values["enabled"]),
                    "priority": priority,
                    **(
                        {"description": description}
                        if description
                        else {}
                    ),
                }
                for line in rests
            ]
            return await self._insert_remote_rows_and_push(
                peer_id,
                new_rows,
                priority,
            )

        form = create_form_dialog(
            "新建对端规则",
            body=body,
            on_submit=submit,
            submit_label="创建并写回",
            close_on_success=True,
        )
        form.dialog.props("position=right maximized")
        form.card.classes(
            "w-[min(100vw,30rem)] h-full no-wrap overflow-y-auto"
        )
        form.open()

    def _switch_owner(self, owner_id: str) -> None:
        """兼容旧跳转入口：同步归属筛选并刷新 Grid。"""

        normalized = str(owner_id or "").strip()
        self.current_owner_id = normalized
        if self._center_owner_filter is not None:
            self._center_owner_filter.set_value(normalized or "")
        self._refresh_rules()

    @staticmethod
    def _center_grid_columns(*, can_reorder: bool = False) -> list[dict[str, Any]]:
        """中心策略核心列定义。"""

        return [
            {
                "field": "priority",
                "headerName": "优先级",
                "width": 105,
                "pinned": "left",
                "checkboxSelection": True,
                "headerCheckboxSelection": True,
                "rowDrag": bool(can_reorder),
            },
            {
                "field": "status_label",
                "headerName": "状态",
                "width": 100,
            },
            {
                "field": "owner_label",
                "headerName": "归属",
                "minWidth": 160,
                "flex": 1,
            },
            {
                "field": "kind_label",
                "headerName": "部署",
                "minWidth": 135,
            },
            {
                "field": "protocol",
                "headerName": "协议",
                "width": 95,
            },
            {
                "field": "action_label",
                "headerName": "动作",
                "width": 105,
            },
            {
                "field": "source",
                "headerName": "来源",
                "minWidth": 180,
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
                "minWidth": 110,
            },
            {
                "field": "description",
                "headerName": "说明",
                "minWidth": 220,
                "flex": 1,
            },
            {
                "field": "id",
                "headerName": "规则 ID",
                "minWidth": 210,
            },
            {
                "field": "actions",
                "headerName": "操作",
                "width": 100,
                "sortable": False,
                "filter": False,
                "pinned": "right",
            },
        ]

    def _load_center_grid_rows(self) -> list[dict[str, Any]]:
        """从 service 获取统一中心规则并转换为稳定 Grid 行。"""

        rules = self.rule_service.list_unified_flat()
        group_names = {
            str(group.get("id") or ""): str(group.get("name") or "")
            for group in GroupService().list_all()
            if str(group.get("id") or "").strip()
        }
        peer_names = {
            str(peer.get("id") or ""): str(peer.get("name") or "")
            for peer in self._peer_service.list_all()
            if str(peer.get("id") or "").strip()
        }
        rows = adapt_center_rule_rows(
            rules,
            group_names=group_names,
            peer_names=peer_names,
        )
        self._center_raw_by_id = {
            str(row["id"]): dict(row.get("raw") or {})
            for row in rows
        }
        return rows

    def _sync_center_owner_options(self, rows: list[dict[str, Any]]) -> None:
        """根据当前数据维护归属筛选选项。"""

        if self._center_owner_filter is None:
            return
        options: dict[str, str] = {"": "全部归属"}
        for row in rows:
            owner_id = str(row.get("owner_id") or "").strip()
            if not owner_id:
                continue
            owner_type = str(row.get("owner_type") or "").strip()
            label = str(row.get("owner_label") or owner_id)
            prefix = {
                "group": "组",
                "user": "用户",
                "peer": "对端",
            }.get(owner_type, "归属")
            options.setdefault(owner_id, f"{prefix} · {label}")

        current = str(self._center_owner_filter.value or "").strip()
        if current and current not in options:
            current = ""
            self.current_owner_id = ""
        self._center_owner_filter.set_options(options, value=current)

    def _filter_center_rows(
        self,
        rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """应用关键词和归属筛选。"""

        query = (
            str(self._center_search_input.value or "").strip().casefold()
            if self._center_search_input is not None
            else ""
        )
        owner_id = (
            str(self._center_owner_filter.value or "").strip()
            if self._center_owner_filter is not None
            else ""
        )
        self.current_owner_id = owner_id
        self._is_unified_list = not bool(owner_id)

        filtered = rows
        if owner_id:
            filtered = [
                row
                for row in filtered
                if str(row.get("owner_id") or "") == owner_id
            ]
        if query:
            filtered = [
                row
                for row in filtered
                if query in str(row.get("search_text") or "")
            ]
        return filtered

    def _refresh_rules(self) -> None:
        """刷新中心策略 AG Grid，区分空数据、无匹配和读取失败。"""

        if self._center_grid_container is None:
            return
        if not self._center_tab_active:
            return

        try:
            all_rows = self._load_center_grid_rows()
        except Exception as exc:
            logger.exception("加载中心防火墙规则失败")
            self._center_all_rows = []
            self._center_display_rows = []
            self._center_grid_view = None
            self._center_grid_container.clear()
            if self._rules_total_footer is not None:
                self._rules_total_footer.set_text("LOAD FAILED")
            with self._center_grid_container:
                error_state(
                    str(exc),
                    title="中心规则加载失败",
                    on_retry=self._refresh_rules,
                )
            return

        self._center_all_rows = all_rows
        self._sync_center_owner_options(all_rows)
        rows = self._filter_center_rows(all_rows)
        self._center_display_rows = rows
        query = (
            str(self._center_search_input.value or "").strip()
            if self._center_search_input is not None
            else ""
        )
        owner_filter = (
            str(self._center_owner_filter.value or "").strip()
            if self._center_owner_filter is not None
            else ""
        )
        self._center_reorder_allowed = not bool(query or owner_filter)
        for row in rows:
            row["actions"] = "操作"

        visible_ids = [str(row.get("id") or "") for row in rows if str(row.get("id") or "")]
        self._visible_center_rule_ids = visible_ids
        self.selected_rule_ids.intersection_update(set(visible_ids))

        if self._rules_total_footer is not None:
            if len(rows) == len(all_rows):
                self._rules_total_footer.set_text(f"{len(all_rows)} RULES TOTAL")
            else:
                self._rules_total_footer.set_text(
                    f"{len(rows)} MATCHED / {len(all_rows)} TOTAL"
                )

        self._center_grid_container.clear()
        self._center_grid_view = None
        with self._center_grid_container:
            if not all_rows:
                empty_state(
                    "暂无中心规则",
                    description="新建规则或从备份导入后会显示在这里。",
                    action_label="新建规则",
                    on_action=self._show_create_dialog,
                    icon="shield",
                )
                return
            if not rows:
                no_match_state(
                    description="当前关键词或归属筛选没有匹配规则。",
                    on_clear=self._clear_center_filters,
                )
                return

            self._center_grid_view = render_data_grid(
                self._center_grid_columns(can_reorder=self._center_reorder_allowed),
                rows,
                row_id="id",
                page_size=50,
                selectable=True,
                selected_ids=self.selected_rule_ids,
                on_cell_click=self._on_center_grid_cell_click,
            )
            self._center_grid_view.grid.on(
                "selectionChanged",
                self._on_center_grid_selection_changed,
            )
            if self._center_reorder_allowed:
                self._center_grid_view.grid.options["rowDragManaged"] = True
                self._center_grid_view.grid.options["suppressMoveWhenRowDragging"] = False
                self._center_grid_view.grid.on(
                    "rowDragEnd",
                    self._on_center_grid_row_drag_end,
                )
                self._center_grid_view.grid.update()

    def _clear_center_filters(self) -> None:
        if self._center_search_input is not None:
            self._center_search_input.set_value("")
        if self._center_owner_filter is not None:
            self._center_owner_filter.set_value("")
        self.current_owner_id = ""
        self._refresh_rules()

    async def _on_center_grid_selection_changed(self, *_args) -> None:
        """同步 AG Grid 选中行到现有批量操作集合。"""

        if self._center_grid_view is None:
            self.selected_rule_ids.clear()
            return
        selected = await self._center_grid_view.get_selected_rows()
        self.selected_rule_ids = {
            str(row.get("id") or "").strip()
            for row in selected
            if str(row.get("id") or "").strip()
        }

    async def _on_center_grid_cell_click(self, event) -> None:
        """复制结构化字段；点击“操作”列打开单项操作。"""

        args = getattr(event, "args", None) or {}
        if not isinstance(args, dict):
            return
        data = args.get("data") or {}
        if not isinstance(data, dict):
            return
        col_id = str(args.get("colId") or "")
        rule_id = str(data.get("id") or "").strip()

        if col_id == "actions" and rule_id:
            self._show_center_rule_actions(rule_id)
            return

        value = ""
        if col_id == "id":
            value = rule_id
        elif col_id == "owner_label":
            value = str(data.get("owner_id") or data.get("owner_label") or "")
        elif col_id in {"source", "dest", "dest_port"}:
            value = str(data.get(col_id) or "")
        if value and value not in {"—", "*"}:
            await self._copy_text_to_clipboard(value)

    def _show_center_rule_actions(self, rule_id: str) -> None:
        """打开单条中心规则操作面板。"""

        raw = self._center_raw_by_id.get(str(rule_id))
        if not raw:
            self._notify_for_page("规则已变化，请刷新后重试", type="warning")
            return
        enabled = bool(raw.get("enabled", True))
        is_center_peer = (
            str(rule_id).startswith(CE_PEER_RULE_PREFIX)
            or raw.get("_row_kind") == "center_peer"
        )

        with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
            ui.label("规则操作").classes("text-h6")
            ui.label(str(rule_id)).classes("font-mono text-caption break-all")
            if is_center_peer:
                ui.label(
                    "这是对端在中心 VPN_FORWARD 的虚拟放行行；删除会清空该对端 LAN。"
                ).classes("section-caption")

            with ui.column().classes("w-full gap-2 q-mt-sm"):
                secondary_action(
                    "编辑",
                    icon="edit",
                    on_click=lambda: (
                        dialog.close(),
                        self._show_edit_dialog(dict(raw)),
                    ),
                )
                secondary_action(
                    "停用" if enabled else "启用",
                    icon="pause_circle" if enabled else "play_circle",
                    on_click=lambda: self._toggle_center_rule_enabled(
                        str(rule_id),
                        not enabled,
                        dialog=dialog,
                    ),
                )
                secondary_action(
                    "复制规则 JSON",
                    icon="content_copy",
                    on_click=lambda: self._schedule_center_rule_copy(dict(raw)),
                )
                danger_action(
                    "删除",
                    icon="delete",
                    on_click=lambda: (
                        dialog.close(),
                        self._confirm_delete(str(rule_id)),
                    ),
                )
            secondary_action("关闭", on_click=dialog.close)

        dialog.open()

    def _schedule_center_rule_copy(self, raw: dict[str, Any]) -> None:
        """复制单条规则 JSON；从同步按钮安全调度异步剪贴板。"""

        try:
            background_tasks.create(
                self._copy_text_to_clipboard(
                    json.dumps(raw, ensure_ascii=False, indent=2)
                ),
                name="firewall-copy-rule",
            )
        except RuntimeError as exc:
            logger.error("调度规则复制失败: %s", exc)
            self._notify_for_page("复制任务未能启动", type="negative")

    def _toggle_center_rule_enabled(
        self,
        rule_id: str,
        enabled: bool,
        *,
        dialog=None,
    ) -> bool:
        """单条规则启停并刷新。"""

        try:
            self.rule_service.set_enabled(rule_id, enabled)
        except Exception as exc:
            logger.error("切换中心规则状态失败 rule=%s: %s", rule_id, exc)
            self._notify_for_page(str(exc), type="negative")
            return False
        if dialog is not None:
            dialog.close()
        self._notify_for_page("已启用" if enabled else "已停用", type="positive")
        self._refresh_rules()
        return True

    def _on_center_grid_row_drag_end(self, event) -> None:
        """AG Grid 拖动结束后持久化全局优先级顺序。"""

        if not self._center_reorder_allowed:
            self._notify_for_page("搜索或归属筛选状态下不能调整优先级", type="warning")
            self._refresh_rules()
            return

        args = getattr(event, "args", None) or {}
        if not isinstance(args, dict):
            self._refresh_rules()
            return
        data = args.get("data")
        if not isinstance(data, dict):
            node = args.get("node")
            data = node.get("data") if isinstance(node, dict) else None
        rule_id = str((data or {}).get("id") or "").strip()
        if not rule_id or rule_id not in self._visible_center_rule_ids:
            self._refresh_rules()
            return

        old_index = self._visible_center_rule_ids.index(rule_id)
        try:
            new_index = int(args.get("overIndex", args.get("rowIndex", old_index)))
        except (TypeError, ValueError):
            new_index = old_index
        if new_index < 0:
            new_index = 0
        if new_index >= len(self._visible_center_rule_ids):
            new_index = len(self._visible_center_rule_ids) - 1

        new_order = self._move_order(
            self._visible_center_rule_ids,
            old_index,
            new_index,
        )
        if new_order and new_order != self._visible_center_rule_ids:
            self._handle_reorder(new_order)
        else:
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
        """第二行只放注释/说明：有则显，与主标题同文不重复。中心/对端/``center_peer`` 共用。"""
        if is_center_peer:
            u = str(data.get("description") or "").strip()
            tag = str(data.get("_center_tag_line") or "").strip()
            if u and tag:
                return f"{u} | {tag}"
            return u or tag
        if is_remote_row:
            a = str(data.get("description") or "").strip()
            b = str(iptables_comment or "").strip()
            return a or b
        t = (title_text or "").strip()
        c = str(data.get("comment") or "").strip()
        if c:
            return c
        d = str(data.get("description") or "").strip()
        if d and d != t:
            return d
        return ""

    @staticmethod
    def _render_empty(icon: str, title: str, copy: str) -> None:
        with ui.element("div").classes("mgmt-dashed-empty"):
            with ui.element("div").classes("mgmt-dashed-empty-badge"):
                ui.icon(icon).classes("text-4xl")
            ui.label(title).classes("mgmt-dashed-empty-title")
            ui.label(copy).classes("mgmt-dashed-empty-copy")

    def _render_rule_card(self, rule: dict, show_owner: bool = False) -> None:
        rid = rule["id"]
        is_center_peer = str(rid).startswith(CE_PEER_RULE_PREFIX) or rule.get("_row_kind") == "center_peer"
        peer_name = ""
        if is_center_peer:
            peer_id = str(rule.get("_peer_id") or "").strip()
            if not peer_id and str(rid).startswith(CE_PEER_RULE_PREFIX):
                peer_id = str(rid)[len(CE_PEER_RULE_PREFIX) :]
            title_text = f"{peer_id[:8]}…" if len(peer_id) > 8 else (peer_id or str(rid))
            prow = self._peer_service.get(peer_id) if peer_id else None
            peer_name = str((prow or {}).get("name") or "").strip()
        else:
            srid = str(rid)
            # 本地中心 JSON：主标题用规则 id；说明走第二行
            title_text = srid[:8] if len(srid) >= 8 else srid
        second_line = FirewallPage._second_line_comment_text(
            rule, title_text=title_text, is_center_peer=is_center_peer, is_remote_row=False
        )
        enabled = rule.get("enabled", True)
        protocol = (rule.get("protocol") or "all").upper()
        if protocol == "ANY":
            protocol = "ALL"
        action = (rule.get("action") or "accept").upper()
        owner_tag = str(rule.get("_owner_id", "") or rule.get("owner_id", ""))
        source = str(rule.get("source_subnet") or "")
        if not source and rule.get("source_ips"):
            source = ",".join(rule["source_ips"])
        source = source or "*"
        dest = str(rule.get("dest_ip") or "*")
        dest_port = str(rule.get("dest_port") or "*")
        flow_text = f"{source} → {dest}:{dest_port}"
        with ui.element("div").classes("firewall-rule-card" + ("" if enabled else " opacity-60")).props(
            f'data-rule-id="{rid}"'
        ):
            with ui.element("div").classes("firewall-rule-card-head"):
                with ui.row().classes("items-center gap-2 flex-1 min-w-0"):
                    self._center_rule_checkboxes[str(rid)] = ui.checkbox(
                        value=rid in self.selected_rule_ids,
                        on_change=lambda e, r=rid: self._toggle_selection(r, e.value),
                    ).classes("mgmt-checkbox")
                    if not is_center_peer:
                        ui.icon("drag_indicator").classes("firewall-rule-drag cursor-move text-grey-6")
                    else:
                        ui.icon("drag_indicator").classes("firewall-rule-drag cursor-move text-grey-5")
                    with ui.element("div").classes("mgmt-record-copy"):
                        with ui.row().classes("items-center gap-2 flex-wrap min-w-0"):
                            ui.label(title_text).classes("mgmt-record-title")
                            # 与 t 样例一致：区分为本机中心 JSON / 对端仅标识 / 对端在中心侧放行
                            if is_center_peer:
                                ui.label("对端·中心放行").classes("user-session-badge")
                            elif (rule.get("deployment_target") or "center") == "peer":
                                ui.label("对端仅标识").classes("user-session-badge")
                            else:
                                ui.label("中心节点").classes("user-session-badge")
                            if is_center_peer and peer_name:
                                ui.label(peer_name).classes("user-session-badge shrink-0")
                        if second_line:
                            with ui.row().classes("w-full min-w-0"):
                                ui.label(second_line).classes("text-caption text-grey-6 line-clamp-1")
                        with ui.element("div").classes("mgmt-record-meta"):
                            if show_owner and (owner_tag or "").strip():
                                ot = (owner_tag or "").strip()
                                with ui.row().classes("items-center gap-1 flex-wrap min-w-0"):
                                    ui.label("归属").classes("text-grey-6 text-caption shrink-0")
                                    o = (
                                        ui.label(ot)
                                        .classes("meta-badge cursor-pointer min-w-0 break-all")
                                        .tooltip("点击复制")
                                    )
                                    o.on("click", lambda t=ot: self._copy_text_to_clipboard(t))
                            for lab, val in [
                                ("协议", protocol),
                                ("动作", action),
                                ("优先级", str(rule.get("priority", 0))),
                            ]:
                                with ui.element("span").classes("mgmt-meta-item"):
                                    ui.label(f"{lab} {val}")
                            with ui.element("span").classes("mgmt-meta-item mgmt-meta-flow"):
                                ui.label(flow_text)
                with ui.element("div").classes("mgmt-actions").classes("row items-center gap-0"):

                    def _sync_enabled(v: bool) -> None:
                        try:
                            self.rule_service.set_enabled(rid, v)
                            logger.info("firewall 规则启用状态已更新 rule_id=%s enabled=%s", rid, v)
                        except Exception as exc:
                            self._notify_for_page(str(exc), type="negative")
                        self._refresh_rules()

                    ui.switch(value=enabled, on_change=lambda e: _sync_enabled(e.value)).props("dense")
                    if is_center_peer:
                        ui.button(
                            icon="edit",
                            on_click=lambda r=rule: self._show_edit_dialog(r),
                        ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn").tooltip("编辑")
                    else:
                        ui.button(
                            icon="edit",
                            on_click=lambda r=rule: self._show_edit_dialog(r),
                        ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn").tooltip("编辑")
                    ui.button(
                        icon="delete",
                        on_click=lambda i=rid: self._confirm_delete(i),
                    ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn is-delete").tooltip("删除")

    def _handle_reorder(self, new_id_order: list[str]) -> None:
        try:
            oid = "" if self._is_unified_list else (self.current_owner_id or "").strip()
            if not new_id_order:
                return
            self.rule_service.reorder(oid, new_id_order)
            self._notify_for_page("规则顺序已更新", type="positive")
            self._refresh_rules()
        except Exception as exc:
            logger.error("reorder: %s", exc)
            self._notify_for_page(f"排序失败: {exc}", type="negative")
            self._refresh_rules()

    def _on_center_sort_end(self, event) -> None:
        """处理 NiceGUI SortableJS 中心规则拖拽结果。"""

        new_order = self._move_order(
            self._visible_center_rule_ids,
            event.old_index,
            event.new_index,
        )
        if new_order:
            self._handle_reorder(new_order)

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
            "w-[min(100vw,30rem)] h-full no-wrap overflow-y-auto"
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

    def _remote_priority_base_list(self, peer_id: str) -> list[dict]:
        """供与中心相同的优先级选择：按当前对端工作副本行推算 priority 集合。"""
        cache = remote_chain_cache.read_remote_chain_cache(peer_id) or {}
        rws = list(cache.get("rows") or [])
        out: list[dict] = []
        for i, r in enumerate(rws):
            p = int(r.get("priority", 0) or 0) or (i + 1) * 10
            out.append({"id": f"r{i}", "priority": p})
        return out

    async def _insert_remote_rows_and_push(
        self,
        peer_id: str,
        new_rows: list[dict],
        priority: int,
    ) -> bool:
        """按优先级插入工作副本并立即写回；失败由缓存 prepush 语义回滚。"""

        if not self._require_remote_strategy_tab():
            return False
        if self._active_remote_peer_id() != peer_id:
            self._notify_for_page(
                "对端已切换，已取消写回",
                type="warning",
            )
            return False

        cache = remote_chain_cache.read_remote_chain_cache(peer_id) or {}
        chain = str(
            cache.get("chain")
            or IptablesManager.peer_chain_name_for_id(peer_id)
        )
        rows = list(cache.get("rows") or [])

        def row_priority(index: int) -> int:
            row = rows[index]
            return int(row.get("priority", 0) or 0) or (
                (index + 1) * 10
            )

        insert_at = len(rows)
        for index in range(len(rows)):
            if row_priority(index) > int(priority):
                insert_at = index
                break

        for offset, new_row in enumerate(new_rows):
            rows.insert(insert_at + offset, dict(new_row))

        remote_chain_cache.mark_pending_apply(
            peer_id,
            chain,
            bool(cache.get("chain_exists", True)),
            rows,
        )
        ok = await self._apply_remote_cache_to_peer(peer_id)
        if not ok:
            self._refresh_remote_view()
            return False

        logger.info(
            "对端链已插入新建行 peer=%s n=%s pri=%s",
            peer_id,
            len(new_rows),
            priority,
        )
        self._notify_for_page("已创建并写回对端", type="positive")
        self._refresh_remote_view()
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
                ui.label(
                    f"优先级：{int(rule.get('priority') or 0)}；"
                    "优先级请在列表拖动调整。"
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
            "w-[min(100vw,30rem)] h-full no-wrap overflow-y-auto"
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

    def _prepare_remote_import(
        self,
        text: str,
        *,
        peer_id: str | None = None,
    ) -> dict[str, Any]:
        """预检查远端备份 / iptables 粘贴 / 混合文本，不产生写入。"""

        target = str(peer_id or self._active_remote_peer_id() or "").strip()
        if not target:
            raise ValueError("请先选择对端")
        normalized = str(text or "").strip().lstrip("\ufeff")
        if not normalized:
            raise ValueError("无内容")

        chain = IptablesManager.peer_chain_name_for_id(target)
        rows, parsed_chain, chain_exists = (
            FirewallPage._rows_from_remote_import_text_mixed(
                normalized,
                target,
                chain,
            )
        )
        if normalized.startswith("{"):
            kind = "backup"
        elif any(
            line.strip().startswith("-A ")
            for line in normalized.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ):
            kind = "iptables"
        else:
            kind = "mixed"

        return {
            "peer_id": target,
            "kind": kind,
            "rows": [dict(row) for row in rows],
            "chain": parsed_chain,
            "chain_exists": bool(chain_exists),
            "count": len(rows),
            "text": normalized,
        }

    async def _execute_remote_import(
        self,
        form,
        status_label,
        prepared: dict[str, Any],
    ) -> bool:
        """写入已预检查的远端工作副本；失败保留表单与输入。"""

        peer_id = str(prepared.get("peer_id") or "").strip()
        if not peer_id or self._active_remote_peer_id() != peer_id:
            self._notify_for_page(
                "对端已切换，已取消导入",
                type="warning",
            )
            return False
        if self._remote_push_running_peer is not None:
            self._notify_for_page(
                "已有对端写回正在执行，请勿重复提交",
                type="warning",
            )
            return False

        rows = [
            dict(row)
            for row in (prepared.get("rows") or [])
        ]
        chain = str(
            prepared.get("chain")
            or IptablesManager.peer_chain_name_for_id(peer_id)
        )
        chain_exists = bool(prepared.get("chain_exists", True))
        total = len(rows)

        self._remote_push_running_peer = peer_id
        form.set_busy(True)
        status_label.text = f"导入中…准备写回 {total} 条规则"
        self._refresh_remote_toolbar_state()
        try:
            remote_chain_cache.mark_pending_apply(
                peer_id,
                chain,
                chain_exists,
                rows,
            )
            ok = await self._apply_remote_cache_to_peer(peer_id)
            if not ok:
                status_label.text = (
                    f"导入失败：成功 0，跳过 0，失败 {max(total, 1)}"
                )
                return False

            self._selected_remote_indices.clear()
            status_label.text = (
                f"导入完成：成功 {total}，跳过 0，失败 0"
            )
            self._notify_for_page(
                status_label.text,
                type="positive",
            )
            form.close()
            return True
        finally:
            if self._remote_push_running_peer == peer_id:
                self._remote_push_running_peer = None
            form.set_busy(False)
            self._refresh_remote_toolbar_state()
            self._refresh_remote_view()

    def _show_remote_import_dialog(self) -> None:
        """统一远端导入表单：上传/粘贴、预检查、确认后一次写回。"""

        peer_id = self._active_remote_peer_id()
        if not peer_id:
            self._notify_for_page("请先选择对端", type="warning")
            return

        refs: dict[str, Any] = {}
        holder: dict[str, Any] = {}

        def body() -> None:
            ui.label(
                "支持对端副本 JSON、iptables -S 输出、"
                "每行 rest 片段与简化规则；预检查不会写入远端。"
            ).classes("section-caption")
            refs["textarea"] = (
                ui.textarea("规则内容")
                .classes("w-full")
                .props("rows=16")
            )
            refs["status"] = ui.label(
                "尚未执行预检查"
            ).classes("section-caption")

            async def on_upload(event) -> None:
                try:
                    raw = await event.file.read()
                    text = raw.decode("utf-8", errors="replace")
                    refs["textarea"].set_value(text)
                    name = str(
                        getattr(event.file, "name", "") or ""
                    ).strip()
                    refs["status"].text = f"已载入 {name}，等待预检查"
                except Exception as exc:
                    logger.exception("远端规则导入读文件失败")
                    refs["status"].text = f"读取失败：{exc}"
                    self._notify_for_page(
                        f"读文件失败: {exc}",
                        type="negative",
                    )

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
                prepared = self._prepare_remote_import(
                    str(refs["textarea"].value or ""),
                    peer_id=peer_id,
                )
            except ValueError as exc:
                refs["status"].text = f"预检查失败：{exc}"
                self._notify_for_page(str(exc), type="negative")
                return False

            count = int(prepared.get("count") or 0)
            kind_label = {
                "backup": "备份 JSON",
                "iptables": "iptables 输出",
                "mixed": "混合/简化文本",
            }.get(str(prepared.get("kind") or ""), "规则文本")
            refs["status"].text = (
                f"预检查通过：{kind_label}，共 {count} 条"
            )
            confirm_dialog.show(
                f"用预检查结果覆盖当前对端链（{count} 条）？",
                impact=(
                    "会覆盖本地工作副本并立即通过 SSH 重写对端链；"
                    "失败时按 prepush 备份恢复，输入内容保留。"
                ),
                object_count=count,
                danger_level="danger",
                on_confirm=lambda: self._schedule_remote_coro(
                    self._execute_remote_import(
                        holder["form"],
                        refs["status"],
                        prepared,
                    )
                ),
            )
            return False

        form = create_form_dialog(
            "导入对端规则",
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

    async def _import_ok_remote_async(self, dialog, text: str) -> None:
        self._import_source_file_mode = None
        self._import_last_uploaded_text = None
        if not self._require_remote_strategy_tab():
            return
        pid = self._active_remote_peer_id()
        if not pid:
            self._notify_for_page("请先选择对端", type="warning")
            return
        cname = IptablesManager.peer_chain_name_for_id(pid)
        try:
            rows, chn, cex = FirewallPage._rows_from_remote_import_text_mixed(text, pid, cname)
        except ValueError as exc:
            logger.error("对端规则导入解析失败: %s", exc)
            self._notify_for_page(str(exc), type="negative")
            return
        remote_chain_cache.mark_pending_apply(pid, chn, cex, rows)
        if await self._apply_remote_cache_to_peer(pid):
            self._selected_remote_indices.clear()
            dialog.close()
            self._notify_for_page("已导入并写回", type="positive")
        self._refresh_remote_view()

    def _toggle_selection(self, rule_id: str, checked: bool) -> None:
        if checked:
            self.selected_rule_ids.add(rule_id)
        else:
            self.selected_rule_ids.discard(rule_id)
        self._sync_center_select_all()

    def _toggle_center_select_all(self, checked: bool) -> None:
        """选择或取消选择当前中心规则列表。"""
        update_visible_selection(
            self.selected_rule_ids,
            self._visible_center_rule_ids,
            checked,
        )
        apply_row_checkboxes(self._center_rule_checkboxes, self.selected_rule_ids)
        self._sync_center_select_all()

    def _sync_center_select_all(self) -> None:
        """同步中心规则列表全选复选框。"""
        if self._center_select_all_checkbox is not None:
            sync_select_all_checkbox(
                self._center_select_all_checkbox,
                self._visible_center_rule_ids,
                self.selected_rule_ids,
            )

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

    async def _do_batch_remote_delete_async(self) -> bool:
        if not self._require_remote_strategy_tab():
            return False
        peer_id = self._active_remote_peer_id()
        if not peer_id:
            self._notify_for_page("请先选择对端", type="warning")
            return False
        if self._remote_push_running_peer is not None:
            self._notify_for_page(
                "已有对端写回正在执行，请勿重复提交",
                type="warning",
            )
            return False

        cache = remote_chain_cache.read_remote_chain_cache(peer_id) or {}
        rows = list(cache.get("rows") or [])
        selected = sorted(self._selected_remote_indices)
        valid = [index for index in selected if 0 <= index < len(rows)]
        skipped = len(selected) - len(valid)
        if not valid:
            self._notify_for_page("未勾选有效规则", type="warning")
            return False

        for index in sorted(valid, reverse=True):
            rows.pop(index)

        chain = str(
            cache.get("chain")
            or IptablesManager.peer_chain_name_for_id(peer_id)
        )
        self._remote_push_running_peer = peer_id
        self._refresh_remote_toolbar_state()
        try:
            remote_chain_cache.mark_pending_apply(
                peer_id,
                chain,
                bool(cache.get("chain_exists", True)),
                rows,
            )
            ok = await self._apply_remote_cache_to_peer(peer_id)
            if ok:
                self._selected_remote_indices.clear()
                self._notify_for_page(
                    f"批量删除完成：成功 {len(valid)}，"
                    f"跳过 {skipped}，失败 0",
                    type="positive",
                )
                return True

            self._notify_for_page(
                f"批量删除失败：成功 0，跳过 {skipped}，"
                f"失败 {len(valid)}",
                type="negative",
            )
            return False
        finally:
            if self._remote_push_running_peer == peer_id:
                self._remote_push_running_peer = None
            self._refresh_remote_toolbar_state()
            self._refresh_remote_view()

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

    async def _do_batch_remote_set_enabled_async(self, en: bool) -> bool:
        if not self._require_remote_strategy_tab():
            return False
        peer_id = self._active_remote_peer_id()
        if not peer_id:
            return False
        if self._remote_push_running_peer is not None:
            self._notify_for_page(
                "已有对端写回正在执行，请勿重复提交",
                type="warning",
            )
            return False

        cache = remote_chain_cache.read_remote_chain_cache(peer_id) or {}
        rows = list(cache.get("rows") or [])
        selected = sorted(self._selected_remote_indices)
        valid = [index for index in selected if 0 <= index < len(rows)]
        invalid_count = len(selected) - len(valid)
        if not valid:
            self._notify_for_page("未勾选有效规则", type="warning")
            return False

        changed = 0
        already = 0
        for index in valid:
            if bool(rows[index].get("enabled", True)) == bool(en):
                already += 1
                continue
            rows[index]["enabled"] = bool(en)
            changed += 1

        skipped = invalid_count + already
        label = "启用" if en else "停用"
        if changed == 0:
            self._selected_remote_indices.clear()
            self._notify_for_page(
                f"批量{label}完成：成功 0，跳过 {skipped}，失败 0",
                type="positive",
            )
            self._refresh_remote_view()
            return True

        chain = str(
            cache.get("chain")
            or IptablesManager.peer_chain_name_for_id(peer_id)
        )
        self._remote_push_running_peer = peer_id
        self._refresh_remote_toolbar_state()
        try:
            remote_chain_cache.mark_pending_apply(
                peer_id,
                chain,
                bool(cache.get("chain_exists", True)),
                rows,
            )
            ok = await self._apply_remote_cache_to_peer(peer_id)
            if ok:
                self._selected_remote_indices.clear()
                self._notify_for_page(
                    f"批量{label}完成：成功 {changed}，"
                    f"跳过 {skipped}，失败 0",
                    type="positive",
                )
                return True

            self._notify_for_page(
                f"批量{label}失败：成功 0，跳过 {skipped}，"
                f"失败 {changed}",
                type="negative",
            )
            return False
        finally:
            if self._remote_push_running_peer == peer_id:
                self._remote_push_running_peer = None
            self._refresh_remote_toolbar_state()
            self._refresh_remote_view()
