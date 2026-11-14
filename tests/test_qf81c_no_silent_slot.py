"""QF-81c: a booked carousel never fails silently at its slot, and the binding is checked inside the publication lock.

Before (QF-81b review, round 2): a planned carousel whose carousel_plan.json was deleted after approval took its slot
and failed with "image files are missing", with no alert, and the next ready episode lost that day's slot (against
QF-110's "never blocks silently"). A plan that could not be read or was corrupt took the slot too, and was held with a
Redraw button that could not help (a redraw draws from the same plan). A drawn booking whose slides were gone failed
silently in the same way. And the approval binding (QF-81b) was checked before the publication lock was taken, so a
plan changed while the lock was awaited was drawn and posted.
After: each is held before the slot is taken, with a plain reason and one alert (what is missing, nothing was posted,
its Reel and other posts are unaffected, Details only), and the next ready episode posts the same day. The plan and
the binding are checked again under the lock; a carousel held there leaves the slot free. Normal runs, QF-81's own
holds and QF-81b's binding holds are as before.
"""
import contextlib
import json
from datetime import timedelta
from pathlib import Path

import pytest
import test_qf81_lazy_carousels as qf81
import test_qf81b_plan_fingerprint as qf81b
from test_qf81_lazy_carousels import TUE, _carousels, _photo, _slot

coord = qf81.coord   # QF-81's fixture: a coordinator that makes planned runs and runs the carousel slot


def _run_dir(c, run_id):
    return Path(c.db.get_run(run_id)["master_video_path"]).parent


def _two_ready(c):
    """An older episode r1 ready, and the newest r2, which the slot would pick first."""
    c.make("r1", TUE - timedelta(days=3, hours=6))
    run_dir, _ = c.make("r2", TUE - timedelta(days=3))
    return run_dir


def _assert_one_plain_alert(c, run_id, what):
    alerts = [a for a in c.alerts if "not posted" in a.lower() or "held" in a.lower()]
    assert len(alerts) == 1, c.alerts
    alert, buttons = alerts[0], c.buttons[c.alerts.index(alerts[0])]
    assert what in alert and "Nothing was posted" in alert and "Reel and other posts are not affected" in alert
    assert "Redraw" not in alert
    assert buttons == [[{"text": "📋 Details", "callback_data": f"cpost:{c.db.run_key(run_id)}"}]]


def test_a_deleted_plan_is_held_with_one_alert_and_the_next_ready_episode_posts(coord):
    run_dir = _two_ready(coord)
    (run_dir / "carousel_plan.json").unlink()
    _slot(coord, TUE)
    held = _carousels(coord, "r2")[0]
    assert held["status"] == "failed" and held["detail"].get("held")
    assert "carousel_plan.json" in held["detail"]["error"] and "missing" in held["detail"]["error"]
    _assert_one_plain_alert(coord, "r2", "carousel_plan.json")
    assert [r for r, _, _ in coord.posted] == ["r1"] and _carousels(coord, "r1")[0]["status"] == "published"


def test_a_deleted_plan_on_a_card_approval_names_the_plan(coord):
    """With a card approval the binding also breaks; the hold names what is missing, not a vague change."""
    run_dir, _ = coord.make("r1", TUE - timedelta(days=3))
    qf81b._approve_on_card(coord, "r1")
    (run_dir / "carousel_plan.json").unlink()
    _slot(coord, TUE)
    held = _carousels(coord, "r1")[0]
    assert held["status"] == "failed" and "carousel_plan.json" in held["detail"]["error"]
    _assert_one_plain_alert(coord, "r1", "carousel_plan.json")
    assert coord.posted == [] and coord.db.get_setting("carousel_posted_on") != TUE.date().isoformat()


@pytest.mark.parametrize("content", [b"\xff\xfe\x00not text", b"{\"stills\": [", b"{\"version\": 2}",
                                     b"[1, 2, 3]", b"{\"stills\": [{\"path\": \"x.jpg\"}]}"],
                         ids=["unreadable", "truncated", "no-photos-key", "not-an-object", "entry-without-sha"])
def test_an_unreadable_or_corrupt_plan_is_held_the_same_way(coord, content):
    run_dir = _two_ready(coord)
    (run_dir / "carousel_plan.json").write_bytes(content)
    _slot(coord, TUE)
    held = _carousels(coord, "r2")[0]
    assert held["status"] == "failed" and "cannot be read" in held["detail"]["error"]
    _assert_one_plain_alert(coord, "r2", "carousel_plan.json")
    assert [r for r, _, _ in coord.posted] == ["r1"] and coord.drawn and all(d[0] != "r2" for d in coord.drawn)


def test_redraw_is_refused_while_the_plan_is_missing(coord):
    run_dir, _ = coord.make("r1", TUE - timedelta(days=3))
    (run_dir / "carousel_plan.json").unlink()
    _slot(coord, TUE)
    held = _carousels(coord, "r1")[0]
    reply = coord.redraw_carousel(str(held["id"]))
    assert "can't be redrawn" in reply and "carousel_plan.json" in reply and "will not post" in reply
    after = _carousels(coord, "r1")[0]
    assert after["status"] == "failed" and not after["detail"].get("priority")


def _drawn_booking(c, tmp_path, payload, slides=2):
    """A carousel drawn with its run (before QF-81): its booking lists its slides. An older r1 is ready too."""
    c.make("r1", TUE - timedelta(days=3, hours=6))
    old = tmp_path / "old"
    files = [_photo(old / "carousel" / f"carousel_slide_{n:02d}.jpg", 30 * n) for n in range(1, slides + 1)]
    (old / "m.mp4").write_bytes(b"0")
    c.db.create_run("old", "01_topic")
    c.db.update_run("old", "published", script_payload=dict(payload, topic_id="01_topic"),
                    master_video_path=old / "m.mp4", thumbnail_cover_path=old / "m.mp4")
    c._book_followers("old", old, TUE - timedelta(days=3))
    reel = c.db.add_scheduled_post("old", "reel", (TUE - timedelta(days=3)).isoformat(), [])
    c.db.update_scheduled_post(reel, "published", detail={"posted_at": (TUE - timedelta(days=3)).isoformat()})
    assert _carousels(c, "old")[0]["files"] == [str(f) for f in files]
    return files


def test_a_drawn_booking_whose_slides_are_gone_is_held_and_the_next_ready_episode_posts(coord, tmp_path,
                                                                                         curated_payload):
    """A carousel drawn with its run (before QF-81) books its slides; when they are gone at the slot it says so."""
    for s in _drawn_booking(coord, tmp_path, curated_payload):
        s.unlink()
    _slot(coord, TUE)
    held = _carousels(coord, "old")[0]
    assert held["status"] == "failed" and "no longer on disk" in held["detail"]["error"]
    _assert_one_plain_alert(coord, "old", "no longer on disk")
    assert [r for r, _, _ in coord.posted] == ["r1"]


# ------------------------------------------------------------- QF-81d: a drawn booking posts its whole deck or nothing
@pytest.mark.parametrize("missing", [[0], [1, 2]], ids=["cover-slide-gone", "two-of-three-gone"])
def test_a_partial_deck_is_held_and_the_next_ready_episode_posts(coord, tmp_path, curated_payload, missing):
    """Before: the slides left were posted, a deck you never approved, and the alert said "Carousel posted"."""
    files = _drawn_booking(coord, tmp_path, curated_payload, slides=3)
    for i in missing:
        files[i].unlink()
    _slot(coord, TUE)
    held = _carousels(coord, "old")[0]
    assert held["status"] == "failed" and held["detail"].get("final")
    assert f"{len(missing)} of its 3 slides" in held["detail"]["error"] and files[missing[0]].name in held["detail"]["error"]
    _assert_one_plain_alert(coord, "old", "is not the carousel you approved")
    assert [r for r, _, _ in coord.posted] == ["r1"]
    reply = coord.redraw_carousel(str(held["id"]))
    assert "can't be redrawn" in reply and "will not post" in reply
    after = _carousels(coord, "old")[0]
    assert after["status"] == "failed" and not after["detail"].get("priority") and after["files"] == held["files"]


def test_a_drawn_slide_changed_after_a_card_approval_is_a_binding_hold(coord, tmp_path, curated_payload):
    """Every slide is there but one is not the one on the card you approved: held, and r1 takes the slot."""
    files = _drawn_booking(coord, tmp_path, curated_payload, slides=3)
    qf81b._approve_on_card(coord, "old")
    _photo(files[1], 220)
    _slot(coord, TUE)
    held = _carousels(coord, "old")[0]
    assert held["status"] == "failed" and held["detail"].get("binding")
    _assert_one_plain_alert(coord, "old", "changed after you approved the review")
    assert [r for r, _, _ in coord.posted] == ["r1"]


def test_a_whole_drawn_deck_still_posts(coord, tmp_path, curated_payload):
    files = _drawn_booking(coord, tmp_path, curated_payload, slides=3)
    qf81b._approve_on_card(coord, "old")
    _slot(coord, TUE)
    assert coord.posted == [("old", "carousel", [str(f) for f in files])]
    assert not any("not posted" in a.lower() or "held" in a.lower() for a in coord.alerts)


# ------------------------------------------------- QF-81c round 2: a plan with no photo a deck can be drawn from
def _no_photo_plan(run_dir, how, payload):
    """r2's plan, made so it holds no photo a deck can be drawn from. "no-still" is the plan the make itself writes
    (carousel_lazy.plan) when the run's scene media are all video or none is on disk; "video-entries" lists only
    video clips (with their SHA-256), fewer stills than the renderer's minimum."""
    from src import carousel_lazy
    clip = run_dir / "clip.mp4"
    clip.write_bytes(b"not a still")
    if how == "no-still":
        carousel_lazy.plan(run_dir, [clip, run_dir / "photos" / "gone.jpg"], dict(payload))
        assert carousel_lazy._read_plan(run_dir)[1] == []
    else:
        plan = run_dir / carousel_lazy.PLAN
        record = json.loads(plan.read_text(encoding="utf-8"))
        record["stills"] = [{"path": str(clip), "sha256": carousel_lazy._sha256(clip)}]
        plan.write_text(json.dumps(record, indent=1), encoding="utf-8", newline="\n")


@pytest.mark.parametrize("how", ["no-still", "video-entries"])
def test_a_plan_without_a_photo_is_held_before_the_slot_and_the_older_episode_posts(coord, curated_payload, how):
    """QF-81c review, round 1, blocking 1: it took the slot and was held with a Redraw that cannot help."""
    _no_photo_plan(_two_ready(coord), how, curated_payload)
    _slot(coord, TUE)
    held = _carousels(coord, "r2")[0]
    assert held["status"] == "failed" and held["detail"].get("final")
    assert "records no photo of the topic" in held["detail"]["error"]
    _assert_one_plain_alert(coord, "r2", "records no photo of the topic")
    assert [r for r, _, _ in coord.posted] == ["r1"] and all(d[0] != "r2" for d in coord.drawn)
    assert coord.db.get_setting("carousel_posted_on") == TUE.date().isoformat()
    reply = coord.redraw_carousel(str(held["id"]))
    assert "can't be redrawn" in reply and "records no photo of the topic" in reply and "will not post" in reply
    after = _carousels(coord, "r2")[0]
    assert after["status"] == "failed" and not after["detail"].get("priority")


def test_fewer_photos_than_a_deck_needs_is_held_the_same_way(coord, monkeypatch):
    """The minimum is carousel_lazy.MIN_STILLS (one today); a plan below it is a final hold before the slot."""
    monkeypatch.setattr("src.carousel_lazy.MIN_STILLS", 3, raising=False)   # QF-81's runs plan two photos
    _two_ready(coord)
    _slot(coord, TUE)
    held = _carousels(coord, "r2")[0]
    assert held["status"] == "failed" and held["detail"].get("final")
    assert "records 2 photos of the topic, and its slides need 3" in held["detail"]["error"]
    assert coord.posted == [] and coord.drawn == []   # r1's plan is below it as well: neither is drawn
    assert len([a for a in coord.alerts if "not posted" in a.lower()]) == 2
    assert "can't be redrawn" in coord.redraw_carousel(str(held["id"]))


def test_a_plan_without_a_photo_costs_no_slot_over_two_slots(coord, curated_payload):
    """The reviewer's two-slot probe: before, r2 took Tuesday's slot, its Redraw booked it first for Wednesday's, and
    it took that one too (two slots lost, two alerts). Now r1 posts Tuesday, the Redraw is refused, r0 posts
    Wednesday, and r2 has one alert."""
    coord.make("r0", TUE - timedelta(days=3, hours=12))
    _no_photo_plan(_two_ready(coord), "no-still", curated_payload)
    _slot(coord, TUE)
    held = _carousels(coord, "r2")[0]
    assert "can't be redrawn" in coord.redraw_carousel(str(held["id"]))
    _slot(coord, TUE)                                   # the daemon's next pass: never twice a day
    _slot(coord, TUE + timedelta(days=1))
    assert [r for r, _, _ in coord.posted] == ["r1", "r0"]
    assert len([a for a in coord.alerts if "not posted" in a.lower() or "held" in a.lower()]) == 1
    assert _carousels(coord, "r2")[0]["status"] == "failed"


@contextlib.contextmanager
def _changed_inside_the_lock(monkeypatch, change):
    """The slot's checks pass; then, while the slot waits for the carousel's publication lock, the plan changes."""
    from src import publication_lock as pl
    real = pl.publication_lock

    @contextlib.contextmanager
    def lock(*a, **k):
        with real(*a, **k):
            if a[1].startswith("carousel:"):
                change()
            yield
    monkeypatch.setattr(pl, "publication_lock", lock)
    yield


def test_a_plan_changed_between_the_slot_check_and_the_draw_is_held(coord, monkeypatch):
    run_dir, _ = coord.make("r1", TUE - timedelta(days=3))
    qf81b._approve_on_card(coord, "r1")
    with _changed_inside_the_lock(monkeypatch, lambda: qf81b._substitute_photo(run_dir)):
        _slot(coord, TUE)
    held = _carousels(coord, "r1")[0]
    assert coord.posted == [] and coord.drawn == []
    assert held["status"] == "failed" and held["detail"].get("binding")
    assert "changed after you approved the review" in held["detail"]["error"]
    _assert_one_plain_alert(coord, "r1", "changed after you approved the review")


def test_a_plan_removed_inside_the_lock_is_held_and_leaves_the_slot_free(coord, monkeypatch):
    run_dir = _two_ready(coord)
    with _changed_inside_the_lock(monkeypatch, lambda: (run_dir / "carousel_plan.json").unlink(missing_ok=True)):
        _slot(coord, TUE)
    held = _carousels(coord, "r2")[0]
    assert held["status"] == "failed" and "carousel_plan.json" in held["detail"]["error"]
    _assert_one_plain_alert(coord, "r2", "carousel_plan.json")
    assert coord.posted == [] and coord.db.get_setting("carousel_posted_on") != TUE.date().isoformat()
    _slot(coord, TUE)                                   # the daemon's next pass in the same slot
    assert [r for r, _, _ in coord.posted] == ["r1"] and _carousels(coord, "r1")[0]["status"] == "published"


# ------------------------------------------------------------------ unchanged: normal runs, QF-81's and QF-81b's holds
def test_a_normal_run_still_posts(coord):
    coord.make("r1", TUE - timedelta(days=3))
    qf81b._approve_on_card(coord, "r1")
    _slot(coord, TUE)
    assert [r for r, _, _ in coord.posted] == ["r1"] and _carousels(coord, "r1")[0]["status"] == "published"
    assert coord.db.get_setting("carousel_posted_on") == TUE.date().isoformat()
    assert not any("not posted" in a.lower() or "held" in a.lower() for a in coord.alerts)


def test_qf81s_photo_hold_still_takes_the_slot_and_offers_redraw(coord):
    run_dir, _ = coord.make("r1", TUE - timedelta(days=3))
    _photo(run_dir / "photos" / "p2.jpg", 200)
    _slot(coord, TUE)
    post = _carousels(coord, "r1")[0]
    assert "Carousel held" in coord.alerts[-1] and "changed since the run was made" in post["detail"]["error"]
    assert coord.buttons[-1] == [[{"text": "🔁 Redraw and post next slot", "callback_data": f"carredraw:{post['id']}"}]]
    assert "is drawn again and posts" in coord.redraw_carousel(str(post["id"]))


def test_qf81bs_binding_hold_is_unchanged(coord):
    coord.make("r1", TUE - timedelta(days=3, hours=6))
    coord.make("r2", TUE - timedelta(days=3))
    qf81b._approve_on_card(coord, "r2")
    qf81b._substitute_photo(_run_dir(coord, "r2"))
    _slot(coord, TUE)
    held = _carousels(coord, "r2")[0]
    assert held["detail"].get("binding") and "changed after you approved the review" in held["detail"]["error"]
    assert [r for r, _, _ in coord.posted] == ["r1"]
