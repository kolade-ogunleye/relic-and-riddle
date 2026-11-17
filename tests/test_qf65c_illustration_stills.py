"""QF-65c: no illustration in Stories or carousels; full illustration provenance.

The rest of QF-65 after QF-65a/b (review 2026-11-05, row QF-65; ruling Q6 of FREEZE-RESUME-rulings):
- an AI-made still (an image_gen illustration gap_gen_*, a copy whose lineage reaches one, anything a record calls
  AI-generated) is never a Story frame or a carousel slide, by any path: the Story's hero fallback, image_review's
  only_strong fallback, scene_planner.strong_shot, the carousel's kept photo match and QF-81's carousel plan;
- a Story or carousel left with no real photo is not made and is held with a true reason (review card, the carousel
  hold), never padded with an illustration;
- the illustration's lineage record carries the prompt, raw_sha256 (== sha256 of the kept best raw), the chosen
  candidate's index and the judges' verdicts.

No network and no model: image generation and the vision judge are fakes.
"""

import concurrent.futures
import io
import json
from datetime import timedelta
from pathlib import Path

from PIL import Image, ImageDraw

from src import (
    carousel_lazy,
    credits,
    image_gen,
    image_quarantine,
    image_review,
    lineage,
    scene_planner,
)
from src import multi_format as mf

STORY = {
    "title": "The Eye of the Sahara", "narration_script": "Thirty miles across, and nobody built it.",
    "first_comment": "?", "hashtags": ["#x"], "caption": "c",
    "story_slides": [
        {"kicker": "VISIBLE FROM SPACE", "line1": "A BULLSEYE", "line2": "IN THE SAND", "body": "Thirty miles across."},
        {"kicker": "THE REVEAL", "line1": "NOT A CRATER", "line2": "A DOME", "body": "Erosion carved the rings."},
    ],
}
CAROUSEL = {"title": "T", "poll": {}, "carousel_slides": [
    {"heading": "The dome", "body": "Built in concrete."}, {"heading": "The mix", "body": "Lime clasts."},
    {"heading": "Your view", "body": "Which mattered more?"}]}


def _photo(path: Path, colour=(90, 80, 70), size=(1200, 1500)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(path)
    return path


def _rotate(monkeypatch, seen=None):
    """The photo match without a model: the stills in turn (what it was given is kept in `seen`)."""
    def match(self, cards, stills, strict=False):
        if seen is not None:
            seen.append(list(stills))
        return [stills[i % len(stills)] for i in range(len(cards))]
    monkeypatch.setattr(mf.MultiFormatGenerator, "_match_photos", match)


def _shown(folder: Path) -> list[str]:
    """The photo names the stills in `folder` were drawn on (their still_layout records)."""
    from src import still_layout
    out = []
    for still in sorted(folder.glob("*.jpg")):
        recipe = (still_layout.load(still) or {}).get("recipe") or {}
        out += [Path(str(recipe[k])).name for k in ("photo", "photo_next") if recipe.get(k)]
    return out


def _copy_of(art: Path, name: str) -> Path:
    """A prepared copy of `art` under another name, its lineage naming `art` (an upscale, a crop)."""
    out = _photo(art.parent / name, colour=(30, 30, 30))
    lineage.record_derived(out, [art], "upscale", {})
    return out


def _try(fn, *a, **k):
    try:
        return fn(*a, **k), None
    except Exception as e:  # noqa: BLE001 - the hold is the expected outcome; its kind is checked by the caller
        return None, e


# ------------------------------------------------------------------ Stories
def test_a_story_sequence_from_an_illustration_draws_no_illustration_frame(tmp_path, monkeypatch):
    """The spec's acceptance: a Story sequence from [gap_gen_x.jpg] draws no illustration frame."""
    _rotate(monkeypatch)
    art = _photo(tmp_path / "gap_gen_x.jpg", colour=(20, 20, 20))
    slides, err = _try(mf.MultiFormatGenerator().generate_story_sequence, art, STORY, tmp_path, scene_images=[art])
    assert not slides and not list((tmp_path / "stories").glob("story_*.jpg"))
    assert "gap_gen_x.jpg" not in _shown(tmp_path / "stories")
    assert type(err).__name__ == "NoRealPhotos" and "AI" in str(err) and "gap_gen_x.jpg" in str(err)


def test_a_story_never_falls_back_to_an_illustrated_hero_or_draws_a_copy_of_one(tmp_path, monkeypatch):
    seen = []
    _rotate(monkeypatch, seen)
    art = _photo(tmp_path / "run" / "gap_gen_beat03.png", colour=(20, 20, 20))
    copy = _copy_of(art, "prepared_beat03.jpg")          # its name no longer says gap_gen_
    real = _photo(tmp_path / "pool" / "01_commons.jpg")
    slides = mf.MultiFormatGenerator().generate_story_sequence(art, STORY, tmp_path, scene_images=[copy, real, art])
    assert len(slides) == 2
    assert set(_shown(tmp_path / "stories")) == {"01_commons.jpg"}
    assert all(Path(p).name == "01_commons.jpg" for stills in seen for p in stills)


def test_a_real_hero_still_serves_a_story_whose_scene_stills_are_all_illustrations(tmp_path, monkeypatch):
    _rotate(monkeypatch)
    art = _photo(tmp_path / "gap_gen_x.jpg", colour=(20, 20, 20))
    hero = _photo(tmp_path / "hero.jpg")
    slides = mf.MultiFormatGenerator().generate_story_sequence(hero, STORY, tmp_path, scene_images=[art])
    assert len(slides) == 2 and set(_shown(tmp_path / "stories")) == {"hero.jpg"}


# ------------------------------------------------------------------ the fallbacks
def test_only_strong_never_falls_back_to_an_illustration(tmp_path):
    art = _photo(tmp_path / "gap_gen_a.jpg")
    copy = _copy_of(art, "a_up.jpg")
    context = _photo(tmp_path / "context.jpg")
    assert image_review.only_strong([art, copy, context], {"nothing_strong"}) == [context]   # the fallback
    assert image_review.only_strong([art, copy, context], None) == [context]                # nothing known
    assert image_review.only_strong([art, copy], {"gap_gen_a"}) == []                        # never "strong"


def test_a_copy_whose_lineage_record_cannot_be_read_is_never_taken_as_real(tmp_path):
    """Review 1 note: a corrupt copy record traced "unknown" and passed as a real photo; it now fails closed."""
    from src.ai_label import is_ai_picture
    art = _photo(tmp_path / "gap_gen_a.jpg")
    copy = _copy_of(art, "a_up.jpg")
    lineage.sidecar(copy).write_text("{not json", encoding="utf-8")
    assert is_ai_picture(copy) is True
    assert image_review.only_strong([copy, _photo(tmp_path / "context.jpg")], None) == [tmp_path / "context.jpg"]
    assert is_ai_picture(_photo(tmp_path / "plain.jpg")) is False   # no record at all: not AI here (QF-65a's label)


def test_an_illustration_is_never_a_strong_shot(tmp_path):
    art = _photo(tmp_path / "gap_gen_a.jpg")
    assert scene_planner.strong_shot(art) is False
    assert scene_planner.strong_shot(_copy_of(art, "a_up.jpg")) is False
    assert scene_planner.strong_shot(_photo(tmp_path / "mine.jpg")) is True    # a photo you supplied still is one


# ------------------------------------------------------------------ carousels
def test_a_carousel_from_illustrations_alone_is_not_drawn(tmp_path, monkeypatch):
    _rotate(monkeypatch)
    art = _photo(tmp_path / "gap_gen_x.jpg", colour=(20, 20, 20))
    slides, err = _try(mf.MultiFormatGenerator().generate_carousel_sequence, [art], CAROUSEL, tmp_path)
    assert not slides and "gap_gen_x.jpg" not in _shown(tmp_path / "carousel")
    assert type(err).__name__ == "NoRealPhotos" and "AI" in str(err)


def test_a_carousel_draws_its_slides_on_real_photos_only_and_never_reuses_a_match_showing_one(tmp_path, monkeypatch):
    seen = []
    _rotate(monkeypatch, seen)
    art = _photo(tmp_path / "gap_gen_x.jpg", colour=(20, 20, 20))
    real = [_photo(tmp_path / f"0{i}.jpg", colour=(40 * i, 60, 90)) for i in range(2)]
    # an earlier match (carousel_photos.json) that put the illustration on a slide
    key = {"cards": [f"{c['heading']}|{c['body']}" for c in CAROUSEL["carousel_slides"]],
           "stills": [str(p) for p in real]}
    (tmp_path / mf.CAROUSEL_PHOTOS).write_text(json.dumps({"key": key, "photos": [str(art), str(real[0]),
                                                                                  str(real[1])]}), encoding="utf-8")
    slides = mf.MultiFormatGenerator().generate_carousel_sequence([art, *real], CAROUSEL, tmp_path)
    assert len(slides) == 3
    assert "gap_gen_x.jpg" not in _shown(tmp_path / "carousel")
    assert seen and all(Path(p).name != "gap_gen_x.jpg" for stills in seen for p in stills)


def test_the_carousel_plan_records_no_illustration_and_one_without_a_real_photo_is_held_truly(tmp_path):
    art = _photo(tmp_path / "run" / "gap_gen_x.jpg", colour=(20, 20, 20))
    real = _photo(tmp_path / "pool" / "01_commons.jpg")
    run = tmp_path / "run"
    carousel_lazy.plan(run, [art, real], CAROUSEL)
    plan = json.loads((run / carousel_lazy.PLAN).read_text(encoding="utf-8"))
    assert [Path(e["path"]).name for e in plan["stills"]] == ["01_commons.jpg"]
    assert carousel_lazy.plan_problem(run) is None
    carousel_lazy.plan(run, [art], CAROUSEL)
    why = carousel_lazy.plan_problem(run) or ""
    assert "no real photo" in why and "gap_gen_x.jpg" in why and "AI" in why


def test_a_plan_made_before_the_filter_draws_from_its_real_photos_only(tmp_path):
    art = _photo(tmp_path / "run" / "gap_gen_x.jpg", colour=(20, 20, 20))
    real = _photo(tmp_path / "pool" / "01_commons.jpg")
    run = tmp_path / "run"
    carousel_lazy.plan(run, [real], CAROUSEL)
    plan = json.loads((run / carousel_lazy.PLAN).read_text(encoding="utf-8"))
    old = dict(plan, stills=[{"path": str(art), "sha256": credits.sha256_file(art)}, *plan["stills"]])
    (run / carousel_lazy.PLAN).write_text(json.dumps(old), encoding="utf-8")
    assert [p.name for p in carousel_lazy._checked_stills(run, CAROUSEL)] == ["01_commons.jpg"]
    only_art = dict(plan, stills=[{"path": str(art), "sha256": credits.sha256_file(art)}])
    (run / carousel_lazy.PLAN).write_text(json.dumps(only_art), encoding="utf-8")
    record = carousel_lazy.draw(run, CAROUSEL, generator=None, auditor=None)
    assert "no real photo" in record["held"] and "gap_gen_x.jpg" in record["held"]


# ------------------------------------------------------------------ the hold, on the review card
def test_a_story_with_no_real_photo_is_held_on_the_review_card_with_its_reason(tmp_path):
    import main
    from src.telegram_bot import TelegramApprovalBot
    no_real = getattr(mf, "NoRealPhotos", None)
    assert no_real is not None, "no hold for a Story or carousel without a real photo"
    (tmp_path / "stories").mkdir()
    (tmp_path / "stories" / "story_01_hook.jpg").write_bytes(b"an earlier render's frame")
    fut = concurrent.futures.Future()
    fut.set_exception(no_real("no real photo to draw the Story frames on: 1 AI-made picture(s) left out "
                              "(gap_gen_x.jpg); an AI picture is never a Story frame or a carousel slide"))
    payload = {"title": "The Richat Structure", "narration_script": "Sediment.", "first_comment": "Why?"}
    assert main.hold_layout_error(fut, tmp_path, "stories", payload) == []
    assert not list((tmp_path / "stories").glob("story_*.jpg"))      # nothing left for the posting glob
    (hold,) = payload["photo_holds"]
    assert hold["deck"] == "stories" and "layout_holds" not in payload
    text = TelegramApprovalBot.format_card_text(payload, "run_x")
    assert "Held, no real photo to draw on: 1" in text and "gap_gen_x.jpg" in text
    assert "would not fit whole" not in text


def test_a_story_hold_counts_each_ai_picture_once(tmp_path, monkeypatch):
    """Review 1: the hero is normally the first scene still, so it was counted twice ("2 AI-made picture(s) left
    out (gap_gen_x.jpg, gap_gen_x.jpg)" for one illustration); a prepared copy of it is the same picture too."""
    _rotate(monkeypatch)
    gen = mf.MultiFormatGenerator()
    art = _photo(tmp_path / "run" / "gap_gen_x.jpg", colour=(20, 20, 20))
    _, err = _try(gen.generate_story_sequence, art, STORY, tmp_path, scene_images=[art])
    assert "1 AI-made picture(s) left out (gap_gen_x.jpg);" in str(err)
    hero = _copy_of(art, "hero_prepared.jpg")              # the hero as the video prepared it, from the same art
    _, err = _try(gen.generate_story_sequence, hero, STORY, tmp_path, scene_images=[art, hero])
    assert "1 AI-made picture(s) left out (gap_gen_x.jpg);" in str(err)
    other = _photo(tmp_path / "run" / "gap_gen_y.jpg", colour=(25, 25, 25))
    _, err = _try(gen.generate_story_sequence, art, STORY, tmp_path, scene_images=[art, other])
    assert "2 AI-made picture(s) left out (gap_gen_x.jpg, gap_gen_y.jpg);" in str(err)


# ------------------------------------------------------------------ a carousel with no real photo, at make time
def _make_run(db, tmp_path, monkeypatch, payload, scene_media, hero):
    """PipelineCoordinator._render_approve_publish as QF-81's first test drives it (no video, no model)."""
    import main
    from main import PipelineCoordinator
    from src.models import QAReport
    coord = PipelineCoordinator(db)
    monkeypatch.setattr(coord, "_verify_final_copy", lambda *a: None)
    monkeypatch.setattr("src.scene_planner.reel_photo_coverage", lambda *a: {"ok": True, "problems": []})
    monkeypatch.setattr(main, "ensure_all", lambda paths: (paths, 0))
    monkeypatch.setattr(main, "ensure_resolution", lambda p: p)
    monkeypatch.setattr(main, "load_credits", lambda d: {"images": []})
    monkeypatch.setattr(main, "llm_available", lambda: True)
    monkeypatch.setattr(main.beat_images, "plan_queries", lambda beats, topic: None)
    monkeypatch.setattr(main, "plan_scenes", lambda beats, media, feedback="": ([hero], [3.0], "n"))
    monkeypatch.setattr(main, "segment_beats", lambda w, d: [{"start": 0, "end": 3, "text": "A door."}])
    monkeypatch.setattr(main, "render_parallax", lambda *a: None)
    monkeypatch.setattr(main, "review_render", lambda *a: QAReport(summary="", issues=[]))
    monkeypatch.setattr(coord.visual_engine, "generate_thumbnails", lambda *a, **k: (tmp_path / "c.jpg", tmp_path / "g.jpg"))
    monkeypatch.setattr(main.graphics, "make_all", lambda *a, **k: [])
    monkeypatch.setattr(coord.video_composer, "compose_video", lambda *a, **k: None)
    monkeypatch.setattr(coord.video_composer, "make_review_proxy", lambda m, o: o)
    monkeypatch.setattr(coord, "publish_run", lambda run_id, dry_run, previous_status: {"status": "simulated"})
    # a run with a hold is not auto-approved: its card goes for review (never sent here) and nobody decides
    monkeypatch.setattr(coord.telegram_bot, "send_approval_request", lambda *a, **k: None)
    monkeypatch.setattr(coord.telegram_bot, "wait_for_decision", lambda *a, **k: "timeout")
    db.create_run("r9", "01_topic")
    run_dir = tmp_path / "run"
    coord._render_approve_publish({"id": "01_topic"}, "r9", run_dir, payload, scene_media, hero,
                                  tmp_path / "v.mp3", tmp_path / "bed.wav", [], 3.0, True, True, None)
    return run_dir


def test_a_carousel_with_no_real_photo_is_held_on_the_review_card_when_the_run_is_made(db, tmp_path, monkeypatch,
                                                                                         curated_payload):
    """Review 1, blocker 1: every candidate still an illustration (the hero is the first one). The Story and the
    carousel are both held at make time, each with its true reason and its AI picture counted once, and the card's
    "Also posted" list no longer says the carousel will be drawn and posted."""
    from src import publishable
    from src.telegram_bot import TelegramApprovalBot, _also_posted
    art = _photo(tmp_path / "run" / "gap_gen_beat02.png", colour=(20, 20, 20))
    payload = dict(curated_payload, caption="Story.", topic_id="01_topic")
    run_dir = _make_run(db, tmp_path, monkeypatch, payload, [art], art)
    holds = {h["deck"]: h["error"] for h in payload.get("photo_holds") or []}
    assert set(holds) == {"stories", "carousel"}, holds
    for why in holds.values():
        assert "no real photo" in why and "1 AI-made picture(s) left out (gap_gen_beat02.png);" in why
    assert holds["carousel"] == carousel_lazy.plan_problem(run_dir)   # the reason its slot or a Redraw would give
    record = publishable.card_record(payload, db, run_dir=run_dir, topic_id="01_topic")
    assert record.get("carousel_held") == holds["carousel"]
    also = _also_posted(record, record["copy"])
    assert not any("drawn from this run's photos" in item for item in also)
    assert not any(item.startswith("Facebook carousel caption") for item in also)
    (held,) = [item for item in also if item.startswith("Carousel (Instagram and Facebook)")]
    assert "not posted, held when you approve" in held and "gap_gen_beat02.png" in held
    text = TelegramApprovalBot.format_card_text({**payload, "_publish_copy": record}, "r9")
    assert "Held, no real photo to draw on: 2" in text and "Instagram carousel: " in text
    assert "Preview carousel" not in text


def test_a_carousel_with_no_real_photo_is_booked_held_and_said_at_once(db, tmp_path, monkeypatch, curated_payload):
    """Approve books it held (final, as its slot would), so the notifications panel says so now, not 48 h later at
    its slot; it is never planned into a slot, and the UI-01g expiry leaves it as it is (never "expired")."""
    from datetime import UTC, datetime

    from main import PipelineCoordinator
    from src import config
    from src.posting_view import carousel_outlook
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", True)
    coord = PipelineCoordinator(db, schedule_posts=True)
    alerts = []
    coord._alert = lambda text, buttons=None, **k: alerts.append(text)
    run_dir = tmp_path / "run"
    art = _photo(run_dir / "gap_gen_beat02.png", colour=(20, 20, 20))
    carousel_lazy.plan(run_dir, [art], dict(curated_payload))
    (run_dir / "m.mp4").write_bytes(b"0")
    db.create_run("r1", "01_topic")
    db.update_run("r1", "pending_approval", script_payload=dict(curated_payload, topic_id="01_topic"),
                  master_video_path=run_dir / "m.mp4")
    lines = coord._book_followers("r1", run_dir, datetime.now(UTC))
    (post,) = [p for p in db.list_scheduled_posts(("pending", "waiting", "failed", "expired")) if p["kind"] == "carousel"]
    assert post["status"] == "failed" and post["detail"].get("final") and post["detail"].get("held")
    assert post["detail"]["error"].startswith("held when it was booked: its plan records no real photo")
    assert any(line.startswith("🖼 Carousel: held, it won't post") for line in lines)
    assert any("Carousel not posted" in a and "gap_gen_beat02.png" in a and "will not post" in a for a in alerts)
    assert post["id"] not in carousel_outlook(db, datetime.now(UTC), may_post=coord._may_post)["plan"]
    coord._expire_held_carousels(datetime.now(UTC) + timedelta(days=30))
    assert [p["status"] for p in db.list_scheduled_posts(("failed", "expired")) if p["id"] == post["id"]] == ["failed"]


# ------------------------------------------------------------------ provenance
GEARS ="The bronze gears of the mechanism turned inside a wooden case."
SHEET = {"facts": [
    {"id": "F1", "statement": ("The mechanism had bronze gears housed in a wooden case about 34 cm wide. "
                               "The mechanism held its bronze gears in a wooden case."),
     "kind": "other", "disputed": False, "evidence": [{"url": "https://www.nature.com/x", "quote": "bronze gears"}]}]}


def _engraving(shade: int) -> bytes:
    im = Image.new("RGB", (1080, 1920), (0, 0, 0))
    d = ImageDraw.Draw(im)
    for x in range(0, 1080, 10):
        d.line([(x, 0), (x, 1920)], fill=(245, 245, shade), width=2)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_the_illustration_record_carries_prompt_raw_hash_candidate_index_and_judge_verdicts(tmp_path, monkeypatch):
    from src import free_llm
    monkeypatch.setattr(image_gen, "is_available", lambda: True)
    monkeypatch.setattr(image_gen, "checkers_ready", lambda: (True, ""))
    monkeypatch.setattr(image_quarantine, "_vision_ready", lambda: True)
    prompts, shades = [], iter([200, 210, 220, 230])
    monkeypatch.setattr(image_gen, "generate_image", lambda p, aspect_ratio="9:16", timeout=None:
                        prompts.append(p) or _engraving(next(shades)))
    asked = []
    monkeypatch.setattr(free_llm, "structured", lambda system, prompt, schema, **k:
                        asked.append(1) or schema(reason=f"judge call {len(asked)}"))
    scores = iter([(False, ["text in the image"], 0.0), (True, [], 5.0), (True, [], 9.0), (True, [], 1.0)])

    def score(raw, final, spec, size, others=(), references=()):
        image_quarantine.vision_rubric(Path(final), spec)   # the judge looks at every candidate
        return next(scores)
    monkeypatch.setattr(image_quarantine, "score_candidate", score)
    spec, _ = image_gen.spec_from_facts(GEARS, {}, SHEET)
    art, info = image_gen.illustrate_beat(GEARS, spec, tmp_path)
    assert art is not None and info["calls"] == 4
    rec = json.loads(lineage.sidecar(art).read_text(encoding="utf-8"))["settings"]["inputs"]
    best = tmp_path / image_gen.CANDIDATE_DIR / f"{info['key']}_best.png"
    assert rec.get("prompt") == prompts[2] and rec["prompt"]
    assert rec.get("raw_sha256") == credits.sha256_file(best)
    assert rec["raw_sha256"] == credits.sha256_file(tmp_path / image_gen.CANDIDATE_DIR / f"{info['key']}_2.png")
    assert rec.get("candidate_index") == 2
    verdicts = rec.get("judge_verdicts") or []
    descriptor = [v for v in verdicts if v.get("judge") == "descriptor"]
    assert [v["verdict"]["reason"] for v in descriptor] == ["judge call 3"]   # the chosen candidate's own answer
    assert {"judge": "rubric", "passed": True, "reasons": [], "score": 9.0} in verdicts
    assert [c["candidate_index"] for c in rec.get("candidates") or []] == [0, 1, 2, 3]


def test_a_saved_illustration_without_its_provenance_is_not_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(image_gen, "is_available", lambda: True)
    monkeypatch.setattr(image_gen, "checkers_ready", lambda: (True, ""))
    calls = []
    monkeypatch.setattr(image_gen, "generate_image", lambda p, aspect_ratio="9:16", timeout=None:
                        calls.append(p) or _engraving(240))
    monkeypatch.setattr(image_quarantine, "score_candidate", lambda *a, **k: (True, [], 1.0))
    spec, _ = image_gen.spec_from_facts(GEARS, {}, SHEET)
    art, _ = image_gen.illustrate_beat(GEARS, spec, tmp_path, candidates=1)
    assert art is not None and len(calls) == 1
    _, info2 = image_gen.illustrate_beat(GEARS, spec, tmp_path, candidates=1)
    assert info2["reused"] and len(calls) == 1                     # whole provenance: reused, after the rubric
    rec = json.loads(lineage.sidecar(art).read_text(encoding="utf-8"))
    for field in ("prompt", "raw_sha256", "candidate_index", "judge_verdicts"):
        stripped = dict(rec, settings=dict(rec["settings"], inputs={k: v for k, v in rec["settings"]["inputs"].items()
                                                                   if k != field}))
        lineage.sidecar(art).write_text(json.dumps(stripped), encoding="utf-8")
        _, info3 = image_gen.illustrate_beat(GEARS, spec, tmp_path, candidates=1)
        assert not info3["reused"], field
