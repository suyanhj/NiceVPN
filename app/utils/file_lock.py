"""JSON 文件原子写入工具

提供文件锁保护的 JSON 读写操作，确保并发安全和写入原子性。
- Linux 平台使用 fcntl.flock 实现文件锁
- Windows 平台降级为 threading.Lock（进程内互斥）
写入流程：先写临时文件，再 rename 到目标路径，保证原子性。
"""

import json
import os
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# ---- 平台适配：选择锁实现 ----

_is_posix = sys.platform != "win32"

if _is_posix:
    import fcntl

# Windows 降级方案：用全局字典按路径分配 threading.Lock
_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


def _get_thread_lock(path: str) -> threading.Lock:
    """按文件路径获取或创建对应的线程锁（Windows 降级用）。"""
    with _thread_locks_guard:
        if path not in _thread_locks:
            _thread_locks[path] = threading.Lock()
        return _thread_locks[path]


@contextmanager
def file_lock(path: str | Path):
    """文件锁上下文管理器。

    Linux：基于 fcntl.flock 的排他锁，使用临时 ``<path>.lock`` 作为锁载体，退出上下文后删除该文件。
    Windows：降级为 threading.Lock，仅保证同进程内互斥。

    用法::

        with file_lock("/data/users.json"):
            # 在此区间内对文件的读写是安全的
            ...
    """
    path = str(Path(path).resolve())

    if _is_posix:
        lock_path = path + ".lock"
        # 确保锁文件所在目录存在
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        fd = open(lock_path, "w")  # noqa: SIM115
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()
            # 仅作 flock 载体，若保留空文件会导致全目录扫描（如设备绑定）时遗留大量 *.json.lock
            try:
                os.unlink(lock_path)
            except OSError:
                pass
    else:
        # Windows 降级：线程锁
        lock = _get_thread_lock(path)
        lock.acquire()
        try:
            yield
        finally:
            lock.release()


def write_json_atomic(path: str | Path, data: Any) -> None:
    """原子写入 JSON 文件。

    流程：
    1. 获取文件锁
    2. 将数据序列化并写入同目录下的临时文件
    3. 用 os.replace 原子替换目标文件

    参数:
        path: 目标 JSON 文件路径
        data: 可被 json.dumps 序列化的数据
    """
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    with file_lock(path):
        # 临时文件放在同目录，确保同一文件系统以支持原子 rename
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent),
            prefix=".tmp_",
            suffix=".json",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
            # 原子替换（同文件系统内 rename 是原子操作）
            os.replace(tmp_path, str(path))
        except BaseException:
            # 写入失败时清理临时文件
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


def read_json(path: str | Path) -> dict:
    """读取 JSON 文件并返回字典。

    文件不存在时返回空字典。读取过程受文件锁保护。

    参数:
        path: JSON 文件路径

    返回:
        解析后的字典，文件不存在或为空时返回 {}
    """
    path = Path(path).resolve()

    if not path.exists():
        return {}

    with file_lock(path):
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return {}
        return json.loads(text)
