from fastapi import APIRouter

from app.api.v1.endpoints import health, invitations, jobs
from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.onboarding import router as onboarding_router

api_router = APIRouter()

api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
api_router.include_router(onboarding_router, prefix="/onboarding", tags=["onboarding"])
api_router.include_router(invitations.router, prefix="/invitations", tags=["invitations"])
