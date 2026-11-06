# -*- coding: utf-8 -*-
"""SQLite label and association repository mixin."""
from __future__ import annotations

from typing import Any, Sequence
from app.storage.repository_helpers import _now_iso, _load_payload, _like_pattern


class TagRepositoryMixin:
    # ---- tags ----

    def create_tag(self, tag_id: str, name: str, description: str = "") -> None:
        tag_id, name = str(tag_id).strip(), str(name).strip()
        if not tag_id or not name:
            raise ValueError("标签 ID 与名称不能为空")
        now = _now_iso()
        with self.connection(write=True) as conn:
            conn.execute(
                "INSERT INTO tags(id,name,description,created_at,updated_at) VALUES(?,?,?,?,?)",
                (tag_id, name, str(description), now, now),
            )

    def list_tags(self) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT id,name,description,created_at,updated_at FROM tags ORDER BY name,id"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_tag(self, tag_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT id,name,description,created_at,updated_at FROM tags WHERE id=?",
                (str(tag_id),),
            ).fetchone()
        return dict(row) if row else None

    def update_tag(self, tag_id: str, *, name: str, description: str = "") -> None:
        name = str(name).strip()
        if not name:
            raise ValueError("标签名称不能为空")
        with self.connection(write=True) as conn:
            cur = conn.execute(
                "UPDATE tags SET name=?,description=?,updated_at=? WHERE id=?",
                (name, str(description), _now_iso(), str(tag_id)),
            )
            if cur.rowcount != 1:
                raise ValueError("标签不存在")

    def delete_tag(self, tag_id: str) -> None:
        with self.connection(write=True) as conn:
            conn.execute("DELETE FROM tags WHERE id=?", (str(tag_id),))

    def set_tag_members(
        self, tag_id: str, *, usernames: Sequence[str] = (), group_ids: Sequence[str] = (),
    ) -> None:
        """整体替换关联，单事务提交；非法成员拒绝并保持原关联。"""
        tid = str(tag_id)
        users = sorted({str(v).strip() for v in usernames if str(v).strip()})
        groups = sorted({str(v).strip() for v in group_ids if str(v).strip()})
        with self.connection(write=True) as conn:
            if conn.execute("SELECT 1 FROM tags WHERE id=?", (tid,)).fetchone() is None:
                raise ValueError("标签不存在")
            if users:
                found = set()
                # 兼容 SQLite 较低的绑定参数上限（部分系统仍为 999）。
                for start in range(0, len(users), 500):
                    part = users[start:start + 500]
                    placeholders = ",".join("?" for _ in part)
                    found.update(r[0] for r in conn.execute(
                        f"SELECT username FROM users WHERE username IN ({placeholders})", part
                    ))
                if len(found) != len(users):
                    raise ValueError("关联用户不存在")
            if groups:
                found = set()
                for start in range(0, len(groups), 500):
                    part = groups[start:start + 500]
                    placeholders = ",".join("?" for _ in part)
                    found.update(r[0] for r in conn.execute(
                        f"SELECT id FROM groups WHERE id IN ({placeholders})", part
                    ))
                if len(found) != len(groups):
                    raise ValueError("关联用户组不存在")
            conn.execute("DELETE FROM user_tags WHERE tag_id=?", (tid,))
            conn.execute("DELETE FROM group_tags WHERE tag_id=?", (tid,))
            conn.executemany(
                "INSERT INTO user_tags(tag_id,username) VALUES (?,?)",
                [(tid, username) for username in users],
            )
            conn.executemany(
                "INSERT INTO group_tags(tag_id,group_id) VALUES (?,?)",
                [(tid, gid) for gid in groups],
            )

    def list_entity_tag_ids(self, entity_type: str, entity_id: str) -> list[str]:
        if entity_type == "user":
            table, column = "user_tags", "username"
        elif entity_type == "group":
            table, column = "group_tags", "group_id"
        else:
            raise ValueError("仅支持 user/group")
        with self.connection() as conn:
            rows = conn.execute(
                f"SELECT tag_id FROM {table} WHERE {column}=? ORDER BY tag_id",
                (str(entity_id),),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def replace_entity_tags(self, entity_type: str, entity_id: str, tag_ids: Sequence[str]) -> None:
        """单个 SQLite 事务整体替换一个用户/组的所有标签，杜绝关联写到一半。"""
        if entity_type == "user":
            table, column, entity_table, entity_key = "user_tags", "username", "users", "username"
        elif entity_type == "group":
            table, column, entity_table, entity_key = "group_tags", "group_id", "groups", "id"
        else:
            raise ValueError("仅支持 user/group")
        eid = str(entity_id).strip()
        ids = sorted({str(value).strip() for value in tag_ids if str(value).strip()})
        with self.connection(write=True) as conn:
            if conn.execute(f"SELECT 1 FROM {entity_table} WHERE {entity_key}=?", (eid,)).fetchone() is None:
                raise ValueError("关联用户或组不存在")
            if ids:
                placeholders = ",".join("?" for _ in ids)
                count = conn.execute(
                    f"SELECT COUNT(*) FROM tags WHERE id IN ({placeholders})", ids,
                ).fetchone()[0]
                if int(count) != len(ids):
                    raise ValueError("存在未知标签")
            conn.execute(f"DELETE FROM {table} WHERE {column}=?", (eid,))
            conn.executemany(
                f"INSERT INTO {table}(tag_id,{column}) VALUES (?,?)",
                [(tid, eid) for tid in ids],
            )

    def batch_change_entity_tags(
        self, entity_type: str, entity_ids: Sequence[str],
        tag_ids: Sequence[str], *, operation: str,
    ) -> dict[str, list[str]]:
        """批量增删关联：单事务校验全部对象，返回每个对象修改前的标签。

        批量失败时全部回滚，不覆盖不相关标签；后续可按快照恢复。
        """
        if entity_type == "user":
            table, column, entity_table, entity_key = "user_tags", "username", "users", "username"
        elif entity_type == "group":
            table, column, entity_table, entity_key = "group_tags", "group_id", "groups", "id"
        else:
            raise ValueError("仅支持 user/group")
        if operation not in ("add", "remove", "replace"):
            raise ValueError("不支持的标签批量操作")
        ids = sorted({str(v).strip() for v in entity_ids if str(v).strip()})
        tags = sorted({str(v).strip() for v in tag_ids if str(v).strip()})
        if not ids:
            raise ValueError("请先选择用户或组")
        if not tags and operation != "replace":
            raise ValueError("请先选择标签")
        with self.connection(write=True) as conn:
            slots = ",".join("?" for _ in ids)
            found = {str(r[0]) for r in conn.execute(
                f"SELECT {entity_key} FROM {entity_table} WHERE {entity_key} IN ({slots})", ids
            )}
            if len(found) != len(ids):
                raise ValueError("所选用户或组有不存在的记录")
            if tags:
                marks = ",".join("?" for _ in tags)
                existing = {str(r[0]) for r in conn.execute(
                    f"SELECT id FROM tags WHERE id IN ({marks})", tags
                )}
                if len(existing) != len(tags):
                    raise ValueError("包含不存在的标签")
            previous: dict[str, list[str]] = {}
            for eid in ids:
                old = [str(r[0]) for r in conn.execute(
                    f"SELECT tag_id FROM {table} WHERE {column}=? ORDER BY tag_id", (eid,)
                )]
                previous[eid] = old
                if operation == "add":
                    desired = sorted(set(old) | set(tags))
                elif operation == "remove":
                    desired = sorted(set(old) - set(tags))
                else:
                    desired = tags
                if desired != old:
                    conn.execute(f"DELETE FROM {table} WHERE {column}=?", (eid,))
                    conn.executemany(
                        f"INSERT INTO {table}(tag_id,{column}) VALUES (?,?)",
                        [(tag, eid) for tag in desired],
                    )
        return previous

    def restore_entity_tag_snapshots(
        self, entity_type: str, snapshots: dict[str, list[str]],
    ) -> None:
        """一次事务恢复批量关联修改前的全部对象。"""
        if entity_type == "user":
            table, column = "user_tags", "username"
        elif entity_type == "group":
            table, column = "group_tags", "group_id"
        else:
            raise ValueError("仅支持 user/group")
        with self.connection(write=True) as conn:
            for eid, tags in snapshots.items():
                conn.execute(f"DELETE FROM {table} WHERE {column}=?", (eid,))
                conn.executemany(
                    f"INSERT INTO {table}(tag_id,{column}) VALUES (?,?)",
                    [(tag, eid) for tag in tags],
                )

    def get_tag_members(self, tag_id: str) -> dict[str, list[str]]:
        with self.connection() as conn:
            users = conn.execute(
                "SELECT username FROM user_tags WHERE tag_id=? ORDER BY username",
                (str(tag_id),),
            ).fetchall()
            groups = conn.execute(
                "SELECT group_id FROM group_tags WHERE tag_id=? ORDER BY group_id",
                (str(tag_id),),
            ).fetchall()
        return {
            "usernames": [r[0] for r in users],
            "group_ids": [r[0] for r in groups],
        }

    def list_tag_counts(self) -> dict[str, dict[str, int]]:
        """一次读取全部标签的关联数量，不反复打开连接或加载成员 payload。"""
        result: dict[str, dict[str, int]] = {}
        specs = (
            ("user_tags", "tag_id", "users", ""),
            ("group_tags", "tag_id", "groups", ""),
            ("firewall_rules", "owner_id", "rules", "WHERE owner_type='tag'"),
        )
        with self.connection() as conn:
            for table, column, key, where in specs:
                rows = conn.execute(
                    f"SELECT {column} AS tag_id, COUNT(*) AS n "
                    f"FROM {table} {where} GROUP BY {column}"
                ).fetchall()
                for row in rows:
                    result.setdefault(str(row["tag_id"]), {"users": 0, "groups": 0, "rules": 0})[
                        key
                    ] = int(row["n"])
        return result

    def list_tag_detail_page(
        self, tag_id: str, kind: str, *, offset: int = 0, limit: int = 20,
        search: str = "",
    ) -> tuple[list[dict[str, Any]], int]:
        """只查展开标签的当前选项卡；用户/组/规则统一服务端分页。"""
        if kind not in ("users", "groups", "rules"):
            raise ValueError("未知标签详情类型")
        tid = str(tag_id)
        where = ""
        params: list[Any] = [tid]
        if kind == "users":
            table = "user_tags AS t LEFT JOIN users AS u ON u.username=t.username"
            match = "t.tag_id=?"
            expression = "t.username"
            projection = "t.username, COALESCE(u.status, 'unknown') AS status"
            ordering = "t.username COLLATE NOCASE"
        elif kind == "groups":
            table = "group_tags AS t LEFT JOIN groups AS g ON g.id=t.group_id"
            match = "t.tag_id=?"
            expression = "g.name"
            projection = "t.group_id, g.payload"
            ordering = "g.name COLLATE NOCASE, t.group_id"
        else:
            table = "firewall_rules AS t"
            match = "t.owner_id=? AND t.owner_type='tag'"
            expression = "COALESCE(json_extract(t.payload, '$.description'), '')"
            projection = "t.payload"
            ordering = "t.priority, t.id"
        query = str(search or "").strip()
        if query:
            if kind == "groups":
                where = f" AND ({expression} LIKE ? ESCAPE '\\' OR t.group_id LIKE ? ESCAPE '\\')"
                params.extend([_like_pattern(query)] * 2)
            else:
                where = f" AND {expression} LIKE ? ESCAPE '\\'"
                params.append(_like_pattern(query))
        with self.connection() as conn:
            total = int(conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {match}{where}", params,
            ).fetchone()[0])
            rows = conn.execute(
                f"SELECT {projection} FROM {table} WHERE {match}{where} "
                f"ORDER BY {ordering} LIMIT ? OFFSET ?",
                [*params, max(1, int(limit)), max(0, int(offset))],
            ).fetchall()
        if kind == "rules":
            return [_load_payload(row["payload"]) for row in rows], total
        if kind == "groups":
            return [
                (_load_payload(row["payload"]) if row["payload"] else {"id": row["group_id"]})
                for row in rows
            ], total
        return [dict(row) for row in rows], total

    def list_tag_candidates_page(
        self, kind: str, *, search: str = "", offset: int = 0, limit: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """关联编辑候选项服务端搜索，避免一次送出全部用户名与组。"""
        if kind == "users":
            table, where, key, order = (
                "users", "status <> 'deleted'", "username", "username COLLATE NOCASE"
            )
            projection = "username, status"
        elif kind == "groups":
            table, where, key, order = "groups", "1=1", "name", "name COLLATE NOCASE, id"
            projection = "id, name, payload"
        else:
            raise ValueError("未知候选成员类型")
        params: list[Any] = []
        if str(search or "").strip():
            pattern = _like_pattern(str(search).strip())
            if kind == "groups":
                where += " AND (name LIKE ? ESCAPE '\\' OR id LIKE ? ESCAPE '\\')"
                params.extend([pattern, pattern])
            else:
                where += f" AND {key} LIKE ? ESCAPE '\\'"
                params.append(pattern)
        with self.connection() as conn:
            total = int(conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {where}", params,
            ).fetchone()[0])
            rows = conn.execute(
                f"SELECT {projection} FROM {table} WHERE {where} ORDER BY {order} "
                "LIMIT ? OFFSET ?",
                [*params, max(1, int(limit)), max(0, int(offset))],
            ).fetchall()
        if kind == "groups":
            return [_load_payload(row["payload"]) for row in rows], total
        return [dict(row) for row in rows], total
