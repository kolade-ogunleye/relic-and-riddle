"""Panel round 3 (2026-11-02), callouts and then-and-now: word-timed reveal with a hold per label and no label drawn
before it is said; 9:16 leaders with one 45-degree bend and a straight stub out of the plate edge; three labels at
most and a crossing label dropped; older labels stepped back; one loupe, beside its label, never past the photo's
native pixels; measured non-text contrast. Then-and-now: a true registered wipe (only the divider moves), measured
registration written beside the clip, exposure matched, and an edge-anchored shade only when a marker needs it."""
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from src import brand, callouts, config, then_now
from tests import callout_fakes

FFMPEG = shutil.which("ffmpeg") is not None
POINTS_4 = [("ventilation shaft", 820, 600), ("rolling stone door", 200, 800), ("carved tuff wall", 700, 1150),
            ("stable trough", 300, 1500)]
POINTS_916 = [("stone door recess", 600, 900), ("carved tuff wall", 300, 1300), ("ventilation shaft", 1000, 420)]


def _photo(path, size=(1200, 2100), seed=1, lo=60, hi=240):
    rng = np.random.default_rng(seed)
    arr = rng.integers(lo, hi, (size[1] // 12, size[0] // 12, 3), dtype=np.uint8)
    Image.fromarray(arr).resize(size, Image.BICUBIC).save(path)
    return path


# ---------------------------------------------------------------- 9:16 leader routing
@pytest.mark.parametrize("points", [POINTS_4[:3], POINTS_916, POINTS_4[1:]])
def test_916_leader_is_one_45_degree_run_then_a_stub_square_out_of_the_edge_centre(points):
    for (_, x, y), lab in zip(points, callouts.layout_labels(points, 1080, 1920)):
        path = callouts._route((x, y), lab["end"], lab["attach"])
        assert path[0] == (x, y) and path[-1] == lab["end"] and len(path) <= 3
        (ax, ay), (bx, by) = path[-2], path[-1]
        if lab["attach"] in ("top", "bottom"):
            assert ax == bx                                     # the stub leaves the edge square to it
        else:
            assert ay == by
        assert callouts._stub(path) >= callouts.STUB
        if len(path) == 3:                                      # the bend: exactly 45 degrees
            (px, py), (qx, qy) = path[0], path[1]
            assert abs(abs(px - qx) - abs(py - qy)) < 1e-6


def test_route_is_straight_when_the_dot_is_square_to_the_attachment():
    assert callouts._route((500, 900), (500, 700), "bottom") == [(500, 900), (500, 700)]
    assert callouts._route((300, 600), (420, 600), "left") == [(300, 600), (420, 600)]


# ---------------------------------------------------------------- clutter control
def test_at_most_three_labels_and_a_clashing_one_is_dropped_last_first():
    kept = callouts.declutter(POINTS_4, 1080, 1920)
    assert len(kept) <= callouts.MAX_LABELS == 3
    assert kept == POINTS_4[:len(kept)]                          # priority order kept; the lowest dropped
    assert not any(lab["conflict"] for lab in callouts.layout_labels(kept, 1080, 1920))


def test_declutter_never_goes_below_two():
    crowded = [("door", 540, 900), ("wall", 545, 905)]          # on top of each other
    # round 5 section 7: with no valid pair declutter returns None (no graphic) instead of a pair in conflict
    assert callouts.declutter(crowded, 1080, 1920) is None


def test_plan_proposes_at_most_three(tmp_path, monkeypatch):
    photo = _photo(tmp_path / "p.jpg")
    # the same features on each of the three location passes (round 5 section 9)
    callout_fakes.install(monkeypatch, [(f"f{i}", 0.2 + 0.15 * i, 0.3 + 0.1 * i, 0.1, 0.1) for i in range(5)])
    _, points = callouts.plan([photo], [f"f{i}" for i in range(5)], "x")
    assert len(points) == callouts.MAX_LABELS


# ---------------------------------------------------------------- word-timed reveal
WORDS = [{"word": w, "start": 20.0 + t, "dur": 0.3} for w, t in (
    ("the", 0.0), ("stone", 0.6), ("door", 0.9), ("recess", 1.1), ("then", 1.4), ("the", 1.6), ("carved", 1.8),
    ("tuff", 2.0), ("wall", 2.2))]
EARLY = [dict(w, start=w["start"] - 0.4) if w["word"] in ("carved", "tuff", "wall") else w for w in WORDS]
BEATS = [{"start": 0.0, "text": "intro"}, {"start": 20.0, "text": " ".join(w["word"] for w in WORDS)},
         {"start": 26.0, "text": "next"}]


def test_each_label_holds_before_the_next_starts():
    labels = ["stone door recess", "carved tuff wall"]
    _, t = callouts.reveal_times(labels, BEATS, WORDS)
    # each on its word, the second held for the first's hold (round 5 section 8: max(1.4 s, 0.06 s per character),
    # so "carved" at 1.8 s waits to 0.6 + 1.4 s)
    assert t == [pytest.approx(0.6), pytest.approx(max(1.8, 0.6 + callouts.hold(labels[0])))]
    _, t = callouts.reveal_times(labels, BEATS, EARLY)                     # "carved" now at 1.4 s: 0.8 s after
    assert t[0] == pytest.approx(0.6) and t[1] == pytest.approx(0.6 + callouts.hold(labels[0]))
    assert callouts.hold("x" * 30) == pytest.approx(1.8)


def test_a_label_said_after_the_clip_is_not_drawn_rather_than_shown_early():
    late = [{"start": 0.0, "text": "door " + "x " * 60 + "wall"}, {"start": 30.0, "text": "end"}]
    _, t = callouts.reveal_times(["door", "wall"], late, squeeze=False)
    assert t[0] is not None and t[1] is None
    _, squeezed = callouts.reveal_times(["door", "wall"], late)              # the default still fits them in
    assert squeezed[1] <= callouts.REVEAL_LAST


def test_the_last_label_stays_open_for_its_hold():
    # round 5 section 8: the last label is fully open LAST_OPEN (1.5 s) before the exit, which starts EXIT_LEAD
    # before the clip ends (was: open MIN_REVEAL_GAP to the clip's end); OPEN_TIME follows the new phase timings
    assert callouts.REVEAL_LAST + callouts.OPEN_TIME + callouts.LAST_OPEN <= callouts.exit_start() + 1e-9
    assert callouts.exit_start() - (callouts.REVEAL_LAST + callouts.OPEN_TIME + callouts.LAST_OPEN) < 0.01
    assert callouts.OPEN_TIME == pytest.approx(callouts.OPEN_FROM_START - callouts.DOT_LEAD)


def test_make_drops_labels_said_outside_the_clip(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CALLOUTS_ENABLED", True)
    photo = _photo(tmp_path / "topic_1.jpg")
    plan_pts = [("door", 0.55, 0.45), ("wall", 0.3, 0.52), ("shaft", 0.7, 0.3)]
    # round 5 section 9: three location passes, then the blind location, blind look, match and decoy
    # object-sized features (operator 2026-11-03: an object keeps its whole extent clear of every plate, and three
    # overlapping 0.3 x 0.2 features leave no layout)
    callout_fakes.install(monkeypatch, [(lb, x, y, 0.08, 0.06) for lb, x, y in plan_pts])
    seen = {}

    def fake_render(photo, points, out, width=1080, height=1920, reveal=None):
        seen.update(points=points, reveal=reveal)
        out.write_bytes(b"clip")
        return out
    monkeypatch.setattr(callouts, "render", fake_render)
    beats = [{"start": 0.0, "text": "the door and the wall " + "x " * 80 + "shaft"}, {"start": 40.0, "text": "end"}]
    clip = callouts.make({"photo_labels": ["door", "wall", "shaft"], "beat_times": beats},
                         {"id": "t", "title": "x"}, [photo], tmp_path / "g", approved=None)
    assert clip is not None and [p[0] for p in seen["points"]] == ["door", "wall"]
    assert all(t <= callouts.REVEAL_LAST for t in seen["reveal"])


def test_plate_wipes_open_from_the_edge_its_leader_meets():
    box = [100, 100, 300, 160]
    m = np.asarray(callouts._wipe_mask((400, 300), box, "bottom", 0.5, pad=0))
    assert m[155, 200] == 255 and m[105, 200] == 0              # the lower half first
    m = np.asarray(callouts._wipe_mask((400, 300), box, "left", 0.5, pad=0))
    assert m[130, 110] == 255 and m[130, 290] == 0
    assert np.asarray(callouts._wipe_mask((400, 300), box, "top", 0.0)).max() == 0


# ---------------------------------------------------------------- loupe
def test_one_loupe_on_the_smallest_feature():
    pts = [("a", 300, 700, 0.03), ("b", 700, 1200, 0.01), ("c", 500, 1500, 0.2)]
    assert callouts.loupe_index(pts) == 1
    labs = callouts.layout_labels(pts, 1080, 1920)
    assert [lab["loupe"] for lab in labs] == [False, True, False]
    assert callouts.loupe_index([("a", 1, 1, 0.5), ("b", 2, 2)]) is None


def test_loupe_never_magnifies_past_the_photos_own_pixels():
    assert callouts.loupe_mag(4.0) == callouts.LOUPE_MAG == 2.0
    assert callouts.loupe_mag(1.8) == pytest.approx(1.8 / (1 + callouts.PUSH_IN), abs=1e-3)
    assert callouts.loupe_mag(1.0) == 0.0                     # an upscaled photo has nothing to magnify


@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)])
def test_loupe_sits_beside_its_label_inside_the_text_box(size):
    w, h = size
    pts = [("stone door recess", w * 0.55, h * 0.47, 0.01), ("carved tuff wall", w * 0.28, h * 0.68, 0.2)]
    lab = callouts.layout_labels(pts, w, h)[0]
    r = callouts._loupe_r(w, h)
    ax, ay = lab["loupe_at"]
    bx0, by0, bx1, by1 = callouts.text_box(w, h)
    assert bx0 <= ax - r and ax + r <= bx1 and by0 <= ay - r and ay + r <= by1
    assert not callouts._overlaps(tuple(lab["box"]), (ax - r, ay - r, ax + r, ay + r), 0)
    u = lab["unit"]
    assert u[0] <= lab["box"][0] and lab["box"][2] <= u[2] and u[1] <= ay - r and ay + r <= u[3]
    x, y = pts[0][1], pts[0][2]
    assert (ax - x) ** 2 + (ay - y) ** 2 > (2 * r) ** 2       # away from the feature it magnifies
    assert not callouts.layout_labels(pts, w, h, loupe=False)[0]["loupe"]


def test_loupe_draws_its_inset_where_asked():
    src = Image.new("RGB", (400, 400), (0, 0, 0))
    src.paste((255, 255, 255), (195, 195, 205, 205))
    out = np.asarray(callouts._loupe(src.copy(), src, 200, 200, 40, 1.0, at=(80, 80)), dtype=int)
    assert (out[80, 75:86] > 200).all() and out[200, 200].max() > 200   # inset at (80, 80); the source untouched


# ---------------------------------------------------------------- non-text contrast
def test_nontext_contrast_reads_the_core_against_its_band():
    im = Image.new("RGB", (100, 100), brand.INK)
    d = ImageDraw.Draw(im)
    d.line([(10, 50), (90, 50)], fill=brand.GOLD, width=3)
    m = Image.new("L", (100, 100), 0)
    ImageDraw.Draw(m).line([(10, 50), (90, 50)], fill=255, width=3)
    assert callouts.nontext_contrast(im, m, 5) > 8
    bare = Image.new("RGB", (100, 100), (235, 190, 80))      # gold on gold rock, no casing
    ImageDraw.Draw(bare).line([(10, 50), (90, 50)], fill=brand.GOLD, width=3)
    assert callouts.nontext_contrast(bare, m, 5) < callouts.NONTEXT_MIN
    assert callouts.nontext_contrast(im, Image.new("L", (100, 100), 0), 5) is None


@pytest.mark.skipif(not FFMPEG, reason="FFmpeg renders the clip")
def test_render_records_measured_nontext_contrast(tmp_path):
    photo = _photo(tmp_path / "p.jpg", lo=150, hi=240)
    # round 5: render refuses a layout in conflict; POINTS_916's wall dot (y 1300) lies below the text box, in the
    # caption band where make never sends a label, so it is drawn at y 1000
    callouts.render(photo, [POINTS_916[0], ("carved tuff wall", 300, 1000)], tmp_path / "graphic_labels.mp4")
    rec = json.loads((tmp_path / "graphic_labels.mp4.contrast.json").read_text(encoding="utf-8"))
    assert rec["elements"] and rec["nontext_target"] == callouts.NONTEXT_MIN
    names = {r["name"] for r in rec["nontext"]}
    assert {"label_1_leader", "label_1_dot", "label_2_leader", "label_2_dot"} <= names
    assert all(r["ratio"] is None or r["ratio"] >= callouts.NONTEXT_MIN for r in rec["nontext"]), rec["nontext"]


# ---------------------------------------------------------------- then and now: the registered wipe
@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)])
def test_divider_holds_then_sweeps_dwells_overshoots_and_settles(size):
    w, h = size
    edge, split = max(w, h), then_now._split_at(w, h)
    W = then_now.WIPE
    assert then_now.divider_at(W["then"] - 0.01, w, h) == edge             # THEN alone
    t_mid = W["then"] + W["to_split"] + W["dwell"] / 2
    assert then_now.divider_at(t_mid, w, h) == pytest.approx(split)        # the dwell at the split
    t_far = W["then"] + W["to_split"] + W["dwell"] + W["to_far"]
    assert then_now.divider_at(t_far, w, h) == pytest.approx(edge * (1 - then_now.SWEEP_TO))
    assert then_now.divider_at(then_now.CLIP_SECONDS, w, h) == pytest.approx(split)
    assert then_now.CLIP_SECONDS - sum(W.values()) >= 1.2
    assert W["then"] == 1.2 and W["to_split"] + W["dwell"] + W["to_far"] == pytest.approx(1.4) and W["settle"] == 0.8


def test_compose_keeps_each_photo_at_its_own_pixels():
    ramp = np.tile(np.arange(1920, dtype=np.float64) / 1920 * 255, (1080, 1)).astype(np.uint8)
    a = Image.fromarray(np.dstack([ramp] * 3))
    b = Image.fromarray(np.dstack([255 - ramp] * 3))
    for pos in (400, 960, 1500):
        im = np.asarray(then_now.compose(a, b, pos))
        assert tuple(im[540, 100]) == tuple(np.asarray(a)[540, 100])        # not a centre window that slides
        assert tuple(im[540, 1900]) == tuple(np.asarray(b)[540, 1900])
    v = Image.fromarray(np.dstack([np.tile(np.arange(1920, dtype=np.uint8)[:, None] // 8, (1, 1080))] * 3))
    im = np.asarray(then_now.compose(v, Image.new("RGB", v.size), 700))
    assert tuple(im[100, 540]) == tuple(np.asarray(v)[100, 540])


# ---------------------------------------------------------------- then and now: measured registration, exposure
def _scene(path, seed=3, size=(1400, 900)):
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 255, (size[1] // 8, size[0] // 8), dtype=np.uint8)
    Image.fromarray(base).resize(size, Image.NEAREST).filter(ImageFilter.GaussianBlur(1.2)).convert("RGB").save(path)
    return path


def _old_of(src, path):
    im = Image.open(src).convert("L")
    w, h = im.size
    im = im.rotate(3.0, resample=Image.BICUBIC).resize((int(w * 1.1), int(h * 1.1)), Image.LANCZOS)
    ImageOps.colorize(im.crop((60, 25, 60 + w, 25 + h)), (40, 25, 10), (235, 215, 180)).save(path)
    return path


def test_registration_is_measured_and_written_beside_the_clip(tmp_path, monkeypatch):
    new = _scene(tmp_path / "new.jpg")
    old = _old_of(new, tmp_path / "old.jpg")
    geo = then_now.geometric_match(old, new)
    assert geo and 0 <= geo["reproj_share"] <= then_now.MAX_REPROJ and geo["reproj_px"] >= 0
    pair = then_now.register(old, new, geo["matrix"], tmp_path / "reg", geo)
    rec = json.loads((tmp_path / "reg" / then_now.REGISTRATION_SIDECAR).read_text(encoding="utf-8"))
    assert rec["inliers"] == geo["inliers"] and "matrix" not in rec and "exposure_gamma" in rec
    monkeypatch.setattr(then_now, "source", lambda topic, wd: (pair[0], {"year": 1900}, pair[1], {"year": 2020}))

    def fake_render(o, oy, n, ny, out, width=1080, height=1920):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"clip")
        return out
    monkeypatch.setattr(then_now, "render", fake_render)
    clip, credits = then_now.make({}, tmp_path / "w")
    side = json.loads((tmp_path / "w" / "graphic_thennow.mp4.registration.json").read_text(encoding="utf-8"))
    assert clip and side["inliers"] == geo["inliers"] and len(credits) == 2


def test_a_loose_fit_fails_the_reprojection_gate(tmp_path, monkeypatch):
    new = _scene(tmp_path / "new.jpg")
    old = _old_of(new, tmp_path / "old.jpg")
    monkeypatch.setattr(then_now, "MAX_REPROJ", 1e-7)
    assert then_now.geometric_match(old, new) is None


def test_exposure_is_matched_on_the_recent_photo_only():
    old = np.full((60, 80, 3), 150, np.uint8)
    dark = np.full((60, 80, 3), 40, np.uint8)
    out, g = then_now.match_exposure(old, dark)
    assert g < 1 and abs(then_now._mean_lstar(out) - then_now._mean_lstar(old)) <= then_now.MAX_LSTAR_GAP
    same, g1 = then_now.match_exposure(old, np.full((60, 80, 3), 145, np.uint8))
    assert g1 == 1.0 and int(same[0, 0, 0]) == 145                          # already close: untouched


# ---------------------------------------------------------------- then and now: the marker shade
def test_shade_is_anchored_at_the_panel_edge_and_full_over_the_marker():
    boxes = [(83, 330, 170, 350), (80, 372, 310, 484)]
    corner = then_now.shade_corner("then", boxes, 1080, 1920)
    assert corner[0] == 0 and 330 <= corner[1] <= 484                      # 9:16: the left edge, level with it
    m = np.asarray(then_now._shade_mask((1080, 1920), boxes, corner, 0.6))
    full = round(255 * 0.6)
    assert m[corner[1], 0] == full and m[350, 300] == full and m[484, 310] >= full - 1
    assert m[1500, 900] == 0 and m[corner[1], 310 + then_now.SHADE_FALL + 5] == 0
    assert then_now.shade_corner("now", [(1024, 130, 1200, 300)], 1920, 1080) == (960, 0)   # 16:9: at the divider


def test_overlapping_shades_do_not_stack():
    a = Image.new("L", (10, 10), 128)
    u = then_now._union([(a, 1.0), (a, 1.0)])
    assert u.getpixel((5, 5)) == 128
    assert then_now._union([(a, 0.5), (Image.new("L", (10, 10), 20), 1.0)]).getpixel((0, 0)) == 64


def test_no_shade_when_the_halo_already_holds(tmp_path, monkeypatch):
    calls = []
    real = then_now._shade_mask
    monkeypatch.setattr(then_now, "_shade_mask", lambda *a, **k: calls.append(1) or real(*a, **k))
    monkeypatch.setattr(then_now, "_encode", lambda frames, out, n, **k: out)
    a = Image.new("RGB", (1200, 2100), (90, 80, 70))
    a.save(tmp_path / "a.jpg")
    a.save(tmp_path / "b.jpg")
    then_now.render(tmp_path / "a.jpg", 1925, tmp_path / "b.jpg", 2019, tmp_path / "x.mp4")
    assert calls == []
    monkeypatch.setattr(then_now, "INK_ON_PAPER", False)   # freeze panel 2026-11-04 step 1: a white ground takes ink type
    w = _photo(tmp_path / "w.jpg", lo=240, hi=256)
    then_now.render(w, 1925, w, 2019, tmp_path / "y.mp4")
    assert calls                                                           # near-white: the shade steps in


# ---------------------------------------------------------------- round-3 review fixes
@pytest.mark.parametrize("size,which,boxes", [
    ((1920, 1080), "then", [(830, 130, 930, 160), (790, 180, 930, 290)]),
    ((1920, 1080), "now", [(990, 130, 1090, 160), (990, 180, 1130, 290)]),
    ((1080, 1920), "then", [(83, 330, 170, 350), (80, 372, 310, 484)]),
])
def test_shade_falls_off_over_shade_fall_screen_pixels_on_both_formats(size, which, boxes):
    """The review measured 240 -> 126 luminance in 36 px on 16:9 (fade in normalised ellipse space, ~30 px)."""
    w, h = size
    alpha = 0.88
    corner = then_now.shade_corner(which, boxes, w, h)
    m = np.asarray(then_now._shade_mask(size, boxes, corner, alpha)).astype(int)
    full = round(255 * alpha)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    # full depth over the marker, zero at SHADE_FALL px beyond its far side, and still shading half-way out
    assert all(m[b[1], b[0]] == full and m[b[3] - 1, b[2] - 1] == full for b in boxes)
    fall = then_now.SHADE_FALL
    assert m[y1 + fall + 1, (min(b[0] for b in boxes) + x1) // 2] == 0
    assert m[y1 + fall // 2, (min(b[0] for b in boxes) + x1) // 2] == pytest.approx(full / 2, abs=3)
    if w > h:   # the far side across the panel
        far = x1 if which == "now" else min(b[0] for b in boxes)
        step = 1 if which == "now" else -1
        assert m[y1 - 20, far + step * (fall + 1)] == 0
        assert m[y1 - 20, far + step * (fall // 2)] == pytest.approx(full / 2, abs=3)
    # bounded gradient: no 60 px run changes by more than the smoothstep allows over 60 of SHADE_FALL px
    def smooth(u):
        return 3 * u * u - 2 * u ** 3
    s = 60 / fall   # the steepest 60 px window is centred on the fade's midpoint
    cap = full * (smooth(0.5 + s / 2) - smooth(0.5 - s / 2)) + 2
    assert np.abs(m[:, 60:] - m[:, :-60]).max() <= cap
    assert np.abs(m[60:, :] - m[:-60, :]).max() <= cap
    assert max(np.abs(np.diff(m, axis=0)).max(), np.abs(np.diff(m, axis=1)).max()) <= 1.5 * full / fall + 1


def test_final_hold_is_what_the_comment_says():
    W = then_now.WIPE
    assert sum(W.values()) == pytest.approx(3.4)
    assert then_now.CLIP_SECONDS - sum(W.values()) == pytest.approx(2.6)
    src = Path(then_now.__file__).read_text()
    assert ">= 1.2 s" not in src and "= 2.6 s" in src
