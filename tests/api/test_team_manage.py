"""Team page (light): change role, remove, leave, invitations list / resend / revoke, audit,
comments index, and Documents grants revoked on removal."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.db.models.audit import AuditLog
from app.db.models.comment import Comment
from app.db.models.document import DocumentGrant
from app.db.models.enums import (
    DocumentKind,
    InvitationStatus,
    MembershipRole,
    MembershipStatus,
    ShareAccess,
)
from app.db.models.invitation import Invitation
from app.db.models.membership import Membership
from app.db.models.user import UserProfile
from app.services.auth.sessions import hash_token
from app.services.documents.service import create_document
from tests.documents_helpers import headers, team
from tests.factories import create_milestone, create_phase, create_roadmap

T = "/api/v1/team"
F, TM, M = MembershipRole.founder, MembershipRole.team_member, MembershipRole.mentor


@pytest.fixture
def mail(monkeypatch):
    out = []

    class _S:
        def send(self, msg):
            out.append(msg)

    monkeypatch.setattr("app.api.v1.endpoints.team.get_email_sender", lambda: _S())
    monkeypatch.setattr("app.services.onboarding.invites.get_email_sender", lambda: _S())
    return out


def _doc_with_grant(db, s, owner, grantee):
    doc = create_document(
        db,
        s,
        created_by_id=owner.id,
        kind=DocumentKind.custom,
        title="D",
        sections=[],
        folder=None,
        template_key=None,
    )
    g = DocumentGrant(
        startup_id=s.id,
        document_id=doc.id,
        grantee_user_id=grantee.id,
        grantee_email=grantee.email.lower(),
        permission=ShareAccess.view,
    )
    db.add(g)
    db.flush()
    return g


# ---------------------------------------------------------------- members list


def test_members_list_has_join_and_last_active(client, db):
    s, u = team(db)
    db.commit()
    rows = client.get(f"{T}/members", headers=headers(u[F], s)).json()["data"]
    assert len(rows) == len(MembershipRole)
    assert {"user_id", "name", "email", "role", "joined_at", "last_active_at"} <= set(rows[0])
    # The sign-in time is account-wide: only founders get it, everyone else sees null.
    from app.db.models.auth import AuthSession

    db.add(
        AuthSession(
            user_id=u[M].id,
            family_id=uuid.uuid4(),
            refresh_token_hash="x" * 64,
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
    )
    db.commit()
    mentor_row = next(r for r in rows if r["user_id"] == str(u[M].id))
    assert mentor_row["last_active_at"] is None
    as_founder = client.get(f"{T}/members", headers=headers(u[F], s)).json()["data"]
    assert next(r for r in as_founder if r["user_id"] == str(u[M].id))["last_active_at"]
    as_mentor = client.get(f"{T}/members", headers=headers(u[M], s)).json()["data"]
    assert all(r["last_active_at"] is None for r in as_mentor)


# ---------------------------------------------------------------- change role


def test_founder_changes_a_role_and_it_is_audited(client, db):
    s, u = team(db)
    db.commit()
    r = client.patch(
        f"{T}/members/{u[M].id}", json={"role": "accountant"}, headers=headers(u[F], s)
    )
    assert r.status_code == 200 and r.json()["data"]["role"] == "accountant"
    db.expire_all()
    assert (
        db.query(Membership).filter_by(user_id=u[M].id, startup_id=s.id).one().role.value
        == "accountant"
    )
    audit = client.get(f"{T}/audit", headers=headers(u[F], s)).json()["data"]["entries"]
    assert audit[0]["action"] == "team.member.role_changed" and audit[0]["target"]


@pytest.mark.parametrize("role", [TM, M, MembershipRole.accountant, MembershipRole.investor])
def test_only_a_founder_manages_members(client, db, role):
    s, u = team(db)
    db.commit()
    h = headers(u[role], s)
    resp = client.patch(f"{T}/members/{u[M].id}", json={"role": "mentor"}, headers=h)
    assert resp.status_code == 403
    resp = client.delete(f"{T}/members/{u[M].id}", headers=h)
    assert resp.status_code == 403
    assert client.get(f"{T}/invitations", headers=h).status_code == 403
    assert client.get(f"{T}/audit", headers=h).status_code == 403
    assert client.get(f"{T}/audit/export", headers=h).status_code == 403


def test_role_change_rules(client, db):
    s, u = team(db)
    db.commit()
    h = headers(u[F], s)
    own = client.patch(f"{T}/members/{u[F].id}", json={"role": "mentor"}, headers=h)
    assert own.status_code == 409 and own.json()["error"]["code"] == "CANNOT_CHANGE_SELF"
    inv = client.patch(f"{T}/members/{u[M].id}", json={"role": "investor"}, headers=h)
    assert inv.status_code == 422
    resp = client.patch(f"{T}/members/{uuid.uuid4()}", json={"role": "mentor"}, headers=h)
    assert resp.status_code == 404


def test_cannot_manage_a_member_of_another_workspace(client, db):
    s, u = team(db)
    s2, u2 = team(db, name="Other")
    db.commit()
    h = headers(u[F], s)
    resp = client.patch(f"{T}/members/{u2[M].id}", json={"role": "accountant"}, headers=h)
    assert resp.status_code == 404
    resp = client.delete(f"{T}/members/{u2[M].id}", headers=h)
    assert resp.status_code == 404


# ---------------------------------------------------------------- remove / leave


def test_remove_locks_them_out_revokes_grants_and_keeps_history(client, db):
    s, u = team(db)
    g = _doc_with_grant(db, s, u[F], u[TM])
    other = _doc_with_grant(db, s, u[F], u[M])  # someone else's grant must survive
    db.commit()
    r = client.delete(f"{T}/members/{u[TM].id}", headers=headers(u[F], s))
    assert r.status_code == 200
    assert r.json()["data"] == {
        "user_id": str(u[TM].id),
        "removed": True,
        "document_grants_revoked": 1,
        "share_links_revoked": 0,
        "invitations_revoked": 0,
    }
    db.expire_all()
    assert db.get(DocumentGrant, g.id).revoked_at is not None
    assert db.get(DocumentGrant, other.id).revoked_at is None
    m = db.query(Membership).filter_by(user_id=u[TM].id, startup_id=s.id).one()
    assert m.status == MembershipStatus.removed  # the row stays: past activity stays attributed
    # Immediately locked out of the workspace routes.
    assert client.get(f"{T}/members", headers=headers(u[TM], s)).status_code == 403
    names = [
        x["user_id"] for x in client.get(f"{T}/members", headers=headers(u[F], s)).json()["data"]
    ]
    assert str(u[TM].id) not in names


def test_remove_revokes_grants_addressed_by_email_only(client, db):
    s, u = team(db)
    g = _doc_with_grant(db, s, u[F], u[TM])
    g.grantee_user_id = None  # a grant that was issued to the email, not yet bound to the account
    db.commit()
    client.delete(f"{T}/members/{u[TM].id}", headers=headers(u[F], s))
    db.expire_all()
    assert db.get(DocumentGrant, g.id).revoked_at is not None


def test_cannot_remove_yourself_or_a_stranger(client, db):
    s, u = team(db)
    db.commit()
    h = headers(u[F], s)
    r = client.delete(f"{T}/members/{u[F].id}", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "CANNOT_REMOVE_SELF"
    resp = client.delete(f"{T}/members/{uuid.uuid4()}", headers=h)
    assert resp.status_code == 404
    resp = client.delete(f"{T}/members/{u[TM].id}", headers=h)
    assert resp.status_code == 200
    resp = client.delete(f"{T}/members/{u[TM].id}", headers=h)
    assert resp.status_code == 404  # already gone


def test_member_leaves_and_loses_grants(client, db):
    s, u = team(db)
    g = _doc_with_grant(db, s, u[F], u[M])
    db.commit()
    r = client.post(f"{T}/leave", headers=headers(u[M], s))
    assert r.status_code == 200 and r.json()["data"]["document_grants_revoked"] == 1
    db.expire_all()
    assert db.get(DocumentGrant, g.id).revoked_at is not None
    assert client.get(f"{T}/members", headers=headers(u[M], s)).status_code == 403
    again = client.post(f"{T}/leave", headers=headers(u[M], s))
    assert again.status_code == 403


def test_the_last_founder_cannot_leave_but_a_co_founder_can(client, db):
    s, u = team(db)
    db.commit()
    r = client.post(f"{T}/leave", headers=headers(u[F], s))
    assert r.status_code == 409 and r.json()["error"]["code"] == "LAST_FOUNDER"
    # Promote a second founder, then the first may leave.
    client.patch(f"{T}/members/{u[TM].id}", json={"role": "founder"}, headers=headers(u[F], s))
    left = client.post(f"{T}/leave", headers=headers(u[F], s))
    assert left.status_code == 200
    last = client.post(f"{T}/leave", headers=headers(u[TM], s))
    assert last.status_code == 409 and last.json()["error"]["code"] == "LAST_FOUNDER"


def test_removed_member_can_be_invited_back(client, db, mail):
    s, u = team(db)
    db.commit()
    hf = headers(u[F], s)
    client.delete(f"{T}/members/{u[TM].id}", headers=hf)
    r = client.post(
        f"{T}/invitations", json={"invites": [{"email": u[TM].email, "role": "mentor"}]}, headers=hf
    )
    assert r.json()["data"]["created"] == [u[TM].email]
    inv = db.query(Invitation).filter_by(email=u[TM].email).one()
    raw = "rejoin-secret"
    inv.token_hash = hash_token(raw)
    db.commit()
    ok = client.post("/api/v1/invitations/accept", json={"token": raw}, headers=headers(u[TM]))
    assert ok.status_code == 200, ok.text
    db.expire_all()
    m = db.query(Membership).filter_by(user_id=u[TM].id, startup_id=s.id).one()
    assert m.status == MembershipStatus.active and m.role == MembershipRole.mentor
    assert client.get(f"{T}/members", headers=headers(u[TM], s)).status_code == 200


# ---------------------------------------------------------------- invitations


def _invite(client, hf, email="new@example.com", role="mentor"):
    r = client.post(
        f"{T}/invitations", json={"invites": [{"email": email, "role": role}]}, headers=hf
    )
    assert r.status_code == 200, r.text


def test_list_resend_revoke_invitation(client, db, mail):
    s, u = team(db)
    db.commit()
    hf = headers(u[F], s)
    _invite(client, hf)
    rows = client.get(f"{T}/invitations", headers=hf).json()["data"]
    assert [(r["email"], r["role"], r["expired"]) for r in rows] == [
        ("new@example.com", "mentor", False)
    ]
    inv_id = rows[0]["id"]
    old_hash = db.query(Invitation).filter_by(id=inv_id).one().token_hash
    before = len(mail)
    r = client.post(f"{T}/invitations/{inv_id}/resend", headers=hf)
    assert r.json()["data"] == {"id": inv_id, "resent": True, "delivered": True}
    assert len(mail) == before + 1 and mail[-1].to == "new@example.com"
    db.expire_all()
    assert (
        db.query(Invitation).filter_by(id=inv_id).one().token_hash != old_hash
    )  # old link is dead
    revoked = client.delete(f"{T}/invitations/{inv_id}", headers=hf)
    assert revoked.json()["data"] == {"id": inv_id, "revoked": True}
    assert client.get(f"{T}/invitations", headers=hf).json()["data"] == []
    for call in (
        client.post(f"{T}/invitations/{inv_id}/resend", headers=hf),
        client.delete(f"{T}/invitations/{inv_id}", headers=hf),
    ):
        assert call.status_code == 409 and call.json()["error"]["code"] == "INVITATION_NOT_PENDING"


def test_resend_revives_an_expired_invitation_and_failure_is_reported(client, db, monkeypatch):
    s, u = team(db)
    db.commit()
    hf = headers(u[F], s)

    class _Boom:
        def send(self, _m):
            raise RuntimeError("smtp down")

    monkeypatch.setattr("app.services.onboarding.invites.get_email_sender", lambda: _Boom())
    _invite(client, hf)
    inv = db.query(Invitation).one()
    inv.expires_at = datetime.now(UTC) - timedelta(days=1)
    db.commit()
    assert client.get(f"{T}/invitations", headers=hf).json()["data"][0]["expired"] is True
    monkeypatch.setattr("app.api.v1.endpoints.team.get_email_sender", lambda: _Boom())
    r = client.post(f"{T}/invitations/{inv.id}/resend", headers=hf)
    assert r.status_code == 200 and r.json()["data"]["delivered"] is False
    db.expire_all()
    assert db.query(Invitation).one().expires_at > datetime.now(UTC)  # revived anyway


def test_invitation_routes_are_workspace_scoped(client, db, mail):
    s, u = team(db)
    s2, u2 = team(db, name="Other")
    db.commit()
    _invite(client, headers(u2[F], s2))
    foreign = db.query(Invitation).one()
    hf = headers(u[F], s)
    foreign_resend = client.post(f"{T}/invitations/{foreign.id}/resend", headers=hf)
    assert foreign_resend.status_code == 404
    resp = client.delete(f"{T}/invitations/{foreign.id}", headers=hf)
    assert resp.status_code == 404
    assert client.get(f"{T}/invitations", headers=hf).json()["data"] == []
    assert db.query(Invitation).one().status == InvitationStatus.pending


# ---------------------------------------------------------------- audit


def test_audit_lists_team_actions_pages_and_exports_csv(client, db, mail):
    s, u = team(db)
    db.commit()
    hf = headers(u[F], s)
    _invite(client, hf, email="cmd@example.com")
    db.query(UserProfile).filter_by(user_id=u[M].id).one().full_name = "=SUM(1+1)"
    db.commit()
    client.patch(f"{T}/members/{u[M].id}", json={"role": "accountant"}, headers=hf)
    client.delete(f"{T}/members/{u[TM].id}", headers=hf)
    # The test DB runs in one transaction (now() is constant), so space the rows out as real
    # requests would be.
    base = datetime.now(UTC)
    for i, action in enumerate(
        ["team.invitation.created", "team.member.role_changed", "team.member.removed"]
    ):
        db.query(AuditLog).filter_by(action=action).update(
            {AuditLog.created_at: base + timedelta(seconds=i)}
        )
    db.commit()
    r = client.get(f"{T}/audit?limit=2", headers=hf).json()["data"]
    assert [e["action"] for e in r["entries"]] == [
        "team.member.removed",
        "team.member.role_changed",
    ]
    assert r["next_cursor"]
    page2 = client.get(f"{T}/audit?limit=2&cursor={r['next_cursor']}", headers=hf).json()["data"]
    assert [e["action"] for e in page2["entries"]] == ["team.invitation.created"]
    assert page2["next_cursor"] is None
    csv_r = client.get(f"{T}/audit/export", headers=hf)
    assert csv_r.headers["content-type"].startswith("text/csv")
    assert "attachment" in csv_r.headers["content-disposition"]
    assert csv_r.text.splitlines()[0] == "when,actor,action,target"
    assert "'=SUM(1+1)" in csv_r.text  # formula-injection guard


def test_audit_never_shows_journal_or_other_workspaces(client, db):
    from app.platform.audit import write_audit

    s, u = team(db)
    s2, u2 = team(db, name="Other")
    write_audit(db, "journal.exported", actor_user_id=u[F].id, startup_id=s.id)
    write_audit(db, "team.member.removed", actor_user_id=u2[F].id, startup_id=s2.id)
    write_audit(db, "workspace.updated", actor_user_id=u[F].id, startup_id=s.id)
    db.commit()
    actions = [
        e["action"]
        for e in client.get(f"{T}/audit", headers=headers(u[F], s)).json()["data"]["entries"]
    ]
    assert actions == ["workspace.updated"]


# ---------------------------------------------------------------- comments index


def _thread(db, s, author, mention=None, resolved=False, title="Validate demand"):
    roadmap = create_roadmap(db, s)
    ms = create_milestone(db, create_phase(db, roadmap), title=title)
    c = Comment(
        startup_id=s.id,
        entity_type="roadmap_milestone",
        entity_id=ms.id,
        author_id=author.id,
        body="Need the survey numbers before we decide",
        mention_user_ids=[str(mention.id)] if mention else [],
        resolved_at=datetime.now(UTC) if resolved else None,
    )
    db.add(c)
    db.flush()
    return ms, c


def test_comments_index_filters(client, db):
    s, u = team(db)
    _thread(db, s, u[F], mention=u[TM], title="Mentions the TM")
    db.commit()
    htm, hf = headers(u[TM], s), headers(u[F], s)
    allr = client.get(f"{T}/comments/threads", headers=htm).json()["data"]
    assert [t["context"] for t in allr] == ["Mentions the TM"]
    assert allr[0]["mentions_me"] is True and allr[0]["unresolved_count"] == 1
    assert allr[0]["last_comment"]["snippet"].startswith("Need the survey")
    assert len(client.get(f"{T}/comments/threads?filter=mentions", headers=htm).json()["data"]) == 1
    assert (
        len(client.get(f"{T}/comments/threads?filter=mentions", headers=hf).json()["data"]) == 0
    )  # author, not mentioned
    assert (
        len(client.get(f"{T}/comments/threads?filter=unresolved", headers=hf).json()["data"]) == 1
    )
    # A member who is neither author nor mentioned sees nothing.
    assert client.get(f"{T}/comments/threads", headers=headers(u[M], s)).json()["data"] == []
    assert client.get(f"{T}/comments/threads?filter=bad", headers=hf).status_code == 422


def test_resolved_thread_drops_out_of_unresolved(client, db):
    s, u = team(db)
    _thread(db, s, u[F], resolved=True)
    db.commit()
    hf = headers(u[F], s)
    assert len(client.get(f"{T}/comments/threads", headers=hf).json()["data"]) == 1
    assert client.get(f"{T}/comments/threads?filter=unresolved", headers=hf).json()["data"] == []


def test_csv_cells_are_neutralised_even_after_whitespace(db):
    from app.services.team.members import _csv_safe

    for bad in [
        "=1+1",
        "+1",
        "-1",
        "@SUM(A1)",
        "\t=1",
        "\r=1",
        "  =1",
        "\x00=1",
        "\u200b=1",
        "\u00a0=1",
        "\ufeff@x",
    ]:
        assert _csv_safe(bad).startswith("'"), repr(bad)
    assert _csv_safe("Ada Founder") == "Ada Founder"
    assert _csv_safe("") == ""


def test_founder_changes_recheck_the_actor_under_lock(client, db):
    # A founder demoted a moment ago (their request already authenticated) must not still manage
    # members: the service re-checks the actor's current membership.
    from app.core.errors import Forbidden
    from app.db.models.startup import Startup
    from app.services.team.members import change_role, remove_member

    s, u = team(db)
    startup = db.get(Startup, s.id)
    db.query(Membership).filter_by(user_id=u[F].id, startup_id=s.id).one().role = TM
    db.flush()
    with pytest.raises(Forbidden):
        change_role(db, startup=startup, actor=u[F], target_user_id=u[M].id, role=TM)
    with pytest.raises(Forbidden):
        remove_member(db, startup=startup, actor=u[F], target_user_id=u[M].id)


def test_removal_kills_their_share_links_and_pending_invitations(client, db, mail):
    from app.db.models.document import DocumentShare

    s, u = team(db)
    g = _doc_with_grant(db, s, u[F], u[M])
    link = DocumentShare(
        startup_id=s.id,
        document_id=g.document_id,
        shared_by_id=u[TM].id,
        email=None,
        access_level=ShareAccess.view,
        token_hash="h" * 64,
    )
    own = DocumentShare(
        startup_id=s.id,
        document_id=g.document_id,
        shared_by_id=u[F].id,
        email=None,
        access_level=ShareAccess.view,
        token_hash="i" * 64,
    )
    db.add_all([link, own])
    db.commit()
    # A second founder sends an invitation, then gets removed by the first.
    client.patch(f"{T}/members/{u[TM].id}", json={"role": "founder"}, headers=headers(u[F], s))
    client.post(
        f"{T}/invitations",
        json={"invites": [{"email": "x@example.com", "role": "mentor"}]},
        headers=headers(u[TM], s),
    )
    r = client.delete(f"{T}/members/{u[TM].id}", headers=headers(u[F], s))
    assert r.json()["data"]["share_links_revoked"] == 1
    assert r.json()["data"]["invitations_revoked"] == 1
    db.expire_all()
    assert db.get(DocumentShare, link.id).revoked_at is not None
    assert db.get(DocumentShare, own.id).revoked_at is None  # someone else's link survives
    assert (
        db.query(Invitation).filter_by(email="x@example.com").one().status
        == InvitationStatus.revoked
    )


def test_demoting_a_founder_revokes_the_invitations_they_sent(client, db, mail):
    s, u = team(db)
    db.commit()
    hf = headers(u[F], s)
    client.patch(f"{T}/members/{u[TM].id}", json={"role": "founder"}, headers=hf)
    client.post(
        f"{T}/invitations",
        json={"invites": [{"email": "y@example.com", "role": "founder"}]},
        headers=headers(u[TM], s),
    )
    client.patch(f"{T}/members/{u[TM].id}", json={"role": "mentor"}, headers=hf)
    assert (
        db.query(Invitation).filter_by(email="y@example.com").one().status
        == InvitationStatus.revoked
    )
