"""QF-88: the photosensitivity, CPS and CVD gates.

- A 10 Hz strobe is blocked by the release gate with a named reason (WCAG 2.3.1 general flash), a red strobe by the
  red-flash rule, and flashing below the thresholds (3 a second, a small patch, a dim flicker) passes. The check
  joins QF-10's single decode (METHOD_VERSION 2). Footage and AI motion clips are checked as they are taken in.
- A caption burst the timing shows at 20 cps, faster than its words are spoken, fails the build; a burst whose words
  are spoken that fast is the narration's pace, recorded and noted on the card.
- A rendered map passes the CVD gate; a graphic whose accent a protanope sees as the figure gold fails it, and
  art_direction never picks such an accent.
- QF-10b's old-master note survives the METHOD_VERSION 2 re-measure.
- Round 7: a flash check with a failing interval stays a fail beside a held stretch and cannot be cleared; a
  0.44-degree checker over 52-60% of a field reversing 8 times a second fails (its squares' whole area counts).
"""

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import brand, colour_vision, config, picture_qa, release
from src.encoding import BT709_FLAGS
from tests.test_qf10_picture_gate import compose_record, gate, old_report, pre_qf01
from tests.test_r3_graphics_2026_11_02 import fake_geo  # noqa: F401  (fixture)
from tests.test_r4_graphics_2026_11_02 import _render_fast


def clip(path: Path, frame, seconds=3.0, w=270, h=480) -> Path:
    """A BT.709 limited-range H.264 clip from RGB frames `frame(i)`, as the composer encodes."""
    n = int(seconds * 30)
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
           "-r", "30", "-i", "-", "-vf", "scale=out_color_matrix=bt709:out_range=tv", "-c:v", "libx264",
           "-preset", "ultrafast", "-crf", "18", *BT709_FLAGS, "-pix_fmt", "yuv420p", str(path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for i in range(n):
        proc.stdin.write(frame(i, w, h).tobytes())
    proc.stdin.close()
    assert proc.wait(timeout=120) == 0
    return path


def strobe(hz, on=(255, 255, 255), off=(0, 0, 0), area=1.0):
    def frame(i, w, h):
        img = np.full((h, w, 3), off, np.uint8)
        if int(i * hz * 2 / 30) % 2 == 0:
            img[:int(h * area ** 0.5), :int(w * area ** 0.5)] = on
        return img
    return frame


def flash_check(path):
    return next(c for c in picture_qa.measure(path)["checks"] if c["name"] == "picture_flash")


# ------------------------------------------------------------------------------------------------- photosensitivity
def test_a_10_hz_strobe_is_blocked_by_the_release_gate_with_a_named_reason(tmp_path):
    report, checks = gate(clip(tmp_path / "strobe.mp4", strobe(10)))
    assert checks["picture_flash"]["status"] == "fail"
    assert checks["picture_measurement"]["status"] == "pass"
    problem = next(p for p in release.release_problems(report, dry_run=True) if p.startswith("picture_flash:"))
    assert "10 times in a second" in problem and "WCAG 2.3.1" in problem and "0:00" in problem


@pytest.mark.parametrize("name, frame, status", [
    ("red_on_black_10hz", strobe(10, (255, 0, 0)), "fail"),
    ("white_4hz", strobe(4), "fail"),
    ("patch_6pct_10hz", strobe(10, area=0.06), "fail"),
    ("white_3hz", strobe(3), "pass"),
    ("dim_10hz", strobe(10, (40, 40, 40), (25, 25, 25)), "pass"),
    ("patch_2pct_10hz", strobe(10, area=0.02), "pass"),
])
def test_flash_thresholds_follow_wcag_2_3_1(tmp_path, name, frame, status):
    check = flash_check(clip(tmp_path / f"{name}.mp4", frame))
    assert check["status"] == status, check["detail"]
    if name.startswith("red"):
        assert "saturated red" in check["detail"]


def test_a_red_flash_on_a_bright_ground_counts_even_where_luminance_alone_would_not(tmp_path):
    # Red against an equally light grey: the luminance hardly changes, the chromaticity does (WCAG note 3).
    grey = 147   # sRGB grey with red's relative luminance (0.2126)
    check = flash_check(clip(tmp_path / "red_grey.mp4", strobe(10, (255, 0, 0), (grey, grey, grey))))
    assert check["status"] == "fail" and "saturated red" in check["detail"]


def test_a_fast_fade_is_one_transition_not_many(tmp_path):
    # Black to white over 4 frames, held, back over 4 frames, three times in 3 s: 1 flash a second.
    def frame(i, w, h):
        phase = (i % 30) / 30
        v = min(1.0, phase * 30 / 4) if phase < 0.5 else max(0.0, 1 - (phase - 0.5) * 30 / 4)
        return np.full((h, w, 3), int(255 * v), np.uint8)
    assert flash_check(clip(tmp_path / "fade.mp4", frame))["status"] == "pass"


def test_flash_joins_the_single_decode_and_redoes_older_measurements():
    assert picture_qa.METHOD_VERSION == 2
    assert [a.name for a in picture_qa.analysers(False)][-1] == "picture_flash"
    assert config.PICTURE_QA_FLASH_PER_S == 3 and config.PICTURE_QA_FLASH_AREA == 0.25


def test_footage_that_flashes_is_not_taken_in(tmp_path, monkeypatch):
    from src import footage
    monkeypatch.setattr(config, "ASSETS_DIR", tmp_path / "assets")
    folder = tmp_path / "assets" / "topic" / "footage"
    folder.mkdir(parents=True)
    clip(folder / "strobe.mp4", strobe(10), seconds=7.0)
    clip(folder / "calm.mp4", lambda i, w, h: np.full((h, w, 3), 90 + i % 60, np.uint8), seconds=7.0)
    clips, credits = footage.get_curated_footage("topic", tmp_path / "work")
    assert [c["title"] for c in credits] == ["Calm"]
    assert len(clips) == 1 and clips[0].is_file()
    assert not (tmp_path / "work" / "footage_02.mp4").exists()


def test_an_ai_motion_clip_that_flashes_is_rejected(tmp_path, monkeypatch):
    from src import motion
    monkeypatch.setattr(motion, "verify_motion_hook_integrity", lambda f0, f1, archetype="megastructure":
                        {"pass": True, "reason": "ok", "inliers": 50, "inlier_ratio": 0.9})
    monkeypatch.setattr(motion, "content_change_share", lambda c: 0.0)
    monkeypatch.setattr(motion, "motion_amount", lambda c: {"flow_share": 1.0, "moving_share": 1.0, "mean_diff": 9.0})
    out = clip(tmp_path / "motion_01.mp4", strobe(10), seconds=motion.SECONDS_OUT)
    with pytest.raises(motion.MotionError, match="Picture check failed: picture_flash"):
        motion._gate(out, "megastructure")
    calm = clip(tmp_path / "motion_02.mp4", lambda i, w, h: np.full((h, w, 3), 90 + i % 60, np.uint8),
                seconds=motion.SECONDS_OUT)
    assert motion._gate(calm, "megastructure")["accepted"] is True


def test_qf10b_old_master_note_survives_the_method_2_re_measure(tmp_path, monkeypatch):
    master = pre_qf01(tmp_path / "m.mp4")
    compose_record(master)
    old_report(master)
    monkeypatch.setattr(picture_qa, "METHOD_VERSION", 1)    # the check as QF-10b ran it
    _, first = gate(master)
    assert first["picture_colour"]["grandfathered"] is True
    monkeypatch.setattr(picture_qa, "METHOD_VERSION", 2)    # this row: measured again, flash included
    _, second = gate(master)
    assert "reused_from" not in second["picture_measurement"]["measurement"]
    assert second["picture_colour"]["grandfathered"] is True and second["picture_colour"]["status"] == "pass"
    assert second["picture_flash"]["status"] == "pass"


# ---------------------------------------------------------------------------------------------------------- CPS
def _words(spec):
    return [{"word": w, "start": s, "dur": d} for w, s, d in spec]


def test_a_burst_shown_at_20_cps_faster_than_it_is_spoken_is_a_timing_defect():
    from src.video_composer import reading_rates
    phrase = _words([("ARCHAEOLOGISTS", 0.0, 0.5), ("FOUND", 0.55, 0.45)])     # 20 characters, spoken in 1.0 s
    rate = reading_rates([phrase], [(0.0, 1.0, [0.0, 0.55])])
    assert rate["timing_defects"] == [] and rate["over_max"] == 1             # 20 cps, as spoken: the narration
    rate = reading_rates([phrase], [(0.0, 0.8, [0.0, 0.55])])                  # 25 cps shown, 20 spoken
    assert [d["shown_cps"] for d in rate["timing_defects"]] == [25.0]
    # the two-frame lead a programme's first burst loses at 0.0 is not a defect
    assert reading_rates([phrase], [(0.0, 0.94, [0.0, 0.55])])["timing_defects"] == []


FAST = [("ARCHAEOLOGISTS", 0.2, 0.6), ("FOUND", 0.85, 0.5), ("A", 1.45, 0.1), ("ROOM.", 1.6, 0.4)]


def test_a_20_cps_burst_cut_short_by_the_timing_fails_the_build(tmp_path, monkeypatch):
    from src import video_composer as vc
    words = _words(FAST)      # "ARCHAEOLOGISTS FOUND": 20 characters holding the voice 1.25 s (16 cps)
    real = vc.burst_timings
    # the timing hides the first burst 0.25 s early: on screen 1.0 s, 20 cps
    monkeypatch.setattr(vc, "burst_timings", lambda phrases, **kw: [
        (s, (h - 0.25 if i == 0 else h), r) for i, (s, h, r) in enumerate(real(phrases, **kw))])
    monkeypatch.setattr(vc, "caption_phrases", lambda w, *a, **k: [w[:2], w[2:]])
    with pytest.raises(vc.CaptionReadingRateError, match="20.0 characters a second.*leaves before its words"):
        vc.VideoComposer().build_subtitles(words, tmp_path / "c.ass")
    monkeypatch.setattr(vc, "burst_timings", real)
    composer = vc.VideoComposer()
    composer.build_subtitles(words, tmp_path / "c.ass")                     # the same words, timed right
    assert composer.last_reading_rate["timing_defects"] == [] and composer.last_reading_rate["bursts"] == 2


def test_the_narrations_pace_is_noted_on_the_card_never_blocking():
    rate = {"max_cps": 17.0, "bursts": 30, "over_max": 4, "over_20": 1, "timing_defects": [],
            "worst": {"at": 12.3, "text": "ARCHAEOLOGISTS FOUND", "shown_cps": 21.0, "spoken_cps": 21.4}}
    (check,) = release._reading_rate_checks({"reading_rate": rate})
    assert check["status"] == "pass" and check["minor"] and check["note"]
    assert "4 of 30" in check["detail"] and "0:12.3" in check["detail"]
    from src.telegram_bot import TelegramApprovalBot
    blocked = {"checks": [{"name": "picture_black", "status": "fail", "detail": "black"}, check]}
    assert "caption_rate (note, not blocking)" in TelegramApprovalBot.release_lines(blocked)
    (bad,) = release._reading_rate_checks({"reading_rate": {**rate, "timing_defects": [rate["worst"]]}})
    assert bad["status"] == "fail"
    assert release._reading_rate_checks({}) == []          # a master rendered before QF-88: nothing claimed


# ---------------------------------------------------------------------------------------------------------- CVD
def test_a_rendered_map_passes_the_cvd_gate(tmp_path, monkeypatch, fake_geo):  # noqa: F811
    from src import graphics
    place = {"lat": 38.3735, "lon": 34.7347, "label": "Derinkuyu", "country": "Turkey"}
    out = tmp_path / "graphics" / "graphic_map.mp4"
    out.parent.mkdir()
    state = _render_fast(monkeypatch, lambda: graphics.render_map(place, out, width=1080, height=1920))
    state["frames"][-1].save(out.with_suffix(".jpg"), quality=90)      # as graphics._encode writes the poster
    result = colour_vision.check(out.with_suffix(".jpg"), brand.TEAL)
    assert {"home_fill", "land"} <= set(result["roles"]), result
    assert result["pass"] and all(p["de00"][v] >= colour_vision.MIN_DE00 for p in result["pairs"] for v in p["de00"])
    (check,) = [c for c in release._colour_vision_checks(tmp_path) if c["name"] == "graphic_colour_vision"]
    assert check["status"] == "pass"


GREEN = (102, 204, 51)   # 59 degrees of hue from gold (art_direction took it before), one colour for a protanope


def _gold_and_accent(path: Path, accent) -> Path:
    im = Image.new("RGB", (540, 960), brand.INK)
    d = ImageDraw.Draw(im)
    d.rectangle([60, 200, 480, 260], fill=brand.GOLD)
    d.rectangle([60, 600, 480, 660], fill=accent)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, quality=90)
    return path


def test_an_accent_a_protanope_sees_as_gold_fails_the_gate(tmp_path):
    assert colour_vision.pair_distance(GREEN, brand.GOLD)["normal"] > 20
    _gold_and_accent(tmp_path / "graphics" / "graphic_route.jpg", GREEN)
    (tmp_path / "palette.json").write_text(json.dumps({"accent": list(GREEN)}), encoding="utf-8")
    (check,) = release._colour_vision_checks(tmp_path)
    assert check["status"] == "fail" and "protanopia" in check["detail"] and "graphic_route.jpg" in check["detail"]
    _gold_and_accent(tmp_path / "graphics" / "graphic_route.jpg", (92, 137, 204))
    (tmp_path / "palette.json").write_text(json.dumps({"accent": [92, 137, 204]}), encoding="utf-8")
    assert release._colour_vision_checks(tmp_path)[0]["status"] == "pass"


def test_art_direction_never_picks_an_accent_colour_blind_viewers_merge_with_gold(tmp_path):
    from src import art_direction
    hero = tmp_path / "hero.jpg"
    Image.new("RGB", (96, 96), GREEN).save(hero)
    assert colour_vision.accent_problem(art_direction._legible(GREEN)) is not None
    pal = art_direction.palette_from(hero)
    assert colour_vision.accent_problem(tuple(pal["accent"])) is None
    assert tuple(pal["accent"]) == tuple(brand.TEAL) and "colour-blind" in pal["source"]


def test_cvd_measures_are_the_published_ones():
    # CIEDE2000 against Sharma, Wu & Dalal 2005 table 1 (pairs 1 and 7); Machado 2009 rows sum to 1.
    assert round(colour_vision.de00((50, 2.6772, -79.7751), (50, 0, -82.7485)), 4) == 2.0425
    assert round(colour_vision.de00((50, 0, 0), (50, -1, 2)), 4) == 2.3669
    for m in colour_vision.MACHADO_2009.values():
        assert np.allclose(m.sum(axis=1), 1.0, atol=1e-5)


def test_the_map_pair_is_judged_on_maps_only(tmp_path):
    # Home-fill and land colours in a photograph (a stat card's ground, a cover) are not a meaning pair: on real
    # stills they matched by chance and fell under the margin. On a map (or a route) they are.
    im = Image.new("RGB", (540, 960), brand.INK)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 540, 400], fill=colour_vision.HOME_TINT)
    d.rectangle([0, 400, 540, 800], fill=colour_vision.LAND_TINT)
    for name in ("graphic_numbers.jpg", "graphic_map.jpg", "graphic_route.jpg", "thumbnail_cover.jpg"):
        im.save(tmp_path / name, quality=95)
    pairs = {n: [p["pair"] for p in colour_vision.check(tmp_path / n)["pairs"]]
             for n in ("graphic_numbers.jpg", "graphic_map.jpg", "graphic_route.jpg", "thumbnail_cover.jpg")}
    assert pairs == {"graphic_numbers.jpg": [], "graphic_map.jpg": [["home_fill", "land"]],
                     "graphic_route.jpg": [["home_fill", "land"]], "thumbnail_cover.jpg": []}


# ------------------------------------------------------------------------------------------- round 2 (review 1)
def checker(squares_across, hz=10):
    def frame(i, w, h):
        s = w // squares_across
        yy, xx = np.mgrid[0:h, 0:w]
        board = ((yy // s + xx // s) % 2).astype(bool)
        if int(i * hz * 2 / 30) % 2:
            board = ~board
        img = np.zeros((h, w, 3), np.uint8)
        img[board] = 255
        return img
    return frame


def halves(i, w, h):
    img = np.zeros((h, w, 3), np.uint8)
    if int(i * 10 * 2 / 30) % 2 == 0:
        img[:, : w // 2] = 255
    else:
        img[:, w // 2:] = 255
    return img


@pytest.mark.parametrize("name, frame", [("checker_12_across", checker(12)), ("checker_6_across", checker(6)),
                                         ("checker_4_across", checker(4)), ("halves", halves)])
def test_counter_phase_flashing_counts_its_whole_area(tmp_path, name, frame):
    # Review 1, blocking 1: a quarter of a field rising while another falls is flashing area, whatever the phase.
    assert flash_check(clip(tmp_path / f"{name}.mp4", frame))["status"] == "fail"


def rect(x0, y0, w, h):
    def frame(i, W, H):
        img = np.zeros((H, W, 3), np.uint8)
        if int(i * 10 * 2 / 30) % 2 == 0:
            img[int(y0):int(y0 + h), int(x0):int(x0 + w)] = 255
        return img
    return frame


FW, FH = 270 / 3, 480 / 3     # a 10-degree field on the 270 x 480 fixtures


@pytest.mark.parametrize("name, box, status", [
    ("strip30_on_grid", (0, FH * 0.1, FW, FH * 0.30), "fail"),
    ("strip30_off_grid", (FW * 0.25, FH * 0.1, FW, FH * 0.30), "fail"),
    ("strip32_off_grid", (FW * 0.25, FH * 0.1, FW, FH * 0.32), "fail"),
    ("square30_off_grid", (FW * 0.47, FH * 0.47, FW * 0.548, FH * 0.548), "fail"),
    ("strip22_on_grid", (0, FH * 0.1, FW, FH * 0.22), "pass"),
])
def test_a_field_is_judged_at_any_position(tmp_path, name, box, status):
    # Review 1, blocking 2: the 25% of a 10-degree field is measured anywhere on the frame, not on a fixed grid.
    assert flash_check(clip(tmp_path / f"{name}.mp4", rect(*box)))["status"] == status


def degree_checker(deg, phase=0.0, w=1080, h=1920):
    """A 10 Hz counter-phase checkerboard of `deg`-degree squares (0.1 degree = a 300th of each side: WCAG note 1's
    field is a third of the side, 10 degrees), offset by `phase` of a square."""
    cw, ch = w / 300 * deg / 0.1, h / 300 * deg / 0.1
    yy, xx = np.mgrid[0:h, 0:w]
    board = (np.floor((xx + phase * cw) / cw) + np.floor((yy + phase * ch) / ch)) % 2 == 1

    def frame(i, w_, h_):
        b = board if int(i * 20 / 30) % 2 == 0 else ~board
        img = np.zeros((h_, w_, 3), np.uint8)
        img[b] = 255
        return img
    return frame


@pytest.mark.parametrize("deg, phase, status", [
    (0.1, 0.0, "fail"), (0.1, 0.5, "fail"), (0.15, 0.5, "fail"), (1.0, 0.3, "fail"),
    (0.028, 0.0, "pass"), (0.04, 0.0, "pass"),   # WCAG's fine, balanced pattern (squares under 0.1 degree)
])
def test_squares_of_a_tenth_of_a_degree_or_more_fail_and_finer_ones_are_exempt(tmp_path, deg, phase, status):
    path = clip(tmp_path / f"c{deg}_{phase}.mp4", degree_checker(deg, phase), seconds=1.2, w=1080, h=1920)
    assert flash_check(path)["status"] == status


def test_a_cut_just_before_a_fast_boundary_never_makes_a_defect():
    # Review 1, blocking 3: snap_to_cuts used to move a boundary onto a cut 3 frames early and turn a 16.9 cps burst
    # into an 18 cps "defect" the build then failed on its own timing. It now refuses such a move.
    from src import video_composer as vc
    b0 = _words([("ROMAN", 0.50, 0.3), ("CONCRETE", 0.85, 0.45), ("HEALS", 1.35, 0.3), ("ITSELF", 1.70, 0.35)])
    b1 = _words([("WITH", 2.10, 0.2), ("LIME", 2.35, 0.3), ("CLASTS.", 2.7, 0.5)])
    timings = vc.burst_timings([b0, b1], dwell=True)
    for k in range(1, 7):
        snapped = vc.snap_to_cuts([b0, b1], timings, [timings[0][1] - k / 30])
        assert vc.reading_rates([b0, b1], snapped)["timing_defects"] == [], k


def test_an_unmeasured_ingest_clip_and_a_missing_palette_are_said_on_the_card(tmp_path):
    (tmp_path / "footage").mkdir()
    (tmp_path / "footage" / "footage_01.json").write_text(json.dumps({"picture_check": "not measured (bad file)"}),
                                                          encoding="utf-8")
    (note,) = release._ingest_notes(tmp_path)
    assert note["status"] == "pass" and note["note"] and "footage_01" in note["detail"]
    _gold_and_accent(tmp_path / "graphics" / "graphic_route.jpg", (92, 137, 204))
    (check,) = release._colour_vision_checks(tmp_path)
    assert check["status"] == "pass" and "not judged" in check["detail"]


def test_a_moving_texture_is_not_flashing(tmp_path):
    # A high-contrast pattern panning across the frame changes each point at a different frame: no quarter of a
    # field rises or falls at once, so it is not counted (the production copy's halftone pan and fast count-ups
    # failed the first round-2 rule, which counted each sample's own transitions).
    def frame(i, w, h):
        xx = np.arange(w)[None, :] + 3 * i
        img = np.zeros((h, w, 3), np.uint8)
        img[:, ((xx // 9) % 2 == 0)[0]] = 255
        return img
    assert flash_check(clip(tmp_path / "pan.mp4", frame))["status"] == "pass"


# ------------------------------------------------------------------------------------------- round 3 (review 1, round 2)
def halves_at(hz):
    def frame(i, w, h):
        img = np.zeros((h, w, 3), np.uint8)
        if int(i * hz * 2 / 30) % 2 == 0:
            img[:, : w // 2] = 255
        else:
            img[:, w // 2:] = 255
        return img
    return frame


def late_fall(i, w, h):
    """Three flashes (6 alternating transitions), then the frame's two halves go dark two frames apart, with a still
    frame between: one fall, as WCAG counts transitions between adjacent peaks and valleys."""
    img = np.zeros((h, w, 3), np.uint8)
    if i < 30:
        if (i // 5) % 2 == 1:
            img[:] = 255
    elif i < 32:
        img[:, w // 2:] = 255
    return img


def in_turn(steps):
    """Columns 4 px wide in three classes; on each of `steps` frames two classes flip, one rising and one falling,
    so a third of every field rises and a third falls on every frame, while each class flips on two frames of
    three; held after that."""
    states = [np.array([0, 1, 0])]
    for k in range(steps):
        s = states[-1].copy()
        s[[c for c in range(3) if c != k % 3]] ^= 1
        states.append(s)

    def frame(i, w, h):
        cls = (np.arange(w) // 4) % 3
        img = np.zeros((h, w, 3), np.uint8)
        img[:, states[min(i, steps)][cls] == 1] = 255
        return img
    return frame


def drifting_band(i, w, h):
    """A 10 Hz band 60% of a field high that drifts 3 px a frame."""
    img = np.zeros((h, w, 3), np.uint8)
    if int(i * 10 * 2 / 30) % 2 == 0:
        top = int(FH * 0.2 + 3 * i)
        img[top:top + int(FH * 0.6)] = 255
    return img


@pytest.mark.parametrize("hz", [2, 3])
@pytest.mark.parametrize("pattern", ["checker", "halves"])
def test_counter_phase_flashing_at_3_hz_or_slower_passes(tmp_path, pattern, hz):
    # Review 1, round 2, blocking 1b: a frame where part of a field rises and part falls is one transition, not two
    # (each part reverses once). Round 2 counted 6 flashes at 3 Hz.
    frame = checker(12, hz) if pattern == "checker" else halves_at(hz)
    assert flash_check(clip(tmp_path / f"{pattern}_{hz}.mp4", frame))["status"] == "pass"


def test_a_second_fall_after_a_still_frame_is_not_a_new_transition(tmp_path):
    # Review 1, round 2, blocking 1a: round 2 forgot the field's direction on the still frame and counted the second
    # half's fall as a 7th transition.
    assert flash_check(clip(tmp_path / "late_fall.mp4", late_fall, seconds=1.4))["status"] == "pass"


def test_parts_of_a_field_changing_in_turn_are_not_one_area_flashing(tmp_path):
    # The real whips (review 1, round 2, blocking 1): a field a third rising and a third falling, frame after frame,
    # made a transition on each frame under the field rule alone. Here 9 such frames: each part flashes 3 times
    # (6 transitions), the limit, so no area of the field flashes more than 3 times; it passes, with that count said.
    check = flash_check(clip(tmp_path / "in_turn.mp4", in_turn(9), seconds=1.5))
    assert check["status"] == "pass", check["detail"]
    assert "the most anywhere: 3 flashes" in check["detail"]


def test_the_same_parts_kept_flashing_fail(tmp_path):
    # The same pattern for a second: each part flashes 10 times (round 2 counted a run of such frames as one).
    assert flash_check(clip(tmp_path / "in_turn_long.mp4", in_turn(30), seconds=1.5))["status"] == "fail"


def test_a_drifting_strobe_still_fails(tmp_path):
    # Each point under the drifting band still flashes more than 3 times a second: the sample condition does not
    # excuse flashing that moves.
    assert flash_check(clip(tmp_path / "drift.mp4", drifting_band))["status"] == "fail"


def test_a_passing_picture_never_reports_more_than_3_flashes(tmp_path):
    # The screen stage counts a 0.04-degree checkerboard as flashing (nearest neighbour aliases it); the fine stage
    # exempts it, and the sentence reports the count the decision rests on.
    path = clip(tmp_path / "c0.04.mp4", degree_checker(0.04), seconds=1.2, w=1080, h=1920)
    check = flash_check(path)
    assert check["status"] == "pass"
    most = float(check["detail"].split("the most anywhere: ")[1].split(" flashes")[0])
    assert most <= 3, check["detail"]


# Review 1, round 3, blocking 1: different parts of a field flashing in turn are flashes of that field (WCAG's
# "combined area of flashes occurring concurrently"), whichever pixels make them. Round 3 passed every one of these.
def rolling(classes_of, on=(255, 255, 255), per=3, cycle=30):
    """Three disjoint pixel classes; class c flashes `per` times at 15 Hz (a frame on, a frame off) from frame
    c * 2 * per of each `cycle` frames, so the field flashes 3 * per times a second, each flash a third of it."""
    def frame(i, w, h):
        cls = classes_of(w, h)
        img = np.zeros((h, w, 3), np.uint8)
        c, r = divmod(i % cycle, 2 * per)
        if c < 3 and r % 2 == 0:
            img[cls == c] = on
        return img
    return frame


def bands(w, h):          # horizontal bands a third of a field high
    return np.broadcast_to((np.arange(h)[:, None] // int(FH / 3)) % 3, (h, w))


def blocks(w, h):         # blocks a third of a field wide
    return np.broadcast_to((np.arange(w)[None, :] // int(FW / 3)) % 3, (h, w))


def columns4(w, h):       # 4-px columns in three classes
    return np.broadcast_to((np.arange(w)[None, :] // 4) % 3, (h, w))


def moving_band(speed, hz=15, share=0.35, start=-60):
    """A 15 Hz band, 35% of a field high, moving `speed` px a frame (a strobing object in motion)."""
    def frame(i, w, h):
        img = np.zeros((h, w, 3), np.uint8)
        top = int(start + speed * i) % (h + 60) - 60
        if int(i * hz * 2 / 30) % 2 == 0:
            img[max(0, top):max(0, top + int(FH * share))] = 255
        return img
    return frame


@pytest.mark.parametrize("name,frame", [
    ("rolling_bands_white", rolling(bands)),
    ("rolling_bands_red", rolling(bands, on=(255, 0, 0))),
    ("rolling_blocks", rolling(blocks)),
    ("rolling_columns4", rolling(columns4)),
    ("rolling_bands_2_each", rolling(bands, per=2)),
    ("moving_band_10px", moving_band(10)),
])
def test_parts_of_a_field_flashing_in_turn_fail(tmp_path, name, frame):
    check = flash_check(clip(tmp_path / f"{name}.mp4", frame))
    assert check["status"] == "fail", check["detail"]


# Review 1, round 4: a counter-phase frame is excused (the whip over detail) only when neither its rise nor its fall
# is coarse (more than 25% of the field by majority over ~1-degree boxes). Round 4 excused every counter-phase frame,
# so a rolling strobe given a counter-phase partner, and fast cuts between pictures whose bright part differs (each
# cut a band going dark while another lights: a general flash over a third of the field, 8 or 9 a second), passed.
def _field_rows(h):
    return np.arange(h) % int(FH)          # every field sees the same layout (its period is the field height)


def masked_rolling(on=(255, 255, 255)):
    """Per field: band A (27%), band B (27%), and below them a class X of 4-px columns (28% of the field). A flashes
    3 times at 15 Hz, then B; X is lit whenever A is not, so A's transitions are counter-phase frames. The field shows
    9 flashes in 0.4 s, at least 6 of them a contiguous 27% of it."""
    def frame(i, w, h):
        y = _field_rows(h)
        img = np.zeros((h, w, 3), np.uint8)
        j = i % 30
        a_on, b_on = j < 6 and j % 2 == 0, 6 <= j < 12 and j % 2 == 0
        if a_on:
            img[y < 43] = on
        if b_on:
            img[(y >= 43) & (y < 86)] = on
        if not a_on:
            img[((np.arange(w) // 4) % 5 < 3)[None, :] & (y >= 86)[:, None]] = on
        return img
    return frame


def cuts(per_s, band=None, classes=3, on=(255, 255, 255)):
    """Hard cuts, `per_s` a second, between `classes` pictures whose bright band (`band` px high, default a third of
    a field) differs: at each cut one band goes dark and the next lights."""
    def frame(i, w, h):
        rows = (np.arange(h)[:, None] // (band or int(FH / 3))) % classes
        img = np.zeros((h, w, 3), np.uint8)
        img[np.broadcast_to(rows == (i * per_s // 30) % classes, (h, w))] = on
        return img
    return frame


def masked_cuts(i, w, h):
    """cuts(8) with a fine partner: a class of 4-px columns lit on every other cut."""
    img = cuts(8)(i, w, h)
    if (i * 8 // 30) % 2:
        img[:, (np.arange(w) // 4) % 5 == 0] = 255
    return img


def masked_rolling_fine(i, w, h):
    """masked_rolling in 4-px columns: A, B and X are column classes, a third of every field each. A flashes 3 times
    at 15 Hz with X lit whenever A is not (fine counter-phase frames, excused as a whip on their own), then B flashes
    3 times: the field shows 6 flashes in 0.4 s, A and X flashing in place."""
    cls = columns4(w, h)
    img = np.zeros((h, w, 3), np.uint8)
    j = i % 30
    img[cls == (0 if j < 6 and j % 2 == 0 else 2)] = 255
    if 6 <= j < 12 and j % 2 == 0:
        img[cls == 1] = 255
    return img


def in_turn_then_flash(i, w, h):
    """The in-turn whip (9 frames), then one full-frame flash in the same second: every part flashes 4 times."""
    img = in_turn(9)(min(i, 11), w, h) if i < 12 else np.zeros((h, w, 3), np.uint8)
    if 14 <= i < 17:
        img[:] = 255
    return img


@pytest.mark.parametrize("name,frame", [
    ("masked_rolling", masked_rolling()),
    ("masked_rolling_fine", masked_rolling_fine),
    ("in_turn_then_a_flash", in_turn_then_flash),
    ("masked_rolling_red", masked_rolling(on=(255, 0, 0))),
    ("cuts_8_per_s", cuts(8)),
    ("cuts_9_per_s", cuts(9)),
    ("cuts_8_band40", cuts(8, band=40)),
    ("cuts_8_red", cuts(8, on=(255, 0, 0))),
    ("cuts_8_masked", masked_cuts),
    ("cuts_12_four_bands_of_26pct", cuts(12, band=42, classes=4)),
])
def test_a_coarse_counter_phase_change_is_never_excused_as_a_whip(tmp_path, name, frame):
    check = flash_check(clip(tmp_path / f"{name}.mp4", frame))
    assert check["status"] == "fail", check["detail"]


# ------------------------------------------------------------------------------------------------- round 6
def _checker4(w, h):
    yy, xx = np.mgrid[0:h, 0:w]
    return ((yy // 4 + xx // 4) % 2).astype(bool)


def sub_band_checker(i, w, h):
    """Review 1, round 5, blocking 1 (sub_band_checker_20): per field, a 0.44-degree checker over its top 20% inverts
    in place at every cut, and below it one lit band (20% of the field) jumps between three places, 8 cuts a second.
    Each alone is under 25% of the field; at every cut 30% of it rises and 30% falls together."""
    b = round(FH * 0.20)
    y = _field_rows(h)
    k = i * 8 // 30
    img = np.zeros((h, w, 3), np.uint8)
    img[(y < b)[:, None] & (_checker4(w, h) ^ bool(k % 2))] = 255
    pos = k % 3
    img[(y >= b + pos * b) & (y < b + (pos + 1) * b)] = 255
    return img


def sub_band_vstrip(i, w, h):
    """The same with the checker as a vertical strip (20% of the field's width) that every band position touches:
    each cut flashes one contiguous 40% of the field."""
    x = np.arange(w) % int(FW)
    y = _field_rows(h)
    k = i * 8 // 30
    img = np.zeros((h, w, 3), np.uint8)
    img[(x < 18)[None, :] & (_checker4(w, h) ^ bool(k % 2))] = 255
    pos = k % 4
    img[((y >= pos * 40) & (y < (pos + 1) * 40))[:, None] & (x >= 18)[None, :]] = 255
    return img


def sub_band_burst(i, w, h):
    """sub_band_checker for one second only, black before and after."""
    return sub_band_checker(i, w, h) if 30 <= i < 60 else np.zeros((h, w, 3), np.uint8)


@pytest.mark.parametrize("name,frame", [
    ("sub_band_checker_20", sub_band_checker),
    ("sub_band_vstrip", sub_band_vstrip),
    ("sub_band_burst", sub_band_burst),
])
def test_a_fine_pattern_flashing_in_place_beside_a_coarse_change_fails(tmp_path, name, frame):
    # Review 1, round 5, blocking 1: each area under 25% of the field, together 30% rising and 30% falling at every
    # cut, 8 a second ("the combined area of flashes occurring concurrently"). Round 5 reported "3 flashes".
    check = flash_check(clip(tmp_path / f"{name}.mp4", frame))
    assert check["status"] == "fail", check["detail"]


def coarse_then_fine_reversal(i, w, h):
    """Review 1, round 5 (coarse3_plus_fine_reversal): a band over 30% of each field flashes 3 times at 15 Hz, then
    a 0.44-degree checker over the other 70% reverses once: 7 transitions in 0.3 s. The checker's one reversal is the
    same count a whip frame over detail gives (the round-5 false rejects), so it is held, never passed."""
    y = _field_rows(h)
    img = np.zeros((h, w, 3), np.uint8)
    if 30 <= i < 36 and i % 2 == 0:
        img[y < 48] = 255
    img[(y >= 48)[:, None] & (_checker4(w, h) ^ bool(i >= 38))] = 255
    return img


def test_a_count_only_a_motion_test_could_settle_is_held_for_the_operator(tmp_path, monkeypatch):
    # Operator decision A after round 5: held, not passed and not rejected as a hazard; the card says why, the report
    # carries a contact sheet from the master's own frames, and no live post or auto-approval goes through until the
    # operator clears it for these bytes.
    from main import PipelineCoordinator
    from src.telegram_bot import TelegramApprovalBot
    master = clip(tmp_path / "held_master.mp4", coarse_then_fine_reversal)
    report, checks = gate(master)
    flash = checks["picture_flash"]
    assert flash["status"] == "needs_review" and flash["hold"] and not flash["intervals"], flash
    assert flash["held"] and flash["held"][0]["end"] - flash["held"][0]["start"] >= 1.0
    assert "Possible flash; needs your look" in flash["detail"] and " s)" in flash["detail"]
    sheet = tmp_path / flash["contact_sheets"][0]["path"]
    assert sheet.suffix == ".jpg" and 0 < sheet.stat().st_size <= 300 * 1024
    with Image.open(sheet) as im:
        assert im.format == "JPEG" and im.width == 8 * config.PICTURE_HOLD_SHEET_TILE
    # Held in a live post (the dry-run report lets a simulated post through; nothing is posted there).
    assert "picture_flash" in {c["name"] for c in release.blocking_checks(report, dry_run=False)}
    assert "picture_flash" not in {c["name"] for c in release.blocking_checks(report, dry_run=True)}
    assert any(p.startswith("picture_flash: Possible flash") for p in release.release_problems(report))
    card = TelegramApprovalBot.release_lines({**report, "dry_run": False,
                                              "checks": [c for c in report["checks"] if c["name"] == "picture_flash"]})
    assert "held: possible flash; needs your look" in card and "blocked" not in card
    alerts = []

    class Stub:
        db = type("D", (), {"run_label": staticmethod(lambda run_id: run_id)})()
        _alert = staticmethod(alerts.append)
    assert PipelineCoordinator._held_for_look(Stub(), "r1", report) and "Possible flash" in alerts[0]
    # Cleared by the operator for these bytes: a note, not blocking; a record for other bytes clears nothing.
    record = release.clear_flash_hold(tmp_path, report, by="test")
    report, checks = gate(master)
    assert checks["picture_flash"]["status"] == "pass" and checks["picture_flash"]["note"]
    assert "you looked at it and cleared it" in checks["picture_flash"]["detail"]
    assert not release.held_flash(report)
    (tmp_path / release.FLASH_REVIEW).write_text(json.dumps({**record, "master_sha256": "0" * 64}), encoding="utf-8")
    report, checks = gate(master)       # the reused measurement is held again
    assert checks["picture_flash"]["status"] == "needs_review" and release.held_flash(report)


@pytest.mark.parametrize("name,frame,seconds,bound", [
    ("in_turn_long", in_turn(30), 1.5, 21),
    ("sub_band_checker_20", sub_band_checker, 3.0, 80),
])
def test_the_flash_count_stays_incremental(tmp_path, monkeypatch, name, frame, seconds, bound):
    # Review 1, round 5: the first form of the in-place rule recomputed every fine frame's shares for every count it
    # tried, and a decode timed out (135 s). Window sums per decoded frame, measured in round 6 (13.3 and 53.0 with
    # both stages), are held to about 1.5 times that.
    calls = []
    real = picture_qa._FlashStage._field_shares

    def counted(mask, fh, fw):
        calls.append(1)
        return real(mask, fh, fw)
    monkeypatch.setattr(picture_qa._FlashStage, "_field_shares", staticmethod(counted))
    result = picture_qa.measure(clip(tmp_path / f"{name}.mp4", frame, seconds=seconds))
    assert next(c for c in result["checks"] if c["name"] == "picture_flash")["status"] == "fail"
    assert len(calls) / result["decoded_frames"] <= bound, len(calls) / result["decoded_frames"]


# ------------------------------------------------------------------------------------------------- round 7
def full_4hz_then_held(i, w, h):
    """Review 1, round 6, blocking 1 (fail_then_hold): a full-frame 4 Hz strobe for a second, black for two, then
    coarse_then_fine_reversal's held second. The strobe is a hazard; the stretch after it is held on its own."""
    if i < 30:
        return strobe(4)(i, w, h)
    if i < 90:
        return np.zeros((h, w, 3), np.uint8)
    return coarse_then_fine_reversal(i - 60, w, h)


def _band_and_checker(band, chk):
    """Review 1, round 6 (sb_b*_c*): per field, a 0.44-degree checker over its top `chk` inverting in place and one
    lit band (`band` of the field) jumping between three places below it, 8 cuts a second."""
    def frame(i, w, h):
        b, cb = round(FH * band), round(FH * chk)
        y = _field_rows(h)
        k = i * 8 // 30
        img = np.zeros((h, w, 3), np.uint8)
        img[(y < cb)[:, None] & (_checker4(w, h) ^ bool(k % 2))] = 255
        pos = k % 3
        img[(y >= cb + pos * b) & (y < cb + (pos + 1) * b)] = 255
        return img
    return frame


def _reversing_until(k):
    """Review 1, round 6 (ip_b3_k6): coarse_then_fine_reversal with the checker reversing k times at 15 Hz."""
    def frame(i, w, h):
        y = _field_rows(h)
        img = np.zeros((h, w, 3), np.uint8)
        j = i - 30
        if 0 <= j < 6 and j % 2 == 0:
            img[y < 48] = 255
        flips = 0 if j < 7 else min(k, (j - 7) // 2 + 1)
        img[((y >= 48) & (y < 160))[:, None] & (_checker4(w, h) ^ bool(flips % 2))] = 255
        return img
    return frame


@pytest.mark.parametrize("name,frame,seconds", [
    ("strobe_then_held", full_4hz_then_held, 5.0),
    ("sb_b22_c08", _band_and_checker(0.22, 0.08), 3.0),
    ("ip_b3_k6", _reversing_until(6), 3.0),
])
def test_a_failing_flash_beside_a_held_stretch_stays_a_fail_and_cannot_be_cleared(tmp_path, name, frame, seconds):
    # Review 1, round 6, blocking 1: a fail with a held stretch outside it became a whole-check hold, and
    # `review clear-flash` then passed the hazard. A failing interval keeps the check a fail; only a pure hold is held.
    from src.telegram_bot import TelegramApprovalBot
    master = clip(tmp_path / f"{name}_master.mp4", frame, seconds=seconds)
    report, checks = gate(master)
    flash = checks["picture_flash"]
    assert flash["status"] == "fail" and flash["intervals"], flash
    assert not flash.get("hold") and not flash.get("held") and not release.held_flash(report)
    assert "picture_flash" in {c["name"] for c in release.blocking_checks(report, dry_run=True)}
    card = TelegramApprovalBot.release_lines({**report, "dry_run": False,
                                              "checks": [c for c in report["checks"] if c["name"] == "picture_flash"]})
    assert "blocked" in card and "held: possible flash" not in card
    with pytest.raises(ValueError, match="cannot be cleared"):
        release.clear_flash_hold(tmp_path, report, by="test")
    assert release.flash_failure(report)
    # A clearance on disk for these bytes (as round 6 could write one) never passes it either.
    sha = checks["picture_measurement"]["measurement"]["master_sha256"]
    (tmp_path / release.FLASH_REVIEW).write_text(json.dumps({"master_sha256": sha, "held": flash["intervals"],
                                                             "cleared_at": "2026-11-12T00:00:00+00:00"}),
                                                 encoding="utf-8")
    report, checks = gate(master)
    assert checks["picture_flash"]["status"] == "fail" and not release.held_flash(report)


def test_a_recorded_fail_with_a_held_stretch_is_put_back_to_the_fail(tmp_path):
    # A report measured by round 6 keeps a failing check marked as held (hold, held, needs_review after its
    # clearance): it is judged as the fail it is, and the operator's clearance cannot be written for it.
    picture = {"master_sha256": "a" * 64, "checks": [
        {"name": "picture_flash", "status": "pass", "detail": "Possible flash at 0:02.3-0:04.0: you looked at it.",
         "held_detail": "The picture flashes up to 3.5 times in a second at 0:00.0-0:01.1 ...",
         "intervals": [{"start": 0.0, "end": 1.1}], "hold": True, "held": [{"start": 2.3, "end": 3.97}],
         "cleared": {"cleared_at": "2026-11-12"}, "minor": True, "note": True}]}
    release._flash_hold(picture, tmp_path / "m_master.mp4")
    flash = picture["checks"][0]
    assert flash["status"] == "fail" and flash["detail"].startswith("The picture flashes up to 3.5")
    assert not any(key in flash for key in ("hold", "held", "cleared", "minor", "note"))
    report = {"checks": [{**flash, "status": "needs_review", "hold": True, "held": [{"start": 2.3, "end": 3.97}]},
                         {"name": "picture_measurement", "status": "pass",
                          "measurement": {"master_sha256": "a" * 64}}]}
    assert not release.held_flash(report)
    with pytest.raises(ValueError, match="cannot be cleared"):
        release.clear_flash_hold(tmp_path, report, by="test")
    assert not (tmp_path / release.FLASH_REVIEW).exists()


def checker_over(share, red=False):
    """Review 1, round 6, blocking 2 (chk52_8 ...): per field, a 0.44-degree checker (4 px of 270) over its top
    `share` reversing in place 8 times a second; at each reversal half of it rises and half falls."""
    def frame(i, w, h):
        y = _field_rows(h)
        k = i * 8 // 30
        img = np.zeros((h, w, 3), np.uint8)
        img[(y < round(FH * share))[:, None] & (_checker4(w, h) ^ bool(k % 2))] = (255, 0, 0) if red else 255
        return img
    return frame


@pytest.mark.parametrize("name,frame", [
    ("chk52_8", checker_over(0.52)),
    ("chk56_8", checker_over(0.56)),
    ("chk60_8", checker_over(0.60)),
    ("chk52_red", checker_over(0.52, red=True)),
    ("sb_b18_c16", _band_and_checker(0.18, 0.16)),
])
def test_a_checker_of_squares_over_a_tenth_of_a_degree_counts_its_whole_area(tmp_path, name, frame):
    # Review 1, round 6, blocking 2: 26-30% of the field rises and 26-30% falls at every reversal, 8 a second; the
    # fine stage's 0.1-degree boxes straddling the squares' edges read as unchanged, so only about 24% was counted
    # and round 6 reported "3 flashes".
    check = flash_check(clip(tmp_path / f"{name}.mp4", frame))
    assert check["status"] == "fail", check["detail"]
