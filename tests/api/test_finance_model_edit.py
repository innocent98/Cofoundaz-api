from datetime import UTC, datetime

import pytest

from app.core.config import settings
from app.db.models.enums import (
    FinancialModelStatus,
    MembershipRole,
    TransactionDirection,
    TransactionSource,
)
from app.db.models.finance import Transaction
from app.db.models.financial_model import FinancialModel
from app.db.models.job import Job, JobStatus
from app.worker.handlers import finance_model as handler_mod
from app.worker.handlers.finance_model import handle_finance_model
from tests.api.test_finance import BASE, _member

MODEL = f"{BASE}/model"


def _tx(db, startup_id, amount, direction, category=None):
    db.add(
        Transaction(
            startup_id=startup_id,
            date=datetime.now(UTC).date(),
            description="seed",
            category=category,
            amount_minor=amount,
            currency="NGN",
            direction=direction,
            source=TransactionSource.manual,
        )
    )


def _complete_model(db, monkeypatch, *, role=MembershipRole.founder, startup=None):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    u, s, h = _member(db, role=role, startup=startup)
    if startup is None:
        _tx(db, s.id, 5_000_000, TransactionDirection.inflow, "Funding")
        _tx(db, s.id, 900_000, TransactionDirection.inflow, "Revenue")
        _tx(db, s.id, 600_000, TransactionDirection.outflow, "Rent")
    m = FinancialModel(
        startup_id=s.id, created_by=u.id, status=FinancialModelStatus.generating, horizon_months=36
    )
    db.add(m)
    db.flush()
    handle_finance_model(
        db, Job(type="ai.finance.model", payload={"model_id": str(m.id)}, status=JobStatus.running)
    )
    db.flush()
    return u, s, h, m


def _row(stmt, label):
    return next(r["values"] for r in stmt["rows"] if r["label"] == label)


def test_generated_model_is_36_months_with_a_checked_balance_sheet_and_yearly_rollup(
    client, db, monkeypatch
):
    _u, _s, h, m = _complete_model(db, monkeypatch)
    data = client.get(f"{MODEL}/{m.id}", headers=h).json()["data"]
    assert data["horizon_months"] == 36 and len(data["months"]) == 36
    assert data["balance_check"] == {"balanced": True, "max_abs_difference_minor": 0}
    assert data["stale"] is False and data["overrides"] is None
    bs = data["balance_sheet"]
    assert _row(bs, "Balance check") == [0] * 36
    assert _row(bs, "Paid-in capital")[0] == 5_000_000  # the ledger's funding row
    assert [p["label"] for p in data["yearly"]["periods"]] == ["Year 1", "Year 2", "Year 3"]
    rev = _row(data["pnl"], "Revenue")
    assert _row(data["yearly"]["pnl"], "Revenue") == [
        sum(rev[:12]),
        sum(rev[12:24]),
        sum(rev[24:]),
    ]
    assert _row(data["yearly"]["balance_sheet"], "Balance check") == [0, 0, 0]
    assert data["ai_assumptions"]["monthly_revenue_growth_pct"] == 0


def test_edit_then_recalculate_changes_numbers_without_any_ai_call(client, db, monkeypatch):
    _u, _s, h, m = _complete_model(db, monkeypatch)
    before = client.get(f"{MODEL}/{m.id}", headers=h).json()["data"]
    jobs_before = db.query(Job).count()

    def boom(*a, **k):
        raise AssertionError("recalculate must not call the AI")

    monkeypatch.setattr(handler_mod, "metered_complete_json", boom)
    month = before["months"][2]
    resp = client.patch(
        f"{MODEL}/{m.id}/assumptions",
        json={
            "monthly_revenue_growth_pct": 8,
            "cogs_pct_of_revenue": 20,
            "hiring_plan": [
                {"role": "Engineer", "start_month": month, "monthly_cost_minor": 150_000}
            ],
            "capex": [{"label": "Laptops", "month": month, "amount_minor": 480_000}],
        },
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    edited = resp.json()["data"]
    assert edited["stale"] is True
    assert edited["pnl"] == before["pnl"]  # saving alone does not recalculate
    assert edited["assumptions"]["monthly_revenue_growth_pct"] == 8
    assert edited["ai_assumptions"]["monthly_revenue_growth_pct"] == 0
    assert edited["overrides"]["cogs_pct_of_revenue"] == 20

    done = client.post(f"{MODEL}/{m.id}/recalculate", headers=h)
    assert done.status_code == 200, done.text
    after = done.json()["data"]
    assert after["stale"] is False and after["recalculated_at"] is not None
    assert _row(after["pnl"], "Revenue")[-1] > _row(before["pnl"], "Revenue")[-1]
    assert _row(after["cash_flow"], "Capex")[2] == 480_000
    assert _row(after["pnl"], "Depreciation")[3] > 0
    assert after["balance_check"]["balanced"] is True
    assert after["months"] == before["months"]  # the window does not drift
    assert db.query(Job).count() == jobs_before  # no job queued either


def test_recalculate_is_deterministic(client, db, monkeypatch):
    _u, _s, h, m = _complete_model(db, monkeypatch)
    client.patch(f"{MODEL}/{m.id}/assumptions", json={"ap_days": 40}, headers=h)
    a = client.post(f"{MODEL}/{m.id}/recalculate", headers=h).json()["data"]
    b = client.post(f"{MODEL}/{m.id}/recalculate", headers=h).json()["data"]
    for key in ("pnl", "cash_flow", "balance_sheet", "yearly"):
        assert a[key] == b[key]


def test_null_clears_an_edit_and_start_overrides_apply(client, db, monkeypatch):
    _u, _s, h, m = _complete_model(db, monkeypatch)
    base = client.get(f"{MODEL}/{m.id}", headers=h).json()["data"]
    resp = client.patch(
        f"{MODEL}/{m.id}/assumptions",
        json={"revenue_0_minor": 2_000_000, "monthly_costs_0_minor": 1_000_000},
        headers=h,
    )
    assert resp.json()["data"]["assumptions"]["revenue_0_minor"] == 2_000_000
    out = client.post(f"{MODEL}/{m.id}/recalculate", headers=h).json()["data"]
    assert _row(out["pnl"], "Revenue")[0] == 2_000_000
    cleared = client.patch(
        f"{MODEL}/{m.id}/assumptions",
        json={"revenue_0_minor": None, "monthly_costs_0_minor": None},
        headers=h,
    ).json()["data"]
    assert cleared["overrides"] is None
    back = client.post(f"{MODEL}/{m.id}/recalculate", headers=h).json()["data"]
    assert back["pnl"] == base["pnl"]


def test_refresh_actuals_rereads_the_ledger(client, db, monkeypatch):
    _u, s, h, m = _complete_model(db, monkeypatch)
    before = client.get(f"{MODEL}/{m.id}", headers=h).json()["data"]
    _tx(db, s.id, 7_000_000, TransactionDirection.inflow, "Funding")
    db.flush()
    plain = client.post(f"{MODEL}/{m.id}/recalculate", headers=h).json()["data"]
    assert _row(plain["balance_sheet"], "Paid-in capital")[0] == 5_000_000
    fresh = client.post(
        f"{MODEL}/{m.id}/recalculate", json={"refresh_actuals": True}, headers=h
    ).json()["data"]
    assert _row(fresh["balance_sheet"], "Paid-in capital")[0] == 12_000_000
    assert (
        _row(fresh["cash_flow"], "Opening cash")[0] > _row(before["cash_flow"], "Opening cash")[0]
    )
    assert fresh["balance_check"]["balanced"] is True


def test_legacy_12_month_model_upgrades_on_recalculate(client, db, monkeypatch):
    _u, s, h, m = _complete_model(db, monkeypatch)
    row = db.get(FinancialModel, m.id)
    months = row.pnl["months"][:12]
    row.starting = None
    row.horizon_months = 12
    row.pnl = {"months": months, "rows": [{"label": "Revenue", "values": [1] * 12}]}
    row.cash_flow = row.balance_sheet = None
    db.flush()
    old = client.get(f"{MODEL}/{m.id}", headers=h).json()["data"]
    assert old["balance_check"] is None  # no check line on a model generated before this
    assert old["yearly"]["periods"] == [
        {"label": "Year 1", "start_month": months[0], "end_month": months[-1]}
    ]
    new = client.post(f"{MODEL}/{m.id}/recalculate", headers=h).json()["data"]
    assert new["horizon_months"] == 36 and len(new["months"]) == 36
    assert new["months"][:12] == months  # original month labels kept
    assert new["balance_check"]["balanced"] is True


@pytest.mark.parametrize(
    ("patch", "field"),
    [
        ({"monthly_revenue_growth_pct": 500}, "monthly_revenue_growth_pct"),
        ({"cogs_pct_of_revenue": -1}, "cogs_pct_of_revenue"),
        ({"ar_days": 400}, "ar_days"),
        ({"depreciation_months": 2}, "depreciation_months"),
        ({"revenue_0_minor": -5}, "revenue_0_minor"),
        ({"hiring_plan": [{"role": "x", "start_month": "2026-13", "monthly_cost_minor": 1}]}, None),
        ({"one_offs": [{"label": "x", "month": "2026-05", "amount_minor": 0}]}, None),
        ({"unknown_field": 1}, None),
        ({"monthly_revenue_growth_pct": "abc"}, "monthly_revenue_growth_pct"),
    ],
)
def test_invalid_edits_are_422(client, db, monkeypatch, patch, field):
    _u, _s, h, m = _complete_model(db, monkeypatch)
    resp = client.patch(f"{MODEL}/{m.id}/assumptions", json=patch, headers=h)
    assert resp.status_code == 422, resp.text
    assert db.get(FinancialModel, m.id).overrides is None


def test_months_outside_the_projection_are_422_with_a_field_path(client, db, monkeypatch):
    _u, _s, h, m = _complete_model(db, monkeypatch)
    inside = client.get(f"{MODEL}/{m.id}", headers=h).json()["data"]["months"][0]
    resp = client.patch(
        f"{MODEL}/{m.id}/assumptions",
        json={
            "capex": [
                {"label": "ok", "month": inside, "amount_minor": 5},
                {"label": "late", "month": "2099-01", "amount_minor": 5},
            ]
        },
        headers=h,
    )
    assert resp.status_code == 422
    errs = resp.json()["error"]["field_errors"]
    assert errs[0]["field"] == "capex[1].month"
    assert "Choose a month between" in errs[0]["message"]


def test_too_many_rows_is_422(client, db, monkeypatch):
    _u, _s, h, m = _complete_model(db, monkeypatch)
    month = client.get(f"{MODEL}/{m.id}", headers=h).json()["data"]["months"][0]
    rows = [{"label": "x", "month": month, "amount_minor": 1}] * 51
    assert (
        client.patch(f"{MODEL}/{m.id}/assumptions", json={"one_offs": rows}, headers=h).status_code
        == 422
    )


def test_models_not_ready_cannot_be_edited(client, db, monkeypatch):
    u, s, h = _member(db)
    m = FinancialModel(
        startup_id=s.id, created_by=u.id, status=FinancialModelStatus.generating, horizon_months=36
    )
    db.add(m)
    db.flush()
    for call in (
        client.patch(f"{MODEL}/{m.id}/assumptions", json={"ar_days": 1}, headers=h),
        client.post(f"{MODEL}/{m.id}/recalculate", headers=h),
    ):
        assert call.status_code == 409
        assert call.json()["error"]["code"] == "MODEL_NOT_READY"


@pytest.mark.parametrize("role", [MembershipRole.team_member, MembershipRole.accountant])
def test_finance_roles_can_edit(client, db, monkeypatch, role):
    _u, s, _h, m = _complete_model(db, monkeypatch)
    _u2, _s2, h2 = _member(db, role=role, startup=s)
    assert (
        client.patch(f"{MODEL}/{m.id}/assumptions", json={"ar_days": 9}, headers=h2).status_code
        == 200
    )
    assert client.post(f"{MODEL}/{m.id}/recalculate", headers=h2).status_code == 200


@pytest.mark.parametrize(
    "role", [MembershipRole.mentor, MembershipRole.investor, MembershipRole.business_consultant]
)
def test_other_roles_are_403(client, db, monkeypatch, role):
    _u, s, _h, m = _complete_model(db, monkeypatch)
    _u2, _s2, h2 = _member(db, role=role, startup=s)
    assert (
        client.patch(f"{MODEL}/{m.id}/assumptions", json={"ar_days": 9}, headers=h2).status_code
        == 403
    )
    assert client.post(f"{MODEL}/{m.id}/recalculate", headers=h2).status_code == 403


def test_another_workspaces_model_is_404(client, db, monkeypatch):
    _u, _s, _h, m = _complete_model(db, monkeypatch)
    _u2, _s2, other = _member(db)
    assert (
        client.patch(f"{MODEL}/{m.id}/assumptions", json={"ar_days": 9}, headers=other).status_code
        == 404
    )
    assert client.post(f"{MODEL}/{m.id}/recalculate", headers=other).status_code == 404


def test_unauthenticated_is_401(client):
    assert (
        client.post(f"{MODEL}/00000000-0000-0000-0000-000000000000/recalculate").status_code == 401
    )
