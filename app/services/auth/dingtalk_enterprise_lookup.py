# -*- coding: utf-8 -*-
"""Short-lived, one-time state for administrator-initiated DingTalk CorpId lookup.

The state is created only by the authenticated NiceVPN settings UI. The public
callback can only complete that exact read-only lookup; it cannot modify the
enterprise allow-list or return results to an unauthenticated polling endpoint.
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass


LOOKUP_TTL_SECONDS = 300


@dataclass(frozen=True)
class DingTalkEnterpriseLookup:
    state: str
    callback_url: str
    app_id: str
    status: str
    created_at: float
    corp_id: str = ""
    enterprise_name: str = ""
    error: str = ""


class DingTalkEnterpriseLookupStore:
    """Process-local, expiring and one-time OAuth lookup state."""

    def __init__(self, *, ttl_seconds: int = LOOKUP_TTL_SECONDS) -> None:
        self.ttl_seconds = max(30, int(ttl_seconds))
        self._lock = threading.RLock()
        self._items: dict[str, DingTalkEnterpriseLookup] = {}

    def _cleanup_locked(self, now: float) -> None:
        expired = [
            state for state, item in self._items.items()
            if now - item.created_at > self.ttl_seconds
        ]
        for state in expired:
            self._items.pop(state, None)

    def create(self, callback_url: str, app_id: str) -> str:
        if not callback_url or not app_id:
            raise ValueError("DingTalk lookup callback and app ID are required")
        state = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self._lock:
            self._cleanup_locked(now)
            self._items[state] = DingTalkEnterpriseLookup(
                state=state,
                callback_url=str(callback_url),
                app_id=str(app_id),
                status="pending",
                created_at=now,
            )
        return state

    def begin_start(self, state: str) -> DingTalkEnterpriseLookup | None:
        now = time.monotonic()
        with self._lock:
            self._cleanup_locked(now)
            item = self._items.get(state)
            if item is None or item.status != "pending":
                return None
            updated = DingTalkEnterpriseLookup(
                **{**item.__dict__, "status": "started"},
            )
            self._items[state] = updated
            return updated

    def begin_callback(self, state: str) -> DingTalkEnterpriseLookup | None:
        now = time.monotonic()
        with self._lock:
            self._cleanup_locked(now)
            item = self._items.get(state)
            if item is None or item.status != "started":
                return None
            updated = DingTalkEnterpriseLookup(
                **{**item.__dict__, "status": "processing"},
            )
            self._items[state] = updated
            return updated

    def complete(self, state: str, *, corp_id: str, enterprise_name: str = "") -> bool:
        with self._lock:
            item = self._items.get(state)
            if item is None or item.status != "processing":
                return False
            self._items[state] = DingTalkEnterpriseLookup(
                **{
                    **item.__dict__,
                    "status": "completed",
                    "corp_id": str(corp_id),
                    "enterprise_name": str(enterprise_name),
                },
            )
            return True

    def fail(self, state: str, reason: str) -> bool:
        with self._lock:
            item = self._items.get(state)
            if item is None or item.status not in {"started", "processing"}:
                return False
            self._items[state] = DingTalkEnterpriseLookup(
                **{
                    **item.__dict__,
                    "status": "failed",
                    "error": str(reason)[:160],
                },
            )
            return True

    def get(self, state: str) -> DingTalkEnterpriseLookup | None:
        now = time.monotonic()
        with self._lock:
            self._cleanup_locked(now)
            return self._items.get(state)

    def consume(self, state: str) -> DingTalkEnterpriseLookup | None:
        with self._lock:
            item = self._items.get(state)
            if item is None or item.status not in {"completed", "failed"}:
                return None
            return self._items.pop(state, None)


_STORE = DingTalkEnterpriseLookupStore()


def get_dingtalk_enterprise_lookup_store() -> DingTalkEnterpriseLookupStore:
    return _STORE
