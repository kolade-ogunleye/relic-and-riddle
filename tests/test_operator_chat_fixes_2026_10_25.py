"""The operator's second pass over the chat (2026-10-25): numbered lists with number buttons and pages, every
item listed or "…and N more", ← back to the screen you came from, no stale "Rendering #8", Alerts drawn as a
screen, and card lists folded whole instead of cut mid-word."""
from src import control
from src.control import make_command_handler, shown_and_more
from src.telegram_bot import TelegramApprovalBot, listed
from src.telegram_ui import came_from, step


def test_back_goes_to_the_screen_you_came_from():
    trail = []
    for cb in ("cdash:", "cstat:", "jobs:"):
        trail = step(trail, cb)
    assert came_from(trail, "jobs:") == "cstat:"                         # Jobs opened from Status: ← Status
    trail = step(trail, "cstat:")                                       # ← pressed: back along the path
    assert trail == ["cdash:", "cstat:"] and came_from(trail, "cstat:") == "cdash:"
    for cb in ("chistory:", "chistory:1", "chist:7"):                   # a page replaces the page before it
        trail = step(trail, cb)
    assert came_from(trail, "chist:7") == "chistory:1"                  # back to the page it was on
    assert step(trail, "cdash:") == ["cdash:"]                          # home starts a new path
    assert step(["cdash:"], "runstopok:1") == ["cdash:"]                # an action is never replayed by ←
    assert came_from([], "jobs:") == "cstat:"                           # no path known: the screen's parent


def _waiting(db, n):
    topics = db.list_all_topics()
    for i in range(n):
        run_id = f"run_w{i}"
        db.create_run(run_id, topics[i % len(topics)]["id"])
        db.update_run(run_id, "pending_approval")
        db.record_approval(run_id, topics[i % len(topics)]["id"], 100 + i)


def test_reviews_are_numbered_paged_and_none_is_left_out(db):
    _waiting(db, 6)
    h = make_command_handler(db, lambda: None, lambda: "")
    text, buttons = h("creviews", "")
    assert "· 6 · page 1 of 2" in text and "\n1. " in text and "\n5. " in text and "\n6. " not in text
    assert [b["text"] for b in buttons[0]] == ["1", "2", "3", "4", "5"]
    assert all(b["callback_data"].startswith("resend:") for b in buttons[0])
    assert {"text": "▶", "callback_data": "creviews:1"} in buttons[1]
    text, buttons = h("creviews", "1")
    assert "\n6. " in text and buttons[0][0]["text"] == "6"


def test_a_topic_waiting_for_review_is_not_shown_as_rendering(db):
    # The queue said "Rendering #8 · progress" all day: the topic of a run waiting for review keeps
    # "in_production", which was taken for a run being made.
    topic = db.list_all_topics()[0]["id"]
    db.create_run("run_r", topic)
    db.record_topic_status(topic, "in_production", run_id="run_r")
    db.update_run("run_r", "pending_approval")
    h = make_command_handler(db, lambda: None, lambda: "")
    reply = h("qrefresh", "")
    labels = [b["text"] for row in reply[1] for b in row]
    assert not any(l.startswith("⏳ Rendering") for l in labels)
    db.update_run("run_r", "producing")                                 # while it is made, it says so
    labels = [b["text"] for row in h("qrefresh", "")[1] for b in row]
    assert any(l.startswith("⏳ Rendering") for l in labels) or not db.get_next_topic()


def test_a_long_section_ends_with_and_n_more():
    assert shown_and_more(["a", "b"]) == ["  • a", "  • b"]
    lines = shown_and_more([str(i) for i in range(12)])
    assert len(lines) == 6 and lines[-1] == "  <i>…and 7 more in Photo sets</i>"


def test_alerts_from_status_are_a_screen_not_a_pair_in_a_pair(db):
    db.record_alert("⏰ <b>Still waiting for your review</b> of a very long title that would once have been cut "
                    "off at ninety characters, mid-word, in the alert list")
    h = make_command_handler(db, lambda: None, lambda: "")
    text, buttons = h("calerts", "")
    assert isinstance(text, str) and text.startswith("🔔 <b>Alerts</b>") and "('" not in text
    assert "mid-word, in the alert list" in text


def test_card_lists_are_whole_and_folded():
    item = "cover_look: " + "the title text sits over the statue's head " * 6
    out = listed("👀 Look at the stills and cover: 1 note(s)", [item])
    assert out.startswith("<b>👀 Look at the stills and cover: 1 note(s)</b>\n<blockquote expandable>• cover_look")
    assert item in out
    payload = {"title": "T", "narration_script": "x", "caption": "C", "carousel_caption": "S",
               "qa_summary": "🔎 Automated video check: 1 issue(s)\n· beat 2, 0:05-0:09: " + "long finding " * 20,
               "self_healing": {"issues": [{"severity": "info", "card": "cover_look", "issue": "words " * 60 + "end."}]},
               "beat_image_note": "🔍 1 photo(s) from outside the topic's set, fetched for one line each: line 4: cut…",
               "beat_image_credits": [{"beat": 4, "looked": "The photograph shows a gallery " + "and more " * 30 + "end."}]}
    card = TelegramApprovalBot.format_card_text(payload, "run_x")
    assert card.count("<blockquote expandable>• ") >= 3
    assert "words end." in card and "and more end." in card                 # nothing cut mid-word


def test_make_again_says_what_it_does_on_the_runs_own_screen(db, tmp_path):
    topic = db.list_all_topics()[0]["id"]
    db.create_run("run_m", topic)
    db.update_run("run_m", "rejected", master_video_path=str(tmp_path / "m.mp4"))
    text, buttons = control.history_details(db, db.run_key("run_m"))
    assert buttons[0][0]["text"] == "🔁 Make again" and "reason you gave" in text
