from fastapi import APIRouter

from app.api.v1.endpoints import health, jobs
from app.api.v1.endpoints.auth import router as auth_router

api_router = APIRouter()

api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
