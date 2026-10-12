import pytest

from main import PipelineCoordinator


@pytest.fixture
def rendered_run(db, tmp_path, curated_payload):
    video = tmp_path / "m.mp4"
    video.write_bytes(b"0")
    cover = tmp_path / "c.jpg"
    cover.write_bytes(b"0")
    curated_payload["topic_id"] = "01_topic"
    curated_payload["human_reviewed"] = True
    db.create_run("r1", "01_topic")
    db.update_run("r1", "pending_approval", script_payload=curated_payload, master_video_path=video,
                  thumbnail_cover_path=cover, thumbnail_grid_path=cover)
    return PipelineCoordinator(db)


@pytest.mark.parametrize("pub_status, run_status, topic_status, count", [
    ("published", "published", "published", 1),
    ("simulated", "simulated", "queued", 0),
    ("failed", "failed", "failed", 0),
    ("unknown", "publish_unknown", "publish_unknown", 0),
])
def test_only_confirmed_publishes_count(rendered_run, monkeypatch, pub_status, run_status, topic_status, count):
    monkeypatch.setattr(rendered_run.publisher, "publish_reel", lambda env, dry_run=False: {"status": pub_status})
    rendered_run.publish_run("r1")
    db = rendered_run.db
    assert db.get_run("r1")["status"] == run_status
    topic = db.get_topic_by_id("01_topic")
    assert (topic["status"], topic["times_published"]) == (topic_status, count)


def test_unknown_publish_topic_is_not_picked_again(rendered_run, monkeypatch):
    monkeypatch.setattr(rendered_run.publisher, "publish_reel", lambda env, dry_run=False: {"status": "unknown"})
    rendered_run.publish_run("r1")
    assert rendered_run.db.get_next_topic()["id"] != "01_topic"


def test_unreviewed_curated_run_cannot_be_published(rendered_run):
    run = rendered_run.db.get_run("r1")
    payload = dict(run["script_payload"], human_reviewed=False)
    rendered_run.db.update_run("r1", "pending_approval", script_payload=payload)
    with pytest.raises(ValueError, match="unreviewed"):
        rendered_run.publish_run("r1")


def test_failed_fact_check_is_revised_then_fails_closed(db, monkeypatch, tmp_path, curated_payload):
    import main
    from src.visual_engine import VisualEngine
    coord = PipelineCoordinator(db)
    monkeypatch.setattr(coord.visual_engine, "get_scene_sequence", lambda t: [tmp_path / "a.jpg"])
    monkeypatch.setattr(VisualEngine, "hero_image", staticmethod(lambda s: s[0]))
    notes_seen = []

    def gen(topic, revision_notes="", previous=None):
        notes_seen.append(revision_notes)
        return dict(curated_payload, origin="claude", topic_id=topic["id"])

    monkeypatch.setattr(coord.script_engine, "generate_script", gen)
    monkeypatch.setattr(coord.script_engine, "verify_claims",
                        lambda p, topic=None, allow_unreviewed_curated=False: (False, p, f"bad claim {len(notes_seen)}"))
    db.create_run("r9", "01_topic")
    with pytest.raises(RuntimeError, match="guardrail"):
        coord._produce_and_publish({"id": "01_topic"}, "r9", tmp_path, True, True, "queued")
    assert notes_seen == ["", "bad claim 1", "bad claim 2"]  # original + MAX_FACT_REVISIONS rewrites


def test_resume_reuses_passed_script_and_voiceover(db, monkeypatch, tmp_path, curated_payload):
    import json
    import main
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)
    run_dir = tmp_path / "r5"
    run_dir.mkdir()
    (run_dir / "script_metadata.json").write_text(json.dumps(dict(curated_payload, guardrail_passed=True)), encoding="utf-8")
    (run_dir / "voiceover.mp3").write_bytes(b"x")
    (run_dir / "words.json").write_text("[]", encoding="utf-8")
    db.create_run("r5", "01_topic")
    db.update_run("r5", "failed", error="RenderError")
    coord = PipelineCoordinator(db)
    monkeypatch.setattr(coord.visual_engine, "get_scene_sequence", lambda t: [tmp_path / "a.jpg"])
    monkeypatch.setattr(main.VisualEngine if hasattr(main, "VisualEngine") else type(coord.visual_engine), "hero_image", staticmethod(lambda s: s[0]))
    for name in ("generate_script", "verify_claims"):
        monkeypatch.setattr(coord.script_engine, name, lambda *a, **k: pytest.fail("script was regenerated"))
    monkeypatch.setattr(coord.audio_engine, "generate_narration", lambda *a, **k: pytest.fail("voice was regenerated"))
    monkeypatch.setattr(coord.audio_engine, "get_duration", lambda p: 44.0)
    monkeypatch.setattr(coord.audio_engine, "generate_ambient_bed", lambda *a, **k: None)
    seen = {}
    monkeypatch.setattr(coord, "_render_approve_publish", lambda *a: seen.setdefault("ok", True) and {"status": "simulated"})
    assert coord.resume_run("r5", dry_run=True)["status"] == "simulated"
    assert seen["ok"]


def test_resume_refuses_published_run(rendered_run):
    rendered_run.db.update_run("r1", "published")
    with pytest.raises(ValueError, match="can't be resumed"):
        rendered_run.resume_run("r1")


def test_run_label_and_short_keys(rendered_run):
    db = rendered_run.db
    label = db.run_label("r1")
    assert "City Hidden Behind One Basement Wall" in label and "r1" not in label
    key = db.run_key("r1")
    assert key.isdigit() and db.resolve_run(key) == "r1" and db.resolve_run("r1") == "r1"


def test_unknown_publish_buttons(rendered_run, monkeypatch):
    monkeypatch.setattr(rendered_run.publisher, "publish_reel", lambda env, dry_run=False: {"status": "unknown"})
    rendered_run.publish_run("r1")
    assert "Recorded as published" in rendered_run.confirm_posted("r1")
    t = rendered_run.db.get_topic_by_id("01_topic")
    assert (t["status"], t["times_published"]) == ("published", 1)
    with pytest.raises(ValueError):
        rendered_run.confirm_not_posted("r1")  # already answered


def test_not_posted_requeues(rendered_run, monkeypatch):
    monkeypatch.setattr(rendered_run.publisher, "publish_reel", lambda env, dry_run=False: {"status": "unknown"})
    rendered_run.publish_run("r1")
    rendered_run.confirm_not_posted("r1")
    assert rendered_run.db.get_topic_by_id("01_topic")["status"] == "queued"
    assert rendered_run.db.get_run("r1")["status"] == "failed"


def test_approval_button_uses_short_key(db, monkeypatch):
    from src.telegram_bot import TelegramApprovalBot
    db.create_run("run_12_flannan_isle_lighthouse_disappearance_1791781280", "01_topic")
    rid = "run_12_flannan_isle_lighthouse_disappearance_1791781280"
    db.record_approval(rid, "01_topic")
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    monkeypatch.setattr(bot, "_api", lambda method, **kw: {})
    key = db.run_key(rid)
    assert len(f"approve:{key}".encode()) <= 64
    cq = {"id": "q", "data": f"approve:{key}", "from": {"id": 555}, "message": {"chat": {"id": 555}, "message_id": 1}}
    assert bot._handle_callback(cq) == "approved"
    assert db.get_approval(rid)["decision"] == "approved"


def test_render_adds_beat_images_and_replans_once_on_picture_mismatch(db, monkeypatch, tmp_path, curated_payload):
    import main
    from src.models import QAIssue, QAReport
    coord = PipelineCoordinator(db)
    topic_img, beat_img = tmp_path / "t.jpg", tmp_path / "b.jpg"
    monkeypatch.setattr(main, "ensure_all", lambda paths: (paths, 0))
    monkeypatch.setattr(main, "ensure_resolution", lambda p: p)
    monkeypatch.setattr(main, "load_credits", lambda d: {"images": []})
    monkeypatch.setattr(main.beat_images, "source", lambda beats, topic, d: (
        [beat_img], [{"file": str(beat_img), "beat": 1, "artist": "Bob", "license": "CC BY 4.0", "attribution_required": True}]))
    plans = []

    def plan(beats, media, feedback=""):
        plans.append((list(media), feedback))
        return [beat_img], [3.0], "n"
    monkeypatch.setattr(main, "plan_scenes", plan)
    monkeypatch.setattr(main, "segment_beats", lambda w, d: [{"start": 0, "end": 3, "text": "A door."}])
    monkeypatch.setattr(main, "render_parallax", lambda *a: None)
    reports = iter([QAReport(summary="", issues=[QAIssue(beat=1, severity="major", issue="Image shows a beach")]),
                    QAReport(summary="", issues=[])])
    monkeypatch.setattr(main, "review_render", lambda *a: next(reports))
    monkeypatch.setattr(coord.visual_engine, "generate_thumbnails", lambda *a: (tmp_path / "c.jpg", tmp_path / "g.jpg"))
    monkeypatch.setattr(coord.multiformat_gen, "generate_story_sequence", lambda *a: [])
    monkeypatch.setattr(coord.multiformat_gen, "generate_carousel_sequence", lambda *a: [])
    renders = []
    monkeypatch.setattr(coord.video_composer, "compose_video", lambda *a, **k: renders.append(1))
    monkeypatch.setattr(coord.video_composer, "make_review_proxy", lambda m, o: o)
    monkeypatch.setattr(coord, "publish_run", lambda run_id, dry_run, previous_status: {"status": "simulated"})
    db.create_run("r9", "01_topic")
    payload = dict(curated_payload, caption="Story.")

    coord._render_approve_publish({"id": "01_topic"}, "r9", tmp_path, payload, [topic_img], topic_img,
                                  tmp_path / "v.mp3", tmp_path / "bed.wav", [], 3.0, True, True, None)

    assert plans[0] == ([topic_img, beat_img], "") and plans[1][1] == "Beat 1: Image shows a beach"
    assert len(renders) == 2  # one re-render, then stop
    assert payload["caption"] == "Story.\n\n📷 Images via Wikimedia Commons: Bob (CC BY 4.0)"
    assert payload["beat_image_note"].startswith("🔍 1 beat-specific image")
