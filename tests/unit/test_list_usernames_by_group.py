# -*- coding: utf-8 -*-
"""UserService.list_usernames_by_group 单元测试"""

import pytest

from app.services.user.crud import UserService
from app.storage.sqlite_store import SqliteStore


def test_list_usernames_by_group_empty_db(tmp_path):
    svc = UserService(SqliteStore(tmp_path / "db.sqlite"))
    assert svc.list_usernames_by_group("g1") == []


def test_list_usernames_by_group_filters_and_sorts(tmp_path):
    store = SqliteStore(tmp_path / "db.sqlite")
    for row in (
        {"username": "bob", "group_id": "g1", "status": "active"},
        {"username": "alice", "group_id": "g1", "status": "active"},
        {"username": "del", "group_id": "g1", "status": "deleted"},
        {"username": "other", "group_id": "g2", "status": "active"},
    ):
        store.upsert_user(row)

    assert UserService(store).list_usernames_by_group("g1") == ["alice", "bob"]


def test_list_usernames_by_group_rejects_empty_id(tmp_path):
    with pytest.raises(ValueError, match="group_id"):
        UserService(SqliteStore(tmp_path / "db.sqlite")).list_usernames_by_group("")
