#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deployed with device-bind.sh, no NiceVPN Python dependency required."""
import argparse
import hashlib
import json
import time
from pathlib import Path


def has_grant(*, grant_dir: Path, instance: str, common_name: str,
              peer_ip: str, peer_port: str, config: Path) -> bool:
    if not instance or not common_name or not peer_ip or not peer_port or not config.is_file():
        return False
    try:
        conf = config.read_text(encoding="utf-8")
        # A successful grant is never a substitute for management-client-auth.
        # Always require a Unix Management socket restricted to root.
        directives = {line.strip() for line in conf.splitlines() if line.strip()
                      and not line.lstrip().startswith("#")}
        if "management-client-auth" not in directives or "management-client-user root" not in directives:
            return False
        if not any(line.startswith("management ") and " unix" in line for line in directives):
            return False
        filename = hashlib.sha256((instance + "\0" + common_name + "\0" + peer_ip + "\0" + peer_port).encode()).hexdigest() + ".json"
        data = json.loads((grant_dir / filename).read_text(encoding="utf-8"))
        return (
            data.get("instance") == instance
            and data.get("common_name") == common_name
            and data.get("peer_ip") == peer_ip
            and data.get("peer_port") == peer_port
            and isinstance(data.get("expires_at"), int)
            and int(time.time()) < data["expires_at"] <= int(time.time()) + 120
        )
    except (OSError, ValueError, TypeError, UnicodeError):
        return False


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--grant-dir", type=Path, required=True)
    p.add_argument("--instance", required=True)
    p.add_argument("--common-name", required=True)
    p.add_argument("--peer-ip", required=True)
    p.add_argument("--peer-port", required=True)
    p.add_argument("--server-config", type=Path, required=True)
    args = p.parse_args()
    return 0 if has_grant(
        grant_dir=args.grant_dir, instance=args.instance,
        common_name=args.common_name, peer_ip=args.peer_ip,
        peer_port=args.peer_port, config=args.server_config,
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
