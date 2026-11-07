"""Callout spec round 5 (creative director, 2026-11-02): one leader style on both formats (S1 straight or S2 one
45-degree bend with a fillet) meeting the exact centre of any plate edge, rigid lock to the dot's push, hard limits
checked at every sampled t, an exact search, declutter that drops the label in conflict, the gold run unbroken from
the plate's bar into the marker, an opaque ink plate, motion cued to the word with an exit, and the trust trail
(native-aspect location shared by both formats, padded crosshair crops, a same-site check, tiers, audit, gate)."""
import json
import math
from itertools import combinations, pairwise
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import callouts, config, free_llm
from tests import callout_fakes

P2_916 = [("stone door recess", 600, 900, 0.05, 260, 300), ("carved tuff wall", 300, 1010, 0.08, 300, 380)]
P2_169 = [("stone door recess", 1100, 500, 0.05, 260, 300), ("carved tuff wall", 600, 620, 0.08, 300, 380),
          ("rock-cut stair", 1450, 700, 0.03, 200, 200)]
WHITE_916 = [("lime-plastered dome", 540, 700, 0.1, 400, 300), ("east lantern window", 800, 1000, 0.02, 120, 160),
             ("door", 250, 1050, 0.02, 120, 200)]
EDGE_916 = [("north tower", 160, 560, 0.03, 160, 200), ("south gate arch", 930, 1040, 0.03, 160, 200)]
WHITE_169 = [("lime-plastered dome", 960, 380, 0.1, 400, 300), ("east lantern window", 1350, 600, 0.02, 120, 160),
             ("door", 600, 650, 0.02, 120, 200)]
# The operator's kind rule (2026-11-03, replacing round 5's size rule OBJECT_MAX / SURFACE_CLEAR): an object keeps
# its whole located box clear of every plate; a surface (kind "extent") only its inner half and 40 [36] px round its
# dot. Under it WHITE_169's three objects get only a pair, and the reference 9:16 pair P2_916 is laid out with the
# wall as a surface (both tested below), so the geometry cases use NICHE_916, an object pair, and WHITE_169_SURFACE,
# the dome as a large surface (over 35 % of the frame: a point marker) whose outer part a plate may cover.
NICHE_916 = [("stone door recess", 640, 720, 0.02, 160, 220), ("carved niche", 300, 980, 0.01, 110, 140)]
WHITE_169_SURFACE = [("lime-plastered dome", 960, 480, 0.4, 1200, 700, "extent", (360, 130, 1560, 830)),
                     ("east lantern window", 1350, 600, 0.02, 120, 160), ("door", 600, 650, 0.02, 120, 200)]
CASES = [(NICHE_916, (1080, 1920)), (P2_169, (1920, 1080)), (EDGE_916, (1080, 1920)), (WHITE_169_SURFACE, (1920, 1080)),
         (WHITE_916[::2], (1080, 1920))]


def _photo(path, size=(1200, 2100), seed=1, lo=60, hi=240):
    rng = np.random.default_rng(seed)
    arr = rng.integers(lo, hi, (size[1] // 12, size[0] // 12, 3), dtype=np.uint8)
    Image.fromarray(arr).resize(size, Image.BICUBIC).save(path)
    return path


def _fmt(size):
    return "landscape" if size[0] > size[1] else "portrait"


# ---------------------------------------------------------------- geometry
@pytest.mark.parametrize("points,size", CASES)
def test_every_leader_meets_an_exact_edge_centre_on_any_of_the_four_edges(points, size):
    for lab in callouts.layout_labels(points, *size):
        assert not lab["conflict"]
        x0, y0, x1, y1 = lab["box"]
        ex, ey = lab["end"]
        want = {"top": ((x0 + x1) / 2, y0), "bottom": ((x0 + x1) / 2, y1),
                "left": (x0, (y0 + y1) / 2), "right": (x1, (y0 + y1) / 2)}[lab["attach"]]
        assert (ex, ey) == pytest.approx(want, abs=0.5)
        assert lab["path"][-1] == pytest.approx((ex, ey))


def test_all_four_edges_are_generated_and_kept_by_the_direction_to_the_dot():
    fmt, bounds = "portrait", callouts.text_box(1080, 1920)
    m = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    ft = callouts._Feat(0, ("north tower", 480, 700), 1080, 1920, fmt, False, 50)
    cs = callouts._candidates(ft, callouts._shapes(m, "north tower", fmt), fmt, 100, bounds)
    assert {c["edge"] for c in cs} == {"top", "bottom", "left", "right"}
    for c in cs:
        px0, py0, px1, py1 = c["plate"]
        phi = math.degrees(math.atan2(abs(ft.x - (px0 + px1) / 2), abs(ft.y - (py0 + py1) / 2)))
        if phi <= 40:
            assert c["edge"] in ("top", "bottom")
        if phi >= 60:
            assert c["edge"] in ("left", "right")
    # review 2026-11-03: a finer grid between the coarse slides (FINE_STEP px), capped after the hard limits
    acrosses = {round(c["end"][0] - ft.x) for c in cs if c["edge"] in ("top", "bottom")}
    assert any(abs(a) % callouts.FINE_STEP == 0 and abs(a) not in (0,) and 34 <= abs(a) < 80 for a in acrosses)


@pytest.mark.parametrize("points,size", CASES)
def test_every_route_is_s1_or_s2_with_its_minimum_stub_and_diagonal(points, size):
    fmt = _fmt(size)
    for lab in callouts.layout_labels(points, *size):
        path, edge = lab["path"], lab["attach"]
        tx, ty = lab["target"]
        ex, ey = lab["end"]
        across = (tx - ex) if edge in ("top", "bottom") else (ty - ey)
        if lab["route"] == "S1":
            assert len(path) == 2 and abs(across) <= callouts.JOG_MAX
        else:
            assert lab["route"] == "S2" and len(path) == 3
            assert callouts._stub(path) >= callouts.STUB_MIN[fmt] - 1e-6
            (ax, ay), (bx, by) = path[0], path[1]
            assert math.hypot(bx - ax, by - ay) >= callouts.DIAG_MIN - 1e-6
        lo, _, _, hi = callouts.LEADER_LEN[fmt]
        assert lo - 1e-6 <= lab["length"] <= hi + 1e-6


@pytest.mark.parametrize("points,size", CASES)
def test_no_route_has_a_90_degree_elbow_or_a_second_bend(points, size):
    for lab in callouts.layout_labels(points, *size):
        path = lab["path"]
        assert len(path) <= 3 and lab["elbow"] is None
        if len(path) == 3:
            (ax, ay), (bx, by), (cx, cy) = path
            u, v = (ax - bx, ay - by), (cx - bx, cy - by)
            ang = math.degrees(math.acos((u[0] * v[0] + u[1] * v[1]) / (math.hypot(*u) * math.hypot(*v))))
            assert ang == pytest.approx(135, abs=1e-6)          # a 45-degree turn, never 90
    assert callouts.ELBOW_RUN is None and callouts.ELBOW_RISE is None


@pytest.mark.parametrize("fmt", ["portrait", "landscape"])
def test_s2_fillet_radius_and_a_straight_aimed_diagonal(fmt):
    r = callouts.FILLET_R[fmt]
    dot, end = (300.0, 900.0), (400.0, 700.0)
    path = callouts._route(dot, end, "bottom")
    assert len(path) == 3
    drawn = callouts._fillet(path, r)
    arc = drawn[1:-1]
    assert len(arc) == callouts.FILLET_PTS
    # the arc's points sit on one circle of radius r
    (ax, ay), (bx, by), (cx, cy) = arc[0], arc[len(arc) // 2], arc[-1]
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    ux = ((ax ** 2 + ay ** 2) * (by - cy) + (bx ** 2 + by ** 2) * (cy - ay) + (cx ** 2 + cy ** 2) * (ay - by)) / d
    uy = ((ax ** 2 + ay ** 2) * (cx - bx) + (bx ** 2 + by ** 2) * (ax - cx) + (cx ** 2 + cy ** 2) * (bx - ax)) / d
    assert all(math.hypot(px - ux, py - uy) == pytest.approx(r, abs=1e-6) for px, py in arc)
    # the diagonal's straight run after the fillet points at the dot centre, and is long enough
    (sx, sy), (fx, fy) = drawn[0], drawn[1]
    straight = math.hypot(fx - sx, fy - sy) - callouts.DOT_DISC[fmt]
    assert straight >= callouts.AIM_MIN
    (qx, qy) = path[1]
    assert (fx - qx) * (sy - qy) - (fy - qy) * (sx - qx) == pytest.approx(0, abs=1e-6)   # on the dot's line


@pytest.mark.parametrize("points,size", CASES[:2])
def test_rigid_lock_keeps_the_leader_shape_relative_to_its_dot(points, size):
    push = callouts.Push(*size)
    for lab in callouts.layout_labels(points, *size, push=push):
        dx0, dy0 = lab["dot"]
        rel = []
        for t in (0.0, callouts.CLIP_SECONDS):
            sx, sy = push.shift(dx0, dy0, t)
            dot_t = push.at(dx0, dy0, t)
            assert (dx0 + sx, dy0 + sy) == pytest.approx(dot_t)
            rel.append([(x + sx - dot_t[0], y + sy - dot_t[1]) for x, y in lab["drawn"]])
        assert np.abs(np.array(rel[0]) - np.array(rel[1])).max() <= 0.5


def _seg_rect_gap(a, b, box):
    return float(callouts._seg_rect(a[0], a[1], b[0], b[1], *box))


def test_co_white_916_gives_a_clean_pair_or_nothing_never_a_conflict():
    w, h = 1080, 1920
    kept = callouts.declutter(WHITE_916, w, h)
    assert kept is None or len(kept) == 2
    if kept is None:
        return
    push = callouts.Push(w, h)
    labs = callouts.layout_labels(kept, w, h, push=push)
    assert not any(lab["conflict"] for lab in labs)
    for t in callouts.SAMPLES:
        moved = []
        for lab in labs:
            sx, sy = push.shift(*lab["dot"], t)
            box = [lab["box"][0] + sx, lab["box"][1] + sy, lab["box"][2] + sx, lab["box"][3] + sy]
            path = [(x + sx, y + sy) for x, y in callouts._trim(lab["path"], lab["r_vis"])]
            moved.append((box, path, lab))
        for (ba, pa, la), (bb, pb, lb) in combinations(moved, 2):
            for p, q in pairwise(pa):
                assert _seg_rect_gap(p, q, bb) >= callouts.LABEL_GAP - 1e-6
                for r, s in pairwise(pb):
                    assert float(callouts._seg_seg(*p, *q, *r, *s)) >= callouts.LEADER_GAP - 1e-6
            for p, q in pairwise(pb):
                assert _seg_rect_gap(p, q, ba) >= callouts.LABEL_GAP - 1e-6
        for (box, _, lab), pt in zip(moved, kept):   # off its own feature's ellipse (objects) at every t
            ft = callouts._Feat(0, pt, w, h, "portrait", False, 0)
            cx, cy = push.at(pt[1], pt[2], t)
            a, b = ft.a * push.zoom(t) + callouts.OWN_CLEAR, ft.b * push.zoom(t) + callouts.OWN_CLEAR
            nx = np.clip(0.0, box[0] - cx, box[2] - cx) / a
            ny = np.clip(0.0, box[1] - cy, box[3] - cy) / b
            assert math.hypot(nx, ny) >= 1 - 1e-6


def test_render_refuses_a_layout_in_conflict(tmp_path, monkeypatch):
    photo = _photo(tmp_path / "p.jpg")
    real = callouts.layout_labels

    def conflicted(*a, **k):
        labs = real(*a, **k)
        labs[0]["conflict"] = True
        return labs
    monkeypatch.setattr(callouts, "layout_labels", conflicted)
    monkeypatch.setattr(callouts, "_encode", lambda frames, out, n, **k: out)
    with pytest.raises(ValueError):
        callouts.render(photo, P2_916, tmp_path / "x.mp4", reveal=[0.6, 2.2])


def test_declutter_drops_the_label_in_conflict_not_the_last_and_none_when_no_pair_is_clean():
    w, h = 1080, 1920
    pts = [("north tower", 420, 640, 0.01, 80, 80), ("south tower", 440, 660, 0.01, 80, 80),
           ("gate arch", 640, 980, 0.01, 80, 80)]
    kept = callouts.declutter(pts, w, h)
    assert kept is not None and len(kept) == 2 and pts[2] in kept     # dropping the last would not clear it
    assert not any(lab["conflict"] for lab in callouts.layout_labels(kept, w, h))
    assert callouts.declutter([("door", 540, 900), ("wall", 545, 905)], w, h) is None


def test_side_attachment_when_the_dot_is_level_and_text_flush_toward_the_leader():
    labs = callouts.layout_labels(EDGE_916, 1080, 1920)
    sides = [lab for lab in labs if lab["attach"] in ("left", "right")]
    assert sides
    for lab in sides:
        x0, y0, x1, y1 = lab["box"]
        dx = lab["dot"][0] - (x0 + x1) / 2
        dy = lab["dot"][1] - (y0 + y1) / 2
        assert math.degrees(math.atan2(abs(dx), abs(dy))) > 40
        if lab["attach"] == "left":
            assert (lab["anchor"], lab["text_x"]) == ("lm", x0 + lab["pad"])
        else:
            assert (lab["anchor"], lab["text_x"]) == ("rm", x1 - lab["pad"])


def test_plate_tops_within_40px_are_snapped_when_the_limits_allow():
    w, h, fmt = 1080, 1920, "portrait"
    pts = [("west niche", 300, 760, 0.01, 80, 80), ("east niche", 760, 790, 0.01, 80, 80)]
    bounds = callouts.text_box(w, h)
    m = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    feats = [callouts._Feat(k, p, w, h, fmt, False, 0) for k, p in enumerate(pts)]
    ctx = callouts._Ctx(feats, w, h, bounds, fmt)
    cands = []
    for k, ft in enumerate(feats):
        cs = callouts._candidates(ft, callouts._shapes(m, pts[k][0], fmt), fmt, 100, bounds)
        ok = ctx.single_ok(k, cs)
        cs = [c for c, o in zip(cs, ok) if o]
        for c in cs:
            c["static"] = ctx.static(c)
        cands.append(cs)
    pair = next((a, b) for a in cands[0] for b in cands[1]
                if 0.5 < abs(a["plate"][1] - b["plate"][1]) <= callouts.ROW_SNAP and callouts._valid_set(ctx, [a, b]))
    snapped, failed = callouts._snap(ctx, list(pair), 100)
    assert failed == 0 and snapped[0]["plate"][1] == pytest.approx(snapped[1]["plate"][1])
    assert callouts._valid_set(ctx, snapped)
    for lab in callouts.layout_labels(pts, w, h):                       # the search's own pick is aligned too
        assert not lab["conflict"]
    labs = callouts.layout_labels(pts, w, h)
    gap = abs(labs[0]["box"][1] - labs[1]["box"][1])
    assert gap == 0 or gap > callouts.ROW_SNAP


@pytest.mark.parametrize("points,size", [(P2_169, (1920, 1080)), (WHITE_169_SURFACE, (1920, 1080))])
def test_three_plates_stay_inside_the_text_box_at_every_sampled_t(points, size):
    push = callouts.Push(*size)
    bx0, by0, bx1, by1 = callouts.text_box(*size)
    labs = callouts.layout_labels(points, *size, push=push)
    assert len(labs) == 3 and not any(lab["conflict"] for lab in labs)
    for lab in labs:
        for t in callouts.SAMPLES:
            sx, sy = push.shift(*lab["dot"], t)
            u = lab["unit"]
            assert bx0 - 0.5 <= u[0] + sx and u[2] + sx <= bx1 + 0.5 and by0 - 0.5 <= u[1] + sy and u[3] + sy <= by1 + 0.5


@pytest.mark.parametrize("points,size", CASES)
def test_a_plate_is_nearer_its_own_dot_than_any_other(points, size):
    labs = callouts.layout_labels(points, *size)
    for lab in labs:
        x0, y0, x1, y1 = lab["box"]

        def dist(px, py, x0=x0, y0=y0, x1=x1, y1=y1):
            return math.hypot(max(x0 - px, 0, px - x1), max(y0 - py, 0, py - y1))
        own = dist(*lab["dot"])
        for other in labs:
            if other is not lab:
                assert own <= callouts.OWN_RATIO * dist(*other["dot"]) + 1


# ---------------------------------------------------------------- rendering
@pytest.fixture
def frames_of(tmp_path, monkeypatch):
    def run(points, size=(1080, 1920), reveal=None, lo=60, hi=240):
        frames = []
        monkeypatch.setattr(callouts, "_encode", lambda fr, out, n, **k: [frames.append(f) for f in fr] and out)
        w, h = size
        photo = _photo(tmp_path / "p.jpg", size=(1200, 2100) if h > w else (2100, 1200), lo=lo, hi=hi)
        callouts.render(photo, points, tmp_path / "x.mp4", width=w, height=h, reveal=reveal)
        rec = json.loads((tmp_path / "x.mp4.contrast.json").read_text(encoding="utf-8"))
        return frames, rec
    return run


def test_no_leader_gold_inside_any_plate_and_the_plate_is_opaque_ink(frames_of, monkeypatch):
    got = []
    real = callouts.layout_labels
    monkeypatch.setattr(callouts, "layout_labels", lambda *a, **k: got.append(real(*a, **k)) or got[-1])
    frames, _ = frames_of(NICHE_916, reveal=[0.6, 2.2])
    labs = got[0]                                            # the layout render drew (it scores the photo too)
    push = callouts.Push(1080, 1920)
    exit_frame = round(callouts.exit_start() * config.VIDEO_FPS)
    for i in (round(3.0 * config.VIDEO_FPS), exit_frame - 1):
        im = np.asarray(frames[i], dtype=int)
        t = i / config.VIDEO_FPS
        for lab in labs:
            sx, sy = push.shift(*lab["dot"], t)
            x0, y0, x1, y1 = (round(lab["box"][0] + sx) + 3, round(lab["box"][1] + sy) + 3,
                              round(lab["box"][2] + sx) - 3, round(lab["box"][3] + sy) - 3)
            patch = im[y0:y1, x0:x1].copy()
            bx0, by0, bx1, by1 = (round(v) for v in lab["bar"])
            patch[max(0, by0 + round(sy) - 3 - y0):max(0, by1 + round(sy) + 3 - y0),
                  max(0, bx0 + round(sx) - 3 - x0):max(0, bx1 + round(sx) + 3 - x0)] = 0
            gold = (patch[..., 0] > 150) & (patch[..., 2] < 110) & (patch[..., 1] > 100)
            assert gold.sum() == 0
            # between the edge and the text the plate is the brand ink, opaque over the photo
            cy = (y0 + y1) // 2
            assert np.abs(im[cy - 2:cy + 2, x0 + 2:x0 + 6] - np.array(callouts.INK)).max() <= 6
    assert callouts.PLATE_ALPHA == 255


def test_the_gold_runs_unbroken_from_the_bar_into_the_marker():
    for points, size in CASES[:3]:
        fmt = _fmt(size)
        for lab in callouts.layout_labels(points, *size):
            art = callouts._Art(lab, fmt, *size)
            sx, sy = art.gold_path[0]
            dx, dy = lab["dot"]
            if lab["loupe"]:
                assert math.hypot(sx - dx, sy - dy) == pytest.approx(lab["src_r"] + 1, abs=1e-6)
            elif lab["mbox"] is None:   # 0.5 px into the 6 px [5 px] core
                assert math.hypot(sx - dx, sy - dy) == pytest.approx(callouts.DOT_CORE[fmt] - 0.5, abs=1e-6)
            ex, ey = lab["end"]
            gx, gy = art.gold_path[-1]
            assert math.hypot(gx - ex, gy - ey) == pytest.approx(1.5)   # 1.5 px into the 3 px bar
            assert art.casing_path[-1] == pytest.approx(lab["end"])    # the casing stops at the plate edge
            bx0, by0, bx1, by1 = lab["bar"]
            assert min(bx1 - bx0, by1 - by0) == callouts.BAR_W == callouts.LEADER_W
            assert bx0 - 1e-6 <= gx <= bx1 + 1e-6 and by0 - 1e-6 <= gy <= by1 + 1e-6


def test_the_marker_does_not_loop_after_landing():
    assert not hasattr(callouts, "PULSE_HZ")
    assert callouts.RIPPLE_TIME < 0.6 and callouts._ease_back(1.0) == 1.0
    lab = callouts.layout_labels(P2_916, 1080, 1920)[0]
    art = callouts._Art(lab, "portrait", 1080, 1920)
    a = art.lines(1.0, 1.0, 1.0, 2)
    b = art.lines(callouts._ease_back(5.0), 1.0, 1.0, 2)
    assert np.array_equal(np.asarray(a[0]), np.asarray(b[0])) and np.array_equal(np.asarray(a[1]), np.asarray(b[1]))
    assert max(abs(callouts._ease_back(u / 100)) for u in range(101)) == pytest.approx(1.08, abs=0.002)


def test_tracked_text_width_fills_the_plate_less_its_padding():
    for points, size in CASES[:3]:
        for lab in callouts.layout_labels(points, *size):
            x0, _, x1, _ = lab["box"]
            assert max(lab["widths"]) == pytest.approx(x1 - x0 - 2 * lab["pad"], abs=1.0)
            assert lab["track"] == pytest.approx(callouts.LABEL_TRACK * lab["font"].size)
            assert lab["step"] == round(1.08 * lab["font"].size)
            assert len(lab["lines"]) <= 2 and all(len(ln) <= callouts.LABEL_MAX_CHARS for ln in lab["lines"])


def test_contrast_holds_at_the_dimmed_values(frames_of, monkeypatch):
    got = []
    real = callouts.layout_labels
    monkeypatch.setattr(callouts, "layout_labels", lambda *a, **k: got.append(real(*a, **k)) or got[-1])
    frames, rec = frames_of(NICHE_916, reveal=[0.6, 2.2], lo=150, hi=240)
    assert rec["elements"] and all(e["drawn"] >= 4.5 for e in rec["elements"])
    assert all(r["ratio"] is None or r["ratio"] >= callouts.NONTEXT_MIN for r in rec["nontext"])
    # label 1 is measured after label 2 lands, at a dim below full strength that its guard step records
    older = [r for r in rec["nontext"] if r["name"].startswith("label_1_") and r["t"] > 2.6]
    assert older
    for r in older:
        step = next(g for g in callouts._GUARD if (g[0], g[1], g[2]) == (r["casing"], r["casing_px"], r["dim"]))
        assert r["dim"] == step[2] < 1.0
    # and the frames really are dimmed there: label 1's leader gold is pure GOLD alone, blended once label 2 lands
    lab, push = got[0][0], callouts.Push(1080, 1920)
    mid = callouts._partial(callouts._trim(lab["drawn"], lab["r_gold"]), 0.5)[-1]

    def off_gold(t):
        sx, sy = push.shift(*lab["dot"], t)
        x, y = round(mid[0] + sx), round(mid[1] + sy)
        px = np.asarray(frames[round(t * config.VIDEO_FPS)], dtype=int)[y - 1:y + 2, x - 1:x + 2].reshape(-1, 3)
        return int(np.abs(px - np.array(callouts.GOLD)).sum(axis=1).min())
    assert off_gold(2.0) <= 6
    assert off_gold(4.0) >= 30


# ---------------------------------------------------------------- motion
def test_the_leader_draws_at_a_constant_speed():
    for arc in (60, 150, 260, 300):
        tl = callouts._Timeline(1.0, arc)
        assert tl.dur == pytest.approx(min(callouts.LEADER_MAX, max(callouts.LEADER_MIN, arc / 1500)))
        lab = callouts.layout_labels(P2_916, 1080, 1920)[0]
        path = callouts._Art(lab, "portrait", 1080, 1920).casing_path
        total = callouts._length(path)
        steps = [callouts._length(callouts._partial(path, f)) for f in (0.2, 0.4, 0.6, 0.8)]
        speeds = [b - a for a, b in pairwise([0.0, *steps])]
        assert max(speeds) <= 1.05 * min(speeds) and steps[-1] == pytest.approx(0.8 * total)


@pytest.mark.parametrize("arc", [56, 120, 260, 420])
def test_text_readable_within_0_30_s_and_the_dot_within_its_window(arc):
    f0 = 2.0
    tl = callouts._Timeline(f0 - callouts.DOT_LEAD, arc)
    assert -0.25 <= tl.start - f0 <= 0.5
    assert tl.text1 - f0 <= callouts.READ_BY + 1e-9
    assert tl.opened - f0 <= callouts.OPEN_TIME + 1e-9


def test_last_label_holds_before_the_exit_and_the_exit_ends_in_the_clip():
    last = callouts.REVEAL_LAST
    tl = callouts._Timeline(last - callouts.DOT_LEAD, 400)
    assert callouts.exit_start() - tl.opened >= callouts.LAST_OPEN - 1e-9
    assert callouts.exit_start() + callouts.EXIT_TIME <= callouts.CLIP_SECONDS
    assert callouts.hold("x" * 10) == 1.4 and callouts.hold("x" * 30) == pytest.approx(1.8)
    assert callouts.hold("x" * 30, landscape=True) == 1.3


def test_the_head_noun_fallback_needs_the_word_once_and_cues_record_their_method():
    assert callouts._mention(callouts._tokens("the old shaft and the shaft"), "ventilation shaft") is None
    assert callouts._mention(callouts._tokens("down the shaft"), "ventilation shaft") == 2
    words = [{"word": w, "start": 10.0 + 0.4 * i, "dur": 0.3} for i, w in enumerate(
        ["past", "the", "stone", "door", "recess", "the", "carved", "tuff", "wall"])]
    beats = [{"start": 0.0, "text": "intro"}, {"start": 10.0, "text": " ".join(w["word"] for w in words)}]
    _, _, info = callouts.reveal_plan(["stone door recess", "carved tuff wall", "a shaft"], beats, words)
    assert [i["method"] for i in info] == ["timed", "timed", "stagger"]
    assert info[0]["word"] == "stone" and info[0]["start"] == pytest.approx(10.8)
    _, _, est = callouts.reveal_plan(["stone door recess", "carved tuff wall"], beats)
    assert [i["method"] for i in est] == ["estimated", "estimated"]


# ---------------------------------------------------------------- trust
def _fake(monkeypatch, feats, sizes=None, calls=None, **kw):
    # the round-5 section 9 flow: three location passes, then a blind location, a blind look, a match, a decoy
    callout_fakes.install(monkeypatch, feats, sizes=sizes, calls=calls, **kw)


def test_a_landscape_photo_is_located_whole_at_its_native_aspect(tmp_path, monkeypatch):
    photo = _photo(tmp_path / "wide.jpg", size=(2400, 1350))
    sizes = []
    _fake(monkeypatch, [("door", 0.5, 0.5), ("wall", 0.3, 0.6)], sizes=sizes)
    callouts.plan([photo], ["door", "wall"], "x", width=1080, height=1920)
    assert sizes == [(1536, 864)] * 3                            # not squashed to 576 x 1024; three passes


def test_one_location_is_shared_by_both_formats(tmp_path, monkeypatch):
    photo = _photo(tmp_path / "wide.jpg", size=(2400, 1350))
    calls = []
    _fake(monkeypatch, [("door", 0.5, 0.45), ("wall", 0.42, 0.55)], calls=calls)
    _, a = callouts.plan([photo], ["door", "wall"], "x", width=1080, height=1920)
    _, b = callouts.plan([photo], ["door", "wall"], "x", width=1920, height=1080)
    assert calls.count("CalloutPlan") == 3 and (tmp_path / callouts.LOCATE_CACHE).exists()   # 3 passes, once
    for pa, pb in zip(a, b):                                     # the same photo point on both frames
        ka, oxa, oya = callouts.cover_map((2400, 1350), (1080, 1920))
        kb, oxb, oyb = callouts.cover_map((2400, 1350), (1920, 1080))
        assert (pa[1] / ka + oxa, pa[2] / ka + oya) == pytest.approx((pb[1] / kb + oxb, pb[2] / kb + oyb))


@pytest.mark.parametrize("xy", [(5, 5), (1195, 2095), (600, 1050)])
def test_the_check_crop_is_padded_so_the_point_is_its_centre(xy):
    src = Image.new("RGB", (1200, 2100), (20, 160, 20))
    crop = np.asarray(callouts.verify_crop(src, xy[0], xy[1], 256), dtype=int)
    c = callouts.VERIFY_PX // 2
    assert crop.shape[:2] == (callouts.VERIFY_PX, callouts.VERIFY_PX)
    assert tuple(crop[c, c]) == pytest.approx((20, 160, 20), abs=6)          # the gap at the centre: the photo
    arm = c + callouts.CROSS_GAP + 4
    assert crop[c, arm, 0] > 200 and crop[c, arm, 1] < 80                    # the red crosshair's arm
    if xy == (5, 5):
        assert tuple(crop[10, 10]) == pytest.approx(callouts.VERIFY_PAD, abs=3)   # grey off the photo's corner
    if xy == (600, 1050):
        assert tuple(crop[10, 10]) == pytest.approx((20, 160, 20), abs=6)


def _src(p, photo_size=(1200, 2100)):
    """A 9:16 frame point as (label, x, y, w, h) fractions of the photo (the fake's features)."""
    k, ox, oy = callouts.cover_map(photo_size, (1080, 1920))
    return (p[0], (p[1] / k + ox) / photo_size[0], (p[2] / k + oy) / photo_size[1],
            p[4] / k / photo_size[0], p[5] / k / photo_size[1])


CHECK_PTS = [("door", 540, 900, 0.01, 100, 100), ("niche", 300, 700, 0.01, 100, 100)]


@pytest.mark.parametrize("kw,kept", [
    ({"same_site": False}, 0),
    ({"box": {"door": [0.0, 0.0, 0.05, 0.05], "niche": [0.0, 0.0, 0.05, 0.05]}}, 0),
    ({}, 2),
    ({"look": {"door": "window opening", "niche": "window opening"}}, 0),
])
def test_the_check_drops_on_another_site_a_disjoint_box_or_a_no(tmp_path, monkeypatch, kw, kept):
    photo = _photo(tmp_path / "p.jpg")
    _fake(monkeypatch, [_src(p) for p in CHECK_PTS], **kw)
    pts = CHECK_PTS
    out = callouts.verify(photo, pts, "x", crop_dir=tmp_path / "checks")
    assert len(out) == kept
    rec = json.loads((tmp_path / "checks" / "checks.json").read_text(encoding="utf-8"))
    assert rec and all(Path(r["crop"]).exists() for r in rec)


def test_an_extent_feature_gets_an_outline_and_no_loupe():
    pts = [("carved frieze", 540, 820, 0.01, 200, 60, "extent", (440, 790, 640, 850)),
           ("stone door recess", 300, 600, 0.02, 80, 80)]
    labs = callouts.layout_labels(pts, 1080, 1920)
    assert labs[0]["tier"] == "B" and not labs[0]["loupe"] and labs[0]["mbox"] is not None
    assert labs[1]["tier"] == "A"
    x0, y0, x1, y1 = labs[0]["mbox"]
    tx, ty = labs[0]["target"]
    assert (tx, ty) in [((x0 + x1) / 2, y0), ((x0 + x1) / 2, y1), (x0, (y0 + y1) / 2), (x1, (y0 + y1) / 2)]
    big = ("west facade", 540, 900, 0.5, 900, 1200, "extent", (90, 300, 990, 1500))
    assert callouts.tier(big, 1080, 1920) == "A"                 # larger than 35 % of the frame: a point marker


def _make_env(tmp_path, monkeypatch, feats):
    monkeypatch.setattr(config, "CALLOUTS_ENABLED", True)
    photo = _photo(tmp_path / "topic_1.jpg")
    _fake(monkeypatch, feats)
    seen = {}

    def fake_render(photo, points, out, width=1080, height=1920, reveal=None):
        seen.update(photo=photo, points=points, reveal=reveal, size=(width, height))
        out.write_bytes(b"clip")
        return out
    monkeypatch.setattr(callouts, "render", fake_render)
    return photo, seen


FEATS = [("door", 0.55, 0.45), ("niche", 0.3, 0.38)]


def test_the_audit_trail_is_written_and_replays_without_a_model(tmp_path, monkeypatch):
    _, seen = _make_env(tmp_path, monkeypatch, FEATS)
    clip = callouts.make({"photo_labels": ["door", "niche"]}, {"id": "t", "title": "x"},
                         [tmp_path / "topic_1.jpg"], tmp_path / "g", approved=None)
    assert clip is not None
    audit = json.loads((tmp_path / "g" / "graphic_labels.mp4.audit.json").read_text(encoding="utf-8"))
    assert audit["models"] == callouts.models() and audit["prompt_hashes"] == callouts.prompt_hashes()
    assert [lab["label"] for lab in audit["labels"]] == ["door", "niche"]
    assert all(lab["tier"] == "A" and "cue_method" in lab for lab in audit["labels"])
    assert audit["checks"] and all(Path(c["crop"]).exists() for c in audit["checks"])
    assert audit["location"]["plan"]["features"]
    cues = json.loads((tmp_path / "g" / "graphic_labels.mp4.cues.json").read_text(encoding="utf-8"))
    assert [c["method"] for c in cues["cues"]] == ["stagger", "stagger"]
    first = dict(seen)
    seen.clear()

    def no_model(*a, **k):
        raise AssertionError("replay called a model")
    monkeypatch.setattr(free_llm, "call", no_model)
    callouts.replay(tmp_path / "g" / "graphic_labels.mp4.audit.json", tmp_path / "again.mp4")
    assert [p[:3] for p in seen["points"]] == pytest.approx([p[:3] for p in first["points"]])
    assert seen["reveal"] == first["reveal"]


def test_auto_approve_without_a_passing_audit_gives_no_graphic(tmp_path, monkeypatch):
    _, seen = _make_env(tmp_path, monkeypatch, FEATS)
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    args = ({"photo_labels": ["door", "niche"]}, {"id": "t", "title": "x"}, [tmp_path / "topic_1.jpg"], tmp_path / "g")
    assert callouts.make(*args, approved=None, auto_approve=True) is None and not seen
    (tmp_path / "audit").mkdir()
    rec = {"pass": True, "models": callouts.models(), "prompt_hashes": callouts.prompt_hashes()}
    (tmp_path / "audit" / "callouts_latest.json").write_text(json.dumps(rec), encoding="utf-8")
    assert callouts.make(*args, approved=None, auto_approve=True) is not None
    stale = dict(rec, prompt_hashes={"locate": "0", "check": "0"})            # a prompt change voids the audit
    (tmp_path / "audit" / "callouts_latest.json").write_text(json.dumps(stale), encoding="utf-8")
    assert not callouts.audit_passes()


# ---------------------------------------------------------------- review 2026-11-03: the kind rule
def test_the_reference_916_pair_gets_a_clean_layout_with_the_wall_as_a_surface():
    # Operator, 2026-11-03 (replacing the test that locked in "no layout"): a real reference photo must not lose its
    # callout to the candidate grid. With the wall as the locator names a wall (a surface), the finer grid and the
    # surface's fallback from an outline to a point marker place both labels, the door's whole box and the wall's
    # inner half kept clear (every rule checked in test_callouts_v4's test_the_reference_916_pair_gets_a_clean_layout).
    w, h = 1080, 1920
    door, wall = P2_916
    kept = callouts.declutter(callouts.clear_of_captions([door, (*wall, "extent", (150, 820, 450, 1200))], w, h), w, h)
    assert kept is not None and [p[0] for p in kept] == ["stone door recess", "carved tuff wall"]
    labs = callouts.layout_labels(kept, w, h)
    assert not any(lab["conflict"] for lab in labs) and [lab["tier"] for lab in labs] == ["A", "A"]


def test_white_169_three_objects_keep_the_dome_clear_by_dropping_it():
    kept = callouts.declutter(WHITE_169, 1920, 1080)
    assert [p[0] for p in kept] == ["east lantern window", "door"]


def _ellipse_gap(box, cx, cy, a, b):
    nx = np.clip(0.0, box[0] - cx, box[2] - cx) / a
    ny = np.clip(0.0, box[1] - cy, box[3] - cy) / b
    return math.hypot(nx, ny)


@pytest.mark.parametrize("points,size", [(WHITE_169_SURFACE, (1920, 1080)), (EDGE_916, (1080, 1920)),
                                         (NICHE_916, (1080, 1920))])
def test_objects_stay_whole_and_surfaces_keep_their_inner_half_and_dot_clear(points, size):
    w, h = size
    fmt = _fmt(size)
    push = callouts.Push(w, h)
    labs = callouts.layout_labels(points, w, h, push=push)
    assert not any(lab["conflict"] for lab in labs)
    for t in callouts.SAMPLES:
        z = push.zoom(t)
        moved = []
        for lab in labs:
            sx, sy = push.shift(*lab["dot"], t)
            moved.append(([lab["box"][0] + sx, lab["box"][1] + sy, lab["box"][2] + sx, lab["box"][3] + sy],
                          [(x + sx, y + sy) for x, y in callouts._trim(lab["path"], lab["r_vis"])]))
        for j, p in enumerate(points):
            cx, cy = push.at(p[1], p[2], t)
            surface = callouts._kind(p) == "extent"
            a, b = p[4] / 2 * z, p[5] / 2 * z
            if surface:
                a, b = a * callouts.SURFACE_INNER, b * callouts.SURFACE_INNER
            for k, (box, path) in enumerate(moved):
                assert _ellipse_gap(box, cx, cy, a, b) >= 1 - 1e-6          # every plate, its own included
                if surface:
                    d = math.hypot(max(box[0] - cx, 0, cx - box[2]), max(box[1] - cy, 0, cy - box[3]))
                    assert d >= callouts.SURFACE_DOT[fmt] - 0.5
                if k != j:                                                   # no other label's leader enters it
                    for (px, py), (qx, qy) in pairwise(path):
                        for i in range(41):
                            x, y = px + (qx - px) * i / 40, py + (qy - py) * i / 40
                            assert math.hypot((x - cx) / a, (y - cy) / b) >= 1 - 1e-6
    # the surface's plate may sit over its outer part: here the dome's own plate is inside the dome's box
    if points is WHITE_169_SURFACE:
        fb, box = points[0][7], labs[0]["box"]
        assert fb[0] <= box[0] and box[2] <= fb[2] and fb[1] <= box[1] and box[3] <= fb[3]


# ---------------------------------------------------------------- review 2026-11-03: fail closed on contrast
def _weak_near(monkeypatch, dots):
    """nontext_contrast faked to read 2:1 for any mark nearest the first of `dots` (its label), else 9:1."""
    def fake(frame, mask, band, skip=0, exclude=None):
        m = np.asarray(mask) > 127
        if m.sum() < 8:
            return None
        ys, xs = np.nonzero(m)
        cx, cy = xs.mean(), ys.mean()
        near = min(range(len(dots)), key=lambda i: math.hypot(cx - dots[i][0], cy - dots[i][1]))
        return 2.0 if near == 0 else 9.0
    monkeypatch.setattr(callouts, "nontext_contrast", fake)


def test_a_label_under_3_to_1_after_every_guard_step_is_dropped(frames_of, monkeypatch):
    pts = P2_169                                              # three labels with a clean layout
    assert len(callouts.declutter(pts, 1920, 1080)) == 3
    _weak_near(monkeypatch, [(p[1], p[2]) for p in pts])
    _, rec = frames_of(pts, size=(1920, 1080), reveal=[0.6, 2.2, 3.4])
    assert [d["label"] for d in rec["dropped"]] == ["stone door recess"] and rec["dropped"][0]["ratio"] == 2.0
    assert [r["label"] for r in rec["layout"]] == ["carved tuff wall", "rock-cut stair"]
    assert all(r["ratio"] is None or r["ratio"] >= callouts.NONTEXT_MIN for r in rec["nontext"])


def test_fewer_than_two_labels_over_3_to_1_is_no_graphic(tmp_path, monkeypatch):
    monkeypatch.setattr(callouts, "_encode", lambda fr, out, n, **k: [None for _ in fr] and out)
    monkeypatch.setattr(callouts, "nontext_contrast", lambda *a, **k: 2.0)
    with pytest.raises(callouts.LowContrast):
        callouts.render(_photo(tmp_path / "p.jpg"), NICHE_916, tmp_path / "x.mp4", reveal=[0.6, 2.2])
    # make gives no graphic
    _make_env(tmp_path, monkeypatch, FEATS)

    def low(*a, **k):
        raise callouts.LowContrast("x")
    monkeypatch.setattr(callouts, "render", low)
    assert callouts.make({"photo_labels": ["door", "niche"]}, {"id": "t", "title": "x"},
                         [tmp_path / "topic_1.jpg"], tmp_path / "g", approved=None) is None


TIER_B = [("lime-plastered dome", 540, 760, 0.03, 300, 200, "extent", (390, 660, 690, 860)),
          ("door", 250, 1000, 0.01, 100, 160)]


def test_the_tier_b_outline_casing_follows_the_guard():
    labs = callouts.layout_labels(TIER_B, 1080, 1920)
    assert labs[0]["tier"] == "B" and not any(lab["conflict"] for lab in labs)
    art = callouts._Art(labs[0], "portrait", 1080, 1920)
    thin = np.asarray(art.lines(1.0, 1.0, 0.0, 2)[0], dtype=int).sum()       # the outline only (no leader yet)
    wide = np.asarray(art.lines(1.0, 1.0, 0.0, 3)[0], dtype=int).sum()
    assert wide > thin * 1.15


def _beige(path, size=(1200, 2100)):
    rng = np.random.default_rng(3)
    arr = np.clip(np.array([214, 190, 150]) + rng.normal(0, 6, (175, 100, 3)), 0, 255).astype(np.uint8)
    Image.fromarray(arr).resize(size, Image.BICUBIC).save(path)
    return path


def test_a_tier_b_outline_on_a_beige_wall_reads_gold_at_3_to_1(tmp_path, monkeypatch):
    frames, got = [], []
    monkeypatch.setattr(callouts, "_encode", lambda fr, out, n, **k: [frames.append(f) for f in fr] and out)
    real = callouts.layout_labels
    monkeypatch.setattr(callouts, "layout_labels", lambda *a, **k: got.append(real(*a, **k)) or got[-1])
    callouts.render(_beige(tmp_path / "beige.jpg"), TIER_B, tmp_path / "x.mp4", reveal=[0.6, 2.2])
    rec = json.loads((tmp_path / "x.mp4.contrast.json").read_text(encoding="utf-8"))
    assert [r["label"] for r in rec["layout"]] == ["lime-plastered dome", "door"] and not rec["dropped"]
    assert rec["layout"][0]["tier"] == "B"
    rows = {r["name"]: r for r in rec["nontext"]}
    assert rows["label_1_dot"]["ratio"] >= callouts.NONTEXT_MIN and rows["label_1_leader"]["ratio"] >= 3.0
    # gold, not brown: before the door lands the outline's centre line is the brand gold itself
    lab, push = got[0][0], callouts.Push(1080, 1920)
    x0, y0, x1, _ = lab["mbox"]
    t = 1.5
    sx, sy = push.shift(*lab["dot"], t)
    im = np.asarray(frames[round(t * config.VIDEO_FPS)], dtype=int)
    xs = [round(x0 + (x1 - x0) * f + sx) for f in (0.3, 0.7)]
    y = round(y0 + sy)
    for x in xs:
        col = im[y - 1:y + 2, x].reshape(-1, 3)
        assert np.abs(col - np.array(callouts.GOLD)).sum(axis=1).min() <= 12


def test_the_lift_shadow_never_darkens_another_labels_gold(frames_of, monkeypatch):
    # a huge, opaque shadow under every plate: any gold drawn under it would come out dark
    monkeypatch.setattr(callouts, "SHADOW_BLUR", 80)
    monkeypatch.setattr(callouts, "SHADOW_ALPHA", 255)
    got = []
    real = callouts.layout_labels
    monkeypatch.setattr(callouts, "layout_labels", lambda *a, **k: got.append(real(*a, **k)) or got[-1])
    frames, _ = frames_of(NICHE_916, reveal=[0.6, 2.2])
    labs, push = got[0], callouts.Push(1080, 1920)
    t = 3.0                                                   # both open, label 2 newest (full strength)
    im = np.asarray(frames[round(t * config.VIDEO_FPS)], dtype=int)
    lab = labs[1]
    sx, sy = push.shift(*lab["dot"], t)
    path = callouts._trim(lab["drawn"], lab["r_gold"])
    for f in (0.3, 0.5, 0.7):
        x, y = callouts._partial(path, f)[-1]
        x, y = round(x + sx), round(y + sy)
        px = im[y - 1:y + 2, x - 1:x + 2].reshape(-1, 3)
        assert np.abs(px - np.array(callouts.GOLD)).sum(axis=1).min() <= 6
    # and the shadow is really there: round the leader the photo is darker than with no shadow at all
    monkeypatch.setattr(callouts, "SHADOW_ALPHA", 0)
    plain, _ = frames_of(NICHE_916, reveal=[0.6, 2.2])
    bare = np.asarray(plain[round(t * config.VIDEO_FPS)], dtype=int)
    x, y = (round(v) for v in callouts._partial(path, 0.5)[-1])
    win = (slice(y + round(sy) - 30, y + round(sy) + 30), slice(x + round(sx) - 30, x + round(sx) + 30))
    assert im[win].mean() < bare[win].mean() - 10
