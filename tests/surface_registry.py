"""Every surface variant the code declares, enumerated FROM the code, and one synthetic render of each for the
type-size observer (design-freeze cycle 0, round 6, adversary r5-3: a hand list left out the monument cover, so a
137 px mutation there passed both inventories).

`registry()` reads the registries the renderers themselves dispatch on: every cover style
(visual_engine.RENDERABLE_STYLES), every carousel archetype (MultiFormatGenerator.CAROUSEL_ARCHETYPES) in both copies,
every card planner (design_cards.plan_*) and every graphic renderer (graphics.render_*) on each canvas it draws
(design_cards.PLAN_SIZES where not both), then-and-now and callouts on both canvases, the Story frame, the YouTube
thumbnail, the end card and the loop bridge, and the captions on both. `render_all` renders each key in it from INPUTS,
which is keyed by KIND (the key without its canvas): a kind the code gains without an input here fails
test_brand_system_r6.test_the_registry_has_an_input_for_every_kind_and_names_both_canvases, and a
surface left unrendered fails test_brand_system_r4.test_the_observer_renders_every_registered_surface.

Cycle 0 round 7 (sweep-paths #4): each kind also renders its data-driven BRANCHES (`branches()`, keys
"<kind>[<branch>]:<canvas>"): the event counts the timeline distinguishes, optional fields present (a credit, a
generated photo's disclosure, the reconstruction badge, range and prefixed figures, a hero figure, a reviewed focus, a
checked hedge), the loop bridge's variants, the maps with water and close neighbours and a home too thin for its name,
and the frames where a figure settles. test_brand_system_r4.test_every_declared_size_site_is_observed holds every
declared size site to being reached by one of them.
"""
from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
TALL, WIDE = (1080, 1920), (1920, 1080)
CANVAS = {"tall": TALL, "wide": WIDE}


def _entries(mod, prefix: str) -> list[str]:
    return sorted(n for n, f in inspect.getmembers(mod, inspect.isfunction)
                  if n.startswith(prefix) and f.__module__ == mod.__name__)


def _kinds_and_canvases() -> list[str]:
    """"<kind>:<canvas>" for every surface kind the code declares, on each canvas it draws."""
    from src import design_cards, graphics
    from src.multi_format import MultiFormatGenerator
    from src.visual_engine import RENDERABLE_STYLES
    out = [f"cover.{s}:tall" for s in RENDERABLE_STYLES]
    out += [f"carousel.{a}.{copy}:carousel" for a in MultiFormatGenerator.CAROUSEL_ARCHETYPES
            for copy in ("instagram", "facebook")]
    sizes = getattr(design_cards, "PLAN_SIZES", {})
    for name in _entries(design_cards, "plan_"):
        canv = sizes.get(name, (TALL, WIDE))
        out += [f"design_cards.{name}:{c}" for c, s in CANVAS.items() if s in canv]
    out += [f"graphics.{name}:{c}" for name in _entries(graphics, "render_") for c in CANVAS]
    out += [f"{m}.render:{c}" for m in ("then_now", "callouts") for c in CANVAS]
    out += ["story.frame:tall", "youtube_thumbnail.render:wide", "end_card.frames:tall", "end_card.frames:wide",
            "end_card.loop_bridge:tall", "captions.build_subtitles:tall", "captions.build_subtitles:wide"]
    return out


def registry() -> list[str]:
    """"<kind>:<canvas>" for every surface variant the code declares, and "<kind>[<branch>]:<canvas>" for each
    data-driven branch of it (cycle 0 round 7, sweep-paths #4: the kinds alone left out the 2-4-event timelines, the
    range cards, the Story hero and credit, the water and neighbour names, the reconstruction badge and the loop
    bridge's variants, whose sizes no declaration ever saw)."""
    out = []
    table = branches()
    for key in _kinds_and_canvases():
        kind, canvas = key.split(":")
        out.append(key)
        out += [f"{kind}[{b}]:{canvas}" for b, fn in table.get(kind, {}).items()
                if canvas in getattr(fn, "canvases", (canvas,))]
    return sorted(out)


def kinds(keys) -> set[str]:
    return {k.split(":")[0].split("[")[0] for k in keys}


def split_key(key: str) -> tuple[str, str | None, str]:
    """(kind, branch or None, canvas) of a registry key."""
    head, canvas = key.split(":")
    kind, _, rest = head.partition("[")
    return kind, (rest[:-1] if rest else None), canvas


# ------------------------------------------------------------------ inputs (synthetic; CI has no output/)

class _Ctx:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        rng = np.random.default_rng(2)
        self.flat = tmp / "p.jpg"
        Image.new("RGB", (1600, 2000), (150, 120, 90)).save(self.flat)
        self.busy = tmp / "busy.jpg"
        Image.fromarray(rng.integers(150, 240, (70, 40, 3), dtype=np.uint8)).resize((1200, 2100), Image.NEAREST).save(
            self.busy)
        self.busy2 = tmp / "busy2.jpg"
        Image.fromarray(np.random.default_rng(3).integers(150, 240, (70, 40, 3), dtype=np.uint8)).resize(
            (1200, 2100), Image.NEAREST).save(self.busy2)
        self.poster = tmp / "poster.jpg"
        im = Image.new("RGB", (1080, 1920), (11, 14, 18))
        im.paste((212, 175, 55), (300, 600, 780, 1300))
        im.save(self.poster)


def _cover(style):
    def go(ctx, size):
        from src.visual_engine import VisualEngine
        payload = {"thumbnail": {"eyebrow": "ANCIENT ENGINEERING", "line1": "THE 144-FOOT", "line2": "WHISPERING DOME",
                                 "tagline": "BUILT WITHOUT RINGS"}}
        VisualEngine().generate_thumbnails(ctx.busy, payload, ctx.tmp / f"cover_{style}", style=style, variants=False)
    return go


def _carousel(arche, copy):
    def go(ctx, size):
        from src.multi_format import MultiFormatGenerator
        card = {"heading": "IMPACT THEORY DISPROVED", "body": "The theory was disproved in 1969.",
                "stat": "40 KM across", "question": "Impact or erosion?"}
        photo = ctx.poster if arche == "diagram" else ctx.flat
        MultiFormatGenerator()._render_carousel_slide(
            arche, photo, ctx.flat, card, 0 if arche == "cover" else 1, 7,
            {"question": "Which?", "options": ["One", "Two"]}, "Send this to a friend.", copy == "facebook",
            subject="EYE OF THE SAHARA")
    return go


CARD_PLANS = ("plan_chapter_card", "plan_myth_fact", "plan_quote_card", "plan_hook_frame")


def _card_plan(name, settle=False, **kw):
    """The card's last frame; `settle`: also the frame halfway through each settling figure's scale (its items'
    `settle` windows, read from the plan), where frame_at draws the figure at an intermediate size. `kw`: the plan's
    own input in place of the base's (a myth, a chapter title)."""
    def go(ctx, size):
        import sys
        sys.path.insert(0, str(ROOT))
        import tests.test_design_cards_2026_11_03 as T
        import tests.test_freeze_cards_2026_11_04 as fc
        from src import config
        from src import design_cards as dc
        config.FACT_RECORD_KEY = config.FACT_RECORD_KEY or "test-only-fact-record-key-0123456789abcdef"
        saved = config.QUOTE_CARDS_ENABLED
        config.QUOTE_CARDS_ENABLED = True     # held by default (operator decision B); the design is still observed
        try:
            plan = {"plan_chapter_card": lambda: fc.chapter_plan(size, **kw),
                    "plan_myth_fact": lambda: fc.plan_myth(size=size, **kw),
                    "plan_quote_card": lambda: dc.plan_quote_card(T.quote(), T.SHEET, *size),
                    "plan_hook_frame": lambda: fc.hook_plan(ctx.busy)}[name]()
            dc.frame_at(plan, plan.frames - 1)
            if settle:
                for it in plan.meta["_r"].get("items", []):
                    if it.get("settle"):
                        s0, sd = it["settle"]
                        dc.frame_at(plan, min(plan.frames - 1, round((s0 + sd / 2) * dc.VIDEO_FPS)))
        finally:
            config.QUOTE_CARDS_ENABLED = saved
    return go


def _stop(city, label, lat, lon):
    """A route stop with its own coordinates (the observer never looks a place up on the network)."""
    return {"place": city, "country": "Turkey", "label": label, "latitude": lat, "longitude": lon}


GRAPHIC_INPUTS: dict[str, Callable] = {
    "render_map": lambda ctx, out, size: __import__("src.graphics", fromlist=["x"]).render_map(
        {"lat": 38.3735, "lon": 34.7347, "label": "Derinkuyu", "country": "Turkey", "approximate": False}, out,
        width=size[0], height=size[1]),
    "render_timeline": lambda ctx, out, size: __import__("src.graphics", fromlist=["x"]).render_timeline(
        [{"year": 1300, "label": "The Knights of St John reach Rhodes", "approximate": True},
         {"year": 1522, "label": "Suleiman the Magnificent takes the city"},
         {"year": 1523, "label": "The knights sail for Malta"},
         {"year": 1912, "label": "Italy takes the Dodecanese"},
         {"year": 1988, "label": "The Medieval City of Rhodes is listed"}], out, width=size[0], height=size[1],
        photo=ctx.flat),
    "render_scale": lambda ctx, out, size: __import__("src.graphics", fromlist=["x"]).render_scale(
        {"value": 280.0, "unit": "ft", "dimension": "depth", "subject": "Derinkuyu depth", "approximate": True}, out,
        width=size[0], height=size[1]),
    "render_stat_cards": lambda ctx, out, size: __import__("src.graphics", fromlist=["x"]).render_stat_cards(
        [{"value": "280 FT", "label": "Depth into volcanic rock"},
         {"value": "20,000", "label": "Estimated sheltered population"}], out, width=size[0], height=size[1]),
    "render_cross_section": lambda ctx, out, size: __import__("src.graphics", fromlist=["x"]).render_cross_section(
        {"subject": "Derinkuyu underground city", "total_depth_ft": 280.0, "approximate": True,
         "levels": [{"depth_ft": 15.0, "label": "Stables & wine presses"},
                    {"depth_ft": 35.0, "label": "Living quarters & halls"},
                    {"depth_ft": 140.0, "label": "Byzantine church"},
                    {"depth_ft": 280.0, "label": "Deepest wells"}]}, out, width=size[0], height=size[1]),
    "render_route": lambda ctx, out, size: __import__("src.graphics", fromlist=["x"]).render_route(
        [_stop("Istanbul", "Constantinople", 41.0082, 28.9784), _stop("Ankara", "Ancyra", 39.9334, 32.8597),
         _stop("Derinkuyu", "Derinkuyu underground city", 38.3735, 34.7347)], out, width=size[0], height=size[1]),
}


def _graphic(name):
    def go(ctx, size):
        GRAPHIC_INPUTS[name](ctx, ctx.tmp / f"{name}_{size[0]}.mp4", size)
    return go


def _then_now(ctx, size):
    from src import then_now
    then_now.render(ctx.busy, 1925, ctx.busy2, 2019, ctx.tmp / f"tn{size[0]}.mp4", width=size[0], height=size[1])


def _then_now_linework(ctx, size):
    """Then-and-now over a pale map-like ground crossed by dense dark linework, where the halo at 1.0 is short and
    the marker takes its glow (glow_fall, band_fall, year_cap)."""
    from PIL import ImageDraw

    from src import then_now
    paths = []
    for k in (0, 1):   # one drawing, the NOW photo its mirror
        im = Image.new("RGB", (1200, 2100), (236, 226, 200))
        d = ImageDraw.Draw(im)
        rng = np.random.default_rng(1)
        for _ in range(900):
            x, y = int(rng.integers(0, 1200)), int(rng.integers(0, 2100))
            d.line([(x, y), (x + int(rng.integers(-80, 80)), y + int(rng.integers(-80, 80)))], fill=(40, 30, 20),
                   width=2)
        paths.append(ctx.tmp / f"linework_{k}.png")
        (im if k == 0 else im.transpose(Image.FLIP_LEFT_RIGHT)).save(paths[-1])
    then_now.render(paths[0], 1925, paths[1], 2019, ctx.tmp / f"tnl{size[0]}.mp4", width=size[0], height=size[1])


def _callouts(ctx, size):
    import tests.test_callouts_exit_2026_11_03 as X  # the reference pair (door recess, carved tuff wall) in both cuts
    from src import callouts
    (w, h), photo_size, pts = X.PAIRS["916" if size == TALL else "169"]
    kept = callouts.declutter(callouts.clear_of_captions(pts, w, h), w, h)
    photo = X._photo(ctx.tmp / f"co{w}.jpg", photo_size)
    callouts.render(photo, kept, ctx.tmp / f"co{w}.mp4", width=w, height=h, reveal=[0.6, 2.2])


def _story(ctx, size):
    from src.multi_format import MultiFormatGenerator
    gen = MultiFormatGenerator()
    slides = [{"kicker": "DID YOU KNOW?", "line1": "ONE MAN", "line2": "KNOCKED", "body": "Short."},
              {"kicker": "WHAT HE FOUND", "line1": "A CITY", "line2": "UNDER THE FIELDS", "body": "A longer body."}]
    for i, s in enumerate(slides):
        gen._render_story_frame(ctx.flat, s, i, 3, {})


def _youtube(ctx, size):
    from src import youtube_thumbnail
    youtube_thumbnail.render(Image.open(ctx.flat), {"title": "THE EYE OF THE SAHARA"}, meta={})
    youtube_thumbnail.render(Image.open(ctx.flat), {"line1": "THE 25-MILE", "line2": "SAHARA EYE"}, meta={})


def _end_card(ctx, size):
    from src import end_card
    old = (end_card.SECONDS, end_card.END_SCREEN_SECONDS)
    end_card.SECONDS = end_card.END_SCREEN_SECONDS = 0.2
    try:
        list(end_card.frames(Image.new("RGB", size, (60, 60, 60)), "Share this with a friend.", {}))
    finally:
        end_card.SECONDS, end_card.END_SCREEN_SECONDS = old


def _captions(ctx, size):
    from src import video_composer as vc
    words = json.loads((ROOT / "docs/reviews/freeze-panel-2026-11-04/fixtures/runs/run_amber_room_heist_1792790550/"
                        "words.json").read_text(encoding="utf-8"))
    vc.VideoComposer().build_subtitles(words, ctx.tmp / f"c{size[0]}.ass", width=size[0], height=size[1])


def _loop_bridge(line=True, follow=False):
    def go(ctx, size):
        from src import end_card
        # LOOP_SECONDS (1 s) at its own FPS: the seam curve needs its frames
        list(end_card.loop_bridge(Image.new("RGB", size, (50, 60, 70)), Image.new("RGB", size, (70, 60, 50)),
                                  "Share this with a friend.", {}, line=line, follow=follow))
    return go


# ------------------------------------------------------------------ data-driven branches of a kind (round 7)

TIMELINE_EVENTS = [{"year": 1300, "label": "The Knights of St John reach Rhodes", "approximate": True},
                   {"year": 1522, "label": "Suleiman the Magnificent takes the city"},
                   {"year": 1523, "label": "The knights sail for Malta"},
                   {"year": 1912, "label": "Italy takes the Dodecanese"},
                   {"year": 1948, "label": "The islands join Greece"},
                   {"year": 1988, "label": "The Medieval City of Rhodes is listed"}]


def _timeline_of(n: int) -> list[dict]:
    """n events, the first and the last always kept and the middle evenly spaced (n up to TIMELINE_EVENTS)."""
    k = len(TIMELINE_EVENTS) - 1
    return [TIMELINE_EVENTS[round(j * k / (n - 1))] for j in range(n)] if n > 1 else TIMELINE_EVENTS[:1]


def _timeline(n, photo=True):
    def go(ctx, size):
        from src import graphics
        graphics.render_timeline(_timeline_of(n), ctx.tmp / f"tl{n}_{size[0]}.mp4", width=size[0], height=size[1],
                                 photo=ctx.flat if photo else None)
    return go


def _stats(cards, photo=False, fps=None):
    """The number cards; `fps`: sampled that often (the observation's 1 fps lands past the figure's settle, where
    _stat_clip draws it at an intermediate size)."""
    def go(ctx, size):
        from src import graphics
        old = graphics.VIDEO_FPS
        graphics.VIDEO_FPS = fps or old
        try:
            graphics.render_stat_cards(cards, ctx.tmp / f"st_{size[0]}.mp4", photo=ctx.flat if photo else None,
                                       width=size[0], height=size[1])
        finally:
            graphics.VIDEO_FPS = old
    return go


def _scale(fact):
    def go(ctx, size):
        from src import graphics
        graphics.render_scale(fact, ctx.tmp / f"sc_{size[0]}.mp4", width=size[0], height=size[1])
    return go


def _story_slide(slide, i, count, poll=None, credited=False):
    """One Story frame; `credited`: its photo carries a CC BY-SA credits.json entry (the credit at its foot)."""
    def go(ctx, size):
        from src.multi_format import MultiFormatGenerator
        photo = ctx.flat
        if credited:
            folder = ctx.tmp / "credited"
            folder.mkdir(exist_ok=True)
            photo = folder / "vault.jpg"
            Image.open(ctx.flat).save(photo)
            (folder / "credits.json").write_text(json.dumps({"images": [{
                "file": photo.name, "artist": "Ann Lee", "title": "File:Vault beneath the old city.jpg",
                "license": "CC BY-SA 4.0", "attribution_required": True,
                "source_url": "https://commons.wikimedia.org/wiki/File:Vault_beneath_the_old_city.jpg",
                "license_url": "https://creativecommons.org/licenses/by-sa/4.0/"}]}), encoding="utf-8")
        MultiFormatGenerator()._render_story_frame(photo, slide, i, count, poll or {})
    return go


def _story_copy_check(ctx, size):
    """The Story's copy check: script_engine.validate_structure measures each Story body as the frame sets it
    (multi_format.story_body_lines), here on the committed Derinkuyu script."""
    from src.models import ScriptPackage
    from src.script_engine import validate_structure
    data = json.loads((ROOT / "data" / "scripts" / "derinkuyu_underground_city.json").read_text(encoding="utf-8"))
    validate_structure(ScriptPackage.model_validate({k: v for k, v in data.items()
                                                     if k in ScriptPackage.model_fields and not k.startswith("_")}))


def _illustrated_cover(style):
    """The cover over a generated illustration (scene_planner.is_illustration: a gap_gen_ hero): the disclosure."""
    def go(ctx, size):
        from src.visual_engine import VisualEngine
        hero = ctx.tmp / "gap_gen_hero.jpg"
        if not hero.exists():
            Image.open(ctx.busy).save(hero)
        payload = {"thumbnail": {"eyebrow": "ANCIENT ENGINEERING", "line1": "THE 144-FOOT", "line2": "WHISPERING DOME",
                                 "tagline": "BUILT WITHOUT RINGS"}}
        VisualEngine().generate_thumbnails(hero, payload, ctx.tmp / f"cover_ill_{style}", style=style, variants=False)
    return go


GUMBAZ_COPY = {"eyebrow": "ANCIENT ACOUSTICS", "line1": "THE 144-FOOT", "line2": "WHISPERING DOME",
               "tagline": "BUILT WITHOUT RINGS", "badge": "EST. 144 FT (44 M)"}
GUMBAZ_SHEET = ROOT / "data" / "fact_sheets" / "gol_gumbaz_whispering_gallery.json"   # tracked: CI has it


def _checked_payload(copy):
    """A cover payload checked against a committed fact sheet (its figure hedged: ESTIMATED)."""
    return {"thumbnail": dict(copy), "fact_review": {"sheet_path": str(GUMBAZ_SHEET)}}


def _focused_cover(style):
    """The cover with a reviewed focus on its hero (cover_design.reviewed_focus's record, here for the synthetic
    hero's own bytes): the immersive and inset-window compositions."""
    def go(ctx, size):
        import hashlib

        from src import cover_design
        from src.visual_engine import VisualEngine
        hero = ctx.tmp / "focus_hero.jpg"
        if not hero.exists():
            Image.open(ctx.busy).save(hero)
        record = {"sha256": hashlib.sha256(hero.read_bytes()).hexdigest(), "focus_box": [0.3, 0.12, 0.7, 0.9],
                  "reviewed": "synthetic"}
        real = cover_design.reviewed_focus
        cover_design.reviewed_focus = lambda path, kept=None: real(path, kept=record)
        try:
            VisualEngine().generate_thumbnails(hero, {"thumbnail": {k: v for k, v in GUMBAZ_COPY.items() if k != "badge"}},
                                               ctx.tmp / f"cover_focus_{style}", style=style, variants=False)
        finally:
            cover_design.reviewed_focus = real
    return go


def _checked_cover(style):
    def go(ctx, size):
        from src.visual_engine import VisualEngine
        VisualEngine().generate_thumbnails(ctx.busy, _checked_payload(GUMBAZ_COPY), ctx.tmp / f"cover_chk_{style}",
                                           style=style, variants=False)
    return go


def _youtube_with(copy, checked=False, **kw):
    def go(ctx, size):
        from src import youtube_thumbnail
        from src.visual_engine import cover_figure
        figure = cover_figure(copy, _checked_payload(copy)) if checked else None
        youtube_thumbnail.render(Image.open(ctx.flat), dict(copy), meta={}, figure=figure, **kw)
    return go


def _only(canvas, fn):
    """A branch the code has on one canvas only (the 16:9 end screen's credits line, slots and hand-off)."""
    fn.canvases = (canvas,)
    return fn


def _end_screen(**kw):
    def go(ctx, size):
        from src import end_card
        old = (end_card.SECONDS, end_card.END_SCREEN_SECONDS)
        end_card.SECONDS = end_card.END_SCREEN_SECONDS = 0.2
        try:
            list(end_card.frames(Image.new("RGB", size, (60, 60, 60)), "The next riddle is right here.", {}, **kw))
        finally:
            end_card.SECONDS, end_card.END_SCREEN_SECONDS = old
    return go


def _captions_badge(ctx, size):
    """The captions with the AI-reconstruction badge on screen (reconstruction_intervals)."""
    from src import video_composer as vc
    words = json.loads((ROOT / "docs/reviews/freeze-panel-2026-11-04/fixtures/runs/run_amber_room_heist_1792790550/"
                        "words.json").read_text(encoding="utf-8"))
    vc.VideoComposer().build_subtitles(words, ctx.tmp / f"cb{size[0]}.ass", width=size[0], height=size[1],
                                       reconstruction_intervals=[(0.0, 4.0)])


def _on_world(job, world, place=None):
    """The map or the route on another synthetic world (`world(tmp)` -> patches over _synthetic_geo's): `place` the
    map's site in place of the base input's."""
    def go(ctx, size):
        from src import geo, graphics
        patches = world(ctx.tmp)
        saved = [(m, n, getattr(m, n)) for m, n, _ in patches]
        for m, n, v in patches:
            setattr(m, n, v)
        geo.marine_polygons.cache_clear()
        out = ctx.tmp / f"{world.__name__}_{job}_{size[0]}.mp4"
        try:
            if place is not None:
                graphics.render_map(place, out, width=size[0], height=size[1])
            else:
                GRAPHIC_INPUTS[f"render_{job}"](ctx, out, size)
        finally:
            for m, n, v in saved:
                setattr(m, n, v)
            geo.marine_polygons.cache_clear()
    return go


def _thin_geo(tmp: Path):
    """A home country far longer than it is wide, so its name fits in its fill at no size (set outside it)."""
    from src import geo
    world = [_country("Gambia", -15.3, 13.45, 2.2, 0.12), _country("Senegal", -14.5, 14.5, 3.5, 2.5),
             _country("Mali", -4.0, 17.0, 6.0, 5.0)]
    return [(geo, "countries", lambda: world),
            (geo, "find_country", lambda name, **k: next((c for c in world if name.lower() in c["names"]), None))]


def branches() -> dict[str, dict[str, Callable]]:
    """kind -> {branch: render(ctx, size)}: the data-driven variants each kind's code distinguishes, beyond its base
    input. Event counts from graphics.TIMELINE_MAX_EVENTS (2 to one over the cap, the base drawing 5); optional fields
    present and absent (a credit, a generated photo's disclosure, the reconstruction badge, range and prefixed
    figures, a hero figure, a poll); the loop bridge's line and follow; the maps with water and close neighbours."""
    from src import graphics
    from src.visual_engine import RENDERABLE_STYLES
    counts = [n for n in range(2, graphics.TIMELINE_MAX_EVENTS + 2) if n != 5]
    out: dict[str, dict[str, Callable]] = {
        "graphics.render_timeline": {**{f"events={n}": _timeline(n) for n in counts},
                                     "events=2.no_photo": _timeline(2, photo=False)},
        "graphics.render_stat_cards": {
            "range": _stats([{"value": "10-20", "label": "Estimated hidden rooms"}]),
            "range_unit": _stats([{"value": "94-104 million years", "label": "Age of the rings"}], photo=True),
            "prefix_conversion": _stats([{"value": "about 280 ft (85 m)", "label": "Depth into volcanic rock"}]),
            "one_heading": _stats([{"value": "Unknown", "label": "Who carved the first rooms"}]),
            "settle": _stats([{"value": "10-20", "label": "Estimated hidden rooms"}], fps=5),
        },
        "graphics.render_scale": {"height": _scale({"value": 144.0, "unit": "ft", "dimension": "height",
                                                    "subject": "A stone tower", "approximate": True})},
        "graphics.render_map": {"coast": _on_world("map", _coast_geo),
                                "thin_home": _on_world("map", _thin_geo, {"lat": 13.45, "lon": -15.3, "label": "Janjanbureh",
                                                                          "country": "Gambia", "approximate": False})},
        "graphics.render_route": {"coast": _on_world("route", _coast_geo)},
        "story.frame": {
            "hero": _story_slide({"kicker": "UNDERGROUND", "line1": "280 FEET", "line2": "BELOW THE HILL",
                                  "body": "An old room lies underground."}, 1, 3),
            "hero_qualifier": _story_slide({"kicker": "UNDERGROUND", "line1": "ABOUT 280 FEET",
                                            "line2": "BELOW THE HILL", "body": "An old room lies underground."}, 1, 3),
            "hero_range": _story_slide({"kicker": "HOW OLD", "line1": "94–104 MILLION YEARS OLD",
                                        "line2": "THE RINGS", "body": "The rock is older than the desert."}, 1, 3),
            "credit": _story_slide({"kicker": "WHAT LIES BELOW", "line1": "OLD ROOMS", "line2": "UNDER THE HILL",
                                    "body": "Stone rooms lie beneath the fields."}, 1, 3, credited=True),
            "poll": _story_slide({"kicker": "YOUR VERDICT", "line1": "AN OLD CITY", "line2": "UNDER THE HILL",
                                  "body": "Which room came first?"}, 2, 3,
                                 {"question": "Which room came first?", "options": ["The old room", "The new room"]}),
            "copy_check": _story_copy_check,
        },
        "end_card.frames": {"credits_slots": _only("wide", _end_screen(credits=True, slot_frames=True)),
                            "no_handoff": _only("wide", _end_screen(handoff=False))},
        "end_card.loop_bridge": {"plain": _loop_bridge(line=False), "follow": _loop_bridge(follow=True)},
        "captions.build_subtitles": {"reconstruction": _captions_badge},
        "then_now.render": {"linework": _only("tall", _then_now_linework)},
        "youtube_thumbnail.render": {
            "illustration": _youtube_with({"title": "THE EYE OF THE SAHARA"}, illustration=True),
            # rules2 R2: A CITY / UNDER / THE FIELDS (a one-word middle row) is refused at the feed floor now
            "lockup": _youtube_with({"line1": "A CITY", "line2": "UNDER A FIELD"}),
            "hedged": _youtube_with(GUMBAZ_COPY, checked=True),
            "one_line_kicker": _youtube_with({"eyebrow": "DESERT ANOMALY", "line1": "THE RICHAT STRUCTURE OF MAURITANIA",
                                              "line2": "SAHARA EYE"}),
        },
    }
    for s in RENDERABLE_STYLES:
        out[f"cover.{s}"] = {"illustration": _illustrated_cover(s), "focus": _focused_cover(s),
                             "fact_checked": _checked_cover(s)}
    for name in CARD_PLANS:
        out[f"design_cards.{name}"] = {"settle": _card_plan(name, settle=True)}
    long_et = _card_plan("plan_myth_fact", m=(
        "Extraterrestrials constructed the circular rings",
        "Wind and water slowly eroded the dome over millions of years into concentric rings", "f_et"))

    long_wide = _card_plan("plan_myth_fact", m=(
        "Extraterrestrials made the circular rings",
        "Wind and water slowly eroded the dome over millions of years into rings", "f_et"))

    def long_myth(ctx, size):
        """The long myth: LONG_ET on 9:16. On 16:9 LONG_ET is a declared refusal (cycle 0 round 8, ruling 4 on every
        row: its only wide forms leave a word alone in a middle row; tests/test_freeze_cards_2026_11_04.refused_wide),
        and its refusal comes before the fit weighs a long word alone, so the wide long myth is one that draws with
        its long first word alone on a row (the fit's lone-word rule and its last-row rule, M2, reached and drawn)."""
        return (long_wide if size[0] > size[1] else long_et)(ctx, size)
    out["design_cards.plan_myth_fact"]["long"] = long_myth
    out["design_cards.plan_chapter_card"]["accented"] = _card_plan("plan_chapter_card",
                                                                   title="The Rings Around the Ténéré Desert")
    return out


def inputs() -> dict[str, Callable]:
    """kind -> render(ctx, size): the synthetic input for every kind the registry can name."""
    from src import design_cards, graphics
    from src.multi_format import MultiFormatGenerator
    from src.visual_engine import RENDERABLE_STYLES
    out: dict[str, Callable] = {f"cover.{s}": _cover(s) for s in RENDERABLE_STYLES}
    out.update({f"carousel.{a}.{c}": _carousel(a, c) for a in MultiFormatGenerator.CAROUSEL_ARCHETYPES
                for c in ("instagram", "facebook")})
    out.update({f"design_cards.{n}": _card_plan(n) for n in _entries(design_cards, "plan_") if n in CARD_PLANS})
    out.update({f"graphics.{n}": _graphic(n) for n in _entries(graphics, "render_") if n in GRAPHIC_INPUTS})
    out.update({"then_now.render": _then_now, "callouts.render": _callouts, "story.frame": _story,
                "youtube_thumbnail.render": _youtube, "end_card.frames": _end_card,
                "end_card.loop_bridge": _loop_bridge(), "captions.build_subtitles": _captions})
    return out


ROUTE_POINTS = {"istanbul": (41.0082, 28.9784), "ankara": (39.9334, 32.8597), "derinkuyu": (38.3735, 34.7347)}


def _country(name, cx, cy, rx, ry):
    import math
    ring = [(cx + rx * math.cos(2 * math.pi * i / 48), cy + ry * math.sin(2 * math.pi * i / 48)) for i in range(48)]
    return {"names": {name.lower()}, "sovereign": name.lower(), "polygons": [[ring]], "size": len(ring),
            "display": name, "label_at": (cx, cy), "label_rank": 2, "type": "Sovereign country",
            "extent": (cx - rx, cy - ry, cx + rx, cy + ry)}


def _coast_geo(tmp: Path):
    """(patches) over _synthetic_geo's: the same home with neighbours touching it (so the close-up names them) and two
    seas beside it in a synthetic Natural Earth marine file (geo.MARINE_FILE, read by geo's own loader), so the water
    names are placed and checked as on a real coast."""
    from src import geo
    world = [_country("Turkey", 35.0, 39.0, 9.0, 3.2), _country("Ukraine", 31.5, 49.0, 7.0, 2.8),
             _country("Egypt", 30.0, 26.5, 5.0, 4.5), _country("Iran", 54.0, 32.5, 7.5, 5.0),
             _country("Syria", 38.5, 34.4, 3.0, 1.9), _country("Georgia", 43.6, 42.0, 2.2, 1.0),
             _country("Greece", 22.2, 39.4, 3.2, 2.6), _country("Iraq", 44.0, 33.0, 4.0, 3.0)]

    def sea(name, cla, rank, x0, y0, x1, y1):
        ring = [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]
        return {"type": "Feature", "properties": {"name": name, "name_en": name, "featurecla": cla, "scalerank": rank},
                "geometry": {"type": "Polygon", "coordinates": [ring]}}
    marine = tmp / "synthetic_marine.geojson"
    marine.write_text(json.dumps({"type": "FeatureCollection", "features": [
        sea("Black Sea", "sea", 2, 27.5, 42.6, 41.5, 46.0),
        sea("Mediterranean Sea", "sea", 1, 23.0, 31.6, 35.5, 35.4)]}), encoding="utf-8")
    return [(geo, "MARINE_FILE", marine), (geo, "countries", lambda: world),
            (geo, "find_country", lambda name, **k: next((c for c in world if name.lower() in c["names"]), None))]


def _synthetic_geo():
    """(patches): the maps drawn on a synthetic Natural Earth stand-in (tests/test_r4_graphics_2026_11_02.route_geo's)
    with every geo data file pointed at a folder that does not exist, so the observation is the same with or without
    tools/geo on disk (CI has none) and nothing is looked up on the network."""
    from src import geo, graphics
    world = [_country("Turkey", 35.0, 39.0, 9.0, 3.2), _country("Ukraine", 31.5, 49.0, 7.0, 2.8),
             _country("Egypt", 30.0, 26.5, 5.0, 4.5), _country("Iran", 54.0, 32.5, 7.5, 5.0)]
    nowhere = ROOT / "tests" / "_no_geo_data_"
    patches = [(geo, n, nowhere / Path(str(getattr(geo, n))).name) for n in dir(geo)
               if n.endswith("_FILE") and n != "CACHE_FILE"]
    patches += [(geo, "available", lambda: True), (geo, "countries", lambda: world),
                (geo, "find_country", lambda name, **k: next((c for c in world if name.lower() in c["names"]), None)),
                (geo, "locate", lambda topic: dict(
                    zip(("lat", "lon"), ROUTE_POINTS[topic["location"]["name"].lower()]),
                    label=topic["location"]["name"], approximate=False, matched="name",
                    country=topic["location"]["country"])),
                (graphics, "_relief", lambda: None)]
    return patches


def render_all(tmp: Path) -> list[str]:
    """Every registered surface and branch rendered once (frames iterated, nothing encoded, 1 fps, a card's last frame);
    returns the keys rendered."""
    from src import callouts, graphics, then_now
    tmp.mkdir(parents=True, exist_ok=True)
    ctx = _Ctx(tmp)
    table = inputs()
    forks = branches()
    geo_saved = [(mod, name, getattr(mod, name)) for mod, name, _ in _synthetic_geo()]
    for mod, name, value in _synthetic_geo():
        setattr(mod, name, value)

    def encode(frames, out, *a, **k):
        for _ in frames:
            pass
        return Path(out)
    saved = (graphics._encode, then_now._encode, callouts._encode, graphics.VIDEO_FPS, graphics.graded_frame)
    graphics._encode = then_now._encode = callouts._encode = encode
    graphics.VIDEO_FPS = 1
    graphics.graded_frame = lambda frame, work, vignette=False: None
    done = []
    try:
        for key in registry():
            kind, branch, canvas = split_key(key)
            (forks[kind][branch] if branch else table[kind])(ctx, CANVAS.get(canvas))
            done.append(key)
    finally:
        (graphics._encode, then_now._encode, callouts._encode, graphics.VIDEO_FPS, graphics.graded_frame) = saved
        for mod, name, value in geo_saved:
            setattr(mod, name, value)
    return done
