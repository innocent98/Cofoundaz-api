"""The Paystack webhook: signature, idempotency, amount checks, renewals, failures, cancellations."""

import json
from datetime import timedelta

from app.db.models.billing import (
    BillingInvoice,
    BillingOperation,
    BillingSubscription,
    BillingWebhookEvent,
)
from app.db.models.notification import Notification
from app.services.billing import subscription as subs
from app.services.finance.payments import stub_transport
from tests.billing_helpers import KEY, post_event, sign, workspace

B = "/api/v1/billing"
W = "/api/v1/webhooks/billing/paystack"


def _pending_checkout(client, h, plan="growth", interval="monthly"):
    r = client.post(
        f"{B}/subscription/checkout", json={"plan": plan, "interval": interval}, headers=h
    )
    ref = r.json()["data"]["reference"]
    return ref, stub_transport._tx_data(ref, stub_transport.PS_TX[ref])  # noqa: SLF001


def _active(client, db, h):
    ref, _ = _pending_checkout(client, h)
    client.get(f"{B}/subscription/verify", params={"reference": ref}, headers=h)
    return ref


def test_missing_or_wrong_signature_is_401_and_nothing_is_recorded(client, db):
    _, _, h = workspace(db)
    ref, data = _pending_checkout(client, h)
    raw = json.dumps({"event": "charge.success", "data": data}).encode()
    for headers in (
        {},
        {"x-paystack-signature": "deadbeef"},
        {"x-paystack-signature": sign(raw, "sk_test_other")},
    ):
        r = client.post(W, content=raw, headers=headers)
        assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_SIGNATURE"
    assert db.query(BillingWebhookEvent).count() == 0
    assert client.get(f"{B}/subscription", headers=h).json()["data"]["status"] == "free"


def test_unconfigured_server_fails_closed(client, db, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "PAYSTACK_SECRET_KEY", None)
    raw = b'{"event":"charge.success","data":{}}'
    resp = client.post(W, content=raw, headers={"x-paystack-signature": sign(raw)})
    assert resp.status_code == 401


def test_live_key_refused_outside_production(client, db, monkeypatch):
    from app.core.config import settings

    _, _, h = workspace(db)
    monkeypatch.setattr(settings, "PAYSTACK_SECRET_KEY", "sk_live_nope")
    r = client.post(
        f"{B}/subscription/checkout", json={"plan": "growth", "interval": "monthly"}, headers=h
    )
    assert r.status_code == 503 and r.json()["error"]["code"] == "BILLING_NOT_CONFIGURED"
    monkeypatch.setattr(settings, "PAYSTACK_SECRET_KEY", None)
    monkeypatch.setattr(settings, "PAYSTACK_TEST_SECRET_KEY", None)
    resp = client.post(
        f"{B}/subscription/checkout", json={"plan": "growth", "interval": "monthly"}, headers=h
    )
    assert resp.status_code == 503


def test_charge_success_activates_and_a_redelivery_is_a_noop(client, db):
    _, startup, h = workspace(db)
    ref, data = _pending_checkout(client, h)
    r = post_event(client, "charge.success", data)
    assert r.status_code == 200 and r.json()["data"]["outcome"] == "applied"
    assert client.get(f"{B}/subscription", headers=h).json()["data"]["plan"]["key"] == "growth"
    dup = post_event(client, "charge.success", data)
    assert dup.status_code == 200 and dup.json()["data"]["outcome"] == "duplicate"
    assert db.query(BillingInvoice).filter_by(startup_id=startup.id).count() == 1
    assert db.query(BillingWebhookEvent).count() == 1
    # the callback verify after the webhook is also a no-op
    v = client.get(f"{B}/subscription/verify", params={"reference": ref}, headers=h).json()["data"]
    assert v["outcome"] == "already_processed"


def test_a_wrong_amount_or_currency_never_grants_the_plan(client, db):
    _, startup, h = workspace(db)
    ref, data = _pending_checkout(client, h)
    r = post_event(client, "charge.success", {**data, "amount": 100})
    assert r.json()["data"]["outcome"] == "amount_mismatch"
    r = post_event(client, "charge.success", {**data, "currency": "USD"})
    assert r.json()["data"]["outcome"] == "amount_mismatch"
    sub = db.query(BillingSubscription).filter_by(startup_id=startup.id).one()
    assert sub.status == "free"
    assert db.query(BillingOperation).filter_by(reference=ref).one().status == "failed"


def test_unknown_reference_and_other_events_are_acknowledged_and_ignored(client, db):
    workspace(db)
    r = post_event(
        client, "charge.success", {"status": "success", "reference": "nope", "amount": 1}
    )
    assert r.status_code == 200 and r.json()["data"]["outcome"] == "ignored_unknown_reference"
    r = post_event(client, "transfer.success", {"x": 1})
    assert r.json()["data"]["outcome"] == "ignored_event"
    r = post_event(client, "charge.success", {"status": "failed", "reference": "x"})
    assert r.json()["data"]["outcome"] == "not_successful"
    for body in (b"[]", b'{"nope":1}'):
        resp = client.post(W, content=body, headers={"x-paystack-signature": sign(body)})
        assert resp.status_code == 200 and resp.json()["data"]["outcome"] == "ignored_malformed"
    garbage = b"not json"
    resp = client.post(W, content=garbage, headers={"x-paystack-signature": sign(garbage)})
    assert resp.status_code == 401


def test_oversized_bodies_are_rejected(client, db):
    big = b"x" * (300 * 1024)
    resp = client.post(W, content=big, headers={"x-paystack-signature": sign(big)})
    assert resp.status_code == 401


def test_renewal_extends_the_period_once(client, db):
    _, startup, h = workspace(db)
    _active(client, db, h)
    sub = db.query(BillingSubscription).filter_by(startup_id=startup.id).one()
    end = sub.current_period_end
    renewal = {
        "status": "success",
        "reference": "ren-1",
        "amount": 2_400_000,
        "currency": "NGN",
        "paid_at": "2026-11-27T10:00:00.000Z",
        "customer": {"customer_code": sub.paystack_customer_code},
        "plan": {"plan_code": "PLN_x"},
    }
    r = post_event(client, "charge.success", renewal)
    assert r.json()["data"]["outcome"] == "renewed"
    db.refresh(sub)
    assert sub.current_period_start.isoformat().startswith(
        "2026-11-27T10:00"
    )  # anchored on the payment
    assert sub.current_period_end > end
    assert db.query(BillingInvoice).filter_by(startup_id=startup.id).count() == 2
    again = post_event(client, "charge.success", {**renewal, "amount": 2_400_001})
    assert again.json()["data"]["outcome"] == "already_processed"
    assert db.query(BillingInvoice).filter_by(startup_id=startup.id).count() == 2


def test_renewal_for_a_scheduled_downgrade_switches_the_plan(client, db):
    _, startup, h = workspace(db)
    ref, _ = _pending_checkout(client, h, "scale")
    client.get(f"{B}/subscription/verify", params={"reference": ref}, headers=h)
    client.post(
        f"{B}/subscription/change",
        json={"plan": "growth", "interval": "monthly"},
        headers={**h, "Idempotency-Key": "auto-1"},
    )
    sub = db.query(BillingSubscription).filter_by(startup_id=startup.id).one()
    post_event(
        client,
        "charge.success",
        {
            "status": "success",
            "reference": "ren-2",
            "amount": 2_400_000,
            "currency": "NGN",
            "customer": {"customer_code": sub.paystack_customer_code},
            "plan": {"plan_code": "PLN_y"},
        },
    )
    db.refresh(sub)
    assert sub.plan_key == "growth" and sub.pending_plan_key is None


def test_renewal_without_a_matching_subscription_is_ignored(client, db):
    workspace(db)
    r = post_event(
        client,
        "charge.success",
        {
            "status": "success",
            "reference": "ren-x",
            "amount": 1,
            "currency": "NGN",
            "customer": {"customer_code": "CUS_none"},
            "plan": {"plan_code": "P"},
        },
    )
    assert r.json()["data"]["outcome"] == "ignored_no_subscription"


def test_payment_failure_starts_grace_then_drops_to_starter(client, db):
    _, startup, h = workspace(db)
    _active(client, db, h)
    sub = db.query(BillingSubscription).filter_by(startup_id=startup.id).one()
    r = post_event(
        client,
        "invoice.payment_failed",
        {
            "amount": 2_400_000,
            "subscription": {"subscription_code": sub.paystack_subscription_code},
            "customer": {"customer_code": sub.paystack_customer_code},
        },
    )
    assert r.json()["data"]["outcome"] == "past_due"
    d = client.get(f"{B}/subscription", headers=h).json()["data"]
    assert d["status"] == "past_due" and d["plan"]["key"] == "growth"
    assert d["payment_issue"]["grace_days_left"] == 7
    assert db.query(Notification).filter(Notification.type == "billing.payment.failed").count() == 1
    assert any(
        i["status"] == "failed" for i in client.get(f"{B}/invoices", headers=h).json()["data"]
    )
    # a second failure does not restart the clock or notify again
    post_event(
        client,
        "invoice.payment_failed",
        {
            "amount": 2_400_000,
            "subscription": {"subscription_code": sub.paystack_subscription_code},
            "x": 2,
        },
    )
    assert db.query(Notification).filter(Notification.type == "billing.payment.failed").count() == 1
    # a successful renewal clears it
    post_event(
        client,
        "charge.success",
        {
            "status": "success",
            "reference": "ren-3",
            "amount": 2_400_000,
            "currency": "NGN",
            "customer": {"customer_code": sub.paystack_customer_code},
            "plan": {"plan_code": "P"},
        },
    )
    d = client.get(f"{B}/subscription", headers=h).json()["data"]
    assert d["status"] == "active" and d["payment_issue"] is None
    # ...and an unpaid grace period runs out
    post_event(
        client,
        "invoice.payment_failed",
        {
            "amount": 1,
            "subscription": {"subscription_code": sub.paystack_subscription_code},
            "x": 3,
        },
    )
    db.refresh(sub)
    sub.grace_ends_at = subs.now() - timedelta(minutes=1)
    db.flush()
    d = client.get(f"{B}/subscription", headers=h).json()["data"]
    assert d["plan"]["key"] == "starter" and d["status"] == "canceled"


def test_payment_failure_for_an_unknown_subscription_is_ignored(client, db):
    workspace(db)
    r = post_event(
        client, "invoice.payment_failed", {"subscription": {"subscription_code": "SUB_none"}}
    )
    assert r.json()["data"]["outcome"] == "ignored_no_subscription"


def test_subscription_lifecycle_events(client, db):
    _, startup, h = workspace(db)
    _active(client, db, h)
    sub = db.query(BillingSubscription).filter_by(startup_id=startup.id).one()
    code = sub.paystack_subscription_code
    out = post_event(
        client,
        "subscription.create",
        {
            "subscription_code": "SUB_other",
            "email_token": "t",
            "customer": {"customer_code": sub.paystack_customer_code},
        },
    ).json()["data"]["outcome"]
    assert out == "ignored_not_current"
    out = post_event(
        client,
        "subscription.create",
        {
            "subscription_code": code,
            "email_token": "fresh",
            "customer": {"customer_code": sub.paystack_customer_code},
        },
    ).json()["data"]["outcome"]
    assert out == "linked"
    db.refresh(sub)
    assert sub.paystack_email_token == "fresh"
    out = post_event(
        client,
        "subscription.create",
        {"subscription_code": "x", "customer": {"customer_code": "CUS_none"}},
    ).json()["data"]["outcome"]
    assert out == "ignored_no_subscription"
    assert (
        post_event(client, "subscription.not_renew", {"subscription_code": code}).json()["data"][
            "outcome"
        ]
        == "cancel_at_period_end"
    )
    db.refresh(sub)
    assert sub.cancel_at_period_end is True
    assert (
        post_event(client, "subscription.not_renew", {"subscription_code": "SUB_none"}).json()[
            "data"
        ]["outcome"]
        == "ignored_no_subscription"
    )
    assert (
        post_event(client, "subscription.disable", {"subscription_code": code}).json()["data"][
            "outcome"
        ]
        == "unlinked"
    )
    db.refresh(sub)
    assert sub.paystack_subscription_code is None
    assert (
        post_event(client, "subscription.disable", {"subscription_code": "SUB_none"}).json()[
            "data"
        ]["outcome"]
        == "ignored_no_subscription"
    )


def test_the_key_is_never_in_a_response(client, db):
    _, _, h = workspace(db)
    r = client.post(
        f"{B}/subscription/checkout", json={"plan": "growth", "interval": "monthly"}, headers=h
    )
    assert KEY not in r.text


def test_a_customer_code_shared_by_two_workspaces_is_never_guessed(client, db):
    from tests.billing_helpers import workspace as ws

    _, a, ha = ws(db)
    _, b, hb = ws(db)
    ra, _ = _pending_checkout(client, ha)
    client.get(f"{B}/subscription/verify", params={"reference": ra}, headers=ha)
    sub_a = db.query(BillingSubscription).filter_by(startup_id=a.id).one()
    db.add(
        BillingSubscription(
            startup_id=b.id,
            plan_key="growth",
            status="active",
            paystack_customer_code=sub_a.paystack_customer_code,
            current_period_end=subs.now() + timedelta(days=5),
        )
    )
    db.flush()
    r = post_event(
        client,
        "charge.success",
        {
            "status": "success",
            "reference": "amb-1",
            "amount": 1,
            "currency": "NGN",
            "customer": {"customer_code": sub_a.paystack_customer_code},
            "plan": {"plan_code": "P"},
        },
    )
    assert r.json()["data"]["outcome"] == "ignored_no_subscription"
    assert db.query(BillingInvoice).filter_by(reference="amb-1").count() == 0


def test_a_retry_that_finally_succeeds_after_grace_reactivates_from_the_plan_code(client, db):
    from app.db.models.billing import BillingPlan

    _, startup, h = workspace(db)
    _active(client, db, h)
    sub = db.query(BillingSubscription).filter_by(startup_id=startup.id).one()
    plan = db.query(BillingPlan).filter_by(key="growth").one()
    plan.paystack_plan_code_monthly = "PLN_growth_m"
    sub.status, sub.plan_key = "canceled", "starter"
    db.flush()
    r = post_event(
        client,
        "charge.success",
        {
            "status": "success",
            "reference": "late-1",
            "amount": 2_400_000,
            "currency": "NGN",
            "customer": {"customer_code": sub.paystack_customer_code},
            "plan": {"plan_code": "PLN_growth_m"},
        },
    )
    assert r.json()["data"]["outcome"] == "renewed"
    db.refresh(sub)
    assert sub.status == "active" and sub.plan_key == "growth"
    r = post_event(
        client,
        "charge.success",
        {
            "status": "success",
            "reference": "late-2",
            "amount": 1,
            "currency": "NGN",
            "customer": {"customer_code": sub.paystack_customer_code},
            "plan": {"plan_code": "PLN_unknown"},
        },
    )
    assert r.json()["data"]["outcome"] in ("renewed", "ignored_no_subscription")


def test_a_renewal_read_after_the_period_end_adds_one_period_not_two(client, db):
    _, startup, h = workspace(db)
    ref, _ = _pending_checkout(client, h, "scale")
    client.get(f"{B}/subscription/verify", params={"reference": ref}, headers=h)
    client.post(
        f"{B}/subscription/change",
        json={"plan": "growth", "interval": "monthly"},
        headers={**h, "Idempotency-Key": "dg"},
    )
    sub = db.query(BillingSubscription).filter_by(startup_id=startup.id).one()
    sub.current_period_end = subs.now() - timedelta(minutes=5)
    db.flush()
    client.get(f"{B}/subscription", headers=h)  # rolls the pending plan in before the webhook lands
    paid = subs.now()
    post_event(
        client,
        "charge.success",
        {
            "status": "success",
            "reference": "ren-once",
            "amount": 2_400_000,
            "currency": "NGN",
            "paid_at": paid.isoformat(),
            "customer": {"customer_code": sub.paystack_customer_code},
            "plan": {"plan_code": "P"},
        },
    )
    db.refresh(sub)
    assert abs((sub.current_period_start - paid).total_seconds()) < 5
    assert (sub.current_period_end - sub.current_period_start).days <= 31
