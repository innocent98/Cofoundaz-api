from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.db.models.enums import RoadmapStatus
from app.db.models.roadmap import (
    Roadmap,
    RoadmapMilestone,
    RoadmapPhase,
    RoadmapTask,
    RoadmapTaskDependency,
)

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
        if base_shift.get(mid, 0) > 0:
            reason = f"{(today - m.due_on).days} days overdue and not yet done."
        else:
            up_id = max_upstream.get(mid)
            up_title = milestones[up_id].title if up_id is not None else "an upstream milestone"
            reason = f"Shifts {s} days with its dependency '{up_title}'."
        changes.append(Change(mid, mid, m.title, m.due_on, m.due_on + timedelta(days=s), reason))
    return changes
