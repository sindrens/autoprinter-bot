"""Server restructure (Oct 2026): fewer channels, cleaner. Nothing is deleted: old channels go to a hidden archive.
INFORMATION: welcome (rules + getting started in one place), announcements, downloads, old-versions
COMMUNITY: general, showcase (time-lapses too), schematics, suggestions
SUPPORT: help, bug-reports
CONTROL ROOM: unchanged
STAFF (hidden): player-reports, bot-logs
ARCHIVE (hidden): rules, getting-started, commands, gifs"""
import json, os, sys
sys.path.insert(0, os.getcwd())
from discord_api import api, GUILD_ID as G

st = json.load(open("state.json"))
S, R = st["channels"], st["roles"]
VIEW, SEND = 1 << 10, 1 << 11
log = lambda *a: print("•", *a)

def patch(cid, **body):
    api("PATCH", f"/channels/{cid}", body, "Server restructure")

# Categories
patch(S["cat_start"], name="📌 INFORMATION")
patch(S["cat_dev"], name="🔒 STAFF")
log("categories renamed")
if "cat_archive" not in S:
    cat = api("POST", f"/guilds/{G}/channels", {"name": "📦 ARCHIVE", "type": 4, "permission_overwrites": [
        {"id": R["everyone"], "type": 0, "deny": str(VIEW)},
        {"id": R["moderator"], "type": 0, "allow": str(VIEW)}]}, "Server restructure")
    S["cat_archive"] = cat["id"]
    log("archive category made")
hidden = [{"id": R["everyone"], "type": 0, "deny": str(VIEW | SEND)}, {"id": R["moderator"], "type": 0, "allow": str(VIEW)}]
for key in ("rules", "getting_started", "commands", "gifs"):
    patch(S[key], parent_id=S["cat_archive"], permission_overwrites=hidden)
    log("archived", key)

# Renames and topics
patch(S["changelog"], name="📢・announcements", topic="New versions and news. Turn on the Updates role to get pinged.")
patch(S["welcome"], topic="What Printer Addon is, how to start, and the rules.")
patch(S["showcase"], topic="Show what you built with Printer Addon. Time-lapses of builds land here too.")
log("renamed / topics")

# Time-lapses go to showcase now
S["gifs_archived"] = S["gifs"]
S["gifs"] = S["showcase"]

# Order inside categories
order = [(S["welcome"], 0), (S["changelog"], 1), (S["downloads"], 2), (S["old_versions"], 3), (S["stat_blocks"], 4),
         (S["general"], 0), (S["showcase"], 1), (S["schematics"], 2), (S["suggestions"], 3),
         (S["help"], 0), (S["bug_reports"], 1)]
api("PATCH", f"/guilds/{G}/channels", [{"id": c, "position": p} for c, p in order], "Server restructure")
log("ordered")

json.dump(st, open("state.json", "w"), indent=1)
log("state.json saved")
