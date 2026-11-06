"""Shared pieces for the Marketplace booking tests: a scriptable fake Paystack (so no test ever
leaves the process), a signed-webhook helper and a ready-made world (an approved expert with a
consultation and a package, open hours, and a founder workspace)."""

import hashlib
import hmac
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.core.config import settings
from app.db.models.enums import MembershipRole
from app.platform.email import ConsoleEmailSender
from app.services.billing import paystack
from app.services.marketplace import emails as mp_emails
from tests.api.test_marketplace_listings import fh, founder, mk_listing, mk_provider
from tests.api.test_staff_users import hdr, member, mk_user

M = "/api/v1/marketplace"
P = "/api/v1/provider"
WEBHOOK = "/api/v1/webhooks/billing/paystack"
SECRET = "sk_test_marketplace_unit_secret"
BRIEF = "We need a review of our seed investment agreement before we sign next week."


class FakePaystack:
    """Stands in for `app.services.billing.paystack`. Scriptable: `pay(ref)` marks a reference
    paid; `down` makes every call raise as if Paystack were unreachable; `reject_refunds` makes
    refunds fail; `amount_override` lets a test make Paystack report a different amount."""

    def __init__(self) -> None:
        self.tx: dict[str, dict[str, Any]] = {}
        self.paid: set[str] = set()
        self.refunds: list[dict[str, Any]] = []
        self.charges: list[dict[str, Any]] = []
        self.down = False
        self.reject_refunds = False
        self.decline_charges = False
        self.amount_override: int | None = None
        self.verify_calls = 0
        self.refund_ids: list[int] = []
        self.timeout_after_accept = False  # Paystack takes the refund, our answer is lost
        self.ignore_refund_filter = False  # a list endpoint that returns every refund
        self.verify_rejects = False
        self.charge_status = "success"  # what an accepted charge answers: success / pending / ...
        self.charge_unavailable = False  # a saved-card charge times out

    def _guard(self) -> None:
        if self.down:
            raise paystack.PaystackUnavailable()

    def initialize(
        self,
        *,
        email: str,
        amount_minor: int,
        reference: str,
        callback_url: str,
        plan_code=None,
        metadata=None,
    ):
        self._guard()
        self.tx[reference] = {"amount": amount_minor, "email": email, "metadata": metadata}
        return {
            "authorization_url": f"https://checkout.paystack.test/{reference}",
            "reference": reference,
        }

    def verify(self, reference: str) -> dict[str, Any]:
        self._guard()
        self.verify_calls += 1
        tx = self.tx.get(reference)
        if self.verify_rejects:
            raise paystack.PaystackRejected()  # a 4xx that isn't "not found"
        if tx is None:
            raise paystack.PaystackNotFound()
        if reference not in self.paid:
            return {
                "status": "abandoned",
                "reference": reference,
                "amount": tx["amount"],
                "currency": "NGN",
            }
        return {
            "status": "success",
            "reference": reference,
            "amount": self.amount_override if self.amount_override is not None else tx["amount"],
            "currency": "NGN",
            "paid_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        }

    def charge_authorization(
        self,
        *,
        authorization_code: str,
        email: str,
        amount_minor: int,
        reference: str,
        metadata=None,
    ):
        self._guard()
        if self.charge_unavailable:
            raise paystack.PaystackUnavailable()
        self.charges.append(
            {"reference": reference, "amount": amount_minor, "auth": authorization_code}
        )
        if self.decline_charges:
            return {"status": "failed", "reference": reference, "amount": amount_minor}
        self.tx[reference] = {"amount": amount_minor, "email": email, "metadata": metadata}
        if self.charge_status == "success":
            self.paid.add(reference)
        return {"status": self.charge_status, "reference": reference, "amount": amount_minor}

    def refund(self, transaction_reference: str, amount_minor: int | None = None):
        self._guard()
        if self.reject_refunds:
            raise paystack.PaystackRejected()
        self.refunds.append({"transaction": transaction_reference, "amount": amount_minor})
        self.refund_ids.append(9000 + len(self.refunds))
        if self.timeout_after_accept:
            raise paystack.PaystackUnavailable()
        return {"id": self.refund_ids[-1], "status": "pending"}

    def list_refunds(self, transaction_reference: str):
        self._guard()
        return [
            {
                "id": rid,
                "amount": r["amount"],
                "status": "processed",
                "transaction_reference": r["transaction"],
            }
            for r, rid in zip(self.refunds, self.refund_ids, strict=True)
            if self.ignore_refund_filter or r["transaction"] == transaction_reference
        ]

    def pay(self, reference: str) -> None:
        self.paid.add(reference)


def install_fake_paystack(monkeypatch) -> FakePaystack:
    fake = FakePaystack()
    monkeypatch.setattr(settings, "PAYSTACK_SECRET_KEY", SECRET)
    monkeypatch.setattr(settings, "PAYSTACK_TEST_SECRET_KEY", None)
    monkeypatch.setattr(settings, "MARKETPLACE_PAYMENTS_ENABLED", True)
    monkeypatch.setattr(settings, "APP_BASE_URL", "https://app.example.test")
    for name in ("initialize", "verify", "charge_authorization", "refund", "list_refunds"):
        monkeypatch.setattr(paystack, name, getattr(fake, name))
    return fake


def install_mailbox(monkeypatch) -> ConsoleEmailSender:
    sender = ConsoleEmailSender()
    monkeypatch.setattr(mp_emails, "get_email_sender", lambda: sender)
    return sender


def signed(body: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(body, separators=(",", ":")).encode()
    sig = hmac.new(SECRET.encode(), raw, hashlib.sha512).hexdigest()
    return raw, {"x-paystack-signature": sig, "content-type": "application/json"}


def post_webhook(client, body: dict[str, Any], *, signature: str | None = None):
    raw, headers = signed(body)
    if signature is not None:
        headers["x-paystack-signature"] = signature
    return client.post(WEBHOOK, content=raw, headers=headers)


def charge_success(reference: str, amount: int | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "event": "charge.success",
        "data": {"reference": reference, "status": "success", "amount": amount, **extra},
    }


class World:
    """An approved Legal-ish expert (a DB-built provider), a consultation and a package, open
    hours, a founder + a team member in one workspace, and a second founder elsewhere."""

    def __init__(self, client, db) -> None:
        self.client, self.db = client, db
        self.expert, self.provider = mk_provider(db, name="Ngozi Eze", category="Design")
        self.consult = mk_listing(db, self.provider, title="Design critique", price_minor=1_000_000)
        self.package = mk_listing(
            db, self.provider, type_="package", title="Logo sprint", price_minor=5_000_000
        )
        self.founder, self.ws = founder(db)
        self.mate = mk_user(db)
        member(db, self.ws, self.mate, MembershipRole.team_member)
        self.other, self.other_ws = founder(db)
        self._open_week()

    def _open_week(self) -> None:
        body = {
            "min_notice_h": 0,
            "buffer_min": 0,
            "rules": [
                {"weekday": d, "start": "08:00", "end": "20:00", "slot_min": 60} for d in range(7)
            ],
            "exceptions": [],
        }
        r = self.client.put(f"{P}/availability", json=body, headers=hdr(self.expert))
        assert r.status_code == 200, r.text

    def h(self, who=None, ws=None) -> dict[str, str]:
        return fh(who or self.founder, ws or self.ws)

    def slots(self, offset_days: int = 2) -> list[str]:
        day = (date.today() + timedelta(days=offset_days)).isoformat()
        r = self.client.get(
            f"{M}/listings/{self.consult.id}/slots",
            params={"from": day, "to": day},
            headers=self.h(),
        )
        assert r.status_code == 200, r.text
        return [s["starts_at"] for s in r.json()["data"]["slots"]]

    def book(
        self,
        *,
        slot: str | None = None,
        listing=None,
        key: str | None = None,
        who=None,
        ws=None,
        **over,
    ):
        listing = listing or self.consult
        body = {"listing_id": str(listing.id), "brief": BRIEF, "accept_terms": True, **over}
        if listing.type.value == "consultation" and "starts_at" not in over:
            body["starts_at"] = slot or self.slots()[0]
        return self.client.post(
            f"{M}/bookings",
            json=body,
            headers={**self.h(who, ws), "Idempotency-Key": key or uuid.uuid4().hex},
        )
