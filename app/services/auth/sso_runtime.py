# -*- coding: utf-8 -*-
"""SSO Management controller for OpenVPN's deferred WebAuth authentication.

An instance's socket reader is the ONLY component that may authorize that
instance's CID/KID. OAuth HTTP callbacks cannot select arbitrary connection
IDs. The controller never silently approves unverified pending sessions.
"""
from __future__ import annotations

import hashlib
import logging
import os
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from app.core.constants import OPENVPN_MGMT_DIR
from app.services.auth.enterprise_sso_policy import SsoPolicyService
from app.services.auth.sso_audit import SsoAudit
from app.services.auth.office_oauth import EnterpriseIdentity
from app.services.auth.openvpn_webauth import (
    ClientNotification, ManagementNotificationParser, auth_allow, auth_deny,
    pending_webauth,
)
from app.services.auth.provider_store import SsoProviderStore
from app.services.auth.sso_grants import write_sso_grant
from app.services.auth.sso_transactions import (
    AuthRequest, WebAuthTransactionStore,
)

logger = logging.getLogger(__name__)


def management_socket_path(instance_id: str) -> Path:
    # Short, safe and deterministic across multiple VPN instances.
    suffix = hashlib.sha256(instance_id.encode("utf-8")).hexdigest()[:20]
    return OPENVPN_MGMT_DIR / f"sso-{suffix}.sock"


@dataclass(frozen=True)
class PendingConnection:
    common_name: str
    provider_id: str
    state_digest: str
    expires_at: int
    peer_ip: str = ""
    peer_port: str = ""


class ManagementInstance:
    def __init__(self, instance_id: str, socket_path: Path, runtime: "SsoRuntime"):
        self.instance_id = instance_id
        self.socket_path = socket_path
        self.runtime = runtime
        self.socket: socket.socket | None = None
        self.socket_lock = threading.RLock()
        self.thread: threading.Thread | None = None

    def send(self, command: str) -> None:
        with self.socket_lock:
            if self.socket is None:
                raise ConnectionError("OpenVPN Management channel is disconnected")
            self.socket.sendall(command.encode("utf-8"))

    def start(self) -> None:
        if self.thread is not None:
            raise RuntimeError("ManagementInstance already started")
        self.thread = threading.Thread(
            target=self._run,
            name=f"nicevpn-sso-{self.instance_id}",
            daemon=True,
        )
        self.thread.start()

    def close(self) -> None:
        with self.socket_lock:
            if self.socket is not None:
                try:
                    self.socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def join(self) -> None:
        if self.thread is not None:
            self.thread.join(timeout=5)

    def _run(self) -> None:
        while not self.runtime.stop_event.is_set():
            conn: socket.socket | None = None
            try:
                conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                conn.settimeout(2)
                conn.connect(str(self.socket_path))
                conn.settimeout(0.5)
                with self.socket_lock:
                    self.socket = conn
                self._receive(conn)
            except OSError as exc:
                if not self.runtime.stop_event.is_set():
                    logger.warning("SSO Management unavailable for %s: %s", self.instance_id, type(exc).__name__)
            finally:
                with self.socket_lock:
                    if self.socket is conn:
                        self.socket = None
                if conn is not None:
                    conn.close()
                self.runtime.clear_instance(self.instance_id)
            self.runtime.stop_event.wait(1.0)

    def _receive(self, conn: socket.socket) -> None:
        parser = ManagementNotificationParser()
        buffer = b""
        while not self.runtime.stop_event.is_set():
            try:
                chunk = conn.recv(16384)
            except socket.timeout:
                continue
            if not chunk:
                return
            buffer += chunk
            if len(buffer) > 2 * 1024 * 1024:
                raise ConnectionError("Management frame too large")
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                event = parser.feed(line.decode("utf-8", errors="replace"))
                if event is not None:
                    try:
                        self.runtime.on_event(self, event)
                    except Exception:
                        logger.exception("SSO Management event processing failed")
                        if event.kind in ("CONNECT", "REAUTH") and event.kid is not None:
                            try:
                                self.send(auth_deny(event.cid, event.kid, "SSO unavailable"))
                            except OSError:
                                return


class SsoRuntime:
    def __init__(
        self, *,
        instances: list[str],
        public_base_url: str,
        policy: SsoPolicyService | None = None,
        providers: SsoProviderStore | None = None,
        transactions: WebAuthTransactionStore | None = None,
    ):
        parsed = urlsplit(public_base_url.rstrip("/"))
        try:
            valid_origin = (
                parsed.scheme in {"http", "https"} and parsed.hostname
                and parsed.username is None and parsed.password is None
                and not parsed.path and not parsed.query and not parsed.fragment
                and not any(ch.isspace() or ord(ch) < 32 for ch in public_base_url)
                and (parsed.port is None or 1 <= parsed.port <= 65535)
            )
        except ValueError:
            valid_origin = False
        if not valid_origin:
            raise ValueError("SSO public base URL must be a valid HTTP or HTTPS origin")
        self.public_base_url = public_base_url.rstrip("/")
        self.policy = policy if policy is not None else SsoPolicyService()
        self.providers = providers if providers is not None else SsoProviderStore()
        self.transactions = transactions if transactions is not None else WebAuthTransactionStore()
        self.audit = SsoAudit(self.transactions.store)
        self.instances = {
            inst: ManagementInstance(inst, management_socket_path(inst), self)
            for inst in instances
        }
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.pending: dict[tuple[str, int, int], PendingConnection] = {}
        self.sessions: dict[tuple[str, int], tuple[str, int]] = {}

    def start(self) -> None:
        if os.name != "posix" or not hasattr(socket, "AF_UNIX"):
            raise RuntimeError("SSO Management currently requires Linux Unix sockets")
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            raise RuntimeError("SSO Management requires root to access restricted Unix sockets")
        if not self.instances:
            raise RuntimeError("No VPN instances configured for SSO")
        if self.providers.first_enabled() is None:
            raise RuntimeError("SSO is enabled but no working OAuth provider is enabled")
        for instance in self.instances.values():
            instance.start()

    def close(self) -> None:
        self.stop_event.set()
        for manager in self.instances.values():
            manager.close()
        for manager in self.instances.values():
            manager.join()
        with self.lock:
            self.pending.clear()
            self.sessions.clear()

    def kill_client_by_common_name(self, common_name: str) -> bool:
        """Keep existing admin kick-offline compatible with Unix Management."""
        if not common_name or any(ch in common_name for ch in "\r\n"):
            return False
        success = False
        for manager in self.instances.values():
            try:
                manager.send(f"kill {common_name}\n")
                success = True
            except (OSError, ConnectionError):
                continue
        return success

    def clear_instance(self, instance_id: str) -> None:
        with self.lock:
            pending_keys = [key for key in self.pending if key[0] == instance_id]
            for key in pending_keys:
                self.pending.pop(key, None)
                self.transactions.cancel_connection(*key)
            for key in list(self.sessions):
                if key[0] == instance_id:
                    self.sessions.pop(key, None)

    @staticmethod
    def _state_digest(state: str) -> str:
        return hashlib.sha256(state.encode()).hexdigest()

    def on_event(self, manager: ManagementInstance, event: ClientNotification) -> None:
        inst = manager.instance_id
        if event.kind == "DISCONNECT":
            with self.lock:
                self.sessions.pop((inst, event.cid), None)
                for key in list(self.pending):
                    if key[0] == inst and key[1] == event.cid:
                        self.pending.pop(key, None)
                        self.transactions.cancel_connection(*key)
            return
        if event.kind not in ("CONNECT", "REAUTH") or event.kid is None:
            return
        key = (inst, event.cid, event.kid)
        decision = self.policy.resolve(event.common_name, enabled=True)
        if decision.denied:
            self.audit.log(event_type="policy_rejected", instance_id=inst,
                           common_name=event.common_name, outcome="failure",
                           reason_code=decision.reason)
            manager.send(auth_deny(event.cid, event.kid, "SSO required"))
            return
        if not decision.requires_sso:
            manager.send(auth_allow(event.cid, event.kid))
            return
        # REAUTH is allowed on the exact live client session for 8 hours. On
        # disconnect or Management reconnect, old sessions are discarded.
        with self.lock:
            old = self.sessions.get((inst, event.cid))
        if event.kind == "REAUTH" and old is not None:
            session_cn, session_expiry = old
            if session_cn == event.common_name and session_expiry > int(time.time()):
                manager.send(auth_allow(event.cid, event.kid))
                return
        if not event.supports_webauth:
            self.audit.log(event_type="no_webauth", instance_id=inst,
                           common_name=event.common_name, outcome="failure",
                           reason_code="missing_webauth")
            manager.send(auth_deny(event.cid, event.kid, "Unsupported SSO client"))
            return
        provider = self.providers.first_enabled()
        if provider is None:
            manager.send(auth_deny(event.cid, event.kid, "SSO unavailable"))
            return
        created = self.transactions.create(
            instance_id=inst,
            cid=event.cid,
            kid=event.kid,
            common_name=event.common_name,
            provider_id=provider.id,
            ttl_seconds=180,
        )
        with self.lock:
            self.pending[key] = PendingConnection(
                event.common_name, provider.id,
                self._state_digest(created.state), created.request.expires_at,
                event.env.get("trusted_ip") or event.env.get("trusted_ip6")
                or event.env.get("untrusted_ip") or event.env.get("untrusted_ip6") or "",
                event.env.get("trusted_port") or event.env.get("untrusted_port") or "",
            )
        url = self.public_base_url + "/auth/sso/start?" + urlencode({"t": created.token})
        manager.send(pending_webauth(event.cid, event.kid, url))
        self.audit.log(event_type="pending", instance_id=inst,
                       common_name=event.common_name, outcome="pending",
                       provider_kind=provider.kind,
                       reason_code="awaiting_provider")
        logger.info("SSO pending: instance=%s CID=%s KID=%s", inst, event.cid, event.kid)

    def select_provider(self, *, token: str, request: AuthRequest, provider_id: str) -> bool:
        """Allow the user to choose an ENABLED trusted enterprise provider.

        The browser chooses only a provider ID from the administrator's list,
        never a CorpId, identity, OpenVPN CID, or authorization decision.
        """
        try:
            provider = self.providers.get(provider_id)
        except Exception:
            return False
        if provider is None:
            return False
        key = (request.instance_id, request.cid, request.kid)
        state_digest = self._state_digest(
            WebAuthTransactionStore.state_for_token(token)
        )
        with self.lock:
            current = self.pending.get(key)
            if (current is None or current.common_name != request.common_name
                    or current.expires_at <= int(time.time())
                    or current.state_digest != state_digest):
                return False
            with self.transactions.store.connection(write=True) as conn:
                result = conn.execute(
                    """
                    UPDATE sso_login_transactions SET provider_id=?
                    WHERE token_hash=? AND status='pending' AND expires_at>?
                      AND instance_id=? AND client_id=? AND key_id=?
                    """,
                    (provider_id, hashlib.sha256(token.encode()).hexdigest(),
                     int(time.time()), request.instance_id, request.cid, request.kid),
                )
                if result.rowcount != 1:
                    return False
            self.pending[key] = PendingConnection(
                current.common_name, provider_id,
                current.state_digest, current.expires_at,
                current.peer_ip, current.peer_port,
            )
            return True

    def finish(
        self, request: AuthRequest, state: str,
        identity: EnterpriseIdentity | None,
    ) -> bool:
        """One active OAuth callback may unblock the exact waiting connection."""
        key = (request.instance_id, request.cid, request.kid)
        with self.lock:
            pending = self.pending.get(key)
            if (pending is None or pending.common_name != request.common_name
                    or pending.provider_id != request.provider_id
                    or pending.state_digest != self._state_digest(state)
                    or pending.expires_at <= int(time.time())):
                return False
            if identity is not None:
                provider = self.providers.get(request.provider_id)
                if (provider is None or identity.provider_kind != provider.kind
                        or identity.enterprise_id != provider.enterprise_id):
                    identity = None
            consumed = self.transactions.consume_state(state, approved=identity is not None)
            if consumed is None:
                return False
            self.pending.pop(key, None)
            manager = self.instances.get(request.instance_id)
            if manager is None:
                return False
            try:
                if identity is None:
                    self.audit.log(event_type="denied", instance_id=request.instance_id,
                                   common_name=request.common_name, outcome="failure",
                                   reason_code="office_identity_rejected")
                    manager.send(auth_deny(request.cid, request.kid))
                    return False
                # Re-evaluate policy at authorization time: a revoked group or
                # certificate may never be approved by a stale browser session.
                policy = self.policy.resolve(request.common_name, enabled=True)
                if policy.denied:
                    manager.send(auth_deny(request.cid, request.kid))
                    return False
                # OpenVPN runs client-connect *after* management auth.
                # Grant post-auth device record-only mode before approval.
                write_sso_grant(
                    request.instance_id, request.common_name,
                    peer_ip=pending.peer_ip, peer_port=pending.peer_port,
                )
                manager.send(auth_allow(request.cid, request.kid))
                self.audit.log(
                    event_type="approved", instance_id=request.instance_id,
                    common_name=request.common_name, outcome="success",
                    provider_kind=identity.provider_kind,
                    enterprise_id=identity.enterprise_id,
                    external_user_id=identity.external_user_id,
                    reason_code="enterprise_match",
                )
                self.sessions[(request.instance_id, request.cid)] = (
                    request.common_name, int(time.time()) + 8 * 3600
                )
                return True
            except OSError:
                return False


_RUNTIME: SsoRuntime | None = None
_RUNTIME_LOCK = threading.RLock()


def get_sso_runtime() -> SsoRuntime | None:
    with _RUNTIME_LOCK:
        return _RUNTIME


def set_sso_runtime(runtime: SsoRuntime | None) -> None:
    global _RUNTIME
    with _RUNTIME_LOCK:
        _RUNTIME = runtime
