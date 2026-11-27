"""
src/line_policy.py
==================
The ONE line-breaking policy of every drawn text surface (design freeze cycle 5, TEXT stream).

Four cycles fixed the same three defects per surface (a line ending on a function word, a number parted from its
unit, a lone last row) and each came back on the next surface, because every surface kept its own word list and its
own keep-together test. This module is the single definition every surface reads:

- the vocabulary (brand_system.LINE_END_NEVER / LINE_END_LAST_RESORT / LINE_END_WEAK): `end_class`;
- keep-together (`keep_together`): a number with its scale word and unit, a hedge with its figure, the two ends of a
  range, a date, an era, a bracketed conversion: no line of any surface breaks inside one;
- the judge (`faults`): the violations of a set of drawn lines, named the same way on every surface, which the
  corpus proof (docs/reviews/freeze-system-2026-11-15/corpus/text_lines.py) counts over every dev run;
- the paragraph breaker (`wrap`): body copy, card lines and labels, broken for the fewest faults first, then the
  evenest rag; headings keep their engine (src/heading.py), which reads the same vocabulary and bonds;
- the phrase unit (`phrase_unit`): an adverb with its participle, a modifier with its noun: a caption burst is never
  cut inside one (lander ruling 2026-11-26).

Where no legal break exists the fallback order is fixed and RECORDED by the caller: reflow at the same size > a
smaller ladder size above the floor > a designed alternative > refusal. A fitter never drops a word (`ellipsis_drop`
is the judge of that: an ellipsis inside a sentence with words missing after it is a truth fault, never a fit).

Portability: closed word classes and word shapes only; no topic lists.
"""
from __future__ import annotations

import itertools
import re
from collections.abc import Callable, Sequence

from . import brand_system

NBSP = " "
_QUOTES = "\"'‘’“”"
_CLOSE = ".,;:!?—–)…"
_OPEN = "(["

# ---- fault names (one spelling everywhere: records, refusals, the corpus tables)
NEVER_END = "never_end"                       # a line (not the last) ends on a LINE_END_NEVER word
FUNCTION_END = "function_word_end"            # ... on a LINE_END_LAST_RESORT word (conjunction, subordinator, ...)
UNIT_SPLIT = "unit_split"                     # a line break inside a keep-together group
LONE_LAST = "lone_last_row"                   # a one-word last row (three words or more) under the balance minimum
WEAK_END = "weak_end"                         # a line ends on a copula or auxiliary: counted apart, no violation
NAME_SPLIT = "name_split"                     # a line break inside a proper name (MOHAMMAD ADIL / SHAH'S), mixed case
MEASURE_OVER = "measure_over"                 # a body line over its character measure: recorded, no line-break fault
VIOLATIONS = (NEVER_END, FUNCTION_END, UNIT_SPLIT, LONE_LAST, NAME_SPLIT)


def bare(word: str) -> str:
    """The word as the vocabulary reads it: lower case, without quotes or closing punctuation."""
    return str(word or "").replace(NBSP, " ").strip().lower().strip(_QUOTES + _OPEN).rstrip(_CLOSE).strip(_QUOTES)


def closes(word: str) -> bool:
    """Whether punctuation after `word` closes its phrase (then any word may end a line: "of," / "and:")."""
    w = str(word or "").strip().rstrip(_QUOTES)
    return bool(w) and w[-1] in _CLOSE


def end_class(word: str) -> str | None:
    """What a line ending on `word` (not the last line) is: "never" (LINE_END_NEVER: hard), "last_resort"
    (LINE_END_LAST_RESORT: only where no other break fits, recorded), "weak" (LINE_END_WEAK), or None (a content word,
    or punctuation closes the phrase)."""
    if closes(word):
        return None
    w = bare(word)
    if w in brand_system.LINE_END_NEVER:
        return "never"
    if w in brand_system.LINE_END_LAST_RESORT:
        return "last_resort"
    if w in brand_system.LINE_END_WEAK:
        return "weak"
    return None


def is_function(word: str) -> bool:
    """A closed-class word (never or last-resort class), whatever follows it."""
    return bare(word) in brand_system.LINE_END_FUNCTION


# --------------------------------------------------------------------------------------------- keep-together
SPELLED_NUMBERS = frozenset((
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen",
    "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty", "forty", "fifty",
    "sixty", "seventy", "eighty", "ninety", "hundred", "thousand", "million", "billion", "trillion", "dozen"))
SCALE_WORDS = frozenset(("hundred", "thousand", "million", "billion", "trillion", "dozen"))
UNITS = frozenset((
    "mm", "cm", "m", "km", "in", "ft", "yd", "mi", "kg", "g", "lb", "lbs", "oz", "t", "ha", "mph", "kph",
    "millimetre", "millimetres", "millimeter", "millimeters", "centimetre", "centimetres", "centimeter",
    "centimeters", "metre", "metres", "meter", "meters", "kilometre", "kilometres", "kilometer", "kilometers",
    "inch", "inches", "foot", "feet", "yard", "yards", "mile", "miles", "acre", "acres", "hectare", "hectares",
    "gram", "grams", "kilogram", "kilograms", "pound", "pounds", "ounce", "ounces", "ton", "tons", "tonne", "tonnes",
    "megaton", "megatons", "kiloton", "kilotons", "litre", "litres", "liter", "liters", "gallon", "gallons",
    "second", "seconds", "minute", "minutes", "hour", "hours", "day", "days", "week", "weeks", "month", "months",
    "year", "years", "decade", "decades", "century", "centuries", "millennium", "millennia",
    "percent", "%", "degrees", "degree", "people", "storeys", "stories", "floors", "levels", "level", "km²", "m²",
    "ft²", "mi²", "knots", "nanometres", "nanometers", "nm", "celsius", "fahrenheit", "volts", "watts"))
AREA_WORDS = frozenset(("square", "cubic", "sq", "metric", "light", "nautical"))
ERAS = frozenset(("bc", "bce", "ad", "ce", "bp", "b.c", "a.d", "b.c.e", "c.e"))
PREFIXES = frozenset(("c", "ca", "circa", "ad", "c.", "ca.", "~", "est", "est."))
HEDGES: tuple[tuple[str, ...], ...] = tuple(sorted((
    ("more", "than"), ("fewer", "than"), ("less", "than"), ("as", "many", "as"), ("as", "much", "as"), ("up", "to"),
    ("at", "least"), ("at", "most"), ("about",), ("roughly",), ("nearly",), ("almost",), ("estimated",), ("some",),
    ("around",), ("approximately",), ("over",), ("under",), ("circa",), ("between",),
), key=len, reverse=True))
MONTHS = frozenset(("january", "february", "march", "april", "may", "june", "july", "august", "september",
                    "october", "november", "december"))
RANGE_JOINERS = frozenset(("to", "and", "or", "-", "–", "—"))
TAIL_WORDS = frozenset(("ago", "old", "bc", "bce", "ad", "ce", "bp"))   # "104 million years AGO": the span's own end

_NUM = re.compile(r"^[(\[~]?[$€£]?\d[\d,.]*(?:[–—-]\d[\d,.]*)?\+?(?:st|nd|rd|th|s|%|[a-z²³°]{0,3})?[)\]]?$",
                  re.IGNORECASE)


def _tok(word: str) -> str:
    return re.sub(r"[^\w%²°~]", "", str(word or "").replace(NBSP, " ")).lower()


def is_number(word: str) -> bool:
    """A numeral as drawn ("1,090", "25+", "c.780" is not: its prefix is its own word), or a spelled number."""
    w = str(word or "").strip().strip(_QUOTES).rstrip(_CLOSE)
    return bool(w) and (bool(_NUM.match(w)) and any(c.isdigit() for c in w) or _tok(w) in SPELLED_NUMBERS)


def keep_together(words: Sequence[str]) -> set[int]:
    """The break positions k (a break between words k-1 and k) NO line of any surface makes, for one text's words:

    - a number and its scale word, its unit and the unit's own qualifier: 1,090 TONNES, 104 MILLION YEARS, 590
      SQUARE FEET, 36 HOURS, TWENTY THOUSAND PEOPLE;
    - a span's closing word: YEARS AGO, 1250 BC, AD 79;
    - a hedge and its figure, and the words of a multi-word hedge before a figure: ABOUT 40, MORE THAN 590, UP TO 12;
    - a prefix and its figure: C. 780, CA. 1200;
    - a date: 5 AUGUST, AUGUST 1944;
    - a bracketed conversion and the measure it converts: 60 FEET (18 M).

    Never across punctuation that closes a phrase ("in 1941, 36 hours"). The two ends of a range ("94 AND 104",
    "94 TO 104") are held too, except that a range too long for a line may break BEFORE its joiner (the callers'
    recorded last resort, `range_seams`)."""
    ws = [str(w) for w in words]
    t = [_tok(w) for w in ws]
    n = len(ws)
    out: set[int] = set()
    for k in range(1, n):
        a, b, ra, rb = t[k - 1], t[k], ws[k - 1], ws[k]
        if closes(ra) and not (rb.lstrip()[:1] in _OPEN):
            continue
        num_a = is_number(ra)
        if num_a and (b in UNITS or b in SCALE_WORDS or b in AREA_WORDS or b in ERAS) and not (
                b == "in" and not any(c.isdigit() for c in a)):
            out.add(k)
        elif a in SCALE_WORDS and k >= 2 and is_number(ws[k - 2]) and (b in UNITS or b in AREA_WORDS):
            out.add(k)   # 104 MILLION | YEARS
        elif a in AREA_WORDS and b in UNITS:
            out.add(k)   # SQUARE | FEET
        elif b in TAIL_WORDS and (a in UNITS or num_a) and k >= 2 and (num_a or is_number(ws[k - 2])
                                                                     or t[k - 2] in SCALE_WORDS | AREA_WORDS):
            out.add(k)   # YEARS | AGO, 1250 | BC
        elif a in PREFIXES and is_number(rb) and (a not in ("c", "ad", "est") or ra.rstrip().endswith(".")
                                                  or a == "ad"):
            out.add(k)   # C. | 780, AD | 79
        elif (a in MONTHS and re.fullmatch(r"\d{1,2}(st|nd|rd|th)?|\d{4}", b)) or (
                re.fullmatch(r"\d{1,2}(st|nd|rd|th)?", a) and b in MONTHS):
            out.add(k)   # AUGUST | 1944
        elif rb.lstrip()[:1] == "(" and any(c.isdigit() for c in rb) and (num_a or a in UNITS):
            out.add(k)   # 60 FEET | (18 M)
        elif ra.lstrip()[:1] == "(" and ")" not in ra:
            out.add(k)   # (18 | M)
        elif num_a and is_number(rb) and (a in SPELLED_NUMBERS or b in SPELLED_NUMBERS):
            out.add(k)   # TWENTY | THOUSAND
    # a hedge and its figure
    for k in range(1, n):
        if not is_number(ws[k]):
            continue
        for hedge in HEDGES:
            lo = k - len(hedge)
            if lo >= 0 and tuple(t[lo:k]) == hedge and not any(closes(w) for w in ws[lo:k]):
                out.update(range(lo + 1, k + 1))
                break
    # both ends of a range: 94 AND 104, 7,000 TO 10,000 (the joiner between two figures)
    for k in range(1, n - 1):
        if t[k] in RANGE_JOINERS and is_number(ws[k + 1]) and not closes(ws[k - 1]):
            j = k - 1
            while j > 0 and (t[j] in UNITS or t[j] in SCALE_WORDS or t[j] in AREA_WORDS):
                j -= 1
            # AND / OR join a range only after BETWEEN ("between 94 and 104"); "c. 780 and 85 m" are two quantities
            between = t[k] not in ("and", "or") or "between" in t[max(0, j - 3):j]
            if is_number(ws[j]) and between:
                out.update((k, k + 1))
    return out


def range_seams(words: Sequence[str]) -> set[int]:
    """The positions inside a range where a break is the recorded last resort: before its joiner (BETWEEN 94 / AND
    104 MILLION YEARS)."""
    ws = [str(w) for w in words]
    t = [_tok(w) for w in ws]
    return {k for k in range(1, len(ws) - 1) if t[k] in RANGE_JOINERS and is_number(ws[k + 1])
            and k in keep_together(ws)}


_REGNAL = re.compile(r"(?=[IVX]{2,5}$)X{0,2}(IX|IV|V?I{0,3})")


def name_bonds(words: Sequence[str]) -> set[int]:
    """The break positions inside a proper name in mixed-case copy: two capitalised words in a row with nothing
    closing the first ("Mohammad Adil Shah's", "Army Group North", "Soviet Union"). Word shapes only: a capital
    followed by lower case, no closed-class word ("The", "In"), no numeral. Copy set in capitals or in title case
    (most of its words capitalised) names nothing this way: the set is empty there, so headings, kickers and captions
    are untouched. A name is parted only where no other break fits (the breakers weigh it above a function-word end:
    a name is never split to avoid one), and the split is recorded (NAME_SPLIT)."""
    ws = [str(w).replace(NBSP, " ").strip() for w in words]
    n = len(ws)
    if n < 3:
        return set()

    def cap(w: str) -> bool:
        s = w.strip(_QUOTES + _OPEN)
        return (len(s) > 1 and s[0].isupper() and any(c.islower() for c in s[1:]) and not is_function(w)
                and bare(w) not in brand_system.LINE_END_WEAK)

    caps = [cap(w) for w in ws]
    initial = sum(1 for w in ws if w.strip(_QUOTES + _OPEN)[:1].isupper())
    if initial * 5 > n * 3:                       # capitals or title case: no name can be told from its shape
        return set()
    out = {k for k in range(1, n) if caps[k - 1] and caps[k] and not closes(ws[k - 1])}
    # a regnal numeral stays with its name: Amenhotep | III, Louis | XIV
    out |= {k for k in range(1, n) if caps[k - 1] and not closes(ws[k - 1])
            and _REGNAL.fullmatch(ws[k].strip(_QUOTES).rstrip(_CLOSE).removesuffix("'s").removesuffix("’s"))}
    return out


# ------------------------------------------------------------------------------------------------ phrase unit
_NOT_ADVERBS = frozenset(("only", "early", "family", "italy", "july", "supply", "reply", "holy", "assembly", "rally",
                          "fly", "ally", "belly", "bully", "daily", "likely", "lonely", "ugly", "friendly", "elderly",
                          "costly", "deadly", "lively", "lovely", "orderly", "apply", "butterfly",
                          "anomaly", "monopoly", "homily", "lily", "jelly", "folly"))


def phrase_unit(before: Sequence[str], w1: str, w2: str) -> str | None:
    """Whether the words w1 w2 (w1 after the words `before`, in one sentence) read as one phrase unit that a caption
    burst cut must not part (lander ruling 2026-11-26), named, else None:

    - "adverb_participle": an -ly adverb and the verb form it modifies (HEAVILY | FIRE-BOMBED, MOST LIKELY |
      INCINERATED is a hedge group, held elsewhere);
    - "modifier_noun": a modifier and its noun: a capitalised modifier before a lower-case noun mid-sentence (SOVIET |
      ARTILLERY, BRITISH | AIRCRAFT), a hyphenated compound modifier (EIGHTEENTH-CENTURY | EXPANSIONS), or one content
      word after the article or preposition that opens the phrase (UNDER EXPERT | SUPERVISION, THE COMPLETED | ROOM).

    Word shapes and the closed classes only (no tagger at run time, no topic list); punctuation after w1 closes the
    unit. A verb form after the opener's one word is no noun (IN GERMANY | FUELING HUNTS)."""
    if not w1 or not w2 or closes(w1):
        return None
    a, b = bare(w1), bare(w2)
    if not a or not b or is_function(a) or is_function(b) or b in brand_system.LINE_END_WEAK:
        return None
    if a in brand_system.LINE_END_WEAK or is_number(w1) or is_number(w2):
        return None
    lower_b = w2.strip(_QUOTES + _OPEN)[:1].islower()
    if a.endswith("ly") and len(a) > 4 and a not in _NOT_ADVERBS and w1.strip(_QUOTES)[:1].islower() and lower_b:
        return "adverb_participle"
    verbish = b.endswith(("ing", "ed")) and "-" not in b
    if not lower_b or verbish:
        return None
    if "-" in a.strip("-") and not a.endswith("ly") and len(a) > 5:
        return "modifier_noun"                      # a hyphenated compound modifier before its noun
    prev = bare(before[-1]) if before else ""
    opener = bool(before) and not closes(before[-1]) and prev in brand_system.LINE_END_FUNCTION and prev not in (
        "and", "or", "but", "nor", "so", "yet", "that", "which", "who", "when", "where", "while", "as", "than", "if",
        "because", "although", "though", "since", "until", "unless", "whether", "how", "why")
    if opener and not a.endswith(("ing",)) :
        return "modifier_noun"                      # [the / under / by] MODIFIER | noun
    if w1.strip(_QUOTES + _OPEN)[:1].isupper() and before and not closes(before[-1]) and not a.endswith("s"):
        return "modifier_noun"                      # a capitalised modifier mid-sentence: SOVIET | artillery
    return None


# ------------------------------------------------------------------------------------------------- the record
# A breaker's own trace: every violation a wrap could not remove (no legal break at that size and width) is noted
# here. It is a TRACE of fits, trial fits included, not a record of what was drawn (freeze cycle 5, review D2: drained
# into a slide's record it named lines the slide never drew and carried other surfaces' notes into the next slide).
# The RECORD of a drawn surface is `drawn_record` of the lines it drew, written by its renderer and kept beside the
# still (still_layout.save: "line_faults"). The trace keeps the last NOTE_LIMIT notes of its thread, for a probe or a
# test that asks what one call left (take_notes before and after it).
import collections as _collections
import threading as _threading

_notes = _threading.local()
NOTE_LIMIT = 64


def note(lines: Sequence[str], left: Sequence[dict], **where) -> None:
    """Trace the violations that stay in one wrap's `lines` (the fallback order found no legal break)."""
    box = getattr(_notes, "items", None)
    if box is None:
        box = _notes.items = _collections.deque(maxlen=NOTE_LIMIT)
    box.append({"lines": [str(ln).replace(NBSP, " ") for ln in lines],
                "faults": [dict(f) for f in left], "reason": "no legal break at this size and width", **where})


def take_notes() -> list[dict]:
    """The notes since the last call (this thread), cleared."""
    box = list(getattr(_notes, "items", None) or [])
    _notes.items = _collections.deque(maxlen=NOTE_LIMIT)
    return box


# ----------------------------------------------------------------------------------------------------- the judge
def _split(line: str) -> list[str]:
    return str(line or "").replace(NBSP, " ").split()


def faults(lines: Sequence[str], widths: Sequence[float] | None = None, heading: bool = False,
           balance_min: float | None = None) -> list[dict]:
    """The policy's violations in one text's drawn `lines` (top to bottom), each {"fault", "line", "word"}:

    - never_end / function_word_end: a line other than the last ends on a never / last-resort word with no
      punctuation closing it (weak_end: on a copula or auxiliary; reported, never a violation);
    - unit_split: a line break inside a keep-together group;
    - lone_last_row (headings only: `heading`): a one-word last row in a heading of three words or more whose two
      last rows balance under `balance_min` (heading.balance_min 0.55; measured `widths`, else characters). The
      accepted stagger (ruling: CARVED / FROM SOLID SALT, a one-word FIRST row where the only legal break staggers) is
      no lone last row.

    One judge for every surface: a renderer's own record never counts fewer than this sees."""
    rows = [_split(ln) for ln in lines]
    rows = [r for r in rows if r]
    out: list[dict] = []
    if len(rows) < 2:
        return out
    words = [w for r in rows for w in r]
    bonds = keep_together(words)
    seams = range_seams(words)
    names = set() if heading else name_bonds(words) - bonds
    at = 0
    for i, row in enumerate(rows[:-1]):
        at += len(row)
        cls = end_class(row[-1])
        if cls:
            out.append({"fault": {"never": NEVER_END, "last_resort": FUNCTION_END, "weak": WEAK_END}[cls], "line": i,
                        "word": bare(row[-1])})
        if at in bonds:
            out.append({"fault": UNIT_SPLIT, "line": i, "word": f"{row[-1]} | {rows[i + 1][0]}",
                        # before a range's joiner (BETWEEN 94 / AND 104 MILLION YEARS): the one break a range too
                        # long for its line may take, as a recorded last resort
                        "seam": at in seams})
        elif at in names:
            out.append({"fault": NAME_SPLIT, "line": i, "word": f"{row[-1]} | {rows[i + 1][0]}"})
    if heading and len(words) >= 3 and len(rows[-1]) == 1:
        floor = brand_system.SPEC["heading.balance_min"] if balance_min is None else balance_min
        ws = list(widths[-2:]) if widths is not None and len(widths) >= 2 else [len(" ".join(rows[-2])),
                                                                                 len(rows[-1][0])]
        bal = min(ws) / max(ws) if max(ws) > 0 else 1.0
        if bal < floor:
            out.append({"fault": LONE_LAST, "line": len(rows) - 1, "word": bare(rows[-1][0]),
                        "balance": round(bal, 2)})
    return out


def lone_last_rebreak(lines: Sequence[str], measure: Callable[[str], float], width: float,
                      balance_min: float | None = None) -> list[str] | None:
    """Lander ruling R3 (2026-11-26, TEXT-03): a heading's last line may not be a single short word (lone_last_row)
    where a legal re-break exists that violates no higher line rule and adds no line. The best such re-break of
    `lines` (no more lines, every line within `width` by `measure`, no never / function-word end, no keep-together
    group or name parted, no lone last row), the same line count first, then the evenest; None where `lines` has no
    lone last row or no such re-break exists (the lone row then stays, recorded with the reason)."""
    rows = [r for r in (_split(ln) for ln in lines) if r]
    widths = [measure(" ".join(r)) for r in rows]
    if not any(f["fault"] == LONE_LAST for f in faults([" ".join(r) for r in rows], widths, heading=True,
                                                         balance_min=balance_min)):
        return None
    words = [w for r in rows for w in r]
    n = len(words)
    best = None
    for m in range(1, len(rows) + 1):
        for cuts in itertools.combinations(range(1, n), m - 1):
            cand = [" ".join(words[a:b]) for a, b in itertools.pairwise((0, *cuts, n))]
            ws = [measure(t) for t in cand]
            if max(ws) > width + 1e-6:
                continue
            left = [f for f in faults(cand, ws, heading=True, balance_min=balance_min) if f["fault"] != WEAK_END]
            if left:
                continue
            key = (abs(len(rows) - m), (max(ws) - min(ws)) if m > 1 else 0.0)
            if best is None or key < best[0]:
                best = (key, cand)
    return best[1] if best else None


def violations(lines: Sequence[str], widths: Sequence[float] | None = None, heading: bool = False,
               balance_min: float | None = None) -> list[dict]:
    """faults() without the weak ends: what the corpus proof counts."""
    return [f for f in faults(lines, widths, heading, balance_min) if f["fault"] in VIOLATIONS]


_ELLIPSIS = re.compile(r"(…|\.\.\.)")


def ellipsis_drop(drawn: str, source: str) -> list[str]:
    """Truth guard for every fitter: the words of `source` that `drawn` lost behind an ellipsis INSIDE a sentence
    (drawn text "... THE STONE … BEEN MOVED" for source "the stone has been moved": ["has"]). A fitter never drops a
    word inside a sentence: it reflows, steps down its ladder, or refuses. An ellipsis the source itself carries, or
    one that ends the drawn text (a recorded truncation at a sentence's end is the caller's own record), is none."""
    if not _ELLIPSIS.search(drawn or ""):
        return []
    norm = lambda s: [re.sub(r"[^\w]", "", w).lower() for w in _ELLIPSIS.sub(" … ", str(s or "")).split()]
    d, s = [w for w in norm(drawn) if w or True], [w for w in norm(source) if w]
    d = [w if w else "…" for w in d]
    lost: list[str] = []
    i = 0
    for pos, w in enumerate(d):
        if w == "…":
            nxt = next((x for x in d[pos + 1:] if x != "…"), None)
            if nxt is None:
                break
            j = i
            while j < len(s) and s[j] != nxt:
                j += 1
            if j < len(s):
                lost.extend(s[i:j])
                i = j
            continue
        if i < len(s) and s[i] == w:
            i += 1
    return lost


# ------------------------------------------------------------------------------------------- the paragraph breaker
def line_cost(words: Sequence[str], cuts: Sequence[int], bonds: set[int] | None = None, seams: set[int] | None = None,
              names: set[int] | None = None) -> tuple[int, int, int, int]:
    """(unit splits, never ends, name splits, last-resort breaks) of the lines `cuts` make of `words`: the policy's
    whole order of violations, worst first, which every breaker minimises before it looks at the rag. A break before
    a range's joiner (`range_seams`: "1,090 tonnes / to 1,168 tonnes") is the range's recorded last resort: it
    weighs with the last-resort ends, never as a unit split, so no breaker trades it for a number parted from its
    unit at the same cost (freeze cycle 5, review D1: the rag then chose "nearly 1,090 / tonnes")."""
    bonds = keep_together(words) if bonds is None else bonds
    seams = range_seams(words) if seams is None else seams
    names = name_bonds(words) if names is None else names
    classes = [end_class(words[k - 1]) for k in cuts]
    return (sum(k in bonds and k not in seams for k in cuts), classes.count("never"),
            sum(k in names and k not in bonds for k in cuts),
            classes.count("last_resort") + sum(k in seams for k in cuts))


def _cuts_of(lines: Sequence[str]) -> tuple[list[str], list[int]]:
    rows = [r for r in (_split(ln) for ln in lines) if r]
    words = [w for r in rows for w in r]
    cuts, at = [], 0
    for r in rows[:-1]:
        at += len(r)
        cuts.append(at)
    return words, cuts


SET_LINES_MAX_FORMS = 250_000     # break sets one body may enumerate (a 40-word body on five lines is ~82,000)
# A line may run this far over its character measure to hold the policy (lander, 2026-11-26: the carousel body's 34
# characters stretch to 38, never 41), recorded when used (drawn_record: measure_over)
MEASURE_STRETCH = 1.12


def stretch_chars(chars: int) -> int:
    """The longest line (characters) a body of character measure `chars` may draw to hold the policy (34 -> 38)."""
    return round(chars * MEASURE_STRETCH) if chars else 0


def _need(words: Sequence[str], chars: int | None) -> int:
    """The fewest lines `words` take within the character measure `chars` (greedy): the body's own line count."""
    if not chars:
        return 1
    need, used = 1, 0
    for x in words:
        used = len(x) if not used else used + 1 + len(x)
        if used > chars and used != len(x):
            need, used = need + 1, len(x)
    return need


def _form_key(words, cuts, chars, need, bonds, seams, names, classes) -> tuple[int, ...]:
    """The order `set_lines` minimises, worst first (see its text); the rag is the caller's."""
    n = len(words)
    spans = list(itertools.pairwise((0, *cuts, n)))
    cl = [classes[k] for k in cuts]
    lens = [len(" ".join(words[a:b])) for a, b in spans]
    stretch = stretch_chars(chars) if chars else 0
    far = sum(max(0, x - stretch) for x in lens) if chars else 0
    near = sum(max(0, min(x, stretch) - chars) for x in lens) if chars else 0
    m = len(spans)
    lone = int(m > 1 and n >= 3 and spans[-1][1] - spans[-1][0] == 1) + (
        sum(1 for a, b in spans[:-1] if b - a == 1) if m > 1 and n >= 4 else 0)
    return (sum(k in bonds and k not in seams for k in cuts), cl.count("never"),
            sum(k in names and k not in bonds for k in cuts), far,
            cl.count("last_resort") + sum(k in seams for k in cuts), lone, max(0, m - need), near, cl.count("weak"))


def set_lines(text: str, measure: Callable[[str], float], width: float, max_lines: int, chars: int | None = None,
              min_lines: int = 1) -> list[str] | None:
    """Body copy set under the whole policy in ONE search (freeze cycle 5, review D1 / D8): every break of `text`
    into `min_lines` .. `max_lines` lines no wider than `width` (measure(line) -> px), the best by, in order:

    1. no number parted from its unit, scale word, hedge or range end (`keep_together`);
    2. the fewest never ends; 3. no proper name parted (`name_bonds`: never to avoid a function-word end);
    4. no line further over the character measure `chars` than MEASURE_STRETCH allows;
    5. the fewest last-resort breaks (a function-word end, a range seam);
    6. no one-word line; 7. no line more than the body needs at its measure (fewer lines are preferred to a line
       slightly over the measure: an extra line moves the whole stack);
    8. the least overrun of the measure within the stretch (legal while the line fits `width`, and RECORDED:
       `drawn_record`), so a line of 40 characters is taken before a figure is parted from its unit;
    9. the fewest weak ends; 10. the evenest rag (a last line longer than the one above it counted again, half).

    None when nothing fits (the caller steps its ladder or refuses; never a dropped word)."""
    words = [w for w in str(text or "").replace(NBSP, " ").split() if w]
    n = len(words)
    if not n:
        return []
    bonds, seams, names = keep_together(words), range_seams(words), name_bonds(words)
    cache: dict[tuple[int, int], float] = {}

    def w(a: int, b: int) -> float:
        if (a, b) not in cache:
            cache[(a, b)] = measure(" ".join(words[a:b]))
        return cache[(a, b)]

    if any(w(i, i + 1) > width for i in range(n)):
        return None
    classes = {k: end_class(words[k - 1]) for k in range(1, n)}
    need = _need(words, chars)
    best = None
    forms = 0
    for m in range(max(1, min_lines), min(max_lines, n) + 1):
        for cuts in itertools.combinations(range(1, n), m - 1):
            forms += 1
            if forms > SET_LINES_MAX_FORMS:
                break
            ws = [w(a, b) for a, b in itertools.pairwise((0, *cuts, n))]
            if max(ws) > width:
                continue
            mean = sum(ws) / m
            rag = sum((x - mean) ** 2 for x in ws) + (0.5 * max(0.0, ws[-1] - ws[-2]) ** 2 if m > 1 else 0.0)
            key = (*_form_key(words, cuts, chars, need, bonds, seams, names, classes), abs(m - need), round(rag, 3))
            if best is None or key < best[0]:
                best = (key, cuts)
    if best is None:
        return None
    return [" ".join(words[a:b]) for a, b in itertools.pairwise((0, *best[1], n))]


def form_key(lines: Sequence[str], chars: int | None = None) -> tuple[int, ...]:
    """What `set_lines` minimises before the rag, for drawn `lines`: (unit splits, never ends, name splits, characters
    beyond the measure's stretch, last-resort breaks, one-word lines, lines more than the measure needs, characters
    over the measure within the stretch, weak ends). A setting no worse by this key than the search's best stands as
    it is (its own rag; no pixel moves)."""
    words, cuts = _cuts_of(lines)
    classes = {k: end_class(words[k - 1]) for k in range(1, len(words))}
    return _form_key(words, cuts, chars, _need(words, chars), keep_together(words), range_seams(words),
                     name_bonds(words), classes)


def policy_key(lines: Sequence[str], chars: int | None = None) -> tuple[int, int, int, int, int]:
    """(unit splits, never ends, name splits, characters beyond the measure's stretch, last-resort breaks) of drawn
    `lines`: the policy's violations, worst first (`form_key`'s head)."""
    return form_key(lines, chars)[:5]


def legal_alternative(text: str, measure: Callable[[str], float], width: float, max_lines: int,
                      lines: Sequence[str], chars: int | None = None) -> list[str] | None:
    """A breaking of `text` that fits `width` on at most `max_lines` lines and is strictly better under the policy
    than the drawn `lines` (`policy_key`), else None: the corpus proof's test that a violation left in drawn body
    copy had no legal alternative."""
    got = set_lines(text, measure, width, max_lines, chars)
    if got is None or policy_key(got, chars) >= policy_key(lines, chars):
        return None
    return got


def drawn_record(lines: Sequence[str], chars: int | None = None, **where) -> list[dict]:
    """The record a renderer persists for the body lines it DREW (never a trial fit): every policy violation left in
    them and every line over the character measure `chars`, each {"fault", "line", "text" (the drawn line), "word" |
    "chars"}; [] when the lines are clean. Computed from the drawn lines themselves, so the record names what is on the picture."""
    rows = [" ".join(_split(ln)) for ln in lines]
    out = [dict(f, text=rows[f["line"]], **where) for f in violations(rows)]
    if chars:
        out += [{"fault": MEASURE_OVER, "line": i, "text": ln, "chars": len(ln), "measure": chars, **where}
                for i, ln in enumerate(rows) if len(ln) > chars]
    return out


def wrap(text: str, measure: Callable[[str], float], width: float, max_lines: int | None = None,
         min_lines: int = 1, widow: bool = True, record: dict | None = None) -> list[str] | None:
    """`text` broken into lines no wider than `width` (measure(line) -> px) under the policy: among the breaks on the
    FEWEST lines that fit (never fewer than `min_lines`, never more than `max_lines`), the one with no keep-together
    split, then the fewest never ends, then the fewest last-resort ends, then (`widow`) no one-word last line where
    another break avoids it, then the fewest weak ends, then the evenest rag. Where the fewest lines leave a
    violation and one more line (within `max_lines`) removes it, the longer form is taken (reflow at the same size
    comes before any smaller size).

    None when the words do not fit `max_lines` (the caller steps down its ladder or refuses; it never drops a
    word). `record`, when given, receives {"lines", "faults"}: the violations that stay, to be recorded by the
    caller (no legal break exists at this width)."""
    words = str(text or "").replace(NBSP + NBSP, NBSP).split(" ")
    words = [w for w in words if w]
    if not words:
        return []
    n = len(words)
    plain = [w.replace(NBSP, " ") for w in words]
    flat: list[str] = []
    owner: list[int] = []            # each policy word's glued chunk (a no-break space never breaks)
    for i, w in enumerate(plain):
        for part in w.split():
            flat.append(part)
            owner.append(i)
    bonds_flat = keep_together(flat)
    first = {}
    for j, o in enumerate(owner):
        first.setdefault(o, j)
    bonds = {k for k in range(1, n) if first[k] in bonds_flat}
    seams_flat, names_flat = range_seams(flat), name_bonds(flat) - bonds_flat
    soft = {k: (int(first[k] in seams_flat), int(first[k] in names_flat)) for k in range(1, n)}
    cache: dict[tuple[int, int], float] = {}

    def w(a: int, b: int) -> float:
        if (a, b) not in cache:
            cache[(a, b)] = measure(" ".join(words[a:b]))
        return cache[(a, b)]

    if any(w(i, i + 1) > width for i in range(n)):
        return None
    # the fewest lines that fit (greedy is optimal for the count)
    need, a = 1, 0
    for b in range(1, n + 1):
        if w(a, b) > width:
            need, a = need + 1, b - 1
    need = max(need, min_lines)
    if max_lines is not None and need > max_lines:
        return None
    best = None
    tries = [need] + ([need + 1] if (max_lines is None or need + 1 <= max_lines) and need + 1 <= n else [])
    for m in tries:
        found = _best_breaks(words, m, w, width, bonds, widow, soft)
        if found is None:
            continue
        if best is None or found[0][:4] < best[0][:4]:   # one more line only to remove a violation
            best = found
        if best[0][:4] == (0, 0, 0, 0):
            break
    if best is None:
        return None
    cuts = best[1]
    lines = [" ".join(words[a:b]) for a, b in itertools.pairwise((0, *cuts, n))]
    if record is not None:
        record["lines"] = [ln.replace(NBSP, " ") for ln in lines]
        record["faults"] = violations(lines)
    return lines


def _best_breaks(words, m, w, width, bonds, widow, soft=None):
    """The best cuts of `words` into exactly `m` lines within `width`: dynamic programme over (lines, end). The key:
    (unit splits, never ends, name splits, last-resort breaks, lone last line, weak ends, rag); `soft[k]` = (range
    seam, name bond) at k: a seam is the range's last resort, never a unit split."""
    n = len(words)
    if m > n:
        return None
    if m == 1:
        return ((0, 0, 0, 0, 0, 0, 0.0), ()) if w(0, n) <= width else None
    pen = {}
    for k in range(1, n):
        cls = end_class(words[k - 1].replace(NBSP, " ").split()[-1])
        seam, name = (soft or {}).get(k, (0, 0))
        pen[k] = (int(k in bonds and not seam), int(cls == "never"), int(bool(name) and k not in bonds),
                  int(cls == "last_resort") + int(bool(seam) and k in bonds), int(cls == "weak"))
    # enumerate when small (exact rag), else a DP on the additive key with a greedy rag
    if n <= 18 and m <= 6:
        best = None
        for cuts in itertools.combinations(range(1, n), m - 1):
            spans = list(itertools.pairwise((0, *cuts, n)))
            ws = [w(a, b) for a, b in spans]
            if max(ws) > width:
                continue
            p = [sum(pen[k][i] for k in cuts) for i in range(5)]
            lone = int(widow and spans[-1][1] - spans[-1][0] == 1 and n >= 3)
            mean = sum(ws) / len(ws)
            rag = sum((x - mean) ** 2 for x in ws[:-1]) + max(0.0, ws[-1] - max(ws[:-1])) ** 2
            key = (p[0], p[1], p[2], p[3], lone, p[4], round(rag, 3))
            if best is None or key < best[0]:
                best = (key, cuts)
        return best
    INF = None
    table: list[dict[int, tuple]] = [dict() for _ in range(m + 1)]
    table[0][0] = ((0, 0, 0, 0, 0, 0, 0.0), ())
    for line in range(1, m + 1):
        for end in range(line, n - (m - line) + 1):
            if line == m and end != n:
                continue
            for start, (key, cuts) in table[line - 1].items():
                if start >= end or w(start, end) > width:
                    continue
                p = pen[end] if end < n else (0, 0, 0, 0, 0)
                lone = int(widow and line == m and end - start == 1 and n >= 3)
                slack = (width - w(start, end)) ** 2 if line < m else 0.0
                cand = ((key[0] + p[0], key[1] + p[1], key[2] + p[2], key[3] + p[3], key[4] + lone, key[5] + p[4],
                         key[6] + slack),
                        cuts + ((end,) if end < n else ()))
                if end not in table[line] or cand[0] < table[line][end][0]:
                    table[line][end] = cand
    return table[m].get(n, INF)
