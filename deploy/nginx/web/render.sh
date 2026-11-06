#!/usr/bin/env bash
# Render the web vhost + upstream for ONE environment into OUT_DIR.
# Writes files only; installs nothing. See docs/deployment/WEB_FRONTEND_EDGE.md.
#
#   WEB_ENV=staging WEB_SERVER_NAMES="web-staging.example.test" \
#   WEB_CERT_NAME=web-staging.example.test WEB_PORT=3001 \
#   OUT_DIR=/tmp/web-edge ./deploy/nginx/web/render.sh
#
# WEB_SERVER_NAMES are served by the app; WEB_REDIRECT_NAMES (optional) only
# 301 to the FIRST server name, e.g. apex -> www, so the browser only ever sees
# one origin (one CORS entry, one cookie host, one OAuth callback).
# WEB_CERT_NAME is the certbot --cert-name covering ALL of them. WEB_ROBOTS defaults to noindex for
# staging and empty for production.
#
# EDGE_LAYOUT=standalone (default): one self-contained site file for stock
#   nginx 1.24 - what the VPS runs today. Installs nothing else.
# EDGE_LAYOUT=full: the templates that build on the repo's full deploy/nginx/
#   layout (conf.d/*, snippets/cofoundaz/, nginx.org >= 1.25.1). Use only once
#   that layout is installed (docs/deployment/NGINX_TLS.md).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
EDGE_LAYOUT="${EDGE_LAYOUT:-standalone}"
case "${EDGE_LAYOUT}" in
  standalone|full) ;;
  *) echo "EDGE_LAYOUT must be standalone or full" >&2; exit 1 ;;
esac
: "${WEB_ENV:?staging|production}" "${WEB_SERVER_NAMES:?}" "${WEB_CERT_NAME:?}" "${WEB_PORT:?}" "${OUT_DIR:?}"
case "${WEB_ENV}" in
  staging) WEB_ROBOTS="${WEB_ROBOTS-noindex, nofollow, noarchive}" ;;
  production) WEB_ROBOTS="${WEB_ROBOTS-}" ;;
  *) echo "WEB_ENV must be staging or production" >&2; exit 1 ;;
esac
printf '%s' "${WEB_PORT}" | grep -qE '^[0-9]{2,5}$' || { echo "WEB_PORT must be a number" >&2; exit 1; }
printf '%s' "${WEB_SERVER_NAMES}" | grep -qE '^[A-Za-z0-9. -]+$' || { echo "WEB_SERVER_NAMES: host names only" >&2; exit 1; }
WEB_REDIRECT_NAMES="${WEB_REDIRECT_NAMES:-}"
[ -z "${WEB_REDIRECT_NAMES}" ] || printf '%s' "${WEB_REDIRECT_NAMES}" | grep -qE '^[A-Za-z0-9. -]+$' || { echo "WEB_REDIRECT_NAMES: host names only" >&2; exit 1; }
PRIMARY="${WEB_SERVER_NAMES%% *}"
printf '%s' "${WEB_CERT_NAME}" | grep -qE '^[A-Za-z0-9.-]+$' || { echo "WEB_CERT_NAME: one name" >&2; exit 1; }

redirect_block() {
  [ -n "${WEB_REDIRECT_NAMES}" ] || { echo "# (no redirect-only names)"; return; }
  if [ "${EDGE_LAYOUT}" = full ]; then
    LISTEN="    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;"
    REDIRECT_HEADERS="    include snippets/cofoundaz/web-security-headers.conf;"
  else
    LISTEN="    listen 443 ssl http2;
    listen [::]:443 ssl http2;"
    REDIRECT_HEADERS="    add_header Strict-Transport-Security \"max-age=31536000\" always;"
  fi
  cat <<BLOCK
# Redirect-only names -> https://${PRIMARY} (one browser origin).
server {
${LISTEN}
    server_name ${WEB_REDIRECT_NAMES};
    ssl_certificate         /etc/letsencrypt/live/${WEB_CERT_NAME}/fullchain.pem;
    ssl_certificate_key     /etc/letsencrypt/live/${WEB_CERT_NAME}/privkey.pem;
    ssl_trusted_certificate /etc/letsencrypt/live/${WEB_CERT_NAME}/chain.pem;
${REDIRECT_HEADERS}
    location / {
        return 301 https://${PRIMARY}\$request_uri;
    }
}
BLOCK
}

render() {
  sed -e "s|__WEB_ENV__|${WEB_ENV}|g" \
      -e "s|__WEB_SERVER_NAMES__|${WEB_SERVER_NAMES}|g" \
      -e "s|__WEB_CERT_NAME__|${WEB_CERT_NAME}|g" \
      -e "s|__WEB_PORT__|${WEB_PORT}|g" \
      -e "s|__WEB_REDIRECT_NAMES__|${WEB_REDIRECT_NAMES}|g" \
      -e "s|__WEB_PRIMARY__|${PRIMARY}|g" \
      -e "s|__WEB_ROBOTS__|${WEB_ROBOTS}|g" "$1" \
    | sed -e "/^# __WEB_REDIRECT_BLOCK__\$/{r ${BLOCK_FILE}" -e "d;}"

}
BLOCK_FILE="$(mktemp)"; trap 'rm -f "${BLOCK_FILE}"' EXIT
redirect_block > "${BLOCK_FILE}"
if [ "${EDGE_LAYOUT}" = standalone ]; then
  mkdir -p "${OUT_DIR}/sites-available"
  render "${HERE}/standalone/web-site.conf.template"      > "${OUT_DIR}/sites-available/web-${WEB_ENV}.conf"
  render "${HERE}/standalone/web-bootstrap.conf.template" > "${OUT_DIR}/sites-available/web-${WEB_ENV}-bootstrap.conf"
else
  mkdir -p "${OUT_DIR}/conf.d" "${OUT_DIR}/sites-available" "${OUT_DIR}/snippets/cofoundaz"
  render "${HERE}/web-upstream.conf.template"   > "${OUT_DIR}/conf.d/21-web-${WEB_ENV}-upstream.conf"
  render "${HERE}/web-site.conf.template"       > "${OUT_DIR}/sites-available/web-${WEB_ENV}.conf"
  render "${HERE}/web-bootstrap.conf.template"  > "${OUT_DIR}/sites-available/web-${WEB_ENV}-bootstrap.conf"
  cp "${HERE}/web-rate-limits.conf"             "${OUT_DIR}/conf.d/31-web-rate-limits.conf"
  cp "${HERE}/snippets/web-security-headers.conf" "${OUT_DIR}/snippets/cofoundaz/"
fi
if grep -rq '__WEB_' "${OUT_DIR}"; then echo "unrendered placeholder left" >&2; exit 1; fi
echo "Rendered ${WEB_ENV} (${EDGE_LAYOUT} layout) into ${OUT_DIR}:"
find "${OUT_DIR}" -type f | sort
