"""Updating the app itself from its GitHub releases (https://github.com/AkiraSeki-Dev/AC8-Asset-Extractor).

A release is tagged vX.Y and carries AC8_Asset_Extractor_vX.Y.zip (one "AC8 Asset Extractor" folder inside). Updating
copies the new files over the app folder but never touches the user's own files: settings, asset list and history,
exports, logs, the AES key and the unpacking tools (those have their own updater, see ac8_tools.py).
"""
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
        m = re.search(rf"^v{re.escape(tag.lstrip('v'))}:(.*?)(?=^v\d+\.\d+:|^## |\Z)", text, re.S | re.M)
        notes = m.group(1).strip() if m else ""
    except Exception:
        pass
    return {"version": version, "url": url, "notes": notes}


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
    for cache in (app_dir / "__pycache__", app_dir / "app" / "__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
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
