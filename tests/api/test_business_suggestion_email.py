"""Suggestion author e-mail goes to founder and team member only; everyone else sees the name."""

import pytest

from app.db.models.enums import MembershipRole, SuggestionOp
from app.db.models.membership import Membership
from app.services.business.suggestions import create_suggestion
from tests.documents_helpers import headers, team

BB = "/api/v1/business-builder"
F, TM = MembershipRole.founder, MembershipRole.team_member


@pytest.mark.parametrize("role", list(MembershipRole))
def test_suggestion_author_email_by_role(client, db, role):
    s, u = team(db)
    author = u[MembershipRole.business_consultant]
    m = db.query(Membership).filter_by(user_id=author.id, startup_id=s.id).one()
    create_suggestion(
        db,
        m,
        SuggestionOp.canvas_update,
        {"canvas_type": "business_model"},
        {"blocks": {"key_partners": ["Acme"]}},
        "tighten this",
    )
    db.commit()
    r = client.get(f"{BB}/suggestions", headers=headers(u[role], s))
    if role == MembershipRole.investor:
        pytest.skip("investor has no Business Builder access")
    assert r.status_code == 200, r.text
    rows = r.json()["data"]["suggestions"]
    assert len(rows) == 1
    got = rows[0]["author"]
    assert got["id"] == str(author.id)
    if role in (F, TM):
        assert got["email"] == author.email
    else:
        assert got["email"] is None
        assert author.email not in r.text
