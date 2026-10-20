# -*- coding: utf-8 -*-
"""初始化向导展示状态适配。"""

from __future__ import annotations

from typing import Any, Mapping

from app.core.init_wizard import StepResult, WizardStep


DISPLAY_STEPS: tuple[tuple[WizardStep, str], ...] = (
    (WizardStep.DETECT_OPENVPN, "环境校验"),
    (WizardStep.INSTALL_OPENVPN, "组件准备"),
    (WizardStep.CONFIG_PKI, "证书体系"),
    (WizardStep.CONFIG_SUBNET, "接入规划"),
    (WizardStep.START_SERVICE, "服务接管"),
)


def wizard_progress_index(step: WizardStep) -> int:
    """把内部步骤映射到 1..5 的展示步骤序号。"""

    if step == WizardStep.DETECT_OPENVPN:
        return 1
    if step in {WizardStep.INSTALL_OPENVPN, WizardStep.CUSTOM_PATH}:
        return 2
    if step == WizardStep.CONFIG_PKI:
        return 3
    if step in {WizardStep.CONFIG_SUBNET, WizardStep.CREATE_DEFAULT_GROUP}:
        return 4
    return 5


def resume_wizard_step(
    config: Mapping[str, Any],
    *,
    has_default_group: bool,
) -> WizardStep:
    """根据已持久化配置推导刷新后应该恢复的页面步骤。"""

    initialized = bool(config.get("initialized"))
    if initialized:
        return WizardStep.DONE
    pki_ready = bool(str(config.get("pki_dir") or "").strip())
    subnet_ready = bool(str(config.get("global_subnet") or "").strip())

    if pki_ready and subnet_ready:
        return (
            WizardStep.START_SERVICE
            if has_default_group
            else WizardStep.CREATE_DEFAULT_GROUP
        )
    if pki_ready:
        return WizardStep.CONFIG_SUBNET
    return WizardStep.DETECT_OPENVPN


def adapt_step_result(
    step: WizardStep,
    result: StepResult,
) -> dict[str, Any]:
    """把步骤结果统一成 UI 可消费的状态语义。"""

    success = bool(result.success)
    title = "本步完成" if success else "待处理"
    tone = "success" if success else "warning"
    message = str(result.message or "").strip() or (
        "步骤执行完成" if success else "步骤需要处理"
    )
    return {
        "step": step,
        "success": success,
        "tone": tone,
        "title": title,
        "message": message,
        "next_step": result.next_step,
    }


def step_title(step: WizardStep) -> str:
    """返回当前步骤标题。"""

    return {
        WizardStep.DETECT_OPENVPN: "环境检查",
        WizardStep.INSTALL_OPENVPN: "安装组件",
        WizardStep.CUSTOM_PATH: "校验路径",
        WizardStep.CONFIG_PKI: "证书体系",
        WizardStep.CONFIG_SUBNET: "接入网段",
        WizardStep.CREATE_DEFAULT_GROUP: "默认策略域",
        WizardStep.START_SERVICE: "启动服务",
        WizardStep.DONE: "初始化完成",
    }[step]
