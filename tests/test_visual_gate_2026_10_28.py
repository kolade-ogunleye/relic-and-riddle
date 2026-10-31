"""Visual-gate false positives from the audit of production and development (2026-10-28), each named by what
went wrong: a minor finding blocked the release with MINOR_FINDINGS_BLOCK_RELEASE off (the gate read the audit's
perfect-score "certified" flag); the vision check called our own treatments defects (a privacy blur "a grey oval
patch", blurred visitors' faces "fingers blocking the lens", a number card's backdrop "an empty dark gradient");
and a map shown after its place was named was still timed as late."""
import json
from pathlib import Path

from PIL import Image

from src import config, release, video_qa
from src.lineage import record_derived, record_graphic
from src.models import QAIssue, QAReport
from src.video_qa import QualityAuditor

ODESSA = ("The background image is an empty/blank dark gradient showing only text graphics, "
          "with no visual imagery.")


def _graphic(tmp_path, name, inputs):
    code = tmp_path / "draw.py"
    code.write_bytes(b"drawing code")
    (tmp_path / "graphics").mkdir(exist_ok=True)
    graphic = tmp_path / "graphics" / name
    graphic.write_bytes(b"graphic")
    record_graphic(graphic, code, inputs)
    return graphic


def _frames(video, times, out_dir):
    """A plain textured frame per time: not blank, nothing bright in the caption keep-out band."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, _ in enumerate(times, 1):
        im = Image.new("L", (54, 96))
        im.putdata([40 + (x * 7 + y * 3) % 30 for y in range(96) for x in range(54)])
        path = out_dir / f"qa_{i:02d}.jpg"
        im.convert("RGB").save(path)
        paths.append(path)
    return paths


def _run(master, payload):
    return {"run_id": "run_v", "topic_id": "t", "master_video_path": str(master), "script_payload": payload,
            "thumbnail_cover_path": "", "thumbnail_grid_path": "", "status": "pending_approval"}


def _visual(report):
    return next(c for c in report["checks"] if c["name"] == "visual_checks")


def test_one_minor_audit_finding_follows_the_setting_and_a_major_always_blocks(tmp_path, monkeypatch):
    # The real audit and the real gate (the earlier test replaced the gate's function, so it never saw this):
    # one minor timing finding scores 95, which is not "certified", and the gate blocked on that.
    monkeypatch.setattr(video_qa, "measure_program_loudness",
                        lambda path: {"integrated_lufs": config.TARGET_LUFS, "true_peak_dbtp": config.TARGET_TRUE_PEAK - 1})
    monkeypatch.setattr(video_qa, "extract_frames", _frames)
    master = tmp_path / "master.mp4"
    master.write_bytes(b"video")
    captions = tmp_path / "master_captions.ass"
    captions.write_text("[Events]\n", encoding="utf-8")
    numbers = _graphic(tmp_path, "graphic_numbers.mp4", {"payload": {"stat_cards": [{"value": "estimated 107 ft", "label": "Height"}]}})
    timeline = [{"asset": str(tmp_path / "hook.jpg"), "start": 0.0, "end": 4.0, "text": "A temple cut from one rock"},
                {"asset": str(tmp_path / "photo.jpg"), "start": 4.0, "end": 9.0, "text": "It rises 107 feet"},
                {"asset": str(numbers), "start": 9.0, "end": 14.0, "text": "above the courtyard, 107 feet"}]
    words = [{"word": "107", "start": 6.5, "dur": 0.3}, {"word": "courtyard", "start": 10.0, "dur": 0.3}]
    audit = QualityAuditor().audit_rendered_timeline(master, timeline, words, out_dir=tmp_path / "audit",
                                                     caption_path=captions)
    assert [i["severity"] for i in audit["issues"]] == ["minor"] and audit["issues"][0]["type"] == "temporal_desync"
    assert audit["score"] == 95 and audit["certified"] is False      # the flag the gate used to require
    clean_look = {"run": True, "issues": []}

    monkeypatch.setattr(config, "MINOR_FINDINGS_BLOCK_RELEASE", False)
    visual = _visual(release.write_release_report(_run(master, {"self_healing": audit, "video_check": clean_look}), dry_run=True))
    assert visual["status"] == "pass" and visual.get("minor") and visual["detail"] == audit["issues"]

    monkeypatch.setattr(config, "MINOR_FINDINGS_BLOCK_RELEASE", True)
    visual = _visual(release.write_release_report(_run(master, {"self_healing": audit, "video_check": clean_look}), dry_run=True))
    assert visual["status"] == "fail"

    major = dict(audit, issues=[dict(audit["issues"][0], severity="major")])
    major_look = {"run": True, "issues": [{"beat": 2, "severity": "major", "issue": "a modern car under ancient surveyors"}]}
    for flag in (False, True):
        monkeypatch.setattr(config, "MINOR_FINDINGS_BLOCK_RELEASE", flag)
        assert _visual(release.write_release_report(_run(master, {"self_healing": major, "video_check": clean_look}),
                                                    dry_run=True))["status"] == "fail"
        assert _visual(release.write_release_report(_run(master, {"self_healing": audit, "video_check": major_look}),
                                                    dry_run=True))["status"] == "fail"
    # Info never blocks; an audit of another master, or none, never passes.
    monkeypatch.setattr(config, "MINOR_FINDINGS_BLOCK_RELEASE", True)
    info = dict(audit, issues=[dict(audit["issues"][0], severity="info")])
    assert _visual(release.write_release_report(_run(master, {"self_healing": info, "video_check": clean_look}),
                                                dry_run=True))["status"] == "pass"
    monkeypatch.setattr(config, "MINOR_FINDINGS_BLOCK_RELEASE", False)
    stale = dict(audit, master_sha256="0" * 64)
    assert _visual(release.write_release_report(_run(master, {"self_healing": stale, "video_check": clean_look}),
                                                dry_run=True))["status"] == "fail"
    assert _visual(release.write_release_report(_run(master, {"video_check": clean_look}), dry_run=True))["status"] == "fail"


def test_a_map_named_earlier_is_on_time_and_one_shown_before_its_place_is_still_early(tmp_path):
    graphic = _graphic(tmp_path, "graphic_map.mp4", {"place": {"label": "Hal Saflieni Hypogeum, Paola", "country": "Malta"}})
    timeline = [{"asset": str(tmp_path / "hook.jpg"), "start": 0.0, "end": 5.0, "text": "Beneath the town of Paola, Malta"},
                {"asset": str(tmp_path / "photo.jpg"), "start": 5.0, "end": 12.0, "text": "a maze carved in rock"},
                {"asset": str(graphic), "start": 12.0, "end": 16.0, "text": "three superimposed levels"}]
    words = [{"word": "Paola,", "start": 2.0, "dur": 0.4}, {"word": "levels", "start": 13.0, "dur": 0.4}]
    report = QualityAuditor().audit_rendered_timeline(tmp_path / "absent.mp4", timeline, words)
    assert not [i for i in report["issues"] if i["type"] in ("semantic_desync", "temporal_desync")]
    # Shown 2 s before its place is first named: still early.
    early = [{"word": "levels", "start": 13.0, "dur": 0.4}, {"word": "Malta", "start": 18.0, "dur": 0.4}]
    report = QualityAuditor().audit_rendered_timeline(tmp_path / "absent.mp4", timeline, early)
    assert [i for i in report["issues"] if i["type"] == "temporal_desync" and "before" in i["issue"]]


def test_a_blank_backdrop_finding_on_our_own_graphic_is_not_counted():
    # Odessa beat 6 (development, 2026-10-27): our number card's dimmed backdrop, reported as a missing picture.
    assets = ["s/parallax/scene_01.mp4"] * 5 + ["s/graphics/graphic_numbers.mp4", "s/parallax/scene_07.mp4"]
    report = QAReport(issues=[QAIssue(beat=6, severity="minor", issue=ODESSA),            # as it was filed
                              QAIssue(beat=7, severity="major", issue=ODESSA),        # a photo beat: kept
                              QAIssue(beat=6, severity="minor", issue="The background is blurred and the number text is cut off at the right edge.")],
                      summary="s")
    kept = video_qa.drop_designed_backdrops(report, assets)
    assert [(i.beat, i.issue[:20]) for i in kept.issues] == [(7, ODESSA[:20]), (6, "The background is bl")]
    assert "Not counted" in kept.summary and "beat 6" in kept.summary
    major = QAReport(issues=[QAIssue(beat=6, severity="major", issue=ODESSA)], summary="s")
    assert video_qa.drop_designed_backdrops(major, assets).issues            # a major is never set aside by wording
    richat = "The background image is extremely blurred/out of focus compared to the other beats, obscuring visual context."
    assert video_qa.designed_backdrop_finding(richat) and video_qa.designed_backdrop_finding(ODESSA)
    assert not video_qa.designed_backdrop_finding("The map label reads 'Libya' but the narration says Mauritania.")
    assert video_qa.drop_designed_backdrops(None, assets) is None


def _composition(tmp_path):
    """A run as the pipeline leaves it: a privacy-blurred photo under a parallax move, AI motion from a photo,
    a wide archive film clip, our own graphic and a plain photo."""
    topic = tmp_path / "assets" / "topic"
    topic.mkdir(parents=True)
    for n in (1, 2, 5):
        Image.new("RGB", (8, 8)).save(topic / f"0{n}_commons.jpg")
    (topic / "credits.json").write_text(json.dumps({"images": [
        {"file": "01_commons.jpg", "title": "File:Pantheon front.jpg", "description": "The Pantheon in Rome, with visitors"},
        {"file": "02_commons.jpg", "title": "File:Amber Room 1917.jpg", "description": ""},
        {"file": "05_commons.jpg", "title": "File:Dome interior.jpg", "description": "Dome interior of the Pantheon"}]}),
        encoding="utf-8")
    run = tmp_path / "run"
    privacy = tmp_path / "cache" / "privacy"
    for d in ("parallax", "motion", "footage", "graphics"):
        (run / d).mkdir(parents=True)
    privacy.mkdir(parents=True)
    blurred = privacy / "01_commons_abc.jpg"
    Image.new("RGB", (8, 8), "grey").save(blurred)
    record_derived(blurred, [topic / "01_commons.jpg"], "face_blur")
    scene1 = run / "parallax" / "scene_01.mp4"
    scene1.write_bytes(b"clip 1")
    record_derived(scene1, [blurred], "parallax", {"preset": "push"})
    anchor = run / "motion" / "motion_02_anchor.jpg"
    Image.new("RGB", (8, 8), "white").save(anchor)
    record_derived(anchor, [topic / "02_commons.jpg"], "motion_anchor_frame")
    motion = run / "motion" / "motion_02.mp4"
    motion.write_bytes(b"ai clip")
    record_derived(motion, [anchor], "ai_motion", {"provider": "kling-3.0", "ai_generated": True})
    film = run / "footage" / "footage_01.mp4"
    film.write_bytes(b"film")
    film.with_suffix(".json").write_text(json.dumps({"title": "Pompeii And Vesuvius", "year": "1950",
                                                     "width": 640, "height": 480}), encoding="utf-8")
    tall = run / "footage" / "footage_02.mp4"
    tall.write_bytes(b"tall film")
    tall.with_suffix(".json").write_text(json.dumps({"title": "Harbour", "width": 1080, "height": 1920}), encoding="utf-8")
    graphic = _graphic(run, "graphic_numbers.mp4", {"payload": {}})
    scene5 = run / "parallax" / "scene_05.mp4"
    scene5.write_bytes(b"clip 5")
    record_derived(scene5, [topic / "05_commons.jpg"], "parallax", {"preset": "pan"})
    return [scene1, motion, film, graphic, scene5, tall, run / "parallax" / "unknown.mp4"]


def test_each_beat_says_how_its_picture_was_made(tmp_path):
    scenes = _composition(tmp_path)
    tags = [video_qa.beat_tag(s) for s in scenes]
    assert "privacy blurs applied over present-day faces" in tags[0]
    assert 'not shown on screen, may be wrong): "The Pantheon in Rome, with visitors"' in tags[0]
    assert "AI motion" in tags[1] and "Amber Room 1917" in tags[1] and "privacy" not in tags[1]
    assert tags[2] == ' [real archive film "Pompeii And Vesuvius" (1950); a wide shot shown whole, fitted over a blurred, darkened copy of itself]'
    assert tags[3] == " [our own graphic: numbers card]"
    assert "Dome interior of the Pantheon" in tags[4] and "privacy" not in tags[4] and "AI motion" not in tags[4]
    assert "fitted" not in tags[5] and "Harbour" in tags[5]       # a vertical clip fills the frame
    assert tags[6] == ""                                          # nothing recorded: nothing claimed
    # Only the privacy-blurred beat says so, and only the wide film says it is fitted.
    assert [i for i, t in enumerate(tags) if "privacy" in t] == [0]
    assert [i for i, t in enumerate(tags) if "fitted over a blurred" in t] == [2]


def test_the_judge_gets_the_tags_and_is_still_asked_for_real_defects(tmp_path, monkeypatch):
    scenes = _composition(tmp_path)
    beats = [{"start": i * 5.0, "end": i * 5.0 + 5, "text": f"line {i + 1}"} for i in range(len(scenes))]
    sent = {}

    def judge(system, content, model, **kw):
        sent["system"], sent["content"] = system, content
        return QAReport(issues=[QAIssue(beat=4, severity="minor", issue=ODESSA),
                                QAIssue(beat=1, severity="minor", issue="The photo is upside down.")], summary="ok")
    monkeypatch.setattr(video_qa.free_llm, "enabled", lambda: False)
    monkeypatch.setattr(video_qa, "claude_available", lambda: True)
    monkeypatch.setattr(video_qa, "extract_frames", _frames)
    monkeypatch.setattr(video_qa, "structured_call", judge)
    cover = tmp_path / "cover.jpg"
    Image.new("RGB", (8, 8)).save(cover)
    report = video_qa.review_render(tmp_path / "master.mp4", beats, cover, tmp_path, scenes)
    texts = [c["text"] for c in sent["content"] if c.get("type") == "text"]
    assert texts[0].startswith("Beat 1 [real archive picture") and "privacy blurs" in texts[0] and texts[0].endswith('narration: "line 1"')
    assert texts[2].startswith("Beat 3 [real archive film") and "fitted over a blurred" in texts[2]
    assert texts[3] == 'Beat 4 [our own graphic: numbers card] narration: "line 4"'
    assert texts[6] == 'Beat 7 narration: "line 7"'
    system = sent["system"]
    assert "not defects in themselves" in system and "privacy" in system and "visually jarring" in system
    for still_found in ("does not match or contradicts the narration", "upside-down", "placard", "cut off"):
        assert still_found in system
    # The graphic's backdrop finding is not counted; the real defect is kept.
    assert [(i.beat, i.issue) for i in report.issues] == [(1, "The photo is upside down.")]


def test_a_graphic_that_failed_to_draw_is_never_set_aside_as_a_designed_backdrop():
    """Review 2026-10-28: a card that drew only its backdrop is a real failure, and so is any major finding."""
    for real in ("The map card shows an empty background with no map drawn",
                 "Blank frame: the timeline graphic is missing, only a dark gradient background",
                 "The map image is blurry and low resolution", "The stat card picture is blank",
                 "the number card shows no number at all, only an empty dark screen",
                 "The image is blank white, nothing is visible"):
        assert not video_qa.designed_backdrop_finding(real), real
