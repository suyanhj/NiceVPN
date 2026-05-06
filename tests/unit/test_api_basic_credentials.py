# -*- coding: utf-8 -*-
"""api_basic_credentials 单元测试"""

import json

import pytest

import app.utils.api_basic_credentials as abc_mod


def test_ensure_creates_once(tmp_path, monkeypatch):
    monkeypatch.setattr(abc_mod, "API_BASIC_CREDENTIALS_FILE", tmp_path / "c.json")
    monkeypatch.setattr(abc_mod, "DATA_DIR", tmp_path)

    abc_mod.ensure_api_basic_credentials_file()
    first = json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))
    assert first["username"] == "vpn"
    assert len(first["password"]) >= 16

    abc_mod.ensure_api_basic_credentials_file()
    second = json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))
    assert second["password"] == first["password"]


def test_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(abc_mod, "API_BASIC_CREDENTIALS_FILE", tmp_path / "c.json")
    (tmp_path / "c.json").write_text(
        json.dumps({"username": "vpn", "password": "x"}),
        encoding="utf-8",
    )
    u, p = abc_mod.load_api_basic_credentials()
    assert u == "vpn" and p == "x"
