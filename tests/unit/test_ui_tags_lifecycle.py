# -*- coding: utf-8 -*-
"""Tag actions should notify before their originating card is removed."""
from __future__ import annotations

from types import SimpleNamespace

from app.ui.pages import tags as tags_module


def _page(monkeypatch, *, should_fail=False):
    events = []
    page = tags_module.TagsPage()
    def delete(tag_id):
        events.append(("delete", tag_id))
        if should_fail:
            raise ValueError("规则引用中")
    page.service = SimpleNamespace(delete=delete)
    page._refresh = lambda: events.append(("refresh", None))
    monkeypatch.setattr(
        tags_module, "safe_notify",
        lambda message, *, type: events.append(("notify", type, message)) or True,
    )
    dialog = SimpleNamespace(close=lambda: events.append(("close", None)))
    return page, dialog, events


def test_delete_notifies_before_refresh_clears_original_slot(monkeypatch):
    page, dialog, events = _page(monkeypatch)
    assert page._delete_tag_and_refresh("tag-1", dialog) is True
    assert events == [
        ("delete", "tag-1"),
        ("notify", "positive", "标签已删除"),
        ("close", None),
        ("refresh", None),
    ]


def test_delete_error_does_not_close_or_redraw(monkeypatch):
    page, dialog, events = _page(monkeypatch, should_fail=True)
    assert page._delete_tag_and_refresh("tag-1", dialog) is False
    assert events == [
        ("delete", "tag-1"),
        ("notify", "negative", "删除失败：规则引用中"),
    ]


def test_notify_context_failure_never_relabels_success_as_delete_failure(monkeypatch):
    page, dialog, events = _page(monkeypatch)
    # The common safe_notify wrapper can return False when client context is gone.
    monkeypatch.setattr(
        tags_module, "safe_notify",
        lambda *_args, **_kwargs: False,
    )
    assert page._delete_tag_and_refresh("tag-2", dialog) is True
    assert events == [("delete", "tag-2"), ("close", None), ("refresh", None)]
