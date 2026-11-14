"""Timeline v2 (2026-11-02, operator-approved brief) and the shared 9:16 inset edge: events on a true time scale,
a break mark on every gap drawn shorter than true scale, each block starting at its own marker and alternating
about the spine, the 16:9 timeline across the frame (the master lifts its lens vignette under graphics), a tracking
reveal that dims earlier events and restores them for the last frame, a darker and quieter photo ground, and every
9:16 graphic starting on one left edge at x 100."""
import json
import re
from itertools import combinations, pairwise
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from src import brand, graphics

DERINKUYU = [{"year": -700, "approximate": True, "label": "Possibly Phrygian tunnels"},
             {"year": 780, "approximate": True, "label": "Expanded against raiders"},
             {"year": 1923, "label": "Abandoned after the population exchange"},
             {"year": 1963, "label": "Rediscovered behind a wall"}]
EVEN = [{"year": 1900, "label": "Founded"}, {"year": 1920, "label": "Extended"}, {"year": 1960, "label": "Closed"}]
TIMESTAMP = (1620, 950, 1920, 1080)


def _photo(path: Path) -> Path:
    rng = np.random.default_rng(4)
    arr = np.clip(np.array([196, 150, 64], np.float32) + rng.normal(0, 30, (1200, 1600, 3)), 0, 255)
    Image.fromarray(arr.astype(np.uint8)).save(path)
    return path


def _render(monkeypatch: pytest.MonkeyPatch, job) -> dict[str, Any]:
    """One render at 4 fps with its frames, recorded plates, spacing and break marks captured."""
    state: dict[str, Any] = {"marks": [], "spacing": []}
    real_record, real_glyph, real_pos = graphics.record_contrast, graphics._break_glyph, graphics.timeline_positions

    def fake_encode(frames, path, n, width=1080, height=1920):
        state["frames"] = [f.convert("RGB") for f in frames]
        return path

    def spy_record(path, frame, plates):
        state["plates"] = [(name, p) for name, p in plates if p is not None and not p.empty]
        return real_record(path, frame, plates)

    def spy_glyph(d, x, y, *a, **k):
        state["marks"].append((len(state.get("frames_seen", [])), x, y))
        return real_glyph(d, x, y, *a, **k)

    def spy_pos(*a, **k):
        out = real_pos(*a, **k)
        state["spacing"].append(out)
        return out
    monkeypatch.setattr(graphics, "VIDEO_FPS", 4)
    monkeypatch.setattr(graphics, "_encode", fake_encode)
    monkeypatch.setattr(graphics, "record_contrast", spy_record)
    monkeypatch.setattr(graphics, "_break_glyph", spy_glyph)
    monkeypatch.setattr(graphics, "timeline_positions", spy_pos)
    clip = job()
    assert clip is not None
    state["record"] = json.loads(Path(str(clip) + graphics.CONTRAST_SIDECAR).read_text(encoding="utf-8"))
    return state


def _blocks(plates) -> dict[int, list[float]]:
    blocks: dict[int, list[float]] = {}
    for n, p in plates:
        m = re.match(r"(year|label)(\d)", n)
        if m:
            k = int(m.group(2))
            b = blocks.setdefault(k, list(p.box))
            blocks[k] = [min(b[0], p.box[0]), min(b[1], p.box[1]), max(b[2], p.box[2]), max(b[3], p.box[3])]
    return blocks


# ----------------------------------------------------------------------------- spacing
def test_spacing_is_proportional_to_elapsed_time_when_it_fits() -> None:
    sp = graphics.timeline_positions(EVEN, 100.0, 1300.0, 200.0)
    steps = [b - a for a, b in pairwise(sp.positions)]
    assert sp.breaks == ()
    assert steps[1] == pytest.approx(2 * steps[0])                 # 40 years twice as long as 20
    assert sp.positions[0] == 100.0 and sp.positions[-1] == pytest.approx(1300.0)


def test_compressed_gaps_are_listed_and_never_read_shorter_than_a_true_scale_gap() -> None:
    sp = graphics.timeline_positions(DERINKUYU, 160.0, 1400.0, [300.0, 300.0, 280.0])
    steps = [b - a for a, b in pairwise(sp.positions)]
    assert sp.breaks == (1, 2)                                     # 1,479 and 1,143 years are cut; 40 is not
    assert steps[2] == pytest.approx(280.0)                        # the true-scale gap at its least step
    assert min(steps[0], steps[1]) >= graphics.COMPRESSED_FLOOR * steps[2] - 1e-6
    assert steps[0] > steps[1]                                     # the order of lengths still holds
    assert steps[0] < 1479 * sp.px_per_year                        # and it is shorter than true scale
    assert sp.positions[-1] == pytest.approx(1400.0)


def test_each_gap_keeps_its_least_step() -> None:
    needs = [320.0, 260.0, 300.0]
    sp = graphics.timeline_positions(DERINKUYU, 0.0, 1400.0, needs)
    assert all(b - a >= nd - 1e-6 for (a, b), nd in zip(pairwise(sp.positions), needs))


def test_without_room_for_any_true_scale_gap_the_steps_are_even_and_marked() -> None:
    sp = graphics.timeline_positions(DERINKUYU, 0.0, 300.0, 150.0)
    assert [round(b - a) for a, b in pairwise(sp.positions)] == [100, 100, 100]
    assert sp.breaks == (1, 2)


# ----------------------------------------------------------------------------- reveal
def test_tracking_reveal_dims_earlier_events_and_restores_them() -> None:
    sch = graphics.reveal_schedule(4, 6.0)
    assert sch.at == sorted(sch.at) and sch.at[-1] < sch.restore < 6.0
    t = sch.at[2] + 0.6                                            # the third event is current
    assert sch.level(2, t) == pytest.approx(1.0, abs=0.01)
    assert sch.level(0, t) == pytest.approx(graphics.DIM_LEVEL, abs=0.02)
    assert sch.level(1, t) == pytest.approx(graphics.DIM_LEVEL, abs=0.05)
    assert sch.level(3, t) == 0.0                                  # not yet landed
    assert all(sch.level(k, 6.0) == pytest.approx(1.0) for k in range(4))
    xs = [0.0, 100.0, 200.0, 300.0]
    heads = [sch.head(xs, i / 10, 400.0) for i in range(61)]
    assert heads == sorted(heads) and heads[-1] == pytest.approx(400.0)   # the spine draws on, never back


def test_backdrop_is_darker_and_quieter() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        photo = _photo(Path(tmp) / "rock.jpg")
        new = np.asarray(graphics.timeline_backdrop(photo, 1080, 1920)).astype(np.float32)
        old = np.asarray(graphics._backdrop(photo, 1080, 1920)).astype(np.float32)
    assert new.mean() < old.mean() * 0.85
    def chroma(a: np.ndarray) -> float:
        return float((a.max(axis=2) - a.min(axis=2)).mean())
    assert chroma(new) < chroma(old) * 0.5


# ----------------------------------------------------------------------------- 16:9
@pytest.fixture
def wide(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    photo = _photo(tmp_path / "rock.jpg")
    return _render(monkeypatch, lambda: graphics.render_timeline(DERINKUYU, tmp_path / "t.mp4", width=1920,
                                                                 height=1080, photo=photo))


def test_wide_break_marks_are_drawn_on_the_compressed_gaps(wide) -> None:
    spacing = wide["spacing"][-1]
    assert spacing.breaks == (1, 2)
    xs = spacing.positions
    marked = {round(x) for _, x, _ in wide["marks"]}
    assert marked == {round((xs[k - 1] + xs[k]) / 2) for k in spacing.breaks}
    arr = np.asarray(wide["frames"][-1]).astype(np.int16)
    spine_y = {round(x): round(y) for _, x, y in wide["marks"]}  # round 4 review 7: the spine moves with its flags
    for k in spacing.breaks:                                       # a light slash crosses the spine there
        x = round((xs[k - 1] + xs[k]) / 2)
        y = spine_y[x]
        patch = arr[y - 15:y + 16, x - 16:x + 17]
        assert (np.abs(patch - np.array(brand.STONE_LT, np.int16)).max(axis=2) <= 40).sum() > 20


def test_wide_blocks_start_at_their_markers_and_alternate(wide) -> None:
    xs = wide["spacing"][-1].positions
    plates = {n: p for n, p in wide["plates"] if n.startswith("year")}
    for k, x in enumerate(xs):   # round 4: a circa year starts with its own lowercase "c." plate
        first = plates.get(f"year{k}_circa", plates[f"year{k}"])
        assert x + 10 <= first.box[0] <= x + 34, (k, x, first.box)
    for n, p in wide["plates"]:
        m = re.match(r"label(\d)", n)
        if m:
            first = plates.get(f"year{m.group(1)}_circa", plates[f"year{m.group(1)}"])   # round 4: "c." starts it
            assert abs(p.box[0] - first.box[0]) <= 8, n    # left-aligned with the year
    blocks = _blocks(wide["plates"])
    assert [(b[1] + b[3]) / 2 < 540 for _, b in sorted(blocks.items())] == [True, False, True, False]


def test_wide_nothing_collides(wide) -> None:
    boxes = [(n, p.box) for n, p in wide["plates"]]
    assert not [(a, b) for (a, ba), (b, bb) in combinations(boxes, 2)
                if graphics.LayoutConstraintSolver.boxes_intersect(ba, bb)]
    blocks = _blocks(wide["plates"])
    for a, b in combinations(sorted(blocks), 2):
        assert not graphics.LayoutConstraintSolver.boxes_intersect(tuple(blocks[a]), tuple(blocks[b]), margin=24)
    for n, p in wide["plates"]:
        if n.startswith("gap"):
            for b in blocks.values():
                assert not graphics.LayoutConstraintSolver.boxes_intersect(p.box, tuple(b), margin=24), n


def test_wide_timeline_spans_the_frame(wide) -> None:
    boxes = [p.box for _, p in wide["plates"]]
    assert min(b[0] for b in boxes) <= 200 and max(b[2] for b in boxes) >= 1500
    assert min(b[0] for b in boxes) >= 96 and max(b[2] for b in boxes) <= 1824          # 5 % title-safe
    assert min(b[1] for b in boxes) >= 54 and max(b[3] for b in boxes) <= 1026
    assert not any(graphics.LayoutConstraintSolver.boxes_intersect(b, TIMESTAMP) for b in boxes)
    # round 4 review 6: the spine is no longer always at y 540 (a flag lengthens so no span's "LATER" sits level
    # with a year, and the spine moves to keep both rows title-safe): its row is the one the gold runs along
    frame = np.asarray(wide["frames"][-1]).astype(np.int16)
    near = np.abs(frame - np.array(brand.GOLD_DIM, np.int16)).max(axis=2) <= 30
    spine_row = 380 + int(np.argmax(near[380:700, 170:1400].sum(axis=1)))
    gold = near[spine_row]
    assert gold[170:200].any() and gold[1400:].any()              # the spine runs from x 160 well past x 1400
    assert all(e["drawn"] >= brand.TEXT_CONTRAST for e in wide["record"]["elements"])


def test_wide_mid_clip_dims_the_earlier_event(wide) -> None:
    sch = graphics.reveal_schedule(4, graphics.CLIP_SECONDS)
    frame = wide["frames"][int((sch.at[1] + 0.7) * 4)]
    year0 = dict(wide["plates"])["year0"].box
    year1 = dict(wide["plates"])["year1"].box
    arr = np.asarray(frame).astype(np.float32)

    def peak(b):
        return float(np.percentile(arr[b[1]:b[3], b[0]:b[2]].max(axis=2), 99))
    assert peak(year0) < peak(year1) * 0.75                       # the earlier date dimmed, the current one full


# ----------------------------------------------------------------------------- 9:16 and the inset edge
def test_tall_timeline_marks_breaks_and_sits_on_the_inset_edge(tmp_path, monkeypatch) -> None:
    photo = _photo(tmp_path / "rock.jpg")
    st = _render(monkeypatch, lambda: graphics.render_timeline(DERINKUYU, tmp_path / "t.mp4", photo=photo))
    assert st["spacing"][-1].breaks and st["marks"]
    arr = np.asarray(st["frames"][-1]).astype(np.int16)
    gold = np.abs(arr - np.array(brand.GOLD, np.int16)).max(axis=2) <= 40
    cols = np.nonzero(gold[300:1100].any(axis=0))[0]
    assert abs(int(cols.min()) - graphics.INSET_X0) <= 3          # the dots' left edge on x 100
    boxes = [p.box for _, p in st["plates"]]
    assert max(b[2] for b in boxes) <= graphics.TEXT_RIGHT
    assert not [(a, b) for (a, ba), (b, bb) in combinations([(n, p.box) for n, p in st["plates"]], 2)
                if graphics.LayoutConstraintSolver.boxes_intersect(ba, bb)]


@pytest.mark.parametrize("name", ["scale_depth", "scale_height", "section", "numbers"])
def test_vertical_graphics_share_the_inset_text_edge(tmp_path, monkeypatch, name) -> None:
    photo = _photo(tmp_path / "rock.jpg")
    section = {"subject": "Derinkuyu underground city", "total_depth_ft": 280.0, "approximate": True,
               "levels": [{"depth_ft": 15.0, "label": "Stables and storage"}, {"depth_ft": 35.0, "label": "Living quarters"},
                          {"depth_ft": 140.0, "label": "Cruciform church"}, {"depth_ft": 280.0, "label": "Deepest wells"}]}
    jobs = {"scale_depth": lambda: graphics.render_scale({"value": 280.0, "unit": "ft", "dimension": "depth",
                                                          "subject": "Derinkuyu depth", "approximate": True},
                                                         tmp_path / "s.mp4"),
            "scale_height": lambda: graphics.render_scale({"value": 32.0, "unit": "m", "dimension": "height",
                                                           "subject": "Kailasa temple", "approximate": True},
                                                          tmp_path / "h.mp4"),
            "section": lambda: graphics.render_cross_section(section, tmp_path / "x.mp4"),
            "numbers": lambda: graphics.render_stat_cards([{"value": "1,200,000 TONS", "label": "Of stone"}],
                                                          tmp_path / "n.mp4", photo)}
    st = _render(monkeypatch, jobs[name])
    boxes = [p.box for _, p in st["plates"]]
    left = min(b[0] for b in boxes)
    assert graphics.INSET_X0 - 2 <= left <= graphics.INSET_X0 + 4, (name, left)
    x0, y0, x1, y1 = graphics.get_text_box(1080, 1920)
    assert all(x0 <= b[0] and b[2] <= x1 and y0 <= b[1] and b[3] <= y1 for b in boxes), name
