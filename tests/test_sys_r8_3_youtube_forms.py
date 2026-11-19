"""SYS-R8-3 (design freeze cycle 0, round 8): the YouTube headline lockup searches every form the heading engine
treats as valid before it refuses.

R2 (heading.lockup_valid) refuses a stacked lockup with a one-word middle row: a two-word field the 904 px column
cannot hold whole must break, and under the other field it leaves one word alone (ROMAN / CONCRETE / HEALS ITSELF).
Before refusing, youtube_thumbnail._lockup now also sets the copy's words as ONE heading (every valid break of the
whole, up to three rows, on the same ladder held at the same floor). Invariants, not pinned measurements:

- a drawn lockup never has a one-word middle row, every row is at or above the floor, at most three rows, and the
  drawn words are the copy's words in order;
- the class is closed: an exhaustive oracle (every cut of the copy's words into one to three rows, each row fitting
  the column at a ladder size held at the floor, valid under the engine's own rules) agrees with _lockup: a copy is
  refused only when no valid form exists, and set whenever one does.
"""
from __future__ import annotations

import itertools
import re

import pytest
from PIL import Image, ImageDraw

from src import brand, heading, typography
from src import youtube_thumbnail as yt
from src.brand_system import TYPE, TYPE_FALLBACK
from src.refusal import LayoutError

WIDTH = 904   # the thumbnail's full type column (no reviewed feature narrows it): the column cover_fit checks

# Two-field copies of the class (a field of two or more words the column may not hold whole), the stress set's shape:
# each is judged by the oracle, never by a pinned outcome
COPIES = [("ROMAN CONCRETE", "HEALS ITSELF"), ("ROMAN CONCRETE", "THAT HEALS ITSELF"), ("LOST SCIENCE", "GREEK ASTRONOMY"),
          ("THE SECRET CITY", "BENEATH THE FLOOR"), ("THE 1,200-TON", "BEDROCK MONSTER"), ("THE SINGING", "720-TON TITAN"),
          ("CARVED FROM", "SOLID SALT"), ("CARVED FROM THE", "MOUNTAIN DOWN"), ("BRONZE GEARS", "LOST IN THE SEA"),
          ("THE 30-MILE DOME", "SEEN FROM SPACE"), ("THE SCRIPT NO ONE", "CAN DECIPHER"), ("GIANT GROUND", "CARVINGS"),
          ("PREHISTORIC SKULL", "SURGERY IN PERU"), ("ANCIENT GREEK", "ANALOG COMPUTER"), ("THE MASSIVE", "WHISPERING DOME"),
          ("BEHIND ONE WALL", "A FORGOTTEN CITY"), ("ONE WALL", "A LOST CITY"), ("WHY ROMAN CONCRETE", "HEALS ITSELF"),
          ("THE EYE OF", "THE SAHARA"), ("TRAPPED IN BEDROCK", "NEVER COMPLETED"), ("DRAWN WITHOUT", "EVER FLYING"),
          ("THE SECRET", "WEAPON"), ("STONE", "GIANTS OF THE PLAIN")]


def _draw():
    return ImageDraw.Draw(Image.new("RGB", (8, 8)))


def _width(text, size):
    return _draw().textlength(text, font=typography.get_font(brand.DISPLAY_FONT, size))


def _sizes():
    floor = yt.feed_floor_px(yt.HERO_FLOOR)
    ladder = (round(TYPE["wide"]["display_xl"]), round(TYPE_FALLBACK["wide"]["display_xl"]))
    return floor, sorted({max(s, floor) for s in ladder}, reverse=True)


def _words(l1, l2):
    return (l1 + " " + l2).split()


def _oracle(l1, l2, width=WIDTH):
    """Every valid form of the copy at or above the floor in at most three rows: [(rows, size)]. A form is valid under
    the engine's own rules (heading.valid) with the payoff seam at the ruled field boundary (heading.lockup_fields).
    Lane B cycle 1: the authored field boundary is a line end like any other (never on a never-end word: CARVED FROM /
    is not a form)."""
    _, sizes = _sizes()
    r1, r2, _ = heading.lockup_fields(l1, l2)
    words = _words(r1, r2)
    seams = ({len(r1.split()), len(l1.split())} if l1 and l2 else set()) if r2 else {None}
    out = []
    for size in sizes:
        for n in (0, 1, 2):
            for cuts in itertools.combinations(range(1, len(words)), n):
                rows = heading.lines_of(words, cuts)
                widths = [_width(t, size) for t in rows]
                if max(widths) > width:
                    continue
                if any(heading.valid(rows, widths=widths, seam=s) for s in seams):
                    out.append((rows, size))
    return out


def _drawn(got):
    rows, f1, f2, rec = got
    lines = [" ".join(t for _, t, _ in runs) for _, runs in rows]
    sizes = [f1.size if k == "title_1" else f2.size for k, _ in rows]
    return lines, sizes, rec


@pytest.mark.parametrize("l1, l2", COPIES)
def test_a_drawn_youtube_lockup_obeys_the_rules_and_a_refusal_means_no_valid_form_exists(l1, l2):
    floor, _ = _sizes()
    forms = _oracle(l1, l2)
    for kickerless in (True, False):
        try:
            got = yt._lockup(_draw(), l1, l2, WIDTH, kickerless=kickerless)
        except LayoutError as e:
            # refused only where nothing valid sets: the class is closed, copy that does not fit goes to the copy edit
            assert not forms, (l1, l2, forms)
            assert re.search(r"feed floor", str(e))
            continue
        lines, sizes, rec = _drawn(got)
        assert forms, (l1, l2)
        assert len(lines) <= 3
        assert min(sizes) >= floor and min(rec["sizes"]) >= floor
        assert all(_width(t, s) <= WIDTH + 1e-6 for t, s in zip(lines, sizes))
        # no one-word middle row (R2), in any form
        assert all(len(t.split()) >= 2 for t in lines[1:-1]), lines
        # the drawn words are the copy's words, in order (a break comma is the only mark a break may drop)
        assert [w.rstrip(",") for w in " ".join(lines).split()] == [w.rstrip(",") for w in _words(l1, l2)]
        assert rec["drawn_rows"] == lines and rec["form"] in ("ruled", "one heading")
        # lane B cycle 1: no row but the last ends on a never-end word, in any form (the authored bypass is closed)
        assert all(heading.end_ok(t.split()[-1]) for t in lines[:-1]), lines
        if rec["form"] != "ruled":
            assert rec["form_diverges"]["form"] == rec["form"] and rec["form_diverges"]["reason"]
            assert rec["form_diverges"]["rows"] == lines
        if rec["form"] == "one heading":
            # the payoff run gold from the copy's own field seam: no word before it is gold
            seam = rec["seam"]
            assert seam == len(heading.lockup_fields(l1, l2)[0].split())
            assert heading.lockup_valid(lines, [_width(t, s) for t, s in zip(lines, sizes)], seam)
            figure = heading._figure_words(_words(l1, l2))   # a stated quantity is gold wherever it stands
            gold = [i for i, (is_gold, _) in enumerate(rows_words(got)) if is_gold and i not in figure]
            assert all(i >= seam for i in gold), (lines, gold)


def rows_words(got):
    """[(gold?, word)] over the drawn rows, in order."""
    rows, *_ = got
    out = []
    for _, runs in rows:
        for _, t, colour in runs:
            out += [(tuple(colour) == tuple(brand.GOLD), w) for w in t.split()]
    return out


def test_the_class_of_a_two_word_field_that_must_break_now_sets_where_a_valid_form_exists():
    """The previously refused class: a field the column cannot hold whole at the floor, whose break under the other
    field leaves a one-word middle row. Wherever the oracle finds any valid form, _lockup draws one (never refused)."""
    _, sizes = _sizes()
    found = 0
    for l1, l2 in COPIES:
        r1, r2, _ = heading.lockup_fields(l1, l2)
        must_break = r2 and any(len(f.split()) >= 2 and _width(f, sizes[-1]) > WIDTH for f in (r1, r2))
        if not must_break or not _oracle(l1, l2):
            continue
        found += 1
        lines, _, rec = _drawn(yt._lockup(_draw(), l1, l2, WIDTH))
        assert all(len(t.split()) >= 2 for t in lines[1:-1]), lines
    assert found   # the set holds members of the class (else this test proves nothing)



def test_a_youtube_refusal_reaches_the_reel_copy_retry_only_where_the_reel_publishes_a_youtube_thumbnail(monkeypatch):
    """Routing: a Reel run draws no 16:9 YouTube thumbnail (delivery profile reel_9x16), so its copy is never sent back
    for one; were its profile to require one, the same copy retry (script_engine.cover_fit_problems) would check it."""
    from types import SimpleNamespace

    from src import cover_fit, delivery_profiles, script_engine
    copy = {"eyebrow": "THE EVIDENCE", "line1": "ROMAN CONCRETE", "line2": "HEALS ITSELF", "tagline": "HOW IT WORKS"}
    pkg = SimpleNamespace(thumbnail=SimpleNamespace(model_dump=lambda: dict(copy)), title="T", narration_script="x.",
                          youtube_title="")
    cover_fit.clear()
    youtube = cover_fit.fit_problems(copy, reel=False)
    reel = cover_fit.fit_problems(copy, youtube=False)
    assert youtube and not reel   # a copy the YouTube column refuses (no valid form) and the Reel cover sets
    assert delivery_profiles.get_delivery_profile("reel").requires_youtube_thumbnail is False
    assert script_engine.cover_fit_problems(pkg) == reel
    monkeypatch.setitem(delivery_profiles.PROFILES, "reel_9x16", delivery_profiles.PROFILES["reel_9x16"].model_copy(
        update={"requires_youtube_thumbnail": True}))
    assert script_engine.cover_fit_problems(pkg) == youtube + reel


def test_the_deep_dive_fit_check_reads_the_copy_the_thumbnail_render_sets(monkeypatch, tmp_path):
    """visual_engine.youtube_cover_copy is the one copy: the render's and the pre-render check's (main's deep-dive
    draft retry), each figure in the narration's unit."""
    from src import visual_engine
    payload = {"thumbnail": {"eyebrow": "DOCUMENTARY", "line1": "A 40 KM RING", "line2": "IN THE DESERT",
                             "tagline": "EXPLORE THE EVIDENCE"},
               "title": "T", "narration_script": "The ring is about 25 miles across."}
    seen = {}

    def fake_render(*a, copy, **k):
        seen["copy"] = {k2: copy[k2] for k2 in ("eyebrow", "line1", "line2", "tagline")}
        raise LayoutError("stop")
    monkeypatch.setattr(yt, "render", fake_render)
    hero = tmp_path / "hero.jpg"
    Image.new("RGB", (64, 36), (90, 80, 70)).save(hero)
    with pytest.raises(LayoutError):
        visual_engine.VisualEngine.generate_youtube_thumbnail(None, hero, payload, tmp_path / "yt.jpg")
    assert seen["copy"] == visual_engine.youtube_cover_copy(payload)
