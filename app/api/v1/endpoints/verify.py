from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.envelope import success_response
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.services.learning.certificates import verify_public

router = APIRouter()


@router.get("/{code}")
# One bucket per caller across every code (a per-URL bucket would let a caller guess codes freely).
@limiter.shared_limit("30/minute", scope="public-verify")
def verify_certificate(
    request: Request,
    code: str,
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Public credential check (no authentication, by design).

    Takes only the short credential ID printed on the certificate. Returns the holder's name, the
    course, the issue date, the issuer and whether it is valid, and nothing else. An unknown or
    malformed code is a plain 404. Rate limited per caller address.
    """
    return success_response(verify_public(db, code))
