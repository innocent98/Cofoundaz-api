"""Hosted smoke-test pages (Module 09.2, D-VAL-1): served at /p/{slug} on the app domain.

The public surface is addressed by the slug alone and only exists while the test is live: a
draft, ended, deleted or unknown slug all raise the same ``NotFound``. Nothing returned to a
visitor mentions the workspace, its members or other visitors. A visitor is counted through a
SHA-256 of the anonymous id their browser generates; the only personal data kept is the email a
visitor types to join the waitlist.
"""

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.enums import ExperimentStatus, ExperimentType
from app.db.models.validation import Experiment, SmokeTestEvent
from app.platform.events import event_bus
from app.services.auth.sessions import hash_token

DEFAULT_POST_SIGNUP_MESSAGE = "You're on the list, we'll be in touch soon."
POWERED_BY = "Powered by Cofoundaz"
METRIC_TYPES = ("email_signup", "cta_click", "preorder_click")
EVENT_KINDS = ("visit", "cta_click", "preorder_click")
# D-VAL-4: no verdict until this many distinct visitors (PRD: "when sample >= threshold").
VERDICT_MIN_VISITS = 100
# D-VAL-5: "{test} just passed {n} signups" fires when the waitlist reaches one of these.
SIGNUP_MILESTONES = (10, 25, 50, 100, 250, 500, 1000)

_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,58}[a-z0-9])$")
_RESERVED = frozenset(
    {"admin", "api", "app", "www", "p", "s", "help", "support", "login", "signup", "cofoundaz"}
)


def _slug_error(message: str) -> AppError:
    return AppError(
        "VALIDATION_ERROR",
        message,
        422,
        field_errors=[{"field": "config.slug", "message": message}],
    )


def normalize_slug(raw: Any) -> str | None:
    """`config["slug"]` → the stored slug, or None when blank. 422 when it can't be a URL."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if not isinstance(raw, str):
        raise _slug_error("Use lowercase letters, numbers and dashes.")
    slug = raw.strip()
    if not _SLUG.match(slug):
        raise _slug_error(
            "Use 3 to 60 lowercase letters, numbers and dashes, not starting or ending with a dash."
        )
    if slug in _RESERVED:
        raise _slug_error("That address is reserved. Try another.")
    return slug


def apply_slug(db: Session, experiment: Experiment) -> None:
    """Copy `config.slug` onto the unique column; 409 when another test already uses it."""
    if experiment.type != ExperimentType.smoke_test:
        return
    slug = normalize_slug((experiment.config or {}).get("slug"))
    if slug is not None:
        taken = (
            db.query(Experiment.id)
            .filter(Experiment.slug == slug, Experiment.id != experiment.id)
            .first()
        )
        if taken is not None:
            raise AppError("SLUG_TAKEN", "That page address is taken. Try another.", 409)
    experiment.slug = slug
    if experiment.status == ExperimentStatus.live:
        if slug is None:
            raise _slug_error("Add a page address before publishing.")
        if experiment.published_at is None:
            experiment.published_at = datetime.now(UTC)


# --- public surface ---------------------------------------------------------------------------


def open_page(db: Session, slug: str) -> Experiment:
    row = db.query(Experiment).filter_by(slug=slug).first()
    if row is None or row.type != ExperimentType.smoke_test or row.status != ExperimentStatus.live:
        raise NotFound()  # uniform for unknown / draft / ended
    return row


def _metric_type(config: dict[str, Any]) -> str:
    value = config.get("metric_type")
    return value if value in METRIC_TYPES else "email_signup"


def _post_signup_message(config: dict[str, Any]) -> str:
    message = config.get("post_signup_message")
    return (
        message.strip()
        if isinstance(message, str) and message.strip()
        else DEFAULT_POST_SIGNUP_MESSAGE
    )


def page_view(experiment: Experiment) -> dict[str, Any]:
    """Exactly what a visitor may see. No ids, status, counts or workspace details."""
    config = experiment.config or {}
    return {
        "headline": str(config.get("headline") or experiment.name),
        "subheadline": str(config.get("subheadline") or ""),
        "cta_text": str(config.get("cta_text") or "Join the waitlist"),
        "require_email": config.get("require_email", True) is not False,
        "show_pricing": config.get("show_pricing") is True,
        "metric_type": _metric_type(config),
        "post_signup_message": _post_signup_message(config),
        "powered_by": POWERED_BY,
    }


def _visitor_hash(experiment: Experiment, visitor_id: str | None) -> str | None:
    if not visitor_id:
        return None
    return hash_token(f"{experiment.id}:{visitor_id}")


def record_event(db: Session, slug: str, kind: str, visitor_id: str | None) -> None:
    experiment = open_page(db, slug)
    db.add(
        SmokeTestEvent(
            experiment_id=experiment.id,
            startup_id=experiment.startup_id,
            kind=kind,
            visitor_hash=_visitor_hash(experiment, visitor_id),
        )
    )
    db.flush()


def _signup_count(db: Session, experiment_id: uuid.UUID) -> int:
    return (
        db.query(func.count(SmokeTestEvent.id))
        .filter(SmokeTestEvent.experiment_id == experiment_id, SmokeTestEvent.kind == "signup")
        .scalar()
        or 0
    )


def record_signup(db: Session, slug: str, email: str, visitor_id: str | None) -> dict[str, str]:
    """Join the waitlist. A repeat email is accepted silently (same answer, no duplicate), so
    the page never reveals who already signed up."""
    experiment = open_page(db, slug)
    email = email.strip().lower()
    exists = (
        db.query(SmokeTestEvent.id)
        .filter_by(experiment_id=experiment.id, kind="signup", email=email)
        .first()
    )
    if exists is None:
        try:
            with db.begin_nested():
                db.add(
                    SmokeTestEvent(
                        experiment_id=experiment.id,
                        startup_id=experiment.startup_id,
                        kind="signup",
                        visitor_hash=_visitor_hash(experiment, visitor_id),
                        email=email,
                    )
                )
                db.flush()
        except IntegrityError:
            pass  # a concurrent identical signup won the race: same outcome
        else:
            count = _signup_count(db, experiment.id)
            if count in SIGNUP_MILESTONES:
                event_bus.publish(
                    db,
                    "validation.smoke_test.milestone",
                    {
                        "startup_id": str(experiment.startup_id),
                        "experiment_id": str(experiment.id),
                        "name": experiment.name,
                        "signups": count,
                    },
                )
    return {"message": _post_signup_message(experiment.config or {})}


# --- member reads -----------------------------------------------------------------------------


def _counter(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _distinct(db: Session, experiment_id: uuid.UUID, kind: str) -> int:
    """People, not page loads: distinct visitor hashes plus events without one."""
    base = db.query(SmokeTestEvent).filter(
        SmokeTestEvent.experiment_id == experiment_id, SmokeTestEvent.kind == kind
    )
    hashed = (
        base.filter(SmokeTestEvent.visitor_hash.is_not(None))
        .with_entities(func.count(func.distinct(SmokeTestEvent.visitor_hash)))
        .scalar()
        or 0
    )
    anonymous = base.filter(SmokeTestEvent.visitor_hash.is_(None)).count()
    return int(hashed) + anonymous


def stats(db: Session, experiment: Experiment) -> dict[str, Any]:
    """Funnel from real page events plus any numbers typed in by hand (`metrics`, kept for tests
    run outside Cofoundaz). The verdict is computed here, once, for every client."""
    metrics = experiment.metrics or {}
    config = experiment.config or {}
    visits = _counter(metrics.get("visits")) + _distinct(db, experiment.id, "visit")
    signups = _counter(metrics.get("signups")) + _signup_count(db, experiment.id)
    cta_clicks = _distinct(db, experiment.id, "cta_click")
    preorder_clicks = _distinct(db, experiment.id, "preorder_click")
    metric_type = _metric_type(config)
    converted = {
        "email_signup": signups,
        "cta_click": cta_clicks,
        "preorder_click": preorder_clicks,
    }[metric_type]
    conversion = min(100.0, round(100 * converted / visits, 1)) if visits else 0.0
    target = config.get("target_conversion")
    target_value = (
        target
        if isinstance(target, int | float) and not isinstance(target, bool) and target > 0
        else None
    )
    verdict = None
    if target_value is not None and visits >= VERDICT_MIN_VISITS:
        verdict = "validated" if conversion >= target_value else "weak"
    return {
        "experiment_id": str(experiment.id),
        "name": experiment.name,
        "status": experiment.status.value,
        "slug": experiment.slug,
        "visits": visits,
        "signups": signups,
        "cta_clicks": cta_clicks,
        "preorder_clicks": preorder_clicks,
        "metric_type": metric_type,
        "conversion": conversion,
        "target_conversion": target_value,
        "sample_needed": VERDICT_MIN_VISITS,
        "verdict": verdict,
    }


def waitlist(db: Session, experiment: Experiment) -> list[dict[str, Any]]:
    rows = (
        db.query(SmokeTestEvent)
        .filter_by(experiment_id=experiment.id, kind="signup")
        .order_by(SmokeTestEvent.created_at.asc(), SmokeTestEvent.id.asc())
        .all()
    )
    return [{"email": r.email, "signed_up_at": r.created_at.isoformat()} for r in rows]
