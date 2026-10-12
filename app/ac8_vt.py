"""Virtual texture (VT / UDIM) reader for cooked UE5.4 textures in ACE COMBAT 8.

Layout after the pixel-format string of FTexturePlatformData when bIsVirtual is set (FVirtualTextureBuiltData):
  NumLayers, ?, WidthInBlocks, HeightInBlocks, TileSize, TileBorderSize, TileDataOffsetPerLayer[],
  NumMips, Width, Height, ChunkIndexPerMip[], BaseOffsetPerMip[], TileOffsetData[] (per mip: Width, Height,
  MaxAddress, Addresses[], Offsets[]), ...tables..., LayerTypes (FString) + fallback colours, Chunks[]
  (SHA1, SizeInBytes, CodecPayloadSize, per layer: codec u8 + payload offset u32, bulk data index).
Tiles are addressed in Morton (Z) order; each tile is TileSize + 2*border pixels of raw GPU blocks.
"""
import struct

import numpy as np
from PIL import Image

from ac8_package import Reader

CODEC_RAW_GPU = 4
BLOCK = {"PF_DXT1": (8, 1), "PF_DXT3": (16, 2), "PF_DXT5": (16, 3), "PF_BC4": (8, 4), "PF_BC5": (16, 5),
         "PF_BC6H": (16, 6), "PF_BC7": (16, 7), "PF_B8G8R8A8": (None, None), "PF_G8": (None, None)}


class VTError(Exception):
    pass


def _u32_array(r):
    n = r.i32()
    if not 0 <= n < 1 << 22:
        raise VTError(f"bad array length {n} at {r.p - 4}")
    return list(struct.unpack_from(f"<{n}I", r.b, r.p)), r.skip(n * 4)


def u32s(r):
    vals, _ = _u32_array(r)
    return vals


def _morton_tables(n):
    """Spread the bits of 0..n-1 for x (even bits) and y (odd bits)."""
    v = np.arange(n, dtype=np.uint32)
    out = np.zeros(n, np.uint32)
    for b in range(16):
        out |= ((v >> b) & 1) << (2 * b)
    return out


class VirtualTexture:
    def __init__(self, pkg, data, pos, fmt):
        """`pos` points just after bIsVirtual in the export data."""
        r = Reader(data, pos)
        self.pkg, self.format = pkg, fmt
        self.num_layers = r.u32()
        if self.num_layers != 1:
            raise VTError(f"{self.num_layers}-layer virtual textures not supported yet")
        r.u32()
        self.blocks_w, self.blocks_h = r.u32(), r.u32()
        self.tile_size, self.border = r.u32(), r.u32()
        self.layer_offsets = u32s(r)
        self.num_mips, self.width, self.height = r.u32(), r.u32(), r.u32()
        self.chunk_per_mip = u32s(r)
        self.base_per_mip = u32s(r)
        n = r.i32()
        self.tile_offsets = []
        for _ in range(n):
            w, h, max_addr = r.u32(), r.u32(), r.u32()
            addrs, offs = u32s(r), u32s(r)
            self.tile_offsets.append((w, h, max_addr, np.array(addrs, np.uint32), np.array(offs, np.uint32)))
        # skip ahead to the layer type string (the remaining index tables are not needed)
        name = fmt.encode() + b"\x00"
        at = data.find(struct.pack("<i", len(name)) + name, r.p)
        if at < 0:
            raise VTError("layer table not found")
        r.p = at
        r.fstring()
        self.fallback = struct.unpack_from("<4f", data, r.p)
        r.skip(16)
        nchunks = r.i32()
        self.chunks = []
        for _ in range(nchunks):
            r.skip(20)
            size, payload = r.u32(), r.u32()
            codec = r.u8()
            r.u32()  # codec payload offset
            res = r.i32()
            self.chunks.append({"size": size, "payload": payload, "codec": codec, "res": res})
        if self.format not in BLOCK:
            raise VTError(f"virtual texture format {self.format} not supported")
        self.block_bytes, self.bcn = BLOCK[self.format]
        if self.block_bytes is None:
            raise VTError(f"uncompressed virtual texture format {self.format} not supported yet")
        px = self.tile_size + 2 * self.border
        self.tile_px = px
        self.tile_bytes = (px // 4) * (px // 4) * self.block_bytes
        if self.layer_offsets and self.layer_offsets[0] not in (0, self.tile_bytes):
            raise VTError(f"tile size mismatch ({self.layer_offsets[0]} vs {self.tile_bytes})")
        self._chunk_cache = {}

    def chunk_bytes(self, i):
        if i not in self._chunk_cache:
            c = self.chunks[i]
            if c["codec"] != CODEC_RAW_GPU:
                raise VTError(f"chunk codec {c['codec']} not supported")
            self._chunk_cache = {i: self.pkg.bulk(c["res"])}
        return self._chunk_cache[i]

    def mip_size(self, mip):
        return max(1, self.width >> mip), max(1, self.height >> mip)

    def decode_mip(self, mip):
        """Whole mip as an RGBA image (missing tiles get the fallback colour)."""
        tw, th, max_addr, addrs, offs = self.tile_offsets[mip]
        w, h = self.mip_size(mip)
        ts, b = self.tile_size, self.border
        fill = tuple(int(max(0, min(1, c)) * 255 + 0.5) for c in self.fallback)
        canvas = Image.new("RGBA", (tw * ts, th * ts), fill)
        data = self.chunk_bytes(self.chunk_per_mip[mip])
        base = self.base_per_mip[mip]
        mx, my = _morton_tables(max(tw, th, 1)), _morton_tables(max(tw, th, 1)) << 1
        mode = {1: "RGBA", 2: "RGBA", 3: "RGBA", 4: "L", 5: "RGB", 6: "RGBF", 7: "RGBA"}[self.bcn]
        for y in range(th):
            for x in range(tw):
                addr = int(mx[x] | my[y])
                if addr >= max_addr:
                    continue
                k = int(np.searchsorted(addrs, addr, side="right")) - 1
                if k < 0 or offs[k] == 0xFFFFFFFF:
                    continue
                tile = int(offs[k]) + addr - int(addrs[k])
                start = base + tile * self.tile_bytes
                raw = data[start:start + self.tile_bytes]
                if len(raw) < self.tile_bytes:
                    continue
                img = Image.frombytes(mode, (self.tile_px, self.tile_px), raw, "bcn", self.bcn)
                img = img.crop((b, b, b + ts, b + ts))
                canvas.paste(img.convert("RGBA"), (x * ts, y * ts))
        return canvas.crop((0, 0, w, h))
