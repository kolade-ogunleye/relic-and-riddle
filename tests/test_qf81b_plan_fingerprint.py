"""QF-81b: a planned carousel's plan is bound to the review card you approved.

Before: the approved review fingerprint (QF-110) left a planned carousel's slides out (they are drawn after review, at
the slot, QF-81) but bound nothing in their place, and the carousel slot did not check the approval at all. A plan
rewritten after approval to name another photo (with that photo's correct SHA-256) passed the slot's own plan checks,
and the slot drew and posted the substituted photo.
After: the plan's bytes are in the fingerprint, and a Telegram approval whose card fingerprint no longer matches holds
the carousel at its slot with a plain reason; nothing is drawn or posted. An unchanged plan still posts.

Round 2 (review 1, blocking 1): such a hold is a binding hold. Its Reel is posted, so the review cannot be approved
again: the alert says the carousel will not post and offers no Redraw; Redraw refuses while the mismatch remains; the
held carousel never takes or blocks a slot another ready episode could use. QF-81's own holds keep their Redraw.
"""
import json
from datetime import timedelta
from pathlib import Path

import test_qf81_lazy_carousels as qf81
from test_qf81_lazy_carousels import TUE, _carousels, _photo, _slot

coord = qf81.coord   # QF-81's fixture: a coordinator that makes planned runs and runs the carousel slot

CARD = 77


def _approve_on_card(c, run_id):
    """What the review card does (telegram_bot): the fingerprint it showed, then the approval on that message."""
    from src.telegram_state import put, review_fingerprint
    shown = review_fingerprint(c.db, run_id)
    put(c.db, "review-card", f"{run_id}:{CARD}", shown)
    c.db.record_approval(run_id, "01_topic", CARD)
    c.db.update_approval_decision(run_id, "approved", decided_by="telegram")
    return shown


def _substitute_photo(run_dir):
    """Rewrites the plan after approval: its second photo becomes another one, recorded with its correct SHA-256, so
    the slot's own plan checks (photo on disk, SHA-256 as recorded) pass."""
    from src import carousel_lazy
    other = _photo(run_dir / "photos" / "other.jpg", 210)
    path = run_dir / carousel_lazy.PLAN
    record = json.loads(path.read_text(encoding="utf-8"))
    record["stills"][1] = {"path": str(other), "sha256": carousel_lazy._sha256(other)}
    path.write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
    return other


def test_a_plan_rewritten_after_approval_holds_the_carousel_at_its_slot(coord):
    run_dir, _ = coord.make("r1", TUE - timedelta(days=3))
    _approve_on_card(coord, "r1")
    other = _substitute_photo(run_dir)
    _slot(coord, TUE)
    post = _carousels(coord, "r1")[0]
    assert coord.posted == [] and coord.drawn == []
    assert post["status"] == "failed" and post["detail"]["held"]
    assert "changed after you approved the review" in post["detail"]["error"] and post["detail"].get("binding")
    assert "changed after you approved the review" in coord.alerts[-1]
    assert not any(str(other) in f for f in post["files"])


def test_the_plan_is_in_the_approved_fingerprint(coord):
    from src.telegram_state import review_fingerprint
    run_dir, _ = coord.make("r1", TUE - timedelta(days=3))
    shown = _approve_on_card(coord, "r1")
    _substitute_photo(run_dir)
    assert review_fingerprint(coord.db, "r1") != shown


def test_an_unchanged_plan_still_posts(coord):
    coord.make("r1", TUE - timedelta(days=3))
    shown = _approve_on_card(coord, "r1")
    _slot(coord, TUE)
    post = _carousels(coord, "r1")[0]
    assert post["status"] == "published" and [r for r, _, _ in coord.posted] == ["r1"]
    from src.telegram_state import review_fingerprint
    assert review_fingerprint(coord.db, "r1") == shown      # installing the slides leaves the binding as it was


def test_the_real_order_never_holds_a_normal_run(coord):
    """The make writes the plan before the card is sent (main._render_approve_publish: carousel_lazy.plan, then the
    card's fingerprint), so the approval is always made with the plan in place. A preview after approval draws into
    its own folder and leaves the plan as it is; the slot reuses that drawing and posts."""
    from src import carousel_lazy
    from src.telegram_state import review_fingerprint
    run_dir, _ = coord.make("r1", TUE - timedelta(days=3))
    assert carousel_lazy.planned(run_dir)                     # the plan exists before any card or approval
    shown = _approve_on_card(coord, "r1")
    coord.preview_carousel("r1")
    assert review_fingerprint(coord.db, "r1") == shown
    drawn = len(coord.drawn)
    _slot(coord, TUE)
    assert _carousels(coord, "r1")[0]["status"] == "published" and len(coord.drawn) == drawn   # the preview reused


def test_an_approval_without_a_card_record_is_not_bound_as_before(coord):
    """The command line and the auto-approve flag record no card fingerprint: nothing to bind to, as for QF-110's
    re-check, so the slot posts as it did."""
    coord.make("r1", TUE - timedelta(days=3))
    coord.db.record_approval("r1", "01_topic")
    coord.db.update_approval_decision("r1", "approved", decided_by="cli")
    _slot(coord, TUE)
    assert _carousels(coord, "r1")[0]["status"] == "published"


def test_a_reviewed_file_gone_after_approval_holds_with_its_reason(coord):
    run_dir, _ = coord.make("r1", TUE - timedelta(days=3))
    _approve_on_card(coord, "r1")
    (run_dir / "m.mp4").unlink()
    _slot(coord, TUE)
    post = _carousels(coord, "r1")[0]
    assert post["status"] == "failed" and "can no longer be read" in post["detail"]["error"] and coord.posted == []


def test_a_binding_hold_says_it_will_not_post_and_offers_no_redraw(coord):
    run_dir, _ = coord.make("r1", TUE - timedelta(days=3))
    _approve_on_card(coord, "r1")
    _substitute_photo(run_dir)
    _slot(coord, TUE)
    post = _carousels(coord, "r1")[0]
    alert, buttons = coord.alerts[-1], coord.buttons[-1]
    assert len(coord.alerts) == 1 and post["detail"].get("binding")
    assert "will not post" in alert and "Nothing was posted" in alert and "Reel and other posts are not affected" in alert
    for impossible in ("new version", "approve it", "Redraw", "next carousel slot"):
        assert impossible not in alert
    assert not any(b["callback_data"].startswith("carredraw:") for row in buttons for b in row)
    assert buttons == [[{"text": "📋 Details", "callback_data": f"cpost:{coord.db.run_key('r1')}"}]]


def test_redraw_is_refused_while_the_plan_does_not_match_the_approval(coord):
    run_dir, _ = coord.make("r1", TUE - timedelta(days=3))
    _approve_on_card(coord, "r1")
    _substitute_photo(run_dir)
    _slot(coord, TUE)
    held = _carousels(coord, "r1")[0]
    reply = coord.redraw_carousel(str(held["id"]))
    assert "can't be redrawn" in reply and "changed after you approved the review" in reply
    assert "will not post" in reply
    after = _carousels(coord, "r1")[0]
    assert after["status"] == "failed" and not after["detail"].get("priority")   # no booking for the next slot


def test_a_newer_ready_episode_takes_the_next_slot_after_a_refused_redraw(coord):
    """Review 1's probe: held, a newer episode made ready, Redraw tapped; the next slot posted nothing and held r1
    again. Now the Redraw is refused and the newer episode takes the slot."""
    run_dir, _ = coord.make("r1", TUE - timedelta(days=3))
    _approve_on_card(coord, "r1")
    _substitute_photo(run_dir)
    _slot(coord, TUE)
    held = _carousels(coord, "r1")[0]
    coord.make("r2", TUE - timedelta(days=2, hours=12))
    coord.redraw_carousel(str(held["id"]))
    _slot(coord, TUE + timedelta(days=1))
    assert [r for r, _, _ in coord.posted] == ["r2"] and _carousels(coord, "r2")[0]["status"] == "published"
    assert len([a for a in coord.alerts if "not posted" in a.lower() or "held" in a.lower()]) == 1


def test_a_binding_hold_never_takes_the_slot_from_another_ready_episode(coord):
    """The newest episode no longer matches its approval: the slot goes to the next ready one the same day."""
    coord.make("r1", TUE - timedelta(days=3, hours=6))
    coord.make("r2", TUE - timedelta(days=3))
    _approve_on_card(coord, "r2")
    _substitute_photo(Path(coord.db.get_run("r2")["master_video_path"]).parent)
    _slot(coord, TUE)
    assert _carousels(coord, "r2")[0]["status"] == "failed" and _carousels(coord, "r2")[0]["detail"].get("binding")
    assert [r for r, _, _ in coord.posted] == ["r1"] and _carousels(coord, "r1")[0]["status"] == "published"


def test_qf81s_own_planner_hold_still_offers_redraw_and_still_posts(coord, monkeypatch):
    """A QF-81 hold (the real planner unavailable at the slot) on a bound approval: Redraw is offered, accepted (the
    binding still matches) and the carousel posts in the next slot."""
    qf81._planner(monkeypatch, None)
    coord.match = True
    coord.make("r1", TUE - timedelta(days=3))
    _approve_on_card(coord, "r1")
    _slot(coord, TUE)
    held = _carousels(coord, "r1")[0]
    assert held["status"] == "failed" and not held["detail"].get("binding")
    assert "Carousel held" in coord.alerts[-1] and "could not be matched" in coord.alerts[-1]
    assert coord.buttons[-1] == [[{"text": "🔁 Redraw and post next slot", "callback_data": f"carredraw:{held['id']}"}]]
    coord.match = False                                   # the cause is dealt with
    qf81._Frozen.now_value = qf81.ps.carousel_time(TUE.date()) + timedelta(hours=2)
    assert "is drawn again and posts" in coord.redraw_carousel(str(held["id"]))
    _slot(coord, TUE + timedelta(days=1))
    assert [r for r, _, _ in coord.posted] == ["r1"] and _carousels(coord, "r1")[0]["status"] == "published"
