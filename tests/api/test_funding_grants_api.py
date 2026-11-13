"""Funding Hub slice E: grants. F + TM only, behind the plan module; the weekly match run (plain
filter, then the model on the best ten, plain fallback), dismiss and not-relevant that persist,
the application tracker, AI drafting, stale and expired handling."""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.config import settings
from app.core.security import create_access_token
from app.db.models.enums import FUNDING_ROLES, MembershipRole, StartupStage
from app.db.models.grant import Grant, GrantAnswer, GrantApplication, GrantMatch
from app.db.models.job import Job
from app.db.models.notification import Notification
from app.platform import llm_budget
from app.services.billing import subscription as subs
from app.services.funding import grants as svc
from app.services.funding import grants_catalogue as cat
from tests.billing_helpers import seed_plans
from tests.factories import create_membership, create_startup, create_user
from tests.grant_helpers import GrantLLM

BASE = "/api/v1/funding"
G = f"{BASE}/grants"
APPS = f"{BASE}/grant-applications"


def _member(db, startup, role):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, u, startup, role=role)
    db.flush()
    return u, {
        "Authorization": f"Bearer {create_access_token(str(u.id))}",
        "X-Workspace-Id": str(startup.id),
    }


@pytest.fixture()
def llm(monkeypatch):
    fake = GrantLLM()
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: fake)
    return fake


@pytest.fixture()
def team(db, llm):
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=owner)
    s.country, s.industry, s.stage = "NG", "health clinic software", StartupStage.launch
    cat.sync_catalogue(db)
    members = {role: _member(db, s, role) for role in MembershipRole}
    db.commit()
    return s, {r: m[1] for r, m in members.items()}, {r: m[0] for r, m in members.items()}


@pytest.fixture()
def founder(team):
    return team[1][MembershipRole.founder]


def _drain(db):
    from app.worker import runner
    from app.worker.handlers import funding as handlers

    runner.register_handler(svc.MATCH_JOB, handlers.handle_grant_match)
    runner.register_handler(svc.DRAFT_JOB, handlers.handle_grant_draft)
    for _ in range(20):
        if runner.run_once(db) == 0:
            break
    db.expire_all()


def _match(client, db, h):
    r = client.post(f"{G}/matches/refresh", headers=h)
    assert r.status_code == 202, r.text
    _drain(db)
    return client.get(f"{G}/matches", headers=h).json()["data"]


def _slug(db, slug):
    return db.query(Grant).filter_by(slug=slug).one()


# --------------------------------------------------------------------------- access
ROUTES = [
    ("GET", G),
    ("GET", f"{G}/matches"),
    ("POST", f"{G}/matches/refresh"),
    ("GET", APPS),
    ("POST", APPS),
]


def test_only_founder_and_team_member_reach_grants(client, team):
    _s, heads, _ = team
    for role, h in heads.items():
        for method, path in ROUTES:
            r = client.request(
                method, path, json={"name": "x"} if method == "POST" else None, headers=h
            )
            if role in FUNDING_ROLES:
                assert r.status_code in (200, 201, 202), (role, path, r.text)
            else:
                assert r.status_code == 403, (role, path)


def test_plan_gate_is_dark_then_402(client, db, team, monkeypatch):
    s, heads, _ = team
    seed_plans(db)
    assert client.get(G, headers=heads[MembershipRole.founder]).status_code == 200
    monkeypatch.setattr(settings, "PLAN_ENFORCEMENT", True)
    sub = subs.get_or_create(db, s.id)
    sub.trial_used_at = subs.now()
    db.flush()
    r = client.get(G, headers=heads[MembershipRole.founder])
    assert r.status_code == 402 and r.json()["error"]["code"] == "PLAN_REQUIRED"
    sub.status, sub.plan_key = "active", "scale"
    sub.current_period_end = subs.now() + timedelta(days=5)
    db.flush()
    assert client.get(G, headers=heads[MembershipRole.founder]).status_code == 200


def test_another_workspaces_ids_are_404(client, db, team, founder):
    first = _match(client, db, founder)["items"][0]
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    other = create_startup(db, owner=owner, name="Other")
    _, oh = _member(db, other, MembershipRole.founder)
    db.commit()
    assert client.post(f"{G}/matches/{first['id']}/dismiss", headers=oh).status_code == 404
    app_id = client.post(f"{G}/{first['grant']['id']}/apply", headers=founder).json()["data"]["id"]
    assert client.get(f"{APPS}/{app_id}", headers=oh).status_code == 404
    assert client.patch(f"{APPS}/{app_id}", json={"notes": "x"}, headers=oh).status_code == 404
    assert client.delete(f"{APPS}/{app_id}", headers=oh).status_code == 404
    assert client.get(APPS, headers=oh).json()["data"]["items"] == []


# --------------------------------------------------------------------------- catalogue
def test_catalogue_lists_every_row_with_its_source_and_check_date(client, founder):
    d = client.get(G, headers=founder).json()["data"]
    assert len(d["items"]) == 15 and d["disclaimer"]
    for g in d["items"]:
        assert g["source_url"].startswith("https://") and g["verified_on"] == "2026-11-12"
    tef = next(g for g in d["items"] if g["slug"] == "tef-entrepreneurship")
    assert tef["amount_max"] == {"minor": 500_000, "currency": "USD"} and tef["closed"] is True


def test_catalogue_filters(client, founder):
    open_ = client.get(f"{G}?only_open=true", headers=founder).json()["data"]["items"]
    assert open_ and all(not g["closed"] for g in open_)
    credits = client.get(f"{G}?kind=in_kind", headers=founder).json()["data"]["items"]
    assert credits and all(g["in_kind"] for g in credits)
    cash = client.get(f"{G}?kind=cash", headers=founder).json()["data"]["items"]
    assert cash and not any(g["in_kind"] for g in cash)
    found = client.get(f"{G}?q=unicef", headers=founder).json()["data"]["items"]
    assert [g["slug"] for g in found] == ["unicef-venture-fund"]
    assert client.get(f"{G}?kind=other", headers=founder).status_code == 422


def test_stale_rows_are_marked(client, db, founder):
    g = _slug(db, "aws-activate")
    g.verified_on = date.today() - timedelta(days=svc.STALE_DAYS + 1)
    db.commit()
    items = client.get(G, headers=founder).json()["data"]["items"]
    assert next(x for x in items if x["slug"] == "aws-activate")["stale"] is True
    assert next(x for x in items if x["slug"] == "google-cloud-startups")["stale"] is False


# --------------------------------------------------------------------------- matching
def test_matching_filters_on_country_and_asks_the_model_about_the_best_ten(
    client, db, team, founder, llm
):
    d = _match(client, db, founder)
    assert d["refresh"]["status"] == "idle" and d["refresh"]["last_run_at"]
    items = d["items"]
    assert 0 < len(items) <= svc.TOP_N
    assert all(i["ai_scored"] for i in items)
    assert items == sorted(items, key=lambda i: -i["score"])
    assert items[0]["why"].startswith("Fits your clinic startup")
    prompt = llm.calls[0][1][-1].content
    assert prompt.count("- slug=") == len(items)
    assert "NG" in prompt  # the founder's country reaches the model


def test_a_grant_for_another_country_is_filtered_out_in_code(client, db, team, founder):
    s = team[0]
    s.country = "US"
    db.commit()
    slugs = {i["grant"]["slug"] for i in _match(client, db, founder)["items"]}
    assert "developpp-ventures" not in slugs  # names Nigeria, Egypt, Kenya... not the US
    assert "tef-entrepreneurship" not in slugs  # Africa only
    assert "aws-activate" in slugs or "google-cloud-startups" in slugs  # open to anyone


def test_the_plain_score_and_a_templated_reason_stand_when_the_model_fails(
    client, db, team, founder, monkeypatch
):
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: GrantLLM(boom=RuntimeError("down")))
    monkeypatch.setattr(settings, "WORKER_MAX_ATTEMPTS", 1)
    items = _match(client, db, founder)["items"]
    assert items and not any(i["ai_scored"] for i in items)
    assert all(0 <= i["score"] <= 100 and i["why"] for i in items)


def test_an_exhausted_budget_never_blocks_matching(client, db, team, founder, monkeypatch, llm):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 1)
    llm_budget.debit(db, team[0].id, 10)
    db.commit()
    items = _match(client, db, founder)["items"]
    assert items and not llm.calls and not any(i["ai_scored"] for i in items)


def test_hostile_model_output_is_clamped_and_unknown_slugs_dropped(
    client, db, founder, monkeypatch
):
    def evil(messages):
        return {
            "matches": [
                {"slug": "not-in-the-list", "score": 99, "why": "x"},
                {"slug": "aws-activate", "score": 5000, "why": "Great fit — really " + "y" * 900},
                {"slug": "aws-activate", "score": 1, "why": "duplicate"},
                {"slug": "google-cloud-startups", "score": "high", "why": "bad score"},
                "junk",
            ]
        }

    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: GrantLLM(match=evil))
    items = _match(client, db, founder)["items"]
    aws = next(i for i in items if i["grant"]["slug"] == "aws-activate")
    assert aws["score"] == 100 and len(aws["why"]) <= 240 and "—" not in aws["why"]
    assert aws["ai_scored"] is True
    gcs = next(i for i in items if i["grant"]["slug"] == "google-cloud-startups")
    assert gcs["ai_scored"] is False  # an unreadable score falls back to the plain one


def test_dismissed_and_not_relevant_persist_across_refreshes(client, db, founder):
    items = _match(client, db, founder)["items"]
    a, b = items[0], items[1]
    assert (
        client.post(f"{G}/matches/{a['id']}/dismiss", headers=founder).json()["data"]["state"]
        == "dismissed"
    )
    r = client.post(f"{G}/matches/{b['id']}/not-relevant", headers=founder)
    assert r.json()["data"]["state"] == "not_relevant"
    after = {i["grant"]["slug"] for i in _match(client, db, founder)["items"]}
    assert a["grant"]["slug"] not in after and b["grant"]["slug"] not in after
    gone = client.get(f"{G}/matches?state=dismissed", headers=founder).json()["data"]["items"]
    assert [i["id"] for i in gone] == [a["id"]]
    assert client.post(f"{G}/matches/{a['id']}/restore", headers=founder).status_code == 200
    back = {
        i["grant"]["slug"]
        for i in client.get(f"{G}/matches", headers=founder).json()["data"]["items"]
    }
    assert a["grant"]["slug"] in back


def test_a_second_refresh_while_one_is_queued_is_deduplicated(client, db, founder):
    first = client.post(f"{G}/matches/refresh", headers=founder)
    second = client.post(f"{G}/matches/refresh", headers=founder)
    assert first.status_code == 202 and second.status_code == 200
    assert second.json()["data"]["deduplicated"] is True
    assert second.json()["data"]["refresh"]["status"] == "queued"
    assert db.query(Job).filter_by(type=svc.MATCH_JOB).count() == 1


def test_a_run_notifies_founders_and_team_members_only_when_something_is_new(
    client, db, team, founder, monkeypatch
):
    monkeypatch.setattr(settings, "FUNDING_GRANT_MATCHING_ENABLED", True)
    s, _heads, users = team
    _match(client, db, founder)
    got = db.query(Notification).filter_by(type="funding.grant.matched").all()
    assert {n.user_id for n in got} == {
        users[MembershipRole.founder].id,
        users[MembershipRole.team_member].id,
    }
    n_before = len(got)
    _match(client, db, founder)  # nothing new
    assert db.query(Notification).filter_by(type="funding.grant.matched").count() == n_before
    assert "new grant" in got[0].title


def test_a_suggestion_that_no_longer_ranks_is_removed_not_kept(client, db, team, founder):
    first = {i["grant"]["slug"] for i in _match(client, db, founder)["items"]}
    gone = _slug(db, sorted(first)[0])
    gone.active = False
    db.commit()
    now = {i["grant"]["slug"] for i in _match(client, db, founder)["items"]}
    assert gone.slug not in now


def test_the_weekly_schedule_enqueues_one_run_per_workspace_per_week(db, team, monkeypatch):
    from app.worker.scheduler import scheduler_tick

    monkeypatch.setattr(settings, "FUNDING_GRANT_MATCHING_ENABLED", True)
    s = team[0]
    now = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
    scheduler_tick(db, now=now)
    assert db.query(Job).filter_by(type=svc.MATCH_JOB, startup_id=s.id).count() == 1
    scheduler_tick(db, now=now + timedelta(days=2))  # same ISO week
    assert db.query(Job).filter_by(type=svc.MATCH_JOB, startup_id=s.id).count() == 1
    scheduler_tick(db, now=now + timedelta(days=7))
    assert db.query(Job).filter_by(type=svc.MATCH_JOB, startup_id=s.id).count() == 2


def test_the_weekly_match_is_off_by_default_so_a_hidden_module_spends_and_sends_nothing(
    client, db, team, founder, llm
):
    from app.worker.scheduler import scheduler_tick

    assert settings.FUNDING_GRANT_MATCHING_ENABLED is False
    scheduler_tick(db, now=datetime(2026, 11, 16, 9, 0, tzinfo=UTC))
    assert db.query(Job).filter_by(type=svc.MATCH_JOB).count() == 0
    # a weekly job that was queued before the switch went off is skipped by the handler
    s = team[0]
    job = svc.job_dispatcher.enqueue(
        db, svc.MATCH_JOB, {"startup_id": str(s.id), "scheduled": True}, s.id
    )
    db.commit()
    _drain(db)
    db.refresh(job)
    assert job.result == {"status": "disabled"}
    assert llm.calls == [] and db.query(GrantMatch).count() == 0


def test_a_manual_refresh_still_works_with_the_switch_off_but_never_notifies(
    client, db, team, founder
):
    assert len(_match(client, db, founder)["items"]) > 0
    assert db.query(Notification).filter_by(type="funding.grant.matched").count() == 0


def test_the_workspace_lock_is_not_held_across_the_model_call(
    client, db, team, founder, monkeypatch
):
    order: list[str] = []
    real_lock = svc.lock_workspace
    monkeypatch.setattr(
        svc, "lock_workspace", lambda *a, **k: (order.append("lock"), real_lock(*a, **k))[1]
    )

    def match(messages):
        order.append("model")
        return {"matches": []}

    llm = GrantLLM(match=match)
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: llm)
    _match(client, db, founder)
    # queueing locks (before the job exists); the run itself locks only after the model call
    assert order == ["lock", "model", "lock"]


def test_a_run_for_a_workspace_without_the_plan_does_nothing(client, db, team, monkeypatch):
    s, heads, _ = team
    job = svc.job_dispatcher.enqueue(db, svc.MATCH_JOB, {"startup_id": str(s.id)}, s.id)
    db.commit()
    seed_plans(db)
    monkeypatch.setattr(settings, "PLAN_ENFORCEMENT", True)
    sub = subs.get_or_create(db, s.id)
    sub.trial_used_at = subs.now()
    db.commit()
    _drain(db)
    db.refresh(job)
    assert job.result == {"status": "plan"}
    assert db.query(GrantMatch).count() == 0


# --------------------------------------------------------------------------- applications
def _apply(client, db, h, slug="google-cloud-startups"):
    r = client.post(f"{G}/{_slug(db, slug).id}/apply", headers=h)
    return r


def test_start_application_seeds_questions_and_marks_the_match(client, db, founder):
    _match(client, db, founder)
    g = _slug(db, "google-cloud-startups")
    r = _apply(client, db, founder)
    assert r.status_code == 201
    d = r.json()["data"]
    assert d["name"] == g.name and d["status"] == "planning" and d["source_url"] == g.source_url
    assert len(d["answers"]) == len(svc.DEFAULT_QUESTIONS) and d["grant"]["slug"] == g.slug
    m = db.query(GrantMatch).filter_by(grant_id=g.id).one()
    assert m.state == "applying"
    again = _apply(client, db, founder)
    assert again.status_code == 200 and again.json()["data"]["id"] == d["id"]
    assert db.query(GrantApplication).count() == 1
    assert (
        client.post(f"{G}/matches/{m.id}/dismiss", headers=founder).status_code == 422
    )  # remove the application first
    assert (
        client.post(f"{G}/{'0' * 8}-0000-0000-0000-{'0' * 12}/apply", headers=founder).status_code
        == 404
    )


def test_deleting_an_application_brings_the_suggestion_back(client, db, founder):
    _match(client, db, founder)
    app_id = _apply(client, db, founder).json()["data"]["id"]
    assert client.delete(f"{APPS}/{app_id}", headers=founder).json()["data"] == {"deleted": True}
    m = db.query(GrantMatch).filter_by(grant_id=_slug(db, "google-cloud-startups").id).one()
    db.refresh(m)
    assert m.state == "suggested"
    assert db.query(GrantAnswer).count() == 0  # answers go with it


def test_manual_application_and_the_tracker(client, db, founder):
    soon = (date.today() + timedelta(days=10)).isoformat()
    r = client.post(
        APPS,
        json={
            "name": "Local Fund",
            "funder": "State",
            "deadline": soon,
            "amount_minor": 50_000_000,
            "currency": "NGN",
        },
        headers=founder,
    )
    assert r.status_code == 201
    d = r.json()["data"]
    assert (
        d["grant_id"] is None
        and d["source_url"] is None
        and d["amount"] == {"minor": 50_000_000, "currency": "NGN"}
    )
    no_ccy = client.post(APPS, json={"name": "X", "amount_minor": 5}, headers=founder)
    assert no_ccy.status_code == 422
    extra = client.post(APPS, json={"name": "X", "bogus": 1}, headers=founder)
    assert extra.status_code == 422
    _apply(client, db, founder)
    t = client.get(APPS, headers=founder).json()["data"]
    assert len(t["items"]) == 2 and t["counts"]["planning"] == 2 and t["expired"] == 0
    assert t["items"][0]["name"] == "Local Fund"  # dated first


def test_status_moves_and_submitted_is_stamped(client, db, founder):
    app_id = _apply(client, db, founder).json()["data"]["id"]
    r = client.patch(
        f"{APPS}/{app_id}", json={"status": "submitted", "notes": "sent"}, headers=founder
    )
    d = r.json()["data"]
    assert d["status"] == "submitted" and d["submitted_at"] and d["notes"] == "sent"
    stamp = d["submitted_at"]
    again = client.patch(f"{APPS}/{app_id}", json={"status": "awarded"}, headers=founder).json()[
        "data"
    ]
    assert again["status"] == "awarded" and again["submitted_at"] == stamp
    assert (
        client.patch(f"{APPS}/{app_id}", json={"status": "nope"}, headers=founder).status_code
        == 422
    )


def test_a_missed_deadline_is_expired_until_it_is_submitted(client, founder):
    past = (date.today() - timedelta(days=2)).isoformat()
    d = client.post(APPS, json={"name": "Late", "deadline": past}, headers=founder).json()["data"]
    assert d["expired"] is True
    assert client.get(APPS, headers=founder).json()["data"]["expired"] == 1
    sent = client.patch(f"{APPS}/{d['id']}", json={"status": "submitted"}, headers=founder).json()[
        "data"
    ]
    assert sent["expired"] is False


def test_a_catalogue_row_that_went_stale_is_flagged_on_the_application(client, db, founder):
    app_id = _apply(client, db, founder).json()["data"]["id"]
    g = _slug(db, "google-cloud-startups")
    g.verified_on = date.today() - timedelta(days=400)
    db.commit()
    assert client.get(f"{APPS}/{app_id}", headers=founder).json()["data"]["catalogue_stale"] is True


def test_a_retired_catalogue_row_cannot_be_started(client, db, founder):
    g = _slug(db, "aws-activate")
    g.active = False
    db.commit()
    assert _apply(client, db, founder, "aws-activate").status_code == 404


def test_the_application_cap(client, founder, monkeypatch):
    monkeypatch.setattr(svc, "MAX_APPLICATIONS", 1)
    assert client.post(APPS, json={"name": "One"}, headers=founder).status_code == 201
    assert client.post(APPS, json={"name": "Two"}, headers=founder).status_code == 422


# --------------------------------------------------------------------------- answers + drafting
def _first_answer(client, founder, app_id):
    return client.get(f"{APPS}/{app_id}", headers=founder).json()["data"]["answers"][0]


def test_answers_crud_and_cap(client, db, founder, monkeypatch):
    app_id = _apply(client, db, founder).json()["data"]["id"]
    a = _first_answer(client, founder, app_id)
    r = client.patch(
        f"{APPS}/{app_id}/answers/{a['id']}", json={"answer": "Mine."}, headers=founder
    )
    assert r.json()["data"]["answer"] == "Mine." and r.json()["data"]["ai_drafted"] is False
    new = client.post(f"{APPS}/{app_id}/answers", json={"question": "Why now?"}, headers=founder)
    assert new.status_code == 201 and new.json()["data"]["position"] == len(svc.DEFAULT_QUESTIONS)
    assert client.delete(
        f"{APPS}/{app_id}/answers/{new.json()['data']['id']}", headers=founder
    ).json()["data"] == {"deleted": True}
    monkeypatch.setattr(svc, "MAX_ANSWERS", len(svc.DEFAULT_QUESTIONS))
    assert (
        client.post(
            f"{APPS}/{app_id}/answers", json={"question": "More?"}, headers=founder
        ).status_code
        == 422
    )


def test_draft_with_ai_fills_an_empty_answer(client, db, founder, llm):
    app_id = _apply(client, db, founder).json()["data"]["id"]
    a = _first_answer(client, founder, app_id)
    r = client.post(f"{APPS}/{app_id}/answers/{a['id']}/draft", json={}, headers=founder)
    assert r.status_code == 202 and r.json()["data"]["draft_status"] == "queued"
    _drain(db)
    got = _first_answer(client, founder, app_id)
    assert got["answer"].startswith("We help clinics") and got["ai_drafted"] is True
    assert got["draft_status"] is None
    prompt = llm.calls[-1][1][-1].content
    assert a["question"] in prompt and "<company>" in prompt


def test_draft_refuses_to_replace_text_without_overwrite(client, db, founder):
    app_id = _apply(client, db, founder).json()["data"]["id"]
    a = _first_answer(client, founder, app_id)
    client.patch(f"{APPS}/{app_id}/answers/{a['id']}", json={"answer": "Mine."}, headers=founder)
    url = f"{APPS}/{app_id}/answers/{a['id']}/draft"
    assert client.post(url, json={}, headers=founder).status_code == 422
    assert db.query(Job).filter_by(type=svc.DRAFT_JOB).count() == 0
    assert client.post(url, json={"overwrite": True}, headers=founder).status_code == 202
    _drain(db)
    assert _first_answer(client, founder, app_id)["answer"].startswith("We help clinics")


def test_text_typed_while_the_draft_runs_is_never_overwritten(client, db, founder):
    app_id = _apply(client, db, founder).json()["data"]["id"]
    a = _first_answer(client, founder, app_id)
    client.post(f"{APPS}/{app_id}/answers/{a['id']}/draft", json={}, headers=founder)
    client.patch(
        f"{APPS}/{app_id}/answers/{a['id']}", json={"answer": "Typed first."}, headers=founder
    )
    _drain(db)
    got = _first_answer(client, founder, app_id)
    assert got["answer"] == "Typed first." and got["draft_status"] is None


def _type_during_the_call(monkeypatch, db, text):
    """Simulate the founder's PATCH committing while the model call is in flight."""
    from sqlalchemy import update

    def during(self, messages, *, schema, max_tokens):
        db.execute(update(GrantAnswer).values(answer=text, draft_status=None, ai_drafted=False))
        return {"answer": "Model text."}

    monkeypatch.setattr(GrantLLM, "complete_json", during)


def test_a_patch_committed_during_the_model_call_is_not_overwritten(
    client, db, founder, monkeypatch
):
    app_id = _apply(client, db, founder).json()["data"]["id"]
    a = _first_answer(client, founder, app_id)
    client.post(f"{APPS}/{app_id}/answers/{a['id']}/draft", json={}, headers=founder)
    _type_during_the_call(monkeypatch, db, "Typed during the call.")
    _drain(db)
    got = _first_answer(client, founder, app_id)
    assert got["answer"] == "Typed during the call." and got["ai_drafted"] is False
    assert got["draft_status"] is None


def test_a_model_failure_marks_the_draft_failed_and_the_button_comes_back(
    client, db, founder, monkeypatch
):
    app_id = _apply(client, db, founder).json()["data"]["id"]
    a = _first_answer(client, founder, app_id)
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: GrantLLM(boom=RuntimeError("down")))
    monkeypatch.setattr(settings, "WORKER_MAX_ATTEMPTS", 1)
    client.post(f"{APPS}/{app_id}/answers/{a['id']}/draft", json={}, headers=founder)
    _drain(db)
    assert _first_answer(client, founder, app_id)["draft_status"] == "failed"
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: GrantLLM())
    assert (
        client.post(
            f"{APPS}/{app_id}/answers/{a['id']}/draft", json={}, headers=founder
        ).status_code
        == 202
    )


def test_a_dead_draft_job_is_reconciled_on_read(client, db, founder):
    app_id = _apply(client, db, founder).json()["data"]["id"]
    a = _first_answer(client, founder, app_id)
    client.post(f"{APPS}/{app_id}/answers/{a['id']}/draft", json={}, headers=founder)
    db.query(Job).delete()
    db.commit()
    assert _first_answer(client, founder, app_id)["draft_status"] == "failed"


def test_draft_is_refused_when_the_daily_budget_is_spent(client, db, team, founder, monkeypatch):
    app_id = _apply(client, db, founder).json()["data"]["id"]
    a = _first_answer(client, founder, app_id)
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 1)
    llm_budget.debit(db, team[0].id, 10)
    db.commit()
    r = client.post(f"{APPS}/{app_id}/answers/{a['id']}/draft", json={}, headers=founder)
    assert r.status_code == 429 and r.json()["error"]["code"] == "AI_BUDGET_EXHAUSTED"
    assert db.query(Job).filter_by(type=svc.DRAFT_JOB).count() == 0
