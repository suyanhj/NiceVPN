# -*- coding: utf-8 -*-
"""对端远端防火墙动态标签 ipset 的声明式生命周期管理。

每个对端使用独立命名空间；集合名同时包含成员快照摘要。更新时先创建并填充新集合，
iptables-restore 原子切换链规则，成功后再回收旧集合。这样不会在更新失败时破坏旧链引用。
"""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
import shlex
import uuid
from dataclasses import dataclass
from typing import Callable, Iterable

from app.services.openvpn.detector import detect_distro_family, parse_os_release_text

logger = logging.getLogger("peer.remote.ipset")

_STABLE_PREFIX = "nvpnpt_"
_STAGE_PREFIX = "nvpnps_"
_MAX_NAME_LENGTH = 31
_MAXELEM_MIN = 65536


@dataclass(frozen=True)
class RemoteIpsetSpec:
    """一个对端标签集合的完整期望成员。"""

    name: str
    entries: tuple[str, ...]


def peer_tag_ipset_prefix(peer_id: str) -> str:
    pid = str(peer_id or "").strip()
    if not pid:
        raise ValueError("peer_id 不能为空")
    return f"{_STABLE_PREFIX}{hashlib.sha1(pid.encode('utf-8')).hexdigest()[:10]}_"


def peer_tag_ipset_stage_prefix(peer_id: str) -> str:
    pid = str(peer_id or "").strip()
    if not pid:
        raise ValueError("peer_id 不能为空")
    return f"{_STAGE_PREFIX}{hashlib.sha1(pid.encode('utf-8')).hexdigest()[:10]}_"


def peer_tag_ipset_name(peer_id: str, tag_id: str, entries: Iterable[str]) -> str:
    """内容寻址命名：同一对端、标签、成员快照得到稳定名称，变化则生成新集合。"""
    tid = str(tag_id or "").strip()
    if not tid:
        raise ValueError("tag_id 不能为空")
    normalized: list[str] = []
    for raw in entries:
        token = str(raw or "").strip()
        if not token:
            continue
        network = ipaddress.ip_network(token, strict=False)
        if network.version != 4:
            raise ValueError(f"对端标签 ipset 暂不支持 IPv6: {token}")
        normalized.append(str(network))
    normalized = sorted(set(normalized))
    if not normalized:
        raise ValueError("不能为无成员标签创建 ipset")
    digest_source = tid + "\\0" + "\\n".join(normalized)
    digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:12]
    name = f"{peer_tag_ipset_prefix(peer_id)}{digest}"
    if len(name) > _MAX_NAME_LENGTH:
        raise ValueError(f"对端标签 ipset 名称过长: {name}")
    return name


def build_remote_tag_ipset_spec(
    peer_id: str, tag_id: str, entries: Iterable[str],
) -> RemoteIpsetSpec:
    normalized = tuple(sorted({
        str(ipaddress.ip_network(str(raw).strip(), strict=False))
        for raw in entries if str(raw or "").strip()
    }))
    name = peer_tag_ipset_name(peer_id, tag_id, normalized)
    return RemoteIpsetSpec(name=name, entries=normalized)


def _stage_name(peer_id: str) -> str:
    name = (
        f"{peer_tag_ipset_stage_prefix(peer_id)}"
        f"{uuid.uuid4().hex[:8]}"
    )
    if len(name) > _MAX_NAME_LENGTH:
        raise RuntimeError(f"对端 ipset staging 名称过长: {name}")
    return name


def _run(exec_ssh: Callable, client, command: str, timeout: int) -> tuple[str, str, int]:
    return exec_ssh(client, command, timeout=timeout)


def _ensure_ipset_installed(
    client,
    sudo_prefix: str,
    exec_ssh: Callable,
    *,
    timeout: int,
) -> None:
    _, _, code = _run(
        exec_ssh, client, f"{sudo_prefix}command -v ipset >/dev/null 2>&1", timeout,
    )
    if code == 0:
        return

    release, err, release_code = _run(
        exec_ssh, client, "cat /etc/os-release 2>/dev/null || true", timeout,
    )
    if release_code != 0 or not release.strip():
        raise RuntimeError(
            f"对端缺少 ipset，且无法读取发行版信息: {(err or '').strip()}"
        )
    info = parse_os_release_text(release)
    family = detect_distro_family(info)
    if family == "debian":
        install = "apt-get update -qq && apt-get install -y ipset"
    elif family == "rhel":
        install = "dnf install -y ipset || yum install -y ipset"
    else:
        raise RuntimeError(
            "对端未安装 ipset，自动安装暂不支持该发行版；"
            f"请手动安装 ipset（id={info.get('id')!r}）"
        )
    out, install_err, install_code = _run(
        exec_ssh, client, f"{sudo_prefix}{install}", timeout=max(timeout, 300),
    )
    if install_code != 0:
        raise RuntimeError(
            "对端 ipset 自动安装失败，请检查软件源和 sudo 权限："
            f"{(install_err or out or '').strip()[-1500:]}"
        )
    _, check_err, check_code = _run(
        exec_ssh, client, f"{sudo_prefix}command -v ipset >/dev/null 2>&1", timeout,
    )
    if check_code != 0:
        raise RuntimeError(
            f"对端 ipset 安装后仍不可用: {(check_err or '').strip()}"
        )
    logger.info("已在对端自动安装 ipset（peer SSH）")


def _list_names(client, sudo_prefix: str, exec_ssh: Callable, timeout: int) -> set[str]:
    out, err, code = _run(
        exec_ssh, client, f"{sudo_prefix}ipset list -n", timeout,
    )
    if code != 0:
        raise RuntimeError(f"读取对端 ipset 列表失败: {(err or out or '').strip()}")
    return {line.strip() for line in out.splitlines() if line.strip()}


def _destroy(client, sudo_prefix: str, name: str, exec_ssh: Callable, timeout: int) -> bool:
    _, err, code = _run(
        exec_ssh, client,
        f"{sudo_prefix}ipset destroy {shlex.quote(name)}",
        timeout,
    )
    if code == 0:
        return True
    message = (err or "").lower()
    if "does not exist" in message or "not exist" in message or "no such" in message:
        return True
    logger.warning("删除对端 ipset 失败 name=%s: %s", name, (err or "").strip())
    return False


def reconcile_remote_peer_tag_ipsets(
    client,
    sudo_prefix: str,
    peer_id: str,
    specs: Iterable[RemoteIpsetSpec],
    exec_ssh: Callable,
    *,
    timeout: int = 120,
) -> set[str]:
    """预创建本轮所需集合，返回本轮新建的 stable 名称。

    本函数不删除旧集合。调用方必须先成功应用引用这些集合的 iptables 链，
    然后调用 gc_remote_peer_tag_ipsets；iptables 应用失败则只清理本轮新建集合。
    """
    desired = list(specs)
    names = [spec.name for spec in desired]
    if len(names) != len(set(names)):
        raise ValueError("对端 ipset 计划中存在重复集合名")
    prefix = peer_tag_ipset_prefix(peer_id)
    for spec in desired:
        if not spec.name.startswith(prefix) or len(spec.name) > _MAX_NAME_LENGTH:
            raise ValueError(f"拒绝管理当前对端命名空间外的 ipset: {spec.name}")
        if not spec.entries:
            raise ValueError(f"对端 ipset 不能为空: {spec.name}")
        for entry in spec.entries:
            network = ipaddress.ip_network(str(entry), strict=False)
            if network.version != 4:
                raise ValueError(f"对端标签 ipset 暂不支持 IPv6: {entry}")

    if not desired:
        return set()

    _ensure_ipset_installed(client, sudo_prefix, exec_ssh, timeout=timeout)
    existing = _list_names(client, sudo_prefix, exec_ssh, timeout)
    newly_created: set[str] = set()
    stage_names: set[str] = set()
    try:
        for spec in desired:
            stable = spec.name
            stage = _stage_name(peer_id)
            stage_names.add(stage)
            maxelem = max(_MAXELEM_MIN, len(spec.entries) * 2)
            create = (
                f"{sudo_prefix}ipset create {shlex.quote(stage)} hash:net "
                f"family inet maxelem {maxelem}"
            )
            out, err, code = _run(exec_ssh, client, create, timeout)
            if code != 0:
                raise RuntimeError(
                    f"创建对端 ipset staging 失败 {stage}: {(err or out or '').strip()}"
                )
            for entry in spec.entries:
                command = (
                    f"{sudo_prefix}ipset add {shlex.quote(stage)} "
                    f"{shlex.quote(str(entry))} -exist"
                )
                out, err, code = _run(exec_ssh, client, command, timeout)
                if code != 0:
                    raise RuntimeError(
                        f"写入对端 ipset 成员失败 {entry}: {(err or out or '').strip()}"
                    )

            if stable not in existing:
                create_stable = (
                    f"{sudo_prefix}ipset create {shlex.quote(stable)} hash:net "
                    f"family inet maxelem {maxelem}"
                )
                out, err, code = _run(exec_ssh, client, create_stable, timeout)
                if code != 0:
                    raise RuntimeError(
                        f"创建对端 ipset stable 失败 {stable}: {(err or out or '').strip()}"
                    )
                existing.add(stable)
                newly_created.add(stable)

            out, err, code = _run(
                exec_ssh, client,
                f"{sudo_prefix}ipset swap {shlex.quote(stage)} {shlex.quote(stable)}",
                timeout,
            )
            if code != 0:
                raise RuntimeError(
                    f"切换对端 ipset 失败 {stable}: {(err or out or '').strip()}"
                )
            _destroy(client, sudo_prefix, stage, exec_ssh, timeout)
            stage_names.discard(stage)
    except Exception:
        for stage in stage_names:
            _destroy(client, sudo_prefix, stage, exec_ssh, timeout)
        for stable in newly_created:
            _destroy(client, sudo_prefix, stable, exec_ssh, timeout)
        raise

    return newly_created


def gc_remote_peer_tag_ipsets(
    client,
    sudo_prefix: str,
    peer_id: str,
    keep_names: Iterable[str],
    exec_ssh: Callable,
    *,
    timeout: int = 60,
) -> list[str]:
    """只回收当前对端命名空间内不再被规则引用的集合和遗留 staging。"""
    try:
        names = _list_names(client, sudo_prefix, exec_ssh, timeout)
    except Exception as exc:
        logger.warning("读取对端 ipset 进行 GC 失败 peer=%s: %s", peer_id[:8], exc)
        return []
    keep = {str(name) for name in keep_names}
    stable_prefix = peer_tag_ipset_prefix(peer_id)
    stage_prefix = peer_tag_ipset_stage_prefix(peer_id)
    removed: list[str] = []
    for name in sorted(names):
        if name.startswith(stage_prefix) or (
            name.startswith(stable_prefix) and name not in keep
        ):
            if _destroy(client, sudo_prefix, name, exec_ssh, timeout):
                removed.append(name)
    return removed


def cleanup_remote_peer_tag_ipsets(
    client,
    sudo_prefix: str,
    peer_id: str,
    exec_ssh: Callable,
    *,
    timeout: int = 60,
) -> list[str]:
    """删除对端实例时清理 NiceVPN 为该对端创建的所有标签集合。"""
    try:
        names = _list_names(client, sudo_prefix, exec_ssh, timeout)
    except Exception as exc:
        logger.info("对端未安装 ipset 或无法读取集合，跳过标签集合清理 peer=%s: %s", peer_id[:8], exc)
        return []
    prefixes = (peer_tag_ipset_prefix(peer_id), peer_tag_ipset_stage_prefix(peer_id))
    removed: list[str] = []
    for name in sorted(names):
        if name.startswith(prefixes):
            if _destroy(client, sudo_prefix, name, exec_ssh, timeout):
                removed.append(name)
    return removed


def referenced_peer_tag_ipsets(rests: Iterable[str], peer_id: str) -> set[str]:
    """保留手动导入的原始规则仍在引用的本对端 NiceVPN 集合。"""
    prefix = peer_tag_ipset_prefix(peer_id)
    found: set[str] = set()
    for rest in rests:
        for match in re.finditer(
            r"(?:--match-set|--set)\\s+([A-Za-z0-9_.-]{1,31})",
            str(rest or ""),
        ):
            name = match.group(1)
            if name.startswith(prefix):
                found.add(name)
    return found
