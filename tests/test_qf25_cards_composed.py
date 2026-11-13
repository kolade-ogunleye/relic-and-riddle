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
    monkeypatch.setattr(encoding, "get_video_encoder", encoder)
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
