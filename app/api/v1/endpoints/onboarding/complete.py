from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.logger import log
from app.db.models.user import User
from app.db.session import get_db
from app.platform.email import get_email_sender
from app.services.auth.emails import welcome_email
from app.services.onboarding.complete import complete_onboarding
from app.services.onboarding.workspace import resolve_or_create_workspace

router = APIRouter()


@router.post("/complete")
def post_complete(
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = resolve_or_create_workspace(db, user)
    first_time = startup.profile.onboarding_completed_at is None
    result = complete_onboarding(db, startup, user)
    db.commit()  # commit BEFORE sending: never email for a completion that rolled back
    if first_time:
        # Once, on the call that completes onboarding (a repeat call returns early above).
        # Best effort: a mail failure never undoes or fails the completion.
        try:
            get_email_sender().send(
                welcome_email(
                    user.email,
                    startup_name=startup.name or "your startup",
                    full_name=user.profile.full_name if user.profile else None,
                )
            )
        except Exception as exc:  # noqa: BLE001 - any sender failure is swallowed here
            log.warning(f"[onboarding] welcome email failed: {exc}")
    return success_response(result)
