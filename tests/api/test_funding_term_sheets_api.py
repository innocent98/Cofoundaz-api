"""Funding Hub slice D: term sheets. Founder-only on every route, typed and extracted sheets, the
confirm step, deterministic flags, optional AI wording, comparison, audit, currency guard."""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.core.security import create_access_token
from app.db.models.enums import MembershipRole
from app.db.models.job import Job
from app.db.models.notification import Notification
from app.db.models.term_sheet import TermSheet, TermSheetAudit
from app.platform import llm_budget
from app.services.billing import subscription as subs
from app.services.funding import term_sheets as svc
from tests.billing_helpers import seed_plans
from tests.deck_helpers import store_deck
from tests.factories import create_membership, create_startup, create_user
from tests.term_helpers import SHEET_TEXT, TermLLM

BASE = "/api/v1/funding"
TS = f"{BASE}/term-sheets"


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
    fake = TermLLM()
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: fake)
    return fake


@pytest.fixture()
def team(db, llm):
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=owner)
    db.flush()
    members = {role: _member(db, s, role) for role in MembershipRole}
    db.commit()
    return s, {r: m[1] for r, m in members.items()}, {r: m[0] for r, m in members.items()}


@pytest.fixture()
def founder(team):
    return team[1][MembershipRole.founder]


def _drain(db):
    from app.worker import runner
    from app.worker.handlers import funding as handlers

    runner.register_handler(svc.EXTRACT_JOB, handlers.handle_extract)
    runner.register_handler(svc.WORDING_JOB, handlers.handle_wording)
    for _ in range(20):
        if runner.run_once(db) == 0:
            break
    db.expire_all()


def typed(client, h, label="Alpha Ventures", **terms):
    body = {"label": label, "terms": {"stage": "seed", **terms}}
    r = client.post(TS, json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["data"]


def uploaded(db, team, text=None, name="termsheet.pdf"):
    s, _heads, users = team
    f, _ = store_deck(db, s, users[MembershipRole.founder], slides=text or SHEET_TEXT, name=name)
    db.commit()
    return f


# ---------------------------------------------------------------- founder only (D-FUND-5)
ID = "{sheet}"
ROUTES = [
    ("GET", "/term-sheets", None),
    ("GET", "/term-sheets/rules", None),
    ("GET", "/term-sheets/audit", None),
    ("GET", "/term-sheets/compare?ids={sheet},{sheet2}", None),
    ("POST", "/term-sheets", {"label": "X", "terms": {"stage": "seed"}}),
    ("POST", "/term-sheets/extract", {"file_id": "{file}"}),
    ("GET", f"/term-sheets/{ID}", None),
    ("POST", f"/term-sheets/{ID}/confirm", {"terms": {"stage": "seed"}}),
    ("PATCH", f"/term-sheets/{ID}", {"label": "Y"}),
    ("DELETE", f"/term-sheets/{ID}", None),
]


def _fill(v, ids):
    if isinstance(v, str):
        return v.format(**ids)
    if isinstance(v, dict):
        return {k: _fill(x, ids) for k, x in v.items()}
    return v


@pytest.fixture()
def ids(client, db, team, founder):
    a = typed(client, founder, "A", liq_pref_x100=100)
    b = typed(client, founder, "B", liq_pref_x100=200)
    f = uploaded(db, team)
    return {"sheet": a["id"], "sheet2": b["id"], "file": str(f.id)}


@pytest.mark.parametrize("role", [r for r in MembershipRole if r != MembershipRole.founder])
@pytest.mark.parametrize("method,path,body", ROUTES)
def test_only_the_founder_can_touch_term_sheets(client, db, team, ids, role, method, path, body):
    """Team members too: offers are founder only. Nothing changes, nothing is queued."""
    before = (db.query(TermSheet).count(), db.query(TermSheetAudit).count(), db.query(Job).count())
    r = client.request(
        method, BASE + _fill(path, ids), json=_fill(body, ids), headers=team[1][role]
    )
    assert r.status_code == 403
    after = (db.query(TermSheet).count(), db.query(TermSheetAudit).count(), db.query(Job).count())
    assert before == after


def test_plan_gate_is_dark_then_402(client, db, team, monkeypatch):
    s, heads, _ = team
    seed_plans(db)
    assert client.get(TS, headers=heads[MembershipRole.founder]).status_code == 200
    monkeypatch.setattr(settings, "PLAN_ENFORCEMENT", True)
    sub = subs.get_or_create(db, s.id)
    sub.trial_used_at = subs.now()
    db.flush()
    r = client.get(TS, headers=heads[MembershipRole.founder])
    assert r.status_code == 402 and r.json()["error"]["code"] == "PLAN_REQUIRED"
    assert client.get(TS, headers=heads[MembershipRole.team_member]).status_code == 403
    sub.status, sub.plan_key = "active", "scale"
    sub.current_period_end = subs.now() + timedelta(days=5)
    db.flush()
    assert client.get(TS, headers=heads[MembershipRole.founder]).status_code == 200


def test_another_workspaces_ids_are_404(client, db, ids):
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    other = create_startup(db, owner=owner, name="Other")
    _, oh = _member(db, other, MembershipRole.founder)
    db.commit()
    for method, path, body in ROUTES:
        if "{" not in path:
            continue
        r = client.request(method, BASE + _fill(path, ids), json=_fill(body, ids), headers=oh)
        assert r.status_code == 404, (method, path)
    assert client.get(TS, headers=oh).json()["data"]["items"] == []


# ---------------------------------------------------------------- typed sheets
def test_empty_state_and_rules_table(client, founder):
    d = client.get(TS, headers=founder).json()["data"]
    assert (
        d["items"] == [] and d["currency"] == "NGN" and "Negotiate with clarity" in d["disclaimer"]
    )
    rules = client.get(f"{TS}/rules", headers=founder).json()["data"]["rules"]
    assert {r["id"] for r in rules} >= {"liq_pref_above_1x", "full_ratchet"}


def test_a_typed_sheet_is_confirmed_on_save_with_flags(client, founder):
    d = typed(client, founder, liq_pref_x100=200, participating=True, pro_rata="super")
    assert d["status"] == "confirmed" and d["confirmed_at"] and d["extracted_by_ai"] is False
    assert [f["rule"] for f in d["flags"]] == [
        "liq_pref_above_1x",
        "participating_preferred",
        "super_pro_rata",
    ]
    assert d["flag_count"] == 3 and d["terms"]["liq_pref_x100"] == 200
    assert "ai_text" in d["flags"][0] and d["flags"][0]["ai_text"] is None


def test_unstated_terms_are_not_stored_and_never_flag(client, founder):
    d = typed(client, founder, pre_money_minor=100, notes=None)
    assert d["terms"] == {"stage": "seed", "pre_money_minor": 100} and d["flags"] == []


@pytest.mark.parametrize(
    "terms",
    [
        {"liq_pref_x100": -1},
        {"liq_pref_x100": 5000},
        {"option_pool_pre_bps": 10001},
        {"pre_money_minor": -5},
        {"stage": "moonshot"},
        {"pro_rata": "mega"},
        {"anti_dilution": "ratchet"},
        {"exclusivity_days": 9999},
        {"participating": False, "participation_cap_x100": 200},
        {"surprise": 1},
    ],
)
def test_term_validation(client, founder, terms):
    r = client.post(TS, json={"label": "X", "terms": terms}, headers=founder)
    assert r.status_code == 422, r.text


def test_label_and_investor_rules(client, founder):
    _resp1 = client.post(TS, json={"label": "", "terms": {}}, headers=founder)
    assert _resp1.status_code == 422
    bad = client.post(
        TS,
        json={"label": "X", "terms": {}, "investor_id": "00000000-0000-4000-8000-000000000000"},
        headers=founder,
    )
    assert bad.status_code == 422
    inv = client.post(f"{BASE}/investors", json={"name": "Tolu"}, headers=founder).json()["data"]
    ok = client.post(
        TS, json={"label": "Tolu's offer", "terms": {}, "investor_id": inv["id"]}, headers=founder
    )
    assert ok.json()["data"]["investor_name"] == "Tolu"
    listed = client.get(f"{TS}?investor_id={inv['id']}", headers=founder).json()["data"]["items"]
    assert [x["label"] for x in listed] == ["Tolu's offer"]
    assert (
        client.get(
            f"{TS}?investor_id=00000000-0000-4000-8000-000000000000", headers=founder
        ).json()["data"]["items"]
        == []
    )


def test_removing_the_pipeline_investor_keeps_the_sheet(client, founder):
    inv = client.post(f"{BASE}/investors", json={"name": "Tolu"}, headers=founder).json()["data"]
    s = client.post(
        TS, json={"label": "Offer", "terms": {}, "investor_id": inv["id"]}, headers=founder
    ).json()["data"]
    client.delete(f"{BASE}/investors/{inv['id']}", headers=founder)
    got = client.get(f"{TS}/{s['id']}", headers=founder).json()["data"]
    assert got["investor_id"] is None and got["label"] == "Offer"


def test_edit_recomputes_flags_and_audits(client, db, team, founder):
    d = typed(client, founder, liq_pref_x100=200)
    r = client.patch(
        f"{TS}/{d['id']}", json={"terms": {"stage": "seed", "liq_pref_x100": 100}}, headers=founder
    )
    assert r.status_code == 200 and r.json()["data"]["flags"] == []
    r = client.patch(f"{TS}/{d['id']}", json={"label": "Renamed"}, headers=founder)
    assert (
        r.json()["data"]["label"] == "Renamed" and r.json()["data"]["terms"]["liq_pref_x100"] == 100
    )
    _resp2 = client.patch(f"{TS}/{d['id']}", json={"label": None}, headers=founder)
    assert _resp2.status_code == 422
    _resp3 = client.patch(f"{TS}/{d['id']}", json={"terms": None}, headers=founder)
    assert _resp3.status_code == 422
    log = client.get(f"{TS}/audit", headers=founder).json()["data"]
    assert [x["action"] for x in reversed(log["items"])] == ["create", "update", "update"]
    first_update = log["items"][-2]
    assert (
        first_update["before"]["flags"] == ["liq_pref_above_1x"]
        and first_update["after"]["flags"] == []
    )
    assert first_update["actor_id"] == str(team[2][MembershipRole.founder].id)


def test_delete_audits_the_before_image(client, founder):
    d = typed(client, founder, liq_pref_x100=200)
    deleted = client.delete(f"{TS}/{d['id']}", headers=founder)
    assert deleted.status_code == 200
    assert client.get(f"{TS}/{d['id']}", headers=founder).status_code == 404
    last = client.get(f"{TS}/audit?limit=1", headers=founder).json()["data"]["items"][0]
    assert (
        last["action"] == "delete"
        and last["before"]["label"] == "Alpha Ventures"
        and last["after"] is None
    )


def test_every_write_takes_the_workspace_lock(client, founder, monkeypatch):
    calls = []
    real = svc.lock_workspace
    monkeypatch.setattr(
        svc, "lock_workspace", lambda db, sid: (calls.append(sid), real(db, sid))[1]
    )
    d = typed(client, founder)
    client.patch(f"{TS}/{d['id']}", json={"label": "B"}, headers=founder)
    client.delete(f"{TS}/{d['id']}", headers=founder)
    assert len(calls) == 3


# ---------------------------------------------------------------- notification
def test_confirming_notifies_other_founders_only_with_the_disclaimer(client, db, team, founder):
    s = team[0]
    second, _h = _member(db, s, MembershipRole.founder)
    db.commit()
    typed(client, founder)
    rows = db.query(Notification).filter_by(type="funding.term_sheet.added").all()
    assert {r.user_id for r in rows} == {second.id}  # not the actor, not team members or advisors
    assert rows[0].title == "Term sheet added, comparison updated"
    assert "Not legal or financial advice" in rows[0].body


# ---------------------------------------------------------------- extraction
def test_extraction_proposes_and_nothing_counts_until_confirmed(client, db, team, founder, llm):
    f = uploaded(db, team)
    r = client.post(f"{TS}/extract", json={"file_id": str(f.id)}, headers=founder)
    assert r.status_code == 202, r.text
    body = r.json()["data"]
    sheet = body["term_sheet"]
    assert (
        body["deduplicated"] is False
        and sheet["status"] == "extracting"
        and sheet["extracted_by_ai"] is True
    )
    assert sheet["label"] == "termsheet.pdf" and sheet["terms"] == {} and sheet["flags"] == []
    assert (
        client.get(f"/api/v1/jobs/{sheet['job_id']}", headers=founder).json()["data"]["status"]
        == "queued"
    )
    # no notification and no flags for a proposal
    assert db.query(Notification).filter_by(type="funding.term_sheet.added").count() == 0
    _drain(db)
    got = client.get(f"{TS}/{sheet['id']}", headers=founder).json()["data"]
    assert got["status"] == "needs_confirmation" and got["confirmed_at"] is None
    assert got["terms"] == {} and got["flags"] == []  # still nothing confirmed
    prop = got["proposed"]
    assert (
        prop["terms"]["pre_money_minor"] == 40_000_000_000 and prop["terms"]["liq_pref_x100"] == 200
    )
    assert prop["quotes"]["pre_money_minor"].startswith("Pre-money valuation")
    assert len(llm.calls) == 1
    # a proposal cannot be compared or edited
    other = typed(client, founder, "B")
    cmp_ = client.get(f"{TS}/compare?ids={sheet['id']},{other['id']}", headers=founder)
    assert cmp_.status_code == 409 and cmp_.json()["error"]["code"] == "TERM_SHEET_NOT_CONFIRMED"
    edit = client.patch(f"{TS}/{sheet['id']}", json={"label": "x"}, headers=founder)
    assert edit.status_code == 409 and edit.json()["error"]["code"] == "NOT_CONFIRMED"


def test_confirm_takes_the_founders_values_not_the_proposal(client, db, team, founder):
    f = uploaded(db, team)
    sheet = client.post(f"{TS}/extract", json={"file_id": str(f.id)}, headers=founder).json()[
        "data"
    ]["term_sheet"]
    early = client.post(
        f"{TS}/{sheet['id']}/confirm", json={"terms": {"stage": "seed"}}, headers=founder
    )
    assert early.status_code == 409 and early.json()["error"]["code"] == "NOT_AWAITING_CONFIRMATION"
    _drain(db)
    corrected = {
        "stage": "seed",
        "pre_money_minor": 40_000_000_000,
        "liq_pref_x100": 100,
        "participating": False,
    }
    r = client.post(
        f"{TS}/{sheet['id']}/confirm",
        json={"terms": corrected, "label": "Alpha, corrected"},
        headers=founder,
    )
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["status"] == "confirmed" and d["label"] == "Alpha, corrected" and d["confirmed_at"]
    assert d["terms"] == corrected and d["flags"] == [] and d["extracted_by_ai"] is True
    assert d["proposed"] is None
    again = client.post(f"{TS}/{sheet['id']}/confirm", json={"terms": corrected}, headers=founder)
    assert again.status_code == 409  # a double click confirms once
    log = client.get(f"{TS}/audit", headers=founder).json()["data"]["items"]
    assert [x["action"] for x in reversed(log)] == [
        "create",
        "update",
        "confirm",
    ]  # update = the job


def test_confirming_announces_once_and_flags_what_the_founder_confirmed(client, db, team, founder):
    s = team[0]
    second, _ = _member(db, s, MembershipRole.founder)
    db.commit()
    f = uploaded(db, team)
    sheet = client.post(f"{TS}/extract", json={"file_id": str(f.id)}, headers=founder).json()[
        "data"
    ]["term_sheet"]
    _drain(db)
    assert db.query(Notification).filter_by(type="funding.term_sheet.added").count() == 0
    proposal = client.get(f"{TS}/{sheet['id']}", headers=founder).json()["data"]["proposed"][
        "terms"
    ]
    r = client.post(f"{TS}/{sheet['id']}/confirm", json={"terms": proposal}, headers=founder)
    rules = [f["rule"] for f in r.json()["data"]["flags"]]
    assert len(rules) == 6 and "option_pool_above_15pct" not in rules  # 12.5% is under 15%
    assert (
        db.query(Notification).filter_by(type="funding.term_sheet.added", user_id=second.id).count()
        == 1
    )


def test_the_same_pdf_is_not_extracted_twice(client, db, team, founder, llm):
    f = uploaded(db, team)
    first = client.post(f"{TS}/extract", json={"file_id": str(f.id)}, headers=founder)
    second = client.post(f"{TS}/extract", json={"file_id": str(f.id)}, headers=founder)
    assert second.status_code == 200 and second.json()["data"]["deduplicated"] is True
    assert second.json()["data"]["term_sheet"]["id"] == first.json()["data"]["term_sheet"]["id"]
    _drain(db)
    third = client.post(f"{TS}/extract", json={"file_id": str(f.id)}, headers=founder)
    assert third.json()["data"]["deduplicated"] is True  # a finished one is the answer too
    assert len(llm.calls) == 1 and db.query(TermSheet).count() == 1


def test_extraction_input_checks(client, db, team, founder):
    f = uploaded(db, team)
    other = client.post(
        f"{TS}/extract", json={"file_id": "00000000-0000-4000-8000-000000000000"}, headers=founder
    )
    assert other.status_code == 404
    from app.db.models.document import DocumentFile
    from app.db.models.enums import ScanStatus

    db.query(DocumentFile).filter_by(id=f.id).update({"scan_status": ScanStatus.pending})
    db.commit()
    r = client.post(f"{TS}/extract", json={"file_id": str(f.id)}, headers=founder)
    assert r.status_code == 409
    db.query(DocumentFile).filter_by(id=f.id).update(
        {"scan_status": ScanStatus.clean, "content_type": "text/plain"}
    )
    db.commit()
    r = client.post(f"{TS}/extract", json={"file_id": str(f.id)}, headers=founder)
    assert r.status_code == 422
    db.query(DocumentFile).filter_by(id=f.id).update(
        {"content_type": "application/pdf", "trashed_at": datetime.now(UTC)}
    )
    db.commit()
    assert (
        client.post(f"{TS}/extract", json={"file_id": str(f.id)}, headers=founder).status_code
        == 404
    )


def test_a_scanned_pdf_with_no_text_fails_cleanly_and_can_be_retried(
    client, db, team, founder, llm
):
    f = uploaded(db, team, text=["x"], name="scan.pdf")
    sheet = client.post(f"{TS}/extract", json={"file_id": str(f.id)}, headers=founder).json()[
        "data"
    ]["term_sheet"]
    _drain(db)
    got = client.get(f"{TS}/{sheet['id']}", headers=founder).json()["data"]
    assert got["status"] == "failed" and got["error"] == "no_text" and llm.calls == []
    again = client.post(f"{TS}/extract", json={"file_id": str(f.id)}, headers=founder)
    assert again.status_code == 202  # a failed one does not block a retry


def test_a_model_failure_marks_the_sheet_failed_on_the_last_attempt(
    client, db, team, founder, monkeypatch
):
    monkeypatch.setattr(
        llm_budget, "get_llm_client", lambda: TermLLM(boom=RuntimeError("provider down"))
    )
    monkeypatch.setattr(settings, "WORKER_MAX_ATTEMPTS", 1)
    f = uploaded(db, team)
    sheet = client.post(f"{TS}/extract", json={"file_id": str(f.id)}, headers=founder).json()[
        "data"
    ]["term_sheet"]
    _drain(db)
    got = client.get(f"{TS}/{sheet['id']}", headers=founder).json()["data"]
    assert got["status"] == "failed" and got["error"] == "generation_failed"


def test_a_dead_job_does_not_leave_the_sheet_extracting_forever(client, db, team, founder):
    f = uploaded(db, team)
    sheet = client.post(f"{TS}/extract", json={"file_id": str(f.id)}, headers=founder).json()[
        "data"
    ]["term_sheet"]
    db.query(Job).filter_by(id=sheet["job_id"]).delete()
    db.commit()
    got = client.get(f"{TS}/{sheet['id']}", headers=founder).json()["data"]
    assert got["status"] == "failed" and got["error"] == "job_failed"


def test_extraction_refuses_when_the_daily_allowance_is_used(
    client, db, team, founder, monkeypatch
):
    monkeypatch.setattr(svc, "over_budget", lambda db, sid: True)
    f = uploaded(db, team)
    r = client.post(f"{TS}/extract", json={"file_id": str(f.id)}, headers=founder)
    assert r.status_code == 429 and r.json()["error"]["code"] == "AI_BUDGET_EXHAUSTED"
    assert db.query(TermSheet).count() == 0


# ---------------------------------------------------------------- wording
def test_ai_wording_is_added_to_the_flags_and_the_fixed_text_stays(client, db, founder, llm):
    d = typed(client, founder, liq_pref_x100=200, exclusivity_days=60)
    assert all(f["ai_text"] is None for f in d["flags"])
    _drain(db)
    got = client.get(f"{TS}/{d['id']}", headers=founder).json()["data"]
    for f in got["flags"]:
        assert (
            f["ai_text"].startswith("In plain words:") and f["text"] and f["ai_text"] != f["text"]
        )
    assert [f["rule"] for f in got["flags"]] == ["liq_pref_above_1x", "exclusivity_above_45_days"]


def test_wording_that_moves_a_number_is_ignored(client, db, founder, monkeypatch):
    bad = {"flags": [{"rule": "liq_pref_above_1x", "text": "They get 3x back first."}]}
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: TermLLM(wording=bad))
    d = typed(client, founder, liq_pref_x100=200)
    _drain(db)
    got = client.get(f"{TS}/{d['id']}", headers=founder).json()["data"]
    assert got["flags"][0]["ai_text"] is None and "2x" in got["flags"][0]["text"]


def test_wording_for_edited_terms_is_dropped_as_stale(client, db, founder, llm):
    d = typed(client, founder, liq_pref_x100=200)
    client.patch(
        f"{TS}/{d['id']}", json={"terms": {"stage": "seed", "liq_pref_x100": 300}}, headers=founder
    )
    _drain(db)
    got = client.get(f"{TS}/{d['id']}", headers=founder).json()["data"]
    assert got["flags"][0]["value"] == 300 and "3x" in (got["flags"][0]["ai_text"] or "3x")


def test_a_wording_failure_changes_nothing(client, db, founder, monkeypatch):
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: TermLLM(boom=RuntimeError("down")))
    monkeypatch.setattr(settings, "WORKER_MAX_ATTEMPTS", 1)
    d = typed(client, founder, liq_pref_x100=200)
    _drain(db)
    got = client.get(f"{TS}/{d['id']}", headers=founder).json()["data"]
    assert (
        got["status"] == "confirmed"
        and got["flags"][0]["ai_text"] is None
        and got["flags"][0]["text"]
    )


def test_no_wording_job_for_a_clean_sheet(client, db, founder, llm):
    typed(client, founder, liq_pref_x100=100)
    assert db.query(Job).filter_by(type=svc.WORDING_JOB).count() == 0


# ---------------------------------------------------------------- comparison
def test_compare_two_to_six_confirmed_sheets(client, founder):
    a = typed(
        client,
        founder,
        "A",
        pre_money_minor=40_000_000,
        investment_minor=10_000_000,
        liq_pref_x100=100,
    )
    b = typed(
        client,
        founder,
        "B",
        pre_money_minor=50_000_000,
        investment_minor=10_000_000,
        liq_pref_x100=200,
        participating=True,
    )
    r = client.get(f"{TS}/compare?ids={a['id']},{b['id']}", headers=founder)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert [c["label"] for c in d["columns"]] == ["A", "B"] and d["currency"] == "NGN"
    pre = next(x for x in d["rows"] if x["term"] == "pre_money_minor")
    assert pre["best"] == [b["id"]] and pre["worst"] == [a["id"]]
    own = next(x for x in d["rows"] if x["term"] == "investor_ownership_bps")
    assert own["values"] == {a["id"]: 2000, b["id"]: 1667}
    assert [f["rule"] for f in d["flags"][b["id"]]] == [
        "liq_pref_above_1x",
        "participating_preferred",
    ]
    assert "Negotiate with clarity" in d["disclaimer"]
    one = client.get(f"{TS}/compare?ids={a['id']}", headers=founder)
    assert one.status_code == 422
    seven = ",".join([a["id"]] + [typed(client, founder, f"S{i}")["id"] for i in range(6)])
    assert client.get(f"{TS}/compare?ids={seven}", headers=founder).status_code == 422
    assert client.get(f"{TS}/compare?ids=nope,nada", headers=founder).status_code == 422
    assert (
        client.get(
            f"{TS}/compare?ids={a['id']},00000000-0000-4000-8000-000000000000", headers=founder
        ).status_code
        == 404
    )


def test_comparing_the_same_sheet_twice_is_not_a_comparison(client, founder):
    a = typed(client, founder, "A")
    assert client.get(f"{TS}/compare?ids={a['id']},{a['id']}", headers=founder).status_code == 422


# ---------------------------------------------------------------- currency
def test_the_round_currency_is_fixed_once_a_sheet_states_money(client, founder):
    _resp4 = client.put(f"{BASE}/round", json={"currency": "USD"}, headers=founder)
    assert _resp4.status_code == 200
    typed(client, founder, pre_money_minor=100)
    r = client.put(f"{BASE}/round", json={"currency": "NGN"}, headers=founder)
    assert r.status_code == 409 and r.json()["error"]["code"] == "CAP_TABLE_HAS_AMOUNTS"
    assert "term sheets" in r.json()["error"]["message"]
    assert client.get(TS, headers=founder).json()["data"]["currency"] == "USD"


def test_a_sheet_with_no_money_does_not_lock_the_currency(client, founder):
    typed(client, founder, liq_pref_x100=200)
    _resp5 = client.put(f"{BASE}/round", json={"currency": "USD"}, headers=founder)
    assert _resp5.status_code == 200


# ---------------------------------------------------------------- review fixes
def _extract(client, db, team, founder, **kw):
    f = uploaded(db, team, **kw)
    sheet = client.post(f"{TS}/extract", json={"file_id": str(f.id)}, headers=founder).json()[
        "data"
    ]["term_sheet"]
    _drain(db)
    return sheet["id"]


def _with_currency(monkeypatch, code):
    payload = TermLLM().extraction
    if code is None:
        payload.pop("currency")
    else:
        payload["currency"] = code
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: TermLLM(extraction=payload))


def test_a_document_in_another_currency_is_flagged_and_needs_an_acknowledgement(
    client, db, team, founder, monkeypatch
):
    _with_currency(monkeypatch, "USD")
    sid = _extract(client, db, team, founder)
    d = client.get(f"{TS}/{sid}", headers=founder).json()["data"]
    assert d["proposed"]["currency"] == "USD"
    assert (
        d["currency_mismatch"] is True
        and d["document_currency"] == "USD"
        and d["round_currency"] == "NGN"
    )
    terms = d["proposed"]["terms"]
    refused = client.post(f"{TS}/{sid}/confirm", json={"terms": terms}, headers=founder)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "CURRENCY_MISMATCH"
    assert (
        "USD" in refused.json()["error"]["message"] and "NGN" in refused.json()["error"]["message"]
    )
    assert (
        client.get(f"{TS}/{sid}", headers=founder).json()["data"]["status"] == "needs_confirmation"
    )
    ok = client.post(
        f"{TS}/{sid}/confirm",
        json={"terms": terms, "acknowledge_currency_mismatch": True},
        headers=founder,
    )
    assert ok.status_code == 200 and ok.json()["data"]["status"] == "confirmed"
    assert ok.json()["data"]["currency_mismatch"] is False  # no longer a proposal


def test_a_mismatch_does_not_block_a_sheet_with_no_money(client, db, team, founder, monkeypatch):
    _with_currency(monkeypatch, "USD")
    sid = _extract(client, db, team, founder)
    r = client.post(
        f"{TS}/{sid}/confirm",
        json={"terms": {"stage": "seed", "liq_pref_x100": 100}},
        headers=founder,
    )
    assert r.status_code == 200


def test_the_same_or_an_unstated_currency_is_no_mismatch(client, db, team, founder, monkeypatch):
    _with_currency(monkeypatch, "NGN")
    same = client.get(f"{TS}/{_extract(client, db, team, founder)}", headers=founder).json()["data"]
    assert same["currency_mismatch"] is False and same["document_currency"] is None
    _with_currency(monkeypatch, None)
    other = _extract(
        client, db, team, founder, text=["Another term sheet. " * 20, "Pre-money 5m. " * 20]
    )
    d = client.get(f"{TS}/{other}", headers=founder).json()["data"]
    assert d["proposed"]["currency"] is None and d["currency_mismatch"] is False


def test_the_mismatch_follows_the_round_currency(client, db, team, founder, monkeypatch):
    client.put(f"{BASE}/round", json={"currency": "USD"}, headers=founder)
    _with_currency(monkeypatch, "USD")
    d = client.get(f"{TS}/{_extract(client, db, team, founder)}", headers=founder).json()["data"]
    assert d["currency_mismatch"] is False


def test_the_wording_write_takes_the_row_lock_after_the_model_returns(
    client, db, founder, monkeypatch
):
    from sqlalchemy import event

    seen: list[str] = []

    def spy(conn, cursor, statement, params, context, many):
        if "term_sheets" in statement and statement.lstrip().upper().startswith("SELECT"):
            seen.append("LOCK" if "FOR UPDATE" in statement.upper() else "READ")

    llm = TermLLM(on_wording=lambda: seen.append("MODEL"))
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: llm)
    d = typed(client, founder, liq_pref_x100=200)
    event.listen(db.get_bind(), "before_cursor_execute", spy)
    try:
        _drain(db)
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", spy)
    after_model = seen[seen.index("MODEL") + 1 :]
    assert "LOCK" in after_model, seen  # the refresh that precedes the comparison and the write
    got = client.get(f"{TS}/{d['id']}", headers=founder).json()["data"]
    assert got["flags"][0]["ai_text"]


def test_an_edit_that_lands_while_the_model_runs_is_not_overwritten(
    client, db, founder, monkeypatch
):
    d = typed(client, founder, liq_pref_x100=200)

    def racing_edit():
        r = client.patch(
            f"{TS}/{d['id']}",
            json={"terms": {"stage": "seed", "liq_pref_x100": 300}},
            headers=founder,
        )
        assert r.status_code == 200

    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: TermLLM(on_wording=racing_edit))
    _drain(db)
    got = client.get(f"{TS}/{d['id']}", headers=founder).json()["data"]
    assert got["terms"]["liq_pref_x100"] == 300 and got["flags"][0]["value"] == 300
    # the v1 wording ("2x") was dropped; only wording made for the v2 terms may be present
    assert all("2x" not in (f["ai_text"] or "") for f in got["flags"])


def test_extraction_reads_the_file_before_it_takes_the_workspace_lock(
    client, db, team, founder, monkeypatch
):
    f = uploaded(db, team)
    order: list[str] = []
    real_lock, real_storage = svc.lock_workspace, svc.get_storage

    class Spy:
        def __init__(self, inner):
            self.inner = inner

        def read_private(self, key):
            order.append("read")
            return self.inner.read_private(key)

    monkeypatch.setattr(
        svc, "lock_workspace", lambda db_, sid: (order.append("lock"), real_lock(db_, sid))[1]
    )
    monkeypatch.setattr(svc, "get_storage", lambda: Spy(real_storage()))
    r = client.post(f"{TS}/extract", json={"file_id": str(f.id)}, headers=founder)
    assert r.status_code == 202
    assert order.index("read") < order.index("lock")


def test_two_extractions_of_one_file_still_make_one_sheet(client, db, team, founder):
    f = uploaded(db, team)
    a = client.post(f"{TS}/extract", json={"file_id": str(f.id)}, headers=founder)
    b = client.post(f"{TS}/extract", json={"file_id": str(f.id)}, headers=founder)
    assert (a.status_code, b.status_code) == (202, 200)
    assert db.query(TermSheet).count() == 1


def test_the_system_changes_to_an_extraction_are_audited_without_an_actor(
    client, db, team, founder
):
    sid = _extract(client, db, team, founder)
    log = client.get(f"{TS}/audit", headers=founder).json()["data"]["items"]
    system = [x for x in log if x["actor_id"] is None]
    assert len(system) == 1 and system[0]["action"] == "update"
    assert (
        system[0]["before"]["status"] == "extracting"
        and system[0]["after"]["status"] == "needs_confirmation"
    )
    assert system[0]["term_sheet_id"] == sid


def test_a_failed_extraction_is_audited_too(client, db, team, founder):
    _extract(client, db, team, founder, text=["x"], name="scan.pdf")
    log = client.get(f"{TS}/audit", headers=founder).json()["data"]["items"]
    system = [x for x in log if x["actor_id"] is None]
    assert system and system[0]["after"]["status"] == "failed"


def test_a_patch_that_changes_nothing_leaves_no_audit_row(client, founder):
    d = typed(client, founder, liq_pref_x100=200)
    before = client.get(f"{TS}/audit", headers=founder).json()["data"]["total"]
    same = client.patch(
        f"{TS}/{d['id']}", json={"label": d["label"], "terms": d["terms"]}, headers=founder
    )
    assert same.status_code == 200
    empty = client.patch(f"{TS}/{d['id']}", json={}, headers=founder)
    assert empty.status_code == 200
    assert client.get(f"{TS}/audit", headers=founder).json()["data"]["total"] == before
    client.patch(f"{TS}/{d['id']}", json={"label": "Renamed"}, headers=founder)
    assert client.get(f"{TS}/audit", headers=founder).json()["data"]["total"] == before + 1


def test_failed_sheets_do_not_count_toward_the_cap(client, db, team, founder, monkeypatch):
    monkeypatch.setattr(svc, "MAX_SHEETS", 2)
    typed(client, founder, "One")
    _extract(client, db, team, founder, text=["x"], name="scan.pdf")  # fails: no text
    assert db.query(TermSheet).filter_by(status="failed").count() == 1
    typed(client, founder, "Two")  # live count is 1 -> allowed
    full = client.post(TS, json={"label": "Three", "terms": {}}, headers=founder)
    assert full.status_code == 422  # two live sheets: now full
    assert db.query(TermSheet).count() == 3
