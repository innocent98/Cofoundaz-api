"""Digest email (PRD 20.3): one email per recipient and workspace, daily at their delivery time
or weekly on their chosen day, listing what arrived since the last one by module.

Subject: "{startup}: your {daily|weekly} Cofoundaz digest". Critical alerts are never in a digest
(they were emailed live), and a digest with nothing to say is not sent.
"""

import html
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.notification import Notification
from app.db.models.notification_preference import NotificationPreference
from app.db.models.startup import Startup
from app.db.models.user import User
from app.platform.email import EmailMessage, get_email_sender
from app.services.notifications.audience import audience_filter
from app.services.notifications.categories import CRITICAL_TYPES, category_for, module_for
from app.services.notifications.preferences import email_enabled

TOP_PER_MODULE = 3
MAX_ITEMS = 200  # newest first; a very long window still yields a small email
MAX_WINDOW = {"daily": timedelta(days=1), "weekly": timedelta(days=7)}


def due_rows(db: Session, now: datetime) -> list[tuple[NotificationPreference, str]]:
    """Preference rows whose digest is due this local day (once per day or week; the scheduler
    claims the (row, local date) so a re-scan never double-sends)."""
    rows = (
        db.query(NotificationPreference)
        .filter(NotificationPreference.digest_mode.in_(("daily", "weekly")))
        .filter(NotificationPreference.master_email.is_(True))
        .all()
    )
    out = []
    for r in rows:
        local = now.astimezone(ZoneInfo(r.timezone))
        if local.time() < r.digest_time:
            continue
        if r.digest_mode == "weekly" and local.weekday() != r.digest_day:
            continue
        out.append((r, local.date().isoformat()))
    return out


def _gather(db: Session, row: NotificationPreference, since: datetime) -> list[Notification]:
    from app.db.models.membership import Membership

    role = (
        db.query(Membership.role).filter_by(user_id=row.user_id, startup_id=row.startup_id).scalar()
    )
    if role is None:
        return []
    items = (
        db.query(Notification)
        .filter(
            Notification.user_id == row.user_id,
            Notification.startup_id == row.startup_id,
            Notification.created_at > since,
            Notification.archived_at.is_(None),
            Notification.type.notin_(sorted(CRITICAL_TYPES)),
            audience_filter(role),
        )
        .order_by(Notification.created_at.desc())
        .limit(MAX_ITEMS)
        .all()
    )
    enabled = {
        c: email_enabled(db, user_id=row.user_id, startup_id=row.startup_id, category=c)
        for c in {category_for(n.type) for n in items}
    }
    return [n for n in items if enabled[category_for(n.type)]]


def render(startup_name: str, mode: str, items: list[Notification]) -> tuple[str, str]:
    counts = Counter(module_for(n.type) for n in items)
    top: dict[str, list[str]] = defaultdict(list)
    for n in items:
        m = module_for(n.type)
        if len(top[m]) < TOP_PER_MODULE:
            top[m].append(n.title)
    base = (settings.APP_BASE_URL or settings.SERVER_HOST).rstrip("/")
    link = html.escape(
        f"{base}/notifications" if base.startswith(("http://", "https://")) else "/notifications",
        quote=True,
    )
    sections = "".join(
        f"<h3>{html.escape(m.title())} ({counts[m]})</h3><ul>"
        + "".join(f"<li>{html.escape(t)}</li>" for t in top[m])
        + "</ul>"
        for m in sorted(counts)
    )
    subject = f"{startup_name}: your {mode} Cofoundaz digest".replace("\r", " ").replace("\n", " ")
    body = (
        '<div style="font-family:system-ui,sans-serif;max-width:560px">'
        f"<h2>Your {html.escape(mode)} digest</h2>{sections}"
        f'<p><a href="{link}">Open your notifications</a></p>'
        '<hr><p style="font-size:12px;color:#666">Urgent alerts always arrive straight away, '
        "so they are not repeated here. Change this in your notification settings.</p></div>"
    )
    return subject, body


def send_digest(db: Session, *, user_id: uuid.UUID, startup_id: uuid.UUID, now: datetime) -> bool:
    """Build and send one digest; advances the cursor either way. True when an email went out."""
    row = (
        db.query(NotificationPreference)
        .filter_by(user_id=user_id, startup_id=startup_id)
        .with_for_update()
        .first()
    )
    if row is None or row.digest_mode not in MAX_WINDOW or not row.master_email:
        return False
    since = max(
        row.last_digest_at or (now - MAX_WINDOW[row.digest_mode]),
        now - MAX_WINDOW[row.digest_mode] * 2,
    )
    items = _gather(db, row, since)
    row.last_digest_at = now
    db.flush()
    user = db.get(User, user_id)
    startup = db.get(Startup, startup_id)
    if not items or user is None or not user.email or startup is None:
        return False
    subject, body = render(startup.name or "Your workspace", row.digest_mode, items)
    get_email_sender().send(EmailMessage(to=user.email, subject=subject, html=body))
    return True
