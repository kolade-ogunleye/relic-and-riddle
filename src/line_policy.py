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
VIOLATIONS = (NEVER_END, FUNCTION_END, UNIT_SPLIT, LONE_LAST)


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
            if is_number(ws[j]):
                out.update((k, k + 1))
    return out


def range_seams(words: Sequence[str]) -> set[int]:
    """The positions inside a range where a break is the recorded last resort: before its joiner (BETWEEN 94 / AND
    104 MILLION YEARS)."""
    ws = [str(w) for w in words]
    t = [_tok(w) for w in ws]
    return {k for k in range(1, len(ws) - 1) if t[k] in RANGE_JOINERS and is_number(ws[k + 1])
            and k in keep_together(ws)}


# ------------------------------------------------------------------------------------------------ phrase unit
_NOT_ADVERBS = frozenset(("only", "early", "family", "italy", "july", "supply", "reply", "holy", "assembly", "rally",
                          "fly", "ally", "belly", "bully", "daily", "likely", "lonely", "ugly", "friendly", "elderly",
                          "costly", "deadly", "lively", "lovely", "orderly", "apply", "multiply", "butterfly",
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
# Every violation a breaker could not remove (no legal break at that size and width) is noted here, so it is RECORDED
# and never silent: a renderer drains the notes into its layout record (take_notes), and the corpus proof reads them
# through the same hook. Thread-local: renders run in worker threads.
import threading as _threading

_notes = _threading.local()
NOTE_LIMIT = 400


def note(lines: Sequence[str], left: Sequence[dict], **where) -> None:
    """Record violations that stay in drawn `lines` (the fallback order found no legal break)."""
    box = getattr(_notes, "items", None)
    if box is None:
        box = _notes.items = []
    if len(box) < NOTE_LIMIT:
        box.append({"lines": [str(ln).replace(NBSP, " ") for ln in lines],
                    "faults": [dict(f) for f in left], "reason": "no legal break at this size and width", **where})


def take_notes() -> list[dict]:
    """The notes since the last call (this thread), cleared."""
    box = getattr(_notes, "items", None) or []
    _notes.items = []
    return list(box)


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
    if heading and len(words) >= 3 and len(rows[-1]) == 1:
        floor = brand_system.SPEC["heading.balance_min"] if balance_min is None else balance_min
        ws = list(widths[-2:]) if widths is not None and len(widths) >= 2 else [len(" ".join(rows[-2])),
                                                                                 len(rows[-1][0])]
        bal = min(ws) / max(ws) if max(ws) > 0 else 1.0
        if bal < floor:
            out.append({"fault": LONE_LAST, "line": len(rows) - 1, "word": bare(rows[-1][0]),
                        "balance": round(bal, 2)})
    return out


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
def break_cost(words: Sequence[str], cuts: Sequence[int], bonds: set[int] | None = None) -> tuple[int, int, int, int]:
    """(unit splits, never ends, last-resort ends, weak ends) of the lines `cuts` make of `words`: the order every
    breaker minimises before it looks at the rag."""
    bonds = keep_together(words) if bonds is None else bonds
    classes = [end_class(words[k - 1]) for k in cuts]
    return (sum(k in bonds for k in cuts), classes.count("never"), classes.count("last_resort"),
            classes.count("weak"))


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
        found = _best_breaks(words, m, w, width, bonds, widow)
        if found is None:
            continue
        if best is None or found[0][:3] < best[0][:3]:   # one more line only to remove a violation
            best = found
        if best[0][:3] == (0, 0, 0):
            break
    if best is None:
        return None
    cuts = best[1]
    lines = [" ".join(words[a:b]) for a, b in itertools.pairwise((0, *cuts, n))]
    if record is not None:
        record["lines"] = [ln.replace(NBSP, " ") for ln in lines]
        record["faults"] = violations(lines)
    return lines


def _best_breaks(words, m, w, width, bonds, widow):
    """The best cuts of `words` into exactly `m` lines within `width`: dynamic programme over (lines, end)."""
    n = len(words)
    if m > n:
        return None
    if m == 1:
        return ((0, 0, 0, 0, 0, 0.0), ()) if w(0, n) <= width else None
    pen = {}
    for k in range(1, n):
        cls = end_class(words[k - 1].replace(NBSP, " ").split()[-1])
        pen[k] = (int(k in bonds), int(cls == "never"), int(cls == "last_resort"), int(cls == "weak"))
    # enumerate when small (exact rag), else a DP on the additive key with a greedy rag
    if n <= 18 and m <= 6:
        best = None
        for cuts in itertools.combinations(range(1, n), m - 1):
            spans = list(itertools.pairwise((0, *cuts, n)))
            ws = [w(a, b) for a, b in spans]
            if max(ws) > width:
                continue
            p = [sum(pen[k][i] for k in cuts) for i in range(4)]
            lone = int(widow and spans[-1][1] - spans[-1][0] == 1 and n >= 3)
            mean = sum(ws) / len(ws)
            rag = sum((x - mean) ** 2 for x in ws[:-1]) + max(0.0, ws[-1] - max(ws[:-1])) ** 2
            key = (p[0], p[1], p[2], lone, p[3], round(rag, 3))
            if best is None or key < best[0]:
                best = (key, cuts)
        return best
    INF = None
    table: list[dict[int, tuple]] = [dict() for _ in range(m + 1)]
    table[0][0] = ((0, 0, 0, 0, 0, 0.0), ())
    for line in range(1, m + 1):
        for end in range(line, n - (m - line) + 1):
            if line == m and end != n:
                continue
            for start, (key, cuts) in table[line - 1].items():
                if start >= end or w(start, end) > width:
                    continue
                p = pen[end] if end < n else (0, 0, 0, 0)
                lone = int(widow and line == m and end - start == 1 and n >= 3)
                slack = (width - w(start, end)) ** 2 if line < m else 0.0
                cand = ((key[0] + p[0], key[1] + p[1], key[2] + p[2], key[3] + lone, key[4] + p[3], key[5] + slack),
                        cuts + ((end,) if end < n else ()))
                if end not in table[line] or cand[0] < table[line][end][0]:
                    table[line][end] = cand
    return table[m].get(n, INF)
