# FE Integration Guide — Onboarding AI Panel (Module 03, AI-consumer set closed)

All request/response bodies below are pasted **verbatim** from live captures taken by
`e2e/test_onboarding_ai_panel.py::test_onboarding_ai_panel` running against a real server
(`scripts/e2e_run.sh`) — see `e2e/_captures/onboarding_ai_panel/*.json`. Nothing here is retyped
from the schema, the service, or memory. IDs in the examples are real values from that ephemeral
test run (they differ on every real request; the shapes are exact).

This is **not a new endpoint.** `GET /onboarding/state` and `PATCH /onboarding/state` already
existed (Module 01.6). Both routes now return one new field, `ai_panel`, because both call the
same `serialize_state` helper.

---

## 0. The one thing the FE must know

`data.ai_panel` (string | null) is a short, first-person calibration message from the "AI
co-founder" — 2–3 sentences that reflect back what it understands about the startup and set
expectations for how it will help. It goes through up to three states over the founder's
onboarding session:

1. **`null`** — while the founder's industry, stage, and goals are not *all* set yet (steps 1–3
   of onboarding), and permanently on any pre-existing workspace that finished onboarding before
   this field existed (no backfill). There is nothing to render.
2. **Templated, instantly** — the moment a `PATCH /onboarding/state` call completes the last of
   the three signals (industry + stage + goals all present), the backend writes a deterministic
   templated message **synchronously**, in the same request that completed the signals. The very
   next `GET`/`PATCH` response already has a non-null `ai_panel`.
3. **AI-authored, seconds later** — that same request also enqueues a background job
   (`ai.onboarding.panel`) that calls the configured LLM and **overwrites** `ai_panel` with a
   generated version. This does not happen on the request that returns step 2 — it happens on a
   later worker poll, off the request/response cycle entirely. **This is a one-shot upgrade**:
   once `ai_panel` is non-null, it is never regenerated again (there's no "recalibrate" trigger
   today — see Follow-ups in the SOP).
4. **If the AI job fails** (LLM timeout, non-2xx, bad key, etc.), `ai_panel` **silently stays on
   the templated value forever** — same permanent-fallback pattern as every other Module 03 AI
   consumer (assessment narrative, mission reason, health recommendations, dashboard briefing,
   roadmap rationale). There is no field indicating "AI enrichment failed" or "still templated."

**Consequence for the FE:** render `ai_panel` opaquely as prose (a message bubble / calibration
card) whenever it is non-null; render nothing (or a neutral placeholder) while it is null. Because
the templated value already appears synchronously, there's no need to poll immediately after the
signals-completing `PATCH` — but if you want to show the eventual AI-upgraded copy rather than the
templated one, **re-fetch `GET /onboarding/state`** a few seconds later (e.g. on next screen visit,
or a single delayed re-fetch) — same "reads current value on next natural fetch" pattern as the
assessment narrative (`docs/fe-integration-guide-ai-assessment-narrative.md`, §3), not a polling
loop. There is no field telling you *which* version (templated vs. AI) you're currently looking
at — the two are indistinguishable by shape, only by content.

---

## 1. No field-nesting trap this time

Unlike the assessment narrative (which lives at two different nesting depths on two different
endpoints), `ai_panel` is **flat under `data`** on both `GET` and `PATCH /onboarding/state` —
`data.ai_panel` — because both routes return the exact same `serialize_state(db, startup, user)`
body. One shape, no divergence to guard against.

---

## 2. Live captures

### 2a. `GET /api/v1/onboarding/state` — the templated panel, immediately after signals complete

`e2e/_captures/onboarding_ai_panel/state_templated.json`:

```json
{
  "data": {
    "step": 4,
    "completed": false,
    "assessment_pending": true,
    "founder_profile": {
      "full_name": "Ada Founder",
      "role_title": "Founder & CEO",
      "country": null,
      "phone": null,
      "how_heard": null
    },
    "startup": {
      "id": "c06bc716-dd1c-433d-93fd-7e512c2c1e8f",
      "name": "Cofoundaz",
      "description": null,
      "website": null,
      "logo_url": null,
      "industry": "Fintech",
      "business_model": "b2b",
      "stage": "idea"
    },
    "goals": [
      "Get first customers"
    ],
    "ai_panel": "Got it — a Fintech startup at the idea stage. Let's calibrate your workspace.",
    "notes": null,
    "invites": []
  },
  "meta": null
}
```

Note: `"Got it — a Fintech startup at the idea stage. Let's calibrate your workspace."` is the
deterministic templated fallback (`_templated_panel(industry, stage)` —
`app/services/onboarding/ai_panel.py`) — it is **not** a stub/placeholder marker, it's real,
readable copy the founder is meant to see immediately. It is *not* an AI output at this point in
the journey.

### 2b. `GET /api/v1/onboarding/state` — after the worker drains the `ai.onboarding.panel` job

`e2e/_captures/onboarding_ai_panel/state_after_drain.json` (same startup, fetched after the
background job ran):

```json
{
  "data": {
    "step": 4,
    "completed": false,
    "assessment_pending": true,
    "founder_profile": {
      "full_name": "Ada Founder",
      "role_title": "Founder & CEO",
      "country": null,
      "phone": null,
      "how_heard": null
    },
    "startup": {
      "id": "c06bc716-dd1c-433d-93fd-7e512c2c1e8f",
      "name": "Cofoundaz",
      "description": null,
      "website": null,
      "logo_url": null,
      "industry": "Fintech",
      "business_model": "b2b",
      "stage": "idea"
    },
    "goals": [
      "Get first customers"
    ],
    "ai_panel": "[stub-llm] AI-generated assessment narrative.",
    "notes": null,
    "invites": []
  },
  "meta": null
}
```

Note: `"[stub-llm] AI-generated assessment narrative."` is **not a bug and not specific to this
feature** — it is the `StubLLMClient` used by this e2e run (`LLM_PROVIDER=stub` — no real LLM
call, no network, no API key), and that stub returns the exact same fixed string for *every*
`complete()` call across the whole codebase (assessment narrative, roadmap rationale, and now the
onboarding panel all produce this identical marker under the stub provider — see
`app/platform/llm.py::StubLLMClient.complete`). In a real deployment with `LLM_PROVIDER=openai`,
this field instead holds a real 2–3 sentence calibration message from the configured model
(`LLM_MODEL`, default `gpt-5.6-luna`), generated from the prompt in
`build_onboarding_panel_messages` (industry/stage/goals only). The FE should not assume any
particular prefix, length, or wording — only that it replaces the templated string from §2a once
the job succeeds.

---

## 3. Suggested FE handling

- **Render `data.ai_panel` opaquely as prose** (a calibration message / welcome card) whenever it
  is non-null. Treat it as freeform text — no markdown, no structure to parse out of it.
- **Render nothing (or a neutral "calibrating..." empty state) while `ai_panel` is null.** This
  covers two distinct cases the FE cannot tell apart from this field alone: the founder hasn't yet
  supplied industry/stage/goals (steps 1–3 not done), or the workspace predates this feature and
  will never get one. Neither case is an error.
- **Do not gate the onboarding flow on `ai_panel`.** It appears the moment the three signals are
  set, which may be before or after the founder reaches step 4 depending on the exact step order
  they filled in — it is calibration copy, not a completion requirement, and `POST
  /onboarding/complete`'s own gate is unaffected by it.
- **One optional re-fetch, not a poll loop.** If you want the AI-upgraded copy instead of the
  templated one, a single delayed `GET /onboarding/state` (or picking it up naturally on the next
  screen the founder visits) is enough — same reasoning as §0.
- **Never diff the two `ai_panel` strings to detect "did AI enrichment happen."** No reliable
  client-side signal exists; see the assessment-narrative guide's §3 for the same caveat, which
  applies identically here.

---

## 4. Verification table

| Behaviour | Verified live? | Source |
|---|---|---|
| `ai_panel` is `null` before industry/stage/goals are all set | ✅ | `e2e/test_onboarding_ai_panel.py` (asserts `null` after only partial signals) |
| Completing the last signal writes the templated `ai_panel` synchronously and flat under `data.ai_panel` on both `GET`/`PATCH` | ✅ | `state_templated.json` |
| `ai.onboarding.panel` job is enqueued exactly once, gated on the write actually completing all three signals | ✅ (indirectly — the job is what produces §2b's result) | `state_templated.json` → `state_after_drain.json` pair, same `startup.id` |
| A second `PATCH` after signals are already complete does not re-enqueue or overwrite the panel | ✅ | `tests/services/onboarding/test_ai_panel_trigger.py::test_generates_and_enqueues_once_when_signals_complete` |
| Draining the worker in-process runs the job and overwrites `ai_panel` with the LLM (stub) output | ✅ | `state_after_drain.json`, `"[stub-llm]" in data["ai_panel"]` assertion in `e2e/test_onboarding_ai_panel.py` |
| The panel stays templated forever if the AI job fails (fail-loud client + no silent fallback write) | ⚠️ unit only — a live e2e run cannot force the configured LLM to fail without breaking the stub-provider guarantee for every other e2e test sharing the same process | `tests/worker/test_onboarding_panel_handler.py::test_fails_loud_on_llm_error` |
| A missing/deleted startup (e.g. a stale job) is a benign no-op, not an error | ⚠️ unit only — not reachable from a normal live journey | `tests/worker/test_onboarding_panel_handler.py::test_noop_when_startup_missing` |
| No PII (founder name, role, country, phone) is sent to the LLM — only industry, stage, goals | ⚠️ unit only (prompt-construction test, not observable over HTTP) | `tests/services/onboarding/test_ai_panel_builder.py::test_builder_is_pii_free_and_includes_signals` |

The three ⚠️ rows are genuine gaps in this one live journey — forcing the configured LLM client to
fail, forcing a stale job against a deleted startup, or observing the LLM prompt payload itself are
not reachable from a normal signup → onboard HTTP walk without either breaking the shared e2e
process's `LLM_PROVIDER=stub` guarantee or fabricating an orphaned job row — both are covered by
passing unit tests at the cited paths instead of an invented live example.
