#!/usr/bin/env bash
# Start the dashboard + a free public HTTPS tunnel (works off your home Wi‑Fi).
# Your Mac must stay awake and online.
#
# Usage:
#   cd ~/cruise-price-tracker
#   ./scripts/start-remote-access.sh
#
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p data

if [[ ! -x "$HOME/.local/bin/ngrok" ]]; then
  echo "ngrok not found at ~/.local/bin/ngrok"
  exit 1
fi

NGROK_DOMAIN="${NGROK_DOMAIN:-recollect-stardom-carve.ngrok-free.dev}"

# Start app if needed
if ! lsof -ti :8765 >/dev/null 2>&1; then
  echo "Starting tracker on port 8765…"
  # shellcheck disable=SC1091
  source .venv/bin/activate
  HOST=0.0.0.0 PORT=8765 nohup python run.py > data/tracker-server.log 2>&1 &
  sleep 2
fi

# Restart tunnel
if pgrep -x ngrok >/dev/null 2>&1; then
  pkill -x ngrok || true
  sleep 1
fi

echo "Starting ngrok public tunnel (static domain)…"
: > data/ngrok-tunnel.log
nohup ngrok http --url="$NGROK_DOMAIN" 8765 > data/ngrok-tunnel.log 2>&1 &
sleep 4

URL="https://$NGROK_DOMAIN"

# Confirm the tunnel actually came up
ok=""
for _ in $(seq 1 15); do
  if curl -sf -o /dev/null --max-time 3 "$URL/api/health" 2>/dev/null; then
    ok="1"
    break
  fi
  sleep 1
done

if [[ -z "$ok" ]]; then
  echo "Tunnel did not come up. See data/ngrok-tunnel.log"
  exit 1
fi

echo ""
echo "=============================================="
echo "  Remote access URL (phone / other Wi‑Fi):"
echo "  $URL   (this link never changes)"
echo "=============================================="
echo ""
echo "Your Mac must stay awake for this link to work."
echo "Local: http://127.0.0.1:8765"
echo ""

# Update DASHBOARD_URL for invite emails (only needed once, but harmless to repeat)
if [[ -f .env ]]; then
  python3 - "$URL" <<'PY'
import sys
from pathlib import Path
url = sys.argv[1].rstrip("/") + "/"
p = Path(".env")
lines = []
for line in p.read_text().splitlines():
    if line.startswith("DASHBOARD_URL="):
        lines.append(f"DASHBOARD_URL={url}")
    else:
        lines.append(line)
if not any(l.startswith("DASHBOARD_URL=") for l in lines):
    lines.append(f"DASHBOARD_URL={url}")
p.write_text("\n".join(lines) + "\n")
print("Updated DASHBOARD_URL in .env →", url)
PY
fi
