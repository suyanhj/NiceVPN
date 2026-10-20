# -*- coding: utf-8 -*-
"""初始化向导展示状态适配测试。"""

from app.core.init_wizard import StepResult, WizardStep
from app.ui.adapters.init_wizard import (
    adapt_step_result,
    resume_wizard_step,
    step_title,
    wizard_progress_index,
)


def test_wizard_progress_index_collapses_internal_steps() -> None:
    assert wizard_progress_index(WizardStep.DETECT_OPENVPN) == 1
    assert wizard_progress_index(WizardStep.INSTALL_OPENVPN) == 2
    assert wizard_progress_index(WizardStep.CUSTOM_PATH) == 2
    assert wizard_progress_index(WizardStep.CONFIG_PKI) == 3
    assert wizard_progress_index(WizardStep.CONFIG_SUBNET) == 4
    assert wizard_progress_index(WizardStep.CREATE_DEFAULT_GROUP) == 4
    assert wizard_progress_index(WizardStep.START_SERVICE) == 5
    assert wizard_progress_index(WizardStep.DONE) == 5


def test_resume_step_from_persisted_config() -> None:
    assert resume_wizard_step({}, has_default_group=False) == WizardStep.DETECT_OPENVPN
    assert resume_wizard_step(
        {"pki_dir": "/etc/openvpn/pki"},
        has_default_group=False,
    ) == WizardStep.CONFIG_SUBNET
    assert resume_wizard_step(
        {
            "pki_dir": "/etc/openvpn/pki",
            "global_subnet": "10.224.0.0/16",
        },
        has_default_group=False,
    ) == WizardStep.CREATE_DEFAULT_GROUP
    assert resume_wizard_step(
        {
            "pki_dir": "/etc/openvpn/pki",
            "global_subnet": "10.224.0.0/16",
        },
        has_default_group=True,
    ) == WizardStep.START_SERVICE
    assert resume_wizard_step(
        {"initialized": True},
        has_default_group=True,
    ) == WizardStep.DONE


def test_step_result_adapter_and_titles() -> None:
    ok = adapt_step_result(
        WizardStep.CONFIG_PKI,
        StepResult(True, "PKI 初始化完成", next_step=WizardStep.CONFIG_SUBNET),
    )
    failed = adapt_step_result(
        WizardStep.CUSTOM_PATH,
        StepResult(False, "路径无效"),
    )

    assert ok["tone"] == "success"
    assert ok["title"] == "本步完成"
    assert ok["next_step"] == WizardStep.CONFIG_SUBNET
    assert failed["tone"] == "warning"
    assert failed["title"] == "待处理"
    assert step_title(WizardStep.CONFIG_PKI) == "证书体系"
    assert step_title(WizardStep.DONE) == "初始化完成"
