"""Reliable depiction, a hard requirement of the illustration tier (operator, 2026-11-02: "Any illustration we're
going to be using for no-photo must reliably depict that thing....that's the whole point").

- A named subject is drawn only from an openly licensed reference image of that very thing, passed into the request.
- Two independent checks must both pass: the reference comparison and the descriptor checklist (fail closed).
- The references are recorded in the lineage and the credits; illustrated beats go on the review card and a run
  with an illustration is never auto-approved.

No network and no model: the image API, the vision checks, OCR, the museum search and the picture looks are fakes.
"""
import base64
import json
import sys
import types
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from src import (
    beat_images,
    config,
    credits,
    image_gen,
    image_quarantine,
    lineage,
    reconstruction,
    scene_planner,
    telegram_bot,
)
from src import picture_coverage as pc
from src import prevoice_lines as pl
from src import substrate_compositor as sc

GEARS = "The bronze gears of the mechanism turned inside a wooden case."
SHEET = {"facts": [
    {"id": "F1", "statement": "The mechanism had bronze gears housed in a wooden case about 34 cm wide.",
     "kind": "other", "disputed": False, "evidence": [{"url": "https://www.nature.com/x", "quote": "bronze gears"}]}]}
TOPIC = {"id": "antikythera", "title": "Antikythera mechanism"}


# ------------------------------------------------------------------ helpers
def _engraving(size=(1080, 1920)) -> Image.Image:
    im = Image.new("RGB", size, (0, 0, 0))
    d = ImageDraw.Draw(im)
    for x in range(0, size[0], 10):
        d.line([(x, 0), (x, size[1])], fill=(245, 245, 245), width=2)
    return im


def _png(im: Image.Image) -> bytes:
    import io
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _photo(path: Path, size=(400, 400), colour=(120, 90, 40)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(path)
    return path


def _ref(tmp_path: Path, name="ref.jpg", title="Antikythera mechanism fragment A", licence="CC BY 4.0") -> dict:
    p = _photo(tmp_path / "refs" / name)
    return {"path": str(p), "title": title, "licence": licence, "url": "https://example.org/a",
            "license_url": "https://creativecommons.org/licenses/by/4.0/", "source": "topic_photo",
            "artist": "Jane Doe", "sha256": credits.sha256_file(p)}


def _fake_ocr(monkeypatch, text=""):
    mod = types.ModuleType("pytesseract")
    mod.image_to_string = lambda im: text
    monkeypatch.setitem(sys.modules, "pytesseract", mod)


def _spec():
    spec, why = image_gen.spec_from_facts(GEARS, TOPIC, SHEET)
    assert spec, why
    spec.update(reconstruction.bind(_placed(_reconstruction(GEARS), GEARS)))
    return spec


def _reconstruction(line):
    return {"sentence": line, "kind": "generable", "visual": pc.NAMED_SUBJECT, "reconstruction": True,
            "reconstruction_kind": "mechanism_in_use", "reconstruction_reason": "Only the ancient fragments survive."}


def _placed(verdict, beat):
    """The verdict as picture_coverage.placed_verdicts hands it on: placed on `beat` by word position."""
    return {**verdict, "placement": {"by": "word_position", "sentence_index": 0, "beat": beat}}


@pytest.fixture
def ready(monkeypatch):
    monkeypatch.setattr(image_gen, "is_available", lambda: True)
    monkeypatch.setattr(image_gen, "checkers_ready", lambda: (True, ""))


# ------------------------------------------------------------------ 1. no reference, no generation
def test_a_named_subject_without_a_reference_is_never_requested(tmp_path, monkeypatch, ready):
    monkeypatch.setattr(image_gen, "generate_image", lambda *a, **k: pytest.fail("no request may be made"))
    art, info = image_gen.illustrate_beat(GEARS, _spec(), tmp_path, visual=pc.NAMED_SUBJECT,
                                          budget=image_gen.Budget(tmp_path))
    assert art is None and info["calls"] == 0 and "reference" in info["reason"]
    assert image_gen.Budget(tmp_path).used() == 0
    # a reference whose file is gone counts as none
    gone = dict(_ref(tmp_path), path=str(tmp_path / "missing.jpg"))
    assert image_gen.illustrate_beat(GEARS, _spec(), tmp_path, visual=pc.NAMED_SUBJECT, references=[gone])[0] is None


def test_find_references_takes_only_open_licences_whose_title_names_the_subject(tmp_path):
    good = _photo(tmp_path / "t" / "good.jpg")
    nc = _photo(tmp_path / "t" / "nc.jpg", colour=(10, 200, 10))
    other = _photo(tmp_path / "t" / "other.jpg", colour=(200, 10, 10))
    tiny = _photo(tmp_path / "t" / "tiny.jpg", size=(100, 100), colour=(1, 2, 3))
    local = [{"path": str(nc), "title": "Antikythera mechanism, main fragment", "license": "CC BY-NC 4.0"},
             {"path": str(other), "title": "A Greek bronze statue", "license": "Public domain"},
             {"path": str(tiny), "title": "Antikythera mechanism", "license": "CC0"},
             {"path": str(good), "title": "Antikythera mechanism (fragment A)", "license": "CC BY 4.0",
              "source_url": "https://commons.example/x", "license_url": "https://creativecommons.org/licenses/by/4.0/",
              "artist": "Jane Doe"}]   # a CC BY reference needs its artist too (follow-up review, 2026-11-02)
    refs, why = image_gen.find_references(GEARS, TOPIC, local, tmp_path, remote=False)
    assert why == "" and [Path(r["path"]).name for r in refs] == ["good.jpg"]
    assert refs[0]["licence"] == "CC BY 4.0" and refs[0]["source"] == "topic_photo" and refs[0]["sha256"]
    # a proper name in the line is the subject: the topic's own photos of something else do not qualify
    refs, why = image_gen.find_references("The Pharos of Alexandria rose over the harbour.", TOPIC, local, tmp_path,
                                          remote=False)
    assert refs == [] and "pharos alexandria" in why
    assert image_gen.subject_terms("The Pharos of Alexandria rose.") == ["pharos", "alexandria"]


def test_museum_references_need_an_open_licence_a_naming_title_and_worldwide_loc_rights(tmp_path, monkeypatch):
    from src import museum_sources as ms
    found = [
        {"title": "Pharos of Alexandria, model (The Met 1)", "license": "CC0", "download_url": "https://m/1.jpg",
         "provider": "met", "source_url": "https://met/1"},
        {"title": "Pharos of Alexandria (Library of Congress)", "license": "Public domain", "download_url": "https://l/2.jpg",
         "provider": "loc", "description": "Rights: No known restrictions on publication in the U.S. Use elsewhere may be restricted."},
        {"title": "Lighthouse at night (Europeana, X)", "license": "CC BY 4.0", "download_url": "https://e/3.jpg",
         "provider": "europeana"},
        {"title": "Pharos of Alexandria engraving (Europeana, Y)", "license": "CC BY-NC 4.0",
         "download_url": "https://e/4.jpg", "provider": "europeana"},
    ]
    asked = {}
    monkeypatch.setattr(ms, "cached_search", lambda q, kws, sources=None, **k: asked.update(q=q, s=sources) or found)
    fetched = []

    def download(cand, work_dir):
        fetched.append(cand["download_url"])
        return _photo(Path(work_dir) / image_gen.REFERENCE_DIR / f"{len(fetched)}.jpg", colour=(len(fetched) * 40, 0, 0))
    monkeypatch.setattr(image_gen, "_download_reference", download)
    refs, why = image_gen.find_references("The Pharos of Alexandria rose over the harbour.", TOPIC, [], tmp_path,
                                          remote=True)
    assert why == "" and fetched == ["https://m/1.jpg"] and refs[0]["source"] == "met" and refs[0]["licence"] == "CC0"
    assert "loc" in asked["s"] and asked["q"] == "pharos alexandria"


# ------------------------------------------------------------------ 2. the reference goes into the request
class _Resp:
    def __init__(self, data, status=200):
        self.status_code, self._data, self.text = status, data, json.dumps(data)

    def json(self):
        return self._data


def test_the_reference_is_sent_as_an_inline_image_part(tmp_path, monkeypatch):
    seen = {}
    # A request is asked of the media cap and ledgered (QF-46): a funded cap on a scratch database.
    from src.db import PipelineDatabase
    monkeypatch.setattr(config, "MEDIA_MONTHLY_BUDGET_USD", 5.0, raising=False)
    ledger = PipelineDatabase(tmp_path / "ledger.db")
    monkeypatch.setattr("src.db.PipelineDatabase", lambda *a, **k: ledger)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k-1", raising=False)
    monkeypatch.setattr(config, "GENERATIVE_GAP_FILLING", True, raising=False)
    monkeypatch.setattr(config, "IMAGE_GEN_MODEL", "gemini-3-pro-image", raising=False)
    monkeypatch.setattr(config, "IMAGE_GEN_API", "auto", raising=False)
    monkeypatch.setattr(image_gen, "is_configured", lambda v: bool(v))
    image_gen._STATUS.clear()
    monkeypatch.setattr(image_gen.requests, "get", lambda *a, **k: _Resp({"models": [
        {"name": "models/gemini-3-pro-image"}, {"name": "models/imagen-x"}]}))

    def post(url, headers=None, json=None, timeout=None):
        seen["body"] = json
        return _Resp({"candidates": [{"content": {"parts": [{"inlineData": {"data": base64.b64encode(b"img").decode()}}]}}]})
    monkeypatch.setattr(image_gen.requests, "post", post)
    ref = Path(_ref(tmp_path)["path"])
    assert image_gen.generate_image("draw it", aspect_ratio="9:16", references=[ref]) == b"img"
    parts = seen["body"]["contents"][0]["parts"]
    assert parts[0] == {"text": "draw it"} and parts[1]["inlineData"]["mimeType"] == "image/jpeg"
    assert base64.b64decode(parts[1]["inlineData"]["data"])[:2] == b"\xff\xd8"
    cfg = seen["body"]["generationConfig"]
    assert cfg["responseModalities"] == ["IMAGE"] and cfg["imageConfig"] == {"aspectRatio": "9:16", "imageSize": "2K"}
    # an unreadable reference, or the Imagen API (no image input here): no request
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not an image")
    monkeypatch.setattr(image_gen.requests, "post", lambda *a, **k: pytest.fail("no request may be made"))
    assert image_gen.generate_image("draw", references=[bad]) is None
    monkeypatch.setattr(config, "IMAGE_GEN_MODEL", "imagen-x", raising=False)
    image_gen._STATUS.clear()
    assert image_gen.generate_image("draw", references=[ref]) is None
    image_gen._STATUS.clear()


def test_the_default_model_is_gemini_3_pro_image_unless_one_is_named(monkeypatch):
    # The default now lives in config (settings inventory); a model the operator names is used as named.
    assert image_gen.DEFAULT_MODEL == "gemini-3-pro-image"
    if not __import__("os").getenv("IMAGE_GEN_MODEL"):
        assert config.IMAGE_GEN_MODEL == "gemini-3-pro-image"
    monkeypatch.setattr(config, "IMAGE_GEN_MODEL", "gemini-3-pro-image", raising=False)
    assert image_gen.model_id() == "gemini-3-pro-image"
    monkeypatch.setattr(config, "IMAGE_GEN_MODEL", "gemini-nano-banana-2.1", raising=False)
    assert image_gen.model_id() == "gemini-nano-banana-2.1"


def test_illustrate_beat_sends_the_references_scores_against_them_and_records_them(tmp_path, monkeypatch, ready):
    ref = _ref(tmp_path)
    calls, scored = [], []
    monkeypatch.setattr(image_gen, "generate_image", lambda p, aspect_ratio="9:16", timeout=None, references=():
                        calls.append((p, list(references))) or _png(_engraving()))
    monkeypatch.setattr(image_quarantine, "score_candidate",
                        lambda raw, final, spec, size, others=(), references=(): scored.append((spec, list(references)))
                        or (True, [], 1.0))
    art, info = image_gen.illustrate_beat(GEARS, _spec(), tmp_path, candidates=1, visual=pc.NAMED_SUBJECT,
                                          references=[ref], reason=image_gen.RECONSTRUCTION_REASON)
    assert art is not None and info["references"][0]["title"] == ref["title"]
    prompt, sent = calls[0]
    assert sent == [Path(ref["path"])] and image_gen.REFERENCE_INSTRUCTION in prompt
    assert "keep its structure exactly" in prompt and "add nothing not in the reference or the facts" in prompt
    assert scored[0][1] == [Path(ref["path"])] and scored[0][0]["visual"] == pc.NAMED_SUBJECT
    rec = json.loads(lineage.sidecar(art).read_text(encoding="utf-8"))["settings"]["inputs"]
    assert rec["references"][0]["licence"] == "CC BY 4.0" and rec["references"][0]["path"] == ref["path"]
    assert rec["references"][0]["sha256"] == ref["sha256"] and rec["visual"] == pc.NAMED_SUBJECT
    assert rec["why_illustrated"] == image_gen.RECONSTRUCTION_REASON
    # another reference is another picture (the cache key carries its digest)
    other = _ref(tmp_path, "ref2.jpg")
    _photo(Path(other["path"]), colour=(5, 5, 200))
    other["sha256"] = credits.sha256_file(Path(other["path"]))
    art2, _ = image_gen.illustrate_beat(GEARS, _spec(), tmp_path, candidates=1, visual=pc.NAMED_SUBJECT,
                                        references=[other], reason=image_gen.UNFIT_REASON)
    assert art2 is not None and art2 != art


def test_a_kind_of_thing_without_a_reference_is_still_drawn_from_its_descriptors(tmp_path, monkeypatch, ready):
    calls = []
    monkeypatch.setattr(image_gen, "generate_image", lambda p, aspect_ratio="9:16", timeout=None:
                        calls.append(p) or _png(_engraving()))   # called with no references argument at all
    monkeypatch.setattr(image_quarantine, "score_candidate", lambda *a, **k: (True, [], 1.0))
    art, info = image_gen.illustrate_beat(GEARS, _spec(), tmp_path, candidates=1, visual=pc.KIND_OF_THING)
    assert art is not None and info["references"] == [] and len(calls) == 1
    assert image_gen.REFERENCE_INSTRUCTION not in calls[0] and "bronze" in calls[0]
    # still no illustration without fact-sheet descriptors
    assert image_gen.spec_from_facts("A kind of quiet settled.", TOPIC, SHEET)[0] is None


# ------------------------------------------------------------------ 3. two independent checks
def _candidate(tmp_path):
    raw, final = tmp_path / "r.png", tmp_path / "f.jpg"
    _engraving().save(raw)
    sc.composite_engraving_on_ink(raw, final, (1080, 1920))
    return raw, final


PASSING = {"descriptors": [{"descriptor": "bronze", "shown": True}, {"descriptor": "gears", "shown": True},
                           {"descriptor": "mechanism", "shown": True}],
           "has_text": False, "anachronism": False, "identifiable_face": False, "subject_in_safe_zone": True,
           "could_be_mistaken_for_photograph": False}
SAME = {"same_object": True, "same_count_of_parts": True, "same_shapes": True, "same_proportions": True,
        "nothing_added": True, "added": [], "confidence": 0.95}


def _vision(monkeypatch, descriptor=None, reference=None, log=None):
    from src import free_llm
    monkeypatch.setattr(image_quarantine, "_vision_ready", lambda: True)

    def structured(system, prompt, schema, role="writer", **k):
        if log is not None:
            log.append({"schema": schema.__name__, "role": role, "images": sum(1 for b in prompt if isinstance(b, dict)),
                        "text": " ".join(b for b in prompt if isinstance(b, str))})
        answer = reference if schema is image_quarantine.ReferenceComparison else descriptor
        if isinstance(answer, Exception):
            raise answer
        return schema(**(answer if answer is not None else {}))
    monkeypatch.setattr(free_llm, "structured", structured)


def test_both_checks_pass_in_separate_calls_and_the_descriptor_check_never_sees_the_reference(tmp_path, monkeypatch):
    raw, final = _candidate(tmp_path)
    refs = [Path(_ref(tmp_path)["path"]), Path(_ref(tmp_path, "b.jpg")["path"])]
    _fake_ocr(monkeypatch)
    calls = []
    _vision(monkeypatch, PASSING, SAME, calls)
    spec = dict(_spec(), visual=pc.NAMED_SUBJECT)
    ok, why, _ = image_quarantine.score_candidate(raw, final, spec, (1080, 1920), references=refs)
    assert ok, why
    desc = next(c for c in calls if c["schema"] == "IllustrationVerdict")
    comp = next(c for c in calls if c["schema"] == "ReferenceComparison")
    assert desc["images"] == 1 and comp["images"] == 1 + len(refs)          # the reference only in the comparison
    assert desc["role"] != comp["role"] and desc["text"] != comp["text"]  # another model where configured
    assert "reference" not in desc["text"].lower()
    models = image_quarantine.vision_models()
    assert models["descriptor"] == config.FREE_CHECKER_MODEL and models["reference"] == config.FREE_WRITER_MODEL


@pytest.mark.parametrize("bad", [{"same_object": False}, {"same_count_of_parts": False}, {"same_shapes": False},
                                 {"same_proportions": False}, {"nothing_added": False},
                                 {"added": ["a second dial"]}, {"confidence": 0.6}, None])
def test_a_reference_comparison_failure_rejects(tmp_path, monkeypatch, bad):
    raw, final = _candidate(tmp_path)
    _fake_ocr(monkeypatch)
    _vision(monkeypatch, PASSING, {**SAME, **bad} if bad is not None else {})   # None: every field left out
    ok, why, _ = image_quarantine.score_candidate(raw, final, dict(_spec(), visual=pc.NAMED_SUBJECT), (1080, 1920),
                                                  references=[Path(_ref(tmp_path)["path"])])
    assert not ok and "reference comparison" in why[0]


def test_a_descriptor_failure_rejects_even_when_the_reference_matches(tmp_path, monkeypatch):
    raw, final = _candidate(tmp_path)
    _fake_ocr(monkeypatch)
    calls = []
    _vision(monkeypatch, {**PASSING, "descriptors": [{"descriptor": "bronze", "shown": True}]}, SAME, calls)
    ok, why, _ = image_quarantine.score_candidate(raw, final, dict(_spec(), visual=pc.NAMED_SUBJECT), (1080, 1920),
                                                  references=[Path(_ref(tmp_path)["path"])])
    assert not ok and "not shown" in why[0] and "gears" in why[0]


def test_either_checker_unavailable_rejects(tmp_path, monkeypatch):
    raw, final = _candidate(tmp_path)
    ref = [Path(_ref(tmp_path)["path"])]
    spec = dict(_spec(), visual=pc.NAMED_SUBJECT)
    _fake_ocr(monkeypatch)
    _vision(monkeypatch, PASSING, RuntimeError("quota"))
    ok, why, _ = image_quarantine.score_candidate(raw, final, spec, (1080, 1920), references=ref)
    assert not ok and "reference comparison failed" in why[0]
    _vision(monkeypatch, RuntimeError("quota"), SAME)
    assert not image_quarantine.score_candidate(raw, final, spec, (1080, 1920), references=ref)[0]
    _vision(monkeypatch, PASSING, SAME)
    monkeypatch.setattr(image_quarantine, "_vision_ready", lambda: False)
    assert not image_quarantine.score_candidate(raw, final, spec, (1080, 1920), references=ref)[0]
    # a named subject scored with no reference is rejected outright; an unreadable reference too
    _vision(monkeypatch, PASSING, SAME)
    ok, why, _ = image_quarantine.score_candidate(raw, final, spec, (1080, 1920))
    assert not ok and "reference" in why[0]
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"x")
    assert not image_quarantine.score_candidate(raw, final, spec, (1080, 1920), references=[bad])[0]
    assert not image_quarantine.reference_check(final, [], spec)[0]


def test_the_vision_checks_send_the_image_as_a_content_block(tmp_path, monkeypatch):
    """free_llm.structured takes no `images` argument: the image goes in the prompt's blocks."""
    from src import free_llm
    img = tmp_path / "g.jpg"
    _engraving().save(img)
    monkeypatch.setattr(image_quarantine, "_vision_ready", lambda: True)
    seen = []

    def strict(system, prompt, schema, role="writer", attempts=4, task_class="heavy", timeout=None, thinking=None,
               request_timeout=None):   # free_llm.structured's own signature
        seen.append(prompt)
        return schema(**PASSING) if schema is image_quarantine.IllustrationVerdict else schema(passed=True)
    monkeypatch.setattr(free_llm, "structured", strict)
    assert image_quarantine.vision_rubric(img, _spec()) == (True, "pass")
    assert image_quarantine.check_historical_anachronism(img, "line", "classical_antiquity") == (True, "pass")
    assert all(isinstance(p, list) and p[1]["type"] == "image" for p in seen)


# ------------------------------------------------------------------ 4. lineage and credits
def test_the_credits_say_what_the_illustration_was_based_on(tmp_path):
    art = tmp_path / "gap_gen_abc.jpg"
    _engraving((54, 96)).save(art)
    ref = _ref(tmp_path, title="Antikythera mechanism fragment A")
    lineage.record_graphic(art, Path(image_gen.__file__), {"model": "m", "descriptors": ["bronze"], "fact_ids": ["F1"],
                                                           "references": [ref]})
    master = tmp_path / "master.mp4"
    master.write_bytes(b"mp4")
    ledger_path = credits.generate_provenance_ledger(run_dir=tmp_path, topic={"id": "t", "title": "T"},
                                                     payload={"caption": "c"}, master_mp4=master, media_items=[art])
    rec = next(a for a in json.loads(ledger_path.read_text(encoding="utf-8"))["assets"] if a["file"] == art.name)
    assert rec["illustration"]["based_on"] == "based on: Antikythera mechanism fragment A, CC BY 4.0"
    assert rec["illustration"]["references"][0]["url"] == "https://example.org/a"
    assert rec["artist"] == "AI illustration (m), based on: Antikythera mechanism fragment A, CC BY 4.0"
    payload = credits.add_provenance_attributions({"caption": "c", "description": "d"}, ledger_path)
    assert "Jane Doe" in payload["caption"] and "Antikythera mechanism fragment A" in payload["description"]
    assert image_gen.based_on([ref]) == "based on: Antikythera mechanism fragment A, CC BY 4.0"


# ------------------------------------------------------------------ 5. the planner and the coverage record
@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(beat_images, "FIT_CACHE_FILE", tmp_path / "cache" / "picture_fit.json")
    monkeypatch.setattr(config, "PICTURE_VISION_CHECK", True)
    monkeypatch.setattr(config, "GENERATIVE_GAP_FILLING", True, raising=False)
    monkeypatch.setattr(config, "PICTURE_CASCADE", True, raising=False)
    monkeypatch.setattr(config, "MAX_GAP_IMAGES_PER_RUN", 2, raising=False)
    scene_planner._folder_credits.cache_clear()
    monkeypatch.setattr(beat_images, "picture_look", lambda path, query, size=512, ask=None: {
        "checked": True, "shows": "unseen" not in query.lower(), "burned_in_text": False, "reason": "r"})
    monkeypatch.setattr(scene_planner, "_fact_sheet_for", lambda topic, payload: SHEET)
    yield tmp_path
    scene_planner._folder_credits.cache_clear()


NAMED = "The unseen Antikythera mechanism hid bronze gears in a wooden case."
FILL = [f"The harbour wall stood firm in year {i}." for i in range(12)]


def _beats(lines, seconds=3.0):
    """One beat per line, with its word offsets in the voiceover's word timeline (_voice), as segment_beats
    records them."""
    ends = [sum(len(t.split()) for t in lines[:i + 1]) for i in range(len(lines))]
    return [{"start": seconds * i, "end": seconds * (i + 1), "text": t, "word_span": [ends[i] - len(t.split()), ends[i]]}
            for i, t in enumerate(lines)]


def _voice(run: Path, lines):
    """The voiceover's word timeline (words.json) for `lines`: a reconstruction's verdict is placed on its beat by
    the beat's word position in it (picture_coverage.placed_verdicts)."""
    words = [{"word": w, "start": 0.3 * i, "dur": 0.25} for i, w in enumerate(" ".join(lines).split())]
    (run / "words.json").write_text(json.dumps(words), encoding="utf-8")


def _plain(folder: Path, n: int):
    return [_photo(folder / f"p{i}.jpg", size=(48, 48), colour=(20 * i % 255, 90, 40)) for i in range(n)]


def test_the_planner_falls_through_and_records_why_when_no_reference_exists(world, monkeypatch, ready):
    run = world / "run"
    lines = ["The aqueduct strides across the valley.", NAMED] + FILL[:10]
    run.mkdir()
    (run / pl.RECORD_FILE).write_text(json.dumps({"verdicts": [
        {"sentence": NAMED, "kind": "unshown", "visual": pc.NAMED_SUBJECT}]}), encoding="utf-8")
    monkeypatch.setattr(image_gen, "generate_image", lambda *a, **k: pytest.fail("no request may be made"))
    from src import museum_sources
    searched = []
    monkeypatch.setattr(museum_sources, "cached_search", lambda q, kws, **k: searched.append(q) or [])
    photos = _plain(world / "x", len(lines))
    out, report = scene_planner.fit_to_lines(_beats(lines), photos, photos, "Antikythera", topic=TOPIC, work_dir=run)
    assert not out[1].name.startswith("gap_gen_") and not report["generated"]
    why = next(r["why"] for r in report["illustration_skipped"] if r["beat"] == 2)
    assert "reference" in why and "antikythera" in why
    row = pc.coverage(_beats(lines), out, report)[1]
    assert "reference" in row["illustration_skipped"] and "illustration" not in row
    assert searched == ["antikythera"]   # the museums were asked (faked) for the named subject before giving up


def test_the_planner_passes_the_approved_reference_and_the_card_lists_it(world, monkeypatch):
    run = world / "run"
    lines = ["The aqueduct strides across the valley.", NAMED] + FILL[:10]
    run.mkdir()
    (run / pl.RECORD_FILE).write_text(json.dumps({"verdicts": [
        {"sentence": NAMED, "kind": "unshown", "visual": pc.NAMED_SUBJECT}]}), encoding="utf-8")
    topic_dir = world / "topic"
    ref = _photo(topic_dir / "fragment.jpg")
    (topic_dir / "credits.json").write_text(json.dumps({"images": [{
        "file": "fragment.jpg", "title": "Antikythera mechanism, fragment A", "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/", "source_url": "https://commons/x",
        "artist": "Jane Doe", "vision_review": {"shows_topic": "yes", "usable": True, "safe": True}}]}), encoding="utf-8")
    got = {}

    def fake_illustrate(line, spec, work_dir, aspect="9:16", budget=None, others=(), references=(), visual=None, **k):
        got.update(refs=list(references), visual=visual)
        p = Path(work_dir) / "gap_gen_fake.jpg"
        _engraving((54, 96)).save(p)
        return p, {"model": "fake", "calls": 1, "references": list(references)}
    monkeypatch.setattr(image_gen, "illustrate_beat", fake_illustrate)
    photos = _plain(world / "x", len(lines)) + [ref]
    out, report = scene_planner.fit_to_lines(_beats(lines), photos, photos, "Antikythera", topic=TOPIC, work_dir=run)
    assert out[1].name == "gap_gen_fake.jpg" and got["visual"] == pc.NAMED_SUBJECT
    assert [Path(r["path"]).name for r in got["refs"]] == ["fragment.jpg"] and got["refs"][0]["licence"] == "CC BY 4.0"
    gen = report["generated"][0]
    assert gen["references"][0]["title"] == "Antikythera mechanism, fragment A" and gen["path"].endswith("gap_gen_fake.jpg")
    row = pc.coverage(_beats(lines), out, report)[1]
    assert row["illustration"]["references"][0]["licence"] == "CC BY 4.0"
    # the review card: the beat, its narrated line, the illustration and its reference
    note = image_gen.review_note(report["generated"])
    assert "Archival Reconstructions (1)" in note and NAMED[:40] in note and "gap_gen_fake.jpg" in note
    assert "Antikythera mechanism, fragment A (CC BY 4.0" in note and "never auto-approved" in note
    card = telegram_bot.TelegramApprovalBot._card_text({"title": "T", "narration_script": NAMED,
                                                        "reconstruction_note": note}, "r1", None, None)
    assert "fragment A" in card and "never auto-approved" in card
    albums = telegram_bot.illustration_review_items({"picture_fit": {"generated": report["generated"]}}, run)
    assert [Path(i["path"]).name for i in albums[0]] == ["gap_gen_fake.jpg", "fragment.jpg"]
    assert "needs your approval" in albums[0][0]["caption"] and "CC BY 4.0" in albums[0][1]["caption"]


# ------------------------------------------------------------------ 6. never auto-approved
def test_auto_approve_is_refused_when_an_illustration_is_used(monkeypatch):
    import main
    used = {"picture_fit": {"generated": [{"beat": 3, "line": "x"}]}}
    why = image_gen.auto_approve_refusal(used)
    assert "auto-approve refused" in why and "beat 3" in why
    assert image_gen.auto_approve_refusal({"picture_fit": {"generated": []}}) == ""
    assert image_gen.auto_approve_refusal({}) == ""
    alerts = []
    me = types.SimpleNamespace(_alert=alerts.append, db=types.SimpleNamespace(run_label=lambda r: "Run 1"))
    allowed = main.PipelineCoordinator._auto_approve_allowed
    assert allowed(me, "r1", used, True) is False and "beat 3" in alerts[0] and "Auto-approve refused" in alerts[0]
    assert allowed(me, "r1", {"picture_fit": {"generated": []}}, True) is True
    assert allowed(me, "r1", used, False) is False and len(alerts) == 1


# ------------------------------------------------------------------ 7. a reference fit to show is SHOWN
def _named_run(world, line, reconstruction=False, lines=None):
    run = world / "run"
    run.mkdir(parents=True, exist_ok=True)
    (run / pl.RECORD_FILE).write_text(json.dumps({"verdicts": [
        {"sentence": line, "kind": "unshown", "visual": pc.NAMED_SUBJECT,
         **(_reconstruction(line) if reconstruction else {})}]}), encoding="utf-8")
    if lines is not None:
        _voice(run, lines)
    return run


def _museum_ref(world, size, name="met.jpg", title="Antikythera mechanism, bronze fragment"):
    p = _photo(world / "museum" / name, size=size, colour=(150, 120, 60))
    return {"path": str(p), "title": title, "licence": "CC0", "url": "https://met/1", "source": "met",
            "license_url": "https://creativecommons.org/publicdomain/zero/1.0/", "artist": "The Met",
            "sha256": credits.sha256_file(p)}


SHOWN_LINE = "The unseen Antikythera mechanism survives as corroded bronze fragments."


def test_a_reference_fit_to_show_is_shown_as_the_exact_photo_never_illustrated(world, monkeypatch):
    run = _named_run(world, SHOWN_LINE)
    ref = _museum_ref(world, (1200, 1600))
    monkeypatch.setattr(image_gen, "find_references", lambda line, topic, local=(), work_dir=None, **k: ([ref], ""))
    looked = []

    def look(path, query, size=512, ask=None):
        looked.append(Path(path).name)
        shows = Path(path).name == "met.jpg" or "unseen" not in query.lower()
        return {"checked": True, "shows": shows, "burned_in_text": False, "reason": "r"}
    monkeypatch.setattr(beat_images, "picture_look", look)
    monkeypatch.setattr(image_gen, "illustrate_beat", lambda *a, **k: pytest.fail("never illustrated"))
    lines = ["The aqueduct strides across the valley.", SHOWN_LINE] + FILL[:10]
    photos = _plain(world / "x", len(lines))
    out, report = scene_planner.fit_to_lines(_beats(lines), photos, photos, "Antikythera", topic=TOPIC, work_dir=run)
    assert out[1] == Path(ref["path"]) and 2 in report["fits"]
    assert {"beat": 2, "tier": pc.EXACT, "to": "met.jpg", "cascade": False} in report["tiers"]
    assert any(s.get("reference") for s in report["swapped"]) and not report["generated"]
    credit = report["reference_credits"][0]
    assert credit["file"] == ref["path"] and credit["license"] == "CC0" and credit["source_url"] == "https://met/1"
    assert looked.count("met.jpg") == 2          # the line check, then the text and watermark look


@pytest.mark.parametrize("line, unfit_ref, expect", [
    # (b) every reference too small for the screen, though reliable as a reference
    (SHOWN_LINE, "small", image_gen.UNFIT_REASON),
    # (b) watermarked
    (SHOWN_LINE, "text", image_gen.UNFIT_REASON),
    # (a) a reconstruction: the photo of today's fragments does not show the mechanism in use
    ("The unseen Antikythera mechanism once turned to show the planets.", "line", image_gen.RECONSTRUCTION_REASON),
    # neither: the reference does not show what the line says, and the line is no reconstruction: no illustration
    (SHOWN_LINE, "line", ""),
])
def test_a_named_subject_is_illustrated_only_for_a_reconstruction_or_unfit_references(world, monkeypatch, line,
                                                                                       unfit_ref, expect):
    lines = ["The aqueduct strides across the valley.", line] + FILL[:10]
    run = _named_run(world, line, reconstruction=expect == image_gen.RECONSTRUCTION_REASON, lines=lines)
    ref = _museum_ref(world, (300, 400) if unfit_ref == "small" else (1200, 1600))
    monkeypatch.setattr(image_gen, "find_references", lambda line, topic, local=(), work_dir=None, **k: ([ref], ""))

    def look(path, query, size=512, ask=None):
        if Path(path).name == "met.jpg":
            return {"checked": True, "shows": unfit_ref != "line", "burned_in_text": unfit_ref == "text", "reason": "r"}
        return {"checked": True, "shows": "unseen" not in query.lower(), "burned_in_text": False, "reason": "r"}
    monkeypatch.setattr(beat_images, "picture_look", look)
    got = {}

    def illustrate(line, spec, work_dir, references=(), visual=None, reason="", **k):
        got.update(reason=reason, refs=list(references))
        if visual == pc.NAMED_SUBJECT and reason not in image_gen.ILLUSTRATION_REASONS:   # the real gate's rule
            return None, {"reason": "no reason to illustrate", "calls": 0}
        p = Path(work_dir) / "gap_gen_r.jpg"
        _engraving((54, 96)).save(p)
        return p, {"model": "fake", "calls": 1, "references": list(references)}
    monkeypatch.setattr(image_gen, "illustrate_beat", illustrate)
    monkeypatch.setattr(scene_planner, "_fact_sheet_for", lambda topic, payload: {"facts": [
        {"id": "F9", "statement": f"{line} It is made of bronze gears in a wooden case.", "disputed": False}]})
    photos = _plain(world / "x", len(lines))
    out, report = scene_planner.fit_to_lines(_beats(lines), photos, photos, "Antikythera", topic=TOPIC, work_dir=run)
    assert out[1] != Path(ref["path"]) and got["reason"] == expect and got["refs"] == [ref]
    if expect:
        assert out[1].name == "gap_gen_r.jpg" and report["generated"][0]["why_illustrated"] == expect
        row = pc.coverage(_beats(lines), out, report)[1]
        assert row["illustration"]["why_illustrated"] == expect and row["references_not_shown"]
    else:
        assert not report["generated"] and not out[1].name.startswith("gap_gen_")


def test_the_real_gate_refuses_a_named_subject_with_references_but_no_reason(tmp_path, monkeypatch, ready):
    monkeypatch.setattr(image_gen, "generate_image", lambda *a, **k: pytest.fail("no request may be made"))
    art, info = image_gen.illustrate_beat(GEARS, _spec(), tmp_path, visual=pc.NAMED_SUBJECT,
                                          references=[_ref(tmp_path)], reason="")
    assert art is None and "reconstruction" in info["reason"]
    assert image_gen.illustration_reason("The temple was built in 432 BC.", ["screen", "line"], 2) == ""
    assert image_gen.illustration_reason("The temple was built in 432 BC.", ["screen", "screen"], 2) == image_gen.UNFIT_REASON
    assert image_gen.illustration_reason("The temple was built in 432 BC.", ["unknown"], 1) == ""
    line = "Inside, a staircase spiralled down."
    decision = {**_reconstruction(line), "reconstruction_kind": "unphotographed_interior",
                "reconstruction_reason": "The interior was destroyed before photography; only a plan survives."}
    assert image_gen.illustration_reason(line, [], 0, verdict=_placed(decision, line)) == image_gen.RECONSTRUCTION_REASON


# ------------------------------------------------------------------ 8. a named subject's crop is OF the named thing
def _big(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.effect_noise((3000, 2400), 80).convert("RGB").save(path, "JPEG")
    return path


def test_a_located_crop_is_cut_around_the_feature_and_confirmed(tmp_path):
    photo = _big(tmp_path / "site.jpg")
    feature = (0.70, 0.40, 0.80, 0.60)
    got = pc.located_crop(photo, tmp_path / "run", [], 1080 / 1920, "Pharos", locate=lambda p, s, l: feature,
                          confirm=lambda c, s: True, min_width=600)
    assert got is not None
    crop, box = got
    assert box[0] <= feature[0] and box[2] >= feature[2] and box[1] <= feature[1] and box[3] >= feature[3]
    assert pc.is_detail_crop(crop) and json.loads(lineage.sidecar(crop).read_text(encoding="utf-8"))["settings"]["located"] == "Pharos"
    # the second look says no: no crop, nothing left behind
    none = pc.located_crop(photo, tmp_path / "run2", [], 1080 / 1920, "Pharos", locate=lambda p, s, l: feature,
                           confirm=lambda c, s: False, min_width=600)
    assert none is None and not any(p.suffix in (".jpg", ".png") for p in (tmp_path / "run2").rglob("*"))
    # nothing located (low confidence or the locator down): no crop
    assert pc.located_crop(photo, tmp_path / "run3", [], 1080 / 1920, "Pharos", locate=lambda p, s, l: None,
                           confirm=lambda c, s: True) is None


def test_the_locator_takes_only_high_confidence_and_fails_closed(tmp_path, monkeypatch):
    from src import free_llm
    photo = _big(tmp_path / "site.jpg")
    monkeypatch.setattr(free_llm, "ready", lambda: True)
    answer = {"label": "Pharos", "visible": True, "x": 0.5, "y": 0.5, "w": 0.2, "h": 0.3, "confidence": "high"}
    monkeypatch.setattr(free_llm, "structured", lambda s, p, schema, **k: schema(**answer))
    assert pc.locate_feature(photo, "Pharos") == pytest.approx((0.4, 0.35, 0.6, 0.65))
    answer["confidence"] = "medium"
    assert pc.locate_feature(photo, "Pharos") is None
    answer.update(confidence="high", visible=False)
    assert pc.locate_feature(photo, "Pharos") is None

    def boom(*a, **k):
        raise RuntimeError("quota")
    monkeypatch.setattr(free_llm, "structured", boom)
    assert pc.locate_feature(photo, "Pharos") is None
    monkeypatch.setattr(beat_images, "picture_look", lambda *a, **k: {"checked": False, "shows": True})
    assert not pc.crop_shows(photo, "Pharos")


def test_a_named_subject_takes_a_located_crop_never_one_chosen_by_detail(world, monkeypatch):
    line = "The unseen Pharos of Alexandria rose over the harbour."
    run = _named_run(world, line)
    topic_dir = world / "topic"
    big = _big(topic_dir / "harbour.jpg")
    (topic_dir / "credits.json").write_text(json.dumps({"images": [{
        "file": "harbour.jpg", "title": "Harbour of Alexandria", "license": "CC0", "source_url": "https://c/x",
        "vision_review": {"shows_topic": "yes", "usable": True, "safe": True}}]}), encoding="utf-8")
    scene_planner._folder_credits.cache_clear()
    monkeypatch.setattr(image_gen, "find_references", lambda *a, **k: ([], "none"))
    monkeypatch.setattr(image_gen, "illustrate_beat", lambda *a, **k: (None, {"reason": "off", "calls": 0}))
    asked = []
    monkeypatch.setattr(pc, "locate_feature", lambda p, s, l="": asked.append(s) or None)   # not found
    lines = ["The aqueduct strides across the valley.", line] + FILL[:10]
    photos = [big] + _plain(world / "x", len(lines) - 1)
    out, report = scene_planner.fit_to_lines(_beats(lines), photos, photos, "Alexandria", topic=TOPIC, work_dir=run)
    assert asked == ["pharos alexandria"] and not pc.is_detail_crop(out[1])   # no crop by detail for a named line
    # located and confirmed: the crop fills the beat
    monkeypatch.setattr(pc, "locate_feature", lambda p, s, l="": (0.45, 0.30, 0.55, 0.55))
    monkeypatch.setattr(pc, "crop_shows", lambda c, s: True)
    run2 = _named_run(world / "b", line)
    out, report = scene_planner.fit_to_lines(_beats(lines), photos, photos, "Alexandria", topic=TOPIC, work_dir=run2)
    assert pc.is_detail_crop(out[1]) and {"beat": 2, "tier": pc.DETAIL_CROP, "to": out[1].name, "cascade": True} in report["tiers"]
    settings = json.loads(lineage.sidecar(out[1]).read_text(encoding="utf-8"))["settings"]
    assert settings["located"] == "pharos alexandria"
