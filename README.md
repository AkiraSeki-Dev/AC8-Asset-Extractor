# ACE Combat 8 Asset Extractor

Browse ACE COMBAT 8's assets, tick what you want, and extract it in one click: textures come out as `.png`,
3D models as `.glb` (with skeleton, skin weights, materials and textures), and optionally as `.fbx`.

| Step | Tool | In | Out |
| --- | --- | --- | --- |
| 1. Unpack | retoc (from `AC8 Unpacker\tools`) | game `.utoc/.ucas` | raw `.uasset/.uexp/.ubulk` → `1_Raw_UAssets` |
| 2a. Textures | `ac8_textures.py` | raw textures | `.png` → `2_Final_Export\Textures` |
| 2b. Meshes | `ac8_gltf.py` | raw static + skeletal meshes | `.glb` → `2_Final_Export\Meshes` |
| 2c. FBX (optional) | `ac8_fbx.py` (built in) | `.glb` | `.fbx` → `2_Final_Export\FBX` |
| 2d. PBR (optional) | `ac8_pbr.py` | the exported PNGs | standard PBR maps + AO → `2_Final_Export\PBR…` |

Plus a built-in **3D viewer** (`ac8_glview.py`), an **aircraft & weapons catalog**, and a **texture browser** — see below.

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

Each tool also works on its own:
`python ac8_textures.py <raw> <out> [--max-texture 2048]`, `python ac8_gltf.py <raw> <out> [--max-texture 2048]`,
`python ac8_pbr.py <png folder> <out> --preset unreal`, `python ac8_fbx.py <glb or folder> <out>`,
`python ac8_livery.py <raw> <final>`, `python ac8_viewer.py <file.glb or folder>` (web viewer).

## Start

Double-click **`AC8 Asset Extractor.exe`**. It's a small launcher that starts the app with no console window and
shows an error box if Python or a package is missing. It needs Python 3.10+ installed (and stays in this folder).
`python AC8_Asset_Extractor.py` still works too.

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
- **Download tools** — only if retoc/repak are missing.
- **AES key** — filled from `AC8 Unpacker\aes-key.txt`. After a game update, start the game without anti-cheat
  and click **Find key** (see the unpacker README).
- **Game folder** — found through Steam automatically. **Exports folder** — where extraction folders go.
- **Max texture size** — per texture / per UDIM tile (default Full size; pick 2048 px for much smaller files).
- **Also make PBR maps** — run the PBR converter automatically after each convert (default: Separate maps).

## Use

**First time:** load the asset list. Either click **Scan Game** (reads every container once, about a minute) or
**Import List…** and pick a listing you already have, such as the `output.txt` made by `ac8_unpack.py list`.
If `output.txt` is in the `AC8 Unpacker` folder it loads automatically. The list is saved as `asset_index.txt`;
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
  `AC8_Exports\_Previews\`, converts them and opens the viewer (a few seconds; repeat previews open instantly).
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
    **• NEW** in the tree. The record is kept in `asset_history.json` next to the app.
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
(`T_Aircraft_Number`, unpacked automatically; a built-in font stands in until it's there). The game's exact number
placement can't be read, so the numbers are fitted into their slots and you can move them. **Gear** (down / up), **Probe** (stowed /
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

## Updating from an older version

v2.38: the app updates itself from GitHub (AkiraSeki-Dev/AC8-Asset-Extractor): Settings → APP → **Check for
updates** downloads the newest release, copies it over the app folder (your settings, asset list, exports, logs, AES
key and tools are kept) and restarts. The daily quiet check also mentions app updates in the log. A fresh install
downloads retoc and repak by itself on first start if they're missing.

v2.37: retoc and repak stay bundled and can be updated from the app: Settings → Tools shows their versions and
**Check for updates** installs the newest Windows release from GitHub (trumank/retoc, trumank/repak). Once a day the
app also checks quietly and notes in the log when an update is out. Credits and licences are in
`AC8 Unpacker\tools\THIRD-PARTY-NOTICES.txt` (linked from Settings).

v2.36: every aircraft opens in the 3D viewer with its gear down and refuel probe stowed (when those parts are there),
even after looking at something else first. What you pick in the Gear / Probe / Pylons dropdowns stays while you keep
looking at the same aircraft.

v2.35: fixed "could not locate the LOD header" for real this time (SM_PI501_001 and similar): some meshes store
their material sections out of index order, which the header check didn't allow. Those meshes also picked up a false
material list ("BlockAll"); the real slot list at the end of the mesh is now chosen.

v2.34: the Export textures and Settings windows open in the middle of the app window.

v2.33: story characters are listed in callsign order too: Joker 1 (Cope), Joker 2, Joker 3, Joker 4, Queen 1, then
the rest.

v2.32: static meshes whose full-detail model is streamed from the `.ubulk` file (instead of stored in the package)
now convert — this was the "could not locate the LOD header" error (e.g. SM_PI501_001). The logs folder is now
`logs` (an existing `Logs` folder is renamed). Names: PC001 is "Squadron outfits PC001 · Joker flight", NC010 is
"Nikolai Seversky".

v2.31: Find key reads the key straight away; if the game isn't running offline it says so. The divider between the
catalog list and the page can be dragged (the width is remembered). Character names: Joker 1 (Rex),
Joker 1 (Cope), Queen 1 (Kate), Jenkins, XO, Medic, D.D., Nikolai Seversky, Civilian 01, General Toprak, Carmille
Tewy; "main roster" is no longer added to the names (they are still listed first).

v2.30: the offline start is automatic. Settings → **Start game offline** puts `AC8_Offline.json` in the game folder,
closes Steam for a moment, adds the offline launch option to ACE COMBAT 8 in Steam's own settings
(`Steam\userdata\<you>\config\localconfig.vdf`, backed up first as `.ac8bak`) and starts the game through Steam.
At the title screen click **Find key**. After the key is found the app offers to **Remove offline launch option**,
which puts back whatever launch options you had before so the game starts normally (also a button in Settings).
The start-without-Steam route has been removed.

v2.29: **Find key** opens a step-by-step window built around the Steam launch option (the stable way): copy the
launch option, **Start through Steam** (finds the game's Steam id from your library), **Find key now** at the title
screen, then remove the launch option. `AC8_Offline.json` is put in the game folder when the window opens. Starting
without Steam is still there as a fallback.

v2.28: getting the AES key is one step. **Find key** offers to start the game in offline mode for you: it puts
`AC8_Offline.json` in the game folder and runs `start_protected_game.exe -anticheat_settings=AC8_Offline.json` with
`EOS_USE_ANTICHEATCLIENTNULL=1`, then reads the key once the title screen is up. Settings also has **Launch game
offline**, and **Steam launch option…** for when the game only starts through Steam:
`cmd /c "set EOS_USE_ANTICHEATCLIENTNULL=1&& %command% -anticheat_settings=AC8_Offline.json"` (copy button) — add
it once, find the key, then remove it again.

v2.27: main roster pilots read "Pilot PI004 · Joker 1 (Rex) · main roster" (names: Rex, Professor, Tasha, Coster, Kate).

v2.26: PI402 / PI403 are the generic Shadow squadron pilots (`Shadow_Generic_Pilot`, `Shadow_Generic_Helmet_Pilot`).

v2.25: shorter extraction folder names when you extract one aircraft or one character: `F18C_2026-10-12_1600`,
`SU57_…`, `Joker1_Cope_…`, `NPC_XO_…`; pilot (cockpit) models end in `_Pilot` (`Joker1_Rex_Pilot_…`). Characters
without a known name use their code (`Online_UN003`, `Animal_AC001`). Anything else is named as before.

v2.24: static meshes that failed with "could not locate the LOD header" (e.g. SM_PI501_003): every candidate vertex
buffer is now tried, and empty material sections no longer stop the LOD header from being recognised. Characters are
named (NC001-NC013 story characters, PI401-PI403 Moon 11 / Shadow, PC001 Joker flight outfits); NM and UN are shown
together as Online.

v2.23: pilot names corrected (PI004 = Joker 1, PI002 = Joker 2, PI003 = Joker 3, PI001 = Joker 4, PI005 = Queen 1,
PI101 = Joker 1 alt), main roster listed in callsign order; AC characters are labelled Animal. A character's page now
groups its meshes by what they are — Outfits, Accessories, Faces, Hair, Beards, Glasses, Face accessories — instead
of by folder (FACE/FACE0001/MESH …).

v2.22: 3D previews no longer pile up. The unpacked game files are deleted as soon as a preview is converted, and
`AC8_Exports\_Previews` is wiped when the app closes (and at start-up, in case it didn't close cleanly). The viewer
holds the open model in memory. Settings → 3D previews: tick "Keep between sessions" for the old behaviour (faster
to reopen), or "Clear now" to free the space. The leftover `_Shaders` folder from the removed shader export is
deleted at start-up.

v2.21: the 3D viewer opens at a 35° field of view. The mouse wheel moves the camera closer / further; only
Alt+wheel changes the field of view (on Windows, Num Lock being on made every wheel turn change it). Framing fits the
whole model from the current angle, so tall character models and outfits are no longer cut off. The catalog splits
characters by folder: pilots first (PI001-PI005 = Joker 1-4 and Queen 1, the main roster), then the other character
sets, and online avatar parts get their own group. **Export game shader** has been removed. Small fixes: closing
the Settings or Export textures window no longer leaves errors behind, unused code tidied up.

v2.20: fixed "unexpected element size 262144" on meshes with a variable number of bones per vertex (e.g.
`SK_PC001_Cloth02_Acce01`): the weight lookup buffer has its own 2-byte header, which was read as data.

v2.19: **Export textures…** in the 3D viewer (top bar, and under the livery picker) bakes the shown livery — paint
layers, emblems and numbers — into the colour maps and writes every texture set of the model in the preset and size
you pick (`2_Final_Export\Textures_Export\<model>_<livery>\<preset>`). New preset **DCS World (RoughMet)**, same as
Substance Painter's "PBR to DCS": `<set>.png` (colour + alpha), `<set>_Roughmet.png` (R AO, G roughness, B metallic),
`<set>_Normal.png` (DirectX); it is also offered for PBR after converting and in the Textures tab. Meshes with a
variable number of bones per vertex (e.g. pilot helmets `SK_PIxxx_Acce01`) now open. A **Logs** folder next to the
exe gets one file per session with everything the log panel shows plus full error details (Logs folder link at the
bottom right). The catalog keeps the groups you opened when you come back from another tab and starts each picked
entry's page at the top; the Extractions panel is wider, with its buttons on their own row.

v2.18: tail number colours come from each livery's materials. `Tail_Color_Left` / `Tail_Color_Right` weren't read
as colours before (their names don't end in "Color"), so every tail fell back to the default orange; e.g. the Shadow
liveries (MP_*_Shadow_Decal) set white. The game's default orange is drawn as it looks in the game (sRGB 230, 67,
45). Livery files from 2.17 and older need the aircraft extracted again — the viewer offers Extract.

v2.17: extracting also unpacks the shared materials and textures the meshes use from outside the ticked folders
(e.g. the ejection seat's MI_MK14 in Vehicles/Aircraft/Common/EjectionSeat, the targeting pod, canopy glass), so
those parts get their textures. Click **Convert** on an existing extraction to fetch them (needs the raw files).

v2.16: numbers and emblems use the game's own maths, read from its compiled livery shader (MP_*_Decal base pass):
- Numbers: p = (U + offsetX_L on the left / offsetX_R on the right, V + offsetY); p.x += (p.y - 1) x SlantDegree
  (mirrored on the left); P = (p - 0.5) / scale + 0.5; each digit is the sheet cell (Font_Select, digit) drawn
  where P (shifted by Number_Interval per digit, +0.015 next to a 1) falls in [1, 1.1]. Font_Select is the
  sheet's column. Red = digit, green = digit + outline (Edge_Switch / Edge_Color).
- Emblems: drawn on UV1 tile row -AreaIndex (Wing top is area 2, Wing bottom area 1).
No more fitted numbers. Livery files update themselves.

v2.15: **Export game shader** no longer unpacks the whole shader library (retoc ran out of memory doing that on
the full game). It reads the containers' table of contents, finds the livery materials' compiled shaders, and copies
just those shader groups — a few MB, no big temporary folder. The old `AC8_Exports\_Shaders\_library` folder can be
deleted.

v2.14: the livery editor scrolls (mouse wheel) when it's taller than the viewport, so its lower controls —
emblem placement, paint, Save as… — are always reachable. **Export game shader** is also on the viewer's top bar.

v2.13: **Export the game's livery shader** now finds the shader list retoc writes next to the library
(ShaderAssetInfo-<name>.assetinfo.json) — before it looked for the wrong file name and found nothing.

v2.12: numbers use only the red channel of `T_Aircraft_Number` — that's the digit; green is the digit plus an
outline, drawn in `<Area>_Edge_Color` only when `<Area>_Edge_Switch` is on (reading both made the digits bold).
Placement refined against Joker 1 on the F/A-18C: the first digit is centred on offsetX, and the number's bottom is
0.78 x offsetY / scale up from the bottom of its area (nose comes down to just above the formation light strip, the
tail number goes up above the antenna fairings). That 0.78 and the 0.7 digit spacing are fitted to in-game shots;
the exact figures are in the compiled shader (Export the game's livery shader).

v2.11: emblem sizes use the game's own formula, read from its compiled material: an emblem is `<Slot>DecalScale`
/ 5 of its area across (bigger value = bigger emblem). Joker 1's card (Wing slot, drawn on the tail, scale 0.7)
is now a small card at the bottom back of the fin like in the game, instead of filling it; the tail wing (2.3) and
the Wings of Theve banner keep their in-game sizes. The editor's emblem Size shows that share.

v2.10: a livery file made before v2.6 (by the extraction of an older version) can't be fully repaired: those
versions misread some values — e.g. `<Slot>DecalAreaIndex`, which part of the jet an emblem is drawn on — and v2.7
replaced them with the game's defaults. The viewer now says so and shows **Extract**, which reads the aircraft
again from the game (Joker 1's card then goes where the game puts it instead of on the wing tops).

v2.9: numbers are placed by the game's own offsets (checked against the Joker 1 F/A-18C in the game): each
number starts `<Area>_offsetX_R` across its area's UV tile on the left side and `<Area>_offsetX_L` on the right
side, with its bottom `<Area>_offsetY` up from the bottom of the tile. The livery editor's Left X / Right X / Up
sliders edit those values. Livery files are updated to the new format automatically.

v2.8: emblem moves go the game's way round — `<Slot>DecalTranslateX` moves an emblem aft on both sides (it went
forward before), so Joker's card sits at the back of the fin and the Wings of Theve banner under the front of the
canopy. Livery files from older versions: a value those versions misread (a stored 0, e.g. the Joker card's
`WingDecalAreaIndex` = tail) now comes back as 0. FBX exports carry the second UV set ("DecalUV") that emblems and
numbers use. Digits no longer pick up bits of their neighbours on the number sheet when seen from far away.

v2.7: numbers match the game's look. Columns 3 and 4 of `T_Aircraft_Number` are leaning (italic) digits;
`<Area>_SlantDegree` is a shear, not an angle — the F/A-18C's -0.2 stands them upright, mirrored on the right side.
Digits follow each other at 0.7 of a sheet cell x (1 + `Number_Interval`), measured on the game's own Joker 1
F/A-18C (the exact figure is in the compiled shader). Livery files made by older versions are now updated from the
values they stored when the unpacked files weren't kept (Keep Raw off). **Export the game's livery shader** (livery
editor) saves the compiled pixel shaders of the aircraft's livery materials as a small zip — the exact maths for
where numbers sit is in there.

v2.6: liveries use the game's own values for everything the files store.
- **Colours**: livery values that aren't set fall back to the shared parent material's defaults, which are read
  from the compiled material data inside `MP_<aircraft>_Decal` (e.g. tail numbers default to red, nose and wing
  numbers to grey), instead of made-up colours.
- **Fonts**: `<Area>_Font_Select` = n picks a pair of columns in `T_Aircraft_Number`: the left side uses column
  2n+1, the right side 2n+2 (Font_Select 1 = columns 3 and 4).
- **Number size**: `<Area>_scale` — each digit is scale/10 of the slot's UV tile (the game reads the sheet as a
  10 x 10 grid), and `Number_Interval` sets the spacing.
- **Emblem placement**: `<Slot>DecalTranslateX/Y` (moves it; mirrored on the right side), `<Slot>DecalAreaIndex`
  (which part of the aircraft it's drawn on — Joker's emblem in the Wing slot is drawn on the tail, the Wings of
  Theve banner under the canopy) and `<Slot>DecalFlipRight`. All editable in the livery editor (Move X / Move Y,
  Drawn on, Mirror on the right side).
- Still fitted: where a number sits inside its slot. The game's offsets (`<Area>_offsetX_L/R`, `offsetY`) are used
  by its compiled pixel shader, which isn't in the asset files; they're kept in the livery file under "game".
Livery files made by older versions are rebuilt automatically when the viewer opens them.

v2.5: numbers are read from the game's own `T_Aircraft_Number` sheet (one row per digit 0-9, one column per font;
`Tail/Body/Wing_Font_Select` picks the column). The sheet has no alpha — the digits are bright on black — so the
brightness is used as the see-through mask, and the livery's number colour tints it. Emblem sizes now follow the
game: `<Slot>DecalScale` repeats the slot's texture coordinates, so a bigger value means a *smaller* emblem (2.5 on
the nose = 40% of the slot). The editor's emblem **Size** shows that the right way round (Size = 1 / game scale).
Emblems without an alpha channel use black as see-through. Click **Convert** on an aircraft extraction so the
viewer picks up the number sheet.

v2.4: the Livery list shows the game's own skins by name. Every livery folder is a complete set made by the game
(colour texture, emblems, number, paint) — e.g. on the F-14D, 021/031/041 are Joker variants and 051 is Queen —
so pick it instead of editing. The skins you can choose in the game are listed first (they're the ones with a skin
icon, shown in the panel); the rest are cutscene / enemy versions, marked "variant". Names come from the game's skin
tables (`Datatables/Information/DT_Skin`, `Datatables/UI/DA_NarrativeSkinDataAsset`) and its English text file
(`Localization/Game/en/Game.locres`), which an aircraft extraction now unpacks too; click **Convert** on an existing
aircraft extraction (with the AES key set) to fetch them. What was found is written to
`2_Final_Export\Liveries\<aircraft>_skin_names.txt`.

v2.3: when the viewer shows a jet whose liveries aren't extracted, an **Extract** button appears next to
Parts / Bones / Info — it extracts the whole aircraft and reopens it with its liveries. Flying with WASD now moves
the turning point next to the camera, so dragging inside a cockpit looks around instead of swinging around a point
far ahead (the mouse wheel still zooms; Alt+wheel is the field of view; F puts everything back). New installs start
with Full size textures, Separate PBR maps, Export FBX on and Keep Raw off. Files older versions left behind
(the old uasset extractor, the .bat, Blender/Edge helpers) are removed automatically at start-up.

v2.2: aircraft numbers on the jets, a livery editor, the Aircraft panel floats inside the 3D view, Shift+wheel fly
speed and Alt+wheel field of view. Click **Convert** on an aircraft extraction (with the AES key set) to fetch the
number font sheet and refresh its livery list.

v2.1: liveries, emblems and custom paint in the 3D viewer; landing gear / refuel probe / weapon pylons attach to
the airframe; the catalog's buttons moved to a bar at the top. GLBs now also carry the second UV set (decal slots).
To get liveries for an aircraft you already extracted, click **Convert** on it (with the AES key set, it also fetches
the emblems); new extractions get them automatically. Each aircraft's liveries are listed in
`2_Final_Export\Liveries\<aircraft>.json` (textures, emblem slots and paint colours per livery).

v2.0: **FBX no longer needs Blender** — the app writes FBX itself (and much faster). Materials now pick the
part's own textures over shared helper textures (a tiling "flakes noise" normal was winning on some cockpits,
e.g. the F/A-18C), and look the part's textures up by name when the material doesn't list them. The viewer's
controls moved to the bar above the view, parts fold open, and you can fly with WASD. Catalog groups start
closed. Click **Convert** on extractions to rebuild their GLB/FBX with the better texture picking.

v1.9: the catalog covers every asset (not just aircraft and weapons) and tracks what each game update adds; the
viewer shows and toggles each part's textures. Canopy "reflection" textures (`…_Reflection_N`) are no longer
used as normal maps — they made cockpits look blotchy in the viewer and in Blender. Click **Convert** on aircraft
extractions to rebuild their GLB/FBX (previews rebuild by themselves).

v1.8: the 3D viewer is now native (drawn by the app with OpenGL) instead of a web page; the first time, click
**Install packages** in Settings when the Setup bar asks. Top-right options are now **Export PNG+GLB**,
**Export FBX** and **Keep Raw**. Texture thumbnails now use the alpha channel (emblems/decals show their shape).

v1.7 fixes models showing **inside out** (you saw the inside faces and could see through the jet). The normals
were decoded as unsigned numbers when the game stores them signed, and the triangle order was flipped on top of
that. Existing GLB/FBX files made by older versions are still inside out — click **Convert** on those extraction
cards to rebuild them (no re-unpacking needed). 3D previews rebuild by themselves the next time you open them.

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
- Previews pile up in `AC8_Exports\_Previews\` — delete that folder any time to free space.
- Anything that can't be converted gets a line in the log; the rest of the extraction carries on.
- Settings (game folder, exports folder, dropdowns) are remembered in `ac8_extractor_settings.json`.

For offline single-player modding of a copy you own. Don't redistribute extracted game content.

The bundled Outfit font (`fonts\`) is SIL Open Font License; it is loaded by the app only, nothing is installed.
Logo and icon: the Wings of Theve wing, in `assets\`.
three.js (viewer) is MIT licensed, in `viewer\lib\`.


## Credits

- **retoc** — https://github.com/trumank/retoc — Copyright (c) 2025 Truman Kilen and Archengius, MIT License.
  Asset conversion by Archengius; debugging and testing by LongerWarrior.
- **repak** — https://github.com/trumank/repak — Copyright 2024 Truman Kilen, spuds, MIT / Apache-2.0.

Both are bundled in `AC8 Unpacker\tools` and kept up to date with Settings → Check for updates.
