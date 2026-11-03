# -*- coding: utf-8 -*-
"""用户/组 CLI 管理面契约测试。"""

from __future__ import annotations

from app.cli.entry import build_parser


def _parse(*argv: str):
    return build_parser().parse_args(list(argv))


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
        args = _parse(*argv)
        assert args.func.__name__ == func_name


def test_user_management_commands_are_exposed() -> None:
    cases = {
        ("user", "list"): "cmd_user_list",
        ("user", "show", "alice"): "cmd_user_show",
        ("user", "add", "--username", "alice", "--group-name", "开发组"): "cmd_user_add",
        ("user", "delete", "alice"): "cmd_user_delete",
        ("user", "enable", "alice"): "cmd_user_enable",
        ("user", "disable", "alice"): "cmd_user_disable",
        ("user", "move", "alice", "--group-name", "运维组"): "cmd_user_move",
        ("user", "regenerate-ovpn", "alice"): "cmd_user_regenerate_ovpn",
        ("user", "kick", "alice"): "cmd_user_kick",
        ("user", "device", "show", "alice"): "cmd_user_device_show",
        ("user", "device", "reset", "alice"): "cmd_user_device_reset",
        ("user", "ovpn", "show", "alice"): "cmd_user_ovpn_show",
        ("user", "ovpn", "export", "alice", "--output", "/tmp/alice.ovpn"): "cmd_user_ovpn_export",
        ("user", "ovpn", "regenerate", "alice"): "cmd_user_ovpn_regenerate",
    }

    for argv, func_name in cases.items():
        args = _parse(*argv)
        assert args.func.__name__ == func_name


def test_legacy_add_commands_remain_compatible() -> None:
    assert _parse(
        "add-group", "--name", "开发组", "--subnet", "10.8.1.0/24"
    ).func.__name__ == "cmd_add_group"
    assert _parse(
        "add-user", "--username", "alice", "--group-name", "开发组"
    ).func.__name__ == "cmd_add_user"


def test_dry_run_is_available_on_mutating_group_and_user_commands() -> None:
    assert _parse(
        "group", "delete", "--group-name", "开发组", "--dry-run"
    ).dry_run is True
    assert _parse(
        "user", "move", "alice", "--group-name", "运维组", "--dry-run"
    ).dry_run is True


def test_device_and_ovpn_mutations_support_dry_run() -> None:
    assert _parse("user", "device", "reset", "alice", "--dry-run").dry_run is True
    assert _parse(
        "user", "ovpn", "export", "alice", "--output", "/tmp/alice.ovpn", "--dry-run"
    ).dry_run is True
    assert _parse("user", "ovpn", "regenerate", "alice", "--dry-run").dry_run is True
