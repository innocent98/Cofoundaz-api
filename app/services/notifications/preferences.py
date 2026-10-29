import uuid
from datetime import UTC, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.db.models.notification_preference import NotificationPreference
from app.services.notifications.categories import (
    CATEGORIES,
    CATEGORY_DEFAULTS,
    CATEGORY_META,
    CRITICAL_TYPES,
    category_for,
    is_critical,
)

SCHEDULE_FIELDS = (
    "digest_mode",
    "digest_time",
    "digest_day",
    "quiet_enabled",
    "quiet_from",
    "quiet_until",
    "timezone",
)


def _row(db: Session, user_id: uuid.UUID, startup_id: uuid.UUID) -> NotificationPreference | None:
    return (
        db.query(NotificationPreference).filter_by(user_id=user_id, startup_id=startup_id).first()
    )


def effective_preferences(
    db: Session, *, user_id: uuid.UUID, startup_id: uuid.UUID
) -> dict[str, Any]:
    """Stored prefs merged over defaults; every category key present."""
    row = _row(db, user_id, startup_id)
    stored = row.categories if row is not None else {}
    stored_in_app = row.in_app if row is not None else {}
    return {
        "master_email": row.master_email if row is not None else True,
        "categories": {c: bool(stored.get(c, CATEGORY_DEFAULTS[c])) for c in CATEGORIES},
        "in_app": {c: bool(stored_in_app.get(c, True)) for c in CATEGORIES},
        "groups": groups_meta(),
        "digest_mode": row.digest_mode if row is not None else "realtime",
        "digest_time": _hhmm(row.digest_time if row is not None else time(8, 0)),
        "digest_day": row.digest_day if row is not None else 0,
        "quiet_enabled": row.quiet_enabled if row is not None else False,
        "quiet_from": _hhmm(row.quiet_from if row is not None else time(20, 0)),
        "quiet_until": _hhmm(row.quiet_until if row is not None else time(6, 0)),
        "timezone": row.timezone if row is not None else "Africa/Lagos",
    }


def groups_meta() -> list[dict[str, Any]]:
    """The Preferences grid rows: key, title, description and the locked-on (critical) types
    each row carries, so the UI can show the lock and its tooltip."""
    return [
        {
            "key": c,
            "title": CATEGORY_META[c][0],
            "description": CATEGORY_META[c][1],
            "critical_types": sorted(t for t in CRITICAL_TYPES if category_for(t) == c),
        }
        for c in CATEGORIES
    ]


def _hhmm(t: time) -> str:
    return t.strftime("%H:%M")


def set_preferences(
    db: Session,
    *,
    user_id: uuid.UUID,
    startup_id: uuid.UUID,
    master_email: bool | None,
    categories: dict[str, bool] | None,
    schedule: dict[str, Any] | None = None,
    in_app: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Upsert; master_email, a subset of category keys and/or the digest / quiet-hours fields in
    `schedule` (None values are ignored). Returns effective prefs."""
    row = _row(db, user_id, startup_id)
    if row is None:
        row = NotificationPreference(
            user_id=user_id,
            startup_id=startup_id,
            master_email=True,
            categories={},
            in_app={},
        )
        db.add(row)
    if master_email is not None:
        row.master_email = master_email
    if categories:
        merged = dict(row.categories)
        merged.update({k: bool(v) for k, v in categories.items() if k in CATEGORIES})
        row.categories = merged
    if in_app:
        merged_in_app = dict(row.in_app)
        merged_in_app.update({k: bool(v) for k, v in in_app.items() if k in CATEGORIES})
        row.in_app = merged_in_app
    for field, value in (schedule or {}).items():
        if value is not None and field in SCHEDULE_FIELDS:
            setattr(row, field, value)
    db.flush()
    return effective_preferences(db, user_id=user_id, startup_id=startup_id)


def email_enabled(
    db: Session, *, user_id: uuid.UUID, startup_id: uuid.UUID, category: str | None
) -> bool:
    """Whether this recipient wants email for this category (enqueue-time gate)."""
    if category is None:
        return False
    eff = effective_preferences(db, user_id=user_id, startup_id=startup_id)
    return bool(eff["master_email"] and eff["categories"].get(category, False))


def in_app_enabled(db: Session, *, user_id: uuid.UUID, startup_id: uuid.UUID, event: str) -> bool:
    """Critical types and types with no row are always delivered in-app; the rest follow the
    category's in-app switch."""
    category = category_for(event)
    if is_critical(event) or category is None:
        return True
    row = _row(db, user_id, startup_id)
    return bool(row.in_app.get(category, True)) if row is not None else True


def in_quiet_hours(row: NotificationPreference | None, now: datetime) -> bool:
    """True while the recipient's quiet window (their own timezone) is on and `now` falls in it."""
    if row is None or not row.quiet_enabled:
        return False
    local = now.astimezone(ZoneInfo(row.timezone)).time().replace(second=0, microsecond=0)
    start, end = row.quiet_from, row.quiet_until
    return start <= local < end if start < end else (local >= start or local < end)


def email_route(
    db: Session,
    *,
    user_id: uuid.UUID,
    startup_id: uuid.UUID,
    event: str,
    now: datetime | None = None,
) -> str:
    """How one notification reaches the recipient's inbox: "now", "digest" (batched) or "skip".

    Critical types always go "now" (PRD 20.2/20.3: they bypass the digest and quiet hours).
    Everything else needs the master switch and its category's email switch, is skipped during
    quiet hours (in-app is unaffected; the item stays in the inbox and a digest) and joins the digest when the user chose one.
    """
    if is_critical(event):
        return "now"
    category = category_for(event)
    if not email_enabled(db, user_id=user_id, startup_id=startup_id, category=category):
        return "skip"
    row = _row(db, user_id, startup_id)
    if in_quiet_hours(row, now or datetime.now(UTC)):
        return "skip"
    if row is not None and row.digest_mode != "realtime":
        return "digest"
    return "now"
