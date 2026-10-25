#!/bin/bash
# Double-click to start the Autoprinter bot.
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv && ./.venv/bin/pip install -q -r requirements.txt
fi
./.venv/bin/python bot.py
