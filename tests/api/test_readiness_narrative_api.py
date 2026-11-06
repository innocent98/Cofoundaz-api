from datetime import UTC, datetime

import pytest

from app.core.security import create_access_token
from app.db.models.enums import READINESS_READ_ROLES, READINESS_WRITE_ROLES, MembershipRole
from app.db.models.job import Job
from app.db.models.readiness import NarrativeCheck
from app.platform import llm_budget
from app.services.readiness import narrative
from tests.factories import create_membership, create_startup, create_user
from tests.narrative_helpers import DRAFT, ClaimsLLM, ready_review

BASE = "/api/v1/readiness/narrative"


def _member(db, startup, role):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, u, startup, role=role)
    db.flush()
    return u, {
        "Authorization": f"Bearer {create_access_token(str(u.id))}",
        "X-Workspace-Id": str(startup.id),
    }


@pytest.fixture()
def team(db, monkeypatch):
    llm = ClaimsLLM()
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: llm)
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=owner)
    db.flush()
    members = {role: _member(db, s, role) for role in MembershipRole}
    db.commit()
    return s, {r: h for r, (_u, h) in members.items()}, members, llm


def _drain(db):
    from app.worker import runner
    from app.worker.handlers import readiness as handlers

    runner.register_handler(narrative.JOB_TYPE, handlers.handle_narrative_check)
    for _ in range(20):
        if runner.run_once(db) == 0:
            break


@pytest.mark.parametrize("role", list(MembershipRole))
@pytest.mark.parametrize("path", ["", "/checks/latest"])
def test_reads_by_role(client, team, role, path):
    _s, heads, _m, _llm = team
    r = client.get(BASE + path, headers=heads[role])
    assert r.status_code == (200 if role in READINESS_READ_ROLES else 403), r.text


@pytest.mark.parametrize("role", list(MembershipRole))
def test_writes_by_role(client, db, team, role):
    _s, heads, _m, _llm = team
    put = client.put(BASE, headers=heads[role], json={"body": DRAFT})
    check = client.post(f"{BASE}/check", headers=heads[role])
    if role in READINESS_WRITE_ROLES:
        assert put.status_code == 200, put.text
        assert check.status_code == 202, check.text
    else:
        assert put.status_code == 403, put.text
        assert check.status_code == 403, check.text
        assert db.query(NarrativeCheck).count() == 0


def test_full_flow(client, db, team):
    s, heads, members, llm = team
    fh = heads[MembershipRole.founder]
    th = heads[MembershipRole.team_member]

    empty = client.get(BASE, headers=fh).json()["data"]
    assert empty["body"] == "" and empty["can_check"] is False and empty["max_chars"] == 20000
    none = client.post(f"{BASE}/check", headers=fh)
    assert none.status_code == 422 and none.json()["error"]["code"] == "NOTHING_TO_CHECK"
    assert client.get(f"{BASE}/checks/latest", headers=fh).json()["data"] == {"check": None}

    ready_review(db, s, members[MembershipRole.founder][0])
    db.commit()
    saved = client.put(BASE, headers=fh, json={"body": DRAFT})
    assert saved.status_code == 200
    assert saved.json()["data"]["body"] == DRAFT and saved.json()["data"]["can_check"] is True
    assert client.get(BASE, headers=th).json()["data"]["body"] == DRAFT  # team member reads

    queued = client.post(f"{BASE}/check", headers=fh)
    assert queued.status_code == 202, queued.text
    body = queued.json()["data"]
    assert body["outcome"] == "queued" and body["check"]["status"] == "queued"
    job_id = body["check"]["job_id"]
    assert client.get(f"/api/v1/jobs/{job_id}", headers=fh).json()["data"]["status"] == "queued"
    again = client.post(f"{BASE}/check", headers=fh)
    assert again.status_code == 200 and again.json()["data"]["outcome"] == "in_progress"

    _drain(db)
    db.expire_all()
    latest = client.get(f"{BASE}/checks/latest", headers=th)
    assert latest.status_code == 200
    c = latest.json()["data"]["check"]
    assert c["status"] == "ready" and c["stale"] is False and c["tokens_used"] == 6100
    assert c["counts"]["high"] >= 1 and c["findings"][0]["severity"] == "high"
    assert {"claims", "facts", "use_of_funds", "findings"} <= set(c)

    reused = client.post(f"{BASE}/check", headers=fh)
    assert reused.status_code == 200 and reused.json()["data"]["outcome"] == "reused"
    assert reused.json()["data"]["check"]["id"] == c["id"]  # nothing changed: the same check
    assert len(llm.calls) == 1

    client.put(BASE, headers=fh, json={"body": DRAFT + " Edited."})
    stale = client.get(f"{BASE}/checks/latest", headers=fh).json()["data"]["check"]
    assert stale["stale"] is True

    score = client.get("/api/v1/readiness/score", headers=fh).json()["data"]
    sigs = {sig["key"]: sig for d in score["dimensions"] for sig in d["signals"]}
    assert sigs["story.narrative"]["href"] == "/investor-readiness/narrative"
    assert sigs["financials.use_of_funds"]["score"] == 100
    hist = client.get("/api/v1/readiness/history?range=30", headers=fh).json()["data"]
    assert any(e["type"] == "narrative_check" for e in hist["log"])


def test_validation_and_tenancy(client, db, team):
    s, heads, _m, _llm = team
    fh = heads[MembershipRole.founder]
    assert client.put(BASE, headers=fh, json={}).status_code == 422
    assert client.put(BASE, headers=fh, json={"body": "x", "extra": 1}).status_code == 422
    assert client.put(BASE, headers=fh, json={"body": "x" * 20001}).status_code == 422
    assert client.put(BASE, headers=fh, json={"body": "x" * 20000}).status_code == 200
    assert client.put(BASE, headers=fh, json={"body": ""}).status_code == 200

    other_owner = create_user(db, email_verified_at=datetime.now(UTC))
    other = create_startup(db, owner=other_owner)
    create_membership(db, other_owner, other, role=MembershipRole.founder)
    db.commit()
    oh = {
        "Authorization": f"Bearer {create_access_token(str(other_owner.id))}",
        "X-Workspace-Id": str(other.id),
    }
    client.put(BASE, headers=oh, json={"body": "Their secret plan with 9m NGN MRR."})
    assert client.get(BASE, headers=fh).json()["data"]["body"] == ""
    foreign = {**fh, "X-Workspace-Id": str(other.id)}
    assert client.get(BASE, headers=foreign).status_code in (403, 404)


def test_budget_exhausted_is_a_clean_429(client, db, team, monkeypatch):
    from app.core.config import settings

    s, heads, _m, _llm = team
    fh = heads[MembershipRole.founder]
    client.put(BASE, headers=fh, json={"body": DRAFT})
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 5)
    llm_budget.debit(db, s.id, 5)
    db.commit()
    r = client.post(f"{BASE}/check", headers=fh)
    assert r.status_code == 429 and r.json()["error"]["code"] == "AI_BUDGET_EXHAUSTED"
    assert db.query(Job).filter_by(type=narrative.JOB_TYPE).count() == 0
