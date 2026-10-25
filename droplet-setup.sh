#!/bin/bash
# Runs ON the droplet (move-to-droplet.command starts it). Sets up the bot as a service that always runs.
set -e
DEST=/opt/autoprinter
cd "$DEST"
echo "   installing Python..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null && apt-get install -y -qq python3-venv python3-pip rsync >/dev/null
sed -i '/^API_HOST=/d' .env && echo "API_HOST=0.0.0.0" >> .env
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip >/dev/null
.venv/bin/pip install -q -r requirements.txt
mkdir -p logs
cat > /etc/systemd/system/autoprinter.service <<UNIT
[Unit]
Description=Autoprinter - the Printer Addon Discord bot
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$DEST
ExecStart=$DEST/.venv/bin/python -u bot.py
Restart=always
RestartSec=5
StandardOutput=append:$DEST/logs/bot.log
StandardError=append:$DEST/logs/bot.log

[Install]
WantedBy=multi-user.target
UNIT
echo "   waiting for port 8787 to be free..."
for i in $(seq 1 20); do ss -ltn "sport = :8787" | grep -q 8787 || break; sleep 1; done
PIDS=$(ss -ltnp "sport = :8787" | grep -o "pid=[0-9]*" | cut -d= -f2 | sort -u)
[ -n "$PIDS" ] && kill $PIDS 2>/dev/null || true
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then ufw allow 8787/tcp >/dev/null; fi
systemctl daemon-reload
systemctl enable autoprinter >/dev/null 2>&1
systemctl restart autoprinter
echo "   service started"
