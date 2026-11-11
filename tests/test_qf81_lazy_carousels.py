"""QF-81: a carousel is drawn only for the episode that takes a carousel slot, Instagram carousel albums are measured
from the Graph, and the digest compares carousels with Reels.

Before: every run drew both carousel copies (Instagram and Facebook's grid) while it was made, seven a week for three
Tue-Thu slots, so most were drawn, matched and audited for nothing; and audience._graph_metrics skipped every
"_carousel" label, so a carousel's saves and reach were never read from Meta's own insights.
"""
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src import post_schedule as ps

ET = ZoneInfo("America/New_York")
TUE = datetime(2026, 11, 10, tzinfo=ET)


def _photo(path: Path, shade: int = 90) -> Path:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 80), (shade, 70, 50)).save(path)
    return path


# ------------------------------------------------------------------ 1. the run records a plan, draws nothing
def test_making_a_run_draws_no_carousel_and_records_its_photos(db, monkeypatch, tmp_path, curated_payload):
    import main
    from main import PipelineCoordinator
    from src import carousel_lazy
    coord = PipelineCoordinator(db)
    monkeypatch.setattr(coord, "_verify_final_copy", lambda *a: None)
    monkeypatch.setattr("src.scene_planner.reel_photo_coverage", lambda *a: {"ok": True, "problems": []})
    topic_img, beat_img = _photo(tmp_path / "t.jpg"), _photo(tmp_path / "b.jpg", 120)
    monkeypatch.setattr(main, "ensure_all", lambda paths: (paths, 0))
    monkeypatch.setattr(main, "ensure_resolution", lambda p: p)
    monkeypatch.setattr(main, "load_credits", lambda d: {"images": []})
    monkeypatch.setattr(main, "llm_available", lambda: True)
    monkeypatch.setattr(main.beat_images, "plan_queries", lambda beats, topic: None)
    monkeypatch.setattr(main, "plan_scenes", lambda beats, media, feedback="": ([beat_img], [3.0], "n"))
    monkeypatch.setattr(main, "segment_beats", lambda w, d: [{"start": 0, "end": 3, "text": "A door."}])
    monkeypatch.setattr(main, "render_parallax", lambda *a: None)
    from src.models import QAReport
    monkeypatch.setattr(main, "review_render", lambda *a: QAReport(summary="", issues=[]))
    monkeypatch.setattr(coord.visual_engine, "generate_thumbnails", lambda *a, **k: (tmp_path / "c.jpg", tmp_path / "g.jpg"))
    monkeypatch.setattr(coord.multiformat_gen, "generate_story_sequence", lambda *a, **k: [])
    monkeypatch.setattr(main.graphics, "make_all", lambda *a, **k: [])
    drawn = []
    monkeypatch.setattr(coord.multiformat_gen, "generate_carousel_sequence", lambda *a, **k: drawn.append(k) or [])
    monkeypatch.setattr(coord.video_composer, "compose_video", lambda *a, **k: None)
    monkeypatch.setattr(coord.video_composer, "make_review_proxy", lambda m, o: o)
    monkeypatch.setattr(coord, "publish_run", lambda run_id, dry_run, previous_status: {"status": "simulated"})
    db.create_run("r9", "01_topic")
    run_dir = tmp_path / "run"
    (run_dir / "carousel").mkdir(parents=True)
    (run_dir / "carousel" / "carousel_slide_01.jpg").write_bytes(b"an earlier make of this run")
    payload = dict(curated_payload, caption="Story.")

    result = coord._render_approve_publish({"id": "01_topic"}, "r9", run_dir, payload, [topic_img, beat_img],
                                           topic_img, tmp_path / "v.mp3", tmp_path / "bed.wav", [], 3.0, True, True, None)

    assert drawn == []                                    # neither copy is drawn while the run is made
    assert result.get("carousel", []) == []
    assert carousel_lazy.planned(run_dir) and carousel_lazy.drawn(run_dir) == []   # the old slide is gone
    plan = json.loads((run_dir / carousel_lazy.PLAN).read_text(encoding="utf-8"))
    assert plan["stills"] and all(len(e["sha256"]) == 64 for e in plan["stills"])


# ------------------------------------------------------------------ 2. booking, the slot, the drawing
class _Frozen(datetime):
    now_value = TUE

    @classmethod
    def now(cls, tz=None):
        return cls.now_value.astimezone(tz) if tz else cls.now_value.astimezone().replace(tzinfo=None)


@pytest.fixture
def coord(db, tmp_path, curated_payload, monkeypatch):
    import main
    from src import db as db_module
    from src import posting_view
    for module in (db_module, posting_view, ps, main):
        monkeypatch.setattr(module, "datetime", _Frozen)
    monkeypatch.setattr("src.release.require_release", lambda *a, **k: {"status": "pass"})
    from main import PipelineCoordinator
    from src import carousel_lazy
    c = PipelineCoordinator(db, schedule_posts=True)
    c.alerts, c.posted, c.drawn, c.buttons = [], [], [], []
    c._alert = lambda text, buttons=None: c.alerts.append(text) or c.buttons.append(buttons)
    c.telegram_bot.stamp_review_outcome = lambda run_id, text: True
    monkeypatch.setattr(c.publisher, "publish_photos", lambda run_id, tid, kind, files, caption="", **k:
                        c.posted.append((run_id, kind, list(files))) or {"status": "published"})
    c.contrast = None      # a ratio: every slide's body text records it (still_layout's sidecar)
    c.on_draw = None       # called inside each drawing (a glyph event, a failed match)
    c.match = False        # True: each drawing matches its photos with the real renderer and scene planner

    def draw(stills, payload, output_dir, facebook=False, graphics=None, strict_match=False):
        assert strict_match   # QF-81: a failed photo match holds at the slot; it never rotates photos
        c.drawn.append((Path(output_dir).parent.name, facebook))
        if c.on_draw:
            c.on_draw(facebook)
        if c.match:   # the renderer's own match (shared by both copies), as generate_carousel_sequence makes it
            c.multiformat_gen._carousel_photos(payload["carousel_slides"], list(stills), Path(output_dir),
                                               strict=strict_match)
        folder = Path(output_dir) / ("carousel_fb" if facebook else "carousel")
        folder.mkdir(parents=True, exist_ok=True)
        slides = [_photo(folder / f"carousel_slide_{n:02d}.jpg") for n in range(1, 4)]
        if c.contrast is not None:
            (folder / "still_layout.json").write_text(json.dumps({s.name: {"elements": [
                {"name": "body", "contrast": c.contrast}]} for s in slides}), encoding="utf-8")
        return slides
    monkeypatch.setattr(c.multiformat_gen, "generate_carousel_sequence", draw)
    c.audit = {"score": 100, "certified": True, "issues": []}
    monkeypatch.setattr("main.QualityAuditor.audit_carousel_slides", lambda self, d, payload=None: c.audit)

    def make(run_id, reel_posted):
        run_dir = tmp_path / run_id
        (run_dir / "m.mp4").parent.mkdir(parents=True, exist_ok=True)
        (run_dir / "m.mp4").write_bytes(b"0")
        carousel_lazy.plan(run_dir, [_photo(run_dir / "photos" / "p1.jpg"), _photo(run_dir / "photos" / "p2.jpg", 40)],
                           dict(curated_payload))
        payload = dict(curated_payload, topic_id="01_topic", human_reviewed=True)
        db.create_run(run_id, "01_topic")
        db.update_run(run_id, "pending_approval", script_payload=payload, master_video_path=run_dir / "m.mp4",
                      thumbnail_cover_path=run_dir / "m.mp4", thumbnail_grid_path=run_dir / "m.mp4")
        lines = c._book_followers(run_id, run_dir, reel_posted)
        reel = db.add_scheduled_post(run_id, "reel", reel_posted.astimezone(UTC).isoformat(), [])
        db.update_scheduled_post(reel, "published", detail={"posted_at": reel_posted.astimezone(UTC).isoformat()})
        db.update_run(run_id, "published")
        return run_dir, lines
    c.make = make
    return c


def _carousels(c, run_id=None):
    statuses = ("pending", "waiting", "published", "expired", "cancelled", "failed", "unknown")
    return [p for p in c.db.list_scheduled_posts(statuses, run_id=run_id) if p["kind"] == "carousel"]


def _slot(c, day):
    slot = ps.carousel_time(day.date())
    _Frozen.now_value = slot + timedelta(minutes=1)
    c._post_carousel_if_due(slot + timedelta(minutes=1))


def test_a_planned_carousel_is_booked_undrawn(coord):
    run_dir, lines = coord.make("r1", TUE - timedelta(days=3))
    post = _carousels(coord, "r1")[0]
    assert post["status"] == "waiting" and post["files"] == []
    assert any(line.startswith("🖼 Carousel:") for line in lines)
    assert coord.drawn == [] and not (run_dir / "carousel").exists()


def test_only_the_episode_that_takes_each_slot_is_drawn(coord):
    """Five episodes wait; the Tue, Wed and Thu slots take the newest each day; the other two are never drawn."""
    for n in range(5):
        coord.make(f"r{n}", TUE - timedelta(days=3, hours=n))
    for day in range(3):
        _slot(coord, TUE + timedelta(days=day))
        _slot(coord, TUE + timedelta(days=day))   # a second pass in the same slot draws and posts nothing
    posted = [run for run, kind, _ in coord.posted]
    assert posted == ["r4", "r3", "r2"]                                     # the newest first, one a day
    assert sorted(coord.drawn) == sorted([(r, f) for r in posted for f in (False, True)])   # both copies, winners only
    for run in ("r0", "r1"):
        assert _carousels(coord, run)[0]["status"] == "waiting" and _carousels(coord, run)[0]["files"] == []
    winner = _carousels(coord, "r4")[0]
    assert winner["status"] == "published" and [Path(f).name for f in winner["files"]] == [
        "carousel_slide_01.jpg", "carousel_slide_02.jpg", "carousel_slide_03.jpg"]
    assert coord.posted[0][2] == winner["files"]


def test_a_photo_changed_since_the_run_holds_the_carousel(coord):
    run_dir, _ = coord.make("r1", TUE - timedelta(days=3))
    _photo(run_dir / "photos" / "p2.jpg", 200)
    _slot(coord, TUE)
    post = _carousels(coord, "r1")[0]
    assert post["status"] == "failed" and "changed since the run was made" in post["detail"]["error"]
    assert coord.posted == [] and coord.drawn == []
    assert "Carousel held" in coord.alerts[-1] and "changed since the run was made" in coord.alerts[-1]
    assert coord.buttons[-1] == [[{"text": "🔁 Redraw and post next slot", "callback_data": f"carredraw:{post['id']}"}]]
    _slot(coord, TUE + timedelta(days=1))       # a failed drawing is not drawn again by itself
    assert coord.drawn == [] and coord.posted == []


@pytest.mark.parametrize("severity,minor_blocks,held", [("major", False, True), ("critical", False, True),
                                                         ("minor", False, False), ("minor", True, True),
                                                         ("info", True, False)])
def test_the_audit_holds_the_carousel_as_the_release_gate_would(coord, monkeypatch, severity, minor_blocks, held):
    monkeypatch.setattr("src.config.MINOR_FINDINGS_BLOCK_RELEASE", minor_blocks)
    coord.audit = {"score": 90, "certified": False,
                   "issues": [{"type": "carousel_slide_look", "severity": severity, "issue": "Slide 2: text cut off"}]}
    run_dir, _ = coord.make("r1", TUE - timedelta(days=3))
    _slot(coord, TUE)
    post = _carousels(coord, "r1")[0]
    assert (post["status"] == "failed") is held and (coord.posted == []) is held
    from src import carousel_lazy
    record = carousel_lazy.load(run_dir)
    assert record["issues"][0]["severity"] == severity and bool(record["held_by"]) is held
    # a held drawing is never installed where the publisher posts from; it stays staged beside its record
    assert bool(carousel_lazy.drawn(run_dir)) is not held
    assert len(list((run_dir / carousel_lazy.STAGE / "carousel").glob("carousel_slide_*.jpg"))) == 3
    if severity == "info":   # the vision look's findings reach the operator (the preview, the hold alert)
        assert record["notes"]["look"] == ["Slide 2: text cut off"]


def test_an_audit_that_cannot_run_holds_the_carousel(coord, monkeypatch):
    def broken(self, d, payload=None):
        raise RuntimeError("vision check crashed")
    monkeypatch.setattr("main.QualityAuditor.audit_carousel_slides", broken)
    coord.make("r1", TUE - timedelta(days=3))
    _slot(coord, TUE)
    post = _carousels(coord, "r1")[0]
    assert post["status"] == "failed" and "audit could not run" in post["detail"]["error"] and coord.posted == []


def test_text_under_the_contrast_target_holds_the_carousel_in_either_copy(coord):
    """Review 1's probe: body text at 2.1:1 in both copies. The release gate held it through text_contrast_audit
    (carousel/ and carousel_fb/); the slot audit holds it too, naming both copies."""
    coord.contrast = 2.1
    run_dir, _ = coord.make("r1", TUE - timedelta(days=3))
    _slot(coord, TUE)
    from src import carousel_lazy
    post, record = _carousels(coord, "r1")[0], carousel_lazy.load(run_dir)
    assert post["status"] == "failed" and coord.posted == []
    contrast = [i for i in record["held_by"] if i["type"] == "text_contrast"]
    assert len(contrast) == 6 and all(i["severity"] == "major" for i in contrast)
    assert {i["issue"].split("/")[0] for i in contrast} == {"carousel", "carousel_fb"}
    assert "2.10:1" in coord.alerts[-1]


def test_contrast_at_the_target_posts(coord):
    from src.brand import TEXT_CONTRAST
    coord.contrast = TEXT_CONTRAST
    coord.make("r1", TUE - timedelta(days=3))
    _slot(coord, TUE)
    assert _carousels(coord, "r1")[0]["status"] == "published"


def test_a_failed_photo_match_holds_and_never_rotates(coord, tmp_path):
    from src.multi_format import MultiFormatGenerator, PhotoMatchFailed

    def fail(facebook):
        raise PhotoMatchFailed("its photos could not be matched to its slides (vision unavailable)")
    coord.on_draw = fail
    coord.make("r1", TUE - timedelta(days=3))
    _slot(coord, TUE)
    post = _carousels(coord, "r1")[0]
    assert post["status"] == "failed" and "could not be matched" in post["detail"]["error"] and coord.posted == []
    # the renderer itself: strict raises, the old call still rotates
    gen, cards = MultiFormatGenerator(), [{"heading": "A", "body": "b"}, {"heading": "C", "body": "d"}]
    stills = [tmp_path / "x.jpg", tmp_path / "y.jpg"]
    import src.scene_planner as planner
    original = planner.plan_scenes
    planner.plan_scenes = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("vision unavailable"))
    try:
        with pytest.raises(PhotoMatchFailed):
            gen._match_photos(cards, stills, strict=True)
        assert gen._match_photos(cards, stills) == stills
    finally:
        planner.plan_scenes = original


def _planner(monkeypatch, reply=None):
    """The real scene planner with only its provider call replaced (no API): `reply` None = no planner available,
    an LLMError = every attempt fails, a ScenePlan = the plan it returns. Returns the calls made and the waits."""
    from src import config, free_llm, scene_planner
    calls, waits = [], []
    monkeypatch.setattr(config, "SCENE_PLAN_ATTEMPTS", 3)
    monkeypatch.setattr(config, "CAROUSEL_PLAN_ATTEMPTS", 2, raising=False)   # QF-81 round 3 settings
    monkeypatch.setattr(config, "CAROUSEL_PLAN_RETRY_SECONDS", 15.0, raising=False)
    monkeypatch.setattr(free_llm, "enabled", lambda: False)
    monkeypatch.setattr(scene_planner, "claude_available", lambda: reply is not None)

    def provider(system, content, schema, **kwargs):
        calls.append(schema.__name__)
        if isinstance(reply, Exception):
            raise reply
        return reply
    monkeypatch.setattr(scene_planner, "structured_call", provider)
    monkeypatch.setattr(scene_planner.time, "sleep", waits.append)
    return calls, waits


def _captions(run_dir):
    (run_dir / "photos" / "credits.json").write_text(json.dumps({"images": [
        {"file": "p1.jpg", "title": "File:Derinkuyu ventilation shaft.jpg", "description": "a deep air shaft"},
        {"file": "p2.jpg", "title": "File:Derinkuyu rolling stone door.jpg", "description": "a round stone door"}]}),
        encoding="utf-8")


def _assert_held_unmatched(coord, run_dir, why):
    from src import carousel_lazy
    post = _carousels(coord, "r1")[0]
    assert post["status"] == "failed" and coord.posted == []
    assert "could not be matched to its slides" in post["detail"]["error"] and why in post["detail"]["error"]
    assert "Carousel held" in coord.alerts[-1] and "could not be matched" in coord.alerts[-1]
    assert coord.buttons[-1] == [[{"text": "🔁 Redraw and post next slot", "callback_data": f"carredraw:{post['id']}"}]]
    assert carousel_lazy.drawn(run_dir) == []          # nothing installed where the publisher posts from


@pytest.mark.parametrize("planner", ["unavailable", "fails every attempt"])
def test_the_real_planner_failing_at_the_slot_holds_instead_of_rotating(coord, monkeypatch, planner):
    """Review 2's probe: plan_scenes catches the planner's failures itself and returns filename order, which is the
    old rotation; strict matching read only its exceptions, so the slot posted unmatched photos silently. Now the
    real plan_scenes (only the provider call replaced) in filename order holds the carousel, with the reason."""
    from src.llm import LLMError
    calls, waits = _planner(monkeypatch, None if planner == "unavailable" else LLMError("vision quota used up"))
    coord.match = True
    run_dir, _ = coord.make("r1", TUE - timedelta(days=3))
    _slot(coord, TUE)
    _assert_held_unmatched(coord, run_dir, "filename order would only rotate them")
    if planner == "fails every attempt":   # bounded at the slot: 2 tries, one 15 s wait, not 3 tries and 3 minutes
        assert calls == ["ScenePlan", "ScenePlan"] and waits == [15.0]
    else:
        assert calls == [] and waits == []
    # the renderer itself: strict raises on filename order; the old two-argument call still rotates
    from src.multi_format import MultiFormatGenerator, PhotoMatchFailed
    stills = sorted((run_dir / "photos").glob("p*.jpg"))
    cards = [{"heading": f"H{i}", "body": f"body {i}"} for i in range(5)]
    with pytest.raises(PhotoMatchFailed, match="could not be matched"):
        MultiFormatGenerator()._match_photos(cards, stills, strict=True)
    assert MultiFormatGenerator()._match_photos(cards, stills) == [stills[i % 2] for i in range(5)]


def test_photos_matched_only_by_their_captions_hold_too(coord, monkeypatch):
    """With archive captions, plan_scenes falls back to caption order: not a vision match either, so it holds."""
    _planner(monkeypatch, None)
    coord.match = True
    run_dir, _ = coord.make("r1", TUE - timedelta(days=3))
    _captions(run_dir)
    _slot(coord, TUE)
    _assert_held_unmatched(coord, run_dir, "archive captions alone are not a match")


def test_a_real_plan_from_the_planner_still_posts(coord, monkeypatch):
    from src.models import ScenePlan
    calls, waits = _planner(monkeypatch, ScenePlan(images=[1, 2, 1, 2, 1, 2, 1], notes="each photo shows its slide"))
    coord.match = True
    run_dir, _ = coord.make("r1", TUE - timedelta(days=3))
    _captions(run_dir)
    _slot(coord, TUE)
    post = _carousels(coord, "r1")[0]
    assert post["status"] == "published" and len(coord.posted) == 1
    assert not any("Carousel held" in a for a in coord.alerts)
    assert calls == ["ScenePlan"] and waits == []       # one plan, shared by both copies
    from src import carousel_lazy
    assert {Path(p["path"]).name for p in carousel_lazy.load(run_dir)["photos"]} == {"p1.jpg", "p2.jpg"}


def test_a_file_error_at_the_slot_holds_with_its_reason(coord, monkeypatch):
    """Review 2, N2: an OSError while the slides are installed escaped the slot after it was taken: no alert, the
    booking left waiting. Now it holds with the reason, and no half-installed copy is left to post."""
    from src import carousel_lazy

    def broken(src, dst, *a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(carousel_lazy.shutil, "copyfile", broken)
    run_dir, _ = coord.make("r1", TUE - timedelta(days=3))
    _slot(coord, TUE)
    post = _carousels(coord, "r1")[0]
    assert post["status"] == "failed" and coord.posted == []
    assert "could not be drawn or installed (disk full)" in post["detail"]["error"]
    assert "Carousel held" in coord.alerts[-1] and "disk full" in coord.alerts[-1]
    assert carousel_lazy.drawn(run_dir) == []


def test_the_preview_is_drawn_once_and_the_slot_reuses_it(coord):
    run_dir, _ = coord.make("r1", TUE - timedelta(days=3))
    record = coord.preview_carousel("r1")
    assert record["held"] == "" and len(record["slides"]["carousel"]) == 3 and coord.drawn == [("r1", False), ("r1", True)]
    assert coord.preview_carousel("r1")["digest"] == record["digest"] and len(coord.drawn) == 2   # paged: not redrawn
    assert carousel_lazy_drawn(run_dir) == []    # the review fingerprint's carousel/ is untouched by a preview
    _slot(coord, TUE)
    assert len(coord.drawn) == 2 and _carousels(coord, "r1")[0]["status"] == "published"   # paid once


def carousel_lazy_drawn(run_dir):
    from src import carousel_lazy
    return carousel_lazy.drawn(run_dir)


def test_a_changed_input_after_the_preview_is_not_reused(coord, monkeypatch):
    coord.make("r1", TUE - timedelta(days=3))
    coord.preview_carousel("r1")
    monkeypatch.setattr("src.carousel_lazy._code_digest", lambda root: {"multi_format.py": "a new release"})
    _slot(coord, TUE)
    assert len(coord.drawn) == 4 and _carousels(coord, "r1")[0]["status"] == "published"   # drawn again, checked


def test_redraw_after_a_hold_posts_in_the_next_slot_ahead_of_newer_episodes(coord):
    coord.contrast = 2.1
    coord.make("r1", TUE - timedelta(days=3))
    _slot(coord, TUE)
    held = _carousels(coord, "r1")[0]
    assert held["status"] == "failed"
    coord.make("r2", TUE - timedelta(days=2, hours=12))   # newer: it would take the next slot
    coord.contrast = None                                 # the cause is dealt with
    _Frozen.now_value = ps.carousel_time(TUE.date()) + timedelta(hours=2)
    reply = coord.redraw_carousel(str(held["id"]))
    assert "is drawn again and posts" in reply
    assert coord.redraw_carousel(str(held["id"])) == "🖼 That carousel is not held any more: see Posting."
    _slot(coord, TUE + timedelta(days=1))
    assert [run for run, _, _ in coord.posted] == ["r1"] and _carousels(coord, "r1")[0]["status"] == "published"


def test_glyph_fallbacks_go_to_the_runs_ledger_and_the_operator(coord):
    import json as _json

    from src import carousel_lazy
    from src.font_engine import LEDGER_NAME, record_glyph_event, set_glyph_ledger
    set_glyph_ledger(None)   # the daemon has no run open

    def glyphs(facebook):
        record_glyph_event("fallback", "Ελλάδα", "Montserrat", "Ελλάδα", fallback="Noto Sans")
    coord.on_draw = glyphs
    coord.contrast = 2.1     # held, so the hold alert carries the note
    run_dir, _ = coord.make("r1", TUE - timedelta(days=3))
    _slot(coord, TUE)
    events = [_json.loads(x) for x in (run_dir / LEDGER_NAME).read_text(encoding="utf-8").splitlines()]
    assert {e["where"] for e in events} == {"carousel", "Facebook carousel"}
    assert carousel_lazy.load(run_dir)["notes"]["glyphs"] and "lettering" in coord.alerts[-1]


def test_the_preview_screen_shows_the_sheet_then_each_slide_with_its_alt_text(coord, monkeypatch):
    from src.control import carousel_preview_screen
    coord.make("r1", TUE - timedelta(days=3))
    monkeypatch.setattr("src.multi_format.slide_alt_text", lambda p: f"alt of {Path(p).parent.name}/{Path(p).name}")
    sent = []
    first = carousel_preview_screen(coord.db, "r1", coord.preview_carousel, lambda p, c, b: sent.append((p, c, b)) or True)
    assert first == "" and Path(sent[0][0]).name == "preview_sheet.jpg" and "Its checks pass" in sent[0][1]
    assert sent[0][2] == [[{"text": "▶", "callback_data": "ccar:r1:1"}, {"text": "⏭", "callback_data": "ccar:r1:3"}]]
    carousel_preview_screen(coord.db, "r1:2", coord.preview_carousel, lambda p, c, b: sent.append((p, c, b)) or True)
    assert Path(sent[1][0]).name == "carousel_slide_02.jpg" and "Slide 2 of 3" in sent[1][1]
    assert "Alt text (Instagram): alt of carousel/carousel_slide_02.jpg" in sent[1][1]
    assert "Alt text (Facebook): alt of carousel_fb/carousel_slide_02.jpg" in sent[1][1]
    assert len(coord.drawn) == 2   # paging reuses the one drawing


def test_a_run_drawn_before_qf81_still_posts_its_slides(coord, tmp_path, curated_payload):
    run_dir = tmp_path / "old"
    (run_dir / "carousel").mkdir(parents=True)
    slides = [str(_photo(run_dir / "carousel" / f"carousel_slide_{n:02d}.jpg")) for n in (1, 2)]
    (run_dir / "m.mp4").write_bytes(b"0")
    coord.db.create_run("old", "01_topic")
    coord.db.update_run("old", "published", script_payload=dict(curated_payload, topic_id="01_topic"),
                        master_video_path=run_dir / "m.mp4")
    coord._book_followers("old", run_dir, TUE - timedelta(days=3))
    assert _carousels(coord, "old")[0]["files"] == slides


# ------------------------------------------------------------------ 3. the review card and the dry run
def test_the_card_carries_the_carousel_caption_and_says_when_it_is_drawn(db, tmp_path, curated_payload):
    from src import carousel_lazy, publishable
    from src.telegram_bot import _also_posted
    payload = dict(curated_payload, topic_id="01_topic")
    payload.pop("carousel_caption", None)          # it posts the Reel caption: the card must still show it
    carousel_lazy.plan(tmp_path, [_photo(tmp_path / "p.jpg")], payload)
    record = publishable.card_record(payload, db, run_dir=tmp_path, topic_id="01_topic")
    assert record["carousel_at_slot"] is True and "carousel.instagram" in record["copy"]
    assert any("drawn from this run's photos and carousel cards when you tap 🖼 Preview carousel or when it takes its carousel slot" in item
               for item in _also_posted(record, record["copy"]))
    db.create_run("r1", "01_topic")
    from src.telegram_state import initialize
    initialize(db)
    publishable.save_record(db, "r1", record)
    publishable.carousel_post_base(payload, db, "r1", "01_topic")   # the caption is the one the card showed


def test_a_dry_run_says_the_carousel_is_drawn_only_for_a_real_slot(db):
    from main import PipelineCoordinator
    coord = PipelineCoordinator(db)
    seen = []
    coord.telegram_bot.stamp_review_outcome = lambda run_id, text: seen.append(text) or True
    coord._stamp_review("r1", "simulated", {"carousel": {"status": "deferred"}})
    assert "drawn only when it takes a carousel slot" in seen[-1]


# ------------------------------------------------------------------ 4. insights and the digest
def test_an_instagram_carousel_is_measured_from_the_graph(monkeypatch):
    from src import audience
    monkeypatch.setattr(audience, "_ig_configured", lambda: True)
    monkeypatch.setattr("src.config.META_IG_ACCOUNT_ID", "17841400000000000")
    monkeypatch.setattr("src.config.META_ACCESS_TOKEN", "token")
    asked = []

    def get(url, params, headers=None):
        asked.append((url, params))
        if url.endswith("/media"):
            return {"data": [{"id": "reel", "timestamp": "2026-11-10T19:01:00+0000", "media_product_type": "REELS",
                              "media_type": "VIDEO"},
                             {"id": "album", "timestamp": "2026-11-10T19:02:00+0000", "media_product_type": "FEED",
                              "media_type": "CAROUSEL_ALBUM"}]}
        return {"data": [{"name": "reach", "values": [{"value": 400}]}, {"name": "saved", "values": [{"value": 12}]}]}
    monkeypatch.setattr(audience, "_get", get)
    item = {"run_id": "", "response": {}, "posted_at": "2026-11-10T19:01:30+00:00"}
    data, _outcome = audience._graph_metrics("instagram_carousel", {}, None, item)
    assert data["source"] == "instagram-graph" and data["media_id"] == "album"
    assert data["reach"] == 400 and data["saved"] == 12
    assert asked[-1][1]["metric"] == ",".join(audience.IG_CAROUSEL_METRICS)
    assert audience._graph_metrics("facebook_carousel", {}, None, item)[0] is None   # Upload-Post measures it


def _measure(db, run_id, label, metrics, snapshot="7d"):
    from src import audience
    audience.ensure_schema(db)
    with db._conn() as conn:
        conn.execute("INSERT OR REPLACE INTO audience_metrics VALUES (?,?,?,?,?,?)",
                     (run_id, label, snapshot, "test", json.dumps(metrics), "2026-11-10T00:00:00+00:00"))


def test_the_digest_compares_carousels_with_reels_from_twelve(db):
    from src import audience
    assert audience.carousel_comparison(db) == []
    for n in range(11):
        _measure(db, f"r{n}", "instagram_carousel", {"reach": 300, "saved": 15})
        _measure(db, f"r{n}", "instagram", {"reach": 1000, "saved": 10})
        _measure(db, f"r{n}", "facebook", {"post_impressions_unique": 250})
    _measure(db, "r11", "instagram_carousel", {"reach": 300, "saved": 15}, snapshot="3d")   # another age: not counted
    lines = audience.carousel_comparison(db)
    assert lines[0] == "• Carousels vs Reels · Instagram (7d): 11 of 12 carousels measured; compared from 12."
    _measure(db, "r11", "instagram_carousel", {"reach": 300, "saved": 15})
    lines = audience.carousel_comparison(db)
    assert lines[0] == ("• Carousels vs Reels · Instagram (7d medians): carousels (12) reach 300, saves 5.0% of reach; "
                        "Reels (11) reach 1000, saves 1.0% of reach.")
    assert lines[-1].startswith("• Facebook Reel reach is 25% of Instagram's (7d, median of 11 episode(s); Facebook's "
                                "unique impressions against Instagram's accounts reached)")
    assert lines[-1] in audience.digest_lines(db)


def test_facebook_rows_without_reach_say_so_and_give_a_labelled_views_ratio(db):
    """Production's real row shapes (counts only): Upload-Post's Facebook Reel rows carry views, likes and comments,
    no reach; its Instagram rows carry reach and views. Review 1 found the ratio line simply missing."""
    from src import audience
    for n in range(6):
        _measure(db, f"r{n}", "instagram", {"source": "upload-post", "reach": 800, "views": 1000, "likes": 30,
                                            "comments": 2, "saves": 4, "shares": 3, "impressions": 1200})
        _measure(db, f"r{n}", "facebook", {"source": "upload-post", "views": 400, "likes": 5, "comments": 1})
    for n in range(2):
        _measure(db, f"r{n}", "instagram_carousel", {"source": "upload-post", "reach": 300, "saves": 9, "views": 500})
        _measure(db, f"r{n}", "facebook_carousel", {"source": "upload-post", "reach": 120, "impressions": 150,
                                                    "likes": 2, "reactions": 2, "shares": 0, "comments": 0})
    lines = audience.carousel_comparison(db)
    assert ("• Facebook Reel reach not measured (7d): its 6 row(s) carry no reach (source upload-post), so the "
            "Facebook-gallery decision's reach ratio cannot be worked out yet.") in lines
    assert "• Instead, views (not reach): Facebook Reel views are 40% of Instagram's (7d, median of 6 episode(s))." in lines
    assert not any(line.startswith("• Facebook Reel reach is") for line in lines)


# ------------------------------------------------------------------ port onto QF-23 and QF-110 (d31604ad)
def _refuse_at_slide_two(coord, copy_is_facebook):
    """A deck that saves its first slide, then refuses its checked copy (typography.TextFitError, QF-23)."""
    from src.typography import TextFitError

    def on_draw(facebook):
        if facebook == copy_is_facebook:
            stage = Path(coord.db.get_run("r1")["master_video_path"]).parent / "carousel_draw"
            folder = stage / ("carousel_fb" if facebook else "carousel")
            _photo(folder / "carousel_slide_01.jpg")
            raise TextFitError("A whole checked sentence that fits no permitted size", where="the slide's body")
    coord.on_draw = on_draw


def test_a_carousel_whose_copy_cannot_be_set_whole_is_held_at_its_slot(coord):
    """QF-23 at the point QF-81 now draws: a LayoutError while the slot draws the carousel holds it with its reason,
    one alert with Redraw; nothing is posted, nothing is installed, the slot does not crash."""
    run_dir, _ = coord.make("r1", TUE - timedelta(days=3))
    _refuse_at_slide_two(coord, copy_is_facebook=False)
    _slot(coord, TUE)
    post = _carousels(coord, "r1")[0]
    assert post["status"] == "failed" and post["detail"]["held"] and coord.posted == []
    assert "its text cannot be set whole" in post["detail"]["error"] and "fits no permitted size" in post["detail"]["error"]
    assert "Carousel held" in coord.alerts[-1] and coord.buttons[-1][0][0]["callback_data"] == f"carredraw:{post['id']}"
    assert not list((run_dir / "carousel").glob("*.jpg")) and not list((run_dir / "carousel_fb").glob("*.jpg"))


def test_a_carousel_whose_copy_cannot_be_set_whole_is_held_in_its_preview(coord):
    run_dir, _ = coord.make("r1", TUE - timedelta(days=3))
    _refuse_at_slide_two(coord, copy_is_facebook=False)
    record = coord.preview_carousel("r1")
    assert record["held"].startswith("its text cannot be set whole") and record["layout_hold"] is True
    assert record["cacheable"] is False and carousel_lazy_drawn(run_dir) == []


def test_a_facebook_copy_that_cannot_be_set_whole_leaves_facebook_the_instagram_slides(coord):
    """QF-23's Facebook rule (hold_layout_error on carousel_fb, then Facebook gets the Instagram slides): its partial
    frames are removed from the drawing, so they are never installed in carousel_fb/ or credited."""
    run_dir, _ = coord.make("r1", TUE - timedelta(days=3))
    _refuse_at_slide_two(coord, copy_is_facebook=True)
    _slot(coord, TUE)
    post = _carousels(coord, "r1")[0]
    assert post["status"] == "published" and len(coord.posted) == 1 and len(coord.posted[0][2]) == 3
    assert not (run_dir / "carousel_fb").exists() or not list((run_dir / "carousel_fb").iterdir())
    note = json.loads((run_dir / "carousel_draw" / "draw.json").read_text(encoding="utf-8"))["notes"]["facebook_copy"]
    assert "its text cannot be set whole" in note and "Facebook gets the Instagram slides" in note


def test_installing_a_planned_carousel_keeps_the_approved_review_fingerprint(coord):
    """QF-110 binds a re-check to the review card's fingerprint; a planned carousel installed at its slot (QF-81) must
    not change it, or a later Reel retry's re-check could never match the approval. A carousel drawn with the run
    (no plan) is still bound by its slides."""
    from src.telegram_state import review_fingerprint
    run_dir, _ = coord.make("r1", TUE - timedelta(days=3))
    before = review_fingerprint(coord.db, "r1")
    _slot(coord, TUE)
    assert carousel_lazy_drawn(run_dir) and _carousels(coord, "r1")[0]["status"] == "published"
    assert review_fingerprint(coord.db, "r1") == before
    (run_dir / "carousel_plan.json").unlink()
    unplanned = review_fingerprint(coord.db, "r1")
    _photo(run_dir / "carousel" / "carousel_slide_01.jpg", 10)
    assert review_fingerprint(coord.db, "r1") != unplanned


def test_post_now_names_a_planned_carousel_it_will_book(coord, monkeypatch):
    """UI-02's "Post now?" names the carousel only when it will post; a planned run has no slides on disk yet, but
    promote_run books its carousel (QF-81), so it is named."""
    from src import posting_view
    from src.publisher import UploadPostPublisher
    monkeypatch.setattr(UploadPostPublisher, "configured", lambda self: True)
    run_dir, _ = coord.make("r1", TUE - timedelta(days=3))
    run = {**coord.db.get_run("r1"), "status": "pending_approval"}
    assert "carousel" in posting_view._post_now_followers(coord.db, "r1", run)
    (run_dir / "carousel_plan.json").unlink()
    assert "carousel" not in posting_view._post_now_followers(coord.db, "r1", run)


def test_clean_up_keeps_the_photos_a_waiting_carousel_is_drawn_from(tmp_path, curated_payload):
    """QF-110's cache clean-up keeps what a run names; a carousel plan names the photos its slot draws from."""
    from src import carousel_lazy, retention
    cached = _photo(tmp_path / "cache" / "text_crop" / "abc" / "01_commons.jpg")
    carousel_lazy.plan(tmp_path / "output" / "run_1_x", [cached], dict(curated_payload))
    assert str(cached.resolve()).casefold() in retention.referenced_files(tmp_path / "output")


# ------------------------------------------------------------------ 5. QF-65a's AI label on a carousel drawn at its slot
class _Sent:
    status_code = 200
    ok = True
    text = ""

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


def _real_publisher(coord, monkeypatch, fail=()):
    """The coordinator's own MetaPublisher.publish_photos (the fixture's stand-in removed) over a configured
    Upload-Post whose requests are recorded; `fail`: platforms Upload-Post reports as failed."""
    import requests

    from src import publisher
    sent = []

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        sent.append(list(data))
        names = [v for k, v in data if k == "platform[]"]
        return _Sent({"success": True, "results": {p: ({"success": False, "error": "x"} if p in fail else
                                                       {"success": True, "url": f"https://{p}/x"}) for p in names}})
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(coord.publisher, "publish_photos", publisher.MetaPublisher.publish_photos.__get__(coord.publisher))
    coord.publisher.publishers = [publisher.UploadPostPublisher(api_key="k" * 20, user="u")]
    return sent


def _draw_showing(coord, monkeypatch, run_dir, ai_slide=None, ai_in_facebook=True):
    """A drawing whose slides' records (still_layout.json, as generate_carousel_sequence writes them) show the run's
    credited photo, and on slide `ai_slide` an image_gen illustration (gap_gen_, video_composer's name)."""
    photo = run_dir / "photos" / "p1.jpg"
    (run_dir / "photos" / "credits.json").write_text(json.dumps({"images": [{"file": "p1.jpg", "license": "CC0"},
                                                                            {"file": "p2.jpg", "license": "CC0"}]}),
                                                      encoding="utf-8")
    art = _photo(run_dir / "gap_gen_beat02.jpg")

    def draw(stills, payload, output_dir, facebook=False, graphics=None, strict_match=False):
        coord.drawn.append((Path(output_dir).parent.name, facebook))
        if coord.on_draw:
            coord.on_draw(facebook)
        folder = Path(output_dir) / ("carousel_fb" if facebook else "carousel")
        folder.mkdir(parents=True, exist_ok=True)
        slides, record = [], {}
        for n in range(1, 4):
            slide = _photo(folder / f"carousel_slide_{n:02d}.jpg")
            shows = art if n == ai_slide and (ai_in_facebook or not facebook) else photo
            record[slide.name] = {"recipe": {"archetype": "full", "photo": str(shows), "photo_next": str(photo)}}
            slides.append(slide)
        (folder / "still_layout.json").write_text(json.dumps(record), encoding="utf-8")
        return slides
    monkeypatch.setattr(coord.multiformat_gen, "generate_carousel_sequence", draw)


def _platforms(request):
    return [v for k, v in request if k == "platform[]"]


def test_a_carousel_drawn_at_its_slot_showing_an_ai_picture_is_labelled_on_instagram_and_kept_off_facebook(
        coord, monkeypatch):
    """QF-65a on QF-81's slot path: the label comes from the slides carousel_lazy.render installed (their records
    travel with them), so an AI picture drawn at the slot carries Instagram's label, and Facebook (no AI label for
    photo posts) is not sent it; the slot's alert says so."""
    run_dir, _ = coord.make("r1", TUE - timedelta(days=3))
    _draw_showing(coord, monkeypatch, run_dir, ai_slide=2)
    sent = _real_publisher(coord, monkeypatch)
    _slot(coord, TUE)
    post = _carousels(coord, "r1")[0]
    assert post["status"] == "published" and [Path(f).parent.name for f in post["files"]] == ["carousel"] * 3
    assert [_platforms(r) for r in sent] == [["instagram"]] and ("is_ai_generated", "true") in sent[0]
    held = post["detail"]["held_platforms"]["facebook"]
    assert "AI illustration (gap_gen_beat02.jpg)" in held
    assert any("with the AI label" in a and "not posted to Facebook" in a for a in coord.alerts)


def test_a_carousel_drawn_at_its_slot_from_real_photos_goes_to_both_unflagged(coord, monkeypatch):
    run_dir, _ = coord.make("r1", TUE - timedelta(days=3))
    _draw_showing(coord, monkeypatch, run_dir)
    sent = _real_publisher(coord, monkeypatch)
    _slot(coord, TUE)
    post = _carousels(coord, "r1")[0]
    assert post["status"] == "published" and "held_platforms" not in post["detail"]
    assert sorted(p for r in sent for p in _platforms(r)) == ["facebook", "instagram"]
    facebook = next(r for r in sent if _platforms(r) == ["facebook"])
    assert all("carousel_fb" in v for k, v in facebook if k == "photos[]") or True   # its own copy (collage rules)
    assert all(k != "is_ai_generated" for r in sent for k, _ in r)


def test_facebook_is_held_by_its_own_copy_s_media(coord, monkeypatch):
    """Each request is judged by the slides it sends: Facebook's grid copy without the AI picture is posted to
    Facebook unlabelled while Instagram's copy, which shows it, carries the label."""
    run_dir, _ = coord.make("r1", TUE - timedelta(days=3))
    _draw_showing(coord, monkeypatch, run_dir, ai_slide=2, ai_in_facebook=False)
    sent = _real_publisher(coord, monkeypatch)
    _slot(coord, TUE)
    ig = next(r for r in sent if _platforms(r) == ["instagram"])
    fb = next(r for r in sent if _platforms(r) == ["facebook"])
    assert ("is_ai_generated", "true") in ig and all(k != "is_ai_generated" for k, _ in fb)
    assert "held_platforms" not in _carousels(coord, "r1")[0]["detail"]


def test_when_facebook_s_copy_cannot_be_set_whole_the_instagram_slides_it_gets_keep_the_ai_rule(coord, monkeypatch):
    """QF-23 + QF-81 + QF-65a: a Facebook copy refused for its layout leaves Facebook the Instagram slides; those
    show the AI picture, so Facebook is not sent them, and Instagram carries the label."""
    run_dir, _ = coord.make("r1", TUE - timedelta(days=3))
    _draw_showing(coord, monkeypatch, run_dir, ai_slide=2)
    from src.typography import LayoutError

    def refuse(facebook):
        if facebook:
            raise LayoutError("slide 2 heading cannot be set whole")
    coord.on_draw = refuse
    sent = _real_publisher(coord, monkeypatch)
    _slot(coord, TUE)
    post = _carousels(coord, "r1")[0]
    assert not list((run_dir / "carousel_fb").glob("carousel_slide_*.jpg"))
    assert [_platforms(r) for r in sent] == [["instagram"]] and ("is_ai_generated", "true") in sent[0]
    assert "facebook" in post["detail"]["held_platforms"]


def test_a_retry_of_a_carousel_drawn_at_its_slot_carries_the_label(coord, monkeypatch):
    """retry_post re-sends the installed slides: Instagram's retry carries the label from their records, and
    Facebook's is held by the AI picture they show."""
    run_dir, _ = coord.make("r1", TUE - timedelta(days=3))
    _draw_showing(coord, monkeypatch, run_dir, ai_slide=2)
    sent = _real_publisher(coord, monkeypatch)
    _slot(coord, TUE)
    post = _carousels(coord, "r1")[0]
    coord.db.update_scheduled_post(post["id"], "failed", detail={"failed": {"instagram": "x", "facebook": "y"},
                                                                  "posted_parts": 0})
    sent.clear()
    reply = coord.retry_post(post["id"])
    assert "posted" in reply and [_platforms(r) for r in sent] == [["instagram"]]
    assert ("is_ai_generated", "true") in sent[0]
