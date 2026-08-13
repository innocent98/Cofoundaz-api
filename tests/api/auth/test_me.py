from datetime import UTC, datetime, timedelta

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, UserStatus
from app.db.models.membership import Membership, MembershipStatus
from tests.factories import create_membership, create_startup, create_user


def test_me_returns_identity_and_memberships(client, db):
    u = create_user(db, email="me@x.com", status=UserStatus.active)
    u.profile.full_name = "Ada Founder"
    s = create_startup(db, owner=u, name="Cofoundaz")
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    r = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {create_access_token(str(u.id))}"}
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["user"]["email"] == "me@x.com"
    assert data["profile"]["full_name"] == "Ada Founder"
    assert data["memberships"][0]["role"] == "founder"
    assert data["active_workspace_id"] == str(s.id)


def test_me_requires_auth(client):
    assert client.get("/api/v1/auth/me").status_code == 401


def test_me_active_workspace_id_is_deterministic_across_calls(client, db):
    # All inserts in this test share one Postgres transaction (the `db` fixture wraps the
    # whole test in a savepoint that never commits), and `TimestampMixin.created_at` uses
    # `server_default=func.now()` -- `now()` is transaction-scoped in Postgres, so it would
    # tie for every row inserted here unless set explicitly. Setting distinct values
    # directly is what actually exercises "earliest-created wins", not just "whatever the
    # DB's default row order happens to be" (which is exactly the non-determinism bug).
    u = create_user(db, email="multi@x.com", status=UserStatus.active)
    s1 = create_startup(db, owner=u, name="First")
    s2 = create_startup(db, owner=u, name="Second")
    earlier = datetime.now(UTC) - timedelta(hours=1)
    later = datetime.now(UTC)
    # Insert the later-created (s2) membership first, so row-insertion order disagrees
    # with created_at order -- only an explicit ORDER BY should decide the winner.
    db.add(
        Membership(
            user_id=u.id,
            startup_id=s2.id,
            role=MembershipRole.founder,
            status=MembershipStatus.active,
            created_at=later,
        )
    )
    db.add(
        Membership(
            user_id=u.id,
            startup_id=s1.id,
            role=MembershipRole.founder,
            status=MembershipStatus.active,
            created_at=earlier,
        )
    )
    db.flush()
    headers = {"Authorization": f"Bearer {create_access_token(str(u.id))}"}

    for _ in range(5):
        r = client.get("/api/v1/auth/me", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["active_workspace_id"] == str(s1.id)
