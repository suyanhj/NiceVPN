# -*- coding: utf-8 -*-
"""验证应用入口在启动阶段显式导入页面与依赖。"""

import importlib


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
