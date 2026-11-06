# -*- coding: utf-8 -*-
"""独立 API 与管理进程共享的审计日志、下载令牌并发回归。"""

import hashlib
import json
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import app.core.constants as constants
import app.utils.audit_log as audit_mod
from app.services.download.link_mgr import consume_link, create_link


def _write_audit_from_process(directory: str, prefix: str, count: int) -> None:
    audit_mod.AUDIT_DIR = Path(directory)
    audit_mod.AuditLogger._instance = None
    audit = audit_mod.AuditLogger()
    for i in range(count):
        audit.log("api_split_test", "user", f"{prefix}-{i}", "测试", "success")


@pytest.mark.skipif(os.name != "posix", reason="跨进程 flock 仅适用于 POSIX")
def test_audit_hash_chain_survives_two_processes(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(audit_mod, "AUDIT_DIR", tmp_path)
    monkeypatch.setattr(audit_mod.AuditLogger, "_instance", None)

    ctx = multiprocessing.get_context("spawn")
    workers = [
        ctx.Process(target=_write_audit_from_process, args=(str(tmp_path), tag, 12))
        for tag in ("web", "api")
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=20)
        assert worker.exitcode == 0

    log_files = list(tmp_path.glob("audit-*.jsonl"))
    assert len(log_files) == 1
    lines = [line for line in log_files[0].read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 24
    previous = "0" * 64
    for line in lines:
        item = json.loads(line)
        assert item["prev_hash"] == previous
        previous = hashlib.sha256(line.encode("utf-8")).hexdigest()


def test_one_time_download_token_is_consumed_once_under_concurrency(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(constants, "DOWNLOAD_LINKS_DIR", tmp_path)
    token = create_link("alice", "/etc/openvpn/alice.ovpn", "https://vpn.example.com").rsplit("/", 1)[-1]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(consume_link, [token] * 8))
    assert sum(result is not None and result.get("error") is None for result in results) == 1
    assert sum(result is not None and bool(result.get("error")) for result in results) == 7
