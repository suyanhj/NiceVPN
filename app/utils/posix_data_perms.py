# -*- coding: utf-8 -*-
"""Linux 下 OpenVPN 相关路径的属主：安装/初始化/写文件后执行 chown，与 shell 一致。

策略（按产品约定）：
- 整棵 /etc/openvpn：`chown -R nobody:nobody`（与 server.conf 中 user/group nobody 一致）。
- 外置 PKI 目录（不在 /etc/openvpn 下时）同样递归 chown 为 nobody:nobody。
- 管理端单独写的单个 CCD/绑定文件：若仍为 root 创建，则对该路径再 chown nobody:nobody。
"""
import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_OPENVPN_DROP_USER = "nobody"


def _nobody_uid_gid() -> tuple[int, int]:
    """返回 (uid, gid)，优先使用组 nobody，不存在则用 nobody 的主属组。"""
    if os.name == "nt":
        raise OSError("非 POSIX 环境")
    import grp
    import pwd

    pw = pwd.getpwnam(_OPENVPN_DROP_USER)
    uid = pw.pw_uid
    try:
        gid = grp.getgrnam(_OPENVPN_DROP_USER).gr_gid
    except KeyError:
        gid = pw.pw_gid
    return uid, gid


def chown_recursive_nobody_nobody(target: Path) -> None:
    """
    等价于 shell：`chown -R nobody:nobody <target>`。
    失败时记录日志并抛出，不静默忽略。
    """
    if os.name == "nt":
        return
    target = target.resolve()
    if not target.exists():
        logger.warning("chown 跳过，路径不存在: %s", target)
        return
    try:
        subprocess.run(
            ["chown", "-R", f"{_OPENVPN_DROP_USER}:{_OPENVPN_DROP_USER}", str(target)],
            check=True,
            capture_output=True,
            text=True,
        )
        logger.info("已执行 chown -R nobody:nobody %s", target)
    except FileNotFoundError as e:
        logger.error("系统无 chown 命令: %s", e)
        raise RuntimeError("无法执行 chown，请确认运行在 Linux 且 PATH 含 coreutils") from e
    except subprocess.CalledProcessError as e:
        err = (e.stderr or e.stdout or "").strip()
        logger.error("chown -R nobody:nobody 失败: %s, stderr=%s", target, err)
        raise RuntimeError(f"chown -R nobody:nobody 失败: {target}: {err}") from e


def apply_openvpn_runtime_permissions(openvpn_conf_dir: Path, pki_dir: Path) -> None:
    """
    安装/初始化/写配置后调用：对 openvpn 配置目录与外置 PKI 做 nobody:nobody 递归属主。
    """
    if os.name == "nt":
        return
    openvpn_conf_dir = openvpn_conf_dir.resolve()
    pki_dir = pki_dir.resolve()
    if openvpn_conf_dir.is_dir():
        chown_recursive_nobody_nobody(openvpn_conf_dir)
    else:
        logger.warning("OpenVPN 配置目录不存在，跳过 chown: %s", openvpn_conf_dir)

    pki_under = False
    try:
        pki_dir.relative_to(openvpn_conf_dir)
        pki_under = True
    except ValueError:
        pass
    if pki_dir.is_dir() and not pki_under:
        chown_recursive_nobody_nobody(pki_dir)


def sync_openvpn_runtime_permissions_from_config() -> None:
    """读取配置中的 openvpn_conf_dir、pki_dir 后调用 apply_openvpn_runtime_permissions。"""
    if os.name == "nt":
        return
    from app.core.config import load_config

    cfg = load_config()
    base_s = str(cfg.get("openvpn_conf_dir") or "").strip() or "/etc/openvpn"
    base = Path(base_s)
    pki_s = str(cfg.get("pki_dir") or "").strip()
    pki = Path(pki_s) if pki_s else base / "pki"
    apply_openvpn_runtime_permissions(base, pki)


def fix_path_for_openvpn_shared_data(path: Path) -> None:
    """root 写完 CCD 或 device_bindings 下文件后，将该路径属主改为 nobody（与整树 chown 策略一致）。"""
    if os.name == "nt":
        return
    path = path.resolve()
    from app.core.constants import CCD_DIR, DEVICE_BINDINGS_DIR

    under_ccd = False
    under_bind = False
    try:
        path.relative_to(CCD_DIR.resolve())
        under_ccd = True
    except ValueError:
        pass
    try:
        path.relative_to(DEVICE_BINDINGS_DIR.resolve())
        under_bind = True
    except ValueError:
        pass
    if not under_ccd and not under_bind:
        return
    uid, gid = _nobody_uid_gid()
    try:
        os.chown(path, uid, gid)
    except OSError as e:
        logger.error("无法 chown nobody 路径 %s: %s", path, e)
        raise


def ensure_device_bind_log_file() -> None:
    """保证设备绑定日志存在，并属主 nobody:nobody（单文件 chown）。"""
    if os.name == "nt":
        return
    from app.core.constants import DEVICE_BIND_LOG_FILE

    p = DEVICE_BIND_LOG_FILE
    uid, gid = _nobody_uid_gid()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.touch()
        os.chown(p, uid, gid)
    except OSError as e:
        logger.error("无法准备设备绑定日志 %s: %s", p, e)
        raise
