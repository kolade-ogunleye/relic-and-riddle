"""Every surface variant the code declares, enumerated FROM the code, and one synthetic render of each for the
type-size observer (design-freeze cycle 0, round 6, adversary r5-3: a hand list left out the monument cover, so a
137 px mutation there passed both inventories).

`registry()` reads the registries the renderers themselves dispatch on: every cover style
(visual_engine.RENDERABLE_STYLES), every carousel archetype (MultiFormatGenerator.CAROUSEL_ARCHETYPES) in both copies,
every card planner (design_cards.plan_*) and every graphic renderer (graphics.render_*) on each canvas it draws
(design_cards.PLAN_SIZES where not both), then-and-now and callouts on both canvases, the Story frame, the YouTube
thumbnail, the end card on both canvases and the captions on both. `render_all` renders each key in it from INPUTS,
which is keyed by KIND (the key without its canvas): a kind the code gains without an input here fails
test_brand_system_r6.test_the_registry_has_an_input_for_every_kind_and_names_both_canvases, and a
surface left unrendered fails test_brand_system_r4.test_the_observer_renders_every_registered_surface.
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


def registry() -> list[str]:
    """"<kind>:<canvas>" for every surface variant the code declares."""
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
            "captions.build_subtitles:tall", "captions.build_subtitles:wide"]
    return sorted(out)


def kinds(keys) -> set[str]:
    return {k.split(":")[0] for k in keys}


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


def _card_plan(name):
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
            plan = {"plan_chapter_card": lambda: fc.chapter_plan(size),
                    "plan_myth_fact": lambda: fc.plan_myth(size=size),
                    "plan_quote_card": lambda: dc.plan_quote_card(T.quote(), T.SHEET, *size),
                    "plan_hook_frame": lambda: fc.hook_plan(ctx.busy)}[name]()
            dc.frame_at(plan, plan.frames - 1)
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


def inputs() -> dict[str, Callable]:
    """kind -> render(ctx, size): the synthetic input for every kind the registry can name."""
    from src import design_cards, graphics
    from src.multi_format import MultiFormatGenerator
    from src.visual_engine import RENDERABLE_STYLES
    out: dict[str, Callable] = {f"cover.{s}": _cover(s) for s in RENDERABLE_STYLES}
    out.update({f"carousel.{a}.{c}": _carousel(a, c) for a in MultiFormatGenerator.CAROUSEL_ARCHETYPES
                for c in ("instagram", "facebook")})
    out.update({f"design_cards.{n}": _card_plan(n) for n in _entries(design_cards, "plan_")
                if n in ("plan_chapter_card", "plan_myth_fact", "plan_quote_card", "plan_hook_frame")})
    out.update({f"graphics.{n}": _graphic(n) for n in _entries(graphics, "render_") if n in GRAPHIC_INPUTS})
    out.update({"then_now.render": _then_now, "callouts.render": _callouts, "story.frame": _story,
                "youtube_thumbnail.render": _youtube, "end_card.frames": _end_card,
                "captions.build_subtitles": _captions})
    return out


ROUTE_POINTS = {"istanbul": (41.0082, 28.9784), "ankara": (39.9334, 32.8597), "derinkuyu": (38.3735, 34.7347)}


def _synthetic_geo():
    """(patches): the maps drawn on a synthetic Natural Earth stand-in (tests/test_r4_graphics_2026_11_02.route_geo's)
    with every geo data file pointed at a folder that does not exist, so the observation is the same with or without
    tools/geo on disk (CI has none) and nothing is looked up on the network."""
    import math

    from src import geo, graphics

    def country(name, cx, cy, rx, ry):
        ring = [(cx + rx * math.cos(2 * math.pi * i / 48), cy + ry * math.sin(2 * math.pi * i / 48)) for i in range(48)]
        return {"names": {name.lower()}, "sovereign": name.lower(), "polygons": [[ring]], "size": len(ring),
                "display": name, "label_at": (cx, cy), "label_rank": 2, "type": "Sovereign country",
                "extent": (cx - rx, cy - ry, cx + rx, cy + ry)}
    world = [country("Turkey", 35.0, 39.0, 9.0, 3.2), country("Ukraine", 31.5, 49.0, 7.0, 2.8),
             country("Egypt", 30.0, 26.5, 5.0, 4.5), country("Iran", 54.0, 32.5, 7.5, 5.0)]
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
    """Every registered surface rendered once (frames iterated, nothing encoded, 1 fps); returns the keys rendered."""
    from src import callouts, graphics, then_now
    tmp.mkdir(parents=True, exist_ok=True)
    ctx = _Ctx(tmp)
    table = inputs()
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
            kind, canvas = key.split(":")
            table[kind](ctx, CANVAS.get(canvas))
            done.append(key)
    finally:
        (graphics._encode, then_now._encode, callouts._encode, graphics.VIDEO_FPS, graphics.graded_frame) = saved
        for mod, name, value in geo_saved:
            setattr(mod, name, value)
    return done
