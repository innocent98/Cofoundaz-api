"""Live end-to-end harness for the auth API.

These tests run against a REAL uvicorn server over HTTP (not the in-process
TestClient), against an isolated `cofoundaz_e2e` Postgres database and live
Redis. They are NOT part of the unit suite (`testpaths = ["tests"]`); run them
via `scripts/e2e_run.sh`, which boots the server with `EMAIL_BACKEND=file` so
this harness can read one-time tokens back out of captured emails.

Config via env:
  E2E_BASE_URL   default http://127.0.0.1:8010
  E2E_MAIL_DIR   default ./var/mail-e2e   (must match the server's EMAIL_FILE_DIR)
  E2E_REMOTE     set to 1 when running against a DEPLOYED environment

REMOTE MODE (E2E_REMOTE=1)
--------------------------
Against a deployed staging box the HTTP side works unchanged - httpx just points
at E2E_BASE_URL. The MAILBOX side does not: `EMAIL_FILE_DIR` is a directory on
the VPS, and this harness reads it from the local filesystem. A test that signs
up a user and needs the verification token out of a captured email therefore
cannot run remotely without shipping the mail directory back to the runner.

Rather than pretend otherwise, remote mode DESELECTS every test whose fixture
closure includes `mailbox` (directly, or transitively via `make_verified_user`)
and prints exactly which ones were dropped and why. The remaining tests still
run for real and still fail the build for real.

This is deliberately automatic rather than a hand-maintained marker list: a new
test that needs a mailbox is excluded the moment it is written, instead of
silently failing in CD months later. See
docs/deployment/DEPLOYMENT_GUIDE.md for which tests gate production.
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
REMOTE = os.environ.get("E2E_REMOTE", "").strip().lower() in {"1", "true", "yes"}

# Fixtures that can only work when the mail directory is on the SAME machine as
# the test run. `make_verified_user` is included because it consumes `mailbox`
# internally - most journey tests reach the mailbox through it without naming it.
_MAILBOX_FIXTURES = frozenset({"mailbox", "make_verified_user"})
# One-time tokens arrive two ways: the verification/password-reset emails now embed
# the token in a clickable FE link (`/verify-email/<tok>`, `/reset-password/<tok>` -
# see app/services/auth/emails.py), while the onboarding-invite email still uses a
# bare `<code><tok></code>`. Match either and take whichever group captured.
_TOKEN_RE = re.compile(r"/(?:verify-email|reset-password)/([A-Za-z0-9_\-]+)|<code>([^<]+)</code>")


def pytest_collection_modifyitems(config, items):
    """In remote mode, deselect tests that need a local mail directory.

    Deselected rather than skipped so they are reported as "deselected" in the
    summary and cannot be mistaken for passes. The list is printed in full: a
    gate whose coverage silently shrinks is worse than no gate.
    """
    if not REMOTE:
        return

    keep, dropped = [], []
    for item in items:
        if _MAILBOX_FIXTURES & set(getattr(item, "fixturenames", ())):
            dropped.append(item)
        else:
            keep.append(item)

    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = keep

    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line("")
        reporter.write_line(
            f"E2E_REMOTE=1: deselected {len(dropped)} mailbox-dependent test(s); "
            f"{len(keep)} will run against {BASE_URL}",
            yellow=True,
        )
        for item in dropped:
            reporter.write_line(f"  deselected (needs local mail dir): {item.nodeid}")
        reporter.write_line("")

    if not keep:
        # Every test was dropped. Refuse to report a green run against a
        # deployed environment on the strength of zero assertions.
        raise pytest.UsageError(
            "E2E_REMOTE=1 deselected every collected test - the remote gate would "
            "have passed without asserting anything. Refusing to continue."
        )


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
    # Recipients live at Resend's `delivered@resend.dev` test sink, plus-addressed for
    # uniqueness. This matters for the REMOTE live-e2e gate, where staging runs
    # EMAIL_BACKEND=resend: Resend REJECTS `example.com` recipients with a 422
    # ("Invalid `to` field ... use our testing email address instead of domains like
    # example.com"), which makes the fail-loud sender raise and every e2e signup 500.
    # `delivered+<unique>@resend.dev` is accepted, always "delivered" to Resend's sink
    # (no real inbox, no bounces), and unique per signup. Transparent to the local file
    # backend + `mailbox` fixture, which match on the full `to` address either way.
    def _make(prefix: str = "e2e") -> str:
        return f"delivered+{prefix}-{uuid.uuid4().hex[:12]}@resend.dev"

    return _make


@pytest.fixture()
def mailbox():
    """Reads captured emails from the file email backend.

    Only usable when EMAIL_FILE_DIR is on this machine. Remote runs deselect
    every test that reaches this fixture, so arriving here in remote mode means
    the deselection logic missed something - fail loudly rather than emit a
    confusing "no token email found" further down.
    """
    if REMOTE:
        raise RuntimeError(
            "The `mailbox` fixture cannot work with E2E_REMOTE=1: EMAIL_FILE_DIR "
            "lives on the deployed host, not on this runner. This test should "
            "have been deselected - see pytest_collection_modifyitems in "
            "e2e/conftest.py."
        )

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
                return m.group(1) or m.group(2)  # URL-token group, else <code> group
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

        @staticmethod
        def latest_for(email: str) -> dict | None:
            """The newest captured email JSON dict for `email`, or None.

            Unlike `latest_token_for` (which extracts a one-time token from the
            HTML body), this returns the whole captured email dict as written by
            the file email backend -- used to assert on/capture the raw delivered
            message itself (subject, to, html) rather than a token inside it.
            """
            files = sorted(Path(MAIL_DIR).glob("*.json"))
            for f in reversed(files):
                data = json.loads(f.read_text())
                if data["to"].lower() == email.lower():
                    return data
            return None

    return _Mailbox()


@pytest.fixture()
def capture():
    """Writes a live response body to e2e/_captures/<group>/<name>.json.

    These captured bodies are the SOURCE of truth for FE integration guides --
    pasted verbatim from here, never retyped from memory.
    """
    base_dir = Path(__file__).parent / "_captures"

    def _capture(group: str, name: str, resp: httpx.Response) -> None:
        out_dir = base_dir / group
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{name}.json").write_text(json.dumps(resp.json(), indent=2) + "\n")

    return _capture


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
