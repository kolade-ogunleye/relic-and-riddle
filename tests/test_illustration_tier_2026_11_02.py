"""The illustration tier (2026-11-02): a labelled, topic-specific illustration for a narrated line no authoritative
photo shows, as the panel (archival producer, illustration art director, QA lead) recommended.

No network and no model: the image API, the vision checks, OCR and the picture looks are fakes.
"""
import base64
import io
import json
import logging
import sys
import types
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import (
    beat_images,
    config,
    credits,
    image_gen,
    image_quarantine,
    lineage,
    publisher,
    scene_planner,
    video_qa,
)
from src import picture_coverage as pc
from src import prevoice_lines as pl
from src import substrate_compositor as sc
from src.brand import BONE, INK2
from src.models import QAIssue, QAReport
from src.video_composer import (
    RECON_BADGE_LABEL,
    TRANS_DUR,
    reconstruction_badge_intervals,
)

GEARS = "The bronze gears of the mechanism turned inside a wooden case."
SHEET = {"facts": [
    {"id": "F1", "statement": "The mechanism had bronze gears housed in a wooden case about 34 cm wide.",
     "kind": "other", "disputed": False, "evidence": [{"url": "https://www.nature.com/x", "quote": "bronze gears"}]},
    {"id": "F2", "statement": "The wreck was dated to about 70 BC.", "kind": "date", "disputed": False,
     "evidence": [{"url": "https://www.britannica.com/y", "quote": "70 BC"}]}]}


# ------------------------------------------------------------------ helpers
def _engraving(size=(1080, 1920), ink=0.2, colour=(0, 0, 0)) -> Image.Image:
    """A dark-ground test 'engraving': fine bright hatching covering about `ink` of the frame."""
    im = Image.new("RGB", size, colour)
    d = ImageDraw.Draw(im)
    step = max(2, round(2 / max(ink, 0.01)))
    for x in range(0, size[0], step):
        d.line([(x, 0), (x, size[1])], fill=(245, 245, 245), width=2)
    return im


def _png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _fake_ocr(monkeypatch, text=""):
    mod = types.ModuleType("pytesseract")
    mod.image_to_string = lambda im: text
    monkeypatch.setitem(sys.modules, "pytesseract", mod)


def _no_ocr(monkeypatch):
    monkeypatch.setitem(sys.modules, "pytesseract", None)   # import raises ImportError


# ------------------------------------------------------------------ A1. coverage severity
def _cov(*rows):
    return {"beats": [dict(r) for r in rows]}


def test_critical_is_never_downgraded():
    rows = [{"beat": 4, "severity": "critical", "issue": "The image does not show the harbour named."}]
    assert video_qa.coverage_severity(rows, _cov({"beat": 4, "tier": pc.DETAIL_CROP, "cascade": True})) == rows


def test_the_illustration_tier_is_never_downgraded():
    rows = [{"beat": 3, "severity": "major", "issue": "The image does not show the gears named."}]
    assert video_qa.coverage_severity(rows, _cov({"beat": 3, "tier": pc.RECONSTRUCTION, "cascade": True})) == rows


def test_a_named_subject_is_downgraded_only_by_a_detail_or_archive_of_it():
    rows = [{"beat": 3, "severity": "major", "issue": "The image does not show the Pharos named."}]
    graphic = _cov({"beat": 3, "tier": pc.GRAPHIC, "cascade": True, "planned_visual": pc.NAMED_SUBJECT})
    assert video_qa.coverage_severity(rows, graphic) == rows
    detail = _cov({"beat": 3, "tier": pc.DETAIL_CROP, "cascade": True, "planned_visual": pc.NAMED_SUBJECT})
    assert video_qa.coverage_severity(rows, detail)[0]["severity"] == "info"


def test_shows_x_instead_of_y_stays_a_blocker():
    cov = _cov({"beat": 2, "tier": pc.SAME_KIND, "cascade": True})
    for issue in ("The frame shows a Greek temple instead of the Pantheon.",
                  "It shows a generic harbour rather than the Pharos.",
                  "The picture shows a fortress, not the lighthouse."):
        rows = [{"beat": 2, "severity": "major", "issue": issue}]
        assert video_qa.coverage_severity(rows, cov) == rows, issue
    assert not video_qa.coverage_warning("The frame shows a Greek temple instead of the Pantheon.")


def test_a_badge_or_disclosure_defect_is_a_picture_defect():
    assert not video_qa.coverage_warning("The disclosure badge is missing; it does not show the dome.")


def test_the_ceiling_blocks_too_many_downgrades():
    cov = _cov(*[{"beat": b, "tier": pc.DETAIL_CROP, "cascade": True, "seconds": 3.0} for b in (2, 3, 4)],
               *[{"beat": b, "tier": pc.EXACT, "cascade": False, "seconds": 3.0} for b in range(5, 15)])
    rows = [{"beat": b, "severity": "major", "issue": "The image does not show the arch named."} for b in (2, 3, 4)]
    out = video_qa.coverage_severity(rows, cov)
    assert [r["severity"] for r in out] == ["major"] * 3 and all(r["coverage_ceiling"] for r in out)
    # two beats, but over a quarter of the runtime
    short = _cov(*[{"beat": b, "tier": pc.DETAIL_CROP, "cascade": True, "seconds": 3.0} for b in (2, 3)],
                 {"beat": 4, "tier": pc.EXACT, "cascade": False, "seconds": 3.0})
    out = video_qa.coverage_severity(rows[:2], short)
    assert [r["severity"] for r in out] == ["major", "major"]
    # within the ceiling: warnings
    ok = _cov(*[{"beat": b, "tier": pc.DETAIL_CROP, "cascade": True, "seconds": 3.0} for b in (2, 3)],
              *[{"beat": b, "tier": pc.EXACT, "cascade": False, "seconds": 3.0} for b in range(4, 20)])
    assert [r["severity"] for r in video_qa.coverage_severity(rows[:2], ok)] == ["info", "info"]


def test_a_missing_coverage_record_is_logged(caplog):
    rows = [{"beat": 2, "severity": "major", "issue": "The image does not show it."}]
    with caplog.at_level(logging.WARNING, logger="src.video_qa"):
        assert video_qa.coverage_severity(rows, None) == rows
    assert "no beat coverage record" in caplog.text


def test_coverage_rows_carry_their_seconds(tmp_path):
    beats = [{"start": 0.0, "end": 2.5, "text": "a"}, {"start": 2.5, "end": 6.0, "text": "b"}]
    rows = pc.coverage(beats, [tmp_path / "a.jpg", tmp_path / "gap_gen_x.jpg"], {})
    assert [r["seconds"] for r in rows] == [2.5, 3.5] and rows[1]["tier"] == pc.RECONSTRUCTION


# ------------------------------------------------------------------ A2. reconstruction findings
def _report(*issues):
    return QAReport(summary="look", issues=[QAIssue(beat=b, severity=s, issue=t) for b, s, t in issues])


def test_only_pure_style_remarks_on_a_badged_illustration_are_dropped(tmp_path):
    assets = [tmp_path / "hero.jpg", tmp_path / "gap_gen_abc.jpg", tmp_path / "photo.jpg"]
    report = _report((2, "major", "The illustration shows an anachronistic cannon on the deck."),
                     (2, "critical", "This is an illustration, not a real photo."),
                     (2, "major", "The illustration does not show the bronze gears."),
                     (2, "major", "The illustration shows a cathedral instead of the temple."),
                     (2, "minor", "The visual is an engraving rather than a real photograph."),
                     (3, "minor", "The visual is an engraving rather than a real photograph."))
    out = video_qa.drop_reconstruction_aesthetic_findings(report, assets)
    kept = [(i.beat, i.issue) for i in out.issues]
    assert (2, "The visual is an engraving rather than a real photograph.") not in kept   # style only: dropped
    assert len(kept) == 5 and (3, "The visual is an engraving rather than a real photograph.") in kept
    assert any("anachronistic" in t for _, t in kept) and any("instead of the temple" in t for _, t in kept)


# ------------------------------------------------------------------ A3. synthesize_gap_asset (legacy path)
def test_a_compositing_failure_returns_none_never_the_raw_file(tmp_path, monkeypatch):
    monkeypatch.setattr(image_gen, "is_available", lambda: True)
    monkeypatch.setattr(image_gen, "generate_raw_linework", lambda *a, **k: _png(_engraving((270, 480))))

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(sc, "composite_linework_onto_substrate", boom)
    assert image_gen.synthesize_gap_asset(4, "The wreck lay on the seabed.", "classical_antiquity", {}, tmp_path) is None
    assert not list(tmp_path.glob("gap_gen_*")) and not list(tmp_path.glob("raw_gen_*"))


def test_the_cache_is_keyed_by_the_line_not_the_beat(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(image_gen, "is_available", lambda: True)
    monkeypatch.setattr(image_gen, "generate_raw_linework", lambda p, *a, **k: calls.append(p) or _png(_engraving((270, 480))))
    first = image_gen.synthesize_gap_asset(2, "Bronze gears turned.", "classical_antiquity", {}, tmp_path)
    again = image_gen.synthesize_gap_asset(7, "Bronze gears turned.", "classical_antiquity", {}, tmp_path)
    other = image_gen.synthesize_gap_asset(2, "A wooden hull sank.", "classical_antiquity", {}, tmp_path)
    assert first and first == again and len(calls) == 2 and other != first
    # an older beat-numbered file made for another line is not reused
    old = tmp_path / "gap_gen_beat_9.jpg"
    _engraving((270, 480)).save(old)
    lineage.record_graphic(old, Path(image_gen.__file__), {"line": "Something else entirely."})
    assert image_gen.synthesize_gap_asset(9, "Marble columns stood.", "classical_antiquity", {}, tmp_path) != old


def test_no_run_folder_means_no_generation(monkeypatch):
    monkeypatch.setattr(image_gen, "is_available", lambda: True)
    assert image_gen.synthesize_gap_asset(2, "x", "classical_antiquity", {}, None) is None
    assert image_gen.illustrate_beat("x", {"era": "classical_antiquity"}, None)[0] is None


# ------------------------------------------------------------------ A3/13. the request: header key, API styles
class _Resp:
    def __init__(self, data, status=200):
        self.status_code, self._data, self.text = status, data, json.dumps(data)

    def json(self):
        return self._data


def test_the_key_is_sent_in_the_header_and_gemini_takes_aspect_and_no_negative_prompt(tmp_path, monkeypatch):
    seen = {}
    # A request is asked of the media cap and ledgered (QF-46): a funded cap on a scratch database.
    from src.db import PipelineDatabase
    monkeypatch.setattr(config, "MEDIA_MONTHLY_BUDGET_USD", 5.0, raising=False)
    ledger = PipelineDatabase(tmp_path / "ledger.db")
    monkeypatch.setattr("src.db.PipelineDatabase", lambda *a, **k: ledger)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k-123", raising=False)
    monkeypatch.setattr(config, "GENERATIVE_GAP_FILLING", True, raising=False)
    monkeypatch.setattr(config, "IMAGE_GEN_MODEL", "gemini-test-image", raising=False)
    monkeypatch.setattr(config, "IMAGE_GEN_API", "auto", raising=False)
    monkeypatch.setattr(image_gen, "is_configured", lambda v: bool(v))
    image_gen._STATUS.clear()

    def get(url, headers=None, params=None, timeout=None):
        seen["get"] = (url, headers)
        return _Resp({"models": [{"name": "models/gemini-test-image"}, {"name": "models/imagen-x"}]})

    def post(url, headers=None, json=None, timeout=None):
        seen["post"] = (url, headers, json)
        b64 = base64.b64encode(b"img").decode()
        return _Resp({"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": b64}}]}}]})
    monkeypatch.setattr(image_gen.requests, "get", get)
    monkeypatch.setattr(image_gen.requests, "post", post)
    assert image_gen.generate_image("draw", aspect_ratio="16:9") == b"img"
    url, headers, body = seen["post"]
    assert "k-123" not in url and headers["x-goog-api-key"] == "k-123" and url.endswith(":generateContent")
    assert body["generationConfig"]["imageConfig"]["aspectRatio"] == "16:9" and "negativePrompt" not in json.dumps(body)
    assert "k-123" not in seen["get"][0] and seen["get"][1]["x-goog-api-key"] == "k-123"
    assert "gemini-test-image" in image_gen.doctor()
    # the legacy Imagen style
    monkeypatch.setattr(config, "IMAGE_GEN_MODEL", "imagen-x", raising=False)
    image_gen._STATUS.clear()
    monkeypatch.setattr(image_gen.requests, "post", lambda url, headers=None, json=None, timeout=None: (
        seen.__setitem__("post", (url, headers, json)) or _Resp({"predictions": [{"bytesBase64Encoded": base64.b64encode(b"i2").decode()}]})))
    assert image_gen.generate_image("draw") == b"i2"
    assert seen["post"][0].endswith("imagen-x:predict") and "negativePrompt" not in json.dumps(seen["post"][2])


def test_an_unreachable_model_fails_closed(monkeypatch):
    monkeypatch.setattr(image_gen, "is_available", lambda: True)
    monkeypatch.setattr(image_gen, "list_models", lambda timeout=15.0: ["some-other-model"])
    monkeypatch.setattr(config, "IMAGE_GEN_MODEL", "gemini-missing-image", raising=False)
    image_gen._STATUS.clear()
    monkeypatch.setattr(image_gen.requests, "post", lambda *a, **k: pytest.fail("no request may be made"))
    assert image_gen.generate_image("draw") is None
    image_gen._STATUS.clear()
    monkeypatch.setattr(image_gen, "list_models", lambda timeout=15.0: None)   # no network
    assert image_gen.generate_image("draw") is None
    image_gen._STATUS.clear()


# ------------------------------------------------------------------ A4. quarantine fails closed
def test_quarantine_rejects_when_vision_is_unavailable_or_raises_or_ocr_is_missing(tmp_path, monkeypatch):
    img = tmp_path / "g.jpg"
    _engraving((1080, 1920)).save(img)
    _fake_ocr(monkeypatch)
    from src import free_llm
    monkeypatch.setattr(free_llm, "ready", lambda: False)
    ok, why = image_quarantine.verify_gap_asset(img, "line", "classical_antiquity")
    assert not ok and "not available" in why
    monkeypatch.setattr(free_llm, "ready", lambda: True)

    def raises(*a, **k):
        raise RuntimeError("quota")
    monkeypatch.setattr(free_llm, "structured", raises)
    assert not image_quarantine.verify_gap_asset(img, "line", "classical_antiquity")[0]
    # the schema the prompt names ('passed') is the one read
    monkeypatch.setattr(free_llm, "structured", lambda s, p, schema, **k: schema(passed=True))
    assert image_quarantine.verify_gap_asset(img, "line", "classical_antiquity") == (True, "pass")
    monkeypatch.setattr(free_llm, "structured", lambda s, p, schema, **k: schema(passed=False, reason="a cannon"))
    assert image_quarantine.verify_gap_asset(img, "line", "classical_antiquity") == (False, "a cannon")
    _no_ocr(monkeypatch)
    monkeypatch.setattr(free_llm, "structured", lambda s, p, schema, **k: schema(passed=True))
    ok, why = image_quarantine.verify_gap_asset(img, "line", "classical_antiquity")
    assert not ok and "OCR" in why
    _fake_ocr(monkeypatch, "SPQR Roma")
    assert not image_quarantine.verify_gap_asset(img, "line", "classical_antiquity")[0]


# ------------------------------------------------------------------ A5/B7/B9. the planner
@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(beat_images, "FIT_CACHE_FILE", tmp_path / "cache" / "picture_fit.json")
    monkeypatch.setattr(config, "PICTURE_VISION_CHECK", True)
    monkeypatch.setattr(config, "GENERATIVE_GAP_FILLING", True, raising=False)
    monkeypatch.setattr(config, "PICTURE_CASCADE", True, raising=False)
    monkeypatch.setattr(config, "MAX_GAP_IMAGES_PER_RUN", 2, raising=False)
    scene_planner._folder_credits.cache_clear()

    def look(path, query, size=512, ask=None):
        bad = "unseen" in query.lower() or "bronze" in query.lower() or "marble" in query.lower()
        return {"checked": True, "shows": not bad, "burned_in_text": False, "reason": "not that" if bad else "shows it"}
    monkeypatch.setattr(beat_images, "picture_look", look)
    monkeypatch.setattr(scene_planner, "_fact_sheet_for", lambda topic, payload: SHEET)
    made = []

    def fake_illustrate(line, spec, work_dir, aspect="9:16", budget=None, others=(), **k):
        p = Path(work_dir) / f"gap_gen_{image_gen.cache_key(line, spec['era'], aspect)}.jpg"
        p.parent.mkdir(parents=True, exist_ok=True)
        _engraving((54, 96)).save(p)
        made.append(line)
        return p, {"model": "fake-model", "calls": 1}
    monkeypatch.setattr(image_gen, "illustrate_beat", fake_illustrate)
    yield types.SimpleNamespace(tmp=tmp_path, made=made)
    scene_planner._folder_credits.cache_clear()


def _photos(folder: Path, n: int):
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for i in range(n):
        p = folder / f"p{i}.jpg"
        Image.new("RGB", (48, 48), (20 * i % 255, 90, 40)).save(p)
        out.append(p)
    return out


def _beats(lines, seconds=3.0):
    return [{"start": seconds * i, "end": seconds * (i + 1), "text": t} for i, t in enumerate(lines)]


def _record(run: Path, verdicts):
    run.mkdir(parents=True, exist_ok=True)
    (run / pl.RECORD_FILE).write_text(json.dumps({"verdicts": verdicts}), encoding="utf-8")


FILL = [f"The harbour wall stood firm in year {i}." for i in range(20)]


def test_a_generated_beat_is_unshown_and_never_held_as_exact(world):
    run = world.tmp / "run"
    lines = ["The aqueduct strides across the valley."] + [GEARS, "An unseen chamber lay below."] + FILL[:9]
    _record(run, [{"sentence": GEARS, "kind": "unshown", "visual": pc.KIND_OF_THING},
                  {"sentence": "An unseen chamber lay below.", "kind": "unshown", "visual": pc.KIND_OF_THING}])
    photos = _photos(world.tmp / "x", len(lines))
    out, report = scene_planner.fit_to_lines(_beats(lines), photos, photos, "Antikythera", work_dir=run)
    assert image_gen.Path(out[1]).name.startswith("gap_gen_") and report["generated"][0]["beat"] == 2
    assert 2 not in report["fits"] and any(u["beat"] == 2 and u["fallback"] == "generated_art" for u in report["unshown"])
    assert out[2] != out[1]                                   # the illustration is never held over the next line
    assert {"beat": 2, "tier": pc.RECONSTRUCTION, "to": out[1].name, "cascade": True} in report["tiers"]
    assert "labelled illustration" in scene_planner.fit_note(report)


def test_abstract_never_generates_and_named_subject_without_evidence_does_not(world, monkeypatch):
    run = world.tmp / "run"
    q = "How many bronze gears were there?"
    named = "The unseen Pharos of Alexandria rose over the harbour."
    lines = ["The aqueduct strides across the valley.", q, FILL[0], named] + FILL[1:9]
    _record(run, [{"sentence": q, "kind": "unshown", "visual": pc.ABSTRACT},
                  {"sentence": named, "kind": "unshown", "visual": pc.NAMED_SUBJECT}])
    related = []
    monkeypatch.setattr(scene_planner, "related_shot", lambda *a, **k: related.append(a[0]) or None)
    photos = _photos(world.tmp / "x", len(lines))
    _, report = scene_planner.fit_to_lines(_beats(lines), photos, photos, "Alexandria", work_dir=run)
    assert world.made == [] and not report["generated"]
    assert named not in related                               # no same-kind photo under a named subject
    skipped = {r["beat"]: r["why"] for r in report.get("illustration_skipped", [])}
    assert "no fact-sheet fact" in skipped.get(4, "")


def test_named_subject_with_descriptors_is_illustrated_before_the_map(world, monkeypatch):
    # Rewritten (follow-up review, 2026-11-02; this test is not on origin/main): it expected an illustration of a
    # named subject with NO reference, passing only because the fixture's fake illustrate_beat ignores the rule.
    # Under the rule a named subject is drawn only from a reference: without one nothing is drawn; with one (here
    # a reconstruction line) it is illustrated before the map, and the reference reaches illustrate_beat.
    fake, got = image_gen.illustrate_beat, []

    def ruled(line, spec, work_dir, aspect="9:16", budget=None, others=(), references=(), visual=None, reason="", **k):
        got.append({"refs": list(references), "visual": visual, "reason": reason})
        if visual == pc.NAMED_SUBJECT and (not references or reason not in image_gen.ILLUSTRATION_REASONS):
            return None, {"reason": "a named subject is drawn only from a reference image of it", "calls": 0}
        return fake(line, spec, work_dir, aspect, budget, others)
    monkeypatch.setattr(image_gen, "illustrate_beat", ruled)
    lines = ["The aqueduct strides across the valley.", GEARS] + FILL[:10]
    photos = _photos(world.tmp / "x", len(lines))

    run = world.tmp / "run"
    _record(run, [{"sentence": GEARS, "kind": "unshown", "visual": pc.NAMED_SUBJECT}])
    monkeypatch.setattr(image_gen, "find_references", lambda *a, **k: ([], "no reference"))
    out, _ = scene_planner.fit_to_lines(_beats(lines), photos, photos, "Antikythera", work_dir=run)
    assert not out[1].name.startswith("gap_gen_") and world.made == []

    ref = world.tmp / "refs" / "fragment.jpg"
    ref.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (300, 300), (150, 120, 60)).save(ref)
    one = {"path": str(ref), "title": "Antikythera mechanism fragment", "licence": "CC0", "sha256": "abc"}
    monkeypatch.setattr(image_gen, "find_references", lambda *a, **k: ([one], ""))
    run2 = world.tmp / "run2"
    _record(run2, [{"sentence": GEARS, "kind": "unshown", "visual": pc.NAMED_SUBJECT}])
    out, report = scene_planner.fit_to_lines(_beats(lines), photos, photos, "Antikythera", work_dir=run2)
    assert out[1].name.startswith("gap_gen_") and world.made == [GEARS]
    assert got[-1]["refs"] == [one] and got[-1]["reason"] == image_gen.RECONSTRUCTION_REASON
    assert not any(t["beat"] == 2 and t["tier"] == pc.MAP for t in report["tiers"])


def test_no_generation_once_the_checks_stopped(world):
    run = world.tmp / "run"
    lines = ["The aqueduct strides across the valley.", FILL[0], GEARS] + FILL[1:9]
    _record(run, [{"sentence": GEARS, "kind": "unshown", "visual": pc.KIND_OF_THING}])
    photos = _photos(world.tmp / "x", len(lines))
    reserve = _photos(world.tmp / "r", 1)
    _, report = scene_planner.fit_to_lines(_beats(lines), photos, photos, "Antikythera", work_dir=run,
                                           max_calls=2, reserve=reserve)
    assert report["stopped"] and world.made == [] and not report["generated"]
    assert scene_planner.illustration_allowed(3, photos, [3.0] * 12, {"enabled": True, "cap": 2}, stopped="budget")


def test_caps_never_two_consecutive_short_shots_runtime_share_and_protected_first(tmp_path):
    out = [tmp_path / f"p{i}.jpg" for i in range(10)]
    ctx = {"enabled": True, "cap": 2, "long_form": False, "total": 30.0, "protected": [False] * 10}
    durs = [3.0] * 10
    assert scene_planner.illustration_allowed(3, out, durs, ctx) == ""
    assert "hook" in scene_planner.illustration_allowed(0, out, durs, ctx)
    out[4] = tmp_path / "gap_gen_a.jpg"
    assert "next or previous" in scene_planner.illustration_allowed(3, out, durs, ctx)
    assert "next or previous" in scene_planner.illustration_allowed(5, out, durs, ctx)
    assert "too short" in scene_planner.illustration_allowed(7, out, durs[:7] + [1.2] + durs[8:], ctx)
    assert "runtime" in scene_planner.illustration_allowed(7, out, durs, ctx)   # 6 s of 30 s > 15 %
    long_ctx = dict(ctx, total=300.0)
    assert scene_planner.illustration_allowed(7, out, durs, long_ctx) == ""
    out[1] = tmp_path / "gap_gen_b.jpg"
    assert "cap" in scene_planner.illustration_allowed(7, out, durs, long_ctx)
    # a protected line later on holds its slot
    fresh = [tmp_path / f"p{i}.jpg" for i in range(10)]
    prot = dict(long_ctx, cap=1, protected=[False] * 8 + [True, False])
    assert "protected" in scene_planner.illustration_allowed(3, fresh, durs, prot)
    assert scene_planner.illustration_allowed(8, fresh, durs, prot) == ""


def test_rejected_candidates_count_against_a_per_run_cap_across_replans(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "IMAGE_GEN_MAX_CALLS_PER_RUN", 6, raising=False)
    monkeypatch.setattr(image_gen, "is_available", lambda: True)
    monkeypatch.setattr(image_gen, "checkers_ready", lambda: (True, ""))
    calls = []
    monkeypatch.setattr(image_gen, "generate_image", lambda p, aspect_ratio="9:16", timeout=None:
                        calls.append(p) or _png(_engraving((1080, 1920))))
    monkeypatch.setattr(image_quarantine, "score_candidate", lambda *a, **k: (False, ["not shown: gears"], 0.0))
    spec, _ = image_gen.spec_from_facts(GEARS, {}, SHEET)
    for _ in range(3):   # three plans of one run
        assert image_gen.illustrate_beat(GEARS, spec, tmp_path, budget=image_gen.Budget(tmp_path))[0] is None
    assert len(calls) == 6 and image_gen.Budget(tmp_path).used() == 6


def test_the_best_passing_candidate_is_kept_and_a_line_change_invalidates_the_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(image_gen, "is_available", lambda: True)
    monkeypatch.setattr(image_gen, "checkers_ready", lambda: (True, ""))
    monkeypatch.setattr(image_gen, "generate_image", lambda p, aspect_ratio="9:16", timeout=None: _png(_engraving((1080, 1920))))
    scores = iter([(False, ["text"], 0.0), (True, [], 5.0), (True, [], 9.0), (True, [], 1.0)])
    monkeypatch.setattr(image_quarantine, "score_candidate", lambda *a, **k: next(scores))
    spec, _ = image_gen.spec_from_facts(GEARS, {}, SHEET)
    art, info = image_gen.illustrate_beat(GEARS, spec, tmp_path)
    assert art is not None and info["calls"] == 4 and len(info["rejected"]) == 1
    best = tmp_path / image_gen.CANDIDATE_DIR / f"{info['key']}_2_final.png"
    assert art.read_bytes() == best.read_bytes()
    rec = json.loads(lineage.sidecar(art).read_text(encoding="utf-8"))["settings"]["inputs"]
    assert rec["line"] == GEARS and rec["descriptors"] == spec["required"] and rec["origin"] == "ai_illustration"
    # reused only after it passes the rubric again
    seen = []
    monkeypatch.setattr(image_quarantine, "score_candidate", lambda *a, **k: seen.append(a) or (True, [], 1.0))
    again, info2 = image_gen.illustrate_beat(GEARS, spec, tmp_path)
    assert again == art and info2["reused"] and info2["calls"] == 0 and len(seen) == 1
    other, _ = image_gen.illustrate_beat("The bronze gears of the mechanism turned slowly.", spec, tmp_path)
    assert other != art


def test_never_on_a_cover_and_not_when_checkers_cannot_run(tmp_path, monkeypatch):
    monkeypatch.setattr(image_gen, "is_available", lambda: True)
    monkeypatch.setattr(image_gen, "generate_image", lambda *a, **k: pytest.fail("no request"))
    spec, _ = image_gen.spec_from_facts(GEARS, {}, SHEET)
    assert image_gen.illustrate_beat(GEARS, spec, tmp_path, surface="cover")[0] is None
    _no_ocr(monkeypatch)
    monkeypatch.setattr(image_quarantine, "_vision_ready", lambda: True)
    art, info = image_gen.illustrate_beat(GEARS, spec, tmp_path)
    assert art is None and "OCR" in info["reason"]


# ------------------------------------------------------------------ A6. badge timing
def test_the_badge_covers_the_crossfades(tmp_path):
    scenes = [tmp_path / "a.jpg", tmp_path / "gap_gen_x.jpg", tmp_path / "b.jpg", tmp_path / "gap_gen_y.jpg"]
    got = reconstruction_badge_intervals(scenes, [3.0, 4.0, 2.0, 3.0], 12.0)
    assert got == [(3.0 - TRANS_DUR / 2, 7.0 + TRANS_DUR / 2), (9.0 - TRANS_DUR / 2, 12.0 + TRANS_DUR / 2)]
    assert reconstruction_badge_intervals([tmp_path / "gap_gen_z.jpg"], [2.0], 2.0)[0][0] == 0.0
    assert RECON_BADGE_LABEL == "AI ILLUSTRATION · NOT A PHOTO"   # item 4 (2026-11-03): was "[ HISTORICAL RECONSTRUCTION ]"


# ------------------------------------------------------------------ B8. protected lines
HOOK = "The Antikythera mechanism has 30 bronze gears."
CLOSE = "Nobody built anything like it for 1,400 years."
MID = "The case was made of wood."
STAT = "Its largest wheel has 223 teeth."


def _payload(**kw):
    p = {"narration_script": f"{HOOK} {MID} {STAT} {CLOSE}", "hook_sentence": HOOK, "closing_sentence": CLOSE,
         "title": "The 2,000-year-old computer", "stat_cards": [{"value": "223", "label": "teeth on one wheel"}],
         "thumbnail": {"line1": "30 GEARS", "line2": ""}, "story_slides": [{}, {"line1": ""}],
         "sentence_trace": [{"sentence": s, "fact_ids": ["F1"]} for s in (HOOK, MID, STAT, CLOSE)]}
    p.update(kw)
    return p


def test_protected_sentences_are_the_hook_the_close_and_packaging_figures():
    assert pl.protected_sentences(_payload()) == [HOOK, STAT, CLOSE]


def test_a_protected_line_with_sourced_evidence_is_not_rewritten(monkeypatch):
    from src import fact_evidence
    monkeypatch.setattr(fact_evidence, "current_fact_review", lambda payload: True)
    verdicts = [{"sentence": s, "kind": "unshown", "visual": pc.NAMED_SUBJECT} for s in (HOOK, MID, STAT, CLOSE)]
    out = pl.needs_rewrite(verdicts, _payload(), SHEET)
    assert [v["sentence"] for v in out] == [MID]
    assert all(v.get("route") == "cascade" and v["protected"] for v in verdicts if v["sentence"] != MID)
    # without a current, sourced fact review the protected lines go to the writer as today
    monkeypatch.setattr(fact_evidence, "current_fact_review", lambda payload: False)
    fresh = [{"sentence": s, "kind": "unshown", "visual": pc.NAMED_SUBJECT} for s in (HOOK, MID, STAT, CLOSE)]
    assert len(pl.needs_rewrite(fresh, _payload(), SHEET)) == 4


def test_generable_named_subject_without_evidence_goes_to_the_writer():
    v = [{"sentence": "The Pharos lighthouse rose over the harbour.", "kind": "generable", "visual": pc.NAMED_SUBJECT},
         {"sentence": GEARS, "kind": "generable", "visual": pc.NAMED_SUBJECT}]
    payload = {"narration_script": f"{v[0]['sentence']} {v[1]['sentence']}"}
    assert [x["sentence"] for x in pl.needs_rewrite(v, payload, SHEET)] == ["The Pharos lighthouse rose over the harbour."]
    assert pl.needs_rewrite(v) == []   # without the payload, as before


def test_a_protected_rewording_that_loses_a_number_or_name_is_refused_and_the_brief_differs():
    assert pl.protected_rewrite_problem(HOOK, "The device has many bronze gears.", [HOOK])
    assert pl.protected_rewrite_problem(HOOK, "The mechanism has 30 bronze gears.", [HOOK])   # lost "Antikythera"
    assert pl.protected_rewrite_problem(HOOK, "Antikythera's device: 30 bronze gears.", [HOOK]) == ""
    note = pl.revision_note([{"sentence": HOOK, "protected": True}, {"sentence": MID}], [{"text": "a wooden box"}])
    assert "Do not reword them toward what the photos show" in note and "keep every number and every name" in note
    assert note.index(MID) < note.index("toward what the photos do show")
    assert HOOK not in note.split("These protected sentences")[0]


# ------------------------------------------------------------------ B9. never generated
@pytest.mark.parametrize("line", [
    "Emperor Hadrian walked through the dome.", "The skeletons lay in rows.", "An inscription ran around the rim.",
    "Gold coins were scattered on the deck.", "A map of the routes survives.", "Divers found the mechanism in 1901.",
    "Some believe giants built the walls.", "Under the microscope, nanoparticles glow.",
    "An X-ray scan reveals hidden teeth."])
def test_never_generated(line):
    assert image_gen.never_generate_reason(line)


def test_lab_lines_get_no_spec_and_contested_facts_get_none():
    assert image_gen.spec_from_facts("Under the microscope the bronze gears show wear.", {}, SHEET)[0] is None
    disputed = {"facts": [dict(SHEET["facts"][0], disputed=True)]}
    assert image_gen.spec_from_facts(GEARS, {}, disputed)[0] is None


# ------------------------------------------------------------------ C10/11. house style and prompt
def test_the_prompt_is_house_style_from_the_fact_sheet_with_no_named_artist():
    spec, _ = image_gen.spec_from_facts(GEARS, {"title": "Antikythera"}, SHEET)
    prompt = image_gen.build_prompt(spec)
    for banned in ("Piranesi", "Dürer", "accuracy", "accurate"):
        assert banned not in prompt
    assert "scratchboard" in prompt and "near-black ink ground" in prompt and "burin" in prompt
    assert "34 cm" in prompt and "exploded axonometric" in prompt and "middle third" in prompt
    assert "Do not include any text" in prompt and "turned inside" not in prompt   # fact-sheet words, not narration
    assert spec["required"] == ["bronze", "gears", "mechanism"] and spec["era"] == "classical_antiquity"


def test_the_era_is_derived_from_the_dates_when_the_topic_has_none():
    assert image_gen.derive_era({}, {"facts": [{"statement": "Built in 1889 for the fair."}]}) == "industrial_victorian"
    assert image_gen.derive_era({}, {"facts": [{"statement": "Carved in the 12th century."}]}) == "medieval_renaissance"
    assert image_gen.derive_era({"era": "modern_cold_war"}, SHEET) == "modern_cold_war"
    assert image_gen.derive_era({}, SHEET) == "classical_antiquity"


def test_the_engraving_is_bone_on_ink_never_coloured_and_never_stretched(tmp_path):
    raw = _engraving((1200, 2133), colour=(40, 0, 60))
    ImageDraw.Draw(raw).rectangle([100, 100, 300, 300], fill=(200, 30, 30))
    out = sc.composite_engraving_on_ink(raw, tmp_path / "e.jpg", (1080, 1920))
    colours = {tuple(c) for c in np.asarray(out).reshape(-1, 3)}
    assert len(colours) <= 3 and BONE in colours and INK2 in colours
    # a 16:9 frame into 9:16 is cropped, never stretched: a centred square stays a square
    wide = Image.new("RGB", (1920, 1080), (0, 0, 0))
    ImageDraw.Draw(wide).rectangle([860, 440, 1060, 640], fill=(255, 255, 255))
    fitted = np.asarray(sc.fit_crop(wide, (540, 960)).convert("L")) > 128
    ys, xs = np.nonzero(fitted)
    assert abs((xs.max() - xs.min()) - (ys.max() - ys.min())) <= 2


# ------------------------------------------------------------------ C12. the rubric
def test_the_raw_rubric(tmp_path):
    assert image_quarantine.raw_rubric(_engraving((1080, 1920)), (1080, 1920))[0]
    light = Image.new("RGB", (1080, 1920), (240, 240, 240))
    ok, why = image_quarantine.raw_rubric(light, (1080, 1920))
    assert not ok and any("dark ground" in w for w in why)
    ok, why = image_quarantine.raw_rubric(_engraving((1080, 1920), colour=(0, 0, 120)), (1080, 1920))
    assert not ok and any("coloured" in w for w in why)
    ok, why = image_quarantine.raw_rubric(_engraving((540, 960)), (1080, 1920))
    assert not ok and any("upscale" in w for w in why)
    ok, why = image_quarantine.raw_rubric(_engraving((1080, 1920), ink=0.9), (1080, 1920))
    assert not ok


def test_the_full_rubric_fails_closed_and_rejects_near_duplicates(tmp_path, monkeypatch):
    raw, final = tmp_path / "r.png", tmp_path / "f.jpg"
    _engraving((1080, 1920)).save(raw)
    sc.composite_engraving_on_ink(raw, final, (1080, 1920))
    spec = {"subject": "gears", "required": ["bronze", "gears"], "era": "classical_antiquity"}
    _fake_ocr(monkeypatch)
    monkeypatch.setattr(image_quarantine, "_vision_ready", lambda: False)
    assert not image_quarantine.score_candidate(raw, final, spec, (1080, 1920))[0]
    monkeypatch.setattr(image_quarantine, "_vision_ready", lambda: True)
    from src import free_llm

    def verdict(**kw):
        base = {"descriptors": [{"descriptor": "bronze", "shown": True}, {"descriptor": "gears", "shown": True}],
                "has_text": False, "anachronism": False, "identifiable_face": False, "subject_in_safe_zone": True,
                "could_be_mistaken_for_photograph": False}
        base.update(kw)
        return lambda s, p, schema, **k: schema(**base)
    monkeypatch.setattr(free_llm, "structured", verdict())
    assert image_quarantine.score_candidate(raw, final, spec, (1080, 1920))[0]
    monkeypatch.setattr(free_llm, "structured", verdict(could_be_mistaken_for_photograph=True))
    assert not image_quarantine.score_candidate(raw, final, spec, (1080, 1920))[0]
    monkeypatch.setattr(free_llm, "structured", verdict(descriptors=[{"descriptor": "bronze", "shown": True}]))
    ok, why, _ = image_quarantine.score_candidate(raw, final, spec, (1080, 1920))
    assert not ok and "gears" in why[0]
    monkeypatch.setattr(free_llm, "structured", verdict())
    ok, why, _ = image_quarantine.score_candidate(raw, final, spec, (1080, 1920), others=[final])
    assert not ok and "pHash" in why[0]
    _fake_ocr(monkeypatch, "Bronze gears of Rhodes")
    assert not image_quarantine.score_candidate(raw, final, spec, (1080, 1920))[0]


# ------------------------------------------------------------------ C14. credits and the description
def test_credits_name_the_model_and_what_it_was_drawn_from(tmp_path):
    art = tmp_path / "gap_gen_abc.jpg"
    _engraving((54, 96)).save(art)
    lineage.record_graphic(art, Path(image_gen.__file__), {"model": "fake-image-model", "descriptors": ["bronze", "gears"],
                                                           "fact_ids": ["F1"]})
    master = tmp_path / "master.mp4"
    master.write_bytes(b"mp4")
    ledger = json.loads(credits.generate_provenance_ledger(run_dir=tmp_path, topic={"id": "t", "title": "T"},
                                                           payload={"caption": "c"}, master_mp4=master,
                                                           media_items=[art]).read_text(encoding="utf-8"))
    rec = next(a for a in ledger["assets"] if a["file"] == art.name)
    assert rec["illustration"] == {"origin": "ai_illustration", "model": "fake-image-model",
                                   "based_on": "based on: bronze, gears", "fact_ids": ["F1"]}
    assert "In-House" not in str(rec["artist"]) and "Proprietary" not in str(rec["license"])
    assert rec["artist"] == "AI illustration (fake-image-model), based on: bronze, gears"


def test_the_description_discloses_illustrations_only_when_used(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0" * 10)
    base = {"topic_id": "t", "title": "T", "caption": "A caption.", "description": "Long description."}
    mp = publisher.MetaPublisher(db=types.SimpleNamespace(), publishers=[])
    plain = mp.build_envelope("r", video, video, video, dict(base))["metadata"]
    assert plain["caption"] == "A caption." and publisher.ILLUSTRATION_DISCLOSURE not in plain["description"]
    used = mp.build_envelope("r", video, video, video, dict(base, picture_fit={"generated": [{"beat": 2}]}))["metadata"]
    assert used["caption"].endswith(publisher.ILLUSTRATION_DISCLOSURE)
    assert used["description"].endswith(publisher.ILLUSTRATION_DISCLOSURE)
    assert used["is_ai_generated"] == config.META_IS_AI_GENERATED
