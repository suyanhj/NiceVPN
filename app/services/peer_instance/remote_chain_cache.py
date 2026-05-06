# -*- coding: utf-8 -*-
"""对端 filter 用户链在管理端的本地工作副本（``REMOTE_PEER_CHAINS_DIR`` 下每对端一 JSON）。

产品约定：对端机链上**实际状态**是事实源；见 ``FirewallPage`` 模块 docstring，须先
``record_from_fetch`` 再经本处读写；一切改动的写回用 ``mark_pending_apply`` / ``record_after_apply``
走 SSH 下发。``rows`` 每项至少含 ``rest``、``enabled``，可选 ``description``、``priority``
（与中心新建/排序一致，用于本机展示与插入位置）。推对端时仅按序下发已启用之 ``rest``。
旧版仅含 ``rests: [str]`` 的仍可读，并视为全部启用。
"""
from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.constants import REMOTE_PEER_CHAINS_DIR
from app.utils.file_lock import read_json, write_json_atomic

logger = logging.getLogger(__name__)

_CACHE_VERSION = 1


def _path_for_peer(peer_id: str) -> Path:
    pid = str(peer_id or "").strip()
    if not pid:
        raise ValueError("peer_id 为空")
    safe = pid.replace(os.sep, "_").replace("/", "_")
    return REMOTE_PEER_CHAINS_DIR / f"{safe}.json"


def _prepush_bak_path(peer_id: str) -> Path:
    """主文件 `peer.json` 在发起写回前快照路径 ``peer.prepush.bak``（同目录）。"""
    p = _path_for_peer(peer_id)
    return p.parent / f"{p.stem}.prepush.bak"


def _backup_main_json_before_mutation(peer_id: str) -> None:
    """在覆盖主工作副本前复制一份；SSH 写回失败时用于恢复，避免「未推成功却已改库」单份真相。"""
    main = _path_for_peer(peer_id)
    if not main.is_file():
        return
    bak = _prepush_bak_path(peer_id)
    try:
        shutil.copy2(main, bak)
        logger.info(
            "对端工作副本已生成写回前备份 peer=%s",
            str(peer_id)[:8] if len(str(peer_id)) > 8 else str(peer_id),
        )
    except OSError as exc:
        logger.error("对端工作副本写回前备份失败 peer=%s: %s", peer_id, exc)
        raise RuntimeError(f"无法备份工作副本，已中止写入: {exc}") from exc


def _clear_prepush_backup(peer_id: str) -> None:
    p = _prepush_bak_path(peer_id)
    try:
        if p.is_file():
            p.unlink()
    except OSError as exc:
        logger.warning("删除对端工作副本 prepush 备份失败: %s", exc)


def _restore_main_from_prepush_backup(peer_id: str, error_message: str | None) -> bool:
    """若存在写回前备份，用其覆盖主文件并写入 ``last_sync_error`` / ``pending_sync``。返回是否已恢复。"""
    main = _path_for_peer(peer_id)
    bak = _prepush_bak_path(peer_id)
    if not bak.is_file():
        return False
    try:
        raw = read_json(bak)
    except (OSError, ValueError, TypeError) as exc:
        logger.error("读取 prepush 备份失败: %s", exc)
        return False
    if not isinstance(raw, dict):
        return False
    raw = dict(raw)
    raw["last_sync_error"] = (error_message or "写回对端失败")[:2000]
    raw["pending_sync"] = True
    raw["updated_at"] = _iso_now()
    write_json_atomic(main, raw)
    logger.info(
        "对端工作副本写回失败，已从 prepush 备份恢复主文件 peer=%s",
        str(peer_id)[:8] if len(str(peer_id)) > 8 else str(peer_id),
    )
    return True


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_rows_from_raw(raw: dict[str, Any]) -> list[dict[str, Any]]:
    if raw.get("rows") and isinstance(raw["rows"], list):
        out: list[dict[str, Any]] = []
        for x in raw["rows"]:
            if not isinstance(x, dict):
                continue
            r = str(x.get("rest", "")).strip()
            if not r:
                continue
            row: dict[str, Any] = {"rest": r, "enabled": bool(x.get("enabled", True))}
            ds = str(x.get("description") or "").strip()
            if ds:
                row["description"] = ds
            if x.get("priority") is not None:
                try:
                    row["priority"] = int(x["priority"])
                except (TypeError, ValueError):
                    pass
            out.append(row)
        return out
    rests = [str(s).strip() for s in (raw.get("rests") or []) if str(s).strip()]
    return [{"rest": s, "enabled": True} for s in rests]


def read_remote_chain_cache(peer_id: str) -> dict[str, Any] | None:
    """读取对端链缓存。无文件或损坏时返回 ``None``。始终含规范化后的 ``rows``。"""
    p = _path_for_peer(peer_id)
    if not p.exists():
        return None
    try:
        raw = read_json(p)
    except (OSError, ValueError, TypeError) as exc:
        logger.error("读远端链缓存失败 peer=%s: %s", peer_id, exc)
        return None
    if not raw or str(raw.get("peer_id") or "").strip() != str(peer_id).strip():
        return None
    if int(raw.get("version", 0)) < 1:
        return None
    rows = _normalize_rows_from_raw(raw)
    out = {**raw, "rows": rows, "rests": [r["rest"] for r in rows]}
    return out


def write_remote_chain_cache(
    peer_id: str,
    *,
    chain: str,
    chain_exists: bool,
    rows: list[dict[str, Any]],
    pending_sync: bool,
    last_sync_error: str | None,
    set_pulled: bool = False,
    set_pushed: bool = False,
) -> None:
    """覆盖写入；``rows`` 每项为 ``{ "rest", "enabled" }``，可选 ``description``、``priority``。"""
    _path_for_peer(peer_id)
    clean_rows: list[dict[str, Any]] = []
    for x in rows:
        if not isinstance(x, dict):
            continue
        r = str(x.get("rest", "")).strip()
        if not r:
            continue
        base: dict[str, Any] = {"rest": r, "enabled": bool(x.get("enabled", True))}
        ds = str(x.get("description") or "").strip()
        if ds:
            base["description"] = ds
        if x.get("priority") is not None:
            try:
                base["priority"] = int(x["priority"])
            except (TypeError, ValueError):
                pass
        clean_rows.append(base)
    prev = read_remote_chain_cache(peer_id) or {}
    now = _iso_now()
    rec: dict[str, Any] = {
        "version": _CACHE_VERSION,
        "peer_id": str(peer_id).strip(),
        "chain": str(chain or ""),
        "chain_exists": bool(chain_exists),
        "rows": clean_rows,
        "rests": [x["rest"] for x in clean_rows],
        "pending_sync": bool(pending_sync),
        "last_sync_error": last_sync_error,
        "updated_at": now,
    }
    rec["last_pulled_at"] = now if set_pulled else prev.get("last_pulled_at")
    rec["last_pushed_at"] = now if set_pushed else prev.get("last_pushed_at")
    p = _path_for_peer(peer_id)
    REMOTE_PEER_CHAINS_DIR.mkdir(parents=True, exist_ok=True)
    write_json_atomic(p, rec)
    # 每步都写文件，仅 debug；对外语义上的「同步并落库」在 ``record_after_apply`` 成功时打一条 info
    logger.debug(
        "远端链已落库 peer=%s rows=%s pending_sync=%s pulled=%s pushed=%s",
        peer_id[:8] if len(str(peer_id)) > 8 else peer_id,
        len(clean_rows),
        pending_sync,
        set_pulled,
        set_pushed,
    )


def rests_to_push_list(rows: list[dict[str, Any]]) -> list[str]:
    """按行顺序、仅启用的片段，供 ``replace_peer_filter_chain_rests_via_ssh``。"""
    return [str(x.get("rest", "")).strip() for x in rows if str(x.get("rest", "")).strip() and x.get("enabled", True)]


def record_from_fetch(snap: dict[str, Any], peer_id: str) -> None:
    """成功 SSH 拉取后：以快照覆盖本地；链上每行均视为对端已启用。"""
    _clear_prepush_backup(peer_id)
    rests = list(snap.get("chain_rests") or [])
    rows = [{"rest": s, "enabled": True} for s in rests]
    write_remote_chain_cache(
        peer_id,
        chain=str(snap.get("chain") or ""),
        chain_exists=bool(snap.get("chain_exists")),
        rows=rows,
        pending_sync=False,
        last_sync_error=None,
        set_pulled=True,
        set_pushed=False,
    )


def record_after_apply(
    peer_id: str,
    chain: str,
    chain_exists: bool,
    rows: list[dict[str, Any]],
    ok: bool,
    error_message: str | None = None,
) -> None:
    """写回对端后落库。``rows`` 为完整工作副本（含未下发链上的停启用行）。"""
    show = str(peer_id).strip()
    if len(show) > 8:
        show = show[:8]
    if ok:
        write_remote_chain_cache(
            peer_id,
            chain=chain,
            chain_exists=chain_exists,
            rows=list(rows),
            pending_sync=False,
            last_sync_error=None,
            set_pulled=False,
            set_pushed=True,
        )
        _clear_prepush_backup(peer_id)
        # 对端写回 + 工作副本已对齐：本条为同步路径唯一 info，避免与 SSH/sudo/每次落库重复
        logger.info("对端链已同步并落库 peer=%s chain=%s rows=%s", show, str(chain or "").strip(), len(rows))
    else:
        if _restore_main_from_prepush_backup(peer_id, error_message):
            return
        write_remote_chain_cache(
            peer_id,
            chain=chain,
            chain_exists=chain_exists,
            rows=list(rows),
            pending_sync=True,
            last_sync_error=(error_message or "写回对端失败")[:2000],
            set_pulled=False,
            set_pushed=False,
        )


def mark_pending_apply(
    peer_id: str,
    chain: str,
    chain_exists: bool,
    rows: list[dict[str, Any]],
) -> None:
    """在发起 SSH 写回前落库，标记未确认对端已对齐。覆盖前对主文件做 ``.prepush.bak`` 快照。"""
    _backup_main_json_before_mutation(peer_id)
    write_remote_chain_cache(
        peer_id,
        chain=chain,
        chain_exists=chain_exists,
        rows=list(rows),
        pending_sync=True,
        last_sync_error=None,
        set_pulled=False,
        set_pushed=False,
    )
