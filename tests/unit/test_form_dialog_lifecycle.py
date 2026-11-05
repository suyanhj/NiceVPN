# -*- coding: utf-8 -*-
"""公共表单：保存回调导致组件删除后不再操作已销毁按钮。"""
from dataclasses import dataclass

from app.ui.components.feedback import AsyncOperationGuard
from app.ui.components.form_dialog import FormDialog


@dataclass
class _Client:
    is_deleted: bool = False


class _FakeElement:
    def __init__(self, deleted=False, client_deleted=False):
        self.is_deleted = deleted
        self.client = _Client(client_deleted)
        self.operations = []

    def props(self, **kwargs):
        self.operations.append(("props", kwargs))

    def disable(self):
        self.operations.append(("disable", None))

    def enable(self):
        self.operations.append(("enable", None))

    def open(self):
        self.operations.append(("open", None))

    def close(self):
        self.operations.append(("close", None))


def _handle(dialog=None, submit=None, cancel=None):
    return FormDialog(
        dialog=dialog or _FakeElement(), card=_FakeElement(),
        body=_FakeElement(), submit_button=submit or _FakeElement(),
        cancel_button=cancel or _FakeElement(), guard=AsyncOperationGuard(),
    )


def test_deleted_buttons_never_receive_busy_updates():
    submit = _FakeElement()
    cancel = _FakeElement()
    handle = _handle(submit=submit, cancel=cancel)
    handle.set_busy(True)
    assert submit.operations
    assert cancel.operations

    submit.is_deleted = True
    cancel.is_deleted = True
    previous_submit = list(submit.operations)
    previous_cancel = list(cancel.operations)
    handle.set_busy(False)
    assert submit.operations == previous_submit
    assert cancel.operations == previous_cancel


def test_one_deleted_button_does_not_block_other_button():
    submit = _FakeElement(deleted=True)
    cancel = _FakeElement()
    handle = _handle(submit=submit, cancel=cancel)
    handle.set_busy(False)
    assert submit.operations == []
    assert ("enable", None) in cancel.operations


def test_deleted_client_skips_button_updates():
    submit = _FakeElement(client_deleted=True)
    cancel = _FakeElement(client_deleted=True)
    handle = _handle(submit=submit, cancel=cancel)
    handle.set_busy(False)
    assert not submit.operations
    assert not cancel.operations


def test_dialog_open_close_skip_deleted_element():
    dialog = _FakeElement(deleted=True)
    handle = _handle(dialog=dialog)
    handle.close()
    handle.open()
    assert not dialog.operations
    dialog.is_deleted = False
    handle.open()
    handle.close()
    assert dialog.operations == [("open", None), ("close", None)]
