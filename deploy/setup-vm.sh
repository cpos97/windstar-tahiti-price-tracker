#!/usr/bin/env bash
# One-time setup for an Oracle Cloud (or any Ubuntu ARM64/x86) always-free VM.
#
# Run ON THE VM as the default user (e.g. `ubuntu`):
#   bash setup-vm.sh
#
# Installs Python + Playwright Chromium + system deps, clones the repo,
# and creates the venv. Secrets are NOT handled here — copy those over
# separately with deploy/sync-to-vm.sh from your Mac.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/cpos97/windstar-tahiti-price-tracker.git}"
APP_DIR="${APP_DIR:-$HOME/cruise-price-tracker}"

echo "==> Installing system packages"
sudo apt-get update -y
sudo apt-get install -y \
  git curl unzip \
  python3 python3-venv python3-pip \
  sqlite3

echo "==> Cloning repo into $APP_DIR"
if [[ -d "$APP_DIR/.git" ]]; then
  git -C "$APP_DIR" pull --ff-only
else
  git clone "$REPO_URL" "$APP_DIR"
fi

cd "$APP_DIR"

echo "==> Creating virtualenv"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Installing Playwright Chromium + OS dependencies"
# --with-deps pulls the shared libs headless Chromium needs on a bare server
playwright install --with-deps chromium

echo "==> Installing ngrok"
if ! command -v ngrok >/dev/null 2>&1; then
  ARCH="$(uname -m)"
  case "$ARCH" in
    aarch64|arm64) NGROK_ARCH="arm64" ;;
    x86_64)        NGROK_ARCH="amd64" ;;
    *) echo "Unsupported arch: $ARCH"; exit 1 ;;
  esac
  curl -sLo /tmp/ngrok.tgz "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-${NGROK_ARCH}.tgz"
  sudo tar -xzf /tmp/ngrok.tgz -C /usr/local/bin ngrok
  rm -f /tmp/ngrok.tgz
fi
ngrok version

mkdir -p "$APP_DIR/data"

echo ""
echo "=============================================================="
echo " Base setup complete."
echo ""
echo " Still to do (from your Mac):"
echo "   1. Run deploy/sync-to-vm.sh to copy .env + saved login session"
echo "   2. Run: ngrok config add-authtoken <YOUR_TOKEN>   (on the VM)"
echo "   3. Install the services:  bash deploy/install-services.sh"
echo "=============================================================="
