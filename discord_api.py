"""Tiny Discord REST helper. Reads the bot token from .env (never printed)."""
import json, os, sys, time, uuid, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))

def load_token():
    with open(os.path.join(HERE, ".env")) as f:
        for line in f:
            line = line.strip()
            if line.startswith("DISCORD_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("DISCORD_TOKEN missing in .env")

TOKEN = load_token()
BASE = "https://discord.com/api/v10"
GUILD_ID = "1563957401948917850"

class APIError(Exception):
    pass

def _send(req):
    req.add_header("Authorization", "Bot " + TOKEN)
    req.add_header("User-Agent", "DiscordBot (autoprinter-admin, 1.0)")
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req) as r:
                txt = r.read().decode()
                return json.loads(txt) if txt else None
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            if e.code == 429:
                wait = json.loads(body).get("retry_after", 1)
                time.sleep(float(wait) + 0.2)
                continue
            raise APIError(f"HTTP {e.code} {req.get_method()} {req.full_url.replace(BASE,'')}: {body[:300]}")
    raise APIError("rate limited too many times")

def api(method, path, body=None, reason=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if reason:
        req.add_header("X-Audit-Log-Reason", reason)
    return _send(req)

def api_files(method, path, payload, files):
    """files: list of (filename, bytes)."""
    boundary = uuid.uuid4().hex
    parts = []
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="payload_json"\r\nContent-Type: application/json\r\n\r\n'.encode()
                 + json.dumps(payload).encode() + b"\r\n")
    for i, (name, data) in enumerate(files):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="files[{i}]"; filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
                     + data + b"\r\n")
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(BASE + path, data=body, method=method)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    return _send(req)
