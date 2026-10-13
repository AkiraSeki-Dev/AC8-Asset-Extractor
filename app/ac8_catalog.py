"""Catalog of everything in the game: aircraft (with parts), weapons, and every other area (vehicles, ships,
characters, environments …) — built from the asset list (file and folder names only), so new content from game
updates shows up as soon as the game is scanned again.

Names are a best-effort lookup from the codes in the folder names; unknown codes are shown as the code itself.
"""
import re

# aircraft code (last part of the folder name, e.g. PP0023_f14d -> f14d) -> display name
AIRCRAFT_NAMES = {
    "a10c": "A-10C Thunderbolt II", "f02a": "F-2A", "f04e": "F-4E Phantom II", "f15c": "F-15C Eagle",
    "f22a": "F-22A Raptor", "f35c": "F-35C Lightning II", "f18c": "F/A-18C Hornet", "j39e": "JAS 39E Gripen E",
    "su34": "Su-34", "e18g": "EA-18G Growler", "typn": "Typhoon", "f15j": "F-15J", "a06e": "A-6E Intruder",
    "rflm": "Rafale M", "su25": "Su-25", "mr2k": "Mirage 2000", "m29a": "MiG-29A", "su57": "Su-57",
    "f16c": "F-16C Fighting Falcon", "f14d": "F-14D Super Tomcat", "f18f": "F/A-18F Super Hornet",
    "f15e": "F-15E Strike Eagle", "su47": "Su-47", "m21b": "MiG-21bis", "su35": "Su-35", "m31b": "MiG-31B",
    "su33": "Su-33", "e06b": "EA-6B Prowler", "f18e": "F/A-18E Super Hornet", "f14a": "F-14A Tomcat",
    "adfx02": "ADFX-02 Morgan", "f117": "F-117A Nighthawk", "b02a": "B-2A Spirit", "b52h": "B-52H",
    "f05e": "F-5E Tiger II", "e02c": "E-2C Hawkeye", "e03a": "E-3 Sentry", "c130": "C-130",
    "yf23": "YF-23 Black Widow II",
}

# weapon code (w_<code>_<variant>) -> (category, display name). Classic Ace Combat abbreviations.
WEAPON_NAMES = {
    "msl": ("Missiles", "Standard missile (MSL)"),
    "aam": ("Missiles", "Air-to-air missile (AAM)"),
    "qaam": ("Missiles", "Quick-maneuver AAM (QAAM)"),
    "4aam": ("Missiles", "4-target AAM (4AAM)"),
    "laam": ("Missiles", "Long-range AAM (LAAM)"),
    "saam": ("Missiles", "Semi-active AAM (SAAM)"),
    "mmsl": ("Missiles", "MMSL"),
    "agm": ("Air-to-ground", "Air-to-ground missile (AGM)"),
    "4agm": ("Air-to-ground", "4-target AGM (4AGM)"),
    "8agm": ("Air-to-ground", "8-target AGM (8AGM)"),
    "lagm": ("Air-to-ground", "Long-range AGM (LAGM)"),
    "lacm": ("Air-to-ground", "Long-range cruise missile (LACM)"),
    "lasm": ("Anti-ship", "Long-range anti-ship missile (LASM)"),
    "sasm": ("Anti-ship", "Anti-ship missile (SASM)"),
    "ugb": ("Bombs", "Unguided bombs (UGB)"),
    "ugbl": ("Bombs", "Unguided bombs, large (UGBL)"),
    "gpb": ("Bombs", "Guided penetration bomb (GPB)"),
    "xsdb": ("Bombs", "XSDB"),
    "sod": ("Bombs", "Stand-off dispenser (SOD)"),
    "sffs": ("Bombs", "SFFS"),
    "mpbm": ("Bombs", "MPBM"),
    "mop": ("Bombs", "MOP"),
    "rkt": ("Rockets & guns", "Rocket launcher (RKT)"),
    "rktl": ("Rockets & guns", "Rocket launcher, large (RKTL)"),
    "mgp": ("Rockets & guns", "Missile / gun pod (MGP)"),
    "tls": ("Special", "Tactical laser system (TLS)"),
    "eml": ("Special", "Electromagnetic launcher (EML)"),
    "iews": ("Special", "Electronic warfare system (IEWS)"),
    "ecmp": ("Special", "ECM pod (ECMP)"),
    "4uav": ("Special", "UAVs (4UAV)"),
    "ggm": ("Special", "GGM"),
    "sam": ("Ground & ship", "Surface-to-air missile (SAM)"),
    "ssm": ("Ground & ship", "Surface-to-surface missile (SSM)"),
    "usm": ("Ground & ship", "USM"),
    "vls": ("Ground & ship", "Vertical launch system (VLS)"),
    "ssp": ("Ground & ship", "SSP"),
}
WEAPON_CATEGORY_ORDER = ["Missiles", "Air-to-ground", "Anti-ship", "Bombs", "Rockets & guns", "Special",
                         "Ground & ship", "Other"]

# part rules for the files in an aircraft folder: (section, label, test)
PART_SECTIONS = ["Airframe", "Cockpit", "Wings & control surfaces", "Landing gear", "Special weapon pylons",
                 "Drop tanks & pods", "Refuel probe", "Weapons & turrets", "Damage models", "Other meshes"]
CONTROL_SURFACES = ("elevator", "flap", "rudder", "aileron", "canard", "slat", "wing", "airbrake", "stabil")


def aircraft_code(folder_name):
    """PP0023_f14d -> f14d ; PP0013_a06e_ms15 -> a06e"""
    parts = folder_name.split("_")
    return parts[1].lower() if len(parts) > 1 else folder_name.lower()


def aircraft_name(folder_name):
    code = aircraft_code(folder_name)
    name = AIRCRAFT_NAMES.get(code, code.upper())
    extra = folder_name.split("_")[2:]
    if extra:
        name += f" ({'_'.join(extra)})"
    return name


def classify_part(stem, folder_name):
    """Mesh file stem -> (section, label)."""
    s = stem
    low = s.lower()
    tail = s.split(folder_name, 1)[1].lstrip("_") if folder_name in s else s.split("_", 1)[-1]
    tl = tail.lower()
    if s.startswith("SK_") and tail == "":
        return "Airframe", "Airframe (moving parts as bones)"
    if tail == "":
        return "Airframe", "Airframe"
    if "_dam" in low or "debris" in tl or "peeloff" in tl:
        if "mainbody" in low:
            return "Damage models", "Damaged main body"
        return "Damage models", "Damaged airframe" if tl in ("dam", "") else tail
    if tl in ("body", "fuselage", "mainbody"):
        return "Airframe", tail
    if "glass" in tl or "canopy" in tl:
        return "Cockpit", tail
    if tl.startswith("cp") or "_cp_" in low or low.endswith("_cp"):
        return "Cockpit", "Airframe with cockpit interior"
    if "gear" in tl:
        state = "down" if "on" in tl else ("up" if "off" in tl else "")
        kind = "animated" if s.startswith("SK_") else "static"
        return "Landing gear", f"Landing gear {state} ({kind})".replace("  ", " ")
    m = re.match(r"swp(\d+)", tl)
    if m:
        return "Special weapon pylons", f"Special weapon pylon set {m.group(1)}"
    if tl.startswith("dptk"):
        return "Drop tanks & pods", "Drop tank" + (f" {tl[4:]}" if tl[4:] else "")
    if tl.startswith("dpp"):
        return "Drop tanks & pods", "Drop tank pylon" + (f" {tl[3:]}" if tl[3:] else "")
    if "refuel" in tl:
        return "Refuel probe", "Refuel probe " + ("out" if "on" in tl else "stowed" if "off" in tl else "")
    if any(k in tl for k in CONTROL_SURFACES):
        return "Wings & control surfaces", tail
    if any(k in tl for k in ("weapon", "turret", "vls", "gun", "missile", "launcher", "pod")):
        return "Weapons & turrets", tail
    return "Other meshes", tail or stem


# Other areas: (group title, folder prefixes, how many folder levels below the prefix make one item)
COLLECTIONS = [
    ("Ground vehicles", ["Vehicles/Ground", "OnlineMode/Vehicles/Ground"], 1),
    ("Ships", ["Vehicles/Vessels", "OnlineMode/Vehicles/Vessels"], 1),
    ("Bosses (online)", ["OnlineMode/Vehicles/Boss"], 1),
    ("Characters", ["Character"], 2),
    ("Online avatars", ["OnlineMode/Avatar"], 2),
    ("Gadgets", ["Gadgets"], 1),
    ("Environments", ["Environments", "OnlineMode/Environment"], 2),
    ("Effects (VFX)", ["VFX", "OnlineMode/VFX"], 1),
    ("Fonts", ["UI/Font", "NUI/Font", "OnlineMode/UI/Font", "[Engine Content]/EngineFonts"], 0),
    ("User interface", ["UI", "NUI", "OnlineMode/UI"], 1),
    ("Emblems", ["Emblem"], 1),
]
OTHER = "Everything else"

# Character/<kind>/<code>: the pilots in the cockpit are Character/PI/PI001 …; PI001-PI005 are the main roster
# Character/<kind>/<code>. NM and UN are both online-mode characters, so they share one label.
CHARACTER_KINDS = {"PI": "Pilot", "NC": "Character", "PC": "Squadron outfits", "NM": "Online", "UN": "Online",
                   "AC": "Animal", "MP": "MP"}
CHARACTER_KIND_ORDER = ["Pilot", "Character", "Squadron outfits", "Online", "Animal", "MP"]
ONLINE_KINDS = ("NM", "UN")  # online-mode characters: listed under Online avatars, not Characters
# names confirmed in game: the pilot codes don't follow the callsign order (PI001 is Joker 4, PI004 is Joker 1)
CHARACTER_NAMES = {
    "PI004": "Joker 1 (Rex)", "PI002": "Joker 2 (Professor)", "PI003": "Joker 3 (Tasha)", "PI001": "Joker 4 (Coster)",
    "PI005": "Queen 1 (Kate)", "PI101": "Joker 1 (alt)", "PI401": "Moon 11 (Tewy)",
    "PI402": "Shadow squadron (generic) — helmet + flight suit", "PI403": "Shadow squadron (generic) — helmet only",
    "PC001": "Joker flight",
    "NC001": "Joker 4 (Coster)", "NC002": "Joker 2 (Professor)", "NC003": "Joker 3 (Tasha)",
    "NC004": "Joker 1 (Cope)", "NC005": "Queen 1 (Kate)", "NC006": "Jenkins", "NC007": "XO", "NC008": "Medic",
    "NC009": "D.D.", "NC010": "Nikolai Seversky", "NC011": "Civilian 01",
    "NC012": "General Toprak", "NC013": "Carmille Tewy",
}
# short names for export folders: Callsign_Name, or NPC_Name without a callsign; pilots (in the cockpit) get _Pilot
CHARACTER_EXPORT = {
    "PI004": "Joker1_Rex", "PI002": "Joker2_Professor", "PI003": "Joker3_Tasha", "PI001": "Joker4_Coster",
    "PI005": "Queen1_Kate", "PI101": "Joker1_Alt", "PI401": "Moon11_Tewy", "PI402": "Shadow_Generic",
    "PI403": "Shadow_Generic_Helmet",
    "NC001": "Joker4_Coster", "NC002": "Joker2_Professor", "NC003": "Joker3_Tasha", "NC004": "Joker1_Cope",
    "NC005": "Queen1_Kate", "NC006": "NPC_Jenkins", "NC007": "NPC_XO", "NC008": "NPC_Medic", "NC009": "NPC_DD",
    "NC010": "NPC_Nikolai", "NC011": "NPC_Civilian01", "NC012": "Shadow22_Toprak", "NC013": "Moon11_Tewy",
    "PC001": "JokerFlight_Outfits",
}
KIND_EXPORT = {"PI": "Pilot", "NC": "NPC", "PC": "Outfits", "NM": "Online", "UN": "Online", "AC": "Animal"}


def export_name(keys):
    """Short folder name for an extraction when everything ticked belongs to one aircraft or one character:
    PP0007_f18c -> F18C, PP0013_a06e_ms15 -> A06E_MS15, Character/NC/NC007 -> NPC_XO, Character/PI/PI004 ->
    Joker1_Rex_Pilot. None when the ticked files are spread over several of them (or aren't either)."""
    names = set()
    for k in keys:
        parts = k.rstrip("/").split("/")
        if len(parts) >= 4 and parts[:2] == ["Vehicles", "Aircraft"] and parts[2] in ("PP", "OP"):
            a = parts[3]
        elif len(parts) >= 4 and parts[:3] == ["OnlineMode", "Vehicles", "Aircraft"] and parts[3][:2] in ("PP", "OP"):
            a = parts[3]
        else:
            a = None
        if a and "_" in a:
            names.add("_".join(a.split("_")[1:]).upper())
            continue
        if len(parts) >= 3 and parts[0] == "Character" and re.fullmatch(r"[A-Z]{2}\d{3}", parts[2]):
            code = parts[2]
            known = CHARACTER_EXPORT.get(code)
            names.add(known + ("_Pilot" if parts[1] == "PI" else "") if known
                      else f"{KIND_EXPORT.get(parts[1], parts[1])}_{code}")
            continue
        return None
    return names.pop() if len(names) == 1 else None


# Joker 1-4, Queen 1 (pilots and their story characters): listed first, in this order
MAIN_ROSTER = ["PI004", "PI002", "PI003", "PI001", "PI005"]
ROSTER_ORDER = ["NC004", "NC002", "NC003", "NC001", "NC005"]

# character mesh names: SK_<code>_<Part><nn>[_<Part><nn>] -> section on the character's page
CHARACTER_PARTS = [("Cloth", "Outfits"), ("Acce", "Accessories"), ("Face", "Faces"), ("Hair", "Hair"),
                   ("Beard", "Beards"), ("Glasses", "Glasses"), ("Grasses", "Glasses")]
CHARACTER_PART_ORDER = ["Outfits", "Accessories", "Faces", "Hair", "Beards", "Glasses", "Face accessories",
                        "Models"]


def character_part(stem):
    """SK_NC001_Face01_Beard01 -> 'Beards'; SK_NC001_Cloth01_Acce01 -> 'Accessories';
    SK_NC001_Face01_Acce01 -> 'Face accessories'; SK_AC001_001 -> 'Models'."""
    tokens = [re.sub(r"\d+$", "", t) for t in stem.split("_")[2:]]
    tokens = [t for t in tokens if t]
    if not tokens:
        return "Models"
    last = tokens[-1]
    if last == "Acce" and len(tokens) > 1 and tokens[-2] == "Face":
        return "Face accessories"
    for prefix, label in CHARACTER_PARTS:
        if last.lower().startswith(prefix.lower()):
            return label
    return last


def character_title(kind, code):
    """('PI', 'PI004') -> 'Pilot PI004 · Joker 1 (Rex)'; shared folders keep their own name."""
    if not re.fullmatch(r"[A-Z]{2}\d{3}", code or ""):
        return f"{kind} · {code}" if code else f"{kind} (shared)"
    name = CHARACTER_NAMES.get(code)
    tag = f" · {name}" if name else ""
    return f"{CHARACTER_KINDS.get(kind, kind)} {code}{tag}"


def _is_mesh(name):
    return name.startswith(("SM_", "SK_"))


def build(index_files):
    """index_files: iterable of display keys (e.g. 'Vehicles/Aircraft/PP/PP0023_f14d/Model/SK_PP0023_f14d.uasset').
    -> {'aircraft': {folder_key: {...}}, 'weapons': {code: {...}}, 'launchers': {name: [keys]},
        'collections': {group title: {item folder key: {...}}}}"""
    aircraft, weapons, launchers = {}, {}, {}
    collections = {}
    for key in index_files:
        parts = key.split("/")
        afolder, handled = None, True
        if len(parts) >= 5 and parts[0] == "Vehicles" and parts[1] == "Aircraft" and parts[2] in ("PP", "OP"):
            afolder, fkey, online = parts[3], "/".join(parts[:4]), False
        elif (len(parts) >= 5 and parts[:3] == ["OnlineMode", "Vehicles", "Aircraft"]
              and parts[3][:2] in ("PP", "OP") and parts[3][2:3].isdigit()):
            afolder, fkey, online = parts[3], "/".join(parts[:4]), True
        if afolder:
            folder = afolder
            a = aircraft.setdefault(fkey, {"folder": folder, "side": folder[:2], "name": aircraft_name(folder),
                                           "code": aircraft_code(folder), "meshes": [], "textures": [],
                                           "liveries": set(), "count": 0, "online": online, "keys": []})
            a["keys"].append(key)
            a["count"] += 1
            stem = parts[-1].rsplit(".", 1)[0]
            if stem.startswith(("SM_", "SK_")):
                a["meshes"].append((key,) + classify_part(stem, folder))
            elif stem.startswith("T_"):
                a["textures"].append(key)
                if len(parts) > 5 and parts[4] == "Textures" and re.fullmatch(r"\d{3}[a-z]?\w*", parts[5]):
                    a["liveries"].add(parts[5])
        elif len(parts) >= 4 and parts[0] == "Vehicles" and parts[1] == "Weapons" and parts[2].startswith("w_"):
            bits = parts[2].split("_")
            code = bits[1]
            cat, label = WEAPON_NAMES.get(code, ("Other", code.upper()))
            w = weapons.setdefault(code, {"code": code, "category": cat, "name": label, "variants": {}})
            v = w["variants"].setdefault(parts[2], {"folder": "/".join(parts[:3]), "meshes": [], "count": 0})
            v["count"] += 1
            if parts[-1].startswith(("SM_", "SK_")):
                v["meshes"].append(key)
        elif len(parts) >= 6 and parts[:4] == ["Vehicles", "Aircraft", "Common", "Launcher"]:
            entry = launchers.setdefault(parts[4], {"folder": "/".join(parts[:5]), "meshes": [], "count": 0})
            entry["count"] += 1
            if parts[-1].startswith(("SM_", "SK_")):
                entry["meshes"].append(key)
        else:
            handled = False
        if not handled:
            _add_collection(collections, key, parts)
    return {"aircraft": aircraft, "weapons": weapons, "launchers": launchers, "collections": collections}


def _add_collection(collections, key, parts):
    group, depth, plen, sort = OTHER, 1, 1, None
    for title, prefixes, d in COLLECTIONS:
        hit = next((pf for pf in prefixes if key.startswith(pf + "/")), None)
        if hit:
            group, depth, plen = title, d, hit.count("/") + 1
            break
    n = min(plen + depth, len(parts) - 1)  # folder levels that make the item
    if n <= 0:
        ikey, title = parts[0] if len(parts) > 1 else "(top level)", "(top level files)"
    else:
        ikey = "/".join(parts[:n])
        title = " / ".join(parts[plen - 1 if group == OTHER else plen:n]) or parts[n - 1]
        if group == "Characters" and parts[plen:plen + 1] and parts[plen] in ONLINE_KINDS:
            # Character/NM and Character/UN are the online mode's characters: they belong with the online avatars
            group = "Online avatars"
            sub = parts[plen:n]
            title = f"Online character {sub[1]}" if len(sub) > 1 else f"Online characters ({sub[0]}, shared)"
            sort = "1 " + title.lower()
        elif group == "Characters":
            sub = parts[plen:n]
            title = character_title(sub[0], sub[1] if len(sub) > 1 else "")
            kind = CHARACTER_KINDS.get(sub[0], sub[0])
            order = CHARACTER_KIND_ORDER
            rank = order.index(kind) if kind in order else len(order)
            code = sub[1] if sub[1:] else ""
            order_ = MAIN_ROSTER if code in MAIN_ROSTER else ROSTER_ORDER
            main = code in order_
            sort = f"{rank:02d} " + (f"0 {order_.index(code)}" if main else f"1 {title.lower()}")
        elif group == "Fonts":
            title = {"UI/Font": "Game fonts (UI/Font)", "NUI/Font": "Menu fonts (NUI/Font)",
                     "[Engine Content]/EngineFonts": "Unreal Engine fonts"}.get(ikey, ikey)
        elif group == "Online avatars":
            title = "Avatar · " + " / ".join(parts[plen:n])
            sort = "0 " + title.lower()   # avatar parts first, then the online characters
    item = collections.setdefault(group, {}).setdefault(ikey, {"folder": ikey, "title": title, "meshes": [],
                                                                "textures": 0, "count": 0, "keys": [],
                                                                "sort": sort or title.lower()})
    item["count"] += 1
    item["keys"].append(key)
    name = parts[-1]
    if _is_mesh(name):
        item["meshes"].append(key)
    elif name.startswith("T_"):
        item["textures"] += 1


GROUP_ORDER = ["Ground vehicles", "Ships", "Bosses (online)", "Characters", "Online avatars", "Gadgets", "Environments",
               "Effects (VFX)", "User interface", "Fonts", "Emblems", OTHER]


WEAPON_TOKEN = re.compile(r"\bw_([a-z0-9]+)_[a-z]\d\b", re.I)


def weapons_in_names(names):
    """Weapon folder tokens (w_qaam_a0 …) found in a package's name map."""
    found = set()
    for n in names:
        for m in WEAPON_TOKEN.finditer(n):
            found.add(m.group(0).lower())
    return sorted(found)
