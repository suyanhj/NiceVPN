# -*- coding: utf-8 -*-
"""OpenVPN Management WebAuth protocol primitives.

Pure protocol code.  It does not enable Management authentication or approve a
connection; callers must explicitly resolve the server-side certificate policy
and verify the external identity before issuing the final command.

See: https://openvpn.net/community-docs/management-interface/
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

_HEADER = re.compile(r"^>CLIENT:(CONNECT|REAUTH|ESTABLISHED|DISCONNECT),(\d+)(?:,(\d+))?$")
_ENV_PREFIX = ">CLIENT:ENV,"
_ID = re.compile(r"^[0-9]{1,12}$")


@dataclass(frozen=True)
class ClientNotification:
    kind: str
    cid: int
    kid: int | None = None
    env: dict[str, str] = field(default_factory=dict)

    @property
    def common_name(self) -> str:
        # This originates in the OpenVPN Management event, never an HTTP param.
        return self.env.get("common_name", "")

    @property
    def supports_webauth(self) -> bool:
        return "webauth" in {
            item.strip().lower() for item in self.env.get("IV_SSO", "").split(",")
        }


class ManagementNotificationParser:
    """Read atomic >CLIENT events; ignore unrelated Management response lines.

    CONNECT/REAUTH always carry a KID; DISCONNECT/ESTABLISHED do not.
    Never log the event env verbatim (could contain credentials).
    """

    def __init__(self) -> None:
        self._kind: str | None = None
        self._cid: int | None = None
        self._kid: int | None = None
        self._env: dict[str, str] = {}

    def feed(self, line: str) -> ClientNotification | None:
        line = line.rstrip("\r\n")
        match = _HEADER.fullmatch(line)
        if match:
            kind, cid, kid = match.groups()
            self._kind = kind
            self._cid = int(cid)
            self._kid = int(kid) if kid is not None else None
            self._env = {}
            return None
        if not line.startswith(_ENV_PREFIX) or self._kind is None:
            return None
        payload = line[len(_ENV_PREFIX):]
        if payload == "END":
            result = ClientNotification(
                kind=self._kind,
                cid=self._cid if self._cid is not None else -1,
                kid=self._kid,
                env=dict(self._env),
            )
            self._kind = None
            self._cid = self._kid = None
            self._env = {}
            return result
        if "=" in payload:
            key, value = payload.split("=", 1)
            if key and len(key) < 128 and len(value) < 8192:
                self._env[key] = value
        return None


def _validate_ids(cid: int, kid: int) -> None:
    if not _ID.fullmatch(str(cid)) or not _ID.fullmatch(str(kid)):
        raise ValueError("OpenVPN CID/KID must be nonnegative decimal integers")


def auth_allow(cid: int, kid: int) -> str:
    _validate_ids(cid, kid)
    return f"client-auth-nt {cid} {kid}\n"


def auth_deny(cid: int, kid: int, reason: str = "SSO authentication failed") -> str:
    _validate_ids(cid, kid)
    # Do not permit user-controlled text or secrets in management commands.
    allowed = {"SSO authentication failed", "SSO required", "SSO timed out",
               "SSO unavailable", "Unsupported SSO client"}
    if reason not in allowed:
        raise ValueError("Unrecognized denial reason")
    return f'client-deny {cid} {kid} "{reason}"\n'


def pending_webauth(
    cid: int, kid: int, url: str, *, timeout_seconds: int = 180
) -> str:
    """Build client-pending-auth for an HTTP or HTTPS URL.

    Caller must create an unguessable, one-time transaction and ensure the
    endpoint cannot directly approve a connection without OAuth.
    """
    _validate_ids(cid, kid)
    parsed = urlsplit(url)
    try:
        valid_url = (
            parsed.scheme in {"http", "https"} and parsed.hostname
            and parsed.username is None and parsed.password is None
            and not parsed.fragment
            and not any(ch.isspace() or ord(ch) < 32 for ch in url)
            and (parsed.port is None or 1 <= parsed.port <= 65535)
        )
    except ValueError:
        valid_url = False
    if not valid_url:
        raise ValueError("WebAuth requires an absolute HTTP or HTTPS URL without credentials")
    if not 30 <= timeout_seconds <= 600:
        raise ValueError("Invalid WebAuth timeout")
    return f"client-pending-auth {cid} {kid} WEB_AUTH:external:{url} {timeout_seconds}\n"
