#!/bin/bash
# Restarts the bot (after code changes). Double-click.
launchctl kickstart -k "gui/$(id -u)/no.sindrens.autoprinter" && echo "✅ Bot restarted." || echo "Not installed: run install-autostart.command first."
sleep 2
