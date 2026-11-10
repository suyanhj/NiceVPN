# -*- coding: utf-8 -*-
"""Bounded SSO authentication audit events in a dedicated, rotating JSONL file.

Never log OAuth authorization codes, tokens, application secrets or raw provider
responses. The former sso_auth_audit SQLite table is retained only to preserve
pre-release database compatibility; new events are no longer written there.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from app.core.constants import LOG_RETENTION_DAYS, SSO_AUTH_AUDIT_LOG_FILE

_EVENT_TYPES = frozenset({
    "pending", "approved", "denied", "disconnected", "timeout",
    "provider_error", "no_webauth", "policy_rejected", "session_reauth",
})

_HANDLER_LOCK = threading.RLock()
_HANDLERS: dict[Path, TimedRotatingFileHandler] = {}


def _file_handler(path: Path) -> TimedRotatingFileHandler:
    """One thread-safe rotating writer per file, even across SSO instances."""
    resolved = path.resolve()
    with _HANDLER_LOCK:
        handler = _HANDLERS.get(resolved)
        if handler is None:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            handler = TimedRotatingFileHandler(
                resolved, when="midnight", interval=1,
                backupCount=LOG_RETENTION_DAYS, encoding="utf-8", utc=True,
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            _HANDLERS[resolved] = handler
        return handler


class SsoAudit:
    def __init__(self, store=None, *, log_path: Path | None = None):
        # Accept historical store argument; SSO audit is file-only now.
        self.log_path = log_path if log_path is not None else SSO_AUTH_AUDIT_LOG_FILE

    def log(
        self, *, event_type: str, instance_id: str, common_name: str,
        outcome: str, provider_kind: str = "", enterprise_id: str = "",
        external_user_id: str = "", reason_code: str = "",
    ) -> None:
        if event_type not in _EVENT_TYPES:
            raise ValueError("Invalid SSO audit event")
        if outcome not in {"success", "failure", "pending", "cancelled"}:
            raise ValueError("Invalid audit outcome")
        fields = (
            instance_id, common_name, provider_kind, enterprise_id,
            external_user_id, reason_code,
        )
        if any(
            not isinstance(v, str) or len(v) > 255
            or any(ord(ch) < 32 for ch in v) for v in fields
        ):
            raise ValueError("Invalid audit field")
        event = {
            "event_time": datetime.now(timezone.utc).isoformat(),
            "instance_id": instance_id, "common_name": common_name,
            "provider_kind": provider_kind, "enterprise_id": enterprise_id,
            "external_user_id": external_user_id, "event_type": event_type,
            "outcome": outcome, "reason_code": reason_code,
        }
        handler = _file_handler(self.log_path)
        record = logging.LogRecord(
            name="nicevpn.sso.audit", level=logging.INFO, pathname=__file__,
            lineno=0, msg=json.dumps(event, ensure_ascii=False, sort_keys=True),
            args=(), exc_info=None,
        )
        # .handle holds the handler's lock for rotation and writes. It does not
        # propagate this high-volume audit stream into app.log or the console.
        handler.handle(record)
