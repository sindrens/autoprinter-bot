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
import sys
import time
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "litematica-printer-addon"))
CODE = ["*.py", "requirements.txt", "*.md", "*.command", "*.sh", "state.json"]  # state.json: channel and role ids (set up from the Mac)
STAMP_FILE = os.path.join(HERE, "logs", "sync-stamp.json")
GITHUB = "https://github.com/sindrens/litematica-printer-addon"
BUILD_DIR = os.path.join(HERE, "release-build")     # a clean clone, only for building releases
OUTBOX = os.path.join(HERE, "release-outbox")       # <version>/release.json + jar (+ card): posted as they are
JDK_DIR = os.path.join(HERE, ".jdk")
GITHUB_EVERY = 300


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


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def post_release(version, lines, jar_path, props, card_path=None):
    with zipfile.ZipFile(jar_path) as z:
        names = set(z.namelist())
    missing = [n for n in ("printer_addon/log-channel.txt", "printer_addon/report-index.txt") if n not in names]
    if missing:
        raise RuntimeError(f"{os.path.basename(jar_path)} lacks {missing}: players' logs would go nowhere")
    body = {"version": version, "lines": lines, "filename": os.path.basename(jar_path), "props": props,
            "jar": base64.b64encode(open(jar_path, "rb").read()).decode(),
            "card": base64.b64encode(open(card_path, "rb").read()).decode() if card_path and os.path.exists(card_path) else None}
    req = urllib.request.Request(API + "/admin/release", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Admin-Key": KEY})
    with urllib.request.urlopen(req, timeout=300) as r:
        log("release", version, "->", r.read().decode()[:200])


def check_outbox():
    """Releases put in release-outbox/<version>/ (release.json with version, lines, filename, props; the jar; a card)."""
    if not os.path.isdir(OUTBOX):
        return
    for d in sorted(os.listdir(OUTBOX)):
        folder = os.path.join(OUTBOX, d)
        meta = os.path.join(folder, "release.json")
        if not os.path.isfile(meta):
            continue
        m = json.load(open(meta))
        if vkey(m["version"]) > vkey(latest_out()):
            cards = glob.glob(os.path.join(folder, "*.png"))
            post_release(m["version"], m["lines"], os.path.join(folder, m["filename"]), m.get("props") or {},
                         cards[0] if cards else None)
        done = os.path.join(HERE, "backups", "released")
        os.makedirs(done, exist_ok=True)
        os.replace(folder, os.path.join(done, d))


def find_jdk():
    """JDK 25 for the build: an installed one, else a copy of Eclipse Temurin 25 in .jdk (downloaded once)."""
    try:
        home = subprocess.run(["/usr/libexec/java_home", "-v", "25"], capture_output=True, text=True).stdout.strip()
        if home and os.path.exists(os.path.join(home, "bin", "java")):
            return home
    except OSError:
        pass
    found = glob.glob(os.path.join(JDK_DIR, "*", "Contents", "Home")) + glob.glob(os.path.join(JDK_DIR, "*", "bin", "..")) 
    for home in found:
        if os.path.exists(os.path.join(home, "bin", "java")):
            return os.path.abspath(home)
    arch = "aarch64" if os.uname().machine in ("arm64", "aarch64") else "x64"
    osname = "mac" if sys.platform == "darwin" else "linux"
    url = f"https://api.adoptium.net/v3/binary/latest/25/ga/{osname}/{arch}/jdk/hotspot/normal/eclipse"
    log("downloading JDK 25 for the builds...")
    os.makedirs(JDK_DIR, exist_ok=True)
    tgz = os.path.join(JDK_DIR, "jdk.tar.gz")
    urllib.request.urlretrieve(url, tgz)
    subprocess.run(["tar", "xzf", tgz, "-C", JDK_DIR], check=True)
    os.remove(tgz)
    return find_jdk()


def git(*args):
    return subprocess.run(["git", "-C", BUILD_DIR] + list(args), capture_output=True, text=True, check=True).stdout


def check_github(failed):
    """A new version on GitHub (gradle.properties + its CHANGELOG section): built in a clean clone and released."""
    if not os.path.isdir(os.path.join(BUILD_DIR, ".git")):
        subprocess.run(["git", "clone", "-q", GITHUB, BUILD_DIR], check=True)
    git("fetch", "-q", "origin", "main")
    git("reset", "-q", "--hard", "origin/main")
    git("clean", "-fdq", "-e", ".gradle", "-e", "build")
    text = open(os.path.join(BUILD_DIR, "gradle.properties")).read()
    p = {}
    for line in text.splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            p[k.strip()] = v.strip()
    m = re.search(r"Litematica Printer ([\d.]+)", text)
    p["printer_version"] = m.group(1) if m else "3.2.2"
    version, mc = p.get("mod_version", ""), p.get("minecraft_version", "")
    cl = open(os.path.join(BUILD_DIR, "CHANGELOG.md")).read()
    m = re.search(rf"^## {re.escape(mc)}-{re.escape(version)}\s*$(.*?)(?=^## |\Z)", cl, re.M | re.S)
    lines = [l.strip()[2:].strip() for l in m.group(1).splitlines() if l.strip().startswith("- ")] if m else []
    if not version or not lines or vkey(version) <= vkey(latest_out()):
        return failed
    if failed.get(version, 0) > time.time() - 1800:
        return failed
    log("new version on GitHub:", version, "- building")
    try:
        channel = os.path.join(REPO, "src", "main", "resources", "printer_addon", "log-channel.txt")
        dest = os.path.join(BUILD_DIR, "src", "main", "resources", "printer_addon", "log-channel.txt")
        open(dest, "wb").write(open(channel, "rb").read())
        env_ = dict(os.environ, JAVA_HOME=find_jdk())
        r = subprocess.run(["./gradlew", "build", "-x", "test", "--no-daemon", "-q"], cwd=BUILD_DIR, env=env_,
                           capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            raise RuntimeError("build failed: " + (r.stderr or r.stdout)[-600:])
        jar = os.path.join(BUILD_DIR, "build", "libs", f"{p.get('mod_id', 'printer_addon')}-{mc}-{version}.jar")
        card = os.path.join(BUILD_DIR, "branding", "discord", f"release-{version}.png")
        post_release(version, lines, jar, p, card)
        failed.pop(version, None)
    except Exception as e:
        log("release of", version, "failed:", str(e)[-500:])
        failed[version] = time.time()
    return failed


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
    failed = {}
    last_github = 0
    me = os.path.getmtime(__file__)
    while True:
        if os.path.getmtime(__file__) != me:  # sync.py itself was updated: start again with the new code
            os.execv(sys.executable, [sys.executable, "-u", __file__])
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
        try:
            check_outbox()
        except Exception as e:
            log("outbox:", e)
        if time.time() - last_github > GITHUB_EVERY:
            last_github = time.time()
            try:
                failed = check_github(failed)
            except Exception as e:
                log("github check:", e)
        time.sleep(60)


if __name__ == "__main__":
    main()
