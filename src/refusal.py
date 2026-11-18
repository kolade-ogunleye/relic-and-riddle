"""
src/refusal.py
==============
The one refusal type (design-freeze cycle 0 round 8, operator ruling 6): every renderer that cannot draw a surface
as designed, without cutting checked copy, dropping a letter or going under a floor or a gate, raises LayoutError
(or a subclass naming the particular refusal: typography.TextFitError, font_engine.GlyphRefused,
design_cards.CardRefused, multi_format.StoryCreditFitError), and every caller records it for that surface alone
(the stills' hold, main.hold_layout_error; the graphics' make_all; the cards' card_composition; the deep dive's cover
job, per surface), never letting one surface's refusal abort its siblings (the round-7 Tunguska job lost its Reel
cover and grid to the YouTube thumbnail's TextFitError). Its own module so the font engine, which the typography
module imports, can subclass it too.
"""
from __future__ import annotations


class LayoutError(ValueError):
    """A card, slide, cover or graphic that cannot be drawn as designed without cutting checked copy (or, in the
    parked freeze work, without dropping a required credit): the surface is refused and held, never delivered
    shortened (QF-23; freeze panel round 2, F1 and F8). The one refusal type: the stills' hold
    (main.hold_layout_error), the graphics' (graphics.make_all) and the review card's "Held" list all catch this
    class, and every more particular refusal subclasses it (TextFitError; the park's BodyFitError; round 8:
    GlyphRefused and CardRefused). `reason` and `code` are what a caller records."""

    @property
    def reason(self) -> str:
        return getattr(self, "_reason", None) or str(self)

    @reason.setter
    def reason(self, value: str) -> None:
        self._reason = value

    @property
    def code(self) -> str:
        return getattr(self, "_code", None) or type(self).__name__

    @code.setter
    def code(self, value: str) -> None:
        self._code = value


def record(error: BaseException, surface: str) -> dict:
    """A refusal as a caller records it: the surface, the refusal's class and code, and its reason."""
    return {"surface": surface, "refused": type(error).__name__, "code": getattr(error, "code", "") or "",
            "reason": getattr(error, "reason", None) or str(error)}
