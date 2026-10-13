"""GLB -> FBX (binary FBX 7.4), written directly — no Blender or other program needed.

    python ac8_fbx.py <file.glb | folder> <out.fbx | out folder>

Writes the mesh (normals, UVs, one material slot per part), the skeleton and skin weights, a bind pose, and the
colour / normal textures embedded in the file. Units are centimetres, Y up (what Unreal, Maya and 3ds Max expect;
Blender converts on import).
"""
import json
import math
import struct
import sys
import time
import zlib
from pathlib import Path

import numpy as np

FBX_VERSION = 7400
HEAD = b"Kaydara FBX Binary  \x00\x1a\x00"
# These three fixed values belong together: FBX readers check the file footer against FileId / CreationTime.
FILE_ID = b"\x28\xb3\x2a\xeb\xb6\x24\xcc\xc2\xbf\xc8\xb0\x2a\xa9\x2b\xfc\xf1"
TIME_ID = "1970-01-01 10:00:00:000"
FOOT_ID = b"\xfa\xbc\xab\x09\xd0\xc8\xd4\x66\xb1\x76\xfb\x83\x1c\xf7\x26\x7e"
FOOT_END = b"\xf8\x5a\x8c\x6a\xde\xf5\xd9\x7e\xec\xe9\x0c\xe3\x75\x8f\x29\x0b"
CM = 100.0  # glTF metres -> FBX centimetres


# ====================================================================== binary FBX writer

class I32(int):
    pass


class I64(int):
    pass


class Node:
    __slots__ = ("name", "props", "children")

    def __init__(self, name, *props, children=None):
        self.name, self.props, self.children = name, props, list(children or [])

    def add(self, name, *props, children=None):
        n = Node(name, *props, children=children)
        self.children.append(n)
        return n


def _prop(v):
    if isinstance(v, bool):
        return b"C" + bytes((1 if v else 0,))
    if isinstance(v, I64):
        return b"L" + struct.pack("<q", v)
    if isinstance(v, I32):
        return b"I" + struct.pack("<i", v)
    if isinstance(v, int):
        return (b"I" + struct.pack("<i", v)) if -2 ** 31 <= v < 2 ** 31 else (b"L" + struct.pack("<q", v))
    if isinstance(v, float):
        return b"D" + struct.pack("<d", v)
    if isinstance(v, str):
        b = v.encode("utf-8")
        return b"S" + struct.pack("<I", len(b)) + b
    if isinstance(v, (bytes, bytearray)):
        return b"R" + struct.pack("<I", len(v)) + bytes(v)
    if isinstance(v, np.ndarray):
        code = {np.dtype(np.float64): b"d", np.dtype(np.float32): b"f", np.dtype(np.int32): b"i",
                np.dtype(np.int64): b"l"}[v.dtype]
        raw = np.ascontiguousarray(v).tobytes()
        if len(raw) > 128:
            data, enc = zlib.compress(raw, 6), 1
        else:
            data, enc = raw, 0
        return code + struct.pack("<III", v.size, enc, len(data)) + data
    raise TypeError(f"can't store {type(v)} in FBX")


SENTINEL = b"\x00" * 13


def _encode(node, offset):
    """-> bytes of this node, given the file offset where it starts."""
    props = b"".join(_prop(p) for p in node.props)
    name = node.name.encode()
    head_len = 12 + 1 + len(name) + len(props)
    parts = []
    pos = offset + head_len
    for c in node.children:
        cb = _encode(c, pos)
        parts.append(cb)
        pos += len(cb)
    if node.children or not node.props:
        parts.append(SENTINEL)
        pos += len(SENTINEL)
    return b"".join([struct.pack("<III", pos, len(node.props), len(props)), bytes((len(name),)), name, props] + parts)


def write_fbx(path, nodes):
    out = bytearray(HEAD + struct.pack("<I", FBX_VERSION))
    for n in nodes:
        out += _encode(n, len(out))  # each top-level node is built in one piece, then appended
    out += SENTINEL
    out += FOOT_ID + b"\x00" * 4
    pad = ((len(out) + 15) & ~15) - len(out)
    out += b"\x00" * (pad or 16)
    out += struct.pack("<I", FBX_VERSION) + b"\x00" * 120 + FOOT_END
    Path(path).write_bytes(bytes(out))


def P(name, ptype, label, flags, *values):
    return Node("P", name, ptype, label, flags, *values)


def props70(*ps):
    return Node("Properties70", children=list(ps))


def name_class(name, cls):
    return f"{name}\x00\x01{cls}"


# ====================================================================== GLB reading

_CT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
_NC = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def read_glb(path):
    data = Path(path).read_bytes()
    if data[:4] != b"glTF":
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

    def view(vi):
        v = j["bufferViews"][vi]
        o = v.get("byteOffset", 0)
        return binc[o:o + v["byteLength"]], v.get("byteStride")

    def acc(ai):
        a = j["accessors"][ai]
        dt, nc, cnt = _CT[a["componentType"]], _NC[a["type"]], a["count"]
        raw, stride = view(a["bufferView"])
        o = a.get("byteOffset", 0)
        isz = np.dtype(dt).itemsize
        if stride and stride != isz * nc:
            return np.ndarray((cnt, nc), dt, buffer=raw, offset=o, strides=(stride, isz)).copy()
        return np.frombuffer(raw, dt, cnt * nc, o).reshape(cnt, nc).copy()

    return j, view, acc


def quat_mat3(q):
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def euler_xyz(r):
    """Rotation matrix -> FBX 'Lcl Rotation' degrees (order XYZ: R = Rz @ Ry @ Rx)."""
    sy = -r[2, 0]
    sy = max(-1.0, min(1.0, sy))
    y = math.asin(sy)
    if abs(sy) < 0.999999:
        x = math.atan2(r[2, 1], r[2, 2])
        z = math.atan2(r[1, 0], r[0, 0])
    else:  # gimbal lock
        x = math.atan2(-r[1, 2], r[1, 1])
        z = 0.0
    return [math.degrees(x), math.degrees(y), math.degrees(z)]


def local_matrix(nd):
    m = np.identity(4)
    if "matrix" in nd:
        return np.array(nd["matrix"], np.float64).reshape(4, 4).T
    m[:3, :3] = quat_mat3(nd.get("rotation", (0, 0, 0, 1))) * np.asarray(nd.get("scale", (1, 1, 1)))[None, :]
    m[:3, 3] = nd.get("translation", (0, 0, 0))
    return m


def to_cm(m):
    m = m.copy()
    m[:3, 3] *= CM
    return m


def fbx_matrix(m):
    return np.ascontiguousarray(m.T.reshape(-1), np.float64)  # FBX stores matrices column by column


# ====================================================================== GLB -> FBX

class IdGen:
    def __init__(self):
        self.n = 1000000

    def __call__(self):
        self.n += 1
        return I64(self.n)


def convert(glb_path, fbx_path):
    j, view, acc = read_glb(glb_path)
    nid = IdGen()
    objects, conns = Node("Objects"), Node("Connections")
    counts = {}

    def count(kind, n=1):
        counts[kind] = counts.get(kind, 0) + n

    def connect(child, parent, prop=None):
        if prop:
            conns.add("C", "OP", child, parent, prop)
        else:
            conns.add("C", "OO", child, parent)

    nodes = j.get("nodes", [])
    parent_of = {}
    for i, nd in enumerate(nodes):
        for c in nd.get("children", []):
            parent_of[c] = i
    world = {}

    def world_of(i):
        if i not in world:
            p = parent_of.get(i)
            world[i] = (world_of(p) if p is not None else np.identity(4)) @ local_matrix(nodes[i])
        return world[i]

    # ---- textures (embedded, shared between materials)
    images = j.get("images", [])
    video_ids, tex_ids = {}, {}

    def texture_for(img_index, role):
        key = (img_index, role)
        if key in tex_ids:
            return tex_ids[key]
        im = images[img_index]
        name = im.get("name") or f"texture_{img_index}"
        fname = name + ".png"
        if img_index not in video_ids:
            raw = view(im["bufferView"])[0] if "bufferView" in im else b""
            vid = nid()
            video_ids[img_index] = vid
            v = objects.add("Video", vid, name_class(name, "Video"), "Clip")
            v.add("Type", "Clip")
            v.children.append(props70(P("Path", "KString", "XRefUrl", "", fname)))
            v.add("UseMipMap", I32(0))
            v.add("Filename", fname)
            v.add("RelativeFilename", fname)
            if raw:
                v.add("Content", bytes(raw))
            count("Video")
        tid = nid()
        t = objects.add("Texture", tid, name_class(name, "Texture"), "")
        t.add("Type", "TextureVideoClip")
        t.add("Version", I32(202))
        t.add("TextureName", name_class(name, "Texture"))
        t.children.append(props70(P("CurrentTextureBlendMode", "enum", "", "", I32(0)),
                                  P("UVSet", "KString", "", "", "UVMap"),
                                  P("UseMaterial", "bool", "", "", I32(1))))
        t.add("Media", name_class(name, "Video"))
        t.add("FileName", fname)
        t.add("RelativeFilename", fname)
        t.add("ModelUVTranslation", 0.0, 0.0)
        t.add("ModelUVScaling", 1.0, 1.0)
        t.add("Texture_Alpha_Source", "None")
        t.add("Cropping", I32(0), I32(0), I32(0), I32(0))
        connect(video_ids[img_index], tid)
        count("Texture")
        tex_ids[key] = tid
        return tid

    textures = j.get("textures", [])

    def img_of(ref):
        if ref and ref.get("index", -1) < len(textures):
            return textures[ref["index"]].get("source")
        return None

    mat_ids = {}

    def material(mi):
        if mi in mat_ids:
            return mat_ids[mi]
        md = j["materials"][mi] if mi is not None and mi < len(j.get("materials", [])) else {"name": "Default"}
        pbr = md.get("pbrMetallicRoughness", {})
        base = pbr.get("baseColorFactor", [0.8, 0.8, 0.8, 1.0])
        mid = nid()
        m = objects.add("Material", mid, name_class(md.get("name", f"Material_{mi}"), "Material"), "")
        m.add("Version", I32(102))
        m.add("ShadingModel", "phong")
        m.add("MultiLayer", I32(0))
        rough = pbr.get("roughnessFactor", 1.0)
        m.children.append(props70(
            P("DiffuseColor", "Color", "", "A", float(base[0]), float(base[1]), float(base[2])),
            P("DiffuseFactor", "Number", "", "A", 1.0),
            P("TransparencyFactor", "Number", "", "A", float(1.0 - base[3])),
            P("Opacity", "double", "Number", "", float(base[3])),
            P("SpecularColor", "Color", "", "A", 1.0, 1.0, 1.0),
            P("SpecularFactor", "Number", "", "A", 0.25),
            P("ShininessExponent", "Number", "", "A", float(max(2.0, (1.0 - rough) * 100.0))),
            P("ReflectionFactor", "Number", "", "A", float(pbr.get("metallicFactor", 0.0)))))
        count("Material")
        b = img_of(pbr.get("baseColorTexture"))
        if b is not None:
            connect(texture_for(b, "base"), mid, "DiffuseColor")
            if md.get("alphaMode") in ("MASK", "BLEND"):
                connect(texture_for(b, "alpha"), mid, "TransparentColor")
        n = img_of(md.get("normalTexture"))
        if n is not None:
            connect(texture_for(n, "normal"), mid, "NormalMap")
        mr = img_of(pbr.get("metallicRoughnessTexture"))
        if mr is not None:  # glTF ORM map (R occlusion, G roughness, B metal) — kept with the file for reference
            connect(texture_for(mr, "orm"), mid, "ReflectionFactor")
        mat_ids[mi] = mid
        return mid

    # ---- skeleton
    skins = j.get("skins", [])
    bone_ids, armature_id = {}, None
    if skins:
        joints = skins[0]["joints"]
        jset = set(joints)
        armature_id = nid()
        root_bones = [jn for jn in joints if parent_of.get(jn) not in jset]
        arm_node = parent_of.get(root_bones[0]) if root_bones else None
        arm_name = nodes[arm_node].get("name", "Armature") if arm_node is not None else "Armature"
        a_attr = nid()
        na = objects.add("NodeAttribute", a_attr, name_class(arm_name, "NodeAttribute"), "Null")
        na.add("TypeFlags", "Null")
        am = objects.add("Model", armature_id, name_class(arm_name, "Model"), "Null")
        am.add("Version", I32(232))
        aw = to_cm(world_of(arm_node)) if arm_node is not None else np.identity(4)
        am.children.append(props70(P("Lcl Translation", "Lcl Translation", "", "A", *map(float, aw[:3, 3])),
                                   P("Lcl Rotation", "Lcl Rotation", "", "A", *euler_xyz(aw[:3, :3])),
                                   P("InheritType", "enum", "", "", I32(1))))
        am.add("Shading", True)
        am.add("Culling", "CullingOff")
        connect(a_attr, armature_id)
        connect(armature_id, I64(0))
        count("NodeAttribute")
        count("Model")
        for jn in joints:
            nd = nodes[jn]
            bid = nid()
            bone_ids[jn] = bid
            name = nd.get("name", f"bone_{jn}")
            attr = nid()
            at = objects.add("NodeAttribute", attr, name_class(name, "NodeAttribute"), "LimbNode")
            at.children.append(props70(P("Size", "double", "Number", "", 1.0)))
            at.add("TypeFlags", "Skeleton")
            loc = local_matrix(nd)
            sc = np.linalg.norm(loc[:3, :3], axis=0)
            rot = loc[:3, :3] / np.where(sc == 0, 1, sc)[None, :]
            bm = objects.add("Model", bid, name_class(name, "Model"), "LimbNode")
            bm.add("Version", I32(232))
            bm.children.append(props70(
                P("Lcl Translation", "Lcl Translation", "", "A", *(float(v) * CM for v in loc[:3, 3])),
                P("Lcl Rotation", "Lcl Rotation", "", "A", *euler_xyz(rot)),
                P("Lcl Scaling", "Lcl Scaling", "", "A", *map(float, sc)),
                P("InheritType", "enum", "", "", I32(1)),
                P("DefaultAttributeIndex", "int", "Integer", "", I32(0))))
            bm.add("Shading", True)
            bm.add("Culling", "CullingOff")
            connect(attr, bid)
            count("NodeAttribute")
            count("Model")
        for jn in joints:
            p = parent_of.get(jn)
            connect(bone_ids[jn], bone_ids[p] if p in bone_ids else armature_id)

    # ---- meshes
    pose_nodes = []
    for ni, nd in enumerate(nodes):
        if nd.get("mesh") is None:
            continue
        mesh = j["meshes"][nd["mesh"]]
        skinned = nd.get("skin") is not None and skins
        mw = np.identity(4) if skinned else world_of(ni)
        pos_all, nrm_all, uv_all, uv1_all, poly_mat, idx_all, jw = [], [], [], [], [], [], []
        mats = []
        base = 0
        for pr in mesh.get("primitives", []):
            if pr.get("mode", 4) != 4:
                continue
            at = pr["attributes"]
            pos = acc(at["POSITION"]).astype(np.float64)
            n = len(pos)
            nrm = acc(at["NORMAL"]).astype(np.float64) if "NORMAL" in at else np.zeros((n, 3))
            uv = acc(at["TEXCOORD_0"]).astype(np.float64) if "TEXCOORD_0" in at else np.zeros((n, 2))
            idx = acc(pr["indices"]).ravel().astype(np.int64) if "indices" in pr else np.arange(n)
            mi = pr.get("material")
            if mi not in mats:
                mats.append(mi)
            pos_all.append(pos)
            nrm_all.append(nrm)
            uv_all.append(uv)
            uv1_all.append(acc(at["TEXCOORD_1"]).astype(np.float64) if "TEXCOORD_1" in at else None)
            idx_all.append(idx + base)
            poly_mat.append(np.full(len(idx) // 3, mats.index(mi), np.int32))
            if skinned and "JOINTS_0" in at:
                jw.append((acc(at["JOINTS_0"]).astype(np.int64), acc(at["WEIGHTS_0"]).astype(np.float64), base))
            base += n
        if not pos_all:
            continue
        pos = np.concatenate(pos_all)
        nrm = np.concatenate(nrm_all)
        uv = np.concatenate(uv_all)
        # second UV set (where decals, emblems and numbers go): kept when any part has one
        uv1 = None
        if any(u is not None for u in uv1_all):
            uv1 = np.concatenate([u if u is not None else np.zeros((len(a), 2)) for u, a in zip(uv1_all, uv_all)])
        idx = np.concatenate(idx_all)
        if not skinned:
            pos = pos @ mw[:3, :3].T + mw[:3, 3]
            nrm = nrm @ np.linalg.inv(mw[:3, :3])
        pvi = idx.astype(np.int32).copy()
        pvi[2::3] = -pvi[2::3] - 1  # last corner of each triangle is stored as -(index + 1)
        gid, mid_ = nid(), nid()
        name = nd.get("name") or mesh.get("name") or f"Mesh_{ni}"
        g = objects.add("Geometry", gid, name_class(name, "Geometry"), "Mesh")
        g.children.append(props70())
        g.add("GeometryVersion", I32(124))
        g.add("Vertices", (pos * CM).reshape(-1))
        g.add("PolygonVertexIndex", pvi)
        le = g.add("LayerElementNormal", I32(0))
        le.add("Version", I32(101))
        le.add("Name", "")
        le.add("MappingInformationType", "ByPolygonVertex")
        le.add("ReferenceInformationType", "Direct")
        le.add("Normals", np.ascontiguousarray(nrm[idx]).reshape(-1))
        lu = g.add("LayerElementUV", I32(0))
        lu.add("Version", I32(101))
        lu.add("Name", "UVMap")
        lu.add("MappingInformationType", "ByPolygonVertex")
        lu.add("ReferenceInformationType", "IndexToDirect")
        uvf = uv.copy()
        uvf[:, 1] = 1.0 - uvf[:, 1]  # glTF UVs run top-down, FBX bottom-up
        lu.add("UV", uvf.reshape(-1))
        lu.add("UVIndex", idx.astype(np.int32))
        if uv1 is not None:
            lu1 = g.add("LayerElementUV", I32(1))
            lu1.add("Version", I32(101))
            lu1.add("Name", "DecalUV")
            lu1.add("MappingInformationType", "ByPolygonVertex")
            lu1.add("ReferenceInformationType", "IndexToDirect")
            uvf1 = uv1.copy()
            uvf1[:, 1] = 1.0 - uvf1[:, 1]
            lu1.add("UV", uvf1.reshape(-1))
            lu1.add("UVIndex", idx.astype(np.int32))
        lm = g.add("LayerElementMaterial", I32(0))
        lm.add("Version", I32(101))
        lm.add("Name", "")
        lm.add("MappingInformationType", "ByPolygon" if len(mats) > 1 else "AllSame")
        lm.add("ReferenceInformationType", "IndexToDirect")
        lm.add("Materials", np.concatenate(poly_mat) if len(mats) > 1 else np.zeros(1, np.int32))
        layer = g.add("Layer", I32(0))
        layer.add("Version", I32(100))
        for t in ("LayerElementNormal", "LayerElementMaterial", "LayerElementUV"):
            e = layer.add("LayerElement")
            e.add("Type", t)
            e.add("TypedIndex", I32(0))
        if uv1 is not None:  # a second layer holds the second UV set
            layer1 = g.add("Layer", I32(1))
            layer1.add("Version", I32(100))
            e = layer1.add("LayerElement")
            e.add("Type", "LayerElementUV")
            e.add("TypedIndex", I32(1))
        count("Geometry")
        mm = objects.add("Model", mid_, name_class(name, "Model"), "Mesh")
        mm.add("Version", I32(232))
        mm.children.append(props70(P("InheritType", "enum", "", "", I32(1)),
                                   P("DefaultAttributeIndex", "int", "Integer", "", I32(0))))
        mm.add("Shading", True)
        mm.add("Culling", "CullingOff")
        count("Model")
        connect(mid_, I64(0))
        connect(gid, mid_)
        for mi in mats:  # material order on the model = slot index
            connect(material(mi), mid_)
        pose_nodes.append((mid_, np.identity(4)))
        if skinned and jw:
            joints = skins[0]["joints"]
            ibm = (acc(skins[0]["inverseBindMatrices"]).astype(np.float64).reshape(-1, 4, 4).transpose(0, 2, 1)
                   if "inverseBindMatrices" in skins[0] else np.tile(np.identity(4), (len(joints), 1, 1)))
            skin_id = nid()
            sk = objects.add("Deformer", skin_id, name_class(name, "Deformer"), "Skin")
            sk.add("Version", I32(101))
            sk.add("Link_DeformAcuracy", 50.0)
            connect(skin_id, gid)
            count("Deformer")
            per_joint = {}
            for jj, ww, off in jw:
                for k in range(jj.shape[1]):
                    sel = ww[:, k] > 0
                    for jidx in np.unique(jj[sel, k]):
                        m = sel & (jj[:, k] == jidx)
                        per_joint.setdefault(int(jidx), ([], []))
                        per_joint[int(jidx)][0].append(np.nonzero(m)[0] + off)
                        per_joint[int(jidx)][1].append(ww[m, k])
            for k, jn in enumerate(joints):
                bind = to_cm(np.linalg.inv(ibm[k]))  # bone's world matrix at bind time
                pose_nodes.append((bone_ids[jn], bind))
                if k not in per_joint:
                    continue
                vi = np.concatenate(per_joint[k][0]).astype(np.int32)
                wv = np.concatenate(per_joint[k][1]).astype(np.float64)
                cid = nid()
                cl = objects.add("Deformer", cid, name_class(nodes[jn].get("name", f"bone_{jn}"), "SubDeformer"),
                                 "Cluster")
                cl.add("Version", I32(100))
                cl.add("UserData", "", "")
                cl.add("Indexes", vi)
                cl.add("Weights", wv)
                cl.add("Transform", fbx_matrix(np.linalg.inv(bind)))   # mesh relative to the bone (mesh at origin)
                cl.add("TransformLink", fbx_matrix(bind))
                connect(cid, skin_id)
                connect(bone_ids[jn], cid)
                count("Deformer")
    if bone_ids and armature_id is not None:
        aw = world_of(parent_of[skins[0]["joints"][0]]) if parent_of.get(skins[0]["joints"][0]) is not None \
            else np.identity(4)
        pose_nodes.append((armature_id, to_cm(aw)))
    if pose_nodes and bone_ids:
        pid = nid()
        pose = objects.add("Pose", pid, name_class("BindPose", "Pose"), "BindPose")
        pose.add("Type", "BindPose")
        pose.add("Version", I32(100))
        pose.add("NbPoseNodes", I32(len(pose_nodes)))
        for node_id, mtx in pose_nodes:
            pn = pose.add("PoseNode")
            pn.add("Node", node_id)
            pn.add("Matrix", fbx_matrix(mtx))
        count("Pose")

    # ---- header, settings, definitions
    t = time.localtime()
    header = Node("FBXHeaderExtension", children=[
        Node("FBXHeaderVersion", I32(1003)), Node("FBXVersion", I32(FBX_VERSION)), Node("EncryptionType", I32(0)),
        Node("CreationTimeStamp", children=[Node("Version", I32(1000)), Node("Year", I32(t.tm_year)),
                                            Node("Month", I32(t.tm_mon)), Node("Day", I32(t.tm_mday)),
                                            Node("Hour", I32(t.tm_hour)), Node("Minute", I32(t.tm_min)),
                                            Node("Second", I32(t.tm_sec)), Node("Millisecond", I32(0))]),
        Node("Creator", "AC8 Asset Extractor")])
    settings = Node("GlobalSettings", children=[Node("Version", I32(1000)), props70(
        P("UpAxis", "int", "Integer", "", I32(1)), P("UpAxisSign", "int", "Integer", "", I32(1)),
        P("FrontAxis", "int", "Integer", "", I32(2)), P("FrontAxisSign", "int", "Integer", "", I32(1)),
        P("CoordAxis", "int", "Integer", "", I32(0)), P("CoordAxisSign", "int", "Integer", "", I32(1)),
        P("OriginalUpAxis", "int", "Integer", "", I32(1)), P("OriginalUpAxisSign", "int", "Integer", "", I32(1)),
        P("UnitScaleFactor", "double", "Number", "", 1.0), P("OriginalUnitScaleFactor", "double", "Number", "", 1.0),
        P("AmbientColor", "ColorRGB", "Color", "", 0.0, 0.0, 0.0),
        P("DefaultCamera", "KString", "", "", "Producer Perspective"),
        P("TimeMode", "enum", "", "", I32(11)),
        P("TimeSpanStart", "KTime", "Time", "", I64(0)), P("TimeSpanStop", "KTime", "Time", "", I64(46186158000)),
        P("CustomFrameRate", "double", "Number", "", 24.0))])
    doc_id = nid()
    documents = Node("Documents", children=[Node("Count", I32(1)), Node(
        "Document", doc_id, "Scene", "Scene", children=[
            props70(P("SourceObject", "object", "", ""), P("ActiveAnimStackName", "KString", "", "", "")),
            Node("RootNode", I64(0))])])
    defs = Node("Definitions", children=[Node("Version", I32(100)), Node("Count", I32(sum(counts.values()) + 1)),
                                         Node("ObjectType", "GlobalSettings", children=[Node("Count", I32(1))])])
    for kind in ("Model", "NodeAttribute", "Geometry", "Material", "Texture", "Video", "Deformer", "Pose"):
        if counts.get(kind):
            defs.add("ObjectType", kind, children=[Node("Count", I32(counts[kind]))])
    Path(fbx_path).parent.mkdir(parents=True, exist_ok=True)
    write_fbx(fbx_path, [header, Node("FileId", FILE_ID), Node("CreationTime", TIME_ID),
                         Node("Creator", "AC8 Asset Extractor"), settings, documents, Node("References"), defs,
                         objects, conns, Node("Takes", children=[Node("Current", "")])])
    return {"materials": len(mat_ids), "textures": len(video_ids), "bones": len(bone_ids)}


def main():
    args = sys.argv[1:]
    if len(args) != 2:
        sys.exit(__doc__)
    src, dst = Path(args[0]), Path(args[1])
    pairs = ([(f, dst / f.relative_to(src).with_suffix(".fbx")) for f in sorted(src.rglob("*.glb"))]
             if src.is_dir() else [(src, dst if dst.suffix.lower() == ".fbx" else dst / (src.stem + ".fbx"))])
    ok = 0
    for i, (g, f) in enumerate(pairs, 1):
        t0 = time.time()
        try:
            info = convert(g, f)
            ok += 1
            print(f"FBX {i}/{len(pairs)}: {f.name} ({info['materials']} materials, {info['textures']} textures, "
                  f"{info['bones']} bones, {time.time() - t0:.1f}s)", flush=True)
        except Exception as e:  # keep going
            print(f"FBX {i}/{len(pairs)}: ERROR {g.name}: {e}", flush=True)
    print(f"FBX done: {ok} of {len(pairs)} written", flush=True)
    return 0 if ok == len(pairs) else 1


if __name__ == "__main__":
    sys.exit(main())
