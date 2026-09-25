#!/usr/bin/env bash
# One-time setup on a Debian/Ubuntu server. Run from anywhere: bash deploy/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."

sudo apt-get update
sudo apt-get install -y python3-venv python3-pip git sqlite3

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .
mkdir -p data

sed "s|__USER__|$USER|g; s|__DIR__|$PWD|g" deploy/expense-tracker.service \
  | sudo tee /etc/systemd/system/expense-tracker.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable expense-tracker

echo
echo "Setup done. Now upload these private files into $PWD:"
echo "  .env  credentials.json  data/token.json  data/tracker.db"
echo "Then start the bot:  sudo systemctl start expense-tracker"
echo "Logs:                journalctl -u expense-tracker -f"
