# -*- coding: utf-8 -*-
"""Firewall 中心规则创建、编辑、删除与中心批量行为 mixin。

该模块只承载中心规则动作；页面壳、跨 Tab 调度、中心 Grid 和远端规则行为
分别由 FirewallPage、FirewallCenterGridMixin 与 FirewallRemoteMixin 管理。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

from nicegui import ui
from pydantic import ValidationError

from app.models.firewall import FirewallRule
from app.services.firewall.rule_service import CE_PEER_RULE_PREFIX
from app.services.group.crud import GroupService
from app.services.user.crud import UserService
from app.ui.components import confirm_dialog
from app.ui.components.form_dialog import create_form_dialog
from app.ui.components.firewall_rule_form import render_firewall_rule_create_fields
from app.ui.firewall_helpers import (
    parse_lan_cidrs_text,
    parse_user_source_for_create,
    read_ccd_vpn_ips,
)
from app.utils.cidr import validate_cidr

logger = logging.getLogger(__name__)


class FirewallCenterActionsMixin:
    """FirewallPage 的中心规则创建、编辑、删除与中心批量实现。"""
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
                ccd_reader=read_ccd_vpn_ips,
                parse_user_source=parse_user_source_for_create,
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

            lan_cidrs = parse_lan_cidrs_text(source_text)
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
                source_subnet, source_ips = parse_user_source_for_create(
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
