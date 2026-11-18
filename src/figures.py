"""
src/figures.py
==============
The one figure rule (design-freeze cycle 0 round 8, lane TRUTH; QF-147; operator ruling 8 of 2026-11-17).

A stated quantity is parsed WHOLE from signed copy: its hedge ("UP TO", "ROUGHLY", a sentence's "By one estimate,"),
its number or span ("11.5", "137-138", "eight to 18"), its scale word ("1.2 MILLION"), its unit ("FT", "PEOPLE",
"LEVELS") and its bracketed conversion ("(41.75 m)"). Every figure-bearing surface reads it here:

- `parse(text)`: every figure of a text, whole (never its first digits: "11" for "11.5 feet", "18" for "eight to 18
  levels", "137" for "between roughly 137 feet ... and 138 feet", "20,000" without its "up to").
- `stat_figure(body, ...)`: the one figure a carousel stat slide draws. It is the BODY's own figure (the body is the
  slide's signed copy and must state the drawn figure); a body that states none gives None, and the slide falls back
  to its no-figure layout (a heading's "280 FEET" over a body about levels is never drawn as the slide's figure).
- `truth_faults(drawn, signed)`: the critic. Every drawn number is among the signed numbers in its unit; a signed
  span is drawn whole; a signed hedge is drawn with it.
- `quantity_spans(text)` / `gold_runs(text)`: ruling 8, the gold scope. The number and its unit are gold (the
  figure face's colour); qualifiers (ABOUT, UP TO, EST., OVER ...), dimension words (TALL, DEEP, LONG, ACROSS, AGO)
  and every other word stay bone. One rule on every quantity surface: number cards, stat slides, the scale headline
  and its on-drawing labels, covers and lockups (src/heading.py), Story reveal frames, hook frames.
- `deck_lead(payload)`: one lead unit per run (the signed scale fact's unit decides; else the narration's).

Topic-agnostic: closed vocabularies of units, count units, hedges and dimension words; no topic names.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

# ------------------------------------------------------------------------------------------------ vocabularies
NUMBER_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
                "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
                "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
SCALE_WORDS = ("thousand", "million", "billion", "trillion")
SCALE_VALUE = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}
ERAS = ("bc", "bce", "ad", "ce")
# Count units: a number of these is a stated quantity whose unit is gold with it (ruling 8). Closed and generic.
COUNT_UNITS = ("people", "persons", "levels", "level", "storeys", "storey", "stories", "floors", "tons", "ton",
               "tonnes", "tonne", "hours", "hour", "days", "day", "weeks", "months", "years", "year", "centuries",
               "century", "decades", "degrees", "percent", "%", "acres", "hectares")
# The hedges a figure carries (longest first when matched). "between" opens a span; it is not a hedge.
HEDGES = ("as many as", "as much as", "more than", "fewer than", "less than", "at least", "up to", "an estimated",
          "estimated", "approximately", "approx.", "roughly", "about", "almost", "nearly", "around", "over",
          "some", "circa", "c.", "est.", "~", "under", "just over", "just under", "well over")
# A hedge drawn on a figure (abbreviations and signs spelled out: "EST." on a size read as "established").
HEDGE_DRAWN = {"~": "ABOUT", "approx.": "APPROXIMATELY", "est.": "ESTIMATED", "an estimated": "ESTIMATED",
               "c.": "CIRCA"}
# Words after a unit that say what it measures: bone, never gold (ruling 8: TALL, DEEP, ACROSS ...).
DIMENSION_WORDS = ("tall", "high", "deep", "wide", "long", "across", "thick", "ago", "old", "in diameter",
                   "in circumference")
# A sentence's lead that hedges the whole sentence ("By one estimate, ..."): drawn as its one word.
LEAD_HEDGES = (("estimat", "ESTIMATED"), ("approximat", "APPROXIMATELY"))


def _measure_units() -> Mapping[str, tuple]:
    from .typography import _UNITS
    return _UNITS


@lru_cache(maxsize=1)
def _unit_tokens() -> tuple[str, ...]:
    toks = set(_measure_units()) | set(COUNT_UNITS)
    return tuple(sorted(toks, key=lambda u: (-len(u), u)))


@lru_cache(maxsize=512)
def _phrase(p: str) -> tuple[str, ...]:
    return tuple(t.lower() for t, _a, _b in _tokens(p))


_TOKEN = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|[A-Za-z]+|²|[–—-]|[()~+%/]|[^\s\w]")


@dataclass
class Figure:
    """One stated quantity, whole. `lo` / `hi` are the numbers as written (a number word given as its digits), `unit`
    as drawn ("FT", "PEOPLE"; "" for a bare count), `hedge` as drawn ("UP TO", "ESTIMATED"; "" for none), `scale`
    ("MILLION" or ""), `conv` the bracketed conversion (a Figure, or None), `tail` the dimension word after it
    ("TALL", "LONG"; bone). `start` / `end`: the figure's characters in the text (hedge to unit or conversion);
    `gold`: the character spans ruling 8 gilds (number, span dash, scale and unit; never the hedge or tail)."""
    lo: str
    hi: str = ""
    unit: str = ""
    hedge: str = ""
    scale: str = ""
    conv: Figure | None = None
    tail: str = ""
    start: int = 0
    end: int = 0
    gold: list[tuple[int, int]] = field(default_factory=list)
    lead_hedge: bool = False       # the hedge is the sentence's lead ("By one estimate,"), not beside the number
    measured: bool = False         # the unit is a measurement unit (FT, M, KM ...), not a count
    era: str = ""                  # "BC", "BCE", "AD", "CE" (a date's era; never gold, never dropped)

    @property
    def values(self) -> tuple[float, ...]:
        """The numbers' values, their scale word applied ("1.2 MILLION" = 1,200,000; "two thousand" = 2,000)."""
        k = SCALE_VALUE.get(self.scale.lower(), 1.0)
        return tuple(_value(x.rstrip("+")) * k for x in (self.lo, self.hi) if x)

    @property
    def number(self) -> str:
        """The drawn number: "137–138" for a span (an en dash, no spaces), with its scale word."""
        n = f"{self.lo}–{self.hi}" if self.hi else self.lo
        return f"{n} {self.scale}" if self.scale else n

    @property
    def quantity(self) -> str:
        """Number and unit, the gold run ("137–138 FT", "20,000 PEOPLE")."""
        return " ".join(x for x in (self.number, self.unit) if x)

    def drawn(self, era: bool = True) -> str:
        """The whole figure: hedge, number, unit, the dimension word that says what it measures and its era ("UP TO
        20,000 PEOPLE", "ROUGHLY 137–138 FT", "94–104 MILLION YEARS AGO", "9500–9000 BCE", "AD 43"). The era is
        kept so a re-parse reads the same figure (a descending BCE span without it read as two numbers). `era`
        False: without it (the stat slide's drawn figure, unchanged in round-8 integration: drawing an era there is
        a design change for the panel)."""
        pre = "AD" if era and self.era == "AD" else ""
        post = self.era if era and self.era not in ("", "AD") else ""
        return " ".join(x for x in (self.hedge, pre, self.quantity, self.tail, post) if x)

    def record(self) -> dict[str, Any]:
        return {"hedge": self.hedge, "number": self.number, "unit": self.unit, "span": bool(self.hi),
                "conversion": self.conv.quantity if self.conv else "", "tail": self.tail,
                "values": list(self.values), "era": self.era, "drawn": self.drawn(era=False)}


def _value(raw: str) -> float:
    raw = str(raw).replace(",", "").strip()
    if raw.lower() in NUMBER_WORDS:
        return float(NUMBER_WORDS[raw.lower()])
    return float(raw)


def _is_num(tok: str) -> bool:
    return bool(re.fullmatch(r"\d[\d,]*(?:\.\d+)?", tok))


def _unit_drawn(token: str) -> tuple[str, bool]:
    """(the unit as drawn, measured?) for a unit token: a measurement unit's short form ("feet" -> "FT"), a count unit
    in capitals ("people" -> "PEOPLE")."""
    low = " ".join(token.lower().split())
    info = _measure_units().get(low)
    if info:
        return info[3].upper(), True
    return low.upper(), False


def _tokens(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(0), m.start(), m.end()) for m in _TOKEN.finditer(text or "")]


def _match_phrase(toks: Sequence[tuple[str, int, int]], i: int, phrases: Iterable[str]) -> tuple[str, int] | None:
    """The longest phrase of `phrases` starting at token i: (phrase, the index after it)."""
    best = None
    for p in phrases:
        words = _phrase(p)
        n = len(words)
        if i + n > len(toks):
            continue
        if all(toks[i + k][0].lower() == words[k] for k in range(n)) and (best is None or n > best[1] - i):
            best = (p, i + n)
    return best


def _hedge_before(toks, i: int) -> tuple[str, int] | None:
    """(the hedge phrase ending right before token i, its first token's index), or None."""
    best = None
    for p in HEDGES:
        n = len(_phrase(p))
        j = i - n
        if j < 0:
            continue
        m = _match_phrase(toks, j, (p,))
        if m and m[1] == i and (best is None or n > i - best[1]):
            best = (p, j)
    return best


def _unit_at(toks, i: int) -> tuple[str, int, bool] | None:
    """(unit as drawn, the index after it, measured?) for a unit at token i (after an optional hyphen), or None.
    "m" and "in" count only when the figure is followed by them as a whole word (never "5M visitors")."""
    j = i
    if j < len(toks) and toks[j][0] in ("-",):
        j += 1
    m = _match_phrase(toks, j, _unit_tokens())
    if not m:
        return None
    phrase, after = m
    if after < len(toks) and toks[after][0] == "²":
        return None
    if phrase.lower() in ("m", "in", "t") and (j == i and toks[j][1] == toks[i - 1][2]):
        return None          # glued to the number ("5M"): not a metre
    if phrase.lower() == "in" and after < len(toks) and toks[after][0][:1].isalpha():
        return None          # "in" opening a phrase ("18 in the city")
    drawn, measured = _unit_drawn(phrase)
    return drawn, after, measured


def _number_at(toks, i: int, words_ok: bool) -> tuple[str, int] | None:
    """(the number as drawn, the index after it) for a number at token i: digits, or a number word when `words_ok`."""
    t = toks[i][0]
    if _is_num(t):
        return t, i + 1
    if words_ok and t.lower() in NUMBER_WORDS:
        return str(NUMBER_WORDS[t.lower()]), i + 1
    return None


def _sentence_lead_hedge(text: str, pos: int) -> str:
    """The one word a sentence's hedging lead gives ("By one estimate, ... 11.5 feet" -> "ESTIMATED"), else ""."""
    start = max(text.rfind(". ", 0, pos), text.rfind("! ", 0, pos), text.rfind("? ", 0, pos))
    sentence = text[start + 2 if start >= 0 else 0:pos]
    m = re.match(r"\s*([A-Za-z][A-Za-z ]{1,40}?)\s*,\s", sentence)
    if m and len(m.group(1).split()) <= 4:
        for stem, word in LEAD_HEDGES:
            if stem in m.group(1).lower():
                return word
    return ""


def _hedge_drawn(phrase: str) -> str:
    low = phrase.lower()
    return HEDGE_DRAWN.get(low, phrase.upper())


def parse(text: str) -> list[Figure]:
    """Every stated quantity in `text`, whole (see the module docstring). A bare number with no unit, scale word or
    hedge (a year, "in 1963") is a figure too (its number is gold, ruling 3), with unit ""."""
    text = text or ""
    toks = _tokens(text)
    out: list[Figure] = []
    i = 0
    while i < len(toks):
        tok = toks[i][0]
        # "between [hedge] X ... and Y" / a number / a number word that opens a span or carries a unit
        num = _number_at(toks, i, words_ok=True)
        if num is None:
            i += 1
            continue
        if not _is_num(tok):   # a number word: only as a span's low end or with its unit ("eight to 18", "two tons")
            between = any(toks[k][0].lower() == "between" for k in range(max(0, i - 3), i))
            nxt = _number_after_sep(toks, num[1], between=between)
            scaled = num[1] < len(toks) and toks[num[1]][0].lower() in SCALE_WORDS
            if nxt is None and _unit_at(toks, num[1]) is None and not scaled:
                i += 1
                continue
        fig, i = _figure_from(text, toks, i)
        out.append(fig)
    return out


def _number_after_sep(toks, j: int, between: bool = False) -> tuple[str, int, int] | None:
    """(separator, the number token's index, its index after) for "- 18" / "to 18" / "and 18" (and only after
    "between") at token j, or None."""
    if j >= len(toks):
        return None
    sep = toks[j][0].lower()
    if sep in ("-", "–", "—", "to") or (between and sep == "and"):
        k = j + 1
        hb = _match_phrase(toks, k, HEDGES)   # "and roughly 138"
        if hb:
            k = hb[1]
        if k < len(toks) and _is_num(toks[k][0]):
            return sep, k, k + 1
    return None


def _figure_from(text: str, toks, i: int) -> tuple[Figure, int]:
    """The figure whose number starts at token i, and the token index after it."""
    hedge, h_start = "", None
    hb = _hedge_before(toks, i)
    between = False
    if hb:
        hedge, h_start = hb
        if h_start > 0 and toks[h_start - 1][0].lower() == "between":
            between = True
    elif i > 0 and toks[i - 1][0].lower() == "between":
        between, h_start = True, i - 1
    if between and hb is None:
        h_start = i - 1
    lo, j = _number_at(toks, i, True)
    gold = [(toks[i][1], toks[j - 1][2])]
    plus = ""
    if j < len(toks) and toks[j][0] == "+":
        plus, gold[-1] = "+", (gold[-1][0], toks[j][2])
        j += 1
    scale = ""
    if j < len(toks) and toks[j][0].lower() in SCALE_WORDS:
        scale, gold[-1] = toks[j][0].upper(), (gold[-1][0], toks[j][2])
        j += 1
    unit1 = _unit_at(toks, j)
    unit, measured = "", False
    if unit1:
        unit, u_after, measured = unit1
        gold[-1] = (gold[-1][0], toks[u_after - 1][2])
        j = u_after
    conv1, j = _conversion(text, toks, j)
    era = ""
    if i > 0 and toks[i - 1][0].lower() == "ad":            # "AD 43"
        era = "AD"
    if j < len(toks) and toks[j][0].lower() in ERAS:        # "205 BC to 87 BC", "9500 BCE"
        era, j = toks[j][0].upper(), j + 1
    hi = ""
    sep = _number_after_sep(toks, j, between=between)
    if sep and not plus:
        s, k, after = sep
        h_lo, h_hi = _value(lo), _value(toks[k][0])
        if h_lo < h_hi or _counts_down(toks, after) or era in ("BC", "BCE"):
            hi = toks[k][0]
            if s in ("-", "–", "—") and not unit1:
                gold[-1] = (gold[-1][0], toks[k][2])   # "137-138": one gold run with its dash
            else:
                gold.append((toks[k][1], toks[k][2]))
            j = after
            if j < len(toks) and toks[j][0].lower() in SCALE_WORDS and not scale:
                scale = toks[j][0].upper()
                gold[-1] = (gold[-1][0], toks[j][2])
                j += 1
            unit2 = _unit_at(toks, j)
            if unit2 and (not unit or unit2[0] == unit):
                unit, measured = unit2[0], unit2[2]
                gold[-1] = (gold[-1][0], toks[unit2[1] - 1][2])
                j = unit2[1]
            conv2, j = _conversion(text, toks, j)
            if conv1 and conv2 and conv1.unit == conv2.unit and _value(conv1.lo) < _value(conv2.lo):
                conv1 = Figure(lo=conv1.lo, hi=conv2.lo, unit=conv1.unit, measured=conv1.measured,
                               start=conv1.start, end=conv2.end, gold=conv1.gold + conv2.gold)
            elif conv2 and not conv1:
                conv1 = conv2
            if j < len(toks) and toks[j][0].lower() in ERAS:
                era, j = toks[j][0].upper(), j + 1
    end = toks[j - 1][2]
    tail = ""
    m = _match_phrase(toks, j, DIMENSION_WORDS)
    if m and (unit or scale):
        tail = m[0].upper()
    drawn_hedge = _hedge_drawn(hedge) if hedge else ""
    lead = False
    if not drawn_hedge:
        drawn_hedge = _sentence_lead_hedge(text, toks[h_start if h_start is not None else i][1])
        lead = bool(drawn_hedge)
    lo_drawn = lo + plus
    start = toks[h_start][1] if h_start is not None else toks[i][1]
    if not _is_num(toks[i][0]) and scale:
        gold = []   # a spelled-out scaled number ("two thousand years ago"): judged, never gilded (no pixel change)
    fig = Figure(lo=lo_drawn, hi=hi, unit=unit, hedge=drawn_hedge, scale=scale, conv=conv1, tail=tail, start=start,
                 end=end, gold=gold, lead_hedge=lead, measured=measured, era=era)
    return fig, j


def _counts_down(toks, j: int) -> bool:
    """A span that runs down is still a span when it counts back in time: "145,000 to 29,000 years ago", "3000 to
    2500 BC" (token j: what follows the far end)."""
    k = j
    if k < len(toks) and toks[k][0].lower() in SCALE_WORDS:
        k += 1
    if k < len(toks) and toks[k][0].lower() in ("years", "year", "centuries", "century", "millennia"):
        k += 1
    return k < len(toks) and toks[k][0].lower() in ("ago", "bc", "bce")


def _conversion(text: str, toks, j: int) -> tuple[Figure | None, int]:
    """A bracketed conversion at token j ("(41.75 m)", "(about 85 m)"): (its figure, the index after ")")."""
    if j < len(toks) and toks[j][0] == "(":
        k = j + 1
        hb = _match_phrase(toks, k, HEDGES)
        if hb:
            k = hb[1]
        if k < len(toks) and _is_num(toks[k][0]):
            u = _unit_at(toks, k + 1)
            if u:
                close = u[1]
                if close < len(toks) and toks[close][0] == ")":
                    return Figure(lo=toks[k][0], unit=u[0], measured=u[2], start=toks[k][1], end=toks[close - 1][2],
                                  gold=[(toks[k][1], toks[close - 1][2])]), close + 1
    return None, j


# ------------------------------------------------------------------------------------------------ the stat slide
def stat_figure(body: str, lead: str | None = None) -> Figure | None:
    """The figure a carousel stat slide draws: the body's first figure with a unit or a scale word (a measured size,
    a count of people or levels, a span), whole, with its hedge; None when the body states none (the slide's no-figure
    layout). A pair ("280 feet (85 m)") leads in `lead` ("imperial" / "metric": the deck's one lead unit) when given."""
    figs = [f for f in parse(body) if f.gold]   # a judged-only figure (a spelled-out scaled number) is never drawn
    measured = [f for f in figs if f.unit or f.scale]
    for fig in measured or figs[:1]:     # a unit-less figure (a year, "over 600") only when the body states no other
        if lead and fig.conv is not None and fig.measured and _system(fig.unit) not in (None, lead) \
                and _system(fig.conv.unit) == lead:
            fig = Figure(lo=fig.conv.lo, hi=fig.conv.hi, unit=fig.conv.unit, hedge=fig.hedge, scale=fig.scale,
                         conv=Figure(lo=fig.lo, hi=fig.hi, unit=fig.unit, measured=True), tail=fig.tail,
                         start=fig.start, end=fig.end, gold=fig.gold, lead_hedge=fig.lead_hedge, measured=True)
        return fig
    return None


def _system(unit: str) -> str | None:
    for token, info in _measure_units().items():
        if info[3].upper() == unit.upper() or token.upper() == unit.upper():
            return info[1]
    return None


def deck_lead(payload: Mapping[str, Any] | None) -> str | None:
    """The run's one lead unit system: the signed scale fact's ("ft" -> "imperial", "m" -> "metric"), else None (the
    narration's, typography.spoken_units, decides)."""
    fact = (payload or {}).get("scale_fact") or {}
    unit = str(fact.get("unit") or "").strip().lower() if isinstance(fact, Mapping) else ""
    return {"ft": "imperial", "m": "metric"}.get(unit)


def split_drawn(drawn: str) -> tuple[str, str, str, str]:
    """(hedge, number, unit, tail) of a figure as `Figure.drawn` writes it: the hedge and the tail bone, the number
    and the unit gold (ruling 8). "UP TO 20,000 PEOPLE" -> ("UP TO", "20,000", "PEOPLE", ""); "94–104 MILLION
    YEARS AGO" -> ("", "94–104", "MILLION YEARS", "AGO"). Text with no figure is all tail."""
    drawn = drawn or ""
    figs = parse(drawn)
    if not figs:
        return "", "", "", drawn.strip()
    f = figs[0]
    if not f.gold:
        return "", "", "", drawn.strip()
    a, gold_end = f.gold[0][0], f.gold[-1][1]
    num = f"{f.lo}–{f.hi}" if f.hi else f.lo
    at = drawn.find(num, a)
    num_end = at + len(num) if at == a else f.gold[0][1]
    if at != a:
        num = drawn[a:num_end]
    return drawn[:a].strip(), num, drawn[num_end:gold_end].strip(), drawn[gold_end:].strip()


# ------------------------------------------------------------------------------------------------ the critic
def _canon(unit: str) -> str:
    u = unit.upper().strip()
    for token, info in _measure_units().items():
        if token.upper() == u or info[3].upper() == u:
            return info[3].upper()
    singular = {"PEOPLE": "PEOPLE", "PERSONS": "PEOPLE", "LEVEL": "LEVELS", "STOREY": "STOREYS", "STORIES": "STOREYS",
                "TON": "TONS", "TONNE": "TONNES", "HOUR": "HOURS", "DAY": "DAYS", "YEAR": "YEARS",
                "CENTURY": "CENTURIES"}
    return singular.get(u, u)


def _era_ok(a: str, b: str) -> bool:
    """Two figures' eras agree (BC = BCE, AD = CE; a figure with no era agrees with any)."""
    canon = {"BCE": "BC", "CE": "AD"}
    return not a or not b or canon.get(a, a) == canon.get(b, b)


def signed_figures(texts: Iterable[str]) -> list[Figure]:
    out: list[Figure] = []
    for t in texts:
        out += parse(str(t or ""))
    return out


def truth_faults(drawn: str, signed: Iterable[str] | Iterable[Figure]) -> list[str]:
    """The critic (QF-147): [] when every figure in `drawn` is a signed figure in its unit, whole (a signed span drawn
    as its span, never one end) and with its hedge where the signed figure has one; else one line per fault. `signed`:
    the signed texts (the slide's body, the stat cards, the fact) or their parsed figures."""
    signed = list(signed)
    figs = signed if signed and isinstance(signed[0], Figure) else signed_figures(signed)   # type: ignore[arg-type]
    faults = []
    for d in parse(drawn):
        views = [(s, v) for s in figs for v in (s, s.conv) if v is not None
                 and _canon(v.unit) == _canon(d.unit) and _era_ok(d.era, v.era)]
        exact = [(s, v) for s, v in views if v.values == d.values]
        if exact:
            if not d.hedge and all(s.hedge for s, _v in exact):
                faults.append(f"{d.drawn()!r}: the signed figure is hedged ({exact[0][0].hedge}) and the drawn one "
                              "is not")
            continue
        part = [(s, v) for s, v in views if set(d.values) < set(v.values)]
        if part:
            faults.append(f"{d.drawn()!r}: one end of the signed span {part[0][1].quantity!r}")
        else:
            faults.append(f"{d.drawn()!r}: no signed figure has this number in this unit")
    return faults


# ------------------------------------------------------------------------------------------------ ruling 8 gold
def quantity_spans(text: str) -> list[tuple[int, int]]:
    """The character spans ruling 8 gilds in `text`: each figure's number (with its span dash and scale word) and its
    unit; never its hedge, its dimension word or any other word. A token that carries a digit ("6-TON", "1963",
    "25+") is gold whole."""
    spans: list[tuple[int, int]] = []
    for f in parse(text):
        spans += f.gold
        if f.conv is not None:
            spans += f.conv.gold
    for m in re.finditer(r"\S*\d\S*", text or ""):   # a word that carries a digit is a quantity's word
        a, b = m.start(), m.end()
        while b > a and text[b - 1] in ".,;:!?)”’":
            b -= 1
        while a < b and text[a] in "(“‘":
            a += 1
        spans.append((a, b))
    spans.sort()
    merged: list[list[int]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def gold_runs(text: str) -> list[tuple[str, bool]]:
    """`text` as runs [(text, gold)]: the quantity spans gold, the rest bone (spaces kept with the bone run before a
    gold one, so the runs join back to `text`)."""
    out: list[tuple[str, bool]] = []
    pos = 0
    for a, b in quantity_spans(text):
        if a > pos:
            out.append((text[pos:a], False))
        out.append((text[a:b], True))
        pos = b
    if pos < len(text or ""):
        out.append((text[pos:], False))
    return [r for r in out if r[0]]


def gold_word_indices(words: Sequence[str]) -> set[int]:
    """Indices of the words of `words` (one heading's words) that ruling 8 gilds."""
    text, starts, k = "", [], 0
    for w in words:
        starts.append(k)
        text += w + " "
        k += len(w) + 1
    out = set()
    for a, b in quantity_spans(text):
        out |= {i for i, s in enumerate(starts) if s < b and s + len(words[i]) > a}
    return out


def qualifier_word_indices(words: Sequence[str]) -> set[int]:
    """Indices of the hedge and dimension words a figure in `words` carries (ruling 8: bone, also inside a gold
    payoff run)."""
    text, starts, k = "", [], 0
    for w in words:
        starts.append(k)
        text += w + " "
        k += len(w) + 1
    gold = gold_word_indices(words)
    out = set()
    for f in parse(text):
        if not f.gold:
            continue
        ranges = [(f.start, f.gold[0][0])]
        if f.tail:
            tail_at = text.upper().find(f.tail, f.end)
            if tail_at >= 0:
                ranges.append((tail_at, tail_at + len(f.tail)))
        for a, b in ranges:
            out |= {i for i, s in enumerate(starts) if a <= s < b and i not in gold}
    return out


# ------------------------------------------------------------------------------- count-up (operator ruling A)
def counts(value: str) -> bool:
    """Whether a drawn figure may count up (operator ruling A, 2026-11-18): only a single plain quantity ("20,000",
    "280 FT (85 M)", "EST. 144 FT": its hedge stays visible and only the number counts). Years, dates, spans and
    ranges never count: they appear whole, with the same rise and fade as other text (a year, an era date, "YEARS
    AGO", any span "9500-9000 BCE", "94-104"). The one rule every animated figure asks (graphics._shown_value)."""
    figs = parse(value or "")
    if len(figs) != 1:
        return False
    f = figs[0]
    if f.hi or f.era or f.tail in ("AGO", "OLD") or (f.conv is not None and f.conv.hi):
        return False
    raw = f.lo.rstrip("+")
    year = not f.unit and not f.scale and re.fullmatch(r"[0-9]{3,4}", raw) and 1000 <= float(raw) <= 2199
    return not year    # a year (or what reads as one) never counts; a plain quantity, a duration included, does
