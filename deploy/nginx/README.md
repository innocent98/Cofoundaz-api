# `deploy/nginx/`

The edge configuration for the VPS: TLS termination, HTTP→HTTPS, security headers, rate
limiting and the reverse proxy to the two compose stacks.

**The operator's manual is [`docs/deployment/NGINX_TLS.md`](../../docs/deployment/NGINX_TLS.md).**
It covers DNS, first-time setup on a fresh host, certificate issue and renewal, the
two-stack port map, and troubleshooting. This file is only a map of the directory.

## Why this is in git

nginx configuration edited on the box is configuration nobody reviewed and nobody can
restore. These files are the source of truth; the host gets copies. Notably,
`certbot --nginx` is **not** used anywhere, because it rewrites config in place — the
`--webroot` flow in the manual writes a challenge file and touches nothing else.

## Layout

| Path | Installs to | Contains |
|---|---|---|
| `conf.d/00-tls.conf` | `/etc/nginx/conf.d/` | protocols, ciphers, session handling, stapling |
| `conf.d/10-hardening.conf` | `/etc/nginx/conf.d/` | `server_tokens`, buffers, timeouts, gzip, log format, per-host header maps |
| `conf.d/20-upstreams.conf` | `/etc/nginx/conf.d/` | the two loopback backends and their keepalive pools |
| `conf.d/30-rate-limits.conf` | `/etc/nginx/conf.d/` | `limit_req` / `limit_conn` zone definitions |
| `snippets/*.conf` | `/etc/nginx/snippets/cofoundaz/` | proxy headers, security headers, JSON error pages, ACME location |
| `sites-available/00-default-deny.conf` | `/etc/nginx/sites-available/` | catch-all for unknown `Host` / bare IP |
| `sites-available/api.cofoundaz.com.conf` | `/etc/nginx/sites-available/` | production vhost → `127.0.0.1:8000` |
| `sites-available/staging-api.cofoundaz.com.conf` | `/etc/nginx/sites-available/` | staging vhost → `127.0.0.1:8001` |
| `bootstrap/acme-bootstrap.conf` | `/etc/nginx/sites-available/` | **first run only** — HTTP-only vhost used to obtain the first certificates |
| `test/verify-local.sh` | — | local validation; runs nginx in a container against the real backends |

The `snippets/cofoundaz/` namespace matters: Debian and Ubuntu ship their own
`/etc/nginx/snippets/`, and dropping files straight into it invites a collision with a
distro update.

## Requirements

- **nginx ≥ 1.25.1.** The vhosts use `http2 on;` as a standalone directive; Debian 12
  (1.22) and Ubuntu 24.04 (1.24) fail `nginx -t` on it. Install from nginx.org — see the
  manual.
- Certificates from Let's Encrypt at `/etc/letsencrypt/live/<host>/`, obtained with
  `certbot certonly --webroot`.

## Validating a change

```bash
# with both compose stacks (or anything answering /health) already up
PROD_PORT=8000 STAGING_PORT=8001 ./deploy/nginx/test/verify-local.sh
```

Runs `nginx -t` on the assembled configuration, then drives 35 behavioural assertions
against a live nginx. On the host, always `nginx -t` before `systemctl reload nginx`, and
never `restart` — reload drains in-flight requests, restart drops them.

## Status

**NOT VERIFIED against a real server.** No VPS or DNS was available when these files were
written: no certificate was issued, no real handshake observed, no SSL Labs grade measured.
Everything here has been parsed by a real nginx and exercised end to end against the real
application with self-signed certificates. Treat the first deploy as the verification run.
