"""Marketplace slice C: creating a booking, the 15-minute hold, consent, and applying a payment
(webhook and verify), including every way money can arrive wrong."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.redis import get_redis
from app.db.models.billing import BillingPaymentMethod
from app.db.models.enums import (
    BookingStatus,
    LedgerKind,
    ListingStatus,
    MembershipRole,
    OrderStatus,
    ProviderStatus,
)
from app.db.models.marketplace import (
    MarketplaceBooking,
    MarketplaceConsent,
    MarketplaceLedgerEntry,
    MarketplaceOrder,
)
from app.services.marketplace import orders
from tests.api.test_staff_users import hdr, member, mk_user
from tests.marketplace_booking_helpers import (
    BRIEF,
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
    r = get_redis()
    for k in r.scan_iter("report_rl:*"):
        r.delete(k)


@pytest.fixture()
def ps(monkeypatch):
    return install_fake_paystack(monkeypatch)


@pytest.fixture()
def mail(monkeypatch):
    return install_mailbox(monkeypatch)


@pytest.fixture()
def w(client, db, ps, mail):
    return World(client, db)


def order_of(db, booking_id) -> MarketplaceOrder:
    return db.query(MarketplaceOrder).filter_by(booking_id=uuid.UUID(booking_id)).one()


def ledger(db, order) -> list[MarketplaceLedgerEntry]:
    return db.query(MarketplaceLedgerEntry).filter_by(order_id=order.id).all()


def paid_booking(w, ps, **kw):
    r = w.book(**kw)
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    ps.pay(data["order"]["reference"])
    hook = post_webhook(w.client, charge_success(data["order"]["reference"]))
    assert hook.status_code == 200, hook.text
    return data["id"], data["order"]["reference"]


# ---------------------------------------------------------------- the switch and the roles


def test_booking_is_refused_while_payments_are_off(client, db, w, monkeypatch):
    monkeypatch.setattr(settings, "MARKETPLACE_PAYMENTS_ENABLED", False)
    r = w.book()
    assert r.status_code == 403 and r.json()["error"]["code"] == "PAYMENTS_DISABLED"
    assert db.query(MarketplaceBooking).count() == 0
    chk = client.get(f"{M}/checkout", headers=w.h()).json()["data"]
    assert chk["payments_enabled"] is False


def test_only_a_founder_books_but_team_members_read(client, db, w):
    mate_h = w.h(w.mate)
    assert w.book(who=w.mate).status_code == 403
    mentor = mk_user(db)
    member(db, w.ws, mentor, MembershipRole.mentor)
    assert w.book(who=mentor).status_code == 403
    anon = client.post(f"{M}/bookings", json={})
    assert anon.status_code == 401
    created = w.book()
    assert created.status_code == 201
    bid = created.json()["data"]["id"]
    assert client.get(f"{M}/bookings/{bid}", headers=mate_h).status_code == 200
    assert client.get(f"{M}/bookings", headers=mate_h).status_code == 200
    assert client.get(f"{M}/bookings", headers=w.h(mentor)).status_code == 403
    cancel = client.post(f"{M}/bookings/{bid}/cancel", headers=mate_h)
    assert cancel.status_code == 403  # a team member can't cancel (or pay)
    verify = client.post(f"{M}/bookings/{bid}/verify-payment", headers=mate_h)
    assert verify.status_code == 403
    checkout = client.get(f"{M}/checkout", headers=mate_h).json()["data"]
    assert checkout["can_book"] is False


# ---------------------------------------------------------------- creating a booking


def test_a_consultation_booking_holds_the_slot_and_returns_a_checkout_url(client, db, w, ps):
    slot = w.slots()[0]
    r = w.book(slot=slot)
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    assert d["replayed"] is False and d["status"] == "pending_payment"
    assert d["starts_at"].startswith(slot[:16]) and d["ends_at"]
    assert d["price_minor"] == 1_000_000 and d["currency"] == "NGN"
    assert d["title"] == "Design critique" and d["brief"] == BRIEF
    assert d["slot_hold_expires_at"] and d["meeting_url"] is None
    o = d["order"]
    assert o["status"] == "pending" and o["amount_minor"] == 1_000_000
    assert o["reference"].startswith("mp_") and len(o["reference"]) == 23
    assert o["checkout_url"] == f"https://checkout.paystack.test/{o['reference']}"
    assert ps.tx[o["reference"]]["amount"] == 1_000_000  # Paystack was asked for the listed price
    assert "commission" not in json.dumps(d) and "net" not in json.dumps(
        d
    )  # founder pays the price
    row = order_of(db, d["id"])
    assert row.commission_minor + row.provider_net_minor == row.amount_minor == 1_000_000
    assert (row.commission_minor, row.provider_net_minor) == (150_000, 850_000)
    assert ledger(db, row) == []  # nothing moves until Paystack confirms


def test_a_package_booking_has_no_slot(client, db, w):
    r = w.book(listing=w.package)
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    assert d["type"] == "package" and d["starts_at"] is None and d["delivery_days"] == 5
    assert d["deliverables"] == ["A brand kit"]
    bad = w.book(listing=w.package, starts_at=w.slots()[0])
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "SLOT_NOT_APPLICABLE"


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"starts_at": None}, "SLOT_REQUIRED"),
        ({"brief": "short"}, "VALIDATION_ERROR"),
        ({"starts_at": "2030-01-01T09:00:00"}, "VALIDATION_ERROR"),  # no UTC offset
        ({"pay_with": "crypto"}, "VALIDATION_ERROR"),
        ({"price_minor": 1}, "VALIDATION_ERROR"),  # unknown field
        ({"listing_id": "nope"}, "VALIDATION_ERROR"),
    ],
)
def test_booking_validation(client, db, w, over, code):
    body = {
        "listing_id": str(w.consult.id),
        "brief": BRIEF,
        "accept_terms": True,
        "starts_at": w.slots()[0],
        **over,
    }
    r = client.post(
        f"{M}/bookings", json=body, headers={**w.h(), "Idempotency-Key": uuid.uuid4().hex}
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == code
    assert db.query(MarketplaceBooking).count() == 0


def test_an_unavailable_time_is_refused(client, db, w):
    far = (datetime.now(UTC) + timedelta(days=2)).replace(
        hour=3, minute=0, second=0, microsecond=0
    )  # 04:00 Lagos
    r = w.book(starts_at=far.isoformat())
    assert r.status_code == 409 and r.json()["error"]["code"] == "SLOT_UNAVAILABLE"
    off_grid = w.slots()[0].replace(":00:00", ":17:00")
    r2 = w.book(starts_at=off_grid)
    assert r2.status_code == 409


def test_idempotency_key_is_required_and_a_replay_returns_the_same_booking(client, db, w, ps):
    slot = w.slots()[0]
    body = {
        "listing_id": str(w.consult.id),
        "brief": BRIEF,
        "accept_terms": True,
        "starts_at": slot,
    }
    for bad in (None, "short", "has spaces in it!"):
        headers = {**w.h(), **({"Idempotency-Key": bad} if bad else {})}
        r = client.post(f"{M}/bookings", json=body, headers=headers)
        assert r.status_code == 422, bad
        assert r.json()["error"]["field_errors"][0]["field"] == "Idempotency-Key"
    first = client.post(
        f"{M}/bookings", json=body, headers={**w.h(), "Idempotency-Key": "key-aaaa-0001"}
    )
    again = client.post(
        f"{M}/bookings", json=body, headers={**w.h(), "Idempotency-Key": "key-aaaa-0001"}
    )
    assert first.status_code == 201 and again.status_code == 200
    assert again.json()["data"]["replayed"] is True
    assert again.json()["data"]["id"] == first.json()["data"]["id"]
    assert (
        again.json()["data"]["order"]["checkout_url"]
        == first.json()["data"]["order"]["checkout_url"]
    )
    assert db.query(MarketplaceBooking).count() == 1 and db.query(MarketplaceOrder).count() == 1
    assert len(ps.tx) == 1  # Paystack was initialised once
    other_slot = w.slots()[1]
    clash = client.post(
        f"{M}/bookings",
        json={**body, "starts_at": other_slot},
        headers={**w.h(), "Idempotency-Key": "key-aaaa-0001"},
    )
    assert clash.status_code == 409 and clash.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_the_same_key_from_another_person_is_a_separate_booking(client, db, w):
    a = w.book(key="shared-key-001", slot=w.slots()[0])
    b = w.book(key="shared-key-001", slot=w.slots()[1], who=w.other, ws=w.other_ws)
    assert a.status_code == 201 and b.status_code == 201
    assert a.json()["data"]["id"] != b.json()["data"]["id"]


def test_terms_are_asked_once_and_recorded(client, db, w):
    chk = client.get(f"{M}/checkout", headers=w.h()).json()["data"]
    assert chk["terms_accepted"] is False and chk["can_book"] is True
    refused = w.book(accept_terms=False)
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "TERMS_REQUIRED"
    assert db.query(MarketplaceConsent).count() == 0 and db.query(MarketplaceBooking).count() == 0
    ok = w.book(slot=w.slots()[0])
    assert ok.status_code == 201
    assert db.query(MarketplaceConsent).count() == 1
    assert client.get(f"{M}/checkout", headers=w.h()).json()["data"]["terms_accepted"] is True
    again = w.book(slot=w.slots()[1], accept_terms=False)  # already accepted: no box needed
    assert again.status_code == 201
    assert db.query(MarketplaceConsent).count() == 1


def test_an_expert_cannot_book_themselves(client, db, w):
    own_ws_user = w.expert
    member(db, w.ws, own_ws_user, MembershipRole.team_member)  # the expert sits in the workspace
    r = w.book()
    assert r.status_code == 422 and r.json()["error"]["code"] == "CANNOT_BOOK_OWN"
    # same email as a member, different user
    w2 = World(client, db)
    w2.provider.email = "twin.address@example.com"  # a member of the workspace has this address
    db.flush()
    twin = mk_user(db, email="twin.address@example.com")
    member(db, w2.ws, twin, MembershipRole.team_member)
    r2 = w2.book(who=w2.founder, ws=w2.ws)
    assert r2.status_code == 422 and r2.json()["error"]["code"] == "CANNOT_BOOK_OWN"
    assert db.query(MarketplaceBooking).count() == 0


@pytest.mark.parametrize("mutate", ["paused", "unlisted", "draft", "suspended"])
def test_hidden_listings_cannot_be_booked(client, db, w, mutate):
    slot = w.slots()[0]
    if mutate == "suspended":
        w.provider.status = ProviderStatus.suspended
    else:
        w.consult.status = ListingStatus(mutate)
    db.flush()
    r = w.book(slot=slot)
    assert r.status_code == 404
    assert db.query(MarketplaceBooking).count() == 0


def test_the_booking_keeps_what_was_bought_after_the_listing_changes(client, db, w):
    r = w.book()
    bid = r.json()["data"]["id"]
    w.consult.title = "Renamed later"
    w.consult.price_minor = 9_999_999
    w.consult.deliverables_json = ["something else"]
    db.flush()
    row = db.get(MarketplaceBooking, uuid.UUID(bid))
    assert (row.title_snap, row.price_minor) == ("Design critique", 1_000_000)
    got = client.get(f"{M}/bookings/{bid}", headers=w.h()).json()["data"]
    assert got["title"] == "Design critique" and got["price_minor"] == 1_000_000
    assert order_of(db, bid).amount_minor == 1_000_000


def test_a_commission_override_is_snapshotted_and_still_adds_up(client, db, w):
    w.provider.commission_bps = 1000
    db.flush()
    bid = w.book().json()["data"]["id"]
    row = db.get(MarketplaceBooking, uuid.UUID(bid))
    assert row.commission_bps_snap == 1000
    o = order_of(db, bid)
    assert (o.commission_minor, o.provider_net_minor) == (100_000, 900_000)
    w.provider.commission_bps = 2500  # a later change doesn't touch an existing booking
    db.flush()
    assert order_of(db, bid).commission_minor == 100_000


# ---------------------------------------------------------------- the slot hold and double booking


def test_a_held_slot_is_not_offered_or_bookable_by_someone_else(client, db, w):
    slot = w.slots()[0]
    assert w.book(slot=slot).status_code == 201
    assert slot not in w.slots()  # gone from the founder-facing slot list
    second = w.book(slot=slot, who=w.other, ws=w.other_ws)
    assert second.status_code == 409 and second.json()["error"]["code"] == "SLOT_UNAVAILABLE"
    assert db.query(MarketplaceBooking).count() == 1


def test_an_overlapping_start_is_blocked_too(client, db, w):
    # step the expert's windows to every 30 minutes so a 09:30 start overlaps a 09:00-10:00 booking
    body = {
        "min_notice_h": 0,
        "buffer_min": 0,
        "rules": [
            {"weekday": d, "start": "08:00", "end": "20:00", "slot_min": 30} for d in range(7)
        ],
        "exceptions": [],
    }
    assert client.put(f"{P}/availability", json=body, headers=hdr(w.expert)).status_code == 200
    first, second, third = w.slots()[0], w.slots()[1], w.slots()[2]
    assert w.book(slot=first).status_code == 201
    overlap = w.book(slot=second, who=w.other, ws=w.other_ws)  # starts 30 min into the first
    assert overlap.status_code == 409
    clear = w.book(slot=third, who=w.other, ws=w.other_ws)  # starts when the first ends
    assert clear.status_code == 201


def test_the_buffer_keeps_a_gap_after_a_booking(client, db, w):
    body = {
        "min_notice_h": 0,
        "buffer_min": 30,
        "rules": [
            {"weekday": d, "start": "08:00", "end": "20:00", "slot_min": 60} for d in range(7)
        ],
        "exceptions": [],
    }
    client.put(f"{P}/availability", json=body, headers=hdr(w.expert))
    slots = w.slots()
    assert w.book(slot=slots[1]).status_code == 201
    assert slots[0] not in w.slots() and slots[2] not in w.slots()  # 30-minute buffer both sides
    assert slots[3] in w.slots()


def test_a_lapsed_hold_frees_the_slot_immediately(client, db, w):
    slot = w.slots()[0]
    bid = w.book(slot=slot).json()["data"]["id"]
    db.get(MarketplaceBooking, uuid.UUID(bid)).slot_hold_expires_at = datetime.now(UTC) - timedelta(
        seconds=1
    )
    db.flush()
    assert slot in w.slots()  # no waiting for the timer
    taken = w.book(slot=slot, who=w.other, ws=w.other_ws)
    assert taken.status_code == 201


def test_a_paid_booking_keeps_the_slot_forever(client, db, w, ps):
    slot = w.slots()[0]
    paid_booking(w, ps, slot=slot)
    assert slot not in w.slots()
    assert w.book(slot=slot, who=w.other, ws=w.other_ws).status_code == 409


def test_the_database_refuses_two_live_bookings_at_one_instant(client, db, w, ps):
    slot = w.slots()[0]
    bid, _ = paid_booking(w, ps, slot=slot)
    first = db.get(MarketplaceBooking, uuid.UUID(bid))
    clone = MarketplaceBooking(
        startup_id=w.other_ws.id,
        listing_id=first.listing_id,
        provider_id=first.provider_id,
        booked_by=w.other.id,
        status=BookingStatus.awaiting_provider,
        type="consultation",
        title_snap="x",
        category_snap="Design",
        price_minor=1,
        commission_bps_snap=1500,
        brief=BRIEF,
        starts_at=first.starts_at,
        ends_at=first.ends_at,
        idempotency_key="backstop-key-1",
    )
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(clone)
            db.flush()


# ---------------------------------------------------------------- applying a payment


def test_a_signed_charge_success_confirms_the_booking(client, db, w, ps, mail):
    d = w.book().json()["data"]
    ref = d["order"]["reference"]
    ps.pay(ref)
    r = post_webhook(client, charge_success(ref))
    assert r.status_code == 200 and r.json()["data"]["outcome"] == "paid"
    got = client.get(f"{M}/bookings/{d['id']}", headers=w.h()).json()["data"]
    assert got["status"] == "awaiting_provider" and got["slot_hold_expires_at"] is None
    assert got["order"]["status"] == "paid" and got["order"]["receipt_number"].startswith("MPR-")
    assert got["order"]["checkout_url"] is None
    window = datetime.fromisoformat(got["accept_by"]) - datetime.now(UTC)
    assert timedelta(hours=47, minutes=59) < window <= timedelta(hours=48)
    o = order_of(db, d["id"])
    (entry,) = ledger(db, o)
    assert (entry.kind, entry.amount_minor, entry.external_ref) == (
        LedgerKind.charge,
        1_000_000,
        ref,
    )
    assert orders.ledger_balance(db, o.id) == o.amount_minor
    assert [m_.to for m_ in mail.sent] == [w.provider.email]  # the expert hears about it
    assert "New booking request" in mail.sent[0].subject


def test_a_bad_or_missing_signature_is_refused_and_changes_nothing(client, db, w, ps):
    d = w.book().json()["data"]
    ps.pay(d["order"]["reference"])
    body = charge_success(d["order"]["reference"])
    assert post_webhook(client, body, signature="0" * 128).status_code == 401
    assert post_webhook(client, body, signature="").status_code == 401
    raw = json.dumps(body).encode()
    assert client.post("/api/v1/webhooks/billing/paystack", content=raw).status_code == 401
    assert order_of(db, d["id"]).status == OrderStatus.pending


def test_a_replayed_webhook_applies_once(client, db, w, ps, mail):
    d = w.book().json()["data"]
    ref = d["order"]["reference"]
    ps.pay(ref)
    body = charge_success(ref)
    assert post_webhook(client, body).json()["data"]["outcome"] == "paid"
    again = post_webhook(client, body)
    assert again.status_code == 200 and again.json()["data"]["outcome"] == "duplicate"
    # the same event re-sent with different bytes is still one payment
    variant = post_webhook(client, charge_success(ref, extra="noise"))
    assert variant.status_code == 200
    assert variant.json()["data"]["outcome"] in ("duplicate", "already_applied")
    o = order_of(db, d["id"])
    assert len(ledger(db, o)) == 1 and len(mail.sent) == 1


def test_the_webhook_body_is_never_trusted_for_the_amount(client, db, w, ps):
    d = w.book().json()["data"]
    ref = d["order"]["reference"]
    ps.pay(ref)
    ps.amount_override = 100  # Paystack says it took N1.00, whatever the body claims
    r = post_webhook(client, charge_success(ref, amount=1_000_000))
    assert r.status_code == 200 and r.json()["data"]["outcome"] == "payment_mismatch"
    assert order_of(db, d["id"]).status == OrderStatus.pending  # never booked at the wrong amount
    b = db.get(MarketplaceBooking, uuid.UUID(d["id"]))
    assert (b.status, b.closed_reason) == (BookingStatus.expired, "payment_review")


def test_an_unpaid_reference_is_not_applied(client, db, w, ps):
    d = w.book().json()["data"]  # never paid at Paystack
    r = post_webhook(client, charge_success(d["order"]["reference"]))
    assert (
        r.status_code == 409 and r.json()["error"]["code"] == "RETRY_LATER"
    )  # Paystack redelivers
    assert order_of(db, d["id"]).status == OrderStatus.pending


def test_an_unknown_marketplace_reference_is_ignored(client, db, w, ps):
    ref = "mp_" + "a" * 20
    ps.tx[ref] = {"amount": 5, "email": "x@example.com"}
    ps.pay(ref)
    r = post_webhook(client, charge_success(ref))
    assert r.status_code == 200 and r.json()["data"]["outcome"] == "ignored_unknown_reference"


def test_a_paystack_outage_makes_the_webhook_retryable(client, db, w, ps):
    from app.db.models.marketplace import MarketplaceWebhookEvent

    d = w.book().json()["data"]
    ref = d["order"]["reference"]
    ps.pay(ref)
    ps.down = True
    r = post_webhook(client, charge_success(ref))
    assert r.status_code == 502  # Paystack will retry
    # Paystack is asked BEFORE anything is recorded, so the retry can't be mistaken for a duplicate.
    assert db.query(MarketplaceWebhookEvent).count() == 0
    # (billing's own dedupe row lives in the request's transaction, which production rolls back;
    # the shared test session doesn't, so the retry itself is exercised through verify-payment.)
    ps.down = False
    ok = client.post(f"{M}/bookings/{d['id']}/verify-payment", headers=w.h())
    assert ok.json()["data"]["outcome"] == "paid"


def test_verify_payment_and_the_webhook_apply_once_between_them(client, db, w, ps, mail):
    d = w.book().json()["data"]
    ref = d["order"]["reference"]
    before = client.post(f"{M}/bookings/{d['id']}/verify-payment", headers=w.h())
    assert (
        before.status_code == 200 and before.json()["data"]["outcome"] == "payment_not_successful"
    )
    ps.pay(ref)
    paid = client.post(f"{M}/bookings/{d['id']}/verify-payment", headers=w.h())
    assert (
        paid.json()["data"]["outcome"] == "paid"
        and paid.json()["data"]["status"] == "awaiting_provider"
    )
    again = client.post(f"{M}/bookings/{d['id']}/verify-payment", headers=w.h())
    assert again.json()["data"]["outcome"] == "already_applied"
    hook = post_webhook(client, charge_success(ref))
    assert hook.json()["data"]["outcome"] in ("already_applied", "duplicate")
    assert len(ledger(db, order_of(db, d["id"]))) == 1 and len(mail.sent) == 1


def test_verify_payment_is_scoped_to_the_callers_workspace(client, db, w, ps):
    d = w.book().json()["data"]
    r = client.post(f"{M}/bookings/{d['id']}/verify-payment", headers=w.h(w.other, w.other_ws))
    assert r.status_code == 404


def test_money_after_the_hold_lapsed_still_confirms_if_the_slot_is_free(client, db, w, ps):
    d = w.book().json()["data"]
    row = db.get(MarketplaceBooking, uuid.UUID(d["id"]))
    row.slot_hold_expires_at = datetime.now(UTC) - timedelta(minutes=5)
    db.flush()
    ps.pay(d["order"]["reference"])
    r = post_webhook(client, charge_success(d["order"]["reference"]))
    assert r.json()["data"]["outcome"] == "paid"
    assert db.get(MarketplaceBooking, uuid.UUID(d["id"])).status == BookingStatus.awaiting_provider


def test_money_after_the_slot_was_taken_is_refunded_automatically(client, db, w, ps):
    slot = w.slots()[0]
    late = w.book(slot=slot).json()["data"]
    db.get(MarketplaceBooking, uuid.UUID(late["id"])).slot_hold_expires_at = datetime.now(
        UTC
    ) - timedelta(minutes=1)
    db.flush()
    winner_id, _ = paid_booking(
        w, ps, slot=slot, who=w.other, ws=w.other_ws
    )  # another founder took it
    ps.pay(late["order"]["reference"])
    r = post_webhook(client, charge_success(late["order"]["reference"]))
    assert r.json()["data"]["outcome"] == "paid_then_refund_requested"
    o = order_of(db, late["id"])
    assert o.status == OrderStatus.paid and o.refund_due_minor == 1_000_000
    out = orders.process_refund(db, o.id)
    assert out == "refunded"
    db.refresh(o)
    assert o.status == OrderStatus.refunded and o.refunded_minor == 1_000_000
    assert ps.refunds == [{"transaction": late["order"]["reference"], "amount": 1_000_000}]
    assert db.get(MarketplaceBooking, uuid.UUID(late["id"])).status == BookingStatus.expired
    assert (
        db.get(MarketplaceBooking, uuid.UUID(winner_id)).status == BookingStatus.awaiting_provider
    )


def test_money_for_a_cancelled_booking_is_refunded(client, db, w, ps):
    d = w.book().json()["data"]
    cancel = client.post(f"{M}/bookings/{d['id']}/cancel", headers=w.h())
    assert cancel.status_code == 200 and cancel.json()["data"]["status"] == "cancelled"
    assert order_of(db, d["id"]).status == OrderStatus.failed
    ps.pay(d["order"]["reference"])  # the founder paid in another tab before the cancel registered
    r = post_webhook(client, charge_success(d["order"]["reference"]))
    assert r.json()["data"]["outcome"] == "paid_then_refund_requested"
    o = order_of(db, d["id"])
    assert o.refund_due_minor == 1_000_000
    assert db.get(MarketplaceBooking, uuid.UUID(d["id"])).status == BookingStatus.cancelled


# ---------------------------------------------------------------- saved card


def _card(db, ws):
    pm = BillingPaymentMethod(
        startup_id=ws.id,
        authorization_code="AUTH_unit_1",
        signature="SIG_unit_1",
        brand="visa",
        last4="4081",
        exp_month="12",
        exp_year="2030",
        is_default=True,
    )
    db.add(pm)
    db.flush()
    return pm


def test_saved_card_needs_a_card(client, db, w):
    r = w.book(pay_with="saved_card")
    assert r.status_code == 422 and r.json()["error"]["code"] == "NO_SAVED_CARD"
    assert db.query(MarketplaceBooking).count() == 0


def test_saved_card_rebooking_is_one_click(client, db, w, ps, mail):
    _card(db, w.ws)
    chk = client.get(f"{M}/checkout", headers=w.h()).json()["data"]
    assert chk["saved_card"] == {"brand": "visa", "last4": "4081"}
    r = w.book(pay_with="saved_card")
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    assert d["status"] == "awaiting_provider" and d["order"]["status"] == "paid"
    assert ps.charges == [
        {"reference": d["order"]["reference"], "amount": 1_000_000, "auth": "AUTH_unit_1"}
    ]
    assert len(ledger(db, order_of(db, d["id"]))) == 1 and len(mail.sent) == 1


def test_a_declined_saved_card_cancels_the_hold_and_charges_nothing(client, db, w, ps, mail):
    _card(db, w.ws)
    ps.decline_charges = True
    slot = w.slots()[0]
    r = w.book(pay_with="saved_card", slot=slot)
    assert r.status_code == 402 and r.json()["error"]["code"] == "CARD_DECLINED"
    row = db.query(MarketplaceBooking).one()
    assert row.status == BookingStatus.cancelled and row.closed_reason == "card_declined"
    o = db.query(MarketplaceOrder).one()
    assert o.status == OrderStatus.failed and ledger(db, o) == []
    assert slot in w.slots()  # the hold is released
    assert mail.sent == []


# ---------------------------------------------------------------- cancelling


def test_cancelling_an_unpaid_booking_releases_the_hold_without_a_refund(client, db, w, ps):
    slot = w.slots()[0]
    d = w.book(slot=slot).json()["data"]
    r = client.post(f"{M}/bookings/{d['id']}/cancel", headers=w.h())
    assert r.status_code == 200 and r.json()["data"]["status"] == "cancelled"
    assert slot in w.slots()
    assert ps.refunds == [] and order_of(db, d["id"]).refund_due_minor == 0


def test_cancelling_a_paid_booking_before_acceptance_refunds_in_full(client, db, w, ps, mail):
    bid, ref = paid_booking(w, ps)
    mail.sent.clear()
    r = client.post(f"{M}/bookings/{bid}/cancel", headers=w.h())
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["status"] == "cancelled" and d["closed_reason"] == "founder_cancelled"
    assert d["order"]["status"] == "refunded" and d["order"]["refunded_minor"] == 1_000_000
    assert ps.refunds == [{"transaction": ref, "amount": 1_000_000}]
    o = order_of(db, bid)
    kinds = sorted((e.kind.value, e.amount_minor) for e in ledger(db, o))
    assert kinds == [("charge", 1_000_000), ("refund", -1_000_000)]
    assert orders.ledger_balance(db, o.id) == o.amount_minor - o.refunded_minor == 0
    assert "requested a full refund" in mail.sent[0].html
    again = client.post(f"{M}/bookings/{bid}/cancel", headers=w.h())
    assert again.status_code == 409 and again.json()["error"]["code"] == "CANCEL_NOT_ALLOWED"


def test_cancelling_someone_elses_booking_is_a_404(client, db, w, ps):
    d = w.book().json()["data"]
    r = client.post(f"{M}/bookings/{d['id']}/cancel", headers=w.h(w.other, w.other_ws))
    assert r.status_code == 404


# ---------------------------------------------------------------- final review: money must never be lost


def test_a_payment_for_the_wrong_amount_is_flagged_for_staff_and_not_lost(
    client, db, w, ps, moderator
):
    from app.db.models.audit import AuditLog

    d = w.book().json()["data"]
    ref = d["order"]["reference"]
    ps.pay(ref)
    ps.amount_override = 1_020_000  # Paystack took N10,200: fees were passed on to the customer
    r = post_webhook(client, charge_success(ref))
    assert r.status_code == 200 and r.json()["data"]["outcome"] == "payment_mismatch"
    o = order_of(db, d["id"])
    assert o.status == OrderStatus.pending  # never booked against the wrong amount...
    assert (o.attention_reason, o.attention_amount_minor) == (
        "payment_mismatch",
        1_020_000,
    )  # ...but flagged
    assert o.attention_at is not None
    audit = (
        db.query(AuditLog)
        .filter_by(action="marketplace.order.payment_mismatch", entity_id=o.id)
        .one()
    )
    assert (
        audit.details["expected_minor"] == 1_000_000
        and audit.details["paystack_minor"] == 1_020_000
    )
    again = post_webhook(client, charge_success(ref, extra="redelivery"))
    assert again.json()["data"]["outcome"] in ("payment_mismatch", "duplicate")
    assert (
        db.query(AuditLog)
        .filter_by(action="marketplace.order.payment_mismatch", entity_id=o.id)
        .count()
        == 1
    )
    queue = client.get("/api/v1/admin/marketplace/orders/attention", headers=hdr(moderator))
    assert queue.status_code == 200
    row = next(x for x in queue.json()["data"]["orders"] if x["id"] == str(o.id))
    assert (
        row["attention_reason"] == "payment_mismatch"
        and row["paystack_reported_minor"] == 1_020_000
    )
    assert row["reference"] == ref and "brief" not in str(row)


def test_the_attention_queue_is_staff_only_and_lists_stuck_refunds(client, db, w, ps, moderator):
    from app.db.models.enums import StaffRole
    from tests.api.test_staff_users import mk_staff

    bid, _ = paid_booking(w, ps)
    o = order_of(db, bid)
    o.refund_failed_at = datetime.now(UTC)
    db.flush()
    url = "/api/v1/admin/marketplace/orders/attention"
    assert client.get(url).status_code == 404
    assert client.get(url, headers=hdr(w.founder)).status_code == 404
    support = mk_staff(db, StaffRole.admin, ("support",))
    assert client.get(url, headers=hdr(support)).status_code == 403
    ids = [x["id"] for x in client.get(url, headers=hdr(moderator)).json()["data"]["orders"]]
    assert str(o.id) in ids


def test_a_webhook_is_retried_when_paystack_refuses_to_verify(client, db, w, ps):
    d = w.book().json()["data"]
    ref = d["order"]["reference"]
    ps.pay(ref)
    ps.verify_rejects = True  # e.g. a 429 or any 4xx that isn't "no such transaction"
    r = post_webhook(client, charge_success(ref))
    assert r.status_code == 502  # non-2xx: Paystack will deliver it again
    assert order_of(db, d["id"]).status == OrderStatus.pending
    ps.verify_rejects = False
    assert post_webhook(client, charge_success(ref)).json()["data"]["outcome"] == "paid"


def test_a_signed_event_for_a_transaction_paystack_has_never_seen_is_answered_200(
    client, db, w, ps
):
    d = w.book().json()["data"]
    ref = d["order"]["reference"]
    del ps.tx[ref]  # Paystack answers 404: it has no such transaction
    r = post_webhook(client, charge_success(ref))
    assert r.status_code == 200 and r.json()["data"]["outcome"] == "unverified"
    assert order_of(db, d["id"]).status == OrderStatus.pending


def test_an_unpaid_hold_that_was_in_fact_paid_is_applied_not_failed(client, db, w, ps):
    from app.services.marketplace import timers

    d = w.book().json()["data"]
    ps.pay(d["order"]["reference"])  # charged, but the webhook never arrived and the tab was closed
    res = timers.tick(db, datetime.now(UTC) + timedelta(minutes=16))
    assert res["expired_unpaid"] == 0
    b = db.get(MarketplaceBooking, uuid.UUID(d["id"]))
    assert b.status == BookingStatus.awaiting_provider
    o = order_of(db, d["id"])
    assert o.status == OrderStatus.paid and len(ledger(db, o)) == 1


def test_an_unpaid_hold_is_left_alone_when_paystack_cannot_be_asked(client, db, w, ps):
    from app.services.marketplace import timers

    d = w.book().json()["data"]
    ps.down = True
    assert timers.tick(db, datetime.now(UTC) + timedelta(minutes=16))["expired_unpaid"] == 0
    assert db.get(MarketplaceBooking, uuid.UUID(d["id"])).status == BookingStatus.pending_payment
    ps.down = False
    assert timers.tick(db, datetime.now(UTC) + timedelta(minutes=16))["expired_unpaid"] == 1
    assert db.get(MarketplaceBooking, uuid.UUID(d["id"])).status == BookingStatus.expired


def test_a_saved_card_charge_that_is_not_over_is_neither_confirmed_nor_cancelled(client, db, w, ps):
    _card(db, w.ws)
    ps.charge_status = "send_otp"  # the bank wants an OTP: the charge is still open
    r = w.book(pay_with="saved_card")
    assert r.status_code == 502 and r.json()["error"]["code"] == "PAYMENT_UNCONFIRMED"
    b = db.query(MarketplaceBooking).one()
    assert b.status == BookingStatus.pending_payment  # the hold stays; it was NOT cancelled
    o = db.query(MarketplaceOrder).one()
    ps.pay(o.paystack_reference)  # the OTP is entered and the charge succeeds
    assert (
        post_webhook(client, charge_success(o.paystack_reference)).json()["data"]["outcome"]
        == "paid"
    )
    assert db.get(MarketplaceBooking, b.id).status == BookingStatus.awaiting_provider


# ---------------------------------------------------------------- the second gate on real charging


def test_production_needs_the_second_flag_before_any_booking(client, db, w, ps, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    refused = w.book()
    assert (
        refused.status_code == 403
        and refused.json()["error"]["code"] == "LIVE_CHARGING_NOT_CONFIRMED"
    )
    assert db.query(MarketplaceBooking).count() == 0 and ps.tx == {}  # Paystack was never called
    assert client.get(f"{M}/checkout", headers=w.h()).json()["data"]["payments_enabled"] is False
    monkeypatch.setattr(settings, "MARKETPLACE_LIVE_CHARGING_CONFIRMED", True)
    assert w.book().status_code == 201
    assert client.get(f"{M}/checkout", headers=w.h()).json()["data"]["payments_enabled"] is True


def test_a_live_key_outside_production_is_refused_too(client, db, w, ps, monkeypatch):
    monkeypatch.setattr(settings, "PAYSTACK_SECRET_KEY", "sk_live_not_a_real_key")
    r = w.book()
    assert r.status_code == 403 and r.json()["error"]["code"] == "LIVE_CHARGING_NOT_CONFIRMED"
    monkeypatch.setattr(settings, "MARKETPLACE_PAYMENTS_ENABLED", False)
    assert (
        w.book().json()["error"]["code"] == "PAYMENTS_DISABLED"
    )  # the first gate still comes first


# ---------------------------------------------------------------- a double submit is a replay


def test_a_double_submit_that_races_the_first_replay_check_is_still_a_replay(
    client, db, w, monkeypatch
):
    from app.services.marketplace import bookings as bookings_svc

    slot = w.slots()[0]
    first = w.book(slot=slot, key="double-submit-001")
    assert first.status_code == 201
    real = bookings_svc.find_replay
    calls = {"n": 0}

    def blind_the_first_look(*a, **kw):  # the winner had not committed when the first check ran
        calls["n"] += 1
        return None if calls["n"] == 1 else real(*a, **kw)

    monkeypatch.setattr(bookings_svc, "find_replay", blind_the_first_look)
    second = w.book(slot=slot, key="double-submit-001")
    assert second.status_code == 200 and second.json()["data"]["replayed"] is True
    assert second.json()["data"]["id"] == first.json()["data"]["id"]  # not a 409 "slot taken"
    assert calls["n"] >= 2 and db.query(MarketplaceBooking).count() == 1


# ---------------------------------------------------------------- email copy and the audit trail


def test_a_late_payment_for_a_slot_that_was_taken_tells_the_founder(client, db, w, ps, mail):
    slot = w.slots()[0]
    late = w.book(slot=slot).json()["data"]
    db.get(MarketplaceBooking, uuid.UUID(late["id"])).slot_hold_expires_at = datetime.now(
        UTC
    ) - timedelta(minutes=1)
    db.flush()
    paid_booking(w, ps, slot=slot, who=w.other, ws=w.other_ws)
    mail.sent.clear()
    ps.pay(late["order"]["reference"])
    assert (
        post_webhook(client, charge_success(late["order"]["reference"])).json()["data"]["outcome"]
        == "paid_then_refund_requested"
    )
    assert [m.to for m in mail.sent] == [w.founder.email]
    html = mail.sent[0].html
    assert "requested a full refund" in html and "taken" in html and "never charged" not in html


def test_closing_emails_never_claim_a_refund_that_has_only_been_requested(client, db, w, ps, mail):
    from app.services.marketplace.emails import booking_closed_email

    for reason in (
        "founder_cancelled",
        "provider_declined",
        "provider_no_response",
        "slot_passed",
        "slot_no_longer_available",
    ):
        html = booking_closed_email(
            "a@example.com", provider="Ngozi", title="Review", reason=reason
        ).html
        assert "requested a full refund" in html, reason
        assert "You were refunded" not in html and "never charged" not in html, reason


def test_failed_checkouts_accepts_and_declines_leave_audit_rows_with_the_workspace(
    client, db, w, ps
):
    from app.db.models.audit import AuditLog

    _card(db, w.ws)
    ps.decline_charges = True
    w.book(pay_with="saved_card", slot=w.slots()[0])
    cancelled = db.query(AuditLog).filter_by(action="marketplace.booking.cancelled").one()
    assert cancelled.startup_id == w.ws.id and cancelled.details == {"why": "card_declined"}
    ps.decline_charges = False
    a, _ = paid_booking(w, ps, slot=w.slots()[1])
    b, _ = paid_booking(w, ps, listing=w.package)
    client.post(f"{P}/bookings/{a}/accept", headers=hdr(w.expert))
    client.post(f"{P}/bookings/{b}/decline", headers=hdr(w.expert))
    for action in ("marketplace.booking.accepted", "marketplace.booking.declined"):
        row = db.query(AuditLog).filter_by(action=action).one()
        assert row.startup_id == w.ws.id, action


@pytest.fixture
def moderator(db):
    from app.db.models.enums import StaffRole
    from tests.api.test_staff_users import mk_staff

    return mk_staff(db, StaffRole.admin, ("moderation",))


# ---------------------------------------------------------------- round three of the Opus review


def test_a_charge_that_succeeded_but_cannot_be_verified_is_unconfirmed_never_nothing_charged(
    client, db, w, ps
):
    _card(db, w.ws)
    ps.charge_status = "success"
    ps.verify_rejects = True  # the charge went through; verify answers a 4xx / 429
    r = w.book(pay_with="saved_card")
    assert r.status_code == 502 and r.json()["error"]["code"] == "PAYMENT_UNCONFIRMED"
    assert "Nothing was charged" not in r.text and "Try again" not in r.text
    b = db.query(MarketplaceBooking).one()
    assert b.status == BookingStatus.pending_payment  # the hold is kept
    assert db.query(MarketplaceOrder).one().status == OrderStatus.pending
    ps.verify_rejects = False
    ps.pay(db.query(MarketplaceOrder).one().paystack_reference)
    verified = client.post(f"{M}/bookings/{b.id}/verify-payment", headers=w.h())
    assert verified.json()["data"]["outcome"] == "paid"


def test_the_verify_endpoint_answers_unconfirmed_when_paystack_cannot_be_asked(client, db, w, ps):
    d = w.book().json()["data"]
    ps.pay(d["order"]["reference"])
    for knob in ("verify_rejects", "down"):
        setattr(ps, knob, True)
        r = client.post(f"{M}/bookings/{d['id']}/verify-payment", headers=w.h())
        assert r.status_code == 502 and r.json()["error"]["code"] == "PAYMENT_UNCONFIRMED", knob
        assert r.json()["error"]["field_errors"][0]["message"] == d["id"]
        setattr(ps, knob, False)
    assert order_of(db, d["id"]).status == OrderStatus.pending


def test_a_mismatched_payment_closes_the_checkout_frees_the_slot_and_tells_the_founder(
    client, db, w, ps, mail, monkeypatch
):
    from app.services.billing import paystack
    from app.services.marketplace import timers

    slot = w.slots()[0]
    d = w.book(slot=slot).json()["data"]
    ref = d["order"]["reference"]
    ps.pay(ref)
    ps.amount_override = 1_020_000
    mail.sent.clear()
    assert post_webhook(client, charge_success(ref)).json()["data"]["outcome"] == "payment_mismatch"
    b = db.get(MarketplaceBooking, uuid.UUID(d["id"]))
    assert (b.status, b.closed_reason) == (BookingStatus.expired, "payment_review")
    assert order_of(db, d["id"]).attention_reason == "payment_mismatch"
    assert [m.to for m in mail.sent] == [w.founder.email]
    assert "doesn't match the price" in mail.sent[0].html and "pay again" in mail.sent[0].html
    assert w.book(slot=slot, who=w.other, ws=w.other_ws).status_code == 201  # slot released

    # the timer never looks at it again
    def boom(reference):
        raise AssertionError("a flagged order must not be re-verified")

    monkeypatch.setattr(paystack, "verify", boom)
    timers.tick(db, datetime.now(UTC) + timedelta(hours=3))
    founder_view = client.get(f"{M}/bookings/{d['id']}", headers=w.h()).json()["data"]
    assert founder_view["status"] == "expired" and founder_view["closed_reason"] == "payment_review"


def test_a_hold_paystack_never_answers_for_is_closed_after_the_grace_and_can_still_revive(
    client, db, w, ps, mail
):
    from app.db.models.audit import AuditLog
    from app.services.marketplace import timers

    d = w.book().json()["data"]
    ref = d["order"]["reference"]
    ps.verify_rejects = True  # e.g. a 400 "reference not found" instead of a 404
    b = db.get(MarketplaceBooking, uuid.UUID(d["id"]))
    hold_end = b.slot_hold_expires_at
    assert timers.tick(db, hold_end + timedelta(minutes=30))["expired_unpaid"] == 0  # patience
    assert db.get(MarketplaceBooking, b.id).status == BookingStatus.pending_payment
    mail.sent.clear()
    assert timers.tick(db, hold_end + timedelta(minutes=61))["expired_unpaid"] == 1  # bounded
    assert db.get(MarketplaceBooking, b.id).status == BookingStatus.expired
    assert order_of(db, d["id"]).status == OrderStatus.failed
    audit = db.query(AuditLog).filter_by(action="marketplace.booking.expired", entity_id=b.id).one()
    assert audit.details["verified"] is False
    assert [m.to for m in mail.sent] == [w.founder.email] and "pay again" in mail.sent[0].html
    # the money, if it had moved, is never lost: a late payment still revives the booking
    ps.verify_rejects = False
    ps.pay(ref)
    assert post_webhook(client, charge_success(ref)).json()["data"]["outcome"] == "paid"
    assert db.get(MarketplaceBooking, b.id).status == BookingStatus.awaiting_provider


def test_one_failing_hold_does_not_abort_the_rest_of_the_tick(client, db, w, ps, monkeypatch):
    from app.services.marketplace import timers

    first = w.book(slot=w.slots()[0]).json()["data"]
    second = w.book(slot=w.slots()[1]).json()["data"]
    for d in (first, second):
        ps.pay(d["order"]["reference"])
    real = orders.apply_payment

    def flaky(db_, reference, tx):
        if reference == first["order"]["reference"]:
            raise RuntimeError("billing not configured")
        return real(db_, reference, tx)

    monkeypatch.setattr(orders, "apply_payment", flaky)
    timers.tick(db, datetime.now(UTC) + timedelta(minutes=16))
    assert (
        db.get(MarketplaceBooking, uuid.UUID(second["id"])).status
        == BookingStatus.awaiting_provider
    )
    assert (
        db.get(MarketplaceBooking, uuid.UUID(first["id"])).status == BookingStatus.pending_payment
    )


def test_a_first_booking_double_submit_does_not_trip_the_consent_row(client, db, w, monkeypatch):
    from app.services.marketplace import bookings as bookings_svc

    db.add(
        MarketplaceConsent(user_id=w.founder.id, kind="marketplace_terms", version="draft-2026-11")
    )
    db.flush()  # the twin request already recorded consent...
    monkeypatch.setattr(bookings_svc, "terms_accepted", lambda *_a, **_k: False)  # ...unseen by us
    r = w.book()
    assert r.status_code == 201, r.text
    assert db.query(MarketplaceConsent).filter_by(user_id=w.founder.id).count() == 1


def test_a_late_refund_webhook_for_a_refund_already_on_the_books_is_not_booked_twice(
    client, db, w, ps
):
    bid, ref = paid_booking(w, ps)
    o = order_of(db, bid)
    orders.request_refund(db, o, 300_000, "goodwill")
    booking = db.get(MarketplaceBooking, o.booking_id)
    orders.record_refund_sent(
        db, o, booking, 300_000, "5551234"
    )  # our answer carried Paystack's id
    orders.request_refund(db, o, 300_000, "more goodwill")  # a later refund, send unanswered
    o.refund_inflight_since = datetime.now(UTC)
    db.flush()
    late = {
        "event": "refund.processed",
        "data": {
            "transaction_reference": ref,
            "id": "5551234",
            "refund_reference": "rf_first",
            "amount": 300_000,
        },
    }
    assert post_webhook(client, late).json()["data"]["outcome"] == "refund_noted"
    db.refresh(o)
    assert o.refunded_minor == 300_000 and o.refund_due_minor == 300_000
