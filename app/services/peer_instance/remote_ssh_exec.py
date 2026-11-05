# -*- coding: utf-8 -*-
"""Shared bounded-time SSH command execution primitive for peer operations."""

from __future__ import annotations

import paramiko


def exec_ssh(
    client: paramiko.SSHClient,
    command: str,
    *,
    timeout: int,
) -> tuple[str, str, int]:
    """Run one remote command, returning decoded stdout, stderr and exit status."""
    _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    out_b = stdout.read()
    err_b = stderr.read()
    code = stdout.channel.recv_exit_status()
    return (
        out_b.decode("utf-8", errors="replace"),
        err_b.decode("utf-8", errors="replace"),
        code,
    )
