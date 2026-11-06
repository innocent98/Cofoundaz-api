# Web frontend edge: nginx sites + TLS for the self-hosted Next.js app

**Status: config and runbook only. Nothing here has been applied to the VPS.** Run it only after Adebayo gives an explicit go-ahead. Hostnames are not decided yet, so every file is a template, and the commands below use `<STAGING_WEB_HOST>`, `<PROD_WEB_HOST>`, `<PROD_WEB_ALT_HOST>` placeholders.

## Two layouts: use `standalone` on today's VPS

**Found on 2026-11-06:** the VPS runs **Ubuntu's stock nginx 1.24.0**. The full `deploy/nginx/` layout (conf.d files, `snippets/cofoundaz/`, nginx.org ≥ 1.25.1) described in NGINX_TLS.md was **never installed** there. Installing into `/etc/nginx/snippets/cofoundaz/` failed with "Not a directory", and 1.24 rejects `http2 on;`.

| `EDGE_LAYOUT` | For | Output |
|---|---|---|
| `standalone` (**default**) | stock nginx 1.24, today's VPS | `sites-available/web-<env>.conf` with an inline upstream, rate-limit zone, ACME location, headers and proxy headers, plus `listen 443 ssl http2;`. Depends on nothing else. Also `web-<env>-bootstrap.conf` |
| `full` | once nginx.org ≥ 1.25.1 and the full layout are installed (NGINX_TLS.md §5) | the conf.d upstream, rate-limit zones, header snippet, and the site and bootstrap files that include them |

Pairs with the `cofoundaz` repo: `deploy/docker-compose.web.yml`, `deploy/web-deploy.sh`, `.github/workflows/cd-*.yml`, and `docs/sop/2026-11-06-fe-vps-deploy.md` there. API edge manual: [NGINX_TLS.md](./NGINX_TLS.md). The same rules apply: nginx ≥ 1.25.1, `certbot certonly --webroot` (never `--nginx`), `nginx -t` before `reload`, never `restart`.

## Files

| Repo path | Installs to | Notes |
|---|---|---|
| `deploy/nginx/web/web-upstream.conf.template` | `/etc/nginx/conf.d/21-web-<env>-upstream.conf` | `127.0.0.1:<WEB_PORT>`; must equal that GitHub environment's `WEB_PORT` var |
| `deploy/nginx/web/web-rate-limits.conf` | `/etc/nginx/conf.d/31-web-rate-limits.conf` | own zones (50 r/s); a page load never spends the API budget |
| `deploy/nginx/web/snippets/web-security-headers.conf` | `/etc/nginx/snippets/cofoundaz/` | HTML-app headers. Not the API's base snippet: its COOP `same-origin` breaks the Google, Paystack and Mono popups |
| `deploy/nginx/web/web-site.conf.template` | `/etc/nginx/sites-available/web-<env>.conf` | :80 ACME + 301, :443 h2 → upstream; `/healthz`, `/_next/static/`, `/`; staging sends `X-Robots-Tag: noindex` |
| `deploy/nginx/web/web-bootstrap.conf.template` | `/etc/nginx/sites-available/web-<env>-bootstrap.conf` | first cert only: :80 ACME, 404 otherwise; removed once the full site is on |
| `deploy/nginx/web/render.sh` | (run anywhere) | fills the templates for one environment into `OUT_DIR`; installs nothing. Optional `WEB_REDIRECT_NAMES` adds a :443 block that 301s those names to the first server name |
| `deploy/nginx/web/standalone/*.template` | `/etc/nginx/sites-available/web-<env>[-bootstrap].conf` | the default, self-contained layout for stock nginx 1.24 |
| `deploy/nginx/test/verify-web-local.sh` | — | local proof against a real web container, 28 assertions. Default: standalone on **nginx 1.24** with only the two site files. `EDGE_LAYOUT=full`: full layout on 1.27 alongside the API vhosts |

## Prerequisites (Adebayo)
1. Hostnames decided. Production may serve two names (apex + www) on one cert.
2. DNS A record(s) → VPS IP, propagated (`dig +short <host>`). AAAA only if IPv6 really works; CAA must allow `letsencrypt.org` ([NGINX_TLS.md §3](./NGINX_TLS.md)).
3. **State on 2026-11-06:** `cofoundaz.com` and `www.cofoundaz.com` **already resolve to this VPS** (DNS at Cloudoon) but have no site or cert, so the public site is down until production is applied. Do **production first**; HTTP-01 webroot works right away. `stage.cofoundaz.com` has no record yet: add an `A` record before its step.
4. Recommended production shape: `WEB_SERVER_NAMES="www.cofoundaz.com"`, `WEB_REDIRECT_NAMES="cofoundaz.com"` (the apex 301s to www, so the browser sees one origin), one cert `--cert-name www.cofoundaz.com -d www.cofoundaz.com -d cofoundaz.com`. The exact copy-paste sequence for production is in the `cofoundaz` repo: `docs/sop/2026-11-06-fe-vps-deploy.md`, "Go-live runbook".
5. **Memory:** 7.8 GiB RAM, no swap, and container limits already above physical RAM. Add a 2 GiB swapfile first (step 1 of that runbook).

## Apply: standalone (production first)

**Hard stop: the web ports must be free.** On 2026-11-06 `127.0.0.1:3000` turned out to be taken by another Express app on this VPS (it answers `/healthz` with `x-powered-by: Express` and `{"error":"Not found"}`). That is why production uses **3100** and staging **3101**. If either line below says `STOP`, do not continue: pick another free port and use it everywhere `WEB_PORT` appears (render step, GitHub var).
```bash
for p in 3100 3101; do if sudo ss -ltnH "( sport = :$p )" | grep -q .; then echo "STOP: port $p is already in use:"; sudo ss -ltnp "( sport = :$p )"; else echo "ok: port $p free"; fi; done
```
To see which nginx site already proxies to a port, use `grep -R`, not `-r`. The files in `sites-enabled` are symlinks, and `grep -r` silently skips them:
```bash
sudo grep -Rn 'proxy_pass\|server 127.0.0.1' /etc/nginx/sites-enabled/
```

The exact copy-paste sequence for production (render, bootstrap, `certbot certonly --webroot`, swap to the real site, checks) is in the `cofoundaz` repo at `docs/sop/2026-11-06-fe-vps-deploy.md`, step 2. In short, for one environment:

```bash
WEB_ENV=production WEB_SERVER_NAMES="www.cofoundaz.com" WEB_REDIRECT_NAMES="cofoundaz.com" WEB_CERT_NAME="www.cofoundaz.com" WEB_PORT=3100 OUT_DIR=/tmp/web-edge-production ./deploy/nginx/web/render.sh
```

1. `sudo mkdir -p /var/www/certbot`. Install and enable `web-production-bootstrap.conf`, then `nginx -t` and reload.
2. `sudo certbot certonly --webroot -w /var/www/certbot --cert-name www.cofoundaz.com -d www.cofoundaz.com -d cofoundaz.com`.
3. Remove the bootstrap symlink. Install and enable `web-production.conf`, then `nginx -t` and reload.
4. `curl https://www.cofoundaz.com/healthz` → 502 until the first web deploy, then 200. The CD check calls the same URL from the VPS via `--resolve` through `location /`.

Staging: the same with `WEB_ENV=staging WEB_SERVER_NAMES="stage.cofoundaz.com" WEB_CERT_NAME="stage.cofoundaz.com" WEB_PORT=3101`, once `stage` has an A record. The two standalone sites use different upstream, rate-limit zone and session-cache names, so they load side by side (proven in the verifier).

## Apply: full layout (later, optional)
Install nginx.org ≥ 1.25.1 and the full `deploy/nginx/` layout first (NGINX_TLS.md §5). Then render with `EDGE_LAYOUT=full`, install the rendered `conf.d/*.conf` and `snippets/cofoundaz/*.conf` along with the site, and remove the standalone `web-<env>.conf` it replaces.

## Renewal
Webroot renewal works unchanged: the :80 block serves `/.well-known/acme-challenge/` from `/var/www/certbot`. Verify with `sudo certbot renew --dry-run --cert-name <host>` (NGINX_TLS.md §6). 

## Roll back
- Edge only: `sudo rm /etc/nginx/sites-enabled/web-<env>.conf && sudo nginx -t && sudo systemctl reload nginx`. The API vhosts are untouched by these files.
- There is no Vercel DNS fallback (DNS already points here); roll back the app with the FE workflow's `image_tag` input.

## Before the first user hits the new host (not edge, but blocking)
API `.env.<env>`: add the web origin(s) to `BACKEND_CORS_ORIGINS`, set `APP_BASE_URL`, re-encrypt, redeploy the API. Also update provider callbacks naming the web host (Google OAuth, Paystack, Flutterwave, Mono, LinkedIn). `security-headers-base.conf`'s CORP `same-site` stays right as long as the web host is under the same registrable domain as the API.

## Not verified
A real Let's Encrypt cert, a real handshake, and renewal. The standalone layout was run on the same nginx version as the VPS (1.24, Alpine build rather than Ubuntu's). `verify-web-local.sh` proves parsing and behaviour with self-signed certs only.

## Follow-ups
- Content-Security-Policy for the HTML app (needs nonces in Next first).
- Same-origin `/api/v1` on the web host (design B): one promotable image, no CORS.
