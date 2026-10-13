"""Minimal reader for cooked UE5.4 packages as written by retoc 'to-legacy' (unversioned, FilterEditorOnly).

Gives the name map, imports, exports (with where their data sits in the .uexp) and the data-resource table
(where bulk payloads sit: inline in .uexp, or in .ubulk/.uptnl).
"""
import struct
from pathlib import Path

TAG = 0x9E2A83C1

# bulk data flags (EBulkDataFlags)
BULK_INLINE = 0x40            # ForceInlinePayload: bytes sit right after the reference in .uexp
BULK_SEPARATE_FILE = 0x100    # PayloadInSeperateFile: bytes in .ubulk (or .uptnl if optional)
BULK_OPTIONAL = 0x800         # OptionalPayload: .uptnl


class Reader:
    __slots__ = ("b", "p")

    def __init__(self, data, pos=0):
        self.b, self.p = data, pos

    def i32(self):
        v = struct.unpack_from("<i", self.b, self.p)[0]
        self.p += 4
        return v

    def u32(self):
        v = struct.unpack_from("<I", self.b, self.p)[0]
        self.p += 4
        return v

    def i64(self):
        v = struct.unpack_from("<q", self.b, self.p)[0]
        self.p += 8
        return v

    def u8(self):
        v = self.b[self.p]
        self.p += 1
        return v

    def take(self, n):
        v = self.b[self.p:self.p + n]
        if len(v) != n:
            raise EOFError(f"wanted {n} bytes at {self.p}, file has {len(self.b)}")
        self.p += n
        return v

    def skip(self, n):
        self.p += n

    def fstring(self):
        n = self.i32()
        if n == 0:
            return ""
        if n > 0:
            if n > 4096:
                raise ValueError(f"bad string length {n} at {self.p - 4}")
            s = self.take(n)[:-1].decode("latin-1")
        else:
            if -n > 4096:
                raise ValueError(f"bad string length {n} at {self.p - 4}")
            s = self.take(-n * 2)[:-2].decode("utf-16-le")
        return s


class Package:
    def __init__(self, uasset_path):
        self.path = Path(uasset_path)
        self.header = self.path.read_bytes()
        r = Reader(self.header)
        if r.u32() != TAG:
            raise ValueError("not a .uasset")
        legacy = r.i32()
        if legacy != -4:
            r.i32()  # legacy UE3 version
        r.i32()  # UE4 version (0 = unversioned)
        if legacy <= -8:
            r.i32()  # UE5 version
        r.i32()  # licensee version
        if legacy <= -9:
            r.skip(20)  # saved hash
            self.total_header = r.i32()
        ncustom = r.i32()
        r.skip(ncustom * 20)
        if legacy > -9:
            self.total_header = r.i32()
        self.package_name = r.fstring()
        self.flags = r.u32()
        name_count, name_off = r.i32(), r.i32()
        r.i32(), r.i32()  # soft object paths
        r.i32(), r.i32()  # gatherable text
        export_count, export_off = r.i32(), r.i32()
        import_count, import_off = r.i32(), r.i32()
        depends_off = r.i32()
        # export entry size differs between engine builds: work it out from where the next table starts
        self.export_stride = (depends_off - export_off) // export_count if export_count else 0
        # names
        self.names = []
        n = Reader(self.header, name_off)
        for _ in range(name_count):
            self.names.append(n.fstring())
            n.skip(4)  # hashes
        # imports: class package, class name, outer, object name, optional
        self.imports = []
        n.p = import_off
        for _ in range(import_count):
            cpkg = self.name(n)
            cname = self.name(n)
            outer = n.i32()
            oname = self.name(n)
            n.skip(4)
            self.imports.append({"class_package": cpkg, "class": cname, "outer": outer, "name": oname})
        # exports
        self.exports = []
        n.p = export_off
        for _ in range(export_count):
            cls, _super, _template, outer = n.i32(), n.i32(), n.i32(), n.i32()
            oname = self.name(n)
            oflags = n.u32()
            size, offset = n.i64(), n.i64()
            n.skip(self.export_stride - 44)
            self.exports.append({"class_index": cls, "outer": outer, "name": oname, "size": size,
                                 "offset": offset, "flags": oflags})
        self._uexp = None
        self._resources = None

    def name(self, r):
        idx, num = r.i32(), r.i32()
        s = self.names[idx] if 0 <= idx < len(self.names) else f"?{idx}"
        return s if num == 0 else f"{s}_{num - 1}"

    def index_name(self, idx):
        """FPackageIndex -> object name (negative = import, positive = export)."""
        if idx < 0 and -idx - 1 < len(self.imports):
            return self.imports[-idx - 1]["name"]
        if idx > 0 and idx - 1 < len(self.exports):
            return self.exports[idx - 1]["name"]
        return None

    def import_path(self, idx):
        """Full path of an import, e.g. /Game/Vehicles/.../MI_Foo.MI_Foo -> package path + object name."""
        if idx >= 0 or -idx - 1 >= len(self.imports):
            return None
        imp = self.imports[-idx - 1]
        parts = [imp["name"]]
        outer = imp["outer"]
        while outer < 0 and -outer - 1 < len(self.imports):
            o = self.imports[-outer - 1]
            parts.append(o["name"])
            outer = o["outer"]
        return list(reversed(parts))

    def export_class(self, e):
        return self.index_name(e["class_index"]) or "?"

    @property
    def uexp(self):
        if self._uexp is None:
            self._uexp = self.path.with_suffix(".uexp").read_bytes()
        return self._uexp

    def export_data(self, e):
        start = e["offset"] - self.total_header
        return self.uexp[start:start + e["size"]], start

    def main_export(self, classes):
        for e in self.exports:
            if self.export_class(e) in classes:
                return e
        return None

    # --- data resources (bulk payloads)
    @property
    def resources(self):
        if self._resources is None:
            self._resources = read_data_resources(self.header)
        return self._resources

    def bulk(self, index, r=None):
        """Bytes of data resource `index`. For inline payloads `r` must sit right after the index (it is advanced)."""
        res = self.resources[index]
        if res["flags"] & BULK_INLINE:
            if r is None:
                raise ValueError("inline payload needs the reader position")
            return r.take(res["size"])
        if res["size"] <= 0:
            return b""
        if res["flags"] & BULK_SEPARATE_FILE:
            f = self.path.with_suffix(".uptnl" if res["flags"] & BULK_OPTIONAL else ".ubulk")
            with open(f, "rb") as fh:
                fh.seek(res["offset"])
                return fh.read(res["size"])
        start = res["offset"] - self.total_header if res["offset"] >= self.total_header else res["offset"]
        return self.uexp[start:start + res["size"]]


def read_data_resources(uasset: bytes):
    size = len(uasset)
    for entry in (44, 48):
        for count in range(0, 4096):
            start = size - 8 - count * entry
            if start < 0:
                break
            version, n = struct.unpack_from("<Ii", uasset, start)
            if n != count or not 0 < version < 16 or (entry == 48) != (version >= 2):
                continue
            out = []
            for i in range(count):
                at = start + 8 + i * entry + (8 if entry == 48 else 4)
                offset, _dup, ssize, rsize, _outer, legacy = struct.unpack_from("<qqqqiI", uasset, at)
                out.append({"offset": offset, "size": ssize, "raw": rsize, "flags": legacy})
            return out
    return []
