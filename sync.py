"""Runs on the developer's Mac (launchd, see move-to-droplet.command). Every minute:
1. bot code changed here -> copied to the droplet (the bot there restarts by itself with it)
2. a new mod version is ready (gradle.properties + CHANGELOG section + built jar) -> sent to the bot to release.
Only the Python standard library: it runs with the Mac's own python3."""
import base64
import glob
import json
import os
import re
import subprocess
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "litematica-printer-addon"))
CODE = ["*.py", "requirements.txt", "*.md", "*.command"]
STAMP_FILE = os.path.join(HERE, "logs", "sync-stamp.json")


def env(key, default=""):
    for line in open(os.path.join(HERE, ".env")):
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip()
    return default


DROPLET = env("DROPLET")          # root@1.2.3.4
API = env("DROPLET_API")          # http://1.2.3.4:8787/api/v1
KEY = env("ADMIN_KEY")


def code_files():
    out = []
    for pattern in CODE:
        out += glob.glob(os.path.join(HERE, pattern))
    return sorted(p for p in out if not os.path.basename(p).startswith("test_"))


def sync_code(last):
    files = code_files()
    stamp = {os.path.basename(p): os.path.getmtime(p) for p in files}
    if stamp == last:
        return last
    changed = [os.path.basename(p) for p in files if last.get(os.path.basename(p)) != stamp[os.path.basename(p)]]
    r = subprocess.run(["rsync", "-az", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=15"] + files + [f"{DROPLET}:/opt/autoprinter/"],
                       capture_output=True, text=True)
    if r.returncode == 0:
        print(time.strftime("%H:%M:%S"), "copied to the droplet:", ", ".join(changed[:8]), flush=True)
        return stamp
    print("copy failed:", r.stderr.strip()[-200:], flush=True)
    return last


def props():
    out = {}
    text = open(os.path.join(REPO, "gradle.properties")).read()
    for line in text.splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    m = re.search(r"Litematica Printer ([\d.]+)", text)
    out["printer_version"] = m.group(1) if m else "3.2.2"
    return out


def changelog(mc, version):
    text = open(os.path.join(REPO, "CHANGELOG.md")).read()
    m = re.search(rf"^## {re.escape(mc)}-{re.escape(version)}\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    return [l.strip()[2:].strip() for l in m.group(1).splitlines() if l.strip().startswith("- ")] if m else []


def latest_out():
    with urllib.request.urlopen(API + "/version", timeout=15) as r:
        return json.loads(r.read()).get("latest") or "0"


def vkey(v):
    return [int(x) for x in re.findall(r"\d+", v or "0")]


def check_release(sent):
    p = props()
    version, mc = p.get("mod_version", ""), p.get("minecraft_version", "")
    if not version or version in sent:
        return sent
    lines = changelog(mc, version)
    jar = os.path.join(REPO, "build", "libs", f"{p.get('mod_id', 'printer_addon')}-{mc}-{version}.jar")
    if not lines or not os.path.exists(jar) or time.time() - os.path.getmtime(jar) < 60:
        return sent
    if vkey(version) <= vkey(latest_out()):
        return sent | {version}
    card = os.path.join(REPO, "branding", "discord", f"release-{version}.png")
    body = {"version": version, "lines": lines, "filename": os.path.basename(jar), "props": p,
            "jar": base64.b64encode(open(jar, "rb").read()).decode(),
            "card": base64.b64encode(open(card, "rb").read()).decode() if os.path.exists(card) else None}
    req = urllib.request.Request(API + "/admin/release", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Admin-Key": KEY})
    with urllib.request.urlopen(req, timeout=180) as r:
        print(time.strftime("%H:%M:%S"), "release", version, "->", r.read().decode()[:200], flush=True)
    return sent | {version}


def main():
    try:
        last = json.load(open(STAMP_FILE))
    except (OSError, ValueError):
        last = {}
    sent = set()
    while True:
        try:
            new = sync_code(last)
            if new != last:
                last = new
                json.dump(last, open(STAMP_FILE, "w"))
        except Exception as e:
            print("sync:", e, flush=True)
        try:
            sent = check_release(sent)
        except Exception as e:
            print("release check:", e, flush=True)
        time.sleep(60)


if __name__ == "__main__":
    main()
