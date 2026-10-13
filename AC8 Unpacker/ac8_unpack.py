"""Ace Combat 8: Wings of Theve pak unpacker.

Finds the pak AES-256 key in your own running copy of the game and drives repak and retoc
to list and extract the encrypted .pak and .utoc/.ucas containers.

    python ac8_unpack.py fetch-tools
    python ac8_unpack.py find-key
    python ac8_unpack.py list --filter Cockpit
    python ac8_unpack.py extract CUR_FocusCamera out
    python ac8_unpack.py extract-pak Live/Content/Movies out
"""
import argparse
import ctypes
import hashlib
import io
import json
import os
import struct
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from ctypes import wintypes
from multiprocessing import Pool
from pathlib import Path

HERE = Path(__file__).resolve().parent
KEY_FILE = HERE / "aes-key.txt"
TOOLS = HERE / "tools"
APP_ID = "2288340"
EXE_NAME = "AceCombat8.exe"
ENGINE = "UE5_4"
PAK_MAGIC = 0x5A6F12E1
PAK_FOOTER = 221  # pak v11: guid 16, encrypted 1, magic 4, version 4, offset 8, size 8, sha1 20, 5 x 32 names
RELEASES = {"retoc": "trumank/retoc", "repak": "trumank/repak"}


# ---------------------------------------------------------------- game folder

def find_game_dir():
    import winreg
    steam = None
    for root, path, name in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                             (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")):
        try:
            with winreg.OpenKey(root, path) as key:
                steam = Path(winreg.QueryValueEx(key, name)[0])
                break
        except OSError:
            pass
    if not steam:
        return None
    libraries = [steam]
    vdf = steam / "steamapps" / "libraryfolders.vdf"
    if vdf.exists():
        for line in vdf.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.strip().split('"')
            if len(parts) >= 4 and parts[1] == "path":
                libraries.append(Path(parts[3].replace("\\\\", "\\")))
    for library in libraries:
        manifest = library / "steamapps" / f"appmanifest_{APP_ID}.acf"
        if not manifest.exists():
            continue
        name = "ACE COMBAT 8"
        for line in manifest.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.strip().split('"')
            if len(parts) >= 4 and parts[1] == "installdir":
                name = parts[3]
        game = library / "steamapps" / "common" / name
        if (game / "Game" / "Content" / "Paks").is_dir():
            return game
    return None


def paks_dir(args):
    game = Path(args.game_dir) if args.game_dir else find_game_dir()
    if not game or not (game / "Game" / "Content" / "Paks").is_dir():
        sys.exit("Could not find the game. Pass --game-dir with the folder that holds start_protected_game.exe.")
    return game / "Game" / "Content" / "Paks"


# ---------------------------------------------------------------- key search

def load_target(paks):
    """Encrypted index and its SHA1 from the smallest pak that has an encrypted index."""
    for pak in sorted(paks.glob("*.pak"), key=lambda p: p.stat().st_size):
        with open(pak, "rb") as f:
            f.seek(-PAK_FOOTER, 2)
            foot = f.read(PAK_FOOTER)
            magic, version, offset, size = struct.unpack_from("<IIQQ", foot, 17)
            if magic != PAK_MAGIC or foot[16] != 1 or size == 0 or size % 16:
                continue
            f.seek(offset)
            return f.read(size), foot[41:61], pak.name
    sys.exit("No pak with an encrypted index found.")


_index = _sha1 = None


def _init_worker(index, sha1):
    global _index, _sha1
    _index, _sha1 = index, sha1


def _verify(key):
    from Crypto.Cipher import AES
    return hashlib.sha1(AES.new(key, AES.MODE_ECB).decrypt(_index)).digest() == _sha1


def _scan(data):
    from Crypto.Cipher import AES
    new, ecb, block, zero4 = AES.new, AES.MODE_ECB, _index[:16], b"\0\0\0\0"
    for i in range(0, len(data) - 31, 4):
        key = data[i:i + 32]
        if zero4 in key:
            continue
        # the index starts with the mount point string, so a small little-endian int32 length
        if new(key, ecb).decrypt(block)[1:4] == b"\0\0\0" and _verify(key):
            return key
    return None


class _MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wintypes.DWORD), ("PartitionId", wintypes.WORD),
                ("RegionSize", ctypes.c_size_t), ("State", wintypes.DWORD),
                ("Protect", wintypes.DWORD), ("Type", wintypes.DWORD)]


def _memory_chunks(pid, slots):
    """Writable committed memory of the process in 8 MB pieces. Waits on `slots` to bound memory use."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(_MBI), ctypes.c_size_t]
    k32.VirtualQueryEx.restype = ctypes.c_size_t
    k32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                      ctypes.POINTER(ctypes.c_size_t)]
    handle = k32.OpenProcess(0x0410, False, pid)  # QUERY_INFORMATION | VM_READ
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    step = 8 << 20
    buf = ctypes.create_string_buffer(step + 32)
    got = ctypes.c_size_t()
    mbi = _MBI()
    address = 0
    writable = 0x04 | 0x08 | 0x40 | 0x80
    try:
        while k32.VirtualQueryEx(handle, address, ctypes.byref(mbi), ctypes.sizeof(mbi)):
            base, size = mbi.BaseAddress or 0, mbi.RegionSize
            if (mbi.State == 0x1000 and not mbi.Protect & 0x101 and mbi.Protect & writable
                    and mbi.Type in (0x20000, 0x1000000)):  # private or image
                for off in range(0, size, step):
                    n = min(step + 32, size - off)  # overlap so a key on a piece boundary is not missed
                    if k32.ReadProcessMemory(handle, base + off, buf, n, ctypes.byref(got)) and got.value >= 32:
                        slots.acquire()
                        yield buf.raw[:got.value]
            address = base + size
            if address >= 0x7FFFFFFF0000:
                break
    finally:
        k32.CloseHandle(handle)


def game_pid():
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {EXE_NAME}", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if parts and parts[0].lower() == EXE_NAME.lower():
            return int(parts[1])
    return None


def cmd_find_key(args):
    try:
        import Crypto.Cipher.AES  # noqa: F401
    except ImportError:
        sys.exit("pycryptodome is missing. Run: pip install pycryptodome")
    paks = paks_dir(args)
    index, sha1, name = load_target(paks)
    pid = args.pid or game_pid()
    if not pid:
        sys.exit(f"{EXE_NAME} is not running. Start the game, wait for the title screen, then run this again.")
    print(f"Checking candidates against {name}. Scanning process {pid} ...", flush=True)
    procs = args.procs or max(1, (os.cpu_count() or 4) - 2)
    slots = threading.BoundedSemaphore(procs * 2)
    scanned, start, key = 0, time.time(), None
    try:
        with Pool(procs, _init_worker, (index, sha1)) as pool:
            for hit in pool.imap_unordered(_scan, _memory_chunks(pid, slots)):
                slots.release()
                scanned += 1
                if hit:
                    key = hit
                    pool.terminate()
                    break
                if scanned % 64 == 0:
                    print(f"  {scanned * 8 / 1024:.1f} GB scanned, {time.time() - start:.0f}s", flush=True)
    except PermissionError:
        sys.exit("Could not read the game's memory. Run the game without anti-cheat (offline) and try again.")
    if not key:
        sys.exit("Key not found. Wait until the title screen is up and try again.")
    text = "0x" + key.hex().upper()
    KEY_FILE.write_text(text + "\n", encoding="ascii")
    print(f"Key: {text}\nSaved to {KEY_FILE}")


# ---------------------------------------------------------------- tools

def cmd_fetch_tools(args):
    TOOLS.mkdir(exist_ok=True)
    for tool, repo in RELEASES.items():
        req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest",
                                     headers={"User-Agent": "ac8-unpacker"})
        release = json.load(urllib.request.urlopen(req))
        asset = next(a for a in release["assets"] if a["name"].endswith("x86_64-pc-windows-msvc.zip"))
        data = urllib.request.urlopen(asset["browser_download_url"]).read()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            member = next(n for n in z.namelist() if n.lower().endswith(f"{tool}.exe"))
            (TOOLS / f"{tool}.exe").write_bytes(z.read(member))
        print(f"{tool} {release['tag_name']} -> {TOOLS / (tool + '.exe')}")


def tool(name):
    local = TOOLS / f"{name}.exe"
    if local.exists():
        return str(local)
    from shutil import which
    found = which(name)
    if not found:
        sys.exit(f"{name}.exe not found. Run: python ac8_unpack.py fetch-tools")
    return found


def read_key(args):
    if args.key:
        return args.key
    if KEY_FILE.exists():
        return KEY_FILE.read_text(encoding="ascii").strip()
    sys.exit("No key yet. Run: python ac8_unpack.py find-key   (or pass --key 0x...)")


def main_containers(paks, ext):
    return sorted(p for p in paks.glob(f"*{ext}") if p.stat().st_size > 1024 and p.name != "global.utoc")


def cmd_list(args):
    paks, key = paks_dir(args), read_key(args)
    needle = (args.filter or "").lower()
    count = 0
    for utoc in main_containers(paks, ".utoc"):
        out = subprocess.run([tool("retoc"), "-a", key, "list", "--path", str(utoc)],
                             capture_output=True, text=True, errors="replace").stdout
        for line in out.splitlines():
            path = line.split()[-1] if line.split() else ""
            if path.startswith("../") and needle in path.lower():
                print(path.replace("../../../", ""))
                count += 1
    for pak in main_containers(paks, ".pak"):
        out = subprocess.run([tool("repak"), "--aes-key", key, "list", str(pak)],
                             capture_output=True, text=True, errors="replace").stdout
        for line in out.splitlines():
            if line and needle in line.lower():
                print(line)
                count += 1
    print(f"{count} files", file=sys.stderr)


def cmd_extract(args):
    paks, key = paks_dir(args), read_key(args)
    code = subprocess.run([tool("retoc"), "-a", key, "to-legacy", "--version", ENGINE, "--no-shaders",
                           "-f", args.filter, str(paks), args.out]).returncode
    sys.exit(code)


def cmd_extract_pak(args):
    paks, key = paks_dir(args), read_key(args)
    for pak in main_containers(paks, ".pak"):
        subprocess.run([tool("repak"), "--aes-key", key, "unpack", "-q", "-f", "-o", args.out,
                        "-i", args.path, str(pak)])
    print(f"Done: {args.out}")


def main():
    parser = argparse.ArgumentParser(description="Ace Combat 8 pak unpacker")
    parser.add_argument("--game-dir", help="game folder (the one with start_protected_game.exe)")
    parser.add_argument("--key", help="AES key as 0x + 64 hex digits (default: aes-key.txt)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("fetch-tools", help="download retoc and repak into ./tools").set_defaults(run=cmd_fetch_tools)
    p = sub.add_parser("find-key", help="find the AES key in the running game")
    p.add_argument("--pid", type=int)
    p.add_argument("--procs", type=int)
    p.set_defaults(run=cmd_find_key)
    p = sub.add_parser("list", help="list files in the containers")
    p.add_argument("--filter", help="only paths containing this text")
    p.set_defaults(run=cmd_list)
    p = sub.add_parser("extract", help="extract assets (.uasset/.uexp) whose name contains FILTER")
    p.add_argument("filter")
    p.add_argument("out")
    p.set_defaults(run=cmd_extract)
    p = sub.add_parser("extract-pak", help="extract non-asset files (audio, movies, ini) under PATH from the .pak files")
    p.add_argument("path", help="file or directory inside the pak, e.g. Live/Content/Movies")
    p.add_argument("out")
    p.set_defaults(run=cmd_extract_pak)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
