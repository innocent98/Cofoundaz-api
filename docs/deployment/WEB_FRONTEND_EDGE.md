# Web frontend edge: nginx sites + TLS for the self-hosted Next.js app

**Status: config and runbook only. Nothing here has been applied to the VPS.** Run it only after Adebayo gives an explicit go-ahead. Hostnames are not decided yet, so every file is a template, and the commands below use `<STAGING_WEB_HOST>`, `<PROD_WEB_HOST>`, `<PROD_WEB_ALT_HOST>` placeholders.

Pairs with the `cofoundaz` repo: `deploy/docker-compose.web.yml`, `deploy/web-deploy.sh`, `.github/workflows/cd-*.yml`, and `docs/sop/2026-11-06-fe-vps-deploy.md` there. API edge manual: [NGINX_TLS.md](./NGINX_TLS.md). The same rules apply: nginx ≥ 1.25.1, `certbot certonly --webroot` (never `--nginx`), `nginx -t` before `reload`, never `restart`.

## Files

| Repo path | Installs to | Notes |
|---|---|---|
| `deploy/nginx/web/web-upstream.conf.template` | `/etc/nginx/conf.d/21-web-<env>-upstream.conf` | `127.0.0.1:<WEB_PORT>`; must equal that GitHub environment's `WEB_PORT` var |
| `deploy/nginx/web/web-rate-limits.conf` | `/etc/nginx/conf.d/31-web-rate-limits.conf` | own zones (50 r/s); a page load never spends the API budget |
| `deploy/nginx/web/snippets/web-security-headers.conf` | `/etc/nginx/snippets/cofoundaz/` | HTML-app headers. Not the API's base snippet: its COOP `same-origin` breaks the Google, Paystack and Mono popups |
| `deploy/nginx/web/web-site.conf.template` | `/etc/nginx/sites-available/web-<env>.conf` | :80 ACME + 301, :443 h2 → upstream; `/healthz`, `/_next/static/`, `/`; staging sends `X-Robots-Tag: noindex` |
| `deploy/nginx/web/web-bootstrap.conf.template` | `/etc/nginx/sites-available/web-<env>-bootstrap.conf` | first cert only: :80 ACME, 404 otherwise; removed once the full site is on |
| `deploy/nginx/web/render.sh` | (run anywhere) | fills the templates for one environment into `OUT_DIR`; installs nothing |
| `deploy/nginx/test/verify-web-local.sh` | — | local proof: renders both environments, `nginx -t` alongside the API vhosts, 28 assertions against a real web container |

## Prerequisites (Adebayo)
1. Hostnames decided. Production may serve two names (apex + www) on one cert.
2. DNS A record(s) → VPS IP, propagated (`dig +short <host>`). AAAA only if IPv6 really works; CAA must allow `letsencrypt.org` ([NGINX_TLS.md §3](./NGINX_TLS.md)).
3. **During the Vercel cutover:** HTTP-01 needs the name to resolve to the VPS, so the **production** cert can only be issued once `www`/apex point here. Do staging first. For production, either flip DNS then issue right away (a short window where HTTPS fails; keep TTL at 300), or issue via DNS-01 beforehand (manual TXT record, `certbot certonly --manual --preferred-challenges dns`) so the cert exists before the flip. **Recommended: DNS-01 for the first production cert**, then switch it to webroot renewal (below).

## Apply (one environment at a time, staging first)

On a checkout of this repo (on the VPS or copied there):

```bash
WEB_ENV=staging WEB_SERVER_NAMES="<STAGING_WEB_HOST>" WEB_CERT_NAME="<STAGING_WEB_HOST>" WEB_PORT=<STAGING_WEB_PORT> OUT_DIR=/tmp/web-edge-staging ./deploy/nginx/web/render.sh
```

`00-default-deny.conf` drops (444) any name it does not know, so a new name first gets the HTTP-only bootstrap site, then its cert, then the full site:

```bash
sudo install -m 644 /tmp/web-edge-staging/sites-available/web-staging-bootstrap.conf /etc/nginx/sites-available/ && sudo ln -sf /etc/nginx/sites-available/web-staging-bootstrap.conf /etc/nginx/sites-enabled/web-staging-bootstrap.conf && sudo nginx -t && sudo systemctl reload nginx
```

```bash
sudo certbot certonly --webroot -w /var/www/certbot --cert-name <STAGING_WEB_HOST> -d <STAGING_WEB_HOST>
```

```bash
sudo rm /etc/nginx/sites-enabled/web-staging-bootstrap.conf
```

```bash
sudo install -m 644 /tmp/web-edge-staging/conf.d/*.conf /etc/nginx/conf.d/
```

```bash
sudo install -m 644 /tmp/web-edge-staging/snippets/cofoundaz/*.conf /etc/nginx/snippets/cofoundaz/
```

```bash
sudo install -m 644 /tmp/web-edge-staging/sites-available/web-staging.conf /etc/nginx/sites-available/ && sudo ln -sf /etc/nginx/sites-available/web-staging.conf /etc/nginx/sites-enabled/web-staging.conf
```

```bash
sudo nginx -t && sudo systemctl reload nginx
```

Then check through nginx before any web container exists (expect 502, which proves TLS + routing), and again after the first CD deploy (expect 200):

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://<STAGING_WEB_HOST>/healthz
```

Production is the same with `WEB_ENV=production`, `WEB_SERVER_NAMES="<PROD_WEB_HOST> <PROD_WEB_ALT_HOST>"`, `WEB_CERT_NAME=<PROD_WEB_HOST>`, `-d <PROD_WEB_HOST> -d <PROD_WEB_ALT_HOST>`.

## Renewal
Webroot renewal works unchanged: the :80 block serves `/.well-known/acme-challenge/` from `/var/www/certbot`. Verify with `sudo certbot renew --dry-run --cert-name <host>` (NGINX_TLS.md §6). A cert first issued with `--manual` DNS-01 will **not** renew by itself: re-issue it once with `--webroot` after DNS points here.

## Roll back
- Edge only: `sudo rm /etc/nginx/sites-enabled/web-<env>.conf && sudo nginx -t && sudo systemctl reload nginx`. The API vhosts are untouched by these files.
- Cutover: point DNS back at Vercel (keep the Vercel project for at least a week).

## Before the first user hits the new host (not edge, but blocking)
API `.env.<env>`: add the web origin(s) to `BACKEND_CORS_ORIGINS`, set `APP_BASE_URL`, re-encrypt, redeploy the API. Also update provider callbacks naming the web host (Google OAuth, Paystack, Flutterwave, Mono, LinkedIn). `security-headers-base.conf`'s CORP `same-site` stays right as long as the web host is under the same registrable domain as the API.

## Not verified
A real Let's Encrypt cert, a real handshake, the DNS-01 path, renewal, and nginx on the VPS version. `verify-web-local.sh` proves parsing and behaviour with self-signed certs only.

## Follow-ups
- Content-Security-Policy for the HTML app (needs nonces in Next first).
- Same-origin `/api/v1` on the web host (design B): one promotable image, no CORS.
