"""Bounce intake from the outreach mailbox's IMAP inbox (cPanel has no webhooks).

A scheduled poll reads new mail, recognises delivery-failure reports (RFC 3464 `multipart/report`
with a `delivery-status` part), finds which of OUR emails bounced, and applies it: the send is
marked `bounced`, the address is suppressed for the workspace, its enrollments stop, and a bounce
rate above 10% pauses the workspace. Idempotent by construction: each (UIDVALIDITY, UID) is
written to a ledger in the same transaction as its effect, so a re-poll, a crash, or a mailbox that
refused to move the mail cannot count a bounce twice.

Matching, most reliable first: our `X-Cofoundaz-Step` header or `Message-ID` inside the returned
original (full message or headers part), else the id quoted anywhere in the report body. A report
we cannot tie to one of our sends is ledgered as `unmatched` and ignored: nothing is suppressed
from an address we cannot attribute to a workspace.

Only permanent failures (Status 5.x.x) count. 4.x.x (mailbox full, delayed) is ignored.
"""

import re
import uuid
from dataclasses import dataclass
from email import message_from_bytes
from email.message import Message
from email.policy import default as default_policy

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.logger import log
from app.db.models.enums import SendStatus, SuppressionReason
from app.db.models.sales_sequences import ImapProcessed, SequenceSend
from app.platform.outreach import BounceMailbox, OutreachUnavailableError, imap_configured
from app.services.sales.suppression import maybe_auto_pause, suppress

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_STATUS_RE = re.compile(r"^Status:\s*(\d)\.(\d+)\.(\d+)", re.MULTILINE | re.IGNORECASE)


@dataclass
class ParsedBounce:
    send_id: uuid.UUID | None
    status: str  # "5.1.1"
    recipient: str | None


def _text_of(part: Message) -> str:
    try:
        payload = part.get_payload(decode=True)
    except Exception:  # noqa: BLE001 - a malformed part is just unreadable
        return ""
    if isinstance(payload, bytes):
        return payload.decode("utf-8", "replace")
    return str(part.get_payload()) if not part.is_multipart() else ""


def _walk_text(msg: Message) -> str:
    chunks: list[str] = []
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype in ("message/delivery-status", "message/rfc822"):
            payload = part.get_payload()
            if isinstance(payload, list):
                chunks.extend(str(inner) for inner in payload)
        elif ctype in ("text/plain", "text/rfc822-headers"):
            chunks.append(_text_of(part))
    return "\n".join(chunks)


def _send_id_from(blob: str) -> uuid.UUID | None:
    marker = re.search(r"X-Cofoundaz-Step:\s*(\S+)", blob, re.IGNORECASE)
    if marker:
        m = _UUID_RE.search(marker.group(1))
        if m:
            return uuid.UUID(m.group(0))
    mid = re.search(r"Message-ID:\s*<?([0-9a-fA-F-]{36})@", blob, re.IGNORECASE)
    if mid:
        return uuid.UUID(mid.group(1))
    return None


def parse_bounce(raw: bytes) -> ParsedBounce | None:
    """A permanent-failure report about one of our sends, or None for anything else."""
    msg = message_from_bytes(raw, policy=default_policy)
    is_report = msg.get_content_type() == "multipart/report"
    kind = str(msg.get_param("report-type") or "").lower()
    if not is_report or kind != "delivery-status":
        return None
    blob = _walk_text(msg)
    status = _STATUS_RE.search(blob)
    if status is None or status.group(1) != "5":
        return None
    recipient = None
    rcpt = re.search(
        r"(?:Final|Original)-Recipient:\s*(?:rfc822;)?\s*<?([^\s<>;]+@[^\s<>;]+)",
        blob,
        re.IGNORECASE,
    )
    if rcpt:
        recipient = rcpt.group(1).strip().lower()
    return ParsedBounce(
        send_id=_send_id_from(blob),
        status=f"{status.group(1)}.{status.group(2)}.{status.group(3)}",
        recipient=recipient,
    )


@dataclass
class PollResult:
    configured: bool = True
    seen: int = 0
    bounced: int = 0
    ignored: int = 0
    unmatched: int = 0


def _claim(db: Session, uidvalidity: int, uid: int) -> bool:
    """Write the ledger row first; False when it was already there (then nothing is applied)."""
    res = db.execute(
        pg_insert(ImapProcessed)
        .values(uidvalidity=uidvalidity, uid=uid, outcome="pending")
        .on_conflict_do_nothing(constraint="uq_imap_uidvalidity_uid")
        .returning(ImapProcessed.id)
    )
    return res.first() is not None


def _set_outcome(db: Session, uidvalidity: int, uid: int, outcome: str) -> None:
    db.execute(
        update(ImapProcessed)
        .where(ImapProcessed.uidvalidity == uidvalidity, ImapProcessed.uid == uid)
        .values(outcome=outcome)
    )


def apply_bounce(db: Session, parsed: ParsedBounce) -> str:
    """Apply one bounce. Returns 'bounced' or 'unmatched'."""
    if parsed.send_id is None:
        return "unmatched"
    send = db.scalar(
        select(SequenceSend).where(SequenceSend.id == parsed.send_id).with_for_update()
    )
    if send is None:
        return "unmatched"
    if parsed.recipient and parsed.recipient != send.recipient_email:
        # Mail that bounced for a different address than we sent to is not ours to act on.
        return "unmatched"
    if send.status in (SendStatus.sent, SendStatus.pending, SendStatus.unknown):
        send.status = SendStatus.bounced
        send.bounce_code = parsed.status
    suppress(db, send.startup_id, send.recipient_email, SuppressionReason.bounced)
    maybe_auto_pause(db, send.startup_id)
    return "bounced"


def poll_bounces(db: Session, mailbox: BounceMailbox | None = None) -> PollResult:
    """Read new mail from the outreach inbox and apply bounces. One transaction per message."""
    own = mailbox is None
    if mailbox is None:
        if not imap_configured():
            return PollResult(configured=False)
        from app.platform.outreach import get_bounce_mailbox

        mailbox = get_bounce_mailbox()
    out = PollResult()
    try:
        last_uid = (
            db.scalar(
                select(func.max(ImapProcessed.uid)).where(
                    ImapProcessed.uidvalidity == mailbox.uidvalidity
                )
            )
            or 0
        )
        db.rollback()
        for uid, raw in mailbox.fetch_after(last_uid):
            out.seen += 1
            try:
                parsed = parse_bounce(raw)
            except Exception as exc:  # noqa: BLE001 - one unreadable message must not stop the rest
                log.warning(f"[outreach] unparseable mail uid={uid}: {type(exc).__name__}")
                parsed = None
            if not _claim(db, mailbox.uidvalidity, uid):
                db.rollback()  # another poller already took this message
                continue
            outcome = "ignored" if parsed is None else apply_bounce(db, parsed)
            _set_outcome(db, mailbox.uidvalidity, uid, outcome)
            db.commit()
            if outcome == "bounced":
                out.bounced += 1
                mailbox.mark_processed(uid)  # after the commit; flags/copies only, never deletes
            elif outcome == "unmatched":
                out.unmatched += 1
            else:
                out.ignored += 1
    except OutreachUnavailableError as exc:
        log.warning(f"[outreach] bounce poll could not reach the mailbox: {exc}")
    finally:
        if own:
            mailbox.close()
    return out
