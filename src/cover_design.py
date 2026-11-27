"""Photograph-first art direction for the existing Reel cover variants.

Photographs are composed inside the centre square before delivery crops are
made. Explicitly reviewed features can be framed more closely; other photos
stay whole. No saliency mask is treated as a semantic subject detector.
"""
from PIL import Image, ImageDraw, ImageOps

from . import brand
from .brand_system import GATES, tok, type_px
from .heading import GOLD_ROLES, field_runs, lockup_fields, set_field, set_heading
from .typography import (
    TextFitError,
    fit_text,
    get_font,
    place_text,
    plan_text,
    probe_text,
    record_contrast,
    text_size,
)

DESIGN_VERSION = "cover-composition-2"


def reviewed_focus(path, kept=None):
    """An explicitly reviewed source region, bound to exact photo bytes.

    Unknown or changed photographs keep the conservative whole-image layout.
    Geometry-changing derivatives never inherit coordinates from another file.
    """
    import hashlib
    import json
    import math

    from .config import BASE_DIR
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if kept is not None:
            photos = [kept]
        else:
            data = json.loads((BASE_DIR / "data/cover_art_direction.json").read_text(encoding="utf-8"))
            photos = data.get("photos", []) if isinstance(data, dict) else []
        for photo in photos:
            if not isinstance(photo, dict):
                continue
            if photo.get("sha256") != digest:
                continue
            box = photo.get("focus_box") or []
            if (len(box) == 4 and all(isinstance(v, (int, float)) and math.isfinite(v) for v in box)
                    and 0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
                return {"focus_box": box, "sha256": digest, "reviewed": photo.get("reviewed", "")}
    except (OSError, ValueError, TypeError):
        pass
    return None


def _shade(im, top, bottom, strength=0.78, feather=280, foot=None, colour=(0, 0, 0)):
    """Neutral, smoothly feathered shade; never re-grade the photograph's hue. `foot` (y, strength, ease): from y
    down, the shade eases to that lighter strength over `ease` px and holds it to `bottom` (round 4: the badge's
    empty area under the deck shows the ambient field faintly, not a dead black band). `colour`: what the shade
    darkens towards; a shade that meets a plate ends in the plate's own value (lane B c4, _render)."""
    import numpy as np

    y = np.arange(im.height, dtype=np.float32)
    distance = np.maximum(np.maximum(top - y, y - bottom), 0)
    v = np.clip(1 - distance / feather, 0, 1)
    v = v * v * (3 - 2 * v)
    level = np.full_like(y, strength)
    if foot is not None:
        y0, light, ease = foot
        t = np.clip((y - y0) / max(1.0, ease), 0, 1)
        level = strength + (light - strength) * (t * t * (3 - 2 * t))
    alpha = Image.fromarray(np.clip(v * level * 255, 0, 255).astype("uint8")[:, None]).resize(im.size)
    layer = Image.new("RGBA", im.size, tuple(colour)[:3] + (255,))
    layer.putalpha(alpha)
    return Image.alpha_composite(im.convert("RGBA"), layer)


def _scrim(im, box, strength=0.55, feather=120):
    """An INK scrim confined to `box`, eased to nothing over `feather` px outside it (round 3: the monument's
    full-width shade turned the lower half of every tile to flat ink and dimmed the subject under the type).

    The falloff is the box blurred by a Gaussian (sigma feather / SCRIM_SIGMAS), not a linear ramp: a ramp's
    plateau and foot are kinks, and over a bright wall they read as a grey rectangle with a hard right edge
    (r5 review, 2026-11-02, Gol Gumbaz's interior). A Gaussian has no kink anywhere, so the edge is invisible;
    the box is first grown by SCRIM_GROW sigmas so the type keeps ~93% of `strength` at its own edge."""
    import numpy as np
    from scipy.ndimage import gaussian_filter
    w, h = im.size
    sigma = max(1.0, feather / SCRIM_SIGMAS)
    # freeze cycle 5 (stream PLATES): the one band-edge limit (band_edge.slope_limit, a floating shade: the photograph
    # goes on round it). A Gaussian edge's steepest point is strength x step / (sigma x sqrt(2 pi)); the step is the
    # bright end of the ground the falloff crosses, the limit that ground's own (flat or bright: a longer falloff)
    from . import band_edge
    lum = band_edge.luma_rows(im)
    xa, xb = max(0, int(box[0])), min(w, int(box[2]))
    ya, yb = max(0, int(box[1])), min(h, int(box[3]))
    need = 0.0
    for ground, a, b in ((lum[:, xa:xb], box[1] - feather, box[1]), (lum[:, xa:xb], box[3], box[3] + feather),
                         (lum[ya:yb].T, box[0] - feather, box[0]), (lum[ya:yb].T, box[2], box[2] + feather)):
        a, b = max(0.0, float(a)), min(float(ground.shape[0] - 1), float(b))   # each side's own falloff zone
        if b - a > 2 and ground.shape[1] > 2:
            limit = band_edge.slope_limit(ground, a, b, w, floating=True) * 1080 / max(1, w)
            need = max(need, band_edge.step_across(ground, a, b, brand.INK, strength) / (limit * 2.5066))
    # lander ruling 2026-11-26 (D1): the falloff never runs past its designed reach (the designed feather: no more of
    # the photograph darkened round the type than the designed scrim did); where the limit wants more (`need`) the
    # edge stays at the designed falloff, recorded by the corpus proof (plates_01: ramp at its designed reach)
    sigma = max(sigma, min(need, feather / SCRIM_SIGMAS))
    k = SCRIM_DOWNSAMPLE
    x0, y0, x1, y1 = (v / k for v in box)
    pad = SCRIM_GROW * sigma / k
    yy, xx = np.ogrid[:-(-h // k), :-(-w // k)]
    inside = ((xx + 0.5 >= x0 - pad) & (xx + 0.5 <= x1 + pad) & (yy + 0.5 >= y0 - pad)
              & (yy + 0.5 <= y1 + pad)).astype(np.float32)
    # mode="nearest": the canvas edge continues the mask, so a stack against the left edge stays shaded there.
    v = gaussian_filter(inside, sigma / k, mode="nearest", truncate=4.0)
    alpha = Image.fromarray(np.clip(v * strength * 255, 0, 255).astype("uint8")).resize(
        (w, h), Image.Resampling.BICUBIC)
    # PLATES-03: the scrim grades the photograph where it falls off (band_edge.graded: its hue and saturation kept, the
    # INK alpha's luma); round the type (the grown box, SCRIM_GROW sigmas: >= ~0.93 of the peak) it is the plain INK
    # alpha (rulings R2 / N2: the type's ground and its contrast unchanged)
    a = np.asarray(alpha, np.float32) / 255.0
    return band_edge.graded(im, a, brand.INK, full=0.9 * float(a.max())).convert("RGBA")


SCRIM_SIGMAS = 3.5      # _scrim: the feather spans this many sigmas past the box (~2% of strength left there)
SCRIM_DOWNSAMPLE = 4    # _scrim: the mask is solved at 1/4 size (a smooth field; bicubic up)
SCRIM_GROW = 1.5        # _scrim: the box grows by this many sigmas first, so the type keeps ~93% of strength


def cap_h_of(font):
    """A font's cap height (the top of "H" above its baseline), 0 for no font."""
    return -font.getbbox("H", anchor="ls")[1] if font is not None else 0


MONUMENT_SCRIM, MONUMENT_SCRIM_FEATHER = 0.6, 200    # round 3: the type stack's own scrim, not a full-width shade;
                                                     # r5: a Gaussian falloff over 200 px (no visible edge)
DIM_WORD_PX, DIM_WORD_TRACK = 38, 2.0                # the claim's dimension word under the monument's unit
QUALIFIER_GAP, QUALIFIER_TRACK = 24, 3.0             # the qualifier kicker's foot to the figure's cap line
QUALIFIER_WEIGHT = 800                               # brand.TEXT_FONT: the eyebrow's face (r5 review: in IBM Plex Mono the period took
                                                      # a full monospace cell and "EST." read as "EST .")
EYEBROW_QUALIFIER_GAP = 44                           # the eyebrow's ink foot to the qualifier's cap line (was ~20)
GRID_WIDTHS = (124, 180)                             # the profile grid's tile widths on phones (3:4 crop)
GRID_SCALE = tuple(w / 1080 for w in GRID_WIDTHS)
QUALIFIER_GRID_CAP = 9                               # px of cap height a qualifier keeps at 124 px wide
GRID_CONTRAST = GATES["contrast.grid"]   # a hook element's contrast on the 124 px tile (grid_proof), cycle 0 round 7
MASTHEAD_GRID_CAP = GATES["masthead.grid_cap"]       # QF-87: a cover's masthead is this tall at 124 px wide, or absent
EYEBROW_PX, EYEBROW_MIN, EYEBROW_TRACK = 40, 36, 4.0   # Montserrat 800 tracked capitals, every style (2026-11-02)
TAGLINE_PX, TAGLINE_MIN = 38, 32                        # never under 32 px: at 24-30 it vanished in the grid
TAGLINE_TRACK = 3.0     # round 4: the deck's tracked capitals (~+80/1000 at 38 px), like the eyebrow; it was the
                        # only untracked caps line in the system and clotted ("BUILT WITHOUT RINGS")
_STOP = {"THE", "A", "AN", "OF", "IN", "AND", "ON", "AT", "TO", "BY"}


def display(text):
    """Display copy as drawn: three full stops become one true ellipsis (U+2026), as a shortened line ends; then
    the shared display typography (round 4, multi_format.typeset: an en dash in a numeric span, curly quotes).
    Glyphs only: never a word or a figure changed."""
    import re

    from .multi_format import typeset
    return typeset(re.sub(r"\s*\.{3}", "\u2026", str(text or "")))


def _tagline_lines(measure, text, width, size, minimum=TAGLINE_MIN):
    """The deck on one line in Montserrat with TAGLINE_TRACK between letters: the room is reduced by the tracking,
    as the eyebrow's is (_eyebrow_lines). Returns (lines, font, shortened)."""
    room = width - TAGLINE_TRACK * max(0, len(text) - 1)
    return fit_text(measure, text, brand.TEXT_FONT, max(minimum, size), int(room), 1, minimum, sentences=False)


def redundant_tagline(copy):
    """True when every word of the tagline already stands in the headline or the eyebrow (a subline that
    repeats the place adds a line, not information, and is omitted)."""
    import re
    def words(t):
        return {w for w in re.findall(r"[A-Z0-9']+", str(t or "").upper()) if w not in _STOP}
    tag = words(copy.get("tagline"))
    return bool(tag) and tag <= words(" ".join(str(copy.get(k) or "") for k in ("line1", "line2", "eyebrow")))


_CAUSE = __import__("re").compile(
    r"\b(?P<verb>[A-Z]{3,}ED|MADE|BUILT|DUG|CUT|BORN|WORN|DRAWN|LEFT|SHAPED|WROUGHT|BLOWN|THROWN)\s+BY\s+"
    r"(?P<agent>[A-Z0-9' ]+)")


def _stem(word):
    """A content word's comparable stem: CARVED/CARVING/CARVES -> CARV, NATURAL -> NATUR (crude, on purpose: it
    only decides whether a reveal repeats a tagline's own cause)."""
    import re
    return re.sub(r"(?:ING|ED|ES|S|AL|E)$", "", word.upper())[:6] or word.upper()


def resolving_tagline(copy, reveal):
    """Why the tagline answers the mystery the cover should open (round 4), or "". Only when the script has a real
    reveal or takeaway line (`reveal`, the payload's closing line; r7: None or a blank line means none): the
    tagline states a cause or maker ("CARVED BY NATURE": a participle and "BY") that the reveal itself states (the
    maker's own word stands there), or every content word of it (two or more) stands in the reveal.
    A mystery hook the reveal does not answer ("GUARDED BY MONKS", "HAUNTED BY ...") is kept. The tagline is then
    dropped, never rewritten."""
    import re
    tag = str(copy.get("tagline") or "").upper()
    if not str(reveal or "").strip() or not tag.strip():
        return ""

    def words(t):
        return {w for w in re.findall(r"[A-Z0-9']+", str(t or "").upper()) if w not in _STOP and len(w) > 2}
    said = {_stem(w) for w in words(reveal)}
    cause = _CAUSE.search(tag)
    # The maker itself must stand in the reveal: a shared verb alone ("the gate is still guarded") is no answer.
    if cause and {_stem(w) for w in words(cause.group("agent"))} & said:
        return "the tagline states the cause the script reveals"
    tw = words(tag)
    if len(tw) >= 2 and tw <= words(reveal):
        return "the tagline repeats the script's reveal"
    return ""


def kicker_track(font):
    """Round 7 (F20): the eyebrow is the system kicker: tracked kicker.track em of its size."""
    return tok("kicker.track") * font.size


def kicker_lead(font):
    """(rule length, gap, thickness) of the system kicker's lead rule at `font` (kicker.* tokens, in its caps)."""
    ch = cap_h_of(font)
    return (max(8, round(ch * tok("kicker.rule"))), round(ch * tok("kicker.rule_gap")),
            max(3, round(ch * tok("kicker.rule_thick"))))


def _eyebrow_lines(measure, text, width, size=EYEBROW_PX):
    """The eyebrow (the system kicker, round 7 F20: its lead rule, then the words tracked kicker.track em) on one
    line in Montserrat 800, never under EYEBROW_MIN (refused there, QF-23: TextFitError). Returns (lines, font,
    shortened)."""
    f = get_font(brand.TEXT_FONT, max(EYEBROW_MIN, size), weight=800)
    rule, gap, _ = kicker_lead(f)
    room = width - rule - gap - kicker_track(f) * max(0, len(text) - 1)
    return fit_text(measure, text, brand.TEXT_FONT, max(EYEBROW_MIN, size), int(room), 1, EYEBROW_MIN,
                    sentences=False, weight=800)


def hedge_line(copy, figure):
    """(text, field) of the hedge a non-monument style sets on its own line above the headline line that carries
    the figure (round 4), or None. Only for a hedged figure (figure_check) whose qualifier is not in a title line
    (the badge "EST. 144 FT (44 M)", which these styles never draw, or an eyebrow or deck, which is texture at grid
    size: r7), in the same drawn form as the monument ("ESTIMATED"), so the styles never disagree on one figure.
    A flat-stated figure stays flat."""
    if not figure or must_withhold(figure) or not figure.get("hedged") or not figure.get("unit"):
        return None
    if not figure.get("qualifier") or figure.get("qualifier_from") in ("line1", "line2"):
        return None
    found = monument_figure(copy)
    if not found or found[2] not in ("line1", "line2") or found[0] != figure.get("number"):
        return None
    text = str(figure.get("drawn_qualifier") or figure.get("qualifier") or "").upper()
    return (text, found[2]) if text else None


def _rows(im, copy, width, style, tight, figure=None, lay_record=None):
    """Fit deliberate headline hierarchy, retaining all four supplied fields."""
    measure = ImageDraw.Draw(im)
    factor = 0.88 if tight else 1
    sizes = {
        "depth_sandwich": (110, 118),   # round 3: the window takes the room (still 9 px cap at 124 px)
        "badge": (108, 164),
        "minimal": (100, 142),
        "lower_third": (120, 138),
    }[style]
    paper = style == "depth_sandwich"
    # Two-tone everywhere it applies (2026-11-02): the second title line is gold on ink; ink on the paper card.
    # Round 7 (F21): one lockup on every style: the payoff line gold (its opening function words bone), its size a
    # fixed ratio of line 1's (lockup.line_ratio), never fitted per cover.
    specs = [
        ("eyebrow", "eyebrow", brand.TEXT_FONT, EYEBROW_PX, EYEBROW_MIN, brand.GOLD, 22),
        ("title_1", "line1", brand.DISPLAY_FONT, sizes[0], 64, brand.BONE, 10),
        ("title_2", "line2", brand.DISPLAY_FONT, sizes[0], 64, brand.GOLD, 26),
        ("tagline", "tagline", brand.TEXT_FONT, TAGLINE_PX, TAGLINE_MIN, brand.STONE_LT, 0),
    ]
    rows, shortened = [], []
    titles, lockup_rec = _lockup(measure, copy, width, int(sizes[0] * factor))
    hedge = hedge_line(copy, figure)
    for name, key, face, size, minimum, colour, gap in specs:
        text = display(copy.get(key)).strip().upper()
        if hedge and key == hedge[1] and text:
            # r7: at the qualifier kicker's grid floor (QUALIFIER_GRID_CAP at 124 px), never texture; figure_check
            # withholds the figure when it cannot be set so, and the last resort keeps the eyebrow size.
            fit = _qualifier_fit(hedge[0], width)
            lines, font, track = fit if fit else (_eyebrow_lines(measure, hedge[0], width, EYEBROW_PX)[:2]
                                                  + (EYEBROW_TRACK,))
            rows += [{"name": "qualifier", "text": line, "font": font, "fill": brand.GOLD, "track": track,
                      "height": text_size(measure, line, font)[1], "gap": HEDGE_GAP, "keep": bool(fit)}
                     for line in lines]
        if not text or (name == "tagline" and redundant_tagline(copy)):
            continue
        if name == "eyebrow":
            lines, font, short = _eyebrow_lines(measure, text, width)
            if short:
                shortened.append(name)
            rows += [{"name": name, "text": line, "font": font, "fill": colour, "track": kicker_track(font),
                      "height": text_size(measure, line, font)[1], "gap": gap, "kicker": True} for line in lines]
            continue
        if name == "tagline":
            lines, font, short = _tagline_lines(measure, text, width, max(minimum, int(size * factor)))
            if short:
                shortened.append(name)
            rows += [{"name": name, "text": line, "font": font, "fill": colour, "track": TAGLINE_TRACK,
                      "height": text_size(measure, line, font)[1], "gap": gap} for line in lines]
            continue
        got = titles.get(key)
        if not got:
            continue
        font, line_runs_ = got
        for i, line_runs in enumerate(line_runs_):
            line = " ".join(t for t, _ in line_runs)
            line_w = measure.textlength(line, font=font)
            dx = 0
            for j, (run, role) in enumerate(line_runs):
                rows.append({"name": name, "text": run, "font": font,
                             "fill": brand.GOLD if role in GOLD_ROLES else brand.BONE,
                             "height": text_size(measure, line, font)[1], "gap": gap if i == len(line_runs_) - 1 else 10,
                             "dx": dx, "same_line": j > 0, "line_w": line_w, "keep_size": True})
                dx += measure.textlength(run + " ", font=font)
    shortened += lockup_rec.get("off_ladder", [])
    if lay_record is not None:
        lay_record["lockup"] = lockup_rec
        lockup_line_faults(lay_record, measure, titles)
    if paper:
        for row in rows:
            row["fill"] = brand.INK if row["name"] not in ("tagline", "qualifier") else brand.INK2
    return rows, shortened


HEDGE_GAP = 16   # the hedge line's foot to the headline line it qualifies
LOCKUP_FALLBACK = 0.8   # the lockup's one recorded fallback tier (TYPE tall display_xl 150 -> 120)


def _lockup(measure, copy, width, size, single_gold=True):
    """Round 7 (F1, F21): the cover's title lockup set by the heading engine. Line 1 at the style's size, line 2 at
    lockup.line_ratio x that (one fixed ratio, never fitted per cover); the authored seam repaired (line 1's closing
    function words open line 2, bone); each field on one line, else the engine's break; the pair takes the one
    fallback tier together; only then the last resort (each line fitted, recorded off_ladder). One field alone is a
    lockup too: its payoff run (the engine's seam) is gold on one line or two. Returns ({"line1"/"line2": (font,
    runs per line)}, record)."""
    l1 = display(copy.get("line1")).strip().upper()
    l2 = display(copy.get("line2")).strip().upper()
    if l1 and l2:
        l1, l2, _ = lockup_fields(l1, l2)
    ratio = tok("lockup.line_ratio")
    # cycle 0 round 8 (operator ruling 7): lockup.line_ratio is a MINIMUM: line 2 (the payoff) grows past s1 x ratio,
    # up to the grid floor's size, until its cap clears the profile grid's floor at the 124 px tile (GRID_HOOK
    # title_2), where it still sets; recorded (line2_grown). The ratio's one value is never fitted per cover.
    from .brand_system import SPEC, px_for_cap
    from .heading import first_row_lone
    grid_px = px_for_cap(brand.DISPLAY_FONT, GRID_HOOK["title_2"] / GRID_SCALE[0])

    def width_at(t, sz):
        return measure.textlength(t, font=get_font(brand.DISPLAY_FONT, sz))

    def set_one(text, sz, gold):
        if not (l1 and l2):   # one field: a lockup of its own (its payoff gold unless the caller has one)
            form = set_heading(text, width_at, width, (sz,), lockup=single_gold)
            return form.runs() if form else None
        form = set_field(text, width_at, width, sz)
        return field_runs(text, form.cuts, gold) if form else None
    deferred = None
    for tier, scale in enumerate((1.0, LOCKUP_FALLBACK)):
        # round 8 (ruling 6 and the lander's floor ruling): line 1 too never steps under the grid floor's size (a
        # face of smaller cap: the second palette's Tunguska lockup drew 7.0 px at the tile)
        s1 = max(round(size * scale), grid_px)
        s2 = round(s1 * ratio)
        out, ok, grown = {}, True, None
        for key, text, sz, gold in (("line1", l1, s1, False), ("line2", l2, s2, True)):
            if not text:
                continue
            runs = set_one(text, sz, gold)
            if runs is None:
                ok = False
                break
            if key == "line2" and l1 and sz < grid_px:
                big = set_one(text, grid_px, gold)
                if big is not None:
                    grown, sz, runs = {"from": sz, "to": grid_px}, grid_px, big
                    s2 = grid_px
            out[key] = (get_font(brand.DISPLAY_FONT, sz), runs)
        if ok and l1 and l2 and not _stack_valid(measure, out, len(l1.split())):
            ok = False   # lane B cycle 1 (SYS-R8-2 open question 5): the stacked rows obey the heading rules too
        if ok and deferred is not None and deferred[1].get("first_row_lone"):
            # SYS-B-6 (freeze SYS batch 3): a held one-word first row gives way only to a form at the balance minimum
            # at its own size, never to a smaller size only to move a word
            ok = deferred[1]["sizes"] == [s1, s2] and _stack_balanced(measure, out)
        if ok:
            got = out, {"tier": tier, "sizes": [s1, s2], "ratio": ratio, "line1": l1, "line2": l2,
                        **({"line2_grown": grown} if grown else {})}
            bal = lone_balance(measure, out) if (l1 and l2) else None
            if bal is None:
                return got
            # lane B cycle 3 (B-S2-2: CARVED / FROM SOLID SALT at balance 0.43): a stacked one-word row under the
            # engine's balance minimum waits, as the heading engine's own staging has it (SYS-D-5: an unbalanced
            # break after both tiers' balanced forms and one-line forms)
            got[1]["balance_below_min"] = round(bal, 3)
            if deferred is None:
                rows, widths = _stack_rows(measure, out)
                if first_row_lone(rows, widths):
                    got[1]["first_row_lone"] = rows[0]
                deferred = got
    if deferred is not None:
        # the copy's words set as ONE heading (as on YouTube, SYS-R8-3: the same words, the payoff gold from the copy's
        # field seam), on the same two tiers at the grid floor: a one-line form or a balanced break wins over the
        # unbalanced stack; else the stack stands, recorded (balance_below_min). SYS-B-6: a held one-word first row
        # gives way only at its own size, to a form at the balance minimum.
        lone_first = bool(deferred[1].get("first_row_lone"))
        one = one_heading(width_at, l1, l2, width, [deferred[1]["sizes"][0]] if lone_first else sorted(
            {max(round(size * s), grid_px) for s in (1.0, LOCKUP_FALLBACK)}, reverse=True))
        if one is not None and lone_first:
            lines_ = list(one[0].lines)
            ws_ = [measure.textlength(t, font=one[1]) for t in lines_]
            if len(lines_) > 1 and min(ws_) / max(ws_) < SPEC["heading.balance_min"]:
                one = None
        if one is not None:
            form, font = one
            return {"line1": (font, form.runs())}, {
                "tier": form.tier, "sizes": [form.size, form.size], "ratio": ratio, "line1": l1, "line2": l2,
                "form": "one heading", "drawn_rows": list(form.lines),
                "form_diverges": {"ruled": [l1, l2], "rows": list(form.lines),
                                  "reason": "the ruled form stacks a one-word row under the heading engine's balance "
                                            f"minimum ({deferred[1]['balance_below_min']}); the headline is set as "
                                            "one heading"}}
        return deferred
    out, off = {}, []
    for key, text, gold in (("line1", l1, False), ("line2", l2, True)):
        if not text:
            continue
        lines, font, _ = fit_text(measure, text, brand.DISPLAY_FONT, round(size * LOCKUP_FALLBACK), width, 2,
                                      min(64, size), sentences=False)
        off.append("title_1" if key == "line1" else "title_2")
        out[key] = (font, [[(ln, "accent" if gold else "plain")] for ln in lines])
    return out, {"tier": None, "off_ladder": off, "ratio": ratio, "line1": l1, "line2": l2}


def _stack_valid(measure, out, seam):
    """Lane B cycle 1 (SYS-R8-2's open question 5, the Reel's part of R2): a two-field lockup's drawn rows, every field
    stacked, obey the heading rules (heading.lockup_valid: no one-word middle row, THE 1,200-TON / BEDROCK / MONSTER;
    the phrase exception on the last row only) and no row but the last ends on a never-end word (the field boundary
    included, as on YouTube). A lockup that fails takes the next tier, then the recorded last resort, which the
    grid proof refuses and the copy stage (cover_fit) sends to the copy edit."""
    from .heading import end_ok, lockup_valid
    rows, widths = [], []
    for key in ("line1", "line2"):
        if key in out:
            font, runs = out[key]
            for line_runs in runs:
                text = " ".join(t for t, _ in line_runs)
                rows.append(text)
                widths.append(measure.textlength(text, font=font))
    # a one-word first row under the balance minimum is not refused here: _lockup holds it (SYS-B-6, lone_balance)
    return (lockup_valid(rows, widths, seam, fields=True, first_lone_ok=True)
            and all(end_ok(r.split()[-1]) for r in rows[:-1] if r.split()))


def head_noun_shape(rows):
    """Operator ruling 4's shape (lander, 2026-11-19: THE SECRET / WEAPON, THE 6-TON / ROOM, THE BURIED / CITY): a
    three-word heading in two rows, an article and modifier row over its head noun alone. The first row opens on an
    article and ends on a word a line may end on; no figure is split from its unit; the last is one content word
    (never a function or never-end word). Such a row stands whatever its
    balance; the never-end and truth rules are unchanged."""
    from .brand_system import LINE_END_LAST_RESORT, LINE_END_NEVER
    from .heading import bare, end_ok, is_function
    from .figures import parse
    rows = [r for r in rows if r.strip()]
    if len(rows) != 2 or len(rows[0].split()) != 2 or len(rows[1].split()) != 1:
        return False
    if bare(rows[0].split()[0]).upper() not in _ARTICLES:   # an article opens the modifier row (THE SECRET / ...)
        return False
    text = f"{rows[0]} {rows[1]}"
    if any(f.start < len(rows[0]) < f.end for f in parse(text)):   # never a figure split from its unit
        return False
    last = bare(rows[1].split()[0])
    return (end_ok(rows[0].split()[-1]) and last.isalpha() and not is_function(rows[1].split()[0])
            and last not in LINE_END_NEVER and last not in LINE_END_LAST_RESORT)


def unbalanced_lone(rows, widths):
    """The stacked rows' balance (narrowest / widest drawn width) when a one-word row stands among two or more rows
    under heading.balance_min (ruling R8-4: the no-lone-row rule holds but for the balanced phrase exception), else
    None. Lane B cycle 3: one reading for the cover and the YouTube lockup."""
    rows = [r for r in rows if r.strip()]
    if len(rows) < 2 or not any(len(r.split()) == 1 for r in rows) or not widths or max(widths) <= 0:
        return None
    if head_noun_shape(rows):   # ruling 4's shape stands whatever its balance (lander, 2026-11-19)
        return None
    bal = min(widths) / max(widths)
    from .brand_system import SPEC
    return bal if bal < SPEC["heading.balance_min"] else None


def lockup_line_faults(record: dict, measure, out) -> None:
    """Freeze cycle 5 (TEXT stream, review D3; real covers: DRAWN WITHOUT / EVER FLYING): the line-break violations
    of the ONE policy that stay in the lockup's DRAWN rows (line_policy.drawn_record of _stack_rows), kept in the
    cover's record as "line_faults" (role "title"), the key still_layout.json keeps beside the cover. The lockup's
    own record (the engine's conjunction_end / locked_last_resort) lives in the layout's meta, which no run file
    holds. Each call replaces the title's rows of an earlier fit of the same layout: only the lockup last set is on
    the record; a clean lockup leaves none."""
    from . import line_policy
    rows, _widths = _stack_rows(measure, out)
    kept = [f for f in record.get("line_faults") or [] if f.get("role") != "title"]
    faults = line_policy.drawn_record(rows, role="title") if len(rows) > 1 else []
    if kept or faults:
        record["line_faults"] = kept + faults
    else:
        record.pop("line_faults", None)


def _stack_rows(measure, out):
    """A cover lockup's drawn rows and their drawn widths (_lockup's {"line1"/"line2": (font, runs)})."""
    rows, widths = [], []
    for key in ("line1", "line2"):
        if key in out:
            font, runs = out[key]
            for line_runs in runs:
                text = " ".join(t for t, _ in line_runs)
                rows.append(text)
                widths.append(measure.textlength(text, font=font))
    return rows, widths


def _stack_balanced(measure, out):
    """The drawn rows balance at heading.balance_min or better (one row: balanced)."""
    from .brand_system import SPEC
    _rows, widths = _stack_rows(measure, out)
    return len(widths) < 2 or (max(widths) > 0 and min(widths) / max(widths) >= SPEC["heading.balance_min"])


def lone_balance(measure, out):
    """unbalanced_lone for a cover lockup's drawn rows (_lockup's {"line1"/"line2": (font, runs)})."""
    return unbalanced_lone(*_stack_rows(measure, out))


def one_heading(width_at, l1, l2, width, sizes, max_lines=2):
    """(form, font) or None: the two fields' words set as ONE heading by the heading engine (set_heading, lockup, the
    payoff gold from the copy's field seam, the engine's staging over `sizes`: one line, a balanced break, the next
    tier, then an unbalanced break), kept only when it obeys the heading rules (heading.valid via set_heading) and
    stacks no one-word row under the balance minimum (unbalanced_lone). `width_at(text, size)`: the caller's
    (_lockup's) measure of a display line, so the lockup and its one-heading form are one size site."""
    from .heading import set_heading
    form = set_heading(f"{l1} {l2}", width_at, width, tuple(sizes), max_lines=max_lines, seam=len(l1.split()),
                       lockup=True)
    if form is None:
        return None
    if unbalanced_lone(list(form.lines), [width_at(t, form.size) for t in form.lines]) is not None:
        return None
    return form, get_font(brand.DISPLAY_FONT, form.size)


def _height(rows):
    return sum(r["height"] + r["gap"] for r in rows if not r.get("same_line"))


TAB_PAD = (24, 14)   # Discovery's eyebrow tab: horizontal, vertical padding
DIM_ROOM = 36        # Monument: the dimension line's room under the figure


def _top_anchor(xy, text, font, anchor, track):
    """(xy, anchor) that draws a tracked line where `anchor` ("lt"/"mt") would put it, on its baseline instead:
    tracked text is drawn a glyph at a time, and a per-glyph top anchor lifts a comma or a full stop to the cap
    line ("DERINKUYU, 1963" read "DERINKUYU' 1963"). Untracked lines are unchanged."""
    if not track or anchor[1] != "t":
        return xy, anchor
    return (xy[0], xy[1] - font.getbbox(text, anchor="ls")[1]), anchor[0] + "s"


def _paint(im, rows, x, top, centered, lay):
    plates = {}
    line_top = top
    for row in rows:
        next_top = top
        if row.get("same_line"):   # round 7: a run of the line above it (a lockup line set in two colours)
            top = line_top
        line_top = top
        if row.get("kicker"):
            # Round 7 (F20): the system kicker (the gold tab retired): a flat lead rule at mid-cap, then the words,
            # planned at the kicker gate (multi_format.plan_kicker: gold, else bone, else not drawn and recorded)
            from .multi_format import plan_kicker
            from .typography import tracked_width
            rule, gap, thick = kicker_lead(row["font"])
            w_txt = tracked_width(row["text"], row["font"], row.get("track", 0))
            x0 = x - (rule + gap + w_txt) / 2 if centered else x
            mid = top - row["font"].getbbox(row["text"], anchor="ls")[1] - cap_h_of(row["font"]) / 2
            xy, anchor = _top_anchor((x0 + rule + gap, top), row["text"], row["font"], "lt", row.get("track", 0))
            plate, rec = plan_kicker(im, xy, row["text"], row["font"], row["fill"], anchor, row.get("track", 0),
                                     rule=(round(x0), round(mid - thick / 2), round(x0) + rule - 1,
                                           round(mid - thick / 2) + thick - 1))
            if lay is not None:
                lay.meta.setdefault("kickers", {})[row["name"]] = rec
            if plate is not None:
                ImageDraw.Draw(im).rectangle([round(x0), round(mid - thick / 2), round(x0) + rule - 1,
                                              round(mid - thick / 2) + thick - 1], fill=tuple(plate.fill) + (255,))
                if lay is not None:
                    lay.meta.setdefault("kicker_rule", {})[row["name"]] = [round(x0), round(mid - thick / 2),
                                                                           round(x0) + rule, round(mid + thick / 2)]
                im = plate.draw(im)
                plates.setdefault(row["name"], []).append(plate)
        else:
            if "dx" in row:
                x0 = (x - row["line_w"] / 2 if centered else x) + row["dx"]
                xy, anchor = _top_anchor((x0, top), row["text"], row["font"], "lt", row.get("track", 0))
            else:
                xy, anchor = _top_anchor((x, top), row["text"], row["font"], "mt" if centered else "lt",
                                         row.get("track", 0))
            im, plate = place_text(im, xy, row["text"], row["font"], row["fill"], anchor=anchor, target=5.1,
                                   track=row.get("track", 0), halo_px=0 if row.get("tab") else None)
            plates.setdefault(row["name"], []).append(plate)
        top = next_top if row.get("same_line") else top + row["height"] + row["gap"]
    for name, ps in plates.items():
        record_contrast(lay, name, ps)
    if lay is not None:
        lay.meta.setdefault("plates", {}).update(plates)
    return im


# the masthead's words: brand.MASTHEAD (read when drawn; BRAND_TEXT below for its readers)
BRAND_PX = 64   # the masthead band's size (ruling 2: no masthead drawn; the covers keep its band)
BRAND_TOP = 462     # the masthead's ascender line, the same band in the grid's square on every style
MINIMAL_GAPS = (40, 22)   # minimal: masthead foot -> eyebrow caps, eyebrow foot -> title caps (ink to ink)


_BRAND_NAMES = {"BRAND_TEXT": "MASTHEAD", "QUALIFIER_FONT": "TEXT_FONT"}


def __getattr__(name: str):
    """A brand string or face this module bound at import before QF-35a: the active brand's (src/brand.py), now."""
    if name in _BRAND_NAMES:
        return getattr(brand, _BRAND_NAMES[name])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _brand(im, lay, centered=False, paper=False, left=None, centre=540, grid_delivery=False):
    """Operator ruling 2 (2026-11-16): a Reel cover (and its grid tile) carries NO masthead; the brand lives on the
    profile, carousel, YouTube and end card. Cycle 0 round 7 removed the drawing path (it was unreached since QF-87:
    every caller passes `grid_delivery`, where the 64 px masthead is 5.2 px at the 124 px tile). `im` comes back
    untouched and lay.meta["masthead"] says why. No photograph is cut back to clear a band for it (_immersive)."""
    font = get_font(brand.MASTHEAD_FONT, BRAND_PX, weight=700)
    if lay is not None:
        lay.meta["masthead"] = {"shown": False, "cap_124": round(cap_h_of(font) * GRID_SCALE[0], 1),
                                "reason": "operator ruling 2: no masthead on the Reel cover "
                                          f"(under {MASTHEAD_GRID_CAP} px cap height at profile-grid size)"}
    return im


COVER_GRID_TOP = 420        # the profile grid's 1:1 square starts here (PLATE_SQUARE): the photograph's view from there
COVER_INK_FADE = 120        # px over which a photograph that ends above the type fades into the INK field under it
COVER_INK_CLEAR = 10        # px of plain INK between that fade's end and the type's first row


COVER_GRID_FOCUS = 0.80     # of a reviewed feature inside the profile square (y from COVER_GRID_TOP), at least
COVER_TYPE_TOP = 1180       # where the type's stack usually starts (1434 less a three-row stack): the hero pick's
COVER_SHOWN_MIN = 0.5       # of an unreviewed photograph's salience a full-width crop shows unobscured, at least: a
                            # crop that leaves most of the photograph out of sight is not that photograph (the
                            # miniature of two ships cut to 9:16 showed parchment and a flame's edge); it stays whole


def cover_windows(top=COVER_TYPE_TOP):
    """The Reel cover's two full-width photo windows, in the ladder's order, as (mode, photo_fitness.Window): the whole
    canvas, then the canvas down to the type's first row (`top`: where the type's stack starts). The view of both
    starts at the profile grid's square (the tile shows nothing above it) and ends where the type or the fade
    begins."""
    from . import photo_fitness as pf
    # the photograph ends COVER_INK_CLEAR px above the type's first row: the fade into INK is complete before the
    # type's ground starts (20 px lower, the eyebrow's top rows stood on the fade's last 2 % and measured 9.94:1
    # where the INK field gives 10.55:1)
    foot = min(1920, int(top) - COVER_INK_CLEAR)
    # a reviewed feature lies whole above the type or the fade (`hold`: it is whole on the Reel cover and no type is
    # set on it), and COVER_GRID_FOCUS of it inside the profile square (the grid tile may trim its top, never more)
    # an unreviewed photograph: half its salience in the Reel's view, half of that in the profile tile's, and its
    # central half in the crop (lander ruling on the second review); else it stands contained on INK
    return (("full_bleed", pf.Window((1080, 1920), view=(0, COVER_GRID_TOP, 1080, top - 40),
                                     hold=(0, 0, 1080, top - 40), focus_min=COVER_GRID_FOCUS,
                                     hold_min=pf.SUBJECT_CUT_MAX,
                                     shown_min=COVER_SHOWN_MIN, tile=(0, COVER_GRID_TOP, 1080, top - 40),
                                     centre_half=True, name="reel cover full_bleed")),
            ("photo_over_ink", pf.Window((1080, foot), view=(0, COVER_GRID_TOP, 1080, foot - COVER_INK_FADE),
                                         hold=(0, 0, 1080, foot - COVER_INK_FADE), focus_min=COVER_GRID_FOCUS,
                                         hold_min=pf.SUBJECT_CUT_MAX,
                                         shown_min=COVER_SHOWN_MIN, tile=(0, COVER_GRID_TOP, 1080, foot),
                                         centre_half=True, name="reel cover photo_over_ink")))


def _filled(source, focus, top, lay):
    """The Reel cover's photograph at full width, by the photo-fitness ladder (src/photo_fitness.py; freeze cycle 5,
    PHOTO-01: the Richat cover's photograph stopped short of the frame's top and the rest was its own edge, extended
    and blurred): (canvas, mode), or None when no crop of the photograph is fit.

    1. "full_bleed": the photograph fills the whole 1080x1920 canvas (its subject in view between the profile
       square's top and the type); the caller shades the type's band on the photograph itself.
    2. "photo_over_ink": it fills the canvas from the top to the type's first row and fades there into the INK field
       the type sits on (the carousel's photo-over-ink split: one system), over COVER_INK_FADE px by smoothstep.
    Each is a real crop of the photograph inside its own content, within photo_fitness.UPSCALE_MAX; a reviewed
    feature (`focus`) is whole in view (photo_fitness.FOCUS_KEEP), else the crop keeps what the photograph offers
    (SUBJECT_KEEP, DENSITY_MIN, FLAT_MAX). Neither fit: None, recorded in lay.meta["photo_fit"], and the cover takes
    the contained window on INK (_photograph). Never a pad, a stretched row or a blurred field."""
    import math

    from . import photo_fitness as pf
    if source.info.get("essential_full_image"):
        return None
    src = ImageOps.exif_transpose(source).convert("RGB")
    sw, sh = src.size
    fbox = None
    if focus:
        fx0, fy0, fx1, fy1 = focus["focus_box"]
        fbox = (fx0 * sw, fy0 * sh, fx1 * sw, fy1 * sh)
    if fbox is None and sh > sw:
        # An unreviewed PORTRAIT photograph stays whole (review of cycle 5, B2): a full-width crop of it keeps a
        # band of its height, and nothing says which band holds the subject (the Memnon statue's head and chest
        # fell above the profile tile; a pillar's capital and a temple's tower were cut). Until a reviewed subject
        # box exists for it, the cover takes the contained window on INK: the photograph once, whole.
        if lay is not None:
            lay.meta["photo_fit"] = {"fit": False, "tried": [], "layout": "contain",
                                     "reason": "the Reel cover's full-width photograph: an unreviewed portrait "
                                               "photograph is shown whole (no subject box to crop it by; rule: "
                                               "a photograph is fit for the window it is put in, "
                                               "src/photo_fitness.py)"}
        return None
    analysis = pf.analyse(src, fbox)
    foot = min(1920, int(top) - COVER_INK_CLEAR)
    tried = []
    for mode, window in cover_windows(top):
        size = window.size
        rec = pf.place(None, window, image=src, analysis=analysis)
        tried.append({"mode": mode, **{k: v for k, v in rec.items() if k != "window"}})
        if rec["fit"]:
            break
    else:
        if lay is not None:
            lay.meta["photo_fit"] = {"fit": False, "tried": tried, "layout": "contain",
                                     "reason": pf.reason(tried[-1], "the Reel cover's full-width photograph")}
        return None
    im = Image.new("RGBA", (1080, 1920), brand.INK + (255,))
    im.paste(pf.crop(src, rec["box"], size).convert("RGBA"), (0, 0))
    if mode == "photo_over_ink":
        im = _shade(im, foot, 1920, strength=1.0, feather=COVER_INK_FADE, colour=brand.INK)
    bx0, by0, bx1, by1 = rec["box"]
    k = size[0] / (bx1 - bx0)
    if lay is not None:
        lay.meta["photo_fit"] = {"fit": True, "layout": mode, "tried": tried}
        comp = {"version": DESIGN_VERSION, "mode": mode, "source_size": [sw, sh],
                "source_box": [round(v, 1) for v in rec["box"]], "image_box": [0, 0, size[0], size[1]],
                "upscale": rec["upscale"], "source_coverage": 1.0, "full_width": True}
        if fbox is not None:
            protected = [max(0, math.floor((fbox[0] - bx0) * k)), max(0, math.floor((fbox[1] - by0) * k)),
                         min(size[0], math.ceil((fbox[2] - bx0) * k)), min(size[1], math.ceil((fbox[3] - by0) * k))]
            lay.protected.append({"name": "reviewed photographic feature", "box": protected})
            comp.update({"focus_box": focus["focus_box"], "protected_box": protected,
                         "reviewed": focus.get("reviewed", ""), "source_sha256": focus.get("sha256", "")})
        lay.meta["composition"] = comp
    return im, mode


BADGE_FOOT = (40, 0.6, 220)   # Discovery: 40 px under the deck the shade eases from 0.84 to 0.6 over 220 px


def _photograph(im, source, aperture, lay, bottom=False):
    """Contain the complete photo, with neither distortion nor an invented crop.

    The source may be landscape, portrait or square. Its exact pasted rectangle
    is protected and retained in the redraw recipe. This is a geometric promise,
    not a claim to have detected or classified a monument or person.
    """
    x0, y0, x1, y1 = map(int, aperture)
    source = ImageOps.exif_transpose(source).convert("RGBA")
    if source.info.get("essential_full_image"):
        photo = source.copy()
        photo.thumbnail((x1 - x0, y1 - y0), Image.Resampling.LANCZOS)
    else:
        photo = ImageOps.contain(source, (x1 - x0, y1 - y0), Image.Resampling.LANCZOS)
    x = x0 + (x1 - x0 - photo.width) // 2
    # Bottom-aligned on the paper card (round 3), so the window's foot keeps its set gap to the kicker.
    y = y1 - photo.height if bottom else y0 + (y1 - y0 - photo.height) // 2
    im.alpha_composite(photo, (x, y))
    box = [x, y, x + photo.width, y + photo.height]
    if lay is not None:
        lay.protected.append({"name": "photograph", "box": box})
        lay.meta["composition"] = {
            "version": DESIGN_VERSION, "mode": "contain", "source_size": list(source.size),
            "source_box": [0, 0, source.width, source.height], "image_box": box,
            "aperture": [x0, y0, x1, y1],
        }
        if lay.meta.get("photo_fit") is None:
            # every cover style records its photograph's fit (second review, D8: lower_third, minimal and the
            # sandwich recorded nothing): contained whole, the photograph's own frame shows when it has one
            from . import photo_fitness as pf
            share = pf.analyse(source.convert("RGB")).matte_share
            problems = ["matte"] if share > pf.MATTE_MAX else []
            why = (pf.reason({"problems": problems}, "the cover's contained photograph") if problems
                   else "the photograph stands whole in its window (no crop)")
            lay.meta["photo_fit"] = {"fit": not problems, "layout": "contain", "tried": [], "matte": round(share, 4),
                                     "problems": problems, "reason": why}
    return im


_YEAR = __import__("re").compile(r"(?<![\w.])(1[0-9]{3}|20[0-9]{2})(?![\w])")


def monument_figure(copy):
    """(number, unit, field, span) of the figure a "monument" cover makes its hero, or None.

    The first measured figure in the title lines, the badge or the tagline ("THE 144-FOOT" -> ("144", "FT");
    "THE 25-MILE" -> ("25", "MILES")), else a year in the tagline or eyebrow ("DERINKUYU, 1963" -> ("1963", "")).
    `span` is the figure's place in that field, so the line it came from can be shown without repeating it.
    """
    from .typography import _FIGURE, _figure_ok, _unit
    for field in ("line1", "line2", "badge", "tagline"):
        text = str(copy.get(field) or "")
        for m in _FIGURE.finditer(text):
            info = _unit(m.group("unit"))
            if info and _figure_ok(m):
                return m.group("num") + m.group("plus"), info[3].upper(), field, m.span()
    for field in ("tagline", "eyebrow"):
        m = _YEAR.search(str(copy.get(field) or ""))
        if m:
            return m.group(1), "", field, m.span()
    return None


# Round 3 (2026-11-02): a hero figure carries its hedge, says what it measures, and never turns a disputed or
# ranged figure into one bare number (operator rule: figures are never more precise than the fact sheet).
QUALIFIERS = ("UP TO", "MORE THAN", "AT LEAST", "ABOUT", "ALMOST", "NEARLY", "OVER", "SOME", "AROUND", "ROUGHLY",
              "APPROX.", "APPROXIMATELY", "CIRCA", "EST.", "C.")
_QUAL = "|".join(__import__("re").escape(q) for q in sorted(QUALIFIERS, key=len, reverse=True))
_DIMENSIONS = ("diameter", "across", "wide", "width", "deep", "depth", "tall", "high", "height", "long", "length",
               "thick", "circumference")
_DIM_MODIFIERS = ("external", "internal", "outer", "inner")
_HEDGE_WORDS = ("approximately", "about", "roughly", "almost", "nearly", "some", "up to", "more than", "over",
                "at least", "around", "estimated", "an estimated", "circa")


def figure_qualifier(copy, found):
    """(qualifier, field) for the hero figure `found` (monument_figure's tuple): the qualifier the copy itself
    sets before that number, in the figure's own field first ("UP TO 144-FOOT"), else in another field carrying
    the same number (the badge "EST. 144 FT (44 M)"). ("", "") when the copy states it flat."""
    import re
    if not found:
        return "", ""
    number, field = found[0], found[2]
    pattern = re.compile(r"(?<![\w.])(" + _QUAL + r")\s*" + re.escape(number) + r"(?![\d])", re.IGNORECASE)
    for key in (field, "badge", "line1", "line2", "tagline", "eyebrow"):
        m = pattern.search(str(copy.get(key) or ""))
        if m:
            q = m.group(1).upper()
            return (q if q.endswith(".") or q not in ("EST", "C", "APPROX") else q + "."), key
    return "", ""


# Round 4 (2026-11-02): "EST." on a dimension read as "established", turning a size claim into a founding date
# (eight lenses). An abbreviated hedge on a measured figure is drawn in full, in the sheet's or the copy's own
# words: the claim's hedge word before the figure ("approximately"), else the sheet's hedge lead as one word
# ("by one estimate" -> "ESTIMATED", HEDGE_LEAD_WORDS), else the copy's own word spelled out ("EST." ->
# "ESTIMATED"). "EST." and "C." stay on dates.
ABBREVIATED = {"EST.": "ESTIMATED", "APPROX.": "APPROXIMATELY"}
# r7 (operator default, 2026-11-02): a single-estimate measurement draws its hedge as the one word ESTIMATED, faithful
# to the sheet's "by one estimate"; the three-word phrase could not hold the grid floor on one line, so the
# monument never rendered and the fallback set the hedge as unreadable texture.
HEDGE_LEAD_WORDS = (("estimat", "ESTIMATED"), ("approximat", "APPROXIMATELY"))


def drawn_qualifier(qualifier, unit, lead_word="", hedge=""):
    """The qualifier as a cover draws it: `qualifier` (the copy's, figure_qualifier) unchanged unless it is an
    abbreviation on a measured figure (`unit`), then the claim's own hedge word (`lead_word`), else the sheet's
    hedge lead as its one word (`hedge`: "by one estimate, the central dome ..." -> "ESTIMATED"), else the
    abbreviation spelled out. Never a word that is in neither the copy nor the sheet."""
    import re
    q = str(qualifier or "").upper()
    if not q or not unit or q not in ABBREVIATED:
        return q
    if lead_word:
        return str(lead_word).upper()
    m = re.match(r"\s*([A-Za-z][A-Za-z ]{1,40}?)\s*,\s", str(hedge or ""))
    if m and len(m.group(1).split()) <= 4:
        for stem, word in HEDGE_LEAD_WORDS:
            if stem in m.group(1).lower():
                return word
    return ABBREVIATED[q]


# Lane B cycle 3 (S1, AD r1 + stress FIGURE_GATE): the hero figure is the copy's statement of it WHOLE. monument_figure
# reads one number and its unit, so on "ROUGHLY 137–138 FT" it took "138 FT": one end of the span, its hedge left
# behind (the YouTube tile drew "138 FT" for the signed "Roughly 137-138 ft"). The statement is read by the system's
# one quantity parser (src/figures.parse, read only), and the hero carries its hedge and both ends of its span.
_COMPARATORS = "<>~≈≤≥"
_UNREAD_HEDGE = __import__("re").compile(
    r"(?:(?:(?<![\w.])(?:" + _QUAL + r"|ESTIMATED|AN ESTIMATED)\s*)?[" + _COMPARATORS + r"]\s*"
    r"|(?<![\w.])(?:" + _QUAL + r"|ESTIMATED|AN ESTIMATED)\s*)$", __import__("re").IGNORECASE)


def stated_figure(copy, found):
    """(figure, unread): the hero figure `found` (monument_figure's tuple, a measured one) as its field states it,
    whole: the src/figures.Figure whose characters hold the found number (its hedge, both ends of a span, its
    conversion; `start` / `end` its place in the field), else None. `unread`: a hedge the parser did not take in front
    of it ("EST. >" before "13,000 LBS", "~" before a number), which no figure line can draw as signed; "" for none.
    Every niche's copy is read the same way: nothing here names a topic."""
    from .figures import parse
    if not found or not found[1]:
        return None, ""
    text = str(copy.get(found[2]) or "")
    at = found[3][0]
    whole = next((f for f in parse(text) if f.start <= at < max(f.end, f.start + 1)), None)
    if whole is None:
        return None, ""
    unread = ""
    if not whole.hedge:
        m = _UNREAD_HEDGE.search(text[:whole.start])
        if m and m.group(0).strip():
            unread = m.group(0).strip()
    return whole, unread


def signed_copy_texts(copy):
    """The cover copy's signed fields, as the texts a drawn figure is checked against (src/figures.truth_faults)."""
    return [str(copy.get(k) or "") for k in ("eyebrow", "line1", "line2", "tagline", "badge") if copy.get(k)]


def hero_truth_faults(qualifier, number, unit, copy):
    """[] when the hero figure as drawn (its qualifier line, number and unit read as one statement) is a signed
    statement of the copy, whole (src/figures.truth_faults: a signed span drawn as its span, a hedged figure with its
    hedge); else the faults. Lane B cycle 3: the guard every figure hero passes before it is drawn."""
    from .figures import truth_faults
    drawn = " ".join(x for x in (str(qualifier or ""), str(number or ""), str(unit or "")) if x)
    return truth_faults(drawn, signed_copy_texts(copy))


_EVENT_VERB = r"([a-z]+ed|built|found|begun|dug|made|cut|laid|won|lost|struck|hit|seen|known|drawn|written)"


def year_event(year, sheet):
    """(word, fact_id): the fact sheet's own event word for `year` ("accidentally rediscovered in 1963" ->
    "REDISCOVERED"), the verb directly before "in/around/by/during <year>" in a claim, or ("", "")."""
    import re
    if not isinstance(sheet, dict):
        return "", ""
    pattern = re.compile(r"\b" + _EVENT_VERB + r"\s+(?:in|around|by|during)\s+(?:the\s+year\s+)?"
                         + re.escape(str(year)) + r"\b", re.IGNORECASE)
    for fact in sheet.get("facts") or []:
        if isinstance(fact, dict):
            m = pattern.search(str(fact.get("statement") or ""))
            if m:
                return m.group(1).upper(), str(fact.get("id") or "")
    return "", ""


def _numbers(text):
    import re
    return {float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", str(text or ""))}


def _dimension_near(text, start, end):
    """The dimension word the claim itself uses nearest to the figure at text[start:end] ("EXTERNAL DIAMETER"),
    from the same clause, or ""."""
    import re
    best = None
    for m in re.finditer(r"\b(" + "|".join(_DIMENSIONS) + r")\b", text, re.IGNORECASE):
        gap = start - m.end() if m.end() <= start else m.start() - end
        clause = text[min(m.end(), start):max(m.start(), end)]
        if gap > 60 or re.search(r"[;.]\s|\band\b", clause):
            continue
        if best is None or gap < best[0]:
            best = (gap, m)
    if best is None:
        return ""
    m = best[1]
    word = m.group(1).upper()
    before = re.search(r"\b(" + "|".join(_DIM_MODIFIERS) + r")\s+$", text[:m.start()], re.IGNORECASE)
    return f"{before.group(1).upper()} {word}" if before else word


def figure_check(copy, sheet=None, payload=None):
    """What the fact sheet says about the copy's hero figure, or None (no measured figure, or no sheet to ask).

    {"number", "unit", "field", "qualifier", "qualifier_from", "fact_id", "dimension", "hedged", "conflict",
    "monument_ok", "reason"}. `conflict` is a figure the sheet gives as a range or marks disputed with another
    value in its hedge (Richat: 40 km, "almost 50 km" from orbit): it is never drawn as one number. `hedged` is
    an estimate (disputed with the same value, or "approximately" in the claim): a hero shows it only with the
    copy's own qualifier. `dimension` is only ever the claim's own word (Gol Gumbaz: "EXTERNAL DIAMETER"); the
    payload's matching stat card label is the fallback source. Nothing here invents a word.
    """
    import re

    from .typography import _FIGURE, _figure_ok, _unit
    found = monument_figure(copy)
    if not found or not isinstance(sheet, dict):
        return None
    if not found[1]:
        # A year hero (round 4): it says what happened in it only with the sheet's own event word, set above it.
        event, fact_id = year_event(found[0], sheet)
        return {"number": found[0], "unit": "", "field": found[2], "kind": "year", "event": event,
                "fact_id": fact_id, "qualifier": "", "qualifier_from": "", "drawn_qualifier": event,
                "dimension": "", "hedged": False, "conflict": False, "monument_ok": False,
                "reason": "" if event else "the fact sheet gives no event word for this year"}
    number, unit, field = found[0], found[1], found[2]
    value = float(number.rstrip("+").replace(",", ""))
    qualifier, q_from = figure_qualifier(copy, found)
    out = {"number": number, "unit": unit, "field": field, "qualifier": qualifier, "qualifier_from": q_from,
           "drawn_qualifier": drawn_qualifier(qualifier, unit), "fact_id": "", "dimension": "", "hedged": False,
           "conflict": False, "monument_ok": False, "reason": ""}
    match = None
    for fact in sheet.get("facts") or []:
        if not isinstance(fact, dict):
            continue
        statement = str(fact.get("statement") or "")
        for m in _FIGURE.finditer(statement):
            info = _unit(m.group("unit"))
            if (info and _figure_ok(m) and info[3].upper() == unit
                    and abs(float(m.group("num").replace(",", "")) - value) < 1e-9):
                match = (fact, statement, m)
                break
        if match:
            break
    if match is None:
        out["reason"] = "the figure is not in the fact sheet in this unit"
        return out
    fact, statement, m = match
    out["fact_id"] = str(fact.get("id") or "")
    # A range in the claim ("8 to 9 kilometres", "94-104"), or a hedge giving another value, is a conflict.
    ranged = (re.search(r"\d[\d,.]*\s*(?:to|-|–|and)\s*$", statement[:m.start()])
              or re.match(r"\s*(?:to|-|–)\s*\d", statement[m.end():]))
    other = _numbers(fact.get("hedge")) - _numbers(statement)
    out["conflict"] = bool(ranged) or bool(fact.get("disputed") and other)
    lead = statement[max(0, m.start() - 28):m.start()].lower()
    lead_word = next((w for w in sorted(_HEDGE_WORDS, key=len, reverse=True)
                      if re.search(r"\b" + re.escape(w) + r"\s+$", lead)), "")
    out["hedged"] = bool(fact.get("disputed")) or bool(lead_word)
    out["drawn_qualifier"] = drawn_qualifier(qualifier, unit, lead_word, fact.get("hedge"))
    out["dimension"] = _dimension_near(statement, m.start(), m.end())
    if not out["dimension"]:
        for card in (payload or {}).get("stat_cards") or []:
            if isinstance(card, dict) and value in _numbers(card.get("value")):
                label = str(card.get("label") or "")
                hit = re.search(r"\b(" + "|".join(_DIMENSIONS) + r")\b", label, re.IGNORECASE)
                if hit:
                    out["dimension"] = _dimension_near(label, hit.start(), hit.start())
                    break
    # r7: the hedge is never texture. Where it is not in a title line, every style sets it at the grid floor
    # (_qualifier_fit, on the narrowest measure, the paper card's); a hedge that cannot be set so withholds the
    # figure on every style (withhold_figure: a figure-free headline), as no qualifier at all does.
    in_title = q_from in ("line1", "line2")
    out["hedge_fits"] = bool(qualifier) and (in_title or _qualifier_fit(out["drawn_qualifier"], HEDGE_MEASURE)
                                             is not None)
    if out["conflict"]:
        out["reason"] = "the sheet gives this figure as a range or disputes its value"
    elif out["hedged"] and not qualifier:
        out["reason"] = "the sheet hedges this figure and the copy carries no qualifier for it"
    elif out["hedged"] and not out["hedge_fits"]:
        out["reason"] = (f"the hedge {out['drawn_qualifier']!r} does not fit one line at profile-grid size "
                         "(the figure is withheld)")
    elif out["drawn_qualifier"] and not in_title and _qualifier_fit(out["drawn_qualifier"]) is None:
        out["reason"] = f"the hedge {out['drawn_qualifier']!r} does not fit one kicker line at profile-grid size"
    elif not out["dimension"]:
        out["reason"] = "the claim does not say what the figure measures"
    else:
        out["monument_ok"] = True
    return out


def must_withhold(figure):
    """True when the hero figure may be shown nowhere (figure_check): a range or disputed value (`conflict`), or
    an estimate whose hedge cannot be shown legibly at grid size (no qualifier, or one that does not fit one line
    at the floor: r7, operator default). A year and an unhedged figure stand."""
    if not figure or figure.get("kind") == "year":
        return False
    if figure.get("conflict"):
        return True
    return bool(figure.get("unit") and figure.get("hedged") and figure.get("fact_id")
                and not figure.get("hedge_fits", True))


def withhold_figure(copy, figure, force=False):
    """The copy with a conflicted figure (figure_check: a range or a disputed value), or an estimate whose hedge
    cannot be shown legibly (must_withhold; `force`: the caller's own floor failed), lifted out of every line
    that states it, with the qualifier in front of it: the cover falls back to a non-numeric headline rather than
    one number the sheet does not give (operator default, round 3). Only payload words remain; none is added."""
    import re

    from .typography import _FIGURE, _figure_ok, _unit
    out = dict(copy)
    if not figure or not figure.get("unit") or not (force or must_withhold(figure)):
        return out
    value = float(str(figure["number"]).rstrip("+").replace(",", ""))
    for key in ("eyebrow", "line1", "line2", "tagline", "badge"):
        text = str(out.get(key) or "")
        cut = False
        # QF-142: the figure in its other forms first, whole (a plain cut inside one would leave its other end): a
        # span or range that has it at either end ("25 TO ALMOST 31 MI", "144-150 FT", "BETWEEN 130 AND 144 FT"),
        # with the hedge it carries, and spelled out ("TWENTY-FIVE MILES"); src/figures.parse reads every stated
        # quantity whole (system rule, read only), the spelled-out numbers are matched here with the unit after them
        for a, b in reversed(_other_forms(text, value)):
            text = _without(text, (a, b))
            cut = True
        for m in reversed(list(_FIGURE.finditer(text))):
            info = _unit(m.group("unit"))
            if info and _figure_ok(m) and abs(float(m.group("num").replace(",", "")) - value) < 1e-9:
                start = m.start()
                q = re.search(r"(?<![\w.])(" + _QUAL + r")\s*$", text[:start], re.IGNORECASE)
                stop = m.end()
                conv = re.match(r"\s*\(([^()]*)\)", text[stop:])
                if conv and any(_unit(c.group("unit")) and _figure_ok(c) for c in _FIGURE.finditer(conv.group(1))):
                    stop += conv.end()        # QF-141: "144 FT (44 M)": the figure in another unit goes with it
                text = _without(text, (q.start() if q else start, stop))
                cut = True
        # QF-141: what the figure leaves of its phrase is drawn only when it still says something on its own
        # ("327 METERS DOWN" left "DOWN" on the cover); a fragment is not drawn (no words are added).
        out[key] = "" if cut and withheld_fragment(text) else text
    return out


def _spelled_value(words):
    """The value of a spelled-out number (["TWENTY", "FIVE"], ["ONE", "HUNDRED", "FORTY", "FOUR"]), or None."""
    from .figures import NUMBER_WORDS, SCALE_VALUE
    total, cur, seen = 0.0, 0.0, False
    for w in words:
        lw = w.lower()
        if lw in NUMBER_WORDS:
            cur += NUMBER_WORDS[lw]
            seen = True
        elif lw == "hundred" and seen:
            cur *= 100
        elif lw in SCALE_VALUE and seen:
            total += cur * SCALE_VALUE[lw]
            cur = 0.0
        elif lw == "and" and seen:
            continue
        else:
            return None
    return total + cur if seen else None


def _other_forms(text, value):
    """QF-142: the character spans of `value` in `text` beyond the plain figure withhold_figure cuts: every stated
    quantity src/figures.parse reads whole (its hedge to its unit or conversion) that is a span or range with `value`
    at either end, and spelled-out numbers equal to `value` followed by a unit. Sorted, not overlapping."""
    import re

    from .figures import NUMBER_WORDS, parse
    from .typography import _unit
    spans = []
    for f in parse(text):
        if f.hi and (f.unit or f.measured) and any(abs(v - value) < 1e-9 for v in f.values):
            spans.append((f.start, f.end))
    names = sorted((w.upper() for w in NUMBER_WORDS), key=len, reverse=True) + ["HUNDRED", "THOUSAND", "MILLION", "AND"]
    alt = "|".join(re.escape(w) for w in names)
    hedge = r"(?:(?:" + _QUAL + r")\s+)?"
    for m in re.finditer(r"(?<![\w-])" + hedge + r"((?:" + alt + r")(?:[\s-]+(?:" + alt + r"))*)\s+([A-Z][A-Z.]*)",
                         text, re.IGNORECASE):
        words = [w for w in re.split(r"[\s-]+", m.group(1)) if w]
        while words and words[-1].upper() == "AND":
            words.pop()
        if _unit(m.group(2)) and _spelled_value(words) is not None and abs(_spelled_value(words) - value) < 1e-9:
            spans.append((m.start(), m.end()))
    spans.sort()
    out = []
    for a, b in spans:
        if out and a < out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


# QF-141: the words a field the figure was lifted out of cannot stand on alone. Articles, prepositions and
# conjunctions (they governed or joined the figure), and the extent, direction and hedge words a measure carries
# ("DOWN", "ACROSS", "DEEP", "ESTIMATED"): with the number gone they say nothing. Units are found by _unit.
_ARTICLES = frozenset({"THE", "A", "AN", "ITS", "THEIR"})
# Words that join something before them to what follows: a remainder that opens on one lost what it joined ("OF
# SOLID SALT", "THAN ANY CATHEDRAL", "THAT CHANGED HISTORY"). A locative preposition opening a remainder governs what
# follows it ("BELOW KRAKOW", "ACROSS THE SAHARA") and stands. The README of QF-141 tables every word and its class.
_JOINERS = frozenset({"OF", "THAN", "AND", "OR", "BUT", "NOR", "AS", "THAT", "WHICH", "WHO", "WHOM", "WHOSE",
                      "IS", "ARE", "WAS", "WERE", "BE", "BEEN"})
# Prepositions that need an object after them (none is used as an adverb on its own in a headline): a remainder that
# ends on one lost it ("DEEPER THAN", "CARVED FROM", "CARVED BY", "BURIED IN"). IN is here although it can be an
# adverb ("CAVED IN"): a cover line that ends on it after a cut has lost its object far more often (QF-141 review 2).
_OBJECT_PREPOSITIONS = frozenset({
    "OF", "TO", "FROM", "WITH", "AT", "BY", "FOR", "IN", "INTO", "ONTO", "UPON", "PER", "VIA", "AMONG", "AMONGST",
    "BETWEEN", "AMID", "AMIDST", "ATOP", "DURING", "DESPITE", "LIKE", "UNLIKE", "EXCEPT", "AGAINST", "TOWARD",
    "TOWARDS", "UNTIL", "TILL", "THAN", "UNTO", "VERSUS", "VS", "CONCERNING", "REGARDING", "INCLUDING",
})
# Locative or adverbial prepositions: each also stands alone as an adverb ("HIDDEN BELOW", "SEALED INSIDE"), so it
# stands at either end of a remainder.
_LOCATIVES = frozenset({
    "ON", "OVER", "UNDER", "BELOW", "ABOVE", "BENEATH", "UNDERNEATH", "ACROSS", "THROUGH", "AROUND", "BEYOND",
    "WITHIN", "WITHOUT", "ALONG", "PAST", "NEAR", "BEHIND", "INSIDE", "OUTSIDE", "BEFORE", "AFTER", "SINCE",
    "OPPOSITE", "ALONGSIDE", "ABOARD", "UP", "DOWN", "OFF", "OUT", "AWAY", "THROUGHOUT", "UNDERGROUND", "AHEAD",
})
# A remainder ending on one of these lost the word it needed after it.
_NEEDS_OBJECT = _ARTICLES | _JOINERS | _OBJECT_PREPOSITIONS
_PREPOSITIONS = _OBJECT_PREPOSITIONS | _LOCATIVES
_MEASURE_WORDS = frozenset({
    "UP", "DOWN", "OFF", "OUT", "AWAY", "DEEP", "DEEPER", "HIGH", "HIGHER", "TALL", "TALLER", "WIDE", "WIDER",
    "LONG", "LONGER", "OLD", "OLDER", "THICK", "AROUND", "ACROSS", "BELOW", "ABOVE", "BENEATH", "UNDERGROUND",
    "UNDERWATER", "AHEAD", "BEHIND", "AGO", "LATER", "EARLIER", "DIAMETER", "WIDTH", "DEPTH", "HEIGHT", "LENGTH",
    "CIRCUMFERENCE", "EXTERNAL", "INTERNAL", "OUTER", "INNER", "ESTIMATED", "EST", "APPROX", "APPROXIMATELY",
    "ABOUT", "ALMOST", "NEARLY", "ROUGHLY", "CIRCA", "SOME", "MORE", "LESS", "LEAST", "OVER", "UNDER", "ONLY",
    "JUST", "EXACTLY", "TOTAL", "RECORD",
})


def withheld_fragment(text):
    """True when `text`, what is left of a field once the hero figure is lifted out of it (withhold_figure), is a
    fragment and is not drawn (QF-141). Measured on its words (letters, digits, apostrophes), it is a fragment when
    it has fewer than two words ("DOWN", "RECORD", "ESTIMATED": a word that belonged to the figure's phrase and
    reads as a stray label alone), or no content word (every word an article, preposition, conjunction, unit,
    extent/direction or hedge word: "UP TO THE", "METERS DEEP"), or the word that joined or governed the figure
    is left dangling: it opens on a joiner (OF, THAN, a conjunction, a relative word or a copula: "OF SOLID SALT")
    or ends on a word that needs an object (an article, a joiner, TO, FROM, WITH, BY, IN...: "DEEPER THAN"). A locative
    or adverbial preposition stands at either end ("BELOW KRAKOW", "HIDDEN BELOW"), and so does an opening
    article or possessive ("THE EYE"). Empty text is no fragment (nothing is drawn)."""
    import re

    from .typography import _unit
    words = [w.strip("'").upper() for w in re.findall(r"[^\W_]+(?:'[^\W_]+)*", str(text or ""))]
    words = [w for w in words if w]
    if not words:
        return False
    if len(words) < 2:
        return True
    if words[-1] in _NEEDS_OBJECT or words[0] in _JOINERS:
        return True
    function = _ARTICLES | _JOINERS | _PREPOSITIONS
    return not any(w not in function and w not in _MEASURE_WORDS and not w.isdigit() and not _unit(w)
                   for w in words)


def _dropped_fields(copy, withheld):
    """{"dropped": [fields]} for the record: the fields withhold_figure left empty (the figure was all of it, or
    the rest was a fragment, QF-141), so a reader of the record knows the field was withheld, not lost."""
    dropped = [k for k in ("eyebrow", "line1", "line2", "tagline", "badge")
               if str(copy.get(k) or "").strip() and not str(withheld.get(k) or "").strip()]
    return {"dropped": dropped} if dropped else {}


_PAIRS = {"(": ")", "[": "]", "{": "}", "“": "”", "‘": "’", "«": "»", '"': '"', "'": "'"}
_CLOSERS = frozenset(_PAIRS.values())
_LEAD_MARKS = ".?!,;:…—–-"      # sentence marks and dashes no remainder opens on
_TRAIL_MARKS = ",;:…—–-"        # marks that introduced a figure: no remainder ends on one


def _quote_marks(text, mark):
    """How many `mark` quotes in `text` are quotes (an apostrophe inside a word, "ROME'S", is not one)."""
    import re
    if mark != "'":
        return text.count(mark)
    return len(re.findall(r"(?<!\w)'|'(?!\w)", text))


def _opens(left):
    """True when `left` ends on an opening bracket or quote that is still open."""
    c = left[-1:]
    if c in ('"', "'"):
        return _quote_marks(left, c) % 2 == 1 and (len(left) < 2 or not left[-2].isalnum())
    return bool(c) and c in _PAIRS


def _closes(right, left):
    """True when `right` starts on the closing bracket or quote of a pair opened in `left`."""
    c = right[:1]
    if c in ('"', "'"):
        return _quote_marks(left, c) % 2 == 1 and (len(right) < 2 or not right[1].isalnum())
    return bool(c) and c in _CLOSERS


def _without(text, span):
    """`text` with the figure at `span` removed, and the article or punctuation it leaves dangling.

    QF-141: what the cut leaves is tidied at its edge, never elsewhere in the field. A pair of brackets or quotes
    the cut empties goes ("THE MINE (144 FT)" -> "THE MINE"), one it leaves words in closes up at the cut ("(144 FT
    DEEP)" -> "(DEEP)"); when the figure opened the field, the mark that closed it opens nothing ("144 FT. NO WAY
    OUT" -> "NO WAY OUT"); when it ended the field, the mark that introduced it ends nothing ("THE DEEPEST - 144 FT"
    -> "THE DEEPEST"); a mark that followed the figure mid-field closes the words before it ("THE SECRET 144 FT. NO
    WAY OUT" -> "THE SECRET. NO WAY OUT"); two separating marks the cut brings together are one."""
    import re
    left, right = text[:span[0]], text[span[1]:]
    if not re.search(r"\w", re.split(r"[)\]}”’»]", right, maxsplit=1)[0]):
        # an article right before the figure, with no word after it (before the bracket it closes), was the figure's
        # ("(THE 144 FT)"); QF-142: a word behind an opening bracket follows it ("THE 144 FT (WOW) SHAFT")
        left = re.sub(r"(?:(?<=[\s(\[{“‘«\"'])|^)(?:THE|A|AN)\s+$", "", left, flags=re.IGNORECASE)
    while left and right and left[-1] in _PAIRS and right[0] == _PAIRS[left[-1]]:
        left, right = left[:-1], right[1:]                         # a pair the cut empties
    if _opens(left):
        # "( DEEP)" -> "(DEEP)"; QF-142: "(144 FT, 9 LEVELS)" -> "(9 LEVELS)": the mark that followed the figure
        # inside the brackets separated it from what is left, and separates nothing now
        right = re.sub(r"^[\s" + re.escape(_TRAIL_MARKS) + r"]+", "", right)
    if _closes(right.lstrip(), left):
        right = right.lstrip()
        # "(DEEP )" -> "(DEEP)"; QF-142: "(9 LEVELS, 144 FT)" -> "(9 LEVELS)"
        left = re.sub(r"[\s" + re.escape(_TRAIL_MARKS) + r"]+$", "", left)
    start = re.fullmatch(r"[\s" + re.escape(_LEAD_MARKS) + r"]*(?:(?:THE|A|AN)\s*)?", left,
                         flags=re.IGNORECASE) is not None
    end = re.fullmatch(r"\s*[.?!]*\s*", right) is not None
    if start:
        # "THE 25-MILE EYE" -> "THE EYE"; "THE 144 FT. NO WAY OUT" -> "NO WAY OUT"
        article = re.sub(r"^[\s" + re.escape(_LEAD_MARKS) + r"]+", "", left).strip()
        # QF-142: the article stays when words follow the cut, behind a bracket too ("THE 144 FT (WOW) SHAFT" ->
        # "THE (WOW) SHAFT"; it dropped THE)
        keep = article and re.search(r"\w", right)
        left = article + " " if keep else ""
        right = right.lstrip() if keep else re.sub(r"^[\s" + re.escape(_LEAD_MARKS) + r"]+", "", right)
    if end:
        tail = right.strip()
        left = re.sub(r"(?:(?<=\s)|^)(?:THE|A|AN)\s*$", "", left, flags=re.IGNORECASE)   # "THE DEEPEST, THE"
        left = re.sub(r"[\s" + re.escape(_TRAIL_MARKS) + r"]+$", "", left)
        if _opens(left):
            left = re.sub(r"[\s" + re.escape(_TRAIL_MARKS) + r"]+$", "", left[:-1])   # opened, never closed
        out = left + tail if left else ""
    elif not left or start:
        out = left + right
    else:
        right = right.lstrip()
        if left.rstrip()[-1:] and left.rstrip()[-1:] in _TRAIL_MARKS and right[:1] and right[:1] in _TRAIL_MARKS:
            right = right[1:].lstrip()                             # "A - 144 FT - B" -> "A - B"; "A, 144 FT, B"
        if right[:1] and right[:1] in ".?!,;:…" or _closes(right, left):
            out = left.rstrip() + right                            # "THE SECRET. NO WAY OUT"
        elif _opens(left):
            out = left + right                                     # "THE MINE (DEEP)"
        else:
            out = left.rstrip() + " " + right
    out = re.sub(r"\s{2,}", " ", out).strip()
    out = re.sub(r"^(THE|A|AN)\s*$", "", out, flags=re.IGNORECASE)
    return out.strip(" ,;:-")


def _monument(im, copy, lay, tight, source, focus, figure=None):
    """The "monument number" cover (2026-11-01): one measured figure as the hero, in gold Anton across the
    centre square, over the full-bleed photograph; the subject's name below it and the eyebrow above.

    It replaces lower_third in the rotation (lower_third differed from Discovery only in its headline colour).
    The line the figure came from is shown without it ("THE 144-FOOT" is not repeated under "144 FT"), and the
    figure's own field is dropped when nothing else is left in it. A reviewed focus frames the photograph as
    Discovery does; otherwise the whole photograph fills the canvas.

    Round 3 (2026-11-02): the figure keeps the copy's own qualifier ("EST.", "UP TO") as a kicker attached above
    it, large enough to read in the profile grid; the claim's dimension word ("EXTERNAL DIAMETER", from the fact
    sheet via figure_check) sits under the unit; a figure the sheet ranges, disputes or hedges without a
    qualifier is never the hero (Discovery is drawn instead); the shade is a scrim behind the type only.
    """
    found = monument_figure(copy)
    margin, width = 84, 1080 - 2 * 84
    declined = None
    # Lane B cycle 3 (stress cover FIGURE_GATE: '7,935 LB' drawn as the hero, '7,934' left in the tagline): the hero is
    # the copy's statement of the figure whole (stated_figure: both span ends, its hedge), or no hero
    stated, unread = stated_figure(copy, found) if (found and found[1]) else (None, "")
    if stated is not None and stated.hi:
        found = (stated.number, found[1], found[2], (stated.start, stated.end))
    if unread:
        declined = (f"truth (drawn figure == signed figure): the copy's hedge {unread!r} before {found[0]} "
                    f"{found[1]} is not one the figure line can draw, and a hedge is never dropped")
    elif found is None or (figure and found[1] and not figure.get("monument_ok")):
        declined = (figure or {}).get("reason") or "figure check" if figure else None
    elif figure and not found[1] and figure.get("kind") == "year" and not figure.get("event"):
        # Round 4: a bare year next to a place reads as a founding date; without the sheet's event word for it
        # the monument declines (operator default: the recommended route).
        declined = figure.get("reason") or "the fact sheet gives no event word for this year"
    q_raw, q_from = figure_qualifier(copy, found) if (found and found[1]) else ("", "")
    if found and found[1] and stated is not None and stated.hedge and not q_raw:
        q_raw, q_from = stated.hedge, found[2]
    if found and found[1]:
        qualifier = str((figure or {}).get("drawn_qualifier") or drawn_qualifier(q_raw, found[1])) if q_raw else ""
        faults = hero_truth_faults(qualifier, found[0], found[1], copy) if declined is None else []
        if faults:
            declined = "truth (drawn figure == signed figure): " + "; ".join(faults)
    elif figure and figure.get("kind") == "year" and figure.get("event"):
        qualifier, q_from = str(figure["event"]), "sheet"
    else:
        qualifier = ""
    q_fit = _qualifier_fit(qualifier, width) if qualifier and declined is None else None
    if qualifier and declined is None and q_fit is None:
        declined = f"the qualifier {qualifier!r} does not fit the measure at profile-grid size"
    if found is None or declined is not None:
        # No figure to make the hero, or one the sheet will not let stand alone: Discovery is the fallback.
        if lay is not None and declined:
            lay.meta["monument_declined"] = declined
        return render_cover(im, withhold_figure(copy, figure), "badge", lay=lay, tight=tight, source=source,
                            focus=focus, figure=figure)
    number, unit, field, span = found
    dimension = str((figure or {}).get("dimension") or "") if unit else ""
    rest = dict(copy)
    if field in ("line1", "line2", "tagline", "eyebrow"):
        cut = span
        if q_raw and q_from == field:   # "UP TO 144-FOOT": the qualifier goes up with the figure
            import re
            q = re.search(r"(?<![\w.])(" + _QUAL + r")\s*$", str(copy[field])[:span[0]], re.IGNORECASE)
            cut = (q.start() if q else span[0], span[1])
        rest[field] = _without(str(copy[field]), cut)
    # The same figure elsewhere in the copy (badge "EST. 144 FT (44 M)" and line1 "THE 144-FOOT") is not
    # repeated under the hero either.
    for other in ("line1", "line2", "tagline"):
        if other != field:
            found_other = monument_figure({other: rest.get(other)})
            if found_other and found_other[0] == number:
                rest[other] = _without(str(rest[other]), found_other[3])
            elif stated is not None and stated.hi:
                # lane B cycle 3: the span hero's statement elsewhere goes whole too (never one end left behind)
                from .figures import parse
                for f in reversed(parse(str(rest.get(other) or ""))):
                    if f.unit and set(f.values) & set(stated.values):
                        rest[other] = _without(str(rest[other]), (f.start, f.end))
    titles = [(key, display(rest.get(key)).strip().upper()) for key in ("line1", "line2")]
    titles = [(n, t) for n, t in titles if t]
    eyebrow = display(rest.get("eyebrow")).strip().upper()
    tagline = "" if redundant_tagline(rest) else display(rest.get("tagline")).strip().upper()

    measure = ImageDraw.Draw(im)
    factor = 0.88 if tight else 1.0
    from .typography import fit_font, tracked_width
    # The unit block right of the figure: the unit, and under it the claim's dimension word (one or two lines).
    dim_font = get_font(brand.TEXT_FONT, DIM_WORD_PX, weight=700)
    dim_lines = []
    if dimension:
        words = dimension.split()
        one = " ".join(words)
        dim_lines = [one] if len(words) == 1 or tracked_width(one, dim_font, DIM_WORD_TRACK) <= 300 else words[:2]
    dim_w = max([tracked_width(t, dim_font, DIM_WORD_TRACK) for t in dim_lines] or [0])
    reserve = max(240, int(dim_w) + 22) if unit else 0
    # The figure: as large as fits the width, up to 420 px (it is the cover's one idea).
    num_font = fit_font(measure, number, brand.DISPLAY_FONT, int(420 * factor), width - reserve, 160)
    unit_font = get_font(brand.DISPLAY_FONT, max(72, int(num_font.size * 0.33)))
    # The qualifier kicker, one line read in the 124 px profile grid (QUALIFIER_GRID_CAP, _qualifier_fit).
    q_lines, q_font, q_track = q_fit if q_fit else ([], None, QUALIFIER_TRACK)
    eyebrow_rows, title_rows, tag_rows = [], [], []
    if eyebrow:
        lines, f_e, _ = _eyebrow_lines(measure, eyebrow, width)
        eyebrow_rows = [(lines[0], f_e, brand.GOLD)]
    if titles:
        # Round 7 (F1, F21): the title lines are the cover's lockup, set by the heading engine at one size (104,
        # then its one fallback tier; never fitted): two-tone when both lines are left (2026-11-02), the payoff gold
        # but for its opening function words; one line alone stays bone (the figure is the cover's gold read).
        got, rec = _lockup(measure, dict(titles), width, int(104 * factor), single_gold=False)
        for name, key in (("title_1", "line1"), ("title_2", "line2")):
            if key not in got:
                continue
            f_t, line_runs_ = got[key]
            for line_runs in line_runs_:
                line, dx, runs = " ".join(t for t, _ in line_runs), 0.0, []
                for run, role in line_runs:
                    runs.append((run, brand.GOLD if role in GOLD_ROLES else brand.BONE, round(dx)))
                    dx += measure.textlength(run + " ", font=f_t)
                title_rows.append((name, line, f_t, runs))
        if lay is not None:
            lay.shortened = list(rec.get("off_ladder", []))
            lay.meta["lockup"] = rec
            lockup_line_faults(lay.meta, measure, got)
    if tagline:
        lines, f_g, _ = _tagline_lines(measure, tagline, width, int(36 * factor))
        tag_rows = [(lines[0], f_g, brand.STONE_LT)]

    num_h = cap_h(num_font)
    q_h = cap_h_of(q_font) + QUALIFIER_GAP if q_font else 0
    # The dimension line marks a measurement; a year or a bare count has no length (operator, 2026-11-02).
    measured_figure = bool(unit)
    gaps = {"eyebrow": 26, "number": 34 + (DIM_ROOM if measured_figure else 0), "title": 10, "tagline": 0}
    # A qualifier under the eyebrow: the two kickers keep EYEBROW_QUALIFIER_GAP of clear field, ink to ink, so they
    # read as two lines (eyebrow, then the figure's own hedge), not one crowded block.
    eyebrow_to_q = None
    if q_font and eyebrow_rows:
        t, f, _ = eyebrow_rows[-1]
        eyebrow_to_q = f.getbbox(t, anchor="lt")[3] + EYEBROW_QUALIFIER_GAP - (text_size(measure, t, f)[1]
                                                                              + gaps["eyebrow"])
        q_h += eyebrow_to_q
    def stack_height():
        return (sum(text_size(measure, t, f)[1] + gaps["eyebrow"] for t, f, _ in eyebrow_rows) + q_h + num_h
                + gaps["number"] + sum(text_size(measure, t, f)[1] + gaps["title"] for _, t, f, _ in title_rows)
                + 14 + sum(text_size(measure, t, f)[1] for t, f, _ in tag_rows))
    top = 1434 - stack_height()
    # The subject sits in the clear band above the type. Round 3: the shade is a scrim confined to the type
    # stack (the photo outside it, and the focus band above it, stay ungraded); each line's own measured halo
    # (place_text) still holds it at contrast wherever the scrim is light.
    im = _full_bleed(source, focus, (440 + top - 120) / 2, lay)
    widths = ([sum(kicker_lead(f)[:2]) + tracked_width(t, f, kicker_track(f)) for t, f, _ in eyebrow_rows]
              + [tracked_width(t, f, TAGLINE_TRACK) for t, f, _ in tag_rows]
              + [text_size(measure, t, f)[0] for _, t, f, _ in title_rows]
              + [text_size(measure, number, num_font)[0] + (reserve if unit else 0)])
    stack_right = min(1080, margin + max(widths))
    # Round 4: the box runs one deck cap height past the last line, so the STONE_LT deck sits on a dark field
    # over a pale facade and does not lean on its halo.
    scrim_foot = 1434 + (cap_h_of(tag_rows[-1][1]) if tag_rows else cap_h_of(get_font(brand.TEXT_FONT, TAGLINE_PX)))
    im = _scrim(im, (margin, top, stack_right, scrim_foot), MONUMENT_SCRIM, MONUMENT_SCRIM_FEATHER)
    if lay is not None:
        lay.meta["scrim"] = {"box": [margin, top, stack_right, scrim_foot], "strength": MONUMENT_SCRIM,
                             "feather": MONUMENT_SCRIM_FEATHER}
    im = _brand(im, lay, grid_delivery=True)

    y = top
    plates = {}

    def put(name, text, font, fill, xy, anchor="lt", target=5.1, track=None):
        nonlocal im
        track = ({"eyebrow": kicker_track(font), "tagline": TAGLINE_TRACK}.get(name, 0)
                 if track is None else track)
        xy, anchor = _top_anchor(xy, text, font, anchor, track)
        im, plate = place_text(im, xy, text, font, fill, anchor=anchor, target=target, track=track)
        plates.setdefault(name, []).append(plate)

    for t, f, c in eyebrow_rows:
        # round 7 (F20 + sweep S1): the system kicker: its lead rule, planned at the kicker gate (5.5), gold, else
        # bone, else not drawn (recorded in lay.meta["kickers"])
        from .multi_format import plan_kicker
        rule, rgap, thick = kicker_lead(f)
        base_k = y - f.getbbox(t, anchor="ls")[1]
        mid = base_k - cap_h_of(f) / 2
        k_plate, k_rec = plan_kicker(im, (margin + rule + rgap, base_k), t, f, c, "ls", kicker_track(f),
                                     rule=(margin, round(mid - thick / 2), margin + rule - 1, round(mid - thick / 2) + thick - 1))
        if lay is not None:
            lay.meta.setdefault("kickers", {})["eyebrow"] = k_rec
        if k_plate is not None:
            ImageDraw.Draw(im).rectangle([margin, round(mid - thick / 2), margin + rule - 1,
                                          round(mid - thick / 2) + thick - 1], fill=tuple(k_plate.fill) + (255,))
            im = k_plate.draw(im)
            plates.setdefault("eyebrow", []).append(k_plate)
        y += text_size(measure, t, f)[1] + gaps["eyebrow"]
    if q_font:
        y += eyebrow_to_q or 0
        put("figure_qualifier", q_lines[0], q_font, brand.GOLD, (margin, y + cap_h_of(q_font)), anchor="ls",
            track=q_track)
        y += q_h - (eyebrow_to_q or 0)
    base = y + num_h
    im = _gold_ground(im, (margin, base - num_h, margin + text_size(measure, number, num_font)[0]
                           + (reserve if unit else 0), base), lay)
    put("figure", number, num_font, brand.GOLD, (margin, base), anchor="ls")
    right = margin + text_size(measure, number, num_font)[0]
    if unit:
        nx = right + 22
        # Unit over the dimension word, the block's last baseline locked to the numeral's.
        dim_pitch = cap_h_of(dim_font) + 12
        unit_base = base - (len(dim_lines) * dim_pitch + 6 if dim_lines else 0)
        put("figure_unit", unit, unit_font, brand.GOLD, (nx, unit_base), anchor="ls")
        block_right = nx + text_size(measure, unit, unit_font)[0]
        for k, t in enumerate(dim_lines):
            yb = base - (len(dim_lines) - 1 - k) * dim_pitch
            put("figure_dim", t, dim_font, brand.STONE_LT, (nx, yb), anchor="ls", target=5.1, track=DIM_WORD_TRACK)
            block_right = max(block_right, nx + tracked_width(t, dim_font, DIM_WORD_TRACK))
        right = round(block_right)
    # The style's signature (2026-11-02): a dimension line under a measured figure, end ticks up, as on a
    # survey drawing. Never under a year or a count: it would claim a length that is not there.
    if measured_figure:
        fig_bottom = max(p.box[3] for n, ps in plates.items() if n.startswith("figure") for p in ps if not p.empty)
        dy = fig_bottom + 30
        d = ImageDraw.Draw(im)
        d.line([(margin, dy), (right, dy)], fill=brand.GOLD + (255,), width=4)
        for xx in (margin, right):
            d.line([(xx, dy - 16), (xx, dy + 2)], fill=brand.GOLD + (255,), width=4)
        if lay is not None:
            lay.add("dimension", (margin, dy - 16, right, dy + 2))
    y = base + gaps["number"]
    for name, t, f, runs in title_rows:
        for run, c, dx in runs:
            put(name, run, f, c, (margin + dx, y))
        y += text_size(measure, t, f)[1] + gaps["title"]
    y += 14
    for t, f, c in tag_rows:
        put("tagline", t, f, c, (margin, y))
    for name, ps in plates.items():
        record_contrast(lay, name, ps)
    if lay is not None:
        lay.meta.setdefault("plates", {}).update(plates)
        lay.meta["monument"] = {"number": number, "unit": unit, "from": field}
        if qualifier or dimension:
            lay.meta["monument_figure"] = {"qualifier": qualifier, "qualifier_from": q_from,
                                           "dimension": dimension, "copy_qualifier": q_raw}
    return im


MONUMENT_MEASURE = 1080 - 2 * 84   # the monument's type measure
HEDGE_MEASURE = 1080 - 2 * 96     # the narrowest measure a hedge line is set on (the paper card's)
GOLD_HUE_NEAR, GOLD_CHROMA_MIN = 25.0, 25.0   # a ground within 25 deg of GOLD's hue and above C* 25 camouflages it
GOLD_GROUND = (0.4, 0.85, 48)                  # then: 40% desaturated, 0.85 luminance, eased over 48 px


def _qualifier_fit(text, width=MONUMENT_MEASURE):
    """(lines, font, track) for the qualifier kicker on one line at QUALIFIER_GRID_CAP px of cap height in the
    124 px grid: with QUALIFIER_TRACK, else untracked; None when it is wider than `width` even then. Round 4
    (operator default, the synthesis's second route): a hedge phrase that cannot hold the floor on one line
    ("BY ONE ESTIMATE", 1,121 px) declines the monument to Discovery, which sets it on its own line; two
    112 px kicker lines made the hedge the cover's second-largest mark and pushed the subject out of frame."""
    from .typography import tracked_width
    size = 60
    while cap_h_of(get_font(brand.TEXT_FONT, size, weight=QUALIFIER_WEIGHT)) * GRID_SCALE[0] < QUALIFIER_GRID_CAP:
        size += 2
    font = get_font(brand.TEXT_FONT, size, weight=QUALIFIER_WEIGHT)
    for track in (QUALIFIER_TRACK, 0.0):
        if tracked_width(text, font, track) <= width:
            return [text], font, track
    return None


def _lch(rgb):
    """CIE L*, C*, h (degrees) of sRGB rows (N x 3, 0-255)."""
    import numpy as np
    a = np.asarray(rgb, dtype=np.float64) / 255
    lin = np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
    xyz = lin @ np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]]).T
    xyz /= np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    lab_l, lab_a, lab_b = 116 * f[:, 1] - 16, 500 * (f[:, 0] - f[:, 1]), 200 * (f[:, 1] - f[:, 2])
    return lab_l, np.hypot(lab_a, lab_b), np.degrees(np.arctan2(lab_b, lab_a)) % 360


def _gold_ground(im, box, lay):
    """Gold-on-gold camouflage gate (round 4): when the photograph behind the gold numeral's box has a median hue
    within GOLD_HUE_NEAR of GOLD's and a chroma above GOLD_CHROMA_MIN (sandstone, a sunlit dome, sand), that
    ground alone is desaturated and pulled darker (GOLD_GROUND), eased at its edge, before the numeral's contrast
    is solved. Elsewhere the photograph is untouched; on other grounds nothing changes."""
    import numpy as np
    x0, y0, x1, y1 = (round(v) for v in box)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(im.width, x1), min(im.height, y1)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return im
    rgba = im.convert("RGBA")
    patch = np.asarray(rgba.convert("RGB").crop((x0, y0, x1, y1)), dtype=np.float64).reshape(-1, 3)
    _, chroma, hue = _lch(np.median(patch, axis=0)[None, :])
    _, _, gold_hue = _lch(np.array([brand.GOLD], dtype=np.float64))
    near = abs((float(hue[0]) - float(gold_hue[0]) + 180) % 360 - 180) <= GOLD_HUE_NEAR
    hit = bool(near and float(chroma[0]) > GOLD_CHROMA_MIN)
    if lay is not None:
        lay.meta["gold_ground"] = {"chroma": round(float(chroma[0]), 1), "hue": round(float(hue[0]), 1),
                                   "applied": hit}
    if not hit:
        return im
    desat, lum, ease = GOLD_GROUND
    arr = np.asarray(rgba, dtype=np.float32)
    rgb = arr[..., :3]
    grey = (rgb @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32))[..., None]
    graded = (rgb + (grey - rgb) * desat) * lum
    yy, xx = np.ogrid[:im.height, :im.width]
    d = np.maximum(np.maximum(np.maximum(x0 - xx, xx - x1), np.maximum(y0 - yy, yy - y1)), 0).astype(np.float32)
    v = np.clip(1 - d / ease, 0, 1)
    v = (v * v * (3 - 2 * v))[..., None]
    arr[..., :3] = rgb + (graded - rgb) * v
    return Image.fromarray(np.clip(arr, 0, 255).astype("uint8"), "RGBA")


def _full_bleed(source, focus, cy, lay):
    """The photograph filling the 9:16 canvas, its reviewed feature (else its centre) placed at x 540, y `cy`
    as far as the canvas allows: the monument cover is photograph-first behind its type, not an inset."""
    if source.info.get("essential_full_image"):
        # Keep the entire photo in the central profile-grid region as well.
        return _photograph(Image.new("RGBA", (1080, 1920), (0, 0, 0, 255)), source,
                           (0, 420, 1080, 1500), lay)
    src = ImageOps.exif_transpose(source).convert("RGBA")
    sw, sh = src.size
    scale = max(1080 / sw, 1920 / sh)
    w, h = round(sw * scale), round(sh * scale)
    fx, fy = 0.5, 0.5
    if focus:
        b = focus["focus_box"]
        fx, fy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    left = min(0, max(1080 - w, round(540 - fx * w)))
    top = min(0, max(1920 - h, round(cy - fy * h)))
    canvas = Image.new("RGBA", (1080, 1920))
    canvas.paste(src.resize((w, h), Image.Resampling.LANCZOS), (left, top))
    if lay is not None:
        lay.meta["composition"] = {"version": DESIGN_VERSION, "mode": "monument_full_bleed",
                                   "image_box": [left, top, left + w, top + h], "source_size": [sw, sh],
                                   **({"focus_box": focus["focus_box"]} if focus else {})}
    return canvas


def cap_h(font):
    return -font.getbbox("8", anchor="ls")[1]


# Operator ruling 7 (2026-11-16, lane B cycle 1): a cover tagline emptied by a withheld figure falls back to another
# already-verified line of the run's copy (never invented text), empty only if none fits. The lines are the run's
# carousel slide headings (verified with the script; short display lines), in deck order. A fallback is a tagline in
# its own right: two to four words, at most TAGLINE_FALLBACK_CHARS, no figure (a number would need its own check),
# no call to action, a phrase that stands alone (withheld_fragment), new to the headline (redundant_tagline) and not
# answering the reveal (resolving_tagline); the one sharing the fewest words with the headline and eyebrow is taken.
TAGLINE_FALLBACK_CHARS = 24
_CTA_WORDS = frozenset({"FOLLOW", "SAVE", "SHARE", "COMMENT", "SUBSCRIBE", "LIKE", "TAP", "SWIPE", "CLICK", "YOUR",
                        "YOU", "LINK", "WATCH"})


def tagline_alternates(script_payload):
    """[(source, line)]: the run's verified lines a withheld tagline may fall back to (fallback_tagline)."""
    out = []
    for i, slide in enumerate(script_payload.get("carousel_slides") or [], 1):
        text = " ".join(str((slide or {}).get("heading") or "").split()).upper() if isinstance(slide, dict) else ""
        if text:
            out.append((f"carousel slide {i} heading", text))
    return out


def fallback_tagline(copy, alternates, reveal=None):
    """(source, line) for the tagline (ruling 7) from `alternates`, or None when none fits."""
    import re
    def words(t):
        return {w for w in re.findall(r"[A-Z0-9']+", str(t or "").upper()) if w not in _STOP}
    shown = words(" ".join(str(copy.get(k) or "") for k in ("line1", "line2", "eyebrow")))
    best = None
    for order, (source, text) in enumerate(alternates):
        t = " ".join(str(text or "").split()).upper()
        n = len(re.findall(r"[^\W_]+(?:'[^\W_]+)*", t))
        if not (2 <= n <= 4) or len(t) > TAGLINE_FALLBACK_CHARS or any(c.isdigit() for c in t):
            continue
        if words(t) & _CTA_WORDS or t.endswith("?") or withheld_fragment(t):
            continue
        cand = dict(copy, tagline=t)
        if redundant_tagline(cand) or resolving_tagline(cand, reveal):
            continue
        score = (len(words(t) & shown), order)
        if best is None or score < best[0]:
            best = (score, (source, t))
    return best[1] if best else None


def render_cover(im, copy, style, center_y=960, lay=None, tight=False, source=None, focus=None, figure=None,
                 illustration=False, reveal=None, alternates=None):
    """Render the three existing choices and the editorial rotation.

    A: a bone-paper archival print, inset image over a compact caption.
    B: immersive discovery photograph, with a dominant gold headline below.
    C: centred cinema title above the photograph.
    Editorial: a wider, quieter photo-first stack with a fine separating rule.

    A/C preserve the full source. B/editorial can frame a manually reviewed
    feature; absent such a record, they also retain the complete photograph.

    `figure` (figure_check, round 3): what the fact sheet says about the copy's hero figure. A conflicted one
    (a range, a disputed value) is lifted out of the headline on every style; the monument draws a figure only
    when the check lets it stand. `illustration`: the hero is a generated illustration, so the window carries
    the on-screen disclosure label (the existing wording, video_composer.RECON_BADGE_LABEL). `reveal` (round 4):
    the script's reveal or takeaway line; a tagline that answers it is dropped (resolving_tagline). `alternates`
    (tagline_alternates): the run's other verified lines a tagline the withheld figure emptied falls back to
    (operator ruling 7, 2026-11-16; fallback_tagline).
    """
    why = resolving_tagline(copy, reveal)
    if why:
        if lay is not None:
            lay.meta["tagline_dropped"] = {"tagline": copy.get("tagline"), "reason": why}
        copy = dict(copy, tagline="")
    if must_withhold(figure):
        withheld = withhold_figure(copy, figure)
        if lay is not None and withheld != dict(copy):
            lay.meta["figure_withheld"] = {"number": figure.get("number"), "unit": figure.get("unit"),
                                           "reason": figure.get("reason"),
                                           **_dropped_fields(copy, withheld)}
        if str(copy.get("tagline") or "").strip() and not str(withheld.get("tagline") or "").strip():
            fb = fallback_tagline(withheld, alternates or (), reveal)
            if fb:
                withheld = dict(withheld, tagline=fb[1])
                if lay is not None:
                    lay.meta["tagline_fallback"] = {"from": fb[0], "text": fb[1],
                                                    "reason": "the tagline was withheld with the figure"}
            elif lay is not None and alternates:
                lay.meta["tagline_fallback"] = {"from": None, "text": "",
                                                "reason": "no other verified line of the run fits as a tagline"}
        copy = withheld
    out = _render(im, copy, style, lay, tight, source, focus, figure)
    if illustration:
        out = _provenance(out, lay)
    if lay is not None:
        lay.meta["grid_proof"] = grid_proof(out, lay)
    return out


def _render(im, copy, style, lay, tight, source, focus, figure):
    source = source if source is not None else im
    if style == "monument":
        return _monument(im, copy, lay, tight, source, focus, figure)
    centered = style == "minimal"
    paper = style == "depth_sandwich"
    margin = 96 if paper else 84
    width = 1080 - 2 * margin
    rows, shortened = _rows(im, copy, width, style, tight, figure,
                            lay_record=lay.meta if lay is not None and isinstance(getattr(lay, "meta", None), dict) else None)
    # Very long generated copy shares the central square with the photograph:
    # reduce the *whole* stack consistently before sacrificing its image space.
    if _height(rows) > 500:
        kept = sum(r["height"] for r in rows if r.get("keep"))
        scale = (500 - sum(r["gap"] for r in rows) - kept) / max(1, sum(r["height"] for r in rows) - kept)
        for row in rows:
            if row.get("keep") or row.get("keep_size"):
                continue   # r7: the hedge holds its grid floor; round 7: the lockup's size is never fitted
            # The eyebrow and the subline keep their floors (36 and 32 px): they are what vanished in the grid.
            floor = {"eyebrow": EYEBROW_MIN, "qualifier": EYEBROW_MIN, "tagline": TAGLINE_MIN}.get(row["name"], 24)
            row["font"] = get_font(brand.DISPLAY_FONT if row["name"].startswith("title") else brand.TEXT_FONT,
                                   max(floor, int(row["font"].size * scale)),
                                   weight=800 if row["name"] in ("eyebrow", "qualifier") else None)
            row["height"] = text_size(ImageDraw.Draw(im), row["text"], row["font"])[1]
    if lay is not None:
        lay.shortened = shortened
    # Round 7 (F20): one kicker on every style: the system kicker (no gold tab: it was a second kicker treatment)
    height = _height(rows)
    if style == "badge" or (focus and style == "lower_third"):
        # Freeze cycle 5 (PHOTO-01 / PHOTO-07): the photograph fills its window with its own pixels or it is not
        # full-bleed (_filled: the photo-fitness ladder). A cover that cannot be filled falls through to the
        # contained window on a clean INK field below; nothing is padded, feathered into a blur or repeated.
        top = 1434 - height
        placed = _filled(source, focus, top, lay)
        if placed is not None:
            im, mode = placed
            if mode == "full_bleed":
                im = _shade(im, top + 10, 1920, strength=0.84 if style == "badge" else 0.78, feather=100,
                            foot=(top + height + BADGE_FOOT[0], BADGE_FOOT[1], BADGE_FOOT[2])
                            if style == "badge" else None)
            im = _brand(im, lay, grid_delivery=True)
            return _paint(im, rows, margin, top, False, lay)
    # The contained window: the photograph appears once, whole, on a flat INK field (paper: inside its card). Freeze
    # cycle 5 (PHOTO-07: the darkened copy of the same photograph ran on above and below its own inset, one picture
    # three times in a tile): no continuation of the photograph outside its window.
    im = Image.new("RGBA", im.size, brand.INK + (255,))
    if paper:
        ImageDraw.Draw(im).rectangle(_sandwich_card(), fill=brand.BONE + (255,))
    im = _brand(im, lay, centered, paper, grid_delivery=True)
    if style == "minimal":
        # Measured ink to ink (2026-11-02 round 2): the masthead's foot to the eyebrow's caps MINIMAL_GAPS[0], the
        # eyebrow's foot to the title's caps MINIMAL_GAPS[1]. Rows are placed by their ascender line, so each gap
        # is corrected by how far the next line's ink starts below it.
        from itertools import pairwise

        def off(r):
            return r["font"].getbbox(r["text"], anchor="lt")[1]
        brand_foot = BRAND_TOP + get_font(brand.MASTHEAD_FONT, BRAND_PX, weight=700).getbbox(brand.MASTHEAD, anchor="lt")[3]
        top = brand_foot + MINIMAL_GAPS[0] - (off(rows[0]) if rows else 0)
        for a, b in pairwise(rows):
            if a["name"] == "eyebrow" and b["name"] != "eyebrow":
                a["gap"] = MINIMAL_GAPS[1] - off(b) + off(a)
        height = _height(rows)
        photo_top = top + height + 40
        aperture = (60, photo_top, 1020, 1456)
    elif paper:
        # One internal grid on the card (round 3): the window, kicker, headline and tagline share x 96-984; the
        # window's foot to the kicker tab is twice the kicker-to-headline gap; the room saved goes to the window.
        top = SANDWICH_FOOT - height
        kicker = next((r for r in rows if r["name"] == "eyebrow"), None)
        gap = 2 * (kicker["gap"] if kicker else 22)
        aperture = (margin, SANDWICH_WINDOW_TOP, 1080 - margin, top - gap)
    else:
        top = 1434 - height
        image_margin = 0 if style == "badge" else 48
        aperture = (image_margin, 528, 1080 - image_margin, top - 40)
    if paper and focus:
        im = _window(im, source, aperture, focus, lay)
    else:
        im = _photograph(im, source, aperture, lay, bottom=paper)
    if paper and lay is not None:
        lay.meta["sandwich"] = {"aperture": list(aperture), "card": list(_sandwich_card())}
    if style == "lower_third":
        y = top - 21
        right = 1080 - margin
        ImageDraw.Draw(im).line((margin, y, right, y), fill=brand.GOLD_DIM + (255,), width=1)
        if lay is not None:
            lay.add("rule", (margin, y, right, y + 1))
    return _paint(im, rows, 540 if centered else margin, top, centered, lay)


PLATE_SQUARE = (0, 420, 1079, 1499)   # the framed-window covers' INK plate: the profile grid's 1:1 square
SANDWICH_WINDOW_TOP = 520  # the paper card's window starts under the masthead (12 px under its ink; round 4:
                            # 4 px of it went to the deck's 32 px card foot, so the window keeps half the card)
SANDWICH_CARD_FOOT = 1479   # the card's own foot (inside the 1:1 square, y 420-1500)
SANDWICH_FOOT = SANDWICH_CARD_FOOT - 32   # the card's type ends here: >= 32 px of card under the deck (round 4)
SANDWICH_INSET = 36         # the card's side inset to its type (x 60 -> 96); the masthead keeps it at the top too


def _sandwich_card():
    """The paper card's rectangle. Round 4: its top sits SANDWICH_INSET above the masthead's cap line (it was
    ~22 px, the visible cramp), never above the 1:1 square's top (y 420); the masthead itself stays locked."""
    return (60, max(420, BRAND_TOP - SANDWICH_INSET), 1019, SANDWICH_CARD_FOOT)


def _window_scale(aw, ah, sw, sh, fw, fh):
    """The paper window's scale: cover the aperture (aw x ah) where the reviewed feature (fw x fh source px) still
    fits inside it, never past 2x the source pixels (as _immersive), never under complete containment."""
    contain = min(aw / sw, ah / sh)
    return max(contain, min(max(aw / sw, ah / sh), aw / fw, ah / fh, 2.0))


def _window(im, source, aperture, focus, lay):
    """The paper card's window filled by a reviewed photograph (round 4): scaled to cover the full aperture
    (x 96-984) as far as the reviewed feature still fits inside it (the protecting scale Discovery uses, capped at
    2x), the feature centred across and the photograph's foot on the aperture's foot (the kicker gap). Where the
    feature needs a smaller scale the window is the photograph's own extent, bottom-aligned. Unreviewed photographs
    keep the complete-photograph containment (_photograph)."""
    if source.info.get("essential_full_image"):
        return _photograph(im, source, aperture, lay, bottom=True)
    import math
    x0, y0, x1, y1 = map(int, aperture)
    src = ImageOps.exif_transpose(source).convert("RGBA")
    sw, sh = src.size
    fx0, fy0, fx1, fy1 = [v * (sw if i % 2 == 0 else sh) for i, v in enumerate(focus["focus_box"])]
    scale = _window_scale(x1 - x0, y1 - y0, sw, sh, fx1 - fx0, fy1 - fy0)
    w, h = max(1, round(sw * scale)), max(1, round(sh * scale))
    left = round((x0 + x1) / 2 - (fx0 + fx1) / 2 * scale)
    left = min(x0, max(x1 - w, left)) if w >= x1 - x0 else x0 + (x1 - x0 - w) // 2
    top = round((y0 + y1) / 2 - (fy0 + fy1) / 2 * scale)
    if h >= y1 - y0:
        top = min(y0, max(y1 - h, top))
        # the feature's foot never below the window's: shift up only as far as the photograph allows
        top = max(y1 - h, min(top, y1 - math.ceil(fy1 * scale)))
    else:
        top = y1 - h
    photo = src.resize((w, h), Image.Resampling.LANCZOS)
    box = [max(x0, left), max(y0, top), min(x1, left + w), min(y1, top + h)]
    im.alpha_composite(photo.crop((box[0] - left, box[1] - top, box[2] - left, box[3] - top)), (box[0], box[1]))
    protected = [math.floor(left + fx0 * scale), math.floor(top + fy0 * scale),
                 math.ceil(left + fx1 * scale), math.ceil(top + fy1 * scale)]
    protected = [max(box[0], protected[0]), max(box[1], protected[1]), min(box[2], protected[2]),
                 min(box[3], protected[3])]
    if lay is not None:
        lay.protected.append({"name": "reviewed photographic feature", "box": protected})
        lay.meta["composition"] = {
            "version": DESIGN_VERSION, "mode": "reviewed_window", "source_size": [sw, sh],
            "source_box": [(box[0] - left) / scale, (box[1] - top) / scale, (box[2] - left) / scale,
                           (box[3] - top) / scale],
            "image_box": box, "focus_box": focus["focus_box"], "protected_box": protected,
            "aperture": [x0, y0, x1, y1], "reviewed": focus.get("reviewed", ""),
            "source_sha256": focus.get("sha256", ""),
        }
    return im


def sandwich_fill(copy, focus, source_size, figure=None):
    """The share (0-1) of the paper card's window the reviewed feature fills once placed there (_window), from
    the copy's own stack height; None without a reviewed focus. cover_selector reads it (round 4: a small subject
    on a flat ceiling made the weakest grid tile)."""
    if not focus or not source_size:
        return None
    im = Image.new("RGBA", (1080, 1920))
    try:
        rows, _ = _rows(im, copy, 1080 - 2 * 96, "depth_sandwich", False, figure)
    except TextFitError:   # QF-23: the stack the card is drawn with then is the tighter fit's (still_layout.drawn)
        try:
            rows, _ = _rows(im, copy, 1080 - 2 * 96, "depth_sandwich", True, figure)
        except TextFitError:
            return 0.0     # the copy fits the paper card in neither fit: it is not offered
    height = _height(rows)
    kicker = next((r for r in rows if r["name"] == "eyebrow"), None)
    top = SANDWICH_FOOT - height
    gap = 2 * (kicker["gap"] if kicker else 22)
    ap = (96, SANDWICH_WINDOW_TOP, 984, top - gap)
    aw, ah = ap[2] - ap[0], ap[3] - ap[1]
    if aw <= 0 or ah <= 0:
        return 0.0
    sw, sh = source_size
    b = focus["focus_box"]
    fw, fh = (b[2] - b[0]) * sw, (b[3] - b[1]) * sh
    scale = _window_scale(aw, ah, sw, sh, fw, fh)
    window = min(aw, sw * scale) * min(ah, sh * scale)
    return round(min(1.0, fw * scale * fh * scale / window), 3) if window else 0.0


def _provenance(im, lay):
    """The on-screen disclosure over a generated hero illustration: the existing wording
    (video_composer.RECON_BADGE_LABEL), IBM Plex Mono in STONE_LT on an INK plate at the window's lower-left edge,
    inside the profile-grid square, measured like every other line.

    Freeze lane F cycle 4 (panel 5 TY r1, the Aswan Reel cover: the plate ran ~70 px past its photo window onto the
    INK field; on real heroes 29 of 49 cover cases, up to 242 px): the plate stands INSIDE the window it labels,
    PROVENANCE_INSET from its left and right edges. The label whole on one line at the largest PROVENANCE_SIZES size
    that fits; else broken at its own separator (" · ", kept on the first line, so the drawn text is the label
    whole) into two lines at the largest size that fits; LayoutError when neither fits at the floor (the cover is
    refused and recorded, never drawn with the disclosure off its window or missing)."""
    from .refusal import LayoutError
    from .typography import tracked_width
    from .video_composer import RECON_BADGE_LABEL
    comp = (lay.meta.get("composition") if lay is not None else None) or {}
    box = comp.get("image_box") or [84, 560, 996, 1100]
    x0, y1 = max(84, int(box[0]) + PROVENANCE_INSET), min(1460, int(box[3]) - PROVENANCE_INSET)
    room = int(box[2]) - PROVENANCE_INSET - x0
    head, sep, tail = RECON_BADGE_LABEL.partition(" · ")
    fit = None
    for lines in [[RECON_BADGE_LABEL]] + ([[head + sep.rstrip(), tail]] if sep else []):
        for px in PROVENANCE_SIZES:
            font = get_font(brand.DATA_FONT, px, weight=600)
            w = max(tracked_width(ln, font, 1.0) for ln in lines) + 2 * PROVENANCE_PAD[0]
            if w <= room:
                fit = (px, lines, font, round(w))
                break
        if fit:
            break
    if fit is None:
        raise LayoutError(f"the AI disclosure does not fit its {int(box[2]) - int(box[0])} px photo window at "
                          f"{PROVENANCE_SIZES[-1]} px on two lines (disclosure rule: the label sits inside the "
                          f"window it labels)")
    px, lines, font, w = fit
    cap = cap_h_of(font)
    h = cap + 2 * PROVENANCE_PAD[1] + (len(lines) - 1) * round(PROVENANCE_LEAD * cap)
    plate = (x0, y1 - h, x0 + w, y1)
    im = im.convert("RGBA")
    ImageDraw.Draw(im).rectangle(plate, fill=brand.INK + (255,))
    ps = []
    for k, ln in enumerate(lines):
        base = y1 - PROVENANCE_PAD[1] - (len(lines) - 1 - k) * round(PROVENANCE_LEAD * cap)
        im, p = place_text(im, (x0 + PROVENANCE_PAD[0], base), ln, font,
                           brand.STONE_LT, anchor="ls", target=5.1, track=1.0, halo_px=0)
        ps.append(p)
    if lay is not None:
        # Kept in the renderer's record, not as a layout element: it sits on the photograph on purpose (the
        # window it labels), which the layout check would otherwise report as an overlap.
        lay.meta.setdefault("plates", {})["provenance"] = ps
        cs = [p.contrast for p in ps if p.contrast is not None]
        lay.meta["provenance"] = {"label": RECON_BADGE_LABEL, "lines": list(lines), "px": px,
                                  "plate": [round(v) for v in plate], "window": [round(v) for v in box],
                                  "contrast": min(cs) if cs else None}
    return im


PROVENANCE_PX, PROVENANCE_PAD = 30, (14, 10)
# one line from 30 px, else the type system's credit size (TYPE tall credit, 24), the floor of either form: the
# label steps only onto TYPE sizes, so it opens no new size site (lane F c4 CI: 28 / 26 were off the ladder)
PROVENANCE_SIZES = (PROVENANCE_PX, round(type_px("credit", "tall")))
PROVENANCE_INSET = 24       # design px: the plate's edge inside its photo window
PROVENANCE_LEAD = 1.75      # cap heights between the two lines' baselines

# The profile-grid proof (round 3): which recorded elements must carry the hook at grid size, and their floors in
# px of cap height at 124 px wide. Texture elements (eyebrow, tagline, dimension word, disclosure) may go small,
# but must never be the only carrier of a fact-bearing word (a qualifier, a unit, a date).
# r7: the hedge line ("qualifier") is a hook like the monument's kicker: it carries the figure's qualifier.
GRID_HOOK = {"title_1": 9, "title_2": 9, "figure": 9, "figure_unit": 9, "figure_qualifier": 9,
             "qualifier": QUALIFIER_GRID_CAP, "brand": MASTHEAD_GRID_CAP}
GRID_TEXTURE = ("eyebrow", "tagline", "figure_dim", "provenance")
CROP_34 = (0, 240, 1080, 1680)


def grid_proof(im, lay):
    """The cover as the profile grid shows it: each recorded text element's cap height in px at 124 and 180 px
    wide (the centred 3:4 crop), its contrast measured on the downscaled tile, whether it stays inside the crop,
    the tile's mean L* (colour-balance QA reads it), and what fails: a hook element under its floor, outside the
    crop or under GRID_CONTRAST on the tile, or a fact-bearing word carried only by texture."""
    import re

    import numpy as np

    from .typography import _FIGURE
    plates = lay.meta.get("plates", {}) if lay is not None else {}
    crop = im.convert("RGB").crop(CROP_34)
    small = crop.resize((GRID_WIDTHS[0], round(GRID_WIDTHS[0] * 4 / 3)), Image.Resampling.LANCZOS)
    blurred = Image.new("RGB", im.size)
    blurred.paste(small.resize(crop.size, Image.Resampling.BICUBIC), CROP_34[:2])
    elements, fails, hook_fails = {}, [], []
    for name, ps in plates.items():
        ps = [p for p in ps if not p.empty]
        if not ps:
            continue
        cap = min(cap_h_of(p.font) for p in ps)
        inside = all(CROP_34[1] <= p.box[1] and p.box[3] <= CROP_34[3] for p in ps)
        # the tile's ring (TextPlate.measure_ring: at 124 px a glyph and its surround fall inside one tile pixel)
        vals = [v for v in (getattr(p, "measure_ring", p.measure)(blurred) for p in ps) if v is not None]
        kind = "hook" if name in GRID_HOOK else "texture"
        elements[name] = {"class": kind, "cap_124": round(cap * GRID_SCALE[0], 1),
                          "cap_180": round(cap * GRID_SCALE[1], 1), "inside_34": inside,
                          "contrast_124": round(min(vals), 2) if vals else None,
                          "text": " ".join(p.text for p in ps)}
        if kind == "hook":
            found = []
            if cap * GRID_SCALE[0] < GRID_HOOK[name]:
                found.append(f"{name} {cap * GRID_SCALE[0]:.1f} px cap at 124 px (floor {GRID_HOOK[name]})")
            if not inside:
                found.append(f"{name} outside the 3:4 crop")
            # cycle 0 round 7 (sweep S1: a monument tile passed at 3.80 / 3.63 / 2.87:1): a hook element's contrast
            # on the tile is part of the proof, at contrast.grid (3.0: the tile is a preview, read at large-text AA)
            c124 = elements[name]["contrast_124"]
            if c124 is not None and c124 < GRID_CONTRAST:
                found.append(f"{name} {c124:.2f}:1 at 124 px (gate {GRID_CONTRAST})")
            fails += found
            hook_fails += found
    hook_text = " ".join(e["text"] for e in elements.values() if e["class"] == "hook").upper()

    def facts(text):
        # A qualifier is fact-bearing only attached to a figure ("OVER 4,000", "EST. 144"): as a plain word
        # ("LOOKING OVER THE CITY", "SOME SAY") it is texture like any other (r5 review: false grid warnings).
        out = {m.group(1).upper() for m in re.finditer(r"(?<![\w.])(" + _QUAL + r")\s*(?=\d)", text, re.IGNORECASE)}
        out |= {m.group(0).upper() for m in _FIGURE.finditer(text)}
        out |= set(_YEAR.findall(text))
        return out
    for name, e in elements.items():
        if e["class"] == "texture" and name != "provenance":
            alone = {w for w in facts(e["text"]) if w not in hook_text}
            if alone:
                fails.append(f"{name} alone carries {', '.join(sorted(alone))} (texture at grid size)")
    arr = np.asarray(small, dtype=np.float32) / 255
    lin = np.where(arr <= 0.04045, arr / 12.92, ((arr + 0.055) / 1.055) ** 2.4)
    y = lin @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    lstar = (116 * np.where(y > 0.008856, np.cbrt(y), 7.787 * y + 16 / 116) - 16).mean()   # per pixel (round 7)
    # hook_fails (round 4): the failures the engine acts on (a tighter draw, then another fitting style); a fact
    # carried only by texture stays a warning.
    return {"ok": not fails, "fails": fails, "hook_fails": hook_fails, "elements": elements,
            "mean_lstar": round(float(lstar), 1)}


def delivered_contrast(lay, path):
    """Measure the actual saved JPEG, including compression, before saving layout."""
    from .typography import remeasure
    with Image.open(path) as saved:
        remeasure(lay, saved.convert("RGB"), lay.meta.get("plates", {}))
