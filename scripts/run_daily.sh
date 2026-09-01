#!/usr/bin/env bash
# Daily collection, intended for cron.
#
# The archive is only survivorship-bias-free if it is actually written every
# session, so this script is deliberately boring: it logs, it retries nothing
# clever, and it exits non-zero loudly enough for cron mail to notice.
#
# Suggested crontab (runs at 17:15 America/New_York, ~75 min after the close so
# consolidated volume has settled):
#
#   CRON_TZ=America/New_York
#   15 17 * * 1-5 /path/to/repo/scripts/run_daily.sh >> /path/to/repo/logs/cron.log 2>&1

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

if [[ -f .env ]]; then
    # shellcheck disable=SC1091
    set -a && source .env && set +a
fi

if [[ -z "${POLYGON_API_KEY:-}" ]]; then
    echo "POLYGON_API_KEY is not set (put it in .env or the cron environment)" >&2
    exit 1
fi

PYTHON="${PYTHON:-python3}"
echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) collecting $(date +%F) ==="
exec "$PYTHON" -m warrior_screener collect --date today "$@"
