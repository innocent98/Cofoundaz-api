"""Live: Marketplace slice A. Staff invite an expert; the expert accepts (wrong account, replay
and expiry refused), fills in a profile and uploads a credential; Admin works the vetting queue
(checklist, request changes, approve, suspend); a second expert is rejected and their documents are
erased. Responses go to e2e/_captures/marketplace_providers/ and are the verbatim source of
docs/fe-integration-guide-marketplace-providers.md.

One real server on a database every other journey shares, so nothing here asserts exact counts.
"""

import os
import subprocess
import sys
import time
from datetime import UTC, date, datetime, timedelta

import httpx
import pyotp

A = "/api/v1/admin/marketplace"
S = "/api/v1/super-admin"
P = "/api/v1/provider"
G = "marketplace_providers"
PDF = b"%PDF-1.4\n%placeholder credential scan\n"
BIO = "Fifteen years advising founders on contracts, shareholder agreements and fundraising."


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


class Authenticator:
    """A phone's authenticator app: a code not yet spent on a step-up (3 fit the ~90 s window,
    each works once), waiting for the next one when all are used."""

    def __init__(self, secret: str) -> None:
        self.totp = pyotp.TOTP(secret)
        self.used: set[str] = set()

    def fresh(self) -> str:
        for _ in range(80):
            now = datetime.now(UTC)
            for k in (0, -1, 1):
                c = self.totp.at(now + timedelta(seconds=30 * k))
                if c not in self.used:
                    self.used.add(c)
                    return c
            time.sleep(1)
        raise AssertionError("no unused authenticator code became available")


def _enroll(http: httpx.Client, u: dict) -> Authenticator:
    login = http.post("/api/v1/auth/login", json=u).json()["data"]
    auth = _auth(login["access_token"])
    secret = http.post("/api/v1/auth/mfa/totp/setup", headers=auth).json()["data"]["secret"]
    r = http.post(
        "/api/v1/auth/mfa/totp/verify", json={"code": pyotp.TOTP(secret).now()}, headers=auth
    )
    assert r.status_code == 200, r.text
    return Authenticator(secret)


def _sign_in(http: httpx.Client, u: dict, app: Authenticator) -> dict:
    gate = http.post("/api/v1/auth/login", json=u).json()["data"]
    r = http.post(
        "/api/v1/auth/mfa/challenge",
        json={"mfa_ticket": gate["mfa_ticket"], "code": app.totp.now()},
    )
    assert r.status_code == 200, r.text
    return _auth(r.json()["data"]["access_token"])


def _login(http: httpx.Client, u: dict) -> dict:
    return _auth(http.post("/api/v1/auth/login", json=u).json()["data"]["access_token"])


def test_marketplace_providers_journey(
    http: httpx.Client, make_verified_user, mailbox, capture, capture_json
):
    # ---- staff: a Super Admin makes a moderator -----------------------------------------------
    sa_user = make_verified_user(http)
    sa_app = _enroll(http, sa_user)
    run = subprocess.run(
        [sys.executable, "-m", "app.scripts.create_super_admin", sa_user["email"]],
        capture_output=True,
        text=True,
        env=os.environ,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    sa = _sign_in(http, sa_user, sa_app)
    mod_user, sup_user = make_verified_user(http), make_verified_user(http)
    apps, heads = {}, {}
    for key, user, scopes in (("mod", mod_user, ["moderation"]), ("sup", sup_user, ["support"])):
        apps[key] = _enroll(http, user)
        r = http.post(
            f"{S}/staff",
            json={
                "email": user["email"],
                "role": "admin",
                "scopes": scopes,
                "reason": "Marketplace vetting rota",
                "totp_code": sa_app.fresh(),
            },
            headers=sa,
        )
        assert r.status_code == 201, r.text
        heads[key] = _sign_in(http, user, apps[key])
    mod, sup = heads["mod"], heads["sup"]
    mod_app = apps["mod"]

    def step_up(extra: dict) -> dict:
        return {
            "reason": "Checked the documents against the rubric",
            "totp_code": mod_app.fresh(),
            **extra,
        }

    # ---- guards ---------------------------------------------------------------------------------
    r = http.get(f"{A}/providers", headers=sup)
    assert r.status_code == 403
    capture(G, "admin_wrong_scope_403", r)
    r = http.get(f"{A}/providers")
    assert r.status_code == 404
    capture(G, "admin_anonymous_404", r)
    stranger = make_verified_user(http)
    s_auth = _login(http, stranger)
    r = http.get(f"{P}/me", headers=s_auth)
    assert r.status_code == 403 and r.json()["error"]["code"] == "NOT_A_PROVIDER"
    capture(G, "provider_me_not_a_provider_403", r)
    r = http.get(f"{P}/me")
    assert r.status_code == 401
    capture(G, "provider_me_anonymous_401", r)
    r = http.get(f"{P}/status", headers=s_auth)
    assert r.status_code == 200 and r.json()["data"]["is_provider"] is False
    capture(G, "status_not_a_provider", r)
    r = http.post(f"{P}/apply", json={"accept_terms": True}, headers=s_auth)
    assert r.status_code == 403 and r.json()["error"]["code"] == "APPLICATIONS_CLOSED"
    capture(G, "apply_closed_403", r)

    r = http.post(f"{A}/providers/invite", json={"email": mod_user["email"]}, headers=mod)
    assert r.status_code == 422 and r.json()["error"]["code"] == "CANNOT_ACT_ON_SELF"
    capture(G, "admin_invite_own_email_422", r)

    # ---- invite ---------------------------------------------------------------------------------
    r = http.get(f"{A}/providers/counts", headers=mod)
    assert r.status_code == 200
    capture(G, "admin_counts", r)
    expert = make_verified_user(http)
    r = http.post(
        f"{A}/providers/invite",
        json={"email": expert["email"], "category": "Legal", "display_name": "Ngozi Eze"},
        headers=mod,
    )
    assert r.status_code == 201, r.text
    capture(G, "admin_invite_201", r)
    pid = r.json()["data"]["provider"]["id"]
    r = http.post(f"{A}/providers/invite", json={"email": expert["email"]}, headers=mod)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ALREADY_INVITED"
    capture(G, "admin_invite_duplicate_409", r)
    r = http.post(f"{A}/providers/invite", json={"email": "nope"}, headers=mod)
    assert r.status_code == 422
    capture(G, "admin_invite_bad_email_422", r)
    mail = mailbox.latest_for(expert["email"])
    assert mail and "/provider/invite/" in mail["html"]
    capture_json(G, "email_invite", mail)
    token = mailbox.latest_token_for(expert["email"], subject_contains="invited")

    # resend rotates the link: the first one stops working
    r = http.post(f"{A}/providers/{pid}/resend-invite", headers=mod)
    assert r.status_code == 200
    capture(G, "admin_resend_invite", r)
    new_token = mailbox.latest_token_for(expert["email"], subject_contains="invited")
    assert new_token != token
    r = http.get(f"{P}/invites/{token}")
    assert r.status_code == 404
    capture(G, "invite_preview_replaced_link_404", r)
    token = new_token
    r = http.get(f"{P}/invites/{token}")
    assert r.status_code == 200
    capture(G, "invite_preview_200", r)

    # ---- accept: wrong account, right account, replay -------------------------------------------
    r = http.post(
        f"{P}/invites/accept", json={"token": token, "accept_terms": True}, headers=s_auth
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "INVITE_EMAIL_MISMATCH"
    capture(G, "invite_accept_wrong_account_403", r)
    e_auth = _login(http, expert)
    r = http.post(
        f"{P}/invites/accept", json={"token": token, "accept_terms": False}, headers=e_auth
    )
    assert r.status_code == 422
    capture(G, "invite_accept_terms_not_accepted_422", r)
    r = http.post(
        f"{P}/invites/accept", json={"token": token, "accept_terms": True}, headers=e_auth
    )
    assert r.status_code == 200, r.text
    capture(G, "invite_accept_200", r)
    r = http.post(
        f"{P}/invites/accept", json={"token": token, "accept_terms": True}, headers=e_auth
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "TOKEN_INVALID"
    capture(G, "invite_accept_replay_400", r)
    assert http.get(f"{P}/invites/{token}").status_code == 404

    # ---- the expert builds a profile ------------------------------------------------------------
    r = http.get(f"{P}/status", headers=e_auth)
    capture(G, "status_invited", r)
    r = http.post(f"{P}/submit", headers=e_auth)
    assert r.status_code == 422 and r.json()["error"]["code"] == "SUBMISSION_INCOMPLETE"
    capture(G, "submit_incomplete_422", r)
    r = http.patch(f"{P}/me", json={"timezone": "Mars/Base"}, headers=e_auth)
    assert r.status_code == 422
    capture(G, "profile_bad_timezone_422", r)
    r = http.patch(f"{P}/me", json={"status": "approved"}, headers=e_auth)
    assert r.status_code == 422
    capture(G, "profile_unknown_field_422", r)
    r = http.patch(
        f"{P}/me",
        json={
            "display_name": "Ngozi Eze",
            "headline": "Corporate lawyer for early-stage startups",
            "bio": BIO,
            "category": "Legal",
            "languages": ["English", "Igbo"],
            "timezone": "Africa/Lagos",
            "location": "Lagos, Nigeria",
        },
        headers=e_auth,
    )
    assert r.status_code == 200, r.text
    capture(G, "profile_patch_200", r)
    r = http.patch(f"{P}/me", json={"categories": ["Accounting"]}, headers=e_auth)
    assert r.status_code == 422 and r.json()["error"]["code"] == "REGULATED_CATEGORY"
    capture(G, "profile_regulated_secondary_422", r)

    # uploads: bytes are sniffed, not trusted
    r = http.post(
        f"{P}/files",
        data={"purpose": "credential"},
        files={"file": ("licence.pdf", b"MZ\x90\x00" + b"\x00" * 64, "application/pdf")},
        headers=e_auth,
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "FILE_CONTENT_MISMATCH"
    capture(G, "upload_content_mismatch_422", r)
    r = http.post(
        f"{P}/files",
        data={"purpose": "credential"},
        files={"file": ("notes.txt", b"hello", "text/plain")},
        headers=e_auth,
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"
    capture(G, "upload_unsupported_type_422", r)
    r = http.post(
        f"{P}/files",
        data={"purpose": "credential"},
        files={"file": ("bar-licence.pdf", PDF, "application/pdf")},
        headers=e_auth,
    )
    assert r.status_code == 201, r.text
    capture(G, "upload_credential_201", r)
    fid = r.json()["data"]["id"]
    r = http.get(f"{P}/files/{fid}/content", headers=e_auth)
    assert r.status_code == 200 and r.content == PDF
    capture_json(
        G,
        "download_own_file_headers",
        {
            "status": r.status_code,
            "content_type": r.headers["content-type"],
            "content_disposition": r.headers["content-disposition"],
            "x_content_type_options": r.headers["x-content-type-options"],
            "cache_control": r.headers["cache-control"],
        },
    )
    r = http.get(f"{P}/files/{fid}/content", headers=s_auth)
    assert r.status_code == 403  # a non-expert never gets near it
    r = http.get(f"{P}/me", headers=e_auth)
    assert r.status_code == 200 and r.json()["data"]["submit_blockers"] == []
    capture(G, "me_ready_to_submit", r)
    r = http.post(f"{P}/submit", headers=e_auth)
    assert r.status_code == 200 and r.json()["data"]["status"] == "in_review"
    capture(G, "submit_200", r)
    r = http.patch(f"{P}/me", json={"headline": "Changed mid-review"}, headers=e_auth)
    assert r.status_code == 409 and r.json()["error"]["code"] == "PROFILE_LOCKED"
    capture(G, "profile_locked_in_review_409", r)
    capture_json(G, "email_application_received", mailbox.latest_for(expert["email"]))

    # ---- admin: the queue --------------------------------------------------------------------
    r = http.get(
        f"{A}/providers", params={"status": "in_review", "query": expert["email"]}, headers=mod
    )
    assert r.status_code == 200 and [p["id"] for p in r.json()["data"]["providers"]] == [pid]
    capture(G, "admin_queue_in_review", r)
    r = http.get(f"{A}/providers/{pid}", headers=mod)
    assert r.status_code == 200
    capture(G, "admin_provider_detail_before_vetting", r)
    r = http.get(f"{A}/providers/{pid}/files/{fid}", headers=mod)
    assert r.status_code == 200 and r.content == PDF
    capture_json(
        G,
        "admin_read_credential_headers",
        {
            "status": r.status_code,
            "content_type": r.headers["content-type"],
            "content_disposition": r.headers["content-disposition"],
        },
    )

    # approve is refused until the rubric is ticked
    r = http.post(f"{A}/providers/{pid}/approve", json=step_up({}), headers=mod)
    assert r.status_code == 422 and r.json()["error"]["code"] == "VETTING_INCOMPLETE"
    capture(G, "admin_approve_incomplete_422", r)
    r = http.put(
        f"{A}/providers/{pid}/vetting",
        json={"checks": {"licence": {"done": True}}},
        headers=mod,
    )
    assert r.status_code == 422
    capture(G, "admin_vetting_licence_needs_expiry_422", r)
    r = http.put(
        f"{A}/providers/{pid}/vetting",
        json={"checks": {"portfolio": {"done": True}}},
        headers=mod,
    )
    assert r.status_code == 422
    capture(G, "admin_vetting_check_not_applicable_422", r)
    r = http.put(
        f"{A}/providers/{pid}/vetting",
        json={
            "checks": {
                "id": {"done": True, "note": "NIN slip seen"},
                "licence": {
                    "done": True,
                    "expires_on": (date.today() + timedelta(days=365)).isoformat(),
                    "note": "Bar roll number matched",
                },
                "references": {"done": True, "count": 1},
            }
        },
        headers=mod,
    )
    assert r.status_code == 200 and r.json()["data"]["missing_checks"] == ["references"]
    capture(G, "admin_vetting_put_one_reference_200", r)

    # request changes, the expert fixes and resubmits
    r = http.post(
        f"{A}/providers/{pid}/request-changes",
        json={"message": "The licence scan is blurry; please upload a clearer copy."},
        headers=mod,
    )
    assert r.status_code == 200 and r.json()["data"]["status"] == "changes_requested"
    capture(G, "admin_request_changes_200", r)
    capture_json(G, "email_changes_requested", mailbox.latest_for(expert["email"]))
    r = http.get(f"{P}/me", headers=e_auth)
    assert r.json()["data"]["review_message"].startswith("The licence scan")
    capture(G, "me_changes_requested", r)
    r = http.post(f"{P}/submit", headers=e_auth)
    assert r.status_code == 200
    # resubmitting wipes the ticks: they were made against evidence that may have changed
    r = http.get(f"{A}/providers/{pid}", headers=mod)
    assert r.json()["data"]["checks"] == {} and r.json()["data"]["status"] == "in_review"
    capture(G, "admin_detail_after_resubmit_ticks_cleared", r)
    r = http.put(
        f"{A}/providers/{pid}/vetting",
        json={
            "checks": {
                "id": {"done": True, "note": "NIN slip seen"},
                "licence": {
                    "done": True,
                    "expires_on": (date.today() + timedelta(days=365)).isoformat(),
                    "note": "Bar roll number matched",
                },
                "references": {"done": True, "count": 2, "note": "Two founders emailed"},
            }
        },
        headers=mod,
    )
    assert r.status_code == 200 and r.json()["data"]["missing_checks"] == []
    capture(G, "admin_vetting_put_complete_200", r)

    # approve: reason too short, bad code, then the real thing
    r = http.post(
        f"{A}/providers/{pid}/approve",
        json={"reason": "ok", "totp_code": mod_app.fresh()},
        headers=mod,
    )
    assert r.status_code == 422
    capture(G, "admin_approve_reason_too_short_422", r)
    r = http.post(
        f"{A}/providers/{pid}/approve",
        json={"reason": "Checked the documents against the rubric", "totp_code": "000000"},
        headers=mod,
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "STEP_UP_INVALID"
    capture(G, "admin_approve_bad_code_403", r)
    r = http.post(f"{A}/providers/{pid}/approve", json=step_up({}), headers=mod)
    assert r.status_code == 200 and r.json()["data"]["status"] == "approved"
    capture(G, "admin_approve_200", r)
    capture_json(G, "email_approved", mailbox.latest_for(expert["email"]))
    r = http.post(f"{A}/providers/{pid}/approve", json=step_up({}), headers=mod)
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATE"
    capture(G, "admin_approve_twice_409", r)
    r = http.get(f"{P}/me", headers=e_auth)
    assert r.json()["data"]["badge"]["vetted"] is True
    capture(G, "me_approved", r)
    r = http.patch(f"{P}/me", json={"category": "Design"}, headers=e_auth)
    assert r.status_code == 409 and r.json()["error"]["code"] == "CATEGORY_LOCKED"
    capture(G, "profile_category_locked_409", r)

    # suspend / unsuspend
    r = http.post(f"{A}/providers/{pid}/suspend", json=step_up({}), headers=mod)
    assert r.status_code == 200 and r.json()["data"]["status"] == "suspended"
    capture(G, "admin_suspend_200", r)
    r = http.patch(f"{P}/me", json={"headline": "Still here"}, headers=e_auth)
    assert r.status_code == 403 and r.json()["error"]["code"] == "PROVIDER_SUSPENDED"
    capture(G, "profile_suspended_403", r)
    r = http.post(f"{A}/providers/{pid}/unsuspend", json=step_up({}), headers=mod)
    assert r.status_code == 200 and r.json()["data"]["status"] == "approved"
    capture(G, "admin_unsuspend_200", r)
    r = http.get(f"{A}/providers/{pid}", headers=mod)
    capture(G, "admin_provider_detail_approved", r)

    # ---- a second expert: rejected, documents erased ---------------------------------------------
    second = make_verified_user(http)
    r = http.post(
        f"{A}/providers/invite", json={"email": second["email"], "category": "Design"}, headers=mod
    )
    pid2 = r.json()["data"]["provider"]["id"]
    tok2 = mailbox.latest_token_for(second["email"], subject_contains="invited")
    a2 = _login(http, second)
    assert (
        http.post(
            f"{P}/invites/accept", json={"token": tok2, "accept_terms": True}, headers=a2
        ).status_code
        == 200
    )
    http.patch(
        f"{P}/me",
        json={
            "display_name": "Tolu Ade",
            "headline": "Brand and product designer",
            "bio": BIO.replace("contracts", "brand systems"),
            "category": "Design",
            "languages": ["English"],
        },
        headers=a2,
    )
    r = http.post(f"{P}/submit", headers=a2)
    assert r.status_code == 422
    assert {e["field"] for e in r.json()["error"]["field_errors"]} == {"credentials", "portfolio"}
    capture(G, "submit_incomplete_design_422", r)
    r = http.post(
        f"{P}/portfolio",
        json={"title": "Fintech rebrand", "url": "https://portfolio.example.com/fintech"},
        headers=a2,
    )
    assert r.status_code == 201
    capture(G, "portfolio_add_201", r)
    r = http.post(
        f"{P}/files",
        data={"purpose": "credential"},
        files={"file": ("id.pdf", PDF, "application/pdf")},
        headers=a2,
    )
    fid2 = r.json()["data"]["id"]
    assert http.post(f"{P}/submit", headers=a2).status_code == 200
    r = http.post(
        f"{A}/providers/{pid2}/reject",
        json=step_up({"template": "nope"}),
        headers=mod,
    )
    assert r.status_code == 422
    capture(G, "admin_reject_bad_template_422", r)
    r = http.post(
        f"{A}/providers/{pid2}/reject",
        json=step_up({"template": "incomplete_documents", "message": "The ID scan is unreadable."}),
        headers=mod,
    )
    assert r.status_code == 200 and r.json()["data"]["status"] == "rejected"
    assert r.json()["data"]["files"] == []
    capture(G, "admin_reject_200", r)
    capture_json(G, "email_rejected", mailbox.latest_for(second["email"]))
    r = http.get(f"{A}/providers/{pid2}/files/{fid2}", headers=mod)
    assert r.status_code == 404
    capture(G, "admin_read_erased_file_404", r)
    r = http.get(f"{P}/me", headers=a2)
    assert r.status_code == 403 and r.json()["error"]["code"] == "PROVIDER_REJECTED"
    capture(G, "provider_me_rejected_403", r)
    r = http.get(f"{P}/status", headers=a2)
    capture(G, "status_rejected", r)

    # ---- audit trail --------------------------------------------------------------------------
    r = http.get(f"{A}/providers/{pid}", headers=mod)
    actions = [h["action"] for h in r.json()["data"]["history"]]
    for expected in (
        "marketplace.provider.invited",
        "marketplace.provider.invite_resent",
        "marketplace.provider.invite_accepted",
        "marketplace.provider.submitted",
        "marketplace.provider.vetting_updated",
        "marketplace.provider.changes_requested",
        "marketplace.provider.approved",
        "marketplace.provider.suspended",
        "marketplace.provider.unsuspended",
    ):
        assert expected in actions, (expected, actions)
    capture(G, "admin_provider_detail_history", r)
