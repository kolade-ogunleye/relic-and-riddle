"""The screens review of 2026-10-25: /render N renders topic N, Help forms vs notes, /help in any case, the
Bump picker, Covers paged, Posting numbered with each post on its own line, 🗓 Slots, confirmations before
Post now and Cancel, typed /posts by episode number, plain words where ids and raw statuses were."""
import sys
from datetime import datetime, timedelta, timezone

import pytest

from src import control, telegram_commands
from src.control import make_command_handler
from src.telegram_ui import long_labels, too_packed, with_nav, parent_of


def _h(db, **kw):
    return make_command_handler(db, lambda: None, lambda: "", **kw)


def test_render_n_renders_that_topic(db):
    started = []
    h = make_command_handler(db, lambda: None, lambda: started.append("run") or "Run started.")
    topics = db.list_all_topics()
    target = next(i for i, t in enumerate(topics, 1) if t["id"] != db.get_next_topic()["id"])
    text, _ = h("qrender", str(target))
    assert started == ["run"] and db.get_next_topic()["id"] == topics[target - 1]["id"] and "queue changed" not in text


def test_help_offers_commands_to_copy_and_notes_to_read():
    text = telegram_commands.help_text("Diagnostics")
    assert "<code>/ask reply" not in text and "<i>reply to its card with /ask and the question</i>" in text
    assert "<code>/render 4</code>" in telegram_commands.help_text("Everyday")
    assert telegram_commands.find_group("topics") == "Topics" and telegram_commands.find_group("diag") == "Diagnostics"
    assert not too_packed(telegram_commands.help_text())


def test_help_topics_opens_topics(db):
    text, _ = _h(db)("chelp", "topics")
    assert text.startswith("❓ <b>Help</b> · Topics")


def test_the_bump_picker_leaves_out_the_topic_already_next(db):
    text, rows = _h(db)("pickbump", "0")
    nxt = db.get_next_topic()
    assert all(nxt["id"] not in b["callback_data"] for row in rows for b in row)


# Sun 25 Oct 2026 17:00 EDT, the day of the review. A carousel goes out 48 h to 7 days after its Reel, on a Tue-Thu
# slot whose hour alternates by date (src/post_schedule.py), so from some days and hours a Reel put an hour ahead
# (not in a Reel slot, as _book does) leaves its carousel no slot before it expires (a Reel at Tue 3 Nov 13:00 EST:
# Thu's 12:00 is 1 h too soon, the next Tue's 19:00 6 h too late). From here it has Tue 27 Oct 19:00.
FROZEN_NOW = datetime(2026, 10, 25, 21, 0, tzinfo=timezone.utc)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return FROZEN_NOW.astimezone(tz) if tz else FROZEN_NOW.astimezone().replace(tzinfo=None)


@pytest.fixture
def frozen_clock(monkeypatch):
    """The booking (this module), its created_at (src.db) and the carousel plan (src.posting_view) all read one
    fixed instant, so a test of who takes a carousel slot does not depend on the day and hour it runs."""
    from src import db as db_module
    from src import posting_view
    for module in (sys.modules[__name__], db_module, posting_view):
        monkeypatch.setattr(module, "datetime", _FrozenDatetime)
    return FROZEN_NOW


def _book(db, tmp_path):
    topic = db.list_all_topics()[0]["id"]
    db.create_run("run_p", topic)
    db.update_run("run_p", "scheduled", master_video_path=str(tmp_path / "m.mp4"))
    # an hour ahead (tests of the carousel's slot freeze the clock: see FROZEN_NOW)
    due = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(second=0, microsecond=0)
    reel = db.add_scheduled_post("run_p", "reel", due.isoformat(), [])
    db.add_scheduled_post("run_p", "story", (due + timedelta(minutes=45)).isoformat(), ["s.jpg"])
    db.add_scheduled_post("run_p", "carousel", None, ["c.jpg"], status="waiting")
    return reel


def test_posting_is_a_numbered_list_with_a_post_a_line_and_slots(db, tmp_path):
    _book(db, tmp_path)
    text, buttons = _h(db)("cposts", "")
    assert "\n1. <b>" in text and "🎬 Reel" in text and "📱 Stories" in text and "🖼 Carousel" in text
    assert not too_packed(text) and "· still to post: 1 Reel, 1 Stories post, 1 carousel, from 1 episode" in text
    flat = [b for row in buttons for b in row]
    assert flat[0] == {"text": "1", "callback_data": f"cpost:{db.run_key('run_p')}"}
    assert {"text": "🗓 Slots", "callback_data": "cslots:"} in flat


def test_slots_show_the_week_booked_and_free(db, tmp_path):
    _book(db, tmp_path)
    text, buttons = _h(db)("cslots", "")
    assert text.startswith("🗓 <b>Slots</b>") and "free Reel slot(s) this week" in text
    assert "<b>free</b>" in text and db.run_label("run_p").split(" ·")[0].replace('"', "&quot;") in text
    assert long_labels(with_nav(buttons, parent_of("cslots"))) == []


def test_post_now_and_cancel_ask_first(db, tmp_path):
    reel = _book(db, tmp_path)
    _, buttons = control.posting_episode(db, "run_p")
    flat = {b["callback_data"] for row in buttons for b in row}
    assert f"postnowask:{db.run_key('run_p')}" in flat and f"cancelask:{reel}" in flat
    assert not any(c.startswith(("postnow:", "cancelpost:")) for c in flat)       # never one tap
    text, rows = _h(db)("cancelask", str(reel))
    assert "Cancel this booking?" in text and rows[0][0]["callback_data"] == f"cancelpost:{reel}"


def test_typed_posts_take_the_episode_number(db, tmp_path):
    reel = _book(db, tmp_path)
    assert telegram_commands.normalise_command("posts", "cancel 1") == ("postsdo", "cancel 1")
    assert telegram_commands.normalise_command("posts", "slots") == ("cslots", "")
    text, rows = _h(db)("postsdo", "cancel 1")
    assert rows[0][0]["callback_data"] == f"cancelpost:{reel}"
    text, _ = _h(db)("postsdo", "now 9")
    assert "There is no episode 9" in text


def test_an_episode_says_what_went_wrong_in_words(db, tmp_path):
    from src.posting_view import post_error
    assert post_error({"error": "Partial publish: ok=['facebook'] failed=['instagram']"}) == "Instagram didn't post; the others did"


def test_pictures_and_the_photo_sheet_name_the_topic_not_its_folder(tmp_path):
    (tmp_path / "some_topic_id").mkdir()
    (tmp_path / "some_topic_id" / "credits.json").write_text('{"images": [], "reviewed": false}', encoding="utf-8")
    caption = control.review_caption(tmp_path / "some_topic_id", title="The Real Title")
    assert caption.startswith("🖼 <b>The Real Title</b>") and "some_topic_id" not in caption.split("\n")[0]


def test_the_digest_uses_words_not_internal_labels(db):
    from src import improve
    text = improve.digest_text(db)
    assert text.startswith("📰 <b>Weekly digest</b>")


def test_a_blocked_card_always_shows_why():
    # Re-check of 2026-10-25: at the tightest budget the gate's own findings were all folded away
    from src.telegram_bot import TelegramApprovalBot
    release = {"dry_run": True, "checks": [{"name": "visual_checks", "status": "fail",
                                           "detail": [{"type": "video_check", "severity": "major", "issue": f"problem {i}"}
                                                      for i in range(6)]}]}
    payload = {"title": "T", "narration_script": " ".join(["word"] * 110), "caption": "Line. " * 400,
               "carousel_caption": "Swipe.", "self_healing": {"issues": [{"severity": "info", "card": "c", "issue": "n " * 200}] * 6},
               "qa_summary": "🔎 Automated video check: 6 issue(s)\n" + "\n".join("· beat 2: " + "x " * 150 for _ in range(6))}
    card = TelegramApprovalBot.format_card_text(payload, "run_x", release=release)
    gate = card.split("Release gate:")[1].split("</blockquote>")[0]
    assert "problem 0" in gate and "problem 1" in gate                        # at least two reasons, always


def test_a_decided_card_stays_one_message_with_its_badge():
    from src.telegram_bot import CARD_PAGE_LIMIT, TelegramApprovalBot, _tg_len
    from src.telegram_pages import PAGE_ABOVE
    payload = {"title": "T", "narration_script": " ".join(["word"] * 110), "caption": "Line. " * 400,
               "carousel_caption": "Swipe.", "self_healing": {"issues": [{"severity": "info", "card": "c", "issue": "n " * 200}] * 5}}
    badge = "\n\n✅ <b>Approved</b> by " + "x" * 64 + " · release check and publishing under way"
    card = TelegramApprovalBot.format_card_text(payload, "run_x", reserve=_tg_len(badge)) + badge
    assert _tg_len(card) <= PAGE_ABOVE and _tg_len(card) - _tg_len(badge) <= CARD_PAGE_LIMIT


def test_different_alerts_with_the_same_heading_are_not_merged(db):
    db.record_alert("❌ <b>Production failed</b>\nTopic A: no script")
    db.record_alert("❌ <b>Production failed</b>\nTopic B: no photos")
    db.record_alert("⏰ Still waiting for your review of \"X\"")
    db.record_alert("⏰ Still waiting for your review of \"X\"")
    text, _ = _h(db)("calerts", "")
    assert "Topic A" in text and "Topic B" in text and text.count("2 times") == 1


def test_a_carousel_that_wont_post_takes_no_slot(db, tmp_path, frozen_clock):
    from src.posting_view import carousel_plan
    _book(db, tmp_path)
    assert carousel_plan(db)                                           # booked and live: it has its slot
    db.set_setting("run_mode:run_p", "dry_run")                        # a dry run nobody promoted
    assert carousel_plan(db) == {}


def test_every_recorded_finding_class_has_words():
    from src.improve import plain_name
    for klass in ("script.facts", "video.other", "issue.bot", "release.asset_rights", "check.second_unavailable",
                  "video.beat_7", "ui.cut_short", "graphics.emphasis_words", "script.unheard_of"):
        name = plain_name(klass)
        assert "." not in name and "_" not in name and name[0].isupper(), (klass, name)


def test_a_live_run_not_promoted_takes_no_slot_where_only_promoted_runs_post(db, tmp_path, frozen_clock):
    # Verification of c27168a: Slots gave such a run a carousel slot the daemon then skipped
    from src.posting_view import carousel_plan, wont_post
    _book(db, tmp_path)
    assert carousel_plan(db) and wont_post(db, "run_p") is None
    db.set_setting("posts_only_promoted", "true")
    assert carousel_plan(db) == {} and "promoted" in wont_post(db, "run_p")
    db.set_setting("promoted:run_p", "2026-10-25")
    assert carousel_plan(db) and wont_post(db, "run_p") is None


def test_the_next_free_slot_is_not_read_as_booked(db, tmp_path):
    """Operator, 2026-10-27: "Next Reel slot Wed 28 Oct ... 3 episode(s) booked" read as three episodes booked
    into a slot that Slots showed free. The slot is named free; what waits is counted by kind, apart from it."""
    from src.control import next_slot_line, still_to_post
    _book(db, tmp_path)
    booked = db.list_scheduled_posts(("pending", "waiting"))
    line = next_slot_line(db, booked)
    assert line.startswith("next free Reel slot ") and "episode(s) booked" not in line
    assert line.endswith("· still to post: 1 Reel, 1 Stories post, 1 carousel, from 1 episode")
    carousels = [{"kind": "carousel", "run_id": r} for r in ("a", "b", "c")]
    assert still_to_post(carousels) == "still to post: 3 carousels, from 3 episodes"
    assert still_to_post([]) == "nothing waiting to post"


def test_a_topic_left_in_production_after_its_run_ended_can_be_restored(db):
    """Antikythera, production 2026-10-27: sent back to Regenerate on 25 Oct, its new version never started; the
    topic stayed "in production", /queue requeue refused it, and the dashboard's Re-render had expired."""
    from src.control import stale_topics
    stuck, busy = db.list_all_topics()[0]["id"], db.list_all_topics()[1]["id"]
    for tid, status in ((stuck, "regenerate"), (busy, "pending_approval")):
        db.create_run(f"run_{tid}", tid)
        db.update_run(f"run_{tid}", status)
        with db._conn() as conn:
            conn.execute("UPDATE topics SET status = 'in_production' WHERE id = ?", (tid,))
    assert stale_topics(db) == {stuck: "regenerate"}                               # one waiting for review is not stale
    text = _h(db)("queue", "")
    text = text[0] if isinstance(text, tuple) else text
    assert "sent back to Regenerate; its new version never started" in text
    serial = [t["id"] for t in db.list_all_topics()].index(stuck) + 1
    reply = _h(db)("requeue", str(serial))
    reply = reply[0] if isinstance(reply, tuple) else reply
    assert "restored to the queue" in reply
    assert next(t for t in db.list_all_topics() if t["id"] == stuck)["status"] == "queued"
    refused = _h(db)("requeue", str([t["id"] for t in db.list_all_topics()].index(busy) + 1))
    assert "cannot be requeued" in (refused[0] if isinstance(refused, tuple) else refused)   # its review still waits
