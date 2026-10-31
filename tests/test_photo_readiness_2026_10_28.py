"""One readiness rule, the video's: approval, pool_ready, the screens and the refill count Topic + Kept + at
most CONTEXT_SHOTS_MAX Context, as get_scene_sequence does. Production 2026-10-28
(run_greek_fire_siphon_1793038718, run_tunguska_event_1908_1793057943) failed at run start on pools that were
approved and shown as ready: "Only 4 of the 6 approved photos ... (need 5): 2 Topic, 4 Context"."""
import importlib
import json
import random

import pytest
from PIL import Image

from src import image_review
from src.config import MIN_SCENE_IMAGES
from src.credits import approve_set, load_credits, save_credits

TOPIC = {"id": "t1", "title": "Some Site", "location": {"name": "Somewhere", "country": "X"}}
VERDICT = {"topic": {"shows_topic": "yes", "usable": True, "safe": True},
           "context": {"shows_topic": "partly", "usable": True, "safe": True},
           "weak": {"shows_topic": "no", "usable": True, "safe": True}}


def _real_pipeline():
    import src.asset_pipeline as ap
    return importlib.reload(ap)  # the conftest stub replaced ensure_pool on the loaded module


def _pool(d, tiers, start=1, **extra):
    """A pool on disk: one photo per tier name (topic | context | weak | kept | unchecked)."""
    d.mkdir(parents=True, exist_ok=True)
    cr = load_credits(d) if (d / "credits.json").is_file() else {"images": []}
    for i, t in enumerate(tiers, start):
        f = f"{i:02d}_commons.jpg"
        Image.new("RGB", (64, 48), (i % 255, 0, 0)).save(d / f)
        im = {"file": f, "title": f"File:Photo {i}.jpg", "license": "CC0", "relevance": i}
        if t in VERDICT:
            im["vision_review"] = dict(VERDICT[t])
        elif t == "kept":
            im["operator_keep"] = True
        cr["images"].append(im)
    cr.update(extra)
    save_credits(d, cr)
    return cr["images"]


def _verdicts(tiers):
    return image_review.PoolReview(verdicts=[image_review.PhotoVerdict(index=i, reason="r", **VERDICT[t])
                                             for i, t in enumerate(tiers, 1)])


def test_two_topic_four_context_is_held_and_not_ready(monkeypatch, tmp_path):
    tiers = ["topic", "topic", "context", "context", "context", "context"]
    _pool(tmp_path / "t1", tiers)
    monkeypatch.setattr(image_review.free_llm, "call", lambda *a, **k: _verdicts(tiers))
    summary = image_review.review_pool(TOPIC, tmp_path / "t1")
    cr = load_credits(tmp_path / "t1")
    assert summary["status"] == "held" and cr["reviewed"] is False and len(cr["images"]) == 6
    assert "4 of 5 usable" in cr["review_note"]
    assert image_review.readiness_text(cr["images"]) == "2 topic photos, 4 context, at most 2 context count: 4 of 5 usable"
    assert not _real_pipeline().pool_ready("t1", tmp_path)


def test_an_approval_made_under_the_old_rule_is_not_ready(tmp_path):
    _pool(tmp_path / "t1", ["topic", "topic", "context", "context", "context", "context"],
          reviewed=True, reviewed_by="vision")
    ap = _real_pipeline()
    assert not ap.pool_ready("t1", tmp_path)
    assert "4 of 5 usable" in ap.not_ready_reason("t1", tmp_path)


def test_five_topic_photos_are_approved_and_ready(monkeypatch, tmp_path):
    tiers = ["topic"] * 5
    _pool(tmp_path / "t1", tiers)
    monkeypatch.setattr(image_review.free_llm, "call", lambda *a, **k: _verdicts(tiers))
    assert image_review.review_pool(TOPIC, tmp_path / "t1")["status"] == "approved"
    assert _real_pipeline().pool_ready("t1", tmp_path)


@pytest.mark.parametrize("seed", range(40))
def test_readiness_and_the_videos_scene_selection_agree(tmp_path, seed):
    from src.visual_engine import MissingAssetsError, VisualEngine
    rnd = random.Random(seed)
    tiers = [rnd.choice(["topic", "context", "context", "weak", "kept", "unchecked"]) for _ in range(rnd.randint(0, 10))]
    _pool(tmp_path / "t1", tiers, reviewed=True)
    ap = _real_pipeline()
    images = load_credits(tmp_path / "t1")["images"]
    r = image_review.readiness(images)
    try:
        scenes = VisualEngine(tmp_path).get_scene_sequence({"id": "t1"})
        video_takes_it = True
    except MissingAssetsError:
        scenes, video_takes_it = [], False
    assert ap.pool_ready("t1", tmp_path) is video_takes_it is r["ready"]
    assert r["usable"] == len(image_review.usable_photos(images))
    if video_takes_it:
        assert len(scenes) == r["usable"]


def _fake_fetch(tmp_path, tiers, seen):
    def run(cmd, **k):
        seen.append(cmd)
        d = tmp_path / cmd[cmd.index("--topic-id") + 1]
        n = len(load_credits(d)["images"])
        _pool(d, tiers, start=n + 1, reviewed=False)
    return run


def _fake_review(monkeypatch):
    def review(topic, d, apply=True):  # the merit review, by the same rule
        c = load_credits(d)
        c["reviewed"] = image_review.video_ready(c["images"])
        c["reviewed_by"] = "vision" if c["reviewed"] else None
        save_credits(d, c)
        return {"status": "approved" if c["reviewed"] else "held", "kept": len(c["images"])}
    monkeypatch.setattr(image_review, "review_pool", review)


def test_a_small_pool_short_of_usable_photos_is_refilled(monkeypatch, tmp_path):
    ap = _real_pipeline()
    _pool(tmp_path / "t1", ["topic", "topic", "context", "context", "context", "context"], reviewed=True, reviewed_by="vision")
    seen = []
    monkeypatch.setattr(ap.subprocess, "run", _fake_fetch(tmp_path, ["topic", "topic"], seen))
    _fake_review(monkeypatch)
    assert ap.ensure_pool(TOPIC, tmp_path)
    assert len(seen) == 1 and "--target-images" not in seen[0]       # under the pool size: the usual refill


def test_a_full_pool_of_context_shots_is_topped_up(monkeypatch, tmp_path):
    ap = _real_pipeline()
    _pool(tmp_path / "t1", ["topic"] + ["context"] * 19, reviewed=True, reviewed_by="vision")
    seen = []
    monkeypatch.setattr(ap.subprocess, "run", _fake_fetch(tmp_path, ["topic", "topic"], seen))
    _fake_review(monkeypatch)
    assert ap.ensure_pool(TOPIC, tmp_path)
    target = int(seen[0][seen[0].index("--target-images") + 1])
    assert target == image_review.MAX_PHOTOS                          # 20 + 2 per missing photo, capped at 24


def test_a_skipped_topic_is_reported_with_its_count(monkeypatch, tmp_path):
    ap = _real_pipeline()
    _pool(tmp_path / "thin", ["topic"] + ["context"] * 23, reviewed=True, reviewed_by="vision")   # full: no fetch
    _pool(tmp_path / "good", ["topic"] * 5, reviewed=True, reviewed_by="vision")
    monkeypatch.setattr(ap.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fetched")))
    _fake_review(monkeypatch)
    skipped = []
    assert ap.first_ready([{"id": "thin"}, {"id": "good"}], tmp_path, skipped=skipped) == {"id": "good"}
    assert [t["id"] for t, _ in skipped] == ["thin"] and "3 of 5 usable" in skipped[0][1]


def test_an_approved_pool_with_unchecked_photos_is_screened_before_it_is_judged(monkeypatch, tmp_path):
    ap = _real_pipeline()
    _pool(tmp_path / "t1", ["unchecked"] * 6, reviewed=True, reviewed_by="operator")
    assert not ap.pool_ready("t1", tmp_path)
    screened = []

    def screen(topic, d):
        c = load_credits(d)
        for im in c["images"]:
            im["vision_review"] = dict(VERDICT["topic"])
        save_credits(d, c)
        screened.append(d.name)
        return len(c["images"])
    monkeypatch.setattr(image_review, "screen_unchecked", screen)
    monkeypatch.setattr(ap.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fetched")))
    assert ap.ensure_pool(TOPIC, tmp_path) and screened == ["t1"]


def test_an_operator_approval_says_when_the_video_cannot_use_the_set(tmp_path):
    _pool(tmp_path, ["topic", "topic", "context", "context", "context", "context"])
    msg = approve_set(tmp_path, MIN_SCENE_IMAGES)
    assert "A video cannot use it yet" in msg and "4 of 5 usable" in msg


def test_the_screens_count_as_the_video_counts(db, tmp_path):
    from src.control import make_command_handler, review_caption
    _pool(tmp_path / "t1", ["topic", "topic", "context", "context", "context", "context"], reviewed=True)
    caption = review_caption(tmp_path / "t1")
    assert "short for a video" in caption and "4 of 5 usable" in caption
    listing = make_command_handler(db, lambda: None, lambda: "", assets_dir=tmp_path)("images", "")
    assert "0 approved" in listing and "4 of 5 usable" in listing
