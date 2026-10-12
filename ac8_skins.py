"""In-game skin names for the liveries ("Joker 1", "Queen 1" …).

Which liveries are real, selectable skins: the game has a UI icon for each (UI/Texture/Icon/Skin/T_UI_Icon_Skin_
<aircraft code>_<livery id>). Their names come from the skin tables (Datatables/Information/DT_Skin,
Datatables/UI/DA_NarrativeSkinDataAsset): each row stores its name as a text key, and the words for that key are
in the game's language file (Localization/Game/<language>/Game.locres, inside the .pak files).

    python ac8_skins.py <1_Raw_UAssets> <aircraft code>      (prints what it finds)
"""
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ac8_package import Package  # noqa: E402

SKIN_TABLES = ["Live/Content/Datatables/Information/DT_Skin.uasset",
               "Live/Content/Datatables/UI/DA_NarrativeSkinDataAsset.uasset"]
ICON_PREFIX = "Live/Content/UI/Texture/Icon/Skin/T_UI_Icon_Skin_"
LOCRES_DIR = "Live/Content/Localization/Game/{lang}"
LOCRES_MAGIC = bytes.fromhex("0e147475674a03fc4a15909da0377f1b")  # FGuid 7574140E-FC034A67-9D90154A-1B7F37A0


# ---------------------------------------------------------------- language file
def _fstring(d, p):
    n = struct.unpack_from("<i", d, p)[0]
    p += 4
    if n == 0:
        return "", p
    if n < 0:
        raw = d[p:p - n * 2]
        return raw.decode("utf-16-le", "replace").rstrip("\0"), p - n * 2
    return d[p:p + n].decode("utf-8", "replace").rstrip("\0"), p + n


def read_locres(path):
    """Game.locres -> {(namespace, key): text}. Handles the legacy and the compact/optimized layouts."""
    d = Path(path).read_bytes()
    out = {}
    p = 0
    version = 0
    if d[:16] == LOCRES_MAGIC:
        version = d[16]
        p = 17
    if version == 0:  # legacy: strings inline
        (nns,) = struct.unpack_from("<I", d, p)
        p += 4
        for _ in range(nns):
            ns, p = _fstring(d, p)
            (nk,) = struct.unpack_from("<I", d, p)
            p += 4
            for _ in range(nk):
                key, p = _fstring(d, p)
                p += 4  # source hash
                text, p = _fstring(d, p)
                out[(ns, key)] = text
        return out
    (str_off,) = struct.unpack_from("<q", d, p)
    p += 8
    if version >= 2:
        p += 4  # entries count
    strings = []
    q = str_off
    (ns_count,) = struct.unpack_from("<i", d, q)
    q += 4
    for _ in range(ns_count):
        s, q = _fstring(d, q)
        if version >= 2:
            q += 4  # ref count
        strings.append(s)
    (nns,) = struct.unpack_from("<I", d, p)
    p += 4
    for _ in range(nns):
        if version >= 2:
            p += 4
        ns, p = _fstring(d, p)
        (nk,) = struct.unpack_from("<I", d, p)
        p += 4
        for _ in range(nk):
            if version >= 2:
                p += 4
            key, p = _fstring(d, p)
            _src_hash, idx = struct.unpack_from("<Ii", d, p)
            p += 8
            if 0 <= idx < len(strings):
                out[(ns, key)] = strings[idx]
    return out


# ---------------------------------------------------------------- skin tables
def _text_at(d, p):
    """An FText stored with its key at p (flags, history type 0, namespace, key, source) -> (ns, key, src, end)."""
    if p > len(d) - 17:
        return None
    flags, hist = struct.unpack_from("<iB", d, p)
    if not (0 <= flags < 0x400 and hist == 0):
        return None
    try:
        ns, q = _fstring(d, p + 5)
        key, q = _fstring(d, q)
        src, q = _fstring(d, q)
    except (struct.error, UnicodeDecodeError, ValueError):
        return None
    if not key or len(key) > 200 or len(ns) > 200 or len(src) > 1000 or q > len(d):
        return None
    if not (key.isprintable() and ns.isprintable() and (not src or src.isprintable())):
        return None
    return ns, key, src, q


def _texts(d):
    """Every FText stored with its key -> [(pos, ns, key, src)]. Where readings overlap, the one that starts later
    that has words in its source string wins (zeros next to a text can otherwise shift it by a field)."""
    out = []
    p = 0
    while p < len(d) - 17:
        c = _text_at(d, p)
        if c is None:
            p += 1
            continue
        def score(t):  # a real text has its words in the source string; the namespace is often set too
            return 2 * bool(t[2].strip()) + bool(t[0])

        for p2 in range(p + 1, min(c[3], p + 48)):
            c2 = _text_at(d, p2)
            if c2 and (score(c2), c2[3]) > (score(c), c[3] - 1):
                p, c = p2, c2
        out.append((p,) + c[:3])
        p = c[3]
    return out


def skin_rows(table_path, code):
    """Rows of a skin table that belong to this aircraft -> {livery id: [(namespace, key, source text)]}.
    A row is recognised by a name containing the aircraft code and a livery id (f14d_051, PP0023_f14d_051 …); the
    texts that follow it, up to the next such name, are that row's."""
    p = Package(table_path)
    rx = re.compile(rf"(?i)(?:^|[_\W]){re.escape(code)}[_\W]?(\d{{3}}[a-z0-9]*)$")
    marks = []
    for exp in p.exports:
        d, _ = p.export_data(exp)
        ids = {i: m.group(1) for i, n in enumerate(p.names) for m in [rx.search(n)] if m}
        for ni, lid in ids.items():
            pat = struct.pack("<ii", ni, 0)
            pos = d.find(pat)
            while pos >= 0:
                marks.append((pos, lid))
                pos = d.find(pat, pos + 1)
        marks.sort()
        rows = {}
        for pos, ns, key, src in _texts(d):
            owner = None
            for mp, lid in marks:
                if mp < pos:
                    owner = lid
                else:
                    break
            if owner:
                rows.setdefault(owner, []).append((ns, key, src))
        if rows:
            return rows
    return {}


def find_names(raw, code, lang="en", log=print):
    """{livery id: name} for one aircraft code, plus a debug text. Empty when the tables aren't unpacked."""
    raw = Path(raw)
    locres = {}
    for f in list((raw / LOCRES_DIR.format(lang=lang)).glob("*.locres")) + list(raw.rglob(f"{lang}/Game.locres")):
        try:
            locres.update(read_locres(f))
        except Exception as e:
            log(f"  Skin names: couldn't read {f.name}: {e}")
    names, debug = {}, []
    for rel in SKIN_TABLES:
        t = raw / rel
        if not t.exists():
            debug.append(f"{rel}: not unpacked")
            continue
        try:
            rows = skin_rows(t, code)
        except Exception as e:
            debug.append(f"{rel}: {e}")
            continue
        debug.append(f"{rel}: {len(rows)} row(s) for {code}")
        for lid, texts in sorted(rows.items()):
            shown = [locres.get((ns, key)) or src for ns, key, src in texts]
            shown = [s for s in shown if s and s.strip()]
            debug.append(f"  {lid}: " + " | ".join(f"{ns}/{key}={locres.get((ns, key), '')!r} src={src!r}"
                                                     for ns, key, src in texts))
            if shown and lid not in names:
                names[lid] = shown[0].strip()
    debug.append(f"language file entries: {len(locres)}")
    return names, "\n".join(debug)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    n, dbg = find_names(sys.argv[1], sys.argv[2])
    print(dbg)
    for k, v in sorted(n.items()):
        print(k, v)
