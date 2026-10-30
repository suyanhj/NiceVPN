# -*- coding: utf-8 -*-
"""NiceVPN 系统一致性诊断。

诊断默认只读；safe_repair 仅执行可重复、不会重建 CA/修改网络策略的同步操作。
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from app.core.config import load_config
from app.core.constants import CCD_DIR, OVPN_PROFILES_DIR, SQLITE_DB_FILE
from app.services.easyrsa.wrapper import inspect_easyrsa_pki
from app.services.openvpn.detector import detect_openvpn
from app.services.openvpn.instance import is_instance_active, resolve_server_conf_dir
from app.services.openvpn.script_sync import (
    OPENVPN_SCRIPT_NAMES,
    sync_packaged_openvpn_scripts,
)
from app.services.user.crud import UserService
from app.utils.posix_data_perms import sync_openvpn_runtime_permissions_from_config


DiagnosticStatus = Literal["ok", "warning", "error"]


@dataclass(slots=True)
class DiagnosticCheck:
    key: str
    title: str
    status: DiagnosticStatus
    detail: str
    repairable: bool = False

    def model_dump(self) -> dict:
        return asdict(self)


class SystemDiagnosticsService:
    """检查 NiceVPN 管理状态与 OpenVPN 运行材料是否一致。"""

    def __init__(self) -> None:
        self.config = load_config()

    def run(self) -> list[dict]:
        checks = [
            self._check_openvpn(),
            self._check_easyrsa(),
            self._check_pki(),
            self._check_crl(),
            self._check_tls_crypt_v2(),
            self._check_sqlite(),
            self._check_instances(),
            self._check_ip_forward(),
            self._check_scripts(),
            self._check_user_materials(),
        ]
        return [check.model_dump() for check in checks]

    def _check_openvpn(self) -> DiagnosticCheck:
        try:
            detected = detect_openvpn()
        except Exception as exc:
            return DiagnosticCheck("openvpn", "OpenVPN", "error", f"检测失败: {exc}")

        if not detected.get("installed"):
            return DiagnosticCheck("openvpn", "OpenVPN", "error", "未检测到 OpenVPN 可执行文件")
        version = str(detected.get("version") or "未知版本")
        path = str(detected.get("path") or "")
        if not detected.get("meets_requirement"):
            return DiagnosticCheck(
                "openvpn",
                "OpenVPN",
                "warning",
                f"{path} · {version}，低于项目推荐版本",
            )
        return DiagnosticCheck("openvpn", "OpenVPN", "ok", f"{path} · {version}")

    def _check_easyrsa(self) -> DiagnosticCheck:
        raw = str(self.config.get("easyrsa_dir") or "").strip()
        if not raw:
            return DiagnosticCheck("easyrsa", "EasyRSA", "error", "未配置 EasyRSA 路径")
        path = Path(raw)
        if not path.is_file():
            return DiagnosticCheck("easyrsa", "EasyRSA", "error", f"文件不存在: {path}")
        if os.name != "nt" and not os.access(path, os.X_OK):
            return DiagnosticCheck("easyrsa", "EasyRSA", "warning", f"脚本不可执行: {path}")
        return DiagnosticCheck("easyrsa", "EasyRSA", "ok", str(path))

    def _pki_dir(self) -> Path:
        return Path(str(self.config.get("pki_dir") or "").strip())

    def _check_pki(self) -> DiagnosticCheck:
        raw = str(self.config.get("pki_dir") or "").strip()
        if not raw:
            return DiagnosticCheck("pki", "PKI", "error", "未配置 PKI 目录")
        state = inspect_easyrsa_pki(raw)
        if state.get("ready"):
            return DiagnosticCheck("pki", "PKI", "ok", f"PKI 完整: {raw}")
        missing = ", ".join(str(x) for x in state.get("missing") or [])
        return DiagnosticCheck(
            "pki",
            "PKI",
            "error",
            f"PKI 状态={state.get('state')}，缺失: {missing or '未知'}",
        )

    def _check_crl(self) -> DiagnosticCheck:
        path = self._pki_dir() / "crl.pem"
        if path.is_file() and path.stat().st_size > 0:
            return DiagnosticCheck("crl", "CRL", "ok", str(path))
        return DiagnosticCheck("crl", "CRL", "error", f"CRL 不存在或为空: {path}")

    def _check_tls_crypt_v2(self) -> DiagnosticCheck:
        path = self._pki_dir().parent / "tc2-server.key"
        if path.is_file() and path.stat().st_size > 0:
            return DiagnosticCheck("tls_crypt_v2", "TLS-Crypt-V2", "ok", str(path))
        return DiagnosticCheck(
            "tls_crypt_v2",
            "TLS-Crypt-V2",
            "error",
            f"服务端密钥不存在或为空: {path}",
        )

    def _check_sqlite(self) -> DiagnosticCheck:
        path = Path(SQLITE_DB_FILE)
        if not path.is_file():
            return DiagnosticCheck("sqlite", "SQLite", "error", f"数据库不存在: {path}")
        try:
            with sqlite3.connect(path, timeout=5) as conn:
                rows = conn.execute("PRAGMA quick_check").fetchall()
        except sqlite3.Error as exc:
            return DiagnosticCheck("sqlite", "SQLite", "error", f"quick_check 失败: {exc}")

        values = [str(row[0]) for row in rows if row]
        if values == ["ok"]:
            return DiagnosticCheck("sqlite", "SQLite", "ok", f"quick_check=ok · {path}")
        return DiagnosticCheck(
            "sqlite",
            "SQLite",
            "error",
            "quick_check: " + "; ".join(values[:5]),
        )

    def _check_instances(self) -> DiagnosticCheck:
        instances = self.config.get("instances") or {}
        if not instances:
            return DiagnosticCheck("instances", "OpenVPN 实例", "warning", "未登记 OpenVPN 实例")

        base = str(self.config.get("openvpn_conf_dir") or "/etc/openvpn")
        conf_dir = resolve_server_conf_dir(base)
        missing_conf: list[str] = []
        inactive: list[str] = []
        for name in sorted(instances):
            if not (conf_dir / f"{name}.conf").is_file():
                missing_conf.append(str(name))
            try:
                if not is_instance_active(str(name)):
                    inactive.append(str(name))
            except Exception:
                inactive.append(str(name))

        if missing_conf:
            return DiagnosticCheck(
                "instances",
                "OpenVPN 实例",
                "error",
                "缺少 server.conf: " + ", ".join(missing_conf[:8]),
            )
        if inactive:
            return DiagnosticCheck(
                "instances",
                "OpenVPN 实例",
                "warning",
                "未运行: " + ", ".join(inactive[:8]),
            )
        return DiagnosticCheck(
            "instances",
            "OpenVPN 实例",
            "ok",
            f"{len(instances)} 个实例配置存在且 systemd 为 active",
        )

    def _check_ip_forward(self) -> DiagnosticCheck:
        path = Path("/proc/sys/net/ipv4/ip_forward")
        if not path.is_file():
            return DiagnosticCheck(
                "ip_forward",
                "IPv4 Forward",
                "warning",
                "当前系统无法读取 /proc/sys/net/ipv4/ip_forward",
            )
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            return DiagnosticCheck("ip_forward", "IPv4 Forward", "warning", str(exc))
        if value == "1":
            return DiagnosticCheck("ip_forward", "IPv4 Forward", "ok", "net.ipv4.ip_forward=1")
        return DiagnosticCheck(
            "ip_forward",
            "IPv4 Forward",
            "error",
            f"net.ipv4.ip_forward={value or '?'}",
        )

    def _check_scripts(self) -> DiagnosticCheck:
        base = Path(str(self.config.get("openvpn_conf_dir") or "/etc/openvpn"))
        scripts_dir = base / "scripts"
        missing = [
            name for name in OPENVPN_SCRIPT_NAMES
            if not (scripts_dir / name).is_file()
        ]
        if missing:
            return DiagnosticCheck(
                "scripts",
                "OpenVPN 钩子脚本",
                "error",
                "缺失: " + ", ".join(missing),
                repairable=True,
            )
        return DiagnosticCheck(
            "scripts",
            "OpenVPN 钩子脚本",
            "ok",
            f"{len(OPENVPN_SCRIPT_NAMES)} 个托管脚本齐全",
            repairable=True,
        )

    def _user_material_state(self, user: dict) -> tuple[list[str], bool]:
        username = str(user.get("username") or "").strip()
        pki = self._pki_dir()
        paths = {
            "证书": pki / "issued" / f"{username}.crt",
            "私钥": pki / "private" / f"{username}.key",
            "tls-crypt-v2": pki / "tc2-clients" / f"{username}.key",
            "CCD": CCD_DIR / username,
        }
        missing = [label for label, path in paths.items() if not path.is_file()]

        ovpn_raw = str(user.get("ovpn_file_path") or "").strip()
        ovpn = Path(ovpn_raw) if ovpn_raw else OVPN_PROFILES_DIR / f"{username}.ovpn"
        if not ovpn.is_file():
            missing.append("OVPN")
            return missing, False

        if missing:
            return missing, False

        try:
            text = ovpn.read_text(encoding="utf-8")
            expected = (
                paths["证书"].read_text(encoding="utf-8").strip(),
                paths["私钥"].read_text(encoding="utf-8").strip(),
                paths["tls-crypt-v2"].read_text(encoding="utf-8").strip(),
            )
        except OSError:
            return ["OVPN/PKI 读取失败"], False

        stale = any(value and value not in text for value in expected)
        return [], stale

    def _check_user_materials(self) -> DiagnosticCheck:
        users = UserService().list_all()
        missing_users: list[str] = []
        stale_users: list[str] = []
        for user in users:
            missing, stale = self._user_material_state(user)
            username = str(user.get("username") or "")
            if missing:
                missing_users.append(f"{username}({','.join(missing)})")
            elif stale:
                stale_users.append(username)

        if missing_users:
            detail = "材料缺失: " + "；".join(missing_users[:5])
            if len(missing_users) > 5:
                detail += f"；另 {len(missing_users) - 5} 个"
            return DiagnosticCheck(
                "user_materials",
                "用户证书 / OVPN / CCD",
                "error",
                detail,
                repairable=True,
            )
        if stale_users:
            detail = "OVPN 与当前 PKI 不一致: " + ", ".join(stale_users[:8])
            return DiagnosticCheck(
                "user_materials",
                "用户证书 / OVPN / CCD",
                "warning",
                detail,
                repairable=True,
            )
        return DiagnosticCheck(
            "user_materials",
            "用户证书 / OVPN / CCD",
            "ok",
            f"{len(users)} 个用户材料一致",
            repairable=True,
        )

    def safe_repair(self) -> list[str]:
        """仅执行安全的幂等同步，不重建 CA、不改 sysctl/路由/防火墙策略。"""

        actions: list[str] = []
        base = str(self.config.get("openvpn_conf_dir") or "/etc/openvpn")

        sync_packaged_openvpn_scripts(base)
        actions.append("已同步 OpenVPN 托管脚本")

        sync_openvpn_runtime_permissions_from_config()
        actions.append("已同步 OpenVPN 运行目录权限")

        user_service = UserService()
        regenerated = 0
        skipped = 0
        for user in user_service.list_all():
            missing, stale = self._user_material_state(user)
            username = str(user.get("username") or "")
            only_ovpn_missing = bool(missing) and set(missing) == {"OVPN"}
            if not stale and not only_ovpn_missing:
                if missing:
                    skipped += 1
                continue
            try:
                user_service.regenerate_ovpn(username)
                regenerated += 1
            except Exception:
                skipped += 1

        if regenerated:
            actions.append(f"已重建 {regenerated} 个缺失或过期的 OVPN")
        if skipped:
            actions.append(f"{skipped} 个用户缺少底层材料，未自动修复")
        return actions
