"""Auth request transaction safety under retries, expiry and concurrency."""
import concurrent.futures
import sqlite3

from app.services.auth.sso_transactions import WebAuthTransactionStore
from app.storage.sqlite_store import SqliteStore


def test_transaction_is_hashed_and_bound_to_vpn_identity(tmp_path):
    db = SqliteStore(tmp_path / "transactions.sqlite")
    svc = WebAuthTransactionStore(db)
    created = svc.create(instance_id="main", cid=11, kid=2, common_name="alice",
                         provider_id="dingtalk-1", now=1000)
    assert svc.get_pending_by_token(created.token, now=1001) == created.request
    assert svc.get_pending_by_state(created.state, now=1001) == created.request
    with sqlite3.connect(db.path) as conn:
        values = conn.execute(
            "SELECT token_hash, state_hash FROM sso_login_transactions"
        ).fetchone()
        assert created.token not in values
        assert created.state not in values
    assert svc.consume_state(created.state, approved=True, now=1002) == created.request
    assert svc.consume_state(created.state, approved=True, now=1002) is None
    assert svc.get_pending_by_token(created.token, now=1002) is None


def test_transaction_expiration_and_cancel(tmp_path):
    svc = WebAuthTransactionStore(SqliteStore(tmp_path / "expired.sqlite"))
    x = svc.create(instance_id="one", cid=12, kid=0, common_name="bob",
                   provider_id="wecom-1", now=1000, ttl_seconds=30)
    assert svc.get_pending_by_token(x.token, now=1030) is None
    assert svc.consume_state(x.state, approved=True, now=1030) is None
    assert svc.expire(now=1030) == 1
    assert svc.expire(now=1030) == 0
    y = svc.create(instance_id="one", cid=12, kid=1, common_name="bob",
                   provider_id="wecom-1", now=1040)
    assert svc.cancel_connection("one", 12, 0) == 0
    assert svc.get_pending_by_token(y.token, now=1041) is not None
    assert svc.cancel_connection("one", 12, 1) == 1
    assert svc.consume_state(y.state, approved=True, now=1041) is None


def test_denial_is_one_time_and_other_instance_is_independent(tmp_path):
    svc = WebAuthTransactionStore(SqliteStore(tmp_path / "deny.sqlite"))
    first = svc.create(instance_id="one", cid=1, kid=0, common_name="cn",
                       provider_id="dingtalk", now=1000)
    second = svc.create(instance_id="two", cid=1, kid=0, common_name="cn",
                        provider_id="dingtalk", now=1000)
    assert svc.consume_state(first.state, approved=False, now=1001) == first.request
    assert svc.consume_state(first.state, approved=True, now=1001) is None
    assert svc.get_pending_by_state(second.state, now=1001) == second.request


def test_concurrent_callbacks_cannot_double_approve(tmp_path):
    svc = WebAuthTransactionStore(SqliteStore(tmp_path / "race.sqlite"))
    a = svc.create(instance_id="main", cid=9, kid=0, common_name="alice",
                   provider_id="dingtalk", now=1000)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(
            lambda _: svc.consume_state(a.state, approved=True, now=1001),
            range(12),
        ))
    assert sum(result is not None for result in results) == 1
