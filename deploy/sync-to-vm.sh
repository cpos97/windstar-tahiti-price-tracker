#!/usr/bin/env bash
# Copy secrets + existing data from this Mac to the VM.
#
# These files are gitignored on purpose (credentials, saved login cookies,
# price history), so they can't come down via `git clone` — they have to be
# copied directly.
#
# Run ON YOUR MAC, from the repo root:
#   VM=ubuntu@<vm-public-ip> bash deploy/sync-to-vm.sh
set -euo pipefail

: "${VM:?Set VM=ubuntu@<vm-public-ip>}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE_DIR="${REMOTE_DIR:-cruise-price-tracker}"
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)"

SSH_OPTS=(-i "$SSH_KEY")

echo "==> Ensuring remote data dir exists"
ssh "${SSH_OPTS[@]}" "$VM" "mkdir -p ~/$REMOTE_DIR/data"

echo "==> Copying .env (credentials)"
scp "${SSH_OPTS[@]}" "$LOCAL_DIR/.env" "$VM:~/$REMOTE_DIR/.env"

echo "==> Copying saved browser login session (if present)"
if [[ -f "$LOCAL_DIR/data/browser_session.json" ]]; then
  scp "${SSH_OPTS[@]}" "$LOCAL_DIR/data/browser_session.json" "$VM:~/$REMOTE_DIR/data/browser_session.json"
else
  echo "   (none found — you'll need to log in on the VM later)"
fi

echo "==> Copying price history database (if present)"
if [[ -f "$LOCAL_DIR/data/tracker.db" ]]; then
  scp "${SSH_OPTS[@]}" "$LOCAL_DIR/data/tracker.db" "$VM:~/$REMOTE_DIR/data/tracker.db"
else
  echo "   (none found — a fresh one will be created)"
fi

echo "==> Locking down permissions on the VM"
ssh "${SSH_OPTS[@]}" "$VM" "chmod 600 ~/$REMOTE_DIR/.env ~/$REMOTE_DIR/data/browser_session.json 2>/dev/null || true"

echo ""
echo "Done. Next, on the VM:"
echo "  ngrok config add-authtoken <YOUR_NGROK_TOKEN>"
echo "  cd ~/$REMOTE_DIR && bash deploy/install-services.sh"
