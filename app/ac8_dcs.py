"""DCS World textures: PBR <-> DCS, file formats (DDS with mipmaps), and the F/A-18 helmet converter.

DCS texture set (the "PBR to DCS" Substance Painter preset):
    <name>            base colour (+ opacity in alpha)
    <name>_Roughmet   R ambient occlusion, G roughness, B metallic
    <name>_Normal     normal map, DirectX (green down)

    python ac8_dcs.py to-dcs  <in folder> <out folder> [--format png|tga|dds] [--normal opengl|directx]
    python ac8_dcs.py to-pbr  <in folder> <out folder> [--format png|tga] [--normal opengl|directx] [--orm]
    python ac8_dcs.py reformat <folder> --format tga|dds        PNGs in a folder -> TGA / DDS (the PNGs go)
    python ac8_dcs.py helmet --out <folder> [--ac8-color F] [--ac8-normal F] [--ac8-mro F]
                             [--dcs-color F] [--dcs-normal F] [--dcs-roughmet F] [--format png|tga|dds]

PBR sets are found by name: Wing_BaseColor + Wing_Metallic + Wing_Roughness … -> set "Wing" (also ORM maps,
_Normal_OpenGL / _Normal_DirectX). DCS sets: Wing + Wing_Roughmet + Wing_Normal.

The helmet converter moves an Ace Combat 8 pilot helmet's textures onto the DCS F/A-18 helmet's UV layout:
assets/helmet_f18.npz holds both helmet shells, lined up in 3D; each texel of the DCS shell takes the colour of
the closest point of the AC8 shell. Normal maps are turned from the AC8 surface's directions into the DCS one's.
"""
import io
import re
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
HELMET_DATA = HERE / "assets" / "helmet_f18.npz"
IMAGE_EXTS = {".png", ".tga", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".dds"}

# name endings -> role (lower-case, without spaces / dashes / underscores); two-word endings are tried first
ROLES = [
    ("roughmet", ["roughmet", "roughnessmetallic", "roughmetal"]),
    ("orm", ["orm", "arm", "occlusionroughnessmetallic"]),
    ("base", ["basecolor", "basecolour", "albedo", "diffuse", "diff", "color", "colour", "col", "bc"]),
    ("metallic", ["metallic", "metalness", "metal", "met"]),
    ("roughness", ["roughness", "rough", "rgh"]),
    ("ao", ["ambientocclusion", "occlusion", "mixedao", "aomixed", "ao"]),
    ("normal_gl", ["normalopengl", "normalgl"]),
    ("normal_dx", ["normaldirectx", "normaldx"]),
    ("normal", ["normal", "normals", "nrm", "nor", "nm"]),
    ("emissive", ["emissive", "emission", "emit", "glow"]),
    ("opacity", ["opacity", "alpha", "transparency"]),
]


# ------------------------------------------------------------------ files
def split_name(stem):
    """'Wing_BaseColor' -> ('Wing', 'base'), 'Wing_Normal_OpenGL' -> ('Wing', 'normal_gl'), 'Wing' -> ('Wing', None)."""
    for pattern in (r"^(.*?)[ _\-.]+([A-Za-z]+[ _\-]+[A-Za-z]+)$", r"^(.*?)[ _\-.]+([A-Za-z][A-Za-z]*)$"):
        m = re.match(pattern, stem)
        if m:
            key = re.sub(r"[ _\-]", "", m.group(2)).lower()
            for role, names in ROLES:
                if key in names:
                    return m.group(1), role
    return stem, None


def find_sets(folder):
    """{set name: {role: path}} for the textures in a folder (not its subfolders)."""
    sets = {}
    for p in sorted(Path(folder).iterdir()):
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
            name, role = split_name(p.stem)
            sets.setdefault(name, {}).setdefault(role or "plain", p)
    return sets


def load(path, size=None):
    """float32 RGBA 0..1"""
    im = Image.open(path)
    im.load()
    if im.mode.startswith("I"):
        a = np.asarray(im, dtype=np.float32)
        im = Image.fromarray((a / max(1.0, a.max()) * 255).astype(np.uint8), "L")
    im = im.convert("RGBA")
    if size and im.size != size:
        im = im.resize(size, Image.LANCZOS)
    return np.asarray(im, dtype=np.float32) / 255.0


def _dds_blocks(im, fmt):
    buf = io.BytesIO()
    try:
        im.save(buf, "DDS", pixel_format=fmt)
    except (OSError, ValueError, KeyError) as e:
        raise RuntimeError(f"this Pillow can't write {fmt} DDS ({e}); update it: pip install -U pillow") from e
    return buf.getvalue()[128:]  # Pillow's header dropped: the blocks follow


def save_dds(im, path, fmt):
    """DDS with a full mip chain (without mipmaps textures shimmer in the distance in DCS). fmt: DXT1 / DXT5."""
    w, h = im.size
    levels, cur = [], im
    while True:
        lw, lh = cur.size
        pad = cur
        if lw % 4 or lh % 4:  # 4x4 blocks: pad the tiny levels
            pad = Image.new(cur.mode, ((lw + 3) // 4 * 4, (lh + 3) // 4 * 4))
            pad.paste(cur, (0, 0))
        levels.append(_dds_blocks(pad, fmt))
        if lw == 1 and lh == 1:
            break
        cur = cur.resize((max(1, lw // 2), max(1, lh // 2)), Image.LANCZOS)
    block = 8 if fmt == "DXT1" else 16
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x80000
    linear = max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * block
    pf = struct.pack("<II4sIIIII", 32, 0x4, fmt.encode(), 0, 0, 0, 0, 0)
    header = struct.pack("<4sIIIIIII", b"DDS ", 124, flags, h, w, linear, 0, len(levels))
    header += b"\0" * 44 + pf + struct.pack("<IIIII", 0x1000 | 0x8 | 0x400000, 0, 0, 0, 0)
    Path(path).write_bytes(header + b"".join(levels))


def save_img(im, path, fmt, kind="color"):
    """Save a PIL image as png / tga / dds. kind ('color' / 'data' / 'normal') picks the DDS compression:
    DXT5 for colour with alpha and for normals, DXT1 otherwise."""
    path = Path(path).with_suffix("." + fmt)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "dds":
        if kind == "normal":
            save_dds(im.convert("RGBA"), path, "DXT5")
        elif im.mode == "RGBA" and im.getextrema()[3][0] < 255:
            save_dds(im, path, "DXT5")
        else:
            save_dds(im.convert("RGB"), path, "DXT1")
    elif fmt == "tga":
        im.save(path, compression="tga_rle")
    else:
        im.save(path)
    return path


def save(arr, path, fmt, kind="color"):
    a = (np.clip(arr, 0, 1) * 255 + 0.5).astype(np.uint8)
    mode = "L" if a.ndim == 2 else {3: "RGB", 4: "RGBA"}[a.shape[-1]]
    return save_img(Image.fromarray(a, mode), path, fmt, kind)


def flip_green(n):
    n = n.copy()
    n[..., 1] = 1.0 - n[..., 1]
    return n


# ------------------------------------------------------------------ PBR <-> DCS
def to_dcs(name, maps, out, fmt="png", normal_in="opengl", log=print):
    """One PBR set -> the DCS files. Returns the paths written."""
    first = next((maps[r] for r in ("base", "plain", "orm", "roughness", "metallic", "normal", "normal_gl",
                                    "normal_dx", "ao") if r in maps), None)
    if first is None:
        return []
    size = Image.open(first).size
    written = []
    base_p = maps.get("base") or maps.get("plain")
    if base_p:
        base = load(base_p, size)
        if "opacity" in maps:
            base[..., 3] = load(maps["opacity"], size)[..., 0]
        written.append(save(base if base[..., 3].min() < 0.999 else base[..., :3], out / name, fmt))
    h, w = size[1], size[0]
    ao, rough, metal = np.ones((h, w), np.float32), np.full((h, w), 0.5, np.float32), np.zeros((h, w), np.float32)
    got = []
    if "orm" in maps:
        orm = load(maps["orm"], size)
        ao, rough, metal = orm[..., 0], orm[..., 1], orm[..., 2]
        got += ["ao", "roughness", "metallic"]
    for role in ("ao", "roughness", "metallic"):
        if role in maps:
            ch = load(maps[role], size)[..., 0]
            ao, rough, metal = (ch, rough, metal) if role == "ao" else (ao, ch, metal) if role == "roughness" \
                else (ao, rough, ch)
            got.append(role)
    if got:
        written.append(save(np.stack([ao, rough, metal], -1), out / f"{name}_Roughmet", fmt, "data"))
        missing = [r for r in ("ao", "roughness", "metallic") if r not in got]
        if missing:
            log(f"  {name}: no {', '.join(missing)} map; used " +
                ", ".join({"ao": "AO white", "roughness": "roughness 0.5", "metallic": "metallic 0"}[r]
                          for r in missing))
    for role, conv in (("normal_dx", "directx"), ("normal_gl", "opengl"), ("normal", normal_in)):
        if role in maps:
            n = load(maps[role], size)[..., :3]
            written.append(save(flip_green(n) if conv == "opengl" else n, out / f"{name}_Normal", fmt, "normal"))
            break
    if "emissive" in maps:
        log(f"  {name}: emissive isn't part of the DCS set; copied as {name}_Emissive")
        written.append(save(load(maps["emissive"], size)[..., :3], out / f"{name}_Emissive", fmt))
    return written


def to_pbr(name, maps, out, fmt="png", normal_out="opengl", orm=False, log=print):
    """One DCS set -> PBR maps."""
    written = []
    base_p = maps.get("plain") or maps.get("base")
    size = Image.open(base_p or next(iter(maps.values()))).size
    if base_p:
        base = load(base_p, size)
        written.append(save(base[..., :3], out / f"{name}_BaseColor", fmt))
        if base[..., 3].min() < 0.999:
            written.append(save(base[..., 3], out / f"{name}_Opacity", fmt, "data"))
    if "roughmet" in maps:
        rm = load(maps["roughmet"], size)
        if orm:
            written.append(save(rm[..., :3], out / f"{name}_ORM", fmt, "data"))
        else:
            written.append(save(rm[..., 2], out / f"{name}_Metallic", fmt, "data"))
            written.append(save(rm[..., 1], out / f"{name}_Roughness", fmt, "data"))
            written.append(save(rm[..., 0], out / f"{name}_AO", fmt, "data"))
    n_p = maps.get("normal") or maps.get("normal_dx") or maps.get("normal_gl")
    if n_p:
        n = load(n_p, size)[..., :3]
        src = "opengl" if n_p == maps.get("normal_gl") else "directx"  # DCS normals are DirectX
        if src != normal_out:
            n = flip_green(n)
        written.append(save(n, out / f"{name}_Normal_{'OpenGL' if normal_out == 'opengl' else 'DirectX'}", fmt,
                            "normal"))
    if "emissive" in maps:
        written.append(save(load(maps["emissive"], size)[..., :3], out / f"{name}_Emissive", fmt))
    return written


def _is_dcs_set(maps):
    return "roughmet" in maps or (("normal" in maps or "plain" in maps) and not
                                  {"base", "metallic", "roughness", "ao", "orm"} & set(maps))


def convert(mode, src, out, fmt=None, normal="opengl", orm=False, log=print):
    """mode 'to-dcs' (PBR sets -> DCS) or 'to-pbr' (DCS sets -> PBR), for the folder and its subfolders (kept in
    the output). Returns the number of files written."""
    src, out = Path(src), Path(out)
    fmt = fmt or ("dds" if mode == "to-dcs" else "png")
    jobs = []
    for d in [src] + sorted(p for p in src.rglob("*") if p.is_dir()):
        if d == out or out in d.parents:
            continue  # don't convert what this run (or an earlier one) wrote into the input folder
        sets = find_sets(d)
        if mode == "to-dcs":
            sets = {k: v for k, v in sets.items() if {"base", "orm", "roughness", "metallic", "ao", "normal",
                                                        "normal_gl", "normal_dx"} & set(v)}
        else:
            sets = {k: v for k, v in sets.items() if _is_dcs_set(v)}
        jobs += [(d, k, v) for k, v in sets.items()]
    if not jobs:
        log(f"No {'PBR' if mode == 'to-dcs' else 'DCS'} texture sets in {src}")
        return 0
    log(f"{len(jobs)} texture set(s) in {src} -> {out} ({fmt.upper()})")
    total = 0
    for d, name, maps in jobs:
        dest = out / d.relative_to(src)
        dest.mkdir(parents=True, exist_ok=True)
        try:
            files = to_dcs(name, maps, dest, fmt, normal, log) if mode == "to-dcs" else \
                to_pbr(name, maps, dest, fmt, normal, orm, log)
        except Exception as e:  # one bad set doesn't stop the rest
            log(f"  {name}: ERROR {e}")
            continue
        total += len(files)
        log(f"  {name}: " + ", ".join(f.name for f in files))
    log(f"Done: {total} file(s)")
    return total


def reformat(folder, fmt, log=print):
    """Every PNG in the folder (recursive) -> TGA / DDS; the PNG is removed. Names tell the DDS compression."""
    n = 0
    for p in sorted(Path(folder).rglob("*.png")):
        _name, role = split_name(p.stem)
        kind = "normal" if role and role.startswith("normal") else "data" if role in ("roughmet", "orm") else "color"
        im = Image.open(p)
        im.load()
        save_img(im.convert("RGBA") if "A" in im.getbands() else im.convert("RGB"), p, fmt, kind)
        p.unlink()
        n += 1
    log(f"Saved {n} file(s) as {fmt.upper()}")
    return n


# ------------------------------------------------------------------ the F/A-18 helmet
def _vertex_normals(V, F):
    _, inv = np.unique(np.round(V, 4), axis=0, return_inverse=True)  # shared across UV seams
    inv = inv.ravel()
    fn = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    acc = np.zeros((inv.max() + 1, 3))
    for k in range(3):
        np.add.at(acc, inv[F[:, k]], fn)
    n = acc[inv]
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)


def _tri_tangents(V, UV, F, FT):
    e1, e2 = V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]
    d1, d2 = UV[FT[:, 1]] - UV[FT[:, 0]], UV[FT[:, 2]] - UV[FT[:, 0]]
    det = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    det = np.where(np.abs(det) < 1e-12, 1e-12, det)
    return (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) / det[:, None], (e2 * d1[:, 0:1] - e1 * d2[:, 0:1]) / det[:, None]


def _frame(T, B, N):
    """Orthonormal (T, B, N) for DirectX normal maps: B points down the image (-v)."""
    T = T - (T * N).sum(-1, keepdims=True) * N
    T /= np.maximum(np.linalg.norm(T, axis=-1, keepdims=True), 1e-12)
    B = B - (B * N).sum(-1, keepdims=True) * N - (B * T).sum(-1, keepdims=True) * T
    B /= np.maximum(np.linalg.norm(B, axis=-1, keepdims=True), 1e-12)
    return T.astype(np.float32), (-B).astype(np.float32), N.astype(np.float32)


def _closest_on_tris(p, A, B, C):
    """Closest points on triangles (Ericson), vectorised -> (barycentrics, squared distances)."""
    ab, ac, ap = B - A, C - A, p - A
    d1, d2 = (ab * ap).sum(-1), (ac * ap).sum(-1)
    bp, cp = p - B, p - C
    d3, d4 = (ab * bp).sum(-1), (ac * bp).sum(-1)
    d5, d6 = (ab * cp).sum(-1), (ac * cp).sum(-1)
    va, vb, vc = d3 * d6 - d5 * d4, d5 * d2 - d1 * d6, d1 * d4 - d3 * d2
    den = va + vb + vc
    den = np.where(np.abs(den) < 1e-12, 1e-12, den)
    v, w = vb / den, vc / den
    bary = np.stack([1 - v - w, v, w], -1)
    one, zero = np.ones_like(d1), np.zeros_like(d1)

    def put(mask, b0, b1, b2):
        bary[mask] = np.stack([b0, b1, b2], -1)[mask]

    def sdiv(a, b):
        return a / np.where(b == 0, 1, b)

    t = sdiv(d1, d1 - d3)
    put((vc <= 0) & (d1 >= 0) & (d3 <= 0), 1 - t, t, zero)
    t = sdiv(d2, d2 - d6)
    put((vb <= 0) & (d2 >= 0) & (d6 <= 0), 1 - t, zero, t)
    t = sdiv(d4 - d3, (d4 - d3) + (d5 - d6))
    put((va <= 0) & (d4 - d3 >= 0) & (d5 - d6 >= 0), zero, 1 - t, t)
    put((d1 <= 0) & (d2 <= 0), one, zero, zero)
    put((d3 >= 0) & (d4 <= d3), zero, one, zero)
    put((d6 >= 0) & (d5 <= d6), zero, zero, one)
    q = bary[..., :1] * A + bary[..., 1:2] * B + bary[..., 2:] * C
    return bary, ((q - p) ** 2).sum(-1)


def _bilinear(img, x, y):
    """Sample an H x W x C float image at pixel coordinates (edge clamped)."""
    h, w = img.shape[:2]
    x, y = np.clip(x, 0, w - 1), np.clip(y, 0, h - 1)
    x0, y0 = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
    x1, y1 = np.minimum(x0 + 1, w - 1), np.minimum(y0 + 1, h - 1)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    return (img[y0, x0] * (1 - fx) * (1 - fy) + img[y0, x1] * fx * (1 - fy) + img[y1, x0] * (1 - fx) * fy
            + img[y1, x1] * fx * fy)


def _rasterize(points2d, faces, values, grid, lo, hi):
    """Interpolate per-vertex `values` over triangles drawn in a 2D plane (points2d) onto a grid x grid image
    spanning [lo, hi]^2. Returns (image of values, face index per pixel or -1)."""
    n = grid
    out = np.zeros((n, n, values.shape[1]), np.float32)
    fid = np.full((n, n), -1, np.int32)
    scale = (n - 1) / (hi - lo)
    P = (points2d - lo) * scale
    for f, (a, b, c) in enumerate(faces):
        tri = P[[a, b, c]]
        x0, y0 = np.floor(tri.min(0)).astype(int)
        x1, y1 = np.ceil(tri.max(0)).astype(int)
        xs, ys = np.meshgrid(np.arange(max(x0, 0), min(x1 + 1, n)), np.arange(max(y0, 0), min(y1 + 1, n)))
        if not xs.size:
            continue
        q = np.stack([xs.ravel(), ys.ravel()], 1).astype(np.float64)
        A, B, C = tri
        den = (B[1] - C[1]) * (A[0] - C[0]) + (C[0] - B[0]) * (A[1] - C[1])
        if abs(den) < 1e-12:
            continue
        l0 = ((B[1] - C[1]) * (q[:, 0] - C[0]) + (C[0] - B[0]) * (q[:, 1] - C[1])) / den
        l1 = ((C[1] - A[1]) * (q[:, 0] - C[0]) + (A[0] - C[0]) * (q[:, 1] - C[1])) / den
        l2 = 1 - l0 - l1
        ok = (l0 >= -1e-6) & (l1 >= -1e-6) & (l2 >= -1e-6)
        if not ok.any():
            continue
        L = np.stack([l0[ok], l1[ok], l2[ok]], 1)
        yy, xx = q[ok, 1].astype(int), q[ok, 0].astype(int)
        out[yy, xx] = L @ values[[a, b, c]]
        fid[yy, xx] = f
    return out, fid


class HelmetMap:
    """Which point of the AC8 helmet each texel of the DCS helmet shell shows (worked out once per size).

    Both shells are caps round the same centre. A point is described by its direction from the centre: azimuth
    phi round the vertical axis (0 = the front) and polar angle theta from the top. Each shell's rim is a
    theta per phi. Every DCS point is matched to the AC8 point at the same phi, with theta scaled so that the AC8
    rim lands on the DCS rim: the whole design is stretched to the DCS shell's edge (no edge line is smeared
    outward, no gaps). The scaling is spread over the middle of the shell, so the name near the rim keeps its
    shape and only moves. At the front, the "rim" used for the DCS shell is raised (RAISE) so the name clears
    the brow pad; below it (behind the pad) the AC8 paint just stops at its edge."""
    PAD = 8       # pixels painted past the shell's UV edges, so mipmaps don't pull in the old colours
    RAISE = 1.55  # at the front: how far above the DCS rim the AC8 rim lands (units of the meshes, ~24 across)
    INSET = 0.35  # the AC8 rim used is this far inside its edge (the original paint spills past the edge)
    GRID = 3072   # resolution of the AC8 shell's lookup image

    def __init__(self, size=4096, data=HELMET_DATA, raise_=RAISE, log=print):
        m = np.load(data)
        self.size = W = H = size
        Vd, Td = m["dcs_v"].astype(np.float64), m["dcs_uv"].astype(np.float64)
        Fd, FTd = m["dcs_f"], m["dcs_ft"]
        Va, Ta = m["ac8_v"].astype(np.float64), m["ac8_uv"].astype(np.float64)
        Fa, FTa = m["ac8_f"], m["ac8_ft"]
        if not np.isscalar(raise_):  # FRONT,BACK from older settings: the front is what counts now
            raise_ = raise_[0]
        self.ac8_uv, self.ac8_ft = Ta, FTa
        # painted details of the AC8 helmet that the DCS helmet doesn't have (the strap slots and their rims),
        # in AC8 UV space: closed up before the transfer
        self.paintout = None
        if "ac8_paintout" in m:
            n = int(m["ac8_paintout_size"][0])
            self.paintout = np.unpackbits(m["ac8_paintout"])[:n * n].reshape(n, n).astype(bool)

        # the shared centre: a sphere fitted to the DCS shell
        A_ = np.c_[2 * Vd, np.ones(len(Vd))]
        sol = np.linalg.lstsq(A_, (Vd ** 2).sum(1), rcond=None)[0]
        ctr, radius = sol[:3], np.sqrt(sol[3] + sol[:3] @ sol[:3])

        def angles(P_):
            d = P_ - ctr
            phi = np.arctan2(d[:, 2], d[:, 0])
            theta = np.arccos(np.clip(d[:, 1] / np.maximum(np.linalg.norm(d, axis=1), 1e-12), -1, 1))
            return phi, theta

        def rim(V, F):
            """theta of the shell's open edge for 720 directions round the helmet (smoothed)."""
            _, wid = np.unique(np.round(V, 4), axis=0, return_inverse=True)
            wid = wid.ravel()
            pairs = np.array([[0, 1], [1, 2], [2, 0]])
            ek = np.sort(wid[F[:, pairs]].reshape(-1, 2), axis=1)
            ep = V[F[:, pairs]].reshape(-1, 2, 3)
            _, inv, cnt = np.unique(ek, axis=0, return_inverse=True, return_counts=True)
            edge = ep[cnt[inv.ravel()] == 1]
            pts_ = (edge[:, :1] + (edge[:, 1:] - edge[:, :1]) * np.linspace(0, 1, 9)[None, :, None]).reshape(-1, 3)
            ph, th = angles(pts_)
            bins = ((ph + np.pi) / (2 * np.pi) * 720).astype(int) % 720
            r = np.full(720, np.nan)
            for b in range(720):
                sel = bins == b
                if sel.any():
                    r[b] = th[sel].max()
            ok = ~np.isnan(r)
            idx = np.arange(720)
            r = np.interp(idx, np.r_[idx[ok] - 720, idx[ok], idx[ok] + 720], np.r_[r[ok], r[ok], r[ok]])
            k = np.exp(-0.5 * (np.arange(-12, 13) / 4.0) ** 2)  # ~2 degrees
            r = np.convolve(np.r_[r[-12:], r, r[:12]], k / k.sum(), mode="valid")
            return r

        def rim_at(r, phi):
            x = (phi + np.pi) / (2 * np.pi) * 720 - 0.5
            return np.interp(x, np.arange(-1, 721), np.r_[r[-1], r, r[0]])

        rim_d, rim_a = rim(Vd, Fd), rim(Va, Fa)
        inset = self.INSET / radius
        lift = raise_ / radius

        def front_weight(phi):  # 1 across the front (where the name is), 0 from the sides round to the back
            a = np.degrees(np.abs(phi))
            t_ = np.clip((90 - a) / 40, 0, 1)
            return t_ * t_ * (3 - 2 * t_)

        # the AC8 shell drawn flat around its top (distance from the top, in the direction phi): a lookup image of
        # its UV and triangle at every point, so any direction can be read with no gaps
        ph_a, th_a = angles(Va)
        flat_a = np.stack([th_a * np.cos(ph_a), th_a * np.sin(ph_a)], 1)
        lim = float(th_a.max()) + 0.05
        log(f"Helmet: mapping the AC8 shell ({self.GRID}×{self.GRID})…")
        uv_img, fid_img = _rasterize(flat_a, Fa, Ta, self.GRID, -lim, lim)
        # fill the few pixels just past the AC8 edge with the edge's UVs (for bilinear reads right at the edge)
        known = fid_img >= 0
        for _ in range(3):
            grow = ~known & (np.roll(known, 1, 0) | np.roll(known, -1, 0) | np.roll(known, 1, 1) | np.roll(known, -1, 1))
            for axis, step in ((0, 1), (0, -1), (1, 1), (1, -1)):
                take = grow & np.roll(known, step, axis)
                uv_img[take] = np.roll(uv_img, step, axis)[take]
                fid_img[take] = np.roll(fid_img, step, axis)[take]
                grow &= ~take
                known |= take
        # how far out each direction actually stays on the AC8 shell in that image (where the edge runs steeply,
        # the smoothed rim above overshoots it)
        scale = (self.GRID - 1) / (2 * lim)
        ray_phi = (np.arange(2880) + 0.5) / 2880 * 2 * np.pi - np.pi
        steps = np.arange(0, lim, 0.5 / scale)
        rx = ((steps[None] * np.cos(ray_phi)[:, None] + lim) * scale).round().astype(int).clip(0, self.GRID - 1)
        ry = ((steps[None] * np.sin(ray_phi)[:, None] + lim) * scale).round().astype(int).clip(0, self.GRID - 1)
        inside = fid_img >= 0
        for _ in range(3 + int(round(self.INSET / radius * scale))):  # INSET in from the edge, all round
            inside = (inside & np.roll(inside, 1, 0) & np.roll(inside, -1, 0)
                      & np.roll(inside, 1, 1) & np.roll(inside, -1, 1))
        on = inside[ry, rx]
        first_off = np.where(on.all(1), len(steps) - 1, np.argmin(on, axis=1))
        ray_lim = steps[np.maximum(first_off - 1, 0)]

        def ray_at(phi):
            x = (phi + np.pi) / (2 * np.pi) * 2880 - 0.5
            return np.interp(x, np.arange(-1, 2881), np.r_[ray_lim[-1], ray_lim, ray_lim[0]])

        log("Helmet: finding the DCS shell's texels…")
        pix, tri_ds, bar_ds = [], [], []
        for f, (t0, t1, t2) in enumerate(FTd):
            uv = np.array([Td[t0], Td[t1], Td[t2]]) * [W, -H] + [0, H]  # pixels (v up -> rows down)
            x0, y0 = np.floor(uv.min(0)).astype(int)
            x1, y1 = np.ceil(uv.max(0)).astype(int)
            xs, ys = np.meshgrid(np.arange(max(x0, 0), min(x1 + 1, W)), np.arange(max(y0, 0), min(y1 + 1, H)))
            P = np.stack([xs.ravel() + 0.5, ys.ravel() + 0.5], 1)
            a, b, c = uv
            den = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
            if abs(den) < 1e-12 or not len(P):
                continue
            l0 = ((b[1] - c[1]) * (P[:, 0] - c[0]) + (c[0] - b[0]) * (P[:, 1] - c[1])) / den
            l1 = ((c[1] - a[1]) * (P[:, 0] - c[0]) + (a[0] - c[0]) * (P[:, 1] - c[1])) / den
            l2 = 1 - l0 - l1
            ok = (l0 >= -1e-4) & (l1 >= -1e-4) & (l2 >= -1e-4)
            if ok.any():
                pix.append(P[ok].astype(np.int32))
                tri_ds.append(np.full(ok.sum(), f, np.int32))
                bar_ds.append(np.stack([l0[ok], l1[ok], l2[ok]], 1).astype(np.float32))
        pix, tri_d_all, bar_d_all = np.concatenate(pix), np.concatenate(tri_ds), np.concatenate(bar_ds)

        log(f"Helmet: matching {len(pix):,} texels to the AC8 helmet…")
        n_all = len(pix)
        uv_a = np.zeros((n_all, 2), np.float32)
        tri_a_all = np.zeros(n_all, np.int32)
        bar_a_all = np.zeros((n_all, 3), np.float32)
        for s0 in range(0, n_all, 500000):
            sl = slice(s0, s0 + 500000)
            pts = (bar_d_all[sl, :, None].astype(np.float64) * Vd[Fd[tri_d_all[sl]]]).sum(1)
            phi, th = angles(pts)
            rd = rim_at(rim_d, phi) - lift * front_weight(phi)  # where the AC8 rim should land
            ra = np.minimum(rim_at(rim_a, phi) - inset, ray_at(phi))  # the AC8 rim (just inside its edge)
            t = th / rd
            # theta_a = theta + (ra - rd) * s(t): the difference is spread over the middle of the shell (s rises
            # from 0 at t=0.15 to 1 at t=0.8), so the top stays put and the band by the rim only moves
            sm = np.clip((t - 0.15) / 0.65, 0, 1)
            sm = sm * sm * (3 - 2 * sm)
            tha = np.minimum(th + (ra - rd) * sm, ra)  # past the raised rim (behind the brow pad): the AC8 edge
            gx = (tha * np.cos(phi) + lim) * scale
            gy = (tha * np.sin(phi) + lim) * scale
            x0 = np.clip(np.floor(gx).astype(int), 0, self.GRID - 2)
            y0 = np.clip(np.floor(gy).astype(int), 0, self.GRID - 2)
            fx, fy = (gx - x0)[:, None], (gy - y0)[:, None]
            uv_a[sl] = (uv_img[y0, x0] * (1 - fx) * (1 - fy) + uv_img[y0, x0 + 1] * fx * (1 - fy)
                        + uv_img[y0 + 1, x0] * (1 - fx) * fy + uv_img[y0 + 1, x0 + 1] * fx * fy)
            tri = fid_img[np.clip(np.round(gy).astype(int), 0, self.GRID - 1),
                          np.clip(np.round(gx).astype(int), 0, self.GRID - 1)]
            tri = np.where(tri >= 0, tri, 0)
            tri_a_all[sl] = tri
            # barycentrics of the UV within that AC8 triangle (for the normal maps' tangent frames)
            tA, tB, tC = Ta[FTa[tri, 0]], Ta[FTa[tri, 1]], Ta[FTa[tri, 2]]
            v0, v1, v2 = tB - tA, tC - tA, uv_a[sl].astype(np.float64) - tA
            d00, d01, d11 = (v0 * v0).sum(1), (v0 * v1).sum(1), (v1 * v1).sum(1)
            d20, d21 = (v2 * v0).sum(1), (v2 * v1).sum(1)
            dn = np.where(np.abs(d00 * d11 - d01 * d01) < 1e-18, 1e-18, d00 * d11 - d01 * d01)
            bv, bw = (d11 * d20 - d01 * d21) / dn, (d00 * d21 - d01 * d20) / dn
            bb = np.clip(np.stack([1 - bv - bw, bv, bw], 1), 0, 1)
            bar_a_all[sl] = bb / np.maximum(bb.sum(1, keepdims=True), 1e-9)
        self.pix, self.uv_a = pix, uv_a
        self._geo = (Va, Ta, Fa, FTa, tri_a_all, bar_a_all, Vd, Td, Fd, FTd, tri_d_all, bar_d_all)
        self._frames = None
        self.mask = np.zeros((H, W), bool)
        self.mask[pix[:, 1], pix[:, 0]] = True
        log(f"Helmet: {len(pix):,} texels on the shell")

    def _tangent_frames(self):
        """Smooth tangent frames of both shells at every texel (only needed for normal maps)."""
        if self._frames is None:
            Va, Ta, Fa, FTa, tri_a, bar_a, Vd, Td, Fd, FTd, tri_d, bar_d = self._geo

            def frames(V, UV, F, FT, tri, bar):
                N = _vertex_normals(V, F)
                t, b = _tri_tangents(V, UV, F, FT)
                acc_t, acc_b = np.zeros((FT.max() + 1, 3)), np.zeros((FT.max() + 1, 3))
                for k in range(3):  # tangents smoothed over each UV vertex (per triangle they'd show facets)
                    np.add.at(acc_t, FT[:, k], t)
                    np.add.at(acc_b, FT[:, k], b)
                w = bar[:, :, None].astype(np.float64)
                n = (w * N[F[tri]]).sum(1)
                n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
                return _frame((w * acc_t[FT[tri]]).sum(1), (w * acc_b[FT[tri]]).sum(1), n)
            self._frames = (frames(Va, Ta, Fa, FTa, tri_a, bar_a), frames(Vd, Td, Fd, FTd, tri_d, bar_d))
        return self._frames

    def _sample(self, path):
        img = self._clean(np.asarray(Image.open(path).convert("RGBA"), dtype=np.float32))
        h, w = img.shape[:2]
        return _bilinear(img, self.uv_a[:, 0] * w - 0.5, (1 - self.uv_a[:, 1]) * h - 0.5)

    def _clean(self, img):
        """Close up AC8's painted strap slots (the DCS helmet has its own strap fittings): row by row, the paint
        on each side of a slot is stretched sideways over it, so the design carries on with no new pixels."""
        if self.paintout is None:
            return img
        h, w = img.shape[:2]
        po = np.asarray(Image.fromarray(self.paintout.astype(np.uint8) * 255).resize((w, h), Image.BILINEAR)) > 0
        out = img.copy()
        for y in np.nonzero(po.any(1))[0]:
            row = po[y]
            d = np.diff(np.concatenate([[0], row.astype(np.int8), [0]]))
            for x0, x1 in zip(np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]):
                width = x1 - x0
                xm = (x0 + x1) / 2
                l0, r1 = max(x0 - width, 0), min(x1 + width, w)  # the paint beside the slot, as wide as the slot
                for lo, hi, s_lo, s_hi in ((l0, int(np.ceil(xm)), l0, x0), (int(np.ceil(xm)), r1, x1, r1)):
                    if hi <= lo or s_hi <= s_lo:
                        continue
                    xs = np.arange(lo, hi)
                    src = s_lo + (xs - lo) * (s_hi - s_lo) / (hi - lo)  # stretch [s_lo, s_hi) over [lo, hi)
                    for c in range(img.shape[2]):
                        out[y, lo:hi, c] = np.interp(src, np.arange(w), img[y, :, c])
        return out

    def _base(self, path, channels):
        """The DCS helmet's own map to paint the shell into, or (without one) a transparent image, so the shell
        drops straight onto a layer in Photoshop."""
        if path:
            im = Image.open(path).convert({3: "RGB", 4: "RGBA"}[channels])
            if im.size != (self.size, self.size):
                im = im.resize((self.size, self.size), Image.LANCZOS)
            return np.asarray(im, dtype=np.float32).copy()
        return np.zeros((self.size, self.size, 4), np.float32)

    def _paint(self, base, values):
        if base.shape[2] == 4 and values.shape[1] == 3:  # opaque shell on a transparent image
            values = np.concatenate([values, np.full((len(values), 1), 255, np.float32)], 1)
        out = base
        out[self.pix[:, 1], self.pix[:, 0]] = values
        out = np.clip(out + 0.5, 0, 255).astype(np.uint8)
        # grow the shell a few pixels past its UV edges (copying its edge colours outward), in its bounding box
        ys, xs = np.nonzero(self.mask)
        y0, y1 = max(ys.min() - self.PAD, 0), min(ys.max() + self.PAD + 1, self.size)
        x0, x1 = max(xs.min() - self.PAD, 0), min(xs.max() + self.PAD + 1, self.size)
        sub, mask = out[y0:y1, x0:x1], self.mask[y0:y1, x0:x1].copy()
        for _ in range(self.PAD):
            new = np.zeros_like(mask)
            for axis, step in ((0, 1), (0, -1), (1, 1), (1, -1)):
                src_m = np.roll(mask, step, axis)
                take = src_m & ~mask & ~new
                if take.any():
                    sub[take] = np.roll(sub, step, axis)[take]
                    new |= take
            if not new.any():
                break
            mask |= new
        im = Image.fromarray(out, {3: "RGB", 4: "RGBA"}[out.shape[2]])
        return im.convert("RGB") if im.mode == "RGBA" and im.getextrema()[3][0] == 255 else im

    def color(self, ac8_color, dcs_color=None):
        col = self._sample(ac8_color)
        col[:, 3] = 255  # the shell is opaque (AC8 colour maps can carry other data in alpha)
        return self._paint(self._base(dcs_color, 4), col)

    def roughmet(self, ac8_mro, dcs_roughmet=None):
        """AC8 _MRO (R metallic, G roughness, B AO) -> DCS Roughmet (R AO, G roughness, B metallic)."""
        mro = self._sample(ac8_mro)
        return self._paint(self._base(dcs_roughmet, 3), mro[:, [2, 1, 0]])

    def normal(self, ac8_normal, dcs_normal=None):
        """AC8 normal map (DirectX) -> the DCS shell's directions (DirectX)."""
        s = self._sample(ac8_normal) / 255.0
        x, y = s[:, 0] * 2 - 1, s[:, 1] * 2 - 1
        z = np.sqrt(np.clip(1 - x * x - y * y, 0, 1))  # rebuilt: the blue channel isn't always z
        (Ta, Ba, Na), (Td, Bd, Nd) = self._tangent_frames()
        # turn the AC8 frame so its normal lies on the DCS one: the shells aren't identical, and the difference
        # in shape mustn't turn into fake bumps (a flat map stays flat)
        k = np.cross(Na, Nd)
        sn = np.linalg.norm(k, axis=1, keepdims=True)
        cs = (Na * Nd).sum(1, keepdims=True)
        k = k / np.maximum(sn, 1e-12)

        def rot(v):
            return v * cs + np.cross(k, v) * sn + k * (k * v).sum(1, keepdims=True) * (1 - cs)
        world = x[:, None] * rot(Ta) + y[:, None] * rot(Ba) + z[:, None] * Nd
        n = np.stack([(world * Td).sum(1), (world * Bd).sum(1), (world * Nd).sum(1)], 1)
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
        return self._paint(self._base(dcs_normal, 3), (n * 0.5 + 0.5) * 255)


def helmet(out, ac8_color=None, ac8_normal=None, ac8_mro=None, dcs_color=None, dcs_normal=None,
           dcs_roughmet=None, fmt=None, raise_=HelmetMap.RAISE, log=print):
    """The helmet converter. The DCS files (optional) keep everything but the shell; the results get their
    names (and format, unless fmt is given). Returns the paths written."""
    out = Path(out)
    size = next((max(Image.open(p).size) for p in (dcs_color, dcs_normal, dcs_roughmet) if p), 4096)
    hm = HelmetMap(size, raise_=raise_, log=log)
    written = []
    for src, base, default, fn, kind in ((ac8_color, dcs_color, "pilot_F18_helmet", hm.color, "color"),
                                         (ac8_mro, dcs_roughmet, "pilot_F18_helmet_Roughmet", hm.roughmet, "data"),
                                         (ac8_normal, dcs_normal, "pilot_F18_helmet_Normal", hm.normal, "normal")):
        if not src:
            continue
        log(f"Helmet: {Path(src).name} -> {Path(base).name if base else default}")
        im = fn(src, base)
        f = fmt or (Path(base).suffix.lower().lstrip(".").replace("tiff", "tif") if base else "png")
        if f not in ("png", "tga", "dds"):
            f = "png"
        written.append(save_img(im, out / (Path(base).stem if base else default), f, kind))
    log(f"Helmet done: {', '.join(p.name for p in written)} in {out}")
    return written


# ------------------------------------------------------------------ command line
def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="DCS World textures (see the top of this file)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("to-dcs", "to-pbr"):
        p = sub.add_parser(name)
        p.add_argument("src")
        p.add_argument("out")
        p.add_argument("--format", choices=["png", "tga", "dds"])
        p.add_argument("--normal", choices=["opengl", "directx"], default="opengl")
        p.add_argument("--orm", action="store_true")
    p = sub.add_parser("reformat")
    p.add_argument("folder")
    p.add_argument("--format", choices=["tga", "dds"], required=True)
    p = sub.add_parser("helmet")
    p.add_argument("--out", required=True)
    for k in ("ac8-color", "ac8-normal", "ac8-mro", "dcs-color", "dcs-normal", "dcs-roughmet"):
        p.add_argument("--" + k)
    p.add_argument("--format", choices=["png", "tga", "dds"])
    p.add_argument("--raise", dest="raise_", default=str(HelmetMap.RAISE),
                   help="move the AC8 design up on the DCS helmet: FRONT[,BACK] (0 = as lined up)")
    a = ap.parse_args(argv)

    def log(msg):
        print(msg, flush=True)
    if a.cmd in ("to-dcs", "to-pbr"):
        return 0 if convert(a.cmd, a.src, a.out, a.format, a.normal, a.orm, log) else 1
    if a.cmd == "reformat":
        reformat(a.folder, a.format, log)
        return 0
    return 0 if helmet(a.out, a.ac8_color, a.ac8_normal, a.ac8_mro, a.dcs_color, a.dcs_normal, a.dcs_roughmet,
                       a.format, float(a.raise_.split(",")[0]), log) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
