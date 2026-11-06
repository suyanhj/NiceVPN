# -*- coding: utf-8 -*-
"""标签列表、详情与关联编辑窗口按需分页，不依赖读取系统全部用户。"""

import asyncio

from nicegui.testing import user_simulation

from app.services.firewall.tag_service import TagService
from app.storage.sqlite_store import SqliteStore
from app.ui.pages import tags as tags_module


def test_tags_page_and_member_dialog_build_with_only_paginated_queries(tmp_path, monkeypatch):
    store = SqliteStore(tmp_path / "tags-ui.sqlite")
    store.upsert_group({"id": "g1", "name": "办公组", "subnet": "10.8.1.0/24"})
    names = [f"member-{i:03}" for i in range(35)]
    with store.connection(write=True) as conn:
        conn.executemany(
            "INSERT INTO users(username,group_id,status,payload) VALUES (?,'g1','active',?)",
            [(name, '{"username":"' + name + '","status":"active"}') for name in names],
        )
    store.create_tag("tag1", "核心关联")
    store.set_tag_members("tag1", usernames=names, group_ids=["g1"])

    monkeypatch.setattr(tags_module, "TagService", lambda: TagService(store))

    def forbid_all_users(*_args, **_kwargs):
        raise AssertionError("标签页不应该触发全量用户读取")

    monkeypatch.setattr(store, "list_users", forbid_all_users)
    monkeypatch.setattr(store, "list_groups", forbid_all_users)

    def builder():
        page = tags_module.TagsPage()
        page.render()
        page._toggle_tag("tag1")
        page._change_detail_page("tag1", "users", 2)
        page._change_detail_tab("tag1", "groups")
        page._show_members_dialog("tag1")

    async def verify():
        async with user_simulation(builder) as user:
            response = await user.open("/")
            assert response.status_code == 200

    asyncio.run(verify())
