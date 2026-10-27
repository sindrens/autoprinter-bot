"""Player features: link the game to Discord, control rooms, the API the mod talks to,
stats, achievements, leaderboard, live counter, commands overview and dev-reply relay."""
import asyncio
import datetime as dt
import hashlib
import io
import json
import os
import re
import secrets
import time

import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands, tasks

from common import *

API_VERSION = "v1"
CODE_TTL = 600            # link codes live 10 minutes
OFFLINE_AFTER = 90        # seconds without a status update = offline
PANEL_EDIT_EVERY = 8      # seconds between control panel edits per player
MSGS_FILE = os.path.join(HERE, "messages.json")


def env(key, default=None):
    try:
        for line in open(os.path.join(HERE, ".env")):
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return default


API_HOST = env("API_HOST", "0.0.0.0")
API_PORT = int(env("API_PORT", "8787"))
now = lambda: int(time.time())
week_key = lambda: dt.date.today().strftime("%G-W%V")
esc = discord.utils.escape_markdown


def fmt_num(n):
    n = float(n or 0)
    for unit, div in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if n >= div:
            return f"{n / div:.1f}".rstrip("0").rstrip(".") + unit
    return f"{int(n)}"


def fmt_time(sec):
    sec = int(sec or 0)
    h, m = divmod(sec // 60, 60)
    return f"{h} h {m} min" if h else f"{m} min"


def bar(pct, n=12):
    pct = max(0.0, min(100.0, float(pct or 0)))
    full = round(pct / 100 * n)
    return "▰" * full + "▱" * (n - full) + f"  {pct:.0f}%"


# ---------------------------------------------------------------- texts
STATE_LABEL = {
    "building": ("🟢", "Building"), "breaking": ("🟢", "Auto break"), "paused": ("⏸️", "Paused"),
    "idle": ("⚪", "Idle"), "problem": ("🔴", "Problem"), "offline": ("⚫", "Offline"),
}
ALERTS = {  # event type -> (emoji, title, ping the owner?)
    "run_start": ("▶️", "Build started", False),
    "finished": ("🏁", "Build finished", True),
    "stopped": ("■", "Build stopped", False),
    "stuck": ("🧱", "Stuck", True),
    "kicked": ("👢", "Kicked from the server", True),
    "rejoined": ("🔁", "Rejoined and carrying on", False),
    "no_money": ("💸", "Out of money", True),
    "no_items": ("📦", "Missing items", True),
    "vault_empty": ("🗄️", "Vaults are empty", True),
    "error": ("⚠️", "Something went wrong", True),
    "custom": ("💬", "Message from the game", False),
    "collectors_sold": ("💰", "Collectors sold (AutoSell)", False),
    "collectors_problem": ("⚠️", "AutoSell stopped", True),
    "layer_cost": ("💸", "A layer cost more than usual", True),
    "schedule": ("⏰", "Stopped on schedule", False),
}
# What players can choose to get (in their control room). Less is more: only what needs them is on by default.
NOTIFY = {  # key: (label, description, default on)
    "finished": ("🏁 Build finished", "When a build is done", True),
    "problems": ("🚨 Problems", "Stuck, missing items, out of money, errors", True),
    "kicked": ("👢 Kicked", "Kicked from the server", True),
    "startstop": ("▶️ Started / stopped", "Every start and stop", False),
    "rejoined": ("🔁 Rejoined", "Joined the server again by itself", False),
    "shopping": ("🛒 Shopping list", "What a build still needs, at the start", False),
    "timelapse": ("🎬 Time-lapse", "A GIF of the build going up when it's done (you choose if it's public)", True),
    "achievements": ("🏅 Achievements", "Show my achievements in #general", True),
    "selling": ("💰 Collector sales", "What AutoSell earned on each round", False),
}
CATEGORY = {"run_start": "startstop", "stopped": "startstop", "finished": "finished", "kicked": "kicked",
            "rejoined": "rejoined", "stuck": "problems", "no_money": "problems", "no_items": "problems",
            "vault_empty": "problems", "error": "problems", "custom": "problems",
            "collectors_sold": "selling", "collectors_problem": "problems",
            "layer_cost": "problems", "schedule": "finished"}


def wants(p, key):
    n = p.get("notify")
    return NOTIFY[key][2] if n is None else key in n


ACHIEVEMENTS = [  # id, name, how to get it, check(stats)
    ("first", "🏁 First Print", "Finish your first build", lambda s: s["builds"] >= 1),
    ("10k", "🧱 Bricklayer", "Place 10,000 blocks", lambda s: s["blocks"] >= 10_000),
    ("100k", "🏗️ Builder", "Place 100,000 blocks", lambda s: s["blocks"] >= 100_000),
    ("1m", "🏭 Factory", "Place 1,000,000 blocks", lambda s: s["blocks"] >= 1_000_000),
    ("10m", "🌆 Megaproject", "Place 10,000,000 blocks", lambda s: s["blocks"] >= 10_000_000),
    ("10b", "📐 Architect", "Finish 10 builds", lambda s: s["builds"] >= 10),
    ("50b", "🏛️ Master Architect", "Finish 50 builds", lambda s: s["builds"] >= 50),
    ("10h", "⏱️ Time Saver", "Let it build for 10 hours", lambda s: s["seconds"] >= 36_000),
    ("100h", "🕰️ Hands Free", "Let it build for 100 hours", lambda s: s["seconds"] >= 360_000),
    ("night", "🌙 Night Shift", "One run of 8 hours or more", lambda s: s["longest"] >= 28_800),
    ("spend", "💰 Big Spender", "Spend 1,000,000 in shops", lambda s: s["money"] >= 1_000_000),
    ("cactus", "🌵 Cactus King", "Finish 5 cactus builds", lambda s: s["tags"].get("cactus", 0) >= 5),
    ("pumpkin", "🎃 Pumpkin Patch", "Finish 5 pumpkin builds", lambda s: s["tags"].get("pumpkin", 0) >= 5),
    ("logger", "📜 Logger", "Send 5 logs", lambda s: s.get("logs", 0) >= 5),
]
TAGS = ["cactus", "pumpkin", "melon", "sugar", "bamboo", "kelp", "wheat", "iron", "gold", "base"]

HELP = [
    ("👋 Everyone", [
        ("/help", "Show this list"),
        ("/docs", "Links to the guide, downloads and changelog"),
        ("/faq", "Quick answers to common questions"),
        ("/suggest", "Suggest an idea (or post in #suggestions)"),
        ("/bug", "Report a bug (or post in #bug-reports)"),
        ("/stats", "Your Printer Addon stats and achievements"),
        ("/leaderboard", "Top printers this week or all time"),
        ("/achievements", "Every achievement and how to get it"),
        ("/schematic share", "Share a .litematic in #schematics, with a 3D preview"),
        ("/schematic search", "Find schematics in the library"),
    ]),
    ("🎮 Your game", [
        ("/link", "Connect Printer Addon to Discord and get your control room"),
        ("/status", "What your game is doing right now"),
        ("/pause · /resume · /stop", "Control your build from Discord"),
        ("/schedule", "Stop at a time (07:00) and/or keep money (500k); also ⏰ in your control room"),
        ("/notifications", "Choose what the bot tells you (less is more by default)"),
        ("/logs", "How many logs you've sent (5 = Logger role)"),
        ("/unlink", "Disconnect your game"),
        ("In game: /printeraddon schematics", "Browse the library and download straight into Litematica"),
    ]),
    ("🛡️ Staff", [
        ("/suggestion-status", "In a suggestion post: Planned, Added or Denied"),
        ("/bug-status", "In a bug post: Looking into it, Fixed or Not a bug"),
        ("/timeout · /kick · /ban", "Moderate a member"),
        ("/purge", "Delete recent messages"),
        ("Reply in a bug thread", "The reporter sees your answer in game"),
    ]),
    ("🛠️ Developer", [
        ("Automatic releases", "A new version in the mod repo with a CHANGELOG entry is posted by itself"),
        ("/log-summary", "Where the players' time goes, and each version compared"),
        ("/release-check", "Check the mod repo for a new version now"),
        ("/release", "Post a version by hand"),
        ("/post · /edit", "Write or edit a message as the bot"),
    ]),
]


def help_embed():
    e = discord.Embed(title="🤖 Bot commands", color=BRAND,
                      description="Type `/` in any channel to see the commands. Most answers are only visible to you.")
    for group, items in HELP:
        e.add_field(name=group, value="\n".join(f"**{c}** · {d}" for c, d in items), inline=False)
    e.set_footer(text="Autoprinter · Printer Addon")
    return e


def link_embed():
    return discord.Embed(title="🔗 Link your game", color=BRAND, description=(
        "Connect Printer Addon to Discord and get your own **control room**: live progress, "
        "a message when your build is done or needs you, and Pause / Resume / Stop from your phone.\n\n"
        "**1.** Click **Get my link code** below (or type **/link**).\n"
        "**2.** In Minecraft, press **J + L** and type the code.\n"
        "**3.** Done! Your private control room appears under 🎮 CONTROL ROOM.\n"
        "Choose what you get with **⚙️ Notifications** there. By default only what matters: "
        "finished, problems and kicks.\n\n"
        "Only you (and the developer) can see your control room. "
        "Nothing private is sent: no chat, no coordinates, no vault or chest locations."))


def load_msgs():
    try:
        return json.load(open(MSGS_FILE))
    except FileNotFoundError:
        return {}


def save_msgs(m):
    json.dump(m, open(MSGS_FILE, "w"), indent=1)


def stats_of(uid):
    s = DATA["stats"].setdefault(str(uid), {})
    for k, v in {"blocks": 0, "seconds": 0, "builds": 0, "money": 0, "longest": 0, "tags": {}, "badges": [],
                 "weekly": {}}.items():
        s.setdefault(k, v)
    return s


# ---------------------------------------------------------------- views
class LinkView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Get my link code", emoji="🔗", style=discord.ButtonStyle.primary, custom_id="link:code")
    async def code(self, inter, _):
        await self.cog.send_code(inter)


class PanelView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Pause", emoji="⏸️", style=discord.ButtonStyle.secondary, custom_id="ctl:pause")
    async def pause(self, inter, _):
        await self.cog.control(inter, "pause")

    @discord.ui.button(label="Resume", emoji="▶️", style=discord.ButtonStyle.success, custom_id="ctl:resume")
    async def resume(self, inter, _):
        await self.cog.control(inter, "resume")

    @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.danger, custom_id="ctl:stop")
    async def stop(self, inter, _):
        await self.cog.control(inter, "stop")

    @discord.ui.button(label="Notifications", emoji="⚙️", style=discord.ButtonStyle.secondary, custom_id="ctl:dm", row=1)
    async def dm(self, inter, _):
        await self.cog.notify_settings(inter)

    @discord.ui.button(label="My stats", emoji="📊", style=discord.ButtonStyle.secondary, custom_id="ctl:stats", row=1)
    async def stats(self, inter, _):
        await inter.response.send_message(embed=self.cog.stats_embed(inter.user), ephemeral=True)

    @discord.ui.button(label="Schedule", emoji="⏰", style=discord.ButtonStyle.secondary, custom_id="ctl:schedule", row=1)
    async def schedule(self, inter, _):
        uid = self.cog.owner_of(inter)
        if not uid or uid != str(inter.user.id):
            return await inter.response.send_message("Only the owner of this control room can do that.", ephemeral=True)
        await inter.response.send_modal(ScheduleModal(self.cog, uid))


def schedule_spec(stop_at, keep_money):
    """'07:00', '500k' -> 'schedule:stop_at=07:00;keep_money=500000', or (None, error)."""
    parts = []
    if stop_at is not None and stop_at.strip() != "":
        t = stop_at.strip().replace(".", ":")
        m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?", t)
        if not m or int(m.group(1)) > 23 or (m.group(2) and int(m.group(2)) > 59):
            return None, "Write the stop time like **07:00** (24-hour clock, your computer's time)."
        parts.append(f"stop_at={int(m.group(1)):02d}:{int(m.group(2) or 0):02d}")
    elif stop_at is not None:
        parts.append("stop_at=")
    if keep_money is not None and str(keep_money).strip() != "":
        k = str(keep_money).strip().lower().replace(",", "").replace("$", "").replace(" ", "")
        mult = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(k[-1:], 1)
        try:
            n = int(float(k[:-1] if mult > 1 else k) * mult)
        except ValueError:
            return None, "Write the money to keep like **500000**, **500k** or **2m**."
        parts.append(f"keep_money={max(0, n)}")
    elif keep_money is not None:
        parts.append("keep_money=0")
    if not parts:
        return None, "Nothing to set."
    return "schedule:" + ";".join(parts), None


class ScheduleModal(discord.ui.Modal, title="Schedule"):
    stop_at = discord.ui.TextInput(label="Stop at (time of day, empty: no stop time)", placeholder="07:00",
                                   required=False, max_length=5)
    keep_money = discord.ui.TextInput(label="Keep at least this much money (0: no limit)", placeholder="500k",
                                      required=False, max_length=15)

    def __init__(self, cog, uid):
        super().__init__()
        self.cog, self.uid = cog, uid
        st = (DATA["players"][uid].get("status") or {}).get("schedule", "")
        m = re.search(r"stops at (\d\d:\d\d)", st)
        if m:
            self.stop_at.default = m.group(1)
        m = re.search(r"keeps \$([\d,]+)", st)
        if m:
            self.keep_money.default = m.group(1).replace(",", "")

    async def on_submit(self, inter):
        spec, err = schedule_spec(self.stop_at.value, self.keep_money.value)
        if err:
            return await inter.response.send_message(err, ephemeral=True)
        self.cog.queue(self.uid, spec)
        await inter.response.send_message("⏰ Schedule sent. Your game confirms it in a few seconds.", ephemeral=True)


class NotifySelect(discord.ui.Select):
    def __init__(self, cog, uid):
        p = DATA["players"][uid]
        options = [discord.SelectOption(label=v[0], description=v[1], value=k, default=wants(p, k))
                   for k, v in NOTIFY.items()]
        super().__init__(placeholder="Pick what you want to get", min_values=0, max_values=len(options), options=options)
        self.cog, self.uid = cog, uid

    async def callback(self, inter):
        DATA["players"][self.uid]["notify"] = list(self.values)
        save_data()
        await inter.response.edit_message(content=self.cog.notify_text(self.uid), view=NotifyView(self.cog, self.uid))


class NotifyView(discord.ui.View):
    def __init__(self, cog, uid):
        super().__init__(timeout=300)
        self.cog, self.uid = cog, uid
        p = DATA["players"][uid]
        self.add_item(NotifySelect(cog, uid))
        ping = discord.ui.Button(label=f"Ping me: {'on' if p.get('ping', True) else 'off'}", emoji="📳", row=1,
                                 style=discord.ButtonStyle.success if p.get("ping", True) else discord.ButtonStyle.secondary)
        dm = discord.ui.Button(label=f"Also DM me: {'on' if p.get('dm', False) else 'off'}", emoji="✉️", row=1,
                               style=discord.ButtonStyle.success if p.get("dm", False) else discord.ButtonStyle.secondary)
        ping.callback = self.toggle("ping", True)
        dm.callback = self.toggle("dm", False)
        self.add_item(ping)
        self.add_item(dm)

    def toggle(self, key, default):
        async def cb(inter):
            p = DATA["players"][self.uid]
            p[key] = not p.get(key, default)
            save_data()
            await inter.response.edit_message(content=self.cog.notify_text(self.uid), view=NotifyView(self.cog, self.uid))
            await self.cog.edit_panel(self.uid, force=True)
        return cb


class ConfirmStop(discord.ui.View):
    def __init__(self, cog, uid):
        super().__init__(timeout=60)
        self.cog, self.uid = cog, uid

    @discord.ui.button(label="Yes, stop the build", style=discord.ButtonStyle.danger)
    async def yes(self, inter, _):
        self.cog.queue(self.uid, "stop")
        await inter.response.edit_message(content="⏹️ Stop sent. Your game stops within a few seconds.", view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def no(self, inter, _):
        await inter.response.edit_message(content="Cancelled. Nothing was sent.", view=None)


# ---------------------------------------------------------------- the cog
class Player(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.runner = None
        self.last_edit = {}
        self.dirty = set()
        self.last_status_at = {}
        self.bad_links = {}
        self.installs_by_ip = {}

    async def cog_load(self):
        self.bot.add_view(LinkView(self))
        self.bot.add_view(PanelView(self))
        app = web.Application(client_max_size=20 * 1024 * 1024)
        r = app.router
        r.add_get(f"/api/{API_VERSION}/health", self.h_health)
        r.add_get(f"/api/{API_VERSION}/version", self.h_version)
        r.add_get(f"/api/{API_VERSION}/suggestions", self.h_suggestions)
        r.add_post(f"/api/{API_VERSION}/link", self.h_link)
        r.add_post(f"/api/{API_VERSION}/unlink", self.h_unlink)
        r.add_post(f"/api/{API_VERSION}/status", self.h_status)
        r.add_post(f"/api/{API_VERSION}/event", self.h_event)
        r.add_get(f"/api/{API_VERSION}/poll", self.h_poll)
        r.add_post(f"/api/{API_VERSION}/shopping-list", self.h_shopping)
        r.add_post(f"/api/{API_VERSION}/vote", self.h_vote)
        r.add_get(f"/api/{API_VERSION}/schematics", self.h_schematics)
        r.add_get(f"/api/{API_VERSION}/schematics/{{sid}}/file", self.h_schematic_file)
        r.add_post(f"/api/{API_VERSION}/build-schematic", self.h_build_schematic)
        r.add_post(f"/api/{API_VERSION}/prices", self.h_prices)
        r.add_post(f"/api/{API_VERSION}/install", self.h_install)
        r.add_post(f"/api/{API_VERSION}/report", self.h_report)
        r.add_post(f"/api/{API_VERSION}/admin/release", self.h_admin_release)
        r.add_get(f"/api/{API_VERSION}/admin/log", self.h_admin_log)
        r.add_post(f"/api/{API_VERSION}/admin/timelapse", self.h_admin_timelapse)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        await web.TCPSite(self.runner, API_HOST, API_PORT).start()
        print(f"Player API on http://{API_HOST}:{API_PORT}/api/{API_VERSION}")
        self.loop_panels.start()
        self.loop_counter.start()

    async def cog_unload(self):
        self.loop_panels.cancel()
        self.loop_counter.cancel()
        if self.runner:
            await self.runner.cleanup()

    @commands.Cog.listener()
    async def on_ready(self):
        await self.ensure_fixed_posts()
        if "release" not in DATA:
            DATA["release"] = {"version": "0.15.5", "date": "October 25, 2026", "notes": "", "url": "", "sha256": ""}
            save_data()

    async def ensure_fixed_posts(self):
        """Keep the #commands list and the #link-your-game panel up to date (edit in place)."""
        posted = load_msgs()
        for key, chan_key, make in (("commands", "commands", lambda: {"embed": help_embed()}),
                                    ("link_panel", "control_start", lambda: {"embed": link_embed(), "view": LinkView(self)})):
            c = ch(chan_key)
            if not c:
                continue
            kwargs = make()
            msg = None
            if posted.get(key):
                try:
                    msg = await c.fetch_message(posted[key])
                    await msg.edit(**kwargs)
                except discord.NotFound:
                    msg = None
            if not msg:
                msg = await c.send(**kwargs)
                posted[key] = msg.id
        save_msgs(posted)

    # ------------------------------------------------ auth helpers
    def auth(self, request):
        h = request.headers.get("Authorization", "")
        if not h.startswith("Bearer "):
            raise web.HTTPUnauthorized(text="missing token")
        uid = DATA["tokens"].get(hashlib.sha256(h[7:].encode()).hexdigest())
        if not uid or uid not in DATA["players"]:
            raise web.HTTPUnauthorized(text="unknown token, link again")
        return uid

    async def body(self, request):
        try:
            b = await request.json()
            if not isinstance(b, dict):
                raise ValueError
            return b
        except Exception:
            raise web.HTTPBadRequest(text="send a JSON object")

    def queue(self, uid, action):
        p = DATA["players"][str(uid)]
        p.setdefault("commands", []).append({"id": secrets.token_hex(4), "action": action, "ts": now()})
        p["commands"] = p["commands"][-10:]
        save_data()

    # ------------------------------------------------ linking
    def new_code(self, uid):
        for c, v in list(DATA["codes"].items()):
            if v["user"] == uid or v["expires"] < now():
                del DATA["codes"][c]
        code = "PRINT-" + "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(4))
        DATA["codes"][code] = {"user": uid, "expires": now() + CODE_TTL}
        save_data()
        return code

    async def send_code(self, inter):
        code = self.new_code(inter.user.id)
        e = discord.Embed(title="Your link code", color=BRAND, description=(
            f"# `{code}`\n"
            f"In Minecraft, press **J + L** and type this code.\n"
            f"It works for **10 minutes**, once. Don't share it."))
        if player_of(inter.user.id):
            e.set_footer(text="You're already linked. Linking again moves your control room to the new game.")
        await inter.response.send_message(embed=e, ephemeral=True)

    async def control_room(self, guild, member, mc):
        p = DATA["players"][str(member.id)]
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=False,
                                                add_reactions=False),
            guild.get_role(int(ROLE["developer"])): discord.PermissionOverwrite(view_channel=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True),
        }
        name = f"🎮・{mc.lower()}"
        c = guild.get_channel(int(p["channel"])) if p.get("channel") else None
        if c:
            await c.edit(name=name, overwrites=overwrites)
        else:
            c = await guild.create_text_channel(name, category=guild.get_channel(int(CH["cat_control"])),
                                                overwrites=overwrites, topic=f"{mc}'s control room. Only you can see this.")
            p["channel"] = c.id
        msg = await c.send(content=member.mention, embed=self.panel_embed(member.id), view=PanelView(self))
        await msg.pin()
        p["panel"] = msg.id
        save_data()
        return c

    # ------------------------------------------------ control panel
    def panel_embed(self, uid):
        p = DATA["players"][str(uid)]
        st = p.get("status") or {}
        online = now() - p.get("last_seen", 0) < OFFLINE_AFTER
        state = st.get("state", "idle") if online else "offline"
        dot, label = STATE_LABEL.get(state, ("⚪", state.title()))
        e = discord.Embed(title=f"🎮 {esc(p['mc'])}'s control room", color={"🟢": GREEN, "🔴": RED, "⏸️": YELLOW}.get(dot, GREY))
        e.add_field(name="Status", value=f"{dot} **{label}**" + (f"\n{esc(st['detail'])}" if st.get("detail") and online else ""), inline=False)
        if st.get("build"):
            e.add_field(name="Build", value=esc(st["build"])[:200], inline=False)
        if st.get("progress") is not None and state != "offline":
            e.add_field(name="Progress", value=bar(st["progress"]), inline=False)
        if st.get("layer") and st.get("layers"):
            e.add_field(name="Layer", value=f"{st['layer']} / {st['layers']}")
        if st.get("eta_s") and state in ("building", "breaking"):
            e.add_field(name="Time left", value=f"~{fmt_time(st['eta_s'])}")
        if st.get("blocks_placed") is not None:
            e.add_field(name="Placed this run", value=fmt_num(st["blocks_placed"]))
        if st.get("money_spent"):
            e.add_field(name="Spent this run", value=fmt_num(st["money_spent"]))
        if st.get("per_min") and state in ("building",):
            e.add_field(name="Speed", value=f"{fmt_num(st['per_min'])} blocks/min")
        if st.get("spent_per_hour") and state in ("building",):
            e.add_field(name="Spending", value=f"{fmt_num(st['spent_per_hour'])} /hour")
        if st.get("layer_text") and state in ("building",):
            e.add_field(name="Now", value=esc(st["layer_text"])[:100])
        if st.get("kind"):
            e.add_field(name="Build type", value=esc(st["kind"])[:100])
        if st.get("schedule"):
            e.add_field(name="⏰ Schedule", value=esc(st["schedule"])[:100])
        if st.get("server"):
            e.add_field(name="Server", value=esc(st["server"]))
        n_on = sum(1 for k in NOTIFY if wants(p, k))
        seen = f"<t:{p['last_seen']}:R>" if p.get("last_seen") else "never"
        e.set_footer(text=f"Printer Addon {st.get('version', '?')} · {n_on} notifications on (⚙️ to change)")
        if state == "offline" and now() - p.get("last_seen", 0) > 600:
            e.description = (f"Last heard from your game {seen}.\n"
                             "**Game on but still offline here?** The link may be gone (linked from another game, or "
                             "unlinked): type **/link** and press **J + L** in game.")
        else:
            e.description = f"Last update {seen}. The buttons send a command to your game."
        return e

    async def edit_panel(self, uid, force=False):
        p = player_of(uid)
        if not p or not p.get("panel"):
            return
        if not force and time.time() - self.last_edit.get(uid, 0) < PANEL_EDIT_EVERY:
            self.dirty.add(uid)
            return
        self.last_edit[uid] = time.time()
        self.dirty.discard(uid)
        c = self.bot.get_channel(int(p["channel"]))
        if not c:
            return
        try:
            msg = await c.fetch_message(int(p["panel"]))
            await msg.edit(embed=self.panel_embed(uid), view=PanelView(self))
        except discord.NotFound:
            msg = await c.send(embed=self.panel_embed(uid), view=PanelView(self))
            p["panel"] = msg.id
            save_data()

    def owner_of(self, inter):
        for uid, p in DATA["players"].items():
            if p.get("channel") == inter.channel_id:
                return uid
        return None

    async def control(self, inter, action, from_command=False):
        uid = str(inter.user.id) if from_command else self.owner_of(inter)
        if not uid or uid not in DATA["players"]:
            return await inter.response.send_message("Link your game first with **/link**.", ephemeral=True)
        if uid != str(inter.user.id):
            return await inter.response.send_message("Only the owner of this control room can do that.", ephemeral=True)
        p = DATA["players"][uid]
        if now() - p.get("last_seen", 0) > OFFLINE_AFTER:
            return await inter.response.send_message(
                "⚫ Your game is offline, so it can't get commands right now. Start Minecraft and try again.", ephemeral=True)
        if action == "stop":
            return await inter.response.send_message("Stop the build? You can start it again in game.",
                                                     view=ConfirmStop(self, uid), ephemeral=True)
        self.queue(uid, action)
        word = {"pause": "⏸️ Pause", "resume": "▶️ Resume"}[action]
        await inter.response.send_message(f"{word} sent. Your game reacts within a few seconds.", ephemeral=True)

    def notify_text(self, uid):
        p = DATA["players"][uid]
        on = [NOTIFY[k][0] for k in NOTIFY if wants(p, k)]
        return ("**⚙️ Notifications**\nThey show up here in your control room. Pick what you want below.\n"
                f"On: {', '.join(on) if on else 'nothing'}\n"
                f"📳 Ping: {'on' if p.get('ping', True) else 'off'} · ✉️ DM: {'on' if p.get('dm', False) else 'off'}")

    async def notify_settings(self, inter):
        uid = str(inter.user.id)
        if not player_of(uid):
            return await inter.response.send_message("Link your game first with **/link**.", ephemeral=True)
        owner = self.owner_of(inter)
        if owner and owner != uid:
            return await inter.response.send_message("Only the owner can change this.", ephemeral=True)
        await inter.response.send_message(self.notify_text(uid), view=NotifyView(self, uid), ephemeral=True)

    # ------------------------------------------------ alerts
    async def alert(self, uid, kind, text=None, fields=None):
        p = player_of(uid)
        if not p or not wants(p, CATEGORY.get(kind, "problems")):
            return
        emoji, title, ping = ALERTS.get(kind, ALERTS["custom"])
        ping = ping and p.get("ping", True)
        e = discord.Embed(title=f"{emoji} {title}", description=(text or "")[:2000],
                          color=RED if ping and kind != "finished" else (GREEN if kind == "finished" else BRAND),
                          timestamp=discord.utils.utcnow())
        for n, v in (fields or []):
            e.add_field(name=n, value=v)
        c = self.bot.get_channel(int(p["channel"])) if p.get("channel") else None
        if c:
            await c.send(content=f"<@{uid}>" if ping else None, embed=e)
        if p.get("dm", False) and ALERTS.get(kind, ALERTS["custom"])[2]:
            u = self.bot.get_user(int(uid)) or await self.bot.fetch_user(int(uid))
            try:
                await u.send(embed=e)
            except discord.HTTPException:
                pass

    # ------------------------------------------------ stats + achievements
    async def add_run(self, uid, run):
        s = stats_of(uid)
        blocks = max(0, int(run.get("blocks", 0)))
        secs = max(0, int(run.get("seconds", 0)))
        s["blocks"] += blocks
        s["seconds"] += secs
        s["money"] += max(0, int(run.get("money", 0)))
        s["longest"] = max(s["longest"], secs)
        w = s["weekly"]
        if w.get("week") != week_key():
            w.clear()
            w.update({"week": week_key(), "blocks": 0, "seconds": 0, "builds": 0})
        w["blocks"] += blocks
        w["seconds"] += secs
        if run.get("result") == "finished":
            s["builds"] += 1
            w["builds"] += 1
            name = (run.get("build") or "").lower()
            for t in TAGS:
                if t in name:
                    s["tags"][t] = s["tags"].get(t, 0) + 1
        DATA["global"]["blocks"] = DATA["global"].get("blocks", 0) + blocks
        save_data()
        await self.check_achievements(uid)

    async def check_achievements(self, uid):
        s = stats_of(uid)
        p = player_of(uid)
        if p:
            s["logs"] = DATA["log_counts"].get(p["mc"].lower(), 0)
        new = [a for a in ACHIEVEMENTS if a[0] not in s["badges"] and a[3](s)]
        for aid, name, how, _ in new:
            s["badges"].append(aid)
            g = ch("general")
            if g and (not p or wants(p, "achievements")):
                await g.send(embed=discord.Embed(description=f"🏅 <@{uid}> unlocked **{name}** · {how}", color=YELLOW),
                             allowed_mentions=discord.AllowedMentions.none())
        if new:
            save_data()

    def stats_embed(self, member):
        s = stats_of(member.id)
        p = player_of(member.id)
        e = discord.Embed(title=f"📊 {member.display_name}", color=BRAND)
        if p:
            e.description = f"Minecraft: **{esc(p['mc'])}**"
        else:
            e.description = "Not linked yet. Use **/link** so your builds count."
        e.add_field(name="Blocks placed", value=fmt_num(s["blocks"]))
        e.add_field(name="Builds finished", value=str(s["builds"]))
        e.add_field(name="Time automated", value=fmt_time(s["seconds"]))
        e.add_field(name="Longest run", value=fmt_time(s["longest"]))
        e.add_field(name="Spent in shops", value=fmt_num(s["money"]))
        e.add_field(name="Logs sent", value=str(DATA["log_counts"].get(p["mc"].lower(), 0) if p else 0))
        got = [a for a in ACHIEVEMENTS if a[0] in s["badges"]]
        e.add_field(name=f"Achievements ({len(got)}/{len(ACHIEVEMENTS)})",
                    value=" ".join(a[1].split(" ")[0] for a in got) or "None yet: see **/achievements**", inline=False)
        e.set_thumbnail(url=member.display_avatar.url)
        return e

    # ------------------------------------------------ slash commands
    @app_commands.command(description="Show every bot command")
    async def help(self, inter: discord.Interaction):
        await inter.response.send_message(embed=help_embed(), ephemeral=True)

    @app_commands.command(description="Connect Printer Addon to Discord and get your control room")
    async def link(self, inter: discord.Interaction):
        await self.send_code(inter)

    @app_commands.command(description="Disconnect your game from Discord")
    async def unlink(self, inter: discord.Interaction):
        uid = str(inter.user.id)
        p = DATA["players"].pop(uid, None)
        if not p:
            return await inter.response.send_message("You're not linked.", ephemeral=True)
        for h, u in list(DATA["tokens"].items()):
            if u == uid:
                del DATA["tokens"][h]
        save_data()
        c = self.bot.get_channel(int(p["channel"])) if p.get("channel") else None
        if c:
            await c.delete(reason="Player unlinked")
        await inter.response.send_message("Unlinked. Your control room is removed and your game can no longer "
                                          "talk to Discord. Your stats are kept.", ephemeral=True)

    @app_commands.command(description="What your game is doing right now")
    async def status(self, inter: discord.Interaction):
        if not player_of(inter.user.id):
            return await inter.response.send_message("Link your game first with **/link**.", ephemeral=True)
        await inter.response.send_message(embed=self.panel_embed(inter.user.id), ephemeral=True)

    @app_commands.command(description="Pause your build")
    async def pause(self, inter: discord.Interaction):
        await self.control(inter, "pause", from_command=True)

    @app_commands.command(description="Resume your build")
    async def resume(self, inter: discord.Interaction):
        await self.control(inter, "resume", from_command=True)

    @app_commands.command(description="Stop your build")
    async def stop(self, inter: discord.Interaction):
        await self.control(inter, "stop", from_command=True)

    @app_commands.command(description="Stop your build at a time, and/or keep money in the bank")
    @app_commands.describe(stop_at="Time of day to stop, like 07:00 (your computer's clock). Empty: no change",
                           keep_money="Never spend below this, like 500k or 2m. 0: no limit",
                           clear="Remove the stop time and the money limit")
    async def schedule(self, inter: discord.Interaction, stop_at: str = None, keep_money: str = None, clear: bool = False):
        uid = str(inter.user.id)
        if not player_of(uid):
            return await inter.response.send_message("Link your game first with **/link**.", ephemeral=True)
        p = DATA["players"][uid]
        if now() - p.get("last_seen", 0) > OFFLINE_AFTER:
            return await inter.response.send_message(
                "⚫ Your game is offline, so it can't get this right now. Start Minecraft and try again.", ephemeral=True)
        if clear:
            spec, err = "schedule:clear", None
        else:
            spec, err = schedule_spec(stop_at, keep_money)
        if err:
            return await inter.response.send_message(err, ephemeral=True)
        self.queue(uid, spec)
        await inter.response.send_message("⏰ Schedule sent. Your game confirms it in a few seconds (in your control room).",
                                          ephemeral=True)

    @app_commands.command(description="Choose which notifications you get")
    async def notifications(self, inter: discord.Interaction):
        await self.notify_settings(inter)

    @app_commands.command(description="Printer Addon stats and achievements")
    async def stats(self, inter: discord.Interaction, member: discord.Member = None):
        await inter.response.send_message(embed=self.stats_embed(member or inter.user))

    @app_commands.command(description="Top printers")
    @app_commands.choices(period=[app_commands.Choice(name="This week", value="week"),
                                  app_commands.Choice(name="All time", value="all")],
                          by=[app_commands.Choice(name="Blocks placed", value="blocks"),
                              app_commands.Choice(name="Builds finished", value="builds"),
                              app_commands.Choice(name="Time automated", value="seconds")])
    async def leaderboard(self, inter: discord.Interaction, period: app_commands.Choice[str] = None,
                          by: app_commands.Choice[str] = None):
        period = period.value if period else "week"
        key = by.value if by else "blocks"
        rows = []
        for uid, s in DATA["stats"].items():
            src = s.get("weekly", {}) if period == "week" else s
            if period == "week" and src.get("week") != week_key():
                continue
            if src.get(key, 0) > 0:
                rows.append((src[key], uid))
        rows.sort(reverse=True)
        medal = ["🥇", "🥈", "🥉"] + ["▫️"] * 7
        show = fmt_time if key == "seconds" else fmt_num
        lines = [f"{medal[i]} <@{uid}> · **{show(v)}**" for i, (v, uid) in enumerate(rows[:10])]
        title = {"blocks": "Blocks placed", "builds": "Builds finished", "seconds": "Time automated"}[key]
        e = discord.Embed(title=f"🏆 {title} · {'this week' if period == 'week' else 'all time'}", color=YELLOW,
                          description="\n".join(lines) or "Nobody yet. Link your game with **/link** and start a build!")
        await inter.response.send_message(embed=e, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(description="Every achievement and how to get it")
    async def achievements(self, inter: discord.Interaction):
        got = set(stats_of(inter.user.id)["badges"])
        lines = [f"{'✅' if a[0] in got else '⬜'} **{a[1]}** · {a[2]}" for a in ACHIEVEMENTS]
        await inter.response.send_message(embed=discord.Embed(title="🏅 Achievements", description="\n".join(lines),
                                                              color=YELLOW), ephemeral=True)

    # ------------------------------------------------ dev replies -> game
    @commands.Cog.listener()
    async def on_message(self, msg):
        if msg.author.bot or not msg.guild or not is_staff(msg.author):
            return
        # staff writes in a bug thread -> reporter sees it in game
        if isinstance(msg.channel, discord.Thread) and msg.channel.parent_id == int(CH["bug_reports"]):
            for n, b in DATA["bugs"].items():
                if b["msg"] == msg.channel.id and str(b["user"]) != str(msg.author.id):
                    await notify_player(str(b["user"]), msg.content, about=f"Bug #{n}", sender=msg.author.display_name)
        # staff replies to a report in #player-reports -> that player sees it in game
        if msg.channel.id == int(CH["player_reports"]) and msg.reference and msg.reference.message_id:
            try:
                ref = await msg.channel.fetch_message(msg.reference.message_id)
            except discord.NotFound:
                return
            title = ref.embeds[0].title if ref.embeds else ""
            for prefix in ("Log from ", "Problem from "):
                if title.startswith(prefix):
                    mc = title[len(prefix):].split(":")[0].strip()
                    uid = uid_by_mc(mc)
                    if uid and await notify_player(uid, msg.content, about="Your report", sender=msg.author.display_name):
                        await msg.add_reaction("📨")

    # ------------------------------------------------ suggestion votes (reactions)
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, ev):
        await self._vote_reaction(ev, +1)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, ev):
        await self._vote_reaction(ev, -1)

    async def _vote_reaction(self, ev, d):
        if ev.user_id == self.bot.user.id:
            return
        for s in DATA["suggestions"].values():
            if s["msg"] == ev.message_id and str(ev.emoji) in ("✅", "❌"):
                k = "up" if str(ev.emoji) == "✅" else "down"
                s[k] = max(0, s.get(k, 0) + d)
                save_data()

    # ------------------------------------------------ background loops
    @tasks.loop(seconds=30)
    async def loop_panels(self):
        for uid, p in list(DATA["players"].items()):
            was_online = p.get("_online", False)
            online = now() - p.get("last_seen", 0) < OFFLINE_AFTER
            if was_online != online:
                p["_online"] = online
                await self.edit_panel(uid, force=True)
            elif uid in self.dirty:
                await self.edit_panel(uid, force=True)
        printing = sum(1 for p in DATA["players"].values()
                       if now() - p.get("last_seen", 0) < OFFLINE_AFTER and (p.get("status") or {}).get("state") in ("building", "breaking"))
        rel = DATA.get("release", {}).get("version", "")
        text = f"{printing} printing now" if printing else f"Printer Addon {rel}".strip()
        await self.bot.change_presence(activity=discord.Game(text))

    @tasks.loop(minutes=10)
    async def loop_counter(self):
        c = ch("stat_blocks")
        if c:
            name = f"🧱 Blocks printed: {fmt_num(DATA['global'].get('blocks', 0))}"
            if c.name != name:
                await c.edit(name=name)

    @loop_panels.before_loop
    @loop_counter.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------ API handlers
    async def h_health(self, request):
        return web.json_response({"ok": True, "bot": str(self.bot.user), "api": API_VERSION})

    async def h_version(self, request):
        r = DATA.get("release", {})
        return web.json_response({"latest": r.get("version"), "date": r.get("date"), "notes": r.get("notes"),
                                  "download": r.get("url"), "sha256": r.get("sha256")})

    async def h_suggestions(self, request):
        rows = [{"number": int(n), "title": s["title"], "status": s.get("status", "Open"),
                 "up": s.get("up", 0) + sum(1 for v in s.get("game_votes", {}).values() if v == "up"),
                 "down": s.get("down", 0) + sum(1 for v in s.get("game_votes", {}).values() if v == "down")}
                for n, s in DATA["suggestions"].items() if s.get("status", "Open") in ("Open", "Planned")]
        rows.sort(key=lambda r: r["up"] - r["down"], reverse=True)
        return web.json_response({"suggestions": rows[:20]})

    def link_limited(self, request):
        """At most 5 wrong codes per address in 10 minutes (codes can't be guessed)."""
        ip = request.headers.get("X-Forwarded-For", request.remote or "?").split(",")[0].strip()
        tries = [t for t in self.bad_links.get(ip, []) if time.time() - t < 600]
        self.bad_links[ip] = tries
        return ip, len(tries) >= 5

    async def h_link(self, request):
        ip, limited = self.link_limited(request)
        if limited:
            return web.json_response({"ok": False, "error": "Too many wrong codes. Wait 10 minutes and get a new one with /link."}, status=429)
        b = await self.body(request)
        code = str(b.get("code", "")).strip().upper()
        if not code.startswith("PRINT-"):
            code = "PRINT-" + code
        player = str(b.get("player", "")).strip()[:16]
        info = DATA["codes"].pop(code, None)
        test_file = os.path.join(HERE, "test_codes.json")  # local testing only: {"PRINT-XXXX": discord_user_id}
        if not info and os.path.exists(test_file):
            tests = json.load(open(test_file))
            if code in tests:
                info = {"user": tests.pop(code), "expires": now() + 60}
                json.dump(tests, open(test_file, "w"))
        if not info or info["expires"] < now():
            self.bad_links.setdefault(ip, []).append(time.time())
            save_data()
            return web.json_response({"ok": False, "error": "That code is wrong or expired. Get a new one with /link."}, status=400)
        if not player:
            return web.json_response({"ok": False, "error": "missing player name"}, status=400)
        uid = str(info["user"])
        for h, u in list(DATA["tokens"].items()):  # one game per Discord user
            if u == uid:
                del DATA["tokens"][h]
        token = secrets.token_urlsafe(32)
        DATA["tokens"][hashlib.sha256(token.encode()).hexdigest()] = uid
        p = DATA["players"].setdefault(uid, {"dm": False, "ping": True})
        p.update({"mc": player, "linked_at": now(), "last_seen": now()})
        DATA["links"][player.lower()] = int(uid)
        save_data()
        guild = self.bot.get_guild(GUILD.id)
        member = guild.get_member(int(uid)) or await guild.fetch_member(int(uid))
        relinked = bool(p.get("channel"))
        c = await self.control_room(guild, member, player)
        if relinked:
            await c.send(embed=discord.Embed(description=f"🔗 Linked again from **{esc(player)}**. An older link from "
                                                         "another game stopped working.", color=GREEN))
        await log("🔗 Game linked", f"{member.mention} ↔ **{esc(player)}** · {c.mention}", GREEN, user=member)
        await self.check_achievements(uid)
        return web.json_response({"ok": True, "token": token, "discord": member.display_name,
                                  "message": f"Linked to {member.display_name} on Discord!"})

    async def h_unlink(self, request):
        uid = self.auth(request)
        for h, u in list(DATA["tokens"].items()):
            if u == uid:
                del DATA["tokens"][h]
        save_data()
        return web.json_response({"ok": True})

    async def h_status(self, request):
        uid = self.auth(request)
        b = await self.body(request)
        if time.time() - self.last_status_at.get(uid, 0) < 2:
            return web.json_response({"ok": True, "throttled": True})
        self.last_status_at[uid] = time.time()
        keep = {"state": str, "detail": str, "build": str, "progress": float, "layer": int, "layers": int,
                "eta_s": int, "blocks_placed": int, "money_spent": int, "version": str, "server": str,
                "layer_text": str, "per_min": int, "spent_per_hour": int, "schedule": str, "kind": str}
        st = {}
        for k, t in keep.items():
            if b.get(k) is not None:
                try:
                    st[k] = t(b[k])[:200] if t is str else t(b[k])
                except (TypeError, ValueError):
                    pass
        p = DATA["players"][uid]
        p["status"] = st
        p["last_seen"] = now()
        await self.edit_panel(uid)
        return web.json_response({"ok": True})

    async def h_event(self, request):
        uid = self.auth(request)
        b = await self.body(request)
        kind = str(b.get("type", "custom"))
        text = esc(str(b.get("message", ""))[:1500])
        DATA["players"][uid]["last_seen"] = now()
        if kind == "run_end":
            run = b.get("run") or {}
            await self.add_run(uid, run)
            result = run.get("result", "stopped")
            fields = [("Blocks", fmt_num(run.get("blocks", 0))), ("Time", fmt_time(run.get("seconds", 0)))]
            if run.get("money"):
                fields.append(("Spent", fmt_num(run["money"])))
            raw_build = re.sub(r",\s*Y -?\d+ to -?\d+\s*$", "", re.sub(r"(?i)^auto build:?\s*", "", str(run.get("build", ""))))
            build = esc(raw_build)[:200]
            kind = "finished" if result == "finished" else (run.get("kind") if run.get("kind") in ALERTS else "error") if result == "problem" else "stopped"
            text = (f"**{build}**\n" if build else "") + text
            await self.alert(uid, kind, text, fields)
            p = DATA["players"][uid]
            lib = self.bot.get_cog("Library")
            c = self.bot.get_channel(int(p["channel"])) if p.get("channel") else None
            if kind == "finished" and lib and c and wants(p, "timelapse"):
                asyncio.create_task(lib.post_timelapse(uid, c, raw_build[:80]))
        else:
            if kind not in ALERTS:
                kind = "custom"
            await self.alert(uid, kind, text)
        await self.edit_panel(uid, force=True)
        return web.json_response({"ok": True})

    async def h_poll(self, request):
        uid = self.auth(request)
        p = DATA["players"][uid]
        p["last_seen"] = now()
        cmds = [c for c in p.pop("commands", []) if now() - c["ts"] < 120]
        msgs = p.pop("inbox", [])
        save_data()
        r = DATA.get("release", {})
        return web.json_response({"commands": cmds, "messages": msgs, "latest": r.get("version"),
                                  "upload_schematic": wants(p, "timelapse")})

    async def h_shopping(self, request):
        uid = self.auth(request)
        b = await self.body(request)
        items = b.get("items") or []
        lines = []
        for it in items[:60]:
            try:
                lines.append(f"• {int(it.get('count', 0)):,} × {esc(str(it.get('name', '?')))[:40]}"
                             + (f" · {fmt_num(it['price'])}" if it.get("price") else ""))
            except (TypeError, ValueError, AttributeError):
                continue
        text = (f"**{esc(str(b.get('build', '')))[:200]}**\n" if b.get("build") else "") + "\n".join(lines)
        if b.get("total"):
            text += f"\n\n**Total:** {fmt_num(b['total'])}"
        p = DATA["players"][uid]
        if not wants(p, "shopping"):
            return web.json_response({"ok": True, "skipped": True})
        c = self.bot.get_channel(int(p["channel"])) if p.get("channel") else None
        if c:
            await c.send(embed=discord.Embed(title="🛒 Shopping list", description=text[:4000], color=BRAND))
        return web.json_response({"ok": True})

    # ------------------------------------------------ logs and reports from the game (instead of a webhook in the jar)
    def client_ip(self, request):
        return request.headers.get("X-Forwarded-For", request.remote or "?").split(",")[0].strip()

    async def h_install(self, request):
        """Every game gets its own key once (no Discord link needed): reports are sent with it, and a key that spams
        can be blocked on its own. At most 5 new keys per address an hour."""
        ip = self.client_ip(request)
        recent = [t for t in self.installs_by_ip.get(ip, []) if time.time() - t < 3600]
        if len(recent) >= 5:
            return web.json_response({"ok": False, "error": "too many new keys, try again later"}, status=429)
        recent.append(time.time())
        self.installs_by_ip[ip] = recent
        key = secrets.token_urlsafe(24)
        DATA.setdefault("installs", {})[hashlib.sha256(key.encode()).hexdigest()] = {"at": now(), "reports": [], "blocked": False}
        save_data()
        return web.json_response({"ok": True, "key": key})

    async def h_report(self, request):
        """A log or problem report: multipart with 'payload' (JSON: title, description, fields, color, content) and up
        to 6 files, 9.5 MB in all. Posted in #player-reports by the bot. With the install key (X-Install-Key)."""
        key = request.headers.get("X-Install-Key", "")
        inst = DATA.get("installs", {}).get(hashlib.sha256(key.encode()).hexdigest())
        if not inst or inst.get("blocked"):
            raise web.HTTPUnauthorized(text="unknown or blocked install key: get one from /install")
        hour = [t for t in inst.get("reports", []) if time.time() - t < 3600]
        if len(hour) >= 30:
            return web.json_response({"ok": False, "error": "too many reports this hour"}, status=429)
        reader = await request.multipart()
        payload, files, size = {}, [], 0
        async for part in reader:
            if part.name == "payload":
                payload = json.loads(await part.text())
            elif part.filename and len(files) < 6:
                data = await part.read(decode=False)
                size += len(data)
                if size > 9_900_000:
                    return web.json_response({"ok": False, "error": "files too big (9.5 MB in all)"}, status=413)
                files.append(discord.File(io.BytesIO(data), filename=os.path.basename(part.filename)[:100]))
        e = discord.Embed(title=str(payload.get("title", "Report"))[:256], description=str(payload.get("description", ""))[:4000],
                          color=int(payload.get("color", BRAND)) if str(payload.get("color", "")).isdigit() else BRAND)
        for f in (payload.get("fields") or [])[:20]:
            e.add_field(name=str(f.get("name", "-"))[:256], value=str(f.get("value", "-"))[:1024], inline=bool(f.get("inline")))
        content = str(payload.get("content", ""))[:1900] or None
        msg = await ch("player_reports").send(content=content, embed=e, files=files, allowed_mentions=discord.AllowedMentions.none())
        hour.append(time.time())
        inst["reports"] = hour
        save_data()
        return web.json_response({"ok": True, "id": str(msg.id), "url": msg.jump_url})

    async def h_prices(self, request):
        """Shop prices from a player's shop scan (J+S), per server: the library says what a build costs there."""
        self.auth(request)
        b = await self.body(request)
        server = str(b.get("server", "")).lower()[:80]
        prices = b.get("prices") or {}
        clean = {}
        for k, v in list(prices.items())[:3000]:
            try:
                if isinstance(k, str) and 0 < float(v) < 1e9:
                    clean[k[:80]] = float(v)
            except (TypeError, ValueError):
                pass
        if server and clean:
            DATA.setdefault("prices", {})[server] = {"prices": clean, "at": now()}
            save_data()
        return web.json_response({"ok": True, "items": len(clean)})

    async def h_schematics(self, request):
        lib = self.bot.get_cog("Library")
        rows = lib.find(request.query.get("q", ""))[:30] if lib else []
        from library import cost_of
        server = request.query.get("server", "").lower() or None
        return web.json_response({"schematics": [
            {"id": s["id"], "name": s["name"], "size": s["size"], "blocks": s["blocks"], "tag": s.get("tag"),
             "stars": s.get("stars", 0), "cost": cost_of(s, server)[0],
             "by": (self.bot.get_user(s["user"]).display_name if self.bot.get_user(s["user"]) else "?")} for s in rows]})

    async def h_schematic_file(self, request):
        lib = self.bot.get_cog("Library")
        sid = request.match_info["sid"]
        path = lib.file_path(sid) if lib else None
        if not path:
            raise web.HTTPNotFound(text="unknown schematic")
        name = DATA["schematics"].get(sid, {}).get("file", f"{sid}.litematic")
        return web.FileResponse(path, headers={"Content-Disposition": f'attachment; filename="{name}"'})

    async def h_build_schematic(self, request):
        uid = self.auth(request)
        data = await request.read()
        lib = self.bot.get_cog("Library")
        if not lib or not data:
            return web.json_response({"ok": False}, status=400)
        try:
            lib.save_run(uid, data)
        except ValueError:
            return web.json_response({"ok": False, "error": "too big"}, status=413)
        return web.json_response({"ok": True})

    async def h_admin_timelapse(self, request):
        """Makes the time-lapse of a player's last build again (when it failed)."""
        key = env("ADMIN_KEY", "")
        if not key or request.headers.get("X-Admin-Key") != key:
            raise web.HTTPUnauthorized(text="no")
        uid, build = request.query.get("uid", ""), request.query.get("build", "")
        p = DATA["players"].get(uid)
        lib = self.bot.get_cog("Library")
        c = self.bot.get_channel(int(p["channel"])) if p and p.get("channel") else None
        if not (lib and c):
            return web.json_response({"ok": False}, status=404)
        await lib.post_timelapse(uid, c, build)
        return web.json_response({"ok": True})

    async def h_admin_log(self, request):
        """The developer reads the bot's log (the end of logs/bot.log) and the runs folder, with the admin key."""
        key = env("ADMIN_KEY", "")
        if not key or request.headers.get("X-Admin-Key") != key:
            raise web.HTTPUnauthorized(text="no")
        if request.query.get("run"):
            path = os.path.join(HERE, "library", "_runs", os.path.basename(request.query["run"]))
            if os.path.exists(path):
                return web.FileResponse(path)
            raise web.HTTPNotFound()
        n = min(2000, int(request.query.get("lines", "300")))
        try:
            lines = open(os.path.join(HERE, "logs", "bot.log"), errors="replace").read().splitlines()[-n:]
        except FileNotFoundError:
            lines = []
        runs = os.path.join(HERE, "library", "_runs")
        files = {f: [os.path.getsize(os.path.join(runs, f)), int(os.path.getmtime(os.path.join(runs, f)))]
                 for f in (os.listdir(runs) if os.path.isdir(runs) else [])}
        tex = os.path.join(HERE, "library", "textures")
        return web.json_response({"log": lines, "runs": files, "textures": len(os.listdir(tex)) if os.path.isdir(tex) else 0})

    async def h_admin_release(self, request):
        """The developer's Mac sends a new version here (sync.py), so releases work with the bot on the droplet."""
        key = env("ADMIN_KEY", "")
        if not key or request.headers.get("X-Admin-Key") != key:
            raise web.HTTPUnauthorized(text="no")
        import base64
        b = await self.body(request)
        rel = self.bot.get_cog("Releases")
        version = str(b.get("version", ""))
        if not rel or not version:
            return web.json_response({"ok": False}, status=400)
        from releases import vkey
        if vkey(version) <= vkey(DATA.get("release", {}).get("version", "0")):
            return web.json_response({"ok": True, "skipped": "already out"})
        if rel.busy:
            return web.json_response({"ok": False, "error": "busy"}, status=409)
        rel.busy = True
        try:
            card = base64.b64decode(b["card"]) if b.get("card") else None
            await rel.publish(version, base64.b64decode(b["jar"]), b["filename"], b["lines"], b.get("props") or {}, image=card)
        finally:
            rel.busy = False
        return web.json_response({"ok": True, "released": version})

    async def h_vote(self, request):
        uid = self.auth(request)
        b = await self.body(request)
        s = DATA["suggestions"].get(str(b.get("number")))
        if not s or b.get("vote") not in ("up", "down", None):
            return web.json_response({"ok": False, "error": "unknown suggestion or vote"}, status=400)
        votes = s.setdefault("game_votes", {})
        if b.get("vote") is None:
            votes.pop(uid, None)
        else:
            votes[uid] = b["vote"]
        save_data()
        return web.json_response({"ok": True})


async def setup(bot):
    await bot.add_cog(Player(bot))
