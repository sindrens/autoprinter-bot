"""One-time server setup: roles, channels, permissions, settings, webhooks.
Safe to run again: finds things by name and only fixes what differs."""
import json
from discord_api import api, APIError, GUILD_ID as G

B = lambda n: 1 << n
VIEW, SEND, SEND_TTS, MANAGE_MSG, EMBED, ATTACH, HISTORY, MENTION_ALL = B(10), B(11), B(12), B(13), B(14), B(15), B(16), B(17)
REACT, KICK, BAN, ADMIN, AUDIT, NICKS, THREADS, TIMEOUT = B(6), B(1), B(2), B(3), B(7), B(27), B(34), B(40)
MUTE, DEAFEN, MOVE = B(22), B(23), B(24)
PUB_THREADS, PRIV_THREADS, SEND_THREADS = B(35), B(36), B(38)

CH = json.load(open("state.json"))["channels"] if __import__("os").path.exists("state.json") else {}
CH = {**{  # existing channel ids
    "cat_start": "1563966398273814659", "welcome": "1563966431455084564", "getting_started": "1563966515420729535",
    "changelog": "1563966607640760331", "downloads": "1563966635172434001",
    "cat_community": "1563966666155761795", "general": "1563957543301292162", "showcase": "1563966779041259590",
    "suggestions": "1563966830186340352",
    "cat_support": "1563966865913675836", "help": "1563966955684237362", "bug_reports": "1563966976295047238",
    "cat_dev": "1563967220139167784", "player_reports": "1563967285734023310",
}, **CH}
OWNER = "107436405818408960"
state = json.load(open("state.json")) if __import__("os").path.exists("state.json") else {}
state.setdefault("roles", {})
state["channels"] = dict(CH)
log = lambda *a: print("•", *a)

def step(name, fn):
    try:
        fn()
    except APIError as e:
        print("!!", name, "->", e)

# ---------- roles ----------
roles = {r["name"]: r for r in api("GET", f"/guilds/{G}/roles")}
everyone = roles["@everyone"]
state["roles"]["everyone"] = everyone["id"]
state["roles"]["autoprinter"] = roles["Autoprinter"]["id"]

ROLE_SPEC = [  # top -> bottom (below the bot's own role)
    ("developer", "Developer", 0x38BDF8, True, ADMIN),
    ("moderator", "Moderator", 0x818CF8, True, AUDIT | KICK | MANAGE_MSG | THREADS | TIMEOUT | NICKS | MUTE | DEAFEN | MOVE),
    ("bots", "Bots", 0x64748B, False, VIEW | SEND | EMBED | ATTACH | HISTORY | REACT),
    ("beta", "Beta Tester", 0x34D399, True, 0),
    ("logger", "Logger", 0xF59E0B, True, 0),
    ("updates", "Updates", 0x60A5FA, False, 0),
    ("printers", "Printers", 0x3B82F6, True, 0),
]
for key, name, color, hoist, perms in ROLE_SPEC:
    def do(key=key, name=name, color=color, hoist=hoist, perms=perms):
        body = {"name": name, "color": color, "hoist": hoist, "permissions": str(perms), "mentionable": key in ("beta", "updates")}
        if name in roles:
            r = api("PATCH", f"/guilds/{G}/roles/{roles[name]['id']}", body, "Server cleanup")
            log("updated role", name)
        else:
            r = api("POST", f"/guilds/{G}/roles", body, "Server cleanup")
            log("created role", name)
        state["roles"][key] = r["id"]
    step("role " + name, do)
    if key not in state["roles"] and name in roles:
        state["roles"][key] = roles[name]["id"]

def everyone_perms():
    p = int(everyone["permissions"]) & ~(MENTION_ALL | SEND_TTS)
    api("PATCH", f"/guilds/{G}/roles/{everyone['id']}", {"permissions": str(p)}, "No @everyone pings")
    log("@everyone can no longer ping everyone")
step("everyone perms", everyone_perms)

def order_roles():
    top = len(api("GET", f"/guilds/{G}/roles"))
    bot_pos = next(r["position"] for r in api("GET", f"/guilds/{G}/roles") if r["id"] == state["roles"]["autoprinter"])
    keys = [k for k, *_ in ROLE_SPEC]
    body = [{"id": state["roles"][k], "position": len(keys) - i} for i, k in enumerate(keys) if k in state["roles"]]
    api("PATCH", f"/guilds/{G}/roles", body, "Role order")
    log("ordered roles under the bot")
step("role order (needs Autoprinter at top)", order_roles)

def give_dev():
    api("PUT", f"/guilds/{G}/members/{OWNER}/roles/{state['roles']['developer']}", None, "Owner = Developer")
    log("gave Developer to maybecries")
step("give developer", give_dev)

# ---------- channels ----------
chans = {c["name"]: c for c in api("GET", f"/guilds/{G}/channels")}
def ensure_channel(key, name, parent, topic, type_=0):
    def do():
        if name in chans:
            state["channels"][key] = chans[name]["id"]; return
        body = {"name": name, "type": type_, "parent_id": parent}
        if type_ == 0: body["topic"] = topic
        c = api("POST", f"/guilds/{G}/channels", body, "Server cleanup")
        state["channels"][key] = c["id"]; log("created", name)
    step("channel " + name, do)
ensure_channel("rules", "📜・rules", CH["cat_start"], "The server rules. Read before posting.")
ensure_channel("bot_logs", "🧾・bot-logs", CH["cat_dev"], "Everything the bot sees: edits, deletes, joins, leaves, roles, bans.")
ensure_channel("old_versions", "🗃️・old-versions", CH["cat_start"], "Older Printer Addon versions. Use the newest one in #downloads unless told otherwise.")
ensure_channel("commands", "🤖・commands", CH["cat_start"], "Every bot command and how to use it.")
ensure_channel("cat_control", "🎮 CONTROL ROOM", None, None, 4)
ensure_channel("control_start", "🔗・link-your-game", state["channels"].get("cat_control"), "Link Printer Addon to Discord and get your own control room.")
ensure_channel("stat_blocks", "🧱 Blocks printed: 0", CH["cat_start"], None, 2)
S, R = state["channels"], state["roles"]

def ow(id_, allow=0, deny=0, t=0):
    return {"id": id_, "type": t, "allow": str(allow), "deny": str(deny)}
read_only = [ow(R["everyone"], deny=SEND | PUB_THREADS | PRIV_THREADS | SEND_THREADS),
             ow(R["moderator"], allow=SEND), ow(R["bots"], allow=SEND)]
private = [ow(R["everyone"], deny=VIEW), ow(R["developer"], allow=VIEW),
           ow(R["moderator"], allow=VIEW | SEND | HISTORY), ow(R["bots"], allow=VIEW | SEND | HISTORY)]
groups = {
    "read_only": (["cat_start", "rules", "welcome", "getting_started", "changelog", "downloads", "old_versions", "commands", "cat_control", "control_start"], read_only),
    "stat": (["stat_blocks"], [ow(R["everyone"], allow=VIEW, deny=B(20))]),
    "private": (["cat_dev", "player_reports", "bot_logs"], private),
}
for gname, (keys, overwrites) in groups.items():
    for k in keys:
        step(f"perms {k}", lambda k=k, o=overwrites: (api("PATCH", f"/channels/{S[k]}", {"permission_overwrites": o}, "Server cleanup"), log(gname, k)))

TOPICS = {
    "welcome": "Start here: what Printer Addon is and where to find everything.",
    "getting_started": "Install Printer Addon and run your first build.",
    "changelog": "What's new in every version.",
    "downloads": "The only official place to download Printer Addon.",
    "general": "Talk about anything Printer Addon and Minecraft.",
    "showcase": "Show what you built with Printer Addon.",
    "help": "Questions about Printer Addon. Say what you tried and your version.",
    "player_reports": "Reports and logs sent from the game.",
    "old_versions": "Older Printer Addon versions. Use the newest one in #downloads unless told otherwise.",
}
SLOW = {"general": 3, "help": 5}
for k, t in TOPICS.items():
    body = {"topic": t}
    if k in SLOW: body["rate_limit_per_user"] = SLOW[k]
    step("topic " + k, lambda k=k, b=body: (api("PATCH", f"/channels/{S[k]}", b, "Server cleanup"), log("topic", k)))

def order_channels():
    order = ["stat_blocks", "welcome", "rules", "getting_started", "commands", "changelog", "downloads", "old_versions", "general", "showcase", "suggestions",
             "help", "bug_reports", "control_start", "player_reports", "bot_logs"]
    api("PATCH", f"/guilds/{G}/channels", [{"id": S[k], "position": i} for i, k in enumerate(order)], "Channel order")
    log("channel order set")
step("channel order", order_channels)

# ---------- server settings ----------
def settings():
    base = {"verification_level": 1, "explicit_content_filter": 2, "default_message_notifications": 1,
            "system_channel_id": S["bot_logs"], "system_channel_flags": 1 | 2 | 4 | 8}
    api("PATCH", f"/guilds/{G}", base, "Server settings"); log("verification Low, content filter on, mentions-only notifications")
    g = api("GET", f"/guilds/{G}")
    feats = list(set(g["features"]) | {"COMMUNITY"})
    try:
        api("PATCH", f"/guilds/{G}", {"features": feats, "rules_channel_id": S["rules"],
                                     "public_updates_channel_id": S["bot_logs"]}, "Enable Community")
        log("Community on, #rules set as rules channel")
    except APIError as e:
        print("!! community ->", e)
step("settings", settings)

# ---------- webhooks ----------
def webhooks():
    for h in api("GET", f"/guilds/{G}/webhooks"):
        if h["channel_id"] == S["player_reports"]:
            log("kept webhook", h["name"], "(mod reports)"); continue
        api("DELETE", f"/webhooks/{h['id']}", None, "Bot posts instead of webhooks"); log("deleted webhook", h["name"])
step("webhooks", webhooks)

json.dump(state, open("state.json", "w"), indent=1)
print("saved state.json")
