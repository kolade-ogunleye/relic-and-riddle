"""QF-11: two-pass mastering (one measurement of the unlimited mix, its gain applied ahead of the limiter) and the
release gate's sound checks: a dropout to silence blocks, a phone-speaker loss is a note, nothing unmeasured
passes, and a measured master is not decoded again."""

import subprocess
from pathlib import Path

import pytest

from src import config, release
from src import video_composer as vc

SR = 48000
VOICE = ("aevalsrc='0.25*sin(2*PI*180*t)*(0.55+0.45*sin(2*PI*3.1*t))"
         "+0.12*sin(2*PI*1250*t)*(0.5+0.5*sin(2*PI*4.3*t))':s=48000:d={d}")
BED = "anoisesrc=color=pink:amplitude=0.03:seed=7:r=48000:d={d}"


def _audio(path: Path, graph: str) -> Path:
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-y", "-filter_complex", graph,
                    "-ac", "2", "-ar", str(SR), str(path)], check=True, timeout=60)
    return path


def master(path: Path, kind: str = "good", seconds: float = 6.0) -> Path:
    """A small master: a voice-like programme over a quiet bed, AAC 192k in an MP4 with a picture."""
    voice, bed = VOICE.format(d=seconds), BED.format(d=seconds)
    shape = {
        "good": "",
        "lead": ",adelay=130|130,atrim=end={d}",                          # a real master's 0.13 s silent start
        "dropout": ",volume=enable='between(t,2.5,3.3)':volume=0",        # 0.8 s of nothing inside
        "tail": ",atrim=end={e},apad=pad_dur=1.3",                       # the last 1.3 s silent
        "subbass": "",
    }[kind].format(d=seconds, e=seconds - 1.3)
    mix = f"{voice}[v];{bed}[b];[v][b]amix=inputs=2:normalize=0{shape}"
    if kind == "subbass":   # a 50 Hz drone over 2-6 s carries most of the loudness there (a phone cannot play it)
        mix = (f"{voice}[v];{bed}[b];aevalsrc='0.9*sin(2*PI*50*t)*between(t,2,6)':s=48000:d={seconds}[s];"
               "[v][b][s]amix=inputs=3:normalize=0")
    wav = _audio(path.with_suffix(".wav"), mix)
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=size=160x240:rate=30:duration={seconds}", "-i", str(wav), "-shortest",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-ar", str(SR), "-ac", "2", str(path)], check=True, timeout=60)
    return path


def gate(path):
    report = release.write_release_report({"master_video_path": str(path), "script_payload": {}}, dry_run=True)
    return report, {check["name"]: check for check in report["checks"]}


def test_good_master_measures_every_sample_and_passes_with_its_figures(tmp_path):
    _, checks = gate(master(tmp_path / "good.mp4"))
    assert checks["audio_measurement"]["status"] == "pass"
    assert checks["audio_dropout"]["status"] == "pass" and checks["audio_phone"]["status"] == "pass"
    assert not checks["audio_phone"].get("minor")
    m = checks["audio_measurement"]["measurement"]
    assert abs(m["decoded_samples_per_channel"] - 6 * SR) <= 2048     # every sample (AAC priming aside)
    for key in ("integrated_lufs", "true_peak_dbtp", "loudness_range_lu", "max_momentary_lufs",
                "max_short_term_lufs", "peak_to_loudness_lu"):
        assert isinstance(m[key], float), key
    assert m["loudness_range_applicable"] is False                    # EBU R 128: not under a minute
    assert m["phone"]["band_hz"] == [config.AUDIO_QA_PHONE_LOW_HZ, config.AUDIO_QA_PHONE_HIGH_HZ]
    assert m["phone"]["worst_window_loss_lu"] is not None and m["phone"]["windows"] > 0
    assert {"peak_dbfs", "rms_dbfs", "flat_factor", "noise_floor_dbfs"} <= set(m["astats"])
    assert m["sibilance"]["worst_momentary_rel_lu"] is not None


def test_a_short_silent_start_like_the_real_masters_is_not_a_dropout(tmp_path):
    _, checks = gate(master(tmp_path / "lead.mp4", "lead"))
    assert checks["audio_dropout"]["status"] == "pass"


@pytest.mark.parametrize("kind", ["dropout", "tail"])
def test_sound_that_drops_to_silence_blocks_the_release(tmp_path, kind):
    report, checks = gate(master(tmp_path / f"{kind}.mp4", kind))
    dropout = checks["audio_dropout"]
    assert dropout["status"] == "fail" and dropout["intervals"]
    span = dropout["intervals"][0]
    assert span["end"] - span["start"] >= (0.7 if kind == "dropout" else 1.2)
    assert "audio_dropout" in {c["name"] for c in release.blocking_checks(report, dry_run=True)}
    problem = next(p for p in release.release_problems(report, dry_run=True) if p.startswith("audio_dropout:"))
    assert "silence" in problem and "0:0" in problem


def test_sound_a_phone_cannot_play_is_a_note_never_a_block(tmp_path):
    report, checks = gate(master(tmp_path / "subbass.mp4", "subbass"))
    phone = checks["audio_phone"]
    assert phone["status"] == "pass" and phone.get("minor") is True and phone["intervals"]
    worst = checks["audio_measurement"]["measurement"]["phone"]["worst_window_loss_lu"]
    assert worst > config.AUDIO_QA_PHONE_LOSS_LU
    assert "audio_phone" not in {c["name"] for c in release.blocking_checks(report, dry_run=False)}
    assert "phone" in phone["detail"] and "Not blocking" in phone["detail"]


def test_a_master_that_cannot_be_measured_fails_closed(tmp_path):
    path = tmp_path / "corrupt.mp4"
    path.write_bytes(b"not a video")
    _, checks = gate(path)
    assert checks["audio_measurement"]["status"] == "fail"
    assert "audio_dropout" not in checks and "audio_phone" not in checks


def test_a_decode_past_its_time_limit_fails_closed(tmp_path, monkeypatch):
    path = master(tmp_path / "good.mp4")
    monkeypatch.setattr(config, "AUDIO_QA_TIMEOUT_BASE_S", 0.0)
    monkeypatch.setattr(config, "AUDIO_QA_TIMEOUT_PER_S", 0.00001)
    _, checks = gate(path)
    assert checks["audio_measurement"]["status"] == "fail"
    assert "tried again at the next check" in checks["audio_measurement"]["detail"]


def test_a_measured_master_is_not_decoded_again_unless_its_bytes_or_method_change(tmp_path, monkeypatch):
    from src import audio_qa
    path = master(tmp_path / "good.mp4")
    gate(path)
    calls = []
    real = audio_qa.measure
    monkeypatch.setattr(audio_qa, "measure", lambda *a, **k: calls.append(1) or real(*a, **k))
    _, checks = gate(path)
    assert calls == [] and checks["audio_measurement"]["measurement"]["reused_from"]
    monkeypatch.setattr(config, "AUDIO_QA_PHONE_LOSS_LU", 9.0)        # a changed threshold is measured again
    gate(path)
    assert calls == [1]


def _counted(monkeypatch):
    decodes = []
    real = vc._run_ffmpeg
    monkeypatch.setattr(vc, "_run_ffmpeg", lambda cmd: decodes.append(cmd) or real(cmd))
    return decodes


def _programme(tmp_path, bed=BED):
    vo = _audio(tmp_path / "vo.wav", VOICE.format(d=6.0).replace("0.25*", "0.08*").replace("0.12*", "0.04*"))
    return vo, _audio(tmp_path / "bed.wav", bed.format(d=7.0))


def test_mastering_measures_the_mix_once_and_lands_on_target(tmp_path, monkeypatch):
    vo, bed = _programme(tmp_path)
    decodes = _counted(monkeypatch)
    composer = vc.VideoComposer()
    gain = composer.calibrate_gain(vo, bed, end=6.0)
    assert len(decodes) == 1                                           # the old search decoded the mix twice
    record = composer.last_mastering
    assert record["analysis_passes"] == 1 and record["verified"] == []
    assert record["limiter_cost_bound_lu"] <= vc.MASTER_TOLERANCE_LU
    integrated, peak = vc.VideoComposer._measure(vo, bed, vc._audio_chain(0, 1, end=6.0) + vc._master_chain(gain))
    assert abs(integrated - vc.TARGET_LUFS) <= vc.MASTER_TOLERANCE_LU and peak <= vc.TARGET_TRUE_PEAK
    before = len(decodes)
    composer.calibrate_gain(vo, bed, end=6.0)                          # a re-render reuses the measured gain
    assert len(decodes) == before and composer.last_mastering["reused"] is True


def test_mastering_verifies_when_the_limiter_could_cost_more_than_the_tolerance(tmp_path, monkeypatch):
    # a bed of loud clicks every 50 ms: the limiter works on every block, so the static gain is checked
    clicks = "aevalsrc='0.9*lt(mod(t,0.05),0.0006)':s=48000:d={d}"
    vo, bed = _programme(tmp_path, clicks)
    decodes = _counted(monkeypatch)
    composer = vc.VideoComposer()
    gain = composer.calibrate_gain(vo, bed, end=6.0)
    record = composer.last_mastering
    assert record["limiter_cost_bound_lu"] > vc.MASTER_TOLERANCE_LU
    assert record["verified"] and len(decodes) == record["analysis_passes"] >= 2
    integrated, _ = vc.VideoComposer._measure(vo, bed, vc._audio_chain(0, 1, end=6.0) + vc._master_chain(gain))
    assert abs(integrated - vc.TARGET_LUFS) <= vc.MASTER_TOLERANCE_LU
    # the estimate is never below what the limiter really took at the first gain
    assert record["verified"][0]["integrated_lufs"] >= vc.TARGET_LUFS - record["limiter_cost_bound_lu"] - 0.1


def test_the_end_card_level_takes_one_decode_and_reads_as_two_did(tmp_path, monkeypatch):
    vo, bed = _programme(tmp_path)
    chain = vc._audio_chain(0, 1, 1.0, end=7.0) + vc._master_chain(2.0)
    body, _ = vc.VideoComposer._measure(vo, bed, chain + ",atrim=end=6.000")
    card, _ = vc.VideoComposer._measure(vo, bed, chain + ",atrim=start=6.000,asetpts=PTS-STARTPTS")
    decodes = _counted(monkeypatch)
    level = vc.VideoComposer().end_card_level(vo, bed, 6.0, 1.0, 2.0)
    assert len(decodes) == 1
    assert (level["body_lufs"], level["card_lufs"]) == (round(body, 1), round(card, 1))



# The detector's log as each FFmpeg prints it, for the "dropout" master (0.8 s of nothing at 2.5-3.3 s): recorded
# with FFmpeg 8.1, whose silencedetect logs as "[silencedetect@gap @ ...]"; and the same decode with the lines that
# FFmpeg 6.1 to 8.0 print instead ("[silencedetect @ ...]", av_log on the filter's private context; 6.1 lines as
# recorded from a 6.1.2 build). CI (Ubuntu FFmpeg 8.0.1) read no silence from the second form and passed the master.
RECORDED = Path(__file__).parent / "fixtures" / "qf11"
DROPOUT = {"duration": 6.0, "rate": SR, "channels": 2}


@pytest.mark.parametrize("log", ["dropout_ffmpeg-8.1.log", "dropout_ffmpeg-6.1-8.0.log"])
def test_the_dropout_is_read_from_every_ffmpeg_form_of_the_detector_log(log):
    from src import audio_qa
    found = audio_qa.parse((RECORDED / log).read_text(encoding="utf-8"), **DROPOUT)
    dropout = next(c for c in found["checks"] if c["name"] == "audio_dropout")
    assert dropout["status"] == "fail"
    assert [(round(s["start"], 2), round(s["end"], 2)) for s in dropout["intervals"]] == [(2.52, 3.31)]


@pytest.mark.parametrize("log", ["dropout_ffmpeg-8.1.log", "dropout_ffmpeg-6.1-8.0.log"])
def test_an_unread_detector_fails_the_measurement_instead_of_passing(log, tmp_path, monkeypatch):
    from src import audio_qa
    text = (RECORDED / log).read_text(encoding="utf-8")
    unread = "\n".join(line for line in text.splitlines() if "silence_" not in line)   # a form nothing parses
    with pytest.raises(ValueError, match="silence detector reported no silence at 0:02"):
        audio_qa.parse(unread, **DROPOUT)
    peakless = "\n".join(line.split("FTPK:")[0] for line in text.splitlines())        # no true peaks to check by
    with pytest.raises(ValueError, match="no frame true peaks"):
        audio_qa.parse(peakless, **DROPOUT)
    # through the release gate: the master's own decode returns that log, and the gate fails closed
    path = master(tmp_path / "dropout.mp4", "dropout")
    real = audio_qa.subprocess.run

    def run(cmd, *a, **k):
        res = real(cmd, *a, **k)
        if cmd[0] == "ffmpeg":
            res.stderr = "\n".join(line for line in res.stderr.splitlines() if "silence_" not in line)
        return res
    monkeypatch.setattr(audio_qa.subprocess, "run", run)
    _, checks = gate(path)
    assert checks["audio_measurement"]["status"] == "fail" and "audio_dropout" not in checks


def test_a_reported_silence_the_meter_hears_as_sound_fails_the_measurement():
    from src import audio_qa
    text = (RECORDED / "dropout_ffmpeg-8.1.log").read_text(encoding="utf-8")
    moved = text.replace("silence_start: 2.517354", "silence_start: 0.517354")   # misread: starts in the sound
    with pytest.raises(ValueError, match="where the sound peaks at"):
        audio_qa.parse(moved, **DROPOUT)
