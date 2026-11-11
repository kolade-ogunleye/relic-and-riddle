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
series. DATA_FONT names the fixed-pitch family used for figures and data (typography.get_font), so a count-up's
digits do not jitter and a credit reads as a printed photo credit.
"""
from typing import Tuple

RGB = Tuple[int, int, int]

INK: RGB = (11, 14, 18)          # #0B0E12, the ground
INK2: RGB = (21, 24, 30)         # #15181E, a panel one step up from the ground
BONE: RGB = (243, 237, 226)      # #F3EDE2, text
STONE: RGB = (154, 147, 133)     # #9A9385, quiet secondary text
STONE_LT: RGB = (184, 177, 163)  # #B8B1A3, secondary text that must still read at small sizes
GOLD: RGB = (242, 182, 48)       # #F2B630, figures, accents, the active caption word
GOLD_DIM: RGB = (138, 106, 30)   # #8A6A1E, a gold line that sits behind text (a timeline spine)
TEAL: RGB = (79, 179, 191)       # #4FB3BF, a chart's second series
CORAL: RGB = (232, 115, 90)      # #E8735A, a chart's third series

DATA_FONT = "IBM Plex Mono"      # typography.get_font family: Medium 500 and SemiBold 600, OFL 1.1 (fonts/)

TEXT_CONTRAST = 4.5              # WCAG AA: every text element, measured on the delivered pixels
PLACE_TARGET = 4.8               # what place_text aims for, so JPEG and the master's conversions still leave >= 4.5


def rgba(colour: RGB, alpha: int = 255) -> Tuple[int, int, int, int]:
    return (int(colour[0]), int(colour[1]), int(colour[2]), int(alpha))


def hex_colour(colour: RGB) -> str:
    return "#%02X%02X%02X" % tuple(int(c) for c in colour[:3])


def ass_colour(colour: RGB, alpha: int = 0) -> str:
    """An ASS subtitle colour (&HAABBGGRR&: blue first; alpha 0 is opaque)."""
    r, g, b = (int(c) for c in colour[:3])
    return "&H%02X%02X%02X%02X&" % (int(alpha), b, g, r)
