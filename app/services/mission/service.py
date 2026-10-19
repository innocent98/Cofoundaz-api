"""Lazy-on-read mission generation, derived streak, and mission serialization.

READ-ONLY against the roadmap: this module only queries `Roadmap`/`RoadmapPhase`/
`RoadmapMilestone`/`RoadmapTask` to select candidate tasks -- it never writes to
those tables. `mission_tasks.roadmap_task_id` is an unconstrained soft link (no
FK), so a roadmap task disappearing later never blocks or cascades on a mission
snapshot that already copied its title/effort.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import exists, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.enums import MissionStatus, MissionTaskStatus, RoadmapStatus, TaskEffort
from app.db.models.mission import Mission, MissionSettings, MissionTask
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase, RoadmapTask
from app.db.models.startup import Startup
from app.platform.events import event_bus
from app.platform.jobs import job_dispatcher
from app.services.roadmap.service import recompute_milestone_progress

_DEFAULT_MISSION_SIZE = 3
_WEEKEND_ISO_WEEKDAYS = (5, 6)  # Saturday, Sunday (date.weekday())
_STREAK_MILESTONES = (7, 30, 100)
_WEEKLY_WINDOW_DAYS = 7  # "this week" = the last 7 days ending today (inclusive)

# The only reject-reason chips the FE offers -- kept here (not in the schema) so
# the endpoint and any future caller validate against one source of truth.
REASON_ALREADY_DONE = "Already done"
REASON_WRONG_PRIORITY = "Wrong priority"
REASON_DOESNT_APPLY = "Doesn't apply"
VALID_REJECT_REASONS = frozenset({REASON_ALREADY_DONE, REASON_WRONG_PRIORITY, REASON_DOESNT_APPLY})
# D-MIS-7 "Thanks, I'll calibrate.": a "Wrong priority" task rests this many days before a
# mission offers it again; a "Doesn't apply" task is never offered again (it stays on the roadmap).
_WRONG_PRIORITY_REST_DAYS = 7
# A snoozed task has moved to tomorrow, so like done/rejected it no longer blocks today.
_RESOLVED_TODAY = (MissionTaskStatus.done, MissionTaskStatus.rejected, MissionTaskStatus.snoozed)
# Tasks that don't count toward a day's work at all (history totals, weekly %).
_NOT_TODAYS_WORK = (MissionTaskStatus.rejected, MissionTaskStatus.snoozed)


def _today() -> date:
    return date.today()


def _roadmap(db: Session, startup: Startup) -> Roadmap | None:
    return db.query(Roadmap).filter_by(startup_id=startup.id).first()


def _settings(db: Session, startup: Startup) -> MissionSettings | None:
    return db.query(MissionSettings).filter_by(startup_id=startup.id).first()


def _candidate_tasks(db: Session, roadmap: Roadmap) -> list[RoadmapTask]:
    """Incomplete roadmap tasks for `roadmap`, in generation priority order."""
    return (
        db.query(RoadmapTask)
        .join(RoadmapMilestone, RoadmapTask.milestone_id == RoadmapMilestone.id)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .filter(
            RoadmapPhase.roadmap_id == roadmap.id,
            RoadmapTask.status != RoadmapStatus.done,
        )
        .order_by(
            # The founder's own order (PRD 04.2 "editable order") wins; unranked tasks keep
            # the default milestone/due-date order after the ranked ones.
            RoadmapTask.mission_rank.asc().nullslast(),
            RoadmapMilestone.due_on.asc().nullslast(),
            RoadmapPhase.order,
            RoadmapMilestone.order,
            RoadmapTask.order,
        )
        .all()
    )


def _mission_candidates(db: Session, roadmap: Roadmap, today: date) -> list[RoadmapTask]:
    """`_candidate_tasks` minus the ones the founder calibrated out of missions (D-MIS-7)."""
    rejected_as = (
        exists()
        .where(MissionTask.roadmap_task_id == RoadmapTask.id)
        .where(MissionTask.status == MissionTaskStatus.rejected)
    )
    doesnt_apply = rejected_as.where(MissionTask.reject_reason == REASON_DOESNT_APPLY)
    resting = (
        rejected_as.where(MissionTask.reject_reason == REASON_WRONG_PRIORITY)
        .where(Mission.id == MissionTask.mission_id)
        .where(Mission.mission_date > today - timedelta(days=_WRONG_PRIORITY_REST_DAYS))
    )
    excluded = {
        row[0]
        for row in db.query(RoadmapTask.id)
        .join(RoadmapMilestone, RoadmapTask.milestone_id == RoadmapMilestone.id)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .filter(RoadmapPhase.roadmap_id == roadmap.id, doesnt_apply | resting)
    }
    return [t for t in _candidate_tasks(db, roadmap) if t.id not in excluded]


def _complete_roadmap_task(db: Session, task: MissionTask) -> None:
    """Mission work is roadmap work: finishing a roadmap-linked mission task finishes the
    roadmap task too, so the milestone moves and tomorrow's mission doesn't offer it again."""
    if task.roadmap_task_id is None:
        return
    roadmap_task = db.query(RoadmapTask).filter_by(id=task.roadmap_task_id).first()
    if roadmap_task is None or roadmap_task.status == RoadmapStatus.done:
        return
    roadmap_task.status = RoadmapStatus.done
    db.flush()
    milestone = db.query(RoadmapMilestone).filter_by(id=roadmap_task.milestone_id).first()
    if milestone is not None:
        recompute_milestone_progress(db, milestone)
        db.flush()


def _most_recent_prior_mission(db: Session, startup: Startup, today: date) -> Mission | None:
    return (
        db.query(Mission)
        .filter(Mission.startup_id == startup.id, Mission.mission_date < today)
        .order_by(Mission.mission_date.desc())
        .first()
    )


def _snoozed_tasks(db: Session, mission: Mission) -> list[MissionTask]:
    return (
        db.query(MissionTask)
        .filter_by(mission_id=mission.id, status=MissionTaskStatus.snoozed)
        .order_by(MissionTask.order)
        .all()
    )


def _enqueue_mission_reason(db: Session, mission: Mission, startup: Startup, order: int) -> None:
    """Queue the `ai.mission.reason` job for a freshly-generated mission.

    `order` is the task counter from `get_or_generate_today`: 0 for an empty
    (e.g. weekend) mission, which has no task to explain and so is skipped.
    """
    if order:
        job_dispatcher.enqueue(db, "ai.mission.reason", {"mission_id": str(mission.id)}, startup.id)


def get_or_generate_today(db: Session, startup: Startup) -> Mission | None:
    today = _today()

    existing = db.query(Mission).filter_by(startup_id=startup.id, mission_date=today).first()
    if existing is not None:
        return existing

    roadmap = _roadmap(db, startup)
    if roadmap is None:
        return None

    settings = _settings(db, startup)
    mission_size = settings.mission_size if settings is not None else _DEFAULT_MISSION_SIZE
    weekend_missions = settings.weekend_missions if settings is not None else False

    mission = Mission(
        startup_id=startup.id,
        mission_date=today,
        generated_by="system",
        status=MissionStatus.pending,
    )
    db.add(mission)
    db.flush()

    if today.weekday() in _WEEKEND_ISO_WEEKDAYS and not weekend_missions:
        return mission  # empty mission -- "weekends off"

    prior = _most_recent_prior_mission(db, startup, today)
    carried = _snoozed_tasks(db, prior) if prior is not None else []

    order = 0
    carried_roadmap_ids: set[uuid.UUID] = set()
    for snoozed in carried:
        if order >= mission_size:
            break
        db.add(
            MissionTask(
                mission_id=mission.id,
                roadmap_task_id=snoozed.roadmap_task_id,
                title=snoozed.title,
                reason=snoozed.reason,
                effort=snoozed.effort,
                order=order,
            )
        )
        if snoozed.roadmap_task_id is not None:
            carried_roadmap_ids.add(snoozed.roadmap_task_id)
        order += 1

    if order < mission_size:
        milestones_by_id: dict[uuid.UUID, RoadmapMilestone] = {}
        for task in _mission_candidates(db, roadmap, today):
            if order >= mission_size:
                break
            if task.id in carried_roadmap_ids:
                continue  # already carried forward this cycle -- don't duplicate
            milestone = milestones_by_id.get(task.milestone_id)
            if milestone is None:
                milestone = db.query(RoadmapMilestone).filter_by(id=task.milestone_id).first()
                if milestone is not None:
                    milestones_by_id[task.milestone_id] = milestone
            reason = f"From your '{milestone.title}' milestone." if milestone is not None else None
            db.add(
                MissionTask(
                    mission_id=mission.id,
                    roadmap_task_id=task.id,
                    title=task.title,
                    reason=reason,
                    effort=task.effort,
                    order=order,
                )
            )
            order += 1

    db.flush()
    _enqueue_mission_reason(db, mission, startup, order)
    return mission


def ensure_today_mission(db: Session, startup: Startup) -> Mission:
    """Today's mission, creating it if needed -- never None, race-safe.

    `get_or_generate_today` does an unguarded check-then-INSERT against
    `uq_mission_startup_date`, and returns None when there is no roadmap yet. For callers
    that must add a task (a custom task, a briefing "Do it"), run it in a SAVEPOINT so a
    losing concurrent racer re-selects the winner's row, and start an empty user mission
    when there's no roadmap rather than blocking on roadmap generation.
    """
    today = _today()
    try:
        with db.begin_nested():
            mission = get_or_generate_today(db, startup)
            if mission is None:
                mission = Mission(
                    startup_id=startup.id,
                    mission_date=today,
                    generated_by="user",
                    status=MissionStatus.pending,
                )
                db.add(mission)
                db.flush()
            return mission
    except IntegrityError:
        winner = db.query(Mission).filter_by(startup_id=startup.id, mission_date=today).first()
        if winner is None:  # pragma: no cover - the conflicting row vanished mid-request
            raise
        return winner


def mission_empty_reason(db: Session, startup: Startup, mission: Mission | None) -> str | None:
    """Why today's mission has no tasks, so the UI can say the right thing (PRD 04.5).

    `no_roadmap` (nothing to draw from yet) · `weekend_off` (weekends-off setting) ·
    `all_caught_up` (no open roadmap tasks left). None when there are tasks.
    """
    if mission is None:
        return "no_roadmap"
    if db.query(MissionTask.id).filter_by(mission_id=mission.id).first() is not None:
        return None
    settings = _settings(db, startup)
    weekend_missions = settings.weekend_missions if settings is not None else False
    if mission.mission_date.weekday() in _WEEKEND_ISO_WEEKDAYS and not weekend_missions:
        return "weekend_off"
    return "all_caught_up"


def _rest_day(db: Session, startup: Startup, mission: Mission | None, day: date) -> bool:
    """A weekends-off day with no tasks. It neither extends nor breaks a streak
    ("Rest is a strategy too.", PRD 04.5)."""
    if day.weekday() not in _WEEKEND_ISO_WEEKDAYS:
        return False
    settings = _settings(db, startup)
    if settings is not None and settings.weekend_missions:
        return False
    if mission is None:
        return True
    return db.query(MissionTask.id).filter_by(mission_id=mission.id).first() is None


def streak(db: Session, startup: Startup) -> int:
    """Consecutive completed missions ending today (or yesterday, if today isn't complete
    yet). Weekends-off rest days are skipped, not counted as breaks. Derived on read."""
    today = _today()
    by_date = {
        m.mission_date: m
        for m in db.query(Mission).filter(
            Mission.startup_id == startup.id, Mission.mission_date <= today
        )
    }
    if not by_date:
        return 0
    earliest = min(by_date)
    todays = by_date.get(today)
    cursor = (
        today
        if todays is not None and todays.status == MissionStatus.complete
        else today - timedelta(days=1)
    )
    count = 0
    while True:
        mission = by_date.get(cursor)
        if mission is not None and mission.status == MissionStatus.complete:
            count += 1
        elif not _rest_day(db, startup, mission, cursor):
            break
        cursor -= timedelta(days=1)
        if cursor < earliest:
            break
    return count


def best_streak(db: Session, startup: Startup) -> int:
    """Longest run of completed missions ever, under the same rules as `streak`."""
    missions = (
        db.query(Mission)
        .filter(Mission.startup_id == startup.id)
        .order_by(Mission.mission_date.asc())
        .all()
    )
    if not missions:
        return 0
    by_date = {m.mission_date: m for m in missions}
    best = run = 0
    day, last = missions[0].mission_date, missions[-1].mission_date
    while day <= last:
        mission = by_date.get(day)
        if mission is not None and mission.status == MissionStatus.complete:
            run += 1
            best = max(best, run)
        elif not _rest_day(db, startup, mission, day):
            run = 0
        day += timedelta(days=1)
    return best


def milestones_for(db: Session, roadmap_task_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    """`{roadmap_task_id: {"id", "title", "due_on"}}` for the milestone chip on task cards."""
    if not roadmap_task_ids:
        return {}
    rows = (
        db.query(
            RoadmapTask.id, RoadmapMilestone.id, RoadmapMilestone.title, RoadmapMilestone.due_on
        )
        .join(RoadmapMilestone, RoadmapTask.milestone_id == RoadmapMilestone.id)
        .filter(RoadmapTask.id.in_(roadmap_task_ids))
        .all()
    )
    return {
        tid: {"id": str(mid), "title": title, "due_on": due.isoformat() if due else None}
        for tid, mid, title, due in rows
    }


def serialize_task(t: MissionTask, milestone: dict | None = None) -> dict:
    return {
        "id": str(t.id),
        "roadmap_task_id": str(t.roadmap_task_id) if t.roadmap_task_id else None,
        "title": t.title,
        "reason": t.reason,
        "effort": t.effort.value,
        "status": t.status.value,
        "order": t.order,
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        "reject_reason": t.reject_reason,
        # The roadmap milestone this task advances (null for custom tasks): PRD 04.1 chip.
        "milestone": milestone,
    }


def serialize_mission(db: Session, mission: Mission, streak: int) -> dict:
    tasks = db.query(MissionTask).filter_by(mission_id=mission.id).order_by(MissionTask.order).all()
    chips = milestones_for(db, [t.roadmap_task_id for t in tasks if t.roadmap_task_id])
    return {
        "mission_date": mission.mission_date.isoformat(),
        "status": mission.status.value,
        "streak": streak,
        "tasks": [
            serialize_task(t, chips.get(t.roadmap_task_id) if t.roadmap_task_id else None)
            for t in tasks
        ],
    }


def _weekly_completion_pct(db: Session, startup: Startup) -> int:
    """Share (rounded %) of the workspace's *non-empty* missions in the last 7 days
    (ending today, inclusive) that reached `complete`.

    Only missions with at least one task that is neither `rejected` nor `snoozed` count -- toward both the
    numerator and the denominator. This deliberately excludes the empty, perpetually
    `pending` mission that a weekends-off day materialises (`get_or_generate_today`
    creates the row, then the weekend branch returns it task-less): counting those
    would punish exactly the "weekends off" behaviour the product intends. An
    all-rejected mission is excluded for the same reason. A genuinely-complete
    mission always has >=1 `done` task, so it is never excluded. Returns 0 when the
    filtered window is empty (no divide-by-zero -- the short-circuit is applied
    AFTER the non-empty filter)."""
    today = _today()
    window_start = today - timedelta(days=_WEEKLY_WINDOW_DAYS - 1)
    has_actionable_task = (
        exists()
        .where(MissionTask.mission_id == Mission.id)
        .where(MissionTask.status.notin_(_NOT_TODAYS_WORK))
    )
    in_window = (
        Mission.startup_id == startup.id,
        Mission.mission_date >= window_start,
        Mission.mission_date <= today,
        has_actionable_task,
    )
    total = db.query(Mission).filter(*in_window).count()
    if total == 0:
        return 0
    completed = (
        db.query(Mission).filter(*in_window, Mission.status == MissionStatus.complete).count()
    )
    return round(completed / total * 100)


def _history_counts(db: Session, startup: Startup) -> dict[uuid.UUID, tuple[int, int]]:
    """`mission_id -> (completed, total)` for every mission in the workspace, in one
    grouped query.

    `total` counts tasks that are neither `rejected` nor `snoozed` (moved to tomorrow);
    `completed` counts `done`. This
    keeps the row coherent with `complete_task`'s all-done check (which also ignores
    `rejected`): a mission whose only unfinished work was rejected reads as fully
    done (e.g. 1/1) rather than a contradictory 1/2-but-complete."""
    rows = (
        db.query(
            MissionTask.mission_id,
            func.count(MissionTask.id)
            .filter(MissionTask.status == MissionTaskStatus.done)
            .label("completed"),
            func.count(MissionTask.id)
            .filter(MissionTask.status.notin_(_NOT_TODAYS_WORK))
            .label("total"),
        )
        .join(Mission, MissionTask.mission_id == Mission.id)
        .filter(Mission.startup_id == startup.id)
        .group_by(MissionTask.mission_id)
        .all()
    )
    return {mission_id: (completed, total) for mission_id, completed, total in rows}


def mission_history(db: Session, startup: Startup) -> dict:
    """Every mission for the workspace, newest first, each with `completed`/`total`
    task counts and its `status`, plus the rolling 7-day completion percentage.

    Read-only: unlike `get_or_generate_today` this never creates today's mission --
    it reports the missions that already exist (which is why a mission only appears
    here once `/today` or a task action has materialised it)."""
    missions = (
        db.query(Mission)
        .filter(Mission.startup_id == startup.id)
        .order_by(Mission.mission_date.desc())
        .all()
    )
    counts = _history_counts(db, startup)
    return {
        "missions": [
            {
                "mission_date": m.mission_date.isoformat(),
                "completed": counts.get(m.id, (0, 0))[0],
                "total": counts.get(m.id, (0, 0))[1],
                "status": m.status.value,
            }
            for m in missions
        ],
        "weekly_completion_pct": _weekly_completion_pct(db, startup),
        "current_streak": streak(db, startup),
        "best_streak": best_streak(db, startup),
    }


def add_custom_task(
    db: Session, mission: Mission, title: str, effort: TaskEffort | None = None
) -> MissionTask:
    """Append a user-authored task to `mission` -- no roadmap link, appended order."""
    max_order = db.query(func.max(MissionTask.order)).filter_by(mission_id=mission.id).scalar()
    next_order = 0 if max_order is None else max_order + 1
    task = MissionTask(
        mission_id=mission.id,
        roadmap_task_id=None,
        title=title,
        effort=effort or TaskEffort.medium,
        order=next_order,
        status=MissionTaskStatus.todo,
    )
    db.add(task)
    db.flush()
    return task


def _maybe_complete_mission(db: Session, mission: Mission) -> None:
    """Flip `mission` to complete once every non-rejected task is done, emit
    `mission.completed`, and check the streak-milestone crossing.

    Shared by `complete_task` and `reject_task` -- either action can clear the
    last blocker (a mission with 2 done + 1 rejected is just as complete as one
    with all 3 done). Guarded so a mission where *every* task is rejected and
    none is done never falsely flips to complete (that would inflate the
    streak for a day the founder did zero work on).
    """
    if mission.status == MissionStatus.complete:
        return

    remaining = (
        db.query(MissionTask)
        .filter(
            MissionTask.mission_id == mission.id,
            MissionTask.status.notin_(_RESOLVED_TODAY),
        )
        .count()
    )
    if remaining != 0:
        return

    done_count = (
        db.query(MissionTask)
        .filter(MissionTask.mission_id == mission.id, MissionTask.status == MissionTaskStatus.done)
        .count()
    )
    if done_count == 0:
        return

    mission.status = MissionStatus.complete
    db.flush()

    event_bus.publish(
        db,
        "mission.completed",
        {
            "startup_id": str(mission.startup_id),
            "mission_id": str(mission.id),
            "mission_date": mission.mission_date.isoformat(),
        },
    )

    startup = db.query(Startup).filter_by(id=mission.startup_id).first()
    if startup is not None:
        new_streak = streak(db, startup)
        if new_streak in _STREAK_MILESTONES:
            event_bus.publish(
                db,
                "mission.streak.milestone",
                {"startup_id": str(mission.startup_id), "streak": new_streak},
            )


def complete_task(db: Session, startup: Startup, task: MissionTask) -> MissionTask:
    """Mark `task` done and cascade: mission-complete + streak-milestone events.

    Idempotent -- re-completing an already-done task is a no-op (no event
    re-fires, `completed_at` isn't clobbered with a fresh timestamp).
    """
    if task.status == MissionTaskStatus.done:
        return task
    task.status = MissionTaskStatus.done
    task.completed_at = datetime.now(UTC)
    db.flush()
    _complete_roadmap_task(db, task)

    event_bus.publish(
        db,
        "mission.task.completed",
        {
            "startup_id": str(startup.id),
            "mission_id": str(task.mission_id),
            "task_id": str(task.id),
        },
    )

    mission = db.query(Mission).filter_by(id=task.mission_id).first()
    if mission is not None:
        _maybe_complete_mission(db, mission)
    return task


def snooze_task(db: Session, task: MissionTask) -> MissionTask:
    """Move `task` to tomorrow. Snoozing the last open task completes today's mission when
    something else was done (a snoozed task no longer blocks "Mission complete.")."""
    task.status = MissionTaskStatus.snoozed
    db.flush()
    mission = db.query(Mission).filter_by(id=task.mission_id).first()
    if mission is not None:
        _maybe_complete_mission(db, mission)
    return task


def reorder_task(db: Session, task: MissionTask, order: int) -> MissionTask:
    task.order = order
    db.flush()
    return task


def reject_task(db: Session, task: MissionTask, reject_reason: str) -> MissionTask:
    """Reject `task`, then re-check mission completion -- rejecting the last
    remaining blocker (with at least one other task already done) completes
    the mission just as surely as completing it would."""
    task.status = MissionTaskStatus.rejected
    task.reject_reason = reject_reason
    db.flush()
    if reject_reason == REASON_ALREADY_DONE:
        _complete_roadmap_task(db, task)

    mission = db.query(Mission).filter_by(id=task.mission_id).first()
    if mission is not None:
        _maybe_complete_mission(db, mission)
    return task
