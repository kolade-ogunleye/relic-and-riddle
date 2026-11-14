"""What a checked fact's prose may and may not give an illustration (QF-61, operator decision 2026-11-12: structured
counts only).

No number is read out of prose for the image generator. The numbers an illustration prompt carries come only from a
fact's structured, signed quantities record (src/fact_quantities.py); everything here only refuses or removes:

- extract() reads every number of the statements with a bounded grammar and reports the quantities it cannot read
  as exact and clear (approximate, ranged, partitive, vague, elliptical, hypothetical, negated, conflicting): a
  non-empty `unresolved_quantities` makes the caller refuse the illustration. It also gives the closed inventory (the
  words of the facts a depicted item may be named with). It never supplies a count or a measure.
- number_free_sentences() gives the sentences of fact text that state no number, each exactly as written: a sentence
  that states any number (number_reasons) is left out of the prompt whole. Prose is never edited (round 7).
- values_in() is the set of numbers a text states (to check a structured entry against its statement and evidence).

This is a bounded grammar over the fact's tokens, not an English parser; what it cannot read it holds.
"""

import re
import unicodedata
from typing import Any

_SMALL = dict(zip(["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
                   "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"],
                  range(20)))
_TENS = dict(zip(["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"], range(20, 100, 10)))
_SCALE = {"hundred": 100, "thousand": 1000, "million": 1_000_000, "billion": 1_000_000_000}
_NUMBER_WORDS = set(_SMALL) | set(_TENS) | set(_SCALE)
_ORDINAL_WORDS = {"first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth",
                  "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth", "seventeenth",
                  "eighteenth", "nineteenth", "twentieth", "thirtieth", "fortieth", "fiftieth", "sixtieth",
                  "seventieth", "eightieth", "ninetieth", "hundredth", "thousandth"}
_FRACTION_WORDS = {"half", "halves", "third", "thirds", "quarter", "quarters", "fourth", "fourths", "fifth", "fifths",
                   "sixth", "sixths", "eighth", "eighths", "tenth", "tenths"}
# vague number words: never a count ("dozens of statues", "several hundred geoglyphs")
_VAGUE = {"dozen", "dozens", "hundreds", "thousands", "millions", "billions", "score", "scores", "tens"}

# measures of size, distance, area, volume, weight and angle: kept as the fact's own words
_PHYSICAL_UNITS = {
    "m", "metre", "metres", "meter", "meters", "cm", "centimetre", "centimetres", "centimeter", "centimeters",
    "mm", "millimetre", "millimetres", "millimeter", "millimeters", "nm", "nanometre", "nanometres", "nanometer",
    "nanometers", "micrometre", "micrometres", "micrometer", "micrometers", "micron", "microns",
    "km", "kilometre", "kilometres", "kilometer", "kilometers", "mi", "mile", "miles", "ft", "foot", "feet",
    "inch", "inches", "yard", "yards", "cubit", "cubits",
    "kg", "kilogram", "kilograms", "gram", "grams", "tonne", "tonnes", "ton", "tons", "lb", "lbs", "pound", "pounds",
    "litre", "litres", "liter", "liters", "gallon", "gallons", "hectare", "hectares", "acre", "acres",
    "degree", "degrees",
    "square metre", "square metres", "square meter", "square meters", "square kilometre", "square kilometres",
    "square kilometer", "square kilometers", "square foot", "square feet", "square mile", "square miles",
    "square inch", "square inches", "square yard", "square yards",
    "cubic metre", "cubic metres", "cubic meter", "cubic meters", "cubic foot", "cubic feet", "cubic yard",
    "cubic yards", "cubic centimetre", "cubic centimetres", "cubic centimeter", "cubic centimeters",
}
# quantities a drawing cannot show (pressure, strength, energy, concentration, temperature, speed)
_NONVISUAL_UNITS = {
    "pascal", "pascals", "kilopascal", "kilopascals", "megapascal", "megapascals", "gigapascal", "gigapascals",
    "mpa", "gpa", "kpa", "psi", "bar", "bars", "atmosphere", "atmospheres", "ppm", "ppb", "parts per million",
    "parts per billion", "joule", "joules", "kilojoule", "kilojoules", "megajoule", "megajoules", "calorie",
    "calories", "kiloton", "kilotons", "megaton", "megatons", "watt", "watts", "kilowatt", "kilowatts", "megawatt",
    "megawatts", "volt", "volts", "hertz", "decibel", "decibels", "kelvin", "celsius", "fahrenheit",
    "km/h", "mph", "knot", "knots",
}
_RATIO = {"percent", "per cent", "percentage", "percentages", "times", "fold"}
_TIME = {"second", "seconds", "minute", "minutes", "hour", "hours", "day", "days", "night", "nights", "week",
         "weeks", "month", "months", "year", "years", "decade", "decades", "century", "centuries", "millennium",
         "millennia", "generation", "generations", "season", "seasons", "lifetime", "lifetimes"}
# nouns that name no physical thing: a count of them constrains nothing a drawing shows
_ABSTRACT = {
    "approach", "origin", "theory", "hypothesis", "idea", "concept", "aspect", "category", "type", "kind", "sort",
    "grade", "phase", "stage", "period", "era", "epoch", "chapter", "criterion", "achievement", "guideline",
    "investigation", "study", "article", "prank", "method", "technique", "reason", "factor", "way", "option",
    "possibility", "explanation", "interpretation", "estimate", "attempt", "effort", "expedition", "campaign",
    "event", "episode", "occasion", "principle", "tradition", "language", "dialect", "name", "word", "trial",
}
_MONTHS = {"January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December", "Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep", "Sept", "Oct", "Nov",
           "Dec"}
_ERAS = {"BC", "BCE", "AD", "CE"}
_LABEL_WORDS = {"magnitude", "number", "no", "chapter", "criteria", "criterion", "page", "plate", "figure", "fig",
                "item", "catalogue", "catalog", "inventory", "volume", "vol", "verse", "line", "grade", "level",
                "cave", "room", "tomb", "pillar", "gallery", "trench", "sector", "area", "zone", "site", "unit",
                "pit", "layer", "stratum"}
# year prepositions: a bare 3-4 digit number directly after one of them, with no noun, is a year
_YEAR_PREPS = {"in", "since", "until", "till", "by", "circa", "c", "ca", "dated", "year", "from", "during",
               "after", "before", "early", "late", "mid"}
_QUALIFIER = re.compile(
    r"(?:\b(?:about|around|approximately|approx|roughly|nearly|almost|some|(?:an\s+)?estimated|circa|ca|c|over|under|"
    r"below|above|beyond|exceeding|more\s+than|less\s+than|fewer\s+than|up\s+to|at\s+least|at\s+most|"
    r"as\s+(?:many|much|far|few|little)\s+as|upwards\s+of|close\s+to|just|slightly|well|typically|perhaps|"
    r"possibly|maybe|reportedly|allegedly|probably|likely|only\s+about|between|from)\.?\s+)+$",
    re.IGNORECASE)
# "or more" right after the number or after its noun ("12 or so lamps", "12 lamps or more")
_TRAILING_APPROX = re.compile(r"(?:^|\| )\s*(?:or\s+(?:so|more|fewer|less|thereabouts)|odd\b|plus\b|\+|-odd)",
                              re.IGNORECASE)
_CONNECTORS = {"to", "and", "or", "–", "—", "-", "by", "x", "×", ","}
_NEGATION = {"no", "not", "never", "without", "neither", "nor", "none", "cannot", "lacks", "lacked", "lacking",
             "nothing"}
_HYPOTHETICAL = {"could", "would", "might", "should", "if", "unless", "whether"}
_STOP = {
    "of", "in", "on", "at", "by", "for", "with", "from", "into", "onto", "across", "to", "and", "or", "but", "nor",
    "that", "which", "who", "whom", "whose", "where", "when", "while", "than", "as", "per", "each", "every",
    "along", "alongside", "before", "after", "during", "between", "among", "amid", "near", "toward", "towards",
    "over", "under", "above", "below", "beyond", "through", "throughout", "until", "till", "since", "about",
    "around", "against", "within", "without", "upon", "via", "including", "comprising", "apart", "away", "ago",
    "the", "a", "an", "its", "their", "his", "her", "our", "your", "my", "this", "these", "those", "is", "are",
    "was", "were", "be", "been", "being", "has", "have", "had", "do", "does", "did", "can", "could", "would",
    "will", "shall", "should", "may", "might", "must", "not", "no", "it", "they", "them", "he", "she", "we",
    "also", "still", "now", "then", "there", "here", "all", "both", "either", "neither", "so", "such", "just",
    "only", "very", "out", "up", "down", "off", "if", "unless", "whether", "because", "although", "though",
    "yet", "ever", "never", "once", "again", "another", "other", "others", "more", "less", "fewer", "most",
    "least", "own", "same", "half",
}
_PRESENTATION = {"background", "hatching", "linework", "line work"}

_IRREGULAR = {
    "teeth": "tooth", "feet": "foot", "men": "man", "women": "woman", "children": "child", "people": "person",
    "oxen": "ox", "mice": "mouse", "geese": "goose", "lice": "louse", "dice": "die", "criteria": "criterion",
    "phenomena": "phenomenon", "crania": "cranium", "data": "datum", "fungi": "fungus", "nuclei": "nucleus",
    "radii": "radius", "vertebrae": "vertebra", "larvae": "larva", "antennae": "antenna", "formulae": "formula",
    "indices": "index", "matrices": "matrix", "vertices": "vertex", "appendices": "appendix", "axes": "axis",
    "series": "series", "species": "species", "means": "means", "sheep": "sheep", "deer": "deer", "fish": "fish",
    "aircraft": "aircraft", "knives": "knife", "lives": "life", "wives": "wife", "leaves": "leaf",
    "shelves": "shelf", "halves": "half", "wolves": "wolf", "calves": "calf", "loaves": "loaf",
    "niches": "niche", "caches": "cache", "avalanches": "avalanche", "moustaches": "moustache",
    "cliches": "cliche", "creches": "creche", "brioches": "brioche",
    "gas": "gas", "canvas": "canvas", "atlas": "atlas", "lens": "lens", "corps": "corps", "forceps": "forceps",
    "biceps": "biceps", "news": "news",
}
_PLURAL_ONLY_IRREGULAR = {w for w, s in _IRREGULAR.items() if w != s}

_TOKEN = re.compile(
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?P<suf>-?[A-Za-z]+)?"
    r"|(?P<word>[A-Za-z]+(?:['’][A-Za-z]+)*(?:-[A-Za-z]+)*)"
    r"|(?P<punct>[.,;:!?()\[\]–—/%$×+-])")
# same-length rewrites so that every span still points into the fact's own text
_ABBREVIATIONS = ((re.compile(r"\bB\.\s?C\.\s?E\."), "BCE"), (re.compile(r"\bB\.\s?C\."), "BC"),
                  (re.compile(r"\bA\.\s?D\."), "AD"), (re.compile(r"\bC\.\s?E\."), "CE"),
                  (re.compile(r"\b(c|ca|approx|no|fig|vol)\.(?=\s*\d)", re.IGNORECASE), None))


def canonical(word: str) -> str:
    """The singular of a noun as the facts and the vision judge write it ("teeth" -> "tooth", "masts" -> "mast")."""
    word = " ".join(str(word).lower().replace("’", "'").split())
    if " " in word:
        head, _, last = word.rpartition(" ")
        return f"{head} {canonical(last)}"
    if word in _IRREGULAR:
        return _IRREGULAR[word]
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("ches", "shes", "xes", "sses", "zes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is", "ous")) and len(word) > 2:
        return word[:-1]
    return word


def _plural_like(word: str) -> bool:
    lower = word.lower()
    if lower in _PLURAL_ONLY_IRREGULAR:
        return True
    return canonical(lower) != lower and not word[:1].isupper()


def number(text: str) -> int | None:
    """An exact whole number written in figures ("1,024") or words ("one hundred and two"); None otherwise."""
    text = text.strip()
    if re.fullmatch(r"\d+|\d{1,3}(?:,\d{3})+", text):
        return int(text.replace(",", ""))
    if any(char.isdigit() for char in text):
        return None
    tokens = [t for t in re.split(r"[- ]+", text.lower()) if t]

    def under_thousand(parts: list[str]) -> int | None:
        subtotal = 0
        if len(parts) >= 2 and parts[1] == "hundred" and 1 <= _SMALL.get(parts[0], 0) <= 9:
            subtotal = _SMALL[parts[0]] * 100
            parts = parts[2:]
            if parts[:1] == ["and"]:
                parts = parts[1:]
                if not parts:
                    return None
        if not parts:
            return subtotal
        if len(parts) == 1 and parts[0] in (_SMALL | _TENS):
            return subtotal + {**_SMALL, **_TENS}[parts[0]]
        if len(parts) == 2 and parts[0] in _TENS and 1 <= _SMALL.get(parts[1], 0) <= 9:
            return subtotal + _TENS[parts[0]] + _SMALL[parts[1]]
        return None

    if not tokens or tokens[0] == "and" or any(t not in _NUMBER_WORDS and t != "and" for t in tokens):
        return None
    if "thousand" not in tokens:
        return under_thousand(tokens) if not ({"million", "billion"} & set(tokens)) else None
    if tokens.count("thousand") != 1:
        return None
    split = tokens.index("thousand")
    high = under_thousand(tokens[:split])
    rest = tokens[split + 1:]
    if rest[:1] == ["and"]:
        rest = rest[1:]
        if not rest:
            return None
    low = under_thousand(rest)
    return high * 1000 + low if high and low is not None else None


def _tokens(text: str) -> list[dict[str, Any]]:
    masked = text
    for pattern, repl in _ABBREVIATIONS:
        if repl is None:
            masked = pattern.sub(lambda m: m.group(1) + " ", masked)
        else:
            masked = pattern.sub(lambda m, r=repl: r + " " * (len(m.group(0)) - len(r)), masked)
    out = []
    for m in _TOKEN.finditer(masked):
        kind = m.lastgroup if m.lastgroup != "suf" else "num"
        if m.group("num") is not None:
            kind = "num"
        out.append({"kind": kind, "text": m.group(0), "start": m.start(), "end": m.end(),
                    "num": m.group("num"), "suf": m.group("suf") or ""})
    return out


def _sentences(tokens: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    out, cur = [], []
    for t in tokens:
        cur.append(t)
        if t["kind"] == "punct" and t["text"] in ".;!?":
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def _low(t: dict[str, Any] | None) -> str:
    return (t or {}).get("text", "").lower()


def _unit_at(toks: list[dict[str, Any]], i: int) -> tuple[str, int] | None:
    """The unit starting at token i (three-, two- or one-word), as (unit, index of its last token)."""
    for n in (3, 2, 1):
        words = toks[i:i + n]
        if len(words) == n and all(w["kind"] == "word" for w in words):
            unit = " ".join(_low(w) for w in words)
            if unit in _PHYSICAL_UNITS or unit in _NONVISUAL_UNITS or unit in _TIME or unit in _RATIO:
                return unit, i + n - 1
    if i < len(toks) and toks[i]["text"] == "%":
        return "percent", i
    return None


def _unit_class(unit: str) -> str:
    if unit in _PHYSICAL_UNITS:
        return "measure"
    if unit in _NONVISUAL_UNITS:
        return "nonvisual_measure"
    if unit in _TIME:
        return "duration"
    return "ratio"


class _Reader:
    """Reads one sentence's numbers in order; each number gets exactly one class."""

    def __init__(self, text: str, toks: list[dict[str, Any]]):
        self.text, self.toks = text, toks
        # a negation or a hypothetical word governs its own comma clause and everything after it in the sentence
        self.scope = [False] * len(toks)
        clause = 0
        for k, t in enumerate(toks):
            if t["text"] == ",":
                clause = k + 1
            low = _low(t)
            trigger = (low in _NEGATION or low.endswith("n't") or low in _HYPOTHETICAL or t["text"] == "may"
                       or (k == 0 and t["text"] in ("Had", "Were", "Should")))
            if trigger:
                for j in range(clause, len(toks)):
                    self.scope[j] = True
        self.negated = any(_low(t) in _NEGATION or _low(t).endswith("n't") for t in toks)

    # -- one number: its value, where it ends, and the words before it
    def read_number(self, i: int) -> dict[str, Any] | None:
        t = self.toks[i]
        if t["kind"] == "num":
            raw, end, value = t["num"], i, None
            if re.fullmatch(r"\d+|\d{1,3}(?:,\d{3})+", raw):
                value = int(raw.replace(",", ""))
            nxt = self.toks[i + 1] if i + 1 < len(self.toks) else None
            if not t["suf"] and nxt and _low(nxt) in ("hundred", "thousand", "million", "billion"):
                scale = _SCALE[_low(nxt)]
                value = int(float(raw.replace(",", "")) * scale) if value is not None or "." in raw else None
                end = i + 1
            return {"start": i, "end": end, "value": value, "decimal": "." in raw, "suffix": t["suf"],
                    "digits": raw.replace(",", ""), "comma": "," in raw, "word": False}
        if t["kind"] != "word":
            return None
        parts = _low(t).split("-")
        if parts[0] not in _NUMBER_WORDS:
            return None
        if len(parts) > 1:
            if all(p in _NUMBER_WORDS for p in parts):
                return {"start": i, "end": i, "value": number(_low(t)), "decimal": False, "suffix": "",
                        "digits": "", "comma": False, "word": True}
            return {"start": i, "end": i, "value": None, "decimal": False, "suffix": "-" + "-".join(parts[1:]),
                    "digits": "", "comma": False, "word": True}
        j = i
        while j + 1 < len(self.toks):
            nxt = self.toks[j + 1]
            if nxt["kind"] == "word" and all(p in _NUMBER_WORDS for p in _low(nxt).split("-")):
                j += 1
            elif (_low(nxt) == "and" and j + 2 < len(self.toks) and self.toks[j + 2]["kind"] == "word"
                  and _low(self.toks[j + 2]).split("-")[0] in _NUMBER_WORDS):
                j += 2
            else:
                break
        phrase = " ".join(_low(x) for x in self.toks[i:j + 1])
        value = number(phrase)
        if value is None and " and " in f" {phrase} ":
            # "five and ten": two numbers joined by "and", not one number
            left = phrase.split(" and ")[0]
            if number(left) is not None:
                k = i
                while _low(self.toks[k + 1]) != "and":
                    k += 1
                return {"start": i, "end": k, "value": number(left), "decimal": False, "suffix": "", "digits": "",
                        "comma": False, "word": True}
        return {"start": i, "end": j, "value": value, "decimal": False, "suffix": "", "digits": "", "comma": False,
                "word": True}

    def before(self, i: int, n: int = 1) -> str:
        return _low(self.toks[i - n]) if i - n >= 0 else ""

    def prefix_text(self, i: int) -> str:
        """The sentence's text before token i (for the qualifier)."""
        first = self.toks[0]["start"]
        return self.text[first:self.toks[i]["start"]]

    def qualifier_start(self, i: int) -> int | None:
        """Token index where a qualifier directly before token i starts ("an estimated", "about", "more than")."""
        m = _QUALIFIER.search(self.prefix_text(i))
        if not m:
            return None
        pos = self.toks[0]["start"] + m.start()
        for k, t in enumerate(self.toks):
            if t["start"] >= pos:
                return k
        return None

    def noun_phrase(self, j: int) -> list[int]:
        """Indices of the words after the number up to the first function word or punctuation (at most 6)."""
        out = []
        while j < len(self.toks) and len(out) < 6:
            t = self.toks[j]
            if t["kind"] != "word" or _low(t) in _STOP:
                break
            out.append(j)
            j += 1
        return out


def _classify(r: _Reader, n: dict[str, Any]) -> dict[str, Any]:
    """One number's class from its own neighbours (ranges are joined afterwards)."""
    toks = r.toks
    i, end = n["start"], n["end"]
    first = toks[i]
    prev = r.before(i)
    prev_raw = toks[i - 1]["text"] if i else ""
    nxt_i = end + 1
    nxt = toks[nxt_i] if nxt_i < len(toks) else None
    suffix = n["suffix"].lstrip("-").lower()

    last = suffix.split("-")[-1]
    if n["word"] and last in _FRACTION_WORDS and _low(first).split("-")[0] in _SMALL:
        return {"class": "fraction"}      # "one-third", "two-thirds" ("twenty-third" is an ordinal)
    if suffix in ("st", "nd", "rd", "th") or last in _ORDINAL_WORDS:
        return {"class": "ordinal"}
    if last in _FRACTION_WORDS:
        return {"class": "fraction"}
    if suffix == "s" and n["digits"] and len(n["digits"]) in (2, 4):
        return {"class": "year"}
    if suffix in ("d",):
        return {"class": "label"}
    if suffix:
        unit = suffix if suffix in _PHYSICAL_UNITS | _NONVISUAL_UNITS | _TIME else ""
        if unit:
            return {"class": _unit_class(unit), "unit_end": i}
        return {"class": "held", "why": "a number joined to a word"}
    if nxt and nxt["text"] == ":" and nxt_i + 1 < len(toks) and toks[nxt_i + 1]["kind"] == "num":
        return {"class": "clock", "skip_next": True}
    if prev_raw in _MONTHS or (nxt and nxt["text"] in _MONTHS):
        return {"class": "date"}
    if (nxt and nxt["text"] in _ERAS) or prev_raw in _ERAS:
        return {"class": "year"}
    unit = _unit_at(toks, nxt_i) if nxt else None
    if unit:
        return {"class": _unit_class(unit[0]), "unit_end": unit[1]}
    np = r.noun_phrase(nxt_i)
    # "Cave 16,", "Pillar 43 in", "Chapter 56 of", "magnitude 5 on": a name's number, with no noun after it
    if not np and (prev in _LABEL_WORDS or (prev_raw[:1].isupper() and i >= 2 and toks[i - 1]["kind"] == "word"
                                            and prev_raw not in _MONTHS and prev_raw not in _ERAS)):
        return {"class": "label"}
    np_words = [toks[k]["text"] for k in np]
    plural_at = next((k for k in np if _plural_like(toks[k]["text"])), None)
    integer = n["value"] is not None and not n["decimal"]
    year_like = integer and not n["comma"] and not n["word"] and 1000 <= n["value"] <= 2099
    if year_like and plural_at is None:
        return {"class": "year"}
    if year_like:      # "in 1500 pieces", "by 1500 workers", "in 2016 revealed numbers": a year or a count
        return {"class": "held", "why": "a year or a count"}
    if integer and not n["comma"] and not n["word"] and 100 <= n["value"] <= 2099 and not np and prev in _YEAR_PREPS:
        return {"class": "year"}
    if nxt and _low(nxt) == "of" and not np:
        after = _low(toks[nxt_i + 1]) if nxt_i + 1 < len(toks) else ""
        if n["value"] == 1 and after not in ("which", "whom", "them", "these", "those"):
            return {"class": "membership"}
        return {"class": "held", "why": "a part of something counted elsewhere (partitive)"}
    if nxt and _low(nxt) == "out" and nxt_i + 1 < len(toks) and _low(toks[nxt_i + 1]) == "of":
        return {"class": "held", "why": "a part of something counted elsewhere (partitive)"}
    if not np:
        return {"class": "held", "why": "no counted noun follows the number", "bare": True}
    if n["value"] == 1:
        head = np[-1]
        if len(np) > 1 and re.search(r"(?:s|ed|ing)$", toks[head]["text"].lower()):
            return {"class": "held", "why": "the counted noun is unclear"}
    else:
        if plural_at is None:
            return {"class": "held", "why": "no plural noun follows the number"}
        head = plural_at
    element = canonical(toks[head]["text"])
    if element in _TIME or toks[head]["text"].lower() in _TIME:
        return {"class": "duration"}
    if element in _ABSTRACT or element in _RATIO:
        return {"class": "abstract"}
    return {"class": "count", "head": head, "element": element, "np": np_words}


def _rate_follows(toks: list[dict[str, Any]], j: int) -> bool:
    """A rate ("per day", "a year", "each day", "of rock per person") within the next four words."""
    window = []
    for t in toks[j:j + 4]:
        if t["kind"] == "punct":
            break
        window.append(_low(t))
    return any(w == "per" or (w in ("each", "every", "a", "an") and k + 1 < len(window) and window[k + 1] in _TIME)
               for k, w in enumerate(window))


def _span(r: _Reader, a: int, b: int) -> str:
    return " ".join(r.text[r.toks[a]["start"]:r.toks[b]["end"]].split())


def _read_sentence(text: str, toks: list[dict[str, Any]], result: dict[str, list]) -> None:
    r = _Reader(text, toks)
    nums: list[dict[str, Any]] = []
    i = 0
    while i < len(toks):
        if _low(toks[i]) in ("hundred", "thousand") and r.before(i) in ("several", "many", "a", "few"):
            i += 1          # "several hundred": a vague quantity, read below
            continue
        n = r.read_number(i)
        if n is None:
            i += 1
            continue
        n.update(_classify(r, n))
        if n.get("skip_next"):
            n["end"] += 2          # "7:14": both numbers are the clock time
        nums.append(n)
        i = max(n["end"], n.get("unit_end", n["end"])) + 1
        if n["class"] == "measure":
            # "38 meters (125 feet)": the conversion belongs to the measure and keeps its qualifier
            stop = n.get("unit_end", n["end"])
            if stop + 2 < len(toks) and toks[stop + 1]["text"] == "(":
                inner = r.read_number(stop + 2)
                if inner:
                    inner.update(_classify(r, inner))
                    close = inner.get("unit_end", inner["end"]) + 1
                    if inner["class"] == "measure" and close < len(toks) and toks[close]["text"] == ")":
                        n["unit_end"] = close
                        i = close + 1

    # ranges, alternatives and lists: numbers joined by a connector, with nothing (or a qualifier) between
    groups: list[list[dict[str, Any]]] = []
    for n in nums:
        if groups:
            a = groups[-1][-1]
            between = toks[a.get("unit_end", a["end"]) + 1:n["start"]]
            words = [x for x in between if not (x["kind"] == "word" and _QUALIFIER.search(x["text"] + " "))]
            joined = bool(words) and len(words) <= 2 and all(x["text"].lower() in _CONNECTORS for x in words)
            compatible = (a.get("bare") or a["class"] == n["class"] or a["class"] in ("year", "date", "label")
                          or n["class"] in ("year", "date"))
            if joined and compatible:
                groups[-1].append(n)
                continue
        groups.append([n])

    for group in groups:
        classes = [n["class"] for n in group]
        if len(group) > 1:
            if "date" in classes:
                shared = "date"
            elif "year" in classes:
                shared = "year"
            elif group[0]["class"] == "label":
                shared = "label"
            else:
                shared = classes[-1]
            for n in group:
                n["class"] = shared
            if shared == "count":
                for n in group:
                    n.update({"class": "held", "why": "a range or alternative of counts"})
        for n in group:
            if n["class"] in ("measure", "count", "nonvisual_measure") and r.scope[n["start"]]:
                n.update({"class": "held", "why": "a negated or hypothetical statement"})
        first, last = group[0], group[-1]
        if last["class"] == "measure":
            q = r.qualifier_start(first["start"])
            start = q if q is not None else first["start"]
            stop = last.get("unit_end", last["end"])
            phrase = _span(r, start, stop)
            rate = _rate_follows(toks, stop + 1)
            if rate or _TRAILING_APPROX.search(r.text[toks[stop]["end"]:]):
                why = "a rate, not a size" if rate else "an open-ended measure"
                result["unresolved_quantities"].append(f"{phrase} ({why})")
                result["classified"].append({"text": phrase, "class": "held", "why": why})
                continue
            if phrase not in result["physical_measures"]:
                result["physical_measures"].append(phrase)
            result["classified"].append({"text": phrase, "class": "measure"})
            continue
        for n in group:
            if n["class"] == "count":
                q = r.qualifier_start(n["start"])
                tail = r.text[toks[n["end"]]["end"]:] + " | " + r.text[toks[n["head"]]["end"]:]
                ranked = r.before(n["start"]) in ("first", "last", "top", "next", "final", "other", "remaining")
                rate = _rate_follows(toks, n["head"] + 1)
                if q is not None or _TRAILING_APPROX.search(tail):
                    n.update({"class": "held", "why": "an approximate or bounded count"})
                elif n["decimal"] or n["value"] is None:
                    n.update({"class": "held", "why": "not an exact whole number"})
                elif ranked:
                    n.update({"class": "held", "why": "a ranked or remaining share, not an inventory"})
                elif rate:
                    n.update({"class": "held", "why": "a rate, not an inventory"})
            if n["class"] == "count":
                source = _span(r, n["start"], n["head"])
                result["counts"].append({"element": n["element"], "count": n["value"], "source": source})
                result["classified"].append({"text": source, "class": "count"})
            elif n["class"] == "held":
                stop = n.get("unit_end", n["end"])
                np = r.noun_phrase(stop + 1)
                held_text = _span(r, n["start"], np[-1] if np else stop)
                why = n.get("why", "unclear")
                result["unresolved_quantities"].append(f"{held_text} ({why})")
                result["classified"].append({"text": held_text, "class": "held", "why": why})
            else:
                result["classified"].append({"text": _span(r, n["start"], n.get("unit_end", n["end"])),
                                             "class": n["class"]})

    # vague quantities: "dozens of statues", "several hundred geoglyphs", "thousands of years"
    for k, t in enumerate(toks):
        low = _low(t)
        several = low in ("hundred", "thousand") and r.before(k) in ("several", "many", "a", "few")
        if low not in _VAGUE and not several:
            continue
        j = k + 1
        if j < len(toks) and _low(toks[j]) == "of":
            j += 1
        while j < len(toks) and _low(toks[j]) in ("the", "its", "their", "these", "those"):
            j += 1
        np = r.noun_phrase(j)
        phrase = _span(r, k - 1 if several else k, np[-1] if np else k)
        heads = [toks[x]["text"] for x in np]
        if any(h.lower() in _TIME or canonical(h) in _TIME for h in heads[:2]):
            result["classified"].append({"text": phrase, "class": "duration"})
        else:
            result["unresolved_quantities"].append(f"{phrase} (a vague quantity)")
            result["classified"].append({"text": phrase, "class": "held", "why": "a vague quantity"})


def extract(statements: str, forms: tuple[str, ...] = ()) -> dict[str, Any]:
    """The refusals and the closed inventory of the checked fact text `statements`; never a count or a measure.

    unresolved_quantities: every quantity that is not exact and clear (non-empty means the illustration must be
    refused; two different counts of one element also hold). allowed_elements: the forms (from `forms`) the fact
    names in a sentence that is not negated. inventory_terms: the singular of every content word of the fact's
    non-negated sentences (what a depicted item may be named with). classified: every number's class (evidence)."""
    text = str(statements or "")
    result: dict[str, list] = {"counts": [], "physical_measures": [], "unresolved_quantities": [], "classified": []}
    allowed_forms = {canonical(f) for f in forms}
    positive, terms = set(), set()
    for toks in _sentences(_tokens(text)):
        reader = _Reader(text, toks)
        _read_sentence(text, toks, result)
        if reader.negated:
            continue
        words = [_low(t) for t in toks if t["kind"] == "word"]
        for k, w in enumerate(words):
            for phrase in (w, f"{w} {words[k + 1]}" if k + 1 < len(words) else ""):
                if phrase and canonical(phrase) in allowed_forms:
                    positive.add(canonical(phrase))
            if w not in _STOP and w not in _NUMBER_WORDS:
                terms.add(canonical(w))
                terms.update(canonical(p) for p in w.split("-") if p and p not in _STOP)

    by_element: dict[str, dict[str, Any]] = {}
    for c in result["counts"]:
        prior = by_element.get(c["element"])
        if prior and prior["count"] != c["count"]:
            result["unresolved_quantities"].append(
                f"{c['element']}: the facts give {prior['count']} and {c['count']} (conflicting counts)")
        by_element.setdefault(c["element"], c)
    allowed = sorted(positive)
    return {"allowed_elements": allowed, "unresolved_quantities": result["unresolved_quantities"],
            "inventory_terms": sorted(terms | set(allowed)), "classified": result["classified"]}


def item_allowed(item: str, terms: set[str]) -> bool:
    """A depicted item the facts name: every content word of it (singular) is a word of the facts, or the item is
    exactly a presentation word (background, hatching, linework). "cannon mount" is not allowed because "cannon"
    is; "wooden stand" needs both "wooden" and "stand" in the facts."""
    clean = " ".join(re.sub(r"[^\w\s'-]", " ", str(item or "")).lower().split())
    if not clean:
        return False
    if clean in _PRESENTATION:
        return True
    words = [w for w in re.split(r"[\s-]+", clean) if w and w not in _STOP]
    if not words or any(w in _NUMBER_WORDS or w.isdigit() for w in words[-1:]):
        return False
    return all(canonical(w) in terms or w.isdigit() or w in _NUMBER_WORDS for w in words)


def item_words(item: str) -> set[str]:
    """The singular of every word of a depicted item (to find a counted element in the inventory)."""
    clean = " ".join(re.sub(r"[^\w\s'-]", " ", str(item or "")).lower().split())
    return {canonical(w) for w in re.split(r"[\s-]+", clean) if w} | ({canonical(clean)} if clean else set())


_DIGITS = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


def _number_word(word: str) -> bool:
    """A cardinal number word, alone or hyphenated ("three", "thirty-five", "two-thirds", "one-hundred")."""
    return word.lower().replace("’", "'").split("-")[0] in _NUMBER_WORDS


def values_in(text: str) -> set[float]:
    """Every number `text` states, in figures ("1,200" -> 1200) or words ("thirty-five", "one hundred and two")."""
    text = str(text or "")
    out = {float(m.group(0).replace(",", "")) for m in _DIGITS.finditer(text)}
    words = re.findall(r"[A-Za-z]+(?:-[A-Za-z]+)*", text)
    k = 0
    while k < len(words):
        if not _number_word(words[k]):
            k += 1
            continue
        j = k
        while j + 1 < len(words) and (_number_word(words[j + 1]) or (
                words[j + 1].lower() == "and" and j + 2 < len(words) and _number_word(words[j + 2]))):
            j += 1
        run = [w.lower() for w in words[k:j + 1]]
        value = number(" ".join(run))
        if value is not None:
            out.add(float(value))
        else:
            out.update(float(v) for w in run if (v := number(w)) is not None)
        k = j + 1
    return out


# ------------------------------------------------------------------ prose for the generator (QF-61 round 7)
# Prose is never edited. A sentence that states any number is left out of the generator prompt whole; every other
# sentence is passed exactly as the fact writes it. Fewer sentences, never an altered meaning.
_ROMAN = re.compile(r"M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})")
_SENTENCE_WORD = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*(?:-[A-Za-z]+)*")
_ERA_DOTTED = re.compile(r"\b(?:A\.\s?D|B\.\s?C(?:\.\s?E)?|C\.\s?E)\.")
# characters that write a number or a quantity: every character with a Unicode numeric value (0-9, other scripts'
# digits, superscripts "²", vulgar fractions "½", roman numeral signs "Ⅻ", circled digits) and these symbols
_NUMBER_SYMBOLS = set("%‰‱°′″#№")
_CARDINALS = set(_SMALL) | set(_TENS) | set(_SCALE) | {"trillion", "nought", "naught"}
_CARDINAL_PLURALS = ({w + "s" for w in _SCALE} | {"trillions", "tens", "dozens", "scores", "teens"}
                     | {w[:-1] + "ies" for w in _TENS})                      # "hundreds", "twenties"
_ORDINALS = _ORDINAL_WORDS | {"millionth", "billionth", "firstly", "secondly", "thirdly"}
_FRACTIONS = {"half", "halves", "halved", "quarter", "quarters", "thirds", "fourths", "fifths", "sixths", "eighths",
              "tenths", "percent", "percentage", "percentages", "cent"}
# words that state how many without a numeral (review 6, blocking 2): collective and multiplicative words, "both",
# "single", and counts by the dozen and the score
_COLLECTIVES = {"pair", "pairs", "paired", "couple", "couples", "twin", "twins", "trio", "trios", "triple", "triples",
                "tripled", "triplet", "triplets", "quartet", "quartets", "quintet", "quintets", "sextet", "sextets",
                "double", "doubles", "doubled", "quadruple", "quadrupled", "duo", "duos", "dual", "triad", "triads",
                "both", "single", "twice", "thrice", "dozen", "score"}
_SPANS = {"decade", "century", "millennium", "fortnight"}                   # "a century later"
_SUPERLATIVE = {"most", "least", "best", "worst", "foremost", "utmost", "uppermost", "innermost", "outermost"}
_NOT_SUPERLATIVE = {"west", "midwest", "southwest", "northwest", "forest", "honest", "dishonest", "modest",
                    "interest", "harvest", "conquest", "request", "protest", "contest", "manifest", "arrest",
                    "chest", "crest", "guest", "nest", "pest", "quest", "rest", "test", "vest", "zest", "priest",
                    "earnest", "inquest", "behest", "tempest", "unrest", "invest", "digest", "suggest", "attest",
                    "detest", "infest", "molest", "everest", "bucharest", "budapest", "palimpsest", "bequest",
                    "armrest", "footrest", "headrest", "breast"}
_ONE_PRONOUN_BEFORE = {"the", "this", "that", "each", "every", "any", "which", "another"}
_ONE_PRONOUN_AFTER = {"that", "which", "who", "whom", "whose", "where", "of", "in", "on", "at", "from", "with", "by",
                      "for", "to"}
_ONE_GENERIC = {"can", "could", "may", "might", "must", "should", "would", "will", "cannot"}
_POSSESSIVES = {"the", "its", "their", "his", "her", "our"}
_ABBREVIATION_ENDS = {"mr", "mrs", "ms", "dr", "st", "mt", "ft", "jr", "sr", "vs", "etc", "cf", "c", "ca", "approx",
                      "no", "nos", "fig", "figs", "vol", "vols", "gen", "col", "capt", "lt", "sgt", "prof", "rev",
                      "hon", "gov", "pres", "inc", "ltd", "co", "corp", "dept", "est", "ed", "eds", "al", "op",
                      "ibid", "pp", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov",
                      "dec", "ie", "eg", "viz", "ch", "pl", "bros"}


def sentences(text: str) -> list[str]:
    """`text` cut into sentences, each exactly as written (its own characters; only the space between sentences is
    left out). A sentence ends at ".", "!" or "?" (with any closing quote or bracket) before white space and then a
    capital letter, a digit, an opening quote or bracket, or at the end. It never ends after an initial ("Derek J."),
    an era written with stops ("A.D.", "B.C."), a dotted abbreviation ("e.g.") or a common one ("St.", "c."): when
    unsure, two sentences stay one."""
    text = str(text or "")
    out, start = [], 0
    for m in re.finditer(r"[.!?]+[\"'”’)\]]*", text):
        end = m.end()
        rest = text[end:]
        nxt = rest.lstrip()
        if nxt and (len(nxt) == len(rest) or not re.match(r"[A-Z0-9\"'“‘(\[]", nxt)):
            continue
        if nxt and m.group(0)[0] == ".":
            word = re.search(r"[A-Za-z.]*$", text[start:m.start()]).group(0)
            last = word.rsplit(".", 1)[-1]
            if len(last) == 1 or "." in word or last.lower() in _ABBREVIATION_ENDS:
                continue
        piece = text[start:end].strip()
        if piece:
            out.append(piece)
        start = end
    piece = text[start:].strip()
    if piece:
        out.append(piece)
    return out


def _superlative(word: str) -> bool:
    low = word.lower()
    return low in _SUPERLATIVE or (low.endswith("est") and len(low) > 4 and low not in _NOT_SUPERLATIVE)


def _one_is_a_number(words: list[str], k: int) -> bool:
    """"one" at words[k] states a count unless it is "no one", "one another", "one of the <superlative>" ("one of the
    largest temples", "one of its oldest", "one of humanity's earliest"), the pronoun after a determiner ("the one
    that", "this one") or the generic pronoun before a modal ("one can see")."""
    low = [w.lower() for w in words]
    prev = low[k - 1] if k else ""
    nxt = low[k + 1] if k + 1 < len(low) else ""
    if prev == "no" or nxt == "another" or nxt in _ONE_GENERIC:
        return False
    owner = low[k + 2] if k + 2 < len(low) else ""
    if nxt == "of" and k + 3 < len(low) and (owner in _POSSESSIVES or owner.endswith(("'s", "’s"))) \
            and _superlative(low[k + 3]):
        return False
    return not (prev in _ONE_PRONOUN_BEFORE and (not nxt or nxt in _ONE_PRONOUN_AFTER))


def number_reasons(sentence: str) -> list[str]:
    """Why `sentence` states a number; empty when it states none. A numeral character (anything with a Unicode numeric
    value: digits, "²", "½", "Ⅻ") or a number symbol ("%", "°"); a number word: cardinal (with plurals: "hundreds",
    "twenties"), ordinal ("first", "nineteenth"), fraction ("half", "percent"), collective or multiplicative ("pair",
    "twin", "trio", "double", "both", "single", "dozen", "score", "fourscore", "twofold"), "a century"/"a decade"; a
    roman numeral ("XII", "IV", "V", "X", and "I" after a capitalised word: "Edward I"); an era marker ("AD",
    "B.C."). "one" is a number except in the idioms of _one_is_a_number. Ordinals are numbers too ("the second floor"
    is dropped)."""
    s = str(sentence or "")
    out = [f"the character {ch!r}" for ch in dict.fromkeys(s)
           if ch in _NUMBER_SYMBOLS or (not ch.isspace() and unicodedata.numeric(ch, None) is not None)]
    if _ERA_DOTTED.search(s):
        out.append("an era marker")
    words = _SENTENCE_WORD.findall(s)
    for k, word in enumerate(words):
        low = word.lower().replace("’", "'")
        parts = [p for p in low.split("-") if p]
        prev = words[k - 1].lower() if k else ""
        if low == "one":
            if _one_is_a_number(words, k):
                out.append("the word 'one'")
        elif any(p in _CARDINALS or p in _CARDINAL_PLURALS or p in _ORDINALS or p in _FRACTIONS or p in _COLLECTIVES
                 or (p.endswith("fold") and p[:-4] in _CARDINALS)
                 or (p.endswith("score") and p[:-5] in _CARDINALS) for p in parts):
            out.append(f"the word {word!r}")
        elif low in _SPANS and prev in ("a", "an", "half"):
            out.append(f"'{prev} {word}'")
        elif word in ("AD", "BC", "BCE", "CE"):
            out.append(f"the era marker {word!r}")
        elif (len(word) > 1 and _ROMAN.fullmatch(word)) or word in ("V", "X") or (
                word == "I" and k >= 1 and words[k - 1][:1].isupper()):
            out.append(f"the roman numeral {word!r}")
    return out


def has_number(text: str) -> bool:
    """Whether `text` states any number (number_reasons)."""
    return bool(number_reasons(text))


def number_free_sentences(text: str) -> list[str]:
    """The sentences of `text` that state no number, each exactly as written. A sentence that states one is left out
    whole (QF-61 round 7: prose is never edited, so no meaning can change)."""
    return [s for s in sentences(text) if not has_number(s)]
