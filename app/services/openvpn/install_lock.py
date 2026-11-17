"""OpenVPN 安装/升级跨进程锁。与 Shell 入口的 flock 文件保持一致。"""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

OPENVPN_INSTALL_LOCK_PATH = Path("/run/lock/nicevpn-install.lock")


@contextmanager
def openvpn_install_lock() -> Iterator[None]:
    """获取安装锁；已有安装或升级时立即失败，不等待或并发修改 prefix。"""
    lock_path = OPENVPN_INSTALL_LOCK_PATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock_file = lock_path.open("a+")
    except OSError as exc:
        raise RuntimeError(f"无法打开 OpenVPN 安装锁 {lock_path}: {exc}") from exc

    with lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("OpenVPN 安装/升级正在执行，请稍后重试") from exc
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
