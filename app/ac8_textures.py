"""Cooked UE5 texture -> PNG converter for ACE COMBAT 8 (works on retoc 'to-legacy' output).

A cooked Texture2D is split over three files:
    .uasset   package header; ends with the data-resource table (where every mip's bytes live)
    .uexp     export data: properties, then the platform data (size, pixel format, mip list);
              small mips are stored inline here
    .ubulk    the big mips

    python ac8_textures.py <raw folder> <output folder> [--max-texture 2048]
"""
import contextlib
import os
import struct
import sys
import threading
from pathlib import Path

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # find the sibling ac8_* modules
from ac8_package import BULK_INLINE, BULK_OPTIONAL, BULK_SEPARATE_FILE  # noqa: E402  (bulk data flags)
from ac8_package import read_data_resources as _read_resources  # noqa: E402

# pixel format -> (block bytes or None for raw, Pillow decoder setup)
BCN = {"PF_DXT1": (8, 1), "PF_DXT3": (16, 2), "PF_DXT5": (16, 3), "PF_BC4": (8, 4),
       "PF_BC5": (16, 5), "PF_BC6H": (16, 6), "PF_BC7": (16, 7)}
RAW = {"PF_B8G8R8A8": (4, "RGBA", "BGRA"), "PF_R8G8B8A8": (4, "RGBA", "RGBA"), "PF_G8": (1, "L", "L"),
       "PF_A8": (1, "L", "L"), "PF_R8": (1, "L", "L"), "PF_G16": (2, "I;16", "I;16"),
       "PF_R8G8": (2, "LA", "LA")}


class TextureError(Exception):
    pass


def read_data_resources(uasset: bytes):
    """The data-resource table (the last block of a UE5.2+ cooked .uasset): where each bulk payload lives."""
    out = _read_resources(uasset)
    if not out:
        raise TextureError("data resource table not found (not a UE5.2+ cooked package?)")
    return out


def find_platform_data(uexp: bytes):
    """Locate FTexturePlatformData: int32 SizeX, SizeY, uint32 PackedData, FString PixelFormat."""
    pos = 0
    while True:
        pos = uexp.find(b"PF_", pos)
        if pos < 0:
            raise TextureError("no pixel format found (virtual texture or not a texture?)")
        strlen = struct.unpack_from("<i", uexp, pos - 4)[0] if pos >= 16 else 0
        end = pos + strlen - 1
        if 4 <= strlen <= 40 and end < len(uexp) and uexp[end] == 0:
            fmt = uexp[pos:end].decode("ascii", "replace")
            sx, sy, packed = struct.unpack_from("<iiI", uexp, pos - 16)
            if 0 < sx <= 65536 and 0 < sy <= 65536:
                return fmt, sx, sy, packed, end + 1
        pos += 3


def parse_texture(uasset_path: Path):
    uasset = uasset_path.read_bytes()
    uexp_path = uasset_path.with_suffix(".uexp")
    if not uexp_path.exists():
        raise TextureError("missing .uexp")
    uexp = uexp_path.read_bytes()
    resources = read_data_resources(uasset)
    fmt, sx, sy, packed, p = find_platform_data(uexp)
    if packed & (1 << 30):  # HasOptData: ExtData, NumMipsInTail
        p += 8
    num_slices = packed & 0x3FFFFFFF & ~(1 << 29) or 1
    first_mip, num_mips = struct.unpack_from("<ii", uexp, p)
    p += 8
    if not 0 < num_mips <= 20:
        raise TextureError(f"unexpected mip count {num_mips}")
    mips = []
    for _ in range(num_mips):
        idx = struct.unpack_from("<i", uexp, p)[0]
        p += 4
        if not 0 <= idx < len(resources):
            raise TextureError(f"bad data resource index {idx}")
        res = resources[idx]
        inline_at = None
        if res["flags"] & BULK_INLINE:
            inline_at = p
            p += res["size"]
        mx, my, mz = struct.unpack_from("<iii", uexp, p)
        p += 12
        mips.append({"res": res, "inline_at": inline_at, "w": mx, "h": my, "d": mz})
    return {"format": fmt, "width": sx, "height": sy, "slices": num_slices, "mips": mips,
            "first_mip": first_mip, "uexp": uexp, "path": uasset_path}


def mip_bytes(tex, mip):
    res = mip["res"]
    if mip["inline_at"] is not None:
        return tex["uexp"][mip["inline_at"]:mip["inline_at"] + res["size"]]
    if res["size"] <= 0:
        return b""
    if res["flags"] & BULK_SEPARATE_FILE:
        ext = ".uptnl" if res["flags"] & BULK_OPTIONAL else ".ubulk"
        f = tex["path"].with_suffix(ext)
        if not f.exists():
            return None
        with open(f, "rb") as fh:
            fh.seek(res["offset"])
            return fh.read(res["size"])
    # payload at end of the export file
    data = tex["uexp"]
    return data[res["offset"]:res["offset"] + res["size"]] if res["offset"] + res["size"] <= len(data) else None


def decode(fmt, data, w, h):
    if fmt in BCN:
        block, n = BCN[fmt]
        bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
        need = bw * bh * block
        if len(data) < need:
            raise TextureError(f"mip data too short ({len(data)} < {need})")
        mode = {1: "RGBA", 2: "RGBA", 3: "RGBA", 4: "L", 5: "RGB", 6: "RGBF", 7: "RGBA"}[n]
        if n == 6:
            img = Image.frombytes("RGBF", (bw * 4, bh * 4), data[:need], "bcn", n)
            img = img.convert("RGB") if hasattr(img, "convert") else img
        else:
            img = Image.frombytes(mode, (bw * 4, bh * 4), data[:need], "bcn", n)
        img = img.crop((0, 0, w, h))
        if n == 5:  # two-channel (usually a normal map): rebuild blue from red/green
            img = rebuild_normal_z(img)
        return img
    if fmt in RAW:
        bpp, mode, raw = RAW[fmt]
        need = w * h * bpp
        if len(data) < need:
            raise TextureError(f"mip data too short ({len(data)} < {need})")
        return Image.frombytes(mode, (w, h), data[:need], "raw", raw)
    raise TextureError(f"pixel format {fmt} not supported yet")


def rebuild_normal_z(img):
    try:
        import numpy as np
    except ImportError:
        return img
    a = np.asarray(img).astype(np.float32)
    x = a[..., 0] / 127.5 - 1.0
    y = a[..., 1] / 127.5 - 1.0
    z = np.sqrt(np.clip(1.0 - x * x - y * y, 0.0, 1.0))
    a[..., 2] = (z * 0.5 + 0.5) * 255.0
    return Image.fromarray(a.clip(0, 255).astype("uint8"), "RGB")


TEXTURE_CLASSES = {"Texture2D", "TextureCube", "Texture2DArray", "VolumeTexture", "TextureRenderTarget2D"}


def is_texture_package(uasset_path: Path):
    """True when the package's own main object is a texture (materials merely import Texture2D)."""
    try:
        head = uasset_path.read_bytes()
        if b"Texture" not in head:
            return False
        from ac8_package import Package
        pkg = Package(uasset_path)
        return any(pkg.export_class(e) in TEXTURE_CLASSES for e in pkg.exports)
    except Exception:
        return False


def convert_texture(uasset_path: Path, out_png: Path, max_size=0):
    """Write the texture as PNG. Virtual (UDIM) textures become one PNG per tile: name.1001.png, name.1002.png …
    max_size > 0 picks the largest mip whose side (per tile) is not bigger than that."""
    from ac8_package import Package
    pkg = Package(uasset_path)
    exp = next((e for e in pkg.exports if pkg.export_class(e) in TEXTURE_CLASSES), None)
    if exp is not None:
        data, _ = pkg.export_data(exp)
        fmt, sx, sy, packed, p = find_platform_data(data)
        if packed & (1 << 30):
            p += 8
        first, nmips = struct.unpack_from("<ii", data, p)
        if nmips == 0 and struct.unpack_from("<i", data, p + 8)[0] == 1:
            import ac8_vt
            v = ac8_vt.VirtualTexture(pkg, data, p + 12, fmt)
            tile_px = v.height // max(1, v.blocks_h)
            mip = 0
            while max_size and (tile_px >> mip) > max_size and mip < v.num_mips - 1:
                mip += 1
            # a huge virtual texture (many big UDIM tiles) takes a lot of memory: only two of those at a time
            big = (v.width >> mip) * (v.height >> mip) > 64_000_000
            with _BIG_TEXTURES if big else contextlib.nullcontext():
                return _write_vt(v, mip, fmt, out_png)
    return _write_plain(uasset_path, out_png, max_size)


_BIG_TEXTURES = threading.BoundedSemaphore(2)


def _write_vt(v, mip, fmt, out_png):
    """Decode a virtual texture at that mip and write it: one PNG, or one per UDIM tile (name.1001.png …)."""
    img = v.decode_mip(mip)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    if v.blocks_w * v.blocks_h == 1:
        img.save(out_png)
        return fmt, img.size, 1
    tw, th = img.width // v.blocks_w, img.height // v.blocks_h

    def save_tile(xy):
        x, y = xy
        tile = img.crop((x * tw, y * th, (x + 1) * tw, (y + 1) * th))
        tile.save(out_png.with_name(f"{out_png.stem}.{1001 + x + 10 * y}.png"))

    # PNG writing is most of the time and Pillow does it outside Python's lock: tiles in parallel
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(4, v.blocks_w * v.blocks_h)) as ex:
        list(ex.map(save_tile, [(x, y) for y in range(v.blocks_h) for x in range(v.blocks_w)]))
    return fmt, (tw, th), v.blocks_w * v.blocks_h


def _write_plain(uasset_path, out_png, max_size):
    tex = parse_texture(uasset_path)
    mips = tex["mips"]
    if max_size:
        small = [m for m in mips if max(m["w"], m["h"]) <= max_size]
        mips = small + [m for m in mips if m not in small] if small else mips
    for mip in mips:
        data = mip_bytes(tex, mip)
        if data:
            img = decode(tex["format"], data, mip["w"], mip["h"] * (tex["slices"] if mip["d"] <= 1 else 1))
            out_png.parent.mkdir(parents=True, exist_ok=True)
            img.save(out_png)
            return tex["format"], img.size, 1
    raise TextureError("no mip data available")


JOBS = max(1, min(os.cpu_count() or 2, 8))  # textures converted at once (decoding and PNG writing run in parallel)


def convert_folder(raw_dir, out_dir, log=print, max_size=0, jobs=JOBS):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    raw_dir, out_dir = Path(raw_dir), Path(out_dir)
    files = [Path(r) / f for r, _d, fs in os.walk(raw_dir) for f in fs if f.lower().endswith(".uasset")]
    textures = sorted(f for f in files if is_texture_package(f))
    log(f"Textures: {len(textures)} of {len(files)} packages ({jobs} at a time)")
    lock = threading.Lock()
    state = {"ok": 0, "fail": 0, "done": 0}
    unsupported = {}

    def one(f):
        rel = f.relative_to(raw_dir).with_suffix(".png")
        # drop the Live/Content prefix so the PNGs mirror the game folders
        parts = rel.parts
        if len(parts) > 2 and parts[1] == "Content":
            rel = Path(*parts[2:])
        res, msg = 0, None
        try:
            res = convert_texture(f, out_dir / rel, max_size)[2]
        except TextureError as e:
            msg = str(e)
        except Exception as e:  # keep going on anything unexpected
            msg = f"ERROR {e}"
        with lock:
            state["done"] += 1
            if msg is None:
                state["ok"] += res
            else:
                state["fail"] += 1
                if msg.startswith("pixel format"):
                    unsupported[msg] = unsupported.get(msg, 0) + 1
                else:
                    log(f"  {f.name}: {msg}")
            i = state["done"]
            if i % 10 == 0 or i == len(textures):
                print(f"\rTextures {i}/{len(textures)}", end="", file=sys.stderr, flush=True)

    with ThreadPoolExecutor(max_workers=jobs) as ex:
        list(ex.map(one, textures))
    ok, fail = state["ok"], state["fail"]
    print(file=sys.stderr)
    for msg, n in unsupported.items():
        log(f"  {n} texture(s): {msg}")
    log(f"Textures done: {ok} PNG written, {fail} failed")
    return ok, fail


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    size = 0
    if "--max-texture" in args:
        i = args.index("--max-texture")
        size = int(args[i + 1])
        del args[i:i + 2]
    if len(args) != 2:
        sys.exit(__doc__)
    convert_folder(args[0], args[1], max_size=size)
