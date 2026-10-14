"""Ace Combat 8 Asset Extractor - browse the game's assets and extract them in one click.

    1. Scan the game once (or import an existing listing) -> asset browser by category / group / type, and the
       Catalog (aircraft, characters, bosses … by name)
    2. Select folders or single assets, press Extract
         Step 1  retoc (AC8 Unpacker tools)   game .utoc/.ucas -> raw .uasset/.uexp  (1_Raw_UAssets)
         Step 2  ac8_textures.py               raw textures     -> .png               (2_Final_Export/Textures)
                 ac8_gltf.py / ac8_fbx.py      raw meshes       -> .glb / .fbx        (2_Final_Export/Meshes, FBX)
    3. Look at the meshes in the 3D viewer (ac8_glview.py), with liveries for aircraft

Every extraction gets its own folder, shown as a card:

    AC8_Exports/
        PP0023_f14d_2026-10-11_0650/
            1_Raw_UAssets/      what came out of the game paks
            2_Final_Export/
                Textures/       .png, in the same folders as in the game
                Meshes/ FBX/    .glb / .fbx
            extraction_info.txt what was selected, date, counts
            extraction_log.txt  full log

Start it with "AC8 Asset Extractor.exe" (built from launcher/), or:  python AC8_Asset_Extractor.py
"""
import datetime as _dt
import importlib.util
import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import tkinter.font as tkfont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # sibling ac8_* modules
import ac8_catalog as catalog  # noqa: E402  (pure python, no extra packages)

APP_NAME = "Ace Combat 8 Asset Extractor"
APP_VERSION = "1.3.7"
KOFI_URL = "https://ko-fi.com/akiraseki"
CODE = Path(__file__).resolve().parent         # app/: the scripts and their fonts, icons and web viewer
HERE = CODE.parent                             # the app folder (the exe, app/, AC8 Unpacker/, data/, logs/)
DATA_DIR = HERE / "data"                       # your own files: settings, asset list, update history
SETTINGS_FILE = DATA_DIR / "ac8_extractor_settings.json"
INDEX_FILE = DATA_DIR / "asset_index.txt"
HISTORY_FILE = DATA_DIR / "asset_history.json"   # files that appeared in each game update (found by Scan Game)
DEPS_FILE = DATA_DIR / "mesh_textures.json"      # the materials / textures each model selected in the catalog uses
ARROW = "arrow" if os.name == "nt" else ""  # the standard pointer (X11's own "arrow" is a different shape)
FONTS_DIR = CODE / "fonts"
ASSETS_DIR = CODE / "assets"

RAW_DIR_NAME = "1_Raw_UAssets"
FINAL_DIR_NAME = "2_Final_Export"
INFO_NAME = "extraction_info.txt"
LOG_NAME = "extraction_log.txt"
LOGS_DIR = HERE / "logs"         # one file per app session: everything the log panel shows, plus error tracebacks
LOGS_KEEP = 40
DEFAULT_OUT_ROOT = HERE / "AC8_Exports"
ENGINE = "UE5_4"
CONTENT = "Live/Content/"

# offline start (no anti-cheat) — needed once, so the key finder can read the running game
OFFLINE_JSON = "AC8_Offline.json"
OFFLINE_ARG = f"-anticheat_settings={OFFLINE_JSON}"
STEAM_LAUNCH_OPTION = f'cmd /c "set EOS_USE_ANTICHEATCLIENTNULL=1&& %command% {OFFLINE_ARG}"'
KEY_RE = re.compile(r"^0x[0-9A-Fa-f]{64}$")
IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WIN else 0
MAX_CMD_FILTERS_CHARS = 24000  # Windows command line limit is ~32k

PACKAGES = {"numpy": "numpy", "PIL": "Pillow", "Crypto": "pycryptodome",
            "OpenGL": "PyOpenGL", "pyopengltk": "pyopengltk"}  # needed (the last two: the 3D viewer)
OPTIONAL_PACKAGES = {}

# Asset types for browsing, from the standard UE naming prefixes (SM_, T_, MI_ ...).
TYPES = [
    ("all", "All types"), ("SM", "Static meshes"), ("SK", "Skeletal meshes"), ("T", "Textures"),
    ("MAT", "Materials"), ("ANIM", "Animations"), ("BP", "Blueprints / UI"), ("MAP", "Levels (.umap)"),
    ("VFX", "VFX"), ("AUD", "Audio events"), ("DATA", "Data tables"), ("OTHER", "Other"),
]
TYPE_LABEL = dict(TYPES)
TYPE_SHORT = {"SM": "MESH", "SK": "SKEL MESH", "T": "TEXTURE", "MAT": "MATERIAL", "ANIM": "ANIM", "BP": "BLUEPRINT",
              "MAP": "LEVEL", "VFX": "VFX", "AUD": "AUDIO", "DATA": "DATA", "OTHER": ""}
_PREFIX = {
    "SM": "SM", "SK": "SK", "SKM": "SK", "T": "T", "TX": "T", "TC": "T",
    "M": "MAT", "MI": "MAT", "MF": "MAT", "MPC": "MAT", "MM": "MAT",
    "A": "ANIM", "AO": "ANIM", "AM": "ANIM", "ABP": "ANIM", "BS": "ANIM", "ABS": "ANIM",
    "BP": "BP", "WBP": "BP", "WB": "BP",
    "NS": "VFX", "NE": "VFX", "NP": "VFX", "FX": "VFX", "P": "VFX",
    "AKE": "AUD", "WW": "AUD", "AK": "AUD",
    "DT": "DATA", "DA": "DATA", "CUR": "DATA", "CT": "DATA", "ST": "DATA",
}


def asset_type(name):
    if name.lower().endswith(".umap"):
        return "MAP"
    return _PREFIX.get(name.split("_", 1)[0].upper(), "OTHER")


# ------------------------------------------------------------------ theme
C = {
    "bg0": "#0c0c0e", "bg1": "#121215", "bg2": "#18181c", "bg3": "#202026",
    "border1": "#1f1f25", "border2": "#2a2a32", "border3": "#3a3a44",
    "text1": "#e8e8ed", "text2": "#a3a3ad", "text3": "#6c6c78",
    "accent": "#e63c2d", "accent_cc": "#ba3227", "accent_hover": "#f04f40",
    "accent_tint": "#2a1412", "accent_line": "#5a2019", "row_sel": "#24171a",
    "ok": "#3ecf6e", "ok_tint": "#10251a", "ok_line": "#1f5a35", "warn": "#e8a33d",
}
F = {}
SCALE = 1.0


def load_private_fonts():
    if not (IS_WIN and FONTS_DIR.is_dir()):
        return
    try:
        import ctypes
        for ttf in FONTS_DIR.glob("*.ttf"):
            ctypes.windll.gdi32.AddFontResourceExW(str(ttf), 0x10, 0)  # FR_PRIVATE
    except Exception:
        pass


def setup_fonts(root):
    global SCALE
    SCALE = max(1.0, root.winfo_fpixels("1i") / 96.0)
    fams = set(tkfont.families(root))
    ui = next((f for f in ("Outfit", "Segoe UI", "Helvetica Neue", "Arial") if f in fams), "TkDefaultFont")
    mono = next((f for f in ("Cascadia Mono", "JetBrains Mono", "Consolas", "DejaVu Sans Mono", "Courier New")
                 if f in fams), "TkFixedFont")
    F.update({"title": (ui, 14, "bold"), "h": (ui, 11, "bold"), "base": (ui, 10), "bold": (ui, 10, "bold"),
              "small": (ui, 9), "tiny": (ui, 8, "bold"), "section": (ui, 9, "bold"), "input": (ui, 11),
              "mono": (mono, 9), "mono_s": (mono, 8)})


# ------------------------------------------------------------------ locating things

def _find_folder(marker, preferred):
    p = HERE / preferred
    if (p / marker).exists():
        return p
    for child in sorted(HERE.iterdir()):
        if child.is_dir() and (child / marker).exists():
            return child
    return None


UNPACKER_DIR = _find_folder("ac8_unpack.py", "AC8 Unpacker")
TEXTURE_CONVERTER = CODE / "ac8_textures.py"  # cooked texture -> PNG (incl. virtual/UDIM textures)
MESH_CONVERTER = CODE / "ac8_gltf.py"         # cooked static/skeletal mesh -> GLB with skeleton + textures
FBX_CONVERTER = CODE / "ac8_fbx.py"           # GLB -> FBX, built in (no Blender needed)
LIVERY_SCRIPT = CODE / "ac8_livery.py"         # aircraft liveries -> 2_Final_Export/Liveries/<aircraft>.json
PBR_SCRIPT = CODE / "ac8_pbr.py"               # packed textures -> PBR maps + AO
FONT_SCRIPT = CODE / "ac8_fonts.py"
DCS_SCRIPT = CODE / "ac8_dcs.py"               # PBR <-> DCS textures, DDS, the F/A-18 helmet converter            # FontFace assets -> the original .ttf / .otf files
PREVIEW_DIR_NAME = "_Previews"
MESH_FORMAT = "5"  # bump when the GLB output changes, so cached previews are rebuilt (5 = 2nd UV set for decals)
PBR_PRESETS = [("off", "Off"), ("separate", "Separate maps (Blender / Substance)"), ("unreal", "Unreal (ORM)"),
               ("unity_hdrp", "Unity HDRP (Mask Map)"), ("unity_urp", "Unity URP / Built-in"), ("gltf", "glTF (ORM)"),
               ("dcs", "DCS World (RoughMet)")]
NORMAL_CONV = [("auto", "Preset default"), ("opengl", "OpenGL (Blender, Unity, glTF)"),
               ("directx", "DirectX (Unreal, Substance DX)")]
TEX_ROLES = [("all", "All"), ("Colour", "Colour"), ("Normal", "Normal + AO"), ("Metal/Rough", "Metal / Rough + AO"),
             ("Emissive", "Emissive"), ("Emblem", "Emblems"), ("Other", "Other")]
MESH_TYPES = ("SM", "SK")


def load_history():
    try:
        h = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return h if isinstance(h.get("updates"), list) else {"updates": []}
    except (OSError, ValueError, AttributeError):
        return {"updates": []}


def save_history(h):
    try:
        HISTORY_FILE.write_text(json.dumps(h, indent=0), encoding="utf-8")
    except OSError:
        pass


def role_name(role):
    return {"base": "the colour map", "normal": "the normal map", "mr": "the metal/rough(/AO) map"}.get(role, role)


def texture_role_label(path):
    stem = Path(path).stem
    stem = re.sub(r"\.\d{4}$", "", stem)
    if "/Emblem/" in str(path).replace("\\", "/") or stem.lower().startswith("t_emblem"):
        return "Emblem"
    suf = stem.rsplit("_", 1)[-1].upper()
    if suf in ("C", "D", "BC", "CO", "COL", "COLOR", "ALBEDO", "BASECOLOR", "DIFFUSE"):
        return "Colour"
    if suf in ("N", "NO", "NM", "NRM", "NORMAL"):
        return "Normal"
    if suf in ("MRX", "MRO", "MROS", "MR", "MRA", "ORM"):
        return "Metal/Rough"
    if suf in ("CE", "E", "EM", "EMISSIVE"):
        return "Emissive"
    return "Other"
TEXTURE_SIZES = [("1024", "1024 px"), ("2048", "2048 px"), ("4096", "4096 px"), ("0", "Full size")]


# files from older versions that nothing uses any more — removed at start-up so the folder stays tidy
# the flat layout before v1.0 had every script, plus assets/ fonts/ viewer/, in the root folder
OLD_ROOT_FILES = ["AC8_Asset_Extractor.py", "ac8_catalog.py", "ac8_fbx.py", "ac8_gltf.py", "ac8_glview.py",
                  "ac8_livery.py", "ac8_mesh.py", "ac8_package.py", "ac8_pbr.py", "ac8_skins.py", "ac8_steam.py",
                  "ac8_textures.py", "ac8_tools.py", "ac8_update.py", "ac8_viewer.py", "ac8_vt.py", "assets", "fonts",
                  "viewer", "__pycache__"]
OBSOLETE = OLD_ROOT_FILES + ["ac8_shaders.py", "ac8_embed.py", "ac8_blender_fbx.py", "AC8 uasset extractor", "Start AC8 Asset Extractor.bat",
            "AC8 Unpacker/.gitignore", "AC8 Unpacker/requirements.txt", "AC8 Unpacker/out"]


def move_user_data():
    """Before v1.0 the settings, asset list and history sat in the root folder: move them into data/."""
    DATA_DIR.mkdir(exist_ok=True)
    for f in (SETTINGS_FILE, INDEX_FILE, HISTORY_FILE):
        old = HERE / f.name
        if old.exists() and not f.exists():
            try:
                old.replace(f)
            except OSError:
                pass


def remove_obsolete():
    removed = []
    # older downloads also carried the launcher's Go source; keep it only in a developer's git checkout
    extra = [] if (HERE / ".git").exists() else ["launcher"]
    for rel in OBSOLETE + extra:
        p = HERE / rel
        try:
            if p.is_dir():
                shutil.rmtree(p)
            elif p.exists():
                p.unlink()
            else:
                continue
            removed.append(rel)
        except OSError:
            pass
    for cache in HERE.glob("__pycache__/ac8_embed*.pyc"):
        try:
            cache.unlink()
        except OSError:
            pass
    return removed


def console_python():
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").exists():
        return str(exe.with_name("python.exe"))
    return str(exe)


def detect_game_dir():
    if not (IS_WIN and UNPACKER_DIR):
        return ""
    try:
        spec = importlib.util.spec_from_file_location("ac8_unpack", UNPACKER_DIR / "ac8_unpack.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        found = mod.find_game_dir()
        return str(found) if found else ""
    except Exception:
        return ""


def retoc_exe():
    if UNPACKER_DIR and (UNPACKER_DIR / "tools" / "retoc.exe").exists():
        return str(UNPACKER_DIR / "tools" / "retoc.exe")
    return shutil.which("retoc")


def safe_name(text):
    text = re.sub(r"[^A-Za-z0-9._+-]+", "_", text.strip()).strip("._")
    return text[:48] or "all"


def count_files(folder, *exts):
    folder = Path(folder)
    if not folder.is_dir():
        return 0
    exts = tuple(e.lower() for e in exts)
    return sum(1 for _r, _d, files in os.walk(folder) for f in files if f.lower().endswith(exts))


class SessionLog:
    """Logs/session_<date>_<time>.txt next to the exe. Thread safe; never raises."""

    def __init__(self):
        self.lock = threading.Lock()
        self.f = None
        self.path = None
        try:
            # older versions made "Logs": rename it (in two steps — Windows ignores a change of case alone)
            if any(p.name == "Logs" for p in HERE.iterdir()):
                tmp = HERE / "logs_rename_tmp"
                (HERE / "Logs").rename(tmp)
                tmp.rename(LOGS_DIR)
            LOGS_DIR.mkdir(exist_ok=True)
            old = sorted(LOGS_DIR.glob("session_*.txt"))
            for o in old[:max(0, len(old) - (LOGS_KEEP - 1))]:
                try:
                    o.unlink()
                except OSError:
                    pass
            self.path = LOGS_DIR / f"session_{_dt.datetime.now():%Y-%m-%d_%H-%M-%S}.txt"
            self.f = open(self.path, "a", encoding="utf-8", buffering=1)
            self.f.write(f"{APP_NAME} v{APP_VERSION}  ·  {sys.platform}  ·  Python {sys.version.split()[0]}\n")
        except Exception:
            self.f = None

    def write(self, text):
        if not self.f:
            return
        with self.lock:
            try:
                self.f.write(f"[{_dt.datetime.now():%H:%M:%S}] {text}\n")
            except Exception:
                pass

    def exc(self, where=""):
        """Call inside an except block: writes the full traceback."""
        import traceback
        self.write((f"{where}\n" if where else "") + traceback.format_exc().rstrip())


SLOG = SessionLog()


def monitor_area(widget):
    """(left, top, right, bottom) of the work area of the monitor the widget is on. Tk's screen size is only the
    primary monitor's, so on Windows this asks the system; elsewhere it falls back to Tk's screen."""
    if IS_WIN:
        try:
            import ctypes
            from ctypes import wintypes

            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                            ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
            pt = wintypes.POINT(widget.winfo_rootx() + widget.winfo_width() // 2,
                                widget.winfo_rooty() + widget.winfo_height() // 2)
            user32 = ctypes.windll.user32
            user32.MonitorFromPoint.restype = wintypes.HMONITOR
            user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
            mon = user32.MonitorFromPoint(pt, 2)  # MONITOR_DEFAULTTONEAREST
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            if mon and user32.GetMonitorInfoW(mon, ctypes.byref(info)):
                r = info.rcWork
                return r.left, r.top, r.right, r.bottom
        except Exception:
            pass
    return 0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight()


def open_folder(path):
    path = Path(path)
    if not path.exists():
        messagebox.showinfo(APP_NAME, f"This folder doesn't exist yet:\n{path}")
        return
    if IS_WIN:
        os.startfile(str(path))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def read_info(folder):
    info = {}
    for name in (INFO_NAME, "job_info.txt"):  # job_info.txt = folders made by v1.0/1.1
        try:
            for line in (folder / name).read_text(encoding="utf-8").splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    info[k.strip().lower()] = v.strip()
            break
        except OSError:
            pass
    return info


def fmt(n):
    return f"{n:,}"


# ------------------------------------------------------------------ asset index

class Node:
    __slots__ = ("name", "key", "parent", "dirs", "files", "types", "total")

    def __init__(self, name, key, parent):
        self.name, self.key, self.parent = name, key, parent
        self.dirs, self.files = {}, []  # files: list of (name, type)
        self.types, self.total = Counter(), 0

    def count(self, t="all"):
        return self.total if t == "all" else self.types.get(t, 0)


def display_key(full):
    """Live/Content/Vehicles/... -> Vehicles/... ; other mounts get a bracketed root."""
    if full.startswith(CONTENT):
        return full[len(CONTENT):]
    parts = full.split("/")
    return f"[{parts[0]} {parts[1]}]/" + "/".join(parts[2:])


def full_path(key):
    if key.startswith("["):
        root, _, rest = key[1:].partition("]")
        return root.replace(" ", "/", 1) + rest
    return CONTENT + key


class AssetIndex:
    def __init__(self, paths=(), source=""):
        self.source = source
        self.root = Node("", "", None)
        self.nodes = {"": self.root}
        self.files = []  # (key, lowercase key, type)
        for full in paths:
            self._add(full)
        self._sum(self.root)

    def _add(self, full):
        key = display_key(full)
        parts = key.split("/")
        node = self.root
        for i, part in enumerate(parts[:-1]):
            child = node.dirs.get(part)
            if child is None:
                ckey = "/".join(parts[:i + 1])
                child = node.dirs[part] = Node(part, ckey, node)
                self.nodes[ckey] = child
            node = child
        t = asset_type(parts[-1])
        node.files.append((parts[-1], t))
        self.files.append((key, key.lower(), t))

    def _sum(self, node):
        for _n, t in node.files:
            node.types[t] += 1
        node.total = len(node.files)
        for child in node.dirs.values():
            self._sum(child)
            node.types.update(child.types)
            node.total += child.total

    def __len__(self):
        return len(self.files)

    def find(self, key):
        """(node, filename) - filename is None for folders."""
        if key in self.nodes:
            return self.nodes[key], None
        parent, _, name = key.rpartition("/")
        return self.nodes.get(parent), name

    def assets_under(self, key, t="all"):
        node, fname = self.find(key)
        if node is None:
            return []
        if fname is not None:
            return [key] if t == "all" or asset_type(fname) == t else []
        out, stack = [], [node]
        while stack:
            n = stack.pop()
            out += [f"{n.key}/{f}" if n.key else f for f, ft in n.files if t == "all" or ft == t]
            stack += n.dirs.values()
        return out

    # --- loading / saving
    @staticmethod
    def parse_listing(text):
        out = set()
        for line in text.splitlines():
            line = line.strip().replace("\\", "/")
            if not line or line.startswith("#"):
                continue
            path = line.split()[-1]
            while path.startswith("../"):
                path = path[3:]
            if path.lower().endswith((".uasset", ".umap")) and "/" in path:
                out.add(path)
        return sorted(out)

    @staticmethod
    def read_text_any(path):
        raw = Path(path).read_bytes()
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            return raw.decode("utf-16", errors="replace")
        if raw[:3] == b"\xef\xbb\xbf":
            return raw[3:].decode("utf-8", errors="replace")
        if raw[1:2] == b"\0":
            return raw.decode("utf-16-le", errors="replace")
        return raw.decode("utf-8", errors="replace")

    def save(self, paths, path=INDEX_FILE, note=""):
        header = f"# {APP_NAME} asset index | {_dt.datetime.now():%Y-%m-%d %H:%M} | {note}\n"
        Path(path).write_text(header + "\n".join(paths) + "\n", encoding="utf-8")


def cover_keys(index, assets, t):
    """Smallest set of keys covering `assets`: a folder when every asset of type t in it is wanted."""
    want = set(assets)

    def rec(node):
        mine = [f"{node.key}/{f}" if node.key else f for f, ft in node.files if t == "all" or ft == t]
        covered = [a for a in mine if a in want]
        kids = [rec(c) for c in node.dirs.values() if c.count(t)]
        full = node.count(t) > 0 and len(covered) == len(mine) and all(k[0] for k in kids)
        if full and node.key:
            return True, [node.key]
        return full, covered + [key for k in kids for key in k[1]]

    return rec(index.root)[1]


def make_filters(index, assets, t):
    """retoc -f filters (substring match on the container path) for the wanted assets."""
    if t != "all":  # a folder filter would also pull the other types, so list the assets
        return [full_path(a) for a in assets]
    return [full_path(k) + ("/" if k in index.nodes else "") for k in cover_keys(index, assets, t)]


def batch_filters(filters, limit=MAX_CMD_FILTERS_CHARS):
    batch, size = [], 0
    for f in filters:
        if batch and size + len(f) + 6 > limit:
            yield batch
            batch, size = [], 0
        batch.append(f)
        size += len(f) + 6
    if batch:
        yield batch


# ------------------------------------------------------------------ widgets

class Button(tk.Frame):
    def __init__(self, parent, text, command=None, variant="default", icon="", font=None, padx=12, pady=6):
        self.variant, self.command, self.enabled, self.hover = variant, command, True, False
        super().__init__(parent, highlightthickness=1, bd=0, cursor="hand2")
        self.label = tk.Label(self, text=(f"{icon}  {text}" if icon else text), font=font or F["bold"],
                              padx=padx, pady=pady, bd=0)
        self.label.pack(fill="both", expand=True)
        for w in (self, self.label):
            w.bind("<Enter>", lambda e: self._set_hover(True))
            w.bind("<Leave>", lambda e: self._set_hover(False))
            w.bind("<Button-1>", self._click)
        self._paint()

    def _colors(self):
        v, h = self.variant, self.hover
        if not self.enabled:
            return C["bg1"], C["text3"], C["border1"]
        if v == "primary":
            col = C["accent_hover"] if h else C["accent"]
            return col, "#ffffff", col
        if v == "danger":
            return (C["accent_tint"] if h else C["bg2"]), C["accent"], C["accent_line"]
        return (C["bg3"] if h else C["bg2"]), C["text1"], (C["border3"] if h else C["border2"])

    def _paint(self):
        bg, fg, line = self._colors()
        self.configure(bg=bg, highlightbackground=line, highlightcolor=line,
                       cursor="hand2" if self.enabled else "arrow")
        self.label.configure(bg=bg, fg=fg)

    def _set_hover(self, on):
        self.hover = on
        self._paint()

    def _click(self, _e=None):
        if self.enabled and self.command:
            self.command()

    def set_enabled(self, on):
        self.enabled = on
        self._paint()

    def set_text(self, text):
        self.label.configure(text=text)


class Check(tk.Frame):
    def __init__(self, parent, variable, text="", bg=None, command=None):
        bg = bg or C["bg1"]
        super().__init__(parent, bg=bg, cursor="hand2")
        self.var, self.command = variable, command
        s = round(16 * SCALE)
        self.s = s
        self.box = tk.Canvas(self, width=s, height=s, bg=bg, highlightthickness=0, bd=0)
        self.box.pack(side="left")
        if text:
            lbl = tk.Label(self, text=text, bg=bg, fg=C["text2"], font=F["base"], padx=7)
            lbl.pack(side="left")
            lbl.bind("<Button-1>", self._toggle)
        self.box.bind("<Button-1>", self._toggle)
        self.bind("<Button-1>", self._toggle)
        watch(self, variable, self._draw)
        self._draw()

    def _toggle(self, _e=None):
        self.var.set(not self.var.get())
        if self.command:
            self.command()
        return "break"

    def _draw(self):
        c, s = self.box, self.s
        c.delete("all")
        if self.var.get():
            c.create_rectangle(1, 1, s - 1, s - 1, fill=C["accent"], outline=C["accent"])
            c.create_line(s * .25, s * .5, s * .44, s * .69, s * .75, s * .31, fill="#fff", width=max(2, s // 8),
                          capstyle="round", joinstyle="round")
        else:
            c.create_rectangle(1, 1, s - 1, s - 1, fill=C["bg2"], outline=C["border3"])


class Segmented(tk.Frame):
    def __init__(self, parent, variable, options, compact=False):
        super().__init__(parent, bg=C["bg2"], highlightthickness=1, highlightbackground=C["border2"])
        self.var, self.items = variable, {}
        for value, text in options:
            lbl = tk.Label(self, text=text, font=F["small"] if compact else F["bold"], padx=9 if compact else 12,
                           pady=4 if compact else 6, cursor="hand2", bd=0)
            lbl.pack(side="left", fill="y")
            lbl.bind("<Button-1>", lambda e, v=value: self.var.set(v))
            self.items[value] = lbl
        watch(self, variable, self._paint)
        self._paint()

    def _paint(self):
        for value, lbl in self.items.items():
            on = self.var.get() == value
            lbl.configure(bg=C["accent"] if on else C["bg2"], fg="#ffffff" if on else C["text3"])


class Input(tk.Frame):
    def __init__(self, parent, variable, placeholder="", icon="", font=None, mono=False):
        super().__init__(parent, bg=C["bg2"], highlightthickness=1, highlightbackground=C["border2"],
                         highlightcolor=C["accent"])
        self.var = variable
        if icon:
            tk.Label(self, text=icon, bg=C["bg2"], fg=C["text3"], font=F["input"], padx=9).pack(side="left")
        self.entry = tk.Entry(self, textvariable=variable, bg=C["bg2"], fg=C["text1"], bd=0, relief="flat",
                              insertbackground=C["accent"], selectbackground=C["accent_cc"],
                              selectforeground="#fff", font=F["mono"] if mono else (font or F["base"]),
                              highlightthickness=0)
        self.entry.pack(side="left", fill="both", expand=True, padx=(0 if icon else 10, 6), ipady=6)
        self.clear = tk.Label(self, text="✕", bg=C["bg2"], fg=C["text3"], font=F["small"], padx=8, cursor="hand2")
        self.clear.bind("<Button-1>", lambda e: (variable.set(""), self.entry.focus_set()))
        self.hint = tk.Label(self, text=placeholder, bg=C["bg2"], fg=C["text3"], font=font or F["base"], anchor="w")
        self.hint.bind("<Button-1>", lambda e: self.entry.focus_set())
        self.entry.bind("<FocusIn>", lambda e: (self.configure(highlightbackground=C["accent"]), self._hint()))
        self.entry.bind("<FocusOut>", lambda e: (self.configure(highlightbackground=C["border2"]), self._hint()))
        watch(self, variable, self._hint)
        self.after(10, self._hint)

    def _hint(self):
        if self.var.get():
            self.clear.pack(side="right")
        else:
            self.clear.pack_forget()
        if self.var.get() or self.focus_get() == self.entry:
            self.hint.place_forget()
        else:
            self.hint.place(in_=self.entry, x=0, rely=0.5, anchor="w")


def center_on(win, parent, size=None):
    """Put a popup window in the middle of the app window (instead of the screen's top-left corner).
    size: (w, h) when the window was given a fixed size."""
    win.withdraw()
    win.update_idletasks()
    w, h = size or (win.winfo_reqwidth(), win.winfo_reqheight())
    x = parent.winfo_rootx() + max(0, (parent.winfo_width() - w) // 2)
    y = parent.winfo_rooty() + max(0, (parent.winfo_height() - h) // 2)
    win.geometry(f"{w}x{h}+{x}+{y}" if size else f"+{x}+{y}")
    win.deiconify()


def watch(widget, variable, fn):
    """variable.trace_add for a widget, removed again when the widget is destroyed — otherwise closing a window
    (Settings, Export textures) leaves traces on shared settings that call into widgets that no longer exist."""
    tid = variable.trace_add("write", lambda *_: widget.winfo_exists() and fn())

    def drop(e):
        if e.widget is widget:
            try:
                variable.trace_remove("write", tid)
            except (tk.TclError, ValueError):
                pass

    widget.bind("<Destroy>", drop, add="+")


class Dropdown(tk.Frame):
    """Button that opens a dark menu. options: list of (value, label)."""

    def __init__(self, parent, title, variable, options=(), width=16, command=None):
        super().__init__(parent, bg=C["bg2"], highlightthickness=1, highlightbackground=C["border2"],
                         cursor="hand2")
        self.var, self.options, self.command = variable, list(options), command
        self.cap = tk.Label(self, text=title.upper(), bg=C["bg2"], fg=C["text3"], font=F["tiny"], padx=10)
        self.cap.pack(side="left")
        self.val = tk.Label(self, bg=C["bg2"], fg=C["text1"], font=F["bold"], width=width, anchor="w", pady=6)
        self.val.pack(side="left")
        self.arrow = tk.Label(self, text="▾", bg=C["bg2"], fg=C["text3"], font=F["base"], padx=8)
        self.arrow.pack(side="left")
        for w in (self, self.cap, self.val, self.arrow):
            w.bind("<Button-1>", self.open)
            w.bind("<Enter>", lambda e: self.configure(highlightbackground=C["border3"]))
            w.bind("<Leave>", lambda e: self.configure(highlightbackground=C["border2"]))
        watch(self, variable, self._show)
        self._show()

    def set_options(self, options):
        self.options = list(options)
        if self.options and self.var.get() not in [v for v, _ in self.options]:
            self.var.set(self.options[0][0])
        self._show()

    def _show(self):
        label = next((lbl for v, lbl in self.options if v == self.var.get()), self.var.get())
        self.val.configure(text=label.split("  (")[0])
        enabled = len(self.options) > 1
        self.val.configure(fg=C["text1"] if enabled else C["text3"])

    MAX_ROWS = 14

    def open(self, _e=None):
        """A dark, scrollable list under the box (in the app's own style, instead of the system menu)."""
        if len(self.options) <= 1:
            return
        if getattr(self, "_pop", None) is not None and self._pop.winfo_exists():
            self._close()
            return
        pop = self._pop = tk.Toplevel(self, bg=C["border3"])
        pop.overrideredirect(True)
        pop.attributes("-topmost", True)
        rows_n = min(len(self.options), self.MAX_ROWS)
        cur = self.var.get()
        frame = tk.Frame(pop, bg=C["bg2"])
        frame.pack(fill="both", expand=True, padx=1, pady=1)
        cv = tk.Canvas(frame, bg=C["bg2"], highlightthickness=0, bd=0)
        inner = tk.Frame(cv, bg=C["bg2"])
        cv.create_window(0, 0, window=inner, anchor="nw")
        self._rows = []
        for i, (value, label) in enumerate(self.options):
            main, _, tag = label.partition("  (")
            on = value == cur
            row = tk.Frame(inner, bg=C["bg3"] if on else C["bg2"], cursor="hand2")
            row.pack(fill="x")
            bar = tk.Frame(row, bg=C["accent"] if on else (C["bg3"] if on else C["bg2"]), width=3)
            bar.pack(side="left", fill="y")
            l1 = tk.Label(row, text=main, bg=row["bg"], fg=C["accent"] if on else C["text1"], font=F["base"],
                          anchor="w", padx=10, pady=4)
            l1.pack(side="left")
            parts = [row, bar, l1]
            if tag:
                l2 = tk.Label(row, text=tag.rstrip(")"), bg=row["bg"], fg=C["text3"], font=F["small"], padx=10)
                l2.pack(side="right")
                parts.append(l2)
            for w in parts:
                w.bind("<Button-1>", lambda e, v=value: (self._close(), self._pick(v)))
                w.bind("<Enter>", lambda e, i=i: self._hover(i))
            self._rows.append((row, parts, on))
        inner.update_idletasks()
        row_h = max(1, inner.winfo_reqheight() // max(1, len(self.options)))
        w = max(self.winfo_width(), inner.winfo_reqwidth() + (16 if len(self.options) > rows_n else 0))
        h = row_h * rows_n
        cv.configure(width=w, height=h, scrollregion=(0, 0, w, inner.winfo_reqheight()))
        cv.itemconfigure(1, width=w - (16 if len(self.options) > rows_n else 0))
        if len(self.options) > rows_n:
            sb = ttk.Scrollbar(frame, orient="vertical", command=cv.yview, style="Dark.Vertical.TScrollbar")
            cv.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
        cv.pack(side="left", fill="both", expand=True)
        # under the box, or above it when there's no room below
        # (kept on the monitor the box is on: other monitors can sit left of or above the main one)
        ml, mt, mr, mb = monitor_area(self)
        x, y = self.winfo_rootx(), self.winfo_rooty() + self.winfo_height() + 2
        if y + h + 4 > mb:
            y = self.winfo_rooty() - h - 4
        pop.geometry(f"+{max(ml, min(x, mr - w - 4))}+{max(mt, y)}")
        sel = next((i for i, (v, _l) in enumerate(self.options) if v == cur), 0)
        self._hi = None
        if len(self.options) > rows_n:  # show the selected one
            cv.yview_moveto(max(0.0, (sel - rows_n // 2) / len(self.options)))

        def wheel(e, d=None):
            cv.yview_scroll(d if d is not None else (-1 if e.delta > 0 else 1) * 2, "units")
            return "break"

        for wdg in [cv, inner] + [p for _r, ps, _o in self._rows for p in ps]:
            wdg.bind("<MouseWheel>", wheel)
            wdg.bind("<Button-4>", lambda e: wheel(e, -2))
            wdg.bind("<Button-5>", lambda e: wheel(e, 2))
        pop.bind("<Escape>", lambda e: self._close())
        pop.bind("<Up>", lambda e: self._key(-1, cv))
        pop.bind("<Down>", lambda e: self._key(1, cv))
        pop.bind("<Return>", lambda e: self._hi is not None and (self._close(), self._pick(self.options[self._hi][0])))
        # a click anywhere else closes it
        pop.bind("<ButtonPress-1>", lambda e: self._outside(e), add="+")
        # ...and so does moving, resizing or minimising the app window, or switching to another program
        # (it's a separate window, so it wouldn't follow the app)
        top = self.winfo_toplevel()
        geo = top.winfo_geometry()

        def moved(_e=None):
            if self._pop is pop and top.winfo_geometry() != geo:
                self._close()
        self._watch = [(top, "<Configure>", top.bind("<Configure>", moved, add="+")),
                       (top, "<Unmap>", top.bind("<Unmap>", lambda e: self._close(), add="+"))]
        pop.bind("<FocusOut>", lambda e: pop.after(80, lambda: self._pop is pop and pop.focus_get() is None
                                                   and self._close()))
        self._hover(sel)
        pop.update_idletasks()
        try:
            pop.grab_set()
        except tk.TclError:
            pass
        pop.focus_force()

    def _outside(self, e):
        p = self._pop
        if p is None:
            return
        x, y = e.x_root - p.winfo_rootx(), e.y_root - p.winfo_rooty()
        if not (0 <= x < p.winfo_width() and 0 <= y < p.winfo_height()):
            self._close()

    def _hover(self, i):
        if self._hi is not None and self._hi < len(self._rows):
            row, parts, on = self._rows[self._hi]
            for p_ in parts[:1] + parts[2:]:
                p_.configure(bg=C["bg3"] if on else C["bg2"])
        self._hi = i
        row, parts, on = self._rows[i]
        for p_ in parts[:1] + parts[2:]:
            p_.configure(bg=C["border3"])

    def _key(self, d, cv):
        i = max(0, min(len(self.options) - 1, (self._hi or 0) + d))
        self._hover(i)
        n = len(self.options)
        top, bot = cv.yview()
        if i / n < top or (i + 1) / n > bot:
            cv.yview_moveto(max(0.0, (i - self.MAX_ROWS // 2) / n))
        return "break"

    def _close(self):
        p, self._pop = getattr(self, "_pop", None), None
        for wdg, seq, fid in getattr(self, "_watch", ()):
            try:  # remove just this binding (older Pythons' unbind(seq, id) drops every binding of seq)
                keep = [ln for ln in wdg.bind(seq).split("\n") if not ln.startswith(f'if {{"[{fid} ')]
                wdg.bind(seq, "\n".join(keep) if any(k.strip() for k in keep) else "")
                wdg.deletecommand(fid)
            except tk.TclError:
                pass
        self._watch = []
        if p is not None:
            try:
                p.grab_release()
                p.destroy()
            except tk.TclError:
                pass

    def _pick(self, value):
        self.var.set(value)
        if self.command:
            self.command()


class Pill(tk.Label):
    def __init__(self, parent, text, kind="accent"):
        bg, fg, line = {"accent": (C["accent_tint"], C["accent"], C["accent_line"]),
                        "ok": (C["ok_tint"], C["ok"], C["ok_line"]),
                        "muted": (C["bg2"], C["text3"], C["border2"])}[kind]
        super().__init__(parent, text=text, bg=bg, fg=fg, font=F["tiny"], padx=6, pady=1, highlightthickness=1,
                         highlightbackground=line)


class Progress(tk.Canvas):
    def __init__(self, parent):
        super().__init__(parent, height=3, bg=C["border1"], highlightthickness=0, bd=0)
        self.running, self.pos = False, 0

    def start(self):
        if not self.running:
            self.running = True
            self._tick()

    def stop(self):
        self.running = False
        self.delete("all")

    def _tick(self):
        if not self.running:
            return
        w = max(self.winfo_width(), 100)
        seg = w // 4
        self.pos = (self.pos + max(6, w // 120)) % (w + seg)
        self.delete("all")
        self.create_rectangle(self.pos - seg, 0, self.pos, 3, fill=C["accent"], width=0)
        self.after(16, self._tick)


def box_image(kind, size):
    """16px checkbox images for the tree: off / on / part."""
    img = tk.PhotoImage(width=size + 6, height=size)
    fill = {"off": C["bg2"], "on": C["accent"], "part": C["bg2"]}[kind]
    line = {"off": C["border3"], "on": C["accent"], "part": C["accent"]}[kind]
    img.put(fill, to=(1, 1, size - 1, size - 1))
    for (x0, y0, x1, y1) in ((0, 0, size, 1), (0, size - 1, size, size), (0, 0, 1, size), (size - 1, 0, size, size)):
        img.put(line, to=(x0, y0, x1, y1))
    if kind == "on":
        pts = [(0.22, 0.5), (0.42, 0.7), (0.78, 0.3)]
        steps = size * 2
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            for i in range(steps + 1):
                x = (ax + (bx - ax) * i / steps) * size
                y = (ay + (by - ay) * i / steps) * size
                img.put("#ffffff", to=(int(x) - 1, int(y) - 1, int(x) + 1, int(y) + 1))
    elif kind == "part":
        q = size // 4
        img.put(C["accent"], to=(q + 1, q + 1, size - q - 1, size - q - 1))
    return img


def setup_ttk(root):
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass
    st.configure("Dark.Vertical.TScrollbar", gripcount=0, background=C["border2"], darkcolor=C["bg0"],
                 lightcolor=C["bg0"], troughcolor=C["bg0"], bordercolor=C["bg0"], arrowcolor=C["text3"],
                 relief="flat", arrowsize=10)
    st.map("Dark.Vertical.TScrollbar", background=[("active", C["border3"]), ("pressed", C["accent"])])
    st.configure("Dark.Treeview", background=C["bg1"], fieldbackground=C["bg1"], foreground=C["text1"],
                 bordercolor=C["bg1"], lightcolor=C["bg1"], darkcolor=C["bg1"], rowheight=round(26 * SCALE),
                 font=F["base"], indent=round(18 * SCALE))
    st.map("Dark.Treeview", background=[("selected", C["row_sel"])], foreground=[("selected", C["text1"])])
    st.configure("Dark.Treeview.Heading", background=C["bg2"], foreground=C["text3"], font=F["tiny"],
                 bordercolor=C["border2"], lightcolor=C["bg2"], darkcolor=C["bg2"], relief="flat", padding=(8, 5))
    st.map("Dark.Treeview.Heading", background=[("active", C["bg3"])])
    st.layout("Dark.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    try:  # make the expand arrows visible on dark
        st.configure("Dark.Treeview", indicatorforeground=C["text2"])
    except tk.TclError:
        pass


# ------------------------------------------------------------------ the app

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        setup_fonts(self)
        setup_ttk(self)
        self.title(f"{APP_NAME}  v{APP_VERSION}")
        self.geometry(f"{round(1440 * SCALE)}x{round(900 * SCALE)}")
        self.minsize(1100, 680)
        self.configure(bg=C["bg0"])
        self._set_icon()

        self.msgs = queue.Queue()
        self.proc = None
        self.worker = None
        self.cancelled = False
        self.run_log = None
        self.running_folder = None
        self.settings_win = None
        self.viewers = []          # running viewer processes
        self.sel_folder = None     # extraction card picked (Textures tab source)
        self.cat_data = None       # catalog built from the asset list
        self.history = load_history()  # {"updates": [{"date", "added": [keys], "removed": n}, ...]}
        self.tab = "assets"
        self.thumbs = {}           # path -> PhotoImage
        self.scroll_canvases = []
        self.card_widgets = []
        self.folders = []
        self._missing_pkgs = []
        self.index = AssetIndex()
        self.view = None        # AssetIndex used for the tree right now (full or search results)
        self.checked = set()    # ticked keys (folders or assets); never one inside another
        self._cat_rows = []     # catalog page rows, painted orange while selected
        self._mesh_deps = self._load_mesh_deps()  # mesh key -> materials / textures it uses
        self._dep_keys = set()  # selected only because a selected model uses them
        self.partial = set()    # every folder that has something ticked inside it
        self._search_job = None

        s = self._load_settings()
        self.v_game = tk.StringVar(value=s.get("game_dir", ""))
        self.v_key = tk.StringVar(value=self._read_key_file())
        self.v_search = tk.StringVar(value="")
        self.v_cat = tk.StringVar(value=s.get("category", ""))
        self.v_group = tk.StringVar(value=s.get("group", ""))
        self.v_type = tk.StringVar(value=s.get("type", "all"))
        self.v_convert = tk.BooleanVar(value=s.get("convert", True))
        self.v_keep_raw = tk.BooleanVar(value=s.get("keep_raw", False))
        self.v_fbx = tk.BooleanVar(value=s.get("fbx", True))
        self.v_max_tex = tk.StringVar(value=s.get("max_texture", "0"))           # full size
        self.v_pbr_auto = tk.StringVar(value=s.get("pbr_auto", "separate"))      # separate maps (Blender / Substance)
        self.v_pbr_preset = tk.StringVar(value=s.get("pbr_preset", "separate"))
        self.v_pbr_normal = tk.StringVar(value=s.get("pbr_normal", "auto"))
        self.dcs_cfg = dict(s.get("dcs", {}))  # the DCS page's folders and options
        self.settings_cat_width = int(s.get("catalog_list_width", 0) or 0)
        self._steam_prev = dict(s.get("steam_prev_launch", {}))  # localconfig.vdf -> launch options before ours
        self.v_keep_previews = tk.BooleanVar(value=bool(s.get("keep_previews", False)))  # off: wiped on quit
        self.v_bake_preset = tk.StringVar(value=s.get("bake_preset", s.get("pbr_preset", "separate")))
        self.v_bake_size = tk.StringVar(value=s.get("bake_size", "0"))
        self.v_bake_normal = tk.StringVar(value=s.get("bake_normal", "auto"))
        self.v_tex_src = tk.StringVar(value="")
        self.v_tex_role = tk.StringVar(value="all")
        self.v_tex_search = tk.StringVar(value="")
        self.v_tex_info = tk.StringVar(value="")
        self.v_tex_count = tk.StringVar(value="")
        self.v_cat_search = tk.StringVar(value="")
        self.v_cat_count = tk.StringVar(value="")
        self.v_view_status = tk.StringVar(value="")
        self.viewer_srv = None   # ac8_viewer.ViewerServer, only for "Open in browser"
        self.glv = None          # ac8_glview widget (native OpenGL), made on first use
        self._glv_failed = ""
        self._view_files, self._view_first, self._view_token = [], None, 0
        self._view_model = None
        self.v_view_model = tk.StringVar(value="")
        self.v_view_mode = tk.StringVar(value=s.get("view_mode", "lit"))
        self.v_view_wire = tk.BooleanVar(value=False)
        self.v_view_skel = tk.BooleanVar(value=False)
        self.v_view_grid = tk.BooleanVar(value=s.get("view_grid", True))
        self.v_view_bg = tk.StringVar(value=s.get("view_bg", "dark"))
        self.v_view_panel = tk.StringVar(value="parts")
        self.v_view_livery = tk.StringVar(value="")
        self.v_view_gear = tk.StringVar(value="down")
        self.v_view_probe = tk.StringVar(value="off")
        self.v_view_pylons = tk.StringVar(value="")
        self._view_liv_data, self._view_liv_final = None, None
        self.v_out_root = tk.StringVar(value=s.get("out_root", str(DEFAULT_OUT_ROOT)))
        self.v_step = tk.StringVar(value="Ready")
        self.v_status = tk.StringVar(value="")
        self.v_tools = tk.StringVar()
        self.v_pkgs = tk.StringVar()
        self.v_count = tk.StringVar()
        self.v_notice = tk.StringVar()
        self.v_sel = tk.StringVar(value="Nothing selected")
        self.v_browser = tk.StringVar(value="")
        if not self.v_game.get():
            self.v_game.set(detect_game_dir())

        self.img = {k: box_image(k, round(15 * SCALE)) for k in ("off", "on", "part")}
        self._removed_files = remove_obsolete()
        self._build_ui()
        self.refresh_status()
        self.refresh_folders()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._pump)
        self.after(50, self._load_index_on_start)
        self.after(3000, self._startup_update_check)
        self.after(1500, self._tools_if_missing)
        try:
            import ac8_update
            ac8_update.cleanup(HERE)
        except Exception:
            pass
        self.after(400, lambda: self._clear_scratch(previews=not self.v_keep_previews.get()))
        self.v_search.trace_add("write", lambda *_: self._search_soon())
        self._log(f"{APP_NAME} v{APP_VERSION}")
        if self._removed_files:
            self._log("  Tidied up files older versions left behind: " + ", ".join(self._removed_files))
        if not UNPACKER_DIR:
            self._log("Put this app in the folder that holds 'AC8 Unpacker'.", "err")

    def _set_icon(self):
        try:
            if IS_WIN and (ASSETS_DIR / "emblem.ico").exists():
                self.iconbitmap(default=str(ASSETS_DIR / "emblem.ico"))
            elif (ASSETS_DIR / "emblem_icon.png").exists():
                self._icon = tk.PhotoImage(file=str(ASSETS_DIR / "emblem_icon.png"))
                self.iconphoto(True, self._icon)
        except tk.TclError:
            pass

    def _load_image(self, name):
        hi = ASSETS_DIR / name.replace(".png", "@2x.png")
        path = hi if SCALE >= 1.5 and hi.exists() else ASSETS_DIR / name
        try:
            return tk.PhotoImage(file=str(path))
        except tk.TclError:
            return None

    # ---------------------------------------------------------- UI

    def _build_ui(self):
        top = tk.Frame(self, bg=C["bg1"])
        top.pack(fill="x")
        bar = tk.Frame(top, bg=C["bg1"], padx=18, pady=8)
        bar.pack(fill="x")

        self._logo = self._load_image("emblem_header.png")
        if self._logo:
            tk.Label(bar, image=self._logo, bg=C["bg1"]).pack(side="left")
        brand = tk.Frame(bar, bg=C["bg1"])
        brand.pack(side="left", padx=(10, 20))
        tk.Label(brand, text="ACE COMBAT 8", bg=C["bg1"], fg=C["text1"], font=F["title"]).pack(anchor="w")
        tk.Label(brand, text=f"ASSET EXTRACTOR  v{APP_VERSION}", bg=C["bg1"], fg=C["accent"], font=F["tiny"]).pack(anchor="w")

        self.search_in = Input(bar, self.v_search, icon="⌕", font=F["input"],
                               placeholder="Search assets…")
        self.search_in.pack(side="left", fill="x", expand=True)
        self.dd_cat = Dropdown(bar, "Category", self.v_cat, width=14, command=self._on_category)
        self.dd_cat.pack(side="left", padx=(10, 0))
        self.dd_group = Dropdown(bar, "Group", self.v_group, width=14, command=self._rebuild_tree)
        self.dd_group.pack(side="left", padx=(6, 0))
        self.dd_type = Dropdown(bar, "Type", self.v_type, TYPES, width=13, command=self._on_type)
        self.dd_type.pack(side="left", padx=(6, 0))
        Button(bar, "Settings", self.open_settings, icon="⚙").pack(side="right", padx=(14, 0))

        tb = tk.Frame(top, bg=C["bg1"], padx=18)
        tb.pack(fill="x", pady=(0, 10))

        def sep():
            tk.Frame(tb, bg=C["border2"], width=1, height=26).pack(side="left", padx=8)

        Button(tb, "Scan Game", self.scan_game, icon="⟳").pack(side="left")
        Button(tb, "Import List…", self.import_list, icon="⇪").pack(side="left", padx=(6, 0))
        sep()
        self.btn_tick_all = Button(tb, "Select All Shown", self.tick_all_shown, icon="☑")
        self.btn_tick_all.pack(side="left")
        Button(tb, "Clear Selection", self.clear_ticks, icon="✕").pack(side="left", padx=(6, 0))
        Button(tb, "Preview 3D", self.preview_selected, icon="◉").pack(side="left", padx=(6, 0))
        sep()
        tk.Label(tb, textvariable=self.v_sel, bg=C["bg1"], fg=C["accent"], font=F["bold"]).pack(side="left")

        self.btn_stop = Button(tb, "Stop", self.stop, variant="danger", icon="■")
        self.btn_stop.pack(side="right")
        self.btn_stop.set_enabled(False)
        self.btn_extract = Button(tb, "Extract Selected", self.extract, variant="primary", icon="▶", padx=16)
        self.btn_extract.pack(side="right", padx=(0, 8))
        tk.Frame(tb, bg=C["border2"], width=1, height=26).pack(side="right", padx=8)
        Check(tb, self.v_keep_raw, "Keep Raw").pack(side="right", padx=(10, 0))
        Check(tb, self.v_fbx, "Export FBX", command=self._on_fbx_toggle).pack(side="right", padx=(10, 0))
        Check(tb, self.v_convert, "Export PNG+GLB").pack(side="right", padx=(10, 0))
        tk.Frame(self, bg=C["border1"], height=1).pack(fill="x")

        self.notice = tk.Frame(self, bg=C["accent_tint"], highlightthickness=1, highlightbackground=C["accent_line"])
        tk.Label(self.notice, text="⚠", bg=C["accent_tint"], fg=C["accent"], font=F["bold"]).pack(side="left",
                                                                                                  padx=(12, 4))
        tk.Label(self.notice, textvariable=self.v_notice, bg=C["accent_tint"], fg=C["text1"],
                 font=F["base"]).pack(side="left", pady=7)
        Button(self.notice, "Open Settings", self.open_settings, font=F["small"], padx=10,
               pady=3).pack(side="right", padx=8, pady=5)

        # "updates available" bar (filled in by the check at every start)
        self.upd_bar = tk.Frame(self, bg=C["ok_tint"], highlightthickness=1, highlightbackground=C["ok_line"])
        tk.Label(self.upd_bar, text="⬆", bg=C["ok_tint"], fg=C["ok"], font=F["bold"]).pack(side="left", padx=(12, 4))
        self.v_upd = tk.StringVar()
        tk.Label(self.upd_bar, textvariable=self.v_upd, bg=C["ok_tint"], fg=C["text1"], font=F["base"]).pack(
            side="left", pady=7)
        x_ = tk.Label(self.upd_bar, text="✕", bg=C["ok_tint"], fg=C["text3"], font=F["small"], cursor="hand2", padx=8)
        x_.pack(side="right", padx=(0, 6))
        x_.bind("<Button-1>", lambda e: self.upd_bar.pack_forget())
        self.upd_btn_tools = Button(self.upd_bar, "Update tools", lambda: (self.upd_bar.pack_forget(), self.fetch_tools()),
                                    font=F["small"], padx=10, pady=3)
        self.upd_btn_app = Button(self.upd_bar, "Update app", lambda: (self.upd_bar.pack_forget(), self.update_app()),
                                  variant="primary", font=F["small"], padx=10, pady=3)

        # tab bar
        self.tabbar = tk.Frame(self, bg=C["bg0"])
        self.tabbar.pack(fill="x", padx=18, pady=(10, 0))
        self.tab_labels = {}
        for key, text in (("assets", "ASSETS"), ("catalog", "CATALOG"), ("textures", "TEXTURES"),
                          ("viewer", "3D VIEWER"), ("dcs", "DCS")):
            lbl = tk.Label(self.tabbar, text=text, bg=C["bg0"], fg=C["text3"], font=F["section"], padx=14, pady=6,
                           cursor="hand2")
            lbl.pack(side="left")
            lbl.bind("<Button-1>", lambda e, k=key: self.show_tab(k))
            self.tab_labels[key] = lbl
        self.tab_labels["assets"].configure(fg=C["accent"])
        tk.Frame(self, bg=C["border1"], height=1).pack(fill="x", padx=18)

        # body: (assets | catalog | textures) | extractions
        self.body = tk.PanedWindow(self, orient="horizontal", bg=C["bg0"], sashwidth=10, bd=0,
                                   sashrelief="flat", showhandle=False)
        self.body.pack(fill="both", expand=True, padx=18, pady=(10, 0))
        self._build_browser()
        self._build_catalog()
        self._build_textures()
        self._build_dcs()
        self._build_viewer_pane()
        self._build_extractions()

        tk.Frame(self, bg=C["border1"], height=1).pack(fill="x", pady=(12, 0))
        bottom = tk.Frame(self, bg=C["bg1"], padx=18, pady=8)
        bottom.pack(fill="x")
        row = tk.Frame(bottom, bg=C["bg1"])
        row.pack(fill="x")
        self.step_dot = tk.Label(row, text="●", bg=C["bg1"], fg=C["text3"], font=F["small"])
        self.step_dot.pack(side="left")
        tk.Label(row, textvariable=self.v_step, bg=C["bg1"], fg=C["text1"], font=F["bold"]).pack(side="left",
                                                                                                padx=(6, 0))
        lk = tk.Label(row, text="Logs folder", bg=C["bg1"], fg=C["accent"], font=F["small"], cursor="hand2")
        lk.pack(side="right", padx=(12, 0))
        lk.bind("<Button-1>", lambda e: open_folder(LOGS_DIR))
        tk.Label(row, textvariable=self.v_status, bg=C["bg1"], fg=C["text3"], font=F["mono_s"]).pack(side="right")
        self.progress = Progress(bottom)
        self.progress.pack(fill="x", pady=(6, 6))
        logbox = tk.Frame(bottom, bg=C["bg0"], highlightthickness=1, highlightbackground=C["border2"])
        logbox.pack(fill="x")
        self.log = tk.Text(logbox, height=7, bg=C["bg0"], fg=C["text2"], font=F["mono"], bd=0, relief="flat",
                           wrap="word", padx=12, pady=6, insertbackground=C["accent"],
                           selectbackground=C["accent_cc"], highlightthickness=0, state="disabled")
        lsb = ttk.Scrollbar(logbox, orient="vertical", command=self.log.yview, style="Dark.Vertical.TScrollbar")
        self.log.configure(yscrollcommand=lsb.set)
        lsb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        for tag, col in (("ok", C["ok"]), ("err", C["accent"]), ("cmd", C["text3"]), ("warn", C["warn"])):
            self.log.tag_configure(tag, foreground=col)

    def _panel(self, title, var, link=None):
        frame = tk.Frame(self.body, bg=C["bg0"])
        head = tk.Frame(frame, bg=C["bg0"])
        head.pack(fill="x", pady=(0, 8))
        tk.Label(head, text=title, bg=C["bg0"], fg=C["text2"], font=F["section"]).pack(side="left")
        if link:
            lk = tk.Label(head, text=link[0], bg=C["bg0"], fg=C["accent"], font=F["small"], cursor="hand2")
            lk.pack(side="right", padx=(10, 0))
            lk.bind("<Button-1>", lambda e: link[1]())
        tk.Label(head, textvariable=var, bg=C["bg0"], fg=C["text3"], font=F["small"]).pack(side="right")
        box = tk.Frame(frame, bg=C["bg1"], highlightthickness=1, highlightbackground=C["border2"])
        box.pack(fill="both", expand=True)
        return frame, box

    def _build_browser(self):
        frame, box = self._panel("ASSET BROWSER", self.v_browser)
        self.body.add(frame, minsize=520, stretch="always")
        self.pane_assets = frame
        self._left_pane = frame
        self._extr_shown = True
        self.tree = ttk.Treeview(box, style="Dark.Treeview", columns=("assets", "meshes", "textures"),
                                 selectmode="extended")
        self.tree.heading("#0", text="NAME   (click the box to select · double-click to open)", anchor="w")
        for col, text, w in (("assets", "ASSETS", 80), ("meshes", "MESHES", 76), ("textures", "TEXTURES", 82)):
            self.tree.heading(col, text=text, anchor="e")
            self.tree.column(col, width=round(w * SCALE), anchor="e", stretch=False)
        self.tree.column("#0", width=round(420 * SCALE), stretch=True)
        self.tree.tag_configure("dir", font=F["bold"])
        self.tree.tag_configure("file", foreground=C["text2"])
        self.tree.tag_configure("more", foreground=C["text3"])
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview, style="Dark.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True, padx=1, pady=1)
        self.tree.bind("<<TreeviewOpen>>", self._on_open)
        self.tree.bind("<Button-1>", self._on_tree_click, add=True)
        self.tree.bind("<space>", self._on_space)

        self.empty = tk.Frame(box, bg=C["bg1"])
        self._big = self._load_image("emblem_large.png")
        if self._big:
            tk.Label(self.empty, image=self._big, bg=C["bg1"]).pack(pady=(10, 14))
        tk.Label(self.empty, text="No asset list yet", bg=C["bg1"], fg=C["text1"], font=F["h"]).pack()
        tk.Label(self.empty, text="Scan Game reads every container once (about a minute) and remembers the list.\n"
                                  "Already have a listing from the unpacker (output.txt)? Use Import List…",
                 bg=C["bg1"], fg=C["text3"], font=F["base"], justify="center").pack(pady=(4, 12))
        btns = tk.Frame(self.empty, bg=C["bg1"])
        btns.pack()
        Button(btns, "Scan Game", self.scan_game, variant="primary", icon="⟳").pack(side="left")
        Button(btns, "Import List…", self.import_list, icon="⇪").pack(side="left", padx=(8, 0))

    def _build_extractions(self):
        frame, box = self._panel("EXTRACTIONS", self.v_count,
                                 ("Open folder ▸", lambda: open_folder(self.v_out_root.get() or DEFAULT_OUT_ROOT)))
        self.pane_extr = frame
        self.body.add(frame, minsize=440, width=round(560 * SCALE))
        self.canvas = tk.Canvas(box, bg=C["bg1"], highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.canvas.yview, style="Dark.Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.cards = tk.Frame(self.canvas, bg=C["bg1"])
        self.cards_win = self.canvas.create_window(0, 0, window=self.cards, anchor="nw")
        self.cards.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.cards_win, width=e.width))
        # A divider (PanedWindow sash) shows the resize cursor by setting it on the whole divider widget while the
        # mouse is over the sash, and puts it back when the mouse leaves. On Windows Tk only re-reads the cursor of
        # the window the mouse is in when that window's own cursor changes, so after the divider has put its cursor
        # back, the page the mouse moved into keeps showing the resize arrows (until the mouse crosses into another
        # widget). Fix: whenever a divider puts its cursor back, and whenever the mouse enters something, re-apply
        # the cursor of the widget under the mouse so Tk works it out again.
        self.tk.eval("""
            namespace eval ::ac8 {}
            proc ::ac8::touchCursor {} {
                set w [winfo containing [winfo pointerx .] [winfo pointery .]]
                if {$w ne "" && [winfo exists $w]} { catch { $w configure -cursor [$w cget -cursor] } }
            }
            foreach p {Leave Cursor} {
                if {[info procs ::ac8::pw$p] eq "" && [info procs ::tk::panedwindow::$p] ne ""} {
                    rename ::tk::panedwindow::$p ::ac8::pw$p
                    proc ::tk::panedwindow::$p {w} "::ac8::pw$p \\$w; after idle ::ac8::touchCursor"
                }
            }""")
        self.bind_all("<Enter>", self._unstick_sash_cursor, add="+")
        self.bind_all("<MouseWheel>", lambda e: self._scroll(-1 if e.delta > 0 else 1, e))
        self.bind_all("<Button-4>", lambda e: self._scroll(-1, e))
        self.bind_all("<Button-5>", lambda e: self._scroll(1, e))

    def _unstick_sash_cursor(self, e=None):
        if e is not None and isinstance(e.widget, tk.PanedWindow):
            return  # over a divider itself: leave its cursor alone
        for pw in (getattr(self, "body", None), getattr(self, "cat_split", None)):
            if pw is None:
                continue
            try:
                # what Tk itself does once it sees the mouse has left the divider: restore the saved cursor and
                # forget it (works the same in Tk 8.6 and 9)
                self.tk.eval(f"""
                    if {{[info exists ::tk::Priv({pw},panecursor)]}} {{
                        {pw} configure -cursor $::tk::Priv({pw},panecursor)
                        unset ::tk::Priv({pw},panecursor)
                        if {{[info exists ::tk::Priv({pw},pwAfterId)]}} {{
                            after cancel $::tk::Priv({pw},pwAfterId)
                            unset ::tk::Priv({pw},pwAfterId)
                        }}
                    }}""")
                if str(pw.cget("cursor")):  # our dividers never set a cursor of their own
                    pw.configure(cursor="")
            except tk.TclError:
                pass
        try:
            self.tk.call("::ac8::touchCursor")
        except tk.TclError:
            pass

    def _scroll(self, direction, e):
        w = self.winfo_containing(e.x_root, e.y_root)
        if self.glv is not None and w is not None and str(w).startswith(str(self.glv)):
            up = direction < 0
            if e.state & 0x0001:  # Shift: fly speed
                sp = self.glv.change_fly_speed(1 if up else -1)
                self.v_view_status.set(f"Fly speed ×{sp:.2f}")
            elif e.state & (0x20000 if IS_WIN else 0x0008):  # Alt only (on Windows 0x0008 is Num Lock): field of view
                fov = self.glv.change_fov(-1 if up else 1)
                self.v_view_status.set(f"Field of view {fov:.0f}°")
            else:
                self.glv._wheel(-direction)
            return
        while w is not None:
            if w is self.canvas or w in self.scroll_canvases:
                w.yview_scroll(direction * 3, "units")
                return
            if w in (self.log, self.tree):
                if not IS_WIN and w is self.tree:  # X11 wheel events are not delivered to Treeview by default
                    self.tree.yview_scroll(direction * 3, "units")
                return
            w = w.master

    # ---------------------------------------------------------- asset index + tree

    def _load_index_on_start(self):
        src = None
        if INDEX_FILE.exists():
            src = INDEX_FILE
        elif UNPACKER_DIR and (UNPACKER_DIR / "output.txt").exists():
            src = UNPACKER_DIR / "output.txt"
        if src:
            self._load_index(src, save=(src != INDEX_FILE))
        else:
            self._rebuild_tree()

    def _load_index(self, path, save=False):
        try:
            text = AssetIndex.read_text_any(path)
            paths = AssetIndex.parse_listing(text)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Couldn't read that listing:\n{e}")
            return
        if not paths:
            messagebox.showerror(APP_NAME, "No .uasset/.umap paths found in that file.")
            return
        self._set_index(paths, f"{Path(path).name}", save)

    def _set_index(self, paths, source, save):
        old = {k for k, _kl, _t in self.index.files} if (source == "game scan" and len(self.index)) else None
        self.index = AssetIndex(paths, source)
        if source == "game scan":  # a game update can change what a model uses: look it up again
            self._mesh_deps = {}
            try:
                DEPS_FILE.unlink()
            except OSError:
                pass
        if old is not None:
            new = {k for k, _kl, _t in self.index.files}
            added, removed = sorted(new - old), len(old - new)
            if added:
                self.history["updates"].append({"date": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                                                "added": added, "removed": removed})
                self.history["updates"] = self.history["updates"][-20:]
                save_history(self.history)
                self._log(f"✔ Game update found: {fmt(len(added))} new file(s)"
                          + (f", {fmt(removed)} removed" if removed else "")
                          + " — see Catalog › New in game updates", "ok")
        if save:
            try:
                self.index.save(paths, note=source)
            except OSError:
                pass
        self.checked.clear()
        self._log(f"✔ Asset list loaded: {fmt(len(self.index))} assets ({source})")
        self._fill_categories()
        self.cat_data = None
        self._cat_shown = None
        if self.tab == "catalog":
            self.refresh_catalog()
        self._rebuild_tree()

    def _fill_categories(self):
        root = self.index.root
        cats = sorted(root.dirs.values(), key=lambda n: (n.name.startswith("["), -n.total))
        self.dd_cat.set_options([("", f"All  ({fmt(root.total)})")] +
                                [(n.key, f"{n.name}  ({fmt(n.total)})") for n in cats])
        self._fill_groups()

    def _fill_groups(self):
        node = self.index.nodes.get(self.v_cat.get())
        if not self.v_cat.get() or node is None:
            self.dd_group.set_options([("", "—")])
            self.v_group.set("")
            return
        groups = sorted(node.dirs.values(), key=lambda n: -n.total)
        self.dd_group.set_options([("", f"All  ({fmt(node.total)})")] +
                                  [(n.key, f"{n.name}  ({fmt(n.total)})") for n in groups])

    def _on_category(self):
        self.v_group.set("")
        self._fill_groups()
        self._rebuild_tree()

    def _on_type(self):
        self._rebuild_tree()
        self._update_selection()

    def _view_root(self):
        return self.v_group.get() or self.v_cat.get() or ""

    def _search_soon(self):
        if self._search_job:
            self.after_cancel(self._search_job)
        self._search_job = self.after(250, self._rebuild_tree)

    def _rebuild_tree(self):
        self._save_settings()
        t = self.v_type.get()
        self.tree.delete(*self.tree.get_children())
        if not len(self.index):
            self.tree.pack_forget()
            self.empty.place(relx=0.5, rely=0.45, anchor="center")
            self.v_browser.set("")
            self.btn_tick_all.set_enabled(False)
            return
        self.empty.place_forget()
        if not self.tree.winfo_ismapped():
            self.tree.pack(fill="both", expand=True, padx=1, pady=1)
        root_key = self._view_root()
        words = self.v_search.get().lower().split()
        if words:  # search: build a small index of just the matches
            prefix = (root_key + "/").lower() if root_key else ""
            hits = [k for k, kl, ft in self.index.files
                    if kl.startswith(prefix) and (t == "all" or ft == t) and all(w in kl for w in words)]
            self.view = AssetIndex([full_path(k) for k in hits], "search")
            self.view_t = "all"  # already filtered
            node = self.view.nodes.get(root_key, self.view.root if not root_key else None)
            shown = len(hits)
            self.v_browser.set(f"{fmt(shown)} matches" + (" (showing first folders)" if shown > 20000 else ""))
            self.btn_tick_all.set_enabled(shown > 0)
            self.btn_tick_all.set_text("☑  Select All Results")
        else:
            self.view, self.view_t = self.index, t
            node = self.index.nodes.get(root_key)
            shown = node.count(t) if node else 0
            where = root_key.replace("/", " › ") if root_key else "whole game"
            self.v_browser.set(f"{fmt(shown)} {TYPE_LABEL[t].lower() if t != 'all' else 'assets'} · {where}")
            self.btn_tick_all.set_enabled(bool(root_key) and shown > 0)
            self.btn_tick_all.set_text("☑  Select All Shown")
        if node is not None:
            self._insert_children("", node)
            if words and shown <= 400:
                self._expand_all("")
        self._refresh_boxes()

    def _insert_children(self, parent_iid, node, limit=3000):
        t = self.view_t
        dirs = sorted((c for c in node.dirs.values() if c.count(t)), key=lambda n: n.name.lower())
        files = sorted((f for f in node.files if t == "all" or f[1] == t), key=lambda f: f[0].lower())
        for c in dirs:
            iid = c.key
            self.tree.insert(parent_iid, "end", iid=iid, text="  " + c.name, image=self.img["off"], tags=("dir",),
                             values=(fmt(c.count(t)), fmt(c.types.get("SM", 0) + c.types.get("SK", 0)) or "",
                                     fmt(c.types.get("T", 0)) or ""))
            self.tree.insert(iid, "end", iid="\0" + iid, text="")  # placeholder so the arrow shows
        for i, (name, ft) in enumerate(files):
            if i >= limit:
                self.tree.insert(parent_iid, "end", iid="\0more" + node.key, text=f"  … {len(files) - limit} more "
                                 "(narrow it down with search or Type)", tags=("more",))
                break
            key = f"{node.key}/{name}" if node.key else name
            self.tree.insert(parent_iid, "end", iid=key, text="  " + name, image=self.img["off"], tags=("file",),
                             values=(TYPE_SHORT[ft], "", ""))

    def _on_open(self, _e=None):
        iid = self.tree.focus()
        kids = self.tree.get_children(iid)
        if len(kids) == 1 and kids[0].startswith("\0"):
            self.tree.delete(kids[0])
            node = self.view.nodes.get(iid)
            if node:
                self._insert_children(iid, node)
                self._refresh_boxes(iid)

    def _expand_all(self, iid):
        for child in self.tree.get_children(iid):
            if child.startswith("\0"):
                continue
            if self.view.nodes.get(child) is not None:
                self.tree.focus(child)
                self.tree.item(child, open=True)
                self._on_open()
                self._expand_all(child)

    # --- ticking
    def _load_mesh_deps(self):
        try:
            return json.loads(DEPS_FILE.read_text()).get("deps", {})
        except (OSError, ValueError, AttributeError):
            return {}

    def _state(self, key):
        if key in self.checked:
            return "on"
        parts = key.split("/")
        for i in range(1, len(parts)):
            if "/".join(parts[:i]) in self.checked:
                return "on"
        return "part" if key in self.partial else "off"

    def _refresh_boxes(self, under=""):
        self.partial = set()
        for c in self.checked:
            parts = c.split("/")
            for i in range(1, len(parts)):
                self.partial.add("/".join(parts[:i]))
        stack = list(self.tree.get_children(under))
        if under and not under.startswith("\0"):
            stack.append(under)
        while stack:
            iid = stack.pop()
            if iid.startswith("\0"):
                continue
            self.tree.item(iid, image=self.img[self._state(iid)])
            stack += self.tree.get_children(iid)
        self._update_selection()
        self._cat_paint_rows()

    def _on_tree_click(self, e):
        if self.tree.identify_region(e.x, e.y) != "tree":
            return
        iid = self.tree.identify_row(e.y)
        if not iid or iid.startswith("\0"):
            return
        elem = self.tree.identify_element(e.x, e.y)
        if "image" in elem:
            self.toggle(iid)
            return "break"

    def _on_space(self, _e=None):
        for iid in self.tree.selection():
            if not iid.startswith("\0"):
                self.toggle(iid, refresh=False)
        self._refresh_boxes()
        return "break"

    def toggle(self, key, refresh=True):
        state = self._state(key)
        if state == "on":
            if key in self.checked:
                self.checked.discard(key)
            else:  # ticked through a parent folder: untick just this branch
                parts = key.split("/")
                anc = next("/".join(parts[:i]) for i in range(1, len(parts)) if "/".join(parts[:i]) in self.checked)
                self.checked.discard(anc)
                node = self.index.nodes[anc]
                for depth in range(len(anc.split("/")), len(parts)):
                    keep = "/".join(parts[:depth + 1])
                    for c in node.dirs.values():
                        if c.key != keep:
                            self.checked.add(c.key)
                    for f, _ft in node.files:
                        fk = f"{node.key}/{f}"
                        if fk != keep:
                            self.checked.add(fk)
                    node = node.dirs.get(parts[depth]) if depth < len(parts) - 1 else None
                    if node is None:
                        break
        else:
            pre = key + "/"
            self.checked = {c for c in self.checked if not c.startswith(pre)}
            self.checked.add(key)
        if refresh:
            self._refresh_boxes()

    def tick_all_shown(self):
        t = self.v_type.get()
        if self.view is not self.index:  # search results: tick exactly the matching assets
            hits = [k for k, _kl, _ft in self.view.files if self._state(k) != "on"]
            for k in cover_keys(self.index, hits, t):
                if self._state(k) != "on":
                    pre = k + "/"
                    self.checked = {c for c in self.checked if not c.startswith(pre)}
                    self.checked.add(k)
        else:
            root = self._view_root()
            if root and self._state(root) != "on":
                self.toggle(root, refresh=False)
        self._refresh_boxes()

    def clear_ticks(self):
        self.checked.clear()
        self._dep_keys.clear()
        self._refresh_boxes()

    def _selected_assets(self, t=None):
        t = t or self.v_type.get()
        out = []
        for key in self.checked:
            out += self.index.assets_under(key, t)
        return out

    def _update_selection(self):
        if not self.checked:
            self.v_sel.set("Nothing selected")
            return
        t = self.v_type.get()
        total = 0
        for key in self.checked:
            node, fname = self.index.find(key)
            if node is None:
                continue
            if fname is None:
                total += node.count(t)
            elif t == "all" or asset_type(fname) == t:
                total += 1
        what = TYPE_LABEL[t].lower() if t != "all" else "assets"
        self.v_sel.set(f"{len(self.checked)} selected  ·  {fmt(total)} {what}")

    # ---------------------------------------------------------- extraction cards

    def scan_folders(self):
        root = Path(self.v_out_root.get() or DEFAULT_OUT_ROOT)
        out = []
        if root.is_dir():
            for d in root.iterdir():
                if d.is_dir() and ((d / RAW_DIR_NAME).is_dir() or (d / FINAL_DIR_NAME).is_dir()):
                    out.append(d)
        out.sort(key=lambda d: d.stat().st_mtime, reverse=True)
        return out

    def refresh_folders(self):
        self.folders = self.scan_folders()
        for w in self.card_widgets:
            w.destroy()
        self.card_widgets = [self._make_card(f) for f in self.folders]
        if not self.folders:
            empty = tk.Frame(self.cards, bg=C["bg1"])
            tk.Label(empty, text="No extractions yet", bg=C["bg1"], fg=C["text1"], font=F["h"]).pack(pady=(30, 4))
            tk.Label(empty, text="Select assets on the left, then press  ▶ Extract Selected.", bg=C["bg1"],
                     fg=C["text3"], font=F["base"]).pack()
            self.card_widgets = [empty]
        for w in self.card_widgets:
            w.pack(fill="x", padx=10, pady=(10, 0))
        self.v_count.set(f"{len(self.folders)} folder{'s' if len(self.folders) != 1 else ''}")

    def _make_card(self, folder):
        running = folder == self.running_folder
        info = read_info(folder)
        raw = count_files(folder / RAW_DIR_NAME, ".uasset", ".umap")
        glb = count_files(folder / FINAL_DIR_NAME / "Meshes", ".glb")
        fbx = count_files(folder / FINAL_DIR_NAME / "FBX", ".fbx")
        png = count_files(folder / FINAL_DIR_NAME / "Textures", ".png")
        card = tk.Frame(self.cards, bg=C["bg2"], highlightthickness=1,
                        highlightbackground=C["accent"] if running else C["border2"])
        tk.Frame(card, bg=C["accent"] if running else C["bg2"], width=3).pack(side="left", fill="y")
        inner = tk.Frame(card, bg=C["bg2"], padx=12, pady=9)
        inner.pack(side="left", fill="both", expand=True)
        r1 = tk.Frame(inner, bg=C["bg2"])
        r1.pack(fill="x")
        tk.Label(r1, text=folder.name, bg=C["bg2"], fg=C["text1"], font=F["bold"], anchor="w").pack(side="left")
        if folder == self.sel_folder and not running:
            card.configure(highlightbackground=C["border3"])
        state, kind = (("RUNNING", "accent") if running else ("DONE", "ok") if glb or png
                       else ("RAW", "accent") if raw else ("EMPTY", "muted"))
        Pill(r1, state, kind).pack(side="right")
        what = info.get("selected") or info.get("ticked") or info.get("asset filter") or "—"
        if len(what) > 90:
            what = what[:87] + "…"
        when = info.get("date") or _dt.datetime.fromtimestamp(folder.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        typ = info.get("type", "")
        tk.Label(inner, text=what, bg=C["bg2"], fg=C["text2"], font=F["small"], anchor="w").pack(fill="x",
                                                                                                pady=(4, 0))
        tk.Label(inner, text=f"{when}" + (f"   ·   {typ}" if typ and typ != "All types" else ""), bg=C["bg2"],
                 fg=C["text3"], font=F["mono_s"], anchor="w").pack(fill="x", pady=(2, 8))
        r3 = tk.Frame(inner, bg=C["bg2"])
        r3.pack(fill="x")
        Pill(r3, f"UASSET {fmt(raw)}", "accent" if raw else "muted").pack(side="left")
        Pill(r3, f"PNG {fmt(png)}", "ok" if png else "muted").pack(side="left", padx=(5, 0))
        Pill(r3, f"GLB {fmt(glb)}", "ok" if glb else "muted").pack(side="left", padx=(5, 0))
        if fbx:
            Pill(r3, f"FBX {fmt(fbx)}", "ok").pack(side="left", padx=(5, 0))
        small = dict(font=F["small"], padx=8, pady=1)
        r4 = tk.Frame(inner, bg=C["bg2"])   # buttons on their own row, so the counts never get squeezed
        r4.pack(fill="x", pady=(7, 0))
        Button(r4, "Final", lambda f=folder: open_folder(f / FINAL_DIR_NAME), **small).pack(side="right")
        Button(r4, "Raw", lambda f=folder: open_folder(f / RAW_DIR_NAME), **small).pack(side="right", padx=(0, 5))
        if raw and not running:
            Button(r4, "Convert", lambda f=folder: self.convert_folder(f), **small).pack(side="right", padx=(0, 5))
        if glb and not running:
            Button(r4, "View 3D", lambda f=folder: self.view_folder(f), variant="primary", **small).pack(
                side="right", padx=(0, 5))

        def pick(_e, f=folder):
            self.sel_folder = f
            self.refresh_folders()
            if self.tab == "textures":
                self.v_tex_src.set(str(f))
                self.load_textures()

        for w in (card, inner, r1, r3, r4):
            w.bind("<Button-1>", pick)
        return card

    # ---------------------------------------------------------- settings window

    def open_settings(self):
        if self.settings_win and self.settings_win.winfo_exists():
            self.settings_win.lift()
            return
        w = tk.Toplevel(self, bg=C["bg0"])
        self.settings_win = w
        w.title("Settings")
        ml, mt, mr, mb = monitor_area(self)
        sw, sh = round(800 * SCALE), min(round(800 * SCALE), mb - mt - 80)
        w.geometry(f"{sw}x{sh}")
        w.transient(self)
        pad = tk.Frame(w, bg=C["bg0"], padx=22, pady=18)
        pad.pack(fill="both", expand=True)
        tk.Label(pad, text="Settings", bg=C["bg0"], fg=C["text1"], font=F["title"]).pack(anchor="w")
        tk.Label(pad, text="Saved automatically.", bg=C["bg0"], fg=C["text3"], font=F["small"]).pack(anchor="w",
                                                                                                    pady=(0, 12))

        def section(title):
            box = tk.Frame(pad, bg=C["bg1"], highlightthickness=1, highlightbackground=C["border2"], padx=14,
                           pady=10)
            box.pack(fill="x", pady=(0, 10))
            tk.Label(box, text=title, bg=C["bg1"], fg=C["text2"], font=F["section"]).pack(anchor="w", pady=(0, 8))
            return box

        def label(row, text):
            tk.Label(row, text=text, bg=C["bg1"], fg=C["text3"], font=F["small"], width=16,
                     anchor="w").pack(side="left")

        def field(box, text, var, buttons, mono=False):
            row = tk.Frame(box, bg=C["bg1"])
            row.pack(fill="x", pady=3)
            label(row, text)
            for btext, cmd in reversed(buttons):
                Button(row, btext, cmd, font=F["small"], padx=10, pady=4).pack(side="right", padx=(6, 0))
            Input(row, var, mono=mono).pack(side="left", fill="x", expand=True)

        box = section("GAME")
        field(box, "Game folder", self.v_game, [("Browse…", self._browse_game), ("Auto-detect", self._auto_game)])
        field(box, "AES key", self.v_key, [("Find key", self.find_key)], mono=True)
        row = tk.Frame(box, bg=C["bg1"])
        row.pack(fill="x", pady=3)
        label(row, "Offline start")
        tk.Label(row, text="needed once, to find the key", bg=C["bg1"], fg=C["text3"],
                 font=F["small"]).pack(side="left")
        Button(row, "Remove offline launch option", self.remove_offline, font=F["small"], padx=10, pady=4).pack(
            side="right", padx=(6, 0))
        Button(row, "Start game offline", self.start_offline, font=F["small"], padx=10, pady=4).pack(side="right")
        box = section("OUTPUT")
        field(box, "Exports folder", self.v_out_root, [("Browse…", self._browse_out)])
        row = tk.Frame(box, bg=C["bg1"])
        row.pack(fill="x", pady=3)
        label(row, "Max texture size")
        Dropdown(row, "Per texture / UDIM tile", self.v_max_tex, TEXTURE_SIZES, width=10).pack(side="left")
        tk.Label(row, text="  bigger = sharper but much larger files", bg=C["bg1"], fg=C["text3"],
                 font=F["small"]).pack(side="left")
        row = tk.Frame(box, bg=C["bg1"])
        row.pack(fill="x", pady=3)
        label(row, "Also make PBR maps")
        Dropdown(row, "After converting", self.v_pbr_auto, PBR_PRESETS, width=30).pack(side="left")
        row = tk.Frame(box, bg=C["bg1"])
        row.pack(fill="x", pady=3)
        label(row, "3D previews")
        Check(row, self.v_keep_previews, "Keep between sessions (faster to reopen, uses disk)").pack(side="left")
        v_freed = tk.StringVar(value="")

        def clear_now():
            if self.worker and self.worker.is_alive():
                v_freed.set("busy — try when nothing is running")
                return
            v_freed.set(f"freed {self._clear_scratch():.0f} MB")

        Button(row, "Clear now", clear_now, font=F["small"], padx=10, pady=4).pack(side="right")
        tk.Label(row, textvariable=v_freed, bg=C["bg1"], fg=C["text3"], font=F["small"]).pack(side="right",
                                                                                              padx=(0, 8))
        box = section("APP")
        row = tk.Frame(box, bg=C["bg1"])
        row.pack(fill="x", pady=3)
        label(row, "Version")
        tk.Label(row, text=f"v{APP_VERSION}", bg=C["bg1"], fg=C["text1"], font=F["base"]).pack(side="left")
        rel = tk.Label(row, text="  release notes", bg=C["bg1"], fg=C["accent"], font=F["small"], cursor="hand2")
        rel.pack(side="left")
        rel.bind("<Button-1>", lambda e: __import__("webbrowser").open(__import__("ac8_update").RELEASES_PAGE))
        Button(row, "Check for updates", self.update_app, font=F["small"], padx=10, pady=4).pack(side="right")
        row = tk.Frame(box, bg=C["bg1"])
        row.pack(fill="x", pady=3)
        label(row, "Support")
        kofi = tk.Label(row, text="Buy me a coffee on Ko-fi", bg=C["bg1"], fg=C["accent"], font=F["base"],
                        cursor="hand2")
        kofi.pack(side="left")
        kofi.bind("<Button-1>", lambda e: __import__("webbrowser").open(KOFI_URL))
        tk.Label(row, text="  — if the app helps you, thank you!", bg=C["bg1"], fg=C["text3"],
                 font=F["small"]).pack(side="left")
        tk.Label(box, text="Disclaimer: This tool is intended for legitimate modding, research, and educational use "
                           "only. Unauthorized redistribution, commercial exploitation, and misuse of copyrighted "
                           "assets are not supported. All extracted assets remain the property of their respective "
                           "copyright holders.", bg=C["bg1"], fg=C["text3"],
                 font=F["small"], anchor="w", justify="left", wraplength=round(720 * SCALE)).pack(fill="x", pady=(4, 0))
        box = section("SETUP")
        for text, var, btext, cmd in (("Tools", self.v_tools, "Check for updates", self.fetch_tools),
                                      ("Python packages", self.v_pkgs, "Install packages", self.install_packages)):
            row = tk.Frame(box, bg=C["bg1"])
            row.pack(fill="x", pady=3)
            label(row, text)
            tk.Label(row, textvariable=var, bg=C["bg1"], fg=C["text1"], font=F["base"]).pack(side="left")
            Button(row, btext, cmd, font=F["small"], padx=10, pady=4).pack(side="right")
        cred = tk.Label(box, text="retoc & repak by Truman Kilen (trumank) · asset conversion by Archengius · "
                                  "MIT licence — third-party notices", bg=C["bg1"], fg=C["text3"], font=F["small"],
                        cursor="hand2", anchor="w")
        cred.pack(fill="x", pady=(4, 0))
        cred.bind("<Button-1>", lambda e: UNPACKER_DIR and open_folder(UNPACKER_DIR / "tools" / "THIRD-PARTY-NOTICES.txt"))
        foot = tk.Frame(pad, bg=C["bg0"])
        foot.pack(fill="x", pady=(6, 0))
        Button(foot, "Done", lambda: self._close_settings(w), variant="primary", padx=18).pack(side="right")
        w.protocol("WM_DELETE_WINDOW", lambda: self._close_settings(w))
        center_on(w, self, (sw, sh))

    def _on_fbx_toggle(self):
        pass  # FBX is written by the app itself (ac8_fbx.py) — nothing to check

    def _close_settings(self, w):
        self._save_settings()
        self.refresh_status()
        self.refresh_folders()
        w.destroy()

    # ---------------------------------------------------------- small helpers

    def _describe_ticks(self, t=None):
        """(short name for the folder, one-line description) of what is ticked. Materials / textures selected only
        because a model uses them don't count for the name (else a pilot's outfit would be named after the
        shared Base/Material folder)."""
        keys = sorted(k for k in self.checked if k not in self._dep_keys) or sorted(self.checked)
        common = os.path.commonprefix([k.split("/") for k in keys]) if keys else []
        if len(keys) == 1:
            name, desc = keys[0].split("/")[-1].rsplit(".", 1)[0], keys[0]
        elif common:
            base = "/".join(common)
            name, desc = common[-1], f"{base}  ({len(keys)} items)"
        else:
            q = self.v_search.get().strip()
            name = q or keys[0].split("/")[0]
            desc = (f'search "{q}"' if q else "several folders") + f"  ({len(keys)} items)"
        short = catalog.export_name(keys)  # one aircraft / character: F18C, Joker1_Cope, NPC_XO …
        if short:
            name = short
        t = t or self.v_type.get()
        if t != "all":
            name += "_" + TYPE_LABEL[t].split(" ")[0]
        return safe_name(name), desc

    def _new_folder(self, name):
        base = f"{name}_{_dt.datetime.now():%Y-%m-%d_%H%M}"
        root = Path(self.v_out_root.get() or DEFAULT_OUT_ROOT)
        folder, n = root / base, 2
        while folder.exists():
            folder, n = root / f"{base}_{n}", n + 1
        return folder

    def _browse_game(self):
        d = filedialog.askdirectory(title="Folder that holds start_protected_game.exe")
        if d:
            self.v_game.set(d)

    def _auto_game(self):
        found = detect_game_dir()
        if found:
            self.v_game.set(found)
            self._log(f"Game found: {found}", "ok")
        else:
            messagebox.showwarning(APP_NAME, "Could not find Ace Combat 8 through Steam. Use Browse… instead.")

    def _browse_out(self):
        d = filedialog.askdirectory(title="Where should extraction folders be created?")
        if d:
            self.v_out_root.set(d)

    def _read_key_file(self):
        if UNPACKER_DIR and (UNPACKER_DIR / "aes-key.txt").exists():
            return (UNPACKER_DIR / "aes-key.txt").read_text(encoding="ascii", errors="ignore").strip()
        return ""

    def _load_settings(self):
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_settings(self):
        data = {"game_dir": self.v_game.get(), "out_root": self.v_out_root.get(),
                "category": self.v_cat.get(), "group": self.v_group.get(), "type": self.v_type.get(),
                "convert": self.v_convert.get(), "fbx": self.v_fbx.get(),
                "max_texture": self.v_max_tex.get(),
                "pbr_auto": self.v_pbr_auto.get(), "pbr_preset": self.v_pbr_preset.get(),
                "pbr_normal": self.v_pbr_normal.get(), "bake_preset": self.v_bake_preset.get(),
                "keep_previews": self.v_keep_previews.get(), "steam_prev_launch": self._steam_prev,
                "catalog_list_width": self._cat_list_width(),
                "bake_size": self.v_bake_size.get(), "bake_normal": self.v_bake_normal.get(), "view_mode": self.v_view_mode.get() if not self.v_view_mode.get().startswith("tex_") else "lit", "view_grid": self.v_view_grid.get(),
                "view_bg": self.v_view_bg.get(),
                "keep_raw": self.v_keep_raw.get(), "dcs": self.dcs_cfg}
        try:
            SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _paks_dir(self):
        game = self.v_game.get().strip() or detect_game_dir()
        if not game:
            return None
        paks = Path(game) / "Game" / "Content" / "Paks"
        return paks if paks.is_dir() else None

    def refresh_status(self):
        problems = []
        if UNPACKER_DIR:
            tools = UNPACKER_DIR / "tools"
            import ac8_tools
            missing = [t for t in ("retoc", "repak") if not ((tools / f"{t}.exe").exists() or shutil.which(t))]
            oodle = (tools / "oo2core_9_win64.dll").exists()
            parts = []
            for t in ("retoc", "repak"):
                v = ac8_tools.installed(tools, t)
                parts.append(f"{t} missing" if v is None and t in missing else f"{t} {v} ✔" if v else f"{t} ✔")
            self.v_tools.set("   ".join(parts) + ("   oodle ✔" if oodle else "   oodle — Check for updates gets it"))
            if missing:
                problems.append(" & ".join(missing) + " missing")
        else:
            self.v_tools.set("AC8 Unpacker folder not found")
            problems.append("AC8 Unpacker folder not found")
        if not TEXTURE_CONVERTER.exists():
            problems.append("ac8_textures.py missing")
        self._missing_pkgs = [pip for mod, pip in PACKAGES.items() if importlib.util.find_spec(mod) is None]
        txt = "all installed ✔" if not self._missing_pkgs else "missing: " + ", ".join(self._missing_pkgs)
        self.v_pkgs.set(txt)
        if [p for p in self._missing_pkgs if p in ("Pillow", "numpy")]:
            problems.append("Python packages missing")
        elif [p for p in self._missing_pkgs if p in ("PyOpenGL", "pyopengltk") and (IS_WIN or p == "PyOpenGL")]:
            problems.append("3D viewer packages missing (Install packages)")
        if not KEY_RE.match(self.v_key.get().strip()):
            problems.append("no AES key")
        if not self._paks_dir():
            problems.append("game folder not set")
        if problems:
            self.v_notice.set("Setup needed: " + " · ".join(problems))
            self.notice.pack(fill="x", padx=18, pady=(12, 0), before=self.body)
        else:
            self.notice.pack_forget()

    # ---------------------------------------------------------- log / pump

    def _log(self, text, tag=None):
        if tag is None:
            low = text.lower()
            if text.startswith("✔"):
                tag = "ok"
            elif text.startswith("$ "):
                tag = "cmd"
            elif "error" in low or "failed" in low or "traceback" in low:
                tag = "err"
            elif low.lstrip().startswith(("note:", "warning")):
                tag = "warn"
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")
        SLOG.write(text)
        if self.run_log:
            try:
                self.run_log.write(text + "\n")
            except Exception:
                pass

    def post(self, kind, value=""):
        self.msgs.put((kind, value))

    def _pump(self):
        try:
            while True:
                kind, value = self.msgs.get_nowait()
                if kind == "log":
                    self._log(value)
                elif kind == "status":
                    self.v_status.set(value[-110:])
                elif kind == "step":
                    self.v_step.set(value)
                elif kind == "done":
                    self._finish(value)
                elif kind == "call":
                    value()
        except queue.Empty:
            pass
        self.after(80, self._pump)

    # ---------------------------------------------------------- running things

    def _busy(self, on):
        self.btn_extract.set_enabled(not on)
        self.btn_stop.set_enabled(on)
        self.step_dot.configure(fg=C["accent"] if on else C["text3"])
        if on:
            self.progress.start()
        else:
            self.progress.stop()
            self.v_status.set("")

    def _start(self, target, title, folder=None):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo(APP_NAME, "Something is already running. Wait for it or press Stop.")
            return
        self._save_settings()
        self.cancelled = False
        self.running_folder = folder
        self._busy(True)
        self.v_step.set(title)
        if folder is not None:
            self.refresh_folders()
        self.worker = threading.Thread(target=self._guard, args=(target,), daemon=True)
        self.worker.start()

    def _guard(self, target):
        try:
            result = target()
        except _Stop as e:
            result = str(e) or "Stopped."
        except Exception as e:
            SLOG.exc("Task failed")
            result = f"ERROR: {e}"
        self.post("done", result or "Done.")

    def _finish(self, message):
        self._busy(False)
        first = message.splitlines()[0]
        self.v_step.set(first)
        self.step_dot.configure(fg=C["ok"] if first.startswith("✔") else
                                C["accent"] if first.startswith(("ERROR", "Stopped")) else C["text3"])
        self._log(message)
        if self.run_log:
            self.run_log.close()
            self.run_log = None
        self.running_folder = None
        self.refresh_status()
        self.refresh_folders()

    def stop(self):
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            self._log("Stopping…", "warn")
            try:
                if IS_WIN:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)], capture_output=True,
                                   creationflags=NO_WINDOW)
                else:
                    self.proc.terminate()
            except Exception:
                pass

    def run_cmd(self, args, cwd, log_lines=True, show_cmd=True):
        if self.cancelled:
            raise _Stop("Stopped.")
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        if show_cmd:
            shown = [str(a) for a in args]
            if len(shown) > 14:
                shown = shown[:12] + [f"… ({len(shown) - 12} more args)"]
            self.post("log", "$ " + " ".join(f'"{a}"' if " " in a else a for a in shown))
        self.proc = subprocess.Popen([str(a) for a in args], cwd=str(cwd), env=env, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        lines, buf = [], ""

        def emit(text):
            lines.append(text)
            if log_lines:
                self.post("log", text)

        read = getattr(self.proc.stdout, "read1", self.proc.stdout.read)
        while True:
            chunk = read(65536)
            if not chunk:
                break
            buf = (buf + chunk.decode("utf-8", errors="replace")).replace("\r\n", "\n")
            while True:
                m = re.search(r"[\r\n]", buf)
                if not m:
                    break
                i = m.start()
                if buf[i] == "\r" and i == len(buf) - 1:
                    break
                text, sep, buf = buf[:i], buf[i], buf[i + 1:]
                if sep == "\n":
                    emit(text)
                elif text.strip():
                    self.post("status", text.strip())
        if buf.strip("\r").strip():
            emit(buf.strip("\r"))
        code = self.proc.wait()
        self.proc = None
        if self.cancelled:
            raise _Stop("Stopped.")
        return code, lines

    def _unpacker_base(self):
        if not UNPACKER_DIR:
            raise RuntimeError("'AC8 Unpacker' folder not found next to this app.")
        cmd = [console_python(), "-u", UNPACKER_DIR / "ac8_unpack.py"]
        if self.v_game.get().strip():
            cmd += ["--game-dir", self.v_game.get().strip()]
        return cmd

    def _check_key(self):
        key = self.v_key.get().strip()
        if not KEY_RE.match(key):
            messagebox.showerror(APP_NAME, "The AES key must be 0x followed by 64 hex digits.\n\nOpen Settings and "
                                           "paste it in, or start the game (without anti-cheat) and click Find key.")
            return None
        try:
            (UNPACKER_DIR / "aes-key.txt").write_text(key + "\n", encoding="ascii")
        except Exception:
            pass
        return key

    def _check_ready(self, need_unpack=True, need_convert=True):
        problems = []
        if need_unpack:
            if not retoc_exe():
                problems.append("retoc.exe is missing. Settings → Download tools.")
            if not self._paks_dir():
                problems.append("Game folder not found. Settings → Game folder.")
        if need_convert:
            for f in (TEXTURE_CONVERTER, MESH_CONVERTER, CODE / "ac8_mesh.py", CODE / "ac8_vt.py",
                      CODE / "ac8_package.py"):
                if not f.exists():
                    problems.append(f"{f.name} is missing next to this app.")
            conv = [p for p in self._missing_pkgs if p in ("Pillow", "numpy")]
            if conv:
                problems.append("Missing Python packages: " + ", ".join(conv) + ". Settings → Install packages.")
        if problems:
            messagebox.showerror(APP_NAME, "\n\n".join(problems))
            return False
        return True

    def _open_log(self, folder):
        folder.mkdir(parents=True, exist_ok=True)
        self.run_log = open(folder / LOG_NAME, "a", encoding="utf-8")
        self.run_log.write(f"\n===== {_dt.datetime.now():%Y-%m-%d %H:%M:%S} =====\n")

    def _write_info(self, folder, ticked=None, type_label=None, n_raw=0, meshes=0, textures=0, started=None, fbx=0):
        old = read_info(folder)
        ticked = ticked if ticked is not None else old.get("selected", old.get("ticked", old.get("asset filter", "")))
        type_label = type_label or old.get("type", "All types")
        info = (f"{APP_NAME} extraction\n"
                f"Date:          {_dt.datetime.now():%Y-%m-%d %H:%M}\n"
                f"Selected:      {ticked}\n"
                f"Type:          {type_label}\n"
                f"Raw UAssets:   {n_raw}\n"
                f"GLB meshes:    {meshes}\n"
                f"PNG textures:  {textures}\n"
                f"FBX files:     {fbx}\n"
                f"Time taken:    {time.time() - (started or time.time()):.0f} s\n"
                f"Game folder:   {self.v_game.get() or '(auto)'}\n")
        items = getattr(self, "_info_items", None)
        if items:
            info += "\nTicked items\n" + "\n".join("  " + k for k in items[:2000]) + "\n"
        elif (folder / INFO_NAME).exists():  # keep the list written earlier
            old_text = (folder / INFO_NAME).read_text(encoding="utf-8")
            if "\nTicked items\n" in old_text:
                info += "\nTicked items\n" + old_text.split("\nTicked items\n", 1)[1]
        (folder / INFO_NAME).write_text(info, encoding="utf-8")

    # ---------------------------------------------------------- pipeline steps (worker thread)

    def _step_unpack(self, key, filters, raw, paks):
        raw.mkdir(parents=True, exist_ok=True)
        batches = list(batch_filters(filters)) if filters else [[]]
        for i, batch in enumerate(batches, 1):
            if len(batches) > 1:
                self.post("step", f"Step 1 — Unpacking from the game (part {i} of {len(batches)})…")
            args = [retoc_exe(), "-a", key, "to-legacy", "--version", ENGINE, "--no-shaders"]
            for f in batch:
                args += ["-f", f]
            code, _ = self.run_cmd(args + [paks, raw], UNPACKER_DIR)
            if code != 0:
                self.post("log", f"  retoc exited with code {code}")
        n = count_files(raw, ".uasset", ".umap")
        if n == 0:
            raise RuntimeError("Nothing was unpacked. Check the log, the AES key and the game folder.")
        self.post("log", f"✔ Step 1 done: {fmt(n)} .uasset files in {raw}")
        return n

    def _step_shared(self, key, raw, paks):
        """Meshes use materials and textures from shared folders too (the ejection seat's MI_MK14 lives in
        Vehicles/Aircraft/Common/EjectionSeat): unpack what the extracted meshes and materials point at outside
        the ticked folders, a few levels deep (mesh -> material -> parent material / textures)."""
        from ac8_package import Package
        want = {"MaterialInstanceConstant", "Material", "Texture2D", "TextureCube"}
        scan = [f for f in raw.rglob("*.uasset") if f.name.split("_", 1)[0] in ("SK", "SM", "MI", "M", "MP")]
        got = 0
        for _round in range(3):
            need = set()
            for f in scan:
                try:
                    pkg = Package(f)
                except Exception:
                    continue
                for i, imp in enumerate(pkg.imports):
                    if imp["class"] not in want or imp["name"].startswith("Default__"):
                        continue
                    path = pkg.import_path(-i - 1)
                    if not path or not path[0].startswith("/Game/"):
                        continue
                    rel = "Live/Content/" + path[0][len("/Game/"):] + ".uasset"
                    if not (raw / rel).exists():
                        need.add(rel)
            if not need:
                break
            self.post("step", f"Unpacking {len(need)} shared material(s)/texture(s) the meshes use…")
            self._retoc(key, sorted(need), raw, paks)
            scan = [raw / r for r in need if (raw / r).exists()]
            got += len(scan)
        if got:
            self.post("log", f"✔ Shared materials: {got} package(s) from outside the selected folders")

    def _step_emblems(self, key, raw, paks):
        """Liveries put emblems from a shared folder (Vehicles/Aircraft/Common/Emblem, Emblem/…) on the aircraft —
        unpack the ones this extraction's livery materials use, so the viewer can show them."""
        try:
            import ac8_livery
            need = ac8_livery.emblem_imports(raw)
        except Exception as e:
            self.post("log", f"  Emblems: couldn't check ({e})")
            return 0
        got = 0
        if need:
            self.post("step", f"Unpacking {len(need)} emblem texture(s) used by the liveries…")
            self._retoc(key, need, raw, paks)
            got = sum(1 for n in need if (raw / n).exists())
            self.post("log", f"✔ Emblems: {got} of {len(need)} unpacked for the liveries")
        if not any(raw.rglob("Materials/*/MI_*_Decal.uasset")):
            return got
        # skin names ("Joker 1", "Queen 1"…): skin tables + icons, and the game's English text file
        try:
            skins = ac8_livery.skin_imports(raw)
        except Exception as e:
            skins = []
            self.post("log", f"  Skin names: couldn't check ({e})")
        if skins:
            self.post("step", "Unpacking the skin tables and icons…")
            self._retoc(key, skins, raw, paks)
        if UNPACKER_DIR and not any(raw.rglob("Game.locres")):
            self.post("step", "Unpacking the game's English text (skin names)…")
            self.run_cmd(self._unpacker_base() + ["--key", key, "extract-pak", "Live/Content/Localization/Game/en",
                                                  str(raw)], UNPACKER_DIR, log_lines=False)
            self.post("log", "✔ Skin names: language file " + ("found" if any(raw.rglob("Game.locres"))
                                                                else "not found (names will use the game's own keys)"))
        return got

    def _step_font_files(self, key, raw):
        """A FontFace can keep its font file next to the package as <name>.ufont, packed in the game's .pak
        files instead of the containers retoc reads: fetch those for the fonts that need them."""
        try:
            import ac8_fonts
            fonts = [f for f in raw.rglob("*.uasset") if ac8_fonts.is_font_face(f)]
            need = sorted({f.parent.relative_to(raw).as_posix() for f in fonts if not ac8_fonts.font_from_package(f)})
        except Exception as e:
            self.post("log", f"  Fonts: couldn't check ({e})")
            return
        if need and UNPACKER_DIR:
            self.post("step", f"Unpacking the font files of {len(need)} font folder(s)…")
            for folder in need:
                self.run_cmd(self._unpacker_base() + ["--key", key, "extract-pak", folder, str(raw)], UNPACKER_DIR,
                             log_lines=False)

    def _step_convert(self, raw, final, fbx=False):
        """Textures -> PNG, meshes -> GLB, and optionally GLB -> FBX (built in). Returns (glb, png, fbx)."""
        size = self.v_max_tex.get() or "0"
        tex_out, mesh_out = final / "Textures", final / "Meshes"
        tex_out.mkdir(parents=True, exist_ok=True)
        self.post("step", "Converting textures to PNG…")
        code, _ = self.run_cmd([console_python(), "-u", TEXTURE_CONVERTER, raw, tex_out, "--max-texture", size], HERE)
        if code != 0:
            raise RuntimeError(f"Texture conversion failed (exit code {code}). See the log.")
        textures = count_files(tex_out, ".png")
        self.post("log", f"✔ Textures: {fmt(textures)} PNG in {tex_out}")
        if textures and any(raw.rglob("MI_*_Decal.uasset")):
            self.post("step", "Reading liveries…")
            self.run_cmd([console_python(), "-u", LIVERY_SCRIPT, raw, final], HERE)
        self.post("step", "Converting meshes to GLB…")
        code, _ = self.run_cmd([console_python(), "-u", MESH_CONVERTER, raw, mesh_out, "--max-texture", size], HERE)
        if code != 0:
            raise RuntimeError(f"Mesh conversion failed (exit code {code}). See the log.")
        meshes = count_files(mesh_out, ".glb")
        if meshes:
            self.post("log", f"✔ Meshes: {fmt(meshes)} GLB in {mesh_out}")
        if any(raw.rglob("*.uasset")):
            fdir = final / "Fonts"
            self.run_cmd([console_python(), "-u", FONT_SCRIPT, raw, fdir], HERE)
            nf = count_files(fdir, ".ttf", ".otf", ".ttc") if fdir.exists() else 0
            if nf:
                self.post("log", f"✔ Fonts: {fmt(nf)} font file(s) in {fdir}")
        if textures and self.v_pbr_auto.get() not in ("", "off"):
            self._step_pbr(tex_out, final / "PBR", self.v_pbr_auto.get(), self.v_pbr_normal.get())
        fbx_count = 0
        if fbx and meshes:
            fbx_count = self._step_fbx(mesh_out, final / "FBX")
        if textures == 0 and meshes == 0 and not count_files(final / "Fonts", ".ttf", ".otf", ".ttc"):
            self.post("log", "  Note: nothing to convert here. Textures become PNG, meshes GLB and fonts TTF/OTF;\n"
                             "  materials, animations, audio… stay as raw files in 1_Raw_UAssets.")
        return meshes, textures, fbx_count

    def _step_pbr(self, tex_dir, out_dir, preset, normal="auto", only=None):
        self.post("step", f"Making PBR maps ({dict(PBR_PRESETS).get(preset, preset)})…")
        cmd = [console_python(), "-u", PBR_SCRIPT, tex_dir, out_dir, "--preset", preset]
        if normal and normal != "auto":
            cmd += ["--normal", normal]
        if only:
            cmd += ["--only", ",".join(sorted(only))]
        code, _ = self.run_cmd(cmd, HERE)
        n = count_files(out_dir, ".png")
        if code != 0:
            raise RuntimeError(f"PBR conversion failed (exit code {code}). See the log.")
        self.post("log", f"✔ PBR: {fmt(n)} maps in {out_dir}")
        return n

    def _step_fbx(self, glb_dir, fbx_dir):
        n_glb = count_files(glb_dir, ".glb")
        if not n_glb:
            return 0
        fbx_dir.mkdir(parents=True, exist_ok=True)
        self.post("step", f"Making FBX ({n_glb} files)…")
        code, lines = self.run_cmd([console_python(), "-u", FBX_CONVERTER, glb_dir, fbx_dir], HERE, log_lines=False)
        for line in lines:
            if line.startswith("FBX ") and "ERROR" in line or "Traceback" in line or line.startswith("Error"):
                self.post("log", "  " + line)
        n = count_files(fbx_dir, ".fbx")
        self.post("log", f"✔ FBX: {fmt(n)} files in {fbx_dir}" if n else "  FBX: none written — see the lines above.")
        return n

    def extract(self, all_types=False):
        """all_types: ignore the Type filter (the catalog's buttons: a model always comes with its textures)."""
        if not len(self.index):
            messagebox.showinfo(APP_NAME, "Scan the game or import a list first, then select what you want.")
            return
        if not self.checked:
            messagebox.showinfo(APP_NAME, "Select at least one folder or asset in the browser.")
            return
        convert = self.v_convert.get()
        if not self._check_ready(need_convert=convert):
            return
        key = self._check_key()
        if not key:
            return
        t = "all" if all_types else self.v_type.get()
        assets = self._selected_assets(t)
        if not assets:
            messagebox.showinfo(APP_NAME, f"None of the selected items contain {TYPE_LABEL[t].lower()}.")
            return
        filters = make_filters(self.index, assets, t)
        if len(assets) > 20000 and not messagebox.askyesno(
                APP_NAME, f"That's {fmt(len(assets))} assets. It will take a while and use a lot of disk space.\n\n"
                          "Continue?"):
            return
        name, ticked = self._describe_ticks(t)
        items = sorted(self.checked)
        paks, keep_raw, fbx = self._paks_dir(), self.v_keep_raw.get(), self.v_fbx.get()
        folder = self._new_folder(name)
        raw, final = folder / RAW_DIR_NAME, folder / FINAL_DIR_NAME
        raw.mkdir(parents=True, exist_ok=True)
        self._open_log(folder)
        self._info_items = items
        self._write_info(folder, ticked, TYPE_LABEL[t], started=time.time())
        self._log(f"Extracting {fmt(len(assets))} assets with {len(filters)} filter(s) → {folder.name}")
        if t != "all":
            self._log(f"  Type filter is on: only {TYPE_LABEL[t].lower()} are extracted (Type → All types for "
                      "everything)", "warn")

        def work():
            started = time.time()
            self.post("step", "Step 1 of 2 — Unpacking from the game…" if convert else "Unpacking from the game…")
            n = self._step_unpack(key, filters, raw, paks)
            self._step_shared(key, raw, paks)
            self._step_emblems(key, raw, paks)
            self._step_font_files(key, raw)
            meshes = textures = nfbx = 0
            if convert:
                meshes, textures, nfbx = self._step_convert(raw, final, fbx)
            self._write_info(folder, ticked, TYPE_LABEL[t], n, meshes, textures, started, nfbx)
            nfont = count_files(final / "Fonts", ".ttf", ".otf", ".ttc") if convert else 0
            if convert and not keep_raw and (textures or meshes or nfont):
                shutil.rmtree(raw, ignore_errors=True)
                self.post("log", "Raw UAssets deleted (Keep Raw is off).")
            tail = (f" — {fmt(n)} raw, {fmt(textures)} PNG, {fmt(meshes)} GLB" + (f", {fmt(nfbx)} FBX" if nfbx else "")
                    + (f", {fmt(nfont)} fonts" if nfont else "") if convert else f" — {fmt(n)} raw assets")
            reopen, self._view_reopen = getattr(self, "_view_reopen", None), None
            if reopen and meshes:  # started from the viewer's Extract button: show the jet again, liveries and all
                self.post("call", lambda: self.open_viewer([final / "Meshes"], reopen))
            return f"✔ Done in {time.time() - started:.0f}s{tail}\nFolder: {folder}"

        self._start(work, "Starting…", folder)

    def convert_folder(self, folder):
        if not self._check_ready(need_unpack=False):
            return
        raw, final = folder / RAW_DIR_NAME, folder / FINAL_DIR_NAME
        fbx = self.v_fbx.get()
        self._open_log(folder)

        key = self.v_key.get().strip() if KEY_RE.match(self.v_key.get().strip()) else None
        paks = self._paks_dir()

        def work():
            started = time.time()
            if key and paks and raw.exists():
                self._step_shared(key, raw, paks)
                self._step_emblems(key, raw, paks)
                self._step_font_files(key, raw)
            meshes, textures, nfbx = self._step_convert(raw, final, fbx)
            self._info_items = None
            self._write_info(folder, None, None, count_files(raw, ".uasset", ".umap"), meshes, textures, started,
                             nfbx)
            return (f"✔ Converted: {fmt(textures)} PNG, {fmt(meshes)} GLB" + (f", {fmt(nfbx)} FBX" if nfbx else "")
                    + f"\nFolder: {final}")

        self._start(work, "Starting…", folder)

    def scan_game(self):
        if not self._check_ready(need_convert=False):
            return
        key = self._check_key()
        if not key:
            return

        def work():
            self.post("step", "Scanning every game container for assets (about a minute)…")
            code, lines = self.run_cmd(self._unpacker_base() + ["--key", key, "list"], UNPACKER_DIR,
                                       log_lines=False)
            paths = AssetIndex.parse_listing("\n".join(lines))
            if not paths:
                tail = "\n".join(lines[-15:])
                raise RuntimeError(f"Scan found no assets (exit code {code}).\n{tail}")
            self.post("call", lambda: self._set_index(paths, "game scan", True))
            return f"✔ Scan complete: {fmt(len(paths))} assets"

        self._start(work, "Scanning…")

    def import_list(self):
        start = str(UNPACKER_DIR) if UNPACKER_DIR else str(HERE)
        path = filedialog.askopenfilename(title="Pick a listing (e.g. output.txt from ac8_unpack.py list)",
                                          initialdir=start, filetypes=[("Text", "*.txt"), ("All files", "*.*")])
        if path:
            self._load_index(path, save=True)

    def _game_root(self):
        game = self.v_game.get().strip() or detect_game_dir()
        return Path(game) if game and Path(game).is_dir() else None

    def _place_offline_json(self, root):
        try:
            src, dst = ASSETS_DIR / OFFLINE_JSON, root / OFFLINE_JSON
            if not dst.exists() or dst.read_bytes() != src.read_bytes():
                shutil.copyfile(src, dst)
                self._log(f"  Copied {OFFLINE_JSON} into {root}")
            return True
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Couldn't copy {OFFLINE_JSON} into the game folder:\n{e}\n\n"
                                           "If the game is under Program Files, start this app as administrator once.")
            return False

    def _steam_appid(self, root):
        """Ace Combat 8's Steam app id, from the Steam library the game is installed in (appmanifest_*.acf)."""
        try:
            ids = root / "steam_appid.txt"
            if ids.exists() and ids.read_text().strip().isdigit():
                return ids.read_text().strip()
            for acf in root.parent.parent.glob("appmanifest_*.acf"):  # steamapps/common/<game> -> steamapps
                txt = acf.read_text(encoding="utf-8", errors="replace")
                m = re.search(r'"installdir"\s+"([^"]+)"', txt)
                if m and m.group(1).lower() == root.name.lower():
                    return acf.stem.split("_", 1)[1]
        except OSError:
            pass
        return None

    def _steam_ready(self):
        """(game folder, Steam app id) or None after telling the user what's missing."""
        root = self._game_root()
        if root is None or not (root / "start_protected_game.exe").exists():
            messagebox.showerror(APP_NAME, "Set the Game folder in Settings first (the Ace Combat 8 folder that holds "
                                           "start_protected_game.exe).")
            return None
        appid = self._steam_appid(root)
        if not appid:
            messagebox.showerror(APP_NAME, "Couldn't find Ace Combat 8 in your Steam library (no appmanifest for "
                                           f"“{root.name}”).")
            return None
        import ac8_steam
        if not ac8_steam.configs():
            messagebox.showerror(APP_NAME, "Couldn't find Steam's user settings (Steam/userdata).")
            return None
        return root, appid

    def _steam_edit(self, appid, value, then, title):
        """Close Steam if it's running, set (value) or restore (value=None) the launch options, then run `then`."""
        import ac8_steam

        def work():
            if ac8_steam.running():
                self.post("step", "Closing Steam (it rewrites its settings when it exits)…")
                if not ac8_steam.shutdown():
                    raise RuntimeError("Steam didn't close. Close it yourself (Steam → Exit) and try again.")
            changed = 0
            for cfg in ac8_steam.configs():
                key = str(cfg)
                if value:
                    prev = ac8_steam.set_launch_options(appid, value, cfg)
                    if prev != value:  # remember the user's own options, to put them back later
                        self._steam_prev[key] = prev or ""
                else:
                    cur = ac8_steam.get_launch_options(appid, cfg)
                    if key not in self._steam_prev and cur != STEAM_LAUNCH_OPTION:
                        continue  # not ours: leave the user's own launch options alone
                    ac8_steam.set_launch_options(appid, self._steam_prev.pop(key, "") or None, cfg)
                changed += 1
            self.post("call", self._save_settings)
            self.post("log", f"  Steam launch options {'set' if value else 'restored'} for {changed} Steam user(s)")
            return then()

        self._start(work, title)

    def start_offline(self):
        """Put AC8_Offline.json in the game folder, add the offline launch option in Steam, start the game."""
        got = self._steam_ready()
        if not got:
            return
        root, appid = got
        if not self._place_offline_json(root):
            return
        import ac8_steam
        if ac8_steam.running() and not messagebox.askokcancel(
                APP_NAME, "Steam will close for a moment so the offline launch option can be added, then the game "
                          "starts through Steam.\n\nWhen it reaches the title screen, click Find key."):
            return

        def then():
            ac8_steam.open_url(f"steam://rungameid/{appid}")
            return "✔ Game starting offline through Steam — click Find key at the title screen"

        self._steam_edit(appid, STEAM_LAUNCH_OPTION, then, "Starting the game offline…")

    def remove_offline(self):
        """Put the game's Steam launch options back to what they were, so it starts normally again."""
        got = self._steam_ready()
        if not got:
            return
        _root, appid = got
        import ac8_steam
        if ac8_steam.running() and not messagebox.askokcancel(
                APP_NAME, "Steam will close for a moment to remove the offline launch option, then open again."):
            return

        def then():
            ac8_steam.open_url("steam://open/main")
            return "✔ Offline launch option removed — the game starts normally again"

        self._steam_edit(appid, None, then, "Removing the offline launch option…")

    def find_key(self):
        self._scan_key()

    def _scan_key(self):
        """The game is at its title screen (offline): read the key from it."""
        if not UNPACKER_DIR:
            messagebox.showerror(APP_NAME, "The AC8 Unpacker folder wasn't found next to this app.")
            return
        if importlib.util.find_spec("Crypto") is None:
            messagebox.showerror(APP_NAME, "pycryptodome is needed for this. Click 'Install packages' first.")
            return
        def work():
            self.post("step", "Scanning the running game for the AES key…")
            code, _ = self.run_cmd(self._unpacker_base() + ["find-key"], UNPACKER_DIR)
            key = self._read_key_file()
            if code == 0 and KEY_RE.match(key):
                self.post("call", lambda: self.v_key.set(key))
                self.post("call", self._after_key)
                return f"✔ Key found and saved: {key}"
            self.post("call", lambda: messagebox.showwarning(
                APP_NAME, "The game isn't started in offline mode.\n\nClick “Start game offline”, wait for the "
                          "title screen, then click Find key again."))
            raise RuntimeError("Key not found — the game isn't running in offline mode.")

        self._start(work, "Finding key…")

    def _after_key(self):
        if messagebox.askyesno(APP_NAME, "Key found and saved.\n\nClose the game, then click Yes to remove the "
                                         "offline launch option so it starts normally again.\n\n(You can also do it "
                                         "later in Settings.)"):
            self.remove_offline()

    def fetch_tools(self):
        """Settings → Check for updates: install retoc / repak when GitHub has a newer release (or they're missing)."""
        if not UNPACKER_DIR:
            return
        import ac8_tools
        tools = UNPACKER_DIR / "tools"

        def work():
            self.post("step", "Checking GitHub for new versions of retoc and repak…")
            done, current = [], []
            for name, (have, ver, url, err) in ac8_tools.check(tools).items():
                if err:
                    raise RuntimeError(f"Couldn't check for tool updates: {err}.")
                if have is None or not have or ac8_tools.newer(ver, have):
                    self.post("step", f"Updating {name} to {ver}…")
                    ac8_tools.install(tools, name, ver, url, log=lambda t: self.post("log", t))
                    done.append(f"{name} {have or '?'} → {ver}" if have is not None else f"{name} {ver}")
                else:
                    current.append(f"{name} {have}")
            if not ac8_tools.has_oodle(tools):
                self.post("step", "Getting the Oodle decompressor…")
                try:
                    ac8_tools.install_oodle(tools, log=lambda t: self.post("log", t))
                    done.append("oodle")
                except Exception as e:
                    self.post("log", f"  Oodle: {ac8_tools.friendly_error(e)} — retoc will try again itself when "
                                     "it needs it")
            self.post("call", self.refresh_status)
            if done:
                return "✔ Updated: " + ", ".join(done) + (f"  (up to date: {', '.join(current)})" if current else "")
            return "✔ Tools are up to date: " + ", ".join(current)

        self._start(work, "Checking for tool updates…")

    def _tools_if_missing(self):
        """First start from a fresh download: fetch retoc / repak when they aren't there yet."""
        tools = UNPACKER_DIR / "tools" if UNPACKER_DIR else None
        if tools is None or not IS_WIN:
            return
        missing = [f"{t}.exe" for t in ("retoc", "repak") if not (tools / f"{t}.exe").exists()]
        if not (tools / "oo2core_9_win64.dll").exists():
            missing.append("the Oodle decompressor")
        if missing:
            self._log(f"Getting {', '.join(missing)} from GitHub…")
            self.fetch_tools()

    def update_app(self):
        """Settings → APP → Check for updates: install the newest release from GitHub, then restart."""
        import ac8_update

        def work():
            self.post("step", "Checking GitHub for a new version of the app…")
            try:
                info = ac8_update.latest()
            except Exception as e:
                import ac8_tools
                raise RuntimeError(f"Couldn't check for updates: {ac8_tools.friendly_error(e)}.")
            if not ac8_update.newer(info["version"], APP_VERSION):
                return f"✔ You have the latest version (v{APP_VERSION})"
            box, ev = {}, threading.Event()

            def ask():
                notes = info["notes"].strip()
                box["ok"] = messagebox.askyesno(
                    APP_NAME, f"Version {info['version']} is available (you have {APP_VERSION}).\n\n"
                              + (notes[:900] + ("…" if len(notes) > 900 else "") + "\n\n" if notes else "")
                              + "Update now? Your settings, exports and asset list are kept. The app restarts "
                                "afterwards.")
                ev.set()

            self.post("call", ask)
            ev.wait()
            if not box.get("ok"):
                return f"Update to {info['version']} skipped"
            self.post("step", f"Updating to {info['version']}…")
            ac8_update.install(HERE, info["url"], log=lambda t: self.post("log", t), version=info["version"])
            self.post("call", lambda: self.after(800, self._restart))
            return f"✔ Updated to v{info['version']} — restarting…"

        self._start(work, "Checking for app updates…")

    def _restart(self):
        self._save_settings()
        launcher = HERE / "AC8 Asset Extractor.exe"
        try:
            if IS_WIN and launcher.exists():
                subprocess.Popen([str(launcher)], cwd=str(HERE))
            else:
                subprocess.Popen([sys.executable, str(Path(__file__).resolve())], cwd=str(HERE))
        except OSError as e:
            messagebox.showinfo(APP_NAME, f"Updated. Please start the app again.\n\n({e})")
        self._on_close()

    def _startup_update_check(self):
        """Every time the app starts, quietly: is there a newer app, retoc or repak? Shown in a bar (and the log)."""
        import ac8_tools
        tools = UNPACKER_DIR / "tools" if UNPACKER_DIR else None

        def run():
            app_new, tool_new = None, []
            try:
                import ac8_update
                info = ac8_update.latest()
                if ac8_update.newer(info["version"], APP_VERSION):
                    app_new = info["version"]
            except Exception:
                pass
            if tools is not None:
                try:
                    for name, (have, ver, _url, err) in ac8_tools.check(tools).items():
                        if not err and have and ac8_tools.newer(ver, have):
                            tool_new.append(f"{name} {ver} (you have {have})")
                except Exception:
                    pass
            if app_new or tool_new:
                self.post("call", lambda: self._show_updates(app_new, tool_new))

        threading.Thread(target=run, daemon=True).start()

    def _show_updates(self, app_new, tool_new):
        parts = ([f"App v{app_new} (you have v{APP_VERSION})"] if app_new else []) + tool_new
        self.v_upd.set("Updates available: " + " · ".join(parts))
        for b in (self.upd_btn_app, self.upd_btn_tools):
            b.pack_forget()
        if tool_new:
            self.upd_btn_tools.pack(side="right", padx=(0, 6), pady=5)
        if app_new:
            self.upd_btn_app.pack(side="right", padx=(0, 6), pady=5)
        self.upd_bar.pack(fill="x", padx=18, pady=(12, 0), before=self.body)
        for p_ in parts:
            self.post("log", f"Update available: {p_}")

    def install_packages(self):
        pkgs = list(dict.fromkeys(PACKAGES.values()))
        optional = list(dict.fromkeys(OPTIONAL_PACKAGES.values()))

        def work():
            self.post("step", "Installing Python packages…")
            code, _ = self.run_cmd([console_python(), "-m", "pip", "install", "--upgrade", *pkgs], HERE)
            if code:
                raise RuntimeError("pip failed. See the log.")
            # optional extras one at a time, so one that can't install doesn't block the rest
            for pkg in optional:
                self.post("step", f"Installing {pkg} (optional)…")
                code, _ = self.run_cmd([console_python(), "-m", "pip", "install", pkg], HERE)
                if code:
                    self.post("log", f"  {pkg} couldn't be installed on this Python (everything else works).")
            importlib.invalidate_caches()
            return "✔ Packages installed"

        self._start(work, "Installing…")

    def _on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(APP_NAME, "Something is still running. Stop it and quit?"):
                return
            self.stop()
        self._save_settings()
        for v in self.viewers:
            try:
                v.terminate()
            except Exception:
                pass
        if self.viewer_srv:
            self.viewer_srv.shutdown()
        if not self.v_keep_previews.get():
            self._clear_scratch()
        self.destroy()

    # ================================================================== tabs

    def show_tab(self, key):
        if key != "viewer" and self.glv is not None:
            self.glv.set_fly(False)
        panes = {"assets": self.pane_assets, "catalog": self.pane_catalog, "textures": self.pane_textures,
                 "viewer": self.pane_viewer, "dcs": self.pane_dcs}
        new = panes[key]
        want_extr = key != "viewer"  # the viewer gets the whole width
        if new is not self._left_pane or want_extr != self._extr_shown:
            if self._left_pane is not self.pane_viewer:
                w = self._left_pane.winfo_width()
                self._left_width = w if w > 50 else getattr(self, "_left_width", 0)
            self.body.forget(self._left_pane)
            if self._extr_shown:
                self.body.forget(self.pane_extr)
                self._extr_shown = False
            self.body.add(new, minsize=520, stretch="always")
            if want_extr:
                self.body.add(self.pane_extr, minsize=440, width=round(560 * SCALE))
                self._extr_shown = True
                if getattr(self, "_left_width", 0) > 50:
                    self.body.paneconfigure(new, width=self._left_width)
            self._left_pane = new
        self.tab = key
        for k, lbl in self.tab_labels.items():
            lbl.configure(fg=C["accent"] if k == key else C["text3"])
        self.after_idle(self._unstick_sash_cursor)
        if key == "catalog":
            self.refresh_catalog()
        elif key == "textures":
            self._fill_tex_sources()
            self.load_textures()
        elif key == "dcs":
            self._dcs_fill_sources()

    def _scrollable(self, parent, bg):
        canvas = tk.Canvas(parent, bg=bg, highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview, style="Dark.Vertical.TScrollbar")
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(canvas, bg=bg)
        win = canvas.create_window(0, 0, window=inner, anchor="nw")

        def region(_e=None):
            # never smaller than the view: a short page then can't be scrolled (it used to slide down the window)
            canvas.configure(scrollregion=(0, 0, canvas.winfo_width(),
                                           max(inner.winfo_reqheight(), canvas.winfo_height())))
        inner.bind("<Configure>", region)
        canvas.bind("<Configure>", lambda e: (canvas.itemconfigure(win, width=e.width), region()))
        self.scroll_canvases.append(canvas)
        return canvas, inner

    # ================================================================== catalog

    def _build_catalog(self):
        frame, box = self._panel("CATALOG", self.v_cat_count)
        self.pane_catalog = frame
        # draggable divider between the list and the page (long character names need the room)
        split = tk.PanedWindow(box, orient="horizontal", bg=C["border2"], sashwidth=6, bd=0, sashrelief="flat",
                               showhandle=False, sashcursor="sb_h_double_arrow")
        split.pack(fill="both", expand=True)
        self.cat_split = split
        # every part of the catalog has a cursor of its own (an arrow), so none of it can show the divider's
        # resize arrows: on Windows a widget without one borrows the divider's while it is being hovered
        left = tk.Frame(split, bg=C["bg1"], cursor=ARROW)
        split.add(left, minsize=round(220 * SCALE), width=self.settings_cat_width or round(380 * SCALE))
        sbox = tk.Frame(left, bg=C["bg1"], padx=8, pady=8)
        sbox.pack(fill="x")
        Input(sbox, self.v_cat_search, icon="⌕", placeholder="Filter the catalog…").pack(fill="x")
        self.v_cat_search.trace_add("write", lambda *_: self.refresh_catalog())
        tframe = tk.Frame(left, bg=C["bg1"], cursor=ARROW)
        tframe.pack(fill="both", expand=True)
        self.cat_tree = ttk.Treeview(tframe, style="Dark.Treeview", show="tree", selectmode="browse", cursor=ARROW)
        sb = ttk.Scrollbar(tframe, orient="vertical", command=self.cat_tree.yview, style="Dark.Vertical.TScrollbar",
                           cursor=ARROW)
        self.cat_tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.cat_tree.pack(fill="both", expand=True)
        self.cat_tree.tag_configure("group", font=F["bold"], foreground=C["text2"])
        self.cat_tree.bind("<<TreeviewSelect>>", lambda e: self._cat_select())
        right = tk.Frame(split, bg=C["bg1"], cursor=ARROW)
        split.add(right, minsize=round(360 * SCALE), stretch="always")
        # fixed action bar for the picked entry (title left, buttons right) — stays put while the list scrolls
        self.cat_head = tk.Frame(right, bg=C["bg1"])
        self.cat_head.pack(fill="x")
        self.cat_head_line = tk.Frame(right, bg=C["border2"], height=1)
        self.cat_canvas, self.cat_detail = self._scrollable(right, C["bg1"])
        self._cat_open = set()      # groups the user had open (kept across tab switches and refreshes)
        self._cat_last_q = ""
        self._cat_shown = None      # tree item whose page is on the right

    def _cat_list_width(self):
        try:
            w = self.cat_split.panes()[0]
            return int(self.nametowidget(w).winfo_width()) if self.nametowidget(w).winfo_ismapped() else \
                self.settings_cat_width
        except Exception:
            return self.settings_cat_width

    def refresh_catalog(self):
        if not len(self.index):
            self.cat_tree.delete(*self.cat_tree.get_children())
            self._cat_message("No asset list yet", "Use Scan Game or Import List… on the Assets tab first.")
            return
        if self.cat_data is None:
            self.cat_data = catalog.build(k for k, _kl, _t in self.index.files)
        q = self.v_cat_search.get().lower().strip()
        t = self.cat_tree
        sel = t.selection()
        top = t.yview()[0]
        if not self._cat_last_q:   # remember what was open in the unfiltered list

            def opened(parent=""):
                for c in t.get_children(parent):
                    if t.item(c, "open"):
                        yield c
                        yield from opened(c)
            self._cat_open = set(opened())
        self._cat_last_q = q
        t.delete(*t.get_children())
        ac = self.cat_data["aircraft"]

        def match(*texts):
            return not q or any(q in x.lower() for x in texts if x)

        groups = [("PP", "Player aircraft"), ("OP", "Enemy & other aircraft")]
        total = 0
        for side, title in groups:
            items = sorted((k, a) for k, a in ac.items()
                           if a["side"] == side and match(a["name"], a["folder"], "online" if a.get("online") else ""))
            if not items:
                continue
            g = t.insert("", "end", iid=f"g:{side}", text=f"  {title}  ({len(items)})", open=bool(q),
                         tags=("group",))
            for k, a in sorted(items, key=lambda kv: kv[1]["folder"]):
                tag = "  · online" if a.get("online") else ""
                badge = "   • NEW" if self._new_in(a["keys"]) else ""
                t.insert(g, "end", iid=f"a:{k}", text=f"  {a['name']}   ·  {a['folder'].split('_')[0]}{tag}{badge}")
                total += 1
        weapons = self.cat_data["weapons"]
        bycat = {}
        for code, w in weapons.items():
            if match(w["name"], code, w["category"]):
                bycat.setdefault(w["category"], []).append(w)
        if bycat:
            g = t.insert("", "end", iid="g:W", text=f"  Weapons  ({sum(len(v) for v in bycat.values())})",
                         open=bool(q), tags=("group",))
            for catname in catalog.WEAPON_CATEGORY_ORDER:
                if catname not in bycat:
                    continue
                cg = t.insert(g, "end", iid=f"wc:{catname}", text=f"  {catname}", open=bool(q))
                for w in sorted(bycat[catname], key=lambda w: w["name"]):
                    t.insert(cg, "end", iid=f"w:{w['code']}", text=f"  {w['name']}")
                    total += 1
        lau = {n: v for n, v in self.cat_data["launchers"].items() if match(n)}
        if lau:
            g = t.insert("", "end", iid="g:L", text=f"  Launchers & racks  ({len(lau)})", open=False,
                         tags=("group",))
            for n in sorted(lau):
                t.insert(g, "end", iid=f"l:{n}", text=f"  {n}")
        cols = self.cat_data["collections"]
        n_items = 0
        for gname in catalog.GROUP_ORDER:
            items = [(k, it) for k, it in cols.get(gname, {}).items() if match(it["title"], k, gname)]
            if not items:
                continue
            nm = sum(len(it["meshes"]) for _k, it in items)
            fonts = gname == "Fonts"
            g = t.insert("", "end", iid=f"g:c:{gname}", tags=("group",), open=bool(q) and len(items) < 60,
                         text=f"  {gname}  ({len(items)}" + (")" if fonts else f" · {fmt(nm)} meshes)"))
            for k, it in sorted(items, key=lambda kv: kv[1].get("sort") or kv[1]["title"].lower()):
                badge = "   • NEW" if self._new_in(it["keys"]) else ""
                what = f"{len(it['keys'])} files" if fonts else \
                    f"{len(it['meshes'])} mesh{'es' if len(it['meshes']) != 1 else ''}"
                t.insert(g, "end", iid=f"c:{gname}|{k}", text=f"  {it['title']}   ·  {what}" + badge)
                n_items += 1
        self._fill_new_group(match)
        if not q:
            for iid in self._cat_open:
                if t.exists(iid):
                    t.item(iid, open=True)
            self.after_idle(lambda: t.yview_moveto(top))
        self.v_cat_count.set(f"{len(ac)} aircraft · {len(weapons)} weapons · {n_items} other items")
        if sel and t.exists(sel[0]):
            t.selection_set(sel[0])
        elif not self.cat_detail.winfo_children():
            self._cat_message("Pick an aircraft or weapon", "Its parts are listed here, sorted by what they are, "
                                                            "with Preview and Select buttons.")

    def _cat_clear_head(self):
        for w in self.cat_head.winfo_children():
            w.destroy()
        self.cat_head_line.pack_forget()

    def _cat_message(self, title, text):
        self._cat_clear_head()
        for w in self.cat_detail.winfo_children():
            w.destroy()
        tk.Label(self.cat_detail, text=title, bg=C["bg1"], fg=C["text1"], font=F["h"]).pack(pady=(40, 4))
        tk.Label(self.cat_detail, text=text, bg=C["bg1"], fg=C["text3"], font=F["base"], wraplength=340,
                 justify="center").pack()

    def _cat_header(self, title, subtitle, buttons):
        self._cat_clear_head()
        for w in self.cat_detail.winfo_children():
            w.destroy()
        bar = tk.Frame(self.cat_head, bg=C["bg1"], padx=16, pady=10)
        bar.pack(fill="x")
        top = tk.Frame(bar, bg=C["bg1"])
        top.pack(fill="x")
        tk.Label(top, text=title, bg=C["bg1"], fg=C["text1"], font=F["title"], anchor="w").pack(side="left")
        tk.Label(top, text=subtitle, bg=C["bg1"], fg=C["text3"], font=F["mono_s"], anchor="w").pack(
            side="left", padx=(14, 0), pady=(4, 0))
        btns = tk.Frame(bar, bg=C["bg1"])
        btns.pack(fill="x", pady=(8, 0))
        self._cat_extract_btn = None
        for text, cmd, variant in buttons:
            b = Button(btns, text, cmd, variant=variant, font=F["small"], padx=10, pady=4)
            b.pack(side="left", padx=(0, 6))
            if text == "Extract now":
                self._cat_extract_btn = b
        self._cat_paint_rows()
        self.cat_head_line.pack(fill="x", after=self.cat_head)
        return bar

    def _cat_section(self, title, note=""):
        f = tk.Frame(self.cat_detail, bg=C["bg1"], padx=16)
        f.pack(fill="x", pady=(10, 0))
        tk.Label(f, text=title.upper(), bg=C["bg1"], fg=C["accent"], font=F["section"], anchor="w").pack(fill="x")
        if note:
            tk.Label(f, text=note, bg=C["bg1"], fg=C["text3"], font=F["small"], anchor="w", justify="left",
                     wraplength=round(520 * SCALE)).pack(fill="x")
        return f

    def _cat_row(self, parent, label, key, mesh=True):
        r = tk.Frame(parent, bg=C["bg2"], highlightthickness=1, highlightbackground=C["border1"], cursor="hand2")
        r.pack(fill="x", pady=(4, 0))
        box = tk.Canvas(r, width=round(16 * SCALE), height=round(16 * SCALE), bg=C["bg2"], highlightthickness=0,
                        bd=0, cursor="hand2")
        box.pack(side="left", padx=(10, 0))
        labels = [tk.Label(r, text=label, bg=C["bg2"], fg=C["text1"], font=F["base"], anchor="w", padx=8, pady=5)]
        labels[0].pack(side="left")
        stem = key.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if not label.startswith(stem):
            labels.append(tk.Label(r, text=stem, bg=C["bg2"], fg=C["text3"], font=F["mono_s"], anchor="w"))
            labels[-1].pack(side="left", padx=(6, 0))
        is_mesh = stem.startswith(("SK_", "SM_"))
        small = dict(font=F["small"], padx=8, pady=1)
        btn = Button(r, "Select", lambda k=key: self._cat_toggle(k, is_mesh), **small)
        btn.pack(side="right", padx=(0, 6), pady=3)
        if mesh:
            Button(r, "Preview", lambda k=key, l=label: self.preview_meshes([k], l), **small).pack(
                side="right", padx=(0, 6), pady=3)
        self._cat_register(r, box, labels, btn, [key], lambda k=key: self._cat_toggle(k, is_mesh))

    # --- selected rows on catalog pages: an orange outline + tick, and the textures a model uses
    def _cat_register(self, row, box, labels, btn, keys, toggle):
        for w in [row, box] + labels:
            w.bind("<Button-1>", lambda e: toggle())
        self._cat_rows.append((row, box, labels, btn, keys))
        self._cat_paint_row(self._cat_rows[-1])

    def _cat_paint_row(self, entry):
        row, box, labels, btn, keys = entry
        if not row.winfo_exists():
            return
        on = all(self._state(k) == "on" for k in keys)
        bg = C["accent_tint"] if on else C["bg2"]
        row.configure(bg=bg, highlightbackground=C["accent"] if on else C["border1"])
        for w in labels:
            w.configure(bg=bg)
        box.configure(bg=bg)
        box.delete("all")
        s = int(box["width"])
        if on:
            box.create_rectangle(1, 1, s - 1, s - 1, fill=C["accent"], outline=C["accent"])
            box.create_line(s * .25, s * .5, s * .44, s * .69, s * .75, s * .31, fill="#fff", width=max(2, s // 8),
                            capstyle="round", joinstyle="round")
        else:
            box.create_rectangle(1, 1, s - 1, s - 1, fill=C["bg2"], outline=C["border3"])
        btn.variant = "danger" if on else "default"
        btn.set_text("✓ Selected" if on else "Select")
        btn._paint()

    def _cat_paint_rows(self):
        self._cat_rows = [e for e in self._cat_rows if e[0].winfo_exists()]
        for e in self._cat_rows:
            self._cat_paint_row(e)
        b = getattr(self, "_cat_extract_btn", None)
        if b is not None and b.winfo_exists():  # with something selected it extracts just that
            b.set_text("Extract selected" if self.checked else "Extract now")

    def _cat_extract(self, whole):
        """The page's Extract button: what's selected, or (with nothing selected) the whole entry."""
        if not self.checked:
            self._tick_keys(whole, True)
        self.extract(all_types=True)

    def _cat_toggle(self, key, is_mesh=False):
        """Select / deselect a catalog row. A model brings the materials and textures it uses along
        (found by reading the mesh, once, then remembered)."""
        deps = self._mesh_deps.get(key, []) if is_mesh else []
        if self._state(key) == "on":
            for k in [key] + deps:
                if self._state(k) == "on":
                    self.toggle(k, refresh=False)
            self._dep_keys -= set(deps)
            self._refresh_boxes()
            self.v_step.set(f"Deselected {key.rsplit('/', 1)[-1].rsplit('.', 1)[0]} — {self.v_sel.get()}")
            return
        self._dep_keys |= {d for d in deps if self._state(d) != "on"}
        self._tick_keys([key] + deps, quiet=True)
        name = key.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if is_mesh and key not in self._mesh_deps:
            self._find_mesh_deps([key])
        else:
            extra = f" + {len(deps)} material(s) / texture(s) it uses" if deps else ""
            self.v_step.set(f"Selected {name}{extra} — {self.v_sel.get()}")

    def _find_mesh_deps(self, mesh_keys):
        """Read which materials and textures these meshes use, then select those too."""
        if (self.worker and self.worker.is_alive()) or not self._paks_dir() or \
                not KEY_RE.match(self.v_key.get().strip()):
            self.v_step.set(f"Selected {len(mesh_keys)} model(s) — their textures are added when extracting")
            return
        key, paks = self.v_key.get().strip(), self._paks_dir()
        tmp = self._preview_root() / "_selection_lookup"
        want = {"MaterialInstanceConstant", "Material", "Texture2D", "TextureCube"}

        def work():
            names = ", ".join(k.rsplit("/", 1)[-1].rsplit(".", 1)[0] for k in mesh_keys[:3])
            self.post("step", f"Finding the textures {names} uses…")
            found = {}
            try:
                for mk in mesh_keys:
                    found[mk] = sorted(k for k in self._unpack_with_deps(key, paks, tmp, [mk], want)
                                       if not k.startswith("["))
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            self.post("call", lambda: self._got_mesh_deps(found))
            n = len({k for v in found.values() for k in v})
            return f"✔ Selected {names} + {n} material(s) / texture(s) it uses"

        self._start(work, "Finding the textures the model uses…")

    def _got_mesh_deps(self, found):
        self._mesh_deps.update(found)
        try:
            DEPS_FILE.write_text(json.dumps({"index": len(self.index), "deps": self._mesh_deps}))
        except OSError:
            pass
        add = [k for mk, ks in found.items() if self._state(mk) == "on" for k in ks if self._state(k) != "on"]
        self._dep_keys |= set(add)
        if add:
            self._tick_keys(add, quiet=True)

    def _tick_keys(self, keys, quiet=False):
        for k in keys:
            if self._state(k) != "on":
                pre = k + "/"
                self.checked = {c for c in self.checked if not c.startswith(pre)}
                self.checked.add(k)
        self._refresh_boxes()
        if not quiet:
            self.v_step.set(f"Selected {len(keys)} item(s) — {self.v_sel.get()}")

    def _cat_select(self):
        sel = self.cat_tree.selection()
        if not sel:
            return
        iid = sel[0]
        if iid == self._cat_shown and self.cat_detail.winfo_children():
            return   # same entry re-selected after a refresh: keep the page (and where it was scrolled to)
        self._cat_shown = iid
        self.cat_canvas.yview_moveto(0)
        for ms in (0, 60, 250):   # again once the new page has been laid out (its height changes the scroll range)
            self.after(ms, lambda: self.cat_canvas.yview_moveto(0))
        kind, _, key = iid.partition(":")
        if kind == "a":
            self._show_aircraft(key)
        elif kind == "w":
            self._show_weapon(key)
        elif kind == "l":
            self._show_launcher(key)
        elif kind == "c":
            gname, _, ikey = key.partition("|")
            self._show_collection(gname, ikey)
        elif kind == "u":
            self._show_update(int(key))

    def _show_aircraft(self, fkey):
        a = self.cat_data["aircraft"][fkey]
        meshes = a["meshes"]
        main = next((k for k, sec, _l in meshes if sec == "Airframe"), meshes[0][0] if meshes else None)
        btns = [("Select whole aircraft", lambda: self._tick_keys([fkey]), "default"),
                ("Extract now", lambda: self._cat_extract([fkey]), "primary")]
        if main:
            fittings = [k for k, sec, _l in meshes if sec in ("Landing gear", "Refuel probe", "Special weapon pylons")
                        and k.rsplit("/", 1)[-1].startswith("SM_")]
            btns.insert(0, ("Preview airframe", lambda: self.preview_meshes([main] + fittings, a["name"]),
                            "default"))
        if meshes:
            btns.insert(1, ("Preview all parts", lambda: self.preview_meshes([k for k, *_ in meshes],
                                                                             a["name"] + " (all parts)"), "default"))
        if a["side"] == "PP":
            btns.append(("Find weapons (beta)", lambda: self.find_weapons(fkey), "default"))
        self._cat_header(a["name"], f"{fkey}   ·   {a['count']} files   ·   {len(meshes)} meshes   ·   "
                                    f"{len(a['textures'])} textures", btns)
        by = {}
        for k, sec, lab in meshes:
            by.setdefault(sec, []).append((lab, k))
        notes = {"Special weapon pylons": "Pylons, racks and launchers for each special-weapon loadout (the "
                                          "launcher geometry is part of these meshes).",
                 "Damage models": "Used when the aircraft is hit or shot down."}
        for sec in catalog.PART_SECTIONS:
            if sec in by:
                f = self._cat_section(f"{sec}  ({len(by[sec])})", notes.get(sec, ""))
                for lab, k in sorted(by[sec]):
                    self._cat_row(f, lab, k)
        if a["liveries"]:
            f = self._cat_section(f"Liveries / paint schemes  ({len(a['liveries'])})",
                                  "Texture folders per paint scheme. 001 is usually the default.")
            for liv in sorted(a["liveries"]):
                key = f"{fkey}/Textures/{liv}"
                if self.index.nodes.get(key):
                    self._cat_row(f, f"Livery {liv}", key, mesh=False)
        tk.Frame(self.cat_detail, bg=C["bg1"], height=16).pack()

    def _show_weapon(self, code):
        w = self.cat_data["weapons"][code]
        allm = [m for v in w["variants"].values() for m in v["meshes"]]
        self._cat_header(w["name"], f"{w['category']}   ·   code {code}   ·   {len(w['variants'])} variant(s)",
                         [("Preview all variants", lambda: self.preview_meshes(allm, w["name"]), "default"),
                          ("Select all variants", lambda: self._tick_keys([v["folder"] for v in w["variants"].values()]),
                           "default")])
        f = self._cat_section("Variants", "The letter after the code is the variant (a0, r0, f0, j0, x0 …), "
                                          "usually a different country's version of the weapon.")
        for vname, v in sorted(w["variants"].items()):
            for m in v["meshes"]:
                self._cat_row(f, vname, m)
            if not v["meshes"]:
                self._cat_row(f, vname + " (no model)", v["folder"], mesh=False)

    def _show_launcher(self, name):
        v = self.cat_data["launchers"][name]
        self._cat_header(name, v["folder"], [("Select", lambda: self._tick_keys([v["folder"]]), "default")])
        self._cat_section("About", "Launchers and racks only have textures and materials here; their geometry is "
                                   "built into each aircraft's special weapon pylon meshes.")

    # ---------------------------------------------------------- game updates / other areas
    def _latest_new(self):
        ups = self.history.get("updates", [])
        if not ups:
            return set()
        if getattr(self, "_latest_new_cache", (None,))[0] is not ups[-1]:
            self._latest_new_cache = (ups[-1], set(ups[-1]["added"]))
        return self._latest_new_cache[1]

    def _new_in(self, keys):
        new = self._latest_new()
        return bool(new) and any(k in new for k in keys)

    def _fill_new_group(self, match):
        ups = self.history.get("updates", [])
        if not ups:
            return
        t = self.cat_tree
        g = t.insert("", 0, iid="g:new", text=f"  New in game updates  ({len(ups)})", open=False, tags=("group",))
        for i in range(len(ups) - 1, -1, -1):
            u = ups[i]
            label = f"  {u['date']}   ·  {fmt(len(u['added']))} new file(s)"
            if match(label, *u["added"][:2000]):
                t.insert(g, "end", iid=f"u:{i}", text=label + ("   • latest" if i == len(ups) - 1 else ""))

    def _show_update(self, i):
        u = self.history["updates"][i]
        added = [k for k in u["added"] if self.index.find(k)[0] is not None] if len(self.index) else u["added"]
        meshes = [k for k in added if k.rsplit("/", 1)[-1].startswith(("SM_", "SK_"))]
        by = {}
        for k in added:
            by.setdefault(k.rsplit("/", 1)[0], []).append(k)
        btns = [("Select all new files", lambda: self._tick_keys(added), "default")]
        if meshes:
            btns.insert(0, ("Preview new meshes" + (" (first 30)" if len(meshes) > 30 else ""),
                            lambda: self.preview_meshes(meshes[:30], f"New in update {u['date']}"), "default"))
        self._cat_header(f"Game update  ·  {u['date']}",
                         f"{fmt(len(added))} new file(s) in {len(by)} folder(s)   ·   {len(meshes)} meshes"
                         + (f"   ·   {fmt(u.get('removed', 0))} removed" if u.get("removed") else ""), btns)
        # which catalog entries got new content
        hits = []
        new = set(added)
        for k, a in self.cat_data["aircraft"].items():
            if any(x in new for x in a["keys"]):
                hits.append((f"Aircraft: {a['name']}", f"a:{k}"))
        for gname, items in self.cat_data["collections"].items():
            for k, it in items.items():
                if any(x in new for x in it["keys"]):
                    hits.append((f"{gname}: {it['title']}", f"c:{gname}|{k}"))
        if hits:
            f = self._cat_section(f"Catalog entries with new content  ({len(hits)})")
            for label, iid in sorted(hits)[:200]:
                r = tk.Frame(f, bg=C["bg2"], highlightthickness=1, highlightbackground=C["border1"])
                r.pack(fill="x", pady=(4, 0))
                tk.Label(r, text=label, bg=C["bg2"], fg=C["text1"], font=F["base"], anchor="w", padx=10,
                         pady=5).pack(side="left")
                Button(r, "Open", lambda x=iid: self._cat_goto(x), font=F["small"], padx=8, pady=1).pack(
                    side="right", padx=6, pady=3)
        f = self._cat_section(f"New files by folder  ({len(by)})", "Select a folder to extract just the new files.")
        for folder in sorted(by)[:300]:
            self._cat_folder_row(f, folder, by[folder])
        if len(by) > 300:
            tk.Label(f, text=f"… and {len(by) - 300} more folders (use Select all new files)", bg=C["bg1"],
                     fg=C["text3"], font=F["small"]).pack(anchor="w")

    def _cat_goto(self, iid):
        if self.cat_tree.exists(iid):
            parent = self.cat_tree.parent(iid)
            while parent:
                self.cat_tree.item(parent, open=True)
                parent = self.cat_tree.parent(parent)
            self.cat_tree.selection_set(iid)
            self.cat_tree.see(iid)

    def _cat_folder_row(self, parent, folder, keys):
        r = tk.Frame(parent, bg=C["bg2"], highlightthickness=1, highlightbackground=C["border1"])
        r.pack(fill="x", pady=(4, 0))
        meshes = [k for k in keys if k.rsplit("/", 1)[-1].startswith(("SM_", "SK_"))]
        tk.Label(r, text=folder, bg=C["bg2"], fg=C["text1"], font=F["mono_s"], anchor="w", padx=10,
                 pady=5).pack(side="left")
        tk.Label(r, text=f"{len(keys)} file(s)" + (f" · {len(meshes)} mesh(es)" if meshes else ""), bg=C["bg2"],
                 fg=C["text3"], font=F["small"]).pack(side="left", padx=(6, 0))
        small = dict(font=F["small"], padx=8, pady=1)
        box = tk.Canvas(r, width=round(16 * SCALE), height=round(16 * SCALE), bg=C["bg2"], highlightthickness=0,
                        bd=0, cursor="hand2")
        box.pack(side="left", padx=(10, 0), before=r.winfo_children()[0])

        def toggle():
            if all(self._state(k) == "on" for k in keys):
                for k in keys:
                    if self._state(k) == "on":
                        self.toggle(k, refresh=False)
                self._refresh_boxes()
            else:
                self._tick_keys(keys)
        btn = Button(r, "Select", toggle, **small)
        btn.pack(side="right", padx=(0, 6), pady=3)
        if meshes:
            Button(r, "Preview", lambda: self.preview_meshes(meshes[:30], folder.rsplit("/", 1)[-1]), **small).pack(
                side="right", padx=(0, 6), pady=3)
        self._cat_register(r, box, [w for w in r.winfo_children() if isinstance(w, tk.Label)], btn, keys, toggle)

    def _show_collection(self, gname, ikey):
        it = self.cat_data["collections"][gname][ikey]
        meshes = sorted(it["meshes"])
        new = self._latest_new()
        btns = [("Select all", lambda: self._tick_keys([ikey] if self.index.nodes.get(ikey) else it["keys"]),
                 "default"),
                ("Extract now", lambda: self._cat_extract([ikey] if self.index.nodes.get(ikey) else it["keys"]),
                 "primary")]
        if meshes:
            btns.insert(0, ("Preview " + (f"first 30 of {len(meshes)} meshes" if len(meshes) > 30 else
                                          f"all {len(meshes)} mesh(es)"),
                            lambda: self.preview_meshes(meshes[:30], it["title"]), "default"))
            if gname.startswith("Bosses") and len(meshes) > 1:  # one vehicle built from many meshes
                btns.insert(0, ("Preview whole vehicle",
                                lambda: self.preview_meshes(meshes, it["title"], together=True), "default"))
        types = {}
        for k in it["keys"]:
            t_ = asset_type(k.rsplit("/", 1)[-1])
            types[t_] = types.get(t_, 0) + 1
        tlabel = dict(TYPES)
        summary = "   ·   ".join(f"{n} {tlabel.get(t_, t_).lower()}" for t_, n in
                                  sorted(types.items(), key=lambda kv: -kv[1])[:5])
        self._cat_header(f"{it['title']}", f"{gname}   ·   {ikey}   ·   {it['count']} files", btns)
        tk.Label(self.cat_detail, text=summary, bg=C["bg1"], fg=C["text3"], font=F["small"], anchor="w",
                 padx=16).pack(fill="x")
        if gname == "Fonts":
            f = self._cat_section(f"Fonts and font sets  ({len(it['keys'])})",
                                  "Extract to get each one as its .ttf / .otf file.\n"
                                  "UI_FontSet_… only point at the other fonts.")
            for k in sorted(it["keys"], key=lambda k_: k_.rsplit("/", 1)[-1].lower()):
                self._cat_row(f, k.rsplit("/", 1)[-1].rsplit(".", 1)[0] + ("   • NEW" if k in new else ""), k, mesh=False)
            tk.Frame(self.cat_detail, bg=C["bg1"], height=16).pack()
            return
        if not meshes:
            self._cat_section("No 3D models here", "This folder has no meshes — Select or Extract it to get its "
                                                    "files (textures come out as PNG, fonts as TTF / OTF).")
        by = {}
        # characters (incl. the online ones): by what the mesh is (outfits, faces, hair …), not by folder
        parts = gname == "Characters" or ikey.startswith("Character/")
        for k in meshes:
            if parts:
                by.setdefault(catalog.character_part(k.rsplit("/", 1)[-1].rsplit(".", 1)[0]), []).append(k)
                continue
            rel = k[len(ikey) + 1:] if k.startswith(ikey + "/") else k
            by.setdefault(rel.rsplit("/", 1)[0] if "/" in rel else "", []).append(k)
        order = catalog.CHARACTER_PART_ORDER
        shown = 0
        for sub in sorted(by, key=lambda x: (order.index(x) if parts and x in order else len(order), x)):
            if shown >= 400:
                tk.Label(self.cat_detail, text=f"… {len(meshes) - shown} more meshes — use Select all / the Assets tab.",
                         bg=C["bg1"], fg=C["text3"], font=F["small"], padx=16).pack(anchor="w", pady=6)
                break
            f = self._cat_section(f"{sub or 'Models'}  ({len(by[sub])})")
            for k in by[sub][:400 - shown]:
                stem = k.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                self._cat_row(f, stem + ("   • NEW" if k in new else ""), k)
                shown += 1
        tk.Frame(self.cat_detail, bg=C["bg1"], height=16).pack()

    def find_weapons(self, fkey):
        """Experimental: unpack this aircraft's data tables and look for weapon names in them."""
        folder = fkey.rsplit("/", 1)[-1]
        keys = [k for k, _kl, _t in self.index.files if k.startswith("Datatables/") and folder.lower() in k.lower()]
        if not keys:
            messagebox.showinfo(APP_NAME, "No data tables mention this aircraft.")
            return
        if not self._check_ready(need_convert=False):
            return
        key = self._check_key()
        if not key:
            return
        out = self._preview_root() / (folder + "_data")
        paks = self._paks_dir()

        def work():
            self.post("step", f"Reading {len(keys)} data table(s) for {folder}…")
            self._retoc(key, [full_path(k) for k in keys], out, paks)
            found, scanned = {}, 0
            from ac8_package import Package
            for f in out.rglob("*.uasset"):
                try:
                    pkg = Package(f)
                except Exception:
                    continue
                scanned += 1
                for tok in catalog.weapons_in_names(pkg.names):
                    found.setdefault(tok, set()).add(f.stem)
            self.post("call", lambda: self._show_found_weapons(fkey, found, scanned))
            return f"✔ Weapon lookup: {len(found)} weapon name(s) in {scanned} data table(s)"

        self._start(work, "Looking up weapons…")

    def _show_found_weapons(self, fkey, found, scanned):
        self._show_aircraft(fkey)
        f = self._cat_section("Weapons found in the game data (experimental)",
                              f"Searched {scanned} data table(s) for weapon names." + (
                                  "" if found else " None found — the link is probably stored as numbers, which "
                                                   "needs the game's type mappings (.usmap) to read."))
        weapons = self.cat_data["weapons"]
        for tok in sorted(found):
            code = tok.split("_")[1]
            w = weapons.get(code)
            v = w["variants"].get(tok) if w else None
            label = (w["name"] if w else code.upper()) + f"   ({tok})"
            if v and v["meshes"]:
                self._cat_row(f, label, v["meshes"][0])
            else:
                tk.Label(f, text=label + "  — no model in this game build", bg=C["bg1"], fg=C["text3"],
                         font=F["small"], anchor="w").pack(fill="x")

    # ================================================================== 3D preview / viewer

    def preview_selected(self):
        if not len(self.index):
            messagebox.showinfo(APP_NAME, "Load the asset list first (Scan Game or Import List…).")
            return
        keys = [k for k in self.tree.selection() if not k.startswith("\0")] or sorted(self.checked)
        if not keys:
            messagebox.showinfo(APP_NAME, "Click a mesh or folder in the browser (or select some), then press "
                                          "Preview 3D.")
            return
        meshes = []
        for k in keys:
            for a in self.index.assets_under(k):
                if asset_type(a.rsplit("/", 1)[-1]) in MESH_TYPES:
                    meshes.append(a)
        meshes = sorted(set(meshes))
        if not meshes:
            messagebox.showinfo(APP_NAME, "There are no static or skeletal meshes in what you picked.")
            return
        if len(meshes) > 40 and not messagebox.askyesno(
                APP_NAME, f"That's {len(meshes)} meshes — previewing them all takes a while.\n\nPreview anyway?"):
            return
        label = keys[0].rstrip("/").split("/")[-1].rsplit(".", 1)[0] + (f"+{len(keys) - 1}" if len(keys) > 1 else "")
        self.preview_meshes(meshes, label)

    def _retoc(self, key, filters, out, paks):
        out.mkdir(parents=True, exist_ok=True)
        for batch in batch_filters(filters):
            args = [retoc_exe(), "-a", key, "to-legacy", "--version", ENGINE, "--no-shaders"]
            for f in batch:
                args += ["-f", f]
            self.run_cmd(args + [paks, out], UNPACKER_DIR, log_lines=False, show_cmd=False)

    def _preview_root(self):
        return Path(self.v_out_root.get() or DEFAULT_OUT_ROOT) / PREVIEW_DIR_NAME

    def _clear_scratch(self, previews=True):
        """Delete the preview cache (and the folder the removed shader export used). -> MB freed."""
        root = Path(self.v_out_root.get() or DEFAULT_OUT_ROOT)
        freed = 0
        for d in ([root / PREVIEW_DIR_NAME] if previews else []) + [root / "_Shaders"]:
            if d.is_dir():
                freed += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
                shutil.rmtree(d, ignore_errors=True)
        return freed / 1e6

    def _unpack_with_deps(self, key, paks, raw, start_keys, want, rounds=4):
        """Unpack start_keys into raw and, round by round, what they import of the classes in `want`
        (mesh -> materials -> textures / parent materials -> their textures). Returns the display keys of
        everything they use (not the start keys themselves); packages already in raw are reused."""
        from ac8_package import Package
        done, found = set(), set()
        todo = [full_path(k) for k in start_keys]
        for round_ in range(rounds):
            todo = [t for t in todo if t not in done]
            if not todo:
                break
            self.post("status", f"round {round_ + 1}: {len(todo)} package(s)")
            missing = [t for t in todo if not (raw / t).exists()]
            if missing:
                self._retoc(key, missing, raw, paks)
            done.update(todo)
            nxt = set()
            for t in todo:
                f = raw / (t[:-len(".uasset")] + ".uasset") if t.endswith(".uasset") else raw / (t + ".uasset")
                if not f.exists():
                    continue
                try:
                    pkg = Package(f)
                except Exception:
                    continue
                for i, imp in enumerate(pkg.imports):
                    if imp["class"] not in want or imp["name"].startswith("Default__"):
                        continue
                    path = pkg.import_path(-i - 1)
                    if not path or not path[0].startswith("/"):
                        continue
                    pp = path[0]
                    if pp.startswith("/Game/"):
                        k = pp[len("/Game/"):] + ".uasset"
                    elif pp.startswith("/Engine/"):
                        k = "[Engine Content]/" + pp[len("/Engine/"):] + ".uasset"
                    else:
                        continue
                    node, fname = self.index.find(k)
                    if node is not None and fname is not None:
                        nxt.add(full_path(k))
                        found.add(k)
            todo = sorted(nxt)
        return found - set(start_keys)

    def preview_meshes(self, mesh_keys, label, together=False):
        """Unpack the meshes + their materials + textures into _Previews, convert to GLB, open the viewer
        (together: start on all the parts shown at once, e.g. a boss built from many meshes)."""
        mesh_keys = [k for k in mesh_keys if k]
        if not mesh_keys:
            return
        if not self._check_ready():
            return
        key = self._check_key()
        if not key:
            return
        root = self._preview_root() / safe_name(label)
        raw, glb = root / "raw", root / "glb"
        stems = [k.rsplit("/", 1)[-1].rsplit(".", 1)[0] for k in mesh_keys]
        stamp = root / "converter_version.txt"
        if glb.exists() and (not stamp.exists() or stamp.read_text().strip() != MESH_FORMAT):
            shutil.rmtree(glb, ignore_errors=True)  # made by an older converter: rebuild from the unpacked files
        have = {p.stem for p in glb.rglob("*.glb")} if glb.exists() else set()
        main = sorted(stems, key=lambda s_: (not s_.startswith("SK_"), len(s_)))[0]
        if all(s_ in have for s_ in stems):
            self.open_viewer([glb], main, together)
            return
        paks = self._paks_dir()
        size = self.v_max_tex.get() or "0"
        size = str(min(int(size) if size != "0" else 4096, 2048))  # previews: keep textures light
        want = {"MaterialInstanceConstant", "Material", "Texture2D", "TextureCube", "SkeletalMesh", "StaticMesh"}

        def work():
            self.post("step", f"Preview: unpacking {len(mesh_keys)} mesh(es)…")
            self._unpack_with_deps(key, paks, raw, mesh_keys, want)
            self.post("step", "Preview: converting to GLB…")
            code, _ = self.run_cmd([console_python(), "-u", MESH_CONVERTER, raw, glb, "--max-texture", size], HERE)
            if code != 0 or not any(glb.rglob("*.glb")):
                raise RuntimeError("Preview conversion failed. See the log.")
            stamp.write_text(MESH_FORMAT)
            if not self.v_keep_previews.get():  # the viewer only needs the GLBs; drop the unpacked game files now
                shutil.rmtree(raw, ignore_errors=True)
            self.post("call", lambda: self.open_viewer([glb], main, together))
            return f"✔ Preview ready: {label}"

        self._start(work, "Preparing preview…")

    def view_folder(self, folder):
        self.open_viewer([folder / FINAL_DIR_NAME / "Meshes"], None)

    ALL_PARTS = "*all parts*"  # Model dropdown value: every model in the list at once

    def open_viewer(self, paths, first=None, together=False):
        files = []
        for p in paths:
            p = Path(p)
            files += sorted(p.rglob("*.glb")) if p.is_dir() else [p]
        if not files:
            messagebox.showinfo(APP_NAME, "No GLB files to show here.")
            return
        self._view_files, self._view_first = files, first
        self._log(f"Viewer: {len(files)} model(s)" + (f", starting with {first}" if first else ""))
        pick = next((f for f in files if f.stem == first), files[0])
        opts = [(str(f), f.stem) for f in files]
        if len(files) > 1:
            opts.insert(0, (self.ALL_PARTS, f"All parts together ({len(self._together_files(files))})"))
        self.dd_view_model.set_options(opts)
        if together and len(files) > 1:
            pick = Path(self.ALL_PARTS)
        self.v_view_model.set(str(pick))
        self.show_tab("viewer")
        self._view_load(pick)

    TOGETHER_SKIP = re.compile(r"_(dam|alt|temp|gearoff|refuelon)(_|$)", re.I)

    def _together_files(self, files):
        """The models to show as one: damaged / alternate versions left out, and an SM_ copy of an SK_ mesh."""
        stems = {f.stem.lower() for f in files}
        out = [f for f in files if not self.TOGETHER_SKIP.search(f.stem)
               and not (f.stem.lower().startswith("sm_") and "sk_" + f.stem.lower()[3:] in stems)]
        return out or files

    # ---------------------------------------------------------- native viewer
    def _ensure_glv(self):
        if self.glv is not None or self._glv_failed:
            return self.glv
        try:
            import ac8_glview
        except Exception as e:
            self._view_fail(f"couldn't load the viewer ({e})")
            return None
        why = ac8_glview.available()
        if why:
            self._view_fail(why + " — open Settings and click Install packages")
            return None
        self.glv = ac8_glview.GLView(self.view_host, bg=C["bg0"], on_error=self._view_fail,
                                     on_ready=self._view_ready)
        self.glv.on_fly = self._view_fly_changed
        self.gizmo = ac8_glview.NavGizmo(self.glv, size=round(92 * SCALE), font_file=str(FONTS_DIR / "Outfit-Bold.ttf"),
                                         on_frame=lambda: self.glv and self.glv.frame(), on_hint=self._view_hint)
        # left side of the view: move / rotate bone tools (G / R)
        self.view_tools = ac8_glview.ToolBar(self.glv, size=round(32 * SCALE), on_change=self._view_tool_changed,
                                             on_hint=self._view_hint, anchor=self._view_tools_anchor)
        # the aircraft panel grows (livery editor): keep the tool buttons beside it
        self.view_aircraft.bind("<Configure>", lambda e: self.glv and self.glv.request(), add="+")
        self.glv.on_bone_pick = self._view_pick_bone
        self.glv.on_pose = self._view_pose_changed
        # bottom right of the view: the list of controls
        self.view_controls_btn = Button(self.view_host, "Controls", self._view_controls, font=F["small"], padx=10,
                                        pady=3)
        return self.glv

    def _view_ready(self, r):
        self._log(f"  3D viewer ready: {r.renderer_name} (OpenGL {r.version.split()[0]})")
        self._view_apply()

    def _view_fail(self, why):
        self._glv_failed = why
        if getattr(self, "gizmo", None) is not None:
            self.gizmo.show(False)
            self.view_controls_btn.place_forget()
            self.view_tools.show(False)
        if self.glv is not None:
            try:
                self.glv.pack_forget()
            except tk.TclError:
                pass
        self.view_placeholder.configure(
            text=f"The 3D viewer couldn't start: {why}\n\nUse “Open in browser” above to view the model meanwhile.")
        self.view_placeholder.place(relx=0.5, rely=0.5, anchor="center")
        self.v_view_status.set("viewer unavailable")
        self._log(f"  3D viewer: {why}", "warn")

    def _view_pick(self):
        if self.v_view_model.get():
            self._view_load(Path(self.v_view_model.get()))

    AIRCRAFT_RE = re.compile(r"^S[KM]_((?:PP|OP)\d+_[A-Za-z0-9]+)")

    def _view_attachments(self, path):
        """Gear / refuel probe / pylon meshes that belong on this airframe: {group: {choice: glb path}}."""
        m = self.AIRCRAFT_RE.match(path.stem)
        if not m:
            return {}
        f = m.group(1)
        if path.stem not in (f"SK_{f}", f"SK_{f}_CP", f"SM_{f}"):
            return {}
        d = path.parent

        def pick(*names):
            return next((d / (n + ".glb") for n in names if (d / (n + ".glb")).exists()), None)

        out = {"gear": {"down": pick(f"SM_{f}_GearOn", f"SK_{f}_GearOn"), "up": pick(f"SM_{f}_GearOff")},
               "probe": {"off": pick(f"SM_{f}_RefuelOff"), "on": pick(f"SM_{f}_RefuelOn")},
               "pylons": {g.stem.rsplit("_", 1)[-1]: g for g in sorted(d.glob(f"SM_{f}_SWP*.glb"))}}
        return {k: {c: p for c, p in v.items() if p} for k, v in out.items() if any(v.values())}

    def _view_liveries(self, path):
        """(final folder, livery data) when this model comes from an extraction that has its liveries."""
        m = self.AIRCRAFT_RE.match(path.stem)
        final = next((p for p in path.parents if p.name == FINAL_DIR_NAME), None)
        if not m or final is None:
            return None, None
        j = final / "Liveries" / f"{m.group(1)}.json"
        try:
            data = json.loads(j.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return final, None
        raw = final.parent / RAW_DIR_NAME
        try:
            import ac8_livery
            if data.get("format", 1) < ac8_livery.FORMAT:  # made by an older version: redo it
                if raw.is_dir() and any(raw.rglob("MI_*_Decal.uasset")):
                    ac8_livery.build(raw, final, log=lambda *_: None)
                    data = json.loads(j.read_text(encoding="utf-8"))
                else:  # the unpacked files weren't kept: update it from the values it stored
                    data = ac8_livery.upgrade(data)
                    j.write_text(json.dumps(data, indent=1), encoding="utf-8")
                self._log(f"  Liveries: updated {j.name} to the current format")
        except Exception as e:
            self._log(f"  Liveries: couldn't update {j.name}: {e}")
        return final, data

    def _view_fill_aircraft(self, path):
        att = self._view_attachments(path)
        final, data = self._view_liveries(path)
        same = data is not None and self._view_liv_data is not None and \
            data.get("aircraft") == self._view_liv_data.get("aircraft")
        self._view_att, self._view_liv_data, self._view_liv_final = att, data, final
        if not same:
            self.v_view_livery.set("")
            self._ov_icon.pack_forget()
        opts = [("", "As exported")]
        for lv in (data or {}).get("custom", []):
            opts.append((lv["id"], "★ " + lv.get("custom_name", lv["id"])))
        livs = (data or {}).get("liveries", [])
        has_game = any(lv.get("in_game") for lv in livs)
        for lv in sorted(livs, key=lambda l: (has_game and not l.get("in_game"), l["id"])):
            tags = []
            if lv.get("decals"):
                tags.append("emblems")
            if "LayerMaskMap" in lv.get("textures", {}):
                tags.append("paint")
            label = lv["id"] + (f" · {lv['name']}" if lv.get("name") else "")
            if has_game and not lv.get("in_game"):
                label = "variant " + label  # cutscene / enemy versions — not selectable in the game
            opts.append((lv["id"], label + (f"  ({', '.join(tags)})" if tags else "")))
        self.dd_view_livery.set_options(opts if data else [("", "Not extracted yet")])
        # a different aircraft starts as it sits on the deck: gear down, probe stowed, no pylon set; picks made
        # in the dropdowns stay while you keep looking at the same aircraft
        m_ = self.AIRCRAFT_RE.match(path.stem)
        code = m_.group(1) if m_ else None
        fresh = code != getattr(self, "_view_att_code", None)
        self._view_att_code = code
        defaults = {"gear": "down", "probe": "off", "pylons": ""}
        for key, dd, labels in (("gear", self.dd_view_gear, {"down": "Down", "up": "Up"}),
                                ("probe", self.dd_view_probe, {"off": "Stowed", "on": "Extended"}),
                                ("pylons", self.dd_view_pylons, None)):
            ch = att.get(key, {})
            var = {"gear": self.v_view_gear, "probe": self.v_view_probe, "pylons": self.v_view_pylons}[key]
            if fresh or (var.get() and var.get() not in ch):
                var.set(defaults[key] if defaults[key] in ch else "")
            o = [("", "None")] + [(c, labels.get(c, c) if labels else f"Set {c[3:]}") for c in ch]
            dd.set_options(o)
        if att or data:
            self.view_aircraft.place(x=10, y=10, anchor="nw")  # top left (the axis circle has the top right)
            self.view_aircraft.lift()
        else:
            self.view_aircraft.place_forget()
        self._ov_edit_btn.configure(fg=C["accent"] if data else C["text3"])
        stale = bool(data and data.get("from_old_version"))
        need = bool(self.AIRCRAFT_RE.match(path.stem)) and (data is None or stale)
        if stale:
            self.v_view_status.set("This aircraft's liveries were read by an older version, which got some values "
                                   "wrong (e.g. which area an emblem is drawn on) — click Extract to read them "
                                   "again from the game.")
        if need:
            self._view_extract_btn.pack(side="left", padx=(8, 0))
        else:
            self._view_extract_btn.pack_forget()
        if not data and self._ov_editing:
            self._ov_toggle_editor()

    def _view_load(self, path, keep_camera=False):
        glv = self._ensure_glv()
        if glv is None:
            return
        import ac8_glview
        self._view_token += 1
        tok = self._view_token
        self._view_fill_aircraft(path)
        if str(path) == self.ALL_PARTS:
            return self._view_load_together(tok, keep_camera)
        att = getattr(self, "_view_att", {})
        extras = [att.get(g, {}).get(v.get()) for g, v in (("gear", self.v_view_gear), ("probe", self.v_view_probe),
                                                          ("pylons", self.v_view_pylons))]
        extras = [e for e in extras if e]
        livery = self._view_livery_entry()
        final = self._view_liv_final
        self.v_view_status.set(f"Loading {path.stem}" + (f" + {len(extras)} part(s)" if extras else "") + "…")

        def work():
            spec = None
            try:
                m = ac8_glview.load_glb(path)
                if extras:
                    m = ac8_glview.merge_models(m, [ac8_glview.load_glb(e) for e in extras])
                st, err = ac8_glview.model_stats(m), None
                if livery:
                    spec = ac8_glview.livery_spec(m, livery, final)
            except Exception as e:
                SLOG.exc(f"Viewer: loading {path}")
                m, st, err = None, None, e
            self.post("call", lambda: self._view_loaded(tok, path, m, st, err, keep_camera, spec))

        threading.Thread(target=work, daemon=True).start()

    def _view_load_together(self, tok, keep_camera):
        import ac8_glview
        files = self._together_files(self._view_files)
        self.v_view_status.set(f"Loading {len(files)} parts…")
        path = Path(self.ALL_PARTS)

        def work():
            try:
                models, cache = [], {}
                for f in files:
                    try:
                        models.append(ac8_glview.load_glb(f, cache=cache))
                    except Exception as e:
                        SLOG.exc(f"Viewer: loading {f}")
                        self.post("call", lambda n=f.name, e=e: self._log(f"  Viewer: skipped {n}: {e}", "warn"))
                if not models:
                    raise RuntimeError("none of the parts could be opened")
                m = ac8_glview.merge_models(models[0], models[1:])
                m.name = f"All parts ({len(models)})"
                st, err = ac8_glview.model_stats(m), None
            except Exception as e:
                SLOG.exc("Viewer: loading all parts")
                m, st, err = None, None, e
            self.post("call", lambda: self._view_loaded(tok, path, m, st, err, keep_camera))

        threading.Thread(target=work, daemon=True).start()

    def _view_aircraft_keys(self, folder):
        """Asset-list folders of an aircraft (e.g. Vehicles/Aircraft/PP/PP0030_su33)."""
        return sorted(k for k in self.index.nodes if k.rsplit("/", 1)[-1] == folder
                      and k.startswith(("Vehicles/Aircraft/", "OnlineMode/Vehicles/Aircraft/")))

    def _view_extract_aircraft(self):
        """Extract the whole aircraft shown (meshes, every livery, emblems) so liveries and the editor work."""
        m = self.AIRCRAFT_RE.match(Path(self.v_view_model.get()).stem) if self.v_view_model.get() else None
        if not m:
            return
        if not len(self.index):
            messagebox.showinfo(APP_NAME, "Load the asset list first (Assets tab: Scan Game or Import List…).")
            return
        keys = self._view_aircraft_keys(m.group(1))
        if not keys:
            messagebox.showinfo(APP_NAME, f"{m.group(1)} isn't in the asset list.")
            return
        self.checked.clear()
        self._tick_keys(keys, True)
        self._log(f"Viewer: extracting the whole aircraft ({', '.join(keys)}) for its liveries")
        self.v_view_status.set("Extracting the whole aircraft… the viewer reopens it with its liveries when done.")
        self._view_reopen = Path(self.v_view_model.get()).stem
        self.extract(all_types=True)
        if not (self.worker and self.worker.is_alive()):  # extract didn't start (setup / key missing)
            self._view_reopen = None

    def _view_livery_entry(self):
        """The livery being shown — an editable copy, so number / emblem / colour edits stay until another
        livery is picked."""
        lid = self.v_view_livery.get()
        if not lid or not self._view_liv_data:
            self._view_edit = None
            return None
        if getattr(self, "_view_edit", None) is not None and self._view_edit.get("id") == lid:
            return self._view_edit
        import copy
        pool = self._view_liv_data.get("liveries", []) + self._view_liv_data.get("custom", [])
        src = next((lv for lv in pool if lv["id"] == lid), None)
        if src is None:
            return None
        self._view_edit = copy.deepcopy(src)
        font = self._view_liv_data.get("number_font") or {}
        rel = font.get("") or (next(iter(font.values())) if font else None)
        self._view_edit["_font_png"] = str(self._view_liv_final / rel) if rel else None
        if self._ov_editing:
            self._ov_fill_editor()
        return self._view_edit

    def _ov_show_icon(self, entry):
        rel = (entry or {}).get("icon")
        path = (self._view_liv_final / rel) if (rel and self._view_liv_final) else None
        if path is None or not path.exists():
            self._ov_icon.pack_forget()
            return
        try:
            from PIL import Image, ImageTk
            im = Image.open(path).convert("RGBA")
            im.thumbnail((round(230 * SCALE), round(115 * SCALE)))
            bg = Image.new("RGBA", im.size, C["bg1"])
            self._ov_icon_img = ImageTk.PhotoImage(Image.alpha_composite(bg, im))
            self._ov_icon.configure(image=self._ov_icon_img)
            self._ov_icon.pack(after=self.dd_view_livery, pady=(6, 0))
        except Exception:
            self._ov_icon.pack_forget()

    def _view_set_livery(self):
        """Livery picked: build it in the background and put it on the model in view."""
        if self.glv is None or self._view_model is None:
            return
        import ac8_glview
        livery, final, model = self._view_livery_entry(), self._view_liv_final, self._view_model
        self._ov_show_icon(livery)
        if not livery:
            self.glv.set_livery(None)
            self.v_view_status.set(f"{model.name}  ·  livery as exported")
            return
        self._view_token += 1
        tok = self._view_token
        self.v_view_status.set(f"Loading livery {livery['id']}…")

        def work():
            try:
                spec, err = ac8_glview.livery_spec(model, livery, final), None
            except Exception as e:
                SLOG.exc("Viewer: livery")
                spec, err = None, e
            self.post("call", lambda: self._view_livery_ready(tok, livery, spec, err))

        threading.Thread(target=work, daemon=True).start()

    def _view_livery_ready(self, tok, livery, spec, err):
        if tok != self._view_token or self.glv is None:
            return
        if err is not None:
            self.v_view_status.set(f"Couldn't load livery {livery['id']}: {err}")
            return
        self.glv.set_livery(spec)
        self.v_view_status.set(self._livery_note(livery, spec))

    def _livery_note(self, livery, spec):
        note = f"{self._view_model.name if self._view_model else ''}  ·  livery {livery['id']}"
        if spec.get("textures", True) and not spec["mats"]:
            note += " — its textures aren't in this extraction"
        if spec.get("missing"):
            note += (f"  ·  {len(spec['missing'])} emblem(s) not extracted ({', '.join(spec['missing'][:3])}"
                     + ("…" if len(spec["missing"]) > 3 else "") + ") — click Convert on the extraction to fetch them")
        if spec.get("layers"):
            note += "  ·  paint colours are approximate"
        return note

    def _view_loaded(self, tok, path, model, stats, err, keep_camera=False, spec=None):
        if tok != self._view_token or self.glv is None:
            return
        if err is not None:
            self.v_view_status.set(f"Couldn't open {path.name}: {err}")
            self._log(f"  Viewer: couldn't open {path.name}: {err}", "err")
            return
        self.view_placeholder.place_forget()
        if not self.glv.winfo_ismapped():
            self.glv.pack(fill="both", expand=True)
        if getattr(self, "gizmo", None) is not None and not self.gizmo.visible:
            self.gizmo.show(True)
            self.view_controls_btn.place(relx=1.0, rely=1.0, x=-8, y=-8, anchor="se")
            self.view_controls_btn.lift()
        self._view_model, self._view_stats = model, stats
        self.glv.set_model(model, keep_camera=keep_camera, livery=spec)
        self._view_apply()
        self._fill_view_parts()
        self._fill_view_bones()
        if getattr(self, "view_tools", None) is not None:
            if model.joints:
                self.view_tools.show(True)
            else:
                self.view_tools.set_active(None)
                self.view_tools.show(False)
        self._fill_view_info()
        n = len(self._view_files)
        lv = self._view_livery_entry()
        self.v_view_status.set(self._livery_note(lv, spec) if (lv and spec) else
                               f"{model.name}" + (f"  ·  {n} models in the list" if n > 1 else ""))
        self.after(50, self.glv.request)  # draw again once the side panel has been rebuilt

    def _view_apply(self, *_):
        g = self.glv
        if g is None or g.r is None:
            return
        g.r.opts.update(mode=self.v_view_mode.get(), wire=self.v_view_wire.get(), skeleton=self.v_view_skel.get(),
                        grid=self.v_view_grid.get(), bg=self.v_view_bg.get())
        g.request()

    TEX_ROLES_VIEW = (("base", "Colour"), ("normal", "Normal"), ("mr", "Metal/Rough"))

    def _fill_view_parts(self):
        for w in self.view_parts.winfo_children():
            w.destroy()
        m = self._view_model
        groups = {}
        by_model = str(self.v_view_model.get()) == self.ALL_PARTS and m.mat_src  # all parts: one entry per mesh
        for p in m.prims:
            if by_model:
                base = m.mat_src[p.mat]
            else:
                base = re.sub(r"\.10\d\d$", "", m.materials[p.mat].name)  # UDIM tiles of one material → one entry
            groups.setdefault(base, set()).add(p.mat)
        self._view_part_vars, self._view_tex_vars, self._view_folds = {}, {}, {}
        self._view_thumb_imgs = []
        for name in sorted(groups):
            mats = sorted(groups[name])
            v = tk.BooleanVar(value=True)
            self._view_part_vars[name] = (v, set(mats))
            card = tk.Frame(self.view_parts, bg=C["bg2"], highlightthickness=1, highlightbackground=C["border1"])
            card.pack(fill="x", pady=(0, 4), padx=(0, 4))
            row = tk.Frame(card, bg=C["bg2"])
            row.pack(fill="x", padx=6, pady=5)
            arrow = tk.Label(row, text="▸", bg=C["bg2"], fg=C["text3"], font=F["base"], width=2, cursor="hand2")
            arrow.pack(side="left")
            Check(row, v, name if len(name) <= 34 else name[:16] + "…" + name[-16:], bg=C["bg2"],
                  command=self._view_parts_changed).pack(side="left")
            only = tk.Label(row, text="only", bg=C["bg2"], fg=C["text3"], font=F["small"], cursor="hand2", padx=4)
            only.pack(side="right")
            only.bind("<Button-1>", lambda e, n=name: self._view_parts_only(n))
            sub = tk.Frame(card, bg=C["bg2"])  # the textures — folded away until the arrow is clicked
            ntex = 0
            for role, label in self.TEX_ROLES_VIEW:
                imgs = [m.materials[i].tex.get(role) for i in mats if m.materials[i].tex.get(role) in m.image_names]
                if not imgs:
                    continue
                more = len({re.sub(r"\.10\d\d$", "", m.image_names[i]) for i in imgs}) - 1
                ntex += 1
                tv = tk.BooleanVar(value=True)
                self._view_tex_vars[(name, role)] = (tv, set(mats))
                tr = tk.Frame(sub, bg=C["bg2"])
                tr.pack(fill="x", padx=(30, 6), pady=(0, 4))
                Check(tr, tv, "", bg=C["bg2"], command=self._view_tex_changed).pack(side="left")
                tex_name = re.sub(r"\.10\d\d$", "", m.image_names[imgs[0]])
                extra = f"  (+{more} more)" if more else \
                    f"  (+{len(set(imgs)) - 1} tiles)" if len(set(imgs)) > 1 else ""
                tk.Label(tr, text=label, bg=C["bg2"], fg=C["text3"], font=F["small"], width=11, anchor="w").pack(
                    side="left", padx=(6, 0))
                lbl = tk.Label(tr, text=f"{tex_name}{extra}", bg=C["bg2"], fg=C["text2"], font=F["mono_s"],
                               anchor="w", cursor="hand2")
                lbl.pack(side="left")
                lbl.bind("<Button-1>", lambda e, ii=imgs[0], r=role, n=name: self._view_show_tex(ii, r, n))
            if not ntex:
                tk.Label(sub, text="no textures (flat colour)", bg=C["bg2"], fg=C["text3"], font=F["small"],
                         anchor="w").pack(fill="x", padx=(34, 6), pady=(0, 4))
            tk.Label(row, text=f"{ntex} tex", bg=C["bg2"], fg=C["text3"], font=F["small"]).pack(side="right")

            def toggle(_e=None, sub=sub, arrow=arrow, name=name, force=None):
                open_ = (not sub.winfo_ismapped()) if force is None else force
                if open_:
                    sub.pack(fill="x")
                else:
                    sub.pack_forget()
                arrow.configure(text="▾" if open_ else "▸")
            arrow.bind("<Button-1>", toggle)
            self._view_folds[name] = toggle
        if not groups:
            tk.Label(self.view_parts, text="No parts", bg=C["bg1"], fg=C["text3"], font=F["small"]).pack()
        self._view_tex_shown = None
        self.view_tex_preview.configure(image=self._blank_thumb(), text="Click a texture name to see it here.",
                                        cursor="")
        self.v_view_tex_info.set("Expand a part to see its textures.")

    def _view_expand_all(self, on):
        for fn in getattr(self, "_view_folds", {}).values():
            fn(force=on)

    def _view_show_tex(self, ii, role, part):
        m = self._view_model
        th = m.thumbs.get(ii)
        if th is None:
            return
        from PIL import Image, ImageTk
        bg = Image.new("RGBA", th.size, C["bg0"])
        img = ImageTk.PhotoImage(Image.alpha_composite(bg, th) if role == "base" else th.convert("RGB"))
        self._view_thumb_imgs = [img]
        self.view_tex_preview.configure(image=img, text="")
        w, h = m.image_sizes.get(ii, th.size)
        self.v_view_tex_info.set(f"{m.image_names[ii]}\n{w} × {h}  ·  {role_name(role)[4:].capitalize()}\nClick the picture to enlarge")
        self._view_tex_shown = (ii, role, part)
        self.view_tex_preview.configure(cursor="hand2")
        # only the preview box changes; the 3D view keeps its shading (MAP picks a texture view there)

    def _view_tex_big(self):
        """The texture in the preview box, in its own window: wheel zoom (at the mouse), drag to move, channels."""
        shown, m = getattr(self, "_view_tex_shown", None), self._view_model
        if not shown or m is None:
            return
        ii, role, part = shown
        from PIL import Image, ImageTk
        import ac8_glview
        try:
            src = ac8_glview.load_full_image(m.image_src[ii])
        except Exception as e:
            SLOG.exc("Viewer: full-size texture")
            src = m.thumbs.get(ii)
            self._log(f"  Couldn't load the full-size texture ({e}); showing the small preview", "warn")
        if src is None:
            return
        src = src.convert("RGBA")
        name = m.image_names.get(ii, "texture")
        fw, fh = m.image_sizes.get(ii, src.size)
        w = tk.Toplevel(self, bg=C["bg0"])
        w.title(f"{name}  ({fw} × {fh})")
        ml, mt, mr, mb = monitor_area(self)  # the monitor the app is on
        sw, sh = mr - ml, mb - mt
        side = min(round(sh * 0.8), round(sw * 0.8), max(src.size) + 40)
        ww, wh = max(side, 420), max(side, 420) + 60
        w.geometry(f"{ww}x{wh}+{ml + max(0, (sw - ww) // 2)}+{mt + max(0, (sh - wh) // 2 - 20)}")  # middle of it
        top = tk.Frame(w, bg=C["bg0"], padx=10, pady=8)
        top.pack(fill="x")
        chan = tk.StringVar(value="all")
        opts = [("all", "RGB" if role != "base" else "Colour"), ("r", "R"), ("g", "G"), ("b", "B"), ("a", "A")]
        Segmented(top, chan, opts, compact=True).pack(side="left")
        info = tk.StringVar()
        tk.Label(top, text="wheel zoom · drag move · double-click fit · Esc close", bg=C["bg0"], fg=C["text3"],
                 font=F["small"]).pack(side="left", padx=10)
        tk.Label(w, textvariable=info, bg=C["bg0"], fg=C["text2"], font=F["small"], anchor="w",
                 padx=10).pack(fill="x", side="bottom", pady=(0, 6))
        cv = tk.Canvas(w, bg=C["bg0"], highlightthickness=0, bd=0, cursor="fleur")
        cv.pack(fill="both", expand=True)
        tip = {"mr": "  ·  R = occlusion, G = roughness, B = metal"}.get(role, "")
        st = {"img": None, "scale": 1.0, "ox": 0.0, "oy": 0.0, "drag": None, "photo": None}

        def checker(size):
            c = Image.new("RGBA", size, "#2a2a2e")
            tile = Image.new("RGBA", (16, 16), "#3a3a40")
            for y in range(0, size[1], 32):
                for x in range(0, size[0], 32):
                    c.paste(tile, (x, y))
                    c.paste(tile, (x + 16, y + 16))
            return c

        def set_chan(*_):
            c = chan.get()
            if c == "all":
                st["img"] = Image.alpha_composite(checker(src.size), src).convert("RGB")
            else:
                st["img"] = src.getchannel("RGBA".index(c.upper())).convert("RGB")
            draw()

        def fit():
            cw, ch = max(cv.winfo_width(), 50), max(cv.winfo_height(), 50)
            iw, ih = src.size
            st["scale"] = min(cw / iw, ch / ih) * 0.96
            st["ox"] = (iw - cw / st["scale"]) / 2
            st["oy"] = (ih - ch / st["scale"]) / 2
            draw()

        def draw():
            img = st["img"]
            if img is None:
                return
            cw, ch = max(cv.winfo_width(), 1), max(cv.winfo_height(), 1)
            k, ox, oy = st["scale"], st["ox"], st["oy"]
            iw, ih = img.size
            x0, y0 = max(0.0, ox), max(0.0, oy)
            x1, y1 = min(float(iw), ox + cw / k), min(float(ih), oy + ch / k)
            cv.delete("all")
            if x1 > x0 and y1 > y0:
                box = (int(x0), int(y0), min(iw, int(math.ceil(x1))), min(ih, int(math.ceil(y1))))
                part_img = img.crop(box)
                dw = max(1, round((box[2] - box[0]) * k))
                dh = max(1, round((box[3] - box[1]) * k))
                part_img = part_img.resize((dw, dh), Image.NEAREST if k >= 2 else Image.BILINEAR,
                                           reducing_gap=None if k >= 1 else 2.0)
                st["photo"] = ImageTk.PhotoImage(part_img, master=cv)
                cv.create_image(round((box[0] - ox) * k), round((box[1] - oy) * k), image=st["photo"], anchor="nw")
            info.set(f"{name}  ·  {fw} × {fh}  ·  {k * 100 * src.size[0] / fw:.0f}%{tip}")

        def zoom(e, steps):
            k0 = st["scale"]
            k1 = min(max(k0 * (1.25 ** steps), 0.02), 64.0)
            px, py = st["ox"] + e.x / k0, st["oy"] + e.y / k0
            st["scale"], st["ox"], st["oy"] = k1, px - e.x / k1, py - e.y / k1
            draw()
            return "break"

        def press(e):
            st["drag"] = (e.x, e.y)

        def move(e):
            if st["drag"]:
                dx, dy = e.x - st["drag"][0], e.y - st["drag"][1]
                st["ox"] -= dx / st["scale"]
                st["oy"] -= dy / st["scale"]
                st["drag"] = (e.x, e.y)
                draw()

        cv.bind("<MouseWheel>", lambda e: zoom(e, 1 if e.delta > 0 else -1))
        cv.bind("<Button-4>", lambda e: zoom(e, 1))
        cv.bind("<Button-5>", lambda e: zoom(e, -1))
        cv.bind("<ButtonPress-1>", press)
        cv.bind("<B1-Motion>", move)
        cv.bind("<ButtonRelease-1>", lambda e: st.update(drag=None))
        cv.bind("<Double-Button-1>", lambda e: fit())
        cv.bind("<Configure>", lambda e: draw())
        w.bind("<Escape>", lambda e: w.destroy())
        watch(w, chan, set_chan)
        st["img"] = None
        set_chan()
        w.after(60, fit)
        w.focus_set()

    def _view_tex_changed(self):
        if self.glv is None or self.glv.r is None:
            return
        off = set()
        for (name, role), (v, mats) in self._view_tex_vars.items():
            if not v.get():
                off |= {(i, role) for i in mats}
        self.glv.r.opts["tex_off"] = off
        self.glv.request()

    def _view_parts_only(self, name):
        for n, (v, _m) in self._view_part_vars.items():
            v.set(n == name)
        self._view_parts_changed()

    def _view_parts_changed(self):
        if self.glv is None or self.glv.r is None:
            return
        hidden = set()
        for v, mats in self._view_part_vars.values():
            if not v.get():
                hidden |= mats
        self.glv.r.opts["hidden"] = hidden
        self.glv.request()

    def _view_tex_all_on(self):
        for v, _m in getattr(self, "_view_tex_vars", {}).values():
            v.set(True)
        self._view_tex_changed()

    def _view_parts_all(self, on):
        for v, _m in getattr(self, "_view_part_vars", {}).values():
            v.set(on)
        self._view_parts_changed()

    def _fill_view_bones(self):
        m = self._view_model
        lb = self.view_bones
        lb.delete(0, "end")
        depth = {}
        jset = set(m.joints)
        for k, jn in enumerate(m.joints):
            par = m.nodes[jn]["parent"]
            depth[jn] = depth.get(par, -1) + 1 if par in jset else 0
            lb.insert("end", "  " * depth[jn] + m.nodes[jn]["name"])
        self._view_bone = None
        self._set_bone_fields(())
        self.view_bone_name.set("Pick a bone" if m.joints else "This model has no skeleton")

    def _view_bone_selected(self, _e=None):
        sel = self.view_bones.curselection()
        if not sel or self._view_model is None:
            return
        k = sel[0]
        self._view_bone = k
        jn = self._view_model.joints[k]
        self.view_bone_name.set(self._view_model.nodes[jn]["name"])
        if self.glv and self.glv.r:
            self.glv.r.opts["bone"] = k
            self._set_bone_fields(self.glv.r.opts["pose"].get(jn, ()))
            if not self.v_view_skel.get():
                self.v_view_skel.set(True)
                self._view_apply()
            tools = getattr(self, "view_tools", None)
            if tools is not None and tools.active is None and tools.visible:
                # a picked bone gets a gizmo straight away: Translate the first time, then the tool used last
                tools.set_active(getattr(self, "_last_bone_tool", "move"))
            self.glv.request()

    def _set_bone_fields(self, pose):
        """Show a bone's pose (rx, ry, rz[, tx, ty, tz]) in the Translation / Rotation fields."""
        pz = list(pose) + [0.0] * (6 - len(pose))
        vals = pz[3:6] + pz[0:3]
        for i, v in enumerate(vals):
            self.v_bone[i].set(f"{float(v):.3f}" if i < 3 else f"{float(v):.1f}")
        for sc, a in zip(getattr(self, "view_rot", []), pz[0:3]):
            sc.set(max(-180.0, min(180.0, ((float(a) + 180.0) % 360.0) - 180.0)))

    def _view_bone_slider(self, n, v):
        """Rotation slider n moved (whole degrees). Ignore the echo of a value set from the pose itself."""
        if self._view_bone is None or self.glv is None or self.glv.r is None:
            return
        pz = self._bone_pose6()
        cur = ((pz[n] + 180.0) % 360.0) - 180.0
        if abs(round(cur) - v) < 0.5:
            return
        pz[n] = v
        self.glv.set_pose6(self._view_bone, pz)
        self.v_bone[3 + n].set(f"{v:.1f}")

    def _bone_pose6(self):
        jn = self._view_model.joints[self._view_bone]
        pz = list(self.glv.r.opts["pose"].get(jn, ()))
        return pz + [0.0] * (6 - len(pz))

    def _view_bone_field(self, i):
        """A typed value: field i = translation X Y Z (0-2) or rotation X Y Z (3-5)."""
        if self._view_bone is None or self.glv is None or self.glv.r is None:
            return
        try:
            v = float(self.v_bone[i].get().replace(",", ".").replace("°", "").strip() or 0)
        except ValueError:
            self._set_bone_fields(self._bone_pose6())  # not a number: put the real value back
            return
        pz = self._bone_pose6()
        slot = i + 3 if i < 3 else i - 3  # pose order is rotation, then translation
        if abs(pz[slot] - v) < 1e-9:
            return
        pz[slot] = v
        self.glv.set_pose6(self._view_bone, pz)
        self._set_bone_fields(pz)

    def _view_bone_step(self, i, up, e):
        if self._view_bone is None:
            return "break"
        fine = e.state & 0x0001
        step = (0.01 if fine else 0.1) if i < 3 else (0.1 if fine else 1.0)
        try:
            v = float(self.v_bone[i].get().replace(",", ".") or 0)
        except ValueError:
            v = 0.0
        self.v_bone[i].set(f"{v + (step if up else -step):.4f}")
        self._view_bone_field(i)
        return "break"

    def _view_tools_anchor(self):
        """Translate / rotate buttons: just right of the aircraft panel when it's showing (so the livery editor
        never covers them), else the default place on the left."""
        ov = self.view_aircraft
        try:
            if not ov.winfo_ismapped():
                return None
            return (ov.winfo_rootx() - self.glv.winfo_rootx() + ov.winfo_width() + 8,
                    ov.winfo_rooty() - self.glv.winfo_rooty())
        except tk.TclError:
            return None

    def _view_tool_changed(self, tool):
        m = self._view_model
        if tool is not None:
            self._last_bone_tool = tool  # picking the next bone brings this tool back
        if tool is None:
            self._view_hint(None)
            return
        if m is None or not m.joints:
            return
        if not self.v_view_skel.get():  # bones have to be visible to click them
            self.v_view_skel.set(True)
            self._view_apply()
        self.v_view_panel.set("bones")
        self.v_view_status.set(("Translate" if tool == "move" else "Rotate") + " · " +
                               ("drag an arrow" if tool == "move" else "drag a ring") +
                               (" of the selected bone" if self._view_bone is not None else " · click a bone first"))

    def _view_pick_bone(self, k):
        lb = self.view_bones
        lb.selection_clear(0, "end")
        lb.selection_set(k)
        lb.see(k)
        self.v_view_panel.set("bones")
        self._view_bone_selected()
        if self.view_tools.active:
            self._view_tool_changed(self.view_tools.active)

    def _view_pose_changed(self, k):
        if k != self._view_bone or self.glv is None or self.glv.r is None:
            return
        jn = self._view_model.joints[k]
        self._set_bone_fields(self.glv.r.opts["pose"].get(jn, ()))

    def _view_reset_bone(self, rotation=False, translation=False):
        """The selected bone's rotation / translation back to its rest pose (Alt+R / Alt+G, like Blender)."""
        if self._view_bone is None or self.glv is None or self.glv.r is None:
            self.v_view_status.set("Pick a bone first")
            return
        self.glv.reset_bone(self._view_bone, rotation=rotation, translation=translation)
        self._set_bone_fields(self._bone_pose6())
        self.v_view_status.set(("Rotation" if rotation else "Translation") + " reset")

    def _view_reset_pose(self):
        if self.glv and self.glv.r:
            self.glv.reset_pose()
        self._set_bone_fields(())

    def _fill_view_info(self):
        m, st = self._view_model, self._view_stats
        x, y, z = st["size"]
        r = self.glv.r if self.glv else None
        txt = (f"{m.name}\n\n"
               f"Vertices      {st['verts']:,}\n"
               f"Triangles     {st['tris']:,}\n"
               f"Materials     {st['materials']}\n"
               f"Textures      {st['textures']}\n"
               f"Bones         {st['bones']}\n"
               f"Size (m)      {x:.2f} W × {y:.2f} H × {z:.2f} L\n"
               f"File          {st['file_mb']:.1f} MB\n\n"
               f"{m.path}")
        if r is not None:
            txt += f"\n\nGPU: {r.renderer_name}"
        self.v_view_info.set(txt)

    # ---------------------------------------------------------- baked texture export

    def _view_export_dialog(self):
        """Small window: preset + size, then bake the livery (paint, emblems, numbers) into the colour maps and
        write every texture set of the model in that preset."""
        if self.glv is None or self.glv.r is None or self._view_model is None:
            messagebox.showinfo(APP_NAME, "Open a model first.")
            return
        if getattr(self, "_bake_win", None) is not None and self._bake_win.winfo_exists():
            self._bake_win.lift()
            return
        w = tk.Toplevel(self, bg=C["bg0"])
        self._bake_win = w
        w.title("Export textures")
        w.transient(self)
        w.resizable(False, False)
        pad = tk.Frame(w, bg=C["bg0"], padx=20, pady=16)
        pad.pack(fill="both", expand=True)
        tk.Label(pad, text="Export textures", bg=C["bg0"], fg=C["text1"], font=F["h"]).pack(anchor="w")
        lv = self._view_livery_entry()
        what = (f"Livery {lv.get('custom_name') or lv['id']} — its paint, emblems and numbers are baked into the "
                f"colour maps." if lv else "The textures as exported (pick a livery first to bake it in).")
        tk.Label(pad, text=f"{self._view_model.name}\n{what}", bg=C["bg0"], fg=C["text3"], font=F["small"],
                 justify="left", wraplength=round(420 * SCALE)).pack(anchor="w", pady=(2, 12))
        Dropdown(pad, "Preset", self.v_bake_preset, PBR_PRESETS[1:], width=38).pack(fill="x")
        Dropdown(pad, "Size", self.v_bake_size, TEXTURE_SIZES, width=38).pack(fill="x", pady=(6, 0))
        Dropdown(pad, "Normal", self.v_bake_normal, NORMAL_CONV, width=38).pack(fill="x", pady=(6, 0))
        tk.Label(pad, text="Size is the largest side of each texture (Full size keeps the game's resolution).",
                 bg=C["bg0"], fg=C["text3"], font=F["small"], wraplength=round(420 * SCALE),
                 justify="left").pack(anchor="w", pady=(8, 0))
        row = tk.Frame(pad, bg=C["bg0"])
        row.pack(fill="x", pady=(14, 0))

        def go():
            w.destroy()
            self._view_export_textures()

        Button(row, "Export", go, variant="primary").pack(side="right")
        Button(row, "Cancel", w.destroy).pack(side="right", padx=(0, 8))
        center_on(w, self)

    def _view_export_textures(self):
        import ac8_glview
        import ac8_pbr
        model, livery = self._view_model, self._view_livery_entry()
        if model is None or self.glv is None or self.glv.r is None:
            return
        src = Path(model.path)
        final = self._view_liv_final or next((p for p in src.parents if p.name == FINAL_DIR_NAME), None)
        preset = self.v_bake_preset.get() or "separate"
        if preset not in ac8_pbr.PRESETS:
            preset = "separate"
        normal = None if self.v_bake_normal.get() in ("", "auto") else self.v_bake_normal.get()
        cap = int(self.v_bake_size.get() or 0)
        tag = f"_{livery['id']}" if livery else ""
        out_root = (final / "Textures_Export" if final else src.parent / "Textures_Export") / \
            f"{model.name}{tag}" / preset
        name_rx = re.compile(r"^(.*_Decal)(?:\.(10\d\d))?$")

        def fit(size):
            w_, h_ = size
            if cap and max(w_, h_) > cap:
                k = cap / max(w_, h_)
                return max(1, round(w_ * k)), max(1, round(h_ * k))
            return w_, h_

        def work():
            import numpy as np
            from PIL import Image
            log = lambda t: self.post("log", t)
            self.post("step", "Finding the model's textures…")
            sets = {}
            tex_dir = final / "Textures" if final else None
            if tex_dir and tex_dir.is_dir():
                for (base, tile, _sub), roles in ac8_pbr.find_sets(tex_dir).items():
                    sets.setdefault((base, tile), roles)

            def set_of(file_name):
                mm = ac8_pbr.NAME_RE.match(Path(file_name).name if str(file_name).endswith(".png")
                                           else Path(file_name).name + ".png")
                return (mm["base"], mm["tile"]) if mm else None

            spec = None
            if livery:
                self.post("step", "Loading the livery at full size…")
                spec = ac8_glview.livery_spec(model, livery, final, max_size=1 << 14, atlas_cell=1024,
                                              log=log)
            jobs, plan = {}, {}   # material index -> bake size; material index -> (set name, tile, roles, size)
            for mi, mat in enumerate(model.materials):
                ii = mat.tex.get("base")
                key = set_of(model.image_names.get(ii, "")) if ii is not None else None
                if key is None and "normal" in mat.tex:
                    key = set_of(model.image_names.get(mat.tex["normal"], ""))
                roles = dict(sets.get(key, {})) if key else {}
                tile = key[1] if key else None
                name = key[0] if key else re.sub(r"[^A-Za-z0-9_.-]+", "_", mat.name)
                lv = spec["mats"].get(mi) if spec else None
                if lv is not None:  # a livery material: use the livery's own texture set
                    files = livery.get("textures", {})
                    mt = name_rx.match(mat.name)
                    t = (mt.group(2) or "") if mt else ""
                    for role in ("Basecolor", "Normalmap", "Livemap"):
                        fl = files.get(role, {}).get("files", {})
                        rel = fl.get(t) or fl.get("") or (fl.get("1001") if not t else None)
                        k2 = set_of(rel) if rel else None
                        if k2 and k2 in sets:
                            roles = {**roles, **sets[k2]}
                            if role == "Basecolor":
                                name, tile = k2
                    if "base" not in lv and roles.get("base"):  # paint over the full-size original
                        im = Image.open(roles["base"]).convert("RGBA")
                        sz = next((a.shape[1::-1] for a in lv.values()), None)
                        if sz and im.size != tuple(sz):
                            im = im.resize(tuple(sz), Image.LANCZOS)
                        lv["base"] = np.asarray(im, np.uint8)
                    if "base" in lv:
                        size = (lv["base"].shape[1], lv["base"].shape[0])
                    elif ii is not None:
                        size = model.image_sizes.get(ii, (2048, 2048))
                    else:
                        size = (2048, 2048)
                    if livery:
                        name = f"{name}{tag}" if tag.lstrip("_") not in name else name
                    jobs[mi] = fit(size)
                    plan[mi] = (name, tile, roles, jobs[mi], True)
                elif roles:
                    size = Image.open(next(iter(roles.values()))).size
                    plan[mi] = (name, tile, roles, fit(size), False)
                elif ii is not None:  # no files on disk (catalog preview): read the colour map off the GPU
                    jobs[mi] = fit(model.image_sizes.get(ii, (1024, 1024)))
                    plan[mi] = (name, tile, {}, jobs[mi], True)
            if not plan:
                raise RuntimeError("this model has no textures to export")
            baked = {}
            if jobs:
                self.post("step", f"Baking {len(jobs)} colour map(s)…")
                box, ev = {}, threading.Event()

                def do_bake():
                    try:
                        box["r"] = self.glv.bake(spec, jobs)
                    except Exception as e:
                        SLOG.exc("Bake")
                        box["e"] = e
                    ev.set()

                self.post("call", do_bake)
                ev.wait()
                if "e" in box:
                    raise RuntimeError(f"baking failed: {box['e']}")
                baked = box["r"]
            out_root.mkdir(parents=True, exist_ok=True)
            done, n_files = set(), 0
            for k, (mi, (name, tile, roles, size, bake)) in enumerate(sorted(plan.items(), key=lambda kv: (not kv[1][4], kv[0])), 1):
                if (name, tile) in done:
                    continue
                done.add((name, tile))
                if self.cancelled:
                    raise _Stop("Stopped.")
                self.post("status", f"{k}/{len(plan)}  {name}")
                base = None
                if bake and mi in baked:
                    rgba, cov = baked[mi]
                    rgba, cov = ac8_glview.dilate(rgba, cov)
                    fallback = roles.get("base")
                    if fallback is not None and not cov.all():  # parts outside the UV layout keep the original
                        orig = np.asarray(Image.open(fallback).convert("RGBA").resize(rgba.shape[1::-1],
                                                                                      Image.LANCZOS), np.uint8)
                        rgba = np.where(cov[..., None], rgba, orig)
                    base = rgba.astype(np.float32) / 255.0
                try:
                    maps = ac8_pbr.unpack_set(roles, size=size, base=base)
                    files = ac8_pbr.write_set(maps, out_root, name, preset, normal, tile)
                    n_files += len(files)
                    log(f"  {name}{'.' + tile if tile else ''}: {'baked + ' if base is not None else ''}"
                        f"{'+'.join(sorted(roles)) or 'colour only'} -> {len(files)} map(s), {size[0]}×{size[1]}")
                except Exception as e:
                    SLOG.exc(f"Export textures: {name}")
                    log(f"  {name}: ERROR {e}")
            self.post("call", lambda: open_folder(out_root))
            return f"✔ Exported {n_files} texture(s) ({ac8_pbr.PRESETS[preset][0]}) to {out_root}"

        self._start(work, "Exporting textures…")

    def _view_screenshot(self):
        if self.glv is None or self.glv.r is None or self._view_model is None:
            messagebox.showinfo(APP_NAME, "Open a model first.")
            return
        src = Path(self._view_model.path)
        out = src.with_name(src.stem + "_screenshot.png")
        n = 2
        while out.exists():
            out = src.with_name(f"{src.stem}_screenshot_{n}.png")
            n += 1
        try:
            self.glv.screenshot(str(out))
            self._log(f"✔ Screenshot saved: {out}")
            self.v_view_status.set(f"Screenshot saved: {out.name}")
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Couldn't save the screenshot:\n{e}")

    FLY_HELP = "Free fly  ·  click or Esc to stop"
    CONTROLS = (
        ("Normal", (("Rotate", "Middle-drag  (or left-drag)"), ("Pan", "Shift + middle-drag  (or right-drag)"),
                    ("Zoom", "Mouse wheel"), ("Frame the model", "F  or double-click"),
                    ("Snap to a side", "Click a ball on the axis circle"),
                    ("Rotate with the circle", "Drag the axis circle"))),
        ("Free fly", (("Start / stop", "Ctrl + F  or  Shift + `"), ("Stop", "Left-click  or  Esc"),
                      ("Look around", "Move the mouse"), ("Move", "W  A  S  D"), ("Down / up", "Q  /  E"),
                      ("Forward / back", "Mouse wheel"), ("Faster", "Hold Shift"))),
        ("Both", (("Fly speed", "Shift + mouse wheel"), ("Field of view", "Alt + mouse wheel"))),
        ("Bones", (("Translate tool / Rotate tool", "G  /  R  (or the buttons on the left)"),
                   ("Pick a bone", "Click it  (Skeleton on)"), ("Translate", "Drag an arrow"),
                   ("Rotate (the bone's own X Y Z)", "Drag a ring"), ("Put the tool away", "G / R again"),
                   ("Reset translation / rotation", "Alt + G  /  Alt + R"),
                   ("Look at the selected bone", ".  (period)"))),
    )

    def _view_controls(self):
        if getattr(self, "_controls_win", None) is not None and self._controls_win.winfo_exists():
            self._controls_win.lift()
            return
        w = tk.Toplevel(self, bg=C["bg0"])
        self._controls_win = w
        w.title("3D viewer controls")
        w.transient(self)
        w.resizable(False, False)
        pad = tk.Frame(w, bg=C["bg0"], padx=22, pady=16)
        pad.pack(fill="both", expand=True)
        for i, (section, rows) in enumerate(self.CONTROLS):
            tk.Label(pad, text=section.upper(), bg=C["bg0"], fg=C["accent"], font=F["small"]).grid(
                row=100 * i, column=0, columnspan=2, sticky="w", pady=(0 if i == 0 else 14, 4))
            for r, (what, how) in enumerate(rows, 1):
                tk.Label(pad, text=what, bg=C["bg0"], fg=C["text3"], font=F["base"]).grid(
                    row=100 * i + r, column=0, sticky="w", padx=(0, 24), pady=1)
                tk.Label(pad, text=how, bg=C["bg0"], fg=C["text1"], font=F["bold"]).grid(
                    row=100 * i + r, column=1, sticky="w", pady=1)
        Button(pad, "Close", w.destroy, font=F["small"], padx=14, pady=4).grid(row=999, column=1, sticky="e",
                                                                              pady=(16, 0))
        w.bind("<Escape>", lambda e: w.destroy())
        center_on(w, self)

    def _view_key(self, e):
        if self.tab != "viewer" or isinstance(self.focus_get(), tk.Entry) or self.glv is None:
            return
        ks = e.keysym.lower()
        ctrl, shift = e.state & 0x0004, e.state & 0x0001
        tilde = ks in ("asciitilde", "~") or (shift and (ks in ("grave", "`") or (IS_WIN and e.keycode == 192)))
        if (ctrl and ks == "f") or tilde:  # Ctrl+F / Shift+` (192: the key left of 1 on Windows, any layout)
            self.glv.toggle_fly()
            return "break"
        if ks == "escape" and self.glv.fly:
            self.glv.set_fly(False)
            return "break"
        if self.glv.key_down(e.keysym):  # free fly: W A S D Q E (Shift faster)
            return "break"
        alt = e.state & (0x20000 if IS_WIN else 0x0008)
        if ks in ("g", "r") and alt and getattr(self, "view_tools", None) is not None and self.view_tools.visible:
            self._view_reset_bone(rotation=ks == "r", translation=ks == "g")  # Alt+G / Alt+R: clear, like Blender
            return "break"
        if ks in ("g", "r") and not ctrl and getattr(self, "view_tools", None) is not None \
                and self.view_tools.visible:  # bone tools: G translate, R rotate (again: put it away)
            t = "move" if ks == "g" else "rotate"
            self.view_tools.set_active(None if self.view_tools.active == t else t)
            return "break"
        if ks == "f" and not ctrl:
            self.glv.frame()
        if ks in ("period", ".", "kp_decimal", "kp_delete") and self.glv.r is not None:  # . : look at the bone, like Blender
            if not self.glv.frame_bone():
                self.v_view_status.set("Pick a bone first ( . looks at the selected bone)")

    def _view_hint(self, text):
        m = self._view_model
        self.v_view_status.set(text or (m.name if m is not None else ""))

    def _view_fly_changed(self, on):
        m = self._view_model
        self.v_view_status.set(self.FLY_HELP if on else (m.name if m is not None else ""))

    def _view_focus_out(self, e):
        if self.glv is None:
            return
        self.glv.keys_released()

        def check():  # the app itself lost focus (another window): stop free fly so the mouse isn't held
            try:
                gone = self.focus_get() is None
            except (KeyError, tk.TclError):
                gone = False
            if gone and self.glv is not None:
                self.glv.set_fly(False)
        self.after(100, check)

    def _view_key_up(self, e):
        if self.glv is not None and self.glv.key_up(e.keysym):
            return "break"

    def _build_viewer_pane(self):
        frame = tk.Frame(self.body, bg=C["bg0"])
        self.pane_viewer = frame
        head = tk.Frame(frame, bg=C["bg0"])
        head.pack(fill="x", pady=(0, 8))
        small = dict(font=F["small"], padx=10, pady=3)
        # right: actions
        Button(head, "Open in browser", self._viewer_in_browser, **small).pack(side="right")
        Button(head, "Screenshot", self._view_screenshot, **small).pack(side="right", padx=(0, 6))
        Button(head, "Export textures…", self._view_export_dialog, variant="primary", **small).pack(
            side="right", padx=(0, 6))
        # left: what to show and how
        self.dd_view_model = Dropdown(head, "Model", self.v_view_model, [("", "—")], width=22,
                                      command=self._view_pick)
        self.dd_view_model.pack(side="left")
        seg = Segmented(head, self.v_view_mode, [("lit", "Lit"), ("colour", "Colour"), ("normals", "Normals"),
                                                 ("clay", "Clay")])
        seg.pack(side="left", padx=(8, 0), fill="y")
        for lbl in seg.items.values():
            lbl.configure(font=F["small"], padx=9, pady=4)
        self.v_view_map = tk.StringVar(value="")
        Dropdown(head, "Map", self.v_view_map,
                 [("", "— (shaded)"), ("tex_base", "Colour map"), ("tex_normal", "Normal map"),
                  ("tex_mr", "Metal / Rough / AO map")], width=11,
                 command=lambda: self.v_view_mode.set(self.v_view_map.get() or "lit")).pack(side="left", padx=(8, 0))
        self.v_view_mode.trace_add("write", lambda *_: self.v_view_map.set(
            self.v_view_mode.get() if self.v_view_mode.get().startswith("tex_") else ""))
        Check(head, self.v_view_wire, "Wireframe", bg=C["bg0"], command=self._view_apply).pack(side="left",
                                                                                                padx=(12, 0))
        Check(head, self.v_view_skel, "Skeleton", bg=C["bg0"], command=self._view_apply).pack(side="left",
                                                                                               padx=(8, 0))
        Check(head, self.v_view_grid, "Grid", bg=C["bg0"], command=self._view_apply).pack(side="left", padx=(8, 0))
        Dropdown(head, "BG", self.v_view_bg, [("dark", "Dark"), ("grey", "Grey"), ("light", "Light")],
                 width=5, command=self._view_apply).pack(side="left", padx=(10, 0))
        self.v_view_mode.trace_add("write", self._view_apply)
        body = tk.Frame(frame, bg=C["bg0"])
        body.pack(fill="both", expand=True)

        # ---- sidebar: parts / bones / info
        side = tk.Frame(body, bg=C["bg1"], width=round(380 * SCALE), highlightthickness=1,
                        highlightbackground=C["border2"])
        side.pack(side="right", fill="y", padx=(8, 0))
        side.pack_propagate(False)
        inner = tk.Frame(side, bg=C["bg1"], padx=12, pady=12)
        inner.pack(fill="both", expand=True)
        tk.Label(inner, textvariable=self.v_view_status, bg=C["bg1"], fg=C["text3"], font=F["small"], anchor="w",
                 justify="left", wraplength=round(340 * SCALE)).pack(fill="x", pady=(0, 8))
        seg_row = tk.Frame(inner, bg=C["bg1"])
        seg_row.pack(fill="x", pady=(8, 8))
        self._view_extract_btn = Button(seg_row, "Extract", self._view_extract_aircraft, variant="primary",
                                        font=F["small"], padx=12, pady=5)  # shown only when liveries are missing
        self.view_panel_seg = Segmented(seg_row, self.v_view_panel, [("parts", "Parts"), ("bones", "Bones"),
                                                                   ("info", "Info")])
        self.view_panel_seg.pack(side="left")
        host = tk.Frame(inner, bg=C["bg1"])
        host.pack(fill="both", expand=True)
        # parts
        parts = tk.Frame(host, bg=C["bg1"])
        row = tk.Frame(parts, bg=C["bg1"])
        row.pack(fill="x", pady=(0, 4))
        Button(row, "Show all", lambda: self._view_parts_all(True), **small).pack(side="left")
        Button(row, "Hide all", lambda: self._view_parts_all(False), **small).pack(side="left", padx=(6, 0))
        Button(row, "All textures on", self._view_tex_all_on, **small).pack(side="left", padx=(6, 0))
        row2 = tk.Frame(parts, bg=C["bg1"])
        row2.pack(fill="x", pady=(0, 8))
        Button(row2, "Expand all", lambda: self._view_expand_all(True), **small).pack(side="left")
        Button(row2, "Collapse all", lambda: self._view_expand_all(False), **small).pack(side="left", padx=(6, 0))
        pv = tk.Frame(parts, bg=C["bg1"])
        pv.pack(fill="x", pady=(0, 6))
        self.view_tex_preview = tk.Label(pv, bg=C["bg0"], fg=C["text3"], font=F["small"], image=self._blank_thumb(),
                                         width=round(96 * SCALE), height=round(96 * SCALE), compound="center",
                                         wraplength=round(90 * SCALE))
        self.view_tex_preview.pack(side="left")
        self.view_tex_preview.bind("<Button-1>", lambda e: self._view_tex_big())
        self.v_view_tex_info = tk.StringVar(value="")
        tk.Label(pv, textvariable=self.v_view_tex_info, bg=C["bg1"], fg=C["text2"], font=F["small"], justify="left",
                 anchor="nw", wraplength=round(230 * SCALE)).pack(side="left", fill="both", expand=True, padx=(10, 0))
        _c, self.view_parts = self._scrollable(parts, C["bg1"])
        # bones
        bones = tk.Frame(host, bg=C["bg1"])
        self.view_bone_name = tk.StringVar(value="Open a model")
        tk.Label(bones, textvariable=self.view_bone_name, bg=C["bg1"], fg=C["text1"], font=F["bold"],
                 anchor="w").pack(fill="x")
        # the selected bone's pose as numbers: type a value (Enter), or scroll over a field (Shift: finer)
        self.v_bone = [tk.StringVar(value="0") for _ in range(6)]  # translation X Y Z, rotation X Y Z
        cols = ("#ff3352", "#8bdc00", "#2890ff")

        def field(parent, i_, unit=""):
            cell = tk.Frame(parent, bg=C["bg2"], highlightthickness=1, highlightbackground=C["border2"],
                            highlightcolor=C["accent"])
            ent = tk.Entry(cell, textvariable=self.v_bone[i_], bg=C["bg2"], fg=C["text1"], bd=0, relief="flat",
                           insertbackground=C["accent"], font=F["mono_s"], width=7, justify="right",
                           highlightthickness=0)
            for seq in ("<Return>", "<KP_Enter>", "<FocusOut>"):
                ent.bind(seq, lambda e, i_=i_: self._view_bone_field(i_))
            ent.bind("<MouseWheel>", lambda e, i_=i_: self._view_bone_step(i_, e.delta > 0, e))
            ent.bind("<Button-4>", lambda e, i_=i_: self._view_bone_step(i_, True, e))
            ent.bind("<Button-5>", lambda e, i_=i_: self._view_bone_step(i_, False, e))
            return cell, ent

        # translation: three fields in a row
        tk.Label(bones, text="Translation", bg=C["bg1"], fg=C["text3"], font=F["small"], anchor="w").pack(
            fill="x", pady=(6, 1))
        r_ = tk.Frame(bones, bg=C["bg1"])
        r_.pack(fill="x")
        for n, axis in enumerate("XYZ"):
            cell, ent = field(r_, n)
            cell.pack(side="left", fill="x", expand=True, padx=(0 if n == 0 else 4, 0))
            tk.Label(cell, text=axis, bg=C["bg2"], fg=cols[n], font=F["bold"], padx=6).pack(side="left")
            ent.pack(side="left", fill="x", expand=True, ipady=3)
            tk.Frame(cell, bg=C["bg2"], width=4).pack(side="left")
        # rotation: a slider and a field per axis
        tk.Label(bones, text="Rotation", bg=C["bg1"], fg=C["text3"], font=F["small"], anchor="w").pack(
            fill="x", pady=(8, 1))
        self.view_rot = []
        for n, axis in enumerate("XYZ"):
            r_ = tk.Frame(bones, bg=C["bg1"])
            r_.pack(fill="x", pady=1)
            tk.Label(r_, text=axis, bg=C["bg1"], fg=cols[n], font=F["bold"], width=2).pack(side="left")
            cell, ent = field(r_, 3 + n)
            cell.pack(side="right")
            ent.pack(side="left", ipady=2)
            tk.Label(cell, text="°", bg=C["bg2"], fg=C["text3"], font=F["small"]).pack(side="left", padx=(0, 4))
            sc = tk.Scale(r_, from_=-180, to=180, orient="horizontal", resolution=1, showvalue=False,
                          bg=C["accent"], troughcolor=C["border2"], activebackground=C["accent_hover"],
                          highlightthickness=0, bd=0, sliderrelief="flat", sliderlength=round(14 * SCALE),
                          width=round(10 * SCALE), command=lambda v, n=n: self._view_bone_slider(n, float(v)))
            sc.pack(side="left", fill="x", expand=True, padx=(0, 8), pady=4)
            self.view_rot.append(sc)
        rr = tk.Frame(bones, bg=C["bg1"])
        rr.pack(fill="x", pady=(4, 6))
        Button(rr, "Reset rotation", lambda: self._view_reset_bone(rotation=True), **small).pack(side="left")
        Button(rr, "Reset translation", lambda: self._view_reset_bone(translation=True), **small).pack(
            side="left", padx=(6, 0))
        Button(rr, "Reset all bones", self._view_reset_pose, **small).pack(side="right")
        lbf = tk.Frame(bones, bg=C["bg2"], highlightthickness=1, highlightbackground=C["border2"])
        lbf.pack(fill="both", expand=True)
        self.view_bones = tk.Listbox(lbf, bg=C["bg2"], fg=C["text2"], selectbackground=C["accent"],
                                     selectforeground="#fff", highlightthickness=0, bd=0, activestyle="none",
                                     font=F["mono_s"], exportselection=False)
        sb = ttk.Scrollbar(lbf, orient="vertical", command=self.view_bones.yview, style="Dark.Vertical.TScrollbar")
        self.view_bones.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.view_bones.pack(side="left", fill="both", expand=True)
        self.view_bones.bind("<<ListboxSelect>>", self._view_bone_selected)
        self._view_bone = None
        # info
        info = tk.Frame(host, bg=C["bg1"])
        self.v_view_info = tk.StringVar(value="Open a model to see its details.")
        tk.Label(info, textvariable=self.v_view_info, bg=C["bg1"], fg=C["text2"], font=F["mono_s"], justify="left",
                 anchor="nw", wraplength=round(340 * SCALE)).pack(fill="both", expand=True)
        panels = {"parts": parts, "bones": bones, "info": info}

        def show_panel(*_):
            for k, f in panels.items():
                f.pack_forget()
            panels.get(self.v_view_panel.get(), parts).pack(fill="both", expand=True)

        self.v_view_panel.trace_add("write", show_panel)
        show_panel()

        # ---- the 3D view
        box = tk.Frame(body, bg=C["bg0"], highlightthickness=1, highlightbackground=C["border2"])
        box.pack(side="left", fill="both", expand=True)
        self.view_host = box
        self.view_placeholder = tk.Label(
            box, text="Press Preview 3D (Assets / Catalog) or View 3D on an extraction to show a model here.",
            bg=C["bg0"], fg=C["text3"], font=F["base"], justify="center")
        self.view_placeholder.place(relx=0.5, rely=0.5, anchor="center")
        self._build_aircraft_overlay(box)
        self.bind("<KeyPress>", self._view_key, add=True)
        self.bind("<KeyRelease>", self._view_key_up, add=True)
        self.bind("<FocusOut>", self._view_focus_out, add=True)

    # ---------------------------------------------------------- aircraft overlay (top-right of the 3D view)
    EMBLEM_SLOTS = [("TailLeft", "Tail, left"), ("TailRight", "Tail, right"), ("WingLeft", "Wing top, left"),
                    ("WingRight", "Wing top, right"), ("WingBottomLeft", "Wing bottom, left"),
                    ("WingBottomRight", "Wing bottom, right"), ("FrontBodyLeft", "Nose side, left"),
                    ("FrontBodyRight", "Nose side, right"), ("Add01Left", "Extra, left"), ("Add01Right", "Extra, right")]
    SLOT_ROWS = {"Tail": 0, "Wing": -1, "WingBottom": -2, "FrontBody": -3, "Add01": -4}
    # <Slot>DecalAreaIndex: where on the aircraft a slot's emblem is drawn (the game's area numbers)
    EMBLEM_AREAS = [("0", "Tail"), ("2", "Wing top"), ("1", "Wing bottom"), ("3", "Nose side"),
                    ("4", "Extra")]

    def _build_aircraft_overlay(self, host):
        ov = tk.Frame(host, bg=C["bg1"], highlightthickness=1, highlightbackground=C["border3"])
        self.view_aircraft = ov
        self._ov_editing = False
        self._view_edit = None
        head = tk.Frame(ov, bg=C["bg1"])
        head.pack(fill="x", padx=10, pady=(7, 5))
        tk.Label(head, text="AIRCRAFT", bg=C["bg1"], fg=C["text3"], font=F["tiny"]).pack(side="left")
        hide = tk.Label(head, text="hide", bg=C["bg1"], fg=C["text3"], font=F["small"], cursor="hand2")
        hide.pack(side="right")
        self._ov_edit_btn = tk.Label(head, text="Edit livery", bg=C["bg1"], fg=C["accent"], font=F["small"],
                                     cursor="hand2")
        self._ov_edit_btn.pack(side="right", padx=(0, 12))
        self._ov_edit_btn.bind("<Button-1>", lambda e: self._ov_toggle_editor())
        body = tk.Frame(ov, bg=C["bg1"])
        body.pack(fill="x", padx=10, pady=(0, 9))

        def toggle_body(_e=None):
            if body.winfo_ismapped():
                body.pack_forget()
                if self._ov_editing:
                    self._ov_editor.pack_forget()
                hide.configure(text="show")
            else:
                body.pack(fill="x", padx=10, pady=(0, 9))
                if self._ov_editing:
                    self._ov_editor.pack(fill="x", padx=10, pady=(0, 10))
                hide.configure(text="hide")

        hide.bind("<Button-1>", toggle_body)
        self.dd_view_livery = Dropdown(body, "Livery", self.v_view_livery, [("", "As exported")], width=13,
                                       command=self._view_set_livery)
        self.dd_view_livery.pack(fill="x")
        self._ov_icon = tk.Label(body, bg=C["bg1"], bd=0)  # the game's own skin icon for the picked livery
        arow = tk.Frame(body, bg=C["bg1"])
        arow.pack(fill="x", pady=(6, 0))
        reload_ = lambda: self._view_model is not None and self._view_load(Path(self.v_view_model.get()), True)
        self.dd_view_gear = Dropdown(arow, "Gear", self.v_view_gear, [("", "None")], width=4, command=reload_)
        self.dd_view_gear.pack(side="left")
        self.dd_view_probe = Dropdown(arow, "Probe", self.v_view_probe, [("", "None")], width=7, command=reload_)
        self.dd_view_probe.pack(side="left", padx=(6, 0))
        self.dd_view_pylons = Dropdown(body, "Pylons", self.v_view_pylons, [("", "None")], width=8, command=reload_)
        self.dd_view_pylons.pack(fill="x", pady=(6, 0))
        Button(body, "Export textures with this livery…", self._view_export_dialog, font=F["small"], padx=8,
               pady=3).pack(fill="x", pady=(6, 0))
        self._build_livery_editor(ov)

    def _build_livery_editor(self, ov):
        # the editor scrolls inside the viewport (mouse wheel) so its lower controls are always reachable
        wrap = tk.Frame(ov, bg=C["bg1"])
        self._ov_editor = wrap
        cv = tk.Canvas(wrap, bg=C["bg1"], highlightthickness=0, bd=0)
        cv.pack(side="left", fill="both", expand=True)
        bar = tk.Frame(wrap, bg=C["bg3"], width=3)
        ed = tk.Frame(cv, bg=C["bg1"])
        cv.create_window(0, 0, window=ed, anchor="nw")
        self.scroll_canvases.append(cv)

        def fit(_e=None):
            w, h = ed.winfo_reqwidth(), ed.winfo_reqheight()
            host = ov.master
            top = wrap.winfo_y() if wrap.winfo_ismapped() else 200
            avail = max(140, host.winfo_height() - 10 - top - 16)
            cv.configure(width=w, height=min(h, avail), scrollregion=(0, 0, w, h))
            if h > avail:
                bar.pack(side="right", fill="y", padx=(4, 0))
            else:
                bar.pack_forget()
                cv.yview_moveto(0)

        ed.bind("<Configure>", fit)
        ov.master.bind("<Configure>", fit, add="+")
        wrap.bind("<Map>", lambda e: self.after(30, fit), add="+")
        small = dict(font=F["small"], padx=8, pady=2)

        def cap(parent, text):
            tk.Label(parent, text=text, bg=C["bg1"], fg=C["text3"], font=F["tiny"], anchor="w").pack(
                fill="x", pady=(8, 3))

        def slider(parent, label, frm, to, res, cmd):
            r = tk.Frame(parent, bg=C["bg1"])
            r.pack(fill="x")
            tk.Label(r, text=label, bg=C["bg1"], fg=C["text3"], font=F["small"], width=7, anchor="w").pack(side="left")
            val = tk.Label(r, text="", bg=C["bg1"], fg=C["text2"], font=F["small"], width=5, anchor="e")
            val.pack(side="right")
            sc = tk.Scale(r, from_=frm, to=to, resolution=res, orient="horizontal", showvalue=False,
                          bg=C["accent"], troughcolor=C["border2"], activebackground=C["accent_hover"],
                          highlightthickness=0, bd=0, sliderrelief="flat", sliderlength=round(12 * SCALE),
                          width=round(8 * SCALE))
            sc.pack(side="left", fill="x", expand=True, pady=2)
            sc.configure(command=lambda v: (val.configure(text=f"{float(v):.2f}"), cmd(float(v))))
            sc._val = val
            return sc

        # number
        cap(ed, "NUMBER")
        r = tk.Frame(ed, bg=C["bg1"])
        r.pack(fill="x")
        self.v_ed_digits = tk.StringVar()
        e = tk.Entry(r, textvariable=self.v_ed_digits, width=5, bg=C["bg2"], fg=C["text1"], bd=0, relief="flat",
                     insertbackground=C["accent"], font=F["bold"], highlightthickness=1,
                     highlightbackground=C["border2"], highlightcolor=C["accent"], justify="center")
        e.pack(side="left", ipady=3)
        self.v_ed_digits.trace_add("write", lambda *_: self._ed_set_digits())
        self.v_ed_show = {}
        for area in ("Tail", "Body", "Wing"):
            v = tk.BooleanVar(value=True)
            self.v_ed_show[area] = v
            Check(r, v, area, command=lambda a=area: self._ed_area_set("show", self.v_ed_show[a].get(), a)).pack(
                side="left", padx=(8, 0))
        self.v_ed_area = tk.StringVar(value="Tail")
        Segmented(ed, self.v_ed_area, [("Tail", "Tail"), ("Body", "Nose"), ("Wing", "Wing")], compact=True).pack(
            anchor="w", pady=(6, 2))
        self.v_ed_area.trace_add("write", lambda *_: self._ov_fill_editor())
        self._ed_size = slider(ed, "Scale", 0.1, 3.0, 0.01, lambda v: self._ed_area_set("scale", v, echo=True))
        # the game's offsets: where the number starts on each side, and how far up its bottom is
        self._ed_xl = slider(ed, "Left X", 0.0, 1.2, 0.01, lambda v: self._ed_area_set("x_left", v, echo=True))
        self._ed_xr = slider(ed, "Right X", 0.0, 1.2, 0.01, lambda v: self._ed_area_set("x_right", v, echo=True))
        self._ed_y = slider(ed, "Up", 0.0, 1.2, 0.01, lambda v: self._ed_area_set("y_up", v, echo=True))
        r = tk.Frame(ed, bg=C["bg1"])
        r.pack(fill="x", pady=(4, 0))
        tk.Label(r, text="Colour", bg=C["bg1"], fg=C["text3"], font=F["small"], width=7, anchor="w").pack(side="left")
        self._ed_col = {}
        for side, text in (("color_L", "left"), ("color_R", "right")):
            sw = tk.Label(r, text=text, bg="#111111", fg="#ffffff", font=F["small"], width=6, cursor="hand2")
            sw.pack(side="left", padx=(0, 6))
            sw.bind("<Button-1>", lambda e, k=side: self._ed_pick_number_colour(k))
            self._ed_col[side] = sw
        self.v_ed_font = tk.StringVar(value="0")
        self.dd_ed_font = Dropdown(r, "Font", self.v_ed_font, [("0", "1")], width=3,
                                   command=lambda: self._ed_area_set("font", int(self.v_ed_font.get())))
        self.dd_ed_font.pack(side="left")
        # emblems
        cap(ed, "EMBLEMS")
        self.v_ed_slot = tk.StringVar(value="TailLeft")
        self.v_ed_emblem = tk.StringVar(value="")
        Dropdown(ed, "Slot", self.v_ed_slot, self.EMBLEM_SLOTS, width=13, command=self._ov_fill_editor).pack(
            fill="x")
        self.dd_ed_emblem = Dropdown(ed, "Emblem", self.v_ed_emblem, [("", "None")], width=11,
                                     command=self._ed_set_emblem)
        self.dd_ed_emblem.pack(fill="x", pady=(5, 0))
        self._ed_escale = slider(ed, "Size", 0.02, 1.0, 0.01, self._ed_set_emblem_scale)
        self.v_ed_earea = tk.StringVar(value="0")
        self.dd_ed_earea = Dropdown(ed, "Drawn on", self.v_ed_earea, self.EMBLEM_AREAS, width=11,
                                    command=lambda: self._ed_set_decal("area", int(self.v_ed_earea.get())))
        self.dd_ed_earea.pack(fill="x", pady=(5, 2))
        self._ed_etx = slider(ed, "Move X", -0.6, 0.6, 0.01, lambda v: self._ed_set_decal("tx", v))
        self._ed_ety = slider(ed, "Move Y", -0.6, 0.6, 0.01, lambda v: self._ed_set_decal("ty", v))
        r = tk.Frame(ed, bg=C["bg1"])
        r.pack(fill="x")
        self.v_ed_eflip = tk.BooleanVar(value=False)
        Check(r, self.v_ed_eflip, "Mirror on the right side",
              command=lambda: self._ed_set_decal("flip", self.v_ed_eflip.get())).pack(side="left")
        # paint
        self._ed_paint = tk.Frame(ed, bg=C["bg1"])
        cap(self._ed_paint, "PAINT COLOURS")
        r = tk.Frame(self._ed_paint, bg=C["bg1"])
        r.pack(fill="x")
        self._ed_layers = []
        for i in range(3):
            sw = tk.Label(r, text=f"Layer {i + 1}", bg="#111111", fg="#ffffff", font=F["small"], width=8,
                          cursor="hand2")
            sw.pack(side="left", padx=(0, 6))
            sw.bind("<Button-1>", lambda e, k=i: self._ed_pick_layer(k))
            self._ed_layers.append(sw)
        # colour texture
        cap(ed, "COLOUR TEXTURE (tail colours…)")
        self._ed_base_cap = ed.winfo_children()[-1]
        self.v_ed_base = tk.StringVar(value="")
        self.dd_ed_base = Dropdown(ed, "Use", self.v_ed_base, [("", "—")], width=14, command=self._ed_set_base)
        self.dd_ed_base.pack(fill="x")
        r = tk.Frame(ed, bg=C["bg1"])
        r.pack(fill="x", pady=(10, 0))
        Button(r, "Save as…", self._ed_save, variant="primary", **small).pack(side="left")
        Button(r, "Reset", self._ed_reset, **small).pack(side="left", padx=(6, 0))
        tk.Label(ed, text="All values are the game's, drawn with the\ngame's own number maths (Left X = offsetX_L,\nRight X = offsetX_R, Up = offsetY).", bg=C["bg1"], fg=C["text3"],
                 font=F["small"], anchor="w", justify="left").pack(fill="x", pady=(6, 0))

    def _ov_toggle_editor(self):
        if not self._view_liv_data and not self._ov_editing:
            self.v_view_status.set("Liveries need the whole aircraft extracted (select the aircraft folder, Extract).")
            return
        self._ov_editing = not self._ov_editing
        if self._ov_editing:
            if not self.v_view_livery.get():  # start from the first livery
                first = (self._view_liv_data.get("liveries") or [{}])[0].get("id", "")
                if first:
                    self.v_view_livery.set(first)
                    self._view_set_livery()
            self._ov_editor.pack(fill="x", padx=10, pady=(0, 10))
            self._ov_edit_btn.configure(text="Done editing")
            self._ov_fill_editor()
        else:
            self._ov_editor.pack_forget()
            self._ov_edit_btn.configure(text="Edit livery")
        self.view_aircraft.lift()

    @staticmethod
    def _lin_to_hex(c):
        return "#%02x%02x%02x" % tuple(int(round(255 * min(max(float(v), 0.0), 1.0) ** (1 / 2.2))) for v in c[:3])

    @staticmethod
    def _hex_to_lin(h):
        return [round((int(h[i:i + 2], 16) / 255.0) ** 2.2, 4) for i in (1, 3, 5)]

    def _ov_fill_editor(self):
        ed = self._view_edit
        if not self._ov_editing or ed is None:
            return
        self._ed_sync = True
        try:
            nums = ed.get("numbers") or {}
            if self.v_ed_digits.get() != nums.get("digits", ""):
                self.v_ed_digits.set(nums.get("digits", ""))
            area = nums.get("areas", {}).get(self.v_ed_area.get(), {})
            for a, v in self.v_ed_show.items():
                v.set(nums.get("areas", {}).get(a, {}).get("show", True))
            if "x_left" not in area and area:  # saved by an older version: start from the game's values
                g_ = area.get("game") or {}
                area.update({"x_left": g_.get("offsetX_L", 0.5), "x_right": g_.get("offsetX_R", 0.5),
                             "y_up": g_.get("offsetY", 0.5)})
            for sc, key, dflt in ((self._ed_size, "scale", 1.0), (self._ed_xl, "x_left", 0.5),
                                  (self._ed_xr, "x_right", 0.5), (self._ed_y, "y_up", 0.5)):
                sc.set(area.get(key, dflt))
                sc._val.configure(text=f"{float(area.get(key, dflt)):.2f}")
            for k, sw in self._ed_col.items():
                c = area.get(k) or [0.05, 0.05, 0.05]
                sw.configure(bg=self._lin_to_hex(c), fg="#000000" if sum(c[:3]) > 1.2 else "#ffffff")
            import ac8_glview
            sheet = ac8_glview.font_sheet(ed.get("_font_png"))
            n_fonts = len(sheet[1])  # Font_Select = the sheet's column
            self.dd_ed_font.set_options([(str(i), str(i + 1)) for i in range(n_fonts)])
            self.v_ed_font.set(str(min(int(area.get("font", 0)), n_fonts - 1)))
            emblems = (self._view_liv_data or {}).get("emblems", {})
            self.dd_ed_emblem.set_options([("", "None")] + [(n, n) for n in sorted(emblems)])
            d = ed.get("decals", {}).get(self.v_ed_slot.get())
            self.v_ed_emblem.set(d["texture"] if d else "")
            size = 0.2 * float(d.get("scale", 1.0) or 1.0) if d else 0.2  # share of its area: DecalScale / 5
            self._ed_escale.set(size)
            self._ed_escale._val.configure(text=f"{size:.2f}")
            self.v_ed_earea.set(str(d.get("area", 0)) if d else "0")
            for sc, key in ((self._ed_etx, "tx"), (self._ed_ety, "ty")):
                v = float(d.get(key, 0.0)) if d else 0.0
                sc.set(v)
                sc._val.configure(text=f"{v:.2f}")
            self.v_ed_eflip.set(bool(d.get("flip")) if d else False)
            if "LayerMaskMap" in ed.get("textures", {}):
                self._ed_paint.pack(fill="x", before=self._ed_base_cap)
                for i, sw in enumerate(self._ed_layers):
                    c = ed.get("colors", {}).get(f"Layer0{i + 1}_Color", [0.5, 0.5, 0.5])
                    sw.configure(bg=self._lin_to_hex(c), fg="#000000" if sum(c[:3]) > 1.2 else "#ffffff")
            else:
                self._ed_paint.pack_forget()
            bases = {}
            for lv in (self._view_liv_data or {}).get("liveries", []):
                t = lv.get("textures", {}).get("Basecolor")
                if t and t.get("files"):
                    bases.setdefault(t["texture"], lv["id"])
            self.dd_ed_base.set_options([(n, f"{n}  ({lid})") for n, lid in sorted(bases.items())])
            self.v_ed_base.set(ed.get("textures", {}).get("Basecolor", {}).get("texture", ""))
        finally:
            self._ed_sync = False

    # ---- edits
    def _ed_area_set(self, key, value, area=None, echo=False):
        if getattr(self, "_ed_sync", False) or self._view_edit is None:
            return
        areas = self._view_edit.setdefault("numbers", {}).setdefault("areas", {})
        cur = areas.get(area or self.v_ed_area.get(), {}).get(key)
        if echo and isinstance(cur, (int, float)) and abs(float(cur) - float(value)) < 0.006:
            return  # a slider repeating the value it was just given
        areas.setdefault(area or self.v_ed_area.get(), {})[key] = value
        if key == "show" and value:  # the livery's own shader had no numbers: drawing them is an edit
            self._view_edit["numbers"]["enabled"] = True
        self._ed_refresh()

    def _ed_set_digits(self):
        if getattr(self, "_ed_sync", False) or self._view_edit is None:
            return
        digits = "".join(c for c in self.v_ed_digits.get() if c.isdigit())[:3]
        self._view_edit.setdefault("numbers", {})["digits"] = digits
        self._ed_refresh()

    def _ed_pick_number_colour(self, key):
        from tkinter import colorchooser
        area = (self._view_edit or {}).get("numbers", {}).get("areas", {}).get(self.v_ed_area.get(), {})
        res = colorchooser.askcolor(color=self._lin_to_hex(area.get(key) or [0.05] * 3), parent=self,
                                    title=f"{self.v_ed_area.get()} number colour ({key[-1]})")
        if res and res[1]:
            self._ed_area_set(key, self._hex_to_lin(res[1]))
            self._ov_fill_editor()

    def _ed_pick_layer(self, i):
        from tkinter import colorchooser
        cols = self._view_edit.setdefault("colors", {})
        res = colorchooser.askcolor(color=self._lin_to_hex(cols.get(f"Layer0{i + 1}_Color", [0.5] * 3)),
                                    parent=self, title=f"Paint layer {i + 1}")
        if res and res[1]:
            cols[f"Layer0{i + 1}_Color"] = self._hex_to_lin(res[1]) + [1.0]
            self._ov_fill_editor()
            self._ed_refresh()

    def _ed_set_emblem(self):
        if getattr(self, "_ed_sync", False) or self._view_edit is None:
            return
        slot, name = self.v_ed_slot.get(), self.v_ed_emblem.get()
        decals = self._view_edit.setdefault("decals", {})
        if not name:
            decals.pop(slot, None)
        else:
            base = slot[:-4] if slot.endswith("Left") else slot[:-5]
            files = (self._view_liv_data or {}).get("emblems", {}).get(name, {})
            old = decals.get(slot, {})
            import ac8_livery
            place = ac8_livery.decal_place(base, self._view_edit.get("scalars") or {})  # the livery's own values
            place.update({k: old[k] for k in ("scale", "tx", "ty", "flip", "area", "area_row") if k in old})
            decals[slot] = dict({"texture": name, "files": files, "row": self.SLOT_ROWS.get(base),
                                 "col": -1 if slot.endswith("Left") else 0}, **place)
        self._ed_refresh()

    def _ed_set_emblem_scale(self, v):
        if getattr(self, "_ed_sync", False) or self._view_edit is None:
            return
        d = self._view_edit.get("decals", {}).get(self.v_ed_slot.get())
        if d and abs(0.2 * float(d.get("scale", 1.0) or 1.0) - v) >= 0.01:
            d["scale"] = v / 0.2
            self._ed_refresh()

    def _ed_set_decal(self, key, value):
        """Move / re-place the selected slot's emblem (the game's DecalTranslate / AreaIndex / FlipRight)."""
        if getattr(self, "_ed_sync", False) or self._view_edit is None:
            return
        d = self._view_edit.get("decals", {}).get(self.v_ed_slot.get())
        if not d:
            return
        if isinstance(value, float) and abs(float(d.get(key, 0.0)) - value) < 0.005:
            return  # a slider repeating the value it was just given
        d[key] = value
        if key == "area":
            import ac8_livery
            d["area_row"] = ac8_livery.AREA_ROWS.get(value, -value)
        self._ed_refresh()

    def _ed_set_base(self):
        if getattr(self, "_ed_sync", False) or self._view_edit is None:
            return
        name = self.v_ed_base.get()
        for lv in (self._view_liv_data or {}).get("liveries", []):
            t = lv.get("textures", {}).get("Basecolor")
            if t and t["texture"] == name:
                self._view_edit.setdefault("textures", {})["Basecolor"] = t
                break
        self._ed_refresh(textures=True)

    def _ed_reset(self):
        self._view_edit = None
        self._view_livery_entry()
        self._ov_fill_editor()
        self._ed_refresh(textures=True)

    def _ed_save(self):
        from tkinter import simpledialog
        if self._view_edit is None or not self._view_liv_data:
            return
        name = simpledialog.askstring(APP_NAME, "Name for this livery:", parent=self,
                                      initialvalue=self._view_edit.get("custom_name", ""))
        if not name:
            return
        import copy
        entry = copy.deepcopy(self._view_edit)
        entry.pop("_font_png", None)
        entry["id"], entry["custom_name"] = "custom:" + name, name
        data = self._view_liv_data
        data["custom"] = [c for c in data.get("custom", []) if c.get("custom_name") != name] + [entry]
        path = self._view_liv_final / "Liveries" / f"{data['aircraft']}.json"
        try:
            path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Couldn't save the livery:\n{e}")
            return
        self._view_fill_aircraft(Path(self.v_view_model.get()))
        self.v_view_livery.set(entry["id"])
        self._view_edit = None
        self._view_livery_entry()
        self._log(f"✔ Saved livery “{name}” in {path.name}")
        self.v_view_status.set(f"Saved livery “{name}”")

    def _ed_refresh(self, textures=False):
        """Redraw with the edited livery: emblems / numbers / colours update quickly (the big textures stay)."""
        if getattr(self, "_ed_job", None):
            self.after_cancel(self._ed_job)
        self._ed_job = self.after(120, lambda: self._ed_rebuild(textures))

    def _ed_rebuild(self, textures):
        self._ed_job = None
        if self.glv is None or self._view_model is None or self._view_edit is None:
            return
        import ac8_glview
        model, final, edit = self._view_model, self._view_liv_final, self._view_edit
        self._view_token += 1
        tok = self._view_token

        def work():
            try:
                spec, err = ac8_glview.livery_spec(model, edit, final, textures=textures), None
            except Exception as e:
                SLOG.exc("Viewer: livery")
                spec, err = None, e
            self.post("call", lambda: self._view_livery_ready(tok, edit, spec, err))

        threading.Thread(target=work, daemon=True).start()

    def _viewer_in_browser(self):
        if not self._view_files:
            messagebox.showinfo(APP_NAME, "Preview or view a model first.")
            return
        import webbrowser
        if self.viewer_srv is None:
            import ac8_viewer
            self.viewer_srv = ac8_viewer.ViewerServer()
        cur = Path(self.v_view_model.get()) if self.v_view_model.get() not in ("", self.ALL_PARTS) else None
        self.viewer_srv.set_files(self._view_files, cur.stem if cur else self._view_first)
        webbrowser.open(self.viewer_srv.url)

    # ================================================================== textures tab

    def _build_textures(self):
        frame, box = self._panel("TEXTURES", self.v_tex_count)
        self.pane_textures = frame
        bar = tk.Frame(box, bg=C["bg1"], padx=8, pady=8)
        bar.pack(fill="x")
        self.dd_tex_src = Dropdown(bar, "From", self.v_tex_src, [("", "—")], width=26,
                                   command=self.load_textures)
        self.dd_tex_src.pack(side="left")
        Dropdown(bar, "Type", self.v_tex_role, TEX_ROLES, width=14, command=self.load_textures).pack(side="left",
                                                                                                    padx=(6, 0))
        Input(bar, self.v_tex_search, icon="⌕", placeholder="Filter by name…").pack(side="left", fill="x",
                                                                                     expand=True, padx=(6, 0))
        self.v_tex_search.trace_add("write", lambda *_: self._tex_search_soon())
        body = tk.Frame(box, bg=C["bg1"])
        body.pack(fill="both", expand=True)
        gridbox = tk.Frame(body, bg=C["bg1"])
        gridbox.pack(side="top", fill="both", expand=True)
        _c, self.tex_grid = self._scrollable(gridbox, C["bg1"])
        tk.Frame(body, bg=C["border2"], height=1).pack(fill="x")
        det = tk.Frame(body, bg=C["bg1"], padx=10, pady=8)
        det.pack(fill="x")
        self.tex_big = tk.Label(det, bg=C["bg0"], image=self._blank_thumb(), width=round(220 * SCALE),
                                height=round(220 * SCALE))
        self.tex_big.pack(side="left")
        side = tk.Frame(det, bg=C["bg1"], padx=12)
        side.pack(side="left", fill="both", expand=True)
        tk.Label(side, textvariable=self.v_tex_info, bg=C["bg1"], fg=C["text1"], font=F["small"], anchor="w",
                 justify="left", wraplength=480).pack(fill="x")
        chans = tk.Frame(side, bg=C["bg1"])
        chans.pack(fill="x", pady=(6, 6))
        tk.Label(chans, text="SHOW", bg=C["bg1"], fg=C["text3"], font=F["tiny"]).pack(side="left", padx=(0, 6))
        for ch in ("RGB", "R", "G", "B", "A"):
            Button(chans, ch, lambda c=ch: self._show_channel(c), font=F["small"], padx=8, pady=1).pack(
                side="left", padx=(0, 4))
        Button(chans, "Open file", self._open_tex, font=F["small"], padx=8, pady=1).pack(side="left", padx=(8, 0))
        pbr = tk.Frame(side, bg=C["bg1"])
        pbr.pack(fill="x", pady=(4, 0))
        tk.Label(pbr, text="PBR", bg=C["bg1"], fg=C["accent"], font=F["section"]).pack(side="left", padx=(0, 6))
        Dropdown(pbr, "Preset", self.v_pbr_preset, PBR_PRESETS[1:], width=22).pack(side="left")
        Dropdown(pbr, "Normal", self.v_pbr_normal, NORMAL_CONV, width=16).pack(side="left", padx=(6, 0))
        row2 = tk.Frame(side, bg=C["bg1"])
        row2.pack(fill="x", pady=(6, 0))
        Button(row2, "Convert this set", self.pbr_selected, variant="primary", font=F["small"], padx=10,
               pady=3).pack(side="left")
        Button(row2, "Convert all sets", self.pbr_all, font=F["small"], padx=10, pady=3).pack(side="left",
                                                                                            padx=(6, 0))
        Button(row2, "Open PBR folder", self._open_pbr_folder, font=F["small"], padx=10, pady=3).pack(
            side="left", padx=(6, 0))
        self._tex_selected = None
        self._tex_search_job = None

    def _tex_folders(self):
        return [f for f in self.scan_folders() if count_files(f / FINAL_DIR_NAME / "Textures", ".png")]

    def _fill_tex_sources(self):
        folders = self._tex_folders()
        opts = [(str(f), f.name) for f in folders]
        self.dd_tex_src.set_options(opts or [("", "— no textures exported yet —")])
        cur = self.v_tex_src.get()
        if self.sel_folder and str(self.sel_folder) in dict(opts):
            self.v_tex_src.set(str(self.sel_folder))
        elif cur not in dict(opts) and opts:
            self.v_tex_src.set(opts[0][0])

    def _tex_search_soon(self):
        if self._tex_search_job:
            self.after_cancel(self._tex_search_job)
        self._tex_search_job = self.after(300, self.load_textures)

    def load_textures(self):
        for w in self.tex_grid.winfo_children():
            w.destroy()
        src = self.v_tex_src.get()
        if not src:
            tk.Label(self.tex_grid, text="No exported textures yet — extract something with Convert on, then come "
                                         "back here.", bg=C["bg1"], fg=C["text3"], font=F["base"]).pack(pady=40)
            self.v_tex_count.set("")
            return
        root = Path(src) / FINAL_DIR_NAME / "Textures"
        role, q = self.v_tex_role.get(), self.v_tex_search.get().lower().strip()
        files = sorted(root.rglob("*.png"))
        files = [f for f in files if (role == "all" or texture_role_label(f) == role) and (not q or q in f.name.lower())]
        limit = 240
        self.v_tex_count.set(f"{len(files)} texture(s)" + (f" — showing first {limit}" if len(files) > limit else ""))
        cols = 5
        for c in range(cols):
            self.tex_grid.columnconfigure(c, weight=1, uniform="tex")
        self._tex_cells = {}
        for i, f in enumerate(files[:limit]):
            cell = tk.Frame(self.tex_grid, bg=C["bg2"], highlightthickness=1, highlightbackground=C["border1"],
                            cursor="hand2")
            cell.grid(row=i // cols, column=i % cols, padx=4, pady=4, sticky="nsew")
            # a blank image makes width/height count pixels (a text label counts characters/lines, which made
            # every cell ~1000x2000 px and pushed the grid past X11's 32767 px limit)
            img = tk.Label(cell, bg=C["bg0"], image=self._blank_thumb(), width=round(120 * SCALE),
                           height=round(120 * SCALE))
            img.pack(padx=4, pady=(4, 2))
            name = f.stem if len(f.stem) <= 26 else f.stem[:12] + "…" + f.stem[-12:]
            tk.Label(cell, text=name, bg=C["bg2"], fg=C["text2"], font=F["mono_s"]).pack()
            Pill(cell, texture_role_label(f).upper()).pack(pady=(2, 4))
            for w in (cell, img) + tuple(cell.winfo_children()):
                w.bind("<Button-1>", lambda e, p=f: self._select_tex(p))
            self._tex_cells[f] = img
        threading.Thread(target=self._make_thumbs, args=(files[:limit], Path(src)), daemon=True).start()

    def _blank_thumb(self):
        if getattr(self, "_blank_img", None) is None:
            self._blank_img = tk.PhotoImage(width=1, height=1)
        return self._blank_img

    def _make_thumbs(self, files, folder):
        try:
            from PIL import Image
        except ImportError:
            return
        cache = folder / ".thumbs2"  # v2: alpha-aware
        if (folder / ".thumbs").is_dir():  # old cache without alpha
            shutil.rmtree(folder / ".thumbs", ignore_errors=True)
        size = round(120 * SCALE)
        for f in files:
            if f in self.thumbs:
                self.post("call", lambda p=f: self._set_thumb(p))
                continue
            try:
                rel = f.relative_to(folder)
                tp = cache / rel.with_suffix(".jpg")
                if tp.exists() and tp.stat().st_mtime >= f.stat().st_mtime:
                    im = Image.open(tp)
                else:
                    im = Image.open(f)
                    im.draft("RGBA", (size * 2, size * 2))
                    im = im.convert("RGBA")
                    im.thumbnail((size, size))
                    # use the alpha (emblems, decals): show it over the panel colour instead of a solid box
                    bg = Image.new("RGBA", im.size, C["bg0"])
                    im = Image.alpha_composite(bg, im).convert("RGB")
                    tp.parent.mkdir(parents=True, exist_ok=True)
                    im.save(tp, "JPEG", quality=85)
                self.post("call", lambda p=f, i=im.copy(): self._set_thumb(p, i))
            except Exception:
                continue

    def _set_thumb(self, path, pil=None):
        lbl = getattr(self, "_tex_cells", {}).get(path)
        if lbl is None or not lbl.winfo_exists():
            return
        if pil is not None:
            from PIL import ImageTk
            self.thumbs[path] = ImageTk.PhotoImage(pil)
        if path in self.thumbs:
            lbl.configure(image=self.thumbs[path], width=round(120 * SCALE), height=round(120 * SCALE))

    def _select_tex(self, path):
        self._tex_selected = path
        for p, lbl in getattr(self, "_tex_cells", {}).items():
            try:
                lbl.master.configure(highlightbackground=C["accent"] if p == path else C["border1"])
            except tk.TclError:
                pass
        self._show_channel("RGB")

    def _show_channel(self, ch):
        path = self._tex_selected
        if not path:
            return
        try:
            from PIL import Image, ImageTk
            im = Image.open(path)
            info = f"{path.name}\n{im.size[0]} × {im.size[1]}  ·  {im.mode}  ·  {texture_role_label(path)}"
            rgba = im.convert("RGBA")
            if ch == "RGB":
                show = rgba
            else:
                show = rgba.getchannel("RGBA".index(ch)).convert("RGB")
            show = show.copy()
            show.thumbnail((round(220 * SCALE), round(220 * SCALE)))
            hint = {"Metal/Rough": "R = metallic · G = roughness · B = AO (in _MRO/_MROS; unused in _MRX)",
                    "Normal": "R/G = normal (DirectX) · B = AO in _NO textures, normal Z in _N"}.get(
                texture_role_label(path), "")
            self.v_tex_info.set(info + (f"\n{hint}" if hint else "") + f"\nShowing: {ch}")
            self._big_img = ImageTk.PhotoImage(show)
            self.tex_big.configure(image=self._big_img)
        except Exception as e:
            SLOG.exc(f"Textures: opening {path}")
            self.v_tex_info.set(f"Couldn't open {path.name}: {e}")

    def _open_tex(self):
        if self._tex_selected:
            open_folder(self._tex_selected)

    def _pbr_out(self):
        src = self.v_tex_src.get()
        return Path(src) / FINAL_DIR_NAME / f"PBR_{self.v_pbr_preset.get()}" if src else None

    def _open_pbr_folder(self):
        out = self._pbr_out()
        if out:
            open_folder(out)

    def pbr_selected(self):
        if not self._tex_selected:
            messagebox.showinfo(APP_NAME, "Click a texture first — its whole set (colour, normal, metal/rough) is "
                                          "converted together.")
            return
        m = re.match(r"^(.+)_[A-Za-z]+(?:\.\d{4})?$", self._tex_selected.stem)
        self._run_pbr({m.group(1)} if m else None)

    def pbr_all(self):
        self._run_pbr(None)

    def _run_pbr(self, only):
        src = self.v_tex_src.get()
        if not src:
            return
        if importlib.util.find_spec("PIL") is None or importlib.util.find_spec("numpy") is None:
            messagebox.showerror(APP_NAME, "Pillow and numpy are needed. Settings → Install packages.")
            return
        tex_dir, out = Path(src) / FINAL_DIR_NAME / "Textures", self._pbr_out()
        preset, normal = self.v_pbr_preset.get(), self.v_pbr_normal.get()

        def work():
            n = self._step_pbr(tex_dir, out, preset, normal, only)
            self.post("call", lambda: open_folder(out) if n else None)
            return f"✔ PBR maps written: {n}"

        self._start(work, "Making PBR maps…")

    # ================================================================== DCS World page

    DCS_MODES = [("ac8_pbr", "AC8 → PBR"), ("ac8_dcs", "AC8 → DCS"), ("pbr_dcs", "PBR → DCS"), ("dcs_pbr", "DCS → PBR")]
    DCS_HELP = {
        "ac8_pbr": "Ace Combat 8 textures (an extraction's PNGs) → standard PBR maps for Blender, Substance, Unreal, "
                   "Unity or glTF. Packed maps are split: _MRO → Metallic / Roughness / AO, _NO → Normal + AO.",
        "ac8_dcs": "Ace Combat 8 textures → DCS World sets: <name> (colour + alpha), <name>_Roughmet (R AO · G "
                   "roughness · B metallic) and <name>_Normal (DirectX).",
        "pbr_dcs": "PBR maps (BaseColor, Metallic, Roughness, AO or ORM, Normal, Opacity, Emissive) → DCS World sets. "
                   "_Normal_OpenGL / _Normal_DirectX in a name wins over the setting. Subfolders are kept.",
        "dcs_pbr": "DCS World sets (<name>, <name>_Roughmet, <name>_Normal) → BaseColor (+ Opacity), Metallic, "
                   "Roughness, AO (or one ORM map) and a normal map in the convention you pick.",
    }
    HELMET_SLOTS = [  # key, label, side, the endings its set-mates have
        ("ac8_color", "Colour  (_C)", "ac8", ("_c",)),
        ("ac8_normal", "Normal  (_N)", "ac8", ("_n", "_no")),
        ("ac8_mro", "Metal / rough  (_MRO)", "ac8", ("_mro", "_mrx")),
        ("dcs_color", "Diffuse", "dcs", ("",)),
        ("dcs_normal", "Normal  (_nm / _Normal)", "dcs", ("_nm", "_normal", "_n")),
        ("dcs_roughmet", "Roughmet", "dcs", ("_roughmet", "_rm")),
    ]

    def _build_dcs(self):
        self.v_dcs_count = tk.StringVar(value="")
        frame, box = self._panel("DCS WORLD", self.v_dcs_count)
        self.pane_dcs = frame
        _c, page = self._scrollable(box, C["bg1"])
        cfg = self.dcs_cfg
        self.v_dcs_mode = tk.StringVar(value=cfg.get("mode", "ac8_dcs"))
        self.v_dcs_src = tk.StringVar(value=cfg.get("src", ""))
        self.v_dcs_out = tk.StringVar(value=cfg.get("out", ""))
        self.v_dcs_extr = tk.StringVar(value="")
        self.v_dcs_fmt = tk.StringVar(value=cfg.get("fmt", "dds"))
        self.v_dcs_normal = tk.StringVar(value=cfg.get("normal", "opengl"))
        self.v_dcs_orm = tk.StringVar(value=cfg.get("layout", "separate"))
        self.v_dcs_help = tk.StringVar()

        def section(title, note=""):
            f = tk.Frame(page, bg=C["bg1"], padx=16)
            f.pack(fill="x", pady=(14, 0))
            tk.Label(f, text=title, bg=C["bg1"], fg=C["accent"], font=F["section"], anchor="w").pack(fill="x")
            if note:
                tk.Label(f, text=note, bg=C["bg1"], fg=C["text3"], font=F["small"], anchor="w", justify="left",
                         wraplength=round(560 * SCALE)).pack(fill="x", pady=(2, 0))
            return f

        def path_row(parent, label, var, browse):
            row = tk.Frame(parent, bg=C["bg1"])
            row.pack(fill="x", pady=(6, 0))
            tk.Label(row, text=label, bg=C["bg1"], fg=C["text3"], font=F["tiny"], width=8, anchor="w").pack(side="left")
            Button(row, "Browse…", browse, font=F["small"], padx=10, pady=3).pack(side="right")
            Input(row, var, placeholder="Pick a folder…", mono=True).pack(side="left", fill="x", expand=True,
                                                                          padx=(0, 6))

        # --- texture converter
        f = section("TEXTURE CONVERTER")
        Segmented(f, self.v_dcs_mode, self.DCS_MODES).pack(anchor="w", pady=(8, 0))
        tk.Label(f, textvariable=self.v_dcs_help, bg=C["bg1"], fg=C["text3"], font=F["small"], anchor="w",
                 justify="left", wraplength=round(560 * SCALE)).pack(fill="x", pady=(6, 0))
        self.dcs_extr_row = tk.Frame(f, bg=C["bg1"])
        self.dd_dcs_extr = Dropdown(self.dcs_extr_row, "Extraction", self.v_dcs_extr, [("", "—")], width=30,
                                    command=self._dcs_pick_extraction)
        self.dd_dcs_extr.pack(side="left")
        tk.Label(self.dcs_extr_row, text="or pick any folder below", bg=C["bg1"], fg=C["text3"],
                 font=F["small"]).pack(side="left", padx=(8, 0))
        self.dcs_paths = tk.Frame(f, bg=C["bg1"])
        self.dcs_paths.pack(fill="x")
        path_row(self.dcs_paths, "INPUT", self.v_dcs_src, lambda: self._dcs_browse(self.v_dcs_src, True))
        path_row(self.dcs_paths, "OUTPUT", self.v_dcs_out, lambda: self._dcs_browse(self.v_dcs_out, False))
        self.dcs_opts = tk.Frame(f, bg=C["bg1"])
        self.dcs_opts.pack(fill="x", pady=(8, 0))
        self.dd_dcs_preset = Dropdown(self.dcs_opts, "Preset", self.v_pbr_preset,
                                      [p for p in PBR_PRESETS if p[0] not in ("off", "dcs")], width=22)
        self.dd_dcs_pbrnormal = Dropdown(self.dcs_opts, "Normal", self.v_pbr_normal, NORMAL_CONV, width=16)
        self.dd_dcs_normal = Dropdown(self.dcs_opts, "Normals", self.v_dcs_normal,
                                      [("opengl", "OpenGL  (Blender, Substance GL)"),
                                       ("directx", "DirectX  (Unreal, Substance DX)")], width=8)
        self.dd_dcs_layout = Dropdown(self.dcs_opts, "Maps", self.v_dcs_orm,
                                      [("separate", "Separate maps"), ("orm", "One ORM map")], width=13)
        self.dd_dcs_fmt = Dropdown(self.dcs_opts, "Save as", self.v_dcs_fmt, [], width=20)
        act = tk.Frame(f, bg=C["bg1"])
        act.pack(fill="x", pady=(10, 0))
        Button(act, "Convert", self.dcs_convert, variant="primary", icon="▶", padx=16).pack(side="left")
        Button(act, "Open output folder", lambda: self.v_dcs_out.get() and open_folder(Path(self.v_dcs_out.get())),
               font=F["small"], padx=10, pady=3).pack(side="left", padx=(8, 0))

        # --- helmet
        f2 = section("F/A-18 PILOT HELMET  (AC8 → DCS)",
                     "Moves an Ace Combat 8 pilot helmet's textures onto the DCS F/A-18 helmet's UV layout (the helmet "
                     "shell). Pick the AC8 colour map: its normal and metal/rough maps are found next to it. Give the "
                     "DCS helmet's own textures to keep its visor, straps and the rest (the results get their names).")
        self.helmet_vars = {k: tk.StringVar(value="") for k, *_ in self.HELMET_SLOTS}
        for side, title in (("ac8", "ACE COMBAT 8"), ("dcs", "DCS HELMET  (optional)")):
            tk.Label(f2, text=title, bg=C["bg1"], fg=C["text2"], font=F["tiny"], anchor="w").pack(fill="x",
                                                                                                 pady=(10, 2))
            for key, label, s_, sufs in self.HELMET_SLOTS:
                if s_ == side:
                    self._helmet_row(f2, key, label, side)
        self.v_helmet_out = tk.StringVar(value=cfg.get("helmet_out", ""))
        self.v_helmet_fmt = tk.StringVar(value=cfg.get("helmet_fmt", "same"))
        path_row(f2, "OUTPUT", self.v_helmet_out, lambda: self._dcs_browse(self.v_helmet_out, False))
        row = tk.Frame(f2, bg=C["bg1"])
        row.pack(fill="x", pady=(8, 0))
        Dropdown(row, "Save as", self.v_helmet_fmt, [("same", "Same as the DCS files"), ("png", "PNG"),
                                                     ("tga", "TGA"), ("dds", "DDS  (compressed + mipmaps)")],
                 width=20).pack(side="left")
        # the DCS helmet's brow pad covers the bottom of the AC8 name at the front: move the front of the design up
        # (older settings were "FRONT,BACK" and fall back to the default)
        raise_opts = [("0", "No"), ("0.8", "A little"), ("1.55", "Name clear of the brow pad  (default)"),
                      ("2.2", "More")]
        saved = cfg.get("helmet_raise", "1.55")
        self.v_helmet_raise = tk.StringVar(value=saved if saved in dict(raise_opts) else "1.55")
        Dropdown(row, "Move design up", self.v_helmet_raise, raise_opts, width=12).pack(side="left", padx=(6, 0))
        act2 = tk.Frame(f2, bg=C["bg1"])
        act2.pack(fill="x", pady=(10, 0))
        Button(act2, "Convert helmet", self.helmet_convert, variant="primary", icon="▶", padx=16).pack(side="left")
        Button(act2, "Open output folder",
               lambda: self.v_helmet_out.get() and open_folder(Path(self.v_helmet_out.get())),
               font=F["small"], padx=10, pady=3).pack(side="left", padx=(8, 0))
        tk.Frame(page, bg=C["bg1"], height=18).pack()

        watch(page, self.v_dcs_mode, self._dcs_mode_changed)
        self._dcs_mode_changed()

    def _helmet_row(self, parent, key, label, side):
        row = tk.Frame(parent, bg=C["bg1"])
        row.pack(fill="x", pady=2)
        tk.Label(row, text=label, bg=C["bg1"], fg=C["text2"], font=F["small"], width=24, anchor="w").pack(side="left")
        x_ = tk.Label(row, text="✕", bg=C["bg1"], fg=C["text3"], font=F["small"], cursor="hand2", padx=6)
        x_.pack(side="right")
        x_.bind("<Button-1>", lambda e: self.helmet_vars[key].set(""))
        Button(row, "…", lambda: self._helmet_pick(key, label, side), font=F["small"], padx=10, pady=2).pack(
            side="right", padx=(6, 0))
        shown = tk.Label(row, text="—", bg=C["bg2"], fg=C["text3"], font=F["mono_s"], anchor="w", padx=8, pady=4)
        shown.pack(side="left", fill="x", expand=True)

        def refresh():
            p_ = self.helmet_vars[key].get()
            shown.configure(text=Path(p_).name if p_ else "—", fg=C["text1"] if p_ else C["text3"])
        watch(shown, self.helmet_vars[key], refresh)

    def _helmet_pick(self, key, label, side):
        start = self.dcs_cfg.get(f"helmet_dir_{side}") or str(HERE)
        f = filedialog.askopenfilename(title=label, initialdir=start,
                                       filetypes=[("Images", "*.png *.tga *.dds *.tif *.tiff"), ("All files", "*.*")])
        if not f:
            return
        f = Path(f)
        self.helmet_vars[key].set(str(f))
        self.dcs_cfg[f"helmet_dir_{side}"] = str(f.parent)
        for k2, _l, s2, sufs2 in self.HELMET_SLOTS:  # the rest of the set, if it's next to it
            if s2 == side and k2 != key and not self.helmet_vars[k2].get():
                sib = self._helmet_sibling(f, sufs2, side)
                if sib and sib != f:
                    self.helmet_vars[k2].set(str(sib))
        if side == "ac8" and not self.v_helmet_out.get():
            self.v_helmet_out.set(str(f.parent / "DCS_Helmet"))

    @staticmethod
    def _helmet_sibling(picked, suffixes, side):
        """T_X_C.png -> T_X_N.png (or another pilot's T_Y_Helmet01_01_N.png: AC8 helmets share normal maps)."""
        stem, low = picked.stem, picked.stem.lower()
        for tail in ("_c", "_n", "_no", "_mro", "_mrx", "_nm", "_normal", "_roughmet", "_rm"):
            if low.endswith(tail):
                stem = stem[:-len(tail)]
                break
        files = [p for p in picked.parent.iterdir() if p.is_file() and p.suffix.lower() in
                 (".png", ".tga", ".dds", ".tif", ".tiff")]
        for suf in suffixes:
            for p in files:
                if p.stem.lower() == (stem + suf).lower():
                    return p
        if side == "ac8":
            core = "_".join(stem.split("_")[2:]).lower()  # helmet01_01
            for suf in suffixes:
                for p in files:
                    if core and p.stem.lower().endswith(core + suf):
                        return p
        return None

    def _dcs_mode_changed(self):
        mode = self.v_dcs_mode.get()
        self.v_dcs_help.set(self.DCS_HELP[mode])
        ac8 = mode.startswith("ac8")
        if ac8:
            self.dcs_extr_row.pack(fill="x", pady=(8, 0), before=self.dcs_paths)
        else:
            self.dcs_extr_row.pack_forget()
        for w in self.dcs_opts.winfo_children():
            w.pack_forget()
        show = {"ac8_pbr": (self.dd_dcs_preset, self.dd_dcs_pbrnormal), "ac8_dcs": (self.dd_dcs_fmt,),
                "pbr_dcs": (self.dd_dcs_normal, self.dd_dcs_fmt),
                "dcs_pbr": (self.dd_dcs_layout, self.dd_dcs_normal, self.dd_dcs_fmt)}[mode]
        for w in show:
            w.pack(side="left", padx=(0, 6))
        self.dd_dcs_normal.cap.configure(text="YOUR NORMALS" if mode == "pbr_dcs" else "NORMAL OUT")
        fmts = [("png", "PNG"), ("tga", "TGA")]
        if mode != "dcs_pbr":
            fmts = [("dds", "DDS  (compressed + mipmaps)")] + fmts
        self.dd_dcs_fmt.set_options(fmts)
        self.dcs_cfg["mode"] = mode
        if ac8:
            self._dcs_fill_sources()
        self._dcs_default_out()

    def _dcs_fill_sources(self):
        if not hasattr(self, "dd_dcs_extr"):
            return
        folders = self._tex_folders()
        opts = [(str(f_), f_.name) for f_ in folders]
        self.dd_dcs_extr.set_options(opts or [("", "— no textures exported yet —")])
        self.v_dcs_count.set(f"{len(opts)} extraction(s) with textures" if opts else "")
        if self.v_dcs_mode.get().startswith("ac8") and opts and not self.v_dcs_src.get():
            self.v_dcs_extr.set(opts[0][0])
            self._dcs_pick_extraction()

    def _dcs_pick_extraction(self):
        folder = self.v_dcs_extr.get()
        if folder:
            self.v_dcs_src.set(str(Path(folder) / FINAL_DIR_NAME / "Textures"))
            self.v_dcs_out.set("")
            self._dcs_default_out()

    def _dcs_default_out(self):
        """Fill the output folder from the input when it's empty (or still the automatic one)."""
        src = self.v_dcs_src.get().strip()
        if not src or (self.v_dcs_out.get() and self.v_dcs_out.get() != getattr(self, "_dcs_auto_out", None)):
            return
        p = Path(src)
        mode = self.v_dcs_mode.get()
        if mode.startswith("ac8") and p.name == "Textures" and p.parent.name == FINAL_DIR_NAME:
            out = p.parent / ("DCS" if mode == "ac8_dcs" else f"PBR_{self.v_pbr_preset.get()}")
        else:
            out = p / ("DCS" if mode in ("ac8_dcs", "pbr_dcs") else "PBR")
        self._dcs_auto_out = str(out)
        self.v_dcs_out.set(str(out))

    def _dcs_browse(self, var, is_input):
        d = filedialog.askdirectory(title="Folder with the textures" if is_input else "Save into",
                                    initialdir=var.get() or str(HERE))
        if d:
            var.set(d)
            if is_input:
                self.v_dcs_extr.set("")
                self._dcs_default_out()

    def dcs_convert(self):
        mode = self.v_dcs_mode.get()
        src, out = self.v_dcs_src.get().strip(), self.v_dcs_out.get().strip()
        if not src or not Path(src).is_dir():
            messagebox.showinfo(APP_NAME, "Pick the folder with the textures first.")
            return
        if not out:
            self._dcs_default_out()
            out = self.v_dcs_out.get().strip()
        fmt, normal, orm = self.v_dcs_fmt.get(), self.v_dcs_normal.get(), self.v_dcs_orm.get() == "orm"
        self.dcs_cfg.update(src=src, out=out, fmt=fmt, normal=normal, layout=self.v_dcs_orm.get())
        src, out = Path(src), Path(out)
        label = dict(self.DCS_MODES)[mode]

        def work():
            if mode == "ac8_pbr":
                n = self._step_pbr(src, out, self.v_pbr_preset.get(), self.v_pbr_normal.get())
            elif mode == "ac8_dcs":
                n = self._step_pbr(src, out, "dcs")
                if fmt != "png" and n:
                    self.post("step", f"Saving as {fmt.upper()}…")
                    self.run_cmd([console_python(), "-u", DCS_SCRIPT, "reformat", out, "--format", fmt], HERE)
            else:
                self.post("step", f"{label}: converting…")
                cmd = [console_python(), "-u", DCS_SCRIPT, "to-dcs" if mode == "pbr_dcs" else "to-pbr", src, out,
                       "--format", fmt, "--normal", normal] + (["--orm"] if orm and mode == "dcs_pbr" else [])
                code, _ = self.run_cmd(cmd, HERE)
                if code not in (0, 1):
                    raise RuntimeError(f"{label} failed (exit code {code}). See the log.")
                n = sum(count_files(out, e) for e in (".png", ".tga", ".dds")) if out.exists() else 0
            if n:
                self.post("call", lambda: open_folder(out))
            return f"✔ {label}: {n} file(s) in {out}" if n else f"{label}: nothing to convert in {src}"

        self._start(work, f"{label}…")

    def helmet_convert(self):
        v = {k: (var.get() or None) for k, var in self.helmet_vars.items()}
        if not any(v[k] for k in ("ac8_color", "ac8_normal", "ac8_mro")):
            messagebox.showinfo(APP_NAME, "Pick at least one Ace Combat 8 helmet texture (the colour map _C).")
            return
        out = self.v_helmet_out.get().strip() or str(Path(v["ac8_color"] or v["ac8_normal"] or v["ac8_mro"]).parent
                                                     / "DCS_Helmet")
        self.v_helmet_out.set(out)
        fmt = self.v_helmet_fmt.get()
        self.dcs_cfg.update(helmet_out=out, helmet_fmt=fmt, helmet_raise=self.v_helmet_raise.get())
        cmd = [console_python(), "-u", DCS_SCRIPT, "helmet", "--out", out, "--raise", self.v_helmet_raise.get()]
        for k, val in v.items():
            if val:
                cmd += ["--" + k.replace("_", "-"), val]
        if fmt != "same":
            cmd += ["--format", fmt]

        def work():
            self.post("step", "Helmet: moving the AC8 textures onto the DCS helmet (about a minute)…")
            code, _ = self.run_cmd(cmd, HERE)
            if code != 0:
                raise RuntimeError(f"Helmet conversion failed (exit code {code}). See the log.")
            self.post("call", lambda: open_folder(Path(out)))
            return f"✔ Helmet done: {out}"

        self._start(work, "Helmet…")


class _Stop(Exception):
    pass


if __name__ == "__main__":
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            # own taskbar entry, so the window shows the wing icon instead of Python's
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AC8.AssetExtractor")
        except Exception:
            pass
    sys.excepthook = lambda t, v, tb: SLOG.write("Unhandled: " + "".join(
        __import__("traceback").format_exception(t, v, tb)).rstrip())
    threading.excepthook = lambda a: SLOG.write(f"Unhandled in thread {a.thread.name if a.thread else '?'}: " + "".join(
        __import__("traceback").format_exception(a.exc_type, a.exc_value, a.exc_traceback)).rstrip())
    try:  # files an update couldn't replace while the app was running (e.g. its font): swap them in first
        import ac8_update
        ac8_update.apply_pending(HERE)
    except Exception:
        pass
    move_user_data()  # before App() reads the settings / asset list / history
    load_private_fonts()
    app = App()
    app.report_callback_exception = lambda t, v, tb: SLOG.write("Error in the window: " + "".join(
        __import__("traceback").format_exception(t, v, tb)).rstrip())
    app.mainloop()
