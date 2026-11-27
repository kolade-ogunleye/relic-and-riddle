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

import math
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
# A scale written as a letter glued to its number ("94-104M years", "3.5bn years", "12k tonnes"): the scale word,
# read ONLY when a count unit follows (freeze cycle 3, stress realism item 2: the dev Richat stat card signs
# "94-104M years"; the gate read it as a bare '94–104' and failed the card that drew it whole, '94–104 MILLION
# YEARS'). Never a metre or a bare letter: "5M visitors", "40m tall" are not read as scaled.
GLUED_SCALE = {"k": "THOUSAND", "m": "MILLION", "mn": "MILLION", "b": "BILLION", "bn": "BILLION"}
ERAS = ("bc", "bce", "ad", "ce")
# Count units: a number of these is a stated quantity whose unit is gold with it (ruling 8). Closed and generic.
COUNT_UNITS = ("people", "persons", "levels", "level", "storeys", "storey", "stories", "floors", "tons", "ton",
               "tonnes", "tonne", "hours", "hour", "days", "day", "weeks", "months", "years", "year", "centuries",
               "century", "decades", "degrees", "percent", "%", "acres", "hectares",
               # freeze cycle 3: a ton that says which (a "1,200 short tons" source figure is not 1,200 tonnes)
               "short tons", "short ton", "long tons", "long ton", "metric tons", "metric ton")
# The hedges a figure carries (longest first when matched). "between" opens a span; it is not a hedge.
HEDGES = ("as many as", "as much as", "more than", "fewer than", "less than", "at least", "up to", "an estimated",
          "estimated", "approximately", "approx.", "roughly", "about", "almost", "nearly", "around", "over",
          "some", "circa", "c.", "est.", "~", "under", "just over", "just under", "well over",
          # a comparator is a hedge (freeze cycle 3, SYS-B-4: a badge signed "EST. >13,000 LBS" was read as an
          # unhedged 13,000 LB, so the gate passed a drawn '13,000 LB'); after an abbreviated hedge it is the hedge
          "est. >", "est. <", "est. ~", ">", "≥", "<", "≤", "≈")
# A hedge's direction (freeze cycle 3, SYS batch 3): a lower bound ("MORE THAN 129 FT": the quantity is at least it) or
# an upper bound ("UP TO 129 FT"); the rest approximate. A drawn hedge of the opposite direction to every signed one
# states another quantity (the gate passed "UP TO 129 FT" for a signed "more than 129 feet").
HEDGE_LOWER = frozenset(("MORE THAN", "AT LEAST", "OVER", "JUST OVER", "WELL OVER", "ESTIMATED MORE THAN"))
HEDGE_UPPER = frozenset(("UP TO", "AS MANY AS", "AS MUCH AS", "FEWER THAN", "LESS THAN", "UNDER", "JUST UNDER",
                         "ALMOST", "NEARLY", "ESTIMATED LESS THAN"))


def hedge_direction(hedge: str) -> int:
    """+1 for a lower-bound hedge, -1 for an upper-bound one, 0 for an approximate one or none."""
    h = " ".join(str(hedge or "").upper().split())
    return 1 if h in HEDGE_LOWER else -1 if h in HEDGE_UPPER else 0


# A hedge drawn on a figure (abbreviations and signs spelled out: "EST." on a size read as "established").
HEDGE_DRAWN = {"~": "ABOUT", "approx.": "APPROXIMATELY", "est.": "ESTIMATED", "an estimated": "ESTIMATED",
               "c.": "CIRCA", ">": "MORE THAN", "≥": "AT LEAST", "<": "LESS THAN", "≤": "UP TO", "≈": "ABOUT",
               "est. >": "ESTIMATED MORE THAN", "est. <": "ESTIMATED LESS THAN", "est. ~": "ESTIMATED"}
# Words after a unit that say what it measures: bone, never gold (ruling 8: TALL, DEEP, ACROSS ...).
DIMENSION_WORDS = ("tall", "high", "deep", "wide", "long", "across", "thick", "ago", "old", "in diameter",
                   "in circumference")
# A sentence's lead that hedges the whole sentence ("By one estimate, ..."): drawn as its one word.
LEAD_HEDGES = (("estimat", "ESTIMATED"), ("approximat", "APPROXIMATELY"))
# The lead phrases that hedge their sentence WHATEVER follows them (SYS-R8-1, cycle 0 round 8: "By one estimate the
# text ..." without its comma, and a caption's words, which carry no punctuation at all, lost the hedge): a
# preposition's lead ("by one estimate", "according to some estimates", "on current estimates"), "estimates suggest /
# put / place ...", "it is estimated". Matched at the sentence's start, followed by a comma, a dash, a colon or nothing.
LEAD_HEDGE_PHRASE = re.compile(
    r"^\s*(?:(?:by|according\s+to|on|in|from)\s+(?:[a-z']+\s+){0,3}?(?P<a>estimat\w*|approximat\w*)"
    r"|(?P<b>estimat\w*)\s+(?:suggest|put|place|say|indicate|show)\w*"
    r"|it\s+is\s+(?P<c>estimat\w*))(?![\w])", re.IGNORECASE)


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
    era_hi: str = ""               # the far end's era when it differs from the near end's ("500 BC to 500 AD"); else
    #                                "" and `era` is both ends' (SYS-R8-1: an era-crossing span read as "AD 500-500")

    @property
    def eras(self) -> tuple[str, str]:
        """Each end's era, canonical (BCE = BC, CE = AD; "" for none): ("BC", "AD") for "500 BC to 500 AD"."""
        lo = _ERA_CANON.get(self.era, self.era)
        hi = _ERA_CANON.get(self.era_hi, self.era_hi) if self.era_hi else lo
        return lo, (hi if self.hi else lo)

    @property
    def years(self) -> tuple[float, ...]:
        """The values on one time line: a BC end negative ("500 BC to 500 AD" = (-500, 500)); without an era, the
        values."""
        return tuple(-v if e == "BC" else v for v, e in zip(self.values, self.eras))

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
        a design change for the panel). An era-crossing span is written with each end's era ("500 BC–AD 500") when
        `era` is True; without its eras ("500–500") it is no longer the figure, so a surface that draws figures without
        eras (the stat slide) withholds it on its own truth check."""
        if era and self.era_hi:
            def end(x: str, e: str) -> str:
                return f"AD {x}" if e == "AD" else f"{x} {e}" if e else x
            n = f"{end(self.lo, self.era)}–{end(self.hi, self.era_hi)}"
            n = f"{n} {self.scale}" if self.scale else n
            return " ".join(x for x in (self.hedge, n, self.unit, self.tail) if x)
        pre = "AD" if era and self.era == "AD" else ""
        post = self.era if era and self.era not in ("", "AD") else ""
        return " ".join(x for x in (self.hedge, pre, self.quantity, self.tail, post) if x)

    def record(self) -> dict[str, Any]:
        return {"hedge": self.hedge, "number": self.number, "unit": self.unit, "span": bool(self.hi),
                "conversion": self.conv.quantity if self.conv else "", "tail": self.tail,
                "values": list(self.values), "era": self.era, "era_hi": self.era_hi, "drawn": self.drawn(era=False)}


_ERA_CANON = {"BCE": "BC", "CE": "AD"}


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


def _glued_scale(toks, j: int) -> str:
    """The scale word a letter glued to the number before token j gives (GLUED_SCALE), when a count unit follows it;
    else ""."""
    if j <= 0 or j >= len(toks) or toks[j][1] != toks[j - 1][2] or not _is_num(toks[j - 1][0]):
        return ""
    scale = GLUED_SCALE.get(toks[j][0].lower(), "")
    k = j + 1
    if scale and k < len(toks) and toks[k][0].lower() in COUNT_UNITS:
        return scale
    # a span's low end glued like its far end ("94M-104M years"): the far end's reading
    if (scale and k + 2 < len(toks) and toks[k][0].lower() in ("-", "–", "—", "to") and _is_num(toks[k + 1][0])
            and toks[k + 2][0].lower() == toks[j][0].lower()):
        return _glued_scale(toks, k + 2)
    return ""


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
    leads = [m.group(1)] if m and len(m.group(1).split()) <= 4 else []
    p = LEAD_HEDGE_PHRASE.match(sentence)     # the closed lead phrases, whatever punctuation follows them
    if p:
        leads.append(p.group("a") or p.group("b") or p.group("c") or "")
    for lead in leads:
        for stem, word in LEAD_HEDGES:
            if stem in lead.lower():
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
            if (nxt is None and _unit_at(toks, num[1]) is None and not scaled
                    and _count_noun_after(toks, num[1], tok) is None):
                i += 1
                continue
        fig, i = _figure_from(text, toks, i)
        out.append(fig)
    return out


COUNT_MODIFIERS_MAX = 2   # modifier words a count word may carry before its count noun ("three superimposed levels")


def _count_noun_after(toks, j: int, count: str) -> tuple[str, int, int] | None:
    """(the count unit as drawn, its token index, the index after it) when a count unit (COUNT_UNITS, never a
    measurement unit) follows token j across 1 .. COUNT_MODIFIERS_MAX modifier words; else None (freeze cycle 4, SYS:
    "three superimposed underground levels" signed no figure). Only after a spelled count word `count` of two or
    more: a digit before modifiers is as often a year ("in 2005 new levels ..."), and "one" before them an idiom
    ("one fine day"), not a count. A modifier is a plain word: never a closed-
    class word (an article, a preposition, a quantifier, a conjunction: brand_system.NEVER_END_WORDS, so "three of
    the levels" and "two more years" are not read), a number word, a scale word, a unit, a hedge or punctuation."""
    from .brand_system import NEVER_END_WORDS
    if NUMBER_WORDS.get(str(count).lower(), 0) < 2:
        return None
    k = j
    for _ in range(COUNT_MODIFIERS_MAX):
        if k >= len(toks):
            return None
        w = toks[k][0].lower()
        if (not w.isalpha() or w in NEVER_END_WORDS or w in NUMBER_WORDS or w in SCALE_WORDS or w in ERAS
                or _match_phrase(toks, k, _unit_tokens()) or _match_phrase(toks, k, HEDGES)
                or _match_phrase(toks, k, DIMENSION_WORDS)):
            return None
        k += 1
        m = _match_phrase(toks, k, COUNT_UNITS)
        if m and not (m[1] < len(toks) and toks[m[1]][0] == "²"):
            return _unit_drawn(m[0])[0], k, m[1]
    return None


def _number_after_sep(toks, j: int, between: bool = False) -> tuple[str, int, int, str] | None:
    """(separator, the number token's index, its index after, the era written before it) for "- 18" / "to 18" /
    "and 18" (and only after "between") at token j, or None. The far end may lead with its era ("500 BC to AD 500",
    "500 BC–AD 500": its era "AD")."""
    if j >= len(toks):
        return None
    sep = toks[j][0].lower()
    if sep in ("-", "–", "—", "to") or (between and sep == "and"):
        k = j + 1
        hb = _match_phrase(toks, k, HEDGES)   # "and roughly 138"
        if hb:
            k = hb[1]
        pre = ""
        if k + 1 < len(toks) and toks[k][0].lower() in ("ad", "ce") and _is_num(toks[k + 1][0]):
            pre, k = toks[k][0].upper(), k + 1
        if k < len(toks) and _is_num(toks[k][0]):
            return sep, k, k + 1, pre
    return None


def _figure_from(text: str, toks, i: int) -> tuple[Figure, int]:
    """The figure whose number starts at token i, and the token index after it."""
    hedge, h_start = "", None
    # the figure's lead starts before an era written ahead of its number ("between AD 500 and 500 BC", "about AD 43")
    lead_at = i - 1 if i > 0 and toks[i - 1][0].lower() in ("ad", "ce") else i
    hb = _hedge_before(toks, lead_at)
    between = False
    if hb:
        hedge, h_start = hb
        if h_start > 0 and toks[h_start - 1][0].lower() == "between":
            between = True
    elif lead_at > 0 and toks[lead_at - 1][0].lower() == "between":
        between, h_start = True, lead_at - 1
    if between and hb is None:
        h_start = lead_at - 1
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
    elif _glued_scale(toks, j):
        scale, gold[-1] = _glued_scale(toks, j), (gold[-1][0], toks[j][2])
        j += 1
    unit1 = _unit_at(toks, j)
    unit, measured = "", False
    if unit1:
        unit, u_after, measured = unit1
        gold[-1] = (gold[-1][0], toks[u_after - 1][2])
        j = u_after
    elif not _is_num(toks[i][0]) and not scale and not plus and (counted := _count_noun_after(toks, j, toks[i][0])):
        # a count word and its count noun across its modifiers ("three superimposed underground levels" = 3 LEVELS):
        # the number and the noun gold, the modifiers bone (ruling 8); the figure ends at the noun
        unit, u_at, u_after = counted
        gold.append((toks[u_at][1], toks[u_after - 1][2]))
        j = u_after
    conv1, j = _conversion(text, toks, j)
    era, era_hi, lo_suffix = "", "", False
    if i > 0 and toks[i - 1][0].lower() == "ad":            # "AD 43"
        era = "AD"
    if j < len(toks) and toks[j][0].lower() in ERAS:        # "205 BC to 87 BC", "9500 BCE"
        era, j, lo_suffix = toks[j][0].upper(), j + 1, True
    hi = ""
    sep = _number_after_sep(toks, j, between=between)
    if sep and not plus:
        s, k, after, pre = sep
        h_lo, h_hi = _value(lo), _value(toks[k][0])
        crossing = bool(pre and era and _ERA_CANON.get(pre, pre) != _ERA_CANON.get(era, era))
        if h_lo < h_hi or _counts_down(toks, after) or era in ("BC", "BCE") or crossing:
            hi = toks[k][0]
            if s in ("-", "–", "—") and not unit1 and not lo_suffix and not pre:
                gold[-1] = (gold[-1][0], toks[k][2])   # "137-138": one gold run with its dash
            else:
                gold.append((toks[k][1], toks[k][2]))   # an era between the ends is never gold
            j = after
            if j < len(toks) and toks[j][0].lower() in SCALE_WORDS and not scale:
                scale = toks[j][0].upper()
                gold[-1] = (gold[-1][0], toks[j][2])
                j += 1
            elif _glued_scale(toks, j) and (not scale or _glued_scale(toks, j) == scale):
                scale = _glued_scale(toks, j)
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
            far = pre
            if j < len(toks) and toks[j][0].lower() in ERAS:
                far, j = toks[j][0].upper(), j + 1
            # SYS-R8-1: each end keeps its own era ("between 500 BC and 500 AD" is BC 500 to AD 500, never "AD
            # 500-500"); one era after the far end alone is both ends' ("9500-9000 BCE"), as before
            if far and era and _ERA_CANON.get(far, far) != _ERA_CANON.get(era, era):
                era_hi = far
            elif far:
                era = far
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
                 end=end, gold=gold, lead_hedge=lead, measured=measured, era=era, era_hi=era_hi)
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
                "TON": "TONS", "TONNE": "TONNES", "SHORT TON": "TONS", "SHORT TONS": "TONS",
                "LONG TON": "LONG TONS", "METRIC TON": "TONNES", "METRIC TONS": "TONNES", "HOUR": "HOURS", "DAY": "DAYS", "YEAR": "YEARS",
                "CENTURY": "CENTURIES"}
    return singular.get(u, u)


def _era_ok(a: str, b: str) -> bool:
    """Two figures' eras agree (BC = BCE, AD = CE; a figure with no era agrees with any)."""
    canon = {"BCE": "BC", "CE": "AD"}
    return not a or not b or canon.get(a, a) == canon.get(b, b)


def _eras_ok(d: Figure, v: Figure) -> bool:
    """Two figures' eras agree end by end (an era-crossing span against itself, never against a one-era span)."""
    if len(d.values) != len(v.values) and (d.era_hi or v.era_hi):
        return False
    return all(_era_ok(a, b) for a, b in zip(d.eras, v.eras))


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
                 and _canon(v.unit) == _canon(d.unit) and _eras_ok(d, v)]
        exact = [(s, v) for s, v in views if v.values == d.values]
        if exact:
            if not d.hedge and all(s.hedge for s, _v in exact):
                faults.append(f"{d.drawn()!r}: the signed figure is hedged ({exact[0][0].hedge}) and the drawn one "
                              "is not")
            elif hedge_direction(d.hedge) and all(hedge_direction(s.hedge) == -hedge_direction(d.hedge)
                                                  for s, _v in exact):
                faults.append(f"{d.drawn()!r}: the drawn hedge ({d.hedge}) bounds the figure the other way from the "
                              f"signed one ({exact[0][0].hedge})")
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


# ------------------------------------------------------------------ derived figures (SYS-R8-1, cycle 0 round 8)
# A timeline's gap label ("53 YEARS LATER", "ABOUT 1,500 YEARS LATER") is DERIVED from the two signed years it spans:
# one rule, here, which the timeline renderers draw (graphics.gap_text) and the drawn-figure gate re-computes from
# the operands the renderers record (figure_gate), so a correct gap is never judged as a claim and a wrong one fails.
def years_between(a: int, b: int) -> int:
    """Calendar years from year `a` to year `b` (negative years are BC; there is no year 0)."""
    return (b - a) - (1 if a < 0 < b else 0)


def elapsed(a: int, b: int, approximate: bool) -> tuple[int, bool] | None:
    """The elapsed years a gap label states between years `a` and `b`, and whether it is hedged ("ABOUT"): exact
    when both dates are exact; when either is approximate, rounded to what the dates support (the nearest 100 from 500
    years, 10 from 50, else 1; never under one step). None under two years (no label)."""
    years = years_between(int(a), int(b))
    if years < 2:
        return None
    if approximate:
        step = 100 if years >= 500 else 10 if years >= 50 else 1
        return max(step, round(years / step) * step), True
    return years, False


def gap_record(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, Any] | None:
    """The layout record of a gap label's derivation: its rule and operands (the two entries' years and whether
    either is approximate) and the figure the rule gives; None when the rule draws no label."""
    approx = bool(a.get("approximate") or b.get("approximate"))
    got = elapsed(int(a["year"]), int(b["year"]), approx)
    if got is None:
        return None
    return {"rule": "elapsed_years", "from": int(a["year"]), "to": int(b["year"]), "approximate": approx,
            "years": got[0], "hedged": got[1]}


# ------------------------------------------------------------------ derived comparisons (freeze cycle 5, FIGURES)
# Lander ruling 2026-11-26. A figure no signed field holds (a scale's "ABOUT 181 M" between a 327 m mine and a 146 m
# pyramid, "about 2 times its length") is DERIVED and may be drawn only when: every operand is a signed figure (the
# run's signed fact, or the reference file's sourced landmark figure, named with its source); the operation is one of
# DERIVED_OPS; the result carries a hedge unless it is exact; its operands are labelled on the drawing (so the reader
# sees what the figure is the difference or the ratio of); and the surface's record stores the operands, the operation
# and the result (derivation). Otherwise it is withheld and the omission recorded.
# TRUTH (lander ruling 2026-11-26, second part): the arithmetic is done on the operands' SIGNED SOURCE values (the
# fact's own value, the reference file's figure in the drawing's unit, unrounded) and the result is rounded ONCE for
# display, with its hedge. Never on operands as already rounded for drawing: a 20 m well against a 60 ft (18.288 m)
# landmark labelled "18 M" is "ABOUT 1.7 M" below it, not 20 - 18 = "2.0". The drawn-figure gate re-computes the
# result from the record's source values (derivation_faults) and checks each against what it claims to be.
DERIVED_OPS = ("difference", "ratio", "sum")
DERIVED_RULE = ("a derived figure is drawn only from signed operands labelled on the drawing, by difference, ratio "
                "or sum, hedged unless exact, with its derivation recorded (lander ruling 2026-11-26)")


NEAR_RATIO = 1.1         # review 2 (N2): "nearly the same" is a derived claim: the ratio of the signed values under this
FT_IN = re.compile(r"^\s*(\d[\d,]*)\s*FT\s+(\d+)\s*IN\s*$", re.IGNORECASE)


def shown_value(shown: str) -> tuple[float, float] | None:
    """(value, its last place) of a drawn derived number: "181" -> (181, 1); "1.7" -> (1.7, 0.1); a feet-and-inches
    figure "10 FT 8 IN" -> (10.667, 1/12) in feet; None for no number ("" or a word form)."""
    raw = str(shown or "").replace(",", "").strip()
    m = FT_IN.match(raw)
    if m:
        return int(m.group(1)) + int(m.group(2)) / 12, 1 / 12
    try:
        v = float(raw)
    except ValueError:
        return None
    return v, 10 ** -len(raw.partition(".")[2])


def comparison_ratio(big: float, small: float) -> str:
    """How many times one figure is the other, never stated more confidently than the figures: one decimal below 2
    ("1.5"), else the whole number rounded half up (2.5 -> 3)."""
    ratio = big / small
    if ratio < 2:
        return f"{math.floor(ratio * 10 + 0.5) / 10:g}"
    return str(math.floor(ratio + 0.5))


def operand(role: str, name: str, text: str, value: float, unit: str, approximate: bool, source: str,
            **more: Any) -> dict[str, Any]:
    """One operand of a derivation: what it is (`role`, `name`), its figure as drawn (`text`), its SIGNED SOURCE
    `value` in `unit` (never the drawn rounding: the arithmetic is done on this), whether it is approximate (a hedged
    fact, or a figure drawn rounded or converted) and the signed field or sourced reference it is."""
    return {"role": role, "name": name, "text": text, "value": float(value), "unit": unit,
            "approximate": bool(approximate), "source": source, **more}


def rounded_once(shown: str, true: float) -> bool:
    """Whether the drawn number `shown` is `true` rounded once at shown's own precision (within half a unit of its
    last drawn place: "1.7" for 1.712, "180" for 180.39; never "2.0" for 1.712, nor "181" for 180.39)."""
    got = shown_value(shown)
    return got is not None and abs(got[0] - float(true)) <= 0.5 * got[1] + 1e-9


def derivation(rule: str, operands: Sequence[Mapping[str, Any]], shown: str, text: str,
               threshold: float | None = None) -> dict[str, Any]:
    """The record of a derived figure: its `rule` (DERIVED_OPS), its operands (operand), the exact result of the rule
    on the operands' signed source values, `shown` (the number as drawn: "181", "2", "" for a comparison that states none,
    "nearly the same height"), whether it is hedged (never exact unless every operand is exact and the drawn number
    is the result itself) and `text` (the words that state it on the surface: "ABOUT 181 M", "about 2 times").
    A word form that states no number ("NEARLY LEVEL", "nearly the same") is a derived claim too (review 2, N2): its
    `threshold` (a difference: the largest result it may state; a ratio: NEAR_RATIO) is recorded and re-checked."""
    vals = [float(o["value"]) for o in operands]
    if rule == "difference":
        result = abs(vals[0] - vals[1])
    elif rule == "sum":
        result = sum(vals)
    elif rule == "ratio":
        result = max(vals[0], vals[1]) / min(vals[0], vals[1])
    else:
        raise ValueError(f"not a derivation rule: {rule!r}")
    got = shown_value(shown)
    exact = (not any(o.get("approximate") for o in operands) and got is not None and abs(got[0] - result) < 1e-9)
    rec = {"rule": rule, "operands": [dict(o) for o in operands], "result": round(result, 6), "shown": shown,
           "hedged": not exact, "text": text}
    if not shown:
        rec["threshold"] = float(threshold if threshold is not None else NEAR_RATIO if rule == "ratio" else 0.0)
    return rec


def _is_number(text: str) -> bool:
    try:
        float(text.replace(",", ""))
    except ValueError:
        return False
    return True


HEDGE_WORDS = ("ABOUT", "NEARLY", "ROUGHLY", "AROUND", "ALMOST", "APPROXIMATELY", "OVER", "UNDER", "UP TO")


def derivation_faults(rec: Mapping[str, Any] | None, labelled: Iterable[str] | None = None) -> list[str]:
    """[] when a derived figure's record satisfies the ruling; else one line per breach. Structural and arithmetical:
    an allowed rule; two operands or more, each with a drawn text, a value, a unit and a source; the result
    re-computed from the operands' signed source values; every operand's drawn figure a statement of its source
    value (within CONVERT_TOL); the drawn number that result rounded once (rounded_once; a ratio by
    comparison_ratio), never the arithmetic of the figures as rounded for drawing; a hedge unless it is exact. With
    `labelled` (the roles whose figures the drawing labels), every operand's role must be among them."""
    if not isinstance(rec, Mapping):
        return ["a derived figure without a derivation record"]
    out = []
    rule, ops = rec.get("rule"), rec.get("operands") or []
    if rule not in DERIVED_OPS:
        return [f"its operation {rule!r} is not one of {', '.join(DERIVED_OPS)}"]
    if len(ops) < 2:
        return ["a derivation needs two operands"]
    for o in ops:
        missing = [k for k in ("text", "value", "unit", "source") if o.get(k) in (None, "")]
        if missing:
            out.append(f"its operand {o.get('name') or o.get('role')!r} carries no {', '.join(missing)}")
    if out:
        return out
    vals = [float(o["value"]) for o in ops]
    for o in ops:                    # the figure an operand is labelled with states its source value
        said = [v for f in parse(str(o["text"])) for v in f.values]
        if len(said) == 1 and abs(said[0] - float(o["value"])) > CONVERT_TOL * max(abs(float(o["value"])), 1e-9) + 1e-9:
            out.append(f"its operand {o.get('name') or o.get('role')!r} is drawn {o['text']!r} for a source value "
                       f"of {float(o['value']):g} {o['unit']}")
    want = (abs(vals[0] - vals[1]) if rule == "difference" else sum(vals) if rule == "sum"
            else max(vals[:2]) / min(vals[:2]))
    if abs(want - float(rec.get("result", float("nan")))) > 1e-4 * max(1.0, abs(want)):
        out.append(f"its recorded result {rec.get('result')} is not the {rule} of its operands ({want:g})")
    shown = str(rec.get("shown") or "")
    got = shown_value(shown)
    if shown and got is None:
        out.append(f"its drawn figure {shown!r} is not a number")
    elif got is not None:
        if rule == "ratio":
            if shown != comparison_ratio(max(vals[:2]), min(vals[:2])):
                out.append(f"its drawn ratio {shown} is not its operands' ({want:.2f})")
        elif not rounded_once(shown, want):
            out.append(f"its drawn figure {shown} is not the {rule} of its operands' signed values ({want:g}) "
                       "rounded once")
    else:                            # a word form ("NEARLY LEVEL", "nearly the same"): its stated threshold holds
        limit = rec.get("threshold")
        if not isinstance(limit, (int, float)) or isinstance(limit, bool):
            out.append(f"its word form {rec.get('text')!r} records no threshold")
        elif rule == "ratio" and not want < float(limit):
            out.append(f"it says {rec.get('text')!r} for a ratio of {want:.3f} (the form holds under {limit:g})")
        elif rule != "ratio" and not want <= float(limit) + 1e-9:
            out.append(f"it says {rec.get('text')!r} for a {rule} of {want:g} (the form holds to {limit:g})")
    exact = (not any(o.get("approximate") for o in ops) and got is not None and abs(got[0] - want) < 1e-9)
    text = str(rec.get("text") or "").upper()
    if not exact and not any(re.search(rf"\b{re.escape(h)}\b", text) for h in HEDGE_WORDS):
        out.append(f"its text {rec.get('text')!r} carries no hedge and it is not exact")
    if labelled is not None:
        have = set(labelled)
        for o in ops:
            if o.get("role") not in have:
                out.append(f"its operand {o.get('name') or o.get('role')!r} is not labelled on the drawing")
    return out


# ------------------------------------------------------------------ converted units (SYS-R8-1, cycle 0 round 8)
# A unit drawn as a conversion of a signed value ("4.6 M" under "15 FT") is drawn at the precision that keeps it
# within CONVERT_TOL of the signed value: the fewest decimals that do (whole metres drew "5 M" for 15 ft, 9 % over).
# The drawn-figure gate judges a conversion with this same tolerance and these same factors (figure_gate imports
# them), so the renderer's rule and the gate's can never disagree.
CONVERT_TOL = 0.02
LENGTH_PER_FT = {"FT": 1.0, "M": 3.28084, "KM": 3280.84, "MILES": 5280.0, "MI": 5280.0, "CM": 0.0328084,
                 "IN": 1 / 12, "YD": 3.0}
MASS_PER_LB = {"LB": 1.0, "KG": 2.20462, "TONNES": 2204.62, "TONS": 2000.0, "LONG TONS": 2240.0}   # TONS: short
# freeze SYS batch 3: areas convert within their kind too (a headline's "830 SQUARE MILES" for a signed 2,150 km²)
AREA_PER_SQFT = {"SQ FT": 1.0, "SQ M": 10.7639, "M²": 10.7639, "SQ KM": 10763910.4, "KM²": 10763910.4,
                 "SQ MI": 27878400.0, "ACRES": 43560.0, "HECTARES": 107639.1}


def in_base(value: float, unit: str) -> tuple[str, float] | None:
    """(dimension, the value in feet or pounds) for a measured unit, or None."""
    u = _canon(unit)
    if u in LENGTH_PER_FT:
        return "length", value * LENGTH_PER_FT[u]
    if u in MASS_PER_LB:
        return "mass", value * MASS_PER_LB[u]
    if u in AREA_PER_SQFT:
        return "area", value * AREA_PER_SQFT[u]
    return None


def within_tol(drawn: float, drawn_unit: str, signed: float, signed_unit: str) -> bool:
    """Whether `drawn` in `drawn_unit` states the signed value `signed` in `signed_unit` within CONVERT_TOL (one
    dimension, the error relative to the signed value)."""
    x, y = in_base(drawn, drawn_unit), in_base(signed, signed_unit)
    return bool(x and y and x[0] == y[0] and abs(x[1] - y[1]) <= CONVERT_TOL * max(abs(y[1]), 1e-9) + 1e-9)


def converted_number(value: float, unit: str, to: str, max_decimals: int = 6) -> str:
    """`value` in `unit` written in the unit `to` ("15", "FT", "M" -> "4.6"; "140" -> "43"), with thousands commas, at
    the fewest decimals whose figure is within CONVERT_TOL of the value (within_tol: the gate's own test)."""
    a, b = in_base(1.0, unit), in_base(1.0, to)
    if not a or not b or a[0] != b[0]:
        raise ValueError(f"no conversion from {unit!r} to {to!r}")
    true = value * a[1] / b[1]
    text = f"{true:,.{max_decimals}f}"
    for dp in range(max_decimals + 1):
        text = f"{true:,.{dp}f}"
        if within_tol(float(text.replace(",", "")), to, value, unit):
            break
    return "0" if float(text.replace(",", "")) == 0 else text


def converted_numbers(values, unit: str, to: str, max_decimals: int = 6) -> list[str]:
    """Every member of `values` (in `unit`) written in `to` at ONE decimal count (freeze SYS-A-1: one precision per
    drawing column, TY6): the least count that puts every member within CONVERT_TOL of its value (within_tol, the
    gate's own test, applied to the set), so a column reads 4.6 / 10.7 / 42.7 / 85.3, or all whole where the
    tolerance allows. A one-member set is converted_number's."""
    vals = [float(v) for v in values]
    a, b = in_base(1.0, unit), in_base(1.0, to)
    if not a or not b or a[0] != b[0]:
        raise ValueError(f"no conversion from {unit!r} to {to!r}")
    trues = [v * a[1] / b[1] for v in vals]
    dp = max_decimals
    for d in range(max_decimals + 1):
        if all(within_tol(float(f"{t:.{d}f}"), to, v, unit) for t, v in zip(trues, vals)):
            dp = d
            break
    out = []
    for t in trues:
        text = f"{t:,.{dp}f}"
        out.append("0" if float(text.replace(",", "")) == 0 else text)
    return out


def signed_number(value: float | str, signed: str | None = None) -> str:
    """A signed value written as drawn, at its SIGNED precision, with thousands commas (freeze cycle 3, SYS-S1-5 and
    stress item 5: the cross-section drew a signed 171.5 ft as '172 FT' and 1,447.9 ft as '1,448 FT', and the gate
    failed the run). The rule: drawn == signed, a signed decimal is never rounded on a drawing. `signed`: the value as
    the copy writes it ("171.50", "1447.9"), whose decimal count is kept (a trailing zero the copy signs stays);
    else the value's own shortest decimals ("6.5", "1,073", "11.25")."""
    if signed is not None and re.fullmatch(r"\s*-?[\d,]*\.?\d+\s*", str(signed)):
        raw = str(signed).strip().replace(",", "")
        dp = len(raw.partition(".")[2])
    else:
        raw = f"{float(value):.6f}".rstrip("0").rstrip(".")
        dp = len(raw.partition(".")[2])
    return f"{float(value):,.{dp}f}"


def stated_at(text: str, pos: int) -> Figure | None:
    """The whole stated quantity of `text` that covers character `pos` (its hedge, both span ends, its scale, unit and
    conversion), else None (freeze cycle 3, SYS-B-4: one accessor, so the cover's monument figure, the YouTube figure
    branch and the hook read a statement the same way, never one number lifted out of it: '138 FT' for 'ROUGHLY
    137–138 FT', '900 YEARS' for 'MORE THAN 900 YEARS')."""
    for f in parse(text or ""):
        if f.start <= pos < max(f.end, f.start + 1):
            return f
    return None
