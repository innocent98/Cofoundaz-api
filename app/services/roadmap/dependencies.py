from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy.orm import Session

from app.db.models.roadmap import RoadmapMilestone, RoadmapPhase, RoadmapTask, RoadmapTaskDependency


def _roadmap_edges(db: Session, roadmap_id: uuid.UUID) -> list[RoadmapTaskDependency]:
    # Edges whose dependent task belongs to this roadmap (both ends are in the
    # same roadmap by construction, since dependency creation is roadmap-scoped).
    return (
        db.query(RoadmapTaskDependency)
        .join(RoadmapTask, RoadmapTaskDependency.task_id == RoadmapTask.id)
        .join(RoadmapMilestone, RoadmapTask.milestone_id == RoadmapMilestone.id)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .filter(RoadmapPhase.roadmap_id == roadmap_id)
        .all()
    )


def dependency_map(db: Session, roadmap_id: uuid.UUID) -> dict[uuid.UUID, list[uuid.UUID]]:
    out: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for e in _roadmap_edges(db, roadmap_id):
        out[e.task_id].append(e.depends_on_task_id)
    return dict(out)


def would_create_cycle(
    db: Session, roadmap_id: uuid.UUID, task_id: uuid.UUID, depends_on_task_id: uuid.UUID
) -> bool:
    # Edge task_id -> depends_on_task_id ("task_id depends on depends_on_task_id").
    # A loop forms iff depends_on_task_id can already reach task_id following
    # existing dependent->dependency edges (DFS from depends_on_task_id).
    adj = dependency_map(db, roadmap_id)
    stack = [depends_on_task_id]
    seen: set[uuid.UUID] = set()
    while stack:
        node = stack.pop()
        if node == task_id:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(adj.get(node, []))
    return False


def add_dependency(
    db: Session, task_id: uuid.UUID, depends_on_task_id: uuid.UUID
) -> tuple[RoadmapTaskDependency, bool]:
    existing = (
        db.query(RoadmapTaskDependency)
        .filter_by(task_id=task_id, depends_on_task_id=depends_on_task_id)
        .first()
    )
    if existing is not None:
        return existing, False
    row = RoadmapTaskDependency(task_id=task_id, depends_on_task_id=depends_on_task_id)
    db.add(row)
    db.flush()
    return row, True
