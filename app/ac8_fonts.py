"""Fonts from the game's FontFace assets -> the original .ttf / .otf files.

    python ac8_fonts.py <raw folder> <out folder>

An Unreal FontFace wraps the font file as it was imported. Depending on how it was cooked, the file's bytes sit
inside the package (.uexp / .ubulk) or next to it as <Name>.ufont. Either way they are the untouched font file,
so this finds the font data (by its sfnt header) and writes it out under the package's name:
    UI/Font/Din2014_Bold.uasset  ->  UI/Font/Din2014_Bold.otf
Composite fonts (UFont, e.g. UI_FontSet_*) only point at FontFaces and hold no font data themselves.
"""
import os
import struct
import sys
from pathlib import Path

SFNT_TAGS = {b"\x00\x01\x00\x00": ".ttf", b"OTTO": ".otf", b"true": ".ttf"}


def _padded(data, start, end):
    """Font files usually end with zero bytes up to a 4-byte boundary: keep those, but nothing else."""
    pad = min((end + 3) & ~3, len(data) - start)
    return pad if not any(data[start + end:start + pad]) else end


def _font_length(data, start, base=None):
    """Length of a valid sfnt font starting at `start`, else 0. base: where its table offsets count from (the
    font itself, or the start of the collection it is in)."""
    base = start if base is None else base
    if start + 12 > len(data):
        return 0
    num = struct.unpack_from(">H", data, start + 4)[0]
    if not 4 <= num <= 64 or start + 12 + num * 16 > len(data):
        return 0
    end, tags = 12 + num * 16, set()
    for i in range(num):
        tag, _sum, off, length = struct.unpack_from(">4sIII", data, start + 12 + i * 16)
        if not all(32 <= c < 127 for c in tag) or off < 12 or base + off + length > len(data):
            return 0
        tags.add(tag)
        end = max(end, base + off + length - start)
    if not ({b"cmap", b"head"} <= tags or {b"cmap", b"CFF "} <= tags):
        return 0
    return _padded(data, start, end)


def _collection_length(data, start):
    """Length of a TrueType collection (.ttc) starting at `start`, else 0."""
    if start + 12 > len(data):
        return 0
    n = struct.unpack_from(">I", data, start + 8)[0]
    if not 1 <= n <= 64 or start + 12 + 4 * n > len(data):
        return 0
    end = 12 + 4 * n
    for i in range(n):
        off = struct.unpack_from(">I", data, start + 12 + 4 * i)[0]
        if data[start + off:start + off + 4] not in SFNT_TAGS:
            return 0
        sub = _font_length(data, start + off, base=start)  # table offsets in a collection count from its start
        if not sub:
            return 0
        end = max(end, off + sub)
    return _padded(data, start, end)


def find_fonts(data):
    """Every complete font file inside a block of bytes: [(bytes, extension)]."""
    out, pos = [], 0
    sigs = list(SFNT_TAGS) + [b"ttcf"]
    while True:
        hits = [(data.find(s, pos), s) for s in sigs]
        hits = [(i, s) for i, s in hits if i >= 0]
        if not hits:
            return out
        i, s = min(hits)
        if s == b"ttcf":
            n, ext = _collection_length(data, i), ".ttc"
        else:
            n, ext = _font_length(data, i), SFNT_TAGS[s]
        if n:
            out.append((bytes(data[i:i + n]), ext))
            pos = i + n
        else:
            pos = i + 1


def is_font_face(uasset):
    """A FontFace package (the composite UFont "font sets" only point at those, so they're skipped)."""
    try:
        with open(uasset, "rb") as fh:
            if b"FontFace" not in fh.read(256 * 1024):  # cheap test first: the class name isn't even there
                return False
    except OSError:
        return False
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from ac8_package import Package
        pkg = Package(uasset)
        return any(pkg.export_class(e) == "FontFace" for e in pkg.exports)
    except Exception:
        return True  # can't read the header: look for font data anyway


def font_from_package(uasset):
    """The font(s) of one FontFace package, from <name>.ufont, .ubulk or .uexp (in that order)."""
    uasset = Path(uasset)
    for ext in (".ufont", ".ubulk", ".uptnl", ".uexp", ".uasset"):
        f = uasset.with_suffix(ext)
        if f.exists():
            fonts = find_fonts(f.read_bytes())
            if fonts:
                return fonts
    return []


def convert_folder(raw_dir, out_dir, log=print):
    raw_dir, out_dir = Path(raw_dir), Path(out_dir)
    pkgs, loose = [], []
    for r, _d, files in os.walk(raw_dir):
        for f in files:
            p = Path(r) / f
            if f.endswith(".uasset") and is_font_face(p):
                pkgs.append(p)
            elif f.endswith(".ufont") and not p.with_suffix(".uasset").exists():
                loose.append(p)  # a .ufont from the .pak without its package: convert it on its own too
    if not pkgs and not loose:
        return 0
    log(f"Fonts: {len(pkgs) + len(loose)} font asset(s)")
    written = 0
    for src in sorted(pkgs) + sorted(loose):
        rel = src.relative_to(raw_dir).with_suffix("")
        parts = rel.parts
        if len(parts) > 2 and parts[1] == "Content":  # drop Live/Content so the folders mirror the game's
            rel = Path(*parts[2:])
        fonts = font_from_package(src) if src.suffix == ".uasset" else find_fonts(src.read_bytes())
        if not fonts:
            log(f"  {src.stem}: no font data found (its .ufont may not be extracted)")
            continue
        for k, (blob, ext) in enumerate(fonts):
            out = out_dir / rel.parent / (rel.name + (f"_{k + 1}" if len(fonts) > 1 else "") + ext)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(blob)
            written += 1
            log(f"  {out.name} ({len(blob) / 1024:.0f} KB)")
    log(f"Fonts done: {written} font file(s) written")
    return written


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    convert_folder(sys.argv[1], sys.argv[2])
