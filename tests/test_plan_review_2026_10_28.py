"""The picture against its narration line, before the render (render audit 2026-10-28).

Production's Roman concrete x3 were blocked after a full render for Pantheon photos under lines about modern
concrete and infrastructure; the gap search found nothing licensed and the repair after the render re-picked the
Pantheon. These tests reproduce that with fixtures: no model is called (picture_look is replaced), no network.
"""
from pathlib import Path

import pytest
from PIL import Image

from src import beat_images, config, improve, scene_planner

HOOK = "Modern concrete structures can deteriorate and crumble just decades after construction."
PANTHEON = "Dedicated in AD 128, Rome's Pantheon preserves the world's largest unreinforced concrete dome."
LIME = "Builders utilized hot mixing with quicklime, generating self-healing properties."
MODERN = "Today, uncontrolled cracking causes rapid structural degradation across modern infrastructure."


@pytest.fixture(autouse=True)
def fit_cache(tmp_path, monkeypatch):
    """Verdicts go to a temp file, never the repository's cache folder; the check is on, as deployed."""
    path = tmp_path / "cache" / "picture_fit.json"
    monkeypatch.setattr(beat_images, "FIT_CACHE_FILE", path)
    monkeypatch.setattr(config, "PICTURE_VISION_CHECK", True)
    return path


def _photo(folder: Path, name: str, shade: int) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    Image.new("RGB", (48, 48), (shade, 90, 40)).save(p, "JPEG")
    return p


def _graphic(folder: Path, name: str) -> Path:
    g = folder / name
    g.write_bytes(b"clip")
    Image.new("RGB", (48, 48)).save(g.with_suffix(".jpg"))  # its poster frame: what marks our own graphic
    return g


def _beats(*lines):
    return [{"start": 3.0 * i, "end": 3.0 * (i + 1), "text": t} for i, t in enumerate(lines)]


def _judge(monkeypatch, wrong=lambda name, line: "pantheon" in name and "modern infrastructure" in line):
    """picture_look as the light model would answer: a Pantheon photo does not show a modern-infrastructure line."""
    looks = []

    def look(path, query, size=512, ask=None):
        looks.append((Path(path).name, query))
        assert ask and query in ask  # asked about the line itself, not a search
        bad = wrong(Path(path).name, query)
        return {"checked": True, "shows": not bad, "burned_in_text": False,
                "reason": "the interior of the ancient Roman Pantheon" if bad else "shows it"}
    monkeypatch.setattr(beat_images, "picture_look", look)
    return looks


def test_roman_case_pantheon_under_modern_line_is_swapped_for_a_photo_that_shows_it(tmp_path, monkeypatch):
    looks = _judge(monkeypatch)
    hook, pantheon, lime = (_photo(tmp_path, n, s) for n, s in (("aqueduct.jpg", 10), ("pantheon_dome.jpg", 60),
                                                                  ("lime_clasts.jpg", 110)))
    other = _photo(tmp_path, "harbour_core.jpg", 160)
    cracked = _photo(tmp_path / "beat_images", "beat04_1.jpg", 210)  # fetched for beat 4's own line
    beats = _beats(HOOK, PANTHEON, LIME, MODERN)
    planned = [hook, pantheon, lime, pantheon]

    out, report = scene_planner.fit_to_lines(beats, planned, [hook, pantheon, lime, other, cracked], "Roman concrete",
                                             line_beat={cracked: 4})

    assert out == [hook, pantheon, lime, cracked]           # the photo fetched for that line is tried first
    assert report["swapped"] == [{"beat": 4, "from": "pantheon_dome.jpg", "to": "beat04_1.jpg"}]
    assert report["unshown"] == [] and scene_planner.fit_note(report) == ""
    assert ("aqueduct.jpg", HOOK) not in looks                # the hook is not judged
    assert report["judged"] == len(looks) == 4                # beats 2, 3, 4, then the one swap tried


def test_roman_case_nothing_licensed_shows_it_holds_the_previous_shot_and_says_so(tmp_path, monkeypatch):
    _judge(monkeypatch)
    hook, pantheon, lime = (_photo(tmp_path, n, s) for n, s in (("aqueduct.jpg", 10), ("pantheon_dome.jpg", 60),
                                                                  ("lime_clasts.jpg", 110)))
    exterior = _photo(tmp_path, "pantheon_exterior.jpg", 160)   # the only spare: another Pantheon
    beats = _beats(HOOK, PANTHEON, LIME, MODERN)

    out, report = scene_planner.fit_to_lines(beats, [hook, pantheon, lime, pantheon], [hook, pantheon, lime, exterior],
                                             "Roman concrete")

    assert out == [hook, pantheon, lime, lime]                  # never the Pantheon again; the shot carries on
    assert report["held"] == [{"beat": 4, "from": "pantheon_dome.jpg", "to": "lime_clasts.jpg"}]
    [entry] = report["unshown"]
    assert entry["beat"] == 4 and entry["line"] == MODERN and entry["fallback"] == "held"
    note = scene_planner.fit_note(report)
    assert note.startswith(scene_planner.FIT_NOTE_LABEL + ": beat 4: “Today, uncontrolled cracking")
    assert "no licensed photo shows it (the previous shot is repeated, reframed)" in note and "Regenerate" in note
    [finding] = scene_planner.fit_findings(report)
    assert finding.startswith("beat 4: 'Today, uncontrolled cracking") and "no licensed photo shows it" in finding


def test_our_own_matching_graphic_is_the_fallback_before_a_held_shot(tmp_path, monkeypatch):
    _judge(monkeypatch)
    hook, pantheon, lime = (_photo(tmp_path, n, s) for n, s in (("aqueduct.jpg", 10), ("pantheon_dome.jpg", 60),
                                                                  ("lime_clasts.jpg", 110)))
    numbers = _graphic(tmp_path, "graphic_numbers.mp4")
    monkeypatch.setattr(scene_planner.graphic_match, "score",
                        lambda g, text: 5 if "infrastructure" in text and Path(g).stem == "graphic_numbers" else 0)
    monkeypatch.setattr(scene_planner.graphic_match, "judgeable", lambda g: True)  # it declares its words
    beats = _beats(HOOK, PANTHEON, LIME, MODERN)

    out, report = scene_planner.fit_to_lines(beats, [hook, pantheon, lime, pantheon], [hook, pantheon, lime, numbers],
                                             "Roman concrete", durations=[3.0, 3.0, 3.0, 3.0])

    assert out[3] == numbers and report["graphic"][0]["beat"] == 4 and report["unshown"] == []


def test_a_good_match_passes_with_one_call_and_a_resume_pays_nothing(tmp_path, monkeypatch, fit_cache):
    looks = _judge(monkeypatch)
    hook, pantheon = _photo(tmp_path, "aqueduct.jpg", 10), _photo(tmp_path, "pantheon_dome.jpg", 60)
    beats = _beats(HOOK, PANTHEON)

    out, report = scene_planner.fit_to_lines(beats, [hook, pantheon], [hook, pantheon], "Roman concrete")
    assert out == [hook, pantheon] and len(looks) == 1 and (report["judged"], report["cached"]) == (1, 0)
    assert fit_cache.is_file()

    # A resumed run, a re-render or a regeneration that keeps the script: read from disk, no new call.
    out, report = scene_planner.fit_to_lines(beats, [hook, pantheon], [hook, pantheon], "Roman concrete")
    assert out == [hook, pantheon] and len(looks) == 1 and (report["judged"], report["cached"]) == (0, 1)

    # The line's spacing and case do not make it a new line; its words do.
    scene_planner.fit_to_lines(_beats(HOOK, "  " + PANTHEON.upper()), [hook, pantheon], [hook, pantheon], "x")
    assert len(looks) == 1
    scene_planner.fit_to_lines(_beats(HOOK, LIME), [hook, pantheon], [hook, pantheon], "x")
    assert len(looks) == 2


def test_a_rejected_verdict_is_cached_too_so_a_resume_repeats_the_same_repair_without_calls(tmp_path, monkeypatch):
    looks = _judge(monkeypatch)
    hook, pantheon, lime = (_photo(tmp_path, n, s) for n, s in (("aqueduct.jpg", 10), ("pantheon_dome.jpg", 60),
                                                                  ("lime_clasts.jpg", 110)))
    beats = _beats(HOOK, LIME, MODERN)
    first, _ = scene_planner.fit_to_lines(beats, [hook, lime, pantheon], [hook, pantheon, lime], "Roman concrete")
    n = len(looks)
    again, report = scene_planner.fit_to_lines(beats, [hook, lime, pantheon], [hook, pantheon, lime], "Roman concrete")
    assert again == first and len(looks) == n and report["judged"] == 0   # same scenes: the master checkpoint holds


def test_graphics_footage_and_the_hook_are_not_judged(tmp_path, monkeypatch):
    looks = _judge(monkeypatch, wrong=lambda name, line: True)  # everything would fail if it were looked at
    hook = _photo(tmp_path, "aqueduct.jpg", 10)
    numbers = _graphic(tmp_path, "graphic_numbers.mp4")
    film = _graphic(tmp_path, "footage_01.mp4")
    beats = _beats(HOOK, "Two thousand years.", "Archive film of the harbour.")

    out, report = scene_planner.fit_to_lines(beats, [hook, numbers, film], [hook, numbers, film], "Roman concrete")

    assert out == [hook, numbers, film] and looks == [] and report["judged"] == 0


def test_a_check_that_cannot_run_keeps_the_plan_and_stops_asking(tmp_path, monkeypatch):
    looks = []

    def down(path, query, size=512, ask=None):
        looks.append(Path(path).name)
        return {"checked": False, "shows": False, "burned_in_text": False, "reason": beat_images.UNAVAILABLE}
    monkeypatch.setattr(beat_images, "picture_look", down)
    photos = [_photo(tmp_path, f"{i:02d}.jpg", 20 * i) for i in range(4)]

    out, report = scene_planner.fit_to_lines(_beats(HOOK, PANTHEON, LIME, MODERN), photos, photos, "Roman concrete")

    assert out == photos and looks == ["01.jpg"] and report["stopped"] == "the picture check could not run"
    assert not beat_images.FIT_CACHE_FILE.exists()          # nothing looked at is remembered


def test_new_calls_stop_at_the_budget(tmp_path, monkeypatch):
    looks = _judge(monkeypatch, wrong=lambda name, line: True)
    photos = [_photo(tmp_path, f"{i:02d}.jpg", 20 * i) for i in range(6)]
    beats = _beats(*(f"line {i} about a named harbour" for i in range(4)))

    out, report = scene_planner.fit_to_lines(beats, photos[:4], photos, "x", max_calls=3)

    assert len(looks) == 3 and report["stopped"].startswith("call budget")


def test_the_check_switched_off_makes_no_call(tmp_path, monkeypatch):
    looks = _judge(monkeypatch)
    monkeypatch.setattr(config, "PRE_RENDER_PICTURE_FIT", False, raising=False)
    photos = [_photo(tmp_path, f"{i:02d}.jpg", 20 * i) for i in range(3)]
    out, report = scene_planner.fit_to_lines(_beats(HOOK, PANTHEON, MODERN), photos, photos, "x")
    assert out == photos and looks == [] and report["stopped"] == "off"


def test_line_fit_asks_about_the_line_and_keeps_the_photo_hash(tmp_path, monkeypatch):
    asked = []

    def look(path, query, size=512, ask=None):
        asked.append(ask)
        return {"checked": True, "shows": False, "burned_in_text": False, "reason": "a Roman temple"}
    monkeypatch.setattr(beat_images, "picture_look", look)
    p = _photo(tmp_path, "pantheon.jpg", 60)
    cache = {}
    v = beat_images.line_fit(p, MODERN, "Roman concrete", cache)
    assert v == {"fits": False, "reason": "a Roman temple", "cached": False}
    assert MODERN in asked[0] and "Roman concrete" in asked[0]
    assert "a picture of the documentary's subject is not enough" in asked[0]
    [entry] = cache.values()
    assert entry["file"] == "pantheon.jpg" and entry["fits"] is False
    assert beat_images.line_fit(p, MODERN, "Roman concrete", cache)["cached"] is True and len(asked) == 1


def _render_stage(db, monkeypatch, tmp_path, payload, photos, plans_out, reports):
    """The whole render stage, offline: every stage but the scene plan, the picture look and the video check
    stubbed. Returns (coordinator, renders)."""
    import main
    from main import PipelineCoordinator
    coord = PipelineCoordinator(db)
    monkeypatch.setattr(coord, "_verify_final_copy", lambda *a: None)
    monkeypatch.setattr(main, "ensure_all", lambda paths: (paths, 0))
    monkeypatch.setattr(main, "ensure_resolution", lambda p: p)
    monkeypatch.setattr(main, "load_credits", lambda d: {"images": []})
    monkeypatch.setattr(main, "llm_available", lambda: True)
    monkeypatch.setattr(main.beat_images, "plan_queries", lambda beats, topic: "plan")
    monkeypatch.setattr(main.footage, "source", lambda beats, topic, d, plan=None: ([], []))
    monkeypatch.setattr(main.beat_images, "source", lambda beats, topic, d, plan=None: ([], []))  # nothing licensed
    monkeypatch.setattr(main.beat_images, "source_gaps", lambda *a, **k: ([], []))             # nor in the gap search
    monkeypatch.setattr(main.graphics, "make_all", lambda *a, **k: [])
    monkeypatch.setattr(main.callouts, "make", lambda *a, **k: None)
    monkeypatch.setattr(main.then_now, "make", lambda *a, **k: (None, []))
    beats = _beats(HOOK, PANTHEON, LIME, MODERN)
    monkeypatch.setattr(main, "segment_beats", lambda w, d: beats)
    plans = iter(plans_out)

    def plan(beats_, media, feedback=""):
        return next(plans)(feedback), [3.0] * 4, "n"
    monkeypatch.setattr(main, "plan_scenes", plan)
    monkeypatch.setattr(main, "render_parallax", lambda *a: None)
    reports = iter(reports)
    monkeypatch.setattr(main, "review_render", lambda *a: next(reports))
    monkeypatch.setattr(coord.visual_engine, "generate_thumbnails", lambda *a, **k: (tmp_path / "c.jpg", tmp_path / "g.jpg"))
    monkeypatch.setattr(coord.multiformat_gen, "generate_story_sequence", lambda *a, **k: [])
    monkeypatch.setattr(coord.multiformat_gen, "generate_carousel_sequence", lambda *a, **k: [])
    renders = []
    monkeypatch.setattr(coord.video_composer, "compose_video", lambda scenes, *a, **k: renders.append([Path(x).name for x in scenes]))
    monkeypatch.setattr(coord.video_composer, "make_review_proxy", lambda m, o: o)
    monkeypatch.setattr(coord, "publish_run", lambda run_id, dry_run, previous_status: {"status": "simulated"})
    db.create_run("r_roman", "01_topic")
    coord._render_approve_publish({"id": "01_topic", "title": "Roman concrete"}, "r_roman", tmp_path / "run", payload,
                                  list(photos), photos[0], tmp_path / "v.mp3", tmp_path / "bed.wav", [], 12.0,
                                  True, True, None)
    return coord, renders


def test_render_roman_case_repaired_before_the_render_composes_once(db, monkeypatch, tmp_path, curated_payload):
    """The Pantheon is caught on the plan, the master is composed once, the card says which line has no picture
    and the finding is filed."""
    from src.models import QAReport
    improve._buffer.clear()
    looks = _judge(monkeypatch)
    pool = tmp_path / "assets"
    hook, pantheon, lime = (_photo(pool, n, s) for n, s in (("aqueduct.jpg", 10), ("pantheon_dome.jpg", 60),
                                                              ("lime_clasts.jpg", 110)))
    payload = dict(curated_payload, caption="Story.")
    feedbacks = []
    _, renders = _render_stage(db, monkeypatch, tmp_path, payload, [hook, pantheon, lime],
                               [lambda fb: feedbacks.append(fb) or [hook, pantheon, lime, pantheon]],  # the Roman pick
                               [QAReport(summary="", issues=[])])

    assert len(renders) == 1 and len(feedbacks) == 1                  # composed once, no repair pass
    assert renders[0] == ["aqueduct.jpg", "pantheon_dome.jpg", "lime_clasts.jpg", "lime_clasts.jpg"]
    assert payload["picture_fit"]["unshown"][0]["beat"] == 4
    assert "beat 4: “Today, uncontrolled cracking" in payload["scene_plan_note"]
    assert [i["klass"] for i in improve._buffer if i["source"] == "picture_fit"] == ["video.picture_relevance"]
    assert len(looks) == 3   # beats 2, 3 and 4: no spare photo to try, and the hook is not judged


def test_the_repair_after_the_render_cannot_put_the_pantheon_back(db, monkeypatch, tmp_path, curated_payload):
    """Roman concrete run 1: the judge after the render flagged beat 4, the re-plan moved the Pantheon there
    again and the second render was blocked. The second plan now passes the same check (from cache where it can)."""
    from src.models import QAIssue, QAReport
    looks = _judge(monkeypatch)
    pool = tmp_path / "assets"
    hook, pantheon, lime, core = (_photo(pool, n, s) for n, s in (("aqueduct.jpg", 10), ("pantheon_dome.jpg", 60),
                                                                    ("lime_clasts.jpg", 110), ("drill_core.jpg", 160)))
    payload = dict(curated_payload, caption="Story.")
    flagged = QAReport(summary="", issues=[QAIssue(beat=4, severity="major", issue="The visual shows a drill core, "
                                                   "which contradicts the narration about modern infrastructure.")])
    _, renders = _render_stage(db, monkeypatch, tmp_path, payload, [hook, pantheon, lime, core],
                               [lambda fb: [hook, pantheon, lime, core], lambda fb: [hook, lime, core, pantheon]],
                               [flagged, QAReport(summary="", issues=[])])

    assert len(renders) == 2 and renders[1][3] != "pantheon_dome.jpg"
    assert renders[1] == ["aqueduct.jpg", "lime_clasts.jpg", "drill_core.jpg", "drill_core.jpg"]
    assert ("pantheon_dome.jpg", MODERN) in looks


def test_a_graphic_that_declares_nothing_is_never_placed_as_what_a_line_says(tmp_path, monkeypatch):
    """Review 2026-10-28: the fallback skipped graphic_match.judgeable, unlike keep_if_it_matches."""
    _judge(monkeypatch)
    hook, pantheon, lime = (_photo(tmp_path, n, s) for n, s in (("aqueduct.jpg", 10), ("pantheon_dome.jpg", 60),
                                                                  ("lime_clasts.jpg", 110)))
    numbers = _graphic(tmp_path, "graphic_numbers.mp4")
    monkeypatch.setattr(scene_planner.graphic_match, "score", lambda g, text: 5)
    monkeypatch.setattr(scene_planner.graphic_match, "judgeable", lambda g: False)
    out, report = scene_planner.fit_to_lines(_beats(HOOK, PANTHEON, LIME, MODERN), [hook, pantheon, lime, pantheon],
                                             [hook, pantheon, lime, numbers], "Roman concrete", durations=[3.0] * 4)
    assert numbers not in out and report["graphic"] == []
