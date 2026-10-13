"""The unpacking tools the app drives — retoc and repak by Truman Kilen (trumank), MIT / Apache-2.0 — and an updater
that installs the newest Windows release from GitHub into "AC8 Unpacker/tools".

    retoc  https://github.com/trumank/retoc   (IoStore .utoc/.ucas -> legacy .uasset/.uexp; asset conversion by Archengius)
    repak  https://github.com/trumank/repak   (.pak files)
"""
import io
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

REPOS = {"retoc": "trumank/retoc", "repak": "trumank/repak"}
ASSET_SUFFIX = "x86_64-pc-windows-msvc.zip"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
VERSIONS = "versions.json"


def _versions_file(tools):
    return Path(tools) / VERSIONS


def _read_versions(tools):
    try:
        return json.loads(_versions_file(tools).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_versions(tools, data):
    _versions_file(tools).write_text(json.dumps(data, indent=1), encoding="utf-8")


def parse_version(text):
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else None


def installed(tools, name):
    """Installed version string ('0.1.5'), '' when the exe is there but its version is unknown, None when missing."""
    exe = Path(tools) / f"{name}.exe"
    if not exe.exists():
        return None
    v = _read_versions(tools).get(name)
    if v:
        return v
    if os.name == "nt":
        try:
            out = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=10,
                                 creationflags=NO_WINDOW).stdout
            ver = parse_version(out)
            if ver:
                v = ".".join(map(str, ver))
                data = _read_versions(tools)
                data[name] = v
                _write_versions(tools, data)
                return v
        except (OSError, subprocess.SubprocessError):
            pass
    return ""


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "ac8-asset-extractor",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def latest_tag(repo):
    """Tag of a repo's newest release, read from where github.com/<repo>/releases/latest redirects to. This is a
    normal web page, so it doesn't use up GitHub's API allowance (60 lookups an hour per internet connection)."""
    req = urllib.request.Request(f"https://github.com/{repo}/releases/latest", method="HEAD",
                                 headers={"User-Agent": "ac8-asset-extractor"})
    with urllib.request.urlopen(req, timeout=30) as r:
        final = r.geturl()
    if "/releases/tag/" not in final:
        raise RuntimeError(f"{repo} has no release yet")
    return urllib.parse.unquote(final.rsplit("/releases/tag/", 1)[1].strip("/"))


def friendly_error(e):
    """Short reason for a failed GitHub request."""
    code = getattr(e, "code", None)
    if code in (403, 429):
        return "GitHub is limiting requests from your connection right now — try again in a while"
    if code == 404:
        return "the release wasn't found on GitHub"
    if isinstance(e, urllib.error.URLError):
        return "couldn't reach GitHub — check your internet connection"
    return str(e)


def latest(name):
    """(version string, download url) of the newest Windows release."""
    repo = REPOS[name]
    try:
        tag = latest_tag(repo)
        url = f"https://github.com/{repo}/releases/download/{tag}/{name}_cli-{ASSET_SUFFIX}"
    except urllib.error.HTTPError:
        raise
    except Exception:  # the web redirect didn't work: fall back to the API
        rel = json.loads(_get(f"https://api.github.com/repos/{repo}/releases/latest"))
        asset = next((a for a in rel.get("assets", []) if a["name"].endswith(ASSET_SUFFIX)), None)
        if asset is None:
            raise RuntimeError(f"{name} {rel.get('tag_name')}: no Windows download in the release")
        tag, url = rel.get("tag_name", "?"), asset["browser_download_url"]
    ver = parse_version(tag)
    return (".".join(map(str, ver)) if ver else tag), url


def newer(a, b):
    """True when version string a is newer than b ('' / None count as unknown -> older)."""
    va, vb = parse_version(a), parse_version(b or "")
    return va is not None and (vb is None or va > vb)


def install(tools, name, version, url, log=print):
    """Download the release zip, put <name>.exe (and its licence files) into tools, remember the version."""
    tools = Path(tools)
    tools.mkdir(parents=True, exist_ok=True)
    log(f"  Downloading {name} {version}…")
    data = _get(url, timeout=120)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        member = next((n for n in z.namelist() if n.lower().endswith(f"{name}.exe")), None)
        if member is None:
            raise RuntimeError(f"{name}.exe isn't in the download")
        tmp = tools / f"{name}.exe.new"
        tmp.write_bytes(z.read(member))
        for n in z.namelist():
            base = n.rsplit("/", 1)[-1]
            if base.upper().startswith(("LICENSE", "LICENCE", "COPYING")):
                (tools / f"{name}-{base}").write_bytes(z.read(n))
    os.replace(tmp, tools / f"{name}.exe")  # fails cleanly if the old exe is still running
    vers = _read_versions(tools)
    vers[name] = version
    _write_versions(tools, vers)
    log(f"  ✔ {name} {version} installed")


# Oodle (Epic / RAD's decompressor) can't be shipped with the app. retoc and repak download this exact file the
# first time they need it (repak's oodle_loader); the app fetches it the same way up front, with the same checksum.
OODLE_DLL = "oo2core_9_win64.dll"
OODLE_URL = ("https://github.com/WorkingRobot/OodleUE/raw/refs/heads/main/Engine/Source/Programs/Shared/"
             "EpicGames.Oodle/Sdk/2.9.10/win/redist/oo2core_9_win64.dll")
OODLE_SHA256 = "6f5d41a7892ea6b2db420f2458dad2f84a63901c9a93ce9497337b16c195f457"


def has_oodle(tools):
    return (Path(tools) / OODLE_DLL).exists()


def install_oodle(tools, log=print):
    """Download oo2core_9_win64.dll next to retoc / repak and check it before saving it."""
    import hashlib
    tools = Path(tools)
    tools.mkdir(parents=True, exist_ok=True)
    log("  Downloading the Oodle decompressor (oo2core_9_win64.dll)…")
    data = _get(OODLE_URL, timeout=120)
    if hashlib.sha256(data).hexdigest() != OODLE_SHA256:
        raise RuntimeError("the downloaded Oodle DLL didn't match its checksum, so it wasn't saved")
    tmp = tools / (OODLE_DLL + ".new")
    tmp.write_bytes(data)
    os.replace(tmp, tools / OODLE_DLL)
    log("  ✔ Oodle decompressor installed")


def check(tools):
    """{name: (installed, latest, url, error)} for both tools (network errors are reported, not raised)."""
    out = {}
    for name in REPOS:
        have = installed(tools, name)
        try:
            ver, url = latest(name)
            out[name] = (have, ver, url, None)
        except Exception as e:  # offline, rate-limited …
            out[name] = (have, None, None, friendly_error(e))
    return out
