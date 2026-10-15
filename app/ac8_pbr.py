"""Turn Ace Combat 8's packed textures into standard PBR maps (+ AO) for Blender, Substance, Unreal, Unity or glTF.

Works on the PNGs the app exports (2_Final_Export/Textures). Textures are grouped into sets by name:
T_PP0023_f14d_001_C + _MRX + _NO -> set "T_PP0023_f14d_001"; UDIM tiles (.1001, .1002 …) are kept apart.

How AC8 packs its channels (checked on the F-14D):
    _C    base colour                       _N    normal (DirectX: green points down)
    _MRX  R metallic, G roughness, B unused _NO   R/G normal (DirectX), B ambient occlusion
    _MRO  R metallic, G roughness, B AO     _CE/_E emissive colour
    _MROS like _MRO (weapons, launchers)

DCS World follows the "PBR to DCS" Substance Painter export preset: <set>.png (base colour + alpha),
<set>_Roughmet.png (R ambient occlusion, G roughness, B metallic, A ambient occlusion again) and <set>_Normal.png (DirectX).

    python ac8_pbr.py <textures folder> <output folder> [--preset separate|unreal|unity_hdrp|unity_urp|gltf|dcs]
                      [--normal opengl|directx]
"""
import os
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image

PRESETS = {
    "separate": ("Separate maps (Blender / Substance)", "opengl"),
    "unreal": ("Unreal Engine (ORM)", "directx"),
    "unity_hdrp": ("Unity HDRP (Mask Map)", "opengl"),
    "unity_urp": ("Unity URP / Built-in (Metallic + Smoothness)", "opengl"),
    "gltf": ("glTF (ORM)", "opengl"),
    "dcs": ("DCS World (RoughMet)", "directx"),
}

ROLE_SUFFIX = {
    "base": {"C", "D", "BC", "CO", "BASECOLOR", "ALBEDO", "DIFFUSE", "COL", "COLOR"},
    "normal": {"N", "NM", "NRM", "NORMAL"},
    "normal_ao": {"NO"},
    "mr": {"MRX", "MR"},
    "mr_ao": {"MRO", "MRA", "MROS"},  # MROS: 4th channel (specular?) is ignored
    "orm": {"ORM", "ARM"},
    "emissive": {"CE", "E", "EM", "EMISSIVE"},
}
NAME_RE = re.compile(r"^(?P<base>.+)_(?P<suffix>[A-Za-z]+)(?:\.(?P<tile>\d{4}))?\.png$")


def role_of(suffix):
    s = suffix.upper()
    for role, names in ROLE_SUFFIX.items():
        if s in names:
            return role
    return None


def find_sets(folder):
    """{(set name, tile or None, subfolder): {role: path}} for every PNG in folder (recursive)."""
    sets = {}
    for r, _d, files in os.walk(folder):
        for f in files:
            m = NAME_RE.match(f)
            if not m:
                continue
            role = role_of(m["suffix"])
            if not role:
                continue
            key = (m["base"], m["tile"], str(Path(r).relative_to(folder)))
            sets.setdefault(key, {})[role] = Path(r) / f
    # only sets that have more than a lone emissive/whatever: need colour or normal or MR
    return {k: v for k, v in sets.items() if {"base", "normal", "normal_ao", "mr", "mr_ao", "orm"} & set(v)}


def _load(path, size=None):
    img = Image.open(path)
    img = img.convert("RGBA")
    if size and img.size != size:
        img = img.resize(size, Image.LANCZOS)
    return np.asarray(img).astype(np.float32) / 255.0


def _save(arr, path, mode):
    a = (np.clip(arr, 0, 1) * 255 + 0.5).astype(np.uint8)
    if mode == "L":
        Image.fromarray(a, "L").save(path)
    else:
        Image.fromarray(a, mode).save(path)
    return path


def unpack_set(roles, size=None, base=None):
    """-> dict of float arrays: base (RGB[A]), normal_dx (RGB, unit), metallic, roughness, ao, emissive.
    `size` (w, h) resizes every map to it; `base` = a float RGBA array used as the colour map instead of the file
    (a baked livery)."""
    if size is None and base is not None:
        size = (base.shape[1], base.shape[0])
    for r in ("base", "mr", "mr_ao", "orm", "normal", "normal_ao"):
        if size is None and r in roles:
            size = Image.open(roles[r]).size
            break
    out = {"size": size}
    if base is not None:
        out["base"] = base
    elif "base" in roles:
        out["base"] = _load(roles["base"], size)
    nsrc = roles.get("normal_ao") or roles.get("normal")
    if nsrc:
        n = _load(nsrc, size)
        x, y = n[..., 0] * 2 - 1, n[..., 1] * 2 - 1
        z = np.sqrt(np.clip(1 - x * x - y * y, 0, 1))  # blue holds AO in _NO, so always rebuild Z
        out["normal_dx"] = np.stack([x, y, z], -1)
        if "normal_ao" in roles:
            out["ao"] = n[..., 2]
    for r in ("mr_ao", "mr"):
        if r in roles:
            m = _load(roles[r], size)
            out["metallic"], out["roughness"] = m[..., 0], m[..., 1]
            if r == "mr_ao" and "ao" not in out:
                out["ao"] = m[..., 2]
            break
    else:
        if "orm" in roles:
            m = _load(roles["orm"], size)
            out["ao"], out["roughness"], out["metallic"] = m[..., 0], m[..., 1], m[..., 2]
    if "emissive" in roles:
        out["emissive"] = _load(roles["emissive"], size)[..., :3]
    h, w = (size[1], size[0]) if size else (1, 1)
    out.setdefault("metallic", np.zeros((h, w), np.float32))
    out.setdefault("roughness", np.full((h, w), 0.5, np.float32))
    out.setdefault("ao", np.ones((h, w), np.float32))
    return out


def normal_image(n_dx, convention):
    n = n_dx.copy()
    if convention == "opengl":
        n[..., 1] = -n[..., 1]
    return n * 0.5 + 0.5


def write_set(maps, out_dir, name, preset, normal_conv=None, tile=None):
    """Write one texture set in the chosen layout. Returns the list of files written."""
    _label, default_conv = PRESETS[preset]
    conv = normal_conv or default_conv
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    tag = "_OpenGL" if conv == "opengl" else "_DirectX"
    m, r, ao = maps["metallic"], maps["roughness"], maps["ao"]

    def p(suffix):  # UDIM tools want the tile number last: name_Map.1001.png
        return out_dir / (f"{name}_{suffix}.{tile}.png" if tile else f"{name}_{suffix}.png")

    if preset == "dcs":  # names and channels of the "PBR to DCS" Substance Painter preset
        def q(suffix):
            return out_dir / (f"{name}{suffix}.{tile}.png" if tile else f"{name}{suffix}.png")
        if "base" in maps:
            written.append(_save(maps["base"], q(""), "RGBA"))
        written.append(_save(np.stack([ao, r, m, ao], -1), q("_Roughmet"), "RGBA"))  # DCS: AO in alpha too
        if "normal_dx" in maps:
            written.append(_save(normal_image(maps["normal_dx"], conv), q("_Normal"), "RGB"))
        if "emissive" in maps:
            written.append(_save(maps["emissive"], q("_Emissive"), "RGB"))
        return written
    if "base" in maps:
        b = maps["base"]
        has_alpha = b[..., 3].min() < 0.98
        written.append(_save(b if has_alpha else b[..., :3], p("BaseColor"), "RGBA" if has_alpha else "RGB"))
    if "normal_dx" in maps:
        written.append(_save(normal_image(maps["normal_dx"], conv), p("Normal" + tag), "RGB"))
    if "emissive" in maps:
        written.append(_save(maps["emissive"], p("Emissive"), "RGB"))
    if preset == "separate":
        written += [_save(m, p("Metallic"), "L"), _save(r, p("Roughness"), "L"), _save(ao, p("AO"), "L")]
    elif preset in ("unreal", "gltf"):
        written.append(_save(np.stack([ao, r, m], -1), p("ORM"), "RGB"))
    elif preset == "unity_hdrp":
        detail = np.zeros_like(m)
        written.append(_save(np.stack([m, ao, detail, 1 - r], -1), p("MaskMap"), "RGBA"))
    elif preset == "unity_urp":
        zero = np.zeros_like(m)
        written.append(_save(np.stack([m, zero, zero, 1 - r], -1), p("MetallicSmoothness"), "RGBA"))
        written.append(_save(ao, p("AO"), "L"))
    return written


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ac8_textures import JOBS  # noqa: E402  (all the CPU cores)


def convert_folder(src, out, preset="separate", normal_conv=None, only=None, log=print, jobs=JOBS):
    """Convert every texture set found in `src`. `only` = optional set of set names to limit to."""
    src, out = Path(src), Path(out)
    sets = find_sets(src)
    if only:
        sets = {k: v for k, v in sets.items() if k[0] in only}
    log(f"PBR: {len(sets)} texture set(s) -> {PRESETS[preset][0]} ({jobs} at a time)")
    import threading
    from concurrent.futures import ThreadPoolExecutor
    lock = threading.Lock()
    state = {"n": 0, "done": 0}

    def one(item):
        (name, tile, sub), roles = item
        label = name + (f".{tile}" if tile else "")
        try:
            maps = unpack_set(roles)
            files = write_set(maps, out / sub, name, preset, normal_conv, tile)
            msg, k = f"  {label}: {'+'.join(sorted(roles))} -> {len(files)} maps", len(files)
        except Exception as e:
            msg, k = f"  {label}: ERROR {e}", 0
        with lock:
            state["n"] += k
            state["done"] += 1
            log(msg)
            print(f"\rPBR {state['done']}/{len(sets)}", end="", file=sys.stderr, flush=True)

    # numpy and PNG writing run outside Python's lock, so sets convert in parallel
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        list(ex.map(one, sorted(sets.items(), key=lambda kv: kv[0][:2])))
    n = state["n"]
    print(file=sys.stderr)
    log(f"PBR done: {n} maps written to {out}")
    return n


if __name__ == "__main__":
    args = list(sys.argv[1:])
    opts = {}
    for flag in ("--preset", "--normal", "--only"):
        if flag in args:
            i = args.index(flag)
            opts[flag] = args[i + 1]
            del args[i:i + 2]
    if len(args) != 2:
        sys.exit(__doc__)
    only = set(opts["--only"].split(",")) if "--only" in opts else None
    convert_folder(args[0], args[1], opts.get("--preset", "separate"), opts.get("--normal"), only)
