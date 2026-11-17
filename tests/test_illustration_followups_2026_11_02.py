"""Follow-ups to the reference-grounded illustration tier (independent review of d3e9c64, 2026-11-02).

1. auto-approve is refused from the scenes themselves (the deep dive records no picture_fit);
2. a reconstruction's reference comparison may add only what the line and the descriptors describe;
3. a sentence's capitalised first word is not a subject term unless the topic's title has it;
4. a CC BY / CC BY-SA reference needs its artist, licence URL and source URL;
5. a named subject goes map, then held shot: never an unmatched graphic between them;
6. the legacy synthesize_gap_asset never draws a named subject without a reference.

No network and no model: every image, vision and museum call is a fake.
"""
import inspect
import io
import json
import logging
import sys
import types
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from src import (
    beat_images,
    config,
    image_gen,
    image_quarantine,
    lineage,
    reconstruction,
    scene_planner,
)
from src import picture_coverage as pc
from src import prevoice_lines as pl

TOPIC = {"id": "antikythera", "title": "Antikythera mechanism"}
SHEET = {"facts": [
    {"id": "F1", "statement": "The mechanism had bronze gears housed in a wooden case about 34 cm wide.",
     "kind": "other", "disputed": False, "evidence": [{"url": "https://www.nature.com/x", "quote": "bronze gears"}]}]}


def _photo(path: Path, size=(400, 400), colour=(120, 90, 40)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", size, colour)
    ImageDraw.Draw(im).rectangle([size[0] // 4, size[1] // 4, size[0] // 2, size[1] // 2], fill=(240, 240, 240))
    im.save(path)
    return path


def _png(size=(1080, 1920)) -> bytes:
    im = Image.new("RGB", size, (0, 0, 0))
    d = ImageDraw.Draw(im)
    for x in range(0, size[0], 10):
        d.line([(x, 0), (x, size[1])], fill=(245, 245, 245), width=2)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _beats(lines, seconds=3.0):
    return [{"start": seconds * i, "end": seconds * (i + 1), "text": t} for i, t in enumerate(lines)]


# ------------------------------------------------------------------ 1. auto-approve read from the scenes
def test_auto_approve_is_refused_for_an_illustration_file_without_a_picture_fit_record(tmp_path):
    photo = _photo(tmp_path / "photo.jpg")
    art = _photo(tmp_path / "gap_gen_0123456789abcdef.jpg")
    assert image_gen.auto_approve_refusal({}, [photo]) == ""
    why = image_gen.auto_approve_refusal({}, [photo, art])
    assert "auto-approve refused" in why and art.name in why
    # a name alone is enough (the file may be gone by the time the gate runs), and a str works as well as a Path
    assert "auto-approve refused" in image_gen.auto_approve_refusal(None, [str(tmp_path / "gap_gen_gone.jpg")])
    # junk in the scene list never raises
    assert image_gen.auto_approve_refusal({}, [None, "", 3]) == ""


def test_auto_approve_is_refused_when_the_lineage_records_an_ai_illustration(tmp_path):
    renamed = _photo(tmp_path / "scene_04.jpg")   # an illustration copied under another name
    lineage.sidecar(renamed).write_text(json.dumps({"settings": {"inputs": {"origin": "ai_illustration"}}}),
                                        encoding="utf-8")
    other = _photo(tmp_path / "scene_05.jpg")
    lineage.sidecar(other).write_text(json.dumps({"settings": {"inputs": {"origin": "graphic"}}}), encoding="utf-8")
    assert image_gen.auto_approve_refusal({"picture_fit": {"generated": []}}, [other]) == ""
    assert renamed.name in image_gen.auto_approve_refusal({"picture_fit": {"generated": []}}, [other, renamed])


def test_the_coordinator_gate_reads_the_scenes_on_both_paths(tmp_path):
    import main
    alerts = []
    me = types.SimpleNamespace(_alert=alerts.append, db=types.SimpleNamespace(run_label=lambda r: "Run 1"))
    allowed = main.PipelineCoordinator._auto_approve_allowed
    art = tmp_path / "gap_gen_abc.jpg"
    assert allowed(me, "r1", {}, True, scenes=[art]) is False and art.name in alerts[0]
    assert allowed(me, "r1", {}, True, scenes=[tmp_path / "photo.jpg"]) is True
    assert allowed(me, "r1", {}, True) is True   # the old call shape still works
    # the deep dive (no picture_fit) and the reel both hand their rendered scenes to the gate
    deep = inspect.getsource(main.PipelineCoordinator._render_and_certify_deep_dive)
    assert "_auto_approve_allowed(run_id, payload, auto_approve, scenes=extended_scenes)" in deep
    reel = inspect.getsource(main.PipelineCoordinator._render_approve_publish)
    assert "_auto_approve_allowed(run_id, payload, auto_approve, scenes=scenes)" in reel


# ------------------------------------------------------------------ 2. a reconstruction's comparison
class _LLM:
    def __init__(self, verdict):
        self.verdict, self.prompts = verdict, []

    def structured(self, system, blocks, model, **k):
        self.prompts.append(blocks[0])
        return self.verdict


def _comparison(monkeypatch, verdict):
    llm = _LLM(verdict)
    from src import free_llm
    monkeypatch.setattr(free_llm, "structured", llm.structured)
    monkeypatch.setattr(image_quarantine, "_vision_ready", lambda: True)
    return llm


GEARS = "The bronze gears of the mechanism turned inside a wooden case."
_YES = {"same_object": True, "same_count_of_parts": True, "same_shapes": True, "same_proportions": True,
        "confidence": 0.95}


def _reconstruction():
    return {"sentence": GEARS, "kind": "generable", "visual": pc.NAMED_SUBJECT, "reconstruction": True,
            "reconstruction_kind": "mechanism_in_use", "reconstruction_reason": "Only ancient fragments survive.",
            "reconstruction_cause": "no_longer_operating", "reconstruction_subject_words": "bronze gears",   # QF-60b
            "placement": {"by": "word_position", "sentence_index": 0, "beat": GEARS}}   # placed on the GEARS beat


def test_a_reconstruction_may_add_only_what_the_line_and_descriptors_describe(tmp_path, monkeypatch):
    draw, ref = _photo(tmp_path / "d.jpg"), _photo(tmp_path / "r.jpg", colour=(10, 10, 200))
    spec = {"topic": "Antikythera mechanism", "required": ["bronze gears", "wooden case"], "line": GEARS,
            "why_illustrated": image_gen.RECONSTRUCTION_REASON, **reconstruction.bind(_reconstruction())}
    llm = _comparison(monkeypatch, image_quarantine.ReferenceComparison(**_YES, nothing_added=True))
    ok, why = image_quarantine.reference_check(draw, [ref], spec)
    assert ok, why
    prompt = llm.prompts[-1]
    assert "RECONSTRUCTION" in prompt and "ONLY" in prompt and GEARS in prompt
    assert "bronze gears; wooden case" in prompt and "Anything added beyond that description fails" in prompt
    # still fail closed: an addition beyond the description, or a verdict that leaves the field out
    _comparison(monkeypatch, image_quarantine.ReferenceComparison(**_YES, nothing_added=True, added=["a sailor"]))
    ok, why = image_quarantine.reference_check(draw, [ref], spec)
    assert not ok and "a sailor" in why
    _comparison(monkeypatch, image_quarantine.ReferenceComparison(**_YES))
    assert image_quarantine.reference_check(draw, [ref], spec)[0] is False


def test_any_other_illustration_keeps_the_strict_nothing_added_rule(tmp_path, monkeypatch):
    draw, ref = _photo(tmp_path / "d.jpg"), _photo(tmp_path / "r.jpg", colour=(10, 10, 200))
    llm = _comparison(monkeypatch, image_quarantine.ReferenceComparison(**_YES, nothing_added=True))
    for spec in ({"required": ["bronze gears"], "line": GEARS, "why_illustrated": image_gen.UNFIT_REASON},
                 {"why_illustrated": image_gen.RECONSTRUCTION_REASON}):   # a reconstruction with nothing described
        image_quarantine.reference_check(draw, [ref], spec)
        assert "RECONSTRUCTION" not in llm.prompts[-1]
        assert "that the references do not show" in llm.prompts[-1]


def test_illustrate_beat_tells_the_rubric_why_and_what_the_line_says(tmp_path, monkeypatch):
    monkeypatch.setattr(image_gen, "is_available", lambda: True)
    monkeypatch.setattr(image_gen, "checkers_ready", lambda: (True, ""))
    monkeypatch.setattr(image_gen, "generate_image", lambda *a, **k: _png())
    seen = []
    monkeypatch.setattr(image_quarantine, "score_candidate",
                        lambda raw, final, spec, size, others=(), references=(): seen.append(spec) or (False, ["x"], 0.0))
    ref = _photo(tmp_path / "refs" / "fragment.jpg")
    one = {"path": str(ref), "title": "Antikythera mechanism fragment", "licence": "CC0", "sha256": "abc"}
    spec = {"era": "classical_antiquity", "required": ["bronze gears"], "subject": "the mechanism",
            **reconstruction.bind(_reconstruction())}
    image_gen.illustrate_beat(GEARS, spec, tmp_path, visual=pc.NAMED_SUBJECT, references=[one],
                              reason=image_gen.RECONSTRUCTION_REASON, candidates=1)
    assert seen and seen[0]["why_illustrated"] == image_gen.RECONSTRUCTION_REASON and seen[0]["line"] == GEARS


# ------------------------------------------------------------------ 3. a sentence's first word
def test_a_sentence_initial_word_is_not_a_subject_term_unless_the_title_has_it():
    assert image_gen.subject_terms("Divers found the Antikythera wreck in 1900.", TOPIC) == ["antikythera"]
    assert image_gen.subject_terms("Like the Pharos, it guided ships.", TOPIC) == ["pharos"]
    assert image_gen.subject_terms("It sank. Sponge divers from Symi found it.", TOPIC) == ["symi"]
    # the title has it: kept, first
    assert image_gen.subject_terms("Antikythera divers found Roman bronzes.", TOPIC)[:1] == ["antikythera"]
    # no name left: the title's words, as before
    assert image_gen.subject_terms("Divers found it.", TOPIC) == ["antikythera", "mechanism"]
    assert image_gen.subject_terms("The Pharos of Alexandria rose.") == ["pharos", "alexandria"]


def test_a_real_reference_is_no_longer_missed_for_a_sentence_initial_word(tmp_path):
    good = _photo(tmp_path / "t" / "wreck.jpg")
    local = [{"path": str(good), "title": "Antikythera wreck finds, National Museum", "license": "CC0"}]
    refs, why = image_gen.find_references("Divers found the Antikythera wreck.", TOPIC, local, tmp_path, remote=False)
    assert why == "" and [Path(r["path"]).name for r in refs] == ["wreck.jpg"]


# ------------------------------------------------------------------ 4. a CC BY credit must be complete
_FULL = {"artist": "Jane Doe", "license_url": "https://creativecommons.org/licenses/by/4.0/",
         "source_url": "https://commons.example/x"}


@pytest.mark.parametrize("missing", ["artist", "license_url", "source_url"])
@pytest.mark.parametrize("licence", ["CC BY 4.0", "CC BY-SA 4.0"])
def test_a_cc_by_reference_without_its_full_credit_is_refused(tmp_path, monkeypatch, missing, licence):
    monkeypatch.setattr(config, "IMAGES_ALLOW_SHARE_ALIKE", True, raising=False)
    p = _photo(tmp_path / "t" / "ref.jpg")
    entry = {"path": str(p), "title": "Antikythera mechanism, fragment A", "license": licence, **_FULL}
    refs, _ = image_gen.find_references(GEARS, TOPIC, [entry], tmp_path, remote=False)
    assert len(refs) == 1 and refs[0]["artist"] == "Jane Doe"
    refs, why = image_gen.find_references(GEARS, TOPIC, [dict(entry, **{missing: ""})], tmp_path, remote=False)
    assert refs == [] and "no openly licensed reference" in why


def test_public_domain_and_cc0_need_no_credit_fields(tmp_path):
    a, b = _photo(tmp_path / "t" / "a.jpg"), _photo(tmp_path / "t" / "b.jpg", colour=(1, 200, 3))
    local = [{"path": str(a), "title": "Antikythera mechanism", "license": "CC0"},
             {"path": str(b), "title": "Antikythera mechanism, 1905 plate", "license": "Public domain"}]
    refs, _ = image_gen.find_references(GEARS, TOPIC, local, tmp_path, remote=False)
    assert [Path(r["path"]).name for r in refs] == ["a.jpg", "b.jpg"]
    assert image_gen.attribution_complete("CC0 1.0", "", "", "") and not image_gen.attribution_complete("CC BY 4.0", "x", "", "y")


def test_a_museum_cc_by_reference_without_an_artist_is_never_fetched(tmp_path, monkeypatch):
    from src import museum_sources as ms
    found = [{"title": "Antikythera mechanism (Europeana)", "license": "CC BY 4.0", "download_url": "https://e/1.jpg",
              "provider": "europeana", "source_url": "https://e/1", "license_url": _FULL["license_url"]},
             {"title": "Antikythera mechanism (Europeana, credited)", "license": "CC BY 4.0",
              "download_url": "https://e/2.jpg", "provider": "europeana", **_FULL}]
    monkeypatch.setattr(ms, "cached_search", lambda q, kws, sources=None, **k: found)
    fetched = []

    def download(cand, work_dir):
        fetched.append(cand["download_url"])
        return _photo(Path(work_dir) / image_gen.REFERENCE_DIR / f"{len(fetched)}.jpg", colour=(len(fetched) * 60, 0, 0))
    monkeypatch.setattr(image_gen, "_download_reference", download)
    refs, _ = image_gen.find_references(GEARS, TOPIC, [], tmp_path, remote=True)
    assert fetched == ["https://e/2.jpg"] and len(refs) == 1 and refs[0]["artist"] == "Jane Doe"


# ------------------------------------------------------------------ 5. map, then the held shot
@pytest.fixture
def planner(tmp_path, monkeypatch):
    monkeypatch.setattr(beat_images, "FIT_CACHE_FILE", tmp_path / "cache" / "picture_fit.json")
    monkeypatch.setattr(config, "PICTURE_VISION_CHECK", True)
    monkeypatch.setattr(config, "GENERATIVE_GAP_FILLING", True, raising=False)
    monkeypatch.setattr(config, "PICTURE_CASCADE", True, raising=False)
    scene_planner._folder_credits.cache_clear()
    monkeypatch.setattr(beat_images, "picture_look", lambda path, query, size=512, ask=None: {
        "checked": True, "shows": "unseen" not in query.lower(), "burned_in_text": False, "reason": "r"})
    monkeypatch.setattr(scene_planner, "_fact_sheet_for", lambda topic, payload: SHEET)
    monkeypatch.setattr(image_gen, "find_references", lambda *a, **k: ([], "no reference"))
    monkeypatch.setattr(image_gen, "illustrate_beat", lambda *a, **k: (None, {"reason": "no reference", "calls": 0}))
    graphic = tmp_path / "graphic_timeline.mp4"
    graphic.write_bytes(b"clip")
    Image.new("RGB", (48, 48)).save(graphic.with_suffix(".jpg"))
    monkeypatch.setattr(scene_planner, "_is_map", lambda p: False)   # no locator map fits this line
    monkeypatch.setattr(scene_planner.graphic_match, "judgeable", lambda g: True)
    monkeypatch.setattr(scene_planner.graphic_match, "score", lambda g, line: 0.0)
    yield types.SimpleNamespace(tmp=tmp_path, graphic=graphic)
    scene_planner._folder_credits.cache_clear()


UNSEEN = "The unseen chamber of the Pharos lay below the lamp."
FILL = [f"The harbour wall stood firm in year {i}." for i in range(12)]


def _plan(planner, visual):
    run = planner.tmp / f"run_{visual}"
    run.mkdir(parents=True, exist_ok=True)
    (run / pl.RECORD_FILE).write_text(json.dumps({"verdicts": [
        {"sentence": UNSEEN, "kind": "unshown", "visual": visual}]}), encoding="utf-8")
    lines = ["The aqueduct strides across the valley.", UNSEEN] + FILL[:8]
    photos = [_photo(planner.tmp / "x" / f"p{i}.jpg", size=(48, 48), colour=(20 * i % 255, 90, 40))
              for i in range(len(lines))]
    pool = photos + [planner.graphic]
    return scene_planner.fit_to_lines(_beats(lines), photos, pool, "Pharos", topic=TOPIC, work_dir=run)


def test_a_named_subject_never_takes_an_unmatched_graphic_after_the_map(planner):
    out, report = _plan(planner, pc.NAMED_SUBJECT)
    assert out[1] != planner.graphic
    assert not any(g.get("beat") == 2 for g in report["graphic"])
    assert any(h["beat"] == 2 for h in report["held"])   # the held shot, straight after the (absent) map


def test_a_kind_of_thing_still_takes_the_graphic(planner):
    out, report = _plan(planner, pc.KIND_OF_THING)   # control: the step itself is unchanged elsewhere
    assert out[1] == planner.graphic and any(g.get("beat") == 2 for g in report["graphic"])


# ------------------------------------------------------------------ 6. the legacy path
def test_legacy_gap_art_refuses_a_named_subject_without_a_reference(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(image_gen, "is_available", lambda: True)
    asked = []
    monkeypatch.setattr(image_gen, "generate_raw_linework", lambda prompt: asked.append(prompt) or None)
    with caplog.at_level(logging.INFO, logger=image_gen.log.name):
        assert image_gen.synthesize_gap_asset(3, "The bronze gears turned.", "classical_antiquity", TOPIC, tmp_path,
                                              visual=pc.NAMED_SUBJECT) is None
        assert image_gen.synthesize_gap_asset(4, "The Pharos of Alexandria rose over the harbour.",
                                              "classical_antiquity", TOPIC, tmp_path) is None
    assert asked == [] and caplog.text.count("Legacy gap art refused") == 2
    # an unnamed line is drawn as before, and the topic's title is never put in the prompt
    assert image_gen.synthesize_gap_asset(5, "Bronze gears turned.", "classical_antiquity", TOPIC, tmp_path) is None
    assert len(asked) == 1 and "Antikythera" not in asked[0] and "Subject:" not in asked[0]


def test_the_planner_never_calls_the_legacy_path():
    src = Path(scene_planner.__file__).read_text(encoding="utf-8")
    assert "synthesize_gap_asset" not in src


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
