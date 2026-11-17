"""Callout review 2026-11-03: the kind rule on the located box (an off-centre dot: a tower's crown, a portal's
lintel), the reference 9:16 pair and the large surface laid out through the finer grid and a surface's fallback to
a point marker, the round-5 section 9 drop paths, tiers that agree across the two cuts, the models that actually
served in the audit (a collapsed check fails closed), and declutter's spoken-order tie."""
import json
import math
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from src import callouts, config, free_llm, llm
from tests import callout_fakes


def _photo(path, size=(1200, 2100), seed=1, lo=60, hi=240):
    rng = np.random.default_rng(seed)
    arr = rng.integers(lo, hi, (size[1] // 12, size[0] // 12, 3), dtype=np.uint8)
    Image.fromarray(arr).resize(size, Image.BICUBIC).save(path)
    return path


def _pt(label, x, y, fw, fh, box, kind="point", size=(1080, 1920)):
    return (label, x, y, fw * fh / (size[0] * size[1]), fw, fh, kind, box)


L169 = (1920, 1080)
# Objects whose pointable point sits near the top of a tall box (the locator's x, y is "a point on the feature's
# surface"; its box is given separately).
OFF_916 = [_pt("north tower", 300, 620, 160, 420, (220, 560, 380, 980)),
           _pt("west portal", 780, 700, 200, 300, (680, 650, 880, 950)),
           _pt("statue niche", 560, 1150, 120, 160, (500, 1070, 620, 1230))]
OFF_169 = [_pt("north tower", 600, 330, 160, 420, (520, 270, 680, 690), size=L169),
           _pt("west portal", 1300, 420, 220, 300, (1190, 370, 1410, 670), size=L169),
           _pt("statue niche", 960, 700, 120, 160, (900, 640, 1020, 800), size=L169)]
# The reference 9:16 pair, the wall as the locator names a wall: a surface (kind "extent").
REF_DOOR = _pt("stone door recess", 600, 900, 260, 300, (470, 750, 730, 1050))
REF_WALL = _pt("carved tuff wall", 300, 1010, 300, 380, (150, 820, 450, 1200), "extent")
# A dome covering about 30 % of the 9:16 frame, with two objects on its outer part.
DOME_916 = [_pt("lime-plastered dome", 540, 700, 900, 700, (90, 350, 990, 1050), "extent"),
            _pt("east lantern window", 820, 980, 110, 150, (765, 905, 875, 1055)),
            _pt("door", 260, 1080, 120, 200, (200, 980, 320, 1180))]


def _box_gap(a, b):
    """The gap between two boxes, 0 when they touch or overlap."""
    return math.hypot(max(b[0] - a[2], a[0] - b[2], 0.0), max(b[1] - a[3], a[1] - b[3], 0.0))


def _seg_hits_box(p, q, b, n=60):
    return any(b[0] < p[0] + (q[0] - p[0]) * i / n < b[2] and b[1] < p[1] + (q[1] - p[1]) * i / n < b[3]
               for i in range(n + 1))


def _check_rules(points, size, labs):
    """At every sampled t: no plate (or loupe unit) covers any part of any object's located box, its own or another's;
    no leader enters another object's box; a point-marked surface keeps its inner half (on its box, centred on the
    box) and SURFACE_DOT px round its dot clear of every plate, and other leaders out of that inner half."""
    w, h = size
    fmt = "landscape" if w > h else "portrait"
    push = callouts.Push(w, h)
    assert not any(lab["conflict"] for lab in labs)
    for t in callouts.SAMPLES:
        z = push.zoom(t)
        moved = []
        for lab in labs:
            sx, sy = push.shift(*lab["dot"], t)
            moved.append(([lab["unit"][0] + sx, lab["unit"][1] + sy, lab["unit"][2] + sx, lab["unit"][3] + sy],
                          [(x + sx, y + sy) for x, y in callouts._trim(lab["path"], lab["r_vis"])]))
        for j, (p, lab) in enumerate(zip(points, labs)):
            b = p[7]
            zb = [w / 2 + (b[0] - w / 2) * z, h / 2 + (b[1] - h / 2) * z, w / 2 + (b[2] - w / 2) * z,
                  h / 2 + (b[3] - h / 2) * z]
            surface_marker = callouts._kind(p) == "extent" and lab["tier"] == "A"
            for k, (unit, path) in enumerate(moved):
                if not surface_marker:
                    assert _box_gap(unit, zb) > 0, (p[0], labs[k]["lines"], t)         # never on an object's box
                    if k != j:
                        for a, c in pairwise(path):
                            assert not _seg_hits_box(a, c, zb), (labs[k]["lines"], p[0], t)
                else:
                    cx, cy = (zb[0] + zb[2]) / 2, (zb[1] + zb[3]) / 2
                    ea, eb = (zb[2] - zb[0]) / 2 * callouts.SURFACE_INNER, (zb[3] - zb[1]) / 2 * callouts.SURFACE_INNER
                    nx = max(unit[0] - cx, 0, cx - unit[2]) / ea
                    ny = max(unit[1] - cy, 0, cy - unit[3]) / eb
                    assert math.hypot(nx, ny) >= 1 - 1e-6                               # not in the inner half
                    dx, dy = push.at(p[1], p[2], t)
                    d = math.hypot(max(unit[0] - dx, 0, dx - unit[2]), max(unit[1] - dy, 0, dy - unit[3]))
                    assert d >= callouts.SURFACE_DOT[fmt] - 0.5
                    if k != j:
                        for a, c in pairwise(path):
                            for i in range(61):
                                x, y = a[0] + (c[0] - a[0]) * i / 60, a[1] + (c[1] - a[1]) * i / 60
                                assert math.hypot((x - cx) / ea, (y - cy) / eb) >= 1 - 1e-6


# ---------------------------------------------------------------- 1. the kind rule on the located box
@pytest.mark.parametrize("points,size,loupe", [(OFF_916, (1080, 1920), False), (OFF_169, L169, False),
                                               (OFF_169, L169, True)])
def test_an_off_centre_dot_keeps_its_objects_whole_box_clear(points, size, loupe):
    kept = callouts.declutter(points, *size, loupe=loupe, mag=2.0 if loupe else None)
    assert kept and len(kept) >= 2
    labs = callouts.layout_labels(kept, *size, loupe=loupe, mag=2.0 if loupe else None)
    _check_rules(kept, size, labs)


@pytest.mark.parametrize("size", [(1080, 1920), L169])
def test_a_plate_on_the_lower_part_of_a_crown_dot_tower_is_refused(size):
    # The dot at the tower's crown; a plate just under the dot sits on the tower's box although it is well clear of
    # a w/2 x h/2 ellipse round the dot: the layout must refuse it.
    w, h = size
    tower = _pt("north tower", w * 0.3, h * 0.35, 160, 420, (w * 0.3 - 80, h * 0.35 - 60, w * 0.3 + 80, h * 0.35 + 360),
                size=size)
    portal = _pt("west portal", w * 0.72, h * 0.36, 200, 300, (w * 0.72 - 100, h * 0.36 - 50, w * 0.72 + 100,
                                                               h * 0.36 + 250), size=size)
    fmt = "landscape" if w > h else "portrait"
    feats = [callouts._Feat(i, p, w, h, fmt, False, 50) for i, p in enumerate([tower, portal])]
    ctx = callouts._Ctx(feats, w, h, callouts.text_box(w, h), fmt)
    on_box = (tower[7][0] - 40, tower[7][3] - 120, tower[7][0] + 200, tower[7][3] - 40)   # over its lower part
    off_ellipse = math.hypot(max(on_box[0] - tower[1], 0, tower[1] - on_box[2]) / 80,
                             max(on_box[1] - tower[2], 0, tower[2] - on_box[3]) / 210)
    assert off_ellipse >= 1.0                       # the old dot-centred ellipse would have let it through
    U = np.array([on_box], dtype=float)
    assert not ctx.plate_clear(U, feats[0], 0, tower[1], tower[2])[0]
    labs = callouts.layout_labels([tower, portal], w, h)
    _check_rules([tower, portal], size, labs)


# ---------------------------------------------------------------- 2. the reference pair and the large surface
def test_the_reference_916_pair_gets_a_clean_layout():
    # Operator, 2026-11-03: a real reference photo must not lose its callout to the candidate grid. The wall (a
    # surface) cannot keep its outline's whole box clear, so it takes a point marker; the door's whole box, the
    # wall's inner half and the 40 px round its dot stay clear of every plate, and no leader enters them.
    w, h = 1080, 1920
    assert callouts.declutter([REF_DOOR, REF_WALL], w, h) is not None     # the layout alone moves the wall
    # as make runs it: the wall's outline would reach the caption band, so the caption check moves it first
    clear = callouts.clear_of_captions([REF_DOOR, REF_WALL], w, h)
    assert [p[0] for p in clear] == ["stone door recess", "carved tuff wall"]
    assert callouts.tier(clear[1], w, h) == "A"
    kept = callouts.declutter(clear, w, h)
    assert kept is not None and [p[0] for p in kept] == ["stone door recess", "carved tuff wall"]
    assert [callouts.tier(p, w, h) for p in kept] == ["A", "A"]
    assert callouts.trust(kept[1]).get("marker") == "A"
    labs = callouts.layout_labels(kept, w, h)
    assert [lab["tier"] for lab in labs] == ["A", "A"] and all(lab["mbox"] is None for lab in labs)
    _check_rules(kept, (w, h), labs)


def test_only_a_surface_on_tier_a_evidence_falls_back_to_a_point_marker():
    w, h = 1080, 1920
    loose = REF_WALL + ({"tier": "B", "evidence_tier": "B", "spread_px": 40},)   # an outline for a loose location
    assert callouts.tier(callouts.as_marker(loose), w, h) == "B"
    assert callouts.declutter([REF_DOOR, loose], w, h) is None
    door_b = REF_DOOR + ({"tier": "B", "evidence_tier": "B", "spread_px": 40},)  # an object never moves
    assert len(callouts._marker_variants([door_b, REF_WALL], w, h)) == 2


def test_the_large_dome_916_gets_a_graphic_with_every_rule_kept():
    w, h = 1080, 1920
    assert callouts.tier(DOME_916[0], w, h) == "B"             # about 30 % of the frame: an outline by default
    assert callouts.declutter(DOME_916, w, h) is not None
    # as make runs it. QF-24: the door (y 1080) no longer reaches the caption band (y 1130 on the organic box, 1098
    # on the ads box), so all three pass; the three have no layout, and the cheapest valid pair keeps the door
    clear = callouts.clear_of_captions(DOME_916, w, h)
    assert [p[0] for p in clear] == ["lime-plastered dome", "east lantern window", "door"]
    kept = callouts.declutter(clear, w, h)
    assert kept is not None and [p[0] for p in kept] == ["lime-plastered dome", "door"]
    assert callouts.tier(kept[0], w, h) == "A"                  # the outline cannot be laid out: a point marker
    labs = callouts.layout_labels(kept, w, h)
    _check_rules(kept, (w, h), labs)


# ---------------------------------------------------------------- 3. the section 9 drop paths
def _src(p, photo_size=(1200, 2100), size=(1080, 1920)):
    k, ox, oy = callouts.cover_map(photo_size, size)
    return (p[0], (p[1] / k + ox) / photo_size[0], (p[2] / k + oy) / photo_size[1],
            p[4] / k / photo_size[0], p[5] / k / photo_size[1])


CHECK_PTS = [("door", 540, 900, 0.01, 100, 100), ("niche", 300, 700, 0.01, 100, 100)]


def _checks(tmp_path):
    return {r["label"]: r for r in json.loads((tmp_path / "checks" / "checks.json").read_text(encoding="utf-8"))}


def test_a_decoy_answered_yes_drops_the_label(tmp_path, monkeypatch):
    photo = _photo(tmp_path / "p.jpg")
    callout_fakes.install(monkeypatch, [_src(p) for p in CHECK_PTS], decoy={"door": True})
    out = callouts.verify(photo, CHECK_PTS, "x", crop_dir=tmp_path / "checks")
    assert [p[0] for p in out] == ["niche"]
    rec = _checks(tmp_path)
    assert rec["door"]["result"] == "dropped: the decoy point was also called the feature"
    assert rec["door"]["decoy"]["shows_feature"] is True and Path(rec["door"]["decoy_crop"]).exists()


def test_a_missing_box_drops_the_label(tmp_path, monkeypatch):
    photo = _photo(tmp_path / "p.jpg")
    callout_fakes.install(monkeypatch, [_src(p) for p in CHECK_PTS], box={"niche": None})
    out = callouts.verify(photo, CHECK_PTS, "x", crop_dir=tmp_path / "checks")
    assert [p[0] for p in out] == ["door"]
    rec = _checks(tmp_path)
    assert rec["niche"]["iou"] is None and rec["niche"]["result"] == "dropped: the check returned no box"


SPREAD_FEATS = [("door", 0.5, 0.45, 0.2, 0.15), ("wall", 0.42, 0.55, 0.2, 0.15)]


def _planned(tmp_path, monkeypatch, dx, size):
    photo = _photo(tmp_path / "tall.jpg")                       # a portrait photo, 1200 x 2100
    callout_fakes.install(monkeypatch, SPREAD_FEATS, passes={2: {"door": (0.5 + dx, 0.45)}})
    found = callouts.plan([photo], ["door", "wall"], "x", width=size[0], height=size[1])
    return {p[0]: p for p in found[1]} if found else {}


def test_a_spread_over_the_limit_drops_the_label(tmp_path, monkeypatch):
    # 0.06 of 1200 px = 72 photo px, 65.8 at the 1920 px reference: over the limit (0.25 x 240 px x 0.914 = 54.9)
    got = _planned(tmp_path, monkeypatch, 0.06, (1080, 1920))
    assert got == {}                                            # the door dropped, one label left: no graphic


def test_a_spread_over_24_px_makes_the_label_tier_b(tmp_path, monkeypatch):
    got = _planned(tmp_path, monkeypatch, 0.03, (1080, 1920))     # 36 photo px: 32.9 at the reference
    door = got["door"]
    assert callouts.trust(door)["tier"] == "B" and callouts.trust(door)["spread_px"] == pytest.approx(32.9, abs=0.1)
    assert callouts.tier(door, 1080, 1920) == "B" and callouts.tier(got["wall"], 1080, 1920) == "A"


# ---------------------------------------------------------------- 4. the same tier on both cuts
@pytest.mark.parametrize("dx", [0.0, 0.02, 0.03])
def test_a_label_gets_the_same_tier_and_spread_on_the_916_and_169_cuts(tmp_path, monkeypatch, dx):
    # 0.02 of a portrait photo's width is 21.9 px at the reference scale: tier A. In frame px it was 21.9 on the
    # Reel but 38.4 on the 16:9 crop (k = 1.6): tier B on YouTube only.
    a = _planned(tmp_path, monkeypatch, dx, (1080, 1920))
    b = _planned(tmp_path, monkeypatch, dx, L169)
    for label in ("door", "wall"):
        ta, tb = callouts.trust(a[label]), callouts.trust(b[label])
        assert ta["spread_px"] == pytest.approx(tb["spread_px"]) and ta["tier"] == tb["tier"]
    assert callouts.trust(a["door"])["tier"] == ("B" if dx >= 0.03 else "A")


# ---------------------------------------------------------------- 5. the models that served
def _make_env(tmp_path, monkeypatch, **kw):
    monkeypatch.setattr(config, "CALLOUTS_ENABLED", True)
    _photo(tmp_path / "topic_1.jpg")
    callout_fakes.install(monkeypatch, [("door", 0.55, 0.45), ("niche", 0.3, 0.38)], **kw)
    seen = {}

    def fake_render(photo, points, out, width=1080, height=1920, reveal=None):
        seen.update(points=points, reveal=reveal)
        out.write_bytes(b"clip")
        return out
    monkeypatch.setattr(callouts, "render", fake_render)
    return seen


def _make(tmp_path, payload=None):
    return callouts.make(payload or {"photo_labels": ["door", "niche"]}, {"id": "t", "title": "x"},
                         [tmp_path / "topic_1.jpg"], tmp_path / "g", approved=None)


def _audit(tmp_path):
    return json.loads((tmp_path / "g" / "graphic_labels.mp4.audit.json").read_text(encoding="utf-8"))


def test_the_audit_records_three_location_passes_and_distinct_served_models(tmp_path, monkeypatch):
    _make_env(tmp_path, monkeypatch)
    assert _make(tmp_path) is not None
    audit = _audit(tmp_path)
    assert audit["location_passes"] == 3
    assert audit["models_served"] == {"locate": [callouts.models()["locate"]],
                                      "check": sorted({callouts.models()["check"], callouts.models()["match"]})}
    assert audit["models_distinct"] is True and audit["checks_collapsed"] == []


def test_the_audit_records_the_model_that_served_not_the_configured_one(tmp_path, monkeypatch):
    # The free engine's Claude last resort answered the checks: the audit names it.
    _make_env(tmp_path, monkeypatch, served={"check": "claude-sonnet-5-5", "match": "claude-sonnet-5-5"})
    assert _make(tmp_path) is not None
    audit = _audit(tmp_path)
    assert audit["models_served"]["check"] == ["claude-sonnet-5-5"] and audit["models_distinct"] is True
    assert all(c["served"] == {"locate": "claude-sonnet-5-5", "look": "claude-sonnet-5-5",
                               "match": "claude-sonnet-5-5", "decoy": "claude-sonnet-5-5"} for c in audit["checks"])


@pytest.mark.parametrize("served", [
    {"locate": "claude-haiku-5-5", "check": "claude-haiku-5-5", "match": "claude-haiku-5-5"},   # a budget swap
    {"check": None},                                                                            # not known
])
def test_a_check_that_collapses_onto_the_locator_or_is_unknown_fails_closed(tmp_path, monkeypatch, served):
    seen = _make_env(tmp_path, monkeypatch, served=served)
    assert _make(tmp_path) is None and not seen
    checks = json.loads((tmp_path / "g" / "callout_checks" / "checks.json").read_text(encoding="utf-8"))
    assert len(checks) == 2 and all(c["result"].startswith("dropped: ") for c in checks)
    if served.get("check"):
        assert all(c["collapsed"] and "locator's model claude-haiku-5-5" in c["result"] for c in checks)
    else:
        assert all("is not known" in c["result"] for c in checks)


def test_a_claude_answer_notes_the_model_the_response_names(monkeypatch):
    # A refusal fallback or a budget swap: the response's model, not the one asked for.
    class Answer(callouts.LabelMatch):
        pass
    resp = SimpleNamespace(model="claude-sonnet-5-5", parsed_output=Answer(same_feature=True, confidence="high"),
                           stop_reason="end_turn", usage=None, _request_id="r", stop_details=None)
    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=lambda **kw: resp)))
    monkeypatch.setattr(llm, "_client", lambda: client)
    with llm.watch_served() as got:
        llm.structured_call("s", "p", Answer, models=["claude-opus-5-5"])
    assert got == ["claude-sonnet-5-5"]
    llm.note_served("outside")                                       # outside a watch: nothing is kept
    assert got == ["claude-sonnet-5-5"]


def test_a_free_engine_answer_notes_the_gemini_model(monkeypatch):
    monkeypatch.setattr(free_llm, "_structured_on", lambda *a, **k: callouts.LabelMatch(same_feature=True,
                                                                                         confidence="high"))
    with llm.watch_served() as got:
        free_llm.structured("s", [{"type": "text", "text": "p"}], callouts.LabelMatch, role="checker")
    assert got == [config.FREE_CHECKER_MODEL]


# ---------------------------------------------------------------- 6. declutter's spoken-order tie
def test_declutter_breaks_a_cost_tie_in_spoken_order(monkeypatch):
    pts = [("a", 100, 100), ("b", 200, 200), ("c", 300, 300)]

    # QF-24: declutter passes layout_labels step_down=False on its full-size pass (the stepped search runs after)
    def fake_layout(points, width, height, loupe=True, push=None, ground=None, mag=None, spoken=None, step_down=True):
        return [{"conflict": len(points) == 3, "set_cost": 1.0} for _ in points]
    monkeypatch.setattr(callouts, "layout_labels", fake_layout)
    assert [p[0] for p in callouts.declutter(pts, 1080, 1920)] == ["a", "b"]
    assert [p[0] for p in callouts.declutter(pts, 1080, 1920, spoken=[2, 1, 0])] == ["b", "c"]
    assert [p[0] for p in callouts.declutter(pts, 1080, 1920, spoken=[2, 0, 1])] == ["a", "c"]


def test_make_passes_the_spoken_order_to_declutter(tmp_path, monkeypatch):
    _make_env(tmp_path, monkeypatch)
    got = {}
    real = callouts.declutter

    def spy(points, *a, **kw):
        got["labels"], got["spoken"] = [p[0] for p in points], kw.get("spoken")
        return real(points, *a, **kw)
    monkeypatch.setattr(callouts, "declutter", spy)
    payload = {"photo_labels": ["door", "niche"],
               "beat_times": [{"start": 0.0, "text": "the niche and the door"},
                              {"start": 2.0, "text": "then it ends"}]}
    assert _make(tmp_path, payload) is not None
    assert got["labels"] == ["door", "niche"] and got["spoken"] == [1, 0]


def test_render_accepts_the_reference_916_pair(tmp_path, monkeypatch):
    # render refuses a layout in conflict (ValueError): the pair as declutter returns it renders on the real push.
    monkeypatch.setattr(callouts, "_encode", lambda frames, out, n, **k: out)
    photo = _photo(tmp_path / "p.jpg")
    kept = callouts.declutter(callouts.clear_of_captions([REF_DOOR, REF_WALL], 1080, 1920), 1080, 1920)
    assert callouts.render(photo, kept, tmp_path / "x.mp4", reveal=[0.6, 2.2]) == tmp_path / "x.mp4"
