# -*- coding: utf-8 -*-
"""Unit 测试的系统环境隔离。"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_openvpn_runtime_dirs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit 测试不得创建或修改宿主机 /etc/openvpn。"""

    from app.core import config as config_module

    monkeypatch.setattr(config_module, "ensure_openvpn_runtime_dirs", lambda: None)
