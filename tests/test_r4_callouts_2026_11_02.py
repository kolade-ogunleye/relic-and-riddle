"""Panel round 4 (2026-11-02), callouts and then-and-now: callout dots (and so their leaders) stay out of the caption
band; a focus spotlight on the newest label, with each label measured on the frames where it is shown; the push stays
centred; then-and-now kickers at phone size in one colour role, the marker shade as a soft local glow kept to its
own photo, one slow push shared by both photos, and old photos whose date reads as a publication date are refused
(a photographic print is not one)."""
import math
from itertools import pairwise

import numpy as np
import pytest
from PIL import Image

from src import callouts, config, safe_zones, then_now
from src.brand import DATA_FONT
from src.typography import get_font
from tests import callout_fakes


def _photo(path, size=(1200, 2100), seed=1, lo=60, hi=240):
    rng = np.random.default_rng(seed)
    arr = rng.integers(lo, hi, (size[1] // 12, size[0] // 12, 3), dtype=np.uint8)
    Image.fromarray(arr).resize(size, Image.BICUBIC).save(path)
    return path


# ---------------------------------------------------------------- caption band
def test_caption_top_is_the_band_on_916_and_the_caption_zone_on_169():
    assert callouts.caption_top(1080, 1920) == safe_zones.caption_band_top(1080, 1920) == 1098
    assert callouts.caption_top(1920, 1080) == 905


@pytest.mark.parametrize("size,low", [((1080, 1920), 1320), ((1080, 1920), 1090), ((1920, 1080), 900)])
def test_a_dot_in_or_pushed_into_the_caption_band_is_dropped(size, low):
    w, h = size
    pts = [("stone door recess", w * 0.55, h * 0.4), ("carved tuff wall", w * 0.3, low)]
    kept = callouts.clear_of_captions(pts, w, h)
    assert [p[0] for p in kept] == ["stone door recess"]


def test_dots_clear_of_the_band_along_the_whole_push_are_kept():
    pts = [("a", 600, 900), ("b", 300, 1000)]
    assert callouts.clear_of_captions(pts, 1080, 1920) == pts
    top = callouts.caption_top(1080, 1920)
    for _, x, y in pts:                                          # the leader never runs lower than its dot
        assert max(q[1] for q in callouts.Push(1080, 1920).track(x, y)) + callouts.DOT_R < top


def test_the_loupe_source_ring_counts_as_the_dot():
    pts = [("a", 600, 700, 0.2), ("b", 300, 1015, 0.01)]         # b gets the loupe: its ring is loupe_r / mag
    assert len(callouts.clear_of_captions(pts, 1080, 1920, mag=0.0)) == 2
    assert [p[0] for p in callouts.clear_of_captions(pts, 1080, 1920, mag=1.3)] == ["a"]


# The review's 9:16 pair with its extents (stone door recess at 600, 900, 260 x 300; carved tuff wall at 300, 1010,
# 300 x 380) has no layout as two objects under the operator's kind rule (2026-11-03: an object keeps its whole box
# clear of every plate); with the wall as a surface it is laid out (test_callouts_v3's
# test_the_reference_916_pair_gets_a_clean_layout_with_the_wall_as_a_surface, and rendered in test_callouts_v4). The
# render tests here draw this pair of object-sized features.
PAIR_916 = [("stone door recess", 640, 720, 0.02, 160, 220), ("carved niche", 300, 980, 0.01, 110, 140)]


def _plan_llm(monkeypatch, feats, size=(0.2, 0.15)):
    # round 5 section 9: three location passes, then the blind location, blind look, match and decoy
    callout_fakes.install(monkeypatch, [(lb, x, y, *size) for lb, x, y in feats])


def test_make_drops_the_low_label_and_gives_up_below_two(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CALLOUTS_ENABLED", True)
    photo = _photo(tmp_path / "topic_1.jpg")
    seen = {}

    def fake_render(photo, points, out, width=1080, height=1920, reveal=None):
        seen.update(points=points)
        out.write_bytes(b"clip")
        return out
    monkeypatch.setattr(callouts, "render", fake_render)
    # plan() admits a dot down to y 1098; at 1095 the push and the dot's ring take it into the band
    # object-sized features (operator 2026-11-03: an object keeps its whole extent clear of every plate)
    _plan_llm(monkeypatch, [("door", 0.55, 0.45), ("wall", 0.3, 0.35), ("shaft", 0.7, 1095 / 1920)], size=(0.08, 0.06))
    payload = {"photo_labels": ["door", "wall", "shaft"]}
    assert callouts.make(payload, {"id": "t", "title": "x"}, [photo], tmp_path / "g", approved=None)
    assert [p[0] for p in seen["points"]] == ["door", "wall"]
    seen.clear()
    _plan_llm(monkeypatch, [("door", 0.55, 0.45), ("shaft", 0.7, 1095 / 1920)], size=(0.08, 0.06))
    assert callouts.make({"photo_labels": ["door", "shaft"]}, {"id": "t", "title": "x"}, [photo], tmp_path / "h",
                         approved=None) is None
    assert "points" not in seen


def test_plan_carries_the_feature_extent_for_the_spotlight(tmp_path, monkeypatch):
    photo = _photo(tmp_path / "p.jpg")
    _plan_llm(monkeypatch, [("door", 0.55, 0.45), ("wall", 0.3, 0.35)])
    _, points = callouts.plan([photo], ["door", "wall"], "x")
    _label, _x, _y, area, fw, fh = points[0][:6]
    # round 5: located on the uncropped 1200 x 2100 photo and mapped into the 9:16 crop (scale 1920 / 2100)
    k = 1920 / 2100
    assert fw == pytest.approx(0.2 * 1200 * k) and fh == pytest.approx(0.15 * 1920)
    assert area == pytest.approx(fw * fh / (1080 * 1920))


# ---------------------------------------------------------------- centred push (round-4 review)
def test_push_is_the_centred_push():
    p = callouts.Push(1080, 1920)
    z = 1 + callouts.PUSH_IN
    assert p.at(300, 1300, callouts.CLIP_SECONDS) == pytest.approx((540 + (300 - 540) * z, 960 + (1300 - 960) * z))
    assert p.track(300, 1300) == [p.at(300, 1300, 0), p.at(300, 1300, callouts.CLIP_SECONDS)]
    assert p.zoom(callouts.CLIP_SECONDS) == pytest.approx(z)
    for i in range(61):                                          # the crop stays centred: never pinned to an edge
        t = callouts.CLIP_SECONDS * i / 60
        x0, y0, x1, y1 = p.crop(t)
        assert (x0 + x1) / 2 == pytest.approx(540) and (y0 + y1) / 2 == pytest.approx(960)
        assert (x1 - x0) * p.zoom(t) == pytest.approx(1080)
    assert callouts.PUSH_IN == 0.06
    assert not hasattr(callouts, "directed_layout")


def test_render_lays_out_on_the_centred_push(tmp_path, monkeypatch):
    seen = []
    real = callouts.layout_labels
    monkeypatch.setattr(callouts, "layout_labels", lambda *a, **k: seen.append(k.get("push")) or real(*a, **k))
    monkeypatch.setattr(callouts, "_encode", lambda frames, out, n, **k: out)
    photo = _photo(tmp_path / "p.jpg")
    callouts.render(photo, PAIR_916, tmp_path / "x.mp4", reveal=[0.6, 2.2])
    assert len(seen) == 1 and isinstance(seen[0], callouts.Push)
    assert seen[0].crop(callouts.CLIP_SECONDS)[1] > 0          # not pinned to the photo's top edge


@pytest.mark.parametrize("size,pts", [
    ((1080, 1920), [("stone door recess", 600, 900), ("carved tuff wall", 300, 1010)]),   # the review's 9:16 pair
    ((1080, 1920), [("stone door recess", 600, 900), ("carved tuff wall", 300, 1000)]),
])
def test_the_stone_door_leader_keeps_its_stub_all_along_the_push(size, pts):
    w, h = size
    push = callouts.Push(w, h)
    labs = callouts.layout_labels(pts, w, h, push=push)
    for lab, (_, x, y) in zip(labs, pts):
        assert not lab["conflict"]
        for i in range(31):                                      # every point of the push, not only its ends
            q = push.at(x, y, callouts.CLIP_SECONDS * i / 30)
            assert not callouts._overlaps(tuple(lab["box"]), (q[0] - callouts.DOT_R, q[1] - callouts.DOT_R,
                                                              q[0] + callouts.DOT_R, q[1] + callouts.DOT_R),
                                          callouts.LABEL_GAP // 2)
            if lab["attach"] in ("top", "bottom"):
                assert callouts._stub(callouts._route(q, lab["end"], lab["attach"])) >= callouts.STUB - 1


# ---------------------------------------------------------------- spotlight
def test_spot_axes_from_the_feature_extent_with_a_floor_and_a_cap():
    assert callouts.spot_axes(("a", 1, 1, 0.1, 200, 100), 1080, 1920) == pytest.approx((125, 62.5))
    assert callouts.spot_axes(("a", 1, 1, 0.0, 10, 10), 1080, 1920) == (callouts.SPOT_MIN, callouts.SPOT_MIN)
    assert callouts.spot_axes(("a", 1, 1), 1080, 1920) == (callouts.SPOT_MIN, callouts.SPOT_MIN)
    assert callouts.spot_axes(("a", 1, 1, 0.9, 1080, 1920), 1080, 1920) == pytest.approx((378, 378))


def test_spotlight_darkens_outside_the_newest_label_only():
    im = Image.new("RGB", (1080, 1920), (200, 200, 200))
    pts = [("a", 300, 600, 0.02, 150, 150), ("b", 800, 1300, 0.02, 150, 150)]
    push = callouts.Push(1080, 1920)
    starts = [0.5, 2.5]
    assert callouts.spotlight(im, 0.4, pts, starts, push) is im              # nothing said yet
    one = np.asarray(callouts.spotlight(im, 2.0, pts, starts, push), dtype=int)
    ax, ay = (round(v) for v in push.at(300, 600, 2.0))
    assert one[ay, ax].max() == 200                                          # on the first feature: untouched
    assert one[100, 1000].max() == round(200 * (1 - callouts.SPOT_DARK))     # far off: 20 % darker
    two = np.asarray(callouts.spotlight(im, 4.0, pts, starts, push), dtype=int)
    bx, by = (round(v) for v in push.at(800, 1300, 4.0))
    assert two[by, bx].max() == 200 and two[ay, ax].max() < 170              # moved to the newest label


def test_spotlight_eases_in_with_the_dot_and_leader():
    im = Image.new("RGB", (540, 960), (200, 200, 200))
    pts = [("a", 100, 100), ("b", 400, 800)]
    push = callouts.Push(540, 960)
    early = np.asarray(callouts.spotlight(im, 0.55, pts, [0.5, 2.5], push), dtype=int)[900, 50].max()
    later = np.asarray(callouts.spotlight(im, 1.5, pts, [0.5, 2.5], push), dtype=int)[900, 50].max()
    assert 200 > early > later == 160


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="FFmpeg renders the clip")
def test_render_keeps_plate_ground_and_contrast_with_the_spotlight(tmp_path):
    import json
    photo = _photo(tmp_path / "p.jpg")
    pts = PAIR_916
    clip = callouts.render(photo, pts, tmp_path / "graphic_labels.mp4", reveal=[0.6, 2.2])
    rec = json.loads((tmp_path / "graphic_labels.mp4.contrast.json").read_text(encoding="utf-8"))
    assert rec["elements"] and all(e["drawn"] >= 4.5 for e in rec["elements"])
    assert all(r["ratio"] is None or r["ratio"] >= callouts.NONTEXT_MIN for r in rec["nontext"])
    assert clip.exists()


# ---------------------------------------------------------------- measuring under the spotlight (round-4 review)
def test_probe_frames_cover_the_frames_where_the_label_is_newest():
    fps, n = config.VIDEO_FPS, int(callouts.CLIP_SECONDS * config.VIDEO_FPS)
    starts = [0.5, 2.5]
    first = [p[0] for p in (callouts.probe_frames(k, starts, n) for k in range(2))]
    one = callouts.probe_frames(0, starts, n)
    open_at = (0.5 + callouts.OPEN_FROM_START) * fps            # round 5: the longest open, from the label's start
    exit_at = round(callouts.exit_start() * fps)                # round 5: every label exits before the clip ends
    assert one[0] == pytest.approx(open_at, abs=1) and one[-1] == exit_at - 1
    assert any(i < 2.5 * fps for i in one)                      # while label 1 is the newest (its own spotlight)
    assert round(2.5 * fps) in one                              # the spotlight starts to move to label 2
    assert round((2.5 + callouts.SPOT_FADE) * fps) in one       # and has settled there
    assert max(b - a for a, b in pairwise(one)) <= round(callouts.MEASURE_STEP * fps)
    assert first[1] > first[0] and all(i >= first[1] for i in callouts.probe_frames(1, starts, n))


def test_each_label_is_recorded_on_its_worst_delivered_frame(tmp_path, monkeypatch):
    frames, recs = [], []
    monkeypatch.setattr(callouts, "_encode", lambda fr, out, n, **k: [frames.append(f) for f in fr] and out)
    monkeypatch.setattr(callouts, "record_contrast",
                        lambda clip, frame, rows: recs.append((frame, rows)) or {"elements": [
                            {"name": nm, "drawn": q.measure(frame), "graded": None} for nm, q in rows]})
    # a bright wall: the spotlight's 20 % is what separates label 1's own frames from the last one
    photo = _photo(tmp_path / "p.jpg", lo=180, hi=255)
    pts = PAIR_916
    callouts.render(photo, pts, tmp_path / "x.mp4", reveal=[0.6, 2.2])
    n = len(frames)
    rec = __import__("json").loads((tmp_path / "x.mp4.contrast.json").read_text(encoding="utf-8"))
    by_name = {e["name"]: e for e in rec["elements"]}
    for k in range(2):
        plates = [(fr, q) for fr, rows in recs for nm, q in rows if nm.startswith(f"label_{k + 1}_")]
        assert plates
        # round 5 (rigid lock): each label moves with its dot, so its lines are measured where they are on each
        # frame; the record holds the value on the frame where they measure lowest
        for j, (fr, q) in enumerate(plates):
            assert by_name[f"label_{k + 1}_{j + 1}"]["drawn"] == pytest.approx(q.measure(fr))
            assert q.measure(fr) >= 4.5
        # ... and that frame is its worst: on every frame from fully open (at the longest) to the exit, the same
        # lines moved by the dot's push measure no lower than the record
        push, fps = callouts.Push(1080, 1920), config.VIDEO_FPS
        dot = (pts[k][1], pts[k][2])
        rec_t = {e["t"] for e in rec["elements"] if e["name"].startswith(f"label_{k + 1}_")}
        assert len(rec_t) == 1
        at = round(rec_t.pop() * fps)
        bx, by = (round(v) for v in push.shift(*dot, at / fps))
        start = (0.6, 2.2)[k] - callouts.LEAD_FRAMES / fps
        for i in range(math.ceil((start + callouts.OPEN_FROM_START) * fps), round(callouts.exit_start() * fps)):
            sx, sy = (round(v) for v in push.shift(*dot, i / fps))
            for j, (_fr, q) in enumerate(plates):
                v = callouts._placed(q, sx - bx, sy - by).measure(frames[i])
                if v is not None:
                    assert by_name[f"label_{k + 1}_{j + 1}"]["drawn"] <= v + 1e-6
    exit_frame = round(callouts.exit_start() * config.VIDEO_FPS)
    assert all(f is not None for f in frames[:exit_frame])
    # label 1's lowest frame is not simply the last one: the last frame's spotlight is on label 2
    t1 = {e["t"] for e in rec["elements"] if e["name"].startswith("label_1_")}
    assert t1 and all(0 < t <= (n - 1) / config.VIDEO_FPS for t in t1)
    assert rec["min"] == min(e["drawn"] for e in rec["elements"])
    assert rec["nontext"] and all("t" in r for r in rec["nontext"])
    assert {r["name"].split("_")[1] for r in rec["nontext"]} == {"1", "2"}


# ---------------------------------------------------------------- then and now: kickers
def test_kickers_have_a_34px_cap_on_916_and_easier_tracking():
    f = get_font(DATA_FONT, then_now.KICKER_PX["portrait"], weight=600)
    assert -f.getbbox("THEN", anchor="ls")[1] >= 34
    assert then_now.KICKER_PX["landscape"] == 40 and then_now.KICKER_TRACK == pytest.approx(0.15)


def _encode_capture(monkeypatch):
    got = {}

    def enc(frames, out, n, **k):
        for im in frames:
            got["last"] = im
        return out
    monkeypatch.setattr(then_now, "_encode", enc)
    monkeypatch.setattr(then_now, "record_contrast", lambda clip, frame, rows: got.update(rows=rows))
    return got


def test_both_kickers_share_one_colour(tmp_path, monkeypatch):
    got = _encode_capture(monkeypatch)
    # a bright sky on the old photo's top half only: gold fails there, so both kickers go bone
    arr = np.full((2100, 1200, 3), 70, np.uint8)
    arr[:1000] = 250
    monkeypatch.setattr(then_now, "INK_ON_PAPER", False)   # freeze panel 2026-11-04 step 1: a white ground takes ink type
    Image.fromarray(arr).save(tmp_path / "old.jpg")
    _photo(tmp_path / "new.jpg", lo=40, hi=90)
    then_now.render(tmp_path / "old.jpg", 1925, tmp_path / "new.jpg", 2019, tmp_path / "x.mp4")
    kickers = {name: p for name, p in got["rows"] if name.endswith("kicker")}
    colours = {p.fill for p in kickers.values()}
    assert colours == {tuple(then_now.WHITE)}, colours          # not one gold and one bone


def test_markers_stay_inside_the_safe_box_at_the_larger_size(tmp_path, monkeypatch):
    got = _encode_capture(monkeypatch)
    _photo(tmp_path / "a.jpg", seed=2)
    _photo(tmp_path / "b.jpg", seed=3)
    then_now.render(tmp_path / "a.jpg", 1925, tmp_path / "b.jpg", 2019, tmp_path / "x.mp4")
    x0, y0, x1, y1 = safe_zones.graphics_box(1080, 1920)
    for name, p in got["rows"]:
        b = p.box
        assert b[0] >= x0 and b[1] >= y0 and b[2] <= x1 and b[3] <= y1, (name, b)
    boxes = dict(got["rows"])
    assert boxes["then_kicker"].box[3] < boxes["then_year"].box[1]       # the kicker clears the year below it


# ---------------------------------------------------------------- then and now: the marker's glow (review r9)
def _reach():
    return then_now.BAND_PAD + then_now.BAND_FALL


@pytest.mark.parametrize("size,which,boxes", [
    ((1920, 1080), "then", [(830, 170, 930, 200), (790, 230, 930, 330)]),
    ((1080, 1920), "then", [(83, 330, 200, 364), (80, 402, 310, 512)]),
    ((1080, 1920), "now", [(83, 816, 200, 850), (80, 888, 310, 998)]),
])
def test_shade_is_a_local_glow_behind_the_marker_not_a_bar(size, which, boxes):
    """Review r9: the full-width band read as a grey header bar. The shade is full only behind the marker's lines
    and gone a short way past them in every direction: bounded in extent, lighter with distance, no edge."""
    w, h = size
    m = np.asarray(then_now.band_mask(which, boxes, w, h, 0.6, pos=max(w, h))
                   if which == "then" else then_now.glow_mask(boxes, w, h, 0.6)).astype(int)
    full = round(255 * 0.6)
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    reach = _reach()
    assert m[(y0 + y1) // 2, (x0 + x1) // 2] == full and m[boxes[0][1], boxes[0][0] + 5] == full
    ys, xs = np.nonzero(m)
    assert ys.min() >= y0 - reach - 1 and ys.max() <= y1 + reach + 1        # bounded above and below the marker
    assert xs.min() >= x0 - reach - 1 and xs.max() <= x1 + reach + 1        # and to either side: never full width
    assert xs.max() - xs.min() < 0.45 * w and ys.max() - ys.min() < 0.33 * h          # vs the old full-width bar
    assert int((m > 0).sum()) < 0.08 * w * h                                 # a small share of the frame
    assert m[min(h - 1, y1 + reach + 2), (x0 + x1) // 2] == 0                # gone a short way below the year
    col = m[y1:, (x0 + x1) // 2]
    assert all(x >= y for x, y in pairwise(col))                             # only lighter away from the marker
    up = m[:y0 + 1, (x0 + x1) // 2][::-1]
    assert all(x >= y for x, y in pairwise(up))                              # and above it: fades from its top
    assert col[then_now.BAND_PAD + then_now.BAND_FALL // 2] < 0.6 * full     # half-way out: well under full
    assert max(np.abs(np.diff(m, axis=0)).max(), np.abs(np.diff(m, axis=1)).max()) <= 6   # no step reads as an edge


def test_16x9_then_glow_is_kept_to_the_old_photo():
    boxes = [(830, 170, 930, 200), (790, 230, 930, 330)]
    split = then_now._split_at(1920, 1080)
    alone = np.asarray(then_now.band_mask("then", boxes, 1920, 1080, 0.6, pos=1920)).astype(int)
    rest = np.asarray(then_now.band_mask("then", boxes, 1920, 1080, 0.6)).astype(int)
    assert alone[:, split:].max() > 0 and alone[:, 1200:].max() == 0      # THEN alone: still only round the marker
    assert rest[:, split:].max() == 0 and rest[250, 880] == round(255 * 0.6)   # at the split: the THEN panel only
    assert then_now.band_mask("then", boxes, 1920, 1080, 0.6, pos=0).getbbox() is None


def test_916_now_glow_hangs_from_the_divider_and_never_reaches_the_old_photo():
    boxes = [(83, 816, 200, 850), (80, 888, 310, 998)]
    split = then_now._split_at(1080, 1920)
    far = round(1920 * (1 - then_now.SWEEP_TO))
    reach = _reach()
    assert 816 - reach < split                                               # at rest its upper fall meets the divider
    for pos in (far, 500, 700, split):
        m = np.asarray(then_now.band_mask("now", boxes, 1080, 1920, 0.6, pos=pos)).astype(int)
        assert m[:pos].max() == 0                                            # nothing on the old photo above it
        assert m[:816 - reach - 1].max() == 0                                # no slab hung off a swept-up divider
        assert m[:, 310 + reach + 2:].max() == 0                             # nor across the frame
    rest = np.asarray(then_now.band_mask("now", boxes, 1080, 1920, 0.6, pos=split)).astype(int)
    assert 0 < rest[split + 1, 150] < round(255 * 0.6)                       # under the divider: the glow's light fall


def test_16x9_then_shade_leaves_the_recent_photo_alone_on_the_split(tmp_path, monkeypatch):
    masks = []
    real = then_now._shade
    monkeypatch.setattr(then_now, "_shade", lambda im, m, a=1.0: masks.append(m) or real(im, m, a))
    _encode_capture(monkeypatch)
    monkeypatch.setattr(then_now, "INK_ON_PAPER", False)   # freeze panel 2026-11-04 step 1: a white ground takes ink type
    w = _photo(tmp_path / "w.jpg", size=(2100, 1200), lo=240, hi=256)      # the old photo: bright, needs a shade
    d = _photo(tmp_path / "d.jpg", size=(2100, 1200), lo=30, hi=70)        # the recent one: dark, needs none
    then_now.render(w, 1925, d, 2019, tmp_path / "z.mp4", width=1920, height=1080)
    assert masks
    last = np.asarray(masks[-1]).astype(int)                             # the last frame, the clip at rest
    split = then_now._split_at(1920, 1080)
    assert last[:, :split - 3].max() > 0 and last[:, split + 3:].max() == 0


def test_render_draws_the_band_when_the_halo_falls_short(tmp_path, monkeypatch):
    calls = []
    real = then_now.band_mask
    monkeypatch.setattr(then_now, "band_mask", lambda *a, **k: calls.append(a[0]) or real(*a, **k))
    _encode_capture(monkeypatch)
    monkeypatch.setattr(then_now, "INK_ON_PAPER", False)   # freeze panel 2026-11-04 step 1: a white ground takes ink type
    w = _photo(tmp_path / "w.jpg", lo=240, hi=256)
    then_now.render(w, 1925, w, 2019, tmp_path / "y.mp4")
    assert calls


# ---------------------------------------------------------------- then and now: one shared push
def test_push_is_shared_eased_and_four_percent():
    assert then_now.push_at(0) == 1.0 and then_now.push_at(then_now.CLIP_SECONDS) == pytest.approx(1.04)
    assert then_now.push_at(0.3) - 1 < 0.04 * 0.3 / then_now.CLIP_SECONDS   # eased in
    src = Image.fromarray(np.tile(np.arange(1123, dtype=np.uint8)[None, :, None] % 251, (1997, 1, 3)))
    start = then_now.pushed(src, 0.0, (1080, 1920))
    end = then_now.pushed(src, then_now.CLIP_SECONDS, (1080, 1920))
    assert start.size == end.size == (1080, 1920)
    # the same transform for both photos: a point registered in both stays registered
    a = then_now.pushed(src, 2.0, (1080, 1920))
    b = then_now.pushed(src.copy(), 2.0, (1080, 1920))
    assert np.array_equal(np.asarray(a), np.asarray(b))


def test_divider_and_markers_hold_still_while_the_photos_push(tmp_path, monkeypatch):
    frames = []
    monkeypatch.setattr(then_now, "_encode", lambda fr, out, n, **k: [frames.append(np.asarray(f)) for f in fr] and out)
    monkeypatch.setattr(then_now, "record_contrast", lambda *a, **k: None)
    _photo(tmp_path / "a.jpg", seed=2)
    _photo(tmp_path / "b.jpg", seed=3)
    then_now.render(tmp_path / "a.jpg", 1925, tmp_path / "b.jpg", 2019, tmp_path / "x.mp4")
    i, j = round(4.0 * config.VIDEO_FPS), len(frames) - 1
    split = then_now._split_at(1080, 1920)
    assert tuple(frames[i][split, 540]) == tuple(frames[j][split, 540])  # the divider stays put
    assert np.abs(frames[i][1300:1400, 400:700].astype(int) - frames[j][1300:1400, 400:700]).mean() > 1   # photos move


# ---------------------------------------------------------------- then and now: capture year
@pytest.mark.parametrize("cand,ok", [
    ({"title": "File:Gol Gumbaz 1900.jpg", "categories": ["Gol Gumbaz"]}, True),
    ({"title": "File:Gol Gumbaz, postcard, 1905.jpg"}, False),
    ({"title": "File:Gol_Gumbaz_photochrom.jpg"}, False),
    ({"title": "File:Gumbaz.jpg", "categories": ["Lantern slides of India"]}, False),
    ({"title": "File:Gumbaz.jpg", "categories": ["Stereographs of Bijapur"]}, False),
    ({"title": "File:Gumbaz.jpg", "description": "Published by Detroit Publishing Co., 1902"}, False),
    ({"title": "File:Gumbaz.jpg", "date_text": "issued 1910"}, False),
    ({"title": "File:Gumbaz.jpg", "categories": ["Engravings of Bijapur"]}, False),
    ({"title": "File:Gumbaz.jpg", "categories": ["Lithographs of India"]}, False),
    ({"title": "File:Gumbaz.jpg", "categories": ["Photochrom prints of India"]}, False),
    # round-4 review: how Commons files real 19th-century photographs; their dates are capture dates
    ({"title": "Gol Gumbaz 1900.jpg", "categories": ["Albumen prints"]}, True),
    ({"title": "File:Gumbaz.jpg", "categories": ["Gelatin silver prints", "Gol Gumbaz"]}, True),
    ({"title": "File:Gumbaz.jpg", "categories": ["Photographic prints in the British Library"]}, True),
    ({"title": "File:Gol Gumbaz, albumen print, 1885.jpg", "description": "Photograph taken in 1885"}, True),
])
def test_publication_dated_old_photos_are_refused(cand, ok):
    assert then_now.capture_dated(cand) is ok


def test_an_albumen_print_with_a_capture_date_survives_sourcing(monkeypatch):
    found = [{"title": "File:Gol Gumbaz 1900.jpg", "year": 1900, "categories": ["Albumen prints", "Gol Gumbaz"]},
             {"title": "File:Gol Gumbaz 1905.jpg", "year": 1905, "categories": ["Postcards of Bijapur"]}]
    monkeypatch.setattr(then_now.commons, "search", lambda *a, **k: [])
    monkeypatch.setattr(then_now.commons, "candidates", lambda *a, **k: iter(found))
    assert [c["title"] for c in then_now._candidates("q", [], old=True)] == ["File:Gol Gumbaz 1900.jpg"]


def test_old_candidates_are_filtered_but_recent_ones_are_not(monkeypatch):
    found = [{"title": "File:Gate postcard 1900.jpg", "year": 1900}, {"title": "File:Gate 1901.jpg", "year": 1901},
             {"title": "File:Gate print 2020.jpg", "year": 2020}]
    monkeypatch.setattr(then_now.commons, "search", lambda *a, **k: [])
    monkeypatch.setattr(then_now.commons, "candidates", lambda *a, **k: iter(found))
    assert [c["title"] for c in then_now._candidates("q", [], old=True)] == ["File:Gate 1901.jpg"]
    assert [c["title"] for c in then_now._candidates("q", [], old=False)] == ["File:Gate print 2020.jpg"]


# ---------------------------------------------------------------- then and now: a light glow (review r8, r9)
def test_band_starts_light_and_stops_at_the_first_depth_that_holds(tmp_path, monkeypatch):
    depths = []
    real = then_now.band_mask
    monkeypatch.setattr(then_now, "band_mask",
                        lambda *a, **k: depths.append(a[4] if len(a) > 4 else k.get("alpha")) or real(*a, **k))
    got = _encode_capture(monkeypatch)
    monkeypatch.setattr(then_now, "INK_ON_PAPER", False)   # freeze panel 2026-11-04 step 1: a white ground takes ink type
    w = _photo(tmp_path / "w.jpg", size=(2100, 1200), lo=240, hi=256)
    d = _photo(tmp_path / "d.jpg", size=(2100, 1200), lo=30, hi=70)
    then_now.render(w, 1925, d, 2019, tmp_path / "z.mp4", width=1920, height=1080)
    assert depths and depths[0] == then_now.BAND_STEPS[0] < then_now.SHADE_STEPS[0]
    assert list(depths) == list(then_now.BAND_STEPS[:len(depths)])     # deepened in order, only as far as needed
    final = got["last"]
    for _, p in got["rows"]:
        assert (p.measure(final) or 0) >= then_now.PLACE_TARGET          # and every marker still holds its floor


def test_916_white_pair_draws_both_bands(tmp_path, monkeypatch):
    calls = []
    real = then_now.band_mask
    monkeypatch.setattr(then_now, "band_mask", lambda *a, **k: calls.append(a[0]) or real(*a, **k))
    got = _encode_capture(monkeypatch)
    monkeypatch.setattr(then_now, "INK_ON_PAPER", False)   # freeze panel 2026-11-04 step 1: a white ground takes ink type
    w = _photo(tmp_path / "w.jpg", lo=244, hi=256)
    v = _photo(tmp_path / "v.jpg", seed=2, lo=244, hi=256)
    then_now.render(w, 1925, v, 2019, tmp_path / "y.mp4")
    assert "then" in calls and "now" in calls
    final = np.asarray(got["last"].convert("L")).astype(int)
    rows = dict(got["rows"])
    _nx0, ny0, nx1, ny1 = rows["now_year"].box
    y = (ny0 + ny1) // 2
    assert final[y, nx1 + 30] < final[1500, 1000] - 25                   # the NOW glow shows beside its year
    assert abs(final[y, 1040] - final[1500, 1000]) <= 6                  # and is gone across the frame: no bar
    for _, p in got["rows"]:
        assert (p.measure(got["last"]) or 0) >= then_now.PLACE_TARGET
