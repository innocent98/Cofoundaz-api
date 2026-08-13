from fastapi import APIRouter

from app.api.v1.endpoints.auth import registration

router = APIRouter()
router.include_router(registration.router)
