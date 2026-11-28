"""
src/multi_format.py
===================
Companion assets derived from the Reel's script package, for manual posting:
1. 2-frame Story sequence (1080x1920): hook and revelation (QF-80). The third frame, the poll's
   question, is no longer made or posted: its native poll sticker cannot be posted through the API,
   so the frame went out as a question with nothing to answer it (STORY_FRAMES, RETIRED_STORY_FRAMES).
2. 7-slide carousel (1440x1800, 4:5): cover, matched-photo fact slides, closing question.
Stories and carousels are posted on their own schedule (src/post_schedule.py).
"""

import contextlib
import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Tuple

from PIL import Image, ImageDraw

from . import brand, brand_system
from .brand_system import (
    GATES,
    LINE_END_LAST_RESORT,
    LINE_END_NEVER,
    colour_of,
    tok,
    type_px,
)
from .config import VIDEO_HEIGHT, VIDEO_WIDTH
from .font_engine import GlyphRefused, choose_face, surface
from .heading import (
    GOLD_ROLES,
    Form,
    field_runs,
    ideal_seam,
    lockup_fields,
    set_field,
    set_heading,
)
from .typography import (
    LayoutError,
    TextFitError,
    cap_height,
    cover_crop,
    drawable,
    field_font,
    fit_font,
    fit_line,
    fit_text,
    get_font,
    is_video,
    plan_text,
    probe_text,
    record_contrast,
    spoken_units,
    text_size,
    tracked_width,
    wrap_text,
)

log = logging.getLogger(__name__)

# Brand tokens (src/brand.py, visual overhaul 2026-10-29): one gold across every surface, bone text, ink grounds,
# read as brand.<ROLE> when drawn (QF-35a role tokens); the names this module bound at import before are served from
# the active brand by __getattr__ (WHITE and LIGHT are BONE).
_BRAND_NAMES = {"GOLD": "GOLD", "WHITE": "BONE", "LIGHT": "BONE", "INK": "INK"}


def __getattr__(name: str):
    """A colour name this module exported before QF-35a: the active brand's role (src/brand.py), read now."""
    if name in _BRAND_NAMES:
        return getattr(brand, _BRAND_NAMES[name])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# The poll frame's room for the native poll sticker, which the operator places when posting (y range on the
# 1080x1920 frame). Nothing is drawn there: the frame asks the question and the sticker answers it. It drew its
# own A and B option boxes and "REPLY WITH A OR B" until 2026-10-29: fake poll controls and a set-reply ask,
# which the engagement-bait check itself flags ("Reply A").
POLL_STICKER_ROOM = (1300, 1560)

# The Story sequence that is made, booked and posted (QF-80, 2026-11-09: option 1 of
# decisions/QF-80-stories-threshold.md): hook and reveal, numbered by their place in the sequence. The poll frame is
# retired: a frame of that name left by an older run is never booked or posted. Its rendering path (asks_poll below)
# stays for redrawing an older run's recorded recipe only.
STORY_FRAMES = ("story_01_hook.jpg", "story_02_reveal.jpg")
RETIRED_STORY_FRAMES = ("story_03_question.jpg",)


def story_posting_frames(paths) -> list[tuple[int, str]]:
    """The Story frames to post from `paths`, in order, as (part, path): a retired frame is dropped and at most
    len(STORY_FRAMES) frames remain. Each part is the frame's place in STORY_FRAMES when every frame carries its
    sequence name (a retried reveal stays part 2, so its idempotency key and its reach, audience <platform>_story2,
    keep the reveal's number); otherwise the frames are numbered in order."""
    kept = [str(p) for p in paths if Path(p).name not in RETIRED_STORY_FRAMES][:len(STORY_FRAMES)]
    if all(Path(p).name in STORY_FRAMES for p in kept):
        return [(STORY_FRAMES.index(Path(p).name) + 1, p) for p in kept]
    return list(enumerate(kept, 1))

STORY_BODY_PX = 46      # the Story body's size; never shrunk below it (2026-10-25: 30 px read as ~11 pt; 40 until
                        # the overhaul of 2026-10-29, which sets it at the critic's measured 46-48 px, ~16 pt)
STORY_BODY_LINES = 3
STORY_POINTER = "FULL STORY ON OUR PROFILE"   # the hook frame's pointer to the Reel (lane D cycle 4: no link sticker)
# Lander ruling 2026-11-26 (panel 6 PL r1 / r2: the pointer is drawn text with no tap target): a pointer line is drawn
# only when the publisher can attach a tap target to the frame (a link sticker). The Story API posts none today, so
# the line is withheld (recorded on the frame: lay.meta["pointer"], kept in stories/still_layout.json) and the block
# is laid without its row, as every later frame of the Story is. The publisher that can attach one sets this.
STORY_TAP_TARGET = False
STORY_POINTER_RULE = "a Story pointer is drawn only with a tap target the publisher can attach (ruling 2026-11-26)"


def story_tap_target() -> bool:
    """Whether the publisher can attach a tap target (a link sticker) to a Story frame today."""
    return bool(STORY_TAP_TARGET)
STORY_BODY_WEIGHT = 600  # Montserrat SemiBold (2026-11-02 round 2: stated, so a default elsewhere cannot thin it)
STORY_TITLE_W = 860      # the title's measure: three lines at its design size before it shrinks (2026-11-02)
STORY_TITLE_LINES = 3
# Freeze lane D cycle 1 (carry-in 1): the reveal's hero figure takes the figure role's size steps (the system's
# figure ladder: TYPE["tall"]["figure"] 300, then the steps youtube_thumbnail.FIGURE_STEPS declares, held equal by
# tests), the largest whose numeral fits the title's measure; never a continuous fit (it was fit_font 276 -> 160).
# The qualifier and the unit are one matched pair on the tall display ladder's least step (story_figure_pair_px:
# TYPE_FALLBACK["tall"]["display_m"], 96; about 0.3 of the 300 figure, as round 3 designed); the reveal's title takes
# the Story's one title size (story_title_px), never a share of it under the Story floors (it was 0.66 x: 91 at 138).
STORY_FIGURE_STEPS = brand_system.FIGURE_STEPS   # SYS-D-1: the system's one figure-step ladder
STORY_FIGURE_GAP = 18    # qualifier to figure and figure to unit, cap to cap (28 and 22 before round 3)
STORY_INLINE_GAP = 0.45   # an inline qualifier to its numeral, as a share of the qualifier's cap height (round 4)
# The poll frame's sticker room sits directly under the question (2026-11-02 round 2: at y 1300-1560 it was half a
# screen from the words it answers), on a quiet ink panel, nothing drawn in it; recorded per frame in layout.json
# (`poll_sticker_room`) and as the frame's protected region. Every frame still leaves POLL_STICKER_ROOM empty.
# Round 3 (2026-11-02): the room is Instagram's default poll footprint (about 760 x 300 on the 1080 frame), centred on
# the text column, and sits on a feathered ink band built with scrim(), not a hard-edged rectangle (a flat grey box on
# Richat's grey photo; hard top and bottom edges across Derinkuyu's architecture). The band is full strength across
# the room, fades over STORY_STICKER_FEATHER px above and below, and is only as deep as the white sticker needs: the
# band's 95th-percentile luma brought to STORY_STICKER_TARGET (about L* 40). Its box is written to
# companion_posting.json (story_poll_sticker.position) so the operator places the sticker on it.
STORY_STICKER_H = 300
STORY_STICKER_W = 760
STORY_STICKER_GAP = 36
STORY_STICKER_FEATHER = 120
STORY_STICKER_TARGET = 0.37
def story_qualifier(prefix: str) -> str | None:
    """A Story title's words before its figure as the hedge the figure is drawn with, or None when they are not one:
    the system's one hedge list (src/figures.py HEDGES; QF-145, operator ruling 6: "ESTIMATED" is a qualifier), an
    abbreviation spelled out (HEDGE_DRAWN: "EST." -> "ESTIMATED"). It was a list of its own without ESTIMATED,
    AN ESTIMATED, AT LEAST or CIRCA, so "ESTIMATED 44 M" stayed a plain title on the reveal."""
    from .figures import HEDGE_DRAWN, HEDGES
    p = " ".join((prefix or "").split()).lower()
    if p and p not in HEDGES and len(p) > 1 and p + "." in HEDGES:   # "APPROX" / "EST" written without the stop
        p += "."
    if not p or p not in HEDGES:
        return None
    return HEDGE_DRAWN.get(p, p).upper()


def story_figure_pair_px() -> int:
    """The reveal's qualifier and unit size: the tall display ladder's least step (read at call time)."""
    return round(brand_system.TYPE_FALLBACK["tall"]["display_m"])


_RANGE_DASH = re.compile(r"(?<![\w.,-])(\d[\d,]*(?:\.\d+)?)\s*(?:-|\u2014|\s\u2013\s)\s*(\d[\d,]*(?:\.\d+)?)(?![\d-])")


def typeset(text: str) -> str:
    """Display typography for a drawn string, glyphs only (round 3, 2026-11-02): never a word or a figure changed.
    A numeric span takes an en dash with no spaces ("94 - 104" -> "94\u2013104"; "25-MILE" is no span and stays);
    straight apostrophes and quotes become curly ("EARTH'S" -> "EARTH\u2019S"); three dots become an ellipsis."""
    if not text:
        return text or ""
    t = text.replace("...", "\u2026")
    t = _RANGE_DASH.sub(lambda m: f"{m.group(1)}\u2013{m.group(2)}", t)
    t = re.sub(r"(?<=\w)'|'(?=\d0s\b)", "\u2019", t)          # EARTH'S, the '60s
    t = re.sub(r"'(?=\w)", "\u2018", t)                       # an opening single quote
    t = re.sub(r"'", "\u2019", t)
    t = re.sub(r'(^|[\s(\[\u2014\u2013])"', "\\1\u201c", t)   # an opening double quote after a space or bracket
    return t.replace('"', "\u201d")


def story_display(line: str) -> str:
    """A Story title line as display type: no tilde glyph ("~280" -> "ABOUT 280") and a figure's unit in its
    short form ("280 FEET DEEP" -> "280 FT DEEP"); the figure itself is never changed."""
    from .typography import _FIGURE, _figure_ok, _unit
    line = re.sub(r"~\s*(?=\d)", "ABOUT ", line or "")

    def short(m):
        info = _unit(m.group("unit"))
        if not info or not _figure_ok(m) or m.group("sep") == "-":   # "THE 25-MILE" stays an adjective
            return m.group(0)
        return f"{m.group('num')}{m.group('plus')} {info[3].upper()}"
    return " ".join(_FIGURE.sub(short, line).split())


def story_figure(line1: str, line2: str):
    """The reveal frame's hero: {"number", "unit", "qualifier", "title"} when a title line carries a measured
    figure (a number with its unit), else None. The unit line keeps what the line says of the figure
    ("FT DEEP"); a qualifier before it ("ABOUT") is kept, never dropped; the other line is the title."""
    from .cover_design import monument_figure
    span = _story_span(line1, line2)
    if span is not None:      # a stated span is the hero whole ("94\u2013104"), never its first number (round 4)
        return span or None
    found = monument_figure({"line1": line1, "line2": line2})
    if not found or not found[1] or found[2] not in ("line1", "line2"):
        return None
    number, unit, field, (a, b) = found
    text = line1 if field == "line1" else line2
    prefix, suffix = text[:a].strip(), text[b:].strip()
    qualifier = story_qualifier(prefix) if prefix else ""
    if qualifier is None:
        return None   # words before the figure ("THE 144-FOOT DOME") read as a phrase: the frame stays a title
    return {"number": number, "unit": " ".join(x for x in (unit, suffix) if x), "qualifier": qualifier,
            "title": line2 if field == "line1" else line1}


def _story_span(line1: str, line2: str):
    """story_figure for a title line that states a span ("94\u2013104 MILLION YEARS OLD", "BETWEEN 8 AND 9 KM WIDE"):
    the hero dict with the span whole as its number (round 4, 2026-11-02: ranges are shown whole, never rounded to
    one figure); {} when a line states a span the frame cannot set as its hero (words before it), so the frame stays
    the two-tone title; None when neither line states a span."""
    for field, text in (("line1", line1 or ""), ("line2", line2 or "")):
        for m in _SPAN.finditer(text):
            lo = m.group("a") or m.group("a2")
            if float(lo.replace(",", "")) >= float(m.group("b").replace(",", "")):
                continue
            prefix, suffix = text[:m.start()].strip(), text[m.end():].strip()
            qualifier = story_qualifier(prefix) if prefix else ""
            if qualifier is None:
                return {}
            unit = " ".join(m.group("unit").upper().split())
            return {"number": f"{lo}\u2013{m.group('b')}", "unit": " ".join(x for x in (unit, suffix) if x),
                    "qualifier": qualifier, "title": line2 if field == "line1" else line1, "span": True}
    return None


def gate_deck(slides: list[dict[str, Any]], poll: dict[str, Any] | None, script_payload: dict[str, Any], surface: str,
              fields: tuple[str, ...] = ("heading", "body"), title: tuple[str, ...] = ("heading",),
              label: str = "slide", hedged: list[dict[str, Any]] | None = None
              ) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """(slides, poll, withheld rows): a deck's drawn copy through the one pre-draw figure gate (lane B c3b,
    visual_engine.gate_drawn), each slide's `fields` in reading order and the poll's question. Each withheld figure is
    recorded on the run's ledger (record_withheld). A slide whose title fields (`title`) or whose body the gate leaves
    with nothing readable has no other candidate in its deck: the deck is refused (refusal.Declined, rule
    figure_gate.evidence; main.hold_layout_error holds it), never drawn with the figure or without its words.
    `hedged` (lane B c3c): when given, a figure that lacks only its signed hedge is drawn with it and appended there
    ({"slide": i or None for the poll, "plain": the plain gate's form}); the caller reverts a slide that does not fit
    (revert_hedges) and records the rest (visual_engine.record_hedged)."""
    from .visual_engine import gate_drawn, headline_refused, record_withheld
    out, rows = [], []
    for i, slide in enumerate(slides, 1):
        name = f"{surface} {label} {i}"
        texts = {k: slide[k] for k in fields if isinstance(slide.get(k), str)}
        order = [k for k in fields if k in texts]
        gated, r = gate_drawn(texts, script_payload, name, order)
        if hedged is not None:
            # lane B c3c: a figure that lacks only its signed hedge is drawn with it; the plain gate's form (the figure
            # withheld) is kept on each row for the caller, which draws it where the hedged slide does not fit
            found: list[dict[str, Any]] = []
            h_gated, h_r = gate_drawn(texts, script_payload, name, order, hedged=found)
            if found:
                plain = {"fields": gated, "rows": r, "refused": _deck_emptied(texts, gated, r, title)}
                hedged += [dict(h, slide=i, plain=plain) for h in found]
                gated, r = h_gated, h_r
        emptied = _deck_emptied(texts, gated, r, title)
        if emptied:
            raise headline_refused(name, r, emptied)
        out.append(dict(slide, **gated))
        rows += r
    poll = dict(poll or {})
    if isinstance(poll.get("question"), str) and poll["question"].strip():
        texts = {"question": poll["question"]}
        gated, r = gate_drawn(texts, script_payload, f"{surface} poll")
        if hedged is not None:
            found = []
            h_gated, h_r = gate_drawn(texts, script_payload, f"{surface} poll", hedged=found)
            if found:
                plain = {"fields": gated, "rows": r,
                         "refused": "its question" if r and not gated["question"].strip() else ""}
                hedged += [dict(h, slide=None, plain=plain) for h in found]
                gated, r = h_gated, h_r
        if r and not gated["question"].strip():
            raise headline_refused(f"{surface} poll", r, "its question")
        poll["question"] = gated["question"]
        rows += r
    record_withheld(script_payload, rows)
    return out, poll, rows


def _deck_emptied(texts: dict[str, Any], gated: dict[str, Any], rows: list, title: tuple[str, ...]) -> str:
    """gate_deck's refusal: "its <fields>" when the gate left the slide's body or every title field with nothing
    readable, else ""."""
    if not rows:
        return ""
    emptied = [k for k in texts if texts[k].strip() and not gated[k].strip()]
    if [k for k in emptied if k not in title] or (emptied and not any(gated[k].strip() for k in title if k in gated)):
        return "its " + " / ".join(emptied)
    return ""


def revert_hedges(slides: list[dict[str, Any]], poll: dict[str, Any], hedged: list[dict[str, Any]], which: set,
                  script_payload: dict[str, Any], surface: str, label: str = "slide") -> list[dict[str, Any]]:
    """Lane B c3c: the slides in `which` (1-based; None for the poll) drawn from the plain gate's form instead of the
    hedged one (gate_deck's `hedged` rows: the hedged slide does not fit its size site), in place. Their withheld
    figures go on the run's ledger; a slide the plain form leaves with nothing readable refuses the deck, as gate_deck
    does. Returns the withheld rows added; the reverted rows are taken out of `hedged`."""
    from .visual_engine import headline_refused, record_withheld
    added: list[dict[str, Any]] = []
    done: set = set()
    for h in [h for h in hedged if h["slide"] in which]:
        i, plain = h["slide"], h["plain"]
        if i in done:
            continue
        done.add(i)
        name = f"{surface} poll" if i is None else f"{surface} {label} {i}"
        if plain["refused"]:
            raise headline_refused(name, plain["rows"], plain["refused"] + " (its hedged form does not fit)")
        if i is None:
            poll["question"] = plain["fields"]["question"]
        else:
            slides[i - 1] = dict(slides[i - 1], **plain["fields"])
        added += plain["rows"]
    hedged[:] = [h for h in hedged if h["slide"] not in which]
    record_withheld(script_payload, added)
    return added


def plain_deck(slides: list[dict[str, Any]], hedged: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A copy of `slides` with every hedged slide in its plain gate's form (for measuring the deck's size decisions
    both ways); the ledger is not touched."""
    out = [dict(s) for s in slides]
    for h in hedged:
        if h["slide"] is not None:
            out[h["slide"] - 1].update(h["plain"]["fields"])
    return out


# Lane D cycle 4 (lander ruling, cycle 3 whole-deck refusals, item b): the slide-drop fallback. A content slide whose
# heading or body the figure gate leaves with nothing readable (gate_deck would refuse the whole deck) is dropped from
# the deck instead, recorded, when the deck stays coherent: its cover and its closing slide kept (neither is ever
# dropped), at least CAROUSEL_MIN_SLIDES slides left (the cover, four content slides and the closing question: the
# Facebook copy's five-photo collage, FACEBOOK_PHOTOS, still fills). Otherwise nothing is dropped and gate_deck refuses
# the deck as before (fail closed). Decided on the copy as drawn (spoken units, the hedge a figure may be drawn with).
CAROUSEL_MIN_SLIDES = 6
SLIDE_DROP_RULE = "carousel.slide_drop"


def emptied_slides(cards: list[dict[str, Any]], script_payload: dict[str, Any], surface: str,
                   fields: tuple[str, ...] = ("heading", "body"), title: tuple[str, ...] = ("heading",),
                   label: str = "slide") -> list[dict[str, Any]]:
    """The slides (1-based "slide", "emptied", "rows") the figure gate would leave with nothing readable (gate_deck's
    refusal), each judged on its drawn form: the hedged form where a figure lacks only its signed hedge. Nothing is
    recorded on the run's ledger (a probe; gate_deck records)."""
    from .visual_engine import gate_drawn
    out = []
    for i, slide in enumerate(cards, 1):
        name = f"{surface} {label} {i}"
        texts = {k: slide[k] for k in fields if isinstance(slide.get(k), str)}
        order = [k for k in fields if k in texts]
        gated, rows = gate_drawn(texts, script_payload, name, order)
        found: list[dict[str, Any]] = []
        h_gated, h_rows = gate_drawn(texts, script_payload, name, order, hedged=found)
        if found:
            gated, rows = h_gated, h_rows
        why = _deck_emptied(texts, gated, rows, title)
        if why:
            out.append({"slide": i, "emptied": why, "rows": rows})
    return out


def slides_to_drop(cards: list[dict[str, Any]], script_payload: dict[str, Any], surface: str,
                   minimum: int = CAROUSEL_MIN_SLIDES) -> list[dict[str, Any]]:
    """The slide-drop fallback's decision: the emptied slides (emptied_slides) to drop, or [] when the deck would not
    stay coherent without them (an emptied cover or closing slide, or fewer than `minimum` slides left): the deck is
    then refused by gate_deck as before."""
    empty = emptied_slides(cards, script_payload, surface)
    n = len(cards)
    if not empty or any(e["slide"] in (1, n) for e in empty) or n - len(empty) < minimum:
        return []
    return empty


def record_dropped(script_payload: dict[str, Any], surface: str, dropped: list[dict[str, Any]],
                   cards: list[dict[str, Any]], kept: int) -> list[dict[str, Any]]:
    """Each dropped slide on the run's ledger (payload["layout_holds"], as record_withheld writes a drawn-without row:
    the review card and the release gate name it) and on the open omissions ledger (refusal.omit, when one is open:
    the evidence board's surfaces.json). One row per surface and slide. Returns the rows."""
    from . import refusal
    from .visual_engine import FIGURE_GATE_RULE
    holds = script_payload.setdefault("layout_holds", [])
    seen = {(h.get("surface"), h.get("omitted")) for h in holds if isinstance(h, dict)}
    out = []
    for e in dropped:
        heading = str(cards[e["slide"] - 1].get("heading") or "")
        omitted = f"slide {e['slide']} ({heading})"
        figures = "; ".join(f"{r['drawn']}: {r['reason']}" for r in e["rows"])
        reason = (f"drawn without {omitted!r}: {e['emptied']} has nothing readable once its unsigned figures are "
                  f"withheld ({figures}); the slide is dropped and the deck keeps {kept} slides (at least "
                  f"{CAROUSEL_MIN_SLIDES}, cover and closing slide kept) (rule: {FIGURE_GATE_RULE}, "
                  f"{SLIDE_DROP_RULE})")
        row = {"surface": surface, "omitted": omitted, "rule": SLIDE_DROP_RULE, "drawn": True, "reason": reason,
               "slide": e["slide"]}
        out.append(row)
        if (surface, omitted) not in seen:
            seen.add((surface, omitted))
            holds.append(row)
            log.warning("%s: %s", surface, reason)
        with contextlib.suppress(refusal.Declined):   # no ledger open: the run's own ledger above holds the record
            refusal.omit(surface, omitted, SLIDE_DROP_RULE, reason)
    return out


def prune_stale_slides(out_dir: Path, total: int) -> list[str]:
    """Slides numbered past `total` left in `out_dir` by an earlier, longer drawing of the deck (a dropped slide makes
    the deck shorter), removed with their layout records: every reader takes the folder's carousel_slide_*.jpg."""
    from . import still_layout
    gone = []
    for p in sorted(Path(out_dir).glob("carousel_slide_*.jpg")):
        m = re.fullmatch(r"carousel_slide_(\d+)\.jpg", p.name)
        if m and int(m.group(1)) > total:
            with contextlib.suppress(OSError):
                p.unlink()
                gone.append(p.name)
    if gone:
        side = still_layout.sidecar(Path(out_dir) / "carousel_slide_01.jpg")
        with contextlib.suppress(OSError, ValueError):
            data = json.loads(side.read_text(encoding="utf-8")) if side.is_file() else {}
            if any(k in data for k in gone):
                side.write_text(json.dumps({k: v for k, v in data.items() if k not in gone}, indent=1,
                                           ensure_ascii=False), encoding="utf-8", newline="\n")
    return gone


def _hedged_fits(got: Any) -> bool:
    """True when a drawn slide or frame (still_layout.drawn's (image, layout, attempts)) holds its copy at its own size
    rules: drawn, no layout problem and no field shortened (lane B c3c: a hedged form that does not is not drawn)."""
    if not isinstance(got, tuple) or len(got) < 2:
        return False
    lay = got[1]
    return not lay.problems() and not list(getattr(lay, "shortened", None) or [])


def slide_hedged(hedged: list[dict[str, Any]], surface: str, i: int, label: str = "slide") -> dict[str, Any] | None:
    """The figure_hedged record of one slide (gate_deck's `hedged` rows), None when no figure on it was hedged."""
    from .visual_engine import hedged_record
    return hedged_record([h for h in hedged if h["surface"] == f"{surface} {label} {i}"])


def slide_withheld(rows: list[dict[str, Any]], surface: str, i: int, label: str = "slide") -> dict[str, Any] | None:
    """The figure_withheld record of one slide (gate_deck rows), None when the gate withheld nothing from it."""
    from .visual_engine import withheld_record
    return withheld_record([r for r in rows if r["surface"] == f"{surface} {label} {i}"])


def story_fields(slide: dict[str, Any], poll: dict[str, Any] | None = None) -> tuple[str, str]:
    """A Story frame's two title fields as drawn: display typography, the poll's question as the headline when the
    slide has none (split by the heading engine), and the authored seam repaired by the engine (round 7, F1: a
    function word closing line 1 opens line 2 instead, in bone: "SEEN FROM" / "SPACE" -> "SEEN" / "FROM SPACE")."""
    l1, l2 = (typeset(slide.get(k) or "").upper() for k in ("line1", "line2"))
    if poll and poll.get("question") and not (l1.strip() or l2.strip()):
        # Round 4 (2026-11-02): the frame's headline is the slide's own lines (fact-checked with the slide) and the
        # native sticker asks the poll's question; the question is set as the headline only when the slide has
        # no lines (it duplicated the sticker word for word, and went on screen unchecked).
        l1, l2 = split_balanced(typeset(poll["question"]).upper())
    l1, l2 = story_display(l1), story_display(l2)
    if l1.strip() and l2.strip():
        l1, l2, _ = lockup_fields(l1, l2)
    return l1, l2


ACCENT_RISE_MIN = 2   # px of ink above the cap line that count as an accent's rise (a capital's antialiasing overshoot is 1)


def accent_rise(d, text: str, font, at_least: int = ACCENT_RISE_MIN) -> int:
    """How far `text`'s highest ink rises above `font`'s cap line (an accented capital: "Á", "Ł"), px; 0 when under
    `at_least` (lane D cycle 3: a line's accents keep the line gap to the line above them, as the kicker's do)."""
    if not text:
        return 0
    rise = -d.textbbox((0, 0), text, font=font, anchor="ls")[1] - cap_height(font)
    return rise if rise >= at_least and rise > 0 else 0


def story_title_px(slides: list[dict[str, Any]], tight: bool = False, poll: dict[str, Any] | None = None) -> int:
    """The Story's ONE title size (round 7, F10; ruling FREEZE-PARK round 4 (2)): for each frame, the largest size
    from the design size (TYPE tall display_xl; tight 124) down to the ruled one-line floor (106) at which every
    field sets on one line; a frame none of those sets keeps the design size (the engine breaks it there). The Story
    takes the least over its frames (a reveal frame's figure sets apart and does not vote; its title, drawn at the
    Story's size since lane D cycle 1, votes as a field): never below 106."""
    design = 124 if tight else round(brand_system.type_px("display_xl", "tall"))
    floor = GATES["heading.story_one_line_floor"]
    # cycle 0 round 8 (panel TY-A-6): the tall canvas's display ladder steps only (150, 138, 124, 120, 112 down to
    # the floor), never a size between them (the Richat Story drew 128); sizes in reference units (ruling 6)
    steps = [round(s) for s in brand_system.display_steps("tall", floor) if s <= design]
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    sizes = []
    for i, slide in enumerate(slides):
        l1, l2 = story_fields(slide, poll)
        hero = story_figure(l1, l2) if i == 1 else None
        fields = [t for t in ((hero["title"],) if hero else (l1, l2)) if t.strip()]
        if not fields:
            continue
        one = next((sz for sz in steps
                    if all(d.textlength(t, font=get_font(brand.DISPLAY_FONT, sz)) <= STORY_TITLE_W for t in fields)),
                   None)
        sizes.append(one or design)
    return max(floor, min(sizes)) if sizes else design


# The Story's text column starts here, 19 px inside the safe box (2026-10-29: left-aligned). PLATES-14 (freeze c5):
# moving it to the margin token moved the type over another ground (and the photo window with it): 31 Story elements
# read lower than base, against ruling N2 (2026-11-26, review 2): kept at 84; PLATES-14 recorded open on the Story.
STORY_MARGIN = 84


def story_body_width() -> int:
    """The Story text column: the safe box less STORY_MARGIN's inset on each side (912 px at 1080x1920)."""
    from .safe_zones import safe_box
    sx0, _, sx1, _ = safe_box("story", VIDEO_WIDTH, VIDEO_HEIGHT)
    inset = STORY_MARGIN - sx0
    return (sx1 - inset) - (sx0 + inset)


def story_window(photo: Path, text_box: tuple[float, float, float, float]) -> tuple[Image.Image, dict[str, Any]]:
    """(the Story frame's photo ground, its window record). Lane D cycle 2 (panel 2 lead F2, verified: the Richat
    Story s3 photo's spacecraft sits behind the text column when centred): the window is the deck window rule's
    (hero_window.deck_window, k = 0: the picture's centre always in view), with the Story's text column as the box
    that should hold no foreign object, the same rule the carousel applies to its masthead box. Centred, as before,
    where the centre is clear or the picture leaves no other window. Freeze cycle 5: that window is held to the
    photo-fitness rule (src/photo_fitness.py; the whole frame is its view): fit, it is drawn as it was; unfit, the
    fit crop of the photograph that changes least is drawn; the measures are in the record ("fit")."""
    from .image_presentation import full_image
    size = (VIDEO_WIDTH, VIDEO_HEIGHT)
    rec: dict[str, Any] = {"centering": [0.5, 0.5], "text_clear": None}
    if not full_image(photo):
        from . import photo_fitness as pf
        from .hero_window import deck_window
        clean, _ = pf.load(Path(photo))
        win = deck_window(clean.size, size, 0, tuple(text_box), clean)
        rec = {"centering": [round(v, 3) for v in win["centering"]], "text_clear": win["masthead_clear"]}
        fit = pf.place(photo, pf.Window(size, name="story"), tuple(rec["centering"]), image=clean,
                       analysis=pf.analysis_of(Path(photo), clean),
                       fixed=bool(getattr(_render_state, "asked_only", False)))
        rec["fit"] = {k: v for k, v in fit.items() if k != "window"}
        if fit["moved"]:
            return pf.crop(clean, fit["box"], size).convert("RGB"), rec
    return cover_crop(photo, size, tuple(rec["centering"])).convert("RGB"), rec


def _story_safe(im: Image.Image):
    """PLATEFORM: the Story safe box a block keeps inside (None under the shipped form: the band ignores it)."""
    from . import plate_form
    from .safe_zones import safe_box
    return safe_box("story", *im.size) if plate_form.active() != "base" else None


def poll_room_box(y0: float, height: int = STORY_STICKER_H) -> tuple[int, int, int, int]:
    """The poll sticker's room from `y0`: STORY_STICKER_W wide (no wider than the text column), centred on the
    Story's text column."""
    col = story_body_width()
    w = min(STORY_STICKER_W, col)
    cx = STORY_MARGIN + col / 2
    return (round(cx - w / 2), round(y0), round(cx + w / 2), round(y0 + height))


def scrim(im: Image.Image, y0: float, y1: float, feather: int = 300, target: float = 0.30, cap: float = 0.90,
          floor: float = 0.18, colour=None, pct: float = 95, grade: bool | None = None,
          out: dict | None = None, plain=(), safe=None, shipped: bool = False, band_on=None) -> Image.Image:
    """An ink gradient behind one text band, deep only as far as the band's own brightness needs (its `pct`
    percentile luminance brought toward `target`), fading over `feather` px above and below: never a box and
    never the whole frame (visual overhaul, 2026-10-29: a 35% shade over every Story and a fixed vignette band
    left bright stone at 3:1 and dark photos darker than they needed). Each line's own halo (place_text) does
    the rest, line by line. `feather` is the designed minimum: each edge's feather grows with the luma step it
    crosses (band_edge.feather: the band-edge token, cycle 0 round 7, panel F8), so a bright ground never gets a lid.
    `out`: a dict that receives the band's strength (PLATES-15 records its lines' ceiling against it). `plain`: the
    type's boxes, round which a graded band stays the plain INK alpha (band_edge.graded, ruling N2).
    `grade` (default: a band whose cap is under 1, never opaque): the band grades the photograph where it grades out,
    its full-strength body under the type the plain INK alpha (band_edge.graded,
    PLATES-03: the ground's hue and saturation kept, the luma the INK alpha's); an opaque panel stays its colour."""
    import numpy as np

    from . import band_edge, plate_form
    if plain and plate_form.active() != "base" and not shipped:
        # design freeze cycle 6 (PLATEFORM; the block family, lander decision, by switch until the default flips): the
        # frame's blocks from the type's items (box, fill, block tag), inside `safe`, never lighter inside than the
        # band on the same pixels (plate_form.with_block), laid where plate_form.choose says (the band leaves a line
        # under the target, or the block darkens less of the frame), else the band; `band_on`: the image the shipped
        # band is laid on (a Story frame's credit band under it), so the choice is the whole frame's
        boxes = [p[0] if len(p) in (2, 3) and hasattr(p[0], "__len__") else p for p in plain]
        got: dict = {}
        banded = scrim(band_on if band_on is not None else im, y0, y1, feather, target, cap, floor, colour, pct,
                       grade, out=got, plain=boxes, shipped=True)
        if getattr(_render_state, "shipped_ground", False) or getattr(_render_state, "kicker_survey", False):
            # a survey or the window fitter's move test reads the shipped ground (lander ruling 1, review of cycle 6)
            if out is not None:
                out["strength"] = got.get("strength", 0.0)
            return banded
        lyr, rec = plate_form.layer(im, plain, safe=safe)
        shaded = plate_form.with_block(im, lyr, banded)
        plate_form.choose(im, banded, shaded, plate_form.items_of(plain), rec)
        _render_state.frame_form = rec["laid"]
        if out is not None:
            out["plate_form"] = rec
            out["strength"] = 0.0 if rec["laid"] != "band" else got.get("strength", 0.0)
        return shaded if rec["laid"] != "band" else banded
    W, H = im.size
    colour = colour or brand.INK
    lum = band_edge.luma_rows(im)
    band = lum[max(0, int(y0)):max(int(y0) + 1, int(y1))] / 255.0
    p = float(np.percentile(band, pct)) if band.size else 0.0
    strength = float(np.clip(1 - target / max(p, 1e-3), floor, cap))
    if out is not None:
        out["strength"] = round(strength, 4)
    # freeze cycle 5 (PLATES): a band with the photograph above AND below it is a floating band (the lower ceiling)
    floating = y0 > 0 and y1 < H - 1
    up = band_edge.feather(lum, y0, "up", colour, strength, feather, floating=floating) if y0 > 0 else feather
    down = band_edge.feather(lum, y1, "down", colour, strength, feather, floating=floating) if y1 < H - 1 else feather
    v = band_edge.band_profile(H, y0, y1, up, down)
    mask = Image.fromarray((v * 255 * strength).astype(np.uint8).reshape(H, 1)).resize((W, H))
    if (cap < 1.0) if grade is None else grade:
        out = band_edge.graded(im, np.asarray(mask, np.float32) / 255.0, colour, full=strength, plain=plain)
        return out.convert("RGBA") if im.mode == "RGBA" else out.convert(im.mode)
    layer = Image.new("RGBA", im.size, tuple(colour) + (255,))
    layer.putalpha(mask)
    out = Image.alpha_composite(im.convert("RGBA"), layer)
    return out if im.mode == "RGBA" else out.convert(im.mode)


def text_band_boxes(d, rows, x: float, bottom: float) -> list:
    """The ink boxes of a carousel stack's rows as _stack sets them (bottom-aligned on `bottom`, at column `x`)."""
    drawn = [r for r in rows if r.get("text")]
    if not drawn:
        return []
    low = max(d.textbbox((x + r.get("x", 0), r["y"]), r["text"], font=r["font"], anchor="ls")[3] for r in drawn)
    off = bottom - low
    return [d.textbbox((x + r.get("x", 0), off + r["y"]), r["text"], font=r["font"], anchor="ls") for r in drawn]


def row_ink_box(d, x: float, baseline: float, text: str, font, track: float = 0.0) -> tuple:
    """A row's drawn ink box at (x, baseline), its tracking included (PLATEFORM: a block holds the drawn ink)."""
    b = d.textbbox((x, baseline), text, font=font, anchor="ls")
    if track:
        b = (b[0], b[1], b[0] + tracked_width(text, font, track), b[3])
    return tuple(b)


def kicker_ink_box(x: float, baseline: float, text: str, font) -> tuple:
    """A kicker's drawn box (draw_kicker): from its lead rule at x to the tracked words' end, cap top to baseline
    (descenders included)."""
    b = font.getbbox(text, anchor="ls")
    return (x, baseline - cap_height(font), x + kicker_width(text, font), baseline + max(0, b[3]))


SHARE_RULE = (96, 6, 26)   # design px: the CTA slide's gold rule (length, thickness, gap above the share line's cap)


def share_rule_box(x: float, share_top: float, px) -> tuple:
    """The CTA slide's gold rule over its share line (SHARE_RULE): (x0, y0, x1, y1), drawn and blocked from here."""
    length, thick, gap = SHARE_RULE
    return (x, share_top - px(gap) - px(thick), x + px(length), share_top - px(gap))


def text_band_items(d, rows, x: float, bottom: float, px) -> list:
    """PLATEFORM (the block family): a carousel stack's rows as (ink box, fill, block tag): the kicker, heading and
    body one block ("main"), a share / CTA line its own ("cta"), its gold rule (drawn 26 + 6 design px above the share
    line's first cap, 96 wide) inside it."""
    drawn = [r for r in rows if r.get("text")]
    if not drawn:
        return []
    low = max(d.textbbox((x + r.get("x", 0), r["y"]), r["text"], font=r["font"], anchor="ls")[3] for r in drawn)
    off = bottom - low
    boxes = [kicker_ink_box(x + r.get("x", 0), off + r["y"], r["text"], r["font"]) if r.get("kicker")
             else row_ink_box(d, x + r.get("x", 0), off + r["y"], r["text"], r["font"], r.get("track", 0.0))
             for r in drawn]
    out = [(b, tuple(r["fill"][:3]), "cta" if r.get("name") == "share" else "main") for b, r in zip(boxes, drawn)]
    share = [b for b, r in zip(boxes, drawn) if r.get("name") == "share"]
    if share:
        top = min(b[1] for b in share)
        out.append((share_rule_box(x, top, px), tuple(brand.GOLD[:3]), "cta"))
    return out


def text_band_need(im: Image.Image, d, rows, x: float, bottom: float) -> float:
    """The strength a carousel stack's band needs for its lines (band_edge.text_strength, PLATES-15): `rows` as
    _stack sets them (bottom-aligned on `bottom`, at column `x`); kicker rows are labels with their own rule."""
    from . import band_edge
    drawn = [r for r in rows if r.get("text")]
    if not drawn:
        return 0.0
    low = max(d.textbbox((x + r.get("x", 0), r["y"]), r["text"], font=r["font"], anchor="ls")[3] for r in drawn)
    off = bottom - low
    boxes = [(d.textbbox((x + r.get("x", 0), off + r["y"]), r["text"], font=r["font"], anchor="ls"), r["fill"])
             for r in drawn if not r.get("kicker")]
    return band_edge.text_strength(im, boxes, brand.INK)


def text_band_record(need: float, strength: float) -> dict[str, Any]:
    """A text band's record (PLATES-15; lander ruling N1, 2026-11-26: 7:1 is the target, the gate floors are the gate,
    and a band never darkens more of the photograph than the base design did): the target, the strength its lines
    would need to read at it (band_edge.text_strength), the strength the band has (its own rule, unchanged), and
    `short` when that is less: the lines then read at the best the band and each line's halo give, the recorded
    ceiling (each line's ratio is on its element)."""
    from . import band_edge
    return {"target": band_edge.text_target(), "need": round(float(need), 3), "strength": round(float(strength), 4),
            "short": bool(need > strength + 0.005),
            "rule": "7:1 target within the base band's darkening (ruling N1): no deeper band, ceiling recorded"}


# Ground-aware legibility (round 4, 2026-11-02), measured and fail-closed. A Story's text band is shaded deeper while
# (a) the texture behind any of its lines is busy (the high-pass RMS of the luma in a ring round the line, on the
# shaded frame, above GROUND_ENERGY_MAX), or (b) a gold line sits on a ground of its own hue (within GOLD_HUE_SEP
# degrees of brand.GOLD at a similar chroma, for GOLD_CLASH_SHARE of its ring: hue gives no separation there). The
# band stays the feathered gradient (never a box: boxes read as caption bars on a Story). A gold line still on a
# gold ground at the deepest step is set in bone. What was measured and chosen is the frame's `ground` record.
GROUND_ENERGY_MAX = 9.0
GROUND_RING = 14
GOLD_HUE_SEP = 25.0
GOLD_CLASH_SHARE = 0.10
STORY_SCRIM_STEPS = (0.28, 0.2, 0.13, 0.08)


def _ring(im: Image.Image, box, pad: int = GROUND_RING) -> Image.Image:
    W, H = im.size
    x0, y0, x1, y1 = (int(v) for v in box)
    return im.crop((max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad)))


def ground_energy(im: Image.Image, box) -> float:
    """The texture behind a line: the RMS of the luma less its 6 px blur, over the line's box and GROUND_RING px
    round it (0-255 luma units; a flat ground is 0)."""
    import numpy as np
    from PIL import ImageFilter
    g = _ring(im.convert("L"), box)
    if not g.width or not g.height:
        return 0.0
    a = np.asarray(g, dtype=np.float32)
    b = np.asarray(g.filter(ImageFilter.GaussianBlur(6)), dtype=np.float32)
    return round(float(np.sqrt(np.mean((a - b) ** 2))), 2)


def gold_clash(im: Image.Image, box, gold=None) -> float:
    """The share of the ground round `box` whose hue is within GOLD_HUE_SEP degrees of `gold` (brand.GOLD) at a
    similar chroma (at least half its saturation) and not dark: where gold type has no hue separation."""
    import colorsys

    import numpy as np
    gold = gold or brand.GOLD
    gh, gs, _ = colorsys.rgb_to_hsv(*(c / 255 for c in gold))
    hsv = np.asarray(_ring(im.convert("RGB"), box).convert("HSV"), dtype=np.float32) / 255.0
    if not hsv.size:
        return 0.0
    dh = np.abs(hsv[..., 0] - gh)
    dh = np.minimum(dh, 1 - dh) * 360
    near = (dh <= GOLD_HUE_SEP) & (hsv[..., 1] >= gs * 0.5) & (hsv[..., 2] >= 0.3)
    return round(float(near.mean()), 3)


def story_ground(base: Image.Image, d, rows, x: float, top: float, block: float, plain=(), band_on=None):
    """The Story's text band shaded (scrim) only as deep as its ground needs (GROUND_ENERGY_MAX, GOLD_CLASH_SHARE),
    trying STORY_SCRIM_STEPS in turn; returns (image, rows, record). A gold row still clashing at the deepest step
    is set in bone (its fill changed, nothing else)."""
    def boxes(gold: bool):
        return [d.textbbox((x + r[6], top + r[1]), r[2], font=r[3], anchor="ls") for r in rows
                if r[2] and ((r[4] == brand.GOLD) if gold else True)]
    every, golds = boxes(False), boxes(True)
    from . import band_edge
    # PLATES-15: what the lines would need for 7:1 on their own ground (band_edge.text_strength), recorded against the
    # band's own strength (ruling N1: never a deeper band)
    need = band_edge.text_strength(base, [(d.textbbox((x + r[6], top + r[1]), r[2], font=r[3], anchor="ls"), r[4])
                                          for r in rows if r[2]], brand.INK)
    got: dict = {}
    from . import plate_form as _pf
    from .safe_zones import safe_box
    items = list(every) + list(plain)
    if _pf.active() != "base":   # PLATEFORM (the block family): the drawn lines with their fills, one block
        items = [(row_ink_box(d, x + r[6], top + r[1], r[2], r[3], r[5]), tuple(r[4][:3]), "main")
                 for r in rows if r[2]] + list(plain)
    for target in STORY_SCRIM_STEPS:
        shaded = scrim(base, top - 40, top + block + 30, feather=320, target=target, out=got,
                       plain=items, safe=safe_box("story", *base.size) if _pf.active() != "base" else None,
                       band_on=band_on)
        energy = max((ground_energy(shaded, b) for b in every), default=0.0)
        clash = max((gold_clash(shaded, b) for b in golds), default=0.0)
        if energy <= GROUND_ENERGY_MAX and clash < GOLD_CLASH_SHARE:
            break
    record = {"scrim_target": target, "energy": energy, "gold_clash": clash, "gold_to_bone": False,
              "text_band": text_band_record(need, got.get("strength", 0.0)),
              **({"plate_form": got["plate_form"]} if "plate_form" in got else {})}
    if clash >= GOLD_CLASH_SHARE:
        rows = [(r[0], r[1], r[2], r[3], brand.BONE if r[4] == brand.GOLD else r[4], r[5], r[6]) for r in rows]
        record["gold_to_bone"] = True
        log.info("Story gold line set in bone: its ground is gold (%.0f%% of its ring).", clash * 100)
    return shaded, rows, record


WARM_HUE = (15, 62)      # degrees: ochre, gold and amber grounds, where the brand gold has no hue contrast
WARM_SHARE = 0.45


def warm_ground(im: Image.Image, box) -> bool:
    """Whether the pixels in `box` are mostly a warm or gold hue (WARM_HUE, saturated and not dark) for at least
    WARM_SHARE of them."""
    import numpy as np
    hsv = np.asarray(im.convert("RGB").crop(tuple(int(v) for v in box)).reduce(4).convert("HSV"), dtype=np.float32)
    if not hsv.size:
        return False
    hue = hsv[..., 0] * 360 / 255
    warm = (hue >= WARM_HUE[0]) & (hue <= WARM_HUE[1]) & (hsv[..., 1] > 64) & (hsv[..., 2] > 70)
    return float(warm.mean()) >= WARM_SHARE


def kicker_gap(size: float) -> int:
    """F20: the one kicker-to-heading gap (the kicker's baseline to the heading's cap line), kicker.gap ems of the
    kicker's size: 32 px on the carousel (45.3 px kicker), 28 on the tall and wide canvases (40 px)."""
    return round(tok("kicker.gap") * size)


def _kicker_tokens() -> tuple[float, float, float, float]:
    """The system kicker (src/brand_system.py kicker.*), read now: (track em, rule caps, gap caps, thickness caps)."""
    return tok("kicker.track"), tok("kicker.rule"), tok("kicker.rule_gap"), tok("kicker.rule_thick")


# Planning margins (round 3, 2026-11-02): small gold kickers are planned at 5.5:1 so Facebook's downscale and
# recompression (960 px, JPEG ~70) still leave them at 4.5 (the FB copy measured 5.01 and 4.83). The masthead's ground
# and tiers are src/masthead.py's (cycle 0: gate 5.5, target 6.5, scrim peak 0.60, halo 0.16).
KICKER_TARGET = GATES["contrast.kicker"]
PLATFORM_WIDTH, PLATFORM_QUALITY = 960, 70   # the platform re-encode every delivered ratio is measured after too


def kicker_width(text: str, font) -> float:
    """A kicker's drawn width: its gold rule and gap, then the tracked text."""
    ch = cap_height(font)
    track, rule, gap, _ = _kicker_tokens()
    return rule * ch + gap * ch + tracked_width(text, font, font.size * track)


LABEL_FIRST_STEP = ("halo", 0)   # typography.label_ladder's first step: the label in its colour, its halo only


def plan_kicker(im: Image.Image, xy, text: str, font, colour, anchor: str = "ls", track: float = 0.0,
                rule=None, **kw):
    """The ONE kicker's gate (round 7, F20 + sweep S1) on the ONE overlay-label rule (operator ruling 2, 2026-11-17:
    typography.plan_label): the kicker planned at the kicker tier (contrast.kicker, 5.5) in its colour (kicker.colour)
    on a halo never deeper than tok("halo.label_max"); else the bone variant (the accent rule's warm-ground fallback);
    else INK type (the light-ground variant); else its colour on a held INK plate that also holds its lead `rule`
    (a box, drawn on the plate in the plate's fill); else no kicker (fail closed, like the masthead). The halo past
    the cap (plan_text's default 0.95: the end screen's WATCH NEXT cloud on bright stone) is retired. Returns (plate or
    None, record): the record names the variant drawn, its measured ratio, and the reason when none is."""
    from .typography import label_passes, plan_label
    gate = GATES["contrast.kicker"]
    target = max(gate, float(kw.pop("target", gate)))
    step = kw.pop("step", None)
    kw.pop("halo_px", None), kw.pop("max_halo", None), kw.pop("min_halo", None)   # the label rule sets the halo
    text = drawable(text, font)   # no emoji, no missing-glyph box (the old kicker path's rule, 2026-10-29)
    if not text.strip():
        return plan_text(im, xy, text, font, tuple(colour), anchor, track=track), {
            "variant": "colour", "contrast": None, "gate": gate, "shown": True}
    member = kicker_label(im, xy, text, font, colour, anchor, track, rule, target)
    from . import plate_form
    if step is None and plate_form.active() == "block" and not getattr(_render_state, "kicker_survey", False) \
            and getattr(_render_state, "frame_form", None) == "block":
        # PLATEFORM (the block family; lander ruling 2, review of cycle 6: one form per frame): a kicker inside its
        # frame's block takes the first step of the ladder (its colour; the block is its ground, no plate of its own);
        # on a frame that keeps the band it takes the deck's one step, which the survey chose on the shipped grounds
        # (scrim draws the band while a deck or Story is surveyed), so it is base's step there
        step = LABEL_FIRST_STEP
    # cycle 0 round 8 (R1, one treatment per set): a deck's kickers take ONE step (the deck's, kicker_step); the
    # first drawing of a deck surveys every step each kicker's ground holds (kicker_survey) so the deck can choose
    forced = step if step is not None else getattr(_render_state, "kicker_step", None)
    broken = None
    if forced is not None:
        plates, rec = plan_label(**member, only=tuple(forced))
        if not rec["shown"]:   # the set's step does not hold here (another fit, say): this kicker's own, recorded
            broken = list(forced)
            plates, rec = plan_label(**member)
    else:
        plates, rec = plan_label(**member)
    passes = None
    if getattr(_render_state, "kicker_survey", False):
        passes = [list(s) for s in label_passes(**member)]
    return finish_kicker(plates, rec, rule, gate, target, text, passes=passes, set_broken=broken,
                         forced=forced is not None and broken is None)


def kicker_label(im: Image.Image, xy, text: str, font, colour, anchor: str, track: float, rule, target: float) -> dict:
    """A kicker as one member of the overlay-label rule (typography.plan_label's arguments): its colour, then bone,
    on its words and its lead `rule` (a box the held plate also holds). Freeze cycle 4 (SYS item 3, panel 5 BC-02 /
    TY: the Aswan deck drew every kicker bone, the pure INK diagram slide's too, where gold reads 10:1): ONE kicker
    colour rule, colour_held: the kicker is its colour on every ground, the ground answered by its backing (the halo,
    then the held INK plate) before the colour gives way to bone (a palette whose colour misses the kicker gate even on
    the plate; recorded by the step drawn)."""
    colours = tuple(dict.fromkeys((tuple(colour[:3]), tuple(brand.BONE))))
    return {"ground": im, "rows": [(xy, drawable(text, font), font, anchor, track)], "colours": colours,
            "target": target, "extra": [rule] if rule is not None else (), "colour_held": True}


def finish_kicker(plates, rec: dict, rule, gate: float, target: float, text: str, passes=None, set_broken=None,
                  forced: bool = False):
    """plan_label's answer for a kicker as plan_kicker returns it: (plate or None, record)."""
    if rec["shown"]:
        plate = plates[0]
        if rule is not None and getattr(plate, "backing", None) is not None:
            plate.backing.parent.devices.append((tuple(rule), tuple(plate.fill)))   # the rule rides on its plate
        out = {"variant": rec["variant"], "step": rec.get("step"), "contrast": plate.contrast, "gate": gate,
               "target": target, "shown": True, "halo": plate.halo, "plate": rec.get("plate")}
        if passes is not None:
            out["passes"] = passes
        if forced:
            out["set_step"] = rec.get("step")
        if set_broken is not None:
            out["set_broken"] = set_broken
        return plate, out
    log.warning("Kicker %r not drawn: %s below the %.1f:1 kicker gate in every variant", text, rec["best_tried"], target)
    return None, {"shown": False, "gate": gate, "target": target, "best_tried": rec["best_tried"],
                  "tried": rec["tried"], "reason": f"under the {target}:1 kicker gate in every overlay-label variant "
                                                   f"(best {rec['best_tried']})"}


@contextlib.contextmanager
def kicker_set(step=None, survey: bool = False):
    """Draws on this thread with a set's ONE kicker step (cycle 0 round 8 R1: every kicker of a deck or a Story in
    one overlay-label treatment, deck_kicker_step), or with `survey` every step each kicker's ground holds recorded
    (the record's "passes") so the set can choose one. Restored on exit."""
    prev = getattr(_render_state, "kicker_step", None), getattr(_render_state, "kicker_survey", False)
    _render_state.kicker_step = tuple(step) if step else None
    _render_state.kicker_survey = bool(survey)
    try:
        yield
    finally:
        _render_state.kicker_step, _render_state.kicker_survey = prev


def kicker_records(lays) -> list[dict]:
    """Every drawn kicker's record across a set of drawn layouts (a deck's slides, a Story's frames)."""
    out = []
    for lay in lays:
        for rec in ((getattr(lay, "meta", None) or {}).get("kickers") or {}).values():
            if rec.get("shown") and rec.get("step") is not None:
                out.append(rec)
    return out


def deck_kicker_step(lays) -> tuple[str, int] | None:
    """The ONE kicker step for a deck (cycle 0 round 8 R1): the least intrusive step every drawn kicker's ground
    holds (typography.set_step over the surveyed passes; the shared step when they already share one); None when
    the deck draws no kicker, or no one step holds on every kicker's ground."""
    from .typography import label_ladder, set_step, step_key
    recs = kicker_records(lays)
    steps = {step_key(r["step"]) for r in recs}
    if len(steps) <= 1:
        return next(iter(steps)) if steps else None
    return set_step(label_ladder((0, 1), colour_held=True), [r.get("passes") or [r["step"]] for r in recs])


_DECK_STEPS: dict[str, tuple[str, int] | None] = {}
_DECK_STEPS_LOCK = threading.Lock()
DECK_STEPS_KEPT = 64


def deck_set_key(output_dir, cards, archetypes, photos, diagram, *rest) -> str:
    """The identity of a deck as both its copies draw it (SYS-R8 integration, ruling 3): the run folder, the cards as
    drawn, the slides' kinds and photographs (path, size and time) and the diagram, the shared inputs and the active
    brand, so a deck's kicker step is reused only by a copy of the very same slides."""
    import hashlib

    def stat(p) -> list:
        try:
            st = Path(p).stat()
            return [str(p), st.st_size, st.st_mtime_ns]
        except (OSError, TypeError):
            return [str(p)]
    blob = json.dumps([str(Path(output_dir).resolve()), cards, archetypes, [stat(p) for p in photos],
                       stat(diagram[1]) if diagram else None, list(rest), brand.fingerprint()],
                      sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def deck_set_step(key: str) -> tuple[bool, tuple[str, int] | None]:
    """(planned, step): the kicker step a copy of this deck was planned on, when one was."""
    with _DECK_STEPS_LOCK:
        if key in _DECK_STEPS:
            return True, _DECK_STEPS[key]
    return False, None


def keep_deck_set_step(key: str, step) -> None:
    """Keeps a deck's planned kicker step for its other copy (the last DECK_STEPS_KEPT decks)."""
    with _DECK_STEPS_LOCK:
        _DECK_STEPS[key] = tuple(step) if step else None
        while len(_DECK_STEPS) > DECK_STEPS_KEPT:
            _DECK_STEPS.pop(next(iter(_DECK_STEPS)))


def clear_deck_steps() -> None:
    with _DECK_STEPS_LOCK:
        _DECK_STEPS.clear()


brand.on_change(clear_deck_steps)


def kickers_on(lays, step) -> bool:
    """Whether every drawn kicker of `lays` is on `step`."""
    from .typography import step_key
    return all(step_key(r["step"]) == step_key(step) for r in kicker_records(lays))


def draw_kicker(im: Image.Image, x: float, baseline: float, text: str, font, colour=None, lay=None,
                name: str = "kicker", plates=None) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """A kicker: a short gold rule at mid-cap height, then the words in tracked gold capitals (the covers'
    eyebrow). A label, never a pill or a button. A cap-height vertical bar read as a letter I before the words
    ("I DID YOU KNOW?", 2026-10-29), so the rule lies flat."""
    colour = colour or colour_of("kicker.colour")
    ch = cap_height(font)
    track, rule, rule_gap, rule_thick = _kicker_tokens()
    bar_w, gap = max(8, round(ch * rule)), round(ch * rule_gap)
    thick = max(3, round(ch * rule_thick))
    mid = baseline - ch / 2
    rule_box = (round(x), round(mid - thick / 2), round(x) + bar_w - 1, round(mid - thick / 2) + thick - 1)
    plate, rec = plan_kicker(im, (x + bar_w + gap, baseline), text, font, colour, "ls", track=font.size * track,
                             rule=rule_box)
    if isinstance(getattr(lay, "meta", None), dict):
        lay.meta.setdefault("kickers", {})[name] = rec
    if plate is None:   # fail closed: no rule and no words under the gate (recorded)
        return im, (round(x), round(baseline - ch), round(x), round(baseline))
    ImageDraw.Draw(im).rectangle([round(x), round(mid - thick / 2), round(x) + bar_w - 1,
                                  round(mid - thick / 2) + thick - 1], fill=tuple(plate.fill))
    im = plate.draw(im)
    box = (round(x), min(plate.box[1], round(baseline - ch)), plate.box[2], max(plate.box[3], round(baseline)))
    if lay is not None:
        lay.add(name, box)
        lay.elements[-1].update(plate.record())
        if isinstance(getattr(lay, "meta", None), dict):   # the rule's own box (the swipe strip meets it)
            lay.meta[f"{name}_rule"] = [round(x), round(mid - thick / 2), round(x) + bar_w, round(mid - thick / 2) + thick]
    if plates is not None:
        plates.setdefault(name, []).append(plate)
    return im, box


def _fit_kicker(text: str, font, width: float) -> tuple[str, bool]:
    """A kicker that fits `width` at this size, whole: (text, False); else TextFitError (QF-23: it was shortened at
    a word with an ellipsis, and the kicker is checked copy like the rest of the frame)."""
    if not text or kicker_width(text, font) <= width:
        return text, False
    raise TextFitError(text, [text], font, width, 1)


def story_body_lines(body: str, asks_poll: bool = False) -> int:
    """How many lines a Story frame's body takes at its fixed size, as the renderer draws it (the poll frame
    drops the body's own questions)."""
    if asks_poll:
        body = " ".join(s for s in re.split(r"(?<=[.!?])\s+", body) if not s.rstrip().endswith("?"))
    if not body.strip():
        return 0
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    return len(wrap_text(d, " ".join(body.split()), get_font(brand.TEXT_FONT, STORY_BODY_PX), story_body_width()))


_AUXILIARY = re.compile(r"^(?:would|will|do|does|did|is|are|was|were|can|could|should|shall|have|has|had|may|might|"
                        r"must)\b", re.IGNORECASE)
_YES_NO = re.compile(r"^\W*(?:yes|no|yeah|nope|definitely|never)\b", re.IGNORECASE)


def poll_problems(poll: dict[str, Any]) -> list[str]:
    """What breaks the poll's own rule, "never a yes/no question" (round 4, 2026-11-02): options that answer yes or
    no ("Yes, safety first" / "No, I need daylight"), or a question opening on an auxiliary verb ("Would you ...?")
    that offers no "or" between choices. [] for a real either/or poll."""
    question = " ".join(str(poll.get("question") or "").split())
    options = [str(o) for o in poll.get("options") or []]
    out = []
    if sum(bool(_YES_NO.match(o)) for o in options) >= 2:
        out.append("the options answer yes or no")
    if question and _AUXILIARY.match(question) and not re.search(r"\bor\b", question, re.IGNORECASE):
        out.append("the question asks for yes or no")
    return out


# Words a line should not end on (round 4 review, 2026-11-02): an article, a conjunction or a short preposition
# leaves the reader holding a word that belongs to the next line ("Tell us in the comments and / follow ...").
# The system's one vocabulary (src/brand_system.py): the words a line never ends on and the conjunctions it ends on
# only as a last resort; both cost the same here (a soft rule: the rag search weighs them).
_DANGLING = LINE_END_NEVER | LINE_END_LAST_RESORT
RAG_DANGLE = 0.3   # a line ending on one of them costs as much as a line this share of `width` off the mean


def dangles(line: str) -> bool:
    """True when the line ends on a word in _DANGLING (punctuation after it means it closes a phrase: not counted)."""
    words = line.split()
    return bool(words) and words[-1].lower() in _DANGLING


def even_rag(d, lines: list[str], font, width: float, measure: int) -> list[str]:
    """The same words on the same number of lines, broken for the evenest rag (round 4, 2026-11-02): the least
    spread of line widths, every line within `width` px and `measure` characters, never a one-word last line where
    the given lines avoid it. A short first line over a full second ("Visible from space, the") is evened out.
    A line that ends on an article, conjunction or short preposition (dangles) costs RAG_DANGLE of the width
    squared, in the given lines too, so an even rag never buys its evenness with a dangling "and".
    Words split at ordinary spaces only, so a figure glued to its unit stays whole. Unchanged when no other break
    is better."""
    from itertools import combinations
    words = " ".join(lines).split(" ")
    n = len(lines)
    if n < 2 or len(words) > 24 or len(words) <= n:
        return lines
    widths = {}

    def w(a: int, b: int) -> float:
        if (a, b) not in widths:
            widths[(a, b)] = d.textlength(" ".join(words[a:b]), font=font)
        return widths[(a, b)]

    from . import line_policy
    bonds, seams, names = line_policy.keep_together(words), line_policy.range_seams(words), line_policy.name_bonds(words)

    def cost(cand):
        ws = [w(a, b) for a, b in cand]
        mean = sum(ws) / len(ws)
        # A last line longer than the one above it reads as an inverted rag: counted as much again.
        dangle = sum(dangles(" ".join(words[a:b])) for a, b in cand[:-1]) * (RAG_DANGLE * width) ** 2
        # freeze cycle 5 (TEXT stream): the one policy before the rag: an even rag never buys its evenness with a
        # number parted from its unit or a line ending on a function word (it re-broke glued figures: the lines come
        # back with ordinary spaces)
        # Review D1: a range seam is a last resort like a function-word end, never the equal of a number parted from
        # its unit (the rag chose "nearly 1,090 / tonnes" over "tonnes / to 1,168"); a proper name is not parted to
        # avoid a function-word end (line_policy.line_cost: unit split > never end > name split > last resort).
        hard = line_policy.line_cost(words, [b for _, b in cand[:-1]], bonds, seams, names)
        return ((64 * hard[0] + 16 * hard[1] + 4 * hard[2] + hard[3]) * (4 * width) ** 2
                + sum((x - mean) ** 2 for x in ws) + max(0.0, ws[-1] - ws[-2]) ** 2 + dangle)
    best, best_cost = None, None
    for cuts in combinations(range(1, len(words)), n - 1):
        bounds = list(zip((0,) + cuts, cuts + (len(words),)))
        if any(w(a, b) > width or len(" ".join(words[a:b])) > measure for a, b in bounds):
            continue
        if bounds[-1][1] - bounds[-1][0] == 1 and len(lines[-1].split(" ")) > 1:
            continue
        c = cost(bounds)
        if best_cost is None or c < best_cost - 1e-6:
            best, best_cost = bounds, c
    if best is None:
        return lines
    out = [" ".join(words[a:b]) for a, b in best]
    return out if cost(best) < cost(list(_bounds(lines))) - 1e-6 else lines


def _bounds(lines: list[str]):
    k = 0
    for ln in lines:
        m = len(ln.split(" "))
        yield (k, k + m)
        k += m


def split_balanced(text: str) -> tuple[str, str]:
    """A display line in two at the break the heading engine prefers (src/heading.py, round 7: before a stop word,
    never a lone last word, a figure kept with its unit), else at the most even word boundary."""
    words = text.split()
    if len(words) < 2:
        return text, ""
    best = ideal_seam(words)
    if best is None:
        best = min(range(1, len(words)), key=lambda k: abs(len(" ".join(words[:k])) - len(" ".join(words[k:]))))
    return drop_break_comma(" ".join(words[:best])), " ".join(words[best:])


def drop_break_comma(line: str) -> str:
    """A display line that a break ends drops its trailing comma: the break (and, two-tone, the colour change)
    already makes the pause, and at the headline's tight leading the comma's tail lands on the next line's cap
    height ("CARVED," over "NOT BUILT" read as one joined mark). Other end punctuation is kept."""
    return line[:-1].rstrip() if line.endswith(",") else line


def _rel_luminance(rgb) -> float:
    def ch(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb[:3]
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def ensure_legible(im: Image.Image, box: tuple[int, int, int, int], text_rgb=None,
                   target: float = 4.5, feather: int = 60) -> Image.Image:
    """Darkens the area behind a block of light text until the text reaches WCAG contrast `target`
    against the bright end of the background there (its 90th-percentile luminance), with feathered
    edges so no box shows. Bright photos get a deeper shade; dark ones are left alone."""
    import numpy as np
    x0, y0, x1, y1 = [int(v) for v in box]
    region = np.asarray(im.convert("RGB").crop((x0, y0, x1, y1)), dtype=np.float32) / 255.0
    weights = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)

    def bright_end(alpha: float) -> float:  # 90th-percentile luminance once shaded by `alpha`
        v = region * (1.0 - alpha)
        lin = np.where(v <= 0.03928, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)
        return float(np.percentile(lin @ weights, 90))

    text_rgb = brand.LEGIBLE_TEXT if text_rgb is None else text_rgb   # the light text assumed (#EBEBEB)
    needed = (_rel_luminance(text_rgb) + 0.05) / target - 0.05  # the brightest background that still gives `target`
    if bright_end(0.0) <= needed:
        return im
    lo, hi = 0.0, 0.92
    for _ in range(12):
        mid = (lo + hi) / 2
        lo, hi = (lo, mid) if bright_end(mid) <= needed else (mid, hi)
    alpha = min(0.92, hi + 0.03)
    from PIL import ImageChops, ImageFilter
    level = round(255 * alpha)
    box_only, grown = Image.new("L", im.size, 0), Image.new("L", im.size, 0)
    ImageDraw.Draw(box_only).rectangle([x0, y0, x1, y1], fill=level)
    ImageDraw.Draw(grown).rectangle([x0 - feather, y0 - feather, x1 + feather, y1 + feather], fill=level)
    # Full strength over the whole box (text may sit at its edges), fading out over `feather` px outside it
    shade = ImageChops.lighter(grown.filter(ImageFilter.GaussianBlur(feather / 3)), box_only)
    black = Image.new("RGB", im.size, (0, 0, 0))
    return Image.composite(black, im.convert("RGB"), shade)


SHARE_WEIGHT = 700   # the closing slide's share-and-follow line: Montserrat Bold (round 4). The panel asked for
                     # SemiBold, but every Montserrat line is already 600 (typography.VARIABLE_WEIGHTS), the question
                     # with it; one step heavier is what sets the ask apart as a second voice.

_render_state = threading.local()   # the slide being drawn on this thread: its subject


def _platform_text_contrast(im: Image.Image, lay) -> dict[str, float]:
    """Each text element's lowest ratio on `im` as delivered: saved as the slide is (CAROUSEL_JPEG), then the
    platform's re-encode (platform_encode); {element name: ratio}."""
    import io
    plates = getattr(lay, "plates", None) or {}
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", **MultiFormatGenerator.CAROUSEL_JPEG)
    buf.seek(0)
    with Image.open(buf) as back:
        delivered = platform_encode(back.convert("RGB"))
    out: dict[str, float] = {}
    for name, ps in plates.items():
        vals = [v for v in (q.measure(delivered) for q in ps if not q.empty) if v is not None]
        if vals:
            out[name] = round(min(vals), 2)
    return out


def _moved_windows(lay) -> list[dict[str, Any]]:
    """The photo-window records of a drawn slide or Story frame whose window was moved (photo_fitness.place)."""
    meta = getattr(lay, "meta", None) or {}
    recs = [w.get("fit") or {} for w in (meta.get("windows") or [])]
    if isinstance(meta.get("window"), dict):
        recs.append(meta["window"].get("fit") or {})
    return [r for r in recs if r.get("moved")]


def _fit_records(lay) -> list[dict[str, Any]]:
    """The photo-window fit records of a drawn slide or Story frame, in order."""
    meta = getattr(lay, "meta", None) or {}
    recs = [w.get("fit") for w in (meta.get("windows") or []) if isinstance(w.get("fit"), dict)]
    if isinstance(meta.get("window"), dict) and isinstance(meta["window"].get("fit"), dict):
        recs.append(meta["window"]["fit"])
    return recs


def keep_text_contrast(draw, im: Image.Image, lay):
    """Review of cycle 5, B3 / B4: a window moved by the photo-fitness rule may not cost the text its legibility.
    `draw()` draws the same slide or Story frame again; when (im, lay) holds a moved window, the slide is drawn again
    with every window as asked (_render_state.asked_only), and each text element is measured on both as delivered
    (_platform_text_contrast). The moved drawing stands unless an element ends under brand.TEXT_CONTRAST lower than
    as asked, or the lowest ratio of the slide falls; then the asked drawing is taken (the order of the brief:
    legibility before subject visibility) and its window records say why ("move_refused"). Both drawings' ratios are
    recorded."""
    moved = _moved_windows(lay)
    if not moved or getattr(_render_state, "asked_only", False):
        return im, lay
    from . import plate_form
    if plate_form.active() != "base" and not getattr(_render_state, "shipped_ground", False):
        # PLATEFORM (lander ruling 1, review of cycle 6): the photograph's window never moves because of the form.
        # The move test is decided on the shipped ground (both drawings drawn with scrim laying the band); the frame
        # is then drawn with the block at the window that test chose, its records carrying the test's
        _render_state.shipped_ground = True
        try:
            im_s, lay_s = draw()
            im_c, lay_c = keep_text_contrast(draw, im_s, lay_s)
        finally:
            _render_state.shipped_ground = False
        refused = [r for r in _fit_records(lay_c) if r.get("move_refused")]
        if not refused:
            for r, c in zip(_fit_records(lay), _fit_records(lay_c)):
                if "text_contrast" in c:
                    r["text_contrast"] = c["text_contrast"]
            return im, lay
        _render_state.asked_only = True
        try:
            im0, lay0 = draw()
        finally:
            _render_state.asked_only = False
        for r, c in zip(_fit_records(lay0), _fit_records(lay_c)):
            for key in ("move_refused", "text_contrast"):
                if key in c:
                    r[key] = c[key]
        return im0, lay0
    _render_state.asked_only = True
    try:
        im0, lay0 = draw()
    finally:
        _render_state.asked_only = False
    now, asked = _platform_text_contrast(im, lay), _platform_text_contrast(im0, lay0)
    # Lander rulings (second and third reviews): the move is refused only when (a) a text element ends under its
    # floor after the re-encode AND lower than as asked, or (b) the slide's LOWEST text ratio falls. One element
    # dropping while the lowest holds is the move's ordinary price (a masthead from 5.21 to 5.00 while the body and
    # heading rose kept every move off), and an element already under its floor as asked may rise and stay under.
    under = sorted(n for n in now if now[n] < brand.TEXT_CONTRAST and now[n] < asked.get(n, 99.0) - 0.005)
    common = [n for n in now if n in asked]
    falls = bool(common) and min(now[n] for n in common) < min(asked[n] for n in common) - 0.005
    lower = under + ([f"lowest {min(now[n] for n in common):.2f} < {min(asked[n] for n in common):.2f}"] if falls
                     else [])
    check = {"moved": {n: now[n] for n in sorted(now)}, "asked": {n: asked[n] for n in sorted(asked)}}
    if not lower:
        for r in moved:
            r["text_contrast"] = check
        return im, lay
    why = (f"the moved window costs the text its legibility after the platform re-encode ({', '.join(lower)}: under "
           f"{brand.TEXT_CONTRAST}:1, or the lowest ratio falls): the window is drawn as asked (rule: a moved window "
           f"keeps the text legible, src/multi_format.py)")
    meta0 = getattr(lay0, "meta", None) or {}
    recs0 = [w.get("fit") for w in (meta0.get("windows") or []) if isinstance(w.get("fit"), dict)]
    if isinstance(meta0.get("window"), dict) and isinstance(meta0["window"].get("fit"), dict):
        recs0.append(meta0["window"]["fit"])
    for r in recs0:
        r["move_refused"] = why
        r["text_contrast"] = check
    return im0, lay0


def _holds_text_contrast(draw):
    """A slide or Story frame renderer whose moved photo window is held to the text's contrast (keep_text_contrast):
    `draw(self, ...)` -> (image, layout)."""
    import functools

    @functools.wraps(draw)
    def guarded(self, *args, **kw):
        _render_state.frame_form = None   # PLATEFORM: this frame's form is its own (a split slide lays none)
        im, lay = draw(self, *args, **kw)
        return keep_text_contrast(lambda: draw(self, *args, **kw), im, lay)
    return guarded


# Lane D cycle 4 (AD-04 + PL-ri3-wedge: the Richat slide 3 showed the Apollo 9 photograph's black window wedge down its
# left edge) found a source's own FRAME (digital black touching the picture's edge) and moved the window off it. Freeze
# cycle 5: the frame is one measure of the photo-fitness rule (src/photo_fitness.py: matte_mask, MATTE_*), judged
# together with the subject the move must keep; _fill and story_window ask it.


class PhotoMatchFailed(RuntimeError):
    """The carousel's photos could not be matched to its slides (MultiFormatGenerator._match_photos, strict)."""


class NoRealPhotos(RuntimeError):
    """A Story or carousel has no real photo to be drawn on: every picture it could use is AI-made (QF-65c). It is
    not made (held with this reason), never padded with an illustration."""


class MultiFormatGenerator:
    def generate_story_sequence(self, hero_img_path: Path, script_payload: dict[str, Any], output_dir: Path,
                                scene_images: list[Path] = None, lookup=None) -> list[Path]:
        """Two Story frames, each on its own matched photo: hook and reveal (STORY_FRAMES; QF-80). The script's
        third slide, the poll's question, is not drawn: the native poll sticker can't be posted through the API.
        Text sits on the calmest band of the photo inside Story-safe zones (clear of the profile bar
        at the top and the reply bar at the bottom), over a soft gradient rather than a box.
        An AI-made picture (ai_label.is_ai_picture: an illustration, or a copy of one) is never a frame, the hero
        fallback included; with no real photo left, NoRealPhotos is raised and no frame is drawn (QF-65c).
        A photo whose credit cannot be set whole and legibly on a frame is not used either (QF-126c,
        story_credit_fits); with none left, StoryCreditFitError holds the Story. `lookup`: the Commons page-id lookup
        (credits.short_link_lookup()) for the shortest address of a photo's page, recorded in the run's folder; None
        uses only the ids already recorded there."""
        from .ai_label import is_ai_picture, no_real_photo_reason, real_stills
        from .credits import find_page_ids, source_page_ids
        out_dir = output_dir / "stories"
        out_dir.mkdir(parents=True, exist_ok=True)
        stills, left_out = real_stills([p for p in (scene_images or []) if not is_video(p)])
        hero_real = bool(hero_img_path) and not is_video(hero_img_path) and not is_ai_picture(hero_img_path)
        if hero_img_path and not hero_real and not is_video(hero_img_path):
            left_out.append(hero_img_path)
        if not stills and not hero_real:
            raise NoRealPhotos(no_real_photo_reason(left_out, "Story frames"))
        candidates = list(dict.fromkeys(stills + ([hero_img_path] if hero_real else [])))
        if lookup is not None:
            find_page_ids(output_dir, [photo_credit(p) for p in candidates], lookup)
        pages = source_page_ids(output_dir)
        creditable = {p for p in candidates if story_credit_fits(p, pages)}
        if not creditable:
            raise StoryCreditFitError(len(candidates))
        if len(creditable) < len(candidates):
            log.info("Story frames: %d photo(s) left out, their credit too long to set whole on a frame: %s",
                     len(candidates) - len(creditable), ", ".join(Path(p).name for p in candidates if p not in creditable))
        stills = [p for p in stills if p in creditable]
        hero_real = hero_real and hero_img_path in creditable
        stills = stills or ([hero_img_path] if hero_real else [])
        slides_data = script_payload["story_slides"][:len(STORY_FRAMES)]
        cards = [{"heading": f"{s['line1']} {s['line2']}", "body": s["body"]} for s in slides_data]
        photos = (self._match_photos(cards, stills) if len(stills) > 1
                  else [hero_img_path if hero_real else stills[0]] * len(cards))
        names = list(STORY_FRAMES)
        for name in RETIRED_STORY_FRAMES:  # an earlier render's poll frame in this folder is not left beside them
            (out_dir / name).unlink(missing_ok=True)
        poll: dict[str, Any] = {}  # no frame asks the poll: it would turn the reveal into a question frame
        # One unit per run: figures in the unit the narration speaks (2026-10-29); matched to photos as written.
        # Cycle 0 round 8: one lead unit per run (the signed scale fact's system decides; figures.deck_lead).
        narration = script_payload.get("narration_script") or ""
        from .figures import deck_lead
        lead = deck_lead(script_payload)
        slides_data = [{k: (spoken_units(v, narration, lead) if isinstance(v, str) else v) for k, v in s.items()}
                       for s in slides_data]
        # Lane B c3b: each frame's two title lines and body through the one pre-draw figure gate
        # (visual_engine.gate_drawn), as drawn; a frame left with nothing readable holds the Story (gate_deck)
        # Lane B c3c: a figure that lacks only its signed hedge is drawn with it where the frame holds it at the
        # Story's own size rules: the Story's one title size unchanged (else every hedged frame takes the plain form)
        # and the frame drawn without a problem or a shortened field (else that frame takes the plain form)
        story_hedged: list[dict[str, Any]] = []
        slides_data, _, story_rows = gate_deck(slides_data, None, script_payload, "Story", ("line1", "line2", "body"),
                                               ("line1", "line2"), "frame", hedged=story_hedged)
        layout = []  # each frame's text block, for the audit and the evidence renders
        title_px = story_title_px(slides_data, poll=poll)   # round 7 (F10): one title size per Story
        if story_hedged and story_title_px(plain_deck(slides_data, story_hedged), poll=poll) != title_px:
            story_rows += revert_hedges(slides_data, {}, story_hedged, {h["slide"] for h in story_hedged},
                                        script_payload, "Story", "frame")
            title_px = story_title_px(slides_data, poll=poll)

        from . import still_layout
        slides = []

        def draw_frames(kicker_step):
            """Every frame drawn (not saved) with the Story's kicker step (None: each its own, surveyed)."""
            done = {}
            for i, slide in enumerate(slides_data):
                try:
                    with surface(f"Story frame {i + 1}"), kicker_set(kicker_step, survey=kicker_step is None):
                        done[i] = still_layout.drawn(
                            lambda tight, i=i, slide=slide: self._render_story_frame(
                                photos[i], slide, i, len(slides_data), poll, tight=tight, title_px=title_px,
                                **({"pages": pages} if pages else {})))
                except GlyphRefused as e:
                    done[i] = e
            return done

        # Cycle 0 round 8 (R1, one treatment per set): a Story's kickers are one set, like a deck's
        # (generate_carousel_sequence): one step that holds on every frame's ground, colour kept where it holds.
        frames_drawn = draw_frames(None)
        misfit = {h["slide"] for h in story_hedged if not _hedged_fits(frames_drawn[h["slide"] - 1])}
        if misfit:
            story_rows += revert_hedges(slides_data, {}, story_hedged, misfit, script_payload, "Story", "frame")
            title_px = story_title_px(slides_data, poll=poll)
            frames_drawn = draw_frames(None)
        from .visual_engine import record_hedged
        record_hedged(script_payload, story_hedged)
        story_lays = [v[1] for v in frames_drawn.values() if not isinstance(v, GlyphRefused)]
        story_step = deck_kicker_step(story_lays)
        if story_step is not None and not kickers_on(story_lays, story_step):
            frames_drawn = draw_frames(story_step)
        for i, (slide, name) in enumerate(zip(slides_data, names)):
            recipe = {"renderer": "multi_format.redraw_story_frame", "photo": str(photos[i]), "slide": slide,
                      "index": i, "count": len(slides_data), "poll": poll, "title_px": title_px,
                      **({"kicker_step": list(story_step)} if story_step else {})}
            path = out_dir / name
            got = frames_drawn[i]
            if isinstance(got, GlyphRefused):
                e = got
                # QF-94: a field on this frame (its copy, or its photo's credit) no bundled face can draw: the frame
                # is refused, never drawn with a letter (or an author's name) missing. The run's glyph ledger
                # carries the reason to the review card; the other frames go on.
                log.warning("Story frame %s refused: %s", name, e.reason)
                path.unlink(missing_ok=True)          # never a frame left over from an earlier render
                layout.append({"frame": name, "refused": e.reason, "chars": e.missing, "scripts": e.scripts,
                               "text": e.text})
                continue
            base, lay, attempts = got
            if isinstance(getattr(lay, "meta", None), dict):
                lay.meta["kicker_set"] = {"step": list(story_step) if story_step else None, "frames": len(slides_data)}
                held = slide_withheld(story_rows, "Story", i + 1, "frame")
                if held:   # lane B c3b: the figures the gate withheld from this frame, on its own record
                    lay.meta["figure_withheld"] = recipe["figure_withheld"] = held
                hedges = slide_hedged(story_hedged, "Story", i + 1, "frame")
                if hedges:   # lane B c3c: the figures drawn with their signed hedge, on the frame's own record
                    lay.meta["figure_hedged"] = recipe["figure_hedged"] = hedges
            layout.append({**lay.meta, "frame": name})
            base.save(path, quality=95)
            _delivered_contrast(lay, path)
            still_layout.save(path, lay, recipe, attempts)
            slides.append(path)
        (out_dir / "layout.json").write_text(json.dumps(layout, indent=2), encoding="utf-8")

        # Multi-platform publishing manifest with dedicated platform packages
        hashtags_list = script_payload.get("hashtags") or ["#Archaeology", "#AncientHistory", "#HistoryBuff", "#AncientTechnology"]
        hashtags_str = " ".join(hashtags_list)
        first_comment = script_payload.get("first_comment", "")
        title = script_payload.get("title", "")
        clean_title = re.sub(r"[^\w\s\-\:\,\']", "", title).strip()
        if len(clean_title) > 60:
            clean_title = clean_title[:57].rstrip() + "..."

        ig_caption = script_payload.get("caption", "")
        fb_caption = f"{clean_title}\n\n{first_comment}\n\nWeigh in with your thoughts in the comments."  # no "below" (operator, 2026-10-28)
        yt_desc = f"{title}\n\n{script_payload.get('narration_script', '')}\n\n{first_comment}\n\n{hashtags_str}"
        yt_tags = [h.lstrip("#").lower() for h in hashtags_list] + ["archaeology", "ancient history", "documentary", "history mysteries"]

        companion_manifest = {
            "story_frames": [p.name for p in slides],  # no story_poll_sticker: no frame keeps a room for one (QF-80)
            # What each frame credits on itself (Stories carry no caption; story_credit, 2026-10-29)
            "story_credits": {e["frame"]: e["photo_credit"].get("lines", []) for e in layout if "refused" not in e},
            "first_comment": first_comment,
            "platforms": {
                "instagram_reels": {
                    "caption": ig_caption,
                    "hashtags": hashtags_list,
                    "first_comment": first_comment,
                    "recommended_sound": "Original Audio",
                },
                "facebook_reels": {
                    "caption": fb_caption,
                    "first_comment": first_comment,
                    "discussion_prompt": first_comment,
                },
                "youtube_shorts": {
                    "title": clean_title,
                    "description": yt_desc,
                    "tags": yt_tags,
                    "pinned_comment": first_comment,
                },
            },
            "note": "Multi-platform publishing manifest: the Story frames and their credits, Facebook MSI discussion copy, and YouTube Shorts SEO metadata.",
        }

        with open(output_dir / "companion_posting.json", "w", encoding="utf-8") as f:
            json.dump(companion_manifest, f, indent=2, ensure_ascii=False)

        log.info("Generated %d Story frames -> %s", len(slides), out_dir)
        return slides

    @_holds_text_contrast
    def _render_story_frame(self, photo: Path, slide: dict[str, Any], i: int, count: int, poll: dict[str, Any],
                            tight: bool = False, pages: dict[str, int] | None = None, title_px: int | None = None):
        """One Story frame and its measured layout (visual overhaul, 2026-10-29): a left-aligned editorial column
        at STORY_MARGIN, a kicker (gold bar, tracked gold capitals), a two-tone Anton title (bone, then gold), the
        Montserrat body in balanced lines, and on the first frame the pointer to the Reel. Every block is measured
        with the font's real metrics before anything is drawn and placed inside the Story box (safe_zones.STORY:
        Meta's 14% top, 35% bottom, 6% sides), on the calmest band of the photo; an adaptive ink gradient sits
        behind that band only, and each line is drawn at a measured contrast (typography.place_text: >= 4.5:1 on
        the pixels touching its glyphs), the ratio recorded with its box for the post-draw check (still_layout).
        `tight`: the second, tighter fit (smaller title and kicker). A photo whose licence asks for attribution is
        credited at the foot of the box (story_credit), recorded as the `photo_credit` element and in the frame's
        layout.json entry. `pages`: the run's known Commons page ids (the credit's source address, QF-126c)."""
        from .safe_zones import safe_box
        from .still_layout import Layout
        sx0, sy0, sx1, sy1 = safe_box("story", VIDEO_WIDTH, VIDEO_HEIGHT)
        x = STORY_MARGIN
        max_w = story_body_width()
        asks_poll = i == count - 1 and len(poll.get("options", [])) >= 2
        lay = Layout("story", (VIDEO_WIDTH, VIDEO_HEIGHT), (sx0, sy0, sx1, sy1), tight=tight,
                     protected=[("poll_sticker_room", (0, POLL_STICKER_ROOM[0], VIDEO_WIDTH, POLL_STICKER_ROOM[1]))]
                     if asks_poll else None)
        lay.plates = {}
        base, window = story_window(photo, (x, sy0, x + max_w, sy1))
        d = ImageDraw.Draw(base)
        # The photo's credit, when its licence asks for one (story_credit; 2026-10-29): at the foot of the Story box,
        # above the reply bar and clear of the poll sticker's room, which both sit lower. Set in the data face (IBM
        # Plex Mono), left-aligned on the text column; its backing is shaded only as far as 4.5:1 needs, before any
        # text is drawn; the frame's text block then stays above it.
        credit = dict(story_credit(photo, pages))
        text_floor = sy1
        from . import plate_form as _pf
        formed = _pf.active() != "base"   # PLATEFORM: the block family (one form for the frame's text and credit)
        credit_items: list = []
        band_on = None
        c_fill = brand.STONE_LT
        if credit["required"]:
            # at most STORY_CREDIT_LINES lines, never under 22 px; "fits" False when it takes more (QF-126c: the
            # Story does not choose such a photo; a redraw that meets one draws it whole and the report holds it)
            setting = story_credit_fit(d, credit["lines"], max_w)
            c_px, font_c, c_drawn = setting["px"], setting["font"], setting["drawn"]
            credit["fits"] = setting["fits"]
            c_lead = _story_credit_lead(c_px)
            c_bottom = sy1 - 10
            c_top = c_bottom - len(c_drawn) * c_lead
            c_w = max(text_size(d, ln, font_c)[0] for ln in c_drawn)
            backing = (x - 16, c_top - 8, x + c_w + 16, c_bottom + 6)
            before = _contrast(base, backing, c_fill)
            # A soft band of ink across the frame, not a box (a feathered rectangle read as a caption bar, 2026-10-29);
            # each line then gets its own halo if the photo still shows through too brightly.
            from . import band_edge
            need_c = band_edge.text_strength(base, [(d.textbbox((x, c_top + k * c_lead), ln, font=font_c, anchor="la"),
                                                     c_fill) for k, ln in enumerate(c_drawn)], brand.INK)
            got_c: dict = {}
            # not graded (ruling N2): the credit's small data face sits in the band's fall-off, and a graded ground there
            # read 0.05-0.25 lower after the platform re-encode
            c_items = [(d.textbbox((x, c_top + k * c_lead), ln, font=font_c, anchor="la"), c_fill, "credit")
                       for k, ln in enumerate(c_drawn)]
            c_band = scrim(base, c_top - 10, c_bottom + 8, feather=90, target=0.16, cap=0.85, floor=0.0, out=got_c,
                           grade=False, plain=c_items, shipped=formed)
            if formed:
                # the block family (lander ruling 4, review of cycle 6): the credit is a block of the frame's own
                # family (its left edge, width snap and gap, one choice for the frame, in the frame's record); its
                # shipped band is what the frame's band is laid on
                credit_items, band_on = c_items, c_band
            else:
                base = c_band
            credit["text_band"] = text_band_record(need_c, got_c.get("strength", 0.0))
            d = ImageDraw.Draw(base)
            credit.update(drawn=c_drawn, px=c_px, backing=list(backing), contrast_unshaded=before,
                          contrast=_contrast(c_band, backing, c_fill),
                          complete=all(drawable(line, font_c) == line for line in credit["lines"]))  # no glyph missing
            text_floor = c_top - STORY_CREDIT_GAP
        kicker = typeset(slide["kicker"]).upper()
        l1, l2 = story_fields(slide, poll if asks_poll else None)
        body = typeset(slide["body"])
        if asks_poll:  # one question per frame: the poll's; the body keeps only its context
            body = " ".join(s for s in re.split(r"(?<=[.!?])\s+", body) if not s.rstrip().endswith("?"))

        # Measured first: the kicker on one line; the title in Anton at its design size on up to STORY_TITLE_LINES
        # lines of STORY_TITLE_W (2026-11-02: three lines before it shrinks), bone then gold; the body at its fixed
        # size in balanced lines. The reveal frame with a measured figure is set as the covers' monument instead:
        # the figure huge in gold, its unit (and what the line says of it) under it, the other line as the title.
        k_px = 34 if tight else round(type_px("kicker", "tall"))
        font_k = get_font(brand.TEXT_FONT, k_px, weight=tok("kicker.weight"))
        while kicker and kicker_width(kicker, font_k) > max_w and font_k.size > 24:
            font_k = get_font(brand.TEXT_FONT, font_k.size - 2, weight=tok("kicker.weight"))
        kicker, k_short = _fit_kicker(kicker, font_k, max_w)
        cap_k = cap_height(font_k)
        k_gap = kicker_gap(font_k.size)                           # F20: the one kicker gap
        hero = story_figure(l1, l2) if i == 1 and not asks_poll else None
        b_step = round(STORY_BODY_PX * brand_system.SPEC["lead.body"])
        b_gap = round(STORY_BODY_PX * tok("band.body_gap_tall"))  # title's last baseline to the body's first (F3)
        font_p = get_font(brand.TEXT_FONT, 32 if tight else 36, weight=800)
        pointer = i == 0 and story_tap_target()   # ruling 2026-11-26: no pointer line without a tap target
        sticker = STORY_STICKER_H if asks_poll else 0
        room = text_floor - (sy0 + 30)                           # the height the whole block may take

        title_w = [STORY_TITLE_W]   # the title's measure; the text column only as the last resort (below)

        def title_plan(size: int, wrap: bool = True, fig_from: int = 0):
            """(rows, height) of the title at `size`: rows of (name, baseline offset from the title's top, text,
            font, fill, x offset). `fig_from`: the reveal figure's first step tried (STORY_FIGURE_STEPS[fig_from:])."""
            out, y = [], 0
            f = get_font(brand.DISPLAY_FONT, size)

            def field_rows(name: str, text: str, gold: bool) -> None:
                """One title field set by the engine at the Story's one size: one line, else a valid break (before a
                stop word, no lone-word row); `gold`: the payoff field, gold but for the function words that open it."""
                nonlocal y
                form = set_field(text, lambda t, sz: d.textlength(t, font=get_font(brand.DISPLAY_FONT, sz)),
                                 title_w[0] if wrap else 10 ** 6, size, max_lines=2 if wrap else 1)
                if form is None and wrap:   # the break gives way before the size (a lone row, recorded), never under
                    form = set_heading(text, lambda t, sz: d.textlength(t, font=get_font(brand.DISPLAY_FONT, sz)),
                                       title_w[0], (size,), max_lines=1, locked_lines=2, balance_min=0.0)
                if form is None:   # no break at this size: the field on one line (fits() then refuses it)
                    form = Form(tuple(text.split()), (), size, 0)
                for line_runs in field_runs(text, form.cuts, gold):
                    # lane D cycle 3 (stress realism, reachable on real copy: the Gol Gumbaz Story 'WITHOÚT
                    # TENSION' at 1.19, the Antikythera kicker over an accented title at 1.02): a line's accents
                    # keep the gap to the kicker or the line above, the pitch growing by their rise
                    y += accent_rise(d, " ".join(r for r, _ in line_runs), f)
                    y += cap_height(f) if not out else round(cap_height(f) * brand_system.SPEC["lead.display"])
                    dx = 0
                    for run, role in line_runs:
                        out.append((name, y, run, f, brand.GOLD if role in GOLD_ROLES else brand.BONE, dx))
                        dx += round(d.textlength(run + " ", font=f))

            if hero:
                # The qualifier and the unit are one matched pair (round 3, 2026-11-02): one face, one size, so three
                # type tiers no longer stack around one number. Lane D cycle 1: the figure on the figure role's steps
                # (STORY_FIGURE_STEPS, the largest whose numeral fits the measure; the least when none does, which
                # fits() refuses), the pair at story_figure_pair_px, the title the Story's one size and a bone field
                # (the figure is the frame's gold read; a quantity in it keeps the one gold scope, field_runs).
                f_fig = next((get_font(brand.DISPLAY_FONT, s) for s in STORY_FIGURE_STEPS[fig_from:]
                              if d.textlength(hero["number"], font=get_font(brand.DISPLAY_FONT, s)) <= title_w[0]),
                             get_font(brand.DISPLAY_FONT, STORY_FIGURE_STEPS[-1]))
                f_unit = get_font(brand.DISPLAY_FONT, story_figure_pair_px())
                q_font = f_unit
                if hero["title"]:
                    field_rows("title_1", hero["title"], False)
                # Round 4 (2026-11-02): the qualifier sits inline before the numeral, on its baseline, at the unit's
                # size ("ABOUT 280"): on a line of its own it read as a one-word headline line. Stacked above the
                # numeral only when the inline pair is wider than the title's measure.
                q_w = d.textlength(hero["qualifier"], font=q_font) + round(cap_height(q_font) * STORY_INLINE_GAP) \
                    if hero["qualifier"] else 0
                inline = bool(hero["qualifier"]) and q_w + d.textlength(hero["number"], font=f_fig) <= title_w[0]
                if hero["qualifier"] and not inline:
                    y += (34 if out else 0) + cap_height(q_font)
                    out.append(("figure_qualifier", y, hero["qualifier"], q_font, brand.BONE, 0))
                y += (STORY_FIGURE_GAP if out else 0) + cap_height(f_fig)
                if inline:
                    out.append(("figure_qualifier", y, hero["qualifier"], q_font, brand.BONE, 0))
                out.append(("figure", y, hero["number"], f_fig, figure_colour(), round(q_w) if inline else 0))
                if hero["unit"]:
                    y += STORY_FIGURE_GAP + cap_height(f_unit)
                    # Under the numeral it measures (inline: indented past the qualifier, so "FT DEEP" reads with "280").
                    # Cycle 0 round 8 (ruling 8): the unit is gold with its number; the dimension word ("DEEP") bone.
                    # rules2 F5: which unit words are gold is the one gold scope's word (figures.gold_word_indices on
                    # the whole figure: qualifier, number and unit), never a local split
                    from .figures import gold_word_indices
                    lead = (hero["qualifier"].split() if hero["qualifier"] else []) + hero["number"].split()
                    uw = hero["unit"].split()
                    gold_set = {i - len(lead) for i in gold_word_indices(lead + uw) if i >= len(lead)}
                    ux = round(q_w) if inline else 0
                    for part, is_gold in unit_runs(hero["unit"], gold_set):
                        out.append(("figure_unit", y, part, f_unit, figure_colour() if is_gold else brand.BONE, ux))
                        ux += round(d.textlength(part + " ", font=f_unit))
                return out, y
            # Round 7 (F1, F10): each field set by the engine at the Story's one size; the second field is the payoff.
            for name, text, gold in (("title_1", l1, False), ("title_2", l2, True)):
                if text:
                    field_rows(name, text, gold)
            return out, y

        def fits(plan) -> bool:
            rows_, _ = plan
            texts = {r[1] for r in rows_ if not r[0].startswith("figure")}
            return (len(texts) <= (2 if hero else STORY_TITLE_LINES)
                    and all(r[5] + d.textlength(r[2], font=r[3]) <= title_w[0] for r in rows_))

        # Round 7 (F10): ONE title size per Story (story_title_px: the least over its frames, never under the ruled
        # one-line floor), one line a field at it, else the engine's break at it. Only a block the box cannot hold at
        # that size steps down further (recorded as story_size_off).
        story_px = title_px if title_px else story_title_px([slide], tight=tight, poll=poll if asks_poll else None)
        # Never under the ruled floor (sweep S1: long titles drew at 76-104 px): below it the frame re-breaks at the
        # floor or is refused (QF-23: TextFitError), never drawn smaller.
        # Ruling (FREEZE-PARK round 4 (2), round 3 (3)(1)): one line a field down to heading.story_one_line_floor (106);
        # a title that wraps never under heading.story_floor (120).
        t_min, wrap_floor = GATES["heading.story_one_line_floor"], GATES["heading.story_floor"]
        t_size = max(t_min, min(story_px, 124)) if tight else max(t_min, story_px)
        # round 8 (TY-A-6): a wrapped title takes the tall display ladder's steps too, never a 2 px fit between them
        trials = [(t_size, False)] + [(round(sz), True) for sz in brand_system.display_steps("tall", wrap_floor)
                                      if sz <= max(t_size, wrap_floor)]
        # lane D cycle 2 (lander ruling 4: floors are gates): before anything else gives, one line a field on the
        # ladder's steps under the Story size, down to the one-line floor (story_size_off, recorded); the wrapped
        # title is never set under heading.story_floor (the old last resort drew 12 of 144 real frames wrapped at
        # 106 / 112). A frame no legal form holds is refused (QF-23), never drawn under a floor.
        trials += [(round(sz), False) for sz in brand_system.display_steps("tall", t_min) if round(sz) < t_size]
        f_body = get_font(brand.TEXT_FONT, STORY_BODY_PX, weight=STORY_BODY_WEIGHT)
        n_body = len(wrap_text(d, " ".join(body.split()), f_body, max_w)) if body.strip() else 0

        def choose(sticker_h: int, lines: int):
            fixed_ = (cap_k + k_gap if kicker else 0) + (110 if pointer else 0) + (
                sticker_h + 2 * STORY_STICKER_GAP if sticker_h else 0)
            need = (lines - 1) * b_step + b_gap + 12 if lines else 0
            for size_, wrap_ in trials:
                # the reveal: the title keeps the Story's size; the figure steps down its own steps first
                for ff in (range(len(STORY_FIGURE_STEPS)) if hero else (0,)):
                    plan = title_plan(size_, wrap_, ff)
                    if fits(plan) and plan[1] + fixed_ + need <= room:
                        return size_, wrap_, fixed_, ff
            return None
        got = choose(sticker, n_body)
        if got is None and sticker:
            # The question, the sticker's room under it and the whole body do not fit the box: the sticker goes
            # to POLL_STICKER_ROOM below the box, as before 2026-11-02, rather than the body being cut.
            sticker = 0
            got = choose(0, n_body)
        got = got or choose(sticker, min(n_body, STORY_BODY_LINES))
        if got is None and hero:
            # lane D cycle 1: a reveal whose figure sets on no step within the box is the two-tone title frame,
            # on the same ladder and floors, before anything is refused
            hero = None
            got = choose(sticker, n_body) or choose(sticker, min(n_body, STORY_BODY_LINES))
        if got is None:
            # lane D cycle 2 (lander ruling 4), the column step: as operator ruling 3 gives the 9:16 cards the box
            # column only where the floor would otherwise refuse, a frame no legal form holds on the title's measure
            # takes one line a field, on the ladder down to the floor, across the Story's text column (story_body_width,
            # the body's own column) before it is refused. Measured: 8 of the 9 real frames the old last resort drew
            # wrapped at 106 set on one line here.
            title_w[0] = max_w
            trials[:] = [(round(sz), False) for sz in brand_system.display_steps("tall", t_min) if round(sz) <= t_size]
            got = choose(sticker, n_body) or choose(sticker, min(n_body, STORY_BODY_LINES))
        got = got or (t_min, False, None, 0)   # one line a field at the floor: drawn whole or refused below
        size, wrap, fixed, fig_from = got
        if fixed is None:
            fixed = (cap_k + k_gap if kicker else 0) + (110 if pointer else 0) + (
                sticker + 2 * STORY_STICKER_GAP if sticker else 0)
        if asks_poll and not sticker:
            lay.meta_room = [0, POLL_STICKER_ROOM[0], VIDEO_WIDTH, POLL_STICKER_ROOM[1]]
        t_rows, t_height = title_plan(size, wrap, fig_from)
        s1 = s2 = False
        if not fits((t_rows, t_height)):   # even the floor does not fit: one line each, whole or refused (QF-23)
            l1, font_t, s1 = fit_line(d, l1, brand.DISPLAY_FONT, t_min, title_w[0], t_min)
            l2, _, s2 = fit_line(d, l2, brand.DISPLAY_FONT, t_min, title_w[0], t_min)
            hero = None
            ct = cap_height(font_t)
            b1 = ct + accent_rise(d, l1, font_t)                       # lane D cycle 3: accents keep the gaps
            t_rows = [r for r in (("title_1", b1, l1, font_t, brand.BONE, 0),
                                  ("title_2", b1 + round(ct * brand_system.SPEC["lead.display"]) + accent_rise(d, l2, font_t), l2, font_t,
                                   brand.GOLD, 0)) if r[2]]
            t_height = t_rows[-1][1] if t_rows else 0
        n_room = max(1, (room - fixed - t_height - b_gap - 16) // b_step + 1)   # 16: the last line's descenders
        body_lines, font_b, b_short = [], get_font(brand.TEXT_FONT, STORY_BODY_PX, weight=STORY_BODY_WEIGHT), False
        # Never smaller than its design size (operator, 2026-10-25). The script stage keeps each body within three
        # lines at this size (story_body_lines); a longer one takes more lines while the box has room, and the frame
        # is refused when it has none (QF-23: TextFitError, never cut at a sentence or a word).
        if body.strip():
            body_lines, font_b, b_short = probe_text(d, body, brand.TEXT_FONT, STORY_BODY_PX, max_w, STORY_BODY_LINES,
                                                   STORY_BODY_PX, quiet=True, weight=STORY_BODY_WEIGHT)
            if b_short:
                body_lines, font_b, b_short = fit_text(d, body, brand.TEXT_FONT, STORY_BODY_PX, max_w,
                                                       max(STORY_BODY_LINES, n_room), STORY_BODY_PX,
                                                       weight=STORY_BODY_WEIGHT)
        lay.shortened = [n for n, short in (("kicker", k_short), ("title_1", s1), ("title_2", s2), ("body", b_short)) if short]

        # Baselines, relative to the block's top.
        rows: list[tuple[str, float, str, Any, Any, float, int]] = []   # (name, baseline, text, font, fill, track, dx)
        y0 = cap_k + k_gap if kicker else 0
        for name, yy, txt, f, fill, dx in t_rows:
            rows.append((name, y0 + yy, txt, f, fill, 0, dx))
        y = y0 + t_height
        sticker_at = None
        if sticker:   # the poll sticker's room, directly under the question (2026-11-02 round 2)
            sticker_at = y + STORY_STICKER_GAP
            y = sticker_at + sticker + STORY_STICKER_GAP - b_gap + cap_height(font_b)
        title_base = y
        for j, line in enumerate(body_lines):
            y = title_base + b_gap + j * b_step
            rows.append(("body", y, line, font_b, brand.BONE, 0, 0))
        pointer_rec = None
        if i == 0 and not pointer:
            pointer_rec = {"shown": False, "by_rule": True, "text": STORY_POINTER,
                           "reason": f"pointer line {STORY_POINTER!r} not drawn: the publisher can attach no tap "
                                     f"target to a Story frame (rule: {STORY_POINTER_RULE})"}
        if pointer:  # No arrow on the pointer (operator, 2026-10-28): the words say where to go.
            pointer_rec = {"shown": True, "text": STORY_POINTER}
            y += 96
            # In bone (2026-11-01): gold is the frame's one first read (the title's second line); the kicker's
            # gold rule and capitals are the brand's kicker, and a third gold line competed with both.
            # Lane D cycle 4 (PL r1 + r2, converging): the pointer names the tap target every Story has, the account
            # header that opens the profile where the Reel posted first (main posts a Story only once its Reel is
            # published); "IN OUR NEW REEL" promised a link no sticker carried (the Story API posts none)
            rows.append(("reel_pointer", y, STORY_POINTER, font_p, brand.BONE, font_p.size * 0.11, 0))
        bottom = max([d.textbbox((x, yy), txt, font=f, anchor="ls")[3] for _, yy, txt, f, _, _, _ in rows]
                     + ([sticker_at + sticker] if sticker else []))
        block = bottom
        top_cand = sy0 + 40 + block // 2
        # One text anchor across the sequence (round 3, 2026-11-02): every frame's kicker sets at the same top y, so a
        # tap to the next frame keeps the eye in place (calm_center_y's per-frame choice set the hook 160-340 px lower
        # for no narrative reason). The scrim and each line's measured halo hold the contrast on any photo.
        cy = top_cand
        top = max(sy0 + 30, min(cy - block // 2, text_floor - block))   # above the photo credit, when there is one
        shade_rows, shade_block = rows, block
        if i == 0 and not pointer and not formed:
            # Rulings N1 / N2 (2026-11-26, review 2 of the PLATES c5 fix): the band and its step are laid for the block
            # as designed, the withheld pointer's line included (its room, as the base laid it), so withholding the
            # line by ruling neither lightens the band under the other lines nor moves the kicker off its plate
            # (31 Story elements read lower than base without it); the line itself is still not drawn
            ghost = ("reel_pointer", y + 96, STORY_POINTER, font_p, brand.BONE,
                     font_p.size * 0.11, 0)
            shade_rows = rows + [ghost]
            shade_block = max(block, d.textbbox((x, ghost[1]), STORY_POINTER, font=font_p, anchor="ls")[3])
        extra = [tuple(credit["backing"])] if credit.get("backing") else []
        if formed:   # the block family: the kicker (drawn apart, above the rows) in the frame's main block, the credit
            extra = ([(kicker_ink_box(x, top + cap_k, kicker, font_k), tuple(colour_of("kicker.colour")[:3]), "main")]
                     if kicker else []) + credit_items
        base, shaded_rows, ground = story_ground(base, d, shade_rows, x, top, shade_block, plain=extra,
                                                 band_on=band_on)
        rows = [r for r in shaded_rows if r[0] != "reel_pointer"] if shade_rows is not rows else shaded_rows
        lay.ground = ground
        im = base.convert("RGBA")
        if sticker:
            room_box = poll_room_box(top + sticker_at, sticker)
            im = scrim(im, room_box[1], room_box[3], feather=STORY_STICKER_FEATHER, target=STORY_STICKER_TARGET,
                       cap=0.9, floor=0.0)
            lay.protected = [{"name": "poll_sticker_room", "box": [int(v) for v in room_box]}]
            lay.meta_room = [int(v) for v in room_box]
        if kicker:
            im, k_box = draw_kicker(im, x, top + cap_k, kicker, font_k, colour_of("kicker.colour"), lay,
                                    plates=lay.plates)
        by_name: dict[str, list] = {}
        for name, yy, txt, f, fill, track, dx in rows:
            plate = plan_text(im, (x + dx, top + yy), txt, f, fill, "ls", track=track)
            im = plate.draw(im)
            lay.plates.setdefault(name, []).append(plate)
            by_name.setdefault(name, []).append(plate)
        # Round 7 (real-runs sweep S2): every line's ratio as finally composed (title_1's ring holds title_2's ink,
        # drawn after it was planned: 8.37 planned, 7.20 delivered), not as planned
        for plates_ in by_name.values():
            for p in plates_:
                m = p.measure(im)
                if m is not None:
                    p.contrast = m
        for name, plates_ in by_name.items():   # each element one block, in reading order
            record_contrast(lay, name, plates_)
        y_end = top + block
        # The poll frame ends on its question: no option boxes and no "REPLY WITH A OR B" (2026-10-29). The
        # native sticker, placed by the operator in POLL_STICKER_ROOM, is the only way to answer it.
        from .still_layout import union
        ux0, uy0, ux1, uy1 = union([tuple(e["box"]) for e in lay.elements])
        if credit["required"]:  # drawn last, recorded as its own element (text_box stays the frame's text block)
            im, plates = story_credit_plates(im, x, c_top, c_lead, c_drawn, font_c, c_fill)
            lay.plates["photo_credit"] = plates
            credit["box"] = list(record_contrast(lay, "photo_credit", plates))
            # The credit's ratio as drawn: the lowest of its lines on the pixels touching their glyphs.
            measured = [p.contrast for p in plates if p.contrast is not None]
            if measured:
                credit["contrast"] = round(min(measured), 2)
        base = im.convert("RGB")
        kickers = (getattr(lay, "meta", None) or {}).get("kickers") if isinstance(getattr(lay, "meta", None), dict) else None
        from . import line_policy as _lp
        _story_faults = _lp.drawn_record(body_lines, role="body")
        if l1 and l2:   # the title's two authored fields: a seam that ends on a function word is on the record too
            _story_faults = _lp.drawn_record([l1, l2], role="title") + _story_faults
        lay.meta = {"frame": None, "size": [VIDEO_WIDTH, VIDEO_HEIGHT], "photo_credit": credit,
                    **({"kickers": kickers} if kickers else {}),
                    "body_source": " ".join(body.split()), "body_drawn": " ".join(body_lines),
                    "body_px": font_b.size, "body_lines": len(body_lines),
                    # freeze cycle 5 (review D2): the line-break violations left in the body lines this frame drew
                    **({"line_faults": _story_faults} if _story_faults else {}),
                    "title_px": size, "story_title_px": story_px, "window": window, "title_measure": title_w[0], "story_size_off": size != t_size,
                    "text_box": [min(ux0, x), uy0, max(ux1, x + max_w), max(uy1, int(y_end))],
                    "safe_box": [sx0, sy0, sx1, sy1],
                    **({"poll_sticker_room": getattr(lay, "meta_room", None)} if asks_poll else {}),
                    "ground": ground,
                    **({"pointer": pointer_rec} if pointer_rec else {}),
                    **({"hero_figure": hero["number"]} if hero else {})}
        return base, lay

    # 4:5 is the tallest ratio Meta's content-publishing API accepts for feed images (4:5 to 1.91:1);
    # the profile grid shows the centre 3:4 of it, so cover text stays in the middle. 1440x1800 is Meta's
    # recommended 4:5 feed size and the widest the Graph API takes (operator, 2026-10-28: sharper slides, same
    # layout). Every carousel pixel value below is a design value on the 1080-wide canvas, drawn through
    # `_px(cw)` (x cw / 1080), so a 1440 slide is the 1080 slide's layout at 4/3 the resolution.
    CAROUSEL_SIZE = (1440, 1800)
    CAROUSEL_DESIGN_W = 1080
    # JPEG: quality 97 with full-resolution colour (4:4:4). 4:2:0 halves the chroma, which fringed the gold type and
    # capped its colour fidelity at ~38 dB PSNR at any quality; 4:4:4 at 97 measured >= 50 dB there and 46 dB luma,
    # the largest of 28 production slides 1.6 MB (Meta's limit is 8 MB; docs/reviews/carousel-1440-2026-10-28).
    CAROUSEL_JPEG = {"quality": 97, "subsampling": 0, "optimize": True}
    PHOTO_H = 760  # fact slides: photo on top, editorial text panel below (design px)

    @classmethod
    def _px(cls, cw: int):
        """Design px (on the 1080-wide canvas) to this canvas's px: px(80) is 80 at 1080 and 107 at 1440."""
        s = cw / cls.CAROUSEL_DESIGN_W

        def px(v):
            return round(v * s)

        def exact(v):  # a font size or stroke width: exactly x 4/3 (FreeType and Pillow take fractional ones)
            v = v * s
            return int(v) if v == int(v) else v
        px.s, px.exact = s, exact
        return px

    @staticmethod
    def _fit_text(d, text: str, family: str, size: int, width: int, max_lines: int, min_size: int, px=round,
                  trial=False, **kw):
        """fit_text decided on the design canvas (design sizes, strokes and floor; `width` in canvas px) and drawn
        at this canvas's scale: the lines break where the 1080 slide breaks them and each font is the 1080 size
        x 4/3. Fitted on the large canvas itself, fit_text's 2 px steps and the fonts' hinting picked other sizes
        and line breaks (a cover heading that fitted its 920 px on one line went onto two at 1440)."""
        fit = probe_text if trial else fit_text
        lines, font, short = fit(d, text, family, size, round(width / getattr(px, "s", 1.0)), max_lines,
                                      min_size, **kw)
        return lines, get_font(family, getattr(px, "exact", px)(font.size)), short

    # The share of a full-bleed slide's height its photograph shows clear above the text stack, per archetype (the
    # stack stands on fixed baselines; measured on the dev runs' recorded layouts, the highest text top of each:
    # cover 0.556-0.588, immersive 0.672-0.693, debate 0.421-0.430). The photo-fitness view of the slide's window.
    PHOTO_VIEW = {"cover": 0.55, "immersive": 0.66, "debate": 0.42}

    @classmethod
    def _masthead_box(cls, cw: int, facebook: bool, archetype: str, inset: bool) -> tuple[int, int, int, int]:
        """The masthead's box on a slide (canvas px, 16 design px of air around the mark and the name): what the deck
        window rule and the photo-fitness rule keep clear. `inset`: the Facebook cover's margin (FB_COVER_MARGIN)."""
        from . import masthead as _mh
        px = cls._px(cw)
        size = type_px("masthead", "carousel") * cw / 1440
        header = cls.FB_HEADER_Y if facebook and archetype == "cover" else cls.HEADER_Y
        mx = px(cls.FB_COVER_MARGIN) if inset else px(80)
        base = px(header) + cap_height(_mh.font_for(size))
        return (mx - px(16), px(header) - px(16), mx + _mh.group_width(size) + px(16), base + px(16))

    @classmethod
    def photo_window(cls, archetype: str, facebook: bool = False, second: bool = False):
        """The photo-fitness Window (src/photo_fitness.py) of a slide's photograph: its size on the canvas, the part
        of it that shows clear of the text stack and the fade, and the masthead's box where the picture runs under
        it. `second`: a comparison slide's lower photograph. The pickers (deck_photo_guard) and the renderer
        (_fill) ask the same question of the same window."""
        from . import photo_fitness as pf
        from .brand_system import tok
        cw, chh = cls.CAROUSEL_SIZE
        px = cls._px(cw)
        box = cls._masthead_box(cw, facebook, archetype, inset=facebook and archetype == "cover")
        mbox = (box[0], 0, box[2], box[3])   # up to the frame's edge (_fill)
        if archetype in cls.PHOTO_VIEW:
            return pf.Window((cw, chh), view=(0, 0, cw, round(chh * cls.PHOTO_VIEW[archetype])), protected=(mbox,),
                             name=f"carousel {archetype}")
        ph = px(cls.SPLIT_PHOTO_H_FB if facebook else cls.SPLIT_PHOTO_H)
        if archetype == "comparison":
            gutter = px(tok("plate.gutter"))
            half = (ph + px(40) - gutter) // 2
            if second:
                low = ph + px(150) - half - gutter
                return pf.Window((cw, low), view=(0, 0, cw, max(1, ph - half - gutter)), name="carousel comparison 2")
            return pf.Window((cw, half), protected=(mbox,), name="carousel comparison 1")
        return pf.Window((cw, ph + px(150)), view=(0, 0, cw, ph), protected=(mbox,), name=f"carousel {archetype}")

    @staticmethod
    def _fill(photo: Path, size: tuple[int, int], top: bool = True, view=None) -> Image.Image:
        """The photo scaled and cropped to `size` in one Lanczos resample from the file itself (the pipeline's
        upscaled copy for a low-resolution source), never from a smaller intermediate. Its window (cycle 0 round 8,
        the deck window rule, hero_window.deck_window): centred, unless this deck shows the photograph again
        (_render_state.window_k: its earlier showings), when it overlaps them as little as the picture allows
        keeping its centre; and (`top`: the picture runs under the masthead) one whose masthead box holds no
        foreign object is preferred.
        Freeze cycle 5 (PHOTO-02/03/04/05): that window is then held to the photo-fitness rule
        (src/photo_fitness.py) for this slide's window (`view`: the part of `size` that shows clear of the text and
        the fade; the masthead's box as its protected box). A fit window is drawn as it was; an unfit one (the
        source's own frame in it, its view flat or emptier than the picture, a foreign structure under the masthead)
        gives way to the fit crop of the same photograph that changes least, inside its content and the
        enlargement limit. Lane D cycle 4's frame_window did the first of these alone and moved the Richat slide 3
        window onto bare sand: nothing measured the subject. Every window's measures are recorded
        (_render_state.window_rec -> lay.meta["windows"]); an unfit photograph with no fit crop is drawn as asked and
        recorded unfit (the deck's photo guard takes another photograph before the slide is drawn)."""
        from . import photo_fitness as pf
        k = int(getattr(_render_state, "window_k", 0) or 0)
        mbox = getattr(_render_state, "masthead_box", None) if top else None
        centering = (0.5, 0.5)
        from .image_presentation import full_image
        if full_image(photo):
            return cover_crop(photo, size, centering).convert("RGB")
        clean, _ = pf.load(Path(photo))
        entry: dict[str, Any] = {"photo": Path(photo).name, "size": list(size), "showing": k}
        earlier: list = []
        if k or mbox:
            from .hero_window import deck_window
            win = deck_window(clean.size, size, k, mbox, clean)
            centering = win["centering"]
            entry.update({"centering": [round(v, 3) for v in centering], "overlap": win["overlap"],
                          "masthead_clear": win["masthead_clear"]})
            across = clean.size[0] / clean.size[1] > size[0] / size[1]
            earlier = [pf.fit_box(clean.size, size, (p, 0.5) if across else (0.5, p)) for p in win["positions"][:-1]]
        # the masthead's protected box runs up to the frame's edge: a wing or an array just above the name crowds it
        # as one under it does (panel 6: the Nazca cover's wing sat between the name and the top edge)
        window = pf.Window(tuple(size), view=view, name="carousel",
                           protected=((mbox[0], 0, mbox[2], mbox[3]),) if mbox else ())
        analysis = pf.analysis_of(Path(photo), clean)
        fit = pf.place(photo, window, centering, image=clean, analysis=analysis, fixed=True)
        if not fit["fit"] and not getattr(_render_state, "asked_only", False):
            from .hero_window import DECK_OVERLAP_MAX
            fit = pf.place(photo, window, centering, earlier=earlier, overlap_max=DECK_OVERLAP_MAX if earlier else None,
                           image=clean, analysis=analysis)
        entry["fit"] = {key: v for key, v in fit.items() if key != "window"}
        rec = getattr(_render_state, "window_rec", None)
        if isinstance(rec, list):
            rec.append(entry)
        if fit["moved"]:
            return pf.crop(clean, fit["box"], tuple(size)).convert("RGB")
        return cover_crop(photo, size, centering).convert("RGB")

    @classmethod
    def _save_slide(cls, im: Image.Image, path: Path) -> None:
        im.save(path, "JPEG", **cls.CAROUSEL_JPEG)

    def _match_photos(self, cards: list[dict[str, Any]], stills: list[Path], strict: bool = False) -> list[Path]:
        """One photo per slide, matched to what the slide says (the scene planner's vision matching). `strict`: a
        failed match raises PhotoMatchFailed instead of rotating the photos (a carousel drawn at its slot is held
        rather than posted with photos nobody matched to its slides: QF-81). A failed match is also a plan without
        the vision planner: plan_scenes catches the planner's failures itself and returns filename order (the
        rotation) or caption order, saying so in its notes; strict holds on both. The strict match tries the
        planner CAROUSEL_PLAN_ATTEMPTS times, first wait CAROUSEL_PLAN_RETRY_SECONDS, so the slot decides promptly."""
        from . import config
        from .scene_planner import CAPTION_NOTES, plan_scenes
        beats = [{"start": float(i), "end": float(i + 1), "text": f"{c['heading']}. {c['body']}"} for i, c in enumerate(cards)]
        try:
            if not strict:
                chosen, _, _ = plan_scenes(beats, stills)
                return [Path(c) for c in chosen]
            chosen, _, notes = plan_scenes(beats, stills, attempts=config.CAROUSEL_PLAN_ATTEMPTS,
                                           retry_seconds=config.CAROUSEL_PLAN_RETRY_SECONDS)
        except Exception as e:  # matching is a refinement; rotation is the fallback
            if strict:
                raise PhotoMatchFailed(f"its photos could not be matched to its slides ({e})") from e
            log.warning("Carousel photo matching failed (%s); rotating photos.", e)
            return [stills[i % len(stills)] for i in range(len(cards))]
        if notes == "filename order":
            raise PhotoMatchFailed("its photos could not be matched to its slides (the picture planner was "
                                   "unavailable or failed every attempt, and filename order would only rotate them)")
        if notes == CAPTION_NOTES:
            raise PhotoMatchFailed("its photos could not be matched to its slides (the picture planner was "
                                   "unavailable or failed every attempt; archive captions alone are not a match)")
        return [Path(c) for c in chosen]

    def _carousel_photos(self, cards: list[dict[str, Any]], stills: list[Path], output_dir: Path,
                         strict: bool = False) -> list[Path]:
        """The slides' photos, matched once per run and shared by both copies (platform audit, 2026-10-28: the
        Instagram and Facebook copies were matched separately, in parallel, and showed different photos for the
        same slide). The match is kept in carousel_photos.json beside the copies; the second copy to be drawn
        waits for the first and reuses it, and a later redraw of one copy reuses it while the cards and the
        photo pool are the same. A run drawn before the record existed reuses the other copy's recorded photos.
        A kept or recorded match showing an AI-made picture is never reused (QF-65c): the slides are matched again
        from `stills`."""
        from .ai_label import is_ai_picture
        output_dir = Path(output_dir)
        record = output_dir / CAROUSEL_PHOTOS
        key = {"cards": [f"{c.get('heading', '')}|{c.get('body', '')}" for c in cards],
               "stills": [str(Path(p)) for p in stills]}
        with _photo_lock(output_dir):
            try:
                kept = json.loads(record.read_text(encoding="utf-8")) if record.is_file() else {}
            except (OSError, ValueError):
                kept = {}
            chosen = [Path(x) for x in kept.get("photos") or []]
            if (kept.get("key") == key and len(chosen) == len(cards) and all(x.is_file() for x in chosen)
                    and not any(is_ai_picture(x) for x in chosen)):
                matched = [Path(x) for x in kept.get("matched") or chosen]
                guarded, choice = deck_photo_guard(cards, matched, stills)   # lane D cycle 4: every path
                if guarded == chosen and kept.get("choice") == choice:
                    return chosen
            else:
                recorded = _recorded_photos(output_dir, cards)
                if any(is_ai_picture(x) for x in recorded):
                    recorded = []
                matched = recorded or (self._match_photos(cards, stills, strict=True) if strict
                                       else self._match_photos(cards, stills))
                guarded, choice = deck_photo_guard(cards, matched, stills)
            try:
                output_dir.mkdir(parents=True, exist_ok=True)
                record.write_text(json.dumps({"key": key, "photos": [str(x) for x in guarded],
                                              "matched": [str(x) for x in matched], "choice": choice,
                                              "rule": PHOTO_CHOICE_RULE}, indent=1, ensure_ascii=False),
                                  encoding="utf-8", newline="\n")
            except OSError as e:  # the record is a convenience; the slides are still drawn
                log.warning("Carousel photo match not recorded (%s).", e)
            return guarded

    @staticmethod
    def route_carousel_archetype(card: dict[str, Any], index: int, total: int) -> str:
        """Semantic intent router that selects the optimal editorial archetype."""
        if index == 0:
            return "cover"
        if index == total - 1:
            return "debate"
        text = (card.get("heading", "") + " " + card.get("body", "")).lower()
        stat_patterns = [
            r"\b\d+[\d,]*\s*(?:tons?|people|feet|ft|meters?|m|stories|levels|degrees|bce?|years?)\b",
            r"\b\d+[,.]\d+\b",
        ]
        if range_figure(text):   # a stated span ("between 94 and 104 million years ago") is the slide's figure
            return "colossal_stat"
        if any(re.search(p, text) for p in stat_patterns) and any(w in text for w in ("tons", "people", "deep", "stories", "levels", "feet", "meters", "capacity")):
            return "colossal_stat"
        # Whole words only (round 3, 2026-11-02): "surface" matched inside "Subsurface" and "vs" inside other words,
        # so slides that contrast nothing got the two-photo stack. Bare "surface" is no contrast ("beneath the
        # surface"); the card must say it compares.
        if _CONTRAST.search(text):
            return "comparison"
        relic_keywords = ("gear", "gears", "dial", "dials", "fragment", "fragments", "inscription", "inscriptions",
                          "bronze", "relic", "artifact", "tablet", "device", "pointer", "teeth",
                          "astronomical", "clockwork", "corroded", "ancient computer")
        if any(w in text for w in relic_keywords):
            return "blueprint" if any(w in text for w in ("shaft", "ventilation", "channel", "door", "rolling stone", "tunnel", "chiseled", "engineered", "mechanism", "structure", "architecture", "block")) else "editorial_split"
        if any(w in text for w in ("shaft", "ventilation", "channel", "door", "rolling stone", "tunnel", "chiseled", "engineered", "mechanism", "structure", "architecture", "block")):
            return "blueprint"
        return "immersive" if index % 2 == 1 else "editorial_split"

    REPEATABLE = ("colossal_stat",)   # a figure slide follows its content: two in a row stay two

    @classmethod
    def sequence_archetypes(cls, cards: list[dict[str, Any]]) -> list[str]:
        """Each slide's archetype (route_carousel_archetype), then a pass over the set so two neighbours never get
        the same photo treatment (2026-11-01: Richat's slides 4 and 5 were both comparisons): the second becomes
        the full-bleed or the split archetype, whichever neither neighbour has. Figure slides keep theirs."""
        total = len(cards)
        out = [cls.route_carousel_archetype(c, i, total) for i, c in enumerate(cards)]
        for i in range(1, total - 1):
            if out[i] == out[i - 1] and out[i] not in cls.REPEATABLE:
                after = out[i + 1] if i + 1 < total else None
                out[i] = next((a for a in ("immersive", "editorial_split") if a not in (out[i - 1], after)),
                              "editorial_split" if out[i - 1] == "immersive" else "immersive")
        return out

    @staticmethod
    def _get_card_category(card: dict[str, Any], archetype: str) -> str:
        """The slide's badge, or "" for none. A slide may not state a qualifier its script did not (platform
        audit, 2026-10-28): keyword labels put "DISCOVERY RECORD" on a slide about marine concrete ("seawalls"
        has "wall"), "ARCHIVAL EVIDENCE" and "ENGINEERING BLUEPRINT" on slides that cite no archive or blueprint,
        and "FORENSIC" on a question. Nothing on a card says what kind of evidence it is (it has a heading and
        a body), so fact slides carry no badge; the closing slide is labelled for what it does, and only when
        it does it: it asks the reader a question."""
        if archetype == "debate" and "?" in f"{card.get('heading', '')} {card.get('body', '')}":
            return "YOUR TAKE"
        return ""

    def _tag(self, category: str, index: int) -> str:
        """A slide's badge: its category. No slide number ("ENGINEERING // 07"): Instagram shows its own "N/M"
        counter and dots, and our slide-style chrome made the carousel look awkward (operator, 2026-10-28).
        Every slide after the cover carries the subject's name (_render_state, per thread), before its category
        when it has one: each photo of a Facebook multi-photo post is opened on its own, without the caption."""
        subject = getattr(_render_state, "subject", "")
        if subject and category:
            return f"{subject} · {category}"     # the closing slide names its subject too (PR review, 2026-11-02)
        return category or subject

    # Carousel chrome (operator, 2026-10-28): "the design we added to the Instagram carousel to make it look like
    # a slide is needless as Instagram has its own look". The dossier counter ("ARCHIVAL RECORD // 01 OF 07"),
    # the progress bar, the dots, the cover's SWIPE cue and the badges' slide numbers are gone from both copies;
    # the band the cues reserved at the bottom is the text's again (_footer_top).
    #
    # Brand placement: "RELIC & RIDDLE" sat top-right, exactly where Instagram overlays its "N/M" counter on
    # carousel media. What each platform draws over the media: Instagram, the counter pill at the top-right
    # (about 14 pt in from the corner, about 40 x 26 pt: on a 390 pt phone, ~2.8 canvas px per pt, x 930-1041,
    # y 39-111), a tagged-people icon at the bottom-left on tagged photos (we post none), and the dots below the
    # media, outside it; Facebook's photo grid draws "+N" over the centre of its last tile and its viewer draws
    # nothing over the photo. The top-left corner is clear on both, so the brand is drawn there, small, at the
    # 80 px text margin. A square grid tile crops 135 px off the top of a 4:5 slide: the brand (y 70-92) then
    # drops out whole rather than half cut. IG_COUNTER is checked on every slide (still_layout `protected`), so
    # nothing we draw ever lands under the counter.
    HEADER_Y = 70
    # Lane D cycle 3 (lander rulings, peer evidence evidence/peers/fb_feed/peer_01.png): Facebook shows the posted
    # photos as a collage of centre squares (FACEBOOK_COLLAGE), and peers brand the collage once. The Facebook cover's
    # masthead sits inside its centre square, FB_HEADER_INSET under its top (FB_SQUARE_TOP: (1350 - 1080) / 2 design
    # px); the Facebook copy's interior slides draw no masthead (five on one collage were brand noise). Instagram's
    # copy is unchanged.
    FB_SQUARE_TOP = 135
    FB_HEADER_INSET = 40
    FB_HEADER_Y = FB_SQUARE_TOP + FB_HEADER_INSET
    BAND_GAP = 16

    def _header_y(self, facebook: bool, archetype: str) -> int:
        """The masthead's top, design px: FB_HEADER_Y on the Facebook cover (the one slide whose masthead the
        collage shows: lane D cycle 3, lander ruling 1 of the second review), else HEADER_Y."""
        return self.FB_HEADER_Y if facebook and archetype == "cover" else self.HEADER_Y

    @staticmethod
    def _fb_cover_inset() -> bool:
        """Whether the Facebook cover being drawn keeps FB_COVER_MARGIN: only where its post can show it as the
        4-photo collage's 2:3 hero tile (a deck of 4 slides or fewer); a deck of 5 or more posts 5 photos
        (facebook_pick), whose collage shows the cover as a full-width centre square (lane D cycle 3, ruling 1)."""
        return bool(getattr(_render_state, "facebook", False)) and bool(getattr(_render_state, "fb_hero_tile", False))

    @staticmethod
    def _ig_counter(cw: int) -> tuple[int, int, int, int]:
        """Where Instagram overlays its carousel counter: the measured pill (above) with room for "10/10" and
        narrower phones: the right 280 px of the top 140 px (design px; the counter is drawn at a fixed share
        of the media's width, so it scales with the canvas)."""
        px = MultiFormatGenerator._px(cw)
        return (cw - px(280), 0, cw, px(140))

    def _zone(self, cw: int, chh: int):
        """Where a slide's text and badges stay: the canvas less 40 px each side (the profile grid's 3:4 crop
        takes 33 px off each side of a 4:5 slide) and 30 px top and bottom (design px)."""
        px = self._px(cw)
        return (px(40), px(30), cw - px(40), chh - px(30))

    def _footer_top(self, chh: int) -> int:
        """The lowest y a slide's text may reach: the zone's bottom less two gaps. Nothing is drawn under the
        text any more (the swipe cues are gone, operator 2026-10-28; Instagram's dots sit below the media).
        The canvas is 4:5, so its width follows from `chh`."""
        cw = chh * 4 // 5
        return self._zone(cw, chh)[3] - 2 * self._px(cw)(self.BAND_GAP)

    @staticmethod
    def _text(d, lay, xy, text, font, fill, anchor="lt", **kw):
        text = drawable(text, font)  # no emoji, no missing-glyph boxes (2026-10-29)
        d.text(xy, text, font=font, fill=fill, anchor=anchor, **kw)
        return tuple(d.textbbox(xy, text, font=font, anchor=anchor, stroke_width=kw.get("stroke_width", 0)))

    # The overhaul's slide grammar (visual critic round 4, 2026-10-29). Every slide's text is one stack, measured
    # first and set bottom-aligned on one shared line, KEY_BOTTOM less KEY_GAP: the line sits inside the profile
    # grid's 1:1 crop (it takes 135 design px off each end of a 4:5 slide), so the same block reads in the grid,
    # the feed and the viewer. Slides alternate full-bleed photographs and photo-over-ink splits (the router's
    # "immersive" and "editorial_split"; blueprint and stat slides are splits, comparison a two-photo split).
    # Light text is drawn at a measured contrast (typography.place_text), over an adaptive ink gradient behind the
    # block only; no pill, badge outline or box anywhere (operator, 2026-10-28: no slide chrome). Sizes are design
    # px on the 1080-wide canvas, drawn through _px.
    KEY_TOP, KEY_BOTTOM, KEY_GAP = 169, 1181, 18
    MARGIN = 84
    # The Facebook cover's side inset, design px: the 2:3 hero tile's centre crop starts 90 design px (120 at 1440) in
    # from each side, plus a 45 design px (60) gutter: the text column is x 180-1260 on the 1440 slide (round 4).
    FB_COVER_MARGIN = 135
    SPLIT_PHOTO_H = 675

    def _stack(self, im: Image.Image, lay, rows: list[dict[str, Any]], x: int, bottom: int, plates: dict[str, list]):
        """Draws `rows` (each {"name", "text", "font", "fill", "y": its baseline relative to the stack's top,
        optional "track", "x": an x offset}) so the stack's lowest ink sits on `bottom`; records each row (rows of
        one name as one block, with their lowest measured ratio). Returns (image, the stack's top)."""
        d = ImageDraw.Draw(im)
        drawn = [r for r in rows if r.get("text")]
        if not drawn:
            return im, bottom
        low = max(d.textbbox((x + r.get("x", 0), r["y"]), r["text"], font=r["font"], anchor="ls")[3] for r in drawn)
        off = bottom - low
        by_name: dict[str, list] = {}
        for r in drawn:
            if r.get("kicker"):
                im, _ = draw_kicker(im, x, off + r["y"], r["text"], r["font"], r["fill"], lay, r["name"], plates)
                continue
            plate = plan_text(im, (x + r.get("x", 0), off + r["y"]), r["text"], r["font"], r["fill"], "ls",
                              track=r.get("track", 0), min_halo=r.get("min_halo", 0.0))
            im = plate.draw(im)
            by_name.setdefault(r["name"], []).append(plate)
            plates.setdefault(r["name"], []).append(plate)
        for ps in by_name.values():   # round 7 (sweep S2): each ratio on the finally composed stack
            for p in ps:
                m = p.measure(im)
                if m is not None:
                    p.contrast = m
        for name, ps in by_name.items():
            record_contrast(lay, name, ps)
            if name == "stat" and lay is not None and getattr(lay, "elements", None):
                # lane D cycle 3 (stress realism, Richat 1793474473): a stat figure is ONE lockup whose rows _rows
                # lays out in reading order (hedge, number, unit lines). drawn_text's geometric order read the
                # stacked unit "MILLION / YEARS AGO" beside the tall number as "94-104 YEARS MILLION AGO" (the
                # number's box overlaps both unit lines), so the drawn-figure gate failed a figure drawn whole.
                lay.elements[-1]["text"] = " ".join(" ".join(str(p.text).split()) for p in ps)
        top = min(d.textbbox((x + r.get("x", 0), off + r["y"]), r["text"], font=r["font"], anchor="ls")[1] for r in drawn)
        return im, top

    # Content slides (2026-11-02 panel round 2): the kicker's and the heading's baselines are the same on every
    # content slide (they drifted ~15 px with each body's length when the whole stack was bottom-aligned), so a
    # swipe keeps the eye in place; the body still ends on the shared bottom line. The baselines are those of the
    # tallest regular stack (a one-line heading over BODY_LINES body lines; on Facebook, whose collage shows each
    # photo about a third of the screen wide, a two-line 140 px heading over BODY_LINES_FB): a shorter body opens
    # the gap under the heading instead of moving it. A slide whose stack is taller, or leads with a figure, is
    # bottom-aligned as before.
    # Round 3 (2026-11-02): Instagram's content headlines take the Facebook treatment (Anton two-tone, one line,
    # else two balanced lines split at the seam, "IMPACT THEORY / DISPROVED"), one step below Facebook's size: the
    # Instagram viewer shows each slide full width, Facebook's collage shows it a third as wide. At (100, 60) on one
    # line the type fell about 2x from the cover to slide 2.
    # Round 3 fix (2026-11-02, second pass): the frame is chosen PER DECK, not per slide (a per-slide frame moved the
    # kicker and heading top up to ~190 px between a deck's one-line and two-line slides, undoing the shared line
    # above). generate_carousel_sequence measures every content heading of the copy first (_deck_heading_frame):
    # - no content heading takes two lines: the ONE-LINE frame. No second heading line is reserved (that
    #   reservation put ~330 canvas px between a one-line heading and its body on Instagram, and shrank the diagram
    #   slide's drawing by about a fifth), and BODY_LINES body lines are, so every body meets the frame;
    # - some content heading takes two lines: the TWO-LINE frame, exactly round 2's (BODY_LINES_TWO_LINE_FRAME body
    #   lines, the second heading line at the design size; one frame for both copies since lane D cycle 2). A one-line heading in such
    #   a deck keeps the open gap under it: one shared kicker and heading line across the swipe is worth more than
    #   that gap, and it cannot close without moving the body off the shared bottom line.
    # Every slide of the copy, the diagram slide with it, sits on the deck's frame; the frame is kept in each slide's
    # recipe (`heading_frame`) so a redraw lands on the same lines. A slide drawn alone defaults to the two-line frame.
    CONTENT_HEADING = (138, 124)         # Instagram: display_l and its one fallback tier (brand_system.TYPE carousel, design px)
    # Lane D cycle 2 (lander ruling 1, SYS-D-4): the Facebook copy is the same slides, so ONE form per heading across
    # both copies: Facebook sets on Instagram's ladder (its own 150 / 140 tier retired; it wrapped 51 of 218 real
    # content headings Instagram sets on one line: VANISHED / IN 1944, 35 / BRONZE GEARS).
    SPLIT_PHOTO_H_FB = 560
    BODY_SPEC = (36, 34)                 # Montserrat, design px: its size and its floor
    BODY_LEADING = 1.38
    BODY_MEASURE = 34                    # characters a body line may hold
    BODY_LINES = 4
    BODY_LINES_FB = 3                    # the stack's body lines planned by the Facebook-only callers (unchanged)
    BODY_LINES_TWO_LINE_FRAME = 3        # body lines the two-line frame reserves, both copies (round 2's frame)
    BODY_LINES_ONE_LINE_FRAME = BODY_LINES   # body lines the one-line frame reserves: every body meets it
    NON_CONTENT_ARCHETYPES = ("cover", "colossal_stat", "debate")   # every other archetype draws a content stack
    # Every archetype _render_carousel_slide draws (its dispatch; anything else draws editorial_split), held equal to
    # that dispatch by tests/test_brand_system_r6.py and rendered for the type-size observer (tests/surface_registry.py)
    CAROUSEL_ARCHETYPES = ("cover", "immersive", "comparison", "blueprint", "colossal_stat", "diagram", "debate",
                           "editorial_split")

    def _content_frame(self, px, facebook: bool, heading_lines: int = 2) -> dict[str, int]:
        """The deck's fixed content baselines, canvas px, for a deck frame of `heading_lines` (1 or 2) heading
        lines: {"kicker", "heading" (the heading's first line), "heading_last" (where a heading's last line falls
        when it fills the frame), "heading_lines"}. heading_lines=2 is round 2's frame, value for value."""
        fx = px.exact
        b_size = fx(self.BODY_SPEC[0])
        step = round(b_size * self.BODY_LEADING)
        last = px(self.KEY_BOTTOM - self.KEY_GAP) - round(b_size * 0.25)   # the last body line's baseline
        two = heading_lines > 1
        lines = self.BODY_LINES_TWO_LINE_FRAME if two else self.BODY_LINES_ONE_LINE_FRAME
        first_body = last - (lines - 1) * step
        top = self._deck_size()   # the deck's one content size (SYS-D-5): its cap and pitch set the frame
        h = get_font(brand.DISPLAY_FONT, fx(top))
        h_last = first_body - round(b_size * tok("band.body_gap"))
        hb = h_last
        if two:
            # The second heading line at the design size (round 3: at its floor the two-line "IMPACT THEORY /
            # DISPROVED" missed the frame by a few px and dropped to bottom-aligned); both copies since lane D cycle 2.
            second = top
            hb = h_last - round(cap_height(get_font(brand.DISPLAY_FONT, fx(second))) * brand_system.SPEC["lead.display"])
        k_size = fx(type_px("kicker", "carousel") * 3 / 4)
        return {"kicker": hb - cap_height(h) - kicker_gap(k_size), "heading": hb, "heading_last": h_last,
                "first_body": first_body, "heading_lines": 2 if two else 1}

    def _content_baselines(self, px, facebook: bool, heading_lines: int = 2) -> tuple[int, int]:
        """(kicker baseline, heading first-line baseline) of the content slides on a `heading_lines` deck frame."""
        f = self._content_frame(px, facebook, heading_lines)
        return f["kicker"], f["heading"]

    @staticmethod
    def _deck_tier() -> int:
        """The deck's content-heading tier the slide being drawn sits on (_render_state, per thread): 0, the design
        size (display_l); 1, the fallback tier for every content slide (SYS-D-5). 0 when drawn alone."""
        return 1 if int(getattr(_render_state, "heading_tier", 0) or 0) > 0 else 0

    def _deck_size(self) -> float:
        """The deck's one content-heading size, design px (CONTENT_HEADING at _deck_tier)."""
        return self.CONTENT_HEADING[self._deck_tier()]

    def _deck_heading_tier(self, cards: list[dict[str, Any]], archetypes: list[str]) -> int:
        """SYS-D-5 (lander ruling c; RUBRIC CD3: one display size per deck): the tier the deck sets EVERY content
        heading at, over both copies (one form per heading, SYS-D-4). 0 when every content heading sets at the design
        size (the engine already prefers two balanced lines there over one line at the fallback); 1 when any one
        takes the fallback tier or a recorded last resort under it, so the deck takes the fallback deck-wide (a last
        resort keeps its own size: it is under the tier either way). Measured on the design canvas as each copy
        cleans and typesets its words (_render_carousel_slide)."""
        from .script_engine import facebook_photo_caption, neutral_cues
        cw = self.CAROUSEL_SIZE[0]
        px = self._px(cw)
        d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        width = cw - 2 * px(self.MARGIN)
        prev = (getattr(_render_state, "facebook", False), getattr(_render_state, "heading_tier", 0),
                getattr(_render_state, "heading_form", None))
        _render_state.heading_tier = 0
        try:
            for fb in (False, True):
                clean = facebook_photo_caption if fb else neutral_cues
                _render_state.facebook = fb
                for card, archetype in zip(cards, archetypes):
                    if archetype in self.NON_CONTENT_ARCHETYPES:
                        continue
                    heading = typeset(clean(card.get("heading") or "")).upper()
                    heading = " ".join(drawable(heading, get_font(brand.DISPLAY_FONT, 100)).split())
                    if not heading.strip():
                        continue
                    try:
                        form, _ = self._set_heading(d, px, heading, width, "wrap", 1.0, self.CONTENT_HEADING, True)
                    except TextFitError:
                        continue   # refused at every size: the deck tier cannot help it (QF-23 refuses the slide)
                    if form.tier != 0:
                        return 1
        finally:
            _render_state.facebook, _render_state.heading_tier, _render_state.heading_form = prev
        return 0

    @staticmethod
    def _frame_lines() -> int:
        """The deck frame the slide being drawn sits on (_render_state, per thread); 2 when drawn alone."""
        return 2 if int(getattr(_render_state, "heading_frame", 2) or 2) > 1 else 1

    def _heading_lines(self, d, px, heading: str, width: int, two_tone: bool | str = "wrap") -> int:
        """How many lines a content slide's heading takes at its design scale (the frame it is planned on). A heading
        that design scale holds only cut takes the two-line frame (QF-23: the slide steps down its scales whole, or is
        refused, when drawn; the frame is the one its two lines, cut, took before)."""
        try:
            rows, _ = self._rows(d, px, heading, "", width, "", "", self.CONTENT_HEADING, self.BODY_LINES, two_tone,
                                 1.0, facebook_heading=True)
        except TextFitError:
            return 2
        return max(1, sum(1 for r in rows if r["name"] == "heading" and r.get("text")))

    def _deck_heading_frame(self, cards: list[dict[str, Any]], archetypes: list[str], facebook: bool) -> int:
        """The copy's frame (1 or 2 heading lines): 2 when any content slide's heading, as that copy draws it
        (its words cleaned and typeset as _render_carousel_slide does), takes two lines at its design scale."""
        from .script_engine import facebook_photo_caption, neutral_cues
        clean = facebook_photo_caption if facebook else neutral_cues
        cw = self.CAROUSEL_SIZE[0]
        px = self._px(cw)
        d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        prev = getattr(_render_state, "facebook", False)
        _render_state.facebook = bool(facebook)
        try:
            for card, archetype in zip(cards, archetypes):
                if archetype in self.NON_CONTENT_ARCHETYPES:
                    continue
                heading = typeset(clean(card.get("heading") or "")).upper()
                if heading.strip() and self._heading_lines(d, px, heading, cw - 2 * px(self.MARGIN)) > 1:
                    return 2
        finally:
            _render_state.facebook = prev
        return 1

    def _body(self, d, px, body: str, width: int, max_lines: int, size: float, floor: float, trial: bool = False):
        """The body as the slide draws it: (lines, font, shortened). Planned on the design canvas like every fit.
        `trial`: a shortened result is returned as is (not refitted and logged), for a caller that tries again.

        First the measured setting: balanced lines of at most BODY_MEASURE characters, not ending on a one-word line
        where a narrower measure avoids it. It stands wherever it holds the ONE line policy (src/line_policy.py): no
        pixel moves there. Where it leaves a violation (a figure parted from its unit, a range cut, a line ending on
        a function word, a proper name parted, a line over the measure), or takes a line more than a setting as
        clean needs (review D8), the body is set again in one search over every break that fits the slide's width
        and `max_lines` (line_policy.set_lines), so the policy holds in the path production draws (freeze cycle 5,
        review D1: the measure was narrowed first and the policy saw only the narrow trial; "nearly 1,090 / tonnes"
        stayed). A line may pass the character measure where that is the only way to hold the policy; _stack
        records it with every violation that stays (lay.meta["line_faults"])."""
        first = fallback = chosen = None
        w = width
        while w >= px(420):
            got = self._fit_text(d, body, brand.TEXT_FONT, int(size), w, max_lines, int(min(floor, size)), px=px,
                                 quiet=True, trial=True)
            lines, _, short = got
            first = first or got
            measure_ok = all(len(ln) <= self.BODY_MEASURE for ln in lines)
            if not short and measure_ok:
                if not (len(lines) > 1 and len(lines[-1].split()) == 1):
                    chosen = (even_rag(d, lines, got[1], width, self.BODY_MEASURE), got[1], got[2])
                    break
                fallback = fallback or got
            w -= px(16)
        chosen = chosen or fallback or first
        if chosen[2] and not trial:   # shortened even so: fitted once more at the full measure, logged
            return self._fit_text(d, body, brand.TEXT_FONT, int(size), width, max_lines, int(min(floor, size)), px=px)
        lines, font, short = chosen
        if short or len(lines) < 2:
            return chosen
        from . import line_policy
        s = getattr(px, "s", 1.0)
        design = get_font(brand.TEXT_FONT, int(size))                 # planned on the design canvas, like _fit_text
        better = line_policy.set_lines(" ".join(body.split()), lambda t: d.textlength(t, font=design),
                                       round(width / s), max_lines, self.BODY_MEASURE)
        if (better and " ".join(better).split() == " ".join(lines).split()
                and line_policy.form_key(better, self.BODY_MEASURE) < line_policy.form_key(lines, self.BODY_MEASURE)):
            return better, font, short
        return chosen

    def _set_heading(self, d, px, heading: str, width: int, two_tone, scale: float, h_spec, content: bool):
        """The slide's heading as the engine sets it (src/heading.py), planned in design px on the type ladder and drawn
        at the canvas's scale: (form, the canvas font). The cover (two_tone True at the display_xl spec) is LOCKED at
        display_xl (F2: never shrunk; up to three lines before that); a content slide takes display_l, then its one
        fallback tier (F4: never a continuous fit), unscaled; the debate slide takes display_m and its fallback tier at
        the stack's scale; any other heading (the stat slide's) keeps its declared steps (DEVIATIONS). TextFitError
        when no form sets (QF-23: refused whole)."""
        s = getattr(px, "s", 1.0)
        design_w = width / s
        fonts: dict = {}

        def measure(line: str, size: float) -> float:
            if size not in fonts:
                fonts[size] = get_font(brand.DISPLAY_FONT, size)
            return d.textlength(line, font=fonts[size])

        design = {k: round(v * 3 / 4, 2) for k, v in brand_system.TYPE["carousel"].items() if v}
        fallback = {k: round(v * 3 / 4, 2) for k, v in brand_system.TYPE_FALLBACK["carousel"].items()}
        form = None
        if two_tone is True and h_spec[0] >= design["display_xl"]:
            form = set_heading(heading, measure, design_w, (design["display_xl"],), lockup=True, locked_lines=4)
            if form is None:   # a recorded last resort (off_ladder) rather than a refused cover
                # (the real Gol Gumbaz FB cover, THE WHISPERING VAULT on 810 px: THE / ... ends on a never word and
                # THE WHISPERING / VAULT holds only under the fallback tier) -- down to the cover's old floor, valid
                # forms only, recorded off_ladder: a deck is never refused for a heading the old path set
                form = set_heading(heading, measure, design_w, (design["display_xl"],), lockup=True, max_lines=4,
                                   last_resort_floor=fallback["display_xl"], final_floor=h_spec[1])
        elif content:
            spec = self.CONTENT_HEADING   # both copies (lane D cycle 2, SYS-D-4)
            tier = self._deck_tier()   # SYS-D-5: the deck's one tier (the fallback only deck-wide)
            tile = getattr(_render_state, "tile_px", None)
            if tile:
                # lane D cycle 3: the Facebook collage's second top tile, at its ladder step (generate_carousel_sequence)
                form = set_heading(heading, measure, design_w, (tile,), extra_lines=3,
                                   last_resort_floor=round(tile * 0.9), final_floor=round(spec[1] * 0.6))
                if form is not None:
                    form.record["collage_tile_px"] = tile
                    tier = 0
            if not tile or form is None:
                form = set_heading(heading, measure, design_w, spec[tier:], extra_lines=3,
                                   last_resort_floor=round(spec[1] * 0.9), final_floor=round(spec[1] * 0.6))
            if form is not None and tier:
                form.tier = None if form.tier is None else form.tier + tier
                form.record["deck_tier"] = tier
        elif two_tone is True:
            form = set_heading(heading, measure, design_w, (design["display_m"] * scale, fallback["display_m"] * scale),
                               lockup=True, extra_lines=3, last_resort_floor=round(fallback["display_m"] * scale * 0.9),
                               final_floor=h_spec[1] * scale)
        else:
            size = h_spec[0] * scale
            while form is None and size >= h_spec[1] * scale:
                form = set_heading(heading, measure, design_w, (size,))
                size -= 2
        if form is None:
            raise TextFitError(heading, [heading], get_font(brand.DISPLAY_FONT, h_spec[0]), int(design_w), 2)
        return form, get_font(brand.DISPLAY_FONT, getattr(px, "exact", px)(form.size))

    def _rows(self, d, px, heading: str, body: str, width: int, kicker: str = "", stat: str = "", h_spec=(112, 60),
              b_lines: int = 3, two_tone: bool | str = False, scale: float = 1.0, body_spec=None,
              facebook_heading: bool = False):
        """A slide's text stack, measured (sizes in design px x `scale`): an optional kicker, an optional figure
        (its unit beside it, smaller, in bone), the heading on one or two balanced lines, the body in balanced
        lines (BODY_SPEC, BODY_LEADING, at most BODY_MEASURE characters a line). `facebook_heading`: the heading
        on one line at the content size (CONTENT_HEADING, both copies since lane D cycle 2), else two balanced
        lines at that size, before the usual fit. Returns (rows, shortened)."""
        fx = getattr(px, "exact", px)
        body_spec = body_spec or self.BODY_SPEC
        rows, y, short = [], 0, []
        if kicker:
            # F20: the one kicker (its size per canvas from the ladder, its gap the kicker.gap token)
            fk = get_font(brand.TEXT_FONT, fx(type_px("kicker", "carousel") * 3 / 4 * scale), weight=tok("kicker.weight"))
            text, ks = _fit_kicker(kicker, fk, width)
            y += cap_height(fk)
            rows.append({"name": "kicker", "text": text, "font": fk, "fill": colour_of("kicker.colour"), "y": y,
                         "kicker": True})
            y += kicker_gap(fk.size)
            short += ["kicker"] if ks else []
        if stat:
            # Cycle 0 round 8 (QF-147, ruling 8): the figure whole, in its parts (src/figures.py split_drawn): the hedge
            # in bone on its own line over the figure, at the unit's size ("UP TO"); the number and its unit in gold
            # (the figure face's colour), the unit beside the number, smaller; the dimension word after it in bone.
            from .figures import split_drawn
            hedge, num, unit_g, tail = split_drawn(stat)
            unit = " ".join(x for x in (unit_g, tail) if x)
            size, stacked = 225 * scale, []
            while True:
                fs, fu = get_font(brand.DISPLAY_FONT, fx(size)), get_font(brand.DISPLAY_FONT, fx(max(40, size * 0.42)))
                w = d.textlength(num, font=fs) + (px(18) + d.textlength(unit, font=fu) if unit else 0)
                if w <= width or size <= 96:
                    break
                if unit and len(unit.split()) > 1 and size <= 170 * scale:
                    # A long unit ("MILLION YEARS AGO") is set as a two-line lockup beside the figure, its foot on the
                    # figure's baseline, rather than shrinking the figure or adding a row under it (round 3).
                    size = 225 * scale
                    while size > 96:
                        fs = get_font(brand.DISPLAY_FONT, fx(size))
                        fu = get_font(brand.DISPLAY_FONT, fx(max(40, size * 0.3)))
                        halves = [h for h in split_balanced(unit) if h]
                        lw = max(d.textlength(h, font=fu) for h in halves)
                        if d.textlength(num, font=fs) + px(22) + lw <= width:
                            stacked = halves
                            break
                        size -= 6
                    break
                size -= 6
            gold = figure_colour()
            if hedge:
                fq = fu if not stacked else get_font(brand.DISPLAY_FONT, fx(max(40, size * 0.42)))
                y += cap_height(fq)
                rows.append({"name": "stat", "text": hedge, "font": fq, "fill": brand.BONE, "y": y, "part": "hedge"})
                y += round(cap_height(fq) * 0.6)
            y += cap_height(fs)
            rows.append({"name": "stat", "text": num, "font": fs, "fill": gold, "y": y, "part": "number"})

            def unit_rows(line: str, x: int, yy: int, offset: int) -> None:
                """One unit line in its runs: the unit gold, the dimension word bone (ruling 8)."""
                for text_, is_gold in unit_runs(line, len(unit_g.split()), offset):
                    rows.append({"name": "stat", "text": text_, "font": fu, "y": yy, "x": x,
                                 "fill": gold if is_gold else brand.BONE, "part": "unit" if is_gold else "tail"})
                    x += int(d.textlength(text_ + " ", font=fu))
            if unit and stacked:
                ux = int(d.textlength(num, font=fs)) + px(22)
                step = round(cap_height(fu) * 1.25)
                done = 0
                for j, half in enumerate(stacked):
                    unit_rows(half, ux, y - (len(stacked) - 1 - j) * step, done)
                    done += len(half.split())
            elif unit:
                unit_rows(unit, int(d.textlength(num, font=fs)) + px(18), y, 0)
            y += px(30 * scale)
        # Round 7 (F1, F2, F4, F5): the heading is set by the one engine (src/heading.py) on the type ladder, never fitted
        # to its measure: a cover (two_tone True) at display_xl, locked (the break and the band give way); a content
        # slide at display_l, else its one fallback tier; a two-line break goes before a stop word. A cover lockup's
        # payoff run is gold on one line or two; an interior heading is bone but for a stated quantity.
        # what no bundled face draws (an emoji) is stripped before the engine sets the line (as _text did: 2026-10-29)
        heading = " ".join(drawable(heading, get_font(brand.DISPLAY_FONT, 100)).split())
        form, font_h = (self._set_heading(d, px, heading, width, two_tone, scale, h_spec, facebook_heading)
                        if heading.strip() else (None, None))
        _render_state.heading_form = form
        for j, line_runs in enumerate(form.runs() if form else ()):
            ch = cap_height(font_h)
            line_text = " ".join(t for t, _ in line_runs)
            if j == 0 and kicker:   # the kicker's gap is to the heading's highest ink (an accented capital rises)
                y += accent_rise(d, line_text, font_h, 0)
            elif j > 0:
                # lane D cycle 3 (stress realism, reachable on real copy: Gol Gumbaz 'THE WHISPERING / VÁULT'
                # touched line 1 at 1.58): a second line's accents keep the line gap to the line above, so the
                # pitch grows by the accent's rise, never the accent into the first line's ink
                y += accent_rise(d, line_text, font_h)
            y += ch if j == 0 else round(ch * brand_system.SPEC["lead.display"])
            x = 0
            for text, role in line_runs:
                rows.append({"name": "heading", "text": text, "font": font_h,
                             "fill": brand.GOLD if role in GOLD_ROLES else brand.BONE, "y": y, "x": x})
                x += round(d.textlength(text + " ", font=font_h))
        if body.strip():
            # cycle 0 round 4: on a content slide the body keeps the deck's one body size (TYPE carousel body): the
            # stack's fit scale gives way in the heading, kicker and figure only (the second brand's wider display
            # face had shrunk single bodies to 27-33 design px, a size per slide)
            # Round 5: nor does its own fit shrink it: a content body that does not set in its lines at the deck size
            # takes one more line (the layout gives in lines, never in size); only then is it shortened as before.
            # Round 7 (real-runs sweep S1): one body size per deck, a ladder step only: on a cover, stat or CTA slide
            # whose stack takes a smaller scale the body takes its one fallback tier (TYPE_FALLBACK carousel body),
            # never the stack's continuous scale (41.3 px against 48 on two real decks)
            b_size = body_spec[0] if (facebook_heading or scale >= 1.0) else body_spec[1]
            if facebook_heading:
                b_lines_, font_b, b_short = self._body(d, px, body, width, b_lines, b_size, b_size, trial=True)
                if b_short:
                    b_lines_, font_b, b_short = self._body(d, px, body, width, b_lines + 1, b_size, b_size)
            else:
                b_lines_, font_b, b_short = self._body(d, px, body, width, b_lines, b_size, b_size, trial=True)
                if b_short and b_size != body_spec[1]:
                    b_size = body_spec[1]
                    b_lines_, font_b, b_short = self._body(d, px, body, width, b_lines, b_size, b_size, trial=True)
                if b_short:   # one more line at the tier, never a smaller size (refitted and logged if still short)
                    b_lines_, font_b, b_short = self._body(d, px, body, width, b_lines + 1, b_size, b_size)
            step = round(font_b.size * self.BODY_LEADING)
            y += round(font_b.size * tok("band.body_gap"))   # round 7 (F3): one heading-body gap token
            for j, line in enumerate(b_lines_):
                rows.append({"name": "body", "text": line, "font": font_b, "fill": brand.BONE, "y": y + j * step})
            short += ["body"] if b_short else []
        return rows, short

    def _slide_text(self, im: Image.Image, lay, cw: int, chh: int, heading: str, body: str, kicker: str = "",
                    stat: str = "", full: bool = True, two_tone: bool | str = False, tight: bool = False, b_lines: int = 3,
                    h_spec=(112, 60), extra=None, content: bool = False, deep: bool = False,
                    margin: int | None = None):
        """Plans a slide's stack at the largest scale whose top clears KEY_TOP, its foot on the shared line; on a
        content slide (`content`) the kicker and the heading sit on the deck's fixed baselines
        (_content_baselines) while the stack fits them. Shades behind it (a full-bleed photo) and draws it.
        `extra(rows, y)`: rows appended below the body. `deep`: the gradient behind a full-bleed stack is taken almost
        to ink (a warm or gold photo under gold type: hue gives no contrast, so luminance must). `margin`: the text
        column's side inset, design px (default MARGIN)."""
        px = self._px(cw)
        d = ImageDraw.Draw(im)
        margin = self.MARGIN if margin is None else margin
        width = cw - 2 * px(margin)
        bottom = px(self.KEY_BOTTOM - self.KEY_GAP)
        facebook = bool(getattr(_render_state, "facebook", False))
        if content:
            h_spec = self.CONTENT_HEADING
            b_lines = max(b_lines, self.BODY_LINES)
        fixed = {}

        def place(rows):
            """Content slides: rows on absolute baselines (the kicker on kb, the heading's first line on hb, the
            body's foot on the shared line), or unchanged (bottom-aligned by _stack) when the stack does not fit
            that frame or leads with a figure."""
            fixed.clear()
            drawn_ = [r for r in rows if r.get("text")]
            heads = [r for r in drawn_ if r["name"] == "heading"]
            bodies = [r for r in drawn_ if r["name"] == "body"]
            kick = next((r for r in drawn_ if r["name"] == "kicker"), None)
            if not content or not heads or not bodies or any(r["name"] == "stat" for r in drawn_):
                return rows

            def ink(r, dy, i):   # the row's ink box edge (1 top, 3 bottom) once moved by dy
                return d.textbbox((0, r["y"] + dy), r["text"], font=r["font"], anchor="ls")[i]
            # Round 7 (F3): the band. The body hangs from the heading's LAST baseline by the one gap token (_rows) and
            # the heading's last baseline sits on the deck frame's shared line, so a one-line and a two-line heading
            # differ only in where the heading's top (and the kicker) stand: two positions per deck, never a hole
            # between heading and body. A body longer than the frame reserves lifts the whole band (foot on the
            # shared bottom line); a heading with more lines than the deck frame says so.
            lines = self._frame_lines()
            n_heads = len({r["y"] for r in heads})
            if n_heads > lines:
                log.warning("Carousel heading takes %d lines on a %d-line deck frame: %.60s", n_heads, lines,
                            heading)
            frame = self._content_frame(px, facebook, max(lines, n_heads))
            shift = frame["heading_last"] - max(r["y"] for r in heads)
            over = max(ink(r, shift, 3) for r in bodies) - bottom
            if over > 0:
                shift -= over
            out = [dict(r, y=r["y"] + shift) for r in rows]
            first = min(r["y"] for r in heads) + shift
            kb = None
            if kick:   # the kicker from the deck size's cap (SYS-D-5), so a last-resort heading does not move it
                # both copies (lane D cycle 2, SYS-D-4): the deck's one size; the collage-stepped Facebook tile its own
                # step (lane D cycle 3: its kicker follows its heading)
                spec = (getattr(_render_state, "tile_px", None) or self._deck_size(),)
                # the gap is to the heading's highest ink: an accented capital ("ÇATALHÖYÜK") rises above the cap line
                ink_top = min(ink(r, shift, 1) for r in heads if r["y"] + shift == first)
                cap_top = first - cap_height(get_font(brand.DISPLAY_FONT, px.exact(spec[0])))
                # only a real rise moves it (an accent, 2 px or more): a capital's 1 px antialiasing overshoot keeps
                # the deck's shared kicker line (_content_frame), so a deck keeps its two kicker positions
                kb = (min(cap_top, ink_top) if ink_top < cap_top - 1 else cap_top) - kicker_gap(kick["font"].size)
                if getattr(_render_state, "tile_px", None) and lay is not None:
                    # lane D cycle 3 (lander ruling): the collage-stepped Facebook tile's kicker follows its heading at
                    # the system kicker gap; proximity outranks the deck's two kicker positions on this one slide
                    lay.meta["kicker_line_exempt"] = ("collage_tile_step: the Facebook collage's second tile sets its "
                                                      "heading a ladder step down; its kicker keeps the system gap "
                                                      "to it (proximity over the deck's kicker line)")
                out = [dict(r, y=kb) if r["name"] == "kicker" else r for r in out]
            fixed.update(kicker=kb, heading=first, heading_last=max(r["y"] for r in heads) + shift,
                         heading_frame=frame["heading_lines"], heading_lines=n_heads)
            return out

        scales = (0.88, 0.8, 0.72) if tight else (1.0, 0.92, 0.84, 0.76, 0.7)

        def stack_at(kick: str):
            for scale in scales:
                try:
                    rows, short = self._rows(d, px, heading, body, width, kick, stat, h_spec, b_lines, two_tone,
                                             scale, facebook_heading=content)
                    if extra:
                        rows = extra(rows, scale)
                except TextFitError:   # QF-23: the next smaller scale before the copy is refused
                    if scale == scales[-1]:
                        raise
                    continue
                rows = place(rows)
                drawn = [r for r in rows if r.get("text")]
                if not drawn:
                    return rows, short
                low = max(d.textbbox((0, r["y"]), r["text"], font=r["font"], anchor="ls")[3] for r in drawn)
                high = min(d.textbbox((0, r["y"]), r["text"], font=r["font"], anchor="ls")[1] for r in drawn)
                if bottom - (low - high) >= px(self.KEY_TOP):
                    return rows, short
            log.warning("Carousel slide text taller than the key area at its smallest scale: %.60s", heading)
            return rows, short
        try:
            rows, short = stack_at(kicker)
        except TextFitError:
            # QF-23: a "<SUBJECT> · <CATEGORY>" kicker no scale holds whole names the subject alone rather than being
            # cut: the category is the house label _get_card_category gives the slide ("YOUR TAKE"), no checked copy,
            # and the subject is what a Facebook photo opened on its own needs. Recorded (shortened
            # "kicker_category"), so the stills audit puts it on the review card; any copy that still does not fit
            # refuses the slide
            subject = kicker.split(" · ", 1)[0] if " · " in (kicker or "") else ""
            if not subject:
                raise
            log.warning("Carousel kicker fits no scale whole; the slide names its subject alone: %s", kicker)
            rows, short = stack_at(subject)
            short = [*short, "kicker_category"]
        if lay is not None:
            lay.shortened += short
            # Freeze cycle 5 (TEXT stream; panel 6: the IG cover body at 45.3 px instead of 48 was recorded nowhere):
            # every size fallback of the stack is recorded per slide: the body's tier (TYPE / TYPE_FALLBACK carousel
            # body, design px x 4/3) and why; and every line-break violation left in the body lines THIS stack draws
            # (line_policy.drawn_record of the drawn rows: never a trial fit's, never another surface's; review D2),
            # with every line over the character measure. still_layout.save writes both beside the still.
            body_rows = [r for r in rows if r.get("name") == "body" and r.get("text")]
            if body_rows:
                drawn_px = float(body_rows[0]["font"].size)
                design_px = float(px.exact(self.BODY_SPEC[0])) if hasattr(px, "exact") else float(self.BODY_SPEC[0])
                if abs(drawn_px - design_px) > 0.01:
                    lay.meta["type_fallbacks"] = [f for f in lay.meta.get("type_fallbacks", [])
                                                  if f.get("role") != "body"]
                    lay.meta["type_fallbacks"].append({
                        "role": "body", "px": round(drawn_px, 2), "design_px": round(design_px, 2),
                        "tier": "TYPE_FALLBACK carousel body",
                        "reason": "the slide's stack did not clear the key area at scale 1.0: the body takes its one "
                                  "fallback tier, never a continuous scale"})
            from . import line_policy as _lp
            faults = _lp.drawn_record([r["text"] for r in body_rows], self.BODY_MEASURE, role="body")
            if faults:
                lay.meta["line_faults"] = [f for f in lay.meta.get("line_faults", []) if f.get("role") != "body"] + faults
        drawn = [r for r in rows if r.get("text")]
        form = getattr(_render_state, "heading_form", None)
        stack_bottom = bottom
        if fixed and drawn:   # round 7 (F3): the band is placed on absolute baselines; _stack keeps them
            stack_bottom = max(d.textbbox((0, r["y"]), r["text"], font=r["font"], anchor="ls")[3] for r in drawn)
        if full and drawn:
            low = max(d.textbbox((0, r["y"]), r["text"], font=r["font"], anchor="ls")[3] for r in drawn)
            high = min(d.textbbox((0, r["y"]), r["text"], font=r["font"], anchor="ls")[1] for r in drawn)
            cap = 0.95 if deep else 0.90
            need = text_band_need(im, d, drawn, px(margin), stack_bottom)
            got: dict = {}
            plain = text_band_boxes(d, drawn, px(margin), stack_bottom)
            from . import plate_form as _pf
            if _pf.active() != "base":
                plain = text_band_items(d, drawn, px(margin), stack_bottom, px)
            im = scrim(im, stack_bottom - (low - high) - px(40), chh, feather=px(300), target=0.08 if deep else 0.26,
                       cap=cap, out=got, plain=plain)
            if lay is not None:
                lay.meta["text_band"] = text_band_record(need, got.get("strength", 0.0))
                if "plate_form" in got:
                    lay.meta["plate_form"] = got["plate_form"]
            if lay is not None and deep:
                lay.meta["ink_band"] = True
        elif drawn and lay is not None:
            # PLATES-15: a split's stack stands on its INK panel (no band of its own); a line that starts on the
            # photograph's fade under 7:1 is recorded with its ceiling (ruling N1: no band is added)
            need = text_band_need(im, d, drawn, px(margin), stack_bottom)
            if need > 0:
                lay.meta["text_band"] = text_band_record(need, 0.0)
        plates = getattr(lay, "plates", None) if lay is not None else None
        if plates is None:
            plates = {}
            if lay is not None:
                lay.plates = plates
        im, _ = self._stack(im.convert("RGBA"), lay, rows, px(margin), stack_bottom, plates)
        if lay is not None and fixed:
            lay.meta["baselines"] = dict(fixed)
        if lay is not None and form is not None and heading.strip():
            lay.meta["heading"] = form.as_record()   # the engine's form, as drawn (tests hold drawn == engine)
        return im.convert("RGB")

    def _split_ground(self, photo: Path, cw: int, chh: int, photo2: Path = None) -> Image.Image:
        """Photo over ink: the photo (or two, stacked) across the top, fading into the ink panel the text sits on.
        Cycle 0 round 7 (panel F8, F9): the fade is the band-edge token's (scrim -> band_edge.feather: its length
        grows with the luma step, so a bright ground is no lid), the same for one photo or two, ending where the
        single photo's does; two photographs are two windows on an INK gutter of tok("plate.gutter") design px (an
        8 px gutter between two pictures of one subject read as a seam inside one composite picture)."""
        from .brand_system import tok
        px = self._px(cw)
        ph = px(self.SPLIT_PHOTO_H_FB if getattr(_render_state, "facebook", False) else self.SPLIT_PHOTO_H)
        im = Image.new("RGB", (cw, chh), brand.INK2)
        if photo2 is None:
            im.paste(self._fill(photo, (cw, ph + px(150)), view=(0, 0, cw, ph)), (0, 0))
        else:
            # Two whole photographs with an ink gutter (2026-11-02 design review); the lower one runs on under the
            # band's fade as the single photo does, so the fade has room without eating its picture.
            gutter = px(tok("plate.gutter"))
            half = (ph + px(40) - gutter) // 2
            im.paste(self._fill(photo, (cw, half)), (0, 0))
            im.paste(self._fill(photo2, (cw, ph + px(150) - half - gutter), top=False,
                                view=(0, 0, cw, max(1, ph - half - gutter))), (0, half + gutter))
        return scrim(im, ph + px(60), chh, feather=px(40 if photo2 is not None else 180), target=0.0, cap=1.0,
                     floor=1.0, colour=brand.INK2)

    def _render_cover(self, photo: Path, card: dict[str, Any], cw: int, chh: int,
                      lay=None, tight: bool = False) -> Image.Image:
        """The cover: full-bleed, the heading in two balanced Anton lines (bone, then gold), the body under it. On the
        Facebook copy the cover is the collage's tall 2:3 hero tile, which shows the slide's centre 2:3 (x 120-1320 at
        1440): its text column is FB_COVER_MARGIN in from each side, so no letter is cut (round 4, 2026-11-02:
        "THE EYE OF" lost its T, the body its first letters)."""
        im = self._fill(photo, (cw, chh), view=(0, 0, cw, round(chh * self.PHOTO_VIEW["cover"])))
        facebook = bool(getattr(_render_state, "facebook", False))
        return self._slide_text(im, lay, cw, chh, card["heading"].upper(), card["body"], two_tone=True, tight=tight,
                                b_lines=3, h_spec=(150, 72), margin=self.FB_COVER_MARGIN if self._fb_cover_inset() else None)

    def _render_immersive(self, photo: Path, card: dict[str, Any], index: int, cw: int, chh: int,
                          lay=None, tight: bool = False) -> Image.Image:
        """Full-bleed photograph, the stack over an ink gradient at its foot."""
        im = self._fill(photo, (cw, chh), view=(0, 0, cw, round(chh * self.PHOTO_VIEW["immersive"])))
        tag = self._tag(self._get_card_category(card, "immersive"), index)
        return self._slide_text(im, lay, cw, chh, card["heading"].upper(), card["body"], kicker=tag, tight=tight,
                                two_tone="wrap", h_spec=(112, 60), content=True)

    def _render_comparison(self, photo1: Path, photo2: Path, card: dict[str, Any], index: int, cw: int, chh: int,
                           lay=None, tight: bool = False) -> Image.Image:
        """Two photographs stacked over the ink panel. No "REF A // SURFACE CONTEXT" labels over them: every
        comparison slide said so, whatever it showed (platform audit, 2026-10-28)."""
        im = self._split_ground(photo1, cw, chh, photo2)
        tag = self._tag(self._get_card_category(card, "comparison"), index)
        return self._slide_text(im, lay, cw, chh, card["heading"].upper(), card["body"], kicker=tag, full=False,
                                tight=tight, two_tone="wrap", b_lines=4, h_spec=(96, 56), content=True)

    def _render_blueprint(self, photo: Path, card: dict[str, Any], index: int, cw: int, chh: int,
                          lay=None, tight: bool = False) -> Image.Image:
        """A structure slide: the split layout (the drafting grid it drew was decoration that said nothing)."""
        im = self._split_ground(photo, cw, chh)
        tag = self._tag(self._get_card_category(card, "blueprint"), index)
        return self._slide_text(im, lay, cw, chh, card["heading"].upper(), card["body"], kicker=tag, full=False,
                                tight=tight, two_tone="wrap", b_lines=4, h_spec=(96, 56), content=True)

    def _render_colossal_stat(self, photo: Path, card: dict[str, Any], index: int, cw: int, chh: int,
                              lay=None, tight: bool = False) -> Image.Image:
        """A figure slide: the split layout led by the figure its BODY states, whole (src/figures.py: hedge, number or
        span, unit; cycle 0 round 8, QF-147). The body is the slide's signed copy and must state the drawn figure: a
        body that states none gets the no-figure layout, never a figure from the heading over a body about something
        else ("280 FEET" over "between eight and 18 levels"), a truncation ("11" for 11.5 ft, "41" for 41.75 m), one
        end of a span ("18 LEVELS", "137 FEET") or a figure without its hedge ("20,000 PEOPLE" for "up to 20,000").
        The drawn figure is checked against the body (figures.truth_faults) and recorded with its parts. No "VERIFIED
        BY ARCHEOLOGICAL RECORD" pill (platform audit, 2026-10-28)."""
        from . import figures
        heading, body = card["heading"].upper(), card["body"]
        im = self._split_ground(photo, cw, chh)
        fig = figures.stat_figure(body)
        # Lane D cycle 1 (carry-in 4): the figure is drawn whole WITH its era ("9500–9000 BCE", "ABOUT 2560 BC"),
        # the era in the unit's place and bone (src/figures.py: an era is never gold): round 8 drew it without
        # (era=False), so a descending BCE span failed its own truth check and was withheld, and a single BC year
        # was drawn bare ("ABOUT 2560" for "about 2560 BC", a different year). An era-crossing span ("500 BC–AD
        # 500") has no stat layout (its eras sit inside the number): withheld, recorded (truth, fail closed).
        stat = fig.drawn(era=True) if fig is not None else ""
        faults = figures.truth_faults(stat, [body]) if stat else []
        if fig is not None and fig.era_hi:
            faults = faults + [f"{stat!r}: an era-crossing span has no stat layout"]
        if faults:   # never drawn: a figure the body does not state whole (the critic's own check, fail closed)
            log.warning("Stat slide figure withheld (%s).", "; ".join(faults))
            stat = ""
        lead = getattr(_render_state, "lead", None)
        system = figures._system(fig.unit) if fig is not None and fig.measured else None
        kept = bool(lead and system and system != lead)
        if lay is not None:
            # the deck's figure record (cycle 0 round 8, lander ruling): a figure the body states in one unit only,
            # with no signed pair in the deck's lead unit, stays in its own unit (truth outranks one system), and says so
            lay.meta["stat_figure"] = ({**fig.record(), "drawn": stat, "source": "body", "faults": faults,
                                        "deck_lead": lead,
                                        "lead_unit": ("kept: no signed pair in the deck's lead unit" if kept
                                                      else "deck lead" if system else "")}
                                       if fig is not None
                                       else {"drawn": "", "source": "", "reason": "the body states no figure"})
            if stat:
                lay.meta["stat"] = stat
        tag = self._tag(self._get_card_category(card, "colossal_stat"), index)
        return self._slide_text(im, lay, cw, chh, heading, body, kicker=tag, stat=stat, full=False, tight=tight,
                                b_lines=3, h_spec=(84, 52))   # its figure leads: bottom-aligned

    def _render_debate(self, photo: Path, card: dict[str, Any], index: int, total: int, cw: int, chh: int, poll: dict[str, Any], f_small,
                       share: str = "", lay=None, tight: bool = False) -> Image.Image:
        """The closing slide: full-bleed, the heading (its last line gold), the card's question, then the run's
        share-and-follow line (operator decision 2026-10-25) as a sentence under a short gold rule. No "TELL US IN
        THE COMMENTS" footer (operator decision 2026-10-29: a second ask beside the share line)."""
        px = self._px(cw)
        im = self._fill(photo, (cw, chh), view=(0, 0, cw, round(chh * self.PHOTO_VIEW["debate"])))
        tag = self._tag(self._get_card_category(card, "debate"), index)
        width = cw - 2 * px(self.MARGIN)
        d = ImageDraw.Draw(im)
        rule = {}

        def with_share(rows, scale):
            if not share:
                return rows
            lines, f_share, sh_short = self._fit_text(d, share, brand.TEXT_FONT, int(36 * scale), width, 2, 28, px=px,
                                                      weight=SHARE_WEIGHT)
            # Round 4 (2026-11-02): the ask one weight above the question (SHARE_WEIGHT), a second voice, and
            # a full body line of air above its gold rule (at one weight and 46 px the two read as one block).
            f_share = get_font(brand.TEXT_FONT, f_share.size, weight=SHARE_WEIGHT)
            # Its break as the body's (round 4 review): never a line ending on "a" or "and" where another fits.
            lines = even_rag(d, lines, f_share, width, max([self.BODY_MEASURE] + [len(ln) for ln in lines]))
            if sh_short and lay is not None and "share" not in lay.shortened:
                lay.shortened.append("share")
            last = max((r["y"] for r in rows), default=0)
            body_step = round(px.exact(self.BODY_SPEC[0] * scale) * self.BODY_LEADING)
            rule["y"] = last + body_step + px(16 * scale)           # the rule's top, relative to the stack
            y = rule["y"] + px(6) + px(26 * scale) + cap_height(f_share)
            step = round(f_share.size * 1.38)
            return rows + [{"name": "share", "text": ln, "font": f_share, "fill": brand.BONE, "y": y + j * step}
                           for j, ln in enumerate(lines)]

        # Round 3 (2026-11-02): over a warm or gold photo the gold line has no hue contrast ("HIDDEN MARVELS" in gold
        # over the gold cave measured 4.88), so the stack sits on an ink lower band.
        warm = warm_ground(im, (0, chh // 2, cw, chh))
        im = self._slide_text(im, lay, cw, chh, card["heading"].upper(), card["body"], kicker=tag, two_tone=True,
                              tight=tight, b_lines=3, h_spec=(112, 60), extra=with_share, deep=warm)
        share_box = next((e["box"] for e in (lay.elements if lay is not None else []) if e["name"] == "share"), None)
        if share_box and "y" in rule:
            # The gold rule: 6 design px tall, 96 wide, its own height above the share line's first cap.
            r = share_rule_box(px(self.MARGIN), share_box[1], px)
            ImageDraw.Draw(im).rectangle([r[0], r[1], r[2] - 1, r[3] - 1], fill=brand.GOLD)
        return im

    def _diagram_fit(self, size: tuple[int, int], box: tuple[int, int, int, int], cw: int, facebook: bool,
                     lines: int) -> tuple[int, int, float]:
        """(top, bottom, k): the room a diagram slide's drawing takes on a `lines` deck frame (above the deck's
        kicker, under the masthead) and the scale `k` its drawn box (`box` of a poster of `size`) is laid at."""
        px = self._px(cw)
        kb = self._content_baselines(px, facebook, lines)[0]
        top, bottom = px(self.HEADER_Y) + px(80), kb - px(34) - px(34)   # the kicker cap, then air
        w, h = max(1, box[2] - box[0]), max(1, box[3] - box[1])
        return top, bottom, min((cw - 2 * px(self.MARGIN - 24)) / w, max(1, bottom - top) / h)

    def diagram_room(self, cw: int | None = None) -> tuple[int, int]:
        """(w, h) the diagram slide's drawing takes on the two-line deck frame (the smaller room; the same on both
        copies since they share one frame): what a drawing for the slide is drawn at (lane D cycle 2)."""
        cw = cw or self.CAROUSEL_SIZE[0]
        px = self._px(cw)
        top, bottom, _k = self._diagram_fit((1, 1), (0, 0, 1, 1), cw, False, 2)
        return cw - 2 * px(self.MARGIN - 24), bottom - top

    def diagram_legible(self, poster: Path, facebook_lines: tuple[tuple[bool, int], ...]) -> tuple[bool, dict]:
        """Lane D cycle 1 (r8 AD R3): whether a drawing laid on the diagram slide keeps every label at the carousel's
        label floor (brand_system.label_floor_px of the slide: TYPE_FLOOR["carousel"]["label"], 36 px on 1440) on
        every copy and frame in `facebook_lines` ((facebook, frame lines) pairs). A drawing guarantees its own canvas's
        floor only (graphics.label_floor: 27 px on a 9:16 poster, none on 16:9), so at scale k its labels are at
        least k x that: the slide holds it only when k x poster floor >= slide floor. The 9:16 scale drawing shrunk
        to ~0.4 of the slide drew 19 px labels; such a slide keeps its photo until the drawing is drawn for the
        slide's own canvas (SYS-D request). The locator map is laid full bleed at 1440/1080 (27 x 4/3 = 36) and is
        judged the same way, by its own scale."""
        from . import graphics
        from .brand_system import label_floor_px
        cw, chh = self.CAROUSEL_SIZE
        with Image.open(poster) as src:
            g = src.convert("RGB")
        own = graphics.label_floor(*g.size)
        need = label_floor_px(cw, chh)
        drawing = slide_drawing(poster)
        if drawing:   # drawn for the slide's room (lane D cycle 2): laid 1:1 when it fits the room of every frame
            own = float(drawing["drawing_floor"])
            rooms = [self._diagram_fit(g.size, (0, 0) + g.size, cw, fb, lines) for fb, lines in facebook_lines]
            ks = [1.0 if g.width <= cw - 2 * self._px(cw)(self.MARGIN - 24) and g.height <= b - t else 0.0
                  for t, b, _k in rooms]
        elif is_map_poster(poster):
            ks = [cw / g.width]
        else:
            _ground, box = diagram_crop(g)
            ks = [self._diagram_fit(g.size, box, cw, fb, lines)[2] for fb, lines in facebook_lines]
        k = min(ks)
        rec = {"poster_floor_px": own, "scale": round(k, 3), "label_px_at_least": round(own * k, 1),
               "slide_floor_px": round(need, 1)}
        return own * k >= need - 1e-6, rec

    def _render_diagram(self, poster: Path, card: dict[str, Any], index: int, cw: int, chh: int,
                        lay=None, tight: bool = False) -> Image.Image:
        """The run's explanatory graphic (its poster frame) re-laid at 4:5 (2026-11-02 panel round 2): the drawing
        cropped to what it draws, on its own ink ground above the deck's fixed text baselines, with the card's own
        kicker, heading and body. Nothing is added to the drawing; its own labels and qualifiers stay as drawn."""
        px = self._px(cw)
        if is_map_poster(poster):
            return self._render_map_slide(poster, card, index, cw, chh, lay=lay, tight=tight)
        with Image.open(poster) as src:
            g = src.convert("RGB")
        ground, box = diagram_crop(g)
        im = Image.new("RGB", (cw, chh), ground)
        facebook = bool(getattr(_render_state, "facebook", False))
        # The drawing takes the room above the deck's kicker (round 3 fix): on a one-line deck frame it has the
        # room a second heading line would have taken; it never moves the kicker off the deck's line. A heading
        # longer than the deck frame (see place() in _slide_text) takes the two-line frame here too.
        heading = card["heading"].upper()
        lines = max(self._frame_lines(), self._heading_lines(ImageDraw.Draw(im), px, heading, cw - 2 * px(self.MARGIN)))
        top, bottom, k = self._diagram_fit(g.size, box, cw, facebook, lines)
        if slide_drawing(poster):   # drawn for this room (lane D cycle 2): laid whole, 1:1, never rescaled
            ground = tuple(int(v) for v in g.getpixel((0, 0)))
            im = Image.new("RGB", (cw, chh), ground)
            art = g
        else:
            art = g.crop(box)
            art = art.resize((max(1, round(art.width * k)), max(1, round(art.height * k))), Image.Resampling.LANCZOS)
        ax, ay = (cw - art.width) // 2, top + (bottom - top - art.height) // 2
        # Feathered into the ground over its padding, so the drawing's own soft backdrop shows no hard edge.
        from PIL import ImageFilter
        f = max(4, px(14))
        mask = Image.new("L", art.size, 0)
        ImageDraw.Draw(mask).rectangle((f, f, art.width - 1 - f, art.height - 1 - f), fill=255)
        im.paste(art, (ax, ay), mask.filter(ImageFilter.GaussianBlur(f / 2)))
        if lay is not None:
            lay.meta["diagram"] = {"poster": str(poster), "source_box": list(box),
                                   "image_box": [ax, ay, ax + art.width, ay + art.height]}
        tag = self._tag(self._get_card_category(card, "diagram"), index)
        return self._slide_text(im, lay, cw, chh, heading, card["body"], kicker=tag, full=False,
                                tight=tight, two_tone="wrap", b_lines=4, content=True)

    def _render_map_slide(self, poster: Path, card: dict[str, Any], index: int, cw: int, chh: int,
                          lay=None, tight: bool = False) -> Image.Image:
        """The locator map's resting frame full bleed (round 4): scaled to the slide's width and cut so the poster's
        centre (the pin) sits at MAP_FOCUS of the slide, never cropped to a drawn box (diagram_crop: a map has no
        flat ground); the card's stack over an ink foot (MAP_FOOT_INK), as on a full-bleed photo slide.
        Round 4 review (2026-11-02): the map's own labels are measured (map_label_bands) against the stack. The
        place label and pin must show whole above the foot; every other label (the scale bar, a neighbour's name)
        shows whole above the foot or lies wholly under its ink, never half under it or behind the text. When the
        pin's resting place leaves no such cut, the map rises (up to MAP_LIFT_MAX design px), then the fade
        shortens (MAP_FADE down to MAP_FADE_MIN). What it chose is in lay.meta["diagram"]; a map it could not
        clear says so there ("labels_clear": False) and in the log."""
        import numpy as np

        from .still_layout import Layout
        px = self._px(cw)
        with Image.open(poster) as src:
            g = src.convert("RGB")
        k = cw / g.width
        tag = self._tag(self._get_card_category(card, "diagram"), index)
        heading = card["heading"].upper()

        def stack(im_, lay_):
            return self._slide_text(im_, lay_, cw, chh, heading, card["body"], kicker=tag, tight=tight,
                                    two_tone="wrap", h_spec=(112, 60), content=True, full=False)
        # Where the stack will stand: a dry run on a throwaway layout (its rows do not depend on the pixels).
        probe = Layout("carousel", (cw, chh), self._zone(cw, chh), tight=tight)
        probe.plates = {}
        stack(Image.new("RGB", (cw, chh), brand.INK), probe)
        texts = [e["box"] for e in probe.elements]
        stack_top = min(b[1] for b in texts) if texts else chh
        if g.height * k < chh:                     # a wide poster: the usual centre crop
            full = cover_crop(poster, (cw, chh)).convert("RGB")
            tops, base_top, record = [0], 0, None   # its crop is not the record's frame: measured by pixels only
        else:
            full = g.resize((cw, round(g.height * k)), Image.Resampling.LANCZOS)
            h = full.height
            base_top = int(min(max(0, MAP_FOCUS[0] * h - MAP_FOCUS[1] * chh), h - chh))
            lift_max = min(h - chh, base_top + px(MAP_LIFT_MAX))
            tops, record = list(range(base_top, lift_max + 1, max(1, px(8)))) or [base_top], poster
            if tops[-1] != lift_max:
                tops.append(lift_max)
        bands = map_label_bands(record, full, k, px)
        pin_y = full.height / 2
        primary, secondary = [], []
        for b in bands:   # the place label stands over the pin; the pin itself is MAP_PIN design px about its centre
            (primary if b[3] > pin_y - px(MAP_LABEL_REACH) and b[1] < pin_y + px(MAP_PIN) else secondary).append(b)
        primary_bottom = max([pin_y + px(MAP_PIN)] + [b[3] for b in primary])
        primary_top = min([pin_y - px(MAP_PIN)] + [b[1] for b in primary])
        foot_low = stack_top - px(MAP_CLEAR)          # the foot's full ink starts here at the latest
        head_room = px(self.HEADER_Y) + px(80)         # the place label stays under the masthead, as a drawing does

        def cut(top, fade):
            """(top, fade, y1) when this cut shows the place whole and no label half under the foot, else None."""
            if primary_top - top < head_room:
                return None
            y1 = foot_low
            for b in sorted(secondary, key=lambda b: -b[3]):   # from the foot up: a half-covered label is covered
                if b[1] - top < y1 and b[3] - top > y1 - fade:
                    y1 = min(y1, b[1] - top)
            return (top, fade, y1) if y1 - fade >= primary_bottom - top + px(MAP_GAP) else None
        step = max(1, px(10))
        choice = None
        # A soft foot (MAP_FADE_GOOD or more) at the least lift first; only then a shorter fade.
        for fades in ([*range(px(MAP_FADE), px(MAP_FADE_GOOD), -step), px(MAP_FADE_GOOD)],
                      [*range(px(MAP_FADE_GOOD) - step, px(MAP_FADE_MIN), -step), px(MAP_FADE_MIN)]):
            choice = next((c for top in tops for fade in fades if (c := cut(top, fade))), None)
            if choice:
                break
        clear = choice is not None
        if not clear:
            log.warning("Locator map slide: no cut keeps the map's labels clear of the text (%.40s).", heading)
            choice = (tops[0], px(MAP_FADE_MIN), foot_low)
        top, fade, y1 = choice
        im = full.crop((0, top, cw, top + chh))
        # The ink foot: none above y1 - fade, a smooth rise to MAP_FOOT_INK at y1, held to the slide's foot.
        ys = np.arange(chh, dtype=np.float32)
        u = np.clip((ys - (y1 - fade)) / max(1, fade), 0, 1)
        alpha = (u * u * (3 - 2 * u) * MAP_FOOT_INK)[:, None, None]
        a = np.asarray(im, dtype=np.float32)
        im = Image.fromarray(np.clip(a * (1 - alpha) + np.array(brand.INK, np.float32) * alpha, 0, 255)
                             .astype(np.uint8), "RGB")
        labels = []
        for b in bands:
            y0b, y1b = b[1] - top, b[3] - top
            if y1b <= 0 or y0b >= chh:
                continue
            shown = y1b <= y1 - fade
            labels.append({"box": [int(b[0]), round(y0b), int(b[2]), round(y1b)],
                           "place": b in primary, "shown": bool(shown)})
        if lay is not None:
            window = None if g.height * k < chh else [0, round(top / k), g.width, round((top + chh) / k)]
            lay.meta["diagram"] = {"poster": str(poster), "kind": MAP_KIND, "source_box": window,
                                   "image_box": [0, 0, cw, chh], "lift": int(top - base_top),
                                   "foot": [int(y1 - fade), int(y1)], "stack_top": int(stack_top),
                                   "labels": labels, "labels_clear": clear}
        return stack(im, lay)

    def _render_editorial_split(self, photo: Path, card: dict[str, Any], index: int, cw: int, chh: int,
                                lay=None, tight: bool = False) -> Image.Image:
        """Photo over ink, the stack on the ink panel."""
        im = self._split_ground(photo, cw, chh)
        tag = self._tag(self._get_card_category(card, "editorial_split"), index)
        return self._slide_text(im, lay, cw, chh, card["heading"].upper(), card["body"], kicker=tag, full=False,
                                tight=tight, two_tone="wrap", b_lines=4, h_spec=(96, 56), content=True)

    @_holds_text_contrast
    def _render_carousel_slide(self, archetype: str, photo: Path, photo_next: Path, card: dict[str, Any], i: int,
                               total: int, poll: dict[str, Any], share: str, facebook: bool, tight: bool = False,
                               subject: str = "", heading_frame: int = 2, strip: dict[str, Any] | None = None,
                               lead: str | None = None, window: int = 0, heading_tier: int = 0,
                               heading_px: float | None = None):
        """One carousel slide and its measured layout: the archetype's own text, then the brand in its band at the
        top-left. Nothing is drawn under Instagram's counter (`protected`). Both copies are free of slide chrome
        (operator, 2026-10-28); `facebook` now changes only the words: the grid copy's slide text says nothing
        about swiping ("Swipe to see how ..." is drawn "See how ..."), and neither copy draws a direction cue.
        `tight`: smaller type, and the brand on a solid pill so it reads over any photo (Richat 2's header
        "collides with the spacecraft's solar panel"). `heading_frame`: the deck's content frame (1 or 2 heading
        lines, _deck_heading_frame); 2, round 2's frame, when the slide is drawn alone. `strip`: the Instagram copy's
        swipe strip on slide 1 or 2 (swipe_strip). `heading_tier`: the deck's content-heading tier
        (_deck_heading_tier, SYS-D-5); 0, the design size, when drawn alone."""
        from .script_engine import facebook_photo_caption, neutral_cues
        from .still_layout import Layout
        clean = facebook_photo_caption if facebook else neutral_cues
        card = dict(card, heading=typeset(clean(card.get("heading") or "")), body=typeset(clean(card.get("body") or "")))
        share = typeset(clean(share or ""))
        # Every slide after the cover names its subject in its kicker: Facebook's viewer opens one photo at a time
        # with the caption on the first only (2026-11-01), and Instagram's copy carries the same kicker so the two
        # copies keep one system and the gold rule (2026-11-02 design review).
        # Per thread: the Instagram and Facebook copies render at once on one generator (PR review, 2026-11-02).
        _render_state.subject = typeset(subject).upper() if i > 0 else ""
        _render_state.facebook = bool(facebook)
        _render_state.heading_frame = 2 if int(heading_frame or 2) > 1 else 1
        _render_state.heading_tier = 1 if int(heading_tier or 0) > 0 else 0
        _render_state.lead = lead
        # lane D cycle 3 (lander ruling 2 of the second review): the Facebook collage's second top tile, a headline
        # slide, sets its heading at this ladder step (design px) so the cover reads over it; None: the deck's tier
        _render_state.tile_px = float(heading_px) if heading_px else None
        cw, chh = self.CAROUSEL_SIZE
        # cycle 0 round 8 (the deck window rule, hero_window.deck_window): `window` is how many earlier slides of
        # this deck show the same photograph; the masthead's box (top-left, HEADER_Y) is kept off foreign objects
        # lane D cycle 3: the deck's posted collage decides the Facebook cover's inset (_fb_cover_inset)
        _render_state.fb_hero_tile = bool(facebook) and total <= 4
        _render_state.window_k = max(0, int(window or 0))
        _render_state.masthead_box = self._masthead_box(cw, bool(facebook), archetype,
                                                        inset=self._fb_cover_inset() and archetype == "cover")
        _render_state.window_rec = []
        lay = Layout("carousel", (cw, chh), self._zone(cw, chh), tight=tight,
                     protected=[("instagram_counter", self._ig_counter(cw))])
        lay.plates = {}
        px = self._px(cw)
        f_small = get_font(brand.TEXT_FONT, px.exact(28))
        if archetype == "cover":
            im = self._render_cover(photo, card, cw, chh, lay=lay, tight=tight)
        elif archetype == "immersive":
            im = self._render_immersive(photo, card, i, cw, chh, lay=lay, tight=tight)
        elif archetype == "comparison":
            im = self._render_comparison(photo, photo_next, card, i, cw, chh, lay=lay, tight=tight)
        elif archetype == "blueprint":
            im = self._render_blueprint(photo, card, i, cw, chh, lay=lay, tight=tight)
        elif archetype == "colossal_stat":
            im = self._render_colossal_stat(photo, card, i, cw, chh, lay=lay, tight=tight)
        elif archetype == "diagram":
            im = self._render_diagram(photo, card, i, cw, chh, lay=lay, tight=tight)
        elif archetype == "debate":
            im = self._render_debate(photo, card, i, total, cw, chh, poll, f_small, share=share, lay=lay, tight=tight)
        else:
            im = self._render_editorial_split(photo, card, i, cw, chh, lay=lay, tight=tight)
        if _render_state.window_rec:
            lay.meta["windows"] = list(_render_state.window_rec)
        _render_state.window_k, _render_state.masthead_box, _render_state.window_rec = 0, None, None

        # The brand, top-left (clear of Instagram's counter, see HEADER_Y): the system's one masthead
        # (src/masthead.py, cycle 0): the gold diamond, then the masthead face in tracked bone capitals at the
        # carousel's masthead size, its ground solved as ruled (a top-edge ink gradient up to 0.60 at its peak, a
        # halo up to 0.16, the light-ground variant where bone cannot reach the gate), never on a pill.
        from . import masthead
        size = type_px("masthead", "carousel") * cw / 1440
        brand_font = masthead.font_for(size)
        # On the Facebook cover (the collage's 2:3 hero tile) the masthead keeps the cover's inset too (round 4: its
        # diamond fell outside the tile's crop at x 107).
        x0 = px(self.FB_COVER_MARGIN) if self._fb_cover_inset() and archetype == "cover" else px(80)
        base = px(self._header_y(facebook, archetype)) + cap_height(brand_font)
        # its room: up to Instagram's counter region (cw - 280 design px), never past it and never clipped
        if facebook and archetype != "cover":
            # lane D cycle 3 (lander ruling 1 of the second review): the Facebook collage is branded once, on its
            # cover tile; an interior Facebook slide draws no masthead (recorded, not a gate failure)
            im, plate, box = im.convert("RGB"), None, None
            mrec = {"shown": False, "reason": "Facebook interior slide: the collage carries the brand on its cover "
                                              "tile only (lane D cycle 3)", "fb_interior": True}
        else:
            im, plate, mrec, box = masthead.draw(im.convert("RGB"), x0, base, size, reach=base + px(300),
                                                 max_width=cw - px(280) - x0)
        if not mrec["shown"] and facebook and archetype == "cover":
            # lane D cycle 3: the masthead's ground is a TOP-EDGE gradient (masthead.top_scrim), so at FB_HEADER_Y it
            # is weaker on the name than at HEADER_Y (6 of 28 real Facebook slides fell under the gate). Only then,
            # the reach that gives the name the strength it has at Instagram's position (the same share of the
            # ramp), every ruled step and the gate unchanged; still under the gate, the masthead stays undrawn.
            ig_base = px(self.HEADER_Y) + cap_height(brand_font)
            matched = (ig_base + px(300)) * base / ig_base
            im, plate, mrec, box = masthead.draw(im.convert("RGB"), x0, base, size, reach=matched,
                                                 max_width=cw - px(280) - x0)
            mrec["reach_matched"] = round(matched)
        if mrec["shown"]:   # one header element, as before: its box takes in the mark
            lay.add("header_brand", box)
            lay.elements[-1].update(plate.record())
            lay.elements[-1]["masthead"] = {k: v for k, v in mrec.items() if k != "box"}
            lay.plates.setdefault("header_brand", []).append(plate)
        if isinstance(getattr(lay, "meta", None), dict):   # fail closed: an undrawn masthead says why in the record
            lay.meta["masthead"] = {k: v for k, v in mrec.items() if k != "box"}
        if strip and not facebook:
            im = swipe_strip(im, lay, strip)
        return im, lay

    def generate_carousel_sequence(self, scene_images: list[Path], script_payload: dict[str, Any], output_dir: Path,
                                   facebook: bool = False, graphics: list[Path] | None = None,
                                   strict_match: bool = False) -> list[Path]:
        """Multi-Template Editorial Carousel (CAROUSEL_SIZE, 1440x1800, 4:5):
        Dynamically routes each slide to one of five editorial layout archetypes
        (Full-Bleed Immersive, Comparative Side-by-Side, Architectural Blueprint, Colossal Stat, Debate).
        facebook=True: the same slides for Facebook's multi-photo post (carousel_fb/). Neither copy has a swipe
        cue, slide counter, progress bar or dots any more (operator, 2026-10-28: Instagram draws its own counter
        and dots; Facebook shows the set as a grid); they differ only in that the grid copy's text never says
        "swipe". The folder and the publisher's per-platform request are kept.
        Each slide is measured and checked after drawing (still_layout); a failing one is drawn once more
        with the tighter fit, and its record is kept in carousel*/still_layout.json.
        `graphics`: the run's graphics (graphic_*.mp4, each with its .jpg poster beside it; default: those in
        output_dir/graphics). When one is an explanatory drawing (DIAGRAM_KINDS), slide 3 or 4 shows it instead of
        a photo (carousel_diagram), the same slide in both copies; without one nothing changes.
        `strict_match`: a failed photo match raises PhotoMatchFailed instead of rotating the photos (QF-81).
        An AI-made picture (ai_label.is_ai_picture) is never a slide's photo; with no real photo left, NoRealPhotos
        is raised and no slide is drawn (QF-65c)."""
        from . import still_layout
        from .ai_label import no_real_photo_reason, real_stills
        out_dir = output_dir / ("carousel_fb" if facebook else "carousel")
        out_dir.mkdir(parents=True, exist_ok=True)
        stills, left_out = real_stills([p for p in scene_images if not is_video(p)])
        if not stills:
            raise NoRealPhotos(no_real_photo_reason(left_out, "carousel"))
        cards = script_payload["carousel_slides"]
        poll = script_payload.get("poll") or {}
        total = len(cards)
        photos = self._carousel_photos(cards, stills, output_dir, strict=strict_match)
        from .script_engine import share_prompt
        share = share_prompt(script_payload)  # the closing slide asks for a share and a follow (2026-10-25)
        narration = script_payload.get("narration_script") or ""
        subject = standalone_subject(script_payload)   # both copies (2026-11-02): one rule for IG and FB
        gate_surface = "Facebook carousel" if facebook else "carousel"
        # Lane D cycle 4 (the slide-drop fallback, cycle 3 ruling b): a content slide the figure gate would leave with
        # nothing readable is dropped, recorded, when the deck stays coherent (slides_to_drop); otherwise gate_deck
        # refuses the deck below, as before. Decided on the copy as drawn, the same on both copies.
        from .figures import deck_lead
        lead0 = deck_lead(script_payload)
        drop = slides_to_drop([dict(card, **{k: spoken_units(card[k], narration, lead0) for k in ("heading", "body")
                                             if isinstance(card.get(k), str)}) for card in cards],
                              script_payload, gate_surface)
        dropped_rows: list[dict[str, Any]] = []
        if drop:
            gone = {e["slide"] - 1 for e in drop}
            dropped_rows = record_dropped(script_payload, gate_surface, drop, cards, len(cards) - len(gone))
            cards = [c for i, c in enumerate(cards) if i not in gone]
            photos = [p for i, p in enumerate(photos) if i not in gone]
            total = len(cards)

        slides = []
        archetypes = self.sequence_archetypes(cards)
        diagram = carousel_diagram(output_dir, cards, graphics)
        withheld: dict[str, Any] | None = None
        if diagram:
            # cycle 0 round 8: the drawing as the active brand and this code draw it, from the signed field; never a
            # stale stored poster (current_poster); no drawing that can be redrawn -> the slide keeps its photo
            drawn_poster, why = current_poster(output_dir, diagram[1], script_payload, room=self.diagram_room(),
                                               slide_width=self.CAROUSEL_SIZE[0])
            log.info("Carousel diagram slide %d: %s.", diagram[0] + 1, why)
            diagram = (diagram[0], drawn_poster) if drawn_poster is not None else None
        if diagram:
            # lane D cycle 1 (r8 AD R3): a drawing laid too small for the carousel's label floor is not the deck's
            # drawn slide; the slide keeps its photo. Judged on the two-line frame (the smaller room) of both copies,
            # so the Instagram and Facebook copies decide the same (one set, ruling c).
            ok, why_small = self.diagram_legible(diagram[1], ((False, 2), (True, 2)))
            if not ok:
                log.warning("Carousel diagram slide %d withheld: its labels would draw under the carousel label "
                            "floor (%s); the slide keeps its photo.", diagram[0] + 1, why_small)
                withheld = {"slide": diagram[0] + 1, "poster": str(diagram[1]), **why_small}
                retract_drawing_omissions(script_payload, diagram[1])   # SYS-A-c4-4: no slide shows that drawing
                diagram = None
        if diagram:
            archetypes = [("diagram" if i == diagram[0] else a) for i, a in enumerate(archetypes)]
        # Lane D cycle 4 (AD-P5-01, lander-verified: the Aswan scale slide drew the Statue of Liberty and no sentence on
        # the slide named it): a diagram slide whose copy does not name the comparator its drawing draws says it, after
        # its own signed body (diagram_comparator: the reference file's name, no figure); the drawing keeps its labels
        comparator: dict[str, Any] | None = None
        if diagram:
            comparator = diagram_comparator(cards[diagram[0]], diagram[1], script_payload)
            if comparator:
                comparator["slide"] = diagram[0] + 1
                cards = [dict(c, body=comparator["body"]) if i == diagram[0] else c for i, c in enumerate(cards)]
        # One unit per run: figures in the unit the narration speaks (2026-10-29), after the photo match; cycle 0
        # round 8: the signed scale fact's unit decides a length's lead for the whole deck (the diagram slide leads
        # in it, so every other slide states it first: figures.deck_lead).
        from .figures import deck_lead
        lead = deck_lead(script_payload)
        cards = [dict(card, **{k: spoken_units(card[k], narration, lead) for k in ("heading", "body")
                               if isinstance(card.get(k), str)}) for card in cards]
        # Lane B c3b: every slide's heading and body (the colossal stat's figure row is read from the body) and the
        # closing slide's poll question through the one pre-draw figure gate (visual_engine.gate_drawn), as drawn
        # Lane B c3c: a figure that lacks only its signed hedge is drawn with it ("RESTING PLACE OF ABOUT 7,000")
        # where the slide holds it at the deck's own size rules: the deck's heading tier and both copies' heading
        # frames unchanged (else every hedged slide takes the plain form), and the slide drawn without a problem or a
        # shortened field (else that slide takes the plain form); the plain form withholds the figure as before
        gate_hedged: list[dict[str, Any]] = []
        cards, poll, gate_rows = gate_deck(cards, poll, script_payload, gate_surface, hedged=gate_hedged)
        # Lane D cycle 3 (lander correction of ruling 4): the diagram slide keeps its OWN signed heading and body; the
        # drawing carries only its own labels (render_scale drawing_floor draws no title, figure or meaning), so the
        # deck has one title system and no slide's signed copy is replaced (cycle 2 set the drawing's scale copy
        # here and the Aswan deck lost "ANCIENT WORK MARKS" and its body).

        # SYS-D-5: the deck's one content-heading tier, over both copies (one size per deck, one form per heading)
        _render_state.tile_px = None
        if gate_hedged:
            plain_cards = plain_deck(cards, gate_hedged)
            if (self._deck_heading_tier(cards, archetypes) != self._deck_heading_tier(plain_cards, archetypes)
                    or any(self._deck_heading_frame(cards, archetypes, fb)
                           != self._deck_heading_frame(plain_cards, archetypes, fb) for fb in (False, True))):
                log.info("%s: the hedged headings change the deck's size decisions; drawn plain.", gate_surface)
                gate_rows += revert_hedges(cards, poll, gate_hedged, {h["slide"] for h in gate_hedged},
                                           script_payload, gate_surface)
        tier = self._deck_heading_tier(cards, archetypes)

        def copy_layout(fb: bool) -> tuple[int, dict[int, dict[str, Any]], list[int]]:
            """A copy's heading frame (one for every slide of the copy), its swipe strips and its drawing order."""
            _render_state.heading_tier = tier   # the frame is measured at the deck's tier
            frame_ = self._deck_heading_frame(cards, archetypes, fb)
            # Instagram's swipe strip (round 4): slide 2 is drawn first, so slide 1's strip meets slide 2's kicker rule.
            strips_: dict[int, dict[str, Any]] = {}
            order_ = list(range(total))
            if not fb and total > 2 and archetypes[0] == "cover" and archetypes[1] not in self.NON_CONTENT_ARCHETYPES:
                order_ = [1, 0] + order_[2:]
                strips_[1] = {"side": "left"}
            return frame_, strips_, order_

        layouts = {facebook: copy_layout(facebook)}
        frame, _strips, order = layouts[facebook]
        drawn_paths: dict[int, Path] = {}
        # the deck window rule (cycle 0 round 8): how many earlier slides show each slide's photograph
        shown = [None if archetypes[j] == "diagram" else Path(photos[j]) for j in range(total)]
        showing = [sum(1 for q in shown[:j] if q is not None and q == shown[j]) for j in range(total)]

        def draw_deck(kicker_step, fb: bool = facebook):
            """Every slide of the copy `fb` drawn (not saved) with the deck's kicker step (None: each kicker its own,
            surveyed)."""
            if fb not in layouts:
                layouts[fb] = copy_layout(fb)
            frame, strips, order = layouts[fb]
            done: dict[int, tuple] = {}
            strips_now = {k: dict(v) for k, v in strips.items()}
            for i in order:
                card = cards[i]
                archetype = archetypes[i]
                photo = diagram[1] if archetype == "diagram" else photos[i]
                photo_next = photos[(i + 1) % len(photos)]
                strip = strips_now.get(i)
                with kicker_set(kicker_step, survey=kicker_step is None):
                    im, lay, attempts = still_layout.drawn(
                        lambda tight, strip=strip, window=showing[i], card=card, archetype=archetype, photo=photo,
                        photo_next=photo_next, i=i: self._render_carousel_slide(
                            archetype, photo, photo_next, card, i, total, poll, share, fb, tight=tight,
                            subject=subject, heading_frame=frame, strip=strip, lead=lead, window=window,
                            heading_tier=tier))
                drew = lay.meta.get("swipe_strip") or {}
                if i == 1 and drew.get("box"):
                    strips_now[0] = {"side": "right", "y0": drew["box"][1], "y1": drew["box"][3]}
                done[i] = (im, lay, attempts, archetype, photo, photo_next, card, drew)
            return done

        # Cycle 0 round 8 (R1, one treatment per set): the deck's kickers are ONE set. The deck is drawn with each
        # kicker on its own step and every step its ground holds surveyed; when the kickers' steps differ, the deck
        # is drawn again with the least intrusive step that holds on every kicker's ground (deck_kicker_step),
        # colour kept wherever it holds for the whole deck. No such step: each keeps its own (gates never traded).
        # SYS-R8 integration (lander ruling 3): the Instagram and Facebook copies are the SAME slides, so they are
        # one set: the step is planned once over both copies' kickers (the first copy drawn surveys the other copy
        # too) and kept for the deck (deck_set_step), so the second copy draws on it without planning again.
        def plan_deck():
            key = deck_set_key(output_dir, cards, archetypes, photos, diagram, poll, share, subject, lead, showing)
            planned, step = deck_set_step(key)
            if planned:
                return draw_deck(step), step
            drawn = draw_deck(None)
            sibling = draw_deck(None, not facebook)
            step = deck_kicker_step([drawn[i][1] for i in order] + [entry[1] for entry in sibling.values()])
            keep_deck_set_step(key, step)
            if step is not None and not kickers_on([drawn[i][1] for i in order], step):
                drawn = draw_deck(step)
            return drawn, step

        drawn_deck, deck_step = plan_deck()
        # lane B c3c: a hedged slide drawn with a problem or a shortened field takes its plain form (the poll's question
        # with any such slide), and the deck is measured and drawn again from its copy as it now stands
        misfit = {h["slide"] for h in gate_hedged
                  if h["slide"] is not None and not _hedged_fits(drawn_deck[h["slide"] - 1][:2])}
        if misfit and any(h["slide"] is None for h in gate_hedged):
            misfit.add(None)
        if misfit:
            log.info("%s: hedged slide(s) %s do not fit; drawn plain.", gate_surface,
                     sorted(i for i in misfit if i is not None))
            gate_rows += revert_hedges(cards, poll, gate_hedged, misfit, script_payload, gate_surface)
            _render_state.tile_px = None
            tier = self._deck_heading_tier(cards, archetypes)
            layouts.clear()
            layouts[facebook] = copy_layout(facebook)
            frame, _strips, order = layouts[facebook]
            drawn_deck, deck_step = plan_deck()
        if comparator:
            j = comparator["slide"] - 1
            lay_j = drawn_deck[j][1]
            if lay_j.problems() or "body" in list(getattr(lay_j, "shortened", None) or []):
                # the comparator line does not fit the slide whole: the slide keeps its own body; the omission recorded
                tail = " " + comparator["line"]
                cards = [dict(c, body=c["body"][:-len(tail)] if c["body"].endswith(tail) else c["body"])
                         if i == j else c for i, c in enumerate(cards)]
                comparator = {**comparator, "drawn": False,
                              "reason": "the slide's body does not hold the comparator line whole"}
                record_comparator_omitted(script_payload, gate_surface, comparator)
                layouts.clear()
                layouts[facebook] = copy_layout(facebook)
                frame, _strips, order = layouts[facebook]
                drawn_deck, deck_step = plan_deck()
            else:
                comparator = {**comparator, "drawn": True}
        from .visual_engine import record_hedged
        record_hedged(script_payload, gate_hedged)
        for i in order:
            im, lay, attempts, archetype, photo, photo_next, card, drew = drawn_deck[i]
            if isinstance(getattr(lay, "meta", None), dict):
                lay.meta["kicker_set"] = {"step": list(deck_step) if deck_step else None, "slides": total}
                held = slide_withheld(gate_rows, gate_surface, i + 1)
                if held:   # lane B c3b: the figures the gate withheld from this slide, on its own record
                    lay.meta["figure_withheld"] = held
                hedges = slide_hedged(gate_hedged, gate_surface, i + 1)
                if hedges:   # lane B c3c: the figures drawn with their signed hedge, on the slide's own record
                    lay.meta["figure_hedged"] = hedges
            path = out_dir / f"carousel_slide_{i + 1:02d}.jpg"
            record_masthead_withheld(script_payload, gate_surface, i + 1, lay)   # freeze c5: never an unrecorded drop
            self._save_slide(im, path)
            _delivered_contrast(lay, path)
            still_layout.save(path, lay, {"renderer": "multi_format.redraw_carousel_slide", "archetype": archetype,
                                          "photo": str(photo), "photo_next": str(photo_next), "card": card, "index": i,
                                          "total": total, "poll": poll, "share": share, "facebook": facebook,
                                          "subject": subject, "heading_frame": frame, "heading_tier": tier,
                                          "lead": lead,
                                          "window": showing[i],
                                          **({"kicker_step": list(deck_step)} if deck_step else {}),
                                          **({"diagram_withheld": withheld}
                                             if withheld and i == withheld["slide"] - 1 else {}),
                                          **({"figure_withheld": lay.meta["figure_withheld"]}
                                             if lay.meta.get("figure_withheld") else {}),
                                          **({"figure_hedged": lay.meta["figure_hedged"]}
                                             if lay.meta.get("figure_hedged") else {}),
                                          **({"deck_dropped": dropped_rows} if dropped_rows else {}),
                                          # freeze cycle 5: each photo window's crop and its fit (an unfit window is drawn as
                                          # asked and RECORDED here with its problems: src/photo_fitness.py)
                                          **({"windows": lay.meta["windows"]} if lay.meta.get("windows") else {}),
                                          **({"comparator": comparator} if comparator and i == comparator["slide"] - 1
                                             else {}),
                                          **({"strip": drew} if drew else {})}, attempts)
            drawn_paths[i] = path
        slides = [drawn_paths[i] for i in range(total)]
        prune_stale_slides(out_dir, total)   # lane D cycle 4: a shorter deck leaves no older slide behind

        if facebook:
            # lane D cycle 3 (lander ruling 2 of the second review): in the 5-photo collage the cover and the second
            # photo are the two equal top tiles; a headline second tile steps its heading down the ladder until the
            # cover's heading is COLLAGE_COVER_OVER_TILE x it (the first such step), on this copy only
            step = collage_tile_step([str(x) for x in slides])
            if step is not None:
                j, size = step
                card, archetype = cards[j], archetypes[j]
                photo = diagram[1] if archetype == "diagram" else photos[j]
                photo_next = photos[(j + 1) % len(photos)]
                with kicker_set(deck_step, survey=deck_step is None):
                    im, lay, attempts = still_layout.drawn(
                        lambda tight: self._render_carousel_slide(
                            archetype, photo, photo_next, card, j, total, poll, share, True, tight=tight,
                            subject=subject, heading_frame=frame, lead=lead, window=showing[j], heading_tier=tier,
                            heading_px=size))
                _render_state.tile_px = None   # never left set for the next deck's measuring
                if not lay.problems():
                    path = drawn_paths[j]
                    if isinstance(getattr(lay, "meta", None), dict):
                        lay.meta["kicker_set"] = {"step": list(deck_step) if deck_step else None, "slides": total}
                        held = slide_withheld(gate_rows, gate_surface, j + 1)
                        if held:
                            lay.meta["figure_withheld"] = held
                        hedges = slide_hedged(gate_hedged, gate_surface, j + 1)
                        if hedges:
                            lay.meta["figure_hedged"] = hedges
                    record_masthead_withheld(script_payload, gate_surface, j + 1, lay)
                    self._save_slide(im, path)
                    _delivered_contrast(lay, path)
                    still_layout.save(path, lay, {"renderer": "multi_format.redraw_carousel_slide",
                                                  "archetype": archetype, "photo": str(photo),
                                                  "photo_next": str(photo_next), "card": card, "index": j,
                                                  "total": total, "poll": poll, "share": share, "facebook": True,
                                                  "subject": subject, "heading_frame": frame, "heading_tier": tier,
                                                  "lead": lead, "window": showing[j], "heading_px": size,
                                                  "kicker_line_exempt": lay.meta.get("kicker_line_exempt"),
                                                  **({"deck_dropped": dropped_rows} if dropped_rows else {}),
                                                  # freeze cycle 5: each photo window's crop and its fit (an unfit window is drawn as
                                                  # asked and RECORDED here with its problems: src/photo_fitness.py)
                                                  **({"windows": lay.meta["windows"]} if lay.meta.get("windows") else {}),
                                                  **({"comparator": comparator}
                                                     if comparator and j == comparator["slide"] - 1 else {}),
                                                  **({"figure_withheld": lay.meta["figure_withheld"]}
                                                     if lay.meta.get("figure_withheld") else {}),
                                                  **({"figure_hedged": lay.meta["figure_hedged"]}
                                                     if lay.meta.get("figure_hedged") else {}),
                                                  **({"kicker_step": list(deck_step)} if deck_step else {})},
                                      attempts)
                else:
                    log.warning("Facebook collage: tile 2 (slide %d) at %s px drew with problems %s; kept at the "
                                "deck's tier.", j + 1, size, lay.problems())
            write_collage_report(slides, out_dir)
        log.info("Generated %d multi-template carousel slides -> %s", len(slides), out_dir)
        return slides


_CONTRAST = re.compile(r"\b(?:versus|vs\.?|compared|unlike|contrast|above and below|then and now|modern)\b")
_SPAN_UNIT = (r"(?:(?:million|billion|thousand)\s+)?(?:years?|feet|ft|metres?|meters?|m|miles?|km|kilometres?|"
              r"kilometers?|tons?|tonnes|people|levels|storeys|stories|centuries)(?:\s+(?:ago|old|deep|tall|high|wide|bce?))?")
_SPAN = re.compile(r"(?<![\w.])(?:between\s+(?P<a>\d[\d,]*(?:\.\d+)?)\s+and\s+|(?P<a2>\d[\d,]*(?:\.\d+)?)\s*"
                   r"(?:-|\u2013|\u2014|to)\s*)(?P<b>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>" + _SPAN_UNIT + r")\b",
                   re.IGNORECASE)


def range_figure(text: str) -> tuple[str, str] | None:
    """A stated span and its unit, whole: ("94\u2013104", "MILLION YEARS AGO") for "between 94 and 104 million years
    ago" or "94-104 million years ago", en dash and no spaces (round 3, 2026-11-02: the stat slide took the first
    number, "94", which is more precise than the sheet). None when the text states no span with a unit. A span whose
    ends are not in order ("1 to 3" is a span; "2025 to 12" is not) is no span."""
    for m in _SPAN.finditer(text or ""):
        lo = m.group("a") or m.group("a2")
        a, b = (float(x.replace(",", "")) for x in (lo, m.group("b")))
        if a < b:
            return f"{lo}\u2013{m.group('b')}", " ".join(m.group("unit").upper().split())
    return None


def figure_colour() -> tuple[int, int, int]:
    """A stated quantity's colour (the figure.colour token: GOLD by default; ruling 3, ruling 8)."""
    return colour_of("figure.colour")


def unit_runs(line: str, gold_words, offset: int = 0) -> list[tuple[str, bool]]:
    """A unit line's runs [(text, gold)]: its first `gold_words` words (counted from `offset`, the words of the
    unit set on earlier lines) are the unit, gold; the rest (the dimension word: TALL, DEEP, AGO) bone (ruling 8).
    `gold_words` may be the set of the unit's gold word indices (figures.gold_word_indices)."""
    out: list[tuple[str, bool]] = []
    for i, w in enumerate(line.split()):
        g = (offset + i in gold_words) if isinstance(gold_words, (set, frozenset)) else offset + i < gold_words
        if out and out[-1][1] == g:
            out[-1] = (out[-1][0] + " " + w, g)
        else:
            out.append((w, g))
    return out


CAROUSEL_PHOTOS = "carousel_photos.json"
CAROUSEL_DIAGRAM = "carousel_diagram.json"
# The graphics that explain something drawn (graphics.make_all's file names), in order of preference; the number
# cards, the timeline and the map draw over a photograph and are not re-laid as a diagram slide.
DIAGRAM_KINDS = ("section", "ventilation", "defense", "scale", "route")
_DIAGRAM_WORDS = {
    "section": ("deep", "depth", "level", "levels", "storey", "storeys", "carved", "below", "down", "feet", "metres"),
    "ventilation": ("air", "shaft", "shafts", "ventilation", "breathe", "fresh"),
    "defense": ("door", "doors", "stone", "rolling", "roll", "seal", "sealed", "wheel"),
    # lane D cycle 3: a length is a scale's dimension too (the Aswan drawing compares a LENGTH; with no length word
    # it went to slide 3 by the tie-break, a slide about tool marks, now that the slide keeps its own words)
    "scale": ("tall", "high", "height", "deep", "depth", "wide", "width", "size", "long", "length", "scale"),
    "route": ("route", "road", "journey", "path", "trade"),
}
DIAGRAM_SLIDES = (2, 3)   # slide 3 or 4 (0-based): after the hook and its first fact
# Round 4 (2026-11-02): with no explanatory graphic, the locator map's resting frame (graphic_map.jpg: relief, globe
# inset, place label) is the deck's drawn slide, so every carousel has one. It has no flat ground to crop to: it is
# laid full bleed (_render_diagram), its pin and label in the slide's upper part (MAP_FOCUS), the text over its foot.
MAP_KIND = "map"
MAP_FOCUS = (0.5, 0.36)   # this share of the poster's height (its centre: the map's pin) lands at this share of the slide's
_DIAGRAM_WORDS[MAP_KIND] = ("where", "desert", "located", "location", "country", "region", "map", "landmark",
                            "space", "orbit", "visible", "border", "coast", "near", "across")


MAP_FOOT_INK = 0.94   # the ink foot under the map slide's stack: the map's lines and labels there all but gone
MAP_CLEAR = 40        # design px from the foot's full ink to the stack's top (the kicker's cap)
MAP_FADE, MAP_FADE_GOOD, MAP_FADE_MIN = 160, 110, 56   # design px over which the foot rises to its ink: at most,
                                                       # preferred at least, and at least
MAP_GAP = 12          # design px from the place label and pin to where the foot starts
MAP_PIN = 30          # design px about the poster's centre the pin (22 px, its ink outline) takes
MAP_LABEL_REACH = 380  # design px above the pin the place label can stand (graphics.render_map: 72 px plus it)
MAP_LIFT_MAX = 160    # design px the map may rise above MAP_FOCUS to clear its labels
MAP_LABEL_INK = 200   # a pixel at least this bright in every channel is label ink (bone type, the scale bar)


def map_label_bands(poster, full: Image.Image, k: float, px) -> list[tuple[float, float, float, float]]:
    """The locator map's own labels as horizontal bands (x0, y0, x1, y1) in `full`'s pixels (the poster scaled by
    `k`): the label boxes graphics.render_map recorded beside its clip (<clip>.contrast.json: the place, the
    country, the scale bar's figures, a neighbour's name), each padded to take in its halo and the bar under it,
    and the rows of `full` that carry label ink (MAP_LABEL_INK: bone type and the bar's white line), for a poster
    with no record. Overlapping bands are merged."""
    import numpy as np
    boxes = []
    side = Path(str(Path(poster).with_suffix(".mp4")) + ".contrast.json") if poster else None
    try:
        record = json.loads(side.read_text(encoding="utf-8")) if side and side.is_file() else {}
    except (OSError, ValueError):
        record = {}
    pad = px(14)
    for e in record.get("elements") or []:
        b = e.get("box") or []
        if len(b) == 4:
            boxes.append((b[0] * k - pad, b[1] * k - pad, b[2] * k + pad, b[3] * k + pad))
    a = np.asarray(full.convert("RGB"), dtype=np.int16)
    ink = a.min(axis=2) >= MAP_LABEL_INK
    rows = np.nonzero(ink.sum(axis=1) >= 3)[0]
    if len(rows):
        gap = max(1, px(10))
        start = prev = int(rows[0])
        for r in [int(v) for v in rows[1:]] + [None]:
            if r is not None and r - prev <= gap:
                prev = r
                continue
            xs = np.nonzero(ink[start:prev + 1].any(axis=0))[0]
            boxes.append((float(xs.min()) - px(6), start - px(6), float(xs.max()) + px(6), prev + 1 + px(6)))
            if r is not None:
                start = prev = r
    boxes.sort(key=lambda b: b[1])
    merged: list[list[float]] = []
    for b in boxes:
        if merged and b[1] <= merged[-1][3]:
            m = merged[-1]
            merged[-1] = [min(m[0], b[0]), m[1], max(m[2], b[2]), max(m[3], b[3])]
        else:
            merged.append(list(b))
    return [tuple(m) for m in merged]


def is_map_poster(poster) -> bool:
    return Path(str(poster)).stem == f"graphic_{MAP_KIND}"


def diagram_poster(output_dir: Path, graphics: list[Path] | None = None) -> tuple[str, Path] | None:
    """(kind, poster) of the run's preferred explanatory graphic whose poster frame exists, else the locator map's
    (MAP_KIND), else None."""
    if graphics is None:
        folder = Path(output_dir) / "graphics"
        graphics = sorted(folder.glob("graphic_*.jpg")) if folder.is_dir() else []
    posters = {}
    for g in graphics or []:
        poster = Path(g).with_suffix(".jpg")
        kind = poster.stem.replace("graphic_", "", 1)
        if kind in (*DIAGRAM_KINDS, MAP_KIND) and poster.is_file():
            posters.setdefault(kind, poster)
    kind = next((k for k in DIAGRAM_KINDS + (MAP_KIND,) if k in posters), None)   # the map only as the fallback
    return (kind, posters[kind]) if kind else None


def carousel_diagram(output_dir: Path, cards: list[dict[str, Any]],
                     graphics: list[Path] | None = None) -> tuple[int, Path] | None:
    """(slide index, poster) for the carousel's one diagram slide, or None: the run's explanatory graphic on
    whichever of DIAGRAM_SLIDES says most about what it draws (else the first). The locator map (MAP_KIND, the
    fallback drawing) goes only on a slide whose words are about place (_DIAGRAM_WORDS["map"]: "visible from
    space ... desert landmark"), on any content slide (slide 2 included: the place is often said first), the one
    that says most, DIAGRAM_SLIDES first on a tie; with no such slide the deck has no drawn slide (round 4 review,
    2026-11-02: the map replaced the photo of a myth-bust about the crater theory). Decided once per run and kept
    in CAROUSEL_DIAGRAM, so the Instagram and Facebook copies, drawn at once, show it on the same slide."""
    output_dir = Path(output_dir)
    record = output_dir / CAROUSEL_DIAGRAM
    key = [f"{c.get('heading', '')}|{c.get('body', '')}" for c in cards]
    with _photo_lock(output_dir):
        try:
            kept = json.loads(record.read_text(encoding="utf-8")) if record.is_file() else {}
        except (OSError, ValueError):
            kept = {}
        if kept.get("key") == key and (not kept.get("poster") or Path(kept["poster"]).is_file()):
            return (int(kept["index"]), Path(kept["poster"])) if kept.get("poster") else None
        found = diagram_poster(output_dir, graphics)
        slots = [i for i in DIAGRAM_SLIDES if 0 < i < len(cards) - 1]
        chosen = None
        if found and slots:
            kind, poster = found
            words = set(_DIAGRAM_WORDS.get(kind, ()))

            def says(i):
                text = re.findall(r"[a-z]+", f"{cards[i].get('heading', '')} {cards[i].get('body', '')}".lower())
                return sum(w in words for w in text)
            if kind == MAP_KIND:
                slots = [i for i in range(1, len(cards) - 1) if says(i) > 0]
                index = max(slots, key=lambda i: (says(i), i in DIAGRAM_SLIDES, -i)) if slots else None
            else:
                index = max(slots, key=lambda i: (says(i), -i))
            chosen = (index, poster) if index is not None else None
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            record.write_text(json.dumps({"key": key, "index": chosen[0] if chosen else None,
                                          "poster": str(chosen[1]) if chosen else ""}, indent=1, ensure_ascii=False),
                              encoding="utf-8", newline="\n")
        except OSError as e:
            log.warning("Carousel diagram choice not recorded (%s).", e)
        return chosen


COMPARATOR_RULE = "carousel.diagram_comparator"


def diagram_comparator(card: dict[str, Any], poster: Path, script_payload: dict[str, Any]) -> dict[str, Any] | None:
    """Lane D cycle 4 (AD-P5-01): the sentence a scale diagram slide adds after its own body when neither its heading
    nor its body names the landmark the drawing sets the subject against ({"body", "line", "landmark", "from"}), or
    None (not a scale drawing, no sourced landmark, or the copy names it). The landmark is the one render_scale draws
    (graphics.pick_reference on the signed scale_fact) in its sentence form (the line of meaning's), and the line
    states no figure: the drawing's own labels carry the figures (the 9:16 graphic's line of meaning is not drawn on
    the slide). Generic: any subject, any landmark of the reference file."""
    if Path(poster).stem.replace("graphic_", "", 1) != "scale":
        return None
    from . import graphics
    fact = script_payload.get("scale_fact") or {}
    try:
        feet = float(fact["value"]) * (3.28084 if fact.get("unit") == "m" else 1.0)
    except (KeyError, TypeError, ValueError):
        return None
    ref = graphics.pick_reference(feet) if feet > 0 else None
    if not ref:
        return None
    name = graphics._landmark_name(ref[0])
    said = set(re.findall(r"[a-z]+", f"{card.get('heading', '')} {card.get('body', '')}".lower()))
    core = [w for w in re.findall(r"[a-z]+", name.lower()) if w not in ("the", "of", "a", "an")]
    if core and all(w in said for w in core):
        return None
    line = f"Drawn to scale beside {name}."
    body = str(card.get("body") or "").rstrip()
    return {"body": f"{body} {line}".strip(), "line": line, "landmark": name, "from": body}


def record_comparator_omitted(script_payload: dict[str, Any], surface: str, comparator: dict[str, Any]) -> None:
    """The comparator line left off its diagram slide (it did not fit whole) on the run's ledger, as a drawn-without
    row (payload["layout_holds"]) and on the open omissions ledger when one is open."""
    from . import refusal
    name = f"{surface} slide {comparator['slide']}"
    reason = (f"drawn without {comparator['line']!r}: {comparator.get('reason', '')}; the drawing still sets the "
              f"subject against {comparator['landmark']} (rule: {COMPARATOR_RULE})")
    holds = script_payload.setdefault("layout_holds", [])
    if not any(h.get("surface") == name and h.get("omitted") == comparator["line"] for h in holds if isinstance(h, dict)):
        holds.append({"surface": name, "omitted": comparator["line"], "rule": COMPARATOR_RULE, "drawn": True,
                      "reason": reason})
        log.warning("%s: %s", name, reason)
    with contextlib.suppress(refusal.Declined):
        refusal.omit(name, comparator["line"], COMPARATOR_RULE, reason)


MASTHEAD_RULE = "a masthead under its gate is not drawn (contrast.masthead_gate, src/masthead.py)"


def record_masthead_withheld(script_payload: dict[str, Any] | None, surface: str, slide: int, lay) -> dict | None:
    """A slide delivered WITHOUT its masthead because the gate withheld it (freeze cycle 5, stream PLATES; panel 6
    AD r2 / BC r2: on the second palettes a bright interior slide lost its masthead and its scrim, and nothing but
    the slide's in-memory layout said so) on the run's ledger, as a drawn-without row (payload["layout_holds"]: the
    review card and the release gate name it) and on the open omissions ledger when one is open (the evidence
    board's surfaces.json). A masthead a rule leaves off (a Facebook interior slide) is no omission: its own record
    says so. Returns the row, None when the masthead was drawn or left off by rule."""
    from . import refusal
    m = (getattr(lay, "meta", None) or {}).get("masthead") or {}
    if not m or m.get("shown") or m.get("fb_interior") or m.get("by_rule"):
        return None
    name, omitted = f"{surface} slide {slide}", "the masthead"
    why = str(m.get("reason") or "its record gives no reason")
    row = {"surface": name, "omitted": omitted, "rule": MASTHEAD_RULE, "drawn": True, "slide": slide,
           "reason": f"drawn without {omitted!r}: {why} (rule: {MASTHEAD_RULE})"}
    if script_payload is not None:
        holds = script_payload.setdefault("layout_holds", [])
        if not any(h.get("surface") == name and h.get("omitted") == omitted for h in holds if isinstance(h, dict)):
            holds.append(row)
            log.warning("%s: %s", name, row["reason"])
    with contextlib.suppress(refusal.Declined):   # no ledger open: the run's own ledger above holds the record
        refusal.omit(name, omitted, MASTHEAD_RULE, why)
    return row


REDRAWN_DIR = "graphics_redrawn"   # <run>/graphics_redrawn/<brand+code key>/graphic_<kind>.jpg (cycle 0 round 8)
# The signed field each diagram kind draws (graphics.make_all), and its renderer's name.
_DIAGRAM_FIELDS = {"scale": ("scale_fact", "render_scale"), "section": ("cross_section", "render_cross_section"),
                   "route": ("route", "render_route"), MAP_KIND: (None, "render_map")}


DRAWING_SIDECAR = ".drawing.json"   # beside a slide drawing's poster: {"drawing_floor", "size"}
# SYS-A-c4-4 (freeze integration 4): beside every redrawn diagram poster, the omission ledger rows its drawing recorded
# ({"rows": [refusal.omit rows]}; [] when it left nothing out), so a cache hit records them again
OMISSIONS_SIDECAR = ".omissions.json"
DIAGRAM_SLIDE_SURFACE = "carousel diagram slide"


def drawing_omissions(poster: Path) -> list[dict[str, Any]] | None:
    """The omission ledger rows recorded when the redrawn diagram `poster` was drawn (OMISSIONS_SIDECAR), or None when
    it carries no record (drawn before the record existed, or unreadable): such a poster is drawn again."""
    side = Path(str(poster) + OMISSIONS_SIDECAR)
    try:
        rec = json.loads(side.read_text(encoding="utf-8")) if side.is_file() else None
    except (OSError, ValueError):
        return None
    rows = rec.get("rows") if isinstance(rec, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else None


def record_drawing_omissions(payload: dict[str, Any] | None, rows: list[dict[str, Any]],
                             outer: list | None = None, poster: Path | None = None) -> list[dict[str, Any]]:
    """The parts a redrawn diagram slide's drawing left out (its renderer's refusal.omit rows) handed on as make_all
    hands the graphics' rows on: payload["layout_holds"] drawn-without rows (refusal.hold_rows), named as the carousel
    diagram slide's drawing ("carousel diagram slide (scale graphic_scale.mp4)") so the review card never reads them
    as the video graphic's; on `outer` (the omissions ledger open round the caller, when one is) too. One row per
    surface and part (the deck's two copies draw it once and reuse it). Each row names the `poster` it belongs to
    ("drawing"), so a drawing the deck then withholds takes its rows back (retract_drawing_omissions). Returns the
    rows as recorded."""
    from . import refusal
    out = []
    for r in refusal.hold_rows(rows):
        row = dict(r)
        if not str(row["surface"]).startswith(DIAGRAM_SLIDE_SURFACE):
            row["surface"] = f"{DIAGRAM_SLIDE_SURFACE} ({row['surface']})"
        if poster is not None:
            row["drawing"] = str(poster)
        out.append(row)
    ledgers = [payload.setdefault("layout_holds", [])] if payload is not None and out else []
    for ledger in ledgers + ([outer] if outer is not None else []):
        seen = {(h.get("surface"), h.get("omitted")) for h in ledger if isinstance(h, dict)}
        for row in out:
            if (row["surface"], row.get("omitted")) not in seen:
                seen.add((row["surface"], row.get("omitted")))
                ledger.append(dict(row))
    return out


def retract_drawing_omissions(payload: dict[str, Any] | None, poster: Path | str) -> int:
    """The rows record_drawing_omissions recorded for `poster` taken off payload["layout_holds"] when the deck does not
    show that drawing after all (diagram_legible withholds it; the slide keeps its photo): the card never names a part
    left out of a drawing no slide shows. Returns how many were taken off."""
    holds = (payload or {}).get("layout_holds")
    if not isinstance(holds, list):
        return 0
    keep = [h for h in holds if not (isinstance(h, dict) and h.get("drawing") == str(poster)
                                     and str(h.get("surface", "")).startswith(DIAGRAM_SLIDE_SURFACE))]
    gone = len(holds) - len(keep)
    holds[:] = keep
    return gone


def slide_drawing(poster: Path) -> dict[str, Any] | None:
    """The record of a drawing drawn for the slide's own room (render_scale drawing_floor; lane D cycle 2), or None
    for a video poster."""
    side = Path(str(poster) + DRAWING_SIDECAR)
    try:
        rec = json.loads(side.read_text(encoding="utf-8")) if side.is_file() else None
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) and rec.get("drawing_floor") else None


def current_poster(output_dir: Path, poster: Path, payload: dict[str, Any] | None,
                   room: tuple[int, int] | None = None, slide_width: int | None = None) -> tuple[Path | None, str]:
    """(the poster a diagram slide embeds, why): the run's stored poster only when it was drawn under the active brand
    by this drawing code (graphics.poster_current: its stamp); else the drawing redrawn from the run's SIGNED field
    under the active brand (poster only, no clip encoded) into REDRAWN_DIR keyed by brand + code, and reused while
    that key holds; (None, reason) when it cannot be redrawn (no signed field, no recorded place for a map): the slide
    then keeps its photo layout. Cycle 0 round 8 (consensus 2): the Aswan diagram slide embedded a stale bar chart
    from older code with a #FED700 bar, unchanged under the second palette.
    `room` (lane D cycle 2, lander ruling 2, with lane A's render_scale drawing_floor): a scale is drawn as the
    DRAWING alone for the slide's room (w, h), every label at graphics.diagram_label_floor(slide_width) or more,
    never a video poster shrunk into the slide; its record (slide_drawing) sits beside it. The slide sets its own
    signed heading and body (lane D cycle 3); the drawing says nothing but its own labels."""
    from . import graphics
    poster = Path(poster)
    kind0 = poster.stem.replace("graphic_", "", 1)
    drawn_for_slide = room is not None and kind0 == "scale"
    if not drawn_for_slide and graphics.poster_current(poster):
        return poster, "stored poster drawn under the active brand by this code"
    kind = poster.stem.replace("graphic_", "", 1)
    field, renderer = _DIAGRAM_FIELDS.get(kind, (None, None))
    if renderer is None:
        return None, f"no renderer redraws a {kind} diagram"
    narration = (payload or {}).get("narration_script") or ""
    if kind == MAP_KIND:
        spec = _recorded_place(poster)
        if not spec:
            return None, "the map's place is not recorded beside its poster: it cannot be redrawn"
    else:
        spec = (payload or {}).get(field)
        if not spec:
            return None, f"the run has no signed {field} to redraw its {kind} diagram from"
    size = (VIDEO_WIDTH, VIDEO_HEIGHT)
    try:
        with Image.open(poster) as im:
            size = im.size
    except OSError:
        pass
    floor = None
    if drawn_for_slide:
        size = (int(room[0]), int(room[1]))
        floor = graphics.diagram_label_floor(int(slide_width or MultiFormatGenerator.CAROUSEL_SIZE[0]))
    import hashlib
    stamp = graphics.stamp()
    # the cache key: the brand and its tokens, the drawing code, and what is drawn (the signed field, its size)
    what = hashlib.sha256(json.dumps([spec, list(size), narration if kind == MAP_KIND else ""]
                                     + ([floor] if floor is not None else []), sort_keys=True,
                                     default=str).encode("utf-8")).hexdigest()
    key = stamp["brand"][:12] + stamp["code"][:12] + what[:12]
    out = Path(output_dir) / REDRAWN_DIR / key / f"graphic_{kind}.mp4"
    from . import refusal
    outer = refusal._LEDGER.get()   # the ledger open round this caller (the evidence board), if any
    cached = drawing_omissions(out.with_suffix(".jpg"))
    if out.with_suffix(".jpg").is_file() and graphics.poster_current(out.with_suffix(".jpg")) and (
            floor is None or slide_drawing(out.with_suffix(".jpg"))) and cached is not None:
        # SYS-A-c4-4: the parts the cached drawing left out are recorded again, never dropped on a cache hit
        record_drawing_omissions(payload, cached, outer, out.with_suffix(".jpg"))
        return out.with_suffix(".jpg"), "redrawn under the active brand (cached)"
    # SYS-A-c4-4 (freeze integration 4): the redraw runs with the omission ledger open, as graphics.make_all runs the
    # graphics: a part the renderer leaves out (refusal.omit: a scale's refused referent note, a map's scale bar or
    # name, a route stop) is recorded and the slide keeps its drawing; with no ledger open the renderer is refused
    # (Declined) and the slide fell back to its photo with the omission on no card
    with contextlib.suppress(OSError):   # a record from an earlier drawing is never read as this one's
        Path(str(out.with_suffix(".jpg")) + OMISSIONS_SIDECAR).unlink(missing_ok=True)
    try:
        with refusal.collecting() as omitted, graphics.poster_only():
            if kind == MAP_KIND:
                made = graphics.render_map(spec, out, width=size[0], height=size[1], narration=narration)
            elif floor is not None:
                made = graphics.render_scale(spec, out, width=size[0], height=size[1], drawing_floor=floor)
            else:
                made = getattr(graphics, renderer)(spec, out, width=size[0], height=size[1])
    except Exception as e:  # noqa: BLE001  a drawing that cannot be redrawn is not shown (the photo slide stays)
        log.warning("Diagram slide: the %s diagram could not be redrawn (%s).", kind, e)
        return None, f"the {kind} diagram could not be redrawn ({type(e).__name__})"
    if not made or not out.with_suffix(".jpg").is_file():
        return None, f"the {kind} renderer drew nothing from the signed field"
    rows = refusal.hold_rows(omitted)
    try:
        Path(str(out.with_suffix(".jpg")) + OMISSIONS_SIDECAR).write_text(
            json.dumps({"rows": rows}, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
    except OSError as e:   # the drawing is used; its rows are recorded below (a later call draws it again)
        log.warning("Diagram slide: the %s drawing's omissions not stored beside it (%s).", kind, e)
    record_drawing_omissions(payload, rows, outer, out.with_suffix(".jpg"))
    if floor is not None:
        Path(str(out.with_suffix(".jpg")) + DRAWING_SIDECAR).write_text(
            json.dumps({"drawing_floor": floor, "size": list(size)}, indent=1, ensure_ascii=False),
            encoding="utf-8", newline="\n")
        return out.with_suffix(".jpg"), "drawn for the slide's room from the signed field under the active brand"
    return out.with_suffix(".jpg"), "redrawn from the signed field under the active brand"


def _recorded_place(poster: Path) -> dict[str, Any] | None:
    """The place a stored map poster was drawn for (its lineage record's inputs), or None."""
    side = Path(str(Path(poster).with_suffix(".mp4")) + ".lineage.json")
    try:
        data = json.loads(side.read_text(encoding="utf-8")) if side.is_file() else {}
    except (OSError, ValueError):
        return None
    place = ((data.get("settings") or {}).get("inputs") or {}).get("place")
    return place if isinstance(place, dict) and place.get("lat") is not None else None


def diagram_crop(poster: Image.Image) -> tuple[tuple[int, int, int], tuple[int, int, int, int]]:
    """(the poster's ground colour, the box of what it draws, padded): the ground is the median of its border."""
    import numpy as np
    a = np.asarray(poster.convert("RGB"), dtype=np.int16)
    border = np.concatenate([a[:8].reshape(-1, 3), a[-8:].reshape(-1, 3), a[:, :8].reshape(-1, 3), a[:, -8:].reshape(-1, 3)])
    ground = tuple(int(v) for v in np.median(border, axis=0))
    # Drawn: clearly off the ground (a soft backdrop gradient is not), in a row or column with a few such pixels.
    ink = np.abs(a - np.array(ground, dtype=np.int16)).max(axis=2) > 40
    ys, xs = np.nonzero(ink.sum(axis=1) >= 4)[0], np.nonzero(ink.sum(axis=0) >= 4)[0]
    if not len(xs) or not len(ys):
        return ground, (0, 0, poster.width, poster.height)
    pad = 28
    return ground, (max(0, int(xs.min()) - pad), max(0, int(ys.min()) - pad),
                    min(poster.width, int(xs.max()) + 1 + pad), min(poster.height, int(ys.max()) + 1 + pad))
_photo_locks: dict[str, threading.Lock] = {}
_photo_locks_guard = threading.Lock()


def _photo_lock(output_dir: Path) -> threading.Lock:
    with _photo_locks_guard:
        return _photo_locks.setdefault(str(Path(output_dir).resolve()), threading.Lock())


def _recorded_photos(output_dir: Path, cards: list[dict[str, Any]]) -> list[Path]:
    """The photos a copy drawn before carousel_photos.json existed recorded for these cards ([] if none)."""
    from . import still_layout
    for copy in ("carousel", "carousel_fb"):
        folder = Path(output_dir) / copy
        recs = [still_layout.load(folder / f"carousel_slide_{i + 1:02d}.jpg") or {} for i in range(len(cards))]
        recipes = [r.get("recipe") or {} for r in recs]
        photos = [Path(r.get("photo") or "") for r in recipes]
        same = all((r.get("card") or {}).get("heading") == c.get("heading") and (r.get("card") or {}).get("body") == c.get("body")
                   for r, c in zip(recipes, cards))
        if same and all(str(x) not in ("", ".") and x.is_file() for x in photos):
            return photos
    return []


# Lane D cycle 4 (CD r2, carousel photo matching): the deck's photo choice is held to three rules after the match, on
# every path (a fresh match, a kept one, an older run's recorded one), and each slide's reason is kept in
# carousel_photos.json ("choice"):
# - no repeat: a photograph (picture_id: a crop or an upscale is its original) is shown on one slide only while the pool
#   has one unused (scene_planner.no_repeats, the Reel's rule); with fewer photos than slides a repeat stays, said so;
# - presence: a slide whose line is not about the subject today (PRESENT_WORDS: its visitors, its present state) never
#   shows a photo whose review saw visitors or modern fittings in it (vision_review.people_prominent, or MODERN_WORDS in
#   what the review saw: they stand between the reader and the subject the line is about, and on a line about the past
#   they contradict it) while the pool has an unused photo the review did not flag; the replacement is the unused photo
#   whose archive caption shares the most distinctive words with the slide (scene_planner.caption_order's measure),
#   pool order on a tie;
# - otherwise the match stands ("matched").
# Generic: words of the present and of modern presence, never a topic or a photo list.
PRESENT_WORDS = re.compile(
    r"\b(?:today|nowadays|now|currently|still|modern|visitors?|tourists?|tourism|crowds?|people|travell?ers|museum|"
    r"visit|visited|visiting|this year|recently)\b", re.IGNORECASE)
MODERN_WORDS = re.compile(
    r"\b(?:visitors?|tourists?|crowds?|sightseers|pedestrians|modern|scaffold\w*|vehicles?|cars|parking|signage|"
    r"souvenir\w*)\b", re.IGNORECASE)   # never fabric a site of any age has (a walkway, stairs, a railing)
PEOPLE_WORDS = re.compile(r"visitors?|tourists?|crowds?|sightseers|pedestrians", re.IGNORECASE)
PHOTO_CHOICE_RULE = "carousel.photo_choice"


def present_line(card: dict[str, Any]) -> bool:
    """Whether a slide's line speaks of the subject today (PRESENT_WORDS): its photo may then show its visitors."""
    return bool(PRESENT_WORDS.search(f"{card.get('heading', '')} {card.get('body', '')}"))


def modern_presence(photo: Path) -> str:
    """What the photo's review saw that is modern ("" when nothing): visitors (people_prominent) or MODERN_WORDS in its
    reason."""
    from .scene_planner import _photo_entry
    entry = _photo_entry(Path(photo)) or {}
    review = entry.get("vision_review") or {}
    if review.get("people_prominent"):
        return "prominent visitors"
    for m in MODERN_WORDS.finditer(str(review.get("reason") or "")):
        word = m.group(0).lower()
        if review.get("people_prominent") is False and PEOPLE_WORDS.fullmatch(word):
            continue   # the review judged the people in it not prominent
        return word
    return ""


def deck_photo_guard(cards: list[dict[str, Any]], chosen: list[Path], stills: list[Path]
                     ) -> tuple[list[Path], list[dict[str, Any]]]:
    """(photos, choice): `chosen` held to the deck's photo rules above, and each slide's reason ({"slide", "photo",
    "reason"}, with "replaced" naming the photo the rule took out)."""
    from collections import Counter

    from . import graphic_match
    from .scene_planner import _id_memo, _photo_caption
    pid = _id_memo()
    chosen = [Path(p) for p in chosen]
    stills = [Path(s) for s in stills]
    caps = {pid(s): graphic_match.tokens(_photo_caption(s)) for s in stills}
    df = Counter(t for c in caps.values() for t in c)
    modern = {pid(s): modern_presence(s) for s in stills}
    out = list(chosen)
    reasons = ["matched"] * len(out)

    def spare_for(k: int, flagged_ok: bool) -> Path | None:
        """The unused photo for slide k whose caption matches its line best (pool order on a tie); one the review saw
        modern presence in only when `flagged_ok`."""
        used = {pid(p) for j, p in enumerate(out) if j != k}
        spare = [s for s in stills if pid(s) not in used and pid(s) != pid(out[k])
                 and (flagged_ok or not modern.get(pid(s)))]
        if not spare:
            return None
        said = graphic_match.tokens(f"{cards[k].get('heading', '')} {cards[k].get('body', '')}") if k < len(cards) \
            else set()
        return max(spare, key=lambda s: (sum(1 / df[t] for t in caps.get(pid(s), set()) & said), -stills.index(s)))

    for k in range(len(out)):
        allow = k >= len(cards) or present_line(cards[k])
        if any(pid(q) == pid(out[k]) for q in out[:k]):          # a repeat: the unused photo that fits best
            pick = spare_for(k, flagged_ok=allow) or spare_for(k, flagged_ok=True)
            if pick is None:
                reasons[k] = "repeat kept: the pool has no unused photo"
                continue
            reasons[k] = f"no repeat: {out[k].name} is already on an earlier slide; {pick.name} fits the line best"
            out[k] = pick
        seen = "" if allow else modern_presence(out[k])
        if not seen:
            continue
        pick = spare_for(k, flagged_ok=False)
        if pick is None:
            reasons[k] = (f"kept: the photo shows {seen} and the line is not about the subject today; no unused "
                          "photo without it")
            continue
        reasons[k] = (f"presence: {out[k].name} shows {seen} and the line is not about the subject today; "
                      f"{pick.name} is the unused photo whose caption matches the line best")
        out[k] = pick
    # Freeze cycle 5 (PHOTO-03/04/05; src/photo_fitness.py): a slide's photograph is fit for the slide's window (its
    # own frame out of the crop, the view not flat nor emptier than the picture, nothing foreign under the masthead),
    # on both copies. An unfit one gives way to the unused photograph that is fit for it and whose caption matches
    # the line best (the first rung of the ladder: another photograph the run already has); with none, it is kept,
    # the renderer takes its best crop (_fill) and the choice says so ("unfit": the measures it fails).
    fits = _deck_fits(cards, out)
    kept_unfit: dict[int, list[str]] = {}
    for k, bad in enumerate(fits):
        if not bad:
            continue
        allow = k >= len(cards) or present_line(cards[k])
        used = {pid(p) for j, p in enumerate(out) if j != k}
        spare = [s for s in stills if pid(s) not in used and pid(s) != pid(out[k])
                 and (allow or not modern.get(pid(s))) and not _slide_unfit(cards, k, s)]
        if not spare:   # kept as chosen: the choice's "unfit" names the measures (its reason stands)
            kept_unfit[k] = bad
            continue
        said = graphic_match.tokens(f"{cards[k].get('heading', '')} {cards[k].get('body', '')}") if k < len(cards)             else set()
        pick = max(spare, key=lambda s: (sum(1 / df[t] for t in caps.get(pid(s), set()) & said), -stills.index(s)))
        reasons[k] = (f"fit: {out[k].name} is unfit for the slide's window ({', '.join(bad)}); {pick.name} is the "
                      "unused fit photo whose caption matches the line best")
        out[k] = pick
    choice = [{"slide": k + 1, "photo": p.name, "reason": r} for k, (p, r) in enumerate(zip(out, reasons))]
    for c, a in zip(choice, chosen):
        if Path(a).name != c["photo"]:
            c["replaced"] = Path(a).name
    for k, bad in kept_unfit.items():
        choice[k]["unfit"] = bad
    return out, choice


def _slide_unfit(cards: list[dict[str, Any]], k: int, photo: Path) -> list[str]:
    """Why `photo` is unfit for slide k's window on either copy (photo_fitness problems; [] when it is fit, when the
    slide draws no photograph of its own or when the photo cannot be read: the renderer decides then). Judged on the
    window as asked (the ladder's order: another photograph before another crop of this one); a structure under the
    masthead counts only when no window of the photograph clears it (the deck window rule moves it first)."""
    from . import photo_fitness as pf
    from .image_presentation import full_image
    if k >= len(cards):
        return []
    archetype = MultiFormatGenerator.sequence_archetypes(cards)[k]
    if archetype == "diagram":
        return []
    try:
        if full_image(Path(photo)):
            return []
        out: list[str] = []
        for facebook in (False, True):
            window = MultiFormatGenerator.photo_window(archetype, facebook)
            rec = pf.place(Path(photo), window, fixed=True)        # the window as asked: the plain centred fit
            bad = [p for p in rec["problems"] if p != "protected"]
            if "protected" in rec["problems"] and not pf.place(Path(photo), window)["fit"]:
                bad.append("protected")                            # no window of it clears the masthead's box
            out += [p for p in bad if p not in out]
        return out
    except (OSError, ValueError):
        return []


def _deck_fits(cards: list[dict[str, Any]], photos: list[Path]) -> list[list[str]]:
    """_slide_unfit for each slide's chosen photograph."""
    return [_slide_unfit(cards, k, p) for k, p in enumerate(photos)]


# ---------------------------------------------------------------- what a posted slide shows
# A slide's record (still_layout.json beside it) keeps its card and photos, so the publisher can say what each
# posted slide shows (alt text), credit every photo it shows, and pick Facebook's photos.

def slide_record(slide: Path) -> dict[str, Any]:
    from . import still_layout
    return (still_layout.load(Path(slide)) or {}).get("recipe") or {}


def slide_photos(slide: Path) -> list[Path]:
    """The photos a slide shows: its own, and on a comparison slide the second one under it."""
    rec = slide_record(slide)
    photos = [rec.get("photo")] + ([rec.get("photo_next")] if rec.get("archetype") == "comparison" else [])
    return [Path(x) for x in photos if x]


def photo_credit(photo: Path) -> dict[str, Any]:
    """The credits.json entry of a sourced photo ({} if none). A derived copy (crop, blur, upscale) has no entry
    beside it; its lineage leads back to the original."""
    photo = Path(photo)
    for _ in range(4):
        try:
            data = json.loads((photo.parent / "credits.json").read_text(encoding="utf-8"))
            images = data if isinstance(data, list) else data.get("images", [])
            entry = next((e for e in images if e.get("file") == photo.name), None)
        except (OSError, ValueError, AttributeError):
            entry = None
        if entry:
            return entry
        try:
            from .lineage import sidecar
            photo = Path(json.loads(sidecar(photo).read_text(encoding="utf-8"))["sources"][0]["path"])
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            return {}
    return {}


CAROUSEL_SHARE_ALIKE = "📷 These slides are licensed CC BY-SA 4.0: https://creativecommons.org/licenses/by-sa/4.0/"


CAROUSEL_ADAPTED = "📷 Photos adapted: cropped, with text added."


def carousel_credits(slides: list[Path]) -> tuple[list[str], bool]:
    """The credit line of every photo the slides show whose licence asks for one, in slide order, once each
    (credits.asset_attribution's format), and whether the slides could be read at all. A CC BY-SA photo makes
    the slides an adaptation under the same licence, which they then say (as the video does)."""
    from .credits import asset_attribution, is_share_alike
    lines, share_alike, readable = [], False, bool(slides)
    for slide in slides:
        if not slide_record(slide):
            readable = False
        for photo in slide_photos(slide):
            entry = photo_credit(photo)
            line = asset_attribution(entry) if entry else ""
            if entry and is_share_alike(entry.get("license", "")):
                share_alike = True
            if line and line not in lines:
                lines.append(line)
    # CC BY 4.0 s3(a)(1)(B): say that the photos were changed (each slide crops its photo and sets text on it)
    adapted = [CAROUSEL_ADAPTED] if lines else []
    return ([CAROUSEL_SHARE_ALIKE] if share_alike else []) + lines + adapted, readable


# ---------------------------------------------------------------- a Story frame's photo credit
# A Story goes out one frame per request with no caption (publisher.publish_photos: "Stories take none"), so a CC BY
# or BY-SA photo on a frame was credited nowhere (design review, 2026-10-29). CC licences ask for attribution "in any
# reasonable manner based on the medium": on a Story that is the frame itself. The frame names the author, the
# licence and its address, and says it was adapted (each frame crops its photo and sets text on it, as the carousel
# says); a BY-SA photo makes the frame an adaptation under BY-SA, which it then states, as the slides and the video do.
STORY_CREDIT_PX = 26        # ~9.5 pt on a phone: small, and read at a glance like a printed photo credit
STORY_CREDIT_LEADING = 34
STORY_CREDIT_MIN_PX = 22    # a credit is set smaller before it takes another line
# QF-126c: the complete credit (the title, the author, the address of the photo's page, the licence's name and
# address, the note of changes; a BY-SA frame's own licence) takes three to five lines of the 912 px column: the
# fewest that hold it at 22 px or more, at most five (measured on the real photo pools, README of
# docs/reviews/quality-fixes-2026-11/QF-126c). A photo whose credit needs more is not used on a frame.
STORY_CREDIT_LINES = 5
STORY_CREDIT_GAP = 28       # between the frame's text block and the credit (the credit's backing fades over 18 px)
STORY_CREDIT_CONTRAST = 4.5  # WCAG AA for small text, against the pixels under the line
STORY_ADAPTED = "adapted: cropped, with text added"
STORY_SHARE_ALIKE = ("CC BY-SA 4.0", "creativecommons.org/licenses/by-sa/4.0")


def _short_url(url: str) -> str:
    return re.sub(r"^(?:https?:)?//(?:www\.)?", "", str(url or "").strip()).rstrip("/")


# QF-126c (operator, 2026-11-14: "follow the best practice"): the frame's credit follows Creative Commons' recommended
# attribution, TASL: the Title (where the record supplies one), the Author, the Source (a frame has no link, so the
# address of the photo's page as text, the shortest official one) and the Licence's name and address, with the
# note of what was changed. CC BY 2.0-3.0 s4(b) ask for the title "if supplied" and the URI the licensor specifies
# for the work; 4.0 s3(a)(1) for a URI or hyperlink to the material "to the extent reasonably practicable". The
# title is the file's own, whole (sanitised as every credit's text is: credits.sanitise_credit_text; not capped at
# credits.TITLE_MAX with "…" as a caption line is: a frame never carries a cut credit), without Commons' "File:"
# namespace and the file's type, which name the page and not the work. The source is the page by its Commons page
# id when the Commons API gave one for that very page (credits.short_source_url, QF-126b:
# https://commons.wikimedia.org/?curid=<id>, MediaWiki's own address for a page; never a third party's shortener),
# else the page's address as recorded; both without the scheme, as a printed address is written. A credit that cannot be set whole and legibly (story_credit_fit:
# at most STORY_CREDIT_LINES lines of the text column, at STORY_CREDIT_MIN_PX or more) is never cut: the Story does
# not use that photo (story_credit_fits), and a frame drawn with one anyway is a release problem.
_FILE_TITLE = re.compile(r"^\s*File:\s*|\.(?:jpe?g|png|tiff?|webp|gif|svg|tif)\s*$", re.IGNORECASE)


def _story_title(entry: dict[str, Any]) -> str:
    """The photo's title as the frame prints it, whole, "" when the record supplies none."""
    from .credits import sanitise_credit_text
    return sanitise_credit_text(_FILE_TITLE.sub("", _FILE_TITLE.sub("", str(entry.get("title") or ""))))


def _story_source(entry: dict[str, Any], pages: dict[str, int] | None) -> str:
    """The photo's page as the frame prints it: by its Commons page id when one is known, else as recorded; "" when
    the record has no http(s) link. Percent-escapes are written as the letters they stand for (as a browser shows
    the address) unless that would put a space or a control character in it."""
    from urllib.parse import unquote

    from .credits import short_source_url
    url = str(entry.get("source_url") or "")
    page = (pages or {}).get(url) if not entry.get("pageid") else entry.get("pageid")
    link = short_source_url({**entry, "pageid": page} if page else entry)
    if link == "Unknown":
        return ""
    readable = unquote(link)
    return _short_url(readable if not re.search(r"[\s\x00-\x1f\x7f]", readable) else link)


def story_credit(photo, pages: dict[str, int] | None = None) -> dict[str, Any]:
    """What a Story frame showing `photo` must credit on itself: {"required", "lines", ...}. Public-domain and CC0
    photos need none, and our own graphics and maps have no credits entry (their map data is public domain).
    `pages`: {source link: Commons page id} known for the run (credits.source_page_ids)."""
    from .credits import (
        author_name,
        is_share_alike,
        licence_link_key,
        needs_attribution,
    )
    entry = photo_credit(Path(photo)) if photo else {}
    if not entry:
        return {"required": False, "reason": "no credits entry (our own graphic, or a photo with no record)", "lines": []}
    licence = " ".join(str(entry.get("license") or "").split())
    if not needs_attribution(entry):
        return {"required": False, "reason": "licence asks for no attribution", "license": licence, "lines": []}
    author, share_alike, url = author_name(entry), is_share_alike(licence), _short_url(entry.get("license_url"))
    title, source = _story_title(entry), _story_source(entry, pages)
    # An adaptation carries the address of the licence it is under (BY-SA 3.0 s4(b), 4.0 s3(b)): for a BY-SA photo
    # that is BY-SA 4.0, the frame's own, which it states on its own line; the photo's licence and its address are
    # named too (4.0 s3(a)(1)(C)), once when the two are the same address.
    frame_sa = share_alike and url and licence_link_key("https://" + url) == licence_link_key("https://" + STORY_SHARE_ALIKE[1])
    licence_part = licence if (frame_sa or not url) else f"{licence} {url}"
    parts = [f"Photo: “{title}” by {author}" if title else f"Photo: {author}"]
    parts += [source] if source else []
    parts += [licence_part, STORY_ADAPTED]
    lines = [" · ".join(parts)]
    if share_alike:
        lines.append(f"This frame: {STORY_SHARE_ALIKE[0]} · {STORY_SHARE_ALIKE[1]}")
    return {"required": True, "title": title, "author": author, "license": licence,
            "license_url": entry.get("license_url") or "", "share_alike": share_alike,
            "source_url": entry.get("source_url") or "", "source": source, "lines": lines}


def _credit_wrap(d, line: str, font, width: int, break_addresses: bool = False) -> list[str]:
    """A credit line wrapped at its " · " breaks first (so "adapted: cropped, with text added" is never split from
    itself), and word by word only inside a part that is wider than the line on its own. `break_addresses`: an
    address wider than the line on its own is broken where a printed address may be (QF-126c): after a "/", before
    a "_", "-", "." or "?" (as style guides break a URL)."""
    out: list[str] = []
    for part in line.split(" · "):
        if out and text_size(d, f"{out[-1]} · {part}", font)[0] <= width:
            out[-1] = f"{out[-1]} · {part}"
        else:
            for ln in wrap_text(d, part, font, width):
                out += _break_address(d, ln, font, width) if break_addresses else [ln]
    return out


_ADDRESS_BREAK = re.compile(r"(?<=/)(?=[^/])|(?=[_\-.?])")


def _break_address(d, line: str, font, width: int) -> list[str]:
    """`line` (one wrapped line) as it is when it fits; else, when it is an address (one word with a "/"), in pieces
    broken at _ADDRESS_BREAK, each line filled as far as it goes."""
    if text_size(d, line, font)[0] <= width or " " in line or "/" not in line:
        return [line]
    out: list[str] = []
    for piece in _ADDRESS_BREAK.split(line):
        if out and text_size(d, out[-1] + piece, font)[0] <= width:
            out[-1] += piece
        elif piece:
            out.append(piece)
    return out


def _credit_weight() -> int:
    """The credit role's weight (tok("credit.weight"), freeze cycle 4: read, no longer a written SPEC value)."""
    from .brand_system import tok as _tok
    return int(_tok("credit.weight"))


def story_credit_fit(d, lines: list[str], width: int | None = None, probe: bool = False) -> dict[str, Any]:
    """How a credit's `lines` are set at the foot of a Story frame: {"px", "font", "drawn", "fits"}. The fewest drawn
    lines, each within the text column, at any size from STORY_CREDIT_PX down to STORY_CREDIT_MIN_PX (a credit is set
    smaller before it takes another line), then the largest size that keeps that count; at most STORY_CREDIT_LINES.
    At the floor otherwise, with "fits" False (QF-126c: never cut, never smaller). GlyphRefused when no bundled face
    can draw it (QF-94). `probe`: a measurement before any frame is drawn (story_credit_fits), which records no glyph
    event (font_engine.choose_face): only a frame that is drawn records its fallback or its refusal."""
    width = width or story_body_width()
    trials = []
    # every size with each address whole, then the floor with an address too wide for the line broken across lines
    sizes = [(c_px, False) for c_px in range(STORY_CREDIT_PX, STORY_CREDIT_MIN_PX - 1, -2)] + [(STORY_CREDIT_MIN_PX, True)]
    for c_px, broken in sizes:
        # the credit's lines in one face (QF-94): a fallback face for all of them when one needs it
        base = get_font(brand.DATA_FONT, c_px, weight=_credit_weight())   # freeze cycle 4: the credit token
        font_c = choose_face("\n".join(lines), base) if probe else field_font("\n".join(lines), base)
        if font_c is None:
            raise GlyphRefused("\n".join(lines), Path(str(base.path)).name, "")
        c_drawn = [ln for line in lines
                   for ln in _credit_wrap(d, line if probe else drawable(line, font_c), font_c, width, broken)]
        whole = all(text_size(d, ln, font_c)[0] <= width for ln in c_drawn)
        trials.append({"px": c_px, "font": font_c, "drawn": c_drawn, "broken": broken,
                       "fits": whole and len(c_drawn) <= STORY_CREDIT_LINES})
    fitting = [t for t in trials if t["fits"]]
    # every address whole first, then the fewest lines, then the largest size (the trials run from the largest size
    # down; min keeps the first)
    chosen = min(fitting, key=lambda t: (t["broken"], len(t["drawn"]))) if fitting else trials[-1]
    if chosen["fits"]:
        # the setting is planned as the frame plans it (story_credit_plates), so a setting judged to fit is one the
        # frame can draw: the probe and the drawing never disagree (QF-126c review 1)
        # on a band as tall as the credit and its halo (each plate is planned on its own crop), in ink: the halo
        # search is the frame's to make on its photo; this plans the same glyph masks and measures them
        lead = _story_credit_lead(chosen["px"])
        try:
            story_credit_plates(Image.new("RGB", (VIDEO_WIDTH, lead * len(chosen["drawn"]) + 120), brand.INK),
                                STORY_MARGIN, 60, lead, chosen["drawn"], chosen["font"], brand.STONE_LT)
        except (TypeError, ValueError):
            chosen = dict(chosen, fits=False)
    return chosen


def _story_credit_lead(px: int) -> int:
    return round(px * STORY_CREDIT_LEADING / STORY_CREDIT_PX)


def story_credit_plates(im: Image.Image, x: int, top: int, lead: int, lines: list[str], font, fill):
    """(the image with the credit's `lines` drawn from `top`, their plates). Each line is planned as the graphics
    plan a light face too thin for typography.ring_contrast to read its own interior (graphics._plan_thin): at the
    22 px floor a short line ("by Pinakpani") of IBM Plex Mono 500 has strokes about 2 px wide and no eroded
    interior, so it is measured against its drawn colour, its halo raised until it reaches the target, as a map's
    neighbour names are (QF-126c review 1: plan_text raised TypeError there)."""
    from .graphics import _plan_thin
    plates = []
    for k, ln in enumerate(lines):
        plate = _plan_thin(im, (x, top + k * lead), ln, font, fill, "la", halo_px=9)
        im = plate.draw(im)
        plates.append(plate)
    return im, plates


def story_credit_fits(photo, pages: dict[str, int] | None = None) -> bool:
    """Whether a Story frame on `photo` can carry the credit its licence asks for whole and legibly (a photo that asks
    for none always can). A credit no bundled face can draw is left to the frame's own refusal (QF-94: the frame is
    refused with the reason and the operator decides), as before this check."""
    credit = story_credit(photo, pages)
    if not credit["required"]:
        return True
    if not (credit["source"] and credit["license_url"]):   # no page or licence address to give: not complete
        return False
    try:
        return story_credit_fit(ImageDraw.Draw(Image.new("RGB", (8, 8))), credit["lines"], probe=True)["fits"]
    except GlyphRefused:
        return True


class StoryCreditFitError(LayoutError):
    """No photo the Story frames could use has a credit that can be set whole and legibly on a frame (QF-126c): the
    Story is held (main.hold_layout_error, the review card's "its text would not fit whole") rather than drawn with
    a credit cut or set below the type floor."""

    def __init__(self, photos: int):
        self.photos = photos
        super().__init__(f"the photo credit of each of its {photos} photo(s) cannot be set whole: more than "
                         f"{STORY_CREDIT_LINES} lines at {STORY_CREDIT_MIN_PX} px, or no address for its page or "
                         f"its licence")


def _contrast(im: Image.Image, box, text_rgb) -> float:
    """WCAG contrast of `text_rgb` against the bright end (90th-percentile luminance) of the pixels in `box`."""
    import numpy as np
    region = np.asarray(im.convert("RGB").crop(tuple(int(v) for v in box)), dtype=np.float32) / 255.0
    lin = np.where(region <= 0.03928, region / 12.92, ((region + 0.055) / 1.055) ** 2.4)
    bg = float(np.percentile(lin @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32), 90))
    return round((_rel_luminance(text_rgb) + 0.05) / (bg + 0.05), 2)


def story_credit_report(frames: list[Path]) -> dict[str, Any]:
    """For the release report: {"credited": [{"frame", "lines"}], "problems": [...]}. Every frame whose photo's
    licence asks for attribution must carry that credit, drawn whole, at the contrast it needs; a frame without its
    layout record cannot show what it credited, which is a problem too."""
    from .credits import source_page_ids
    from .hold_advice import STORY_REDRAW
    credited, problems = [], []
    by_dir: dict[str, dict[str, Any]] = {}
    for frame in [Path(f) for f in frames]:
        folder = str(frame.parent)
        if folder not in by_dir:
            try:
                by_dir[folder] = {e.get("frame"): e for e in json.loads((frame.parent / "layout.json").read_text(encoding="utf-8"))}
            except (OSError, ValueError, AttributeError):
                by_dir[folder] = {}
        photo = slide_record(frame).get("photo")
        drawn = (by_dir[folder].get(frame.name) or {}).get("photo_credit")
        if not photo or drawn is None:
            problems.append(f"{frame.name}: no layout record of its photo and credit (drawn before 2026-10-29?)")
            continue
        # The photo's page by its id or by its recorded address (QF-126c): either is its complete credit, so a
        # frame drawn before an id was found is not held for it.
        expected = story_credit(photo, source_page_ids(frame.parent.parent))
        if not expected["required"]:
            continue
        if drawn.get("lines") not in (expected["lines"], story_credit(photo)["lines"]):
            # a frame drawn before its credit took its present form (QF-126c) is drawn again, never posted as it is;
            # the cause and what is needed first, the lines after, so a notice cut short keeps them (review 1)
            problems.append(f"{frame.name}: drawn with another credit than its photo needs (an older form{STORY_REDRAW}; "
                            f"it credits {drawn.get('lines')}, its photo needs {expected['lines']}")
        elif (drawn.get("fits") is False or len(drawn.get("drawn") or []) > STORY_CREDIT_LINES
              or int(drawn.get("px") or 0) < STORY_CREDIT_MIN_PX):
            problems.append(f"{frame.name}: its photo's credit takes {len(drawn.get('drawn') or [])} lines at "
                            f"{drawn.get('px')} px, more than {STORY_CREDIT_LINES} at {STORY_CREDIT_MIN_PX} px or more "
                            f"(the frame needs a photo whose credit fits{STORY_REDRAW}")
        elif not drawn.get("complete"):
            problems.append(f"{frame.name}: the credit could not be drawn whole (a character the font lacks): {drawn.get('drawn')}")
        elif float(drawn.get("contrast") or 0) < STORY_CREDIT_CONTRAST:
            problems.append(f"{frame.name}: the credit's contrast is {drawn.get('contrast')}:1, under {STORY_CREDIT_CONTRAST}:1")
        else:
            credited.append({"frame": frame.name, "lines": drawn.get("lines")})
    return {"credited": credited, "problems": problems}


ALT_TEXT_MAX = 1000  # Instagram's and Upload-Post's limit per photo
_CAMERA_FILE = re.compile(r"\b(?:DSCN?|IMG|DCP)_?\d{3,}\w*\b", re.IGNORECASE)


def _photo_subject(photo: Path) -> str:
    """What a photo shows, in plain words, from its archive record: the description, else the file title."""
    entry = photo_credit(photo)
    text = str(entry.get("description") or "").strip() or re.sub(
        r"^File:|\.(jpe?g|png|tiff?|webp)$", "", str(entry.get("title") or ""), flags=re.I)
    text = re.sub(r"<[^>]+>", " ", _CAMERA_FILE.sub(" ", text)).replace("_", " ")
    text = " ".join(text.split()).strip(" .,-")
    # Its first sentence: uploaders append notes ("Title is mistake. Tree image is taken ...").
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    first = parts[0]
    for more in parts[1:]:
        if len(first) >= 20:
            break
        first += " " + more
    text = first.strip(" .,-")
    return text[:300].rsplit(" ", 1)[0] if len(text) > 300 else text


def _sentence(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text if not text or text[-1] in ".?!" else text + "."


def slide_alt_text(slide: Path) -> str:
    """Alt text for one posted slide: what its photo shows (the archive's own description), then the words on
    it, in the case the script wrote them ("" when the slide has no record). Plain sentences, at most 1,000
    characters; it states nothing the slide and the photo's record do not."""
    rec = slide_record(slide)
    card = rec.get("card") or {}
    if not rec or not (card.get("heading") or card.get("body")):
        return ""
    subjects = [x for x in (_photo_subject(photo) for photo in slide_photos(slide)) if x]
    if rec.get("archetype") == "diagram" and is_map_poster(rec.get("photo") or ""):
        picture = "A locator map from the video."
    elif rec.get("archetype") == "diagram":
        picture = "A diagram from the video."
    elif len(subjects) > 1:
        picture = f"Two photos: {_sentence(subjects[0])[:-1]}; and {_sentence(subjects[1])}"
    elif subjects:
        picture = f"Photo: {_sentence(subjects[0])}"
    else:
        picture = ""
    from .script_engine import facebook_photo_caption, neutral_cues
    clean = facebook_photo_caption if Path(slide).parent.name == "carousel_fb" else neutral_cues
    words = [_sentence(clean(card.get("heading", ""))), _sentence(clean(card.get("body", "")))]
    if rec.get("archetype") == "debate":  # the closing slide's share line; no comments footer (2026-10-29)
        words += [_sentence(rec.get("share") or "")]
    text = " ".join(x for x in [picture, "Text on the slide:", *words] if x)
    if len(text) > ALT_TEXT_MAX:
        text = text[:ALT_TEXT_MAX - 1].rsplit(" ", 1)[0] + "…"
    return text


# Operator decision 2026-11-02, approved for the default 2026-11-03: Facebook's collage shows five tiles (the fifth
# under "+N"), so its gallery takes at most five (the cover, the three strongest content slides, the closing slide).
# FACEBOOK_PHOTOS is the cap facebook_pick and publisher.py use (4 until 2026-11-03); FACEBOOK_GALLERY is its old name.
FACEBOOK_PHOTOS = 5
FACEBOOK_GALLERY = FACEBOOK_PHOTOS


_MYTH_BUST = re.compile(r"\b(?:disproved|disproven|debunked|myths?|mistaken|wrongly|false|once thought|initially|"
                        r"considered|believed)\b", re.IGNORECASE)
_ANAPHORA = re.compile(r"^\W*(?:it|its|this|that|these|those|they|their|them|he|she|his|her|here|there|such)\b",
                       re.IGNORECASE)


# slide_strength's tiers (review of round 4, 2026-11-02): the figure slide is the deck's one number hero and the
# only tile that is not a heading-and-body stack, so it outranks the drawn slide, which outranks a myth-bust (a text
# claim, usually on a stand-in photo). With two content tiles the figure and the drawn slide both make the post.
STRENGTH_FIGURE, STRENGTH_DRAWN, STRENGTH_MYTH_BUST = 3, 2, 1


def slide_strength(rec: dict[str, Any]) -> int:
    """How strongly a slide's record says it stands on its own as a tile (round 4, 2026-11-02), highest first:
    STRENGTH_FIGURE for a figure slide (a stat, or the colossal_stat archetype), STRENGTH_DRAWN for the drawn slide
    (diagram), STRENGTH_MYTH_BUST for a myth-bust (its heading or body contradicts an earlier view: "IMPACT THEORY
    DISPROVED"); else 0. A slide that is several of these takes the highest."""
    recipe = rec.get("recipe") or {}
    card = recipe.get("card") or {}
    if recipe.get("archetype") == "colossal_stat" or any(e.get("name") == "stat" for e in rec.get("elements") or []):
        return STRENGTH_FIGURE
    if recipe.get("archetype") == "diagram":
        return STRENGTH_DRAWN
    return STRENGTH_MYTH_BUST if _MYTH_BUST.search(f"{card.get('heading', '')} {card.get('body', '')}") else 0


def standalone_problems(rec: dict[str, Any]) -> list[str]:
    """Why a slide does not stand alone as a Facebook photo (each opens alone, without the caption or its
    neighbours): a body that opens on a pronoun ("It ...", "This ...") whose referent is on another slide, or a
    heading that is only a figure with no unit beside it. [] when it stands alone; nothing is rewritten."""
    card = (rec.get("recipe") or {}).get("card") or {}
    out = []
    if _ANAPHORA.match(str(card.get("body") or "")):
        out.append("its body opens on a word that points to another slide")
    heading = " ".join(str(card.get("heading") or "").split())
    if re.fullmatch(r"[\d,.\u2013+-]+", heading or "x"):
        out.append("its heading is a bare number")
    return out


def facebook_pick(slides: list[str], limit: int = FACEBOOK_PHOTOS) -> list[str]:
    """The slides Facebook's multi-photo post gets, at most `limit` (FACEBOOK_PHOTOS, 5): its collage puts "+N"
    over the last tile it shows, so a seven-photo post hid the closing share-and-follow slide (platform audit,
    2026-10-28). The cover, the
    closing slide, and between them the strongest content slides: those drawn as designed (no layout problem,
    nothing shortened), standing alone (standalone_problems), a single photo, the strongest (slide_strength: a
    figure, then the drawn slide, then a myth-bust), showing a photo the post does not already show, then the
    earliest (the script's order is its order of importance: the reveal comes first). Kept in slide order."""
    slides = list(slides)
    if len(slides) <= limit:
        return _figure_second(slides)
    from . import still_layout
    first, last, middle = slides[0], slides[-1], list(enumerate(slides[1:-1], 1))
    shown = {str(p) for p in slide_photos(Path(first)) + slide_photos(Path(last))}

    def rank(item):
        i, slide = item
        rec = still_layout.load(Path(slide)) or {}
        photos = {str(p) for p in slide_photos(Path(slide))}
        # A single full-bleed photo before a two-photo stack (round 3, 2026-11-02): a stack's hard seam reads as a
        # deck slide in a standalone lightbox and spends a collage tile on two half pictures.
        # Round 4: a photo that does not stand alone (its body leans on another slide) after one that does; then the
        # strongest (a figure, the drawn slide, a myth-bust) before photo novelty, so the order never rests on
        # which stand-in photos happened to repeat.
        return (bool(rec.get("problems")), len(rec.get("shortened") or []), bool(standalone_problems(rec)),
                len(photos) > 1, -slide_strength(rec), bool(photos and photos <= shown), i)

    picked = []
    while middle and len(picked) < limit - 2:  # one at a time: a pick's photos count as shown for the next
        best = min(middle, key=rank)
        middle.remove(best)
        picked.append(best)
        shown |= {str(p) for p in slide_photos(Path(best[1]))}
    return _figure_second([first] + [slide for _, slide in sorted(picked)] + [last])


COLLAGE_COVER_OVER_TILE = 1.25   # lander ruling (lane D cycle 3): cover heading over the collage's second top tile's


def collage_tile_step(slides: list[str]) -> tuple[int, float] | None:
    """(slide index, design px) for the Facebook collage's second top tile when it is a headline slide whose heading
    the cover's does not reach COLLAGE_COVER_OVER_TILE x: the first display-ladder step under the tile's size that
    the cover's heading reaches that share over. None when the post is not a 5-photo collage, tile 2 is a figure
    slide (a different device; ruling 3), it carries no heading, it already holds the ratio, or no step does. The
    cover is never stepped up. Read from the slides' layout records (the copy as drawn)."""
    from . import still_layout
    posted = facebook_pick(slides)
    if len(posted) != FACEBOOK_COLLAGE_MAX:
        return None
    recs = [still_layout.load(Path(x)) or {} for x in (posted[0], posted[1])]
    if slide_strength(recs[1]) == STRENGTH_FIGURE:
        return None

    def heading_px(rec):
        return next((float(e["px"]) for e in rec.get("elements") or [] if e.get("name") == "heading" and e.get("px")),
                    None)
    cover, tile = heading_px(recs[0]), heading_px(recs[1])
    if not cover or not tile or cover >= COLLAGE_COVER_OVER_TILE * tile - 1e-6:
        return None
    w = (recs[1].get("size") or MultiFormatGenerator.CAROUSEL_SIZE)[0]
    steps = [s for s in brand_system.display_steps("carousel") if s < tile - 1e-6
             and cover >= COLLAGE_COVER_OVER_TILE * s - 1e-6]
    if not steps:
        return None
    return slides.index(posted[1]), round(max(steps) * 1080 / w, 2)


def _figure_second(posted: list[str]) -> list[str]:
    """Lane D cycle 3 (lander ruling 4): in the 5-photo collage the cover and the second photo are the two equal top
    tiles, so the second is not a headline slide when the post has a figure slide (slide_strength STRENGTH_FIGURE:
    its heading at the stat's size, its read the figure): it moves to second place, the rest keep their order.
    Unchanged for a post of other than 5 photos (no such pair of tiles) or with no figure slide."""
    if len(posted) != 5:
        return list(posted)
    from . import still_layout
    middle = posted[1:-1]
    fig = next((x for x in middle if slide_strength(still_layout.load(Path(x)) or {}) == STRENGTH_FIGURE), None)
    if fig is None or fig == middle[0]:
        return list(posted)
    return [posted[0], fig] + [x for x in middle if x != fig] + [posted[-1]]


# Facebook's collage for a multi-photo post of 4:5 photos, as tiles (x, y, w, h) in shares of the frame's WIDTH, each
# showing the centre crop of its photo. Four photos (round 3, 2026-11-02; not yet measured on a capture): one large
# 2:3 portrait beside three stacked, in a square frame. Five or more (lane D cycle 3, lander ruling 3; measured on the
# operator's capture evidence/peers/fb_feed/peer_01.png, 1137 px wide: top tiles x 0-566 / 570-1137, bottom x 0-376 /
# 380-756 / 760-1137): two square halves over three square thirds, 4 px gaps at 1137 px, and "+N" over the centre of
# the fifth when the post has N more. Our post sends at most FACEBOOK_PHOTOS (5, facebook_pick), so no "+N" is drawn
# over it; the model keeps it for any set of more.
_FB_GAP = 4 / 1137
_FB_HALF = (1 - _FB_GAP) / 2
_FB_THIRD = (1 - 2 * _FB_GAP) / 3
FACEBOOK_COLLAGE = {
    4: ((0, 0, 2 / 3, 1), (2 / 3, 0, 1 / 3, 1 / 3), (2 / 3, 1 / 3, 1 / 3, 1 / 3), (2 / 3, 2 / 3, 1 / 3, 1 / 3)),
    5: ((0, 0, _FB_HALF, _FB_HALF), (_FB_HALF + _FB_GAP, 0, _FB_HALF, _FB_HALF))
       + tuple((k * (_FB_THIRD + _FB_GAP), _FB_HALF + _FB_GAP, _FB_THIRD, _FB_THIRD) for k in range(3)),
}
FACEBOOK_COLLAGE_MAX = 5
FACEBOOK_MORE_BOX = (0.25, 0.3, 0.75, 0.7)   # the fifth tile's centre (shares of the tile) that "+N" covers
COLLAGE_TEXT = ("kicker", "heading", "body", "stat", "share")


def collage_crop(size: tuple[int, int], aspect: float) -> tuple[int, int, int, int]:
    """The centre crop of a `size` photo that a tile of `aspect` (width / height) shows."""
    w, h = size
    if w / h < aspect:          # the tile is wider: the photo's top and bottom are cut
        vh = w / aspect
        return (0, round((h - vh) / 2), w, round((h + vh) / 2))
    vw = h * aspect
    return (round((w - vw) / 2), 0, round((w + vw) / 2), h)


def facebook_collage_report(slides: list[Path], board: Path | None = None, width: int = 1080) -> dict[str, Any]:
    """The posted Facebook set in its collage (FACEBOOK_COLLAGE): per tile, what its centre crop shows and which
    text elements it cuts (from each slide's layout record). {"layout", "tiles": [...], "problems": [...]}. With
    `board`, the collage is drawn there as a QA image. A set with no collage model (under 4 or over 5) is reported
    as such, with no problems."""
    from . import still_layout
    slides = [Path(x) for x in slides]
    more = max(0, len(slides) - FACEBOOK_COLLAGE_MAX)
    tiles = FACEBOOK_COLLAGE.get(min(len(slides), FACEBOOK_COLLAGE_MAX))
    if not tiles:
        return {"layout": None, "tiles": [], "problems": []}
    out, problems = [], []
    height = round(max(ty + th for _tx, ty, _tw, th in tiles) * width)
    canvas = Image.new("RGB", (width, height), brand.BONE) if board else None
    for n, (slide, (tx, ty, tw, th)) in enumerate(zip(slides, tiles)):
        rec = still_layout.load(slide) or {}
        size = tuple(rec.get("size") or MultiFormatGenerator.CAROUSEL_SIZE)
        crop = collage_crop(size, tw / th)
        cut = []
        for e in rec.get("elements") or []:
            if e["name"] in COLLAGE_TEXT:
                b = e["box"]
                if b[0] < crop[0] or b[1] < crop[1] or b[2] > crop[2] or b[3] > crop[3]:
                    cut.append(e["name"])
                elif more and n == len(tiles) - 1:   # "+N" over the last tile's centre
                    mx0, my0, mx1, my1 = (crop[0] + f * (crop[2] - crop[0]) if k % 2 == 0 else
                                          crop[1] + f * (crop[3] - crop[1]) for k, f in enumerate(FACEBOOK_MORE_BOX))
                    if b[0] < mx1 and b[2] > mx0 and b[1] < my1 and b[3] > my0:
                        cut.append(f"{e['name']} (under +{more})")
        out.append({"slide": slide.name, "tile": [round(v, 4) for v in (tx, ty, tw, th)], "crop": list(crop),
                    "cut": cut})
        problems += [f"{slide.name}: the collage tile cuts its {name}" for name in cut]
        if canvas is not None and slide.is_file():
            box = (round(tx * width), round(ty * width), round((tx + tw) * width), round((ty + th) * width))
            with Image.open(slide) as im:
                tile = im.convert("RGB").crop(crop).resize((box[2] - box[0], box[3] - box[1]), Image.Resampling.LANCZOS)
            canvas.paste(tile, box[:2])
            if cut:
                ImageDraw.Draw(canvas).rectangle(box, outline=brand.CORAL, width=6)
    if canvas is not None:
        Path(board).parent.mkdir(parents=True, exist_ok=True)
        canvas.save(board, quality=90)
    return {"layout": f"{len(slides)} photos", "tiles": out, "problems": problems, "more": more}


COLLAGE_REPORT = "collage_report.json"


def write_collage_report(slides: list[Path], out_dir: Path) -> dict[str, Any]:
    """ADVISORY, not a gate: the collage report (facebook_collage_report) of the set publisher.py posts by default
    (facebook_pick(slides), FACEBOOK_PHOTOS), written to <carousel_fb>/collage_report.json with its board
    (collage_board.jpg) and each cut logged as a warning. Nothing fails, redraws or blocks on it: no QA certifier
    reads this file yet (video_qa would have to), and the cuts it names (a collage tile's centre crop through a
    slide's kicker, heading or body) are an OPEN defect of the Facebook copy's layout, not fixed by recording them."""
    out_dir = Path(out_dir)
    posted = [Path(x) for x in facebook_pick([str(x) for x in slides])]
    rep = dict(facebook_collage_report(posted, out_dir / "collage_board.jpg"), advisory=True,
               posted=[x.name for x in posted])
    try:
        (out_dir / COLLAGE_REPORT).write_text(json.dumps(rep, indent=1), encoding="utf-8")
    except OSError as e:
        log.warning("Collage report not written to %s (%s).", out_dir, e)
    for problem in rep["problems"]:
        log.warning("Facebook collage (advisory): %s", problem)
    return rep


def _delivered_contrast(lay, path: Path) -> None:
    """Each placed element's ratio measured again on the saved JPEG (what is posted), written into its layout
    record, which still_layout keeps and video_qa.still_contrast_problems asserts (2026-10-29)."""
    from .typography import remeasure
    plates = getattr(lay, "plates", None)
    if not plates:
        return
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            remeasure(lay, im, plates)
            platform = platform_encode(im)
    except OSError as e:  # the planned ratios stay recorded
        log.warning("Contrast not re-measured on %s (%s).", Path(path).name, e)
        return
    # Round 3 (2026-11-02): measured again after a platform-like downscale and recompression; an element under 4.5
    # there is flagged in its element record (`contrast_platform`, `platform_low`, both kept in still_layout.json)
    # and listed in lay.meta["platform_contrast_low"]. ADVISORY, not a gate: only the delivered-JPEG `contrast` is
    # asserted (video_qa.still_contrast_problems); nothing asserts the platform figures, and lay.meta is not written
    # to still_layout.json (still_layout.Layout.to_dict leaves meta out), so the still-level list lives only in
    # memory and in the warning below. The per-element flags are the durable record.
    low = []
    for e in getattr(lay, "elements", []):
        ps = [p for p in plates.get(e["name"], []) if not p.empty]
        vals = [v for v in (p.measure(platform) for p in ps) if v is not None]
        if vals:
            e["contrast_platform"] = round(min(vals), 2)
            if e["contrast_platform"] < brand.TEXT_CONTRAST:
                e["platform_low"] = True
                low.append(e["name"])
    if isinstance(getattr(lay, "meta", None), dict):
        lay.meta["platform_contrast_low"] = low
    if low:
        log.warning("%s: under %.1f:1 after a platform re-encode: %s", Path(path).name, brand.TEXT_CONTRAST, low)


def platform_encode(im: Image.Image) -> Image.Image:
    """`im` as a platform delivers it, back at its own size for measuring: downscaled to PLATFORM_WIDTH, JPEG at
    PLATFORM_QUALITY with 4:2:0 chroma, scaled up again."""
    import io
    w, h = im.size
    small = im.convert("RGB").resize((PLATFORM_WIDTH, max(1, round(h * PLATFORM_WIDTH / w))), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=PLATFORM_QUALITY, subsampling=2)
    buf.seek(0)
    with Image.open(buf) as back:
        return back.convert("RGB").resize((w, h), Image.Resampling.BICUBIC)


def _font_family(font) -> str:
    """The bundled family a loaded font came from (for refitting it at another size)."""
    from .typography import FONT_FILES
    if getattr(font, "_rr_family", None):      # a brand face, or a fallback face standing in for one (QF-94)
        return font._rr_family
    base = Path(getattr(font, "path", "") or "").name
    return next((n for n, f in FONT_FILES.items() if f == base), brand.TEXT_FONT)


def redraw_story_frame(path: Path, recipe: dict[str, Any]) -> dict[str, Any]:
    """Draws one Story frame again with the tighter fit, from its kept recipe, in place (still_vision: the
    look reported its text cut off or overlapping); updates layout.json and the frame's layout record."""
    from . import still_layout
    from .credits import source_page_ids
    gen = MultiFormatGenerator()
    with kicker_set(recipe.get("kicker_step")):   # the Story's one kicker step (round 8 R1)
        im, lay = gen._render_story_frame(Path(recipe["photo"]), recipe["slide"], int(recipe["index"]),
                                          int(recipe["count"]), recipe.get("poll") or {}, tight=True,
                                          pages=source_page_ids(Path(path).parent.parent),
                                          title_px=recipe.get("title_px"))
    im.save(path, quality=95)
    _delivered_contrast(lay, path)
    layout_file = Path(path).parent / "layout.json"
    try:
        entries = json.loads(layout_file.read_text(encoding="utf-8")) if layout_file.is_file() else []
    except (OSError, ValueError):
        entries = []
    entry = {**lay.meta, "frame": Path(path).name}
    entries = [entry if e.get("frame") == entry["frame"] else e for e in entries] if any(
        e.get("frame") == entry["frame"] for e in entries) else entries + [entry]
    layout_file.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    prior = still_layout.load(path) or {}
    return still_layout.save(path, lay, recipe, int(prior.get("attempts", 1)) + 1)


SUBJECT_MAX_WORDS = 6


def standalone_subject(script_payload: dict[str, Any]) -> str:
    """The subject's name for Facebook's standalone photos: the carousel cover's heading ("THE EYE OF THE SAHARA"),
    the script's own words, never a qualifier it did not state; "" when it is missing or too long to be a name."""
    from .script_engine import facebook_photo_caption
    cards = script_payload.get("carousel_slides") or []
    heading = " ".join(facebook_photo_caption(str((cards[0] if cards else {}).get("heading") or "")).split())
    heading = heading.rstrip(" .!?:")
    return heading if heading and len(heading.split()) <= SUBJECT_MAX_WORDS else ""


def _redraw_strip(kept: dict[str, Any] | None, path: Path | None = None) -> dict[str, Any] | None:
    """The swipe strip a redrawn slide draws again: the same side; on slide 1 at the height slide 2's kicker rule
    has now (its record beside `path`, carousel_slide_02.jpg), so the line still meets across the seam after
    slide 2 was redrawn (round 4 review, 2026-11-02); the kept height only when slide 2 has no record. None when
    slide 2 now draws no strip."""
    if not kept or kept.get("side") is None:
        return None
    if kept.get("side") == "left":
        return {"side": "left"} if kept.get("box") else None
    if path is not None:
        from . import still_layout
        rec = still_layout.load(Path(path).with_name("carousel_slide_02.jpg"))
        if rec is not None:
            box = ((rec.get("recipe") or {}).get("strip") or {}).get("box")
            return {"side": "right", "y0": box[1], "y1": box[3]} if box else None
    box = kept.get("box") or [0, kept.get("y0"), 0, kept.get("y1")]
    if box[1] is None or box[3] is None:
        return None
    return {"side": "right", "y0": box[1], "y1": box[3]}


# ---------------------------------------------------------------- Instagram's swipe strip
# Round 4 (2026-11-02): a non-verbal swipe cue on the Instagram copy only. Slide 2's gold kicker rule runs in from
# the slide's left edge, and slide 1 carries the same strip's first SWIPE_STRIP design px at the same height, cut at
# its right edge: across the seam it reads as one line that continues onto the next slide. No chevron and no "swipe"
# words (operator, 2026-10-28). The Facebook copy is a standalone grid and draws none. Slide 1 draws its part only
# where nothing of its own text is near; else it draws none and says why (lay.meta["swipe_strip"]).
SWIPE_STRIP = 72          # design px of the strip on slide 1 (96 at 1440)
# design px it keeps from any text of slide 1: 36 (48 at 1440; was 24, 32): PLATES-13, panel 6 (the Aswan cover's strip
# ended ~45 px right of the heading at its cap line, a near-collision tick): it meets the heading or clears it by the
# ledger's 48 canvas px (24 half px), else slide 1 draws no part and says why (lay.meta["swipe_strip"])
SWIPE_CLEAR = 36


def swipe_strip(im: Image.Image, lay, strip: dict[str, Any]) -> Image.Image:
    """Draws `strip` ({"side": "left"} on slide 2: from the left edge to its kicker rule; {"side": "right", "y0",
    "y1"} on slide 1: the last SWIPE_STRIP px to the right edge) in gold; recorded in lay.meta["swipe_strip"]."""
    cw = im.width
    px = MultiFormatGenerator._px(cw)
    if strip.get("side") == "left":
        rule = lay.meta.get("kicker_rule")
        if not rule:
            lay.meta["swipe_strip"] = {"side": "left", "skipped": "the slide has no kicker rule"}
            return im
        box = [0, rule[1], rule[0], rule[3]]
    else:
        box = [cw - px(SWIPE_STRIP), int(strip["y0"]), cw, int(strip["y1"])]
        clear = px(SWIPE_CLEAR)
        hit = [e["name"] for e in lay.elements if e["name"] != "header_brand" and e["box"][2] + clear > box[0]
               and e["box"][1] - clear < box[3] and e["box"][3] + clear > box[1]]
        if hit:
            lay.meta["swipe_strip"] = {"side": "right", "skipped": f"it would touch the slide's {hit[0]}"}
            return im
        pf = (lay.meta.get("plate_form") or {}) if isinstance(lay.meta, dict) else {}
        if pf.get("laid") == "block":
            # PLATEFORM (lander ruling 5, review of cycle 6): under the block family nothing floats on the photograph:
            # the strip is drawn only inside a block (to its inner pad); the out-of-crop part that is the swipe cue is
            # the masthead stream's (outside the 3:4 grid crop), so its seam is left for it here
            inner = [(b["box"][0] + pf.get("side_pad", 0), b["box"][1], b["box"][2] - pf.get("side_pad", 0), b["box"][3])
                     for b in pf.get("blocks") or []]
            hold = next((ib for ib in inner if ib[1] <= box[1] and box[3] <= ib[3] and ib[2] > box[0]), None)
            if hold is None:
                lay.meta["swipe_strip"] = {"side": "right", "skipped": "under the block family the strip is drawn only "
                                           "inside a block; its out-of-crop part is the masthead stream's"}
                return im
            box = [box[0], box[1], min(box[2], round(hold[2])), box[3]]
    im = im.convert("RGB")
    if box[2] > box[0]:
        ImageDraw.Draw(im).rectangle([box[0], box[1], box[2] - 1, box[3] - 1], fill=brand.GOLD)
    lay.meta["swipe_strip"] = {"side": strip.get("side"), "box": box}
    return im


def redraw_carousel_slide(path: Path, recipe: dict[str, Any], tight: bool = True) -> dict[str, Any]:
    """Draws one carousel slide again with the tighter fit (or `tight` as given), from its kept recipe, in place.
    The record keeps the strip it drew now. When slide 2's strip moved (its kicker rule did), slide 1 is drawn
    again at its own fit so its part of the strip meets slide 2's across the seam (round 4 review, 2026-11-02)."""
    from . import still_layout
    path = Path(path)
    gen = MultiFormatGenerator()
    with kicker_set(recipe.get("kicker_step")):   # the deck's one kicker step (round 8 R1)
        im, lay = gen._render_carousel_slide(recipe["archetype"], Path(recipe["photo"]), Path(recipe["photo_next"]),
                                             recipe["card"], int(recipe["index"]), int(recipe["total"]),
                                             recipe.get("poll") or {}, recipe.get("share") or "",
                                             bool(recipe.get("facebook")),
                                             tight=tight, subject=recipe.get("subject") or "",
                                             heading_frame=int(recipe.get("heading_frame") or 2), lead=recipe.get("lead"),
                                             heading_tier=int(recipe.get("heading_tier") or 0),
                                             strip=_redraw_strip(recipe.get("strip"), path),
                                             window=int(recipe.get("window") or 0),
                                             heading_px=recipe.get("heading_px"))
        _render_state.tile_px = None
    gen._save_slide(im, path)
    _delivered_contrast(lay, path)
    prior = still_layout.load(path) or {}
    old_strip = recipe.get("strip") or {}
    recipe = {k: v for k, v in recipe.items() if k != "strip"}
    drew = lay.meta.get("swipe_strip") or {}
    if drew:
        recipe["strip"] = drew
    recipe = {k: v for k, v in recipe.items() if k != "windows"}
    if lay.meta.get("windows"):   # the photo windows as drawn now, with their fit (freeze cycle 5)
        recipe["windows"] = lay.meta["windows"]
    entry = still_layout.save(path, lay, recipe, int(prior.get("attempts", 1)) + 1)
    if old_strip.get("side") == "left" and old_strip.get("box") != drew.get("box"):
        first = path.with_name("carousel_slide_01.jpg")
        rec1 = still_layout.load(first) or {}
        r1 = rec1.get("recipe") or {}
        if r1 and int(r1.get("index", -1)) == 0 and not r1.get("facebook"):   # a slide 1 drawn with no part as well
            redraw_carousel_slide(first, {**r1, "strip": r1.get("strip") or {"side": "right"}},
                                  tight=bool(rec1.get("tight")))
    return entry
