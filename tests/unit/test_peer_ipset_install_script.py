# -*- coding: utf-8 -*-
"""远端 ipset Shell 安装器：通过隔离 PATH 模拟包管理器，不修改测试机系统。"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "app" / "scripts" / "peer" / "ensure-ipset.sh"
pytestmark = pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="requires Linux shell",
)


def _run_installer(
    tmp_path: Path,
    os_release: str | None,
    *,
    installed: bool = False,
    manager: str = "apt-get",
    creates_ipset: bool = True,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """所有包管理器都是 mock；不会执行系统的 apt/dnf/yum。"""
    bin_dir = tmp_path / "mock-bin"
    bin_dir.mkdir()
    os_release_path = tmp_path / "os-release"
    if os_release is not None:
        os_release_path.write_text(os_release, encoding="utf-8")

    script = SCRIPT.read_text(encoding="utf-8")
    original_path = 'export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"'
    assert original_path in script
    script = script.replace(original_path, 'export PATH="$NICEVPN_TEST_PATH"')
    script = script.replace("/etc/os-release", str(os_release_path))

    calls_path = tmp_path / "package-calls.txt"
    package_stub = """#!/bin/bash
printf '%s %s\\n' "${0##*/}" "$*" >> "$NICEVPN_TEST_COMMANDS"
if [[ "$NICEVPN_TEST_CREATE_IPSET" == "1" && "$1" == "install" ]]; then
    printf '#!/bin/bash\\nexit 0\\n' > "$NICEVPN_TEST_PATH/ipset"
    /bin/chmod +x "$NICEVPN_TEST_PATH/ipset"
fi
"""
    manager_path = bin_dir / manager
    manager_path.write_text(package_stub, encoding="utf-8")
    manager_path.chmod(0o755)

    if installed:
        binary = bin_dir / "ipset"
        binary.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        binary.chmod(0o755)

    env = dict(os.environ)
    env.update(
        {
            "NICEVPN_TEST_PATH": str(bin_dir),
            "NICEVPN_TEST_COMMANDS": str(calls_path),
            "NICEVPN_TEST_CREATE_IPSET": "1" if creates_ipset else "0",
        }
    )
    result = subprocess.run(
        [shutil.which("bash") or "/bin/bash", "-c", script],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    calls = calls_path.read_text(encoding="utf-8").splitlines() if calls_path.exists() else []
    return result, calls


def test_installer_shell_syntax():
    result = subprocess.run(
        [shutil.which("bash") or "/bin/bash", "-n", str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_installer_reuses_existing_ipset_without_package_changes(tmp_path: Path):
    result, calls = _run_installer(tmp_path, "ID=debian\n", installed=True)

    assert result.returncode == 0, result.stderr
    assert "跳过" in result.stdout
    assert calls == []


def test_installer_installs_debian_ipset(tmp_path: Path):
    result, calls = _run_installer(tmp_path, "ID=ubuntu\nID_LIKE=debian\n")

    assert result.returncode == 0, result.stderr
    assert calls == ["apt-get update -qq", "apt-get install -y ipset"]


def test_installer_installs_rhel_ipset_via_dnf(tmp_path: Path):
    result, calls = _run_installer(tmp_path, "ID=rocky\nID_LIKE=rhel\n", manager="dnf")

    assert result.returncode == 0, result.stderr
    assert calls == ["dnf install -y ipset"]


def test_installer_installs_rhel_ipset_via_yum_fallback(tmp_path: Path):
    result, calls = _run_installer(tmp_path, "ID=centos\nID_LIKE=rhel\n", manager="yum")

    assert result.returncode == 0, result.stderr
    assert calls == ["yum install -y ipset"]


def test_installer_rejects_unsupported_distro(tmp_path: Path):
    result, calls = _run_installer(tmp_path, "ID=arch\n")

    assert result.returncode != 0
    assert "不支持的发行版" in result.stderr
    assert calls == []


def test_installer_rejects_missing_os_release(tmp_path: Path):
    result, calls = _run_installer(tmp_path, None)

    assert result.returncode != 0
    assert "无法自动安装 ipset" in result.stderr
    assert calls == []


def test_installer_rejects_success_without_ipset(tmp_path: Path):
    result, calls = _run_installer(
        tmp_path, "ID=debian\n", creates_ipset=False,
    )

    assert result.returncode != 0
    assert "ipset 不在 PATH 中" in result.stderr
    assert calls == ["apt-get update -qq", "apt-get install -y ipset"]
