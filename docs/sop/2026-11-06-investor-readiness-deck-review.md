# Investor Readiness: pitch deck review (slice B)

**What shipped:** PRD 15.2. A founder uploads a pitch deck PDF through Documents, queues a review, and gets Narrative, Data clarity and Design scores (/10), the top five fixes, a note and a thumbnail per slide, and a version history. The review feeds the Story dimension and meets the "reviewed pitch deck" must-have of the 69 cap. API only; FE guide section 12 of `docs/fe-integration-guide-investor-readiness.md`. Branch `feature/readiness-deck-review`, PR: see the branch. Migration **`0091_readiness_deck_reviews`**.

**Migration chain:** written on `0089_readiness_scores` (develop's head) because Funding's `0090` was not merged. If `0090` merges first, change `down_revision` in `alembic/versions/0091_readiness_deck_reviews.py` to Funding's revision id; nothing else changes.

**Why:** the score page had a "Deck" stub and the D-IR-2 cap needs a real "reviewed deck". Decisions (D-IR-1..7, accepted 2026-11-05): D-IR-5 text plus layout heuristics, no vision; founder starts reviews, team member reads, other roles 403; deck review is AI enrichment so the daily token budget applies.

**How**

| Topic | Decision |
|---|---|
| Upload | Reuses `POST /documents/files` (PDF magic-byte check, 15 MB cap, malware scan, private storage). `POST /readiness/deck-reviews {file_id}` then references a clean, PDF, live (not trashed) file of the workspace. No second upload path. |
| Dedupe and versions | `content_sha256` of the stored bytes. The same bytes with a non-failed review return that review (HTTP 200, `deduplicated: true`) and queue nothing; a failed review can be retried. `version` = reviews per workspace, assigned under a `pg_advisory_xact_lock`. |
| Job | `ai.readiness.deck_review` (the `ai.` prefix is what puts it under the 100k/day budget). Extract per page with `pypdf` (first 30 pages), layout check, one `metered_complete_json` call (about 10k tokens, max output 2500), thumbnails. Provider errors re-raise until the last attempt, then the review is `failed: generation_failed`. The job itself ends `succeeded` with a failed review, so the FE reads the review. |
| Paid-for work is never undone | The job runs in the runner's savepoint and the token debit is inside it, so anything that raises after the model call would roll the debit back and the retry would spend the tokens again. Everything after the model call is therefore best effort: each thumbnail save, and the `readiness.deck.reviewed` publish, log and carry on (a review without thumbnails is a valid review). |
| Dead jobs | A queued or running review counts as a duplicate only while its job is queued or running. `reconcile()` marks a review whose job failed, was cancelled or vanished as `failed` with `error: job_failed` (on create, on read and on list), so it neither shows "queued" for ever nor blocks re-uploading the same file. |
| Worker safety | `app/worker/runner.py:reap_stale` no longer re-queues a stale `running` job that has already used `WORKER_MAX_ATTEMPTS` claims: it is finalised as `failed` ("worker stopped before the job finished"). This matters because deck review is the first job to parse untrusted PDFs in the worker: a PDF that kills the process cannot loop for ever. |
| Failure reasons | `no_text` (under 200 characters of text: scanned deck), `unreadable`, `file_missing`, `over_budget`, `generation_failed`, `job_failed` (the job died or exhausted its retries without finishing; set when the review is read). `POST` itself returns 429 `AI_BUDGET_EXHAUSTED` when the allowance is already spent (after the dedupe check, so a repeat never fails on budget). |
| Design score | D-IR-5: **a layout check in code**, not a visual judgement (the client is text-only). Starts at 10; minus for under 6 or over 20 or 25 slides, average words per slide, share of dense slides, share of slides with images, share of near-empty slides. Facts are returned under `scores.layout`. |
| Prompt injection | Deck text goes to the model inside `<deck>` tags with a system instruction that it is untrusted data; a pasted `</deck>` is stripped; per-slide and total length are capped. Every model output is validated: scores clamped to 1..10, fixes cut to five 300-character strings, notes keyed by real slide numbers and cut to 400, so a hostile deck cannot inject structure. |
| Thumbnails | `pypdfium2` (new dependency, pure wheel; Pillow is now declared explicitly) renders PNG with the longest side capped at 480 px (scale computed from the page size; a page declaring more than 14400 points is skipped, so a hostile MediaBox cannot force a huge allocation), once per review, stored with `save_private` under `readiness/{startup}/{review}/{n}.png`. Reads return a signed link: the provider's signed URL on Cloudinary, or a 15 minute `sign_key` link served by `GET /readiness/deck-thumbnails?token=` locally (rate limited, key must start with the workspace's own prefix). A render failure leaves `thumbnail_url: null`; it never fails the review. |
| Score wiring | New signals `story.pitch_deck` (a pitch-deck document or any reviewed deck) and `story.deck_review` (latest review's `overall`, target 6/6/6/7/7/8 by stage, weight 2, 0 when none). The must-have `pitch_deck_reviewed` is met by a finished review. `readiness.deck.reviewed` refreshes the score immediately (it bypasses the 15 minute throttle, because the founder is waiting) and is registered in the worker process too. History `log` lists finished reviews and `markers` carries "Deck reviewed (vN)". |

**What's involved**

| Area | Paths |
|---|---|
| Model, migration | `app/db/models/readiness.py` (`DeckReview`), `alembic/versions/0091_readiness_deck_reviews.py` |
| Service, job | `app/services/readiness/deck.py`, `app/worker/handlers/readiness.py` (registered in `app/worker/__main__.py`, which also now registers the readiness event triggers in the worker) |
| API | `app/api/v1/endpoints/readiness.py`, `app/schemas/readiness.py`: `POST`/`GET /readiness/deck-reviews`, `GET /readiness/deck-reviews/{id}`, `GET /readiness/deck-thumbnails` |
| Score | `app/services/readiness/signals.py`, `triggers.py`, `service.py` (history log) |
| Worker | `app/worker/runner.py` (`reap_stale`) |
| Dependencies | `pypdfium2`, `pillow` (`pyproject.toml`, `poetry.lock`) |

**Verification:** `tests/services/readiness/test_deck.py` (layout rules, cleaning and clamping, prompt fencing, extraction, thumbnails, create guards, run paths, retries, score wiring), `tests/api/test_readiness_deck_api.py` (all 7 roles on create and read, the full queue to thumbnail flow, tampered, foreign and expired tokens, budget 429, validation, tenancy), migration round trip (`tests/test_readiness_migration.py`, down to 0089, to 0088 and back), live journey `e2e/test_readiness_deck_review.py` against a real server with the job drained in-process **and a canned language model** (captures in `e2e/_captures/readiness_deck/`). Also covered: a storage error after the model call keeps the review ready with the tokens counted once; a failing event subscriber does not undo it; a dead job does not lock the deck; `reap_stale` fails a job that used all its attempts. Not verified: a real model's output or token cost; Cloudinary thumbnails; the real malware scanner path (`FILE_NOT_CLEAN` is unit-tested).

**Operate / roll back:** needs no new env var. The worker must run the new handler (it is registered in `app/worker/__main__.py`). Roll back by downgrading `0091` (drops `deck_reviews`; stored thumbnails stay in storage under `readiness/`) and reverting the PR; without the table the score's `_deck_reviewed` reads would fail, so revert both together.

**Follow-ups**

- Mock Q&A (slice D) and Narrative (slice C) reuse the deck text; narrative claim extraction can read `deck_reviews.slides` words later, or re-extract from the file.
- Real-model run to replace the 10k token estimate with a measured one, and to check that a model never returns a score outside 1..10 in practice (clamped anyway).
- Orphan thumbnails: deleting a review is not offered, so none are orphaned today; a purge is needed if reviews become deletable.
- Layout check weights are a first guess; tune after real decks.
