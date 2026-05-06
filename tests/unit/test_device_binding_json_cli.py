# -*- coding: utf-8 -*-
"""device_binding_json.py 命令行（与 device-bind.sh 约定一致）。"""

import json
import subprocess
import sys
from pathlib import Path


def _script_path() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "app" / "scripts" / "device_binding_json.py"


def test_write_new_and_update(tmp_path: Path):
    """write-new 创建文件；update 改写字段。"""
    py = _script_path()
    d = tmp_path / "bindings"
    r = subprocess.run(
        [
            sys.executable,
            str(py),
            "write-new",
            "--bindings-dir",
            str(d),
            "--username",
            "alice",
            "--fingerprint",
            "fp1",
            "--fingerprint-source",
            "IV_HWADDR",
            "--iv-plat",
            "android",
            "--time-ascii",
            "2026-04-09 12:00:00",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    files = list(d.glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["username"] == "alice"
    assert data["iv_plat"] == "android"
    assert data["last_connected_since"] == "2026-04-09 12:00:00"

    r2 = subprocess.run(
        [
            sys.executable,
            str(py),
            "update",
            "--file",
            str(files[0]),
            "--iv-plat",
            "",
            "--time-ascii",
            "2026-04-10 08:00:00",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r2.returncode == 0, r2.stderr
    data2 = json.loads(files[0].read_text(encoding="utf-8"))
    assert "iv_plat" not in data2
    assert data2["last_connected_since"] == "2026-04-10 08:00:00"
