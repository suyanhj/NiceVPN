# -*- coding: utf-8 -*-
"""NiceVPN 完整业务备份与恢复。

备份包含恢复 VPN 服务所需的持久状态，不包含运行日志、审计历史和一次性下载文件。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import uuid4

from app.core.config import load_config
from app.core.constants import (
    ADMIN_SESSION_SECRET_FILE,
    API_BASIC_CREDENTIALS_FILE,
    BACKUPS_DIR,
    CONFIG_FILE,
    DATA_DIR,
    REMOTE_PEER_CHAINS_DIR,
    SQLITE_DB_FILE,
)
from app.models.config import SystemConfig
from app.services.easyrsa.wrapper import inspect_easyrsa_pki
from app.services.openvpn.script_sync import sync_packaged_openvpn_scripts
from app.storage.sqlite_store import SCHEMA_VERSION
from app.utils.posix_data_perms import sync_openvpn_runtime_permissions_from_config


BACKUP_FORMAT = "nicevpn-full-backup"
BACKUP_FORMAT_VERSION = 1


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_file(src: Path, dst: Path) -> bool:
    if not src.is_file():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def _copy_tree(src: Path, dst: Path) -> bool:
    """复制目录状态；源不存在时仍创建空目标，便于恢复时清理旧残留。"""

    dst.mkdir(parents=True, exist_ok=True)
    if not src.is_dir():
        return False
    shutil.copytree(src, dst, dirs_exist_ok=True)
    return True


def _sqlite_snapshot(src: Path, dst: Path) -> None:
    if not src.is_file():
        raise FileNotFoundError(f"SQLite 数据库不存在: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=10) as source:
        with sqlite3.connect(dst, timeout=10) as target:
            source.backup(target)
            check = target.execute("PRAGMA quick_check").fetchall()
            values = [str(row[0]) for row in check if row]
            if values != ["ok"]:
                raise RuntimeError("SQLite 备份 quick_check 失败: " + "; ".join(values[:5]))


def _payload_files(payload: Path) -> list[Path]:
    return sorted(path for path in payload.rglob("*") if path.is_file())


def _manifest_for(payload: Path, *, config: dict) -> dict:
    files: dict[str, dict[str, object]] = {}
    for path in _payload_files(payload):
        rel = path.relative_to(payload).as_posix()
        files[rel] = {
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        }

    return {
        "format": BACKUP_FORMAT,
        "format_version": BACKUP_FORMAT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "openvpn_conf_dir": str(config.get("openvpn_conf_dir") or "/etc/openvpn"),
            "pki_dir": str(config.get("pki_dir") or ""),
        },
        "files": files,
    }


def _copy_backup_payload(payload: Path, config: dict) -> None:
    app_dir = payload / "app"
    vpn_dir = payload / "openvpn"
    app_dir.mkdir(parents=True, exist_ok=True)
    vpn_dir.mkdir(parents=True, exist_ok=True)

    _sqlite_snapshot(Path(SQLITE_DB_FILE), app_dir / "nicevpn.db")
    _copy_file(Path(CONFIG_FILE), app_dir / "config.json")
    _copy_file(Path(API_BASIC_CREDENTIALS_FILE), app_dir / "api_basic_credentials.json")
    _copy_file(Path(ADMIN_SESSION_SECRET_FILE), app_dir / "admin_session_secret")
    _copy_tree(Path(REMOTE_PEER_CHAINS_DIR), app_dir / "remote_peer_chains")

    openvpn_base = Path(str(config.get("openvpn_conf_dir") or "/etc/openvpn"))
    pki_raw = str(config.get("pki_dir") or "").strip()
    pki_dir = Path(pki_raw) if pki_raw else openvpn_base / "pki"

    _copy_tree(pki_dir, vpn_dir / "pki")
    _copy_tree(openvpn_base / "server", vpn_dir / "server")
    _copy_tree(openvpn_base / "client", vpn_dir / "client")
    _copy_tree(openvpn_base / "ccd", vpn_dir / "ccd")
    _copy_tree(
        openvpn_base / "mgmt" / "device_bindings",
        vpn_dir / "mgmt" / "device_bindings",
    )
    _copy_tree(openvpn_base / "mgmt" / "ovpn", vpn_dir / "mgmt" / "ovpn")
    _copy_file(
        openvpn_base / "mgmt" / "device_bind_mode",
        vpn_dir / "mgmt" / "device_bind_mode",
    )
    _copy_file(pki_dir.parent / "tc2-server.key", vpn_dir / "tc2-server.key")


def create_full_backup(*, label: str = "manual") -> Path:
    """创建带 manifest 和 SHA-256 的完整业务备份。"""

    config = dict(load_config())
    backup_dir = Path(BACKUPS_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True)
    safe_label = "".join(ch for ch in str(label or "manual") if ch.isalnum() or ch in "-_")
    safe_label = safe_label or "manual"
    archive = backup_dir / f"nicevpn-{safe_label}-{_utc_stamp()}.tar.gz"

    with tempfile.TemporaryDirectory(prefix=".nicevpn-backup-", dir=backup_dir) as tmp:
        root = Path(tmp)
        payload = root / "payload"
        _copy_backup_payload(payload, config)
        manifest = _manifest_for(payload, config=config)
        (root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # 创建阶段就按恢复规则自校验，拒绝产出不可恢复的“成功备份”。
        _validate_restore_payload(root)

        with tarfile.open(archive, "w:gz") as tar:
            tar.add(root / "manifest.json", arcname="manifest.json", recursive=False)
            tar.add(payload, arcname="payload")

    if os.name != "nt":
        archive.chmod(0o600)
    return archive


def _safe_extract(archive: Path, target: Path) -> None:
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts:
                raise ValueError(f"备份包含非法路径: {member.name}")
            if member.issym() or member.islnk():
                raise ValueError(f"备份不允许符号链接/硬链接: {member.name}")
            if not (member.isfile() or member.isdir()):
                raise ValueError(f"备份包含不支持的成员类型: {member.name}")
        tar.extractall(target)


def _load_verified_manifest(root: Path) -> dict:
    manifest_path = root / "manifest.json"
    payload = root / "payload"
    if not manifest_path.is_file() or not payload.is_dir():
        raise ValueError("不是有效的 NiceVPN 完整备份：缺少 manifest.json 或 payload")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"备份 manifest 无法读取: {exc}") from exc

    if manifest.get("format") != BACKUP_FORMAT:
        raise ValueError("备份格式不匹配")
    if int(manifest.get("format_version") or 0) != BACKUP_FORMAT_VERSION:
        raise ValueError(
            f"不支持的备份格式版本: {manifest.get('format_version')}"
        )

    expected = manifest.get("files")
    if not isinstance(expected, dict):
        raise ValueError("备份 manifest.files 无效")

    actual_files = {
        path.relative_to(payload).as_posix(): path
        for path in _payload_files(payload)
    }
    if set(actual_files) != set(expected):
        missing = sorted(set(expected) - set(actual_files))
        extra = sorted(set(actual_files) - set(expected))
        raise ValueError(
            f"备份文件清单不一致 missing={missing[:5]} extra={extra[:5]}"
        )

    for rel, meta in expected.items():
        if not isinstance(meta, dict):
            raise ValueError(f"备份 manifest 条目无效: {rel}")
        path = actual_files[rel]
        if int(meta.get("size") or -1) != path.stat().st_size:
            raise ValueError(f"备份文件大小校验失败: {rel}")
        if str(meta.get("sha256") or "") != _sha256(path):
            raise ValueError(f"备份 SHA-256 校验失败: {rel}")

    return manifest


def _validate_nicevpn_sqlite(path: Path) -> None:
    required_tables = {
        "schema_migrations",
        "storage_meta",
        "users",
        "groups",
        "peers",
        "firewall_rules",
    }
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10) as conn:
        values = [
            str(row[0])
            for row in conn.execute("PRAGMA quick_check").fetchall()
            if row
        ]
        if values != ["ok"]:
            raise ValueError(
                "备份 SQLite quick_check 失败: " + "; ".join(values[:5])
            )
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing = sorted(required_tables - tables)
        if missing:
            raise ValueError(
                "备份不是完整的 NiceVPN SQLite，缺少表: " + ", ".join(missing)
            )
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()
        version = int((row or [0])[0] or 0)
        if version <= 0:
            raise ValueError("备份 SQLite 缺少 schema 版本")
        if version > SCHEMA_VERSION:
            raise ValueError(
                f"备份 SQLite schema v{version} 高于当前支持的 v{SCHEMA_VERSION}"
            )


def _validate_restore_payload(root: Path) -> dict:
    manifest = _load_verified_manifest(root)
    payload = root / "payload"
    config_path = payload / "app" / "config.json"
    db_path = payload / "app" / "nicevpn.db"
    pki_dir = payload / "openvpn" / "pki"

    if not config_path.is_file():
        raise ValueError("备份缺少 app/config.json")
    if not db_path.is_file():
        raise ValueError("备份缺少 app/nicevpn.db")
    pki_state = inspect_easyrsa_pki(str(pki_dir))
    if not pki_state.get("ready"):
        missing = ", ".join(str(item) for item in pki_state.get("missing") or [])
        raise ValueError(
            "备份 PKI 不完整"
            + (f"：缺少 {missing}" if missing else "")
        )
    if not (payload / "openvpn" / "tc2-server.key").is_file():
        raise ValueError("备份缺少 openvpn/tc2-server.key")

    try:
        config_data = json.loads(config_path.read_text(encoding="utf-8"))
        SystemConfig.model_validate(config_data)
    except Exception as exc:
        raise ValueError(f"备份 config.json 校验失败: {exc}") from exc

    _validate_nicevpn_sqlite(db_path)

    return manifest


def inspect_full_backup(archive_path: str | Path) -> dict:
    """校验备份并返回不含秘密内容的摘要。"""

    archive = Path(archive_path)
    if not archive.is_file():
        raise FileNotFoundError(f"备份文件不存在: {archive}")
    with tempfile.TemporaryDirectory(prefix=".nicevpn-inspect-") as tmp:
        root = Path(tmp)
        _safe_extract(archive, root)
        manifest = _validate_restore_payload(root)
        return {
            "format_version": manifest["format_version"],
            "created_at": manifest["created_at"],
            "file_count": len(manifest["files"]),
            "source": dict(manifest.get("source") or {}),
        }


def _validate_restore_target(path: Path, label: str) -> Path:
    """拒绝把备份中的路径指向根目录或过浅系统目录。"""

    resolved = path.expanduser().resolve()
    if not resolved.is_absolute() or len(resolved.parts) < 3:
        raise ValueError(f"{label} 恢复路径不安全: {resolved}")
    return resolved


def _replace_file(src: Path, dst: Path) -> None:
    if not src.is_file():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    stage = dst.parent / f".{dst.name}.restore-{uuid4().hex}"
    shutil.copy2(src, stage)
    os.replace(stage, dst)


def _replace_optional_file(src: Path, dst: Path) -> None:
    """恢复可选文件；备份中不存在时清理当前残留，保持快照语义。"""

    if src.is_file():
        _replace_file(src, dst)
        return
    try:
        dst.unlink(missing_ok=True)
    except IsADirectoryError as exc:
        raise RuntimeError(f"恢复目标应为文件但实际为目录: {dst}") from exc


def _replace_tree(src: Path, dst: Path) -> None:
    if not src.is_dir():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    stage = dst.parent / f".{dst.name}.restore-{uuid4().hex}"
    old = dst.parent / f".{dst.name}.restore-old-{uuid4().hex}"
    shutil.copytree(src, stage)
    moved_old = False
    try:
        if dst.exists():
            os.replace(dst, old)
            moved_old = True
        os.replace(stage, dst)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        if moved_old and old.exists() and not dst.exists():
            os.replace(old, dst)
        raise
    finally:
        if old.exists():
            shutil.rmtree(old, ignore_errors=True)


def _restore_sqlite(src: Path) -> None:
    dst = Path(SQLITE_DB_FILE)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=10) as source:
        with sqlite3.connect(dst, timeout=10) as target:
            source.backup(target)
            target.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            values = [
                str(row[0])
                for row in target.execute("PRAGMA quick_check").fetchall()
                if row
            ]
            if values != ["ok"]:
                raise RuntimeError(
                    "恢复后的 SQLite quick_check 失败: " + "; ".join(values[:5])
                )


def restore_full_backup(archive_path: str | Path) -> dict:
    """校验并恢复完整业务备份；恢复前自动生成当前状态备份。"""

    archive = Path(archive_path)
    if not archive.is_file():
        raise FileNotFoundError(f"备份文件不存在: {archive}")

    with tempfile.TemporaryDirectory(prefix=".nicevpn-restore-") as tmp:
        root = Path(tmp)
        _safe_extract(archive, root)
        manifest = _validate_restore_payload(root)
        payload = root / "payload"

        pre_restore = create_full_backup(label="pre-restore")

        config_data = json.loads(
            (payload / "app" / "config.json").read_text(encoding="utf-8")
        )
        openvpn_base = _validate_restore_target(
            Path(str(config_data.get("openvpn_conf_dir") or "/etc/openvpn")),
            "OpenVPN",
        )
        pki_raw = str(config_data.get("pki_dir") or "").strip()
        pki_target = _validate_restore_target(
            Path(pki_raw) if pki_raw else openvpn_base / "pki",
            "PKI",
        )

        _restore_sqlite(payload / "app" / "nicevpn.db")
        _replace_file(payload / "app" / "config.json", Path(CONFIG_FILE))
        _replace_optional_file(
            payload / "app" / "api_basic_credentials.json",
            Path(API_BASIC_CREDENTIALS_FILE),
        )
        _replace_optional_file(
            payload / "app" / "admin_session_secret",
            Path(ADMIN_SESSION_SECRET_FILE),
        )
        _replace_tree(
            payload / "app" / "remote_peer_chains",
            Path(REMOTE_PEER_CHAINS_DIR),
        )

        vpn = payload / "openvpn"
        _replace_tree(vpn / "pki", pki_target)
        _replace_tree(vpn / "server", openvpn_base / "server")
        _replace_tree(vpn / "client", openvpn_base / "client")
        _replace_tree(vpn / "ccd", openvpn_base / "ccd")
        _replace_tree(
            vpn / "mgmt" / "device_bindings",
            openvpn_base / "mgmt" / "device_bindings",
        )
        _replace_tree(vpn / "mgmt" / "ovpn", openvpn_base / "mgmt" / "ovpn")
        _replace_optional_file(
            vpn / "mgmt" / "device_bind_mode",
            openvpn_base / "mgmt" / "device_bind_mode",
        )
        _replace_file(vpn / "tc2-server.key", pki_target.parent / "tc2-server.key")

        # 脚本属于当前程序版本的托管代码，不从备份覆盖。
        sync_packaged_openvpn_scripts(openvpn_base)
        sync_openvpn_runtime_permissions_from_config()

        return {
            "restored": True,
            "backup_created_at": manifest["created_at"],
            "pre_restore_backup": str(pre_restore),
            "requires_restart": True,
        }
