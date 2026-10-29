from __future__ import annotations

import json
import re
import uuid
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import AppError, NotFound
from app.core.security import verify_password
from app.db.models.journal import JournalEntry
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_workspace
from app.platform.audit import write_audit
from app.schemas.journal import (
    JournalDeleteAll,
    JournalEntryCreate,
    JournalEntryListItem,
    JournalEntryListResponse,
    JournalEntryResponse,
    JournalEntryUpdate,
    JournalMonthCount,
    JournalMonthsResponse,
    JournalPromptResponse,
    JournalSettingsResponse,
    JournalSettingsUpdate,
    MoodAnnotation,
    MoodDataPoint,
    MoodTrendResponse,
    PasscodeRemove,
    PasscodeSet,
    PasscodeUnlock,
    RetroBody,
    RetroCreate,
    UnlockResponse,
)
from app.services.journal import account, lock, prompt_library, retros, stats
from app.services.journal.service import JournalService

_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

router = APIRouter()


def _startup(
    db: Session,
    membership: Membership,
) -> Startup:
    startup = db.query(Startup).filter(Startup.id == membership.startup_id).first()

    if startup is None:
        raise NotFound()

    return startup


def _token(x_journal_token: str | None = Header(default=None)) -> str | None:
    return x_journal_token


def _require_founder(
    db: Session,
    *,
    user: User,
    startup: Startup,
    token: str | None = None,
    enforce_lock: bool = True,
) -> None:
    JournalService.check_founder_access(
        db,
        user_id=user.id,
        startup_id=startup.id,
    )
    if enforce_lock:
        lock.require_unlocked(db, startup_id=startup.id, founder_id=user.id, token=token)


def _today(value: date | None) -> date:
    """The client's local date, trusted only within one day of the server's (timezone skew)."""
    server = date.today()
    return value if value is not None and abs((value - server).days) <= 1 else server


def _month(value: str | None) -> tuple[int, int] | None:
    if value is None:
        return None
    if not _MONTH.match(value):
        raise AppError(
            "VALIDATION_ERROR",
            "month must look like 2026-09.",
            422,
            field_errors=[{"field": "month", "message": "Use YYYY-MM."}],
        )
    return int(value[:4]), int(value[5:])


def _entry_response(
    entry: JournalEntry,
) -> JournalEntryResponse:
    return JournalEntryResponse(
        id=entry.id,
        startup_id=entry.startup_id,
        founder_id=entry.founder_id,
        date=entry.date,
        content=JournalService.get_entry_content(entry),
        mood=JournalService.score_to_mood(entry.mood),
        stress=entry.stress,
    )


def _entry_list_item(
    entry: JournalEntry,
) -> JournalEntryListItem:
    content = JournalService.get_entry_content(entry)

    first_line = content.splitlines()[0] if content.splitlines() else ""

    return JournalEntryListItem(
        id=entry.id,
        date=entry.date,
        mood=JournalService.score_to_mood(entry.mood),
        stress=entry.stress,
        first_line=first_line,
        preview=content[:280],
        word_count=len(content.split()),
    )


# ----------------------------------------------------------------------
# Create
# ----------------------------------------------------------------------


@router.post(
    "/entries",
    response_model=dict[str, Any],
)
def create_journal_entry(
    payload: JournalEntryCreate,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    token: str | None = Depends(_token),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)

    _require_founder(
        db,
        user=user,
        startup=startup,
        token=token,
    )

    entry = JournalService.create_entry(
        db,
        user_id=user.id,
        startup_id=startup.id,
        data=payload,
    )

    db.commit()

    return success_response(_entry_response(entry).model_dump())


# ----------------------------------------------------------------------
# List / search
# ----------------------------------------------------------------------


@router.get(
    "/entries",
    response_model=dict[str, Any],
)
def list_journal_entries(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    search: str | None = Query(default=None, min_length=1),
    month: str | None = Query(default=None),
    token: str | None = Depends(_token),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)

    _require_founder(
        db,
        user=user,
        startup=startup,
        token=token,
    )
    month_key = _month(month)

    if search:
        entries, total = JournalService.search_entries(
            db,
            user_id=user.id,
            startup_id=startup.id,
            query=search,
            limit=limit,
            offset=skip,
            month=month_key,
        )
    else:
        entries, total = JournalService.list_entries(
            db,
            user_id=user.id,
            startup_id=startup.id,
            limit=limit,
            offset=skip,
            month=month_key,
        )

    result = JournalEntryListResponse(
        entries=[_entry_list_item(entry) for entry in entries],
        total=total,
    )

    return success_response(result.model_dump())


# ----------------------------------------------------------------------
# Mood trends
# ----------------------------------------------------------------------


@router.get(
    "/mood",
    response_model=dict[str, Any],
)
def get_mood_trend(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    start_date: date | None = Query(default=None),  # noqa: B008
    end_date: date | None = Query(default=None),  # noqa: B008
    limit: int = Query(default=90, ge=1, le=365),
    range_: str | None = Query(default=None, alias="range", pattern="^(4w|12w|6m|1y)$"),
    token: str | None = Depends(_token),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)

    _require_founder(
        db,
        user=user,
        startup=startup,
        token=token,
    )
    if range_ is not None and start_date is None:
        end_date = end_date or date.today()
        start_date = end_date - timedelta(days=stats.RANGES[range_])

    if start_date is not None and end_date is not None and start_date > end_date:
        raise AppError(
            "VALIDATION_ERROR",
            "start_date cannot be after end_date.",
            422,
            field_errors=[{"field": "start_date", "message": "Must be on or before end_date."}],
        )

    logs = JournalService.get_mood_trend(
        db,
        user_id=user.id,
        startup_id=startup.id,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )

    window_start = start_date or (logs[0].date if logs else date.today())
    window_end = end_date or (logs[-1].date if logs else date.today())
    result = MoodTrendResponse(
        annotations=[
            MoodAnnotation(**a)
            for a in stats.mood_annotations(
                db, startup_id=startup.id, start=window_start, end=window_end
            )
        ],
        points=[
            MoodDataPoint(
                date=log.date,
                mood=JournalService.score_to_mood(log.mood),
                stress=log.stress,
            )
            for log in logs
        ],
    )

    return success_response(result.model_dump())


# ----------------------------------------------------------------------
# Today's prompt
# ----------------------------------------------------------------------


@router.get(
    "/prompts/today",
    response_model=dict[str, Any],
)
def get_today_journal_prompt(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    token: str | None = Depends(_token),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)

    _require_founder(
        db,
        user=user,
        startup=startup,
        token=token,
    )

    row = JournalService.get_or_create_today_prompt(db, startup_id=startup.id, founder_id=user.id)

    db.commit()  # get_or_create_today_prompt may have lazily created today's row + job

    return success_response(
        JournalPromptResponse(prompt=row.prompt, dismissed=row.dismissed).model_dump()
    )


# ----------------------------------------------------------------------
# Month index and by-date lookup (declared before /entries/{entry_id})
# ----------------------------------------------------------------------


@router.get("/entries/months", response_model=dict[str, Any])
def list_entry_months(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    token: str | None = Depends(_token),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    _require_founder(db, user=user, startup=startup, token=token)
    rows = stats.months(db, startup_id=startup.id, founder_id=user.id)
    return success_response(
        JournalMonthsResponse(months=[JournalMonthCount(**r) for r in rows]).model_dump()
    )


@router.get("/entries/by-date/{entry_date}", response_model=dict[str, Any])
def get_entry_by_date(
    entry_date: date,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    token: str | None = Depends(_token),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    _require_founder(db, user=user, startup=startup, token=token)
    entry = JournalService.get_entry_by_date(
        db, user_id=user.id, startup_id=startup.id, entry_date=entry_date
    )
    return success_response(_entry_response(entry).model_dump())


# ----------------------------------------------------------------------
# Get one
# ----------------------------------------------------------------------


@router.get(
    "/entries/{entry_id}",
    response_model=dict[str, Any],
)
def get_journal_entry(
    entry_id: uuid.UUID,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    token: str | None = Depends(_token),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)

    _require_founder(
        db,
        user=user,
        startup=startup,
        token=token,
    )

    entry = JournalService.get_entry(
        db,
        user_id=user.id,
        startup_id=startup.id,
        entry_id=entry_id,
    )

    return success_response(_entry_response(entry).model_dump())


# ----------------------------------------------------------------------
# Update
# ----------------------------------------------------------------------


@router.patch(
    "/entries/{entry_id}",
    response_model=dict[str, Any],
)
def update_journal_entry(
    entry_id: uuid.UUID,
    payload: JournalEntryUpdate,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    token: str | None = Depends(_token),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)

    _require_founder(
        db,
        user=user,
        startup=startup,
        token=token,
    )

    entry = JournalService.update_entry(
        db,
        user_id=user.id,
        startup_id=startup.id,
        entry_id=entry_id,
        data=payload,
    )

    db.commit()

    return success_response(_entry_response(entry).model_dump())


# ----------------------------------------------------------------------
# Delete
# ----------------------------------------------------------------------


@router.delete(
    "/entries/{entry_id}",
    response_model=dict[str, Any],
)
def delete_journal_entry(
    entry_id: uuid.UUID,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    token: str | None = Depends(_token),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)

    _require_founder(
        db,
        user=user,
        startup=startup,
        token=token,
    )

    JournalService.delete_entry(
        db,
        user_id=user.id,
        startup_id=startup.id,
        entry_id=entry_id,
    )

    db.commit()

    return success_response(
        {
            "id": str(entry_id),
            "deleted": True,
        }
    )


# ----------------------------------------------------------------------
# Stats, insights
# ----------------------------------------------------------------------


@router.get("/stats", response_model=dict[str, Any])
def get_stats(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    today: date | None = Query(default=None),  # noqa: B008
    token: str | None = Depends(_token),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    _require_founder(db, user=user, startup=startup, token=token)
    return success_response(
        stats.stats(db, startup_id=startup.id, founder_id=user.id, today=_today(today))
    )


@router.get("/insights", response_model=dict[str, Any])
def get_insights(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    today: date | None = Query(default=None),  # noqa: B008
    token: str | None = Depends(_token),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    _require_founder(db, user=user, startup=startup, token=token)
    row = lock.get_settings(db, startup_id=startup.id, founder_id=user.id)
    result = stats.insights(
        db,
        startup_id=startup.id,
        founder_id=user.id,
        today=_today(today),
        supportive_dismissed_at=row.supportive_dismissed_at,
    )
    db.commit()
    return success_response(result)


@router.post("/insights/supportive/dismiss", response_model=dict[str, Any])
def dismiss_supportive_card(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    token: str | None = Depends(_token),  # noqa: B008
) -> dict[str, Any]:
    from datetime import UTC, datetime

    startup = _startup(db, membership)
    _require_founder(db, user=user, startup=startup, token=token)
    row = lock.get_settings(db, startup_id=startup.id, founder_id=user.id)
    row.supportive_dismissed_at = datetime.now(UTC)
    db.commit()
    return success_response({"dismissed": True})


# ----------------------------------------------------------------------
# Prompts: dismiss, library
# ----------------------------------------------------------------------


@router.post("/prompts/today/dismiss", response_model=dict[str, Any])
def dismiss_today_prompt(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    token: str | None = Depends(_token),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    _require_founder(db, user=user, startup=startup, token=token)
    row = JournalService.get_or_create_today_prompt(db, startup_id=startup.id, founder_id=user.id)
    row.dismissed = True
    db.commit()
    return success_response({"dismissed": True})


@router.get("/prompts/library", response_model=dict[str, Any])
def get_prompt_library(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    theme: str | None = Query(default=None, pattern="^(decisions|energy|team|wins)$"),  # noqa: B008
    token: str | None = Depends(_token),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    _require_founder(db, user=user, startup=startup, token=token)
    return success_response({"themes": prompt_library.library(theme)})


# ----------------------------------------------------------------------
# Settings and passcode (settings + unlock never need an unlock token)
# ----------------------------------------------------------------------


def _settings_payload(row: Any) -> dict[str, Any]:
    locked = row.locked_until
    return JournalSettingsResponse(
        passcode_enabled=row.passcode_hash is not None,
        locked_until=locked,
        daily_prompt_notifications=row.daily_prompt_notifications,
        evening_reminder=row.evening_reminder,
    ).model_dump(mode="json")


@router.get("/settings", response_model=dict[str, Any])
def get_journal_settings(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    _require_founder(db, user=user, startup=startup, enforce_lock=False)
    row = lock.get_settings(db, startup_id=startup.id, founder_id=user.id)
    db.commit()
    return success_response(_settings_payload(row))


@router.put("/settings", response_model=dict[str, Any])
def update_journal_settings(
    payload: JournalSettingsUpdate,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    token: str | None = Depends(_token),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    _require_founder(db, user=user, startup=startup, token=token)
    row = lock.get_settings(db, startup_id=startup.id, founder_id=user.id)
    account.apply_settings(row, payload)
    db.commit()
    return success_response(_settings_payload(row))


@router.put("/passcode", response_model=dict[str, Any])
def set_journal_passcode(
    payload: PasscodeSet,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    _require_founder(db, user=user, startup=startup, enforce_lock=False)
    row = lock.get_settings(db, startup_id=startup.id, founder_id=user.id)
    had = row.passcode_hash is not None
    token = lock.set_passcode(
        db, row, passcode=payload.passcode, current_passcode=payload.current_passcode
    )
    write_audit(
        db,
        "journal.passcode.changed" if had else "journal.passcode.set",
        actor_user_id=user.id,
        startup_id=startup.id,
    )
    db.commit()
    return success_response(
        UnlockResponse(token=token, expires_in=lock.TOKEN_TTL_SECONDS).model_dump()
    )


@router.post("/unlock", response_model=dict[str, Any])
def unlock_journal(
    payload: PasscodeUnlock,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    _require_founder(db, user=user, startup=startup, enforce_lock=False)
    row = lock.get_settings(db, startup_id=startup.id, founder_id=user.id)
    token = lock.unlock(db, row, passcode=payload.passcode)
    db.commit()
    return success_response(
        UnlockResponse(token=token, expires_in=lock.TOKEN_TTL_SECONDS).model_dump()
    )


@router.post("/lock", response_model=dict[str, Any])
def lock_journal(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    _require_founder(db, user=user, startup=startup, enforce_lock=False)
    row = lock.get_settings(db, startup_id=startup.id, founder_id=user.id)
    lock.lock_now(db, row)
    db.commit()
    return success_response({"locked": row.passcode_hash is not None})


@router.delete("/passcode", response_model=dict[str, Any])
def remove_journal_passcode(
    payload: PasscodeRemove,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Forgot or no longer wanted: the ACCOUNT password removes it (entries are untouched)."""
    startup = _startup(db, membership)
    _require_founder(db, user=user, startup=startup, enforce_lock=False)
    if user.password_hash is None or not verify_password(payload.password, user.password_hash):
        raise AppError(
            "PASSWORD_INCORRECT",
            "That password did not match.",
            403,
            field_errors=[{"field": "password", "message": "Incorrect password."}],
        )
    row = lock.get_settings(db, startup_id=startup.id, founder_id=user.id)
    lock.remove_passcode(db, row)
    write_audit(db, "journal.passcode.removed", actor_user_id=user.id, startup_id=startup.id)
    db.commit()
    return success_response(_settings_payload(row))


# ----------------------------------------------------------------------
# Export and permanent delete
# ----------------------------------------------------------------------


@router.get("/export")
def export_journal(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    token: str | None = Depends(_token),  # noqa: B008
) -> Response:
    startup = _startup(db, membership)
    _require_founder(db, user=user, startup=startup, token=token)
    data = account.export_all(db, startup_id=startup.id, founder_id=user.id)
    write_audit(db, "journal.exported", actor_user_id=user.id, startup_id=startup.id)
    db.commit()
    return Response(
        content=json.dumps({"data": data, "meta": None}, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="journal-export.json"',
            "Cache-Control": "no-store",
        },
    )


@router.delete("", response_model=dict[str, Any])
def delete_journal(
    payload: JournalDeleteAll,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    token: str | None = Depends(_token),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    _require_founder(db, user=user, startup=startup, token=token)
    counts = account.delete_all(db, startup_id=startup.id, founder_id=user.id)
    write_audit(db, "journal.deleted", actor_user_id=user.id, startup_id=startup.id)
    db.commit()
    return success_response({"deleted": True, **counts})


# ----------------------------------------------------------------------
# Retros (PRD 21.5)
# ----------------------------------------------------------------------


@router.get("/retros/offers", response_model=dict[str, Any])
def list_retro_offers(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    token: str | None = Depends(_token),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    _require_founder(db, user=user, startup=startup, token=token)
    rows = retros.offers(db, startup_id=startup.id, founder_id=user.id)
    return success_response({"offers": rows})


@router.post("/retros/offers/{milestone_id}/dismiss", response_model=dict[str, Any])
def dismiss_retro_offer(
    milestone_id: uuid.UUID,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    token: str | None = Depends(_token),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    _require_founder(db, user=user, startup=startup, token=token)
    retros.dismiss_offer(db, startup_id=startup.id, founder_id=user.id, milestone_id=milestone_id)
    db.commit()
    return success_response({"dismissed": True})


@router.post("/retros", response_model=dict[str, Any], status_code=201)
def create_retro(
    payload: RetroCreate,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    token: str | None = Depends(_token),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    _require_founder(db, user=user, startup=startup, token=token)
    row = retros.create(
        db,
        startup_id=startup.id,
        founder_id=user.id,
        milestone_id=payload.milestone_id,
        body=RetroBody(**payload.model_dump(exclude={"milestone_id"})),
    )
    db.commit()
    return success_response(retros.present(row))


@router.get("/retros", response_model=dict[str, Any])
def list_retros(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    token: str | None = Depends(_token),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    _require_founder(db, user=user, startup=startup, token=token)
    rows = retros.list_all(db, founder_id=user.id, startup_id=startup.id)
    return success_response({"retros": [retros.present(r) for r in rows]})


@router.get("/retros/{retro_id}", response_model=dict[str, Any])
def get_retro(
    retro_id: uuid.UUID,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    token: str | None = Depends(_token),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    _require_founder(db, user=user, startup=startup, token=token)
    row = retros.get(db, founder_id=user.id, startup_id=startup.id, retro_id=retro_id)
    return success_response(retros.present(row))


@router.put("/retros/{retro_id}", response_model=dict[str, Any])
def update_retro(
    retro_id: uuid.UUID,
    payload: RetroBody,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    token: str | None = Depends(_token),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    _require_founder(db, user=user, startup=startup, token=token)
    row = retros.get(db, founder_id=user.id, startup_id=startup.id, retro_id=retro_id)
    retros.update(db, row, payload)
    db.commit()
    return success_response(retros.present(row))


@router.delete("/retros/{retro_id}", response_model=dict[str, Any])
def delete_retro(
    retro_id: uuid.UUID,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    token: str | None = Depends(_token),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    _require_founder(db, user=user, startup=startup, token=token)
    row = retros.get(db, founder_id=user.id, startup_id=startup.id, retro_id=retro_id)
    retros.delete(db, row)
    db.commit()
    return success_response({"id": str(retro_id), "deleted": True})
