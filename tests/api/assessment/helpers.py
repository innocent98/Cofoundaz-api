"""Shared builders for the Assessment PRD tests (roles, walking an assessment to the end)."""

from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole
from tests.factories import create_membership, create_startup, create_user

BASE = "/api/v1/assessments"

# Roles that may read assessments (D-ASM-1) and the rest of MembershipRole.
READ_ROLES = (MembershipRole.founder, MembershipRole.team_member)
BLOCKED_ROLES = tuple(r for r in MembershipRole if r not in READ_ROLES)


def headers_for(user, startup) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {create_access_token(str(user.id))}",
        "X-Workspace-Id": str(startup.id),
    }


def founder(db, **startup_kw):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u, **startup_kw)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    return u, s, headers_for(u, s)


def member(db, startup, role: MembershipRole):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, u, startup, role=role)
    db.flush()
    return u, headers_for(u, startup)


def walk(client, h, aid, *, pick: int = 0) -> list[str]:
    """Answer every remaining question (option `pick`, scale 3, 1000, 'n/a'); return the keys."""
    vals = {"scale_1_5": 3, "numeric_currency": 1000, "short_text": "n/a"}
    keys: list[str] = []
    while True:
        nq = client.get(f"{BASE}/{aid}/next-question", headers=h).json()["data"]["next_question"]
        if nq is None:
            return keys
        if nq["qtype"] == "single_choice":
            val = nq["options"][pick]["value"]
        elif nq["qtype"] == "multi_choice":
            val = [nq["options"][pick]["value"]]
        else:
            val = vals[nq["qtype"]]
        r = client.post(
            f"{BASE}/{aid}/answers", headers=h, json={"question_key": nq["key"], "value": val}
        )
        assert r.status_code == 200, r.text
        keys.append(nq["key"])


def take_and_complete(client, h, *, pick: int = 0) -> str:
    """Start (or resume) an assessment, walk it to the end and complete it. Returns its id."""
    aid = client.post(BASE, headers=h).json()["data"]["assessment_id"]
    walk(client, h, aid, pick=pick)
    r = client.post(f"{BASE}/{aid}/complete", headers=h)
    assert r.status_code == 200, r.text
    return aid
