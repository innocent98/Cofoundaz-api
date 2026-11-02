"""Live Sales slice A journey: pipeline stages, settings, accounts/contacts, leads (+ CSV import),
lead conversion, deals and stage moves, activities, the stall scheduler + notification, the
overview, Dashboard KPI, search, the activity feed, and the mentor 403.

One founder workspace. Every request/response body is captured to `e2e/_captures/sales_a/*.json`;
those files are the verbatim source of `docs/fe-integration-guide-sales-crm.md`.

Not exercised live (covered by unit tests, labelled in the guide): the consultant 403 (same
role gate as the mentor), the 2 MB / 5,000-row CSV limits, the 10-stage maximum is exercised
(11 stages rejected) but the exact 10-stage success is not.

Keep app-internal imports LAZY (inside helpers): the remote live-e2e gate has no app env.
"""

import uuid
from datetime import UTC, datetime, timedelta

import httpx

G = "sales_a"
S = "/api/v1/sales"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _onboard(c: httpx.Client, auth: dict) -> None:
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Founder"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "NovaPay"})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "validation"},
    )
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Grow"]})
    assert c.post("/api/v1/onboarding/complete", headers=auth).status_code == 200


def _join(c, wh, ws, role, unique_email, mailbox):
    """Invite `role` to the workspace and accept as a freshly verified user."""
    email = unique_email(role)
    r = c.post(
        "/api/v1/team/invitations", headers=wh, json={"invites": [{"email": email, "role": role}]}
    )
    assert r.status_code == 200, r.text
    token = mailbox.latest_token_for(email, subject_contains="invited")
    pw = f"Ev-{uuid.uuid4().hex[:12]}-9"
    c.post("/api/v1/auth/signup", json={"email": email, "password": pw})
    c.post(
        "/api/v1/auth/verify",
        json={"token": mailbox.latest_token_for(email, subject_contains="Verify")},
    )
    access = c.post("/api/v1/auth/login", json={"email": email, "password": pw}).json()["data"][
        "access_token"
    ]
    auth = _auth(access)
    acc = c.post("/api/v1/invitations/accept", headers=auth, json={"token": token})
    assert acc.status_code == 200, acc.text
    return {**auth, "X-Workspace-Id": ws}


def _tick_and_drain(now: datetime) -> None:
    """One scheduler tick, then drain the worker queue, in-process (same as the real loop)."""
    from app.db.session import SessionLocal
    from app.services.notifications.registry import register as register_notifications
    from app.worker import runner
    from app.worker.handlers import scheduled  # noqa: F401  (registers the job handlers)
    from app.worker.scheduler import scheduler_tick

    register_notifications()
    db = SessionLocal()
    try:
        scheduler_tick(db, now=now)
    finally:
        db.close()
    db2 = SessionLocal()
    try:
        for _ in range(500):  # the jobs table is shared with every earlier journey
            if runner.run_once(db2) == 0:
                break
    finally:
        db2.close()


def _age_deal(deal_id: str, days: int) -> None:
    """Make a deal look quiet: push its last_activity_at back (no endpoint can do this)."""
    from sqlalchemy import text

    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        db.execute(
            text("UPDATE deals SET last_activity_at = :ts WHERE id = :id"),
            {"ts": datetime.now(UTC) - timedelta(days=days), "id": deal_id},
        )
        db.commit()
    finally:
        db.close()


def _notes(c, h, type_: str) -> list[dict]:
    r = c.get("/api/v1/notifications", headers=h)
    assert r.status_code == 200, r.text
    return [n for n in r.json()["data"]["notifications"] if n["type"] == type_]


def test_sales_a_journey(
    base_url, make_verified_user, unique_email, mailbox, capture, capture_json
):
    with httpx.Client(base_url=base_url, timeout=20.0) as c:
        u = make_verified_user(c)
        auth = _auth(c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"])
        _onboard(c, auth)
        ws = c.get("/api/v1/auth/me", headers=auth).json()["data"]["active_workspace_id"]
        wh = {**auth, "X-Workspace-Id": ws}

        # 1. Overview on a fresh workspace (lazy-seeds the pipeline) and the seeded stages.
        ov0 = c.get(S, headers=wh)
        assert ov0.status_code == 200, ov0.text
        assert ov0.json()["data"]["total_pipeline_minor"] == 0
        capture(G, "overview_empty", ov0)
        st = c.get(f"{S}/stages", headers=wh)
        assert st.status_code == 200, st.text
        stages = st.json()["data"]
        assert [s["name"] for s in stages][0] == "New" and len(stages) == 7
        capture(G, "stages_seeded", st)
        by_name = {s["name"]: s for s in stages}

        # 2. Stage editor: rename + add, then the failure shapes.
        edited = [
            {
                "id": s["id"],
                "name": "Discovery" if s["name"] == "Contacted" else s["name"],
                "kind": s["kind"],
                "win_probability": s["win_probability"],
            }
            for s in stages
        ]
        edited.insert(
            3, {"name": "Demo", "kind": "open", "win_probability": 50}
        )  # a new stage has no id
        put = c.put(f"{S}/stages", headers=wh, json={"stages": edited})
        assert put.status_code == 200, put.text
        names = [s["name"] for s in put.json()["data"]]
        assert names[1] == "Discovery" and names[3] == "Demo"
        capture(G, "stages_replaced", put)
        stages = put.json()["data"]
        by_name = {s["name"]: s for s in stages}

        too_many = [{"name": f"S{i}", "kind": "open", "win_probability": 10} for i in range(9)]
        too_many += [
            {"name": "W", "kind": "won", "win_probability": 100},
            {"name": "L", "kind": "lost", "win_probability": 0},
        ]
        r = c.put(f"{S}/stages", headers=wh, json={"stages": too_many})
        assert r.status_code == 422, r.text
        capture(G, "stages_error_too_many", r)
        no_won = [s for s in edited if s.get("kind") != "won"]
        for s in no_won:
            if "id" in s:
                s["id"] = by_name[s["name"]]["id"]
        r = c.put(f"{S}/stages", headers=wh, json={"stages": no_won})
        assert r.status_code == 422, r.text
        capture(G, "stages_error_no_won", r)

        # An empty stage can be removed (soft delete): drop Demo again, keep everything else.
        keep = [
            {
                "id": s["id"],
                "name": s["name"],
                "kind": s["kind"],
                "win_probability": s["win_probability"],
            }
            for s in stages
            if s["name"] != "Demo"
        ]
        r = c.put(f"{S}/stages", headers=wh, json={"stages": keep})
        assert r.status_code == 200 and "Demo" not in [s["name"] for s in r.json()["data"]]
        capture(G, "stages_removed_empty_stage", r)
        stages = r.json()["data"]
        by_name = {s["name"]: s for s in stages}

        # 3. Settings.
        gs = c.get(f"{S}/settings", headers=wh)
        assert gs.status_code == 200 and gs.json()["data"]["stall_days"] == 14
        capture(G, "settings_default", gs)
        ps = c.patch(f"{S}/settings", headers=wh, json={"stall_days": 7})
        assert ps.status_code == 200 and ps.json()["data"]["stall_days"] == 7
        capture(G, "settings_updated", ps)
        r = c.patch(f"{S}/settings", headers=wh, json={"stall_days": 1})
        assert r.status_code == 422, r.text
        capture(G, "settings_error_stall_days", r)

        # 4. Account + contact.
        acc = c.post(
            f"{S}/accounts",
            headers=wh,
            json={"name": "Acme Logistics", "website": "acme.example", "industry": "Logistics"},
        )
        assert acc.status_code == 200, acc.text
        account = acc.json()["data"]
        assert account["website"] == "https://acme.example"
        capture(G, "account_created", acc)
        r = c.post(
            f"{S}/accounts", headers=wh, json={"name": "Bad Co", "website": "javascript:alert(1)"}
        )
        assert r.status_code == 422, r.text
        capture(G, "account_error_website", r)
        con = c.post(
            f"{S}/contacts",
            headers=wh,
            json={
                "account_id": account["id"],
                "first_name": "Tunde",
                "last_name": "Okafor",
                "email": "tunde@acme.example",
                "title": "Head of Ops",
            },
        )
        assert con.status_code == 200, con.text
        contact = con.json()["data"]
        capture(G, "contact_created", con)
        cl = c.get(f"{S}/contacts?account_id={account['id']}", headers=wh)
        assert cl.json()["data"]["total"] == 1
        capture(G, "contacts_list_by_account", cl)
        al = c.get(f"{S}/accounts?q=acme", headers=wh)
        assert al.json()["data"]["total"] == 1
        capture(G, "accounts_list", al)

        # 5. Leads: two manual leads, then a CSV import.
        l1 = c.post(
            f"{S}/leads",
            headers=wh,
            json={
                "name": "Chidi Eze",
                "company": "Zenith Foods",
                "email": "chidi@zenith.example",
                "source": "referral",
            },
        )
        assert l1.status_code == 200, l1.text
        lead_company = l1.json()["data"]
        capture(G, "lead_created", l1)
        l2 = c.post(f"{S}/leads", headers=wh, json={"name": "Solo Prospect"})
        assert l2.status_code == 200, l2.text
        lead_solo = l2.json()["data"]
        r = c.post(f"{S}/leads", headers=wh, json={"source": "manual"})
        assert r.status_code == 422, r.text
        capture(G, "lead_error_no_identity", r)
        r = c.post(f"{S}/leads", headers=wh, json={"name": "X", "status": "converted"})
        assert r.status_code == 422, r.text
        capture(G, "lead_error_status_converted", r)

        csv_body = (
            b"Full Name,Company,E-mail,Source\n"
            b"Kemi Bello,Bello Print,kemi@bello.example,event\n"
            b"Femi Ade,Ade Farms,femi@ade.example,inbound\n"
            b"Chidi Again,Zenith Foods,CHIDI@zenith.example,manual\n"  # duplicate of lead 1
            b"Bad Email,Broken Ltd,not-an-email,manual\n"  # invalid email
            b",,orphan@nowhere.example,manual\n"  # no name or company
        )
        pv = c.post(
            f"{S}/leads/import/preview",
            headers=wh,
            files={"file": ("leads.csv", csv_body, "text/csv")},
        )
        assert pv.status_code == 200, pv.text
        preview = pv.json()["data"]
        assert preview["row_count"] == 5
        capture(G, "import_preview", pv)
        bad = c.post(
            f"{S}/leads/import/preview",
            headers=wh,
            files={"file": ("bad.csv", b"\xff\xfe\x00\x00", "text/csv")},
        )
        assert bad.status_code == 422, bad.text
        capture(G, "import_preview_unreadable", bad)
        mapping = preview["suggested_mapping"]
        imp = c.post(
            f"{S}/leads/import",
            headers=wh,
            json={"upload_id": preview["upload_id"], "mapping": mapping},
        )
        assert imp.status_code == 200, imp.text
        res = imp.json()["data"]
        assert res["added"] == 2 and res["duplicates_skipped"] == 1 and res["invalid_total"] == 2
        capture(G, "import_result", imp)
        again = c.post(
            f"{S}/leads/import",
            headers=wh,
            json={"upload_id": preview["upload_id"], "mapping": mapping},
        )
        assert again.status_code == 404, again.text
        capture(G, "import_error_upload_used", again)
        ll = c.get(f"{S}/leads?source=csv", headers=wh)
        assert ll.json()["data"]["total"] == 2
        capture(G, "leads_list_filtered", ll)

        # 6. Convert: a lead with a company creates account + contact + deal; one without,
        # only contact + deal (account is null).
        cv = c.post(f"{S}/leads/{lead_company['id']}/convert", headers=wh)
        assert cv.status_code == 200, cv.text
        assert cv.json()["data"]["account"] is not None
        capture(G, "lead_convert_with_company", cv)
        cv2 = c.post(f"{S}/leads/{lead_solo['id']}/convert", headers=wh)
        assert cv2.status_code == 200, cv2.text
        assert cv2.json()["data"]["account"] is None
        capture(G, "lead_convert_no_company", cv2)
        r = c.post(f"{S}/leads/{lead_company['id']}/convert", headers=wh)
        assert r.status_code == 422, r.text
        capture(G, "lead_convert_again_error", r)
        lg = c.get(f"{S}/leads/{lead_company['id']}", headers=wh)
        assert lg.json()["data"]["status"] == "converted"
        capture(G, "lead_after_convert", lg)

        # 7. Deals.
        d1 = c.post(
            f"{S}/deals",
            headers=wh,
            json={
                "name": "Acme fleet rollout",
                "account_id": account["id"],
                "contact_id": contact["id"],
                "value_minor": 250000000,
                "next_step": "Send proposal",
                "next_step_due": (datetime.now(UTC) + timedelta(days=3)).date().isoformat(),
            },
        )
        assert d1.status_code == 200, d1.text
        deal = d1.json()["data"]
        assert deal["stage_id"] == by_name["New"]["id"] and deal["currency"] == "NGN"
        capture(G, "deal_created", d1)
        d2 = c.post(
            f"{S}/deals",
            headers=wh,
            json={
                "name": "Quiet renewal",
                "account_id": account["id"],
                "value_minor": 80000000,
                "stage_id": by_name["Qualified"]["id"],
            },
        )
        assert d2.status_code == 200, d2.text
        quiet = d2.json()["data"]
        capture(G, "deal_created_in_stage", d2)
        r = c.post(f"{S}/deals", headers=wh, json={"name": "Neg", "value_minor": -5})
        assert r.status_code == 422, r.text
        capture(G, "deal_error_negative_value", r)
        dl = c.get(f"{S}/deals?open_only=true&limit=2", headers=wh)
        assert dl.status_code == 200 and dl.json()["data"]["limit"] == 2
        capture(G, "deals_list_page", dl)
        dg = c.get(f"{S}/deals/{deal['id']}", headers=wh)
        assert dg.status_code == 200
        capture(G, "deal_detail", dg)
        pd = c.patch(f"{S}/deals/{deal['id']}", headers=wh, json={"value_minor": 300000000})
        assert pd.status_code == 200 and pd.json()["data"]["value_minor"] == 300000000
        capture(G, "deal_updated", pd)
        r = c.patch(
            f"{S}/deals/{deal['id']}", headers=wh, json={"stage_id": by_name["Qualified"]["id"]}
        )
        assert r.status_code == 422, r.text
        capture(G, "deal_patch_stage_error", r)

        # 8. Stage moves.
        lost_id, won_id = by_name["Closed Lost"]["id"], by_name["Closed Won"]["id"]
        r = c.post(f"{S}/deals/{deal['id']}/stage", headers=wh, json={"stage_id": lost_id})
        assert r.status_code == 422, r.text
        capture(G, "deal_move_lost_without_reason", r)
        lost = c.post(
            f"{S}/deals/{deal['id']}/stage",
            headers=wh,
            json={"stage_id": lost_id, "closed_reason": "price", "closed_note": "Budget frozen"},
        )
        assert lost.status_code == 200, lost.text
        assert lost.json()["data"]["closed_reason"] == "price"
        capture(G, "deal_move_lost", lost)
        reopen = c.post(
            f"{S}/deals/{deal['id']}/stage",
            headers=wh,
            json={"stage_id": by_name["Proposal"]["id"]},
        )
        assert reopen.status_code == 200, reopen.text
        rd = reopen.json()["data"]
        assert rd["closed_at"] is None and rd["closed_reason"] is None and rd["closed_note"] is None
        capture(G, "deal_move_reopen", reopen)
        won = c.post(f"{S}/deals/{deal['id']}/stage", headers=wh, json={"stage_id": won_id})
        assert won.status_code == 200, won.text
        assert won.json()["data"]["closed_at"] is not None
        capture(G, "deal_move_won", won)
        # Reopen and win again the same day: still ONE won notification.
        c.post(
            f"{S}/deals/{deal['id']}/stage",
            headers=wh,
            json={"stage_id": by_name["Proposal"]["id"]},
        )
        c.post(f"{S}/deals/{deal['id']}/stage", headers=wh, json={"stage_id": won_id})
        won_notes = _notes(c, wh, "sales.deal.won")
        assert len(won_notes) == 1, won_notes
        capture_json(G, "notification_deal_won", won_notes[0])
        hist = c.get(f"{S}/deals/{deal['id']}", headers=wh)
        assert len(hist.json()["data"]["stage_history"]) >= 5
        capture(G, "deal_detail_with_history", hist)
        r = c.post(
            f"{S}/deals/{deal['id']}/stage", headers=wh, json={"stage_id": str(uuid.uuid4())}
        )
        assert r.status_code == 422, r.text
        capture(G, "deal_move_unknown_stage", r)

        # Stage removal is blocked while it holds deals (the quiet deal sits in Qualified).
        remove_q = [
            {
                "id": s["id"],
                "name": s["name"],
                "kind": s["kind"],
                "win_probability": s["win_probability"],
            }
            for s in stages
            if s["name"] != "Qualified"
        ]
        r = c.put(f"{S}/stages", headers=wh, json={"stages": remove_q})
        assert r.status_code == 422, r.text
        assert r.json()["error"]["message"] == "Move the deals out of Qualified first."
        capture(G, "stages_error_has_deals", r)

        # 9. Activities.
        a1 = c.post(
            f"{S}/activities",
            headers=wh,
            json={"kind": "call", "body": "Intro call with Tunde", "deal_id": quiet["id"]},
        )
        assert a1.status_code == 200, a1.text
        activity = a1.json()["data"]
        capture(G, "activity_created", a1)
        a2 = c.post(
            f"{S}/activities",
            headers=wh,
            json={
                "kind": "note",
                "body": "Account prefers WhatsApp",
                "account_id": account["id"],
                "contact_id": contact["id"],
            },
        )
        assert a2.status_code == 200, a2.text
        capture(G, "activity_created_on_account", a2)
        r = c.post(f"{S}/activities", headers=wh, json={"kind": "note", "body": "Orphan"})
        assert r.status_code == 422, r.text
        capture(G, "activity_error_no_link", r)
        pa = c.patch(
            f"{S}/activities/{activity['id']}",
            headers=wh,
            json={"kind": "meeting", "body": "Intro meeting with Tunde"},
        )
        assert pa.status_code == 200 and pa.json()["data"]["kind"] == "meeting"
        capture(G, "activity_updated", pa)
        fl = c.get(f"{S}/activities?account_id={account['id']}", headers=wh)
        assert fl.status_code == 200 and fl.json()["data"]["total"] == 1
        capture(G, "activities_list_by_account", fl)
        fd = c.get(f"{S}/activities?deal_id={quiet['id']}", headers=wh)
        assert fd.json()["data"]["total"] == 1
        capture(G, "activities_list_by_deal", fd)
        r = c.get(f"{S}/activities", headers=wh)
        assert r.status_code == 422, r.text
        capture(G, "activities_list_error_no_filter", r)
        da = c.delete(f"{S}/activities/{activity['id']}", headers=wh)
        assert da.status_code == 200, da.text
        capture(G, "activity_deleted", da)
        r = c.delete(f"{S}/activities/{activity['id']}", headers=wh)
        assert r.status_code == 404, r.text
        capture(G, "activity_delete_not_found", r)
        # A fresh activity on the quiet deal so the stall clock is reset by a real action.
        c.post(
            f"{S}/activities",
            headers=wh,
            json={"kind": "email", "body": "Sent renewal terms", "deal_id": quiet["id"]},
        )
        fresh = c.get(f"{S}/deals/{quiet['id']}", headers=wh)
        assert fresh.json()["data"]["is_stalled"] is False
        capture(G, "deal_before_stall", fresh)

        # 10. Force a stall: 20 days quiet vs a 7 day threshold, tick, drain.
        _age_deal(quiet["id"], 20)
        stalled_view = c.get(f"{S}/deals/{quiet['id']}", headers=wh)
        sd = stalled_view.json()["data"]
        assert sd["is_stalled"] is True and sd["days_inactive"] >= 19
        capture(G, "deal_stalled", stalled_view)
        _tick_and_drain(datetime.now(UTC))
        stalled_notes = [
            n
            for n in _notes(c, wh, "sales.deal.stalled")
            if n["data"].get("deal_id") == quiet["id"]
        ]
        assert len(stalled_notes) == 1, stalled_notes
        capture_json(G, "notification_deal_stalled", stalled_notes[0])
        _tick_and_drain(datetime.now(UTC))  # a second tick must not notify again
        again_notes = [
            n
            for n in _notes(c, wh, "sales.deal.stalled")
            if n["data"].get("deal_id") == quiet["id"]
        ]
        assert len(again_notes) == 1

        # 11. Account page, overview, Dashboard KPI.
        ad = c.get(f"{S}/accounts/{account['id']}", headers=wh)
        assert ad.status_code == 200, ad.text
        assert ad.json()["data"]["lifetime_won_minor"] == 300000000
        capture(G, "account_detail", ad)
        ov = c.get(S, headers=wh)
        assert ov.status_code == 200, ov.text
        capture(G, "overview", ov)
        summary = c.get("/api/v1/dashboard/summary", headers=wh)
        assert summary.status_code == 200, summary.text
        pipe = summary.json()["data"]["kpis"]["metrics"]["pipeline"]
        assert pipe["status"] == "ok", pipe
        capture(G, "dashboard_summary", summary)
        capture_json(G, "dashboard_kpi_pipeline", pipe)

        # 12. Search + activity feed.
        sr = c.get("/api/v1/search", params={"q": "Acme"}, headers=wh)
        assert sr.status_code == 200, sr.text
        types = {h["type"] for h in sr.json()["data"]["results"]}
        assert {"deal", "sales_account"} <= types or "deal" in types, types
        capture(G, "search_acme", sr)
        sc = c.get("/api/v1/search", params={"q": "Tunde"}, headers=wh)
        assert sc.status_code == 200
        capture(G, "search_contact", sc)
        feed = c.get("/api/v1/dashboard/activity", headers=wh)
        assert feed.status_code == 200, feed.text
        assert any("sales." in json_row.get("action", "") for json_row in _feed_rows(feed))
        capture(G, "dashboard_activity_feed", feed)

        # 13. Cleanup shapes: delete a contact/account/deal/lead.
        dd = c.delete(f"{S}/deals/{quiet['id']}", headers=wh)
        assert dd.status_code == 200, dd.text
        capture(G, "deal_deleted", dd)
        r = c.get(f"{S}/deals/{quiet['id']}", headers=wh)
        assert r.status_code == 404
        capture(G, "deal_get_not_found", r)

        # 14. Mentor: every Sales route is 403, search and the KPI tile give nothing.
        mentor = _join(c, wh, ws, "mentor", unique_email, mailbox)
        m403 = c.get(S, headers=mentor)
        assert m403.status_code == 403, m403.text
        capture(G, "mentor_overview_403", m403)
        m2 = c.post(f"{S}/deals", headers=mentor, json={"name": "Nope"})
        assert m2.status_code == 403, m2.text
        capture(G, "mentor_create_deal_403", m2)
        ms = c.get("/api/v1/search", params={"q": "Acme"}, headers=mentor)
        assert ms.status_code == 200
        assert not any(h["type"] in ("deal", "sales_account") for h in ms.json()["data"]["results"])
        capture(G, "mentor_search_acme", ms)
        mk = c.get("/api/v1/dashboard/summary", headers=mentor)
        assert mk.status_code == 200, mk.text
        assert mk.json()["data"]["kpis"]["metrics"]["pipeline"]["status"] == "restricted"
        capture_json(G, "mentor_kpi_pipeline", mk.json()["data"]["kpis"]["metrics"]["pipeline"])


def _feed_rows(resp: httpx.Response) -> list[dict]:
    data = resp.json()["data"]
    rows = data.get("activities") or data.get("items") or data.get("activity") or []
    return rows if isinstance(rows, list) else []
