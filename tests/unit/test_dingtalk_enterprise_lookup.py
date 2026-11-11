from app.services.auth.dingtalk_enterprise_lookup import (
    DingTalkEnterpriseLookupStore,
)


def test_lookup_state_is_one_time_and_result_can_be_consumed():
    store = DingTalkEnterpriseLookupStore()
    state = store.create(
        "https://vpn.example.test/auth/sso/dingtalk/enterprise-lookup/callback",
        "ding-app",
    )

    assert len(state) >= 32
    started = store.begin_start(state)
    assert started is not None
    assert started.status == "started"
    assert store.begin_start(state) is None

    processing = store.begin_callback(state)
    assert processing is not None
    assert processing.status == "processing"
    assert store.begin_callback(state) is None

    assert store.complete(
        state, corp_id="dingCorp", enterprise_name="示例企业",
    )
    result = store.get(state)
    assert result is not None
    assert result.status == "completed"
    assert result.corp_id == "dingCorp"
    assert result.enterprise_name == "示例企业"
    assert store.consume(state) == result
    assert store.get(state) is None


def test_lookup_state_rejects_callback_before_start_and_after_failure():
    store = DingTalkEnterpriseLookupStore()
    state = store.create(
        "https://vpn.example.test/auth/sso/dingtalk/enterprise-lookup/callback",
        "ding-app",
    )

    assert store.begin_callback(state) is None
    assert store.fail(state, "not started") is False

    assert store.begin_start(state) is not None
    assert store.fail(state, "authorization cancelled")
    failed = store.get(state)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.error == "authorization cancelled"
    assert store.consume(state) == failed
