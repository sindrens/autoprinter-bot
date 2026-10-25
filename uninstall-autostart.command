#!/bin/bash
# Stops the bot and removes the autostart. Double-click.
launchctl bootout "gui/$(id -u)/no.sindrens.autoprinter" 2>/dev/null
rm -f "$HOME/Library/LaunchAgents/no.sindrens.autoprinter.plist"
echo "Bot stopped and autostart removed."
sleep 2
