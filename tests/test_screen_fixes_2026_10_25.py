"""The screens review of 2026-10-25: /render N renders topic N, Help forms vs notes, /help in any case, the
Bump picker, Covers paged, Posting numbered with each post on its own line, 🗓 Slots, confirmations before
Post now and Cancel, typed /posts by episode number, plain words where ids and raw statuses were."""
from datetime import datetime, timedelta, timezone

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


def _book(db, tmp_path):
    topic = db.list_all_topics()[0]["id"]
    db.create_run("run_p", topic)
    db.update_run("run_p", "scheduled", master_video_path=str(tmp_path / "m.mp4"))
    due = (datetime.now(timezone.utc) + timedelta(days=1)).replace(minute=0, second=0, microsecond=0)
    reel = db.add_scheduled_post("run_p", "reel", due.isoformat(), [])
    db.add_scheduled_post("run_p", "story", (due + timedelta(minutes=45)).isoformat(), ["s.jpg"])
    db.add_scheduled_post("run_p", "carousel", None, ["c.jpg"], status="waiting")
    return reel


def test_posting_is_a_numbered_list_with_a_post_a_line_and_slots(db, tmp_path):
    _book(db, tmp_path)
    text, buttons = _h(db)("cposts", "")
    assert "\n1. <b>" in text and "🎬 Reel" in text and "📱 Stories" in text and "🖼 Carousel" in text
    assert not too_packed(text) and "1 episode(s) booked" in text
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
