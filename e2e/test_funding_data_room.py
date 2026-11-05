"""Live Funding Hub data room journey (slice B): a founder sets up the room, adds a PDF and a text
file from Documents, shares the Financials folder with an investor email, and the investor (no
account) proves the address with the emailed code, browses, opens a PDF (watermarked with their
email), sends heartbeats and reads. The founder sees the access log and a notification, narrows the
scope, then revokes and every investor route dies at once. Then the guards: wrong email and wrong
codes look identical, and a guessing loop is throttled.

Every JSON response goes to e2e/_captures/funding_data_room/ and is the verbatim source of
docs/fe-integration-guide-funding-data-room.md. The PDF itself is binary, so its response headers and
the text extracted from it are captured instead. Not live here: role 403s and the 402 plan gate
(unit-tested; the e2e server has PLAN_ENFORCEMENT off), the 15-minute per-share lock (unit-tested) and
the emailed-code throttle of 10 an hour (unit-tested; repeated journeys would trip it).
"""

import io
import re
import time

import httpx
from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas

F = "/api/v1/funding"
DR = f"{F}/data-room"
S = "X-Data-Room-Session"
G = "funding_data_room"


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


def _pdf(text: str) -> bytes:
    buf = io.BytesIO()
    cv = Canvas(buf)
    cv.drawString(72, 720, text)
    cv.showPage()
    cv.save()
    return buf.getvalue()


def _upload(c, wh, name, body, ct):
    r = c.post("/api/v1/documents/files", files={"file": (name, body, ct)}, headers=wh)
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _code(mailbox, email) -> str:
    mail = mailbox.latest_for(email)
    assert mail and "code" in mail["subject"].lower(), mail
    return re.search(r"<strong>(\d{6})</strong>", mail["html"]).group(1)


def test_funding_data_room_journey(
    base_url, make_verified_user, capture, capture_json, mailbox, unique_email
):
    with httpx.Client(base_url=base_url, timeout=20.0) as c:
        u = make_verified_user(c)
        auth = _login(c, u)
        _onboard(c, auth)
        wh = _wh(c, auth)
        investor = unique_email("investor")

        # 1. Nothing set up yet: the empty state, not an error.
        r = c.get(DR, headers=wh)
        assert r.json()["data"]["set_up"] is False
        capture(G, "room_empty", r)

        # 2. Set up the six standard folders, add two files from Documents.
        r = c.post(f"{DR}/setup", headers=wh)
        assert [f["name"] for f in r.json()["data"]["folders"]][:2] == ["Corporate", "Financials"]
        capture(G, "setup", r)
        folders = {f["name"]: f["id"] for f in r.json()["data"]["folders"]}
        fin = folders["Financials"]
        pdf = _upload(c, wh, "pitch-deck.pdf", _pdf("Pitch deck (placeholder)"), "application/pdf")
        txt = _upload(c, wh, "model-notes.txt", b"Placeholder model notes", "text/plain")
        r = c.post(f"{DR}/folders/{fin}/items", headers=wh, json={"file_id": pdf["id"]})
        assert r.status_code == 201
        capture(G, "item_added", r)
        pdf_item = r.json()["data"]
        r = c.post(f"{DR}/folders/{fin}/items", headers=wh, json={"file_id": txt["id"]})
        txt_item = r.json()["data"]
        r = c.post(f"{DR}/folders/{fin}/items", headers=wh, json={"file_id": pdf["id"]})
        assert r.status_code == 409
        capture(G, "item_duplicate_409", r)
        r = c.post(f"{DR}/folders", headers=wh, json={"name": "Board"})
        assert r.status_code == 201
        capture(G, "folder_created", r)
        r = c.post(f"{DR}/folders", headers=wh, json={"name": "board"})
        assert r.status_code == 409
        capture(G, "folder_duplicate_409", r)
        r = c.get(DR, headers=wh)
        capture(G, "room_with_items", r)

        # 3. A pipeline investor, then a share tied to them.
        inv = c.post(f"{F}/investors", headers=wh, json={"name": "Tolu Bello", "type": "angel"})
        inv_id = inv.json()["data"]["id"]
        r = c.post(
            f"{DR}/shares",
            headers=wh,
            json={
                "email": investor,
                "folder_ids": [fin],
                "investor_id": inv_id,
                "expires_in_days": 30,
            },
        )
        assert r.status_code == 201, r.text
        capture(G, "share_created", r)
        share = r.json()["data"]
        token = share["link"].rsplit("/dr/", 1)[1]
        invite = mailbox.latest_for(investor)
        assert invite and token in invite["html"]
        capture_json(G, "email_invite", invite)
        r = c.post(f"{DR}/shares", headers=wh, json={"email": investor, "folder_ids": []})
        assert r.status_code == 422
        capture(G, "share_validation_422", r)

        # 4. The investor's door. Unknown links are one 404.
        r = c.get(f"{F}/dr/{token}")
        assert r.json()["data"]["requires_code"] is True
        capture(G, "public_meta", r)
        r = c.get(f"{F}/dr/{'A' * 43}")
        assert r.status_code == 404
        capture(G, "public_unknown_link_404", r)
        r = c.get(f"{F}/dr/{token}/tree")
        assert r.status_code == 401
        capture(G, "tree_without_session_401", r)

        # Asking for a code looks the same for a stranger and for the invited address.
        before = mailbox.count_for("stranger@example.com")
        r_stranger = c.post(f"{F}/dr/{token}/otp", json={"email": "stranger@example.com"})
        capture(G, "otp_unknown_email_202", r_stranger)
        r_right = c.post(f"{F}/dr/{token}/otp", json={"email": investor})
        assert r_stranger.status_code == r_right.status_code == 202
        assert r_stranger.json() == r_right.json()
        assert mailbox.count_for("stranger@example.com") == before
        capture(G, "otp_invited_email_202", r_right)
        time.sleep(0.3)
        code = _code(mailbox, investor)
        capture_json(G, "email_code", mailbox.latest_for(investor))

        wrong = "000000" if code != "000000" else "111111"
        r = c.post(f"{F}/dr/{token}/verify", json={"email": investor, "code": wrong})
        assert r.status_code == 401
        capture(G, "verify_wrong_code_401", r)
        r = c.post(f"{F}/dr/{token}/verify", json={"email": investor, "code": code})
        assert r.status_code == 200, r.text
        capture(G, "verify_ok", r)
        sess = {S: r.json()["data"]["session"]}
        r = c.post(f"{F}/dr/{token}/verify", json={"email": investor, "code": code})
        assert r.status_code == 401  # a code works once
        capture(G, "verify_code_reused_401", r)

        # 5. Browse and read.
        r = c.get(f"{F}/dr/{token}/tree", headers=sess)
        assert [f["name"] for f in r.json()["data"]["folders"]] == ["Financials"]
        capture(G, "tree", r)
        r = c.post(f"{F}/dr/{token}/items/{pdf_item['id']}/open", headers=sess)
        assert r.status_code == 200
        capture(G, "open_pdf", r)
        opened = r.json()["data"]
        r = c.get(opened["content_url"].replace("/api/v1/funding", F))
        assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
        text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages)
        assert investor.lower() in text and "UTC" in text
        capture_json(
            G,
            "content_pdf",
            {
                "status": r.status_code,
                "headers": {
                    k: r.headers[k]
                    for k in (
                        "content-type",
                        "content-disposition",
                        "cache-control",
                        "x-data-room-view",
                    )
                },
                "extracted_text": text,
            },
        )
        time.sleep(2)
        r = c.post(f"{F}/dr/{token}/heartbeat", headers=sess, json={"view_id": opened["view_id"]})
        assert r.status_code == 200 and r.json()["data"]["duration_s"] >= 1
        capture(G, "heartbeat", r)
        # A text file cannot be watermarked, so with downloads off it is refused, and a refusal is
        # not logged as a view.
        r = c.post(f"{F}/dr/{token}/items/{txt_item['id']}/open", headers=sess)
        assert r.status_code == 422 and r.json()["error"]["code"] == "VIEW_ONLY_UNSUPPORTED"
        capture(G, "open_text_view_only_422", r)
        r = c.get(f"{F}/dr/{token}/items/{txt_item['id']}/content", headers=sess)
        assert r.status_code == 422
        capture(G, "content_text_view_only_422", r)
        assert c.get(f"{DR}/access-log", headers=wh).json()["data"]["total"] == 1
        # The founder allows downloads; the viewer already inside gets the raw file straight away.
        r = c.patch(f"{DR}/shares/{share['id']}", headers=wh, json={"allow_download": True})
        assert r.json()["data"]["allow_download"] is True
        capture(G, "share_updated", r)
        r = c.get(f"{F}/dr/{token}/tree", headers=sess)
        assert all(i["viewable"] for i in r.json()["data"]["folders"][0]["items"])
        capture(G, "tree_downloads_on", r)
        r = c.get(f"{F}/dr/{token}/items/{txt_item['id']}/content", headers=sess)
        assert r.status_code == 200 and r.content == b"Placeholder model notes"
        capture_json(
            G,
            "content_text",
            {
                "status": r.status_code,
                "headers": {
                    k: r.headers[k]
                    for k in ("content-type", "content-disposition", "cache-control")
                },
                "body": r.text,
            },
        )
        r = c.get(f"{F}/dr/{token}/items/{pdf_item['id']}/content?ticket=not-a-ticket")
        assert r.status_code == 401
        capture(G, "content_bad_ticket_401", r)

        # 6. The founder sees who looked.
        r = c.get(f"{DR}/access-log", headers=wh)
        assert r.json()["data"]["total"] == 2
        capture(G, "access_log", r)
        r = c.get(f"{DR}/shares", headers=wh)
        assert r.json()["data"]["items"][0]["view_count"] == 2
        capture(G, "shares_list", r)
        r = c.get("/api/v1/notifications", headers=wh)
        capture(G, "notifications", r)
        assert "funding.dataroom.viewed" in str(r.json())

        # 7. Change the share; revoke; everything dies at once.
        r = c.patch(f"{DR}/shares/{share['id']}", headers=wh, json={"expires_at": None})
        capture(G, "share_expiry_cleared", r)
        r = c.delete(f"{DR}/shares/{share['id']}", headers=wh)
        assert r.json()["data"]["status"] == "revoked"
        capture(G, "share_revoked", r)
        dead = [
            c.get(f"{F}/dr/{token}"),
            c.get(f"{F}/dr/{token}/tree", headers=sess),
            c.get(f"{F}/dr/{token}/items/{pdf_item['id']}/content", headers=sess),
            c.post(f"{F}/dr/{token}/heartbeat", headers=sess, json={"view_id": opened["view_id"]}),
        ]
        assert [d.status_code for d in dead] == [404] * 4
        capture(G, "after_revoke_meta_404", dead[0])
        capture(G, "after_revoke_content_404", dead[2])
        r = c.patch(f"{DR}/shares/{share['id']}", headers=wh, json={"allow_download": False})
        assert r.status_code == 404
        capture(G, "revoked_share_edit_404", r)

        # 8. A second share: wrong codes are throttled per client address (8 a minute), which is
        # what stops a guessing loop long before the per-share lock (10 wrong codes -> 15 minutes,
        # covered by the unit tests because this client address trips the throttle first).
        r = c.post(f"{DR}/shares", headers=wh, json={"email": investor, "folder_ids": [fin]})
        t2 = r.json()["data"]["link"].rsplit("/dr/", 1)[1]
        statuses = []
        for _ in range(10):
            bad = c.post(f"{F}/dr/{t2}/verify", json={"email": investor, "code": "123456"})
            statuses.append(bad.status_code)
            if bad.status_code == 429:
                capture(G, "verify_ip_throttled_429", bad)
                capture_json(G, "verify_ip_throttled_429_headers", dict(bad.headers))
                break
        assert 401 in statuses and statuses[-1] == 429, statuses

        # 9. Folder delete and item removal.
        r = c.delete(f"{DR}/items/{txt_item['id']}", headers=wh)
        capture(G, "item_removed", r)
        r = c.delete(f"{DR}/folders/{folders['Corporate']}", headers=wh)
        capture(G, "folder_deleted", r)
