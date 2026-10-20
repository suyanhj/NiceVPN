# -*- coding: utf-8 -*-
"""验证应用入口在启动阶段显式导入页面与依赖。"""

import ast
import importlib
from pathlib import Path


MAIN_PATH = Path(__file__).resolve().parents[2] / "main.py"


def test_main_has_no_function_local_imports() -> None:
    """入口函数内不得通过延迟导入掩盖模块、依赖或循环错误。"""

    tree = ast.parse(MAIN_PATH.read_text(encoding="utf-8"))
    local_imports: list[tuple[str, int]] = []
    for function in (
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        for node in ast.walk(function):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                local_imports.append((function.name, node.lineno))
    assert local_imports == []


def test_main_imports_all_page_modules_without_cycle() -> None:
    """重新导入入口时所有页面模块均已就绪且不存在循环导入。"""

    main_module = importlib.import_module("main")
    for class_name in (
        "DashboardPage",
        "UsersPage",
        "GroupsPage",
        "PeersPage",
        "FirewallPage",
        "CertsPage",
        "ServicesPage",
        "SettingsPage",
        "InitPage",
    ):
        assert getattr(main_module, class_name) is not None
