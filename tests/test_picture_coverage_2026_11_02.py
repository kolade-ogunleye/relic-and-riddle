"""Every narrated line has a planned, honest visual (2026-11-02).

The per-line visual plan before the voice (bounded rewrite rounds), the fallback cascade under a line no photo shows
(a detail crop, archive film, the map), the per-beat coverage record, the video check's warning path, the topic image
gate and the further open-access sources. No model and no network: looks, judges and HTTP answers are fakes.
"""
import json
from pathlib import Path

import pytest
import test_prevoice_lines_2026_10_28 as prevoice_suite
from PIL import Image
from test_prevoice_lines_2026_10_28 import (  # noqa: F401
    CLOSE,
    HARBOUR,
    MODERN,
    QUESTION,
    REWORDED,
    Judge,
)

# The prevoice world (photos, judge, writer path) from the existing suite; its fixture is reused as it is.
from test_prevoice_lines_2026_10_28 import HOOK as R_HOOK
from test_prevoice_lines_2026_10_28 import PANTHEON as R_PANTHEON

import main
from src import beat_images, config, scene_planner, topic_engine, video_qa
from src import museum_sources as ms
from src import picture_coverage as pc
from src import prevoice_lines as pl


@pytest.fixture
def pw(tmp_path, monkeypatch):
    """The existing suite's prevoice world (test_prevoice_lines_2026_10_28.world), built as it builds it."""
    return prevoice_suite.world.__wrapped__(tmp_path, monkeypatch)


TOWERS = "Modern steel towers rust away within a single lifetime."
TOWERS_REWORDED = "The harbour piers outlasted the iron cramps that once held their facing stones."


# ------------------------------------------------------------------ 1. the per-line visual plan
def test_plan_visual_defaults_follow_the_kind_and_keep_the_models_class():
    assert pl.plan_visual("abstract", None) == pc.ABSTRACT
    assert pl.plan_visual("generable", None) == pc.KIND_OF_THING
    assert pl.plan_visual("unshown", None) == pc.NAMED_SUBJECT and pl.plan_visual("shown", None) == pc.NAMED_SUBJECT
    assert pl.plan_visual("unshown", "kind_of_thing") == pc.KIND_OF_THING


def test_only_unshown_named_subject_lines_need_the_writer():
    verdicts = [{"sentence": "a", "kind": "unshown", "visual": pc.NAMED_SUBJECT},
                {"sentence": "b", "kind": "unshown", "visual": pc.KIND_OF_THING},
                {"sentence": "c", "kind": "unshown"},                       # no class recorded: as before, sent back
                {"sentence": "d", "kind": "abstract", "visual": pc.ABSTRACT},
                {"sentence": "e", "kind": "shown", "visual": pc.NAMED_SUBJECT}]
    assert [v["sentence"] for v in pl.needs_rewrite(verdicts)] == ["a", "c"]
    assert [v["sentence"] for v in pl.unshown(verdicts)] == ["a", "b", "c"]   # the existing helper is unchanged


def test_rounds_default_to_two_and_are_bounded(monkeypatch):
    assert config.PREVOICE_REWRITE_ROUNDS == 2 and pl.rounds() == 2
    monkeypatch.setattr(config, "PREVOICE_REWRITE_ROUNDS", 0)
    assert pl.rounds() == 1


def test_judge_records_each_lines_planned_visual(pw):
    out = pw.check({"narration_script": f"{R_HOOK} {R_PANTHEON} {QUESTION} {CLOSE}", "guardrail_passed": True})
    record = json.loads((pw.run_dir / pl.RECORD_FILE).read_text(encoding="utf-8"))
    assert {v["sentence"]: v["visual"] for v in record["verdicts"]}[QUESTION] == pc.ABSTRACT
    assert {v["sentence"]: v["visual"] for v in record["verdicts"]}[R_PANTHEON] == pc.NAMED_SUBJECT
    assert out[pl.STAGE]["unshown"] == []


def _two_line_writer(pw):
    def revise(topic, run_id, run_dir, dry_run, initial_payload=None, initial_notes=""):
        pw.revisions.append({"payload": initial_payload, "notes": initial_notes})
        text = initial_payload["narration_script"]
        for old, new in ((MODERN, REWORDED), (TOWERS, TOWERS_REWORDED)):
            if old in text:      # one line a round: the writer fixes the first it can
                return dict(initial_payload, narration_script=text.replace(old, new), guardrail_passed=True)
        return dict(initial_payload, guardrail_passed=True)
    pw.coord._write_and_check_script = revise


def test_a_second_round_rewords_what_the_first_left(pw):
    _two_line_writer(pw)
    script = f"{R_HOOK} {R_PANTHEON} {MODERN} {TOWERS} {QUESTION}"
    out = pw.check({"narration_script": script, "guardrail_passed": True})
    assert len(pw.revisions) == 2                                      # two rounds, both through the writer
    assert TOWERS in pw.revisions[1]["notes"] and MODERN not in pw.revisions[1]["notes"]  # only what is left
    assert REWORDED in pw.revisions[1]["payload"]["narration_script"]   # round 2 starts from round 1's rewording
    assert REWORDED in out["narration_script"] and TOWERS_REWORDED in out["narration_script"]
    assert out[pl.STAGE]["rewritten"] == [MODERN, TOWERS] and out[pl.STAGE]["left"] == []


def test_one_round_when_the_operator_sets_one(pw, monkeypatch):
    monkeypatch.setattr(config, "PREVOICE_REWRITE_ROUNDS", 1)
    _two_line_writer(pw)
    script = f"{R_HOOK} {R_PANTHEON} {MODERN} {TOWERS} {QUESTION}"
    out = pw.check({"narration_script": script, "guardrail_passed": True})
    assert len(pw.revisions) == 1 and out["narration_script"] == script   # the verified script stands
    assert out[pl.STAGE]["left"] == [MODERN, TOWERS]


def test_an_unshown_kind_of_thing_line_is_left_to_the_cascade(pw, monkeypatch):
    base = Judge()

    def judge(system, prompt, schema, **kw):
        report = base(system, prompt, schema, **kw)
        for v in report.lines:
            if v.kind == "unshown":
                v.visual = "kind_of_thing"     # "modern bridges": any photo of the kind is honest
        return report
    monkeypatch.setattr("src.free_llm.call", judge)
    out = pw.check({"narration_script": f"{R_HOOK} {R_PANTHEON} {MODERN} {QUESTION}", "guardrail_passed": True})
    assert pw.revisions == [] and out[pl.STAGE]["unshown"] == [MODERN] and out[pl.STAGE]["left"] == []


def test_the_check_that_cannot_run_still_never_blocks(pw):
    pw.judge.fail = True
    payload = {"narration_script": f"{R_HOOK} {MODERN}", "guardrail_passed": True}
    assert pw.check(payload) is payload and pw.revisions == []


# ------------------------------------------------------------------ 2. the fallback cascade
HOOK = "The great aqueduct strides across the valley on three tiers of arches."
PANTHEON = "Dedicated in AD 128, Rome's Pantheon preserves the largest unreinforced dome."
LIME = "Builders mixed quicklime into the hot mortar."
MODERN_LINE = "Today, bridges crack within decades across modern highways."


@pytest.fixture(autouse=True)
def fit_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(beat_images, "FIT_CACHE_FILE", tmp_path / "cache" / "picture_fit.json")
    monkeypatch.setattr(config, "PICTURE_VISION_CHECK", True)
    monkeypatch.setattr(config, "GENERATIVE_GAP_FILLING", False, raising=False)
    scene_planner._folder_credits.cache_clear()
    yield
    scene_planner._folder_credits.cache_clear()


def _photo(folder: Path, name: str, size=(48, 48), noise=False) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    im = Image.effect_noise(size, 80).convert("RGB") if noise else Image.new("RGB", size, (90, 90, 40))
    im.save(p, "JPEG")
    return p


def _approved(folder: Path, *names: str) -> None:
    images = [{"file": n, "title": f"File:{n}", "license": "CC0", "source_url": f"https://example.org/{n}",
               "vision_review": {"shows_topic": "yes", "usable": True, "safe": True, "reason": "the stone arches"}}
              for n in names]
    (folder / "credits.json").write_text(json.dumps({"reviewed": True, "images": images}), encoding="utf-8")
    scene_planner._folder_credits.cache_clear()


def _judge(monkeypatch, wrong=lambda name, line: "modern" in line.lower()):
    def look(path, query, size=512, ask=None):
        bad = wrong(Path(path).name, query)
        return {"checked": True, "shows": not bad, "burned_in_text": False, "reason": "not that" if bad else "shows it"}
    monkeypatch.setattr(beat_images, "picture_look", look)


def _beats(*lines):
    return [{"start": 3.0 * i, "end": 3.0 * (i + 1), "text": t} for i, t in enumerate(lines)]


def test_a_detail_crop_of_a_strong_approved_photo_fills_the_beat_before_a_held_shot(tmp_path, monkeypatch):
    _judge(monkeypatch)
    topic = tmp_path / "topic"
    hook = _photo(topic, "01_commons.jpg", (3000, 2400), noise=True)
    _approved(topic, "01_commons.jpg")
    pantheon, lime = _photo(tmp_path / "x", "pantheon.jpg"), _photo(tmp_path / "x", "lime.jpg")
    run = tmp_path / "run"
    beats = _beats(HOOK, PANTHEON, LIME, MODERN_LINE)
    out, report = scene_planner.fit_to_lines(beats, [hook, pantheon, lime, pantheon], [hook, pantheon, lime],
                                             "Roman concrete", work_dir=run)
    crop = out[3]
    assert crop.parent.parent.name == pc.DETAIL_DIR and crop.name == hook.name    # its credit is found by name
    assert pc.is_detail_crop(crop) and scene_planner.picture_id(crop) == scene_planner.picture_id(hook)
    assert report["tiers"] == [{"beat": 4, "tier": pc.DETAIL_CROP, "to": crop.name, "cascade": True}]
    assert report["held"] == [] and report["unshown"][0]["fallback"] == pc.DETAIL_CROP
    with Image.open(crop) as im:
        assert im.width >= config.DETAIL_CROP_MIN_WIDTH and abs(im.width / im.height - 1080 / 1920) < 0.02
    assert "a detail of one of the topic's photos" in scene_planner.fit_note(report)
    record = pc.write(run, beats, out, report)
    tiers = {r["beat"]: (r["tier"], r["cascade"]) for r in record["beats"]}
    assert tiers == {1: (pc.UNJUDGED, False), 2: (pc.EXACT, False), 3: (pc.EXACT, False), 4: (pc.DETAIL_CROP, True)}
    assert pc.load(run)["summary"] == {pc.EXACT: 2, pc.DETAIL_CROP: 1, pc.UNJUDGED: 1}
    assert pc.cascade_beats(pc.load(run)) == {4: pc.DETAIL_CROP}


def test_two_crops_of_one_photo_show_distinct_regions(tmp_path):
    photo = _photo(tmp_path, "01.jpg", (3200, 2400), noise=True)
    first = pc.detail_crop(photo, tmp_path / "run", [], 1080 / 1920, min_width=600)
    second = pc.detail_crop(photo, tmp_path / "run", [first[1]], 1080 / 1920, min_width=600)
    assert first and second and first[0] != second[0]
    assert pc.iou(first[1], second[1]) <= pc.DETAIL_MAX_IOU


def test_a_photo_too_small_for_a_punch_in_is_not_cropped(tmp_path):
    photo = _photo(tmp_path, "small.jpg", (640, 480))
    assert pc.detail_crop(photo, tmp_path / "run", [], 1080 / 1920, min_width=600) is None


def test_a_photo_without_an_approved_record_is_never_cropped(tmp_path, monkeypatch):
    _judge(monkeypatch)
    big = _photo(tmp_path / "supplied", "big.jpg", (3000, 2400), noise=True)   # no credits.json: not approved
    lime = _photo(tmp_path / "x", "lime.jpg")
    out, report = scene_planner.fit_to_lines(_beats(HOOK, LIME, MODERN_LINE), [big, lime, big], [big, lime],
                                             "Roman concrete", work_dir=tmp_path / "run")
    assert not any(pc.is_detail_crop(p) for p in out) and report["tiers"] == []


def test_archive_film_whose_record_names_the_line_comes_next(tmp_path, monkeypatch):
    _judge(monkeypatch)
    hook, lime = _photo(tmp_path, "aqueduct.jpg"), _photo(tmp_path, "lime.jpg")
    clip = tmp_path / "footage_bridge.mp4"
    clip.write_bytes(b"clip")
    clip.with_suffix(".json").write_text(json.dumps({"title": "Highway bridges cracking, 1958 newsreel"}), encoding="utf-8")
    out, report = scene_planner.fit_to_lines(_beats(HOOK, LIME, MODERN_LINE), [hook, lime, hook], [hook, lime, clip],
                                             "Roman concrete", work_dir=tmp_path / "run")
    assert out[2] == clip and report["tiers"][-1]["tier"] == pc.ARCHIVE_FILM


def test_the_locator_map_covers_a_line_that_names_its_place(tmp_path, monkeypatch):
    _judge(monkeypatch, wrong=lambda name, line: "campania" in line.lower())
    hook, lime = _photo(tmp_path, "aqueduct.jpg"), _photo(tmp_path, "lime.jpg")
    locator = tmp_path / "graphic_map.mp4"
    locator.write_bytes(b"clip")
    Image.new("RGB", (48, 48)).save(locator.with_suffix(".jpg"))
    monkeypatch.setattr(scene_planner, "_is_map", lambda p: Path(p).stem == "graphic_map")
    monkeypatch.setattr(scene_planner.graphic_match, "judgeable", lambda g: False)   # not a "says the line" graphic
    monkeypatch.setattr(scene_planner.graphic_match, "score",
                        lambda g, line: 3.0 if "campania" in line.lower() else 0.0)
    line = "The volcanic ash came from the Bay of Naples in Campania."
    out, report = scene_planner.fit_to_lines(_beats(HOOK, LIME, line), [hook, lime, hook], [hook, lime, locator],
                                             "Roman concrete", work_dir=tmp_path / "run")
    assert out[2] == locator and report["tiers"][-1]["tier"] == pc.MAP


def test_the_cascade_off_keeps_the_held_shot(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PICTURE_CASCADE", False)
    _judge(monkeypatch)
    topic = tmp_path / "topic"
    hook = _photo(topic, "01_commons.jpg", (3000, 2400), noise=True)
    _approved(topic, "01_commons.jpg")
    lime = _photo(tmp_path / "x", "lime.jpg")
    out, report = scene_planner.fit_to_lines(_beats(HOOK, LIME, MODERN_LINE), [hook, lime, hook], [hook, lime],
                                             "Roman concrete", work_dir=tmp_path / "run")
    assert out[2] == lime and report["held"] and not any(pc.is_detail_crop(p) for p in out)


def test_coverage_reads_assets_when_no_row_names_them(tmp_path):
    beats = _beats("a", "b", "c", "d")
    scenes = [tmp_path / "x.jpg", tmp_path / "gap_gen_beat_2.jpg", tmp_path / "footage_a.mp4", tmp_path / "y.jpg"]
    rows = pc.coverage(beats, scenes, {"held": [{"beat": 4, "to": "x.jpg"}], "fits": [], "tiers": [
        {"beat": 3, "tier": pc.SAME_KIND, "to": "other.jpg", "cascade": True}]})   # a row for another picture: stale
    assert [r["tier"] for r in rows] == [pc.UNJUDGED, pc.RECONSTRUCTION, pc.ARCHIVE_FILM, pc.HELD]
    assert not any(r["cascade"] for r in rows)


def test_coverage_carries_each_beats_planned_visual(tmp_path):
    (tmp_path / pl.RECORD_FILE).write_text(json.dumps({"verdicts": [
        {"sentence": "How old is it?", "visual": pc.ABSTRACT},
        {"sentence": "The Pont du Gard carried water for centuries.", "visual": pc.NAMED_SUBJECT}]}), encoding="utf-8")
    beats = _beats("How old is it?", "The Pont du Gard carried water", "for centuries.", "Something else entirely.")
    assert pc.planned_visuals(tmp_path, beats) == [pc.ABSTRACT, pc.NAMED_SUBJECT, pc.NAMED_SUBJECT, None]


# ------------------------------------------------------------------ 3. the video check's severity
COVERAGE = {"beats": [{"beat": 4, "tier": pc.DETAIL_CROP, "cascade": True},
                      {"beat": 5, "tier": pc.EXACT, "cascade": False},
                      {"beat": 6, "tier": pc.HELD, "cascade": False}]}


def test_a_lower_tier_beat_that_only_misses_the_named_thing_is_a_warning():
    rows = [{"beat": 4, "severity": "major", "issue": "The image does not show the modern bridges the narration mentions."}]
    [row] = video_qa.coverage_severity(rows, COVERAGE)
    assert row["severity"] == "info" and row["warning"] and row["was"] == "major" and row["tier"] == pc.DETAIL_CROP
    assert row["issue"].startswith("covered by a lower tier (detail crop): ")


def test_a_wrong_or_invented_subject_stays_a_blocker_even_on_a_lower_tier():
    rows = [{"beat": 4, "severity": "major", "issue": "The picture contradicts the narration: it shows a different temple."},
            {"beat": 4, "severity": "critical", "issue": "A modern car is shown instead of the ancient surveyors."},
            {"beat": 4, "severity": "major", "issue": "Burned-in text and a watermark; does not show the dome."}]
    assert video_qa.coverage_severity(rows, COVERAGE) == rows


def test_an_exact_held_or_unrecorded_beat_keeps_its_severity():
    rows = [{"beat": b, "severity": "major", "issue": "The image does not show the harbour named."} for b in (5, 6, 7)]
    assert video_qa.coverage_severity(rows, COVERAGE) == rows
    assert video_qa.coverage_severity(rows, None) == rows and video_qa.coverage_severity(rows, {}) == rows


def test_a_warning_does_not_block_the_release_gate():
    # src/release.py: a finding of severity "info" is shown on the card and never blocks.
    src = Path("src/release.py").read_text(encoding="utf-8")
    assert 'i.get("severity") != "info"' in src


def test_main_grades_the_video_check_with_the_coverage_record():
    src = Path(main.__file__).read_text(encoding="utf-8")
    assert "picture_coverage.write(run_dir, beats, scenes, fit_report)" in src
    assert "coverage_severity(payload[\"video_check\"][\"issues\"]" in src


# ------------------------------------------------------------------ 4. the topic image gate
def _pool(assets: Path, tid: str, n: int, reviewed=True, dupes=0) -> None:
    folder = assets / tid
    folder.mkdir(parents=True)
    images = []
    for i in range(n + dupes):
        name = f"{i:02d}_commons.jpg"
        (folder / name).write_bytes(b"jpg")
        images.append({"file": name, "title": f"File:Photo {min(i, n - 1)}.jpg", "license": "CC0",
                       "source_url": "https://commons.wikimedia.org/x",
                       "vision_review": {"shows_topic": "yes", "usable": True, "safe": True}})
    images.append({"file": "weak.jpg", "title": "File:Weak.jpg", "license": "CC0",
                   "vision_review": {"shows_topic": "no", "usable": True, "safe": True}})
    (folder / "weak.jpg").write_bytes(b"jpg")
    (folder / "credits.json").write_text(json.dumps({"reviewed": reviewed, "images": images}), encoding="utf-8")


def test_distinct_approved_authoritative_photos_are_counted(tmp_path):
    _pool(tmp_path, "rich", 9, dupes=2)
    assert topic_engine.authoritative_images("rich", tmp_path) == 9            # duplicates and weak photos not counted
    _pool(tmp_path, "unreviewed", 3, reviewed=False)
    assert topic_engine.authoritative_images("unreviewed", tmp_path) is None
    assert topic_engine.authoritative_images("missing", tmp_path) is None


def test_thin_topics_are_demoted_with_a_reason_never_dropped(tmp_path):
    assert config.TOPIC_MIN_AUTHORITATIVE_IMAGES == 8
    _pool(tmp_path, "thin", 3)
    _pool(tmp_path, "rich", 8)
    topics = [{"id": "thin", "title": "Thin"}, {"id": "unfetched", "title": "Not fetched"}, {"id": "rich", "title": "Rich"}]
    ordered, flagged = topic_engine.demote_thin(topics, tmp_path)
    assert [t["id"] for t in ordered] == ["unfetched", "rich", "thin"]           # all kept; the thin one last
    [(topic, why)] = flagged
    assert topic["id"] == "thin" and "only 3 distinct approved photo(s)" in why and "needs 8" in why


def test_the_selection_uses_the_gate_and_says_it():
    src = Path(main.__file__).read_text(encoding="utf-8")
    assert "ranked, thin = demote_thin(ranked)" in src and "Topics moved back: too few photos" in src


# ------------------------------------------------------------------ 5. further open-access sources
def _replies(monkeypatch, mapping, seen=None):
    def get(url, params=None, timeout=30, headers=None):
        if seen is not None:
            seen.append((url, params, headers))
        for key, value in mapping.items():
            if key in url:
                return value
        return None
    monkeypatch.setattr(ms, "_get", get)


def test_openverse_takes_open_licences_on_allowed_hosts_only(monkeypatch):
    item = lambda lic, url, v="4.0": {"title": "Pont du Gard", "url": url, "license": lic, "license_version": v,
                                      "creator": "A. Photographer", "foreign_landing_url": "https://flickr.com/p/1",
                                      "provider": "flickr", "tags": [{"name": "aqueduct"}]}
    _replies(monkeypatch, {"api.openverse.org": {"results": [
        item("by", "https://live.staticflickr.com/1/a.jpg"), item("cc0", "https://upload.wikimedia.org/b.jpg"),
        item("by-nd", "https://live.staticflickr.com/1/c.jpg"), item("by-nc", "https://live.staticflickr.com/1/d.jpg"),
        item("pdm", "https://evil.example.com/e.jpg"), item("by", "http://live.staticflickr.com/1/f.jpg")]}})
    got = ms.openverse_search("pont du gard")
    assert [c["license"] for c in got] == ["CC BY 4.0", "CC0"]
    assert got[0]["attribution_required"] and got[0]["artist"] == "A. Photographer" and got[0]["provider"] == "openverse"
    assert "aqueduct" in got[0]["categories"]


def test_library_of_congress_takes_only_no_known_restrictions(monkeypatch):
    res = lambda rights, img="https://tile.loc.gov/storage-services/a/1r.jpg#h=100": {
        "title": "Aqueduct at Segovia", "url": "https://www.loc.gov/item/1/", "date": "1890",
        "image_url": ["https://tile.loc.gov/storage-services/a/1t.gif", img], "rights_advisory": rights}
    _replies(monkeypatch, {"www.loc.gov": {"results": [
        res("No known restrictions on publication."), res("Rights status not evaluated."),
        res(["No known restrictions"], img="https://evil.example.com/x.jpg")]}})
    [got] = ms.loc_search("segovia aqueduct")
    assert got["download_url"] == "https://tile.loc.gov/storage-services/a/1r.jpg" and got["year"] == 1890
    assert got["license"] == "Public domain" and got["provider"] == "loc" and "No known restrictions" in got["description"]


def test_art_institute_takes_public_domain_artworks_through_iiif(monkeypatch):
    seen = []
    _replies(monkeypatch, {"api.artic.edu": {"config": {"iiif_url": "https://www.artic.edu/iiif/2"}, "data": [
        {"id": 1, "title": "View of the Aqueduct", "image_id": "abc", "is_public_domain": True, "date_start": 1820,
         "artist_display": "Hubert Robert", "date_display": "1820"},
        {"id": 2, "title": "Modern print", "image_id": "def", "is_public_domain": False},
        {"id": 3, "title": "No image", "image_id": None, "is_public_domain": True}]}}, seen)
    [got] = ms.artic_search("aqueduct")
    assert got["download_url"] == "https://www.artic.edu/iiif/2/abc/full/843,/0/default.jpg"
    assert got["license"] == "CC0" and got["year"] == 1820 and got["source_url"].endswith("/artworks/1")
    assert seen[0][1]["query[term][is_public_domain]"] == "true" and "AIC-User-Agent" in seen[0][2]


def test_the_further_sources_are_off_until_enabled(monkeypatch):
    for flag in ("MUSEUM_OPENVERSE_ENABLED", "MUSEUM_LOC_ENABLED", "MUSEUM_ARTIC_ENABLED"):
        assert getattr(config, flag) is False
    monkeypatch.setattr(config, "SMITHSONIAN_API_KEY", "")
    monkeypatch.setattr(config, "EUROPEANA_API_KEY", "")
    assert ms.line_sources() == ("met", "nasa")
    asked = []
    for name in ms.SOURCES:
        monkeypatch.setitem(ms.SOURCES, name, lambda q, limit=12, n=name: asked.append(n) or [])
    ms.search_all("x", [])
    assert asked == list(ms.BASE_SOURCES)                                    # the pool's sources as before
    monkeypatch.setattr(config, "MUSEUM_ARTIC_ENABLED", True)
    assert ms.line_sources() == ("met", "nasa", "artic")


def test_cached_search_runs_new_sources_through_the_licence_and_keyword_filters(monkeypatch, tmp_path):
    monkeypatch.setattr(ms, "CACHE_FILE", tmp_path / "museum.json")
    monkeypatch.setitem(ms.SOURCES, "openverse", lambda q, limit=6: [
        ms._candidate(title="Pont du Gard aqueduct", download_url="https://upload.wikimedia.org/a.jpg", license="CC BY 4.0"),
        ms._candidate(title="Pont du Gard", download_url="https://upload.wikimedia.org/b.jpg", license="CC BY-SA 4.0"),
        ms._candidate(title="A cat", download_url="https://upload.wikimedia.org/c.jpg", license="CC0")])
    got = ms.cached_search("pont du gard", ["gard"], sources=("openverse",))
    assert [c["title"] for c in got] == ["Pont du Gard aqueduct"]         # share-alike off by default; off-subject out


def test_new_hosts_are_on_the_download_allow_list():
    from src.footage import _host_allowed
    assert _host_allowed("live.staticflickr.com", "openverse") and _host_allowed("upload.wikimedia.org", "openverse")
    assert _host_allowed("tile.loc.gov", "loc") and _host_allowed("www.artic.edu", "artic")
    assert not _host_allowed("staticflickr.com.evil.net", "openverse") and not _host_allowed("artic.edu.evil", "artic")


def test_the_video_check_is_told_a_beat_is_a_detail_crop(tmp_path):
    photo = _photo(tmp_path, "01.jpg", (3200, 2400), noise=True)
    crop, _ = pc.detail_crop(photo, tmp_path / "run", [], 1080 / 1920, min_width=600)
    assert "a detail (punch-in) of one of the documentary's own photos" in video_qa.beat_tag(crop)
