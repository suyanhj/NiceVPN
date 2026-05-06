# -*- coding: utf-8 -*-
"""进程退出协调：供长耗时后台任务感知 Ctrl+C。"""
from __future__ import annotations

import threading

_shutdown_event = threading.Event()


def request_shutdown() -> None:
    """标记进程正在退出，长任务应尽快中止。"""
    _shutdown_event.set()


def is_shutdown_requested() -> bool:
    """返回是否已请求进程退出。"""
    return _shutdown_event.is_set()
