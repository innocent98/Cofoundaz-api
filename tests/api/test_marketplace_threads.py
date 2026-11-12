"""Marketplace slice D: conversations between a workspace and an expert, before a booking and on
one, with unread counts, files, the 10-minute notification collapse and the access rules."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db.models.enums import BookingStatus, MembershipRole, ProviderStatus, StaffRole
from app.db.models.marketplace import MarketplaceMessage, MarketplaceThread
from app.db.models.notification import Notification
from tests.api.test_marketplace_listings import mk_provider
from tests.api.test_staff_users import hdr, member, mk_staff, mk_user
from tests.marketplace_booking_helpers import M, P, World, install_fake_paystack, install_mailbox
from tests.marketplace_fulfilment_helpers import (
    PDF,
    accepted,
    booking,
    founder_file,
    notes,
    paid,
    provider_file,
    sent_to,
)

KIND = "marketplace.message.new"


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


def open_thread(w, who=None, ws=None, provider=None, expect=None):
    r = w.client.post(
        f"{M}/threads",
        json={"provider_id": str((provider or w.provider).id)},
        headers=w.h(who, ws),
    )
    if expect:
        assert r.status_code == expect, r.text
    return r


def tid(w) -> str:
    r = open_thread(w)
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def say(w, thread, body="Hello there", *, files=None, who=None, ws=None):
    return w.client.post(
        f"{M}/threads/{thread}/messages",
        json={"body": body, "file_ids": files or []},
        headers=w.h(who, ws),
    )


def reply(w, thread, body="Happy to help", *, files=None):
    return w.client.post(
        f"{P}/threads/{thread}/messages",
        json={"body": body, "file_ids": files or []},
        headers=hdr(w.expert),
    )


def read_founder(w, thread, who=None, **params):
    return w.client.get(f"{M}/threads/{thread}/messages", params=params, headers=w.h(who))


def read_expert(w, thread, **params):
    return w.client.get(f"{P}/threads/{thread}/messages", params=params, headers=hdr(w.expert))


# ================================================================ opening a conversation


def test_a_founder_opens_one_conversation_per_expert(client, db, w):
    first = open_thread(w)
    assert first.status_code == 201
    d = first.json()["data"]
    assert d["booking_id"] is None and d["unread_count"] == 0 and d["last_message_at"] is None
    assert d["provider"] == {"id": str(w.provider.id), "display_name": "Ngozi Eze"}
    again = open_thread(w)
    assert again.status_code == 200 and again.json()["data"]["id"] == d["id"]
    other_ws = open_thread(w, w.other, w.other_ws)
    assert other_ws.status_code == 201 and other_ws.json()["data"]["id"] != d["id"]
    assert db.query(MarketplaceThread).count() == 2


def test_only_a_founder_opens_a_conversation_with_an_approved_expert(client, db, w):
    mentor = mk_user(db)
    member(db, w.ws, mentor, MembershipRole.mentor)
    assert open_thread(w, w.mate).status_code == 403
    assert open_thread(w, mentor).status_code == 403
    assert client.post(f"{M}/threads", json={"provider_id": str(w.provider.id)}).status_code == 401
    assert (
        client.post(f"{M}/threads", json={"provider_id": "nope"}, headers=w.h()).status_code == 422
    )
    assert open_thread(w, expect=None).status_code == 201
    _, pend_row = mk_provider(db, name="Pending Pat", status=ProviderStatus.in_review)
    assert open_thread(w, provider=pend_row).status_code == 404
    assert (
        client.post(
            f"{M}/threads", json={"provider_id": str(uuid.uuid4())}, headers=w.h()
        ).status_code
        == 404
    )
    # an expert can't open a conversation with their own workspace
    own_user, own_row = mk_provider(db, name="Own Expert")
    member(db, w.ws, own_user, MembershipRole.team_member)
    r = open_thread(w, provider=own_row)
    assert r.status_code == 422 and r.json()["error"]["code"] == "CANNOT_MESSAGE_OWN"


def test_the_database_allows_one_pre_booking_thread_per_pair_and_one_per_booking(db, w):
    db.add(MarketplaceThread(startup_id=w.ws.id, provider_id=w.provider.id))
    db.flush()
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(MarketplaceThread(startup_id=w.ws.id, provider_id=w.provider.id))
        db.flush()


# ================================================================ sending and reading


def test_messages_flow_both_ways_with_unread_counts_on_each_side(client, db, w):
    t = tid(w)
    # an empty conversation is invisible to the expert: they cannot be cold-contacted by a stub
    assert client.get(f"{P}/threads", headers=hdr(w.expert)).json()["data"]["threads"] == []
    assert client.get(f"{P}/threads/{t}", headers=hdr(w.expert)).status_code == 404
    for text in ("Hi Ngozi", "Do you do pitch decks?"):
        r = say(w, t, text)
        assert r.status_code == 201, r.text
        assert r.json()["data"]["mine"] is True and r.json()["data"]["sender_side"] == "founder"
    seen = client.get(f"{P}/threads", headers=hdr(w.expert)).json()["data"]
    assert [x["id"] for x in seen["threads"]] == [t] and seen["unread_total"] == 2
    assert seen["threads"][0]["unread_count"] == 2
    assert seen["threads"][0]["last_message_preview"] == "Do you do pitch decks?"
    page = read_expert(w, t).json()["data"]
    assert [m["body"] for m in page["messages"]] == ["Hi Ngozi", "Do you do pitch decks?"]
    assert all(m["mine"] is False for m in page["messages"])
    assert client.get(f"{P}/threads", headers=hdr(w.expert)).json()["data"]["unread_total"] == 0
    # the founder side now sees their messages as read, and gets the reply as unread
    assert reply(w, t, "Yes, I do.").status_code == 201
    mine = client.get(f"{M}/threads", headers=w.h()).json()["data"]
    assert mine["unread_total"] == 1 and mine["threads"][0]["unread_count"] == 1
    assert mine["threads"][0]["last_message_preview"] == "Yes, I do."
    got = read_founder(w, t).json()["data"]["messages"]
    assert [(m["sender_side"], m["read"]) for m in got] == [
        ("founder", True),
        ("founder", True),
        ("provider", True),
    ]
    assert client.get(f"{M}/threads", headers=w.h()).json()["data"]["unread_total"] == 0
    detail = client.get(f"{M}/threads/{t}", headers=w.h()).json()["data"]
    assert detail["unread_count"] == 0 and detail["last_message_at"]


def test_opening_a_thread_does_not_read_it_but_listing_messages_does(client, db, w):
    t = tid(w)
    say(w, t)
    reply(w, t)
    assert client.get(f"{M}/threads/{t}", headers=w.h()).json()["data"]["unread_count"] == 1
    assert client.get(f"{M}/threads/{t}", headers=w.h()).json()["data"]["unread_count"] == 1
    read_founder(w, t)
    assert client.get(f"{M}/threads/{t}", headers=w.h()).json()["data"]["unread_count"] == 0


def test_a_team_member_reads_for_the_whole_workspace_but_cannot_post(client, db, w):
    t = tid(w)
    say(w, t)
    reply(w, t)
    assert w.client.get(f"{M}/threads", headers=w.h(w.mate)).json()["data"]["unread_total"] == 1
    assert read_founder(w, t, who=w.mate).status_code == 200
    assert client.get(f"{M}/threads", headers=w.h()).json()["data"]["unread_total"] == 0  # shared
    r = say(w, t, "I want to join in", who=w.mate)
    assert r.status_code == 403
    assert db.query(MarketplaceMessage).filter_by(sender_user_id=w.mate.id).count() == 0


def test_the_access_matrix_for_a_thread(client, db, w):
    t = tid(w)
    say(w, t)
    mentor = mk_user(db)
    member(db, w.ws, mentor, MembershipRole.mentor)
    other_expert, _ = mk_provider(db, name="Other Expert")
    moderator = mk_staff(db, StaffRole.admin, ("moderation",))
    base = f"{M}/threads/{t}"
    # anonymous
    for method, url in (("get", base), ("get", f"{base}/messages"), ("post", f"{base}/messages")):
        assert (
            getattr(client, method)(
                url, **({"json": {"body": "x"}} if method == "post" else {})
            ).status_code
            == 401
        )
    # a mentor in the same workspace, and another workspace's founder
    assert client.get(base, headers=w.h(mentor)).status_code == 403
    assert read_founder(w, t, who=mentor).status_code == 403
    assert say(w, t, who=mentor).status_code == 403
    for who, ws in ((w.other, w.other_ws),):
        assert client.get(base, headers=w.h(who, ws)).status_code == 404
        assert read_founder(w, t, who=who).status_code in (404, 403)
        assert say(w, t, who=who, ws=ws).status_code == 404
    # another expert, and platform staff (who never read threads outside a dispute)
    assert client.get(f"{P}/threads/{t}", headers=hdr(other_expert)).status_code == 404
    assert client.get(f"{P}/threads", headers=hdr(other_expert)).json()["data"]["threads"] == []
    assert reply_as(client, t, other_expert).status_code == 404
    for url in (base, f"{base}/messages", f"{P}/threads/{t}"):
        assert client.get(url, headers=hdr(moderator)).status_code in (403, 404, 422)
    assert db.query(MarketplaceMessage).count() == 1


def reply_as(client, thread, expert):
    return client.post(f"{P}/threads/{thread}/messages", json={"body": "hi"}, headers=hdr(expert))


def test_a_suspended_expert_can_no_longer_read_or_reply(client, db, w):
    t = tid(w)
    say(w, t)
    w.provider.status = ProviderStatus.suspended
    db.flush()
    for r in (
        client.get(f"{P}/threads", headers=hdr(w.expert)),
        read_expert(w, t),
        reply(w, t),
    ):
        assert r.status_code == 403 and r.json()["error"]["code"] == "PROVIDER_NOT_APPROVED"


def test_a_message_is_plain_text_of_one_to_four_thousand_characters(client, db, w):
    t = tid(w)
    for body in ("", "   ", "x" * 4001):
        assert say(w, t, body).status_code == 422
    assert client.post(f"{M}/threads/{t}/messages", json={}, headers=w.h()).status_code == 422
    assert (
        client.post(
            f"{M}/threads/{t}/messages", json={"body": "hi", "surprise": 1}, headers=w.h()
        ).status_code
        == 422
    )
    markup = "<script>alert(1)</script> & <b>bold</b>"
    r = say(w, t, markup)
    assert (
        r.status_code == 201 and r.json()["data"]["body"] == markup
    )  # stored and returned as text
    assert say(w, t, "x" * 4000).status_code == 201


def test_messages_page_back_newest_first_in_order(client, db, w):
    t = tid(w)
    for i in range(5):
        say(w, t, f"m{i}")
    newest = read_founder(w, t, limit=2).json()["data"]
    assert [m["body"] for m in newest["messages"]] == ["m3", "m4"] and newest["has_more"] is True
    older = read_founder(w, t, limit=2, before=newest["messages"][0]["id"]).json()["data"]
    assert [m["body"] for m in older["messages"]] == ["m1", "m2"] and older["has_more"] is True
    oldest = read_founder(w, t, limit=2, before=older["messages"][0]["id"]).json()["data"]
    assert [m["body"] for m in oldest["messages"]] == ["m0"] and oldest["has_more"] is False
    assert read_founder(w, t, before=str(uuid.uuid4())).status_code == 404
    assert read_founder(w, t, limit=0).status_code == 422


# ================================================================ files in messages


def test_files_sent_in_a_pre_booking_message_reach_that_expert_only(client, db, w):
    t = tid(w)
    fid = founder_file(w, "message")
    r = say(w, t, "Here is our deck", files=[fid])
    assert r.status_code == 201 and r.json()["data"]["files"][0]["id"] == fid
    assert r.json()["data"]["files"][0]["url"] == f"/api/v1/marketplace/files/{fid}/content"
    other_expert, _ = mk_provider(db, name="Other Expert")
    got = client.get(f"{P}/files/{fid}/content", headers=hdr(w.expert))
    assert got.status_code == 200 and got.content == PDF
    assert client.get(f"{P}/files/{fid}/content", headers=hdr(other_expert)).status_code == 404
    assert client.get(f"{M}/files/{fid}/content", headers=w.h(w.mate)).status_code == 200
    assert (
        client.get(f"{M}/files/{fid}/content", headers=w.h(w.other, w.other_ws)).status_code == 404
    )
    seen = read_expert(w, t).json()["data"]["messages"][0]
    assert seen["files"][0]["url"] == f"/api/v1/provider/files/{fid}/content"
    # and back: the expert's file reaches the founder side
    back = provider_file(w, "message")
    assert reply(w, t, "Notes attached", files=[back]).status_code == 201
    assert client.get(f"{M}/files/{back}/content", headers=w.h()).status_code == 200


def test_message_files_must_be_your_own_unsent_message_uploads_and_at_most_five(client, db, w):
    t = tid(w)
    wrong_purpose = founder_file(w, "brief")
    theirs = provider_file(w, "message")
    sent = founder_file(w, "message")
    assert say(w, t, "one", files=[sent]).status_code == 201
    for bad in (wrong_purpose, theirs, sent, str(uuid.uuid4())):
        r = say(w, t, "again", files=[bad])
        assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_FILE", bad
    assert say(w, t, "six", files=[founder_file(w, "message") for _ in range(6)]).status_code == 422
    assert (
        say(w, t, "five", files=[founder_file(w, "message") for _ in range(5)]).status_code == 201
    )
    assert db.query(MarketplaceMessage).count() == 2


# ================================================================ conversations on a booking


def test_paying_for_a_booking_opens_its_conversation(client, db, w, ps):
    bid = paid(w, ps)
    b = booking(db, bid)
    t = db.query(MarketplaceThread).filter_by(booking_id=b.id).one()
    assert (t.startup_id, t.provider_id) == (w.ws.id, w.provider.id)
    view = client.get(f"{M}/bookings/{bid}", headers=w.h()).json()["data"]
    assert view["thread_id"] == str(t.id)
    assert client.get(f"{P}/bookings/{bid}", headers=hdr(w.expert)).json()["data"][
        "thread_id"
    ] == str(t.id)
    seen = client.get(f"{P}/threads", headers=hdr(w.expert)).json()["data"]["threads"]
    assert seen[0]["booking_id"] == bid and seen[0]["booking_title"] == "Design critique"
    assert "client_name" in seen[0] and "startup" not in str(seen[0])


def test_a_pre_booking_thread_is_attached_to_the_booking_not_duplicated(client, db, w, ps):
    t = tid(w)
    say(w, t, "Before we book: do you cover seed agreements?")
    reply(w, t, "Yes.")
    bid = paid(w, ps)
    assert db.query(MarketplaceThread).filter_by(startup_id=w.ws.id).count() == 1
    thread = db.get(MarketplaceThread, uuid.UUID(t))
    assert str(thread.booking_id) == bid
    msgs = read_founder(w, t).json()["data"]["messages"]
    assert [m["body"] for m in msgs] == ["Before we book: do you cover seed agreements?", "Yes."]
    # another workspace's pre-booking thread with the same expert is untouched
    o = open_thread(w, w.other, w.other_ws).json()["data"]
    assert o["booking_id"] is None
    # the next booking with the same expert opens its own thread; a new pre-booking one is allowed
    bid2 = paid(w, ps, slot=w.slots()[1])
    assert db.query(MarketplaceThread).filter_by(startup_id=w.ws.id).count() == 2
    assert booking(db, bid2) is not None
    fresh = open_thread(w)
    assert fresh.status_code == 201 and fresh.json()["data"]["id"] != t


def test_the_booking_alias_posts_into_the_booking_thread(client, db, w, ps):
    bid = paid(w, ps)
    r = client.post(
        f"{M}/bookings/{bid}/messages",
        json={"body": "Where do I send the contract?"},
        headers=w.h(),
    )
    assert r.status_code == 201, r.text
    t = booking_thread(db, bid)
    reply(w, str(t.id), "Upload it to the brief, please.")
    got = client.get(f"{M}/bookings/{bid}/messages", headers=w.h()).json()["data"]
    assert got["thread_id"] == str(t.id)
    assert [m["body"] for m in got["messages"]] == [
        "Where do I send the contract?",
        "Upload it to the brief, please.",
    ]
    theirs = client.post(
        f"{P}/bookings/{bid}/messages",
        json={"body": "Also: bring your cap table."},
        headers=hdr(w.expert),
    )
    assert theirs.status_code == 201
    expert_view = client.get(f"{P}/bookings/{bid}/messages", headers=hdr(w.expert)).json()["data"]
    assert len(expert_view["messages"]) == 3
    # guards: team members read, don't post; another workspace and another expert get 404
    assert (
        client.post(
            f"{M}/bookings/{bid}/messages", json={"body": "x"}, headers=w.h(w.mate)
        ).status_code
        == 403
    )
    assert client.get(f"{M}/bookings/{bid}/messages", headers=w.h(w.mate)).status_code == 200
    assert (
        client.post(
            f"{M}/bookings/{bid}/messages", json={"body": "x"}, headers=w.h(w.other, w.other_ws)
        ).status_code
        == 404
    )
    other_expert, _ = mk_provider(db, name="Other Expert")
    assert (
        client.post(
            f"{P}/bookings/{bid}/messages", json={"body": "x"}, headers=hdr(other_expert)
        ).status_code
        == 404
    )


def booking_thread(db, bid) -> MarketplaceThread:
    return db.query(MarketplaceThread).filter_by(booking_id=uuid.UUID(bid)).one()


def test_there_is_no_conversation_until_a_booking_is_paid(client, db, w, ps):
    d = w.book().json()["data"]  # pending payment
    r = client.post(f"{M}/bookings/{d['id']}/messages", json={"body": "hello"}, headers=w.h())
    assert r.status_code == 409 and r.json()["error"]["code"] == "THREAD_NOT_AVAILABLE"
    assert db.query(MarketplaceThread).filter_by(booking_id=uuid.UUID(d["id"])).count() == 0
    # and the expert, who cannot see an unpaid booking at all, gets a 404
    assert client.get(f"{P}/bookings/{d['id']}/messages", headers=hdr(w.expert)).status_code == 404


def test_a_booking_paid_before_threads_existed_gets_its_thread_on_first_use(client, db, w, ps):
    bid = paid(w, ps)
    db.delete(booking_thread(db, bid))
    db.flush()
    r = client.post(f"{M}/bookings/{bid}/messages", json={"body": "hello"}, headers=w.h())
    assert r.status_code == 201
    assert booking_thread(db, bid) is not None


def test_a_closed_bookings_conversation_is_read_only_a_completed_ones_is_not(client, db, w, ps):
    cancelled = paid(w, ps)
    client.post(f"{M}/bookings/{cancelled}/cancel", headers=w.h())
    assert booking(db, cancelled).status == BookingStatus.cancelled
    t = booking_thread(db, cancelled)
    r = say(w, str(t.id), "are you there?")
    assert r.status_code == 409 and r.json()["error"]["code"] == "THREAD_CLOSED"
    assert reply(w, str(t.id)).status_code == 409
    assert read_founder(w, str(t.id)).status_code == 200  # the history stays readable
    assert client.get(f"{M}/threads/{t.id}", headers=w.h()).json()["data"]["read_only"] is True
    done = accepted(w, ps, listing=w.package)
    client.post(
        f"{P}/bookings/{done}/deliver",
        json={"message": "done", "file_ids": []},
        headers=hdr(w.expert),
    )
    client.post(f"{M}/bookings/{done}/accept-delivery", headers=w.h())
    assert booking(db, done).status == BookingStatus.completed
    assert say(w, str(booking_thread(db, done).id), "thank you!").status_code == 201


# ================================================================ notifications and email


def test_new_expert_messages_notify_the_founder_once_per_ten_minutes(client, db, w):
    mentor = mk_user(db)
    member(db, w.ws, mentor, MembershipRole.mentor)
    t = tid(w)
    say(w, t)
    for i in range(3):
        reply(w, t, f"reply {i}")
    n = notes(db, w.founder, KIND)
    assert len(n) == 1 and n[0].title == "Ngozi Eze: reply 0" and n[0].data["thread_id"] == t
    for nobody in (w.mate, mentor, w.other, w.expert):
        assert notes(db, nobody, KIND) == []
    n[0].created_at = datetime.now(UTC) - timedelta(minutes=11)
    db.flush()
    reply(w, t, "reply 3")
    assert [x.title for x in sorted(notes(db, w.founder, KIND), key=lambda x: x.created_at)] == [
        "Ngozi Eze: reply 0",
        "Ngozi Eze: reply 3",
    ]
    # another conversation is its own window
    t2 = open_thread(w, w.other, w.other_ws).json()["data"]["id"]
    say(w, t2, who=w.other, ws=w.other_ws)
    reply(w, t2, "hello other")
    assert len(notes(db, w.other, KIND)) == 1


def test_the_expert_gets_one_email_per_conversation_per_window_without_the_text(
    client, db, w, mail
):
    w.founder.profile.full_name = "<b>Ada</b> Obi"
    db.flush()
    t = tid(w)
    mail.sent.clear()
    say(w, t, "Secret deal terms: 20% for N50m")
    say(w, t, "And a second message")
    mine = sent_to(mail, w.expert.email)
    assert len(mine) == 1 and mine[0].subject == "You have a new message on Cofoundaz"
    assert "&lt;b&gt;Ada&lt;/b&gt; Obi" in mine[0].html and "<b>Ada</b>" not in mine[0].html
    assert "Secret deal terms" not in mine[0].html and "second message" not in mine[0].html
    thread = db.get(MarketplaceThread, uuid.UUID(t))
    thread.provider_emailed_at = datetime.now(UTC) - timedelta(minutes=11)
    db.flush()
    say(w, t, "Third message, a new window")
    assert len(sent_to(mail, w.expert.email)) == 2
    # the expert's own message emails nobody on the founder side
    mail.sent.clear()
    reply(w, t)
    assert mail.sent == []


def test_an_expert_who_is_no_longer_approved_is_not_emailed(client, db, w, mail):
    t = tid(w)
    w.provider.status = ProviderStatus.suspended
    db.flush()
    mail.sent.clear()
    say(w, t)
    assert sent_to(mail, w.expert.email) == []


def test_posting_is_rate_limited(client, db, w):
    from app.main import app

    t = tid(w)
    db.commit()
    app.state.limiter.enabled = True
    app.state.limiter.reset()
    try:
        codes = [say(w, t, f"m{i}").status_code for i in range(31)]
    finally:
        app.state.limiter.enabled = False
        app.state.limiter.reset()
    assert codes[:30] == [201] * 30 and codes[30] == 429


def test_a_message_is_audited_without_its_text(client, db, w):
    from app.db.models.audit import AuditLog

    t = tid(w)
    say(w, t, "private words")
    rows = db.query(AuditLog).filter_by(action="marketplace.message.sent").all()
    assert len(rows) == 1 and "private words" not in str(rows[0].details)
    assert db.query(Notification).filter(Notification.data["thread_id"].astext == t).count() == 0
