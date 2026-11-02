# -*- coding: utf-8 -*-
"""start.sh / systemd 部署契约测试。"""

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
START = ROOT / "start.sh"
SERVICE = ROOT / "deploy" / "systemd" / "openvpn-mgmt.service"


def test_start_sh_bash_syntax() -> None:
    result = subprocess.run(
        ["bash", "-n", str(START)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_start_sh_supports_python_discovery_and_daemon_mode() -> None:
    text = START.read_text(encoding="utf-8")

    assert 'PYTHON_BIN="${PYTHON_BIN:-}"' in text
    assert "VIRTUAL_ENV" in text
    assert "CONDA_PREFIX" in text
    assert ".venv/bin/python" in text
    assert "sys.version_info >= (3, 10)" in text
    assert 'if [[ "${1:-}" == "daemon" ]]' in text
    assert "systemctl daemon-reload" in text
    assert 'systemctl enable "${SERVICE_NAME}"' in text
    assert 'systemctl restart "${SERVICE_NAME}"' in text


def test_systemd_template_runs_python_main_directly() -> None:
    text = SERVICE.read_text(encoding="utf-8")

    assert "WorkingDirectory=@NICEVPN_ROOT@" in text
    assert 'ExecStart="@PYTHON_BIN@" "@NICEVPN_ROOT@/main.py"' in text
    assert "start.sh" not in next(
        line for line in text.splitlines() if line.startswith("ExecStart=")
    )
