from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models.enums import RoadmapStatus, StartupStage, TaskEffort
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase, RoadmapTask
from app.db.models.startup import Startup
from app.db.models.user import User
from app.platform.events import event_bus
from app.services.roadmap.dependencies import dependency_map
from app.services.roadmap.templates import ROADMAP_TEMPLATE_VERSION, STAGE_TEMPLATES


def _weeks(base: date, n: int) -> date:
    return base + timedelta(weeks=n)


def _stage_key(startup: Startup) -> str:
    if startup.stage is not None and startup.stage.value in STAGE_TEMPLATES:
        return startup.stage.value
    return "idea"  # defensive fallback; onboarding guarantees a stage


def generate_roadmap(db: Session, startup: Startup, *, actor: User | None = None) -> Roadmap:
    """Idempotent create-once roadmap generation from the startup's stage template.

    Race-safe: the claim is a single INSERT ... ON CONFLICT (startup_id) DO NOTHING
    RETURNING id. Under concurrent callers for the same startup, Postgres serializes
    on the unique index -- the loser's insert blocks until the winner commits, then
    observes the conflict and returns no row, so it falls through to loading the
    winner's already-built roadmap instead of racing to build its own tree.
    """
    stage_key = _stage_key(startup)
    tmpl = STAGE_TEMPLATES[stage_key]
    # roadmaps.stage is NOT NULL; startup.stage can be None (defensive fallback
    # above), so stamp the resolved template stage rather than the raw startup one.
    stage = StartupStage(stage_key)

    claim_stmt = (
        pg_insert(Roadmap)
        .values(
            id=uuid.uuid4(),
            startup_id=startup.id,
            stage=stage,
            template_key=tmpl["key"],
            template_version=ROADMAP_TEMPLATE_VERSION,
            generated_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(index_elements=["startup_id"])
        .returning(Roadmap.id)
    )
    new_id = db.execute(claim_stmt).scalar_one_or_none()
    if new_id is None:
        # Someone else generated it; their row (and tree) is committed by the time
        # our blocked insert unblocks and reports the conflict.
        return db.query(Roadmap).filter_by(startup_id=startup.id).one()

    roadmap = db.query(Roadmap).filter_by(id=new_id).one()
    base = date.today()
    milestone_count = 0

    for p_idx, ph in enumerate(tmpl["phases"]):
        phase = RoadmapPhase(
            roadmap_id=roadmap.id,
            name=ph["name"],
            order=p_idx,
            starts_on=_weeks(base, ph["start_week"]),
            ends_on=_weeks(base, ph["end_week"]),
        )
        db.add(phase)
        db.flush()
        for m_idx, ms in enumerate(ph["milestones"]):
            milestone = RoadmapMilestone(
                phase_id=phase.id,
                title=ms["title"],
                due_on=_weeks(base, ms["due_week"]),
                status=RoadmapStatus.todo,
                progress=0,
                order=m_idx,
            )
            db.add(milestone)
            db.flush()
            milestone_count += 1
            for t_idx, tk in enumerate(ms["tasks"]):
                db.add(
                    RoadmapTask(
                        milestone_id=milestone.id,
                        title=tk["title"],
                        effort=TaskEffort(tk["effort"]),
                        status=RoadmapStatus.todo,
                        order=t_idx,
                        due_on=None,
                    )
                )
    db.flush()

    event_bus.publish(
        "roadmap.generated",
        {
            "startup_id": str(startup.id),
            "roadmap_id": str(roadmap.id),
            "stage": roadmap.stage.value,
            "template_key": roadmap.template_key,
            "milestone_count": milestone_count,
        },
    )
    return roadmap


def recompute_milestone_progress(db: Session, milestone: RoadmapMilestone) -> None:
    tasks = db.query(RoadmapTask).filter_by(milestone_id=milestone.id).all()
    if not tasks:
        milestone.progress = 100 if milestone.status == RoadmapStatus.done else 0
        return
    done = sum(1 for t in tasks if t.status == RoadmapStatus.done)
    milestone.progress = round(100 * done / len(tasks))


def milestone_overdue(m: RoadmapMilestone) -> bool:
    return m.due_on is not None and m.due_on < date.today() and m.status != RoadmapStatus.done


def task_overdue(t: RoadmapTask) -> bool:
    return t.due_on is not None and t.due_on < date.today() and t.status != RoadmapStatus.done


def person_ref(db: Session, user_id: uuid.UUID | None) -> dict | None:
    if user_id is None:
        return None
    u = db.query(User).filter_by(id=user_id).first()
    if u is None:
        return None
    name = getattr(getattr(u, "profile", None), "full_name", None)
    return {"id": str(u.id), "name": name}


def serialize_tree(db: Session, roadmap: Roadmap, startup: Startup) -> dict:
    dep_map = dependency_map(db, roadmap.id)  # {dependent_task_id: [dependency_id, ...]}
    phases = (
        db.query(RoadmapPhase).filter_by(roadmap_id=roadmap.id).order_by(RoadmapPhase.order).all()
    )
    out_phases: list[dict] = []
    for ph in phases:
        milestones = (
            db.query(RoadmapMilestone)
            .filter_by(phase_id=ph.id)
            .order_by(RoadmapMilestone.order)
            .all()
        )
        out_milestones: list[dict] = []
        for m in milestones:
            tasks = (
                db.query(RoadmapTask).filter_by(milestone_id=m.id).order_by(RoadmapTask.order).all()
            )
            dep_count = sum(1 for t in tasks if t.id in dep_map)
            out_milestones.append(
                {
                    "id": str(m.id),
                    "title": m.title,
                    "description": m.description,
                    "due_on": m.due_on.isoformat() if m.due_on else None,
                    "owner": person_ref(db, m.owner_id),
                    "status": m.status.value,
                    "progress": m.progress,
                    "overdue": milestone_overdue(m),
                    "order": m.order,
                    "dependency_count": dep_count,
                    "tasks": [
                        {
                            "id": str(t.id),
                            "title": t.title,
                            "description": t.description,
                            "effort": t.effort.value,
                            "status": t.status.value,
                            "assignee": person_ref(db, t.assignee_id),
                            "due_on": t.due_on.isoformat() if t.due_on else None,
                            "overdue": task_overdue(t),
                            "order": t.order,
                            "depends_on": [str(d) for d in dep_map.get(t.id, [])],
                        }
                        for t in tasks
                    ],
                }
            )
        out_phases.append(
            {
                "id": str(ph.id),
                "name": ph.name,
                "order": ph.order,
                "starts_on": ph.starts_on.isoformat() if ph.starts_on else None,
                "ends_on": ph.ends_on.isoformat() if ph.ends_on else None,
                "milestones": out_milestones,
            }
        )
    return {
        "roadmap": {
            "id": str(roadmap.id),
            "stage": roadmap.stage.value,
            "template_key": roadmap.template_key,
            "generated_at": roadmap.generated_at.isoformat(),
        },
        "current_stage": startup.stage.value if startup.stage else None,
        "phases": out_phases,
    }
