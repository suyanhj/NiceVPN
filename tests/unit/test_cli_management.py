# -*- coding: utf-8 -*-
"""NiceVPN CLI 命令层级、标签和防火墙管理契约测试。"""

from __future__ import annotations

import pytest

from app.cli.entry import build_parser


def _parse(*argv: str):
    return build_parser().parse_args(list(argv))


def test_root_help_only_exposes_resource_groups() -> None:
    help_text = build_parser().format_help()
    for command in ("group", "user", "tag", "firewall", "admin"):
        assert command in help_text
    for legacy in ("add-group", "add-user", "add-firewall", "hash-admin-password"):
        assert legacy not in help_text


def test_group_management_commands_are_exposed() -> None:
    cases = {
        ("group", "list"): "cmd_group_list",
        ("group", "show", "--group-name", "开发组"): "cmd_group_show",
        ("group", "add", "--name", "开发组", "--subnet", "10.8.1.0/24"): "cmd_group_add",
        ("group", "update", "--group-name", "开发组", "--name", "研发组"): "cmd_group_update",
        ("group", "delete", "--group-name", "开发组"): "cmd_group_delete",
        ("group", "enable", "--group-name", "开发组"): "cmd_group_enable",
        ("group", "disable", "--group-name", "开发组"): "cmd_group_disable",
    }
    for argv, func_name in cases.items():
        assert _parse(*argv).func.__name__ == func_name


def test_user_management_commands_are_exposed_without_duplicate_ovpn_action() -> None:
    cases = {
        ("user", "list"): "cmd_user_list",
        ("user", "show", "alice"): "cmd_user_show",
        ("user", "add", "--username", "alice", "--group-name", "开发组"): "cmd_user_add",
        ("user", "delete", "alice"): "cmd_user_delete",
        ("user", "enable", "alice"): "cmd_user_enable",
        ("user", "disable", "alice"): "cmd_user_disable",
        ("user", "move", "alice", "--group-name", "运维组"): "cmd_user_move",
        ("user", "kick", "alice"): "cmd_user_kick",
        ("user", "device", "show", "alice"): "cmd_user_device_show",
        ("user", "device", "reset", "alice"): "cmd_user_device_reset",
        ("user", "ovpn", "show", "alice"): "cmd_user_ovpn_show",
        ("user", "ovpn", "export", "alice", "--output", "/tmp/alice.ovpn"): "cmd_user_ovpn_export",
        ("user", "ovpn", "regenerate", "alice"): "cmd_user_ovpn_regenerate",
    }
    for argv, func_name in cases.items():
        assert _parse(*argv).func.__name__ == func_name

    with pytest.raises(SystemExit):
        _parse("user", "regenerate-ovpn", "alice")


def test_tag_crud_and_policy_flags_are_exposed() -> None:
    cases = {
        ("tag", "list"): "cmd_tag_list",
        ("tag", "show", "员工"): "cmd_tag_show",
        ("tag", "add", "--name", "员工"): "cmd_tag_add",
        ("tag", "update", "员工", "--sso-required"): "cmd_tag_update",
        ("tag", "delete", "员工"): "cmd_tag_delete",
    }
    for argv, func_name in cases.items():
        assert _parse(*argv).func.__name__ == func_name

    updated = _parse("tag", "update", "员工")
    assert updated.device_binding_exempt is None
    assert updated.sso_required is None
    assert _parse("tag", "add", "--name", "豁免组", "--device-binding-exempt").device_binding_exempt


def test_tag_member_operations_support_users_and_groups() -> None:
    cases = {
        ("tag", "member", "list", "员工"): "cmd_tag_member_list",
        ("tag", "member", "add", "员工", "--user", "alice", "--group", "研发组"): "cmd_tag_member_change",
        ("tag", "member", "remove", "员工", "--user", "alice"): "cmd_tag_member_change",
        ("tag", "member", "set", "员工", "--group", "研发组"): "cmd_tag_member_change",
        ("tag", "member", "clear", "员工"): "cmd_tag_member_change",
    }
    for argv, func_name in cases.items():
        assert _parse(*argv).func.__name__ == func_name

    args = _parse("tag", "member", "add", "员工", "--user", "alice", "--group", "研发组")
    assert args.operation == "add"
    assert args.usernames == ["alice"]
    assert args.groups == ["研发组"]


def test_firewall_commands_are_nested_and_support_tag_owner() -> None:
    cases = {
        ("firewall", "rule", "list"): "cmd_firewall_rule_list",
        (
            "firewall", "rule", "add", "--owner-type", "tag", "--owner", "员工",
            "--iptables-line", "iptables -A FORWARD -s 10.8.1.0/24 -j ACCEPT",
        ): "cmd_add_firewall",
        ("firewall", "rule", "delete", "rule-id"): "cmd_firewall_rule_delete",
        ("firewall", "rule", "enable", "rule-id"): "cmd_firewall_rule_enable",
        ("firewall", "rule", "disable", "rule-id"): "cmd_firewall_rule_disable",
    }
    for argv, func_name in cases.items():
        assert _parse(*argv).func.__name__ == func_name

    with pytest.raises(SystemExit):
        _parse("add-firewall", "--owner-type", "group")


def test_admin_password_tool_is_nested() -> None:
    assert _parse("admin", "password", "hash").func.__name__ == "cmd_hash_admin_password"
    with pytest.raises(SystemExit):
        _parse("hash-admin-password")


def test_dry_run_is_available_on_mutating_commands() -> None:
    assert _parse("group", "delete", "--group-name", "开发组", "--dry-run").dry_run is True
    assert _parse("user", "move", "alice", "--group-name", "运维组", "--dry-run").dry_run is True
    assert _parse("tag", "delete", "员工", "--dry-run").dry_run is True
    assert _parse("tag", "member", "add", "员工", "--user", "alice", "--dry-run").dry_run is True
    assert _parse("firewall", "rule", "delete", "rule-id", "--dry-run").dry_run is True


def test_device_and_ovpn_mutations_support_dry_run() -> None:
    assert _parse("user", "device", "reset", "alice", "--dry-run").dry_run is True
    assert _parse(
        "user", "ovpn", "export", "alice", "--output", "/tmp/alice.ovpn", "--dry-run"
    ).dry_run is True
    assert _parse("user", "ovpn", "regenerate", "alice", "--dry-run").dry_run is True
