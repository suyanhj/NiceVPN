# -*- coding: utf-8 -*-
"""Firewall 对端单条规则动作 mixin。

远端同步、状态、AG Grid 与排序由 FirewallRemoteMixin 管理；
远端导入/批量由 FirewallRemoteImportBatchMixin 管理。本模块只承载
单条规则创建、编辑、删除以及工作副本写回动作。
"""

from __future__ import annotations

import logging
from typing import Any

from nicegui import ui
from pydantic import ValidationError

from app.models.firewall import FirewallRule
from app.services.firewall.iptables_mgr import IptablesManager
from app.services.firewall.simple_rule_import import remote_rests_from_create_fields
from app.services.firewall.tag_service import TagService
from app.services.group.crud import GroupService
from app.services.peer_instance import remote_chain_cache
from app.services.peer_instance.peer_ssh_connect import peer_row_has_usable_ssh_auth
from app.services.user.crud import UserService
from app.ui.adapters.firewall import parse_remote_rule_hints
from app.ui.components import confirm_dialog
from app.ui.components.form_dialog import create_form_dialog
from app.ui.components.firewall_rule_form import render_firewall_rule_create_fields
from app.ui.firewall_helpers import (
    parse_source_text_for_remote_save as _parse_source_text_for_remote_save,
    parse_user_source_for_create as _parse_user_source_for_create,
    read_ccd_vpn_ips as _read_ccd_vpn_ips,
    remote_row_to_edit_form_dict as _remote_row_to_edit_form_dict,
)

logger = logging.getLogger(__name__)


class FirewallRemoteActionsMixin:
    """FirewallPage 的远端单条规则动作实现。"""

    def _show_edit_remote_line_dialog(self, idx: int, row: dict) -> None:
        """统一表单编辑对端规则；标签规则使用逻辑规则编辑器。"""

        if isinstance(row.get("tag_binding"), dict):
            self._show_edit_remote_tag_rule_dialog(idx, row)
            return
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
            "firewall-rule-drawer w-[min(100vw,30rem)] h-full no-wrap overflow-y-auto"
        )
        form.open()

    def _show_edit_remote_tag_rule_dialog(self, idx: int, row: dict) -> None:
        """编辑一条逻辑标签规则；源地址始终由标签当前成员动态计算。"""
        if not self._require_remote_strategy_tab():
            return
        peer_id = self._active_remote_peer_id()
        if not peer_id:
            self._notify_for_page("请选择对端", type="warning")
            return
        binding = dict(row.get("tag_binding") or {})
        tag_service = TagService()
        tags = tag_service.list_all()
        options = {
            str(tag.get("id") or ""): str(tag.get("name") or "")
            for tag in tags if tag.get("id")
        }
        current_tag_id = str(binding.get("tag_id") or "")
        if current_tag_id and current_tag_id not in options:
            options[current_tag_id] = f"{current_tag_id[:8]}…（标签可能已删）"
        fields: dict[str, Any] = {}

        def body() -> None:
            ui.label(
                "这是一条动态标签规则。每次写回时，NiceVPN 会按标签当前用户/组成员更新对端 ipset，"
                "iptables 通过集合匹配源地址，不会为每个用户单独生成一条规则；标签没有有效成员时不会下发。"
            ).classes("section-caption")
            fields["tag"] = ui.select(
                options, label="来源标签", value=current_tag_id, with_input=True,
            ).classes("w-full")
            fields["action"] = ui.select(
                {"accept": "放行 (ACCEPT)", "drop": "丢弃 (DROP)", "reject": "拒绝 (REJECT)"},
                label="动作", value=str(binding.get("action") or "accept"),
            ).classes("w-full")
            fields["protocol"] = ui.select(
                {"all": "不限 (ALL)", "tcp": "TCP", "udp": "UDP", "icmp": "ICMP"},
                label="协议", value=str(binding.get("protocol") or "all"),
                with_input=True, new_value_mode="add-unique",
            ).classes("w-full")
            fields["dest_ip"] = ui.input(
                "目标地址", value=str(binding.get("dest_ip") or ""),
                placeholder="空=不限",
            ).classes("w-full")
            fields["dest_port"] = ui.input(
                "目标端口", value=str(binding.get("dest_port") or ""),
                placeholder="空=不限",
            ).classes("w-full")
            fields["description"] = ui.input(
                "规则描述", value=str(row.get("description") or ""),
            ).classes("w-full")
            fields["enabled"] = ui.switch(
                "启用", value=bool(row.get("enabled", True)),
            )

        async def submit() -> bool:
            tag_id = str(fields["tag"].value or "").strip()
            tag = tag_service.store.get_tag(tag_id) if tag_id else None
            if tag is None:
                self._notify_for_page("请选择有效标签", type="negative")
                return False
            action = str(fields["action"].value or "accept").strip().lower()
            protocol = str(fields["protocol"].value or "all").strip().lower()
            dest_ip = str(fields["dest_ip"].value or "").strip()
            dest_port = str(fields["dest_port"].value or "").strip()
            try:
                FirewallRule(
                    owner_type="tag", owner_id=tag_id,
                    action=action, priority=1, protocol=protocol,
                    dest_ip=dest_ip or None, dest_port=dest_port or None,
                )
            except (ValidationError, ValueError) as exc:
                self._notify_for_page(str(exc), type="negative")
                return False
            new_row = {
                "rest": "",
                "enabled": bool(fields["enabled"].value),
                "priority": int(row.get("priority", 0) or (idx + 1) * 10),
                "description": str(fields["description"].value or "").strip(),
                "tag_binding": {
                    "tag_id": tag_id,
                    "tag_name": str(tag.get("name") or tag_id),
                    "action": action,
                    "protocol": protocol,
                    "dest_ip": dest_ip,
                    "dest_port": dest_port,
                },
            }
            return await self._save_remote_block_replace_and_push(
                peer_id, idx, [new_row],
            )

        form = create_form_dialog(
            f"编辑标签规则 #{idx + 1}",
            body=body,
            on_submit=submit,
            submit_label="保存并写回",
            close_on_success=True,
        )
        form.dialog.props("position=right maximized")
        form.card.classes(
            "firewall-rule-drawer w-[min(100vw,30rem)] h-full no-wrap overflow-y-auto"
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
        tags = TagService().list_all()
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
                tags=tags,
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
                if values["owner_type"] == "tag":
                    tag_id = str(values["owner_id"] or "").strip()
                    tag = TagService().store.get_tag(tag_id)
                    if tag is None:
                        raise ValueError("所选标签不存在，请刷新后重试")
                    FirewallRule(
                        owner_type="tag",
                        owner_id=tag_id,
                        action=str(values["action"] or "accept"),
                        priority=priority,
                        protocol=str(values["protocol"] or "all"),
                        dest_ip=values.get("dest_ip"),
                        dest_port=values.get("dest_port"),
                    )
                    new_rows = [{
                        "rest": "",
                        "enabled": bool(values["enabled"]),
                        "priority": priority,
                        "tag_binding": {
                            "tag_id": tag_id,
                            "tag_name": str(tag.get("name") or tag_id),
                            "action": str(values["action"] or "accept"),
                            "protocol": str(values["protocol"] or "all"),
                            "dest_ip": str(values.get("dest_ip") or ""),
                            "dest_port": str(values.get("dest_port") or ""),
                        },
                    }]
                else:
                    rests = remote_rests_from_create_fields(
                        source_subnet=values["source_subnet"],
                        source_ips=values["source_ips"],
                        action=values["action"],
                        protocol=values["protocol"],
                        dest_ip=values["dest_ip"],
                        dest_port=values["dest_port"],
                    )
                    description = str(values.get("description") or "").strip() or None
                    new_rows = [
                        {
                            "rest": line,
                            "enabled": bool(values["enabled"]),
                            "priority": priority,
                            **({"description": description} if description else {}),
                        }
                        for line in rests
                    ]
            except (ValueError, ValidationError, TypeError) as exc:
                self._notify_for_page(str(exc), type="negative")
                return False

            if values["owner_type"] == "tag":
                description = str(values.get("description") or "").strip()
                if description:
                    new_rows[0]["description"] = description
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
            "firewall-rule-drawer w-[min(100vw,30rem)] h-full no-wrap overflow-y-auto"
        )
        form.open()

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
