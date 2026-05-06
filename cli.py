#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""独立入口：与 ``python main.py cli ...`` 等价，``python cli.py add-group ...``。"""
from __future__ import annotations

import sys

from app.cli.entry import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
