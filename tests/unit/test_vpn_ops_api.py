# -*- coding: utf-8 -*-
"""VPN 公网 API 路由单元测试（HTTP Basic + 初始化门禁）"""

import json
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.utils.api_basic_credentials as abc_mod
from app.api import vpn_ops


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(abc_mod, "API_BASIC_CREDENTIALS_FILE", tmp_path / "api_basic.json")
    cred = {"username": "vpn", "password": "test-secret-password"}
    (tmp_path / "api_basic.json").write_text(json.dumps(cred), encoding="utf-8")

    app = FastAPI()
    app.include_router(vpn_ops.router, prefix="/api")
    return TestClient(app)


def _auth_headers():
    import base64

    token = base64.b64encode(b"vpn:test-secret-password").decode("ascii")
    return {"Authorization": f"Basic {token}"}


def test_create_401_without_auth(client):
    r = client.post("/api/vpn/users", json={"username": "u1"})
    assert r.status_code == 401


def test_create_401_wrong_password(client):
    import base64

    bad = base64.b64encode(b"vpn:wrong").decode("ascii")
    r = client.post(
        "/api/vpn/users",
        json={"username": "u1"},
        headers={"Authorization": f"Basic {bad}"},
    )
    assert r.status_code == 401


def test_create_503_not_initialized(client, monkeypatch):
    def _cfg():
        m = MagicMock()
        m.initialized = False
        m.download_base_url = None
        return m

    monkeypatch.setattr(vpn_ops, "load_config", _cfg)
    r = client.post("/api/vpn/users", json={"username": "u1"}, headers=_auth_headers())
    assert r.status_code == 503


def test_create_single_returns_one_ovpn_link(client, monkeypatch, tmp_path):
    monkeypatch.setattr(abc_mod, "API_BASIC_CREDENTIALS_FILE", tmp_path / "api_basic.json")
    (tmp_path / "api_basic.json").write_text(
        json.dumps({"username": "vpn", "password": "test-secret-password"}),
        encoding="utf-8",
    )

    def _cfg():
        m = MagicMock()
        m.initialized = True
        m.download_base_url = "https://api.example.com"
        return m

    monkeypatch.setattr(vpn_ops, "load_config", _cfg)

    calls = {"create": 0}
    link_kw: list[dict] = []

    class FakeUserSvc:
        def __init__(self):
            self._store: dict = {}

        def create(self, **kwargs):
            calls["create"] += 1
            un = kwargs["username"]
            self._store[un] = {
                "ovpn_file_path": "/tmp/x.ovpn",
                "username": un,
                "status": "active",
            }

        def get(self, username):
            return self._store.get(username)

    monkeypatch.setattr(vpn_ops, "UserService", FakeUserSvc)
    monkeypatch.setattr(vpn_ops, "_resolve_group_id_by_name", lambda _: "gid-default")

    def fake_link(**kw):
        link_kw.append(kw)
        return "https://api.example.com/download/one"

    monkeypatch.setattr(vpn_ops, "create_link", fake_link)

    def fail_zip(*a, **k):
        raise AssertionError("single user must not call build_ovpn_zip")

    monkeypatch.setattr(vpn_ops, "build_ovpn_zip", fail_zip)

    r = client.post("/api/vpn/users", json={"username": "alice", "count": 1}, headers=_auth_headers())
    assert r.status_code == 200
    body = r.json()
    assert body.get("created") is True
    assert body["usernames"] == ["alice"]
    assert body["download_url"] == "https://api.example.com/download/one"
    assert calls["create"] == 1
    assert len(link_kw) == 1
    assert link_kw[0]["username"] == "alice"
    assert link_kw[0]["base_url"] == "https://api.example.com"
    assert str(link_kw[0]["ovpn_path"]).replace("\\", "/") == "/tmp/x.ovpn"


def test_create_multi_uses_zip_and_one_link(client, monkeypatch, tmp_path):
    monkeypatch.setattr(abc_mod, "API_BASIC_CREDENTIALS_FILE", tmp_path / "api_basic.json")
    (tmp_path / "api_basic.json").write_text(
        json.dumps({"username": "vpn", "password": "test-secret-password"}),
        encoding="utf-8",
    )

    def _cfg():
        m = MagicMock()
        m.initialized = True
        m.download_base_url = "https://api.example.com"
        return m

    monkeypatch.setattr(vpn_ops, "load_config", _cfg)

    calls = {"create": 0}
    link_kw: list[dict] = []

    class FakeUserSvc:
        def __init__(self):
            self._store: dict = {}

        def create(self, **kwargs):
            calls["create"] += 1
            un = kwargs["username"]
            self._store[un] = {
                "ovpn_file_path": f"/tmp/{un}.ovpn",
                "username": un,
                "status": "active",
            }

        def get(self, username):
            return self._store.get(username)

    monkeypatch.setattr(vpn_ops, "UserService", FakeUserSvc)
    monkeypatch.setattr(vpn_ops, "_resolve_group_id_by_name", lambda _: "gid-default")

    def fake_link(**kw):
        link_kw.append(kw)
        return "https://api.example.com/download/bundle-token"

    monkeypatch.setattr(vpn_ops, "create_link", fake_link)

    def fake_zip(entries, prefix):
        assert len(entries) == 2
        assert entries[0][0] == "alice"
        zfile = tmp_path / "mock.zip"
        zfile.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        return zfile, "alice_20990101_000000.zip"

    monkeypatch.setattr(vpn_ops, "build_ovpn_zip", fake_zip)

    r = client.post("/api/vpn/users", json={"username": "alice", "count": 2}, headers=_auth_headers())
    assert r.status_code == 200
    body = r.json()
    assert body.get("created") is True
    assert body["usernames"] == ["alice", "alice_1"]
    assert body["download_url"] == "https://api.example.com/download/bundle-token"
    assert calls["create"] == 2
    assert len(link_kw) == 1
    assert link_kw[0]["username"] == "alice"
    assert link_kw[0]["download_filename"] == "alice_20990101_000000.zip"
    assert str(link_kw[0]["ovpn_path"]).endswith("mock.zip")


def test_batch_401_without_auth(client):
    r = client.post("/api/vpn/users/batch", json=[{"username": "a", "count": 1}])
    assert r.status_code == 401


def test_batch_two_prefixes_one_merged_zip(client, monkeypatch, tmp_path):
    monkeypatch.setattr(abc_mod, "API_BASIC_CREDENTIALS_FILE", tmp_path / "api_basic.json")
    (tmp_path / "api_basic.json").write_text(
        json.dumps({"username": "vpn", "password": "test-secret-password"}),
        encoding="utf-8",
    )

    def _cfg():
        m = MagicMock()
        m.initialized = True
        m.download_base_url = "https://api.example.com"
        return m

    monkeypatch.setattr(vpn_ops, "load_config", _cfg)

    calls = {"create": 0}
    link_kw: list[dict] = []
    zip_calls: list[tuple] = []

    class FakeUserSvc:
        def __init__(self):
            self._store: dict = {}

        def create(self, **kwargs):
            calls["create"] += 1
            un = kwargs["username"]
            self._store[un] = {
                "ovpn_file_path": f"/tmp/{un}.ovpn",
                "username": un,
                "status": "active",
            }

        def get(self, username):
            return self._store.get(username)

    monkeypatch.setattr(vpn_ops, "UserService", FakeUserSvc)
    monkeypatch.setattr(vpn_ops, "_resolve_group_id_by_name", lambda _: "gid-default")

    def fake_link(**kw):
        link_kw.append(kw)
        return f"https://api.example.com/dl/{len(link_kw)}"

    monkeypatch.setattr(vpn_ops, "create_link", fake_link)

    def fake_zip(entries, prefix):
        zip_calls.append((tuple(e[0] for e in entries), prefix))
        assert prefix == vpn_ops._BATCH_ZIP_BUNDLE_PREFIX
        assert len(entries) == 3
        zfile = tmp_path / "merged.zip"
        zfile.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        return zfile, "vpns_20990101_000000.zip"

    monkeypatch.setattr(vpn_ops, "build_ovpn_zip", fake_zip)

    payload = [
        {"username": "alpha", "count": 1},
        {"username": "beta", "count": 2},
    ]
    r = client.post("/api/vpn/users/batch", json=payload, headers=_auth_headers())
    assert r.status_code == 200
    body = r.json()
    assert body.get("created") is True
    assert body["usernames"] == ["alpha", "beta", "beta_1"]
    assert body["download_url"] == "https://api.example.com/dl/1"
    assert body.get("message") is None
    assert calls["create"] == 3
    assert len(link_kw) == 1
    assert link_kw[0]["username"] == vpn_ops._BATCH_ZIP_BUNDLE_PREFIX
    assert link_kw[0]["download_filename"] == "vpns_20990101_000000.zip"
    assert len(zip_calls) == 1


def test_batch_partial_skip_still_one_zip(client, monkeypatch, tmp_path):
    monkeypatch.setattr(abc_mod, "API_BASIC_CREDENTIALS_FILE", tmp_path / "api_basic.json")
    (tmp_path / "api_basic.json").write_text(
        json.dumps({"username": "vpn", "password": "test-secret-password"}),
        encoding="utf-8",
    )

    def _cfg():
        m = MagicMock()
        m.initialized = True
        m.download_base_url = "https://api.example.com"
        return m

    monkeypatch.setattr(vpn_ops, "load_config", _cfg)

    class FakeUserSvc:
        def __init__(self):
            self._store: dict = {
                "exists1": {
                    "username": "exists1",
                    "status": "active",
                    "ovpn_file_path": "/tmp/exists1.ovpn",
                }
            }

        def create(self, **kwargs):
            un = kwargs["username"]
            self._store[un] = {
                "ovpn_file_path": f"/tmp/{un}.ovpn",
                "username": un,
                "status": "active",
            }

        def get(self, username):
            return self._store.get(username)

    monkeypatch.setattr(vpn_ops, "UserService", FakeUserSvc)
    monkeypatch.setattr(vpn_ops, "_resolve_group_id_by_name", lambda _: "gid-default")
    monkeypatch.setattr(vpn_ops, "create_link", lambda **kw: "https://api.example.com/dl/z")

    def fake_zip(entries, prefix):
        assert len(entries) == 1
        assert entries[0][0] == "newguy"
        zfile = tmp_path / "m.zip"
        zfile.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        return zfile, "vpns_ts.zip"

    monkeypatch.setattr(vpn_ops, "build_ovpn_zip", fake_zip)

    payload = [
        {"username": "exists1", "count": 1},
        {"username": "newguy", "count": 1},
    ]
    r = client.post("/api/vpn/users/batch", json=payload, headers=_auth_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["created"] is True
    assert body["usernames"] == ["exists1", "newguy"]
    assert body["download_url"]
    assert "exists1" in (body.get("message") or "")


def test_batch_rejects_sum_count_over_limit(client, monkeypatch, tmp_path):
    monkeypatch.setattr(abc_mod, "API_BASIC_CREDENTIALS_FILE", tmp_path / "api_basic.json")
    (tmp_path / "api_basic.json").write_text(
        json.dumps({"username": "vpn", "password": "test-secret-password"}),
        encoding="utf-8",
    )

    def _cfg():
        m = MagicMock()
        m.initialized = True
        m.download_base_url = "https://api.example.com"
        return m

    monkeypatch.setattr(vpn_ops, "load_config", _cfg)
    monkeypatch.setattr(vpn_ops, "UserService", MagicMock())
    big = [{"username": f"u{i}", "count": 500} for i in range(5)]
    r = client.post("/api/vpn/users/batch", json=big, headers=_auth_headers())
    assert r.status_code == 400
    assert "2000" in (r.json().get("detail") or "")


def test_create_skips_when_user_already_exists(client, monkeypatch, tmp_path):
    monkeypatch.setattr(abc_mod, "API_BASIC_CREDENTIALS_FILE", tmp_path / "api_basic.json")
    (tmp_path / "api_basic.json").write_text(
        json.dumps({"username": "vpn", "password": "test-secret-password"}),
        encoding="utf-8",
    )

    def _cfg():
        m = MagicMock()
        m.initialized = True
        m.download_base_url = None
        return m

    monkeypatch.setattr(vpn_ops, "load_config", _cfg)

    class FakeUserSvc:
        def create(self, **kwargs):
            raise AssertionError("不应在用户已存在时调用 create")

        def get(self, username):
            if username == "alice":
                return {"username": "alice", "status": "active"}
            return None

    monkeypatch.setattr(vpn_ops, "UserService", FakeUserSvc)
    monkeypatch.setattr(vpn_ops, "_resolve_group_id_by_name", lambda _: "gid")

    r = client.post("/api/vpn/users", json={"username": "alice", "count": 1}, headers=_auth_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["created"] is False
    assert body["download_url"] is None
    assert "已存在" in (body.get("message") or "")
    assert body["usernames"] == ["alice"]


def test_delete_200_when_no_matching_users(client, monkeypatch):
    def _cfg():
        m = MagicMock()
        m.initialized = True
        return m

    monkeypatch.setattr(vpn_ops, "load_config", _cfg)

    class FakeUserSvc:
        def list_all(self):
            return []

    monkeypatch.setattr(vpn_ops, "UserService", FakeUserSvc)
    r = client.delete("/api/vpn/users/nobody", headers=_auth_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["deleted"] == []
    assert body.get("message")


def test_reset_binding_404_no_matching_users(client, monkeypatch):
    def _cfg():
        m = MagicMock()
        m.initialized = True
        return m

    monkeypatch.setattr(vpn_ops, "load_config", _cfg)

    class FakeUserSvc:
        def list_all(self):
            return [{"username": "other", "status": "active"}]

    monkeypatch.setattr(vpn_ops, "UserService", FakeUserSvc)
    r = client.post(
        "/api/vpn/users/x/reset-device-binding",
        headers=_auth_headers(),
    )
    assert r.status_code == 404
