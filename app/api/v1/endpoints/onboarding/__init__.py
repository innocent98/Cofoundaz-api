from fastapi import APIRouter

from app.api.v1.endpoints.onboarding import invites, logo, state

router = APIRouter()
router.include_router(state.router)
router.include_router(logo.router)
router.include_router(invites.router)
