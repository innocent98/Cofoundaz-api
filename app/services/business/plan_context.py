from typing import Any

from sqlalchemy.orm import Session

from app.db.models.assessment import Assessment, AssessmentResult
from app.db.models.business import BusinessCanvas, BusinessRecord
from app.db.models.enums import AssessmentStatus
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase
from app.db.models.startup import Startup


def build_plan_context(db: Session, startup: Startup) -> dict[str, Any]:
    """PII-free snapshot of the startup's Business Builder data for plan generation."""
    canvases = {
        c.type.value: c.blocks
        for c in db.query(BusinessCanvas).filter(BusinessCanvas.startup_id == startup.id).all()
    }
    records: dict[str, list[dict[str, Any]]] = {}
    for r in (
        db.query(BusinessRecord)
        .filter(BusinessRecord.startup_id == startup.id)
        .order_by(BusinessRecord.kind, BusinessRecord.position)
        .all()
    ):
        records.setdefault(r.kind.value, []).append(r.data)

    result = (
        db.query(AssessmentResult)
        .join(Assessment, Assessment.id == AssessmentResult.assessment_id)
        .filter(Assessment.startup_id == startup.id, Assessment.status == AssessmentStatus.completed)
        .order_by(Assessment.completed_at.desc())
        .first()
    )
    assessment = (
        {"overall": result.overall_provisional, "dimension_scores": result.dimension_scores}
        if result
        else None
    )

    roadmap_rows = (
        db.query(RoadmapPhase.name, RoadmapMilestone.title, RoadmapMilestone.status)
        .join(Roadmap, Roadmap.id == RoadmapPhase.roadmap_id)
        .join(RoadmapMilestone, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .filter(Roadmap.startup_id == startup.id)
        .order_by(RoadmapPhase.order, RoadmapMilestone.order)
        .all()
    )
    roadmap = [{"phase": p, "milestone": t, "status": (s.value if s else None)} for p, t, s in roadmap_rows]

    return {
        "profile": {
            "name": startup.name,
            "industry": startup.industry,
            "stage": (startup.stage.value if startup.stage else None),
            "business_model": (startup.business_model.value if startup.business_model else None),
        },
        "canvases": canvases,
        "records": records,
        "assessment": assessment,
        "roadmap": roadmap,
    }
