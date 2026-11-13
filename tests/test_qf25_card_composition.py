"""Real pipeline entry points for checked design cards; all checkers are offline."""
import copy
import json
import re
import subprocess

import numpy as np
import pytest
import soundfile as sf
from test_design_cards_2026_11_03 import (
    SHEET,
    _gate_payload,
    _photo,
    statement,
)

from src import card_composition, graphics
from src.fact_evidence import record_fact_review


def test_opening_preserves_voice_rights_without_creating_a_license(tmp_path, monkeypatch):
    from src.audio_provenance import inspect_audio_rights, record_voice
    monkeypatch.setattr(card_composition, "load_contract", lambda _: {
        "kind": "hook", "caption_clear_until_s": 1.4, "seconds": 1.5})
    voice = tmp_path / "voice.wav"
    sf.write(voice, np.zeros(4800), 48000)
    words = [{"word": "Checked.", "start": 0.0, "dur": .1}]
    def schedule():
        return card_composition.opening_schedule(tmp_path / "hook.mp4", words, [.1],
            [{"start": 0.0, "end": .1}], voice, voice, .1, tmp_path / "opening")
    record_voice(voice, "offline-fixture", True, "Checked.")
    result = schedule()
    assert inspect_audio_rights(result["voice"], "voice", "", script="Checked.")[0]
    assert not inspect_audio_rights(result["voice"], "voice", "", script="Edited.")[0]
    record_voice(voice, "offline-fixture", False, "Checked.")
    assert not inspect_audio_rights(schedule()["voice"], "voice", "", script="Checked.")[0]
    voice.with_suffix(".wav.rights.json").write_text("{}", encoding="utf-8")
    assert not inspect_audio_rights(schedule()["voice"], "voice", "", script="Checked.")[0]


def test_release_word_clock_requires_the_verified_opening_contract(monkeypatch):
    from src.checkpoints import fingerprint
    words = [{"word": "Checked.", "start": 0.0, "dur": .1}]
    record = {"kind": "hook", "caption_clear_until_s": 1.4, "seconds": 1.5, "sha256": "hook-hash"}
    monkeypatch.setattr(card_composition, "load_contract", lambda _: record)
    manifest = {"opening_offset_s": 1.4, "opening_card": {"asset": "hook.mp4", "start": 0.0,
        "end": 1.4, "voice_start": 1.4}, "scenes": {"hook.mp4": "hook-hash"}}
    assert fingerprint(card_composition.composed_words(manifest, words)) == fingerprint([
        {**words[0], "start": 1.4}])
    assert card_composition.composed_words({}, words) == words
    for broken in ({**manifest, "opening_offset_s": 1.5}, {**manifest, "opening_card": None},
                   {**manifest, "scenes": {}}, {**manifest, "opening_offset_s": 0.0}):
        with pytest.raises(ValueError):
            card_composition.composed_words(broken, words)


def checked_payload(tmp_path):
    sheet = copy.deepcopy(SHEET)
    statement("The ring formed through erosion", "f_cons", sheet)
    payload = _gate_payload(sheet, "title", "The Richat Structure")
    payload["chapters"] = [{"act_number": 1, "title": "The ring formed through erosion", "start_sec": 3.0}]
    path = tmp_path / "sheet.json"
    path.write_text(json.dumps(sheet), encoding="utf-8")
    # Chapter timing is structural; the card title still needs its signed field.
    record_fact_review(payload, path, check=payload["fact_review"]["check"])
    return payload, path


def test_graphics_pipeline_builds_the_checked_chapter(tmp_path, monkeypatch):
    payload, _ = checked_payload(tmp_path)
    monkeypatch.setattr(graphics.geo, "locate", lambda *_: None)
    # Encode is the external boundary. Planning, measured native frames, field
    # verification, generated poster, lineage and card metadata remain real.
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
    paths = graphics.make_all(payload, {}, tmp_path / "graphics")
    assert [p.name for p in paths] == ["graphic_chapter_01.mp4"]
    assert frames and frames[0].size == (1080, 1920)
    assert paths[0].with_suffix(".jpg").is_file()
    assert card_composition.matches(paths[0], {"start": 3, "end": 6, "text": "The ring formed through erosion."})
    assert not card_composition.matches(paths[0], {"start": 3, "end": 4, "text": "The ring formed through erosion."})
    assert not card_composition.matches(paths[0], {"start": 8, "end": 11, "text": "The ring formed through erosion."})
    contrast = paths[0].with_name(paths[0].name + ".contrast.json")
    original_contrast = contrast.read_bytes()
    contrast.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="contrast evidence changed"):
        card_composition.load_contract(paths[0])
    contrast.write_bytes(original_contrast)
    metadata = paths[0].with_name(paths[0].name + ".design.json")
    metadata.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="metadata changed"):
        card_composition.load_contract(paths[0])


def test_real_act_headings_get_checked_before_becoming_card_fields(tmp_path, monkeypatch):
    from test_freeze_cards_2026_11_04 import RICHAT_ACTS

    from src import design_cards as dc
    titles = list(RICHAT_ACTS)
    payload = {"acts": [{"act_number": i, "act_title": title, "claim_fact_ids": ["f_cons", "f_len"]}
                        for i, title in enumerate(titles, 1)]}
    source, target = tmp_path / "research.json", tmp_path / "run-sheet.json"
    original = json.dumps(SHEET)
    source.write_text(original, encoding="utf-8")
    calls = []
    def checked(payload, sheet):
        title = payload["title"]
        calls.append(title)
        ok = title in titles
        return ok, 100, [{"claim": title, "where": "title", "fact_ids": ["f_cons", "f_len"],
                         "claim_type": "rhetorical_framing", "verdict": "traced" if ok else "contradicts"}], "mock"
    def evidence(payload, sheet):
        return "pass", 100, [{"claim": payload["title"], "where": "title", "quote_ids": [],
                              "claim_type": "rhetorical_framing", "verdict": "supported"}], "mock"
    card_composition.check_chapter_fields(payload, source, target, trace=checked, evidence=evidence)
    sheet = json.loads(target.read_text(encoding="utf-8"))
    assert len(calls) == len(sheet["chapter_gates"]) == 5
    assert [entry["payload"]["title"] for entry in sheet["chapter_gates"]] == titles
    assert source.read_text(encoding="utf-8") == original
    card_composition.check_chapter_fields(payload, source, target, trace=checked, evidence=evidence)
    assert len(calls) == 5  # signed current fields reused, no repeat provider expense
    payload.update(design_card_sheet=str(target), chapters=[
        {"act_number": i, "title": title, "start_sec": (i - 1) * 10.0}
        for i, title in enumerate(titles, 1)])
    frames = []
    def encode(images, out, count, width, height):
        last = None
        for last in images:
            pass
        frames.append(last)
        last.convert("RGB").save(out.with_suffix(".jpg"))
        out.write_bytes(b"offline chapter encoder fixture")
    monkeypatch.setattr(dc, "_encode", encode)
    monkeypatch.setattr(graphics.geo, "locate", lambda *_: None)
    monkeypatch.setattr(graphics, "graded_frame", lambda *_a, **_k: None)
    paths = graphics.make_all(payload, {}, tmp_path / "documentary", width=1920, height=1080)
    assert len(paths) == len(frames) == 5
    assert [card_composition.load_contract(p)["checked_text"] for p in paths] == titles
    assert all(frame.size == (1920, 1080) for frame in frames)
    card_composition.require_chapters(paths, payload["chapters"])
    payload["acts"][0]["act_title"] = "Aliens built the ring"
    with pytest.raises(ValueError, match="heading failed"):
        card_composition.check_chapter_fields(payload, source, target, trace=checked, evidence=evidence)
    # A modified authored title cannot borrow a successful heading's gate.
    payload["chapters"][0]["title"] = "Aliens built the ring"
    assert len(graphics.make_all(payload, {}, tmp_path / "edited", width=1920, height=1080)) == 4


def test_documentary_intervals_hold_all_five_chapters_without_retiming_words():
    words = [{"word": str(i), "start": i * .5, "dur": .4} for i in range(100)]
    original = copy.deepcopy(words)
    chapters = [{"act_number": i + 1, "start_sec": i * 10.0} for i in range(5)]
    cuts = [{"start": float(i), "end": float(i + 5)} for i in range(0, 50, 5)]
    result = card_composition.chapter_cuts(cuts, chapters, words, 50)
    assert words == original
    assert " ".join(b["text"] for b in result).split() == [w["word"] for w in words]
    assert sum(b["end"] - b["start"] for b in result) == 50
    for chapter in chapters:
        beat = next(b for b in result if b["start"] == chapter["start_sec"])
        assert beat["end"] - beat["start"] == 3.5


def test_opening_remixes_music_with_shifted_narration_cues_and_invalidates_cache(tmp_path, monkeypatch):
    from src.audio_engine import AudioEngine
    rate = 48000
    tone = .12 * np.sin(2 * np.pi * 330 * np.arange(rate * 12) / rate)
    music_path = tmp_path / "generated-score.wav"
    sf.write(music_path, np.column_stack((tone, tone)), rate)
    music = {"source": "library", "path": str(music_path), "credit": "", "tracks": []}
    words = [{"word": "A", "start": 0.0, "dur": .4}, {"word": "reveal", "start": 3.0, "dur": .5}]
    plan = {"moments": [{"t": 3.0, "kind": "reveal", "effect": None}]}
    engine = AudioEngine()
    original_mix = engine.generate_ambient_bed
    calls = []
    def mix(*args, **kwargs):
        calls.append((args, copy.deepcopy(kwargs)))
        return original_mix(*args, **kwargs)
    monkeypatch.setattr(engine, "generate_ambient_bed", mix)
    kwargs = {"sample_rate": rate, "loop_seamless": False, "end_card": True}
    out = card_composition.mix_opening_bed(engine, music, tmp_path / "bed.wav", words, plan,
        9.5, 9.0, [4.4, 3.0, 3.0], 1.4, **kwargs)
    audio, sr = sf.read(out, always_2d=True)
    assert sr == rate and len(audio) / sr == pytest.approx(10.9)
    assert np.sqrt(np.mean(audio[int(.2 * sr):int(.8 * sr)] ** 2)) > .001
    assert calls[0][1]["words"] == [{**w, "start": w["start"] + 1.4} for w in words]
    assert calls[0][1]["sound_plan"]["moments"][0]["t"] == 4.4
    assert calls[0][1]["end_card_at"] == 10.4
    assert calls[0][1]["scene_cuts"] == [4.4, 7.4]
    card_composition.mix_opening_bed(engine, music, tmp_path / "bed.wav", words, plan,
        9.5, 9.0, [4.4, 3.0, 3.0], 1.4, **kwargs)
    assert len(calls) == 1
    card_composition.mix_opening_bed(engine, music, tmp_path / "bed.wav", words, plan,
        9.5, 9.0, [4.5, 3.0, 3.0], 1.5, **kwargs)
    assert len(calls) == 2


def test_verified_hook_reaches_the_native_master_with_all_voice_and_caption_words(tmp_path, monkeypatch):
    from src import encoding
    from src import video_composer as vc
    from src.audio_engine import AudioEngine
    sheet = copy.deepcopy(SHEET)
    statement("About 40 km across", "f_len", sheet)
    source = tmp_path / "facts.json"
    source.write_text(json.dumps(sheet), encoding="utf-8")
    photo = _photo(tmp_path / "generated-photo.jpg", "dark")
    words = [{"word": word, "start": i * .65, "dur": .6}
             for i, word in enumerate(["We", "approach", "the", "ring", "quietly."])]
    original = copy.deepcopy(words)
    encoder = lambda: ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "18"]
    monkeypatch.setattr(encoding, "get_video_encoder", encoder)
    monkeypatch.setattr(vc, "_get_video_encoder", encoder)
    card_composition.make_all({"design_card_sheet": str(source)}, tmp_path / "cards", photo=photo, words=words)
    hook = card_composition.opening_card(tmp_path / "cards")
    assert hook is not None
    rate = 48000
    samples = .1 * np.sin(2 * np.pi * 220 * np.arange(rate * 4) / rate)
    voice, score = tmp_path / "voice.wav", tmp_path / "generated-score.wav"
    sf.write(voice, samples, rate, subtype="DOUBLE")
    from src.audio_provenance import record_voice
    script = " ".join(w["word"] for w in words)
    record_voice(voice, "offline-generated-fixture", True, script)
    sf.write(score, np.column_stack((samples * .4, samples * .4)), rate, subtype="DOUBLE")
    beats = [{"start": 0.0, "end": 4.0, "text": " ".join(w["word"] for w in words)}]
    schedule = card_composition.opening_schedule(hook, words, [4.0], beats, voice, score, 4.0, tmp_path / "opening")
    shifted, sr = sf.read(schedule["voice"], always_2d=True)
    assert np.array_equal(shifted[round(1.4 * sr):, 0], samples)
    assert not np.any(shifted[:round(1.4 * sr)])
    assert words == original
    assert schedule["beats"] == [{**beats[0], "start": 1.4, "end": 5.4}]
    music = {"source": "library", "path": str(score), "credit": "", "tracks": []}
    bed = card_composition.mix_opening_bed(AudioEngine(), music, tmp_path / "bed.wav", words, None,
        4.0, 4.0, schedule["durations"], schedule["offset"], sample_rate=rate,
        loop_seamless=False, end_card=False)
    master = tmp_path / "master.mp4"
    vc.VideoComposer().compose_video([photo], schedule["voice"], bed, schedule["words"], schedule["total"],
        master, durations=schedule["durations"], opening_card=hook, caption_offset_s=schedule["offset"])
    ass = master.with_name("master_captions.ass").read_text(encoding="utf-8")
    events = [line.split(",", 9) for line in ass.splitlines() if line.startswith("Dialogue:")]
    assert events
    assert min(float(event[1].split(":")[-1]) for event in events) >= 1.4
    for suffix in (".srt", ".vtt"):
        text = master.with_suffix(suffix).read_text(encoding="utf-8").lower()
        for word in words:
            assert word["word"].lower().strip(".") in text
    manifest = json.loads(master.with_suffix(".composition.json").read_text(encoding="utf-8"))
    assert manifest["opening_offset_s"] == 1.4 and len(manifest["design_cards"]) == 1
    duration = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=duration", "-of", "csv=p=0", str(master)], text=True)
    assert float(duration) == pytest.approx(5.4, abs=1 / 30)
    frame = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", "0.6", "-i", str(master),
        "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
    image = np.frombuffer(frame, np.uint8).reshape(1920, 1080, 3)
    gold = (image[:, :, 0] > 140) & (image[:, :, 1] > 80) & (image[:, :, 2] < 100)
    assert gold.sum() > 2000  # native checked hook ink exists in the actual delivered frames
    assert len(re.findall(r"Dialogue:", ass)) == len(events)
    from src import video_qa
    from src.models import QAReport
    reviewed = []
    monkeypatch.setattr(video_qa.free_llm, "enabled", lambda: False)
    monkeypatch.setattr(video_qa, "claude_available", lambda: True)
    def review(_system, content, _model, **_kwargs):
        reviewed.extend(content)
        return QAReport(issues=[], summary="offline provider boundary")
    monkeypatch.setattr(video_qa, "structured_call", review)
    report = video_qa.review_render(master, schedule["beats"], photo, tmp_path, [photo])
    assert report is not None
    assert any("opening card, before narration" in item.get("text", "") for item in reviewed)
    assert (tmp_path / "qa_opening" / "qa_01.jpg").is_file()
    # Release reads the unshifted narration cache, while verifying the actual
    # composed audio and the signed opening's exact clock transformation.
    from src import release
    from src.credits import generate_provenance_ledger
    (tmp_path / "words.json").write_text(json.dumps(words), encoding="utf-8")
    ledger = generate_provenance_ledger(tmp_path, {"id": "offline-qf25"}, {}, master,
        schedule["voice"], bed, media_items=[photo, hook])
    assert video_qa.verify_provenance_integrity(ledger)
    release_report = release.write_release_report({"master_video_path": str(master),
        "script_payload": {"narration_script": script}}, dry_run=True)
    checks = {check["name"]: check for check in release_report["checks"]}
    for name in ("composition_inputs", "provenance", "voiceover_rights"):
        assert checks[name]["status"] == "pass", checks[name]


def _assert_checked_card_native(tmp_path, monkeypatch, kind, width=1920, height=1080):
    from test_design_cards_2026_11_03 import myth, quote

    from src import config, encoding, scene_planner
    from src import video_composer as vc
    sheet = copy.deepcopy(SHEET)
    payload = {}
    text = {"chapter": "The ring formed through erosion", "mythfact":
            "Aliens built the ring. The ring formed through erosion.",
            "quote": "Theodore Monod said It is not a crater."}[kind]
    if kind == "chapter":
        statement(text, "f_cons", sheet)
        payload["chapters"] = [{"act_number": 1, "title": text, "start_sec": 3.0}]
    elif kind == "mythfact":
        myth(sheet=sheet)
    else:
        quote(sheet=sheet)
        # Exercise the existing design's composition without changing the
        # operator's default hold in config or either environment.
        monkeypatch.setattr(config, "QUOTE_CARDS_ENABLED", True)
    source = tmp_path / "facts.json"
    source.write_text(json.dumps(sheet), encoding="utf-8")
    payload["design_card_sheet"] = str(source)
    encoder = lambda: ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "18"]
    monkeypatch.setattr(encoding, "get_video_encoder", encoder)
    monkeypatch.setattr(vc, "_get_video_encoder", encoder)
    monkeypatch.setattr(scene_planner.free_llm, "enabled", lambda: False)
    monkeypatch.setattr(scene_planner, "claude_available", lambda: False)
    photos = [_photo(tmp_path / (ground + ".jpg"), ground) for ground in ("dark", "smooth", "warm")]
    myth_words = ([{"word": word, "start": 3.0 + i * .8, "dur": .7}
                   for i, word in enumerate(text.split()[:4])] +
                  [{"word": word, "start": 6.2 + i * .4, "dur": .36}
                   for i, word in enumerate(text.split()[4:])]) if kind == "mythfact" else None
    paths = card_composition.make_all(payload, tmp_path / "cards", width=width, height=height, words=myth_words)
    assert len(paths) == 1 and card_composition.load_contract(paths[0])["kind"] == kind
    record = card_composition.load_contract(paths[0])
    held = record["seconds"] + .2
    beats = [{"start": 0.0, "end": 3.0, "text": "Look closely."},
             {"start": 3.0, "end": 3.0 + held, "text": text},
             {"start": 3.0 + held, "end": 3.0 + held + 15.0, "text": "The view continues."}]
    scenes, durations, _ = scene_planner.plan_scenes(beats, [*photos, *paths], use_storyboard=False,
                                                  documentary_chapters=kind == "chapter")
    assert scenes[1] == paths[0] and scenes[0] in photos
    words = []
    for beat in beats:
        if kind == "mythfact" and beat["start"] == 3.0:
            words.extend(myth_words)
            continue
        tokens = beat["text"].split()
        spacing = min(.4, (beat["end"] - beat["start"] - .2) / len(tokens))
        words += [{"word": word, "start": beat["start"] + i * spacing, "dur": spacing * .9}
                  for i, word in enumerate(tokens)]
    total = beats[-1]["end"]
    rate = 48000
    samples = .08 * np.sin(2 * np.pi * 220 * np.arange(round(total * rate)) / rate)
    voice, bed = tmp_path / "voice.wav", tmp_path / "bed.wav"
    sf.write(voice, samples, rate)
    sf.write(bed, np.column_stack((samples * .2, samples * .2)), rate)
    master = tmp_path / "master.mp4"
    vc.VideoComposer().compose_video(scenes, voice, bed, words, total, master, durations=durations,
                                     width=width, height=height)
    manifest = json.loads(master.with_suffix(".composition.json").read_text(encoding="utf-8"))
    assert len(manifest["design_cards"]) == 1
    assert next(iter(manifest["design_cards"].values()))["contract"]["kind"] == kind
    assert manifest["opening_offset_s"] == 0  # ordinary cards never suppress captions
    if height > width:
        assert not manifest.get("caption_graphic_overlap"), manifest.get("caption_graphic_overlap")
    if kind == "mythfact":
        measured = json.loads(paths[0].with_name(paths[0].name + ".design.json").read_text(encoding="utf-8"))
        first_full = next(e["t_full"] for e in measured["elements"] if e["name"] == "fact_0")
        assert abs(3.0 + first_full - 6.2) <= .5 / 30
        shifted_words = [{**w, "start": w["start"] + .2} for w in words]
        assert not card_composition.matches(paths[0], beats[1], shifted_words)
        too_fast = [{**w, "start": 3.0 + i * .2, "dur": .18} for i, w in enumerate(myth_words)]
        assert card_composition.make_all(payload, tmp_path / "too-fast", width=width, height=height,
                                         words=too_fast) == []
        # Joining the two spoken sentences preserves the nine-photo capacity
        # after the ordinary three-graphic allowance, without touching word times.
        split = [beats[0], {"start": 3.0, "end": 6.2, "text": "Aliens built the ring."},
                 {"start": 6.2, "end": beats[1]["end"], "text": "The ring formed through erosion."}]
        tail = [{"start": beats[1]["end"] + i * 3, "end": beats[1]["end"] + (i + 1) * 3,
                 "text": f"View {i}."} for i in range(10)]
        original_words = copy.deepcopy(words)
        joined = card_composition.card_cuts(split + tail, paths, words)
        assert len(joined) == 12 and joined[1]["end"] == beats[1]["end"]
        assert " ".join(b["text"] for b in joined) == " ".join(b["text"] for b in split + tail)
        assert card_composition.card_cuts(split + tail[:-1], paths, words) == split + tail[:-1]
        assert words == original_words
    card_frame = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", str(record["seconds"] - .2),
        "-i", str(paths[0]), "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
    final_frame = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", str(3.0 + record["seconds"] - .2),
        "-i", str(master), "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
    from PIL import Image
    decoded = np.frombuffer(card_frame, np.uint8).reshape(height, width, 3)
    reference = graphics.graded_frame(Image.fromarray(decoded), tmp_path / "graded-reference", vignette=False)
    assert reference is not None
    before = np.asarray(reference)[:750]
    after = np.frombuffer(final_frame, np.uint8).reshape(height, width, 3)[:750]
    assert np.mean(np.abs(before.astype(float) - after.astype(float))) < 5
    if kind == "quote":
        monkeypatch.setattr(config, "QUOTE_CARDS_ENABLED", False)
        with pytest.raises(ValueError, match="held"):
            card_composition.load_contract(paths[0])


@pytest.mark.parametrize("kind", ["chapter", "mythfact", "quote"])
def test_checked_card_survives_real_planning_and_native_composition(tmp_path, monkeypatch, kind):
    _assert_checked_card_native(tmp_path, monkeypatch, kind)


def test_portrait_myth_keeps_correction_source_and_ordinary_captions_clear(tmp_path, monkeypatch):
    _assert_checked_card_native(tmp_path, monkeypatch, "mythfact", width=1080, height=1920)
