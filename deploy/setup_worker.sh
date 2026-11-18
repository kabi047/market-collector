#!/usr/bin/env bash
# Run on an Ubuntu Always Free VM from the project directory.
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT_DIR="$PWD"
RUN_USER="$(id -un)"
if [ "$RUN_USER" = root ]; then
  echo 'Run as the regular SSH user, with sudo access.' >&2
  exit 1
fi
[ -f .env ] || { echo 'Copy the private .env file to the VM first.' >&2; exit 1; }
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip
python3 -m venv venv
venv/bin/pip install -r requirements.txt
mkdir -p state/logs data
chmod 600 .env
TOKEN_FROM_HF=0 venv/bin/python collect.py cloud-capture --slot probe
# Disable only legacy live/login cron commands for this exact project.
if command -v crontab >/dev/null; then
  (crontab -l 2>/dev/null || true) | awk -v dir="$PROJECT_DIR" \
    '!(index($0, dir) && ($0 ~ /collect.py live/ || $0 ~ /collect.py login/))' | crontab -
fi
sudo tee /etc/systemd/system/market-collector.service >/dev/null <<EOF
[Unit]
Description=Zerodha market recorder
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$PROJECT_DIR
Environment=TZ=Asia/Kolkata
Environment=TOKEN_FROM_HF=0
Environment=LIVE_SHIP_MINUTES=5
ExecStart="$PROJECT_DIR/venv/bin/python" -m deploy.cloud_worker
Restart=always
RestartSec=60
KillSignal=SIGINT
TimeoutStopSec=180
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now market-collector
sudo systemctl status market-collector --no-pager
