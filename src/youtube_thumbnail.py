"""The 16:9 YouTube thumbnail (2026-11-01 design review: 4.5/10 before).

The documentary's thumbnail was drawn by the old depth-layered engine: a Cinzel headline, outlined pills around
an eyebrow and a tagline, eight words competing, no masthead, and at YouTube's mobile search size (about 168 px
wide) the text was about 6% of the frame, unreadable. The panel's specification, in the covers' own system:

- the photograph full-bleed, its reviewed feature (else its centre) placed right of centre;
- an ink gradient from the left edge (85% over the left 42%, easing out by x 1180), the type on it;
- the masthead top-left: the gold diamond and RELIC & RIDDLE in gold Cinzel Bold at 64 px;
- the hero: the copy's measured figure in gold Anton ("25 MILES", the unit at half the figure's size) with the
  subject's name tight under it in bone (at most four words in all), else a year from the tagline ("1963") over
  the payoff line, else the two headline lines in Anton, bone then gold; no eyebrow, no tagline, no pills;
- a crop that clamps and leaves the reviewed feature left of x 1250 is enlarged (at most x1.35) until it
  lands at x 1300-1380, and the type's column stops short of the feature;
- YouTube's timestamp corner (x 1620-1920, y 950-1080) carries nothing.

Round 3 (2026-11-02): the ink feather ends at x 1040 (it dimmed the subject's left edge); a figure the fact sheet
ranges or disputes is lifted out of the copy (never one bare number), a hedged one is the hero only with the
copy's own qualifier above it, and the claim's dimension word sits under the unit (figure_dim, outside the word
budget, like the qualifier: both belong to the figure); when the hero's words repeat the video title, the subject
line gives way to another line of the verified thumbnail copy (else meta["title_overlap"]); every render is
measured at feed size (downscale_check: 168 and 246 px wide).

Every line is planned at a measured contrast (typography.plan_text), like every other still.
"""
from __future__ import annotations

from typing import Any

from PIL import Image, ImageDraw, ImageOps

from . import brand
from .typography import cap_height, get_font, plan_text, tracked_width

W, H = 1920, 1080
LEFT = 96
TEXT_RIGHT = 1000
TIMESTAMP_ZONE = (1620, 950, 1920, 1080)
FOCUS_X = 1340            # where the reviewed feature's centre is placed
FOCUS_MIN_X = 1250        # a clamped crop that leaves it left of here is enlarged ...
FOCUS_LAND = (1300, 1380)  # ... until it lands in this band
MAX_ZOOM = 1.35           # never more than this beyond the cover fill
SHADE_ALPHA, SHADE_SOLID, SHADE_END = 0.85, 0.42 * W, 1040   # the left ink: 85% over the left 42%, then fading
BRAND_PX = 64             # the masthead's size (2026-11-02 round 2: 44 px read small); its track, mark and gap are
                          # the system's (src/brand_system.py masthead.*)
MAX_WORDS = 4             # across the headline lines
UNIT_RATIO = 0.5          # the unit beside the figure, at half its size
TITLE_GAP_EM = 0.08       # the figure's baseline to the title's caps, in title ems
DIM_PX = 44               # the claim's dimension word under the unit (figure_dim)
QUAL_PX = 96              # the copy's qualifier over the figure (figure_qualifier), at least; raised until it
                          # keeps LINE_FLOOR px of cap height at 168 px wide
# brand.DISPLAY_FONT: r7: the kicker's face when one word cannot hold the floor in the text face (_qual_font)
QUAL_WEIGHT, QUAL_TRACK = 800, 3   # brand.TEXT_FONT, the covers' eyebrow face (r5 review: in IBM Plex Mono
                                                            # the period took a full cell, "EST ." with a gap)
DIM_TITLE_GAP = 40        # px of clear field from the dimension word's baseline to the title's cap line
PLATE_TARGET = 5.1        # every line's planned ratio at full size ...
FEED_TARGET_STEP, FEED_TARGET_MAX = 1.0, 9.1   # ... raised per line until it holds 4.5 at 246 px wide
FEED_HALO_GROWTH = 0.35   # each raise also widens that line's halo by 35% of its default spread
FEED_WIDTHS = (168, 246)  # YouTube's mobile search and home-feed thumbnail widths
HERO_FLOOR, LINE_FLOOR = 12, 7   # px of cap height at 168 px wide: the hero line, every other line
_STOP = {"THE", "A", "AN", "OF", "IN", "AND", "ON", "AT", "TO", "BY", "FROM", "HOW", "WHY", "WHAT"}
# Feed-size texture: no floor, no contrast gate. r7: never a hedge or a year's event word ("qualifier",
# "figure_qualifier"): wherever a figure is shown its qualifier holds LINE_FLOOR at 168 px, else the figure is not shown.
_TEXTURE = frozenset({"brand", "figure_dim", "provenance"})
QUAL_LEADING = 18         # px between the two lines of a two-line hedge kicker ("BY ONE / ESTIMATE")
NUM_MAX = 300             # the numeral's ceiling (Anton). Round 4 proposed 400; held at 300: the larger type
                          # passes the feed gate on the branch's hard-photo test, which then no longer exercises
                          # the gate's re-plan loop (operator decision pending)
FEATHER_PAD, FEATHER_EASE, FEATHER_MIN_EASE = 40, 520, 320   # r7: the full ink holds to the type's edge + 40 (the
                          # type sits in solid ink), then a soft ease over 520 px that ends by the subject's centre
                          # (never under 320 px); the round-4 ease (240 px, capped at SHADE_END) still read as a hard
                          # vertical edge, and a long title ran past where it began
FEATHER_SLOPE_MAX = 1.0   # the fade's steepest step, in 8-bit levels over white per px (round 4: ~1.4)
BAND_PAD = (40, 72)       # the hero stack is centred between the masthead's foot + 40 and the frame's foot - 72
HEDGE_GAP = 22   # the headline path's hedge line (r7: at the qualifier kicker's feed floor, _qual_font) to the headline


def _photo(source: Image.Image, focus: dict | None, meta: dict | None = None) -> Image.Image:
    """The photograph filling the frame, its reviewed feature (else its centre) placed at x FOCUS_X. When the
    crop clamps at the photo's edge and leaves the feature left of FOCUS_MIN_X, it is enlarged (at most
    MAX_ZOOM) until the feature lands in FOCUS_LAND: on the ink side it sat under the headline."""
    if source.info.get("essential_full_image"):
        from .image_presentation import fit_image
        if meta is not None:
            meta["presentation"] = "essential_full_image"
        return fit_image(source, (W, H)).convert("RGB")
    src = ImageOps.exif_transpose(source).convert("RGB")
    sw, sh = src.size
    fill = max(W / sw, H / sh)
    fx, fy = 0.5, 0.5
    if focus:
        b = focus["focus_box"]
        fx, fy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2

    def place(scale):
        w, h = round(sw * scale), round(sh * scale)
        left = min(0, max(W - w, round(FOCUS_X - fx * w)))
        top = min(0, max(H - h, round(540 - fy * h)))
        return w, h, left, top

    scale = fill
    w, h, left, top = place(scale)
    if left + fx * w < FOCUS_MIN_X:   # the feature (or, unreviewed, the photo's centre) left on the ink side
        target = sum(FOCUS_LAND) / 2
        scale = fill * min(MAX_ZOOM, max(1.0, target / max(1.0, fx * sw * fill)))
        w, h, left, top = place(scale)
    canvas = Image.new("RGB", (W, H))
    canvas.paste(src.resize((w, h), Image.Resampling.LANCZOS), (left, top))
    if meta is not None:
        meta["zoom"] = round(scale / fill, 3)
        meta["focus_x"] = round(left + fx * w)
        if focus:
            b = focus["focus_box"]
            meta["focus_box"] = [round(left + b[0] * w), round(top + b[1] * h), round(left + b[2] * w),
                                 round(top + b[3] * h)]
    return canvas


def _gradient(im: Image.Image, type_right: float | None = None, stop: float | None = None) -> Image.Image:
    """Ink at SHADE_ALPHA over the left 42% (the type's side), easing to nothing by SHADE_END. With the type's
    measured right edge (round 4), the full ink holds to that edge plus FEATHER_PAD, so every line sits in solid
    ink (r7: it was capped short of a long title), and then eases over FEATHER_EASE, its steepest step under
    FEATHER_SLOPE_MAX: a soft gradient with no visible vertical edge. `stop`: the subject's centre on the canvas;
    the ink is gone by there (never sooner than FEATHER_MIN_EASE past the solid ink)."""
    import numpy as np
    solid, end = SHADE_SOLID, SHADE_END
    if type_right is not None:
        solid = type_right + FEATHER_PAD
        end = solid + FEATHER_EASE
        if stop is not None:
            end = max(solid + FEATHER_MIN_EASE, min(end, stop))
    x = np.arange(W, dtype=np.float32)
    t = np.clip((x - solid) / max(1.0, end - solid), 0, 1)
    a = (1 - t * t * (3 - 2 * t)) * SHADE_ALPHA
    alpha = Image.fromarray((a * 255).astype("uint8")[None, :]).resize((W, H))
    ink = Image.new("RGBA", (W, H), brand.INK + (255,))
    ink.putalpha(alpha)
    return Image.alpha_composite(im.convert("RGBA"), ink)


def _words(text: str, limit: int) -> str:
    """The line whole, in capitals, when it has at most `limit` words, else "" (QF-23, operator decision option 1,
    decisions/QF-23-thumbnail-question.md: the budget selects a whole verified line, never its first `limit`
    words; it cut the line there before)."""
    words = " ".join((text or "").split()).upper().split()
    return " ".join(words) if len(words) <= limit else ""


# QF-143: marks that lead a line into the one written after it ("THE RICHAT STRUCTURE:" over "THE EYE OF THE SAHARA")
_LEAD_MARKS = ":;,&/+-–—"
# QF-143 round 2: the words a whole headline line the writer chose cannot end on, standing alone. These are the
# articles, the prepositions that take an object (cover_design._OBJECT_PREPOSITIONS) and the true conjunctions. IN
# is left out because it is also an adverbial particle ("LOCKED IN"). The copulas and relatives of
# cover_design._JOINERS are left out too: they end complete lines ("HERE IT IS", "WHAT IT WAS", "GUESS WHO"). Those
# classes were tuned for the remainder of a cut field (QF-141), not for a whole line.
_LINE_CONJUNCTIONS = frozenset({"AND", "OR", "BUT", "NOR"})


def _line_end_words() -> frozenset:
    from .cover_design import _ARTICLES, _OBJECT_PREPOSITIONS
    return _ARTICLES | (_OBJECT_PREPOSITIONS - {"IN"}) | _LINE_CONJUNCTIONS


# A line that opens on OF or THAN continues a phrase from the line before it ("OF THE ANDES") and never stands
# alone. A line that opens on a conjunction stands only when the next word starts a clause of its own: a subject
# pronoun or a sentence adverb ("AND THEN NONE", "BUT NOBODY KNEW", "AND IT VANISHED"). Followed by anything else
# (an article, a noun), the conjunction carries on a list from the line before ("AND THE SPHINX").
_OPEN_PREPOSITIONS = frozenset({"OF", "THAN"})
_CLAUSE_OPENERS = frozenset({
    "I", "WE", "YOU", "HE", "SHE", "IT", "THEY", "THIS", "THAT", "THESE", "THOSE", "THERE", "HERE",
    "NOBODY", "NO", "NONE", "NOTHING", "NOONE", "EVERYONE", "SOMEONE", "SOMETHING", "EVERYTHING",
    "THEN", "YET", "SO", "STILL", "NOW", "NEVER", "ALWAYS", "ONLY", "EVEN", "SOMEHOW", "SUDDENLY",
    "WHY", "HOW", "WHAT", "WHO", "WHERE", "WHEN",
})


def _end(text: str) -> str:
    """`text` without the marks at its end that lead into a line not drawn after it (QF-143): a drawn headline
    never ends on a colon, semicolon, comma, dash, ampersand, plus or slash."""
    t = (text or "").rstrip()
    while t and t[-1] in _LEAD_MARKS:
        t = t[:-1].rstrip()
    return t


def _alone(text: str) -> str:
    """`text`, a whole verified headline line drawn without the copy line written with it (the budget left that one
    out, or the copy has none), as it stands alone (QF-143). Its leading mark is dropped (_end). It is "" when it
    still dangles: it ends on a word that needs what followed it (_line_end_words: an article, a preposition that
    takes an object, a conjunction: "THE LOST CITY OF", "CARVED FROM"), or it opens on a word that joins it to what
    came before (OF or THAN, or a conjunction that does not open a clause of its own: "OF THE ANDES", "AND THE
    SPHINX"). A line closed by its own sentence mark ("GUESS WHO?", "OR WAS IT?") stands. A line is never cut
    (QF-23)."""
    import re
    t = _end(text)
    words = [w.strip("'").upper() for w in re.findall(r"[^\W_]+(?:'[^\W_]+)*", t)]
    if not words or t[-1] in "?!.…":
        return t
    if words[-1] in _line_end_words() or words[0] in _OPEN_PREPOSITIONS:
        return ""
    if words[0] in _LINE_CONJUNCTIONS and (len(words) < 2 or words[1] not in _CLAUSE_OPENERS):
        return ""
    return t


def headline_note(meta: dict) -> str:
    """The review card's line for a headline the budget did not take whole (QF-23, option 1), or ""."""
    if meta.get("headline_dropped"):
        return f"🖼 YouTube thumbnail: drawn without its headline: {meta['headline_dropped']}"
    if meta.get("headline_from"):
        if meta.get("headline_fragment"):   # QF-143 round 2: the true reason, not the budget's
            return (f"🖼 YouTube thumbnail: headline from the {meta['headline_from']}: "
                    f"{' and '.join(meta['headline_fragment'])} does not stand alone without the line written with it")
        return (f"🖼 YouTube thumbnail: headline from the {meta['headline_from']}: the title's lines do not fit the "
                f"four-word budget whole")
    if meta.get("headline_lines_dropped"):
        return (f"🖼 YouTube thumbnail: {' and '.join(meta['headline_lines_dropped'])} left out: longer than the "
                f"budget, never cut")
    if meta.get("headline_fragment"):
        return (f"🖼 YouTube thumbnail: {' and '.join(meta['headline_fragment'])} left out: it does not stand alone "
                f"without the line written with it")
    return ""


def _intersects(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _content(text: str) -> set:
    import re
    return {w for w in re.findall(r"[A-Z0-9']+", str(text or "").upper()) if w not in _STOP and len(w) > 1}


def _type_edge(plates) -> float | None:
    """The drawn type's right edge (every plate), or None when nothing is drawn."""
    boxes = [p.box[2] for _, p in plates if not p.empty]
    return float(max(boxes)) if boxes else None


def render(source: Image.Image, copy: dict[str, Any], focus: dict | None = None,
           meta: dict | None = None, figure: dict | None = None, youtube_title: str | None = None,
           illustration: bool = False, reveal: str | None = None) -> tuple[Image.Image, list]:
    """(the 1920x1080 thumbnail, its text plates) from a hero photograph and the cover copy. `meta`, when given,
    receives the crop's zoom and the reviewed feature's box on the canvas (`focus_box`). `figure`: the fact
    sheet's word on the hero figure (cover_design.figure_check); `youtube_title`: the video's title, so the
    thumbnail does not repeat it; `illustration`: the photo is a generated illustration (the disclosure label);
    `reveal`: the script's reveal or takeaway line (a tagline that answers it is never drawn).

    The feed-size gate closes the loop (r5 review, 2026-11-02: the delivered Gol Gumbaz sample planned its title
    at 5.1 full size and measured 4.43 at 246 px wide, failing its own gate): a line whose ratio falls under 4.5
    at feed size is planned again with a higher target and a wider halo until it holds or FEED_TARGET_MAX. The
    first pass also measures the type's right edge, and the ink's feather is fitted to it (round 4)."""
    targets: dict[str, float] = {}
    given = dict(meta or {})
    edge = None
    while True:
        trial = dict(given)   # each attempt starts from the caller's meta, so nothing stale carries over
        im, plates = _render(source, copy, focus, trial, figure, youtube_title, illustration, targets, edge, reveal)
        if edge is None and _type_edge(plates) is not None:
            edge = _type_edge(plates)
            continue
        low = [n for n in _low_contrast(trial) if targets.get(n, PLATE_TARGET) + FEED_TARGET_STEP <= FEED_TARGET_MAX]
        if not low:
            break
        for n in low:
            targets[n] = targets.get(n, PLATE_TARGET) + FEED_TARGET_STEP
    if targets:
        trial["feed_targets"] = {n: round(t, 2) for n, t in targets.items()}
    if edge is not None:
        trial["ink_edge"] = round(edge)
    if meta is not None:
        meta.update(trial)
    return im, plates


def _low_contrast(meta: dict) -> list:
    """The gated lines downscale_check found under 4.5 at 246 px wide."""
    return [n for n, row in (meta.get("downscale") or {}).items() if n not in _TEXTURE
            and row.get("contrast_246") is not None and row["contrast_246"] < 4.5]


def feed_floor_px(floor: float) -> int:
    """The least display size (reference units: brand.DISPLAY_FONT) whose drawn cap holds `floor` px at YouTube's
    168 px feed width (HERO_FLOOR or LINE_FLOOR): a gate on drawn ink as a size the ladder is held at (cycle 0
    round 8, ruling 6: a face of smaller cap takes a larger reference size; Anton: 160 for the hero, 94 a line)."""
    from .brand_system import px_for_cap
    return px_for_cap(brand.DISPLAY_FONT, floor * W / FEED_WIDTHS[0])


def _lockup(measure, l1: str, l2: str, width: float, kickerless: bool = False, room: float | None = None):
    """The headline lockup (round 7, F1 / F21; round 8, rulings 4, 5, 7): ([(element name, [(dx, text, colour)]) per
    drawn line], line-1 font, line-2 font, record). One size for the lockup's lines (lockup.line_ratio, a minimum:
    line 2 never smaller than line 1); the wide display_xl ladder (190, its fallback 160), never under the hero
    line's feed floor (HERO_FLOOR at 168 px wide, feed_floor_px); line 2 gold but its opening function words; one
    field alone is a lockup whose payoff run (the engine's seam) is gold.

    One form per run (ruling 4, panel BC-8): the two fields are set in the RULED form every other surface sets
    (heading.lockup_fields: line 1's closing function words open line 2, CARVED / FROM SOLID SALT), each field one
    line, else (ruling 7) two lines (the two-line tier, valid forms; the phrase exception keeps a payoff word alone:
    THE SECRET / WEAPON), at most three rows in all. Where the ruled form cannot hold the feed floor on the ladder,
    the authored whole lines (QF-23 / QF-143) are set instead and the record says why (form_diverges: legibility at
    feed size outranks one form across surfaces). The kickerless variant (ruling 5): with no kicker drawn above it,
    the headline takes its larger size on two lines before the fallback size on one ("one size up": the kicker's
    room). Below the ladder only a recorded last resort (off_ladder, 4 px steps, valid forms) down to 96; where
    none sets, LayoutError (a recorded refusal, never type drawn past its column)."""
    from .brand_system import TYPE, TYPE_FALLBACK, tok
    from .heading import GOLD_ROLES, field_runs, lockup_fields, set_field, set_heading
    from .typography import LayoutError
    ratio = tok("lockup.line_ratio")

    def width_at(t, sz):
        return measure.textlength(t, font=get_font(brand.DISPLAY_FONT, sz))

    def runs_of(line_runs, font):
        out, dx = [], 0.0
        for t, role in line_runs:
            out.append((round(dx), t, brand.GOLD if role in GOLD_ROLES else brand.BONE))
            dx += measure.textlength(t + " ", font=font)
        return out

    def attempt(f1, f2, s1, tier, n):
        s2 = max(s1, round(s1 * ratio))
        rows, forms_ = [], []
        for key, text, sz, gold in (("title_1", f1, s1, False), ("title_2", f2, s2, True)):
            if not text:
                continue
            if not (f1 and f2):
                form = set_heading(text, width_at, width, (sz,), max_lines=n, lockup=True)
                line_runs = form.runs() if form else None
            else:
                form = set_field(text, width_at, width, sz, max_lines=n)
                line_runs = field_runs(text, form.cuts, gold) if form else None
            if line_runs is None:
                return None
            forms_.append(form)
            font = get_font(brand.DISPLAY_FONT, sz)
            rows += [(key, runs_of(lr, font)) for lr in line_runs]
        if len(rows) > 3:
            return None
        if room is not None:
            ch = cap_height(get_font(brand.DISPLAY_FONT, s2))
            if (len(rows) - 1) * int(ch * 1.18) + ch > room:
                return None
        phrase = [f.record["phrase_exception"] for f in forms_ if f.record.get("phrase_exception")]
        return rows, get_font(brand.DISPLAY_FONT, s1), get_font(brand.DISPLAY_FONT, s2), {
            "tier": tier, "sizes": [s1, s2], "ratio": ratio, "line1": f1, "line2": f2, "rows": len(rows),
            **({"phrase_exception": phrase} if phrase else {})}

    floor = feed_floor_px(HERO_FLOOR)
    ladder = (round(TYPE["wide"]["display_xl"]), round(TYPE_FALLBACK["wide"]["display_xl"]))
    sizes = sorted({max(s, floor) for s in ladder}, reverse=True)
    order = [(s, t, 1) for t, s in enumerate(sizes)] + [(s, t, 2) for t, s in enumerate(sizes)]
    if kickerless and tok("lockup.kickerless_up") and len(sizes) > 1:
        order = [(sizes[0], 0, 1), (sizes[0], 0, 2)] + [(s, t + 1, n) for t, s in enumerate(sizes[1:]) for n in (1, 2)]
    forms = []
    if l1 and l2:
        r1, r2, _ = lockup_fields(l1, l2)
        forms.append((r1, r2, None))
        if (r1, r2) != (l1, l2):
            forms.append((l1, l2, "the ruled form does not hold the feed floor on the ladder"))
    else:
        forms.append((l1, l2, None))

    def done(got, why):
        if kickerless:
            got[3]["kickerless"] = True
        if why:
            got[3]["form_diverges"] = {"ruled": list(forms[0][:2]), "drawn": [got[3]["line1"], got[3]["line2"]],
                                       "reason": why}
        return got
    for f1, f2, why in forms:
        for s1, tier, n in order:
            got = attempt(f1, f2, s1, tier, n)
            if got:
                return done(got, why)
    # Lander ruling (cycle 0 round 8): no surface draws under its floor. The ladder above is held at the hero's feed
    # floor; a headline no form sets there is refused (recorded), never drawn smaller (round 7 stepped off the ladder
    # to 96 px: the second palette drew 5.8-9.8 px of cap at the 168 px feed against the 12 px floor)
    raise LayoutError(f"the YouTube headline {' / '.join(t for t in (l1, l2) if t)!r} sets in no form of "
                      f"{round(width)} px at its {floor} px feed floor or more")


def _title_form(measure, title: str, width: float, room: float):
    """(lines, font, record) for the figure branch's title under the figure (cycle 0 round 8, ruling 7; panel MG-2:
    it was a continuous fit from 150 to 90 that refused where two lines fit). The wide display ladder's steps from
    152 down (brand_system.display_steps), never under its feed floor (LINE_FLOOR at 168 px wide), one line, else
    the two-line tier (valid forms), in `room` px of height; else LayoutError (recorded, never drawn unfit)."""
    from .brand_system import display_steps
    from .heading import set_heading
    from .typography import LayoutError
    floor = feed_floor_px(LINE_FLOOR)
    steps = [s for s in display_steps("wide") if s <= 152]
    sizes = sorted({max(round(s), floor) for s in steps}, reverse=True)

    def width_at(t, sz):
        return measure.textlength(t, font=get_font(brand.DISPLAY_FONT, sz))
    for n in (1, 2):
        for tier, sz in enumerate(sizes):
            form = set_heading(title, width_at, width, (sz,), max_lines=n)
            if form is None:
                continue
            f = get_font(brand.DISPLAY_FONT, sz)
            ch = cap_height(f)
            if (len(form.lines) - 1) * int(ch * 1.18) + ch > room:
                continue
            rec = {"size": sz, "tier": tier, "lines": list(form.lines)}
            if form.record.get("phrase_exception"):
                rec["phrase_exception"] = form.record["phrase_exception"]
            return list(form.lines), f, rec
    raise LayoutError(f"the YouTube title {title!r} sets in no form of {round(width)} px at {sizes[-1]} px or more")


def _kicker_font(text: str, width: float):
    """F20 on YouTube: the system kicker's face and weight at the least size whose cap holds LINE_FLOOR at 168 px
    wide, when its rule, gap and tracked words fit `width` there; else None (not drawn, recorded by the caller).
    Round 8 (lander ruling: no surface draws under its floor): it no longer steps under the floor to fit."""
    from .brand_system import tok
    size = QUAL_PX
    while cap_height(get_font(brand.TEXT_FONT, size, weight=tok("kicker.weight"))) * FEED_WIDTHS[0] / W < LINE_FLOOR:
        size += 2
    font = get_font(brand.TEXT_FONT, size, weight=tok("kicker.weight"))
    ch = cap_height(font)
    if (tok("kicker.rule") + tok("kicker.rule_gap")) * ch + tracked_width(text, font, tok("kicker.track") * size) <= width:
        return font
    return None


# The figure's size steps (reference units; round 8: they were a continuous fit from NUM_MAX down to 160 in a column
# less a fixed 300 px reservation for the unit), each held at the hero's feed floor; the unit at UNIT_RATIO of the
# figure, never under its line floor (LINE_FLOOR at 168 px wide: "SQ MI" drew 6.0 px against 7 in the second palette)
FIGURE_STEPS = (300, 260, 220, 190, 160)
FIGURE_UNIT_GAP = 20


def _figure_fonts(measure, number: str, unit: str, width: float, dim_w: float = 0.0):
    """(figure font, unit font, record): the largest FIGURE_STEPS size, held at the hero's feed floor, at which the
    numeral, the gap and its unit (or the dimension words under the unit, `dim_w`, whichever is wider) fit `width`,
    the unit at UNIT_RATIO of it and at least its own feed floor; else LayoutError (recorded, never under a floor)."""
    from .typography import LayoutError
    num_floor, unit_floor = feed_floor_px(HERO_FLOOR), feed_floor_px(LINE_FLOOR)
    sizes = sorted({max(s, num_floor) for s in FIGURE_STEPS}, reverse=True)
    for tier, s in enumerate(sizes):
        f_num = get_font(brand.DISPLAY_FONT, s)
        f_unit = get_font(brand.DISPLAY_FONT, max(80, round(s * UNIT_RATIO), unit_floor))
        need = measure.textlength(number, font=f_num)
        if unit:
            need += FIGURE_UNIT_GAP + max(measure.textlength(unit, font=f_unit), dim_w)
        if need <= width:
            return f_num, f_unit, {"size": s, "unit_size": f_unit.size, "tier": tier}
    raise LayoutError(f"the figure {number} {unit} does not fit {round(width)} px at its feed floors")


def _kicker_gap(font) -> int:
    from .brand_system import tok
    return round(tok("kicker.gap") * font.size)


def _draw_kicker(im, x, baseline, text, font, feed_kw):
    """The system kicker (src/multi_format.draw_kicker's device: a flat gold rule one cap long at mid-cap, then the
    tracked capitals), planned at the thumbnail's feed target."""
    from .brand_system import colour_of, tok
    colour = colour_of("kicker.colour")
    ch = cap_height(font)
    bar_w, gap = max(8, round(ch * tok("kicker.rule"))), round(ch * tok("kicker.rule_gap"))
    thick = max(3, round(ch * tok("kicker.rule_thick")))
    mid = baseline - ch / 2
    from .brand_system import GATES
    from .multi_format import plan_kicker
    kw = dict(feed_kw, target=max(feed_kw.get("target", 0), GATES["contrast.kicker"]))
    plate, rec = plan_kicker(im, (x + bar_w + gap, baseline), text, font, colour, "ls",
                             tok("kicker.track") * font.size, **kw)
    if plate is None:   # fail closed under the kicker gate (sweep S1)
        return im, None, rec
    ImageDraw.Draw(im).rectangle([round(x), round(mid - thick / 2), round(x) + bar_w - 1,
                                  round(mid - thick / 2) + thick - 1], fill=tuple(plate.fill))
    return plate.draw(im), plate, rec


def _qual_font(text: str, width: float):
    """(lines, font, track) for a qualifier or event kicker at LINE_FLOOR px of cap height at 168 px wide: one line
    (tracked, else untracked), else two at the word break that balances them (16:9 has the height the 9:16 cover
    lacks: "BY ONE / ESTIMATE" over the figure); else (r7) in Anton, the brand's condensed display face, at the same
    cap floor: one word cannot break, and the column beside a reviewed feature is ~660 px ("ESTIMATED" in
    Montserrat 800 is 710, "REDISCOVERED" 975). None when no such setting fits `width`."""
    size = QUAL_PX
    while cap_height(get_font(brand.TEXT_FONT, size, weight=QUAL_WEIGHT)) * FEED_WIDTHS[0] / W < LINE_FLOOR:
        size += 4
    font = get_font(brand.TEXT_FONT, size, weight=QUAL_WEIGHT)
    for track in (QUAL_TRACK, 0):
        if tracked_width(text, font, track) <= width:
            return [text], font, track
    words = text.split()
    best = None
    for k in range(1, len(words)):
        a, b = " ".join(words[:k]), " ".join(words[k:])
        wide = max(tracked_width(a, font, QUAL_TRACK), tracked_width(b, font, QUAL_TRACK))
        if wide <= width and (best is None or wide < best[0]):
            best = (wide, [a, b])
    if best:
        return best[1], font, QUAL_TRACK
    size = QUAL_PX
    while cap_height(get_font(brand.DISPLAY_FONT, size)) * FEED_WIDTHS[0] / W < LINE_FLOOR:
        size += 2
    condensed = get_font(brand.DISPLAY_FONT, size)
    for track in (QUAL_TRACK, 0):
        if tracked_width(text, condensed, track) <= width:
            return [text], condensed, track
    return None


def _render(source, copy, focus, meta, figure, youtube_title, illustration, targets, edge=None, reveal=None):
    def feed(name: str, font) -> dict:
        """plan_text's target (and, once raised, a wider halo: at feed size a 21-31 px halo shrinks to 3-4 px and
        stops separating the glyph from a busy photo) for line `name`."""
        t = targets.get(name, PLATE_TARGET)
        if t <= PLATE_TARGET:
            return {"target": t}
        steps = (t - PLATE_TARGET) / FEED_TARGET_STEP
        base = int(min(31, max(21, font.size // 3)))
        return {"target": t, "halo_px": round(base * (1 + FEED_HALO_GROWTH * steps)), "max_halo": 1.0}
    from .cover_design import (
        _YEAR,
        _dropped_fields,
        _without,
        drawn_qualifier,
        figure_qualifier,
        hedge_line,
        monument_figure,
        must_withhold,
        resolving_tagline,
        withhold_figure,
    )
    from .multi_format import typeset
    why = resolving_tagline(copy, reveal)
    if why:
        meta["tagline_dropped"] = {"tagline": copy.get("tagline"), "reason": why}
        copy = dict(copy, tagline="")
    if must_withhold(figure):
        # A range or disputed value, or (r7) an estimate whose hedge cannot be shown legibly: no figure anywhere.
        withheld = withhold_figure(copy, figure)
        if withheld != dict(copy):
            meta["figure_withheld"] = {"number": figure.get("number"), "unit": figure.get("unit"),
                                       "reason": figure.get("reason"), **_dropped_fields(copy, withheld)}
            if not figure.get("conflict"):
                meta["figure_declined"] = figure.get("reason") or "the figure's hedge cannot be shown"
        copy = withheld

    def withhold_here(why, found_):
        """The figure lifted out of the copy: its qualifier cannot hold the feed floor here (r7)."""
        nonlocal copy
        fig = figure if (figure and figure.get("unit")) else {"number": found_[0], "unit": found_[1]}
        meta["figure_declined"] = why
        withheld_ = withhold_figure(copy, fig, force=True)
        meta["figure_withheld"] = {"number": fig.get("number"), "unit": fig.get("unit"), "reason": why,
                                   **_dropped_fields(copy, withheld_)}
        copy = withheld_
    photo = _photo(source, focus, meta)
    im = _gradient(photo, edge, meta.get("focus_x"))
    measure = ImageDraw.Draw(im)
    plates = []

    # The masthead: the system's one treatment (src/masthead.py, cycle 0): the gold diamond, then the name in
    # tracked bone capitals (gold until cycle 0: a second masthead colour that competed with the hook), its ground
    # solved as ruled (scrim peak 0.60, halo 0.16, gate 5.5, target 6.5), top-left.
    from . import masthead
    f_brand = masthead.font_for(BRAND_PX)
    top = 72
    mode = im.mode
    im, name, meta["masthead"], _ = masthead.draw(im, LEFT, top + cap_height(f_brand), BRAND_PX)
    im = im.convert(mode)
    if name is not None:   # fail closed: an undrawn masthead (under its gate) is recorded in meta["masthead"]
        plates.append(("brand", name))
    # The band under the masthead the hero stack is centred in (round 4: it sat at a fixed y 560).
    centre = round((top + cap_height(f_brand) + BAND_PAD[0] + H - BAND_PAD[1]) / 2)
    band = (H - BAND_PAD[1]) - (top + cap_height(f_brand) + BAND_PAD[0])   # the stack's height (round 8)

    # The type never reaches the reviewed feature: its column ends 48 px short of it.
    right = TEXT_RIGHT
    fb = meta.get("focus_box")
    if fb and LEFT + 640 <= fb[0] - 48 < right:   # a feature reaching further left than that is wider than the
        right = fb[0] - 48                       # frame's right side: the type keeps its column (recorded below)
    width = right - LEFT
    found = monument_figure(copy)
    q_raw = figure_qualifier(copy, found)[0] if (found and found[1]) else ""
    same = bool(figure and found and str(figure.get("number")) == found[0])
    qualifier = ""
    if q_raw:
        qualifier = str(figure.get("drawn_qualifier") or "") if (same and figure.get("drawn_qualifier")) else (
            drawn_qualifier(q_raw, found[1]))
    q_fit, q_name = None, "figure_qualifier"
    if found and found[1] and figure and figure.get("sheet_missing"):
        meta["figure_declined"] = figure.get("reason") or "the payload was fact-checked but its sheet did not load"
        found = None
    elif found and found[1] and figure and figure.get("hedged") and not qualifier:
        meta["figure_declined"] = "the sheet hedges this figure and the copy carries no qualifier for it"
        found = None   # never one bare number for an estimate: the headline carries it as written instead
    elif found and found[1] and qualifier:
        q_fit = _qual_font(qualifier, width)
        if q_fit is None:
            # The hedge does not hold the feed floor: the figure is shown nowhere (r7, operator default; round 4
            # set it in the headline with the hedge as texture, so the figure read bare at feed size).
            withhold_here(f"the hedge {qualifier!r} does not fit at feed size", found)
            found = None
    dimension = str((figure or {}).get("dimension") or "") if (found and found[1]) else ""
    year = None
    if not (found and found[1]) and "figure_declined" not in meta:
        # No measured figure: a year from the tagline or the eyebrow is the hero when the copy has one. With the
        # fact sheet's word on it (round 4), only with the sheet's own event word set above it ("REDISCOVERED").
        for field in ("tagline", "eyebrow"):
            m = _YEAR.search(str(copy.get(field) or ""))
            if m:
                year = (m.group(1), "", field, m.span())
                break
        if year and figure and figure.get("kind") == "year" and str(figure.get("number")) == year[0]:
            event = str(figure.get("event") or "")
            q_fit = _qual_font(event, width) if event else None
            if not event:
                meta["year_declined"] = figure.get("reason") or "the fact sheet gives no event word for this year"
                year = None
            elif q_fit is None:
                # r7: the event word cannot hold the feed floor ("REDISCOVERED", 12 capitals): the year is not the
                # hero (round 4 set the word as texture and 1963 read bare at feed size). The covers decline it too.
                meta["year_declined"] = f"the event word {event!r} does not fit at feed size"
                year = None
            else:
                qualifier = event
                meta["year_event"] = event
    hero = found if (found and found[1]) else year
    if hero:
        number, unit, field, span = hero
        rest = dict(copy)
        if field in ("line1", "line2"):
            rest[field] = _without(str(copy[field]), span)
        named = [(k, str(rest.get(k) or "")) for k in ("line1", "line2")]
        if not unit:   # a year: the payoff line (the second) under it, else the first
            named = [(k, ln) for k, ln in named[::-1] if ln.strip()][:1]
        lines = [ln for _, ln in named]
        budget = MAX_WORDS - (2 if unit else 1)   # the figure (and its unit) count
        # QF-23 (option 1): both lines when they fit the budget whole, else the first whole line that does, else
        # another verified line (eyebrow, tagline) that does, else no headline (recorded); a line is never cut.
        # QF-143: a line drawn without its partner stands alone (_alone: no trailing colon, never a fragment).
        both = _words(" ".join(lines), budget)
        both = _end(both) if sum(bool(ln.strip()) for ln in lines) > 1 else _alone(both)
        title = next((t for t in [both] + [_alone(_words(ln, budget)) for ln in lines] if t), "")
        if not both:
            frag = [k for k, ln in named if _words(ln, budget) and not _alone(_words(ln, budget))]
            if frag:
                meta["headline_fragment"] = frag
        if not title and any(ln.strip() for ln in lines):
            alt = next(((k, _alone(_words(str(rest.get(k) or ""), budget))) for k in ("eyebrow", "tagline")
                        if _alone(_words(str(rest.get(k) or ""), budget))), None)
            if alt:
                meta["headline_from"] = alt[0]
                title = alt[1]
            else:
                meta["headline_dropped"] = (f"no verified line stands alone in the {budget}-word headline"
                                            if "headline_fragment" in meta else
                                            f"no verified line fits the {budget}-word headline whole")
        if youtube_title and len(_content(title) & _content(youtube_title)) >= 2:
            # The title already says it: the subject line gives way to another verified line of the copy.
            alt = next(((k, _alone(_words(display_line, budget))) for k in ("eyebrow", "tagline")
                        for display_line in [str(rest.get(k) or "")]
                        if display_line.strip() and len(display_line.split()) <= budget
                        and _alone(_words(display_line, budget))
                        and len(_content(display_line) & _content(youtube_title)) < 2
                        and not _content(display_line) <= _content(title)), None)
            if alt:
                meta["tension_from"] = alt[0]
                title = alt[1]
            else:
                meta["title_overlap"] = True
        title = typeset(title)
        f_dim = get_font(brand.TEXT_FONT, DIM_PX, weight=700)
        dim_lines = (dimension.split() if dimension and measure.textlength(dimension, font=f_dim) > 300 else (
            [dimension] if dimension else []))[:2] if unit else []
        # The numeral on its size steps, held at its feed floor, its unit at its own (round 8, lander ruling)
        f_num, f_unit, meta["figure_form"] = _figure_fonts(
            measure, number, unit, width, max((measure.textlength(t, font=f_dim) for t in dim_lines), default=0.0))
        q_lines, f_q, q_track = q_fit if (q_fit and qualifier) else ([], None, QUAL_TRACK)
        num_w = measure.textlength(number, font=f_num)
        q_h = (len(q_lines) * cap_height(f_q) + (len(q_lines) - 1) * QUAL_LEADING + 28) if f_q else 0
        # Round 8 (ruling 7, panel MG-2): the title under the figure on the wide display ladder, one line, else the
        # two-line tier, in the band's height left under the figure (it was a continuous fit 150 -> 90 that refused
        # where two lines fit, and its TextFitError aborted the run's whole cover job)
        t_lines, f_t, meta["title_form"] = _title_form(measure, title, width, band - q_h - cap_height(f_num) - (
            DIM_TITLE_GAP if dim_lines else 0)) if title else ([], None, None)
        t_pitch = int(cap_height(f_t) * 1.18) if f_t else 0
        gap_t = round(TITLE_GAP_EM * f_t.size) if f_t else 0
        if dim_lines:   # the dimension word ends on the figure's baseline: the title keeps clear of it (r5: ~12 px)
            gap_t = max(gap_t, DIM_TITLE_GAP)
        stack = q_h + cap_height(f_num) + (gap_t + cap_height(f_t) + (len(t_lines) - 1) * t_pitch if f_t else 0)
        base = round(centre - stack / 2 + q_h + cap_height(f_num))
        for k, line in enumerate(q_lines if f_q else []):
            yq = base - cap_height(f_num) - 28 - (len(q_lines) - 1 - k) * (cap_height(f_q) + QUAL_LEADING)
            # ruling 8 (cycle 0 round 8): a figure's qualifier is bone; its number and unit gold
            p_q = plan_text(im, (LEFT, yq), line, f_q, brand.BONE, "ls", q_track, **feed(q_name, f_q))
            im = p_q.draw(im)
            plates.append((q_name, p_q))
        p_num = plan_text(im, (LEFT, base), number, f_num, brand.GOLD, "ls", **feed("figure", f_num))
        im = p_num.draw(im)
        plates.append(("figure", p_num))
        if unit:
            nx = LEFT + num_w + FIGURE_UNIT_GAP
            pitch = cap_height(f_dim) + 12
            unit_base = base - (len(dim_lines) * pitch + 6 if dim_lines else 0)
            p_unit = plan_text(im, (nx, unit_base), unit, f_unit, brand.GOLD, "ls", **feed("figure_unit", f_unit))
            im = p_unit.draw(im)
            plates.append(("figure_unit", p_unit))
            for k, t in enumerate(dim_lines[:2]):
                yb = base - (min(2, len(dim_lines)) - 1 - k) * pitch
                p_dim = plan_text(im, (nx, yb), t, f_dim, brand.BONE, "ls", 2, target=5.1)
                im = p_dim.draw(im)
                plates.append(("figure_dim", p_dim))
        for k, line in enumerate(t_lines):
            p_t = plan_text(im, (LEFT, base + gap_t + cap_height(f_t) + k * t_pitch), line, f_t, brand.BONE, "ls",
                            **feed("title", f_t))
            im = p_t.draw(im)
            plates.append(("title", p_t))
    else:
        hedge = hedge_line(copy, figure)
        h_fit = _qual_font(hedge[0], width) if hedge else None
        if hedge and h_fit is None:
            found_h = monument_figure(copy)
            if found_h:
                withhold_here(f"the hedge {hedge[0]!r} does not fit at feed size", found_h)
            hedge = None
        l1, l2 = _words(copy.get("line1", ""), 3), _words(copy.get("line2", ""), 3)
        long_lines = [k for k, t in (("line1", l1), ("line2", l2)) if str(copy.get(k) or "").strip() and not t]
        if l1 and l2:
            l2 = _end(l2)   # QF-143: the headline never ends on a mark that leads on
        elif l1 or l2:
            # QF-143: one line drawn without the other stands alone: no trailing colon ("THE RICHAT STRUCTURE:"
            # with "THE EYE OF THE SAHARA" over the budget), and never a fragment (_alone)
            k, kept = ("line1", l1) if l1 else ("line2", l2)
            if not _alone(kept):
                meta["headline_fragment"] = [k]
            l1, l2 = (_alone(l1), "") if l1 else ("", _alone(l2))
        if (long_lines or "headline_fragment" in meta) and not (l1 or l2):
            # QF-23 (option 1): no headline line fits the budget whole: another verified line that does, else none
            alt = next(((k, _alone(_words(str(copy.get(k) or ""), 3))) for k in ("eyebrow", "tagline")
                        if _alone(_words(str(copy.get(k) or ""), 3))
                        and not (youtube_title and len(_content(copy.get(k)) & _content(youtube_title)) >= 2)), None)
            if alt:
                meta["headline_from"], l1 = alt
            else:
                meta["headline_dropped"] = ("no verified line stands alone in the 3-word headline lines"
                                            if "headline_fragment" in meta else
                                            "no verified line fits the 3-word headline lines whole")
        elif long_lines:
            meta["headline_lines_dropped"] = long_lines   # the other line stands alone, whole
        kicker = ""
        if bool(l1) != bool(l2) and ("figure_withheld" in meta or "figure_declined" in meta
                                     or "headline_lines_dropped" in meta):
            # Round 4: a withheld or declined figure left one headline line (Richat: "SAHARA EYE" alone, a third
            # of the left half empty). The other line of the two-tone system comes from the verified copy: the
            # eyebrow, else the tagline, when it is short, new to the headline and not the video title again.
            kept = l1 or l2
            fill = next(((k, _words(str(copy.get(k) or ""), 3)) for k in ("eyebrow", "tagline")
                         if str(copy.get(k) or "").strip() and len(str(copy.get(k)).split()) <= 3
                         and not _content(copy.get(k)) <= _content(kept)
                         and not (youtube_title and len(_content(copy.get(k)) & _content(youtube_title)) >= 2)),
                        None)
            if fill:
                meta["headline_fill_from"] = fill[0]
                kicker = typeset(fill[1])
        h_lines, f_h, h_track = h_fit if (hedge and h_fit) else ([], None, QUAL_TRACK)
        h_h = (len(h_lines) * cap_height(f_h) + (len(h_lines) - 1) * QUAL_LEADING + HEDGE_GAP) if f_h else 0
        f_k = None
        if kicker:
            # F20: the filled line is the system's kicker (text face 800, tracked, gold, the lead rule, the one gap)
            # at the size that holds the feed floor (LINE_FLOOR at 168 px wide); one that does not fit the column at
            # that size is not drawn (recorded): it was a bone Anton line competing with the headline (panel F20).
            f_k = _kicker_font(kicker, width)
            if f_k is None:
                meta["headline_fill_dropped"] = {"from": meta.pop("headline_fill_from", None), "text": kicker,
                                                 "reason": "the kicker does not fit the column at the feed floor"}
                kicker = ""
            else:
                h_h += cap_height(f_k) + _kicker_gap(f_k)
        # Round 7 (F1, F21) and round 8 (rulings 4, 5, 7): the headline is the YouTube lockup, set by the heading
        # engine in the run's one ruled form (the authored seam repaired, as on the Reel cover), line 2 at least
        # line 1's size, the pair on the wide display_xl ladder held at its feed floor, one line a field, else the
        # two-line tier; kickerless (no kicker, no hedge above it), the larger size on two lines first. Below the
        # ladder only as a recorded last resort; a headline no form sets is a recorded refusal (LayoutError).
        lines, f, f2, lock = _lockup(measure, typeset(l1), typeset(l2), width, kickerless=not (f_k or f_h),
                                    room=band - h_h)
        meta["lockup"] = lock
        pitch = int(cap_height(f2) * 1.18)
        y = round(centre - (pitch * (len(lines) - 1) + cap_height(f) + h_h) / 2 + h_h + cap_height(f))
        if f_k:
            im, p_k, meta["kicker"] = _draw_kicker(im, LEFT, y - cap_height(f) - _kicker_gap(f_k), kicker, f_k,
                                                   feed("kicker", f_k))
            if p_k is not None:
                plates.append(("kicker", p_k))
        if f_h:
            # The hedge on its own line above the headline (round 4), as on the covers ("ESTIMATED"), at the
            # feed floor and gated like every other line (r7: it was texture).
            foot = y - cap_height(f) - (cap_height(f_k) + _kicker_gap(f_k) if f_k else 0) - HEDGE_GAP
            for k, line in enumerate(h_lines):
                yh = foot - (len(h_lines) - 1 - k) * (cap_height(f_h) + QUAL_LEADING)
                p_h = plan_text(im, (LEFT, yh), line, f_h, brand.BONE, "ls", h_track, **feed("qualifier", f_h))
                im = p_h.draw(im)
                plates.append(("qualifier", p_h))
        for k, (name, runs) in enumerate(lines):
            for dx, t, colour in runs:
                fr = f2 if name == "title_2" else f   # line 2 at its own size (lockup.line_ratio, a minimum)
                p = plan_text(im, (LEFT + dx, y + k * pitch), t, fr, colour, "ls", **feed(name, fr))
                im = p.draw(im)
                plates.append((name, p))
    if illustration:
        im, p = _provenance(im)
        plates.append(("provenance", p))
    if fb:
        meta["text_over_focus"] = [n for n, p in plates if n != "brand" and _intersects(p.box, fb)]
    meta["_plates"] = plates
    downscale_check(im.convert("RGB"), meta)
    return im.convert("RGB"), plates


def _provenance(im: Image.Image):
    """The existing on-screen disclosure (video_composer.RECON_BADGE_LABEL) bottom-left, clear of the timestamp
    corner: IBM Plex Mono in STONE_LT on an INK plate."""
    from .video_composer import RECON_BADGE_LABEL
    f = get_font(brand.DATA_FONT, 30, weight=600)
    w = ImageDraw.Draw(im).textlength(RECON_BADGE_LABEL, font=f)
    y1 = H - 64
    ImageDraw.Draw(im).rectangle((LEFT, y1 - cap_height(f) - 20, LEFT + w + 28, y1), fill=brand.INK + (255,))
    p = plan_text(im, (LEFT + 14, y1 - 10), RECON_BADGE_LABEL, f, brand.STONE_LT, "ls", target=5.1, halo_px=0)
    return p.draw(im), p


def downscale_check(im: Image.Image, meta: dict) -> bool:
    """The thumbnail as the feed shows it (FEED_WIDTHS): each text plate's cap height there and its contrast on
    the downscaled pixels, into meta["downscale"]; meta["downscale_ok"] and meta["downscale_fails"] say whether
    the hero line keeps HERO_FLOOR px at 168 wide, every other line LINE_FLOOR px (the masthead, the dimension word
    and the disclosure are texture), every contrast 4.5 at 246 wide, and no plate reaches past the ink's fade."""
    plates = meta.get("_plates") or []
    hero = next((n for n in ("figure", "title_1", "title") if any(k == n for k, _ in plates)), None)
    texture = _TEXTURE
    out, fails = {}, []
    blurred = {w: im.resize((w, round(w * H / W)), Image.Resampling.LANCZOS).resize((W, H), Image.Resampling.BICUBIC)
               for w in FEED_WIDTHS}
    for name, p in plates:
        if p.empty:
            continue
        cap = cap_height(p.font)
        row = {f"cap_{w}": round(cap * w / W, 1) for w in FEED_WIDTHS}
        row.update({f"contrast_{w}": (round(v, 2) if v is not None else None)
                    for w in FEED_WIDTHS for v in [getattr(p, "measure_ring", p.measure)(blurred[w])]})  # tile scale: ring
        out.setdefault(name, row)
        if name in texture:
            continue
        floor = HERO_FLOOR if name == hero else LINE_FLOOR
        if row["cap_168"] < floor:
            fails.append(f"{name} {row['cap_168']} px cap at 168 wide (floor {floor})")
        # Contrast is gated on the 246 px home-feed tile: at 168 px the resampling blurs every stroke into its
        # halo, so the ring measure there reads low even for the 300 px figure (recorded, not gated). Round 4
        # tried gating the hero lines at 168 too: raising a line's target and halo does not lift its 168 px
        # measure on a busy photo, so the gate could not be met; it stays recorded.
        c = row["contrast_246"]
        if c is not None and c < 4.5:
            fails.append(f"{name} contrast {c} at 246 wide")

        if p.box[2] > SHADE_END:
            fails.append(f"{name} reaches past the ink's fade (x {p.box[2]})")
    meta["downscale"], meta["downscale_fails"], meta["downscale_ok"] = out, fails, not fails
    return not fails
