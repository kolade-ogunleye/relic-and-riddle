"""QF-10: the release gate decodes every frame of the master and fails a black, frozen, barred or out-of-range
picture and wrong colour tags; a real dark house graphic is not mistaken for bars; nothing unmeasured passes."""

import json
import subprocess
from pathlib import Path

import pytest

from src import config, picture_qa, release
from src.encoding import BT709_FLAGS, RGB_TO_BT709

FIXTURES = Path(__file__).parent / "fixtures" / "qf10"
# Wieliczka's depth-comparison graphic as the Reel shows it at 25 s (run_wieliczka_salt_mine_1792974672): the house
# dark ground, the bars and their labels in the middle, the caption below. Sampled on the gate's own 270 x 480 grid.
WIELICZKA = FIXTURES / "wieliczka_depth_graphic_25s.png"
PICTURE = {"picture_colour", "picture_black", "picture_freeze", "picture_bars", "picture_range"}


def make(path, kind):
    source = {
        "good": "testsrc2=size=160x240:rate=30:duration=1.2",
        "black": "color=black:size=160x240:rate=30:duration=1.2",
        "freeze": "color=orange:size=160x240:rate=30:duration=6.4",
        "bars": "testsrc2=size=144x220:rate=30:duration=1.2,pad=160:240:8:10:black",
        "range": "nullsrc=size=160x240:rate=30:duration=1.2,geq=lum='mod(2*X+N,256)':cb=128:cr=128",
        "ringing": "nullsrc=size=160x240:rate=30:duration=1.2,geq=lum='if(lt(X,80),14,237)':cb=128:cr=128",
        "colour": "testsrc2=size=160x240:rate=30:duration=1.2",
    }[kind]
    tags = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
    if kind == "colour":
        tags = ["-colorspace", "smpte170m", "-color_primaries", "smpte170m", "-color_trc", "smpte170m"]
    matrix = "smpte170m" if kind == "colour" else "bt709"
    source += f",setparams=range=tv:colorspace={matrix}:color_primaries={matrix}:color_trc={matrix}"
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-y", "-f", "lavfi", "-i", source,
                    "-c:v", "libx264", "-threads", "1", "-preset", "ultrafast", "-crf", "0", "-pix_fmt", "yuv420p",
                    *tags, str(path)], check=True, timeout=60)
    return path


def still(path, image, extra=""):
    """A real frame as the composer encodes an RGB picture (BT.709, VIDEO_CRF), held 1.5 s."""
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-y", "-loop", "1", "-framerate", "30",
                    "-t", "1.5", "-i", str(image), "-vf", f"{extra}{RGB_TO_BT709}", "-c:v", "libx264", "-threads",
                    "1", "-preset", "fast", "-crf", str(config.VIDEO_CRF), *BT709_FLAGS, "-pix_fmt", "yuv420p",
                    str(path)], check=True, timeout=60)
    return path


HALFTONE = ("nullsrc=size=320x480:rate=30:duration=1.2,geq=lum='if(gt(sin(X*1.3+N*0.2)*sin(Y*1.3),"
            "0.6*sin(X/37)*cos(Y/29)),235,16)':cb=128:cr=128")


def halftone(path):
    """Fine black-and-white dots, legal 16/235 only, encoded lossy at VIDEO_CRF: the encoder rings beyond the range
    beside every dot edge (about 25% of samples), as encoders do on a real halftone photo."""
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-y", "-f", "lavfi", "-i",
                    HALFTONE + ",setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709",
                    "-c:v", "libx264", "-threads", "1", "-preset", "fast", "-crf", str(config.VIDEO_CRF),
                    "-pix_fmt", "yuv420p", *BT709_FLAGS, str(path)], check=True, timeout=60)
    return path


def full_range(path, image):
    """A real picture converted to full-range YUV but tagged limited (the defect picture_range exists for)."""
    full = "scale=out_color_matrix=bt709:out_range=pc,format=yuv420p,setparams=range=tv:colorspace=bt709"
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-y", "-loop", "1", "-framerate", "30",
                    "-t", "1.5", "-i", str(image), "-vf", full + ":color_primaries=bt709:color_trc=bt709",
                    "-c:v", "libx264",
                    "-threads", "1", "-preset", "fast", "-crf", str(config.VIDEO_CRF), *BT709_FLAGS,
                    "-pix_fmt", "yuv420p", str(path)], check=True, timeout=60)
    return path


def gate(path):
    report = release.write_release_report({"master_video_path": str(path), "script_payload": {}}, dry_run=True)
    return report, {check["name"]: check for check in report["checks"]}


@pytest.mark.parametrize("kind", ["black", "freeze", "bars", "range", "colour"])
def test_release_rejects_each_deliberately_broken_master(tmp_path, kind):
    report, checks = gate(make(tmp_path / f"{kind}.mp4", kind))
    assert checks[f"picture_{kind}"]["status"] == "fail"
    assert checks["picture_measurement"]["status"] == "pass"
    assert f"picture_{kind}" in {check["name"] for check in release.blocking_checks(report, dry_run=True)}
    # The card shows what was found, not "no detail recorded" (check_problems reads `detail`).
    problem = next(p for p in release.release_problems(report, dry_run=True) if p.startswith(f"picture_{kind}:"))
    assert "no detail recorded" not in problem and len(problem) > len(f"picture_{kind}: ") + 10


def test_good_moving_master_passes_every_picture_check_and_reports_only_measured_ones(tmp_path):
    _, checks = gate(make(tmp_path / "good.mp4", "good"))
    picture = {name: c for name, c in checks.items() if name.startswith("picture_")}
    # No SSIM line: with no reference picture there is nothing to compare, and an unmeasured check is not a pass.
    assert set(picture) == PICTURE | {"picture_measurement"}
    assert all(c["status"] == "pass" for c in picture.values())
    measured = picture["picture_measurement"]["measurement"]
    assert measured["decoded_frames"] == 36 and measured["stride"] == 1


def test_real_dark_house_graphic_is_not_bars_but_the_same_picture_letterboxed_is(tmp_path):
    graphic = picture_qa.measure(still(tmp_path / "graphic.mp4", WIELICZKA))
    status = {c["name"]: c["status"] for c in graphic["checks"]}
    assert status["picture_bars"] == "pass", graphic["checks"]
    assert all(status[n] == "pass" for n in ("picture_black", "picture_freeze", "picture_range"))
    # The same dark picture shrunk into flat code-16 padding top and bottom: real letterboxing, which must fail.
    boxed = picture_qa.measure(still(tmp_path / "boxed.mp4", WIELICZKA, "scale=270:380,pad=270:480:0:50:black,"))
    bars = next(c for c in boxed["checks"] if c["name"] == "picture_bars")
    assert bars["status"] == "fail" and bars["intervals"][0]["end"] - bars["intervals"][0]["start"] >= 1.0


def test_failed_decoder_never_becomes_a_pass(tmp_path):
    path = tmp_path / "corrupt.mp4"
    path.write_bytes(b"not a video")
    _, checks = gate(path)
    assert checks["picture_measurement"]["status"] == "fail"
    assert not PICTURE & set(checks)


def test_a_decode_past_its_time_limit_fails_closed(tmp_path, monkeypatch):
    path = make(tmp_path / "good.mp4", "good")
    monkeypatch.setattr(config, "PICTURE_QA_TIMEOUT_BASE_S", 0.0)
    monkeypatch.setattr(config, "PICTURE_QA_TIMEOUT_PER_S", 0.0)
    _, checks = gate(path)
    assert checks["picture_measurement"]["status"] == "fail"
    assert "did not finish" in checks["picture_measurement"]["detail"]
    assert not PICTURE & set(checks)


def test_the_time_limit_grows_with_the_programme():
    assert picture_qa.timeout_s(360) > picture_qa.timeout_s(50) * 4
    assert picture_qa.timeout_s(360) >= 360 * config.PICTURE_QA_TIMEOUT_PER_S


def test_frame_count_mismatch_is_refused(tmp_path, monkeypatch):
    path = make(tmp_path / "good.mp4", "good")
    real = picture_qa._probe
    monkeypatch.setattr(picture_qa, "_probe", lambda p: (*real(p)[:4], real(p)[4] + [99.0]))
    with pytest.raises(ValueError, match="Decoded 36 frames"):
        picture_qa.measure(path)


def test_two_code_codec_ringing_is_not_mistaken_for_full_range(tmp_path):
    report = picture_qa.measure(make(tmp_path / "ringing.mp4", "ringing"))
    assert next(c for c in report["checks"] if c["name"] == "picture_range")["status"] == "pass"


def test_codec_ringing_on_a_halftone_passes_but_a_full_range_picture_tagged_limited_fails(tmp_path):
    ringing = picture_qa.measure(halftone(tmp_path / "halftone.mp4"))
    assert next(c for c in ringing["checks"] if c["name"] == "picture_range")["status"] == "pass"
    real = picture_qa.measure(full_range(tmp_path / "full.mp4", WIELICZKA))
    assert next(c for c in real["checks"] if c["name"] == "picture_range")["status"] == "fail"


def test_measurement_is_reused_only_for_the_same_bytes_and_method(tmp_path, monkeypatch):
    path = make(tmp_path / "black.mp4", "black")
    first, _ = gate(path)
    calls = []
    real = picture_qa._decode
    monkeypatch.setattr(picture_qa, "_decode", lambda *a: calls.append(1) or real(*a))
    _, checks = gate(path)
    assert calls == [] and checks["picture_black"]["status"] == "fail"
    assert checks["picture_measurement"]["measurement"]["reused_from"] == first["checked_at"]
    monkeypatch.setattr(config, "PICTURE_QA_BAR_LEVEL", config.PICTURE_QA_BAR_LEVEL + 1)
    gate(path)
    assert calls == [1]
    make(path, "good")   # new bytes under the same name
    _, checks = gate(path)
    assert calls == [1, 1] and checks["picture_black"]["status"] == "pass"


def test_runs_and_freeze_intervals_close_at_the_end_of_the_programme():
    runs = picture_qa._Runs(0.1)
    for t, holds in ((0.0, False), (0.7, True), (0.9, True)):
        runs.frame(t, holds)
    assert runs.finish(1.0) == [{"start": 0.7, "end": 1.0}]
    short = picture_qa._Runs(0.1)
    short.frame(0.0, True)
    short.frame(1 / 30, False)
    assert short.finish(1.0) == []


def test_release_report_stays_json(tmp_path):
    path = make(tmp_path / "good.mp4", "good")
    gate(path)
    json.loads((tmp_path / "release_report.json").read_text(encoding="utf-8"))


# --- Round 5: QF-52 essential images shown whole, and masters made before the colour fix -------------------------

PHOTO = Path(__file__).parent.parent / "docs/reviews/end-card-2026-10-25/voynich_timeline.jpg"   # a real landscape


def essential_then_letterboxed(path, w=270, h=480):
    """An essential image shown whole through the composer's own chain (image_presentation.fit_filter padding, the
    film grade and lens vignette of picture_chain, VIDEO_CRF), 1.5 s; then a picture shrunk into flat padding, 1.5 s
    (true letterboxing). Concatenated as the composer joins scenes, graded once over both."""
    from src.image_presentation import fit_filter
    from src.video_composer import film_grade_filter, picture_chain
    graph = (f"[0:v]{fit_filter(PHOTO, w, h)},fps=30,setsar=1,{RGB_TO_BT709}[a];"
             f"[1:v]scale={w}:{h * 3 // 4},pad={w}:{h}:0:{h // 8}:black,fps=30,setsar=1,{RGB_TO_BT709}[b];"
             f"[a][b]concat=n=2:v=1:a=0,{picture_chain(film_grade_filter(), w, h)}[v]")
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-y",
                    "-loop", "1", "-framerate", "30", "-t", "1.5", "-i", str(PHOTO),
                    "-loop", "1", "-framerate", "30", "-t", "1.5", "-i", str(WIELICZKA),
                    "-filter_complex", graph, "-map", "[v]", "-c:v", "libx264", "-threads", "1", "-preset", "fast",
                    "-crf", str(config.VIDEO_CRF), *BT709_FLAGS, "-pix_fmt", "yuv420p", str(path)],
                   check=True, timeout=120)
    return path


def compose_record(master, **extra):
    from src.credits import sha256_file
    Path(master).with_suffix(".composition.json").write_text(
        json.dumps({"master_sha256": sha256_file(master), **extra}), encoding="utf-8")


def test_essential_image_padding_is_not_bars_but_letterboxing_after_it_still_is(tmp_path):
    master = essential_then_letterboxed(tmp_path / "m.mp4")
    compose_record(master, full_image_spans=[[0.0, 1.5]])
    _, checks = gate(master)
    bars = checks["picture_bars"]
    assert bars["status"] == "fail", bars
    assert all(i["start"] >= 1.5 for i in bars["intervals"]) and "shown whole" in bars["detail"]
    # Without the composition record's spans the essential image's own padding is measured too: it fails from 0 s.
    raw = picture_qa.measure(master)
    assert next(c for c in raw["checks"] if c["name"] == "picture_bars")["intervals"][0]["start"] == 0.0


def test_essential_image_alone_passes_through_the_real_chain(tmp_path):
    master = essential_then_letterboxed(tmp_path / "m.mp4")
    measured = picture_qa.measure(master, exempt_bars=[[0.0, 1.5]])
    bars = next(c for c in measured["checks"] if c["name"] == "picture_bars")
    assert all(i["start"] >= 1.5 for i in bars["intervals"])
    whole = picture_qa.measure(master, exempt_bars=[[0.0, 3.0]])
    assert {c["name"]: c["status"] for c in whole["checks"]}["picture_bars"] == "pass"


def test_full_image_spans_follow_the_designation_and_the_loop_bridge(tmp_path):
    from PIL import Image

    from src.image_presentation import designate, full_image_spans
    plain, essential = tmp_path / "plain.jpg", tmp_path / "essential.jpg"
    for p in (plain, essential):
        Image.new("RGB", (64, 48), (90, 80, 70)).save(p)
    designate(essential, "operator: the inscription must be shown whole")
    assert full_image_spans([plain, essential], [0.0, 4.0], [4.0, 9.5]) == [[4.0, 9.5]]
    assert full_image_spans([plain, essential], [0.0, 4.0], [4.0, 9.5], bridge=(9.5, 12.4)) == [[4.0, 9.5],
                                                                                                [9.5, 12.4]]
    assert full_image_spans([plain, plain], [0.0, 4.0], [4.0, 9.5], bridge=(9.5, 12.4)) == []


def pre_qf01(path, kind="good"):
    """A master as made before QF-01: BT.709 matrix and limited range, primaries and transfer unset."""
    source = {"good": "testsrc2=size=160x240:rate=30:duration=1.2",
              "black": "color=black:size=160x240:rate=30:duration=1.2"}[kind]
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-y", "-f", "lavfi", "-i", source,
                    "-c:v", "libx264", "-threads", "1", "-preset", "ultrafast", "-crf", "0", "-pix_fmt", "yuv420p",
                    "-colorspace", "bt709", "-color_range", "tv", str(path)], check=True, timeout=60)
    return path


def old_report(master):
    (Path(master).parent / "release_report.json").write_text(json.dumps(
        {"schema_version": "1.0", "checks": [{"name": "duration", "status": "pass", "detail": "Measured 1.2s."}]}),
        encoding="utf-8")


def test_master_gated_before_qf10_with_pre_qf01_tags_gets_a_note_not_a_block(tmp_path):
    master = pre_qf01(tmp_path / "m.mp4")
    assert picture_qa.measure(master)["checks"][0]["tags"] == picture_qa.PRE_QF01_TAGS
    compose_record(master)
    old_report(master)
    for _ in range(2):   # the first QF-10 gate grandfathers it; the re-check (reusing the measurement) keeps it
        report, checks = gate(master)
        colour = checks["picture_colour"]
        assert colour["status"] == "pass" and colour["grandfathered"] and "re-render" in colour["detail"]
        assert "picture_colour" not in {c["name"] for c in release.blocking_checks(report)}
        assert not [p for p in release.release_problems(report) if p.startswith("picture_")]
    assert json.loads((tmp_path / "release_report.json").read_text(encoding="utf-8"))["schema_version"] == "1.1"


@pytest.mark.parametrize("case", ["new_render", "never_gated", "gated_by_qf10", "other_tags"])
def test_the_colour_note_is_only_for_masters_gated_before_qf10(tmp_path, case):
    master = (make(tmp_path / "m.mp4", "colour") if case == "other_tags" else pre_qf01(tmp_path / "m.mp4"))
    compose_record(master, **({"full_image_spans": []} if case == "new_render" else {}))
    if case in ("new_render", "other_tags"):
        old_report(master)
    if case == "gated_by_qf10":
        gate(master)   # a QF-10 report without the note: its next check must not grant one
        compose_record(master)
    report, checks = gate(master)
    assert checks["picture_colour"]["status"] == "fail" and not checks["picture_colour"].get("grandfathered")
    assert "picture_colour" in {c["name"] for c in release.blocking_checks(report)}


def test_other_picture_checks_still_block_a_grandfathered_master(tmp_path):
    master = pre_qf01(tmp_path / "m.mp4", "black")
    compose_record(master)
    old_report(master)
    report, checks = gate(master)
    assert checks["picture_colour"]["status"] == "pass"
    assert "picture_black" in {c["name"] for c in release.blocking_checks(report)}
    with pytest.raises(ValueError, match="picture_black"):
        release.require_release({"master_video_path": str(master), "script_payload": {}})


def test_any_measurement_error_is_a_failed_check_not_a_crash(tmp_path, monkeypatch):
    master = make(tmp_path / "good.mp4", "good")
    monkeypatch.setattr(picture_qa, "measure", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    _, checks = gate(master)
    assert checks["picture_measurement"]["status"] == "fail" and "boom" in checks["picture_measurement"]["detail"]
