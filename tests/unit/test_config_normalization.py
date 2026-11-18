# -*- coding: utf-8 -*-
"""部署 YAML、运行配置 JSON 的归属与历史迁移回归测试。"""

import json
from pathlib import Path

import pytest
import yaml
from box import Box

from app.core import config as cfg
from app.models.config import SystemConfig


def _paths(monkeypatch, tmp_path: Path):
    data = tmp_path / "data"
    deploy = tmp_path / "config.yml"
    runtime = data / "config.json"
    bootstrap = data / "admin_initial_password"
    monkeypatch.setattr(cfg, "CONFIG_FILE", deploy)
    monkeypatch.setattr(cfg, "RUNTIME_CONFIG_FILE", runtime)
    monkeypatch.setattr(cfg, "DATA_DIR", data)
    monkeypatch.setattr(cfg, "ADMIN_INITIAL_PASSWORD_FILE", bootstrap)
    monkeypatch.setattr(cfg, "ensure_openvpn_runtime_dirs", lambda: None)
    return deploy, runtime


def test_new_environment_uses_defaults_without_forcing_config_file(monkeypatch, tmp_path: Path) -> None:
    deploy, runtime = _paths(monkeypatch, tmp_path)
    loaded = cfg.load_config()
    assert loaded.initialized is False
    assert not deploy.exists()
    assert not runtime.exists()
    assert runtime.parent.is_dir()


def test_load_config_normalizes_empty_dict_optional_strings(monkeypatch, tmp_path: Path) -> None:
    _, runtime = _paths(monkeypatch, tmp_path)
    runtime.parent.mkdir()
    runtime.write_text(json.dumps({"pki_dir": {}, "easyrsa_dir": {}}), encoding="utf-8")
    loaded = cfg.load_config()
    assert loaded.pki_dir is None
    assert loaded.easyrsa_dir is None


def test_save_config_writes_runtime_json_without_touching_yaml(monkeypatch, tmp_path: Path) -> None:
    deploy, runtime = _paths(monkeypatch, tmp_path)
    original = "# 人工注释\nadmin_login_enabled: false\nadmin_username: admin\n"
    deploy.write_text(original, encoding="utf-8")
    cfg.save_config(Box({
        "pki_dir": {}, "easyrsa_dir": {}, "initialized": True,
        "admin_login_enabled": True,
        "admin_username": "someone-else",
        "global_subnet": "10.224.0.0/16",
    }, default_box=True))
    runtime_data = json.loads(runtime.read_text(encoding="utf-8"))
    assert runtime_data["initialized"] is True
    assert runtime_data["global_subnet"] == "10.224.0.0/16"
    assert runtime_data["pki_dir"] is None
    assert runtime_data["easyrsa_dir"] is None
    assert "admin_login_enabled" not in runtime_data
    assert "admin_username" not in runtime_data
    assert "admin_password" not in runtime_data
    assert deploy.read_text(encoding="utf-8") == original
    assert runtime.stat().st_mode & 0o777 == 0o600


def test_legacy_json_migrates_once_without_losing_settings(monkeypatch, tmp_path: Path) -> None:
    deploy, runtime = _paths(monkeypatch, tmp_path)
    runtime.parent.mkdir()
    runtime.write_text(json.dumps({
        "initialized": True,
        "global_subnet": "10.224.0.0/16",
        "instances": {"server": {"proto": "udp", "port": 1194}},
        "server_ip": "vpn.example.com",
        "admin_login_enabled": False,
        "admin_username": "oldadmin",
        "admin_session_lifetime_minutes": 60,
    }), encoding="utf-8")
    config = cfg.load_config()
    assert deploy.is_file()
    assert config.initialized is True
    assert config.server_ip == "vpn.example.com"
    assert config.instances["server"]["port"] == 1194
    assert config.admin_username == "oldadmin"
    assert yaml.safe_load(deploy.read_text(encoding="utf-8"))["admin_session_lifetime_minutes"] == 60
    runtime_data = json.loads(runtime.read_text(encoding="utf-8"))
    assert runtime_data["global_subnet"] == "10.224.0.0/16"
    assert "admin_username" not in runtime_data
    assert cfg.load_config().server_ip == "vpn.example.com"


def test_legacy_admin_login_remains_enabled_after_config_split(monkeypatch, tmp_path: Path) -> None:
    from app.services.auth import AdminAuthService

    deploy, runtime = _paths(monkeypatch, tmp_path)
    runtime.parent.mkdir()
    original_hash = AdminAuthService().hash_password("legacy-strong-password")
    runtime.write_text(json.dumps({
        "initialized": True,
        "admin_username": "legacyadmin",
        "admin_password_hash": original_hash,
        "admin_session_lifetime_minutes": 120,
    }), encoding="utf-8")

    loaded = cfg.load_config()
    assert loaded.admin_login_enabled is True
    assert loaded.admin_username == "legacyadmin"
    assert loaded.admin_password_hash == original_hash
    assert yaml.safe_load(deploy.read_text(encoding="utf-8"))["admin_login_enabled"] is True
    assert json.loads(runtime.read_text(encoding="utf-8"))["admin_password_hash"] == original_hash
    assert "admin_username" not in json.loads(runtime.read_text(encoding="utf-8"))
    assert cfg.load_config().admin_password_hash == original_hash


def test_deployment_params_win_but_runtime_json_remains_sole_mutable_source(monkeypatch, tmp_path: Path) -> None:
    deploy, runtime = _paths(monkeypatch, tmp_path)
    runtime.parent.mkdir()
    runtime.write_text(json.dumps({
        "admin_login_enabled": True,
        "admin_username": "legacy",
        "admin_session_lifetime_minutes": 60,
        "global_subnet": "10.224.0.0/16",
    }), encoding="utf-8")
    deploy.write_text("admin_login_enabled: false\nadmin_username: operator\n", encoding="utf-8")
    value = cfg.load_config()
    assert value.admin_login_enabled is False
    assert value.admin_username == "operator"
    cfg.save_config(value)
    assert "admin_username" not in json.loads(runtime.read_text(encoding="utf-8"))
    assert cfg.load_config().global_subnet == "10.224.0.0/16"


def test_root_yaml_rejects_runtime_only_fields(monkeypatch, tmp_path: Path) -> None:
    deploy, _ = _paths(monkeypatch, tmp_path)
    deploy.write_text("global_subnet: 10.224.0.0/16\n", encoding="utf-8")
    with pytest.raises(ValueError, match="只能放部署参数"):
        cfg.load_config()


def test_yaml_auth_enabled_with_direct_password_is_bootstrapped_once(monkeypatch, tmp_path: Path) -> None:
    deploy, runtime = _paths(monkeypatch, tmp_path)
    original = (
        '# 启动密码\nadmin_login_enabled: true\nadmin_username: admin\n'
        'admin_password: "custom-strong-password"\n'
    )
    deploy.write_text(original, encoding="utf-8")
    loaded = cfg.load_config()
    assert loaded.admin_login_enabled is True
    assert loaded.admin_password is None
    assert loaded.admin_password_hash.startswith("$argon2id$")
    assert "custom-strong-password" not in runtime.read_text(encoding="utf-8")
    assert deploy.read_text(encoding="utf-8") == original
    assert cfg.load_config().admin_password_hash == loaded.admin_password_hash


def test_yaml_auth_without_password_generates_unique_initial_secret(monkeypatch, tmp_path: Path) -> None:
    from app.services.auth import AdminAuthService

    deploy, runtime = _paths(monkeypatch, tmp_path)
    deploy.write_text("admin_login_enabled: true\n", encoding="utf-8")
    loaded = cfg.load_config()
    secret_file = runtime.parent / "admin_initial_password"
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


def test_config_template_only_covers_deployment_fields() -> None:
    template = Path(__file__).resolve().parents[2] / "config.example.yml"
    defaults = yaml.safe_load(template.read_text(encoding="utf-8"))
    assert set(defaults) == cfg.DEPLOYMENT_CONFIG_FIELDS
    assert defaults["admin_login_enabled"] is False
    assert defaults["admin_password"] is None
    assert "global_subnet" not in defaults
    assert "admin_password_hash" not in defaults
    assert "global_subnet" in SystemConfig.model_fields
