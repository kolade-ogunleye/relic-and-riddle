"""Design freeze, SYS batch 2 (the lander's approved system requests, 2026-11-19): invariants, never pinned
measurements. Synthetic inputs only (CI has no output/).

- SYS-D-5 (ruling c): the heading engine takes a balanced break at the design size before one line at the fallback
  tier, and a carousel deck sets its content headings at ONE tier, over both copies (the fallback only deck-wide).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from src import brand, brand_system, heading
from src.typography import get_font

_D = ImageDraw.Draw(Image.new("RGB", (8, 8)))


def _measure(t: str, size: float) -> float:
    return _D.textlength(t, font=get_font(brand.DISPLAY_FONT, size))


# ------------------------------------------------------------------ SYS-D-5: the design size before the fallback
@pytest.mark.parametrize("text", ["MAGMATIC UPLIFT", "ORBITAL LANDMARK", "FORGOTTEN KNOWLEDGE", "ENDURING LANDMARKS"])
def test_a_balanced_break_at_the_design_size_beats_one_line_at_the_fallback(text):
    big, small = 138.0, 124.0
    one_big, one_small = _measure(text, big), _measure(text, small)
    width = (one_big + one_small) / 2           # one line fits only at the fallback tier
    form = heading.set_heading(text, _measure, width, (big, small))
    two = heading.set_heading(text, _measure, width, (big,), max_lines=2)
    lens = [_measure(t, big) for t in two.lines] if two else []
    balanced = bool(two and len(lens) == 2 and min(lens) / max(lens) >= brand_system.SPEC["heading.balance_min"])
    if balanced:
        assert form.size == big and form.tier == 0 and len(form.lines) == 2
    else:
        assert form.size == small and len(form.lines) == 1


def test_an_unbalanced_break_at_the_design_size_does_not_beat_one_line_at_the_fallback():
    # TWO / ANCIENT ERAS is a valid but unbalanced break: the fallback's one line keeps its place before it
    text = "TWO ANCIENT ERAS"
    big, small = 138.0, 124.0
    width = (_measure(text, big) + _measure(text, small)) / 2
    form = heading.set_heading(text, _measure, width, (big, small))
    lens = [_measure(t, big) for t in heading.lines_of(text.split(), (1,))]
    assert min(lens) / max(lens) < brand_system.SPEC["heading.balance_min"]
    assert form.size == small and len(form.lines) == 1


def test_a_one_size_ladder_is_unchanged_by_the_order():
    for text in ("LINES IN THE SAND", "MAGMATIC UPLIFT", "THE EYE OF THE SAHARA"):
        for width in (400.0, 700.0, 1200.0):
            form = heading.set_heading(text, _measure, width, (138.0,))
            if form is not None:
                assert form.size == 138.0 and form.tier == 0


def _gen():
    from src import multi_format as mf
    return mf, mf.MultiFormatGenerator()


def _content_sizes(mf, gen, cards, tier):
    from src.script_engine import facebook_photo_caption, neutral_cues
    cw = gen.CAROUSEL_SIZE[0]
    px = gen._px(cw)
    width = cw - 2 * px(gen.MARGIN)
    arch = gen.sequence_archetypes(cards)
    out = []
    prev = getattr(mf._render_state, "heading_tier", 0)
    mf._render_state.heading_tier = tier
    try:
        for fb in (False, True):
            clean = facebook_photo_caption if fb else neutral_cues
            mf._render_state.facebook = fb
            for card, a in zip(cards, arch):
                if a in gen.NON_CONTENT_ARCHETYPES:
                    continue
                text = mf.typeset(clean(card["heading"])).upper()
                form, _ = gen._set_heading(_D, px, text, width, "wrap", 1.0, gen.CONTENT_HEADING, True)
                out.append(form)
    finally:
        mf._render_state.heading_tier = prev
        mf._render_state.facebook = False
    return out


def _deck(headings):
    cards = [{"heading": "THE EYE OF THE SAHARA", "body": "A cover."}]
    cards += [{"heading": h, "body": "A plain fact about the site, told in one short line."} for h in headings]
    cards += [{"heading": "WHAT DO YOU THINK?", "body": "Tell us below?"}]
    return cards


@pytest.mark.parametrize("headings", [
    ["MAGMATIC UPLIFT", "IMPACT THEORY DISPROVED", "GLOBAL HERITAGE", "ORBITAL LANDMARK"],
    ["A DIPLOMATIC GIFT", "MORNING MIST ARMOR", "SCRAPING THE PEBBLES", "TWO ANCIENT ERAS"],
    ["HIDDEN GEARWORK", "COMPLEX MECHANICS", "PLANETARY CYCLES", "LOST FOR CENTURIES"],
])
def test_a_deck_sets_its_content_headings_at_one_ladder_size_on_both_copies(headings):
    mf, gen = _gen()
    cards = _deck(headings)
    tier = gen._deck_heading_tier(cards, gen.sequence_archetypes(cards))
    forms = _content_sizes(mf, gen, cards, tier)
    on_ladder = {f.size for f in forms if f.tier is not None}
    assert len(on_ladder) == 1, on_ladder
    assert on_ladder == {gen.CONTENT_HEADING[tier]}
    # the fallback only deck-wide: tier 1 only when some heading does not set at the design size
    design = _content_sizes(mf, gen, cards, 0)
    assert tier == (1 if any(f.tier != 0 for f in design) else 0)


def test_the_deck_tier_travels_in_the_slide_recipe_and_its_redraw():
    import inspect

    from src import multi_format as mf
    src = inspect.getsource(mf.MultiFormatGenerator.generate_carousel_sequence)
    assert '"heading_tier": tier' in src and "heading_tier=tier" in src
    assert 'heading_tier=int(recipe.get("heading_tier") or 0)' in inspect.getsource(mf.redraw_carousel_slide)

