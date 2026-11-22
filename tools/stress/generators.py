"""Seeded, deterministic hostile inputs (no network, no paid API): copy, figures, scale facts and places.

Every generator takes (count, seed) and returns a list of JSON-serialisable values, each tagged with the class it
was drawn from (`cls`) so a failure is reported by class. The first draws walk every class in turn, so even a small
count (--quick) touches each class once; the rest are drawn at random from the seed.
"""
from __future__ import annotations

import random

# ------------------------------------------------------------------------------------------------ vocabularies
WORDS = ["ancient", "stone", "ring", "desert", "temple", "buried", "city", "river", "walls", "ruins", "tower", "gate", "harbour", "palace", "tomb", "shaft", "chamber", "vault", "mosaic", "column", "bridge", "island", "crater", "forest", "valley", "glacier", "canal", "library", "fortress", "altar", "obelisk", "quarry", "marble", "salt", "amber", "copper", "bronze", "iron", "gold", "silver", "tunnel", "cistern", "bastion", "citadel"]
LONG_WORDS = ("Incomprehensibilities", "Counterrevolutionaries", "Llanfairpwllgwyngyll", "Pneumonoultramicroscopic",
              "Mamihlapinatapai", "Tetragrammatonical", "Konstantinopolitanischer", "Hottentottenstottertrottel",
              "Rindfleischetikettierungsueberwachung", "Supercalifragilisticexpialidocious")
NEVER_END = ("of", "the", "a", "an", "in", "on", "at", "to", "for", "with", "by", "from", "and", "or", "but",
             "into", "under", "over", "between", "than")
APOSTROPHES = ("Nebuchadnezzar's walls", "the builders' secret", "O'Connell's map", "it's not a crater",
               "rock 'n' roll", "the king’s tomb", "Ka’ba stone")
HYPHENS = ("Ottoman-era garrison", "glazed-brick lions", "saddam-era bricks", "twenty-first-century survey",
           "north-west face", "well-preserved state-of-the-art vault")
EN_DASH = ("1453–1571 siege", "Rome–Byzantium road", "north–south axis", "Paris–Dakar",
           "2560–2540 BC")
GREEK = ("Ακρόπολη Αθηνών",
         "Κνωσός", "Δελφοί ναός")
CYRILLIC = ("Янтарная комната",
            "Перевал Дятлова",
            "Тунгусский метеорит")
ARABIC = ("البتراء", "مدينة القدس",
          "الأهرامات")
CJK = ("兵馬俑", "万里の長城", "故宫博物院",
       "경복궁")
ACCENTED = ("Kraków", "Łódź", "Ærøskøbing", "Ñuñoa", "Đakovo",
            "Øresund", "İstanbul", "Hôtel-Dieu", "São Luís", "Königsberg",
            "Brașov", "Peč", "Tromsø")
PROPER_RUNS = ("Hagia Sophia Grand Mosque Istanbul Bosphorus", "Saint Petersburg Catherine Palace Tsarskoye Selo",
               "Mohenjo Daro Indus Valley Sindh Pakistan", "Great Zimbabwe Masvingo Province Zimbabwe",
               "Machu Picchu Urubamba Cusco Region Peru")


def _words(rnd: random.Random, n: int) -> str:
    return " ".join(rnd.choice(WORDS) for _ in range(n))


COPY_CLASSES = ("len_1", "len_2", "len_3", "len_5", "len_8", "len_13", "len_21", "len_40", "long_word", "all_caps",
                "mixed_case", "apostrophe", "hyphen", "en_dash", "greek", "cyrillic", "arabic", "cjk", "accented",
                "never_end_first", "never_end_mid", "never_end_last", "never_end_every", "proper_run", "empty",
                "whitespace")


def copy_text(cls: str, rnd: random.Random) -> str:
    if cls.startswith("len_"):
        return _words(rnd, int(cls[4:])).capitalize()
    if cls == "long_word":
        return rnd.choice(LONG_WORDS)
    if cls == "all_caps":
        return _words(rnd, rnd.randint(2, 6)).upper()
    if cls == "mixed_case":
        return " ".join(w.upper() if i % 2 else w.capitalize() for i, w in enumerate(_words(rnd, 4).split()))
    if cls == "apostrophe":
        return rnd.choice(APOSTROPHES)
    if cls == "hyphen":
        return rnd.choice(HYPHENS)
    if cls == "en_dash":
        return rnd.choice(EN_DASH)
    if cls in ("greek", "cyrillic", "arabic", "cjk"):
        pool = {"greek": GREEK, "cyrillic": CYRILLIC, "arabic": ARABIC, "cjk": CJK}[cls]
        return f"{rnd.choice(pool)} {_words(rnd, 2)}"
    if cls == "accented":
        return f"The {rnd.choice(WORDS)} of {rnd.choice(ACCENTED)}"
    if cls.startswith("never_end"):
        base = _words(rnd, 5).split()
        ne = rnd.choice(NEVER_END)
        if cls == "never_end_first":
            base.insert(0, ne)
        elif cls == "never_end_mid":
            base.insert(len(base) // 2, ne)
        elif cls == "never_end_last":
            base.append(ne)
        else:   # a never-end word after every word: every break position meets one
            base = [x for w in base for x in (w, rnd.choice(NEVER_END))]
        return " ".join(base).capitalize()
    if cls == "proper_run":
        return rnd.choice(PROPER_RUNS)
    if cls == "empty":
        return ""
    if cls == "whitespace":
        return rnd.choice((" ", "   ", "\t", " ", " \n "))
    raise ValueError(cls)


def copy_cases(count: int, seed: int, classes=COPY_CLASSES) -> list[dict]:
    rnd = random.Random(seed)
    out = []
    for i in range(count):
        cls = classes[i % len(classes)] if i < len(classes) else rnd.choice(classes)
        out.append({"cls": cls, "text": copy_text(cls, rnd)})
    return out


# ------------------------------------------------------------------------------------------------ figures
FIGURE_CLASSES = ("zero", "tiny_decimal", "huge_sep", "span", "span_descending", "span_era", "bc", "ad", "bce",
                  "ce", "circa", "about", "estimated", "more_than", "between", "unit_pair", "conversion",
                  "negative_depth", "fraction")
UNITS = ("m", "km", "ft", "metres", "meters", "feet", "miles", "kg", "tonnes", "lbs", "years", "people", "steps")


def figure_text(cls: str, rnd: random.Random) -> dict:
    """{"text": the drawn-candidate copy, "signed": the narration sentence that signs it, "value", "unit"}."""
    u = rnd.choice(UNITS)
    n = rnd.randint(2, 9000)
    if cls == "zero":
        t = f"0 {u}"
    elif cls == "tiny_decimal":
        t = f"0.{rnd.randint(1, 9):d}{rnd.randint(0, 9):d}04 {u}"
    elif cls == "huge_sep":
        t = f"{rnd.randint(1, 999):d},{rnd.randint(0, 999):03d},{rnd.randint(0, 999):03d},{rnd.randint(0, 999):03d} {u}"
    elif cls == "span":
        t = f"{n:,}–{n + rnd.randint(1, 500):,} {u}"
    elif cls == "span_descending":
        t = f"{n + rnd.randint(1, 500):,}–{n:,} {u}"
    elif cls == "span_era":
        t = f"{rnd.randint(100, 3000)} BC–AD {rnd.randint(1, 1500)}"
    elif cls == "bc":
        t = f"{rnd.randint(100, 9000)} BC"
    elif cls == "ad":
        t = f"AD {rnd.randint(1, 1900)}"
    elif cls == "bce":
        t = f"{rnd.randint(100, 9000)} BCE"
    elif cls == "ce":
        t = f"{rnd.randint(1, 1900)} CE"
    elif cls == "circa":
        t = f"c. {rnd.randint(100, 1900)}"
    elif cls == "about":
        t = f"ABOUT {n:,} {u}"
    elif cls == "estimated":
        t = f"ESTIMATED {n:,} {u}"
    elif cls == "more_than":
        t = f"MORE THAN {n:,} {u}"
    elif cls == "between":
        t = f"BETWEEN {n:,} AND {n + rnd.randint(1, 900):,} {u}"
    elif cls == "unit_pair":
        t = f"{n:,} m ({round(n * 3.28084):,} ft)"
    elif cls == "conversion":
        t = f"{n:,} ft ({round(n * 0.3048):,} m)"
    elif cls == "negative_depth":
        t = f"-{n:,} m"
    elif cls == "fraction":
        t = rnd.choice(("1/2 mile", "3/4 of the walls", "½ km", "2⅔ m", "one third of the city"))
    else:
        raise ValueError(cls)
    return {"text": t, "signed": f"The record says {t}."}


def figure_cases(count: int, seed: int, classes=FIGURE_CLASSES) -> list[dict]:
    rnd = random.Random(seed + 101)
    out = []
    for i in range(count):
        cls = classes[i % len(classes)] if i < len(classes) else rnd.choice(classes)
        out.append({"cls": cls, **figure_text(cls, rnd)})
    return out


# ------------------------------------------------------------------------------------------------ scale and maps
SCALE_CLASSES = ("h_1m", "h_3m", "h_10m", "h_50m", "h_150m", "h_400m", "h_1000m", "smaller_than_person",
                 "taller_than_every", "imperial_small", "imperial_huge", "zero")


def scale_fact(cls: str, rnd: random.Random) -> dict:
    sub = rnd.choice(("Stone idol", "Salt cathedral", "Bronze colossus", "Ring wall", "Iron pillar",
                      "Incomprehensibilities monument of the long name"))
    value, unit = {"h_1m": (1.0, "m"), "h_3m": (3.0, "m"), "h_10m": (10.0, "m"), "h_50m": (50.0, "m"),
                   "h_150m": (150.0, "m"), "h_400m": (400.0, "m"), "h_1000m": (1000.0, "m"),
                   "smaller_than_person": (round(rnd.uniform(0.05, 1.2), 2), "m"),
                   "taller_than_every": (round(rnd.uniform(2500, 9000)), "m"),
                   "imperial_small": (round(rnd.uniform(1, 6), 1), "ft"),
                   "imperial_huge": (round(rnd.uniform(3000, 30000)), "ft"), "zero": (0.0, "m")}[cls]
    return {"value": value, "unit": unit, "dimension": "length", "subject": sub,
            "approximate": rnd.random() < 0.5}


def scale_cases(count: int, seed: int) -> list[dict]:
    rnd = random.Random(seed + 202)
    return [{"cls": c, "fact": scale_fact(c, rnd)} for c in
            (SCALE_CLASSES[i % len(SCALE_CLASSES)] if i < len(SCALE_CLASSES) else rnd.choice(SCALE_CLASSES)
             for i in range(count))]


# Real coordinates (public, approximate) so the offline map draws its own country; names are the stress.
PLACES = (
    (59.716, 30.396, "Russia"), (29.976, 31.131, "Egypt"), (-13.163, -72.545, "Peru"), (41.008, 28.980, "Turkey"),
    (21.125, -11.401, "Mauritania"), (50.0, 20.05, "Poland"), (1.290, 103.852, "Singapore"),
    (43.733, 7.420, "Monaco"), (41.902, 12.453, "Vatican City"), (-20.27, 30.93, "Zimbabwe"),
    (64.15, -21.94, "Iceland"), (-17.71, 178.06, "Fiji"), (65.0, -179.5, "Russia"), (35.0, 33.0, "Cyprus"),
    (31.78, 35.23, "Israel"), (27.17, 78.04, "India"), (40.43, 116.57, "China"), (-27.12, -109.35, "Chile"),
)
MAP_CLASSES = ("plain", "long_name", "no_region", "same_as_country", "non_latin", "accented", "tiny_state",
               "antimeridian", "island")


def place(cls: str, rnd: random.Random) -> dict:
    lat, lon, country = rnd.choice(PLACES)
    label = rnd.choice(("Stone Ring", "Old Harbour", "Salt Mine", "Lost Gate"))
    if cls == "long_name":
        label = "The Great Incomprehensibilities Memorial Necropolis of the Northern Kingdom"
    elif cls == "no_region":
        country = ""
    elif cls == "same_as_country":
        label = country or "Monaco"
    elif cls == "non_latin":
        label = rnd.choice(GREEK + CYRILLIC + ARABIC + CJK)
    elif cls == "accented":
        label = rnd.choice(ACCENTED)
    elif cls == "tiny_state":
        lat, lon, country = rnd.choice(((43.733, 7.420, "Monaco"), (41.902, 12.453, "Vatican City"),
                                        (1.290, 103.852, "Singapore")))
        label = country
    elif cls == "antimeridian":
        lat, lon, country = rnd.choice(((65.0, -179.5, "Russia"), (-17.71, 178.06, "Fiji")))
    elif cls == "island":
        lat, lon, country = rnd.choice(((-27.12, -109.35, "Chile"), (64.15, -21.94, "Iceland")))
    return {"lat": lat, "lon": lon, "country": country, "label": label, "approximate": rnd.random() < 0.3}


def map_cases(count: int, seed: int) -> list[dict]:
    rnd = random.Random(seed + 303)
    out = []
    for i in range(count):
        cls = MAP_CLASSES[i % len(MAP_CLASSES)] if i < len(MAP_CLASSES) else rnd.choice(MAP_CLASSES)
        out.append({"cls": cls, "place": place(cls, rnd)})
    return out
