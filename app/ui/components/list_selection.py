"""管理列表全选状态辅助函数。"""

from collections.abc import Collection, Mapping
from contextlib import contextmanager
from typing import Hashable, Iterator, TypeVar

from nicegui.elements.checkbox import Checkbox


SelectionKey = TypeVar("SelectionKey", bound=Hashable)


@contextmanager
def _muted_change_handlers(checkbox: Checkbox) -> Iterator[None]:
    """临时摘除复选框的 change 回调。

    NiceGUI 的 ``set_value`` 在值发生变化时会调用 ``on_change``，
    程序化同步状态会被当成用户点击，进而反向触发全选逻辑。
    """
    handlers = checkbox._change_handlers
    checkbox._change_handlers = []
    try:
        yield
    finally:
        checkbox._change_handlers = handlers


def set_value_silently(checkbox: Checkbox, value: bool | None) -> None:
    """设置复选框状态且不触发 ``on_change``。

    Args:
        checkbox: 目标复选框。
        value: ``True`` 全选、``False`` 未选、``None`` 半选。

    Returns:
        无；直接更新复选框。
    """
    with _muted_change_handlers(checkbox):
        checkbox.set_value(value)


def update_visible_selection(
    selected_keys: set[SelectionKey],
    visible_keys: Collection[SelectionKey],
    checked: bool,
) -> None:
    """根据全选状态更新当前可见记录。

    Args:
        selected_keys: 页面当前保存的选中键集合。
        visible_keys: 当前页或当前页签可见的记录键。
        checked: ``True`` 表示加入全部可见键，``False`` 表示移除。

    Returns:
        无；直接更新 ``selected_keys``。
    """
    if checked:
        selected_keys.update(visible_keys)
    else:
        selected_keys.difference_update(visible_keys)


def apply_row_checkboxes(
    row_checkboxes: Mapping[SelectionKey, Checkbox],
    selected_keys: set[SelectionKey],
) -> None:
    """把选中集合同步到各行复选框，避免整表重绘。

    Args:
        row_checkboxes: 记录键到行复选框的映射。
        selected_keys: 页面当前保存的选中键集合。

    Returns:
        无；直接更新各行复选框。
    """
    for key, checkbox in row_checkboxes.items():
        set_value_silently(checkbox, key in selected_keys)


def sync_select_all_checkbox(
    checkbox: Checkbox,
    visible_keys: Collection[SelectionKey],
    selected_keys: set[SelectionKey],
) -> None:
    """同步全选复选框的禁用、选中和半选状态。

    Args:
        checkbox: 列表标题中的 NiceGUI 复选框。
        visible_keys: 当前页或当前页签可见的记录键。
        selected_keys: 页面当前保存的选中键集合。

    Returns:
        无；直接更新复选框状态。
    """
    if not visible_keys:
        set_value_silently(checkbox, False)
        checkbox.disable()
        return

    checkbox.enable()
    selected_count = sum(key in selected_keys for key in visible_keys)
    if selected_count == 0:
        set_value_silently(checkbox, False)
    elif selected_count == len(visible_keys):
        set_value_silently(checkbox, True)
    else:
        # Quasar 的 indeterminate-value 默认为 null，对应 Python 的 None
        set_value_silently(checkbox, None)
