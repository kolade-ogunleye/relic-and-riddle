"""Operator-approved batch (2026-11-03, "I'll go with your recommendations"):

1. A 9:16 Reel ends on the loop bridge (src/end_card.py loop_bridge, LOOP_SECONDS, no share line) in place of the
   full end card, which stays the 16:9 YouTube end screen; the music bed is sized to the bridge and runs through the
   seam (no fade to silence, no whoosh or bell). A bridge that would hitch at the seam is retried without its push;
   only if that hitches too is it dropped (the Reel ends at the narration) with a warning on the review card.
2. 9:16 captions at 80 px (FONT_SIZE; the ASS size follows it). A bonded run (BETWEEN 94 AND 104 METRES) shrinks to
   CAPTION_BOND_MIN_SIZE rather than leave its figure; an old .env with FONT_SIZE=72 is warned about.
3. Facebook galleries take at most 5 photos (FACEBOOK_PHOTOS).
4. The AI illustration label reads exactly "AI ILLUSTRATION · NOT A PHOTO".
5. The Library of Congress source is on by default (MUSEUM_LOC_ENABLED).
6. main.py hands the prepared hero to cover_selector.for_run.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import end_card

ROOT = Path(__file__).resolve().parent.parent
LABEL = "AI ILLUSTRATION · NOT A PHOTO"


def _config_defaults(tmp_path, *names, **env):
    """The values src/config.py gives `names` with none of them set in the environment and no .env to read (a
    fresh interpreter whose app root is an empty folder), plus `env`."""
    Path(tmp_path).mkdir(parents=True, exist_ok=True)
    clean = {k: v for k, v in os.environ.items() if k not in names}
    clean.update(RELIC_APP_ROOT=str(tmp_path), PYTHONPATH=str(ROOT), **env)
    code = ("import json; from src import config; "
            f"print(json.dumps({{n: getattr(config, n) for n in {list(names)!r}}}))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=clean, cwd=str(tmp_path),
                         timeout=120, check=False)
    assert out.returncode == 0, out.stderr[-800:]
    return json.loads(out.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------- 1. the Reel's loop bridge
def test_the_bridge_is_short_and_carries_no_share_line():
    assert end_card.LOOP_SECONDS <= 1.0 and end_card.LOOP_LINE is False
    assert end_card.LOOP_VARIANT != end_card.VARIANT          # audience analysis tells the two endings apart
    assert end_card.SECONDS > end_card.LOOP_SECONDS           # the full card is kept (the 16:9 end screen)


def test_the_bed_is_sized_to_the_bridge():
    assert end_card.bed_tail(True) == pytest.approx(end_card.LOOP_SECONDS + 0.05)
    assert end_card.bed_tail(True) < end_card.SECONDS          # no longer the full card's length
    assert end_card.bed_tail(False) == pytest.approx(0.5)


def test_main_sizes_the_reel_bed_with_the_bridge():
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "bed_seconds = vo_dur + end_card.bed_tail(card)" in src
    assert "end_card.SECONDS" not in src


def test_the_loop_motion_follows_the_closing_and_opening_shots(tmp_path):
    from src.video_composer import TRANS_DUR, VIDEO_FPS, loop_motion
    stills = [tmp_path / "a.jpg", tmp_path / "b.jpg"]
    got = loop_motion(stills, [2.0, 2.0], 4.0, set())
    frames = round((4.0 - (2.0 - TRANS_DUR / 2)) * VIDEO_FPS)
    # QF-22: a still eases into the extent the old capped push reached (end_card.shot_drift, removed with zoompan)
    # and ends at rest; the opening still starts at rest: no push through the seam (was 0.0016 each)
    zoom = min(1 + end_card.LOOP_PUSH * frames, end_card.LOOP_PUSH_CAP)
    assert got == {"close_zoom": round(zoom, 6), "close_push": 0.0, "open_push": 0.0}
    # a long closing shot has reached its cap: no push it did not have
    long = loop_motion(stills, [2.0, 20.0], 22.0, set())
    assert long["close_zoom"] == pytest.approx(end_card.LOOP_PUSH_CAP) and long["close_push"] == 0.0
    # a closing video clip (parallax, footage) moves by itself: the bridge adds none; a macro drift pushes faster
    clips = [tmp_path / "a.jpg", tmp_path / "b.mp4"]
    assert loop_motion(clips, [2.0, 2.0], 4.0, set())["close_push"] == 0.0
    assert loop_motion(stills, [2.0, 2.0], 4.0, {1})["close_push"] == 0.0     # QF-22: was 0.0025, at rest now
    # the AI hook clip keeps its push from frame 0, which the bridge pre-rolls frame 0 at
    hook = [tmp_path / "motion_01.mp4", tmp_path / "b.jpg"]
    assert loop_motion(hook, [2.0, 2.0], 4.0, set())["open_push"] == pytest.approx(end_card.LOOP_PUSH)
    assert loop_motion([tmp_path / "a.mp4", tmp_path / "b.jpg"], [2.0, 2.0], 4.0, set())["open_push"] == 0.0


def test_the_record_and_note_for_a_bridge_and_a_dropped_one(tmp_path):
    master = tmp_path / "m_master.mp4"
    master.with_suffix(".composition.json").write_text(json.dumps({"end_card": {
        "variant": end_card.LOOP_VARIANT, "start_s": 30.0, "duration_s": end_card.LOOP_SECONDS, "text": None}}))
    rec = end_card.end_card_record(master, fallback=True)   # the share line's fallback: no line is drawn
    assert rec == {"variant": end_card.LOOP_VARIANT, "start_s": 30.0, "duration_s": end_card.LOOP_SECONDS,
                   "text": None, "fallback": False}
    assert end_card.end_card_note(rec) is None
    note = {"type": "end_card", "card": "End card", "severity": "warning", "issue": "hitch"}
    master.with_suffix(".composition.json").write_text(json.dumps({"end_card": {
        "variant": None, "start_s": 30.0, "duration_s": 0.0, "text": None, "note": note}}))
    rec = end_card.end_card_record(master)
    assert rec["variant"] is None and end_card.end_card_note(rec) == note


def test_the_gate_note_says_what_the_reel_does_instead(monkeypatch):
    monkeypatch.setattr(end_card, "LOOP_MAX_MAD", 0.01)
    last = Image.new("RGB", (1080, 1920), (40, 40, 40))
    first = Image.new("RGB", (1080, 1920), (220, 210, 190))
    out = end_card.check_loop_bridge(last, first, "Send this.", line=end_card.LOOP_LINE)
    assert out["ok"] is False and "ends at the narration" in out["note"]["issue"]


def _render(tmp_path, monkeypatch=None, fail_gate=False, fail_push=False):
    import soundfile as sf

    from src.audio_engine import AudioEngine
    from src.video_composer import VideoComposer
    sr, vo_dur = 48000, 3.0
    t = np.arange(int(sr * vo_dur)) / sr
    voice = tmp_path / "voiceover.wav"
    sf.write(voice, 0.3 * np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t)), sr)
    music = tmp_path / "music.wav"
    tm = np.arange(int(sr * 10)) / sr
    sf.write(music, np.column_stack([0.2 * np.sin(2 * np.pi * 110 * tm)] * 2), sr)
    bed = AudioEngine().generate_ambient_bed(vo_dur + end_card.bed_tail(True), tmp_path / "ambient_bed.wav",
                                             music_path=music, end_card_at=vo_dur)
    scenes = []
    for i, colour in enumerate(((200, 170, 110), (60, 80, 120))):
        p = tmp_path / f"s{i}.jpg"
        im = Image.new("RGB", (1200, 2100), colour)
        ImageDraw.Draw(im).ellipse([300, 600, 900, 1400], fill=(240, 220, 160) if i else (40, 30, 20))
        im.save(p)
        scenes.append(p)
    if fail_gate:
        real = end_card.check_loop_bridge

        def failing(*a, **kw):
            out = real(*a, **kw)
            note = {"type": "end_card", "card": "End card", "severity": "warning", "issue": "test hitch"}
            return {**out, "ok": False, "problems": ["test hitch"], "note": note}
        monkeypatch.setattr(end_card, "check_loop_bridge", failing)
    if fail_push:   # the bridge hitches only with its push through the seam
        # QF-22: stills now ease to rest at the seam (no push), so the test supplies a pushed seam itself
        monkeypatch.setattr("src.video_composer.seam_from_cameras", lambda *_a, **_k: {
            "close_zoom": 1.08, "close_push": end_card.LOOP_PUSH, "open_push": end_card.LOOP_PUSH})
        real_gate = end_card.check_loop_bridge

        def push_fails(*a, **kw):
            out = real_gate(*a, **kw)
            if kw.get("close_push") or kw.get("open_push"):
                return {**out, "ok": False, "problems": ["push hitch"]}
            return out
        monkeypatch.setattr(end_card, "check_loop_bridge", push_fails)
    words = [{"word": "Old", "start": 0.2, "dur": 0.4}, {"word": "maps.", "start": 0.7, "dur": 0.5},
             {"word": "Read", "start": 1.6, "dur": 0.4}, {"word": "them.", "start": 2.1, "dur": 0.5}]
    master = tmp_path / "t_master.mp4"
    VideoComposer().compose_video(scenes, voice, bed, words, vo_dur, master, durations=[1.5, 1.5],
                                  end_card="Send this to the codebreaker you know.")
    return master, vo_dur


def _probe_duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def _frame(path, which):
    args = ["-sseof", "-0.04"] if which == "last" else []
    out = subprocess.run(["ffmpeg", "-v", "error", *args, "-i", str(path), "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True)
    return np.frombuffer(out.stdout[:1080 * 1920 * 3], np.uint8).reshape(1920, 1080, 3).astype(np.float32)


def test_a_reel_master_ends_on_the_bridge_and_loops_into_its_first_frame(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg is required to render a master")
    master, vo_dur = _render(tmp_path)
    rec = json.loads(master.with_suffix(".composition.json").read_text(encoding="utf-8"))["end_card"]
    assert rec["variant"] == end_card.LOOP_VARIANT and rec["duration_s"] == end_card.LOOP_SECONDS
    assert rec["text"] is None and rec["line"] is False and rec["lines"] == []          # no share line
    assert end_card.seam_problems(rec) == []
    assert _probe_duration(master) == pytest.approx(vo_dur + end_card.LOOP_SECONDS, abs=0.1)
    # the last delivered frame is one step short of frame 0: the loop closes
    mad = np.abs(_frame(master, "last") - _frame(master, "first")).mean()
    assert mad < end_card.LOOP_MAX_MAD + 4      # the gate's bound, plus the delivery encode
    assert "still_fallback" not in rec and rec["open_push"] == 0.0     # QF-22: the opening still starts at rest
    # the bridge's level is set per run with room to spare: the bed no longer fades out under it (review: 12.8 dB
    # asked of a +/-12 dB clamp)
    assert abs(rec["level"]["correction_db"]) < 6.0


def test_a_bridge_that_hitches_with_its_push_ships_without_it(tmp_path, monkeypatch):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg is required to render a master")
    master, vo_dur = _render(tmp_path, monkeypatch, fail_push=True)
    rec = json.loads(master.with_suffix(".composition.json").read_text(encoding="utf-8"))["end_card"]
    assert rec["variant"] == end_card.LOOP_VARIANT and rec["still_fallback"] == ["push hitch"]
    assert rec["push"] == 0.0 and rec["open_push"] == 0.0 and rec["crossfade"] == end_card.LOOP_CROSSFADE[1]
    assert end_card.seam_problems(rec) == [] and "note" not in rec
    assert _probe_duration(master) == pytest.approx(vo_dur + end_card.LOOP_SECONDS, abs=0.1)
    assert end_card.end_card_note(end_card.end_card_record(master)) is None


def test_the_bridge_bed_runs_through_the_seam(tmp_path):
    import soundfile as sf

    from src.audio_engine import AudioEngine
    sr, vo = 48000, 4.0
    t = np.arange(int(sr * 12)) / sr
    music = tmp_path / "music.wav"
    sf.write(music, np.column_stack([0.2 * np.sin(2 * np.pi * 220 * t)] * 2), sr)
    eng = AudioEngine()
    assert eng.ends_on_loop_bridge(vo + end_card.bed_tail(True), vo)
    assert not eng.ends_on_loop_bridge(vo + end_card.SECONDS + 0.05, vo) and not eng.ends_on_loop_bridge(9.0, None)
    bed = eng.generate_ambient_bed(vo + end_card.bed_tail(True), tmp_path / "bed.wav", music_path=music, end_card_at=vo)
    audio, _ = sf.read(bed)
    end = len(audio) / sr
    assert end == pytest.approx(vo + end_card.bed_tail(True), abs=0.01)
    rms = lambda a, b: float(np.sqrt(np.mean(audio[int(a * sr):int(b * sr)] ** 2)))
    body, held = rms(2.0, 3.5), rms(vo + 0.4, vo + 0.6)
    assert 20 * np.log10(held / body) == pytest.approx(eng.LOOP_BRIDGE_MUSIC_DB, abs=1.5)   # no card drop
    # held, not faded, up to the last 50 ms (was: a 1.2 s fade then a 0.5 s one, reaching near-silence at the seam)
    assert 20 * np.log10(rms(end - 0.15, end - eng.LOOP_BRIDGE_EDGE) / held) == pytest.approx(0.0, abs=1.0)
    assert rms(end - 0.005, end) < held * 0.5                                   # still no click at the seam
    # and it opens on the same micro-fade, not a 1 s fade-in from silence
    assert rms(eng.LOOP_BRIDGE_EDGE, 0.15) > 0.4 * rms(0.15, 0.3)              # a 1 s fade-in reads under 0.1
    # no whoosh at the bridge's start: the held level is flat across it
    assert rms(vo + 0.3, vo + 0.45) == pytest.approx(rms(vo + 0.6, vo + 0.75), rel=0.1)


def test_a_bridge_that_fails_its_gate_is_dropped_with_a_warning(tmp_path, monkeypatch):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg is required to render a master")
    master, vo_dur = _render(tmp_path, monkeypatch, fail_gate=True)
    rec = json.loads(master.with_suffix(".composition.json").read_text(encoding="utf-8"))["end_card"]
    assert rec["variant"] is None and rec["duration_s"] == 0.0 and rec["problems"] == ["test hitch"]
    assert _probe_duration(master) == pytest.approx(vo_dur, abs=0.1)
    note = end_card.end_card_note(end_card.end_card_record(master))
    assert note["severity"] == "warning" and note["issue"] == "test hitch"
    assert not list(tmp_path.glob("*_frame0.png")) and not list(tmp_path.glob("*_body.mkv"))


# ---------------------------------------------------------------- 2. 80 px captions
def test_reel_captions_are_80_px(tmp_path):
    from src import config
    from src.video_composer import CAPTION_BURST_MIN_SIZE, VideoComposer, reel_render_px
    assert config.FONT_SIZE == 80 and config.FONT_SIZE_ASS == round(80 / 0.5625) == 142
    assert CAPTION_BURST_MIN_SIZE == 74
    assert reel_render_px(80) == pytest.approx(80, rel=0.04)       # libass draws about 80 px (2.6% over)
    words = [{"word": w, "start": 0.3 + 0.4 * i, "end": 0.6 + 0.4 * i} for i, w in enumerate(["Old", "maps", "lie."])]
    ass = VideoComposer().build_subtitles(words, tmp_path / "c.ass").read_text(encoding="utf-8")
    style = next(ln for ln in ass.splitlines() if ln.startswith("Style: CapText,")).split(",")
    assert int(style[2]) == 142


@pytest.mark.parametrize("text,run", [
    # freeze pass C7 (2026-11-04): BETWEEN 94 AND 104 METRES draws 647 px of ink at 66 px (libass's own measure, C5),
    # over the 640 px budget, so the burst breaks before the range's joiner (was the run "between 94 and 104 metres"
    # whole). Fix round 1 (operator decision 2026-11-04, the HEAD-regression gate): HEAD 0036b18 keeps ITS WALLS
    # MEASURE | BETWEEN ..., so the one cut the floor forces is BETWEEN 94 | AND 104 METRES (round 2: "94 and 104
    # metres", BETWEEN moved onto MEASURE, a second cut HEAD does not have)
    ("Its walls measure between 94 and 104 metres around the base.", "and 104 metres"),
    # AN ESTIMATED 200,000 TONS is no bonded run (AN | ESTIMATED splits): it may not shrink past the burst floor, and
    # the split keeps the hedge with its figure without leaving AN alone (LONE_WORD_PENALTY)
    ("They removed an estimated 200,000 tons of rock from the cliff.", "estimated 200,000 tons"),
])
def test_a_bonded_run_shrinks_rather_than_leave_its_figure_at_80_px(text, run, tmp_path):
    import re

    from src.video_composer import (
        CAPTION_BOND_MIN_SIZE,
        CAPTION_BURST_MIN_SIZE,
        CAPTION_HIDDEN,
        VideoComposer,
        _caption_fit_size,
        caption_bonds,
        partition_words_grammatical,
    )
    assert CAPTION_BOND_MIN_SIZE == 66                     # the floor every burst had at 72 px
    words = [{"word": w, "start": 0.3 * i, "dur": 0.25} for i, w in enumerate(text.split())]
    phrases = partition_words_grammatical(words)
    texts = [" ".join(w["word"] for w in p) for p in phrases]
    assert any(run in t for t in texts), texts
    assert all(len(p) > 1 for p in phrases), texts          # no lone AN before ESTIMATED 200,000 TONS
    assert "an estimated 200,000 tons" not in " | ".join(texts) or "between" in text
    from src.video_composer import _relax_figure_bonds
    bonds = _relax_figure_bonds(words, caption_bonds(words))
    for p in phrases:
        size = _caption_fit_size(p)
        if len(p) > 1 and not caption_bonds(p):
            assert size >= CAPTION_BURST_MIN_SIZE          # a burst with no bond keeps the burst floor
        assert size >= CAPTION_BOND_MIN_SIZE
    ends, n = set(), 0
    for p in phrases[:-1]:
        n += len(p)
        ends.add(n - 1)
    assert not ends & bonds
    ass = VideoComposer().build_subtitles(words, tmp_path / "c.ass").read_text(encoding="utf-8")
    shown = [re.sub(r"\{[^}]*\}", "", ln.split(",", 9)[9]).replace(CAPTION_HIDDEN, " ").split()
             for ln in ass.splitlines() if ln.startswith("Dialogue:") and ",CapText," in ln]
    assert any(" ".join(run.upper().split()) in " ".join(e) for e in shown)


def test_a_dot_env_left_at_72_px_is_warned_about():
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "if config.FONT_SIZE == 72:" in src and "FONT_SIZE=72 is set" in src
    assert "FONT_SIZE=72 keeps the old size" in (ROOT / ".env.example").read_text(encoding="utf-8")


def test_the_caption_size_and_loc_defaults_hold_without_a_dot_env(tmp_path):
    got = _config_defaults(tmp_path, "FONT_SIZE", "FONT_SIZE_ASS", "MUSEUM_LOC_ENABLED")
    assert got == {"FONT_SIZE": 80, "FONT_SIZE_ASS": 142, "MUSEUM_LOC_ENABLED": True}
    # an operator who sets them keeps their value, the ASS size following the type size
    got = _config_defaults(tmp_path / "set", "FONT_SIZE", "FONT_SIZE_ASS", "MUSEUM_LOC_ENABLED",
                           FONT_SIZE="72", MUSEUM_LOC_ENABLED="false")
    assert got == {"FONT_SIZE": 72, "FONT_SIZE_ASS": 128, "MUSEUM_LOC_ENABLED": False}


def test_the_env_example_documents_the_new_defaults():
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "\nFONT_SIZE=80\n" in example and "# MUSEUM_LOC_ENABLED=true" in example


# ---------------------------------------------------------------- 3. Facebook takes five
def test_facebook_galleries_are_capped_at_five(tmp_path):
    from src import multi_format
    assert multi_format.FACEBOOK_PHOTOS == multi_format.FACEBOOK_GALLERY == 5
    slides = []
    for i in range(7):
        p = tmp_path / f"carousel_slide_{i + 1:02d}.jpg"
        Image.new("RGB", (8, 8)).save(p)
        slides.append(str(p))
    picked = multi_format.facebook_pick(slides)
    assert len(picked) == 5 and picked[0] == slides[0] and picked[-1] == slides[-1] and picked == sorted(picked)
    assert multi_format.facebook_pick(slides[:5]) == slides[:5]


# ---------------------------------------------------------------- 4. the AI illustration label
def test_the_illustration_label_reads_exactly(tmp_path):
    from src.video_composer import RECON_BADGE_LABEL, VideoComposer
    assert RECON_BADGE_LABEL == LABEL
    words = [{"word": "Ancient", "start": 0.2, "end": 0.6}, {"word": "craft.", "start": 0.6, "end": 1.2}]
    for size in ((1080, 1920), (1920, 1080)):
        ass = VideoComposer().build_subtitles(words, tmp_path / f"{size[0]}.ass", width=size[0], height=size[1],
                                              reconstruction_intervals=[(0.0, 2.0)]).read_text(encoding="utf-8")
        badge = next(ln for ln in ass.splitlines() if ln.startswith("Dialogue:") and ",ReconBadge," in ln)
        assert badge.endswith("}" + LABEL)


def test_the_label_draws_in_its_font_without_dropping_a_glyph():
    from src.typography import drawable, get_font
    assert drawable(LABEL, "IBM Plex Mono") == LABEL
    font = get_font("IBM Plex Mono", 30, weight=600)
    assert font.getlength(LABEL) == font.getlength("[ HISTORICAL RECONSTRUCTION ]")   # the plates keep their width


# ---------------------------------------------------------------- 5. the Library of Congress
def test_the_library_of_congress_is_asked_by_default(monkeypatch):
    from src import config
    from src import museum_sources as ms
    assert config.MUSEUM_LOC_ENABLED is True
    monkeypatch.setattr(config, "MUSEUM_OPENVERSE_ENABLED", False)
    monkeypatch.setattr(config, "MUSEUM_ARTIC_ENABLED", False)
    assert ms.extra_sources() == ("loc",) and "loc" in ms.line_sources()
    monkeypatch.setattr(config, "MUSEUM_LOC_ENABLED", False)
    assert "loc" not in ms.line_sources()


# ---------------------------------------------------------------- 6. the cover selector sees the prepared hero
class _Stop(Exception):
    pass


def test_main_hands_the_prepared_hero_to_the_cover_selector(tmp_path, monkeypatch):
    from main import PipelineCoordinator
    from src import cover_selector
    seen = {}

    def for_run(db, run_id, payload, hero=None):
        seen.update(db=db, run_id=run_id, hero=hero)
        raise _Stop

    monkeypatch.setattr(cover_selector, "for_run", for_run)
    refit = tmp_path / "hero_refit.jpg"

    class Prepared:
        def result(self):
            return [tmp_path / "s1.jpg"], refit, 0

    class Coordinator:
        db = object()

    with pytest.raises(_Stop):
        PipelineCoordinator._render_approve_publish(
            Coordinator(), {"id": "01_topic", "title": "T"}, "run_1", tmp_path, {}, [tmp_path / "s0.jpg"],
            tmp_path / "hero_original.jpg", tmp_path / "vo.wav", tmp_path / "bed.wav", [], 3.0, False, True, None,
            prepared=Prepared())
    assert seen == {"db": Coordinator.db, "run_id": "run_1", "hero": refit}   # the refit, not the original


def test_an_atomic_pair_stays_whole_when_no_burst_fits_at_80_px():
    """At 80 px BUILT UNDER KING KRISHNA I no longer fits one line; the partitioner's last-resort cut keeps the
    name with its numeral (tests/test_unesco_and_upgrades.py held it at 72 px)."""
    from src.video_composer import partition_words_grammatical
    words = [{"word": w, "start": 0.3 * i, "dur": 0.25}
             for i, w in enumerate(["Built", "under", "King", "Krishna", "I", "of", "the", "Rashtrakuta", "dynasty."])]
    phrases = [[w["word"] for w in p] for p in partition_words_grammatical(words, 3, 4, 5)]
    assert any(p[-2:] == ["Krishna", "I"] or p[:2] == ["Krishna", "I"] or "Krishna" in p and "I" in p
               for p in phrases)
    assert [w for p in phrases for w in p] == [w["word"] for w in words]


def test_a_dropped_bridge_master_is_re_levelled_to_target(tmp_path, monkeypatch):
    """CI 2026-11-03: cutting the programme at the narration left it 0.9-1.1 LU over target (it was mastered with
    the bridge's quieter tail), which failed delivery on the runner. The cut programme is re-levelled to target."""
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg is required to render a master")
    from src.video_qa import measure_program_loudness
    master, _ = _render(tmp_path, monkeypatch, fail_gate=True)
    assert measure_program_loudness(master)["integrated_lufs"] == pytest.approx(-14.0, abs=0.5)


def test_relevel_filter_targets_the_cut_and_is_bounded(tmp_path):
    import soundfile as sf

    from src.video_composer import RELEVEL_MAX_DB, _relevel_filter
    sr = 48000
    t = np.arange(sr * 3) / sr
    wav = tmp_path / "p.wav"
    sf.write(wav, np.column_stack([0.05 * np.sin(2 * np.pi * 220 * t)] * 2), sr)
    f = _relevel_filter(wav, 3.0, -14.0)
    gain = float(f.split("volume=")[1].split("dB")[0])
    assert 0 < gain <= RELEVEL_MAX_DB and "alimiter" in f and f.endswith(",")
