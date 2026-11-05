from datetime import UTC, datetime

import pytest

from app.core.security import create_access_token
from app.db.models.enums import READINESS_READ_ROLES, READINESS_WRITE_ROLES, MembershipRole
from app.db.models.job import Job
from app.platform import llm_budget
from app.services.readiness import qa
from tests.factories import create_membership, create_startup, create_user
from tests.qa_helpers import ScriptedLLM

BASE = "/api/v1/readiness/qa-sessions"
SECRET = "our secret runway number is 4.2 months"


def _member(db, startup, role):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, u, startup, role=role)
    db.flush()
    return {
        "Authorization": f"Bearer {create_access_token(str(u.id))}",
        "X-Workspace-Id": str(startup.id),
    }


@pytest.fixture()
def team(db, monkeypatch):
    fake = ScriptedLLM()
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: fake)
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=owner)
    db.flush()
    heads = {role: _member(db, s, role) for role in MembershipRole}
    db.commit()
    return s, heads


def _drain(db):
    from app.worker import runner
    from app.worker.handlers import readiness as handlers

    runner.register_handler(qa.JOB_TYPE, handlers.handle_qa_scoring)
    for _ in range(20):
        if runner.run_once(db) == 0:
            break


def _start(client, h, **kw):
    body = {"focus": "financials", "intensity": "skeptical_vc", **kw}
    return client.post(BASE, headers=h, json=body)


@pytest.mark.parametrize("role", list(MembershipRole))
def test_start_by_role(client, team, role):
    _s, heads = team
    r = _start(client, heads[role])
    assert r.status_code == (201 if role in READINESS_WRITE_ROLES else 403), r.text


@pytest.mark.parametrize("role", list(MembershipRole))
def test_answer_and_end_by_role(client, team, role):
    _s, heads = team
    sid = _start(client, heads[MembershipRole.founder]).json()["data"]["session"]["id"]
    m = client.post(f"{BASE}/{sid}/messages", headers=heads[role], json={"content": "hello"})
    e = client.post(f"{BASE}/{sid}/end", headers=heads[role])
    if role in READINESS_WRITE_ROLES:
        assert m.status_code == 200 and e.status_code == 200, (m.text, e.text)
    else:
        assert m.status_code == 403 and e.status_code == 403


@pytest.mark.parametrize("role", list(MembershipRole))
def test_reads_by_role(client, team, role):
    _s, heads = team
    sid = _start(client, heads[MembershipRole.founder]).json()["data"]["session"]["id"]
    lst = client.get(BASE, headers=heads[role])
    one = client.get(f"{BASE}/{sid}", headers=heads[role])
    if role in READINESS_READ_ROLES:
        assert lst.status_code == 200 and one.status_code == 200, (lst.text, one.text)
    else:
        assert lst.status_code == 403 and one.status_code == 403


def test_a_team_member_never_sees_what_the_founder_said(client, db, team):
    s, heads = team
    f, tm = heads[MembershipRole.founder], heads[MembershipRole.team_member]
    sid = _start(client, f).json()["data"]["session"]["id"]
    for _ in range(3):
        client.post(f"{BASE}/{sid}/messages", headers=f, json={"content": SECRET})
    client.post(f"{BASE}/{sid}/end", headers=f)
    _drain(db)
    db.expire_all()
    founder_view = client.get(f"{BASE}/{sid}", headers=f).json()["data"]
    assert SECRET in repr(founder_view["transcript"]) and founder_view["scorecard"]["answers"]
    for r in (client.get(f"{BASE}/{sid}", headers=tm), client.get(BASE, headers=tm)):
        assert r.status_code == 200 and SECRET not in r.text
        assert "Investor question" not in r.text and "transcript" not in r.text
        assert "scorecard" not in r.text
    seen = client.get(f"{BASE}/{sid}", headers=tm).json()["data"]
    assert seen["rating"] == "good" and seen["turns"] == 3 and seen["rating_label"] == "Good"


def test_the_flow_start_answer_cap_score_and_list(client, db, team):
    s, heads = team
    f = heads[MembershipRole.founder]
    start = _start(client, f)
    assert start.status_code == 201, start.text
    body = start.json()["data"]
    assert body["reply"] == "Investor question 1?" and body["session"]["status"] == "active"
    sid = body["session"]["id"]
    clash = _start(client, f)
    assert clash.status_code == 409 and clash.json()["error"]["code"] == "SESSION_ACTIVE"
    for n in range(1, 10):
        r = client.post(f"{BASE}/{sid}/messages", headers=f, json={"content": f"answer {n}"})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["reply"] == f"Investor question {n + 1}?"
    last = client.post(f"{BASE}/{sid}/messages", headers=f, json={"content": "answer 10"})
    d = last.json()["data"]
    assert d["reply"] is None and d["ended"] is True and d["session"]["status"] == "ending"
    after = client.post(f"{BASE}/{sid}/messages", headers=f, json={"content": "11"})
    assert after.status_code == 409 and after.json()["error"]["code"] == "SESSION_NOT_ACTIVE"
    _drain(db)
    db.expire_all()
    done = client.get(f"{BASE}/{sid}", headers=f).json()["data"]
    # The scripted scorer rates only the first three answers; the unrated seven default to 1, so the
    # weakest is decided from the ratings (first of them), and the model answer, written for #2, is
    # not attached to it.
    assert done["status"] == "scored" and done["scorecard"]["weakest"]["i"] == 4
    assert done["scorecard"]["weakest"]["model_answer"] is None
    assert len(done["scorecard"]["answers"]) == 10
    listed = client.get(BASE, headers=f).json()["data"]["sessions"]
    assert [x["id"] for x in listed] == [sid] and "transcript" not in listed[0]
    score = client.get("/api/v1/readiness/score", headers=f).json()["data"]
    story = next(d for d in score["dimensions"] if d["key"] == "story")
    assert any(sg["key"] == "story.mock_qa" and sg["value"] > 0 for sg in story["signals"])
    hist = client.get("/api/v1/readiness/history?range=30", headers=f).json()["data"]
    assert any(e["type"] == "qa_session" for e in hist["log"])


def test_ending_early_and_with_no_answers(client, db, team):
    s, heads = team
    f = heads[MembershipRole.founder]
    sid = _start(client, f).json()["data"]["session"]["id"]
    empty = client.post(f"{BASE}/{sid}/end", headers=f)
    assert empty.status_code == 200 and empty.json()["data"]["session"]["status"] == "abandoned"
    assert db.query(Job).filter_by(type=qa.JOB_TYPE).count() == 0
    sid2 = _start(client, f).json()["data"]["session"]["id"]
    client.post(f"{BASE}/{sid2}/messages", headers=f, json={"content": "one answer"})
    ended = client.post(f"{BASE}/{sid2}/end", headers=f)
    assert ended.json()["data"]["session"]["status"] == "ending"
    again = client.post(f"{BASE}/{sid2}/end", headers=f)
    assert again.status_code == 200 and again.json()["data"]["session"]["status"] == "ending"
    assert db.query(Job).filter_by(type=qa.JOB_TYPE).count() == 1


def test_validation_and_tenancy(client, db, team):
    s, heads = team
    f = heads[MembershipRole.founder]
    assert _start(client, f, focus="everything").status_code == 422
    assert _start(client, f, intensity="gentle").status_code == 422
    assert client.post(BASE, headers=f, json={"focus": "market"}).status_code == 422
    assert (
        client.post(
            BASE, headers=f, json={"focus": "market", "intensity": "skeptical_vc", "x": 1}
        ).status_code
        == 422
    )
    sid = _start(client, f).json()["data"]["session"]["id"]
    assert client.post(f"{BASE}/{sid}/messages", headers=f, json={"content": ""}).status_code == 422
    too_long = client.post(f"{BASE}/{sid}/messages", headers=f, json={"content": "x" * 2001})
    assert too_long.status_code == 422
    blank = client.post(f"{BASE}/{sid}/messages", headers=f, json={"content": "   "})
    assert blank.status_code == 422
    other = create_startup(db, owner=create_user(db, email_verified_at=datetime.now(UTC)))
    oh = _member(db, other, MembershipRole.founder)
    db.commit()
    for r in (
        client.get(f"{BASE}/{sid}", headers=oh),
        client.post(f"{BASE}/{sid}/messages", headers=oh, json={"content": "hi"}),
        client.post(f"{BASE}/{sid}/end", headers=oh),
    ):
        assert r.status_code == 404
    assert client.get(f"{BASE}/00000000-0000-0000-0000-000000000000", headers=f).status_code == 404


def test_budget_gates(client, db, team, monkeypatch):
    from app.core.config import settings

    s, heads = team
    f = heads[MembershipRole.founder]
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 100_000)
    llm_budget.debit(db, s.id, 70_000)
    db.commit()
    low = _start(client, f)
    assert low.status_code == 409 and low.json()["error"]["code"] == "AI_BUDGET_LOW"
    assert db.query(Job).count() == 0
