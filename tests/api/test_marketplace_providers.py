"""Marketplace slice A: experts (providers), the `require_provider` guard, invite / apply, the
profile and credential APIs, and Admin vetting (checklist, decisions, audited file reads)."""

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from html import escape

import pyotp
import pytest
from cryptography.fernet import Fernet

from app.api.provider_deps import require_provider
from app.core.config import settings
from app.core.errors import AppError
from app.core.redis import get_redis
from app.db.models.audit import AuditLog
from app.db.models.enums import MembershipRole, ProviderStatus, StaffRole
from app.db.models.marketplace import MarketplaceFile, MarketplaceProvider
from app.platform.email import ConsoleEmailSender
from app.platform.storage import get_storage
from app.services.marketplace import emails as mp_emails
from app.services.marketplace.providers import hash_token
from tests.api.test_staff_users import hdr, member, mk_staff, mk_user, mk_ws

P = "/api/v1/provider"
A = "/api/v1/admin/marketplace"
REASON = "Checked the documents against the rubric"
PDF = b"%PDF-1.4\n%fake but magic-correct\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
BIO = "Fifteen years advising founders on contracts, shareholder agreements and fundraising."


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "APP_BASE_URL", "https://app.example.test")
    monkeypatch.setattr(settings, "MARKETPLACE_OPEN_APPLICATIONS", False)
    r = get_redis()
    for pat in ("staff_stepup_*",):
        for k in r.scan_iter(pat):
            r.delete(k)


@pytest.fixture()
def mail(monkeypatch):
    sender = ConsoleEmailSender()
    monkeypatch.setattr(mp_emails, "get_email_sender", lambda: sender)
    return sender


@pytest.fixture()
def moderator(db):
    return mk_staff(db, StaffRole.admin, ("moderation",))


def token_from(msg) -> str:
    m = re.search(r"/provider/invite/([A-Za-z0-9_\-]+)", msg.html)
    assert m, msg.html
    return m.group(1)


def staff_post(client, actor, path, reason=REASON, **extra):
    r = get_redis()
    for k in r.scan_iter("staff_stepup_used:*"):
        r.delete(k)
    body = {"reason": reason, "totp_code": pyotp.TOTP(actor.test_secret).now(), **extra}
    return client.post(f"{A}{path}", json=body, headers=hdr(actor))


def invite(client, staff, email="ngozi@example.com", **extra):
    return client.post(f"{A}/providers/invite", json={"email": email, **extra}, headers=hdr(staff))


def invited_and_accepted(client, db, staff, mail, email="ngozi@example.com", category="Legal"):
    """An expert who accepted their invite: (user, provider_id)."""
    r = invite(client, staff, email, category=category)
    assert r.status_code == 201, r.text
    token = token_from(mail.sent[-1])
    user = mk_user(db, email=email, name="Ngozi Eze")
    ok = client.post(
        f"{P}/invites/accept", json={"token": token, "accept_terms": True}, headers=hdr(user)
    )
    assert ok.status_code == 200, ok.text
    return user, ok.json()["data"]["id"]


def upload(
    client, user, content=PDF, name="licence.pdf", ctype="application/pdf", purpose="credential"
):
    return client.post(
        f"{P}/files",
        data={"purpose": purpose},
        files={"file": (name, content, ctype)},
        headers=hdr(user),
    )


def fill_profile(client, user, **over):
    body = {
        "display_name": "Ngozi Eze",
        "headline": "Corporate lawyer for early-stage startups",
        "bio": BIO,
        "category": "Legal",
        "languages": ["English"],
        **over,
    }
    r = client.patch(f"{P}/me", json=body, headers=hdr(user))
    assert r.status_code == 200, r.text
    return r.json()["data"]


def ready_to_submit(client, db, staff, mail, **kw):
    user, pid = invited_and_accepted(client, db, staff, mail, **kw)
    fill_profile(client, user)
    assert upload(client, user).status_code == 201
    return user, pid


def in_review(client, db, staff, mail, **kw):
    user, pid = ready_to_submit(client, db, staff, mail, **kw)
    r = client.post(f"{P}/submit", headers=hdr(user))
    assert r.status_code == 200, r.text
    return user, pid


def tick_all_legal(client, staff, pid):
    r = client.put(
        f"{A}/providers/{pid}/vetting",
        json={
            "checks": {
                "id": {"done": True, "note": "NIN slip seen"},
                "licence": {
                    "done": True,
                    "expires_on": (date.today() + timedelta(days=365)).isoformat(),
                },
                "references": {"done": True, "count": 2},
            }
        },
        headers=hdr(staff),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def approved(client, db, staff, mail, **kw):
    user, pid = in_review(client, db, staff, mail, **kw)
    tick_all_legal(client, staff, pid)
    r = staff_post(client, staff, f"/providers/{pid}/approve")
    assert r.status_code == 200, r.text
    return user, pid


def audit_actions(db, entity_id=None):
    q = db.query(AuditLog.action)
    if entity_id:
        q = q.filter(AuditLog.entity_id == uuid.UUID(str(entity_id)))
    return [a for (a,) in q.all()]


# ---------------------------------------------------------------- the guard


PROVIDER_ROUTES = [
    ("get", "/me"),
    ("patch", "/me"),
    ("post", "/submit"),
    ("post", "/files"),
    ("get", f"/files/{uuid.uuid4()}/content"),
    ("delete", f"/files/{uuid.uuid4()}"),
    ("post", "/portfolio"),
    ("patch", f"/portfolio/{uuid.uuid4()}"),
    ("delete", f"/portfolio/{uuid.uuid4()}"),
]


def test_every_provider_route_needs_sign_in(client):
    for method, path in PROVIDER_ROUTES:
        assert getattr(client, method)(f"{P}{path}").status_code == 401, path


def test_non_providers_get_403_on_every_provider_route(client, db):
    founder = mk_user(db)
    ws = mk_ws(db, founder, plan="growth")
    teammate = mk_user(db)
    member(db, ws, teammate, MembershipRole.team_member)
    staff = mk_staff(db, StaffRole.super_admin)
    for who in (founder, teammate, staff):
        for method, path in PROVIDER_ROUTES:
            r = getattr(client, method)(f"{P}{path}", headers=hdr(who))
            assert r.status_code == 403, (method, path, r.status_code)
            assert r.json()["error"]["code"] == "NOT_A_PROVIDER"


def test_unverified_email_is_refused(client, db):
    u = mk_user(db, verified=False)
    r = client.get(f"{P}/me", headers=hdr(u))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"


def test_an_expert_cannot_use_workspace_routes(client, db, moderator, mail):
    user, _ = invited_and_accepted(client, db, moderator, mail)
    r = client.get("/api/v1/documents/files", headers=hdr(user))
    assert r.status_code in (400, 403, 404, 422)  # no workspace: never data
    assert "Ngozi" not in r.text


def test_an_expert_is_not_staff(client, db, moderator, mail):
    user, _ = invited_and_accepted(client, db, moderator, mail)
    r = client.get(f"{A}/providers", headers=hdr(user))
    assert r.status_code == 404


def test_guard_reads_the_database_every_request(client, db, moderator, mail):
    user, pid = invited_and_accepted(client, db, moderator, mail)
    assert client.get(f"{P}/me", headers=hdr(user)).status_code == 200
    db.query(MarketplaceProvider).filter_by(id=uuid.UUID(pid)).update(
        {"status": ProviderStatus.rejected}
    )
    db.flush()
    r = client.get(f"{P}/me", headers=hdr(user))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "PROVIDER_REJECTED"


def test_approved_only_guard_blocks_unapproved_and_suspended(db, moderator, client, mail):
    user, pid = invited_and_accepted(client, db, moderator, mail)
    row = db.get(MarketplaceProvider, uuid.UUID(pid))
    dep = require_provider(approved=True)
    for st in (ProviderStatus.invited, ProviderStatus.in_review, ProviderStatus.suspended):
        row.status = st
        db.flush()
        with pytest.raises(AppError) as exc:
            dep(user=user, db=db)
        assert exc.value.code == "PROVIDER_NOT_APPROVED"
        assert exc.value.http_status == 403
    row.status = ProviderStatus.approved
    db.flush()
    assert dep(user=user, db=db).provider.id == row.id


def test_status_probe_never_403s(client, db, moderator, mail):
    plain = mk_user(db)
    r = client.get(f"{P}/status", headers=hdr(plain))
    assert r.status_code == 200
    assert r.json()["data"] == {
        "is_provider": False,
        "status": None,
        "applications_open": False,
    }
    user, _ = invited_and_accepted(client, db, moderator, mail)
    d = client.get(f"{P}/status", headers=hdr(user)).json()["data"]
    assert d["is_provider"] is True and d["status"] == "invited"


# ---------------------------------------------------------------- admin guard


ADMIN_ROUTES = [
    ("get", "/providers"),
    ("get", "/providers/counts"),
    ("post", "/providers/invite"),
    ("get", f"/providers/{uuid.uuid4()}"),
    ("put", f"/providers/{uuid.uuid4()}/vetting"),
    ("post", f"/providers/{uuid.uuid4()}/approve"),
    ("post", f"/providers/{uuid.uuid4()}/reject"),
    ("post", f"/providers/{uuid.uuid4()}/request-changes"),
    ("post", f"/providers/{uuid.uuid4()}/suspend"),
    ("post", f"/providers/{uuid.uuid4()}/unsuspend"),
    ("post", f"/providers/{uuid.uuid4()}/resend-invite"),
    ("post", f"/providers/{uuid.uuid4()}/revoke-invite"),
    ("get", f"/providers/{uuid.uuid4()}/files/{uuid.uuid4()}"),
]


def test_admin_routes_hide_from_non_staff_and_refuse_wrong_scope(client, db):
    founder = mk_user(db)
    mk_ws(db, founder)
    support_only = mk_staff(db, StaffRole.admin, ("support",))
    for method, path in ADMIN_ROUTES:
        assert getattr(client, method)(f"{A}{path}").status_code == 404, path
        assert getattr(client, method)(f"{A}{path}", headers=hdr(founder)).status_code == 404, path
        r = getattr(client, method)(f"{A}{path}", headers=hdr(support_only))
        assert r.status_code == 403, (path, r.status_code)


def test_super_admin_passes_the_scope_check(client, db):
    sa = mk_staff(db, StaffRole.super_admin)
    assert client.get(f"{A}/providers", headers=hdr(sa)).status_code == 200


# ---------------------------------------------------------------- invite


def test_invite_creates_a_pending_row_and_emails_a_link(client, db, moderator, mail):
    r = invite(client, moderator, "Ngozi@Example.com", category="Legal", display_name="Ngozi Eze")
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["undelivered"] == []
    prov = data["provider"]
    assert prov["email"] == "ngozi@example.com"  # normalised
    assert prov["status"] == "invited" and prov["invite_pending"] is True
    assert prov["user_id"] is None
    assert len(mail.sent) == 1 and mail.sent[0].to == "ngozi@example.com"
    assert "/provider/invite/" in mail.sent[0].html
    row = db.get(MarketplaceProvider, uuid.UUID(prov["id"]))
    assert row.invite_token_hash == hash_token(token_from(mail.sent[0]))  # only the hash is stored
    assert "marketplace.provider.invited" in audit_actions(db, prov["id"])


def test_invite_validation_and_duplicates(client, db, moderator, mail):
    assert invite(client, moderator, "not-an-email").status_code == 422
    assert invite(client, moderator, category="Cooking").status_code == 422
    assert invite(client, moderator).status_code == 201
    dup = invite(client, moderator)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "ALREADY_INVITED"


def test_invite_preview_is_public_and_never_leaks_the_token_state(client, db, moderator, mail):
    invite(client, moderator, category="Legal")
    token = token_from(mail.sent[0])
    r = client.get(f"{P}/invites/{token}")
    assert r.status_code == 200
    assert r.json()["data"]["email"] == "ngozi@example.com"
    assert client.get(f"{P}/invites/{'x' * 43}").status_code == 404


def test_accept_links_the_account_and_starts_a_draft(client, db, moderator, mail):
    user, pid = invited_and_accepted(client, db, moderator, mail)
    row = db.get(MarketplaceProvider, uuid.UUID(pid))
    assert row.user_id == user.id and row.invite_token_hash is None
    me = client.get(f"{P}/me", headers=hdr(user)).json()["data"]
    assert me["status"] == "invited" and me["terms_accepted_at"]
    assert {b["field"] for b in me["submit_blockers"]} >= {"headline", "bio", "credentials"}
    assert "marketplace.provider.invite_accepted" in audit_actions(db, pid)


def test_invite_token_is_single_use(client, db, moderator, mail):
    invite(client, moderator)
    token = token_from(mail.sent[0])
    user = mk_user(db, email="ngozi@example.com")
    body = {"token": token, "accept_terms": True}
    assert client.post(f"{P}/invites/accept", json=body, headers=hdr(user)).status_code == 200
    again = client.post(f"{P}/invites/accept", json=body, headers=hdr(user))
    assert again.status_code == 400 and again.json()["error"]["code"] == "TOKEN_INVALID"
    other = mk_user(db, email="ngozi@example.com".replace("ngozi", "ngozi2"))
    assert client.post(f"{P}/invites/accept", json=body, headers=hdr(other)).status_code == 400
    assert client.get(f"{P}/invites/{token}").status_code == 404  # preview is gone too


def test_expired_invite_is_refused(client, db, moderator, mail):
    r = invite(client, moderator)
    token = token_from(mail.sent[0])
    row = db.get(MarketplaceProvider, uuid.UUID(r.json()["data"]["provider"]["id"]))
    row.invite_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.flush()
    user = mk_user(db, email="ngozi@example.com")
    res = client.post(
        f"{P}/invites/accept", json={"token": token, "accept_terms": True}, headers=hdr(user)
    )
    assert res.status_code == 400 and res.json()["error"]["code"] == "TOKEN_INVALID"
    assert client.get(f"{P}/invites/{token}").status_code == 404


def test_wrong_account_cannot_accept_or_burn_the_invite(client, db, moderator, mail):
    invite(client, moderator)
    token = token_from(mail.sent[0])
    stranger = mk_user(db, email="someone.else@example.com")
    r = client.post(
        f"{P}/invites/accept", json={"token": token, "accept_terms": True}, headers=hdr(stranger)
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "INVITE_EMAIL_MISMATCH"
    assert client.get(f"{P}/invites/{token}").status_code == 200  # still usable by the right person


def test_accept_requires_terms_and_a_session(client, db, moderator, mail):
    invite(client, moderator)
    token = token_from(mail.sent[0])
    user = mk_user(db, email="ngozi@example.com")
    no_terms = client.post(
        f"{P}/invites/accept", json={"token": token, "accept_terms": False}, headers=hdr(user)
    )
    assert no_terms.status_code == 422
    anon = client.post(f"{P}/invites/accept", json={"token": token, "accept_terms": True})
    assert anon.status_code == 401


def test_resend_replaces_the_token_and_revoke_removes_the_row(client, db, moderator, mail):
    r = invite(client, moderator)
    pid = r.json()["data"]["provider"]["id"]
    first = token_from(mail.sent[0])
    res = client.post(f"{A}/providers/{pid}/resend-invite", headers=hdr(moderator))
    assert res.status_code == 200
    second = token_from(mail.sent[1])
    assert first != second
    assert client.get(f"{P}/invites/{first}").status_code == 404  # replay of the old link
    assert client.get(f"{P}/invites/{second}").status_code == 200
    rv = client.post(f"{A}/providers/{pid}/revoke-invite", headers=hdr(moderator))
    assert rv.status_code == 200
    assert client.get(f"{P}/invites/{second}").status_code == 404
    assert db.get(MarketplaceProvider, uuid.UUID(pid)) is None
    assert "marketplace.provider.invite_revoked" in audit_actions(db)


def test_resend_after_accept_is_refused(client, db, moderator, mail):
    _, pid = invited_and_accepted(client, db, moderator, mail)
    r = client.post(f"{A}/providers/{pid}/resend-invite", headers=hdr(moderator))
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVITE_NOT_PENDING"


def test_daily_invite_cap(client, db, moderator, mail, monkeypatch):
    monkeypatch.setattr("app.services.marketplace.providers.DAILY_INVITE_CAP", 2)
    assert invite(client, moderator, "a@example.com").status_code == 201
    assert invite(client, moderator, "b@example.com").status_code == 201
    r = invite(client, moderator, "c@example.com")
    assert r.status_code == 429 and r.json()["error"]["code"] == "INVITE_LIMIT_REACHED"


# ---------------------------------------------------------------- apply (open applications)


def test_apply_is_closed_until_the_flag_is_on(client, db):
    u = mk_user(db)
    r = client.post(f"{P}/apply", json={"accept_terms": True}, headers=hdr(u))
    assert r.status_code == 403 and r.json()["error"]["code"] == "APPLICATIONS_CLOSED"


def test_apply_when_open_feeds_the_same_queue(client, db, moderator, monkeypatch):
    monkeypatch.setattr(settings, "MARKETPLACE_OPEN_APPLICATIONS", True)
    u = mk_user(db, name="Tolu Ade")
    r = client.post(f"{P}/apply", json={"category": "Design", "accept_terms": True}, headers=hdr(u))
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    assert (
        d["status"] == "applied"
        and d["source"] == "application"
        and d["display_name"] == "Tolu Ade"
    )
    assert client.post(f"{P}/apply", json={"accept_terms": True}, headers=hdr(u)).status_code == 409
    q = client.get(f"{A}/providers?status=applied", headers=hdr(moderator)).json()["data"]
    assert [p["email"] for p in q["providers"]] == [u.email]
    no_terms = client.post(f"{P}/apply", json={"accept_terms": False}, headers=hdr(mk_user(db)))
    assert no_terms.status_code == 422


def test_apply_is_refused_when_an_invite_is_waiting(client, db, moderator, mail, monkeypatch):
    monkeypatch.setattr(settings, "MARKETPLACE_OPEN_APPLICATIONS", True)
    invite(client, moderator, "ngozi@example.com")
    u = mk_user(db, email="ngozi@example.com")
    r = client.post(f"{P}/apply", json={"accept_terms": True}, headers=hdr(u))
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVITE_PENDING"


# ---------------------------------------------------------------- profile


def test_profile_patch_round_trip_and_validation(client, db, moderator, mail):
    user, _ = invited_and_accepted(client, db, moderator, mail)
    d = fill_profile(client, user, location="Lagos", timezone="Africa/Lagos", categories=["Design"])
    assert d["headline"].startswith("Corporate") and d["categories"] == ["Design"]
    h = hdr(user)
    assert client.patch(f"{P}/me", json={}, headers=h).status_code == 422
    assert client.patch(f"{P}/me", json={"timezone": "Mars/Base"}, headers=h).status_code == 422
    assert client.patch(f"{P}/me", json={"timezone": None}, headers=h).status_code == 422
    assert client.patch(f"{P}/me", json={"category": "Cooking"}, headers=h).status_code == 422
    assert client.patch(f"{P}/me", json={"nope": 1}, headers=h).status_code == 422  # extra=forbid
    assert (
        client.patch(f"{P}/me", json={"user_id": str(uuid.uuid4())}, headers=h).status_code == 422
    )
    assert client.patch(f"{P}/me", json={"status": "approved"}, headers=h).status_code == 422


def test_cannot_borrow_a_regulated_category_as_a_secondary(client, db, moderator, mail):
    user, _ = invited_and_accepted(client, db, moderator, mail, category="Design")
    fill_profile(client, user, category="Design")
    r = client.patch(f"{P}/me", json={"categories": ["Legal"]}, headers=hdr(user))
    assert r.status_code == 422 and r.json()["error"]["code"] == "REGULATED_CATEGORY"


def test_prescreen_flags_contact_details_without_blocking(client, db, moderator, mail):
    user, pid = invited_and_accepted(client, db, moderator, mail)
    fill_profile(
        client,
        user,
        bio=BIO + " Call me on +234 803 123 4567 or email me@lawfirm.com for guaranteed approval.",
    )
    detail = client.get(f"{A}/providers/{pid}", headers=hdr(moderator)).json()["data"]
    rules = {f["rule"] for f in detail["prescreen_flags"]}
    assert "contact_details" in rules and "guaranteed_outcome" in rules


def test_expert_view_hides_staff_only_fields(client, db, moderator, mail):
    user, pid = in_review(client, db, moderator, mail)
    tick_all_legal(client, moderator, pid)
    me = client.get(f"{P}/me", headers=hdr(user)).json()["data"]
    assert me["badge"] == {"vetted": False, "checks": []}  # half-done ticks never leak
    assert "checks" not in me and "prescreen_flags" not in me and "history" not in me
    assert "commission_bps" not in me and "vetting_json" not in me


# ---------------------------------------------------------------- files


def test_upload_stores_privately_and_owner_can_download(client, db, moderator, mail):
    user, _ = invited_and_accepted(client, db, moderator, mail)
    r = upload(client, user)
    assert r.status_code == 201, r.text
    f = r.json()["data"]
    assert (
        f["mime"] == "application/pdf"
        and f["scan_status"] == "clean"
        and f["size_bytes"] == len(PDF)
    )
    row = db.get(MarketplaceFile, uuid.UUID(f["id"]))
    assert row.storage_key.startswith(f"marketplace/{user.id}/") and row.storage_key.endswith(
        ".pdf"
    )
    assert get_storage().read_private(row.storage_key) == PDF
    dl = client.get(f"{P}/files/{f['id']}/content", headers=hdr(user))
    assert dl.status_code == 200 and dl.content == PDF
    assert dl.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in dl.headers["content-disposition"]
    assert dl.headers["cache-control"] == "private, no-store"


def test_upload_sniffs_the_bytes_not_the_header(client, db, moderator, mail):
    user, _ = invited_and_accepted(client, db, moderator, mail)
    exe = b"MZ\x90\x00" + b"\x00" * 64
    r = upload(client, user, exe, "licence.pdf", "application/pdf")
    assert r.status_code == 422 and r.json()["error"]["code"] == "FILE_CONTENT_MISMATCH"
    assert (
        upload(client, user, PNG, "x.pdf", "application/pdf").status_code == 422
    )  # png sent as pdf
    assert upload(client, user, b"", "empty.pdf", "application/pdf").status_code == 422
    assert upload(client, user, PDF, "x.exe", "application/x-msdownload").json()["error"][
        "code"
    ] == ("UNSUPPORTED_FILE_TYPE")
    docx = b"PK\x03\x04" + b"\x00" * 30
    assert (
        upload(
            client,
            user,
            docx,
            "c.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ).json()["error"]["code"]
        == "UNSUPPORTED_FILE_TYPE"
    )  # not an allowed credential type
    assert db.query(MarketplaceFile).count() == 0  # nothing stored for any refusal


def test_upload_size_cap_is_15mb(client, db, moderator, mail):
    user, _ = invited_and_accepted(client, db, moderator, mail)
    ok = PDF + b"\x00" * (15 * 1024 * 1024 - len(PDF))
    assert upload(client, user, ok).status_code == 201
    big = PDF + b"\x00" * (15 * 1024 * 1024 - len(PDF) + 1)
    r = upload(client, user, big)
    assert r.status_code == 422 and r.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_avatar_rules_and_replacement(client, db, moderator, mail):
    user, _ = invited_and_accepted(client, db, moderator, mail)
    assert upload(client, user, PDF, "a.pdf", "application/pdf", "avatar").status_code == 422
    first = upload(client, user, PNG, "a.png", "image/png", "avatar").json()["data"]["id"]
    second = upload(client, user, JPG, "b.jpg", "image/jpeg", "avatar").json()["data"]["id"]
    assert (
        client.get(f"{P}/files/{first}/content", headers=hdr(user)).status_code == 404
    )  # replaced
    assert (
        client.patch(f"{P}/me", json={"avatar_file_id": second}, headers=hdr(user)).status_code
        == 200
    )
    cred = upload(client, user).json()["data"]["id"]
    bad = client.patch(f"{P}/me", json={"avatar_file_id": cred}, headers=hdr(user))
    assert bad.status_code == 422


def test_filename_is_sanitised(client, db, moderator, mail):
    user, _ = invited_and_accepted(client, db, moderator, mail)
    f = upload(client, user, name="../../etc/pass wd<script>.pdf").json()["data"]
    assert "/" not in f["filename"] and "<" not in f["filename"] and f["filename"].endswith(".pdf")


def test_credential_file_quota(client, db, moderator, mail, monkeypatch):
    monkeypatch.setitem(
        __import__("app.services.marketplace.files", fromlist=["x"]).MAX_FILES_PER_PURPOSE,
        "credential",
        2,
    )
    user, _ = invited_and_accepted(client, db, moderator, mail)
    assert upload(client, user).status_code == 201
    assert upload(client, user).status_code == 201
    r = upload(client, user)
    assert r.status_code == 409 and r.json()["error"]["code"] == "FILE_LIMIT_REACHED"


def test_files_are_private_to_their_owner(client, db, moderator, mail):
    a, _ = invited_and_accepted(client, db, moderator, mail, "a@example.com")
    b, _ = invited_and_accepted(client, db, moderator, mail, "b@example.com")
    fid = upload(client, a).json()["data"]["id"]
    assert client.get(f"{P}/files/{fid}/content", headers=hdr(b)).status_code == 404
    assert client.delete(f"{P}/files/{fid}", headers=hdr(b)).status_code == 404
    assert client.get(f"{P}/files/{fid}/content", headers=hdr(a)).status_code == 200


def test_delete_own_credential_in_draft_erases_bytes(client, db, moderator, mail):
    user, _ = invited_and_accepted(client, db, moderator, mail)
    fid = upload(client, user).json()["data"]["id"]
    key = db.get(MarketplaceFile, uuid.UUID(fid)).storage_key
    assert client.delete(f"{P}/files/{fid}", headers=hdr(user)).status_code == 200
    assert get_storage().read_private(key) is None
    assert client.get(f"{P}/files/{fid}/content", headers=hdr(user)).status_code == 404


# ---------------------------------------------------------------- portfolio


def test_portfolio_crud_and_limits(client, db, moderator, mail):
    user, _ = invited_and_accepted(client, db, moderator, mail, category="Design")
    h = hdr(user)
    assert client.post(f"{P}/portfolio", json={"title": "Site"}, headers=h).status_code == 422
    assert (
        client.post(
            f"{P}/portfolio", json={"title": "Site", "url": "javascript:alert(1)"}, headers=h
        ).status_code
        == 422
    )
    item = client.post(
        f"{P}/portfolio",
        json={"title": "Brand site", "url": "https://work.example.test"},
        headers=h,
    )
    assert item.status_code == 201
    iid = item.json()["data"]["id"]
    assert item.json()["data"]["position"] == 0
    upd = client.patch(
        f"{P}/portfolio/{iid}", json={"title": "Brand site v2", "position": 3}, headers=h
    )
    assert upd.status_code == 200 and upd.json()["data"]["title"] == "Brand site v2"
    assert client.patch(f"{P}/portfolio/{iid}", json={"title": None}, headers=h).status_code == 422
    pf = upload(client, user, PNG, "p.png", "image/png", "portfolio").json()["data"]["id"]
    with_file = client.post(f"{P}/portfolio", json={"title": "Poster", "file_id": pf}, headers=h)
    assert with_file.status_code == 201
    cred = upload(client, user).json()["data"]["id"]
    wrong = client.post(f"{P}/portfolio", json={"title": "X", "file_id": cred}, headers=h)
    assert wrong.status_code == 422
    assert client.delete(f"{P}/portfolio/{iid}", headers=h).status_code == 200
    assert client.delete(f"{P}/portfolio/{iid}", headers=h).status_code == 404
    assert len(client.get(f"{P}/me", headers=h).json()["data"]["portfolio"]) == 1


def test_portfolio_belongs_to_one_expert(client, db, moderator, mail):
    a, _ = invited_and_accepted(client, db, moderator, mail, "a@example.com", "Design")
    b, _ = invited_and_accepted(client, db, moderator, mail, "b@example.com", "Design")
    iid = client.post(
        f"{P}/portfolio", json={"title": "Mine", "url": "https://a.example.test"}, headers=hdr(a)
    ).json()["data"]["id"]
    assert (
        client.patch(f"{P}/portfolio/{iid}", json={"title": "Stolen"}, headers=hdr(b)).status_code
        == 404
    )
    assert client.delete(f"{P}/portfolio/{iid}", headers=hdr(b)).status_code == 404


# ---------------------------------------------------------------- submit


def test_submit_lists_what_is_missing(client, db, moderator, mail):
    user, _ = invited_and_accepted(client, db, moderator, mail)
    r = client.post(f"{P}/submit", headers=hdr(user))
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "SUBMISSION_INCOMPLETE"
    assert {e["field"] for e in err["field_errors"]} >= {"headline", "bio", "credentials"}


def test_non_regulated_categories_need_a_portfolio_item(client, db, moderator, mail):
    user, _ = invited_and_accepted(client, db, moderator, mail, category="Design")
    fill_profile(client, user, category="Design")
    upload(client, user)
    err = client.post(f"{P}/submit", headers=hdr(user)).json()["error"]
    assert [e["field"] for e in err["field_errors"]] == ["portfolio"]
    client.post(
        f"{P}/portfolio", json={"title": "W", "url": "https://w.example.test"}, headers=hdr(user)
    )
    assert client.post(f"{P}/submit", headers=hdr(user)).status_code == 200


def test_submit_moves_to_review_locks_the_profile_and_emails(client, db, moderator, mail):
    user, pid = ready_to_submit(client, db, moderator, mail)
    mail.sent.clear()
    r = client.post(f"{P}/submit", headers=hdr(user))
    assert r.status_code == 200 and r.json()["data"]["status"] == "in_review"
    assert mail.sent[0].to == user.email and "received" in mail.sent[0].subject.lower()
    assert "marketplace.provider.submitted" in audit_actions(db, pid)
    h = hdr(user)
    locked = client.patch(f"{P}/me", json={"headline": "Changed mid-review"}, headers=h)
    assert locked.status_code == 409 and locked.json()["error"]["code"] == "PROFILE_LOCKED"
    assert upload(client, user).status_code == 409
    fid = client.get(f"{P}/me", headers=h).json()["data"]["files"][0]["id"]
    d = client.delete(f"{P}/files/{fid}", headers=h)
    assert d.status_code == 409 and d.json()["error"]["code"] == "CREDENTIALS_LOCKED"
    again = client.post(f"{P}/submit", headers=h)
    assert again.status_code == 409 and again.json()["error"]["code"] == "INVALID_STATE"


# ---------------------------------------------------------------- vetting: checklist


def test_queue_shows_the_application_with_files_and_history(client, db, moderator, mail):
    user, pid = in_review(client, db, moderator, mail)
    q = client.get(f"{A}/providers?status=in_review", headers=hdr(moderator)).json()["data"]
    assert [p["id"] for p in q["providers"]] == [pid]
    d = client.get(f"{A}/providers/{pid}", headers=hdr(moderator)).json()["data"]
    assert d["required_checks"] == ["id", "licence", "references"] and d["references_required"] == 2
    assert d["missing_checks"] == ["id", "licence", "references"]
    assert len(d["files"]) == 1 and d["files"][0]["purpose"] == "credential"
    assert {h["action"] for h in d["history"]} >= {
        "marketplace.provider.submitted",
        "marketplace.provider.invite_accepted",
    }


def test_checklist_rules(client, db, moderator, mail):
    _, pid = in_review(client, db, moderator, mail)
    h = hdr(moderator)
    url = f"{A}/providers/{pid}/vetting"
    no_expiry = client.put(url, json={"checks": {"licence": {"done": True}}}, headers=h)
    assert no_expiry.status_code == 422
    past = client.put(
        url,
        json={"checks": {"licence": {"done": True, "expires_on": "2020-01-01"}}},
        headers=h,
    )
    assert past.status_code == 422
    n_a = client.put(url, json={"checks": {"portfolio": {"done": True}}}, headers=h)
    assert n_a.status_code == 422 and "doesn't apply" in n_a.json()["error"]["message"]
    no_count = client.put(url, json={"checks": {"references": {"done": True}}}, headers=h)
    assert no_count.status_code == 422
    assert client.put(url, json={"checks": {}}, headers=h).status_code == 422
    assert client.put(url, json={"checks": {"bogus": {"done": True}}}, headers=h).status_code == 422


def test_ticking_and_unticking_updates_missing_and_badge(client, db, moderator, mail):
    _, pid = in_review(client, db, moderator, mail)
    d = tick_all_legal(client, moderator, pid)
    assert d["missing_checks"] == []
    assert d["badge"] == {"vetted": False, "checks": []}  # nothing is claimed before approval
    assert d["badge_preview"] == [
        "Identity verified",
        "Professional licence verified",
        "2 client references checked",
    ]
    assert (
        d["checks"]["id"]["by"] == str(moderator.id)
        and d["checks"]["id"]["note"] == "NIN slip seen"
    )
    d2 = client.put(
        f"{A}/providers/{pid}/vetting",
        json={"checks": {"licence": {"done": False}}},
        headers=hdr(moderator),
    ).json()["data"]
    assert (
        d2["missing_checks"] == ["licence"]
        and "Professional licence verified" not in d2["badge_preview"]
    )
    assert d2["licence_expires_on"] is None
    assert d2["badge"]["vetted"] is False  # ticks alone never make an expert Vetted


def test_one_reference_is_not_enough_for_a_regulated_category(client, db, moderator, mail):
    _, pid = in_review(client, db, moderator, mail)
    h = hdr(moderator)
    client.put(
        f"{A}/providers/{pid}/vetting",
        json={
            "checks": {
                "id": {"done": True},
                "licence": {
                    "done": True,
                    "expires_on": (date.today() + timedelta(days=90)).isoformat(),
                },
                "references": {"done": True, "count": 1},
            }
        },
        headers=h,
    )
    r = staff_post(client, moderator, f"/providers/{pid}/approve")
    assert r.status_code == 422 and r.json()["error"]["code"] == "VETTING_INCOMPLETE"
    assert "references" in r.json()["error"]["message"]


def test_checklist_is_unavailable_for_a_draft(client, db, moderator, mail):
    _, pid = invited_and_accepted(client, db, moderator, mail)
    r = client.put(
        f"{A}/providers/{pid}/vetting",
        json={"checks": {"id": {"done": True}}},
        headers=hdr(moderator),
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATE"


# ---------------------------------------------------------------- vetting: decisions


def test_approve_requires_the_checklist_reason_and_a_fresh_code(client, db, moderator, mail):
    user, pid = in_review(client, db, moderator, mail)
    blocked = staff_post(client, moderator, f"/providers/{pid}/approve")
    assert blocked.status_code == 422 and blocked.json()["error"]["code"] == "VETTING_INCOMPLETE"
    tick_all_legal(client, moderator, pid)
    h = hdr(moderator)
    url = f"{A}/providers/{pid}/approve"
    assert client.post(url, json={"reason": REASON}, headers=h).status_code == 422  # no code
    short = client.post(url, json={"reason": "ok", "totp_code": "123456"}, headers=h)
    assert short.status_code == 422
    bad = client.post(url, json={"reason": REASON, "totp_code": "000000"}, headers=h)
    assert bad.status_code == 403 and bad.json()["error"]["code"] == "STEP_UP_INVALID"
    assert db.get(MarketplaceProvider, uuid.UUID(pid)).status == ProviderStatus.in_review
    mail.sent.clear()
    ok = staff_post(client, moderator, f"/providers/{pid}/approve")
    assert ok.status_code == 200, ok.text
    d = ok.json()["data"]
    assert d["status"] == "approved" and d["vetted_at"] and d["badge"]["vetted"] is True
    assert d["badge"]["checks"][0] == "Identity verified" and len(d["badge"]["checks"]) == 3
    assert "approved" in mail.sent[0].subject.lower() and mail.sent[0].to == user.email
    row = db.get(MarketplaceProvider, uuid.UUID(pid))
    assert row.vetted_by == moderator.id
    assert "marketplace.provider.approved" in audit_actions(db, pid)
    ev = db.query(AuditLog).filter_by(action="marketplace.provider.approved").one()
    assert ev.reason == REASON and ev.details["from"] == "in_review"


def test_approved_expert_keeps_editing_but_not_their_category(client, db, moderator, mail):
    user, pid = approved(client, db, moderator, mail)
    h = hdr(user)
    assert (
        client.patch(f"{P}/me", json={"headline": "Now senior counsel"}, headers=h).status_code
        == 200
    )
    r = client.patch(f"{P}/me", json={"category": "Design"}, headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "CATEGORY_LOCKED"
    same = client.patch(f"{P}/me", json={"category": "Legal"}, headers=h)
    assert same.status_code == 200
    assert upload(client, user).status_code == 201  # renewals
    fid = client.get(f"{P}/me", headers=h).json()["data"]["files"][0]["id"]
    assert client.delete(f"{P}/files/{fid}", headers=h).status_code == 409  # evidence is kept


def test_request_changes_round_trip(client, db, moderator, mail):
    user, pid = in_review(client, db, moderator, mail)
    mail.sent.clear()
    short = client.post(
        f"{A}/providers/{pid}/request-changes",
        json={"message": "too short"},
        headers=hdr(moderator),
    )
    assert short.status_code == 422
    msg = "The licence scan is blurry, please upload a clearer copy of both sides."
    r = client.post(
        f"{A}/providers/{pid}/request-changes", json={"message": msg}, headers=hdr(moderator)
    )
    assert r.status_code == 200 and r.json()["data"]["status"] == "changes_requested"
    assert msg in mail.sent[0].html
    h = hdr(user)
    me = client.get(f"{P}/me", headers=h).json()["data"]
    assert me["review_message"] == msg and me["can_edit_profile"] and me["can_submit"]
    assert (
        client.patch(f"{P}/me", json={"headline": "Fixed headline"}, headers=h).status_code == 200
    )
    assert client.post(f"{P}/submit", headers=h).status_code == 200
    me2 = client.get(f"{P}/me", headers=h).json()["data"]
    assert me2["status"] == "in_review" and me2["review_message"] is None


def test_request_changes_html_escapes_staff_text(client, db, moderator, mail):
    _, pid = in_review(client, db, moderator, mail)
    mail.sent.clear()
    client.post(
        f"{A}/providers/{pid}/request-changes",
        json={"message": "Please fix <script>alert(1)</script> in the bio."},
        headers=hdr(moderator),
    )
    assert "<script>" not in mail.sent[0].html and "&lt;script&gt;" in mail.sent[0].html


def test_reject_erases_credentials_and_tells_the_expert(client, db, moderator, mail):
    user, pid = in_review(client, db, moderator, mail)
    fid = client.get(f"{P}/me", headers=hdr(user)).json()["data"]["files"][0]["id"]
    key = db.get(MarketplaceFile, uuid.UUID(fid)).storage_key
    mail.sent.clear()
    bad = staff_post(client, moderator, f"/providers/{pid}/reject", template="nope")
    assert bad.status_code == 422
    r = staff_post(
        client,
        moderator,
        f"/providers/{pid}/reject",
        template="licence_not_verified",
        message="The bar number did not match the roll.",
    )
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["status"] == "rejected" and d["files"] == []
    assert get_storage().read_private(key) is None
    assert escape("couldn't verify your professional licence") in mail.sent[0].html
    assert "marketplace.provider.files_erased" in audit_actions(db, pid)
    after = client.get(f"{P}/me", headers=hdr(user))
    assert after.status_code == 403 and after.json()["error"]["code"] == "PROVIDER_REJECTED"
    assert client.get(f"{P}/status", headers=hdr(user)).json()["data"]["status"] == "rejected"


def test_suspend_and_unsuspend(client, db, moderator, mail):
    user, pid = approved(client, db, moderator, mail)
    mail.sent.clear()
    r = staff_post(client, moderator, f"/providers/{pid}/suspend", reason="Complaint 4821 upheld")
    assert r.status_code == 200 and r.json()["data"]["status"] == "suspended"
    assert r.json()["data"]["suspended_reason"] == "Complaint 4821 upheld"
    assert mail.sent and "paused" in mail.sent[0].subject.lower()
    h = hdr(user)
    frozen = client.patch(f"{P}/me", json={"headline": "Still here"}, headers=h)
    assert frozen.status_code == 403 and frozen.json()["error"]["code"] == "PROVIDER_SUSPENDED"
    assert client.get(f"{P}/me", headers=h).status_code == 200  # can still sign in and read
    back = staff_post(client, moderator, f"/providers/{pid}/unsuspend")
    assert back.status_code == 200 and back.json()["data"]["status"] == "approved"
    assert back.json()["data"]["suspended_reason"] is None


def test_unsuspend_needs_a_valid_licence(client, db, moderator, mail):
    _, pid = approved(client, db, moderator, mail)
    staff_post(client, moderator, f"/providers/{pid}/suspend")
    row = db.get(MarketplaceProvider, uuid.UUID(pid))
    row.vetting_json = {
        **row.vetting_json,
        "checks": {
            **row.vetting_json["checks"],
            "licence": {**row.vetting_json["checks"]["licence"], "expires_on": "2020-01-01"},
        },
    }
    db.flush()
    r = staff_post(client, moderator, f"/providers/{pid}/unsuspend")
    assert r.status_code == 422 and r.json()["error"]["code"] == "VETTING_INCOMPLETE"


@pytest.mark.parametrize(
    ("setup_status", "action", "extra"),
    [
        (ProviderStatus.invited, "approve", {}),
        (ProviderStatus.applied, "reject", {"template": "other"}),
        (ProviderStatus.approved, "reject", {"template": "other"}),
        (ProviderStatus.rejected, "approve", {}),
        (ProviderStatus.in_review, "suspend", {}),
        (ProviderStatus.in_review, "unsuspend", {}),
        (ProviderStatus.approved, "approve", {}),
        (ProviderStatus.suspended, "suspend", {}),
    ],
)
def test_state_machine_refuses_illegal_transitions(
    client, db, moderator, setup_status, action, extra
):
    row = MarketplaceProvider(
        email=f"{uuid.uuid4().hex[:6]}@example.com",
        status=setup_status,
        source="invite",
        category="Design",
        categories=[],
        languages=[],
        vetting_json={},
    )
    db.add(row)
    db.flush()
    r = staff_post(client, moderator, f"/providers/{row.id}/{action}", **extra)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "INVALID_STATE"
    db.refresh(row)
    assert row.status == setup_status


def test_request_changes_only_from_review(client, db, moderator, mail):
    _, pid = approved(client, db, moderator, mail)
    r = client.post(
        f"{A}/providers/{pid}/request-changes",
        json={"message": "Please send updated documents."},
        headers=hdr(moderator),
    )
    assert r.status_code == 409


def test_unknown_provider_404_does_not_spend_the_authenticator_code(client, db, moderator):
    ghost = uuid.uuid4()
    code = pyotp.TOTP(moderator.test_secret).now()
    r = client.post(
        f"{A}/providers/{ghost}/approve",
        json={"reason": REASON, "totp_code": code},
        headers=hdr(moderator),
    )
    assert r.status_code == 404 and r.json()["error"]["code"] == "PROVIDER_NOT_FOUND"
    assert not get_redis().exists(f"staff_stepup_used:{moderator.id}:{code}")
    assert client.get(f"{A}/providers/{ghost}", headers=hdr(moderator)).status_code == 404


def test_admin_decisions_extra_fields_rejected(client, db, moderator):
    r = client.post(
        f"{A}/providers/{uuid.uuid4()}/suspend",
        json={"reason": REASON, "totp_code": "123456", "status": "approved"},
        headers=hdr(moderator),
    )
    assert r.status_code == 422


# ---------------------------------------------------------------- audited file reads


def test_staff_credential_reads_are_audited(client, db, moderator, mail):
    user, pid = in_review(client, db, moderator, mail)
    fid = client.get(f"{P}/me", headers=hdr(user)).json()["data"]["files"][0]["id"]
    r = client.get(f"{A}/providers/{pid}/files/{fid}", headers=hdr(moderator))
    assert r.status_code == 200 and r.content == PDF
    assert r.headers["x-content-type-options"] == "nosniff"
    ev = db.query(AuditLog).filter_by(action="marketplace.file.viewed").one()
    assert ev.actor_user_id == moderator.id and ev.details["credential"] is True
    assert ev.details["provider_id"] == pid and ev.entity_id == uuid.UUID(fid)


def test_staff_cannot_reach_another_experts_file_through_this_one(client, db, moderator, mail):
    a, pid_a = in_review(client, db, moderator, mail)
    b, _ = invited_and_accepted(client, db, moderator, mail, "other@example.com")
    fid_b = upload(client, b).json()["data"]["id"]
    r = client.get(f"{A}/providers/{pid_a}/files/{fid_b}", headers=hdr(moderator))
    assert r.status_code == 404
    assert db.query(AuditLog).filter_by(action="marketplace.file.viewed").count() == 0


# ---------------------------------------------------------------- queue, counts


def test_counts_filter_and_pagination(client, db, moderator, mail):
    for i in range(3):
        invite(client, moderator, f"expert{i}@example.com", category="Design")
    h = hdr(moderator)
    c = client.get(f"{A}/providers/counts", headers=h).json()["data"]
    assert c["by_status"]["invited"] == 3 and set(c["by_status"]) == {
        s.value for s in ProviderStatus
    }
    page1 = client.get(f"{A}/providers?limit=2", headers=h).json()
    assert len(page1["data"]["providers"]) == 2 and page1["meta"]["next_cursor"]
    page2 = client.get(
        f"{A}/providers?limit=2&cursor={page1['meta']['next_cursor']}", headers=h
    ).json()
    ids = [p["id"] for p in page1["data"]["providers"] + page2["data"]["providers"]]
    assert len(ids) == 3 == len(set(ids))
    assert (
        client.get(f"{A}/providers?query=expert1", headers=h).json()["data"]["providers"][0][
            "email"
        ]
        == "expert1@example.com"
    )
    assert client.get(f"{A}/providers?category=Legal", headers=h).json()["data"]["providers"] == []
    assert client.get(f"{A}/providers?status=bogus", headers=h).status_code == 422


def test_licence_expiry_is_flagged_30_days_ahead(client, db, moderator, mail):
    _, pid = approved(client, db, moderator, mail)
    row = db.get(MarketplaceProvider, uuid.UUID(pid))
    row.licence_expires_on = date.today() + timedelta(days=20)
    db.flush()
    d = client.get(f"{A}/providers/{pid}", headers=hdr(moderator)).json()["data"]
    assert d["licence_expiring"] is True
    assert (
        client.get(f"{A}/providers/counts", headers=hdr(moderator)).json()["data"][
            "licence_expiring"
        ]
        == 1
    )


# ---------------------------------------------------------------- email failure never breaks the action


def test_a_failing_mail_backend_does_not_undo_the_action(client, db, moderator, monkeypatch):
    class Boom:
        def send(self, msg):
            raise RuntimeError("smtp down")

    monkeypatch.setattr(mp_emails, "get_email_sender", lambda: Boom())
    r = invite(client, moderator)
    assert r.status_code == 201 and r.json()["data"]["undelivered"] == ["ngozi@example.com"]
