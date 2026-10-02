# FE Integration Guide — AI Co-Founder (Module 03): Consolidated Index

> **What this document is.** An **index**, not a payload guide. The AI Co-Founder shipped as a
> set of background AI-enrichment features, each embedded in a different product surface, so each
> one is documented in that surface's own FE guide. This file maps the whole Co-Founder in one
> place and points you to the authoritative section for each piece. **Every request/response body
> lives in the linked guide and was captured against the running app there** — this index
> deliberately contains no payloads, so there is nothing here to drift out of date against the
> captures.
>
> **Provenance of the two load-bearing claims in this index** (everything else is a pointer):
> - The "documented in" links were each opened and the named section confirmed present (not
>   assumed from the filename).
> - The "interactive chat is NOT built" claim (§3) was verified against the live router: the
>   `/ai` router exposes only `GET /ai/status` — there is no conversation/message endpoint.

---

## 0. The one thing the FE must know

"AI Co-Founder" in the product today is **not a chatbot you send messages to.** It is a brand
applied to ~8 places where the backend quietly upgrades otherwise-templated content into
AI-personalized content, plus one status endpoint. There is **no two-way conversation endpoint**
(see §3). If your design calls for the `/app/ai` chat drawer from the PRD, that backend does not
exist yet — do not wire a chat UI to a live endpoint expecting it to answer.

Everything that *is* built follows one of two async shapes, described in §1.

---

## 1. The two async patterns (shared across every Co-Founder feature)

Nothing the Co-Founder does is instant-with-the-final-answer. Each feature either returns a
**templated placeholder first** and upgrades it, or returns **202 and makes you poll**. Learn
these two once and every row in §2 is familiar.

**Pattern A — "templated now, AI shortly after" (no new endpoint, no 202).**
The triggering call (completing an assessment, saving onboarding state, loading the dashboard)
returns a fast, deterministic **templated** value immediately, and enqueues a background job. The
FE shows the templated value right away, then **re-fetches the same read endpoint a few seconds
later** (or on the next screen visit) to pick up the AI version. There is no push and usually no
explicit status flag — you detect the upgrade by the field's content changing, except the
dashboard briefing, which carries an explicit `generating → complete` status.

**Pattern B — "202 + poll".**
The trigger returns **202** with a `status: "generating"` (or an equivalent), and the FE polls a
read endpoint until `status === "complete"` (or `"failed"`). Used by the business plan generator
and the canvas/records AI-fill.

**When the AI silently doesn't upgrade:** it may be budget exhaustion, not a bug. The workspace's
AI budget state is exposed by `GET /ai/status` (`over_budget`) — see
[ai-status](fe-integration-guide-ai-status.md). On `over_budget`, Pattern-A features keep the
templated value and Pattern-B features resolve to `failed`. Surface a graceful "AI unavailable
right now" rather than polling forever.

---

## 2. The Co-Founder, surface by surface

Each row is one shipped Co-Founder feature (Module 03 slice or the §08.11 generator built on the
same seam). Read the linked guide/section for the real bodies, status codes, and error shapes.

| # | Co-Founder feature | Where it appears for the user | Endpoint(s) the FE calls | Async job | Pattern | Documented in |
|---|---|---|---|---|---|---|
| 1 | **Assessment narrative** | Assessment results screen — the written read of their business | `POST /assessments/{id}/complete` (templated), re-fetch `GET /assessments/{id}` (AI) | `ai.assessment.narrative` | A | [ai-assessment-narrative](fe-integration-guide-ai-assessment-narrative.md) — §1 nesting trap, §2 captures |
| 2 | **Canvas AI-fill** | Business Builder canvases — "fill this for me" | `POST /business-builder/canvases/{type}/ai-fill`, then poll job or re-fetch `GET /canvases/{type}` | `business.canvas.ai_fill` | B | [ai-canvas-fill](fe-integration-guide-ai-canvas-fill.md) — §1–§4 |
| 3 | **Records AI-fill** | Business Builder typed records (persona / revenue_stream / competitor / pricing) | `POST /business-builder/{kind}/ai-fill`, then re-fetch the kind's list | `business.{kind}.ai_fill` | B | [ai-canvas-fill](fe-integration-guide-ai-canvas-fill.md) — §5 (records) |
| 4 | **Business plan generator** (§08.11) | Business Builder → full plan document | `POST /business-builder/plan/generate` → **202**, poll `GET /business-builder/plan`, then `GET /documents/{document_id}` for content | `business.plan.generate` | B | [ai-business-plan](fe-integration-guide-ai-business-plan.md) — §0–§1 |
| 5 | **Mission reason** | Today's Mission — why each task matters today | re-fetch `GET /missions/today` → `reason` | `ai.mission.reason` | A | [mission](fe-integration-guide-mission.md) — §1, `reason` subsection |
| 6 | **Health recommendations** | Health Score — the recommendation `body` copy | re-fetch `GET /health-score` → `recommendations[].body` | `ai.health.recommendations` | A | [health-score](fe-integration-guide-health-score.md) — §5, `body` subsection |
| 7 | **Dashboard daily briefing** | Founder Dashboard — "Your AI Briefing" (briefing / risks / opportunities) | re-fetch `GET /dashboard/summary` → `briefing`/`risks`/`opportunities` (explicit `generating → complete`) | `ai.dashboard.briefing` | A (with status flag) | [dashboard](fe-integration-guide-dashboard.md) — "AI daily briefing" section |
| 8 | **Roadmap re-plan rationale** | Roadmap re-plan — the AI-authored explanation of the new plan | `POST /roadmap/replan/preview` · `POST /roadmap/replan/apply` · `GET /roadmap/replan/history` → `rationale` | `ai.roadmap.rationale` | A | [roadmap](fe-integration-guide-roadmap.md) — §9 "AI Re-plan", `rationale` subsection |
| 9 | **Onboarding AI panel** | Onboarding — the one-time personalized welcome panel | re-fetch `GET /onboarding/state` → `ai_panel` | `ai.onboarding.panel` | A | [onboarding-ai-panel](fe-integration-guide-onboarding-ai-panel.md) — §1–§2 |
| — | **AI budget / status** | Any AI surface — "is AI available / how many credits left" | `GET /ai/status` | (read-only) | — | [ai-status](fe-integration-guide-ai-status.md) — §2–§3 |

> Business Builder suggestions / positioning map (`POST /business-builder/suggestions`, …) are a
> related Builder feature but are **Module 08**, not an AI Co-Founder job — documented in
> [business-builder-suggestions](fe-integration-guide-business-builder-suggestions.md). Listed here
> only so you don't confuse it with #2/#3.

---

## 3. ⚠️ NOT built — the interactive chat (`/app/ai` drawer)

The PRD's headline for Module 03 is a conversational assistant: the floating "Ask your AI
Co-Founder" dock, two-way chat, specialist routing ("Finance Advisor via Co-Founder"), the
"Why I said this" expander, and action chips ("Draft the document", "Add as task"). **None of
that has a backend.**

- The `/ai` router exposes **only `GET /ai/status`** — there is no `POST` to send a message, no
  conversation/thread resource, no streaming endpoint.
- This was a deliberate scope decision when Module 03 was marked complete: the onboarding panel
  is "a single one-time greeting, not a two-way conversation."

**FE consequence:** do not ship the chat drawer against a live endpoint — there is nothing to call.
If/when the chat is built it will be a new module-sized effort (conversation storage, specialist
routing, streaming, action-chip tool results) and will get its own FE guide. Until then, treat the
AI Co-Founder as the enrichment features in §2.

---

## 4. Verification table

| Claim in this index | Verified how |
|---|---|
| Each §2 feature's bodies/status codes/errors are real | In the linked guide — every one was captured live against the running app (see each guide's own verification table) |
| The "documented in" section exists in each linked guide | Each target section was opened and confirmed present (not inferred from filename) |
| Interactive chat is not built (§3) | Live `/ai` router inspected — only `GET /ai/status` is registered; no conversation/message endpoint exists |
| The §2 endpoint paths and async job names | Taken from the linked guides and the module build record; the FE should still treat the linked guide as the source of truth for exact shapes |

> This index re-derives no payloads; it is safe to trust exactly as far as the guides it links. If
> a linked guide changes, update the one-line description here in the same pass.
