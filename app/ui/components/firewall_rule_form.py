# -*- coding: utf-8 -*-
"""中心与对端防火墙新建规则共享表单字段。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from nicegui import ui


@dataclass(slots=True)
class FirewallRuleCreateFields:
    """共享新建规则字段与取值逻辑。"""

    owner_type: Any
    group: Any
    users: Any
    auto_source: Any
    peer_lan: Any
    source: Any
    user_hint: Any
    action: Any
    protocol: Any
    dest_ip: Any
    dest_port: Any
    enabled: Any
    priority_mode: Any
    priority: Any
    description: Any
    group_map: dict[str, Mapping[str, Any]]
    peer_preset_by_key: dict[str, str]
    ccd_reader: Callable[[list[str]], list[str]]
    parse_user_source: Callable[
        [str], tuple[str | None, list[str] | None]
    ]

    def read_values(self) -> dict[str, Any]:
        """读取并校验表单值；业务层只需处理优先级与落库/写回。"""

        owner_type = str(self.owner_type.value or "group").strip()
        if owner_type == "group":
            owner_id = str(self.group.value or "").strip()
            if not owner_id:
                raise ValueError("请选择组")
            if bool(self.auto_source.value) and owner_id in self.group_map:
                source_subnet = (
                    str(self.group_map[owner_id].get("subnet") or "").strip()
                    or None
                )
                source_ips = None
            else:
                preset_key = str(self.peer_lan.value or "").strip()
                preset = (
                    self.peer_preset_by_key.get(preset_key)
                    if preset_key
                    else None
                )
                if preset:
                    source_subnet, source_ips = preset, None
                else:
                    raw_source = str(self.source.value or "").strip()
                    source_subnet = raw_source or None
                    source_ips = None
        elif owner_type == "user":
            selected = self.users.value or []
            user_ids = (
                list(selected)
                if isinstance(selected, list)
                else [selected]
            )
            user_ids = [
                str(value).strip()
                for value in user_ids
                if str(value).strip()
            ]
            if not user_ids:
                raise ValueError("请选择用户")
            owner_id = user_ids[0]
            raw_source = str(self.source.value or "").strip()
            if raw_source:
                source_subnet, source_ips = self.parse_user_source(
                    raw_source
                )
            else:
                source_ips = self.ccd_reader(user_ids)
                source_subnet = None
                if not source_ips:
                    raise ValueError("无 CCD 或读不到虚拟 IP")
        else:
            raise ValueError(f"不支持的来源类型: {owner_type}")

        return {
            "owner_type": owner_type,
            "owner_id": owner_id,
            "source_subnet": source_subnet,
            "source_ips": source_ips,
            "action": str(self.action.value or "accept"),
            "protocol": str(self.protocol.value or "all"),
            "dest_ip": str(self.dest_ip.value or "").strip() or None,
            "dest_port": str(self.dest_port.value or "").strip() or None,
            "description": (
                str(self.description.value or "").strip() or None
            ),
            "enabled": bool(self.enabled.value),
            "priority_mode": str(
                self.priority_mode.value or "insert"
            ),
            "priority_value": self.priority.value,
        }


def render_firewall_rule_create_fields(
    *,
    groups: Sequence[Mapping[str, Any]],
    users: Sequence[Mapping[str, Any]],
    peer_presets_loader: Callable[[str], Sequence[Mapping[str, Any]]],
    ccd_reader: Callable[[list[str]], list[str]],
    parse_user_source: Callable[
        [str], tuple[str | None, list[str] | None]
    ],
    default_group_id: str | None = None,
    owner_type_label: str = "归属类型",
    peer_lan_label: str = "对端内网 CIDR（任选；与上项二选一）",
    source_placeholder: str = "组可自动填子网；用户可从 CCD 读取",
    user_hint_text: str = "多选用户时可预填 CCD 虚拟 IP，或留空从 CCD 读。",
    dest_ip_placeholder: str = "子网或多 IP；空=不限",
    dest_port_placeholder: str = "空=不限制",
) -> FirewallRuleCreateFields:
    """渲染中心/对端一致的新建规则字段及来源联动。"""

    group_map = {
        str(group.get("id") or ""): group
        for group in groups
        if str(group.get("id") or "").strip()
    }
    user_options = {
        str(user.get("username") or ""): str(user.get("username") or "")
        for user in users
        if str(user.get("username") or "").strip()
    }
    if default_group_id not in group_map:
        default_group_id = next(iter(group_map), None)

    owner_type = ui.select(
        {"group": "组", "user": "用户"},
        label=owner_type_label,
        value="group",
    ).classes("w-full")
    group = ui.select(
        {
            group_id: (
                f"{row.get('name') or group_id} "
                f"({row.get('subnet') or '—'})"
            )
            for group_id, row in group_map.items()
        },
        label="选择组",
        value=default_group_id,
        with_input=True,
    ).classes("w-full")
    users_select = ui.select(
        user_options,
        label="选择用户（多选）",
        multiple=True,
        with_input=True,
    ).classes("w-full")
    users_select.set_visibility(False)

    auto_source = ui.switch("源用本组子网", value=False)
    peer_lan = ui.select(
        {},
        label=peer_lan_label,
        with_input=True,
    ).classes("w-full")
    source = ui.input(
        "源地址",
        placeholder=source_placeholder,
    ).classes("w-full")
    user_hint = ui.label(user_hint_text).classes("section-caption")
    user_hint.set_visibility(False)

    with ui.row().classes("w-full gap-sm"):
        action = ui.select(
            {
                "accept": "放行 (ACCEPT)",
                "drop": "丢弃 (DROP)",
                "reject": "拒绝 (REJECT)",
            },
            label="动作",
            value="accept",
        ).classes("w-full")
        protocol = ui.select(
            {
                "all": "不限 (ALL)",
                "tcp": "TCP",
                "udp": "UDP",
                "icmp": "ICMP",
            },
            label="协议",
            value="all",
            with_input=True,
            new_value_mode="add-unique",
        ).classes("w-full")

    dest_ip = ui.input(
        "目标地址",
        placeholder=dest_ip_placeholder,
    ).classes("w-full")
    dest_port = ui.input(
        "目标端口",
        placeholder=dest_port_placeholder,
    ).classes("w-full")
    enabled = ui.switch("创建后启用", value=True)
    priority_mode = ui.toggle(
        {
            "insert": "插入（指定优先级）",
            "append": "追加（链尾）",
        },
        value="insert",
    ).classes("w-full")
    priority = ui.number(
        "优先级",
        value=None,
        min=1,
        max=999999,
        step=10,
    ).classes("w-full")
    description = ui.input(
        "规则描述",
        placeholder="可选",
    ).classes("w-full")

    peer_preset_by_key: dict[str, str] = {}

    def mutex() -> None:
        if owner_type.value != "group":
            return
        auto = bool(auto_source.value)
        key = str(peer_lan.value or "").strip()
        preset = peer_preset_by_key.get(key) if key else None
        raw = str(source.value or "").strip()
        preset_lock = bool(preset and raw and raw == preset)
        peer_lan.set_enabled(not auto)
        auto_source.set_enabled(auto or not preset_lock)

    def sync_peer_presets() -> None:
        if owner_type.value != "group":
            peer_lan.set_visibility(False)
            return
        peer_lan.set_visibility(True)
        group_id = str(group.value or "").strip()
        presets = list(peer_presets_loader(group_id)) if group_id else []
        peer_preset_by_key.clear()
        peer_preset_by_key.update(
            {
                str(item.get("key") or ""): str(item.get("cidr") or "")
                for item in presets
                if str(item.get("key") or "").strip()
                and str(item.get("cidr") or "").strip()
            }
        )
        options = {
            str(item.get("key") or ""): str(
                item.get("label") or item.get("cidr") or item.get("key")
            )
            for item in presets
            if str(item.get("key") or "").strip()
        }
        peer_lan.set_options(options)
        if peer_lan.value and peer_lan.value not in options:
            peer_lan.set_value(None)
        mutex()

    def on_owner_type_change() -> None:
        is_group = owner_type.value == "group"
        group.set_visibility(is_group)
        users_select.set_visibility(not is_group)
        auto_source.set_visibility(is_group)
        peer_lan.set_visibility(is_group)
        user_hint.set_visibility(not is_group)
        if is_group:
            sync_peer_presets()
        mutex()

    def on_group_change() -> None:
        group_id = str(group.value or "").strip()
        if bool(auto_source.value) and group_id in group_map:
            source.set_value(
                str(group_map[group_id].get("subnet") or "")
            )
        sync_peer_presets()

    def on_auto_change() -> None:
        if bool(auto_source.value):
            peer_lan.set_value(None)
            group_id = str(group.value or "").strip()
            if group_id in group_map:
                source.set_value(
                    str(group_map[group_id].get("subnet") or "")
                )
            source.props(add="readonly")
        else:
            source.props(remove="readonly")
        sync_peer_presets()

    def on_peer_change() -> None:
        key = str(peer_lan.value or "").strip()
        cidr = peer_preset_by_key.get(key) if key else None
        if cidr:
            auto_source.set_value(False)
            source.props(remove="readonly")
            source.set_value(cidr)
        mutex()

    def on_users_change() -> None:
        selected = users_select.value or []
        values = (
            list(selected)
            if isinstance(selected, list)
            else [selected]
        )
        clean = [
            str(value).strip()
            for value in values
            if str(value).strip()
        ]
        user_hint.set_visibility(bool(clean))
        if clean:
            ips = ccd_reader(clean)
            if ips:
                source.set_value(", ".join(ips))

    def on_source_change() -> None:
        if owner_type.value != "group":
            return
        if not str(source.value or "").strip():
            peer_lan.set_value(None)
        mutex()

    owner_type.on_value_change(lambda _event: on_owner_type_change())
    group.on_value_change(lambda _event: on_group_change())
    users_select.on_value_change(lambda _event: on_users_change())
    auto_source.on_value_change(lambda _event: on_auto_change())
    peer_lan.on_value_change(lambda _event: on_peer_change())
    source.on_value_change(lambda _event: on_source_change())
    priority_mode.on_value_change(
        lambda _event: priority.set_visibility(
            priority_mode.value == "insert"
        )
    )
    on_group_change()

    return FirewallRuleCreateFields(
        owner_type=owner_type,
        group=group,
        users=users_select,
        auto_source=auto_source,
        peer_lan=peer_lan,
        source=source,
        user_hint=user_hint,
        action=action,
        protocol=protocol,
        dest_ip=dest_ip,
        dest_port=dest_port,
        enabled=enabled,
        priority_mode=priority_mode,
        priority=priority,
        description=description,
        group_map=group_map,
        peer_preset_by_key=peer_preset_by_key,
        ccd_reader=ccd_reader,
        parse_user_source=parse_user_source,
    )
