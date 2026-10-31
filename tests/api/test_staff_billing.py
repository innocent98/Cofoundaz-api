"""Admin slice C: Super Admin billing overrides (refund, credit, extend trial, comp)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.redis import get_redis
from app.db.models.audit import AuditLog
from app.db.models.billing import BillingInvoice, BillingOverride, BillingPlan, BillingSubscription
from app.db.models.enums import StaffRole
from app.services.billing import subscription as subs
from tests.api.test_staff_users import hdr, mk_staff
from tests.billing_helpers import _paystack_stub, seed_plans, workspace  # noqa: F401

S = "/api/v1/super-admin"
B = "/api/v1/billing"
REASON = "Goodwill after a failed renewal"


@pytest.fixture(autouse=True)
def _mfa_key(monkeypatch):
    monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())


@pytest.fixture(autouse=True)
def _clean_redis():
    r = get_redis()
    for pat in ("staff_stepup_*", "staff_user_email:*"):
        for k in r.scan_iter(pat):
            r.delete(k)
    yield


def sa(db):
    return mk_staff(db, StaffRole.super_admin)


def act(client, staff, ws, action, **body):
    body.setdefault("request_id", str(uuid.uuid4()))
    r = get_redis()
    for k in r.scan_iter("staff_stepup_used:*"):
        r.delete(k)
    import pyotp

    payload = {"reason": REASON, "totp_code": pyotp.TOTP(staff.test_secret).now(), **body}
    return client.post(f"{S}/billing/{ws.id}/{action}", json=payload, headers=hdr(staff))


def invoice(db, ws, amount=2_400_000, status="paid", n=1, kind="subscription"):
    inv = BillingInvoice(
        startup_id=ws.id,
        number=f"CFZ-2026-{n:04d}",
        kind=kind,
        description="Growth plan, monthly",
        amount_minor=amount,
        status=status,
        reference=f"ref-{uuid.uuid4()}",
        plan_key="growth",
        paid_at=datetime.now(UTC),
    )
    db.add(inv)
    db.flush()
    return inv


# ---------------------------------------------------------------- access


ACTIONS = ["refund", "credit", "extend-trial", "comp"]


@pytest.mark.parametrize("action", ACTIONS)
def test_only_a_super_admin_can_reach_billing_overrides(client, db, action):
    _, ws, founder = workspace(db)
    unknown = client.get("/api/v1/definitely-not-a-route").json()
    admin = mk_staff(db, StaffRole.admin, scopes=("support", "content", "moderation"))
    for headers in ({}, founder, hdr(admin)):
        r = client.post(f"{S}/billing/{ws.id}/{action}", json={}, headers=headers)
        assert r.status_code == 404 and r.json() == unknown
        assert client.get(f"{S}/billing/{ws.id}", headers=headers).status_code == 404


@pytest.mark.parametrize(
    ("action", "extra"),
    [
        ("refund", {"invoice_id": str(uuid.uuid4()), "amount_minor": 100}),
        ("credit", {"credits": 10}),
        ("extend-trial", {"days": 3}),
        ("comp", {"plan_key": "growth", "days": 30}),
    ],
)
def test_every_override_needs_a_reason_a_fresh_code_and_a_request_id(client, db, action, extra):
    _, ws, _ = workspace(db)
    staff = sa(db)
    short = act(client, staff, ws, action, reason="too short", **extra)
    assert short.status_code == 422
    wrong = client.post(
        f"{S}/billing/{ws.id}/{action}",
        json={"request_id": str(uuid.uuid4()), "reason": REASON, "totp_code": "000000", **extra},
        headers=hdr(staff),
    )
    assert wrong.status_code == 403 and wrong.json()["error"]["code"] == "STEP_UP_INVALID"
    nokey = client.post(
        f"{S}/billing/{ws.id}/{action}",
        json={"reason": REASON, "totp_code": "123456", **extra},
        headers=hdr(staff),
    )
    assert nokey.status_code == 422
    assert db.query(BillingOverride).count() == 0
    assert db.query(AuditLog).filter(AuditLog.action.like("billing.%")).count() == 0


def test_unknown_workspace_is_404(client, db):
    staff = sa(db)
    r = client.get(f"{S}/billing/{uuid.uuid4()}", headers=hdr(staff))
    assert r.status_code == 404 and r.json()["error"]["code"] == "WORKSPACE_NOT_FOUND"


# ---------------------------------------------------------------- refund


def test_partial_then_full_refund_updates_the_founders_invoice_list(client, db):
    _, ws, founder = workspace(db)
    staff = sa(db)
    inv = invoice(db, ws, amount=2_400_000)
    r = act(client, staff, ws, "refund", invoice_id=str(inv.id), amount_minor=1_000_000)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["replayed"] is False
    assert d["override"]["provider_refund"] == "pending"
    assert d["invoice"]["refunded_minor"] == 1_000_000
    assert d["invoice"]["refundable_minor"] == 1_400_000 and d["invoice"]["status"] == "paid"
    listed = client.get(f"{B}/invoices", headers=founder).json()["data"][0]
    assert listed["refunded_minor"] == 1_000_000 and listed["status"] == "paid"
    r2 = act(client, staff, ws, "refund", invoice_id=str(inv.id), amount_minor=1_400_000)
    assert r2.status_code == 200
    listed = client.get(f"{B}/invoices", headers=founder).json()["data"][0]
    assert listed["status"] == "refunded" and listed["refunded_minor"] == 2_400_000
    pdf = client.get(f"{B}/invoices/{inv.id}/pdf", headers=founder)
    assert pdf.status_code == 200


def test_cannot_refund_more_than_the_invoice(client, db):
    _, ws, _ = workspace(db)
    staff = sa(db)
    inv = invoice(db, ws, amount=500_000)
    assert (
        act(client, staff, ws, "refund", invoice_id=str(inv.id), amount_minor=300_000).status_code
        == 200
    )
    r = act(client, staff, ws, "refund", invoice_id=str(inv.id), amount_minor=200_001)
    assert r.status_code == 422 and r.json()["error"]["code"] == "REFUND_EXCEEDS_INVOICE"
    db.refresh(inv)
    assert inv.refunded_minor == 300_000
    assert db.query(BillingOverride).filter_by(kind="refund").count() == 1


def test_refund_rules_on_invoice_state_and_ownership(client, db):
    _, ws, _ = workspace(db)
    _, other, _ = workspace(db)
    staff = sa(db)
    failed = invoice(db, ws, status="failed", n=1)
    done = invoice(db, ws, status="refunded", n=2)
    foreign = invoice(db, other, n=3)
    for inv, code, status in (
        (failed, "INVOICE_NOT_REFUNDABLE", 409),
        (done, "INVOICE_NOT_REFUNDABLE", 409),
        (foreign, "INVOICE_NOT_FOUND", 404),
    ):
        r = act(client, staff, ws, "refund", invoice_id=str(inv.id), amount_minor=100)
        assert r.status_code == status and r.json()["error"]["code"] == code
    zero = act(client, staff, ws, "refund", invoice_id=str(failed.id), amount_minor=0)
    assert zero.status_code == 422


def test_refund_replay_applies_once(client, db):
    _, ws, _ = workspace(db)
    staff = sa(db)
    inv = invoice(db, ws, amount=1_000_000)
    rid = str(uuid.uuid4())
    first = act(
        client, staff, ws, "refund", request_id=rid, invoice_id=str(inv.id), amount_minor=600_000
    )
    again = act(
        client, staff, ws, "refund", request_id=rid, invoice_id=str(inv.id), amount_minor=600_000
    )
    assert first.status_code == again.status_code == 200
    assert again.json()["data"]["replayed"] is True
    assert again.json()["data"]["override"]["id"] == first.json()["data"]["override"]["id"]
    db.refresh(inv)
    assert inv.refunded_minor == 600_000
    assert db.query(AuditLog).filter_by(action="billing.refund.issued").count() == 1


def test_request_id_cannot_be_reused_for_something_else(client, db):
    _, ws, _ = workspace(db)
    _, other, _ = workspace(db)
    staff = sa(db)
    rid = str(uuid.uuid4())
    assert act(client, staff, ws, "credit", request_id=rid, credits=5).status_code == 200
    for target, action, body in (
        (ws, "extend-trial", {"days": 3}),
        (other, "credit", {"credits": 5}),
    ):
        r = act(client, staff, target, action, request_id=rid, **body)
        assert r.status_code == 409 and r.json()["error"]["code"] == "REQUEST_ID_REUSED"


def test_refund_writes_an_audit_row_with_reason_and_amounts(client, db):
    _, ws, _ = workspace(db)
    staff = sa(db)
    inv = invoice(db, ws)
    act(client, staff, ws, "refund", invoice_id=str(inv.id), amount_minor=250_000)
    row = db.query(AuditLog).filter_by(action="billing.refund.issued").one()
    assert row.actor_user_id == staff.id and row.startup_id == ws.id
    assert row.reason == REASON
    assert row.details["amount_minor"] == 250_000 and row.details["invoice_id"] == str(inv.id)


# ---------------------------------------------------------------- credit


def test_credit_raises_the_founders_ai_credit_limit_once(client, db):
    _, ws, founder = workspace(db)
    staff = sa(db)
    before = client.get(f"{B}/usage", headers=founder).json()["data"]["ai_credits"]
    rid = str(uuid.uuid4())
    r = act(client, staff, ws, "credit", request_id=rid, credits=500)
    assert r.status_code == 200 and r.json()["data"]["override"]["credits"] == 500
    act(client, staff, ws, "credit", request_id=rid, credits=500)  # replay
    after = client.get(f"{B}/usage", headers=founder).json()["data"]["ai_credits"]
    assert after["limit"] == before["limit"] + 500 and after["from_packs"] == 500
    overview = client.get(f"{S}/billing/{ws.id}", headers=hdr(staff)).json()["data"]
    assert overview["credits_granted_total"] == 500
    assert [o["kind"] for o in overview["overrides"]] == ["credit"]


def test_credit_bounds(client, db):
    _, ws, _ = workspace(db)
    staff = sa(db)
    for credits in (0, -5, 100_001):
        assert act(client, staff, ws, "credit", credits=credits).status_code == 422


# ---------------------------------------------------------------- extend trial


def _trialing(db, ws, days_left=3, started_days_ago=11):
    t = datetime.now(UTC)
    sub = subs.get_or_create(db, ws.id)
    sub.status, sub.plan_key = "trialing", "growth"
    sub.trial_used_at = t - timedelta(days=started_days_ago)
    sub.trial_ends_at = sub.current_period_end = t + timedelta(days=days_left)
    sub.current_period_start = sub.trial_used_at
    db.flush()
    return sub


def test_extend_trial_moves_the_end_date_the_founder_sees(client, db):
    _, ws, founder = workspace(db)
    staff = sa(db)
    sub = _trialing(db, ws)
    old_end = sub.trial_ends_at
    r = act(client, staff, ws, "extend-trial", days=7)
    assert r.status_code == 200, r.text
    got = client.get(f"{B}/subscription", headers=founder).json()["data"]
    assert got["status"] == "trialing"
    assert datetime.fromisoformat(got["trial_ends_at"]) == old_end + timedelta(days=7)
    assert got["ends_at"] == got["trial_ends_at"]
    assert r.json()["data"]["subscription"]["trial_ends_at"] == got["trial_ends_at"]


def test_extend_trial_replay_adds_days_once(client, db):
    _, ws, _ = workspace(db)
    staff = sa(db)
    sub = _trialing(db, ws)
    old_end = sub.trial_ends_at
    rid = str(uuid.uuid4())
    act(client, staff, ws, "extend-trial", request_id=rid, days=5)
    again = act(client, staff, ws, "extend-trial", request_id=rid, days=5)
    assert again.json()["data"]["replayed"] is True
    db.refresh(sub)
    assert sub.trial_ends_at == old_end + timedelta(days=5)


def test_extend_trial_refusals(client, db):
    _, ws, _ = workspace(db)
    staff = sa(db)
    r = act(client, staff, ws, "extend-trial", days=3)  # free workspace
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_TRIALING"
    _trialing(db, ws, days_left=3, started_days_ago=11)  # 14 days in, 90 max
    assert act(client, staff, ws, "extend-trial", days=61).status_code == 422
    assert act(client, staff, ws, "extend-trial", days=60).status_code == 200  # ends day 74
    r = act(client, staff, ws, "extend-trial", days=20)  # would pass day 90
    assert r.status_code == 422 and r.json()["error"]["code"] == "TRIAL_TOO_LONG"


def test_a_lapsed_trial_cannot_be_extended(client, db):
    _, ws, _ = workspace(db)
    staff = sa(db)
    _trialing(db, ws, days_left=-1)
    r = act(client, staff, ws, "extend-trial", days=3)
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_TRIALING"


# ---------------------------------------------------------------- comp


def test_comp_gives_the_founder_the_plan_until_it_lapses(client, db):
    _, ws, founder = workspace(db)
    staff = sa(db)
    r = act(client, staff, ws, "comp", plan_key="growth", days=30)
    assert r.status_code == 200, r.text
    got = client.get(f"{B}/subscription", headers=founder).json()["data"]
    assert got["plan"]["key"] == "growth" and got["status"] == "active"
    assert got["comped"] is True and got["renews_at"] is None and got["ends_at"]
    # time passes: it lapses to Starter with no payment and no Paystack call
    sub = db.query(BillingSubscription).filter_by(startup_id=ws.id).one()
    sub.current_period_end = datetime.now(UTC) - timedelta(minutes=1)
    db.flush()
    got = client.get(f"{B}/subscription", headers=founder).json()["data"]
    assert got["plan"]["key"] == "starter" and got["status"] == "canceled"
    assert got["comped"] is False


def test_comp_replay_and_recomp(client, db):
    _, ws, _ = workspace(db)
    staff = sa(db)
    rid = str(uuid.uuid4())
    assert (
        act(client, staff, ws, "comp", request_id=rid, plan_key="growth", days=30).status_code
        == 200
    )
    assert act(client, staff, ws, "comp", request_id=rid, plan_key="growth", days=30).json()[
        "data"
    ]["replayed"]
    again = act(client, staff, ws, "comp", plan_key="scale", days=10)  # a second comp replaces
    assert again.status_code == 200
    assert again.json()["data"]["subscription"]["plan"]["key"] == "scale"
    assert db.query(BillingOverride).filter_by(kind="comp").count() == 2


def test_comp_refuses_starter_unknown_plan_and_a_paying_workspace(client, db):
    _, ws, _ = workspace(db)
    staff = sa(db)
    assert (
        act(client, staff, ws, "comp", plan_key="starter", days=30).json()["error"]["code"]
        == "PLAN_NOT_COMPABLE"
    )
    assert act(client, staff, ws, "comp", plan_key="nope", days=30).status_code == 404
    assert act(client, staff, ws, "comp", plan_key="growth", days=0).status_code == 422
    assert act(client, staff, ws, "comp", plan_key="growth", days=366).status_code == 422
    sub = subs.get_or_create(db, ws.id)
    sub.status, sub.plan_key = "active", "growth"
    sub.current_period_start = datetime.now(UTC)
    sub.current_period_end = datetime.now(UTC) + timedelta(days=20)
    sub.paystack_subscription_code = "SUB_x"
    db.flush()
    r = act(client, staff, ws, "comp", plan_key="scale", days=30)
    assert r.status_code == 409 and r.json()["error"]["code"] == "PAID_SUBSCRIPTION"
    db.refresh(sub)
    assert sub.plan_key == "growth" and sub.comped is False


def test_comp_over_a_trial_ends_the_trial_clock(client, db):
    _, ws, founder = workspace(db)
    staff = sa(db)
    _trialing(db, ws)
    assert act(client, staff, ws, "comp", plan_key="growth", days=60).status_code == 200
    got = client.get(f"{B}/subscription", headers=founder).json()["data"]
    assert got["status"] == "active" and got["trial_ends_at"] is None and got["comped"]


def test_a_comped_workspace_cannot_cancel_resume_or_change_plan_but_can_buy(client, db):
    _, ws, founder = workspace(db)
    staff = sa(db)
    act(client, staff, ws, "comp", plan_key="growth", days=30)
    idem = {**founder, "Idempotency-Key": "k-1"}
    for path, body in (
        ("/subscription/cancel", {}),
        ("/subscription/resume", None),
        ("/subscription/change", {"plan": "scale", "interval": "monthly"}),
    ):
        r = client.post(f"{B}{path}", json=body, headers=idem)
        assert r.status_code == 409 and r.json()["error"]["code"] == "PLAN_COMPLIMENTARY", path
    buy = client.post(
        f"{B}/subscription/checkout",
        json={"plan": "scale", "interval": "monthly"},
        headers=idem,
    )
    assert buy.status_code == 200, buy.text


def test_comped_workspaces_are_not_revenue(client, db):
    _, ws, _ = workspace(db)
    _, paying, _ = workspace(db)
    staff = sa(db)
    growth = db.query(BillingPlan).filter_by(key="growth").one()
    sub = subs.get_or_create(db, paying.id)
    sub.status, sub.plan_key = "active", "growth"
    sub.current_period_start = datetime.now(UTC)
    sub.current_period_end = datetime.now(UTC) + timedelta(days=20)
    db.flush()
    act(client, staff, ws, "comp", plan_key="scale", days=30)
    k = client.get("/api/v1/admin/dashboard/kpis", headers=hdr(staff)).json()["data"]
    assert k["paid_conversion"]["paying_workspaces"] == 1
    assert k["mrr"]["minor"] == growth.price_monthly_minor


def test_overview_lists_everything(client, db):
    _, ws, _ = workspace(db)
    staff = sa(db)
    inv = invoice(db, ws)
    act(client, staff, ws, "credit", credits=10)
    act(client, staff, ws, "refund", invoice_id=str(inv.id), amount_minor=100)
    d = client.get(f"{S}/billing/{ws.id}", headers=hdr(staff)).json()["data"]
    # newest first in production; both rows share one transaction timestamp here
    assert {o["kind"] for o in d["overrides"]} == {"refund", "credit"}
    assert {o["actor_email"] for o in d["overrides"]} == {staff.email}
    assert d["invoices"][0]["refundable_minor"] == inv.amount_minor - 100
    assert d["workspace"]["id"] == str(ws.id)


def test_a_replay_with_different_values_is_refused(client, db):
    _, ws, _ = workspace(db)
    staff = sa(db)
    inv = invoice(db, ws, amount=1_000_000)
    rid = str(uuid.uuid4())
    assert (
        act(
            client, staff, ws, "refund", request_id=rid, invoice_id=str(inv.id), amount_minor=100
        ).status_code
        == 200
    )
    r = act(client, staff, ws, "refund", request_id=rid, invoice_id=str(inv.id), amount_minor=999)
    assert r.status_code == 409 and r.json()["error"]["code"] == "REQUEST_ID_REUSED"
    db.refresh(inv)
    assert inv.refunded_minor == 100


def test_buying_a_plan_while_comped_activates_it_instead_of_refunding(client, db):
    from tests.billing_helpers import post_event

    user, ws, founder = workspace(db)
    staff = sa(db)
    act(client, staff, ws, "comp", plan_key="growth", days=30)
    buy = client.post(
        f"{B}/subscription/checkout",
        json={"plan": "scale", "interval": "monthly"},
        headers={**founder, "Idempotency-Key": "k-buy"},
    ).json()["data"]
    scale = db.query(BillingPlan).filter_by(key="scale").one()
    r = post_event(
        client,
        "charge.success",
        {
            "id": 1, "status": "success", "reference": buy["reference"],
            "amount": scale.price_monthly_minor, "currency": "NGN",
            "paid_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "customer": {"email": user.email, "customer_code": "CUS_comp_1"},
        },
    )  # fmt: skip
    assert r.status_code == 200, r.text
    got = client.get(f"{B}/subscription", headers=founder).json()["data"]
    assert got["plan"]["key"] == "scale" and got["comped"] is False and got["status"] == "active"
    inv = client.get(f"{B}/invoices", headers=founder).json()["data"][0]
    assert inv["status"] == "paid"


def test_old_paystack_events_do_not_touch_a_comped_workspace(client, db):
    from tests.billing_helpers import post_event

    user, ws, founder = workspace(db)
    staff = sa(db)
    sub = subs.get_or_create(db, ws.id)
    sub.status, sub.paystack_customer_code = "canceled", "CUS_old_1"
    db.flush()
    act(client, staff, ws, "comp", plan_key="scale", days=30)
    cust = {"customer_code": "CUS_old_1", "email": user.email}
    post_event(client, "invoice.payment_failed", {"customer": cust, "subscription": {}})
    post_event(
        client,
        "subscription.create",
        {"subscription_code": "SUB_late", "email_token": "t", "customer": cust, "plan": {}},
    )
    post_event(
        client,
        "charge.success",
        {
            "id": 2, "status": "success", "reference": "renewal-late-1", "amount": 1,
            "currency": "NGN", "customer": cust, "plan": {"plan_code": "PLN_x"},
        },
    )  # fmt: skip
    got = client.get(f"{B}/subscription", headers=founder).json()["data"]
    assert got["plan"]["key"] == "scale" and got["status"] == "active" and got["comped"] is True
    assert got["payment_issue"] is None
    db.expire_all()
    row = db.query(BillingSubscription).filter_by(startup_id=ws.id).one()
    assert row.paystack_subscription_code is None


def test_a_long_comp_meters_credits_by_calendar_month(client, db):
    _, ws, founder = workspace(db)
    staff = sa(db)
    act(client, staff, ws, "comp", plan_key="growth", days=365)
    c = client.get(f"{B}/usage", headers=founder).json()["data"]["cycle"]
    start = datetime.fromisoformat(c["starts_on"])
    assert start.day == 1 and (datetime.fromisoformat(c["resets_on"]) - start).days <= 31


def test_staff_user_and_workspace_reads_flag_comped_workspaces(client, db):
    _, ws, _ = workspace(db)
    staff = sa(db)
    act(client, staff, ws, "comp", plan_key="growth", days=30)
    rows = client.get(
        "/api/v1/admin/workspaces", params={"query": ws.name or ""}, headers=hdr(staff)
    )
    mine = [w for w in rows.json()["data"]["workspaces"] if w["id"] == str(ws.id)]
    assert mine and mine[0]["comped"] is True and mine[0]["billing_status"] == "active"
