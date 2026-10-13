"""Aircraft liveries: which textures, emblems and paint colours each livery uses.

    python ac8_livery.py <1_Raw_UAssets> <2_Final_Export>

Every aircraft has one material per livery: Materials/<id>/MI_<aircraft>_<id>_Decal. Each sets (or inherits from
its parent material) these values:
  Basecolor / Livemap / Normalmap   the livery's colour, metal-rough and normal textures (UDIM tiles)
  <Slot>Left/RightDecalMap          emblems: TailLeft, WingLeft, WingBottomLeft, FrontBodyLeft, Add01Left …
  LayerMaskMap + Layer01..03_Color  custom paint: the mask's R/G/B areas painted in those colours

The emblem slots are places on the aircraft's second UV set (see DECAL_SLOTS), the same on every aircraft.
Writes 2_Final_Export/Liveries/<aircraft>.json for the 3D viewer, with each texture resolved to its PNG(s).
"""
import json
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ac8_package import Package  # noqa: E402

# decal slot -> UV1 tile row; Left slots use tile column -1, Right slots column 0
DECAL_SLOTS = {"Tail": 0, "Wing": -1, "WingBottom": -2, "FrontBody": -3, "Add01": -4, "Add02": 1, "Add03": 2}
SLOT_RE = re.compile(r"^(?P<slot>[A-Za-z0-9]+?)(?P<side>Left|Right)DecalMap$")
BLANK_TEXTURES = ("Default_000_D", "T_Black01_M")  # "no emblem" placeholders
NUMBER_FONT = "T_Aircraft_Number"   # the digit sheet the numbers are drawn from
NUMBER_FONT_PATH = "Live/Content/Vehicles/Aircraft/Common/Emblem/Number/T_Aircraft_Number.uasset"
FORMAT = 5  # 5: Tail_Color_Left/Right read as colours; 4: number placement from the decoded game shader; livery JSON version (2: game defaults, emblem placement, number scale/fonts; 3: number placement)
# number areas -> UV1 tile row (same slot system as the emblems)
NUMBER_AREAS = {"Tail": 0, "Body": 1, "Wing": 2}
# <Slot>DecalAreaIndex -> UV1 tile row it draws on (the game's defaults: Tail 0, WingBottom 1, Wing 2, FrontBody 3)
AREA_ROWS = {k: -k for k in range(8)}  # the game's shader adds (0, AreaIndex) to UV1: area k draws on tile row -k

# The parent of every aircraft decal material (MP_Aircraft_Decal) and its default values, as stored in the game
# (read from the uniform-expression defaults inside MP_<aircraft>_Decal). Used when a livery doesn't set a value
# and the aircraft's own files can't be read for them.
MASTER_DEFAULTS = {
    "Tail_Color_Left": ("vec", [0.6795, 0.0, 0.0356, 1.0]), "Tail_Color_Right": ("vec", [0.6795, 0.0, 0.0356, 1.0]),
    "Body_Color": ("vec", [0.3333, 0.3333, 0.3333, 1.0]), "Wing_Color": ("vec", [0.3333, 0.3333, 0.3333, 1.0]),
    "Tail_Edge_Color": ("vec", [0.9531, 0.9531, 0.9531, 1.0]), "Body_Edge_Color": ("vec", [0.9531, 0.9531, 0.9531, 1.0]),
    "Wing_Edge_Color": ("vec", [0.9531, 0.9531, 0.9531, 1.0]),
    "Number_Interval": ("f", -0.04),
    **{f"{a}_{k}": ("f", v) for a in ("Tail", "Body", "Wing") for k, v in
       (("offsetX_L", 0.3), ("offsetX_R", 0.3), ("offsetY", 0.5), ("scale", 1.0), ("Font_Select", 0.0),
        ("SlantDegree", 0.0), ("Edge_Switch", 0.0))},
    **{f"{sl}Decal{k}": ("f", v) for sl, area in (("Tail", 0), ("WingBottom", 1), ("Wing", 2), ("FrontBody", 3),
                                                  ("Add01", 0), ("Add02", 0), ("Add03", 0))
       for k, v in (("AreaIndex", float(area)), ("Scale", 1.0), ("TranslateX", 0.0), ("TranslateY", 0.0),
                    ("FlipRight", 0.0))},
}


GAME_ORANGE = [0.6795, 0.0, 0.0356]          # the master material's default tail-number colour (linear)
ORANGE_SHOWN = [(c / 255.0) ** 2.2 for c in (230, 67, 45)]   # how that orange actually looks in the game


def shown_color(c):
    """Colours as the viewer draws them: the game's default tail orange is drawn as it looks in the game
    (sRGB 230, 67, 45); every other colour is the game's value as is."""
    c = [float(x) for x in c[:3]]
    if all(abs(x - y) < 0.01 for x, y in zip(c, GAME_ORANGE)):
        return [round(x, 4) for x in ORANGE_SHOWN]
    return c


def numbers_of(scalars, colors):
    """The aircraft number ("203") and how it's drawn, from a livery's merged parameters (all game values; the
    viewer draws them with the game's own number shader maths, see ac8_glview.number_spec)."""
    def f(name, default=0.0):
        return float(scalars.get(name, default))

    digits = "".join(str(int(f(f"Number0{i}_Select")) % 10) for i in (1, 2, 3))
    two = f("NumberCountIsTwo") >= 0.5
    areas = {}
    for area, row in NUMBER_AREAS.items():
        scale = f(f"{area}_scale", 1.0)
        a = {"row": row, "font": int(f(f"{area}_Font_Select")), "show": scale > 0.01, "scale": scale,
             "x_left": f(f"{area}_offsetX_L"), "x_right": f(f"{area}_offsetX_R"), "y_up": f(f"{area}_offsetY"),
             "slant": f(f"{area}_SlantDegree"),
             "edge": f(f"{area}_Edge_Switch") >= 0.5,
             "edge_color": (colors.get(f"{area}_Edge_Color") or [0.95, 0.95, 0.95, 1])[:3],
             "game": {"offsetX_L": f(f"{area}_offsetX_L"), "offsetX_R": f(f"{area}_offsetX_R"),
                      "offsetY": f(f"{area}_offsetY"), "scale": scale}}
        if area == "Tail":
            a["color_L"] = shown_color((colors.get("Tail_Color_Left") or GAME_ORANGE)[:3])
            a["color_R"] = shown_color((colors.get("Tail_Color_Right") or colors.get("Tail_Color_Left")
                                        or GAME_ORANGE)[:3])
        else:
            a["color_L"] = a["color_R"] = (colors.get(f"{area}_Color") or [0.333, 0.333, 0.333, 1])[:3]
        areas[area] = a
    return {"digits": digits[1:] if two else digits, "interval": f("Number_Interval", -0.04), "enabled": True,
            "areas": areas}


def decal_place(slot, scalars):
    """Where a slot's emblem goes, from the game's values: <Slot>DecalScale (the emblem is DecalScale / 5 of its
    area across),
    <Slot>DecalTranslateX/Y (moves it; mirrored on the right side), <Slot>DecalAreaIndex (which area of the aircraft
    it's drawn on — e.g. Joker's wing emblem sits in the Wing slot but is drawn on the tail) and <Slot>DecalFlipRight
    (mirror the emblem on the right side)."""
    def f(k, dflt):
        return float(scalars.get(f"{slot}Decal{k}", dflt))
    area = int(round(f("AreaIndex", MASTER_DEFAULTS.get(f"{slot}DecalAreaIndex", ("f", 0))[1])))
    return {"scale": f("Scale", 1.0) or 1.0, "tx": f("TranslateX", 0.0), "ty": f("TranslateY", 0.0),
            "flip": f("FlipRight", 0.0) >= 0.5, "area": area, "area_row": AREA_ROWS.get(area, -area)}


def upgrade(data):
    """Bring a livery file made by an older version up to date without the unpacked game files: rebuild the
    numbers and emblem placement from the raw values it stored, with the shared parent material's defaults
    filling in what a livery doesn't set. Older versions also misread a 0 as noise; impossible values are
    dropped so the default applies."""
    for lv in data.get("liveries", []):
        sc = {k: v[1] for k, v in MASTER_DEFAULTS.items() if v[0] == "f"}
        co = {k: v[1] for k, v in MASTER_DEFAULTS.items() if v[0] == "vec"}
        if any(is_color(k) for k in (lv.get("scalars") or {})):  # colours older versions read as numbers
            data["from_old_version"] = True
        for k, v in (lv.get("scalars") or {}).items():
            if is_color(k):
                continue
            if k.endswith(("AreaIndex", "_Select")) and (v != int(v) or not 0 <= v <= 9):
                v = 0.0  # older versions read a stored 0 as noise: what's there is a 0
            sc[k] = v
        co.update(lv.get("colors") or {})
        lv["scalars"], lv["colors"] = sc, co
        lv["numbers"] = numbers_of(sc, co)
        for key, d in (lv.get("decals") or {}).items():
            d.update(decal_place(re.sub(r"(Left|Right)$", "", key), sc))
    data["format"] = FORMAT
    # values older versions misread can't be recovered from the file (2.7 replaced them with defaults): the
    # viewer offers to extract the aircraft again
    if "custom" not in data:  # files the extraction writes always have it; ones from before 2.6 don't
        data["from_old_version"] = True
    return data


def is_color(name):
    """Vector (colour) parameters: Body_Color, Tail_Color_Left/Right, Tail_Edge_Color, Layer01_Color …
    (not the *_Color_Weight / *_ColorFade_Weight scalars)."""
    return "Color" in name and not name.endswith("Weight")


def read_params(path):
    """Material instance -> ({name: value}, parent import path or None). Values: ('tex', name) / ('vec', rgba) /
    ('f', float). Reads the parameter arrays by their fixed layout: FName, association byte, index -1, value, guid."""
    p = Package(path)
    data, _ = p.export_data(p.exports[0])
    names, nn, nimp = p.names, len(p.names), len(p.imports)
    out = {}
    pos = 0
    while pos < len(data) - 13:
        ni, num, assoc, idx = struct.unpack_from("<iiBi", data, pos)
        if 0 <= ni < nn and num == 0 and assoc in (0, 1, 2) and idx == -1 and not names[ni].startswith("/"):
            name, v = names[ni], pos + 13
            # a value of 0 isn't written: the entry's header then has its "has zeroes" bit and a mask (bit 1 =
            # the value) — read it, so a 0 is a 0 and not the bytes of the GUID that follows
            if pos >= 5 and data[pos - 5:pos - 3] == b"\x80\x07" and data[pos - 3] & 0x02:
                if is_color(name):
                    out.setdefault(name, ("vec", [0.0, 0.0, 0.0, 0.0]))
                elif not name.endswith("Map"):
                    out.setdefault(name, ("f", 0.0))
                pos = v + (0 if data[pos - 3] & 0x04 else 16)
                continue
            ref = struct.unpack_from("<i", data, v)[0]
            if -nimp <= ref <= -1 and p.imports[-ref - 1]["class"].startswith("Texture"):
                out[name] = ("tex", p.imports[-ref - 1]["name"])
                pos = v + 4 + 16
                continue
            if is_color(name) and v + 16 <= len(data):
                rgba = struct.unpack_from("<4f", data, v)
                if all(-0.01 <= c <= 64 for c in rgba):
                    out[name] = ("vec", [round(c, 4) for c in rgba])
                    pos = v + 16 + 16
                    continue
            if v + 4 <= len(data) and not name.endswith("Map"):
                f = struct.unpack_from("<f", data, v)[0]
                # zero values aren't written at all (the GUID follows right away): what we read is then noise
                if not (1e-3 <= abs(f) < 1e4) or (name.endswith(("_Select", "AreaIndex")) and f != int(f)):
                    f = 0.0
                out.setdefault(name, ("f", round(f, 4)))
                pos = v + 4
                continue
        pos += 1
    parent = None
    for i, imp in enumerate(p.imports):
        if imp["class"] == "MaterialInstanceConstant" and not imp["name"].startswith("Default__"):
            ip = p.import_path(-i - 1)
            if ip and ip[0].startswith("/Game/"):
                parent = ip[0]
    return out, parent


def shader_map_defaults(path):
    """Default values of every number parameter of the master material, from the compiled shader map stored inside
    a material instance (MP_<aircraft>_Decal has one). -> {name: ('f', x) | ('vec', rgba)}, or {} if none.
    The map is a frozen memory image: a name list, the frozen bytes, then a table saying where each name goes.
    The parameter list (name, index -1, association, type 0 scalar / 1 vector, offset into the defaults) and the
    defaults blob are found through that table."""
    p = Package(path)
    d, _ = p.export_data(p.exports[0])
    tag = d.find(b"FMaterialShaderMapContent\0")
    if tag < 8:
        return {}
    q = tag - 8
    (count,) = struct.unpack_from("<i", d, q)
    if not 0 < count < 5000:
        return {}
    names, r = [], q + 4
    for _ in range(count):
        (n,) = struct.unpack_from("<i", d, r)
        if not 0 < n < 512:
            return {}
        names.append(d[r + 4:r + 3 + n].decode("latin-1"))
        r += 4 + n + 4
    best = None
    for o in range(r, min(r + 160, len(d) - 4)):  # the frozen image's size, then the image, then the patch table
        (size,) = struct.unpack_from("<i", d, o)
        if not 1000 < size < len(d) - o:
            continue
        tail = d[o + 4 + size:]
        for s0 in range(0, min(len(tail), 8192) - 12, 4):
            k, got = s0, []
            while k + 12 <= len(tail):
                i, num, npat = struct.unpack_from("<iii", tail, k)
                if not (0 <= i < count and num == 0 and 0 < npat < 500 and k + 12 + 4 * npat <= len(tail)):
                    break
                offs = struct.unpack_from(f"<{npat}I", tail, k + 12)
                if any(x >= size for x in offs):
                    break
                got.append((i, offs))
                k += 12 + 4 * npat
            # the real table runs to (nearly) the end of the data; other runs of small numbers don't
            if len(got) > 20 and len(tail) - k <= 64 and (best is None or len(got) > len(best[2])):
                best = (o + 4, size, got)
    if not best:
        return {}
    base, size, got = best
    frozen = d[base:base + size]
    recs = []
    for i, offs in got:
        for x in offs:
            if x + 28 <= size and frozen[x + 12:x + 16] == b"\xff\xff\xff\xff":
                assoc, typ, off = struct.unpack_from("<IIi", frozen, x + 16)
                if assoc <= 2 and typ in (0, 1) and 0 <= off < 1 << 16 and off % 4 == 0:
                    recs.append((names[i], typ, off))
    if not recs:
        return {}
    need = max(off + (16 if t else 4) for _, t, off in recs)
    cands = []
    for o in range(0, min(4096, size - 16), 8):  # the defaults array: (frozen pointer, count, max), count = bytes
        ptr, num, mx = struct.unpack_from("<qii", frozen, o)
        if ptr & 1 and num == mx and need <= num <= need + 64:
            t = o + (ptr >> 24)
            if 0 <= t and t + num <= size:
                cands.append((num - need, frozen[t:t + num]))
    if not cands:
        return {}
    blob = min(cands, key=lambda c: c[0])[1]
    out = {}
    for name, t, off in recs:
        if t:
            out[name] = ("vec", [round(c, 4) for c in struct.unpack_from("<4f", blob, off)])
        else:
            out[name] = ("f", round(struct.unpack_from("<f", blob, off)[0], 4))
    return out


def material_defaults(mats_dir, log=print):
    """The master material's defaults for one aircraft (from its MP_*_Decal), on top of the built-in copy."""
    out, best = dict(MASTER_DEFAULTS), {}
    # every compiled variant lists the parameters it uses; the fullest one has the number ones too
    for mp in sorted(Path(mats_dir).glob("Common/MP_*Decal.uasset")) + sorted(Path(mats_dir).glob("*/MI_*_Decal.uasset")):
        try:
            got = shader_map_defaults(mp)
        except Exception as e:
            log(f"  {mp.name}: couldn't read the material defaults ({e})")
            continue
        if len(got) > len(best):
            best = got
        if "Tail_Color_Left" in best:
            break
    out.update(best)
    return out


_COMPILED = {}


def compiled_params(raw, path, depth=0):
    """Names of the parameters the game's compiled shader for this material actually uses: the nearest material in
    the parent chain that carries its own compiled shader (a material whose switches differ from its parent's gets
    one). A livery whose shader has no Tail_scale etc. draws no numbers in the game. None if no shader is found."""
    if path is None or depth > 6 or not path.exists():
        return None
    key = str(path)
    if key not in _COMPILED:
        try:
            _COMPILED[key] = set(shader_map_defaults(path))
        except Exception:
            _COMPILED[key] = set()
    if _COMPILED[key]:
        return _COMPILED[key]
    _, parent = read_params(path)
    if not parent:
        return None
    return compiled_params(raw, raw / "Live" / "Content" / (parent[len("/Game/"):] + ".uasset"), depth + 1)


def resolve(raw, path, depth=0):
    """Parameters of a material instance merged over its parents' (child values win)."""
    if path is None or depth > 6 or not path.exists():
        return {}
    params, parent = read_params(path)
    merged = {}
    if parent:
        merged.update(resolve(raw, raw / "Live" / "Content" / (parent[len("/Game/"):] + ".uasset"), depth + 1))
    merged.update(params)
    return merged


def png_index(final):
    """Texture name -> {'1001': relpath, …} or {'': relpath} for every PNG under 2_Final_Export/Textures."""
    idx = {}
    tex = Path(final) / "Textures"
    for f in tex.rglob("*.png"):
        m = re.match(r"^(.*)\.(10\d\d)$", f.stem)
        name, tile = (m.group(1), m.group(2)) if m else (f.stem, "")
        idx.setdefault(name, {})[tile] = str(f.relative_to(final)).replace("\\", "/")
    return idx


def build(raw, final, log=print):
    raw, final = Path(raw), Path(final)
    pngs = png_index(final)
    written = 0
    for mats in sorted(raw.rglob("Materials")):
        decals = sorted(mats.glob("*/MI_*_Decal.uasset"))
        if not decals or not mats.is_dir():
            continue
        folder = mats.parent.name
        liveries, missing = [], set()
        defaults = material_defaults(mats, log)
        for mi in decals:
            lid = mi.parent.name
            try:
                params = dict(defaults)
                params.update(resolve(raw, mi))
            except Exception as e:
                log(f"  livery {lid}: {e}")
                continue
            entry = {"id": lid, "material": mi.stem, "textures": {}, "decals": {}, "colors": {}, "scalars": {}}
            for name, (kind, val) in sorted(params.items()):
                if kind == "tex":
                    if val in BLANK_TEXTURES:
                        continue
                    m = SLOT_RE.match(name)
                    key = (m.group("slot"), m.group("side")) if m else None
                    files = pngs.get(val)
                    if not files:
                        missing.add(val)
                    if key:
                        entry["decals"][f"{key[0]}{key[1]}"] = {"texture": val, "files": files or {},
                                                                "row": DECAL_SLOTS.get(key[0]),
                                                                "col": -1 if key[1] == "Left" else 0}
                    elif name in ("Basecolor", "Livemap", "Normalmap", "LayerMaskMap"):
                        entry["textures"][name] = {"texture": val, "files": files or {}}
                elif kind == "vec":
                    entry["colors"][name] = val
                else:
                    entry["scalars"][name] = val
            entry["numbers"] = numbers_of(entry["scalars"], entry["colors"])
            used = compiled_params(raw, mi)
            if used is not None and "Tail_scale" not in used:  # this livery's shader has no numbers
                entry["numbers"]["enabled"] = False
                for a in entry["numbers"]["areas"].values():
                    a["show"] = False
            for key, d in entry["decals"].items():
                d.update(decal_place(re.sub(r"(Left|Right)$", "", key), entry["scalars"]))
            liveries.append(entry)
        if not liveries:
            continue
        # in-game skins: the ones with a UI icon; their names from the skin tables + language file
        code = aircraft_code(folder)
        try:
            import ac8_skins
            names, debug = ac8_skins.find_names(raw, code, log=log)
        except Exception as e:
            names, debug = {}, f"skin names: {e}"
        for lv in liveries:
            icon = pngs.get(f"T_UI_Icon_Skin_{code}_{lv['id']}", {})
            lv["icon"] = icon.get("") or (next(iter(icon.values())) if icon else None)
            lv["in_game"] = bool(icon) or lv["id"] in names
            if lv["id"] in names:
                lv["name"] = names[lv["id"]]
        out = final / "Liveries" / f"{folder}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        font = pngs.get(NUMBER_FONT, {})
        emblems = {name: files for name, files in pngs.items()
                   if any("/Emblem/" in rel for rel in files.values()) and name != NUMBER_FONT}
        custom = []
        try:  # keep liveries the user saved from the viewer
            custom = json.loads(out.read_text(encoding="utf-8")).get("custom", [])
        except (OSError, ValueError):
            pass
        out.write_text(json.dumps({"format": FORMAT, "aircraft": folder, "liveries": liveries,
                                   "missing": sorted(missing), "number_font": font, "emblems": emblems,
                                   "custom": custom}, indent=1), encoding="utf-8")
        written += 1
        (final / "Liveries" / f"{folder}_skin_names.txt").write_text(debug + "\n", encoding="utf-8")
        n_dec = sum(1 for lv in liveries if lv["decals"])
        n_game = sum(1 for lv in liveries if lv.get("in_game"))
        log(f"  Liveries: {folder}: {len(liveries)} liveries ({n_game} in-game skins, {len(names)} named, "
            f"{n_dec} with emblems)"
            + (f" — {len(missing)} texture(s) not extracted" if missing else ""))
    return written


def aircraft_code(folder):
    """PP0023_f14d -> f14d (the code the skin icons and tables use)."""
    parts = folder.split("_")
    return parts[1].lower() if len(parts) > 1 else folder.lower()


def skin_imports(raw):
    """What the skin names and icons need: the skin tables and each extracted aircraft's skin icons."""
    import ac8_skins
    raw = Path(raw)
    need = [t for t in ac8_skins.SKIN_TABLES if not (raw / t).exists()]
    for mats in raw.rglob("Materials"):
        if mats.is_dir() and any(mats.glob("*/MI_*_Decal.uasset")):
            code = aircraft_code(mats.parent.name)
            if not any((raw / ac8_skins.ICON_PREFIX).parent.glob(f"T_UI_Icon_Skin_{code}_*.uasset")):
                need.append(ac8_skins.ICON_PREFIX + code + "_")
    return need


def emblem_imports(raw):
    """/Game/... texture paths that livery materials use but that weren't unpacked (emblems live in a shared
    folder, so ticking an aircraft doesn't bring them along)."""
    raw = Path(raw)
    need = set()
    decal_mats = list(raw.rglob("Materials/*/MI_*_Decal.uasset"))
    if decal_mats and not (raw / NUMBER_FONT_PATH).exists():
        need.add(NUMBER_FONT_PATH)
    for mi in decal_mats + list(raw.rglob("Materials/Common/MP_*Decal.uasset")):
        try:
            p = Package(mi)
        except Exception:
            continue
        for i, imp in enumerate(p.imports):
            if imp["class"] == "Texture2D":
                ip = p.import_path(-i - 1)
                if ip and ip[0].startswith("/Game/"):
                    rel = "Live/Content/" + ip[0][len("/Game/"):] + ".uasset"
                    if not (raw / rel).exists():
                        need.add(rel)
    return sorted(need)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    n = build(sys.argv[1], sys.argv[2])
    print(f"Liveries: {n} aircraft", flush=True)
