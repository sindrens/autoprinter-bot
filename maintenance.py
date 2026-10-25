"""Housekeeping: restart by itself when the bot's code changes (launchd starts it again),
and a nightly copy of data.json (the last 14 are kept)."""
import datetime as dt
import glob
import os
import shutil

from discord.ext import commands, tasks

from common import *

BACKUP_DIR = os.path.join(HERE, "backups", "data")


def code_stamp():
    return {p: os.path.getmtime(p) for p in glob.glob(os.path.join(HERE, "*.py"))}


class Maintenance(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.stamp = code_stamp()

    async def cog_load(self):
        self.bot.loop.create_task(self.ensure_requirements())
        self.reload_watch.start()
        self.nightly.start()

    async def cog_unload(self):
        self.reload_watch.cancel()
        self.nightly.cancel()

    async def ensure_requirements(self):
        """Installs what requirements.txt asks for when something is missing (e.g. numpy for the previews)."""
        import asyncio, importlib.util, sys
        missing = [m for m in ("numpy", "PIL") if importlib.util.find_spec(m) is None]
        if not missing:
            return
        print("Installing", missing)
        p = await asyncio.create_subprocess_exec(sys.executable, "-m", "pip", "install", "-q", "-r",
                                                 os.path.join(HERE, "requirements.txt"))
        await p.wait()
        print("pip finished:", p.returncode)

    @tasks.loop(seconds=20)
    async def reload_watch(self):
        now = code_stamp()
        if now != self.stamp:
            changed = [os.path.basename(p) for p in now if now.get(p) != self.stamp.get(p)]
            print("Code changed, restarting:", changed)
            try:
                await log("♻️ Restarting", "Code updated: " + ", ".join(changed), GREY)
            except Exception:
                pass
            save_data()
            os._exit(0)  # launchd (KeepAlive) starts the new code

    @tasks.loop(time=dt.time(hour=3))
    async def nightly(self):
        self.backup()

    def backup(self):
        os.makedirs(BACKUP_DIR, exist_ok=True)
        save_data()
        shutil.copy2(DATA_FILE, os.path.join(BACKUP_DIR, f"data-{dt.date.today().isoformat()}.json"))
        for old in sorted(glob.glob(os.path.join(BACKUP_DIR, "data-*.json")))[:-14]:
            os.remove(old)


async def setup(bot):
    await bot.add_cog(Maintenance(bot))
