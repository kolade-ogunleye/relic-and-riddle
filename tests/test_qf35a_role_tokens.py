"""QF-35a (design-freeze ruling Q11, 2026-11-10): the frozen designs read their palette, font families and masthead
strings from ONE brand configuration object (src/brand.py BrandConfig) at call time.

- The default configuration holds today's values (pixel identity is measured on real runs and the evidence
  fixtures: docs/reviews/quality-fixes-2026-11/QF-35a/).
- A source scan of the frozen design modules finds no colour literal, font-family literal or brand string outside
  the configuration (ALLOW lists what must stay, each with its reason), and no import-time copy of a role (a
  `from .brand import GOLD`, a module-level `X = brand.GOLD`, a default argument `fill=brand.GOLD`), which would
  ignore a later brand.
- A second brand (other colours, another bundled face in the display role, another masthead) renders through the
  frozen designs without a code change, and every design module's old names follow it.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent

# The frozen design surfaces (FREEZE-SCOPE 2026-11-10 and HANDOFF section 8): covers, grid tiles and the YouTube
# thumbnail; captions; carousels, galleries and Stories; the loop bridge and end screen; then-and-now; first frame,
# chapter, myth and quote cards; maps, timelines, scale, stat cards, cross-section, routes; photo callouts; and the
# art direction's accent that colours the graphics; and (QF-35a port onto LAND-29) the five-event dense timeline.
FROZEN = ("graphics", "design_cards", "multi_format", "cover_design", "youtube_thumbnail", "end_card", "then_now",
          "callouts", "video_composer", "art_direction", "timeline_dense", "masthead", "brand_system")

# What stays a literal, keyed (module, enclosing function or assigned name, literal source) -> reason.
ALLOW = {
    # sizes, steps and boxes that happen to be three or four small integers (not colours)
    ("graphics", "COUNTRY_SMALL_PX", "(24, 22, 20)"): "px steps",
    ("graphics", "NEIGHBOUR_PX_STEPS", "(0, 2, 4, 6)"): "px steps",
    ("graphics", "WIDE_PRIMARY_MIN", "(48, 28, 22)"): "px minimums",
    ("graphics", "ROUTE_NAME_STEPS", "(34, 32, 30, 28)"): "px steps",
    ("graphics", "_stat_clip", "(0, 0, 44, 4)"): "a bar box default",
    ("graphics", "name_places", "(1, 2, 3)"): "line counts",
    ("graphics", "name_places", "(4, 5, 6, 7)"): "preference ranks",
    ("graphics", "name_places", "(0, 3, 4)"): "an empty array's shape",
    ("graphics", "_rock_ground", "[3, 3, 4]"): "a brightness ramp added to the brand INK sky, not a colour",
    ("graphics", "__init__", "(0, 0, 1, 1)"): "a bounding box fallback",
    ("design_cards", "plan_chapter_card", "(120, 112, 104, 96)"): "type sizes",
    ("multi_format", "MAP_FADE", "(160, 110, 56)"): "fade lengths",
    ("multi_format", "STRENGTH_FIGURE", "(3, 2, 1)"): "ranks",
    ("end_card", "SHARE_SIZES", "(58, 56, 54)"): "type sizes",
    # the design system's declared surface size systems (brand_system.DEVIATIONS, cycle 0 round 2)
    ("brand_system", "DEVIATIONS", "(128, 120, 112)"): "type sizes (hook, 3 lines)",
    ("brand_system", "DEVIATIONS", "(58, 56, 54)"): "type sizes (end card share)",
    ("brand_system", "DEVIATIONS", "(64, 60, 56)"): "type sizes (end screen hand-off)",
    ("brand_system", "DEVIATIONS", "(190, 96, 4)"): "type sizes (YouTube headline from, floor, step)",
    ("brand_system", "DEVIATIONS", "(100, 164, 96)"): "type sizes (cover titles min, max, floor)",
    ("brand_system", "DEVIATIONS", "(138, 124, 150, 140)"): "type sizes (carousel heading IG / FB)",
    ("brand_system", "DEVIATIONS", "(150, 106, 120)"): "type sizes (Story title)",
    ("end_card", "LANDSCAPE_SHARE_SIZES", "(64, 60, 56)"): "type sizes",
    ("end_card", "layout", "(44, 40, 36)"): "type sizes",
    ("then_now", "geometric_match", "[0, 0, 1]"): "a homography row",
    ("then_now", "register", "[1, 1, 1, 1]"): "a row of homogeneous coordinates",
    ("callouts", "SHADOW_DY", "(4, 14, 90)"): "shadow offset, blur and alpha",
    ("callouts", "LEADER_GAP", "(20, 32, 120, 15)"): "leader geometry",
    ("callouts", "LEADER_LEN", "(56, 80, 180, 260)"): "leader lengths",
    ("callouts", "LEADER_LEN", "(48, 70, 160, 240)"): "leader lengths",
    # model-facing marks: the vision check's inputs, never drawn on a delivered frame
    ("callouts", "VERIFY_PAD", "(128, 128, 128)"): "the verification crop's grey padding, sent to the vision check",
    ("callouts", "verify_crop", "(255, 40, 40)"): "the verification crosshair, sent to the vision check",
    ("callouts", "_ringed", "(255, 40, 40)"): "the verification ring, sent to the vision check",
    # analysis grounds, not drawn
    ("video_composer", "caption_contrast_record", "(255, 255, 255)"): "the worst-case ground a contrast is measured on",
    ("video_composer", "caption_contrast_record", "'white'"): "its name in the record",
    ("video_composer", "caption_contrast_record", "'black'"): "its name in the record",
    # a font literal that is a measure's definition, not a brand face
    ("video_composer", "_caption_text_width", "'Anton'"): ("the round-2 reference measure the burst cuts are defined on "
                                                          "(operator decision 2026-11-04); no renderer draws it"),
}

COLOUR_WORDS = {"white", "black", "red", "green", "blue", "gold", "yellow", "grey", "gray", "orange", "purple"}
HEX = re.compile(r"^#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?([0-9a-fA-F]{2})?$")
COLOUR_KWARGS = {"fill", "outline", "color", "colour", "stroke_fill", "fill_color", "text_color", "border_color"}
BRAND_WORDS = re.compile(r"relic|riddle", re.IGNORECASE)


def _families() -> set[str]:
    from src import font_assets, typography
    return set(typography.FONT_FILES) | set(font_assets.DERIVED_FAMILIES)


def _roles() -> set[str]:
    """Every name a design may read from brand: the palette roles, the font roles and the masthead strings (on a
    tree without a brand configuration object, brand.py's upper-case constants)."""
    from src import brand
    if hasattr(brand, "BRAND_PALETTE"):
        return set(brand.BRAND_PALETTE) | set(brand.FONT_ROLES) | set(brand.TEXT_ROLES)
    return {k for k, v in vars(brand).items() if k.isupper() and isinstance(v, (tuple, str))}


def _tree(module: str) -> ast.Module:
    return ast.parse((ROOT / "src" / f"{module}.py").read_text(encoding="utf-8"))


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}


def _owner(node: ast.AST, parents: dict) -> str:
    """The function a literal sits in, else the first name it is assigned to at module level."""
    chain, n = [], node
    while n in parents:
        n = parents[n]
        chain.append(n)
    for n in chain:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return n.name
    for n in chain:
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            target = n.targets[0] if isinstance(n, ast.Assign) else n.target
            first = target.elts[0] if isinstance(target, ast.Tuple) else target
            return ast.unparse(first)
    return "<module>"


def _docstrings(tree: ast.AST) -> set[int]:
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.body:
            first = n.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                out.add(id(first.value))
    return out


def _is_byte(e: ast.AST) -> bool:
    return isinstance(e, ast.Constant) and type(e.value) is int and 0 <= e.value <= 255


def literal_findings(module: str) -> list[tuple[str, int, str, str]]:
    """(kind, line, owner, literal) for every colour, font-family or brand-string literal in `module` outside ALLOW:
    a 3- or 4-element tuple or list whose first three elements are integers 0-255 (not pure black: a shade, a shadow
    or an empty layer, which no brand changes), a hex colour string, a colour word given as a colour, a registered
    family name, and any string naming the brand."""
    tree, families = _tree(module), _families()
    parents, docs = _parents(tree), _docstrings(tree)
    found = []
    for n in ast.walk(tree):
        kind = None
        if isinstance(n, (ast.Tuple, ast.List)) and len(n.elts) in (3, 4) and all(_is_byte(e) for e in n.elts[:3]):
            if any(e.value for e in n.elts[:3]):
                kind = "colour"
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
            p = parents.get(n)
            as_colour = (isinstance(p, ast.keyword) and p.arg in COLOUR_KWARGS) or (
                isinstance(p, ast.Call) and ast.unparse(p.func).endswith("Image.new") and n in p.args[2:3]) or (
                isinstance(p, ast.Tuple) and len(p.elts) == 2 and p.elts[0] is n)
            if HEX.match(n.value) or (n.value.lower() in COLOUR_WORDS and as_colour):
                kind = "colour"
            elif n.value in families:
                kind = "font"
            elif BRAND_WORDS.search(n.value) and n.value != n.value.lower():
                kind = "brand string"
        if kind:
            owner, src = _owner(n, parents), ast.unparse(n)
            if (module, owner, src) not in ALLOW:
                found.append((kind, n.lineno, owner, src))
    return found


def import_time_findings(module: str) -> list[tuple[int, str]]:
    """Every read of a brand role that binds at import: `from .brand import <role>` or `from .graphics import
    <role alias>` at module level, and a brand.<role> read evaluated at import (a module- or class-level statement,
    a default argument, a decorator); a function or lambda body reads it when called."""
    tree, roles = _tree(module), _roles()
    deferred = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            deferred.update(id(x) for st in n.body for x in ast.walk(st))
        elif isinstance(n, ast.Lambda):
            deferred.update(id(x) for x in ast.walk(n.body))
    found = []
    for st in tree.body:
        if isinstance(st, ast.ImportFrom) and st.module in ("brand", "graphics"):
            found.extend((st.lineno, f"from .{st.module} import {a.name}") for a in st.names
                         if a.name in roles or a.name in ("WHITE", "MUTED", "LIGHT", "GOLD_LINE"))
    for n in ast.walk(tree):
        if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in ("brand", "_brand")
                and n.attr in roles and id(n) not in deferred):
            found.append((n.lineno, ast.unparse(n)))
    return found


@pytest.mark.parametrize("module", FROZEN)
def test_no_colour_font_or_brand_literal_outside_the_brand_configuration(module):
    assert literal_findings(module) == []


@pytest.mark.parametrize("module", FROZEN)
def test_no_role_is_copied_at_import_time(module):
    assert import_time_findings(module) == []


def test_every_allowed_literal_still_exists():
    """The allow-list holds no stale entry (each must name a literal the scan still meets)."""
    met = set()
    for module in FROZEN:
        tree = _tree(module)
        parents = _parents(tree)
        for n in ast.walk(tree):
            if isinstance(n, (ast.Tuple, ast.List, ast.Constant)):
                met.add((module, _owner(n, parents), ast.unparse(n)))
    assert sorted(k for k in ALLOW if k not in met) == []


# ---------------------------------------------------------------- the configuration object
def _second_brand():
    """A test brand: every palette colour with its red and blue swapped (the aliases kept), the text face in the
    display role, another masthead and end-card lines."""
    from src import brand
    base = brand.DEFAULT_BRAND
    palette = {k: (v if isinstance(v, str) else (v[2], v[1], v[0])) for k, v in base.palette.items()}
    return base.derive("Second test brand", palette=palette, fonts={"display": "Montserrat"},
                       text={"masthead": "SECOND TEST BRAND", "follow": "FOLLOW FOR THE NEXT ONE",
                             "handoff": "The next one is right here."})


def test_default_brand_is_todays_values():
    from src import brand
    assert hasattr(brand, "BrandConfig") and hasattr(brand, "use"), "no brand configuration object"
    assert (brand.INK, brand.BONE, brand.GOLD, brand.STONE_LT) == ((11, 14, 18), (243, 237, 226), (242, 182, 48),
                                                                   (184, 177, 163))
    assert (brand.DISPLAY_FONT, brand.MASTHEAD_FONT, brand.TEXT_FONT, brand.DATA_FONT) == (
        "Anton", "Cinzel", "Montserrat", "IBM Plex Mono")
    assert brand.MASTHEAD == "RELIC & RIDDLE"
    assert brand.SEA == brand.INK and brand.GLOBE_LIMB == brand.GOLD     # aliases follow their role


def test_old_module_names_follow_the_active_brand():
    """The names the design modules exported before (graphics.WHITE, end_card.FOLLOW, video_composer.COL_EMPHASIS,
    ...) are read from the active brand, so a reader never sees a stale copy."""
    from src import art_direction as ad
    from src import (
        brand,
        cover_design,
        design_cards,
        end_card,
        graphics,
        multi_format,
        then_now,
    )
    from src import video_composer as vc
    assert hasattr(brand, "use"), "no brand configuration object"
    second = _second_brand()
    with brand.use(second):
        gold = second.colour("GOLD")
        assert graphics.GOLD == multi_format.GOLD == end_card.GOLD == then_now.GOLD == gold
        assert graphics.WHITE == second.colour("BONE") and graphics.SEA == second.colour("INK")
        assert vc.COL_EMPHASIS == brand.ass_colour(gold) and vc.COL_BACK == brand.ass_colour(second.colour("INK"), 0x20)
        assert cover_design.BRAND_TEXT == "SECOND TEST BRAND" and end_card.FOLLOW == "FOLLOW FOR THE NEXT ONE"
        assert design_cards.STRIKE_BACK != (177, 135, 40) and ad.DEFAULT_PALETTE["accent"] == second.colour("TEAL")
        assert brand.DISPLAY_FONT == "Montserrat"
    assert graphics.GOLD == (242, 182, 48) and vc.COL_EMPHASIS == "&H0030B6F2&" and end_card.HANDOFF == (
        "The next riddle is right here.")
    with pytest.raises(ValueError):
        brand.BrandConfig("incomplete", palette={"INK": (0, 0, 0)})


def _ground(tmp_path: Path) -> Path:
    import numpy as np
    rng = np.random.default_rng(7)
    rows = np.linspace(0, 1, 1920)[:, None, None]
    arr = np.array((96, 120, 150), float) * (1 - rows) + np.array((150, 120, 80), float) * rows
    arr = np.broadcast_to(arr, (1920, 1080, 3)).copy() + rng.normal(0, 14, (1920, 1080, 3))
    p = tmp_path / "ground.png"
    Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).save(p)
    return p


def test_a_second_brand_renders_through_the_frozen_designs(tmp_path):
    """The end card, the loop bridge's FOLLOW and the Reel cover's masthead drawn in the second brand: its gold
    appears, today's gold does not, and the masthead is its string in its face, with no code change."""
    import numpy as np

    from src import brand, end_card
    assert hasattr(brand, "use"), "no brand configuration object"
    second = _second_brand()
    ground = Image.open(_ground(tmp_path)).convert("RGB")

    def near(im, rgb, tol=6):
        a = np.asarray(im.convert("RGB"), dtype=np.int16)
        return int((np.abs(a - np.array(rgb)).max(axis=2) <= tol).sum())

    today = list(end_card.frames(ground, "Send this to someone who loves a mystery.", {}))[-1]
    with brand.use(second):
        lay = end_card.layout("Send this to someone who loves a mystery.")
        drawn = list(end_card.frames(ground, "Send this to someone who loves a mystery.", {}))[-1]
    assert lay["masthead"]["text"] == "SECOND TEST BRAND"
    assert near(today, (242, 182, 48)) > 500 and near(drawn, (242, 182, 48)) == 0
    assert near(drawn, second.colour("GOLD")) > 500
    assert brand.active() is brand.DEFAULT_BRAND


def test_measures_cached_in_a_brand_face_follow_the_brand():
    """A cache keyed without the family (the card's display cap height, the then-and-now year's cap) is dropped
    when the brand changes, so the second brand never measures with the first brand's face."""
    from src import brand, then_now
    from src import design_cards as dc
    assert hasattr(brand, "use"), "no brand configuration object"
    cap, year = dc._cap(120), then_now.year_cap("portrait")
    with brand.use(_second_brand()):
        assert dc._cap(120) != cap and then_now.year_cap("portrait") != year
    assert dc._cap(120) == cap and then_now.year_cap("portrait") == year


def test_a_brand_configured_after_import_reaches_the_caption_file(tmp_path):
    """Call time, not import time: the composer is imported (and has written a caption file in today's brand) before
    the brand is swapped with brand.configure; the next caption file carries the new brand's colours and none of
    today's, and the default returns with configure(None). The words are a real run's (the freeze panel's fixture)."""
    import json

    from src import brand
    from src import video_composer as vc
    assert hasattr(brand, "configure"), "no brand configuration object"
    words = json.loads((ROOT / "docs" / "reviews" / "freeze-panel-2026-11-04" / "fixtures" / "runs"
                        / "run_amber_room_heist_1792790550" / "words.json").read_text(encoding="utf-8"))[:60]
    emphasis = [w["word"].strip(".,;:!?").lower() for w in words[5:8]]
    today_colours = ("&H00E2EDF3&", "&H0030B6F2&", "&H20120E0B&")     # bone, gold, the INK plate
    second = _second_brand()
    theirs = (brand.ass_colour(second.colour("BONE")), brand.ass_colour(second.colour("GOLD")),
              brand.ass_colour(second.colour("INK"), 0x20))

    def written(name: str, **kw) -> str:
        return vc.VideoComposer().build_subtitles(words, tmp_path / name, emphasis=emphasis, **kw).read_text(
            encoding="utf-8")
    for kw in ({}, {"width": 1920, "height": 1080}):
        before = written("before.ass", **kw)
        previous = brand.configure(second)
        try:
            swapped = written("swapped.ass", **kw)
        finally:
            brand.configure(None)
        assert previous is brand.DEFAULT_BRAND and brand.active() is brand.DEFAULT_BRAND
        assert all(c in before for c in today_colours) and not any(c in before for c in theirs)
        assert all(c in swapped for c in theirs) and not any(c in swapped for c in today_colours)
        assert written("after.ass", **kw) == before


def test_a_design_drawn_on_another_thread_reads_the_swapped_brand():
    """The graphics draw frames on worker threads: the active brand is process-wide, so a thread started inside
    brand.use reads the swapped roles (a context variable would hand it the default)."""
    from concurrent.futures import ThreadPoolExecutor

    from src import brand, graphics
    assert hasattr(brand, "use"), "no brand configuration object"
    second = _second_brand()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with brand.use(second):
            seen = pool.submit(lambda: (graphics.GOLD, brand.MASTHEAD, brand.DISPLAY_FONT)).result()
        after = pool.submit(lambda: (graphics.GOLD, brand.MASTHEAD, brand.DISPLAY_FONT)).result()
    assert seen == (second.colour("GOLD"), "SECOND TEST BRAND", "Montserrat")
    assert after == ((242, 182, 48), "RELIC & RIDDLE", "Anton")


# ---------------------------------------------------------------- the port onto LAND-29 (round-1 review gaps)
def _third_brand():
    """A brand that moves every channel of every colour, greys too (the red/blue swap leaves a grey such as
    LEGIBLE_TEXT on itself), with Cinzel in the display, data (so the badge) and 16:9 caption roles."""
    from src import brand
    base = brand.DEFAULT_BRAND
    palette = {k: (v if isinstance(v, str) else (min(255, v[1] + 12), v[0], max(0, v[2] - 12)))
               for k, v in base.palette.items()}
    return base.derive("Third test brand", palette=palette,
                       fonts={"display": "Cinzel", "data": "Cinzel", "caption_wide": "Cinzel"},
                       text={"masthead": "THIRD TEST BRAND"})


WORDS = [{"word": w, "start": 0.4 * i, "end": 0.4 * i + 0.35}
         for i, w in enumerate(["The", "Richat", "Structure", "is", "situated", "near", "the", "town", "of",
                                "Ouadane", "in", "Mauritania."])]


def _style(text: str, name: str) -> list[str]:
    return next(line for line in text.splitlines() if line.startswith(f"Style: {name},")).split(",")


def test_the_16x9_caption_face_libass_draws_is_the_caption_wide_role(tmp_path):
    """The 16:9 captions draw in the face the plate is measured in, from the brand's caption_wide role: today's
    Montserrat as its derived static 700 instance (RR Sans, embedded), Cinzel as its own (RR Serif, embedded), each
    at the ASS size of the role's metrics; a face libass cannot draw at the measured weight is refused, never drawn
    in another face (the round-1 review: the role moved the measure while libass kept drawing RR Sans)."""
    from src import brand
    from src import video_composer as vc
    assert hasattr(brand, "use"), "no brand configuration object"

    def written(name: str) -> str:
        return vc.VideoComposer().build_subtitles(WORDS, tmp_path / name, width=1920, height=1080,
                                                  reconstruction_intervals=[(0.0, 2.0)]).read_text(encoding="utf-8")
    px = round(vc.CAPTION_WIDE_SIZE * 1080 / 1080)
    today = written("today.ass")
    assert _style(today, "CapText")[1:3] == ["RR Sans", str(vc.ass_font_size("Montserrat", px, 700))]
    assert "fontname: RRSans.ttf" in today
    with brand.use(_third_brand()):
        third = written("third.ass")
        with (brand.use(brand.active().derive("unbundled", fonts={"caption_wide": "No Such Face"})),
              pytest.raises(ValueError, match="no static instance")):
            written("refused.ass")
    assert _style(third, "CapText")[1:3] == ["RR Serif", str(vc.ass_font_size("Cinzel", px, 700))]
    assert "fontname: RRSerif.ttf" in third and "RRSans" not in third
    assert today.split("[Events]")[1] != third.split("[Events]")[1]     # the plates measured in Cinzel
    assert written("again.ass") == today
    from src.font_assets import WIDE_SERIF_FAMILY, derived_font
    assert vc.libass_font_problems(ROOT / "fonts", [(WIDE_SERIF_FAMILY, 700)], [derived_font(WIDE_SERIF_FAMILY)]) == []


def test_the_reconstruction_badge_reads_its_face_from_the_brand(tmp_path):
    """The AI-reconstruction badge's face is the brand's badge role (the data face by default), read when the caption
    file is written: a brand that changes the data face moves the badge's style and its measured plate."""
    from src import brand
    from src import video_composer as vc
    assert hasattr(brand, "use") and "badge" in brand.DEFAULT_BRAND.fonts, "the badge face is no brand role"

    def written(name: str, **kw) -> str:
        return vc.VideoComposer().build_subtitles(WORDS, tmp_path / name, reconstruction_intervals=[(0.0, 2.0)],
                                                  **kw).read_text(encoding="utf-8")
    for kw in ({}, {"width": 1920, "height": 1080}):
        today = written("today.ass", **kw)
        # QF-133: the data face's measured 600 file, by the name libass knows it by
        assert _style(today, "ReconBadge")[1] == _style(today, "ReconPlate")[1] == "IBM Plex Mono SmBld"
        assert brand.BADGE_FONT == brand.DATA_FONT == "IBM Plex Mono" and vc.RECON_FONT == "IBM Plex Mono"
        with brand.use(_third_brand()):
            third = written("third.ass", **kw)
            assert vc.RECON_FONT == brand.BADGE_FONT == "Cinzel"
        # round 2: the variable Cinzel draws as its static 600 instance, the weight the badge is measured at, embedded
        assert _style(third, "ReconBadge")[1] == _style(third, "ReconPlate")[1] == "RR Serif SemiBold"
        assert "fontname: RRSerifSemiBold.ttf" in third
        badge = [line for line in third.splitlines() if "AI ILLUSTRATION" in line or r"\p1" in line]
        assert badge and badge != [line for line in today.splitlines() if "AI ILLUSTRATION" in line or r"\p1" in line]
    with pytest.raises(ValueError):
        brand.BrandConfig("loop", fonts={**brand.DEFAULT_BRAND.fonts, "data": "badge"})


def test_the_dense_timeline_and_the_colour_vision_roles_follow_the_brand():
    """Stack modules the role tokens now reach: the five-event dense timeline plans in the brand's faces (its plan
    cache dropped when the brand changes) and the colour-vision gate checks the brand's own colours."""
    from src import brand, colour_vision, timeline_dense
    assert hasattr(brand, "use"), "no brand configuration object"
    events = [{"year": 1300, "label": "The Knights of St John reach Rhodes", "approximate": True},
              {"year": 1522, "label": "Suleiman the Magnificent takes the city"},
              {"year": 1523, "label": "The knights sail for Malta"},
              {"year": 1912, "label": "Italy takes the Dodecanese"},
              {"year": 1988, "label": "The Medieval City of Rhodes is listed"}]
    today = timeline_dense.plan(events, 1080, 1920)
    assert today is not None and today["year_font"].getname()[0] == "Anton"
    third = _third_brand()
    with brand.use(third):
        theirs = timeline_dense.plan(events, 1080, 1920)
        assert colour_vision.ROLES["gold"] == third.colour("GOLD") != (242, 182, 48)
        assert colour_vision.HOME_TINT == third.colour("HOME_TINT")
        assert colour_vision.LAND_TINT == third.colour("LAND_TINT")
        assert colour_vision.roles_for(None)["land"] == third.colour("LAND_TINT")
    assert theirs is None or theirs["year_font"].getname()[0] == "Cinzel"
    assert timeline_dense.plan(events, 1080, 1920)["year_font"].getname()[0] == "Anton"
    assert colour_vision.ROLES["gold"] == (242, 182, 48) and colour_vision.HOME_TINT == (168, 137, 81)



def test_a_timeline_span_too_thin_to_read_its_own_colour_is_measured_on_its_drawn_colour():
    """The third brand's 9:16 timeline set a span (its STONE, light enough to be read as light text, at track 2)
    whose glyphs leave typography.ring_contrast no eroded interior: plan_text raises TypeError there (the stack's
    signal for a thin face; today's STONE is dark text, measured on its fill, so the default never met it), which
    stopped the render. The spans now go through graphics._plan_thin, which measures such a face against its drawn
    colour, as the map's neighbour names already were; a face plan_text can read is planned exactly as before."""
    import numpy as np

    from src import brand, graphics
    from src.typography import get_font, plan_text
    ground = Image.fromarray(np.full((200, 600, 3), 10, np.uint8))
    with brand.use(_third_brand()):
        thin, read = get_font(brand.TEXT_FONT, 10, weight=300), get_font(brand.TEXT_FONT, 26, weight=700)
        with pytest.raises(TypeError):
            plan_text(ground, (20, 100), "6 YEARS LATER", thin, brand.STONE, "ls", track=2)
        plate = graphics._plan_thin(ground, (20, 100), "6 YEARS LATER", thin, brand.STONE, "ls", halo_px=None,
                                    track=2)
        assert plate.contrast is not None and plate.contrast >= brand.TEXT_CONTRAST and plate.track == 2
        same = graphics._plan_thin(ground, (20, 100), "6 YEARS LATER", read, brand.STONE, "ls", halo_px=None,
                                   track=2)
        want = plan_text(ground, (20, 100), "6 YEARS LATER", read, brand.STONE, "ls", track=2)
    assert (same.halo, same.contrast, same.box, type(same)) == (want.halo, want.contrast, want.box, type(want))


# ---------------------------------------------------------------- round 2: values derived from a role at import
_DUMP = r"""
import importlib, json, sys
sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[1] + "/tests")
from src import brand
import test_qf35a_role_tokens as t
mode = sys.argv[2]
foreign = t._third_brand().derive("every face moved", fonts={"text": "Cinzel", "masthead": "Montserrat"})
if mode in ("foreign", "restored"):
    brand.configure(foreign)
mods = [importlib.import_module("src." + m) for m in t.FROZEN + ("colour_vision",)]
if mode == "restored":
    brand.configure(None)
if mode == "after":
    brand.configure(foreign)

def simple(v, depth=0):
    if isinstance(v, (bool, int, float, str, type(None))):
        return True
    if depth < 3 and isinstance(v, (tuple, list, frozenset, set)):
        return all(simple(x, depth + 1) for x in v)
    if depth < 3 and isinstance(v, dict):
        return all(simple(k, depth + 1) and simple(x, depth + 1) for k, x in v.items())
    return False
out = {}
for mod in mods:
    names = [k for k, v in vars(mod).items() if not k.startswith("__") and simple(v)]
    names += [k for k in ("KICKER_PX", "BAND_FALL") if hasattr(mod, k) and k not in names]
    out[mod.__name__] = {k: repr(sorted(getattr(mod, k)) if isinstance(getattr(mod, k), (set, frozenset))
                                 else getattr(mod, k)) for k in names}
print(json.dumps(out))
"""


def _module_values(mode: str) -> dict:
    import json
    import os
    import subprocess
    import sys
    env = {**os.environ, "PYTHONHASHSEED": "0", "FONT_SIZE": "80"}
    got = subprocess.run([sys.executable, "-c", _DUMP, str(ROOT), mode], check=False, capture_output=True, text=True,
                         env=env, timeout=600, cwd=str(ROOT))
    assert got.returncode == 0, got.stderr[-2000:]
    return json.loads(got.stdout.strip().splitlines()[-1])


def test_no_module_value_is_measured_from_the_brand_at_import():
    """Round 2 (the port's adversary review): a value computed from a role when the module is imported (then_now's
    KICKER_PX and BAND_FALL, measured in the data and display faces) keeps the first brand's measure. Every frozen
    module is imported in a fresh process under the default brand, under a brand that moves every colour and face
    (configured before the import), and under that brand with the default configured again afterwards: every
    module-level value must be the same in the three, and a brand configured after the import must give what a fresh
    import under it gives (the old names read through each module's __getattr__)."""
    from src import brand
    assert hasattr(brand, "configure"), "no brand configuration object"
    parent, foreign, restored, after = (_module_values(m) for m in ("default", "foreign", "restored", "after"))
    moved = {f"{m}.{k}": (parent[m].get(k), foreign[m].get(k)) for m in parent
             for k in set(parent[m]) | set(foreign[m]) if parent[m].get(k) != foreign[m].get(k)
             and k not in ("KICKER_PX", "BAND_FALL")}
    assert moved == {}
    assert restored == parent
    # the brand-measured names follow the brand, configured before or after the import alike
    for k in ("KICKER_PX", "BAND_FALL"):
        assert after["src.then_now"][k] == foreign["src.then_now"][k] != parent["src.then_now"][k]


def test_the_badge_face_libass_draws_weighs_what_the_plate_is_measured_at():
    """Round 2: the badge style asks bold and its plate is measured at 600, so libass must find a face of that family
    within 150 of 700: a variable badge role draws its derived static 600 instance (no synthetic weight); a static
    face too light for it (Anton, 400) and a face with no static instance are refused, as compose_video refuses them."""
    from src import brand
    from src import video_composer as vc
    from src.font_assets import DERIVED_FAMILIES, derived_font
    assert hasattr(brand, "use"), "no brand configuration object"
    assert vc._badge_family() == "IBM Plex Mono SmBld"   # today's: static, its 600 file's libass name (QF-133)
    for face, derived in (("Cinzel", "RR Serif SemiBold"), ("Montserrat", "RR Sans SemiBold")):
        with brand.use(brand.DEFAULT_BRAND.derive("badge", fonts={"data": face})):
            assert vc._badge_family() == derived
        assert vc.libass_font_problems(ROOT / "fonts", [(derived, 700)], [derived_font(derived)]) == []
        assert DERIVED_FAMILIES[derived][1] == "wght600"
    with brand.use(brand.DEFAULT_BRAND.derive("badge", fonts={"data": "Anton"})):
        assert vc._badge_family() == "Anton"
        assert vc.libass_font_problems(ROOT / "fonts", [("Anton", 700)]) != []   # compose_video refuses it
    with brand.use(brand.DEFAULT_BRAND.derive("badge", fonts={"badge": "No Such Face"})),             pytest.raises(ValueError, match="no static instance"):
        vc._badge_family()


def test_the_badge_face_is_checked_only_when_a_badge_is_drawn(tmp_path, monkeypatch):
    """Round 3 (the port's second adversary review): compose_video checked the badge face even when no reconstruction
    was on screen, so a brand whose data face is too light for the badge (Anton, 400) could not compose a photo-only
    video that draws no badge. The check now runs where the badge is drawn: the photo-only composition passes the
    font checks, and the same brand with a reconstruction on screen is refused before rendering."""
    import wave

    from src import brand
    from src import video_composer as vc
    assert hasattr(brand, "use"), "no brand configuration object"
    photo = tmp_path / "photo.png"
    Image.new("RGB", (1080, 1920), (60, 70, 80)).save(photo)
    audio = tmp_path / "audio.wav"
    with wave.open(str(audio), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(bytes(48000 * 2 * 3))
    words = [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.35}
             for i, w in enumerate(["A", "tower", "stands", "beside", "the", "wall"])]

    class PastTheFontChecks(Exception):
        pass

    def stop(*_a, **_k):
        raise PastTheFontChecks

    composer = vc.VideoComposer()
    monkeypatch.setattr(composer, "export_subtitles_srt", stop)
    compose = getattr(vc.VideoComposer.compose_video, "__wrapped__", vc.VideoComposer.compose_video)
    with brand.use(brand.DEFAULT_BRAND.derive("light badge", fonts={"data": "Anton"})):
        assert vc.reconstruction_badge_intervals([photo], [3], 3) == []
        with pytest.raises(PastTheFontChecks):
            compose(composer, [photo], audio, audio, words, 3, tmp_path / "photo.mp4", durations=[3])
        monkeypatch.setattr(vc, "reconstruction_badge_intervals", lambda *_a, **_k: [(0.0, 3.0)])
        with pytest.raises(vc.RenderError, match="reconstruction badge"):
            compose(composer, [photo], audio, audio, words, 3, tmp_path / "badge.mp4", durations=[3])
