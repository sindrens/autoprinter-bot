"""Forums: #suggestions and #bug-reports are forum channels with tags. Anyone can post there directly or use
/suggest and /bug. Staff set the status (tag) with /suggestion-status and /bug-status. In #help the bot points
to a matching FAQ answer."""
import re
import time

import discord
from discord import app_commands
from discord.ext import commands

from common import *

TAGS = STATE.get("tags", {})
SUG_STATUS = {"Open": (BRAND, "🗳️ Open for votes"), "Planned": (YELLOW, "📌 Planned"),
              "Added": (GREEN, "✅ Added"), "Denied": (RED, "❌ Denied")}
BUG_STATUS = {"Open": (RED, "🐞 Open"), "Looking into it": (YELLOW, "🔍 Looking into it"),
              "Fixed": (GREEN, "✅ Fixed"), "Not a bug": (GREY, "➖ Not a bug")}
FAQ_WORDS = {
    "install": ["install", "mods folder", "fabric", "doesn't load", "crash on start"],
    "start": ["how do i start", "how to start", "start a build", "start building"],
    "shop": ["buy", "shop", "doesn't purchase", "spending"],
    "vaults": ["vault", "/pv", "player vault"],
    "chests": ["chest", "link a chest", "storage"],
    "download": ["download", "where do i get", "newest version"],
}


def forum(key):
    return bot.get_channel(int(CH[key]))


def tag(forum_ch, key, name):
    tid = TAGS.get(key, {}).get(name)
    return forum_ch.get_tag(int(tid)) if tid else None


async def set_status(thread, key, name, color, label, note, who):
    """The status tag on the post, the status field in its first message, and a line in the thread."""
    f = thread.parent
    keep = [t for t in thread.applied_tags if t.name not in (SUG_STATUS if key == "suggestions" else BUG_STATUS)]
    new = tag(f, key, name)
    await thread.edit(applied_tags=keep + ([new] if new else []), archived=False)
    try:
        first = await thread.fetch_message(thread.id)
        if first.author.id == bot.user.id and first.embeds:
            e = first.embeds[0]
            e.color = color
            for i, fl in enumerate(e.fields):
                if fl.name == "Status":
                    e.set_field_at(i, name="Status", value=label + (f"\n{note}" if note else ""), inline=False)
            await first.edit(embed=e)
    except discord.HTTPException:
        pass
    await thread.send(f"**Status:** {label}" + (f"\n> {note}" if note else "") + f"\n-# set by {who}")


def number_of(kind, thread_id):
    for n, s in DATA[kind].items():
        if s["msg"] == thread_id:
            return n
    return None


class SuggestModal(discord.ui.Modal, title="New suggestion"):
    idea = discord.ui.TextInput(label="Your idea, in one line", max_length=90)
    details = discord.ui.TextInput(label="What should it do, and why?", style=discord.TextStyle.paragraph, max_length=1500)

    async def on_submit(self, inter):
        f = forum("suggestions")
        DATA["counter"]["suggestion"] += 1
        n = DATA["counter"]["suggestion"]
        e = discord.Embed(description=self.details.value, color=BRAND)
        e.set_author(name=inter.user.display_name, icon_url=inter.user.display_avatar.url)
        e.add_field(name="Status", value=SUG_STATUS["Open"][1], inline=False)
        t = await f.create_thread(name=f"#{n} {self.idea.value}"[:100], embed=e,
                                  applied_tags=[x for x in [tag(f, "suggestions", "Open")] if x])
        DATA["suggestions"][str(n)] = {"msg": t.thread.id, "user": inter.user.id, "title": self.idea.value, "status": "Open"}
        save_data()
        await t.message.add_reaction("✅")
        await t.message.add_reaction("❌")
        await inter.response.send_message(f"Thanks! Your suggestion is posted: {t.thread.mention}", ephemeral=True)


class BugModal(discord.ui.Modal, title="Report a bug"):
    version = discord.ui.TextInput(label="Printer Addon version (e.g. 0.16.1)", max_length=20)
    what = discord.ui.TextInput(label="What went wrong?", style=discord.TextStyle.paragraph, max_length=1000)
    steps = discord.ui.TextInput(label="What were you doing?", style=discord.TextStyle.paragraph, max_length=800, required=False)
    sent = discord.ui.TextInput(label="Sent a report in game? (yes/no)", max_length=10, required=False)

    async def on_submit(self, inter):
        f = forum("bug_reports")
        DATA["counter"]["bug"] += 1
        n = DATA["counter"]["bug"]
        e = discord.Embed(description=self.what.value, color=RED)
        e.set_author(name=inter.user.display_name, icon_url=inter.user.display_avatar.url)
        e.add_field(name="Version", value=self.version.value)
        e.add_field(name="In-game report", value=self.sent.value or "-")
        if self.steps.value:
            e.add_field(name="What they were doing", value=self.steps.value, inline=False)
        e.add_field(name="Status", value=BUG_STATUS["Open"][1], inline=False)
        t = await f.create_thread(name=f"Bug #{n} · {self.what.value[:60]}"[:100], embed=e,
                                  applied_tags=[x for x in [tag(f, "bug_reports", "Open")] if x])
        await t.thread.send(f"{inter.user.mention} add screenshots or your log here. Thanks for reporting!")
        DATA["bugs"][str(n)] = {"msg": t.thread.id, "user": inter.user.id, "status": "Open"}
        save_data()
        await inter.response.send_message(f"Bug #{n} posted: {t.thread.mention}", ephemeral=True)


class Forums(commands.Cog):
    def __init__(self, bot_):
        self.bot = bot_
        self.helped = {}

    # posts made straight in the forum get a number, the Open tag and (suggestions) the vote reactions
    @commands.Cog.listener()
    async def on_thread_create(self, thread):
        if thread.parent_id not in (int(CH["suggestions"]), int(CH["bug_reports"])) or thread.owner_id == self.bot.user.id:
            return
        key = "suggestions" if thread.parent_id == int(CH["suggestions"]) else "bug_reports"
        kind, counter = ("suggestions", "suggestion") if key == "suggestions" else ("bugs", "bug")
        if number_of(kind, thread.id):
            return
        DATA["counter"][counter] += 1
        n = DATA["counter"][counter]
        DATA[kind][str(n)] = {"msg": thread.id, "user": thread.owner_id, "title": thread.name, "status": "Open"}
        save_data()
        open_tag = tag(thread.parent, key, "Open")
        try:
            await thread.edit(name=(f"#{n} " if key == "suggestions" else f"Bug #{n} · ")[:12] + thread.name[:85],
                              applied_tags=[t for t in thread.applied_tags if t.name not in ("Open",)] + ([open_tag] if open_tag else []))
        except discord.HTTPException:
            pass
        if key == "suggestions":
            try:
                first = await thread.fetch_message(thread.id)
                await first.add_reaction("✅")
                await first.add_reaction("❌")
            except discord.HTTPException:
                pass
        else:
            await thread.send("Thanks! To help find it fast: your **Printer Addon version**, and a report from the game "
                              "(**J + B → Report / share log**) if you can.")

    # #help: point to a matching FAQ answer (once per person per 30 minutes)
    @commands.Cog.listener()
    async def on_message(self, msg):
        if msg.author.bot or msg.channel.id != int(CH["help"]) or is_staff(msg.author):
            return
        if time.time() - self.helped.get(msg.author.id, 0) < 1800:
            return
        text = msg.content.lower()
        hit = next((k for k, words in FAQ_WORDS.items() if any(w in text for w in words)), None)
        if not hit:
            return
        self.helped[msg.author.id] = time.time()
        q, a = FAQ[hit]
        e = discord.Embed(title=f"💡 {q}", description=a, color=BRAND)
        e.set_footer(text="Didn't help? Someone will answer soon. More: /faq")
        await msg.reply(embed=e, mention_author=False)

    # ------------------------------------------------ commands
    @app_commands.command(description="Suggest an idea for Printer Addon")
    async def suggest(self, inter: discord.Interaction):
        await inter.response.send_modal(SuggestModal())

    @app_commands.command(description="Report a bug in Printer Addon")
    async def bug(self, inter: discord.Interaction):
        await inter.response.send_modal(BugModal())

    @app_commands.command(name="suggestion-status", description="Staff: set the status of a suggestion (run it in the post, or give its number)")
    @app_commands.choices(status=[app_commands.Choice(name=k, value=k) for k in SUG_STATUS])
    @app_commands.default_permissions(manage_messages=True)
    async def suggestion_status(self, inter: discord.Interaction, status: app_commands.Choice[str], number: int = None,
                                note: str = None):
        await self._status(inter, "suggestions", status.value, number, note)

    @app_commands.command(name="bug-status", description="Staff: set the status of a bug (run it in the post, or give its number)")
    @app_commands.choices(status=[app_commands.Choice(name=k, value=k) for k in BUG_STATUS])
    @app_commands.default_permissions(manage_messages=True)
    async def bug_status(self, inter: discord.Interaction, status: app_commands.Choice[str], number: int = None,
                         note: str = None):
        await self._status(inter, "bug_reports", status.value, number, note)

    async def _status(self, inter, key, status, number, note):
        kind = "suggestions" if key == "suggestions" else "bugs"
        n = str(number) if number else number_of(kind, inter.channel_id)
        item = DATA[kind].get(n) if n else None
        if not item:
            return await inter.response.send_message("Run it inside the post, or give its number.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        thread = self.bot.get_channel(item["msg"]) or await self.bot.fetch_channel(item["msg"])
        color, label = (SUG_STATUS if kind == "suggestions" else BUG_STATUS)[status]
        await set_status(thread, key, status, color, label, note, inter.user.display_name)
        item["status"] = status
        save_data()
        what = f"suggestion #{n} {item.get('title', '')}" if kind == "suggestions" else f"bug report #{n}"
        await notify_player(str(item["user"]), f"Your {what} is now {status}." + (f" {note}" if note else ""),
                            about=f"{'Suggestion' if kind == 'suggestions' else 'Bug'} #{n}")
        if status in ("Fixed", "Not a bug", "Added", "Denied"):
            await thread.edit(archived=True, locked=status != "Added")
        await inter.followup.send(f"#{n} is now {status}.", ephemeral=True)


async def setup(bot_):
    await bot_.add_cog(Forums(bot_))
