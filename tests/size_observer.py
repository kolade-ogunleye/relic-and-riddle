"""Runtime type-size observation (design-freeze cycle 0, round 4: the static scan missed aliases, font_variant and a
wrapper). Inside `observe()`, EVERY font a surface draws with is recorded with its size and its site:

- every FreeType font object constructed (`PIL.ImageFont.FreeTypeFont.__init__`: truetype, font_variant, the repo's
  FeaturedFont and fallback instances all construct one), and
- every call to the repo's cached loader `typography.get_font`, wherever it is bound (each module attribute holding
  it, so an alias is caught too, and a cache hit that constructs nothing is still seen).

The site is the innermost frame in a surface module (the typography / font_engine / font_assets internals pass
through to their caller), as "module:qualname" with "<locals>" removed, matching tests/type_sites.py. A font made in
a loader a surface module defines itself (type_sites.wrappers(): `masthead:font_for`, `graphics:figure_font`,
`design_cards:_design_wrap`, ...) is recorded at that loader AND at its caller, as the static scan counts both (cycle
0 round 7, sweep-paths #4: the callers had been dropped, so a route's place names were seen only as
graphics:place_font and a myth card's wraps only as _design_wrap; and the masthead's wrapper was skipped, so
masthead:font_for was declared and never seen). Sizes are rounded to 0.001 px.
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

from type_sites import SURFACE_MODULES, wrappers

WRAPPERS = wrappers()
ROOT = Path(__file__).resolve().parent.parent
INTERNAL = {"typography", "font_engine", "font_assets", "brand", "brand_system"}
OUTSIDE = "<outside>"


def normalise(qualname: str) -> str:
    """A frame's qualname as tests/type_sites.py names its site: "<locals>" dropped, and a lambda or comprehension
    folded into the function it is written in."""
    parts = [p for p in qualname.split(".") if p != "<locals>"]
    while parts and parts[-1].startswith("<") and parts[-1].endswith(">"):
        parts.pop()
    return ".".join(parts) or "<module>"


def _sites() -> list[str]:
    """The surface site making the font (and, inside a surface loader, its caller's too), or ["<outside>"] (a test's own
    call, or an internal one)."""
    out: list[str] = []
    f = sys._getframe(2)
    while f is not None:
        path = Path(f.f_code.co_filename)
        if path.parent.name == "src" and path.parent.parent == ROOT:
            mod = path.stem
            qual = normalise(f.f_code.co_qualname)
            if mod in SURFACE_MODULES and mod not in INTERNAL:
                out.append(f"{mod}:{qual}")
                if qual.rsplit(".", 1)[-1] not in WRAPPERS:
                    return out
        f = f.f_back
    return out or [OUTSIDE]


@contextmanager
def observe():
    """Yields a set that fills with (site, size) for every font drawn with inside the block."""
    from PIL import ImageFont

    from src import typography
    seen: set[tuple[str, float]] = set()
    orig_init = ImageFont.FreeTypeFont.__init__
    orig_get = typography.get_font

    def init(self, font=None, size=10, *a, **k):
        seen.update((site, round(float(size), 3)) for site in _sites())
        return orig_init(self, font, size, *a, **k)

    def get_font(name, size, *a, **k):
        seen.update((site, round(float(size), 3)) for site in _sites())
        return orig_get(name, size, *a, **k)
    get_font.cache_clear = orig_get.cache_clear

    # every lru cache of the repo is cleared first, so a font a cached measure made earlier is made (and seen) again:
    # the observation does not depend on what ran before it
    for name, mod in list(sys.modules.items()):
        if name == "src" or name.startswith("src."):
            for value in list(vars(mod).values()):
                clear = getattr(value, "cache_clear", None)
                if callable(clear) and hasattr(value, "cache_info"):
                    clear()
    patched = []
    for name, mod in list(sys.modules.items()):
        if not (name == "src" or name.startswith("src.")):
            continue
        for attr, value in list(vars(mod).items()):
            if value is orig_get:
                patched.append((mod, attr))
                setattr(mod, attr, get_font)
    # round 7 (sweep: a font constructed before the observation and drawn during it went unseen): every font is also
    # recorded where it is DRAWN or measured (FreeTypeFont.getmask2, which Pillow's text drawing calls), whenever made
    orig_mask = ImageFont.FreeTypeFont.getmask2

    def getmask2(self, *a, **k):
        seen.update((site, round(float(self.size), 3)) for site in _sites())
        return orig_mask(self, *a, **k)
    ImageFont.FreeTypeFont.__init__ = init
    ImageFont.FreeTypeFont.getmask2 = getmask2
    try:
        yield seen
    finally:
        ImageFont.FreeTypeFont.__init__ = orig_init
        ImageFont.FreeTypeFont.getmask2 = orig_mask
        for mod, attr in patched:
            setattr(mod, attr, orig_get)
