"""Billing: access, checkout, callback verify, plan changes, cancel, cards, add-ons, invoices."""

from datetime import timedelta

import pytest

from app.core.config import settings
from app.db.models.billing import (
    BillingAddonPurchase,
    BillingInvoice,
    BillingOperation,
    BillingPaymentMethod,
    BillingSubscription,
)
from app.db.models.enums import MembershipRole
from app.db.models.notification import Notification
from app.services.billing import subscription as subs
from app.services.finance.payments import stub_transport
from tests.billing_helpers import workspace

B = "/api/v1/billing"


def _checkout(client, h, plan="growth", interval="monthly", key=None):
    headers = {**h, **({"Idempotency-Key": key} if key else {})}
    return client.post(
        f"{B}/subscription/checkout", json={"plan": plan, "interval": interval}, headers=headers
    )


def _subscribe(client, db, h, plan="growth", interval="monthly"):
    r = _checkout(client, h, plan, interval)
    assert r.status_code == 200, r.text
    ref = r.json()["data"]["reference"]
    v = client.get(f"{B}/subscription/verify", params={"reference": ref}, headers=h)
    assert v.status_code == 200, v.text
    return ref


def test_plans_are_listed_for_any_member(client, db):
    _, _, h = workspace(db, MembershipRole.mentor)
    d = client.get(f"{B}/plans", headers=h).json()["data"]
    assert [p["key"] for p in d["plans"]] == ["starter", "growth", "scale"]
    assert d["plans"][1]["price_monthly_minor"] == 2_400_000
    assert d["plans"][2]["seats"] is None
    assert {a["key"] for a in d["addons"]} == {"credits_2000", "credits_10000"}


@pytest.mark.parametrize(
    "role", [MembershipRole.team_member, MembershipRole.mentor, MembershipRole.accountant]
)
def test_non_founders_get_the_founder_message(client, db, role):
    _, _, h = workspace(db, role)
    for path in ("/subscription", "/usage", "/invoices", "/payment-methods", "/details"):
        r = client.get(f"{B}{path}", headers=h)
        assert r.status_code == 403, path
        assert r.json()["error"]["message"] == "Billing is managed by your workspace founder."
    assert client.post(f"{B}/subscription/cancel", json={}, headers=h).status_code == 403


def test_new_workspace_is_on_starter_with_no_trial_when_enforcement_is_off(client, db):
    _, _, h = workspace(db)
    d = client.get(f"{B}/subscription", headers=h).json()["data"]
    assert d["plan"] == {"key": "starter", "name": "Starter"}
    assert d["status"] == "free" and d["price_minor"] == 0 and d["renews_at"] is None
    assert d["trial_available"] is True and d["enforcement"] is False and d["payment_issue"] is None


def test_checkout_returns_a_link_and_replays_the_same_one(client, db):
    _, startup, h = workspace(db)
    r1 = _checkout(client, h, key="k-1")
    r2 = _checkout(client, h, key="k-1")
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["data"] == r2.json()["data"]
    assert r1.json()["data"]["authorization_url"].startswith("https://checkout.paystack.com/")
    assert db.query(BillingOperation).filter_by(startup_id=startup.id).count() == 1
    other = _checkout(client, h, key="k-2")
    assert other.json()["data"]["reference"] != r1.json()["data"]["reference"]


def test_checkout_validation(client, db):
    _, _, h = workspace(db)
    assert _checkout(client, h, "starter").status_code == 422
    assert _checkout(client, h, "nope").status_code == 404
    assert (
        client.post(
            f"{B}/subscription/checkout", json={"plan": "growth", "interval": "weekly"}, headers=h
        ).status_code
        == 422
    )


def test_callback_verify_activates_the_plan_once(client, db):
    _, startup, h = workspace(db)
    ref = _subscribe(client, db, h)
    d = client.get(f"{B}/subscription", headers=h).json()["data"]
    assert d["plan"]["key"] == "growth" and d["status"] == "active" and d["renews_at"]
    again = client.get(f"{B}/subscription/verify", params={"reference": ref}, headers=h).json()[
        "data"
    ]
    assert again["outcome"] == "already_processed" and again["status"] == "paid"
    inv = client.get(f"{B}/invoices", headers=h).json()["data"]
    assert len(inv) == 1 and inv[0]["amount_minor"] == 2_400_000 and inv[0]["status"] == "paid"
    assert inv[0]["number"].startswith("CFZ-")
    cards = client.get(f"{B}/payment-methods", headers=h).json()["data"]
    assert len(cards) == 1 and cards[0]["last4"] == "4081" and cards[0]["is_default"] is True
    assert "authorization_code" not in cards[0]
    sub = db.query(BillingSubscription).filter_by(startup_id=startup.id).one()
    assert sub.paystack_subscription_code and sub.paystack_email_token
    assert db.query(Notification).filter(Notification.type == "billing.plan.changed").count() == 1
    assert (
        client.get(f"{B}/subscription/verify", params={"reference": ref}, headers=h).status_code
        == 200
    )


def test_abandoned_checkout_stays_free(client, db):
    _, _, h = workspace(db)
    ref = _checkout(client, h).json()["data"]["reference"]
    stub_transport.PS_ABANDONED.add(ref)
    out = client.get(f"{B}/subscription/verify", params={"reference": ref}, headers=h).json()[
        "data"
    ]
    assert out["status"] == "pending" and out["outcome"] == "not_successful"
    assert client.get(f"{B}/subscription", headers=h).json()["data"]["plan"]["key"] == "starter"


def test_verify_of_another_workspaces_reference_is_404(client, db):
    _, _, h1 = workspace(db)
    _, _, h2 = workspace(db)
    ref = _checkout(client, h1).json()["data"]["reference"]
    assert (
        client.get(f"{B}/subscription/verify", params={"reference": ref}, headers=h2).status_code
        == 404
    )
    assert (
        client.get(f"{B}/subscription/verify", params={"reference": "nope"}, headers=h1).status_code
        == 404
    )


def test_already_subscribed_cannot_checkout_again(client, db):
    _, _, h = workspace(db)
    _subscribe(client, db, h)
    r = _checkout(client, h, "scale")
    assert r.status_code == 409 and r.json()["error"]["code"] == "ALREADY_SUBSCRIBED"


def test_preview_and_upgrade_charge_the_prorated_difference(client, db):
    _, startup, h = workspace(db)
    _subscribe(client, db, h)
    sub = db.query(BillingSubscription).filter_by(startup_id=startup.id).one()
    now = subs.now()
    sub.current_period_start, sub.current_period_end = now - timedelta(days=10), now + timedelta(
        days=20
    )
    db.flush()
    p = client.post(
        f"{B}/subscription/preview-change", json={"plan": "scale", "interval": "monthly"}, headers=h
    ).json()["data"]
    assert p["kind"] == "upgrade" and p["effective"] == "now"
    assert 1_550_000 <= p["credit_minor"] <= 1_650_000  # 20/30 of 2,400,000
    assert p["amount_due_minor"] == 6_800_000 - p["credit_minor"]
    r = client.post(
        f"{B}/subscription/change",
        json={"plan": "scale", "interval": "monthly"},
        headers={**h, "Idempotency-Key": "up-1"},
    )
    assert r.status_code == 200 and r.json()["data"]["result"] == "applied"
    assert client.get(f"{B}/subscription", headers=h).json()["data"]["plan"]["key"] == "scale"
    kinds = [i["kind"] for i in client.get(f"{B}/invoices", headers=h).json()["data"]]
    assert "upgrade" in kinds
    replay = client.post(
        f"{B}/subscription/change",
        json={"plan": "scale", "interval": "monthly"},
        headers={**h, "Idempotency-Key": "up-1"},
    )
    assert replay.status_code == 200 and replay.json()["data"]["result"] == "applied"


def test_upgrade_needs_a_card_and_a_declined_card_changes_nothing(client, db):
    _, startup, h = workspace(db)
    _subscribe(client, db, h)
    db.query(BillingPaymentMethod).filter_by(startup_id=startup.id).delete()
    db.flush()
    r = client.post(
        f"{B}/subscription/change",
        json={"plan": "scale", "interval": "monthly"},
        headers={**h, "Idempotency-Key": "auto-1"},
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "NO_PAYMENT_METHOD"
    db.add(
        BillingPaymentMethod(
            startup_id=startup.id,
            authorization_code="AUTH_decline_x",
            signature="s",
            brand="visa",
            last4="0002",
            exp_month="01",
            exp_year="2030",
            is_default=True,
        )
    )
    db.flush()
    r = client.post(
        f"{B}/subscription/change",
        json={"plan": "scale", "interval": "monthly"},
        headers={**h, "Idempotency-Key": "auto-2"},
    )
    assert r.status_code == 402 and r.json()["error"]["code"] == "CHARGE_FAILED"
    assert client.get(f"{B}/subscription", headers=h).json()["data"]["plan"]["key"] == "growth"


def test_downgrade_is_scheduled_and_lands_at_period_end(client, db):
    _, startup, h = workspace(db)
    _subscribe(client, db, h, "scale")
    pre = client.post(
        f"{B}/subscription/preview-change",
        json={"plan": "growth", "interval": "monthly"},
        headers=h,
    ).json()["data"]
    assert (
        pre["kind"] == "downgrade"
        and pre["amount_due_minor"] == 0
        and pre["effective"] == "period_end"
    )
    r = client.post(
        f"{B}/subscription/change",
        json={"plan": "growth", "interval": "monthly"},
        headers={**h, "Idempotency-Key": "auto-3"},
    )
    assert r.json()["data"]["result"] == "scheduled"
    d = client.get(f"{B}/subscription", headers=h).json()["data"]
    assert d["plan"]["key"] == "scale" and d["pending_change"]["plan"]["key"] == "growth"
    sub = db.query(BillingSubscription).filter_by(startup_id=startup.id).one()
    sub.current_period_end = subs.now() - timedelta(minutes=1)
    db.flush()
    d = client.get(f"{B}/subscription", headers=h).json()["data"]
    assert d["plan"]["key"] == "growth" and d["pending_change"] is None and d["status"] == "active"


def test_same_plan_and_free_targets(client, db):
    _, _, h = workspace(db)
    r = client.post(
        f"{B}/subscription/change",
        json={"plan": "growth", "interval": "monthly"},
        headers={**h, "Idempotency-Key": "auto-4"},
    )
    assert r.json()["data"]["result"] == "checkout"
    _subscribe(client, db, h)
    same = client.post(
        f"{B}/subscription/change",
        json={"plan": "growth", "interval": "monthly"},
        headers={**h, "Idempotency-Key": "auto-5"},
    )
    assert same.status_code == 409 and same.json()["error"]["code"] == "NO_CHANGE"
    to_free = client.post(
        f"{B}/subscription/change",
        json={"plan": "starter", "interval": "monthly"},
        headers={**h, "Idempotency-Key": "auto-6"},
    )
    assert to_free.status_code == 200
    assert client.get(f"{B}/subscription", headers=h).json()["data"]["cancel_at_period_end"] is True


def test_monthly_to_annual_is_an_upgrade_with_credit(client, db):
    _, _, h = workspace(db)
    _subscribe(client, db, h)
    p = client.post(
        f"{B}/subscription/preview-change", json={"plan": "growth", "interval": "annual"}, headers=h
    ).json()["data"]
    assert p["kind"] == "upgrade" and p["amount_due_minor"] < 24_000_000


def test_cancel_resume_and_expiry(client, db):
    _, startup, h = workspace(db)
    assert (
        client.post(f"{B}/subscription/cancel", json={}, headers=h).json()["error"]["code"]
        == "NOT_SUBSCRIBED"
    )
    _subscribe(client, db, h)
    c = client.post(
        f"{B}/subscription/cancel", json={"reason": "Too expensive right now"}, headers=h
    )
    assert c.status_code == 200 and c.json()["data"]["cancel_at_period_end"] is True
    d = client.get(f"{B}/subscription", headers=h).json()["data"]
    assert d["cancel_at_period_end"] and d["ends_at"] and d["renews_at"] is None
    assert d["cancel_reason"] == "Too expensive right now" and d["plan"]["key"] == "growth"
    res = client.post(f"{B}/subscription/resume", headers=h)
    assert res.status_code == 200 and res.json()["data"]["cancel_at_period_end"] is False
    assert (
        client.post(f"{B}/subscription/resume", headers=h).json()["error"]["code"] == "NOT_CANCELED"
    )
    client.post(f"{B}/subscription/cancel", json={}, headers=h)
    sub = db.query(BillingSubscription).filter_by(startup_id=startup.id).one()
    sub.current_period_end = subs.now() - timedelta(seconds=5)
    db.flush()
    d = client.get(f"{B}/subscription", headers=h).json()["data"]
    assert d["plan"]["key"] == "starter" and d["status"] == "canceled"


def test_trial_is_once_and_expires(client, db):
    _, startup, h = workspace(db)
    t = client.post(f"{B}/trial", headers=h)
    assert t.status_code == 200 and t.json()["data"]["status"] == "trialing"
    assert (
        t.json()["data"]["plan"]["key"] == "growth" and t.json()["data"]["trial_available"] is False
    )
    again = client.post(f"{B}/trial", headers=h)
    assert again.status_code == 409 and again.json()["error"]["code"] == "TRIAL_UNAVAILABLE"
    sub = db.query(BillingSubscription).filter_by(startup_id=startup.id).one()
    sub.trial_ends_at = subs.now() - timedelta(seconds=1)
    db.flush()
    d = client.get(f"{B}/subscription", headers=h).json()["data"]
    assert d["plan"]["key"] == "starter" and d["status"] == "free"
    assert client.post(f"{B}/trial", headers=h).status_code == 409


def test_enforcement_starts_the_one_trial_for_existing_workspaces(client, db, monkeypatch):
    _, _, h = workspace(db)
    monkeypatch.setattr(settings, "PLAN_ENFORCEMENT", True)
    d = client.get(f"{B}/subscription", headers=h).json()["data"]
    assert d["status"] == "trialing" and d["plan"]["key"] == "growth" and d["enforcement"] is True


def test_cards_default_remove_and_last_card_rule(client, db):
    _, startup, h = workspace(db)
    _subscribe(client, db, h)
    first = client.get(f"{B}/payment-methods", headers=h).json()["data"][0]["id"]
    r = client.delete(f"{B}/payment-methods/{first}", headers=h)
    assert (
        r.status_code == 409
        and r.json()["error"]["message"] == "Add another method before removing this one."
    )
    db.add(
        BillingPaymentMethod(
            startup_id=startup.id,
            authorization_code="AUTH_2",
            signature="s2",
            brand="mastercard",
            last4="1111",
            exp_month="03",
            exp_year="2031",
        )
    )
    db.flush()
    cards = client.get(f"{B}/payment-methods", headers=h).json()["data"]
    second = next(c["id"] for c in cards if c["last4"] == "1111")
    assert client.put(f"{B}/payment-methods/{second}/default", headers=h).status_code == 200
    cards = client.get(f"{B}/payment-methods", headers=h).json()["data"]
    assert cards[0]["id"] == second and cards[0]["is_default"] and not cards[1]["is_default"]
    assert client.delete(f"{B}/payment-methods/{second}", headers=h).status_code == 200
    left = client.get(f"{B}/payment-methods", headers=h).json()["data"]
    assert [c["id"] for c in left] == [first] and left[0]["is_default"] is True
    assert client.delete(f"{B}/payment-methods/{second}", headers=h).status_code == 404
    assert client.put(f"{B}/payment-methods/{second}/default", headers=h).status_code == 404


def test_add_card_checks_with_50_naira_and_refunds(client, db):
    _, startup, h = workspace(db)
    r = client.post(f"{B}/payment-methods", headers=h)
    assert r.status_code == 200
    op = db.query(BillingOperation).filter_by(startup_id=startup.id, kind="card").one()
    assert op.amount_minor == 5000
    out = client.get(
        f"{B}/subscription/verify", params={"reference": op.reference}, headers=h
    ).json()["data"]
    assert out["status"] == "paid"
    assert len(client.get(f"{B}/payment-methods", headers=h).json()["data"]) == 1
    assert client.get(f"{B}/invoices", headers=h).json()["data"] == []  # the check is not a sale


def test_addon_purchase_adds_credits_and_an_invoice(client, db):
    _, startup, h = workspace(db)
    before = client.get(f"{B}/usage", headers=h).json()["data"]["ai_credits"]["limit"]
    r = client.post(f"{B}/addons/credits_2000/purchase", headers={**h, "Idempotency-Key": "a1"})
    assert r.status_code == 200
    ref = r.json()["data"]["reference"]
    client.get(f"{B}/subscription/verify", params={"reference": ref}, headers=h)
    client.get(f"{B}/subscription/verify", params={"reference": ref}, headers=h)
    assert db.query(BillingAddonPurchase).filter_by(startup_id=startup.id).count() == 1
    assert (
        client.get(f"{B}/usage", headers=h).json()["data"]["ai_credits"]["limit"] == before + 2000
    )
    inv = client.get(f"{B}/invoices", headers=h).json()["data"]
    assert inv[0]["kind"] == "addon" and inv[0]["amount_minor"] == 800_000
    assert client.post(f"{B}/addons/nope/purchase", headers=h).status_code == 404
    assert client.get(f"{B}/addons", headers=h).json()["data"]["tokens_per_credit"] == 50


def test_addon_idempotency_key_is_bound_to_its_operation(client, db):
    _, _, h = workspace(db)
    assert (
        client.post(
            f"{B}/addons/credits_2000/purchase", headers={**h, "Idempotency-Key": "z"}
        ).status_code
        == 200
    )
    other = client.post(f"{B}/addons/credits_10000/purchase", headers={**h, "Idempotency-Key": "z"})
    assert other.status_code == 409


def test_invoice_pdf_and_details(client, db):
    _, _, h = workspace(db)
    _subscribe(client, db, h)
    inv_id = client.get(f"{B}/invoices", headers=h).json()["data"][0]["id"]
    put = client.put(
        f"{B}/details",
        json={"company_name": "NovaPay Ltd", "tax_id": "TIN-1", "address": "1 Marina\nLagos"},
        headers=h,
    )
    assert put.status_code == 200
    assert client.get(f"{B}/details", headers=h).json()["data"]["company_name"] == "NovaPay Ltd"
    cleared = client.put(f"{B}/details", json={}, headers=h).json()["data"]
    assert cleared == {"company_name": None, "tax_id": None, "address": None}
    client.put(
        f"{B}/details",
        json={"company_name": "NovaPay Ltd", "address": "1 Marina\nLagos"},
        headers=h,
    )
    pdf = client.get(f"{B}/invoices/{inv_id}/pdf", headers=h)
    assert (
        pdf.status_code == 200
        and pdf.content.startswith(b"%PDF")
        and pdf.headers["content-type"] == "application/pdf"
    )
    assert (
        client.get(f"{B}/invoices/00000000-0000-0000-0000-000000000000/pdf", headers=h).status_code
        == 404
    )


def test_invoices_are_scoped_to_the_workspace(client, db):
    _, _, h1 = workspace(db)
    _, _, h2 = workspace(db)
    _subscribe(client, db, h1)
    assert len(client.get(f"{B}/invoices", headers=h1).json()["data"]) == 1
    assert client.get(f"{B}/invoices", headers=h2).json()["data"] == []
    inv = db.query(BillingInvoice).one()
    assert client.get(f"{B}/invoices/{inv.id}/pdf", headers=h2).status_code == 404


def test_usage_meters_and_the_90_percent_alert_fires_once(client, db):
    from datetime import UTC, datetime

    from app.db.models.llm_usage import LlmUsageDaily

    _, startup, h = workspace(db)
    d = client.get(f"{B}/usage", headers=h).json()["data"]
    assert d["ai_credits"] == {
        "used": 0,
        "limit": 500,
        "percent": 0,
        "from_packs": 0,
        "tokens_per_credit": 50,
    }
    assert d["seats"] == {"used": 1, "limit": 1} and d["storage"]["limit_mb"] == 5120
    assert d["breakdown"] == [] and d["daily"] == []
    db.add(
        LlmUsageDaily(
            startup_id=startup.id, usage_date=datetime.now(UTC).date(), tokens_used=50 * 460
        )
    )
    db.flush()
    d = client.get(f"{B}/usage", headers=h).json()["data"]
    assert d["ai_credits"]["used"] == 460 and d["ai_credits"]["percent"] == 92
    assert d["daily"][0]["credits"] == 460
    client.get(f"{B}/usage", headers=h)
    assert (
        db.query(Notification).filter(Notification.type == "billing.usage.threshold").count() == 1
    )


def test_seats_count_founders_and_team_members_only(client, db):
    from tests.factories import create_membership, create_user

    user, startup, h = workspace(db)
    for role in (MembershipRole.team_member, MembershipRole.mentor, MembershipRole.accountant):
        create_membership(db, create_user(db), startup, role=role)
    db.flush()
    assert client.get(f"{B}/usage", headers=h).json()["data"]["seats"]["used"] == 2


def test_unused_pack_credits_carry_over_and_used_ones_do_not(client, db):
    from datetime import UTC, datetime, timedelta

    from app.db.models.billing import BillingAddonPurchase
    from app.db.models.llm_usage import LlmUsageDaily

    _, startup, h = workspace(db)
    db.add(
        BillingAddonPurchase(
            startup_id=startup.id,
            addon_key="credits_2000",
            credits=2000,
            amount_minor=1,
            reference="p1",
        )
    )
    last_month = datetime.now(UTC).date().replace(day=1) - timedelta(days=3)
    # last month used 800 credits against a 500-credit Starter plan: 300 came out of the pack
    db.add(LlmUsageDaily(startup_id=startup.id, usage_date=last_month, tokens_used=50 * 800))
    db.flush()
    d = client.get(f"{B}/usage", headers=h).json()["data"]["ai_credits"]
    assert d["from_packs"] == 1700 and d["limit"] == 2200 and d["used"] == 0


def test_change_requires_an_idempotency_key(client, db):
    _, _, h = workspace(db)
    r = client.post(
        f"{B}/subscription/change", json={"plan": "growth", "interval": "monthly"}, headers=h
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_upgrade_restarts_the_period_so_a_big_credit_is_not_free_time(client, db):
    _, startup, h = workspace(db)
    _subscribe(client, db, h, "growth", "annual")
    r = client.post(
        f"{B}/subscription/change",
        json={"plan": "scale", "interval": "monthly"},
        headers={**h, "Idempotency-Key": "ann-to-m"},
    )
    assert r.status_code == 200 and r.json()["data"]["amount_charged_minor"] == 0
    sub = db.query(BillingSubscription).filter_by(startup_id=startup.id).one()
    assert sub.plan_key == "scale" and sub.interval == "monthly"
    assert (sub.current_period_end - sub.current_period_start).days <= 31  # one month, not ~a year


def test_a_pending_charge_is_kept_for_the_webhook_and_a_decline_is_recorded(client, db):
    _, startup, h = workspace(db)
    _subscribe(client, db, h)
    pm = db.query(BillingPaymentMethod).filter_by(startup_id=startup.id).one()
    pm.authorization_code = "AUTH_decline_1"
    db.flush()
    r = client.post(
        f"{B}/subscription/change",
        json={"plan": "scale", "interval": "monthly"},
        headers={**h, "Idempotency-Key": "dec-1"},
    )
    assert r.status_code == 402
    op = db.query(BillingOperation).filter_by(startup_id=startup.id, kind="upgrade").one()
    assert op.status == "failed"  # survived the error response: it was committed before the charge


def test_removing_the_last_card_is_blocked_even_while_canceling_and_resume_needs_one(client, db):
    _, startup, h = workspace(db)
    _subscribe(client, db, h)
    client.post(f"{B}/subscription/cancel", json={}, headers=h)
    card = client.get(f"{B}/payment-methods", headers=h).json()["data"][0]["id"]
    assert client.delete(f"{B}/payment-methods/{card}", headers=h).status_code == 409
    db.query(BillingPaymentMethod).filter_by(startup_id=startup.id).delete()
    db.flush()
    r = client.post(f"{B}/subscription/resume", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "NO_PAYMENT_METHOD"


def test_an_active_plan_with_no_renewal_lapses_after_the_grace_window(client, db):
    _, startup, h = workspace(db)
    _subscribe(client, db, h)
    sub = db.query(BillingSubscription).filter_by(startup_id=startup.id).one()
    sub.current_period_end = subs.now() - timedelta(days=settings.BILLING_GRACE_DAYS, minutes=1)
    db.flush()
    d = client.get(f"{B}/subscription", headers=h).json()["data"]
    assert d["plan"]["key"] == "starter" and d["status"] == "canceled"


def test_a_second_paid_checkout_is_refunded_not_double_subscribed(client, db):
    _, startup, h = workspace(db)
    first = _checkout(client, h, key="c1").json()["data"]["reference"]
    second = _checkout(client, h, key="c2").json()["data"]["reference"]
    client.get(f"{B}/subscription/verify", params={"reference": first}, headers=h)
    out = client.get(f"{B}/subscription/verify", params={"reference": second}, headers=h).json()[
        "data"
    ]
    assert out["outcome"] == "duplicate_payment_refunded"
    inv = {i["status"] for i in client.get(f"{B}/invoices", headers=h).json()["data"]}
    assert inv == {"paid", "refunded"}
    assert (
        len(stub_transport.PS_SUBS) == 1
        and sum(len(v) for v in stub_transport.PS_SUBS.values()) == 1
    )


def test_trial_is_not_restarted_for_a_workspace_that_canceled(client, db, monkeypatch):
    _, startup, h = workspace(db)
    _subscribe(client, db, h)
    client.post(f"{B}/subscription/cancel", json={}, headers=h)
    sub = db.query(BillingSubscription).filter_by(startup_id=startup.id).one()
    sub.current_period_end = subs.now() - timedelta(seconds=1)
    db.flush()
    monkeypatch.setattr(settings, "PLAN_ENFORCEMENT", True)
    d = client.get(f"{B}/subscription", headers=h).json()["data"]
    assert d["status"] == "canceled" and d["plan"]["key"] == "starter"


def test_a_second_upgrade_is_refused_while_one_is_in_flight(client, db):
    _, startup, h = workspace(db)
    _subscribe(client, db, h)
    db.add(
        BillingOperation(
            startup_id=startup.id,
            reference="cfz_upgrade_inflight",
            kind="upgrade",
            amount_minor=1,
            params={"plan_key": "scale", "interval": "monthly"},
        )
    )
    db.flush()
    r = client.post(
        f"{B}/subscription/change",
        json={"plan": "scale", "interval": "monthly"},
        headers={**h, "Idempotency-Key": "second"},
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "UPGRADE_IN_PROGRESS"
