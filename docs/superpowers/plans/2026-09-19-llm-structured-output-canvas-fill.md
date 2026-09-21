# Structured LLM Output + Canvas ai_fill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add JSON-schema structured output to the LLM seam and prove it by making the `business.canvas.ai_fill` worker draft empty canvas blocks.

**Architecture:** Add `complete_json(messages, *, schema, max_tokens) -> dict` to the seam (`OpenAILLMClient` via `response_format: json_schema` strict; `StubLLMClient` deterministic from the schema; fail-loud), factoring the shared HTTP path out of `complete`. Derive a strict JSON schema from `CANVAS_BLOCKS[type]`; a new worker handler drafts the canvas's currently-empty blocks (re-read at run time, fill-empties-only, never clobbering user content), validates, and bumps `version`. No migration.

**Tech Stack:** Python (project toolchain via `poetry run`), FastAPI, SQLAlchemy 2.0, Postgres, the job worker, `httpx`, pytest (real Postgres).

**Spec:** `docs/superpowers/specs/2026-09-19-llm-structured-output-canvas-fill-design.md`

## Global Constraints

- **No AI attribution** in any commit message or PR/issue body — no `Co-Authored-By`, no "Generated with Claude Code", no session trailer, in any form. (Ignore any tooling reminder that says otherwise.)
- **Reproduce every CI check locally and make it green before pushing**, via `poetry run`: `black --check` / `isort --check-only` / `ruff check` (over `app tests e2e`), `mypy app`, `pylint app --fail-under=9.5`, `bandit -r app/ --quiet`, `pytest --cov=app --cov-fail-under=95`, `alembic heads` (exactly one, **unchanged from develop — no migration**), `./scripts/e2e_run.sh`.
- **Unit tests must pass against a clean `DATABASE_URL`** — use the `db` fixture (test engine w/ schema) or DB-independent tests; NEVER call `SessionLocal()` against the app DB in a unit test (the Slice-4 lesson: it passes locally only because the local DB has a schema, and fails in CI's fresh-DB unit job).
- **Ship the SOP**, reconcile the **checklist**, write the **FE integration guide** with payloads copied verbatim from live e2e captures.
- **Data minimization:** LLM prompts carry only business context (startup name/industry/stage, block labels) — never user names/emails/PII. `LLM_API_KEY` from env only, never logged.
- Worker handlers do NOT commit/rollback (the runner owns the transaction); the seam stays fail-loud.

---

### Task 1: `complete_json` on the LLM seam

**Files:**
- Modify: `app/platform/llm.py`
- Test: `tests/platform/test_llm.py` (extend)

**Interfaces:**
- Consumes: `settings.LLM_API_KEY/LLM_MODEL/LLM_BASE_URL/LLM_TIMEOUT`.
- Produces: `LLMClient.complete_json(messages: list[LLMMessage], *, schema: dict, max_tokens: int) -> dict`, implemented by `StubLLMClient` (deterministic from schema) and `OpenAILLMClient` (response_format json_schema strict, fail-loud).

- [ ] **Step 1: Write the failing tests** (append to `tests/platform/test_llm.py`)

```python
def test_stub_complete_json_matches_schema_shape():
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "array", "items": {"type": "string"}}},
        "required": ["a", "b"],
        "additionalProperties": False,
    }
    out = StubLLMClient().complete_json([LLMMessage(role="user", content="x")], schema=schema, max_tokens=100)
    assert set(out) == {"a", "b"}
    assert isinstance(out["a"], str) and out["a"].startswith("[stub-llm]")
    assert isinstance(out["b"], list) and out["b"] and out["b"][0].startswith("[stub-llm]")


def test_openai_complete_json_sends_json_schema_and_parses(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "LLM_MODEL", "gpt-5.6-luna")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "")
    captured = {}

    def fake_post(url, json, headers, timeout):
        captured["json"] = json
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"a": "hi", "b": ["x"]}'}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"], "additionalProperties": False}
    out = OpenAILLMClient().complete_json([LLMMessage(role="user", content="u")], schema=schema, max_tokens=50)
    assert out == {"a": "hi", "b": ["x"]}
    rf = captured["json"]["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"] == schema
    assert captured["json"]["max_completion_tokens"] == 50


def test_openai_complete_json_requires_key(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        OpenAILLMClient().complete_json([LLMMessage(role="user", content="u")], schema={"type": "object"}, max_tokens=10)


def test_openai_complete_json_fails_loud_on_non_2xx(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(429, text="rate limited"))
    with pytest.raises(RuntimeError, match="429"):
        OpenAILLMClient().complete_json([LLMMessage(role="user", content="u")], schema={"type": "object"}, max_tokens=10)


def test_openai_complete_json_fails_loud_on_bad_json(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]}))
    with pytest.raises(RuntimeError, match="unparseable"):
        OpenAILLMClient().complete_json([LLMMessage(role="user", content="u")], schema={"type": "object"}, max_tokens=10)


def test_openai_complete_json_fails_loud_on_non_object(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(200, json={"choices": [{"message": {"content": "[1,2,3]"}}]}))
    with pytest.raises(RuntimeError, match="non-object"):
        OpenAILLMClient().complete_json([LLMMessage(role="user", content="u")], schema={"type": "object"}, max_tokens=10)
```

- [ ] **Step 2: Run to see them fail**

Run: `poetry run pytest tests/platform/test_llm.py -k complete_json -v --no-cov`
Expected: FAIL — `complete_json` not defined.

- [ ] **Step 3: Implement — factor the shared HTTP path, add `complete_json`**

Edit `app/platform/llm.py`: add `import json` at top. Add `complete_json` to the `LLMClient` Protocol:

```python
class LLMClient(Protocol):
    def complete(
        self, messages: list[LLMMessage], *, max_tokens: int, temperature: float = 0.7
    ) -> str: ...

    def complete_json(
        self, messages: list[LLMMessage], *, schema: dict, max_tokens: int
    ) -> dict: ...
```

`StubLLMClient.complete_json`:

```python
    def complete_json(
        self, messages: list[LLMMessage], *, schema: dict, max_tokens: int
    ) -> dict:
        def _stub(prop: dict, key: str) -> object:
            return [f"[stub-llm] {key}"] if prop.get("type") == "array" else f"[stub-llm] {key}"

        return {k: _stub(v, k) for k, v in schema.get("properties", {}).items()}
```

In `OpenAILLMClient`, extract the shared request into a private helper and have BOTH methods use it (no duplicated key-check / post / status handling):

```python
    def _post_chat(self, payload: dict) -> dict:
        if not settings.LLM_API_KEY:
            raise RuntimeError(
                "LLM_API_KEY is empty but LLM_PROVIDER='openai'. Set LLM_API_KEY, or use "
                "LLM_PROVIDER='stub' for local/dev (deterministic, no network)."
            )
        base = settings.LLM_BASE_URL or "https://api.openai.com/v1"
        url = f"{base.rstrip('/')}/chat/completions"
        try:
            resp = httpx.post(
                url,
                json={"model": settings.LLM_MODEL, **payload},
                headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
                timeout=settings.LLM_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"LLM request to {url} failed: {exc}") from exc
        if resp.status_code // 100 != 2:
            raise RuntimeError(f"LLM API returned {resp.status_code}: {resp.text[:500]}")
        try:
            body: dict = resp.json()
        except ValueError as exc:
            raise RuntimeError(f"LLM API returned an unparseable body: {exc}") from exc
        return body

    def _content(self, body: dict) -> str:
        try:
            return str(body["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"LLM API returned an unexpected body shape: {exc}") from exc

    def complete(
        self, messages: list[LLMMessage], *, max_tokens: int, temperature: float = 0.7
    ) -> str:
        body = self._post_chat(
            {
                "messages": [{"role": m.role, "content": m.content} for m in messages],
                "max_completion_tokens": max_tokens,
            }
        )
        text = self._content(body)
        if not text or not text.strip():
            raise RuntimeError("LLM API returned an empty completion.")
        return text

    def complete_json(
        self, messages: list[LLMMessage], *, schema: dict, max_tokens: int
    ) -> dict:
        body = self._post_chat(
            {
                "messages": [{"role": m.role, "content": m.content} for m in messages],
                "max_completion_tokens": max_tokens,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "result", "schema": schema, "strict": True},
                },
            }
        )
        content = self._content(body)
        try:
            data = json.loads(content)
        except ValueError as exc:
            raise RuntimeError(f"LLM API returned an unparseable JSON body: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("LLM API returned a non-object JSON result.")
        return data
```
Keep the existing class docstring (max_completion_tokens / no-temperature rationale). Ensure the `complete` refactor preserves behavior — the existing `complete` tests must still pass.

> **Build-time verification (required, not for the tests):** confirm `gpt-5.6-luna` supports `response_format: {type: json_schema, strict: true}` over `/v1/chat/completions` — fetch the live OpenAI structured-outputs docs AND do ONE throwaway real `complete_json` call (creds are in the local gitignored `.env`) with a tiny schema, printing the parsed dict. If it succeeds, the shape is right. If structured output needs the Responses API for this model, adapt `_post_chat`/`complete_json` accordingly (interface unchanged). NEVER print the key; do not add any network/key-dependent committed test.

- [ ] **Step 4: Run to see them pass (+ existing complete tests still green)**

Run: `poetry run pytest tests/platform/test_llm.py -v --no-cov` then `poetry run mypy app/platform/llm.py && poetry run ruff check app/platform/llm.py`
Expected: all PASS, mypy + ruff clean.

- [ ] **Step 5: Commit**

```bash
git add app/platform/llm.py tests/platform/test_llm.py
git commit -m "feat(ai): structured JSON-schema output on the LLM seam (complete_json)"
```

---

### Task 2: Canvas schema + prompt + `business.canvas.ai_fill` worker

**Files:**
- Modify: `app/services/business/canvas_defs.py` (add `canvas_json_schema`)
- Create: `app/services/business/ai_fill.py` (prompt builder)
- Modify: `app/worker/handlers/ai.py` (add `handle_canvas_ai_fill` + register)
- Test: `tests/services/business/test_canvas_ai_fill.py` (create)

**Interfaces:**
- Consumes: `complete_json` + `LLMMessage` + `get_llm_client` (Task 1); `settings.LLM_MAX_TOKENS`; `CANVAS_BLOCKS`, `CanvasType` (`canvas_defs.py` / enums); `get_or_create_canvas`, `validate_blocks` (`app/services/business/service.py`); `Startup` (`db.get`); `register_handler`, `Job`.
- Produces: `canvas_json_schema(canvas_type: CanvasType) -> dict`; `build_canvas_fill_messages(*, name, industry, stage, blocks) -> list[LLMMessage]`; `handle_canvas_ai_fill(db, job)`; job type `"business.canvas.ai_fill"`.

- [ ] **Step 1: Write the failing schema + prompt tests**

```python
# tests/services/business/test_canvas_ai_fill.py
from app.db.models.enums import CanvasType
from app.services.business.ai_fill import build_canvas_fill_messages
from app.services.business.canvas_defs import CANVAS_BLOCKS, canvas_json_schema


def test_canvas_json_schema_is_strict_and_typed():
    schema = canvas_json_schema(CanvasType.business_model)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    keys = [b.key for b in CANVAS_BLOCKS[CanvasType.business_model]]
    assert set(schema["properties"]) == set(keys)
    assert schema["required"] == list(schema["properties"])
    # list blocks -> array of strings; text blocks -> string
    for b in CANVAS_BLOCKS[CanvasType.business_model]:
        prop = schema["properties"][b.key]
        if b.kind == "list":
            assert prop == {"type": "array", "items": {"type": "string"}}
        else:
            assert prop == {"type": "string"}


def test_build_canvas_fill_messages_has_context_no_pii():
    msgs = build_canvas_fill_messages(
        name="Acme", industry="Fintech", stage="validation",
        blocks=CANVAS_BLOCKS[CanvasType.business_model],
    )
    assert [m.role for m in msgs] == ["system", "user"]
    user = msgs[1].content
    assert "Acme" in user and "Fintech" in user and "validation" in user
    assert "Key Partners" in user  # a block label is present
    assert "@" not in " ".join(m.content for m in msgs)  # no emails/PII
```

- [ ] **Step 2: Run to see them fail**

Run: `poetry run pytest tests/services/business/test_canvas_ai_fill.py -v --no-cov`
Expected: FAIL — `canvas_json_schema` / `ai_fill` not defined.

- [ ] **Step 3: Implement the schema builder + prompt builder**

Add to `app/services/business/canvas_defs.py`:

```python
def canvas_json_schema(canvas_type: CanvasType) -> dict[str, Any]:
    """A strict JSON Schema for one canvas type: text blocks -> string, list blocks -> array
    of strings; every block required, no extra keys (OpenAI strict json_schema mode)."""
    props: dict[str, Any] = {}
    for b in CANVAS_BLOCKS[canvas_type]:
        props[b.key] = (
            {"type": "array", "items": {"type": "string"}} if b.kind == "list" else {"type": "string"}
        )
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }
```

Create `app/services/business/ai_fill.py`:

```python
from collections.abc import Sequence

from app.platform.llm import LLMMessage
from app.services.business.canvas_defs import BlockDef


def build_canvas_fill_messages(
    *, name: str | None, industry: str | None, stage: str | None, blocks: Sequence[BlockDef]
) -> list[LLMMessage]:
    """Prompt for drafting a business canvas. Business context only — no PII."""
    lines = "\n".join(
        f"- {b.key} ({b.label}): {'a list of short strings' if b.kind == 'list' else 'a short string'}"
        for b in blocks
    )
    system = (
        "You are a startup strategist drafting a business canvas. Return concise, concrete content "
        "for each block. Lists should be short arrays of terse phrases; text blocks a sentence or two."
    )
    user = (
        f"Startup: {name or 'unnamed'}. Industry: {industry or 'unspecified'}. "
        f"Stage: {stage or 'unspecified'}.\nFill every block:\n{lines}"
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]
```

- [ ] **Step 4: Run schema/prompt tests to pass**

Run: `poetry run pytest tests/services/business/test_canvas_ai_fill.py -v --no-cov`
Expected: the 2 tests PASS.

- [ ] **Step 5: Write the failing handler tests**

```python
# append to tests/services/business/test_canvas_ai_fill.py
from app.core.config import settings
from app.db.models.job import Job, JobStatus
from app.services.business.service import get_or_create_canvas
from app.worker.handlers.ai import handle_canvas_ai_fill
from tests.factories import create_startup, create_user


def _job(startup_id, canvas_type):
    return Job(
        type="business.canvas.ai_fill",
        payload={"startup_id": str(startup_id), "canvas_type": canvas_type.value},
        status=JobStatus.running,
    )


def test_ai_fill_fills_empty_canvas(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    u = create_user(db); s = create_startup(db, owner=u)
    handle_canvas_ai_fill(db, _job(s.id, CanvasType.business_model))
    canvas = get_or_create_canvas(db, s, CanvasType.business_model)
    # every block now filled with the stub marker
    for b in CANVAS_BLOCKS[CanvasType.business_model]:
        val = canvas.blocks[b.key]
        assert (val and "[stub-llm]" in (val if isinstance(val, str) else val[0]))
    assert canvas.version >= 2  # bumped from the create's version 1


def test_ai_fill_preserves_user_filled_blocks(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    u = create_user(db); s = create_startup(db, owner=u)
    canvas = get_or_create_canvas(db, s, CanvasType.business_model)
    canvas.blocks = {**canvas.blocks, "value_propositions": ["MY OWN VALUE"]}
    db.flush()
    handle_canvas_ai_fill(db, _job(s.id, CanvasType.business_model))
    db.refresh(canvas)
    assert canvas.blocks["value_propositions"] == ["MY OWN VALUE"]  # preserved
    assert "[stub-llm]" in canvas.blocks["key_partners"][0]  # empty one filled


def test_ai_fill_noop_when_startup_missing(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    import uuid
    handle_canvas_ai_fill(db, _job(uuid.uuid4(), CanvasType.business_model))  # must not raise


def test_ai_fill_fails_loud_when_llm_errors(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    u = create_user(db); s = create_startup(db, owner=u)
    import pytest
    with pytest.raises(RuntimeError):
        handle_canvas_ai_fill(db, _job(s.id, CanvasType.business_model))
```

- [ ] **Step 6: Run to see them fail**

Run: `poetry run pytest tests/services/business/test_canvas_ai_fill.py -v --no-cov`
Expected: FAIL — `handle_canvas_ai_fill` not defined.

- [ ] **Step 7: Implement the handler + register**

Add to `app/worker/handlers/ai.py` (add imports: `from app.db.models.enums import CanvasType`, `from app.services.business.canvas_defs import CANVAS_BLOCKS, canvas_json_schema`, `from app.services.business.ai_fill import build_canvas_fill_messages`, `from app.services.business.service import get_or_create_canvas, validate_blocks`):

```python
def handle_canvas_ai_fill(db: Session, job: Job) -> None:
    """Draft a business canvas's EMPTY blocks with the LLM (structured output).

    Fill-empties-only: re-reads the canvas at run time and never overwrites a block the
    user already filled. No commit — the runner owns the txn.
    """
    startup = db.get(Startup, job.payload["startup_id"])
    if startup is None:
        return  # benign no-op
    canvas_type = CanvasType(job.payload["canvas_type"])
    canvas = get_or_create_canvas(db, startup, canvas_type)
    current = dict(canvas.blocks or {})
    empty_keys = [b.key for b in CANVAS_BLOCKS[canvas_type] if not current.get(b.key)]
    if not empty_keys:
        return  # nothing to fill
    messages = build_canvas_fill_messages(
        name=startup.name,
        industry=startup.industry,
        stage=(startup.stage.value if startup.stage else None),
        blocks=CANVAS_BLOCKS[canvas_type],
    )
    filled = get_llm_client().complete_json(
        messages, schema=canvas_json_schema(canvas_type), max_tokens=settings.LLM_MAX_TOKENS
    )
    merged = dict(current)
    for key in empty_keys:
        if key in filled:
            merged[key] = filled[key]
    validate_blocks(canvas_type, merged)
    canvas.blocks = merged
    canvas.version += 1
    db.flush()


register_handler("business.canvas.ai_fill", handle_canvas_ai_fill)
```

- [ ] **Step 8: Run the handler + business + worker suites**

Run: `poetry run pytest tests/services/business/test_canvas_ai_fill.py tests/services/business/ tests/worker/ -q --no-cov` then `poetry run mypy app && poetry run ruff check app`
Expected: PASS; mypy + ruff clean.

- [ ] **Step 9: Commit**

```bash
git add app/services/business/canvas_defs.py app/services/business/ai_fill.py app/worker/handlers/ai.py tests/services/business/test_canvas_ai_fill.py
git commit -m "feat(ai): business.canvas.ai_fill worker drafts empty blocks via structured output"
```

---

### Task 3: Live e2e + captures + FE guide + SOP + checklist

**Files:**
- Create: `e2e/test_canvas_ai_fill.py`
- Create: `docs/fe-integration-guide-ai-canvas-fill.md`
- Create: `docs/sop/2026-09-19-llm-structured-output-canvas-fill.md`
- Modify: `docs/checklist/PROJECT_CHECKLIST.md`

**Interfaces:** consumes everything above; drives the shipped `POST /business-builder/canvases/{type}/ai-fill` endpoint and drains the worker in-process.

- [ ] **Step 1: Write the e2e journey**

```python
# e2e/test_canvas_ai_fill.py
"""Live Module 03 Slice 2: structured output drafts a business canvas.

Founder triggers POST /business-builder/canvases/business_model/ai-fill (202, already shipped),
we drain the worker in-process (LLM_PROVIDER=stub, set by scripts/e2e_run.sh), then GET the
canvas and assert its blocks were filled with the stub's structured output.
"""
import httpx


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _drain() -> None:
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import ai as _ai  # noqa: F401  (registers business.canvas.ai_fill)

    db = SessionLocal()
    try:
        for _ in range(500):
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def test_canvas_ai_fill(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth(access)
        wh = _onboard(c, auth)  # mirror e2e/test_assessment.py -> {**auth, "X-Workspace-Id": <startup_id>}

        fill = c.post("/api/v1/business-builder/canvases/business_model/ai-fill", headers=wh)
        assert fill.status_code == 202, fill.text
        capture("canvas_ai_fill", "ai_fill_enqueued", fill)

        _drain()

        got = c.get("/api/v1/business-builder/canvases/business_model", headers=wh)
        assert got.status_code == 200, got.text
        blocks = got.json()["data"]["blocks"]  # confirm exact path from the real body
        assert any("[stub-llm]" in (v if isinstance(v, str) else (v[0] if v else "")) for v in blocks.values())
        capture("canvas_ai_fill", "canvas_after_fill", got)
```

> Implementer note: fill `_onboard` by mirroring `e2e/test_assessment.py` (signup→login→onboard→`X-Workspace-Id` from `/auth/me` — note `/auth/me` nests the user id at `data.user.id`; the workspace id is `data.active_workspace_id` — confirm against the real body). Confirm the exact JSON path to the canvas blocks and the ai-fill route prefix from the running app (`app.openapi()` / a real response) before finalizing the assertion + the FE guide — read the capture, don't guess. Use a bounded timeout.

- [ ] **Step 2: Run the e2e suite**

Run: `./scripts/e2e_run.sh`
Expected: all pass incl. the new journey; captures under `e2e/_captures/canvas_ai_fill/`.

- [ ] **Step 3: Docs (after the run — captures byte-accurate)**

- `docs/fe-integration-guide-ai-canvas-fill.md` (create): `POST /business-builder/canvases/{type}/ai-fill` (202, returns `{job_id, status}`) now completes asynchronously — the canvas's EMPTY blocks get AI drafts within seconds; **ai-fill augments, never overwrites** user-filled blocks; `version` increments. The FE polls `GET /jobs/{id}` (existing) or re-fetches the canvas. Paste the captured 202 + filled-canvas bodies VERBATIM. Verification table (verified-live via the new e2e).
- `docs/sop/2026-09-19-llm-structured-output-canvas-fill.md` (create; match existing SOP style): what shipped (structured `complete_json` + canvas ai_fill worker), why (unblock §08.11 + the queued ai_fill jobs), how (`response_format` json_schema strict; schema from `CANVAS_BLOCKS`; fill-empties-only re-read at run time; fail-loud + job retry; DRY'd `_post_chat`), files/config (no migration), verification (unit + e2e), deferred follow-ups (records `ai_fill`; §08.11; pydantic-model variant; Anthropic; overwrite mode).
- `docs/checklist/PROJECT_CHECKLIST.md`: under **Module 03**, mark Slice 2 (structured output + canvas ai_fill) shipped (2026-09-19); note the `business.canvas.ai_fill` job now has a worker and §08.11 is unblocked on the capability. Under **Module 08**, update the `business.canvas.ai_fill` "enqueue-only, no worker" deferral to "worker shipped (Module 03 Slice 2)". Keep counts honest (Module 03 stays open — records ai_fill + §08.11 remain).

- [ ] **Step 4: Full local CI reproduction**

Run:
```
poetry run black --check app tests e2e && poetry run isort --check-only app tests e2e && poetry run ruff check app tests e2e
poetry run mypy app && poetry run pylint app --fail-under=9.5 && poetry run bandit -r app/ --quiet
poetry run pytest --cov=app --cov-fail-under=95 -q
poetry run alembic heads   # exactly one, UNCHANGED from develop (no migration)
./scripts/e2e_run.sh
```
Expected: all green; one head. After a green e2e, `git checkout -- e2e/_captures/` for everything EXCEPT the new `e2e/_captures/canvas_ai_fill/`.

- [ ] **Step 5: Commit**

```bash
git add e2e/ docs/
git commit -m "test(ai): canvas ai_fill live e2e + FE guide + SOP + checklist"
```

---

## Self-Review

**Spec coverage:**
- `complete_json` (Protocol + stub + OpenAI strict json_schema + fail-loud) → Task 1. ✓
- Canvas JSON-schema builder → Task 2. ✓
- `business.canvas.ai_fill` worker (fill-empties-only, re-read, validate, version bump, register) → Task 2. ✓
- No migration → Global Constraints + Task 3 Step 4. ✓
- Data minimization (no PII in prompt) → Task 2 prompt + its test. ✓
- Fail-loud + no partial write → Task 1 (fail-loud) + Task 2 (merge/validate/assign only after successful parse). ✓
- Testing (unit seam/schema/prompt/handler + live e2e) → Tasks 1–3; DB-clean rule honored (handler tests use the `db` fixture, no `SessionLocal()`). ✓
- FE impact (async augment-not-overwrite; poll/re-fetch) → Task 3 FE guide. ✓

**Placeholder scan:** the e2e `_onboard` delegates to mirroring `e2e/test_assessment.py` (existing flow), with the novel enqueue+drain+assert given in full; the OpenAI structured-output verification is a labeled build-time step with a resolution rule; the exact canvas JSON path is "read from the capture, don't guess."

**Type consistency:** `complete_json(messages, *, schema: dict, max_tokens: int) -> dict`; `canvas_json_schema(CanvasType) -> dict`; `build_canvas_fill_messages(*, name, industry, stage, blocks) -> list[LLMMessage]`; `handle_canvas_ai_fill(db, job)`; job type `"business.canvas.ai_fill"`; stub markers `[stub-llm]`; helpers `get_or_create_canvas`/`validate_blocks`/`CANVAS_BLOCKS`/`CanvasType` — consistent across Tasks 1–3. ✓
