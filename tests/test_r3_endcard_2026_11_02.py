"""Panel round 3 (2026-11-02), end card and loop bridge: the 9:16 loop bridge lands one step short of the Reel's
real frame 0 (no duplicated frame), refuses a resized still, crops rather than stretches, carries the closing
shot's drift, dims lightly and lifts that dim with a 0.5-0.7 s crossfade so no frame steps more than 25 L*, and
can run purely visual; the 16:9 end screen dims a scrim behind its text column, pushes at most 2 %, labels the
video slot WATCH NEXT (and optionally frames the slots and notes image credits); the 9:16 card's group is
centred in the Reel box."""
from itertools import pairwise

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import brand, end_card, safe_zones

SHARE = "Send this to the codebreaker you know, and follow for more."


def _dark_last():
    im = Image.new("RGB", (1080, 1920), (18, 16, 14))
    ImageDraw.Draw(im).rectangle([300, 700, 780, 1200], fill=(60, 50, 40))
    return im


def _bright_first():
    im = Image.new("RGB", (1080, 1920), (235, 228, 210))
    ImageDraw.Draw(im).rectangle([200, 600, 880, 1300], fill=(250, 210, 90))
    return im


def _bridge(last, first, **kw):
    stats = {}
    frames = list(end_card.loop_bridge(last, first, SHARE, stats, **kw))
    return frames, stats


# ---------------------------------------------------------------- loop bridge seam


@pytest.mark.parametrize("line", [True, False], ids=["with_line", "visual"])
def test_the_seam_never_steps_more_than_the_luminance_cap(line):
    last, first = _dark_last(), _bright_first()
    frames, stats = _bridge(last, first, line=line)
    seq = [last, *frames, first]
    steps = [abs(end_card.mean_lstar(b) - end_card.mean_lstar(a)) for a, b in pairwise(seq)]
    assert max(steps) <= end_card.LOOP_MAX_STEP_L
    assert stats["max_step_L"] <= end_card.LOOP_MAX_STEP_L and abs(stats["max_step_L"] - max(steps)) < 0.5
    # and the other way round (bright last into a dark opening)
    _, stats = _bridge(first, last, line=line)
    assert stats["max_step_L"] <= end_card.LOOP_MAX_STEP_L


def test_the_bridge_stops_one_step_short_of_frame_zero():
    last, first = _dark_last(), _bright_first()
    frames, stats = _bridge(last, first, line=False)
    assert len(frames) == round(end_card.LOOP_SECONDS * end_card.FPS)
    assert abs(stats["mix_last"] - 29 / 30) < 1e-4                         # one ordinary step short of frame 0
    b = np.asarray(first, np.float32)
    gap = np.abs(np.asarray(last, np.float32) - b).mean()
    assert 0 < np.abs(np.asarray(frames[-1], np.float32) - b).mean() < gap / 30 + 2   # not frame 0, but all but there
    assert np.abs(np.asarray(frames[-1], np.float32) - b).mean() < np.abs(np.asarray(frames[-2], np.float32) - b).mean()
    # the first bridge frame is the next frame of the last shot, not a held copy of it
    a = np.asarray(last, np.float32)
    assert 0 < np.abs(np.asarray(frames[0], np.float32) - a).mean() < 3


def test_the_bridge_takes_only_the_rendered_frame_zero():
    with pytest.raises(ValueError):
        list(end_card.loop_bridge(_dark_last(), _bright_first().resize((1200, 2100)), SHARE))
    with pytest.raises(ValueError):
        list(end_card.loop_bridge(_dark_last(), Image.new("RGB", (1920, 1080)), SHARE))


def test_a_last_frame_of_another_size_is_cropped_not_stretched():
    last = Image.new("RGB", (1080, 2400), (20, 20, 20))
    ImageDraw.Draw(last).rectangle([0, 0, 1079, 239], fill=(200, 30, 30))   # cropped away by a centred fit
    frames, _ = _bridge(last, _bright_first(), line=False)
    top = np.asarray(frames[0], np.float32)[:150]
    assert top[..., 0].mean() < 60                                          # no red band at the top
    assert frames[0].size == (end_card.WIDTH, end_card.HEIGHT)
    assert end_card._cover(Image.new("RGB", (2000, 1000)), (1080, 1920)).size == (1080, 1920)


def _square_width(im, row=960):
    lum = np.asarray(im.convert("L"), np.float32)[row]
    on = np.where(lum > 128)[0]
    return int(on[-1] - on[0] + 1)


def test_the_closing_drift_carries_through_the_bridge():
    last = Image.new("RGB", (1080, 1920), (10, 10, 10))
    ImageDraw.Draw(last).rectangle([340, 760, 739, 1159], fill=(250, 250, 250))
    first = Image.new("RGB", (1080, 1920), (10, 10, 10))
    frames, stats = _bridge(last, first, line=False)
    w0, w8 = _square_width(frames[0]), _square_width(frames[8])
    assert w0 >= 400 and w8 > w0
    expect = 400 * (1 + end_card.LOOP_PUSH * 9)
    assert abs(w8 - expect) <= 3
    assert stats["push"] == end_card.LOOP_PUSH


def test_the_dim_is_light_and_lifts_with_the_crossfade():
    assert 0.3 <= end_card.LOOP_DIM <= 0.5
    grey = Image.new("RGB", (1080, 1920), (150, 150, 150))
    frames, _ = _bridge(grey, grey.copy(), line=False)
    lum = [float(np.asarray(f.convert("L"), np.float32).mean()) for f in frames]
    assert min(lum) > 150 * 0.5                                            # never a black dip
    assert abs(lum[-1] - 150) < 2                                          # back to the picture by the seam
    k = int(np.argmin(lum))
    assert all(b >= a - 0.01 for a, b in pairwise(lum[k:]))       # one dip, then a steady lift


def test_the_crossfade_length_is_an_option_between_half_and_seven_tenths():
    last, first = _dark_last(), _bright_first()
    _, st = _bridge(last, first, line=True)
    assert st["crossfade"] == end_card.LOOP_CROSSFADE[0] == 0.5
    _, st = _bridge(last, first, line=False)
    assert st["crossfade"] == end_card.LOOP_CROSSFADE[1] == 0.7
    frames, st = _bridge(last, first, line=False, crossfade=0.6)
    assert st["crossfade"] == 0.6
    a = np.asarray(last, np.float32)
    start = round((end_card.LOOP_SECONDS - 0.6) * end_card.FPS)
    for k in range(start):                                                 # nothing of frame 0 before the crossfade
        assert np.asarray(frames[k], np.float32)[1500:1600, 50:150].mean() < a[1500:1600, 50:150].mean() + 1
    assert _bridge(last, first, line=False, crossfade=0.1)[1]["crossfade"] == 0.5
    assert _bridge(last, first, line=False, crossfade=2.0)[1]["crossfade"] == 0.7


def test_the_purely_visual_bridge_records_no_text():
    last, first = _dark_last(), _bright_first()
    vis, st = _bridge(last, first, line=False)
    assert st["text"] is None and st["lines"] == [] and st["contrast"] is None and st["line"] is False
    assert st["follow"] is False
    txt, st2 = _bridge(last, first, line=True, crossfade=0.7)
    assert st2["text"] == "Send this to the codebreaker you know." and st2["contrast"] >= brand.TEXT_CONTRAST
    k = 7   # at the hold the two differ only where the line is
    diff = np.abs(np.asarray(vis[k], np.float32) - np.asarray(txt[k], np.float32)).max(axis=2)
    rows = np.where(diff.max(axis=1) > 0)[0]
    assert rows.size and rows.min() > 500 and rows.max() < 1050


def test_the_line_is_gone_before_the_picture_has_mostly_changed():
    last, first = _dark_last(), _bright_first()
    vis, _ = _bridge(last, first, line=False, crossfade=0.5)
    txt, _ = _bridge(last, first, line=True, crossfade=0.5)
    start = round(0.5 * end_card.FPS)
    for k in range(start + round(0.4 * 0.5 * end_card.FPS) + 1, len(vis)):  # no ghost into frame 0
        assert np.abs(np.asarray(vis[k], np.int16) - np.asarray(txt[k], np.int16)).max() <= 1


# ---------------------------------------------------------------- 16:9 end screen


@pytest.fixture
def short_end_screen(monkeypatch):
    monkeypatch.setattr(end_card, "END_SCREEN_SECONDS", 3.0)


def _plate():
    im = Image.new("RGB", (1920, 1080), (205, 190, 165))
    d = ImageDraw.Draw(im)
    for x in range(0, 1920, 90):   # some texture, so softening and the scrim are visible
        d.line([(x, 0), (x + 300, 1080)], fill=(120, 105, 90), width=6)
    return im


def test_the_landscape_push_is_capped_and_eases_to_rest():
    s = end_card.END_SCREEN_SECONDS
    assert end_card.landscape_zoom_at(0) == 1.0
    assert abs(end_card.landscape_zoom_at(s) - (1 + end_card.LANDSCAPE_PUSH)) < 1e-9 and end_card.LANDSCAPE_PUSH <= 0.02
    zs = [end_card.landscape_zoom_at(t / 30) for t in range(round(s * 30) + 1)]
    v = np.diff(zs)
    assert (v >= 0).all() and v[-1] < v[0] / 20                            # slows to rest


def test_the_16x9_kicker_labels_the_video_slot_and_measures(short_end_screen):
    stats = {}
    frames = list(end_card.frames(_plate(), "Send this to a space enthusiast you know.", stats))
    assert set(stats["contrast"]) == {"brand", "share"}                    # the extras keep their own keys
    assert stats["contrast_kicker"] is not None and stats["contrast_kicker"] >= brand.TEXT_CONTRAST
    assert stats["kicker"] == end_card.KICKER and not any(c.isdigit() for c in end_card.KICKER)
    ex = end_card.end_screen_extras()
    vx0, vy0 = end_card.END_SCREEN_SLOTS["video"][:2]
    assert ex["kicker"]["xy"][0] == vx0                                    # left-aligned with the slot
    assert ex["kicker"]["xy"][1] <= vy0 - end_card.SLOT_FRAME_OFFSET - end_card.KICKER_GAP + 1
    assert ex["kicker"]["top"] >= round(1080 * 0.05)                        # inside title-safe
    assert "contrast_credits" not in stats and stats["slot_frames"] is False
    assert frames[-1].size == (1920, 1080)


def test_the_16x9_dim_is_a_scrim_behind_the_text_column(short_end_screen):
    frames = list(end_card.frames(_plate(), "Send this to a space enthusiast you know."))
    lum = np.asarray(frames[-1].convert("L"), np.float32)
    left = lum[650:1000, 40:900].mean()
    right = lum[300:620, 1300:1800].mean()                                 # inside the video slot
    assert right > left + 25
    tex = lum[300:620, 1300:1800].std()
    assert tex > 8                                                         # the right side keeps its texture


def test_slot_frames_are_hairlines_that_leave_the_slots_clear(short_end_screen):
    plain = list(end_card.frames(_plate(), "Send this to a space enthusiast you know."))[-1]
    stats = {}
    framed = list(end_card.frames(_plate(), "Send this to a space enthusiast you know.", stats, slot_frames=True))[-1]
    a, b = np.asarray(plain, np.int16), np.asarray(framed, np.int16)
    x0, y0, x1, y1 = end_card.END_SCREEN_SLOTS["video"]
    assert np.abs(a[y0 + 4:y1 - 4, x0 + 4:x1 - 4] - b[y0 + 4:y1 - 4, x0 + 4:x1 - 4]).max() <= 1   # interior clear
    o = end_card.SLOT_FRAME_OFFSET
    top_edge = b[y0 - o, x0:x1]
    assert np.abs(top_edge - np.array(brand.GOLD_DIM)).sum(axis=1).mean() < 40            # a GOLD_DIM hairline
    assert np.abs(a[y0 - o - 3, x0:x1] - b[y0 - o - 3, x0:x1]).max() <= 1                 # one pixel wide
    assert stats["slot_frames"] is True


def test_the_credits_line_only_when_asked_and_clear_of_the_slots(short_end_screen):
    stats = {}
    list(end_card.frames(_plate(), "Send this to a space enthusiast you know.", stats, credits=True))
    assert stats["contrast_credits"] is not None and stats["contrast_credits"] >= brand.TEXT_CONTRAST
    assert set(stats["contrast"]) == {"brand", "share"}
    c = end_card.end_screen_extras()["credits"]
    sx1 = end_card.END_SCREEN_SLOTS["subscribe"][2]
    x, baseline = c["xy"]
    w = c["font"].getlength(c["text"]) + c["track"] * (len(c["text"]) - 1)
    assert x > sx1 + end_card.SLOT_FRAME_OFFSET and x + w < end_card.END_SCREEN_SLOTS["video"][0]
    assert baseline <= 1080 - 80                                           # above YouTube's progress bar
    assert "SOURCES" not in c["text"]


# ---------------------------------------------------------------- 9:16 card group


@pytest.mark.parametrize("share", ["Send this to the codebreaker you know.",
                                   "Send this to a space enthusiast you know, and follow for more.",
                                   ("Send this to the friend who still believes the old maps hid a whole continent "
                                    "beneath the ice of the southern ocean.")])
def test_the_916_card_group_is_centred_in_the_reel_box(share):
    lay = end_card.layout(share)
    _, y0, _, y1 = safe_zones.REEL.box()
    cap_top = lay["masthead"]["font"].getbbox("R", anchor="lt")[1]
    g_top = lay["masthead_top"] + cap_top
    g_bottom = lay["box"][3]
    assert y0 <= g_top and g_bottom <= y1
    assert abs((g_top + g_bottom) / 2 - (y0 + y1) / 2) <= 2
    assert lay["rule_y"] > g_top and lay["top"] > lay["rule_y"]


# ---------------------------------------------------------------- round 5: the seam keeps moving


def _textured(seed, bright):
    r = np.random.default_rng(seed)
    im = Image.fromarray(r.integers(0, 255, (60, 34, 3)).astype(np.uint8)).resize((1080, 1920), Image.BICUBIC)
    d = ImageDraw.Draw(im)
    for k in range(0, 1920, 120):
        d.line([(0, k), (1080, k + 300)], fill=(240, 230, 200) if bright else (30, 25, 20), width=8)
    return im


def _mad(a, b):
    return float(np.abs(np.asarray(a, np.float32) - np.asarray(b, np.float32)).mean())


@pytest.mark.parametrize("cf", [0.5, 0.6, 0.7])
def test_the_seam_curve_starts_at_rest_and_ends_at_29_of_30_still_moving(cf):
    n = round(cf * end_card.FPS)
    mix = end_card._seam_curve(n, 1 - 1 / 30)
    vals = [mix(k / n) for k in range(n + 1)]
    assert vals[0] == 0 and abs(vals[-1] - 1) < 1e-9 and abs(vals[-2] - 29 / 30) < 1e-9
    steps = np.diff(vals)
    assert (steps > 0).all()                                               # monotone, never a held frame
    assert steps[0] < steps[n // 2] / 4                                    # eases in
    assert steps[-1] > steps[-2] / 2                                       # no stall into frame 0 (smootherstep: ~0)
    _, st = _bridge(_dark_last(), _bright_first(), line=False, crossfade=cf)
    assert abs(st["mix_last"] - 29 / 30) < 1e-4


@pytest.mark.parametrize("line", [True, False], ids=["with_line", "visual"])
def test_motion_is_continuous_through_the_loop_point(line):
    last, first = _textured(1, False), _textured(2, True)
    frames, _ = _bridge(last, first, line=line)
    reel = [first] + [Image.fromarray(end_card._pushed(first, 1 + end_card.LOOP_PUSH * k, 1080, 1920)
                                      .clip(0, 255).astype(np.uint8)) for k in (1, 2)]
    seq = frames + reel
    d = [_mad(a, b) for a, b in pairwise(seq)]
    seam = len(frames) - 1                                                 # the step bridge -> frame 0
    own = d[seam + 1]                                                      # the Reel's own push step
    assert 0 < d[seam] < 3 * own                                           # frame 0 differs, by about one step
    for k in range(seam - 3, seam + 2):                                    # no stall-then-move hitch
        assert d[k] > 0.6 * own, (k, d[k], own)


def _ink_width(im, row=960):
    """The square's width from the row's ink integral: sub-pixel, linear in a crossfade's mix, and blind to an
    even dim (the ground and the square's middle set the scale)."""
    lum = np.asarray(im.convert("L"), np.float32)[row]
    lo, hi = float(lum[:100].mean()), float(lum[520:560].mean())
    return float(((lum - lo) / (hi - lo)).clip(0, 1).sum())


def _square(w=400):
    im = Image.new("RGB", (1080, 1920), (10, 10, 10))
    ImageDraw.Draw(im).rectangle([540 - w // 2, 760, 540 + w // 2 - 1, 1159], fill=(250, 250, 250))
    return im


def test_the_incoming_frame_zero_carries_the_opening_push():
    sq = _square()
    frames, _ = _bridge(sq, sq.copy(), line=False, close_push=0.0)        # the outgoing shot holds still
    n = round(end_card.LOOP_CROSSFADE[1] * end_card.FPS)
    mix = end_card._seam_curve(n, 1 - 1 / 30)
    total = len(frames)
    for i in (total - 12, total - 6, total - 1):
        m = mix((i - (total - n)) / n)
        expect = (1 - m) * 400 + m * 400 * (1 + end_card.LOOP_PUSH * (i - total))
        assert abs(_ink_width(frames[i]) - expect) < 0.6, (i, _ink_width(frames[i]), expect)
    assert _ink_width(frames[-6]) < 399.4                                  # not a frozen frame 0 (that is 400)


def test_frames_before_frame_zero_are_wider_with_the_edge_mirrored():
    sq = _square()
    assert np.array_equal(end_card._pulled(sq, 1.0, 1080, 1920), end_card._pushed(sq, 1.0, 1080, 1920))
    z = 1 - 0.0016 * 20
    w = _ink_width(Image.fromarray(end_card._pulled(sq, z, 1080, 1920).clip(0, 255).astype(np.uint8)))
    assert abs(w - 400 * z) < 0.6
    edge = Image.new("RGB", (1080, 1920), (128, 128, 128))
    ImageDraw.Draw(edge).rectangle([0, 0, 1079, 7], fill=(200, 40, 40))     # a red top edge
    top = end_card._pulled(edge, z, 1080, 1920)[:80, 300:700]
    assert top.mean(axis=2).min() > 60                                     # no black border anywhere
    red = np.where((top[..., 0] > 150) & (top[..., 1] < 90))[0]
    assert red.size and red.min() > 15 and red.max() < 60                  # the red edge, mirrored about itself


def test_shot_drift_follows_the_composers_capped_push():
    end, v = end_card.shot_drift(60)
    assert abs(end - (1 + 0.0016 * 60)) < 1e-12 and abs(v - 0.0016) < 1e-12
    end, v = end_card.shot_drift(200)                                      # > ~5 s: at the cap, static
    assert end == end_card.LOOP_PUSH_CAP == 1.24 and v == 0.0
    end, v = end_card.shot_drift(141, rate=0.0017)                         # a part step left to the cap
    assert abs(end - 1.2397) < 1e-9 and abs(v - 0.0003) < 1e-9


def test_the_bridge_starts_no_push_the_closing_shot_did_not_have():
    sq = _square()
    end, v = end_card.shot_drift(200)
    frames, st = _bridge(sq, sq.copy(), line=False, close_zoom=end, close_push=v)
    assert st["push"] == 0.0 and st["close_zoom"] == 1.24
    widths = [_ink_width(f) for f in frames[:8]]
    assert max(widths) - min(widths) < 0.5                                 # static, as the shot ended


def test_the_outgoing_drift_is_the_composers_absolute_rate():
    sq = _square()
    frames, _ = _bridge(sq, sq.copy(), line=False, close_zoom=1.2, close_push=0.0016)
    expect = 400 * (1 + 0.0016 / 1.2 * 9)                                  # relative 0.0016/Z, not 0.0016
    assert abs(_ink_width(frames[8]) - expect) < 0.6
    with pytest.raises(ValueError):
        list(end_card.loop_bridge(sq, sq.copy(), SHARE, close_zoom=0))
