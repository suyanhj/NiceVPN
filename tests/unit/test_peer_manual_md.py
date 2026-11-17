# -*- coding: utf-8 -*-
"""对端手动部署 Markdown 生成单元测试"""

import subprocess
from pathlib import Path

from app.services.peer_instance import peer_manual_md
from app.services.peer_instance.peer_manual_md import build_peer_site_manual_context, build_peer_site_manual_markdown


def test_build_peer_site_manual_contains_essentials():
    md = build_peer_site_manual_markdown(
        peer_name="站点甲",
        peer_id="abc-uuid",
        bound_username="u1",
        lan_cidrs=["192.168.1.0/24"],
        global_subnet="10.255.0.0/16",
        masquerade_on_peer=False,
    )
    assert "站点甲" in md
    assert "abc-uuid" in md
    assert "peer=abc-uuid" in md
    assert "ovpn-mgmt-peer" in md
    assert "role=fwd-global" in md
    assert "10.255.0.0/16" in md
    assert "192.168.1.0/24" in md
    assert "u1" in md
    assert "/etc/openvpn/client/client.conf" in md
    assert "openvpn-client@client.service" in md
    assert "openvpn-client.service" in md
    assert "OpenVPN 2.7.9" in md
    assert "--disable-dco" in md
    assert "OpenSSL_1_1_1k" in md
    assert "FORWARD" in md
    assert "ovpn-mgmt-peer" in md
    assert "role=fwd-global" in md


def test_build_peer_site_manual_context_has_copyable_commands():
    ctx = build_peer_site_manual_context(
        peer_name="站点甲",
        peer_id="abc-uuid",
        bound_username="u1",
        lan_cidrs=["192.168.1.0/24"],
        global_subnet="10.255.0.0/16",
        masquerade_on_peer=True,
    )
    commands = ctx["commands"]
    joined = "\n".join(commands)
    assert ctx["overview"]["client_config_path"] == "/etc/openvpn/client/client.conf"
    assert len(commands) >= 4
    assert "peer=abc-uuid" in joined
    assert "role=masq" not in joined
    assert "-i tun0" not in joined
    assert all("--disable-dco" not in command for command in commands)
    optional_steps = [step for step in ctx["steps"] if step.get("optional")]
    assert len(optional_steps) == 4
    assert any("--disable-dco" in step["command"] for step in optional_steps)
    assert any("role=masq" in step["command"] for step in optional_steps)



def test_peer_manual_shell_content_is_externalized() -> None:
    template_dir = Path(peer_manual_md._MANUAL_TEMPLATE_DIR)
    for name in (
        "prepare-client.sh.in",
        "start-client.sh.in",
        "forward-rule.sh.in",
        "masquerade.sh.in",
        "verify.sh.in",
        "prepare-build-deps.sh.in",
        "build-openssl.sh.in",
        "build-openvpn.sh.in",
        "site-manual.md.in",
    ):
        assert (template_dir / name).is_file(), name

    py_source = Path(peer_manual_md.__file__).read_text(encoding="utf-8")
    assert "systemctl enable --now openvpn-client@client.service" not in py_source
    assert "iptables -I FORWARD 1" not in py_source
    assert "iptables -t nat -I POSTROUTING 1" not in py_source
    assert "install -d -m 0755 /etc/openvpn/client" not in py_source



def test_peer_manual_shell_templates_have_valid_bash_syntax() -> None:
    template_dir = Path(peer_manual_md._MANUAL_TEMPLATE_DIR)
    for name in (
        "prepare-build-deps.sh.in",
        "build-openssl.sh.in",
        "build-openvpn.sh.in",
        "start-client.sh.in",
    ):
        result = subprocess.run(
            ["bash", "-n", str(template_dir / name)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{name}: {result.stderr}"



def test_optional_manual_steps_are_collapsible_and_base_commands_exclude_them() -> None:
    ctx = build_peer_site_manual_context(
        peer_name="站点甲",
        peer_id="abc-uuid",
        bound_username="u1",
        lan_cidrs=["192.168.1.0/24"],
        global_subnet="10.255.0.0/16",
        masquerade_on_peer=False,
    )
    md = peer_manual_md.build_peer_site_manual_markdown(
        peer_name="站点甲",
        peer_id="abc-uuid",
        bound_username="u1",
        lan_cidrs=["192.168.1.0/24"],
        global_subnet="10.255.0.0/16",
        masquerade_on_peer=False,
    )
    optional_titles = [step["title"] for step in ctx["steps"] if step.get("optional")]
    assert len(optional_titles) == 4
    for title in optional_titles:
        assert f"<summary>" in md
        assert title in md
    assert md.count("<details>") == len(optional_titles)
    assert md.count("</details>") == len(optional_titles)
    assert all("--disable-dco" not in command for command in ctx["commands"])


def test_rhel_build_dependencies_use_one_shared_yum_dnf_package_list() -> None:
    template = Path(peer_manual_md._MANUAL_TEMPLATE_DIR) / "prepare-build-deps.sh.in"
    source = template.read_text(encoding="utf-8")
    assert 'RHEL_PACKAGE_MANAGER="dnf"' in source
    assert 'RHEL_PACKAGE_MANAGER="yum"' in source
    assert source.count('"${RHEL_PACKAGE_MANAGER}" install -y') == 1
    assert source.count("gcc gcc-c++ make") == 1
    result = subprocess.run(
        ["bash", "-n", str(template)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
