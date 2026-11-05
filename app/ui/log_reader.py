# -*- coding: utf-8 -*-
"""有界文件尾部日志读取；供本机服务及对端安装日志共用。"""

from __future__ import annotations

import os
from pathlib import Path


DEFAULT_LOG_TAIL_MAX_BYTES = 512 * 1024


def read_log_tail_lines(
    path: Path,
    max_lines: int,
    *,
    max_bytes: int = DEFAULT_LOG_TAIL_MAX_BYTES,
) -> str:
    """按行数与读取字节双重限制，避免大文件或超长单行耗尽内存。

    不读取整份文件，也不将文件内容缓存到业务状态中。
    """
    if max_lines <= 0 or max_bytes <= 0:
        return ""

    block_size = 65536
    chunks: list[bytes] = []
    total_newlines = 0
    total_bytes = 0
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        position = stream.tell()
        while position > 0 and total_newlines <= max_lines and total_bytes < max_bytes:
            step = min(block_size, position, max_bytes - total_bytes)
            position -= step
            stream.seek(position)
            chunk = stream.read(step)
            chunks.append(chunk)
            total_newlines += chunk.count(b"\n")
            total_bytes += len(chunk)

    raw = b"".join(reversed(chunks))
    tail_lines = raw.splitlines()[-max_lines:]
    body = "\n".join(line.decode("utf-8", errors="replace") for line in tail_lines)
    if position > 0 and total_bytes >= max_bytes:
        return f"(日志过大，仅读取末尾 {max_bytes // 1024} KiB)\n{body}"
    return body
