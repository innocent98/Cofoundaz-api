"""Live end-to-end harness for the auth API.

These tests run against a REAL uvicorn server over HTTP (not the in-process
TestClient), against an isolated `cofoundaz_e2e` Postgres database and live
Redis. They are NOT part of the unit suite (`testpaths = ["tests"]`); run them
via `scripts/e2e_run.sh`, which boots the server with `EMAIL_BACKEND=file` so
this harness can read one-time tokens back out of captured emails.

Config via env:
  E2E_BASE_URL   default http://127.0.0.1:8010
  E2E_MAIL_DIR   default ./var/mail-e2e   (must match the server's EMAIL_FILE_DIR)
"""

import json
import os
import re
import uuid
from pathlib import Path

import httpx
import pytest

BASE_URL = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8010")
MAIL_DIR = os.environ.get("E2E_MAIL_DIR", "./var/mail-e2e")
_TOKEN_RE = re.compile(r"<code>([^<]+)</code>")


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL


@pytest.fixture()
def http() -> httpx.Client:
    # Fresh cookie jar per test so cookie-transport assertions are isolated.
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as client:
        yield client


@pytest.fixture()
def unique_email():
    def _make(prefix: str = "e2e") -> str:
        return f"{prefix}-{uuid.uuid4().hex[:12]}@example.com"

    return _make


@pytest.fixture()
def mailbox():
    """Reads captured emails from the file email backend."""

    def _latest_token_for(email: str, *, subject_contains: str | None = None) -> str:
        files = sorted(Path(MAIL_DIR).glob("*.json"))
        for f in reversed(files):  # newest first (timestamp-prefixed filenames)
            data = json.loads(f.read_text())
            if data["to"].lower() != email.lower():
                continue
            if subject_contains and subject_contains.lower() not in data["subject"].lower():
                continue
            m = _TOKEN_RE.search(data["html"])
            if m:
                return m.group(1)
        raise AssertionError(f"no token email found for {email} (subject~={subject_contains})")

    class _Mailbox:
        latest_token_for = staticmethod(_latest_token_for)

        @staticmethod
        def count_for(email: str) -> int:
            n = 0
            for f in Path(MAIL_DIR).glob("*.json"):
                if json.loads(f.read_text())["to"].lower() == email.lower():
                    n += 1
            return n

    return _Mailbox()


@pytest.fixture()
def make_verified_user(mailbox, unique_email):
    """Signs up + verifies a fresh user over HTTP; returns {email, password}."""

    def _make(http: httpx.Client, *, password: str | None = None) -> dict[str, str]:
        # Generated, not hardcoded: satisfies the policy (>=8 chars + a digit) and
        # keeps each e2e user's secret unique.
        password = password or f"Ev-{uuid.uuid4().hex[:12]}-9"
        email = unique_email()
        r = http.post("/api/v1/auth/signup", json={"email": email, "password": password})
        assert r.status_code == 201, r.text
        token = mailbox.latest_token_for(email, subject_contains="Verify")
        r = http.post("/api/v1/auth/verify", json={"token": token})
        assert r.status_code == 200, r.text
        return {"email": email, "password": password}

    return _make
