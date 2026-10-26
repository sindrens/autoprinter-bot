"""Automatic releases: when the mod repo has a new version (gradle.properties), its jar is built and CHANGELOG.md has
a section for it, the bot posts it: release card + changelog, the jar in #downloads (the old one moves to
#old-versions), a short note in #general and a ping for the Updates role. Writing the changelog section is the trigger."""
import asyncio
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from common import *

MSGS_FILE = os.path.join(HERE, "messages.json")


def env(key, default=None):
    try:
        for line in open(os.path.join(HERE, ".env")):
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return default


REPO = os.path.abspath(env("MOD_REPO", os.path.join(HERE, "..", "litematica-printer-addon")))


def vkey(v):
    return [int(x) for x in re.findall(r"\d+", v or "0")]


def read_props():
    props = {}
    text = open(os.path.join(REPO, "gradle.properties")).read()
    for line in text.splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            props[k.strip()] = v.strip()
    m = re.search(r"Litematica Printer ([\d.]+)", text)
    props["printer_version"] = m.group(1) if m else "3.2.2"
    return props


def changelog_lines(mc, version):
    try:
        text = open(os.path.join(REPO, "CHANGELOG.md")).read()
    except FileNotFoundError:
        return None
    m = re.search(rf"^## {re.escape(mc)}-{re.escape(version)}\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        return None
    lines = [l.strip()[2:].strip() for l in m.group(1).splitlines() if l.strip().startswith("- ")]
    return lines or None


ICONS = [("fix", "check"), ("faster", "zap"), ("speed", "zap"), ("smooth", "zap"), ("log", "file-text"),
         ("report", "file-text"), ("discord", "messages-square"), ("buy", "shopping-cart"), ("shop", "shopping-cart"),
         ("break", "pickaxe"), ("fly", "plane"), ("layer", "layers"), ("setting", "settings-2"), ("safe", "shield-check"),
         ("new", "sparkles"), ("link", "send")]


def icon_for(line):
    low = line.lower()
    for word, icon in ICONS:
        if word in low:
            return icon
    return "check"


def pillow_card(version, date, lines):
    """A release card drawn here (no browser needed): the logo's gradient, the version big, the changes listed."""
    import numpy as np
    from PIL import Image, ImageDraw
    from schem import _font
    W, H = 1600, 960
    xs = np.linspace(0, 1, W)[None, :, None]
    ys = np.linspace(0, 1, H)[:, None, None]
    t = np.clip(xs * 0.7 + ys * 0.3, 0, 1)
    c1, c2, c3 = np.array([34, 211, 238.0]), np.array([14, 165, 233.0]), np.array([79, 70, 229.0])
    rgb = np.where(t < 0.42, c1 + (c2 - c1) * (t / 0.42), c2 + (c3 - c2) * ((t - 0.42) / 0.58))
    img = Image.fromarray(rgb.astype(np.uint8))
    d = ImageDraw.Draw(img)
    for x in range(0, W, 64):
        d.line([(x, 0), (x, H)], fill=(255, 255, 255, 18), width=2)
    for y in range(0, H, 64):
        d.line([(0, y), (W, y)], fill=(255, 255, 255, 18), width=2)
    img = Image.blend(Image.fromarray(rgb.astype(np.uint8)), img, 0.12)
    d = ImageDraw.Draw(img)
    try:
        logo = Image.open(os.path.join(HERE, "assets", "server_icon.png")).convert("RGBA").resize((56, 56))
        img.paste(logo, (W - 330, 44), logo)
    except OSError:
        pass
    d.text((W - 262, 72), "Printer Addon", font=_font(30), fill="white", anchor="lm")
    d.rounded_rectangle((100, 80, 330, 136), radius=28, outline=(255, 255, 255), width=2)
    d.text((215, 108), "New version", font=_font(28), fill="white", anchor="mm")
    d.text((96, 170), f"v{version}", font=_font(150), fill="white")
    d.text((100, 352), f"Printer Addon · {date}", font=_font(34), fill=(240, 249, 255))
    y = 450
    for line in lines[:6]:
        d.ellipse((100, y, 152, y + 52), fill="white")
        d.text((126, y + 26), "✓", font=_font(30), fill=(67, 56, 202), anchor="mm")
        text = line if len(line) <= 58 else line[:56].rsplit(" ", 1)[0] + "…"
        d.text((180, y + 26), text, font=_font(34), fill="white", anchor="lm")
        y += 76
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


async def render_card(version, date, lines):
    """The release card in the logo's style (branding/discord_images.py + render-pages.js). Drawn here when that
    can't run (on the droplet)."""
    card = await _render_branding_card(version, date, lines)
    if card is None:
        try:
            card = await asyncio.get_running_loop().run_in_executor(None, pillow_card, version, date, lines)
        except Exception as e:
            print("card failed:", e)
    return card


async def _render_branding_card(version, date, lines):
    branding = os.path.join(REPO, "branding")
    ready = os.path.join(branding, "discord", f"release-{version}.png")
    if os.path.exists(ready):  # made by hand already (branding/discord_images.py)
        return open(ready, "rb").read()
    script = (
        "import sys, os; sys.path.insert(0, sys.argv[1]); import discord_images as d\n"
        "d.PAGES.clear(); import json\n"
        "items = json.loads(sys.argv[4])\n"
        "d.release(sys.argv[2], sys.argv[3], [tuple(i) for i in items])\n"
        "open(os.path.join(d.OUT, 'pages.txt'), 'w').write('\\n'.join(f'{n} {w} {h}' for n, w, h in d.PAGES))\n")
    items = json.dumps([[icon_for(l), l[:60]] for l in lines[:6]])
    try:
        p = await asyncio.create_subprocess_exec(sys.executable, "-c", script, branding, version, date, items,
                                                 stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, err = await asyncio.wait_for(p.communicate(), 60)
        if p.returncode != 0:
            print("release card:", err.decode()[-300:])
            return None
        p = await asyncio.create_subprocess_exec("node", "render-pages.js", "discord", cwd=branding,
                                                 stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, err = await asyncio.wait_for(p.communicate(), 120)
        png = os.path.join(branding, "discord", f"release-{version}.png")
        if p.returncode == 0 and os.path.exists(png):
            return open(png, "rb").read()
        print("release card:", err.decode()[-300:])
    except (FileNotFoundError, asyncio.TimeoutError) as e:
        print("release card skipped:", e)
    return None


class UpdatesButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Ping me about updates", emoji="🔔", style=discord.ButtonStyle.secondary, custom_id="role:updates")
    async def toggle(self, inter, _):
        role = inter.guild.get_role(int(ROLE["updates"]))
        if role in inter.user.roles:
            await inter.user.remove_roles(role, reason="Update pings off")
            await inter.response.send_message("🔕 You won't be pinged about updates any more.", ephemeral=True)
        else:
            await inter.user.add_roles(role, reason="Update pings on")
            await inter.response.send_message("🔔 You'll be pinged when a new version is out.", ephemeral=True)


class Releases(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.busy = False

    async def cog_load(self):
        self.bot.add_view(UpdatesButton())
        self.watch.start()

    async def cog_unload(self):
        self.watch.cancel()

    # ------------------------------------------------ the watcher
    @tasks.loop(minutes=2)
    async def watch(self):
        try:
            await self.check(announce_errors=False)
        except Exception as e:  # no internet for a moment: try again next round (finished steps are skipped)
            print("release check failed:", repr(e)[:200])

    @watch.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    async def check(self, announce_errors=True):
        """Returns a short text of what happened."""
        if self.busy:
            return "A release is already being posted."
        try:
            props = read_props()
        except FileNotFoundError:
            return "Releases come from the developer's Mac (sync.py)."
        version, mc = props.get("mod_version", ""), props.get("minecraft_version", "")
        current = DATA.get("release", {}).get("version", "0")
        if vkey(version) <= vkey(current):
            return f"Nothing new: {version} is already out."
        lines = changelog_lines(mc, version)
        if not lines:
            return f"{version} has no section in CHANGELOG.md yet (## {mc}-{version}): not released."
        jar = os.path.join(REPO, "build", "libs", f"{props.get('mod_id', 'printer_addon')}-{mc}-{version}.jar")
        if not os.path.exists(jar):
            return f"{version} isn't built yet ({os.path.basename(jar)} missing): run ./gradlew build."
        if time.time() - os.path.getmtime(jar) < 60:
            return "The jar was just built: waiting a minute so it's complete."
        self.busy = True
        try:
            await self.publish(version, open(jar, "rb").read(), os.path.basename(jar), lines, props)
            return f"Released {version}."
        finally:
            self.busy = False

    # ------------------------------------------------ posting
    async def publish(self, version, data, filename, lines, props=None, image=None, ping=True):
        props = props or {}
        date = dt.datetime.now(dt.timezone(dt.timedelta(hours=1))).strftime("%B %-d, %Y")  # Norway
        sha = hashlib.sha256(data).hexdigest()
        guild = self.bot.get_guild(GUILD.id)
        if image is None:
            image = await render_card(version, date, lines)
        notes = "\n".join(f"• {l}" for l in lines)
        done = DATA.setdefault("release_steps", {}).setdefault(version, [])

        def step_done(name):
            done.append(name)
            save_data()

        # 1. changelog: card + notes + ping
        cl = ch("changelog")
        cl_msg = None
        if "changelog" in done:
            async for old in cl.history(limit=10):
                if old.embeds and old.embeds[0].title == f"Printer Addon {version}":
                    cl_msg = old
                    break
        if image and "card" not in done:
            await cl.send(file=discord.File(io.BytesIO(image), filename=f"release-{version}.png"))
            step_done("card")
        e = discord.Embed(title=f"Printer Addon {version}", color=BRAND,
                          description=f"*{date}*\n\n{notes}\n\n📥 Download: {ch('downloads').mention}")
        updates = guild.get_role(int(ROLE["updates"])) if ROLE.get("updates") else None
        if cl_msg is None:
            cl_msg = await cl.send(content=updates.mention if (ping and updates) else None, embed=e, view=UpdatesButton(),
                                   allowed_mentions=discord.AllowedMentions(roles=True))
            step_done("changelog")
            try:
                await cl_msg.create_thread(name=f"Talk about {version}")
            except discord.HTTPException:
                pass

        # 2. downloads: the old jar moves to #old-versions, the new one goes in
        posted = json.load(open(MSGS_FILE))
        dl_ids = posted.get("downloads", [])
        if len(dl_ids) > 2 and "archived" not in done:
            try:
                old = await ch("downloads").fetch_message(dl_ids[-1])
                old_ver = old.embeds[0].title.replace("Printer Addon ", "") if old.embeds else "?"
                if old.attachments and ch("old_versions"):
                    a = old.attachments[0]
                    oe = discord.Embed(title=f"Printer Addon {old_ver}", color=GREY,
                                       description=f"An older version. The newest is in {ch('downloads').mention}.")
                    for f in (old.embeds[0].fields if old.embeds else []):
                        if f.name in ("Released", "SHA-256"):
                            oe.add_field(name=f.name, value=f.value, inline=f.name != "SHA-256")
                    await ch("old_versions").send(embed=oe, file=await a.to_file())
                await old.delete()
            except discord.NotFound:
                pass
            dl_ids = dl_ids[:2]
            posted["downloads"] = dl_ids
            json.dump(posted, open(MSGS_FILE, "w"), indent=1)
            step_done("archived")
        mcv = props.get("minecraft_version", "26.2")
        dl = discord.Embed(title=f"Printer Addon {version}", color=BRAND,
                           description=f"The newest version. What's new: {ch('changelog').mention}")
        dl.add_field(name="Minecraft", value=f"{mcv} · Fabric")
        dl.add_field(name="Size", value=f"{len(data) / 1e6:.1f} MB")
        dl.add_field(name="Released", value=date)
        dl.add_field(name="Needs", inline=False, value=(
            f"Fabric API, MaLiLib {props.get('malilib_version', '0.29.6')}, Litematica {props.get('litematica_version', '0.28.8')}, "
            f"Litematica Printer {props.get('printer_version', '3.2.2')}+"))
        dl.add_field(name="SHA-256", value=f"`{sha}`", inline=False)
        if "downloads" not in done:
            m = await ch("downloads").send(embed=dl, file=discord.File(io.BytesIO(data), filename=filename))
            posted["downloads"] = dl_ids + [m.id]
            json.dump(posted, open(MSGS_FILE, "w"), indent=1)
            step_done("downloads")
        else:
            m = await ch("downloads").fetch_message(posted["downloads"][-1])

        # 3. a short note in #general
        g = ch("general")
        if g and "general" not in done:
            step_done("general")
            await g.send(embed=discord.Embed(color=BRAND, description=(
                f"📢 **Printer Addon {version} is out!**\n{lines[0]}" + (f" (+{len(lines) - 1} more)" if len(lines) > 1 else "")
                + f"\nWhat's new: {cl_msg.jump_url} · Download: {ch('downloads').mention}")))

        # 4. remembered: linked games show "update available", the bot's status shows it
        DATA["release"] = {"version": version, "date": date, "notes": "\n".join(lines)[:1500], "url": m.jump_url, "sha256": sha}
        save_data()
        await log("🚀 Released", f"Printer Addon {version} · {len(lines)} changes", GREEN)

    # ------------------------------------------------ commands
    @app_commands.command(name="release-check", description="Developer: check the mod repo for a new version now")
    @app_commands.default_permissions(administrator=True)
    async def release_check(self, inter: discord.Interaction):
        await inter.response.defer(ephemeral=True)
        await inter.followup.send(await self.check(), ephemeral=True)


async def setup(bot):
    await bot.add_cog(Releases(bot))
