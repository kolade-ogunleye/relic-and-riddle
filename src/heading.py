"""
src/heading.py
==============
The one heading-break engine (design-freeze cycle 0 round 7, preview panel F1, F5, F10, F18, F21). Every renderer
that breaks a heading or a display line sets it here: the carousel (covers and interiors), the Story frames, the Reel
covers, the YouTube thumbnail and the hook / chapter / myth / quote cards (src/design_cards.py `_wrap`, display type);
caption chunking (src/video_composer.py) reads the same never-end vocabulary for its burst ends.

The rules (src/brand_system.py "Heading rules" and "Accent"; operator rulings FREEZE-PARK round 4 (3)):

- NEVER: no line but the last ends on an article or a preposition (brand_system.LINE_END_NEVER: the articles, of,
  in, from, near, to, at, on, by, with, ...). Hard: such a break is never chosen, at any size. A two-line break goes
  BEFORE the stop word: THE EYE / OF THE SAHARA, never THE EYE OF / THE SAHARA.
- LAST RESORT: a conjunction end (LINE_END_LAST_RESORT) only when no break without one fits.
- A value never splits from its unit ("25+ MILES", "1,073 FT"); a break comma is dropped (the break is the pause).
- No lone-word row: in a heading of three words or more, the last row (and a middle row of a three-row form) holds
  two words or more ("SEEN FROM / SPACE" is refused; LINES / IN THE SAND is a phrase, kept).
- Balance: a two-line form prefers its shorter line >= heading.balance_min (0.55) of the longer.
- Ladder, never a continuous fit: a heading is tried at its role's size, then at most ONE recorded fallback tier
  (the caller passes the ladder; brand_system.TYPE / TYPE_FALLBACK); the order is one line at the size, then one
  line at the fallback tier, then a balanced break at the size, then at the fallback, then any valid break at the
  size, then at the fallback (a heading keeps its one line while the ladder holds it: two values, never a fit).
  A cover passes one size only (display_xl is locked: the break and the band give way, never the cover size).

Accent (F5, decided in round 7; brand_system "Accent"): the payoff run is gold on a LOCKUP (the carousel cover, the
Reel cover, the YouTube headline, a Story title: the surfaces whose copy states a payoff), on one line or two: the
words from the accent seam on (an authored line1 / line2 seam, else the break the engine would make for two lines),
less the function words that open it, which stay bone (OF THE SAHARA: SAHARA gold). An INTERIOR heading (a carousel
content slide) is never gold, wrapped or not; only a stated quantity in it is (operator ruling 3: Anton gold).
Reason: a payoff is a property of the copy, never of the wrap; gold on the cover lockup only keeps the deck's one
display gold read where the copy states it, and bone carries the most contrast on every interior ground (trade-off
order: feed legibility > one system > hierarchy).
"""
from __future__ import annotations

import itertools
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from . import brand_system

PLAIN, LEAD, ACCENT, FIGURE = "plain", "lead", "accent", "figure"
GOLD_ROLES = frozenset((ACCENT, FIGURE))
_QUOTES = "\"'‘’“”"
_CLOSE = ".,;:!?—–)"


def bare(word: str) -> str:
    return str(word or "").strip().lower().strip(_QUOTES).rstrip(_CLOSE).strip(_QUOTES)


def never_end() -> frozenset:
    """The hard never-end set (the system's one vocabulary)."""
    return brand_system.LINE_END_NEVER


def end_ok(word: str, last_resort: bool = True) -> bool:
    """Whether a line other than the last may end on `word` (punctuation after it closes a phrase)."""
    return brand_system.line_end_ok(word, last_resort=last_resort)


def is_function(word: str) -> bool:
    """A function word: one of the never-end words or the conjunctions. "OR" opening an alternative may stay gold."""
    w = bare(word)
    return w != "or" and (w in brand_system.LINE_END_NEVER or w in brand_system.LINE_END_LAST_RESORT)


def _figure_words(words: Sequence[str]) -> set[int]:
    """Indices of the words of a stated quantity: a number, its "+" and its unit ("25+ MILES", "1,073 FT", "1963")."""
    from .typography import _FIGURE
    out = {i for i, w in enumerate(words) if any(c.isdigit() for c in w)}
    text, starts, k = "", [], 0
    for w in words:
        starts.append(k)
        text += w + " "
        k += len(w) + 1
    for m in _FIGURE.finditer(text):
        out |= {i for i, s in enumerate(starts) if m.start() <= s < m.end()}
    return out


def _bonded(words: Sequence[str]) -> set[int]:
    """Break positions k (between words k-1 and k) inside a stated quantity: never a break."""
    fig = _figure_words(words)
    out = set()
    for k in range(1, len(words)):
        if k - 1 in fig and k in fig and not any(c.isdigit() for c in words[k]):   # a number and its unit
            out.add(k)
        if any(c.isdigit() for c in words[k]) and bare(words[k - 1]) in ("about", "over", "nearly", "almost",
                                                                         "some", "roughly", "around", "~"):
            out.add(k)   # a hedge stays with its figure
    return out


def break_points(words: Sequence[str], last_resort: bool = True) -> list[int]:
    """Every k (1 .. n-1) where a line may end after word k-1."""
    bonded = _bonded(words)
    return [k for k in range(1, len(words)) if k not in bonded and end_ok(words[k - 1], last_resort)]


def _lone_row(spans: Sequence[tuple[int, int]], n: int) -> bool:
    if len(spans) < 2 or n < 3:
        return False
    if spans[-1][1] - spans[-1][0] < 2:
        return True
    return any(b - a < 2 for a, b in spans[1:-1])


def _drop_comma(line: str) -> str:
    return line[:-1].rstrip() if line.endswith(",") else line


def lines_of(words: Sequence[str], cuts: Sequence[int]) -> tuple[str, ...]:
    bounds = (0, *cuts, len(words))
    out = [" ".join(words[a:b]) for a, b in itertools.pairwise(bounds)]
    return tuple(_drop_comma(t) if i < len(out) - 1 else t for i, t in enumerate(out))


def valid(lines: Sequence[str], lone_ok: bool = False) -> bool:
    """A drawn form obeys the engine's hard rules: no line but the last ends on a never word; no lone-word row (unless
    `lone_ok`: a locked cover's last resort, where the break gives way before the size does)."""
    words = " ".join(lines).split()
    spans, k = [], 0
    for ln in lines:
        m = len(ln.split())
        spans.append((k, k + m))
        k += m
    if any(not ln.split() for ln in lines):
        return False
    if any(not end_ok(ln.split()[-1]) for ln in lines[:-1]):
        return False
    bonded = _bonded(words)
    if any(b in bonded for _, b in spans[:-1]):
        return False
    return lone_ok or not _lone_row(spans, len(words))


@dataclass
class Form:
    """A heading as the engine set it: its lines, the size and ladder tier it set at, and the accent seam (the word
    index the payoff run begins at; None: no payoff run)."""
    words: tuple[str, ...]
    cuts: tuple[int, ...]
    size: float
    tier: int
    seam: int | None = None
    lockup: bool = False
    record: dict = field(default_factory=dict)

    @property
    def lines(self) -> tuple[str, ...]:
        return lines_of(self.words, self.cuts)

    def runs(self) -> list[list[tuple[str, str]]]:
        """Per line, the runs to draw: [(text, role)], role PLAIN / LEAD (bone) or ACCENT / FIGURE (gold)."""
        return runs(self.words, self.cuts, self.seam if self.lockup else None)

    def as_record(self) -> dict:
        return {"lines": list(self.lines), "size": self.size, "tier": self.tier, "seam": self.seam,
                "lockup": self.lockup, **self.record}


def roles(words: Sequence[str], seam: int | None) -> list[str]:
    """Each word's role: before the seam PLAIN; from it, the opening function words LEAD, the rest ACCENT; a stated
    quantity FIGURE wherever it stands (ruling 3)."""
    out = [PLAIN] * len(words)
    if seam is not None and 0 <= seam < len(words):
        k = seam
        while k < len(words) and is_function(words[k]):
            out[k] = LEAD
            k += 1
        if k < len(words):
            for i in range(k, len(words)):
                out[i] = ACCENT
        else:   # the run is function words only: no payoff
            for i in range(seam, len(words)):
                out[i] = PLAIN
    for i in _figure_words(words):
        out[i] = FIGURE
    return out


def runs(words: Sequence[str], cuts: Sequence[int], seam: int | None) -> list[list[tuple[str, str]]]:
    rl = roles(words, seam)
    gold = [r in GOLD_ROLES for r in rl]
    lines = lines_of(words, cuts)
    out = []
    for (a, b), text in zip(itertools.pairwise((0, *cuts, len(words))), lines):
        line_words = text.split()
        cur: list[tuple[str, str]] = []
        for j, w in enumerate(line_words):
            role = ACCENT if gold[a + j] and rl[a + j] == ACCENT else (FIGURE if gold[a + j] else
                                                                        (LEAD if rl[a + j] == LEAD else PLAIN))
            if cur and (cur[-1][1] in GOLD_ROLES) == (role in GOLD_ROLES):
                cur[-1] = (cur[-1][0] + " " + w, cur[-1][1])
            else:
                cur.append((w, role))
        out.append(cur)
    return out


def ideal_seam(words: Sequence[str]) -> int | None:
    """The payoff seam of a one-field heading: the two-line break the engine prefers regardless of width (valid,
    no conjunction end where another exists, then the most even in characters)."""
    n = len(words)
    if n < 2:
        return None
    best = None
    for k in break_points(words):
        cand = lines_of(words, (k,))
        if not valid(cand, lone_ok=True):   # the payoff may be one word ("THE SECRET / WEAPON": WEAPON gold)
            continue
        conj = bare(words[k - 1]) in brand_system.LINE_END_LAST_RESORT
        a, b = len(cand[0]), len(cand[1])
        score = (conj, not valid(cand), -min(a, b) / max(a, b))
        if best is None or score < best[0]:
            best = (score, k)
    return best[1] if best else None


def authored_seam(line1: str, line2: str) -> int | None:
    """A two-field lockup's seam (line1 / line2), with line1's closing function words moved to open line 2 (they then
    stay bone): "CARVED FROM" / "SOLID SALT" -> seam after CARVED."""
    w1 = line1.split()
    if not w1 or not line2.split():
        return None
    k = len(w1)
    while k > 0 and not end_ok(w1[k - 1]):
        k -= 1
    return k if k > 0 else None


_ISSUED: list = []   # instrumentation hook (tests): every Form the engine hands out


def _issue(form: Form) -> Form:
    for hook in tuple(_ISSUED):
        hook(form)
    return form


def _choose(words, cut_sets, fits, balance_min, seam_pref, strict, measure, lone_ok=False):
    best = None
    for cuts in cut_sets:
        lines = lines_of(words, cuts)
        if not fits(lines) or not valid(lines, lone_ok):
            continue
        conj = sum(bare(words[k - 1]) in brand_system.LINE_END_LAST_RESORT
                   and not lines[i].endswith(tuple(_CLOSE)) for i, k in enumerate(cuts))
        lens = [measure(t) for t in lines]
        bal = min(lens) / max(lens) if len(lens) > 1 and max(lens) > 0 else 1.0
        if strict and cuts and (conj or bal < balance_min):
            continue
        score = (0 if seam_pref is None or seam_pref in cuts else 1, len(cuts), conj, -bal)
        if best is None or score < best[0]:
            best = (score, cuts, conj, bal)
    return best


def set_heading(text: str, measure: Callable[[str, float], float], width: float, sizes: Sequence[float],
                max_lines: int = 2, seam: int | None = None, lockup: bool = False,
                balance_min: float | None = None, locked_lines: int = 0) -> Form | None:
    """The heading `text` set on the ladder `sizes` (first size, then at most the recorded fallback): None when no
    valid form fits `width` at any of them. `measure(line, size)` is a line's drawn width at `size`. `seam`: an
    authored line1 / line2 seam (word index) the break prefers; `lockup`: the payoff run is gold (else the heading is
    bone but for its stated quantities). `locked_lines`: a size-locked cover's last resort: up to this many lines at
    the (one) size, a lone-word row then allowed (the break and the band give way, never the size); recorded."""
    words = tuple(" ".join(str(text or "").split()).split())
    if not words:
        return None
    balance_min = brand_system.SPEC["heading.balance_min"] if balance_min is None else balance_min
    points = break_points(words)
    multi = [c for n in range(1, max_lines) for c in itertools.combinations(points, n)]

    def fits_at(size):
        return lambda lines: all(measure(t, size) <= width for t in lines)

    stages = []
    for tier, size in enumerate(sizes):
        stages.append((tier, size, [()], 0.0, True))
    for tier, size in enumerate(sizes):
        stages.append((tier, size, multi, balance_min, True))
    for tier, size in enumerate(sizes):
        stages.append((tier, size, multi, 0.0, False))
    if locked_lines > max_lines:
        more = [c for n in range(1, locked_lines) for c in itertools.combinations(points, n)]
        stages.append((0, sizes[0], more, 0.0, False))
    for tier, size, cut_sets, bmin, strict in stages:
        lone_ok = cut_sets is not multi and cut_sets != [()]
        got = _choose(words, cut_sets, fits_at(size), bmin, seam, strict, lambda t, s=size: measure(t, s), lone_ok)
        if got is None:
            continue
        _, cuts, conj, bal = got
        acc = seam if seam is not None else (cuts[-1] if cuts else ideal_seam(words))
        return _issue(Form(words, tuple(cuts), size, tier, acc, lockup,
                           {"balance": round(bal, 3), "conjunction_end": bool(conj),
                            **({"locked_last_resort": True} if lone_ok else {})}))
    return None


def lockup_fields(line1: str, line2: str) -> tuple[str, str, int]:
    """A two-field lockup (line1 bone / line2 payoff) with the authored seam repaired: line1's closing function words
    open line 2 instead. Returns (line1, line2, lead) where `lead` counts line 2's opening function words (bone)."""
    w1, w2 = line1.split(), line2.split()
    if not w1 or not w2:
        text = " ".join(w1 + w2)
        return text, "", 0
    k = authored_seam(line1, line2)
    if k is None:   # line1 is all function words: one field
        return " ".join(w1 + w2), "", 0
    l1, l2 = w1[:k], w1[k:] + w2
    lead = 0
    while lead < len(l2) and is_function(l2[lead]):
        lead += 1
    return " ".join(l1), " ".join(l2), lead


def set_field(text: str, measure: Callable[[str, float], float], width: float, size: float, max_lines: int = 2,
              seam: int | None = None, lockup: bool = False) -> Form | None:
    """One field of a lockup at one size: one line, else a valid break (no balance demand: the field is the copy's
    own phrase). None when it does not set at `size`."""
    return set_heading(text, measure, width, (size,), max_lines=max_lines, seam=seam, lockup=lockup, balance_min=0.0)


def field_runs(text: str, cuts: Sequence[int], gold: bool) -> list[list[tuple[str, str]]]:
    """A lockup field's runs: the payoff field (gold) keeps its opening function words bone (LEAD)."""
    words = text.split()
    return runs(words, cuts, 0 if gold else None)


def check_drawn(lines: Sequence[str]) -> bool:
    """For a renderer's post-draw record: the drawn lines obey the engine's hard rules."""
    return valid([t for t in lines if t.strip()]) if len([t for t in lines if t.strip()]) > 1 else True

