#!/usr/bin/env bash
# Restart the tracker if checks have silently stopped.
#
# Why this exists: on 2026-08-09 a Playwright call inside the scheduled price
# check hung indefinitely. APScheduler is configured max_instances=1, so the
# stuck job held the only slot and every subsequent run was skipped for two
# days. The web app kept serving normally, so nothing looked wrong.
#
# Rather than guess at every way Playwright can wedge, watch the outcome: if
# nothing has been checked recently, restart the service.
set -euo pipefail
APP_DIR="/home/ubuntu/cruise-price-tracker"
DB="$APP_DIR/data/tracker.db"
MAX_AGE_MIN="${MAX_AGE_MIN:-90}"   # ~3 missed 30-min cycles

[ -f "$DB" ] || exit 0

age_min=$("$APP_DIR/.venv/bin/python" - "$DB" <<'PY'
import sqlite3, sys
from datetime import datetime, timezone
con = sqlite3.connect(sys.argv[1])
row = con.execute("SELECT MAX(last_checked) FROM cruises WHERE active=1").fetchone()
if not row or not row[0]:
    print(99999); raise SystemExit
ts = row[0].split(".")[0]
last = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
print(int((datetime.now(timezone.utc) - last).total_seconds() // 60))
PY
)

if [ "$age_min" -gt "$MAX_AGE_MIN" ]; then
  logger -t cruise-watchdog "No successful check in ${age_min}m (limit ${MAX_AGE_MIN}m) — restarting cruise-tracker"
  systemctl restart cruise-tracker
else
  logger -t cruise-watchdog "OK — last check ${age_min}m ago"
fi
