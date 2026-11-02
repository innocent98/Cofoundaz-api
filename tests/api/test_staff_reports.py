"""Reported issues: the founder's "report this" action and the staff moderation console."""

import uuid

import pyotp
import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.redis import get_redis
from app.db.models.ai_chat import AiConversation, AiMessage
from app.db.models.audit import AuditLog
from app.db.models.comment import Comment
from app.db.models.content_report import ContentReport
from app.db.models.enums import (
    CanvasType,
    ChatMessageRole,
    ChatMessageStatus,
    MembershipRole,
    ReportStatus,
    StaffRole,
)
from app.services.business.service import get_or_create_canvas
from tests.api.test_staff_users import hdr, member, mk_staff, mk_user, mk_ws

S = "/api/v1/admin/reports"
F = "/api/v1/reports"
REASON = "Reviewed the snippet against the guidelines"
SECRET_THREAD_TEXT = "THREAD-ONLY-SENTINEL-DO-NOT-LEAK"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    r = get_redis()
    for pat in ("report_rl:*", "staff_stepup_*"):
        for k in r.scan_iter(pat):
            r.delete(k)


def fh(user, ws):
    return {**hdr(user), "X-Workspace-Id": str(ws.id)}


class World:
    pass


@pytest.fixture()
def world(db):
    w = World()
    w.owner = mk_user(db, name="Ada Obi")
    w.ws = mk_ws(db, w.owner, name="Acme Co", plan="growth")
    w.mate = mk_user(db, name="Tunde Bello")
    member(db, w.ws, w.mate, MembershipRole.team_member)
    w.canvas = get_or_create_canvas(db, w.ws, CanvasType.swot)
    db.flush()
    return w


def mk_comment(db, ws, author, entity_id=None, body="You are all wrong about this.", **kw):
    c = Comment(
        startup_id=ws.id,
        entity_type=kw.pop("entity_type", "business_canvas"),
        entity_id=entity_id or uuid.uuid4(),
        author_id=author.id,
        body=body,
        **kw,
    )
    db.add(c)
    db.flush()
    return c


def mk_chat(db, ws, user, reply="Here is a confident but wrong answer.", status=None):
    conv = AiConversation(startup_id=ws.id, created_by=user.id, title="t")
    db.add(conv)
    db.flush()
    ask = AiMessage(conversation_id=conv.id, role=ChatMessageRole.user, content=SECRET_THREAD_TEXT)
    ans = AiMessage(
        conversation_id=conv.id,
        role=ChatMessageRole.assistant,
        content=reply,
        status=status or ChatMessageStatus.ready,
    )
    db.add_all([ask, ans])
    db.flush()
    return conv, ask, ans


def report(client, user, ws, item_type, item_id, reason="inaccurate", **extra):
    body = {"item_type": item_type, "item_id": str(item_id), "reason": reason, **extra}
    return client.post(F, json=body, headers=fh(user, ws))


def filed(client, user, ws, item_type, item_id, **kw):
    r = report(client, user, ws, item_type, item_id, **kw)
    assert r.status_code == 201, r.text
    return r.json()["data"]


def act(client, staff, rid, action, step_up=False, reason=REASON, **extra):
    body = {"reason": reason, **extra}
    if step_up:
        r = get_redis()
        for k in r.scan_iter("staff_stepup_used:*"):
            r.delete(k)
        body["totp_code"] = pyotp.TOTP(staff.test_secret).now()
    return client.post(f"{S}/{rid}/{action}", json=body, headers=hdr(staff))


def moderator(db, **kw):
    return mk_staff(db, scopes=("moderation",), **kw)


# ---------------------------------------------------------------- founder: filing


def test_report_a_comment_copies_the_snippet(client, db, world):
    c = mk_comment(db, world.ws, world.mate)
    out = filed(client, world.owner, world.ws, "comment", c.id, reason="abusive", note="Rude")
    assert out["status"] == "received" and out["item_type"] == "comment"
    assert set(out) == {"id", "item_type", "item_id", "reason", "status", "created_at"}
    row = db.get(ContentReport, uuid.UUID(out["id"]))
    assert row.snippet == c.body and row.item_author_id == world.mate.id
    assert row.startup_id == world.ws.id and row.reporter_id == world.owner.id
    audit = db.query(AuditLog).filter(AuditLog.action == "report.filed").one()
    assert c.body not in str(audit.details)


def test_report_an_ai_reply(client, db, world):
    _, _, ans = mk_chat(db, world.ws, world.owner)
    out = filed(client, world.owner, world.ws, "ai_message", ans.id, note="Wrong runway")
    row = db.get(ContentReport, uuid.UUID(out["id"]))
    assert row.snippet == ans.content and row.item_author_id is None
    assert SECRET_THREAD_TEXT not in row.snippet


def test_long_snippet_is_capped(client, db, world):
    c = mk_comment(db, world.ws, world.mate, body="x" * 5000)
    out = filed(client, world.owner, world.ws, "comment", c.id)
    assert len(db.get(ContentReport, uuid.UUID(out["id"])).snippet) == 2000


@pytest.mark.parametrize(
    "bad",
    [
        {"item_type": "review"},
        {"reason": "nope"},
        {"reason": "other"},  # needs a note
        {"note": "n" * 501},
        {"note": "   "},
        {"extra": 1},
        {"item_id": "not-a-uuid"},
    ],
)
def test_report_validation(client, db, world, bad):
    c = mk_comment(db, world.ws, world.mate)
    body = {"item_type": "comment", "item_id": str(c.id), "reason": "spam", **bad}
    resp = client.post(F, json=body, headers=fh(world.owner, world.ws))
    assert resp.status_code == 422


def test_cannot_report_your_own_comment(client, db, world):
    c = mk_comment(db, world.ws, world.owner)
    r = report(client, world.owner, world.ws, "comment", c.id)
    assert r.status_code == 422 and r.json()["error"]["code"] == "OWN_COMMENT"


def test_cross_workspace_and_private_items_are_404(client, db, world):
    other_owner = mk_user(db)
    other_ws = mk_ws(db, other_owner, name="Other Co")
    canvas = get_or_create_canvas(db, other_ws, CanvasType.swot)
    theirs = mk_comment(db, other_ws, other_owner, canvas.id)
    _, _, their_ai = mk_chat(db, other_ws, other_owner)
    # A teammate's own AI chat is private to them, even inside the same workspace.
    _, _, mate_ai = mk_chat(db, world.ws, world.mate)
    _, ask, _ = mk_chat(db, world.ws, world.owner)
    _, _, failed = mk_chat(db, world.ws, world.owner, status=ChatMessageStatus.failed)
    doc = mk_comment(db, world.ws, world.mate, world.canvas.id, entity_type="document")
    for kind, item in (
        ("comment", theirs),
        ("ai_message", their_ai),
        ("ai_message", mate_ai),
        ("ai_message", ask),  # a person's own prompt is not an AI output
        ("ai_message", failed),
        ("comment", doc),
        ("comment", type("X", (), {"id": uuid.uuid4()})),
        ("ai_message", type("X", (), {"id": uuid.uuid4()})),
    ):
        resp = report(client, world.owner, world.ws, kind, item.id)
        assert resp.status_code == 404
    # The other workspace's id with my own token is refused outright (not a member).
    r = client.post(
        F,
        json={"item_type": "comment", "item_id": str(theirs.id), "reason": "spam"},
        headers=fh(world.owner, other_ws),
    )
    assert r.status_code == 403
    assert db.query(ContentReport).count() == 0


def test_needs_auth_and_membership(client, db, world):
    c = mk_comment(db, world.ws, world.mate)
    body = {"item_type": "comment", "item_id": str(c.id), "reason": "spam"}
    resp = client.post(F, json=body)
    assert resp.status_code in (401, 422)
    outsider = mk_user(db)
    resp = client.post(F, json=body, headers=fh(outsider, world.ws))
    assert resp.status_code == 403


def test_one_live_report_per_person_per_item(client, db, world):
    c = mk_comment(db, world.ws, world.mate)
    filed(client, world.owner, world.ws, "comment", c.id)
    again = report(client, world.owner, world.ws, "comment", c.id)
    assert again.status_code == 409 and again.json()["error"]["code"] == "ALREADY_REPORTED"
    # A second person may report the same item; the first may report once the case closes.
    third = mk_user(db)
    member(db, world.ws, third, MembershipRole.team_member)
    filed(client, third, world.ws, "comment", c.id)
    row = db.query(ContentReport).filter_by(reporter_id=world.owner.id).one()
    row.status = ReportStatus.dismissed
    db.flush()
    filed(client, world.owner, world.ws, "comment", c.id)


def test_reporting_is_rate_limited(client, db, world):
    for _ in range(10):
        c = mk_comment(db, world.ws, world.mate)
        filed(client, world.owner, world.ws, "comment", c.id)
    c = mk_comment(db, world.ws, world.mate)
    r = report(client, world.owner, world.ws, "comment", c.id)
    assert r.status_code == 429 and r.json()["error"]["code"] == "RATE_LIMITED"


def test_my_reports_lists_only_mine_and_live(client, db, world):
    c1, c2 = (mk_comment(db, world.ws, world.mate) for _ in range(2))
    mine = filed(client, world.owner, world.ws, "comment", c1.id)
    filed(client, world.owner, world.ws, "comment", c2.id)
    third = mk_user(db)
    member(db, world.ws, third, MembershipRole.team_member)
    filed(client, third, world.ws, "comment", c1.id)
    db.get(ContentReport, uuid.UUID(mine["id"])).status = ReportStatus.removed
    db.flush()
    got = client.get(f"{F}/mine", headers=fh(world.owner, world.ws)).json()["data"]["reports"]
    assert [g["item_id"] for g in got] == [str(c2.id)]
    theirs = client.get(f"{F}/mine", headers=fh(third, world.ws)).json()["data"]["reports"]
    assert [g["item_id"] for g in theirs] == [str(c1.id)]


# ---------------------------------------------------------------- staff: guards

ROUTES = [
    ("get", ""),
    ("get", "/counts"),
    ("get", f"/{uuid.uuid4()}"),
    ("post", f"/{uuid.uuid4()}/dismiss"),
    ("post", f"/{uuid.uuid4()}/escalate"),
    ("post", f"/{uuid.uuid4()}/remove"),
    ("post", f"/{uuid.uuid4()}/restore"),
]


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_only_moderation_staff_get_through(client, db, world, method, path):
    url = f"{S}{path}"
    send = getattr(client, method)
    resp = send(url)
    assert resp.status_code == 404  # signed out: the route hides itself
    resp = send(url, headers=hdr(world.owner))
    assert resp.status_code == 404  # a founder
    support_only = mk_staff(db, scopes=("support",))
    resp = send(url, headers=hdr(support_only))
    assert resp.status_code == 403
    content_only = mk_staff(db, scopes=("content",))
    resp = send(url, headers=hdr(content_only))
    assert resp.status_code == 403
    for ok in (moderator(db), mk_staff(db, role=StaffRole.super_admin)):
        resp = send(url, headers=hdr(ok))
        assert resp.status_code not in (401, 403)


# ---------------------------------------------------------------- staff: queue


def test_queue_tabs_filters_search_and_paging(client, db, world):
    mod = moderator(db)
    c1 = mk_comment(db, world.ws, world.mate, body="needle-comment text")
    c2 = mk_comment(db, world.ws, world.mate, body="plain comment")
    _, _, ans = mk_chat(db, world.ws, world.owner, reply="ai output body")
    r1 = filed(client, world.owner, world.ws, "comment", c1.id, reason="spam")
    filed(client, world.owner, world.ws, "comment", c2.id, reason="abusive")
    filed(client, world.owner, world.ws, "ai_message", ans.id, reason="inaccurate")
    mine = {r1["id"]}

    def ids(**params):
        r = client.get(S, params=params, headers=hdr(mod))
        assert r.status_code == 200, r.text
        return {x["id"] for x in r.json()["data"]["reports"]}

    assert r1["id"] in ids()
    assert ids(type="ai_message") and all(
        x["item_type"] == "ai_message"
        for x in client.get(S, params={"type": "ai_message"}, headers=hdr(mod)).json()["data"][
            "reports"
        ]
    )
    assert ids(reason="spam") >= mine
    assert ids(query="needle-comment") == mine
    assert ids(query="Acme") >= mine and ids(query="ada obi") >= mine
    assert ids(query="zzz-no-such") == set()
    assert not ids(tab="escalated") & mine and not ids(tab="closed") & mine
    two = client.get(S, params={"limit": 2}, headers=hdr(mod)).json()
    assert len(two["data"]["reports"]) == 2 and two["meta"]["next_cursor"]
    nxt = client.get(
        S, params={"limit": 2, "cursor": two["meta"]["next_cursor"]}, headers=hdr(mod)
    ).json()
    assert {x["id"] for x in nxt["data"]["reports"]}.isdisjoint(
        {x["id"] for x in two["data"]["reports"]}
    )
    for bad in ({"tab": "nope"}, {"type": "review"}, {"reason": "x"}, {"cursor": "junk"}):
        resp = client.get(S, params=bad, headers=hdr(mod))
        assert resp.status_code == 422
    counts = client.get(f"{S}/counts", headers=hdr(mod)).json()["data"]
    assert set(counts) == {"open", "escalated", "closed"} and counts["open"] >= 3


def test_list_truncates_and_detail_shows_only_the_snippet(client, db, world):
    mod = moderator(db)
    long = "L" * 500
    c = mk_comment(db, world.ws, world.mate, body=long)
    conv, ask, ans = mk_chat(db, world.ws, world.owner)
    rc = filed(client, world.owner, world.ws, "comment", c.id)
    ra = filed(client, world.owner, world.ws, "ai_message", ans.id)
    row = client.get(S, headers=hdr(mod)).json()["data"]["reports"]
    mine = next(x for x in row if x["id"] == rc["id"])
    assert len(mine["snippet"]) == 200 and mine["snippet_truncated"] is True
    d = client.get(f"{S}/{rc['id']}", headers=hdr(mod)).json()["data"]
    assert d["snippet"] == long and d["reports_on_item"] == 1
    assert d["reporter"]["email"] == world.owner.email and d["reporter"]["name"] == "Ada Obi"
    assert d["author"] == {"id": str(world.mate.id), "name": "Tunde Bello"}  # no email (R5)
    assert d["workspace"]["name"] == "Acme Co" and d["workspace"]["plan_key"] == "growth"
    # The chat the AI reply came from is never read: not its prompt, title or conversation id.
    blob = client.get(f"{S}/{ra['id']}", headers=hdr(mod)).text
    blob += client.get(S, headers=hdr(mod)).text
    assert SECRET_THREAD_TEXT not in blob
    assert str(conv.id) not in blob and str(ask.id) not in blob
    resp = client.get(f"{S}/{uuid.uuid4()}", headers=hdr(mod))
    assert resp.status_code == 404


# ---------------------------------------------------------------- staff: actions


def test_dismiss(client, db, world):
    mod = moderator(db)
    c = mk_comment(db, world.ws, world.mate)
    rid = filed(client, world.owner, world.ws, "comment", c.id)["id"]
    short = act(client, mod, rid, "dismiss", reason="no")
    assert short.status_code == 422
    r = act(client, mod, rid, "dismiss")  # no authenticator code needed
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["status"] == "dismissed" and d["decision_reason"] == REASON
    assert d["decided_by"]["id"] == str(mod.id)
    assert db.get(Comment, c.id).removed_by_moderation_at is None
    resp = act(client, mod, rid, "dismiss")
    assert resp.status_code == 409
    resp = act(client, mod, rid, "escalate")
    assert resp.status_code == 409
    row = db.query(AuditLog).filter(AuditLog.action == "report.dismissed").one()
    assert row.actor_user_id == mod.id and row.reason == REASON
    assert c.body not in str(row.details)


def test_escalate_then_only_a_super_admin_decides(client, db, world):
    mod, boss = moderator(db), mk_staff(db, role=StaffRole.super_admin)
    c = mk_comment(db, world.ws, world.mate)
    rid = filed(client, world.owner, world.ws, "comment", c.id)["id"]
    resp = act(client, mod, rid, "escalate", reason="short")
    assert resp.status_code == 422
    r = act(client, mod, rid, "escalate")
    assert r.status_code == 200 and r.json()["data"]["status"] == "escalated"
    assert r.json()["data"]["escalation_reason"] == REASON
    again = act(client, mod, rid, "escalate")
    assert again.status_code == 409 and again.json()["error"]["code"] == "ALREADY_ESCALATED"
    for action, step in (("dismiss", False), ("remove", True)):
        r = act(client, mod, rid, action, step_up=step)
        assert r.status_code == 403 and r.json()["error"]["code"] == "SUPER_ADMIN_ONLY"
    assert db.get(Comment, c.id).removed_by_moderation_at is None
    esc = client.get(S, params={"tab": "escalated"}, headers=hdr(mod)).json()["data"]["reports"]
    assert rid in {x["id"] for x in esc}
    resp = act(client, boss, rid, "dismiss")
    assert resp.json()["data"]["status"] == "dismissed"


def test_remove_hides_the_comment_for_its_author_and_closes_siblings(client, db, world):
    mod, boss = moderator(db), mk_staff(db, role=StaffRole.super_admin)
    c = mk_comment(db, world.ws, world.mate, world.canvas.id, body="Awful words")
    third = mk_user(db)
    member(db, world.ws, third, MembershipRole.team_member)
    r1 = filed(client, world.owner, world.ws, "comment", c.id)["id"]
    r2 = filed(client, third, world.ws, "comment", c.id)["id"]
    act(client, mod, r1, "escalate")
    r = act(client, boss, r1, "remove", step_up=True)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "removed"
    assert db.get(ContentReport, uuid.UUID(r2)).status == ReportStatus.removed  # R8
    thread = client.get(
        "/api/v1/team/comments",
        params={"entity_type": "business_canvas", "entity_id": str(world.canvas.id)},
        headers=fh(world.mate, world.ws),
    ).json()["data"]
    mine = next(x for x in thread if x["id"] == str(c.id))
    assert mine["removed_by_moderation"] is True and mine["body"] == "Removed by moderation"
    assert "Awful words" not in str(thread)
    # The author cannot edit it back into view.
    edit = client.patch(
        f"/api/v1/team/comments/{c.id}", json={"body": "again"}, headers=fh(world.mate, world.ws)
    )
    assert edit.status_code == 403
    audit = db.query(AuditLog).filter(AuditLog.action == "report.item_removed").one()
    assert audit.details["reports_closed"] == 2 and "Awful words" not in str(audit.details)
    # A removed item cannot be reported again.
    resp = report(client, third, world.ws, "comment", c.id)
    assert resp.status_code == 404


def test_remove_hides_an_ai_reply_and_restore_brings_it_back(client, db, world):
    mod = moderator(db)
    conv, _, ans = mk_chat(db, world.ws, world.owner, reply="Harmful advice")
    rid = filed(client, world.owner, world.ws, "ai_message", ans.id, reason="harmful")["id"]
    resp = act(client, mod, rid, "remove", step_up=True)
    assert resp.status_code == 200
    url = f"/api/v1/ai/conversations/{conv.id}"
    msgs = client.get(url, headers=fh(world.owner, world.ws)).json()["data"]["messages"]
    shown = next(m for m in msgs if m["id"] == str(ans.id))
    assert shown["removed_by_moderation"] is True and shown["content"] == "Removed by moderation"
    # Restore is reversible, needs a reason and a code, and only works on a removed report.
    resp = act(client, mod, rid, "restore")
    assert resp.status_code == 422  # no code
    r = act(client, mod, rid, "restore", step_up=True)
    assert r.status_code == 200 and r.json()["data"]["status"] == "restored"
    msgs = client.get(url, headers=fh(world.owner, world.ws)).json()["data"]["messages"]
    back = next(m for m in msgs if m["id"] == str(ans.id))
    assert back["removed_by_moderation"] is False and back["content"] == "Harmful advice"
    resp = act(client, mod, rid, "restore", step_up=True)
    assert resp.status_code == 409
    assert db.query(AuditLog).filter(AuditLog.action == "report.item_restored").count() == 1


def test_remove_and_restore_need_a_fresh_code(client, db, world):
    mod = moderator(db)
    c = mk_comment(db, world.ws, world.mate)
    rid = filed(client, world.owner, world.ws, "comment", c.id)["id"]
    r = client.post(
        f"{S}/{rid}/remove",
        json={"reason": REASON, "totp_code": "000000"},
        headers=hdr(mod),
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "STEP_UP_INVALID"
    assert db.get(Comment, c.id).removed_by_moderation_at is None
    resp = act(client, mod, rid, "remove", step_up=False)
    assert resp.status_code == 422
    extra = client.post(
        f"{S}/{rid}/dismiss", json={"reason": REASON, "totp_code": "123456"}, headers=hdr(mod)
    )
    assert extra.status_code == 422  # dismiss takes no code


def test_remove_when_the_author_already_deleted_the_item(client, db, world):
    mod = moderator(db)
    c = mk_comment(db, world.ws, world.mate)
    rid = filed(client, world.owner, world.ws, "comment", c.id)["id"]
    db.delete(c)
    db.flush()
    r = act(client, mod, rid, "remove", step_up=True)
    assert r.status_code == 200 and r.json()["data"]["status"] == "removed"
    audit = db.query(AuditLog).filter(AuditLog.action == "report.item_removed").one()
    assert audit.details["item_existed"] is False
    # The snippet survives for the record.
    resp = client.get(f"{S}/{rid}", headers=hdr(mod))
    assert resp.json()["data"]["snippet"]


def test_a_moderator_cannot_close_an_escalation_by_removing_a_sibling_report(client, db, world):
    mod, boss = moderator(db), mk_staff(db, role=StaffRole.super_admin)
    c = mk_comment(db, world.ws, world.mate)
    third = mk_user(db)
    member(db, world.ws, third, MembershipRole.team_member)
    escalated = filed(client, world.owner, world.ws, "comment", c.id)["id"]
    plain = filed(client, third, world.ws, "comment", c.id)["id"]
    act(client, mod, escalated, "escalate")
    blocked = act(client, mod, plain, "remove", step_up=True)
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "SUPER_ADMIN_ONLY"
    assert db.get(Comment, c.id).removed_by_moderation_at is None
    assert db.get(ContentReport, uuid.UUID(escalated)).status == ReportStatus.escalated
    # A Super Admin's removal answers both; a moderator then cannot undo it.
    assert act(client, boss, plain, "remove", step_up=True).status_code == 200
    undo = act(client, mod, plain, "restore", step_up=True)
    assert undo.status_code == 403 and undo.json()["error"]["code"] == "SUPER_ADMIN_ONLY"
    assert db.get(Comment, c.id).removed_by_moderation_at is not None
    assert act(client, boss, plain, "restore", step_up=True).status_code == 200


def test_removed_comment_text_is_hidden_in_the_threads_index(client, db, world):
    mod = moderator(db)
    c = mk_comment(db, world.ws, world.mate, world.canvas.id, body="Quiet abuse text")
    rid = filed(client, world.owner, world.ws, "comment", c.id)["id"]
    act(client, mod, rid, "remove", step_up=True)
    r = client.get("/api/v1/team/comments/threads", headers=fh(world.mate, world.ws))
    assert r.status_code == 200 and "Quiet abuse text" not in r.text
    assert any(t["entity_id"] == str(world.canvas.id) for t in r.json()["data"])  # still listed
