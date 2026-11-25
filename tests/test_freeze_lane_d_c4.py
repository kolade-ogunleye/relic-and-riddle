"""Design-freeze lane D (carousel and Stories), cycle 4: invariants. Synthetic inputs (CI has no output/).

1. Slide-drop fallback (cycle 3 ruling b): a content slide the figure gate leaves with nothing readable is dropped,
   recorded, when the deck stays coherent (cover and closing slide kept, at least CAROUSEL_MIN_SLIDES left); otherwise
   the deck is refused as before. A shorter deck leaves no older slide file behind.
2. A source's own digital-black frame (a spacecraft window's wedge, a scan border) never shows in a photo window where
   a window within FRAME_ZOOM_MAX holding the centre clears it; a photographed dark is never taken for one.
3. The deck's photo choice: no photograph twice while the pool has an unused one; no photo whose review saw visitors
   or modern presence on a line not about the subject today while the pool has an unused one without; every slide's
   reason recorded.
4. A scale diagram slide names the landmark its drawing draws (no figure added); a copy that names it is left alone.
5. The Story hook frame's pointer names no link it lacks.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import still_layout
from src.multi_format import (
    CAROUSEL_MIN_SLIDES,
    FRAME_ZOOM_MAX,
    SLIDE_DROP_RULE,
    STORY_POINTER,
    MultiFormatGenerator,
    deck_photo_guard,
    diagram_comparator,
    frame_mask,
    frame_window,
    prune_stale_slides,
    record_dropped,
    slides_to_drop,
)
from tests.figure_evidence import signed

SYN = {"title": "The Bronze Machine", "narration_script": "The wreck lies about 45 metres down. It held 82 fragments."}


def _deck(n: int = 7) -> list[dict]:
    cards = [{"heading": "THE BRONZE MACHINE", "body": "A machine of gears lay in the wreck."}]
    cards += [{"heading": f"FACT {chr(65 + i)}", "body": "Divers raised it from the wreck."} for i in range(n - 2)]
    return cards + [{"heading": "YOUR TAKE", "body": "Could anyone build it again?"}]


# ------------------------------------------------------------------------------------------------ 1. slide drop
def test_an_interior_slide_left_with_nothing_readable_is_dropped_and_recorded():
    cards = _deck()
    cards[3] = {"heading": "THE WRECK", "body": "900 TONS"}            # unsigned: the body is emptied
    drop = slides_to_drop(cards, dict(SYN), "carousel")
    assert [d["slide"] for d in drop] == [4]
    p = dict(SYN)
    rows = record_dropped(p, "carousel", drop, cards, len(cards) - 1)
    assert rows[0]["rule"] == SLIDE_DROP_RULE and rows[0]["slide"] == 4 and "THE WRECK" in rows[0]["omitted"]
    assert p["layout_holds"] == rows and "900 TON" in rows[0]["reason"]
    record_dropped(p, "carousel", drop, cards, len(cards) - 1)        # a second copy or redraw: one row
    assert len(p["layout_holds"]) == 1


@pytest.mark.parametrize("where", [0, 6])
def test_an_emptied_cover_or_closing_slide_is_never_dropped(where):
    cards = _deck()
    cards[where] = {"heading": "THE WRECK", "body": "900 TONS"}
    assert slides_to_drop(cards, dict(SYN), "carousel") == []        # gate_deck then refuses the deck


def test_a_drop_that_leaves_the_deck_under_its_minimum_is_refused_instead():
    cards = _deck()
    for i in (2, 4):
        cards[i] = {"heading": "THE WRECK", "body": "900 TONS"}
    assert len(cards) - 2 < CAROUSEL_MIN_SLIDES
    assert slides_to_drop(cards, dict(SYN), "carousel") == []


def test_a_signed_deck_drops_nothing():
    assert slides_to_drop(_deck(), dict(SYN), "carousel") == []


def test_a_shorter_deck_leaves_no_older_slide_behind(tmp_path):
    for i in range(1, 8):
        p = tmp_path / f"carousel_slide_{i:02d}.jpg"
        Image.new("RGB", (8, 8)).save(p)
    (tmp_path / "still_layout.json").write_text(json.dumps({f"carousel_slide_{i:02d}.jpg": {} for i in range(1, 8)}),
                                                encoding="utf-8")
    assert prune_stale_slides(tmp_path, 6) == ["carousel_slide_07.jpg"]
    assert sorted(p.name for p in tmp_path.glob("carousel_slide_*.jpg"))[-1] == "carousel_slide_06.jpg"
    assert "carousel_slide_07.jpg" not in json.loads((tmp_path / "still_layout.json").read_text(encoding="utf-8"))


def _photos(tmp_path: Path, n: int = 4) -> list[Path]:
    out = []
    for i in range(n):
        p = tmp_path / f"p{i}.jpg"
        Image.new("RGB", (1600, 1200), (60 + 25 * i, 90, 120 - 10 * i)).save(p)
        out.append(p)
    return out


def test_the_deck_draws_without_the_dropped_slide_on_both_copies(tmp_path):
    cards = _deck()
    cards[3] = {"heading": "THE WRECK", "body": "900 TONS"}
    payload = signed({"carousel_slides": [c for i, c in enumerate(cards) if i != 3], "title": "The Bronze Machine"})
    payload["carousel_slides"] = cards                                 # the emptied slide's figure is not signed
    run = tmp_path / "run"
    g = MultiFormatGenerator()
    for fb in (False, True):
        slides = g.generate_carousel_sequence(_photos(tmp_path) * 2, payload, run, facebook=fb)
        assert len(slides) == 6
        recs = [still_layout.load(s)["recipe"] for s in slides]
        assert [r["card"]["heading"] for r in recs] == [c["heading"] for i, c in enumerate(cards) if i != 3]
        assert all(r["total"] == 6 and r["deck_dropped"][0]["slide"] == 4 for r in recs)
        assert not (slides[0].parent / "carousel_slide_07.jpg").exists()
    assert {h["surface"] for h in payload["layout_holds"] if h.get("rule") == SLIDE_DROP_RULE} == {
        "carousel", "Facebook carousel"}


# ------------------------------------------------------------------------------------------------ 2. frames
def _wedge(w: int = 1600, h: int = 1600) -> Image.Image:
    rng = np.random.default_rng(4)
    a = (rng.normal(120, 18, (h, w, 3))).clip(30, 230).astype(np.uint8)
    im = Image.fromarray(a)
    ImageDraw.Draw(im).polygon([(0, h * 0.45), (0, h), (w * 0.16, h)], fill=(0, 0, 0))   # a window's black corner
    return im


def test_a_black_window_wedge_is_cleared_holding_the_centre_and_never_enlarged_past_the_fit():
    im = _wedge()
    for size in ((1440, 1800), (1440, 1000)):
        got = frame_window(im, size)
        assert got is not None and got["box"] is not None and got["record"]["cleared"], size
        x0, y0, x1, y1 = got["box"]
        assert x0 <= im.width / 2 <= x1 and y0 <= im.height / 2 <= y1          # the centre stays in view
        mask, scale = frame_mask(im)
        win = mask[int(y0 * scale):int(y1 * scale), int(x0 * scale):int(x1 * scale)]
        assert win.mean() <= 0.0005
        assert abs((x1 - x0) / (y1 - y0) - size[0] / size[1]) < 0.01
        fit_w = min(im.width, im.height * size[0] / size[1])
        assert (x1 - x0) >= fit_w / FRAME_ZOOM_MAX - 2


def test_a_photographed_dark_is_not_a_frame():
    rng = np.random.default_rng(5)
    a = (rng.normal(120, 18, (1200, 1600, 3))).clip(30, 230).astype(np.uint8)
    a[:, :500] = rng.normal(8, 1.5, (1200, 500, 3)).clip(4, 12).astype(np.uint8)   # a black cloth: dark, even, not 0
    assert frame_window(Image.fromarray(a), (1440, 1800)) is None


def test_a_frame_no_window_can_clear_is_kept_and_recorded():
    a = np.full((1200, 1600, 3), 120, np.uint8)
    a[:420] = 0                                                       # black space across the whole top
    got = frame_window(Image.fromarray(a), (1440, 1000))
    assert got is not None and got["box"] is None and got["record"]["cleared"] is False


def test_the_slide_window_draws_no_wedge(tmp_path):
    p = tmp_path / "wedge.jpg"
    _wedge().save(p, quality=95)
    from src.multi_format import _render_state
    _render_state.window_rec = []
    im = MultiFormatGenerator._fill(p, (1440, 1800), top=False)
    rec, _render_state.window_rec = _render_state.window_rec, None
    a = np.asarray(im.convert("L"))
    assert (a <= 12).mean() < 0.001 and rec and rec[0]["frame"]["cleared"]


# ------------------------------------------------------------------------------------------------ 3. photo choice
def _pool(tmp_path: Path, reasons: list[str]) -> list[Path]:
    out, images = [], []
    for i, why in enumerate(reasons):
        name = f"{i + 1:02d}_commons.jpg"
        Image.new("RGB", (64, 48), (40 * i % 255, 90, 120)).save(tmp_path / name)
        images.append({"file": name, "title": f"File:Stone {i}", "vision_review": {"reason": why, "kept": True}})
        out.append(tmp_path / name)
    (tmp_path / "credits.json").write_text(json.dumps({"images": images}), encoding="utf-8")
    return out


def test_no_photo_is_shown_twice_while_the_pool_has_an_unused_one(tmp_path):
    pool = _pool(tmp_path, ["The stone", "The stone again", "The quarry", "A close view", "The trench"])
    cards = _deck(5)
    got, choice = deck_photo_guard(cards, [pool[0], pool[1], pool[0], pool[1], pool[2]], pool)
    assert len({p.name for p in got}) == 5
    assert choice[2]["reason"].startswith("no repeat") and choice[2]["replaced"] == pool[0].name
    short, choice = deck_photo_guard(_deck(4), [pool[0], pool[1], pool[0], pool[1]], pool[:2])
    assert [c["reason"] for c in choice][2:] == ["repeat kept: the pool has no unused photo"] * 2


def test_visitors_never_stand_in_a_line_that_is_not_about_today(tmp_path):
    pool = _pool(tmp_path, ["The stone in its trench with visitors giving scale", "The stone", "The quarry floor",
                            "The trench wall", "The cracked shaft"])
    cards = [{"heading": "THE STONE", "body": "It still lies in the quarry today."},
             {"heading": "CARVED IN PLACE", "body": "Workers cut it from the bedrock."},
             {"heading": "THE QUARRY", "body": "The quarry floor shows tool marks."},
             {"heading": "YOUR TAKE", "body": "Would you have finished it?"}]
    got, choice = deck_photo_guard(cards, [pool[1], pool[0], pool[2], pool[3]], pool)
    assert got[1] == pool[4] and choice[1]["reason"].startswith("presence") and choice[1]["replaced"] == pool[0].name
    got, choice = deck_photo_guard(cards, [pool[0], pool[1], pool[2], pool[3]], pool)
    assert got[0] == pool[0] and choice[0]["reason"] == "matched"     # the line speaks of the stone today
    assert all(c["reason"] for c in choice)


# ------------------------------------------------------------------------------------------------ 4. comparator
FACT = {"value": 138.0, "unit": "ft", "dimension": "length", "subject": "Giant stone", "approximate": True}


def test_a_scale_slide_names_the_landmark_its_drawing_draws_and_adds_no_figure(tmp_path):
    from src import graphics
    poster = tmp_path / "graphic_scale.jpg"
    card = {"heading": "COLOSSAL SCALE", "body": "Its completed length is about 138 feet."}
    got = diagram_comparator(card, poster, {"scale_fact": FACT})
    name = graphics._landmark_name(graphics.pick_reference(138.0)[0])
    assert got and name in got["line"] and got["body"].startswith(card["body"]) and got["body"].endswith(got["line"])
    assert not any(ch.isdigit() for ch in got["line"])
    named = dict(card, body=card["body"] + f" That is less than half of {name}.")
    assert diagram_comparator(named, poster, {"scale_fact": FACT}) is None
    assert diagram_comparator(card, tmp_path / "graphic_route.jpg", {"scale_fact": FACT}) is None
    assert diagram_comparator(card, poster, {}) is None


# ------------------------------------------------------------------------------------------------ 5. Story pointer
def test_the_story_pointer_promises_no_link():
    assert "REEL" not in STORY_POINTER and "LINK" not in STORY_POINTER and "TAP" not in STORY_POINTER
