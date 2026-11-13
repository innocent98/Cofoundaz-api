"""Shared pieces for the slice D tests: paying and accepting a booking in one line, uploads, a
shared document, and readers for the events and notifications a step raised."""

import io
import uuid
import zipfile
from datetime import UTC, datetime, timedelta

from app.db.models.document import Document
from app.db.models.marketplace import MarketplaceBooking, MarketplaceOrder
from app.db.models.notification import Notification
from app.platform.events import event_bus
from tests.api.test_staff_users import hdr
from tests.marketplace_booking_helpers import M, P, charge_success, post_webhook

PDF = b"%PDF-1.4\n%fake but magic-correct\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def docx_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<w:document/>")
    return buf.getvalue()


def paid(w, ps, **kw) -> str:
    """Book, pay (webhook) and return the booking id; the booking is `awaiting_provider`."""
    r = w.book(**kw)
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    ps.pay(d["order"]["reference"])
    hook = post_webhook(w.client, charge_success(d["order"]["reference"]))
    assert hook.status_code == 200, hook.text
    return str(d["id"])


def accepted(w, ps, **kw) -> str:
    bid = paid(w, ps, **kw)
    r = w.client.post(f"{P}/bookings/{bid}/accept", headers=hdr(w.expert))
    assert r.status_code == 200, r.text
    return bid


def booking(db, bid) -> MarketplaceBooking:
    return db.get(MarketplaceBooking, uuid.UUID(str(bid)))


def order_of(db, bid) -> MarketplaceOrder:
    return db.query(MarketplaceOrder).filter_by(booking_id=uuid.UUID(str(bid))).one()


def end_session(db, bid, *, ago: timedelta = timedelta(hours=1)) -> MarketplaceBooking:
    """Move a consultation's slot into the past so it has ended."""
    b = booking(db, bid)
    ends = datetime.now(UTC) - ago
    b.ends_at, b.starts_at = ends, ends - timedelta(hours=1)
    db.flush()
    return b


def upload(client, headers, base, purpose, *, content=PDF, name="a.pdf", ctype="application/pdf"):
    return client.post(
        f"{base}/files",
        headers=headers,
        data={"purpose": purpose},
        files={"file": (name, content, ctype)},
    )


def file_id(resp) -> str:
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


def founder_file(w, purpose="brief", **kw) -> str:
    return file_id(upload(w.client, w.h(), M, purpose, **kw))


def provider_file(w, purpose="delivery", **kw) -> str:
    return file_id(upload(w.client, hdr(w.expert), P, purpose, **kw))


def mk_doc(db, ws, owner, title="Cap table notes") -> Document:
    doc = Document(
        startup_id=ws.id,
        created_by_id=owner.id,
        title=title,
        sections=[{"heading": "Notes", "body": "Seed round: 20% for N50m."}],
    )
    db.add(doc)
    db.flush()
    db.refresh(doc)
    return doc


def events(name: str, booking_id: str | None = None) -> list[dict]:
    return [
        p
        for e, p in event_bus.published  # type: ignore[attr-defined]
        if e == name and (booking_id is None or p.get("booking_id") == booking_id)
    ]


def notes(db, user, kind: str) -> list[Notification]:
    return db.query(Notification).filter_by(user_id=user.id, type=kind).all()


def sent_to(mail, address: str):
    return [m for m in mail.sent if m.to == address]
