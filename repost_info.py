"""Delete the old info messages and post them again from the bot (no webhooks).
Source = the newest backup. Posted message ids are saved to messages.json so they can be edited later."""
import json, glob, os, copy
from discord_api import api, api_files, APIError

BRAND = 0x3B82F6
state = json.load(open("state.json"))
S = state["channels"]
backup = json.load(open(sorted(glob.glob("backups/backup-*.json"))[0]))  # oldest = original content
ATT = "assets/attachments"

def old_posts(key):
    msgs = [m for m in reversed(backup["messages"][S[key]]) if m.get("webhook_id")]
    out = []
    for m in msgs:
        files = [(a["filename"], open(f"{ATT}/{m['id']}_{a['filename']}", "rb").read()) for a in m["attachments"]]
        embeds = []
        for e in m["embeds"]:
            ne = {k: e[k] for k in ("title", "description", "fields", "footer", "url") if k in e}
            ne["color"] = BRAND
            embeds.append(ne)
        out.append({"content": m["content"], "embeds": embeds, "files": files})
    return out

def ch(key): return f"<#{S[key]}>"

plan = {}
# welcome: banner + welcome embed (rules move to #rules)
w = old_posts("welcome")
banner, main = w[0], w[1]
welcome_embed, rules_embed = main["embeds"][0], main["embeds"][1]
welcome_embed["description"] = welcome_embed["description"].replace(
    "**Start here**\n", f"**Start here**\n📜 {ch('rules')} read these first\n")
plan["welcome"] = [banner, {"content": "", "embeds": [welcome_embed], "files": []}]
rules_embed["description"] = (
    "**1.** Be friendly and helpful.\n"
    "**2.** No spam, ads or self-promotion.\n"
    f"**3.** Use the right channel: questions in {ch('help')}, problems in {ch('bug_reports')}, ideas in {ch('suggestions')}.\n"
    f"**4.** Download Printer Addon only from {ch('downloads')}.\n"
    "**5.** Only use it where automation is allowed: check your server's rules.\n"
    "**6.** Don't ping staff for help. Someone will answer.\n"
    "**7.** Keep it in English so everyone can follow.\n\n"
    "Breaking the rules can get you a timeout, kick or ban.")
plan["rules"] = [{"content": "", "embeds": [rules_embed], "files": []}]

plan["getting_started"] = old_posts("getting_started")
plan["changelog"] = old_posts("changelog")
d = old_posts("downloads")
plan["downloads"] = [d[0], d[1], d[-1]]  # banner, how to install, newest version only
plan["showcase"] = old_posts("showcase")

s = old_posts("suggestions")
s[-1]["embeds"][0]["description"] = (
    "Have an idea to make Printer Addon better?\n"
    "• Type **/suggest** and write what it should do, and why\n"
    "• The bot posts it with ✅ and ❌ so everyone can vote\n"
    "• You'll see when an idea is **Planned**, **Added** or **Denied**\n\n"
    f"The most wanted ideas go into the next versions. Bugs belong in {ch('bug_reports')}.")
plan["suggestions"] = s
plan["help"] = old_posts("help")
b = old_posts("bug_reports")
for p in b:
    for e in p["embeds"]:
        if e.get("title", "").startswith("How to report"):
            e["description"] = e["description"].replace(
                "**4.** Then post here what happened and your version.",
                "**4.** Then type **/bug** here: it opens a short form and makes a thread for your report.")
plan["bug_reports"] = b

def wipe(cid):
    msgs = []
    before = None
    while True:
        m = api("GET", f"/channels/{cid}/messages?limit=100" + (f"&before={before}" if before else ""))
        if not m: break
        msgs += m; before = m[-1]["id"]
        if len(m) < 100: break
    ids = [m["id"] for m in msgs]
    while len(ids) >= 2:
        chunk, ids = ids[:100], ids[100:]
        api("POST", f"/channels/{cid}/messages/bulk-delete", {"messages": chunk}, "Rebuild info channel")
    for i in ids:
        api("DELETE", f"/channels/{cid}/messages/{i}", None, "Rebuild info channel")
    return len(msgs)

posted = {}
for key in ["welcome", "rules", "getting_started", "changelog", "downloads", "showcase", "suggestions", "help", "bug_reports"]:
    cid = S[key]
    n = wipe(cid)
    posted[key] = []
    for p in plan[key]:
        payload = {"content": p["content"], "embeds": p["embeds"], "allowed_mentions": {"parse": []}}
        if p["files"]:
            payload["attachments"] = [{"id": i, "filename": f} for i, (f, _) in enumerate(p["files"])]
            m = api_files("POST", f"/channels/{cid}/messages", payload, p["files"])
        else:
            m = api("POST", f"/channels/{cid}/messages", payload)
        posted[key].append(m["id"])
    print(f"• {key}: removed {n}, posted {len(posted[key])}")

json.dump(posted, open("messages.json", "w"), indent=1)
print("saved messages.json")
