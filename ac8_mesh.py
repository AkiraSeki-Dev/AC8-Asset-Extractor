"""Cooked UE5.4 mesh reader for ACE COMBAT 8 (retoc 'to-legacy' output).

Reads the highest-detail LOD of StaticMesh and SkeletalMesh packages: positions, normals, tangents, UVs, vertex
colours, triangles, material sections, and for skeletal meshes the bone hierarchy and skin weights.
Properties are stored "unversioned" (they can't be read without the game's type mappings), so the native render
data that follows them is located by its own structure and then read field by field.
"""
import re
import struct

import numpy as np

from ac8_package import Package, Reader

POS_SIG = re.compile(rb"\x0c\x00\x00\x00(....)\x0c\x00\x00\x00\1", re.S)


class MeshError(Exception):
    pass


class Section:
    __slots__ = ("material", "first_index", "num_tris", "min_vert", "max_vert", "bone_map")

    def __init__(self, material, first_index, num_tris, min_vert=0, max_vert=0, bone_map=None):
        self.material, self.first_index, self.num_tris = material, first_index, num_tris
        self.min_vert, self.max_vert, self.bone_map = min_vert, max_vert, bone_map


class Mesh:
    def __init__(self, name):
        self.name = name
        self.positions = None   # (N,3) float32, UE space (cm, Z-up, left-handed)
        self.normals = None     # (N,3)
        self.tangents = None    # (N,4)
        self.uvs = []           # list of (N,2)
        self.colors = None      # (N,4) uint8
        self.indices = None     # (M,) uint32
        self.sections = []
        self.materials = []     # [(slot name, material import path parts)]
        self.bones = []         # [(name, parent, (rot xyzw), (pos xyz), (scale xyz))]
        self.joints = None      # (N,4/8) uint16 bone indices (global)
        self.weights = None     # (N,4/8) float32


# ------------------------------------------------------------------ shared buffer readers

def read_bulk_array(r, elem_expected=None):
    elem, count = r.i32(), r.i32()
    if elem_expected is not None and elem != elem_expected:
        raise MeshError(f"unexpected element size {elem} (wanted {elem_expected}) at {r.p - 8}")
    if count < 0 or elem < 0 or elem * count > len(r.b) - r.p:
        raise MeshError(f"bad bulk array {elem}x{count} at {r.p - 8}")
    return elem, count, r.take(elem * count)


def unpack_normals(raw, n):
    """FPackedNormal x2 (TangentX, TangentZ) per vertex, or FPackedRGBA16N x2 when high precision.
    UE5 stores these as SIGNED values (int8 / 127, int16 / 32767) — UE4 used biased unsigned bytes."""
    if len(raw) == n * 8:
        a = np.frombuffer(raw, np.int8).reshape(n, 2, 4).astype(np.float32) / 127.0
    elif len(raw) == n * 16:
        a = np.frombuffer(raw, np.int16).reshape(n, 2, 4).astype(np.float32) / 32767.0
    else:
        raise MeshError("tangent buffer size mismatch")
    tx, tz = a[:, 0, :], a[:, 1, :]
    return tz[:, :3], np.concatenate([tx[:, :3], np.where(tz[:, 3:4] < 0, -1.0, 1.0)], axis=1)


def read_static_vertex_buffer(r, n):
    """FStaticMeshVertexBuffer: tangents + UV channels."""
    r.skip(2)  # strip flags
    num_uv, nverts = r.i32(), r.i32()
    full_uv, hi_tan = r.i32(), r.i32()
    if nverts != n or not 0 < num_uv <= 8:
        raise MeshError(f"vertex buffer mismatch: {nverts} verts / {num_uv} uv (expected {n})")
    _e, _c, tan = read_bulk_array(r)
    normals, tangents = unpack_normals(tan, n)
    elem, count, uvraw = read_bulk_array(r)
    if count != n * num_uv:
        raise MeshError("uv count mismatch")
    dtype = np.float32 if elem == 8 else np.float16
    uv = np.frombuffer(uvraw, dtype).reshape(n, num_uv, 2).astype(np.float32)
    return normals, tangents, [uv[:, i, :].copy() for i in range(num_uv)]


def read_color_buffer(r, n):
    r.skip(2)
    _stride, nverts = r.i32(), r.i32()
    if nverts == 0:
        return None
    elem, count, raw = read_bulk_array(r)
    if count != n or elem != 4:
        return None
    bgra = np.frombuffer(raw, np.uint8).reshape(n, 4)
    return bgra[:, [2, 1, 0, 3]].copy()


def read_raw_index_buffer(r):
    """FRawStaticIndexBuffer: bool32 is32bit, bulk bytes, bool32 shouldExpandTo32Bit."""
    is32 = r.i32()
    elem, count, raw = read_bulk_array(r)
    r.i32()  # bShouldExpandTo32Bit
    if is32:
        return np.frombuffer(raw, np.uint32).astype(np.uint32)
    return np.frombuffer(raw, np.uint16).astype(np.uint32)


def find_static_materials(pkg, data):
    """StaticMaterials (end of the export): count, then per slot: material index, slot FName, UV channel data.
    Every plausible array near the end is scored: one that ends exactly at the end of the export and whose slots
    point at real material imports wins (an array of empty slots named e.g. 'BlockAll' is a false match)."""
    entry = 36
    best, best_score = None, None
    for start in range(len(data) - 4 - entry, max(0, len(data) - 8192), -1):
        n = struct.unpack_from("<i", data, start)[0]
        if not 0 < n <= 256 or start + 4 + n * entry > len(data):
            continue
        ok = True
        slots, real = [], 0
        for i in range(n):
            mat, nidx, nnum = struct.unpack_from("<iii", data, start + 4 + i * entry)
            if not (mat < 0 and -mat - 1 < len(pkg.imports)) and mat != 0:
                ok = False
                break
            if not 0 <= nidx < len(pkg.names):
                ok = False
                break
            cls = pkg.imports[-mat - 1]["class"] if mat < 0 else ""
            if mat != 0 and "Material" not in cls:
                ok = False
                break
            real += mat != 0
            slots.append((pkg.names[nidx], pkg.import_path(mat) if mat else None))
        if not ok:
            continue
        score = (start + 4 + n * entry == len(data), real, n)
        if best_score is None or score > best_score:
            best, best_score = slots, score
    return best or []


# ------------------------------------------------------------------ static mesh

def read_static_mesh(path):
    pkg = Package(path)
    exp = pkg.main_export({"StaticMesh"})
    if exp is None:
        raise MeshError("no StaticMesh export")
    data, start = pkg.export_data(exp)
    mesh = None
    errors = []
    # 1. walk the LOD headers in order (LOD 0 comes first). A LOD is either inlined (its buffers follow right away)
    #    or streamed (its buffers live in a data resource — the .ubulk next to the package).
    for at, k, inlined, after in _lod_headers(data):
        try:
            if inlined:
                r = Reader(data, after + 2)  # buffer strip flags
            else:
                (idx,) = struct.unpack_from("<i", data, after)
                blob = pkg.bulk(idx)
                if not blob:
                    raise MeshError("streamed LOD data is missing (.ubulk not unpacked?)")
                r = Reader(blob, 2)
            mesh = _read_lod(r, exp["name"], data, at, k)
            break
        except Exception as e:  # not a real header (or unreadable): try the next one
            errors.append(str(e))
    # 2. old way: find the position buffer and look for an inlined header right before it
    if mesh is None:
        mesh = _read_lod_by_buffer(data, exp["name"])
    mesh.materials = find_static_materials(pkg, data)
    _check(mesh)
    return mesh


def _lod_headers(data, max_sections=64):
    """FStaticMeshLODResources headers in `data`, in file order: (offset, section count, inlined, offset after the
    two flags). Layout: count, sections (40 bytes each), max deviation (float), cooked-out flag, inlined flag."""
    out = []
    raw = np.frombuffer(data, np.uint8)
    for shift in range(4):
        n = (len(raw) - shift) // 4
        if n <= 0:
            continue
        ints = np.frombuffer(raw[shift:shift + n * 4].tobytes(), "<i4")
        for i in np.nonzero((ints >= 1) & (ints <= max_sections))[0]:
            at = shift + int(i) * 4
            k = int(ints[i])
            flags = at + 4 + 40 * k + 4
            if flags + 12 > len(data):
                continue
            cooked, inlined = struct.unpack_from("<II", data, flags)
            if cooked != 0 or inlined not in (0, 1):
                continue
            (dev,) = struct.unpack_from("<f", data, flags - 4)
            if not (dev == dev and 0.0 <= dev < 1e6):
                continue
            if not _sections_chain_ok(data, at + 4, k, 40):
                continue
            out.append((at, k, bool(inlined), flags + 8))
    out.sort()
    return out


def _sections_at(data, at, k):
    sections = []
    for i in range(k):
        mat, first, ntri, vmin, vmax = struct.unpack_from("<iiiii", data, at + 4 + i * 40)
        sections.append(Section(mat, first, ntri, vmin, vmax))
    return sections


def _read_buffers(r, mesh):
    """SerializeBuffers from the stride on: positions, tangents/UVs, colours, indices."""
    _stride, n = r.i32(), r.i32()
    _e, _c, raw = read_bulk_array(r, 12)
    mesh.positions = np.frombuffer(raw, np.float32).reshape(n, 3).copy()
    mesh.normals, mesh.tangents, mesh.uvs = read_static_vertex_buffer(r, n)
    mesh.colors = read_color_buffer(r, n)
    mesh.indices = read_raw_index_buffer(r)


def _read_lod(r, name, data, at, k):
    mesh = Mesh(name)
    mesh.sections = _sections_at(data, at, k)
    _read_buffers(r, mesh)
    n = len(mesh.positions)
    if any(s_.num_tris and s_.max_vert >= n for s_ in mesh.sections):
        raise MeshError("sections point past the vertices")
    _check(mesh)
    return mesh


def _read_lod_by_buffer(data, name):
    """Fallback: find the position buffer (stride 12, count, 12-byte bulk array) and an inlined header before it."""
    lod_start, found_buffer = None, False
    for m in POS_SIG.finditer(data):
        found_buffer = True
        p = m.start()
        nverts = struct.unpack_from("<i", data, p + 4)[0]
        for k in range(1, 256):  # walk back over: strip(2) count sections(40 each) maxdev cookedout inlined strip(2)
            at = p - 2 - 12 - 40 * k - 4
            if at < 2:
                break
            if struct.unpack_from("<i", data, at)[0] == k and _sections_chain_ok(data, at + 4, k, 40, nverts):
                lod_start = at
                break
        if lod_start is not None:
            break
    if not found_buffer:
        raise MeshError("no vertex buffer found")
    if lod_start is None:
        raise MeshError("could not locate the LOD header")
    k = struct.unpack_from("<i", data, lod_start)[0]
    return _read_lod(Reader(data, lod_start + 4 + 40 * k + 12 + 2), name, data, lod_start, k)


def _sections_chain_ok(data, at, k, size, nverts=None):
    """Sections cover the index buffer in non-overlapping ranges that start at 0. They are usually stored in index
    order, but not always (SM_PI501_001 lists them by material), so the ranges are checked sorted. Empty sections
    (0 triangles, sometimes with odd vertex ranges) are allowed, and so are small gaps between ranges."""
    ranges = []
    for i in range(k):
        mat, first, ntri, vmin, vmax = struct.unpack_from("<iiiii", data, at + i * size)
        if not (0 <= mat < 256 and ntri >= 0 and first >= 0):
            return False
        if ntri == 0:
            continue
        if vmin < 0 or vmin > vmax or (nverts is not None and vmax >= nverts):
            return False
        ranges.append((first, first + ntri * 3))
    if not ranges:
        return False
    ranges.sort()
    if ranges[0][0] != 0:
        return False
    return all(b_[0] >= a_[1] for a_, b_ in zip(ranges, ranges[1:]))


def _check(mesh):
    n = len(mesh.positions)
    if mesh.indices is None or len(mesh.indices) % 3 or (len(mesh.indices) and mesh.indices.max() >= n):
        raise MeshError("index buffer doesn't fit the vertices")
    total = sum(s.num_tris for s in mesh.sections) * 3
    if total > len(mesh.indices):
        raise MeshError("sections point past the index buffer")


# ------------------------------------------------------------------ skeletal mesh

def find_ref_skeleton(pkg, data):
    """FReferenceSkeleton: bone infos (FName, parent), bind pose (FTransform as doubles), name->index map.
    All three arrays have the same count; that's how it is told apart from anything else."""
    n_names = len(pkg.names)
    for at in range(0, min(len(data), 1 << 20) - 16):
        n = struct.unpack_from("<i", data, at)[0]
        if not 1 <= n <= 4096:
            continue
        ni, nn, par = struct.unpack_from("<iii", data, at + 4)
        if par != -1 or not 0 <= ni < n_names:
            continue
        pose_at = at + 4 + n * 12
        if pose_at + 4 > len(data) or struct.unpack_from("<i", data, pose_at)[0] != n:
            continue
        map_at = pose_at + 4 + n * 80
        if map_at + 4 > len(data) or struct.unpack_from("<i", data, map_at)[0] != n:
            continue
        bones_raw = [struct.unpack_from("<iii", data, at + 4 + i * 12) for i in range(n)]
        if any(not (0 <= b[0] < n_names) or (i and not 0 <= b[2] < i) for i, b in enumerate(bones_raw)):
            continue
        bones = []
        for i, (ni, nn, par) in enumerate(bones_raw):
            v = struct.unpack_from("<10d", data, pose_at + 4 + i * 80)
            name = pkg.names[ni] if nn == 0 else f"{pkg.names[ni]}_{nn - 1}"
            bones.append((name, par, v[0:4], v[4:7], v[7:10]))
        return at, bones, map_at + 4 + n * 12
    raise MeshError("bone hierarchy not found")


def find_skeletal_materials(pkg, data, bones_at):
    """FSkeletalMaterial array right before the skeleton: material, slot FName, [imported-name flag/FName], UV data."""
    for size in (40, 48):
        for k in range(1, 257):
            at = bones_at - 4 - k * size
            if at < 0:
                break
            if struct.unpack_from("<i", data, at)[0] != k:
                continue
            slots = []
            for i in range(k):
                mat, ni, nn = struct.unpack_from("<iii", data, at + 4 + i * size)
                if mat > 0 or (mat < 0 and -mat - 1 >= len(pkg.imports)) or not 0 <= ni < len(pkg.names):
                    slots = None
                    break
                slots.append((pkg.names[ni], pkg.import_path(mat) if mat else None))
            if slots is not None:
                return slots
    return []


def read_skeletal_mesh(path):
    pkg = Package(path)
    exp = pkg.main_export({"SkeletalMesh"})
    if exp is None:
        raise MeshError("no SkeletalMesh export")
    data, _start = pkg.export_data(exp)
    bones_at, bones, after = find_ref_skeleton(pkg, data)
    mesh = Mesh(exp["name"])
    mesh.bones = bones
    mesh.materials = find_skeletal_materials(pkg, data, bones_at)
    r = Reader(data, after)
    cooked, nlods = r.i32(), r.i32()
    if cooked != 1 or not 1 <= nlods <= 16:
        raise MeshError(f"unexpected render data header ({cooked}, {nlods})")
    # LOD 0
    r.skip(2)  # strip flags
    cooked_out, inlined = r.i32(), r.i32()
    if cooked_out:
        raise MeshError("LOD 0 is cooked out")
    nreq = r.i32()
    r.skip(nreq * 2)  # required bones
    nsec = r.i32()
    if not 0 < nsec < 512:
        raise MeshError(f"bad section count {nsec}")
    sec_verts = []
    for _ in range(nsec):
        r.skip(2)
        mat = struct.unpack("<h", r.take(2))[0]
        base, ntri = r.i32(), r.i32()
        r.skip(4 + 1 + 4 + 4)  # recompute tangent, mask channel, cast shadow, visible in ray tracing
        base_vert = r.u32()
        if r.i32() != 0:
            raise MeshError("cloth mapping data not supported")
        nb = r.i32()
        bone_map = np.frombuffer(r.take(nb * 2), np.uint16).copy()
        nv, _max_inf = r.i32(), r.i32()
        r.skip(2 + 20)  # cloth asset index, clothing data
        nd = r.i32()
        r.skip(nd * 4)
        ni = r.i32()
        r.skip(ni * 8)
        r.skip(4)  # disabled
        mesh.sections.append(Section(mat, base, ntri, base_vert, base_vert + nv - 1, bone_map))
        sec_verts.append((base_vert, nv, bone_map))
    nact = r.i32()
    r.skip(nact * 2)
    r.skip(4)  # buffers size
    if not inlined:
        raise MeshError("LOD 0 is stored outside the package (not supported yet)")
    r.skip(2)  # strip flags
    idx_size = r.u8()
    elem, count, raw = read_bulk_array(r)
    mesh.indices = np.frombuffer(raw, np.uint32 if elem == 4 else np.uint16).astype(np.uint32)
    _stride, n = r.i32(), r.i32()
    _e, _c, raw = read_bulk_array(r, 12)
    mesh.positions = np.frombuffer(raw, np.float32).reshape(n, 3).copy()
    mesh.normals, mesh.tangents, mesh.uvs = read_static_vertex_buffer(r, n)
    # skin weights
    r.skip(2)
    variable, max_inf, _total, nverts = r.i32(), r.i32(), r.u32(), r.u32()
    idx16, w16 = r.i32(), r.i32()
    if nverts != n:
        raise MeshError("skin weight count mismatch")
    _e, _c, raw = read_bulk_array(r, 1)
    ib, wb = (2 if idx16 else 1), (2 if w16 else 1)
    if variable:
        # variable influences: a lookup buffer gives each vertex (offset << 8 | count) into the weight data,
        # which holds that vertex's bone indices followed by its weights
        r.skip(2)  # the lookup buffer's own strip flags
        nlook = r.i32()
        _e, _c, look = read_bulk_array(r, 4)
        look = np.frombuffer(look, np.uint32)
        if nlook != n or len(look) != n:
            raise MeshError("skin weight lookup count mismatch")
        offs, cnts = (look >> 8).astype(np.int64), (look & 0xFF).astype(np.int64)
        if cnts.max(initial=0) > max(max_inf, 1) or (offs + cnts * (ib + wb)).max(initial=0) > len(raw):
            raise MeshError("skin weight lookup out of range")
        data = np.frombuffer(raw, np.uint8)
        local = np.zeros((n, max_inf), np.int32)
        w = np.zeros((n, max_inf), np.float32)
        for k in range(max_inf):
            has = cnts > k
            if not has.any():
                break
            o = offs[has]
            c = cnts[has]
            io = o + k * ib
            wo = o + c * ib + k * wb
            if idx16:
                local[has, k] = data[io] | (data[io + 1].astype(np.int32) << 8)
            else:
                local[has, k] = data[io]
            if w16:
                w[has, k] = (data[wo] | (data[wo + 1].astype(np.int32) << 8)) / 65535.0
            else:
                w[has, k] = data[wo] / 255.0
    else:
        per = max_inf * (ib + wb)
        if len(raw) != n * per:
            raise MeshError("skin weight size mismatch")
        a = np.frombuffer(raw, np.uint8).reshape(n, per)
        local = a[:, :max_inf * ib].copy().view(np.uint16 if idx16 else np.uint8).reshape(n, max_inf).astype(np.int32)
        w = a[:, max_inf * ib:].copy().view(np.uint16 if w16 else np.uint8).reshape(n, max_inf).astype(np.float32)
        w /= 65535.0 if w16 else 255.0
    joints = np.zeros((n, max_inf), np.int32)
    for base_vert, nv, bone_map in sec_verts:
        sl = slice(base_vert, base_vert + nv)
        joints[sl] = bone_map[np.clip(local[sl], 0, len(bone_map) - 1)]
    s = w.sum(1, keepdims=True)
    w = np.where(s > 0, w / np.maximum(s, 1e-9), np.eye(1, max_inf, dtype=np.float32))
    mesh.joints, mesh.weights = joints.astype(np.uint16), w.astype(np.float32)
    _check(mesh)
    return mesh


def read_mesh(path):
    """Read a StaticMesh or SkeletalMesh package (whichever it is)."""
    pkg = Package(path)
    classes = {pkg.export_class(e) for e in pkg.exports}
    if "SkeletalMesh" in classes:
        return read_skeletal_mesh(path)
    if "StaticMesh" in classes:
        return read_static_mesh(path)
    raise MeshError("not a mesh package")


def is_mesh_package(path):
    try:
        head = open(path, "rb").read()
    except OSError:
        return False
    return b"\x00StaticMesh\x00" in head or b"\x00SkeletalMesh\x00" in head
