"""Autoprinter: the Printer Addon Discord bot.
Run:  ./start.command   (or: python3 bot.py)"""
import datetime as dt
import hashlib
import io
import json
import os
import re

import discord
from discord import app_commands

from common import *  # bot, DATA, CH, ROLE, colors, ch(), log(), is_staff(), save_data()
from discord_api import load_token

# ---------------------------------------------------------------- events
@bot.event
async def on_ready():
    await bot.change_presence(activity=discord.Game("Printing schematics..."))
    bot.tree.copy_global_to(guild=GUILD)
    await bot.tree.sync(guild=GUILD)
    print(f"Online as {bot.user}")
    await recount_logs()
    for name in DATA.get("links", {}):
        await check_logger(bot.get_guild(GUILD.id), name)
    await log("🟢 Bot started", f"Online as {bot.user.mention}", GREEN)


@bot.event
async def on_member_join(m):
    if m.guild.id != GUILD.id:
        return
    role = m.guild.get_role(int(ROLE["printers"]))
    if role and not m.bot:
        await m.add_roles(role, reason="Auto role")
    if m.bot:
        b = m.guild.get_role(int(ROLE["bots"]))
        if b:
            await m.add_roles(b, reason="Bot role")
        return
    g = ch("general")
    if g:
        e = discord.Embed(
            description=(f"Welcome {m.mention}! 👋\n\n"
                         f"📖 Get started in {ch('getting_started').mention}\n"
                         f"📥 Download in {ch('downloads').mention}\n"
                         f"📜 Read the {ch('rules').mention}"),
            color=BRAND)
        e.set_thumbnail(url=m.display_avatar.url)
        await g.send(embed=e)
    age = (discord.utils.utcnow() - m.created_at).days
    await log("📥 Member joined", f"{m.mention} · account {age} days old" + (" ⚠️ new account" if age < 7 else ""),
              GREEN, user=m)


@bot.event
async def on_member_remove(m):
    roles = ", ".join(r.name for r in m.roles if r.name != "@everyone") or "none"
    await log("📤 Member left", f"{m.mention}", YELLOW, [("Roles", roles)], user=m)


@bot.event
async def on_member_update(before, after):
    added = [r.name for r in after.roles if r not in before.roles]
    removed = [r.name for r in before.roles if r not in after.roles]
    if added or removed:
        await log("🎭 Roles changed", after.mention, BRAND,
                  [("Added", ", ".join(added) or "-"), ("Removed", ", ".join(removed) or "-")], user=after)
    if before.nick != after.nick:
        await log("✏️ Nickname changed", after.mention, BRAND, [("Before", before.nick), ("After", after.nick)], user=after)
    if before.timed_out_until != after.timed_out_until and after.timed_out_until:
        await log("⏳ Timed out", f"{after.mention} until {discord.utils.format_dt(after.timed_out_until)}", RED, user=after)


@bot.event
async def on_message_delete(msg):
    if msg.author.bot or not msg.guild or msg.channel.id == int(CH["bot_logs"]):
        return
    files = "\n".join(a.filename for a in msg.attachments)
    await log("🗑️ Message deleted", f"in {msg.channel.mention}", RED,
              [("Content", msg.content or "(no text)")] + ([("Files", files)] if files else []), user=msg.author)


@bot.event
async def on_bulk_message_delete(msgs):
    if msgs and msgs[0].channel.id != int(CH["bot_logs"]):
        await log("🗑️ Bulk delete", f"{len(msgs)} messages in {msgs[0].channel.mention}", RED)


@bot.event
async def on_message_edit(before, after):
    if after.author.bot or not after.guild or before.content == after.content:
        return
    await log("✏️ Message edited", f"in {after.channel.mention} · [jump]({after.jump_url})", YELLOW,
              [("Before", before.content or "-"), ("After", after.content or "-")], user=after.author)


@bot.event
async def on_member_ban(guild, user):
    await log("🔨 Banned", user.mention, RED, user=user)


@bot.event
async def on_member_unban(guild, user):
    await log("♻️ Unbanned", user.mention, GREEN, user=user)


@bot.event
async def on_guild_channel_create(c):
    await log("➕ Channel created", c.mention, GREEN)


@bot.event
async def on_guild_channel_delete(c):
    await log("➖ Channel deleted", f"#{c.name}", RED)


@bot.event
async def on_guild_channel_update(before, after):
    changes = []
    if before.name != after.name:
        changes.append(("Name", f"{before.name} → {after.name}"))
    if getattr(before, "topic", None) != getattr(after, "topic", None):
        changes.append(("Topic", f"{before.topic} → {after.topic}"))
    if before.overwrites != after.overwrites:
        changes.append(("Permissions", "changed"))
    if changes:
        await log("🔧 Channel changed", after.mention, BRAND, changes)


@bot.event
async def on_guild_role_create(r):
    await log("➕ Role created", r.name, GREEN)


@bot.event
async def on_guild_role_delete(r):
    await log("➖ Role deleted", r.name, RED)


@bot.event
async def on_audit_log_entry_create(entry):
    """Who did it: every audit log entry, short."""
    who = entry.user.mention if entry.user else "someone"
    try:
        target = getattr(entry.target, "mention", None) or str(entry.target)
    except (TypeError, ValueError):
        target = "-"
    reason = f"\nReason: {entry.reason}" if entry.reason else ""
    await log("📋 Audit log", f"{who} · **{entry.action.name.replace('_', ' ')}** · {target}{reason}", GREY)


@bot.event
async def on_message(msg):
    if msg.guild and msg.channel.id == int(CH["player_reports"]):
        name = log_player(msg)
        if name:
            DATA.setdefault("log_counts", {})[name] = DATA["log_counts"].get(name, 0) + 1
            save_data()
            await check_logger(msg.guild, name)
    if msg.author.bot or not msg.guild:
        return
    await bot.process_commands(msg)



# ---------------------------------------------------------------- Logger role
# The mod posts "Log from <name>" / "Problem from <name>" in #player-reports (via its webhook).
# Players link their Minecraft name with /link; at LOGGER_MIN logs they get the Logger role.
LOGGER_MIN = 5
LOG_TITLE = re.compile(r"^(?:Log|Problem) from ([A-Za-z0-9_]{2,16})")


def log_player(msg):
    # reports come from the mod's webhook, or (newer versions) through the bot's own API
    if not (msg.webhook_id or (bot.user and msg.author.id == bot.user.id)) or not msg.embeds:
        return None
    m = LOG_TITLE.match(msg.embeds[0].title or "")
    return m.group(1).lower() if m else None


async def recount_logs():
    counts = {}
    async for msg in ch("player_reports").history(limit=None):
        name = log_player(msg)
        if name:
            counts[name] = counts.get(name, 0) + 1
    DATA["log_counts"] = counts
    save_data()


async def check_logger(guild, mc_name):
    did = uid_by_mc(mc_name) or DATA.setdefault("links", {}).get(mc_name)
    if not did or DATA["log_counts"].get(mc_name, 0) < LOGGER_MIN:
        return
    member = guild.get_member(int(did))
    role = guild.get_role(int(ROLE["logger"]))
    if member and role and role not in member.roles:
        await member.add_roles(role, reason=f"{LOGGER_MIN}+ logs sent")
        g = ch("general")
        if g:
            await g.send(embed=discord.Embed(
                description=f"📜 {member.mention} sent **{DATA['log_counts'][mc_name]} logs** and is now a **Logger**. "
                            f"Thanks for helping make Printer Addon better!", color=0xF59E0B))


@bot.tree.command(description="See how many logs you have sent")
async def logs(inter: discord.Interaction, member: discord.Member = None):
    member = member or inter.user
    p = player_of(member.id)
    name = p["mc"].lower() if p else None
    if not name:
        return await inter.response.send_message("Not linked yet. Use **/link** to connect your game.", ephemeral=True)
    n = DATA.get("log_counts", {}).get(name, 0)
    await inter.response.send_message(f"**{name}** has sent **{n}** logs ({LOGGER_MIN} needed for Logger).", ephemeral=True)


# ---------------------------------------------------------------- releases
@bot.tree.command(description="Developer: post a version by hand (normally automatic from the mod repo)")
@app_commands.describe(version="e.g. 0.16.1", jar="The .jar file", notes="The changes, separated by ;",
                       image="Optional release picture", ping="Ping the Updates role")
@app_commands.default_permissions(administrator=True)
async def release(inter: discord.Interaction, version: str, jar: discord.Attachment, notes: str,
                  image: discord.Attachment = None, ping: bool = True):
    await inter.response.defer(ephemeral=True)
    lines = [l.strip() for l in notes.replace("\\n", ";").split(";") if l.strip()]
    img = await image.read() if image else None
    await bot.get_cog("Releases").publish(version, await jar.read(), jar.filename, lines, image=img, ping=ping)
    await inter.followup.send(f"Released {version}.", ephemeral=True)


# ---------------------------------------------------------------- writing as the bot
class PostModal(discord.ui.Modal):
    title_in = discord.ui.TextInput(label="Title", max_length=256, required=False)
    body = discord.ui.TextInput(label="Text", style=discord.TextStyle.paragraph, max_length=4000)

    def __init__(self, channel=None, message=None):
        super().__init__(title="Edit post" if message else "New post")
        self.channel, self.message = channel, message
        if message and message.embeds:
            self.title_in.default = message.embeds[0].title
            self.body.default = message.embeds[0].description

    async def on_submit(self, inter):
        e = discord.Embed(title=self.title_in.value or None, description=self.body.value, color=BRAND)
        if self.message:
            await self.message.edit(embed=e)
            await inter.response.send_message("Edited.", ephemeral=True)
        else:
            m = await self.channel.send(embed=e)
            await inter.response.send_message(f"Posted: {m.jump_url}", ephemeral=True)


@bot.tree.command(description="Developer: post a message as the bot")
@app_commands.default_permissions(administrator=True)
async def post(inter: discord.Interaction, channel: discord.TextChannel):
    await inter.response.send_modal(PostModal(channel=channel))


@bot.tree.command(description="Developer: edit a message the bot posted (paste the message link)")
@app_commands.default_permissions(administrator=True)
async def edit(inter: discord.Interaction, message_link: str):
    try:
        parts = message_link.rstrip("/").split("/")
        c = bot.get_channel(int(parts[-2]))
        m = await c.fetch_message(int(parts[-1]))
    except Exception:
        return await inter.response.send_message("Couldn't find that message.", ephemeral=True)
    if m.author.id != bot.user.id:
        return await inter.response.send_message("I can only edit my own messages.", ephemeral=True)
    await inter.response.send_modal(PostModal(message=m))


# ---------------------------------------------------------------- moderation
@bot.tree.command(description="Mod: time out a member")
@app_commands.default_permissions(moderate_members=True)
async def timeout(inter: discord.Interaction, member: discord.Member, minutes: app_commands.Range[int, 1, 40320],
                  reason: str = "No reason"):
    await member.timeout(dt.timedelta(minutes=minutes), reason=f"{inter.user}: {reason}")
    await inter.response.send_message(f"{member.mention} timed out for {minutes} min.", ephemeral=True)


@bot.tree.command(description="Mod: kick a member")
@app_commands.default_permissions(kick_members=True)
async def kick(inter: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await member.kick(reason=f"{inter.user}: {reason}")
    await inter.response.send_message(f"Kicked {member}.", ephemeral=True)


@bot.tree.command(description="Mod: ban a member")
@app_commands.default_permissions(ban_members=True)
async def ban(inter: discord.Interaction, member: discord.Member, reason: str = "No reason",
              delete_days: app_commands.Range[int, 0, 7] = 0):
    await member.ban(reason=f"{inter.user}: {reason}", delete_message_seconds=delete_days * 86400)
    await inter.response.send_message(f"Banned {member}.", ephemeral=True)


@bot.tree.command(description="Mod: delete the last messages in this channel")
@app_commands.default_permissions(manage_messages=True)
async def purge(inter: discord.Interaction, amount: app_commands.Range[int, 1, 100],
                user: discord.Member = None):
    await inter.response.defer(ephemeral=True)
    deleted = await inter.channel.purge(limit=amount, check=(lambda m: m.author == user) if user else None)
    await log("🧹 Purge", f"{inter.user.mention} deleted {len(deleted)} messages in {inter.channel.mention}", RED)
    await inter.followup.send(f"Deleted {len(deleted)} messages.", ephemeral=True)


# ---------------------------------------------------------------- help


@bot.tree.command(description="Quick answers to common questions")
@app_commands.choices(topic=[app_commands.Choice(name=v[0], value=k) for k, v in FAQ.items()])
async def faq(inter: discord.Interaction, topic: app_commands.Choice[str]):
    q, a = FAQ[topic.value]
    await inter.response.send_message(embed=discord.Embed(title=q, description=a, color=BRAND))


@bot.tree.command(description="Links to the guide, downloads and changelog")
async def docs(inter: discord.Interaction):
    e = discord.Embed(title="Printer Addon", color=BRAND, description=(
        f"📖 Guide: <#{CH['getting_started']}>\n📥 Download: <#{CH['downloads']}>\n"
        f"📢 What's new: <#{CH['changelog']}>\n❓ Help: <#{CH['help']}>"))
    await inter.response.send_message(embed=e, ephemeral=True)


@bot.tree.error
async def on_app_error(inter, error):
    if isinstance(getattr(error, "original", error), discord.NotFound):
        return  # the interaction expired (the bot was slow or offline for a moment)
    msg = "You don't have permission for that." if isinstance(error, app_commands.MissingPermissions) else f"Error: {error}"
    try:
        await inter.response.send_message(msg, ephemeral=True)
    except discord.InteractionResponded:
        await inter.followup.send(msg, ephemeral=True)
    await log("⚠️ Command error", f"/{inter.command.name if inter.command else '?'}: {error}", RED, user=inter.user)


async def _setup_hook():
    await bot.load_extension("player")
    await bot.load_extension("releases")
    await bot.load_extension("maintenance")
    await bot.load_extension("forums")
    await bot.load_extension("library")
    await bot.load_extension("summary")

bot.setup_hook = _setup_hook

if __name__ == "__main__":
    bot.run(load_token())
