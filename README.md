# Autoprinter bot

**Run it on this Mac (always on):** double-click `install-autostart.command` once. The bot then starts when you log in,
restarts by itself if it crashes, and keeps the Mac awake while it runs. `restart-bot.command` after changes,
`uninstall-autostart.command` to remove it. Log: `logs/bot.log`. (`start.command` runs it once in a window, for testing.)

**Releases are automatic:** bump `mod_version`, add `## 26.2-<version>` to CHANGELOG.md and build the jar.
Within 2 minutes the bot posts the release card, changelog, jar (old jar → #old-versions), a note in #general and
pings the Updates role. `/release-check` checks right away.

| File | What |
|---|---|
| `.env` | Bot token (never share) |
| `bot.py` | The bot |
| `setup_server.py` | Roles, channels, permissions, settings (safe to re-run) |
| `repost_info.py` | Rebuilds the info channels from the backup |
| `state.json` | Channel and role IDs |
| `messages.json` | IDs of the bot's info posts |
| `data.json` | Suggestions and bug reports |
| `backups/`, `assets/` | Full backup, pictures and old jars |

## Commands
Everyone: /help, /suggest, /bug, /faq, /docs, /stats, /leaderboard, /achievements
Your game: /link, /status, /pause, /resume, /stop, /alerts, /logs, /unlink
Staff: /suggestion-status, /bug-status, /timeout, /kick, /ban, /purge
Developer: /release, /post, /edit

## Player API
The bot also runs a small web API (port `API_PORT` in `.env`, default 8787) that the mod talks to. See `API.md`.
Files: `player.py` (control rooms, API, stats), `common.py` (shared helpers).
