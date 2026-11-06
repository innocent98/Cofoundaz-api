"""Live Funding Hub cap table journey (slice C): a founder starts from the empty state, adds
themselves, an option pool, an employee grant with vesting, a post-money SAFE with a cap, a
discounted SAFE and an interest-bearing note, reads the ledger fully diluted and basic, models a
Seed round (pool top-up, SAFE conversion) then Seed + Series A, saves a scenario, edits an entry,
and reads the audit trail. Then the guard rails.

Every JSON response goes to e2e/_captures/funding_cap_table/ and is the verbatim source of
docs/fe-integration-guide-funding-cap-table.md. Not live here: the role 403s (team members and every
other role, unit-tested; this journey has one user) and the 402 plan gate (the e2e server runs with
enforcement off).
"""

import httpx

F = "/api/v1/funding"
CT = f"{F}/cap-table"
G = "funding_cap_table"


def _onboard(c: httpx.Client, auth: dict) -> None:
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Obi"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "Placeholder Co"})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "launch"},
    )
    c.patch(
        "/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Raise a seed round"]}
    )
    c.post("/api/v1/onboarding/complete", headers=auth)


def _login(c: httpx.Client, user: dict) -> dict:
    tok = c.post("/api/v1/auth/login", json=user).json()["data"]["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def _wh(c: httpx.Client, auth: dict) -> dict:
    me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
    return {**auth, "X-Workspace-Id": me["active_workspace_id"]}


def test_funding_cap_table_journey(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=20.0) as c:
        u = make_verified_user(c)
        auth = _login(c, u)
        _onboard(c, auth)
        wh = _wh(c, auth)

        # 1. Empty state.
        r = c.get(CT, headers=wh)
        assert r.json()["data"]["empty"] is True
        capture(G, "ledger_empty", r)
        r = c.post(
            f"{CT}/model",
            headers=wh,
            json={"rounds": [{"new_money_minor": 10, "pre_money_minor": 40}]},
        )
        assert r.status_code == 422 and r.json()["error"]["code"] == "EMPTY_CAP_TABLE"
        capture(G, "model_empty_422", r)

        # 2. Holders and entries.
        def holder(name, kind, **kw):
            r = c.post(f"{CT}/holders", headers=wh, json={"name": name, "kind": kind, **kw})
            assert r.status_code == 201, r.text
            return r

        def entry(hid, **kw):
            r = c.post(f"{CT}/entries", headers=wh, json={"holder_id": hid, **kw})
            assert r.status_code == 201, r.text
            return r

        r = holder("Ada Obi", "founder")
        capture(G, "holder_created", r)
        ada = r.json()["data"]["id"]
        pool = holder("Option pool", "pool").json()["data"]["id"]
        eve = holder("Eve Placeholder", "employee", email="eve@example.com").json()["data"]["id"]
        angel = holder("Angel One", "investor").json()["data"]["id"]
        bank = holder("Lagos Bank", "investor").json()["data"]["id"]
        r = c.post(f"{CT}/holders", headers=wh, json={"name": "Second pool", "kind": "pool"})
        assert r.status_code == 409
        capture(G, "holder_second_pool_409", r)

        r = entry(ada, security="common", shares=8_000_000, issued_on="2025-01-15")
        capture(G, "entry_common", r)
        entry(pool, security="option", shares=1_000_000)
        r = entry(
            eve,
            security="option",
            shares=480_000,
            price_minor=10,
            issued_on="2025-03-01",
            vesting_start_on="2025-03-01",
            vesting_months=48,
            vesting_cliff_months=12,
        )
        capture(G, "entry_option_vesting", r)
        r = entry(
            angel,
            security="safe",
            amount_minor=1_000_000,
            valuation_cap_minor=10_000_000,
            issued_on="2025-09-01",
        )
        capture(G, "entry_safe_post_money", r)
        safe_id = r.json()["data"]["id"]
        entry(
            angel, security="safe", amount_minor=500_000, discount_bps=2000, issued_on="2025-10-01"
        )
        r = entry(
            bank,
            security="note",
            amount_minor=750_000,
            interest_bps=800,
            valuation_cap_minor=12_000_000,
            safe_kind="pre_money",
            issued_on="2025-06-01",
        )
        capture(G, "entry_note", r)

        # Shape rules.
        r = c.post(
            f"{CT}/entries",
            headers=wh,
            json={"holder_id": angel, "security": "safe", "amount_minor": 5, "shares": 100},
        )
        assert r.status_code == 422
        capture(G, "entry_safe_with_shares_422", r)
        r = c.post(
            f"{CT}/entries",
            headers=wh,
            json={"holder_id": pool, "security": "common", "shares": 5},
        )
        assert r.status_code == 422
        capture(G, "entry_on_pool_not_options_422", r)

        # 3. The ledger, fully diluted and basic.
        r = c.get(f"{CT}?fully_diluted=true&as_of=2026-06-01", headers=wh)
        assert r.json()["data"]["totals"]["fully_diluted_shares"] == 9_480_000
        capture(G, "ledger_fully_diluted", r)
        r = c.get(f"{CT}?fully_diluted=false&as_of=2026-06-01", headers=wh)
        capture(G, "ledger_basic", r)

        # 4. Model a round.
        seed = {"name": "Seed", "new_money_minor": 10_000_000, "pre_money_minor": 40_000_000}
        r = c.post(
            f"{CT}/model",
            headers=wh,
            json={"as_of": "2026-06-01", "rounds": [{**seed, "pool_target_bps": 1000}]},
        )
        assert r.status_code == 200, r.text
        capture(G, "model_seed", r)
        d = r.json()["data"]
        assert sum(x["after_bps"] for x in d["dilution"]) == 10_000
        r = c.post(
            f"{CT}/model",
            headers=wh,
            json={
                "as_of": "2026-06-01",
                "rounds": [
                    {**seed, "pool_target_bps": 1000},
                    {
                        "name": "Series A",
                        "new_money_minor": 50_000_000,
                        "pre_money_minor": 150_000_000,
                    },
                ],
            },
        )
        capture(G, "model_seed_and_series_a", r)
        r = c.post(
            f"{CT}/model",
            headers=wh,
            json={"rounds": [{**seed, "pool_target_bps": 9000}]},
        )
        assert r.status_code == 422 and r.json()["error"]["code"] == "POOL_TARGET_TOO_HIGH"
        capture(G, "model_pool_too_high_422", r)

        # 5. Scenarios.
        r = c.post(
            f"{CT}/scenarios",
            headers=wh,
            json={
                "name": "Seed at 40M",
                "inputs": {"as_of": "2026-06-01", "rounds": [{**seed, "pool_target_bps": 1000}]},
            },
        )
        assert r.status_code == 201
        sc = r.json()["data"]
        capture(G, "scenario_created", r)
        r = c.get(f"{CT}/scenarios", headers=wh)
        capture(G, "scenarios_list", r)
        r = c.patch(f"{CT}/scenarios/{sc['id']}", headers=wh, json={"name": "Seed at 40M (base)"})
        capture(G, "scenario_renamed", r)

        # 6. Edit, then the audit trail.
        r = c.patch(f"{CT}/entries/{safe_id}", headers=wh, json={"valuation_cap_minor": 9_000_000})
        assert r.json()["data"]["valuation_cap_minor"] == 9_000_000
        capture(G, "entry_updated", r)
        r = c.patch(f"{CT}/entries/{safe_id}", headers=wh, json={"shares": 5})
        assert r.status_code == 422
        capture(G, "entry_update_invalid_422", r)
        r = c.get(f"{CT}/scenarios/{sc['id']}", headers=wh)
        capture(G, "scenario_open", r)
        r = c.get(f"{CT}/audit?limit=3", headers=wh)
        assert r.json()["data"]["total"] >= 10
        capture(G, "audit", r)

        # 7. Currency is fixed once amounts exist; delete.
        c.put(f"{F}/round", headers=wh, json={"currency": "NGN"})
        r = c.put(f"{F}/round", headers=wh, json={"currency": "USD"})
        assert r.status_code == 409 and r.json()["error"]["code"] == "CAP_TABLE_HAS_AMOUNTS"
        capture(G, "round_currency_locked_409", r)
        r = c.delete(f"{CT}/entries/{safe_id}", headers=wh)
        capture(G, "entry_deleted", r)
        r = c.delete(f"{CT}/scenarios/{sc['id']}", headers=wh)
        capture(G, "scenario_deleted", r)
        r = c.delete(f"{CT}/holders/{bank}", headers=wh)
        capture(G, "holder_deleted", r)
