"""Live Legal & Compliance journey (Module 13, Slice 1).

`test_legal_journey`: a founder onboards, then walks all three Slice 1 surfaces --

  * **Formation (13.1)**: reads the supported jurisdictions as CODES (`NG`, not the prototype's
    "Nigeria"), reads the Nigerian checklist with its `estimates: true` flag and its disclaimer
    (said out loud so no client can present catalog figures as verified fact -- D6), ticks a
    step, unticks it (the PATCH carries the desired STATE, so a repeat is
    idempotent -- spec D7), and is refused an unsupported jurisdiction with a 404 rather than a
    422, which keeps the catalog non-enumerable (D3).
  * **IP tracker (13.4)**: creates a registered trademark with a future renewal, creates a second
    asset whose renewal has passed and reads back `derived_status: "expired"` -- derived, never
    stored, because the FE's add drawer cannot create it (D9) -- lists, edits, and deletes.
  * **Compliance (13.5)**: creates a MONTHLY filing and an already-overdue one, lists the month
    with the derived badge on every row (the FE counts its own overdue banner from these, so
    there is no summary endpoint -- D13), attaches a proof of filing, marks the recurring one
    done and reads back the NEXT occurrence that spawned from it, linked by `previous_item_id`.

Then the access rules, live: a **legal advisor** reads every surface and is refused every write
(D1), a **mentor** is refused even the reads, and a second workspace proves a cross-tenant id is
a **404, not a 403** -- the id must not be confirmed to exist.

Every response body is captured to `e2e/_captures/legal/*.json` -- those files are the verbatim
source for `docs/fe-integration-guide-legal.md`. They must be REAL bodies from this live run,
complete and untrimmed.

Dates are computed from the CURRENT UTC date (the same clock the derived statuses and the
reminders share -- D4), so the journey stays valid whenever it runs and the derived badges are
meaningful rather than accidental.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

BASE = "/api/v1/legal"

# Real PNG magic bytes. The service classifies the upload by these and ignores the declared
# Content-Type, so a body that merely starts with the letters "PNG" is refused.
PNG = b"\x89PNG\r\n\x1a\n"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _onboard_steps(c: httpx.Client, auth: dict, *, stage: str, name: str) -> None:
    """Walk steps 1-4 of the wizard (same shape as e2e/test_calendar.py)."""
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


def _workspace(c: httpx.Client, make_verified_user, *, name: str) -> dict:
    """Onboard a founder and return headers carrying their workspace."""
    u = make_verified_user(c)
    access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
    auth = _auth(access)
    _onboard_steps(c, auth, stage="validation", name=name)
    done = c.post("/api/v1/onboarding/complete", headers=auth)
    assert done.status_code == 200, done.text
    me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
    return {**auth, "X-Workspace-Id": me["active_workspace_id"]}


def _join(c, wh, role, unique_email, mailbox) -> dict:
    """Invite `role` into the founder's workspace and accept as a fresh verified user."""
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
    auth = _auth(access)
    acc = c.post("/api/v1/invitations/accept", headers=auth, json={"token": token})
    assert acc.status_code == 200, acc.text
    return {**auth, "X-Workspace-Id": wh["X-Workspace-Id"]}


@pytest.mark.critical
def test_legal_journey(base_url, make_verified_user, unique_email, mailbox, capture, capture_json):
    with httpx.Client(base_url=base_url, timeout=15.0) as c:
        wh = _workspace(c, make_verified_user, name="Cofoundaz Legal")

        today = datetime.now(UTC).date()

        # ---------------------------------------------------------------- 13.1 Entity formation

        # 1. Codes plus a display name. The FE sends `code`; it renders `name`.
        juris = c.get(f"{BASE}/formation/jurisdictions", headers=wh)
        assert juris.status_code == 200, juris.text
        codes = [j["code"] for j in juris.json()["data"]["jurisdictions"]]
        assert "NG" in codes
        capture("legal", "formation_jurisdictions", juris)

        # 2. The checklist: steps defined in CODE, only progress stored (D7). Estimates are
        #    integer minor units plus an ISO-4217 code, never floats (D5).
        checklist = c.get(f"{BASE}/formation/checklist?jurisdiction=NG", headers=wh)
        assert checklist.status_code == 200, checklist.text
        body = checklist.json()["data"]
        assert body["disclaimer"]
        first_step = body["steps"][0]
        step_key = first_step["key"]
        # The prototype squashes these into one `meta` string; the contract keeps them apart.
        assert "meta" not in first_step
        assert first_step["completed"] is False
        capture("legal", "formation_checklist", checklist)

        # 3. Tick one step. The PATCH returns the WHOLE checklist, so the FE needs no second call.
        ticked = c.patch(
            f"{BASE}/formation/steps/{step_key}",
            headers=wh,
            json={"jurisdiction": "NG", "completed": True},
        )
        assert ticked.status_code == 200, ticked.text
        assert ticked.json()["data"]["steps"][0]["completed"] is True
        capture("legal", "formation_step_ticked", ticked)

        # 4. The body is the desired STATE, not a delta, so repeating it changes nothing (D7).
        again = c.patch(
            f"{BASE}/formation/steps/{step_key}",
            headers=wh,
            json={"jurisdiction": "NG", "completed": True},
        )
        assert again.status_code == 200, again.text
        assert again.json()["data"]["steps"][0]["completed"] is True

        unticked = c.patch(
            f"{BASE}/formation/steps/{step_key}",
            headers=wh,
            json={"jurisdiction": "NG", "completed": False},
        )
        assert unticked.status_code == 200, unticked.text
        assert unticked.json()["data"]["steps"][0]["completed"] is False

        # 5. Unsupported jurisdiction: 404, not 422. It names a resource, and a 404 keeps the
        #    catalog non-enumerable (D3). The prototype's dropdown still offers Kenya and Ghana.
        missing = c.get(f"{BASE}/formation/checklist?jurisdiction=KE", headers=wh)
        assert missing.status_code == 404, missing.text
        capture("legal", "formation_unsupported_jurisdiction", missing)

        # -------------------------------------------------------------------- 13.4 IP tracker

        # 6. A registered trademark renewing inside the reminder window.
        tm = c.post(
            f"{BASE}/ip-assets",
            headers=wh,
            json={
                "type": "trademark",
                "name": "Kolo (wordmark)",
                "jurisdiction": "Nigeria",
                "status": "registered",
                "renewal_on": (today + timedelta(days=10)).isoformat(),
            },
        )
        assert tm.status_code == 200, tm.text
        tm_id = tm.json()["data"]["id"]
        assert tm.json()["data"]["derived_status"] == "registered"
        capture("legal", "ip_asset_created", tm)

        # 7. A passed renewal reads as `expired` -- DERIVED, never stored (D9). `status` is still
        #    the stored `registered`, which is what an edit form must send back.
        old = c.post(
            f"{BASE}/ip-assets",
            headers=wh,
            json={
                "type": "domain",
                "name": "kolo.africa",
                "jurisdiction": "Global",
                "status": "registered",
                "renewal_on": (today - timedelta(days=30)).isoformat(),
            },
        )
        assert old.status_code == 200, old.text
        old_id = old.json()["data"]["id"]
        assert old.json()["data"]["derived_status"] == "expired"
        assert old.json()["data"]["status"] == "registered"
        capture("legal", "ip_asset_expired", old)

        assets = c.get(f"{BASE}/ip-assets", headers=wh)
        assert assets.status_code == 200, assets.text
        assert len(assets.json()["data"]["ip_assets"]) == 2
        capture("legal", "ip_assets_list", assets)

        edited = c.patch(
            f"{BASE}/ip-assets/{tm_id}", headers=wh, json={"name": "Kolo (wordmark, class 36)"}
        )
        assert edited.status_code == 200, edited.text
        capture("legal", "ip_asset_updated", edited)

        removed = c.delete(f"{BASE}/ip-assets/{old_id}", headers=wh)
        assert removed.status_code == 200, removed.text
        assert removed.json()["data"] == {"deleted": True}
        capture("legal", "ip_asset_deleted", removed)

        gone = c.get(f"{BASE}/ip-assets/{old_id}", headers=wh)
        assert gone.status_code == 404, gone.text

        # ------------------------------------------------------------- 13.5 Compliance calendar

        # 8. A MONTHLY filing -- VAT and PAYE are monthly in Nigeria, so recurrence is the
        #    module's most-used field. `anchor_day` is derived from due_on, never sent (D24).
        vat_due = today + timedelta(days=3)
        vat = c.post(
            f"{BASE}/compliance-items",
            headers=wh,
            json={
                "title": "VAT return",
                "authority": "FIRS",
                "due_on": vat_due.isoformat(),
                "recurrence": "monthly",
            },
        )
        assert vat.status_code == 200, vat.text
        vat_id = vat.json()["data"]["id"]
        assert vat.json()["data"]["derived_status"] == "due_soon"
        assert vat.json()["data"]["proof_url"] is None
        capture("legal", "compliance_item_created", vat)

        # 9. An already-overdue filing. A done item is never overdue however old; an OPEN one
        #    past its date is (D12).
        late = c.post(
            f"{BASE}/compliance-items",
            headers=wh,
            json={
                "title": "Annual return",
                "authority": "CAC",
                "due_on": (today - timedelta(days=5)).isoformat(),
            },
        )
        assert late.status_code == 200, late.text
        assert late.json()["data"]["derived_status"] == "overdue"
        assert late.json()["data"]["recurrence"] == "none"
        capture("legal", "compliance_item_overdue", late)

        # 10. The calendar read. Every row carries its derived badge, so the FE's overdue banner
        #     counts these itself -- there is no summary endpoint (D13).
        listed = c.get(
            f"{BASE}/compliance-items"
            f"?date_from={(today - timedelta(days=30)).isoformat()}"
            f"&date_to={(today + timedelta(days=60)).isoformat()}",
            headers=wh,
        )
        assert listed.status_code == 200, listed.text
        rows = listed.json()["data"]["compliance_items"]
        assert {r["derived_status"] for r in rows} == {"due_soon", "overdue"}
        capture("legal", "compliance_items_list", listed)

        # 11. Proof of filing. The type comes from the file's own leading bytes, never the
        #     declared Content-Type, and the file is stored PRIVATELY -- so the row carries the
        #     authenticated ROUTE, not a storage address.
        no_proof = c.get(f"{BASE}/compliance-items/{vat_id}/proof", headers=wh)
        assert no_proof.status_code == 404, no_proof.text
        capture("legal", "compliance_proof_download_none", no_proof)

        proof = c.post(
            f"{BASE}/compliance-items/{vat_id}/proof",
            headers=wh,
            files={"file": ("vat-receipt.png", PNG + b"receipt-bytes", "image/png")},
        )
        assert proof.status_code == 200, proof.text
        assert proof.json()["data"]["proof_url"] == f"{BASE}/compliance-items/{vat_id}/proof"
        capture("legal", "compliance_proof_attached", proof)

        # 11b. D-LEG-5: ONE authenticated GET streams the private file. No signed link; the FE
        #      sends the bearer token and saves the blob. A binary body cannot be pasted into a
        #      JSON capture, so the status, the relevant headers and the byte count are recorded.
        got = c.get(f"{BASE}/compliance-items/{vat_id}/proof", headers=wh)
        assert got.status_code == 200, got.status_code
        assert got.content == PNG + b"receipt-bytes"
        assert got.headers["content-type"] == "image/png"
        assert got.headers["content-disposition"] == 'attachment; filename="proof-of-filing.png"'
        capture_json(
            "legal",
            "compliance_proof_download",
            {
                "status_code": got.status_code,
                "headers": {
                    k: got.headers[k]
                    for k in (
                        "content-type",
                        "content-disposition",
                        "x-content-type-options",
                        "cache-control",
                        "content-security-policy",
                    )
                },
                "body": f"<{len(got.content)} raw bytes: the uploaded file, byte for byte>",
            },
        )
        unauth = c.get(f"{BASE}/compliance-items/{vat_id}/proof")
        assert unauth.status_code in (401, 403), unauth.text

        # 12. A file whose declared type lies about its bytes is refused, and nothing is stored.
        liar = c.post(
            f"{BASE}/compliance-items/{vat_id}/proof",
            headers=wh,
            files={"file": ("not-really.png", b"<html>nope</html>", "image/png")},
        )
        assert liar.status_code == 422, liar.text
        capture("legal", "compliance_proof_rejected", liar)

        # 13. Marking a RECURRING filing done spawns the next occurrence, anchored so the series
        #     cannot drift (D16/D24). Proof stays optional: this one happens to carry one.
        done = c.patch(f"{BASE}/compliance-items/{vat_id}", headers=wh, json={"status": "done"})
        assert done.status_code == 200, done.text
        assert done.json()["data"]["status"] == "done"
        assert done.json()["data"]["derived_status"] == "done"
        capture("legal", "compliance_item_done", done)

        after = c.get(
            f"{BASE}/compliance-items"
            f"?date_from={today.isoformat()}&date_to={(today + timedelta(days=120)).isoformat()}",
            headers=wh,
        )
        assert after.status_code == 200, after.text
        spawned = [r for r in after.json()["data"]["compliance_items"] if r["previous_item_id"]]
        assert len(spawned) == 1, after.text
        assert spawned[0]["previous_item_id"] == vat_id
        assert spawned[0]["title"] == "VAT return"
        assert spawned[0]["recurrence"] == "monthly"
        # The successor is a fresh filing: open, undone, and carrying no proof of its own.
        assert spawned[0]["status"] == "open"
        assert spawned[0]["proof_url"] is None
        capture("legal", "compliance_recurrence_spawned", after)

        # ------------------------------------------------------------------------ access rules

        # 14. A legal advisor READS every surface and WRITES none of it (D1).
        la = _join(c, wh, "legal_advisor", unique_email, mailbox)

        la_read = c.get(f"{BASE}/compliance-items", headers=la)
        assert la_read.status_code == 200, la_read.text
        capture("legal", "legal_advisor_read", la_read)

        la_proof = c.get(f"{BASE}/compliance-items/{vat_id}/proof", headers=la)
        assert la_proof.status_code == 200, la_proof.status_code
        la_ip = c.get(f"{BASE}/ip-assets", headers=la)
        assert la_ip.status_code == 200
        la_checklist = c.get(f"{BASE}/formation/checklist?jurisdiction=NG", headers=la)
        assert la_checklist.status_code == 200

        la_write = c.post(
            f"{BASE}/compliance-items",
            headers=la,
            json={"title": "Nope", "due_on": today.isoformat()},
        )
        assert la_write.status_code == 403, la_write.text
        capture("legal", "legal_advisor_write_forbidden", la_write)

        la_tick = c.patch(
            f"{BASE}/formation/steps/{step_key}",
            headers=la,
            json={"jurisdiction": "NG", "completed": True},
        )
        assert la_tick.status_code == 403

        # 15. A mentor is outside the reader set, so even the GETs are refused. The titles name
        #     the filing, so delivering them would leak exactly what the routes withhold.
        mentor = _join(c, wh, "mentor", unique_email, mailbox)
        mentor_read = c.get(f"{BASE}/compliance-items", headers=mentor)
        assert mentor_read.status_code == 403, mentor_read.text
        capture("legal", "forbidden_role", mentor_read)
        mentor_proof = c.get(f"{BASE}/compliance-items/{vat_id}/proof", headers=mentor)
        assert mentor_proof.status_code == 403, mentor_proof.text

        mentor_ip = c.get(f"{BASE}/ip-assets", headers=mentor)
        assert mentor_ip.status_code == 403
        mentor_formation = c.get(f"{BASE}/formation/jurisdictions", headers=mentor)
        assert mentor_formation.status_code == 403

        # 16. Another workspace: a cross-tenant id is a 404, never a 403. A 403 would confirm the
        #     id exists; a 404 says nothing.
        other = _workspace(c, make_verified_user, name="Another Startup")
        cross = c.get(f"{BASE}/compliance-items/{vat_id}", headers=other)
        assert cross.status_code == 404, cross.text
        capture("legal", "cross_tenant_404", cross)

        cross_proof = c.get(f"{BASE}/compliance-items/{vat_id}/proof", headers=other)
        assert cross_proof.status_code == 404, cross_proof.text
        cross_ip = c.get(f"{BASE}/ip-assets/{tm_id}", headers=other)
        assert cross_ip.status_code == 404
        cross_delete = c.delete(f"{BASE}/compliance-items/{vat_id}", headers=other)
        assert cross_delete.status_code == 404
