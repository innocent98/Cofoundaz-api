#!/usr/bin/env bash
# Render the web vhost + upstream for ONE environment into OUT_DIR.
# Writes files only; installs nothing. See docs/deployment/WEB_FRONTEND_EDGE.md.
#
#   WEB_ENV=staging WEB_SERVER_NAMES="web-staging.example.test" \
#   WEB_CERT_NAME=web-staging.example.test WEB_PORT=3001 \
#   OUT_DIR=/tmp/web-edge ./deploy/nginx/web/render.sh
#
# WEB_SERVER_NAMES may hold several names (e.g. apex + www); WEB_CERT_NAME is the
# certbot --cert-name covering all of them. WEB_ROBOTS defaults to noindex for
# staging and empty for production.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
: "${WEB_ENV:?staging|production}" "${WEB_SERVER_NAMES:?}" "${WEB_CERT_NAME:?}" "${WEB_PORT:?}" "${OUT_DIR:?}"
case "${WEB_ENV}" in
  staging) WEB_ROBOTS="${WEB_ROBOTS-noindex, nofollow, noarchive}" ;;
  production) WEB_ROBOTS="${WEB_ROBOTS-}" ;;
  *) echo "WEB_ENV must be staging or production" >&2; exit 1 ;;
esac
printf '%s' "${WEB_PORT}" | grep -qE '^[0-9]{2,5}$' || { echo "WEB_PORT must be a number" >&2; exit 1; }
printf '%s' "${WEB_SERVER_NAMES}" | grep -qE '^[A-Za-z0-9. -]+$' || { echo "WEB_SERVER_NAMES: host names only" >&2; exit 1; }
printf '%s' "${WEB_CERT_NAME}" | grep -qE '^[A-Za-z0-9.-]+$' || { echo "WEB_CERT_NAME: one name" >&2; exit 1; }

render() {
  sed -e "s|__WEB_ENV__|${WEB_ENV}|g" \
      -e "s|__WEB_SERVER_NAMES__|${WEB_SERVER_NAMES}|g" \
      -e "s|__WEB_CERT_NAME__|${WEB_CERT_NAME}|g" \
      -e "s|__WEB_PORT__|${WEB_PORT}|g" \
      -e "s|__WEB_ROBOTS__|${WEB_ROBOTS}|g" "$1"
}
mkdir -p "${OUT_DIR}/conf.d" "${OUT_DIR}/sites-available" "${OUT_DIR}/snippets/cofoundaz"
render "${HERE}/web-upstream.conf.template" > "${OUT_DIR}/conf.d/21-web-${WEB_ENV}-upstream.conf"
render "${HERE}/web-site.conf.template"     > "${OUT_DIR}/sites-available/web-${WEB_ENV}.conf"
render "${HERE}/web-bootstrap.conf.template" > "${OUT_DIR}/sites-available/web-${WEB_ENV}-bootstrap.conf"
cp "${HERE}/web-rate-limits.conf"            "${OUT_DIR}/conf.d/31-web-rate-limits.conf"
cp "${HERE}/snippets/web-security-headers.conf" "${OUT_DIR}/snippets/cofoundaz/"
if grep -rq '__WEB_' "${OUT_DIR}"; then echo "unrendered placeholder left" >&2; exit 1; fi
echo "Rendered ${WEB_ENV} into ${OUT_DIR}:"
find "${OUT_DIR}" -type f | sort
