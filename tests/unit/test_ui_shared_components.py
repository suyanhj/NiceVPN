# -*- coding: utf-8 -*-
"""共享后台组件的构建与状态测试。"""

from __future__ import annotations

import asyncio

import pytest
from nicegui import ui
from nicegui.testing import user_simulation

from app.ui.components.actions import create_action
from app.ui.components.cards import info_card, metric_card, quick_link_card
from app.ui.components.charts import chart_options, render_chart_card
from app.ui.components.confirm_dialog import build as build_confirm_dialog
from app.ui.components.data_grid import render_data_grid
from app.ui.components.feedback import AsyncOperationGuard, run_guarded, safe_notify
from app.ui.components.form_dialog import create_form_dialog
from app.ui.components.states import empty_state, error_state, loading_state, no_match_state
from app.ui.components.status import status_badge, structured_value


def test_status_values_cards_and_states_build_without_business_services() -> None:
    """状态、结构化值、卡片和页面状态可独立构建。"""

    state: dict[str, object] = {}

    def page() -> None:
        state["running"] = status_badge("running")
        state["disabled"] = status_badge("disabled")
        state["warning"] = status_badge("warning")
        state["failed"] = status_badge("failed")
        state["unknown"] = status_badge("unknown")
        state["value"] = structured_value("10.255.0.2/32", kind="cidr")
        state["metric"] = metric_card(
            "在线设备",
            12,
            helper="较上次刷新 +2",
            icon="devices",
            tone="success",
        )
        state["compact_metric"] = metric_card(
            "证书总数",
            8,
            helper_tooltip="当前证书库中的证书总数",
            icon="verified_user",
            compact=True,
        )
        state["info"] = info_card(
            "配置影响",
            "修改后需要客户端重新连接。",
            icon="info",
            tone="warning",
        )
        state["quick"] = quick_link_card(
            "证书管理",
            description="查看临期与已吊销证书",
            icon="verified_user",
            on_click=lambda: None,
        )
        loading_state()
        empty_state("暂无用户", description="创建用户后会显示在这里")
        no_match_state(on_clear=lambda: None)
        error_state("远程服务不可达", on_retry=lambda: None)

    async def verify_page() -> None:
        async with user_simulation(page) as user:
            response = await user.open("/")
            assert response.status_code == 200
            for text in (
                "运行",
                "停用",
                "警告",
                "失败",
                "未知",
                "10.255.0.2/32",
                "在线设备",
                "配置影响",
                "证书管理",
                "正在加载",
                "暂无用户",
                "没有匹配结果",
                "远程服务不可达",
            ):
                await user.should_see(text)

    asyncio.run(verify_page())

    assert state["running"].label.text == "运行"
    assert state["value"].label.text == "10.255.0.2/32"
    assert state["metric"].value.text == "12"
    assert "vpn-metric-card--compact" in state["compact_metric"].container._classes
    assert "vpn-metric-card--v2" not in state["compact_metric"].container._classes
    assert state["compact_metric"].helper is None
    assert state["compact_metric"].help_icon is not None
    assert "vpn-metric-card__help-icon" in state["compact_metric"].help_icon._classes
    assert "vpn-card--clickable" in state["quick"].container._classes


def test_status_and_card_validation_rejects_invalid_inputs() -> None:
    """非法类型与空标题应尽早失败。"""

    with pytest.raises(ValueError, match="状态类型"):
        status_badge("broken")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="指标名称"):
        metric_card(" ", 1)
    with pytest.raises(ValueError, match="只能设置一个"):
        quick_link_card("入口", route="/users", on_click=lambda: None)
    with pytest.raises(ValueError, match="紧凑指标卡"):
        metric_card("冲突", 1, compact=True, v2_effect=True)


def test_confirm_dialog_supports_impact_count_and_required_text() -> None:
    """高影响操作可展示范围，并要求指定确认文本。"""

    state: dict[str, object] = {}

    def page() -> None:
        handle = build_confirm_dialog(
            "将重建 CA。",
            lambda: None,
            title="重建 CA",
            impact="现有客户端证书将全部失效。",
            object_count=23,
            confirm_text="REBUILD",
            danger_level="danger",
        )
        handle.dialog.open()
        state["handle"] = handle

    async def verify_page() -> None:
        async with user_simulation(page) as user:
            response = await user.open("/")
            assert response.status_code == 200
            await user.should_see("重建 CA")
            await user.should_see("现有客户端证书将全部失效")
            await user.should_see("影响对象：23 项")
            await user.should_see("请输入 REBUILD 确认")

    asyncio.run(verify_page())

    handle = state["handle"]
    assert handle.confirm_input is not None
    assert handle.confirm_button._props["disable"] is True
    assert handle.confirm_button._props["color"] == "negative"


def test_shared_create_action_supports_semantic_icon_only_mode() -> None:
    """纯图标创建按钮保留非空语义文案，同时不渲染可见文字。"""

    state: dict[str, object] = {}

    def page() -> None:
        state["button"] = create_action(
            "新建用户",
            icon="person_add",
            icon_only=True,
        )

    async def verify_page() -> None:
        async with user_simulation(page) as user:
            response = await user.open("/")
            assert response.status_code == 200

    asyncio.run(verify_page())

    button = state["button"]
    assert button._props.get("aria-label") == "新建用户"
    assert button._props.get("icon") == "person_add"

    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    actions_source = (
        root / "app" / "ui" / "components" / "actions.py"
    ).read_text(encoding="utf-8")
    assert "vpn-action--icon-only" in actions_source

    with pytest.raises(ValueError, match="纯图标操作按钮必须提供图标"):
        create_action("新建用户", icon_only=True)


def test_page_create_actions_share_low_emphasis_v2_variant() -> None:
    """页级新增入口统一使用 create_action，保存/推送等主操作不受影响。"""

    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    actions_source = (
        root / "app" / "ui" / "components" / "actions.py"
    ).read_text(encoding="utf-8")
    shell_css = (
        root / "app" / "ui" / "static" / "css" / "shell.css"
    ).read_text(encoding="utf-8")

    assert '"create": "outline color=primary"' in actions_source
    assert 'def create_action(' in actions_source
    assert ".vpn-action--create" in shell_css
    assert "border: 1px solid rgba(45, 212, 191, 0.62) !important;" in shell_css
    assert "background: rgba(45, 212, 191, 0.035) !important;" in shell_css
    assert "0 0 16px rgba(45, 212, 191, 0.16)" in shell_css

    page_expectations = {
        "groups.py": "新建用户组",
        "peers.py": "新建对端",
        "users.py": "新建用户",
        "firewall.py": "新建规则",
    }
    for filename, label in page_expectations.items():
        source = (
            root / "app" / "ui" / "pages" / filename
        ).read_text(encoding="utf-8")
        assert "create_action(" in source
        create_index = source.index("create_action(")
        assert label in source[create_index:create_index + 260]

    users_source = (
        root / "app" / "ui" / "pages" / "users.py"
    ).read_text(encoding="utf-8")
    firewall_source = (
        root / "app" / "ui" / "pages" / "firewall.py"
    ).read_text(encoding="utf-8")
    peers_source = (
        root / "app" / "ui" / "pages" / "peers.py"
    ).read_text(encoding="utf-8")
    assert "primary_action(" not in users_source
    assert "primary_action(" in firewall_source
    assert "primary_action(" in peers_source


def test_shared_dialog_theme_overrides_legacy_quasar_skin() -> None:
    """共享表单/确认弹窗必须使用 V2 黑灰青蓝主题，而不是 legacy 蓝灰卡片。"""

    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    shell_css = (
        root / "app" / "ui" / "static" / "css" / "shell.css"
    ).read_text(encoding="utf-8")

    assert ".q-dialog .q-card.vpn-form-dialog" in shell_css
    assert ".q-dialog .q-card.vpn-confirm-dialog" in shell_css
    assert "background: #111214 !important;" in shell_css
    assert "background: #0c0c0d !important;" in shell_css
    assert "border-color: #15bde8 !important;" in shell_css
    assert ".q-dialog .vpn-form-dialog__actions .bg-primary" in shell_css


def test_form_dialog_exposes_busy_control_and_preserves_structure() -> None:
    """统一表单对话框可控制提交忙碌状态。"""

    state: dict[str, object] = {}

    def page() -> None:
        handle = create_form_dialog(
            "编辑用户",
            body=lambda: ui.input("用户名", value="alice"),
            on_submit=lambda: True,
        )
        handle.open()
        state["handle"] = handle

    async def verify_page() -> None:
        async with user_simulation(page) as user:
            response = await user.open("/")
            assert response.status_code == 200
            await user.should_see("编辑用户")
            await user.should_see("用户名")
            await user.should_see("保存")
            await user.should_see("取消")

    asyncio.run(verify_page())

    handle = state["handle"]
    handle.set_busy(True)
    assert handle.submit_button._props["loading"] is True
    assert handle.submit_button._props["disable"] is True
    handle.set_busy(False)
    assert handle.submit_button._props.get("loading") is not True


def test_data_grid_uses_community_modules_selection_paging_and_summary() -> None:
    """共享 AG Grid 固定 Community 模块并提供选择、分页和结果摘要。"""

    state: dict[str, object] = {}

    def page() -> None:
        view = render_data_grid(
            (
                {"field": "name", "headerName": "名称"},
                {"field": "status", "headerName": "状态"},
            ),
            (
                {"id": "a", "name": "Alice", "status": "运行"},
                {"id": "b", "name": "Bob", "status": "停用"},
            ),
            page_size=25,
            on_cell_click=lambda _: None,
        )
        state["view"] = view

    async def verify_page() -> None:
        async with user_simulation(page) as user:
            response = await user.open("/")
            assert response.status_code == 200
            await user.should_see("共 2 条")

    asyncio.run(verify_page())

    view = state["view"]
    assert view.grid._props["modules"] == ["AllCommunityModule"]
    assert view.grid.options["theme"] == "quartz"
    assert view.grid.options["pagination"] is True
    assert view.grid.options["paginationPageSize"] == 25
    locale = view.grid.options["localeText"]
    assert locale["contains"] == "包含"
    assert locale["notContains"] == "不包含"
    assert locale["equals"] == "等于"
    assert locale["notEqual"] == "不等于"
    assert locale["startsWith"] == "开头是"
    assert locale["endsWith"] == "结尾是"
    assert locale["blank"] == "为空"
    assert locale["notBlank"] == "不为空"
    assert locale["applyFilter"] == "应用"
    assert locale["clearFilter"] == "清除"
    assert locale["pageSizeSelectorLabel"] == "每页"
    assert view.grid.options["rowSelection"] == {"mode": "multiRow"}
    assert view.grid.options["selectionColumnDef"]["pinned"] == "left"
    assert view.grid.options["selectionColumnDef"]["lockPosition"] == "left"
    assert view.grid.options["selectionColumnDef"]["width"] == 48
    assert view.grid.options["selectionColumnDef"]["suppressMovable"] is True
    assert "suppressHorizontalScroll" not in view.grid.options
    assert ":getRowId" in view.grid.options
    assert view.grid._event_listeners


def test_chart_card_supports_data_empty_and_error_states() -> None:
    """图表卡可在数据、空数据和局部错误之间稳定构建。"""

    state: dict[str, object] = {}

    def page() -> None:
        state["data"] = render_chart_card(
            "服务状态",
            {
                "series": [
                    {
                        "type": "pie",
                        "data": [
                            {"name": "运行", "value": 3},
                            {"name": "停止", "value": 1},
                        ],
                    }
                ]
            },
        )
        state["empty"] = render_chart_card("流量分布", {"series": []})
        state["error"] = render_chart_card("证书趋势", error="证书数据源不可用")

    async def verify_page() -> None:
        async with user_simulation(page) as user:
            response = await user.open("/")
            assert response.status_code == 200
            await user.should_see("服务状态")
            await user.should_see("流量分布")
            await user.should_see("暂无图表数据")
            await user.should_see("证书数据源不可用")

    asyncio.run(verify_page())

    assert state["data"].chart is not None
    assert state["data"].chart._props["theme"]["darkMode"] is True
    assert state["data"].chart._props["theme"]["backgroundColor"] == "transparent"
    assert state["empty"].chart is None
    assert state["empty"].placeholder is not None
    assert state["error"].chart is None
    assert chart_options()["backgroundColor"] == "transparent"


def test_async_operation_guard_blocks_duplicates_and_recovers() -> None:
    """重复提交被忽略，成功和异常后都恢复可操作状态。"""

    async def scenario() -> None:
        guard = AsyncOperationGuard()
        assert guard.try_start() is True
        assert guard.try_start() is False
        guard.finish()

        started, result = await run_guarded(
            guard,
            lambda: 42,
            duplicate_message=None,
        )
        assert started is True
        assert result == 42
        assert guard.busy is False

        errors: list[str] = []

        def fail() -> None:
            raise RuntimeError("boom")

        started, result = await run_guarded(
            guard,
            fail,
            duplicate_message=None,
            on_error=lambda exc: errors.append(str(exc)),
        )
        assert started is True
        assert result is None
        assert errors == ["boom"]
        assert guard.busy is False

    asyncio.run(scenario())


def test_safe_notify_ignores_closed_page_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """页面已关闭导致通知不可用时不得把后台流程再次打断。"""

    def fail_notify(*_args, **_kwargs):
        raise RuntimeError("client gone")

    monkeypatch.setattr("app.ui.components.feedback.ui.notify", fail_notify)
    assert safe_notify("完成", type="positive") is False
