# AC8 Asset Extractor — Ace Combat 8

A utility designed to extract and organize game assets from Ace Combat 8 for modding, research, and educational
purposes. The tool aims to simplify asset extraction and inspection, allowing users to explore aircraft models,
textures, and other game resources for personal projects and technical analysis.

> **Disclaimer:** This tool is intended for legitimate modding, research, and educational use only. Unauthorized
> redistribution, commercial exploitation, and misuse of copyrighted assets are not supported. All extracted assets
> remain the property of their respective copyright holders.

## Install

Get the latest version from the [Releases page](https://github.com/AkiraSeki-Dev/AC8-Asset-Extractor/releases/latest):

- **`AC8_Asset_Extractor_vX.Y.Z_Setup.exe` (recommended):** run it and follow the steps. It brings its own Python, so
  nothing else needs installing, and it doesn't need admin rights. It adds a Start menu shortcut (and a desktop one if
  you like) and appears in Windows' **Apps** list for uninstalling. Windows may show "Windows protected your PC" because
  the installer isn't signed: click **More info → Run anyway**.
- **`AC8_Asset_Extractor_vX.Y.Z.zip` (portable):** unzip it anywhere and double-click **`AC8 Asset Extractor.exe`**.
  Needs [Python 3.10 or newer](https://www.python.org/downloads/) installed (tick **Add python.exe to PATH**).

## First-time setup

Open **Settings** (top right). A red **Setup needed** bar on the main screen tells you if anything is still missing.

1. **Install packages** (Settings → SETUP): only for the zip version; installs what the app needs in one click. The
   installer already includes them.
2. **Game folder:** usually found automatically through Steam (or click **Auto-detect**). Otherwise use **Browse…**
   and pick the folder that holds `start_protected_game.exe`.
3. **AES key:** the game's files are encrypted, so the key is read from the running game, once:
   1. Click **Start game offline**. Steam closes for a moment, then the game starts in offline mode.
   2. Wait for the title screen, then click **Find key**.
   3. Close the game and say **Yes** to remove the offline launch option, so the game starts normally again.

   Repeat this only if a game update changes the key.
4. Back on the main screen, click **Scan Game** to load the list of game files (about a minute). Scan again after
   each game update.

## Extracting

- **Assets tab:** find files with the **Category / Group / Type** dropdowns or the search bar, select them, and press
  **▶ Extract Selected**.
- **Catalog tab:** the same files sorted into aircraft, weapons, characters, vehicles, environments and so on. Pick
  an entry and use **Extract now**, or **Preview** a part in 3D first.
- **Select the whole aircraft folder** so its meshes, materials and textures come out together. That's how textures
  get linked to each part.
- Options next to the Extract button: **Export PNG+GLB** (convert the files), **Export FBX**, **Keep Raw** (keep the
  unpacked game files).
- In Settings: **Max texture size** (Full size, or 2048 px for much smaller files) and **Also make PBR maps** (Blender,
  Unreal, Unity, glTF or DCS World layout).

## 3D viewer

Open it with **Preview 3D** (Assets tab), **Preview** in the catalog, or **View 3D** on an extraction. The **Controls**
button (bottom right of the view) lists all of these.

| Action | How |
| --- | --- |
| Orbit | middle-drag (or left-drag) |
| Pan | **Shift** + middle-drag (or right-drag) |
| Zoom | mouse wheel |
| Snap to a side | click an axis ball on the circle in the top right corner (drag the circle to rotate) |
| Look at a texture up close | open a part with ▸, click a texture name, then click its picture |
| Frame the model | **F**, double-click, or the button under the axis circle |
| Free fly on / off | **Ctrl + F** or **Shift + `** (left-click or Esc also stops) |
| Free fly | move the mouse to look, **W A S D** move, **Q** down, **E** up, wheel forward/back |
| Fly speed | **Shift + mouse wheel** |
| Field of view | **Alt + mouse wheel** |
| Pick a bone | click it in the view (with **Skeleton** on), or in the **Bones** list |
| Translate / rotate a bone | **G** / **R** (or the buttons on the left of the view), then drag an arrow / a ring. Rotation uses the bone's own X / Y / Z |
| Exact bone values | **Bones** panel: type the Translation / Rotation numbers (or scroll over them); Rotation also has sliders |
| Reset a bone | **Alt + G** / **Alt + R**, or **Reset translation** / **Reset rotation** (Bones panel) |
| Look at the selected bone | **.** (period) |

- **Model → All parts together** shows every mesh in the list at once (damaged / alternate versions left out), e.g.
  a boss built from many parts. In the catalog, bosses have **Preview whole vehicle**.
- **Aircraft panel** (top left of the view): pick a **Livery** and attach the **Gear**, **Probe** and **Pylons**.
  **Edit livery** changes the numbers, emblems and colours; **Save as…** keeps your version.
- **Export textures…** (top bar) saves the model's textures with the livery baked in, in the PBR layout and size you
  choose.
- **Screenshot** saves a PNG of the view (without its buttons) next to the model.
- Posing bones only changes what the viewer shows; exported files keep the model's original pose.

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
      1_Raw_UAssets\           the unpacked game files (only with Keep Raw)
      2_Final_Export\
        Textures\  Meshes\  FBX\  PBR_<preset>\  Textures_Export\  Liveries\
      extraction_info.txt     what was selected, when, and counts
      extraction_log.txt      the full log
```

## Updating

- **The app:** Settings → APP → **Check for updates**. Your settings, file list, exports and key are kept.
- **The unpacking tools:** Settings → SETUP → **Check for updates**.
- Every time it starts, the app checks for a newer app, retoc and repak. If there is one, a green bar shows it with
  **Update app** / **Update tools** buttons.

## Tips

- Parts that come out light grey use a material from another folder (e.g. shared cockpit glass): select that too.
- If something fails, the log at the bottom says why, and the rest of the extraction carries on. Send the file
  from the **Logs folder** when reporting a problem.
- 3D previews are cleared when the app closes (Settings → 3D previews to keep them).

## Version history

v1.2.1: a better 3D viewer and catalog.
- Viewer controls work like Blender: middle-drag rotate, Shift+middle-drag pan, wheel zoom. **Ctrl+F** (or
  Shift+`) turns on free fly: mouse look, W A S D, Q down, E up; left-click or Esc to stop. The **Controls** button
  lists everything.
- An axis circle in the top right: click a ball to look from that side, drag it to rotate. The button under it
  frames the model.
- Bosses: **Preview whole vehicle** in the catalog, and **Model → All parts together** shows every part at once.
- Click a texture's preview to open it full size (zoom, pan, R/G/B/A channels). Clicking a texture no longer
  changes the 3D view's shading.
- Fixed the resize cursor getting stuck over the catalog list. Online characters moved to **Online avatars**.
- "Tick" is now "Select" everywhere; the version number shows under the title.

v1.2: fixed in-app updates failing with "Access is denied" on the app's font (Windows keeps it locked while the app
runs). Updates now skip unchanged files, swap all changed files in together, and finish any locked file the next
time the app starts.

v1.1: a Windows installer (`…_Setup.exe`) that includes its own Python and packages, installs without admin rights,
adds Start menu / desktop shortcuts and an uninstaller. The zip stays available for portable use.

v1.0: first public release.

## Support

If the app helps you and you'd like to support it: [ko-fi.com/akiraseki](https://ko-fi.com/akiraseki) ☕
(also in the app: Settings → APP → Support).

## Credits

- **retoc** — https://github.com/trumank/retoc — Copyright (c) 2025 Truman Kilen and Archengius, MIT License.
  Asset conversion by Archengius; debugging and testing by LongerWarrior.
- **repak** — https://github.com/trumank/repak — Copyright 2024 Truman Kilen, spuds, MIT / Apache-2.0.
- Outfit font (`app\fonts\`): SIL Open Font License. three.js (`app\viewer\lib\`): MIT License.

**Building from source:** the launcher is in `launcher\` (Go). Run `go build -ldflags "-H windowsgui -s -w" -o
"..\AC8 Asset Extractor.exe" .` there, or double-click `build.bat`. The app itself runs with
`python app\AC8_Asset_Extractor.py`.
