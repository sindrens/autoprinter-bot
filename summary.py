"""Weekly summary of the players' logs in #player-reports: how fast builds went, where the time went, why runs ended,
and each version next to the others (did a change help?). Posted in #bot-logs on Mondays, or with /log-summary."""
import collections
import datetime as dt
import gzip
import io
import json
import re

import discord
from discord import app_commands
from discord.ext import commands, tasks

from common import *

TIME_RE = re.compile(r"([a-z\-]+)=([\d.]+)s")
SUPPLY = ("buying", "vault")


def _num(v):
    try:
        return float(str(v).rstrip("s").replace(",", ""))
    except ValueError:
        return 0.0


def parse_log(data, name):
    """RUN-END lines of a run log (.jsonl or .jsonl.gz)."""
    if name.endswith(".gz"):
        data = gzip.decompress(data)
    runs = []
    for line in io.TextIOWrapper(io.BytesIO(data), encoding="utf-8", errors="replace"):
        if '"RUN-END"' not in line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        times = {k: float(v) for k, v in TIME_RE.findall(str(r.get("times", "")))}
        runs.append({"version": str(r.get("version", r.get("v", "?"))), "why": str(r.get("why", "?")),
                     "seconds": _num(r.get("time", 0)), "placed": int(r.get("placed", 0) or 0), "times": times,
                     "trips": int(r.get("trips", 0) or 0), "missed": int(r.get("missedClicks", 0) or 0),
                     "clicks": int(r.get("clicks", 0) or 0)})
    return runs


class Summary(commands.Cog):
    def __init__(self, bot_):
        self.bot = bot_

    async def cog_load(self):
        self.weekly.start()

    async def cog_unload(self):
        self.weekly.cancel()

    @tasks.loop(time=dt.time(hour=7, minute=0))
    async def weekly(self):
        if dt.date.today().weekday() == 0:  # Mondays
            await self.post(7)

    @weekly.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    async def collect(self, days):
        c = ch("player_reports")
        since = discord.utils.utcnow() - dt.timedelta(days=days)
        runs, titles = [], collections.Counter()
        async for msg in c.history(limit=1000, after=since):
            if not (msg.webhook_id or msg.author.id == self.bot.user.id) or not msg.embeds:
                continue
            title = msg.embeds[0].title or ""
            kind = re.sub(r"^(Log|Problem) from \S+\s*[·:]?\s*", "", title).split("·")[0].strip() or title.split(" ")[0]
            titles[("Problem" if title.startswith("Problem") else "Log") + (": " + kind[:40] if kind else "")] += 1
            for a in msg.attachments:
                if re.search(r"\.jsonl(\.gz)?$", a.filename) and a.size < 15_000_000:
                    try:
                        runs += parse_log(await a.read(), a.filename)
                    except Exception as e:
                        print("summary: couldn't read", a.filename, e)
        return runs, titles

    def build(self, runs, titles, days):
        e = discord.Embed(title=f"📊 Logs of the last {days} days", color=BRAND)
        builds = [r for r in runs if r["placed"] > 0]
        if not runs:
            e.description = "No run logs in #player-reports in this time."
            return e
        hours = sum(r["seconds"] for r in runs) / 3600
        placed = sum(r["placed"] for r in builds)
        bsec = sum(r["seconds"] for r in builds) or 1
        e.description = (f"**{len(runs)} runs** ({len(builds)} placing), **{placed:,} blocks** in {hours:.1f} h · "
                         f"**{placed / bsec * 60:,.0f} blocks a minute** on average")
        # where the time went (runs that placed)
        times = collections.Counter()
        for r in builds:
            times.update(r["times"])
        total = sum(times.values()) or 1
        e.add_field(name="Where the time went", inline=False,
                    value="\n".join(f"{k}: {v / total:.0%}" for k, v in times.most_common(6)) or "-")
        ends = collections.Counter(r["why"] for r in runs)
        e.add_field(name="How runs ended", value="\n".join(f"{k}: {v}" for k, v in ends.most_common(5)))
        e.add_field(name="Reports", value="\n".join(f"{k}: {v}" for k, v in titles.most_common(6)) or "-")
        # each version next to the others
        by = collections.defaultdict(list)
        for r in builds:
            by[r["version"]].append(r)
        rows = []
        for v in sorted(by, key=lambda x: [int(n) for n in re.findall(r"\d+", x)])[-5:]:
            rs = by[v]
            sec = sum(r["seconds"] for r in rs) or 1
            t = collections.Counter()
            for r in rs:
                t.update(r["times"])
            tt = sum(t.values()) or 1
            supply = sum(t.get(k, 0) for k in SUPPLY) / tt
            per_k = sum(r["trips"] for r in rs) / max(1, sum(r["placed"] for r in rs)) * 1000
            rows.append(f"**{v}**: {sum(r['placed'] for r in rs) / sec * 60:,.0f}/min · placing {t.get('placing', 0) / tt:.0%} · "
                        f"vault+shop {supply:.0%} · {per_k:.1f} trips per 1000 blocks ({len(rs)} runs)")
        e.add_field(name="By version", value="\n".join(rows) or "-", inline=False)
        e.set_footer(text="From the run logs players sent (RUN-END lines)")
        return e

    async def post(self, days, channel=None):
        runs, titles = await self.collect(days)
        await (channel or ch("bot_logs")).send(embed=self.build(runs, titles, days))

    @app_commands.command(name="log-summary", description="Developer: summary of the players' logs")
    @app_commands.default_permissions(administrator=True)
    async def log_summary(self, inter: discord.Interaction, days: app_commands.Range[int, 1, 60] = 7):
        await inter.response.defer(ephemeral=True)
        runs, titles = await self.collect(days)
        await inter.followup.send(embed=self.build(runs, titles, days), ephemeral=True)


async def setup(bot_):
    await bot_.add_cog(Summary(bot_))
