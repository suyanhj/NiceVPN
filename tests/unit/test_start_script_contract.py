# -*- coding: utf-8 -*-
"""start.sh / systemd 部署契约测试。"""

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
START = ROOT / "start.sh"
SERVICE = ROOT / "deploy" / "systemd" / "openvpn-mgmt.service"


def _select_python_for_test(
    root: Path, *, explicit_python: str = ""
) -> subprocess.CompletedProcess[str]:
    """只执行 start.sh 的解释器选择函数，避免启动 NiceGUI 或写入 systemd。"""
    assert START.read_text(encoding="utf-8").strip().endswith('main "$@"')
    script = (
        "source <(sed '$d' \"$1\")\n"
        'ROOT_DIR="$2"\n'
        'select_python\n'
        'printf "\\nSELECTED_PYTHON=%s\\n" "$PYTHON_BIN"\n'
    )
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    env.pop("CONDA_PREFIX", None)
    env["PYTHON_BIN"] = explicit_python
    return subprocess.run(
        ["bash", "-c", script, "bash", str(START), str(root)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_start_sh_keeps_project_venv_python_symlink(tmp_path: Path) -> None:
    """uv 的 .venv/bin/python 常指向基础解释器，不可 realpath 后写入 unit。"""
    venv_dir = tmp_path / ".venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(venv_dir)],
        capture_output=True,
        text=True,
        check=True,
    )

    result = _select_python_for_test(tmp_path)
    assert result.returncode == 0, result.stderr
    selected = result.stdout.split("SELECTED_PYTHON=", 1)[1].strip()
    assert selected == str(venv_dir / "bin" / "python")

    # 由选择出的解释器启动时，应以虚拟环境为 sys.prefix，不能退回系统解释器。
    prefix = subprocess.check_output(
        [selected, "-c", "import sys; print(sys.prefix)"], text=True
    ).strip()
    assert prefix == str(venv_dir)


def test_start_sh_explicit_python_still_has_highest_priority(tmp_path: Path) -> None:
    """显式设置 PYTHON_BIN 的原有覆盖能力不能丢。"""
    (tmp_path / ".venv").mkdir()
    result = _select_python_for_test(tmp_path, explicit_python=sys.executable)
    assert result.returncode == 0, result.stderr
    assert f"SELECTED_PYTHON={os.path.abspath(sys.executable)}" in result.stdout


def test_start_sh_does_not_silently_ignore_broken_project_venv(tmp_path: Path) -> None:
    """项目 .venv 损坏时直接报错，不回退到 uv 的系统解释器。"""
    (tmp_path / ".venv").mkdir()
    result = _select_python_for_test(tmp_path)
    assert result.returncode != 0
    assert "项目 .venv 存在，但 bin/python 不可执行" in result.stderr


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
    assert "os.path.abspath(sys.executable)" in text
    assert "os.path.realpath(sys.executable)" not in text
    assert "sys.version_info >= (3, 10)" in text
    assert 'if [[ "${1:-}" == "daemon" ]]' in text
    assert "systemctl daemon-reload" in text
    assert 'systemctl enable "${SERVICE_NAME}"' in text
    assert 'systemctl restart "${SERVICE_NAME}"' in text
    assert 'systemctl restart "${API_SERVICE_NAME}"' not in text
    assert 'systemctl enable "${API_SERVICE_NAME}"' not in text
    assert 'exec "${PYTHON_BIN}" -m app.api.server' not in text


def test_systemd_template_runs_python_main_directly() -> None:
    text = SERVICE.read_text(encoding="utf-8")

    assert "WorkingDirectory=@NICEVPN_ROOT@" in text
    assert 'ExecStart="@PYTHON_BIN@" main.py' in text
    assert "start.sh" not in next(
        line for line in text.splitlines() if line.startswith("ExecStart=")
    )


def test_systemd_template_runs_one_process_for_two_ports() -> None:
    text = SERVICE.read_text(encoding="utf-8")
    assert 'ExecStart="@PYTHON_BIN@" main.py' in text
    assert "EnvironmentFile=-/etc/nicevpn/listen.env" in text
    assert not (ROOT / "deploy" / "systemd" / "openvpn-mgmt-api.service").exists()


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
    assert 'DOCKER_NODE_IMAGE="${DOCKER_NODE_IMAGE:-' in build_script
    assert "--file -" in build_script
    assert "--target frontend-assets" not in build_script
    assert "deploy/docker/Dockerfile" not in build_script
    assert "frontend-build.json" in build_script
    assert "生产环境无需构建" in build_script
