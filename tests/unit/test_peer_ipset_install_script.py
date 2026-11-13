# -*- coding: utf-8 -*-
"""对端防火墙依赖安装：隔离 PATH 模拟 apt/dnf/yum，不修改测试机系统。"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "app" / "scripts" / "peer" / "ensure-firewall-tools.sh"
COMMON = SCRIPT.parents[1] / "common" / "linux-packages.sh"
pytestmark = pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="requires Linux shell",
)


def _run_installer(
    tmp_path: Path,
    os_release: str | None,
    *,
    installed_commands: tuple[str, ...] = (),
    include_ipset: bool = True,
    manager: str = "apt-get",
    creates_commands: bool = True,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """包管理器和命令均为 mock；不会执行真实 apt/dnf/yum。"""
    bin_dir = tmp_path / "mock-bin"
    bin_dir.mkdir()
    os_release_path = tmp_path / "os-release"
    if os_release is not None:
        os_release_path.write_text(os_release, encoding="utf-8")

    script = COMMON.read_text(encoding="utf-8") + "\n" + SCRIPT.read_text(encoding="utf-8")
    original_path = 'export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"'
    assert original_path in script
    script = script.replace(original_path, 'export PATH="$NICEVPN_TEST_PATH"')

    calls_path = tmp_path / "package-calls.txt"
    package_stub = """#!/bin/bash
printf '%s %s\n' "${0##*/}" "$*" >> "$NICEVPN_TEST_COMMANDS"
if [[ "$1" == "install" && "$NICEVPN_TEST_CREATE_COMMANDS" == "1" ]]; then
    shift 2
    for package in "$@"; do
        case "$package" in
            iptables|ipset)
                printf '#!/bin/bash\nexit 0\n' > "$NICEVPN_TEST_PATH/$package"
                /bin/chmod +x "$NICEVPN_TEST_PATH/$package"
                ;;
        esac
    done
fi
"""
    manager_path = bin_dir / manager
    manager_path.write_text(package_stub, encoding="utf-8")
    manager_path.chmod(0o755)

    for command in installed_commands:
        binary = bin_dir / command
        binary.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        binary.chmod(0o755)

    env = dict(os.environ)
    env.update(
        {
            "NICEVPN_TEST_PATH": str(bin_dir),
            "NICEVPN_OS_RELEASE_FILE": str(os_release_path),
            "NICEVPN_TEST_COMMANDS": str(calls_path),
            "NICEVPN_TEST_CREATE_COMMANDS": "1" if creates_commands else "0",
            "NICEVPN_INCLUDE_IPSET": "1" if include_ipset else "0",
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
    for path in (COMMON, SCRIPT):
        result = subprocess.run(
            [shutil.which("bash") or "/bin/bash", "-n", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr


def test_existing_iptables_and_ipset_skip_package_manager(tmp_path: Path):
    result, calls = _run_installer(
        tmp_path, "ID=debian\n",
        installed_commands=("iptables", "ipset"),
    )

    assert result.returncode == 0, result.stderr
    assert calls == []


def test_debian_installs_iptables_and_ipset_in_one_package_transaction(tmp_path: Path):
    result, calls = _run_installer(tmp_path, "ID=ubuntu\nID_LIKE=debian\n")

    assert result.returncode == 0, result.stderr
    assert calls == [
        "apt-get update -qq",
        "apt-get install -y iptables ipset",
    ]


def test_rhel_dnf_installs_iptables_and_ipset_in_one_package_transaction(tmp_path: Path):
    result, calls = _run_installer(
        tmp_path, "ID=rocky\nID_LIKE=rhel\n", manager="dnf",
    )

    assert result.returncode == 0, result.stderr
    assert calls == ["dnf install -y iptables ipset"]


def test_rhel_yum_fallback_installs_iptables_and_ipset_in_one_package_transaction(tmp_path: Path):
    result, calls = _run_installer(
        tmp_path, "ID=centos\nID_LIKE=rhel\n", manager="yum",
    )

    assert result.returncode == 0, result.stderr
    assert calls == ["yum install -y iptables ipset"]


def test_without_tag_ipsets_only_iptables_is_required(tmp_path: Path):
    result, calls = _run_installer(
        tmp_path, "ID=debian\n", include_ipset=False,
    )

    assert result.returncode == 0, result.stderr
    assert calls == [
        "apt-get update -qq",
        "apt-get install -y iptables",
    ]


def test_one_missing_command_installs_the_complete_required_package_set(tmp_path: Path):
    result, calls = _run_installer(
        tmp_path,
        "ID=debian\n",
        installed_commands=("iptables",),
        include_ipset=True,
    )

    assert result.returncode == 0, result.stderr
    assert calls == [
        "apt-get update -qq",
        "apt-get install -y iptables ipset",
    ]


def test_installer_rejects_unsupported_distro(tmp_path: Path):
    result, calls = _run_installer(tmp_path, "ID=arch\n")

    assert result.returncode != 0
    assert "不支持的发行版" in result.stderr
    assert calls == []


def test_installer_rejects_missing_command_after_package_install(tmp_path: Path):
    result, calls = _run_installer(
        tmp_path, "ID=debian\n", creates_commands=False,
    )

    assert result.returncode != 0
    assert "安装后仍找不到 iptables" in result.stderr
    assert calls == [
        "apt-get update -qq",
        "apt-get install -y iptables ipset",
    ]



def test_shared_helper_deduplicates_packages_for_multiple_commands(tmp_path: Path):
    bin_dir = tmp_path / "mock-bin"
    bin_dir.mkdir()
    os_release = tmp_path / "os-release"
    os_release.write_text("ID=debian\\n", encoding="utf-8")
    calls_path = tmp_path / "calls.txt"
    manager = bin_dir / "apt-get"
    manager.write_text(
        "#!/bin/bash\\n"
        'printf "%s\\n" "$*" >> "$NICEVPN_TEST_COMMANDS"\\n'
        'if [[ "$1" == "install" ]]; then\\n'
        '  shift 2\\n'
        '  for package in "$@"; do\\n'
        '    if [[ "$package" == "coreutils" ]]; then\\n'
        '      for command in cp ln; do printf "#!/bin/bash\\nexit 0\\n" > "$NICEVPN_TEST_PATH/$command"; chmod +x "$NICEVPN_TEST_PATH/$command"; done\\n'
        '    fi\\n'
        '  done\\n'
        'fi\\n',
        encoding="utf-8",
    )
    manager.chmod(0o755)

    script = COMMON.read_text(encoding="utf-8") + "\\nnicevpn_ensure_commands cp coreutils ln coreutils\\n"
    env = dict(os.environ)
    env.update({
        "PATH": str(bin_dir),
        "NICEVPN_TEST_PATH": str(bin_dir),
        "NICEVPN_OS_RELEASE_FILE": str(os_release),
        "NICEVPN_TEST_COMMANDS": str(calls_path),
    })
    result = subprocess.run(
        [shutil.which("bash") or "/bin/bash", "-c", script],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert calls_path.read_text(encoding="utf-8").splitlines() == [
        "update -qq",
        "install -y coreutils",
    ]
