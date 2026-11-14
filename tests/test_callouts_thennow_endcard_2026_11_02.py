"""Panel round 2 (2026-11-02): callouts inside the platforms' text box with haloed leaders, then-and-now ending
on both photographs with their years, the end card saying follow once at cover-scale masthead, its 16:9 YouTube
end-screen variant, and the 9:16 loop bridge that resolves into the Reel's first frame."""
import json
import shutil

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import brand, callouts, end_card, safe_zones, then_now

FFMPEG = shutil.which("ffmpeg") is not None
POINTS_916 = [("stone door recess", 600, 900), ("carved tuff wall", 300, 1300), ("ventilation shaft", 1000, 420),
              ("rolling stone door", 70, 700)]


def _photo(path, size=(1200, 2100), seed=1):
    rng = np.random.default_rng(seed)
    arr = rng.integers(150, 240, (size[1] // 30, size[0] // 30, 3), dtype=np.uint8)   # bright, busy stone
    Image.fromarray(arr).resize(size, Image.NEAREST).save(path)
    return path


# ---------------------------------------------------------------- callouts
def test_callout_plates_stay_in_the_reel_text_box():
    x0, y0, x1, y1 = callouts.text_box(1080, 1920)
    reel = safe_zones.safe_box("reel")
    # QF-24 (decision QF-24/QF-87 layout, 2026-11-09 and 2026-11-13): the label box runs to the organic Reel box's
    # edges (113-848), which already hold the platforms' clearance; it was the ads box 18 px in (83, 870)
    assert (x0, x1) == (reel[0], reel[2]) == (113, 848)
    for lab in callouts.layout_labels(POINTS_916, 1080, 1920):
        b = lab["box"]
        assert x0 <= b[0] and b[2] <= x1 and y0 <= b[1] and b[3] <= y1, (lab["lines"], b)
        assert len(lab["lines"]) <= 2 and lab["font"].size >= 34


def test_callout_labels_on_16x9_keep_title_safe_with_elbow_leaders():
    from src.graphics import get_text_box
    x0, y0, x1, y1 = get_text_box(1920, 1080)
    labs = callouts.layout_labels([("stone door recess", 1100, 500), ("carved tuff wall", 500, 750)], 1920, 1080)
    for lab in labs:
        b = lab["box"]
        assert x0 <= b[0] and b[2] <= x1 and y0 <= b[1] and b[3] <= y1
        # round 5 retired the 16:9 elbow (section 1: one leader style on both formats) and set the 16:9 label at
        # 44 px (section 6, was 36); the leader still meets the centre of a plate edge
        assert lab["elbow"] is None and lab["font"].size <= callouts.LABEL_PX["landscape"] == 44
        ex, ey = lab["end"]
        assert (ex, ey) == pytest.approx(callouts._attach(tuple(b), lab["attach"]), abs=1)
    a, b = labs[0]["box"], labs[1]["box"]
    assert not callouts._overlaps(tuple(a), tuple(b), 0)


def test_callout_leader_meets_the_centre_of_its_label_edge():
    # Operator, 2026-11-02: no corner attachments, one alignment rule (rewritten from the side-flush rule above
    # it replaced): top/bottom edge centre with centred text, or side mid-height with text flush toward the leader.
    for lab in callouts.layout_labels(POINTS_916, 1080, 1920):
        x0, y0, x1, y1 = lab["box"]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        ex, ey = lab["end"]
        if lab["attach"] in ("top", "bottom"):
            assert abs(ex - cx) <= 1 and ey == (y0 if lab["attach"] == "top" else y1) and lab["anchor"] == "mm"
        else:
            assert ex == (x0 if lab["attach"] == "left" else x1) and abs(ey - cy) <= 1
            assert lab["anchor"] == ("lm" if lab["attach"] == "left" else "rm")


def test_partial_leader_draws_along_its_length():
    pts = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)]
    assert callouts._partial(pts, 0.0) == [(0.0, 0.0)]
    assert callouts._partial(pts, 0.25) == [(0.0, 0.0), (50.0, 0.0)]
    assert callouts._partial(pts, 0.75)[-1] == (100.0, 50.0)
    assert callouts._partial(pts, 1.0) == pts


@pytest.mark.skipif(not FFMPEG, reason="FFmpeg renders the clip")
def test_callouts_clip_records_label_contrast_and_haloed_leader(tmp_path):
    photo = _photo(tmp_path / "p.jpg")
    # round 5: render refuses a layout in conflict, and POINTS_916's wall dot (y 1300) is below the text box and in
    # the caption band, where make never sends a label; the wall is drawn at y 1000 instead
    pts = [POINTS_916[0], ("carved tuff wall", 300, 1000)]
    clip = callouts.render(photo, pts, tmp_path / "graphic_labels.mp4")
    rec = json.loads((tmp_path / "graphic_labels.mp4.contrast.json").read_text(encoding="utf-8"))
    assert rec["elements"] and all(e["drawn"] >= brand.TEXT_CONTRAST for e in rec["elements"])
    last = np.asarray(Image.open(clip.with_suffix(".jpg")).convert("RGB"), dtype=np.int32)
    lab = callouts.layout_labels(pts, 1080, 1920)[0]
    # The plate's ground is the brand ink (230/255 over the dimmed photo), not a neutral black.
    b = lab["box"]
    cy = (b[1] + b[3]) // 2
    patch = last[cy - 3:cy + 3, b[0] + 5:b[0] + 11].reshape(-1, 3).mean(axis=0)   # between edge and text
    assert patch.max() < 60


# ---------------------------------------------------------------- then and now
def test_then_now_divider_ends_on_the_split_and_shows_both_photos():
    for w, h in ((1080, 1920), (1920, 1080)):
        split = then_now._split_at(w, h)
        assert then_now.divider_at(0.0, w, h) == max(w, h)                     # the old photo whole
        assert then_now.divider_at(then_now.CLIP_SECONDS, w, h) == pytest.approx(split)
        settled = sum(then_now.WIPE.values())
        assert then_now.CLIP_SECONDS - settled >= 1.2                          # the split holds >= 1.2 s
    assert then_now._split_at(1080, 1920) == 768 and then_now._split_at(1920, 1080) == 960
    a = Image.new("RGB", (1080, 1920), (200, 30, 30))
    b = Image.new("RGB", (1080, 1920), (30, 30, 200))
    im = np.asarray(then_now.compose(a, b, 768))
    assert tuple(im[400, 540]) == (200, 30, 30) and tuple(im[1100, 540]) == (30, 30, 200)
    assert tuple(im[768, 540]) == brand.BONE                                    # the bone divider
    side = np.asarray(then_now.compose(a.resize((1920, 1080)), b.resize((1920, 1080)), 960))
    assert tuple(side[540, 400]) == (200, 30, 30) and tuple(side[540, 1500]) == (30, 30, 200)


@pytest.mark.skipif(not FFMPEG, reason="FFmpeg renders the clip")
@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)], ids=["9x16", "16x9"])
def test_then_now_last_frame_keeps_both_years_at_measured_contrast(tmp_path, size):
    old, new = _photo(tmp_path / "a.jpg", seed=2), _photo(tmp_path / "b.jpg", seed=3)
    then_now.render(old, 1925, new, 2019, tmp_path / "graphic_thennow.mp4", width=size[0], height=size[1])
    rec = json.loads((tmp_path / "graphic_thennow.mp4.contrast.json").read_text(encoding="utf-8"))
    texts = {e["name"]: e["text"] for e in rec["elements"]}
    assert texts == {"then_kicker": "THEN", "then_year": "1925", "now_kicker": "NOW", "now_year": "2019"}
    assert all(e["drawn"] >= brand.TEXT_CONTRAST for e in rec["elements"]), rec["elements"]


# ---------------------------------------------------------------- end card
def test_the_card_never_says_follow_twice():
    lay = end_card.layout("Send this to a space enthusiast you know.")
    assert lay["follow"] is True
    raw = "Send this to a space enthusiast you know, and follow for the next natural marvel."
    lay = end_card.layout(raw)
    assert lay["follow"] is False                       # the share copy asks already: no gold line
    assert end_card.says_follow(raw) and not end_card.says_follow(end_card.card_text(raw))
    stats = {}
    frames = list(end_card.frames(Image.new("RGB", (1080, 1920), (120, 110, 100)), raw, stats))
    assert len(frames) == round(end_card.SECONDS * end_card.FPS)
    assert "follow" not in stats["contrast"] and stats["follow"] is False


def test_the_masthead_is_at_cover_scale_inside_the_safe_width():
    lay = end_card.layout("Send this to the codebreaker you know.")
    m = lay["masthead"]
    # QF-24 (decision 2026-11-13, (b), chosen on real renders): the group centred in the organic Reel box (x 480, its
    # 735 px), not on the frame's 616 px centred width (x 540) that set the masthead under its cover scale
    centre, column = end_card.reel_column()
    assert m["size"] >= 64 and 600 <= m["width"] <= min(end_card.MASTHEAD_W, column)
    x = lay["masthead_xy"][0] - 2 * m["mark_r"] - m["gap"]
    assert abs((x + m["width"] / 2) - centre) <= 2 and centre == 480        # centred
    assert lay["share_font"].size >= 54 and lay["line_h"] > 0


def test_the_card_spreads_through_the_reel_box():
    _, y0, _, y1 = safe_zones.REEL.box()
    lay = end_card.layout("Send this to the codebreaker you know.")
    centre = (end_card.MAST_TOP + lay["box"][3]) / 2
    assert y0 <= end_card.MAST_TOP and lay["box"][3] <= y1
    assert abs(centre - (y0 + y1) / 2) <= 80                                # balanced in the box, not top-heavy


def test_the_16x9_end_screen_keeps_youtube_slots_clear():
    share = "Send this to a space enthusiast you know."
    lay = end_card.layout(share, 1920, 1080)
    assert lay["landscape"] and lay["follow"] is False
    vid, sub = end_card.END_SCREEN_SLOTS["video"], end_card.END_SCREEN_SLOTS["subscribe"]
    right = lay["align_x"] + max(lay["share_widths"])
    bottom = lay["top"] + len(lay["lines"]) * lay["line_h"]
    assert right <= vid[0] and bottom <= sub[1]
    m = lay["masthead"]
    assert lay["masthead_xy"][0] - 2 * m["mark_r"] - m["gap"] == round(1920 * 0.08)   # top-left, title-safe
    stats = {}
    frames = list(end_card.frames(Image.new("RGB", (1920, 1080), (230, 220, 200)), share, stats))
    assert frames[-1].size == (1920, 1080) and len(frames) == round(end_card.END_SCREEN_SECONDS * end_card.FPS)
    assert stats["seconds"] >= 5.0                                           # YouTube's end screen minimum
    assert set(stats["contrast"]) == {"brand", "share"}
    assert all(v is not None and v >= brand.TEXT_CONTRAST for v in stats["contrast"].values())


def test_the_loop_bridge_resolves_into_the_first_frame():
    last = Image.new("RGB", (1080, 1920), (230, 225, 210))
    first = Image.new("RGB", (1080, 1920), (40, 90, 140))
    d = ImageDraw.Draw(first)
    d.rectangle([200, 600, 880, 1300], fill=(220, 180, 60))
    stats = {}
    frames = list(end_card.loop_bridge(last, first, "Send this to the codebreaker you know, and follow for more.",
                                       stats))
    assert len(frames) == round(end_card.LOOP_SECONDS * end_card.FPS) and end_card.LOOP_SECONDS <= 1.0
    assert np.abs(np.asarray(frames[0], np.float32) - np.asarray(last, np.float32)).mean() < 3     # no jump in
    # Loops clean: the last bridge frame is one step short of frame 0 (mix 29/30, round-3 panel), so the Reel's own
    # frame 0 completes the crossfade and plays once; identical (< 1) made frame 0 play twice and the seam stutter.
    seam = np.abs(np.asarray(frames[-1], np.float32) - np.asarray(first, np.float32)).mean()
    assert 0 < seam < 8
    assert stats["text"] == "Send this to the codebreaker you know." and "follow" not in stats["text"].lower()
    assert stats["contrast"] is not None and stats["contrast"] >= brand.TEXT_CONTRAST
