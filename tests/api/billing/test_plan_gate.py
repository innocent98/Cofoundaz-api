"""The plan gate: dark by default, 402 PLAN_REQUIRED when enforced, applied to Sales outreach (D-SAL-11)."""

import uuid
from datetime import timedelta

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.models.billing import BillingSubscription
from app.db.models.enums import MembershipRole
from app.db.session import get_db
from app.services.billing import gate
from app.services.billing import subscription as subs
from tests.billing_helpers import workspace

SEQ = "/api/v1/sales/sequences/{}/enroll"
FOLLOW = "/api/v1/sales/deals/{}/follow-up/send"


def _enroll(client, h):
    return client.post(SEQ.format(uuid.uuid4()), json={}, headers=h)


def _follow(client, h):
    return client.post(FOLLOW.format(uuid.uuid4()), json={"draft_id": str(uuid.uuid4())}, headers=h)


def test_enforcement_off_lets_everything_through(client, db):
    _, _, h = workspace(db)
    assert _enroll(client, h).status_code != 402
    assert _follow(client, h).status_code != 402


def test_enforcement_on_blocks_starter_with_the_plan_needed(client, db, monkeypatch):
    _, startup, h = workspace(db)
    monkeypatch.setattr(settings, "PLAN_ENFORCEMENT", True)
    sub = subs.get_or_create(db, startup.id)
    sub.trial_used_at = subs.now()  # no trial: a plain Starter workspace
    db.flush()
    for call in (_enroll, _follow):
        r = call(client, h)
        assert r.status_code == 402, r.text
        err = r.json()["error"]
        assert err["code"] == "PLAN_REQUIRED" and err["message"] == "Upgrade to Growth to use this."
        assert err["field_errors"] == [{"field": "plan", "message": "growth"}]


def test_trial_and_paid_plans_pass_the_gate(client, db, monkeypatch):
    _, startup, h = workspace(db)
    monkeypatch.setattr(settings, "PLAN_ENFORCEMENT", True)
    assert _enroll(client, h).status_code != 402  # the lazy trial started on first read
    sub = db.query(BillingSubscription).filter_by(startup_id=startup.id).one()
    assert sub.status == "trialing"
    sub.trial_ends_at = subs.now() - timedelta(seconds=1)
    db.flush()
    assert _enroll(client, h).status_code == 402  # trial over, back on Starter
    sub.status, sub.plan_key = "active", "growth"
    sub.current_period_end = subs.now() + timedelta(days=10)
    db.flush()
    assert _enroll(client, h).status_code != 402


def test_scale_only_features_need_scale(db, monkeypatch):
    _, startup, _ = workspace(db)
    monkeypatch.setattr(settings, "PLAN_ENFORCEMENT", True)
    sub = subs.get_or_create(db, startup.id)
    sub.status, sub.plan_key = "active", "growth"
    sub.current_period_end = subs.now() + timedelta(days=5)
    db.flush()
    assert gate.has_feature(db, startup.id, "sales_outreach") is True
    assert gate.has_feature(db, startup.id, "legal") is False
    with pytest.raises(gate.PlanRequired) as exc:
        gate.assert_feature(db, startup.id, "legal")
    assert exc.value.message == "Upgrade to Scale to use this."
    with pytest.raises(gate.PlanRequired) as exc2:
        gate.assert_feature(db, startup.id, "no_such_feature")
    assert exc2.value.message == "Upgrade to a paid plan to use this."
    sub.plan_key = "scale"
    db.flush()
    assert gate.has_feature(db, startup.id, "legal") is True


def test_require_feature_dependency_is_reusable(db, monkeypatch):
    user, startup, h = workspace(db, MembershipRole.founder)
    app = FastAPI()
    from app.core.errors import register_exception_handlers  # same envelope as the real app

    register_exception_handlers(app)

    @app.get("/locked")
    def locked(m=Depends(gate.require_feature("funding"))):  # noqa: B008
        return {"ok": True}

    app.dependency_overrides[get_db] = lambda: db
    c = TestClient(app)
    assert c.get("/locked", headers=h).status_code == 200  # dark
    monkeypatch.setattr(settings, "PLAN_ENFORCEMENT", True)
    sub = subs.get_or_create(db, startup.id)
    sub.trial_used_at = subs.now()
    db.flush()
    r = c.get("/locked", headers=h)
    assert r.status_code == 402 and r.json()["error"]["message"] == "Upgrade to Scale to use this."


def test_receipt_and_catalog_helpers(db):
    workspace(db)
    assert subs.add_interval(subs.now().replace(year=2026, month=1, day=31), "monthly").month == 2
    assert subs.add_interval(subs.now().replace(year=2026, month=3, day=15), "annual").year == 2027
