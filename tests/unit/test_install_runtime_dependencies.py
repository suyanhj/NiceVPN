# -*- coding: utf-8 -*-
"""本机 OpenVPN/EasyRSA 运行与安装依赖统一预检测试。"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
COMMON = ROOT / "app" / "scripts" / "common" / "linux-packages.sh"
SCRIPT = ROOT / "app" / "scripts" / "install" / "ensure-runtime-dependencies.sh"
pytestmark = pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="requires Bash",
)


def _run_profile(
    tmp_path: Path,
    os_release: str,
    profile: str,
    *,
    installed_commands: tuple[str, ...] = (),
    manager: str = "apt-get",
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir = tmp_path / "mock-bin"
    bin_dir.mkdir()
    os_release_path = tmp_path / "os-release"
    os_release_path.write_text(os_release, encoding="utf-8")
    calls_path = tmp_path / "package-calls.txt"

    package_stub = """#!/bin/bash
printf '%s %s\\n' "${0##*/}" "$*" >> "$NICEVPN_TEST_COMMANDS"
if [[ "$1" == "install" ]]; then
    shift 2
    for package in "$@"; do
        case "$package" in
            iptables)
                for command in iptables iptables-save iptables-restore; do
                    printf '#!/bin/bash\\nexit 0\\n' > "$NICEVPN_TEST_PATH/$command"
                    /bin/chmod +x "$NICEVPN_TEST_PATH/$command"
                done
                ;;
            ipset) commands=(ipset) ;;
            conntrack|conntrack-tools) commands=(conntrack) ;;
            procps|procps-ng) commands=(sysctl) ;;
            logrotate) commands=(logrotate) ;;
            openssl) commands=(openssl) ;;
            curl) commands=(curl) ;;
            tar) commands=(tar) ;;
            gzip) commands=(gzip) ;;
            findutils) commands=(find) ;;
            coreutils) commands=(cp ln mktemp mkdir rm mv head) ;;
            gnupg) commands=(gpg) ;;
            *) commands=() ;;
        esac
        for command in "${commands[@]:-}"; do
            printf '#!/bin/bash\\nexit 0\\n' > "$NICEVPN_TEST_PATH/$command"
            /bin/chmod +x "$NICEVPN_TEST_PATH/$command"
        done
    done
fi
"""
    manager_file = bin_dir / manager
    manager_file.write_text(package_stub, encoding="utf-8")
    manager_file.chmod(0o755)

    for command in installed_commands:
        binary = bin_dir / command
        binary.write_text("#!/bin/bash\\nexit 0\\n", encoding="utf-8")
        binary.chmod(0o755)

    script_text = SCRIPT.read_text(encoding="utf-8")
    path_export = 'export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"'
    assert path_export in script_text
    script_text = script_text.replace(path_export, 'export PATH="$NICEVPN_TEST_PATH"')
    source_line = 'source "${SCRIPT_DIR}/../common/linux-packages.sh"'
    assert source_line in script_text
    script_text = script_text.replace(source_line, f'source "{COMMON}"')
    test_script = tmp_path / "ensure-runtime-dependencies.sh"
    test_script.write_text(script_text, encoding="utf-8")

    env = dict(os.environ)
    env.update(
        {
            "NICEVPN_TEST_PATH": str(bin_dir),
            "NICEVPN_OS_RELEASE_FILE": str(os_release_path),
            "NICEVPN_TEST_COMMANDS": str(calls_path),
            "NICEVPN_DEPENDENCY_PROFILE": profile,
        }
    )
    result = subprocess.run(
        [shutil.which("bash") or "/bin/bash", str(test_script)],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    calls = calls_path.read_text(encoding="utf-8").splitlines() if calls_path.exists() else []
    return result, calls


def test_existing_openvpn_runtime_tools_skip_package_manager(tmp_path: Path):
    commands = (
        "iptables", "iptables-save", "iptables-restore", "ipset",
        "conntrack", "sysctl", "logrotate",
    )
    result, calls = _run_profile(
        tmp_path, "ID=debian\\n", "openvpn-runtime",
        installed_commands=commands,
    )

    assert result.returncode == 0, result.stderr
    assert calls == []


def test_existing_openvpn_repairs_all_missing_runtime_tools_in_one_debian_transaction(
    tmp_path: Path,
):
    result, calls = _run_profile(
        tmp_path, "ID=ubuntu\\nID_LIKE=debian\\n", "openvpn-runtime",
    )

    assert result.returncode == 0, result.stderr
    assert calls == [
        "apt-get update -qq",
        "apt-get install -y iptables ipset conntrack procps logrotate",
    ]


def test_existing_openvpn_repairs_all_missing_runtime_tools_in_one_rhel_transaction(
    tmp_path: Path,
):
    result, calls = _run_profile(
        tmp_path, "ID=rocky\\nID_LIKE=rhel\\n", "openvpn-runtime",
        manager="dnf",
    )

    assert result.returncode == 0, result.stderr
    assert calls == [
        "dnf install -y iptables ipset conntrack-tools procps-ng logrotate",
    ]


def test_existing_easyrsa_only_repairs_openssl_runtime_dependency(tmp_path: Path):
    result, calls = _run_profile(
        tmp_path, "ID=debian\\n", "easyrsa-runtime",
    )

    assert result.returncode == 0, result.stderr
    assert calls == [
        "apt-get update -qq",
        "apt-get install -y openssl",
    ]


def test_missing_easyrsa_installs_all_download_and_unpack_tools_once(tmp_path: Path):
    result, calls = _run_profile(
        tmp_path, "ID=debian\\n", "easyrsa-install",
    )

    assert result.returncode == 0, result.stderr
    assert calls == [
        "apt-get update -qq",
        "apt-get install -y curl tar gzip openssl findutils coreutils",
    ]


def test_runtime_dependency_profile_rejects_unknown_profile(tmp_path: Path):
    result, calls = _run_profile(
        tmp_path, "ID=debian\\n", "unknown-profile",
    )

    assert result.returncode == 2
    assert "未知依赖配置" in result.stderr
    assert calls == []
