# SOP — Document share/signature emails point at the FE origin, not the API (Module 18 fix)

**What shipped** — a bug fix: the document **share** and **e-signature** emails (Module 18,
Slices 3 & 4) now build their clickable links off the **frontend** origin (`APP_BASE_URL`) instead
of the **API** origin (`SERVER_HOST`). A founder who shares a document, or sends one for signature,
now receives a link that opens the FE app's `/shared/:token` or `/sign/:token` page — not a raw
API route that 404s.

Branch `fix/document-emails-fe-links`, off `develop` @ `69bba1d`. PR into `develop`.

## Why

`app/api/v1/endpoints/documents.py` built all three tokenized-link emails as
`f"{settings.SERVER_HOST}/shared/{raw}"` / `f"{settings.SERVER_HOST}/sign/{raw}"`. `SERVER_HOST` is
the **API** origin — on staging `https://staging-api.cofoundaz.com`, on prod `https://api.cofoundaz.com`.
A real staging share email was observed linking to
`https://staging-api.cofoundaz.com/shared/iKvkqc17...`, which is wrong on two counts:

1. **Wrong origin** — `/shared/:token` and `/sign/:token` are meant to be **frontend** pages that
   the recipient's browser renders, then call the API from. Pointing at the API host lands the
   recipient on the backend, not the app.
2. **Missing `/api/v1` prefix** — even as an API URL it 404s, because the real endpoint is
   `GET /api/v1/shared/{token}`. So the link was doubly broken.

The auth emails (verify / reset) already solved exactly this: `app/services/auth/emails.py::_base_url`
builds off `(APP_BASE_URL or SERVER_HOST)`. The document emails simply hadn't adopted that pattern.

## How

**Mirror the auth-email base pattern, one small helper per module.** Added
`_fe_base_url()` to `app/api/v1/endpoints/documents.py`:

```python
def _fe_base_url() -> str:
    return (settings.APP_BASE_URL or settings.SERVER_HOST).rstrip("/")
```

— identical resolution to `auth/emails.py::_base_url` and `worker/handlers/email.py::deep_link`.
The three links now read `f"{_fe_base_url()}/shared/{raw}"` / `f"{_fe_base_url()}/sign/{raw}"`.
A single module-local helper (rather than a shared util) matches how auth does it and keeps the
three call sites from repeating the base expression. **`SERVER_HOST` semantics are unchanged** — it
is still the API origin and still the fallback when `APP_BASE_URL` is unset (e.g. local dev).

**`APP_BASE_URL` is a required deploy var.** It already exists (`config.py:50`, defaults to `""`)
and is already set on staging/prod to the FE origin per prior auth-email work. If it were ever left
blank, the links silently fall back to `SERVER_HOST` and the bug returns — so this is documented as
a required (not optional) env var in both FE guides.

**Alternatives rejected:** (a) changing `SERVER_HOST` to the FE origin — would break every other
`SERVER_HOST` consumer that legitimately wants the API origin; (b) hardcoding the `/api/v1` prefix
into the link — wrong direction, these are FE routes, not API routes.

## What's involved

| Area | Path | Change |
|---|---|---|
| Fix | `app/api/v1/endpoints/documents.py` | new `_fe_base_url()`; 3 links (share create ~L284, signature create ~L372, remind ~L399) now use it |
| Unit tests | `tests/api/test_document_shares.py` | +2: FE-origin link, `SERVER_HOST` fallback (trailing-slash trim) |
| Unit tests | `tests/api/test_signatures.py` | +2: signer_links FE-origin, remind-email FE-origin (via recording sender) |
| e2e | `e2e/test_documents.py` | sharing + esignature journeys assert the emailed link uses the FE origin; capture the delivered email bodies |
| e2e harness | `e2e/conftest.py` | new `capture_json` fixture (writes a decoded object, e.g. an email body) |
| e2e runner | `scripts/e2e_run.sh` | exports `APP_BASE_URL` (default `http://localhost:3000`) so captures prove the FE origin |
| Captures | `e2e/_captures/documents/{share_email,signature_email}.json` (new) + refreshed `share_*`/`signature_*` | verbatim source for the FE guides |
| FE guides | `docs/fe-integration-guide-documents-sharing.md` (§1, §5, §6, §8), `...-esignature.md` (§0, §1, §3–§4, §9, §13) | link now `{APP_BASE_URL}/...`; the FE routes to build; verification rows |

## Verification

- **Unit (TDD):** wrote the 4 assertions first, watched them fail against the old `SERVER_HOST`
  links (`http://localhost/...`), then implemented the helper. `tests/api/test_document_shares.py`
  + `tests/api/test_signatures.py` — **20 passed**. Full suite green at **≥95%** coverage (CI gate).
- **e2e (live, file email backend):** ran `scripts/e2e_run.sh`-equivalent against real
  uvicorn + Postgres + Redis with `APP_BASE_URL=http://localhost:3000` — **42 passed**. The
  captured `share_email.json` / `signature_email.json` bodies, and the `link`/`signer_links` in the
  create captures, all show `http://localhost:3000/...` (the FE origin), and the emailed link equals
  the create-response link.
- Full local CI reproduction (black/isort/ruff/mypy/pylint≥9.5/bandit/pytest≥95%/alembic single
  head/e2e) run green before push.

## Operate / roll back

- **Operate:** no migration, no new env var. `APP_BASE_URL` must be the FE origin in
  staging/prod (already is). Local dev with `APP_BASE_URL` unset falls back to `SERVER_HOST` — links
  will point at the API origin locally, which is expected and harmless (no real recipient).
- **Roll back:** revert the PR; nothing stateful changed.

## Follow-ups

- The FE must build the `/shared/:token` and `/sign/:token` pages (see the two FE guides). Until it
  does, a staging link 200s with raw JSON from the public API endpoints rather than a rendered page —
  the origin is now correct, the page is the FE's to add.
- `e2e/test_documents.py`'s sharing/esignature journeys read the local mail dir directly (module
  helpers, not the `mailbox` fixture), so they are not deselected under `E2E_REMOTE=1` yet would
  need a local mail dir to pass remotely — pre-existing, unchanged by this fix.
