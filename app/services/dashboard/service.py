import base64
import binascii
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models.activity import ActivityLog
from app.db.models.enums import MissionTaskStatus, RoadmapStatus
from app.db.models.mission import Mission, MissionTask
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase
from app.db.models.startup import Startup
from app.db.models.user import User, UserProfile
from app.services.health_score.service import get_overview, latest_completed_result
from app.services.mission.service import get_or_generate_today, serialize_mission, streak

UPCOMING_WINDOW_DAYS = 7

_BRIEFING_EMPTY = (
    "I'll have your first briefing ready tomorrow morning once I've seen a full day "
    "of your workspace."
)
_RISKS_EMPTY = "No open risks. I'm watching runway, deadlines, and pipeline for you."
_OPPS_EMPTY = "Opportunities I spot — grants, quick wins, market signals — will show up here."


def _salutation(now: datetime) -> str:
    h = now.hour
    if h < 12:
        return "Good morning"
    if h < 18:
        return "Good afternoon"
    return "Good evening"


def _first_name(user: User) -> str:
    name = (user.profile.full_name if user.profile else None) or ""
    return name.split(" ")[0] if name else ""


def _section(fn: Any) -> Any:
    """Run fn(), collapsing any exception to {"error": True}.

    Used ONLY for `health`: `get_overview` commits internally
    (app/services/health_score/service.py:190), and an internal `commit()` issued from
    inside a SAVEPOINT block (see `_section_isolated` below) breaks the SAVEPOINT --
    so `health` can't use the savepoint-wrapped variant every other section uses.
    """
    try:
        return fn()
    except Exception:  # noqa: BLE001 - per-card isolation: one failure must not 500 the page
        return {"error": True}


def _section_isolated(db: Session, fn: Any) -> Any:
    """Like `_section`, but also isolates DB statement errors, not just Python-level ones.

    A failed flush/statement (e.g. a unique-constraint violation) marks the *whole*
    shared `Session` rollback-only. Without isolation, every later section would then
    raise `PendingRollbackError`, and so would the endpoint's final `db.commit()`
    (app/api/v1/endpoints/dashboard.py:37) -- one card's DB error would 500 the entire
    page instead of just that card. Running fn() inside its own SAVEPOINT means a
    failure only rolls back that SAVEPOINT, leaving the outer session -- and every
    other section -- usable.

    NOT used for `health` (see `_section`'s docstring).
    """
    try:
        with db.begin_nested():
            return fn()
    except Exception:  # noqa: BLE001 - per-card isolation: one failure must not 500 the page
        return {"error": True}


def _upcoming(db: Session, startup: Startup) -> list[dict[str, Any]]:
    today = date.today()
    rows = (
        db.query(RoadmapMilestone)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .join(Roadmap, RoadmapPhase.roadmap_id == Roadmap.id)
        .filter(Roadmap.startup_id == startup.id)
        .filter(RoadmapMilestone.due_on >= today)
        .filter(RoadmapMilestone.due_on <= today + timedelta(days=UPCOMING_WINDOW_DAYS))
        .filter(RoadmapMilestone.status != RoadmapStatus.done)
        .order_by(RoadmapMilestone.due_on.asc())
        .all()
    )
    result = []
    for m in rows:
        assert m.due_on is not None  # guaranteed by the due_on >= today filter above
        result.append(
            {
                "id": str(m.id),
                "title": m.title,
                "due_on": m.due_on.isoformat(),
                "milestone_id": str(m.id),
            }
        )
    return result


def _tasks_done_this_week(db: Session, startup: Startup) -> int:
    # Keyed on `MissionTask.completed_at`, not the owning Mission's `mission_date`:
    # `complete_task` (app/services/mission/service.py) is the only production path that
    # sets `status=done`, and it always sets `completed_at` atomically in the same call —
    # so a task can be dated to a mission from over a week ago yet genuinely completed
    # today, and that must count. `completed_at` is tz-aware (DateTime(timezone=True)), so
    # `since` must be tz-aware too or the comparison raises/misbehaves against Postgres.
    since = datetime.now(UTC) - timedelta(days=7)
    return (
        db.query(MissionTask)
        .join(Mission, MissionTask.mission_id == Mission.id)
        .filter(Mission.startup_id == startup.id)
        .filter(MissionTask.status == MissionTaskStatus.done)
        .filter(MissionTask.completed_at >= since)
        .count()
    )


def _get_or_generate_today_race_safe(db: Session, startup: Startup) -> Mission | None:
    """Race-safe wrapper around `get_or_generate_today`.

    `get_or_generate_today` (app/services/mission/service.py:84-106) does an unguarded
    check-then-INSERT against the `uq_mission_startup_date` unique constraint. Two
    concurrent first-loads-of-the-day for the same startup (two summary polls, or a
    summary racing `GET /missions/today`) can both pass its `existing is None` check and
    both INSERT -- the loser's flush raises `IntegrityError`. Run the call inside a
    SAVEPOINT so a losing racer rolls back only its own attempt, then re-select today's
    mission: under READ COMMITTED the winner's now-committed row is visible.
    """
    try:
        with db.begin_nested():
            return get_or_generate_today(db, startup)
    except IntegrityError:
        return db.query(Mission).filter_by(startup_id=startup.id, mission_date=date.today()).first()


def _mission_section(db: Session, startup: Startup) -> Any:
    m = _get_or_generate_today_race_safe(db, startup)
    if m is None:
        return None
    return serialize_mission(db, m, streak(db, startup))


def get_summary(db: Session, startup: Startup, user: User) -> dict[str, Any]:
    return {
        "greeting": {
            "salutation": _salutation(datetime.now()),
            "first_name": _first_name(user),
            "startup_name": startup.name,
        },
        "health": _section(lambda: get_overview(db, startup)),
        "mission": _section_isolated(db, lambda: _mission_section(db, startup)),
        "upcoming": _section_isolated(db, lambda: _upcoming(db, startup)),
        "kpis": _section_isolated(
            db,
            lambda: {
                "tasks_done_this_week": _tasks_done_this_week(db, startup),
                "revenue": None,
                "runway": None,
                "pipeline_value": None,
                "campaign_performance": None,
            },
        ),
        "calibration": _section_isolated(
            db, lambda: {"assessment_complete": latest_completed_result(db, startup.id) is not None}
        ),
        "briefing": {"status": "empty", "message": _BRIEFING_EMPTY},
        "risks": {"status": "empty", "message": _RISKS_EMPTY},
        "opportunities": {"status": "empty", "message": _OPPS_EMPTY},
    }


def _encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{row_id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts, row_id = raw.split("|", 1)
        return datetime.fromisoformat(ts), uuid.UUID(row_id)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise AppError(
            "VALIDATION_ERROR",
            "Invalid pagination cursor.",
            422,
            field_errors=[{"field": "cursor", "message": "This page link is no longer valid."}],
        ) from exc


def get_activity(
    db: Session, startup: Startup, cursor: str | None = None, limit: int = 20
) -> dict[str, Any]:
    limit = max(1, min(limit, 50))
    q = (
        db.query(ActivityLog, UserProfile.full_name)
        .outerjoin(UserProfile, UserProfile.user_id == ActivityLog.actor_user_id)
        .filter(ActivityLog.startup_id == startup.id)
        .order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc())
    )
    if cursor:
        c_ts, c_id = _decode_cursor(cursor)
        q = q.filter(
            (ActivityLog.created_at < c_ts)
            | ((ActivityLog.created_at == c_ts) & (ActivityLog.id < c_id))
        )
    rows = q.limit(limit + 1).all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    items = []
    for row, full_name in rows:
        actor = (
            {"id": str(row.actor_user_id), "name": full_name}
            if row.actor_user_id is not None
            else None
        )
        items.append(
            {
                "id": str(row.id),
                "action": row.action,
                "entity_type": row.entity_type,
                "entity_id": str(row.entity_id) if row.entity_id else None,
                "summary": row.summary,
                "meta": row.meta,
                "actor": actor,
                "created_at": row.created_at.isoformat(),
            }
        )
    next_cursor = None
    if has_more and rows:
        last, _ = rows[-1]
        next_cursor = _encode_cursor(last.created_at, last.id)
    return {"items": items, "next_cursor": next_cursor}
