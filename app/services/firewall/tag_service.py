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

    def set_members(self, tag_id: str, *, usernames=(), group_ids=()) -> None:
        self.store.set_tag_members(tag_id, usernames=usernames, group_ids=group_ids)

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
