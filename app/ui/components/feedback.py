# -*- coding: utf-8 -*-
"""页面安全通知与异步重复提交保护。"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, TypeVar

from nicegui import ui


logger = logging.getLogger(__name__)
NotifyType = Literal["positive", "negative", "warning", "info", "ongoing"]
T = TypeVar("T")


def safe_notify(message: object, *, type: NotifyType = "info", **kwargs: Any) -> bool:
    """向仍有效的页面客户端发送通知；页面已关闭时安全忽略。"""

    try:
        ui.notify(str(message), type=type, **kwargs)
        return True
    except RuntimeError:
        logger.debug("页面客户端已不可用，忽略通知: %s", message)
        return False


@dataclass(slots=True)
class AsyncOperationGuard:
    """用于阻止同一页面操作重复提交的轻量状态锁。"""

    busy: bool = False

    def try_start(self) -> bool:
        if self.busy:
            return False
        self.busy = True
        return True

    def finish(self) -> None:
        self.busy = False


async def run_guarded(
    guard: AsyncOperationGuard,
    operation: Callable[[], T | Awaitable[T]],
    *,
    duplicate_message: str | None = "操作正在进行，请稍候",
    on_error: Callable[[Exception], Any] | None = None,
) -> tuple[bool, T | None]:
    """只执行一次同步/异步操作，并确保异常后恢复可操作状态。"""

    if not guard.try_start():
        if duplicate_message:
            safe_notify(duplicate_message, type="info")
        return False, None

    try:
        result = operation()
        if inspect.isawaitable(result):
            result = await result
        return True, result
    except Exception as exc:
        if on_error is not None:
            on_error(exc)
        else:
            safe_notify(f"操作失败: {exc}", type="negative")
        return True, None
    finally:
        guard.finish()
