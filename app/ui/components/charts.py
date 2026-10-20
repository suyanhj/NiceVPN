# -*- coding: utf-8 -*-
"""统一 ECharts 主题与图表卡。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from nicegui import ui

from app.ui.components.states import empty_state, error_state


_DARK_ECHART_THEME: dict[str, Any] = {
    "darkMode": True,
    "backgroundColor": "transparent",
    "textStyle": {"color": "#cbd5e1"},
    "title": {"textStyle": {"color": "#d9e2ec"}},
    "legend": {"textStyle": {"color": "#94a3b8"}},
    "categoryAxis": {
        "axisLine": {"lineStyle": {"color": "#475569"}},
        "axisLabel": {"color": "#94a3b8"},
        "splitLine": {"lineStyle": {"color": "rgba(148,163,184,0.12)"}},
    },
    "valueAxis": {
        "axisLine": {"lineStyle": {"color": "#475569"}},
        "axisLabel": {"color": "#94a3b8"},
        "splitLine": {"lineStyle": {"color": "rgba(148,163,184,0.12)"}},
    },
}


@dataclass(slots=True)
class ChartCard:
    container: Any
    chart: Any | None
    placeholder: Any | None


def chart_options(options: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """生成后台统一暗色透明图表基础 option。"""

    base: dict[str, Any] = {
        "backgroundColor": "transparent",
        "textStyle": {"color": "#cbd5e1"},
        "tooltip": {"trigger": "item"},
        "series": [],
    }
    if options:
        base.update(dict(options))
    return base


def _has_series_data(options: Mapping[str, Any]) -> bool:
    series = options.get("series")
    if not isinstance(series, list) or not series:
        return False
    for item in series:
        if not isinstance(item, Mapping):
            continue
        data = item.get("data")
        if data is None:
            return True
        if isinstance(data, (list, tuple)) and data:
            return True
    return False


def render_chart_card(
    title: str,
    options: Mapping[str, Any] | None = None,
    *,
    description: str | None = None,
    error: str | None = None,
    empty_message: str = "暂无可展示的数据",
    on_point_click: Callable[..., Any] | None = None,
) -> ChartCard:
    """渲染带标题、无数据和错误占位的 ECharts 卡片。"""

    normalized_title = str(title).strip()
    if not normalized_title:
        raise ValueError("图表标题不能为空")

    merged = chart_options(options)
    with ui.card().classes("vpn-chart-card w-full") as container:
        ui.label(normalized_title).classes("vpn-chart-card__title")
        if description:
            ui.label(str(description)).classes("vpn-chart-card__description")

        chart = None
        placeholder = None
        if error:
            placeholder = error_state(str(error), title="图表不可用").container
        elif not _has_series_data(merged):
            placeholder = empty_state(
                "暂无图表数据",
                description=empty_message,
                icon="insert_chart_outlined",
            ).container
        else:
            chart = (
                ui.echart(
                    merged,
                    theme=_DARK_ECHART_THEME,
                    on_point_click=on_point_click,
                )
                .classes("vpn-chart-card__chart w-full")
            )
    return ChartCard(container=container, chart=chart, placeholder=placeholder)
