# -*- coding: utf-8 -*-
"""NiceGUI Screen 测试：仅本目录启用真实浏览器插件。"""

from __future__ import annotations

import pytest


pytest_plugins = ["nicegui.testing.plugin"]


@pytest.fixture
def chrome_options(chrome_options):
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1440,900")
    return chrome_options
