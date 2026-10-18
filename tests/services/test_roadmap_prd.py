"""Roadmap PRD completion: AI breakdown, team members, comments thread, due-soon notifications."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import AppError, Forbidden, NotFound
from app.core.security import create_access_token
from app.db.models.comment import Comment
from app.db.models.enums import JobStatus, MembershipRole, RoadmapStatus
from app.db.models.job import Job
from app.db.models.notification import Notification
from app.db.models.roadmap import RoadmapPhase, RoadmapTask
from app.platform import llm_budget
from app.platform.events import DispatchingEventBus, event_bus
from app.services.notifications.registry import SPECS, register
from app.services.roadmap import breakdown as bd
from app.services.team import service as team
from app.worker.handlers.ai import handle_roadmap_breakdown
from tests.factories import (
    create_membership,
    create_milestone,
    create_phase,
    create_roadmap,
    create_startup,
    create_task,
    create_user,
)


@pytest.fixture
def ws(db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    r = create_roadmap(db, startup=s)
    m = create_milestone(db, phase=create_phase(db, roadmap=r), title="Validate demand")
    create_task(db, milestone=m, title="Interview users")
    db.flush()
    return u, s, m


def _h(u, s):
    return {
        "Authorization": f"Bearer {create_access_token(str(u.id))}",
        "X-Workspace-Id": str(s.id),
    }


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload

    def complete_json(self, messages, *, schema, max_tokens):
        return self.payload


# --- AI breakdown -------------------------------------------------------------------------


def test_start_breakdown_enqueues_once(db, ws):
    _u, s, m = ws
    job = bd.start_breakdown(db, s, m.id)
    assert job.type == "ai.roadmap.breakdown" and job.payload["milestone_id"] == str(m.id)
    with pytest.raises(AppError) as exc:
        bd.start_breakdown(db, s, m.id)
    assert (exc.value.code, exc.value.http_status) == ("BREAKDOWN_IN_PROGRESS", 409)
    job.status = JobStatus.succeeded
    db.flush()
    assert bd.start_breakdown(db, s, m.id).id != job.id  # a finished job doesn't block


def test_breakdown_is_tenant_scoped(db, ws):
    _u, _s, m = ws
    other = create_startup(db, owner=create_user(db))
    with pytest.raises(NotFound):
        bd.start_breakdown(db, other, m.id)


def test_handler_appends_validated_tasks(db, ws, monkeypatch):
    from app.core.config import settings

    _u, s, m = ws
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    rows = [
        {"title": "Interview users", "effort": "small"},  # duplicate of existing -> skipped
        {"title": "Write the screener", "effort": "small"},
        {"title": "Book 5 calls", "effort": "bogus"},  # bad effort -> medium
        {"title": "  ", "effort": "large"},  # blank -> skipped
        {"title": "Synthesize notes", "effort": "large"},
    ]
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: _FakeLLM({"tasks": rows}))
    job = bd.start_breakdown(db, s, m.id)
    handle_roadmap_breakdown(db, job)
    titles = [
        t.title
        for t in db.query(RoadmapTask).filter_by(milestone_id=m.id).order_by(RoadmapTask.order)
    ]
    assert titles == ["Interview users", "Write the screener", "Book 5 calls", "Synthesize notes"]
    assert job.result["status"] == "ready" and len(job.result["created_task_ids"]) == 3
    booked = db.query(RoadmapTask).filter_by(title="Book 5 calls").one()
    assert booked.effort.value == "medium"


def test_handler_over_budget_and_gone(db, ws, monkeypatch):
    _u, s, m = ws
    monkeypatch.setattr(llm_budget, "over_budget", lambda *_a, **_k: True)
    job = bd.start_breakdown(db, s, m.id)
    handle_roadmap_breakdown(db, job)
    assert job.result["status"] == "over_budget"
    gone = Job(
        type="ai.roadmap.breakdown",
        payload={"startup_id": str(s.id), "milestone_id": str(uuid.uuid4())},
    )
    handle_roadmap_breakdown(db, gone)
    assert gone.result == {"status": "gone"}


def test_breakdown_endpoint_roles(client, db, ws):
    u, s, m = ws
    mentor = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, mentor, s, role=MembershipRole.mentor)
    db.commit()
    url = f"/api/v1/roadmap/milestones/{m.id}/breakdown"
    assert client.post(url, headers=_h(mentor, s)).status_code == 403
    r = client.post(url, headers=_h(u, s))
    assert r.status_code == 202, r.text
    assert r.json()["data"]["status"] == "queued"
    assert client.post(url, headers=_h(u, s)).status_code == 409


# --- members --------------------------------------------------------------------------------


def test_members_list(client, db, ws):
    u, s, _m = ws
    mate = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, mate, s, role=MembershipRole.team_member)
    db.commit()
    r = client.get("/api/v1/team/members", headers=_h(mate, s))
    assert r.status_code == 200, r.text
    roles = {row["user_id"]: row["role"] for row in r.json()["data"]}
    assert roles == {str(u.id): "founder", str(mate.id): "team_member"}


# --- comments ---------------------------------------------------------------------------------


def test_comment_thread_and_mention_notification(db, ws):
    u, s, m = ws
    mate = create_user(db)
    create_membership(db, mate, s, role=MembershipRole.team_member)
    db.flush()
    bus = DispatchingEventBus()
    register(bus)
    orig = team.event_bus
    team.event_bus = bus
    try:
        c = team.create_comment(
            db,
            s,
            u,
            MembershipRole.founder,
            entity_type="roadmap_milestone",
            entity_id=m.id,
            body="Can you take this?",
            mention_user_ids=[mate.id, mate.id],
        )
    finally:
        team.event_bus = orig
    assert c["mention_user_ids"] == [str(mate.id)]
    notes = db.query(Notification).filter_by(user_id=mate.id, type="team.comment.mentioned").all()
    assert len(notes) == 1 and notes[0].title.startswith("You were mentioned by")
    assert (
        db.query(Notification).filter_by(user_id=u.id, type="team.comment.mentioned").count() == 0
    )
    thread = team.list_comments(db, s, mate, "roadmap_milestone", m.id)
    assert [x["body"] for x in thread] == ["Can you take this?"]
    assert thread[0]["is_mine"] is False


def test_comment_validation(db, ws):
    u, s, m = ws
    other = create_startup(db, owner=create_user(db))
    r2 = create_roadmap(db, startup=other)
    foreign = create_milestone(db, phase=create_phase(db, roadmap=r2))
    with pytest.raises(NotFound):
        team.create_comment(
            db,
            s,
            u,
            MembershipRole.founder,
            entity_type="roadmap_milestone",
            entity_id=foreign.id,
            body="x",
            mention_user_ids=[],
        )
    with pytest.raises(AppError):
        team.create_comment(
            db,
            s,
            u,
            MembershipRole.founder,
            entity_type="roadmap_milestone",
            entity_id=m.id,
            body="x",
            mention_user_ids=[create_user(db).id],  # not a member
        )
    with pytest.raises(Forbidden):
        team.create_comment(
            db,
            s,
            u,
            MembershipRole.investor,
            entity_type="roadmap_milestone",
            entity_id=m.id,
            body="x",
            mention_user_ids=[],
        )


def test_comment_edit_delete_resolve_permissions(db, ws):
    u, s, m = ws
    mentor = create_user(db)
    create_membership(db, mentor, s, role=MembershipRole.mentor)
    db.flush()
    c = team.create_comment(
        db,
        s,
        mentor,
        MembershipRole.mentor,
        entity_type="roadmap_milestone",
        entity_id=m.id,
        body="draft",
        mention_user_ids=[],
    )
    cid = uuid.UUID(c["id"])
    with pytest.raises(Forbidden):
        team.edit_comment(db, s, u, cid, "hijack")  # not the author
    edited = team.edit_comment(db, s, mentor, cid, "final")
    assert edited["body"] == "final" and edited["edited_at"]
    assert team.set_resolved(db, s, u, MembershipRole.founder, cid, True)["resolved_at"]
    outsider = create_user(db)
    create_membership(db, outsider, s, role=MembershipRole.legal_advisor)
    with pytest.raises(Forbidden):
        team.set_resolved(db, s, outsider, MembershipRole.legal_advisor, cid, False)
    with pytest.raises(Forbidden):
        team.delete_comment(db, s, u, cid)
    team.delete_comment(db, s, mentor, cid)
    assert db.query(Comment).count() == 0


def test_comment_endpoints(client, db, ws):
    u, s, m = ws
    db.commit()
    task = db.query(RoadmapTask).filter_by(milestone_id=m.id).first()
    body = {"entity_type": "roadmap_task", "entity_id": str(task.id), "body": "On it"}
    r = client.post("/api/v1/team/comments", json=body, headers=_h(u, s))
    assert r.status_code == 201, r.text
    cid = r.json()["data"]["id"]
    q = {"entity_type": "roadmap_task", "entity_id": str(task.id)}
    assert len(client.get("/api/v1/team/comments", params=q, headers=_h(u, s)).json()["data"]) == 1
    bad = client.post(
        "/api/v1/team/comments", json={**body, "entity_type": "invoice"}, headers=_h(u, s)
    )
    assert bad.status_code == 422
    res = client.post(f"/api/v1/team/comments/{cid}/resolve", json={}, headers=_h(u, s))
    assert res.status_code == 200 and res.json()["data"]["resolved_at"]
    assert (
        client.patch(
            f"/api/v1/team/comments/{cid}", json={"body": "Done"}, headers=_h(u, s)
        ).json()["data"]["body"]
        == "Done"
    )
    assert client.delete(f"/api/v1/team/comments/{cid}", headers=_h(u, s)).status_code == 204


# --- notifications --------------------------------------------------------------------------


def test_due_soon_scheduling_and_copy(db, ws, monkeypatch):
    from app.core.config import settings
    from app.worker import scheduler
    from app.worker.handlers.scheduled import handle_roadmap_due_soon

    _u, s, m = ws
    monkeypatch.setattr(settings, "SCHEDULER_TIMEZONE", "UTC")
    now = datetime.now(UTC)
    m.due_on = now.date() + timedelta(days=1)
    phase = db.get(RoadmapPhase, m.phase_id)
    far = create_milestone(db, phase=phase)
    far.due_on = now.date() + timedelta(days=10)
    done = create_milestone(db, phase=phase)
    done.due_on, done.status = now.date(), RoadmapStatus.done
    db.flush()
    due = {d.scope_key for d in scheduler._due_milestones_soon(db, now)}
    assert str(m.id) in due and str(far.id) not in due and str(done.id) not in due

    before = len(event_bus.published)
    handle_roadmap_due_soon(
        db,
        Job(
            type="scheduled.roadmap.due_soon",
            payload={"startup_id": str(s.id), "milestone_id": str(m.id)},
        ),
    )
    ((event, payload),) = [
        e for e in event_bus.published[before:] if e[0] == "roadmap.milestone.due_soon"
    ]
    assert payload["relative"] == "tomorrow"
    assert SPECS["roadmap.milestone.due_soon"].title(payload) == (
        "Milestone due soon: Validate demand, tomorrow"
    )
    assert SPECS["roadmap.milestone.overdue"].title({}) == (
        "Milestone overdue, I've drafted a re-plan for your review"
    )
    assert SPECS["roadmap.milestone.overdue"].body({}) == "Review re-plan"
