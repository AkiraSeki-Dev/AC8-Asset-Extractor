"""Updating the app itself from its GitHub releases (https://github.com/AkiraSeki-Dev/AC8-Asset-Extractor).

A release is tagged vX.Y and carries AC8_Asset_Extractor_vX.Y.zip (one "AC8 Asset Extractor" folder inside). Updating
copies the new files over the app folder but never touches the user's own files: settings, asset list and history,
exports, logs, the AES key and the unpacking tools (those have their own updater, see ac8_tools.py).
"""
import io
import json
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
KEEP = ("ac8_extractor_settings.json", "asset_index.txt", "asset_history.json", "ac8_exports/", "logs/",
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
    """{'version': '2.38', 'url': zip download, 'notes': release text} of the newest release."""
    rel = json.loads(_get(f"https://api.github.com/repos/{REPO}/releases/latest"))
    asset = next((a for a in rel.get("assets", []) if a["name"].lower().endswith(".zip")), None)
    if asset is None:
        raise RuntimeError(f"release {rel.get('tag_name')} has no zip to download")
    ver = parse(rel.get("tag_name", ""))
    return {"version": ".".join(map(str, ver[:2] if ver and ver[2] == 0 else ver or ())) or rel.get("tag_name"),
            "url": asset["browser_download_url"], "notes": rel.get("body") or ""}


def _kept(rel):
    r = rel.replace("\\", "/").lower()
    return any(r == k.rstrip("/") or r.startswith(k) for k in KEEP)


def install(app_dir, url, log=print):
    """Download the release zip and copy it over app_dir. -> number of files written."""
    app_dir = Path(app_dir)
    log("  Downloading the update…")
    data = _get(url, timeout=300)
    n = 0
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
            dest.parent.mkdir(parents=True, exist_ok=True)
            payload = z.read(info)
            if dest.name.lower() == LAUNCHER.lower() and dest.exists():
                # the launcher is running: Windows lets a running exe be renamed, not overwritten
                old = dest.with_name(dest.name + ".old")
                try:
                    if old.exists():
                        old.unlink()
                    dest.rename(old)
                except OSError:
                    pass
            tmp = dest.with_name(dest.name + ".new")
            tmp.write_bytes(payload)
            os.replace(tmp, dest)
            n += 1
    shutil.rmtree(app_dir / "__pycache__", ignore_errors=True)
    log(f"  ✔ {n} files updated")
    return n


def cleanup(app_dir):
    """Remove the launcher left over from the last update (it was still running then)."""
    old = Path(app_dir) / (LAUNCHER + ".old")
    try:
        if old.exists():
            old.unlink()
    except OSError:
        pass
