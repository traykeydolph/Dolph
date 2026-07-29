#!/usr/bin/env bash
# Install the trading-bot systemd units on a Linux VPS.
# Run from the repo root (or anywhere) as the user who will OWN the bot:
#     sudo ./deploy/install.sh
#
# It templates USER + APP_DIR into the unit files, installs them, reloads
# systemd, and enables both the bot and the 5-minute health timer. It does NOT
# start the bot (do that after you've confirmed .env — see DEPLOY.md).
set -euo pipefail

# App dir = parent of this script's dir; owner = the invoking (non-root) user.
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="${SUDO_USER:-$USER}"
UNIT_DIR="/etc/systemd/system"
UNITS=(trading-bot.service trading-bot-health.service trading-bot-health.timer)

if [[ $EUID -ne 0 ]]; then
  echo "Please run with sudo (needs to write $UNIT_DIR)." >&2
  exit 1
fi

echo "App dir : $APP_DIR"
echo "Run user: $RUN_USER"

[[ -x "$APP_DIR/venv/bin/python" ]] || {
  echo "ERROR: $APP_DIR/venv/bin/python not found — create the venv first (see DEPLOY.md)." >&2
  exit 1
}

for unit in "${UNITS[@]}"; do
  sed -e "s|%APP_DIR%|$APP_DIR|g" -e "s|%USER%|$RUN_USER|g" \
      "$APP_DIR/deploy/$unit" > "$UNIT_DIR/$unit"
  echo "installed $UNIT_DIR/$unit"
done

systemctl daemon-reload
systemctl enable trading-bot.service trading-bot-health.timer
systemctl start trading-bot-health.timer

echo
echo "Installed + enabled. NOT started the bot yet."
echo "Next:  sudo systemctl start trading-bot     # launch"
echo "       journalctl -u trading-bot -f          # watch startup health check"
