"""Shared objects for the bot: config, data, the bot instance and helpers."""
import datetime as dt
import hashlib
import json
import os

import discord
from discord import app_commands
from discord.ext import commands

HERE = os.path.dirname(os.path.abspath(__file__))
from discord_api import load_token, GUILD_ID  # token is read from .env, never printed

STATE = json.load(open(os.path.join(HERE, "state.json")))
CH, ROLE = STATE["channels"], STATE["roles"]
DATA_FILE = os.path.join(HERE, "data.json")
BRAND, GREEN, RED, YELLOW, GREY = 0x3B82F6, 0x22C55E, 0xEF4444, 0xF59E0B, 0x64748B
GUILD = discord.Object(id=int(GUILD_ID))


def load_data():
    try:
        d = json.load(open(DATA_FILE))
    except (FileNotFoundError, json.JSONDecodeError):
        d = {}
    for k, v in {"suggestions": {}, "bugs": {}, "counter": {"suggestion": 0, "bug": 0}, "links": {}, "log_counts": {},
                 "players": {}, "tokens": {}, "codes": {}, "stats": {}, "global": {"blocks": 0}}.items():
        d.setdefault(k, v)
    return d


def save_data():
    tmp = DATA_FILE + ".tmp"
    json.dump(DATA, open(tmp, "w"), indent=1)
    os.replace(tmp, DATA_FILE)


DATA = load_data()
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


def ch(key):
    return bot.get_channel(int(CH[key]))


def is_staff(member):
    ids = {r.id for r in getattr(member, "roles", [])}
    return member.guild_permissions.administrator or int(ROLE["developer"]) in ids or int(ROLE["moderator"]) in ids


async def log(title, description="", color=GREY, fields=None, user=None):
    c = ch("bot_logs")
    if not c:
        return
    e = discord.Embed(title=title, description=description[:4000], color=color, timestamp=discord.utils.utcnow())
    for name, value in (fields or []):
        e.add_field(name=name, value=(str(value) or "-")[:1024], inline=False)
    if user:
        e.set_author(name=str(user), icon_url=user.display_avatar.url)
        e.set_footer(text=f"User ID {user.id}")
    await c.send(embed=e, allowed_mentions=discord.AllowedMentions.none())




def player_of(uid):
    return DATA["players"].get(str(uid))


def uid_by_mc(mc_name):
    mc_name = (mc_name or "").lower()
    for uid, p in DATA["players"].items():
        if p.get("mc", "").lower() == mc_name:
            return uid
    return None


async def notify_player(uid, text, about=None, sender=None):
    """Send a message to a linked player: shows in game (next poll) and in their control room."""
    p = player_of(uid)
    if not p:
        return False
    p.setdefault("inbox", []).append({"text": text[:1000], "about": about, "from": sender,
                                      "ts": int(discord.utils.utcnow().timestamp())})
    p["inbox"] = p["inbox"][-20:]
    save_data()
    c = bot.get_channel(int(p["channel"])) if p.get("channel") else None
    if c:
        e = discord.Embed(description=text[:4000], color=BRAND)
        e.set_author(name=f"Message{' from ' + sender if sender else ''}{' · ' + about if about else ''}")
        await c.send(embed=e, allowed_mentions=discord.AllowedMentions.none())
    return True


FAQ = {
    "install": ("How do I install it?", f"Put Fabric API, MaLiLib, Litematica, Litematica Printer and Printer Addon "
                                        f"in `.minecraft/mods` for Minecraft 26.2. Full guide: <#{CH['getting_started']}>"),
    "start": ("How do I start a build?", "Load the schematic in Litematica, press **J + B** and click "
                                         "**▶ START auto build**. Stop with **■ STOP** or **J + Q**."),
    "shop": ("It doesn't buy items", "Press **J + S** once per server to scan the shop, and check your spending limit."),
    "vaults": ("It doesn't use my vaults", "Press **J + C** and enter your vault numbers under *Important*, e.g. `1,2`."),
    "chests": ("It doesn't use my chest", "Look at the chest, press **J + B** and click *Link*. Only linked chests are used."),
    "report": ("How do I report a bug?", f"In game: **J + B** → **Report / share log**, then **/bug** in <#{CH['bug_reports']}>."),
    "download": ("Where do I download it?", f"Only from <#{CH['downloads']}>."),
}

