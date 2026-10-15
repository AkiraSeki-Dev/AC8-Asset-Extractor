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
- **Catalog tab:** the same files sorted into aircraft, weapons, characters, vehicles, environments, fonts and so on.
  Pick an entry and use **Extract now**, or **Preview** a part in 3D first. Selected rows are outlined in orange
  with a tick (click the row, its box or **Select** again to deselect). Selecting a model also selects the materials
  and textures it uses (read from the mesh the first time, then remembered). With something selected, the page's
  button reads **Extract selected** and extracts just that; with nothing selected, **Extract now** takes the whole
  entry.
- **Fonts:** the game's fonts come out as the original `.ttf` / `.otf` files (Catalog → **Fonts**, or select a
  `Font` folder), in `2_Final_Export\Fonts`.
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
| Translate / rotate a bone | **G** / **R** (or the two buttons at the top left of the view, beside the Aircraft panel), then drag an arrow / a ring. Rotation uses the bone's own X / Y / Z |
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

## DCS World page

The **DCS** tab has the tools for DCS World mods:

- **Texture converter**, four ways:
  - **AC8 → PBR:** an extraction's textures → standard PBR maps (Blender / Substance, Unreal, Unity, glTF).
  - **AC8 → DCS:** an extraction's textures → DCS sets: `<name>` (colour + alpha), `<name>_Roughmet` (R AO · G
    roughness · B metallic), `<name>_Normal` (DirectX).
  - **PBR → DCS:** any PBR maps (BaseColor, Metallic, Roughness, AO, Normal, Opacity) → DCS sets.
  - **DCS → PBR:** DCS sets → BaseColor (+ Opacity), Metallic, Roughness, AO and a normal map.

  **One set:** pick each map into its slot, or drag it in from Explorer (the rest of the set next to it is filled
  in for you), and give it a
  **Name** (the suffixes are added). **Whole folder:** pick an extraction (AC8 modes) or any folder and every set in
  it is converted. **Save as** DDS (compressed, with mipmaps), PNG or TGA.
- **F/A-18 pilot helmet (AC8 → DCS):** moves an Ace Combat 8 pilot helmet's colour, normal and metal/rough maps
  onto the DCS F/A-18 helmet's UV layout. Pick the AC8 colour map (`T_…_Helmet…_C`); the others are found next to
  it (or drag the files from Explorer onto the slots). Give the DCS helmet's own textures to keep its visor and straps (the results get their names). About a
  minute or two. The design flows in smooth curves out to the DCS helmet's edge (the original texture's spill past
  the mesh edge isn't used); AC8's painted strap slots are carried across as they are, to touch up in your editor;
  **Move design up** lifts the front of the design so the name clears the DCS helmet's brow pad; **Name** sets
  the files' name (_Roughmet and _Normal are added), otherwise they're named after the DCS files.

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
        Textures\  Meshes\  FBX\  Fonts\  PBR_<preset>\  Textures_Export\  Liveries\
      extraction_info.txt     what was selected, when, and counts
      extraction_log.txt      the full log
```

## Updating

- **The app:** Settings → APP → **Check for updates**. Your settings, file list, exports and key are kept.
- **The unpacking tools:** Settings → SETUP → **Check for updates**.
- **Release or test builds:** Settings → APP → **Update to** picks what the updater follows: **Release** or
  **Test build** (the "Test build" pre-release, shown as **v-** plus its commit in the top left, e.g. v-0708d9a). It
  starts on the one your copy came from; switch it and Check for updates offers the other one.

## Tips

- Parts that come out light grey use a material from another folder (e.g. shared cockpit glass): select that too.
- If something fails, the log at the bottom says why, and the rest of the extraction carries on. Send the file
  from the **Logs folder** when reporting a problem.
- 3D previews are cleared when the app closes (Settings → 3D previews to keep them).
- Converting uses all your CPU cores (found automatically; with 5 or more, one is left free so the PC stays
  responsive). For faster extractions and smaller files, set **Max texture size** to 2048 px in Settings: full size
  is about three times slower for aircraft textures.

## Version history

v1.3.9:
- Settings → APP → **Update to**: pick whether the updater follows **Release** or **Test build**.
- DCS page › One set: the slots are grouped under the file they go into (e.g. **→ Wing_Roughmet** R AO · G
  roughness · B metallic), so you can see which map makes what.

v1.3.8: the DCS page's texture converter and helmet converter, reworked.
- **Texture converter › One set:** pick each map into its own slot (the rest of the set next to it is filled in)
  and give it a **Name**; the suffixes are added. **Whole folder** works as before. ORM is gone from the page.
- **F/A-18 helmet converter:** the design curves smoothly out to the DCS helmet's edge with no squeezed bands,
  streaks or shifts along the edge; the front name stays clear of the brow pad. AC8's strap slots are left as they
  are (touch them up in your editor). New **Name** field for the output files.
- **Test builds** (the "Test build" pre-release) show **v-** plus their commit in the top left and update
  themselves to newer test builds.

v1.3.7: smoother edges from the F/A-18 helmet converter: the design is stretched out to the DCS helmet's edge with
one smooth stretch instead of row by row, so busy designs (and UV checkers) no longer get stepped, wavy or bent
cells along the edges.

v1.3.6: the F/A-18 helmet converter keeps lettering straight (the back name's letters no longer wobble): each texel
now reads the exact point on the AC8 helmet's surface.

v1.3.5: a cleaner F/A-18 helmet converter.
- Where the DCS shell reaches past the AC8 one at the sides, the design is stretched sideways to fill it, with no
  curves, upward streaks or smeared edges.
- No more hairline cracks through the lettering.
- **Move design up** now sets how far the front name is lifted (the default clears the brow pad).

v1.3.4: the F/A-18 helmet converter leaves everything around the shell transparent when no DCS texture is given,
so the result drops straight onto a layer in Photoshop.

v1.3.3: the F/A-18 helmet converter moves the front of the design up more, so the name clears the DCS helmet's
brow pad (the back stays where it was); nothing is stretched at the front any more, so the name keeps its shape.

v1.3.2: the F/A-18 helmet converter stretches instead of filling.
- Where the DCS shell reaches past the AC8 one (mostly the sides), the paint just inside the AC8 edge is stretched a
  little to cover it - only there, so text near the edges stays intact.
- The strap slots are closed up by stretching the paint beside them, so busy designs carry on with no smudged
  patches. Every pixel comes from the AC8 texture.

v1.3.1: a better F/A-18 helmet converter.
- Paint that's off the AC8 helmet mesh (the original texture's spill past its edges) is no longer carried across,
  and past the AC8 rim the paint pattern carries on instead of being stretched into streaks.
- AC8's painted strap slots are left out (the DCS helmet has its own strap fittings).
- **Move design up** lifts the design to the DCS helmet's slightly higher brow edge (on by default).

v1.3.0: a DCS World page.
- **Texture converter:** AC8 → PBR, AC8 → DCS, PBR → DCS and DCS → PBR, from an extraction or any folder, saved as DDS
  (compressed, with mipmaps), PNG or TGA. DCS sets: `<name>`, `<name>_Roughmet`, `<name>_Normal`.
- **F/A-18 pilot helmet converter:** moves an Ace Combat 8 pilot helmet's colour, normal and metal/rough maps onto
  the DCS F/A-18 helmet's UV layout, keeping the DCS helmet's visor and straps.

v1.2.8: catalog fixes.
- The page's Extract button extracts just what's selected (it reads **Extract selected**); with nothing selected,
  **Extract now** still takes the whole entry.
- Catalog extractions ignore the Type filter, so a model always comes out with its materials and textures (GLB and
  FBX), and the folder is named after the aircraft / character again (not the shared folder its materials are in).
- Short catalog pages no longer scroll.

v1.2.7: clearer selecting in the catalog.
- Selected rows are outlined in orange with a tick and read "✓ Selected"; click the row again to deselect.
- Selecting a model also selects the materials and textures it uses, even ones in other folders (read from the
  mesh the first time, then remembered until the next Scan Game).

v1.2.6: the logo text at the top of the app (and of the 3D viewer page) is back to "ACE COMBAT 8" in capitals, to
match the line under it.

v1.2.5: small fixes.
- Dropdown lists (e.g. the livery list) close when the app window is moved, resized or minimised, or when you
  click another program, instead of staying behind on the screen.
- The game's name is written "Ace Combat 8" everywhere in the app.

v1.2.4: fixes for more than one monitor.
- Dropdown lists (e.g. the livery list) open under their box on the monitor the app is on, instead of jumping to
  the main monitor.
- The full-size texture window opens in the middle of the app's monitor.

v1.2.3: font extraction.
- The game's fonts now come out as their original `.ttf` / `.otf` files, in `2_Final_Export\Fonts`.
- New **Fonts** group in the catalog: game fonts, menu fonts and Unreal Engine fonts.

v1.2.2: bone posing, faster extraction and a tidier viewer.
- **Bones:** click a bone in the 3D view, then **G** (translate) or **R** (rotate) and drag the arrows / rings, in
  the bone's own X / Y / Z. Exact Translation / Rotation numbers (and rotation sliders) in the Bones panel,
  **Alt+G** / **Alt+R** to reset, **.** to look at the selected bone.
- **Faster extraction:** textures, meshes and PBR maps convert on all your CPU cores; meshes no longer re-make
  shared textures for every part (an F-14 went from about 2.5 min to under 30 s for its meshes).
- The axis circle, frame button and bone tools are see-through, drawn inside the view like Blender's.
- Dropdowns open a dark list in the app's style (one scrolling column, e.g. the livery list).
- The app checks for app / retoc / repak updates every time it starts and shows a bar with update buttons.
- Settings → APP → Support: a Ko-fi link, if you'd like to support the app.
- Clean-up of unused code; the README was checked and corrected.

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
