"""Operator round 3 (2026-11-02): callout leaders meet their plate at an edge centre with one alignment rule; every
label is checked again on its own crop (fail closed); only approved topic photos are labelled; labels appear as
the narration names them; a small feature gets a loupe; a then-and-now is shown only for a pair that shares a
viewpoint (feature geometry and a vision check), registered so the wipe lines the two up."""
import json

import numpy as np
import pytest
from PIL import Image, ImageFilter, ImageOps

from src import callouts, config, free_llm, then_now
from src.llm import LLMError

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
        expected = {"top": ("mm", round((x0 + x1) / 2)), "bottom": ("mm", round((x0 + x1) / 2)),
                    "left": ("lm", x0 + callouts.PLATE_PAD_X), "right": ("rm", x1 - callouts.PLATE_PAD_X)}
        assert (lab["anchor"], lab["text_x"]) == expected[lab["attach"]]


def test_most_labels_attach_top_or_bottom_and_leaders_are_mostly_vertical():
    for points in (POINTS_916[:2], POINTS_4):
        labs = callouts.layout_labels(points, 1080, 1920)
        vertical = [lab for lab in labs if lab["attach"] in ("top", "bottom")]
        assert len(vertical) * 2 > len(labs)
        for (_, x, y), lab in zip(points, labs):
            if lab["attach"] in ("top", "bottom"):
                assert abs(lab["end"][0] - x) <= abs(lab["end"][1] - y)


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
    for lab, (_, x, y) in zip(callouts.layout_labels(POINTS_169, 1920, 1080), POINTS_169):
        assert lab["elbow"] is not None and lab["attach"] in ("top", "bottom")
        assert lab["elbow"] == (lab["end"][0], y) and abs(lab["end"][0] - (lab["box"][0] + lab["box"][2]) / 2) <= 1


# ---------------------------------------------------------------- accuracy: an independent check per label
def _fake_llm(monkeypatch, plan_points, verdicts, ready=True):
    """free_llm faked: the plan returns `plan_points` ((label, x, y) as fractions) on photo 1; each label's check
    returns verdicts[label] (True / False / an exception)."""
    calls = []
    monkeypatch.setattr(free_llm, "ready", lambda: ready)

    def call(system, content, schema, **kw):
        calls.append(schema.__name__)
        if schema is callouts.CalloutPlan:
            return callouts.CalloutPlan(image=1, features=[
                callouts.Located(label=lb, visible=True, x=x, y=y, w=w, h=h, confidence="high")
                for lb, x, y, w, h in plan_points])
        if schema is callouts.LabelCheck:
            text = " ".join(c.get("text", "") for c in content if c.get("type") == "text")
            label = text.split("Label: ")[1].split("\n")[0]
            v = verdicts[label]
            if isinstance(v, Exception):
                raise v
            return callouts.LabelCheck(shows_feature=v, confidence="high", note="")
        raise AssertionError(schema)
    monkeypatch.setattr(free_llm, "call", call)
    return calls


PLAN = [("stone door recess", 0.55, 0.45, 0.3, 0.2), ("carved tuff wall", 0.3, 0.5, 0.4, 0.3),
        ("ventilation shaft", 0.7, 0.3, 0.1, 0.1)]
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
    assert calls.count("LabelCheck") == 3                       # one independent check per proposed label


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
    monkeypatch.setattr(free_llm, "ready", lambda: True)

    def call(system, content, schema, **kw):
        import base64
        import io
        sizes.append(Image.open(io.BytesIO(base64.b64decode(content[1]["source"]["data"]))).size)
        return callouts.LabelCheck(shows_feature=True, confidence="high", note="")
    monkeypatch.setattr(free_llm, "call", call)
    kept = callouts.verify(photo, [("a", 540, 960), ("b", 20, 1900)], "x")
    assert len(kept) == 2 and sizes == [(callouts.VERIFY_PX, callouts.VERIFY_PX)] * 2


def test_medium_confidence_yes_is_a_no(tmp_path, monkeypatch):
    photo = _photo(tmp_path / "p.jpg")
    monkeypatch.setattr(free_llm, "ready", lambda: True)
    monkeypatch.setattr(free_llm, "call", lambda *a, **k: callouts.LabelCheck(shows_feature=True, confidence="medium", note=""))
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
    assert callouts.reveal_times(["a door", "a wall"], None) == (None, [callouts.DOT_AT, callouts.DOT_AT + callouts.STAGGER])
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
