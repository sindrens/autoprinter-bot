#!/bin/bash
# Double-click once: the Autoprinter bot starts with your Mac, restarts by itself if it crashes,
# and keeps the Mac awake while it runs (the screen can still turn off).
cd "$(dirname "$0")"
DIR="$(pwd)"
LABEL="no.sindrens.autoprinter"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

echo "Setting up the Autoprinter bot..."
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv || { echo "Python 3 is missing. Install it from python.org and try again."; read -n1; exit 1; }
fi
./.venv/bin/pip install -q --upgrade pip >/dev/null 2>&1
./.venv/bin/pip install -q -r requirements.txt || { echo "Couldn't install discord.py."; read -n1; exit 1; }
mkdir -p logs "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>/usr/bin/caffeinate</string><string>-is</string>
    <string>$DIR/.venv/bin/python</string><string>-u</string><string>$DIR/bot.py</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>15</integer>
  <key>StandardOutPath</key><string>$DIR/logs/bot.log</string>
  <key>StandardErrorPath</key><string>$DIR/logs/bot.log</string>
</dict></plist>
PL

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart -k "gui/$(id -u)/$LABEL"
sleep 6
if launchctl print "gui/$(id -u)/$LABEL" | grep -q "state = running"; then
  echo ""
  echo "✅ The bot is running and starts by itself when you log in."
  echo "   Log: $DIR/logs/bot.log"
  echo "   Restart after changes: double-click restart-bot.command"
else
  echo "⚠️  The bot didn't start. Last lines of the log:"
  tail -20 logs/bot.log
fi
echo ""
read -n1 -p "Press any key to close."
