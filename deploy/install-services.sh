#!/usr/bin/env bash
# Install + enable the systemd services so the tracker and its tunnel
# start automatically on boot and restart on crash.
#
# Run ON THE VM, from the repo root:
#   bash deploy/install-services.sh
set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/cruise-price-tracker}"
NGROK_DOMAIN="${NGROK_DOMAIN:-recollect-stardom-carve.ngrok-free.dev}"
RUN_USER="$(id -un)"

if [[ ! -f "$APP_DIR/.env" ]]; then
  echo "ERROR: $APP_DIR/.env is missing — run deploy/sync-to-vm.sh from your Mac first."
  exit 1
fi

render_unit() {
  sed -e "s|__USER__|$RUN_USER|g" \
      -e "s|__APP_DIR__|$APP_DIR|g" \
      -e "s|__NGROK_DOMAIN__|$NGROK_DOMAIN|g" \
      "$1"
}

echo "==> Installing systemd units"
render_unit "$APP_DIR/deploy/cruise-tracker.service" | sudo tee /etc/systemd/system/cruise-tracker.service >/dev/null
render_unit "$APP_DIR/deploy/cruise-tunnel.service"  | sudo tee /etc/systemd/system/cruise-tunnel.service  >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable --now cruise-tracker.service
sudo systemctl enable --now cruise-tunnel.service

sleep 5
echo ""
echo "==> Status"
sudo systemctl --no-pager --lines=5 status cruise-tracker.service || true
sudo systemctl --no-pager --lines=5 status cruise-tunnel.service || true

echo ""
echo "==> Local health check"
curl -sf -o /dev/null -w "localhost:8765/api/health -> %{http_code}\n" http://127.0.0.1:8765/api/health \
  || echo "health check failed — see $APP_DIR/data/tracker-server.log"

echo ""
echo "Public URL: https://$NGROK_DOMAIN"
