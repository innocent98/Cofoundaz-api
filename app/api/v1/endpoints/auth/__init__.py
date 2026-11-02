from fastapi import APIRouter

from app.api.v1.endpoints.auth import (
    google,
    login,
    me,
    mfa,
    password,
    registration,
    seams,
    sessions,
)

router = APIRouter()
router.include_router(registration.router)
router.include_router(login.router)
router.include_router(google.router)
router.include_router(mfa.router)
router.include_router(sessions.router)
router.include_router(password.router)
router.include_router(me.router)
router.include_router(seams.router)
