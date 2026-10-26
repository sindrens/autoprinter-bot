"""Schematic library (#schematics forum) and build time-lapses.
/schematic share posts a .litematic with a 3D preview; files dropped straight in the forum get one too.
The game can list and download them (API in player.py). Needs numpy + Pillow (see requirements.txt)."""
import asyncio
import io
import os
import re
import secrets
import time

import discord
from discord import app_commands
from discord.ext import commands

from common import *

LIB = os.path.join(HERE, "library")
RUNS = os.path.join(LIB, "_runs")
MAX_BYTES = 8 * 1024 * 1024
TAG_NAMES = ["Farm", "Base", "Decor", "Redstone", "Other"]


def schem():
    import schem as s  # numpy + Pillow: only loaded when needed
    return s


def safe_name(name):
    return re.sub(r"[^A-Za-z0-9 _.-]", "", name).strip()[:60] or "schematic"


async def in_thread(fn, *a):
    return await asyncio.get_running_loop().run_in_executor(None, fn, *a)


def _analyse(data):
    s = schem()
    sch = s.load(data)
    img = s.preview(sch)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    import numpy as np
    ids, n = np.unique(sch.blocks[sch.blocks > 0], return_counts=True)
    raw = {sch.palette[i].split("[")[0]: int(c) for i, c in zip(ids, n)}
    return sch.name, sch.size, sch.count, sch.materials(8), buf.getvalue(), raw


# blocks that are bought as another item
ITEM_FOR = {"minecraft:redstone_wire": "minecraft:redstone", "minecraft:tripwire": "minecraft:string",
            "minecraft:farmland": "minecraft:dirt", "minecraft:wall_torch": "minecraft:torch",
            "minecraft:redstone_wall_torch": "minecraft:redstone_torch", "minecraft:water": None, "minecraft:lava": None,
            "minecraft:bubble_column": None, "minecraft:attached_pumpkin_stem": "minecraft:pumpkin_seeds",
            "minecraft:pumpkin_stem": "minecraft:pumpkin_seeds", "minecraft:attached_melon_stem": "minecraft:melon_seeds",
            "minecraft:melon_stem": "minecraft:melon_seeds", "minecraft:wheat": "minecraft:wheat_seeds",
            "minecraft:carrots": "minecraft:carrot", "minecraft:potatoes": "minecraft:potato",
            "minecraft:cocoa": "minecraft:cocoa_beans", "minecraft:kelp_plant": "minecraft:kelp"}


def cost_of(item, server=None):
    """(estimated cost, share of the blocks priced) at the shop prices players sent from a server, or (None, 0)."""
    prices_by = DATA.get("prices", {})
    if not prices_by or not item.get("raw"):
        return None, 0
    if not server or server not in prices_by:
        server = max(prices_by, key=lambda k: prices_by[k].get("at", 0))
    prices = prices_by[server]["prices"]
    total, priced, blocks = 0.0, 0, 0
    for block, count in item["raw"].items():
        it = ITEM_FOR.get(block, block)
        if it is None:
            continue
        blocks += count
        if it in prices:
            total += prices[it] * count
            priced += count
    return (round(total), priced / blocks if blocks else 0) if priced else (None, 0)


def _timelapse(path, title):
    s = schem()
    return s.timelapse(s.load(open(path, "rb").read()), title)


def info_embed(item, author=None):
    x, y, z = item["size"]
    e = discord.Embed(title=f"📐 {item['name']}", color=BRAND, description=item.get("desc") or None)
    e.add_field(name="Size", value=f"{x} × {y} × {z}")
    e.add_field(name="Blocks", value=f"{item['blocks']:,}")
    e.add_field(name="ID", value=f"`{item['id']}`")
    cost, share = cost_of(item)
    if cost is not None:
        e.add_field(name="Cost in the shop", value=f"~${cost:,}" + ("" if share > 0.95 else f" ({share:.0%} of the blocks priced)"))
    if item.get("materials"):
        e.add_field(name="Materials", inline=False,
                    value="\n".join(f"{c:,} × {n}" for n, c in item["materials"][:8]))
    e.set_image(url="attachment://preview.png")
    e.set_footer(text=f"In game: /printeraddon schematic get {item['id']}")
    if author:
        e.set_author(name=author.display_name, icon_url=author.display_avatar.url)
    return e


class ShareView(discord.ui.View):
    """Under a time-lapse in a control room: make it public in #gifs."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Make it public", emoji="🌍", style=discord.ButtonStyle.success, custom_id="tl:share")
    async def share(self, inter, button):
        msg = inter.message
        owner = next((u for u, p in DATA["players"].items() if p.get("channel") == inter.channel_id), None)
        if owner != str(inter.user.id):
            return await inter.response.send_message("Only the owner can share this.", ephemeral=True)
        gif = next((a for a in msg.attachments if a.filename.endswith((".gif", ".mp4"))), None)
        if not gif:
            return await inter.response.send_message("The GIF is gone.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        title = msg.embeds[0].title if msg.embeds else "A build"
        e = discord.Embed(description=f"🎬 **{title.replace('🎬 ', '')}**\nbuilt by {inter.user.mention} with Printer Addon",
                          color=BRAND)
        if gif.filename.endswith(".gif"):
            e.set_image(url=f"attachment://{gif.filename}")
        target = (ch("gifs") if CH.get("gifs") else None) or discord.utils.get(inter.guild.text_channels, name="🎬・gifs") \
            or ch("showcase")
        posted = await target.send(embed=e, file=await gif.to_file(), allowed_mentions=discord.AllowedMentions.none())
        await posted.add_reaction("🔥")
        button.disabled = True
        button.label = "Public"
        await msg.edit(view=self)
        await inter.followup.send(f"It's public now: {posted.jump_url}", ephemeral=True)


class Library(commands.Cog):
    def __init__(self, bot_):
        self.bot = bot_
        os.makedirs(RUNS, exist_ok=True)
        DATA.setdefault("schematics", {})

    async def cog_load(self):
        self.bot.add_view(ShareView())

    # ------------------------------------------------ adding
    async def add(self, data, filename, user, tag=None, desc=None, name=None, thread=None):
        """Saves the file, draws the preview and posts it (or answers in the given forum thread)."""
        if len(data) > MAX_BYTES:
            raise ValueError("The file is bigger than 8 MB.")
        sname, size, count, mats, png, raw = await in_thread(_analyse, data)
        sid = secrets.token_hex(3)
        name = safe_name(name or os.path.splitext(filename)[0] or sname)
        open(os.path.join(LIB, f"{sid}.litematic"), "wb").write(data)
        item = {"id": sid, "name": name, "size": list(size), "blocks": count, "materials": mats, "desc": desc,
                "user": user.id, "tag": tag, "added": int(time.time()), "file": f"{name}.litematic", "raw": raw, "stars": 0}
        files = [discord.File(io.BytesIO(png), "preview.png"),
                 discord.File(io.BytesIO(data), f"{name.replace(' ', '_')}.litematic")]
        forum = ch("schematics")
        if thread is not None:
            m = await thread.send(embed=info_embed(item, user), files=[files[0]])
            item["thread"] = thread.id
            first = await thread.fetch_message(thread.id)
        else:
            tags = [t for t in forum.available_tags if t.name == (tag or "Other")]
            t = await forum.create_thread(name=f"{name}", embed=info_embed(item, user), files=files, applied_tags=tags)
            item["thread"] = t.thread.id
            first = t.message
        try:
            await first.add_reaction("⭐")
        except discord.HTTPException:
            pass
        DATA["schematics"][sid] = item
        save_data()
        return item

    @commands.Cog.listener()
    async def on_thread_create(self, thread):
        """A .litematic posted straight in #schematics gets its preview and an ID."""
        if not CH.get("schematics") or thread.parent_id != int(CH["schematics"]) or thread.owner_id == self.bot.user.id:
            return
        await asyncio.sleep(2)
        try:
            first = await thread.fetch_message(thread.id)
        except discord.HTTPException:
            return
        att = next((a for a in first.attachments if a.filename.endswith(".litematic")), None)
        if not att:
            return await thread.send("Add the **.litematic** file to your post so others can download it "
                                     "(or use **/schematic share**).")
        try:
            tag = next((t.name for t in thread.applied_tags), None)
            await self.add(await att.read(), att.filename, first.author, tag, name=thread.name, thread=thread)
        except Exception as e:
            await thread.send(f"Couldn't read that schematic: {e}")

    # ------------------------------------------------ time-lapse
    def save_run(self, uid, data):
        if len(data) > MAX_BYTES * 2:
            raise ValueError("too big")
        open(os.path.join(RUNS, f"{uid}.litematic"), "wb").write(data)

    async def post_timelapse(self, uid, channel, build):
        path = os.path.join(RUNS, f"{uid}.litematic")
        if not os.path.exists(path) or time.time() - os.path.getmtime(path) > 7 * 86400:
            return
        try:
            data, ext = await in_thread(_timelapse, path, build or "Your build")
        except Exception as e:
            import traceback
            print("time-lapse failed:", e)
            traceback.print_exc()
            return
        e = discord.Embed(title=f"🎬 {build or 'Your build'}", color=GREEN,
                          description="Your build, layer by layer. **Wanna make it public?** Click 🌍 and it goes to #gifs.")
        if ext == "gif":
            e.set_image(url="attachment://timelapse.gif")
        await channel.send(embed=e, file=discord.File(io.BytesIO(data), f"timelapse.{ext}"), view=ShareView())

    # ------------------------------------------------ commands
    group = app_commands.Group(name="schematic", description="The schematic library")

    @group.command(name="share", description="Share a .litematic in #schematics (with a 3D preview)")
    @app_commands.describe(file="Your .litematic file", name="Name (default: the file name)", tag="What kind of build",
                           description="A line about it")
    @app_commands.choices(tag=[app_commands.Choice(name=t, value=t) for t in TAG_NAMES])
    async def share(self, inter: discord.Interaction, file: discord.Attachment, tag: app_commands.Choice[str],
                    name: str = None, description: str = None):
        if not file.filename.endswith(".litematic"):
            return await inter.response.send_message("That's not a .litematic file.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        try:
            item = await self.add(await file.read(), file.filename, inter.user, tag.value, description, name)
        except Exception as e:
            return await inter.followup.send(f"Couldn't read that schematic: {e}", ephemeral=True)
        await inter.followup.send(f"Shared! <#{item['thread']}> · ID `{item['id']}`", ephemeral=True)

    @group.command(name="search", description="Find schematics in the library")
    async def search(self, inter: discord.Interaction, words: str = ""):
        rows = self.find(words)[:15]
        lines = []
        for s in rows:
            cost, _ = cost_of(s)
            lines.append(f"`{s['id']}` **{s['name']}**" + (f" ⭐{s['stars']}" if s.get("stars") else "")
                         + f" · {s['size'][0]}×{s['size'][1]}×{s['size'][2]}" + (f" · ~${cost:,}" if cost else "")
                         + f" · <#{s['thread']}>")
        await inter.response.send_message(embed=discord.Embed(
            title=f"📐 Schematics{' · ' + words if words else ''}", color=BRAND,
            description="\n".join(lines) or "Nothing found. Share one with **/schematic share**."), ephemeral=True)

    # ⭐ under a library post: the best ones come first (in game and in /schematic search)
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, ev):
        await self._star(ev, +1)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, ev):
        await self._star(ev, -1)

    async def _star(self, ev, d):
        if str(ev.emoji) != "⭐" or ev.user_id == self.bot.user.id:
            return
        for s in DATA["schematics"].values():
            if s.get("thread") == ev.message_id:
                s["stars"] = max(0, s.get("stars", 0) + d)
                save_data()
                return

    def find(self, words=""):
        words = (words or "").lower().split()
        rows = [s for s in DATA["schematics"].values()
                if all(w in (s["name"] + " " + (s.get("tag") or "") + " " + (s.get("desc") or "")).lower() for w in words)]
        return sorted(rows, key=lambda s: (s.get("stars", 0), s["added"]), reverse=True)

    def file_path(self, sid):
        p = os.path.join(LIB, f"{re.sub('[^0-9a-f]', '', sid)}.litematic")
        return p if os.path.exists(p) else None


async def setup(bot_):
    await bot_.add_cog(Library(bot_))
