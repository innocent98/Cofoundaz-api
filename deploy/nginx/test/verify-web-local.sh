#!/usr/bin/env bash
#
# Local validation for the WEB frontend vhosts (deploy/nginx/web/) - no VPS,
# no DNS, no Let's Encrypt. Companion to verify-local.sh (the API vhosts).
#
# Renders BOTH web environments with placeholder host names, assembles them with
# the existing conf.d + snippets, runs `nginx -t`, then drives assertions against
# a real nginx in front of a real web container (the image built from the
# cofoundaz repo's Dockerfile).
#
# USAGE
#   WEB_IMAGE=cofoundaz-web:local ./deploy/nginx/test/verify-web-local.sh
#   EDGE_LAYOUT=full ...   # the full deploy/nginx layout on nginx 1.27
#
# Default EDGE_LAYOUT=standalone runs on nginx 1.24 (Ubuntu's stock version on
# the VPS) with ONLY the two rendered site files - no conf.d, no snippets - to
# prove the standalone files really stand alone.
#
# Placeholder hosts only (*.example.test); real names come from the runbook.
set -euo pipefail

EDGE_LAYOUT="${EDGE_LAYOUT:-standalone}"
if [ "${EDGE_LAYOUT}" = standalone ]; then
    NGINX_IMAGE="${NGINX_IMAGE:-nginx:1.24-alpine}"
else
    NGINX_IMAGE="${NGINX_IMAGE:-nginx:1.27-alpine}"
fi
export EDGE_LAYOUT
WEB_IMAGE="${WEB_IMAGE:-cofoundaz-web:local}"
WEB_PORT="${WEB_PORT:-3911}"
HTTP_PORT="${HTTP_PORT:-18180}"
HTTPS_PORT="${HTTPS_PORT:-18543}"
NGINX_CT="cofoundaz-web-nginx-verify"
WEB_CT="cofoundaz-web-backend-verify"
STG="web-staging.example.test"
PROD="web.example.test"
PROD_ALT="www.web.example.test"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$(dirname "${HERE}")"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/cofoundaz-web-verify.XXXXXX")"
PASS=0; FAIL=0

cleanup() {
    docker rm -f "${NGINX_CT}" "${WEB_CT}" >/dev/null 2>&1 || true
    rm -rf "${WORK}"
}
trap cleanup EXIT
ok()  { PASS=$((PASS + 1)); printf '  PASS  %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  FAIL  %s\n' "$1"; }
expect()   { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (expected '$2', got '$3')"; fi; }
contains() { case "$3" in *"$2"*) ok "$1" ;; *) bad "$1 (missing '$2')" ;; esac; }
lacks()    { case "$3" in *"$2"*) bad "$1 (unexpectedly has '$2')" ;; *) ok "$1" ;; esac; }
lower()    { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

docker rm -f "${NGINX_CT}" "${WEB_CT}" >/dev/null 2>&1 || true

echo "==> [1/4] Rendering both web environments + assembling /etc/nginx"
mkdir -p "${WORK}/etc/conf.d" "${WORK}/etc/snippets/cofoundaz" "${WORK}/certbot/.well-known/acme-challenge" "${WORK}/log"
WEB_ENV=staging WEB_SERVER_NAMES="${STG}" WEB_CERT_NAME="${STG}" WEB_PORT="${WEB_PORT}" \
    OUT_DIR="${WORK}/r-stg" "${SRC}/web/render.sh" >/dev/null
# Production as recommended: www serves, apex only redirects to it.
WEB_ENV=production WEB_SERVER_NAMES="${PROD_ALT}" WEB_REDIRECT_NAMES="${PROD}" WEB_CERT_NAME="${PROD}" WEB_PORT="${WEB_PORT}" \
    OUT_DIR="${WORK}/r-prod" "${SRC}/web/render.sh" >/dev/null
if [ "${EDGE_LAYOUT}" = full ]; then
    cp "${SRC}"/conf.d/*.conf "${WORK}/etc/conf.d/"
    cp "${SRC}"/snippets/*.conf "${WORK}/etc/snippets/cofoundaz/"
    for r in r-stg r-prod; do
        cp "${WORK}/${r}"/conf.d/*.conf "${WORK}/etc/conf.d/"
        cp "${WORK}/${r}"/snippets/cofoundaz/*.conf "${WORK}/etc/snippets/cofoundaz/"
    done
fi
cp "${WORK}/r-stg/sites-available/web-staging.conf"     "${WORK}/etc/conf.d/60-web-staging.conf"
cp "${WORK}/r-prod/sites-available/web-production.conf" "${WORK}/etc/conf.d/61-web-production.conf"
# Mutation: the upstream is the host, not the nginx container's own loopback.
sed -i.bak "s#server 127.0.0.1:${WEB_PORT}#server host.docker.internal:${WEB_PORT}#" "${WORK}"/etc/conf.d/*.conf
rm -f "${WORK}"/etc/conf.d/*.bak
for h in "${STG}" "${PROD}"; do
    mkdir -p "${WORK}/letsencrypt/live/${h}"
    SAN="DNS:${h}"; [ "${h}" = "${PROD}" ] && SAN="${SAN},DNS:${PROD_ALT}"
    openssl req -x509 -newkey rsa:2048 -sha256 -days 2 -nodes \
        -keyout "${WORK}/letsencrypt/live/${h}/privkey.pem" -out "${WORK}/letsencrypt/live/${h}/fullchain.pem" \
        -subj "/CN=${h}" -addext "subjectAltName=${SAN}" >/dev/null 2>&1
    cp "${WORK}/letsencrypt/live/${h}/fullchain.pem" "${WORK}/letsencrypt/live/${h}/chain.pem"
done
echo "acme-token-body" > "${WORK}/certbot/.well-known/acme-challenge/verify-token"
cat > "${WORK}/etc/nginx.conf" <<'NGX'
worker_processes 1;
error_log /var/log/nginx/error.log warn;
pid /tmp/nginx.pid;
events { worker_connections 1024; }
http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;
    sendfile      on;
    include       /etc/nginx/conf.d/*.conf;
}
NGX
ARGS=(--add-host "host.docker.internal:host-gateway"
      -v "${WORK}/etc/nginx.conf:/etc/nginx/nginx.conf:ro" -v "${WORK}/etc/conf.d:/etc/nginx/conf.d:ro"
      -v "${WORK}/etc/snippets:/etc/nginx/snippets:ro" -v "${WORK}/letsencrypt:/etc/letsencrypt:ro"
      -v "${WORK}/certbot:/var/www/certbot:ro" -v "${WORK}/log:/var/log/nginx")

echo "==> [2/4] nginx -t (${EDGE_LAYOUT} layout on ${NGINX_IMAGE})"
docker run --rm "${ARGS[@]}" "${NGINX_IMAGE}" nginx -t > "${WORK}/t.log" 2>&1 || true; grep -E "warn|emerg" "${WORK}/t.log" || true
if grep -q "test is successful" "${WORK}/t.log"; then ok "configuration parses"; else bad "configuration does not parse"; exit 1; fi
# ssl_stapling warnings are expected: self-signed certs carry no OCSP URL.
lacks "no nginx warnings (besides self-signed OCSP)" "[warn]" "$(grep -v 'ssl_stapling' "${WORK}/t.log" || true)"

# Bootstrap (first-cert) sites must load with NO certificates present.
mkdir -p "${WORK}/boot/conf.d"
[ "${EDGE_LAYOUT}" = full ] && cp "${SRC}"/conf.d/10-hardening.conf "${WORK}/boot/conf.d/"
cp "${WORK}/r-stg/sites-available/web-staging-bootstrap.conf" "${WORK}/r-prod/sites-available/web-production-bootstrap.conf" "${WORK}/boot/conf.d/"
if docker run --rm -v "${WORK}/etc/nginx.conf:/etc/nginx/nginx.conf:ro" -v "${WORK}/boot/conf.d:/etc/nginx/conf.d:ro" \
     -v "${WORK}/etc/snippets:/etc/nginx/snippets:ro" "${NGINX_IMAGE}" nginx -t >/dev/null 2>&1; then
    ok "bootstrap sites parse without any certificate"
else
    bad "bootstrap sites do not parse"
fi

echo "==> [3/4] Starting web (${WEB_IMAGE}) on 127.0.0.1:${WEB_PORT} and nginx"
docker run -d --name "${WEB_CT}" -p "127.0.0.1:${WEB_PORT}:3000" --read-only \
    --tmpfs /tmp --tmpfs /app/.next/cache:uid=1000,gid=1000 "${WEB_IMAGE}" >/dev/null
docker run -d --name "${NGINX_CT}" "${ARGS[@]}" -p "${HTTP_PORT}:80" -p "${HTTPS_PORT}:443" "${NGINX_IMAGE}" >/dev/null
for _ in $(seq 1 30); do curl -fs "http://127.0.0.1:${WEB_PORT}/healthz" >/dev/null 2>&1 && break; sleep 1; done

S() { curl -sk --resolve "$1:${HTTPS_PORT}:127.0.0.1" "${@:2}"; }

echo "==> [4/4] Assertions"
expect "apex https -> 301 to www" "301 https://${PROD_ALT}/x?y=1" \
    "$(S "${PROD}" -o /dev/null -w '%{http_code} %{redirect_url}' "https://${PROD}:${HTTPS_PORT}/x?y=1" | sed "s#:${HTTPS_PORT}##")"
for h in "${PROD}" ""; do [ -n "${h}" ] || continue  # apex: redirect-only, still answers ACME on :80
    expect "${h}: http -> 301" 301 "$(curl -s --resolve "${h}:${HTTP_PORT}:127.0.0.1" -o /dev/null -w '%{http_code}' "http://${h}:${HTTP_PORT}/x")"
    expect "${h}: ACME challenge served over http" "acme-token-body" \
        "$(curl -s --resolve "${h}:${HTTP_PORT}:127.0.0.1" "http://${h}:${HTTP_PORT}/.well-known/acme-challenge/verify-token")"
done
for h in "${STG}" "${PROD_ALT}"; do
    expect "${h}: /healthz via nginx+TLS -> 200" 200 "$(S "${h}" -o /dev/null -w '%{http_code}' "https://${h}:${HTTPS_PORT}/healthz")"
    expect "${h}: / -> 200" 200 "$(S "${h}" -o /dev/null -w '%{http_code}' "https://${h}:${HTTPS_PORT}/")"
    expect "${h}: http -> 301" 301 "$(curl -s --resolve "${h}:${HTTP_PORT}:127.0.0.1" -o /dev/null -w '%{http_code}' "http://${h}:${HTTP_PORT}/x")"
    expect "${h}: ACME challenge served over http" "acme-token-body" \
        "$(curl -s --resolve "${h}:${HTTP_PORT}:127.0.0.1" "http://${h}:${HTTP_PORT}/.well-known/acme-challenge/verify-token")"
done
HS="$(lower "$(S "${STG}" -D - -o /dev/null "https://${STG}:${HTTPS_PORT}/")")"
HP="$(lower "$(S "${PROD_ALT}" -D - -o /dev/null "https://${PROD_ALT}:${HTTPS_PORT}/")")"
contains "h2 negotiated" "http/2 200" "${HP}"
contains "nosniff" "x-content-type-options: nosniff" "${HP}"
contains "frame sameorigin" "x-frame-options: sameorigin" "${HP}"
contains "referrer strict-origin" "referrer-policy: strict-origin-when-cross-origin" "${HP}"
contains "hsts" "strict-transport-security: max-age=" "${HP}"
lacks "no COOP (would break OAuth/payment popups)" "cross-origin-opener-policy" "${HP}"
contains "staging is noindex" "x-robots-tag: noindex" "${HS}"
lacks "production has no x-robots-tag" "x-robots-tag" "${HP}"
lacks "server version hidden" "server: nginx/" "${HP}"
ASSET="$(S "${PROD_ALT}" "https://${PROD_ALT}:${HTTPS_PORT}/" | grep -oE '/_next/static/[^"]+\.js' | head -1)"
if [ -n "${ASSET}" ]; then
    HA="$(lower "$(S "${PROD_ALT}" -D - -o /dev/null -H 'Accept-Encoding: gzip' "https://${PROD_ALT}:${HTTPS_PORT}${ASSET}")")"
    contains "static asset 200" "http/2 200" "${HA}"
    contains "static asset immutable" "immutable" "${HA}"
    contains "static asset gzipped" "content-encoding: gzip" "${HA}"
else
    bad "no /_next/static asset found in the home page"
fi
expect "TRACE -> 405" 405 "$(S "${PROD_ALT}" -o /dev/null -w '%{http_code}' -X TRACE "https://${PROD_ALT}:${HTTPS_PORT}/")"
docker stop "${WEB_CT}" >/dev/null
expect "backend down -> 502" 502 "$(S "${PROD_ALT}" -o /dev/null -w '%{http_code}' "https://${PROD_ALT}:${HTTPS_PORT}/healthz")"

echo
echo "passed ${PASS}, failed ${FAIL}"
[ "${FAIL}" -eq 0 ]
