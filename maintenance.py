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
    files = glob.glob(os.path.join(HERE, "*.py")) + [os.path.join(HERE, "state.json")]
    return {p: os.path.getmtime(p) for p in files if os.path.exists(p)}


class Maintenance(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.stamp = code_stamp()

    async def cog_load(self):
        self.bot.loop.create_task(self.ensure_requirements())
        self.reload_watch.start()
        self.nightly.start()
        self.github_update.start()

    async def cog_unload(self):
        self.reload_watch.cancel()
        self.nightly.cancel()
        self.github_update.cancel()

    async def ensure_requirements(self):
        """Installs what requirements.txt asks for when something is missing (e.g. numpy for the previews)."""
        import asyncio, importlib.util, sys
        missing = [m for m in ("numpy", "PIL", "imageio", "imageio_ffmpeg") if importlib.util.find_spec(m) is None]
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

    # ------------------------------------------------ updates from GitHub
    REPO_URL = "https://github.com/sindrens/autoprinter-bot"

    async def git(self, *args):
        import asyncio
        p = await asyncio.create_subprocess_exec("git", "-C", HERE, *args, stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.PIPE)
        out, err = await p.communicate()
        return p.returncode, out.decode().strip(), err.decode().strip()

    @tasks.loop(minutes=5)
    async def github_update(self):
        """The bot's code comes from GitHub by itself: every 5 minutes a new commit on main is taken (the code files
        only: data, settings and logs aren't in git). Changed code restarts the bot (see reload_watch)."""
        import shutil
        if shutil.which("git") is None or os.path.exists(os.path.join(HERE, "NO_GITHUB_UPDATES")):
            return
        if not os.path.isdir(os.path.join(HERE, ".git")):
            for args in (("init", "-q"), ("remote", "add", "origin", self.REPO_URL), ("fetch", "-q", "origin", "main"),
                         ("reset", "-q", "origin/main")):  # keeps the files as they are
                code, _, err = await self.git(*args)
                if code != 0:
                    print("github update setup:", err[:200])
                    return
            print("Updates from GitHub set up")
            return
        code, _, err = await self.git("fetch", "-q", "origin", "main")
        if code != 0:
            return
        _, head, _ = await self.git("rev-parse", "HEAD")
        _, remote, _ = await self.git("rev-parse", "origin/main")
        if head == remote:
            return
        _, changed, _ = await self.git("diff", "--name-only", head, remote)
        # Files the bot writes itself are kept as they are (messages.json left git; a reset would take it away)
        keep = {}
        for name in ("messages.json",):
            path = os.path.join(HERE, name)
            if os.path.exists(path):
                keep[path] = open(path, "rb").read()
        code, _, err = await self.git("reset", "-q", "--hard", "origin/main")
        for path, data in keep.items():
            open(path, "wb").write(data)
        if code == 0:
            print("Updated from GitHub:", changed.replace("\n", ", ")[:300])
            try:
                await log("⬇️ Updated from GitHub", changed.replace("\n", ", ")[:1000], GREY)
            except Exception:
                pass

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
