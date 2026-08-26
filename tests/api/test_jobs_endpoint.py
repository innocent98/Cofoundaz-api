import uuid
from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole
from app.platform.jobs import JobDispatcher
from tests.factories import create_membership, create_startup, create_user


def _member(db):
    """A verified founder + their workspace + a Bearer header (no workspace header:
    job polling happens before the FE has an X-Workspace-Id)."""
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    return u, s, {"Authorization": f"Bearer {create_access_token(str(u.id))}"}


def test_get_job_requires_auth(client, db):
    _u, s, _h = _member(db)
    job = JobDispatcher().enqueue(db, "roadmap.generate", {}, s.id)
    db.commit()
    assert client.get(f"/api/v1/jobs/{job.id}").status_code == 401


def test_member_reads_own_workspace_job(client, db):
    _u, s, h = _member(db)
    job = JobDispatcher().enqueue(db, "roadmap.generate", {}, s.id)
    db.commit()
    r = client.get(f"/api/v1/jobs/{job.id}", headers=h)
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["type"] == "roadmap.generate"
    assert body["status"] == "queued"


def test_cross_workspace_job_404(client, db):
    _ua, sa, _ha = _member(db)
    _ub, _sb, hb = _member(db)  # different user + workspace
    job = JobDispatcher().enqueue(db, "roadmap.generate", {}, sa.id)
    db.commit()
    # B is not a member of A's workspace -> uniform 404, no existence leak.
    assert client.get(f"/api/v1/jobs/{job.id}", headers=hb).status_code == 404


def test_missing_job_404(client, db):
    _u, _s, h = _member(db)
    db.commit()
    assert client.get(f"/api/v1/jobs/{uuid.uuid4()}", headers=h).status_code == 404


def test_orphan_job_without_startup_not_readable(client, db):
    _u, _s, h = _member(db)
    job = JobDispatcher().enqueue(db, "orphan.job", {})  # no startup_id
    db.commit()
    assert client.get(f"/api/v1/jobs/{job.id}", headers=h).status_code == 404
