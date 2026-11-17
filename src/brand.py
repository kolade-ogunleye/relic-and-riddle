"""
src/brand.py
============
The channel's colour tokens, in one place (visual overhaul, 2026-10-29). Until then each renderer kept its own
gold: (255, 215, 0) in the Story frames, carousels, graphics, covers and art direction, (212, 175, 55) on the
end card, so one Reel showed three golds side by side (visual critic round 4, conflict 3). Every renderer now
reads these; the values are the critic's measured palette (C:/tmp/visual_review/critic_round4/brand4.py), chosen
so each text colour clears WCAG 4.5:1 on INK with room to spare:

    BONE on INK 16.6:1, GOLD on INK 10.6:1, STONE_LT on INK 9.1:1, STONE on INK 6.3:1, INK on GOLD 10.6:1.

GOLD is the figure and accent colour (numbers, years, the active caption word, rules); BONE is the text colour
(warmer than pure white, so it sits on photographs without glare); INK is the ground (shades, halos, panels);
STONE / STONE_LT are secondary text (credits, gaps, notes); TEAL and CORAL are only for charts' second and third
series. DATA_FONT names the fixed-pitch family used for data figures (rulers, axes, indices, units on a drawing),
credits and the badge (typography.get_font), so a count-up's digits do not jitter and a credit reads as a printed
photo credit; a stated quantity (a claim's figure) is set in the display face (the design system, src/brand_system.py).
"""
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Tuple

RGB = Tuple[int, int, int]

# Role tokens (QF-35a, design-freeze ruling Q11, 2026-11-10): the palette, the font families and the masthead strings
# of the frozen designs live in ONE brand configuration object (BrandConfig), read when a design is drawn, never
# copied at import. `brand.GOLD`, `brand.DISPLAY_FONT` and `brand.MASTHEAD` are looked up in the active configuration
# on every read (the module __getattr__ below), so a second brand renders through the same code
# (`with brand.use(cfg): ...`). A design module reads them as `brand.<ROLE>` inside a function: `from .brand import
# GOLD` or a module-level copy would freeze today's value (tests/test_qf35a_role_tokens.py scans for both).
#
# Palette roles: the brand tokens (the critic's measured palette, 2026-10-29), then the design roles that were
# literals in the design modules until QF-35a, each value unchanged. A value may name another role (an alias), so a
# palette that changes INK also moves SEA.
BRAND_PALETTE: Mapping[str, RGB | str] = MappingProxyType({
    "INK": (11, 14, 18),          # #0B0E12, the ground
    "INK2": (21, 24, 30),         # #15181E, a panel one step up from the ground
    "BONE": (243, 237, 226),      # #F3EDE2, text
    "STONE": (154, 147, 133),     # #9A9385, quiet secondary text
    "STONE_LT": (184, 177, 163),  # #B8B1A3, secondary text that must still read at small sizes
    "GOLD": (242, 182, 48),       # #F2B630, figures, accents, the active caption word
    "GOLD_DIM": (138, 106, 30),   # #8A6A1E, a gold line that sits behind text (a timeline spine)
    "TEAL": (79, 179, 191),       # #4FB3BF, a chart's second series; the art direction's fallback accent
    "CORAL": (232, 115, 90),      # #E8735A, a chart's third series
    # maps and globes (graphics.py)
    "SEA": "INK",                 # the sea is the ground
    "LAND": (40, 43, 48),
    "HOME": (92, 74, 42),
    "BORDER": (96, 98, 104),
    "GRID": (24, 28, 34),
    "LAND_TINT": (88, 92, 100),   # the relief map's other land (round 6)
    "HOME_TINT": (168, 137, 81),  # the relief map's home country (round 6)
    "SEA_TOP": (14, 22, 34),      # the relief map's sea gradient, top
    "SEA_DEEP": (7, 11, 19),      # and bottom
    "MAP_NAME": "STONE_LT",       # a neighbour's name (round 6)
    "MAP_BORDER": "STONE",        # a neighbour's border (round 3)
    "COUNTRY_OUT_FILL": "STONE_LT",   # a country named outside its fill
    "GLOBE_LAND": (176, 170, 160),    # the context globe's other land (round 6)
    "GLOBE_LIMB": "GOLD",         # the globe's 2 px limb (round 6 review)
    "NEUTRAL_LAND": (57, 61, 64),     # a neutral (disputed or unmatched) ground on the globe and relief map (QF-20)
    "NEUTRAL_EDGE": (154, 160, 164),  # and its dashed border
    "GOLD_LIGHT": (250, 214, 120),    # the home coast's inner edge (round 6)
    "WATER_FILL": (110, 155, 166),    # a water body's name
    "PANEL": (10, 10, 12),        # the map title pill (alpha in the caller)
    "HATCH": (100, 115, 130),     # the cross-hatching stroke (alpha in the caller)
    # timelines, scales, sections (graphics.py)
    "SCALE_LINE": (232, 227, 218),    # #E8E3DA, the scale ruler's line, ticks and labels
    "TALL_LABEL_FILL": (189, 182, 170),   # #BDB6AA, a 9:16 timeline label (round 6)
    "EARTH": (38, 31, 24),        # the cross-section's ground
    "EARTH_LINE": (52, 43, 33),   # and its strata
    "ROCK_TOP": (36, 31, 27),     # the carved-rock ground's banded rock, at the surface
    "ROCK_DEEP": (18, 17, 17),    # and deepest
    # social frames (multi_format.py)
    "LEGIBLE_TEXT": (235, 235, 235),  # ensure_legible's assumed text colour
    "PLATE": (12, 16, 22),        # a text plate (alpha in the caller)
    # then-and-now (then_now.py)
    "GLOW_INK": (30, 22, 16),     # #1E1610, the year label's glow
    # art direction (art_direction.py)
    "ACCENT_BACKDROP": (12, 12, 14),  # the dark backdrop an accent's contrast is measured on
    "ACCENT": "TEAL",             # the fallback accent when the hero has no usable hue
})

# Font roles: families registered in typography.FONT_FILES (or font_assets.DERIVED_FAMILIES). None reads the
# configured setting (src/config.py: FONT_NAME for the 9:16 caption, CAPTION_WIDE_FONT for the 16:9 burn-in); a value
# that names another role is that role's family (an alias, so a brand that changes the data face moves the badge).
BRAND_FONTS: Mapping[str, str | None] = MappingProxyType({
    "display": "Anton",           # titles, headlines, card claims
    "masthead": "Cinzel",         # the masthead's serif
    "text": "Montserrat",         # kickers, labels, bodies, eyebrows
    "data": "IBM Plex Mono",      # figures, units, scale bars, credits (Medium 500 and SemiBold 600, OFL 1.1, fonts/)
    "caption": None,              # the 9:16 caption family (config.FONT_NAME)
    "caption_wide": None,         # the 16:9 burn-in family (config.CAPTION_WIDE_FONT)
    "badge": "data",              # the AI-reconstruction badge on the captions (config.RECON_FONT until the port)
})

# Strings: the masthead and the end card's brand lines.
BRAND_TEXT: Mapping[str, str] = MappingProxyType({
    "masthead": "RELIC & RIDDLE",
    "follow": "FOLLOW FOR THE NEXT RELIC",        # the end card's follow line
    "handoff": "The next riddle is right here.",  # the 16:9 end screen's hand-off to the watch-next slot
})


def _ratio(a: RGB, b: RGB) -> float:
    """The WCAG contrast ratio of two sRGB colours."""
    def lum(c):
        lin = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in (x / 255 for x in c)]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _system_tokens() -> Mapping[str, Any]:
    from .brand_system import SYSTEM_TOKENS
    return SYSTEM_TOKENS


@dataclass(frozen=True)
class BrandConfig:
    """One brand's role tokens: `palette` (role -> RGB, or another role's name), `fonts` (role -> family; None reads
    the configured setting), `text` (the masthead and the end card's lines) and `system` (the design system's tokens,
    src/brand_system.py). Every role of the default must be
    defined; `derive` gives a copy with some roles replaced."""
    name: str = "Relic & Riddle"
    palette: Mapping[str, RGB | str] = field(default_factory=lambda: BRAND_PALETTE)
    fonts: Mapping[str, str | None] = field(default_factory=lambda: BRAND_FONTS)
    text: Mapping[str, str] = field(default_factory=lambda: BRAND_TEXT)
    # The design system's tokens (src/brand_system.py: sizes, ratios, alphas, gaps, easings; cycle 0, 2026-11-15).
    system: Mapping[str, Any] = field(default_factory=lambda: _system_tokens())

    def __post_init__(self) -> None:
        for attr, base in (("palette", BRAND_PALETTE), ("fonts", BRAND_FONTS), ("text", BRAND_TEXT),
                           ("system", _system_tokens())):
            value = getattr(self, attr)
            missing = sorted(set(base) - set(value))
            if missing:
                raise ValueError(f"brand {self.name!r}: {attr} lacks the roles {missing}")
            object.__setattr__(self, attr, MappingProxyType(dict(value)))
        from .brand_system import GATES, SYSTEM_TOKENS
        ruled = sorted(set(self.system) & set(GATES))
        if ruled:
            raise ValueError(f"brand {self.name!r}: {ruled} are gates (operator rulings), not brand tokens")
        unknown = sorted(set(self.system) - set(SYSTEM_TOKENS))
        if unknown:   # a SPEC value no renderer reads yet, or a typo: an override that would change nothing
            raise ValueError(f"brand {self.name!r}: {unknown} are not runtime tokens (src/brand_system.py)")
        from .brand_system import TOKEN_RANGES
        for key, (lo, hi) in TOKEN_RANGES.items():
            value = self.system[key]
            if not (isinstance(value, (int, float)) and lo <= value <= hi):
                raise ValueError(f"brand {self.name!r}: {key} = {value!r} is outside its range {lo}..{hi}")
        from .brand_system import TOKEN_JOINT
        for key, factor, other in TOKEN_JOINT:
            if self.system[key] > factor * self.system[other] + 1e-12:
                raise ValueError(f"brand {self.name!r}: {key} = {self.system[key]} exceeds {factor} x {other} "
                                 f"({self.system[other]}): the two interact (src/brand_system.py TOKEN_JOINT)")
        for role in self.palette:
            self.colour(role)
        # The mark (cycle 0 round 6, adversary r5-1: GOLD = INK drew an invisible diamond): its gold fill is what reads
        # on an INK ground, its INK edge what reads on a bone one; a palette where either misses the mark gate
        # (WCAG 1.4.11) is refused here, before any surface draws it.
        from .brand_system import GATES as _G
        mark_gate = _G["contrast.mark"]
        for a, b in (("GOLD", "INK"), ("INK", "BONE")):
            r = _ratio(self.colour(a), self.colour(b))
            if r < mark_gate:
                raise ValueError(f"brand {self.name!r}: the mark's {a} against {b} is {r:.2f}:1, under the "
                                 f"{mark_gate}:1 mark gate (src/brand_system.py contrast.mark)")
        # The kicker (cycle 0 round 7, sweep S1: kicker.colour = INK drew a Story kicker at 1.55:1): its colour sits on
        # the INK text band; a colour that cannot reach the kicker gate there is refused before any surface draws it
        # (on a busy ground multi_format.plan_kicker then takes bone, or draws none).
        k_role = self.system.get("kicker.colour")
        if k_role is not None:
            r = _ratio(self.colour(k_role), self.colour("INK"))
            if r < _G["contrast.kicker"]:
                raise ValueError(f"brand {self.name!r}: kicker.colour {k_role} against INK is {r:.2f}:1, under the "
                                 f"{_G['contrast.kicker']}:1 kicker gate (src/brand_system.py contrast.kicker)")
        for role in self.fonts:
            self._font_role(role)

    def derive(self, name: str, palette: Mapping | None = None, fonts: Mapping | None = None,
               text: Mapping | None = None, system: Mapping | None = None) -> "BrandConfig":
        return BrandConfig(name, {**self.palette, **(palette or {})}, {**self.fonts, **(fonts or {})},
                           {**self.text, **(text or {})}, {**self.system, **(system or {})})

    def colour(self, role: str) -> RGB:
        value, seen = self.palette[role], {role}
        while isinstance(value, str):
            if value in seen:
                raise ValueError(f"brand {self.name!r}: palette alias loop at {role}")
            seen.add(value)
            value = self.palette[value]
        return (int(value[0]), int(value[1]), int(value[2]))

    def _font_role(self, role: str) -> str:
        """The role whose family `role` reads (itself, or the end of its alias chain)."""
        seen = {role}
        while (family := self.fonts[role]) in self.fonts:
            if family in seen:
                raise ValueError(f"brand {self.name!r}: font alias loop at {role}")
            seen.add(family)
            role = family
        return role

    def font(self, role: str) -> str:
        role = self._font_role(role)
        family = self.fonts[role]
        if family is None:
            from . import config
            family = {"caption": config.FONT_NAME, "caption_wide": config.CAPTION_WIDE_FONT}[role]
        return family


DEFAULT_BRAND = BrandConfig()
_active: BrandConfig = DEFAULT_BRAND
_on_change: list = []


def on_change(clear) -> None:
    """Call `clear()` whenever the active brand changes: a cache a design module keeps of a measure taken in a brand
    face (keyed without the family) registers its cache_clear here, so it never serves the previous brand's."""
    _on_change.append(clear)


def active() -> BrandConfig:
    """The brand configuration every design reads now."""
    return _active


def configure(config: BrandConfig | None) -> BrandConfig:
    """Make `config` (None: the default) the active brand for the whole process; returns the one it replaced."""
    global _active
    previous, _active = _active, config or DEFAULT_BRAND
    if _active is not previous:
        for clear in _on_change:
            clear()
    return previous


@contextmanager
def use(config: BrandConfig) -> Iterator[BrandConfig]:
    """Draw with `config` inside the block (process-wide, so the graphics' frame threads see it too)."""
    previous = configure(config)
    try:
        yield config
    finally:
        configure(previous)


FONT_ROLES = {"DISPLAY_FONT": "display", "MASTHEAD_FONT": "masthead", "TEXT_FONT": "text", "DATA_FONT": "data",
              "CAPTION_FONT": "caption", "CAPTION_WIDE_FONT": "caption_wide", "BADGE_FONT": "badge"}
TEXT_ROLES = {"MASTHEAD": "masthead", "FOLLOW_LINE": "follow", "HANDOFF_LINE": "handoff"}


def __getattr__(name: str):
    """`brand.GOLD`, `brand.DISPLAY_FONT`, `brand.MASTHEAD`: the active brand's role at the moment of the read."""
    cfg = _active
    if name in cfg.palette:
        return cfg.colour(name)
    if name in FONT_ROLES:
        return cfg.font(FONT_ROLES[name])
    if name in TEXT_ROLES:
        return cfg.text[TEXT_ROLES[name]]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(_active.palette) | set(FONT_ROLES) | set(TEXT_ROLES))


TEXT_CONTRAST = 4.5              # WCAG AA: every text element, measured on the delivered pixels (= contrast.text)
PLACE_TARGET = 4.8               # what place_text aims for, so JPEG and the master's conversions still leave >= 4.5
                                 # (= contrast.place; both gates stay constants: a brand never lowers them)


def rgba(colour: RGB, alpha: int = 255) -> Tuple[int, int, int, int]:
    return (int(colour[0]), int(colour[1]), int(colour[2]), int(alpha))


def hex_colour(colour: RGB) -> str:
    return "#%02X%02X%02X" % tuple(int(c) for c in colour[:3])


def ass_colour(colour: RGB, alpha: int = 0) -> str:
    """An ASS subtitle colour (&HAABBGGRR&: blue first; alpha 0 is opaque)."""
    r, g, b = (int(c) for c in colour[:3])
    return "&H%02X%02X%02X%02X&" % (int(alpha), b, g, r)
