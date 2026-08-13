from fastapi import APIRouter

from app.core.errors import FeatureNotEnabled

router = APIRouter()


@router.post("/oauth/{provider}")
def oauth(provider: str) -> None:
    raise FeatureNotEnabled()


@router.post("/mfa/sms/setup")
def sms_setup() -> None:
    raise FeatureNotEnabled()


@router.post("/mfa/sms/verify")
def sms_verify() -> None:
    raise FeatureNotEnabled()
