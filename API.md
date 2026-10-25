# Printer Addon ↔ Discord API (v1)

Base URL: `https://<vps-domain>/api/v1` (set later; local test: `http://localhost:8787/api/v1`).
All bodies are JSON. After linking, send `Authorization: Bearer <token>` on every call.
Never send chat, coordinates, chest or vault locations.

## Linking (J + L in game)
`POST /link` `{ "code": "PRINT-AB12", "player": "<mc name>" }`
→ `{ "ok": true, "token": "...", "discord": "Name", "message": "Linked to Name on Discord!" }`
→ `400 { "ok": false, "error": "..." }` (show the error in game)
Store the token in the mod config. `401` on any call = token invalid → show "Link again with /link".

`POST /unlink` → `{ "ok": true }` (delete the token locally too)

## Status (every 5 s while the game runs, also when idle)
`POST /status`
```json
{ "state": "building|breaking|paused|idle|problem", "detail": "Waiting for shop", "build": "Cactus tower 125x239x125",
  "progress": 62.5, "layer": 140, "layers": 239, "eta_s": 4200, "blocks_placed": 81234,
  "money_spent": 52000, "version": "0.15.5", "server": "play.mythian.net" }
```
No status for 90 s = shown as offline.

## Events (when they happen)
`POST /event` `{ "type": "...", "message": "optional text" }`
Types: `run_start`, `stuck`, `kicked`, `rejoined`, `no_money`, `no_items`, `vault_empty`, `error`, `custom`.

End of every run (finished, stopped by player, or stopped by a problem), with totals for stats and achievements:
```json
{ "type": "run_end", "message": "optional",
  "run": { "result": "finished|stopped|problem", "build": "Cactus tower", "blocks": 412000, "seconds": 11520, "money": 84000 } }
```

## Poll (every 3 s while linked)
`GET /poll` →
```json
{ "commands": [ { "id": "a1b2c3d4", "action": "pause|resume|stop", "ts": 1792960000 } ],
  "messages": [ { "text": "Fixed in 0.15.6!", "about": "Bug #3", "from": "maybecries", "ts": 1792960000 } ],
  "latest": "0.15.5" }
```
Run each command once (they are removed when polled). Show messages as a toast + in a "Messages" list.
If `latest` is newer than the mod's version, show "Update available".

## Shopping list (before a build starts, optional)
`POST /shopping-list` `{ "build": "Cactus tower", "items": [ { "name": "Sand", "count": 30000, "price": 15000 } ], "total": 90000 }`

## Update check (no token needed)
`GET /version` → `{ "latest": "0.15.5", "date": "...", "notes": "...", "download": "<discord link>", "sha256": "..." }`

## Suggestions in game (no token for reading)
`GET /suggestions` → `{ "suggestions": [ { "number": 4, "title": "...", "status": "Open", "up": 7, "down": 1 } ] }`
`POST /vote` `{ "number": 4, "vote": "up|down|null" }`

## Health
`GET /health` → `{ "ok": true }`
