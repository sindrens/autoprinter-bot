#!/bin/bash
# Run once: moves the bot from this Mac to the DigitalOcean droplet, where it runs 24/7.
# Afterwards this Mac only copies code changes and new mod versions to it (sync.py), when it's on.
cd "$(dirname "$0")"
DIR="$(pwd)"
IP=139.59.156.198
HOST="root@$IP"
DEST=/opt/autoprinter
API="http://$IP:8787/api/v1"

echo "1/6  Checking the login to the droplet..."
ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" true || { echo "Can't log in to $HOST without a password. Run setup-relay.command first."; read -n1; exit 1; }

echo "2/6  Settings..."
grep -q "^ADMIN_KEY=" .env || echo "ADMIN_KEY=$(openssl rand -hex 24)" >> .env
sed -i '' '/^DROPLET=/d;/^DROPLET_API=/d' .env
echo "DROPLET=$HOST" >> .env
echo "DROPLET_API=$API" >> .env

echo "3/6  Stopping the bot and the relay on this Mac..."
for L in no.sindrens.autoprinter no.sindrens.autoprinter-relay; do
  launchctl bootout "gui/$(id -u)/$L" 2>/dev/null
  rm -f "$HOME/Library/LaunchAgents/$L.plist"
done
sleep 2

echo "4/6  Copying the bot and its data to the droplet..."
ssh "$HOST" "mkdir -p $DEST"
rsync -az --exclude ".venv" --exclude "logs" --exclude "__pycache__" --exclude "backups/reports*" --exclude "test_*" \
  --exclude ".git" "$DIR/" "$HOST:$DEST/" || { echo "Copy failed."; read -n1; exit 1; }

echo "5/6  Setting up the droplet (takes a minute)..."
ssh "$HOST" "bash $DEST/droplet-setup.sh" || { echo "Setup on the droplet failed (see above)."; read -n1; exit 1; }

echo "6/6  Keeping code and new versions in sync from this Mac..."
mkdir -p logs "$HOME/Library/LaunchAgents"
PLIST="$HOME/Library/LaunchAgents/no.sindrens.autoprinter-sync.plist"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>no.sindrens.autoprinter-sync</string>
  <key>ProgramArguments</key><array><string>/usr/bin/python3</string><string>-u</string><string>$DIR/sync.py</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DIR/logs/sync.log</string>
  <key>StandardErrorPath</key><string>$DIR/logs/sync.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/no.sindrens.autoprinter-sync" 2>/dev/null
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo ""
echo "Testing..."
for i in $(seq 1 30); do
  if curl -s -m 5 "$API/health" | grep -q '"ok": true'; then
    echo ""
    echo "✅ The bot runs on the droplet now, 24/7, also when this Mac is off."
    echo "   Its log: ssh $HOST tail -f $DEST/logs/bot.log"
    read -n1 -p "Press any key to close."; exit 0
  fi
  sleep 3
done
echo "⚠️  No answer yet. Check: ssh $HOST tail -30 $DEST/logs/bot.log"
read -n1 -p "Press any key to close."
