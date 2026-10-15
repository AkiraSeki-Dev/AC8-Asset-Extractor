"""Updating the app itself from its GitHub releases (https://github.com/AkiraSeki-Dev/AC8-Asset-Extractor).

Two channels. Live: a release is tagged vX.Y.Z and carries AC8_Asset_Extractor_vX.Y.Z.zip (one "AC8 Asset Extractor" folder inside). Updating
copies the new files over the app folder but never touches the user's own files: settings, asset list and history,
exports, logs, the AES key and the unpacking tools (those have their own updater, see ac8_tools.py).
Test: a test build (app/build_info.json says {"channel": "test", "build": N}) only follows the "Test build"
pre-release (tag test-latest), whose build.json names its build number and zip.
"""
import json
import io
import os
import re
import shutil
import urllib.request
import zipfile
from pathlib import Path

REPO = "AkiraSeki-Dev/AC8-Asset-Extractor"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
LAUNCHER = "AC8 Asset Extractor.exe"

# paths (relative to the app folder, lower case, forward slashes) an update never overwrites
KEEP = ("data/", "ac8_extractor_settings.json", "asset_index.txt", "asset_history.json", "ac8_exports/", "logs/",
        "ac8 unpacker/aes-key.txt", "ac8 unpacker/tools/")


def parse(v):
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", v or "")
    return tuple(int(x or 0) for x in m.groups()) if m else None


def newer(a, b):
    pa, pb = parse(a), parse(b)
    return pa is not None and (pb is None or pa > pb)


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "ac8-asset-extractor",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def latest():
    """{'version': '1.1', 'url': zip download, 'notes': release text} of the newest release. Found through the
    normal release pages (not GitHub's API, which only allows 60 lookups an hour per internet connection)."""
    from ac8_tools import latest_tag
    tag = latest_tag(REPO)
    ver = parse(tag)
    version = ".".join(map(str, ver[:2] if ver and ver[2] == 0 else ver or ())) or tag
    url = f"https://github.com/{REPO}/releases/download/{tag}/AC8_Asset_Extractor_{tag}.zip"
    notes = ""
    try:  # the README's line for this version is the release notes
        text = _get(f"https://raw.githubusercontent.com/{REPO}/{tag}/README.md").decode("utf-8", "replace")
        m = re.search(rf"^v{re.escape(tag.lstrip('v'))}:(.*?)(?=^v\d+(?:\.\d+)+:|^## |\Z)", text, re.S | re.M)
        notes = m.group(1).strip() if m else ""
    except Exception:
        pass
    return {"version": version, "url": url, "notes": notes}


TEST_TAG = "test-latest"


def build_info(code_dir):
    """{'channel': 'live' | 'test', 'build': N, 'commit': sha} written into the package by the build; a copy run
    straight from the repo counts as live."""
    try:
        info = json.loads((Path(code_dir) / "build_info.json").read_text(encoding="utf-8"))
        if isinstance(info, dict) and info.get("channel") in ("live", "test"):
            info["build"] = int(info.get("build") or 0)
            return info
    except (OSError, ValueError, TypeError):
        pass
    return {"channel": "live", "build": 0}


def latest_test():
    """{'version': 'Dev-<commit>', 'build': N, 'url': zip download, 'notes': what changed} of the current test build."""
    base = f"https://github.com/{REPO}/releases/download/{TEST_TAG}"
    info = json.loads(_get(f"{base}/build.json").decode("utf-8"))
    build = int(info["build"])
    return {"version": f"Dev-{info.get('commit') or build}", "build": build, "url": f"{base}/{info['zip']}",
            "notes": str(info.get("notes") or "")}


def _kept(rel):
    r = rel.replace("\\", "/").lower()
    return any(r == k.rstrip("/") or r.startswith(k) for k in KEEP)


UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\AC8AssetExtractor"


def _note_installed_version(version):
    """Installed with the setup program: keep the version shown in Windows' Apps list up to date."""
    if os.name != "nt" or not version:
        return
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, version)
    except OSError:
        pass  # not installed with the setup program (zip copy): nothing to update


PENDING = ".pending"   # a new file that couldn't replace the old one yet (locked): swapped in at the next start


def install(app_dir, url, log=print, version=None):
    """Download the release zip and put it over app_dir. -> number of files changed.

    All changed files are first written next to the old ones, then swapped in, so a problem part-way can't leave a
    mix of old and new files. Unchanged files are skipped. A file Windows keeps locked while the app runs (its own
    font, the running launcher) is queued as <name>.pending and swapped in by apply_pending() at the next start."""
    app_dir = Path(app_dir)
    log("  Downloading the update…")
    data = _get(url, timeout=300)
    staged = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = [i for i in z.infolist() if not i.is_dir()]
        # the zip holds one top folder ("AC8 Asset Extractor/…"): strip it
        tops = {i.filename.split("/", 1)[0] for i in names if "/" in i.filename}
        strip = len(next(iter(tops))) + 1 if len(tops) == 1 else 0
        for info in names:
            rel = info.filename[strip:]
            if not rel or rel.startswith("/") or ".." in Path(rel).parts or _kept(rel):
                continue
            dest = app_dir / rel
            payload = z.read(info)
            try:
                if dest.exists() and dest.read_bytes() == payload:
                    continue  # unchanged
            except OSError:
                pass
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".new")
            tmp.write_bytes(payload)
            staged.append((tmp, dest))
    log(f"  {len(staged)} file(s) to update")
    later = 0
    for tmp, dest in staged:
        if dest.name.lower() == LAUNCHER.lower() and dest.exists():
            # the launcher is running: Windows lets a running exe be renamed, not overwritten
            old = dest.with_name(dest.name + ".old")
            try:
                if old.exists():
                    old.unlink()
                dest.rename(old)
            except OSError:
                pass
        try:
            os.replace(tmp, dest)
        except OSError:  # locked: finish it at the next start
            os.replace(tmp, dest.with_name(dest.name + PENDING))
            later += 1
    for cache in (app_dir / "__pycache__", app_dir / "app" / "__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    _note_installed_version(version)
    log(f"  ✔ {len(staged) - later} file(s) updated" + (f", {later} more when the app restarts" if later else ""))
    return len(staged)


def apply_pending(app_dir):
    """At start-up, before anything is loaded: swap in files an update had to leave for later, and clear leftovers
    of an interrupted update."""
    app_dir = Path(app_dir)
    # only the program's own files: the root (launcher), app\ and the unpacker script — never data\ or exports
    found = []
    for folder, deep in ((app_dir, False), (app_dir / "app", True), (app_dir / "AC8 Unpacker", False)):
        if folder.is_dir():
            for pattern in ("*" + PENDING, "*.new"):
                found += list(folder.rglob(pattern) if deep else folder.glob(pattern))
    for f in found:
        target = f.with_name(f.name[: -len(f.suffix)])
        try:
            os.replace(f, target)
        except OSError:
            pass


def cleanup(app_dir):
    """Remove the launcher left over from the last update (it was still running then)."""
    apply_pending(app_dir)
    old = Path(app_dir) / (LAUNCHER + ".old")
    try:
        if old.exists():
            old.unlink()
    except OSError:
        pass
