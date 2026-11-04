# -*- coding: utf-8 -*-
"""防火墙标签：持久化关联与运行时源地址解析。

标签本身不是地址快照。每次防火墙刷新时按当前用户 CCD IP、当前组网段
重新计算成员，保证 ipset swap 使用完整期望状态而非增量 append。
"""
from __future__ import annotations

import uuid

from app.storage.sqlite_store import SqliteStore, get_default_store
from app.services.firewall.ipset_mgr import IpsetManager


class TagService:
    def __init__(self, store: SqliteStore | None = None):
        self.store = store or get_default_store()

    def create(self, name: str, description: str = "") -> dict:
        tag_id = str(uuid.uuid4())
        self.store.create_tag(tag_id, name, description)
        return self.store.get_tag(tag_id) or {}

    def list_all(self) -> list[dict]:
        return self.store.list_tags()

    def set_members(self, tag_id: str, *, usernames=(), group_ids=(), refresh_kernel: bool = True) -> None:
        """更新关联后刷新内核；失败时恢复 SQLite 原关联。"""
        previous = self.store.get_tag_members(tag_id)
        self.store.set_tag_members(tag_id, usernames=usernames, group_ids=group_ids)
        if not refresh_kernel:
            return
        try:
            from app.services.firewall.rule_service import FirewallRuleService
            FirewallRuleService(store=self.store).refresh_vpn_forward_only()
        except Exception:
            self.store.set_tag_members(tag_id, **previous)
            raise

    def set_entity_tags(
        self, entity_type: str, entity_id: str, tag_ids,
        *, refresh_kernel: bool = True,
    ) -> None:
        """替换某用户或组的标签（保留其它实体的标签关联）。"""
        if entity_type not in ("user", "group"):
            raise ValueError("仅支持 user/group")
        ids = {str(v) for v in tag_ids}
        tags = {row["id"] for row in self.store.list_tags()}
        if not ids.issubset(tags):
            raise ValueError("存在未知标签")
        changes: list[tuple[str, dict[str, list[str]]]] = []
        try:
            for tag_id in sorted(tags):
                previous = self.store.get_tag_members(tag_id)
                members = {key: list(values) for key, values in previous.items()}
                key = "usernames" if entity_type == "user" else "group_ids"
                current = set(members[key])
                desired = (current | {entity_id}) if tag_id in ids else (current - {entity_id})
                if desired == current:
                    continue
                members[key] = sorted(desired)
                self.store.set_tag_members(tag_id, **members)
                changes.append((tag_id, previous))
            if refresh_kernel and changes:
                from app.services.firewall.rule_service import FirewallRuleService
                FirewallRuleService(store=self.store).refresh_vpn_forward_only()
        except Exception:
            for tag_id, previous in reversed(changes):
                self.store.set_tag_members(tag_id, **previous)
            raise

    def get_entity_tag_ids(self, entity_type: str, entity_id: str) -> list[str]:
        if entity_type not in ("user", "group"):
            raise ValueError("仅支持 user/group")
        key = "usernames" if entity_type == "user" else "group_ids"
        return [
            tag["id"] for tag in self.store.list_tags()
            if entity_id in self.store.get_tag_members(tag["id"])[key]
        ]

    def refresh_if_referenced(self, *, entity_type: str, entity_id: str, other_group_id: str = "") -> None:
        """用户/组状态或地址变动后，仅在存在受影响标签规则时刷新。"""
        relevant = set(self.get_entity_tag_ids(entity_type, entity_id))
        if entity_type == "user":
            user = self.store.get_user(entity_id)
            if user:
                relevant.update(self.get_entity_tag_ids("group", str(user.get("group_id") or "")))
            if other_group_id:
                relevant.update(self.get_entity_tag_ids("group", other_group_id))
        if not relevant:
            return
        active_refs = {
            str(rule.get("owner_id") or "")
            for rule in self.store.list_firewall_rules()
            if rule.get("owner_type") == "tag" and rule.get("enabled", True)
        }
        if not (active_refs & relevant):
            return
        from app.services.firewall.rule_service import FirewallRuleService
        FirewallRuleService(store=self.store).refresh_vpn_forward_only()

    def resolve_addresses(self, tag_id: str, *, ccd_ips: dict[str, str] | None = None) -> tuple[str, ...]:
        """合并直接关联用户和关联组下的用户虚拟 IP，以及当前组 CIDR。

        只采集 active 用户/组，且只读现有 CCD，不猜测/分配新的地址。
        """
        if self.store.get_tag(tag_id) is None:
            raise ValueError(f"标签不存在: {tag_id}")
        members = self.store.get_tag_members(tag_id)
        ip_by_user = ccd_ips
        if ip_by_user is None:
            from app.services.user.crud import UserService
            ip_by_user = UserService(store=self.store).list_ccd_virtual_ipv4_by_username()
        usernames = set(members["usernames"])
        addresses: list[str] = []
        for group_id in members["group_ids"]:
            group = self.store.get_group(group_id)
            if not group or str(group.get("status", "active")) != "active":
                continue
            subnet = str(group.get("subnet") or "").strip()
            if subnet:
                addresses.append(subnet)
            # 加入组内活动用户的 CCD 地址，便于组子网与 CCD 地址不一致时仍可覆盖。
            usernames.update(self.store.list_active_usernames_by_group(group_id))
        for username in sorted(usernames):
            user = self.store.get_user(username)
            if user and str(user.get("status", "active")) == "active":
                address = ip_by_user.get(username)
                if address:
                    addresses.append(address)
        return IpsetManager.normalize_entries(addresses)
