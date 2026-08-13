from fastapi import APIRouter

from app.api.v1.endpoints.auth import login, mfa, registration, sessions

router = APIRouter()
router.include_router(registration.router)
router.include_router(login.router)
router.include_router(mfa.router)
router.include_router(sessions.router)
