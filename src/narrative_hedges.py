"""The hedge rule's lexical parts (QF-16): one quantity matcher, the uncertainty markers, the automatic hedge's scope.

Operator ruling 2026-11-11 (decisions/QF-16-question.md, under delegation: 1 = B, 2 = P3, 3 = body qualifiers free):
- The fact sheet's automatic hedge ("by one estimate, ..." / "by one account, ...") is kept only for a weak figure that
  is a measured quantity (a number with a unit) or a date, in the statement's own words (saying a quoted range instead
  is deferred to proposed QF-112). A weak count or event figure gets no automatic hedge (and is dropped unless the
  sheet's writer authored one).
- In the hook, the payoff and the final loop, a disputed fact from the run's own sheet blocks after the one shared
  repair edit; any other uncertainty wording there gets that edit and then ships flagged. A question is exempt.
- Qualifiers in the body are the writer's: neither blocked nor flagged.

A lexical floor (word forms and positions), not a judge of truth: the fact checks still decide what is supported.
"""

import re

_DIGITS = r"\d[\d,]*(?:\.\d+)?"
_UNITS_1 = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
            "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_SMALL = {w: i for i, w in enumerate(_UNITS_1)} | _TENS
_SCALES = {"hundred": 100, "thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}
# Quantity words that are not a single value: a multiple or a vague magnitude ("twice as often", "dozens of statues").
_MULTIPLES = {"twice", "thrice", "half", "double", "triple", "dozen", "dozens", "hundreds", "thousands", "millions",
              "billions", "several", "a few", "one-third", "one-quarter", "one-half", "two-thirds", "three-quarters"}
_WORD = "|".join(sorted(set(_SMALL) | set(_SCALES), key=len, reverse=True))
_SPELLED = re.compile(rf"\b(?:{_WORD})(?:[\s-]+(?:and\s+)?(?:{_WORD}))*\b", re.IGNORECASE)
_MULTIPLE = re.compile(r"\b(?:" + "|".join(re.escape(m) for m in sorted(_MULTIPLES, key=len, reverse=True)) + r")\b",
                       re.IGNORECASE)
_QUANTITY = re.compile(rf"(?:{_DIGITS}|\b(?:{_WORD})\b|{_MULTIPLE.pattern})", re.IGNORECASE)


def _spelled_value(text: str) -> float | None:
    """'one hundred forty-four' -> 144, 'thirty-five' -> 35, 'two thousand' -> 2000; None if it is not a number."""
    total, current, seen = 0, 0, False
    for word in re.split(r"[\s-]+", text.lower()):
        if word == "and" or not word:
            continue
        if word in _SMALL:
            current += _SMALL[word]
            seen = True
        elif word in _SCALES:
            current = max(current, 1) * _SCALES[word]
            if _SCALES[word] >= 1000:
                total, current = total + current, 0
            seen = True
        else:
            return None
    return float(total + current) if seen else None


def values(text: str) -> list[float]:
    """Every single value the text states, in digits ('20,000', '1.8') or in words ('one hundred forty-four')."""
    text = text or ""
    out = [float(m.replace(",", "")) for m in re.findall(_DIGITS, text)]
    for m in _SPELLED.finditer(text):
        v = _spelled_value(m.group())
        if v is not None:
            out.append(v)
    return out


def has_quantity(text: str) -> bool:
    """Whether the text states a quantity: digits, a spelled number, or a multiple ('twice', 'dozens of')."""
    return bool(_QUANTITY.search(text or ""))


def starts_with_quantity(text: str, within: int = 3) -> bool:
    """Whether a quantity comes within the first `within` words (an approximation qualifies the figure after it:
    'roughly 280 feet', 'an estimated thirty-five gears', 'nearly one-third larger')."""
    head = " ".join((text or "").split()[:within])
    return has_quantity(head)


# ---------------------------------------------------------------- the automatic hedge's scope (ruling part 1, B)
_RANGE = re.compile(rf"\b(?:between\s+({_DIGITS})\s+and\s+({_DIGITS})|from\s+({_DIGITS})\s+to\s+({_DIGITS})|"
                    rf"({_DIGITS})\s*(?:to|[–—-])\s*({_DIGITS}))", re.IGNORECASE)
# A unit of measure after a figure (length, area, volume, mass, angle, share, energy, pressure, time). Generic units,
# never a topic's words. A bare count ("four chapels", "107 inscriptions") is not a measured quantity.
_UNIT_NAME = (
    r"(?:square\s+|cubic\s+)?(?:m|cm|mm|km|nm|ft|mi|kg|lb|lbs|ha|°|%|"
    r"metres?|meters?|kilometres?|kilometers?|centimetres?|centimeters?|millimetres?|millimeters?|nanometres?|"
    r"nanometers?|micrometres?|micrometers?|feet|foot|inch(?:es)?|miles?|yards?|tonnes?|tons?|kilograms?|grams?|"
    r"pounds?|degrees?|percent|per\s*cent|megapascals?|megatons?|kilotons?|hectares?|acres?|liters?|litres?|"
    r"gallons?|years?|days?|hours?|minutes?|seconds?|centuries|millennia|parts\s+per)(?![A-Za-z])")
_UNIT = re.compile(rf"{_DIGITS}(?:\s*-\s*|\s+)?{_UNIT_NAME}", re.IGNORECASE)


def ranges(text: str) -> set[tuple[float, float]]:
    """Explicit digit ranges ('between 60 and 85', '62 to 67', '70–60'); unrelated numbers never make one."""
    out = set()
    for m in _RANGE.finditer(text or ""):
        v = [float(x.replace(",", "")) for x in m.groups() if x is not None]
        if len(v) == 2 and v[0] != v[1]:
            out.add((v[0], v[1]))
    return out


def measured(statement: str) -> bool:
    """Whether the statement gives a measured quantity: a figure followed by a unit of measure."""
    return bool(_UNIT.search(statement or ""))


def auto_hedge_allowed(statement: str, kind: str) -> bool:
    """The automatic hedge's scope (ruling B): a date, or a number that is a measured quantity."""
    return kind == "date" or (kind == "number" and measured(statement))


def with_quoted_range(statement: str, evidence: list[dict], kind: str = "number") -> str:
    """The statement as the automatic hedge says it: unchanged, always (lander's decision after review 3, round 4).

    Saying a quoted range in place of the statement's figure is deferred to proposed QF-112: rounds 3 and 4 both
    produced wrong or false text (a date or lifespan made a range, a page range taken, a bound such as "up to" or
    "more than" kept before the range, a hyphenated "85-meter" broken, a range of another dimension adopted). The
    honest "by one estimate, <statement>" stays, in the statement's own words."""
    return statement


# ---------------------------------------------------------------- uncertainty wording (ruling part 2, P3)
# Approximations qualify the figure that follows them; alone ("around the city", "about the temple", "some still
# stand") they are not hedges.
_APPROX = re.compile(r"\b(?:approximately|roughly|circa|nearly|almost|about|around|some|up to|an estimated|"
                     r"estimated|perhaps as (?:many|much) as)\b", re.IGNORECASE)
# Uncertainty about the claim itself, wherever it stands.
_UNCERTAIN = re.compile(
    r"\b(?:by one (?:estimate|account)|reportedly|allegedly|possibly|perhaps|probably|(?:most |more )?likely|"
    r"apparently|presumably|seemingly|arguably|maybe|seem(?:s|ed)? to|(?:is|are|was|were) (?:believed|thought|said) to|"
    r"may (?:have|be|date)|might(?: have| be)?|could have|suggests?|suggested|estimates?)\b", re.IGNORECASE)
AUTO_LEAD = re.compile(r"\bby one (?:estimate|account)\b", re.IGNORECASE)


def markers(sentence: str) -> list[tuple[str, bool]]:
    """The sentence's hedge wording, each with whether it qualifies a quantity (the one quantity matcher: digits,
    spelled numbers, multiples). 'By one estimate' qualifies a quantity anywhere in the rest of its sentence."""
    found = []
    for m in _APPROX.finditer(sentence or ""):
        if starts_with_quantity(sentence[m.end():]):
            found.append((m.group(), True))
    for m in _UNCERTAIN.finditer(sentence or ""):
        rest = sentence[m.end():]
        quantitative = has_quantity(rest) if AUTO_LEAD.fullmatch(m.group()) else starts_with_quantity(rest)
        found.append((m.group(), quantitative))
    return found


def is_question(sentence: str) -> bool:
    return (sentence or "").rstrip(" \"'”’)").endswith("?")


def _distinctive(value: float) -> bool:
    """A value specific enough to tie a sentence to one fact: not a small whole count ('one', 'two', 'five')."""
    return value >= 10 or value != int(value)


def disputed_figures(sentence: str, sheet: dict | None) -> list[float]:
    """The sentence's figures that only a disputed fact of the sheet states (in its statement or its hedge; metric
    and imperial conversions count), never one an undisputed fact also states."""
    from .fact_sheet import _number_supported
    facts = [f for f in ((sheet or {}).get("facts") or []) if isinstance(f, dict)]
    disputed = [v for f in facts if f.get("disputed")
                for v in values(f.get("statement", "") + " " + f.get("hedge", ""))]
    plain = [v for f in facts if not f.get("disputed") for v in values(f.get("statement", ""))]
    return [v for v in values(sentence) if _distinctive(v) and _number_supported(v, disputed)
            and not _number_supported(v, plain)]


def carries_disputed(sentence: str, sheet: dict | None) -> str:
    """Why the sentence states a disputed fact of the sheet ('' when it does not): the automatic hedge's own lead, or
    a figure only a disputed fact states."""
    lead = AUTO_LEAD.search(sentence or "")
    if lead:
        return f'"{lead.group()}"'
    figures = disputed_figures(sentence, sheet)
    return f"the disputed figure {figures[0]:,.10g}" if figures else ""
