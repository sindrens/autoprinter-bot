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



# ------------------------------------------------------------------ Minecraft textures
# The game's own block textures, read from the official client jar (downloaded once from Mojang, kept in TEX_DIR).
import os
import json as _json
import re as _re
import urllib.request as _url
import zipfile as _zip

TEX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "library", "textures")
MC_VERSION = os.environ.get("MC_VERSION", "26.2")
TINTS = {"grass": (145, 189, 89), "fern": (145, 189, 89), "leaves": (119, 171, 47), "vine": (119, 171, 47),
         "lily_pad": (32, 128, 48), "water": (63, 118, 228), "sugar_cane": (145, 189, 89)}
FIXED_TINT = {"spruce_leaves": (97, 153, 97), "birch_leaves": (128, 167, 85)}
NO_TINT = ("cherry_leaves", "azalea_leaves", "flowering_azalea_leaves", "pale_oak_leaves")


def ensure_textures():
    """Downloads the block textures once (about 40 MB jar, 1-2 MB kept)."""
    if os.path.isdir(TEX_DIR) and len(os.listdir(TEX_DIR)) > 500:
        return True
    try:
        manifest = _json.load(_url.urlopen("https://piston-meta.mojang.com/mc/game/version_manifest_v2.json", timeout=30))
        ver = next(v for v in manifest["versions"] if v["id"] == MC_VERSION)
        client = _json.load(_url.urlopen(ver["url"], timeout=30))["downloads"]["client"]["url"]
        data = _url.urlopen(client, timeout=300).read()
        os.makedirs(TEX_DIR, exist_ok=True)
        with _zip.ZipFile(io.BytesIO(data)) as z:
            for n in z.namelist():
                if n.startswith("assets/minecraft/textures/block/") and n.endswith(".png"):
                    open(os.path.join(TEX_DIR, os.path.basename(n)), "wb").write(z.read(n))
        return True
    except Exception as e:
        print("textures not available:", e)
        return False


_TEX_CACHE = {}


def _texture(name):
    if name in _TEX_CACHE:
        return _TEX_CACHE[name]
    path = os.path.join(TEX_DIR, name + ".png")
    img = None
    if os.path.exists(path):
        im = Image.open(path).convert("RGBA")
        w, h = im.size
        if h > w:  # animated: the first frame
            im = im.crop((0, 0, w, w))
        img = np.asarray(im.resize((16, 16), Image.NEAREST), dtype=np.float32)
    _TEX_CACHE[name] = img
    return img


def _base_names(b):
    out = [b]
    rules = [(r"_(stairs|slab)$", ""), (r"_wall$", ""), (r"_fence(_gate)?$", ""), (r"_carpet$", "_wool"),
             (r"_pane$", ""), (r"_wood$", "_log"), (r"_hyphae$", "_stem"), (r"^waxed_", ""), (r"_button$", ""),
             (r"_pressure_plate$", ""), (r"_door$", "_door_top"), (r"_trapdoor$", "_trapdoor")]
    for pat, rep in rules:
        if _re.search(pat, b):
            n = _re.sub(pat, rep, b)
            out += [n, n + "s", n + "_planks", n.replace("brick", "bricks"), n + "_block"]
    out += [b + "_block", b.replace("_block", ""), b + "s"]
    if b in ("water", "lava"):
        out.insert(0, b + "_still")
    if b == "tripwire" or b == "string":
        out.insert(0, "tripwire")
    if b == "redstone_wire":
        out.insert(0, "redstone_dust_dot")
    if b in ("smooth_stone_slab",):
        out.insert(0, "smooth_stone")
    if b.endswith("_slab") and b.replace("_slab", "") in ("smooth_quartz", "smooth_sandstone", "smooth_red_sandstone"):
        out.insert(0, b.replace("smooth_", "").replace("_slab", "") + "_top")
    return out


def _face_texture(block, face):
    """face: 'top' or 'side'."""
    b = block.replace("minecraft:", "")
    for base in _base_names(b):
        tries = [base + "_top", base + "_end", base] if face == "top" else [base + "_side", base + "_front", base]
        if base.endswith("_log") or base.endswith("_stem"):
            tries = [base + "_top", base] if face == "top" else [base]
        for t in tries:
            img = _texture(t)
            if img is not None:
                return _tint(b, t, img)
    return None


def _tint(block, tex, img):
    if any(k in block for k in NO_TINT) or tex == "grass_block_side":
        return img
    tint = FIXED_TINT.get(block)
    if tint is None:
        tint = next((c for k, c in TINTS.items() if k in tex and not tex.endswith("_side")), None)
    if tint is None:
        return img
    out = img.copy()
    out[..., :3] = out[..., :3] * (np.array(tint, dtype=np.float32) / 255.0)
    return out


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
    """Pixel offsets of one isometric cube (width 2s, height 2s), its face (0 top, 1 right/+x, 2 left/+z), the spot
    on the face (u, v in 0..1) and whether the pixel is on the face's edge."""
    w, h = 2 * s, 2 * s
    faces = []
    for face, poly in ((0, [(s, 0), (2 * s, s / 2), (s, s), (0, s / 2)]),
                       (1, [(s, s), (2 * s, s / 2), (2 * s, 1.5 * s), (s, 2 * s)]),
                       (2, [(0, s / 2), (s, s), (s, 2 * s), (0, 1.5 * s)])):
        img = Image.new("L", (w + 1, h + 1), 0)
        ImageDraw.Draw(img).polygon(poly, fill=1)
        faces.append(np.array(img, dtype=bool))
    taken = np.zeros_like(faces[0])
    dx, dy, f, edge, uu, vv = [], [], [], [], [], []
    for i, m in enumerate(faces):
        m = m & ~taken
        taken |= m
        p = np.pad(m, 1)
        inner = m & p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]
        ys, xs = np.nonzero(m)
        cx, cy = xs + 0.5, ys + 0.5
        if i == 0:  # top: x along (s, s/2), z along (-s, s/2) from (s, 0)
            a, b = (cx - s) / s, 2 * cy / s
            u, v = (a + b) / 2, (b - a) / 2
        elif i == 1:  # +x side: z along (-s, s/2) from (2s, s/2), down s
            u = (2 * s - cx) / s
            v = (cy - (s / 2 + u * s / 2)) / s
        else:  # +z side: x along (s, s/2) from (0, s/2), down s
            u = cx / s
            v = (cy - (s / 2 + u * s / 2)) / s
        dx += xs.tolist(); dy += ys.tolist(); f += [i] * len(xs)
        edge += (~inner[ys, xs]).tolist() if s >= 3 else [False] * len(xs)
        uu += np.clip(u, 0, 0.999).tolist(); vv += np.clip(v, 0, 0.999).tolist()
    return (np.array(dx), np.array(dy), np.array(f), np.array(edge), np.array(uu), np.array(vv))


_SPRITE_CACHE = {}


def _block_sprites(palette, s, textured):
    """Per palette entry: the sprite's colours (K, 3) and which pixels are drawn (K,), and whether it's see-through."""
    dx, dy, face, edge, u, v = _sprite(s)
    shade = np.array([1.0, 0.8, 0.62], dtype=np.float32)[face] * np.where(edge, 0.88, 1.0).astype(np.float32)
    K = len(dx)
    cols = np.zeros((len(palette), K, 3), dtype=np.float32)
    vis = np.zeros((len(palette), K), dtype=bool)
    solid = np.zeros(len(palette), dtype=bool)
    ti, tj = (v * 16).astype(int), (u * 16).astype(int)
    for i, name in enumerate(palette):
        if not name:
            continue
        key = (name, s, textured)
        if key not in _SPRITE_CACHE:
            top = side = None
            if textured:
                top, side = _face_texture(name, "top"), _face_texture(name, "side")
                if top is None and side is not None:
                    top = side
                if side is None and top is not None:
                    side = top
            if top is None:
                c = np.array(color_of(name), dtype=np.float32)
                rgba = np.concatenate([np.tile(c, (K, 1)), np.full((K, 1), 255.0)], axis=1)
            else:
                faces = [top, side, side]
                if s < 6:  # too small for the pattern: each face its texture's average colour
                    means = []
                    for t in faces:
                        a = t[..., 3:4] / 255.0
                        cov = float(a.mean())
                        means.append(np.concatenate([(t[..., :3] * a).sum((0, 1)) / max(1e-6, a.sum()), [255.0 if cov > 0.35 else 0.0]]))
                    rgba = np.stack([means[f] for f in face])
                else:
                    rgba = np.stack([faces[f][ti[k], tj[k]] for k, f in enumerate(face)])
            c = rgba[:, :3] * shade[:, None]
            alpha = rgba[:, 3] > 127
            _SPRITE_CACHE[key] = (c, alpha, bool(alpha.mean() > 0.97))
        cols[i], vis[i], solid[i] = _SPRITE_CACHE[key]
    return (dx, dy), cols, vis, solid


def _scale_for(sx, sy, sz, max_w=900, max_h=900):
    for s in range(16, 0, -1):
        w = (sx + sz) * s + 2 * s
        h = int((sx + sz) * s / 2 + sy * s + 2 * s)
        if w <= max_w and h <= max_h:
            return s
    return 1


def _background(H, W, top=(17, 24, 39), bottom=(30, 41, 59)):
    t = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
    return (np.array(top, np.float32) * (1 - t) + np.array(bottom, np.float32) * t).repeat(W, axis=1)


def render(blocks, palette, scale=None, size=None, textured=True, lift=None, pad=(0, 0)):
    """blocks[y, z, x]. Returns a PIL image. size=(sx, sy, sz) keeps the canvas fixed between GIF frames;
    lift=(y_from, pixels): blocks from that level up are drawn higher (the falling-in layer of the time-lapse);
    pad=(top, bottom) extra pixels for the text."""
    sy, sz, sx = blocks.shape
    if size:
        sx, sy, sz = size
    s = scale or _scale_for(sx, sy, sz)
    W = (sx + sz) * s + 2 * s
    H = int((sx + sz) * s / 2 + sy * s + 2 * s) + 1 + pad[0] + pad[1]
    canvas = _background(H, W)
    occ = blocks > 0
    if occ.any():
        (dx, dy), cols, vis, solid = _block_sprites(palette, s, textured)
        opaque = solid[blocks] & occ
        pad_o = np.pad(opaque, 1)
        # drawn if a face toward the camera (+y top, +x right, +z left) isn't covered by an opaque block
        exposed = occ & (~pad_o[2:, 1:-1, 1:-1] | ~pad_o[1:-1, 1:-1, 2:] | ~pad_o[1:-1, 2:, 1:-1])
        y, z, x = np.nonzero(exposed)
        ids = blocks[y, z, x]
        order = np.argsort(x + y + z, kind="stable")
        y, z, x, ids = y[order], z[order], x[order], ids[order]
        u = (x - z + sz - 1) * s
        v = ((x + z) * s / 2 + (sy - 1 - y) * s).astype(np.int64) + pad[0]
        if lift:
            v = v - np.where(y >= lift[0], lift[1], 0)
        px = (u[:, None] + dx[None, :])
        py = (v[:, None] + dy[None, :])
        col = cols[ids]
        ok = vis[ids] & (px >= 0) & (px < W) & (py >= 0) & (py < H)
        canvas[py[ok], px[ok]] = col[ok]
    return Image.fromarray(canvas.clip(0, 255).astype(np.uint8))


def preview(sch, max_px=900):
    textured = ensure_textures()
    img = render(sch.blocks, sch.palette, textured=textured)
    img.thumbnail((max_px, max_px))
    return img


def _font(size):
    from PIL import ImageFont
    for f in ("DejaVuSans-Bold.ttf", "Arial Bold.ttf", "Inter-Bold.otf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def _overlay(img, title, level, top, total_levels, blocks_done, done=False):
    """Name at the top, a progress bar and the block count at the bottom, the brand in the corner."""
    d = ImageDraw.Draw(img)
    W, H = img.size
    big, small = _font(max(14, W // 34)), _font(max(11, W // 52))
    d.text((14, 10), title[:48], fill=(255, 255, 255), font=big)
    d.text((W - 14, 14), "Printer Addon", fill=(148, 163, 184), font=small, anchor="ra")
    frac = 1.0 if done else max(0.0, min(1.0, (level - top[0] + 1) / max(1, total_levels)))
    y0 = H - 26
    d.rounded_rectangle((14, y0, W - 14, y0 + 8), radius=4, fill=(51, 65, 85))
    d.rounded_rectangle((14, y0, 14 + max(8, int((W - 28) * frac)), y0 + 8), radius=4,
                        fill=(34, 197, 94) if done else (59, 130, 246))
    label = f"Finished · {blocks_done:,} blocks" if done else f"Layer {level - top[0] + 1} of {total_levels} · {blocks_done:,} blocks"
    d.text((14, y0 - 6), label, fill=(226, 232, 240), font=small, anchor="ls")


def timelapse(sch, title=None, frames=48, max_bytes=9_000_000):
    """A GIF of the build going up layer by layer, each new layer dropping into place. Returns bytes."""
    textured = ensure_textures()
    sx, sy, sz = sch.size
    occupied = np.nonzero(sch.blocks.reshape(sy, -1).any(axis=1))[0]
    if len(occupied) == 0:
        raise ValueError("empty schematic")
    lo, hi = int(occupied[0]), int(occupied[-1])
    total = hi - lo + 1
    title = title or sch.name
    counts = np.cumsum((sch.blocks > 0).reshape(sy, -1).sum(axis=1))
    for limit, nframes, colors in ((720, frames, 192), (600, frames, 160), (480, 36, 128), (360, 28, 96)):
        steps = sorted(set(np.linspace(lo, hi, min(nframes, total)).round().astype(int).tolist()))
        s = _scale_for(sx, sy, sz, limit, limit - 70)
        pad = (44, 34)
        imgs, durations = [], []
        prev = lo - 1
        for i, lv in enumerate(steps):
            part = sch.blocks.copy()
            part[lv + 1:] = 0
            done_blocks = int(counts[lv])
            # the new layers fall in: two frames on the way down, then in place
            for k, drop in enumerate((int(s * 2.2), int(s * 0.9)) if s >= 3 and i > 0 else ()):
                f = render(part, sch.palette, s, (sx, sy, sz), textured, lift=(prev + 1, drop), pad=pad)
                _overlay(f, title, lv, (lo, hi), total, done_blocks)
                imgs.append(f); durations.append(40)
            f = render(part, sch.palette, s, (sx, sy, sz), textured, pad=pad)
            _overlay(f, title, lv, (lo, hi), total, done_blocks)
            imgs.append(f)
            # slow at the start and the end, quick in the middle
            t = i / max(1, len(steps) - 1)
            durations.append(int(160 - 100 * (1 - abs(2 * t - 1))))
            prev = lv
        final = render(sch.blocks, sch.palette, s, (sx, sy, sz), textured, pad=pad)
        _overlay(final, title, hi, (lo, hi), total, int(counts[hi]), done=True)
        imgs.append(final); durations.append(3500)
        pal = [im.convert("P", palette=Image.ADAPTIVE, colors=colors) for im in imgs]
        buf = io.BytesIO()
        pal[0].save(buf, "GIF", save_all=True, append_images=pal[1:], duration=durations, loop=0, optimize=True, disposal=1)
        if buf.tell() <= max_bytes:
            return buf.getvalue()
    return buf.getvalue()
