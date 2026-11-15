"""
src/hold_advice.py
==================
What a hold reason tells the operator to do, phrased from the actions the surface it is read on offers (UI-01h).

The producers of a hold reason (the release gate's checks, QF-126/126b's credit holds) write their advice once, in the
words below (the canonical form: a log, the CLI and a stored report keep it). A reason is read on several surfaces,
and each offers other buttons: the review panel has Edit text and Regenerate; a booking held at its slot has Make a
new version and Cancel booking; a run the release gate failed (auto-approve, the CLI) has Re-render, Make again or
Back to the queue, and Details. "Re-send the review or save any Edit" on a held booking, or on a run that was failed,
named buttons those surfaces do not have (LAND-33 port review 1). for_surface() rewrites each canonical advice into
the surface's own words: the review names its buttons; the other surfaces say what is needed without a verb, and
their own next-step line (posting_view.BLOCKED_NEXT, the failed run's Details) names their buttons.

This is the one table of those words (tests/test_ui01h_hold_advice.py checks every hold reason on every surface
against the buttons that surface shows, with its own vocabulary of remedies).
"""

from __future__ import annotations

REVIEW = "review"   # the review panel (and the earlier chat's review card): Edit text, Regenerate, Reject
BOOKED = "booked"   # a booking held at its slot (QF-110): its item and its Details (Make a new version, Cancel booking)
FAILED = "failed"   # a run the release gate failed or blocked: its item, its Details, the late-failure item
SURFACES = (REVIEW, BOOKED, FAILED)

# ---------------------------------------------------------------- the canonical advice, as the producers write it
# src/credits.py, credit_reach_problems: a credit that fits once the credits are written again (QF-126b) ...
FIT_LINKS = " (re-send or any Edit)"
FIT_WRITE = ("While it waits for review, re-send the review or save any Edit to write them; a post already approved "
             "or booked needs a new version, which is written with them.")
# ... credits that do not fit the text (QF-126) ...
SHORTEN = "Shorten the text so they fit (Edit, for a caption), or Regenerate with fewer photos that need a credit."
SHORTEN_LIMIT = ("Shorten it (Edit, for a caption) or Regenerate with fewer photos that need a credit: a credit is "
                 "never shortened or left out.")
# ... and, in incomplete_credit, an author the source names that the credit does not print.
AUTHOR_REGENERATE = ", or Regenerate without this photo"
# src/release.py: a credit line with a link, address, handle or hashtag (QF-42) ...
CREDIT_LINES = "Edit the text or Regenerate: either rewrites the credit lines."
# ... a Story frame drawn before Story credits were drawn ...
STORY_REDRAW = ": redraw the Stories)"
# ... and captions shown faster than they are read (QF-88).
CAPTION_RATE = "Re-render: the caption timing must hold them longer."

# src/picture_qa.py, src/audio_qa.py and src/release.py: the picture, sound and colour holds whose remedy is a new
# render (QF-10, QF-11, QF-88; UI-01h round 2). The found facts and the diagnosis before them are kept word for word.
BLACK = "check the cut or transition there and re-render."
FREEZE = "shorten that shot or give it motion, then re-render."
BARS = "re-render it filling the frame"
RANGE = "(a full-range picture tagged as limited range): re-render it."
FLASH = "slow, shrink or remove the flashing there, then re-render."
FLASH_HELD = "clear it; if it does, re-render."
COLOUR_TAGS = "wrongly: re-render it to tag it BT.709."
COLOUR_GRANDFATHERED = "It can still be posted; re-render it to tag it."
DROPOUT = "check the voice and the music there and re-render."
GRAPHIC_COLOURS = ": re-render with an accent clear of them."

# Where a new render is made, in each surface's words: the review's Regenerate makes one; a held booking's Make a new
# version is one; a failed run's Details say whether and how it can be made again.
_NEW_RENDER = {REVIEW: ": Regenerate makes one", BOOKED: " (a new version is one)", FAILED: ""}


def _render_phrases(before: str, after: str = ".") -> dict[str, str]:
    return {surface: f"{before}{where}{after}" for surface, where in _NEW_RENDER.items()}


# Each canonical advice in each surface's words. The review offers Edit text and Regenerate. A held booking offers
# Make a new version (its review offers Edit text and Regenerate) and Cancel booking, which BLOCKED_NEXT names; a
# failed run offers what its Details and its item show. Neither names a button here.
PHRASES: dict[str, dict[str, str]] = {
    FIT_LINKS: {REVIEW: "", BOOKED: "", FAILED: ""},
    FIT_WRITE: {
        REVIEW: "Save an Edit text that changes the copy to write them, or Regenerate.",
        BOOKED: "A new version is written with them.",
        FAILED: "A new render is written with them.",
    },
    SHORTEN: {
        REVIEW: "Shorten the text so they fit (Edit text, for a caption), or Regenerate with fewer photos that need "
                "a credit.",
        BOOKED: "The text must be shorter for them to fit, or the video use fewer photos that need a credit: both "
                "are done in the review of a new version.",
        FAILED: "The text must be shorter for them to fit, or the video use fewer photos that need a credit.",
    },
    SHORTEN_LIMIT: {
        REVIEW: "Shorten it (Edit text, for a caption) or Regenerate with fewer photos that need a credit: a credit "
                "is never shortened or left out.",
        BOOKED: "It must be shorter, or the video use fewer photos that need a credit (both are done in the review "
                "of a new version): a credit is never shortened or left out.",
        FAILED: "It must be shorter, or the video use fewer photos that need a credit: a credit is never shortened "
                "or left out.",
    },
    AUTHOR_REGENERATE: {
        REVIEW: AUTHOR_REGENERATE,
        BOOKED: ", or leave this photo out of a new version",
        FAILED: ", or leave this photo out of a new render",
    },
    CREDIT_LINES: {
        REVIEW: "Edit text or Regenerate: either rewrites the credit lines.",
        BOOKED: "A new version rewrites the credit lines.",
        FAILED: "A new render rewrites the credit lines.",
    },
    STORY_REDRAW: {
        REVIEW: ": the Stories must be drawn again, in a new render: Regenerate makes one)",
        BOOKED: ": the Stories must be drawn again)",
        FAILED: ": the Stories must be drawn again)",
    },
    CAPTION_RATE: {
        REVIEW: "A new render must hold them longer: Regenerate makes one.",
        BOOKED: "A new render must hold them longer: a new version is one.",
        FAILED: "A new render must hold them longer.",
    },
    BLACK: _render_phrases("check the cut or transition there in a new render"),
    FREEZE: _render_phrases("shorten that shot or give it motion in a new render"),
    BARS: _render_phrases("a new render must fill the frame", ""),
    RANGE: _render_phrases("(a full-range picture tagged as limited range): a new render tags it right"),
    FLASH: _render_phrases("slow, shrink or remove the flashing there in a new render"),
    FLASH_HELD: _render_phrases("clear it; if it does, it needs a new render"),
    COLOUR_TAGS: _render_phrases("wrongly: a new render tags it BT.709"),
    COLOUR_GRANDFATHERED: _render_phrases("It can still be posted; a new render tags it"),
    DROPOUT: _render_phrases("check the voice and the music there in a new render"),
    GRAPHIC_COLOURS: _render_phrases(": a new render needs an accent clear of them"),
}


def for_surface(text: str, surface: str) -> str:
    """`text` (a hold reason, or several joined) with each canonical advice in `surface`'s words; the text as it is
    for an unknown surface. A reason already in a surface's words is left as it is (the words differ)."""
    if surface not in SURFACES or not text:
        return text
    for canonical, phrased in PHRASES.items():
        if canonical in text:
            text = text.replace(canonical, phrased[surface])
    return text
