# -*- coding: utf-8 -*-
"""全局 pytest 隔离配置。"""

from __future__ import annotations

import pytest

from app.storage.migration import reset_storage_ready_for_tests
from app.storage.sqlite_store import reset_default_store_for_tests


@pytest.fixture(autouse=True)
def _isolated_sqlite_storage(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """每个测试使用独立 SQLite，避免结构化元数据跨测试泄漏。"""

    monkeypatch.setenv("NICEVPN_DB_PATH", str(tmp_path / "nicevpn-test.db"))
    reset_default_store_for_tests()
    reset_storage_ready_for_tests()
    yield
    reset_default_store_for_tests()
    reset_storage_ready_for_tests()
