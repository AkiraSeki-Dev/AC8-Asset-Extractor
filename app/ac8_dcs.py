"""DCS World textures: PBR <-> DCS, file formats (DDS with mipmaps), and the F/A-18 helmet converter.

DCS texture set (the "PBR to DCS" Substance Painter preset):
    <name>            base colour (+ opacity in alpha)
    <name>_Roughmet   R ambient occlusion, G roughness, B metallic, A ambient occlusion again (as DCS's own)
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
# the DCS helmets the AC8 pilot helmet can go onto: key -> (name, the two shells lined up, default file name)
AIRCRAFT = {
    "f18": ("F/A-18", HERE / "assets" / "helmet_f18.npz", "pilot_F18_helmet"),
    "f14": ("F-14", HERE / "assets" / "helmet_f14.npz", "pilot_F14_helmet"),
}
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
    """Save a PIL image as png / tga / dds. kind ('color' / 'data' / 'normal' / 'roughmet') picks the DDS
    compression: DXT5 for colour with alpha, normals and Roughmet (its alpha holds AO, like DCS's own), DXT1
    otherwise. DDS files get a full mip chain, like the game's."""
    path = Path(path).with_suffix("." + fmt)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "dds":
        if kind in ("normal", "roughmet"):
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
        written.append(save(np.stack([ao, rough, metal, ao], -1), out / f"{name}_Roughmet", fmt, "roughmet"))
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
        if rm[..., 3].min() < 0.999:  # DCS keeps AO in the alpha too, at full precision (DXT5 alpha): use that
            rm[..., 0] = rm[..., 3]
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


# the slots of the one-set converter, per mode: (role, label)
SET_SLOTS = {
    "ac8": [("base", "Colour  (_C)"), ("normal", "Normal  (_N / _NO)"), ("mr", "Metal / rough  (_MRO / _MRX)"),
            ("emissive", "Emissive  (_CE / _E)")],
    "pbr_dcs": [("base", "Base colour"), ("opacity", "Opacity"), ("metallic", "Metallic"), ("roughness", "Roughness"),
                ("ao", "Ambient occlusion"), ("normal", "Normal"),
                ("emissive", "Emissive")],
    "dcs_pbr": [("plain", "Diffuse"), ("roughmet", "Roughmet"), ("normal", "Normal  (_Normal / _nm)")],
}


def _to_fmt(paths, fmt):
    """PNGs just written -> TGA / DDS (the PNG is removed). Returns the new paths."""
    if fmt in (None, "png"):
        return list(paths)
    new = []
    for p in paths:
        p = Path(p)
        _n, role = split_name(p.stem)
        kind = "normal" if role and role.startswith("normal") else role if role == "roughmet" else \
            "data" if role == "orm" else "color"
        im = Image.open(p)
        im.load()
        new.append(save_img(im.convert("RGBA") if "A" in im.getbands() else im.convert("RGB"), p, fmt, kind))
        p.unlink()
    return new


def convert_set(mode, slots, out, name, fmt=None, normal=None, orm=False, preset="separate", log=print):
    """One texture set, its maps picked one by one. mode: ac8_pbr, ac8_dcs, pbr_dcs or dcs_pbr; slots: {role: path}
    (roles from SET_SLOTS). Returns the paths written."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    slots = {k: Path(v) for k, v in slots.items() if v}
    if not slots:
        raise ValueError("no textures picked")
    if mode.startswith("ac8"):
        if str(HERE) not in sys.path:
            sys.path.insert(0, str(HERE))
        import ac8_pbr
        roles = {}
        for role, p in slots.items():
            tail = p.stem.rsplit("_", 1)[-1].upper() if "_" in p.stem else ""
            if role == "normal":
                roles["normal_ao" if tail == "NO" else "normal"] = p
            elif role == "mr":
                roles["mr" if tail in ("MRX", "MR") else "mr_ao"] = p
            else:
                roles[role] = p
        maps = ac8_pbr.unpack_set(roles)
        if mode == "ac8_dcs":
            files = _to_fmt(ac8_pbr.write_set(maps, out, name, "dcs"), fmt or "dds")
        else:
            files = _to_fmt(ac8_pbr.write_set(maps, out, name, preset, normal), fmt or "png")
    elif mode == "pbr_dcs":
        files = to_dcs(name, slots, out, fmt or "dds", normal or "opengl", log)
    elif mode == "dcs_pbr":
        files = to_pbr(name, slots, out, fmt or "png", normal or "opengl", orm, log)
    else:
        raise ValueError(mode)
    log(f"{name}: " + ", ".join(Path(f).name for f in files))
    return files


def reformat(folder, fmt, log=print):
    """Every PNG in the folder (recursive) -> TGA / DDS; the PNG is removed. Names tell the DDS compression."""
    n = 0
    for p in sorted(Path(folder).rglob("*.png")):
        _name, role = split_name(p.stem)
        kind = "normal" if role and role.startswith("normal") else role if role == "roughmet" else \
            "data" if role == "orm" else "color"
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

    Both shells are caps round the same centre and are drawn flat around their tops. The DCS shell's edge is
    laid onto the AC8 shell's edge (matched at the corners of the face opening and by direction elsewhere) and
    that pull is spread inward, fading out before the top, so the design flows in smooth curves out to the DCS
    edge with nothing smeared or repeated. Where the outlines turn differently and the pull would still run past
    the AC8 edge, the design is eased in over a wider strip. At the front the design is raised (RAISE) so the name
    clears the brow pad, and the thin strip under the name is stretched down to the DCS edge (behind the pad).
    A few texels past the shell's UV edge are mapped the same way, so mipmaps stay clean."""
    PAD = 8       # pixels painted past the shell's UV edges, so mipmaps don't pull in the old colours
    RAISE = 1.55  # at the front: how far above the DCS rim the AC8 rim lands (units of the meshes, ~24 across)
    INSET = 0.35  # the AC8 rim used is this far inside its edge (the original paint spills past the edge)
    GRID = 3072   # resolution of the AC8 shell's lookup image
    FRONT = (35, 50)  # degrees from the front: the raise (and its rim warp) is full up to the first, gone by the second
    ROWBAND = 160  # texels (at 4096): the least a row's end is eased in over
    SIDE_WARP = 1  # 1: the sides are pulled out to the DCS edge in the flat view (0: only stretched along the rows)
    ALONG = 0.05  # radians of rim the side stretch is evened out over
    SQUEEZE = 0.02  # radians: how far in from the AC8 edge paint that would run past it is eased in
    UNDER = 0.03  # at the front: the strip of the AC8 design under the name that is stretched to the DCS edge
    SPREAD = 2    # how locally the pull follows the nearest bit of rim (higher: more local)
    FADE = (0.2, 0.7)  # the pull is full this far in from the rim, then fades out over this much (radians)

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

        # the shared centre: a sphere fitted to the DCS shell
        A_ = np.c_[2 * Vd, np.ones(len(Vd))]
        sol = np.linalg.lstsq(A_, (Vd ** 2).sum(1), rcond=None)[0]
        ctr, radius = sol[:3], np.sqrt(sol[3] + sol[:3] @ sol[:3])

        def angles(P_):
            d = P_ - ctr
            phi = np.arctan2(d[:, 2], d[:, 0])
            theta = np.arccos(np.clip(d[:, 1] / np.maximum(np.linalg.norm(d, axis=1), 1e-12), -1, 1))
            return phi, theta

        def edge_points(V, F):
            """Points along the shell's open edge (its boundary edges, each cut in 8)."""
            _, wid = np.unique(np.round(V, 4), axis=0, return_inverse=True)
            wid = wid.ravel()
            pairs = np.array([[0, 1], [1, 2], [2, 0]])
            ek = np.sort(wid[F[:, pairs]].reshape(-1, 2), axis=1)
            ep = V[F[:, pairs]].reshape(-1, 2, 3)
            _, inv, cnt = np.unique(ek, axis=0, return_inverse=True, return_counts=True)
            edge = ep[cnt[inv.ravel()] == 1]
            return (edge[:, :1] + (edge[:, 1:] - edge[:, :1]) * np.linspace(0, 1, 9)[None, :, None]).reshape(-1, 3)

        def rim(V, F):
            """theta of the shell's open edge for 720 directions round the helmet (smoothed)."""
            ph, th = angles(edge_points(V, F))
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

        rim_d = rim(Vd, Fd)
        lift = raise_ / radius

        def front_weight(phi):  # 1 across the front (where the name is), 0 from the sides round to the back
            a = np.degrees(np.abs(phi))
            t_ = np.clip((self.FRONT[1] - a) / (self.FRONT[1] - self.FRONT[0]), 0, 1)
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

        def rim_loop(V, F):
            """The shell's open edge as one ordered loop of points (the longest, if there are several)."""
            _, wid = np.unique(np.round(V, 4), axis=0, return_inverse=True)
            wid = wid.ravel()
            pairs = np.array([[0, 1], [1, 2], [2, 0]])
            ek = np.sort(wid[F[:, pairs]].reshape(-1, 2), axis=1)
            uk, cnt = np.unique(ek, axis=0, return_counts=True)
            pos = np.zeros((wid.max() + 1, 3))
            pos[wid] = V
            nbr = {}
            for u, v in uk[cnt == 1]:
                nbr.setdefault(u, []).append(v)
                nbr.setdefault(v, []).append(u)
            loops, seen = [], set()
            for start in nbr:
                if start in seen:
                    continue
                loop, prev, cur = [start], None, start
                seen.add(start)
                while True:
                    nxt = [n for n in nbr[cur] if n != prev and (n not in seen or (n == start and len(loop) > 2))]
                    if not nxt or nxt[0] == start:
                        break
                    prev, cur = cur, nxt[0]
                    loop.append(cur)
                    seen.add(cur)
                loops.append(loop)
            loop = max(loops, key=len)
            return pos[loop]

        def flat_loop(P, raise_front):
            ph_, th_ = angles(P)
            if raise_front:
                th_ = th_ - lift * front_weight(ph_)
            return np.stack([th_ * np.cos(ph_), th_ * np.sin(ph_)], 1)

        def resample(L, n):
            L2 = np.r_[L, L[:1]]
            seg = np.linalg.norm(np.diff(L2, axis=0), axis=1)
            cum = np.r_[0, np.cumsum(seg)]
            t = np.linspace(0, cum[-1], n, endpoint=False)
            return np.stack([np.interp(t, cum, L2[:, 0]), np.interp(t, cum, L2[:, 1])], 1)

        def arrange(L):
            """Start the loop at the middle of the front and run it round towards +phi."""
            ph_ = np.arctan2(L[:, 1], L[:, 0])
            L = np.roll(L, -int(np.argmin(np.abs(ph_) + 10 * (np.hypot(L[:, 0], L[:, 1]) > 1.8))), axis=0)
            if np.arctan2(L[5, 1], L[5, 0]) < np.arctan2(L[0, 1], L[0, 0]):
                L = np.r_[L[:1], L[1:][::-1]]
            return L

        def anchors(L):
            """Matching points round the loop (indices): each corner of the face opening (where the edge comes
            closest to the top, between the front and the side) and, elsewhere, fixed directions, so away from
            the corners the pull is straight out (no sideways shift of the design)."""
            th_ = np.hypot(L[:, 0], L[:, 1])
            ph_ = np.degrees(np.unwrap(np.arctan2(L[:, 1], L[:, 0])))
            ph_ = ph_ - ph_[0]

            def at(deg):
                return int(np.argmax(ph_ >= deg))

            def notch(lo, hi):
                i_ = np.arange(at(lo), at(hi))
                return int(i_[np.argmin(th_[i_])])
            k = [0, at(20), notch(25, 75), at(80)]
            k += [at(d) for d in (100, 130, 160, 180, 200, 230, 260)]
            k += [at(280), notch(285, 335), at(340), len(L)]
            return k

        # The warp, drawn flat: the DCS rim (raised at the front) is laid onto the AC8 rim (INSET inside its edge)
        # by distance along them between matching points (the corners of the face opening, fixed directions
        # elsewhere), so the paint is pulled straight across to the DCS edge with no bunching. The pull is spread
        # inward and fades out before the top; the band along the rim (the name) moves as one piece.
        N = 2400
        bd = arrange(resample(flat_loop(rim_loop(Vd, Fd), True), N))
        contour = inside & ~(np.roll(inside, 1, 0) & np.roll(inside, -1, 0)
                             & np.roll(inside, 1, 1) & np.roll(inside, -1, 1))
        cy, cx = np.nonzero(contour)
        ba = np.stack([cx, cy], 1) / scale - lim
        la = flat_loop(rim_loop(Va, Fa), False)
        for s0 in range(0, len(la), 512):  # onto the inset edge
            d2 = ((la[s0:s0 + 512, None] - ba[None]) ** 2).sum(2)
            la[s0:s0 + 512] = ba[np.argmin(d2, 1)]
        la = arrange(resample(la, N))
        kd, ka = anchors(bd), anchors(la)
        src = np.interp(np.arange(N), kd, ka)  # position along the AC8 loop for each DCS loop point
        i0 = np.floor(src).astype(int) % N
        fr = (src - np.floor(src))[:, None]
        disp_front = la[i0] * (1 - fr) + la[(i0 + 1) % N] * fr - bd
        # Away from the front the design stays as lined up and is only ever stretched OUT: where the DCS edge
        # reaches past the AC8 one, it reads the nearest bit of the AC8 edge (evened out along the rim);
        # where the DCS edge is inside the AC8 one, nothing moves (the extra AC8 paint is simply cut off, never
        # squeezed in).
        gxb = np.clip(np.round((bd[:, 0] + lim) * scale).astype(int), 0, self.GRID - 1)
        gyb = np.clip(np.round((bd[:, 1] + lim) * scale).astype(int), 0, self.GRID - 1)
        out_ = ~inside[gyb, gxb]
        disp_side = np.zeros_like(bd)
        for s0 in range(0, N, 512):
            d2 = ((bd[s0:s0 + 512, None] - ba[None]) ** 2).sum(2)
            disp_side[s0:s0 + 512] = ba[np.argmin(d2, 1)] - bd[s0:s0 + 512]
        disp_side[~out_] = 0
        # even out along the rim (circular), keeping the ends of each overhang easing in from zero
        sig_ = max(N * self.ALONG / (2 * np.pi), 1.0)
        kk_ = np.exp(-0.5 * (np.arange(-int(3 * sig_), int(3 * sig_) + 1) / sig_) ** 2)
        kk_ /= kk_.sum()
        pw = len(kk_) // 2
        disp_side = np.stack([np.convolve(np.r_[disp_side[-pw:, c], disp_side[:, c], disp_side[:pw, c]], kk_,
                                          mode="valid") for c in (0, 1)], 1)
        wf_ = front_weight(np.arctan2(bd[:, 1], bd[:, 0]))[:, None]
        disp = wf_ * disp_front + (1 - wf_) * disp_side * self.SIDE_WARP
        G = 384
        gx_, gy_ = np.meshgrid(np.linspace(-lim, lim, G), np.linspace(-lim, lim, G))
        cells = np.stack([gx_.ravel(), gy_.ravel()], 1)
        D = np.zeros_like(cells)
        dmin = np.zeros(len(cells))
        for s0 in range(0, len(cells), 2048):
            d2 = ((cells[s0:s0 + 2048, None] - bd[None]) ** 2).sum(2)
            w_ = 1 / (d2 + 0.01 ** 2) ** self.SPREAD
            D[s0:s0 + 2048] = (w_ @ disp) / w_.sum(1, keepdims=True)
            dmin[s0:s0 + 2048] = np.sqrt(d2.min(1))
        c_th = np.hypot(cells[:, 0], cells[:, 1])
        c_ph = np.arctan2(cells[:, 1], cells[:, 0])
        outside = c_th >= rim_at(rim_d, c_ph) - lift * front_weight(c_ph)
        fade = np.clip(1 - (dmin - self.FADE[0]) / self.FADE[1], 0, 1)  # full by the rim, none towards the top
        fade = np.where(outside, 1.0, fade * fade * (3 - 2 * fade))
        D = (D * fade[:, None]).reshape(G, G, 2)
        gscale = (G - 1) / (2 * lim)

        log("Helmet: finding the DCS shell's texels…")
        pix, tri_ds, bar_ds, depths = [], [], [], []
        pad_px = self.PAD * W / 4096
        for f, (t0, t1, t2) in enumerate(FTd):
            uv = np.array([Td[t0], Td[t1], Td[t2]]) * [W, -H] + [0, H]  # pixels (v up -> rows down)
            x0, y0 = np.floor(uv.min(0)).astype(int) - pad_px
            x1, y1 = np.ceil(uv.max(0)).astype(int) + pad_px
            xs, ys = np.meshgrid(np.arange(max(x0, 0), min(x1 + 1, W)), np.arange(max(y0, 0), min(y1 + 1, H)))
            P = np.stack([xs.ravel() + 0.5, ys.ravel() + 0.5], 1)
            a, b, c = uv
            den = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
            if abs(den) < 1e-12 or not len(P):
                continue
            l0 = ((b[1] - c[1]) * (P[:, 0] - c[0]) + (c[0] - b[0]) * (P[:, 1] - c[1])) / den
            l1 = ((c[1] - a[1]) * (P[:, 0] - c[0]) + (a[0] - c[0]) * (P[:, 1] - c[1])) / den
            l2 = 1 - l0 - l1
            # distance (pixels) inside the triangle: the texels a few pixels past the shell's UV edge are mapped
            # too (carried on smoothly from the triangle), so mipmaps and filtering never pull in other colours
            area2 = abs(den)
            hs = [area2 / max(np.linalg.norm(b - c), 1e-9), area2 / max(np.linalg.norm(c - a), 1e-9),
                  area2 / max(np.linalg.norm(a - b), 1e-9)]
            depth = np.minimum(np.minimum(l0 * hs[0], l1 * hs[1]), l2 * hs[2])
            ok = depth >= -pad_px
            if ok.any():
                pix.append(P[ok].astype(np.int32))
                tri_ds.append(np.full(ok.sum(), f, np.int32))
                bar_ds.append(np.stack([l0[ok], l1[ok], l2[ok]], 1).astype(np.float32))
                depths.append(depth[ok].astype(np.float32))
        pix, tri_d_all, bar_d_all = np.concatenate(pix), np.concatenate(tri_ds), np.concatenate(bar_ds)
        depth_all = np.concatenate(depths)
        # each texel once, from the triangle it is deepest inside
        key = pix[:, 1].astype(np.int64) * W + pix[:, 0]
        order = np.lexsort((-depth_all, key))
        first = np.r_[True, key[order][1:] != key[order][:-1]]
        sel = order[first]
        pix, tri_d_all, bar_d_all = pix[sel], tri_d_all[sel], bar_d_all[sel]
        self.core = depth_all[sel] >= -1e-3   # on the shell itself (the rest is the padding round it)

        log(f"Helmet: matching {len(pix):,} texels to the AC8 helmet…")
        n_all = len(pix)

        # 1. where each DCS texel lands on the flattened AC8 shell: as lined up, plus the warp
        qx_all = np.zeros(n_all)
        qy_all = np.zeros(n_all)
        keep_all = np.zeros(n_all, bool)
        for s0 in range(0, n_all, 500000):
            sl = slice(s0, s0 + 500000)
            pts = (bar_d_all[sl, :, None].astype(np.float64) * Vd[Fd[tri_d_all[sl]]]).sum(1)
            phi, th = angles(pts)
            # At the front the design is raised (the name clears the brow pad): rather than running off the
            # AC8 edge below the raised rim, the thin strip under the name is stretched down to the DCS edge.
            # Depth from the DCS rim d -> depth from the raised rim smax(k d, d - L) (a smooth corner just
            # under the name; the rest of the shell only moves up by L).
            L_ = lift * front_weight(phi)
            rdt = rim_at(rim_d, phi)
            dd = rdt - th
            ga = self.UNDER / radius
            k_ = ga / (L_ + ga)
            w_ = (0.04 / radius) * L_ / max(lift, 1e-9)
            a_, b_ = k_ * dd, dd - L_
            f_ = (a_ + b_ + np.sqrt((a_ - b_) ** 2 + w_ ** 2)) / 2
            th = (rdt - L_) - f_
            px, py = th * np.cos(phi), th * np.sin(phi)
            cxg = np.clip((px + lim) * gscale, 0, G - 1.001)
            cyg = np.clip((py + lim) * gscale, 0, G - 1.001)
            i0, j0 = cxg.astype(int), cyg.astype(int)
            ax, ay = (cxg - i0)[:, None], (cyg - j0)[:, None]
            dv = (D[j0, i0] * (1 - ax) * (1 - ay) + D[j0, i0 + 1] * ax * (1 - ay)
                  + D[j0 + 1, i0] * (1 - ax) * ay + D[j0 + 1, i0 + 1] * ax * ay)
            qx_all[sl], qy_all[sl] = px + dv[:, 0], py + dv[:, 1]
            keep_all[sl] = front_weight(phi) > 0.02

        # 2. where the DCS shell reaches past the AC8 one (away from the front), each row of the DCS texture is
        # stretched sideways out to the shell's end: the row keeps its own paint, so nothing shifts up or down.
        # The gap at each row's end is evened out from row to row (no steps), and the stretch eases in over a
        # band a few times wider than the gap.
        qa_ = np.arctan2(qy_all, qx_all)
        rq = np.hypot(qx_all, qy_all)
        ok_all = rq <= ray_at(qa_) - 0.006
        ROWQ = {}
        ys_u = pix[:, 1]
        order_ = np.lexsort((pix[:, 0], ys_u))
        ys_o = ys_u[order_]
        starts = np.r_[0, np.nonzero(np.diff(ys_o))[0] + 1]
        ends = np.r_[starts[1:], len(order_)]
        rows = ys_o[starts]
        gl = np.zeros(len(rows))
        gr = np.zeros(len(rows))
        for i_, (a0, a1) in enumerate(zip(starts, ends)):
            idx = order_[a0:a1]
            xs_ = pix[idx, 0]
            ok_ = ok_all[idx]
            kp_ = keep_all[idx]
            ROWQ[i_] = idx
            if ok_.all() or not ok_.any():
                continue
            # the row's first and last stretch of shell (its ends); a gap only counts if the end isn't the front
            br = np.nonzero(np.diff(xs_) > 1)[0]
            l_end = br[0] + 1 if len(br) else len(xs_)
            r_start = br[-1] + 1 if len(br) else 0
            okl = np.nonzero(ok_[:l_end])[0]
            if len(okl) and okl[0] > 0 and not kp_[:okl[0]].any():
                gl[i_] = okl[0]
            okr = np.nonzero(ok_[r_start:])[0]
            if len(okr) and r_start + okr[-1] < len(xs_) - 1 and not kp_[r_start + okr[-1] + 1:].any():
                gr[i_] = len(xs_) - 1 - (r_start + okr[-1])

        def even(g_):
            wn_ = max(int(0.004 * H), 2)
            ext_ = np.r_[np.zeros(wn_), g_, np.zeros(wn_)]
            mx_ = np.max(np.stack([ext_[i:i + len(g_)] for i in range(2 * wn_ + 1)]), 0)
            k2 = np.exp(-0.5 * (np.arange(-2 * wn_, 2 * wn_ + 1) / wn_) ** 2)
            return np.convolve(np.r_[np.zeros(2 * wn_), mx_, np.zeros(2 * wn_)], k2 / k2.sum(), mode="valid")
        gl, gr = even(gl), even(gr)
        band0 = self.ROWBAND * W / 4096
        for i_ in np.nonzero((gl > 0.05) | (gr > 0.05))[0]:
            idx = ROWQ[i_]
            n_ = len(idx)
            if n_ < 8:
                continue
            xs_ = np.arange(n_, dtype=np.float64)
            src = xs_.copy()
            for g_, side in ((gl[i_], -1), (gr[i_], 1)):
                if g_ <= 0.05:
                    continue
                B = min(max(3 * g_, band0), n_ / 2)
                t = xs_ if side < 0 else (n_ - 1 - xs_)       # distance in from this end
                m_ = t < B + g_
                u_ = np.clip(t[m_] / (B + g_), 0, 1)
                # t -> g + B*h(u), h eases from slope (B+g)/B... smooth: reads further in near the end
                new_t = t[m_] + g_ * (1 - u_) ** 2
                src[m_] = new_t if side < 0 else (n_ - 1 - new_t)
            qx_r, qy_r = qx_all[idx].copy(), qy_all[idx].copy()
            qx_all[idx] = np.interp(src, xs_, qx_r)
            qy_all[idx] = np.interp(src, xs_, qy_r)

        # 3. read the AC8 UV (and triangle) there
        uv_a = np.zeros((n_all, 2), np.float32)
        tri_a_all = np.zeros(n_all, np.int32)
        bar_a_all = np.zeros((n_all, 3), np.float32)
        for s0 in range(0, n_all, 500000):
            sl = slice(s0, s0 + 500000)
            qx, qy = qx_all[sl], qy_all[sl]
            qph = np.arctan2(qy, qx)
            # never off the AC8 shell: anything that would run past its edge is eased in just inside it (a smooth
            # squeeze, no streaks from repeating the edge pixel)
            r_, R_ = np.hypot(qx, qy), ray_at(qph)
            e_ = self.SQUEEZE
            knee = R_ - e_
            qth = np.where(r_ < knee, r_, R_ - e_ * np.exp(-(r_ - knee) / e_))
            gx = (qth * np.cos(qph) + lim) * scale
            gy = (qth * np.sin(qph) + lim) * scale
            # the exact point on the AC8 triangle in this direction from the centre (the flat image only says
            # which triangle - reading UVs from it bends straight lines a little at every triangle edge). Near an
            # edge the image can name the neighbour, so the triangles a few pixels around are tried too.
            dvec = np.stack([np.sin(qth) * np.cos(qph), np.cos(qth), np.sin(qth) * np.sin(qph)], 1)
            best = np.full(len(dvec), -np.inf)
            tri = np.zeros(len(dvec), np.int64)
            bu = np.zeros(len(dvec))
            bv = np.zeros(len(dvec))
            ix, iy = np.round(gx).astype(int), np.round(gy).astype(int)
            for ox, oy in ((0, 0), (2, 0), (-2, 0), (0, 2), (0, -2), (2, 2), (-2, -2), (2, -2), (-2, 2)):
                t_ = fid_img[np.clip(iy + oy, 0, self.GRID - 1), np.clip(ix + ox, 0, self.GRID - 1)]
                t_ = np.where(t_ >= 0, t_, 0)
                A3, B3, C3 = Va[Fa[t_, 0]], Va[Fa[t_, 1]], Va[Fa[t_, 2]]
                e1, e2, so = B3 - A3, C3 - A3, ctr - A3
                pv = np.cross(dvec, e2)
                det = (e1 * pv).sum(1)
                det = np.where(np.abs(det) < 1e-12, 1e-12, det)
                u_ = (so * pv).sum(1) / det
                v_ = (dvec * np.cross(so, e1)).sum(1) / det
                score = np.minimum(np.minimum(u_, v_), 1 - u_ - v_)  # >= 0 inside the triangle
                take = score > best + 1e-9
                best = np.where(take, score, best)
                tri = np.where(take, t_, tri)
                bu = np.where(take, u_, bu)
                bv = np.where(take, v_, bv)
            tri_a_all[sl] = tri
            bb = np.clip(np.stack([1 - bu - bv, bu, bv], 1), 0, 1)
            bb /= np.maximum(bb.sum(1, keepdims=True), 1e-9)
            bar_a_all[sl] = bb
            uv_a[sl] = (bb[:, :, None] * Ta[FTa[tri]]).sum(1)
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
        img = np.asarray(Image.open(path).convert("RGBA"), dtype=np.float32)
        h, w = img.shape[:2]
        return _bilinear(img, self.uv_a[:, 0] * w - 0.5, (1 - self.uv_a[:, 1]) * h - 0.5)

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
        """AC8 _MRO (R metallic, G roughness, B AO) -> DCS Roughmet (R AO, G roughness, B metallic, A AO)."""
        mro = self._sample(ac8_mro)
        return self._paint(self._base(dcs_roughmet, 4), mro[:, [2, 1, 0, 2]])

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
           dcs_roughmet=None, fmt=None, raise_=HelmetMap.RAISE, log=print, name=None, aircraft="f18"):
    """The helmet converter. The DCS files (optional) keep everything but the shell; the results get their
    names (and format, unless fmt is given), or, with name, <name>, <name>_Roughmet and <name>_Normal.
    Returns the paths written."""
    out = Path(out)
    name = re.sub(r'[<>:"/\\|?*]', "_", (name or "").strip()).strip(" .")
    name = re.sub(r"\.(png|tga|dds|tif|tiff)$", "", name, flags=re.I)
    name = re.sub(r"_(roughmet|normal|nm)$", "", name, flags=re.I)
    size = next((max(Image.open(p).size) for p in (dcs_color, dcs_normal, dcs_roughmet) if p), 4096)
    label, data, default_stem = AIRCRAFT[aircraft]
    log(f"Helmet: AC8 → DCS {label} helmet")
    hm = HelmetMap(size, data=data, raise_=raise_, log=log)
    written = []
    for src, base, suffix, fn, kind in ((ac8_color, dcs_color, "", hm.color, "color"),
                                        (ac8_mro, dcs_roughmet, "_Roughmet", hm.roughmet, "roughmet"),
                                        (ac8_normal, dcs_normal, "_Normal", hm.normal, "normal")):
        if not src:
            continue
        stem = name + suffix if name else (Path(base).stem if base else default_stem + suffix)
        log(f"Helmet: {Path(src).name} -> {stem}")
        im = fn(src, base)
        f = fmt or (Path(base).suffix.lower().lstrip(".").replace("tiff", "tif") if base else "png")
        if f not in ("png", "tga", "dds"):
            f = "png"
        written.append(save_img(im, out / stem, f, kind))
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
    p = sub.add_parser("set", help="one texture set, its maps picked one by one")
    p.add_argument("--mode", choices=["ac8_pbr", "ac8_dcs", "pbr_dcs", "dcs_pbr"], required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--slot", action="append", default=[], help="ROLE=PATH")
    p.add_argument("--format", choices=["png", "tga", "dds"])
    p.add_argument("--normal", choices=["opengl", "directx"])
    p.add_argument("--orm", action="store_true")
    p.add_argument("--preset", default="separate")
    p = sub.add_parser("reformat")
    p.add_argument("folder")
    p.add_argument("--format", choices=["tga", "dds"], required=True)
    p = sub.add_parser("helmet")
    p.add_argument("--out", required=True)
    for k in ("ac8-color", "ac8-normal", "ac8-mro", "dcs-color", "dcs-normal", "dcs-roughmet"):
        p.add_argument("--" + k)
    p.add_argument("--format", choices=["png", "tga", "dds"])
    p.add_argument("--name", help="file name for the results (_Roughmet / _Normal are added)")
    p.add_argument("--aircraft", choices=sorted(AIRCRAFT), default="f18", help="which DCS helmet")
    p.add_argument("--raise", dest="raise_", default=str(HelmetMap.RAISE),
                   help="move the AC8 design up on the DCS helmet: FRONT[,BACK] (0 = as lined up)")
    a = ap.parse_args(argv)

    def log(msg):
        print(msg, flush=True)
    if a.cmd in ("to-dcs", "to-pbr"):
        return 0 if convert(a.cmd, a.src, a.out, a.format, a.normal, a.orm, log) else 1
    if a.cmd == "set":
        slots = dict(x.split("=", 1) for x in a.slot)
        return 0 if convert_set(a.mode, slots, a.out, a.name, a.format, a.normal, a.orm, a.preset, log) else 1
    if a.cmd == "reformat":
        reformat(a.folder, a.format, log)
        return 0
    return 0 if helmet(a.out, a.ac8_color, a.ac8_normal, a.ac8_mro, a.dcs_color, a.dcs_normal, a.dcs_roughmet,
                       a.format, float(a.raise_.split(",")[0]), log, a.name, a.aircraft) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
