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
  (the caller passes the ladder; brand_system.TYPE / TYPE_FALLBACK); the order is one line at the size, then a
  balanced break at the size, then one line at the fallback tier, then a balanced break there, then any valid break
  at the size, then at the fallback (SYS-D-5, lander ruling c: the design size before the fallback tier; two values,
  never a fit). A carousel deck then sets its interior headings at ONE tier (multi_format._deck_heading_tier): the
  fallback applies deck-wide, never to one slide.
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
    """Indices of the words of a stated quantity: a number, its "+", its scale word and its unit ("25+ MILES",
    "1,073 FT", "20,000 PEOPLE", "1963"). Cycle 0 round 8 (ruling 8): the one gold-scope rule, src/figures.py."""
    from .figures import gold_word_indices
    return gold_word_indices(words)


def _qualifier_words(words: Sequence[str]) -> set[int]:
    """Indices of a stated quantity's hedge and dimension words (ABOUT, UP TO, TALL, ACROSS): bone (ruling 8)."""
    from .figures import qualifier_word_indices
    return qualifier_word_indices(words)


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


# Cycle 0 round 8 (operator ruling 4): the phrase exception to the no-lone-row rule. A one-word LAST row may stand when
# it is a complete phrase of its own and the break's balance is >= heading.balance_min (0.55): IMPACT THEORY /
# DISPROVED (0.69) beats IMPACT / THEORY DISPROVED (0.40, a split compound noun). A complete phrase is a predicate
# (a past participle: DISPROVED, FOUND, LOST, BURIED), a row the line before closes with punctuation ("THE TRUTH: /
# REVEALED"), or a lockup's payoff run (the seam the copy states its payoff from: THE SECRET / WEAPON). A noun left
# alone under its modifiers is not one ("SEEN FROM / SPACE" stays refused). Middle rows keep the rule.
_PARTICIPLES = frozenset((
    "found", "lost", "built", "made", "sunk", "hidden", "broken", "frozen", "stolen", "forgotten", "risen", "fallen",
    "drawn", "gone", "won", "done", "known", "seen", "shown", "thrown", "torn", "worn", "born", "begun", "sung", "hung",
    "struck", "split", "cast", "set", "kept", "left", "held", "sold", "told", "bound", "ground", "dug", "spun", "swept",
    "unseen", "unknown", "undone", "untold", "overthrown", "rewritten", "written", "taken", "given", "beaten"))
_NOT_PARTICIPLES = frozenset(("hundred", "sacred", "naked", "wicked", "kindred", "rugged", "jagged", "ragged", "red",
                              "bed", "shed", "seed", "need", "speed", "greed", "breed", "deed", "steed", "weed", "feed",
                              "creed", "bleed", "tweed", "led", "fled", "wed", "sled", "bred", "fed", "naked",
                              "beloved", "learned", "aged", "blessed", "crooked", "wretched", "dogged", "hatred"))


# a stated quantity's dimension word completes it ("ABOUT 40 KM / ACROSS"): a phrase of its own after a figure
_DIMENSIONS = frozenset(("across", "wide", "tall", "deep", "long", "high", "thick", "old", "away", "heavy", "around"))


def predicate(word: str, after: str = "") -> bool:
    """A word that completes a phrase on its own (a past participle; a dimension word after a figure), for the phrase
    exception."""
    w = bare(word)
    if w in _DIMENSIONS and any(c.isdigit() for c in after):
        return True
    if not w.isalpha() or w in _NOT_PARTICIPLES:
        return False
    return w in _PARTICIPLES or (len(w) >= 5 and w.endswith("ed"))


def phrase_row(lines: Sequence[str], widths: Sequence[float] | None = None, seam: int | None = None) -> bool:
    """Whether a lone last row of `lines` stands under the phrase exception (ruling 4): the row is a complete phrase
    (a predicate, a row the line before closes with punctuation, or the lockup payoff from word `seam`) and the two
    last lines balance >= heading.balance_min (measured `widths`, else characters)."""
    if len(lines) < 2:
        return False
    last = lines[-1].split()
    if len(last) != 1:
        return False
    before = lines[-2].rstrip()
    start = sum(len(ln.split()) for ln in lines[:-1])
    prev = lines[-2].split()
    if not (predicate(last[0], " ".join(prev[-2:])) or before.endswith(tuple(_CLOSE))
            or (seam is not None and seam == start)):
        return False
    ws = list(widths[-2:]) if widths is not None and len(widths) >= 2 else [len(lines[-2]), len(lines[-1])]
    return max(ws) > 0 and min(ws) / max(ws) >= brand_system.SPEC["heading.balance_min"]


def _drop_comma(line: str) -> str:
    return line[:-1].rstrip() if line.endswith(",") else line


def lines_of(words: Sequence[str], cuts: Sequence[int]) -> tuple[str, ...]:
    bounds = (0, *cuts, len(words))
    out = [" ".join(words[a:b]) for a, b in itertools.pairwise(bounds)]
    return tuple(_drop_comma(t) if i < len(out) - 1 else t for i, t in enumerate(out))


def valid(lines: Sequence[str], lone_ok: bool = False, widths: Sequence[float] | None = None,
          seam: int | None = None, free_ends: Sequence[int] = ()) -> bool:
    """A drawn form obeys the engine's hard rules: no line but the last ends on a never word; no lone-word row (unless
    `lone_ok`: a locked cover's last resort, where the break gives way before the size does; or a last row the phrase
    exception holds, phrase_row: ruling 4, measured on `widths` when given). `free_ends`: rows whose end is the copy's
    own field boundary, not a break the engine chose (a lockup's authored lines drawn whole, QF-23 / QF-143): free of
    the bonded-unit reading only, never of the never-end rule (freeze SYS-B-1: the exemption drew CARVED FROM / SOLID
    SALT, a row ending on FROM; lockup_fields already moves a field's closing never-end words to the next field)."""
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
    # cycle 0 round 8 (panel: "SACRED PATHS / OR / ASTRONOMY", "CARVED / FROM"): a row of function words only is
    # never a row, at any word count and in any last resort (it is the break's hinge, not a line of the heading)
    if len(lines) > 1 and any(all(bare(w) in brand_system.LINE_END_NEVER or bare(w) in brand_system.LINE_END_LAST_RESORT
                                  for w in ln.split()) for ln in lines):
        return False
    bonded = _bonded(words)
    if any(b in bonded for i, (_, b) in enumerate(spans[:-1]) if i not in free_ends):
        return False
    if lone_ok or not _lone_row(spans, len(words)):
        return True
    # the phrase exception: the last row alone may stand (middle rows keep the rule)
    return not any(b - a < 2 for a, b in spans[1:-1]) and phrase_row(lines, widths, seam)


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
    for i in _qualifier_words(words):   # ruling 8: a quantity's qualifiers stay bone, in a payoff run too
        if out[i] == ACCENT:
            out[i] = LEAD
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


def _choose(words, cut_sets, fits, balance_min, seam_pref, strict, measure, lone_ok=False, phrase_seam=None):
    best = None
    for cuts in cut_sets:
        lines = lines_of(words, cuts)
        if not fits(lines):
            continue
        lens = [measure(t) for t in lines]
        if not valid(lines, lone_ok, lens, phrase_seam):
            continue
        conj = sum(bare(words[k - 1]) in brand_system.LINE_END_LAST_RESORT
                   and not lines[i].endswith(tuple(_CLOSE)) for i, k in enumerate(cuts))
        bal = min(lens) / max(lens) if len(lens) > 1 and max(lens) > 0 else 1.0
        if strict and cuts and (conj or bal < balance_min):
            continue
        score = (0 if seam_pref is None or seam_pref in cuts else 1, len(cuts), conj, -bal)
        if best is None or score < best[0]:
            best = (score, cuts, conj, bal)
    return best


def set_heading(text: str, measure: Callable[[str, float], float], width: float, sizes: Sequence[float],
                max_lines: int = 2, seam: int | None = None, lockup: bool = False,
                balance_min: float | None = None, locked_lines: int = 0,
                last_resort_floor: float | None = None, extra_lines: int = 0,
                final_floor: float | None = None) -> Form | None:
    """The heading `text` set on the ladder `sizes` (first size, then at most the recorded fallback): None when no
    valid form fits `width` at any of them. `measure(line, size)` is a line's drawn width at `size`. `seam`: an
    authored line1 / line2 seam (word index) the break prefers; `lockup`: the payoff run is gold (else the heading is
    bone but for its stated quantities). `locked_lines`: a size-locked cover's last resort: up to this many lines at
    the (one) size, a lone-word row then allowed (the break and the band give way, never the size); recorded.
    `extra_lines`: before that, up to this many lines on the ladder (valid forms only; recorded by the line count).
    `last_resort_floor`: an interior's last resort when no valid form sets on the ladder: the fallback tier stepped
    down 2 px at a time to this floor (valid forms only), then a lone-word row at the fallback tier; each recorded
    (off_ladder / lone_last_resort), so a heading is refused only where even that does not set. `final_floor`: below
    that, further 2 px steps to this floor (a face too wide for every form on the ladder; recorded off_ladder)."""
    words = tuple(" ".join(str(text or "").split()).split())
    if not words:
        return None
    balance_min = brand_system.SPEC["heading.balance_min"] if balance_min is None else balance_min
    points = break_points(words)
    multi = [c for n in range(1, max_lines) for c in itertools.combinations(points, n)]

    def fits_at(size):
        return lambda lines: all(measure(t, size) <= width for t in lines)

    stages = []
    # SYS-D-5 (lander ruling c): the design size first, whole: one line, then a balanced break AT the size, before
    # any form at the fallback tier (MAGMATIC / UPLIFT at 138 beats MAGMATIC UPLIFT at 124, 3 px over). An
    # unbalanced break keeps its place after both tiers' balanced forms.
    for tier, size in enumerate(sizes):
        stages.append((tier, size, [()], 0.0, True))
        stages.append((tier, size, multi, balance_min, True))
    for tier, size in enumerate(sizes):
        stages.append((tier, size, multi, 0.0, False))
    if extra_lines > max_lines:   # an interior's next resort: more lines on the ladder (valid forms), before any fit
        more = [c for n in range(max_lines, extra_lines) for c in itertools.combinations(points, n)]
        for tier, size in enumerate(sizes):
            stages.append((tier, size, more, 0.0, False))
    lone_sets = None
    if locked_lines > max_lines:
        lone_sets = [c for n in range(1, locked_lines) for c in itertools.combinations(points, n)]
        stages.append((0, sizes[0], lone_sets, 0.0, False))
    off = []
    if last_resort_floor is not None:
        size = sizes[-1] - 2
        while size >= last_resort_floor:
            off.append(size)
            stages.append((None, size, [()] + multi, 0.0, False))
            size -= 2
        lone_sets = [c for n in range(1, max(max_lines, extra_lines)) for c in itertools.combinations(points, n)]
        stages.append((len(sizes) - 1, sizes[-1], lone_sets, 0.0, False))
        stages += [(None, size, lone_sets, 0.0, False) for size in off]   # never refused while a form sets
        if final_floor is not None:   # a face too wide for every form above (a second brand): smaller, valid, recorded
            size = (off[-1] if off else sizes[-1]) - 2
            while size >= final_floor:
                stages.append((None, size, lone_sets, 0.0, False))
                size -= 2
    # ruling 4: a lockup's payoff run is a complete phrase (the authored seam, else the engine's ideal one)
    phrase_seam = seam if seam is not None else (ideal_seam(words) if lockup else None)
    for tier, size, cut_sets, bmin, strict in stages:
        lone_ok = cut_sets is lone_sets
        got = _choose(words, cut_sets, fits_at(size), bmin, seam, strict, lambda t, s=size: measure(t, s), lone_ok,
                      phrase_seam)
        if got is None:
            continue
        _, cuts, conj, bal = got
        acc = seam if seam is not None else (cuts[-1] if cuts else ideal_seam(words))
        drawn = lines_of(words, cuts)
        phrase = (not lone_ok and len(cuts) > 0 and _lone_row(
            [(a, b) for a, b in itertools.pairwise((0, *cuts, len(words)))], len(words)))
        return _issue(Form(words, tuple(cuts), size, tier, acc, lockup,
                           {"balance": round(bal, 3), "conjunction_end": bool(conj),
                            **({"phrase_exception": drawn[-1]} if phrase else {}),
                            **({"locked_last_resort": True} if lone_ok else {}),
                            **({"off_ladder": True} if tier is None else {})}))
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


def lockup_valid(lines: Sequence[str], widths: Sequence[float] | None = None, seam: int | None = None,
                 fields: bool = False) -> bool:
    """A whole lockup's drawn rows, every field stacked, obey the engine's hard rules (valid): a field set valid on its
    own can still leave a one-word MIDDLE row once stacked under another field (cycle 0 round 8 R2: the YouTube
    two-line tier set THE 1,200-TON / BEDROCK / MONSTER, the field BEDROCK MONSTER broken alone). `widths`: each
    row's drawn width (the phrase exception's balance); `seam`: the word the copy's payoff field opens at (a
    two-field lockup's line 2), whose last row the phrase exception may keep alone (ruling 4). `fields`: `seam` is a
    boundary between two authored fields, so the row ending there is the copy's own line end (each field's own
    breaks are the engine's and set_field already held them to the rules): free of the bonded-unit reading, never of
    the never-end rule (SYS-B-1)."""
    rows = [t for t in lines if t.strip()]
    if len(rows) < 2:
        return True
    free, k = [], 0
    for i, ln in enumerate(rows):
        k += len(ln.split())
        if fields and seam is not None and k == seam:
            free.append(i)
    return valid(rows, widths=widths, seam=seam, free_ends=free)


def check_drawn(lines: Sequence[str]) -> bool:
    """For a renderer's post-draw record: the drawn lines obey the engine's hard rules."""
    return valid([t for t in lines if t.strip()]) if len([t for t in lines if t.strip()]) > 1 else True

