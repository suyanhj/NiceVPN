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
    assert 'ExecStart="@PYTHON_BIN@" main.py' in text
    assert "start.sh" not in next(
        line for line in text.splitlines() if line.startswith("ExecStart=")
    )



def test_start_sh_uses_precompiled_vue_assets_by_default() -> None:
    text = START.read_text(encoding="utf-8")

    assert 'BUILD_UI="${NICEVPN_BUILD_UI:-0}"' in text
    assert "frontend_assets_ready()" in text
    assert "prepare_ui()" in text
    assert 'if [[ "${1:-}" == "--build-ui" ]]' in text
    assert 'BUILD_UI=1' in text
    assert "使用仓库内预编译 Vue 前端资源" in text


def test_build_ui_supports_local_npm_or_docker() -> None:
    build_script = (ROOT / "build-ui.sh").read_text(encoding="utf-8")

    assert "command -v npm" in build_script
    assert "npm ci --no-audit --no-fund" in build_script
    assert "npm run check" in build_script
    assert 'command -v "${DOCKER_BIN}"' in build_script
    assert "生产环境无需构建" in build_script
