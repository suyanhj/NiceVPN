# -*- coding: utf-8 -*-
"""AG Grid 服务端分页所需的轻量筛选/排序模型解析。"""

from __future__ import annotations

from typing import Any, Mapping, Sequence


def set_filter_values(
    filter_model: Mapping[str, Any] | None,
    field: str,
) -> list[str]:
    model = (filter_model or {}).get(field)
    if not isinstance(model, Mapping):
        return []
    values = model.get("values")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    return [str(value) for value in values]


def multi_text_filter_spec(
    filter_model: Mapping[str, Any] | None,
    field: str,
) -> tuple[list[str], str, str] | None:
    """返回 (values, match_type, operator)，兼容新多值过滤器和旧单值文本过滤器。"""

    model = (filter_model or {}).get(field)
    if not isinstance(model, Mapping):
        return None

    if str(model.get("filterType") or "") == "niceVpnMultiText":
        raw_values = model.get("values")
        if not isinstance(raw_values, Sequence) or isinstance(raw_values, (str, bytes)):
            return None
        values = [str(value).strip() for value in raw_values if str(value).strip()]
        if not values:
            return None
        mode = str(model.get("type") or "containsAny")
        mapping = {
            "containsAny": ("contains", "OR"),
            "containsAll": ("contains", "AND"),
            "notContainsAny": ("notContains", "AND"),
            "equalsAny": ("equals", "OR"),
        }
        match_type, operator = mapping.get(mode, ("contains", "OR"))
        return values, match_type, operator

    single = text_filter_spec(filter_model, field)
    if single is None:
        return None
    value, match_type = single
    return [value], match_type, "AND"


def text_filter_spec(
    filter_model: Mapping[str, Any] | None,
    field: str,
) -> tuple[str, str] | None:
    """返回 (文本, AG Grid type)；复杂双条件留给 Python fallback。"""

    model = (filter_model or {}).get(field)
    if not isinstance(model, Mapping):
        return None
    if model.get("operator") or model.get("conditions"):
        return None
    value = model.get("filter")
    if value is None:
        return None
    return str(value), str(model.get("type") or "contains")


def first_sort_spec(
    column_state: Sequence[Mapping[str, Any]] | None,
    *,
    default_field: str,
) -> tuple[str, bool]:
    ordered = sorted(
        (
            row
            for row in (column_state or [])
            if isinstance(row, Mapping) and row.get("sort")
        ),
        key=lambda row: int(row.get("sortIndex") or 0),
    )
    if not ordered:
        return default_field, False
    first = ordered[0]
    return str(first.get("colId") or default_field), str(first.get("sort")) == "desc"


def _single_filter_match(value: Any, model: Mapping[str, Any]) -> bool:
    filter_type = str(model.get("filterType") or "text")
    if filter_type == "niceVpnSet":
        values = model.get("values")
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            return True
        return str(value) in {str(item) for item in values}

    kind = str(model.get("type") or "contains")
    text = "" if value is None else str(value)
    needle = "" if model.get("filter") is None else str(model.get("filter"))

    if kind == "blank":
        return not text
    if kind == "notBlank":
        return bool(text)

    left = text.casefold()
    right = needle.casefold()
    if kind == "equals":
        return left == right
    if kind == "notEqual":
        return left != right
    if kind == "notContains":
        return right not in left
    if kind == "startsWith":
        return left.startswith(right)
    if kind == "endsWith":
        return left.endswith(right)
    if filter_type == "number":
        try:
            actual = float(value)
            target = float(model.get("filter"))
        except (TypeError, ValueError):
            return False
        if kind == "lessThan":
            return actual < target
        if kind == "lessThanOrEqual":
            return actual <= target
        if kind == "greaterThan":
            return actual > target
        if kind == "greaterThanOrEqual":
            return actual >= target
        if kind == "notEqual":
            return actual != target
        return actual == target
    return right in left


def _model_match(value: Any, model: Mapping[str, Any]) -> bool:
    if str(model.get("filterType") or "") == "niceVpnMultiText":
        raw_values = model.get("values")
        if not isinstance(raw_values, Sequence) or isinstance(raw_values, (str, bytes)):
            return True
        values = [str(item).strip() for item in raw_values if str(item).strip()]
        if not values:
            return True
        mode = str(model.get("type") or "containsAny")
        if mode == "containsAll":
            return all(_single_filter_match(value, {"filter": item, "type": "contains"}) for item in values)
        if mode == "notContainsAny":
            return all(_single_filter_match(value, {"filter": item, "type": "notContains"}) for item in values)
        if mode == "equalsAny":
            return any(_single_filter_match(value, {"filter": item, "type": "equals"}) for item in values)
        return any(_single_filter_match(value, {"filter": item, "type": "contains"}) for item in values)

    conditions = model.get("conditions")
    if isinstance(conditions, Sequence) and not isinstance(conditions, (str, bytes)):
        valid = [item for item in conditions if isinstance(item, Mapping)]
        if valid:
            operator = str(model.get("operator") or "AND").upper()
            matches = [_single_filter_match(value, item) for item in valid]
            return any(matches) if operator == "OR" else all(matches)

    condition1 = model.get("condition1")
    condition2 = model.get("condition2")
    if isinstance(condition1, Mapping) and isinstance(condition2, Mapping):
        operator = str(model.get("operator") or "AND").upper()
        matches = (
            _single_filter_match(value, condition1),
            _single_filter_match(value, condition2),
        )
        return any(matches) if operator == "OR" else all(matches)

    return _single_filter_match(value, model)


def row_matches_filter_model(
    row: Mapping[str, Any],
    filter_model: Mapping[str, Any] | None,
    *,
    value_fields: Mapping[str, str] | None = None,
) -> bool:
    aliases = value_fields or {}
    for field, model in (filter_model or {}).items():
        if not isinstance(model, Mapping):
            continue
        source_field = aliases.get(str(field), str(field))
        if not _model_match(row.get(source_field), model):
            return False
    return True


def filter_rows(
    rows: Sequence[Mapping[str, Any]],
    filter_model: Mapping[str, Any] | None,
    *,
    value_fields: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in rows
        if row_matches_filter_model(row, filter_model, value_fields=value_fields)
    ]


def sort_rows(
    rows: Sequence[Mapping[str, Any]],
    column_state: Sequence[Mapping[str, Any]] | None,
    *,
    default_field: str,
) -> list[dict[str, Any]]:
    field, descending = first_sort_spec(column_state, default_field=default_field)

    def key(row: Mapping[str, Any]) -> tuple[int, Any]:
        value = row.get(field)
        if isinstance(value, (int, float)):
            return 0, value
        return 1, str(value or "").casefold()

    return sorted((dict(row) for row in rows), key=key, reverse=descending)
