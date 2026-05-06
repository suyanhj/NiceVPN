# -*- coding: utf-8 -*-
"""系统配置读写归一化单测。"""

import json
from pathlib import Path

from box import Box


def test_load_config_normalizes_empty_dict_optional_strings(monkeypatch, tmp_path: Path) -> None:
    """兼容历史坏数据：可选字符串字段中的 {} 应按 None 读取。"""
    from app.core import config as cfg

    config_file = tmp_path / "data" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text(json.dumps({"pki_dir": {}, "easyrsa_dir": {}}), encoding="utf-8")
    monkeypatch.setattr(cfg, "CONFIG_FILE", config_file)
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path / "data")

    loaded = cfg.load_config()

    assert loaded.pki_dir is None
    assert loaded.easyrsa_dir is None


def test_save_config_normalizes_box_empty_dict_optional_strings(monkeypatch, tmp_path: Path) -> None:
    """保存配置前应清理 Box 空对象，避免下次 Pydantic 校验失败。"""
    from app.core import config as cfg

    config_file = tmp_path / "data" / "config.json"
    monkeypatch.setattr(cfg, "CONFIG_FILE", config_file)
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path / "data")

    cfg.save_config(Box({"pki_dir": {}, "easyrsa_dir": {}, "initialized": False}, default_box=True))

    data = json.loads(config_file.read_text(encoding="utf-8"))
    assert data["pki_dir"] is None
    assert data["easyrsa_dir"] is None
