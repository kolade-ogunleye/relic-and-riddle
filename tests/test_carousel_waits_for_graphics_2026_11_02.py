"""Carousel slides wait for the run's graphic posters (2026-11-02)."""

from main import PipelineCoordinator


def test_the_carousel_waits_for_the_graphics_posters(db, monkeypatch, tmp_path, curated_payload):
    """The diagram slide re-lays a graphic poster, so both carousel copies start after graphics.make_all ends."""
    import threading
    import time

    import main
    from src.models import QAIssue, QAReport
    coord = PipelineCoordinator(db)
    monkeypatch.setattr(coord, "_verify_final_copy", lambda *a: None)  # This test isolates scene planning.
    topic_img, beat_img = tmp_path / "t.jpg", tmp_path / "b.jpg"
    monkeypatch.setattr(main, "ensure_all", lambda paths: (paths, 0))
    monkeypatch.setattr(main, "ensure_resolution", lambda p: p)
    monkeypatch.setattr(main, "load_credits", lambda d: {"images": []})
    monkeypatch.setattr(main, "llm_available", lambda: True)
    monkeypatch.setattr(main.beat_images, "plan_queries", lambda beats, topic: "plan")
    monkeypatch.setattr(main.footage, "source", lambda beats, topic, d, plan=None: ([], []))
    monkeypatch.setattr(main.beat_images, "source", lambda beats, topic, d, plan=None: (
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
    monkeypatch.setattr(coord.visual_engine, "generate_thumbnails", lambda *a, **k: (tmp_path / "c.jpg", tmp_path / "g.jpg"))
    monkeypatch.setattr(coord.multiformat_gen, "generate_story_sequence", lambda *a, **k: [])
    graphics_done = threading.Event()
    seen = []

    def make_all(*a, **k):
        time.sleep(0.3)
        graphics_done.set()
        return []
    monkeypatch.setattr(main.graphics, "make_all", make_all)

    def carousel(*a, **k):
        seen.append(graphics_done.is_set())
        return []
    monkeypatch.setattr(coord.multiformat_gen, "generate_carousel_sequence", carousel)
    renders = []
    monkeypatch.setattr(coord.video_composer, "compose_video", lambda *a, **k: renders.append(1))
    monkeypatch.setattr(coord.video_composer, "make_review_proxy", lambda m, o: o)
    monkeypatch.setattr(coord, "publish_run", lambda run_id, dry_run, previous_status: {"status": "simulated"})
    db.create_run("r9", "01_topic")
    payload = dict(curated_payload, caption="Story.")

    coord._render_approve_publish({"id": "01_topic"}, "r9", tmp_path, payload, [topic_img], topic_img,
                                  tmp_path / "v.mp3", tmp_path / "bed.wav", [], 3.0, True, True, None)

    assert seen and all(seen)  # every carousel copy (Instagram and Facebook) began after the graphics


