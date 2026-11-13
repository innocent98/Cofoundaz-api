"""Suggestion author e-mail goes to founder and team member only; everyone else sees the name."""

import pytest

from app.db.models.enums import MembershipRole, SuggestionOp
from app.db.models.membership import Membership
from app.db.models.user import UserProfile
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


def _body(note="x"):
    return {
        "op": "canvas_update",
        "target": {"canvas_type": "business_model"},
        "payload": {"blocks": {"key_partners": ["Acme"]}},
        "note": note,
    }


def test_create_by_consultant_hides_the_authors_own_email(client, db):
    s, u = team(db)
    consultant = u[MembershipRole.business_consultant]
    r = client.post(f"{BB}/suggestions", json=_body(), headers=headers(consultant, s))
    assert r.status_code == 201, r.text
    assert r.json()["data"]["author"]["email"] is None
    assert consultant.email not in r.text


@pytest.mark.parametrize("resolver", [F, TM])
def test_approve_and_reject_show_the_email_to_founder_and_team_member(client, db, resolver):
    s, u = team(db)
    consultant = u[MembershipRole.business_consultant]
    ids = [
        client.post(f"{BB}/suggestions", json=_body(n), headers=headers(consultant, s)).json()[
            "data"
        ]["id"]
        for n in ("a", "b")
    ]
    h = headers(u[resolver], s)
    approved = client.post(f"{BB}/suggestions/{ids[0]}/approve", headers=h)
    rejected = client.post(f"{BB}/suggestions/{ids[1]}/reject", headers=h)
    assert approved.status_code == 200, approved.text
    assert rejected.status_code == 200, rejected.text
    for r in (approved, rejected):
        assert r.json()["data"]["author"]["email"] == consultant.email


@pytest.mark.parametrize("role", list(MembershipRole))
def test_editor_without_a_name_is_never_shown_by_email(client, db, role):
    """`updated_by` / `edited_by` fall back to a neutral label, not the address, for every role."""
    s, u = team(db)
    editor = u[TM]
    db.query(UserProfile).filter_by(user_id=editor.id).update({"full_name": None})
    db.commit()
    saved = client.put(
        f"{BB}/canvases/swot",
        json={"blocks": {"strengths": ["Team"]}, "version": 1},
        headers=headers(editor, s),
    )
    assert saved.status_code == 200, saved.text
    h = headers(u[role], s)
    canvas = client.get(f"{BB}/canvases/swot", headers=h)
    versions = client.get(f"{BB}/canvases/swot/versions", headers=h)
    assert canvas.status_code == 200 and versions.status_code == 200
    assert canvas.json()["data"]["updated_by"]["name"] == "A team member"
    assert versions.json()["data"]["versions"][0]["edited_by"]["name"] == "A team member"
    assert editor.email not in canvas.text + versions.text
