# nginx sites for the self-hosted web frontend (config + runbook, not applied)

**What shipped:** templated nginx vhosts, upstream, rate-limit zones, security headers and a first-cert bootstrap site for the Next.js frontend moving off Vercel onto this VPS, plus the operator runbook `docs/deployment/WEB_FRONTEND_EDGE.md` and a local verifier. Branch `ci/fe-nginx-sites`, PR to `develop`. Companion: `cofoundaz` repo PR `ci/fe-vps-deploy` (Docker image + CD). **Nothing was applied to the VPS, DNS or any `.env`.**

**Why:** the frontend now deploys as a container on `127.0.0.1:<WEB_PORT>`; host nginx is the only path in, and this repo already owns the edge config (`deploy/nginx/`).

## How
| Decision | Reason |
|---|---|
| Templates + `render.sh`, not fixed files | Hostnames and ports are not decided yet; nothing environment-specific is committed. `render.sh` validates its inputs and fails on any unrendered placeholder |
| Own header snippet (`web-security-headers.conf`) | The API snippet's COOP `same-origin` and `no-referrer` break the Google sign-in, Paystack and Mono popups. No CSP yet (Next inline scripts need nonces first) |
| Own rate-limit zones (50 r/s, burst 200, 400 for `/_next/static/`) | A page load fans out into many asset requests; it must not spend the visitor's API budget |
| Staging `X-Robots-Tag: noindex`, production none | Keep staging out of search |
| Bootstrap :80-only site | `00-default-deny.conf` 444s unknown names, so the first webroot cert needs a site that answers ACME before the TLS site can load |
| Apex 301 → www (`WEB_REDIRECT_NAMES`) | One browser origin: one CORS entry, one cookie host, one OAuth callback. DNS for both already points here, so HTTP-01 works for the first cert |

## What's involved
`deploy/nginx/web/{web-site,web-upstream,web-bootstrap}.conf.template`, `deploy/nginx/web/{web-rate-limits.conf,render.sh}`, `deploy/nginx/web/snippets/web-security-headers.conf`, `deploy/nginx/test/verify-web-local.sh`, `deploy/nginx/README.md` (map rows), `docs/deployment/WEB_FRONTEND_EDGE.md`. Existing API vhosts and conf.d are unchanged.

## Verification (2026-11-06, local Docker, nginx 1.27-alpine)
`./deploy/nginx/test/verify-web-local.sh` with the real web image built from the `cofoundaz` Dockerfile: **28/28 pass**. It checks `nginx -t` with API + both web vhosts (no warnings besides the self-signed OCSP stapling one); for staging, prod and the prod alt name: `/healthz` 200 over TLS, `/` 200, http→301, ACME served on :80; h2; nosniff, SAMEORIGIN, strict-origin referrer, HSTS, no COOP; staging noindex and production none; apex https 301 → www keeping path and query; bootstrap sites load with no certs present; server version hidden; a `/_next/static` asset 200, immutable and gzipped; TRACE 405; backend down → 502. `shellcheck` clean. **Not verified:** real certs, renewal, and the VPS's nginx build.

## Operate / roll back
Follow the runbook, staging first, only after Adebayo's go-ahead. Roll back: remove the `sites-enabled` symlink, `nginx -t`, reload. API vhosts are unaffected.

## Follow-ups
CSP with nonces; design B (same-origin `/api/v1` on the web host); API `BACKEND_CORS_ORIGINS` / `APP_BASE_URL` and provider callbacks per new host (Adebayo, before cutover).
