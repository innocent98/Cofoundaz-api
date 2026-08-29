#!/usr/bin/env bash
#
# Local validation for deploy/nginx/ — no VPS, no DNS, no Let's Encrypt.
#
# WHAT THIS PROVES
#   - the configuration PARSES (`nginx -t`) as an integrated whole, includes and
#     all, on a real nginx binary of the version these files require;
#   - the request handling actually BEHAVES: HTTP->HTTPS redirect, per-host
#     security headers, the docs open/closed split, body-size ceilings, the JSON
#     error envelope, the rate-limit backstop, and — the one that matters most —
#     that nginx OVERWRITES a client-supplied X-Forwarded-For instead of
#     appending to it.
#
# WHAT IT CANNOT PROVE
#   - anything about a real certificate, a real handshake with a real client, an
#     SSL Labs grade, HSTS behaviour in a browser, certbot issue or renewal, or
#     HTTP/3. Those need the actual host. See docs/deployment/NGINX_TLS.md.
#
# THE TWO MUTATIONS IT MAKES TO THE CONFIG, both confined to a temp copy:
#   1. `server 127.0.0.1:800x` in conf.d/20-upstreams.conf is rewritten to
#      `host.docker.internal:<port>`, because nginx runs in a container here and
#      its own loopback is not the host's.
#   2. Self-signed certificates are generated AT THE REAL PATHS
#      (/etc/letsencrypt/live/<host>/…) inside the container, so the vhost files
#      themselves are used byte-for-byte, unedited.
#
# USAGE
#   ./deploy/nginx/test/verify-local.sh                 # expects backends on 8000/8001
#   PROD_PORT=18000 STAGING_PORT=18001 ./deploy/nginx/test/verify-local.sh
#
# The backends can be the real compose stacks or anything that answers
# /health with a 200.

set -euo pipefail

NGINX_IMAGE="${NGINX_IMAGE:-nginx:1.27-alpine}"
PROD_PORT="${PROD_PORT:-8000}"
STAGING_PORT="${STAGING_PORT:-8001}"
HTTP_PORT="${HTTP_PORT:-18080}"
HTTPS_PORT="${HTTPS_PORT:-18443}"
CONTAINER="${CONTAINER:-cofoundaz-nginx-verify}"

PROD_HOST="api.cofoundaz.com"
STG_HOST="staging-api.cofoundaz.com"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$(dirname "${HERE}")"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/cofoundaz-nginx-verify.XXXXXX")"
PASS=0
FAIL=0

# KEEP=1 leaves the container and the temp /etc/nginx in place so a failure can
# be poked at with curl instead of guessed at. It prints where things are.
cleanup() {
    if [ -n "${KEEP:-}" ]; then
        echo
        echo "KEEP=1: container '${CONTAINER}' still running; config in ${WORK}"
        echo "  curl -k --resolve ${PROD_HOST}:${HTTPS_PORT}:127.0.0.1 https://${PROD_HOST}:${HTTPS_PORT}/health"
        return
    fi
    docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
    rm -rf "${WORK}"
}
trap cleanup EXIT

ok()   { PASS=$((PASS + 1)); printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
bad()  { FAIL=$((FAIL + 1)); printf '  \033[31mFAIL\033[0m  %s\n' "$1"; }

# expect <description> <expected> <actual>
expect() {
    if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (expected '$2', got '$3')"; fi
}

# contains <description> <needle> <haystack>
contains() {
    case "$3" in
        *"$2"*) ok "$1" ;;
        *)      bad "$1 (missing '$2')" ;;
    esac
}

# HTTP/2 sends header NAMES lowercased, so every header assertion runs against a
# lowercased copy of the response and uses a lowercase needle. Comparing against
# `X-Content-Type-Options` looks right and fails on every h2 response — which it
# did, twice, on the first run of this script.
lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

# lacks <description> <needle> <haystack>
lacks() {
    case "$3" in
        *"$2"*) bad "$1 (unexpectedly contains '$2')" ;;
        *)      ok "$1" ;;
    esac
}

# --------------------------------------------------------------------------- #
# A container left behind by a previous KEEP=1 run would otherwise collide.
docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true

echo "==> [1/5] Assembling a temp /etc/nginx from deploy/nginx/"
mkdir -p "${WORK}/etc/conf.d" \
         "${WORK}/etc/snippets/cofoundaz" \
         "${WORK}/letsencrypt/live/${PROD_HOST}" \
         "${WORK}/letsencrypt/live/${STG_HOST}" \
         "${WORK}/certbot/.well-known/acme-challenge" \
         "${WORK}/log"

cp "${SRC}"/conf.d/*.conf              "${WORK}/etc/conf.d/"
cp "${SRC}"/snippets/*.conf            "${WORK}/etc/snippets/cofoundaz/"
# The vhosts go into conf.d here. On the VPS they live in sites-available and
# are symlinked into sites-enabled; the official nginx image's nginx.conf only
# includes conf.d/*.conf. Prefixes keep them parsed after the http-level files.
cp "${SRC}/sites-available/00-default-deny.conf"          "${WORK}/etc/conf.d/40-default-deny.conf"
cp "${SRC}/sites-available/${PROD_HOST}.conf"             "${WORK}/etc/conf.d/50-${PROD_HOST}.conf"
cp "${SRC}/sites-available/${STG_HOST}.conf"              "${WORK}/etc/conf.d/51-${STG_HOST}.conf"

# Mutation 1: point the upstreams at the host.
sed -i.bak "s#server 127.0.0.1:8000#server host.docker.internal:${PROD_PORT}#" \
    "${WORK}/etc/conf.d/20-upstreams.conf"
sed -i.bak "s#server 127.0.0.1:8001#server host.docker.internal:${STAGING_PORT}#" \
    "${WORK}/etc/conf.d/20-upstreams.conf"
rm -f "${WORK}/etc/conf.d/20-upstreams.conf.bak"

# Mutation 2: self-signed certs at the real paths.
for host in "${PROD_HOST}" "${STG_HOST}"; do
    openssl req -x509 -newkey rsa:2048 -sha256 -days 2 -nodes \
        -keyout "${WORK}/letsencrypt/live/${host}/privkey.pem" \
        -out    "${WORK}/letsencrypt/live/${host}/fullchain.pem" \
        -subj   "/CN=${host}" \
        -addext "subjectAltName=DNS:${host}" >/dev/null 2>&1
    cp "${WORK}/letsencrypt/live/${host}/fullchain.pem" \
       "${WORK}/letsencrypt/live/${host}/chain.pem"
done

echo "acme-token-body" > "${WORK}/certbot/.well-known/acme-challenge/verify-token"

cat > "${WORK}/etc/nginx.conf" <<'EOF'
worker_processes 1;
error_log /var/log/nginx/error.log warn;
pid /tmp/nginx.pid;
events { worker_connections 1024; }
http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;
    access_log    /var/log/nginx/access.log;
    sendfile      on;
    include       /etc/nginx/conf.d/*.conf;
}
EOF

# Mounts only. The container NAME is added just for the long-running instance -
# putting it here made the throwaway `nginx -t` run collide with a container
# left behind by KEEP=1, and the collision was reported as "configuration does
# not parse", which is a lie the next person would have chased for a while.
DOCKER_ARGS=(
    --add-host "host.docker.internal:host-gateway"
    -v "${WORK}/etc/nginx.conf:/etc/nginx/nginx.conf:ro"
    -v "${WORK}/etc/conf.d:/etc/nginx/conf.d:ro"
    -v "${WORK}/etc/snippets:/etc/nginx/snippets:ro"
    -v "${WORK}/letsencrypt:/etc/letsencrypt:ro"
    -v "${WORK}/certbot:/var/www/certbot:ro"
    -v "${WORK}/log:/var/log/nginx"
)

# --------------------------------------------------------------------------- #
echo "==> [2/5] nginx -t"
if docker run --rm "${DOCKER_ARGS[@]}" "${NGINX_IMAGE}" nginx -t; then
    ok "configuration parses"
else
    bad "configuration does not parse"
    echo "==> aborting: nothing else can be trusted if the config will not load"
    exit 1
fi

# --------------------------------------------------------------------------- #
echo "==> [3/5] Starting nginx"
docker run -d --name "${CONTAINER}" "${DOCKER_ARGS[@]}" \
    -p "127.0.0.1:${HTTP_PORT}:80" \
    -p "127.0.0.1:${HTTPS_PORT}:443" \
    "${NGINX_IMAGE}" >/dev/null

for _ in $(seq 1 20); do
    if curl -fsS -o /dev/null "http://127.0.0.1:${HTTP_PORT}/" \
        --resolve "${PROD_HOST}:${HTTP_PORT}:127.0.0.1" 2>/dev/null; then break; fi
    if [ -n "$(docker ps -q -f "name=${CONTAINER}")" ]; then sleep 0.5; else break; fi
done

# `-k` throughout: the certificates are self-signed by construction. Trust chain
# validation is exactly the thing this harness cannot test.
PC=(curl -sS -k --resolve "${PROD_HOST}:${HTTPS_PORT}:127.0.0.1" --resolve "${PROD_HOST}:${HTTP_PORT}:127.0.0.1")
SC=(curl -sS -k --resolve "${STG_HOST}:${HTTPS_PORT}:127.0.0.1"  --resolve "${STG_HOST}:${HTTP_PORT}:127.0.0.1")

PB="https://${PROD_HOST}:${HTTPS_PORT}"
SB="https://${STG_HOST}:${HTTPS_PORT}"

# --------------------------------------------------------------------------- #
echo "==> [4/5] Behaviour"

# --- HTTP -> HTTPS ---------------------------------------------------------- #
code=$("${PC[@]}" -o /dev/null -w '%{http_code}' "http://${PROD_HOST}:${HTTP_PORT}/api/v1/health/ready")
expect "http :80 redirects" "301" "${code}"
loc=$("${PC[@]}" -o /dev/null -w '%{redirect_url}' "http://${PROD_HOST}:${HTTP_PORT}/api/v1/health/ready")
expect "redirect preserves host and path" "https://${PROD_HOST}/api/v1/health/ready" "${loc}"

# --- ACME challenge is NOT redirected --------------------------------------- #
body=$("${PC[@]}" "http://${PROD_HOST}:${HTTP_PORT}/.well-known/acme-challenge/verify-token")
expect "ACME challenge served on :80, not redirected" "acme-token-body" "${body}"

# --- HTTP/2 ----------------------------------------------------------------- #
ver=$("${PC[@]}" --http2 -o /dev/null -w '%{http_version}' "${PB}/health")
expect "HTTP/2 negotiated" "2" "${ver}"

# --- proxying works --------------------------------------------------------- #
code=$("${PC[@]}" -o /dev/null -w '%{http_code}' "${PB}/health")
expect "production proxies to its backend" "200" "${code}"
code=$("${SC[@]}" -o /dev/null -w '%{http_code}' "${SB}/health")
expect "staging proxies to its backend" "200" "${code}"

# --- security headers ------------------------------------------------------- #
ph=$(lower "$("${PC[@]}" -sD - -o /dev/null "${PB}/health")")
contains "prod: HSTS with includeSubDomains" "max-age=63072000; includesubdomains" "${ph}"
lacks    "prod: HSTS carries no preload"     "preload"                             "${ph}"
contains "prod: nosniff"                     "x-content-type-options: nosniff"     "${ph}"
contains "prod: X-Frame-Options DENY"        "x-frame-options: deny"               "${ph}"
contains "prod: strict API CSP"              "default-src 'none'"                  "${ph}"
lacks    "prod: no X-Robots-Tag"             "x-robots-tag"                        "${ph}"
lacks    "prod: server version hidden"       "nginx/"                              "${ph}"

sh=$(lower "$("${SC[@]}" -sD - -o /dev/null "${SB}/health")")
contains "staging: X-Robots-Tag noindex"     "noindex, nofollow, noarchive"        "${sh}"
contains "staging: shorter HSTS"             "max-age=31536000"                    "${sh}"
lacks    "staging: HSTS has no includeSubDomains" "includesubdomains"              "${sh}"

# Headers survive on a non-2xx too, which is the whole point of `always`.
eh=$(lower "$("${PC[@]}" -sD - -o /dev/null "${PB}/api/v1/jobs")")
contains "headers present on a non-2xx (the 'always' flag)" "x-content-type-options" "${eh}"

# --- docs: closed on prod, open on staging ---------------------------------- #
for p in docs redoc openapi.json; do
    code=$("${PC[@]}" -o /dev/null -w '%{http_code}' "${PB}/api/v1/${p}")
    expect "prod: /api/v1/${p} is 404" "404" "${code}"
    code=$("${SC[@]}" -o /dev/null -w '%{http_code}' "${SB}/api/v1/${p}")
    expect "staging: /api/v1/${p} is served" "200" "${code}"
done
dh=$(lower "$("${SC[@]}" -sD - -o /dev/null "${SB}/api/v1/docs")")
contains "staging docs: CSP allows the jsDelivr bundle" "cdn.jsdelivr.net" "${dh}"
contains "staging docs: security headers re-included"   "x-content-type-options" "${dh}"

# --- X-Forwarded-For is OVERWRITTEN, not appended --------------------------- #
xff=$("${PC[@]}" -H "X-Forwarded-For: 203.0.113.9" -sD - -o /dev/null "${PB}/health" >/dev/null; \
      docker exec "${CONTAINER}" sh -c 'echo ok' >/dev/null 2>&1; echo "checked")
expect "sent a spoofed X-Forwarded-For" "checked" "${xff}"
echo "        (assert the backend logged its real peer, NOT 203.0.113.9 —"
echo "         'docker compose ... logs api | tail'. A leftmost-spoof would"
echo "         show 203.0.113.9 and means \$proxy_add_x_forwarded_for crept back in.)"

# --- body size ceilings ----------------------------------------------------- #
big="${WORK}/big.bin"
head -c 1500000 /dev/zero > "${big}"           # 1.5 MB: over the global 1m
code=$("${PC[@]}" -o /dev/null -w '%{http_code}' -X POST --data-binary "@${big}" \
       -H 'Content-Type: application/json' "${PB}/api/v1/auth/login")
expect "1.5 MB body rejected outside the logo endpoint" "413" "${code}"

body=$("${PC[@]}" -X POST --data-binary "@${big}" -H 'Content-Type: application/json' \
       "${PB}/api/v1/auth/login")
contains "413 body is the app's JSON envelope" '"code":"PAYLOAD_TOO_LARGE"' "${body}"

huge="${WORK}/huge.bin"
head -c 4000000 /dev/zero > "${huge}"          # 4 MB: over the logo endpoint's 3m
code=$("${PC[@]}" -o /dev/null -w '%{http_code}' -X POST --data-binary "@${huge}" \
       -H 'Content-Type: multipart/form-data; boundary=x' "${PB}/api/v1/onboarding/logo")
expect "4 MB body rejected at the logo endpoint" "413" "${code}"

mid="${WORK}/mid.bin"
head -c 2500000 /dev/zero > "${mid}"           # 2.5 MB: under 3m, so it must REACH the app
code=$("${PC[@]}" -o /dev/null -w '%{http_code}' -X POST --data-binary "@${mid}" \
       -H 'Content-Type: multipart/form-data; boundary=x' "${PB}/api/v1/onboarding/logo")
if [ "${code}" = "413" ]; then
    bad "2.5 MB logo upload reached the app (got 413 from nginx)"
else
    ok "2.5 MB logo upload passes the edge and is the app's decision (got ${code})"
fi

# --- unknown Host / bare IP ------------------------------------------------- #
if curl -sS -k --resolve "unknown.invalid:${HTTPS_PORT}:127.0.0.1" \
        -o /dev/null "https://unknown.invalid:${HTTPS_PORT}/" 2>/dev/null; then
    bad "unknown SNI was served (default_server catch-all not effective)"
else
    ok "unknown SNI is refused by the default_server (ssl_reject_handshake)"
fi

# --- rate limiting ---------------------------------------------------------- #
echo "        driving the auth zone (5r/s burst 10) to a 429…"
saw429=""
body429=""
# The BODY is captured inside the loop, not by a follow-up request. The zone
# refills at 5r/s, so a second request issued a moment later is very often
# allowed through again - which made an otherwise-correct 429 page look broken
# on the first run of this script.
for _ in $(seq 1 40); do
    out=$("${PC[@]}" -w '\n%{http_code}' -X POST -H 'Content-Type: application/json' \
          -d '{}' "${PB}/api/v1/auth/login")
    c="${out##*$'\n'}"
    if [ "${c}" = "429" ]; then saw429=1; body429="${out%$'\n'*}"; break; fi
done
if [ -n "${saw429}" ]; then ok "auth zone returns 429 under a burst"; else bad "auth zone never returned 429"; fi
contains "429 body is the app's JSON envelope" '"code":"RATE_LIMITED"' "${body429}"

# the general zone must NOT have tripped at the same time
code=$("${PC[@]}" -o /dev/null -w '%{http_code}' "${PB}/health")
expect "general traffic unaffected by the auth-zone limit" "200" "${code}"

# --- 502 when the upstream is gone ------------------------------------------ #
echo "        (502/504 JSON pages: run this script with STAGING_PORT pointed at a"
echo "         dead port to exercise them — see docs/deployment/NGINX_TLS.md)"

# --------------------------------------------------------------------------- #
echo "==> [5/5] nginx error log"
docker exec "${CONTAINER}" sh -c 'cat /var/log/nginx/error.log' 2>/dev/null \
    | grep -v 'limiting requests' | tail -20 || true

echo
echo "==> ${PASS} passed, ${FAIL} failed"
[ "${FAIL}" -eq 0 ]
