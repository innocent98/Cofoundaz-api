"""AI Co-Founder slice 3 over HTTP: confirm-first actions, feedback, regenerate, suggestions,
the bench, and a pinned specialist. Assign-then-assert throughout."""

import datetime as dt
import json
import uuid
from datetime import UTC, datetime

import pytest

from app.core.config import settings
from app.core.security import create_access_token
from app.db.models.ai_chat import AiAction, AiAgentRoute, AiConversation, AiMessage, AiSuggestion
from app.db.models.dashboard import DailyBriefing
from app.db.models.document import Document
from app.db.models.enums import (
    BriefingStatus,
    ChatMessageRole,
    ChatMessageStatus,
    MembershipRole,
    RoadmapStatus,
)
from app.db.models.llm_usage import LlmUsageDaily
from app.db.models.roadmap import RoadmapTask
from app.platform import llm_budget
from app.platform.events import event_bus
from app.platform.llm_budget import today_utc
from app.services.ai_preferences import update_prefs
from app.worker import runner
from app.worker.handlers import ai as _ai  # noqa: F401  (registers ai.cofounder.reply)
from tests.factories import (
    create_membership,
    create_milestone,
    create_phase,
    create_recommendation,
    create_roadmap,
    create_startup,
    create_user,
)

C = "/api/v1/ai/conversations"
AI = "/api/v1/ai"


class _LLM:
    """Scripted model: `reply` is returned verbatim by every text call."""

    def __init__(self, reply="Here is my answer.", tokens=10):
        self.reply = reply
        self.last_usage_tokens = tokens
        self.calls = 0

    def complete(self, messages, *, max_tokens, temperature=0.7):
        self.calls += 1
        return self.reply

    def complete_json(self, messages, *, schema, max_tokens):
        return {"agent": "strategist", "confidence": 0.7}


def _actions_block(*items) -> str:
    return "Here is my take.\n<actions>" + json.dumps(list(items)) + "</actions>"


@pytest.fixture()
def llm(monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 100_000)
    holder = _LLM()
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: holder)
    return holder


def _hdr(user, s):
    return {
        "Authorization": f"Bearer {create_access_token(str(user.id))}",
        "X-Workspace-Id": str(s.id),
    }


def _world(db, with_roadmap=True):
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=owner)
    create_membership(db, owner, s, role=MembershipRole.founder)
    ms = []
    if with_roadmap:
        road = create_roadmap(db, s)
        phase = create_phase(db, road)
        ms = [
            create_milestone(db, phase, title="Ship the MVP", due_on=dt.date(2027, 1, 10), order=0),
            create_milestone(
                db, phase, title="First 10 customers", due_on=dt.date(2027, 2, 1), order=1
            ),
        ]
    db.flush()
    return owner, s, _hdr(owner, s), ms


def _member(db, s, role):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, u, s, role=role)
    db.flush()
    return u, _hdr(u, s)


def _work(db):
    for _ in range(6):
        if runner.run_once(db) == 0:
            break


def _ask(client, db, h, text="What should I focus on this week?", **extra):
    db.commit()
    r = client.post(C, headers=h, json={"message": text, **extra})
    assert r.status_code == 201, r.text
    cid = r.json()["data"]["id"]
    _work(db)
    got = client.get(f"{C}/{cid}", headers=h).json()["data"]
    return got, got["messages"][1]


# ---------------------------------------------------------------- proposals


def test_a_reply_can_propose_actions_and_nothing_is_written_until_confirmed(client, db, llm):
    _o, s, h, _ms = _world(db)
    llm.reply = _actions_block(
        {"kind": "add_task", "title": "Call five customers", "milestone": "M1", "effort": "small"},
        {"kind": "update_roadmap", "milestone": "M2", "due_on": "2027-03-01"},
        {
            "kind": "draft_document",
            "doc_kind": "one_pager",
            "title": "Pitch one pager",
            "sections": [{"heading": "Problem", "body": "Founders lose time."}],
        },
    )
    conv, reply = _ask(client, db, h)

    assert reply["content"] == "Here is my take."  # the block is not part of the text
    assert [a["kind"] for a in reply["actions"]] == ["add_task", "update_roadmap", "draft_document"]
    assert {a["status"] for a in reply["actions"]} == {"proposed"}
    assert reply["actions"][0]["label"] == "Add as task: Call five customers"
    assert all(not a["undoable"] for a in reply["actions"])
    assert db.query(RoadmapTask).count() == 0 and db.query(Document).count() == 0
    assert conv["topic"] == "strategy"


def test_invalid_proposals_are_dropped_and_the_reply_still_lands(client, db, llm):
    _o, s, h, _ms = _world(db)
    llm.reply = _actions_block(
        {"kind": "delete_everything"},
        {"kind": "add_task", "title": "x", "milestone": "M9"},  # no such milestone key
        {"kind": "update_roadmap", "milestone": "M1", "due_on": "not-a-date"},
        {"kind": "add_task", "title": "Real one", "milestone": "M1"},
    )
    _conv, reply = _ask(client, db, h)
    assert reply["status"] == "ready"
    assert [a["label"] for a in reply["actions"]] == ["Add as task: Real one"]


def test_garbage_in_the_block_costs_the_chips_never_the_reply(client, db, llm):
    _o, s, h, _ms = _world(db)
    llm.reply = "Plain answer.\n<actions>{this is not json</actions>"
    _conv, reply = _ask(client, db, h)
    assert reply["content"] == "Plain answer." and reply["actions"] == []
    llm.reply = 'Cut off\n<actions>[{"kind":'
    _conv2, reply2 = _ask(client, db, h)
    assert reply2["content"] == "Cut off" and reply2["actions"] == []


def test_the_model_cannot_name_a_database_id(client, db, llm):
    _o, s, h, ms = _world(db)
    other_owner = create_user(db)
    other = create_startup(db, owner=other_owner)
    other_ms = create_milestone(db, create_phase(db, create_roadmap(db, other)), title="Not yours")
    db.flush()
    llm.reply = _actions_block(
        {"kind": "add_task", "title": "Sneaky", "milestone": str(other_ms.id)},
        {"kind": "add_task", "title": "Own id", "milestone": str(ms[0].id)},
    )
    _conv, reply = _ask(client, db, h)
    assert reply["actions"] == []


def test_agents_only_propose_their_own_kinds(client, db, llm):
    _o, s, h, _ms = _world(db)
    llm.reply = _actions_block(
        {"kind": "add_task", "title": "x", "milestone": "M1"},
        {
            "kind": "draft_document",
            "doc_kind": "nda",
            "title": "NDA",
            "sections": [{"heading": "Parties", "body": "A and B"}],
        },
    )
    _conv, reply = _ask(client, db, h, "Draft an NDA for a contractor")  # the Legal Assistant
    assert reply["agent"]["key"] == "legal"
    assert [a["kind"] for a in reply["actions"]] == ["draft_document"]


# ---------------------------------------------------------------- confirm / undo / dismiss


def _propose(client, db, h, llm, *items):
    llm.reply = _actions_block(*items)
    conv, reply = _ask(client, db, h)
    return conv, reply, reply["actions"]


def _url(reply, action, op):
    return f"{AI}/messages/{reply['id']}/actions/{action['id']}/{op}"


def test_confirm_add_task_creates_it_and_undo_removes_it(client, db, llm):
    _o, s, h, ms = _world(db)
    _c, reply, acts = _propose(
        client, db, h, llm, {"kind": "add_task", "title": "Call five customers", "milestone": "M1"}
    )
    r = client.post(_url(reply, acts[0], "confirm"), headers=h)
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["status"] == "executed" and body["undoable"] is True and body["href"] == "/roadmap"
    task = db.query(RoadmapTask).filter_by(title="Call five customers").one()
    assert task.milestone_id == ms[0].id

    again = client.post(_url(reply, acts[0], "confirm"), headers=h)  # idempotent
    assert again.status_code == 200
    assert db.query(RoadmapTask).filter_by(title="Call five customers").count() == 1

    undone = client.post(_url(reply, acts[0], "undo"), headers=h)
    assert undone.status_code == 200 and undone.json()["data"]["status"] == "undone"
    db.expire_all()
    assert db.query(RoadmapTask).filter_by(title="Call five customers").count() == 0


def test_undo_refuses_when_the_task_was_changed_since(client, db, llm):
    _o, s, h, _ms = _world(db)
    _c, reply, acts = _propose(
        client, db, h, llm, {"kind": "add_task", "title": "Edited later", "milestone": "M1"}
    )
    client.post(_url(reply, acts[0], "confirm"), headers=h)
    task = db.query(RoadmapTask).filter_by(title="Edited later").one()
    task.status = RoadmapStatus.in_progress
    db.commit()
    r = client.post(_url(reply, acts[0], "undo"), headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ACTION_CHANGED"
    db.expire_all()
    assert db.query(RoadmapTask).filter_by(title="Edited later").count() == 1


def test_update_roadmap_changes_the_milestone_and_undo_restores_it(client, db, llm):
    _o, s, h, ms = _world(db)
    _c, reply, acts = _propose(
        client,
        db,
        h,
        llm,
        {
            "kind": "update_roadmap",
            "milestone": "M1",
            "due_on": "2027-05-05",
            "status": "in_progress",
        },
    )
    r = client.post(_url(reply, acts[0], "confirm"), headers=h)
    assert r.status_code == 200, r.text
    db.expire_all()
    assert ms[0].due_on == dt.date(2027, 5, 5) and ms[0].status == RoadmapStatus.in_progress
    undone = client.post(_url(reply, acts[0], "undo"), headers=h)
    assert undone.status_code == 200
    db.expire_all()
    assert ms[0].due_on == dt.date(2027, 1, 10) and ms[0].status == RoadmapStatus.todo


def test_update_roadmap_undo_refuses_after_a_later_edit(client, db, llm):
    _o, s, h, ms = _world(db)
    _c, reply, acts = _propose(
        client, db, h, llm, {"kind": "update_roadmap", "milestone": "M1", "due_on": "2027-05-05"}
    )
    client.post(_url(reply, acts[0], "confirm"), headers=h)
    ms[0].due_on = dt.date(2027, 9, 9)
    db.commit()
    r = client.post(_url(reply, acts[0], "undo"), headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ACTION_CHANGED"


def test_draft_document_creates_it_and_undo_trashes_it(client, db, llm):
    _o, s, h, _ms = _world(db)
    llm.reply = _actions_block(
        {
            "kind": "draft_document",
            "doc_kind": "nda",
            "title": "Contractor NDA",
            "sections": [{"heading": "Parties", "body": "Acme and the contractor."}],
        }
    )
    conv, reply = _ask(client, db, h, "Draft an NDA for a contractor")
    act = reply["actions"][0]
    r = client.post(_url(reply, act, "confirm"), headers=h)
    assert r.status_code == 200, r.text
    doc = db.query(Document).filter_by(title="Contractor NDA").one()
    assert doc.ai_generated is True and doc.trashed_at is None
    assert doc.sections[-1]["heading"] == "Not legal advice"  # D-DOC-4
    assert r.json()["data"]["href"] == f"/documents/{doc.id}"
    undone = client.post(_url(reply, act, "undo"), headers=h)
    assert undone.status_code == 200
    db.expire_all()
    assert db.get(Document, doc.id).trashed_at is not None  # recoverable, not destroyed


def test_dismiss_then_confirm_is_refused(client, db, llm):
    _o, s, h, _ms = _world(db)
    _c, reply, acts = _propose(
        client, db, h, llm, {"kind": "add_task", "title": "Nope", "milestone": "M1"}
    )
    d = client.post(_url(reply, acts[0], "dismiss"), headers=h)
    assert d.status_code == 200 and d.json()["data"]["status"] == "dismissed"
    c = client.post(_url(reply, acts[0], "confirm"), headers=h)
    assert c.status_code == 409 and c.json()["error"]["code"] == "ACTION_NOT_PROPOSED"
    assert db.query(RoadmapTask).filter_by(title="Nope").count() == 0


def test_confirm_fails_cleanly_when_the_milestone_was_deleted_meanwhile(client, db, llm):
    _o, s, h, ms = _world(db)
    _c, reply, acts = _propose(
        client, db, h, llm, {"kind": "add_task", "title": "Orphan", "milestone": "M1"}
    )
    db.delete(ms[0])
    db.commit()
    r = client.post(_url(reply, acts[0], "confirm"), headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ACTION_TARGET_GONE"
    assert db.query(RoadmapTask).filter_by(title="Orphan").count() == 0


def test_actions_are_private_to_the_conversation_owner_and_closed_to_other_roles(client, db, llm):
    _o, s, h, _ms = _world(db)
    _t, tm = _member(db, s, MembershipRole.team_member)
    _m, mentor = _member(db, s, MembershipRole.mentor)
    _c, reply, acts = _propose(
        client, db, h, llm, {"kind": "add_task", "title": "Private", "milestone": "M1"}
    )
    url = _url(reply, acts[0], "confirm")
    assert client.post(url, headers=tm).status_code == 404  # another member's chat
    assert client.post(url, headers=mentor).status_code == 403
    assert client.post(_url(reply, acts[0], "undo"), headers=mentor).status_code == 403
    assert client.post(_url(reply, acts[0], "dismiss"), headers=mentor).status_code == 403
    assert db.query(RoadmapTask).filter_by(title="Private").count() == 0
    unknown = f"{AI}/messages/{reply['id']}/actions/{uuid.uuid4()}/confirm"
    assert client.post(unknown, headers=h).status_code == 404


def test_executing_publishes_the_event(client, db, llm):
    _o, s, h, _ms = _world(db)
    _c, reply, acts = _propose(
        client, db, h, llm, {"kind": "add_task", "title": "Evented", "milestone": "M1"}
    )
    seen = len(event_bus.published)
    resp = client.post(_url(reply, acts[0], "confirm"), headers=h)
    assert resp.status_code == 200
    names = [e for e, _p in event_bus.published[seen:]]
    assert "ai.action.executed" in names


# ---------------------------------------------------------------- feedback


def test_thumbs_up_down_and_clear(client, db, llm):
    _o, s, h, _ms = _world(db)
    _c, reply = _ask(client, db, h)
    url = f"{AI}/messages/{reply['id']}/feedback"
    up = client.post(url, headers=h, json={"rating": "up"})
    assert up.status_code == 200 and up.json()["data"]["feedback"] == "up"
    down = client.post(url, headers=h, json={"rating": "down", "note": "Too generic"})
    assert down.json()["data"]["feedback"] == "down"
    msg = db.get(AiMessage, uuid.UUID(reply["id"]))
    db.refresh(msg)
    assert msg.feedback_note == "Too generic"
    cleared = client.post(url, headers=h, json={"rating": None})
    assert cleared.json()["data"]["feedback"] is None
    db.refresh(msg)
    assert msg.feedback_note is None


def test_feedback_validation_privacy_and_state(client, db, llm):
    _o, s, h, _ms = _world(db)
    _t, tm = _member(db, s, MembershipRole.team_member)
    _m, mentor = _member(db, s, MembershipRole.mentor)
    _c, reply = _ask(client, db, h)
    url = f"{AI}/messages/{reply['id']}/feedback"
    assert client.post(url, headers=h, json={"rating": "meh"}).status_code == 422
    assert client.post(url, headers=h, json={"rating": "up", "extra": 1}).status_code == 422
    assert client.post(url, headers=h, json={"rating": "up", "note": "x" * 501}).status_code == 422
    assert client.post(url, headers=tm, json={"rating": "up"}).status_code == 404
    assert client.post(url, headers=mentor, json={"rating": "up"}).status_code == 403
    pending = client.post(C, headers=h, json={"message": "second one"}).json()["data"]
    gen_url = f"{AI}/messages/{pending['messages'][1]['id']}/feedback"
    r = client.post(gen_url, headers=h, json={"rating": "up"})  # still generating
    assert r.status_code == 409 and r.json()["error"]["code"] == "FEEDBACK_NOT_ALLOWED"


# ---------------------------------------------------------------- regenerate


def _spent(db, s):
    return (
        db.query(LlmUsageDaily.tokens_used)
        .filter_by(startup_id=s.id, usage_date=today_utc())
        .scalar()
        or 0
    )


def test_regenerate_answers_again_with_the_same_specialist_and_bills_it(client, db, llm):
    _o, s, h, _ms = _world(db)
    conv, reply = _ask(client, db, h, "How long is my runway?")
    assert reply["agent"]["key"] == "finance"
    before = _spent(db, s)
    llm.reply = "A second, different answer."
    r = client.post(f"{C}/{conv['id']}/messages/{reply['id']}/regenerate", headers=h)
    assert r.status_code == 202, r.text
    assert r.json()["data"]["status"] == "generating" and r.json()["data"]["content"] == ""
    _work(db)
    got = client.get(f"{C}/{conv['id']}", headers=h).json()["data"]["messages"][1]
    assert got["content"] == "A second, different answer." and got["agent"]["key"] == "finance"
    assert got["id"] == reply["id"]  # the same turn, replaced in place
    assert _spent(db, s) == before + 10  # one more reply call, no second routing
    assert db.query(AiAgentRoute).filter_by(message_id=uuid.UUID(reply["id"])).count() == 1


def test_regenerate_is_also_the_retry_of_a_failed_or_cancelled_reply(client, db, llm):
    _o, s, h, _ms = _world(db)
    db.commit()
    conv = client.post(C, headers=h, json={"message": "How long is my runway?"}).json()["data"]
    mid = conv["messages"][1]["id"]
    client.post(f"{C}/{conv['id']}/messages/{mid}/cancel", headers=h)
    r = client.post(f"{C}/{conv['id']}/messages/{mid}/regenerate", headers=h)
    assert r.status_code == 202
    _work(db)  # the old queued job (generation 0) is ignored; the new one answers once
    got = client.get(f"{C}/{conv['id']}", headers=h).json()["data"]["messages"][1]
    assert got["status"] == "ready"
    assert llm.calls == 1  # not twice


def test_regenerate_guards(client, db, llm):
    _o, s, h, _ms = _world(db)
    _t, tm = _member(db, s, MembershipRole.team_member)
    conv, reply = _ask(client, db, h, "How long is my runway?")
    url = f"{C}/{conv['id']}/messages/{reply['id']}/regenerate"
    assert client.post(url, headers=tm).status_code == 404
    # not the latest reply
    second = client.post(
        f"{C}/{conv['id']}/messages", headers=h, json={"content": "and next month?"}
    )
    assert second.status_code == 202
    gen = client.post(url, headers=h)
    assert gen.status_code == 409 and gen.json()["error"]["code"] in (
        "NOT_LATEST_REPLY",
        "REPLY_IN_PROGRESS",
    )
    _work(db)
    old = client.post(url, headers=h)
    assert old.status_code == 409 and old.json()["error"]["code"] == "NOT_LATEST_REPLY"
    latest = client.get(f"{C}/{conv['id']}", headers=h).json()["data"]["messages"][-1]
    busy = client.post(f"{C}/{conv['id']}/messages/{latest['id']}/regenerate", headers=h)
    assert busy.status_code == 202
    again = client.post(f"{C}/{conv['id']}/messages/{latest['id']}/regenerate", headers=h)
    assert again.status_code == 409 and again.json()["error"]["code"] == "REPLY_IN_PROGRESS"


def test_regenerate_refuses_after_an_action_was_executed(client, db, llm):
    _o, s, h, _ms = _world(db)
    conv, reply, acts = _propose(
        client, db, h, llm, {"kind": "add_task", "title": "Done already", "milestone": "M1"}
    )
    client.post(_url(reply, acts[0], "confirm"), headers=h)
    r = client.post(f"{C}/{conv['id']}/messages/{reply['id']}/regenerate", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ACTION_EXECUTED"


def test_regenerate_drops_unconfirmed_proposals(client, db, llm):
    _o, s, h, _ms = _world(db)
    conv, reply, acts = _propose(
        client, db, h, llm, {"kind": "add_task", "title": "Stale idea", "milestone": "M1"}
    )
    llm.reply = "No proposals this time."
    r = client.post(f"{C}/{conv['id']}/messages/{reply['id']}/regenerate", headers=h)
    assert r.status_code == 202
    _work(db)
    got = client.get(f"{C}/{conv['id']}", headers=h).json()["data"]["messages"][1]
    assert got["actions"] == [] and db.query(AiAction).count() == 0


def test_regenerate_respects_the_daily_budget(client, db, llm, monkeypatch):
    _o, s, h, _ms = _world(db)
    conv, reply = _ask(client, db, h, "How long is my runway?")
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 4_000)
    row = db.query(LlmUsageDaily).filter_by(startup_id=s.id).one()
    row.tokens_used = 3_000
    db.commit()
    calls = llm.calls
    r = client.post(f"{C}/{conv['id']}/messages/{reply['id']}/regenerate", headers=h)
    assert r.status_code == 202
    _work(db)
    got = client.get(f"{C}/{conv['id']}", headers=h).json()["data"]["messages"][1]
    assert got["status"] == "failed" and llm.calls == calls  # refused before any model call


# ---------------------------------------------------------------- suggestions


def _briefing(db, s):
    db.add(
        DailyBriefing(
            startup_id=s.id,
            briefing_date=dt.date.today(),
            status=BriefingStatus.ready,
            briefing="b",
            risks="r",
            opportunities="o",
            actions=[
                {
                    "label": "Interview two customers",
                    "kind": "navigate",
                    "module": "validation",
                    "accepted_task_id": None,
                },
                {
                    "label": "Already a task",
                    "kind": "task",
                    "module": None,
                    "accepted_task_id": "x",
                },
            ],
            opportunity_items=[{"text": "A pricing page test", "module": "marketing"}],
        )
    )
    db.flush()


def test_suggestions_aggregate_existing_outputs_with_fixed_links(client, db):
    o, s, h, _ms = _world(db)
    _briefing(db, s)
    create_recommendation(
        db, s, key="k1", title="Record your first sale", body="Revenue unlocks runway."
    )
    db.commit()
    r = client.get(f"{AI}/suggestions", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["enabled"] is True
    by_title = {i["title"]: i for i in data["items"]}
    assert set(by_title) == {
        "Interview two customers",
        "A pricing page test",
        "Record your first sale",
    }
    assert by_title["Interview two customers"]["href"] == "/validation"
    assert (
        by_title["A pricing page test"]["href"] == "/marketing"
    )  # its own link, not the last action's
    assert by_title["Record your first sale"]["href"] == "/health"
    assert "Already a task" not in by_title
    again = client.get(f"{AI}/suggestions", headers=h).json()["data"]["items"]
    assert len(again) == 3  # no duplicates on re-read
    assert db.query(AiSuggestion).count() == 3


def test_accept_dismiss_snooze_and_they_stay_gone(client, db):
    o, s, h, _ms = _world(db)
    _briefing(db, s)
    create_recommendation(db, s, key="k1", title="Record your first sale")
    db.commit()
    items = {
        i["title"]: i["id"]
        for i in client.get(f"{AI}/suggestions", headers=h).json()["data"]["items"]
    }
    acc = client.post(f"{AI}/suggestions/{items['Interview two customers']}/accept", headers=h)
    assert acc.status_code == 200 and acc.json()["data"]["status"] == "accepted"
    dis = client.post(f"{AI}/suggestions/{items['A pricing page test']}/dismiss", headers=h)
    assert dis.json()["data"]["status"] == "dismissed"
    snz = client.post(
        f"{AI}/suggestions/{items['Record your first sale']}/snooze", headers=h, json={"days": 3}
    )
    assert snz.json()["data"]["status"] == "snoozed"
    assert client.get(f"{AI}/suggestions", headers=h).json()["data"]["items"] == []
    flip = client.post(f"{AI}/suggestions/{items['A pricing page test']}/accept", headers=h)
    assert flip.status_code == 409 and flip.json()["error"]["code"] == "SUGGESTION_CLOSED"
    # a snoozed card comes back once its time is up
    row = db.get(AiSuggestion, uuid.UUID(items["Record your first sale"]))
    row.snoozed_until = datetime.now(UTC) - dt.timedelta(minutes=1)
    db.commit()
    back = client.get(f"{AI}/suggestions", headers=h).json()["data"]["items"]
    assert [i["title"] for i in back] == ["Record your first sale"]
    bad = client.post(
        f"{AI}/suggestions/{items['Record your first sale']}/snooze", headers=h, json={"days": 99}
    )
    assert bad.status_code == 422


def test_suggestions_are_per_member_and_follow_the_proactive_toggle(client, db):
    o, s, h, _ms = _world(db)
    t, tm = _member(db, s, MembershipRole.team_member)
    _m, mentor = _member(db, s, MembershipRole.mentor)
    _briefing(db, s)
    db.commit()
    mine = client.get(f"{AI}/suggestions", headers=h).json()["data"]["items"]
    cid = mine[0]["id"]
    assert client.post(f"{AI}/suggestions/{cid}/dismiss", headers=tm).status_code == 404
    theirs = client.get(f"{AI}/suggestions", headers=tm).json()["data"]["items"]
    assert len(theirs) == len(mine) and {i["id"] for i in theirs}.isdisjoint(
        {i["id"] for i in mine}
    )
    assert client.get(f"{AI}/suggestions", headers=mentor).status_code == 403
    update_prefs(db, user_id=t.id, startup_id=s.id, changes={"proactive_suggestions": False})
    db.commit()
    off = client.get(f"{AI}/suggestions", headers=tm).json()["data"]
    assert off == {"enabled": False, "items": []}


def test_a_new_batch_publishes_the_event_and_notifies_once(client, db):
    from app.db.models.notification import Notification
    from app.services.notifications.registry import register

    register()
    o, s, h, _ms = _world(db)
    _briefing(db, s)
    create_recommendation(db, s, key="k1", title="Record your first sale")
    db.commit()
    seen = len(event_bus.published)
    resp = client.get(f"{AI}/suggestions", headers=h)
    assert resp.status_code == 200
    events = [(e, p) for e, p in event_bus.published[seen:] if e == "ai.suggestion.created"]
    assert len(events) == 3 and [p["notify"] for _e, p in events] == [True, False, False]
    notes = db.query(Notification).filter_by(user_id=o.id, type="ai.suggestion.created").all()
    assert len(notes) == 1 and notes[0].title == "Your AI Co-Founder has a suggestion for you"
    client.get(f"{AI}/suggestions", headers=h)  # nothing new: no second notification
    assert db.query(Notification).filter_by(user_id=o.id, type="ai.suggestion.created").count() == 1


# ---------------------------------------------------------------- bench and pinning


def test_the_bench_lists_four_live_agents_and_the_rest_as_coming_soon(client, db):
    _o, s, h, _ms = _world(db, with_roadmap=False)
    _m, mentor = _member(db, s, MembershipRole.mentor)
    db.commit()
    r = client.get(f"{AI}/agents", headers=h)
    assert r.status_code == 200, r.text
    agents = r.json()["data"]
    assert len(agents) == 11
    live = [a["key"] for a in agents if a["available"]]
    assert live == ["strategist", "finance", "legal", "mentor"]
    soon = [a for a in agents if not a["available"]]
    assert len(soon) == 7 and {a["status"] for a in soon} == {"coming_soon"}
    assert client.get(f"{AI}/agents", headers=mentor).status_code == 403


def test_a_conversation_can_be_pinned_to_an_agent(client, db, llm):
    _o, s, h, _ms = _world(db, with_roadmap=False)
    conv, reply = _ask(client, db, h, "hello there", agent_key="legal")
    assert conv["pinned_agent_key"] == "legal" and reply["agent"]["key"] == "legal"
    assert conv["topic"] == "legal"
    route = db.query(AiAgentRoute).filter_by(message_id=uuid.UUID(reply["id"])).one()
    assert route.method == "pinned" and route.confidence == 1.0
    assert "you asked for this specialist" in reply["reasoning_summary"]
    # a keyword-heavy follow-up still goes to the pinned agent
    fu = client.post(
        f"{C}/{conv['id']}/messages", headers=h, json={"content": "What is my runway?"}
    )
    assert fu.status_code == 202
    _work(db)
    last = client.get(f"{C}/{conv['id']}", headers=h).json()["data"]["messages"][-1]
    assert last["agent"]["key"] == "legal"
    bad = client.post(C, headers=h, json={"message": "hi", "agent_key": "marketing"})
    assert bad.status_code == 422
    assert db.query(AiConversation).filter_by(pinned_agent_key="marketing").count() == 0
    assert ChatMessageRole.assistant and ChatMessageStatus.ready


def test_undo_refuses_after_any_edit_to_the_created_task(client, db, llm):
    _o, s, h, _ms = _world(db)
    _c, reply, acts = _propose(
        client, db, h, llm, {"kind": "add_task", "title": "Edit me", "milestone": "M1"}
    )
    client.post(_url(reply, acts[0], "confirm"), headers=h)
    task = db.query(RoadmapTask).filter_by(title="Edit me").one()
    task.description = "I wrote details here"
    db.commit()
    r = client.post(_url(reply, acts[0], "undo"), headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ACTION_CHANGED"
    db.expire_all()
    assert db.query(RoadmapTask).filter_by(title="Edit me").count() == 1


def test_a_done_status_is_not_a_valid_roadmap_proposal(client, db, llm):
    _o, s, h, _ms = _world(db)
    _c, reply, acts = _propose(
        client,
        db,
        h,
        llm,
        {"kind": "update_roadmap", "milestone": "M1", "status": "done"},
        {"kind": "update_roadmap", "milestone": "M1", "status": "in_progress"},
    )
    assert [a["params"].get("status") for a in acts] == ["in_progress"]


def test_actions_on_a_moderation_removed_message_cannot_be_run(client, db, llm):
    _o, s, h, _ms = _world(db)
    conv, reply, acts = _propose(
        client, db, h, llm, {"kind": "add_task", "title": "Hidden", "milestone": "M1"}
    )
    msg = db.get(AiMessage, uuid.UUID(reply["id"]))
    msg.removed_by_moderation_at = datetime.now(UTC)
    db.commit()
    r = client.post(_url(reply, acts[0], "confirm"), headers=h)
    assert r.status_code == 404
    regen = client.post(f"{C}/{conv['id']}/messages/{reply['id']}/regenerate", headers=h)
    assert regen.status_code == 404
    assert db.query(RoadmapTask).filter_by(title="Hidden").count() == 0


def test_regenerate_only_drops_proposals_and_still_sees_an_executed_one(client, db, llm):
    _o, s, h, _ms = _world(db)
    conv, reply, acts = _propose(
        client,
        db,
        h,
        llm,
        {"kind": "add_task", "title": "First", "milestone": "M1"},
        {"kind": "add_task", "title": "Second", "milestone": "M1"},
    )
    client.post(_url(reply, acts[0], "confirm"), headers=h)
    r = client.post(f"{C}/{conv['id']}/messages/{reply['id']}/regenerate", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ACTION_EXECUTED"
    db.expire_all()
    # the refused request changed nothing: both action rows are still there
    assert db.query(AiAction).filter_by(message_id=uuid.UUID(reply["id"])).count() == 2
