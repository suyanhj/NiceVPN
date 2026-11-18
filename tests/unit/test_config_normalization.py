# -*- coding: utf-8 -*-
"""根目录 YAML 配置与旧 JSON 数据迁移。"""
import json
from pathlib import Path

import yaml
from box import Box

from app.core import config as cfg


def _paths(monkeypatch, tmp_path: Path):
    data = tmp_path / "data"
    monkeypatch.setattr(cfg, "DATA_DIR", data)
    monkeypatch.setattr(cfg, "LEGACY_CONFIG_FILE", data / "config.json")
    monkeypatch.setattr(cfg, "CONFIG_FILE", tmp_path / "config.yml")
    monkeypatch.setattr(cfg, "ADMIN_INITIAL_PASSWORD_FILE", data / "admin_initial_password")
    monkeypatch.setattr(cfg, "ensure_openvpn_runtime_dirs", lambda: None)
    return tmp_path / "config.yml", data / "config.json"


def test_load_config_normalizes_empty_dict_optional_strings(monkeypatch, tmp_path: Path) -> None:
    config_file, _ = _paths(monkeypatch, tmp_path)
    config_file.write_text("pki_dir: {}\neasyrsa_dir: {}\n", encoding="utf-8")
    loaded = cfg.load_config()
    assert loaded.pki_dir is None
    assert loaded.easyrsa_dir is None


def test_save_config_normalizes_box_empty_dict_optional_strings(monkeypatch, tmp_path: Path) -> None:
    config_file, _ = _paths(monkeypatch, tmp_path)
    cfg.save_config(Box({"pki_dir": {}, "easyrsa_dir": {}, "initialized": False}, default_box=True))
    data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert data["pki_dir"] is None
    assert data["easyrsa_dir"] is None
    assert config_file.stat().st_mode & 0o777 == 0o600


def test_legacy_json_migrates_once_without_losing_settings(monkeypatch, tmp_path: Path) -> None:
    config_file, old_file = _paths(monkeypatch, tmp_path)
    old_file.parent.mkdir()
    old_file.write_text(json.dumps({
        "initialized": True,
        "global_subnet": "10.224.0.0/16",
        "instances": {"server": {"proto": "udp", "port": 1194}},
        "server_ip": "vpn.example.com",
    }), encoding="utf-8")
    config = cfg.load_config()
    assert config_file.is_file()
    assert old_file.is_file()
    assert config.server_ip == "vpn.example.com"
    assert config.instances["server"]["port"] == 1194
    assert yaml.safe_load(config_file.read_text(encoding="utf-8"))["global_subnet"] == "10.224.0.0/16"
    assert cfg.load_config().server_ip == "vpn.example.com"


def test_new_template_cannot_shadow_initialized_legacy_data(monkeypatch, tmp_path: Path) -> None:
    config_file, old_file = _paths(monkeypatch, tmp_path)
    old_file.parent.mkdir()
    old_file.write_text('{"initialized":true,"server_ip":"vpn.example.com"}', encoding="utf-8")
    config_file.write_text("initialized: false\n", encoding="utf-8")
    import pytest
    with pytest.raises(ValueError, match="旧 data/config.json"):
        cfg.load_config()


def test_yaml_auth_enabled_with_direct_password_is_hashed_and_cleared(monkeypatch, tmp_path: Path) -> None:
    config_file, _ = _paths(monkeypatch, tmp_path)
    config_file.write_text(
        'admin_login_enabled: true\nadmin_username: admin\nadmin_password: "custom-strong-password"\n',
        encoding="utf-8",
    )
    loaded = cfg.load_config()
    assert loaded.admin_login_enabled is True
    assert loaded.admin_password is None
    assert loaded.admin_password_hash.startswith("$argon2id$")
    assert "custom-strong-password" not in config_file.read_text(encoding="utf-8")


def test_yaml_auth_without_password_generates_unique_initial_secret(monkeypatch, tmp_path: Path) -> None:
    from app.services.auth import AdminAuthService
    config_file, _ = _paths(monkeypatch, tmp_path)
    config_file.write_text("admin_login_enabled: true\n", encoding="utf-8")
    loaded = cfg.load_config()
    secret_file = tmp_path / "data" / "admin_initial_password"
    password = secret_file.read_text(encoding="utf-8").strip()
    assert secret_file.stat().st_mode & 0o777 == 0o600
    assert len(password) >= 24
    assert loaded.admin_username == "admin"
    assert loaded.admin_login_enabled is True
    assert AdminAuthService().verify_credentials(
        submitted_username="admin",
        submitted_password=password,
        configured_username=loaded.admin_username,
        configured_password_hash=loaded.admin_password_hash,
    )
    assert cfg.load_config().admin_password_hash == loaded.admin_password_hash


def test_config_template_covers_all_system_config_fields() -> None:
    from app.models.config import SystemConfig

    template = Path(__file__).resolve().parents[2] / "config.example.yml"
    defaults = yaml.safe_load(template.read_text(encoding="utf-8"))
    assert set(SystemConfig.model_fields) <= set(defaults)
    assert defaults["admin_login_enabled"] is False
    assert defaults["admin_password"] is None
