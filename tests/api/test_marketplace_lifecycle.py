"""Marketplace slice C after payment: the expert's accept / decline, the timers (hold, 48-hour
acceptance, refund retries), refunds and their ledger, receipts, and the read APIs."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db.models.enums import (
    BookingStatus,
    LedgerKind,
    MembershipRole,
    OrderStatus,
    ProviderStatus,
)
from app.db.models.marketplace import (
    MarketplaceBooking,
    MarketplaceLedgerEntry,
    MarketplaceOrder,
    MarketplaceWebhookEvent,
)
from app.services.marketplace import orders, timers
from tests.api.test_staff_users import hdr, member, mk_user
from tests.marketplace_booking_helpers import (
    M,
    P,
    World,
    charge_success,
    install_fake_paystack,
    install_mailbox,
    post_webhook,
)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())


@pytest.fixture()
def ps(monkeypatch):
    return install_fake_paystack(monkeypatch)


@pytest.fixture()
def mail(monkeypatch):
    return install_mailbox(monkeypatch)


@pytest.fixture()
def w(client, db, ps, mail):
    return World(client, db)


def paid(w, ps, **kw):
    r = w.book(**kw)
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    ps.pay(d["order"]["reference"])
    hook = post_webhook(w.client, charge_success(d["order"]["reference"]))
    assert hook.status_code == 200, hook.text
    return d["id"], d["order"]["reference"]


def order_of(db, bid) -> MarketplaceOrder:
    return db.query(MarketplaceOrder).filter_by(booking_id=uuid.UUID(bid)).one()


def row(db, bid) -> MarketplaceBooking:
    return db.get(MarketplaceBooking, uuid.UUID(bid))


def entries(db, order):
    return sorted(
        (e.kind.value, e.amount_minor)
        for e in db.query(MarketplaceLedgerEntry).filter_by(order_id=order.id).all()
    )


# ---------------------------------------------------------------- the expert's inbox


def test_the_expert_sees_the_brief_and_their_earnings_but_not_the_workspace(client, db, w, ps):
    bid, _ = paid(w, ps)
    inbox = client.get(f"{P}/bookings", headers=hdr(w.expert)).json()["data"]
    assert [b["id"] for b in inbox["bookings"]] == [bid]
    b = inbox["bookings"][0]
    assert b["status"] == "awaiting_provider" and b["brief"].startswith("We need a review")
    assert b["earnings"] == {
        "price_minor": 1_000_000,
        "commission_bps": 1500,
        "commission_minor": 150_000,
        "net_minor": 850_000,
        "currency": "NGN",
        "paid": True,
    }
    assert b["meeting_url"] is None  # shown only once accepted
    text = str(b)
    assert str(w.ws.id) not in text and "Acme" not in text and str(w.founder.id) not in text
    assert inbox["pagination"]["total"] == 1


def test_an_unpaid_checkout_is_not_in_the_experts_inbox(client, db, w):
    w.book()
    assert client.get(f"{P}/bookings", headers=hdr(w.expert)).json()["data"]["bookings"] == []


def test_another_expert_cannot_see_or_act_on_a_booking(client, db, w, ps):
    bid, _ = paid(w, ps)
    from tests.api.test_marketplace_listings import mk_provider

    other, _prov = mk_provider(db, name="Other Expert")
    assert client.get(f"{P}/bookings/{bid}", headers=hdr(other)).status_code == 404
    assert client.post(f"{P}/bookings/{bid}/accept", headers=hdr(other)).status_code == 404
    assert client.post(f"{P}/bookings/{bid}/decline", headers=hdr(other)).status_code == 404
    assert client.get(f"{P}/bookings", headers=hdr(other)).json()["data"]["bookings"] == []


def test_provider_booking_routes_need_an_approved_expert(client, db, w, ps):
    bid, _ = paid(w, ps)
    anon = client.get(f"{P}/bookings")
    assert anon.status_code == 401
    founder_try = client.get(f"{P}/bookings", headers=hdr(w.founder))
    assert (
        founder_try.status_code == 403 and founder_try.json()["error"]["code"] == "NOT_A_PROVIDER"
    )
    w.provider.status = ProviderStatus.suspended
    db.flush()
    for method, path in (
        ("get", ""),
        ("get", f"/{bid}"),
        ("post", f"/{bid}/accept"),
        ("post", f"/{bid}/decline"),
    ):
        r = getattr(client, method)(f"{P}/bookings{path}", headers=hdr(w.expert))
        assert r.status_code == 403 and r.json()["error"]["code"] == "PROVIDER_NOT_APPROVED"


def test_tab_and_status_filters(client, db, w, ps):
    a, _ = paid(w, ps, slot=w.slots()[0])
    b, _ = paid(w, ps, listing=w.package, who=w.other, ws=w.other_ws)
    client.post(f"{P}/bookings/{b}/accept", headers=hdr(w.expert))
    h = hdr(w.expert)
    upcoming = client.get(f"{P}/bookings?tab=upcoming", headers=h).json()["data"]["bookings"]
    progress = client.get(f"{P}/bookings?tab=in_progress", headers=h).json()["data"]["bookings"]
    assert [x["id"] for x in upcoming] == [a] and [x["id"] for x in progress] == [b]
    assert (
        client.get(f"{P}/bookings?status=in_progress", headers=h).json()["data"]["pagination"][
            "total"
        ]
        == 1
    )
    assert client.get(f"{P}/bookings?tab=bogus", headers=h).status_code == 422
    assert client.get(f"{P}/bookings?status=bogus", headers=h).status_code == 422


# ---------------------------------------------------------------- accept


def test_accepting_a_consultation_confirms_it_and_reveals_the_meeting_link(client, db, w, ps):
    bid, _ = paid(w, ps)
    r = client.post(f"{P}/bookings/{bid}/accept", headers=hdr(w.expert))
    assert r.status_code == 200 and r.json()["data"]["status"] == "confirmed"
    assert r.json()["data"]["meeting_url"] == "https://meet.example.com/ngozi"
    mine = client.get(f"{M}/bookings/{bid}", headers=w.h()).json()["data"]
    assert mine["status"] == "confirmed" and mine["meeting_url"] == "https://meet.example.com/ngozi"
    assert row(db, bid).accepted_at is not None
    again = client.post(f"{P}/bookings/{bid}/accept", headers=hdr(w.expert))
    assert again.status_code == 409 and again.json()["error"]["code"] == "INVALID_STATE"


def test_accepting_a_package_starts_the_delivery_clock(client, db, w, ps):
    bid, _ = paid(w, ps, listing=w.package)
    before = datetime.now(UTC)
    r = client.post(f"{P}/bookings/{bid}/accept", headers=hdr(w.expert))
    assert r.json()["data"]["status"] == "in_progress"
    due = datetime.fromisoformat(r.json()["data"]["due_at"])
    assert timedelta(days=5) - timedelta(minutes=1) < due - before < timedelta(days=5, minutes=1)


def test_the_snapshot_meeting_link_survives_a_listing_edit(client, db, w, ps):
    bid, _ = paid(w, ps)
    w.consult.meeting_url = "https://meet.example.com/changed"
    db.flush()
    client.post(f"{P}/bookings/{bid}/accept", headers=hdr(w.expert))
    got = client.get(f"{M}/bookings/{bid}", headers=w.h()).json()["data"]
    assert got["meeting_url"] == "https://meet.example.com/ngozi"


def test_accepting_after_the_window_or_the_slot_is_refused(client, db, w, ps):
    a, _ = paid(w, ps, slot=w.slots()[0])
    row(db, a).accept_by = datetime.now(UTC) - timedelta(seconds=1)
    db.flush()
    r = client.post(f"{P}/bookings/{a}/accept", headers=hdr(w.expert))
    assert r.status_code == 409 and r.json()["error"]["code"] == "ACCEPT_WINDOW_CLOSED"
    b, _ = paid(w, ps, slot=w.slots()[1], who=w.other, ws=w.other_ws)
    row(db, b).starts_at = datetime.now(UTC) - timedelta(minutes=1)
    db.flush()
    r2 = client.post(f"{P}/bookings/{b}/accept", headers=hdr(w.expert))
    assert r2.status_code == 409 and r2.json()["error"]["code"] == "ACCEPT_WINDOW_CLOSED"


# ---------------------------------------------------------------- decline and the refund


def test_declining_refunds_in_full_and_tells_the_founder(client, db, w, ps, mail):
    bid, ref = paid(w, ps)
    mail.sent.clear()
    r = client.post(f"{P}/bookings/{bid}/decline", headers=hdr(w.expert))
    assert r.status_code == 200 and r.json()["data"]["status"] == "declined"
    assert ps.refunds == [{"transaction": ref, "amount": 1_000_000}]
    o = order_of(db, bid)
    db.refresh(o)
    assert (o.status, o.refunded_minor, o.refund_due_minor) == (OrderStatus.refunded, 1_000_000, 0)
    assert entries(db, o) == [("charge", 1_000_000), ("refund", -1_000_000)]
    assert orders.ledger_balance(db, o.id) == 0
    got = client.get(f"{M}/bookings/{bid}", headers=w.h()).json()["data"]
    assert got["status"] == "declined" and got["order"]["status"] == "refunded"
    assert [m.to for m in mail.sent] == [w.founder.email]
    assert "requested a full refund" in mail.sent[0].html
    again = client.post(f"{P}/bookings/{bid}/decline", headers=hdr(w.expert))
    assert again.status_code == 409
    assert len(ps.refunds) == 1  # never refunded twice


def test_declining_frees_the_slot_for_someone_else(client, db, w, ps):
    slot = w.slots()[0]
    bid, _ = paid(w, ps, slot=slot)
    assert slot not in w.slots()
    client.post(f"{P}/bookings/{bid}/decline", headers=hdr(w.expert))
    assert slot in w.slots()
    assert w.book(slot=slot, who=w.other, ws=w.other_ws).status_code == 201


def test_a_refund_waits_for_paystack_and_is_retried_by_the_timer(client, db, w, ps):
    bid, ref = paid(w, ps)
    ps.down = True
    r = client.post(f"{P}/bookings/{bid}/decline", headers=hdr(w.expert))
    assert r.status_code == 200 and r.json()["data"]["status"] == "declined"
    o = order_of(db, bid)
    db.refresh(o)
    assert (o.refund_due_minor, o.refunded_minor, o.refund_attempts) == (1_000_000, 0, 1)
    assert o.refund_next_attempt_at > datetime.now(UTC)  # backed off
    assert entries(db, o) == [("charge", 1_000_000)]  # nothing is recorded as refunded yet
    founder_view = client.get(f"{M}/bookings/{bid}", headers=w.h()).json()["data"]["order"]
    assert founder_view["refund_pending_minor"] == 1_000_000 and founder_view["refunded_minor"] == 0
    # not due yet: the timer leaves it alone
    assert timers.tick(db, datetime.now(UTC))["refunded"] == 0
    ps.down = False
    # Paystack is back, but the first attempt had no verdict: within 2 hours we only reconcile
    assert timers.tick(db, datetime.now(UTC) + timedelta(hours=1))["refunded"] == 0
    assert ps.refunds == []
    assert timers.tick(db, datetime.now(UTC) + timedelta(hours=3))["refunded"] == 1
    db.refresh(o)
    assert (o.status, o.refunded_minor, o.refund_due_minor) == (OrderStatus.refunded, 1_000_000, 0)
    assert o.refund_inflight_since is None
    assert entries(db, o) == [("charge", 1_000_000), ("refund", -1_000_000)]


def test_a_refund_paystack_keeps_rejecting_is_flagged_for_staff(client, db, w, ps):
    bid, _ = paid(w, ps)
    ps.reject_refunds = True
    client.post(f"{P}/bookings/{bid}/decline", headers=hdr(w.expert))
    o = order_of(db, bid)
    t = datetime.now(UTC)
    for _i in range(12):
        t += timedelta(days=2)
        timers.tick(db, t)
    db.refresh(o)
    assert o.refund_attempts == 12 and o.refund_failed_at is not None
    assert o.refund_due_minor == 1_000_000 and o.refunded_minor == 0
    attempts = o.refund_attempts
    timers.tick(db, t + timedelta(days=9))
    db.refresh(o)
    assert o.refund_attempts == attempts  # it stops hammering Paystack


def test_process_refund_cannot_refund_twice(client, db, w, ps):
    bid, _ = paid(w, ps)
    client.post(f"{P}/bookings/{bid}/decline", headers=hdr(w.expert))
    o = order_of(db, bid)
    assert orders.process_refund(db, o.id) == "nothing_due"
    assert len(ps.refunds) == 1


def test_a_partial_refund_leaves_the_order_partially_refunded(client, db, w, ps):
    bid, ref = paid(w, ps)
    o = order_of(db, bid)
    assert orders.request_refund(db, o, 300_000, "goodwill") is True
    assert orders.process_refund(db, o.id) == "refunded"
    db.refresh(o)
    assert o.status == OrderStatus.partially_refunded and o.refunded_minor == 300_000
    assert ps.refunds == [{"transaction": ref, "amount": 300_000}]
    assert orders.ledger_balance(db, o.id) == 700_000 == o.amount_minor - o.refunded_minor
    assert orders.request_refund(db, o, 800_000, "too much") is False  # more than what's left
    assert orders.request_refund(db, o, 700_000, "rest") is True
    assert orders.process_refund(db, o.id) == "refunded"
    db.refresh(o)
    assert o.status == OrderStatus.refunded and orders.ledger_balance(db, o.id) == 0
    assert entries(db, o) == [("charge", 1_000_000), ("refund", -700_000), ("refund", -300_000)]


def test_a_refund_cannot_exceed_what_was_paid_and_unpaid_orders_have_nothing_to_refund(
    client, db, w, ps
):
    d = w.book().json()["data"]
    o = order_of(db, d["id"])
    assert orders.request_refund(db, o, 1, "no money yet") is False  # pending order
    bid, _ = paid(w, ps, slot=w.slots()[1])
    paid_order = order_of(db, bid)
    assert orders.request_refund(db, paid_order, 0, "zero") is False
    assert orders.request_refund(db, paid_order, -5, "negative") is False
    assert orders.request_refund(db, paid_order, 1_000_001, "over") is False


def test_a_failed_refund_webhook_puts_the_money_back_on_the_books_once(client, db, w, ps):
    bid, ref = paid(w, ps)
    client.post(f"{P}/bookings/{bid}/decline", headers=hdr(w.expert))
    o = order_of(db, bid)
    body = {
        "event": "refund.failed",
        "data": {"transaction_reference": ref, "refund_reference": "rf_1", "amount": 1_000_000},
    }
    r = post_webhook(client, body)
    assert r.status_code == 200 and r.json()["data"]["outcome"] == "refund_reversed"
    db.refresh(o)
    assert o.refunded_minor == 0 and o.refund_failed_at is not None and o.status == OrderStatus.paid
    assert entries(db, o) == [
        ("adjustment", 1_000_000),
        ("charge", 1_000_000),
        ("refund", -1_000_000),
    ]
    assert orders.ledger_balance(db, o.id) == 1_000_000 == o.amount_minor - o.refunded_minor
    assert post_webhook(client, body).json()["data"]["outcome"] == "duplicate"
    assert len(entries(db, o)) == 3
    odd = post_webhook(
        client,
        {"event": "refund.failed", "data": {"transaction_reference": ref, "amount": 5_000_000}},
    )
    assert odd.json()["data"]["outcome"] in ("ignored", "duplicate")


def test_refund_processed_is_noted_without_changing_money(client, db, w, ps):
    bid, ref = paid(w, ps)
    client.post(f"{P}/bookings/{bid}/decline", headers=hdr(w.expert))
    o = order_of(db, bid)
    before = entries(db, o)
    r = post_webhook(
        client,
        {
            "event": "refund.processed",
            "data": {"transaction_reference": ref, "amount": 1_000_000, "refund_reference": "rf_9"},
        },
    )
    assert r.json()["data"]["outcome"] == "refund_noted"
    assert entries(db, o) == before


# ---------------------------------------------------------------- the timers


def test_an_unpaid_hold_expires_without_a_charge(client, db, w, ps):
    slot = w.slots()[0]
    d = w.book(slot=slot).json()["data"]
    t = datetime.now(UTC) + timedelta(minutes=16)
    res = timers.tick(db, t)
    assert res["expired_unpaid"] == 1
    assert row(db, d["id"]).status == BookingStatus.expired
    assert row(db, d["id"]).closed_reason == "payment_not_completed"
    o = order_of(db, d["id"])
    assert o.status == OrderStatus.failed and entries(db, o) == [] and ps.refunds == []
    assert timers.tick(db, t)["expired_unpaid"] == 0  # idempotent


def test_a_hold_that_is_still_valid_is_left_alone(client, db, w):
    d = w.book().json()["data"]
    assert timers.tick(db, datetime.now(UTC) + timedelta(minutes=5))["expired_unpaid"] == 0
    assert row(db, d["id"]).status == BookingStatus.pending_payment


def test_an_unanswered_booking_expires_after_48_hours_and_is_refunded(client, db, w, ps, mail):
    bid, ref = paid(w, ps, listing=w.package)  # a package has no slot to start first
    mail.sent.clear()
    assert timers.tick(db, datetime.now(UTC) + timedelta(hours=47))["expired_unanswered"] == 0
    res = timers.tick(db, datetime.now(UTC) + timedelta(hours=49))
    assert res == {"expired_unpaid": 0, "expired_unanswered": 1, "refunded": 1}
    b = row(db, bid)
    assert b.status == BookingStatus.expired and b.closed_reason == "provider_no_response"
    o = order_of(db, bid)
    db.refresh(o)
    assert o.status == OrderStatus.refunded and ps.refunds == [
        {"transaction": ref, "amount": 5_000_000}
    ]
    assert entries(db, o) == [("charge", 5_000_000), ("refund", -5_000_000)]
    assert [m.to for m in mail.sent] == [
        w.founder.email
    ] and "requested a full refund" in mail.sent[0].html
    again = timers.tick(db, datetime.now(UTC) + timedelta(hours=50))
    assert again["expired_unanswered"] == 0 and len(ps.refunds) == 1
    late = client.post(f"{P}/bookings/{bid}/accept", headers=hdr(w.expert))
    assert late.status_code == 409  # too late to accept


def test_an_accepted_booking_never_expires(client, db, w, ps):
    bid, _ = paid(w, ps)
    client.post(f"{P}/bookings/{bid}/accept", headers=hdr(w.expert))
    assert timers.tick(db, datetime.now(UTC) + timedelta(days=30))["expired_unanswered"] == 0
    assert row(db, bid).status == BookingStatus.confirmed and ps.refunds == []


def test_a_consultation_whose_slot_starts_unanswered_expires_and_is_refunded(client, db, w, ps):
    bid, ref = paid(w, ps)
    b = row(db, bid)
    res = timers.tick(
        db, b.starts_at + timedelta(minutes=1)
    )  # the slot began before the expert answered
    assert res["expired_unanswered"] == 1 and res["refunded"] == 1
    assert row(db, bid).closed_reason == "slot_passed"
    assert ps.refunds == [{"transaction": ref, "amount": 1_000_000}]


def test_the_timer_is_registered_with_the_scheduler_and_the_worker(client, db):
    from app.worker import scheduler
    from app.worker.handlers.marketplace import SCHED_MARKETPLACE_TIMERS
    from app.worker.runner import JOB_HANDLERS

    due = scheduler._due_marketplace_timers(db, datetime.now(UTC))
    assert [d.job_type for d in due] == [SCHED_MARKETPLACE_TIMERS]
    assert SCHED_MARKETPLACE_TIMERS in JOB_HANDLERS


# ---------------------------------------------------------------- reads, orders and receipts


def test_founder_reads_are_scoped_to_the_workspace(client, db, w, ps):
    bid, _ = paid(w, ps)
    ours = client.get(f"{M}/bookings", headers=w.h()).json()["data"]
    assert [b["id"] for b in ours["bookings"]] == [bid] and ours["pagination"]["total"] == 1
    theirs = client.get(f"{M}/bookings", headers=w.h(w.other, w.other_ws)).json()["data"]
    assert theirs["bookings"] == []
    assert client.get(f"{M}/bookings/{bid}", headers=w.h(w.other, w.other_ws)).status_code == 404
    assert (
        client.get(f"{M}/bookings/{bid}", headers=w.h(w.mate)).status_code == 200
    )  # a team member reads


def test_a_checkout_hold_is_not_listed_but_is_readable(client, db, w):
    d = w.book().json()["data"]
    assert client.get(f"{M}/bookings", headers=w.h()).json()["data"]["bookings"] == []
    got = client.get(f"{M}/bookings/{d['id']}", headers=w.h()).json()["data"]
    assert got["status"] == "pending_payment" and got["order"]["checkout_url"]


def test_orders_and_the_receipt(client, db, w, ps):
    unpaid = w.book().json()["data"]
    bid, ref = paid(w, ps, slot=w.slots()[1])
    o = order_of(db, bid)
    listing = client.get(f"{M}/orders", headers=w.h()).json()["data"]
    assert [x["id"] for x in listing["orders"]] == [
        str(o.id)
    ]  # the unpaid checkout isn't an order yet
    assert (
        listing["orders"][0]["title"] == "Design critique"
        and listing["orders"][0]["status"] == "paid"
    )
    no_receipt = client.get(f"{M}/orders/{unpaid['order']['id']}/receipt", headers=w.h())
    assert no_receipt.status_code == 404
    r = client.get(f"{M}/orders/{o.id}/receipt", headers=w.h())
    assert r.status_code == 200, r.text
    rec = r.json()["data"]
    assert rec["receipt_number"] == o.receipt_number and rec["reference"] == ref
    assert rec["total_minor"] == 1_000_000 and rec["is_tax_invoice"] is False
    assert sum(line["amount_minor"] for line in rec["lines"]) == rec["total_minor"]
    assert [line["amount_minor"] for line in rec["lines"]] == [850_000, 150_000]
    assert "Ngozi Eze" in rec["lines"][0]["label"] and "not a tax invoice" in rec["note"]
    assert (
        client.get(f"{M}/orders/{o.id}/receipt", headers=w.h(w.other, w.other_ws)).status_code
        == 404
    )
    assert client.get(f"{M}/orders/{o.id}/receipt", headers=w.h(w.mate)).status_code == 200
    filtered = client.get(f"{M}/orders?status=refunded", headers=w.h()).json()["data"]["orders"]
    assert filtered == []


def test_receipt_numbers_are_unique(client, db, w, ps):
    a, _ = paid(w, ps, slot=w.slots()[0])
    b, _ = paid(w, ps, slot=w.slots()[1], who=w.other, ws=w.other_ws)
    na, nb = order_of(db, a).receipt_number, order_of(db, b).receipt_number
    assert na != nb and na.startswith("MPR-") and nb.startswith("MPR-")


# ---------------------------------------------------------------- ledger and database invariants


def test_the_ledger_refuses_a_duplicate_movement(client, db, w, ps):
    bid, ref = paid(w, ps)
    o = order_of(db, bid)
    assert orders.write_ledger(db, o, w.provider.id, LedgerKind.charge, 1_000_000, ref) is False
    assert entries(db, o) == [("charge", 1_000_000)]


def test_the_ledger_refuses_a_zero_movement(client, db, w, ps):
    bid, _ = paid(w, ps)
    o = order_of(db, bid)
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            orders.write_ledger(db, o, w.provider.id, LedgerKind.adjustment, 0, "zero")


def test_the_database_keeps_an_orders_numbers_honest(client, db, w, ps):
    bid, _ = paid(w, ps)
    o = order_of(db, bid)
    for field, value in (
        ("commission_minor", 1),  # commission + net no longer equals the amount
        ("refunded_minor", 1_000_001),  # more refunded than paid
        ("amount_minor", 0),
    ):
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                setattr(o, field, value)
                db.flush()
        db.refresh(o)
    with pytest.raises(IntegrityError):  # refunded + due may not exceed the amount either
        with db.begin_nested():
            o.refunded_minor, o.refund_due_minor = 600_000, 500_000
            db.flush()


def test_every_event_is_recorded_once(client, db, w, ps):
    bid, ref = paid(w, ps)
    assert (
        db.query(MarketplaceWebhookEvent).filter_by(event_key=f"mp:charge.success:{ref}:").count()
        == 1
    )


def test_a_workspace_mentor_sees_no_bookings_or_orders(client, db, w, ps):
    paid(w, ps)
    mentor = mk_user(db)
    member(db, w.ws, mentor, MembershipRole.mentor)
    for path in ("/bookings", "/orders", "/checkout"):
        assert client.get(f"{M}{path}", headers=w.h(mentor)).status_code == 403


# ---------------------------------------------------------------- review fixes (Opus review of slice C)


def test_a_refund_whose_answer_was_lost_is_not_sent_twice(client, db, w, ps):
    bid, ref = paid(w, ps)
    ps.timeout_after_accept = True  # Paystack takes the refund, but we never see the answer
    client.post(f"{P}/bookings/{bid}/decline", headers=hdr(w.expert))
    o = order_of(db, bid)
    db.refresh(o)
    assert len(ps.refunds) == 1 and o.refund_due_minor == 1_000_000 and o.refunded_minor == 0
    ps.timeout_after_accept = False
    assert timers.tick(db, datetime.now(UTC) + timedelta(minutes=10))["refunded"] == 1
    db.refresh(o)
    assert len(ps.refunds) == 1  # reconciled with Paystack's own list, not resent
    assert (o.status, o.refunded_minor, o.refund_due_minor) == (OrderStatus.refunded, 1_000_000, 0)
    assert entries(db, o) == [("charge", 1_000_000), ("refund", -1_000_000)]
    ref_entry = (
        db.query(MarketplaceLedgerEntry).filter_by(order_id=o.id, kind=LedgerKind.refund).one()
    )
    assert (
        ref_entry.external_ref == "refund:reconcile:1000000"
    )  # booked by reconciling with Paystack's total


def test_an_unconfirmed_refund_is_not_resent_inside_the_wait_and_is_after_it(client, db, w, ps):
    bid, ref = paid(w, ps)
    o = order_of(db, bid)
    assert orders.request_refund(db, o, 300_000, "goodwill") is True
    ps.down = True
    assert orders.process_refund(db, o.id) == "retry_scheduled"  # no verdict: marker stays
    db.refresh(o)
    assert o.refund_inflight_since is not None
    ps.down = False
    assert orders.process_refund(db, o.id) == "awaiting_reconcile"  # nothing at Paystack, too soon
    assert ps.refunds == []
    o.refund_inflight_since = datetime.now(UTC) - timedelta(hours=3)
    db.flush()
    assert orders.process_refund(db, o.id) == "refunded"  # nothing was accepted: now it is sent
    assert ps.refunds == [{"transaction": ref, "amount": 300_000}]
    db.refresh(o)
    assert o.refund_inflight_since is None and o.refunded_minor == 300_000


def test_a_definitive_rejection_clears_the_marker_so_the_next_try_is_immediate(client, db, w, ps):
    bid, _ = paid(w, ps)
    o = order_of(db, bid)
    orders.request_refund(db, o, 300_000, "goodwill")
    ps.reject_refunds = True
    assert orders.process_refund(db, o.id) == "retry_scheduled"
    db.refresh(o)
    assert o.refund_inflight_since is None  # a 4xx means nothing was accepted
    ps.reject_refunds = False
    assert orders.process_refund(db, o.id) == "refunded"


def test_a_rejection_after_an_unverdicted_send_keeps_the_marker(client, db, w, ps):
    bid, ref = paid(w, ps)
    o = order_of(db, bid)
    orders.request_refund(db, o, 300_000, "goodwill")
    ps.down = True
    assert orders.process_refund(db, o.id) == "retry_scheduled"  # no verdict: the marker is set
    ps.down = False
    o.refund_inflight_since = datetime.now(UTC) - timedelta(
        hours=3
    )  # past the wait: a re-send is allowed
    db.flush()
    ps.reject_refunds = True  # ... and Paystack says no (perhaps "already refunded")
    assert orders.process_refund(db, o.id) == "retry_scheduled"
    db.refresh(o)
    assert o.refund_inflight_since is not None  # not cleared: the earlier send may have landed
    ps.reject_refunds = False
    ps.refund(ref, 300_000)  # in fact it had landed at Paystack
    assert orders.process_refund(db, o.id) == "refunded"  # reconciled, not sent a third time
    assert len(ps.refunds) == 1 and o.refunded_minor == 300_000


def test_a_lost_partial_refund_is_not_refunded_twice(client, db, w, ps):
    bid, ref = paid(w, ps)
    o = order_of(db, bid)
    assert orders.request_refund(db, o, 300_000, "goodwill") is True
    ps.timeout_after_accept = True
    assert orders.process_refund(db, o.id) == "retry_scheduled"
    ps.timeout_after_accept = False
    assert orders.process_refund(db, o.id) == "refunded"
    db.refresh(o)
    assert ps.refunds == [{"transaction": ref, "amount": 300_000}]  # one real refund, not two
    assert o.refunded_minor == 300_000 and orders.ledger_balance(db, o.id) == 700_000


def test_refund_processed_settles_a_refund_we_never_saw_answered(client, db, w, ps):
    bid, ref = paid(w, ps)
    ps.timeout_after_accept = True  # Paystack takes the refund, our answer is lost
    client.post(f"{P}/bookings/{bid}/decline", headers=hdr(w.expert))
    ps.timeout_after_accept = False
    o = order_of(db, bid)
    db.refresh(o)
    assert o.refund_due_minor == 1_000_000 and o.refunded_minor == 0 and len(ps.refunds) == 1
    body = {
        "event": "refund.processed",
        "data": {"transaction_reference": ref, "refund_reference": "rf_77", "amount": 1_000_000},
    }
    r = post_webhook(client, body)
    assert r.json()["data"]["outcome"] == "refund_settled"  # booked from what Paystack holds
    db.refresh(o)
    assert (o.status, o.refunded_minor, o.refund_due_minor) == (OrderStatus.refunded, 1_000_000, 0)
    assert entries(db, o) == [("charge", 1_000_000), ("refund", -1_000_000)]
    assert timers.tick(db, datetime.now(UTC) + timedelta(hours=1))["refunded"] == 0
    assert len(ps.refunds) == 1  # nothing was sent again
    assert post_webhook(client, body).json()["data"]["outcome"] == "duplicate"


def test_a_refund_webhook_books_nothing_when_no_send_is_unanswered(client, db, w, ps):
    bid, ref = paid(w, ps)
    o = order_of(db, bid)
    orders.request_refund(db, o, 1_000_000, "owed")  # owed, but no send attempted yet: no marker
    body = {
        "event": "refund.processed",
        "data": {"transaction_reference": ref, "refund_reference": "rf_early", "amount": 1_000_000},
    }
    assert post_webhook(client, body).json()["data"]["outcome"] == "refund_noted"
    db.refresh(o)
    assert o.refunded_minor == 0 and o.refund_due_minor == 1_000_000  # still owed, still to be sent


def test_a_refund_webhook_for_the_wrong_amount_or_unknown_order_is_only_noted(client, db, w, ps):
    bid, ref = paid(w, ps)
    ps.timeout_after_accept = True
    client.post(f"{P}/bookings/{bid}/decline", headers=hdr(w.expert))  # an unanswered send
    ps.timeout_after_accept = False
    o = order_of(db, bid)
    too_much = {
        "event": "refund.processed",
        "data": {"transaction_reference": ref, "refund_reference": "r1", "amount": 9_999_999},
    }
    assert post_webhook(client, too_much).json()["data"]["outcome"] == "refund_noted"
    no_ref = {
        "event": "refund.processed",
        "data": {"transaction_reference": ref, "amount": 1_000_000},
    }
    assert post_webhook(client, no_ref).json()["data"]["outcome"] == "refund_noted"
    db.refresh(o)
    assert o.refunded_minor == 0 and o.refund_due_minor == 1_000_000


def test_a_reversed_refund_can_be_refunded_again_and_the_ledger_agrees(client, db, w, ps):
    bid, ref = paid(w, ps)
    client.post(f"{P}/bookings/{bid}/decline", headers=hdr(w.expert))
    post_webhook(
        client,
        {
            "event": "refund.failed",
            "data": {"transaction_reference": ref, "refund_reference": "rf_a", "amount": 1_000_000},
        },
    )
    o = order_of(db, bid)
    db.refresh(o)
    assert o.refunded_minor == 0
    assert orders.request_refund(db, o, 1_000_000, "staff retry") is True
    assert (
        orders.process_refund(db, o.id) == "refunded"
    )  # same amount as the first: a new Paystack id
    db.refresh(o)
    assert o.refunded_minor == 1_000_000 and orders.ledger_balance(db, o.id) == 0
    assert len(entries(db, o)) == 4  # charge, refund, adjustment, refund


def test_a_second_founders_live_hold_blocks_reviving_a_lapsed_booking(client, db, w, ps):
    slot = w.slots()[0]
    late = w.book(slot=slot).json()["data"]
    db.get(MarketplaceBooking, uuid.UUID(late["id"])).slot_hold_expires_at = datetime.now(
        UTC
    ) - timedelta(minutes=1)
    db.flush()
    rival = w.book(slot=slot, who=w.other, ws=w.other_ws)  # holds the slot, hasn't paid yet
    assert rival.status_code == 201
    ps.pay(late["order"]["reference"])
    r = post_webhook(client, charge_success(late["order"]["reference"]))
    assert r.json()["data"]["outcome"] == "paid_then_refund_requested"  # the rival's hold stands
    assert row(db, rival.json()["data"]["id"]).status == BookingStatus.pending_payment


def test_an_unconfirmed_saved_card_charge_keeps_the_hold_for_the_webhook(client, db, w, ps):
    from app.db.models.billing import BillingPaymentMethod

    db.add(
        BillingPaymentMethod(
            startup_id=w.ws.id,
            authorization_code="AUTH_u",
            signature="SIG_u",
            brand="visa",
            last4="4081",
            exp_month="12",
            exp_year="2030",
            is_default=True,
        )
    )
    db.flush()
    ps.charge_unavailable = True  # the charge may or may not have gone through
    r = w.book(pay_with="saved_card")
    assert r.status_code == 502 and r.json()["error"]["code"] == "PAYMENT_UNCONFIRMED"
    b = db.query(MarketplaceBooking).one()
    assert r.json()["error"]["field_errors"] == [{"field": "booking_id", "message": str(b.id)}]
    assert b.status == BookingStatus.pending_payment  # NOT cancelled
    o = db.query(MarketplaceOrder).one()
    assert o.status == OrderStatus.pending
    ps.tx[o.paystack_reference] = {"amount": o.amount_minor, "email": "x@example.com"}
    ps.pay(o.paystack_reference)  # it had in fact been charged: the webhook confirms the booking
    ok = post_webhook(client, charge_success(o.paystack_reference))
    assert ok.json()["data"]["outcome"] == "paid"
    assert row(db, str(b.id)).status == BookingStatus.awaiting_provider


def test_a_webhook_that_isnt_a_verdict_does_not_block_paystacks_retry(client, db, w, ps):
    d = w.book().json()["data"]
    ref = d["order"]["reference"]
    body = charge_success(ref)
    first = post_webhook(client, body)  # not paid at Paystack yet
    assert first.status_code == 409 and first.json()["error"]["code"] == "RETRY_LATER"
    assert db.query(MarketplaceWebhookEvent).count() == 0  # nothing recorded for a non-verdict
    ps.pay(ref)
    retry = post_webhook(client, body)  # the very same bytes
    assert retry.json()["data"]["outcome"] == "paid"
    assert post_webhook(client, body).json()["data"]["outcome"] == "duplicate"


def test_a_booking_cannot_be_replayed_from_another_workspace(client, db, w):
    first = w.book(key="cross-ws-key-001", slot=w.slots()[0])
    assert first.status_code == 201
    member(db, w.other_ws, w.founder, MembershipRole.founder)  # the same person, a second workspace
    r = w.book(key="cross-ws-key-001", slot=w.slots()[0], who=w.founder, ws=w.other_ws)
    assert r.status_code == 409 and r.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert "brief" not in r.text


def test_a_refund_accepted_by_paystack_but_never_recorded_is_booked_not_resent(client, db, w, ps):
    """A crash after Paystack accepted the refund but before we committed leaves refund_attempts
    at 0; the next attempt must still find it at Paystack instead of sending a second one."""
    bid, ref = paid(w, ps)
    o = order_of(db, bid)
    assert orders.request_refund(db, o, 300_000, "goodwill") is True
    ps.refund(ref, 300_000)  # Paystack took it; our process died before recording anything
    o.refund_inflight_since = datetime.now(UTC)  # the committed marker a crash leaves behind
    db.flush()
    assert o.refunded_minor == 0
    assert orders.process_refund(db, o.id) == "refunded"
    db.refresh(o)
    assert len(ps.refunds) == 1  # nothing was sent a second time
    assert o.refunded_minor == 300_000 and o.refund_due_minor == 0
    assert orders.ledger_balance(db, o.id) == 700_000


def test_an_earlier_refund_is_never_booked_as_a_later_one(client, db, w, ps):
    bid, ref = paid(w, ps)
    o = order_of(db, bid)
    assert orders.request_refund(db, o, 200_000, "first") is True
    assert orders.process_refund(db, o.id) == "refunded"  # Paystack holds 200k, booked
    assert orders.request_refund(db, o, 200_000, "second") is True  # a second 200k is now due
    # the late webhook for the FIRST refund carries a different identifier than the one we booked
    late = {
        "event": "refund.processed",
        "data": {"transaction_reference": ref, "refund_reference": "RR-first", "amount": 200_000},
    }
    assert post_webhook(client, late).json()["data"]["outcome"] == "refund_noted"
    db.refresh(o)
    assert o.refunded_minor == 200_000 and o.refund_due_minor == 200_000  # the second is still owed
    assert orders.process_refund(db, o.id) == "refunded"
    db.refresh(o)
    assert [r["amount"] for r in ps.refunds] == [200_000, 200_000]  # and it really was sent
    assert o.refunded_minor == 400_000 and orders.ledger_balance(db, o.id) == 600_000


def test_refunds_belonging_to_another_order_are_never_booked_here(client, db, w, ps):
    a, ref_a = paid(w, ps, slot=w.slots()[0])
    b, ref_b = paid(w, ps, slot=w.slots()[1], who=w.other, ws=w.other_ws)
    oa, ob = order_of(db, a), order_of(db, b)
    assert orders.request_refund(db, oa, 1_000_000, "a") is True
    assert orders.process_refund(db, oa.id) == "refunded"  # Paystack now holds A's refund
    ps.ignore_refund_filter = True  # a list endpoint that returns every refund, whatever we ask
    assert orders.request_refund(db, ob, 1_000_000, "b") is True
    assert orders.process_refund(db, ob.id) == "refunded"
    db.refresh(ob)
    assert [r["transaction"] for r in ps.refunds] == [ref_a, ref_b]  # B's own refund was sent
    assert ob.refunded_minor == 1_000_000
    assert entries(db, ob) == [("charge", 1_000_000), ("refund", -1_000_000)]


def test_a_checkout_hold_can_be_listed_when_asked_for(client, db, w):
    d = w.book().json()["data"]
    default = client.get(f"{M}/bookings", headers=w.h()).json()["data"]["bookings"]
    assert default == []
    holds = client.get(f"{M}/bookings?status=pending_payment", headers=w.h()).json()["data"][
        "bookings"
    ]
    assert [b["id"] for b in holds] == [d["id"]]
