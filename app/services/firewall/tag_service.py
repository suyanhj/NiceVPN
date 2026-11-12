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

    def create(self, name: str, description: str = "", *, device_binding_exempt: bool = False, sso_required: bool = False) -> dict:
        tag_id = str(uuid.uuid4())
        self.store.create_tag(tag_id, name, description, device_binding_exempt=device_binding_exempt, sso_required=sso_required)
        return self.store.get_tag(tag_id) or {}

    def list_all(self) -> list[dict]:
        return self.store.list_tags()

    def update(self, tag_id: str, *, name: str, description: str = "", device_binding_exempt: bool | None = None, sso_required: bool | None = None) -> dict:
        self.store.update_tag(tag_id, name=name, description=description, device_binding_exempt=device_binding_exempt, sso_required=sso_required)
        return self.store.get_tag(tag_id) or {}

    def delete(self, tag_id: str) -> None:
        """拒绝删除仍有防火墙规则引用的标签，避免悬空规则。"""
        if self.store.get_tag(tag_id) is None:
            raise ValueError("标签不存在")
        referenced = [
            row for row in self.store.list_firewall_rules()
            if row.get("owner_type") == "tag" and str(row.get("owner_id")) == str(tag_id)
        ]
        if referenced:
            raise ValueError(f"标签仍被 {len(referenced)} 条中心防火墙规则引用，请先删除规则")
        peer_refs = self.list_peer_tag_references(tag_id)
        if peer_refs:
            reference_types = {
                str(item.get("reference_type") or "对端配置")
                for item in peer_refs
            }
            reference_labels = []
            if "Mesh 路由" in reference_types:
                reference_labels.append("对端路由(Mesh)")
            if "远端防火墙" in reference_types:
                reference_labels.append("远端防火墙规则")
            raise ValueError(
                f"标签仍被{'、'.join(reference_labels)}引用，请先移除对端引用后再删除"
            )
        self.store.delete_tag(tag_id)

    def _list_all_peer_tag_references(self) -> list[dict]:
        """读取对端 Mesh 标签范围和远端防火墙逻辑标签规则引用。"""
        from app.services.peer_instance import remote_chain_cache
        from app.services.peer_instance.service import PeerService

        references: list[dict] = []
        for peer in PeerService(store=self.store).list_all():
            peer_id = str(peer.get("id") or "").strip()
            if not peer_id:
                continue
            peer_name = str(peer.get("name") or peer_id)
            mesh_tag_ids = {
                str(value).strip()
                for value in (peer.get("mesh_route_visible_tag_ids") or [])
                if str(value).strip()
            }
            for tag_id in sorted(mesh_tag_ids):
                references.append({
                    "tag_id": tag_id,
                    "reference_type": "Mesh 路由",
                    "peer_id": peer_id,
                    "peer_name": peer_name,
                    "description": "该对端 LAN 路由按标签成员下发",
                    "enabled": True,
                })

            cache = remote_chain_cache.read_remote_chain_cache(peer_id)
            if not cache:
                continue
            for index, row in enumerate(cache.get("rows") or []):
                raw_binding = row.get("tag_binding") if isinstance(row, dict) else None
                binding = raw_binding if isinstance(raw_binding, dict) else None
                tag_id = str((binding or {}).get("tag_id") or "").strip()
                if not tag_id:
                    continue
                references.append({
                    "tag_id": tag_id,
                    "reference_type": "远端防火墙",
                    "peer_id": peer_id,
                    "peer_name": peer_name,
                    "row_index": index + 1,
                    "priority": int(row.get("priority") or (index + 1) * 10),
                    "description": str(row.get("description") or "").strip(),
                    "action": str(binding.get("action") or "accept").upper(),
                    "protocol": str(binding.get("protocol") or "all").upper(),
                    "dest_ip": str(binding.get("dest_ip") or "").strip(),
                    "dest_port": str(binding.get("dest_port") or "").strip(),
                    "enabled": bool(row.get("enabled", True)),
                })
        return references

    def peer_tag_reference_counts(self) -> dict[str, int]:
        """返回每个标签被 Mesh 路由和远端防火墙引用的数量。"""
        counts: dict[str, int] = {}
        for reference in self._list_all_peer_tag_references():
            tag_id = str(reference.get("tag_id") or "")
            if tag_id:
                counts[tag_id] = counts.get(tag_id, 0) + 1
        return counts

    def list_peer_tag_references(self, tag_id: str) -> list[dict]:
        """列出引用指定标签的对端路由与远端防火墙规则。"""
        wanted = str(tag_id or "").strip()
        if not wanted:
            return []
        return [
            reference
            for reference in self._list_all_peer_tag_references()
            if str(reference.get("tag_id") or "") == wanted
        ]

    def rule_count(self, tag_id: str) -> int:
        return sum(
            1 for row in self.store.list_firewall_rules()
            if row.get("owner_type") == "tag" and str(row.get("owner_id")) == str(tag_id)
        )

    def set_members(self, tag_id: str, *, usernames=(), group_ids=(), refresh_kernel: bool = True) -> None:
        """更新关联后刷新内核；失败时恢复 SQLite 原关联。"""
        previous = self.store.get_tag_members(tag_id)
        self.store.set_tag_members(tag_id, usernames=usernames, group_ids=group_ids)
        if not refresh_kernel:
            return
        try:
            self.refresh_for_tags([tag_id])
        except Exception:
            self.store.set_tag_members(tag_id, **previous)
            try:
                self.refresh_for_tags([tag_id])
            except Exception:
                import logging
                logging.getLogger(__name__).exception(
                    "标签关联回滚后重新收敛失败 tag=%s", str(tag_id)[:8],
                )
            raise

    def set_entity_tags(
        self, entity_type: str, entity_id: str, tag_ids,
        *, refresh_kernel: bool = True,
    ) -> None:
        """单事务替换关联；内核刷新失败则恢复修改前关联。"""
        before = self.store.list_entity_tag_ids(entity_type, entity_id)
        desired = sorted(set(str(value) for value in tag_ids))
        if before == desired:
            return
        self.store.replace_entity_tags(entity_type, entity_id, desired)
        if not refresh_kernel:
            return
        affected = set(before) | set(desired)
        try:
            self.refresh_for_tags(affected)
        except Exception:
            self.store.replace_entity_tags(entity_type, entity_id, before)
            try:
                self.refresh_for_tags(affected)
            except Exception:
                import logging
                logging.getLogger(__name__).exception(
                    "标签关联回滚后重新收敛失败 entity_type=%s entity_id=%s",
                    entity_type, str(entity_id)[:32],
                )
            raise

    def get_entity_tag_ids(self, entity_type: str, entity_id: str) -> list[str]:
        return self.store.list_entity_tag_ids(entity_type, entity_id)

    def batch_change_entities(
        self, entity_type: str, entity_ids, tag_ids, *, operation: str,
    ) -> int:
        """批量关联在单事务更新，并且只执行一次防火墙收敛。"""
        ids = sorted({str(v).strip() for v in entity_ids if str(v).strip()})
        tags = sorted({str(v).strip() for v in tag_ids if str(v).strip()})
        before = self.store.batch_change_entity_tags(
            entity_type, ids, tags, operation=operation,
        )
        affected = set(tags)
        for previous in before.values():
            affected.update(previous)
        try:
            self.refresh_for_tags(affected)
        except Exception:
            self.store.restore_entity_tag_snapshots(entity_type, before)
            try:
                self.refresh_for_tags(affected)
            except Exception:
                import logging
                logging.getLogger(__name__).exception(
                    "批量标签关联回滚后重新收敛失败 entity_type=%s",
                    entity_type,
                )
            raise
        return len(ids)

    def refresh_for_tags(self, tag_ids) -> None:
        """关联刚被级联删除时，也能按修改前的标签 ID 判断是否需要刷新。"""
        affected = set(tag_ids)
        if not affected:
            return
        active = {
            str(rule.get("owner_id") or "")
            for rule in self.store.list_firewall_rules()
            if rule.get("owner_type") == "tag" and rule.get("enabled", True)
        }
        if affected & active:
            from app.services.firewall.rule_service import FirewallRuleService
            FirewallRuleService(store=self.store).refresh_vpn_forward_only()
        self._refresh_mesh_routes_for_tags(affected)
        self._schedule_remote_firewall_refresh(affected)

    def _refresh_mesh_routes_for_tags(self, tag_ids) -> None:
        """仅当对端路由策略引用了这些标签时，重写用户 CCD 的 Mesh push 块。"""
        affected = {str(value).strip() for value in tag_ids if str(value).strip()}
        if not affected:
            return
        from app.services.peer_instance.service import PeerService
        peers = PeerService(store=self.store).list_all()
        if not any(
            affected & {
                str(value).strip()
                for value in (peer.get("mesh_route_visible_tag_ids") or [])
                if str(value).strip()
            }
            for peer in peers
        ):
            return
        PeerService(store=self.store).sync_all_mesh_push_routes_in_ccd()

    def _schedule_remote_firewall_refresh(self, tag_ids) -> None:
        """远端 SSH 写回在后台执行；失败保留远端工作副本的待同步状态。"""
        from app.services.firewall.remote_tag_rules import schedule_remote_tag_firewall_sync
        schedule_remote_tag_firewall_sync(tag_ids, store=self.store)

    def refresh_if_referenced(
        self, *, entity_type: str, entity_id: str,
        other_group_id: str = "", refresh_mesh_routes: bool = True,
    ) -> None:
        """用户/组状态或地址变动后，收敛中心/对端所有引用到的标签规则与路由。

        调用方若已因用户状态/切组变化执行过全量 Mesh CCD 同步，可关闭
        refresh_mesh_routes，避免同一事务重复扫描全部用户。
        """
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
        if active_refs & relevant:
            from app.services.firewall.rule_service import FirewallRuleService
            FirewallRuleService(store=self.store).refresh_vpn_forward_only()
        if refresh_mesh_routes:
            self._refresh_mesh_routes_for_tags(relevant)
        self._schedule_remote_firewall_refresh(relevant)

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
