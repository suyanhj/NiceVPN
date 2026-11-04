# -*- coding: utf-8 -*-
"""声明式 ipset 生命周期管理。

NiceVPN 只管理自己的命名空间。调用方负责提供完整的期望集合，本模块负责：
1. stable name：rule / peer / tag 使用稳定名称；
2. stage：先在临时集合写入完整成员；
3. swap：使用 ipset swap 原子替换 stable 集合内容；
4. rollback：iptables 应用失败时把 stable 集合恢复到旧内容；
5. gc：仅删除 NiceVPN 命名空间内、不再处于期望状态的孤儿集合；
6. reconcile：把内核实际状态收敛到调用方给出的期望状态。

reconcile 本身不会立即销毁旧内容。旧 stable 内容会暂存在 staging 集合中，
直到调用方确认 iptables 已成功应用后再 commit；因此规则和集合可以作为一个事务回滚。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import ipaddress
import logging
import subprocess
import uuid
from collections.abc import Iterable
from typing import Callable

logger = logging.getLogger(__name__)

IPSET_MAX_NAME_LENGTH = 31

# 现有命名空间继续保留，避免本轮重构导致无必要的大规模迁名。
IPSET_PREFIX = "ovpnfw_"
IPSET_PREFIX_PEER = "ovpnpeer_"
IPSET_PREFIX_TAG = "ovpntag_"
IPSET_STAGE_PREFIX = "ovpnstg_"

MANAGED_STABLE_PREFIXES = (
    IPSET_PREFIX,
    IPSET_PREFIX_PEER,
    IPSET_PREFIX_TAG,
)
MANAGED_PREFIXES = MANAGED_STABLE_PREFIXES + (IPSET_STAGE_PREFIX,)


@dataclass(frozen=True)
class IpsetSpec:
    """一个 stable ipset 的完整期望状态。"""

    name: str
    entries: tuple[str, ...]
    owner_type: str
    owner_id: str
    purpose: str
    set_type: str = "hash:net"
    family: str = "inet"


@dataclass(frozen=True)
class IpsetSwap:
    """一次 stable <-> stage 交换记录，供 commit / rollback 使用。"""

    stable_name: str
    stage_name: str
    stable_was_created: bool


@dataclass
class IpsetReconcileTransaction:
    """reconcile 后、commit 前的事务状态。"""

    desired_names: set[str]
    swaps: list[IpsetSwap] = field(default_factory=list)
    committed: bool = False
    rolled_back: bool = False


class IpsetManager:
    """NiceVPN ipset 的声明式 reconcile 管理器。"""

    def __init__(
        self,
        runner: Callable[..., subprocess.CompletedProcess] | None = None,
    ) -> None:
        self._runner = runner or subprocess.run

    @classmethod
    def stable_name(cls, owner_type: str, owner_id: str, purpose: str = "src") -> str:
        """按 owner + purpose 生成稳定且不超过 31 字符的集合名。

        rule / peer 延续当前线上名称算法，保证升级后现有 iptables 引用可直接复用。
        tag 命名空间预留给下一阶段标签系统。
        """
        ot = str(owner_type or "").strip().lower()
        oid = str(owner_id or "").strip()
        pur = str(purpose or "").strip().lower()
        if not oid:
            raise ValueError("ipset owner_id 不能为空")
        if not pur:
            raise ValueError("ipset purpose 不能为空")

        if ot == "rule":
            digest = hashlib.md5(f"{oid}_{pur}".encode("utf-8")).hexdigest()[:20]
            name = f"{IPSET_PREFIX}{pur[0]}{digest}"
        elif ot == "peer":
            # 与历史 _peer_lan_ipset_name(peer_id) 完全兼容。
            if pur == "lan":
                digest = hashlib.md5(f"peer_lan_{oid}".encode("utf-8")).hexdigest()[:20]
                name = f"{IPSET_PREFIX_PEER}l{digest}"
            else:
                digest = hashlib.md5(f"peer_{pur}_{oid}".encode("utf-8")).hexdigest()[:20]
                name = f"{IPSET_PREFIX_PEER}{pur[0]}{digest}"
        elif ot == "tag":
            digest = hashlib.md5(f"tag_{oid}_{pur}".encode("utf-8")).hexdigest()[:20]
            name = f"{IPSET_PREFIX_TAG}{pur[0]}{digest}"
        else:
            raise ValueError(f"不支持的 ipset owner_type: {owner_type}")

        if len(name) > IPSET_MAX_NAME_LENGTH:
            raise ValueError(f"ipset 名称过长: {name}")
        return name

    @classmethod
    def spec(
        cls,
        owner_type: str,
        owner_id: str,
        purpose: str,
        entries: Iterable[str],
    ) -> IpsetSpec:
        """构造规范化后的期望集合。"""
        normalized = cls.normalize_entries(entries)
        return IpsetSpec(
            name=cls.stable_name(owner_type, owner_id, purpose),
            entries=normalized,
            owner_type=str(owner_type).strip().lower(),
            owner_id=str(owner_id).strip(),
            purpose=str(purpose).strip().lower(),
        )

    @staticmethod
    def normalize_entries(entries: Iterable[str]) -> tuple[str, ...]:
        """规范化 IPv4 IP/CIDR，去重并稳定排序。

        单 IP 统一转为 /32；CIDR 使用网络地址，避免相同语义出现多个文本形式。
        """
        networks: dict[tuple[int, int], str] = {}
        for raw in entries:
            token = str(raw or "").strip()
            if not token:
                continue
            try:
                network = ipaddress.ip_network(token, strict=False)
            except ValueError as exc:
                raise ValueError(f"非法 ipset IPv4 地址/CIDR: {token}") from exc
            if network.version != 4:
                raise ValueError(f"当前 ipset 仅支持 IPv4: {token}")
            key = (int(network.network_address), network.prefixlen)
            networks[key] = str(network)
        return tuple(networks[key] for key in sorted(networks))

    @staticmethod
    def is_managed_name(name: str) -> bool:
        return any(str(name).startswith(prefix) for prefix in MANAGED_PREFIXES)

    @staticmethod
    def is_stable_managed_name(name: str) -> bool:
        return any(str(name).startswith(prefix) for prefix in MANAGED_STABLE_PREFIXES)

    @staticmethod
    def _stage_name(stable_name: str) -> str:
        digest = hashlib.sha1(stable_name.encode("utf-8")).hexdigest()[:12]
        nonce = uuid.uuid4().hex[:8]
        name = f"{IPSET_STAGE_PREFIX}{digest}_{nonce}"
        if len(name) > IPSET_MAX_NAME_LENGTH:
            raise RuntimeError(f"staging ipset 名称过长: {name}")
        return name

    def list_names(self) -> set[str]:
        result = self._run(["ipset", "list", "-n"], check=False, timeout=10)
        if result.returncode != 0:
            raise RuntimeError(
                "ipset list -n 失败: " + self._stderr(result)
            )
        return {line.strip() for line in (result.stdout or "").splitlines() if line.strip()}

    def reconcile(self, specs: Iterable[IpsetSpec]) -> IpsetReconcileTransaction:
        """把所有期望集合 stage + swap 到 stable 名称。

        成功后必须由调用方二选一：
        - iptables 应用成功：commit(txn)
        - iptables 应用失败：rollback(txn)

        这里不做 GC，避免旧 iptables 规则仍引用即将淘汰的集合。
        """
        desired = list(specs)
        names = [spec.name for spec in desired]
        if len(names) != len(set(names)):
            raise ValueError("reconcile 中存在重复 ipset stable name")
        for spec in desired:
            self._validate_spec(spec)

        existing = self.list_names()
        txn = IpsetReconcileTransaction(desired_names=set(names))

        try:
            for spec in desired:
                stage_name = self._stage_name(spec.name)
                stable_existed = spec.name in existing

                self._create_empty(
                    stage_name,
                    set_type=spec.set_type,
                    family=spec.family,
                    exist_ok=False,
                )
                try:
                    for entry in spec.entries:
                        self._run(
                            ["ipset", "add", stage_name, entry, "-exist"],
                            timeout=10,
                        )

                    if not stable_existed:
                        self._create_empty(
                            spec.name,
                            set_type=spec.set_type,
                            family=spec.family,
                            exist_ok=True,
                        )
                        existing.add(spec.name)

                    self._run(["ipset", "swap", stage_name, spec.name], timeout=10)
                except Exception:
                    # swap 之前失败：stage 可以直接删除；若 stable 是本轮临时创建，
                    # 且尚未产生 swap 记录，则恢复到“不存在”的初始状态。
                    self._destroy_best_effort(stage_name)
                    if not stable_existed:
                        self._destroy_best_effort(spec.name)
                        existing.discard(spec.name)
                    raise

                txn.swaps.append(
                    IpsetSwap(
                        stable_name=spec.name,
                        stage_name=stage_name,
                        stable_was_created=not stable_existed,
                    )
                )
        except Exception:
            ok = self.rollback(txn)
            if not ok:
                logger.critical("ipset reconcile 失败且回滚不完整，请人工检查 ipset")
            raise

        return txn

    def commit(self, txn: IpsetReconcileTransaction) -> bool:
        """确认 reconcile 结果，并清理 stage 与孤儿 stable 集合。

        GC 失败不会反向破坏已经成功的 iptables 数据面；返回 False 并记录日志，
        后续任意一次 reconcile 会再次尝试收敛。
        """
        if txn.committed:
            return True
        if txn.rolled_back:
            logger.error("不能 commit 已 rollback 的 ipset 事务")
            return False

        ok = True
        for swap in txn.swaps:
            if not self._destroy_best_effort(swap.stage_name):
                ok = False

        if not self.gc(txn.desired_names):
            ok = False

        txn.committed = True
        return ok

    def rollback(self, txn: IpsetReconcileTransaction) -> bool:
        """逆序 swap 回旧内容，并恢复 reconcile 前 stable 集合是否存在的状态。"""
        if txn.rolled_back:
            return True
        if txn.committed:
            logger.error("不能 rollback 已 commit 的 ipset 事务")
            return False

        ok = True
        for swap in reversed(txn.swaps):
            try:
                self._run(
                    ["ipset", "swap", swap.stage_name, swap.stable_name],
                    timeout=10,
                )
            except Exception as exc:
                ok = False
                logger.critical(
                    "ipset rollback swap 失败 stable=%s stage=%s: %s",
                    swap.stable_name,
                    swap.stage_name,
                    exc,
                )
                # stage 里可能仍保存旧内容，保留现场供人工恢复。
                continue

            if swap.stable_was_created:
                if not self._destroy_best_effort(swap.stable_name):
                    ok = False
            if not self._destroy_best_effort(swap.stage_name):
                ok = False

        txn.rolled_back = True
        return ok

    def gc(self, desired_names: Iterable[str]) -> bool:
        """删除 NiceVPN 命名空间中的孤儿 stable 集合与遗留 stage 集合。"""
        desired = {str(name).strip() for name in desired_names if str(name).strip()}
        try:
            actual = self.list_names()
        except RuntimeError as exc:
            logger.warning("ipset GC 无法枚举集合: %s", exc)
            return False

        ok = True
        for name in sorted(actual):
            if name.startswith(IPSET_STAGE_PREFIX):
                if not self._destroy_best_effort(name):
                    ok = False
                continue
            if self.is_stable_managed_name(name) and name not in desired:
                if not self._destroy_best_effort(name):
                    ok = False
        return ok

    def create_set(self, name: str, subnet: str) -> bool:
        """兼容旧接口；新业务应使用 spec + reconcile。"""
        try:
            self._create_empty(name, set_type="hash:net", family="inet", exist_ok=True)
            entry = self.normalize_entries([subnet])[0]
            self._run(["ipset", "add", name, entry, "-exist"], timeout=10)
            return True
        except (RuntimeError, ValueError) as exc:
            logger.error("创建 ipset 集合 %s 失败: %s", name, exc)
            return False

    def update_set(self, name: str, subnet: str) -> bool:
        """兼容旧接口；通过 stage/swap 原子替换单集合内容，不执行全局 GC。"""
        stage = self._stage_name(name)
        stable_existed = False
        try:
            stable_existed = name in self.list_names()
            self._create_empty(stage, set_type="hash:net", family="inet", exist_ok=False)
            entry = self.normalize_entries([subnet])[0]
            self._run(["ipset", "add", stage, entry, "-exist"], timeout=10)
            if not stable_existed:
                self._create_empty(name, set_type="hash:net", family="inet", exist_ok=True)
            self._run(["ipset", "swap", stage, name], timeout=10)
            self._destroy_best_effort(stage)
            return True
        except (RuntimeError, ValueError) as exc:
            logger.error("更新 ipset 集合 %s 失败: %s", name, exc)
            self._destroy_best_effort(stage)
            if not stable_existed:
                self._destroy_best_effort(name)
            return False

    def delete_set(self, name: str) -> bool:
        """兼容旧接口；集合不存在视为成功。"""
        try:
            names = self.list_names()
            if name not in names:
                return True
            self._run(["ipset", "destroy", name], timeout=10)
            return True
        except RuntimeError as exc:
            logger.error("删除 ipset 集合 %s 失败: %s", name, exc)
            return False

    def _validate_spec(self, spec: IpsetSpec) -> None:
        if not self.is_stable_managed_name(spec.name):
            raise ValueError(f"拒绝管理 NiceVPN 命名空间外的 ipset: {spec.name}")
        if len(spec.name) > IPSET_MAX_NAME_LENGTH:
            raise ValueError(f"ipset 名称过长: {spec.name}")
        if spec.set_type != "hash:net":
            raise ValueError(f"当前仅支持 hash:net: {spec.set_type}")
        if spec.family != "inet":
            raise ValueError(f"当前仅支持 family inet: {spec.family}")

    def _create_empty(
        self,
        name: str,
        *,
        set_type: str,
        family: str,
        exist_ok: bool,
    ) -> None:
        args = ["ipset", "create", name, set_type, "family", family]
        if exist_ok:
            args.append("-exist")
        self._run(args, timeout=10)

    def _destroy_best_effort(self, name: str) -> bool:
        result = self._run(["ipset", "destroy", name], check=False, timeout=10)
        if result.returncode == 0:
            return True
        stderr = self._stderr(result)
        if "does not exist" in stderr.lower() or "not exist" in stderr.lower():
            return True
        logger.warning("销毁 ipset 失败 %s: %s", name, stderr)
        return False

    @staticmethod
    def _stderr(result: subprocess.CompletedProcess) -> str:
        return str(result.stderr or result.stdout or "").strip() or "(无 stderr/stdout)"

    def _run(
        self,
        args: list[str],
        *,
        check: bool = True,
        timeout: int = 10,
    ) -> subprocess.CompletedProcess:
        logger.debug("执行命令: %s", " ".join(args))
        try:
            result = self._runner(
                args,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"执行 {' '.join(args)} 失败: {exc}") from exc
        if check and result.returncode != 0:
            raise RuntimeError(
                f"命令失败 rc={result.returncode}: {' '.join(args)}: {self._stderr(result)}"
            )
        return result
