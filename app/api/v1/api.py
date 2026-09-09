from fastapi import APIRouter

from app.api.v1.endpoints import (
    assessments,
    business,
    dashboard,
    documents,
    health,
    health_score,
    invitations,
    jobs,
    journal,
    mission,
    roadmap,
)
from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.onboarding import router as onboarding_router

api_router = APIRouter()

api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
api_router.include_router(onboarding_router, prefix="/onboarding", tags=["onboarding"])
api_router.include_router(invitations.router, prefix="/invitations", tags=["invitations"])
api_router.include_router(assessments.router, prefix="/assessments", tags=["assessments"])
api_router.include_router(health_score.router, prefix="/health-score", tags=["health-score"])
api_router.include_router(roadmap.router, prefix="/roadmap", tags=["roadmap"])
api_router.include_router(mission.router, prefix="/missions", tags=["missions"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
api_router.include_router(business.router, prefix="/business-builder", tags=["business-builder"])
api_router.include_router(journal.router, prefix="/journal", tags=["journal"])
api_router.include_router(documents.router, tags=["documents"])
