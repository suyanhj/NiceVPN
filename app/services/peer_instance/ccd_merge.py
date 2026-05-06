# -*- coding: utf-8 -*-
"""CCD 中与对端实例相关的 iroute 块合并（保留 ifconfig-push 等其它行）"""
from __future__ import annotations

from ipaddress import ip_network


# 全网 mesh：各客户端 CCD 中 push route 块标记（与按 peer 划分的 iroute 块独立）
MESH_PUSH_BEGIN = "# --- ovpn-mesh-peer-routes begin ---\n"
MESH_PUSH_END = "# --- ovpn-mesh-peer-routes end ---\n"


def peer_ccd_block_markers(peer_id: str) -> tuple[str, str]:
    begin = f"# --- ovpn-peer begin {peer_id} ---\n"
    end = f"# --- ovpn-peer end {peer_id} ---\n"
    return begin, end


def build_iroute_block(peer_id: str, lan_cidrs: list[str]) -> str:
    """生成带标记的 iroute 块（OpenVPN iroute 使用网络地址 + 点分掩码）。"""
    begin, end = peer_ccd_block_markers(peer_id)
    lines = [begin]
    for cidr in lan_cidrs:
        s = str(cidr).strip()
        if not s:
            continue
        net = ip_network(s, strict=False)
        if net.version != 4:
            continue
        lines.append(f"iroute {net.network_address} {net.netmask}\n")
    lines.append(end)
    return "".join(lines)


def merge_peer_block_into_ccd(existing_text: str, peer_id: str, lan_cidrs: list[str]) -> str:
    """从现有 CCD 全文移除该 peer_id 旧块，再写入新 iroute 块。

    - 正文内**尚未**出现 ``ovpn-mesh-peer-routes`` 标记时：新块追加在全文末尾（与历史行为一致）。
    - 若已存在 mesh 段：新 iroute **插在 mesh 块之前**，与 ``merge_mesh_peer_push_routes_into_ccd`` 的「mesh 在最后」约定一致，
      避免绑定用户在一次保存内先后执行本函数与 mesh 合并时出现 peer/mesh 顺序来回抖动、反复写盘。
    """
    begin, end = peer_ccd_block_markers(peer_id)
    lines = existing_text.splitlines(keepends=True)
    out: list[str] = []
    skip = False
    for line in lines:
        if line.startswith(begin.rstrip("\n")):
            skip = True
            continue
        if line.startswith(end.rstrip("\n")):
            skip = False
            continue
        if not skip:
            out.append(line)
    base = "".join(out).rstrip()
    if base and not base.endswith("\n"):
        base += "\n"
    if not lan_cidrs:
        return base
    block = build_iroute_block(peer_id, lan_cidrs)
    idx = base.find(MESH_PUSH_BEGIN)
    if idx >= 0:
        return base[:idx] + block + base[idx:]
    return base + block


def strip_peer_block_from_ccd(existing_text: str, peer_id: str) -> str:
    """仅删除对端块，不追加新内容。"""
    return merge_peer_block_into_ccd(existing_text, peer_id, [])


def mesh_peer_push_route_markers() -> tuple[str, str]:
    return MESH_PUSH_BEGIN, MESH_PUSH_END


def sorted_unique_ipv4_cidrs(cidrs: list[str]) -> list[str]:
    """去重、排序后的 IPv4 CIDR 字符串列表（用于稳定生成 push / 文档块）。"""
    keys: list[str] = []
    seen: set[str] = set()
    for raw in cidrs or []:
        s = str(raw).strip()
        if not s:
            continue
        net = ip_network(s, strict=False)
        if net.version != 4:
            continue
        canon = str(net)
        if canon not in seen:
            seen.add(canon)
            keys.append(canon)
    keys.sort(key=lambda x: ip_network(x, strict=False))
    return keys


def build_mesh_peer_push_routes_block(cidrs: list[str]) -> str:
    """生成带标记的 push route 块（OpenVPN CCD 中 push 使用网络地址 + 点分掩码）。"""
    normalized = sorted_unique_ipv4_cidrs(cidrs)
    if not normalized:
        return ""
    lines = [MESH_PUSH_BEGIN]
    for c in normalized:
        net = ip_network(c, strict=False)
        lines.append(f'push "route {net.network_address} {net.netmask}"\n')
    lines.append(MESH_PUSH_END)
    return "".join(lines)


def merge_mesh_peer_push_routes_into_ccd(existing_text: str, cidrs: list[str]) -> str:
    """移除旧 mesh push 块；若 cidrs 非空则在文末追加新块。"""
    begin, end = mesh_peer_push_route_markers()
    begin_prefix = begin.rstrip("\n")
    end_prefix = end.rstrip("\n")
    lines = existing_text.splitlines(keepends=True)
    out: list[str] = []
    skip = False
    for line in lines:
        if line.startswith(begin_prefix):
            skip = True
            continue
        if line.startswith(end_prefix):
            skip = False
            continue
        if not skip:
            out.append(line)
    base = "".join(out).rstrip()
    if base and not base.endswith("\n"):
        base += "\n"
    block = build_mesh_peer_push_routes_block(cidrs)
    if not block:
        return base
    return base + block
