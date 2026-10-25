#!/bin/bash
# Run once. Lets other players' games reach the bot on this Mac through your DigitalOcean droplet:
# the Mac keeps an SSH connection open to the droplet, and the droplet passes port 8787 through it.
# Your website on the droplet is not touched.
cd "$(dirname "$0")"
DIR="$(pwd)"
LABEL="no.sindrens.autoprinter-relay"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PORT=8787

read -p "Droplet IP address (from the DigitalOcean dashboard): " IP
read -p "SSH user [root]: " USER_
USER_=${USER_:-root}
HOST="$USER_@$IP"

echo ""
echo "1/4  Checking that this Mac can log in to the droplet without a password..."
if ! ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new "$HOST" true 2>/dev/null; then
  [ -f "$HOME/.ssh/id_ed25519.pub" ] || ssh-keygen -t ed25519 -N "" -f "$HOME/.ssh/id_ed25519" -q
  echo "     Adding this Mac's key to the droplet (type the droplet's password once):"
  ssh-copy-id -i "$HOME/.ssh/id_ed25519.pub" -o StrictHostKeyChecking=accept-new "$HOST" || { echo "Couldn't log in. Check the IP and user."; read -n1; exit 1; }
fi

echo "2/4  Letting the droplet pass port $PORT through (sshd setting + firewall)..."
ssh "$HOST" "bash -s" <<REMOTE
set -e
SUDO=""; [ "\$(id -u)" = 0 ] || SUDO="sudo"
printf "GatewayPorts clientspecified\nClientAliveInterval 15\nClientAliveCountMax 3\n" | \$SUDO tee /etc/ssh/sshd_config.d/printer-addon-relay.conf >/dev/null
grep -q "^Include /etc/ssh/sshd_config.d" /etc/ssh/sshd_config || echo "GatewayPorts clientspecified" | \$SUDO tee -a /etc/ssh/sshd_config >/dev/null
\$SUDO systemctl reload ssh 2>/dev/null || \$SUDO systemctl reload sshd 2>/dev/null || \$SUDO service ssh reload
if command -v ufw >/dev/null && \$SUDO ufw status | grep -q "Status: active"; then \$SUDO ufw allow $PORT/tcp >/dev/null; echo "     ufw: port $PORT open"; fi
REMOTE

echo "3/4  Starting the relay (starts with the Mac, reconnects by itself)..."
mkdir -p logs "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>/usr/bin/ssh</string><string>-N</string>
    <string>-o</string><string>BatchMode=yes</string>
    <string>-o</string><string>ServerAliveInterval=20</string>
    <string>-o</string><string>ServerAliveCountMax=3</string>
    <string>-o</string><string>ExitOnForwardFailure=yes</string>
    <string>-R</string><string>0.0.0.0:$PORT:localhost:$PORT</string>
    <string>$HOST</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$DIR/logs/relay.log</string>
  <key>StandardErrorPath</key><string>$DIR/logs/relay.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
launchctl bootstrap "gui/$(id -u)" "$PLIST"
sleep 5

echo "4/4  Testing from the internet..."
URL="http://$IP:$PORT/api/v1"
echo "$URL" > relay.txt
if curl -s -m 10 "$URL/health" | grep -q '"ok": true'; then
  echo ""
  echo "✅ Done! Players' games reach the bot at: $URL"
else
  echo ""
  echo "⚠️  The relay runs, but $URL/health didn't answer."
  echo "   - Is the bot running? (install-autostart.command)"
  echo "   - Using a DigitalOcean Cloud Firewall? Add an inbound rule: TCP $PORT, all sources."
fi
echo ""
read -n1 -p "Press any key to close."
