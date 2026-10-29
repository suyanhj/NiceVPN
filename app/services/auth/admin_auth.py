# -*- coding: utf-8 -*-
"""单管理员口令哈希与凭据验证。"""

from __future__ import annotations

import secrets
import threading
import time
from collections import deque
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass

from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError


@dataclass(frozen=True, slots=True)
class LoginAttemptResult:
    """单次登录尝试的统一结果。"""

    success: bool
    blocked: bool = False


class LoginAttemptLimiter:
    """按来源限制连续失败登录，成功或窗口结束后可恢复。"""

    def __init__(
        self,
        *,
        max_failures: int = 5,
        window_seconds: float = 300,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_failures < 1 or window_seconds <= 0:
            raise ValueError("登录限流参数必须为正数")
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._clock = clock
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _active_failures(self, source: str, now: float) -> deque[float]:
        failures = self._failures.setdefault(source, deque())
        threshold = now - self.window_seconds
        while failures and failures[0] <= threshold:
            failures.popleft()
        if not failures:
            self._failures.pop(source, None)
            return deque()
        return failures

    def allows_attempt(self, source: str) -> bool:
        """当前来源未达到失败阈值时允许尝试。"""

        with self._lock:
            failures = self._active_failures(str(source), self._clock())
            return len(failures) < self.max_failures

    def record_failure(self, source: str) -> None:
        """记录一次失败。"""

        with self._lock:
            now = self._clock()
            failures = self._active_failures(str(source), now)
            if not failures:
                failures = self._failures.setdefault(str(source), deque())
            failures.append(now)

    def reset(self, source: str) -> None:
        """成功登录后清除该来源的失败记录。"""

        with self._lock:
            self._failures.pop(str(source), None)


class AdminAuthService:
    """封装 pwdlib 的 Argon2id 生成与统一凭据验证。"""

    def __init__(self, password_hash: PasswordHash | None = None) -> None:
        self._password_hash = password_hash or PasswordHash.recommended()

    def hash_password(self, password: str) -> str:
        """生成可写入配置文件的 Argon2id PHC 哈希。"""

        normalized = str(password)
        if not normalized:
            raise ValueError("管理员密码不能为空")
        if len(normalized) > 1024:
            raise ValueError("管理员密码长度不能超过 1024 个字符")
        return self._password_hash.hash(normalized)

    def verify_credentials(
        self,
        *,
        submitted_username: str,
        submitted_password: str,
        configured_username: str,
        configured_password_hash: str,
    ) -> bool:
        """统一验证用户名和密码，不暴露具体失败字段。"""

        username_matches = secrets.compare_digest(
            str(submitted_username),
            str(configured_username),
        )
        try:
            password_matches = self._password_hash.verify(
                str(submitted_password),
                str(configured_password_hash),
            )
        except (UnknownHashError, ValueError, TypeError):
            return False
        return username_matches and password_matches


class AdminLoginManager:
    """组合凭据验证、失败限流和会话建立。"""

    def __init__(
        self,
        *,
        configured_username: str,
        configured_password_hash: str,
        session_lifetime_seconds: int,
        auth_service: AdminAuthService | None = None,
        limiter: LoginAttemptLimiter | None = None,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.configured_username = configured_username
        self.configured_password_hash = configured_password_hash
        self.session_lifetime_seconds = session_lifetime_seconds
        self.auth_service = auth_service or AdminAuthService()
        self.limiter = limiter or LoginAttemptLimiter()
        self._wall_clock = wall_clock

    def attempt_login(
        self,
        session: MutableMapping,
        *,
        submitted_username: str,
        submitted_password: str,
        source: str,
    ) -> LoginAttemptResult:
        """执行统一登录判断，并仅在成功时写入最小会话状态。"""

        if not self.limiter.allows_attempt(source):
            return LoginAttemptResult(success=False, blocked=True)

        if not self.auth_service.verify_credentials(
            submitted_username=submitted_username,
            submitted_password=submitted_password,
            configured_username=self.configured_username,
            configured_password_hash=self.configured_password_hash,
        ):
            self.limiter.record_failure(source)
            return LoginAttemptResult(success=False)

        self.limiter.reset(source)
        session.clear()
        session.update(
            authenticated=True,
            username=self.configured_username,
            expires_at=self._wall_clock() + self.session_lifetime_seconds,
        )
        return LoginAttemptResult(success=True)
