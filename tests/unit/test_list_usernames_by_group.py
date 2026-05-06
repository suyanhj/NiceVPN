# -*- coding: utf-8 -*-
"""UserService.list_usernames_by_group 单元测试"""

import json

import pytest

from app.services.user import crud as user_crud
from app.services.user.crud import UserService


def test_list_usernames_by_group_empty_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(user_crud, "USERS_DIR", tmp_path)
    assert UserService().list_usernames_by_group("g1") == []


def test_list_usernames_by_group_filters_and_sorts(monkeypatch, tmp_path):
    monkeypatch.setattr(user_crud, "USERS_DIR", tmp_path)
    (tmp_path / "b.json").write_text(
        json.dumps(
            {"username": "bob", "group_id": "g1", "status": "active"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "a.json").write_text(
        json.dumps(
            {"username": "alice", "group_id": "g1", "status": "active"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "d.json").write_text(
        json.dumps(
            {"username": "del", "group_id": "g1", "status": "deleted"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "o.json").write_text(
        json.dumps(
            {"username": "other", "group_id": "g2", "status": "active"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert UserService().list_usernames_by_group("g1") == ["alice", "bob"]


def test_list_usernames_by_group_rejects_empty_id():
    with pytest.raises(ValueError, match="group_id"):
        UserService().list_usernames_by_group("")
