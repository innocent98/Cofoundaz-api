"""Shared setup for the Billing tests: seeded plans, a founder workspace, signed webhooks."""

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import create_access_token
from app.db.models.billing import BillingPlan
from app.db.models.enums import MembershipRole
from app.services.billing.catalog import PLANS
from app.services.finance.payments import stub_transport
from tests.factories import create_membership, create_startup, create_user

KEY = "sk_test_unit_billing_key"


@pytest.fixture(autouse=True)
def _paystack_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every Billing test talks to the in-process Paystack stub with a throwaway test key."""
    monkeypatch.setattr(settings, "PAYSTACK_SECRET_KEY", KEY)
    monkeypatch.setattr(settings, "PAYSTACK_TEST_SECRET_KEY", None)
    monkeypatch.setattr(settings, "PAYMENT_PROVIDER_STUB", True)
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "PLAN_ENFORCEMENT", False)
    stub_transport.PS_TX.clear()
    stub_transport.PS_ABANDONED.clear()
    stub_transport.PS_SUBS.clear()
    stub_transport.PS_CUSTOMER_IDS.clear()


def seed_plans(db: Session) -> None:
    if db.query(BillingPlan).count():
        return
    for p in PLANS:
        db.add(BillingPlan(**p))
    db.flush()


def workspace(db: Session, role: MembershipRole = MembershipRole.founder, owner=None):
    """(user, startup, headers) for a member of a workspace; seeds the plan catalogue."""
    seed_plans(db)
    owner = owner or create_user(db, email_verified_at=datetime.now(UTC))
    startup = create_startup(db, owner=owner)
    if role == MembershipRole.founder:
        user = owner
    else:
        user = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, owner, startup, role=MembershipRole.founder)
    if user is not owner:
        create_membership(db, user, startup, role=role)
    db.flush()
    headers = {
        "Authorization": f"Bearer {create_access_token(str(user.id))}",
        "X-Workspace-Id": str(startup.id),
    }
    return user, startup, headers


def sign(raw: bytes, key: str = KEY) -> str:
    return hmac.new(key.encode(), raw, hashlib.sha512).hexdigest()


def post_event(client, event: str, data: dict[str, Any], *, key: str = KEY, sig: str | None = None):
    raw = json.dumps({"event": event, "data": data}, sort_keys=True).encode()
    return client.post(
        "/api/v1/webhooks/billing/paystack",
        content=raw,
        headers={
            "x-paystack-signature": sig if sig is not None else sign(raw, key),
            "content-type": "application/json",
        },
    )
