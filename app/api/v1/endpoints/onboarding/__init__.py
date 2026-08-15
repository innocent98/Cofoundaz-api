from fastapi import APIRouter

from app.api.v1.endpoints.onboarding import state

router = APIRouter()
router.include_router(state.router)
