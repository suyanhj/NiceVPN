# -*- coding: utf-8 -*-
"""AG Grid 标签式多文本过滤模型测试。"""

from app.ui.grid_query import (
    filter_rows,
    multi_text_filter_spec,
)


def _model(mode: str, values: list[str]) -> dict:
    return {
        "name": {
            "filterType": "niceVpnMultiText",
            "type": mode,
            "values": values,
        }
    }


def test_multi_text_filter_spec_maps_modes() -> None:
    assert multi_text_filter_spec(_model("containsAny", ["aa", "bb"]), "name") == (
        ["aa", "bb"],
        "contains",
        "OR",
    )
    assert multi_text_filter_spec(_model("containsAll", ["aa", "bb"]), "name") == (
        ["aa", "bb"],
        "contains",
        "AND",
    )
    assert multi_text_filter_spec(_model("notContainsAny", ["aa", "bb"]), "name") == (
        ["aa", "bb"],
        "notContains",
        "AND",
    )
    assert multi_text_filter_spec(_model("equalsAny", ["aa", "bb"]), "name") == (
        ["aa", "bb"],
        "equals",
        "OR",
    )


def test_multi_text_filter_fallback_matches_all_modes() -> None:
    rows = [
        {"name": "aa-bb"},
        {"name": "aa-only"},
        {"name": "bb-only"},
        {"name": "cc"},
    ]

    assert [row["name"] for row in filter_rows(rows, _model("containsAny", ["aa", "bb"]))] == [
        "aa-bb",
        "aa-only",
        "bb-only",
    ]
    assert [row["name"] for row in filter_rows(rows, _model("containsAll", ["aa", "bb"]))] == [
        "aa-bb",
    ]
    assert [row["name"] for row in filter_rows(rows, _model("notContainsAny", ["aa", "bb"]))] == [
        "cc",
    ]
    assert [row["name"] for row in filter_rows(rows, _model("equalsAny", ["aa-only", "cc"]))] == [
        "aa-only",
        "cc",
    ]
