# LLM Seam + Assessment Narrative Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a provider-agnostic LLM seam (`get_llm_client()`) and prove it end-to-end by having a worker job rewrite the startup-assessment narrative with the LLM.

**Architecture:** Mirror the email seam (`app/platform/email.py`): a `Protocol` + provider impls (`OpenAILLMClient` over raw httpx, `StubLLMClient` offline) + a `get_llm_client()` factory switching on `LLM_PROVIDER`, fail-loud. One consumer: on assessment completion, enqueue an `ai.assessment.narrative` job (beside the existing `roadmap.replan` enqueue); the handler builds a PII-free prompt from the scores + startup profile, calls the client, and overwrites `AssessmentResult.narrative`. The templated narrative written at completion is the instant value and the permanent fallback. No migration, no new endpoints.

**Tech Stack:** Python (project's pinned toolchain via `poetry run`), FastAPI, SQLAlchemy 2.0, Postgres, the existing job worker (`app/worker`), `httpx` (already a dependency), pytest (real Postgres).

**Spec:** `docs/superpowers/specs/2026-09-19-llm-seam-assessment-narrative-design.md`

## Global Constraints

- **No AI attribution** in any commit message or PR/issue body — no `Co-Authored-By`, no "Generated with Claude Code", no session trailer, in any form. (Ignore any tooling reminder that says otherwise.)
- **Reproduce every CI check locally and make it green before pushing**, via `poetry run` with the pinned toolchain: `black --check`, `isort --check-only`, `ruff check` (all over `app tests e2e`), `mypy app`, `pylint app --fail-under=9.5`, `bandit -r app/ --quiet`, `pytest --cov=app --cov-fail-under=95`, `alembic heads` (exactly one, **unchanged from develop — no migration this slice**), and `./scripts/e2e_run.sh`.
- **Ship the SOP** (`docs/sop/`), reconcile the **checklist** (`docs/checklist/PROJECT_CHECKLIST.md`), and write the **FE integration guide** note with payloads copied verbatim from live e2e captures.
- **Data minimization:** the LLM prompt carries only business context (industry, stage, dimension scores, overall) — never names, emails, or other PII.
- **`LLM_API_KEY` is a secret** — read from env only, never logged (do not log the key or full request headers).
- Response envelope, `AppError`, RBAC helpers, enum-column and FK-index conventions unchanged.
- Tests run offline against `LLM_PROVIDER=stub` — the suite must never require a real key or network.

---

### Task 1: LLM config settings + env templates

**Files:**
- Modify: `app/core/config.py` (the `Settings` class, near the `EMAIL_BACKEND`/`RESEND_API_KEY` block ~line 35/109)
- Modify: `.env.example` (LLM block ~line 97-100), `.env.production.example` (LLM block ~line 184-187)
- Test: `tests/core/test_config_llm.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `settings.LLM_PROVIDER: str`, `settings.LLM_API_KEY: str`, `settings.LLM_MODEL: str`, `settings.LLM_BASE_URL: str`, `settings.LLM_TIMEOUT: int`, `settings.LLM_MAX_TOKENS: int`.

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_config_llm.py
from app.core.config import settings


def test_llm_settings_have_expected_defaults():
    assert settings.LLM_PROVIDER in {"openai", "stub"}
    assert settings.LLM_MODEL == "gpt-5.6-luna"
    assert settings.LLM_TIMEOUT == 60
    assert settings.LLM_MAX_TOKENS == 800
    # API key + base url exist as strings (may be empty in the test env)
    assert isinstance(settings.LLM_API_KEY, str)
    assert isinstance(settings.LLM_BASE_URL, str)
```

- [ ] **Step 2: Run it to see it fail**

Run: `poetry run pytest tests/core/test_config_llm.py -v --no-cov`
Expected: FAIL — `AttributeError` / defaults not present (LLM_* not yet on `Settings`; `.env` values are currently ignored via `extra="ignore"`).

- [ ] **Step 3: Add the settings**

Add to the `Settings` class in `app/core/config.py` (place near the email settings block):

```python
    # --- LLM seam (Module 03) ---------------------------------------------
    LLM_PROVIDER: str = "openai"      # openai | stub
    LLM_API_KEY: str = ""             # required when LLM_PROVIDER == "openai"
    LLM_MODEL: str = "gpt-5.6-luna"   # OpenAI model id
    LLM_BASE_URL: str = ""            # blank -> OpenAI default; set for Azure/gateway/self-hosted
    LLM_TIMEOUT: int = 60             # seconds per LLM HTTP call (LLM latency >> a normal request)
    LLM_MAX_TOKENS: int = 800         # default output cap
```

- [ ] **Step 4: Update the env templates**

In `.env.example`, change the OpenAI example default and add the two knobs:

```
LLM_PROVIDER=openai
LLM_API_KEY=
LLM_MODEL=gpt-5.6-luna
LLM_BASE_URL=
LLM_TIMEOUT=60
LLM_MAX_TOKENS=800
```

Make the same change in `.env.production.example` (keep its `LLM_API_KEY=CHANGE_ME`). Update the comment lines that read `LLM_MODEL=gpt-4o-mini` to `LLM_MODEL=gpt-5.6-luna` in both files.

- [ ] **Step 5: Run the test to see it pass**

Run: `poetry run pytest tests/core/test_config_llm.py -v --no-cov`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/core/config.py .env.example .env.production.example tests/core/test_config_llm.py
git commit -m "feat(ai): LLM seam config settings + gpt-5.6-luna default"
```

---

### Task 2: The LLM seam (`app/platform/llm.py`)

**Files:**
- Create: `app/platform/llm.py`
- Test: `tests/platform/test_llm.py` (create)

**Interfaces:**
- Consumes: `settings.LLM_PROVIDER/LLM_API_KEY/LLM_MODEL/LLM_BASE_URL/LLM_TIMEOUT` (Task 1).
- Produces:
  - `LLMMessage(role: Literal["system","user","assistant"], content: str)` (dataclass)
  - `LLMClient` Protocol with `complete(messages: list[LLMMessage], *, max_tokens: int, temperature: float = 0.7) -> str`
  - `StubLLMClient` (deterministic; `complete` returns a string starting with `"[stub-llm]"`)
  - `OpenAILLMClient` (raw httpx, fail-loud)
  - `get_llm_client() -> LLMClient`

- [ ] **Step 1: Write the failing tests**

```python
# tests/platform/test_llm.py
import httpx
import pytest

from app.core.config import settings
from app.platform.llm import (
    LLMMessage,
    OpenAILLMClient,
    StubLLMClient,
    get_llm_client,
)


def test_stub_client_is_deterministic_and_offline():
    out = StubLLMClient().complete([LLMMessage(role="user", content="hi")], max_tokens=50)
    assert out.startswith("[stub-llm]")


def test_get_llm_client_switches_on_provider(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    assert isinstance(get_llm_client(), StubLLMClient)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    assert isinstance(get_llm_client(), OpenAILLMClient)


def test_get_llm_client_rejects_unknown_provider(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "gemini")
    with pytest.raises(RuntimeError, match="Unknown LLM_PROVIDER"):
        get_llm_client()


def test_openai_client_requires_key(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        OpenAILLMClient().complete([LLMMessage(role="user", content="hi")], max_tokens=50)


def test_openai_client_happy_path(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "LLM_MODEL", "gpt-5.6-luna")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "")

    captured = {}

    def fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["auth"] = headers.get("Authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello there"}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    out = OpenAILLMClient().complete(
        [LLMMessage(role="system", content="s"), LLMMessage(role="user", content="u")],
        max_tokens=42,
    )
    assert out == "hello there"
    assert captured["url"].endswith("/v1/chat/completions")
    assert captured["json"]["model"] == "gpt-5.6-luna"
    assert captured["auth"] == "Bearer sk-test"
    assert captured["json"]["messages"][0] == {"role": "system", "content": "s"}


def test_openai_client_fails_loud_on_non_2xx(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(429, text="rate limited"))
    with pytest.raises(RuntimeError, match="429"):
        OpenAILLMClient().complete([LLMMessage(role="user", content="u")], max_tokens=10)


def test_openai_client_fails_loud_on_empty_completion(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(
        httpx, "post",
        lambda *a, **k: httpx.Response(200, json={"choices": [{"message": {"content": "  "}}]}),
    )
    with pytest.raises(RuntimeError, match="empty"):
        OpenAILLMClient().complete([LLMMessage(role="user", content="u")], max_tokens=10)
```

- [ ] **Step 2: Run to see them fail**

Run: `poetry run pytest tests/platform/test_llm.py -v --no-cov`
Expected: FAIL — `ModuleNotFoundError: app.platform.llm`.

- [ ] **Step 3: Implement the seam**

```python
# app/platform/llm.py
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx

from app.core.config import settings

Role = Literal["system", "user", "assistant"]


@dataclass
class LLMMessage:
    role: Role
    content: str


class LLMClient(Protocol):
    def complete(
        self, messages: list[LLMMessage], *, max_tokens: int, temperature: float = 0.7
    ) -> str: ...


class StubLLMClient:
    """Deterministic, offline client for tests and local dev (LLM_PROVIDER=stub).

    Returns a fixed, recognizable string so a test can assert the AI path ran without
    a network call or an API key.
    """

    def complete(
        self, messages: list[LLMMessage], *, max_tokens: int, temperature: float = 0.7
    ) -> str:
        return "[stub-llm] AI-generated assessment narrative."


class OpenAILLMClient:
    """Calls the OpenAI (or OpenAI-compatible) Chat Completions API over httpx. Fail-loud.

    Never returns a silent empty/partial completion: a missing key, non-2xx, transport
    error, unparseable body, or empty text all raise RuntimeError so the worker job's
    retry/backoff can act on it.
    """

    def complete(
        self, messages: list[LLMMessage], *, max_tokens: int, temperature: float = 0.7
    ) -> str:
        if not settings.LLM_API_KEY:
            raise RuntimeError(
                "LLM_API_KEY is empty but LLM_PROVIDER='openai'. Set LLM_API_KEY, or use "
                "LLM_PROVIDER='stub' for local/dev (deterministic, no network)."
            )
        base = settings.LLM_BASE_URL or "https://api.openai.com"
        url = f"{base.rstrip('/')}/v1/chat/completions"
        payload = {
            "model": settings.LLM_MODEL,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        try:
            resp = httpx.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
                timeout=settings.LLM_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"LLM request to {url} failed: {exc}") from exc
        if resp.status_code // 100 != 2:
            raise RuntimeError(f"LLM API returned {resp.status_code}: {resp.text[:500]}")
        try:
            text = resp.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"LLM API returned an unparseable body: {exc}") from exc
        if not text or not text.strip():
            raise RuntimeError("LLM API returned an empty completion.")
        return text


def get_llm_client() -> LLMClient:
    provider = settings.LLM_PROVIDER
    if provider == "stub":
        return StubLLMClient()
    if provider == "openai":
        return OpenAILLMClient()
    raise RuntimeError(f"Unknown LLM_PROVIDER={provider!r}. Supported: 'openai', 'stub'.")
```

> **Build-time verification (do this before trusting a live call, not for the tests):** `gpt-5.6-luna` postdates the plan author's model knowledge. Fetch the current OpenAI docs for this model (`https://developers.openai.com/api/docs/models/gpt-5.6-luna`) and confirm: (a) it is served over `/v1/chat/completions` (if it is **Responses-API-only**, change `url` to `/v1/responses` and adapt the request/response mapping — the `LLMClient` interface and the tests stay the same, only the mocked shapes update); (b) the output-cap parameter name — some newer models use `max_completion_tokens` instead of `max_tokens`. Update the payload key and the happy-path test's assertion together if so. Do not add `reasoning.effort` in this slice (default is fine).

- [ ] **Step 4: Run to see them pass**

Run: `poetry run pytest tests/platform/test_llm.py -v --no-cov`
Expected: PASS (all 7).

- [ ] **Step 5: Commit**

```bash
git add app/platform/llm.py tests/platform/test_llm.py
git commit -m "feat(ai): provider-agnostic LLM seam (openai + stub, fail-loud)"
```

---

### Task 3: Assessment-narrative consumer (prompt builder + job handler + enqueue + register)

**Files:**
- Create: `app/services/assessment/narrative.py`
- Create: `app/worker/handlers/ai.py`
- Modify: `app/services/assessment/service.py` (enqueue, next to the `roadmap.replan` enqueue ~line 218)
- Modify: `app/worker/__main__.py` (`register()` imports the new handler module)
- Modify: `tests/worker/conftest.py` (evict `app.worker.handlers.ai` in the autouse fixture, mirroring `email`/`scheduled`)
- Test: `tests/services/assessment/test_narrative.py` (create), `tests/worker/test_ai_handler.py` (create)

**Interfaces:**
- Consumes: `LLMMessage`, `get_llm_client()` (Task 2); `settings.LLM_MAX_TOKENS` (Task 1); `register_handler` + `Handler = Callable[[Session, Job], None]` (`app/worker/runner.py`); `Job` (`app/db/models/job.py`, has `.type`, `.payload`); `AssessmentResult` (`app/db/models/assessment.py`, `.assessment_id`, `.dimension_scores: dict[str,int]`, `.overall_provisional: int`, `.narrative: str`); `Startup` (`.industry: str | None`, `.stage` enum with `.value`); `job_dispatcher.enqueue(db, type, payload, startup_id)` (already imported in `service.py`).
- Produces: `build_narrative_messages(*, dimension_scores, overall, industry, stage) -> list[LLMMessage]`; job type string `"ai.assessment.narrative"`; `handle_assessment_narrative(db, job)`.

- [ ] **Step 1: Write the failing prompt-builder test**

```python
# tests/services/assessment/test_narrative.py
from app.services.assessment.narrative import build_narrative_messages


def test_build_narrative_messages_includes_context_and_no_pii():
    msgs = build_narrative_messages(
        dimension_scores={"team": 70, "market": 40},
        overall=55,
        industry="Fintech",
        stage="validation",
    )
    assert [m.role for m in msgs] == ["system", "user"]
    user = msgs[1].content
    assert "Fintech" in user and "validation" in user
    assert "55" in user and "team" in user and "market" in user
    # data minimization: the builder has no parameter for names/emails, and emits none
    blob = " ".join(m.content for m in msgs).lower()
    assert "@" not in blob


def test_build_narrative_messages_tolerates_missing_profile():
    msgs = build_narrative_messages(
        dimension_scores={"team": 10}, overall=10, industry=None, stage=None
    )
    assert "unspecified" in msgs[1].content
```

- [ ] **Step 2: Run to see it fail**

Run: `poetry run pytest tests/services/assessment/test_narrative.py -v --no-cov`
Expected: FAIL — `ModuleNotFoundError: app.services.assessment.narrative`.

- [ ] **Step 3: Implement the prompt builder**

```python
# app/services/assessment/narrative.py
from app.platform.llm import LLMMessage


def build_narrative_messages(
    *,
    dimension_scores: dict[str, int],
    overall: int,
    industry: str | None,
    stage: str | None,
) -> list[LLMMessage]:
    """Turn a scored assessment + startup profile into an LLM prompt.

    Data minimization: only business signals go in — no names, emails, or identifiers.
    """
    dims = ", ".join(f"{k}: {v}/100" for k, v in sorted(dimension_scores.items()))
    system = (
        "You are an experienced startup advisor writing a concise, encouraging but honest "
        "assessment narrative for a founder. Write 2-3 short paragraphs. No preamble, no headings."
    )
    user = (
        f"Industry: {industry or 'unspecified'}. Stage: {stage or 'unspecified'}. "
        f"Overall readiness: {overall}/100. Dimension scores: {dims}. "
        "Write the narrative: what is strong, and what to prioritise next."
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]
```

- [ ] **Step 4: Run to see it pass**

Run: `poetry run pytest tests/services/assessment/test_narrative.py -v --no-cov`
Expected: PASS.

- [ ] **Step 5: Write the failing handler tests**

First extend the autouse fixture in `tests/worker/conftest.py` so it also evicts the new handler module (mirrors the existing `email`/`scheduled` handling):

```python
    sys.modules.pop("app.worker.handlers.ai", None)
```
(add it next to the existing `pop` calls, both before and after `yield`.)

```python
# tests/worker/test_ai_handler.py
import pytest

from app.core.config import settings
from app.db.models.job import Job, JobStatus
from app.worker.handlers.ai import handle_assessment_narrative
from tests.factories import create_assessment, create_startup, create_user


def _completed_result(db, startup):
    # create an assessment + a result row with a templated narrative to be overwritten
    from app.db.models.assessment import AssessmentResult
    from app.db.models.enums import AssessmentStatus

    a = create_assessment(db, startup=startup, status=AssessmentStatus.completed)
    r = AssessmentResult(
        assessment_id=a.id,
        dimension_scores={"team": 60, "market": 50},
        overall_provisional=55,
        narrative="TEMPLATED narrative.",
    )
    db.add(r)
    db.flush()
    return a, r


def _job(startup_id, assessment_id):
    return Job(
        type="ai.assessment.narrative",
        payload={"startup_id": str(startup_id), "assessment_id": str(assessment_id)},
        status=JobStatus.running,
    )


def test_handler_overwrites_narrative_with_llm_text(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    u = create_user(db)
    s = create_startup(db, owner=u)
    a, r = _completed_result(db, s)
    handle_assessment_narrative(db, _job(s.id, a.id))
    db.refresh(r)
    assert r.narrative.startswith("[stub-llm]")


def test_handler_is_a_noop_when_result_missing(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    u = create_user(db)
    s = create_startup(db, owner=u)
    a = create_assessment(db, startup=s)  # no result row
    handle_assessment_narrative(db, _job(s.id, a.id))  # must not raise


def test_handler_fails_loud_when_llm_errors(db, monkeypatch):
    # openai provider + empty key => get_llm_client() returns OpenAILLMClient, complete() raises
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    u = create_user(db)
    s = create_startup(db, owner=u)
    a, r = _completed_result(db, s)
    with pytest.raises(RuntimeError):
        handle_assessment_narrative(db, _job(s.id, a.id))
```

> Implementer note: confirm the `create_assessment` / `create_startup` / `create_user` factory signatures in `tests/factories.py` (per prior work: `create_assessment(db, *, startup, creator=None, type=..., status=..., bank_version="v1")` — no `completed_at` kwarg; `create_startup(db, owner=...)`). Adjust the helper if a signature differs; keep the assertions.

- [ ] **Step 6: Run to see them fail**

Run: `poetry run pytest tests/worker/test_ai_handler.py -v --no-cov`
Expected: FAIL — `ModuleNotFoundError: app.worker.handlers.ai`.

- [ ] **Step 7: Implement the handler**

```python
# app/worker/handlers/ai.py
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.assessment import AssessmentResult
from app.db.models.job import Job
from app.db.models.startup import Startup
from app.platform.llm import get_llm_client
from app.services.assessment.narrative import build_narrative_messages
from app.worker.runner import register_handler


def handle_assessment_narrative(db: Session, job: Job) -> None:
    """Rewrite an assessment's narrative with the LLM. No commit — the runner owns the txn."""
    assessment_id = job.payload["assessment_id"]
    startup_id = job.payload["startup_id"]
    result = (
        db.query(AssessmentResult)
        .filter(AssessmentResult.assessment_id == assessment_id)
        .one_or_none()
    )
    if result is None:
        return  # nothing to enrich (result missing) — benign no-op
    startup = db.get(Startup, startup_id)
    industry = startup.industry if startup else None
    stage = startup.stage.value if (startup and startup.stage) else None
    messages = build_narrative_messages(
        dimension_scores=result.dimension_scores,
        overall=result.overall_provisional,
        industry=industry,
        stage=stage,
    )
    text = get_llm_client().complete(messages, max_tokens=settings.LLM_MAX_TOKENS)
    result.narrative = text.strip()


register_handler("ai.assessment.narrative", handle_assessment_narrative)
```

- [ ] **Step 8: Wire the enqueue + the register import**

In `app/services/assessment/service.py`, directly after the existing `roadmap.replan` enqueue (~line 218), add:

```python
    job_dispatcher.enqueue(db, "ai.assessment.narrative", job_payload, startup.id)
```
(`job_payload` is the existing `{"startup_id": ..., "assessment_id": ...}` dict already built above.)

In `app/worker/__main__.py`, add to `register()` (next to the other handler imports):

```python
    import app.worker.handlers.ai  # noqa: F401
```

- [ ] **Step 9: Run the handler tests + the worker suite (no regression)**

Run: `poetry run pytest tests/worker/test_ai_handler.py tests/services/assessment/ tests/worker/ -q --no-cov`
Expected: PASS, including the existing worker + assessment tests.

- [ ] **Step 10: Commit**

```bash
git add app/services/assessment/narrative.py app/worker/handlers/ai.py app/services/assessment/service.py app/worker/__main__.py tests/worker/conftest.py tests/services/assessment/test_narrative.py tests/worker/test_ai_handler.py
git commit -m "feat(ai): assessment-narrative job — LLM rewrites the result narrative"
```

---

### Task 4: Live e2e + captures + FE guide + SOP + checklist

**Files:**
- Create: `e2e/test_ai_assessment_narrative.py`
- Modify: `scripts/e2e_run.sh` (add `export LLM_PROVIDER="stub"`)
- Create: `docs/fe-integration-guide-ai-assessment-narrative.md`
- Create: `docs/sop/2026-09-19-llm-seam-assessment-narrative.md`
- Modify: `docs/checklist/PROJECT_CHECKLIST.md`

**Interfaces:** consumes everything above; drives the assessment API to completion (mirror `e2e/test_assessment.py`) and drains the worker in-process (mirror `e2e/test_notifications_email.py::_drain`).

- [ ] **Step 1: Pin the stub in the e2e harness**

In `scripts/e2e_run.sh`, next to `export EMAIL_BACKEND="file"`, add:

```bash
export LLM_PROVIDER="stub"
```

This makes both the server process and the pytest process (which runs the in-process drain) use the offline deterministic client — no key, no network.

- [ ] **Step 2: Write the e2e journey**

```python
# e2e/test_ai_assessment_narrative.py
"""Live Module 03 Slice 1: the LLM seam rewrites the assessment narrative.

Drives a real assessment to completion via the API (mirrors e2e/test_assessment.py),
then drains the worker in-process (mirrors e2e/test_notifications_email.py::_drain).
With LLM_PROVIDER=stub (set by scripts/e2e_run.sh) the narrative deterministically
becomes the stub's AI text, proving enqueue -> job -> LLM -> persist end to end.
"""
import httpx


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _drain() -> None:
    # Lazy app imports (keep Settings out of collection, per e2e/test_notifications_email.py).
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import ai as _ai  # noqa: F401  (registers ai.assessment.narrative)

    db = SessionLocal()
    try:
        for _ in range(500):
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def test_assessment_narrative_is_ai_rewritten(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth(access)

        # Drive an assessment to completion. Mirror e2e/test_assessment.py exactly for the
        # start -> next-question -> answers -> complete walk (adaptive question loop), capturing
        # the assessment id and the completion response (templated narrative).
        # ... (reuse the helper/flow from e2e/test_assessment.py) ...
        # assessment_id = <the completed assessment's id>
        # complete_resp = <the POST /assessments/{id}/complete response>
        capture("ai_assessment_narrative", "complete_templated", complete_resp)

        # Drain: the ai.assessment.narrative job runs and overwrites the narrative.
        _drain()

        got = c.get(f"/api/v1/assessments/{assessment_id}", headers=auth)
        assert got.status_code == 200, got.text
        narrative = got.json()["data"]["result"]["narrative"]  # confirm exact path from the body
        assert narrative.startswith("[stub-llm]"), narrative
        capture("ai_assessment_narrative", "result_ai_narrative", got)
```

> Implementer note: fill in the assessment start→complete walk by mirroring `e2e/test_assessment.py` (it already answers the adaptive questions and completes). Confirm the exact JSON path to the narrative from the real `GET /assessments/{id}` body (it may be `data.result.narrative` or `data.narrative` — read the capture, do not guess) and fix the assertion + FE guide to match what the server actually returns.

- [ ] **Step 3: Run the e2e suite**

Run: `./scripts/e2e_run.sh`
Expected: all pass incl. the new journey; captures under `e2e/_captures/ai_assessment_narrative/`.

- [ ] **Step 4: Docs (after the run — captures byte-accurate)**

- `docs/fe-integration-guide-ai-assessment-narrative.md`: explain that `AssessmentResult.narrative`
  is **templated at completion and asynchronously upgraded to an AI version** (usually seconds later),
  and **stays templated if the AI job fails** — so the FE must re-fetch `GET /assessments/{id}` and
  not treat the completion-time narrative as final. Paste the two captured bodies verbatim
  (`complete_templated`, `result_ai_narrative`), trimmed but not invented. Add a small verification
  table (this behavior verified-live via `e2e/test_ai_assessment_narrative.py`).
- `docs/sop/2026-09-19-llm-seam-assessment-narrative.md`: what shipped (the seam + the one consumer),
  why (unblock Module 03 + the deferred AI hooks), how (seam mirrors the email seam; async job;
  templated fallback; fail-loud + job retry), files/config touched (no migration), the
  `LLM_PROVIDER/LLM_MODEL/LLM_TIMEOUT/LLM_MAX_TOKENS` knobs and the `stub` test/e2e mode, verification
  (unit + e2e), and the deferred items from the spec's Follow-ups. Match the existing `docs/sop/` style.
- `docs/checklist/PROJECT_CHECKLIST.md`: add a **Module 03 — AI Co-Founder** section marking Slice 1
  (LLM seam + assessment narrative) shipped, and move Module 03 from "not started" to "open" in the
  status snapshot/tally. Note that §08.11 (Module 08) is now **unblocked** on the seam (still needs
  structured-output support — a later slice).

- [ ] **Step 5: Full local CI reproduction**

Run each and make all green:
```
poetry run black --check app tests e2e && poetry run isort --check-only app tests e2e && poetry run ruff check app tests e2e
poetry run mypy app && poetry run pylint app --fail-under=9.5 && poetry run bandit -r app/ --quiet
poetry run pytest --cov=app --cov-fail-under=95 -q
poetry run alembic heads   # exactly one, UNCHANGED from develop (no migration this slice)
./scripts/e2e_run.sh
```
Expected: all green; one head.

- [ ] **Step 6: Commit**

```bash
git add e2e/ scripts/e2e_run.sh docs/
git commit -m "test(ai): LLM seam + assessment narrative live e2e + FE guide + SOP + checklist"
```

---

## Self-Review

**Spec coverage:**
- Seam (`llm.py`: Protocol, OpenAI, Stub, factory, fail-loud) → Task 2. ✓
- Config (LLM_* settings, gpt-5.6-luna default, timeout/max_tokens, env templates, stub in test/e2e) → Task 1 (settings/env) + Task 4 (e2e stub export); unit tests monkeypatch `settings.LLM_PROVIDER`. ✓
- Consumer (prompt builder, handler, enqueue-at-completion, register) → Task 3. ✓
- No migration → asserted in Global Constraints + Task 4 Step 5. ✓
- Error handling (fail-loud client, job retry/backoff, templated fallback) → Task 2 (fail-loud tests) + Task 3 (handler propagates) + inherent runner retry. ✓
- Data minimization (no PII in prompt) → Task 3 prompt builder + its test. ✓
- Testing (unit seam/factory/handler + e2e stub) → Tasks 2/3/4. ✓
- FE impact (narrative changes post-completion; re-fetch) → Task 4 FE guide. ✓
- Security (key from env, never logged) → Global Constraints + Task 2 impl (no logging of key). ✓

**Placeholder scan:** the e2e assessment start→complete walk is delegated to "mirror `e2e/test_assessment.py`" with the novel drain + assertion given in full — this is a reference to existing, working code (per the writing-plans rule against repeating ~60 lines of an existing flow), not an unwritten step. The exact narrative JSON path is explicitly "read from the capture, do not guess." The OpenAI request-shape verification is a labelled build-time check with a concrete resolution rule, not a TODO.

**Type consistency:** `LLMMessage`/`LLMClient.complete(messages, *, max_tokens, temperature=0.7)->str`, `StubLLMClient` output prefix `"[stub-llm]"`, `get_llm_client()`, job type `"ai.assessment.narrative"`, `build_narrative_messages(*, dimension_scores, overall, industry, stage)`, `handle_assessment_narrative(db, job)`, `AssessmentResult.{assessment_id,dimension_scores,overall_provisional,narrative}`, `Startup.{industry,stage}` — consistent across Tasks 1–4. ✓
