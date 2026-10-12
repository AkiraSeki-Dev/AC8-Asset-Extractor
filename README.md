# AC8 Asset Extractor — Ace Combat 8

A utility designed to extract and organize game assets from Ace Combat 8 for modding, research, and educational
purposes. The tool aims to simplify asset extraction and inspection, allowing users to explore aircraft models,
textures, and other game resources for personal projects and technical analysis.

> **Disclaimer:** This tool is intended for legitimate modding, research, and educational use only. Unauthorized
> redistribution, commercial exploitation, and misuse of copyrighted assets are not supported. All extracted assets
> remain the property of their respective copyright holders.

**Download:** the latest version is on the [Releases page](https://github.com/AkiraSeki-Dev/AC8-Asset-Extractor/releases/latest)
— unzip it anywhere and double-click `AC8 Asset Extractor.exe`. Updates install from inside the app (Settings → APP →
Check for updates).

Browse ACE COMBAT 8's assets, tick what you want, and extract it in one click: textures come out as `.png`,
3D models as `.glb` (with skeleton, skin weights, materials and textures), and optionally as `.fbx`.

| Step | Tool | In | Out |
| --- | --- | --- | --- |
| 1. Unpack | retoc (from `AC8 Unpacker\tools`) | game `.utoc/.ucas` | raw `.uasset/.uexp/.ubulk` → `1_Raw_UAssets` |
| 2a. Textures | `ac8_textures.py` | raw textures | `.png` → `2_Final_Export\Textures` |
| 2b. Meshes | `ac8_gltf.py` | raw static + skeletal meshes | `.glb` → `2_Final_Export\Meshes` |
| 2c. FBX (optional) | `ac8_fbx.py` (built in) | `.glb` | `.fbx` → `2_Final_Export\FBX` |
| 2d. PBR (optional) | `ac8_pbr.py` | the exported PNGs | standard PBR maps + AO → `2_Final_Export\PBR…` |

(The scripts are in the `app` folder.) Plus a built-in **3D viewer** (`ac8_glview.py`), an **aircraft & weapons catalog**, and a **texture browser** — see below.

**Textures:** normal textures and *virtual textures* (the big UDIM aircraft liveries). A UDIM texture becomes one
PNG per tile: `T_PP0023_f14d_001_C.1001.png`, `.1002.png` …

**Meshes:** static meshes (`SM_`) and skeletal meshes (`SK_`, e.g. the main airframe with its moving-part bones),
highest detail level. Each material slot is linked to its material instance and gets its colour, normal and
metal/roughness textures embedded in the `.glb`. UDIM materials are split into one part per tile so they display
correctly in any glTF viewer, Blender, Unreal, Unity… Normal maps are converted to glTF's convention, and the
metal/roughness channels are repacked. Scale is real-world metres (the F-14D comes out 19.1 m long).

**FBX:** written by the app itself (`ac8_fbx.py`) — no Blender or any other program needed. Binary FBX 7.4 with
the mesh, normals, UVs, one material slot per part, the skeleton with skin weights and bind pose, and the colour /
normal / ORM textures embedded. Centimetres, Y up (what Unreal, Maya and 3ds Max expect; Blender converts on
import). Tick **Export FBX**.

**PBR maps:** AC8 packs several maps into one texture. The converter unpacks them into the layout your tool wants:

| Game texture | Red | Green | Blue |
| --- | --- | --- | --- |
| `_C` | colour | colour | colour |
| `_MRX` | metallic | roughness | (unused) |
| `_MRO` / `_MROS` | metallic | roughness | ambient occlusion |
| `_NO` | normal X | normal Y | ambient occlusion |
| `_N` | normal X | normal Y | normal Z |

Presets: **Separate maps** (BaseColor, Normal, Metallic, Roughness, AO — Blender / Substance), **Unreal** (ORM),
**Unity HDRP** (Mask Map), **Unity URP / Built-in** (Metallic + Smoothness, AO), **glTF** (ORM). Normal maps come out
OpenGL or DirectX style (preset default, or pick one). UDIM tiles are named `name_Map.1001.png`. The game's own
baked AO is used — no baking needed. The GLB files also get this AO.

Each tool also works on its own (from the `app` folder):
`python ac8_textures.py <raw> <out> [--max-texture 2048]`, `python ac8_gltf.py <raw> <out> [--max-texture 2048]`,
`python ac8_pbr.py <png folder> <out> --preset unreal`, `python ac8_fbx.py <glb or folder> <out>`,
`python ac8_livery.py <raw> <final>`, `python ac8_viewer.py <file.glb or folder>` (web viewer).

## Start

Double-click **`AC8 Asset Extractor.exe`**. It's a small launcher that starts the app with no console window and
shows an error box if Python or a package is missing. It needs Python 3.10+ installed (and stays in this folder).
`python app\AC8_Asset_Extractor.py` works too.

### What's in the folder

```
AC8 Asset Extractor.exe   the launcher — double-click this
app\                      the program: Python scripts, fonts, icons, web viewer
AC8 Unpacker\             key finder + retoc / repak (the unpacking tools) in tools\
data\                     your settings, asset list and game-update history (made on first start)
logs\                     one log per session (Logs folder link, bottom right)
AC8_Exports\              your extractions (or wherever Settings → Exports folder points)
```

Updates replace `app\`, the launcher and the unpacker script; `data\`, `logs\`, `AC8_Exports\`, your AES key and the
tools are never touched.

### Rebuilding the .exe

The source is in `launcher\` (Go, standard library only; the icon and version info are in
`rsrc_windows_amd64.syso`, which `go build` picks up automatically). From the `launcher` folder:

```
go build -ldflags "-H windowsgui -s -w" -o "..\AC8 Asset Extractor.exe" .
```

or double-click `launcher\build.bat`. `-H windowsgui` = no console window, `-s -w` = smaller file.

If anything is missing, an orange **Setup needed** bar tells you what. Click **Settings** (top right) to:

- **Install packages** — Pillow and numpy (converters), pycryptodome (Find key), PyOpenGL and pyopengltk
  (the 3D viewer).
- **Tools → Check for updates** — installs or updates retoc / repak (downloaded automatically on first start).
- **APP → Check for updates** — updates the app itself from this repo's releases.
- **AES key** — filled from `AC8 Unpacker\aes-key.txt`. The first time (and after a game update that changes it)
  click **Start game offline**, wait for the title screen, then **Find key**; afterwards **Remove offline launch
  option** so the game starts normally.
- **Game folder** — found through Steam automatically. **Exports folder** — where extraction folders go.
- **Max texture size** — per texture / per UDIM tile (default Full size; pick 2048 px for much smaller files).
- **Also make PBR maps** — run the PBR converter automatically after each convert (default: Separate maps).

## Use

**First time:** load the asset list. Either click **Scan Game** (reads every container once, about a minute) or
**Import List…** and pick a listing you already have, such as the `output.txt` made by `ac8_unpack.py list`.
If `output.txt` is in the `AC8 Unpacker` folder it loads automatically. The list is saved as `data\asset_index.txt`;
scan again after a game update.

**Every time:**

1. Narrow it down with the dropdowns in the top bar:
   - **Category** — top folder (Vehicles, Environments, Character, Animation …)
   - **Group** — the folder inside it (Aircraft, Ground …)
   - **Type** — Static meshes, Skeletal meshes, Textures, Materials, Animations, Blueprints, Levels, VFX, Audio, Data
   and/or type in the **search bar** (several words = all must match, e.g. `f14d cockpit`).
2. **Tick** folders or single assets (click the box; Space ticks the highlighted rows). Ticking a folder takes
   everything inside it; untick a subfolder to leave it out. **Tick All Shown / Tick All Results** ticks
   everything currently listed.
3. Press **▶ Extract Ticked**. If a Type is chosen, only that type is extracted from what you ticked.

The ASSETS / MESHES / TEXTURES columns show how much is in each folder before you extract it.

Top-right options: **Export PNG+GLB** (off = just unpack the raw files), **Export FBX**, **Keep Raw** (off =
delete `1_Raw_UAssets` after a successful convert), **Stop**.

**Extractions** (right panel) lists every extraction folder with what was ticked, the date, a status
(RUNNING / RAW / DONE) and file counts, plus **View 3D**, **Convert** (re-run step 2 — use this on extractions made
with older versions), **Raw** and **Final** buttons. Click a card to select it (the Textures tab then shows it).

### Tabs

- **Assets** — the browser described above. **Preview 3D** shows whatever is highlighted (or ticked) in the 3D
  viewer *before* extracting: it unpacks just those meshes plus their materials and textures into
  `AC8_Exports\_Previews\`, converts them and opens the viewer (a few seconds). Previews are cleared when the app
  closes (Settings → 3D previews → Keep between sessions to keep them).
- **Catalog** — the whole game, sorted (the picked entry's name and its buttons stay in a bar at the top while
  its parts list scrolls; **Preview airframe** also brings the landing gear, refuel probe and pylons along):
  - **Aircraft** (player and enemy, including the online-mode ones, marked *online*), with their parts sorted by
    what they are: airframe, cockpit, wings & control surfaces, landing gear, special weapon pylon sets, drop
    tanks & pods, refuel probe, weapons & turrets, damage models, plus the liveries;
  - **Weapons** by type (missiles, air-to-ground, anti-ship, bombs, rockets & guns, special…), with each variant;
    **launchers & racks** (textures only — their geometry is part of each aircraft's pylon sets);
  - **Ground vehicles, Ships, Bosses (online), Characters, Gadgets, Environments, Effects, User interface,
    Emblems** and **Everything else** — every file in the game is in one of these. Each entry lists its 3D
    models by sub-folder with **Preview** / **Tick**, plus **Preview** (first 30 models), **Tick all** and
    **Extract now**.
  - **New in game updates** (top of the list): every time **Scan Game** finds files that weren't there at the
    last scan, they're recorded with the date. Open an update to see which catalog entries got new content and
    the new files by folder (tick a folder to extract just the new stuff). Entries with new content are marked
    **• NEW** in the tree. The record is kept in `data\asset_history.json`.
  Every part has **Preview** and **Tick**; aircraft also have Tick whole aircraft / Extract now / Preview all parts.
  **Find weapons (beta)** unpacks the aircraft's data tables and lists weapon names it finds in them — it may come
  back empty, because the game can store that link as numbers (see "Going further" below).
  Names come from the codes in the folder names; codes it doesn't recognise are shown as the code.
- **Textures** — thumbnails of an extraction's PNGs, filtered by type (colour, normal + AO, metal/rough + AO,
  emissive, emblems) and name. Click one to see it large and per channel (R, G, B, A). **Convert this set** /
  **Convert all sets** makes PBR maps with the preset and normal style you pick.

### 3D viewer

The viewer is part of the app: the **3D Viewer** tab draws the model itself with OpenGL on your graphics card
(no browser, no extra window). It takes the full width while you're on it: the display controls are in the bar
above the view (**Model**, shading, **Map**, Wireframe / Skeleton / Grid, background), and the sidebar on the
right has **Parts**, **Bones** and **Info**. Needs PyOpenGL + pyopengltk (Settings › Install packages) and a graphics driver with OpenGL 3.3, which
any PC from the last ~12 years has. **Open in browser** still shows the same model in the web viewer if you
ever want that.

Orbit (left-drag), pan (right-drag or Shift+left-drag), zoom (mouse wheel), **F** or double-click to frame.
Fly around with **W A S D** (forward / left / back / right), **Space** up, **Ctrl** down, hold **Shift** to go
faster — handy inside cockpits and big environment pieces. Shading: lit,
colour only, normals, clay; wireframe, skeleton, grid, background colour.
**Parts** lists every material on the model; open one with its arrow (all start folded — **Expand all** /
**Collapse all**) to see the textures it uses (Colour / Normal / Metal-Rough), so you can check a part is using
the right ones: tick a part to show/hide it (**only** = show just that one), tick
a texture to switch just that texture off, and click a texture's name to see it in the preview box and switch the
view to that map. **Map** paints every part with only its colour, normal or metal/rough/AO map (parts that
don't have that map show a dark checkerboard).
**Aircraft** (floating panel, top-right of the view, for jets — **hide** folds it away): **Livery** switches between every paint scheme the game has for that aircraft —
its colour / metal-rough / normal textures, the **emblems** it puts on the tails, wings and nose (the game places
them on the aircraft's second UV set, in fixed slots: TailLeft/Right, WingLeft/Right, WingBottomLeft/Right,
FrontBodyLeft/Right, Add01Left/Right), and custom **paint** (a mask texture whose red / green / blue areas are painted
with the livery's Layer01–03 colours; shown approximately). Tail colours like a white fin with a black tip are
painted into the livery's own colour texture, so they appear as soon as you pick that livery. Liveries need the
whole aircraft folder extracted (the app also unpacks the emblems they use from the shared emblem folders). Aircraft
numbers ("203") are drawn too: every livery sets its three digits (`Number01–03_Select`), the font, size and
colour for the tail, nose and wing numbers, and the app draws them from the game's digit sheet
(`T_Aircraft_Number`, unpacked automatically; a built-in font stands in until it's there). Placement, spacing and slant
follow the game's own livery shader, so they sit where they do in game. **Gear** (down / up), **Probe** (stowed /
extended) and **Weapon pylons** (set 1–6) attach those parts to the airframe — they're separate meshes in the game.
**Edit livery** (in the panel): change the number (digits, which areas show it, size, height, position, colour,
font), put any extracted emblem in any slot and size it, recolour the paint layers, and swap the colour texture —
e.g. Queen's orange tail is simply a different colour texture (`T_…_007q_C`) from the black-tailed Joker one.
**Save as…** keeps it as a custom livery (★ in the list, stored in `Liveries\<aircraft>.json`); **Reset** undoes.
Free-fly extras: **Shift+wheel** changes the WASD fly speed, **Alt+wheel** narrows / widens the field of view.
**Bones** poses the skeleton — pick a bone and turn it with the sliders (gear doors, flaps, airbrakes, swing wings…).
**Info** shows vertex/triangle counts, materials, textures, bones and real-world size. **Screenshot** saves a PNG
next to the model (twice the on-screen size). With several models (View 3D on an extraction), pick one in
**Model**.

## Features

**Extracting**
- Browse all ~89,000 game files by folder, type and search; tick folders or single files and extract in one click.
- Each extraction gets its own folder (short names for one aircraft or character: `F18C_…`, `Joker1_Cope_…`,
  `NPC_XO_…`), with the raw files, the converted files, an info file and a full log.
- Keep or drop the raw unpacked files; re-convert an old extraction without unpacking again.

**Converting**
- Textures to PNG, including the big UDIM virtual textures (one PNG per tile), at full size or capped per texture.
- Static and skeletal meshes to GLB with skeleton, skin weights, materials and embedded textures, in real-world
  metres; meshes stored in the separate `.ubulk` file, with variable bones per vertex or with out-of-order material
  sections all convert.
- FBX written by the app itself (no Blender): skeleton, skin weights, bind pose, both UV sets, embedded textures.
- PBR maps in seven presets: Separate maps (Blender / Substance), Unreal (ORM), Unity HDRP (Mask Map),
  Unity URP / Built-in, glTF (ORM) and DCS World (RoughMet, matching Substance's "PBR to DCS"), with OpenGL or
  DirectX normals and the game's own baked AO.

**Catalog**
- Every aircraft (player, enemy, online) with its parts sorted: airframe, cockpit, control surfaces, landing gear,
  pylon sets, drop tanks, refuel probe, weapons, damage models, liveries.
- Weapons by type with every variant, launchers and racks, ground vehicles, ships, online bosses, gadgets,
  environments, effects, UI, emblems and everything else.
- Characters named where known — the pilots (Joker 1 Rex, Joker 2 Professor, Joker 3 Tasha, Joker 4 Coster,
  Queen 1 Kate, Moon 11, the Shadow pilots) and the story characters (Cope, Kate, Jenkins, XO, D.D., Nikolai
  Seversky, General Toprak, Carmille Tewy…) — with each character's meshes grouped into outfits, accessories, faces,
  hair, beards and glasses; online avatar parts in their own group.
- **New in game updates:** after a game update, Scan Game shows exactly which files are new.
- Preview any part in 3D straight from the catalog; previews are temporary and cleared when the app closes.

**Texture browser**
- Thumbnails of an extraction's textures, filtered by kind and name; view any one large and per channel; convert
  one set or all sets to PBR maps.

**3D viewer**
- Drawn by the app on your graphics card: lit, colour, normals and clay shading, wireframe, skeleton, grid,
  per-map views (colour / normal / metal-rough-AO).
- Orbit, pan, mouse-wheel zoom, WASD free-fly (Shift+wheel for speed), Alt+wheel field of view (35° by default),
  framing that fits the whole model.
- Show / hide parts and single textures, see which textures each material uses, pose the skeleton with sliders,
  model info and real-world size, high-resolution screenshots, and the same model in a web viewer.
- Aircraft open with landing gear down and refuel probe stowed; switch gear, probe and pylon sets.

**Liveries**
- Every paint scheme the game has for an aircraft: its textures, paint layers and colours, emblems in the game's
  slots and tail / nose / wing numbers, drawn with the game's own placement maths and digit sheet.
- **Edit livery:** numbers (digits, areas, size, position, colour, font), emblems in any slot, paint colours and
  colour texture; save as custom liveries.
- **Export textures:** bakes the livery — paint, emblems and numbers — into the colour maps and writes every
  texture set of the model in any PBR preset and size.

**Getting the game key**
- **Start game offline** sets up the offline launch through Steam by itself, **Find key** reads the AES key from
  the running game, and **Remove offline launch option** puts your Steam launch options back.

**Keeping up to date**
- The app updates itself from this repo's releases (Settings → APP → Check for updates), keeping your settings,
  asset list, exports, logs and key.
- retoc and repak update from their own releases (Settings → Tools → Check for updates) and download by
  themselves on first start; a quiet daily check notes any update in the log.
- A `logs` folder with one log per session, including full error details.

## Version history

v1.0: first public release.

## Output folders

Every extraction gets its own folder, named after what you ticked:

```
AC8_Exports\
  PP0023_f14d_2026-10-11_1148\
    1_Raw_UAssets\        straight from the game paks (Live\Content\...)
    2_Final_Export\
      Textures\           .png, same folders as in the game (UDIM tiles as name.1001.png …)
      Meshes\             .glb, same folders as in the game
      FBX\                .fbx (when Export FBX is on)
      PBR\, PBR_<preset>\  PBR maps (auto, or from the Textures tab)
      Liveries\          <aircraft>.json: every livery's textures, emblems and paint colours
    extraction_info.txt   what was ticked, type, date, file counts
    extraction_log.txt    full log of every step
```

## Going further: exact weapon loadouts and material settings

Which weapons an aircraft carries, and every material's exact settings, are stored in the game's data in a compact
form that can't be read without the game's *type mappings* (a `.usmap` file). That file is made by running a
dumper tool (e.g. Dumper-7) inside the running game — the same "start without anti-cheat" route used for Find key.
If you make one, send it over and the catalog can read the real loadouts instead of guessing from names.

## Tips

- **Tick the whole aircraft folder** (e.g. Vehicles › Aircraft › PP › PP0023_f14d) so the meshes, their material
  instances and textures come out together — that's how the converter links textures to each part. Parts whose
  material lives elsewhere (shared cockpit glass, ejection seat…) come out light grey unless you tick that too.
- In each aircraft folder: `SK_…` is the main airframe (moving parts as bones), `SK_…_CP` the cockpit version,
  `SK_…_DAM` damaged versions, `SM_…_SWP*` weapon pylon sets, `SM_…_GearOn/Off` gear, `DPTK` drop tanks.
- Materials, animations, audio, blueprints… are unpacked into `1_Raw_UAssets` but not converted.
- 3D previews are cleared when the app closes (Settings → 3D previews to keep them instead).
- Anything that can't be converted gets a line in the log; the rest of the extraction carries on.
- Settings (game folder, exports folder, dropdowns) are remembered in `data\ac8_extractor_settings.json`.

For offline single-player modding of a copy you own. Don't redistribute extracted game content (see the disclaimer
at the top).

The bundled Outfit font (`app\fonts\`) is SIL Open Font License; it is loaded by the app only, nothing is installed.
Logo and icon: the Wings of Theve wing, in `app\assets\`.
three.js (viewer) is MIT licensed, in `app\viewer\lib\`.


## Credits

- **retoc** — https://github.com/trumank/retoc — Copyright (c) 2025 Truman Kilen and Archengius, MIT License.
  Asset conversion by Archengius; debugging and testing by LongerWarrior.
- **repak** — https://github.com/trumank/repak — Copyright 2024 Truman Kilen, spuds, MIT / Apache-2.0.

Both are bundled in `AC8 Unpacker\tools` and kept up to date with Settings → Check for updates.
