"""D-ASM-1: assessment reads are founder + team_member only (answers + narrative are founder
business data). Every other role gets the project's standard 403 FORBIDDEN envelope."""

import pytest

from app.db.models.enums import MembershipRole
from tests.api.assessment.helpers import (
    BASE,
    BLOCKED_ROLES,
    READ_ROLES,
    founder,
    headers_for,
    member,
    take_and_complete,
)


def _seed(client, db):
    _u, s, fh = founder(db)
    db.commit()
    aid = take_and_complete(client, fh)
    return s, aid


def test_blocked_roles_are_every_non_founder_non_team_role():
    assert set(BLOCKED_ROLES) == {
        MembershipRole.mentor,
        MembershipRole.accountant,
        MembershipRole.legal_advisor,
        MembershipRole.business_consultant,
        MembershipRole.investor,
    }


@pytest.mark.parametrize("role", READ_ROLES)
def test_founder_and_team_member_can_read_all_three(client, db, role):
    s, aid = _seed(client, db)
    _u, h = member(db, s, role)
    db.commit()
    assert client.get(BASE, headers=h).status_code == 200
    assert client.get(f"{BASE}/{aid}", headers=h).status_code == 200
    assert client.get(f"{BASE}/compare?ids={aid}", headers=h).status_code == 200


@pytest.mark.parametrize("role", BLOCKED_ROLES)
def test_other_roles_get_403_on_list_detail_and_compare(client, db, role):
    s, aid = _seed(client, db)
    _u, h = member(db, s, role)
    db.commit()
    for url in (BASE, f"{BASE}/{aid}", f"{BASE}/compare?ids={aid}"):
        r = client.get(url, headers=h)
        assert r.status_code == 403, (role, url, r.text)
        assert r.json()["error"]["code"] == "FORBIDDEN"
        # nothing about the assessment leaks in the refusal
        assert "answers" not in r.text and "narrative" not in r.text


def test_outsider_and_cross_workspace_unchanged(client, db):
    s, aid = _seed(client, db)
    _u2, _s2, other_h = founder(db)
    db.commit()
    # another workspace's founder, pointing at OUR workspace id: not a member -> 403
    outsider = {**other_h, "X-Workspace-Id": str(s.id)}
    assert client.get(BASE, headers=outsider).status_code == 403
    # their own workspace, our assessment id -> 404 (tenancy scoped)
    assert client.get(f"{BASE}/{aid}", headers=other_h).status_code == 404


def test_write_endpoints_still_founder_only(client, db):
    s, _aid = _seed(client, db)
    tm, _ = member(db, s, MembershipRole.team_member)
    db.commit()
    assert client.post(BASE, headers=headers_for(tm, s)).status_code == 403
