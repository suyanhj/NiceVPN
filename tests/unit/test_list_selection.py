"""管理列表全选辅助函数单元测试。

用真实的 NiceGUI 复选框验证，避免测试替身掩盖 Quasar 的实际行为。
"""

import pytest
from nicegui import ui

from app.ui.components.list_selection import (
    apply_row_checkboxes,
    sync_select_all_checkbox,
    update_visible_selection,
)


@pytest.fixture
def change_log() -> list:
    """记录复选框 on_change 回调的调用值。"""
    return []


def test_update_visible_selection_preserves_hidden_records():
    """测试全选只更新当前可见记录，不影响其他分页。"""
    selected = {"hidden"}
    update_visible_selection(selected, ["alice", "bob"], True)
    assert selected == {"hidden", "alice", "bob"}

    update_visible_selection(selected, ["alice", "bob"], False)
    assert selected == {"hidden"}


def test_sync_does_not_retrigger_change_handler(change_log):
    """测试程序化同步不会反向触发 on_change，避免自激重绘。"""
    checkbox = ui.checkbox(value=False, on_change=lambda e: change_log.append(e.value))

    sync_select_all_checkbox(checkbox, ["a", "b"], {"a", "b"})

    assert checkbox.value is True
    assert change_log == []


def test_partial_selection_uses_quasar_indeterminate_value():
    """测试半选状态下 model-value 为 None，即 Quasar 的 indeterminate。"""
    checkbox = ui.checkbox(value=False)

    sync_select_all_checkbox(checkbox, ["a", "b"], {"a"})

    assert checkbox.value is None
    assert checkbox._props["model-value"] is None


def test_full_and_empty_selection_states():
    """测试全选与全不选状态。"""
    checkbox = ui.checkbox(value=False)

    sync_select_all_checkbox(checkbox, ["a", "b"], {"a", "b"})
    assert checkbox.value is True

    sync_select_all_checkbox(checkbox, ["a", "b"], set())
    assert checkbox.value is False


def test_empty_list_disables_checkbox():
    """测试无可见记录时禁用全选框。"""
    checkbox = ui.checkbox(value=True)

    sync_select_all_checkbox(checkbox, [], set())

    assert checkbox.value is False
    assert checkbox.enabled is False


def test_apply_row_checkboxes_is_silent(change_log):
    """测试批量同步行复选框不会触发各行 on_change。"""
    rows = {
        key: ui.checkbox(value=False, on_change=lambda e: change_log.append(e.value))
        for key in ("a", "b")
    }

    apply_row_checkboxes(rows, {"a"})

    assert rows["a"].value is True
    assert rows["b"].value is False
    assert change_log == []
