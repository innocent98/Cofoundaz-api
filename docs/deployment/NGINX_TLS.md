# nginx + TLS — cofoundaz-api

> **Type:** deployment reference · **Stack:** nginx on the VPS host, in front of two
> Docker-Compose stacks · **Last verified:** 2026-08-29

The edge. Everything between the public internet and `127.0.0.1:${API_PORT}` on the VPS:
TLS termination, HTTP→HTTPS, security headers, rate limiting, body-size ceilings, and the
JSON error envelope for the errors nginx generates itself.

The configuration lives in **`deploy/nginx/`** and is version-controlled on purpose. It is
not edited on the box. A config that is hand-edited on the server is a config nobody can
review and nobody can restore.

## How to read the verification markers

Same convention as [DEPLOYMENT_GUIDE.md](./DEPLOYMENT_GUIDE.md). Where something was
**observed**, it is stated plainly. Where it was reasoned from config and never executed,
it carries:

> **NOT VERIFIED —** what was not proven, and what would prove it.

**No VPS and no DNS were available.** No certificate was obtained, no real TLS handshake
happened, no SSL Labs scan was run. What *was* done: the whole configuration was loaded by
a real nginx and driven end to end against the real application with self-signed
certificates — 35 assertions, all passing (§10). Treat the first real deploy as the
verification run and correct this document afterwards.

---

## 1. The port map

One VPS, two compose stacks, two hostnames. nginx routes on the `Host` header.

| Environment | Hostname | Compose project | `DEPLOY_PATH` | `API_PORT` | nginx upstream |
|---|---|---|---|---|---|
| production | `api.cofoundaz.com` | `cofoundaz-api-prod` | `/opt/cofoundaz` | **8000** | `cofoundaz_production` |
| staging | `staging-api.cofoundaz.com` | `cofoundaz-api-staging` | `/opt/cofoundaz-staging` | **8001** | `cofoundaz_staging` |

`API_PORT` and `COMPOSE_PROJECT_NAME` come from each stack's `.env`. They are the only two
values that *must* differ, and the consequences of getting either wrong are covered in
`.env.production.example` §9 and in the header of `docker-compose.prod.yml`.

**The API is never published on `0.0.0.0`.** Both stacks bind `127.0.0.1:${API_PORT}:8000`.
This is not belt-and-braces on top of UFW — it is the *only* control, because Docker writes
its own iptables chain that is consulted **before** UFW's rules, so `ufw deny 8000` does not
protect a port published on all interfaces. nginx is the sole route in.

---

## 2. What is in the repo, and where each file goes on the host

```
deploy/nginx/
├── conf.d/                                  ->  /etc/nginx/conf.d/
│   ├── 00-tls.conf                              protocols, ciphers, sessions, stapling
│   ├── 10-hardening.conf                        server_tokens, buffers, gzip, per-host maps
│   ├── 20-upstreams.conf                        the two loopback backends + keepalive pools
│   └── 30-rate-limits.conf                      limit_req / limit_conn zone definitions
├── snippets/                                ->  /etc/nginx/snippets/cofoundaz/
│   ├── acme-challenge.conf                      the /.well-known/ location
│   ├── json-errors.conf                         413/429/502/503/504 in the app's envelope
│   ├── proxy-backend.conf                       every proxy_set_header and timeout
│   └── security-headers-base.conf               everything except CSP
├── sites-available/                         ->  /etc/nginx/sites-available/  (+ symlink)
│   ├── 00-default-deny.conf                     catch-all for unknown Host / bare IP
│   ├── api.cofoundaz.com.conf
│   └── staging-api.cofoundaz.com.conf
├── bootstrap/
│   └── acme-bootstrap.conf                      first-run only; see §5
└── test/
    └── verify-local.sh                          local validation harness; see §10
```

Note the `snippets/cofoundaz/` namespace. Debian and Ubuntu ship their own
`/etc/nginx/snippets/`, and dropping files straight into it invites a collision with a
distro update.

### Which nginx, and why it matters

The vhosts use **`http2 on;`** as a standalone directive. The older
`listen 443 ssl http2;` form was deprecated in nginx 1.25.1. So:

| nginx | Result |
|---|---|
| ≥ 1.25.1 | works |
| Debian 12 (1.22), Ubuntu 24.04 (1.24) | **`nginx -t` fails**: `unknown directive "http2"` |

Do not "fix" that by reverting to the deprecated listen parameter. Install from nginx.org,
which also makes HTTP/3 and the brotli module available:

```bash
curl -fsSL https://nginx.org/keys/nginx_signing.key \
  | sudo gpg --dearmor -o /usr/share/keyrings/nginx-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/nginx-archive-keyring.gpg] \
http://nginx.org/packages/mainline/ubuntu $(lsb_release -cs) nginx" \
  | sudo tee /etc/apt/sources.list.d/nginx.list
sudo apt-get update && sudo apt-get install -y nginx
nginx -v            # must print >= 1.25.1
```

> **NOT VERIFIED —** the repository setup above has not been run on the target host.
> Verify with `nginx -v` before enabling either vhost.

**nginx.org's packages do not read `sites-enabled/`.** Their `nginx.conf` includes only
`conf.d/*.conf`. Either add `include /etc/nginx/sites-enabled/*;` to the `http {}` block, or
place the vhost files directly in `conf.d/` with a numeric prefix that sorts them after the
four `conf.d` files above. Debian/Ubuntu's own package includes both directories already.

---

## 3. DNS

Two records, both pointing at the VPS's public address. Create them **before** running
certbot — HTTP-01 validation resolves the name and connects to it, so a missing or
still-propagating record is the most common first-attempt failure.

| Type | Name | Value | TTL |
|---|---|---|---|
| A | `api` | *VPS IPv4* | 300 while setting up |
| A | `staging-api` | *VPS IPv4* | 300 while setting up |
| AAAA | `api` | *VPS IPv6* | only if the host actually has IPv6 |
| AAAA | `staging-api` | *VPS IPv6* | only if the host actually has IPv6 |

Two things that silently break issuance:

- **An AAAA record on a host without working IPv6.** Let's Encrypt prefers IPv6 when an
  AAAA exists and does **not** fall back to IPv4 on failure. A stale or aspirational AAAA
  makes every validation fail with a connection timeout that looks like a firewall problem.
  Publish AAAA only once `curl -6` works from off-host.
- **A CAA record that omits Let's Encrypt.** If `cofoundaz.com` has any CAA record, it must
  include `letsencrypt.org` or issuance is refused outright:
  `dig +short CAA cofoundaz.com`.

Set TTL low (300s) while setting up, then raise it.

---

## 4. Firewall

```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp        # ACME challenge + the HTTPS redirect
sudo ufw allow 443/tcp
# sudo ufw allow 443/udp     # ONLY if HTTP/3 is enabled - see §8
sudo ufw enable
```

**Port 80 stays open permanently.** It is tempting to close it once TLS works — that
breaks `--webroot` renewal, and the breakage surfaces 60 days later as an expired
certificate rather than as an error now.

---

## 5. First-time setup on a fresh VPS

> **NOT VERIFIED —** this entire section. No host was available. Every command is
> reasoned from the committed configuration. Verification is: run it, and confirm
> `curl -I https://api.cofoundaz.com/health` returns `200` with `strict-transport-security`
> present.

### The chicken-and-egg, stated first

The two real vhosts declare `ssl_certificate /etc/letsencrypt/live/<host>/fullchain.pem`.
Those files do not exist yet, and nginx **refuses to start or reload** while a server block
points at a certificate that is not there:

```
nginx: [emerg] cannot load certificate "/etc/letsencrypt/live/api.cofoundaz.com/fullchain.pem":
BIO_new_file() failed (SSL: error:80000002:system library::No such file or directory)
```

So the real vhosts cannot be enabled first. `deploy/nginx/bootstrap/acme-bootstrap.conf`
exists to break the cycle: it serves the ACME challenge on port 80 for both hostnames and
declares no TLS at all.

**`certbot --nginx` is deliberately not used.** It rewrites the config in place, which
would mean the files in this repo stop describing what is actually running. `--webroot`
writes a challenge file and touches nothing else.

### Steps

**1. Install nginx and certbot.**

```bash
sudo apt-get install -y certbot          # the plugin-free package is enough for --webroot
nginx -v                                 # >= 1.25.1, see §2
```

**2. Create the webroot.**

```bash
sudo mkdir -p /var/www/certbot/.well-known/acme-challenge
sudo chown -R www-data:www-data /var/www/certbot     # `nginx:nginx` on nginx.org packages
```

**3. Install the configuration.**

```bash
# from a checkout of this repo on the VPS, or scp'd
sudo mkdir -p /etc/nginx/snippets/cofoundaz
sudo cp deploy/nginx/conf.d/*.conf          /etc/nginx/conf.d/
sudo cp deploy/nginx/snippets/*.conf        /etc/nginx/snippets/cofoundaz/
sudo cp deploy/nginx/sites-available/*.conf /etc/nginx/sites-available/
sudo cp deploy/nginx/bootstrap/*.conf       /etc/nginx/sites-available/

# Debian/Ubuntu ship a default vhost that owns port 80. Remove it, or it answers
# for every unmatched Host and the catch-all below never sees anything.
sudo rm -f /etc/nginx/sites-enabled/default
```

**4. Enable ONLY the bootstrap vhost, and get the certificates.**

```bash
sudo ln -sf ../sites-available/acme-bootstrap.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# Prove the challenge path is reachable BEFORE involving Let's Encrypt. Their
# rate limits are per-hostname and unforgiving; a failed real attempt costs an
# hour of waiting, this costs nothing.
echo hello | sudo tee /var/www/certbot/.well-known/acme-challenge/probe
curl -sS http://api.cofoundaz.com/.well-known/acme-challenge/probe          # -> hello
curl -sS http://staging-api.cofoundaz.com/.well-known/acme-challenge/probe  # -> hello
sudo rm /var/www/certbot/.well-known/acme-challenge/probe

# Dry run first, against the staging CA. Same code path, no rate limit.
sudo certbot certonly --webroot -w /var/www/certbot --dry-run \
     -d api.cofoundaz.com -d staging-api.cofoundaz.com

# Then for real - SEPARATELY, one certificate per hostname (see below).
sudo certbot certonly --webroot -w /var/www/certbot \
     --agree-tos -m <ops-mailbox> --no-eff-email \
     -d api.cofoundaz.com
sudo certbot certonly --webroot -w /var/www/certbot \
     --agree-tos -m <ops-mailbox> --no-eff-email \
     -d staging-api.cofoundaz.com
```

**Two certificates, not one SAN certificate covering both.** A single certificate with both
names would work and would halve the renewals. It is rejected here because it couples the
two environments at the layer where they should be most independent: one certificate means
one renewal, and a renewal failure takes **both** hosts down at once. It also means
staging's hostname is disclosed in production's certificate, permanently and publicly, via
Certificate Transparency logs. Per-host issue costs one extra command and nothing else.

A **wildcard** (`*.cofoundaz.com`) would need DNS-01 validation and a DNS-provider API
token stored on the VPS. That is a real escalation of blast radius — a token that can edit
the zone — in exchange for convenience this setup does not need at two hostnames.

**5. Swap to the real vhosts.**

```bash
sudo rm /etc/nginx/sites-enabled/acme-bootstrap.conf
sudo ln -sf ../sites-available/00-default-deny.conf              /etc/nginx/sites-enabled/
sudo ln -sf ../sites-available/api.cofoundaz.com.conf            /etc/nginx/sites-enabled/
sudo ln -sf ../sites-available/staging-api.cofoundaz.com.conf    /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

`reload`, never `restart`. Reload forks new workers with the new config and lets the old
ones finish their in-flight requests; restart drops every open connection.

**6. Verify.**

```bash
curl -I http://api.cofoundaz.com/health                    # 301 -> https://
curl -I https://api.cofoundaz.com/health                   # 200 + security headers
curl -I --http2 https://api.cofoundaz.com/health           # HTTP/2 200
curl -sI https://staging-api.cofoundaz.com/health | grep -i x-robots-tag

# TLS floor: 1.2 must connect, 1.1 must not.
openssl s_client -connect api.cofoundaz.com:443 -servername api.cofoundaz.com -tls1_2 </dev/null
openssl s_client -connect api.cofoundaz.com:443 -servername api.cofoundaz.com -tls1_1 </dev/null   # must FAIL

# The full chain is served, not just the leaf. This is the check that catches
# fullchain.pem/cert.pem confusion, which browsers hide and mobile clients do not.
openssl s_client -connect api.cofoundaz.com:443 -servername api.cofoundaz.com </dev/null 2>/dev/null \
  | grep -E 'Certificate chain|s:|i:'
```

Then externally: <https://www.ssllabs.com/ssltest/analyze.html?d=api.cofoundaz.com>.
Target **A+**. Note that A+ requires HSTS with `max-age` ≥ 180 days, which the production
vhost sets (`63072000`); it does **not** require `preload`, and preload is deliberately
absent — see §7.

---

## 6. Renewal, and how to verify it before it matters

certbot installs a systemd timer at install time. It runs twice daily and renews anything
inside 30 days of expiry.

```bash
systemctl list-timers | grep certbot
sudo systemctl status certbot.timer      # must be `active (waiting)`
```

### nginx must be told to pick up the new certificate

A renewed certificate on disk changes nothing until nginx reloads — the old one stays in
worker memory until then. This is the single most common way a "working" auto-renewal
still results in an outage, because everything looks correct: certbot succeeded, the files
on disk are new, and the server keeps serving an expired certificate.

```bash
sudo mkdir -p /etc/letsencrypt/renewal-hooks/deploy
printf '#!/bin/sh\nsystemctl reload nginx\n' \
  | sudo tee /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
```

A **deploy hook in `renewal-hooks/deploy/`** rather than `renew_hook =` in each
`/etc/letsencrypt/renewal/<domain>.conf`: the directory form applies to every certificate,
including ones added later, so adding a third hostname cannot forget it.

### Prove renewal works now, not in 60 days

```bash
sudo certbot renew --dry-run
```

This exercises the full path — challenge write, HTTP fetch, validation — against the
staging CA, without touching rate limits or the live certificate. **Run it after any change
to the `:80` server blocks.** The failure mode it catches is a redirect swallowing
`/.well-known/`, which is invisible until the certificate it would have renewed has
already expired.

Confirm it also reaches the deploy hook:

```bash
sudo certbot renew --dry-run --run-deploy-hooks
sudo journalctl -u nginx --since '5 min ago' | tail
```

### Monitor expiry independently

Auto-renew can fail silently — a rate limit, a DNS change, a full disk. Check from
somewhere that is not the VPS:

```bash
echo | openssl s_client -connect api.cofoundaz.com:443 -servername api.cofoundaz.com 2>/dev/null \
  | openssl x509 -noout -enddate
```

> **NOT VERIFIED —** no renewal has ever run, dry or otherwise. `--dry-run` on the real
> host is the verification.

---

## 7. HSTS, and why `preload` is absent

Both vhosts send HSTS. Neither sends `preload`, and the production host is the one where
that restraint matters.

| Host | Header |
|---|---|
| `api.cofoundaz.com` | `max-age=63072000; includeSubDomains` |
| `staging-api.cofoundaz.com` | `max-age=31536000` |

`includeSubDomains` on `api.cofoundaz.com` is close to a no-op — nothing is served under
`*.api.cofoundaz.com` — and is set so that anything added later inherits HTTPS-only by
default.

**`preload` is the one irreversible directive in this configuration.** Submitting a name to
the browser preload list is not a header you can take back: removal takes months to reach
shipped browser versions, and until then browsers refuse plain HTTP for that name
regardless of what the server sends.

It is also not this host's decision. hstspreload.org requires the **apex**
(`cofoundaz.com`) to serve `includeSubDomains; preload` and redirect to HTTPS — and an apex
preloaded with `includeSubDomains` locks the **entire zone**, including subdomains this API
will never hear about: a vendor CNAME that only speaks HTTP, a status page, a mail host.
That is a whole-zone commitment for whoever owns `cofoundaz.com`, made once the zone is
known to be all-HTTPS permanently. Adding `preload` to an API vhost makes it for them, by
accident.

Staging gets a plain one-year `max-age` with no `includeSubDomains`: narrower, and
survivable if staging ever has to move.

**A note for whoever is debugging this later:** HSTS is remembered by the *browser*.
Removing the header does not release a client that has already seen it. Use a fresh
profile, or `chrome://net-internals/#hsts` to delete the entry — do not conclude the server
is misconfigured.

---

## 8. HTTP/3, brotli, and OCSP stapling — the honest status

All three are present in the configuration and **none of them is doing anything yet.**

| Feature | Status |
|---|---|
| **HTTP/2** | **On.** Verified negotiating h2 locally. |
| **HTTP/3 (QUIC)** | Commented out in both vhosts. Needs an nginx built with QUIC *and* `ufw allow 443/udp`. Uncommenting on an nginx without QUIC is an `unknown directive` parse failure, so it must be deliberate. If both vhosts enable it, `reuseport` goes on exactly **one** of them. |
| **brotli** | Commented out in `conf.d/10-hardening.conf`. Not compiled into Debian's or Ubuntu's nginx. From nginx.org: `apt-get install nginx-module-brotli`, then two `load_module` lines at the top of `nginx.conf`. Worth roughly 15–20% over gzip on JSON — real, not dramatic. |
| **OCSP stapling** | On, and currently **inert**. Let's Encrypt retired its OCSP responders and no longer puts an OCSP URL in issued certificates, so nginx logs `"ssl_stapling" ignored, no OCSP responder URL in the certificate` and staples nothing. That warning is **expected and correct** — do not "fix" it. The directives stay because they cost nothing and become live if the issuer ever changes. |

> **NOT VERIFIED —** HTTP/3 and brotli have never been parsed by an nginx capable of
> accepting them. The stapling warning **was** observed, exactly as described, during local
> validation.

---

## 9. What the edge actually enforces

### Rate limiting — a backstop, not a second per-user limiter

The application already limits: `app/main.py` builds a slowapi `Limiter` at
`RATE_LIMIT_PER_MINUTE` (120/minute), keyed on the authenticated user's `sub` claim and
falling back to the client address. That is the **fairness** layer.

nginx cannot see a JWT subject, so anything it counts is per-IP — and a per-IP budget tight
enough to be a per-user limit would punish everyone behind one office NAT or one mobile
carrier's CGNAT for each other's traffic. So nginx does the job the app cannot: it absorbs
abuse before it costs a gunicorn worker, a database connection, or a JWT verification.

| Zone | Rate | Burst | Applies to |
|---|---|---|---|
| `cfz_prod_general` | 30 r/s | 60 | everything on production |
| `cfz_prod_auth` | 5 r/s | 10 | `/api/v1/auth/` on production |
| `cfz_stg_general` | 30 r/s | 60 | everything on staging |
| `cfz_stg_auth` | **15 r/s** | 30 | `/api/v1/auth/` on staging |
| `cfz_conn` | — | 40 concurrent | both hosts, shared |

The general zone sits ~15× above the app's 2 r/s per-user budget. **That gap is the
design.** Narrow it and nginx starts pre-empting the app's limiter for shared-IP users,
which is exactly the failure being avoided.

Staging's auth zone is looser than production's on purpose: the CD gate drives signup,
login and refresh in a tight serial loop from **one** GitHub runner address. At
production's 5 r/s the gate would be one slow runner away from failing on a 429 unrelated
to the change under test — and a gate that goes red for no reason teaches people to re-run
it, which is how a gate stops being a gate.

Zones are **separate per environment** so an E2E burst against staging cannot spend
production's budget for the same address.

### Body size — sized to the logo endpoint, and no larger

| Scope | Limit | Why |
|---|---|---|
| everything | `1m` | the API is JSON; 1 MB is already generous |
| `= /api/v1/onboarding/logo` | `3m` | the app accepts 2 MiB and rejects more itself |

`app/api/v1/endpoints/onboarding/logo.py` sets `_MAX_BYTES = 2 * 1024 * 1024` and returns a
422 with *"Logo must be 2 MB or smaller."*. nginx's stock 1 MB default would 413 a 1.5 MB
PNG the app would have accepted — and the frontend cannot diagnose that, because the
response is nginx HTML with no application error code in it.

The ceiling is `3m` rather than exactly `2m` for a reason beyond headroom: multipart framing
adds bytes on top of the file, and more importantly a request between 2 MB and 3 MB now
**reaches the app** and comes back as the app's actionable JSON 422 instead of a generic
413. The edge limit exists to stop a 500 MB POST; the product rule belongs to the app.

Verified locally: 1.5 MB → 413 elsewhere; 4 MB → 413 at the logo endpoint; 2.5 MB → passes
the edge and the app decides.

### Security headers

Set on **every** response including 4xx and 5xx (`always` on every directive — without it
nginx omits them on exactly the responses an attacker is most likely to be reading).

`X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
`Permissions-Policy`, `Cross-Origin-Opener-Policy`, `Cross-Origin-Resource-Policy`, HSTS,
and `X-Robots-Tag` on staging only.

CSP is **not** in the shared snippet, because it is the one header that genuinely differs:

- API responses: `default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'`
- the docs pages (staging only): wide enough for jsDelivr and Google Fonts, and no wider.

> **Watch out for `add_header` inheritance.** A location that sets *any* `add_header`
> inherits *none* from the server block. That is why `security-headers-base.conf` is an
> include rather than a server-level block, and why every location with its own CSP
> includes it again. Getting this wrong silently strips every security header from one
> route.

### The OpenAPI docs: closed on production, open on staging

The application always mounts `/api/v1/docs`, `/api/v1/redoc` and `/api/v1/openapi.json`
(`app/main.py` sets all three unconditionally). Closing them is therefore an edge decision,
and this is the only place it can be made without touching app code.

Production returns **404** for all three. The schema is a complete map of every route,
parameter and model — the reconnaissance an attacker would otherwise assemble by hand, and
the first thing an automated scanner looks for. 404 rather than 403, because a 403 confirms
the path exists.

Staging serves them, and that is what makes closing production cheap: staging runs the
**same image**, so the schema there is authoritative for the frontend and for codegen.
`/api/v1/openapi.json` on staging is also **not optional** —
`e2e/test_smoke.py::test_openapi_served` fetches it, and that test is one of the 13 that
gate production.

To open them on production, copy the docs location from the staging vhost — it carries the
relaxed CSP. Deleting the 404 block alone yields a blank page, because the strict API CSP
blocks the bundle.

### Timeouts

`proxy_read_timeout` is **65s**, deliberately five seconds above gunicorn's `--timeout 60`.
If both fired at 60s they would race, and whoever won would decide whether the operator
sees a useful `WORKER TIMEOUT` in the container log or an opaque nginx 504 with no upstream
record. Letting gunicorn always fire first keeps the diagnosis on the side that knows what
the request was doing.

The upstream keepalive pool sets `keepalive_timeout 4s`, which must stay **below**
gunicorn's `--keep-alive 5`. If nginx held a pooled connection longer than gunicorn is
willing to, gunicorn would close first and nginx would write into a dead socket — the
classic source of intermittent, unreproducible 502s behind nginx.

---

## 10. Local validation

```bash
# with the two compose stacks (or anything answering /health) already up
PROD_PORT=8000 STAGING_PORT=8001 ./deploy/nginx/test/verify-local.sh
KEEP=1 ... ./deploy/nginx/test/verify-local.sh    # leave the container up to poke at
```

It assembles a temp `/etc/nginx` from `deploy/nginx/`, generates self-signed certificates
**at the real `/etc/letsencrypt/live/…` paths** so the vhost files are used unedited, runs
`nginx -t`, then drives the running server. It makes exactly two mutations, both in the temp
copy: the upstream addresses (nginx is in a container, so its loopback is not the host's)
and those certificates.

**Result, 2026-08-29: 35 assertions, 0 failures** — redirect and its target, ACME path not
redirected, h2 negotiated, both backends proxied, per-host header sets, `always` on a
non-2xx, docs 404-on-prod/200-on-staging, docs CSP, three body-size boundaries with JSON
413, unknown-SNI rejection, auth-zone 429 with the JSON envelope, and general traffic
unaffected by the auth-zone limit.

It **cannot** test: a real certificate chain, a real handshake, the SSL Labs grade, HSTS in
a browser, certbot, or HTTP/3.

---

## 11. `X-Forwarded-For` and the application's rate limiter

> **This section describes a real defect that is NOT fixed by anything in this repo.** The
> nginx half is done and committed. The application-side half is one environment variable
> and is deliberately left for its own review — see "The fix" below.

### The symptom

Behind nginx, every **unauthenticated** request keys the application's rate limiter on the
same address. Login, signup, verify-resend and forgot-password — precisely the endpoints
where per-IP limiting is the entire point — share one 120/minute bucket for the whole
internet.

### Why, exactly

`_rate_limit_key` in `app/main.py` falls back to `get_remote_address(request)`, which is
`request.client.host` — the ASGI `scope["client"]`. Uvicorn sets that from the socket peer,
unless `ProxyHeadersMiddleware` rewrites it from `X-Forwarded-For`.

That middleware **is** already installed: `uvicorn.Config(proxy_headers=True)` is the
default and `UvicornWorker` does not override it. But it only trusts a peer in
`forwarded_allow_ips`, whose default is `"127.0.0.1,::1"`.

And the peer is not `127.0.0.1`. nginx connects to `127.0.0.1:${API_PORT}` on the host, but
Docker opens a **second** connection into the container from the compose bridge gateway. So
uvicorn sees `172.x.0.1`, the default never matches, the header is ignored.

Observed directly, 2026-08-29, against the real image and compose stack:

| Request to `127.0.0.1:18000/health` | Client the app logged |
|---|---|
| no forwarded header | `172.25.0.1` — the bridge gateway, **never** `127.0.0.1` |
| `X-Forwarded-For: 203.0.113.9` | `172.25.0.1` — header ignored |
| (container's own healthcheck) | `127.0.0.1` — the only thing the default ever matches |

### The fix

**One environment variable in each stack's `.env`. No code change, no CLI flag.**

```
FORWARDED_ALLOW_IPS=*
```

Gunicorn reads it natively — `gunicorn.config.ForwardedAllowIPS` is
`os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1,::1")` — and `UvicornWorker` passes
`self.cfg.forwarded_allow_ips` straight into `uvicorn.Config`.

With it set, the same three requests give:

| Request | Client the app logged |
|---|---|
| `X-Forwarded-For: 203.0.113.9` | `203.0.113.9` ✅ |
| `X-Forwarded-For: 198.51.100.7, 172.25.0.1` | `198.51.100.7` — the **leftmost** |
| no header | `172.25.0.1` |

**Why `*` and not something narrower.** Two narrower options were tried and both fail:

- **A CIDR (`172.16.0.0/12`) is rejected by gunicorn.** Its
  `validate_string_to_addr_list` calls `ipaddress.ip_address()` on each entry, which raises
  on a network. The container crash-loops with
  `Error: '172.16.0.0/12' does not appear to be an IPv4 or IPv6 address`. Observed.
  (uvicorn's own `_TrustedHosts` *does* accept CIDR — gunicorn never lets the value reach
  it. Documentation that recommends a CIDR here is describing bare uvicorn.)
- **A literal gateway address** works but is not stable: Docker allocates the bridge subnet
  from a pool at network-creation time, so it can change on any `compose down` / `up`, and
  it differs between the two stacks.

### Why `*` is safe here — and the condition that must stay true

`*` means "trust the forwarded header from any peer". Two things make that acceptable, and
**both** must hold:

1. **The port is published on `127.0.0.1` only.** Nothing off-host can reach the app to
   forge a header in the first place.
2. **nginx OVERWRITES `X-Forwarded-For` rather than appending to it.** This is the real
   control. `deploy/nginx/snippets/proxy-backend.conf` sets:

   ```nginx
   proxy_set_header X-Forwarded-For $remote_addr;
   ```

   **not** the near-universal `$proxy_add_x_forwarded_for`, which appends. The second row
   of the table above is the reason: with `*`, uvicorn takes the **leftmost** value. Under
   `$proxy_add_x_forwarded_for` a client sending `X-Forwarded-For: 198.51.100.7` would have
   its own fabrication forwarded first — and could pick a fresh rate-limit identity per
   request, defeating the anonymous limiter completely.

   Overwriting makes the header single-valued, so leftmost equals rightmost and which end
   uvicorn reads stops mattering. Verified end to end: a request sent **through nginx**
   carrying `X-Forwarded-For: 203.0.113.9` reached the app as nginx's real peer, not as
   `203.0.113.9`.

**If a CDN or WAF is ever put in front of this host** (Cloudflare, Fastly), `$remote_addr`
becomes the CDN's address and that line starts discarding the real client. The fix is then
`ngx_http_realip_module` — `set_real_ip_from <cdn ranges>` plus `real_ip_header
CF-Connecting-IP` — so `$remote_addr` is the true client again. Do **not** solve it by
reverting to `$proxy_add_x_forwarded_for`.

### What still needs doing

`FORWARDED_ALLOW_IPS=*` is documented in `.env.production.example`, so it is in front of
whoever fills in the next environment. **It is not active anywhere.** The real values live
in `.env.staging.enc` / `.env.production.enc`, and those are regenerated by
`./scripts/env.sh encrypt <env>` — Adebayo's action, not this repo's.

The same variable also fixes `X-Forwarded-Proto`: without it `request.url.scheme` is `http`
behind the proxy, so anything the app builds from the request URL comes out as a plaintext
link. (Verification email links happen to be safe — they are built from the `SERVER_HOST`
setting, not from the request — but that is luck, not design.)

---

## 12. Staging is publicly reachable — the posture

Staging **has** to be reachable from the internet: the CD gate runs the E2E suite against it
from a GitHub-hosted runner (`.github/workflows/cd.yml`, job `staging-e2e`), and those
runners have no stable egress addresses. An IP allowlist would break the gate. That is a
constraint, not a preference.

### What ships, and what it is worth

| Control | Status | Worth |
|---|---|---|
| `X-Robots-Tag: noindex, nofollow, noarchive` | **on** | stops well-behaved crawlers indexing a duplicate of production. A request, not a control. |
| TLS + HSTS + full security header set | **on** | identical posture to production |
| Rate limiting | **on**, auth zone loosened for the gate | absorbs scripted abuse |
| OpenAPI docs | **served** | required — the gate fetches `openapi.json`; and serving them here is what lets production close them |
| HTTP Basic | **off**, ready to enable | see below |

### The recommendation

**Ship it without authentication.** The honest reasoning:

- Staging runs the **same image** as production and holds **test data**. The asset being
  protected is not the data; it is the absence of a second front door.
- The real exposures are signup spam and account enumeration, and both are addressed by the
  rate limiting and by the app's own lockout (`LOGIN_MAX_FAILS` / `LOGIN_LOCKOUT_MINUTES`).
- Every auth layer added here is a layer the CD gate depends on. A gate that fails because
  a credential rotated is a gate people learn to bypass.

**This holds only while staging's `.env` contains staging secrets.** If a staging
environment is ever pointed at a production database, given production SMTP credentials, or
loaded with real user data, this recommendation is void and Basic auth becomes mandatory.

### If Basic auth is wanted anyway — how CD passes credentials

The mechanism, with **zero changes to the `e2e/` suite**:

```bash
# on the VPS
sudo apt-get install -y apache2-utils
sudo htpasswd -c /etc/nginx/cofoundaz-staging.htpasswd ci
sudo chown root:www-data /etc/nginx/cofoundaz-staging.htpasswd
sudo chmod 640 /etc/nginx/cofoundaz-staging.htpasswd
```

Then uncomment the two `auth_basic` lines in the staging vhost **and** the `auth_basic off;`
line in the health location — and note that `snippets/cofoundaz/acme-challenge.conf` is
already exempt because `auth_basic` is not set inside it. Enabling auth without those
exemptions breaks certificate renewal and every uptime probe, and both failures are silent
until they are not.

On the GitHub side: add a **secret** `E2E_BASE_URL` on the `staging` environment carrying
the credentials in the URL, and change one line in `cd.yml`:

```yaml
E2E_BASE_URL: ${{ secrets.E2E_BASE_URL }}   # https://ci:<pass>@staging-api.cofoundaz.com
```

`vars.APP_URL` stays clean, because it is rendered in the GitHub UI as `environment.url`
and would otherwise display the password.

`e2e/conftest.py` builds `httpx.Client(base_url=BASE_URL)`, and httpx sends Basic
automatically from URL userinfo. **Verified** against the pinned httpx 0.27.2 inside the
project's own image, with that exact construction: `Authorization: Basic …` decoding to
`ci-user:ci-pass`, and no header at all when the userinfo is absent.

The cost, stated plainly: a credential that must be rotated, that can break the production
deploy path when it is, and that has to be exempted for ACME and health. That is why it is
not the default.

---

## 13. Troubleshooting

### 502 Bad Gateway

The upstream is not answering. In order:

```bash
cd /opt/cofoundaz                    # or /opt/cofoundaz-staging
docker compose -f docker-compose.prod.yml --env-file .env ps
docker compose -f docker-compose.prod.yml --env-file .env logs --tail=100 api
grep -E '^(API_PORT|COMPOSE_PROJECT_NAME)=' .env
sudo ss -ltnp | grep -E '8000|8001'  # is anything actually listening?
sudo tail -50 /var/log/nginx/cofoundaz-prod.error.log
```

The specific traps here:

- **`API_PORT` disagrees with the nginx upstream.** The one failure mode this two-stack
  layout adds. `conf.d/20-upstreams.conf` hardcodes 8000/8001; if a `.env` says something
  else, that vhost 502s while the other is perfectly healthy.
- **Both stacks on the same `COMPOSE_PROJECT_NAME`.** The second `compose up` adopts the
  first's containers and the first stack's port stops being served.
- **Intermittent 502s under load, nothing in the app logs.** Upstream keepalive racing
  gunicorn's `--keep-alive`. `keepalive_timeout` in the upstream block must be *below*
  gunicorn's value; it is 4s vs 5s. Suspect this if you changed either.
- **`connect() failed (111: Connection refused)`** in the error log means nothing is
  listening — a container problem. **`upstream prematurely closed connection`** means the
  worker died mid-response — an application problem, so read the app log.

nginx is not the fix for either. If the container is down, that belongs to the compose
stack — see [DEPLOYMENT_GUIDE.md](./DEPLOYMENT_GUIDE.md) §12.

### 504 Gateway Timeout

The app accepted the request and took longer than `proxy_read_timeout` (65s). Because
gunicorn's `--timeout` is 60s, **gunicorn should have fired first** — so look for
`WORKER TIMEOUT` in the app log and treat that as the real signal.

Raising `proxy_read_timeout` is almost never the fix. A 504 means an endpoint took over a
minute; raising the timeout hides it and moves the failure to whatever the client's own
timeout is. Check for a slow query first —
`docker compose … logs db | grep 'duration:'` surfaces anything over 1s, which
`docker-compose.prod.yml` already logs.

### SSL handshake failures

```bash
openssl s_client -connect api.cofoundaz.com:443 -servername api.cofoundaz.com </dev/null
```

| What you see | Cause |
|---|---|
| `unrecognized_name` / handshake refused | SNI did not match a vhost — you reached `00-default-deny.conf`. Check `server_name` and DNS. |
| Works in a browser, fails in a mobile app / Java / older curl | **`cert.pem` instead of `fullchain.pem`.** Browsers cache intermediates and fetch by AIA, so they hide it. Check the `Certificate chain` block for two entries. |
| `tlsv1 alert protocol version` from an old client | Working as intended. TLS 1.0/1.1 are off. Update the client's TLS library; do not lower the floor. |
| `certificate has expired` while certbot reports success | The renewal ran but nginx was never reloaded. Install the deploy hook (§6) and `systemctl reload nginx`. |
| nginx will not start: `cannot load certificate … No such file` | The certificate does not exist yet. Use the bootstrap vhost (§5). |
| `socket() [::]:80 failed (97: Address family not supported)` | IPv6 is disabled on the host. Remove the `listen [::]:…` lines, or enable IPv6. |

### 413 on a logo upload

Expected above 3 MB. **Between 2 MB and 3 MB the app should answer, not nginx** — if a
2.5 MB upload returns nginx's 413, `client_max_body_size` on the
`= /api/v1/onboarding/logo` location was lost. Confirm the exact-match location is still
exact: adding a trailing slash or changing it to a prefix silently stops it matching.

### Unexpected 429

Check which layer produced it. The bodies are deliberately different in `code`:

- `{"error":{"code":"RATE_LIMITED", …}}` with **no** `X-Request-ID` in the access log's
  upstream columns → nginx's `limit_req`. Look for `limiting requests` in the error log.
- The same code but with an upstream status recorded (`us=429` in the access log) → the
  app's slowapi limiter.

An E2E gate hitting 429 on staging means `cfz_stg_auth` is too tight, or the app's
`RATE_LIMIT_PER_MINUTE` is being consumed by warm workers — CD already restarts the `api`
service before the gate for exactly that reason.

### Redirect loop

`ERR_TOO_MANY_REDIRECTS` on every request usually means the application is generating
`http://` URLs because it does not know the request arrived over TLS — the
`X-Forwarded-Proto` half of §11. Set `FORWARDED_ALLOW_IPS` and it goes away.

### Certbot renewal fails

```bash
sudo certbot renew --dry-run
sudo tail -50 /var/log/letsencrypt/letsencrypt.log
curl -sS http://api.cofoundaz.com/.well-known/acme-challenge/probe   # after creating it
```

Usual causes: `/.well-known/acme-challenge/` being redirected to HTTPS instead of served on
`:80`; `/var/www/certbot` missing or not readable by the nginx worker user; port 80 closed
in UFW after TLS started working; a stale AAAA record (§3).

---

## Related documents

- [DEPLOYMENT_GUIDE.md](./DEPLOYMENT_GUIDE.md) — the compose stack, CI/CD, sizing, runbooks
- [GITHUB_ACTIONS_SETUP.md](./GITHUB_ACTIONS_SETUP.md) — secrets, environments, the staging gate
- [ENV_ENCRYPTION.md](./ENV_ENCRYPTION.md) — how `.env` reaches the VPS
- [ROLLBACK.md](./ROLLBACK.md) — reverting a deploy
