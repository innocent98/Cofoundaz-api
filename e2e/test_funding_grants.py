"""Live Funding Hub grants journey (slice E): a founder browses the verified catalogue, runs the
match job (drained in this process with a CANNED model, so the scores and the one line reasons are
the fake's, not a real model's: the live part is the API, the queue, the plain country filter, the
persistence of dismiss and not-relevant, the tracker, drafting and the guard rails), dismisses and
restores a suggestion, starts and tracks an application, drafts an answer, adds a grant by hand,
and hits the guard rails. A team member and a mentor joined through real invitations prove the
role gate live.

Every response goes to e2e/_captures/funding_grants/ and is the verbatim source of
docs/fe-integration-guide-funding-grants.md. Not live here: the 402 plan gate (the e2e server runs
with enforcement off), a real model's scoring and drafting quality, the weekly schedule firing on
its own (the journey queues the same job through the refresh route), and the notification (checked
in the unit tests).
"""

import uuid
from datetime import date, timedelta

import httpx
import pytest

from tests.grant_helpers import GrantLLM

F = "/api/v1/funding"
G = f"{F}/grants"
APPS = f"{F}/grant-applications"
CAP = "funding_grants"


def _onboard(c: httpx.Client, auth: dict) -> None:
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Obi"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "Placeholder Co"})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={
            "step": 3,
            "industry": "Health clinic software",
            "business_model": "b2b",
            "stage": "launch",
            "country": "NG",
        },
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


def _drain(canned: GrantLLM) -> None:
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
    yield GrantLLM()
    llm_budget.get_llm_client = real


def test_funding_grants_journey(
    base_url, make_verified_user, capture, unique_email, mailbox, canned_llm
):
    with httpx.Client(base_url=base_url, timeout=30.0) as c:
        u = make_verified_user(c)
        token = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = {"Authorization": f"Bearer {token}"}
        _onboard(c, auth)
        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wh = {**auth, "X-Workspace-Id": me["active_workspace_id"]}

        # 1. The catalogue: every row carries its source link and the date it was checked.
        r = c.get(G, headers=wh)
        assert r.status_code == 200 and len(r.json()["data"]["items"]) == 15
        capture(CAP, "catalogue", r)
        r = c.get(f"{G}?only_open=true&kind=in_kind&q=cloud", headers=wh)
        assert r.status_code == 200 and r.json()["data"]["items"]
        capture(CAP, "catalogue_filtered", r)
        r = c.get(f"{G}?kind=bogus", headers=wh)
        assert r.status_code == 422
        capture(CAP, "catalogue_bad_filter_422", r)

        # 2. Matches before any run, then queue a run and poll.
        r = c.get(f"{G}/matches", headers=wh)
        assert r.json()["data"]["items"] == [] and r.json()["data"]["refresh"]["status"] == "never"
        capture(CAP, "matches_never_run", r)
        r = c.post(f"{G}/matches/refresh", headers=wh)
        assert r.status_code == 202, r.text
        capture(CAP, "refresh_queued", r)
        r = c.post(f"{G}/matches/refresh", headers=wh)
        assert r.status_code == 200 and r.json()["data"]["deduplicated"] is True
        capture(CAP, "refresh_deduplicated", r)
        r = c.get(f"{G}/matches", headers=wh)
        assert r.json()["data"]["refresh"]["status"] == "queued"
        capture(CAP, "matches_queued", r)
        _drain(canned_llm)
        r = c.get(f"{G}/matches", headers=wh)
        d = r.json()["data"]
        assert d["refresh"]["status"] == "idle" and 0 < len(d["items"]) <= 10
        assert all(i["ai_scored"] for i in d["items"])
        capture(CAP, "matches", r)
        items = d["items"]

        # 3. Dismiss one, mark another not relevant: both stay out after a new run.
        r = c.post(f"{G}/matches/{items[0]['id']}/dismiss", headers=wh)
        assert r.status_code == 200
        capture(CAP, "match_dismissed", r)
        r = c.post(f"{G}/matches/{items[1]['id']}/not-relevant", headers=wh)
        assert r.json()["data"]["state"] == "not_relevant"
        capture(CAP, "match_not_relevant", r)
        c.post(f"{G}/matches/refresh", headers=wh)
        _drain(canned_llm)
        after = c.get(f"{G}/matches", headers=wh).json()["data"]["items"]
        gone = {items[0]["grant"]["slug"], items[1]["grant"]["slug"]}
        assert gone.isdisjoint({i["grant"]["slug"] for i in after})
        r = c.get(f"{G}/matches?state=dismissed", headers=wh)
        assert [i["id"] for i in r.json()["data"]["items"]] == [items[0]["id"]]
        capture(CAP, "matches_dismissed_list", r)
        r = c.post(f"{G}/matches/{items[0]['id']}/restore", headers=wh)
        assert r.json()["data"]["state"] == "suggested"
        capture(CAP, "match_restored", r)

        # 4. Start an application from a suggestion.
        pick = next(i for i in c.get(f"{G}/matches", headers=wh).json()["data"]["items"])
        r = c.post(f"{G}/{pick['grant']['id']}/apply", headers=wh)
        assert r.status_code == 201, r.text
        capture(CAP, "apply_created", r)
        app = r.json()["data"]
        r = c.post(f"{G}/{pick['grant']['id']}/apply", headers=wh)
        assert r.status_code == 200 and r.json()["data"]["id"] == app["id"]
        capture(CAP, "apply_existing", r)
        r = c.post(f"{G}/matches/{pick['id']}/dismiss", headers=wh)
        assert r.status_code == 422
        capture(CAP, "dismiss_while_applying_422", r)
        r = c.get(f"{G}/matches?state=applying", headers=wh)
        assert [i["id"] for i in r.json()["data"]["items"]] == [pick["id"]]
        capture(CAP, "matches_applying_list", r)

        # 5. Draft an answer with AI, then guard it.
        first, second = app["answers"][0], app["answers"][1]
        url = f"{APPS}/{app['id']}/answers/{first['id']}"
        r = c.post(f"{url}/draft", headers=wh, json={})
        assert r.status_code == 202 and r.json()["data"]["draft_status"] == "queued"
        capture(CAP, "draft_queued", r)
        _drain(canned_llm)
        r = c.get(f"{APPS}/{app['id']}", headers=wh)
        got = r.json()["data"]["answers"][0]
        assert got["ai_drafted"] is True and got["draft_status"] is None and got["answer"]
        capture(CAP, "application_after_draft", r)
        r = c.post(f"{url}/draft", headers=wh, json={})
        assert r.status_code == 422
        capture(CAP, "draft_needs_overwrite_422", r)
        r = c.patch(
            f"{APPS}/{app['id']}/answers/{second['id']}",
            headers=wh,
            json={"answer": "We serve 40 clinics in Lagos."},
        )
        assert r.status_code == 200 and r.json()["data"]["ai_drafted"] is False
        capture(CAP, "answer_edited", r)
        r = c.post(f"{APPS}/{app['id']}/answers", headers=wh, json={"question": "Why now?"})
        assert r.status_code == 201
        capture(CAP, "answer_added", r)
        extra = r.json()["data"]["id"]
        r = c.delete(f"{APPS}/{app['id']}/answers/{extra}", headers=wh)
        assert r.json()["data"] == {"deleted": True}
        capture(CAP, "answer_deleted", r)

        # 6. The tracker: a grant added by hand, a status move, an overdue one.
        soon = (date.today() + timedelta(days=21)).isoformat()
        r = c.post(
            APPS,
            headers=wh,
            json={
                "name": "State innovation fund",
                "funder": "Lagos State",
                "deadline": soon,
                "amount_minor": 500_000_000,
                "currency": "NGN",
            },
        )
        assert r.status_code == 201, r.text
        capture(CAP, "manual_created", r)
        past = (date.today() - timedelta(days=3)).isoformat()
        late = c.post(APPS, headers=wh, json={"name": "Missed call", "deadline": past}).json()[
            "data"
        ]
        assert late["expired"] is True
        r = c.patch(f"{APPS}/{app['id']}", headers=wh, json={"status": "submitted"})
        assert r.json()["data"]["submitted_at"]
        capture(CAP, "application_submitted", r)
        r = c.get(APPS, headers=wh)
        t = r.json()["data"]
        assert t["counts"]["submitted"] == 1 and t["expired"] == 1
        capture(CAP, "tracker", r)
        r = c.post(APPS, headers=wh, json={"name": "X", "amount_minor": 5})
        assert r.status_code == 422
        capture(CAP, "manual_amount_needs_currency_422", r)
        r = c.patch(f"{APPS}/{app['id']}", headers=wh, json={"status": "done"})
        assert r.status_code == 422
        capture(CAP, "bad_status_422", r)
        r = c.delete(f"{APPS}/{late['id']}", headers=wh)
        assert r.json()["data"] == {"deleted": True}
        capture(CAP, "application_deleted", r)
        r = c.get(f"{APPS}/{uuid.uuid4()}", headers=wh)
        assert r.status_code == 404
        capture(CAP, "application_404", r)

        # 7. Roles: a team member is in, a mentor is refused, live.
        member = _join(c, wh, "team_member", unique_email, mailbox)
        assert c.get(APPS, headers=member).status_code == 200
        outsider = _join(c, wh, "mentor", unique_email, mailbox)
        r = c.get(G, headers=outsider)
        assert r.status_code == 403
        capture(CAP, "mentor_403", r)
        assert c.post(f"{G}/matches/refresh", headers=outsider).status_code == 403
