# ac8-unpacker

Unpack the encrypted `.pak` and `.utoc/.ucas` containers of Ace Combat 8: Wings of Theve (Steam, Windows).

The script finds the pak AES-256 key in your own running copy of the game, then drives
[repak](https://github.com/trumank/repak) and [retoc](https://github.com/trumank/retoc) to list and extract files.
No game files are included here.

## Key

The key isn't included here. Get it with `python ac8_unpack.py find-key` while the game runs in offline mode
(or with **Find key** in the AC8 Asset Extractor). It is saved to `aes-key.txt` next to this script.
If a game update changes it, run `find-key` again.

## Requirements

- Windows, Python 3.9 or newer
- `pip install pycryptodome` (only for `find-key`)
- The game installed through Steam

## Use

```
python ac8_unpack.py fetch-tools
python ac8_unpack.py list --filter Cockpit
python ac8_unpack.py extract CUR_FocusCamera out
python ac8_unpack.py extract-pak Live/Config out
```

| Command | What it does |
| --- | --- |
| `fetch-tools` | Downloads the latest repak and retoc releases into `tools\` |
| `find-key` | Finds the key in the running game and saves it to `aes-key.txt` |
| `list [--filter TEXT]` | Lists file paths in all containers |
| `extract FILTER OUT` | Extracts assets whose name contains FILTER as legacy `.uasset`/`.uexp` |
| `extract-pak PATH OUT` | Extracts non-asset files (audio, movies, ini, localisation) under PATH from the `.pak` files |

The game folder is found through Steam. Pass `--game-dir "<folder with start_protected_game.exe>"` if it is not.
With no `aes-key.txt`, pass `--key 0x...` or put the key above in that file.

## Finding the key yourself

The key is not stored in plain form in the exe file, so `find-key` reads it from the running game.
Easy Anti-Cheat blocks that, so start the game without it (offline, single player only):

```
cd /d "<game>\Game\Binaries\Win64"
set SteamAppId=2288340
AceCombat8.exe -SaveToUserDir
```

Wait for the title screen, then in another window:

```
python ac8_unpack.py find-key
```

Every 32-byte candidate is used to decrypt the index of the smallest pak and is accepted only if the result
matches the SHA-1 stored in that pak's footer, so a reported key is always correct. It takes a few seconds.

## Limits

- **Extraction only.** The game refuses containers that are not signed with the publisher's RSA key
  ("Missing signature"), also from `Paks\~mods`. Repacked or new containers do not load until that check is solved.
- Extracted assets are UE 5.4 cooked assets. Editing them needs the usual Unreal tooling.
- Tested on Steam build 25201480 only.

## Notes

For offline single-player modding of a copy you own. Do not run a modified or anti-cheat-free game in online
modes, and do not redistribute extracted game content.

MIT licensed. Not affiliated with Bandai Namco.
