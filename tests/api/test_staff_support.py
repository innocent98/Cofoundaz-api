"""Tickets: the founder's Help intake and the staff Support console."""

import uuid
from datetime import timedelta

import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.redis import get_redis
from app.db.models.audit import AuditLog
from app.db.models.enums import MembershipRole, StaffRole, TicketStatus
from app.db.models.support import SupportTicket, SupportTicketNote
from app.services.support import core
from tests.api.test_staff_users import hdr, member, mk_staff, mk_user, mk_ws

S = "/api/v1/admin/support"
F = "/api/v1/support"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    r = get_redis()
    for k in r.scan_iter("support_rl:*"):
        r.delete(k)


class Outbox:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)


@pytest.fixture()
def outbox(monkeypatch):
    box = Outbox()
    monkeypatch.setattr(core, "get_email_sender", lambda: box)
    return box


def fh(user, ws):
    return {**hdr(user), "X-Workspace-Id": str(ws.id)}


@pytest.fixture()
def world(db):
    owner = mk_user(db, name="Ada Obi")
    ws = mk_ws(db, owner, name="Acme Co", plan="growth")
    return owner, ws


def open_ticket(client, user, ws, **over):
    body = {"category": "bug", "subject": "Export fails", "body": "It spins forever."}
    r = client.post(f"{F}/tickets", json={**body, **over}, headers=fh(user, ws))
    assert r.status_code == 201, r.text
    return r.json()["data"]


# ---------------------------------------------------------------- founder intake


def test_open_ticket_shape_and_defaults(client, db, world):
    owner, ws = world
    t = open_ticket(client, owner, ws)
    assert t["number"].startswith("T-") and t["status"] == "open" and t["category"] == "bug"
    assert [m["author_kind"] for m in t["messages"]] == ["founder"]
    assert t["messages"][0]["is_mine"] is True and t["messages"][0]["author_name"] == "Ada Obi"
    # Nothing internal reaches a founder.
    for key in ("priority", "assignee", "sla", "notes", "escalated", "escalation_reason"):
        assert key not in t
    row = db.query(SupportTicket).one()
    assert row.priority.value == "P3" and row.startup_id == ws.id
    assert open_ticket(client, owner, ws, category="billing") and (
        db.query(SupportTicket).filter(SupportTicket.category == "billing").one().priority.value
        == "P2"
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"category": "nope"},
        {"subject": "x"},
        {"body": "   "},
        {"body": "a" * 5001},
        {"priority": "P1"},
    ],
)
def test_open_ticket_validation(client, world, bad):
    owner, ws = world
    body = {"category": "bug", "subject": "Export fails", "body": "Help", **bad}
    assert client.post(f"{F}/tickets", json=body, headers=fh(owner, ws)).status_code == 422


def test_open_ticket_needs_auth_and_membership(client, db, world):
    owner, ws = world
    body = {"category": "bug", "subject": "Export fails", "body": "Help"}
    assert client.post(f"{F}/tickets", json=body).status_code in (401, 422)
    outsider = mk_user(db)
    r = client.post(
        f"{F}/tickets", json=body, headers={**hdr(outsider), "X-Workspace-Id": str(ws.id)}
    )
    assert r.status_code == 403


def test_ticket_creation_is_rate_limited(client, world):
    owner, ws = world
    for _ in range(5):
        open_ticket(client, owner, ws)
    r = client.post(
        f"{F}/tickets",
        json={"category": "bug", "subject": "Sixth one", "body": "x"},
        headers=fh(owner, ws),
    )
    assert r.status_code == 429 and r.json()["error"]["code"] == "RATE_LIMITED"


def test_founder_sees_own_workspace_only(client, db, world):
    owner, ws = world
    mine = open_ticket(client, owner, ws)
    other_owner = mk_user(db)
    other_ws = mk_ws(db, other_owner, name="Other Co")
    theirs = open_ticket(client, other_owner, other_ws)

    listed = client.get(f"{F}/tickets", headers=fh(owner, ws)).json()["data"]["tickets"]
    assert [t["id"] for t in listed] == [mine["id"]]
    # Someone else's id is a 404, with either workspace header.
    for h in (fh(owner, ws), {**hdr(owner), "X-Workspace-Id": str(other_ws.id)}):
        r = client.get(f"{F}/tickets/{theirs['id']}", headers=h)
        assert r.status_code in (403, 404)
    assert client.get(f"{F}/tickets/{theirs['id']}", headers=fh(owner, ws)).status_code == 404
    r = client.post(
        f"{F}/tickets/{theirs['id']}/messages", json={"body": "hi"}, headers=fh(owner, ws)
    )
    assert r.status_code == 404


def test_team_member_sees_only_own_but_workspace_founder_sees_all(client, db, world):
    owner, ws = world
    mate = mk_user(db)
    member(db, ws, mate, MembershipRole.team_member)
    other_mate = mk_user(db)
    member(db, ws, other_mate, MembershipRole.team_member)
    by_mate = open_ticket(client, mate, ws)
    by_other = open_ticket(client, other_mate, ws)

    ids = lambda u: {  # noqa: E731
        t["id"] for t in client.get(f"{F}/tickets", headers=fh(u, ws)).json()["data"]["tickets"]
    }
    assert ids(mate) == {by_mate["id"]}
    assert ids(owner) == {by_mate["id"], by_other["id"]}
    assert client.get(f"{F}/tickets/{by_other['id']}", headers=fh(mate, ws)).status_code == 404
    assert client.get(f"{F}/tickets/{by_other['id']}", headers=fh(owner, ws)).status_code == 200


def test_list_filters_by_status_and_pages(client, world):
    owner, ws = world
    for i in range(3):
        open_ticket(client, owner, ws, subject=f"Ticket {i}")
    page = client.get(f"{F}/tickets?limit=2", headers=fh(owner, ws)).json()
    assert len(page["data"]["tickets"]) == 2 and page["meta"]["next_cursor"]
    rest = client.get(
        f"{F}/tickets?limit=2&cursor={page['meta']['next_cursor']}", headers=fh(owner, ws)
    ).json()
    assert len(rest["data"]["tickets"]) == 1 and rest["meta"].get("next_cursor") is None
    assert client.get(f"{F}/tickets?status=resolved", headers=fh(owner, ws)).json()["data"] == {
        "tickets": []
    }
    assert client.get(f"{F}/tickets?cursor=garbage", headers=fh(owner, ws)).status_code == 422


def test_founder_reply_and_reopen_window(client, db, world):
    owner, ws = world
    staff = mk_staff(db)
    t = open_ticket(client, owner, ws)
    r = client.post(
        f"{F}/tickets/{t['id']}/messages", json={"body": "More detail"}, headers=fh(owner, ws)
    )
    assert r.status_code == 201 and len(r.json()["data"]["messages"]) == 2

    client.post(f"{S}/tickets/{t['id']}/resolve", json={}, headers=hdr(staff))
    r = client.post(
        f"{F}/tickets/{t['id']}/messages", json={"body": "Still broken"}, headers=fh(owner, ws)
    )
    assert r.status_code == 201 and r.json()["data"]["status"] == "open"  # T7: reopened
    row = db.query(SupportTicket).one()
    assert row.resolved_at is None and row.first_response_at is None

    client.post(f"{S}/tickets/{t['id']}/resolve", json={}, headers=hdr(staff))
    db.query(SupportTicket).update({"resolved_at": core.now_utc() - timedelta(days=15)})
    db.flush()
    r = client.post(f"{F}/tickets/{t['id']}/messages", json={"body": "Late"}, headers=fh(owner, ws))
    assert r.status_code == 409 and r.json()["error"]["code"] == "TICKET_CLOSED"


# ---------------------------------------------------------------- staff guards

ROUTES = [
    ("get", "/counts"),
    ("get", "/assignees"),
    ("get", "/tickets"),
    ("get", f"/tickets/{uuid.uuid4()}"),
    ("post", f"/tickets/{uuid.uuid4()}/assign"),
    ("post", f"/tickets/{uuid.uuid4()}/reply"),
    ("post", f"/tickets/{uuid.uuid4()}/notes"),
    ("patch", f"/tickets/{uuid.uuid4()}/priority"),
    ("post", f"/tickets/{uuid.uuid4()}/escalate"),
    ("post", f"/tickets/{uuid.uuid4()}/resolve"),
    ("post", f"/tickets/{uuid.uuid4()}/reopen"),
    ("get", "/canned"),
    ("post", "/canned"),
    ("patch", f"/canned/{uuid.uuid4()}"),
    ("delete", f"/canned/{uuid.uuid4()}"),
]


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_staff_routes_hide_from_non_staff_and_need_the_support_scope(client, db, method, path):
    unknown = client.get("/api/v1/definitely-not-a-route").json()
    plain = mk_user(db)
    no_mfa_staff = mk_user(db)  # a founder, even one named in platform_staff, needs TOTP on
    for headers in ({}, hdr(plain), hdr(no_mfa_staff)):
        r = getattr(client, method)(f"{S}{path}", headers=headers)
        assert r.status_code == 404 and r.json() == unknown
    content = mk_staff(db, scopes=("content",))
    r = getattr(client, method)(f"{S}{path}", headers=hdr(content))
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"


# ---------------------------------------------------------------- staff queue


def test_queue_tabs_filters_search_and_counts(client, db, world):
    owner, ws = world
    staff = mk_staff(db)
    a = open_ticket(client, owner, ws, subject="Invoice PDF is blank", category="billing")
    b = open_ticket(client, owner, ws, subject="Dark mode please", category="feature_request")
    h = hdr(staff)

    def ids(qs=""):
        r = client.get(f"{S}/tickets{qs}", headers=h)
        assert r.status_code == 200, r.text
        return [t["id"] for t in r.json()["data"]["tickets"]]

    assert set(ids()) >= {a["id"], b["id"]}
    assert ids("?category=billing") == [a["id"]]
    assert ids("?priority=P2") == [a["id"]]
    assert ids("?query=dark") == [b["id"]]
    assert ids(f"?query={a['number']}") == [a["id"]]
    assert ids("?query=acme") and a["id"] in ids("?query=acme")
    assert ids("?query=ada%20obi") and a["id"] in ids("?query=ada%20obi")
    assert ids("?query=%25") == []  # a LIKE wildcard is taken literally
    assert ids("?tab=mine") == []
    assert ids("?tab=resolved") == []
    assert ids("?unassigned=true") and ids("?escalated=true") == []

    row = client.get(f"{S}/tickets?query=dark", headers=h).json()["data"]["tickets"][0]
    assert row["founder"]["email"] == owner.email and row["workspace"]["plan_key"] == "growth"
    assert row["sla"]["state"] == "ok" and row["assignee"] is None

    c = client.get(f"{S}/counts", headers=h).json()["data"]
    assert c["open"] >= 2 and c["mine"] == 0 and c["unassigned"] >= 2
    assert client.get(f"{S}/tickets?tab=bogus", headers=h).status_code == 422
    assert client.get(f"{S}/tickets?cursor=garbage", headers=h).status_code == 422


def test_sla_chips_and_filter(client, db, world):
    owner, ws = world
    staff = mk_staff(db)
    late = open_ticket(client, owner, ws, subject="Late one")
    soon = open_ticket(client, owner, ws, subject="Soon one")
    fresh = open_ticket(client, owner, ws, subject="Fresh one")
    now = core.now_utc()
    for tid, due in (
        (late["id"], now - timedelta(minutes=5)),
        (soon["id"], now + timedelta(minutes=20)),
    ):
        db.query(SupportTicket).filter(SupportTicket.id == tid).update({"sla_due_at": due})
    db.flush()
    h = hdr(staff)
    state = {
        t["id"]: t["sla"]["state"]
        for t in client.get(f"{S}/tickets", headers=h).json()["data"]["tickets"]
    }
    assert (state[late["id"]], state[soon["id"]], state[fresh["id"]]) == (
        "breached",
        "due_soon",
        "ok",
    )
    ids = lambda q: [  # noqa: E731
        t["id"] for t in client.get(f"{S}/tickets?sla={q}", headers=h).json()["data"]["tickets"]
    ]
    assert ids("breached") == [late["id"]] and ids("due_soon") == [soon["id"]]
    assert client.get(f"{S}/counts", headers=h).json()["data"]["breached"] == 1
    # Answering stops the clock: met when on time, late when after the due time.
    client.post(f"{S}/tickets/{late['id']}/reply", json={"body": "Sorry"}, headers=h)
    client.post(f"{S}/tickets/{soon['id']}/reply", json={"body": "On it"}, headers=h)
    state = {
        t["id"]: t["sla"]["state"]
        for t in client.get(f"{S}/tickets", headers=h).json()["data"]["tickets"]
    }
    assert state[late["id"]] == "late" and state[soon["id"]] == "met"


def test_detail_has_thread_notes_and_only_ticket_context(client, db, world):
    owner, ws = world
    staff = mk_staff(db)
    t = open_ticket(client, owner, ws)
    client.post(
        f"{S}/tickets/{t['id']}/notes",
        json={"body": "Looks like the export queue"},
        headers=hdr(staff),
    )
    d = client.get(f"{S}/tickets/{t['id']}", headers=hdr(staff)).json()["data"]
    assert [n["body"] for n in d["notes"]] == ["Looks like the export queue"]
    assert d["notes"][0]["author"]["email"] == staff.email
    assert set(d["workspace"]) == {"id", "name", "plan_key"}  # D-ADM-9: no deeper account data
    assert set(d["founder"]) == {"id", "name", "email"}
    assert client.get(f"{S}/tickets/{uuid.uuid4()}", headers=hdr(staff)).status_code == 404


def test_internal_notes_and_staff_identity_never_reach_the_founder(client, db, world):
    owner, ws = world
    staff = mk_staff(db, email="secret.agent@cofoundaz.test")
    t = open_ticket(client, owner, ws)
    client.post(
        f"{S}/tickets/{t['id']}/notes", json={"body": "INTERNAL-ONLY-MARKER"}, headers=hdr(staff)
    )
    client.post(
        f"{S}/tickets/{t['id']}/escalate",
        json={"reason": "Founder is a design partner"},
        headers=hdr(staff),
    )
    client.post(f"{S}/tickets/{t['id']}/reply", json={"body": "We are on it"}, headers=hdr(staff))
    for url in (f"{F}/tickets/{t['id']}", f"{F}/tickets"):
        text = client.get(url, headers=fh(owner, ws)).text
        assert "INTERNAL-ONLY-MARKER" not in text and "design partner" not in text
        assert "secret.agent" not in text and str(staff.id) not in text
    msgs = client.get(f"{F}/tickets/{t['id']}", headers=fh(owner, ws)).json()["data"]["messages"]
    assert msgs[1]["author_kind"] == "staff" and msgs[1]["author_name"] == "Cofoundaz Support"
    assert msgs[1]["is_mine"] is False


# ---------------------------------------------------------------- staff actions


def audit(db, action):
    return db.query(AuditLog).filter(AuditLog.action == action).all()


def test_reply_stops_clock_assigns_emails_and_audits(client, db, world, outbox):
    owner, ws = world
    staff = mk_staff(db)
    t = open_ticket(client, owner, ws, subject="Export fails")
    r = client.post(
        f"{S}/tickets/{t['id']}/reply",
        json={"body": "Try again now <b>please</b>"},
        headers=hdr(staff),
    )
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert (
        d["assignee"]["email"] == staff.email
        and d["first_response_at"]
        and d["last_author"] == "staff"
    )
    assert [m["author_kind"] for m in d["messages"]] == ["founder", "staff"]
    (mail,) = outbox.sent
    assert mail.to == owner.email and t["number"] in mail.subject
    assert "&lt;b&gt;" in mail.html and "<b>please" not in mail.html  # body is escaped
    (a,) = audit(db, "ticket.replied")
    assert (
        a.actor_user_id == staff.id and a.startup_id == ws.id and a.entity_type == "support_ticket"
    )
    assert "Try again" not in str(a.details)  # ids only, never message text
    # A second reply keeps the first-reply time and the assignee.
    first = d["first_response_at"]
    other = mk_staff(db)
    d2 = client.post(
        f"{S}/tickets/{t['id']}/reply", json={"body": "More"}, headers=hdr(other)
    ).json()["data"]
    assert d2["first_response_at"] == first and d2["assignee"]["email"] == staff.email


def test_email_failure_does_not_undo_the_reply(client, db, world, monkeypatch):
    from app.core.errors import EmailDeliveryFailed

    class Broken:
        def send(self, msg):
            raise EmailDeliveryFailed()

    monkeypatch.setattr(core, "get_email_sender", lambda: Broken())
    owner, ws = world
    staff = mk_staff(db)
    t = open_ticket(client, owner, ws)
    r = client.post(f"{S}/tickets/{t['id']}/reply", json={"body": "Hello"}, headers=hdr(staff))
    assert r.status_code == 200 and len(r.json()["data"]["messages"]) == 2


def test_reply_and_note_validation(client, db, world):
    owner, ws = world
    staff = mk_staff(db)
    t = open_ticket(client, owner, ws)
    for path in ("reply", "notes"):
        for body in ({"body": ""}, {"body": "x" * 5001}, {}, {"body": "ok", "extra": 1}):
            r = client.post(f"{S}/tickets/{t['id']}/{path}", json=body, headers=hdr(staff))
            assert r.status_code == 422


def test_assign_rules(client, db, world):
    owner, ws = world
    staff, mate = mk_staff(db), mk_staff(db)
    content_only = mk_staff(db, scopes=("content",))
    sa = mk_staff(db, StaffRole.super_admin)
    t = open_ticket(client, owner, ws)
    names = {
        s["email"] for s in client.get(f"{S}/assignees", headers=hdr(staff)).json()["data"]["staff"]
    }
    assert {staff.email, mate.email, sa.email} <= names and content_only.email not in names

    def assign(who):
        return client.post(
            f"{S}/tickets/{t['id']}/assign", json={"assignee_user_id": who}, headers=hdr(staff)
        )

    r = assign(str(mate.id))
    assert r.status_code == 200 and r.json()["data"]["assignee"]["id"] == str(mate.id)
    assert (
        client.get(f"{S}/tickets?tab=mine", headers=hdr(mate)).json()["data"]["tickets"][0]["id"]
        == t["id"]
    )
    assert assign(str(mate.id)).json()["error"]["code"] == "NO_CHANGE"
    for bad in (str(content_only.id), str(owner.id), str(uuid.uuid4())):
        assert assign(bad).json()["error"]["code"] == "ASSIGNEE_NOT_ALLOWED"
    assert assign(None).json()["data"]["assignee"] is None
    assert len(audit(db, "ticket.assigned")) == 2


def test_priority_change_moves_the_deadline_until_answered(client, db, world):
    owner, ws = world
    staff = mk_staff(db)
    t = open_ticket(client, owner, ws)  # P3, 24h
    row = db.query(SupportTicket).one()
    created = row.created_at
    r = client.patch(f"{S}/tickets/{t['id']}/priority", json={"priority": "P1"}, headers=hdr(staff))
    assert r.status_code == 200 and r.json()["data"]["priority"] == "P1"
    db.refresh(row)
    assert row.sla_due_at == created + timedelta(hours=1)
    assert (
        client.patch(
            f"{S}/tickets/{t['id']}/priority", json={"priority": "P1"}, headers=hdr(staff)
        ).json()["error"]["code"]
        == "NO_CHANGE"
    )
    assert (
        client.patch(
            f"{S}/tickets/{t['id']}/priority", json={"priority": "P9"}, headers=hdr(staff)
        ).status_code
        == 422
    )
    (a,) = audit(db, "ticket.priority_changed")
    assert a.details["from"] == "P3" and a.details["to"] == "P1"


def test_escalate_needs_a_reason_sets_p1_and_leaves_a_note(client, db, world):
    owner, ws = world
    staff = mk_staff(db)
    t = open_ticket(client, owner, ws)
    url = f"{S}/tickets/{t['id']}/escalate"
    assert client.post(url, json={}, headers=hdr(staff)).status_code == 422
    assert client.post(url, json={"reason": "short"}, headers=hdr(staff)).status_code == 422
    r = client.post(url, json={"reason": "Design partner is blocked"}, headers=hdr(staff))
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["priority"] == "P1" and d["escalated"] is True
    assert d["escalation_reason"] == "Design partner is blocked"
    assert "Design partner is blocked" in d["notes"][0]["body"]
    row = db.query(SupportTicket).one()
    assert row.sla_due_at <= core.now_utc() + timedelta(hours=1, minutes=1)
    assert (
        client.post(url, json={"reason": "Again, for sure"}, headers=hdr(staff)).json()["error"][
            "code"
        ]
        == "ALREADY_ESCALATED"
    )
    (a,) = audit(db, "ticket.escalated")
    assert a.reason == "Design partner is blocked" and a.actor_user_id == staff.id
    assert [
        x["id"]
        for x in client.get(f"{S}/tickets?escalated=true", headers=hdr(staff)).json()["data"][
            "tickets"
        ]
    ] == [t["id"]]


def test_resolve_emails_and_reopen_restarts_the_clock(client, db, world, outbox):
    owner, ws = world
    staff = mk_staff(db)
    t = open_ticket(client, owner, ws)
    r = client.post(
        f"{S}/tickets/{t['id']}/resolve",
        json={"message": "Fixed in today's release."},
        headers=hdr(staff),
    )
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert (
        d["status"] == "resolved" and d["resolved_at"] and d["resolved_by"]["email"] == staff.email
    )
    assert d["messages"][-1]["body"] == "Fixed in today's release."
    (mail,) = outbox.sent
    assert (
        mail.to == owner.email
        and mail.subject.startswith("Resolved:")
        and "Fixed in today" in mail.html
    )
    assert (
        client.get(f"{S}/tickets?tab=resolved", headers=hdr(staff)).json()["data"]["tickets"][0][
            "id"
        ]
        == t["id"]
    )
    # Acting on a resolved ticket is refused until it is reopened.
    for path, body in (
        ("reply", {"body": "x"}),
        ("resolve", {}),
        ("escalate", {"reason": "Because reasons"}),
    ):
        r = client.post(f"{S}/tickets/{t['id']}/{path}", json=body, headers=hdr(staff))
        assert r.status_code == 409 and r.json()["error"]["code"] == "TICKET_RESOLVED"
    assert (
        client.post(
            f"{S}/tickets/{t['id']}/reopen",
            json={"reason": "Customer says it is back"},
            headers=hdr(staff),
        ).json()["data"]["status"]
        == "open"
    )
    row = db.query(SupportTicket).one()
    assert row.first_response_at is None and row.status == TicketStatus.open
    assert (
        client.post(f"{S}/tickets/{t['id']}/reopen", json={}, headers=hdr(staff)).json()["error"][
            "code"
        ]
        == "TICKET_NOT_RESOLVED"
    )
    assert len(audit(db, "ticket.resolved")) == 1 and len(audit(db, "ticket.reopened")) == 1


def test_resolve_without_message_still_emails(client, db, world, outbox):
    owner, ws = world
    staff = mk_staff(db)
    t = open_ticket(client, owner, ws)
    assert (
        client.post(f"{S}/tickets/{t['id']}/resolve", json={}, headers=hdr(staff)).status_code
        == 200
    )
    assert len(outbox.sent) == 1


def test_a_ticket_is_still_staff_visible_after_its_author_is_gone(client, db, world):
    owner, ws = world
    staff = mk_staff(db)
    t = open_ticket(client, owner, ws)
    db.query(SupportTicket).update({"created_by": None})
    db.flush()
    d = client.get(f"{S}/tickets/{t['id']}", headers=hdr(staff)).json()["data"]
    assert d["founder"] is None
    assert (
        client.post(
            f"{S}/tickets/{t['id']}/reply", json={"body": "hello"}, headers=hdr(staff)
        ).status_code
        == 200
    )


def test_canned_responses_crud_and_audit(client, db):
    staff = mk_staff(db)
    h = hdr(staff)
    r = client.post(
        f"{S}/canned", json={"title": "Greeting", "body": "Hi, thanks for reaching out."}, headers=h
    )
    assert r.status_code == 201, r.text
    cid = r.json()["data"]["id"]
    assert client.post(f"{S}/canned", json={"title": "", "body": "x"}, headers=h).status_code == 422
    assert client.get(f"{S}/canned", headers=h).json()["data"]["canned"][0]["title"] == "Greeting"
    r = client.patch(f"{S}/canned/{cid}", json={"body": "Hello!"}, headers=h)
    assert r.json()["data"]["body"] == "Hello!" and r.json()["data"]["title"] == "Greeting"
    assert (
        client.patch(f"{S}/canned/{cid}", json={}, headers=h).json()["error"]["code"] == "NO_CHANGE"
    )
    assert client.delete(f"{S}/canned/{cid}", headers=h).json()["data"]["deleted"] is True
    assert client.delete(f"{S}/canned/{cid}", headers=h).status_code == 404
    assert (
        client.patch(f"{S}/canned/{uuid.uuid4()}", json={"title": "x"}, headers=h).status_code
        == 404
    )
    assert [len(audit(db, a)) for a in ("canned.created", "canned.updated", "canned.deleted")] == [
        1,
        1,
        1,
    ]
    # Founders have no route to the canned replies.
    owner = mk_user(db)
    assert client.get(f"{S}/canned", headers=hdr(owner)).status_code == 404


def test_dashboard_counts_open_tickets_for_any_staff(client, db, world):
    owner, ws = world
    content = mk_staff(db, scopes=("content",))
    open_ticket(client, owner, ws)
    data = client.get("/api/v1/admin/dashboard/attention", headers=hdr(content)).json()["data"]
    assert data["tickets"]["open"] >= 1 and data["tickets"]["breached"] == 0
    assert db.query(SupportTicketNote).count() == 0
