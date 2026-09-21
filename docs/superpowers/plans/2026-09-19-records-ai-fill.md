# Records ai_fill Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consume the `business.{kind}.ai_fill` jobs — when a record kind is empty, generate up to 3 AI-drafted records via structured output and append them.

**Architecture:** Extend the Slice-2 structured-output stub to recurse nested schemas; add a strict per-kind JSON schema (`record_json_schema`) + a PII-free prompt builder; add a worker handler (registered for all four `business.{kind}.ai_fill` types) that fills only when the kind is empty, generates up to 3 records with `complete_json`, and appends each via the existing pydantic-validating `create_record` (skipping any that fail). No migration; reuses the existing `business.artifact.completed` event.

**Tech Stack:** Python (project toolchain via `poetry run`), FastAPI, SQLAlchemy 2.0, Postgres, the job worker, the LLM seam (`app/platform/llm.py`), pytest.

**Spec:** `docs/superpowers/specs/2026-09-19-records-ai-fill-design.md`

## Global Constraints

- **No AI attribution** in any commit or PR/issue body.
- **Reproduce every CI check locally and make it green before pushing** (black/isort/ruff over `app tests e2e`, `mypy app`, `pylint app --fail-under=9.5`, `bandit -r app/ --quiet`, `pytest --cov=app --cov-fail-under=95`, `alembic heads` = **exactly one, UNCHANGED — no migration**, `./scripts/e2e_run.sh`), via `poetry run`.
- **Unit tests DB-clean** — `db` fixture / DB-independent; never `SessionLocal()` against the app DB.
- **Data minimization:** prompts carry only startup name/industry/stage + field labels; no PII. `LLM_API_KEY` never logged.
- **Ship the SOP**, reconcile the **checklist**, write the **FE integration guide** note with payloads verbatim from live e2e captures.
- Worker handlers do NOT commit/rollback (runner owns the txn); seam fail-loud.

---

### Task 1: Stub recursion + `record_json_schema` + prompt builder

**Files:**
- Modify: `app/platform/llm.py` (`StubLLMClient.complete_json`)
- Modify: `app/services/business/record_defs.py` (add `record_json_schema`), `app/services/business/ai_fill.py` (add `build_record_fill_messages`)
- Test: `tests/platform/test_llm.py` (extend), `tests/services/business/test_records_ai_fill.py` (create)

**Interfaces:**
- Consumes: `RECORD_SCHEMAS`, `RecordKind`, `ThreatLevel`, `PricingModelType` (`record_defs.py` / enums); `LLMMessage` (`app/platform/llm.py`).
- Produces: `StubLLMClient.complete_json` recursing nested schemas; `record_json_schema(kind: RecordKind) -> dict`; `build_record_fill_messages(kind, *, name, industry, stage) -> list[LLMMessage]`.

- [ ] **Step 1: Write the failing stub-recursion test** (append to `tests/platform/test_llm.py`)

```python
def test_stub_complete_json_recurses_nested_objects_and_enums():
    schema = {
        "type": "object",
        "properties": {
            "records": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "score": {"type": "number"},
                        "level": {"type": "string", "enum": ["low", "high"]},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["name", "score", "level", "tags"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["records"],
        "additionalProperties": False,
    }
    out = StubLLMClient().complete_json([LLMMessage(role="user", content="x")], schema=schema, max_tokens=100)
    assert isinstance(out["records"], list) and len(out["records"]) == 1
    rec = out["records"][0]
    assert isinstance(rec["name"], str) and rec["name"].startswith("[stub-llm]")
    assert rec["score"] == 0
    assert rec["level"] == "low"          # first enum value
    assert rec["tags"] == ["[stub-llm] tags"]


def test_stub_complete_json_still_flat_for_canvas_shape():
    schema = {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "array", "items": {"type": "string"}}}, "required": ["a", "b"], "additionalProperties": False}
    out = StubLLMClient().complete_json([LLMMessage(role="user", content="x")], schema=schema, max_tokens=50)
    assert out["a"].startswith("[stub-llm]") and out["b"] == ["[stub-llm] b"]
```

- [ ] **Step 2: Run to see them fail**

Run: `poetry run pytest tests/platform/test_llm.py -k stub_complete_json -v --no-cov`
Expected: the nested test FAILs (current stub returns `records: ["[stub-llm] records"]`, a list of strings, not objects).

- [ ] **Step 3: Make the stub recursive**

Replace `StubLLMClient.complete_json`'s body in `app/platform/llm.py` with a recursive builder:

```python
    def complete_json(self, messages: list[LLMMessage], *, schema: dict, max_tokens: int) -> dict:
        result = self._stub_value(schema, "value")
        return result if isinstance(result, dict) else {"value": result}

    @staticmethod
    def _stub_value(node: dict, key: str) -> object:
        if "enum" in node and node["enum"]:
            return node["enum"][0]
        node_type = node.get("type")
        if node_type == "object":
            return {k: StubLLMClient._stub_value(v, k) for k, v in node.get("properties", {}).items()}
        if node_type == "array":
            return [StubLLMClient._stub_value(node.get("items", {"type": "string"}), key)]
        if node_type in ("number", "integer"):
            return 0
        if node_type == "boolean":
            return False
        return f"[stub-llm] {key}"
```
This preserves the flat canvas behavior (object of string/array-of-string → same output) and adds nesting/enums/numbers.

- [ ] **Step 4: Run the stub tests + the existing complete_json tests**

Run: `poetry run pytest tests/platform/test_llm.py -v --no-cov`
Expected: all PASS (new nested + flat, plus the Slice-2 canvas/schema tests unchanged).

- [ ] **Step 5: Write the failing schema + prompt tests** (`tests/services/business/test_records_ai_fill.py`)

```python
from app.db.models.enums import PricingModelType, RecordKind, ThreatLevel
from app.services.business.ai_fill import build_record_fill_messages
from app.services.business.record_defs import record_json_schema


def test_record_json_schema_is_strict_records_array():
    for kind in RecordKind:
        s = record_json_schema(kind)
        assert s["type"] == "object" and s["required"] == ["records"] and s["additionalProperties"] is False
        arr = s["properties"]["records"]
        assert arr["type"] == "array" and arr["maxItems"] == 3
        item = arr["items"]
        assert item["type"] == "object" and item["additionalProperties"] is False
        assert item["required"] == list(item["properties"])  # strict: all required


def test_record_json_schema_enums():
    comp = record_json_schema(RecordKind.competitor)["properties"]["records"]["items"]["properties"]
    assert comp["threat_level"]["enum"] == [m.value for m in ThreatLevel]
    assert "map_x" not in comp and "map_y" not in comp  # UI coords omitted from ai-fill
    pricing = record_json_schema(RecordKind.pricing)["properties"]["records"]["items"]["properties"]
    assert pricing["model_type"]["enum"] == [m.value for m in PricingModelType]
    assert pricing["tiers"]["items"]["type"] == "object"


def test_build_record_fill_messages_pii_free():
    msgs = build_record_fill_messages(RecordKind.persona, name="Acme", industry="Fintech", stage="validation")
    assert [m.role for m in msgs] == ["system", "user"]
    blob = " ".join(m.content for m in msgs)
    assert "Acme" in blob and "persona" in blob.lower() and "@" not in blob
```

- [ ] **Step 6: Run to see them fail**

Run: `poetry run pytest tests/services/business/test_records_ai_fill.py -v --no-cov`
Expected: FAIL — `record_json_schema` / `build_record_fill_messages` not defined.

- [ ] **Step 7: Implement `record_json_schema`** (add to `app/services/business/record_defs.py`)

```python
def _record_item_schema(kind: RecordKind) -> dict[str, Any]:
    _str = {"type": "string"}
    _strs = {"type": "array", "items": {"type": "string"}}
    if kind == RecordKind.persona:
        props = {"name": _str, "demographics": _str, "goals": _strs,
                 "frustrations": _strs, "watering_holes": _strs, "quote": _str}
    elif kind == RecordKind.revenue_stream:
        props = {"name": _str, "pricing_basis": _str, "est_monthly": {"type": "number"}, "assumptions": _str}
    elif kind == RecordKind.competitor:
        props = {"name": _str, "positioning": _str, "price": _str, "strengths": _strs, "weaknesses": _strs,
                 "threat_level": {"type": "string", "enum": [m.value for m in ThreatLevel]}}
        # map_x/map_y (UI positioning coords) intentionally omitted — the AI shouldn't set them.
    elif kind == RecordKind.pricing:
        props = {
            "model_type": {"type": "string", "enum": [m.value for m in PricingModelType]},
            "tiers": {"type": "array", "items": {
                "type": "object",
                "properties": {"name": _str, "price": _str, "features": _strs},
                "required": ["name", "price", "features"], "additionalProperties": False,
            }},
        }
    else:  # pragma: no cover - exhaustive over RecordKind
        raise ValueError(kind)
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def record_json_schema(kind: RecordKind) -> dict[str, Any]:
    """Strict OpenAI JSON schema: an object with a `records` array (<=3) of the kind's shape."""
    return {
        "type": "object",
        "properties": {"records": {"type": "array", "maxItems": 3, "items": _record_item_schema(kind)}},
        "required": ["records"],
        "additionalProperties": False,
    }
```

- [ ] **Step 8: Implement `build_record_fill_messages`** (add to `app/services/business/ai_fill.py`)

```python
from app.db.models.enums import RecordKind  # add import


def build_record_fill_messages(
    kind: RecordKind, *, name: str | None, industry: str | None, stage: str | None
) -> list[LLMMessage]:
    """Prompt for drafting up to 3 records of one kind. Business context only — no PII."""
    label = kind.value.replace("_", " ")
    system = (
        "You are a startup strategist. Return up to 3 realistic, concrete "
        f"{label} records for the startup. Keep each terse and specific."
    )
    user = (
        f"Startup: {name or 'unnamed'}. Industry: {industry or 'unspecified'}. "
        f"Stage: {stage or 'unspecified'}.\nGenerate up to 3 {label} records."
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]
```

- [ ] **Step 9: Run + typecheck**

Run: `poetry run pytest tests/services/business/test_records_ai_fill.py tests/platform/test_llm.py -v --no-cov` then `poetry run mypy app && poetry run ruff check app`
Expected: PASS; mypy + ruff clean.

- [ ] **Step 10: Commit**

```bash
git add app/platform/llm.py app/services/business/record_defs.py app/services/business/ai_fill.py tests/platform/test_llm.py tests/services/business/test_records_ai_fill.py
git commit -m "feat(ai): recursive structured stub + strict record schemas + record-fill prompt"
```

---

### Task 2: `business.{kind}.ai_fill` worker

**Files:**
- Modify: `app/worker/handlers/ai.py` (add `handle_record_ai_fill` + register 4 types)
- Test: `tests/worker/test_records_ai_fill_handler.py` (create)

**Interfaces:**
- Consumes: `record_json_schema`, `build_record_fill_messages` (Task 1); `get_llm_client` + `settings.LLM_MAX_TOKENS`; `create_record` (`app/services/business/records.py`); `RecordKind`, `BusinessRecord`, `Startup`, `AppError`; `register_handler`, `Job`.
- Produces: `handle_record_ai_fill(db, job)`; job types `business.persona.ai_fill` / `business.revenue_stream.ai_fill` / `business.competitor.ai_fill` / `business.pricing.ai_fill`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/worker/test_records_ai_fill_handler.py
import pytest

from app.core.config import settings
from app.db.models.business import BusinessRecord
from app.db.models.enums import RecordKind
from app.db.models.job import Job, JobStatus
from app.services.business.records import create_record
from app.worker.handlers.ai import handle_record_ai_fill
from tests.factories import create_startup, create_user


def _job(startup_id, kind):
    return Job(type=f"business.{kind.value}.ai_fill",
               payload={"startup_id": str(startup_id), "kind": kind.value}, status=JobStatus.running)


def _count(db, s, kind):
    return db.query(BusinessRecord).filter_by(startup_id=s.id, kind=kind).count()


def test_ai_fill_populates_empty_kind(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    u = create_user(db); s = create_startup(db, owner=u)
    handle_record_ai_fill(db, _job(s.id, RecordKind.persona))
    n = _count(db, s, RecordKind.persona)
    assert 1 <= n <= 3
    rec = db.query(BusinessRecord).filter_by(startup_id=s.id, kind=RecordKind.persona).first()
    assert "[stub-llm]" in rec.data["name"]  # pydantic-valid record created


def test_ai_fill_noop_when_kind_not_empty(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    u = create_user(db); s = create_startup(db, owner=u)
    create_record(db, s, RecordKind.persona, {"name": "Mine"})
    handle_record_ai_fill(db, _job(s.id, RecordKind.persona))
    assert _count(db, s, RecordKind.persona) == 1  # unchanged


def test_ai_fill_noop_when_startup_missing(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    import uuid
    handle_record_ai_fill(db, _job(uuid.uuid4(), RecordKind.persona))  # no raise


def test_ai_fill_fails_loud_on_llm_error(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    u = create_user(db); s = create_startup(db, owner=u)
    with pytest.raises(RuntimeError):
        handle_record_ai_fill(db, _job(s.id, RecordKind.competitor))
```

- [ ] **Step 2: Run to see them fail**

Run: `poetry run pytest tests/worker/test_records_ai_fill_handler.py -v --no-cov`
Expected: FAIL — `handle_record_ai_fill` not defined.

- [ ] **Step 3: Implement the handler** (add to `app/worker/handlers/ai.py`; add imports)

```python
# add imports at top of handlers/ai.py:
from app.core.errors import AppError
from app.db.models.business import BusinessRecord
from app.db.models.enums import RecordKind
from app.services.business.ai_fill import build_record_fill_messages
from app.services.business.record_defs import record_json_schema
from app.services.business.records import create_record


def handle_record_ai_fill(db: Session, job: Job) -> None:
    """Draft up to 3 records for an EMPTY record kind via structured output. No commit."""
    kind = RecordKind(job.payload["kind"])
    startup = db.get(Startup, job.payload["startup_id"])
    if startup is None:
        return
    if db.query(BusinessRecord).filter_by(startup_id=startup.id, kind=kind).count():
        return  # fill-empties only
    result = get_llm_client().complete_json(
        build_record_fill_messages(
            kind,
            name=startup.name,
            industry=startup.industry,
            stage=(startup.stage.value if startup.stage else None),
        ),
        schema=record_json_schema(kind),
        max_tokens=settings.LLM_MAX_TOKENS,
    )
    for rec in (result.get("records") or [])[:3]:
        try:
            create_record(db, startup, kind, rec)
        except AppError:
            continue  # skip a record that fails the kind's validation; keep the good ones
    db.flush()


for _kind in RecordKind:
    register_handler(f"business.{_kind.value}.ai_fill", handle_record_ai_fill)
```

- [ ] **Step 4: Run tests + worker suite**

Run: `poetry run pytest tests/worker/test_records_ai_fill_handler.py tests/worker/ tests/services/business/ -q --no-cov` then `poetry run mypy app && poetry run ruff check app`
Expected: PASS; mypy + ruff clean.

- [ ] **Step 5: Commit**

```bash
git add app/worker/handlers/ai.py tests/worker/test_records_ai_fill_handler.py
git commit -m "feat(ai): business.{kind}.ai_fill worker drafts records for empty kinds"
```

---

### Task 3: Live e2e + FE guide + SOP + checklist

**Files:**
- Create: `e2e/test_records_ai_fill.py`
- Modify: `docs/fe-integration-guide-ai-canvas-fill.md` (add a records section), `docs/checklist/PROJECT_CHECKLIST.md`
- Create: `docs/sop/2026-09-19-records-ai-fill.md`

**Interfaces:** consumes everything above; drives the shipped `POST /business-builder/{kind}/ai-fill` endpoint + in-process drain.

- [ ] **Step 1: Write the e2e journey**

```python
# e2e/test_records_ai_fill.py
"""Live: business.{kind}.ai_fill drafts records for an empty kind (stub LLM)."""
import httpx


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _drain() -> None:
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import ai as _ai  # noqa: F401  (registers business.{kind}.ai_fill)

    db = SessionLocal()
    try:
        for _ in range(500):
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def test_records_ai_fill_personas(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth(access)
        wh = _onboard(c, auth)  # mirror e2e/test_assessment.py -> {**auth, "X-Workspace-Id": <startup_id>}

        fill = c.post("/api/v1/business-builder/personas/ai-fill", headers=wh)
        assert fill.status_code == 202, fill.text
        capture("records_ai_fill", "personas_ai_fill_enqueued", fill)

        _drain()

        got = c.get("/api/v1/business-builder/personas", headers=wh)
        assert got.status_code == 200, got.text
        records = got.json()["data"]  # confirm exact path/shape from the real body
        assert any("[stub-llm]" in r["data"]["name"] for r in records)
        capture("records_ai_fill", "personas_after_fill", got)
```
> Implementer note: fill `_onboard` by mirroring `e2e/test_assessment.py` (`/auth/me` user id at `data.user.id`, workspace at `data.active_workspace_id`). Confirm the personas GET route + exact JSON path/shape (`data` list of records, each with `.data.name`) from a real response — read the capture, don't guess. Bounded timeout.

- [ ] **Step 2: Run the e2e suite**

Run: `./scripts/e2e_run.sh`
Expected: pass incl. the new journey; captures under `e2e/_captures/records_ai_fill/`.

- [ ] **Step 3: Docs**

- `docs/fe-integration-guide-ai-canvas-fill.md`: add a "Records ai-fill" section — `POST /business-builder/{kind}/ai-fill` (persona/revenue-streams/competitors/pricing; 202 `{job_id, status}`) now completes async; an **empty** kind gets up to 3 AI-drafted records; a kind that already has records is a **no-op** (v1 seeds empty lists, doesn't top up/overwrite). Poll `GET /jobs/{id}` or re-fetch `GET /business-builder/{kind}`. Paste the captured 202 + list bodies VERBATIM.
- `docs/sop/2026-09-19-records-ai-fill.md` (match existing SOP style): what shipped (records ai_fill worker, closes the last unconsumed ai_fill jobs), why, how (recursive structured stub; strict per-kind schema; fill-empties, up to 3; validate-and-skip via `create_record`; reuses `business.artifact.completed`), files/config (no migration), verification (unit + e2e), deferred (top-up/replace modes; remaining Module 03 consumers).
- `docs/checklist/PROJECT_CHECKLIST.md`: under Module 03 / Module 08, mark the records `business.{kind}.ai_fill` worker shipped (2026-09-19) — the last unconsumed ai_fill job now has a worker. Module 03 stays open (mission/health/dashboard/onboarding/roadmap consumers remain). Keep counts honest.

- [ ] **Step 4: Full local CI reproduction**

Run:
```
poetry run black --check app tests e2e && poetry run isort --check-only app tests e2e && poetry run ruff check app tests e2e
poetry run mypy app && poetry run pylint app --fail-under=9.5 && poetry run bandit -r app/ --quiet
poetry run pytest --cov=app --cov-fail-under=95 -q
poetry run alembic heads   # exactly one, UNCHANGED from develop (no migration)
./scripts/e2e_run.sh
```
Expected: all green; one head. After a green e2e, `git checkout -- e2e/_captures/` for everything EXCEPT the new `e2e/_captures/records_ai_fill/`.

- [ ] **Step 5: Commit**

```bash
git add e2e/ docs/
git commit -m "test(ai): records ai_fill live e2e + FE guide + SOP + checklist"
```

---

## Self-Review

**Spec coverage:**
- Stub recursion (nested objects/arrays/enums/numbers) → Task 1. ✓
- `record_json_schema(kind)` strict per kind (map_x/map_y omitted) → Task 1. ✓
- `handle_record_ai_fill` (4 types, fill-empties, up to 3, validate-and-skip via `create_record`, no-commit, fail-loud) → Task 2. ✓
- Reuses `business.artifact.completed` (no new notification) → inherent to `create_record`. ✓
- No migration → Global Constraints + Task 3 Step 4. ✓
- Tests (stub/schema/prompt + handler + e2e), DB-clean → Tasks 1–3. ✓
- FE guide (records section) + SOP + checklist → Task 3. ✓

**Placeholder scan:** the e2e `_onboard` delegates to mirroring `e2e/test_assessment.py`; the personas GET path is "read from the capture." Everything else is real code.

**Type consistency:** `StubLLMClient._stub_value(node, key)`; `record_json_schema(kind: RecordKind) -> dict`; `build_record_fill_messages(kind, *, name, industry, stage) -> list[LLMMessage]`; `handle_record_ai_fill(db, job)`; job types `business.{kind}.ai_fill`; `create_record(db, startup, kind, data)`; stub markers `[stub-llm]` — consistent across Tasks 1–3. ✓
