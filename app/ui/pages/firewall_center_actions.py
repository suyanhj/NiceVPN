# -*- coding: utf-8 -*-
"""Firewall 中心规则创建、编辑、导入与中心批量行为 mixin。

该模块只承载中心规则动作；页面壳、跨 Tab 调度、中心 Grid 和远端规则行为
分别由 FirewallPage、FirewallCenterGridMixin 与 FirewallRemoteMixin 管理。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from typing import Any

from nicegui import run, ui
from pydantic import ValidationError

from app.models.firewall import FirewallRule
from app.services.firewall.rule_service import CE_PEER_RULE_PREFIX
from app.services.firewall.simple_rule_import import (
    center_rule_payload_from_simplified,
    is_center_backup_json_text,
    parse_center_simplified_lines,
    resolve_center_owner_type,
    source_fields_for_center,
)
from app.services.group.crud import GroupService
from app.services.user.crud import UserService
from app.ui.components import confirm_dialog
from app.ui.components.form_dialog import create_form_dialog
from app.ui.components.firewall_rule_form import render_firewall_rule_create_fields
from app.ui.firewall_helpers import (
    firewall_export_json_filename,
    parse_lan_cidrs_text,
    parse_user_source_for_create,
    read_ccd_vpn_ips,
)
from app.utils.cidr import validate_cidr

logger = logging.getLogger(__name__)


class FirewallCenterActionsMixin:
    """FirewallPage 的中心规则动作与导入实现。"""
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
                source_ips = read_ccd_vpn_ips([owner_id])
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
                src_ips = read_ccd_vpn_ips([oid])
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
