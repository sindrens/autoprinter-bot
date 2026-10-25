"""Litematica schematics: read (.litematic), and draw an isometric 3D picture or a layer-by-layer GIF.
Needs numpy and Pillow. No Minecraft textures: each block gets a colour from its name."""
import gzip
import hashlib
import io
import struct

import numpy as np
from PIL import Image, ImageDraw

# ------------------------------------------------------------------ NBT (just what .litematic needs)
def _read_nbt(data):
    pos = 0

    def take(n):
        nonlocal pos
        b = data[pos:pos + n]
        pos += n
        return b

    def string():
        (n,) = struct.unpack(">H", take(2))
        return take(n).decode("utf-8", "replace")

    def payload(t):
        if t == 1: return struct.unpack(">b", take(1))[0]
        if t == 2: return struct.unpack(">h", take(2))[0]
        if t == 3: return struct.unpack(">i", take(4))[0]
        if t == 4: return struct.unpack(">q", take(8))[0]
        if t == 5: return struct.unpack(">f", take(4))[0]
        if t == 6: return struct.unpack(">d", take(8))[0]
        if t == 7:
            (n,) = struct.unpack(">i", take(4)); return take(n)
        if t == 8: return string()
        if t == 9:
            et = take(1)[0]; (n,) = struct.unpack(">i", take(4))
            return [payload(et) for _ in range(n)]
        if t == 10:
            out = {}
            while True:
                tt = take(1)[0]
                if tt == 0: return out
                name = string()
                out[name] = payload(tt)
        if t == 11:
            (n,) = struct.unpack(">i", take(4)); return np.frombuffer(take(4 * n), dtype=">i4")
        if t == 12:
            (n,) = struct.unpack(">i", take(4)); return np.frombuffer(take(8 * n), dtype=">i8")
        raise ValueError(f"bad NBT tag {t}")

    t = take(1)[0]
    string()
    return payload(t)


def _unpack(longs, bits, count):
    """Litematica packs indices tightly across longs (an entry may span two)."""
    words = longs.astype(np.uint64)
    idx = np.arange(count, dtype=np.uint64) * np.uint64(bits)
    w = (idx >> np.uint64(6)).astype(np.int64)
    off = idx & np.uint64(63)
    lo = words[w] >> off
    span = (off + np.uint64(bits)) > np.uint64(64)
    nxt = np.where(span, words[np.minimum(w + 1, len(words) - 1)], np.uint64(0))
    sh = np.where(span, np.uint64(64) - off, np.uint64(0))
    hi = np.where(span, nxt << sh, np.uint64(0))
    return ((lo | hi) & np.uint64((1 << bits) - 1)).astype(np.int32)


class Schematic:
    def __init__(self, name, blocks, palette, meta):
        self.name, self.blocks, self.palette, self.meta = name, blocks, palette, meta  # blocks[y, z, x] -> palette index (0 = air)

    @property
    def size(self):
        y, z, x = self.blocks.shape
        return x, y, z

    @property
    def count(self):
        return int((self.blocks > 0).sum())

    def materials(self, top=12):
        ids, n = np.unique(self.blocks[self.blocks > 0], return_counts=True)
        rows = sorted(zip(n.tolist(), [self.palette[i] for i in ids]), reverse=True)
        return [(name.replace("minecraft:", "").replace("_", " "), c) for c, name in rows[:top]]


AIR = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air", "minecraft:structure_void"}


def load(data):
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    root = _read_nbt(data)
    meta = root.get("Metadata", {})
    regions = root.get("Regions", {})
    if not regions:
        raise ValueError("no regions in this schematic")
    boxes = []
    for r in regions.values():
        p, s = r["Position"], r["Size"]
        mn = [p[k] + (s[k] + 1 if s[k] < 0 else 0) for k in "xyz"]
        boxes.append((r, mn, [abs(s[k]) for k in "xyz"]))
    lo = [min(b[1][i] for b in boxes) for i in range(3)]
    hi = [max(b[1][i] + b[2][i] for b in boxes) for i in range(3)]
    sx, sy, sz = (hi[i] - lo[i] for i in range(3))
    if sx * sy * sz > 40_000_000:
        raise ValueError("schematic too big to draw")
    names = ["minecraft:air"]
    index = {"minecraft:air": 0}
    out = np.zeros((sy, sz, sx), dtype=np.int32)
    for r, mn, (rx, ry, rz) in boxes:
        pal = [e["Name"] for e in r["BlockStatePalette"]]
        bits = max(2, (len(pal) - 1).bit_length())
        n = rx * ry * rz
        idx = _unpack(np.asarray(r["BlockStates"]), bits, n).reshape(ry, rz, rx)
        remap = np.array([0 if nm in AIR else index.setdefault(nm, len(index)) for nm in pal], dtype=np.int32)
        names = list(index)
        sub = remap[np.clip(idx, 0, len(pal) - 1)]
        ox, oy, oz = (mn[0] - lo[0], mn[1] - lo[1], mn[2] - lo[2])
        view = out[oy:oy + ry, oz:oz + rz, ox:ox + rx]
        np.copyto(view, sub, where=sub > 0)
    names = [None] * len(index)
    for k, v in index.items():
        names[v] = k
    return Schematic(meta.get("Name") or "schematic", out, names, meta)


# ------------------------------------------------------------------ colours
COLORS = [
    ("water", (63, 118, 228)), ("lava", (234, 108, 24)), ("ice", (160, 190, 245)), ("snow", (240, 248, 250)),
    ("glass_pane", (190, 220, 235)), ("glass", (200, 230, 240)),
    ("cactus", (88, 140, 52)), ("leaves", (64, 120, 40)), ("grass_block", (100, 160, 70)), ("moss", (90, 130, 45)),
    ("vine", (60, 110, 40)), ("kelp", (70, 120, 50)), ("bamboo", (120, 160, 60)), ("sugar_cane", (130, 180, 90)),
    ("melon", (110, 150, 40)), ("pumpkin", (210, 120, 30)), ("hay", (200, 170, 40)), ("wheat", (210, 190, 90)),
    ("farmland", (100, 70, 45)), ("dirt", (125, 90, 60)), ("mud", (70, 60, 55)), ("podzol", (95, 70, 40)),
    ("red_sand", (190, 100, 45)), ("sandstone", (215, 205, 155)), ("sand", (220, 210, 160)), ("gravel", (135, 128, 125)),
    ("clay", (160, 165, 180)), ("blackstone", (45, 40, 48)), ("basalt", (80, 80, 85)), ("deepslate", (75, 75, 80)),
    ("obsidian", (35, 25, 50)), ("netherrack", (115, 50, 50)), ("nether_brick", (60, 30, 35)), ("soul", (85, 65, 52)),
    ("end_stone", (220, 222, 160)), ("purpur", (170, 125, 170)), ("quartz", (235, 230, 225)), ("prismarine", (90, 160, 150)),
    ("copper", (190, 110, 80)), ("iron", (215, 215, 215)), ("gold", (245, 205, 60)), ("diamond", (100, 220, 215)),
    ("emerald", (60, 200, 100)), ("redstone", (190, 30, 25)), ("lapis", (40, 70, 160)), ("amethyst", (140, 100, 200)),
    ("hopper", (70, 70, 75)), ("observer", (100, 100, 100)), ("piston", (150, 130, 100)), ("rail", (140, 120, 90)),
    ("chest", (160, 115, 50)), ("barrel", (130, 95, 55)), ("crafting", (140, 100, 60)), ("furnace", (110, 110, 110)),
    ("string", (230, 230, 230)), ("tripwire", (230, 230, 230)), ("torch", (250, 200, 80)), ("lantern", (240, 190, 90)),
    ("glowstone", (240, 210, 130)), ("sea_lantern", (210, 230, 220)), ("shroomlight", (240, 150, 70)),
    ("terracotta", (155, 95, 70)), ("brick", (150, 75, 60)), ("tuff", (110, 112, 105)), ("calcite", (225, 225, 220)),
    ("andesite", (135, 135, 135)), ("diorite", (200, 200, 200)), ("granite", (150, 105, 85)),
    ("cobblestone", (120, 120, 120)), ("smooth_stone", (160, 160, 160)), ("stone", (125, 125, 125)),
    ("dark_oak", (65, 45, 25)), ("spruce", (110, 80, 50)), ("birch", (200, 180, 125)), ("jungle", (160, 115, 80)),
    ("acacia", (170, 90, 50)), ("mangrove", (115, 50, 45)), ("cherry", (225, 175, 165)), ("crimson", (110, 50, 70)),
    ("warped", (45, 110, 105)), ("oak", (160, 130, 80)), ("bookshelf", (140, 100, 60)), ("wool", (230, 230, 230)),
    ("concrete", (200, 200, 200)), ("slime", (110, 190, 90)), ("honey", (230, 160, 40)), ("bone", (225, 220, 200)),
    ("scaffolding", (200, 170, 100)), ("ladder", (150, 115, 70)), ("fence", (150, 120, 75)), ("wall", (120, 120, 120)),
]
DYES = {"white": (233, 236, 236), "orange": (240, 118, 20), "magenta": (190, 70, 180), "light_blue": (58, 175, 217),
        "yellow": (248, 198, 40), "lime": (112, 185, 25), "pink": (237, 141, 172), "gray": (62, 68, 71),
        "light_gray": (142, 142, 134), "cyan": (21, 137, 145), "purple": (121, 42, 172), "blue": (53, 57, 157),
        "brown": (114, 71, 40), "green": (84, 109, 27), "red": (161, 39, 34), "black": (20, 21, 25)}


def color_of(name):
    n = name.replace("minecraft:", "")
    for dye in sorted(DYES, key=len, reverse=True):
        if n.startswith(dye + "_"):
            return DYES[dye]
    for key, c in COLORS:
        if key in n:
            return c
    h = hashlib.md5(n.encode()).digest()
    return (90 + h[0] % 90, 90 + h[1] % 90, 90 + h[2] % 90)


TRANSLUCENT = ("glass", "water", "ice")


# ------------------------------------------------------------------ drawing
def _sprite(s):
    """Pixel offsets of one isometric cube (width 2s, height 2s) and which face each belongs to (0 top, 1 right, 2 left)."""
    w, h = 2 * s, 2 * s
    faces = []
    for face, poly in ((0, [(s, 0), (2 * s, s / 2), (s, s), (0, s / 2)]),
                       (1, [(s, s), (2 * s, s / 2), (2 * s, 1.5 * s), (s, 2 * s)]),
                       (2, [(0, s / 2), (s, s), (s, 2 * s), (0, 1.5 * s)])):
        img = Image.new("L", (w + 1, h + 1), 0)
        ImageDraw.Draw(img).polygon(poly, fill=1)
        faces.append(np.array(img, dtype=bool))
    taken = np.zeros_like(faces[0])
    dx, dy, f, edge = [], [], [], []
    for i, m in enumerate(faces):
        m = m & ~taken
        taken |= m
        p = np.pad(m, 1)
        inner = m & p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]
        ys, xs = np.nonzero(m)
        dx += xs.tolist(); dy += ys.tolist(); f += [i] * len(xs)
        edge += (~inner[ys, xs]).tolist() if s >= 3 else [False] * len(xs)
    return np.array(dx), np.array(dy), np.array(f), np.array(edge)


def _scale_for(sx, sy, sz, max_w=900, max_h=900):
    for s in range(12, 0, -1):
        w = (sx + sz) * s + 2 * s
        h = int((sx + sz) * s / 2 + sy * s + 2 * s)
        if w <= max_w and h <= max_h:
            return s
    return 1


def render(blocks, palette, scale=None, size=None, background=(15, 23, 42)):
    """blocks[y, z, x]. Returns a PIL image. size=(sx, sy, sz) keeps the canvas fixed between GIF frames."""
    sy, sz, sx = blocks.shape
    if size:
        sx, sy, sz = size
    s = scale or _scale_for(sx, sy, sz)
    W = (sx + sz) * s + 2 * s
    H = int((sx + sz) * s / 2 + sy * s + 2 * s) + 1
    canvas = np.zeros((H, W, 3), dtype=np.uint8)
    canvas[:] = background
    occ = blocks > 0
    if occ.any():
        pad = np.pad(occ, 1)
        # a block is drawn if a face toward the camera (+y top, +x right, +z left) is open
        exposed = occ & (~pad[2:, 1:-1, 1:-1] | ~pad[1:-1, 1:-1, 2:] | ~pad[1:-1, 2:, 1:-1])
        y, z, x = np.nonzero(exposed)
        ids = blocks[y, z, x]
        order = np.argsort(x + y + z, kind="stable")
        y, z, x, ids = y[order], z[order], x[order], ids[order]
        lut = np.array([color_of(n) if n else (0, 0, 0) for n in palette], dtype=np.float32)
        u = (x - z + sz - 1) * s
        v = ((x + z) * s / 2 + (sy - 1 - y) * s).astype(np.int64)
        dx, dy, face, edge = _sprite(s)
        shade = np.array([1.0, 0.78, 0.62], dtype=np.float32)[face] * np.where(edge, 0.86, 1.0).astype(np.float32)
        px = (u[:, None] + dx[None, :]).ravel()
        py = (v[:, None] + dy[None, :]).ravel()
        col = (lut[ids][:, None, :] * shade[None, :, None]).reshape(-1, 3)
        ok = (px >= 0) & (px < W) & (py >= 0) & (py < H)
        canvas[py[ok], px[ok]] = col[ok].clip(0, 255).astype(np.uint8)
    return Image.fromarray(canvas)


def preview(sch, max_px=900):
    img = render(sch.blocks, sch.palette)
    img.thumbnail((max_px, max_px))
    return img


def timelapse(sch, frames=36, max_bytes=8_000_000):
    """A GIF of the build going up layer by layer. Returns bytes."""
    sx, sy, sz = sch.size
    occupied = np.nonzero(sch.blocks.reshape(sy, -1).any(axis=1))[0]
    if len(occupied) == 0:
        raise ValueError("empty schematic")
    lo, hi = int(occupied[0]), int(occupied[-1])
    levels = sorted(set(np.linspace(lo, hi, min(frames, hi - lo + 1)).round().astype(int).tolist()))
    for limit in (900, 640, 480, 360):
        s = _scale_for(sx, sy, sz, limit, limit)
        imgs = []
        for lv in levels:
            part = sch.blocks.copy()
            part[lv + 1:] = 0
            imgs.append(render(part, sch.palette, s, (sx, sy, sz)).convert("P", palette=Image.ADAPTIVE, colors=128))
        durations = [90] * (len(imgs) - 1) + [2500]
        buf = io.BytesIO()
        imgs[0].save(buf, "GIF", save_all=True, append_images=imgs[1:], duration=durations, loop=0, optimize=True)
        if buf.tell() <= max_bytes:
            return buf.getvalue()
    return buf.getvalue()
