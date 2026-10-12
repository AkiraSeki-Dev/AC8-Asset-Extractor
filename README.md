# AC8 Asset Extractor — Ace Combat 8

A utility designed to extract and organize game assets from Ace Combat 8 for modding, research, and educational
purposes. The tool aims to simplify asset extraction and inspection, allowing users to explore aircraft models,
textures, and other game resources for personal projects and technical analysis.

> **Disclaimer:** This tool is intended for legitimate modding, research, and educational use only. Unauthorized
> redistribution, commercial exploitation, and misuse of copyrighted assets are not supported. All extracted assets
> remain the property of their respective copyright holders.

## Install

1. Install [Python 3.10 or newer](https://www.python.org/downloads/) (tick **Add python.exe to PATH**).
2. Download the latest zip from the [Releases page](https://github.com/AkiraSeki-Dev/AC8-Asset-Extractor/releases/latest)
   and unzip it anywhere.
3. Double-click **`AC8 Asset Extractor.exe`**.

## First-time setup

Open **Settings** (top right). An orange **Setup needed** bar tells you if anything is still missing.

1. **Install packages:** installs what the app needs (one click).
2. **Game folder:** usually found automatically through Steam. Otherwise use **Browse…** and pick the folder that
   holds `start_protected_game.exe`.
3. **AES key:** the game's files are encrypted, so the key is read from the running game, once:
   1. Click **Start game offline**. Steam closes for a moment, then the game starts in offline mode.
   2. Wait for the title screen, then click **Find key**.
   3. Close the game and say **Yes** to remove the offline launch option, so the game starts normally again.

   Repeat this only if a game update changes the key.
4. Back on the main screen, click **Scan Game** to load the list of game files (about a minute). Scan again after
   each game update.

## Extracting

- **Assets tab:** find files with the **Category / Group / Type** dropdowns or the search bar, tick them, and press
  **▶ Extract Ticked**.
- **Catalog tab:** the same files sorted into aircraft, weapons, characters, vehicles, environments and so on. Pick
  an entry and use **Extract now**, or **Preview** a part in 3D first.
- **Tick the whole aircraft folder** so its meshes, materials and textures come out together. That's how textures
  get linked to each part.
- Options next to the Extract button: **Export PNG+GLB** (convert the files), **Export FBX**, **Keep Raw** (keep the
  unpacked game files).
- In Settings: **Max texture size** (Full size, or 2048 px for much smaller files) and **Also make PBR maps** (Blender,
  Unreal, Unity, glTF or DCS World layout).

## 3D viewer

Open it with **View 3D** on an extraction or **Preview** in the catalog.

| Action | How |
| --- | --- |
| Orbit / pan | left-drag / right-drag |
| Zoom | mouse wheel |
| Frame the model | **F** or double-click |
| Fly | **W A S D**, **Space** up, **Ctrl** down, hold **Shift** for speed |
| Field of view | **Alt + mouse wheel** |

- **Aircraft panel** (top right of the view): pick a **Livery** and attach the **Gear**, **Probe** and **Pylons**.
  **Edit livery** changes the numbers, emblems and colours; **Save as…** keeps your version.
- **Export textures…** (top bar) saves the model's textures with the livery baked in, in the PBR layout and size you
  choose.
- **Screenshot** saves a PNG next to the model.

## Where things go

```
AC8 Asset Extractor\
  AC8 Asset Extractor.exe   start here
  app\                      the program
  AC8 Unpacker\             key finder and unpacking tools
  data\                     your settings and file list
  logs\                     one log per session (Logs folder link, bottom right)
  AC8_Exports\              your extractions:
    F18C_2026-10-12_1600\
      2_Final_Export\
        Textures\  Meshes\  FBX\  PBR_<preset>\  Textures_Export\  Liveries\
      extraction_log.txt
```

## Updating

- **The app:** Settings → APP → **Check for updates**. Your settings, file list, exports and key are kept.
- **The unpacking tools:** Settings → SETUP → **Check for updates**.
- The app checks once a day by itself and notes any update in the log at the bottom.

## Tips

- Parts that come out light grey use a material from another folder (e.g. shared cockpit glass): tick that too.
- If something fails, the log at the bottom says why, and the rest of the extraction carries on. Send the file
  from the **Logs folder** when reporting a problem.
- 3D previews are cleared when the app closes (Settings → 3D previews to keep them).

## Version history

v1.0.2: the Oodle decompressor (`oo2core_9_win64.dll`), which can't be shipped in the download, is now fetched by the
app on first start and by Settings → Tools → Check for updates, and checked before it's saved.

v1.0.1: update checks no longer fail with "rate limit exceeded" — they use GitHub's normal release pages instead of
its API, which only allows a few lookups an hour per internet connection.

v1.0: first public release.

## Credits

- **retoc** — https://github.com/trumank/retoc — Copyright (c) 2025 Truman Kilen and Archengius, MIT License.
  Asset conversion by Archengius; debugging and testing by LongerWarrior.
- **repak** — https://github.com/trumank/repak — Copyright 2024 Truman Kilen, spuds, MIT / Apache-2.0.
- Outfit font (`app\fonts\`): SIL Open Font License. three.js (`app\viewer\lib\`): MIT License.

**Building from source:** the launcher is in `launcher\` (Go). Run `go build -ldflags "-H windowsgui -s -w" -o
"..\AC8 Asset Extractor.exe" .` there, or double-click `build.bat`. The app itself runs with
`python app\AC8_Asset_Extractor.py`.
