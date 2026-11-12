# -*- coding: utf-8 -*-
"""远端对端防火墙中的动态标签规则展开与同步。"""
from __future__ import annotations

import logging
import threading
from ipaddress import IPv4Network, ip_network
from typing import Any, Iterable

from app.services.firewall.tag_service import TagService
from app.services.peer_instance import remote_chain_cache
from app.services.peer_instance.service import PeerService

logger = logging.getLogger(__name__)
_REMOTE_TAG_SYNC_LOCK = threading.Lock()


def _tag_binding(row: dict[str, Any]) -> dict[str, Any] | None:
    value = row.get("tag_binding")
    return value if isinstance(value, dict) and str(value.get("tag_id") or "").strip() else None


def has_remote_tag_rule_references(tag_ids: Iterable[str], *, store=None) -> bool:
    """检查本地远端链工作副本是否存在引用指定标签的逻辑规则。"""
    wanted = {str(value).strip() for value in tag_ids if str(value).strip()}
    if not wanted:
        return False
    peers = PeerService(store=store).list_all()
    for peer in peers:
        peer_id = str(peer.get("id") or "").strip()
        if not peer_id:
            continue
        cache = remote_chain_cache.read_remote_chain_cache(peer_id)
        if not cache:
            continue
        if any(
            str((_tag_binding(row) or {}).get("tag_id") or "") in wanted
            for row in (cache.get("rows") or [])
        ):
            return True
    return False


def _minimal_source_addresses(addresses: Iterable[str]) -> list[str]:
    """折叠被更大 CIDR 覆盖的单 IP/子网，避免组标签产生大量冗余规则。"""
    networks: set[IPv4Network] = set()
    for raw in addresses:
        value = str(raw or "").strip()
        if not value:
            continue
        network = ip_network(value, strict=False)
        if network.version != 4:
            raise ValueError(f"远端标签规则暂不支持 IPv6 源地址: {value}")
        networks.add(network)

    minimal: list[IPv4Network] = []
    for network in sorted(
        networks,
        key=lambda item: (item.prefixlen, int(item.network_address)),
    ):
        if any(network.subnet_of(parent) for parent in minimal):
            continue
        minimal.append(network)

    return [
        str(network.network_address)
        if network.prefixlen == 32
        else str(network)
        for network in minimal
    ]


def materialize_remote_chain_rows(
    rows: list[dict[str, Any]],
    *,
    tag_service: TagService | None = None,
) -> list[str]:
    """把逻辑标签规则按当前成员地址展开为远端 iptables rest 列表。

    标签规则在本地缓存中保持一行逻辑记录；每次真正推送时重新读取成员，
    因此成员增删、切组、CCD IP 变化不会依赖创建规则时的静态地址快照。
    空标签不生成任何规则，避免退化为无源地址限制的 ACCEPT。
    """
    from app.services.firewall.simple_rule_import import remote_rests_from_create_fields

    tags = tag_service or TagService()
    rests: list[str] = []
    addresses_by_tag: dict[str, list[str]] = {}
    ccd_ips: dict[str, str] | None = None
    if any(_tag_binding(row) for row in rows if isinstance(row, dict)) and hasattr(tags, "store"):
        # 一次扫描 CCD，多个标签共用快照，避免每条标签规则重复遍历目录。
        from app.services.user.crud import UserService
        ccd_ips = UserService(store=tags.store).list_ccd_virtual_ipv4_by_username()

    for row in rows:
        if not isinstance(row, dict) or not bool(row.get("enabled", True)):
            continue
        binding = _tag_binding(row)
        if binding is None:
            raw = str(row.get("rest") or "").strip()
            if raw:
                rests.append(raw)
            continue

        tag_id = str(binding.get("tag_id") or "").strip()
        if tag_id not in addresses_by_tag:
            try:
                if ccd_ips is None:
                    resolved = tags.resolve_addresses(tag_id)
                else:
                    resolved = tags.resolve_addresses(tag_id, ccd_ips=ccd_ips)
                addresses_by_tag[tag_id] = _minimal_source_addresses(resolved)
            except ValueError as exc:
                raise RuntimeError(f"远端标签规则引用的标签或地址无效: {tag_id}: {exc}") from exc
        addresses = addresses_by_tag[tag_id]
        if not addresses:
            logger.info("远端标签规则当前无有效成员，跳过下发 tag=%s", tag_id[:8])
            continue
        rests.extend(
            remote_rests_from_create_fields(
                source_subnet=None,
                source_ips=list(addresses),
                action=str(binding.get("action") or "accept"),
                protocol=str(binding.get("protocol") or "all"),
                dest_ip=str(binding.get("dest_ip") or "").strip() or None,
                dest_port=str(binding.get("dest_port") or "").strip() or None,
            )
        )
    return rests


def mark_remote_tag_rules_pending(tag_ids: Iterable[str], *, store=None) -> list[str]:
    """先持久化待同步状态，进程重启时也不会把远端滞后伪装成已同步。"""
    wanted = {str(value).strip() for value in tag_ids if str(value).strip()}
    if not wanted:
        return []
    pending_peers: list[str] = []
    for peer in PeerService(store=store).list_all():
        peer_id = str(peer.get("id") or "").strip()
        if not peer_id:
            continue
        cache = remote_chain_cache.read_remote_chain_cache(peer_id)
        if not cache:
            continue
        rows = list(cache.get("rows") or [])
        if not any(
            bool(row.get("enabled", True))
            and str((_tag_binding(row) or {}).get("tag_id") or "") in wanted
            for row in rows
        ):
            continue
        remote_chain_cache.write_remote_chain_cache(
            peer_id,
            chain=str(cache.get("chain") or ""),
            chain_exists=bool(cache.get("chain_exists", True)),
            rows=rows,
            pending_sync=True,
            last_sync_error="标签成员发生变化，等待远端防火墙刷新",
        )
        pending_peers.append(peer_id)
    return pending_peers


def sync_remote_tag_firewall_rules(tag_ids: Iterable[str], *, store=None) -> dict[str, Any]:
    """按当前标签成员重推所有引用这些标签的远端防火墙工作副本。"""
    wanted = {str(value).strip() for value in tag_ids if str(value).strip()}
    peer_service = PeerService(store=store)
    tag_service = TagService(store=store)
    results: dict[str, Any] = {"updated": [], "failed": [], "skipped": []}

    for peer in peer_service.list_all():
        peer_id = str(peer.get("id") or "").strip()
        if not peer_id:
            continue
        cache = remote_chain_cache.read_remote_chain_cache(peer_id)
        if not cache:
            continue
        rows = list(cache.get("rows") or [])
        if not any(
            str((_tag_binding(row) or {}).get("tag_id") or "") in wanted
            for row in rows
        ):
            continue

        chain = str(cache.get("chain") or "")
        chain_exists = bool(cache.get("chain_exists", True))
        prepared = False
        try:
            remote_chain_cache.mark_pending_apply(
                peer_id, chain, chain_exists, rows,
            )
            prepared = True
            rests = materialize_remote_chain_rows(rows, tag_service=tag_service)
            peer_service.apply_remote_peer_filter_chain_rests(peer_id, rests)
            remote_chain_cache.record_after_apply(
                peer_id, chain, chain_exists, rows, True, None,
            )
            results["updated"].append(peer_id)
            logger.info(
                "远端标签防火墙已按最新成员同步 peer=%s rules=%s",
                peer_id[:8], len(rests),
            )
        except Exception as exc:
            if prepared:
                remote_chain_cache.record_after_apply(
                    peer_id, chain, chain_exists, rows, False, str(exc),
                )
            results["failed"].append({"peer_id": peer_id, "error": str(exc)})
            logger.warning(
                "远端标签防火墙同步失败 peer=%s；工作副本已标记待同步: %s",
                peer_id[:8], exc,
            )
    return results


def schedule_remote_tag_firewall_sync(tag_ids: Iterable[str], *, store=None) -> bool:
    """异步安排远端标签规则同步，避免标签管理页面等待 SSH。"""
    wanted = sorted({str(value).strip() for value in tag_ids if str(value).strip()})
    if not wanted or not has_remote_tag_rule_references(wanted, store=store):
        return False
    pending_peers = mark_remote_tag_rules_pending(wanted, store=store)
    if not pending_peers:
        return False

    def worker() -> None:
        with _REMOTE_TAG_SYNC_LOCK:
            try:
                sync_remote_tag_firewall_rules(wanted, store=store)
            except Exception:
                logger.exception("后台远端标签防火墙同步异常 tags=%s", len(wanted))

    threading.Thread(
        target=worker,
        name="nicevpn-remote-tag-firewall-sync",
        daemon=True,
    ).start()
    logger.info(
        "已安排远端标签防火墙刷新 tags=%s peers=%s",
        len(wanted), len(pending_peers),
    )
    return True
