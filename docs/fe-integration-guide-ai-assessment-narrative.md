# FE Integration Guide — AI Assessment Narrative (Module 03 Slice 1)

All request/response bodies below are pasted **verbatim** from live captures taken by
`e2e/test_ai_assessment_narrative.py::test_assessment_narrative_is_ai_rewritten` running against a
real server (`scripts/e2e_run.sh`) — see `e2e/_captures/ai_assessment_narrative/*.json`. Nothing
here is retyped from the schema, the service, or memory. IDs in the examples are real values from
that ephemeral test run (they differ on every real request; the shapes are exact).

This is **not a new endpoint or a new field.** `AssessmentResult.narrative` already existed
(Module 07) and both routes below already returned it. What changed is *when its final value is
available* — see §1.

---

## 0. The one thing the FE must know

`AssessmentResult.narrative` is now written **twice**:

1. **At completion (`POST /assessments/{id}/complete`)** — a fast, synchronous, **templated**
   narrative (e.g. `"Your strongest area is legal (100). Focus next on money (71)."`), computed
   from the dimension scores with no external call. The founder sees this the instant they finish
   the assessment.
2. **Seconds later, asynchronously** — a background job (`ai.assessment.narrative`) calls the
   configured LLM and **overwrites** the same `narrative` field with an AI-generated version (2–3
   short paragraphs). This does NOT happen on the request that returns step 1 — it happens on a
   later worker poll, off the request/response cycle entirely.
3. **If the AI job fails** (LLM timeout, non-2xx, bad key, etc.), the narrative **silently stays
   templated** — the job's own retry/backoff (worker-level, exhausts after `WORKER_MAX_ATTEMPTS`)
   is the only recovery path; there is no user-visible error and no field indicating "AI enrichment
   failed" or "still templated." **The templated narrative from step 1 is the permanent fallback,
   not just a loading state with a guaranteed upgrade.**

**Consequence for the FE:** do not treat the narrative returned by the completion response as
final. If you display it immediately (recommended — it's real content, not a placeholder), **re-fetch
`GET /assessments/{id}` after a short delay (or on next screen visit) to pick up the upgraded
version if one landed.** There is no webhook/push for this — see §3 for a suggested approach. There
is also no field telling you *which* version (templated vs AI) you're currently looking at — the
two are indistinguishable by shape, only by content.

---

## 1. Field-nesting trap: the SAME field, TWO different response shapes

This is the most likely source of an integration bug, so it is called out on its own before any
example:

| Endpoint | Where `narrative` lives |
|---|---|
| `POST /api/v1/assessments/{id}/complete` | **flat**, directly under `data` → `data.narrative` |
| `GET /api/v1/assessments/{id}` | **nested** one level deeper, under `data.result` → `data.result.narrative` |

Both are confirmed live below (§2). This asymmetry is pre-existing (Module 07's original response
shapes — `complete_assessment`'s return dict vs. `get_assessment`'s response builder are two
different code paths that were never unified) and is **not new to this slice** — it just now
matters more, because the FE must re-fetch `GET /assessments/{id}` to see the AI upgrade, and a
copy-pasted `data.narrative` accessor from the completion handler will read `undefined` there.

`data.result` is `null` when an assessment has no result yet (i.e., not completed) — guard for that
before reading `.narrative` off it, same as every other field under `result`
(`dimension_scores`/`overall_provisional`).

---

## 2. Live captures

### 2a. `POST /api/v1/assessments/{id}/complete` — the templated narrative, at completion

`e2e/_captures/ai_assessment_narrative/complete_templated.json`:

```json
{
  "data": {
    "assessment_id": "a5022b58-f3bb-4a81-891f-1a4f67d27e8d",
    "status": "completed",
    "dimension_scores": {
      "product": 78,
      "market": 75,
      "money": 71,
      "legal": 100,
      "team": 75
    },
    "overall_provisional": 80,
    "narrative": "Your strongest area is legal (100). Focus next on money (71)."
  },
  "meta": null
}
```

### 2b. `GET /api/v1/assessments/{id}` — after the worker drains the `ai.assessment.narrative` job

`e2e/_captures/ai_assessment_narrative/result_ai_narrative.json` (same assessment id, fetched
after the background job ran):

```json
{
  "data": {
    "id": "a5022b58-f3bb-4a81-891f-1a4f67d27e8d",
    "type": "initial",
    "status": "completed",
    "answers_by_dimension": {
      "product": [
        { "question_key": "product_stage", "value": "live" },
        { "question_key": "product_confidence", "value": 3 }
      ],
      "market": [
        { "question_key": "market_clarity", "value": 3 },
        { "question_key": "market_research", "value": "deep" }
      ],
      "money": [
        { "question_key": "has_revenue", "value": "yes" },
        { "question_key": "mrr", "value": 1000 },
        { "question_key": "runway_confidence", "value": 3 }
      ],
      "legal": [
        { "question_key": "incorporated", "value": "yes" },
        { "question_key": "ip_assigned", "value": "yes" }
      ],
      "team": [
        { "question_key": "team_size", "value": "team" },
        { "question_key": "team_confidence", "value": 3 }
      ]
    },
    "result": {
      "dimension_scores": {
        "team": 75,
        "legal": 100,
        "money": 71,
        "market": 75,
        "product": 78
      },
      "overall_provisional": 80,
      "narrative": "[stub-llm] AI-generated assessment narrative."
    }
  },
  "meta": null
}
```

Note: `"[stub-llm] AI-generated assessment narrative."` is the fixed, deterministic output of the
`StubLLMClient` used by this e2e run (`LLM_PROVIDER=stub` — no real LLM call, no network, no API
key). In a real deployment with `LLM_PROVIDER=openai`, this field instead holds a real 2–3 paragraph
narrative from the configured model (`LLM_MODEL`, default `gpt-5.6-luna`). The FE should not assume
any particular prefix or length — only that it replaces the templated string from §2a once the job
succeeds.

---

## 3. Suggested FE handling

- **Render the completion response's `data.narrative` immediately** — it is real, readable content,
  not a spinner placeholder. Do not block the completion screen on the AI upgrade.
- **On the assessment's detail/results screen** (wherever `GET /assessments/{id}` is called, e.g.
  the founder revisits their score later), **always render `data.result.narrative` from that fresh
  fetch** rather than caching the completion response's narrative — this is how the AI upgrade
  actually reaches the user, with zero extra API surface.
- **No polling loop is required or recommended for this specifically.** Unlike Module 20's
  notification feed (which has no server-pushed events and genuinely needs a poll cadence), the
  narrative upgrade is a "reads current value on next natural fetch" pattern — the worker typically
  drains a job within a few seconds of `complete_assessment` enqueuing it, and the assessment
  results screen is not a real-time surface. If product wants a "researching your results..."
  transient state on the completion screen itself, that's a UX decision the backend does not gate —
  there is no field to key it off (see §0); the FE would need its own short client-side timer, not
  a server signal.
- **Never diff the two narrative strings to detect "did AI enrichment happen."** The templated and
  AI text can coincidentally look similar in structure; there is no reliable client-side signal.
  If a future slice needs "is this AI-enriched?" as a real product signal, that needs a new
  server-side boolean field — out of scope for this slice, not simulable from the client.

---

## 4. Verification table

| Behaviour | Verified live? | Source |
|---|---|---|
| `POST /assessments/{id}/complete` returns the templated narrative flat under `data.narrative` | ✅ | `complete_templated.json` |
| `ai.assessment.narrative` job enqueued on every assessment completion (alongside `roadmap.replan`) | ✅ (indirectly — the job is what produces §2b's result) | `complete_templated.json` → `result_ai_narrative.json` pair, same `assessment_id` |
| Draining the worker in-process runs the job and overwrites `AssessmentResult.narrative` with the LLM (stub) output | ✅ | `result_ai_narrative.json`, `narrative.startswith("[stub-llm]")` assertion in `e2e/test_ai_assessment_narrative.py` |
| `GET /assessments/{id}` nests the (upgraded) narrative under `data.result.narrative`, not flat | ✅ | `result_ai_narrative.json` |
| The narrative stays templated forever if the AI job fails (fail-loud client + no silent fallback write) | ⚠️ unit only — a live e2e run cannot force the configured LLM to fail without breaking the stub-provider guarantee for every other e2e test sharing the same process | `tests/worker/test_ai_handler.py::test_handler_fails_loud_when_llm_errors` (asserts `RuntimeError` propagates, leaving the runner's own retry/backoff and the templated row untouched — no code path writes a fallback string on failure) |
| A missing `AssessmentResult` (e.g. a stale/duplicate job) is a benign no-op, not an error | ⚠️ unit only — not reachable from a normal live journey | `tests/worker/test_ai_handler.py::test_handler_is_a_noop_when_result_missing` |
| No PII (answers, emails, names) is sent to the LLM — only dimension scores, overall score, industry, stage | ⚠️ unit only (prompt-construction test, not observable over HTTP) | `tests/services/assessment/test_narrative.py` |

The two ⚠️ rows are genuine gaps in this one live journey — forcing the configured LLM client to
fail, or forcing a stale job against a deleted result, are not reachable from a normal signup →
complete-assessment HTTP walk without either breaking the shared e2e process's `LLM_PROVIDER=stub`
guarantee or fabricating an orphaned job row — both are covered by passing unit tests at the cited
paths instead of an invented live example.
