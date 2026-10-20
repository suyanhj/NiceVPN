# -*- coding: utf-8 -*-
"""初始化向导共享页面、恢复、生命周期与失败重试测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from nicegui import ui
from nicegui.testing import user_simulation

from app.core.init_wizard import InitWizard, StepResult, WizardStep
from app.ui.pages.init_page import InitPage, _quick_cidr_check


class FakeWizard:
    def __init__(
        self,
        *,
        config: dict | None = None,
        results: dict[WizardStep, StepResult | Exception] | None = None,
    ) -> None:
        self.config = dict(config or {})
        self.results = dict(results or {})
        self.calls: list[tuple[WizardStep, dict]] = []

    def run_step(
        self,
        step: WizardStep,
        data: dict | None = None,
    ) -> StepResult:
        self.calls.append((step, dict(data or {})))
        value = self.results.get(step)
        if isinstance(value, Exception):
            raise value
        if value is not None:
            return value
        return StepResult(False, f"{step.value} pending")


def _page(
    monkeypatch: pytest.MonkeyPatch,
    wizard: FakeWizard,
    *,
    has_default_group: bool = False,
) -> InitPage:
    monkeypatch.setattr(
        "app.ui.pages.init_page.InitWizard",
        lambda: wizard,
    )
    page = InitPage()
    monkeypatch.setattr(
        page,
        "_has_default_group",
        lambda: has_default_group,
    )
    return page


def test_init_page_missing_openvpn_shows_install_and_custom_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wizard = FakeWizard(
        results={
            WizardStep.DETECT_OPENVPN: StepResult(
                False,
                "未检测到 OpenVPN",
                next_step=WizardStep.INSTALL_OPENVPN,
            )
        }
    )
    page = _page(monkeypatch, wizard)

    async def verify() -> None:
        async with user_simulation(page.render) as user:
            response = await user.open("/")
            assert response.status_code == 200
            await user.should_see("安全接入平台初始化")
            await user.should_see("接入组件未就绪")
            await user.should_see("自动安装 OpenVPN")
            await user.should_see("指定已安装路径")
            assert page.current_step == WizardStep.DETECT_OPENVPN

    asyncio.run(verify())
    assert wizard.calls[0][0] == WizardStep.DETECT_OPENVPN


def test_init_page_detect_ready_goes_to_pki(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wizard = FakeWizard(
        results={
            WizardStep.DETECT_OPENVPN: StepResult(
                True,
                "已检测到 OpenVPN 2.7.0",
                next_step=WizardStep.CONFIG_PKI,
            )
        }
    )
    page = _page(monkeypatch, wizard)

    async def verify() -> None:
        async with user_simulation(page.render) as user:
            await user.open("/")
            await user.should_see("建立平台证书体系")
            await user.should_see("开始建立证书体系")

    asyncio.run(verify())


def test_init_page_resumes_from_existing_pki_and_subnet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pki_page = _page(
        monkeypatch,
        FakeWizard(config={"pki_dir": "/etc/openvpn/pki"}),
    )

    async def verify_pki_resume() -> None:
        async with user_simulation(pki_page.render) as user:
            await user.open("/")
            await user.should_see("已恢复进度")
            await user.should_see("规划接入地址池")
            assert pki_page.current_step == WizardStep.CONFIG_SUBNET

    asyncio.run(verify_pki_resume())

    service_page = _page(
        monkeypatch,
        FakeWizard(
            config={
                "pki_dir": "/etc/openvpn/pki",
                "global_subnet": "10.224.0.0/16",
            }
        ),
        has_default_group=True,
    )

    async def verify_service_resume() -> None:
        async with user_simulation(service_page.render) as user:
            await user.open("/")
            await user.should_see("启动默认接入服务")
            await user.should_see("启动并进入平台")
            assert service_page.current_step == WizardStep.START_SERVICE

    asyncio.run(verify_service_resume())


def test_custom_path_exception_isolated_and_form_remains_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wizard = FakeWizard(
        results={
            WizardStep.DETECT_OPENVPN: StepResult(False, "未检测到 OpenVPN"),
            WizardStep.CUSTOM_PATH: RuntimeError("detector crashed"),
        }
    )
    page = _page(monkeypatch, wizard)

    async def verify() -> None:
        async with user_simulation(page.render) as user:
            await user.open("/")
            page._run_step(
                WizardStep.CUSTOM_PATH,
                {"path": "/opt/openvpn"},
            )
            await user.should_see("校验自定义执行路径")
            await user.should_see("校验并继续")
            await user.should_see("返回上一步")
            assert page.current_step == WizardStep.CUSTOM_PATH

    asyncio.run(verify())


def test_install_and_pki_duplicate_submit_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _page(monkeypatch, FakeWizard())
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.ui.pages.init_page.ui.notify",
        lambda message, type="info", **_kwargs: messages.append(
            (str(message), type)
        ),
    )

    page._install_running = True
    page._handle_install()
    assert messages[-1] == (
        "OpenVPN 正在安装，请稍候",
        "warning",
    )

    page._pki_running = True
    page._handle_pki_init()
    assert messages[-1] == (
        "证书体系初始化进行中，请稍候",
        "warning",
    )


def test_init_page_cleanup_deactivates_all_ui_timers() -> None:
    page = InitPage.__new__(InitPage)
    page._client_deleted = False

    class Timer:
        def __init__(self) -> None:
            self.calls = 0

        def deactivate(self) -> None:
            self.calls += 1

    install = Timer()
    pki = Timer()
    complete = Timer()
    page._install_timer = install
    page._pki_timer = pki
    page._complete_timer = complete

    page._cleanup_timers()

    assert page._client_deleted is True
    assert install.calls == 1
    assert pki.calls == 1
    assert complete.calls == 1
    assert page._install_timer is None
    assert page._pki_timer is None
    assert page._complete_timer is None


def test_render_registers_client_delete_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _page(
        monkeypatch,
        FakeWizard(
            results={
                WizardStep.DETECT_OPENVPN: StepResult(
                    False,
                    "未检测到 OpenVPN",
                )
            }
        ),
    )

    async def verify() -> None:
        async with user_simulation(page.render) as user:
            await user.open("/")
            client = page.content.client
            assert len(client.delete_handlers) >= 1

    asyncio.run(verify())


def test_pki_failure_keeps_retry_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _page(monkeypatch, FakeWizard())

    def build() -> None:
        page.render()
        page.current_step = WizardStep.CONFIG_PKI
        page._render_step_result(
            StepResult(False, "PKI 初始化失败")
        )

    async def verify() -> None:
        async with user_simulation(build) as user:
            await user.open("/")
            await user.should_see("PKI 初始化失败")
            await user.should_see("开始建立证书体系")

    asyncio.run(verify())


def test_start_service_failure_keeps_retry_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _page(
        monkeypatch,
        FakeWizard(
            config={
                "pki_dir": "/etc/openvpn/pki",
                "global_subnet": "10.224.0.0/16",
            },
            results={
                WizardStep.START_SERVICE: StepResult(
                    False,
                    "启动失败",
                )
            },
        ),
        has_default_group=True,
    )

    async def verify() -> None:
        async with user_simulation(page.render) as user:
            await user.open("/")
            page._run_step(
                WizardStep.START_SERVICE,
                {"instance_name": "server"},
            )
            await user.should_see("启动失败")
            await user.should_see("启动并进入平台")
            assert page.current_step == WizardStep.START_SERVICE

    asyncio.run(verify())


def test_initialized_wizard_shows_ready_state_without_running_detect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wizard = FakeWizard(config={"initialized": True})
    page = _page(monkeypatch, wizard, has_default_group=True)

    class FakeTimer:
        def deactivate(self) -> None:
            pass

    monkeypatch.setattr(
        "app.ui.pages.init_page.ui.timer",
        lambda *_args, **_kwargs: FakeTimer(),
    )

    async def verify() -> None:
        async with user_simulation(page.render) as user:
            await user.open("/")
            await user.should_see("平台已就绪")
            await user.should_see("进入后台")
            assert page.current_step == WizardStep.DONE

    asyncio.run(verify())
    assert wizard.calls == []


@pytest.mark.parametrize(
    ("cidr", "expected"),
    [
        ("10.224.0.0/16", True),
        ("10.224.0.0/24", True),
        ("10.224.0.0/30", True),
        ("10.224.0.0/15", False),
        ("10.224.0.0/31", False),
        ("10.224.0.1/32", False),
        ("not-a-cidr", False),
    ],
)
def test_quick_cidr_check_matches_documented_pool_range(
    cidr: str,
    expected: bool,
) -> None:
    assert _quick_cidr_check(cidr) is expected


def test_core_wizard_rejects_31_and_32_subnets() -> None:
    wizard = InitWizard.__new__(InitWizard)

    result31 = wizard._config_subnet(
        {"subnet": "10.224.0.0/31"}
    )
    result32 = wizard._config_subnet(
        {"subnet": "10.224.0.1/32"}
    )

    assert result31.success is False
    assert "/16～/30" in result31.message
    assert result32.success is False
    assert "/16～/30" in result32.message


def test_root_page_uses_dashboard_after_initialized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main as app_main

    events: list[str] = []
    monkeypatch.setattr(
        app_main,
        "load_config",
        lambda: SimpleNamespace(initialized=True),
    )
    monkeypatch.setattr(app_main, "setup_theme", lambda: None)
    monkeypatch.setattr(
        app_main,
        "render_app_shell",
        lambda *_args, **_kwargs: events.append("shell"),
    )
    monkeypatch.setattr(
        app_main.DashboardPage,
        "render",
        lambda _self: ui.label("dashboard-ready"),
    )
    monkeypatch.setattr(
        app_main.InitPage,
        "render",
        lambda _self: ui.label("init-wizard"),
    )

    async def verify() -> None:
        async with user_simulation(app_main._render_root_page) as user:
            await user.open("/")
            await user.should_see("dashboard-ready")
            await user.should_not_see("init-wizard")

    asyncio.run(verify())
    assert events == ["shell"]
