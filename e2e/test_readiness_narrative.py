"""Live Investor Readiness slice C journey: a founder writes a financial narrative, has a deck
reviewed, records revenue and a Funding round target, runs the narrative check, polls the job,
reads the findings, re-checks after fixing the books (claims reused, no tokens), edits the draft
(a new model call), and sees the two score signals and the history log. Also: the empty state,
roles (team member reads, cannot edit or run; mentor gets 403), validation, and both budget gates.

Jobs are drained in this process, as the worker would. The deck review uses the canned reviewer.
The claim extraction uses a SCRIPTED model by default; with NARRATIVE_E2E_REAL_LLM=1 (and
LLM_PROVIDER / LLM_API_KEY in this process's environment) it uses the real model instead, and the
assertions that depend on exact claims are relaxed to shape checks. Responses go to
e2e/_captures/readiness_narrative/ and are the verbatim source of the Narrative section of
docs/fe-integration-guide-investor-readiness.md.
"""

import io
import json
import os
import uuid
from datetime import UTC, datetime

import httpx
import pytest

# This journey drives the app with an in-process canned LLM (`tests.*_helpers`), which cannot be
# injected into a DEPLOYED server, and those helpers import app code that builds `Settings()`
# (needs SECRET_KEY, DATABASE_URL, ...) which the remote gate runner does not have. So under
# E2E_REMOTE it is SKIPPED, visibly, BEFORE the helper imports: collection never builds
# Settings, and the staging gate does not claim to have verified this journey. It runs in
# the local/CI e2e job (scripts/e2e_run.sh), where the server is in-process-compatible.
if os.environ.get("E2E_REMOTE", "").strip().lower() in {"1", "true", "yes"}:
    pytest.skip(
        "needs an in-process canned LLM; not runnable against a deployed server",
        allow_module_level=True,
    )

from tests.deck_helpers import CannedLLM, deck_pdf
from tests.narrative_helpers import CLAIMS, DRAFT

R = "/api/v1/readiness"
N = f"{R}/narrative"
REAL = os.environ.get("NARRATIVE_E2E_REAL_LLM", "").strip() in {"1", "true", "yes"}
G = "readiness_narrative_real" if REAL else "readiness_narrative"
SLIDES = [
    "Acme Pay\nSend money across Africa in seconds",
    "Problem\nSMEs lose 8% of every cross border payment to fees and delays",
    "Solution\nInstant settlement over local rails with one API",
    "Market\nTAM 12bn USD, SAM 2bn USD, SOM 150m USD (source: placeholder report)",
    "Traction\nMRR 1.6m NGN, 45 paying merchants, 12% month on month growth",
    "Business model\n1.2% per transaction, 82% gross margin",
    "Team\nTwo founders, ex payments engineers",
    "The ask\nRaising 90m NGN to hire 3 engineers and open Ghana",
]


class Router:
    """Deck reviews get the canned reviewer; claim extraction gets the script or the real model."""

    def __init__(self) -> None:
        self.deck = CannedLLM()
        self.claim_calls = 0
        self.last_usage_tokens = 0

    def complete_json(self, messages, *, schema, max_tokens):
        if "claims" not in schema.get("properties", {}):
            out = self.deck.complete_json(messages, schema=schema, max_tokens=max_tokens)
            self.last_usage_tokens = self.deck.last_usage_tokens
            return out
        self.claim_calls += 1
        if REAL:
            from app.platform.llm import get_llm_client

            client = get_llm_client()
            try:
                return client.complete_json(messages, schema=schema, max_tokens=max_tokens)
            finally:
                self.last_usage_tokens = getattr(client, "last_usage_tokens", 0)
        self.last_usage_tokens = 6100
        return json.loads(json.dumps(CLAIMS))


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
    pw = "Member-" + email.split("@")[0][-8:] + "-9"
    c.post("/api/v1/auth/signup", json={"email": email, "password": pw})
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


def _drain(llm: Router) -> None:
    from app.db.session import SessionLocal
    from app.platform import llm_budget
    from app.services.notifications.registry import register as register_notifications
    from app.services.readiness.triggers import register as register_readiness
    from app.worker import runner
    from app.worker.handlers import readiness as handlers

    register_notifications()
    register_readiness()
    runner.register_handler("ai.readiness.deck_review", handlers.handle_deck_review)
    runner.register_handler("ai.readiness.narrative_check", handlers.handle_narrative_check)
    llm_budget.get_llm_client = lambda: llm  # type: ignore[assignment]
    db = SessionLocal()
    try:
        for _ in range(100):
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def _set_usage(workspace_id: str, tokens: int) -> None:
    """Set today's AI usage for the workspace directly (the gates read it from the database)."""
    from sqlalchemy import text

    from app.db.session import SessionLocal

    with SessionLocal() as db:
        db.execute(
            text(
                "INSERT INTO llm_usage_daily (id, startup_id, usage_date, tokens_used, created_at, "
                "updated_at) VALUES (:id, :s, :d, :t, now(), now()) ON CONFLICT (startup_id, "
                "usage_date) DO UPDATE SET tokens_used = :t"
            ),
            {
                "id": str(uuid.uuid4()),
                "s": workspace_id,
                "d": datetime.now(UTC).date(),
                "t": tokens,
            },
        )
        db.commit()


@pytest.fixture()
def router():
    from app.platform import llm_budget

    real = llm_budget.get_llm_client
    llm = Router()
    yield llm
    llm_budget.get_llm_client = real


def _txn(c, wh, amount_minor, direction, category):
    r = c.post(
        "/api/v1/finance/transactions",
        headers=wh,
        json={
            "date": datetime.now(UTC).date().isoformat(),
            "description": f"{category} placeholder",
            "category": category,
            "amount_minor": amount_minor,
            "currency": "NGN",
            "direction": direction,
        },
    )
    assert r.status_code == 200, r.text


def test_narrative_journey(base_url, make_verified_user, capture, unique_email, mailbox, router):
    with httpx.Client(base_url=base_url, timeout=60.0) as c:
        u = make_verified_user(c)
        token = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = {"Authorization": f"Bearer {token}"}
        _onboard(c, auth)
        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        ws_id = me["active_workspace_id"]
        wh = {**auth, "X-Workspace-Id": ws_id}
        tm = _join(c, wh, "team_member", unique_email, mailbox)
        mentor = _join(c, wh, "mentor", unique_email, mailbox)

        # 1. Empty state: no draft, no deck, nothing to check.
        empty = c.get(N, headers=wh)
        assert empty.status_code == 200 and empty.json()["data"]["can_check"] is False
        capture(G, "draft_empty", empty)
        nothing = c.post(f"{N}/check", headers=wh)
        assert nothing.status_code == 422 and nothing.json()["error"]["code"] == "NOTHING_TO_CHECK"
        capture(G, "check_nothing_422", nothing)
        none = c.get(f"{N}/checks/latest", headers=wh)
        assert none.json()["data"] == {"check": None}
        capture(G, "latest_none", none)

        # 2. The real numbers: a reviewed deck, revenue in the books, a Funding round target.
        up = c.post(
            "/api/v1/documents/files",
            headers=wh,
            files={
                "file": ("Acme Pay seed deck.pdf", io.BytesIO(deck_pdf(SLIDES)), "application/pdf")
            },
            data={"folder": "Pitch Deck"},
        )
        assert up.status_code == 201, up.text
        rv = c.post(f"{R}/deck-reviews", headers=wh, json={"file_id": up.json()["data"]["id"]})
        assert rv.status_code == 202, rv.text
        _drain(router)
        _txn(c, wh, 480_000_000, "in", "Revenue")  # NGN 4.8M this month = 1.6M a month
        rnd = c.put(
            "/api/v1/funding/round",
            headers=wh,
            json={
                "name": "Seed",
                "status": "open",
                "currency": "NGN",
                "target_minor": 9_000_000_000,
            },
        )
        assert rnd.status_code == 200, rnd.text

        # 3. Write the draft; validation; roles.
        saved = c.put(N, headers=wh, json={"body": DRAFT})
        assert saved.status_code == 200 and saved.json()["data"]["can_check"] is True
        capture(G, "draft_put_200", saved)
        too_long = c.put(N, headers=wh, json={"body": "x" * 20001})
        assert too_long.status_code == 422
        capture(G, "draft_put_too_long_422", too_long)
        tm_read = c.get(N, headers=tm)
        assert tm_read.status_code == 200 and tm_read.json()["data"]["body"] == DRAFT
        capture(G, "draft_team_member_200", tm_read)
        tm_put = c.put(N, headers=tm, json={"body": "mine now"})
        assert tm_put.status_code == 403
        capture(G, "draft_put_team_member_403", tm_put)
        tm_check = c.post(f"{N}/check", headers=tm)
        assert tm_check.status_code == 403
        capture(G, "check_team_member_403", tm_check)
        mentor_read = c.get(N, headers=mentor)
        assert mentor_read.status_code == 403
        capture(G, "draft_mentor_403", mentor_read)

        # 4. Run the check: 202 and a job; a second press returns the same check.
        queued = c.post(f"{N}/check", headers=wh)
        assert queued.status_code == 202, queued.text
        q = queued.json()["data"]
        assert q["outcome"] == "queued" and q["check"]["status"] == "queued"
        capture(G, "check_202", queued)
        again = c.post(f"{N}/check", headers=wh)
        assert again.status_code == 200 and again.json()["data"]["outcome"] == "in_progress"
        assert again.json()["data"]["check"]["id"] == q["check"]["id"]
        capture(G, "check_in_progress_200", again)
        capture(G, "latest_queued", c.get(f"{N}/checks/latest", headers=wh))
        job_q = c.get(f"/api/v1/jobs/{q['check']['job_id']}", headers=wh)
        capture(G, "job_queued", job_q)

        _drain(router)
        job_done = c.get(f"/api/v1/jobs/{q['check']['job_id']}", headers=wh)
        assert job_done.json()["data"]["status"] == "succeeded", job_done.text
        capture(G, "job_succeeded", job_done)
        latest = c.get(f"{N}/checks/latest", headers=wh)
        chk = latest.json()["data"]["check"]
        assert chk["status"] == "ready" and chk["tokens_used"] > 0 and chk["stale"] is False
        capture(G, "latest_ready", latest)
        capture(G, "latest_ready_team_member", c.get(f"{N}/checks/latest", headers=tm))
        if not REAL:
            kinds = {(f["metric"], f["kind"], f["severity"]) for f in chk["findings"]}
            assert ("mrr", "inconsistent", "high") in kinds
            assert ("mrr", "match", "ok") in kinds
            assert ("burn", "unverified", "info") in kinds  # books show no net burn
            assert ("ask", "match", "ok") in kinds
            assert ("growth_pct", "unverified", "info") in kinds
            assert job_done.json()["data"]["result"]["dropped_claims"] == 3

        # 5. Score and history pick it up.
        score = c.get(f"{R}/score", headers=wh)
        sigs = {s["key"]: s for d in score.json()["data"]["dimensions"] for s in d["signals"]}
        assert sigs["financials.use_of_funds"]["score"] == 100
        assert sigs["story.narrative"]["score"] in (50, 100)
        capture(G, "score_after_check", score)
        hist = c.get(f"{R}/history?range=30", headers=wh)
        assert any(e["type"] == "narrative_check" for e in hist.json()["data"]["log"])
        capture(G, "history_after_check", hist)

        # 6. Fix the books (2M a month in, 500k a month net out); same text: claims reused, free.
        _txn(c, wh, 120_000_000, "in", "Revenue")
        _txn(c, wh, 750_000_000, "out", "Salaries")
        calls = router.claim_calls
        reused = c.post(f"{N}/check", headers=wh)
        assert reused.status_code == 200, reused.text
        r = reused.json()["data"]
        assert r["outcome"] == "reused" and r["check"]["tokens_used"] == 0
        assert router.claim_calls == calls
        capture(G, "check_reused_200", reused)

        # 7. Edit the draft: the last check is stale; checking again is a new model call.
        fixed = DRAFT.replace("2m NGN MRR", "1.6m NGN MRR")
        c.put(N, headers=wh, json={"body": fixed})
        stale = c.get(f"{N}/checks/latest", headers=wh)
        assert stale.json()["data"]["check"]["stale"] is True
        capture(G, "latest_stale", stale)

        # 8. Budget gates, before anything is queued.
        _set_usage(ws_id, 95_000)
        low = c.post(f"{N}/check", headers=wh)
        assert low.status_code == 409 and low.json()["error"]["code"] == "AI_BUDGET_LOW"
        capture(G, "check_budget_low_409", low)
        _set_usage(ws_id, 100_000)
        out = c.post(f"{N}/check", headers=wh)
        assert out.status_code == 429 and out.json()["error"]["code"] == "AI_BUDGET_EXHAUSTED"
        capture(G, "check_budget_exhausted_429", out)
        _set_usage(ws_id, 0)
        second = c.post(f"{N}/check", headers=wh)
        assert second.status_code == 202, second.text
        _drain(router)
        assert router.claim_calls == calls + 1
        capture(G, "latest_after_edit", c.get(f"{N}/checks/latest", headers=wh))
