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
from app.services.peer_instance.service import PeerService
from app.services.user.crud import UserService
from app.ui.components import confirm_dialog
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
    """从对端机链一行的 rest 中抽取常用字段，用于与中心卡同构展示；非完整解析。"""
    t = (rest or "").strip()
    out: dict[str, str] = {}
    m = re.search(r"-p\s+(!?\S+)", t, re.IGNORECASE)
    if m:
        out["proto"] = m.group(1).upper()
    m = re.search(r"-j\s+(\S+)", t, re.IGNORECASE)
    if m:
        out["action"] = m.group(1).upper()
    m = re.search(r"-s\s+(\S+)", t)
    if m:
        out["source"] = m.group(1)
    m = re.search(r"-d\s+(\S+)", t)
    if m:
        out["dest"] = m.group(1)
    m = re.search(r"--dport(?:s)?\s+(!?\S+)", t, re.IGNORECASE)
    if m:
        out["dport"] = m.group(1)
    m = re.search(r"-i\s+(\S+)", t)
    if m:
        out["in_if"] = m.group(1)
    m = re.search(r"-o\s+(\S+)", t)
    if m:
        out["out_if"] = m.group(1)
    m = re.search(r'--comment\s+"((?:\\.|[^"\\])*)"', t)
    if not m:
        m = re.search(r"--comment\s+(\S+)", t)
    if m:
        c = m.group(1).replace("\\'", "'")
        out["comment"] = c if len(c) <= 120 else c[:117] + "…"
    return out


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
    """把缓存里 ISO 时间（存 UTC，如 ``…Z``）格式化为**上海/东八区**本地时间；无或解析失败则 — / 回退截断。"""
    if not raw or not str(raw).strip():
        return "—"
    s = str(raw).strip()
    try:
        iso = s[:-1] + "+00:00" if s.endswith("Z") else s
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_TZ_UTC_PLUS_8).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, OSError):
        pass
    if "T" in s:
        s = s.replace("T", " ", 1)
    if len(s) > 10 and s[10] == " " and len(s) >= 19:
        return s[:19]
    return s[:32]


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
        self._remote_fetch_generation = 0
        self._remote_fetch_running_peer: str | None = None

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

    def _run_javascript_in_page_client(self, code: str) -> None:
        """不依赖协程 slot 栈执行 JS（与 ``ui.run_javascript`` 同效，用显式 client）。"""
        c = self._page_client()
        if c is not None:
            c.run_javascript(code, timeout=1.0)
            return
        ui.run_javascript(code)

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
        try:
            self._ng_client = context.client
        except RuntimeError:
            self._ng_client = None
        ui.add_head_html('<script src="/static/Sortable.min.js"></script>')

        with ui.column().classes("page-shell mgmt-page w-full h-full min-h-0 page-shell--firewall"):
            with ui.element("div").classes("mgmt-header-row"):
                with ui.element("div").classes("mgmt-header-copy"):
                    ui.label("规则管理").classes("mgmt-title")
                    ui.label("本机中心 JSON + 对端 iptables 链，分两个 Tab。").classes("mgmt-desc")

            # 顶栏与 script/py/vpn/t.html 的 .control-header-row / .compact-tabs / .btn-group 同一套
            with ui.element("div").classes("firewall-control-header firewall-tab-bar"):
                with ui.tabs(value="center", on_change=self._on_strat_change).classes(
                    "firewall-compact-tabs"
                ) as self._strat_tabs:
                    ui.tab("center", label="中心策略")
                    ui.tab("remote", label="对端远端")
                with ui.element("div").classes("firewall-btn-group"):
                    # 与组管理条一致：仅「新建」主色，其余 outline
                    ui.button("批量启用", on_click=self._batch_enable).props(
                        "outline dense no-caps no-ripple"
                    ).classes("mgmt-toolbar-btn is-outline")
                    ui.button("批量停用", on_click=self._batch_disable).props(
                        "outline dense no-caps no-ripple"
                    ).classes("mgmt-toolbar-btn is-outline")
                    ui.button("批量删除", on_click=self._batch_delete).props(
                        "outline dense no-caps no-ripple"
                    ).classes("mgmt-toolbar-btn is-outline is-danger")
                    ui.button("导入", on_click=self._show_import_dialog).props(
                        "outline dense no-caps no-ripple"
                    ).classes("mgmt-toolbar-btn is-outline mgmt-toolbar-cjk-2")
                    ui.button("导出", on_click=self._export_backup).props(
                        "outline dense no-caps no-ripple"
                    ).classes("mgmt-toolbar-btn is-outline mgmt-toolbar-cjk-2")
                    ui.button("新建规则", on_click=self._on_new_rule_click).props(
                        "unelevated dense no-caps no-ripple"
                    ).classes("mgmt-toolbar-btn is-primary q-ml-sm")

            with ui.tab_panels(
                self._strat_tabs, value="center", keep_alive=False
            ).classes("w-full h-full min-h-0 flex-1 firewall-tabpanels"):
                with ui.tab_panel("center").classes("h-full min-h-0 flex-1"):
                    with ui.column().classes("firewall-center-canvas w-full min-h-0 flex-1 gap-0"):
                        with ui.element("div").classes("firewall-search-row w-full flex-shrink-0"):
                            with ui.element("div").classes("w-full min-w-0"):
                                self.owner_input = ui.input(
                                    label="关键词",
                                    placeholder="归属 / 组 ID / IP，回车跳转",
                                    value=self.current_owner_id,
                                ).classes("w-full").props("outlined dense").on(
                                    "keydown.enter",
                                    lambda _: self._switch_owner(self.owner_input.value),
                                )
                        # 与组管理「分组列表」同构：带边框的 mgmt-panel-list 作为唯一伸缩列，内层再铺规则卡片
                        with ui.element("section").classes(
                            "mgmt-panel mgmt-panel-list firewall-center-shell w-full min-h-0 flex-1"
                        ):
                            self.rules_container = ui.column().classes("mgmt-stretch w-full min-h-0 flex-1")
                            with self.rules_container:
                                pass
                                
                        with ui.element("div").classes("mgmt-page-foot w-full mt-auto flex-shrink-0"):
                            with ui.row().classes("mgmt-page-footer-row w-full items-center justify-between text-grey-6"):
                                ui.label("DRAG TO CHANGE PRIORITY").classes("uppercase tracking-widest")
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
            # 对端链拖拽排序事件（同页只注册一次，与 ``firewall_reorder`` 一致）
            ui.on("firewall_remote_reorder", self._on_remote_reorder)  # type: ignore[call-overload, misc]

    def _on_strat_change(self, e) -> None:
        v = str(getattr(e, "value", None) or "center")
        self._center_tab_active = v == "center"
        logger.debug("firewall 策略页签切换为 %s", v)
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
        try:
            background_tasks.create(
                self._remote_fetch_chain_via_ssh(target, generation),
                name="firewall-remote-fetch",
            )
        except RuntimeError:
            if self._remote_fetch_running_peer == target:
                self._remote_fetch_running_peer = None
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
            self._notify_for_page(f"从对端拉取失败: {exc}", type="negative")
            return
        finally:
            if self._remote_fetch_running_peer == target:
                self._remote_fetch_running_peer = None
        if self._center_tab_active or generation != self._remote_fetch_generation:
            return
        remote_chain_cache.record_from_fetch(snap, target)
        show = target[:8] if len(target) > 8 else target
        logger.debug("对端链已拉取并落库 peer=%s", show)
        if self._active_remote_peer_id() != target:
            return
        self._refresh_remote_view()

    def _build_remote_tab_static(self) -> None:
        """渲染对端区静态壳层；有对端时默认选第一项。进入本 Tab 时由 :meth:`_on_enter_remote_tab` 先拉后显。"""
        opts = self._peer_options_for_select()
        with ui.element("div").classes("firewall-search-row w-full"):
            with ui.element("div").classes("w-full min-w-0"):
                if opts:
                    first_id = next(iter(opts))
                    self._remote_peer_select = ui.select(
                        opts,
                        label="对端节点",
                        value=first_id,
                        with_input=True,
                    ).classes("w-full").props("outlined dense")
                    self._remote_peer_select.on_value_change(
                        lambda _: self._on_remote_peer_select_change()
                    )
                else:
                    self._remote_peer_select = None
                    ui.label("无对端，请先在「对端站点」添加。").classes("text-grey")
        self._remote_list_container = ui.column().classes("firewall-remote-list-stretch w-full min-h-0 gap-0 h-full")
        with self._remote_list_container:
            pass

        with ui.element("div").classes("mgmt-page-foot w-full mt-auto flex-shrink-0"):
            with ui.row().classes("mgmt-page-footer-row w-full items-center justify-between text-grey-6"):
                ui.label("DRAG TO CHANGE PRIORITY").classes("uppercase tracking-widest")
                self._remote_rules_total_footer = ui.label("0 RULES TOTAL").classes(
                    "uppercase tracking-widest"
                )

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

    async def _on_remote_push(self) -> None:
        if not self._require_remote_strategy_tab():
            return
        pid = self._active_remote_peer_id()
        if not pid:
            self._notify_for_page("请选择对端", type="warning")
            return
        c0 = remote_chain_cache.read_remote_chain_cache(pid) or {}
        ch0 = str(c0.get("chain") or IptablesManager.peer_chain_name_for_id(pid))
        ce0 = bool(c0.get("chain_exists"))
        rw0 = list(c0.get("rows") or [])
        remote_chain_cache.mark_pending_apply(pid, ch0, ce0, rw0)
        if await self._apply_remote_cache_to_peer(pid):
            self._notify_for_page("已写回对端", type="positive")
        self._refresh_remote_view()

    async def _on_remote_row_enabled_change(self, idx: int, e) -> None:
        """对端链规则启用开关：更新本地工作副本后立即 SSH 写回对端。"""
        if not self._require_remote_strategy_tab():
            return
        pid = self._active_remote_peer_id()
        if not pid:
            self._notify_for_page("请选择对端", type="warning")
            return
        cname = IptablesManager.peer_chain_name_for_id(pid)
        c2 = remote_chain_cache.read_remote_chain_cache(pid) or {}
        rws = list(c2.get("rows") or [])
        if not (0 <= idx < len(rws)):
            logger.error("对端链行索引越界 peer=%s idx=%s n=%s", pid, idx, len(rws))
            self._notify_for_page("数据已变，请重拉或换对端。", type="warning")
            self._refresh_remote_view()
            return
        rws[idx]["enabled"] = bool(e.value)
        remote_chain_cache.mark_pending_apply(
            pid, str(c2.get("chain") or cname), bool(c2.get("chain_exists", True)), rws
        )
        if await self._apply_remote_cache_to_peer(pid):
            self._notify_for_page("已写回对端", type="positive")
        self._refresh_remote_view()

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
                out.append({"rest": r, "enabled": bool(x.get("enabled", True))})
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
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.startswith("-A "):
                if not s.startswith(prefix):
                    raise ValueError(f"行不属于当前对端链 {ch}，请只贴本链规则：{s[:120]}")
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

    def _schedule_remote_coro(self, coro) -> None:
        try:
            asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            pass

    def _refresh_remote_view(self) -> None:
        if not self._remote_list_container:
            return
        self._remote_list_container.clear()
        pid = self._active_remote_peer_id()
        with self._remote_list_container:
            if not pid:
                self._render_empty(
                    "hub",
                    "请先选对端",
                    "在上方选对端；进页会自动尝试拉取链。",
                )
                if self._remote_rules_total_footer is not None:
                    self._remote_rules_total_footer.set_text("0 RULES TOTAL")
                return
            cache = remote_chain_cache.read_remote_chain_cache(pid)
            rows = list(cache.get("rows") or []) if cache else []
            if cache and cache.get("rows") is not None:
                n = len(cache.get("rows") or [])
                self._selected_remote_indices = {i for i in self._selected_remote_indices if 0 <= i < n}
            else:
                self._selected_remote_indices.clear()
            if self._remote_rules_total_footer is not None:
                self._remote_rules_total_footer.set_text(f"{len(rows)} RULES TOTAL")
            if not cache:
                self._render_empty(
                    "cloud_off",
                    "尚无本地副本",
                    "可重进本页自动拉取，或用「导入 / 新建」后再写回。",
                )
                return
            if not rows:
                self._render_empty(
                    "shield",
                    "暂无规则",
                    "新建写回，或等拉取/导入；可拖拽排序。",
                )
                return
            peer_row = self._peer_service.get(pid) or {}
            peer_badge = str(peer_row.get("name") or "").strip() or self._peer_id_short_label(pid)
            t_pull = _format_peer_cache_timestamp(str(cache.get("last_pulled_at") or "") or None)
            t_sync = _format_peer_cache_timestamp(str(cache.get("last_pushed_at") or "") or None)
            with ui.element("div").classes("mgmt-panel mgmt-panel-flex w-full min-h-0 flex-1"):
                with ui.element("div").classes("mgmt-list-head"):
                    with ui.column().classes("gap-1"):
                        ui.label("规则顺序").classes("mgmt-section-title")
                        ui.label(f"拉取：{t_pull}  同步：{t_sync}").classes("mgmt-section-sub")
                    ui.label(f"对端: {peer_badge}").classes("meta-badge")
                list_id = "firewall-remote-rule-list"
                with ui.element("div").classes("mgmt-panel-scroll flex-1").props(f'id="{list_id}"'):
                    for i, row in enumerate(rows):
                        self._render_remote_rule_card(i, row, row_index_1based=i + 1, n_rows=len(rows))
        self._run_javascript_in_page_client(
            """
            if (typeof Sortable !== 'undefined') {
                var el = document.getElementById('firewall-remote-rule-list');
                if (el) {
                    if (el._sortable) { el._sortable.destroy(); }
                    el._sortable = new Sortable(el, {
                        animation: 150,
                        handle: '.firewall-rule-drag',
                        ghostClass: 'sortable-ghost',
                        onEnd: function() {
                            var items = el.querySelectorAll('[data-remote-row-index]');
                            var idxs = Array.from(items).map(function(n) { return n.getAttribute('data-remote-row-index'); });
                            emitEvent('firewall_remote_reorder', { indices: idxs });
                        }
                    });
                }
            }
            """
        )

    def _peer_id_short_label(self, pid: str) -> str:
        p = str(pid or "")
        if len(p) > 8:
            return f"{p[:8]}…"
        return p or "—"

    def _on_remote_reorder(self, e) -> None:
        raw = (e.args or {}).get("indices")
        if not raw or not isinstance(raw, list):
            return
        try:
            new_order = [int(x) for x in raw]
        except (TypeError, ValueError) as ex:
            logger.error("对端重排索引非法: %s", ex)
            return
        self._schedule_remote_coro(self._do_remote_reorder_async(new_order))

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
            pid, str(c2.get("chain") or cname), bool(c2.get("chain_exists", True)), reordered
        )
        self._selected_remote_indices.clear()
        if await self._apply_remote_cache_to_peer(pid):
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
                    ui.checkbox(
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
        """与 :meth:`_show_edit_dialog`（非对端·中心行）同套表单项，保存为 ``rest`` 并写回对端。"""
        if not self._require_remote_strategy_tab():
            return
        pid = self._active_remote_peer_id()
        if not pid:
            return
        er = _remote_row_to_edit_form_dict(row)
        ssub = er.get("source_subnet") or ""
        sips = er.get("source_ips")
        src_val = ssub or (",".join(sips) if sips else "")
        cnm = IptablesManager.peer_chain_name_for_id(pid)
        with ui.dialog().props("position=right maximized") as dialog, ui.card().classes(
            "w-[min(100vw,28rem)] h-full"
        ):
            ui.label("编辑对端链内规则").classes("text-h6")
            ui.label(
                f"链 {cnm}；保存后 SSH 写回。"
            ).classes("text-caption text-grey q-mb-sm")
            action_select = ui.select(
                {"accept": "放行 (ACCEPT)", "drop": "丢弃 (DROP)", "reject": "拒绝 (REJECT)"},
                label="动作",
                value=er.get("action", "accept"),
            ).classes("w-full")
            protocol_select = ui.select(
                {"all": "不限 (ALL)", "tcp": "TCP", "udp": "UDP", "icmp": "ICMP"},
                label="协议",
                value=(er.get("protocol") or "all").replace("any", "all"),
                with_input=True,
                new_value_mode="add-unique",
            ).classes("w-full")
            source_input = ui.input("源子网 CIDR 或源 IP 列表", value=src_val).classes("w-full")
            dest_ip_input = ui.input("目标地址", value=er.get("dest_ip") or "").classes("w-full")
            dest_port_input = ui.input("目标端口", value=er.get("dest_port") or "").classes("w-full")
            desc_input = ui.input("规则描述", value=er.get("description") or "").classes("w-full")
            enabled_switch = ui.switch("启用", value=er.get("enabled", True))
            rest0 = str(row.get("rest") or "")
            raw_ta = ui.textarea("原始 rest（与 ``iptables -A`` 后同形，高级）", value=rest0).classes("w-full").props(
                "rows=3"
            )
            with ui.row().classes("justify-end q-mt-md"):
                ui.button("取消", on_click=dialog.close).props("flat")

                def _save_click() -> None:
                    use_raw = (raw_ta.value or "").strip() != rest0.strip()
                    if use_raw:
                        self._schedule_remote_coro(
                            self._do_edit_remote_line_raw_apply(
                                dialog, pid, idx, (raw_ta.value or "").strip(), enabled_switch.value
                            )
                        )
                    else:
                        self._do_edit_remote_line_form(
                            dialog,
                            pid,
                            idx,
                            action=action_select.value,
                            protocol=protocol_select.value,
                            source_input=source_input.value,
                            dest_ip=dest_ip_input.value or None,
                            dest_port=dest_port_input.value or None,
                            description=desc_input.value or None,
                            enabled=enabled_switch.value,
                        )

                ui.button("保存", on_click=_save_click).props("color=primary")
        dialog.open()

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
        t = (source_input or "").strip()
        src_sub, src_ips = _parse_source_text_for_remote_save(t)
        dp = (dest_port or "").strip()
        a = str(action or "accept").lower()
        if a not in ("accept", "drop", "reject"):
            a = "accept"
        try:
            FirewallRule(
                owner_type="group",
                owner_id="_",
                action=a,  # type: ignore[arg-type]
                priority=1,
                dest_port=dp or None,
            )
        except ValidationError as exc:
            self._notify_for_page(str(exc), type="negative")
            return
        try:
            rests = remote_rests_from_create_fields(
                source_subnet=src_sub,
                source_ips=src_ips,
                action=action,
                protocol=protocol,
                dest_ip=(dest_ip or "").strip() or None,
                dest_port=dp or None,
            )
        except (ValueError, ValidationError) as exc:
            logger.error("对端编辑生成 rest: %s", exc)
            self._notify_for_page(str(exc), type="negative")
            return
        pr = 0
        c2 = remote_chain_cache.read_remote_chain_cache(peer_id) or {}
        rws0 = list(c2.get("rows") or [])
        if 0 <= idx < len(rws0):
            pr = int(rws0[idx].get("priority", 0) or 0) or (idx + 1) * 10
        desc = (description or "").strip() or None
        new_block = [
            {
                "rest": line,
                "enabled": bool(enabled),
                "priority": pr,
                **({"description": desc} if desc else {}),
            }
            for line in rests
        ]
        self._schedule_remote_coro(
            self._do_edit_remote_block_replace_and_push(dialog, peer_id, idx, new_block)
        )

    async def _do_edit_remote_line_raw_apply(
        self, dialog, peer_id: str, idx: int, body: str, enabled: bool
    ) -> None:
        if not self._require_remote_strategy_tab() or not body or "\n" in body or "\r" in body:
            if body and ("\n" in body or "\r" in body):
                self._notify_for_page("单条规则不能含换行", type="negative")
            return
        c2 = remote_chain_cache.read_remote_chain_cache(peer_id) or {}
        cname = IptablesManager.peer_chain_name_for_id(peer_id)
        rws = list(c2.get("rows") or [])
        if not (0 <= idx < len(rws)):
            self._notify_for_page("本地数据已变化，请重试", type="warning")
            return
        old = rws[idx]
        rws[idx] = {**old, "rest": body, "enabled": bool(enabled)}
        chn = str(c2.get("chain") or cname)
        cex = bool(c2.get("chain_exists", True))
        remote_chain_cache.mark_pending_apply(peer_id, chn, cex, rws)
        if await self._apply_remote_cache_to_peer(peer_id):
            dialog.close()
            self._notify_for_page("已写回对端", type="positive")
        self._refresh_remote_view()

    async def _do_edit_remote_block_replace_and_push(
        self, dialog, peer_id: str, at_idx: int, new_block: list[dict]
    ) -> None:
        if not self._require_remote_strategy_tab():
            return
        if self._active_remote_peer_id() != peer_id:
            self._notify_for_page("对端已切换，已取消写回", type="warning")
            return
        c2 = remote_chain_cache.read_remote_chain_cache(peer_id) or {}
        cname = IptablesManager.peer_chain_name_for_id(peer_id)
        rws = list(c2.get("rows") or [])
        if not (0 <= at_idx < len(rws)):
            self._notify_for_page("本地数据已变化，请重试", type="warning")
            return
        rws[at_idx : at_idx + 1] = new_block
        chn = str(c2.get("chain") or cname)
        cex = bool(c2.get("chain_exists", True))
        remote_chain_cache.mark_pending_apply(peer_id, chn, cex, rws)
        try:
            rests = remote_chain_cache.rests_to_push_list(rws)
            await run.io_bound(
                self._peer_service.apply_remote_peer_filter_chain_rests, peer_id, rests
            )
            remote_chain_cache.record_after_apply(peer_id, chn, cex, rws, True, None)
        except Exception as exc:
            remote_chain_cache.record_after_apply(peer_id, chn, cex, rws, False, str(exc))
            logger.error("对端编辑写回: %s", exc)
            self._notify_for_page(str(exc), type="negative")
            return
        logger.info("对端链行已按表单更新 peer=%s at=%s n=%s", peer_id, at_idx, len(new_block))
        dialog.close()
        self._notify_for_page("已写回对端", type="positive")
        self._refresh_remote_view()

    def _confirm_delete_remote_line(self, idx: int) -> None:
        confirm_dialog.show(
            "删除本行并立即写回对端。确定？",
            on_confirm=lambda: self._schedule_remote_coro(self._do_delete_remote_line_async(idx)),
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
            pid, str(c2.get("chain") or cname), bool(c2.get("chain_exists", True)), rws
        )
        self._reindex_selected_after_remote_delete(idx)
        if await self._apply_remote_cache_to_peer(pid):
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
        self._open_create_rule_dialog("remote")

    def _switch_owner(self, owner_id: str) -> None:
        self.current_owner_id = owner_id.strip()
        self._refresh_rules()

    def _refresh_rules(self) -> None:
        if self.rules_container is None:
            return
        if not self._center_tab_active:
            return
        self.rules_container.clear()
        self._is_unified_list = not (self.current_owner_id or "").strip()
        with self.rules_container:
            if self._is_unified_list:
                rules = self.rule_service.list_unified_flat()
                scope_title: str | None = None
                sub_hint: str | None = None
                show_owner = True
            else:
                rules = self.rule_service.list_by_owner(self.current_owner_id)
                scope_title = f"归属: {self.current_owner_id}"
                sub_hint = "拖拽排序（JSON 规则）。"
                show_owner = False

            n_rules = len(rules)
            if self._rules_total_footer is not None:
                self._rules_total_footer.set_text(f"{n_rules} RULES TOTAL")

            if not rules:
                self._render_empty("shield", "暂无规则", "点新建，或清空关键词看全部。")
                return
            with ui.element("div").classes("mgmt-panel mgmt-panel-flex w-full min-h-0 flex-1"):
                with ui.element("div").classes("mgmt-list-head"):
                    with ui.column().classes("gap-1"):
                        ui.label("规则顺序").classes("mgmt-section-title")
                        if sub_hint:
                            ui.label(sub_hint).classes("mgmt-section-sub")
                    if scope_title:
                        ui.label(scope_title).classes("meta-badge")
                list_id = "firewall-rule-list"
                with ui.element("div").classes("mgmt-panel-scroll flex-1").props(f'id="{list_id}"'):
                    for rule in rules:
                        self._render_rule_card(rule, show_owner=show_owner)
            self._run_javascript_in_page_client(
                """
                if (typeof Sortable !== 'undefined') {
                    var el = document.getElementById('firewall-rule-list');
                    if (el) {
                        if (el._sortable) { el._sortable.destroy(); }
                        el._sortable = new Sortable(el, {
                            animation: 150,
                            handle: '.firewall-rule-drag',
                            ghostClass: 'sortable-ghost',
                            onEnd: function(evt) {
                                var items = evt.to.querySelectorAll('[data-rule-id]');
                                var ids = Array.from(items).map(function(e) { return e.getAttribute('data-rule-id'); });
                                emitEvent('firewall_reorder', { ids: ids });
                            }
                        });
                    }
                }
                """
            )
            ui.on("firewall_reorder", lambda e: self._handle_reorder(e.args.get("ids", [])))

    def _copy_text_to_clipboard(self, text: str) -> None:
        """将文本写入浏览器剪贴板并轻提示；供归属等可点击区域使用。"""
        t = (text or "").strip()
        if not t:
            return
        self._run_javascript_in_page_client(
            f"navigator.clipboard.writeText({json.dumps(t)});"
        )
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
                    ui.checkbox(
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

    def _show_create_dialog(self) -> None:
        self._open_create_rule_dialog("center")

    def _open_create_rule_dialog(self, target: str) -> None:
        """``target`` 为 ``center`` 时写中心 JSON；为 ``remote`` 时同表单生成对端链 ``rest`` 并写回。"""
        if target not in ("center", "remote"):
            return
        if target == "center" and not self._require_center_strategy_tab():
            return
        if target == "remote":
            if not self._require_remote_strategy_tab():
                return
            rpid = self._active_remote_peer_id()
            if not rpid:
                self._notify_for_page("请先在「对端远端」选对端", type="warning")
                return
            if not self._peer_service.get(rpid):
                self._notify_for_page("对端不存在", type="negative")
                return
            c0 = remote_chain_cache.read_remote_chain_cache(rpid) or {}
            if not (c0.get("rows") is not None) and not bool(c0.get("chain_exists", False)):
                self._notify_for_page(
                    "对端链可能尚未创建：可先 SSH 下发防火墙，或直接建规则后写回。",
                    type="warning",
                )
        else:
            rpid = ""
        groups = GroupService().list_all()
        group_map = {g["id"]: g for g in groups}
        users = UserService().list_all()
        user_options = {u["username"]: u["username"] for u in users if u.get("username")}
        from app.core.config import load_config

        config = load_config()
        instances = list((config or {}).get("instances", {}).keys()) if config else []
        auto_instance = instances[0] if instances else "server"
        default_group_id: str | None = None
        if groups:
            cur = str(self.current_owner_id or "").strip()
            default_group_id = cur if cur in group_map else str(groups[0]["id"])
        with ui.dialog().props("position=right maximized") as dialog, ui.card().classes(
            "w-[min(100vw,28rem)] h-full no-wrap overflow-y-auto"
        ):
            ui.label("新建对端链规则" if target == "remote" else "新建中心规则").classes("text-h6")
            if target == "remote" and rpid:
                cnm = IptablesManager.peer_chain_name_for_id(rpid)
                prn = self._peer_service.get(rpid) or {}
                ui.label(
                    f"对端 {str(prn.get('name') or rpid)[:40]} · 链 {cnm}；保存后 SSH 写回。"
                ).classes("text-caption text-grey")
            owner_type_select = ui.select(
                {"group": "组", "user": "用户"},
                label="归属类型",
                value="group",
            ).classes("w-full")
            group_options = {g["id"]: f"{g['name']} ({g['subnet']})" for g in groups}
            group_select = ui.select(
                group_options, label="选择组", with_input=True, value=default_group_id
            ).classes("w-full")
            user_select = ui.select(
                user_options, label="选择用户（多选）", multiple=True, with_input=True
            ).classes("w-full")
            user_select.set_visibility(False)
            peer_src_ux: dict = {"user_picked": False}
            peer_preset_holder: dict = {"by_key": {}}
            # 首帧部分环境下 select 的 value 尚未就绪，用 default_group_id 补一次，否则对端 CIDR 下拉 gid 为空
            peer_group_value_seeded: dict = {"done": False}
            auto_src_switch = ui.switch("源用本组子网", value=False)
            peer_lan_for_src = ui.select(
                {},
                label="对端内网 CIDR（任选；与上项二选一）",
                with_input=True,
            ).classes("w-full")
            source_cidr_input = ui.input(
                "源地址", placeholder="可走自动 / CCD"
            ).classes("w-full")
            source_user_hint = ui.label("多选用户时可预填 CCD 虚拟 IP，或留空从 CCD 读。").classes(
                "text-caption text-grey"
            )
            source_user_hint.set_visibility(False)
            with ui.row().classes("w-full gap-sm"):
                action_select = ui.select(
                    {"accept": "放行 (ACCEPT)", "drop": "丢弃 (DROP)", "reject": "拒绝 (REJECT)"},
                    label="动作",
                    value="accept",
                ).classes("w-full")
                protocol_select = ui.select(
                    {"all": "不限 (ALL)", "tcp": "TCP", "udp": "UDP", "icmp": "ICMP"},
                    label="协议",
                    value="all",
                    with_input=True,
                    new_value_mode="add-unique",
                ).classes("w-full")
            dest_ip_input = ui.input("目标地址", placeholder="子网或多 IP；空=不限").classes("w-full")
            dest_port_input = ui.input("目标端口", placeholder="空=不限制").classes("w-full")
            priority_mode = ui.toggle(
                {"insert": "插入（指定行号）", "append": "追加（链尾）"},
                value="insert",
            ).classes("w-full")
            priority_input = ui.number("插入行号", value=None, min=1, max=9999, step=10).classes("w-full")
            priority_mode.on_value_change(lambda: priority_input.set_visibility(priority_mode.value == "insert"))
            desc_input = ui.input("规则描述", placeholder="可选").classes("w-full")

            def _mutex() -> None:
                if owner_type_select.value != "group":
                    return
                a = bool(auto_src_switch.value)
                k = str(peer_lan_for_src.value or "").strip()
                c = (peer_preset_holder.get("by_key") or {}).get(k) if k else None
                raw = (source_cidr_input.value or "").strip()
                # 对端 CIDR 与源框绑定仅当源内容仍为该网段时生效；手改/清空后解除互斥
                c_lock = bool(c and raw and raw == c)
                peer_lan_for_src.set_enabled(not a)
                auto_src_switch.set_enabled(a or (not c_lock))

            def _on_source_cidr_in_group() -> None:
                if owner_type_select.value != "group":
                    return
                if not (source_cidr_input.value or "").strip():
                    peer_lan_for_src.value = None
                    peer_src_ux["user_picked"] = False
                _mutex()

            def _sync_peer() -> None:
                if owner_type_select.value != "group":
                    peer_lan_for_src.set_visibility(False)
                    return
                peer_lan_for_src.set_visibility(True)
                if not peer_group_value_seeded["done"] and default_group_id and not str(
                    group_select.value or ""
                ).strip():
                    group_select.value = str(default_group_id)
                peer_group_value_seeded["done"] = True
                gid = str(group_select.value or "").strip()
                presets = (
                    self._peer_service.list_peer_lan_firewall_presets_for_center_form()
                    if gid
                    else []
                )
                peer_preset_holder["by_key"] = {p["key"]: p["cidr"] for p in presets}
                peer_lan_for_src.options = {p["key"]: p["label"] for p in presets}
                peer_lan_for_src.update()
                if peer_lan_for_src.value and peer_lan_for_src.value not in peer_lan_for_src.options:
                    peer_lan_for_src.value = None
                if (peer_lan_for_src.value in peer_lan_for_src.options) and not peer_src_ux["user_picked"]:
                    peer_lan_for_src.value = None
                peer_lan_for_src.update()
                _mutex()

            def _on_peer_ch() -> None:
                v = peer_lan_for_src.value
                if v in (peer_preset_holder.get("by_key") or {}):
                    peer_src_ux["user_picked"] = True
                else:
                    peer_src_ux["user_picked"] = False
                cidr = (peer_preset_holder.get("by_key") or {}).get(v) if v else None
                if cidr:
                    auto_src_switch.value = False
                    source_cidr_input.value = cidr
                    source_cidr_input.props(remove="readonly")
                _mutex()

            def _on_group() -> None:
                if auto_src_switch.value and group_select.value and str(group_select.value) in group_map:
                    source_cidr_input.value = group_map[str(group_select.value)].get("subnet", "")
                peer_src_ux["user_picked"] = False
                _sync_peer()

            def _on_auto() -> None:
                if auto_src_switch.value:
                    peer_lan_for_src.value = None
                    peer_src_ux["user_picked"] = False
                    g = str(group_select.value or "")
                    if g and g in group_map:
                        source_cidr_input.value = group_map[g].get("subnet", "")
                    source_cidr_input.props(add="readonly")
                else:
                    source_cidr_input.props(remove="readonly")
                _sync_peer()

            def _ot() -> None:
                ig = owner_type_select.value == "group"
                group_select.set_visibility(ig)
                user_select.set_visibility(not ig)
                auto_src_switch.set_visibility(ig)
                peer_lan_for_src.set_visibility(ig)
                source_user_hint.set_visibility(not ig)
                if ig:
                    _sync_peer()
                _mutex()

            owner_type_select.on_value_change(_ot)
            group_select.on_value_change(_on_group)
            def _on_users_ch() -> None:
                sel = user_select.value
                ulist = list(sel) if isinstance(sel, list) else ([sel] if sel else [])
                source_user_hint.set_visibility(bool(ulist))
                if ulist:
                    ips = _read_ccd_vpn_ips([str(x) for x in ulist])
                    if ips:
                        source_cidr_input.value = ", ".join(ips)

            user_select.on_value_change(_on_users_ch)
            source_cidr_input.on_value_change(lambda _: _on_source_cidr_in_group())
            auto_src_switch.on_value_change(_on_auto)
            peer_lan_for_src.on_value_change(_on_peer_ch)
            _on_group()

            with ui.row().classes("justify-end q-mt-md"):
                ui.button("取消", on_click=dialog.close).props("flat")
                def _ok() -> None:
                    if target == "center":
                        self._collect_center_create(
                            dialog,
                            owner_type_select,
                            group_select,
                            user_select,
                            group_map,
                            auto_src_switch,
                            peer_lan_for_src,
                            peer_preset_holder,
                            source_cidr_input,
                            action_select,
                            protocol_select,
                            dest_ip_input,
                            dest_port_input,
                            priority_mode,
                            priority_input,
                            desc_input,
                            auto_instance,
                        )
                    else:
                        self._collect_remote_create_like_center(
                            dialog,
                            rpid,
                            owner_type_select,
                            group_select,
                            user_select,
                            group_map,
                            auto_src_switch,
                            peer_lan_for_src,
                            peer_preset_holder,
                            source_cidr_input,
                            action_select,
                            protocol_select,
                            dest_ip_input,
                            dest_port_input,
                            priority_mode,
                            priority_input,
                            desc_input,
                        )

                ui.button("创建", on_click=_ok).props("color=primary")
        dialog.open()

    def _read_create_form_to_values(
        self,
        owner_type_select,
        group_select,
        user_select,
        group_map,
        auto_src_switch,
        peer_lan_for_src,
        peer_preset_holder,
        source_cidr_input,
        action_select,
        protocol_select,
        dest_ip_input,
        dest_port_input,
        desc_input,
    ) -> dict | None:
        """与中心「新建」表单一致，读入为 dict；缺省项已通知并返回 None。"""
        ot = owner_type_select.value
        if ot == "group":
            oid = str(group_select.value or "").strip()
            if not oid:
                self._notify_for_page("请选择组", type="negative")
                return None
            if auto_src_switch.value and oid in group_map:
                src_sub = group_map[oid].get("subnet", "") or None
                src_ips = None
            else:
                k = str(peer_lan_for_src.value or "").strip()
                cidr = (peer_preset_holder.get("by_key") or {}).get(k) if k else None
                if cidr:
                    src_sub, src_ips = cidr, None
                else:
                    raw = (source_cidr_input.value or "").strip()
                    if raw:
                        src_sub, src_ips = raw, None
                    else:
                        src_sub, src_ips = None, None
        else:
            sel = user_select.value or []
            if not sel:
                self._notify_for_page("请选择用户", type="negative")
                return None
            ulist = list(sel) if isinstance(sel, list) else [sel]
            oid = ulist[0] if ulist else ""
            if not oid:
                self._notify_for_page("请选择用户", type="negative")
                return None
            raw = (source_cidr_input.value or "").strip()
            if raw:
                try:
                    ssub, sips = _parse_user_source_for_create(raw)
                except ValueError as exc:
                    self._notify_for_page(str(exc), type="negative")
                    return None
                src_sub, src_ips = ssub, sips
            else:
                src_ips = _read_ccd_vpn_ips([str(x) for x in ulist])
                src_sub = None
                if not src_ips:
                    self._notify_for_page("无 CCD 或读不到虚拟 IP", type="negative")
                    return None
        return {
            "owner_type": ot,
            "owner_id": oid,
            "source_subnet": src_sub,
            "source_ips": src_ips,
            "action": str(action_select.value or "accept"),
            "protocol": str(protocol_select.value or "all"),
            "dest_ip": (dest_ip_input.value or "").strip() or None,
            "dest_port": (dest_port_input.value or "").strip() or None,
            "description": (desc_input.value or "").strip() or None,
        }

    def _remote_priority_base_list(self, peer_id: str) -> list[dict]:
        """供与中心相同的优先级选择：按当前对端工作副本行推算 priority 集合。"""
        cache = remote_chain_cache.read_remote_chain_cache(peer_id) or {}
        rws = list(cache.get("rows") or [])
        out: list[dict] = []
        for i, r in enumerate(rws):
            p = int(r.get("priority", 0) or 0) or (i + 1) * 10
            out.append({"id": f"r{i}", "priority": p})
        return out

    def _collect_center_create(
        self,
        dialog,
        owner_type_select,
        group_select,
        user_select,
        group_map,
        auto_src_switch,
        peer_lan_for_src,
        peer_preset_holder,
        source_cidr_input,
        action_select,
        protocol_select,
        dest_ip_input,
        dest_port_input,
        priority_mode,
        priority_input,
        desc_input,
        auto_instance: str,
    ) -> None:
        v = self._read_create_form_to_values(
            owner_type_select,
            group_select,
            user_select,
            group_map,
            auto_src_switch,
            peer_lan_for_src,
            peer_preset_holder,
            source_cidr_input,
            action_select,
            protocol_select,
            dest_ip_input,
            dest_port_input,
            desc_input,
        )
        if v is None:
            return
        pr = self._resolve_priority(priority_mode.value, priority_input.value)
        self._do_create(
            dialog,
            owner_type=v["owner_type"],
            owner_id=v["owner_id"],
            instance=auto_instance,
            deployment_target="center",
            action=v["action"],
            protocol=v["protocol"],
            source_subnet=v["source_subnet"],
            source_ips=v["source_ips"],
            dest_ip=v["dest_ip"],
            dest_port=v["dest_port"],
            priority=pr,
            description=v["description"],
        )

    def _collect_remote_create_like_center(
        self,
        dialog,
        peer_id: str,
        owner_type_select,
        group_select,
        user_select,
        group_map,
        auto_src_switch,
        peer_lan_for_src,
        peer_preset_holder,
        source_cidr_input,
        action_select,
        protocol_select,
        dest_ip_input,
        dest_port_input,
        priority_mode,
        priority_input,
        desc_input,
    ) -> None:
        v = self._read_create_form_to_values(
            owner_type_select,
            group_select,
            user_select,
            group_map,
            auto_src_switch,
            peer_lan_for_src,
            peer_preset_holder,
            source_cidr_input,
            action_select,
            protocol_select,
            dest_ip_input,
            dest_port_input,
            desc_input,
        )
        if v is None:
            return
        base = self._remote_priority_base_list(peer_id)
        pr = self._resolve_priority(priority_mode.value, priority_input.value, base=base)
        try:
            rests = remote_rests_from_create_fields(
                source_subnet=v["source_subnet"],
                source_ips=v["source_ips"],
                action=v["action"],
                protocol=v["protocol"],
                dest_ip=v["dest_ip"],
                dest_port=v["dest_port"],
            )
        except (ValueError, ValidationError) as exc:
            logger.error("对端新建 rest 生成失败: %s", exc)
            self._notify_for_page(str(exc), type="negative")
            return
        desc = (v.get("description") or "").strip() or None
        new_rows: list[dict] = [
            {
                "rest": line,
                "enabled": True,
                "priority": pr,
                **({"description": desc} if desc else {}),
            }
            for line in rests
        ]
        self._schedule_remote_coro(
            self._do_remote_create_insert_and_push(dialog, str(peer_id).strip(), new_rows, pr)
        )

    async def _do_remote_create_insert_and_push(
        self,
        dialog,
        peer_id: str,
        new_rows: list[dict],
        priority: int,
    ) -> None:
        if not self._require_remote_strategy_tab():
            return
        if self._active_remote_peer_id() != peer_id:
            self._notify_for_page("对端已切换，已取消写回", type="warning")
            return
        c2 = remote_chain_cache.read_remote_chain_cache(peer_id) or {}
        cname = IptablesManager.peer_chain_name_for_id(peer_id)
        rws = list(c2.get("rows") or [])

        def row_pri(i: int) -> int:
            r = rws[i]
            return int(r.get("priority", 0) or 0) or (i + 1) * 10

        insert_at = len(rws)
        for i in range(len(rws)):
            if row_pri(i) > int(priority):
                insert_at = i
                break
        for j, nr in enumerate(new_rows):
            rws.insert(insert_at + j, nr)
        chn = str(c2.get("chain") or cname)
        cex = bool(c2.get("chain_exists", True))
        remote_chain_cache.mark_pending_apply(peer_id, chn, cex, rws)
        try:
            rests = remote_chain_cache.rests_to_push_list(rws)
            await run.io_bound(
                self._peer_service.apply_remote_peer_filter_chain_rests, peer_id, rests
            )
            remote_chain_cache.record_after_apply(peer_id, chn, cex, rws, True, None)
        except Exception as exc:
            remote_chain_cache.record_after_apply(peer_id, chn, cex, rws, False, str(exc))
            logger.error("对端新建规则写回: %s", exc)
            self._notify_for_page(str(exc), type="negative")
            return
        logger.info("对端链已插入新建行 peer=%s n=%s pri=%s", peer_id, len(new_rows), priority)
        dialog.close()
        self._notify_for_page("已写回对端", type="positive")
        self._refresh_remote_view()

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

    def _do_create(self, dialog, **kwargs) -> None:
        owner_id = str(kwargs.get("owner_id", "") or "")
        if not owner_id:
            self._notify_for_page("请选择归属", type="negative")
            return
        try:
            ot = kwargs.pop("owner_type", "group")
            payload = {"owner_type": ot, **kwargs}
            if "protocol" in payload and str(payload.get("protocol") or "").lower() in ("any", "all"):
                payload["protocol"] = "all"
            self.rule_service.create(payload)
            dialog.close()
            self._notify_for_page("规则创建成功", type="positive")
            self._refresh_rules()
        except Exception as exc:
            logger.error("create rule: %s", exc)
            self._notify_for_page(f"创建失败: {exc}", type="negative")
            self._refresh_rules()

    def _show_edit_dialog(self, rule: dict) -> None:
        if not self._require_center_strategy_tab():
            return
        rid = str(rule.get("id") or "")
        is_center_peer = rid.startswith(CE_PEER_RULE_PREFIX) or rule.get("_row_kind") == "center_peer"
        with ui.dialog().props("position=right maximized") as dialog, ui.card().classes(
            "w-[min(100vw,28rem)] h-full"
        ):
            ui.label("编辑对端·中心放行" if is_center_peer else "编辑中心规则").classes("text-h6")
            if is_center_peer:
                ui.label(
                    "字段对应对端 JSON 的 center_forward_*；保存后与本机 VPN_FORWARD 一致；仅支持放行。"
                ).classes("text-caption text-grey q-mb-sm")
            action_select = ui.select(
                {"accept": "放行 (ACCEPT)", "drop": "丢弃 (DROP)", "reject": "拒绝 (REJECT)"},
                label="动作",
                value=rule.get("action", "accept"),
            ).classes("w-full")
            protocol_select = ui.select(
                {"all": "不限 (ALL)", "tcp": "TCP", "udp": "UDP", "icmp": "ICMP"},
                label="协议",
                value=(rule.get("protocol") or "all").replace("any", "all"),
                with_input=True,
                new_value_mode="add-unique",
            ).classes("w-full")
            ssub = rule.get("source_subnet") or ""
            sips = rule.get("source_ips")
            src_val = ssub or (",".join(sips) if sips else "")
            source_input = ui.input("源子网 CIDR 或源 IP 列表", value=src_val).classes("w-full")
            dest_ip_input = ui.input("目标地址", value=rule.get("dest_ip") or "").classes("w-full")
            dest_port_input = ui.input("目标端口", value=rule.get("dest_port") or "").classes("w-full")
            desc_input = ui.input("规则描述", value=rule.get("description") or "").classes("w-full")
            enabled_switch = ui.switch("启用", value=rule.get("enabled", True))
            with ui.row().classes("justify-end q-mt-md"):
                ui.button("取消", on_click=dialog.close).props("flat")
                ui.button(
                    "保存",
                    on_click=lambda: self._do_edit(
                        dialog,
                        rule,
                        action=action_select.value,
                        protocol=protocol_select.value,
                        source_input=source_input.value,
                        dest_ip=dest_ip_input.value or None,
                        dest_port=dest_port_input.value or None,
                        description=desc_input.value or None,
                        enabled=enabled_switch.value,
                    ),
                ).props("color=primary")
        dialog.open()

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
    ) -> None:
        t = (source_input or "").strip()
        rid = str(original.get("id") or "")
        is_ce = rid.startswith(CE_PEER_RULE_PREFIX) or original.get("_row_kind") == "center_peer"
        if is_ce:
            peer_id = str(original.get("_peer_id") or "").strip()
            if not peer_id and rid.startswith(CE_PEER_RULE_PREFIX):
                peer_id = rid[len(CE_PEER_RULE_PREFIX) :]
            peer_id = peer_id.strip()
            if not peer_id:
                self._notify_for_page("对端 id 无效", type="negative")
                return
            if not self._peer_service.get(peer_id):
                self._notify_for_page("对端不存在", type="negative")
                return
            if (str(action or "").lower() != "accept"):
                self._notify_for_page("对端·中心放行仅支持 ACCEPT", type="negative")
                return
            lans = _parse_lan_cidrs_text(t)
            for c in lans:
                if not validate_cidr(c):
                    self._notify_for_page(f"无效 CIDR: {c}", type="negative")
                    return
            dp = (dest_port or "").strip()
            try:
                FirewallRule(
                    owner_type="group",
                    owner_id="_",
                    action="accept",
                    priority=1,
                    dest_port=dp or None,
                )
            except ValidationError as exc:
                logger.error("对端·中心放行端口校验: %s", exc)
                self._notify_for_page(str(exc), type="negative")
                return
            try:
                self._peer_service.update(
                    peer_id,
                    {
                        "lan_cidrs": lans,
                        "center_forward_enabled": bool(enabled),
                        "center_forward_dest_ip": (dest_ip or "").strip(),
                        "center_forward_dest_port": dp,
                        "center_forward_protocol": (protocol or "all").strip().lower(),
                        "center_forward_rule_description": (description or "").strip(),
                    },
                )
            except Exception as exc:
                logger.error("对端·中心放行保存: %s", exc)
                self._notify_for_page(f"更新失败: {exc}", type="negative")
                return
            dialog.close()
            self._notify_for_page("已更新", type="positive")
            if os.name == "nt":
                self._notify_for_page("当前为 Windows，本机未落 iptables；请在 Linux 网关核对。", type="warning")
            self._refresh_rules()
            return
        otype = str(original.get("owner_type", "group"))
        src_subnet = None
        src_ips = None
        if otype == "user" and t:
            try:
                src_subnet, src_ips = _parse_user_source_for_create(t)
            except ValueError as exc:
                self._notify_for_page(str(exc), type="negative")
                return
        else:
            src_subnet = t or None
        new_rule_data = {**original, "action": action, "protocol": protocol, "source_subnet": src_subnet, "source_ips": src_ips, "dest_ip": dest_ip, "dest_port": dest_port, "description": description, "enabled": enabled}
        new_rule_data.pop("_owner_id", None)
        new_rule_data.pop("_row_kind", None)
        new_rule_data.pop("_peer_id", None)
        new_rule_data["updated_at"] = datetime.now().isoformat()
        try:
            self.rule_service.update_by_id(str(original["id"]), new_rule_data)
            dialog.close()
            self._notify_for_page("已更新", type="positive")
            self._refresh_rules()
        except Exception as exc:
            logger.error("edit: %s", exc)
            self._notify_for_page(f"更新失败: {exc}", type="negative")
            self._refresh_rules()

    def _confirm_delete(self, rule_id: str) -> None:
        confirm_dialog.show(
            f"删除规则 {str(rule_id)[:8]}…？（对端行会清该对端 LAN）",
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

    def _show_import_dialog(self) -> None:
        self._import_source_file_mode = None
        self._import_last_uploaded_text = None
        if self._center_tab_active:
            title = "导入中心规则"
            hint = (
                ".json = 整库 backup；.txt = 简写追加到当前关键词选中的归属。"
                " 纯粘贴：`{` 开头为 backup，否则为简写。"
            )
        else:
            title = "导入对端链规则"
            hint = (
                "粘贴或上传 .json / .txt：iptables -S、每行片段、简写、或对端副本 JSON。# 为注释。"
            )
        with ui.dialog() as dialog, ui.card().classes("w-full max-w-2xl"):
            ui.label(title).classes("text-h6")
            ui.label(hint).classes("text-caption text-grey")
            ta = ui.textarea("规则内容").classes("w-full q-mt-sm").props("rows=16")
            ta.on_value_change(lambda _: self._on_import_textarea_value_change(ta))

            async def on_import_file_upload(e) -> None:
                try:
                    data = await e.file.read()
                    text = data.decode("utf-8", errors="replace")
                    self._import_last_uploaded_text = (text or "").strip()
                    ta.set_value(text)
                    name = (getattr(e.file, "name", "") or "").strip()
                    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                    if ext == "json":
                        self._import_source_file_mode = "json_overwrite"
                    elif ext in ("txt", "text"):
                        self._import_source_file_mode = "simplified_text"
                    else:
                        self._import_source_file_mode = None
                    self._notify_for_page(
                        f"已载入: {name}"
                        + (
                            "（.json 覆盖 / .txt 追加）"
                            if self._center_tab_active and self._import_source_file_mode
                            else ""
                        ),
                        type="positive",
                    )
                    logger.info(
                        "防火墙导入已读入文件 name=%s mode=%s center=%s",
                        name,
                        self._import_source_file_mode,
                        self._center_tab_active,
                    )
                except Exception as exc:
                    fname = (getattr(e.file, "name", "") or "").strip()
                    self._import_last_uploaded_text = None
                    self._import_source_file_mode = None
                    logger.exception("防火墙导入读文件失败: %s", fname)
                    self._notify_for_page(f"读文件失败 ({fname}): {exc}", type="negative")
                    raise RuntimeError(f"读文件失败: {exc}") from exc

            ul = ui.upload(
                label="从文件加载（.json / .txt）",
                on_upload=on_import_file_upload,
                auto_upload=True,
                max_files=1,
            ).props('accept=".json,.txt,.text,application/json,text/plain"')
            ul.classes("w-full q-mt-sm")
            with ui.row().classes("justify-end q-mt-md"):
                ui.button("取消", on_click=dialog.close).props("flat")
                ui.button("导入", on_click=lambda: self._do_import(dialog, ta.value)).props("color=primary")
        dialog.open()

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
        if self.rule_service.restore(text):
            dialog.close()
            self._notify_for_page("backup 已恢复", type="positive")
            self._refresh_rules()
        else:
            self._notify_for_page("backup 恢复失败", type="negative")

    def _import_ok_center_simplified(self, dialog, specs) -> None:
        """简写多行：逐条 create，优先级取全局未占用值。"""
        from app.core.config import load_config

        self._import_source_file_mode = None
        self._import_last_uploaded_text = None
        oid = (self.current_owner_id or "").strip()
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
            all_r = self.rule_service.list_all_flat()
            used = {int(r.get("priority", 0) or 0) for r in all_r}
            mx = max(used) if used else 0
            cand = mx + 10
            while cand in used:
                cand += 1
            payload["priority"] = int(cand)
            try:
                if str(payload.get("protocol") or "").lower() in ("any", "all", ""):
                    payload["protocol"] = "all"
                self.rule_service.create(payload)
            except Exception as exc:
                logger.error("中心简写第 %s 行创建失败: %s", i + 1, exc)
                self._notify_for_page(f"第 {i + 1} 行创建失败: {exc}", type="negative")
                return
        dialog.close()
        logger.info("中心简写导入完成 n=%s owner=%s", len(specs), oid)
        self._notify_for_page("简写已追加", type="positive")
        self._refresh_rules()

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
            dialog.close()
            self._notify_for_page("已导入并写回", type="positive")
        self._selected_remote_indices.clear()
        self._refresh_remote_view()

    def _toggle_selection(self, rule_id: str, checked: bool) -> None:
        if checked:
            self.selected_rule_ids.add(rule_id)
        else:
            self.selected_rule_ids.discard(rule_id)

    def _batch_delete(self) -> None:
        if self._center_tab_active:
            if not self._require_center_strategy_tab():
                return
            if not self.selected_rule_ids:
                self._notify_for_page("未勾选", type="warning")
                return
            confirm_dialog.show("批量删除所选中心规则？", on_confirm=self._do_batch_delete_center)
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
        confirm_dialog.show(
            "删除勾选行并写回对端。确定？",
            on_confirm=lambda: self._schedule_remote_coro(self._do_batch_remote_delete_async()),
        )

    def _do_batch_delete_center(self) -> None:
        for rid in list(self.selected_rule_ids):
            try:
                self.rule_service.delete(rid)
            except Exception as exc:
                self._notify_for_page(str(exc), type="negative")
        self.selected_rule_ids.clear()
        self._refresh_rules()

    async def _do_batch_remote_delete_async(self) -> None:
        if not self._require_remote_strategy_tab():
            return
        pid = self._active_remote_peer_id()
        if not pid:
            self._notify_for_page("请先选择对端", type="warning")
            return
        c2 = remote_chain_cache.read_remote_chain_cache(pid) or {}
        cname = IptablesManager.peer_chain_name_for_id(pid)
        rws = list(c2.get("rows") or [])
        if not rws or not self._selected_remote_indices:
            self._notify_for_page("未勾选或无可删行", type="warning")
            return
        to_drop = sorted(self._selected_remote_indices, reverse=True)
        for j in to_drop:
            if 0 <= j < len(rws):
                rws.pop(j)
        remote_chain_cache.mark_pending_apply(
            pid, str(c2.get("chain") or cname), bool(c2.get("chain_exists", True)), rws
        )
        if await self._apply_remote_cache_to_peer(pid):
            self._notify_for_page("已写回对端", type="positive")
        self._selected_remote_indices.clear()
        self._refresh_remote_view()

    def _batch_enable(self) -> None:
        self._batch_set_enabled(True)

    def _batch_disable(self) -> None:
        self._batch_set_enabled(False)

    def _batch_set_enabled(self, en: bool) -> None:
        if self._center_tab_active:
            if not self._require_center_strategy_tab():
                return
            for rid in list(self.selected_rule_ids):
                try:
                    self.rule_service.set_enabled(rid, en)
                except Exception as exc:
                    self._notify_for_page(str(exc), type="negative")
            self.selected_rule_ids.clear()
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

    async def _do_batch_remote_set_enabled_async(self, en: bool) -> None:
        if not self._require_remote_strategy_tab():
            return
        pid = self._active_remote_peer_id()
        if not pid:
            return
        c2 = remote_chain_cache.read_remote_chain_cache(pid) or {}
        cname = IptablesManager.peer_chain_name_for_id(pid)
        rws = list(c2.get("rows") or [])
        for j in self._selected_remote_indices:
            if 0 <= j < len(rws):
                rws[j]["enabled"] = en
        remote_chain_cache.mark_pending_apply(
            pid, str(c2.get("chain") or cname), bool(c2.get("chain_exists", True)), rws
        )
        if await self._apply_remote_cache_to_peer(pid):
            self._notify_for_page("已写回对端", type="positive")
        self._selected_remote_indices.clear()
        self._refresh_remote_view()
