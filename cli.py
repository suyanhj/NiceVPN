#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NiceVPN CLI 唯一 Python 启动入口。"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许通过系统软链接从任意工作目录启动 CLI。
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.cli.entry import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
