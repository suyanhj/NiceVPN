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
import random
import socket
import threading
import time
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from app.core.constants import OPENVPN_MGMT_DIR
from app.services.auth.enterprise_sso_policy import SsoPolicyService
from app.services.auth.sso_audit import SsoAudit
from app.services.auth.office_oauth import EnterpriseIdentity
from app.services.auth.openvpn_webauth import (
    ClientNotification, ManagementNotificationParser, ManagementStatusParser,
    auth_allow, auth_deny,
    pending_webauth,
)
from app.services.auth.provider_store import SsoProviderStore, UNIFIED_DINGTALK_ID
from app.core.config import load_config
from app.utils.sso_enterprises import is_allowed_enterprise
from app.services.auth.sso_grants import write_sso_grant
from app.services.auth.sso_sessions import (
    SSO_SESSION_TTL_SECONDS, OnlineSsoBinding, SsoSessionStore,
    verified_peer_fingerprint,
)
from app.services.auth.sso_transactions import (
    AuthRequest, WebAuthTransactionStore,
)

logger = logging.getLogger(__name__)

# One complete Management status request per VPN instance, not per client.
# Keep high-frequency status reads separate from low-frequency SQLite writes.
STATUS_POLL_MIN_SECONDS = 1.0
STATUS_POLL_MAX_SECONDS = 5.0
STATUS_RESPONSE_TIMEOUT_SECONDS = 10.0
# Refresh a little before the one-hour expiry; exactly 3600 seconds would
# race a delayed Management status response and cause unwanted re-login.
ONLINE_LEASE_WRITE_SECONDS = 55 * 60.0
EXPIRED_SESSION_PRUNE_SECONDS = 1800.0


class SsoFinishResult(str, Enum):
    """Keep an explicit denial distinct from an expired/replayed WebAuth state."""
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    UNAVAILABLE = "unavailable"


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
    cert_fingerprint: str = ""
    started_at: float = 0.0


@dataclass(frozen=True)
class ActiveSsoSession:
    common_name: str
    provider_id: str
    enterprise_id: str
    cert_fingerprint: str
    expires_at: int
    provider_app_id: str = ""
    connected_at: int = 0
    enterprise_name: str = ""


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
        status_parser = ManagementStatusParser()
        next_status_poll = time.monotonic() + random.uniform(
            STATUS_POLL_MIN_SECONDS, STATUS_POLL_MAX_SECONDS,
        )
        buffer = b""
        while not self.runtime.stop_event.is_set():
            now = time.monotonic()
            if (status_parser.pending
                    and now - status_parser.started_at > STATUS_RESPONSE_TIMEOUT_SECONDS):
                # A late response cannot be safely associated with a later
                # poll. Reconnect the management socket instead of reusing it.
                raise ConnectionError("SSO Management status request timed out")
            if not status_parser.pending and now >= next_status_poll:
                status_parser.begin(now)
                self.send("status 3\n")
                next_status_poll = float("inf")
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
                decoded = line.decode("utf-8", errors="replace").rstrip("\r")
                snapshot = status_parser.feed(decoded)
                if snapshot is not None:
                    next_status_poll = time.monotonic() + random.uniform(
                        STATUS_POLL_MIN_SECONDS, STATUS_POLL_MAX_SECONDS,
                    )
                    try:
                        self.runtime.on_status(self, snapshot)
                    except Exception:
                        logger.exception("SSO Management online status processing failed")
                elif not status_parser.pending and next_status_poll == float("inf"):
                    # Invalid/unsupported status result: retry with jitter.
                    next_status_poll = time.monotonic() + random.uniform(
                        STATUS_POLL_MIN_SECONDS, STATUS_POLL_MAX_SECONDS,
                    )
                event = parser.feed(decoded)
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
        self.session_cache = SsoSessionStore(self.transactions.store)
        self.instances = {
            inst: ManagementInstance(inst, management_socket_path(inst), self)
            for inst in instances
        }
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.pending: dict[tuple[str, int, int], PendingConnection] = {}
        self.sessions: dict[tuple[str, int], ActiveSsoSession] = {}
        self.established: set[tuple[str, int]] = set()
        self.online_written_at: dict[tuple[str, int], float] = {}
        self.recovery_checked: dict[tuple[str, int], tuple[str, int]] = {}
        self.last_pruned_at = 0.0
        self.status_provider_checked_at = 0.0
        self.status_provider = None
        self.establish_timings: dict[tuple[str, int], tuple[float, str]] = {}

    def start(self) -> None:
        if os.name != "posix" or not hasattr(socket, "AF_UNIX"):
            raise RuntimeError("SSO Management currently requires Linux Unix sockets")
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            raise RuntimeError("SSO Management requires root to access restricted Unix sockets")
        if not self.instances:
            raise RuntimeError("No VPN instances configured for SSO")
        provider = self.providers.first_enabled()
        if provider is None:
            raise RuntimeError("SSO is enabled but no working OAuth provider is enabled")
        if (provider.id == UNIFIED_DINGTALK_ID
                and not load_config().get("enterprise_sso_allowed_corps")):
            raise RuntimeError("Unified DingTalk SSO requires at least one allowed CorpId")
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
            self.established.clear()
            self.online_written_at.clear()
            self.recovery_checked.clear()
            self.status_provider = None
            self.establish_timings.clear()

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
                    self.established.discard(key)
                    self.online_written_at.pop(key, None)
            for key in list(self.recovery_checked):
                if key[0] == instance_id:
                    self.recovery_checked.pop(key, None)
            for key in list(self.establish_timings):
                if key[0] == instance_id:
                    self.establish_timings.pop(key, None)

    @staticmethod
    def _state_digest(state: str) -> str:
        return hashlib.sha256(state.encode()).hexdigest()

    def on_status(
        self, manager: ManagementInstance,
        clients: dict[int, tuple[str, int]],
    ) -> None:
        """Renew existing rows only for current OpenVPN-confirmed connections.

        During a NiceVPN-only restart, restore the identity mapping using
        BOTH the stored CID and OpenVPN's server-side connection start time.
        Merely seeing the same CN in status must never create an SSO grant.
        """
        inst = manager.instance_id
        now = time.monotonic()
        if now - self.last_pruned_at >= EXPIRED_SESSION_PRUNE_SECONDS:
            self.session_cache.prune_expired()
            self.last_pruned_at = now
        if now - self.status_provider_checked_at >= 30 or self.status_provider_checked_at == 0:
            self.status_provider = self.providers.first_enabled()
            self.status_provider_checked_at = now
        provider = self.status_provider
        if provider is None:
            return
        bindings: list[OnlineSsoBinding] = []
        sso_config = load_config()
        with self.lock:
            # Do not re-query SQLite for the same unknown/non-SSO client every
            # 1–5 seconds; retry only for a different actual VPN connection.
            for key, identity in list(self.recovery_checked.items()):
                if key[0] == inst and clients.get(key[1]) != identity:
                    self.recovery_checked.pop(key, None)

        for cid, (common_name, connected_at) in clients.items():
            key = (inst, cid)
            with self.lock:
                active = self.sessions.get(key)
                established = key in self.established
                last_write = self.online_written_at.get(key)
            if not established and active is None:
                with self.lock:
                    if self.recovery_checked.get(key) == (common_name, connected_at):
                        continue
                    self.recovery_checked[key] = (common_name, connected_at)
                # A Management reconnection does not re-emit ESTABLISHED.
                # Recover only a prior exact trusted CID+timestamp binding.
                stored = self.session_cache.recover_online(
                    instance_id=inst, common_name=common_name,
                    client_id=cid, connected_at=connected_at,
                    provider_id=provider.id, provider_app_id=provider.app_id,
                )
                if stored is None:
                    continue
                active = ActiveSsoSession(
                    stored.common_name, stored.provider_id, stored.enterprise_id,
                    stored.cert_fingerprint, stored.expires_at,
                    stored.provider_app_id, connected_at, stored.enterprise_name,
                )
                with self.lock:
                    self.sessions[key] = active
                    self.established.add(key)
                established = True
            if (not established or active is None or not active.cert_fingerprint
                    or active.common_name != common_name
                    or (active.connected_at and active.connected_at != connected_at)
                    or active.provider_id != provider.id
                    or active.provider_app_id != provider.app_id):
                continue
            if last_write is not None and now - last_write < ONLINE_LEASE_WRITE_SECONDS:
                continue
            if provider.id == UNIFIED_DINGTALK_ID:
                if not is_allowed_enterprise(
                    sso_config, active.enterprise_id, active.enterprise_name,
                ):
                    continue
            elif active.enterprise_id != provider.enterprise_id:
                continue
            policy = self.policy.resolve(
                common_name, enabled=True,
                all_users=bool(sso_config.get("enterprise_sso_all_users", False)),
            )
            if policy.denied or not policy.requires_sso:
                continue
            bindings.append(OnlineSsoBinding(
                common_name, active.cert_fingerprint, provider.id,
                provider.app_id, active.enterprise_id, cid, connected_at,
                active.enterprise_name,
            ))

        if bindings:
            refreshed = self.session_cache.refresh_online(
                instance_id=inst, bindings=bindings,
            )
            with self.lock:
                for binding in bindings:
                    if (binding.common_name, binding.cert_fingerprint) not in refreshed:
                        continue  # An administrator may have revoked the row.
                    key = (inst, binding.client_id)
                    current = self.sessions.get(key)
                    if current is not None and current.cert_fingerprint == binding.cert_fingerprint:
                        self.sessions[key] = replace(
                            current, expires_at=int(time.time()) + SSO_SESSION_TTL_SECONDS,
                            connected_at=binding.connected_at,
                        )
                        self.online_written_at[key] = now

    def on_event(self, manager: ManagementInstance, event: ClientNotification) -> None:
        inst = manager.instance_id
        if event.kind == "DISCONNECT":
            self.session_cache.disconnected(instance_id=inst, client_id=event.cid)
            with self.lock:
                self.sessions.pop((inst, event.cid), None)
                self.established.discard((inst, event.cid))
                self.online_written_at.pop((inst, event.cid), None)
                self.recovery_checked.pop((inst, event.cid), None)
                self.establish_timings.pop((inst, event.cid), None)
                for key in list(self.pending):
                    if key[0] == inst and key[1] == event.cid:
                        self.pending.pop(key, None)
                        self.transactions.cancel_connection(*key)
            return
        if event.kind == "ESTABLISHED":
            with self.lock:
                if (inst, event.cid) in self.sessions:
                    self.established.add((inst, event.cid))
                measured = self.establish_timings.pop((inst, event.cid), None)
            if measured is not None:
                started, source = measured
                logger.info(
                    "SSO OpenVPN establishment: instance=%s CID=%s mode=%s "
                    "auth_allow_to_established_ms=%d",
                    inst, event.cid, source, round((time.monotonic() - started) * 1000),
                )
            return
        if event.kind not in ("CONNECT", "REAUTH") or event.kid is None:
            return
        key = (inst, event.cid, event.kid)
        decision = self.policy.resolve(
            event.common_name, enabled=True,
            all_users=bool(load_config().get("enterprise_sso_all_users", False)),
        )
        if decision.denied:
            self.audit.log(event_type="policy_rejected", instance_id=inst,
                           common_name=event.common_name, outcome="failure",
                           reason_code=decision.reason)
            manager.send(auth_deny(event.cid, event.kid, "SSO required"))
            return
        if not decision.requires_sso:
            manager.send(auth_allow(event.cid, event.kid))
            return
        # CONNECT is always sent after OpenVPN's native certificate validation.
        # We only reuse a previously verified OAuth identity; no CA/CRL or
        # certificate signature checks happen in NiceVPN.
        fingerprint = verified_peer_fingerprint(event.env)
        provider = self.providers.first_enabled()
        if provider is None:
            manager.send(auth_deny(event.cid, event.kid, "SSO unavailable"))
            return

        def corp_allowed(corp_id: str, enterprise_name: str = "") -> bool:
            if provider.id == UNIFIED_DINGTALK_ID:
                return is_allowed_enterprise(load_config(), corp_id, enterprise_name)
            return corp_id == provider.enterprise_id

        with self.lock:
            active = self.sessions.get((inst, event.cid))

        if event.kind == "REAUTH" and active is not None:
            fingerprint_unchanged = (
                not fingerprint or not active.cert_fingerprint
                or fingerprint == active.cert_fingerprint
            )
            valid = (
                active.common_name == event.common_name
                and active.provider_id == provider.id
                and (active.cert_fingerprint or active.expires_at > int(time.time()))
                and active.provider_app_id == provider.app_id
                and fingerprint_unchanged
                and corp_allowed(active.enterprise_id, active.enterprise_name)
            )
            if valid and active.cert_fingerprint:
                # Respect administrator revocation even for a live TLS REAUTH.
                stored = self.session_cache.get(
                    instance_id=inst, common_name=event.common_name,
                    cert_fingerprint=active.cert_fingerprint,
                    provider_id=provider.id, provider_app_id=provider.app_id,
                )
                valid = (
                    stored is not None
                    and stored.enterprise_id == active.enterprise_id
                    and stored.enterprise_name == active.enterprise_name
                )
            if valid:
                manager.send(auth_allow(event.cid, event.kid))
                logger.info(
                    "SSO live-session REAUTH: instance=%s CID=%s KID=%s",
                    inst, event.cid, event.kid,
                )
                return

        # A client missing WEB_AUTH cannot silently bypass a required policy,
        # even if it presents the same certificate that was used earlier.
        if not event.supports_webauth:
            self.audit.log(event_type="no_webauth", instance_id=inst,
                           common_name=event.common_name, outcome="failure",
                           reason_code="missing_webauth")
            manager.send(auth_deny(event.cid, event.kid, "Unsupported SSO client"))
            return

        if fingerprint:
            stored = self.session_cache.get(
                instance_id=inst, common_name=event.common_name,
                cert_fingerprint=fingerprint, provider_id=provider.id,
                provider_app_id=provider.app_id,
            )
            if stored is not None and corp_allowed(
                stored.enterprise_id, stored.enterprise_name,
            ):
                peer_ip = (event.env.get("trusted_ip") or event.env.get("trusted_ip6")
                           or event.env.get("untrusted_ip")
                           or event.env.get("untrusted_ip6") or "")
                peer_port = (event.env.get("trusted_port")
                             or event.env.get("untrusted_port") or "")
                if event.kind == "CONNECT":
                    # client-connect still runs on new connections. Re-issue a
                    # short-lived bypass for the existing device-binding hook.
                    write_sso_grant(
                        inst, event.common_name, peer_ip=peer_ip,
                        peer_port=peer_port,
                    )
                manager.send(auth_allow(event.cid, event.kid))
                with self.lock:
                    self.sessions[(inst, event.cid)] = ActiveSsoSession(
                        event.common_name, provider.id, stored.enterprise_id,
                        fingerprint, stored.expires_at, provider.app_id,
                        enterprise_name=stored.enterprise_name,
                    )
                    if event.kind == "CONNECT":
                        self.establish_timings[(inst, event.cid)] = (
                            time.monotonic(), "cached",
                        )
                self.audit.log(
                    event_type="session_reauth", instance_id=inst,
                    common_name=event.common_name, outcome="success",
                    provider_kind=provider.kind, enterprise_id=stored.enterprise_id,
                    reason_code="cached_connect" if event.kind == "CONNECT"
                    else "cached_tls_reauth",
                )
                logger.info(
                    "SSO persisted session reused: instance=%s CID=%s KID=%s kind=%s",
                    inst, event.cid, event.kid, event.kind,
                )
                return

        # No verified fingerprint or no eligible cache: perform normal OAuth.
        # A missing fingerprint never falls back to CN-only cache reuse.
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
                fingerprint, time.monotonic(),
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
        if provider is None or provider_id not in {
            row["id"] for row in self.providers.enabled_metadata()
        }:
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
                current.cert_fingerprint, current.started_at,
            )
            return True

    def finish(
        self, request: AuthRequest, state: str,
        identity: EnterpriseIdentity | None,
    ) -> bool:
        """Compatibility boolean API for Management/older callers."""
        return self.finish_with_result(request, state, identity) == SsoFinishResult.APPROVED

    def finish_with_result(
        self, request: AuthRequest, state: str,
        identity: EnterpriseIdentity | None,
    ) -> SsoFinishResult:
        """Differentiate a consumed but *denied* login from a stale OAuth state.

        Only a live instance's trusted Management channel sends allow/deny.
        HTTP callbacks must not choose a connection or change the identity.
        """
        key = (request.instance_id, request.cid, request.kid)
        with self.lock:
            pending = self.pending.get(key)
            if (pending is None or pending.common_name != request.common_name
                    or pending.provider_id != request.provider_id
                    or pending.state_digest != self._state_digest(state)
                    or pending.expires_at <= int(time.time())):
                return SsoFinishResult.EXPIRED
            deny_reason = "office_identity_rejected"
            if identity is not None:
                provider = self.providers.get(request.provider_id)
                if provider is None or identity.provider_kind != provider.kind:
                    identity = None
                elif provider.id == UNIFIED_DINGTALK_ID:
                    if not is_allowed_enterprise(
                        load_config(), identity.enterprise_id, identity.enterprise_name,
                    ):
                        logger.warning(
                            "SSO unified DingTalk rejected: corp_id=%s not in allow-list",
                            identity.enterprise_id,
                        )
                        deny_reason = "enterprise_not_allowed"
                        identity = None
                elif identity.enterprise_id != provider.enterprise_id:
                    deny_reason = "enterprise_not_allowed"
                    identity = None
            consumed = self.transactions.consume_state(state, approved=identity is not None)
            if consumed is None:
                return SsoFinishResult.EXPIRED
            self.pending.pop(key, None)
            manager = self.instances.get(request.instance_id)
            if manager is None:
                return SsoFinishResult.UNAVAILABLE
            try:
                if identity is None:
                    self.audit.log(event_type="denied", instance_id=request.instance_id,
                                   common_name=request.common_name, outcome="failure",
                                   reason_code=deny_reason)
                    manager.send(auth_deny(request.cid, request.kid))
                    return SsoFinishResult.DENIED
                # Re-evaluate only the existing SSO opt-in status. OpenVPN owns
                # certificate, CRL, device, CCD, and network-access enforcement.
                policy = self.policy.resolve(
                    request.common_name, enabled=True,
                    all_users=bool(load_config().get("enterprise_sso_all_users", False)),
                )
                if policy.denied:
                    self.audit.log(event_type="policy_rejected", instance_id=request.instance_id,
                                   common_name=request.common_name, outcome="failure",
                                   reason_code=policy.reason)
                    manager.send(auth_deny(request.cid, request.kid))
                    return SsoFinishResult.DENIED
                # This short-lived grant only bypasses the post-SSO fingerprint
                # check; OpenVPN continues its normal client-connect processing.
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
                expires_at = int(time.time()) + SSO_SESSION_TTL_SECONDS
                with self.lock:
                    self.sessions[(request.instance_id, request.cid)] = ActiveSsoSession(
                        request.common_name, provider.id, identity.enterprise_id,
                        pending.cert_fingerprint, expires_at, provider.app_id,
                        enterprise_name=identity.enterprise_name,
                    )
                    self.establish_timings[(request.instance_id, request.cid)] = (
                        time.monotonic(), "oauth",
                    )
                if pending.cert_fingerprint:
                    try:
                        self.session_cache.remember(
                            instance_id=request.instance_id,
                            common_name=request.common_name,
                            cert_fingerprint=pending.cert_fingerprint,
                            provider_id=provider.id, provider_app_id=provider.app_id,
                            enterprise_id=identity.enterprise_id,
                            enterprise_name=identity.enterprise_name,
                        )
                    except Exception:
                        # The client was already authorized by OpenVPN. A cache
                        # failure must not falsely report that OAuth failed;
                        # the next reconnect simply returns to the browser.
                        logger.exception("SSO durable session save failed")
                else:
                    logger.warning(
                        "SSO session not cached: OpenVPN did not export "
                        "tls_digest_sha256_0 for instance=%s CID=%s",
                        request.instance_id, request.cid,
                    )
                if pending.started_at:
                    logger.info(
                        "SSO OAuth flow: instance=%s CID=%s "
                        "connect_to_auth_allow_ms=%d",
                        request.instance_id, request.cid,
                        round((time.monotonic() - pending.started_at) * 1000),
                    )
                logger.info(
                    "SSO approved: instance=%s CID=%s KID=%s vpn_cn=%s "
                    "provider=%s enterprise_id=%s external_user_id=%s",
                    request.instance_id, request.cid, request.kid,
                    request.common_name, request.provider_id,
                    identity.enterprise_id, identity.external_user_id or "<unavailable>",
                )
                return SsoFinishResult.APPROVED
            except OSError:
                return SsoFinishResult.UNAVAILABLE


_RUNTIME: SsoRuntime | None = None
_RUNTIME_LOCK = threading.RLock()


def get_sso_runtime() -> SsoRuntime | None:
    with _RUNTIME_LOCK:
        return _RUNTIME


def set_sso_runtime(runtime: SsoRuntime | None) -> None:
    global _RUNTIME
    with _RUNTIME_LOCK:
        _RUNTIME = runtime
