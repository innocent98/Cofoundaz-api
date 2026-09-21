from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from app.db.models.enums import RoadmapStatus
from app.db.models.roadmap import (
    Roadmap,
    RoadmapMilestone,
    RoadmapPhase,
    RoadmapReplan,
    RoadmapTask,
    RoadmapTaskDependency,
)
from app.db.models.user import User
from app.platform.events import event_bus

REPLAN_BUFFER_DAYS = 7


@dataclass
class Change:
    change_id: uuid.UUID
    milestone_id: uuid.UUID
    title: str
    old_due: date
    new_due: date
    reason: str


def _milestones(db: Session, roadmap_id: uuid.UUID) -> list[RoadmapMilestone]:
    return (
        db.query(RoadmapMilestone)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .filter(RoadmapPhase.roadmap_id == roadmap_id)
        .all()
    )


def detect_drift(db: Session, roadmap: Roadmap) -> list[RoadmapMilestone]:
    today = date.today()
    return [
        m
        for m in _milestones(db, roadmap.id)
        if m.due_on is not None and m.due_on < today and m.status != RoadmapStatus.done
    ]


def _milestone_precedence(db: Session, roadmap: Roadmap) -> dict[uuid.UUID, set[uuid.UUID]]:
    # task -> its milestone
    task_rows = (
        db.query(RoadmapTask.id, RoadmapTask.milestone_id)
        .join(RoadmapMilestone, RoadmapTask.milestone_id == RoadmapMilestone.id)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .filter(RoadmapPhase.roadmap_id == roadmap.id)
        .all()
    )
    task_ms: dict[uuid.UUID, uuid.UUID] = {}
    for task_id, milestone_id in task_rows:
        task_ms[task_id] = milestone_id
    preds: dict[uuid.UUID, set[uuid.UUID]] = {}
    for dep in db.query(RoadmapTaskDependency).all():
        down_ms = task_ms.get(dep.task_id)  # task depends on depends_on
        up_ms = task_ms.get(dep.depends_on_task_id)  # so depends_on's milestone is upstream
        if down_ms and up_ms and down_ms != up_ms:
            preds.setdefault(down_ms, set()).add(up_ms)
    return preds


def _toposort(nodes: list[uuid.UUID], preds: dict[uuid.UUID, set[uuid.UUID]]) -> list[uuid.UUID]:
    ordered: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()

    def visit(n: uuid.UUID, stack: set[uuid.UUID]) -> None:
        if n in seen or n in stack:  # cycle guard: skip the back-edge
            return
        stack.add(n)
        for up in preds.get(n, ()):
            visit(up, stack)
        stack.discard(n)
        if n not in seen:
            seen.add(n)
            ordered.append(n)

    for n in nodes:
        visit(n, set())
    return ordered  # upstreams appear before downstreams


def compute_replan(db: Session, roadmap: Roadmap) -> list[Change]:
    today = date.today()
    milestones = {m.id: m for m in _milestones(db, roadmap.id)}
    base_shift: dict[uuid.UUID, int] = {}
    for mid, m in milestones.items():
        if m.due_on is not None and m.due_on < today and m.status != RoadmapStatus.done:
            target = today + timedelta(days=REPLAN_BUFFER_DAYS)
            base_shift[mid] = max(0, (target - m.due_on).days)
        else:
            base_shift[mid] = 0

    preds = _milestone_precedence(db, roadmap)
    order = _toposort(list(milestones.keys()), preds)

    shift: dict[uuid.UUID, int] = {}
    max_upstream: dict[uuid.UUID, uuid.UUID | None] = {}
    for mid in order:
        s = base_shift.get(mid, 0)
        winner = None
        for up in preds.get(mid, ()):
            if shift.get(up, 0) > s:
                s = shift[up]
                winner = up
        shift[mid] = s
        max_upstream[mid] = winner

    changes: list[Change] = []
    for mid in order:
        s = shift.get(mid, 0)
        m = milestones[mid]
        if s <= 0 or m.due_on is None:
            continue
        own_shift = base_shift.get(mid, 0)
        winner = max_upstream.get(mid)
        if winner is None:
            # own slip is the sole driver of the shift
            overdue_days = (today - m.due_on).days
            reason = f"{overdue_days} days overdue and not yet done."
        else:
            # an upstream milestone's shift is what actually drove `s`
            up_title = milestones[winner].title
            if own_shift > 0:
                # also self-slipped, but the cascade is the bigger mover — say both
                overdue_days = (today - m.due_on).days
                reason = (
                    f"{overdue_days} days overdue; shifts {s} days with its "
                    f"dependency '{up_title}'."
                )
            else:
                reason = f"Shifts {s} days with its dependency '{up_title}'."
        changes.append(Change(mid, mid, m.title, m.due_on, m.due_on + timedelta(days=s), reason))
    return changes


def apply_replan(db: Session, roadmap: Roadmap, actor: User, change_ids: list[uuid.UUID]) -> dict:
    proposal = {c.change_id: c for c in compute_replan(db, roadmap)}
    now = datetime.now(UTC)
    applied: list[str] = []
    skipped: list[str] = []
    snapshot: list[dict] = []

    for cid in change_ids:
        c = proposal.get(cid)
        if c is None:
            skipped.append(str(cid))
            continue
        m = db.get(RoadmapMilestone, c.milestone_id)
        # The proposal was derived from these same live milestones moments ago, so
        # this narrows the Optional for mypy rather than guarding a real case.
        # Bandit flags it (B101) because `python -O` strips asserts: under -O this
        # line vanishes and a None would fall through to the attribute writes below.
        # Kept as an assert rather than a silent `continue` so that a violated
        # invariant stays loud; the service is not run with -O.
        assert m is not None  # nosec B101
        m.due_on = c.new_due
        m.last_replanned_at = now
        m.last_replan_reason = c.reason
        applied.append(str(cid))
        snapshot.append(
            {
                "milestone_id": str(c.milestone_id),
                "title": c.title,
                "old_due": c.old_due.isoformat(),
                "new_due": c.new_due.isoformat(),
                "reason": c.reason,
            }
        )

    replan_id: str | None = None
    summary: str | None = None
    if applied:
        n = len(applied)
        summary = f"Re-planned {n} milestone{'s' if n != 1 else ''}"
        replan = RoadmapReplan(
            roadmap_id=roadmap.id,
            applied_by=actor.id,
            change_count=n,
            changes=snapshot,
            summary=summary,
        )
        db.add(replan)
        db.flush()
        replan_id = str(replan.id)
        event_bus.publish(
            db,
            "roadmap.replanned",
            {
                "startup_id": str(roadmap.startup_id),
                "roadmap_id": str(roadmap.id),
                "replan_id": replan_id,
                "change_count": n,
                "applied_by": str(actor.id),
            },
        )
    db.flush()
    return {"applied": applied, "skipped": skipped, "replan_id": replan_id, "summary": summary}
