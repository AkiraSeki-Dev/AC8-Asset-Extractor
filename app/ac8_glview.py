"""Native 3D model viewer for the app: OpenGL drawn straight into a Tk frame (no browser).

Windows: a real OpenGL window inside the Tk frame (pyopengltk + PyOpenGL, wgl).
Elsewhere: renders off-screen (EGL) and shows the frames in a Tk image — same renderer, same look.

    load_glb(path)       -> Model   (CPU only, safe in a worker thread)
    GLView(parent, ...)             Tk widget; view.set_model(model), view.opts, view.request()
"""
import hashlib
import io
import json
import math
import os
import struct
import time

import tkinter as tk

import numpy as np

IS_WIN = os.name == "nt"
OFFSCREEN = not IS_WIN or os.environ.get("AC8_GL_OFFSCREEN") == "1"
if OFFSCREEN:
    os.environ.setdefault("PYOPENGL_PLATFORM", "egl")
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        os.environ.setdefault("EGL_PLATFORM", "surfaceless")

MAX_VIEW_TEXTURE = 4096  # textures bigger than this are shrunk for the viewer (files are untouched)


# ====================================================================== maths (row-major numpy, column vectors)

def quat_mat(q):
    x, y, z, w = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), 0],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), 0],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), 0],
        [0, 0, 0, 1]], np.float64)


def trs(t, r, s):
    m = quat_mat(r)
    m[:3, :3] *= np.asarray(s, np.float64)[None, :]
    m[:3, 3] = t
    return m


def euler_mat(rx, ry, rz):
    """Degrees, applied X then Y then Z (local bone axes)."""
    a, b, c = (math.radians(v) for v in (rx, ry, rz))
    ca, sa, cb, sb, cc, sc = math.cos(a), math.sin(a), math.cos(b), math.sin(b), math.cos(c), math.sin(c)
    X = np.array([[1, 0, 0, 0], [0, ca, -sa, 0], [0, sa, ca, 0], [0, 0, 0, 1]])
    Y = np.array([[cb, 0, sb, 0], [0, 1, 0, 0], [-sb, 0, cb, 0], [0, 0, 0, 1]])
    Z = np.array([[cc, -sc, 0, 0], [sc, cc, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
    return Z @ Y @ X


def perspective(fov_deg, aspect, near, far):
    f = 1.0 / math.tan(math.radians(fov_deg) / 2)
    return np.array([[f / aspect, 0, 0, 0], [0, f, 0, 0],
                     [0, 0, (far + near) / (near - far), 2 * far * near / (near - far)], [0, 0, -1, 0]])


def look_at(eye, target, up=(0, 1, 0)):
    eye, target, up = (np.asarray(v, np.float64) for v in (eye, target, up))
    f = target - eye
    f /= np.linalg.norm(f)
    s = np.cross(f, up)
    if np.linalg.norm(s) < 1e-6:
        s = np.cross(f, (0, 0, 1))
    s /= np.linalg.norm(s)
    u = np.cross(s, f)
    m = np.identity(4)
    m[0, :3], m[1, :3], m[2, :3] = s, u, -f
    m[:3, 3] = -m[:3, :3] @ eye
    return m


# ====================================================================== GLB loading

_CT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
_NC = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


class Prim:
    __slots__ = ("pos", "nrm", "uv", "uv1", "joints", "weights", "idx", "mat", "node")


class Material:
    def __init__(self, name):
        self.name = name
        self.base = (1.0, 1.0, 1.0, 1.0)
        self.metal, self.rough = 1.0, 1.0
        self.alpha, self.cutoff, self.double = "OPAQUE", 0.5, False
        self.tex = {}         # role -> image index ("base", "normal", "mr")
        self.has_occ = False


class Model:
    def __init__(self, path):
        self.path = path
        self.name = os.path.splitext(os.path.basename(path))[0]
        self.prims, self.materials, self.images = [], [], {}
        self.image_names, self.image_sizes, self.thumbs = {}, {}, {}  # per image index
        self.image_src = {}    # per image index: (glb path, bufferView, uri) — to load it again at full size
        self.nodes = []        # dicts: name, children, t, r, s, mesh, skin
        self.roots = []
        self.joints = []       # node index per skin joint
        self.ibm = None        # (n, 4, 4)
        self.skinned_nodes = set()
        self.lo = self.hi = np.zeros(3)
        self.mat_src = None    # after merge_models: source model name per material


def load_full_image(src):
    """A texture of a GLB at full size (PIL image) from Model.image_src."""
    from PIL import Image
    path, bv, uri = src
    if bv is None:
        return Image.open(os.path.join(os.path.dirname(path), uri))
    with open(path, "rb") as fh:
        data = fh.read()
    off, j, binc = 12, None, b""
    while off < len(data):
        clen, ctype = struct.unpack_from("<I4s", data, off)
        if ctype == b"JSON":
            j = json.loads(data[off + 8:off + 8 + clen])
        elif ctype == b"BIN\0":
            binc = data[off + 8:off + 8 + clen]
        off += 8 + clen
    v = j["bufferViews"][bv]
    o = v.get("byteOffset", 0)
    return Image.open(io.BytesIO(binc[o:o + v["byteLength"]]))


def load_glb(path, max_texture=MAX_VIEW_TEXTURE, cache=None):
    """cache: a dict shared between loads so a texture several GLBs carry is decoded (and later uploaded) once."""
    from PIL import Image
    with open(path, "rb") as fh:
        data = fh.read()
    magic, _ver, _length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF":
        raise ValueError("not a GLB file")
    off, j, binc = 12, None, b""
    while off < len(data):
        clen, ctype = struct.unpack_from("<I4s", data, off)
        chunk = data[off + 8:off + 8 + clen]
        if ctype == b"JSON":
            j = json.loads(chunk)
        elif ctype == b"BIN\0":
            binc = chunk
        off += 8 + clen
    views = j.get("bufferViews", [])
    accs = j.get("accessors", [])

    def view_bytes(vi):
        v = views[vi]
        o = v.get("byteOffset", 0)
        return binc[o:o + v["byteLength"]], v.get("byteStride")

    def acc(ai):
        a = accs[ai]
        dt, nc, cnt = _CT[a["componentType"]], _NC[a["type"]], a["count"]
        if "bufferView" not in a:
            return np.zeros((cnt, nc), dt)
        raw, stride = view_bytes(a["bufferView"])
        o = a.get("byteOffset", 0)
        isz = np.dtype(dt).itemsize
        if stride and stride != isz * nc:
            arr = np.ndarray((cnt, nc), dt, buffer=raw, offset=o, strides=(stride, isz)).copy()
        else:
            arr = np.frombuffer(raw, dt, cnt * nc, o).reshape(cnt, nc).copy()
        if a.get("normalized") and dt != np.float32:
            arr = arr.astype(np.float32) / np.iinfo(dt).max
        return arr

    m = Model(path)
    for i, md in enumerate(j.get("materials", [])):
        mat = Material(md.get("name", f"Material_{i}"))
        pbr = md.get("pbrMetallicRoughness", {})
        mat.base = tuple(pbr.get("baseColorFactor", (1, 1, 1, 1)))
        mat.metal = pbr.get("metallicFactor", 1.0)
        mat.rough = pbr.get("roughnessFactor", 1.0)
        mat.alpha = md.get("alphaMode", "OPAQUE")
        mat.cutoff = md.get("alphaCutoff", 0.5)
        mat.double = md.get("doubleSided", False)
        texs = j.get("textures", [])
        for role, ref in (("base", pbr.get("baseColorTexture")), ("normal", md.get("normalTexture")),
                          ("mr", pbr.get("metallicRoughnessTexture")), ("occ", md.get("occlusionTexture"))):
            if ref and ref.get("index", -1) < len(texs) and "source" in texs[ref["index"]]:
                mat.tex[role] = texs[ref["index"]]["source"]
        mat.has_occ = "occ" in mat.tex and mat.tex.get("occ") == mat.tex.get("mr")
        m.materials.append(mat)
    if not m.materials:
        m.materials.append(Material("Default"))
        m.materials[0].base, m.materials[0].metal, m.materials[0].rough = (0.6, 0.6, 0.62, 1), 0.0, 0.6

    # only decode the images the materials use
    used = {i for mat in m.materials for k, i in mat.tex.items() if k != "occ"}
    for ii in sorted(used):
        try:
            im = j["images"][ii]
            m.image_names[ii] = im.get("name") or os.path.splitext(os.path.basename(im.get("uri", "")))[0] or \
                f"image {ii}"
            m.image_src[ii] = (os.fspath(path), im.get("bufferView"), im.get("uri"))
            if "bufferView" in im:
                raw, _ = view_bytes(im["bufferView"])
                key = hashlib.blake2b(raw, digest_size=16).digest()
            else:
                raw, key = None, os.path.abspath(os.path.join(os.path.dirname(path), im["uri"]))
            if cache is not None and key in cache:
                m.image_sizes[ii], m.thumbs[ii], m.images[ii] = cache[key]
                continue
            img = Image.open(io.BytesIO(raw) if raw is not None else key)
            img = img.convert("RGBA")
            m.image_sizes[ii] = img.size
            th = img.copy()
            th.thumbnail((160, 160))
            m.thumbs[ii] = th
            if max_texture and max(img.size) > max_texture:
                img.thumbnail((max_texture, max_texture))
            m.images[ii] = np.asarray(img, np.uint8)
            if cache is not None:
                cache[key] = (m.image_sizes[ii], m.thumbs[ii], m.images[ii])
        except Exception:
            pass

    for i, nd in enumerate(j.get("nodes", [])):
        if "matrix" in nd:
            mm = np.array(nd["matrix"], np.float64).reshape(4, 4).T
            t = mm[:3, 3].copy()
            sc = np.linalg.norm(mm[:3, :3], axis=0)
            rot = mm[:3, :3] / np.where(sc == 0, 1, sc)
            q = _mat_quat(rot)
        else:
            t = np.array(nd.get("translation", (0, 0, 0)), np.float64)
            q = np.array(nd.get("rotation", (0, 0, 0, 1)), np.float64)
            sc = np.array(nd.get("scale", (1, 1, 1)), np.float64)
        m.nodes.append({"name": nd.get("name", f"node{i}"), "children": nd.get("children", []), "t": t, "r": q,
                        "s": sc, "mesh": nd.get("mesh"), "skin": nd.get("skin"), "parent": -1})
    for i, nd in enumerate(m.nodes):
        for c in nd["children"]:
            m.nodes[c]["parent"] = i
    scene = j.get("scenes", [{}])[j.get("scene", 0)] if j.get("scenes") else {}
    m.roots = scene.get("nodes") or [i for i, n in enumerate(m.nodes) if n["parent"] < 0]

    skins = j.get("skins", [])
    if skins:
        sk = skins[0]
        m.joints = list(sk["joints"])
        if "inverseBindMatrices" in sk:
            m.ibm = acc(sk["inverseBindMatrices"]).astype(np.float64).reshape(-1, 4, 4).transpose(0, 2, 1)
        else:
            m.ibm = np.tile(np.identity(4), (len(m.joints), 1, 1))

    meshes = j.get("meshes", [])
    mesh_nodes = [i for i, n in enumerate(m.nodes) if n["mesh"] is not None] or ([None] if meshes else [])
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    world = node_worlds(m, {})
    for ni in mesh_nodes:
        mi = m.nodes[ni]["mesh"] if ni is not None else 0
        skinned = ni is not None and m.nodes[ni]["skin"] is not None and m.joints
        if skinned:
            m.skinned_nodes.add(ni)
        for pr in meshes[mi].get("primitives", []):
            if pr.get("mode", 4) != 4:
                continue
            at = pr["attributes"]
            p = Prim()
            p.pos = acc(at["POSITION"]).astype(np.float32)
            n = len(p.pos)
            p.nrm = acc(at["NORMAL"]).astype(np.float32) if "NORMAL" in at else None
            p.uv = acc(at["TEXCOORD_0"]).astype(np.float32) if "TEXCOORD_0" in at else np.zeros((n, 2), np.float32)
            p.uv1 = acc(at["TEXCOORD_1"]).astype(np.float32) if "TEXCOORD_1" in at else None  # decal slots
            p.joints = acc(at["JOINTS_0"]).astype(np.uint16) if skinned and "JOINTS_0" in at else None
            p.weights = acc(at["WEIGHTS_0"]).astype(np.float32) if skinned and "WEIGHTS_0" in at else None
            p.idx = acc(pr["indices"]).ravel() if "indices" in pr else np.arange(n, dtype=np.uint32)
            p.idx = p.idx.astype(np.uint32 if n > 65535 else np.uint16)
            if p.nrm is None:
                p.nrm = flat_normals(p.pos, p.idx)
            p.mat = pr.get("material", 0) if pr.get("material", 0) < len(m.materials) else 0
            p.node = ni
            m.prims.append(p)
            pts = p.pos if (skinned or ni is None) else (p.pos @ world[ni][:3, :3].T + world[ni][:3, 3])
            if len(pts):
                lo, hi = np.minimum(lo, pts.min(0)), np.maximum(hi, pts.max(0))
    if not np.isfinite(lo).all():
        lo, hi = np.full(3, -1.0), np.full(3, 1.0)
    m.lo, m.hi = lo, hi
    return m


def _mat_quat(r):
    t = np.trace(r)
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        return np.array([(r[2, 1] - r[1, 2]) / s, (r[0, 2] - r[2, 0]) / s, (r[1, 0] - r[0, 1]) / s, 0.25 * s])
    i = int(np.argmax(np.diag(r)))
    j_, k = (i + 1) % 3, (i + 2) % 3
    s = math.sqrt(1.0 + r[i, i] - r[j_, j_] - r[k, k]) * 2
    q = np.zeros(4)
    q[i] = 0.25 * s
    q[j_] = (r[j_, i] + r[i, j_]) / s
    q[k] = (r[k, i] + r[i, k]) / s
    q[3] = (r[k, j_] - r[j_, k]) / s
    return q


def flat_normals(pos, idx):
    tri = idx.reshape(-1, 3).astype(np.int64)
    fn = np.cross(pos[tri[:, 1]] - pos[tri[:, 0]], pos[tri[:, 2]] - pos[tri[:, 0]])
    acc = np.zeros_like(pos, dtype=np.float64)
    for k in range(3):
        np.add.at(acc, tri[:, k], fn)
    ln = np.linalg.norm(acc, axis=1, keepdims=True)
    return (acc / np.where(ln == 0, 1, ln)).astype(np.float32)


def node_worlds(m, pose):
    """World matrix of every node; pose = {node index: (rx, ry, rz) degrees} added on top of the rest pose."""
    world = [None] * len(m.nodes)

    def walk(i, parent):
        nd = m.nodes[i]
        local = trs(nd["t"], nd["r"], (1, 1, 1))
        if i in pose:
            local = local @ euler_mat(*pose[i])
        local[:3, :3] = local[:3, :3] * np.asarray(nd["s"])[None, :]
        world[i] = parent @ local
        for c in nd["children"]:
            walk(c, world[i])

    for r in m.roots:
        walk(r, np.identity(4))
    for i in range(len(world)):
        if world[i] is None:
            walk(i, np.identity(4))
    return world


def merge_models(main, extras):
    """Show several GLBs as one model (airframe + landing gear + refuel probe …). The extra parts are posed
    statically (their own skeletons stay in the rest pose); their materials keep their names, so a livery that
    re-textures the airframe's materials re-textures them too."""
    if main.mat_src is None:
        main.mat_src = [main.name] * len(main.materials)
    have = {id(v): i for i, v in main.images.items()}  # textures shared through load_glb's cache: keep one copy
    for k, ex in enumerate(extras, 1):
        mo, io = len(main.materials), 100000 * k
        world = node_worlds(ex, {})
        remap = {}
        for i, v in ex.images.items():
            if id(v) in have:
                remap[i] = have[id(v)]
            else:
                remap[i] = have[id(v)] = i + io
                main.images[i + io], main.image_names[i + io] = v, ex.image_names.get(i, f"image {i}")
                main.image_sizes[i + io], main.thumbs[i + io] = ex.image_sizes.get(i), ex.thumbs.get(i)
                if i in ex.image_src:
                    main.image_src[i + io] = ex.image_src[i]
        for i, v in ex.image_names.items():  # images that failed to decode: keep their names
            if i not in remap:
                main.image_names[i + io] = v
        for mat in ex.materials:
            mat.tex = {r: remap.get(i, i + io) for r, i in mat.tex.items()}
            main.materials.append(mat)
            main.mat_src.append(ex.name)
        for p in ex.prims:
            if p.node is not None and p.node not in ex.skinned_nodes:
                w = world[p.node]
                p.pos = (p.pos @ w[:3, :3].T + w[:3, 3]).astype(np.float32)
                p.nrm = (p.nrm @ np.linalg.inv(w[:3, :3])).astype(np.float32)
            p.node, p.joints, p.weights = None, None, None
            p.mat += mo
            main.prims.append(p)
        main.lo, main.hi = np.minimum(main.lo, ex.lo), np.maximum(main.hi, ex.hi)
        main.name += " + " + ex.name.split("_")[-1]
    return main


def _rgba(path, size=None):
    from PIL import Image
    im = Image.open(path).convert("RGBA")
    if size and im.size != size:
        im = im.resize(size, Image.BILINEAR)
    return np.asarray(im, np.uint8)


def livery_spec(model, livery, final_dir, max_size=2048, atlas_cell=512, log=print, textures=True):
    """CPU side of a livery (worker thread): load and prepare its textures for the model's livery materials
    (names ending in _Decal, one per UDIM tile) and pack its emblems into one atlas. -> spec for set_livery."""
    import re as _re
    from PIL import Image
    final_dir = os.fspath(final_dir)
    tex = livery.get("textures", {})

    def path_for(role, tile):
        files = tex.get(role, {}).get("files", {})
        rel = files.get(tile) or files.get("") or (files.get("1001") if not tile else None)
        return os.path.join(final_dir, rel) if rel else None

    mats = {}
    for mi, mat in enumerate(model.materials if textures else []):
        m = _re.match(r"^(.*_Decal)(?:\.(10\d\d))?$", mat.name)
        if not m:
            continue
        tile = m.group(2) or ""
        roles = {}
        b = path_for("Basecolor", tile)
        if b and os.path.exists(b):
            img = Image.open(b).convert("RGBA")
            if max(img.size) > max_size:
                img.thumbnail((max_size, max_size))
            roles["base"] = np.asarray(img, np.uint8)
        size = (roles["base"].shape[1], roles["base"].shape[0]) if "base" in roles else None
        n_path = path_for("Normalmap", tile)
        ao = None
        if n_path and os.path.exists(n_path):
            a = _rgba(n_path, size).astype(np.float32) / 127.5 - 1.0
            x, y = a[..., 0], -a[..., 1]  # DirectX -> OpenGL, like the GLB converter
            z = np.sqrt(np.clip(1.0 - x * x - y * y, 0.0, 1.0))
            nrm = np.stack([x, y, z], -1) * 127.5 + 127.5
            roles["normal"] = np.dstack([nrm.clip(0, 255).astype(np.uint8), np.full(x.shape, 255, np.uint8)])
            if "_NO" in os.path.basename(n_path):
                ao = ((a[..., 2] + 1.0) * 127.5).clip(0, 255).astype(np.uint8)
        mr_path = path_for("Livemap", tile)
        if mr_path and os.path.exists(mr_path):
            a = _rgba(mr_path, size)
            orm = np.empty_like(a)
            orm[..., 0] = ao if ao is not None else 255  # glTF order: R occlusion, G roughness, B metal
            orm[..., 1] = a[..., 1]
            orm[..., 2] = a[..., 0]
            orm[..., 3] = 255
            roles["mr"] = orm
        mk = path_for("LayerMaskMap", tile)
        if mk and os.path.exists(mk):
            roles["mask"] = _rgba(mk, size)
        if roles:
            mats[mi] = roles
    layers = None
    cols = livery.get("colors", {})
    if "LayerMaskMap" in tex:
        layers = [cols.get(f"Layer0{i}_Color", [0.5, 0.5, 0.5, 1])[:3] for i in (1, 2, 3)]
    atlas, slots, missing = None, {}, []
    decals = [(k, d) for k, d in sorted(livery.get("decals", {}).items()) if d.get("row") is not None]
    if decals:
        grid = 4
        atlas = np.zeros((atlas_cell * grid, atlas_cell * grid, 4), np.uint8)
        cache = {}
        n = 0
        for key, d in decals:
            files = d.get("files") or {}
            rel = files.get("") or (next(iter(files.values())) if files else None)
            if not rel or not os.path.exists(os.path.join(final_dir, rel)):
                missing.append(d["texture"])
                continue
            if rel not in cache:
                if n >= grid * grid:
                    continue
                cx, cy = (n % grid) * atlas_cell, (n // grid) * atlas_cell
                em = _rgba(os.path.join(final_dir, rel), (atlas_cell, atlas_cell)).copy()
                if em[..., 3].min() >= 250:  # no alpha: black is the see-through part
                    em[..., 3] = em[..., :3].max(-1)
                atlas[cy:cy + atlas_cell, cx:cx + atlas_cell] = em
                inset = 0.5 / (atlas_cell * grid)
                cache[rel] = (cx / (atlas_cell * grid) + inset, cy / (atlas_cell * grid) + inset,
                              1.0 / grid - 2 * inset, 1.0 / grid - 2 * inset)
                n += 1
            slots[(d["col"], d["row"])] = tuple(cache[rel]) + (
                float(d.get("scale", 1.0)) or 1.0, float(d.get("tx", 0.0)), float(d.get("ty", 0.0)),
                1.0 if d.get("flip") else 0.0, float(d.get("area_row", d["row"])))
        if not slots:
            atlas = None
    numbers = number_spec(livery.get("numbers"), font_sheet(livery.get("_font_png")), slot_bounds(model))
    return {"mats": mats, "layers": layers, "atlas": atlas, "slots": slots, "missing": sorted(set(missing)),
            "numbers": numbers, "textures": textures}


_FONT_CACHE = {}


def dilate(rgba, covered, steps=16):
    """Grow the baked islands outwards by `steps` pixels (copies the nearest painted pixel), so the edges don't
    show seams when the texture is mip-mapped. -> (RGBA, covered)."""
    h, w = covered.shape
    idx = np.where(covered.ravel(), np.arange(h * w, dtype=np.int64), -1).reshape(h, w)
    if not covered.any():
        return rgba, covered
    for _ in range(steps):
        empty = idx < 0
        if not empty.any():
            break
        for sh, ax in ((1, 0), (-1, 0), (1, 1), (-1, 1)):
            nb = np.roll(idx, sh, ax)
            take = empty & (nb >= 0)
            idx[take] = nb[take]
            empty &= ~take
    got = idx >= 0
    flat = rgba.reshape(-1, rgba.shape[-1])
    out = rgba.copy()
    out[got] = flat[idx[got]]
    return out, got


def font_sheet(png=None):
    """Digit sheet -> (RGBA array, [per font: 10 glyph rects (u, v, w, h) for 0-9], source).
    The game's T_Aircraft_Number is a grid: one row per digit (0 at the top, 9 at the bottom) and one column per
    font (Tail/Body/Wing_Font_Select picks the column). It has no real alpha - the digits are bright on black - so
    the brightness is used as the alpha. Without the game sheet a stand-in is drawn from the app's own font."""
    key = png or ""
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    from PIL import Image, ImageDraw, ImageFont
    result = None
    if png and os.path.exists(png):
        try:
            result = _game_font_sheet(Image.open(png).convert("RGBA"))
        except Exception:
            result = None
    if result is None:  # stand-in laid out like the game's sheet: 10 x 10 cells, digit d in row d
        here = os.path.dirname(os.path.abspath(__file__))
        N = 1020
        cell = N // 10
        sheet = Image.new("RGBA", (N, N), (255, 255, 255, 0))
        d = ImageDraw.Draw(sheet)
        fonts = []
        for col, face in enumerate(("Outfit-Bold.ttf", "Outfit-Regular.ttf")):
            try:
                ft = ImageFont.truetype(os.path.join(here, "fonts", face), int(cell * 0.78))
            except OSError:
                ft = ImageFont.load_default()
            for dgt in range(10):
                d.text((col * cell + cell / 2, dgt * cell + cell * 0.55), str(dgt), font=ft,
                       fill=(255, 255, 255, 255), anchor="mm")
            fonts.append([(col / 10, dgt / 10, 0.1, 0.1) for dgt in range(10)])
        result = (np.asarray(sheet, np.uint8), fonts, "built-in")
    _FONT_CACHE[key] = result
    return result


def _game_font_sheet(img):
    """T_Aircraft_Number: 10 rows of digits (0 at the top), one column per font. The game reads it as a 10 x 10 grid
    (its shader divides the digit and font numbers by 10), so each digit is a square cell one tenth of the sheet
    high; that square, centred on the font's column, is what gets drawn."""
    a = np.asarray(img)
    H, W = a.shape[:2]
    flat_alpha = int(a[..., 3].min()) >= 250 or int(a[..., 3].max()) <= 5
    alpha = a[..., :3].max(-1) if flat_alpha else a[..., 3]
    ink = alpha > 40
    cols = _runs(ink.any(0))
    if not 1 <= len(cols) <= 10:
        n = 4
        cols = [(int(j * W / n), int((j + 1) * W / n)) for j in range(n)]
    cell = H / 10.0
    fonts = []
    for x0, x1 in cols:
        cx = (x0 + x1) / 2.0
        fonts.append([((cx - cell / 2) / W, d * cell / H, cell / W, cell / H) for d in range(10)])
    if flat_alpha:  # the game's sheet: red = the digit, green = the digit plus its outline (the "Edge" option)
        fill, edge = a[..., 0], np.maximum(a[..., 1], a[..., 0])
    else:
        fill = edge = alpha
    out = np.dstack([np.full(alpha.shape, 255, np.uint8), edge.astype(np.uint8),
                     np.full(alpha.shape, 255, np.uint8), fill.astype(np.uint8)])
    return out, fonts, "game"


def _runs(mask):
    """[(start, end)] of the True stretches in a 1-D mask (glyph rows / columns)."""
    out, start = [], None
    for i, v in enumerate(list(mask) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start > 2:
                out.append((start, i))
            start = None
    return out


def slot_bounds(model):
    """Where each decal/number slot really is inside its UV1 tile: {(col, row): (u0, v0, u1, v1)}."""
    import re as _re
    lo, hi = {}, {}
    for p in model.prims:
        if p.uv1 is None or not _re.search(r"_Decal(\.10\d\d)?$", model.materials[p.mat].name):
            continue
        tri = p.uv1[p.idx.reshape(-1, 3).astype(np.int64)]
        e1, e2 = tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]
        real = np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]) > 1e-7
        tri = tri[real]
        if not len(tri):
            continue
        tile = np.floor(tri.mean(1)).astype(int)
        for key in {tuple(t) for t in tile}:
            if key[0] not in (-1, 0) or not -4 <= key[1] <= 2:
                continue
            sel = (tile == key).all(1)
            pts = tri[sel].reshape(-1, 2) - np.asarray(key, np.float32)
            a, b = pts.min(0), pts.max(0)
            lo[key] = np.minimum(lo.get(key, a), a)
            hi[key] = np.maximum(hi.get(key, b), b)
    out = {k: (float(lo[k][0]), float(lo[k][1]), float(hi[k][0]), float(hi[k][1])) for k in lo}
    # the slot's real shape (a fin top is swept back, a wing slot is a trapezoid): 64x64 coverage per slot
    masks = {}
    n = 64
    gx, gy = np.meshgrid((np.arange(n) + 0.5) / n, (np.arange(n) + 0.5) / n)
    for p in model.prims:
        if p.uv1 is None or not _re.search(r"_Decal(\.10\d\d)?$", model.materials[p.mat].name):
            continue
        tri = p.uv1[p.idx.reshape(-1, 3).astype(np.int64)]
        tile = np.floor(tri.mean(1)).astype(int)
        for t, key in zip(tri, map(tuple, tile)):
            if key not in out:
                continue
            a, b, c = t - np.asarray(key, np.float32)
            d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
            if abs(d) < 1e-9:
                continue
            l1 = ((b[1] - c[1]) * (gx - c[0]) + (c[0] - b[0]) * (gy - c[1])) / d
            l2 = ((c[1] - a[1]) * (gx - c[0]) + (a[0] - c[0]) * (gy - c[1])) / d
            inside = (l1 >= 0) & (l2 >= 0) & (l1 + l2 <= 1)
            masks[key] = masks.get(key, np.zeros((n, n), bool)) | inside
    return {k: v + (masks.get(k),) for k, v in out.items()}


def number_spec(numbers, sheet, bounds=None):
    """The aircraft number exactly as the game's livery shader draws it (MP_*_Decal base pass pixel shader,
    decoded from the compiled game shader). Per area (row r = 0 tail, 1 nose, 2 wing) and side (UV1 tile column
    -1 = left, 0 = right):
        p    = (U within the tile + offsetX_L (left) / offsetX_R (right),  V within the tile + offsetY)
        p.x += (p.y - 1) * SlantDegree   (right side; the left side uses -SlantDegree)
        P    = (p - 0.5) / scale + 0.5
    and digit k is drawn where (P.x + shift_k, P.y) lies in [1, 1.1]^2, sampling T_Aircraft_Number's cell
    (Font_Select, digit) of its 10 x 10 grid. Number01 has shift 0; Number02 shift Number_Interval + e01 + e02;
    Number03 that plus Number_Interval + e02 + e03, where e = 0.015 for a digit 1 (narrow 1s sit closer).
    The red channel is the digit, green the digit with its outline (drawn in Edge_Color when Edge_Switch is on)."""
    if not numbers or not numbers.get("digits") or numbers.get("enabled") is False:
        return None
    atlas, fonts, source = sheet
    place = np.zeros((3, 2, 4), np.float32)    # offsetX, offsetY, slant, scale
    digs = np.full((3, 2, 4), -1.0, np.float32)  # font column, Number01, Number02, Number03 (-1 = hidden)
    shift = np.zeros((3, 2, 4), np.float32)    # shift of each digit, w = 1 when drawn
    colors = np.zeros((3, 2, 3), np.float32)
    edges = np.zeros((3, 2, 4), np.float32)
    raw = [int(c) for c in numbers.get("digits", "") if c.isdigit()][:3]
    two = len(raw) == 2
    d = ([None] + raw) if two else (raw + [None] * (3 - len(raw)))   # two digits: Number01 isn't drawn
    gap = float(numbers.get("interval", -0.04))
    e = [0.015 if x == 1 else 0.0 for x in d]
    s1 = gap + e[0] + e[1]
    s2 = s1 + gap + e[1] + e[2]
    drawn = False
    for area, a in numbers.get("areas", {}).items():
        row = a.get("row")
        if row not in (0, 1, 2) or not a.get("show", True):
            continue
        scale = float(a.get("scale") or 0)
        if scale <= 0.001:
            continue
        g = a.get("game") or {}
        x_l = a.get("x_left", g.get("offsetX_L", 0.5))
        x_r = a.get("x_right", g.get("offsetX_R", 0.5))
        y = a.get("y_up", g.get("offsetY", 0.5))
        sl = float(a.get("slant", 0.0))
        font = max(int(a.get("font", 0)), 0)
        if source != "game":
            font = min(font, len(fonts) - 1)
        for side in (0, 1):
            place[row, side] = (float(x_l if side == 0 else x_r), float(y), -sl if side == 0 else sl, scale)
            digs[row, side] = (font, *[(-1 if x is None else x) for x in d])
            shift[row, side] = (0.0, s1, s2, 1.0)
            colors[row, side] = (a.get("color_L") if side == 0 else a.get("color_R")) or (0.33, 0.33, 0.33)
            if a.get("edge"):
                edges[row, side] = tuple((a.get("edge_color") or (0.95, 0.95, 0.95))[:3]) + (1.0,)
        drawn = True
    if not drawn:
        return None
    return {"atlas": atlas, "place": place, "digits": digs, "shift": shift, "colors": colors, "edges": edges,
            "source": source}


def model_stats(m):
    verts = sum(len(p.pos) for p in m.prims)
    tris = sum(len(p.idx) // 3 for p in m.prims)
    size = m.hi - m.lo
    return {"verts": verts, "tris": tris, "materials": len({p.mat for p in m.prims}), "textures": len(m.images),
            "bones": len(m.joints), "size": size,
            "file_mb": os.path.getsize(m.path) / 1e6 if os.path.exists(m.path) else 0}


# ====================================================================== renderer (GL thread only)

VERT = """
#version 330
layout(location=0) in vec3 aPos;
layout(location=1) in vec3 aNrm;
layout(location=2) in vec2 aUV;
layout(location=3) in vec4 aJoints;
layout(location=4) in vec4 aWeights;
layout(location=5) in vec2 aUV1;
uniform mat4 uVP;
uniform mat4 uModel;
uniform bool uSkin;
uniform sampler2D uBones;
uniform bool uBake;         // texture baking: draw the mesh flat in its UV layout instead of in 3D
uniform vec2 uBakeOff;      // UDIM tile of the material being baked
out vec3 vPos;
out vec3 vNrm;
out vec2 vUV;
out vec2 vUV1;
mat4 bone(float j) {
    int i = int(j + 0.5);
    return mat4(texelFetch(uBones, ivec2(0, i), 0), texelFetch(uBones, ivec2(1, i), 0),
                texelFetch(uBones, ivec2(2, i), 0), texelFetch(uBones, ivec2(3, i), 0));
}
void main() {
    mat4 m = uModel;
    if (uSkin) {
        m = aWeights.x * bone(aJoints.x) + aWeights.y * bone(aJoints.y)
          + aWeights.z * bone(aJoints.z) + aWeights.w * bone(aJoints.w);
    }
    vec4 p = m * vec4(aPos, 1.0);
    vPos = p.xyz;
    vNrm = mat3(m) * aNrm;
    vUV = aUV;
    vUV1 = aUV1;
    gl_Position = uBake ? vec4((aUV - uBakeOff) * 2.0 - 1.0, 0.0, 1.0) : uVP * p;
}
"""

FRAG = """
#version 330
in vec3 vPos;
in vec3 vNrm;
in vec2 vUV;
in vec2 vUV1;
out vec4 oColor;
uniform bool uHasMask;      // livery paint: mask R/G/B areas painted with uLayer[0..2]
uniform sampler2D tMask;
uniform vec3 uLayer[3];
uniform bool uHasDecals;    // livery emblems: UV1 tile (column -1/0, row -4..2) -> slot rect in the decal atlas
uniform sampler2D tDecal;
uniform vec4 uSlot[14];      // per emblem slot (Left/Right x Tail..Add03): its rect in the decal atlas
uniform vec4 uSlotXf[14];    // game placement: DecalScale, TranslateX, TranslateY, FlipRight
uniform float uSlotArea[14]; // UV1 tile row the slot draws on (DecalAreaIndex)
uniform bool uHasNumbers;   // aircraft number: 3 areas (tail/body/wing rows 0..2) x 2 sides x 3 digits
uniform sampler2D tFont;
uniform vec4 uNumPlace[6];  // per area x side: offsetX, offsetY, slant, scale
uniform vec4 uNumDigits[6]; // font column, Number01, Number02, Number03 (-1 = not drawn)
uniform vec4 uNumShift[6];  // shift of each digit; w = 1 when the area is drawn
uniform vec3 uNumCol[6];
uniform vec4 uNumEdge[6];     // outline colour, w = 1 when <Area>_Edge_Switch is on
uniform int uMode;          // 0 lit, 1 colour, 2 normals, 3 clay, 4 flat colour (wireframe), 5 one texture, 6 bake
uniform int uShow;          // mode 5: 0 colour map, 1 normal map, 2 metal/rough/AO map
uniform vec4 uFlat;
uniform vec4 uBase;
uniform float uMetal;
uniform float uRough;
uniform bool uHasBase;
uniform bool uHasNrm;
uniform bool uHasMR;
uniform bool uHasOcc;
uniform sampler2D tBase;
uniform sampler2D tNrm;
uniform sampler2D tMR;
uniform int uAlpha;         // 0 opaque, 1 mask, 2 blend
uniform float uCutoff;
uniform vec3 uEye;
uniform vec3 uKey;
uniform vec3 uFill;
const float PI = 3.14159265;

vec3 sky(vec3 d) {
    float t = clamp(d.y * 0.5 + 0.5, 0.0, 1.0);
    vec3 ground = vec3(0.16, 0.15, 0.14), horizon = vec3(0.52, 0.53, 0.55), top = vec3(0.40, 0.47, 0.58);
    return t < 0.5 ? mix(ground, horizon, t * 2.0) : mix(horizon, top, (t - 0.5) * 2.0);
}

vec3 perturb(vec3 N, vec3 p, vec2 uv, vec3 m) {
    vec3 q0 = dFdx(p), q1 = dFdy(p);
    vec2 st0 = dFdx(uv), st1 = dFdy(uv);
    vec3 q1perp = cross(q1, N), q0perp = cross(N, q0);
    vec3 T = q1perp * st0.x + q0perp * st1.x;
    vec3 B = q1perp * st0.y + q0perp * st1.y;
    float det = max(dot(T, T), dot(B, B));
    float s = det == 0.0 ? 0.0 : inversesqrt(det);
    return normalize(T * (m.x * s) + B * (m.y * s) + N * m.z);
}

vec3 light(vec3 N, vec3 V, vec3 L, vec3 col, vec3 diff, vec3 F0, float rough) {
    float NdL = max(dot(N, L), 0.0);
    if (NdL <= 0.0) return vec3(0.0);
    vec3 H = normalize(L + V);
    float NdV = max(dot(N, V), 1e-4), NdH = max(dot(N, H), 0.0), VdH = max(dot(V, H), 0.0);
    float a = rough * rough, a2 = a * a;
    float d = NdH * NdH * (a2 - 1.0) + 1.0;
    float D = a2 / (PI * d * d);
    float k = (rough + 1.0) * (rough + 1.0) / 8.0;
    float G = (NdL / (NdL * (1.0 - k) + k)) * (NdV / (NdV * (1.0 - k) + k));
    vec3 F = F0 + (1.0 - F0) * pow(1.0 - VdH, 5.0);
    vec3 spec = D * G * F / max(4.0 * NdL * NdV, 1e-4);
    return (diff / PI * (1.0 - F) + spec) * NdL * col;
}

vec3 aces(vec3 x) {
    return clamp((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14), 0.0, 1.0);
}

void main() {
    if (uMode == 4) { oColor = uFlat; return; }
    if (uMode == 5) {
        bool has = uShow == 0 ? uHasBase : (uShow == 1 ? uHasNrm : uHasMR);
        if (!has) {  // no such texture on this part: dim checkerboard
            float c = mod(floor(gl_FragCoord.x / 8.0) + floor(gl_FragCoord.y / 8.0), 2.0);
            oColor = vec4(vec3(0.09 + 0.05 * c), 1.0);
            return;
        }
        vec4 t = uShow == 0 ? texture(tBase, vUV) : (uShow == 1 ? texture(tNrm, vUV) : texture(tMR, vUV));
        if (uShow == 0) t.rgb = pow(t.rgb, vec3(1.0 / 2.2));
        oColor = vec4(t.rgb, uAlpha == 2 ? 0.35 : 1.0);
        return;
    }
    vec4 base = uBase;
    if (uHasBase) base *= texture(tBase, vUV);
    if (uHasMask) {
        vec3 m = texture(tMask, vUV).rgb;
        vec3 detail = clamp(base.rgb / 0.18, 0.0, 1.6);  // keep panel lines and grime under the paint
        base.rgb = mix(base.rgb, uLayer[0] * detail, m.r);
        base.rgb = mix(base.rgb, uLayer[1] * detail, m.g);
        base.rgb = mix(base.rgb, uLayer[2] * detail, m.b);
    }
    if (uHasDecals) {
        ivec2 t = ivec2(floor(vUV1));
        if ((t.x == -1 || t.x == 0) && t.y >= -4 && t.y <= 2) {
            vec2 f = fract(vUV1);
            for (int k = 0; k < 14; k++) {
                vec4 r = uSlot[k];
                if (r.z <= 0.0 || (k - (k / 2) * 2) != t.x + 1 || int(floor(uSlotArea[k] + 0.5)) != t.y) continue;
                vec4 x = uSlotXf[k];
                bool right = t.x == 0;
                // TranslateX moves the emblem aft on both sides (the left tile's U runs aft, the right one's forward)
                vec2 tr = vec2(right ? x.y : -x.y, x.z);
                // the game's shader: UV multiplier = 1 / (0.2 x DecalScale), so an emblem is DecalScale / 5 of its
                // area across (Joker 1: tail wing 2.3 -> 0.46, card 0.7 -> 0.14)
                vec2 q = (f + tr - 0.5) / (0.2 * max(x.x, 1e-3)) + 0.5;
                if (right && x.w > 0.5) q.x = 1.0 - q.x;         // FlipRight
                if (q.x >= 0.0 && q.x <= 1.0 && q.y >= 0.0 && q.y <= 1.0) {
                    vec4 d = texture(tDecal, r.xy + q * r.zw);
                    base.rgb = mix(base.rgb, d.rgb, d.a);
                }
            }
        }
    }
    if (uHasNumbers) {  // the game's number shader (see number_spec)
        ivec2 t = ivec2(floor(vUV1));
        if ((t.x == -1 || t.x == 0) && t.y >= 0 && t.y <= 2) {
            int n = t.y * 2 + t.x + 1;
            vec4 pl = uNumPlace[n];
            vec4 dg = uNumDigits[n];
            vec4 sh = uNumShift[n];
            if (sh.w > 0.5) {
                vec2 f = fract(vUV1);
                vec2 pp = f + pl.xy;
                pp.x += (pp.y - 1.0) * pl.z;
                vec2 P = (pp - 0.5) / pl.w + 0.5;
                vec2 gx = dFdx(vUV1) / pl.w, gy = dFdy(vUV1) / pl.w;
                float fill = 0.0, edge = 0.0;
                for (int i = 0; i < 3; i++) {
                    float d = dg[i + 1];
                    if (d < 0.0) continue;
                    vec2 c = vec2(P.x + sh[i], P.y);
                    if (c.x < 1.0 || c.x > 1.1 || c.y < 1.0 || c.y > 1.1) continue;
                    vec4 s4 = textureGrad(tFont, vec2(dg.x, d) * 0.1 + (c - 1.0), gx, gy);
                    fill += s4.a;
                    edge += s4.g;
                }
                vec4 ed = uNumEdge[n];
                if (ed.w > 0.5) base.rgb = mix(base.rgb, ed.rgb, clamp(edge, 0.0, 1.0));
                base.rgb = mix(base.rgb, uNumCol[n], clamp(fill, 0.0, 1.0));
            }
        }
    }
    if (uMode == 6) {  // baking: the painted colour map itself, sRGB encoded, with its own alpha
        vec3 c = clamp(base.rgb, 0.0, 1.0);
        vec3 srgb = mix(c * 12.92, 1.055 * pow(c, vec3(1.0 / 2.4)) - 0.055, step(0.0031308, c));
        oColor = vec4(srgb, base.a);
        return;
    }
    if (uAlpha == 1 && base.a < uCutoff) discard;
    float alpha = uAlpha == 2 ? base.a : 1.0;
    vec3 N = normalize(vNrm);
    if (!gl_FrontFacing) N = -N;
    if (uHasNrm && uMode != 1) {
        vec3 m = texture(tNrm, vUV).xyz * 2.0 - 1.0;
        m.y = -m.y;  // glTF UVs run top-down
        N = perturb(N, vPos, vUV, m);
    }
    if (uMode == 2) { oColor = vec4(N * 0.5 + 0.5, uAlpha == 2 ? 0.3 : 1.0); return; }
    if (uMode == 1) { oColor = vec4(pow(base.rgb, vec3(1.0 / 2.2)), alpha); return; }
    float metal = uMetal, rough = uRough, ao = 1.0;
    if (uHasMR) {
        vec4 mr = texture(tMR, vUV);
        rough *= mr.g;
        metal *= mr.b;
        if (uHasOcc) ao = mr.r;
    }
    if (uMode == 3) { base.rgb = vec3(0.50); metal = 0.0; rough = 0.55; alpha = uAlpha == 2 ? 0.3 : 1.0; }
    rough = clamp(rough, 0.05, 1.0);
    vec3 V = normalize(uEye - vPos);
    float NdV = max(dot(N, V), 1e-4);
    vec3 F0 = mix(vec3(0.04), base.rgb, metal);
    vec3 diff = base.rgb * (1.0 - metal);
    vec3 c = light(N, V, uKey, vec3(2.6, 2.5, 2.35), diff, F0, rough)
           + light(N, V, uFill, vec3(0.45, 0.5, 0.6), diff, F0, rough);
    vec3 Fa = F0 + (max(vec3(1.0 - rough), F0) - F0) * pow(1.0 - NdV, 5.0);
    vec3 R = reflect(-V, N);
    vec3 env = mix(sky(R), sky(N), rough * rough);
    c += (diff * sky(N) * (1.0 - Fa) + env * Fa) * ao * 0.9;
    oColor = vec4(pow(aces(c * 0.95), vec3(1.0 / 2.2)), alpha);
}
"""

LINE_VERT = """
#version 330
layout(location=0) in vec3 aPos;
layout(location=1) in vec4 aCol;
uniform mat4 uVP;
out vec4 vCol;
void main() { vCol = aCol; gl_Position = uVP * vec4(aPos, 1.0); }
"""

LINE_FRAG = """
#version 330
in vec4 vCol;
out vec4 oColor;
void main() { oColor = vCol; }
"""

BACKGROUNDS = {"dark": (0.047, 0.047, 0.055), "grey": (0.22, 0.22, 0.24), "light": (0.78, 0.78, 0.8)}
ORANGE = (0.90, 0.24, 0.18)


DEFAULT_FOV = 35.0


class Camera:
    def __init__(self):
        self.target = np.zeros(3)
        self.dist, self.yaw, self.pitch, self.fov = 10.0, math.radians(35), math.radians(18), DEFAULT_FOV
        self.aspect = 16 / 9
        self.radius = 5.0

    def eye(self):
        cp = math.cos(self.pitch)
        return self.target + self.dist * np.array([cp * math.sin(self.yaw), math.sin(self.pitch),
                                                   cp * math.cos(self.yaw)])

    def frame(self, lo, hi):
        """Back off until all 8 corners of the bounding box fit in the view from the current angle, with a small
        margin — works for tall character models (height-limited) and long aircraft (width-limited) alike."""
        lo, hi = np.asarray(lo, float), np.asarray(hi, float)
        self.target = (lo + hi) / 2
        self.radius = max(float(np.linalg.norm(hi - lo)) / 2, 1e-3)
        tv = math.tan(math.radians(self.fov) / 2)
        th = tv * max(self.aspect, 0.2)
        cp = math.cos(self.pitch)
        back = np.array([cp * math.sin(self.yaw), math.sin(self.pitch), cp * math.cos(self.yaw)])  # target -> eye
        right = np.cross(-back, [0.0, 1.0, 0.0])
        right = right / (np.linalg.norm(right) or 1.0)
        up = np.cross(right, -back)
        dist = 0.0
        for x in (lo[0], hi[0]):
            for y in (lo[1], hi[1]):
                for z in (lo[2], hi[2]):
                    c = np.array([x, y, z]) - self.target
                    toward = float(c @ back)
                    dist = max(dist, toward + abs(float(c @ right)) / th, toward + abs(float(c @ up)) / tv)
        self.dist = max(dist * 1.05, self.radius * 0.05)

    def matrices(self, w, h):
        self.aspect = w / max(h, 1)
        near = max(self.dist - self.radius * 3, self.dist * 0.01, 1e-3)
        far = self.dist + self.radius * 6 + 10
        proj = perspective(self.fov, w / max(h, 1), near, far)
        return proj @ look_at(self.eye(), self.target)

    def orbit(self, dx, dy):
        self.yaw -= dx * 0.008
        self.pitch = min(max(self.pitch + dy * 0.008, -1.55), 1.55)

    def pan(self, dx, dy, h):
        view = look_at(self.eye(), self.target)
        right, up = view[0, :3], view[1, :3]
        k = 2 * self.dist * math.tan(math.radians(self.fov) / 2) / max(h, 1)
        self.target = self.target - right * dx * k + up * dy * k

    def look(self, dx, dy, k=0.003):
        """Free fly: turn the camera where it stands (mouse right = turn right, mouse down = look down)."""
        eye = self.eye()
        self.yaw -= dx * k
        self.pitch = min(max(self.pitch + dy * k, -1.55), 1.55)
        cp = math.cos(self.pitch)
        self.target = eye - self.dist * np.array([cp * math.sin(self.yaw), math.sin(self.pitch), cp * math.cos(self.yaw)])

    def forward(self):
        f = self.target - self.eye()
        return f / max(float(np.linalg.norm(f)), 1e-9)

    def zoom(self, steps):
        """Dolly: move closer / further from the point being looked at (the field of view stays the same)."""
        self.dist = min(max(self.dist * (0.88 ** steps), self.radius * 0.002, 1e-3), self.radius * 60)


class Renderer:
    """All GL work. Needs a current OpenGL 3.3+ context (compatibility or core)."""

    def __init__(self):
        from OpenGL import GL
        self.GL = GL
        self.model = None
        self.gpu = []          # per prim: (vao, buffers, count, index type)
        self.textures = {}     # image index -> GL texture
        self.opts = {"mode": "lit", "wire": False, "skeleton": False, "grid": True, "bg": "dark",
                     "hidden": set(), "pose": {}, "bone": None,
                     "tex_off": set()}  # (material index, role) pairs switched off
        self.cam = Camera()
        self.livery = None
        self.fbo = None
        self.fbo_size = (0, 0)
        self.samples = 4
        self._init()

    # ------------------------------------------------------------- setup
    def _compile(self, vs, fs):
        GL = self.GL

        def sh(src, kind):
            s = GL.glCreateShader(kind)
            GL.glShaderSource(s, src)
            GL.glCompileShader(s)
            if not GL.glGetShaderiv(s, GL.GL_COMPILE_STATUS):
                raise RuntimeError(GL.glGetShaderInfoLog(s).decode(errors="replace"))
            return s

        p = GL.glCreateProgram()
        for s in (sh(vs, GL.GL_VERTEX_SHADER), sh(fs, GL.GL_FRAGMENT_SHADER)):
            GL.glAttachShader(p, s)
        GL.glLinkProgram(p)
        if not GL.glGetProgramiv(p, GL.GL_LINK_STATUS):
            raise RuntimeError(GL.glGetProgramInfoLog(p).decode(errors="replace"))
        return p

    def _init(self):
        GL = self.GL
        self.version = GL.glGetString(GL.GL_VERSION).decode(errors="replace")
        self.renderer_name = GL.glGetString(GL.GL_RENDERER).decode(errors="replace")
        self.prog = self._compile(VERT, FRAG)
        self.line_prog = self._compile(LINE_VERT, LINE_FRAG)
        self.u = {n: GL.glGetUniformLocation(self.prog, n) for n in (
            "uVP", "uModel", "uSkin", "uBones", "uMode", "uFlat", "uBase", "uMetal", "uRough", "uHasBase", "uHasNrm",
            "uHasMR", "uHasOcc", "tBase", "tNrm", "tMR", "uAlpha", "uCutoff", "uEye", "uKey", "uFill", "uShow", "uHasMask", "tMask",
            "uLayer", "uHasDecals", "tDecal", "uSlot", "uSlotXf", "uSlotArea", "uHasNumbers", "tFont", "uNumPlace", "uNumDigits", "uNumShift", "uNumCol",
            "uNumEdge", "uBake", "uBakeOff")}
        self.ul = GL.glGetUniformLocation(self.line_prog, "uVP")
        self.bone_tex = GL.glGenTextures(1)
        self._set_bones(np.identity(4)[None])
        self.line_vao = GL.glGenVertexArrays(1)
        self.line_vbo = GL.glGenBuffers(1)
        try:
            self.max_aniso = float(GL.glGetFloatv(0x84FF))  # GL_MAX_TEXTURE_MAX_ANISOTROPY
        except Exception:
            self.max_aniso = 0.0
        try:
            self.samples = max(1, min(4, int(GL.glGetIntegerv(GL.GL_MAX_SAMPLES))))
        except Exception:
            self.samples = 1

    def _set_bones(self, mats):
        GL = self.GL
        data = np.ascontiguousarray(mats.transpose(0, 2, 1).astype(np.float32))  # each row = one matrix column
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.bone_tex)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA32F, 4, len(mats), 0, GL.GL_RGBA, GL.GL_FLOAT, data)

    # ------------------------------------------------------------- model upload
    def clear(self):
        GL = self.GL
        for vao, bufs, _n, _t in self.gpu:
            GL.glDeleteVertexArrays(1, [vao])
            GL.glDeleteBuffers(len(bufs), bufs)
        if self.textures:
            GL.glDeleteTextures(len(self.textures), list(self.textures.values()))
        self.gpu, self.textures, self.model = [], {}, None

    def set_model(self, m, keep_camera=False):
        GL = self.GL
        self.clear_livery()
        self.clear()
        self.model = m
        for p in m.prims:
            vao = GL.glGenVertexArrays(1)
            GL.glBindVertexArray(vao)
            bufs = []
            for loc, arr, kind in ((0, p.pos, GL.GL_FLOAT), (1, p.nrm, GL.GL_FLOAT), (2, p.uv, GL.GL_FLOAT),
                                   (3, p.joints, GL.GL_UNSIGNED_SHORT), (4, p.weights, GL.GL_FLOAT),
                                   (5, p.uv1, GL.GL_FLOAT)):
                if arr is None:
                    GL.glDisableVertexAttribArray(loc)
                    continue
                b = GL.glGenBuffers(1)
                bufs.append(b)
                GL.glBindBuffer(GL.GL_ARRAY_BUFFER, b)
                GL.glBufferData(GL.GL_ARRAY_BUFFER, arr.nbytes, np.ascontiguousarray(arr), GL.GL_STATIC_DRAW)
                GL.glEnableVertexAttribArray(loc)
                GL.glVertexAttribPointer(loc, arr.shape[1], kind, GL.GL_FALSE, 0, None)
            ib = GL.glGenBuffers(1)
            bufs.append(ib)
            GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, ib)
            GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, p.idx.nbytes, np.ascontiguousarray(p.idx), GL.GL_STATIC_DRAW)
            GL.glBindVertexArray(0)
            itype = GL.GL_UNSIGNED_INT if p.idx.dtype == np.uint32 else GL.GL_UNSIGNED_SHORT
            self.gpu.append((vao, bufs, len(p.idx), itype))
        for mat in m.materials:
            for role, ii in mat.tex.items():
                if role == "occ" or ii in self.textures or ii not in m.images:
                    continue
                self.textures[ii] = self._texture(m.images[ii], srgb=(role == "base"))
        m.images.clear()  # CPU copies no longer needed
        self.opts["hidden"], self.opts["pose"], self.opts["bone"], self.opts["tex_off"] = set(), {}, None, set()
        self.update_pose()
        if not keep_camera:
            self.cam.fov = DEFAULT_FOV
            self.cam.frame(m.lo, m.hi)

    def _texture(self, img, srgb, mipmaps=True):
        GL = self.GL
        t = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, t)
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
        h, w = img.shape[:2]
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_SRGB8_ALPHA8 if srgb else GL.GL_RGBA8, w, h, 0, GL.GL_RGBA,
                        GL.GL_UNSIGNED_BYTE, np.ascontiguousarray(img))
        if mipmaps:
            GL.glGenerateMipmap(GL.GL_TEXTURE_2D)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER,
                           GL.GL_LINEAR_MIPMAP_LINEAR if mipmaps else GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_REPEAT)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_REPEAT)
        if self.max_aniso > 1:
            GL.glTexParameterf(GL.GL_TEXTURE_2D, 0x84FE, min(8.0, self.max_aniso))
        return t

    # ------------------------------------------------------------- liveries
    def clear_livery(self):
        lv = getattr(self, "livery", None)
        if lv and lv.get("gl"):
            self.GL.glDeleteTextures(len(lv["gl"]), lv["gl"])
        self.livery = None

    def set_livery(self, spec):
        """spec: {'mats': {material index: {'base'|'mr'|'normal'|'mask': RGBA uint8 array}}, 'layers': [rgb]*3 or None,
        'atlas': RGBA array or None, 'slots': {(col, row): (u, v, w, h)}} — or None for the exported look."""
        keep = None
        if spec and not spec.get("textures", True) and self.livery:  # quick update: emblems / numbers / colours
            keep = self.livery
            keep_gl = set(t for roles in keep["mats"].values() for t in roles.values())
            drop = [t for t in keep["gl"] if t not in keep_gl]
            if drop:
                self.GL.glDeleteTextures(len(drop), drop)
            self.livery = None
        else:
            self.clear_livery()
        if not spec:
            return
        gl, mats = [], {}
        if keep:
            mats = keep["mats"]
            gl = [t for roles in mats.values() for t in roles.values()]
        for mi, roles in spec["mats"].items():
            mats[mi] = {}
            for role, img in roles.items():
                t = self._texture(img, srgb=(role == "base"))
                gl.append(t)
                mats[mi][role] = t
        atlas = None
        if spec.get("atlas") is not None:
            atlas = self._texture(spec["atlas"], srgb=True, mipmaps=False)
            gl.append(atlas)
        slots = np.zeros((14, 4), np.float32)
        slot_xf = np.tile(np.asarray([1.0, 0.0, 0.0, 0.0], np.float32), (14, 1))
        slot_area = np.zeros(14, np.float32)
        for (col, row), rect in spec.get("slots", {}).items():
            if col in (-1, 0) and -4 <= row <= 2:
                k = (row + 4) * 2 + (col + 1)
                slots[k] = rect[:4]
                slot_area[k] = rect[8] if len(rect) > 8 else row
                if len(rect) > 7:
                    slot_xf[k] = (rect[4] if rect[4] > 0 else 1.0, rect[5], rect[6], rect[7])
                elif len(rect) > 4 and rect[4] > 0:
                    slot_xf[k, 0] = rect[4]
        num = spec.get("numbers")
        font = None
        if num and num.get("atlas") is not None:
            font = self._texture(num["atlas"], srgb=False)
            self.GL.glTexParameteri(self.GL.GL_TEXTURE_2D, 0x813D, 4)  # GL_TEXTURE_MAX_LEVEL: keep digits apart
            self.GL.glTexParameteri(self.GL.GL_TEXTURE_2D, self.GL.GL_TEXTURE_WRAP_S, self.GL.GL_CLAMP_TO_EDGE)
            self.GL.glTexParameteri(self.GL.GL_TEXTURE_2D, self.GL.GL_TEXTURE_WRAP_T, self.GL.GL_CLAMP_TO_EDGE)
            gl.append(font)
        layers = np.asarray(spec.get("layers") or [[0, 0, 0]] * 3, np.float32).reshape(3, 3)
        self.livery = {"gl": gl, "mats": mats, "atlas": atlas, "slots": slots, "layers": layers,
                       "has_layers": spec.get("layers") is not None, "slot_xf": slot_xf, "slot_area": slot_area,
                       "font": font,
                       "num_place": np.asarray(num["place"], np.float32).reshape(6, 4) if font else None,
                       "num_digits": np.asarray(num["digits"], np.float32).reshape(6, 4) if font else None,
                       "num_shift": np.asarray(num["shift"], np.float32).reshape(6, 4) if font else None,
                       "num_col": np.asarray(num["colors"], np.float32).reshape(6, 3) if font else None,
                       "num_edge": np.asarray(num.get("edges", np.zeros((3, 2, 4))), np.float32).reshape(6, 4)
                       if font else None}

    # ------------------------------------------------------------- baking
    def bake(self, spec, jobs):
        """Paint each material's colour map with a livery (paint layers, emblems, numbers) by drawing the mesh in
        its UV layout. spec: a livery spec (full-size textures) or None; jobs: {material index: (w, h)}.
        -> {material index: (RGBA uint8 (h, w, 4), covered bool (h, w))}; rows top first, like the PNGs."""
        GL, u, m = self.GL, self.u, self.model
        out = {}
        saved, saved_off = self.livery, self.opts["tex_off"]
        self.livery, self.opts["tex_off"] = None, set()
        fbo = tex = None
        try:
            if spec:
                self.set_livery(spec)
            limit = int(GL.glGetIntegerv(GL.GL_MAX_TEXTURE_SIZE))
            fbo = GL.glGenFramebuffers(1)
            tex = GL.glGenTextures(1)
            GL.glUseProgram(self.prog)
            for name, unit in (("tBase", 0), ("tNrm", 1), ("tMR", 2), ("uBones", 3), ("tMask", 4), ("tDecal", 5),
                               ("tFont", 6)):
                GL.glUniform1i(u[name], unit)
            GL.glUniform1i(u["uBake"], 1)
            GL.glDisable(GL.GL_DEPTH_TEST)
            GL.glDisable(GL.GL_CULL_FACE)
            GL.glDisable(GL.GL_BLEND)
            GL.glPixelStorei(GL.GL_PACK_ALIGNMENT, 1)
            for mi, (w, h) in jobs.items():
                w, h = max(1, min(int(w), limit)), max(1, min(int(h), limit))
                prims = [i for i, p in enumerate(m.prims) if p.mat == mi]
                if not prims:
                    continue
                uv = np.concatenate([m.prims[i].uv for i in prims])
                off = np.floor(np.median(uv, 0)) if len(uv) else np.zeros(2)
                GL.glUniform2f(u["uBakeOff"], float(off[0]), float(off[1]))
                GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
                GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, w, h, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, None)
                GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
                GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)
                GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
                GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_TEXTURE_2D, tex, 0)
                GL.glViewport(0, 0, w, h)
                imgs = []
                for flat in (False, True):  # the colour, then which pixels the UV layout covers
                    GL.glClearColor(0, 0, 0, 0)
                    GL.glClear(GL.GL_COLOR_BUFFER_BIT)
                    for i in prims:
                        p = m.prims[i]
                        mat = m.materials[mi]
                        if flat:
                            GL.glUniform1i(u["uMode"], 4)
                            GL.glUniform4f(u["uFlat"], 1.0, 1.0, 1.0, 1.0)
                            self._prim_uniforms(p, mat, 4, textures=False)
                        else:
                            self._prim_uniforms(p, mat, 6)
                        vao, _b, n, itype = self.gpu[i]
                        GL.glBindVertexArray(vao)
                        GL.glDrawElements(GL.GL_TRIANGLES, n, itype, None)
                    GL.glBindVertexArray(0)
                    data = GL.glReadPixels(0, 0, w, h, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
                    imgs.append(np.frombuffer(data, np.uint8).reshape(h, w, 4).copy())
                # row 0 of the read-back is v = 0, which is the top row of a glTF texture: no flip
                out[mi] = (imgs[0], imgs[1][..., 0] > 127)
        finally:
            try:
                GL.glUseProgram(self.prog)
                GL.glUniform1i(u["uBake"], 0)
                GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
                if fbo is not None:
                    GL.glDeleteFramebuffers(1, [fbo])
                if tex is not None:
                    GL.glDeleteTextures(1, [tex])
                if spec:
                    self.clear_livery()
            finally:
                self.livery, self.opts["tex_off"] = saved, saved_off
        return out

    # ------------------------------------------------------------- pose
    def update_pose(self):
        m = self.model
        if m is None:
            return
        self.world = node_worlds(m, self.opts["pose"])
        if m.joints:
            mats = np.stack([self.world[jn] @ m.ibm[k] for k, jn in enumerate(m.joints)])
            self._set_bones(mats)

    def bone_positions(self):
        m = self.model
        return [self.world[jn][:3, 3] for jn in m.joints]

    # ------------------------------------------------------------- drawing
    def _ensure_fbo(self, w, h):
        GL = self.GL
        if self.fbo and self.fbo_size == (w, h):
            return
        if self.fbo:
            GL.glDeleteFramebuffers(2, [self.fbo, self.resolve])
            GL.glDeleteRenderbuffers(3, self.rbs)
        self.fbo, self.resolve = GL.glGenFramebuffers(2)
        rbs = GL.glGenRenderbuffers(3)
        self.rbs = list(rbs)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.fbo)
        GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, rbs[0])
        GL.glRenderbufferStorageMultisample(GL.GL_RENDERBUFFER, self.samples, GL.GL_RGBA8, w, h)
        GL.glFramebufferRenderbuffer(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_RENDERBUFFER, rbs[0])
        GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, rbs[1])
        GL.glRenderbufferStorageMultisample(GL.GL_RENDERBUFFER, self.samples, GL.GL_DEPTH_COMPONENT24, w, h)
        GL.glFramebufferRenderbuffer(GL.GL_FRAMEBUFFER, GL.GL_DEPTH_ATTACHMENT, GL.GL_RENDERBUFFER, rbs[1])
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.resolve)
        GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, rbs[2])
        GL.glRenderbufferStorage(GL.GL_RENDERBUFFER, GL.GL_RGBA8, w, h)
        GL.glFramebufferRenderbuffer(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_RENDERBUFFER, rbs[2])
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
        self.fbo_size = (w, h)

    def render(self, w, h, target_fbo=0):
        """Draw a frame (multisampled) and resolve it into target_fbo (0 = the window, None = don't resolve)."""
        GL = self.GL
        w, h = max(1, int(w)), max(1, int(h))
        self._ensure_fbo(w, h)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.fbo)
        GL.glViewport(0, 0, w, h)
        bg = BACKGROUNDS.get(self.opts["bg"], BACKGROUNDS["dark"])
        GL.glClearColor(bg[0], bg[1], bg[2], 1.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glDepthFunc(GL.GL_LEQUAL)
        vp = self.cam.matrices(w, h)
        if self.opts["grid"]:
            self._draw_grid(vp)
        if self.model is not None:
            self._draw_model(vp)
            if self.opts["skeleton"] and self.model.joints:
                self._draw_skeleton(vp)
        # resolve the multisampled image into a plain buffer of the same format
        GL.glBindFramebuffer(GL.GL_READ_FRAMEBUFFER, self.fbo)
        GL.glBindFramebuffer(GL.GL_DRAW_FRAMEBUFFER, self.resolve)
        GL.glBlitFramebuffer(0, 0, w, h, 0, 0, w, h, GL.GL_COLOR_BUFFER_BIT, GL.GL_NEAREST)
        if target_fbo is None:
            return
        # then copy to the window (a single-sample copy may convert formats; a multisample one may not)
        GL.glBindFramebuffer(GL.GL_READ_FRAMEBUFFER, self.resolve)
        GL.glBindFramebuffer(GL.GL_DRAW_FRAMEBUFFER, target_fbo)
        GL.glBlitFramebuffer(0, 0, w, h, 0, 0, w, h, GL.GL_COLOR_BUFFER_BIT, GL.GL_NEAREST)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, target_fbo)

    def read_pixels(self, w, h):
        """Render into an off-screen buffer and return an (h, w, 3) uint8 image (top row first)."""
        GL = self.GL
        self.render(w, h, None)
        GL.glBindFramebuffer(GL.GL_READ_FRAMEBUFFER, self.resolve)
        GL.glPixelStorei(GL.GL_PACK_ALIGNMENT, 1)
        data = GL.glReadPixels(0, 0, w, h, GL.GL_RGB, GL.GL_UNSIGNED_BYTE)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
        img = np.frombuffer(data, np.uint8).reshape(h, w, 3)
        return img[::-1].copy()

    def _draw_model(self, vp):
        GL, u, m = self.GL, self.u, self.model
        GL.glUseProgram(self.prog)
        GL.glUniformMatrix4fv(u["uVP"], 1, GL.GL_TRUE, vp.astype(np.float32))
        GL.glUniform3f(u["uEye"], *self.cam.eye())
        key = np.array([0.45, 0.8, 0.55])
        fill = np.array([-0.6, 0.25, -0.5])
        # lights follow the camera's orbit so the side you look at is always lit
        cy, sy = math.cos(self.cam.yaw), math.sin(self.cam.yaw)
        rot = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
        GL.glUniform3f(u["uKey"], *(rot @ key / np.linalg.norm(key)))
        GL.glUniform3f(u["uFill"], *(rot @ fill / np.linalg.norm(fill)))
        GL.glUniform1i(u["tBase"], 0)
        GL.glUniform1i(u["tNrm"], 1)
        GL.glUniform1i(u["tMR"], 2)
        GL.glUniform1i(u["uBones"], 3)
        GL.glUniform1i(u["tMask"], 4)
        GL.glUniform1i(u["tDecal"], 5)
        GL.glUniform1i(u["tFont"], 6)
        GL.glActiveTexture(GL.GL_TEXTURE3)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.bone_tex)
        mode = {"lit": 0, "colour": 1, "normals": 2, "clay": 3, "tex_base": 5, "tex_normal": 5,
                "tex_mr": 5}.get(self.opts["mode"], 0)
        GL.glUniform1i(u["uShow"], {"tex_normal": 1, "tex_mr": 2}.get(self.opts["mode"], 0))
        hidden = self.opts["hidden"]
        order = sorted(range(len(m.prims)), key=lambda i: m.materials[m.prims[i].mat].alpha == "BLEND")
        GL.glEnable(GL.GL_CULL_FACE)
        GL.glCullFace(GL.GL_BACK)
        GL.glFrontFace(GL.GL_CCW)
        for i in order:
            p = m.prims[i]
            if p.mat in hidden:
                continue
            mat = m.materials[p.mat]
            blend = mat.alpha == "BLEND"
            self._prim_uniforms(p, mat, mode)
            if mat.double:
                GL.glDisable(GL.GL_CULL_FACE)
            else:
                GL.glEnable(GL.GL_CULL_FACE)
            if blend:
                GL.glEnable(GL.GL_BLEND)
                GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
                GL.glDepthMask(GL.GL_FALSE)
            vao, _b, n, itype = self.gpu[i]
            GL.glBindVertexArray(vao)
            GL.glDrawElements(GL.GL_TRIANGLES, n, itype, None)
            if blend:
                GL.glDisable(GL.GL_BLEND)
                GL.glDepthMask(GL.GL_TRUE)
        if self.opts["wire"]:
            GL.glUniform1i(u["uMode"], 4)
            col = (1.0, 0.45, 0.35, 0.28) if self.opts["bg"] != "light" else (0.6, 0.12, 0.08, 0.35)
            GL.glUniform4f(u["uFlat"], *col)
            GL.glEnable(GL.GL_BLEND)
            GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
            GL.glPolygonMode(GL.GL_FRONT_AND_BACK, GL.GL_LINE)
            GL.glEnable(GL.GL_POLYGON_OFFSET_LINE)
            GL.glPolygonOffset(-1.0, -1.0)
            for i in order:
                p = m.prims[i]
                if p.mat in hidden:
                    continue
                self._prim_uniforms(p, m.materials[p.mat], 4, textures=False)
                vao, _b, n, itype = self.gpu[i]
                GL.glBindVertexArray(vao)
                GL.glDrawElements(GL.GL_TRIANGLES, n, itype, None)
            GL.glPolygonMode(GL.GL_FRONT_AND_BACK, GL.GL_FILL)
            GL.glDisable(GL.GL_POLYGON_OFFSET_LINE)
            GL.glDisable(GL.GL_BLEND)
        GL.glBindVertexArray(0)
        GL.glDisable(GL.GL_CULL_FACE)

    def _prim_uniforms(self, p, mat, mode, textures=True):
        GL, u, m = self.GL, self.u, self.model
        skinned = p.node in m.skinned_nodes and p.joints is not None
        GL.glUniform1i(u["uSkin"], int(skinned))
        model = np.identity(4) if (skinned or p.node is None) else self.world[p.node]
        GL.glUniformMatrix4fv(u["uModel"], 1, GL.GL_TRUE, model.astype(np.float32))
        if mode == 4:
            return
        GL.glUniform1i(u["uMode"], mode)
        GL.glUniform4f(u["uBase"], *mat.base)
        GL.glUniform1f(u["uMetal"], mat.metal)
        GL.glUniform1f(u["uRough"], mat.rough)
        GL.glUniform1i(u["uAlpha"], {"OPAQUE": 0, "MASK": 1, "BLEND": 2}.get(mat.alpha, 0))
        GL.glUniform1f(u["uCutoff"], mat.cutoff)
        lv = self.livery["mats"].get(p.mat) if (self.livery and textures) else None
        for unit, role, flag in ((0, "base", "uHasBase"), (1, "normal", "uHasNrm"), (2, "mr", "uHasMR")):
            t = self.textures.get(mat.tex.get(role)) if textures and (p.mat, role) not in self.opts["tex_off"] \
                else None
            if lv and role in lv and (p.mat, role) not in self.opts["tex_off"]:
                t = lv[role]
            GL.glUniform1i(u[flag], int(t is not None))
            GL.glActiveTexture(GL.GL_TEXTURE0 + unit)
            GL.glBindTexture(GL.GL_TEXTURE_2D, t or 0)
        has_occ = (mat.has_occ and "mr" in mat.tex) or bool(lv and "mr" in lv)
        GL.glUniform1i(u["uHasOcc"], int(has_occ and (p.mat, "mr") not in self.opts["tex_off"]))
        mask = lv.get("mask") if lv else None
        GL.glUniform1i(u["uHasMask"], int(mask is not None and self.livery["has_layers"]))
        GL.glActiveTexture(GL.GL_TEXTURE4)
        GL.glBindTexture(GL.GL_TEXTURE_2D, mask or 0)
        decals = bool(lv) and self.livery["atlas"] is not None and p.uv1 is not None
        GL.glUniform1i(u["uHasDecals"], int(decals))
        GL.glActiveTexture(GL.GL_TEXTURE5)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.livery["atlas"] if decals else 0)
        if lv:
            GL.glUniform3fv(u["uLayer"], 3, self.livery["layers"])
            GL.glUniform4fv(u["uSlot"], 14, self.livery["slots"])
            GL.glUniform4fv(u["uSlotXf"], 14, self.livery["slot_xf"])
            GL.glUniform1fv(u["uSlotArea"], 14, self.livery["slot_area"])
        nums = bool(lv) and self.livery["font"] is not None and p.uv1 is not None
        GL.glUniform1i(u["uHasNumbers"], int(nums))
        GL.glActiveTexture(GL.GL_TEXTURE6)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.livery["font"] if nums else 0)
        if nums:
            GL.glUniform4fv(u["uNumPlace"], 6, self.livery["num_place"])
            GL.glUniform4fv(u["uNumDigits"], 6, self.livery["num_digits"])
            GL.glUniform4fv(u["uNumShift"], 6, self.livery["num_shift"])
            GL.glUniform3fv(u["uNumCol"], 6, self.livery["num_col"])
            GL.glUniform4fv(u["uNumEdge"], 6, self.livery["num_edge"])

    def _lines(self, vp, pts, cols, width=1.0, depth=True, points=0.0):
        GL = self.GL
        if not len(pts):
            return
        data = np.hstack([np.asarray(pts, np.float32), np.asarray(cols, np.float32)])
        GL.glUseProgram(self.line_prog)
        GL.glUniformMatrix4fv(self.ul, 1, GL.GL_TRUE, vp.astype(np.float32))
        GL.glBindVertexArray(self.line_vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.line_vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, data.nbytes, np.ascontiguousarray(data), GL.GL_DYNAMIC_DRAW)
        GL.glEnableVertexAttribArray(0)
        GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, GL.GL_FALSE, 28, None)
        GL.glEnableVertexAttribArray(1)
        import ctypes
        GL.glVertexAttribPointer(1, 4, GL.GL_FLOAT, GL.GL_FALSE, 28, ctypes.c_void_p(12))
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
        if not depth:
            GL.glDisable(GL.GL_DEPTH_TEST)
        try:
            GL.glLineWidth(width)
        except Exception:
            pass
        if points:
            try:
                GL.glPointSize(points)
            except Exception:
                pass
            GL.glDrawArrays(GL.GL_POINTS, 0, len(data))
        else:
            GL.glDrawArrays(GL.GL_LINES, 0, len(data))
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glDisable(GL.GL_BLEND)
        GL.glBindVertexArray(0)

    def _draw_grid(self, vp):
        if self.model is not None:
            lo, hi = self.model.lo, self.model.hi
        else:
            lo, hi = np.array([-5.0, 0, -5]), np.array([5.0, 0, 5])
        span = max(hi[0] - lo[0], hi[2] - lo[2], 1e-3)
        step = 10 ** math.floor(math.log10(span / 4))
        n = int(math.ceil(span * 1.25 / step / 2))
        cx, cz = round((lo[0] + hi[0]) / 2 / step) * step, round((lo[2] + hi[2]) / 2 / step) * step
        y = lo[1]
        light = self.opts["bg"] == "light"
        base = (0, 0, 0, 0.18) if light else (1, 1, 1, 0.10)
        major = (0, 0, 0, 0.32) if light else (1, 1, 1, 0.2)
        pts, cols = [], []
        for k in range(-n, n + 1):
            c = major if k % 5 == 0 else base
            pts += [(cx + k * step, y, cz - n * step), (cx + k * step, y, cz + n * step),
                    (cx - n * step, y, cz + k * step), (cx + n * step, y, cz + k * step)]
            cols += [c] * 4
        self._lines(vp, pts, cols)

    def _draw_skeleton(self, vp):
        m = self.model
        pos = self.bone_positions()
        jindex = {jn: k for k, jn in enumerate(m.joints)}
        sel = self.opts["bone"]
        pts, cols = [], []
        for k, jn in enumerate(m.joints):
            parent = m.nodes[jn]["parent"]
            if parent in jindex:
                c = (1, 1, 1, 1) if sel in (k, jindex[parent]) else (*ORANGE, 1)
                pts += [pos[jindex[parent]], pos[k]]
                cols += [c, c]
        self._lines(vp, pts, cols, width=2.0, depth=False)
        self._lines(vp, pos, [((1, 1, 1, 1) if k == sel else (*ORANGE, 1)) for k in range(len(pos))], depth=False,
                    points=6.0)


# ====================================================================== Tk widget

def available():
    """'' when the viewer can run, else a reason."""
    try:
        import OpenGL  # noqa: F401
    except ImportError:
        return "PyOpenGL isn't installed"
    if not OFFSCREEN:
        try:
            import pyopengltk  # noqa: F401
        except ImportError:
            return "pyopengltk isn't installed"
    return ""


def GLView(parent, bg="#0c0c0e", on_error=None, on_ready=None):
    if OFFSCREEN:
        return _OffscreenView(parent, bg, on_error, on_ready)
    return _make_window_view(parent, bg, on_error, on_ready)


class NavGizmo(tk.Canvas):
    """Blender's navigation gizmo for a GLView: the X / Y / Z axes as seen from the camera. Click a ball to look
    along that axis, drag the circle to orbit. Labelled like Blender (Z up), which is how the exported GLBs open
    there: Blender X = glTF +X, Blender Y = glTF -Z, Blender Z = glTF +Y."""

    AXES = (("X", (1.0, 0.0, 0.0), "#ff3352"), ("Y", (0.0, 0.0, -1.0), "#8bdc00"), ("Z", (0.0, 1.0, 0.0), "#2890ff"))

    def __init__(self, view, size=96, font_file=None, on_frame=None, on_hint=None):
        # under the circle: a round "frame the model" button
        self.btn, self.gap = round(size * 0.36), round(size * 0.1)
        self.height = size + (self.gap + self.btn if on_frame else 0)
        tk.Canvas.__init__(self, view.master, width=size, height=self.height, highlightthickness=0, bd=0,
                           bg=self._bg(view), cursor="hand2")
        self.view, self.size = view, size
        self.font_file, self.on_frame, self.on_hint = font_file, on_frame, on_hint
        self._job, self._hover, self._press, self._balls = None, None, None, []
        view.gizmo = self
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: (self._set_hover(None), self.on_hint and self.on_hint(None)))
        self.bind("<ButtonPress-1>", self._down)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", self._up)
        self.request()

    @staticmethod
    def _bg(view):
        r = getattr(view, "r", None)
        c = BACKGROUNDS.get(r.opts.get("bg", "dark") if r else "dark", BACKGROUNDS["dark"])
        return "#%02x%02x%02x" % tuple(round(v * 255) for v in c)

    def place_in(self, **kw):
        self.place(**kw)
        self.tk.call("raise", self._w)  # (Canvas.lift raises canvas items, not the widget)

    def request(self):
        if self._job is None:
            try:
                self._job = self.after_idle(self.draw)
            except tk.TclError:
                pass

    SS = 4  # drawn this many times larger, then scaled down: smooth (anti-aliased) edges

    def _pil_font(self, px):
        from PIL import ImageFont
        key = round(px)
        cache = self.__dict__.setdefault("_fonts", {})
        if key not in cache:
            f = None
            for path in ([self.font_file] if self.font_file else []) + ["segoeuib.ttf", "arialbd.ttf",
                                                                        "DejaVuSans-Bold.ttf"]:
                try:
                    f = ImageFont.truetype(path, key)
                    break
                except Exception:
                    pass
            if f is None:
                try:
                    f = ImageFont.load_default(key)
                except Exception:
                    f = ImageFont.load_default()
            cache[key] = f
        return cache[key]

    def draw(self):
        self._job = None
        v = self.view
        if v.r is None:
            return
        from PIL import Image, ImageDraw, ImageTk
        light = v.r.opts.get("bg") == "light"
        bg = self._bg(v)
        self.configure(bg=bg)
        s, k = self.size, self.SS
        c, R, br = s / 2, s / 2 - s * 0.13, s * 0.105
        img = Image.new("RGB", (s * k, self.height * k), bg)
        dr = ImageDraw.Draw(img)

        def oval(x, y, r, fill=None, outline=None, width=0):
            dr.ellipse((x * k - r * k, y * k - r * k, x * k + r * k, y * k + r * k), fill=fill, outline=outline,
                       width=round(width * k))

        if (self._hover is not None and self._hover[0] != "frame") or self._press is not None:
            oval(c, c, s / 2 - 0.5, fill="#c8c8cc" if light else "#3a3a40")
        if self.on_frame:  # frame button: corner brackets around a dot
            hot = self._hover is not None and self._hover[0] == "frame"
            fy, b = s + self.gap + self.btn / 2, self.btn
            oval(c, fy, b / 2 - 0.5, fill=("#b4b4ba" if hot else "#d4d4d8") if light else ("#4a4a52" if hot else "#2c2c32"))
            ink = "#202024" if light else "#e8e8ec"
            h, L, lw = b * 0.24, b * 0.11, max(1.4, b / 18) * k
            for sx in (-1, 1):
                for sy in (-1, 1):
                    x0, y0 = (c + sx * h) * k, (fy + sy * h) * k
                    dr.line((x0, y0, x0 - sx * L * k, y0), fill=ink, width=round(lw))
                    dr.line((x0, y0, x0, y0 - sy * L * k), fill=ink, width=round(lw))
            oval(c, fy, b * 0.07, fill=ink)
        view_m = look_at(v.r.cam.eye(), v.r.cam.target)
        right, up, toward = view_m[0, :3], view_m[1, :3], view_m[2, :3]
        balls = []
        for name, d, col in self.AXES:
            for sign in (1, -1):
                w = np.array(d) * sign
                x, y = c + R * float(w @ right), c - R * float(w @ up)
                balls.append((float(w @ toward), sign, name, col, x, y, tuple(w)))
        balls.sort(key=lambda b: b[0])  # far side first
        font = self._pil_font(br * 1.25 * k)
        self._balls = []
        for depth, sign, name, col, x, y, w in balls:
            hot = self._hover == (name, sign)
            if sign > 0:
                dr.line((c * k, c * k, x * k, y * k), fill=col, width=round(max(2.0, s / 40) * k))
                oval(x, y, br, fill="#ffffff" if hot else col)
                dr.text((x * k, y * k), name, font=font, fill=col if hot else "#101014", anchor="mm")
            else:
                fill = _mix(col, "#ffffff" if light else "#000000", 0.55)
                oval(x, y, br, fill=col if hot else fill, outline=col, width=max(1.2, s / 64))
                if hot:
                    dr.text((x * k, y * k), "-" + name, font=font, fill="#101014", anchor="mm")
            self._balls.append((x, y, br, name, sign, w))
        self._photo = ImageTk.PhotoImage(img.resize((s, self.height), Image.LANCZOS), master=self)
        self.delete("all")
        self.create_image(0, 0, image=self._photo, anchor="nw")

    def _hit(self, x, y):
        if self.on_frame and (x - self.size / 2) ** 2 + (y - self.size - self.gap - self.btn / 2) ** 2 <= \
                (self.btn / 2) ** 2:
            return "frame", 0, None
        for bx, by, br, name, sign, w in reversed(self._balls):  # front-most first
            if (x - bx) ** 2 + (y - by) ** 2 <= (br + 2) ** 2:
                return name, sign, w
        return None

    def _inside(self, x, y):
        return (x - self.size / 2) ** 2 + (y - self.size / 2) ** 2 <= (self.size / 2) ** 2

    def _set_hover(self, h):
        if h != self._hover:
            self._hover = h
            self.request()

    def _motion(self, e):
        hit = self._hit(e.x, e.y)
        h = hit[:2] if hit else ("ring", 0) if self._inside(e.x, e.y) else None
        if h != self._hover and self.on_hint:
            self.on_hint("Frame the model  (F)" if h and h[0] == "frame" else None)
        self._set_hover(h)
        self.configure(cursor="hand2" if hit else "fleur" if self._inside(e.x, e.y) else "")

    def _down(self, e):
        hit = self._hit(e.x, e.y)
        if hit and hit[0] == "frame":
            self._press = None
            self.on_frame()
            return
        self._press = (e.x, e.y, e.x, e.y, False)

    def _drag(self, e):
        if self._press is None or self.view.r is None:
            return
        x0, y0, lx, ly, moved = self._press
        if not moved and abs(e.x - x0) + abs(e.y - y0) < 3:
            return
        self.view.set_fly(False)
        self.view.r.cam.orbit(e.x - lx, e.y - ly)
        self.view.request()
        self._press = (x0, y0, e.x, e.y, True)

    def _up(self, e):
        p, self._press = self._press, None
        if p and not p[4]:
            hit = self._hit(e.x, e.y)
            if hit and hit[0] != "frame":
                self.view.snap_to(hit[2])
        self.request()


def _mix(a, b, t):
    a, b = (tuple(int(h[i:i + 2], 16) for i in (1, 3, 5)) for h in (a, b))
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


class _ViewMixin:
    """Mouse controls, redraw scheduling and the shared API for both widget kinds."""

    def _setup_common(self, on_error, on_ready):
        self.r = None
        self.on_error, self.on_ready = on_error, on_ready
        self._pending_model = None
        self._redraw_job = None
        self._drag = None
        self.failed = ""
        self._keys, self._key_rel_jobs, self._fly_job, self._fly_last = set(), {}, None, 0.0
        self.fly_speed = 1.0
        self.fly = False          # free fly: mouse turns the camera, W A S D Q E move it
        self.on_fly = None        # called with True / False when free fly turns on / off
        self._mw = None
        self._center = None

    def _bind_mouse(self, w):
        # Blender style: middle-drag orbit, Shift+middle-drag pan, wheel zoom (left-drag orbit and right-drag pan
        # too, for mice / touchpads without a usable middle button)
        self._mw = w
        w.bind("<ButtonPress-1>", lambda e: self._press(e, "orbit"))
        w.bind("<ButtonPress-2>", lambda e: self._press(e, "orbit"))
        w.bind("<ButtonPress-3>", lambda e: self._press(e, "pan"))
        w.bind("<Shift-ButtonPress-1>", lambda e: self._press(e, "pan"))
        w.bind("<Shift-ButtonPress-2>", lambda e: self._press(e, "pan"))
        w.winfo_toplevel().bind("<Motion>", self._fly_look, add="+")
        # free fly: a left click anywhere goes back to the normal (Blender) controls, keeping the view
        w.winfo_toplevel().bind("<ButtonPress-1>", lambda e: self.fly and self.set_fly(False), add="+")
        for b in (1, 2, 3):
            w.bind(f"<B{b}-Motion>", self._motion)
            w.bind(f"<ButtonRelease-{b}>", lambda e: setattr(self, "_drag", None))
        # the mouse wheel is handled by the app (zoom / Shift: fly speed / Alt: field of view)
        w.bind("<Double-Button-1>", lambda e: self.frame())

    def _press(self, e, kind):
        if self.fly:
            return
        self._drag = (kind, e.x, e.y)
        try:
            e.widget.focus_set()
        except Exception:
            pass

    def _motion(self, e):
        if not self._drag or self.r is None:
            return
        kind, x, y = self._drag
        dx, dy = e.x - x, e.y - y
        if kind == "orbit":
            self.r.cam.orbit(dx, dy)
        else:
            self.r.cam.pan(dx, dy, self._size()[1])
        self._drag = (kind, e.x, e.y)
        self.request()

    def _wheel(self, steps):
        if self.r is None:
            return
        cam = self.r.cam
        if self.fly:  # move forward / back along the view
            cam.target = cam.target + cam.forward() * steps * max(cam.radius, 0.5) * 0.12 * self.fly_speed
        else:
            cam.zoom(steps)
        self.request()

    # ---------------------------------------------------------- free fly (Ctrl+F / Shift+`)
    FLY_KEYS = {"w": "w", "a": "a", "s": "s", "d": "d", "q": "down", "e": "up", "shift_l": "fast", "shift_r": "fast"}

    def set_fly(self, on):
        on = bool(on) and self.r is not None and self._mw is not None
        if on == self.fly:
            return
        self.fly = on
        self._drag = None
        w = self._mw
        if on:
            self._anchor_near()
            try:
                w.configure(cursor="none")
                w.focus_set()
            except Exception:
                pass
            self._warp_center()
        else:
            self._keys.clear()
            self._center = None
            try:
                w.configure(cursor="")
            except Exception:
                pass
            self._aim_at_model()
            self.request()
        if self.on_fly:
            self.on_fly(on)

    def toggle_fly(self):
        self.set_fly(not self.fly)
        return self.fly

    def _warp_center(self):
        w = self._mw
        cx, cy = max(w.winfo_width(), 2) // 2, max(w.winfo_height(), 2) // 2
        self._center = (w.winfo_rootx() + cx, w.winfo_rooty() + cy)
        try:
            w.event_generate("<Motion>", warp=True, x=cx, y=cy)
        except Exception:
            pass

    def _fly_look(self, e):
        if not self.fly or self._center is None or self.r is None:
            return
        dx, dy = e.x_root - self._center[0], e.y_root - self._center[1]
        if not dx and not dy:
            return
        self.r.cam.look(dx, dy)
        self._warp_center()
        self.request()

    def _aim_at_model(self):
        """Leaving free fly: keep the view, but put the turning point back in front of the camera at the model's
        distance, so orbiting turns around the model again."""
        cam = self.r.cam
        eye, fwd = cam.eye(), cam.forward()
        m = self.r.model
        centre = (np.asarray(m.lo) + np.asarray(m.hi)) / 2 if m is not None else cam.target
        d = float((centre - eye) @ fwd)
        if d < cam.radius * 0.25:
            d = max(cam.radius * 0.5, 0.5)
        cam.target, cam.dist = eye + fwd * d, d

    def key_down(self, keysym):
        k = self.FLY_KEYS.get(keysym.lower())
        if k is None or self.r is None or not self.fly:
            return False
        job = self._key_rel_jobs.pop(k, None)
        if job:
            self.after_cancel(job)  # X11 key repeat sends release+press pairs: ignore the release
        self._keys.add(k)
        if self._fly_job is None:
            self._fly_last = time.time()
            self._fly_job = self.after(16, self._fly_tick)
        return True

    def key_up(self, keysym):
        k = self.FLY_KEYS.get(keysym.lower())
        if k is None or not self.fly:
            return False
        self._key_rel_jobs[k] = self.after(30, lambda: (self._keys.discard(k), self._key_rel_jobs.pop(k, None)))
        return True

    def _anchor_near(self):
        """Starting to fly: bring the turning point close in front of the camera (the camera itself stays put),
        so looking around in a cockpit turns the view instead of swinging it around a point far ahead."""
        cam = self.r.cam
        eye = cam.eye()
        fwd = cam.target - eye
        fwd /= max(np.linalg.norm(fwd), 1e-9)
        near = min(cam.dist, max(cam.radius * 0.06, 0.25))
        cam.target = eye + fwd * near
        cam.dist = near

    def keys_released(self):
        self._keys.clear()

    def _fly_tick(self):
        now = time.time()
        dt = min(now - self._fly_last, 0.05)
        self._fly_last = now
        moving = self._keys - {"fast"}
        if not moving or self.r is None:
            self._fly_job = None
            return
        cam = self.r.cam
        fwd = cam.target - cam.eye()
        fwd /= max(np.linalg.norm(fwd), 1e-9)
        right = np.cross(fwd, (0.0, 1.0, 0.0))
        right /= max(np.linalg.norm(right), 1e-9)
        up = np.array([0.0, 1.0, 0.0])
        k = self._keys
        move = fwd * (("w" in k) - ("s" in k)) + right * (("d" in k) - ("a" in k)) + up * (("up" in k) - ("down" in k))
        speed = max(cam.radius, 0.5) * 0.6 * (4.0 if "fast" in k else 1.0) * self.fly_speed  # sizes / second
        cam.target = cam.target + move * speed * dt
        self.request()
        self._fly_job = self.after(16, self._fly_tick)

    # ---------------------------------------------------------- public API
    @property
    def opts(self):
        return self.r.opts if self.r else {}

    def change_fly_speed(self, steps):
        self.fly_speed = float(min(max(self.fly_speed * (1.25 ** steps), 0.01), 40.0))
        return self.fly_speed

    def change_fov(self, steps):
        if self.r is None:
            return 0
        self.r.cam.fov = float(min(max(self.r.cam.fov + 3.0 * steps, 8.0), 110.0))
        self.request()
        return self.r.cam.fov

    def frame(self):
        self.set_fly(False)
        if self.r and self.r.model is not None:
            self.r.cam.frame(self.r.model.lo, self.r.model.hi)
            self.request()

    def set_model(self, model, keep_camera=False, livery=None):
        if self.r is None:
            self._pending_model = (model, livery)
            return
        if not keep_camera:
            self.set_fly(False)
        self._make_current()
        self.r.set_model(model, keep_camera=keep_camera)
        if livery:
            self.r.set_livery(livery)
        self.request()

    def set_livery(self, spec):
        if self.r is None:
            return
        self._make_current()
        self.r.set_livery(spec)
        self.request()

    def set_pose(self, bone_k, angles):
        m = self.r.model
        jn = m.joints[bone_k]
        if any(angles):
            self.r.opts["pose"][jn] = tuple(angles)
        else:
            self.r.opts["pose"].pop(jn, None)
        self._make_current()
        self.r.update_pose()
        self.request()

    def reset_pose(self):
        self.r.opts["pose"].clear()
        self._make_current()
        self.r.update_pose()
        self.request()

    def bake(self, spec, jobs):
        self._make_current()
        try:
            return self.r.bake(spec, jobs)
        finally:
            self.request()

    def screenshot(self, path):
        from PIL import Image
        w, h = self._size()
        self._make_current()
        scale = 2 if max(w, h) < 2000 else 1
        img = self.r.read_pixels(w * scale, h * scale)
        Image.fromarray(img).save(path)
        self.request()
        return path

    def request(self):
        if self._redraw_job is None:
            self._redraw_job = self.after_idle(self._redraw_now)
        g = getattr(self, "gizmo", None)
        if g is not None:
            g.request()

    def snap_to(self, back, ms=220):
        """Turn the camera (around the point it looks at) so it looks from direction `back`, animated."""
        if self.r is None:
            return
        self.set_fly(False)
        cam = self.r.cam
        bx, by, bz = back
        if abs(by) > 0.99:
            yaw1, pitch1 = 0.0, math.copysign(1.5697, by)  # top / bottom: keep "forward" pointing up the screen
        else:
            yaw1, pitch1 = math.atan2(bx, bz), math.asin(max(-1.0, min(1.0, by)))
        yaw0, pitch0 = cam.yaw, cam.pitch
        d = (yaw1 - yaw0 + math.pi) % (2 * math.pi) - math.pi  # the short way round
        t0 = time.time()
        if getattr(self, "_snap_job", None):
            self.after_cancel(self._snap_job)

        def step():
            t = min((time.time() - t0) * 1000 / ms, 1.0)
            e = t * t * (3 - 2 * t)
            cam.yaw, cam.pitch = yaw0 + d * e, pitch0 + (pitch1 - pitch0) * e
            self.request()
            self._snap_job = self.after(15, step) if t < 1 else None
        step()

    def _ready(self):
        if self._pending_model is not None:
            (m, liv), self._pending_model = self._pending_model, None
            self.r.set_model(m)
            if liv:
                self.r.set_livery(liv)
        if self.on_ready:
            self.on_ready(self.r)

    def _fail(self, e):
        self.failed = str(e) or e.__class__.__name__
        if self.on_error:
            self.on_error(self.failed)


def _make_window_view(parent, bg, on_error, on_ready):
    import pyopengltk.win32 as pw
    from pyopengltk import OpenGLFrame
    pw.pfd.cDepthBits = 24  # the window itself; drawing happens in a 24-bit multisampled buffer anyway

    class _WindowView(OpenGLFrame, _ViewMixin):
        def __init__(self, parent):
            OpenGLFrame.__init__(self, parent)
            self._setup_common(on_error, on_ready)
            self._bind_mouse(self)
            self._inited = False

        def initgl(self):  # pyopengltk also calls this on every resize
            if self._inited or self.failed:
                return
            self._inited = True
            try:
                self.r = Renderer()
            except Exception as e:
                self.r = None
                self._fail(e)
                return
            self._ready()

        def _size(self):
            return max(1, self.winfo_width()), max(1, self.winfo_height())

        def _make_current(self):
            self.tkMakeCurrent()

        def redraw(self):
            if self.r is None:
                return
            try:
                self.r.render(*self._size(), 0)
            except Exception as e:
                self._fail(e)
                self.r = None

        def _redraw_now(self):
            self._redraw_job = None
            if self.winfo_ismapped() and self.r is not None:
                self._display()

    v = _WindowView(parent)
    return v


class _OffscreenView(tk.Frame, _ViewMixin):
    """EGL off-screen rendering shown through a Tk image (Linux etc.)."""

    def __init__(self, parent, bg, on_error, on_ready):
        tk.Frame.__init__(self, parent, bg=bg)
        self._setup_common(on_error, on_ready)
        self.label = tk.Label(self, bg=bg, bd=0, highlightthickness=0)
        self.label.place(x=0, y=0, relwidth=1, relheight=1)
        self._bind_mouse(self.label)
        self._photo = None
        self.bind("<Configure>", lambda e: self.request())
        self.after(10, self._init_gl)

    def _init_gl(self):
        try:
            _egl_context()
            self.r = Renderer()
        except Exception as e:
            self.r = None
            self._fail(e)
            return
        self._ready()
        self.request()

    def _size(self):
        return max(1, self.winfo_width()), max(1, self.winfo_height())

    def _make_current(self):
        pass

    def _redraw_now(self):
        self._redraw_job = None
        if self.r is None or not self.winfo_ismapped():
            return
        from PIL import Image, ImageTk
        w, h = self._size()
        try:
            img = Image.fromarray(self.r.read_pixels(w, h))
        except Exception as e:
            self._fail(e)
            self.r = None
            return
        if self._photo is not None and (self._photo.width(), self._photo.height()) == (w, h):
            self._photo.paste(img)
        else:
            self._photo = ImageTk.PhotoImage(img)
            self.label.configure(image=self._photo)


_EGL = None


def _egl_context():
    global _EGL
    if _EGL is not None:
        return
    import ctypes
    from OpenGL import EGL
    d = EGL.eglGetDisplay(EGL.EGL_DEFAULT_DISPLAY)
    if not EGL.eglInitialize(d, None, None):
        raise RuntimeError("EGL unavailable")
    attrs = (EGL.EGLint * 13)(EGL.EGL_SURFACE_TYPE, EGL.EGL_PBUFFER_BIT, EGL.EGL_RED_SIZE, 8, EGL.EGL_GREEN_SIZE, 8,
                              EGL.EGL_BLUE_SIZE, 8, EGL.EGL_DEPTH_SIZE, 24, EGL.EGL_RENDERABLE_TYPE,
                              EGL.EGL_OPENGL_BIT, EGL.EGL_NONE)
    cfg, n = EGL.EGLConfig(), EGL.EGLint()
    EGL.eglChooseConfig(d, attrs, ctypes.pointer(cfg), 1, ctypes.pointer(n))
    if n.value < 1:
        raise RuntimeError("no EGL OpenGL config")
    surf = EGL.eglCreatePbufferSurface(d, cfg, (EGL.EGLint * 5)(EGL.EGL_WIDTH, 16, EGL.EGL_HEIGHT, 16, EGL.EGL_NONE))
    EGL.eglBindAPI(EGL.EGL_OPENGL_API)
    ctx = EGL.eglCreateContext(d, cfg, EGL.EGL_NO_CONTEXT, None)
    if not EGL.eglMakeCurrent(d, surf, surf, ctx):
        raise RuntimeError("couldn't start EGL")
    _EGL = (d, surf, ctx)


# ====================================================================== command line: render a GLB to a PNG (tests)

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Render a GLB with the app's viewer (headless check).")
    ap.add_argument("glb")
    ap.add_argument("out")
    ap.add_argument("--size", default="1400x860")
    ap.add_argument("--mode", default="lit")
    ap.add_argument("--yaw", type=float)
    ap.add_argument("--pitch", type=float)
    ap.add_argument("--wire", action="store_true")
    ap.add_argument("--skeleton", action="store_true")
    ap.add_argument("--zoom", type=float, default=0)
    a = ap.parse_args()
    _egl_context()
    r = Renderer()
    print(r.version, "|", r.renderer_name, "| samples", r.samples, flush=True)
    model = load_glb(a.glb)
    r.set_model(model)
    print(model_stats(model), flush=True)
    r.opts.update(mode=a.mode, wire=a.wire, skeleton=a.skeleton)
    if a.yaw is not None:
        r.cam.yaw = math.radians(a.yaw)
    if a.pitch is not None:
        r.cam.pitch = math.radians(a.pitch)
    r.cam.zoom(a.zoom)
    w, h = (int(v) for v in a.size.split("x"))
    from PIL import Image
    Image.fromarray(r.read_pixels(w, h)).save(a.out)
    print("wrote", a.out)
