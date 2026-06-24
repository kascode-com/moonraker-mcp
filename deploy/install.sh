#!/usr/bin/env bash
# Install / update the Moonraker MCP server on a Raspberry Pi (Debian-based).
# Run from a checkout of this repo on the Pi:  bash deploy/install.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="${INSTALL_DIR:-$HOME/moonraker-mcp}"
ENV_FILE="${ENV_FILE:-/etc/moonraker-mcp.env}"
SERVICE_NAME="moonraker-mcp.service"

echo ">> Repo:    $REPO_DIR"
echo ">> Install: $INSTALL_DIR"

# Sync source into the install dir (skip if running from there already).
if [ "$REPO_DIR" != "$INSTALL_DIR" ]; then
  mkdir -p "$INSTALL_DIR"
  cp -r "$REPO_DIR/." "$INSTALL_DIR/"
fi
cd "$INSTALL_DIR"

# Python venv + install.
if [ ! -d venv ]; then
  python3 -m venv venv
fi
./venv/bin/pip install --upgrade pip
./venv/bin/pip install .

# Seed the env file from the example on first install (never overwrite).
if [ ! -f "$ENV_FILE" ]; then
  echo ">> Creating $ENV_FILE from .env.example — edit it to set MOONRAKER_URL / MCP_AUTH_TOKEN."
  sudo cp "$INSTALL_DIR/.env.example" "$ENV_FILE"
  sudo chmod 600 "$ENV_FILE"
fi

# Install + (re)start the systemd unit.
sudo cp "$INSTALL_DIR/deploy/$SERVICE_NAME" "/etc/systemd/system/$SERVICE_NAME"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo ">> Done. Status:"
sudo systemctl --no-pager status "$SERVICE_NAME" || true
echo ">> Edit $ENV_FILE then: sudo systemctl restart $SERVICE_NAME"
