"""Set or remove a game's Steam launch options by editing Steam's own per-user config
(Steam/userdata/<user>/config/localconfig.vdf → UserLocalConfigStore/Software/Valve/Steam/apps/<appid>/LaunchOptions).

Steam keeps that file in memory and writes it back when it exits, so Steam has to be closed while it is edited.
A backup (localconfig.vdf.ac8bak) is written next to it before the first change.
"""
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WIN else 0
APPS_PATH = ("UserLocalConfigStore", "Software", "Valve", "Steam", "apps")


# ------------------------------------------------------------------ text KeyValues (VDF)

_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])|(//[^\n]*)|(\S+)', re.S)


def _unescape(s):
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), s)


def _escape(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


def loads(text):
    """VDF text -> nested lists [(key, value-or-list)] (order and duplicate keys kept)."""
    stack = [[]]
    key = None
    for m in _TOKEN.finditer(text):
        q, brace, comment, bare = m.groups()
        if comment is not None:
            continue
        if brace == "{":
            node = []
            stack[-1].append((key, node))
            stack.append(node)
            key = None
        elif brace == "}":
            stack.pop()
            if not stack:
                raise ValueError("unbalanced braces")
        else:
            tok = _unescape(q) if q is not None else bare
            if key is None:
                key = tok
            else:
                stack[-1].append((key, tok))
                key = None
    if len(stack) != 1:
        raise ValueError("unbalanced braces")
    return stack[0]


def dumps(node, depth=0):
    out = []
    tab = "\t" * depth
    for k, v in node:
        if isinstance(v, list):
            out.append(f'{tab}"{_escape(k)}"\n{tab}{{\n{dumps(v, depth + 1)}{tab}}}\n')
        else:
            out.append(f'{tab}"{_escape(k)}"\t\t"{_escape(v)}"\n')
    return "".join(out)


def _child(node, key, create=False):
    for k, v in node:
        if k.lower() == key.lower() and isinstance(v, list):
            return v
    if not create:
        return None
    new = []
    node.append((key, new))
    return new


# ------------------------------------------------------------------ Steam install

def steam_dir():
    if IS_WIN:
        try:
            import winreg
            for hive, sub in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                              (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
                try:
                    with winreg.OpenKey(hive, sub) as k:
                        for name in ("SteamPath", "InstallPath"):
                            try:
                                p = Path(winreg.QueryValueEx(k, name)[0])
                                if (p / "userdata").is_dir():
                                    return p
                            except OSError:
                                pass
                except OSError:
                    pass
        except ImportError:
            pass
        for p in (Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam",):
            if (p / "userdata").is_dir():
                return p
    for p in (Path.home() / ".steam" / "steam", Path.home() / ".local" / "share" / "Steam"):
        if (p / "userdata").is_dir():
            return p
    return None


def configs(steam=None):
    steam = steam or steam_dir()
    if not steam:
        return []
    return sorted((steam / "userdata").glob("*/config/localconfig.vdf"))


def running():
    if IS_WIN:
        try:
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq steam.exe", "/NH"], capture_output=True,
                                 text=True, creationflags=NO_WINDOW).stdout
            return "steam.exe" in out.lower()
        except OSError:
            return False
    try:
        return subprocess.run(["pgrep", "-x", "steam"], capture_output=True).returncode == 0
    except OSError:
        return False


def shutdown(steam=None, wait=40.0):
    """Ask Steam to exit and wait for it. -> True when it is closed."""
    if not running():
        return True
    steam = steam or steam_dir()
    exe = (steam / "steam.exe") if (steam and IS_WIN) else "steam"
    try:
        subprocess.Popen([str(exe), "-shutdown"], creationflags=NO_WINDOW)
    except OSError:
        return False
    end = time.time() + wait
    while time.time() < end:
        time.sleep(1.0)
        if not running():
            time.sleep(1.5)  # let it finish writing its files
            return True
    return False


def open_url(url):
    if IS_WIN:
        os.startfile(url)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", url])
    else:
        subprocess.Popen(["xdg-open", url])


# ------------------------------------------------------------------ launch options

def get_launch_options(appid, cfg):
    root = loads(Path(cfg).read_text(encoding="utf-8", errors="replace"))
    node = root
    for k in APPS_PATH + (str(appid),):
        node = _child(node, k)
        if node is None:
            return None
    return next((v for k, v in node if k.lower() == "launchoptions" and not isinstance(v, list)), None)


def set_launch_options(appid, value, cfg):
    """Set (value = text) or remove (value = None) the launch options in one localconfig.vdf.
    -> the previous value (None when there was none)."""
    cfg = Path(cfg)
    text = cfg.read_text(encoding="utf-8", errors="replace")
    root = loads(text)
    node = root
    for k in APPS_PATH + (str(appid),):
        node = _child(node, k, create=True)
    prev = None
    for i, (k, v) in enumerate(list(node)):
        if k.lower() == "launchoptions" and not isinstance(v, list):
            prev = v
            del node[i]
            break
    if value:
        node.append(("LaunchOptions", value))
    bak = cfg.with_name(cfg.name + ".ac8bak")
    if not bak.exists():
        shutil.copyfile(cfg, bak)
    tmp = cfg.with_name(cfg.name + ".ac8tmp")
    tmp.write_text(dumps(root), encoding="utf-8")
    loads(tmp.read_text(encoding="utf-8"))  # must read back cleanly before it replaces the real file
    os.replace(tmp, cfg)
    return prev
