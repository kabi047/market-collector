#!/usr/bin/env bash
# One-time setup on a fresh Ubuntu server (Oracle Cloud Always Free, etc.). Run from the project folder:
#   bash deploy/setup_server.sh
set -euo pipefail
cd "$(dirname "$0")/.."

sudo timedatectl set-timezone Asia/Kolkata
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip cron util-linux

# small machines: add 4 GB swap so end-of-day compaction never runs out of memory
if [ "$(free -m | awk '/Mem:/{print $2}')" -lt 6000 ] && [ ! -f /swapfile ]; then
  sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi

python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
mkdir -p state/logs data

[ -f .env ] || { cp .env.example .env; echo ">>> Fill in .env (nano .env) before the first run."; }
chmod 600 .env

(crontab -l 2>/dev/null | grep -v 'collect.py' || true; sed "s|__DIR__|$PWD|g" deploy/crontab.txt | grep -v '^#') | crontab -
echo "cron installed:"; crontab -l | grep collect.py
echo
echo "Next:  venv/bin/python collect.py login --auto && venv/bin/python collect.py universe"
echo "Then:  nohup venv/bin/python collect.py historical > state/logs/historical_first.log 2>&1 &"
