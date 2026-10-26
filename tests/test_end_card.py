"""The Reel's end card (operator-approved "capsule v3", 2026-10-25): appended after the narration, inside the safe
area, its dim measured on each frame, its outline drawn the same from either side, its sound in the bed and
mastered with the programme, and recorded in the run so the audience numbers can be compared."""
import json
import shutil

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import end_card
from src.safe_zones import REEL
from src.script_engine import (SHARE_FALLBACK, end_card_share, is_stock_share, share_line_problems,
                               share_repeats_narration)

LINE = "Send this to the codebreaker you know, and follow for the next relic."


def test_the_card_shows_the_share_half_of_the_line():
    assert end_card.card_text(LINE) == "Send this to the codebreaker you know."
    assert end_card.card_text("Pass this on to a map lover. Follow for the next one!") == "Pass this on to a map lover."
    assert end_card.card_text("Tag the engineer in your life — and follow along") == "Tag the engineer in your life."
    assert end_card.card_text("Send it to a stonemason") == "Send it to a stonemason."


def test_stock_phrasing_is_rejected_and_a_story_specific_line_is_not():
    for stock in ("Share this with a friend, and follow for more.", "Send this to someone who loves history, and follow us.",
                  "Tag a friend who needs to see this and follow for the next one.", SHARE_FALLBACK):
        assert is_stock_share(stock), stock
        assert any("stock phrasing" in p for p in share_line_problems(stock)), stock
    for specific in (LINE, "Send this to the friend who'd spot the next one, and follow for the next relic.",
                     "Send this to a friend who loves ancient engineering, and follow for the next relic."):
        assert not is_stock_share(specific) and share_line_problems(specific) == [], specific


def test_a_share_ask_the_narration_already_made_is_not_repeated_on_the_card():
    narration = "The script was never read. So send this to the codebreaker you know."
    assert share_repeats_narration(LINE, narration)
    assert not share_repeats_narration(LINE, "The script was never read. Nobody has cracked it.")
    text, fallback = end_card_share({"share_line": LINE, "narration_script": narration})
    assert fallback and text == end_card.card_text(SHARE_FALLBACK)


def test_the_fallback_is_flagged_and_noted_on_the_review_card():
    text, fallback = end_card_share({"share_line": "Share this with a friend and follow."})
    assert fallback and text == end_card.card_text(SHARE_FALLBACK)
    assert end_card_share({"share_line": LINE, "narration_script": "It was never read."}) == \
        ("Send this to the codebreaker you know.", False)
    note = end_card.end_card_note({"variant": end_card.VARIANT, "text": text, "fallback": True})
    assert note["severity"] == "info" and note["card"] and text in note["issue"]
    assert end_card.end_card_note({"variant": end_card.VARIANT, "text": text, "fallback": False}) is None


@pytest.mark.parametrize("share", ["Send this to the codebreaker you know.",
                                   "Send this to the friend who still believes the old maps hid a whole continent "
                                   "beneath the ice of the southern ocean."])
def test_everything_stays_inside_the_safe_area(share):
    lay = end_card.layout(share)
    x0, y0, x1, y1 = REEL.box(end_card.WIDTH, end_card.HEIGHT)
    assert lay["max_width"] == REEL.centred_width()
    assert max(lay["share_widths"]) <= lay["max_width"] and len(lay["lines"]) <= 5
    box = lay["box"]
    assert x0 <= box[0] and box[2] <= x1 and y0 <= end_card.TOP - 120 and box[3] <= y1   # clear of the right rail
    assert lay["follow_font"].size >= 30


def _busy(seed=3):
    rng = np.random.default_rng(seed)
    arr = np.full((1920, 1080, 3), 200, np.uint8)
    im = Image.fromarray(arr)
    d = ImageDraw.Draw(im)
    for _ in range(900):   # handwriting-like strokes over a light page
        x, y = rng.integers(0, 1080), rng.integers(0, 1920)
        d.line([(x, y), (x + rng.integers(10, 60), y + rng.integers(-8, 8))], fill=(60, 40, 30), width=4)
    return im


@pytest.mark.parametrize("frame", [Image.new("RGB", (1080, 1920), (250, 250, 245)), _busy()], ids=["bright", "busy"])
def test_the_dim_meets_its_targets_on_a_bright_and_a_busy_frame(frame):
    shape, soften, stats = end_card.fit_dim(frame)
    assert stats["band"] <= end_card.TARGETS["band"] and stats["band_std"] <= end_card.TARGETS["band_std"]
    assert stats["above"] <= end_card.TARGETS["outside"] and stats["below"] <= end_card.TARGETS["outside"]
    assert shape.shape == (1920, 1)


def test_a_busy_frame_is_softened_and_a_plain_one_is_not():
    assert end_card.fit_dim(_busy())[2]["soften"] > 0.3
    assert end_card.fit_dim(Image.new("RGB", (1080, 1920), (120, 140, 170)))[2]["soften"] == 0.0


def test_the_finished_capsule_outline_is_symmetric():
    # Operator, 2026-10-25: the two traced halves did not meet (one edge a pixel higher, a step at the centre),
    # from PIL's wide lines being offset by their drawing direction.
    lay = end_card.layout("Send this to the codebreaker you know.")
    layer = Image.new("RGBA", (end_card.WIDTH, end_card.HEIGHT), (0, 0, 0, 0))
    end_card._Outline(lay["box"]).draw(layer, 1.0)
    alpha = np.asarray(layer, dtype=np.float32)[..., 3]
    assert alpha.max() > 200
    assert np.abs(alpha - np.flip(alpha, axis=1)).mean() < 0.05                 # left and right mirror
    centre = alpha[:, end_card.WIDTH // 2 - 40:end_card.WIDTH // 2 + 40]          # the straight runs
    mid = int((lay["box"][1] + lay["box"][3]) / 2)
    top, bottom = centre[:mid].sum(axis=0) / 255, centre[mid:].sum(axis=0) / 255
    assert np.allclose(top, bottom, atol=0.05) and abs(top.mean() - end_card.OUTLINE_WIDTH) < 0.5
    # half traced: still mirror symmetric (both halves run at once)
    half = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    end_card._Outline(lay["box"]).draw(half, 0.5)
    a2 = np.asarray(half, dtype=np.float32)[..., 3]
    assert np.abs(a2 - np.flip(a2, axis=1)).mean() < 0.05


def test_the_card_runs_its_length_and_starts_on_the_last_frame():
    last = _busy(5)
    frames = list(end_card.frames(last, "Send this to the codebreaker you know.", stats := {}))
    assert len(frames) == round(end_card.SECONDS * end_card.FPS) and frames[0].size == (1080, 1920)
    first = np.asarray(frames[0], dtype=np.float32)
    assert np.abs(first - np.asarray(last, dtype=np.float32)).mean() < 3          # no jump at the seam
    end = np.asarray(frames[-1].convert("L"), dtype=np.float32)
    assert end[int(1920 * 0.26):int(1920 * 0.66)].mean() < 60                     # the card is up
    assert stats["dim"]["band"] <= 30 and stats["follow_size"] >= 30


def test_the_bed_carries_the_card_sound_and_ends_with_it(tmp_path):
    import soundfile as sf
    from src.audio_engine import AudioEngine
    sr, vo = 48000, 4.0
    t = np.arange(int(sr * 12)) / sr
    music = tmp_path / "music.wav"
    sf.write(music, np.column_stack([0.2 * np.sin(2 * np.pi * 220 * t)] * 2), sr)
    eng = AudioEngine()
    bed = eng.generate_ambient_bed(vo + end_card.SECONDS + 0.05, tmp_path / "bed.wav", music_path=music, end_card_at=vo)
    audio, _ = sf.read(bed)
    assert len(audio) / sr == pytest.approx(vo + end_card.SECONDS + 0.05, abs=0.01)
    rms = lambda a, b: float(np.sqrt(np.mean(audio[int(a * sr):int(b * sr)] ** 2)))
    body, held = rms(2.0, 3.5), rms(vo + 0.4, vo + end_card.BELL_AT - 0.2)
    assert held < body and 20 * np.log10(held / body) == pytest.approx(eng.END_CARD_MUSIC_DB, abs=1.5)
    if eng.licensed("sfx_bell_toll.wav"):
        assert rms(vo + end_card.BELL_AT, vo + end_card.BELL_AT + 0.4) > held * 1.3   # the bell lands
        assert any(s["name"] == "sfx_bell_toll.wav" for s in eng.last_sfx_sources)  # in the bed's rights evidence
    assert rms(len(audio) / sr - 0.02, len(audio) / sr) < 0.01                    # faded out with the card


def test_audience_reads_the_card_and_what_it_costs():
    from src.audience import card_retention, end_card_comparison
    graph = {str(i): 1.0 - i * 0.01 for i in range(41)}
    card = {"variant": end_card.VARIANT, "start_s": 46.0, "duration_s": end_card.SECONDS}
    out = card_retention({"post_video_retention_graph": graph}, 48.9, card)
    assert out["card_retention"] == pytest.approx(1.0 - 38 * 0.01) and out["card_drop"] == pytest.approx(0.02)
    assert card_retention({}, 48.9, card) == {"card_retention": None, "card_drop": None}
    rows = [{"run_id": "a", "platform": "instagram", "end_card": end_card.VARIANT, "share_rate": 0.02, "watch_ratio": 0.6,
             "card_drop": 0.02},
            {"run_id": "b", "platform": "instagram", "end_card": None, "share_rate": 0.01, "watch_ratio": 0.65}]
    line = end_card_comparison(rows)
    assert "with it 1 video(s), shares 2.0%" in line and "without 1 video(s), shares 1.0%" in line and "2%" in line
    assert end_card_comparison(rows[:1]) is None


def test_the_run_records_its_end_card(tmp_path):
    master = tmp_path / "m_master.mp4"
    master.with_suffix(".composition.json").write_text(json.dumps({"end_card": {
        "variant": end_card.VARIANT, "start_s": 45.2, "duration_s": end_card.SECONDS, "text": "Send it.", "dim": {}}}))
    assert end_card.end_card_record(master, fallback=True) == {
        "variant": end_card.VARIANT, "start_s": 45.2, "duration_s": end_card.SECONDS, "text": "Send it.", "fallback": True}
    (tmp_path / "n_master.composition.json").write_text(json.dumps({"end_card": None}))
    assert end_card.end_card_record(tmp_path / "n_master.mp4") is None


def test_a_carded_master_passes_the_release_media_checks(tmp_path, monkeypatch):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg is required to render a master")
    import soundfile as sf
    from src import release
    from src.audio_engine import AudioEngine
    from src.delivery_profiles import get_delivery_profile
    from src.video_composer import VideoComposer
    sr, vo_dur = 48000, 3.0
    t = np.arange(int(sr * vo_dur)) / sr
    voice = tmp_path / "voiceover.wav"
    sf.write(voice, 0.3 * np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t)), sr)
    music = tmp_path / "music.wav"
    tm = np.arange(int(sr * 10)) / sr
    sf.write(music, np.column_stack([0.2 * np.sin(2 * np.pi * 110 * tm)] * 2), sr)
    bed = AudioEngine().generate_ambient_bed(vo_dur + end_card.SECONDS + 0.05, tmp_path / "ambient_bed.wav",
                                             music_path=music, end_card_at=vo_dur)
    scenes = []
    for i, colour in enumerate(((180, 160, 120), (90, 110, 150))):
        p = tmp_path / f"s{i}.jpg"
        _busy(i).resize((1200, 2100)).save(p) if i else Image.new("RGB", (1200, 2100), colour).save(p)
        scenes.append(p)
    words = [{"word": "Old", "start": 0.2, "dur": 0.4}, {"word": "maps.", "start": 0.7, "dur": 0.5},
             {"word": "Read", "start": 1.6, "dur": 0.4}, {"word": "them.", "start": 2.1, "dur": 0.5}]
    master = tmp_path / "t_master.mp4"
    VideoComposer().compose_video(scenes, voice, bed, words, vo_dur, master, durations=[1.5, 1.5],
                                  end_card="Send this to the codebreaker you know.")
    manifest = json.loads(master.with_suffix(".composition.json").read_text(encoding="utf-8"))
    assert manifest["end_card"]["variant"] == end_card.VARIANT and manifest["end_card"]["start_s"] == pytest.approx(vo_dur)
    assert not list(tmp_path.glob("*_body.mkv")) and not list(tmp_path.glob("*_programme.wav"))   # passes cleaned up
    import subprocess

    def lufs(*window):
        err = subprocess.run(["ffmpeg", "-nostats", *window, "-i", str(master), "-vn", "-af", "ebur128", "-f", "null", "-"],
                             capture_output=True, text=True).stderr
        return float(err[err.rfind("Summary:"):].split("I:")[1].split("LUFS")[0])
    gap = lufs("-t", f"{vo_dur:.2f}") - lufs("-ss", f"{vo_dur:.2f}")
    from src.video_composer import END_CARD_BELOW_BODY_LU
    assert gap == pytest.approx(END_CARD_BELOW_BODY_LU, abs=1.5)                  # measured and set, per run
    assert manifest["end_card"]["level"]["body_lufs"] is not None
    short = get_delivery_profile("reel").model_copy(update={"duration_min": 0.0})
    monkeypatch.setattr(release, "get_delivery_profile", lambda name: short)
    release.write_release_report({"master_video_path": str(master), "script_payload": {"caption": "c"}}, dry_run=True)
    report = json.loads((tmp_path / "release_report.json").read_text(encoding="utf-8"))
    checks = {c["name"]: c for c in report["checks"]}
    for name in ("dimensions", "channels", "sample_rate", "loudness", "true_peak", "sync_drift", "duration"):
        assert checks[name]["status"] == "pass", checks[name]
    assert "Measured 5.9" in checks["duration"]["detail"] or "Measured 5.8" in checks["duration"]["detail"]
