import json
from discord_api import api
me = api("GET", "/users/@me")
print("Bot:", me["username"])
guilds = api("GET", "/users/@me/guilds")
out = {}
for g in guilds:
    gid = g["id"]
    roles = api("GET", f"/guilds/{gid}/roles")
    chans = api("GET", f"/guilds/{gid}/channels")
    info = api("GET", f"/guilds/{gid}?with_counts=true")
    out[gid] = {"name": g["name"], "members": info.get("approximate_member_count"), "roles": roles, "channels": chans}
    print(f"\n== {g['name']} ({gid}) members~{info.get('approximate_member_count')} roles={len(roles)} channels={len(chans)}")
json.dump(out, open("snapshot.json", "w"), indent=1)
