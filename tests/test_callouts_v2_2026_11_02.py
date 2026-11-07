"""Operator round 3 (2026-11-02): callout leaders meet their plate at an edge centre with one alignment rule; every
label is checked again on its own crop (fail closed); only approved topic photos are labelled; labels appear as
the narration names them; a small feature gets a loupe; a then-and-now is shown only for a pair that shares a
viewpoint (feature geometry and a vision check), registered so the wipe lines the two up."""
import json
import math

import numpy as np
import pytest
from PIL import Image, ImageFilter, ImageOps

from src import callouts, config, free_llm, then_now
from src.llm import LLMError
from tests import callout_fakes

POINTS_916 = [("stone door recess", 600, 900), ("carved tuff wall", 300, 1300), ("ventilation shaft", 1000, 420),
              ("rolling stone door", 70, 700)]
POINTS_4 = [("ventilation shaft", 820, 600), ("rolling stone door", 200, 800), ("carved tuff wall", 700, 1150),
            ("stable trough", 300, 1500)]
POINTS_169 = [("stone door recess", 1100, 500), ("carved tuff wall", 500, 750)]


def _photo(path, size=(1200, 2100), seed=1):
    rng = np.random.default_rng(seed)
    arr = rng.integers(60, 240, (size[1] // 12, size[0] // 12, 3), dtype=np.uint8)
    Image.fromarray(arr).resize(size, Image.BICUBIC).save(path)
    return path


# ---------------------------------------------------------------- leader attachment and alignment
@pytest.mark.parametrize("points,size", [(POINTS_916[:2], (1080, 1920)), (POINTS_916, (1080, 1920)),
                                         (POINTS_4, (1080, 1920)), (POINTS_169, (1920, 1080))])
def test_leader_meets_the_centre_of_a_plate_edge_never_a_corner(points, size):
    for lab in callouts.layout_labels(points, *size):
        x0, y0, x1, y1 = lab["box"]
        ex, ey = lab["end"]
        if lab["attach"] in ("top", "bottom"):
            assert abs(ex - (x0 + x1) / 2) <= 1 and ey == (y0 if lab["attach"] == "top" else y1)
        else:
            assert abs(ey - (y0 + y1) / 2) <= 1 and ex == (x0 if lab["attach"] == "left" else x1)


@pytest.mark.parametrize("points,size", [(POINTS_916, (1080, 1920)), (POINTS_4, (1080, 1920)),
                                         (POINTS_169, (1920, 1080))])
def test_one_alignment_rule_centred_on_top_bottom_flush_toward_a_side_leader(points, size):
    for lab in callouts.layout_labels(points, *size):
        x0, _, x1, _ = lab["box"]
        pad = callouts.PLATE_PAD["landscape" if size[0] > size[1] else "portrait"]   # 26 px [24 px]
        expected = {"top": ("mm", round((x0 + x1) / 2)), "bottom": ("mm", round((x0 + x1) / 2)),
                    "left": ("lm", x0 + pad), "right": ("rm", x1 - pad)}
        assert (lab["anchor"], lab["text_x"]) == expected[lab["attach"]]


def test_most_labels_attach_top_or_bottom_and_leaders_are_mostly_vertical():
    # Round 5 section 2 retired the side penalty ("top or bottom for most labels"): the direction from the plate to
    # the dot picks the edge, top/bottom within 40 degrees of vertical, a side within 30 degrees of horizontal.
    for points in (POINTS_916[:2], POINTS_4):
        labs = callouts.layout_labels(points, 1080, 1920)
        for (_, x, y), lab in zip(points, labs):
            x0, y0, x1, y1 = lab["box"]
            phi = math.degrees(math.atan2(abs(x - (x0 + x1) / 2), abs(y - (y0 + y1) / 2)))
            if lab["attach"] in ("top", "bottom"):
                assert phi < 60 and abs(lab["end"][0] - x) <= abs(lab["end"][1] - y)
            else:
                assert phi > 40


@pytest.mark.parametrize("points,size", [(POINTS_916, (1080, 1920)), (POINTS_4, (1080, 1920)),
                                         (POINTS_169, (1920, 1080))])
def test_plates_keep_24px_apart_and_off_every_dot_inside_the_text_box(points, size):
    bx0, by0, bx1, by1 = callouts.text_box(*size)
    labs = callouts.layout_labels(points, *size)
    for i, a in enumerate(labs):
        b = a["box"]
        assert bx0 <= b[0] and b[2] <= bx1 and by0 <= b[1] and b[3] <= by1
        for c in labs[i + 1:]:
            assert not callouts._overlaps(tuple(b), tuple(c["box"]), callouts.LABEL_GAP)
        for _, x, y in points:
            assert not callouts._overlaps(tuple(b), (x - 10, y - 10, x + 10, y + 10), 0)


def test_16x9_elbow_runs_vertical_into_the_plate_edge_centre():
    # Round 5 section 1 retired the 16:9 90-degree elbow: one leader style (S1 or S2) on both formats, still meeting
    # the centre of the plate edge.
    for lab, (_, x, y) in zip(callouts.layout_labels(POINTS_169, 1920, 1080), POINTS_169):
        assert lab["elbow"] is None and lab["route"] in ("S1", "S2") and not lab["conflict"]
        if lab["attach"] in ("top", "bottom"):
            assert abs(lab["end"][0] - (lab["box"][0] + lab["box"][2]) / 2) <= 1


# ---------------------------------------------------------------- accuracy: an independent check per label
def _fake_llm(monkeypatch, plan_points, verdicts, ready=True):
    """free_llm faked: the plan returns `plan_points` ((label, x, y, w, h) as fractions) on photo 1 on all three
    location passes; each label's check (round 5 section 9: blind location, blind look, match, decoy) passes for
    verdicts[label] True, its blind look sees something else for False, and every call about it raises an
    exception."""
    calls = []
    return callout_fakes.install(monkeypatch, plan_points, ready=ready, calls=calls,
                                 look={lb: "something else" for lb, v in verdicts.items() if v is False},
                                 fail={lb: v for lb, v in verdicts.items() if isinstance(v, Exception)})


# Object-sized features (operator 2026-11-03: an object keeps its whole extent clear of every plate, so the earlier
# 0.3 x 0.2 and 0.4 x 0.3 features, which overlap each other, leave no layout at all), the door a little higher and
# to the right (at 0.55, 0.45 the wall's plate has no room: above it the door's dot is nearer than its own)
PLAN = [("stone door recess", 0.6, 0.42, 0.08, 0.06), ("carved tuff wall", 0.3, 0.5, 0.08, 0.06),
        ("ventilation shaft", 0.7, 0.3, 0.08, 0.06)]
PAYLOAD = {"photo_labels": ["stone door recess", "carved tuff wall", "ventilation shaft"]}


@pytest.fixture
def rendered(monkeypatch):
    monkeypatch.setattr(config, "CALLOUTS_ENABLED", True)
    seen = {}

    def fake_render(photo, points, out, width=1080, height=1920, reveal=None):
        seen.update(points=points, reveal=reveal)
        out.write_bytes(b"clip")
        return out
    monkeypatch.setattr(callouts, "render", fake_render)
    return seen


def test_failing_verification_drops_that_label(tmp_path, monkeypatch, rendered):
    photo = _photo(tmp_path / "topic_1.jpg")
    calls = _fake_llm(monkeypatch, PLAN, {"stone door recess": True, "carved tuff wall": True, "ventilation shaft": False})
    clip = callouts.make(PAYLOAD, {"id": "t", "title": "Derinkuyu"}, [photo], tmp_path / "g", approved=None)
    assert clip is not None
    assert [p[0] for p in rendered["points"]] == ["stone door recess", "carved tuff wall"]
    assert calls.count("CheckLook") == 3                        # one independent (blind) look per proposed label


def test_unavailable_verifier_drops_every_label_and_no_graphic(tmp_path, monkeypatch, rendered):
    photo = _photo(tmp_path / "topic_1.jpg")
    err = LLMError("quota")
    _fake_llm(monkeypatch, PLAN, dict.fromkeys(PAYLOAD["photo_labels"], err))
    assert callouts.make(PAYLOAD, {"id": "t", "title": "Derinkuyu"}, [photo], tmp_path / "g", approved=None) is None
    assert "points" not in rendered


def test_one_passing_label_is_not_enough(tmp_path, monkeypatch, rendered):
    photo = _photo(tmp_path / "topic_1.jpg")
    _fake_llm(monkeypatch, PLAN, {"stone door recess": True, "carved tuff wall": False, "ventilation shaft": False})
    assert callouts.make(PAYLOAD, {"id": "t", "title": "Derinkuyu"}, [photo], tmp_path / "g", approved=None) is None


def test_two_passing_labels_render_with_their_cues(tmp_path, monkeypatch, rendered):
    photo = _photo(tmp_path / "topic_1.jpg")
    _fake_llm(monkeypatch, PLAN[:2], {"stone door recess": True, "carved tuff wall": True})
    clip = callouts.make(PAYLOAD, {"id": "t", "title": "Derinkuyu"}, [photo], tmp_path / "g", approved=None)
    assert clip == tmp_path / "g" / "graphic_labels.mp4" and len(rendered["points"]) == 2
    cues = json.loads((tmp_path / "g" / "graphic_labels.mp4.cues.json").read_text(encoding="utf-8"))
    assert cues["labels"] == ["stone door recess", "carved tuff wall"] and len(cues["reveal"]) == 2


def test_verify_crop_is_full_resolution_around_the_dot(tmp_path, monkeypatch):
    photo = _photo(tmp_path / "big.jpg", size=(2160, 3840))
    sizes = []
    pts = [("a", 540, 960, 0.01, 100, 100), ("b", 20, 1900, 0.01, 100, 100)]     # the box agreement needs a box
    callout_fakes.install(monkeypatch, [(p[0], p[1] * 2 / 2160, p[2] * 2 / 3840, 200 / 2160, 200 / 3840) for p in pts])
    fake = free_llm.call

    def call(system, content, schema, **kw):
        import base64
        import io
        if schema is callouts.CheckLook:                                         # the crosshair close-up
            sizes.append(Image.open(io.BytesIO(base64.b64decode(content[1]["source"]["data"]))).size)
        return fake(system, content, schema, **kw)
    monkeypatch.setattr(free_llm, "call", call)
    kept = callouts.verify(photo, pts, "x")
    assert len(kept) == 2 and sizes == [(callouts.VERIFY_PX, callouts.VERIFY_PX)] * 2


def test_medium_confidence_yes_is_a_no(tmp_path, monkeypatch):
    photo = _photo(tmp_path / "p.jpg")
    callout_fakes.install(monkeypatch, [("a", 0.5, 0.5)], confidence="medium")
    assert callouts.verify(photo, [("a", 540, 960)], "x") == []


def test_only_approved_topic_photos_are_labelled(tmp_path):
    a, b, c = tmp_path / "topic_1_balanced.jpg", tmp_path / "context_2.jpg", tmp_path / "clip.mp4"
    for p in (a, b):
        _photo(p, size=(60, 100))
    c.write_bytes(b"")
    assert callouts.approved_photos([a, b, c], {"id": "t"}, {"topic_1"}) == [a]
    assert callouts.approved_photos([a, b, c], {"id": "t"}, set()) == []          # none approved: none, no fallback
    assert callouts.approved_photos([a, b, c], {"id": "t"}, None) == [a, b]        # your own photos: all stills
    assert callouts.approved_photos([a, b], {}) == []                              # no topic folder to check


def test_no_approved_photo_means_no_plan_call(tmp_path, monkeypatch, rendered):
    photo = _photo(tmp_path / "context_1.jpg")
    calls = _fake_llm(monkeypatch, PLAN, dict.fromkeys(PAYLOAD["photo_labels"], True))
    assert callouts.make(PAYLOAD, {"id": "t", "title": "x"}, [photo], tmp_path / "g", approved={"topic_9"}) is None
    assert calls == []


# ---------------------------------------------------------------- reveal on the word, loupe
WORDS = [{"word": w, "start": 20.0 + t, "dur": 0.3} for w, t in (
    ("Past", 0.0), ("the", 0.3), ("stone", 0.6), ("door", 0.9), ("recess,", 1.2), ("rooms", 1.9), ("open", 2.2),
    ("into", 2.5), ("carved", 2.8), ("tuff", 3.1), ("wall.", 3.4))]
BEATS = [{"start": 0.0, "text": "An intro line."}, {"start": 20.0, "text": " ".join(w["word"] for w in WORDS)},
         {"start": 24.0, "text": "And on."}]


def test_labels_reveal_when_the_narration_names_them():
    anchor, t = callouts.reveal_times(["carved tuff wall", "stone door recess"], BEATS, WORDS)
    assert anchor == 1
    assert t[1] == pytest.approx(0.6) and t[0] == pytest.approx(2.8)   # "stone" at 20.6 s, "carved" at 22.8 s


def test_reveal_without_word_timings_estimates_from_the_beat_text():
    anchor, t = callouts.reveal_times(["stone door recess", "carved tuff wall"], BEATS)
    assert anchor == 1 and callouts.DOT_AT <= t[0] < t[1] <= callouts.REVEAL_LAST


def test_reveal_falls_back_to_the_stagger_and_stays_in_the_clip():
    # round 5 section 8: a label holds max(1.4 s, 0.06 s per character) before the next (was STAGGER alone)
    gap = max(callouts.STAGGER, callouts.hold("a door"))
    assert callouts.reveal_times(["a door", "a wall"], None) == (None, [callouts.DOT_AT, callouts.DOT_AT + gap])
    late = [{"start": 0.0, "text": "door " + "x " * 60 + "wall"}, {"start": 30.0, "text": "end"}]
    _, t = callouts.reveal_times(["door", "wall"], late)
    assert t[0] < t[1] <= callouts.REVEAL_LAST and t[1] - t[0] >= callouts.MIN_REVEAL_GAP - 1e-6


def test_small_feature_gets_a_loupe_and_its_plate_clears_it():
    pts = [("stone door recess", 600, 900, 0.01), ("carved tuff wall", 300, 1300, 0.2)]
    labs = callouts.layout_labels(pts, 1080, 1920)
    assert labs[0]["loupe"] and not labs[1]["loupe"]
    r = callouts._loupe_r(1080, 1920)
    assert not callouts._overlaps(tuple(labs[0]["box"]), (600 - r, 900 - r, 600 + r, 900 + r), 0)


def test_loupe_magnifies_inside_a_gold_ring():
    src = Image.new("RGB", (400, 400), (0, 0, 0))
    src.paste((255, 255, 255), (195, 195, 205, 205))          # a 10 px feature
    out = np.asarray(callouts._loupe(src.copy(), src, 200, 200, 60, 1.0), dtype=int)
    assert (out[200, 192:208] > 200).all() and (out[200, 145:180] < 40).all()   # 10 px -> ~22 px wide
    ring = out[200, 200 + 61]
    assert ring[0] > 150 and ring[2] < 120                     # gold at the rim


# ---------------------------------------------------------------- then-and-now viewpoint gate and registration
def _scene(path, seed=3, size=(1400, 900)):
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 255, (size[1] // 8, size[0] // 8), dtype=np.uint8)
    im = Image.fromarray(base).resize(size, Image.NEAREST).filter(ImageFilter.GaussianBlur(1.2)).convert("RGB")
    im.save(path)
    return path


def _old_of(src, path, angle=3.0, scale=1.1, shift=(60, 25)):
    im = Image.open(src).convert("L")
    w, h = im.size
    im = im.rotate(angle, resample=Image.BICUBIC).resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    im = im.crop((shift[0], shift[1], shift[0] + w, shift[1] + h))
    ImageOps.colorize(im, (40, 25, 10), (235, 215, 180)).save(path)
    return path


def test_geometry_matches_a_shared_viewpoint_and_rejects_another_scene(tmp_path):
    new = _scene(tmp_path / "new.jpg")
    old = _old_of(new, tmp_path / "old.jpg")
    geo = then_now.geometric_match(old, new)
    assert geo and geo["inliers"] >= then_now.MIN_INLIERS and geo["scale"] == pytest.approx(1.1, abs=0.05)
    assert then_now.geometric_match(_scene(tmp_path / "other.jpg", seed=9), new) is None


def _fake_view(monkeypatch, same=True, camera=True, conf="high", error=None):
    def call(system, content, schema, **kw):
        if error:
            raise error
        assert schema is then_now.ViewpointCheck
        return then_now.ViewpointCheck(same_structure=same, similar_camera_position=camera, confidence=conf, note="")
    monkeypatch.setattr(free_llm, "call", call)


@pytest.mark.parametrize("kw,ok", [({}, True), ({"camera": False}, False), ({"conf": "medium"}, False),
                                   ({"same": False}, False), ({"error": LLMError("down")}, False)])
def test_viewpoint_gate_needs_geometry_and_a_high_confidence_vision_yes(tmp_path, monkeypatch, kw, ok):
    new = _scene(tmp_path / "new.jpg")
    old = _old_of(new, tmp_path / "old.jpg")
    _fake_view(monkeypatch, **kw)
    assert (then_now.shares_viewpoint(old, new, "X") is not None) is ok


def test_viewpoint_gate_rejects_a_different_view_even_when_vision_says_yes(tmp_path, monkeypatch):
    _fake_view(monkeypatch)
    assert then_now.shares_viewpoint(_scene(tmp_path / "a.jpg", seed=4), _scene(tmp_path / "b.jpg", seed=5), "X") is None


def test_registration_lines_the_recent_photo_up_with_the_old(tmp_path):
    new = _scene(tmp_path / "new.jpg")
    old = _old_of(new, tmp_path / "old.jpg")
    geo = then_now.geometric_match(old, new)
    pair = then_now.register(old, new, geo["matrix"], tmp_path / "reg")
    assert pair is not None
    a = np.asarray(Image.open(pair[0]).convert("L"), dtype=float)
    b = np.asarray(Image.open(pair[1]).convert("L"), dtype=float)
    assert a.shape == b.shape and a.size >= then_now.MIN_SHARED_AREA * 1400 * 900
    raw_a = np.asarray(Image.open(old).convert("L"), dtype=float)
    raw_b = np.asarray(Image.open(new).convert("L"), dtype=float)
    assert np.corrcoef(a.ravel(), b.ravel())[0, 1] > 0.6 > np.corrcoef(raw_a.ravel(), raw_b.ravel())[0, 1]


def test_source_drops_a_pair_the_gate_rejects(tmp_path, monkeypatch):
    old_p, new_p = _scene(tmp_path / "o.jpg", seed=4), _scene(tmp_path / "n.jpg", seed=5)
    monkeypatch.setattr(free_llm, "ready", lambda: True)
    monkeypatch.setattr(then_now, "_candidates", lambda q, kw, old: [
        {"title": "File:Gate 1900.jpg", "year": 1900 if old else 2020, "download_url": "u"}])
    monkeypatch.setattr(then_now.commons, "download_jpeg", lambda url, path: Image.open(old_p if "then" in path.name else new_p).save(path))
    monkeypatch.setattr(then_now, "ensure_resolution", lambda p: p)
    monkeypatch.setattr(then_now, "_pick", lambda op, recents, place: then_now.PairPick(
        old_is_photograph=True, graphic_or_sensitive=False, best_match=1, confidence="high", note=""))
    _fake_view(monkeypatch)
    assert then_now.source({"location": {"name": "Gate Town", "country": "X"}}, tmp_path / "w") is None


def test_the_pipeline_passes_the_timed_words_to_the_callouts() -> None:
    """Labels reveal on the word that names them, so the render hands callouts.make the narration's words."""
    import inspect

    import main
    source = inspect.getsource(main.PipelineCoordinator._render_approve_publish)
    assert 'callouts.make, payload, topic, scene_media, run_dir / "graphics", words=words' in source
