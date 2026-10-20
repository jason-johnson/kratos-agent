#!/usr/bin/env bash
# kratos-agent e2e-smoke runner.
#
# Installs deps + chromium on first run (cached afterwards), resolves the target
# endpoints, then executes the Playwright suite. Extra args go to `playwright test`.
#
# Endpoints are NEVER hardcoded in this repo (it is public). Set KRATOS_URL to
# the AKS ingress origin, or provide separate frontend/backend URLs for a local
# or port-forwarded test.
#
# Usage:
#   KRATOS_URL=https://... ./run.sh
#   ./run.sh --grep "chat"         # filter to chat spec
#   SKIP_BROWSER=1 ./run.sh        # API-only mode (no chromium)
#   KRATOS_BACKEND_URL=https://...  KRATOS_FRONTEND_URL=https://... ./run.sh
set -euo pipefail

cd "$(dirname "$0")"
: "${KRATOS_FRONTEND_URL:=${KRATOS_URL:-}}"
: "${KRATOS_BACKEND_URL:=${KRATOS_URL:-}}"
export KRATOS_FRONTEND_URL KRATOS_BACKEND_URL

if [[ -z "${KRATOS_FRONTEND_URL:-}" || -z "${KRATOS_BACKEND_URL:-}" ]]; then
  echo "[e2e-smoke] ERROR: could not resolve target endpoints." >&2
  echo "  Export KRATOS_URL, or both KRATOS_FRONTEND_URL and KRATOS_BACKEND_URL." >&2
  exit 1
fi

if [[ ! -d node_modules/@playwright/test ]]; then
  echo "[e2e-smoke] installing npm dependencies …"
  if [[ -z "${PRIVATE_NPMRC:-}" || ! -f "$PRIVATE_NPMRC" ]]; then
    echo "[e2e-smoke] ERROR: PRIVATE_NPMRC must point to a private-feed npmrc file." >&2
    exit 1
  fi
  npm ci --userconfig "$PRIVATE_NPMRC" --no-audit --no-fund --silent
fi

if [[ -z "${SKIP_BROWSER:-}" ]]; then
  PW_BIN="./node_modules/.bin/playwright"
  if ! "$PW_BIN" install --dry-run chromium >/dev/null 2>&1; then
    echo "[e2e-smoke] installing chromium for Playwright …"
    "$PW_BIN" install chromium
  fi
fi

echo "[e2e-smoke] frontend  = ${KRATOS_FRONTEND_URL}"
echo "[e2e-smoke] backend   = ${KRATOS_BACKEND_URL}"
echo "[e2e-smoke] skip-browser = ${SKIP_BROWSER:-0}"

exec ./node_modules/.bin/playwright test "$@"
