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


def test_summary_returns_all_sections(client, db):
    _u, s, h = _member(db)
    db.commit()

    r = client.get("/api/v1/dashboard/summary", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert set(data) >= {
        "greeting",
        "health",
        "mission",
        "upcoming",
        "kpis",
        "calibration",
        "briefing",
        "risks",
        "opportunities",
    }
    assert data["greeting"]["startup_name"] == s.name


def test_summary_requires_membership(client, db):
    _u, s, _h = _member(db)
    outsider = create_user(db, email_verified_at=datetime.now(UTC))
    db.commit()
    headers = {
        "Authorization": f"Bearer {create_access_token(str(outsider.id))}",
        "X-Workspace-Id": str(s.id),
    }

    r = client.get("/api/v1/dashboard/summary", headers=headers)
    assert r.status_code == 403


def test_summary_requires_verified_email(client, db):
    u = create_user(db, email_verified_at=None)
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.commit()
    headers = {
        "Authorization": f"Bearer {create_access_token(str(u.id))}",
        "X-Workspace-Id": str(s.id),
    }

    r = client.get("/api/v1/dashboard/summary", headers=headers)
    assert r.status_code == 403
