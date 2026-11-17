"""Design-freeze cycle 0, round 7, lane HEADING (preview panel 1, C:/tmp/qf-handoff/queue/CYCLE0.preview-panel-1.md):
the one heading-break engine (src/heading.py) and the type ladder / band it sets on.

- F1: one engine for every renderer that breaks a heading or a display line; a hard never-end set (articles, of,
  in, from, near, to, at, on, by, with ...); the break goes BEFORE the stop word; function words opening the accent
  line stay bone; no lone-word row. The panel's anchors: THE EYE / OF THE SAHARA (OF THE bone), LINES IN THE SAND
  (one line at the L tier, LINES / IN THE SAND on the locked cover), THE DESERT EYE / SEEN FROM SPACE.
- F5: the accent rule is independent of the wrap: a lockup's payoff run is gold on one line or two; an interior
  heading is never gold but for a stated quantity.
- F2 / F3 / F4 / F10: covers locked at display_xl (>= 1.08 x display_l); interiors on display_l and one fallback tier;
  one Story title size (the least over its frames, never under 106); the body hangs from the heading's last baseline
  by one gap token, so a deck's heading tops take two positions.
- F18 / F20 / F21: the 9:16 chapter card's lone row, one kicker, the cover / YouTube lockup's fixed ratio.

Synthetic inputs only (CI has no output/). Invariants and the panel's measured cases, never a pinned pixel.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from src import brand, brand_system, heading
from src.typography import get_font

ROOT = Path(__file__).resolve().parent.parent
_D = ImageDraw.Draw(Image.new("RGB", (8, 8)))
COVER_W = (1440 - 2 * 112) * 3 / 4          # the IG cover's measure in design px (planned on the 1080 slide)
XL = brand_system.TYPE["carousel"]["display_xl"] * 3 / 4
L, L_FB = (brand_system.TYPE["carousel"]["display_l"] * 3 / 4, brand_system.TYPE_FALLBACK["carousel"]["display_l"] * 3 / 4)


def _measure(t: str, size: float) -> float:
    return _D.textlength(t, font=get_font(brand.DISPLAY_FONT, size))


def _gold(form) -> list[str]:
    return [t for line in form.runs() for t, role in line if role in heading.GOLD_ROLES]


# ------------------------------------------------------------------ F1: the engine's rules (the panel's anchors)

def test_the_never_set_is_the_operators_and_hard():
    never = brand_system.LINE_END_NEVER
    assert {"a", "an", "the", "of", "in", "from", "near", "to", "at", "on", "by", "with"} <= never
    assert not never & brand_system.LINE_END_LAST_RESORT          # of / in are never, no longer a last resort
    from src import design_cards, multi_format, video_composer
    assert never <= design_cards.W1_STOPS and never <= multi_format._DANGLING
    assert never <= video_composer.DANGLING_ENDS                  # caption bursts: the 16:9 burst ending on "near"


def test_the_richat_cover_anchor_breaks_before_the_stop_word_with_of_the_in_bone():
    form = heading.set_heading("THE EYE OF THE SAHARA", _measure, COVER_W, (XL,), lockup=True, locked_lines=3)
    assert form.lines == ("THE EYE", "OF THE SAHARA") and form.size == XL
    assert form.runs()[1] == [("OF THE", heading.LEAD), ("SAHARA", heading.ACCENT)]
    assert _gold(form) == ["SAHARA"]


def test_the_nazca_anchor_one_line_at_the_l_tier_and_lines_in_the_sand_on_the_locked_cover():
    interior = heading.set_heading("LINES IN THE SAND", _measure, COVER_W, (L, L_FB))
    assert interior.lines == ("LINES IN THE SAND",) and interior.size in (L, L_FB)
    cover = heading.set_heading("LINES IN THE SAND", _measure, COVER_W, (XL,), lockup=True, locked_lines=3)
    assert cover.size == XL
    assert cover.lines in (("LINES IN THE SAND",), ("LINES", "IN THE SAND"))
    assert _gold(cover) == ["SAND"]                               # the payoff run, on one line or two (F5)


@pytest.mark.parametrize("text", [
    "THE EYE OF THE SAHARA", "LINES IN THE SAND", "CRATED IN A BASEMENT", "A CITY NEAR THE RIVER MOUTH",
    "CARVED FROM SOLID SALT", "THE TOMB OF THE UNKNOWN KING", "BUILT WITH NO MORTAR AT ALL", "SEEN FROM SPACE",
    "THE WALLS BY THE OLD SEA GATE", "ARCHAEOLOGICAL EVIDENCE OF A FLOOD"])
@pytest.mark.parametrize("width", [500, 700, 900, 1216])
def test_no_form_ends_a_line_on_a_never_word_or_leaves_a_lone_word_row(text, width):
    for sizes, kw in (((XL,), {"lockup": True, "locked_lines": 3}), ((L, L_FB), {})):
        form = heading.set_heading(text, _measure, width * 3 / 4, sizes, **kw)
        if form is None:
            continue
        lines = form.lines
        assert all(ln.split()[-1].lower() not in brand_system.LINE_END_NEVER for ln in lines[:-1]), lines
        if not form.record.get("locked_last_resort") and len(text.split()) >= 3 and len(lines) > 1:
            assert len(lines[-1].split()) >= 2, lines
        assert form.size in sizes                                 # the ladder, never a fitted size
        assert all(_measure(t, form.size) <= width * 3 / 4 for t in lines)


def test_a_value_never_splits_from_its_unit_and_a_stated_quantity_is_gold():
    for width in (420, 520, 640):
        form = heading.set_heading("ABOUT 25+ MILES WIDE ACROSS", _measure, width, (L, L_FB))
        if form is None:
            continue
        assert all(not ln.endswith(("25+", "ABOUT")) for ln in form.lines), form.lines
    interior = heading.set_heading("25+ MILES WIDE", _measure, COVER_W, (L, L_FB))
    assert _gold(interior) == ["25+ MILES"]                       # ruling 3: Anton gold; the rest bone (F5)


def test_the_ladder_order_one_line_first_then_the_fallback_then_a_break():
    # one line at L when it fits; else one line at the fallback tier; else a two-line break at L
    short = heading.set_heading("ORBITAL LANDMARK", _measure, COVER_W, (L, L_FB))
    assert len(short.lines) == 1 and short.size in (L, L_FB)
    assert short.tier == (0 if _measure("ORBITAL LANDMARK", L) <= COVER_W else 1)
    long_ = heading.set_heading("BUILT BY HAND WITHOUT MORTAR", _measure, COVER_W, (L, L_FB))
    assert len(long_.lines) == 2 and long_.size in (L, L_FB)
    # no valid form on the ladder: the recorded last resort (off the ladder, then a lone row), never a refusal
    last = heading.set_heading("THE MYSTERY OF THE DESERT RINGS", _measure, COVER_W, (L, L_FB),
                               last_resort_floor=round(L_FB * 0.9))
    assert last is not None and (last.record.get("off_ladder") or last.record.get("locked_last_resort"))
    assert heading.set_heading("THE MYSTERY OF THE DESERT RINGS", _measure, COVER_W, (L, L_FB)) is None


def test_story_fields_repair_the_authored_seam():
    from src.multi_format import story_fields
    assert story_fields({"line1": "Seen from", "line2": "space"}) == ("SEEN", "FROM SPACE")
    assert story_fields({"line1": "The desert eye", "line2": "seen from space"}) == ("THE DESERT EYE", "SEEN FROM SPACE")
    l1, l2, lead = heading.lockup_fields("THE EYE OF", "THE SAHARA")
    assert (l1, l2, lead) == ("THE EYE", "OF THE SAHARA", 2)


# ------------------------------------------------------------------ the carousel: F2, F3, F4, F5

def _slide(tmp_path, archetype, card, frame=2, photo_rgb=(70, 70, 70)):
    from src.multi_format import MultiFormatGenerator
    photo = tmp_path / f"p{photo_rgb[0]}.jpg"
    if not photo.exists():
        Image.new("RGB", (1600, 2000), photo_rgb).save(photo)
    _, lay = MultiFormatGenerator()._render_carousel_slide(archetype, photo, photo, card, 1 if archetype != "cover" else 0,
                                                           7, {}, "", False, subject="TEST", heading_frame=frame)
    return lay


def _heading_lines(lay, name="heading"):
    """The drawn heading as lines: its plates (one per colour run) joined per baseline, left to right."""
    by_y: dict = {}
    for p in lay.plates[name]:
        by_y.setdefault(round(p.xy[1]), []).append(p)
    return [(y, " ".join(p.text for p in sorted(ps, key=lambda q: q.xy[0])), ps) for y, ps in sorted(by_y.items())]


def test_the_carousel_cover_is_locked_at_display_xl_and_sets_the_ruled_form(tmp_path):
    lay = _slide(tmp_path, "cover", {"heading": "THE EYE OF THE SAHARA", "body": "A concentric formation in Mauritania."})
    lines = _heading_lines(lay)
    assert [t for _, t, _ in lines] == ["THE EYE", "OF THE SAHARA"]
    xl = brand_system.TYPE["carousel"]["display_xl"]
    assert all(abs(p.font.size - xl) < 0.01 for _, _, ps in lines for p in ps)
    assert xl >= brand_system.SPEC["heading.cover_over_interior"] * brand_system.TYPE["carousel"]["display_l"]
    gold = [p.text for _, _, ps in lines for p in ps if tuple(p.fill) == tuple(brand.GOLD)]
    assert gold == ["SAHARA"]
    assert lay.meta["heading"]["lines"] == ["THE EYE", "OF THE SAHARA"] and lay.meta["heading"]["lockup"]


def test_a_long_cover_heading_keeps_display_xl_and_the_break_gives_way(tmp_path):
    lay = _slide(tmp_path, "cover", {"heading": "THE IMPOSSIBLE GEOMETRY OF THE DESERT RINGS", "body": "A short body."})
    xl = brand_system.TYPE["carousel"]["display_xl"]
    assert all(abs(p.font.size - xl) < 0.01 for p in lay.plates["heading"]) or lay.meta["heading"].get("off_ladder")
    assert len({round(p.xy[1]) for p in lay.plates["heading"]}) >= 2


@pytest.mark.parametrize("text", ["ORBITAL LANDMARK", "IMPACT THEORY DISPROVED", "BUILT BY HAND WITHOUT MORTAR"])
def test_interior_headings_sit_on_the_ladder_and_are_never_gold(tmp_path, text):
    lay = _slide(tmp_path, "immersive", {"heading": text, "body": "A body of a few words that wraps once or twice."})
    sizes = {round(p.font.size, 2) for p in lay.plates["heading"]}
    ladder = {round(brand_system.TYPE["carousel"]["display_l"], 2),
              round(brand_system.TYPE_FALLBACK["carousel"]["display_l"], 2)}
    assert len(sizes) == 1 and sizes <= ladder                     # F4: two values, never a fit
    assert not any(tuple(p.fill) == tuple(brand.GOLD) for p in lay.plates["heading"])   # F5: interiors bone


def test_the_body_hangs_from_the_headings_last_baseline_by_one_gap(tmp_path):
    """F3: on one deck frame, a one-line and a two-line heading put their body the same gap under their last
    baseline; the heading's last baseline is shared (the band), so the tops take two positions only."""
    gaps, lasts, firsts = set(), set(), {}
    body = "The concentric formation serves as a prominent desert landmark."
    for text in ("ORBITAL LANDMARK", "BUILT BY HAND WITHOUT MORTAR", "RINGS OF ROCK"):
        lay = _slide(tmp_path, "immersive", {"heading": text, "body": body}, frame=2)
        heads = [y for y, _, _ in _heading_lines(lay)]
        b0 = min(round(p.xy[1]) for p in lay.plates["body"])
        gaps.add(b0 - heads[-1])
        lasts.add(heads[-1])
        firsts.setdefault(len(heads), set()).add(heads[0])
    b_px = brand_system.TYPE["carousel"]["body"]
    assert gaps == {round(b_px * brand_system.tok("band.body_gap"))}
    assert len(lasts) == 1 and all(len(v) == 1 for v in firsts.values())


# ------------------------------------------------------------------ the Story: F10

def test_one_story_title_size_the_least_over_its_frames_never_under_106():
    from src.multi_format import story_title_px
    floor = brand_system.GATES["heading.story_one_line_floor"]
    design = brand_system.TYPE["tall"]["display_xl"]
    short = {"line1": "Old rooms", "line2": "under salt", "kicker": "", "body": ""}
    longer = {"line1": "The desert eye", "line2": "seen from space", "kicker": "", "body": ""}
    longest = {"line1": "Brick and mortar of the", "line2": "whispering gallery dome", "kicker": "", "body": ""}
    a, b = story_title_px([short]), story_title_px([longer])
    assert a == design and floor <= b < design
    assert story_title_px([short, longer]) == min(a, b)           # one size: the least over the frames
    assert story_title_px([short, longer, longest]) >= floor      # never under the ruled floor


def test_the_richat_story_anchor_one_line_a_field_at_the_story_size(tmp_path):
    from src.multi_format import MultiFormatGenerator, story_title_px
    photo = tmp_path / "s.jpg"
    Image.new("RGB", (1200, 2100), (60, 55, 50)).save(photo)
    slides = [{"kicker": "Orbital mystery", "line1": "The desert eye", "line2": "seen from space",
               "body": "A colossal concentric formation marks the vast desert."},
              {"kicker": "Up close", "line1": "Rings of rock", "line2": "worn by wind",
               "body": "Erosion exposed the layers over millions of years."}]
    px = story_title_px(slides)
    for i, slide in enumerate(slides):
        _, lay = MultiFormatGenerator()._render_story_frame(photo, slide, i, 2, {}, title_px=px)
        assert lay.meta["title_px"] == px == lay.meta["story_title_px"] and not lay.meta["story_size_off"]
        t1 = [p.text for p in lay.plates["title_1"]]
        t2 = [p for p in lay.plates["title_2"]]
        if i == 0:
            assert t1 == ["THE DESERT EYE"] and " ".join(p.text for p in t2) == "SEEN FROM SPACE"
            assert len({round(p.xy[1]) for p in t2}) == 1          # one line a field
        assert all(tuple(p.fill) == tuple(brand.GOLD) for p in t2 if p.text.split()[0].lower() not in
                   brand_system.LINE_END_NEVER)


# ------------------------------------------------------------------ the Reel cover and YouTube lockups: F21

def test_the_reel_cover_lockup_takes_a_fixed_ratio_and_the_repaired_seam():
    from src import cover_design
    got, rec = cover_design._lockup(_D, {"line1": "Carved from", "line2": "solid salt"}, 900, 108)
    assert rec["line1"] == "CARVED" and rec["line2"] == "FROM SOLID SALT"
    f1, _ = got["line1"]
    f2, runs2 = got["line2"]
    assert f2.size == round(f1.size * brand_system.tok("lockup.line_ratio"))
    assert runs2 == [[("FROM", heading.LEAD), ("SOLID SALT", heading.ACCENT)]]
    with brand.use(brand.DEFAULT_BRAND.derive("ratio", system={"lockup.line_ratio": 1.3})):
        got, _ = cover_design._lockup(_D, {"line1": "Old", "line2": "salt"}, 900, 100)
        assert got["line2"][0].size == 130


def test_the_youtube_lockup_sits_on_the_wide_ladder_with_the_payoff_gold():
    from src import youtube_thumbnail
    ladder = (round(brand_system.TYPE["wide"]["display_xl"]), round(brand_system.TYPE_FALLBACK["wide"]["display_xl"]))
    rows, f1, f2, rec = youtube_thumbnail._lockup(_D, "SAHARA EYE", "", 900)
    assert rec["tier"] in (0, 1) and f1.size in ladder
    rows, f1, f2, rec = youtube_thumbnail._lockup(_D, "THE SECRET WEAPON", "", 900)
    assert rec["tier"] in (0, 1) or rec.get("off_ladder")          # below the ladder only as a recorded last resort
    gold = [t for _, runs in rows for _, t, c in runs if tuple(c) == tuple(brand.GOLD)]
    assert gold == ["WEAPON"]                                      # the payoff, on one line (F5)
    rows, f1, f2, rec = youtube_thumbnail._lockup(_D, "THE EYE OF", "THE SAHARA", 900)
    assert [" ".join(t for _, t, _ in runs) for _, runs in rows] == ["THE EYE", "OF THE SAHARA"]
    assert f2.size == round(f1.size * brand_system.tok("lockup.line_ratio"))


def test_the_youtube_fallback_tier_holds_the_feed_floor():
    from src import youtube_thumbnail
    from src.typography import cap_height
    fb = brand_system.TYPE_FALLBACK["wide"]["display_xl"]
    cap = cap_height(get_font(brand.DISPLAY_FONT, round(fb)))
    assert cap * youtube_thumbnail.FEED_WIDTHS[0] / youtube_thumbnail.W >= youtube_thumbnail.HERO_FLOOR


# ------------------------------------------------------------------ the cards: F18

def test_the_cards_display_wrap_takes_only_the_engines_forms():
    from src import design_cards
    f = get_font(brand.DISPLAY_FONT, 120)
    for text in ("ARCHAEOLOGICAL EVIDENCE OF THE FLOOD", "THE FINAL EVIDENCE", "BUILT WITHOUT ANY RINGS",
                 "A CITY NEAR THE SEA"):
        for measure in (520, 640, 760):
            lines = design_cards._wrap(text, f, measure, 3, even=True)
            if lines:
                assert heading.valid(lines), (text, measure, lines)
    assert design_cards.CHAPTER_NUMERAL_PX == {False: brand_system.TYPE["tall"]["figure"],
                                               True: brand_system.TYPE["wide"]["figure"]}


# ------------------------------------------------------------------ one kicker: F20

def test_one_kicker_on_every_surface():
    from src import cover_design, design_cards, multi_format, youtube_thumbnail
    track = brand_system.tok("kicker.track")
    # the cover eyebrow: the kicker's tracking and lead rule, no tab
    rows, _ = cover_design._rows(Image.new("RGB", (1080, 1920)), {"eyebrow": "Subterranean marvel", "line1": "Carved",
                                                                  "line2": "solid salt"}, 900, "badge", False)
    eb = next(r for r in rows if r["name"] == "eyebrow")
    assert eb["kicker"] and eb["track"] == pytest.approx(track * eb["font"].size) and not eb.get("tab")
    # the cards: the lead rule before the label, the canvas's kicker size and tracking
    card = design_cards._Card("chapter", design_cards._Geo(1080, 1920), Image.new("RGB", (1080, 1920), brand.INK))
    design_cards._kicker(card, "kicker", "CHAPTER", 113, 400, brand.GOLD, brand.GOLD, (0.1, 0.3), (0.15, 0.4))
    els = {e.name: e for e in card.elements}
    assert els["kicker_rule"].box[2] < els["kicker_label"].box[0]
    assert els["kicker_rule"].box[3] - els["kicker_rule"].box[1] < 12                        # a flat bar, not a rule above
    # the carousel and the Story size from the ladder; one gap token
    assert multi_format.kicker_gap(40) == round(brand_system.tok("kicker.gap") * 40)
    # YouTube: the text face at the kicker's weight
    f = youtube_thumbnail._kicker_font("SAHARA EYE", 900)
    assert f is not None and Path(f.path).name == Path(get_font(brand.TEXT_FONT, 80, weight=800).path).name


# ------------------------------------------------------------------ a renderer cannot break a heading without the engine

RENDERERS = ("multi_format", "cover_design", "youtube_thumbnail", "design_cards")
PRIMITIVES = {"balanced_wrap": None, "wrap_text": None, "fit_text": 5, "probe_text": 5, "_fit_text": 5,
              "fit_lines": 4}           # name -> the max_lines argument's position (None: always multi-line)
ENGINE_BACKED = {"design_cards": {"_wrap"}, "multi_format": {"split_balanced"}}


def _display(arg) -> bool:
    return "DISPLAY_FONT" in ast.unparse(arg)


def test_no_renderer_breaks_a_display_line_outside_the_engine():
    """Statically: in the renderers, no call breaks display type onto lines (a wrap primitive with the display face and
    a line count other than a literal 1) except in a function the engine backs, and those call the engine."""
    offenders = []
    for mod in RENDERERS:
        tree = ast.parse((ROOT / "src" / f"{mod}.py").read_text(encoding="utf-8"))
        for fn in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
            backed = fn.name in ENGINE_BACKED.get(mod, set())
            for call in (c for c in ast.walk(fn) if isinstance(c, ast.Call)):
                name = getattr(call.func, "id", getattr(call.func, "attr", None))
                if name not in PRIMITIVES or not any(_display(a) for a in call.args):
                    continue
                pos = PRIMITIVES[name]
                lines = call.args[pos] if pos is not None and len(call.args) > pos else None
                if pos is not None and isinstance(lines, ast.Constant) and lines.value == 1:
                    continue
                if pos is not None and lines is None:
                    continue
                if not backed:
                    offenders.append(f"{mod}:{fn.name}:{call.lineno} {name}")
            if backed:
                src = ast.unparse(fn)
                assert "heading" in src or "ideal_seam" in src, f"{mod}.{fn.name} is engine-backed but never calls it"
    # the last resorts the lockups record (off_ladder) are the one allowed bypass, each recorded
    allowed = {"cover_design:_lockup"}
    assert {o.rsplit(":", 1)[0] for o in offenders} <= allowed, offenders


def test_every_drawn_heading_is_a_form_the_engine_issued(tmp_path, monkeypatch):
    """At run time: the carousel cover and interior, the Story frame and the Reel cover / YouTube lockups draw only
    lines the engine handed out (heading._ISSUED instrumentation)."""
    issued: list = []
    monkeypatch.setattr(heading, "_ISSUED", [lambda f: issued.append(f.lines)])
    for archetype, text in (("cover", "THE EYE OF THE SAHARA"), ("immersive", "THE MYSTERY OF THE DESERT RINGS"),
                            ("immersive", "ORBITAL LANDMARK")):
        lay = _slide(tmp_path, archetype, {"heading": text, "body": "A body line of some words."})
        drawn = tuple(t for _, t, _ in _heading_lines(lay))
        assert drawn in issued, (drawn, issued[-3:])
    from src.multi_format import MultiFormatGenerator
    photo = tmp_path / "st.jpg"
    Image.new("RGB", (1200, 2100), (60, 55, 50)).save(photo)
    _, lay = MultiFormatGenerator()._render_story_frame(photo, {"kicker": "K", "line1": "The desert eye",
                                                                "line2": "seen from space", "body": "A body."}, 0, 2, {})
    for name in ("title_1", "title_2"):
        drawn = tuple(t for _, t, _ in _heading_lines(lay, name))
        assert drawn in issued, (name, drawn)
    issued.clear()
    from src import youtube_thumbnail
    youtube_thumbnail._lockup(_D, "THE SECRET WEAPON", "", 900)
    assert issued and all(heading.valid(ls, lone_ok=True) for ls in issued)


def test_the_engine_tokens_are_runtime_and_bounded():
    for key in ("kicker.gap", "band.body_gap", "band.body_gap_tall", "lockup.line_ratio", "card.tall_centre"):
        assert key in brand_system.SYSTEM_TOKENS and key in brand_system.TOKEN_RANGES
        lo, hi = brand_system.TOKEN_RANGES[key]
        assert lo <= brand_system.SYSTEM_TOKENS[key] <= hi


# ------------------------------------------------------------------ sweep S1 (CYCLE0-sweep-paths.codex.md, items 1-3)

def test_a_story_title_is_never_drawn_under_the_ruled_floor(tmp_path):
    """Item 1: long fields re-break at the floor or the frame is refused; never a title under 106 px."""
    from src.multi_format import MultiFormatGenerator, story_title_px
    from src.typography import TextFitError
    floor = brand_system.GATES["heading.story_one_line_floor"]
    photo = tmp_path / "s.jpg"
    Image.new("RGB", (1200, 2100), (60, 55, 50)).save(photo)
    for slide in ({"kicker": "K", "line1": "The extraordinarily long and winding underground passages",
                   "line2": "carved beneath the ancient mountain city walls", "body": "A body line."},
                  {"kicker": "K", "line1": "Brick and mortar of the", "line2": "whispering gallery dome",
                   "body": "A body line."}):
        px = story_title_px([slide])
        assert px >= floor
        for tight in (False, True):
            try:
                _, lay = MultiFormatGenerator()._render_story_frame(photo, slide, 0, 2, {}, tight=tight, title_px=px)
            except TextFitError:
                continue                                           # refused whole (QF-23), never drawn smaller
            sizes = [p.font.size for n in ("title_1", "title_2") for p in lay.plates.get(n, [])]
            assert sizes and min(sizes) >= floor, sizes


def test_every_kicker_passes_its_gate_or_is_not_drawn():
    """Item 2: the one kicker function fails closed like the masthead: gold, else bone, else nothing (recorded)."""
    import numpy as np

    from src import multi_format
    from src.typography import get_font as gf
    font = gf(brand.TEXT_FONT, 45, weight=800)
    dark = Image.new("RGB", (900, 200), brand.INK)
    plate, rec = multi_format.plan_kicker(dark, (60, 120), "THE RIDDLE", font, brand.GOLD, "ls", 6)
    assert plate is not None and rec["variant"] == "colour" and rec["contrast"] >= rec["gate"] == 5.5
    grey = Image.new("RGB", (900, 200), (128, 128, 128))
    plate, rec = multi_format.plan_kicker(grey, (60, 120), "THE RIDDLE", font, brand.GOLD, "ls", 6, max_halo=0.0)
    assert plate is None and rec["shown"] is False and "kicker gate" in rec["reason"]
    # drawn through draw_kicker: nothing at all is drawn under the gate (no rule, no words)
    from src.still_layout import Layout
    lay = Layout("carousel", (900, 200), (0, 0, 900, 200))
    real = multi_format.plan_text
    try:
        multi_format.plan_text = lambda *a, **k: real(*a, **{**k, "max_halo": 0.0})
        out, _ = multi_format.draw_kicker(grey.copy(), 60, 120, "THE RIDDLE", font, brand.GOLD, lay)
    finally:
        multi_format.plan_text = real
    assert np.array_equal(np.asarray(out.convert("RGB")), np.asarray(grey))
    assert lay.meta["kickers"]["kicker"]["shown"] is False


def test_a_brand_kicker_colour_under_the_gate_is_refused():
    with pytest.raises(ValueError, match="kicker gate"):
        brand.DEFAULT_BRAND.derive("ink kicker", system={"kicker.colour": "INK"})
    brand.DEFAULT_BRAND.derive("teal kicker", system={"kicker.colour": "TEAL"})


def test_the_cover_eyebrow_is_planned_at_the_kicker_gate():
    """Item 3: a cover's eyebrow (the monument's too) is the kicker role at the kicker gate, not the 5.1 display tier."""
    from src import cover_design
    from src.still_layout import Layout
    im = Image.new("RGBA", (1080, 1920), brand.INK + (255,))
    rows, _ = cover_design._rows(im, {"eyebrow": "Subterranean marvel", "line1": "Carved", "line2": "solid salt"}, 900,
                                 "badge", False)
    lay = Layout("cover", (1080, 1920), (60, 60, 1020, 1860))
    cover_design._paint(im, rows, 84, 1100, False, lay)
    rec = lay.meta["kickers"]["eyebrow"]
    assert rec["gate"] == brand_system.GATES["contrast.kicker"] and rec["shown"] and rec["contrast"] >= rec["gate"]
    src = (ROOT / "src" / "cover_design.py").read_text(encoding="utf-8")
    assert 'put("eyebrow"' not in src                              # the monument's eyebrow goes through plan_kicker


@pytest.mark.parametrize("ground", [(24, 28, 34), (243, 237, 226), (200, 190, 170)], ids=["ink", "bone", "bright"])
def test_the_end_screen_kicker_takes_the_one_gate(ground, monkeypatch):
    """Sweep (tokens #2): WATCH NEXT drew at 5.18 on bone and 5.39 on a bright photo; it now passes the kicker gate
    in its colour or bone, or is not drawn (recorded)."""
    from src import end_card
    monkeypatch.setattr(end_card, "END_SCREEN_SECONDS", 0.2)
    stats: dict = {}
    list(end_card.frames(Image.new("RGB", (1920, 1080), ground), "Share this with a friend.", stats))
    rec = stats["kicker_gate"]
    assert rec["gate"] == brand_system.GATES["contrast.kicker"]
    if rec["shown"]:
        assert rec["contrast"] >= rec["gate"]
    else:
        assert "kicker gate" in rec["reason"]
