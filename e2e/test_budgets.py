"""Live Finance Hub budgets journey (Module 12, Slice 4b).

`test_finance_budgets_journey`: a founder onboards, logs expenses in three categories dated LAST
month, and then walks the whole budgets lifecycle:

  draft-from-actuals for THIS month (one budget per last-month category, limit == that category's
  last-month expense total) -> re-running it is a no-op (skip-existing)
  -> log expenses THIS month (incl. a lower-case category variant and a manual outflow that must
     NOT count) -> GET the month: spent / variance / over_budget / percent_used are DERIVED from
     this month's expenses on every read
  -> create a budget (spent already reflects existing expenses) -> PATCH the limit so
     `over_budget` flips -> GET one to confirm -> a zero-limit budget (`percent_used: null`)
     -> a limit above the int32 range (budgets are aggregates: 64-bit)
  -> copy-last-month into NEXT month (skips a category that already has a budget; never
     overwrites its limit) -> DELETE a budget.

It also captures the error shapes the FE has to handle (duplicate category+month, validation,
explicit null on `limit_minor`, bad month, missing body, unknown id, 401).

Every response body is captured to `e2e/_captures/budgets/*.json` -- those files are the verbatim
source for docs/fe-integration-guide-finance-budgets.md. They must be REAL bodies from this live
run, complete and untrimmed.

Finance is founder/team_member/accountant RBAC, so this needs nothing but a freshly onboarded
founder + workspace. Dates are computed relative to today (UTC, the server's clock): expenses
"this month" are dated today; "last month" expenses are dated the 15th of the previous month.
"""

import uuid
from datetime import UTC, date, datetime

import httpx


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _onboard_steps(c: httpx.Client, auth: dict, *, stage: str, name: str) -> None:
    """Walk steps 1-4 of the wizard (same shape as e2e/test_expenses.py)."""
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Founder"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": name})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": stage},
    )
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 4, "goals": ["Get first customers"]},
    )


def _shift_month(today: date, months: int) -> date:
    """The 15th of the month `months` months from `today`'s month (negative = past)."""
    idx = today.year * 12 + (today.month - 1) + months
    return date(idx // 12, idx % 12 + 1, 15)


def test_finance_budgets_journey(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        # 0. Onboard a founder -- finance needs nothing else.
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)

        _onboard_steps(c, auth, stage="validation", name="Cofoundaz Budgets")
        onboarded = c.post("/api/v1/onboarding/complete", headers=auth)
        assert onboarded.status_code == 200, onboarded.text

        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wh = {**auth, "X-Workspace-Id": me["active_workspace_id"]}

        today = datetime.now(UTC).date()
        this_month = today.strftime("%Y-%m")
        last_month_day = _shift_month(today, -1)
        next_month = _shift_month(today, 1).strftime("%Y-%m")

        def _expense(vendor: str, category: str, when: date, amount: int) -> dict:
            r = c.post(
                "/api/v1/finance/expenses",
                headers=wh,
                json={
                    "vendor": vendor,
                    "category": category,
                    "expense_date": when.isoformat(),
                    "amount_minor": amount,
                },
            )
            assert r.status_code == 200, r.text
            return r.json()["data"]

        # 1. Seed LAST month's expenses in three categories so draft-from-actuals has a source.
        # Infrastructure is two expenses (6M + 2M = 8M) to prove the draft sums them.
        _expense("AWS", "Infrastructure", last_month_day, 6_000_000)
        _expense("Vercel", "Infrastructure", last_month_day, 2_000_000)
        _expense("Google Ads", "Marketing", last_month_day, 5_000_000)
        _expense("Notion", "Software", last_month_day, 1_500_000)

        # 2. Draft THIS month's budgets from last month's actuals: one per category, limit == that
        # category's last-month total, currency NGN, sorted by category, spent 0 (nothing this
        # month yet).
        draft = c.post(
            "/api/v1/finance/budgets/draft-from-actuals",
            headers=wh,
            json={"period_month": this_month},
        )
        assert draft.status_code == 200, draft.text
        d = draft.json()["data"]
        assert d["month"] == this_month
        assert [(b["category"], b["limit_minor"]) for b in d["budgets"]] == [
            ("Infrastructure", 8_000_000),
            ("Marketing", 5_000_000),
            ("Software", 1_500_000),
        ]
        for b in d["budgets"]:
            assert b["period_month"] == this_month
            assert b["currency"] == "NGN"
            assert b["notes"] is None
            assert b["spent_minor"] == 0
            assert b["variance_minor"] == -b["limit_minor"]
            assert b["over_budget"] is False
            assert b["percent_used"] == 0.0
        capture("budgets", "draft_from_actuals", draft)
        infra_id, marketing_id, software_id = (b["id"] for b in d["budgets"])

        # 2b. Running the draft again is a no-op: every category already has a budget this month, so
        # nothing is created and nothing is overwritten.
        draft_again = c.post(
            "/api/v1/finance/budgets/draft-from-actuals",
            headers=wh,
            json={"period_month": this_month},
        )
        assert draft_again.status_code == 200, draft_again.text
        assert draft_again.json()["data"] == {"month": this_month, "budgets": []}
        capture("budgets", "draft_from_actuals_again_skips_existing", draft_again)

        # 2c. A month whose previous month has no expenses drafts nothing.
        draft_empty = c.post(
            "/api/v1/finance/budgets/draft-from-actuals",
            headers=wh,
            json={"period_month": "2020-02"},
        )
        assert draft_empty.status_code == 200, draft_empty.text
        assert draft_empty.json()["data"] == {"month": "2020-02", "budgets": []}
        capture("budgets", "draft_from_actuals_empty_source", draft_empty)

        # 3. Log THIS month's expenses (dated today). Infrastructure 9M is OVER its 8M limit;
        # Marketing 2M is under 5M; Software has none; Payroll has spend but no budget yet.
        _expense("AWS", "Infrastructure", today, 9_000_000)
        _expense("Google Ads", "Marketing", today, 2_000_000)
        _expense("Team salaries", "Payroll", today, 3_000_000)
        # A LOWER-CASE category is a different string: it does NOT count toward "Infrastructure".
        _expense("Hetzner", "infrastructure", today, 1_000_000)
        # A manual outflow transaction that is NOT logged as an expense does NOT count either.
        manual = c.post(
            "/api/v1/finance/transactions",
            headers=wh,
            json={
                "date": today.isoformat(),
                "description": "Ad agency retainer (manual, not an expense)",
                "category": "Marketing",
                "amount_minor": 10_000_000,
                "currency": "NGN",
                "direction": "out",
            },
        )
        assert manual.status_code == 200, manual.text

        # 4. GET this month's budgets: spent / variance / over_budget / percent_used are derived
        # from THIS month's expenses only.
        listed = c.get("/api/v1/finance/budgets", headers=wh, params={"month": this_month})
        assert listed.status_code == 200, listed.text
        rows = listed.json()["data"]["budgets"]
        assert listed.json()["data"]["month"] == this_month
        by_cat = {r["category"]: r for r in rows}
        assert list(by_cat) == ["Infrastructure", "Marketing", "Software"]  # sorted, Payroll absent

        infra = by_cat["Infrastructure"]
        assert infra["id"] == infra_id
        assert infra["spent_minor"] == 9_000_000  # the lower-case 1M is NOT included
        assert infra["variance_minor"] == 1_000_000
        assert infra["over_budget"] is True
        assert infra["percent_used"] == 112.5

        mkt = by_cat["Marketing"]
        assert mkt["id"] == marketing_id
        assert mkt["spent_minor"] == 2_000_000  # the 10M manual outflow is NOT included
        assert mkt["variance_minor"] == -3_000_000
        assert mkt["over_budget"] is False
        assert mkt["percent_used"] == 40.0

        soft = by_cat["Software"]
        assert soft["id"] == software_id
        assert soft["spent_minor"] == 0
        assert soft["variance_minor"] == -1_500_000
        assert soft["over_budget"] is False
        assert soft["percent_used"] == 0.0
        capture("budgets", "budgets_list_this_month", listed)

        # Derived on every read: the same budget, read by id, agrees.
        one = c.get(f"/api/v1/finance/budgets/{infra_id}", headers=wh)
        assert one.status_code == 200, one.text
        assert one.json()["data"] == infra
        capture("budgets", "budget_get_one", one)

        # A month with no budgets is an empty list, not an error.
        empty = c.get("/api/v1/finance/budgets", headers=wh, params={"month": "2020-01"})
        assert empty.status_code == 200, empty.text
        assert empty.json()["data"] == {"month": "2020-01", "budgets": []}
        capture("budgets", "budgets_list_empty_month", empty)

        # 5. Create a budget for Payroll (3M already spent this month) -> under its 5M limit.
        payroll = c.post(
            "/api/v1/finance/budgets",
            headers=wh,
            json={
                "category": "Payroll",
                "period_month": this_month,
                "limit_minor": 5_000_000,
                "notes": "Q4 headcount plan",
            },
        )
        assert payroll.status_code == 200, payroll.text
        p = payroll.json()["data"]
        assert p["currency"] == "NGN"  # defaults to NGN when omitted
        assert p["notes"] == "Q4 headcount plan"
        assert p["spent_minor"] == 3_000_000  # existing expenses count immediately
        assert p["variance_minor"] == -2_000_000
        assert p["over_budget"] is False
        assert p["percent_used"] == 60.0
        capture("budgets", "budget_created", payroll)
        payroll_id = p["id"]

        # PATCH the limit down to 2M -> over_budget flips true. Only limit_minor + notes are
        # writable; spent / variance / over_budget / percent_used are recomputed.
        patched = c.patch(
            f"/api/v1/finance/budgets/{payroll_id}",
            headers=wh,
            json={"limit_minor": 2_000_000, "notes": "Hiring freeze"},
        )
        assert patched.status_code == 200, patched.text
        pp = patched.json()["data"]
        assert pp["id"] == payroll_id
        assert pp["limit_minor"] == 2_000_000
        assert pp["notes"] == "Hiring freeze"
        assert pp["spent_minor"] == 3_000_000
        assert pp["variance_minor"] == 1_000_000
        assert pp["over_budget"] is True
        assert pp["percent_used"] == 150.0
        capture("budgets", "budget_patched_over_budget", patched)

        confirm = c.get(f"/api/v1/finance/budgets/{payroll_id}", headers=wh)
        assert confirm.status_code == 200, confirm.text
        assert confirm.json()["data"]["over_budget"] is True
        assert confirm.json()["data"]["limit_minor"] == 2_000_000
        capture("budgets", "budget_get_after_patch", confirm)

        # PATCH with notes only leaves the limit alone; notes: null clears the note.
        cleared = c.patch(f"/api/v1/finance/budgets/{payroll_id}", headers=wh, json={"notes": None})
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["data"]["notes"] is None
        assert cleared.json()["data"]["limit_minor"] == 2_000_000
        capture("budgets", "budget_patched_notes_cleared", cleared)

        # A zero limit: allowed; percent_used is null (no divide-by-zero); spent 0 is not over.
        zero = c.post(
            "/api/v1/finance/budgets",
            headers=wh,
            json={"category": "Legal", "period_month": this_month, "limit_minor": 0},
        )
        assert zero.status_code == 200, zero.text
        z = zero.json()["data"]
        assert z["limit_minor"] == 0
        assert z["spent_minor"] == 0
        assert z["over_budget"] is False
        assert z["percent_used"] is None
        capture("budgets", "budget_created_zero_limit", zero)
        legal_id = z["id"]

        # A limit above the int32 range (2,147,483,647): budgets are aggregates, so 64-bit.
        big = c.post(
            "/api/v1/finance/budgets",
            headers=wh,
            json={
                "category": "Equipment",
                "period_month": this_month,
                "limit_minor": 5_000_000_000,
            },
        )
        assert big.status_code == 200, big.text
        assert big.json()["data"]["limit_minor"] == 5_000_000_000
        capture("budgets", "budget_created_limit_above_int32", big)

        # The month now has six budgets (the derived values follow live expenses).
        listed2 = c.get("/api/v1/finance/budgets", headers=wh, params={"month": this_month})
        assert listed2.status_code == 200, listed2.text
        assert [r["category"] for r in listed2.json()["data"]["budgets"]] == [
            "Equipment",
            "Infrastructure",
            "Legal",
            "Marketing",
            "Payroll",
            "Software",
        ]
        capture("budgets", "budgets_list_after_edits", listed2)

        # Spent follows a new expense immediately (no stored actuals): +1M Marketing -> 3M.
        _expense("Billboard", "Marketing", today, 1_000_000)
        after_expense = c.get(f"/api/v1/finance/budgets/{marketing_id}", headers=wh)
        assert after_expense.status_code == 200, after_expense.text
        ae = after_expense.json()["data"]
        assert ae["spent_minor"] == 3_000_000
        assert ae["variance_minor"] == -2_000_000
        assert ae["percent_used"] == 60.0
        capture("budgets", "budget_get_after_new_expense", after_expense)

        # 6. Copy this month's budgets into NEXT month. Pre-create Marketing there with a
        # different limit: the copy must skip it (never overwrite) and return only the rest.
        existing_next = c.post(
            "/api/v1/finance/budgets",
            headers=wh,
            json={"category": "Marketing", "period_month": next_month, "limit_minor": 999_000},
        )
        assert existing_next.status_code == 200, existing_next.text
        capture("budgets", "budget_created_next_month_existing", existing_next)

        copied = c.post(
            "/api/v1/finance/budgets/copy-last-month",
            headers=wh,
            json={"period_month": next_month},
        )
        assert copied.status_code == 200, copied.text
        cp = copied.json()["data"]
        assert cp["month"] == next_month
        assert [(b["category"], b["limit_minor"]) for b in cp["budgets"]] == [
            ("Equipment", 5_000_000_000),
            ("Infrastructure", 8_000_000),
            ("Legal", 0),
            ("Payroll", 2_000_000),
            ("Software", 1_500_000),
        ]  # Marketing skipped (already budgeted); limits copied as they stand after the edits
        for b in cp["budgets"]:
            assert b["period_month"] == next_month
            assert b["currency"] == "NGN"
            assert b["spent_minor"] == 0  # no expenses next month
        capture("budgets", "copy_last_month", copied)

        # The pre-existing Marketing budget kept its own limit.
        next_list = c.get("/api/v1/finance/budgets", headers=wh, params={"month": next_month})
        assert next_list.status_code == 200, next_list.text
        next_by_cat = {r["category"]: r for r in next_list.json()["data"]["budgets"]}
        assert len(next_by_cat) == 6
        assert next_by_cat["Marketing"]["limit_minor"] == 999_000
        capture("budgets", "budgets_list_next_month", next_list)

        # Copying again is a no-op.
        copy_again = c.post(
            "/api/v1/finance/budgets/copy-last-month",
            headers=wh,
            json={"period_month": next_month},
        )
        assert copy_again.status_code == 200, copy_again.text
        assert copy_again.json()["data"] == {"month": next_month, "budgets": []}
        capture("budgets", "copy_last_month_again_skips_existing", copy_again)

        # 7. DELETE a budget; it is gone and the list follows.
        deleted = c.delete(f"/api/v1/finance/budgets/{legal_id}", headers=wh)
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["data"] == {"deleted": True}
        capture("budgets", "budget_deleted", deleted)

        gone = c.get(f"/api/v1/finance/budgets/{legal_id}", headers=wh)
        assert gone.status_code == 404, gone.text
        capture("budgets", "error_get_deleted_budget", gone)
        after_delete = c.get("/api/v1/finance/budgets", headers=wh, params={"month": this_month})
        assert "Legal" not in [r["category"] for r in after_delete.json()["data"]["budgets"]]
        assert len(after_delete.json()["data"]["budgets"]) == 5

        # 8. Error shapes the FE must handle.
        dup = c.post(
            "/api/v1/finance/budgets",
            headers=wh,
            json={"category": "Marketing", "period_month": this_month, "limit_minor": 1},
        )
        assert dup.status_code == 422, dup.text
        assert dup.json()["error"]["code"] == "VALIDATION_ERROR"
        capture("budgets", "error_duplicate_category_month", dup)
        # ... and the session survived the duplicate: a normal read still works.
        assert c.get(f"/api/v1/finance/budgets/{marketing_id}", headers=wh).status_code == 200

        # Case-exact: "marketing" is a DIFFERENT category, so it is a separate, allowed budget.
        lower = c.post(
            "/api/v1/finance/budgets",
            headers=wh,
            json={"category": "marketing", "period_month": this_month, "limit_minor": 1_000},
        )
        assert lower.status_code == 200, lower.text
        assert lower.json()["data"]["spent_minor"] == 0
        capture("budgets", "budget_created_lowercase_category_is_distinct", lower)

        bad_create = c.post(
            "/api/v1/finance/budgets",
            headers=wh,
            json={"category": "", "period_month": "2026-13", "limit_minor": -5},
        )
        assert bad_create.status_code == 422, bad_create.text
        capture("budgets", "error_create_validation", bad_create)

        null_patch = c.patch(
            f"/api/v1/finance/budgets/{payroll_id}", headers=wh, json={"limit_minor": None}
        )
        assert null_patch.status_code == 422, null_patch.text
        capture("budgets", "error_patch_null_limit", null_patch)

        bad_month = c.get("/api/v1/finance/budgets", headers=wh, params={"month": "2026-13"})
        assert bad_month.status_code == 422, bad_month.text
        capture("budgets", "error_list_bad_month", bad_month)

        missing_month = c.get("/api/v1/finance/budgets", headers=wh)
        assert missing_month.status_code == 422, missing_month.text
        capture("budgets", "error_list_missing_month", missing_month)

        bad_seed = c.post(
            "/api/v1/finance/budgets/draft-from-actuals",
            headers=wh,
            json={"period_month": "2026-9"},
        )
        assert bad_seed.status_code == 422, bad_seed.text
        capture("budgets", "error_seed_bad_month", bad_seed)

        unknown = c.get(f"/api/v1/finance/budgets/{uuid.uuid4()}", headers=wh)
        assert unknown.status_code == 404, unknown.text
        capture("budgets", "error_unknown_budget", unknown)

        unauth = c.get("/api/v1/finance/budgets", params={"month": this_month})
        assert unauth.status_code == 401, unauth.text
        capture("budgets", "error_unauthenticated", unauth)
