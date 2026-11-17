"""QF-25 regression: checked design cards reach the composed master, and the hook keeps its caption-clear opening.

Before QF-25 nothing imported src/design_cards.py: graphics.make_all returned no chapter card, and the composer had
no way to open a Reel on the hook while honouring the design's caption_clear_until_s. These tests go through the
normal entry points (graphics.make_all, VideoComposer.compose_video, the release gate's picture and sound checks)
with generated fixtures only; checkers and providers are offline.
"""
import copy
import importlib
import inspect
import json
import subprocess

import numpy as np
import soundfile as sf
from test_design_cards_2026_11_03 import SHEET, _gate_payload, _photo, quote, statement

from src import config, graphics
from src.fact_evidence import record_fact_review


def _fake_encoder(monkeypatch):
    from src import design_cards
    frames = []

    def encode(images, out, count, width, height):
        last = None
        for last in images:
            pass
        frames.append(last)
        last.convert("RGB").save(out.with_suffix(".jpg"))
        out.write_bytes(b"offline encoder fixture")
    monkeypatch.setattr(design_cards, "_encode", encode)
    monkeypatch.setattr(graphics, "graded_frame", lambda *_a, **_k: None)
    return frames


def test_the_graphics_pipeline_composes_the_checked_chapter_card_and_holds_the_quote(tmp_path, monkeypatch):
    sheet = copy.deepcopy(SHEET)
    statement("The ring formed through erosion", "f_cons", sheet)
    quote(sheet=sheet)
    payload = _gate_payload(sheet, "title", "The Richat Structure")
    payload["chapters"] = [{"act_number": 1, "title": "The ring formed through erosion", "start_sec": 3.0}]
    path = tmp_path / "sheet.json"
    path.write_text(json.dumps(sheet), encoding="utf-8")
    record_fact_review(payload, path, check=payload["fact_review"]["check"])
    monkeypatch.setattr(graphics.geo, "locate", lambda *_: None)
    monkeypatch.setattr(config, "QUOTE_CARDS_ENABLED", False)   # the operator's hold (FREEZE-RESUME Q10)
    frames = _fake_encoder(monkeypatch)
    made = graphics.make_all(payload, {}, tmp_path / "graphics")
    names = [p.name for p in made]
    assert "graphic_chapter_01.mp4" in names, f"the checked chapter card never reaches the composer: {names}"
    assert not any(name.startswith("graphic_quote_") for name in names), "a held quote card was composed"
    assert frames and frames[0].size == (1080, 1920)


def test_the_hook_opens_the_master_and_no_caption_starts_before_caption_clear_until_s(tmp_path, monkeypatch):
    from src import encoding
    from src import video_composer as vc
    params = inspect.signature(vc.VideoComposer.compose_video).parameters
    assert "opening_card" in params and "caption_offset_s" in params, \
        "the composer cannot open a Reel on the hook frame or honour its caption_clear_until_s"
    card_composition = importlib.import_module("src.card_composition")
    from src.audio_engine import AudioEngine
    sheet = copy.deepcopy(SHEET)
    statement("About 40 km across", "f_len", sheet)
    source = tmp_path / "facts.json"
    source.write_text(json.dumps(sheet), encoding="utf-8")
    photo = _photo(tmp_path / "generated-photo.jpg", "dark")
    words = [{"word": word, "start": i * .65, "dur": .6}
             for i, word in enumerate(["We", "approach", "the", "ring", "quietly."])]
    encoder = lambda: ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "18"]
    monkeypatch.setattr(encoding, "delivery_video_encoder", encoder)
    monkeypatch.setattr(vc, "_get_video_encoder", encoder)
    card_composition.make_all({"design_card_sheet": str(source)}, tmp_path / "cards", photo=photo, words=words)
    hook = card_composition.opening_card(tmp_path / "cards")
    assert hook is not None, "the checked hook frame was not made for this Reel"
    # the designer's own statement of when the first caption may appear, read from the card's design record
    clear = json.loads((tmp_path / "cards" / "hook_frame.mp4.design.json").read_text(encoding="utf-8"))[
        "caption_clear_until_s"]
    assert clear > 0
    rate = 48000
    samples = .1 * np.sin(2 * np.pi * 220 * np.arange(rate * 4) / rate)
    voice, score = tmp_path / "voice.wav", tmp_path / "generated-score.wav"
    sf.write(voice, samples, rate, subtype="DOUBLE")
    sf.write(score, np.column_stack((samples * .4, samples * .4)), rate, subtype="DOUBLE")
    beats = [{"start": 0.0, "end": 4.0, "text": " ".join(w["word"] for w in words)}]
    schedule = card_composition.opening_schedule(hook, words, [4.0], beats, voice, score, 4.0, tmp_path / "opening")
    music = {"source": "library", "path": str(score), "credit": "", "tracks": []}
    bed = card_composition.mix_opening_bed(AudioEngine(), music, tmp_path / "bed.wav", words, None,
                                           4.0, 4.0, schedule["durations"], schedule["offset"], sample_rate=rate,
                                           loop_seamless=False, end_card=False)
    master = tmp_path / "master.mp4"
    vc.VideoComposer().compose_video([photo], schedule["voice"], bed, schedule["words"], schedule["total"],
                                     master, durations=schedule["durations"], opening_card=hook,
                                     caption_offset_s=schedule["offset"])
    ass = master.with_name("master_captions.ass").read_text(encoding="utf-8")
    starts = []
    for line in ass.splitlines():
        if line.startswith("Dialogue:"):
            h, m, s = line.split(",", 2)[1].split(":")
            starts.append(int(h) * 3600 + int(m) * 60 + float(s))
    assert starts and min(starts) >= clear - 1e-6, (min(starts or [0]), clear)
    # the spoken narration itself starts no earlier: the voice is silent before caption_clear_until_s
    shifted, sr = sf.read(schedule["voice"], always_2d=True)
    assert not np.any(shifted[:int(clear * sr)])
    # the hook is what the viewer sees in the opening, the photo after it
    frame = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", "0.6", "-i", str(master),
                                     "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
    image = np.frombuffer(frame, np.uint8).reshape(1920, 1080, 3)
    gold = (image[:, :, 0] > 140) & (image[:, :, 1] > 80) & (image[:, :, 2] < 100)
    assert gold.sum() > 2000
    # the release gate's every-frame picture checks (QF-10) and every-sample sound checks (QF-11) pass the opening
    from src import audio_qa, picture_qa
    picture = picture_qa.measure(master)
    sound = audio_qa.measure(master)
    failed = [c for c in picture["checks"] + sound["checks"] if c["status"] == "fail"]
    assert not failed, failed


def _scene_photo(path):
    """A textured, photo-like still (large soft forms, a horizon, fine grain): a crop or tone jump shows in it."""
    from PIL import Image, ImageFilter
    rng = np.random.default_rng(25)
    w, h = 1600, 2000
    coarse = Image.fromarray((rng.random((40, 32, 3)) * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC)
    arr = np.asarray(coarse.filter(ImageFilter.GaussianBlur(18)), dtype=np.float32) * 0.6
    y = np.linspace(0, 1, h)[:, None, None]
    arr += np.where(y < 0.45, 120 * (1 - y), 60 + 40 * y) * np.array([1.0, 0.85, 0.65])
    yy, xx = np.mgrid[0:h, 0:w]
    arr[(xx - 900) ** 2 / 9 + (yy - 1100) ** 2 / 64 < 90 ** 2] = (205, 180, 140)   # a monolith-like form
    arr += rng.normal(0, 6, arr.shape)
    Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB").save(path, quality=92)
    return path


def test_the_hook_hands_over_to_its_own_photo_without_a_jump(tmp_path, monkeypatch):
    """QF-25 review 1: the hook's ground is the run's hero, and the opening picture is very often that same photo
    (8 of 18 real Reels). The handover at the offset must step like ordinary motion, not jump (MAE ~35 before)."""
    from src import encoding
    from src import video_composer as vc
    card_composition = importlib.import_module("src.card_composition")
    sheet = copy.deepcopy(SHEET)
    statement("About 40 km across", "f_len", sheet)
    source = tmp_path / "facts.json"
    source.write_text(json.dumps(sheet), encoding="utf-8")
    photo = _scene_photo(tmp_path / "hero.jpg")
    words = [{"word": word, "start": i * .65, "dur": .6}
             for i, word in enumerate(["We", "approach", "the", "ring", "quietly."])]
    encoder = lambda: ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "18"]
    monkeypatch.setattr(encoding, "delivery_video_encoder", encoder)
    monkeypatch.setattr(vc, "_get_video_encoder", encoder)
    card_composition.make_all({"design_card_sheet": str(source)}, tmp_path / "cards", photo=photo, words=words)
    hook = card_composition.opening_card(tmp_path / "cards")
    assert hook is not None
    rate = 48000
    samples = .1 * np.sin(2 * np.pi * 220 * np.arange(rate * 4) / rate)
    voice = tmp_path / "voice.wav"
    sf.write(voice, samples, rate, subtype="DOUBLE")
    beats = [{"start": 0.0, "end": 4.0, "text": " ".join(w["word"] for w in words)}]
    schedule = card_composition.opening_schedule(hook, words, [4.0], beats, voice, voice, 4.0, tmp_path / "opening")
    master = tmp_path / "master.mp4"
    vc.VideoComposer().compose_video([photo], schedule["voice"], schedule["voice"], schedule["words"],
                                     schedule["total"], master, durations=schedule["durations"], opening_card=hook,
                                     caption_offset_s=schedule["offset"])
    count = round((schedule["offset"] + 0.6) * 30)
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(master), "-frames:v", str(count),
                                   "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
    frames = np.frombuffer(raw, np.uint8).reshape(-1, 1920, 1080, 3)[:, :1090].astype(np.float32)
    steps = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2, 3))     # steps[i]: frame i -> i + 1
    at = round(schedule["offset"] * 30)                                # the first frame after the hook
    motion = float(np.median(steps[at:]))
    assert steps[at - 1] <= max(3.0, 2.5 * motion), (steps[at - 3:at + 2].round(2).tolist(), motion)
    assert float(steps[at:].max()) <= 3.0, steps[at:].round(2).tolist()


def test_the_review_cards_video_check_times_are_on_the_hooked_masters_clock():
    """QF-25 review 1: with a hook every beat starts 1.4 s later in the master. The card's video-check summary and
    the record the release gate reads (video_check "where") must both use the master's beats, the ones the look
    sampled (render_beats), not the narration's."""
    import ast
    from pathlib import Path

    from main import beat_where
    from src.models import QAIssue, QAReport
    from src.video_qa import qa_summary_html
    tree = ast.parse(Path("main.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_render_approve_publish")

    def beats_arg(name, index):
        calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and getattr(c.func, "id", "") == name]
        assert calls, name
        return {ast.unparse(c.args[index]) for c in calls}
    assert beats_arg("qa_summary_html", 1) == beats_arg("beat_where", 1) == {"render_beats"}
    render_beats = [{"start": 1.4, "end": 4.4, "text": "a"}, {"start": 4.4, "end": 9.4, "text": "b"}]
    qa = QAReport(issues=[QAIssue(beat=2, severity="major", issue="x")], summary="s")
    where = beat_where(2, render_beats)
    assert where == "beat 2, 0:04-0:09" and "0:04-0:09" in qa_summary_html(qa, render_beats)


def test_the_caption_rate_records_times_are_on_the_hooked_masters_clock(tmp_path):
    """Port onto QF-88: the composer records each caption burst's reading rate with its time, and the release gate
    prints that time for the operator. With a hook the captions start 1.4 s later in the master, so the record's
    times move with them; without one they are unchanged."""
    from src.video_composer import VideoComposer
    words = [{"word": w, "start": s, "end": s + d} for w, s, d in
             (("Archaeologists", 0.2, 0.6), ("found", 0.85, 0.5), ("a", 1.45, 0.1), ("room.", 1.6, 0.4))]
    plain, hooked = VideoComposer(), VideoComposer()
    plain.build_subtitles(words, tmp_path / "plain.ass")
    hooked.build_subtitles(words, tmp_path / "hooked.ass", timeline_offset_s=1.4)
    before, after = plain.last_reading_rate, hooked.last_reading_rate
    assert before["worst"] and after["worst"]["at"] == round(before["worst"]["at"] + 1.4, 2)
    assert {k: v for k, v in after.items() if k != "worst"} == {k: v for k, v in before.items() if k != "worst"}
    assert {**after["worst"], "at": before["worst"]["at"]} == before["worst"]
