"""Live Funding Hub term sheet journey (slice D): a founder types one offer in, uploads a PDF for
extraction (the job is drained in this process with a CANNED model, so the extracted numbers and
the plain-language wording are the fake's, not a real model's: the live part is the API, the queue,
PDF text extraction, validation, the fixed rules table, the confirm step, the comparison and the
audit trail), confirms corrected values, compares three offers, edits one, and hits the guard
rails. A team member joined through a real invitation proves the founder-only 403 live.

Every response goes to e2e/_captures/funding_term_sheets/ and is the verbatim source of
docs/fe-integration-guide-funding-term-sheets.md. Not live here: the 402 plan gate (the e2e server
runs with enforcement off), a real model's extraction quality, and the notification (it goes to
OTHER founders, and this workspace has one).
"""

import io
import uuid

import httpx
import pytest

from tests.deck_helpers import deck_pdf
from tests.term_helpers import SHEET_TEXT, TermLLM

F = "/api/v1/funding"
TS = f"{F}/term-sheets"
G = "funding_term_sheets"


def _onboard(c: httpx.Client, auth: dict) -> None:
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Obi"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "Placeholder Co"})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "launch"},
    )
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Raise funding"]})
    c.post("/api/v1/onboarding/complete", headers=auth)


def _join(c: httpx.Client, wh: dict, role: str, unique_email, mailbox) -> dict:
    email = unique_email(role)
    r = c.post(
        "/api/v1/team/invitations", headers=wh, json={"invites": [{"email": email, "role": role}]}
    )
    assert r.status_code == 200, r.text
    token = mailbox.latest_token_for(email, subject_contains="invited")
    pw = f"Ev-{uuid.uuid4().hex[:12]}-9"
    su = c.post(
        "/api/v1/auth/signup", json={"email": email, "password": pw, "accepted_terms": True}
    )
    assert su.status_code == 201, su.text
    c.post(
        "/api/v1/auth/verify",
        json={"token": mailbox.latest_token_for(email, subject_contains="Verify")},
    )
    access = c.post("/api/v1/auth/login", json={"email": email, "password": pw}).json()["data"][
        "access_token"
    ]
    auth = {"Authorization": f"Bearer {access}"}
    acc = c.post("/api/v1/invitations/accept", headers=auth, json={"token": token})
    assert acc.status_code == 200, acc.text
    return {**auth, "X-Workspace-Id": wh["X-Workspace-Id"]}


def _upload(c: httpx.Client, wh: dict, name: str, body: bytes, ctype="application/pdf") -> str:
    r = c.post(
        "/api/v1/documents/files",
        headers=wh,
        files={"file": (name, io.BytesIO(body), ctype)},
        data={"folder": "Funding"},
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def _drain(canned: TermLLM) -> None:
    """Run the queued jobs here, as the worker process would, with the language model faked."""
    from app.db.session import SessionLocal
    from app.platform import llm_budget
    from app.services.notifications.registry import register as register_notifications
    from app.worker import runner
    from app.worker.handlers import funding as _h  # noqa: F401  (registers the handlers)

    register_notifications()
    llm_budget.get_llm_client = lambda: canned  # type: ignore[assignment]
    db = SessionLocal()
    try:
        for _ in range(100):
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


@pytest.fixture()
def canned_llm():
    from app.platform import llm_budget

    real = llm_budget.get_llm_client
    yield TermLLM()
    llm_budget.get_llm_client = real


def test_funding_term_sheets_journey(
    base_url, make_verified_user, capture, unique_email, mailbox, canned_llm
):
    with httpx.Client(base_url=base_url, timeout=30.0) as c:
        u = make_verified_user(c)
        token = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = {"Authorization": f"Bearer {token}"}
        _onboard(c, auth)
        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wh = {**auth, "X-Workspace-Id": me["active_workspace_id"]}

        # 1. Empty state, and the table the flags come from.
        r = c.get(TS, headers=wh)
        assert r.json()["data"]["items"] == []
        capture(G, "list_empty", r)
        r = c.get(f"{TS}/rules", headers=wh)
        assert len(r.json()["data"]["rules"]) == 7
        capture(G, "rules", r)

        # 2. Typed sheets are confirmed on save. One is ordinary, one trips several rules.
        inv = c.post(f"{F}/investors", headers=wh, json={"name": "Tolu Bello", "type": "angel"})
        inv_id = inv.json()["data"]["id"]
        r = c.post(
            TS,
            headers=wh,
            json={
                "label": "Alpha Ventures",
                "investor_id": inv_id,
                "terms": {
                    "stage": "seed",
                    "pre_money_minor": 40_000_000_000,
                    "investment_minor": 10_000_000_000,
                    "option_pool_pre_bps": 1000,
                    "liq_pref_x100": 100,
                    "participating": False,
                    "investor_board_seats": 1,
                    "founder_board_seats": 2,
                    "pro_rata": "standard",
                    "exclusivity_days": 30,
                    "anti_dilution": "broad_based",
                },
            },
        )
        assert r.status_code == 201, r.text
        capture(G, "create_typed_clean", r)
        alpha = r.json()["data"]
        r = c.post(
            TS,
            headers=wh,
            json={
                "label": "Beta Capital",
                "terms": {
                    "stage": "seed",
                    "pre_money_minor": 50_000_000_000,
                    "investment_minor": 10_000_000_000,
                    "option_pool_pre_bps": 1800,
                    "liq_pref_x100": 200,
                    "participating": True,
                    "participation_cap_x100": 300,
                    "investor_board_seats": 2,
                    "pro_rata": "super",
                    "exclusivity_days": 60,
                    "anti_dilution": "full_ratchet",
                },
            },
        )
        assert r.status_code == 201
        capture(G, "create_typed_flagged", r)
        beta = r.json()["data"]
        assert beta["flag_count"] == 7
        _drain(canned_llm)  # the wording job
        r = c.get(f"{TS}/{beta['id']}", headers=wh)
        assert all(f["ai_text"] for f in r.json()["data"]["flags"])
        capture(G, "detail_flagged_with_wording", r)
        r = c.post(TS, headers=wh, json={"label": "Bad", "terms": {"liq_pref_x100": 99999}})
        assert r.status_code == 422
        capture(G, "create_invalid_422", r)

        # 3. Upload a PDF, extract, review the proposal, confirm corrected values.
        fid = _upload(c, wh, "gamma-term-sheet.pdf", deck_pdf(SHEET_TEXT))
        r = c.post(f"{TS}/extract", headers=wh, json={"file_id": fid, "investor_id": inv_id})
        assert r.status_code == 202, r.text
        capture(G, "extract_queued", r)
        gamma = r.json()["data"]["term_sheet"]
        r = c.post(f"{TS}/extract", headers=wh, json={"file_id": fid})
        assert r.status_code == 200 and r.json()["data"]["deduplicated"] is True
        capture(G, "extract_deduplicated", r)
        r = c.get(f"{TS}/{gamma['id']}", headers=wh)
        assert r.json()["data"]["status"] == "extracting"
        capture(G, "detail_extracting", r)
        r = c.post(f"{TS}/{gamma['id']}/confirm", headers=wh, json={"terms": {"stage": "seed"}})
        assert r.status_code == 409
        capture(G, "confirm_too_early_409", r)
        _drain(canned_llm)
        r = c.get(f"{TS}/{gamma['id']}", headers=wh)
        assert r.json()["data"]["status"] == "needs_confirmation"
        capture(G, "detail_needs_confirmation", r)
        proposal = r.json()["data"]["proposed"]["terms"]
        r = c.get(f"{TS}/compare?ids={alpha['id']},{gamma['id']}", headers=wh)
        assert r.status_code == 409
        capture(G, "compare_unconfirmed_409", r)
        corrected = {**proposal, "exclusivity_days": 45, "investor_board_seats": 1}
        r = c.post(
            f"{TS}/{gamma['id']}/confirm",
            headers=wh,
            json={"terms": corrected, "label": "Gamma Partners"},
        )
        assert r.status_code == 200, r.text
        capture(G, "confirm", r)
        r = c.post(f"{TS}/{gamma['id']}/confirm", headers=wh, json={"terms": corrected})
        assert r.status_code == 409
        capture(G, "confirm_twice_409", r)

        # 4. Compare three offers side by side.
        ids = ",".join([alpha["id"], beta["id"], gamma["id"]])
        r = c.get(f"{TS}/compare?ids={ids}", headers=wh)
        assert r.status_code == 200, r.text
        capture(G, "compare", r)
        r = c.get(f"{TS}/compare?ids={alpha['id']}", headers=wh)
        assert r.status_code == 422
        capture(G, "compare_one_sheet_422", r)

        # 5. List, filter by investor, edit, audit.
        r = c.get(TS, headers=wh)
        assert len(r.json()["data"]["items"]) == 3
        capture(G, "list", r)
        r = c.get(f"{TS}?investor_id={inv_id}", headers=wh)
        assert {i["label"] for i in r.json()["data"]["items"]} == {
            "Alpha Ventures",
            "Gamma Partners",
        }
        capture(G, "list_for_investor", r)
        r = c.patch(
            f"{TS}/{beta['id']}",
            headers=wh,
            json={
                "terms": {
                    **beta["terms"],
                    "liq_pref_x100": 100,
                    "participating": False,
                    "participation_cap_x100": None,
                }
            },
        )
        assert r.status_code == 200, r.text
        capture(G, "update_terms_flags_recomputed", r)
        r = c.get(f"{TS}/audit?limit=4", headers=wh)
        capture(G, "audit", r)

        # 6. Failure paths: a PDF with no text, a non-PDF.
        blank = _upload(c, wh, "scan.pdf", deck_pdf([" "], images=True))
        r = c.post(f"{TS}/extract", headers=wh, json={"file_id": blank})
        assert r.status_code == 202
        scan = r.json()["data"]["term_sheet"]
        _drain(canned_llm)
        r = c.get(f"{TS}/{scan['id']}", headers=wh)
        assert r.json()["data"]["status"] == "failed" and r.json()["data"]["error"] == "no_text"
        capture(G, "detail_failed_no_text", r)
        txt = _upload(c, wh, "notes.txt", b"just text", "text/plain")
        r = c.post(f"{TS}/extract", headers=wh, json={"file_id": txt})
        assert r.status_code == 422
        capture(G, "extract_not_a_pdf_422", r)

        # 7. The currency is fixed once a sheet states money.
        r = c.put(f"{F}/round", headers=wh, json={"currency": "NGN"})
        assert r.status_code == 200
        r = c.put(f"{F}/round", headers=wh, json={"currency": "USD"})
        assert r.status_code == 409
        capture(G, "round_currency_locked_409", r)

        # 8. A team member is refused, live.
        member = _join(c, wh, "team_member", unique_email, mailbox)
        r = c.get(TS, headers=member)
        assert r.status_code == 403
        capture(G, "team_member_403", r)
        r = c.post(TS, headers=member, json={"label": "x", "terms": {}})
        assert r.status_code == 403
        assert len(c.get(TS, headers=wh).json()["data"]["items"]) == 4

        # 9. Delete.
        r = c.delete(f"{TS}/{scan['id']}", headers=wh)
        capture(G, "delete", r)
