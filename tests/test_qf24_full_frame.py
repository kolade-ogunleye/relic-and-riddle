"""QF-24: 9:16 figures use the full frame inside the shared organic Reel safe rectangle (project quality review
2026-11-05, section 3: "the lower half of every 9:16 graphic is empty because the 35% bottom inset is Meta's ads
guidance, not organic UI"). Decision QF-24/QF-87 layout (2026-11-09): the Reel box is (113, 288)-(848, 1370) at
1080 x 1920, 240 px of it are reserved for captions (the 20 px clearance included), the graphics stop the existing
40 px gap above that band (y 1090), and the captions keep their reviewed 640 px measure, centred in the box.

The copy below is real dev-run copy (number cards, the scale fact; docs/reviews/quality-fixes-2026-11/QF-23/
real_inputs.json); the cross-section and timeline copy are the reviewed fixtures of tests/test_r4_graphics_2026_11_02.py
(no dev run carries a cross-section)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

from src import graphics, safe_zones
from src import video_composer as vc
from src.config import (
    CAPTION_MARGIN_V,
    CAPTION_REEL_CLEARANCE,
    VIDEO_HEIGHT,
    VIDEO_WIDTH,
)
from src.video_qa import caption_pills

W, H = VIDEO_WIDTH, VIDEO_HEIGHT
REEL_RECT = (113, 288, 848, 1370)          # the approved shared organic Reel rectangle
GRAPHICS_FOOT = 1090                        # 1370 - 240 (caption band) - 40 (gap)

# Real dev-run copy (values and labels as the runs' verified scripts hold them)
PAIRS = {
    "trepanation": [{"value": "7,000–10,000 yrs ago", "label": "Earliest documented surgery"},
                    {"value": "Up to 80% (est.)", "label": "Peruvian cranial surgery survival rate"}],
    "tunguska": [{"value": "Est. >830 sq mi", "label": "Devastated taiga forest"},
                 {"value": "10–15 megatons TNT", "label": "Calculated airburst energy"}],
    "obelisk": [{"value": "Roughly 137–138 ft", "label": "Estimated completed length"},
                {"value": "Nearly 1,090–1,168 t", "label": "Calculated finished weight"}],
    "voynich": [{"value": "1404–1438", "label": "Parchment date range"},
                {"value": "estimated 20 to 25", "label": "Characters in alphabet"}],
    "richat": [{"value": "25 to almost 31 mi", "label": "Recorded diameter"},
               {"value": "94–104M years ago", "label": "Carbonatite rock cooling age"}],
}
OBELISK = {"value": 138.0, "unit": "ft", "dimension": "length", "subject": "Unfinished obelisk", "approximate": True}
DEPTH = {"value": 280.0, "unit": "ft", "dimension": "depth", "subject": "Derinkuyu depth", "approximate": True}
SECTION = {"subject": "Derinkuyu underground city", "total_depth_ft": 280.0, "approximate": True,
           "levels": [{"depth_ft": 15.0, "label": "Stables and storage"}, {"depth_ft": 35.0, "label": "Living quarters"},
                      {"depth_ft": 140.0, "label": "Cruciform church"}, {"depth_ft": 280.0, "label": "Deepest wells"}]}
DERINKUYU = [{"year": -700, "approximate": True, "label": "Possibly Phrygian tunnels"},
             {"year": 780, "approximate": True, "label": "Expanded against raiders"},
             {"year": 1923, "label": "Abandoned after the population exchange"},
             {"year": 1963, "label": "Rediscovered behind a wall"}]


def _plates(monkeypatch: pytest.MonkeyPatch, job) -> list[tuple[str, Any]]:
    """The final plates one 9:16 render records (two frames a second, nothing encoded or graded)."""
    got: list[tuple[str, Any]] = []
    monkeypatch.setattr(graphics, "VIDEO_FPS", 2)
    monkeypatch.setattr(graphics, "graded_frame", lambda *a, **k: None)
    monkeypatch.setattr(graphics, "_encode", lambda frames, path, n, *a, **k: [None for _ in frames] and path)
    monkeypatch.setattr(graphics, "record_contrast", lambda out, frame, plates: got.extend(
        (n, p) for n, p in plates if p is not None and not p.empty) or {})
    assert job() is not None
    return got


def _words(texts) -> list[str]:
    return sorted(w for t in texts for w in re.findall(r"[A-Z0-9]+", str(t).upper()))


def test_the_reel_box_is_the_shared_organic_rectangle_with_a_240px_caption_band():
    assert safe_zones.safe_box("reel") == REEL_RECT
    assert safe_zones.REEL == safe_zones.union(*safe_zones.DELIVERABLES["reel"])
    for name in safe_zones.DELIVERABLES["reel"]:     # each platform's own free area holds the shared box
        assert safe_zones.inside(REEL_RECT, safe_zones.PLATFORM_UI[name].box()), name
    assert safe_zones.caption_band_top() == REEL_RECT[3] - 240 == 1130
    assert safe_zones.graphics_box() == (113, 288, 848, GRAPHICS_FOOT)
    assert CAPTION_MARGIN_V == H - REEL_RECT[3] == 550
    # the Stories keep their own (Meta) profile: the Reel screenshots say nothing about the Story viewer
    assert safe_zones.safe_box("story") == (65, 269, 1015, 1248)
    # the graphics read the same statement: their text box and shared inset edges sit inside the organic box
    assert graphics.get_text_box(W, H) == (113, 330, 848, GRAPHICS_FOOT) and graphics.TEXT_BOTTOM == GRAPHICS_FOOT
    assert (graphics.INSET_X0, graphics.INSET_X1) == (120, 820) == (graphics.inset_x0(), graphics.inset_x1())
    assert graphics.inset_x0(540, 960) == 60 and graphics.inset_x1(540, 960) == 410


def test_captions_keep_their_640px_measure_centred_in_the_reel_box(tmp_path):
    # Centred on the frame, the organic box leaves 616 px (its right rail, 232 px, is wider than its left, 113), which
    # would narrow every line to 560 px and cut new bursts; the reviewed measure is kept and centred in the box.
    assert vc.MAX_CAPTION_WIDTH == 640
    assert vc.reel_caption_centre_x() == 480 and vc.reel_caption_span() == 2 * (480 - 113)
    words, t = [], 0.0
    for text in ("Visible from space, a bullseye in the Sahara.", "Counterrevolutionaries and pseudoarchaeologists",
                 "It is 25 to almost 31 miles across!", "Ok."):
        for word in text.split():
            words.append({"word": word, "start": round(t, 3), "dur": 0.3})
            t += 0.35
    pills = caption_pills(vc.VideoComposer().build_subtitles(words, tmp_path / "c.ass"))
    assert len(pills) >= 4
    assert all(safe_zones.inside(p, REEL_RECT) for p in pills), pills
    assert all(p[1] >= safe_zones.caption_band_top() for p in pills), pills            # inside the reserved band
    assert all(REEL_RECT[3] - CAPTION_REEL_CLEARANCE - 1 <= p[3] <= REEL_RECT[3] - CAPTION_REEL_CLEARANCE + 1
               for p in pills)
    widest = max(pills, key=lambda p: p[2] - p[0])
    assert abs((widest[0] + widest[2]) / 2 - 480) <= 40                                 # around the box's centre


def test_the_two_line_guard_reads_the_caption_band_not_a_stale_constant():
    # It compared with a fixed 1058 (the ads graphics floor): once the box moved, a two-line plate could rise over
    # graphics that now run to 1090. It reads the reserved band itself, which stands the 40 px gap below them.
    assert vc.CAPTION_GUARD_GRAPHICS_BOTTOM == safe_zones.graphics_box()[3] == GRAPHICS_FOOT
    one_line_top = H - CAPTION_MARGIN_V - CAPTION_REEL_CLEARANCE - vc.REEL_PLATE_H
    for rung in vc.CAPTION_LADDER:
        top = one_line_top - round(vc.CAPTION_TWO_LINE_PITCH_EM * vc.reel_cap_height(rung))
        assert vc._two_line_clears_graphics(rung) == (top >= safe_zones.caption_band_top()), (rung, top)
        assert top - vc.CAPTION_GUARD_GRAPHICS_BOTTOM >= 40 or not vc._two_line_clears_graphics(rung)


@pytest.mark.parametrize("name", sorted(PAIRS))
def test_number_cards_stay_inside_the_organic_box_measured_at_the_size_they_are_drawn(name, tmp_path, monkeypatch):
    plates = _plates(monkeypatch, lambda: graphics.render_stat_cards(PAIRS[name], tmp_path / "n.mp4"))
    box = safe_zones.graphics_box()
    assert all(safe_zones.inside(tuple(round(v) for v in p.box), box) for _n, p in plates), \
        [(n, p.text, p.box) for n, p in plates if not safe_zones.inside(tuple(round(v) for v in p.box), box)]
    # The pair shares one figure size, and the fit is measured on that size: the next larger scale step does not
    # hold the equal-size pair between the bounds (it was tested on the cards' own larger sizes, so a pair could
    # step down a whole scale, kickers and labels too, where the drawn pair fitted).
    figures = [p for n, p in plates if n == "figure"]
    assert len(figures) == 2 and len({p.font.size for p in figures}) == 1, [(p.text, p.font.size) for p in figures]
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    top, bottom = graphics.get_text_bounds(W, H)
    text_w = graphics.get_text_box(W, H)[2] - graphics.inset_x0() - 4
    cards = [dict(c, value=graphics.spoken_scale(graphics.figure_text(graphics.spoken_units(str(c["value"]), "")), ""),
                  label=graphics.spoken_units(str(c["label"]), "")) for c in PAIRS[name]]

    def drawn_at(scale: float) -> list[dict[str, Any]] | None:
        try:
            own = [graphics._stat_layout(probe, c, round(230 * scale), text_w, scale) for c in cards]
            shared = min(p["figure_px"] for p in own)
            drawn = [graphics._stat_layout(probe, c, shared, text_w, scale, num_min=shared) for c in cards]
        except graphics.TextFitError:
            return None
        return drawn if sum(p["height"] for p in drawn) + 90 <= bottom - top else None

    expected = next(d for d in (drawn_at(s) for s in (1.0, 0.9, 0.8, 0.7, 0.6)) if d)
    assert figures[0].font.size == expected[0]["figure_px"]
    labels = [p.font.size for n, p in plates if n == "label"]
    want = [r["font"].size for d in expected for r in d["rows"] if r["name"] == "label"]
    assert labels == want


def test_every_portrait_graphic_keeps_its_words_whole_inside_the_organic_box(tmp_path, monkeypatch):
    box = safe_zones.graphics_box()
    jobs = {
        "scale_height": (lambda: graphics.render_scale(OBELISK, tmp_path / "s.mp4"), ["Unfinished obelisk"]),
        "scale_depth": (lambda: graphics.render_scale(DEPTH, tmp_path / "d.mp4"), ["Derinkuyu depth"]),
        "section": (lambda: graphics.render_cross_section(SECTION, tmp_path / "c.mp4"),
                    [SECTION["subject"]] + [lv["label"] for lv in SECTION["levels"]]),
        "timeline": (lambda: graphics.render_timeline(DERINKUYU, tmp_path / "t.mp4"),
                     [e["label"] for e in DERINKUYU]),
    }
    for key, (job, copy) in jobs.items():
        plates = _plates(monkeypatch, job)
        outside = [(n, p.text, p.box) for n, p in plates
                   if not safe_zones.inside(tuple(round(v) for v in p.box), box)]
        assert not outside, (key, outside)
        drawn = _words(p.text for _n, p in plates)
        for word in _words(copy):
            assert word in drawn, (key, word)


def test_the_height_scale_keeps_the_landmark_measurement_note_whole_on_the_narrower_rail(tmp_path, monkeypatch):
    # The note is drawn tracked (1 px); it was wrapped on its untracked advance and held to two rows, so on the
    # organic box's right edge "GROUND TO TORCH TIP" was dropped whole (inside the box or not at all).
    plates = _plates(monkeypatch, lambda: graphics.render_scale(OBELISK, tmp_path / "s.mp4"))
    notes = [p for n, p in plates if n.startswith("ref_note")]
    assert " ".join(p.text for p in notes).split() == ["GROUND", "TO", "TORCH", "TIP"]
    f_rn = graphics.SCALE_RULER_PX - 4
    assert all(p.font.size == f_rn for p in notes)                     # at the ruler's size, never smaller
    assert all(p.box[2] <= graphics.get_text_box(W, H)[2] for p in notes)


def test_the_cutaway_uses_the_organic_box_without_shrinking_its_type(tmp_path, monkeypatch):
    plates = _plates(monkeypatch, lambda: graphics.render_cross_section(SECTION, tmp_path / "c.mp4"))
    rows = dict(plates)
    labels = [p for n, p in plates if re.fullmatch(r"level\d+_label\d+", n)]
    assert labels and all(p.font.size >= graphics._SECTION_TALL.lab_px - 2 for p in labels)   # its own 2 px step
    assert rows["level3_depth"].font.size == graphics._SECTION_TALL.deep_px                     # the type at its size
    for k in range(len(SECTION["levels"])):                       # each level's feet and metres on one line, whole
        feet, metres = rows[f"level{k}_depth"], rows[f"level{k}_metres"]
        assert metres.box[0] > feet.box[2] and metres.box[2] <= REEL_RECT[2]
    # the labels and the depth axis reach down into the room the organic box adds (the ads foot was 1058)
    assert max(p.box[3] for _n, p in plates) > 1058


def test_a_reconstruction_badge_starts_inside_the_reel_box(tmp_path):
    # The 9:16 badge stood at x 84, 19 px inside the ads box and 29 px outside the organic one (its left guard): it
    # keeps those 19 px inside the organic box, under its top bar.
    words = [{"word": w, "start": 0.4 * k, "dur": 0.35} for k, w in enumerate(["A", "reconstruction", "of", "the", "vault"])]
    ass = vc.VideoComposer().build_subtitles(words, tmp_path / "r.ass", reconstruction_intervals=[(0.0, 2.0)])
    text = ass.read_text(encoding="utf-8")
    badge = {tuple(int(v) for v in m) for line in text.splitlines() if ",ReconPlate," in line
             for m in re.findall(r"\\pos\((\d+),(\d+)\)\\p1", line)}
    assert badge == {(132, 300)}, badge
    assert safe_zones.inside((132, 300, 132, 300), REEL_RECT)


def test_the_design_grid_keeps_the_frame_scale_on_the_narrower_box():
    g = graphics._Grid(W, H)
    assert g.k == 1.0                                           # the type is not scaled to the narrower box
    assert g.x(g.DESIGN_X0) == graphics.inset_x0()               # the design's inset edge on the shared one
    assert g.ix(g.bx1) == pytest.approx(g.DESIGN_X0 + 848 - 120) and g.iy(g.by1) == pytest.approx(GRAPHICS_FOOT)
    small = graphics._Grid(540, 960)
    assert small.k == 0.5 and small.x(small.DESIGN_X0) == graphics.inset_x0(540, 960)


def test_the_evidence_record_of_the_meta_bounds_is_committed_beside_the_row():
    path = Path(__file__).resolve().parents[1] / safe_zones.SOURCES["meta_reels_observed"]
    data = path.read_text(encoding="utf-8")
    assert '"shared_guarded"' in data and "[\n   113,\n   288,\n   848,\n   1370\n  ]" in data
    assert "sha256" not in data                                  # rectangles only: no screenshot hash or pixels
