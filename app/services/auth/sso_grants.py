# -*- coding: utf-8 -*-
"""Publish short-lived SSO approvals to OpenVPN's post-auth client-connect hook.

Device binding is only skipped when the *trusted* Management auth channel
already approved the same certificate. A grant is not a replacement for the
Management AUTH decision; unauthenticated clients never reach client-connect.
"""
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

from app.core.constants import OPENVPN_MGMT_DIR

GRANTS_DIR = OPENVPN_MGMT_DIR / "sso_grants"


def _grant_name(instance_id: str, common_name: str, peer_ip: str, peer_port: str) -> str:
    raw = (instance_id + "\0" + common_name).encode("utf-8")
    return hashlib.sha256(raw).hexdigest() + ".json"


def write_sso_grant(
    instance_id: str, common_name: str, *, peer_ip: str, peer_port: str,\n    grant_dir: Path | None = None
) -> None:
    if not instance_id or not common_name or not peer_ip or not peer_port:
        raise ValueError("Missing SSO grant identity")
    root = grant_dir if grant_dir is not None else GRANTS_DIR
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o755)
    target = root / _grant_name(instance_id, common_name, peer_ip, peer_port)
    record = {
        "version": 1,
        "instance": instance_id,
        "common_name": common_name,\n        "peer_ip": peer_ip,\n        "peer_port": peer_port,
        "expires_at": int(time.time()) + 120,
    }
    fd, stage = tempfile.mkstemp(dir=root, prefix=".sso-")
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(record, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(stage, target)
    finally:
        if os.path.exists(stage):
            os.unlink(stage)
