"""Marketplace slice D: delivery and acceptance, the fulfilment timers, brief / delivery files,
"attach from Documents", the Calendar meeting, founder notifications, and the three slice C lows."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.db.models.audit import AuditLog
from app.db.models.document import DocumentGrant
from app.db.models.enums import (
    BookingStatus,
    LedgerKind,
    MembershipRole,
    OrderStatus,
    StaffRole,
)
from app.db.models.job import Job
from app.db.models.marketplace import (
    MarketplaceBooking,
    MarketplaceBookingDocument,
    MarketplaceDelivery,
    MarketplaceFile,
    MarketplaceLedgerEntry,
)
from app.services.marketplace import closing, deliveries, orders, timers
from tests.api.test_staff_users import hdr, member, mk_staff, mk_user
from tests.marketplace_booking_helpers import (
    M,
    P,
    World,
    charge_success,
    install_fake_paystack,
    install_mailbox,
    post_webhook,
)
from tests.marketplace_fulfilment_helpers import (
    PDF,
    PNG,
    accepted,
    booking,
    docx_bytes,
    end_session,
    events,
    file_id,
    founder_file,
    mk_doc,
    notes,
    order_of,
    paid,
    provider_file,
    sent_to,
    upload,
)

ADMIN = "/api/v1/admin/marketplace"


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


@pytest.fixture()
def moderator(db):
    return mk_staff(db, StaffRole.admin, ("moderation",))


def deliver(w, bid, message="Here is the finished brand kit.", files=None, expect=200):
    r = w.client.post(
        f"{P}/bookings/{bid}/deliver",
        json={"message": message, "file_ids": files or []},
        headers=hdr(w.expert),
    )
    assert r.status_code == expect, r.text
    return r


def delivered_package(w, ps, files=None):
    bid = accepted(w, ps, listing=w.package)
    deliver(w, bid, files=files)
    return bid


# ================================================================ delivering a package


def test_a_package_is_delivered_with_files_and_the_client_gets_seven_days(client, db, w, ps, mail):
    bid = accepted(w, ps, listing=w.package)
    fid = provider_file(w, "delivery")
    before = datetime.now(UTC)
    r = deliver(w, bid, "Final files attached.", [fid])
    d = r.json()["data"]
    assert d["status"] == "delivered"
    assert (
        timedelta(days=7) - timedelta(minutes=1)
        < (datetime.fromisoformat(d["auto_release_at"]) - before)
        < timedelta(days=7, minutes=1)
    )
    assert d["deliveries"][0]["round"] == 1 and d["deliveries"][0]["decision"] is None
    assert d["deliveries"][0]["files"][0]["id"] == fid
    mine = client.get(f"{M}/bookings/{bid}", headers=w.h()).json()["data"]
    assert (
        mine["status"] == "delivered"
        and mine["deliveries"][0]["message"] == "Final files attached."
    )
    assert mine["deliveries"][0]["files"][0]["url"] == f"/api/v1/marketplace/files/{fid}/content"
    assert len(events("marketplace.delivery.submitted", bid)) == 1
    assert booking(db, bid).delivered_at is not None


def test_the_founder_is_told_in_the_app_when_work_is_delivered_and_no_mentor_is(client, db, w, ps):
    mentor = mk_user(db)
    member(db, w.ws, mentor, MembershipRole.mentor)
    bid = delivered_package(w, ps)
    n = notes(db, w.founder, "marketplace.delivery.submitted")
    assert len(n) == 1 and n[0].title == "Ngozi Eze delivered your order"
    assert n[0].body == "Review delivery" and n[0].data["booking_id"] == bid
    assert notes(db, w.mate, "marketplace.delivery.submitted") == []  # team members read, not told
    assert notes(db, mentor, "marketplace.delivery.submitted") == []
    assert notes(db, w.other, "marketplace.delivery.submitted") == []


def test_only_the_bookings_own_expert_can_deliver(client, db, w, ps):
    from tests.api.test_marketplace_listings import mk_provider

    bid = accepted(w, ps, listing=w.package)
    other, _ = mk_provider(db, name="Other Expert")
    body = {"message": "hi", "file_ids": []}
    assert (
        client.post(f"{P}/bookings/{bid}/deliver", json=body, headers=hdr(other)).status_code == 404
    )
    assert client.post(f"{P}/bookings/{bid}/deliver", json=body).status_code == 401
    for who in (w.founder, w.mate):
        r = client.post(f"{P}/bookings/{bid}/deliver", json=body, headers=hdr(who))
        assert r.status_code == 403 and r.json()["error"]["code"] == "NOT_A_PROVIDER"
    assert booking(db, bid).status == BookingStatus.in_progress


@pytest.mark.parametrize(
    ("setup", "code"),
    [("awaiting", "INVALID_STATE"), ("consultation", "INVALID_STATE")],
)
def test_delivering_in_the_wrong_state_or_type_is_refused(client, db, w, ps, setup, code):
    if setup == "awaiting":
        bid = paid(w, ps, listing=w.package)
    else:
        bid = accepted(w, ps)
    r = deliver(w, bid, expect=409)
    assert r.json()["error"]["code"] == code
    assert booking(db, bid).status in (BookingStatus.awaiting_provider, BookingStatus.confirmed)


def test_delivering_twice_does_not_make_a_second_round(client, db, w, ps):
    bid = delivered_package(w, ps)
    r = deliver(w, bid, "again", expect=409)
    assert r.json()["error"]["code"] == "INVALID_STATE"
    assert db.query(MarketplaceDelivery).filter_by(booking_id=uuid.UUID(bid)).count() == 1


def test_a_delivery_needs_a_message_and_its_files_must_be_the_experts_own(client, db, w, ps):
    bid = accepted(w, ps, listing=w.package)
    empty = client.post(
        f"{P}/bookings/{bid}/deliver", json={"message": "  ", "file_ids": []}, headers=hdr(w.expert)
    )
    assert empty.status_code == 422
    theirs = founder_file(w, "brief")
    r = deliver(w, bid, files=[theirs], expect=422)  # the founder's file, not the expert's
    assert r.json()["error"]["code"] == "INVALID_FILE"
    wrong_purpose = provider_file(w, "message")
    assert (
        deliver(w, bid, files=[wrong_purpose], expect=422).json()["error"]["code"] == "INVALID_FILE"
    )
    assert (
        deliver(w, bid, files=[str(uuid.uuid4())], expect=422).json()["error"]["code"]
        == "INVALID_FILE"
    )
    assert booking(db, bid).status == BookingStatus.in_progress
    ok = provider_file(w, "delivery")
    deliver(w, bid, files=[ok, ok], expect=422)  # the same file twice
    deliver(w, bid, files=[ok])


# ================================================================ accepting a delivery


def test_accepting_completes_the_booking_records_it_and_moves_no_money(client, db, w, ps, mail):
    bid = delivered_package(w, ps)
    o = order_of(db, bid)
    ledger_before = sorted(
        (e.kind, e.amount_minor) for e in db.query(MarketplaceLedgerEntry).filter_by(order_id=o.id)
    )
    mail.sent.clear()
    r = client.post(f"{M}/bookings/{bid}/accept-delivery", headers=w.h())
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["replayed"] is False and d["status"] == "completed"
    assert d["deliveries"][0]["decision"] == "accepted" and d["deliveries"][0]["decided_at"]
    assert d["auto_release_at"] is None
    db.refresh(o)
    assert o.status == OrderStatus.paid and o.refunded_minor == 0
    assert (
        ledger_before
        == sorted(
            (e.kind, e.amount_minor)
            for e in db.query(MarketplaceLedgerEntry).filter_by(order_id=o.id)
        )
        == [(LedgerKind.charge, 5_000_000)]
    )
    hooks = events("marketplace.delivery.accepted", bid)
    assert len(hooks) == 1 and hooks[0]["auto_released"] is False
    assert [m.subject for m in sent_to(mail, w.expert.email)] == ["Delivery accepted: Logo sprint"]
    assert db.query(AuditLog).filter_by(action="marketplace.delivery.accepted").count() >= 1


def test_accepting_twice_is_a_harmless_replay(client, db, w, ps, mail):
    bid = delivered_package(w, ps)
    client.post(f"{M}/bookings/{bid}/accept-delivery", headers=w.h())
    mail.sent.clear()
    again = client.post(f"{M}/bookings/{bid}/accept-delivery", headers=w.h())
    assert again.status_code == 200 and again.json()["data"]["replayed"] is True
    assert len(events("marketplace.delivery.accepted", bid)) == 1
    assert mail.sent == []
    assert (
        db.query(MarketplaceDelivery).filter_by(booking_id=uuid.UUID(bid)).one().decision
        == "accepted"
    )


def test_accepting_before_anything_is_delivered_is_refused(client, db, w, ps):
    bid = accepted(w, ps, listing=w.package)
    r = client.post(f"{M}/bookings/{bid}/accept-delivery", headers=w.h())
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATE"
    assert events("marketplace.delivery.accepted", bid) == []


@pytest.mark.parametrize("path", ["accept-delivery", "request-changes"])
def test_only_the_founder_of_that_workspace_can_answer_a_delivery(client, db, w, ps, path):
    bid = delivered_package(w, ps)
    body = {"reason": "Please change the logo colours."} if path == "request-changes" else None
    url = f"{M}/bookings/{bid}/{path}"
    mentor = mk_user(db)
    member(db, w.ws, mentor, MembershipRole.mentor)
    assert client.post(url, json=body).status_code == 401
    assert client.post(url, json=body, headers=w.h(w.mate)).status_code == 403  # team member reads
    assert client.post(url, json=body, headers=w.h(mentor)).status_code == 403
    assert client.post(url, json=body, headers=w.h(w.other, w.other_ws)).status_code == 404
    assert client.post(url, json=body, headers=hdr(w.expert)).status_code in (403, 404, 422)
    assert booking(db, bid).status == BookingStatus.delivered
    assert client.post(url, json=body, headers=w.h()).status_code == 200


# ================================================================ requesting changes


def test_requesting_changes_reopens_the_work_and_counts_the_round(client, db, w, ps, mail):
    bid = delivered_package(w, ps)
    mail.sent.clear()
    reason = "The <b>logo</b> is too dark & needs a lighter variant."
    r = client.post(f"{M}/bookings/{bid}/request-changes", json={"reason": reason}, headers=w.h())
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["status"] == "in_progress" and d["revisions_used"] == 1 and d["revisions_left"] == 1
    assert d["auto_release_at"] is None
    assert d["deliveries"][0]["decision"] == "changes_requested"
    assert d["deliveries"][0]["decision_note"] == reason
    mail_to_expert = sent_to(mail, w.expert.email)
    assert len(mail_to_expert) == 1
    assert (
        "&lt;b&gt;logo&lt;/b&gt;" in mail_to_expert[0].html
        and "<b>logo</b>" not in mail_to_expert[0].html
    )
    assert len(events("marketplace.delivery.changes_requested", bid)) == 1
    # the expert resubmits: round 2
    deliver(w, bid, "Lighter variant added.")
    got = client.get(f"{M}/bookings/{bid}", headers=w.h()).json()["data"]
    assert [x["round"] for x in got["deliveries"]] == [1, 2] and got["status"] == "delivered"


def test_the_reason_is_required_and_a_consultation_has_no_revisions(client, db, w, ps):
    bid = delivered_package(w, ps)
    for body in ({}, {"reason": "short"}, {"reason": "x" * 2001}):
        r = client.post(f"{M}/bookings/{bid}/request-changes", json=body, headers=w.h())
        assert r.status_code == 422
    assert booking(db, bid).status == BookingStatus.delivered
    cid = accepted(w, ps, who=w.other, ws=w.other_ws)
    end_session(db, cid)
    client.post(f"{P}/bookings/{cid}/complete", headers=hdr(w.expert))
    r = client.post(
        f"{M}/bookings/{cid}/request-changes",
        json={"reason": "I would like this to be redone."},
        headers=w.h(w.other, w.other_ws),
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "NOT_APPLICABLE"


def test_after_the_included_rounds_the_next_request_points_at_report_a_problem(client, db, w, ps):
    bid = accepted(w, ps, listing=w.package)
    for n in range(2):
        deliver(w, bid, f"round {n + 1}")
        ok = client.post(
            f"{M}/bookings/{bid}/request-changes",
            json={"reason": f"Change number {n + 1} please."},
            headers=w.h(),
        )
        assert ok.status_code == 200
    deliver(w, bid, "round 3")
    r = client.post(
        f"{M}/bookings/{bid}/request-changes",
        json={"reason": "One more change please."},
        headers=w.h(),
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "REVISION_LIMIT_REACHED"
    assert "Report a problem" in r.json()["error"]["message"]
    b = booking(db, bid)
    assert (b.status, b.revisions_used) == (BookingStatus.delivered, 2)
    # accepting is still open
    assert client.post(f"{M}/bookings/{bid}/accept-delivery", headers=w.h()).status_code == 200


def test_the_included_rounds_come_from_the_listing_snapshot(client, db, w, ps):
    w.package.revisions_included = 1
    db.flush()
    bid = accepted(w, ps, listing=w.package)
    w.package.revisions_included = 5  # a later listing edit must not buy more rounds
    db.flush()
    deliver(w, bid)
    one = client.post(
        f"{M}/bookings/{bid}/request-changes", json={"reason": "Change number one."}, headers=w.h()
    )
    assert one.status_code == 200 and one.json()["data"]["revisions_left"] == 0
    deliver(w, bid, "second")
    two = client.post(
        f"{M}/bookings/{bid}/request-changes", json={"reason": "Change number two."}, headers=w.h()
    )
    assert two.status_code == 409 and two.json()["error"]["code"] == "REVISION_LIMIT_REACHED"


def test_asking_for_the_same_changes_twice_is_a_replay_and_emails_once(client, db, w, ps, mail):
    bid = delivered_package(w, ps)
    body = {"reason": "Please change the logo colours."}
    client.post(f"{M}/bookings/{bid}/request-changes", json=body, headers=w.h())
    mail.sent.clear()
    again = client.post(f"{M}/bookings/{bid}/request-changes", json=body, headers=w.h())
    assert again.status_code == 200 and again.json()["data"]["replayed"] is True
    assert booking(db, bid).revisions_used == 1 and mail.sent == []
    other = client.post(
        f"{M}/bookings/{bid}/request-changes",
        json={"reason": "A different request."},
        headers=w.h(),
    )
    assert other.status_code == 409 and other.json()["error"]["code"] == "INVALID_STATE"


# ================================================================ consultations


def test_a_consultation_is_marked_complete_only_after_the_session(client, db, w, ps):
    bid = accepted(w, ps)
    early = client.post(f"{P}/bookings/{bid}/complete", headers=hdr(w.expert))
    assert early.status_code == 409 and early.json()["error"]["code"] == "SESSION_NOT_ENDED"
    b = end_session(db, bid)
    r = client.post(f"{P}/bookings/{bid}/complete", headers=hdr(w.expert))
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "delivered"
    db.refresh(b)
    assert b.auto_release_at == max(b.ends_at, b.delivered_at) + timedelta(hours=48)
    again = client.post(f"{P}/bookings/{bid}/complete", headers=hdr(w.expert))
    assert again.status_code == 409 and again.json()["error"]["code"] == "INVALID_STATE"
    assert db.query(MarketplaceDelivery).filter_by(booking_id=b.id).count() == 1
    assert (
        client.post(f"{M}/bookings/{bid}/accept-delivery", headers=w.h()).json()["data"]["status"]
        == "completed"
    )


def test_complete_is_for_the_experts_own_confirmed_consultations_only(client, db, w, ps):
    from tests.api.test_marketplace_listings import mk_provider

    pkg = accepted(w, ps, listing=w.package)
    r = client.post(f"{P}/bookings/{pkg}/complete", headers=hdr(w.expert))
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATE"
    waiting = paid(w, ps, slot=w.slots()[1])
    end_session(db, waiting)
    r2 = client.post(f"{P}/bookings/{waiting}/complete", headers=hdr(w.expert))
    assert r2.status_code == 409 and r2.json()["error"]["code"] == "INVALID_STATE"  # not accepted
    done = accepted(w, ps, slot=w.slots()[2])
    end_session(db, done)
    other, _ = mk_provider(db, name="Other Expert")
    assert client.post(f"{P}/bookings/{done}/complete", headers=hdr(other)).status_code == 404
    assert client.post(f"{P}/bookings/{done}/complete").status_code == 401
    assert client.post(f"{P}/bookings/{done}/complete", headers=hdr(w.founder)).status_code == 403


# ================================================================ the timers


def test_a_session_nobody_marked_becomes_delivered_then_auto_released(client, db, w, ps, mail):
    bid = accepted(w, ps)
    b = end_session(db, bid, ago=timedelta(hours=2))
    t = datetime.now(UTC)
    res = timers.tick(db, t)
    assert res["consultations_delivered"] == 1 and res["auto_released"] == 0
    db.refresh(b)
    assert b.status == BookingStatus.delivered
    assert b.auto_release_at == t + timedelta(hours=48)
    assert timers.tick(db, t)["consultations_delivered"] == 0  # replay: nothing changes
    assert db.query(MarketplaceDelivery).filter_by(booking_id=b.id).count() == 1
    assert len(notes(db, w.founder, "marketplace.delivery.submitted")) == 1
    # before the window ends: still waiting
    assert timers.tick(db, b.auto_release_at - timedelta(minutes=1))["auto_released"] == 0
    mail.sent.clear()
    res = timers.tick(db, b.auto_release_at + timedelta(minutes=1))
    assert res["auto_released"] == 1
    db.refresh(b)
    assert b.status == BookingStatus.completed and b.auto_release_at is None
    d = db.query(MarketplaceDelivery).filter_by(booking_id=b.id).one()
    assert d.decision == "auto_released" and d.decided_at is not None
    assert len(events("marketplace.delivery.auto_released", bid)) == 1
    hook = events("marketplace.delivery.accepted", bid)
    assert len(hook) == 1 and hook[0]["auto_released"] is True
    assert "accepted automatically" in sent_to(mail, w.expert.email)[0].html
    n = notes(db, w.founder, "marketplace.delivery.auto_released")
    assert len(n) == 1 and n[0].title == "Design critique was accepted automatically"
    assert timers.tick(db, t + timedelta(days=5))["auto_released"] == 0


def test_a_delivered_package_is_auto_released_after_seven_days_and_not_before(client, db, w, ps):
    bid = delivered_package(w, ps)
    b = booking(db, bid)
    assert timers.tick(db, b.delivered_at + timedelta(days=6, hours=23))["auto_released"] == 0
    assert booking(db, bid).status == BookingStatus.delivered
    assert timers.tick(db, b.delivered_at + timedelta(days=7, minutes=1))["auto_released"] == 1
    assert booking(db, bid).status == BookingStatus.completed
    assert (
        db.query(MarketplaceDelivery).filter_by(booking_id=b.id).one().decision == "auto_released"
    )
    # the founder accepting afterwards is a replay of a completed booking, not an error
    r = client.post(f"{M}/bookings/{bid}/accept-delivery", headers=w.h())
    assert r.status_code == 200 and r.json()["data"]["replayed"] is True
    assert r.json()["data"]["status"] == "completed"
    assert len(events("marketplace.delivery.accepted", bid)) == 1  # nothing emitted twice


def test_the_timer_after_an_acceptance_changes_nothing(client, db, w, ps, mail):
    bid = delivered_package(w, ps)
    client.post(f"{M}/bookings/{bid}/accept-delivery", headers=w.h())
    mail.sent.clear()
    assert timers.tick(db, datetime.now(UTC) + timedelta(days=30))["auto_released"] == 0
    assert len(events("marketplace.delivery.accepted", bid)) == 1 and mail.sent == []
    assert events("marketplace.delivery.auto_released", bid) == []
    assert (
        db.query(MarketplaceDelivery).filter_by(booking_id=uuid.UUID(bid)).one().decision
        == "accepted"
    )


def test_a_requested_change_stops_the_auto_release_clock(client, db, w, ps):
    bid = delivered_package(w, ps)
    client.post(
        f"{M}/bookings/{bid}/request-changes",
        json={"reason": "Please change the logo colours."},
        headers=w.h(),
    )
    res = timers.tick(db, datetime.now(UTC) + timedelta(days=30))
    assert res["auto_released"] == 0
    assert booking(db, bid).status == BookingStatus.in_progress


def test_an_overdue_package_is_flagged_once_and_nothing_else_changes(client, db, w, ps, mail):
    bid = accepted(w, ps, listing=w.package)
    b = booking(db, bid)
    mail.sent.clear()
    assert timers.tick(db, b.due_at - timedelta(minutes=1))["overdue_flagged"] == 0
    late = b.due_at + timedelta(hours=1)
    assert timers.tick(db, late)["overdue_flagged"] == 1
    db.refresh(b)
    assert b.status == BookingStatus.in_progress and b.overdue_notified_at is not None
    assert [m.subject for m in sent_to(mail, w.expert.email)] == ["Overdue: Logo sprint"]
    assert len(notes(db, w.founder, "marketplace.booking.overdue")) == 1
    mail.sent.clear()
    assert timers.tick(db, late + timedelta(days=1))["overdue_flagged"] == 0
    assert mail.sent == [] and len(notes(db, w.founder, "marketplace.booking.overdue")) == 1


def test_one_failing_booking_does_not_stop_the_fulfilment_timers(client, db, w, ps, monkeypatch):
    first = delivered_package(w, ps)
    second = delivered_package_other(w, ps)
    real = deliveries.auto_release

    def flaky(db_, b, t):
        if str(b.id) == first:
            raise RuntimeError("boom")
        return real(db_, b, t)

    monkeypatch.setattr(deliveries, "auto_release", flaky)
    res = timers.tick(db, datetime.now(UTC) + timedelta(days=8))
    assert res["auto_released"] == 1
    assert booking(db, second).status == BookingStatus.completed
    assert booking(db, first).status == BookingStatus.delivered


def delivered_package_other(w, ps):
    bid = accepted(w, ps, listing=w.package, who=w.other, ws=w.other_ws)
    deliver(w, bid)
    return bid


# ================================================================ uploads and who can read them


def test_uploads_are_limited_by_role_and_purpose(client, db, w, ps):
    mentor = mk_user(db)
    member(db, w.ws, mentor, MembershipRole.mentor)
    assert upload(client, w.h(), M, "brief").status_code == 201
    assert upload(client, w.h(), M, "message").status_code == 201
    assert upload(client, w.h(), M, "delivery").status_code == 422  # a founder doesn't deliver
    assert upload(client, w.h(), M, "credential").status_code == 422
    assert upload(client, w.h(w.mate), M, "brief").status_code == 403  # team members read only
    assert upload(client, w.h(mentor), M, "brief").status_code == 403
    assert (
        client.post(
            f"{M}/files", data={"purpose": "brief"}, files={"file": ("a.pdf", PDF)}
        ).status_code
        == 401
    )
    assert upload(client, hdr(w.expert), P, "delivery").status_code == 201
    assert upload(client, hdr(w.expert), P, "message").status_code == 201
    assert upload(client, hdr(w.expert), P, "brief").status_code == 422  # only a client briefs
    assert upload(client, hdr(w.founder), P, "delivery").status_code == 403


def test_upload_validation_uses_the_bytes_not_the_claim(client, db, w):
    h = w.h()
    docx = upload(
        client,
        h,
        M,
        "brief",
        content=docx_bytes(),
        name="n.docx",
        ctype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert docx.status_code == 201 and docx.json()["data"]["mime"].endswith(
        "wordprocessingml.document"
    )
    png = upload(client, h, M, "message", content=PNG, name="s.png", ctype="image/png")
    assert png.status_code == 201
    text = upload(client, h, M, "brief", content=b"plain text", name="a.txt", ctype="text/plain")
    assert text.status_code == 422 and text.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"
    liar = upload(client, h, M, "brief", content=b"MZ not a pdf", name="a.pdf")
    assert liar.status_code == 422 and liar.json()["error"]["code"] == "FILE_CONTENT_MISMATCH"
    big = upload(client, h, M, "brief", content=PDF + b"0" * (10 * 1024 * 1024))
    assert big.status_code == 422 and big.json()["error"]["code"] == "FILE_TOO_LARGE"
    # a delivery may be a CSV, which a brief may not
    csv = upload(
        client, hdr(w.expert), P, "delivery", content=b"a,b\n1,2\n", name="d.csv", ctype="text/csv"
    )
    assert csv.status_code == 201


def test_too_many_unsent_uploads_are_refused(client, db, w):
    for _ in range(20):
        assert upload(client, w.h(), M, "message").status_code == 201
    r = upload(client, w.h(), M, "message")
    assert r.status_code == 409 and r.json()["error"]["code"] == "FILE_LIMIT_REACHED"
    assert upload(client, w.h(), M, "brief").status_code == 201  # per purpose


def download(client, base, fid, headers, suffix="/content"):
    return client.get(f"{base}/files/{fid}{suffix}", headers=headers)


def test_an_unsent_upload_is_readable_only_by_its_owner(client, db, w, ps):
    fid = founder_file(w, "brief")
    assert download(client, M, fid, w.h()).content == PDF
    assert download(client, M, fid, w.h(), suffix="").status_code == 200  # the bare path too
    assert download(client, M, fid, w.h(w.mate)).status_code == 404
    assert download(client, M, fid, w.h(w.other, w.other_ws)).status_code == 404
    assert download(client, P, fid, hdr(w.expert)).status_code == 404
    assert download(client, M, fid, {}).status_code == 401
    theirs = provider_file(w, "delivery")
    assert download(client, P, theirs, hdr(w.expert)).status_code == 200
    assert download(client, M, theirs, w.h()).status_code == 404  # not delivered yet


def test_brief_files_travel_with_the_booking_to_that_expert_only(client, db, w, ps):
    from tests.api.test_marketplace_listings import mk_provider

    mentor = mk_user(db)
    member(db, w.ws, mentor, MembershipRole.mentor)
    fid = founder_file(w, "brief")
    r = w.book(file_ids=[fid])
    assert r.status_code == 201, r.text
    bid = r.json()["data"]["id"]
    assert r.json()["data"]["brief_files"][0]["id"] == fid
    # still waiting for payment: the expert cannot see the booking, so nor its brief
    assert download(client, P, fid, hdr(w.expert)).status_code == 404
    ps.pay(r.json()["data"]["order"]["reference"])
    post_webhook(client, charge_success(r.json()["data"]["order"]["reference"]))
    other_expert, _ = mk_provider(db, name="Other Expert")
    assert download(client, P, fid, hdr(w.expert)).content == PDF
    assert download(client, P, fid, hdr(other_expert)).status_code == 404
    assert download(client, M, fid, w.h(w.mate)).status_code == 200  # team member reads
    assert download(client, M, fid, w.h(mentor)).status_code == 403
    assert download(client, M, fid, w.h(w.other, w.other_ws)).status_code == 404
    seen = client.get(f"{P}/bookings/{bid}", headers=hdr(w.expert)).json()["data"]
    assert seen["brief_files"][0]["url"] == f"/api/v1/provider/files/{fid}/content"
    assert str(w.ws.id) not in str(seen)


def test_brief_files_must_be_yours_unsent_and_at_most_five(client, db, w, ps):
    mine = [founder_file(w, "brief") for _ in range(6)]
    assert w.book(file_ids=mine).status_code == 422  # six
    theirs = file_id(upload(client, w.h(w.other, w.other_ws), M, "brief"))
    for bad in (
        theirs,
        provider_file(w, "delivery"),
        founder_file(w, "message"),
        str(uuid.uuid4()),
    ):
        r = w.book(file_ids=[bad])
        assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_FILE", bad
    assert db.query(MarketplaceBooking).count() == 0  # nothing was created, nothing is held
    ok = w.book(file_ids=mine[:5])
    assert ok.status_code == 201 and len(ok.json()["data"]["brief_files"]) == 5
    reuse = w.book(file_ids=[mine[0]], who=w.founder, slot=w.slots()[1])  # already sent
    assert reuse.status_code == 422 and reuse.json()["error"]["code"] == "INVALID_FILE"
    gone = db.get(MarketplaceFile, uuid.UUID(mine[5]))
    assert gone.booking_id is None


def test_a_replayed_booking_does_not_attach_files_again(client, db, w, ps):
    first, second = founder_file(w, "brief"), founder_file(w, "brief")
    slot = w.slots()[0]
    a = w.book(slot=slot, key="replay-key-0001", file_ids=[first])
    assert a.status_code == 201
    b = w.book(slot=slot, key="replay-key-0001", file_ids=[second])
    assert b.status_code == 200 and b.json()["data"]["replayed"] is True
    assert b.json()["data"]["id"] == a.json()["data"]["id"]
    assert [f["id"] for f in b.json()["data"]["brief_files"]] == [first]
    assert db.get(MarketplaceFile, uuid.UUID(second)).booking_id is None


def test_delivery_files_reach_the_founder_side_and_nobody_else(client, db, w, ps):
    fid = provider_file(w, "delivery")
    bid = accepted(w, ps, listing=w.package)
    deliver(w, bid, files=[fid])
    assert download(client, M, fid, w.h()).status_code == 200
    assert download(client, M, fid, w.h(w.mate)).status_code == 200
    assert download(client, M, fid, w.h(w.other, w.other_ws)).status_code == 404
    from tests.api.test_marketplace_listings import mk_provider

    other_expert, _ = mk_provider(db, name="Other Expert")
    assert download(client, P, fid, hdr(other_expert)).status_code == 404
    assert download(client, P, fid, hdr(w.expert)).status_code == 200
    # the expert cannot delete a file they already sent
    gone = client.delete(f"{P}/files/{fid}", headers=hdr(w.expert))
    assert gone.status_code == 409 and gone.json()["error"]["code"] == "FILE_IN_USE"


def test_a_founder_can_remove_an_unsent_upload_but_not_a_sent_one(client, db, w, ps):
    spare, sent = founder_file(w, "brief"), founder_file(w, "brief")
    removed = client.delete(f"{M}/files/{spare}", headers=w.h())
    assert removed.status_code == 200
    assert download(client, M, spare, w.h()).status_code == 404
    by_mate = client.delete(f"{M}/files/{spare}", headers=w.h(w.mate))
    assert by_mate.status_code == 403
    w.book(file_ids=[sent])
    r = client.delete(f"{M}/files/{sent}", headers=w.h())
    assert r.status_code == 409 and r.json()["error"]["code"] == "FILE_IN_USE"


# ================================================================ attach from Documents


def attach(w, bid, doc, headers=None):
    return w.client.post(
        f"{M}/bookings/{bid}/documents",
        json={"document_id": str(doc.id)},
        headers=w.h() if headers is None else headers,
    )


def test_a_shared_document_is_readable_by_the_expert_for_this_booking_only(client, db, w, ps):
    from tests.api.test_marketplace_listings import mk_provider

    doc = mk_doc(db, w.ws, w.founder)
    bid = accepted(w, ps)
    r = attach(w, bid, doc)
    assert r.status_code == 201, r.text
    expires = datetime.fromisoformat(r.json()["data"]["expires_at"])
    assert (
        timedelta(days=30) - timedelta(minutes=1)
        < expires - datetime.now(UTC)
        <= timedelta(days=30)
    )
    seen = client.get(f"{P}/bookings/{bid}/documents/{doc.id}", headers=hdr(w.expert))
    assert seen.status_code == 200
    assert seen.json()["data"]["title"] == "Cap table notes"
    assert seen.json()["data"]["sections"][0]["body"].startswith("Seed round")
    assert str(w.ws.id) not in seen.text and "Acme" not in seen.text
    listed = client.get(f"{P}/bookings/{bid}", headers=hdr(w.expert)).json()["data"]["documents"]
    assert [x["document_id"] for x in listed] == [str(doc.id)]
    assert listed[0]["url"] == f"/api/v1/provider/bookings/{bid}/documents/{doc.id}"
    other_expert, _ = mk_provider(db, name="Other Expert")
    assert (
        client.get(f"{P}/bookings/{bid}/documents/{doc.id}", headers=hdr(other_expert)).status_code
        == 404
    )
    # a second booking with the same expert does not inherit the grant
    bid2 = accepted(w, ps, slot=w.slots()[1])
    assert (
        client.get(f"{P}/bookings/{bid2}/documents/{doc.id}", headers=hdr(w.expert)).status_code
        == 404
    )
    # this is not a Documents grant: nothing appears in the expert's Shared-with-me
    assert db.query(DocumentGrant).count() == 0


def test_only_a_founders_own_live_documents_can_be_attached(client, db, w, ps):
    mentor = mk_user(db)
    member(db, w.ws, mentor, MembershipRole.mentor)
    bid = accepted(w, ps)
    doc = mk_doc(db, w.ws, w.founder)
    foreign = mk_doc(db, w.other_ws, w.other)
    assert attach(w, bid, foreign).status_code == 404  # another workspace's document
    assert attach(w, bid, doc, w.h(w.mate)).status_code == 403
    assert attach(w, bid, doc, w.h(mentor)).status_code == 403
    assert attach(w, bid, doc, w.h(w.other, w.other_ws)).status_code == 404  # not their booking
    assert attach(w, bid, doc, {}).status_code == 401
    doc.trashed_at = datetime.now(UTC)
    db.flush()
    assert attach(w, bid, doc).status_code == 404
    assert db.query(MarketplaceBookingDocument).count() == 0


def test_a_shared_document_stops_working_when_it_expires_is_removed_or_trashed(client, db, w, ps):
    doc = mk_doc(db, w.ws, w.founder)
    bid = accepted(w, ps)
    attach(w, bid, doc)
    url = f"{P}/bookings/{bid}/documents/{doc.id}"
    assert client.get(url, headers=hdr(w.expert)).status_code == 200
    link = db.query(MarketplaceBookingDocument).one()
    link.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.flush()
    assert client.get(url, headers=hdr(w.expert)).status_code == 404
    assert (
        client.get(f"{P}/bookings/{bid}", headers=hdr(w.expert)).json()["data"]["documents"] == []
    )
    again = attach(w, bid, doc)  # attaching again renews it
    assert again.status_code == 201 and client.get(url, headers=hdr(w.expert)).status_code == 200
    revoked_by_mate = client.delete(f"{M}/bookings/{bid}/documents/{doc.id}", headers=w.h(w.mate))
    assert revoked_by_mate.status_code == 403
    revoked = client.delete(f"{M}/bookings/{bid}/documents/{doc.id}", headers=w.h())
    assert revoked.status_code == 200
    assert client.get(url, headers=hdr(w.expert)).status_code == 404
    attach(w, bid, doc)
    doc.trashed_at = datetime.now(UTC)
    db.flush()
    assert client.get(url, headers=hdr(w.expert)).status_code == 404


def test_attaching_the_same_document_twice_changes_nothing(client, db, w, ps):
    doc = mk_doc(db, w.ws, w.founder)
    bid = accepted(w, ps)
    a, b = attach(w, bid, doc), attach(w, bid, doc)
    assert a.status_code == b.status_code == 201
    assert a.json()["data"]["expires_at"] == b.json()["data"]["expires_at"]
    assert db.query(MarketplaceBookingDocument).count() == 1
    listed = client.get(f"{M}/bookings/{bid}/documents", headers=w.h(w.mate)).json()["data"]
    assert [d["status"] for d in listed["documents"]] == ["active"]


def test_at_most_ten_documents_per_booking(client, db, w, ps):
    bid = accepted(w, ps)
    for i in range(10):
        assert attach(w, bid, mk_doc(db, w.ws, w.founder, f"Doc {i}")).status_code == 201
    r = attach(w, bid, mk_doc(db, w.ws, w.founder, "One too many"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "DOCUMENT_LIMIT_REACHED"


@pytest.mark.parametrize("how", ["completed", "cancelled", "declined", "expired"])
def test_every_ending_revokes_the_shared_documents(client, db, w, ps, how):
    doc = mk_doc(db, w.ws, w.founder)
    if how == "completed":
        bid = delivered_package(w, ps)
        attach(w, bid, doc)
        client.post(f"{M}/bookings/{bid}/accept-delivery", headers=w.h())
    else:
        bid = paid(w, ps, listing=w.package)
        attach(w, bid, doc)
        if how == "cancelled":
            client.post(f"{M}/bookings/{bid}/cancel", headers=w.h())
        elif how == "declined":
            client.post(f"{P}/bookings/{bid}/decline", headers=hdr(w.expert))
        else:
            timers.tick(db, datetime.now(UTC) + timedelta(hours=49))
    assert booking(db, bid).status.value == how
    link = db.query(MarketplaceBookingDocument).one()
    assert link.revoked_at is not None
    assert (
        client.get(f"{P}/bookings/{bid}/documents/{doc.id}", headers=hdr(w.expert)).status_code
        == 404
    )
    late = attach(w, bid, doc)
    assert late.status_code == 409 and late.json()["error"]["code"] == "INVALID_STATE"


# ================================================================ the Calendar meeting


MEET = "https://meet.example.com/ngozi"


def invite_jobs(db, event_id):
    return [
        j
        for j in db.query(Job).filter_by(type="calendar.invites").all()
        if str(
            j.payload.get("event_id") or (j.payload.get("cancel_snapshot") or {}).get("event_id")
        )
        == str(event_id)
    ]


def run_jobs(db, jobs, monkeypatch, mail):
    from app.worker.handlers.calendar import handle_invites

    monkeypatch.setattr("app.services.calendar.invites.get_email_sender", lambda: mail)
    for j in jobs:
        handle_invites(db, j)


def test_accepting_a_consultation_puts_a_meeting_on_the_founders_calendar(
    client, db, w, ps, mail, monkeypatch
):
    from app.db.models.calendar import CalendarAttendee, CalendarEvent

    bid = paid(w, ps)
    assert db.query(CalendarEvent).filter_by(startup_id=w.ws.id).count() == 0
    # nothing the expert has been sent so far carries the meeting link
    assert all("meet.example.com" not in m.html for m in sent_to(mail, w.expert.email))
    assert client.get(f"{M}/bookings/{bid}/calendar.ics", headers=w.h()).status_code == 404

    accepted_resp = client.post(f"{P}/bookings/{bid}/accept", headers=hdr(w.expert))
    assert accepted_resp.status_code == 200
    b = booking(db, bid)
    ev = db.get(CalendarEvent, b.calendar_event_id)
    assert ev is not None and ev.startup_id == w.ws.id and ev.source_ref == f"marketplace:{bid}"
    assert ev.title == "Design critique with Ngozi Eze" and ev.video_url == MEET
    assert ev.visibility == "team" and ev.created_by == w.founder.id
    att = db.query(CalendarAttendee).filter_by(event_id=ev.id).all()
    assert [(a.email, a.invite_state) for a in att] == [(w.expert.email.lower(), "pending")]
    # the booking view carries the event and the add-to-calendar link
    mine = client.get(f"{M}/bookings/{bid}", headers=w.h(w.mate)).json()["data"]
    assert mine["calendar_event_id"] == str(ev.id) and mine["meeting_url"] == MEET
    assert mine["calendar_ics_url"] == f"/api/v1/marketplace/bookings/{bid}/calendar.ics"
    # the invite job sends the expert an ICS with the link
    jobs = invite_jobs(db, ev.id)
    assert len(jobs) == 1
    mail.sent.clear()
    run_jobs(db, jobs, monkeypatch, mail)
    invite = sent_to(mail, w.expert.email)
    assert len(invite) == 1 and MEET in invite[0].html
    ics = invite[0].attachments[0].content.decode()
    assert "METHOD:REQUEST" in ics and f"UID:{ev.id}@cofoundaz.com" in ics and MEET in ics
    assert "Brief" not in ics and "seed investment" not in ics  # the brief never leaves the app


def test_add_to_calendar_downloads_an_ics_for_founder_side_readers_only(client, db, w, ps):
    mentor = mk_user(db)
    member(db, w.ws, mentor, MembershipRole.mentor)
    bid = accepted(w, ps)
    url = f"{M}/bookings/{bid}/calendar.ics"
    r = client.get(url, headers=w.h())
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
    assert (
        "attachment" in r.headers["content-disposition"]
        and r.headers["cache-control"] == "private, no-store"
    )
    body = r.text.replace("\r\n ", "")  # unfold long lines
    assert "BEGIN:VCALENDAR" in body and f"mailto:{w.founder.email}" in body and MEET in body
    theirs = client.get(url, headers=w.h(w.mate)).text.replace("\r\n ", "")
    assert f"mailto:{w.mate.email}" in theirs  # addressed to whoever downloads it
    assert client.get(url, headers=w.h(mentor)).status_code == 403
    assert client.get(url, headers=w.h(w.other, w.other_ws)).status_code == 404
    assert client.get(url).status_code == 401
    assert client.get(url, headers=hdr(w.expert)).status_code in (403, 404, 422)


def test_a_package_gets_no_calendar_event(client, db, w, ps):
    bid = accepted(w, ps, listing=w.package)
    assert booking(db, bid).calendar_event_id is None
    assert client.get(f"{M}/bookings/{bid}/calendar.ics", headers=w.h()).status_code == 404


def test_a_booking_that_never_happens_never_gets_a_meeting(client, db, w, ps):
    from app.db.models.calendar import CalendarEvent

    declined = paid(w, ps)
    client.post(f"{P}/bookings/{declined}/decline", headers=hdr(w.expert))
    cancelled = paid(w, ps, slot=w.slots()[1])
    client.post(f"{M}/bookings/{cancelled}/cancel", headers=w.h())
    assert db.query(CalendarEvent).filter_by(startup_id=w.ws.id).count() == 0


def test_the_meeting_and_the_invite_are_cancelled_when_the_booking_is_closed(
    client, db, w, ps, mail, monkeypatch
):
    from app.db.models.calendar import CalendarEvent

    bid = accepted(w, ps)
    b = booking(db, bid)
    event_id = b.calendar_event_id
    run_jobs(db, invite_jobs(db, event_id), monkeypatch, mail)  # the invite went out
    mail.sent.clear()
    b.status, b.closed_reason = (
        BookingStatus.cancelled,
        "cancelled_by_staff",
    )  # slice E's kind of ending
    closing.on_closed(db, b, drop_calendar=True)
    db.flush()
    assert db.get(CalendarEvent, event_id) is None and b.calendar_event_id is None
    cancel_jobs = [j for j in invite_jobs(db, event_id) if "cancel_snapshot" in j.payload]
    assert len(cancel_jobs) == 1
    run_jobs(db, cancel_jobs, monkeypatch, mail)
    gone = sent_to(mail, w.expert.email)
    assert len(gone) == 1 and gone[0].subject.startswith("Cancelled: Design critique")
    assert "METHOD:CANCEL" in gone[0].attachments[0].content.decode()
    closing.on_closed(db, b, drop_calendar=True)  # a repeat is harmless
    assert len([j for j in invite_jobs(db, event_id) if "cancel_snapshot" in j.payload]) == 1


def test_a_completed_booking_keeps_its_meeting_as_the_record(client, db, w, ps):
    from app.db.models.calendar import CalendarEvent

    bid = accepted(w, ps)
    end_session(db, bid)
    client.post(f"{P}/bookings/{bid}/complete", headers=hdr(w.expert))
    client.post(f"{M}/bookings/{bid}/accept-delivery", headers=w.h())
    assert booking(db, bid).status == BookingStatus.completed
    assert db.get(CalendarEvent, booking(db, bid).calendar_event_id) is not None


def test_a_calendar_failure_never_undoes_an_accept(client, db, w, ps, monkeypatch):
    from app.services.calendar import meetings

    def boom(*a, **k):
        raise RuntimeError("calendar is down")

    monkeypatch.setattr(meetings, "create_meeting", boom)
    bid = paid(w, ps)
    r = client.post(f"{P}/bookings/{bid}/accept", headers=hdr(w.expert))
    assert r.status_code == 200 and r.json()["data"]["status"] == "confirmed"
    assert booking(db, bid).calendar_event_id is None


# ================================================================ founder notifications


def test_booking_confirmed_tells_the_founder_with_the_date_and_nobody_else(client, db, w, ps):
    mentor = mk_user(db)
    member(db, w.ws, mentor, MembershipRole.mentor)
    bid = accepted(w, ps)
    n = notes(db, w.founder, "marketplace.booking.confirmed")
    assert len(n) == 1 and n[0].title.startswith(
        "Booking confirmed: Design critique with Ngozi Eze · "
    )
    assert n[0].data["booking_id"] == bid and "brief" not in str(n[0].data)
    for other in (w.mate, mentor, w.other, w.expert):
        assert notes(db, other, "marketplace.booking.confirmed") == []
    pkg = accepted(w, ps, listing=w.package, who=w.other, ws=w.other_ws)
    n2 = notes(db, w.other, "marketplace.booking.confirmed")
    assert (
        n2[0].title == "Booking confirmed: Logo sprint with Ngozi Eze"
        and n2[0].data["booking_id"] == pkg
    )


def test_declined_and_expired_bookings_tell_the_founder_they_were_refunded(client, db, w, ps):
    a = paid(w, ps)
    client.post(f"{P}/bookings/{a}/decline", headers=hdr(w.expert))
    n = notes(db, w.founder, "marketplace.booking.declined")
    assert [(x.title, x.body) for x in n] == [
        ("Ngozi Eze could not take this on.", "You were refunded in full.")
    ]
    paid(w, ps, slot=w.slots()[1], who=w.other, ws=w.other_ws)
    timers.tick(db, datetime.now(UTC) + timedelta(hours=49))
    assert len(notes(db, w.other, "marketplace.booking.expired")) == 1
    assert notes(db, w.founder, "marketplace.booking.expired") == []


def test_the_marketplace_row_in_notification_preferences_is_honoured(client, db, w, ps):
    prefs = client.put(
        "/api/v1/notifications/preferences", json={"in_app": {"marketplace": False}}, headers=w.h()
    )
    assert prefs.status_code == 200 and prefs.json()["data"]["categories"]["marketplace"] is False
    delivered = delivered_package(w, ps)
    assert notes(db, w.founder, "marketplace.delivery.submitted") == []  # switched off
    assert notes(db, w.other, "marketplace.delivery.submitted") == []
    assert delivered


# ================================================================ the three #234 lows


def closed_unverified(w, ps, db, *, slot):
    """A hold that Paystack never answered for, closed after the grace and flagged for staff."""
    d = w.book(slot=slot).json()["data"]
    ps.verify_rejects = True
    b = booking(db, d["id"])
    timers.tick(db, b.slot_hold_expires_at + timedelta(minutes=61))
    o = order_of(db, d["id"])
    assert o.attention_reason == "unverified_close" and o.attention_resolved_at is None
    ps.verify_rejects = False
    return d


def queue(client, moderator):
    r = client.get(f"{ADMIN}/orders/attention", headers=hdr(moderator))
    assert r.status_code == 200
    return {x["id"]: x for x in r.json()["data"]["orders"]}


def test_an_unverified_close_clears_itself_when_the_late_payment_revives_the_booking(
    client, db, w, ps, moderator
):
    d = closed_unverified(w, ps, db, slot=w.slots()[0])
    o = order_of(db, d["id"])
    assert str(o.id) in queue(client, moderator)
    ps.pay(d["order"]["reference"])
    assert (
        post_webhook(client, charge_success(d["order"]["reference"])).json()["data"]["outcome"]
        == "paid"
    )
    db.refresh(o)
    assert booking(db, d["id"]).status == BookingStatus.awaiting_provider
    assert o.attention_reason == "unverified_close"  # the record stays
    assert o.attention_resolved_at is not None and o.attention_resolved_by is None
    assert str(o.id) not in queue(client, moderator)
    assert (
        db.query(AuditLog)
        .filter_by(action="marketplace.order.attention_auto_resolved", entity_id=o.id)
        .count()
        == 1
    )
    # the revived booking has its conversation
    assert client.get(f"{M}/bookings/{d['id']}", headers=w.h()).json()["data"]["thread_id"]


def test_an_unverified_close_clears_itself_when_the_order_is_fully_refunded(
    client, db, w, ps, moderator
):
    slot = w.slots()[0]
    d = closed_unverified(w, ps, db, slot=slot)
    o = order_of(db, d["id"])
    assert w.book(slot=slot, who=w.other, ws=w.other_ws).status_code == 201  # the slot is taken
    ps.pay(d["order"]["reference"])
    out = post_webhook(client, charge_success(d["order"]["reference"])).json()["data"]["outcome"]
    assert out == "paid_then_refund_requested"
    db.refresh(o)
    assert o.attention_resolved_at is None and str(o.id) in queue(
        client, moderator
    )  # money still held
    assert orders.process_refund(db, o.id) == "refunded"
    db.refresh(o)
    assert o.status == OrderStatus.refunded and o.attention_resolved_at is not None
    assert str(o.id) not in queue(client, moderator)


def test_a_mismatch_flag_is_never_cleared_automatically(client, db, w, ps, moderator):
    d = w.book().json()["data"]
    ps.pay(d["order"]["reference"])
    ps.amount_override = 1_020_000
    post_webhook(client, charge_success(d["order"]["reference"]))
    o = order_of(db, d["id"])
    assert o.attention_reason == "payment_mismatch" and o.attention_resolved_at is None
    assert str(o.id) in queue(client, moderator)


def code(staff) -> str:
    import pyotp

    from app.core.redis import get_redis

    for k in get_redis().scan_iter("staff_stepup_used:*"):
        get_redis().delete(k)
    return pyotp.TOTP(staff.test_secret).now()


def test_staff_can_resolve_a_flagged_order_with_a_reason_and_it_is_audited(
    client, db, w, ps, moderator
):
    d = w.book().json()["data"]
    ps.pay(d["order"]["reference"])
    ps.amount_override = 1_020_000
    post_webhook(client, charge_success(d["order"]["reference"]))
    o = order_of(db, d["id"])
    url = f"{ADMIN}/orders/{o.id}/attention/resolve"
    support = mk_staff(db, StaffRole.admin, ("support",))
    assert client.post(url, json={"reason": "Refunded N10,200 by hand."}).status_code == 404
    assert (
        client.post(
            url, json={"reason": "Refunded N10,200 by hand."}, headers=hdr(w.founder)
        ).status_code
        == 404
    )
    assert (
        client.post(
            url, json={"reason": "Refunded N10,200 by hand."}, headers=hdr(support)
        ).status_code
        == 403
    )
    assert client.post(url, json={"reason": "too short"}, headers=hdr(moderator)).status_code == 422
    no_code = client.post(url, json={"reason": "Refunded N10,200 by hand."}, headers=hdr(moderator))
    assert no_code.status_code == 422  # a money flag needs the authenticator code
    assert no_code.json()["error"]["field_errors"][0]["field"] == "totp_code"
    bad = client.post(
        url,
        json={"reason": "Refunded N10,200 by hand.", "totp_code": "000000"},
        headers=hdr(moderator),
    )
    assert bad.status_code in (401, 403, 422) and o.attention_resolved_at is None
    assert client.post(url, json={}, headers=hdr(moderator)).status_code == 422
    assert (
        client.post(
            f"{ADMIN}/orders/{uuid.uuid4()}/attention/resolve",
            json={"reason": "Refunded N10,200 by hand."},
            headers=hdr(moderator),
        ).status_code
        == 404
    )
    ok = client.post(
        url,
        json={"reason": "Refunded N10,200 by hand.", "totp_code": code(moderator)},
        headers=hdr(moderator),
    )
    assert ok.status_code == 200, ok.text
    assert (
        ok.json()["data"]["resolved_at"]
        and ok.json()["data"]["attention_reason"] == "payment_mismatch"
    )
    db.refresh(o)
    assert o.attention_resolved_by == moderator.id and o.attention_resolved_at is not None
    audit = (
        db.query(AuditLog)
        .filter_by(action="marketplace.order.attention_resolved", entity_id=o.id)
        .one()
    )
    assert audit.actor_user_id == moderator.id and audit.reason == "Refunded N10,200 by hand."
    assert audit.ip is not None and audit.user_agent is not None
    done = client.get(f"{ADMIN}/orders/attention?resolved=true", headers=hdr(moderator)).json()[
        "data"
    ]
    assert [x["id"] for x in done["orders"]] == [str(o.id)] and done["orders"][0]["resolved_at"]
    assert str(o.id) not in queue(client, moderator)
    again = client.post(
        url,
        json={"reason": "Refunded N10,200 by hand.", "totp_code": code(moderator)},
        headers=hdr(moderator),
    )
    assert again.status_code == 409 and again.json()["error"]["code"] == "NOT_FLAGGED"


def test_resolving_an_order_that_was_never_flagged_is_refused(client, db, w, ps, moderator):
    d = w.book().json()["data"]
    o = order_of(db, d["id"])
    r = client.post(
        f"{ADMIN}/orders/{o.id}/attention/resolve",
        json={"reason": "Nothing to see here."},
        headers=hdr(moderator),
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_FLAGGED"


def test_a_new_problem_on_a_resolved_order_puts_it_back_in_the_queue(client, db, w, ps, moderator):
    d = w.book().json()["data"]
    o = order_of(db, d["id"])
    b = booking(db, d["id"])
    orders.flag_attention(db, o, b, "payment_mismatch", 1_020_000)
    orders.resolve_attention(db, o.id, staff_user_id=moderator.id, reason="Dealt with by hand.")
    assert str(o.id) not in queue(client, moderator)
    orders.flag_attention(db, o, b, "payment_mismatch", 1_030_000)  # same reason, new event
    assert o.attention_resolved_at is None and str(o.id) in queue(client, moderator)
    orders.resolve_attention(db, o.id, staff_user_id=moderator.id, reason="Dealt with by hand.")
    o.refund_attempts = 11
    o.refund_due_minor = 1000
    orders._refund_retry(db, o, datetime.now(UTC))  # the 12th failed refund
    assert o.refund_failed_at is not None and o.attention_resolved_at is None
    assert o.attention_reason == "refund_failed" and str(o.id) in queue(client, moderator)


def test_the_attention_log_line_does_not_say_paystack_reports_none(client, db, w, ps, monkeypatch):
    lines: list[str] = []

    class Spy:
        def error(self, msg):
            lines.append(msg)

        def warning(self, msg):
            lines.append(msg)

    monkeypatch.setattr(orders, "log", Spy())
    d = w.book().json()["data"]
    o, b = order_of(db, d["id"]), booking(db, d["id"])
    orders.flag_attention(db, o, b, "unverified_close", None)
    orders.flag_attention(db, o, b, "payment_mismatch", 1_020_000)
    assert "None" not in lines[0] and "Paystack reports" not in lines[0]
    assert "hold closed without a Paystack answer" in lines[0] and "no amount" in lines[0]
    assert "payment did not match" in lines[1] and "Paystack reports 1020000" in lines[1]


def test_a_payment_landing_on_a_closed_hold_emails_the_founder_once_even_when_replayed(
    client, db, w, ps, mail
):
    d = w.book().json()["data"]
    ref = d["order"]["reference"]
    b = booking(db, d["id"])
    timers.tick(db, b.slot_hold_expires_at + timedelta(minutes=1))  # Paystack says unpaid: closed
    assert booking(db, d["id"]).closed_reason == "payment_not_completed"
    mail.sent.clear()
    ps.pay(ref)
    ps.amount_override = 1_020_000  # the late payment doesn't match the price
    first = post_webhook(client, charge_success(ref))
    assert first.json()["data"]["outcome"] == "payment_mismatch"
    subjects = [m.subject for m in sent_to(mail, w.founder.email)]
    assert subjects == ["We're checking your payment"]
    # the same webhook again, a re-verify from the founder's screen, and a direct re-apply
    assert post_webhook(client, charge_success(ref)).json()["data"]["outcome"] == "duplicate"
    verify = client.post(f"{M}/bookings/{d['id']}/verify-payment", headers=w.h())
    assert verify.status_code == 200 and verify.json()["data"]["outcome"] == "payment_mismatch"
    tx = ps.verify(ref)
    assert orders.apply_payment(db, ref, tx) == "payment_mismatch"
    assert [m.subject for m in sent_to(mail, w.founder.email)] == subjects
    assert (
        db.query(AuditLog).filter_by(action="marketplace.booking.expired", entity_id=b.id).count()
        == 2
    )
    assert booking(db, d["id"]).closed_reason == "payment_review"


# ================================================================ review fixes


def lapse(w, ps, db, **kw):
    """A checkout whose hold ran out unpaid: expired / payment_not_completed, never paid."""
    d = w.book(**kw).json()["data"]
    timers.tick(db, booking(db, d["id"]).slot_hold_expires_at + timedelta(minutes=1))
    b = booking(db, d["id"])
    assert (b.status, b.closed_reason) == (BookingStatus.expired, "payment_not_completed")
    return d["id"]


def test_an_expert_sees_nothing_of_a_booking_that_was_never_paid(client, db, w, ps):
    fid = founder_file(w, "brief")
    doc = mk_doc(db, w.ws, w.founder)
    d = w.book(file_ids=[fid], listing=w.package).json()["data"]
    timers.tick(db, booking(db, d["id"]).slot_hold_expires_at + timedelta(minutes=1))
    bid, h = d["id"], hdr(w.expert)
    assert booking(db, bid).closed_reason == "payment_not_completed"
    assert client.get(f"{P}/bookings/{bid}", headers=h).status_code == 404
    assert client.get(f"{P}/bookings", headers=h).json()["data"]["bookings"] == []
    assert client.get(f"{P}/files/{fid}/content", headers=h).status_code == 404
    assert client.get(f"{P}/bookings/{bid}/messages", headers=h).status_code == 404
    assert client.get(f"{P}/bookings/{bid}/documents", headers=h).status_code == 404
    assert (
        client.post(f"{P}/bookings/{bid}/deliver", json={"message": "x"}, headers=h).status_code
        == 404
    )
    assert doc is not None
    # a booking that WAS paid and then cancelled stays visible to its expert
    gone = paid(w, ps, listing=w.package, who=w.other, ws=w.other_ws)
    client.post(f"{M}/bookings/{gone}/cancel", headers=w.h(w.other, w.other_ws))
    assert booking(db, gone).status == BookingStatus.cancelled
    assert client.get(f"{P}/bookings/{gone}", headers=h).status_code == 200
    # a late payment refunded straight back never went live either
    ids = [x["id"] for x in client.get(f"{P}/bookings", headers=h).json()["data"]["bookings"]]
    assert ids == [gone]


def test_a_suspended_expert_cannot_download_a_clients_file_but_keeps_their_own(client, db, w, ps):
    from app.db.models.enums import ProviderStatus

    fid = founder_file(w, "brief")
    w.book(file_ids=[fid])
    bid = paid(w, ps, file_ids=[founder_file(w, "brief")], slot=w.slots()[1])
    mine = provider_file(w, "delivery")
    assert client.get(f"{P}/bookings/{bid}", headers=hdr(w.expert)).status_code == 200
    shared = booking(db, bid)
    shared_file = db.query(MarketplaceFile).filter_by(booking_id=shared.id).one()
    url = f"{P}/files/{shared_file.id}/content"
    assert client.get(url, headers=hdr(w.expert)).status_code == 200
    w.provider.status = ProviderStatus.suspended
    db.flush()
    assert client.get(url, headers=hdr(w.expert)).status_code == 404
    assert client.get(f"{P}/files/{mine}/content", headers=hdr(w.expert)).status_code == 200


def test_a_late_consultation_hand_over_still_gives_the_client_two_days(client, db, w, ps):
    bid = accepted(w, ps)
    b = end_session(db, bid, ago=timedelta(days=10))  # the worker was down
    t = datetime.now(UTC)
    res = timers.tick(db, t)
    assert res["consultations_delivered"] == 1 and res["auto_released"] == 0  # not in the same pass
    db.refresh(b)
    assert b.status == BookingStatus.delivered
    assert b.auto_release_at == t + timedelta(hours=48)
    assert timers.tick(db, t + timedelta(hours=47))["auto_released"] == 0
    assert timers.tick(db, t + timedelta(hours=49))["auto_released"] == 1
    # the expert marking it complete late gets the same two days
    other = accepted(w, ps, slot=w.slots()[1], who=w.other, ws=w.other_ws)
    end_session(db, other, ago=timedelta(days=9))
    client.post(f"{P}/bookings/{other}/complete", headers=hdr(w.expert))
    got = booking(db, other)
    assert got.auto_release_at - datetime.now(UTC) > timedelta(hours=47)
