"""Cooked AC8 meshes -> .glb (glTF 2.0 binary) with skeleton, skin weights, materials and embedded textures.

    python ac8_gltf.py <raw folder> <output folder> [--max-texture 2048]

Conversions: Unreal (cm, Z-up, left-handed) -> glTF (m, Y-up, right-handed): (x, y, z) -> (x, z, y) / 100. That axis
swap is a mirror, which already turns UE's clockwise front faces into glTF's counter-clockwise ones. UDIM / virtual textures are split per tile: one material and primitive per tile.
Normal maps are converted from DirectX (green down) to glTF/OpenGL (green up), with blue rebuilt from red/green.
"""
import io
import json
import re
import os
import struct
import sys
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # find the sibling ac8_* modules
import ac8_mesh as am
import ac8_textures as at
import ac8_vt as vt
from ac8_package import Package

GL_FLOAT, GL_USHORT, GL_UINT = 5126, 5123, 5125
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963

ROLE_BASE = {"C", "D", "BC", "CO", "BASECOLOR", "ALBEDO", "DIFFUSE", "COL", "COLOR", "A"}
ROLE_NORMAL = {"N", "NO", "NM", "NRM", "NORMAL", "NOR"}  # _NO: blue channel is ambient occlusion
ROLE_MR = {"MRX", "MRO", "MR", "MRA", "MRAO", "MROS"}    # _MRO/_MROS: blue channel is ambient occlusion
ROLE_ORM = {"ORM", "ARM", "RMA"}
TRANSPARENT_HINTS = ("glass", "canopy", "hud", "visor", "window", "lens")


# ------------------------------------------------------------------ coordinates

def to_gl(v):
    return v[:, [0, 2, 1]]


def quat_to_mat(x, y, z, w):
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def mat_to_quat(m):
    t = np.trace(m)
    if t > 0:
        s = np.sqrt(t + 1.0) * 2
        w, x, y, z = 0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        w, x, y, z = (m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s
    elif m[1, 1] > m[2, 2]:
        s = np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        w, x, y, z = (m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s
    else:
        s = np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        w, x, y, z = (m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s
    q = np.array([x, y, z, w])
    return q / np.linalg.norm(q)


SWAP = np.array([[1, 0, 0], [0, 0, 1], [0, 1, 0]], float)


def bone_to_gl(rot, pos, scale):
    """UE local bone transform -> glTF (rotation xyzw, translation m, scale)."""
    r = SWAP @ quat_to_mat(*rot) @ SWAP
    t = SWAP @ np.array(pos) / 100.0
    s = np.array([scale[0], scale[2], scale[1]])
    return mat_to_quat(r), t, s


def trs_matrix(q, t, s):
    m = np.eye(4)
    m[:3, :3] = quat_to_mat(*q) * s
    m[:3, 3] = t
    return m


# ------------------------------------------------------------------ textures and materials

class TextureLibrary:
    def __init__(self, raw_dir, max_size=2048, log=print):
        self.raw = Path(raw_dir)
        self.max_size = max_size
        self.log = log
        self.by_name = {}
        for r, _d, files in os.walk(self.raw):
            for f in files:
                if f.endswith(".uasset"):
                    self.by_name.setdefault(f[:-7].lower(), Path(r) / f)
        self.cache = {}
        self.encoded = {}  # (texture, tile, kind, ao) -> (bytes, mime, has alpha): shared by every GLB
        self._lock = threading.Lock()
        self._key_locks = {}

    def key_lock(self, key):
        """One lock per texture, so parallel meshes decode / encode a shared texture only once."""
        with self._lock:
            return self._key_locks.setdefault(key, threading.Lock())

    def find(self, path_parts):
        """Import path (['/Game/A/B/T_X', 'T_X']) -> .uasset in the raw folder, if it was extracted."""
        if not path_parts:
            return None
        pkg = path_parts[0]
        for prefix, local in (("/Game/", "Live/Content/"), ("/Engine/", "Engine/Content/")):
            if pkg.startswith(prefix):
                p = self.raw / (local + pkg[len(prefix):] + ".uasset")
                if p.exists():
                    return p
        return self.by_name.get(pkg.rsplit("/", 1)[-1].lower())

    def load(self, uasset):
        """-> {'udim': (w, h), 'tiles': {(x, y): Image}} or {'udim': None, 'tiles': {(0, 0): Image}}"""
        key = str(uasset)
        if key in self.cache:
            return self.cache[key]
        with self.key_lock(("load", key)):
            if key not in self.cache:
                self.cache[key] = self._load(uasset)
        return self.cache[key]

    def _load(self, uasset):
        result = None
        try:
            pkg = Package(uasset)
            exp = next(e for e in pkg.exports if pkg.export_class(e) in at.TEXTURE_CLASSES)
            data, _ = pkg.export_data(exp)
            fmt, sx, sy, packed, pos = at.find_platform_data(data)
            first, nmips = struct.unpack_from("<ii", data, pos)
            if nmips == 0 and struct.unpack_from("<i", data, pos + 8)[0] == 1:
                v = vt.VirtualTexture(pkg, data, pos + 12, fmt)
                tile_px = v.height // max(1, v.blocks_h)
                mip = 0
                while self.max_size and (tile_px >> mip) > self.max_size and mip < v.num_mips - 1:
                    mip += 1
                img = v.decode_mip(mip)
                tw, th = img.width // v.blocks_w, img.height // v.blocks_h
                tiles = {}
                for y in range(v.blocks_h):
                    for x in range(v.blocks_w):
                        tiles[(x, y)] = img.crop((x * tw, y * th, (x + 1) * tw, (y + 1) * th))
                result = {"udim": (v.blocks_w, v.blocks_h) if v.blocks_w * v.blocks_h > 1 else None,
                          "tiles": tiles}
            else:
                tex = at.parse_texture(Path(uasset))
                for mip in tex["mips"]:
                    if self.max_size and max(mip["w"], mip["h"]) > self.max_size and mip is not tex["mips"][-1]:
                        continue
                    data_b = at.mip_bytes(tex, mip)
                    if data_b:
                        img = at.decode(tex["format"], data_b, mip["w"], mip["h"])
                        result = {"udim": None, "tiles": {(0, 0): img}}
                        break
        except Exception as e:
            self.log(f"  texture {Path(uasset).name}: {e}")
        return result


FAKE_MAP_HINTS = ("reflection", "matcap", "envmap", "cubemap")  # effect textures named _N/_C that aren't surface maps


def texture_role(name):
    suffix = name.rsplit("_", 1)[-1].upper()
    if any(h in name.lower() for h in FAKE_MAP_HINTS):
        # e.g. T_..._CP_Reflection_N: drives a fake canopy reflection, not the surface bumps; used as a normal map it
        # covers the cockpit in coloured blotches. Keep its colour/alpha (glass tint), never use it as a normal.
        return "base" if suffix in ROLE_BASE or suffix == "CO" else None
    if suffix in ROLE_BASE:
        return "base"
    if suffix in ROLE_NORMAL:
        return "normal"
    if suffix in ROLE_MR:
        return "mr"
    if suffix in ROLE_ORM:
        return "orm"
    return None


GENERIC_TEX_HINTS = ("noise", "flake", "detail", "tiling", "tile", "default", "dummy", "grunge", "scratch", "dirt",
                     "blank", "white", "black", "gray", "grey", "flat")


def _tokens(name):
    return {t for t in re.split(r"[_\W]+", name.lower()) if len(t) > 1 and t not in ("t", "mi", "m")}


def _texture_score(tex_name, tex_path, mi_name, depth):
    """Higher = more likely the part's own texture. A material can import shared helper textures (a tiling flake
    noise normal, a dirt mask…) next to its real ones; those must not win."""
    low = tex_name.lower()
    score = -10 * depth
    if any(h in low for h in GENERIC_TEX_HINTS):
        score -= 50
    if "/common/" in str(tex_path).replace("\\", "/").lower():
        score -= 20
    score += 15 * len(_tokens(tex_name) & _tokens(mi_name))
    return score


def material_textures(lib, mi_path, depth=0, _mi_name=None):
    """{'base': (uasset, name), 'normal': ..., 'mr'/'orm': ...} from a material instance's imports (and its
    parents). When several textures fit a role, the one that looks like the part's own wins (see _texture_score)."""
    cands = {}
    _collect_textures(lib, mi_path, 0, cands, None)
    out = {}
    for role, lst in cands.items():
        if role != "base":  # a tiling noise / detail normal on its own looks worse than no normal map at all
            lst = [c for c in lst if not any(h in c[1][1].lower() for h in GENERIC_TEX_HINTS)]
        if lst:
            out[role] = max(lst, key=lambda c: c[0])[1]
    # roles still missing: the part's own textures are usually named after the material
    # (MI_f18c_CP -> T_PP0007_f18c_CP_N), even when the material instance only points at shared helpers
    mi = lib.find(mi_path)
    if mi is not None:
        stem = re.sub(r"^(MI|M|MIC)_", "", Path(mi).stem, flags=re.I).lower()
        if stem:
            for name, path in lib.by_name.items():
                if not name.startswith("t_") or not (name[:-len(name.rsplit("_", 1)[-1]) - 1].endswith("_" + stem)):
                    continue
                role = texture_role(name)
                if role == "mr" and "orm" in out or role == "orm" and "mr" in out:
                    continue
                if role and role not in out:
                    out[role] = (path, Path(path).stem)
    return out


def _collect_textures(lib, mi_path, depth, cands, mi_name):
    mi = lib.find(mi_path)
    if mi is None or depth > 4:
        return
    try:
        pkg = Package(mi)
    except Exception:
        return
    mi_name = mi_name or Path(mi).stem
    parents = []
    for i, imp in enumerate(pkg.imports):
        if imp["class"] == "Texture2D":
            role = texture_role(imp["name"])
            if role:
                path = lib.find(pkg.import_path(-i - 1))
                if path:
                    sc = _texture_score(imp["name"], path, mi_name, depth)
                    cands.setdefault(role, []).append((sc, (path, imp["name"])))
        elif imp["class"] in ("MaterialInstanceConstant",) and not imp["name"].startswith("Default__"):
            parents.append(pkg.import_path(-i - 1))
    for parent in parents:
        _collect_textures(lib, parent, depth + 1, cands, mi_name)


def prep_normal(img):
    a = np.asarray(img.convert("RGB")).astype(np.float32) / 127.5 - 1.0
    x, y = a[..., 0], -a[..., 1]  # DirectX -> OpenGL
    z = np.sqrt(np.clip(1.0 - x * x - y * y, 0.0, 1.0))
    out = np.stack([x, y, z], -1) * 127.5 + 127.5
    return Image.fromarray(out.clip(0, 255).astype(np.uint8), "RGB")


def prep_mr(img, kind, ao=None):
    """-> glTF ORM image: R occlusion, G roughness, B metallic. `ao` = optional greyscale AO image."""
    a = np.asarray(img.convert("RGB"))
    if kind == "orm":
        return Image.fromarray(a, "RGB")
    out = np.empty_like(a)
    if ao is not None:
        out[..., 0] = np.asarray(ao.convert("L").resize(img.size, Image.BILINEAR))
    else:
        out[..., 0] = 255
    out[..., 1] = a[..., 1]     # roughness
    out[..., 2] = a[..., 0]     # metallic
    return Image.fromarray(out, "RGB")


def ao_from(img, name):
    """AC8 keeps baked AO in the blue channel of _NO and _MRO textures."""
    suffix = name.rsplit("_", 1)[-1].upper()
    if suffix in ("NO", "MRO", "MRA", "MROS"):
        return img.convert("RGB").split()[2]
    return None


def encode(img, kind):
    buf = io.BytesIO()
    if kind == "base" and img.mode == "RGBA" and img.getextrema()[3][0] < 250:
        img.save(buf, "PNG", optimize=False)
        return buf.getvalue(), "image/png", True
    if kind == "normal":
        img.save(buf, "PNG", optimize=False)
        return buf.getvalue(), "image/png", False
    img.convert("RGB").save(buf, "JPEG", quality=92)
    return buf.getvalue(), "image/jpeg", False


# ------------------------------------------------------------------ glTF building

class GLB:
    def __init__(self):
        self.bin = bytearray()
        self.j = {"asset": {"version": "2.0", "generator": "ACE Combat 8 Asset Extractor"},
                  "scenes": [{"nodes": []}], "scene": 0, "nodes": [], "meshes": [], "materials": [],
                  "accessors": [], "bufferViews": [], "buffers": [], "textures": [], "images": [],
                  "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}]}

    def view(self, data, target=None):
        while len(self.bin) % 4:
            self.bin.append(0)
        v = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": len(data)}
        if target:
            v["target"] = target
        self.bin += data
        self.j["bufferViews"].append(v)
        return len(self.j["bufferViews"]) - 1

    def accessor(self, arr, ctype, atype, target=None, minmax=False, normalized=False):
        arr = np.ascontiguousarray(arr)
        a = {"bufferView": self.view(arr.tobytes(), target), "componentType": ctype, "count": int(arr.shape[0]),
             "type": atype}
        if normalized:
            a["normalized"] = True
        if minmax:
            a["min"] = [float(x) for x in arr.min(0)]
            a["max"] = [float(x) for x in arr.max(0)]
        self.j["accessors"].append(a)
        return len(self.j["accessors"]) - 1

    def image(self, data, mime, name):
        self.j["images"].append({"bufferView": self.view(data), "mimeType": mime, "name": name})
        self.j["textures"].append({"sampler": 0, "source": len(self.j["images"]) - 1})
        return len(self.j["textures"]) - 1

    def node(self, n):
        self.j["nodes"].append(n)
        return len(self.j["nodes"]) - 1

    def write(self, path):
        for k in [k for k, v in self.j.items() if isinstance(v, list) and not v and k not in ("buffers",)]:
            del self.j[k]
        while len(self.bin) % 4:
            self.bin.append(0)
        self.j["buffers"] = [{"byteLength": len(self.bin)}]
        js = json.dumps(self.j, separators=(",", ":")).encode()
        js += b" " * (-len(js) % 4)
        total = 12 + 8 + len(js) + 8 + len(self.bin)
        with open(path, "wb") as f:
            f.write(struct.pack("<III", 0x46546C67, 2, total))
            f.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
            f.write(struct.pack("<II", len(self.bin), 0x004E4942) + bytes(self.bin))


class MaterialBuilder:
    def __init__(self, glb, lib, log=print):
        self.glb, self.lib, self.log = glb, lib, log
        self.mats = {}
        self.images = {}

    def tile_image(self, uasset, tile):
        t = self.lib.load(uasset)
        if not t:
            return None
        return t["tiles"].get(tile) or t["tiles"].get((0, 0))

    def tex(self, uasset, tile, kind, ao_src=None):
        key = (str(uasset), tile, kind, str(ao_src[0]) if ao_src else None)
        if key in self.images:
            return self.images[key]
        enc = self.lib.encoded.get(key)
        if enc is None:
            with self.lib.key_lock(("enc",) + key):  # another mesh may be making the same image right now
                enc = self.lib.encoded.get(key)
                if enc is None:
                    enc = self.lib.encoded[key] = self._encode(uasset, tile, kind, ao_src)
        if enc is False:
            self.images[key] = (None, False)
            return self.images[key]
        data, mime, alpha = enc
        name = Path(uasset).stem + (f".{1001 + tile[0] + tile[1] * 10}" if tile != (0, 0) else "")
        idx = self.glb.image(data, mime, name)
        self.images[key] = (idx, alpha)
        return self.images[key]

    def _encode(self, uasset, tile, kind, ao_src):
        """-> (bytes, mime, has alpha), or False when the texture can't be read."""
        t = self.lib.load(uasset)
        img = (t["tiles"].get(tile) or t["tiles"].get((0, 0))) if t else None
        if img is None:
            return False
        if kind == "normal":
            img = prep_normal(img)
        elif kind in ("mr", "orm"):
            ao = None
            if ao_src:
                src_img = self.tile_image(ao_src[0], tile)
                ao = ao_from(src_img, ao_src[1]) if src_img is not None else None
            img = prep_mr(img, kind, ao)
        return encode(img, "base" if kind == "base" else ("normal" if kind == "normal" else "mr"))

    def udim_of(self, slot_textures):
        for role in ("base", "normal", "mr", "orm"):
            if role in slot_textures:
                t = self.lib.load(slot_textures[role][0])
                if t and t["udim"]:
                    return t["udim"]
        return None

    def get(self, slot_name, mi_path, textures, tile):
        key = (slot_name, tuple(mi_path or ()), tile)
        if key in self.mats:
            return self.mats[key]
        name = slot_name + (f".{1001 + tile[0] + tile[1] * 10}" if tile is not None else "")
        tile = tile or (0, 0)
        m = {"name": name, "doubleSided": False,
             "pbrMetallicRoughness": {"metallicFactor": 1.0, "roughnessFactor": 1.0}}
        pbr = m["pbrMetallicRoughness"]
        has_alpha = False
        if "base" in textures:
            idx, has_alpha = self.tex(textures["base"][0], tile, "base")
            if idx is not None:
                pbr["baseColorTexture"] = {"index": idx}
        if "baseColorTexture" not in pbr:
            pbr["baseColorFactor"] = [0.6, 0.6, 0.62, 1.0]
        if "normal" in textures:
            idx, _ = self.tex(textures["normal"][0], tile, "normal")
            if idx is not None:
                m["normalTexture"] = {"index": idx}
        mr = textures.get("mr") or textures.get("orm")
        if mr:
            ao_src = None
            if "mr" in textures:
                for cand in (textures.get("normal"), mr):
                    if cand and cand[1].rsplit("_", 1)[-1].upper() in ("NO", "MRO", "MRA", "MROS"):
                        ao_src = cand
                        break
            idx, _ = self.tex(mr[0], tile, "mr" if "mr" in textures else "orm", ao_src)
            if idx is not None:
                pbr["metallicRoughnessTexture"] = {"index": idx}
                if "orm" in textures or ao_src:
                    m["occlusionTexture"] = {"index": idx}
        if not mr:
            pbr["metallicFactor"], pbr["roughnessFactor"] = 0.0, 0.6
        if any(h in slot_name.lower() for h in TRANSPARENT_HINTS):
            m["alphaMode"] = "BLEND"
            m["doubleSided"] = True
            c = pbr.get("baseColorFactor", [1, 1, 1, 1])
            pbr["baseColorFactor"] = [c[0], c[1], c[2], 0.35]
            pbr["metallicFactor"], pbr["roughnessFactor"] = 0.0, 0.05
        elif has_alpha:
            m["alphaMode"] = "MASK"
            m["alphaCutoff"] = 0.5
        self.glb.j["materials"].append(m)
        self.mats[key] = len(self.glb.j["materials"]) - 1
        return self.mats[key]


def build_glb(mesh, lib, out_path, log=print):
    glb = GLB()
    mb = MaterialBuilder(glb, lib, log)
    slot_tex = []
    for slot, path in mesh.materials:
        tx = material_textures(lib, path) if path else {}
        slot_tex.append((slot, path, tx, mb.udim_of(tx)))
    pos_gl = to_gl(mesh.positions) / 100.0
    nrm = to_gl(mesh.normals).astype(np.float64)
    length = np.linalg.norm(nrm, axis=1)
    bad = length < 1e-4
    if bad.any():  # a few game vertices store a zero normal: rebuild those from the surrounding triangles
        tri = mesh.indices.reshape(-1, 3)
        fn = np.cross(pos_gl[tri[:, 1]] - pos_gl[tri[:, 0]], pos_gl[tri[:, 2]] - pos_gl[tri[:, 0]])
        acc = np.zeros_like(nrm)
        for k in range(3):
            np.add.at(acc, tri[:, k], fn)
        nrm[bad] = acc[bad]
        length = np.linalg.norm(nrm, axis=1)
        still = length < 1e-12
        nrm[still] = (0.0, 1.0, 0.0)
        length[still] = 1.0
    nrm = (nrm / length[:, None]).astype(np.float32)
    uv0 = mesh.uvs[0] if mesh.uvs else np.zeros((len(pos_gl), 2), np.float32)
    skinned = bool(mesh.bones) and mesh.joints is not None
    if skinned:
        j4, w4 = mesh.joints, mesh.weights
        if j4.shape[1] > 4:  # keep the 4 strongest influences
            order = np.argsort(-w4, axis=1)[:, :4]
            j4 = np.take_along_axis(j4, order, 1)
            w4 = np.take_along_axis(w4, order, 1)
        elif j4.shape[1] < 4:
            pad = 4 - j4.shape[1]
            j4 = np.pad(j4, ((0, 0), (0, pad)))
            w4 = np.pad(w4, ((0, 0), (0, pad)))
        w4 = w4 / np.maximum(w4.sum(1, keepdims=True), 1e-9)
        j4 = np.where(w4 > 0, j4, 0)  # unused influence slots point at joint 0
    prims = []
    for s in mesh.sections:
        if s.num_tris <= 0:
            continue
        # UE's left-handed winding becomes glTF's counter-clockwise front faces through the Y/Z swap: keep order
        tri = mesh.indices[s.first_index:s.first_index + s.num_tris * 3].reshape(-1, 3)
        slot, path, tx, udim = slot_tex[s.material] if s.material < len(slot_tex) else (f"Material_{s.material}",
                                                                                       None, {}, None)
        groups = [(None, tri)]
        if udim:
            c = uv0[tri].mean(1)
            tile_x = np.clip(np.floor(c[:, 0]).astype(int), 0, udim[0] - 1)
            tile_y = np.clip(np.floor(c[:, 1]).astype(int), 0, udim[1] - 1)
            groups = [((tx_, ty_), tri[(tile_x == tx_) & (tile_y == ty_)])
                      for tx_ in range(udim[0]) for ty_ in range(udim[1])
                      if np.any((tile_x == tx_) & (tile_y == ty_))]
        for tile, t in groups:
            used, inv = np.unique(t.ravel(), return_inverse=True)
            uv = uv0[used].copy()
            if tile:
                uv -= np.array(tile, np.float32)
            attrs = {
                "POSITION": glb.accessor(pos_gl[used].astype(np.float32), GL_FLOAT, "VEC3", ARRAY_BUFFER, True),
                "NORMAL": glb.accessor(nrm[used].astype(np.float32), GL_FLOAT, "VEC3", ARRAY_BUFFER),
                "TEXCOORD_0": glb.accessor(uv.astype(np.float32), GL_FLOAT, "VEC2", ARRAY_BUFFER),
            }
            if len(mesh.uvs) > 1:  # second UV set: aircraft decal slots (emblems) live here — kept as-is
                attrs["TEXCOORD_1"] = glb.accessor(mesh.uvs[1][used].astype(np.float32), GL_FLOAT, "VEC2",
                                                   ARRAY_BUFFER)
            if skinned:
                attrs["JOINTS_0"] = glb.accessor(j4[used].astype(np.uint16), GL_USHORT, "VEC4", ARRAY_BUFFER)
                attrs["WEIGHTS_0"] = glb.accessor(w4[used].astype(np.float32), GL_FLOAT, "VEC4", ARRAY_BUFFER)
            idx = inv.astype(np.uint32 if len(used) > 65535 else np.uint16)
            prims.append({"attributes": attrs,
                          "indices": glb.accessor(idx, GL_UINT if idx.dtype == np.uint32 else GL_USHORT, "SCALAR",
                                                  ELEMENT_ARRAY_BUFFER),
                          "material": mb.get(slot, path, tx, tile)})
    glb.j["meshes"].append({"name": mesh.name, "primitives": prims})
    # the mesh node sits at the scene root (glTF wants skinned meshes there); bones go under an armature node
    mesh_node = glb.node({"name": mesh.name, "mesh": 0})
    glb.j["scenes"][0]["nodes"].append(mesh_node)
    root = None
    if mesh.bones:
        root = glb.node({"name": mesh.name + "_Armature", "children": []})
        glb.j["scenes"][0]["nodes"].append(root)
    if skinned:
        bone_nodes, globals_ = [], []
        for i, (name, parent, rot, pos, scale) in enumerate(mesh.bones):
            q, t, s = bone_to_gl(rot, pos, scale)
            n = {"name": name, "rotation": [float(x) for x in q], "translation": [float(x) for x in t]}
            if not np.allclose(s, 1.0):
                n["scale"] = [float(x) for x in s]
            bone_nodes.append(glb.node(n))
            local = trs_matrix(q, t, s)
            globals_.append(local if parent < 0 else globals_[parent] @ local)
        for i, (name, parent, *_rest) in enumerate(mesh.bones):
            if parent >= 0:
                glb.j["nodes"][bone_nodes[parent]].setdefault("children", []).append(bone_nodes[i])
            else:
                glb.j["nodes"][root]["children"].append(bone_nodes[i])
        ibm = np.stack([np.linalg.inv(g).T for g in globals_]).astype(np.float32).reshape(-1, 16)
        glb.j["skins"] = [{"name": mesh.name + "_Skeleton", "joints": bone_nodes,
                           "inverseBindMatrices": glb.accessor(ibm, GL_FLOAT, "MAT4"),
                           "skeleton": bone_nodes[0]}]
        glb.j["nodes"][mesh_node]["skin"] = 0
    glb.write(out_path)
    return len(prims), len(glb.j.get("images", []))


JOBS = max(1, min(os.cpu_count() or 2, 8))  # meshes converted at once (they share one texture cache)


def convert_folder(raw_dir, out_dir, max_texture=2048, log=print, jobs=JOBS):
    from concurrent.futures import ThreadPoolExecutor
    raw_dir, out_dir = Path(raw_dir), Path(out_dir)
    files = sorted(Path(r) / f for r, _d, fs in os.walk(raw_dir) for f in fs if f.endswith(".uasset"))
    meshes = [f for f in files if am.is_mesh_package(f)]
    log(f"Meshes: {len(meshes)} found ({jobs} at a time)")
    lib = TextureLibrary(raw_dir, max_texture, log)
    lock = threading.Lock()
    state = {"ok": 0, "fail": 0, "done": 0}

    def one(f):
        rel = f.relative_to(raw_dir).with_suffix(".glb")
        parts = rel.parts
        if len(parts) > 2 and parts[1] == "Content":
            rel = Path(*parts[2:])
        out = out_dir / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            t0 = time.time()
            mesh = am.read_mesh(f)
            nprim, nimg = build_glb(mesh, lib, out, log)
            kind = f"{len(mesh.bones)} bones" if mesh.bones else "static"
            msg, good = (f"  {f.stem}: {len(mesh.positions):,} verts, {len(mesh.indices) // 3:,} tris, {kind}, "
                         f"{nprim} parts, {nimg} textures ({time.time() - t0:.1f}s)"), True
        except Exception as e:
            msg, good = f"  {f.stem}: ERROR {e}", False
        with lock:
            state["ok" if good else "fail"] += 1
            state["done"] += 1
            log(msg)
            print(f"\rMeshes {state['done']}/{len(meshes)}", end="", file=sys.stderr, flush=True)

    with ThreadPoolExecutor(max_workers=jobs) as ex:
        list(ex.map(one, meshes))
    ok, fail = state["ok"], state["fail"]
    print(file=sys.stderr)
    log(f"Meshes done: {ok} GLB written, {fail} failed")
    return ok, fail


if __name__ == "__main__":
    args = list(sys.argv[1:])
    size = 2048
    if "--max-texture" in args:
        i = args.index("--max-texture")
        size = int(args[i + 1])  # 0 = full size
        del args[i:i + 2]
    if len(args) != 2:
        sys.exit(__doc__)
    convert_folder(args[0], args[1], size)
