"""Ordinary photographic subjects cannot relax reference fidelity through keywords; a structured verdict reaches
the real beats of the sentence it was given for, and no other."""

import json
from pathlib import Path
from unittest import mock

import pytest

from src import image_gen, image_quarantine, prevoice_lines

ORDINARY = [
    "Miners worked the salt by hand.",
    "Inside the museum stands a statue.",
    "The road turns east.",
    "The original manuscript survives in the library.",
    "The interior is open to visitors.",
    "The mill is still in operation today.",
    "The gears are displayed inside a glass case.",
    "A rotating exhibit shows both sides of the fragment.",
    "The workers assembled for a photograph.",
    "The collection once housed a bronze statue.",
    "The original stone door remains in place.",
    "A working replica stands beside the fragments.",
    "The camera turns toward the courtyard.",
    "The construction of the museum ended in 1990.",
    "The inscription is within the entrance hall.",
    "The river powered mills that still survive.",
    "The museum operated throughout the summer.",
    "The town used to hold a weekly market.",
    "The gears meshed in the modern demonstration model.",
    "The gallery was originally a warehouse.",
    "The stone blocks stand beside the road.",
    "Three bronze fragments lie on the table.",
    "The temple was built in 432 BC.",
    "Visitors can see the carved doorway.",
    "A staircase connects the two galleries.",
]


def subject(line):
    """The words naming what is reconstructed, copied from the line (QF-60b): its longest word but "inside"."""
    return max((w for w in line.replace(",", " ").replace(".", " ").split() if w.lower() != "inside"), key=len)


def verdict(line, **changes):
    return {"sentence": line, "kind": "generable", "visual": "named_subject", "reconstruction": True,
            "reconstruction_kind": "mechanism_in_use", "reconstruction_cause": "no_longer_operating",
            "reconstruction_subject_words": subject(line),
            "reconstruction_reason": "The ancient operating state cannot be photographed; only fragments survive.",
            **changes}


def placed(v, beat):
    """The verdict as picture_coverage.placed_verdicts hands it to the planner: placed on `beat` by word position."""
    return {**v, "placement": {"by": "word_position", "sentence_index": 0, "beat": beat}}


@pytest.mark.parametrize("line", ORDINARY[:3])
def test_ordinary_example_never_grants_an_exception_from_a_keyword(line):
    assert image_gen.illustration_reason(line) == ""


def test_twenty_five_ordinary_lines_stay_photographic_without_a_verdict():
    assert sum(bool(image_gen.illustration_reason(line)) for line in ORDINARY) <= 1


@pytest.mark.parametrize("change", [
    {"reconstruction": "true"}, {"reconstruction": False}, {"reconstruction_kind": "interior"},
    {"reconstruction_reason": ""}, {"sentence": "Another sentence."}, {"kind": "shown"},
    {"visual": "abstract"}, {"reconstruction_kind": []},
    {"reconstruction_reason": "\u200b"}, {"reconstruction_reason": "."}, {"reconstruction_reason": "x"},
])
def test_invalid_or_stale_verdict_cannot_grant_an_exception(change):
    line = "Inside, the lost mechanism once turned to show the planets."
    assert image_gen.illustration_reason(line, verdict=placed(verdict(line), line)) == image_gen.RECONSTRUCTION_REASON
    assert image_gen.illustration_reason(line, verdict=placed(verdict(line, **change), line)) == ""


def test_structured_prevoice_answer_is_retained_and_bound_to_the_input(monkeypatch):
    from src import free_llm
    line = "The ancient mechanism drove its bronze gears."

    def answer(system, prompt, schema, **kwargs):
        return schema(lines=[{"index": 1, **{k: v for k, v in verdict(line).items() if k != "sentence"}}])

    monkeypatch.setattr(free_llm, "call", answer)
    row = prevoice_lines.judge(line, [{"file": "fragments.png", "text": "The surviving fragments"}], "Mechanism")[0]
    assert row["sentence"] == line and row["reconstruction"] is True
    assert image_gen.illustration_reason(line, verdict=placed(row, line)) == image_gen.RECONSTRUCTION_REASON


def test_missing_verdict_keeps_the_reference_rule_strict():
    spec = {"line": "Inside, the gears turned.", "required": ["bronze gears"],
            "why_illustrated": image_gen.RECONSTRUCTION_REASON}
    assert "may show more" not in image_quarantine._nothing_added_rule(spec)


def test_valid_reconstruction_keeps_the_fact_descriptor_limit():
    line = "The ancient mechanism drove its bronze gears."
    spec = {"line": line, "required": ["bronze gears"], "why_illustrated": image_gen.RECONSTRUCTION_REASON,
            **_reconstruction().bind(placed(verdict(line), line))}
    rule = image_quarantine._nothing_added_rule(spec)
    assert "bronze gears" in rule and "Anything added beyond that description fails" in rule


def test_explicit_reconstruction_string_cannot_bypass_the_prevoice_decision(tmp_path, monkeypatch):
    from PIL import Image
    reference = tmp_path / "reference.png"
    Image.new("RGB", (32, 32)).save(reference)
    monkeypatch.setattr(image_gen, "is_available", lambda: True)
    monkeypatch.setattr(image_gen, "checkers_ready", lambda: pytest.fail("must refuse before model checks"))
    result, info = image_gen.illustrate_beat("Inside, the gears turned.", {"required": ["bronze gears"]}, tmp_path,
                                           visual="named_subject", references=[{"path": str(reference)}],
                                           reason=image_gen.RECONSTRUCTION_REASON)
    assert result is None and info["calls"] == 0 and "structured pre-voice" in info["reason"]


# ------------------------------------------------------------------ round 4: the verdict reaches the real beats
# Review 3 (blocking 1): the narration is split into beats (scene_planner.segment_beats) that are fragments of a
# sentence. A verdict matched only when the beat WAS the whole sentence (26 of 192 real beats). It is bound to the
# beat's own sentence now, placed by word position (round 5, below).
RECONSTRUCTED = ("The bronze gears of the lost mechanism turned inside a wooden case, driving the dials that "
                 "tracked the moon, the sun and the planets across the sky.")
ORDINARY_NEXT = "Today three fragments rest in a museum in Athens."
AQUEDUCT = "The aqueduct strides across the valley."


def _reconstruction():
    try:
        from src import reconstruction
    except ImportError:
        pytest.fail("no structured reconstruction verdict (src/reconstruction.py)")
    return reconstruction


def _words(*sentences, step=0.3):
    words, t = [], 0.0
    for s in sentences:
        for w in s.split():
            words.append({"word": w, "start": round(t, 3), "dur": step - 0.05})
            t += step
    return words, t


def _run(tmp_path, verdicts, words, name="run"):
    """A run folder: the pre-voice record and, unless `words` is None, the voiceover's word timeline."""
    run = tmp_path / name
    run.mkdir(parents=True, exist_ok=True)
    (run / prevoice_lines.RECORD_FILE).write_text(json.dumps({"verdicts": verdicts}), encoding="utf-8")
    if words is not None:
        (run / "words.json").write_text(json.dumps(words), encoding="utf-8")
    return run


def _placed_verdicts(run, beats):
    from src import picture_coverage
    if not hasattr(picture_coverage, "placed_verdicts"):
        pytest.fail("no placement of a beat in its own sentence by word position")
    return picture_coverage.placed_verdicts(run, beats)


def _split(tmp_path, *sentences, verdicts, tail=None):
    """The real beat split (scene_planner.segment_beats) and the real placement of each beat in its own sentence
    by word position (picture_coverage.placed_verdicts)."""
    from src import scene_planner
    words, total = _words(*sentences)
    if tail is not None:
        total = words[-1]["start"] + words[-1]["dur"] + tail
    beats = scene_planner.segment_beats(words, total)
    run = _run(tmp_path, verdicts, words)
    return run, beats, _placed_verdicts(run, beats)


def _owners(beats, *sentences):
    """The test's own oracle: the sentence indexes each beat's words are spoken in, counted from the beats' word
    counts in order (no code under test)."""
    sentence_of = [i for i, s in enumerate(sentences) for _ in s.split()]
    out, at = [], 0
    for b in beats:
        n = len(b["text"].split())
        out.append(set(sentence_of[at:at + n]))
        at += n
    return out


def _granted(beat, v):
    """All three places agree: the planner's reason, the generation gate (generation off, so only its own
    reconstruction refusal can answer first; nothing is requested), and the reference comparison's relaxed
    nothing-added rule."""
    rec = _reconstruction()
    if image_gen.illustration_reason(beat, verdict=v) != image_gen.RECONSTRUCTION_REASON:
        return False
    spec = {"line": beat, "required": ["bronze gears"], "why_illustrated": image_gen.RECONSTRUCTION_REASON,
            **rec.bind(v)}
    with mock.patch.object(image_gen, "is_available", lambda: False):
        _, info = image_gen.illustrate_beat(beat, dict(spec), None, reason=image_gen.RECONSTRUCTION_REASON)
    return info["reason"] == "generation off or no key" and "may show more" in image_quarantine._nothing_added_rule(spec)


def test_a_valid_verdict_reaches_every_beat_of_a_multi_beat_sentence(tmp_path):
    _, beats, mapped = _split(tmp_path, RECONSTRUCTED, ORDINARY_NEXT,
                              verdicts=[verdict(RECONSTRUCTED), {"sentence": ORDINARY_NEXT, "kind": "shown"}])
    owners = _owners(beats, RECONSTRUCTED, ORDINARY_NEXT)
    inside = [k for k, o in enumerate(owners) if o == {0}]
    assert len(inside) >= 2 and all(beats[k]["text"] != RECONSTRUCTED for k in inside)   # fragments
    assert all(mapped[k] and mapped[k]["sentence"] == RECONSTRUCTED and _granted(beats[k]["text"], mapped[k])
               for k in inside)
    # the ordinary sentence's beats keep strict fidelity, whatever its record says
    assert not any(_granted(beats[k]["text"], mapped[k]) for k, o in enumerate(owners) if mapped[k] and o == {1})


def test_a_beat_running_into_an_ordinary_sentence_is_refused(tmp_path):
    # A beat that starts in the reconstructed sentence and carries words of the next one has no single sentence:
    # it is not placed, and even a verdict placed on it by hand fails (its words are not all inside the sentence).
    short = "Inside, the lost gears turned."
    _, beats, mapped = _split(tmp_path, short, ORDINARY_NEXT,
                              verdicts=[verdict(short), {"sentence": ORDINARY_NEXT, "kind": "shown"}])
    spanning = [k for k, o in enumerate(_owners(beats, short, ORDINARY_NEXT)) if len(o) > 1]
    assert spanning and all(mapped[k] is None for k in spanning)
    assert not any(_granted(beats[k]["text"], placed(verdict(short), beats[k]["text"])) for k in spanning)


@pytest.mark.parametrize("beat", ["art of the", "gears turned inside a wooden box", "The silver gears", "",
                                  "case gears"])
def test_a_beat_not_inside_the_verdicts_sentence_is_refused(beat):
    # whole words only ("art" is inside "start" as letters, not as a word), every word, in order
    line = "The start of the bronze gears turned inside a wooden case."
    assert image_gen.illustration_reason(beat, verdict=placed(verdict(line), beat)) == ""


def test_the_stored_sentence_must_equal_the_verdicts_own_sentence_exactly():
    rec = _reconstruction()
    beat = "turned inside a wooden case,"
    spec = {"line": beat, "required": ["bronze gears"], "why_illustrated": image_gen.RECONSTRUCTION_REASON,
            **rec.bind(placed(verdict(RECONSTRUCTED), beat))}
    assert rec.spec_approved(beat, spec) and "may show more" in image_quarantine._nothing_added_rule(spec)
    for stored in (None, "", RECONSTRUCTED.lower(), RECONSTRUCTED.replace("lost ", ""), ORDINARY_NEXT):
        changed = dict(spec, reconstruction_sentence=stored)
        assert not rec.spec_approved(beat, changed)
        assert "may show more" not in image_quarantine._nothing_added_rule(changed)


def _photos(tmp_path, n):
    from PIL import Image
    photos = []
    for i in range(n):
        p = tmp_path / "x" / f"p{i}.jpg"
        p.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (48, 48), (20 * i % 255, 90, 40)).save(p)
        photos.append(p)
    return photos


def _planner_world(tmp_path, monkeypatch, cap):
    """fit_to_lines with every photo judged wrong for its line and one reference too small to show (drawn from)."""
    from PIL import Image

    from src import beat_images, config, scene_planner
    monkeypatch.setattr(beat_images, "FIT_CACHE_FILE", tmp_path / "cache" / "picture_fit.json")
    monkeypatch.setattr(config, "PICTURE_VISION_CHECK", True)
    monkeypatch.setattr(config, "GENERATIVE_GAP_FILLING", True, raising=False)
    monkeypatch.setattr(config, "PICTURE_CASCADE", True, raising=False)
    monkeypatch.setattr(config, "MAX_GAP_IMAGES_PER_RUN", cap, raising=False)
    scene_planner._folder_credits.cache_clear()
    monkeypatch.setattr(beat_images, "picture_look", lambda path, query, size=512, ask=None: {
        "checked": True, "shows": False, "burned_in_text": False, "reason": "not that"})
    monkeypatch.setattr(scene_planner, "_fact_sheet_for", lambda topic, payload: {"facts": []})
    monkeypatch.setattr(image_gen, "spec_from_facts", lambda line, *a, **k: (
        {"era": "classical_antiquity", "required": ["bronze gears"], "fact_ids": ["F1"], "line": line}, ""))
    ref = tmp_path / "refs" / "fragment.jpg"
    ref.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (300, 300), (150, 120, 60)).save(ref)
    one = {"path": str(ref), "title": "mechanism fragment", "licence": "CC0", "sha256": "abc"}
    monkeypatch.setattr(image_gen, "find_references", lambda *a, **k: ([one], ""))


def _gated(monkeypatch, seen, make=True):
    """The REAL illustration gate with generation off (nothing is requested); each ask is recorded. With `make`, a
    granted ask returns a stand-in drawing (as generation would); without, nothing is drawn, so no neighbour is
    held back by an illustrated beat and every beat is asked."""
    from PIL import Image
    real = image_gen.illustrate_beat

    def gated(line, spec, work_dir, aspect="9:16", budget=None, others=(), references=(), visual=None, reason="",
              **k):
        with monkeypatch.context() as m:
            m.setattr(image_gen, "is_available", lambda: False)
            _, info = real(line, dict(spec), work_dir, aspect, references=references, visual=visual, reason=reason)
        relaxed = "may show more" in image_quarantine._nothing_added_rule(dict(spec, line=line, why_illustrated=reason))
        seen.append({"line": line, "reason": reason, "gate": info["reason"], "relaxed": relaxed,
                     "sentence": spec.get("reconstruction_sentence"),
                     "granted": (reason == image_gen.RECONSTRUCTION_REASON
                                 and info["reason"] == "generation off or no key" and relaxed)})
        if info["reason"] != "generation off or no key" or not make:
            return None, info
        p = Path(work_dir) / f"gap_gen_{image_gen.cache_key(line, spec['era'], aspect)}.jpg"
        Image.new("RGB", (54, 96), (30, 30, 30)).save(p)
        return p, {"model": "fake-model", "calls": 1}
    monkeypatch.setattr(image_gen, "illustrate_beat", gated)


def _fit(tmp_path, monkeypatch, beats, run):
    """scene_planner.fit_to_lines over `beats` (the first is the hook, never illustrated); {beat text: granted},
    granted meaning the planner's reason, the real gate and the relaxed nothing-added rule all allowed a
    reconstruction. Nothing is drawn and no cap or runtime share stops an ask, so every later beat is asked."""
    from src import config, scene_planner
    _planner_world(tmp_path, monkeypatch, cap=50)
    monkeypatch.setattr(config, "IMAGE_GEN_MAX_RUNTIME_SHARE", 1.0, raising=False)
    seen: list = []
    _gated(monkeypatch, seen, make=False)
    photos = _photos(tmp_path, len(beats))
    try:
        scene_planner.fit_to_lines(beats, photos, photos, "Mill", work_dir=run)
    finally:
        scene_planner._folder_credits.cache_clear()
    out: dict = {}
    for s in seen:
        out[s["line"]] = out.get(s["line"], False) or s["granted"]
    assert set(out) == {b["text"] for b in beats[1:]}, "every beat after the hook is asked"
    return out


def test_fit_to_lines_illustrates_a_real_beat_split_as_a_reconstruction(tmp_path, monkeypatch):
    from src import picture_coverage as pc
    from src import scene_planner
    rec = _reconstruction()
    _planner_world(tmp_path, monkeypatch, cap=2)
    seen: list = []
    _gated(monkeypatch, seen)
    fill = [f"The harbour wall stood firm in year {i}." for i in range(8)]
    aqueduct = "The aqueduct strides across the valley on its tall stone arches."   # a beat of its own
    sentences = (aqueduct, RECONSTRUCTED, *fill)
    run, beats, mapped = _split(tmp_path, *sentences, verdicts=[
        {"sentence": aqueduct, "kind": "shown", "visual": pc.NAMED_SUBJECT},
        {**verdict(RECONSTRUCTED), "kind": "unshown"}])
    inside = [k for k, o in enumerate(_owners(beats, *sentences)) if o == {1}]
    assert len(inside) >= 2 and all(beats[k]["text"] != RECONSTRUCTED for k in inside)
    assert all(mapped[k]["placement"]["sentence_index"] == 1 for k in inside)
    photos = _photos(tmp_path, len(beats))
    try:
        out, report = scene_planner.fit_to_lines(beats, photos, photos, "Mechanism", work_dir=run)
    finally:
        scene_planner._folder_credits.cache_clear()
    asked = [s for s in seen if s["line"] in {beats[k]["text"] for k in inside}]
    assert asked and all(s["granted"] and s["sentence"] == RECONSTRUCTED for s in asked)
    assert out[inside[0]].name.startswith("gap_gen_")
    made = [g for g in report["generated"] if g["beat"] - 1 in inside]
    assert made and all(g["reconstruction_sentence"] == RECONSTRUCTED
                        and rec.approved(g["line"], g["reconstruction_verdict"], g["reconstruction_sentence"])
                        for g in made)


# ------------------------------------------------------------------ round 5: placement by word position
# Review 4 (blocking 1): picture_coverage.beat_verdicts gives a beat the FIRST sentence containing its words, so a
# short beat of an ordinary sentence ("the sands.") inherited an earlier reconstruction sentence's verdict once the
# voice ran a second past the last word (segment_beats then keeps the short final beat on its own). The verdict now
# comes only from the beat's own sentence, found by the beat's word offsets in the voiceover's word timeline.
HOOK = "This is the story of a mill beside the river."   # beat 1, never illustrated
HOOK_VERDICT = {"sentence": HOOK, "kind": "shown", "visual": "named_subject"}
MILL = "Inside the mill, the great wheel turned, grinding grain for the town, the sands."
MILL_TODAY = "Today the river has silted up, the mill is gone, the sands."
MILL_VERDICTS = [verdict(MILL), {"sentence": MILL_TODAY, "kind": "shown", "visual": "named_subject",
                                 "reconstruction": False, "reconstruction_kind": None, "reconstruction_reason": ""}]


@pytest.mark.parametrize("tail", [0.0, 1.0, 3.0])
def test_the_reviewers_mill_probe_refuses_the_ordinary_sentences_final_beat(tmp_path, monkeypatch, tail):
    from src import scene_planner
    words, _ = _words(HOOK, MILL, MILL_TODAY)
    beats = scene_planner.segment_beats(words, words[-1]["start"] + words[-1]["dur"] + tail)
    owners = _owners(beats, HOOK, MILL, MILL_TODAY)
    assert owners[0] == {0}
    granted = _fit(tmp_path, monkeypatch, beats, _run(tmp_path, [HOOK_VERDICT, *MILL_VERDICTS], words))
    inside = [beats[k]["text"] for k, o in enumerate(owners) if o == {1}]
    others = [beats[k]["text"] for k, o in enumerate(owners) if k and o != {1}]
    assert inside and all(granted[text] is True for text in inside)        # s1's own beats keep the exception
    assert others and not any(granted[text] for text in others)            # s2's and spanning beats never
    if tail:   # the probe's case: the ordinary sentence's short final beat stands on its own
        assert any(o == {2} and beats[k]["text"] == "the sands." for k, o in enumerate(owners))


@pytest.mark.parametrize("tail", [0.0, 1.0, 3.0])
def test_the_mill_probe_places_each_beat_in_its_own_sentence(tmp_path, tail):
    _, beats, mapped = _split(tmp_path, MILL, MILL_TODAY, verdicts=MILL_VERDICTS, tail=tail)
    for b, v, o in zip(beats, mapped, _owners(beats, MILL, MILL_TODAY)):
        if len(o) > 1:
            assert v is None                                                   # spans two sentences: refused
        else:
            (i,) = o
            assert v["sentence"] == (MILL, MILL_TODAY)[i] and v["placement"]["sentence_index"] == i
            assert _granted(b["text"], v) is (i == 0)


def _hand_beats(groups, step=0.5):
    """Beats cut by hand at the given word groups, with their word offsets (as segment_beats records them)."""
    beats, at = [], 0
    for g in groups:
        n = len(g.split())
        beats.append({"start": at * step, "end": (at + n) * step, "text": g, "word_span": [at, at + n]})
        at += n
    return beats


def test_repeated_words_across_sentences_take_their_own_sentences_verdict(tmp_path, monkeypatch):
    first = "Inside the mill at dawn, the great wheel turned."
    second = "Long after the flood, the great wheel turned, the miller's children say."
    verdicts = [HOOK_VERDICT, verdict(first), {"sentence": second, "kind": "shown", "visual": "named_subject"}]
    words, _ = _words(HOOK, first, second, step=0.5)
    beats = _hand_beats([HOOK, "Inside the mill at dawn,", "the great wheel turned.", "Long after the flood,",
                         "the great wheel turned,", "the miller's children say."])
    run = _run(tmp_path, verdicts, words)
    granted = _fit(tmp_path, monkeypatch, beats, run)
    assert granted["Inside the mill at dawn,"] is True and granted["the great wheel turned."] is True
    # the same words as beat 3, spoken in the ordinary sentence: beat 5 never takes the first sentence's exception
    assert not granted["the great wheel turned,"] and not granted["the miller's children say."]
    mapped = _placed_verdicts(run, beats)
    assert [v["placement"]["sentence_index"] for v in mapped] == [0, 1, 1, 2, 2, 2]
    assert _granted(beats[2]["text"], mapped[2]) and not _granted(beats[4]["text"], mapped[4])


def test_a_beat_with_no_word_timeline_is_refused(tmp_path, monkeypatch):
    from src import scene_planner
    words, total = _words(HOOK, MILL, MILL_TODAY)
    beats = scene_planner.segment_beats(words, total)
    assert any(o == {1} for o in _owners(beats, HOOK, MILL, MILL_TODAY))
    run = _run(tmp_path, [HOOK_VERDICT, *MILL_VERDICTS], None)          # no words.json: nothing can be placed
    assert not any(_fit(tmp_path, monkeypatch, beats, run).values())
    assert _placed_verdicts(run, beats) == [None] * len(beats)
    (run / "words.json").write_text(json.dumps(words), encoding="utf-8")   # with it, s1's beats are placed
    assert any(v and v["placement"]["sentence_index"] == 1 for v in _placed_verdicts(run, beats))


def test_a_beat_the_word_alignment_cannot_place_with_certainty_is_refused(tmp_path):
    # The voice timeline lacks words the script has (a number it did not time). Beats in a block of the alignment
    # that occurs once in each stream are placed; a block that also occurs elsewhere ("the sands") and a beat
    # across the gap are not.
    script = "Inside the mill, the great wheel turned 44 m, the sands 12 m."
    words = [w for w in _words(script, MILL_TODAY)[0] if w["word"].strip(",.") not in ("44", "m", "12")]
    beats = _hand_beats(["Inside the mill, the great wheel turned", "the sands", "Today the river has silted up,",
                         "the mill is gone, the sands."])
    run = _run(tmp_path, [verdict(script), MILL_VERDICTS[1]], words)
    mapped = _placed_verdicts(run, beats)
    assert mapped[0]["placement"]["sentence_index"] == 0 and _granted(beats[0]["text"], mapped[0])
    assert mapped[1] is None                                            # "the sands": no certain place
    assert [v["placement"]["sentence_index"] for v in mapped[2:]] == [1, 1]
    across = _hand_beats(["Inside the mill, the great", "wheel turned the sands", "Today the river has silted up,",
                          "the mill is gone, the sands."])
    assert _placed_verdicts(run, across)[1] is None


def test_a_missing_malformed_or_stale_word_span_is_refused(tmp_path):
    words, _ = _words(MILL, MILL_TODAY)
    good = _hand_beats(["Inside the mill, the great wheel turned,", "grinding grain for the town, the sands."])
    run = _run(tmp_path, MILL_VERDICTS, words)
    assert all(_placed_verdicts(run, good))
    for bad in ({k: x for k, x in good[0].items() if k != "word_span"}, dict(good[0], word_span=[0, 99]),
                dict(good[0], word_span=[1, 7]), dict(good[0], word_span=[3, 3]), dict(good[0], word_span="0-6"),
                dict(good[0], word_span=[True, 6]), dict(good[0], text="Inside the mill, the bronze wheel turned,")):
        assert _placed_verdicts(run, [bad]) == [None]


def test_a_verdict_not_placed_on_the_beat_by_word_position_grants_nothing():
    rec = _reconstruction()
    s1 = verdict(MILL)
    # fragments handed s1's verdict without a placement, as the first-containing-sentence lookup did ("the sands."
    # ends both mill sentences)
    for fragment in ("the sands.", "the great wheel turned,", "Inside the mill,"):
        assert rec.approved(fragment, s1) is False
        assert image_gen.illustration_reason(fragment, verdict=s1) == ""
    # placed on a different beat, or a placement not made by word position
    assert rec.approved("the sands.", placed(s1, "Inside the mill,")) is False
    assert rec.approved("the sands.", {**s1, "placement": {"by": "text", "sentence_index": 0,
                                                           "beat": "the sands."}}) is False
    assert rec.approved("the sands.", {**s1, "placement": {"by": "word_position", "sentence_index": True,
                                                           "beat": "the sands."}}) is False
    assert rec.approved("the sands.", placed(s1, "the sands.")) is True    # the same check with a placement


def test_a_placement_in_the_record_itself_is_never_trusted(tmp_path):
    from src import picture_coverage
    forged = {**verdict(MILL_TODAY), "kind": "shown",
              "placement": {"by": "word_position", "sentence_index": 0, "beat": "the sands."}}
    words, _ = _words(MILL, MILL_TODAY)
    beats = _hand_beats(["Inside the mill, the great wheel turned,", "grinding grain for the town, the sands.",
                         "Today the river has silted up,", "the mill is gone,", "the sands."])
    run = _run(tmp_path, [verdict(MILL), forged], words)
    assert all("placement" not in (v or {}) for v in picture_coverage.beat_verdicts(run, beats))
    mapped = _placed_verdicts(run, beats)
    assert [v["placement"]["sentence_index"] for v in mapped] == [0, 0, 1, 1, 1]
    assert not _granted("the sands.", mapped[4])
