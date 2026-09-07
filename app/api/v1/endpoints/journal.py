from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import AppError, NotFound
from app.db.models.journal import JournalEntry
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_workspace
from app.schemas.journal import (
    JournalEntryCreate,
    JournalEntryListItem,
    JournalEntryListResponse,
    JournalEntryResponse,
    JournalEntryUpdate,
    JournalPromptResponse,
    MoodDataPoint,
    MoodTrendResponse,
)
from app.services.journal.service import JournalService

router = APIRouter()


def _startup(
    db: Session,
    membership: Membership,
) -> Startup:
    startup = db.query(Startup).filter(Startup.id == membership.startup_id).first()

    if startup is None:
        raise NotFound()

    return startup


def _require_founder(
    db: Session,
    *,
    user: User,
    startup: Startup,
) -> None:
    JournalService.check_founder_access(
        db,
        user_id=user.id,
        startup_id=startup.id,
    )


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
        first_line=first_line,
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
) -> dict[str, Any]:
    startup = _startup(db, membership)

    _require_founder(
        db,
        user=user,
        startup=startup,
    )

    entry = JournalService.create_entry(
        db,
        user_id=user.id,
        startup_id=startup.id,
        data=payload,
    )

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
) -> dict[str, Any]:
    startup = _startup(db, membership)

    _require_founder(
        db,
        user=user,
        startup=startup,
    )

    if search:
        entries, total = JournalService.search_entries(
            db,
            user_id=user.id,
            startup_id=startup.id,
            query=search,
            limit=limit,
            offset=skip,
        )
    else:
        entries, total = JournalService.list_entries(
            db,
            user_id=user.id,
            startup_id=startup.id,
            limit=limit,
            offset=skip,
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
) -> dict[str, Any]:
    startup = _startup(db, membership)

    _require_founder(
        db,
        user=user,
        startup=startup,
    )

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

    result = MoodTrendResponse(
        points=[
            MoodDataPoint(
                date=log.date,
                mood=JournalService.score_to_mood(log.mood),
                stress=log.stress,
            )
            for log in logs
        ]
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
) -> dict[str, Any]:
    startup = _startup(db, membership)

    _require_founder(
        db,
        user=user,
        startup=startup,
    )

    prompt = JournalService.get_prompt()

    return success_response(JournalPromptResponse(prompt=prompt).model_dump())


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
) -> dict[str, Any]:
    startup = _startup(db, membership)

    _require_founder(
        db,
        user=user,
        startup=startup,
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
) -> dict[str, Any]:
    startup = _startup(db, membership)

    _require_founder(
        db,
        user=user,
        startup=startup,
    )

    entry = JournalService.update_entry(
        db,
        user_id=user.id,
        startup_id=startup.id,
        entry_id=entry_id,
        data=payload,
    )

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
) -> dict[str, Any]:
    startup = _startup(db, membership)

    _require_founder(
        db,
        user=user,
        startup=startup,
    )

    JournalService.delete_entry(
        db,
        user_id=user.id,
        startup_id=startup.id,
        entry_id=entry_id,
    )

    return success_response(
        {
            "id": str(entry_id),
            "deleted": True,
        }
    )
