from datetime import UTC, datetime

from app.core.config import settings
from app.core.security import create_access_token
from app.db.models.enums import JobStatus, MembershipRole
from app.db.models.job import Job
from app.platform import llm_budget
from tests.factories import create_membership, create_startup, create_user


def _founder(db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return u, s, h


def test_ai_status_shape_and_over_budget(client, db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 1000)
    _u, s, h = _founder(db)
    llm_budget.debit(db, s.id, 400)
    db.add(Job(type="ai.dashboard.briefing", payload={}, startup_id=s.id, status=JobStatus.failed))
    db.add(Job(type="email.notification", payload={}, startup_id=s.id, status=JobStatus.failed))
    db.commit()

    r = client.get("/api/v1/ai/status", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()["data"]

    assert data["tokens_used_today"] == 400
    assert data["daily_budget"] == 1000
    assert data["over_budget"] is False
    assert "resets_at" in data and data["resets_at"]

    types = [f["type"] for f in data["recent_enrichment_failures"]]
    assert "ai.dashboard.briefing" in types
    assert "email.notification" not in types
    for f in data["recent_enrichment_failures"]:
        assert set(f) == {"type", "failed_at"}


def test_ai_status_no_usage_defaults_to_zero(client, db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 1000)
    _u, _s, h = _founder(db)
    db.commit()

    r = client.get("/api/v1/ai/status", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["tokens_used_today"] == 0
    assert data["recent_enrichment_failures"] == []


def test_ai_status_over_budget_true(client, db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 100)
    _u, s, h = _founder(db)
    llm_budget.debit(db, s.id, 100)
    db.commit()

    r = client.get("/api/v1/ai/status", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["over_budget"] is True


def test_ai_status_unlimited_budget_is_null(client, db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 0)
    _u, _s, h = _founder(db)
    db.commit()

    r = client.get("/api/v1/ai/status", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["daily_budget"] is None
    assert data["over_budget"] is False


def test_ai_status_excludes_other_startups_failures(client, db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 1000)
    _u, s, h = _founder(db)
    other_u = create_user(db, email_verified_at=datetime.now(UTC))
    other_s = create_startup(db, owner=other_u)
    db.add(
        Job(
            type="ai.dashboard.briefing",
            payload={},
            startup_id=other_s.id,
            status=JobStatus.failed,
        )
    )
    db.commit()

    r = client.get("/api/v1/ai/status", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["recent_enrichment_failures"] == []


def test_ai_status_requires_membership(client, db):
    _u, s, _h = _founder(db)
    outsider = create_user(db, email_verified_at=datetime.now(UTC))
    db.commit()
    headers = {
        "Authorization": f"Bearer {create_access_token(str(outsider.id))}",
        "X-Workspace-Id": str(s.id),
    }

    r = client.get("/api/v1/ai/status", headers=headers)
    assert r.status_code == 403
