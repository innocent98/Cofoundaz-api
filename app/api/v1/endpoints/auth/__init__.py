from fastapi import APIRouter

from app.api.v1.endpoints.auth import login, registration

router = APIRouter()
router.include_router(registration.router)
router.include_router(login.router)
