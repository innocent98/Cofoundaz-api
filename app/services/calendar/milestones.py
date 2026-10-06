import uuid
from datetime import UTC, date, datetime

from sqlalchemy import nullslast
from sqlalchemy.orm import Session

from app.db.models.enums import RoadmapStatus
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase
from app.schemas.calendar import MilestoneResponse

ON_TRACK = "on_track"
AT_RISK = "at_risk"
COMPLETE = "complete"
NOT_STARTED = "not_started"


def calendar_status(status: RoadmapStatus, due_on: date | None, today: date) -> str:
    """The four labels the FE badges, derived — RoadmapStatus has no notion of risk.

    Order matters: a finished milestone is never "at risk" however old its due date, and being
    overdue outranks being in progress. RoadmapStatus is exactly {todo, in_progress, done}, so
    the final branch can only be `todo`.
    """
    if status == RoadmapStatus.done:
        return COMPLETE
    if due_on is not None and due_on < today:
        return AT_RISK
    if status == RoadmapStatus.in_progress:
        return ON_TRACK
    return NOT_STARTED


def serialize_milestone(row: RoadmapMilestone, *, today: date) -> MilestoneResponse:
    # `tasksInfo` is NOT returned: the FE renders "11 of 13 tasks complete" but nothing in the
    # schema backs it. Omitted rather than faked, and labelled not-backed in the FE guide.
    return MilestoneResponse(
        id=row.id,
        title=row.title,
        due_on=row.due_on,
        status=row.status,
        calendar_status=calendar_status(row.status, row.due_on, today),
        progress=row.progress,
    )


def list_milestones(
    db: Session, *, startup_id: uuid.UUID, today: date | None = None
) -> list[MilestoneResponse]:
    """Read-only projection of this workspace's roadmap milestones (spec D1).

    `RoadmapMilestone` carries no `startup_id`; it scopes through
    `phase_id -> RoadmapPhase.roadmap_id -> Roadmap.startup_id`, so this three-table join IS the
    tenancy boundary. Mirrors `_due_overdue_milestones` in `app/worker/scheduler.py`, with two
    deliberate differences:

    * no `Startup.deleted_at` filter — that function scans every workspace, while this read
      already has an active `Membership` for this one (`resolve_workspace` applies no such
      filter either, so adding one here would be inconsistent with the rest of the app);
    * "today" from `datetime.now(UTC).date()` rather than its `_local(now)`, which depends on
      the `SCHEDULER_TIMEZONE` setting. The two agree while that setting is "UTC", but the
      calendar's clock should not move when a scheduler config value changes (spec D6).
    """
    as_of = today or datetime.now(UTC).date()
    rows = (
        db.query(RoadmapMilestone)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .join(Roadmap, RoadmapPhase.roadmap_id == Roadmap.id)
        .filter(Roadmap.startup_id == startup_id)
        .order_by(
            # Undated milestones sort last; Postgres would otherwise put the NULLs first on ASC.
            nullslast(RoadmapMilestone.due_on.asc()),
            RoadmapMilestone.created_at.asc(),
        )
        .all()
    )
    return [serialize_milestone(row, today=as_of) for row in rows]
