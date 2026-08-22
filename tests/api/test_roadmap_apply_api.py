from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from tests.factories import create_membership, create_startup, create_user


def _member(db, *, role=MembershipRole.founder, stage=StartupStage.validation):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u, stage=stage)
    create_membership(db, u, s, role=role)
    db.flush()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return u, s, h


def _tree_counts(client, headers):
    d = client.get("/api/v1/roadmap", headers=headers).json()["data"]
    ph = len(d["phases"])
    ms = sum(len(p["milestones"]) for p in d["phases"])
    return ph, ms


def test_apply_then_reapply_is_noop(client, db, monkeypatch):
    events = []
    from app.platform import events as ev

    monkeypatch.setattr(ev.event_bus, "publish", lambda e, p: events.append((e, p)))

    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    ph0, ms0 = _tree_counts(client, h)

    r = client.post("/api/v1/roadmap/templates/mvp-build/apply", headers=h)
    assert r.status_code == 201
    assert r.json()["data"]["already_applied"] is False
    assert any(e == "roadmap.template.applied" for e, _ in events)
    ph1, ms1 = _tree_counts(client, h)
    assert ph1 > ph0

    events.clear()
    r2 = client.post("/api/v1/roadmap/templates/mvp-build/apply", headers=h)
    assert r2.status_code == 200
    assert r2.json()["data"]["already_applied"] is True
    assert not any(e == "roadmap.template.applied" for e, _ in events)
    ph2, ms2 = _tree_counts(client, h)
    assert (ph2, ms2) == (ph1, ms1)  # no duplication

    # gallery now marks it applied
    items = client.get("/api/v1/roadmap/templates", headers=h).json()["data"]
    assert next(i for i in items if i["id"] == "mvp-build")["applied"] is True


def test_apply_unknown_404(client, db):
    _u, _s, h = _member(db, role=MembershipRole.founder, stage=StartupStage.idea)
    db.commit()
    assert client.post("/api/v1/roadmap/templates/nope/apply", headers=h).status_code == 404


def test_apply_forbidden_for_mentor(client, db):
    _u, _s, h = _member(db, role=MembershipRole.mentor, stage=StartupStage.idea)
    db.commit()
    assert client.post("/api/v1/roadmap/templates/mvp-build/apply", headers=h).status_code == 403
