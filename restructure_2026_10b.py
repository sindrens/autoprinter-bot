"""Cleaner names (Oct 2026): plain words, no emoji; categories in reading order. Nothing is deleted."""
import json, os, sys
sys.path.insert(0, os.getcwd())
from discord_api import api, GUILD_ID as G
S = json.load(open("state.json"))["channels"]
names = {
    "cat_start": "Information", "welcome": "welcome", "changelog": "announcements", "downloads": "downloads",
    "old_versions": "old-versions",
    "cat_community": "Community", "general": "general", "showcase": "showcase", "schematics": "schematics",
    "suggestions": "suggestions",
    "cat_support": "Support", "help": "help", "bug_reports": "bug-reports",
    "cat_control": "Your game", "control_start": "link-your-game",
    "cat_dev": "Staff", "player_reports": "player-reports", "bot_logs": "bot-logs",
    "cat_archive": "Archive",
}
for key, name in names.items():
    if key in S:
        api("PATCH", f"/channels/{S[key]}", {"name": name}, "Cleaner names")
        print("•", key, "->", name)
# Control rooms and the counter: same style
for c in api("GET", f"/guilds/{G}/channels"):
    if c.get("parent_id") == S["cat_control"] and c["name"].startswith("🎮・"):
        api("PATCH", f"/channels/{c['id']}", {"name": c["name"][2:]}, "Cleaner names")
        print("• room", c["name"][2:])
    if c["id"] == S.get("stat_blocks"):
        api("PATCH", f"/channels/{c['id']}", {"name": c["name"].replace("🧱 ", "")}, "Cleaner names")
        print("• counter", c["name"].replace("🧱 ", ""))
order = [(S["cat_start"], 0), (S["cat_community"], 1), (S["cat_support"], 2), (S["cat_control"], 3), (S["cat_dev"], 4), (S["cat_archive"], 5)]
api("PATCH", f"/guilds/{G}/channels", [{"id": c, "position": p} for c, p in order], "Cleaner names")
print("• categories ordered")
