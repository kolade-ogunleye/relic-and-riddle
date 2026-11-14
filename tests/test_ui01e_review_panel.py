"""UI-01e: one paged review panel (design docs/design/telegram-clean-chat-2026-11-11.md §6.5, §6.5.1, §6.6; mock-ups
9, 10 and 11).

One video message carries every review waiting, soonest-closing first, "Review k of N" in its caption; paging swaps
the media with editMessageMedia from the video's file_id (one upload per file, ever); five tabs within a 1,024-character
caption (Posts replaces "In full", Pictures the albums); a blocked gate shows a disabled "Blocked by a check"; Approve &
post is one tap with Undo on an item; Reject and Regenerate open the reason step (improve.REASONS, "In my words");
typed input is asked inside the panel; a timeout becomes a Needs-you item with Resend; the panel goes with its last
review. A fake Bot API keeps ids, media, file ids and deletions; nothing calls Telegram."""
import json
import shutil
from datetime import UTC
from pathlib import Path

import pytest
from PIL import Image

from src import config, telegram_bot

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "ui02_review_v2" / "run_gol_gumbaz_whispering_gallery_1793278143"


@pytest.fixture(autouse=True)
def clean_chat(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", True, raising=False)
    monkeypatch.setattr(config, "PUSH_ALERT_SECONDS", 8.0, raising=False)
    monkeypatch.setattr(config, "REVIEW_UNDO_MINUTES", 10.0, raising=False)
    monkeypatch.setattr(config, "REVIEW_APPROVE_UNDO_LEAD_MINUTES", 15.0, raising=False)
    monkeypatch.setattr(telegram_bot, "_video_dimensions", lambda path: {"width": 720, "height": 1280})


class FakeTelegram:
    """Messages with their media, captions and keyboards; a file sent as a file gets a file id; deletions kept."""

    def __init__(self):
        self.next_id, self.calls, self.messages, self.deleted, self.uploads = 100, [], {}, set(), []

    def _file_ids(self, mid, kinds):
        out = {}
        if "video" in kinds:
            out["video"] = {"file_id": f"vid-{mid}", "cover": [{"file_id": f"cov-{mid}"}]}
        if "photo" in kinds:
            out["photo"] = [{"file_id": f"pic-{mid}-s"}, {"file_id": f"pic-{mid}"}]
        return out

    def __call__(self, method, timeout=30, **kw):
        data = dict(kw.get("data") or {})
        files = dict(kw.get("files") or {})
        self.calls.append((method, data))
        if files:
            self.uploads.append((method, sorted(files)))
        if method in ("sendMessage", "sendVideo", "sendPhoto"):
            self.next_id += 1
            self.messages[self.next_id] = {**data, "kind": method}
            return {"message_id": self.next_id, **self._file_ids(self.next_id, {method[4:].lower()})}
        if method.startswith("editMessage"):
            mid = int(data["message_id"])
            if mid in self.deleted or mid not in self.messages:
                raise RuntimeError(f"Telegram {method} failed: HTTP 400 Bad Request: message to edit not found")
            if method == "editMessageMedia":
                media = json.loads(data["media"])
                self.messages[mid] = {**self.messages[mid], "caption": media.get("caption"), "media": media,
                                      "reply_markup": data.get("reply_markup")}
                return {"message_id": mid, **self._file_ids(mid, {media["type"]} if files else set())}
            self.messages[mid] = {**self.messages[mid], **data}
            return {"message_id": mid}
        if method == "deleteMessage":
            self.deleted.add(int(data["message_id"]))
        return True


def make_bot(db, monkeypatch):
    from src import notice_conditions
    from src.telegram_bot import TelegramApprovalBot
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="100", approver_ids={"7"})
    fake = FakeTelegram()
    monkeypatch.setattr(bot, "_api", fake)
    monkeypatch.setattr(bot, "_queue_navigation", lambda work: work() or True)
    monkeypatch.setattr(bot, "_delete_soon", bot.delete_message)
    monkeypatch.setattr(notice_conditions, "current", lambda db_: [])
    bot.layout.timer = lambda *a, **k: type("T", (), {"start": lambda self: None, "daemon": True})()
    bot.layout.tick(force=True)          # the chat as it stands: the pinned notifications panel exists
    fake.calls.clear()
    fake.uploads.clear()
    return bot, fake


def a_review(db, tmp_path, monkeypatch, run_id, *, passing=True, stories=2, closes=None):
    """The real development run's records (tests/fixtures/ui02_review_v2) in a folder of its own, with a stand-in
    video and cover (bytes only); `passing` turns its two failed checks to pass (an approvable version)."""
    from src import release
    from src.telegram_state import initialize
    initialize(db)
    root = tmp_path / run_id
    shutil.copytree(FIXTURE, root)
    report = json.loads((root / "release_report.json").read_text(encoding="utf-8"))
    if passing:
        report["checks"] = [{**c, "status": "pass"} for c in report["checks"]]
    (root / "release_report.json").write_text(json.dumps(report), encoding="utf-8")
    (root / "master.mp4").write_bytes(b"offline review video " + run_id.encode())
    Image.new("RGB", (20, 20), "blue").save(root / "thumbnail_cover.jpg")
    (root / "stories").mkdir(exist_ok=True)
    for n in range(1, stories + 1):
        Image.new("RGB", (9, 16), "red").save(root / "stories" / f"story_{n:02d}.jpg")
    reports = getattr(release, "_ui01e_reports", {})
    reports[str(root)] = report
    release._ui01e_reports = reports
    monkeypatch.setattr(release, "write_release_report",
                        lambda run, dry_run=False: release._ui01e_reports[str(Path(run["master_video_path"]).parent)])
    payload = json.loads((root / "script_metadata.json").read_text(encoding="utf-8"))
    topic = db.list_all_topics()[0]["id"]
    payload["topic_id"] = topic
    db.create_run(run_id, topic)
    db.update_run(run_id, "pending_approval", script_payload=payload, master_video_path=root / "master.mp4",
                  thumbnail_cover_path=root / "thumbnail_cover.jpg")
    return root, payload


def deliver(bot, db, root, payload, run_id, closes=None):
    mid = bot.send_approval_request(root / "master.mp4", root / "thumbnail_cover.jpg", payload, run_id)
    if closes is not None:
        with db._conn() as conn:
            conn.execute("UPDATE approvals SET expires_at=? WHERE run_id=?", (closes, run_id))
    return mid


def tap(bot, data, mid, qid="q"):
    return bot.review.tap({"id": qid, "data": data, "from": {"id": 7, "first_name": "Op"},
                           "message": {"message_id": mid, "chat": {"id": 100}}}, None)


def rows(fake, mid):
    markup = fake.messages[mid].get("reply_markup")
    return json.loads(markup)["inline_keyboard"] if markup else []


def labels(fake, mid):
    return [[b["text"] for b in row] for row in rows(fake, mid)]


def caption(fake, mid):
    return fake.messages[mid].get("caption") or ""


def answers(fake):
    return [d.get("text") for m, d in fake.calls if m == "answerCallbackQuery"]


def items(db, prefix=""):
    from src import notifications
    return [i for i in notifications.open_items(db) if i["dedupe_key"].startswith(prefix)]


# ------------------------------------------------------------------------------------------------ delivery
def test_a_review_is_one_silent_video_message_with_its_summary_face_and_nothing_else(db, tmp_path, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    root, payload = a_review(db, tmp_path, monkeypatch, "rv1")
    mid = deliver(bot, db, root, payload, "rv1")
    sent = [(m, d) for m, d in fake.calls if m.startswith("send")]
    assert [m for m, _ in sent] == ["sendVideo"]                        # no album, no card, no "In full", no line
    data = sent[0][1]
    assert data["disable_notification"] == "true" and data["supports_streaming"] == "true"
    assert ("sendVideo", ["cover", "video"]) in fake.uploads              # the video and its cover, uploaded once
    assert db.get_telegram_card("layout:review")["message_id"] == mid
    assert caption(fake, mid).startswith("<b>🎬 Review 1 of 1</b>\n")
    assert labels(fake, mid) == [["Approve & post"], ["Edit text", "Regenerate"], ["🎨 Covers", "Reject"],
                                 ["Script", "Posts"], ["Checks", "Pictures · 2"]]
    approve = rows(fake, mid)[0][0]
    assert approve["style"] == "success" and approve["callback_data"] == f"rva:{db.run_key('rv1')}"
    assert rows(fake, mid)[2][1]["style"] == "danger"                     # Reject: danger, last, on the right
    assert (db.get_approval("rv1") or {})["telegram_message_id"] == mid
    record = bot.review.record("rv1")
    assert record["video_id"] == f"vid-{mid}" and record["cover_id"] == f"cov-{mid}"


def test_a_second_review_raises_the_count_in_place_and_the_pager_is_soonest_closing_first(db, tmp_path, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    r1, p1 = a_review(db, tmp_path, monkeypatch, "late")
    r2, p2 = a_review(db, tmp_path, monkeypatch, "soon")
    mid = deliver(bot, db, r1, p1, "late", closes=4_000_000_000)
    sends = len([m for m, _ in fake.calls if m.startswith("send")])
    assert deliver(bot, db, r2, p2, "soon", closes=3_000_000_000) == mid
    assert len([m for m, _ in fake.calls if m.startswith("send")]) == sends   # no new message
    assert bot.review.pager() == ["soon", "late"]                            # the window that closes first, first
    view = bot.review.view()
    assert view["run"] == "late"                                             # the page stays where it is
    bot.review.draw()
    assert caption(fake, mid).startswith("<b>🎬 Review 2 of 2</b>")
    assert labels(fake, mid)[-1] == ["◀ Prev"]                                # only the arrows that apply


def test_paging_swaps_the_media_in_place_and_never_uploads_a_video_twice(db, tmp_path, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    for run, closes in (("a1", 1), ("a2", 2), ("a3", 3)):
        root, payload = a_review(db, tmp_path, monkeypatch, run)
        mid = deliver(bot, db, root, payload, run, closes=3_000_000_000 + closes)
    tap(bot, f"rvp:{db.run_key('a1')}", mid)
    assert caption(fake, mid).startswith("<b>🎬 Review 1 of 3</b>")
    assert labels(fake, mid)[-1] == ["Next ▶", "⏭"]                          # review 1 of 3 (design §6.5)
    tap(bot, f"rvp:{db.run_key('a3')}", mid)
    assert caption(fake, mid).startswith("<b>🎬 Review 3 of 3</b>") and labels(fake, mid)[-1] == ["⏮", "◀ Prev"]
    uploads_before = list(fake.uploads)
    tap(bot, f"rvp:{db.run_key('a1')}", mid)                                  # back to a page already shown
    tap(bot, f"rvp:{db.run_key('a3')}", mid)
    assert fake.uploads == uploads_before                                     # from file_id: nothing uploaded
    medias = [json.loads(d["media"]) for m, d in fake.calls if m == "editMessageMedia"]
    assert medias[-1]["media"] == bot.review.record("a3")["video_id"] and medias[-1]["cover"].startswith("cov-")
    videos = [names for method, names in fake.uploads if "video" in names]
    assert len(videos) == 2                     # one upload per review shown (a2 was never shown), never twice
    assert len([m for m, _ in fake.calls if m.startswith("send")]) == 1       # one message for all three


# ------------------------------------------------------------------------------------------------ tabs
@pytest.mark.parametrize("passing", [True, False])
def test_every_face_of_a_real_run_fits_a_caption_and_posts_says_every_word(db, tmp_path, monkeypatch, passing):
    from src import publishable
    from src.review_panel import CAPTION_LIMIT, visible_len
    bot, fake = make_bot(db, monkeypatch)
    root, payload = a_review(db, tmp_path, monkeypatch, "real", passing=passing)
    mid = deliver(bot, db, root, payload, "real")
    key = db.run_key("real")
    faces = {}
    for tab in ("sum", "scr", "pst", "chk", "pic"):
        tap(bot, f"rvt:{key}:{tab}", mid)
        pages, seen = [caption(fake, mid)], 1
        while any(b["text"] == "▶" for row in rows(fake, mid) for b in row):
            seen += 1
            tap(bot, f"rvi:{key}:{seen}", mid)
            pages.append(caption(fake, mid))
        faces[tab] = pages
        for text in pages:
            assert visible_len(text) <= CAPTION_LIMIT, (tab, visible_len(text))
    shown = " ".join(faces["pst"])
    copy = publishable.load_record(db, "real")["copy"]
    from src.notifications import plain
    for word in copy["reel.caption"].split():
        assert word in plain(shown)                                            # word for word, across its pages
    assert "Pictures 1 of 2" in faces["pic"][0] and "Story frame 1 of 2" in faces["pic"][0]
    for word in payload["narration_script"].split():
        assert word in plain(" ".join(faces["scr"]))                           # the whole narration
    if not passing:
        assert plain(faces["chk"][0]).split("\n\n", 1)[1].startswith("⛔ Blocked")   # what blocks, first


def test_pictures_put_each_illustration_beside_its_reference_then_the_story_frames(db, tmp_path, monkeypatch):
    from src.review_panel import pictures_of
    root = tmp_path / "pics"
    (root / "stories").mkdir(parents=True)
    Image.new("RGB", (40, 60), "green").save(root / "art.jpg")
    Image.new("RGB", (80, 60), "white").save(root / "ref.jpg")
    Image.new("RGB", (9, 16), "red").save(root / "stories" / "story_01.jpg")
    payload = {"picture_fit": {"generated": [{"beat": 8, "path": str(root / "art.jpg"), "line": "They rocked them upright.",
                                              "references": [{"path": str(root / "ref.jpg"), "title": "Routledge, 1914",
                                                              "licence": "public domain"}]}]}}
    found = pictures_of(payload, root)
    assert len(found) == 2
    with Image.open(found[0]["path"]) as pair:
        assert pair.height == 720 and pair.width > 720                       # one side-by-side picture
    assert "“They rocked them upright.”" in found[0]["caption"] and "Routledge, 1914 (public domain)" in found[0]["caption"]
    assert found[1]["caption"].startswith("Story frame 1 of 1")


def test_a_blocked_gate_shows_a_disabled_blocked_by_a_check_and_approve_cannot_decide(db, tmp_path, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    root, payload = a_review(db, tmp_path, monkeypatch, "blk", passing=False)
    mid = deliver(bot, db, root, payload, "blk")
    first = rows(fake, mid)[0]
    assert [b["text"] for b in first] == ["Blocked by a check"] and first[0]["disabled"] is True
    assert not any(b["callback_data"].startswith("rva:") for row in rows(fake, mid) for b in row)
    assert "Can't be approved yet" in caption(fake, mid)
    assert tap(bot, f"rva:{db.run_key('blk')}", mid) is None
    assert (db.get_approval("blk") or {})["decision"] == "pending"
    tap(bot, f"rvb:{db.run_key('blk')}", mid)
    assert answers(fake)[-1].startswith("A check blocks this version")


# ------------------------------------------------------------------------------------------------ decisions
def test_approve_is_one_tap_the_review_leaves_and_the_item_keeps_undo(db, tmp_path, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    root, payload = a_review(db, tmp_path, monkeypatch, "ap")
    mid = deliver(bot, db, root, payload, "ap")
    assert tap(bot, f"rva:{db.run_key('ap')}", mid) == "approved"
    assert (db.get_approval("ap") or {})["decision"] == "approved"
    assert mid in fake.deleted and db.get_telegram_card("layout:review") is None   # the pager is empty: it goes
    item = items(db, "posted:ap")[0]
    assert item["section"] == "info" and item["title"] == "Approved" and item["emoji"] == "✅"
    assert item["actions"] == [{"text": "Undo approval", "callback_data": f"rvua:{db.run_key('ap')}"}]
    assert "Undo until" in item["body"]
    # the UI-02 rule is kept: a decision on the panel waits out the Undo window before the worker acts
    assert telegram_bot.v2_card_decision(db, "ap", db.get_approval("ap"))
    # Undo from the item: the review is back in the pager, in a panel of its own again
    tap(bot, f"rvua:{db.run_key('ap')}", mid)
    assert (db.get_approval("ap") or {})["decision"] == "pending"
    new = db.get_telegram_card("layout:review")["message_id"]
    assert new != mid and caption(fake, new).startswith("<b>🎬 Review 1 of 1</b>")
    assert items(db, "posted:ap") == []
    assert tap(bot, f"rva:{db.run_key('ap')}", new) == "approved"         # the restored Approve decides


def test_a_claimed_post_now_drops_undo_and_a_booked_slot_keeps_it_until_15_minutes_before(db, tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    bot, _fake = make_bot(db, monkeypatch)
    root, payload = a_review(db, tmp_path, monkeypatch, "cl")
    mid = deliver(bot, db, root, payload, "cl")
    tap(bot, f"rva:{db.run_key('cl')}", mid)
    bot.close_undo("cl")                                                     # the worker claimed it: post now
    assert items(db, "posted:cl")[0]["actions"] == [] and "Undo until" not in items(db, "posted:cl")[0]["body"]
    root2, payload2 = a_review(db, tmp_path, monkeypatch, "bk")
    mid2 = deliver(bot, db, root2, payload2, "bk")
    tap(bot, f"rva:{db.run_key('bk')}", mid2)
    slot = datetime.now(UTC) + timedelta(hours=3)
    db.add_scheduled_post("bk", "reel", slot.isoformat(), [])
    bot.review.booked("bk")
    item = items(db, "posted:bk")[0]
    assert item["actions"] and item["body"].startswith("Posts ")
    assert abs(bot.review.undo_until("bk") - (slot.timestamp() - 15 * 60)) < 1
    bot.review.tick(slot.timestamp() - 14 * 60)                              # 14 minutes before the slot
    assert items(db, "posted:bk")[0]["actions"] == []


def test_reject_opens_the_reason_step_and_a_reason_rejects_with_a_note_and_undo(db, tmp_path, monkeypatch):
    from src.improve import REASONS
    bot, fake = make_bot(db, monkeypatch)
    root, payload = a_review(db, tmp_path, monkeypatch, "rj")
    mid = deliver(bot, db, root, payload, "rj")
    key = db.run_key("rj")
    tap(bot, f"rvr:{key}", mid)
    text = caption(fake, mid)
    assert "❌ <b>Reject this version?</b>" in text and "Expires in 5 min" in text
    chips = [b for row in rows(fake, mid)[:-1] for b in row]
    assert [b["text"] for b in chips] == [*(v for k, v in REASONS.items() if k != "other"), "In my words"]
    assert all(len(row) == 2 for row in rows(fake, mid)[:-1]) and labels(fake, mid)[-1] == ["← Keep it"]
    tap(bot, f"rvk:{key}", mid)                                              # ← Keep it restores the face
    assert labels(fake, mid)[0] == ["Approve & post"]
    tap(bot, f"rvr:{key}", mid)
    assert tap(bot, f"rvs:{key}:hook", mid) == "rejected"
    assert json.loads(db.get_setting("decision_reason:rj"))["reason"] == "hook"
    assert "❌ <b>Rejected</b> · Hook / story" in caption(fake, mid) and labels(fake, mid) == [["Done", "↩️ Undo"]]
    assert bot._awaiting[0] == "rvin"                                         # a note can be typed for 2 minutes
    item = items(db, "rejected:rj")[0]
    assert item["actions"][0]["callback_data"] == f"rvua:{key}" and "Hook / story" in item["body"]
    tap(bot, f"rvu:{key}", mid)                                              # Undo on the face
    assert (db.get_approval("rj") or {})["decision"] == "pending" and labels(fake, mid)[0] == ["Approve & post"]


def test_the_reason_step_restores_itself_after_five_minutes(db, tmp_path, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    root, payload = a_review(db, tmp_path, monkeypatch, "ex")
    mid = deliver(bot, db, root, payload, "ex")
    clock = [bot.review.clock()]
    bot.review.clock = lambda: clock[0]                                      # the panel's own clock, held still
    tap(bot, f"rvr:{db.run_key('ex')}", mid)
    bot.review.tick(clock[0] + 299)
    assert "Reject this version?" in caption(fake, mid)
    bot.review.tick(clock[0] + 301)
    assert labels(fake, mid)[0] == ["Approve & post"]


def test_regenerate_has_its_estimate_and_redo_without_a_note(db, tmp_path, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    monkeypatch.setattr(telegram_bot, "typical_run_seconds", lambda *a, **k: 18 * 60)
    root, payload = a_review(db, tmp_path, monkeypatch, "rg")
    mid = deliver(bot, db, root, payload, "rg")
    key = db.run_key("rg")
    tap(bot, f"rvg:{key}", mid)
    assert "🔄 <b>Make a new version?</b>" in caption(fake, mid) and "About 18 min." in caption(fake, mid)
    assert labels(fake, mid)[-2:] == [["Redo without a note"], ["← Keep it"]]
    assert tap(bot, f"rvn:{key}", mid) == "regenerate"
    assert (db.get_approval("rg") or {})["decision"] == "regenerate" and mid in fake.deleted


# ------------------------------------------------------------------------------------------------ typed input
def typed(bot, text, message_id=900, handler=None):
    update = {"update_id": message_id, "message": {"message_id": message_id, "text": text, "chat": {"id": 100},
                                                   "from": {"id": 7}, "date": int(bot.review.clock())}}
    bot._process_update(update, handler or (lambda c, a: ""))


def test_in_my_words_rejects_with_the_typed_text_and_the_message_leaves(db, tmp_path, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    root, payload = a_review(db, tmp_path, monkeypatch, "iw")
    mid = deliver(bot, db, root, payload, "iw")
    key = db.run_key("iw")
    tap(bot, f"rvr:{key}", mid)
    tap(bot, f"rvs:{key}:other", mid)
    assert "✍️ <b>In your words</b>" in caption(fake, mid) and (db.get_approval("iw") or {})["decision"] == "pending"
    typed(bot, "The hook promises a secret the film never tells.", 901)
    assert 901 in fake.deleted
    assert (db.get_approval("iw") or {})["decision"] == "rejected"
    reason = json.loads(db.get_setting("decision_reason:iw"))
    assert reason["reason"] == "other" and reason["text"] == "The hook promises a secret the film never tells."


def test_edit_text_is_asked_on_posts_and_the_answer_goes_to_edittext(db, tmp_path, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    root, payload = a_review(db, tmp_path, monkeypatch, "ed")
    mid = deliver(bot, db, root, payload, "ed")
    key = db.run_key("ed")
    tap(bot, f"rve:{key}", mid)
    assert "Review 1 of 1 · Posts" in caption(fake, mid) and "✏️ <b>Edit the post text</b>" in caption(fake, mid)
    assert labels(fake, mid) == [["Copy current text"], ["Cancel"]]
    seen = []

    def handler(command, args):
        seen.append((command, args, caption(fake, mid)))
        return "❌ Not changed: the new caption failed the fact check."
    typed(bot, "A new caption for the Reel.", 902, handler)
    assert seen[0][:2] == ("edittext", f"{key} A new caption for the Reel.")
    assert "✅ Got it: ‘A new caption for the Reel.’" in seen[0][2]          # the panel acknowledged first
    assert 902 in fake.deleted
    assert "Not changed: the new caption failed the fact check." in caption(fake, mid)   # the result, on Posts


def test_a_command_while_armed_runs_and_the_input_keeps_waiting_a_photo_is_refused(db, tmp_path, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    root, payload = a_review(db, tmp_path, monkeypatch, "cm")
    mid = deliver(bot, db, root, payload, "cm")
    tap(bot, f"rve:{db.run_key('cm')}", mid)
    ran = []
    monkeypatch.setattr(bot, "_run_command", lambda handler, command, args, target=None: ran.append(command))
    typed(bot, "/queue", 903)
    assert ran == ["queue"] and bot.review.armed()
    mid = db.get_telegram_card("layout:review")["message_id"]   # the first dashboard re-posts the panel under it
    photo = {"update_id": 904, "message": {"message_id": 904, "photo": [{"file_id": "x"}], "chat": {"id": 100},
                                          "from": {"id": 7}, "date": int(bot.review.clock())}}
    bot._process_update(photo, lambda c, a: "")
    assert 904 in fake.deleted and "That was a photo; type the text instead." in caption(fake, mid)
    assert bot.review.armed()


def test_one_input_at_a_time_a_bare_ask_takes_it_and_the_panel_says_so(db, tmp_path, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    root, payload = a_review(db, tmp_path, monkeypatch, "one")
    mid = deliver(bot, db, root, payload, "one")
    tap(bot, f"rve:{db.run_key('one')}", mid)
    monkeypatch.setattr(bot, "_run_command", lambda *a, **k: None)
    monkeypatch.setattr(bot, "start_operator_job", lambda *a, **k: None)
    typed(bot, "/ask", 905)
    assert bot._awaiting[0] == "ask" and "Your text is now for /ask" in caption(fake, mid)


# ------------------------------------------------------------------------------------------------ endings
def test_a_timeout_leaves_the_pager_becomes_a_needs_you_item_and_the_panel_goes(db, tmp_path, monkeypatch):
    from src import notice_conditions
    real = notice_conditions.current
    bot, fake = make_bot(db, monkeypatch)
    root, payload = a_review(db, tmp_path, monkeypatch, "to")
    mid = deliver(bot, db, root, payload, "to")
    monkeypatch.setattr(notice_conditions, "current", real)                  # the real conditions from here
    db.update_approval_decision("to", "timeout", decided_by="system")
    db.update_run("to", "timeout")
    bot.dismiss_approval_buttons("to")
    bot.timeout_notice("to")
    assert mid in fake.deleted and db.get_telegram_card("layout:review") is None
    notice_conditions.sync(db)
    item = items(db, "fail:to")[0]                                           # under the run's key: one item
    assert item["section"] == "needs" and item["title"] == "Review timed out" and item["emoji"] == "⏰"
    assert item["actions"] == [{"text": "Resend for review", "callback_data": f"resend:{db.run_key('to')}"},
                               {"text": "Details", "callback_data": f"chist:{db.run_key('to')}"}]
    assert "nothing was posted. The render is kept" in item["body"]
    assert items(db, "cond:timed-out") == []                                  # not a second item for it
    assert [i["dedupe_key"] for i in items(db) if "to" == (i.get("run_id") or "")] == ["fail:to"]
    db.update_run("to", "pending_approval")                                   # resent: it resolves itself
    notice_conditions.sync(db)
    assert items(db, "fail:to") == []
    assert not [d for m, d in fake.calls if m == "sendMessage" and "No decision" in d.get("text", "")]


def test_a_timeout_is_said_through_the_senders_path_and_replaces_the_runs_failure_item(db, tmp_path, monkeypatch):
    """Round 2's sender rule (UI-01a): the timeout goes through notifications.say (bot.notify) under fail:<run>
    with replace, so a run has one failure item, and nothing is sent as a message of its own."""
    from src import notifications
    bot, fake = make_bot(db, monkeypatch)
    said = []
    real_say = notifications.say
    monkeypatch.setattr(notifications, "say", lambda send, text, **kw: said.append(kw) or real_say(send, text, **kw))
    root, payload = a_review(db, tmp_path, monkeypatch, "tr")
    deliver(bot, db, root, payload, "tr")
    notifications.post(db, notifications.NEEDS, "fail:tr", "Production failed", "an earlier failure", run="tr")
    db.update_approval_decision("tr", "timeout", decided_by="system")
    db.update_run("tr", "timeout")
    fake.calls.clear()
    bot.timeout_notice("tr")
    assert said and said[-1]["key"] == "fail:tr" and said[-1]["replace"] is True and said[-1]["group"] == "needs"
    mine = items(db, "fail:tr")
    assert len(mine) == 1 and mine[0]["title"] == "Review timed out" and mine[0]["subject"] == db.run_label("tr")
    # only its push alert (one line that rings, deleted after 8 s) is sent: no message of its own
    assert [d.get("text", "")[:1] for m, d in fake.calls if m.startswith("send")] == ["🔔"]


def test_a_review_not_delivered_is_a_needs_you_item_said_through_the_senders_path(db, tmp_path, monkeypatch):
    bot, fake = make_bot(db, monkeypatch)
    root, payload = a_review(db, tmp_path, monkeypatch, "nd")
    monkeypatch.setattr(bot.review, "deliver", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Telegram is down")))
    assert deliver(bot, db, root, payload, "nd") is None
    item = items(db, "undelivered:nd")[0]
    assert item["section"] == "needs" and item["title"] == "Review not delivered" and item["emoji"] == "⚠️"
    assert item["actions"] == [{"text": "Resend for review", "callback_data": f"resend:{db.run_key('nd')}"}]
    assert [d.get("text", "")[:1] for m, d in fake.calls if m.startswith("send")] == ["🔔"]   # its push alert only


def test_show_reviews_on_the_reviews_item_turns_the_panel_to_the_soonest_closing(db, tmp_path, monkeypatch):
    from src import notice_conditions
    bot, fake = make_bot(db, monkeypatch)
    for run, closes in (("s1", 9), ("s2", 1)):
        root, payload = a_review(db, tmp_path, monkeypatch, run)
        mid = deliver(bot, db, root, payload, run, closes=3_000_000_000 + closes)
    assert notice_conditions._reviews_action(db) == {"text": "🎬 Show reviews", "callback_data": "rvshow:"}
    assert bot.review.view()["run"] == "s1"
    tap(bot, "rvshow:", mid)
    assert bot.review.view()["run"] == "s2" and caption(fake, mid).startswith("<b>🎬 Review 1 of 2</b>")


# ------------------------------------------------------------------------------------------------ conventions
def test_every_review_callback_is_a_panel_kind_and_every_label_and_callback_fits(db, tmp_path, monkeypatch):
    from src.review_panel import REVIEW_ACTIONS
    from src.telegram_ui import PANEL_KINDS, SCREENS, long_labels
    assert REVIEW_ACTIONS <= PANEL_KINDS and "crev" in SCREENS
    bot, fake = make_bot(db, monkeypatch)
    root, payload = a_review(db, tmp_path, monkeypatch, "cv")
    mid = deliver(bot, db, root, payload, "cv")
    key = db.run_key("cv")
    for data in (None, f"rvr:{key}", f"rvk:{key}", f"rvg:{key}", f"rvk:{key}", f"rvt:{key}:pic", f"rvt:{key}:chk",
                 f"rve:{key}"):
        if data:
            tap(bot, data, mid)
        keyboard = rows(fake, mid)
        assert long_labels(keyboard) == []
        assert not any(b["text"] == "🏠 Dashboard" for row in keyboard for b in row)     # D1: no home on a panel
        for row in keyboard:
            for b in row:
                assert len(b.get("callback_data", "").encode()) <= 64
                assert b.get("callback_data", "").partition(":")[0] in PANEL_KINDS | {"ccov", ""}


def test_with_the_switch_off_the_earlier_review_card_is_sent(db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_CLEAN_CHAT", False, raising=False)
    bot, fake = make_bot(db, monkeypatch)
    root, payload = a_review(db, tmp_path, monkeypatch, "off")
    deliver(bot, db, root, payload, "off")
    assert db.get_telegram_card("layout:review") is None
    assert "sendMessage" in [m for m, _ in fake.calls]                        # the review card of its own


# ------------------------------------------------------------------------------------------------ one renderer, one lock order
def test_a_process_that_is_not_the_renderer_only_records_the_review_and_the_renderer_draws_it(db, tmp_path, monkeypatch):
    from src import telegram_layout
    bot, fake = make_bot(db, monkeypatch)
    telegram_layout.release(db, bot.layout.owner)
    assert telegram_layout.acquire(db, "the-daemon")                         # another process renders the chat
    root, payload = a_review(db, tmp_path, monkeypatch, "wk")
    deliver(bot, db, root, payload, "wk")
    assert not [m for m, _ in fake.calls if m.startswith("send")]            # the worker sends nothing
    assert bot.review.pager() == ["wk"] and db.get_setting("review-panel-redraw")
    telegram_layout.release(db, "the-daemon")
    bot.layout.take()                                                        # the renderer's next tick
    assert bot.review.redraw_if_asked()
    mid = db.get_telegram_card("layout:review")["message_id"]
    assert (db.get_approval("wk") or {})["telegram_message_id"] == mid
    assert tap(bot, f"rva:{db.run_key('wk')}", mid) == "approved"            # its taps decide


def test_the_panel_never_calls_the_layout_or_the_dashboard_while_it_holds_its_lock(db, tmp_path, monkeypatch):
    bot, _fake = make_bot(db, monkeypatch)
    held = []
    monkeypatch.setattr(bot.layout, "kick", lambda: held.append(("kick", bot.review._depth)))
    real = bot.show_dashboard
    monkeypatch.setattr(bot, "show_dashboard", lambda *a, **k: held.append(("dashboard", bot.review._depth)) or real(*a, **k))
    root, payload = a_review(db, tmp_path, monkeypatch, "lk")
    mid = deliver(bot, db, root, payload, "lk")
    bot._awaiting = ("ask", bot.review.clock() + 600)                         # an input waiting elsewhere
    tap(bot, f"rve:{db.run_key('lk')}", mid)                                 # moves it: the dashboard says so
    tap(bot, f"rva:{db.run_key('lk')}", mid)
    assert ("dashboard", 0) in held and ("kick", 0) in held
    assert all(depth == 0 for _, depth in held)


def test_undoing_a_booked_approve_runs_postings_cancel_outside_the_panels_lock(db, tmp_path, monkeypatch):
    """Posting's cancel draws the dashboard (the dashboard lock, then the layout's): never under the panel's lock,
    which the renderer's tick takes inside Layout._lock (one lock order, round 2)."""
    from datetime import datetime, timedelta
    bot, fake = make_bot(db, monkeypatch)
    root, payload = a_review(db, tmp_path, monkeypatch, "cb")
    mid = deliver(bot, db, root, payload, "cb")
    tap(bot, f"rva:{db.run_key('cb')}", mid)
    db.add_scheduled_post("cb", "reel", (datetime.now(UTC) + timedelta(hours=3)).isoformat(), [])
    monkeypatch.setattr(telegram_bot, "undo_minutes", lambda: 0.0)            # past UI-02's window: the booking
    seen = []

    def handler(command, args):
        seen.append((command, bot.review._depth, bot.review._lock._is_owned()))
        return "Booking cancelled."
    bot.review.tap({"id": "qb", "data": f"rvua:{db.run_key('cb')}", "from": {"id": 7},
                    "message": {"message_id": mid, "chat": {"id": 100}}}, handler)
    assert seen == [("cancelpost", 0, False)]
    assert items(db, "posted:cb") == [] and answers(fake)[-1] == "Booking cancelled."


def test_a_tap_answered_by_a_process_that_is_not_the_renderer_draws_nothing(db, tmp_path, monkeypatch):
    from src import telegram_layout
    bot, fake = make_bot(db, monkeypatch)
    root, payload = a_review(db, tmp_path, monkeypatch, "nr")
    mid = deliver(bot, db, root, payload, "nr")
    telegram_layout.release(db, bot.layout.owner)
    assert telegram_layout.acquire(db, "the-daemon")
    fake.calls.clear()
    tap(bot, f"rvt:{db.run_key('nr')}:scr", mid)
    assert not [m for m, _ in fake.calls if m.startswith(("send", "edit", "delete"))]
    assert db.get_setting("review-panel-redraw")                              # the renderer draws it


# ------------------------------------------------------------------------------------------------ round 3 (chain)
REAL_CONDITIONS = __import__("src.notice_conditions", fromlist=["current"]).current   # before any stand-in


def item_tap(bot, item, callback, handler=None):
    from src import notifications
    bot._notifications_tap({"id": f"it{item['id']}", "data": f"n:{item['id']}:{notifications.action_ref({'callback_data': callback})}",
                            "from": {"id": 7}, "message": {"message_id": 1, "chat": {"id": 100}}}, handler)


def test_undo_approval_from_the_item_closes_it_only_when_undone(db, tmp_path, monkeypatch):
    """Review 2 (UI-01a blocking 1, on this row's item): an Undo that is refused keeps its item; one that works
    closes it. The item's button carries no emoji (design §6.1, mock-up 3: "Undo approval")."""
    from src import notifications
    bot, _fake = make_bot(db, monkeypatch)
    root, payload = a_review(db, tmp_path, monkeypatch, "ua")
    mid = deliver(bot, db, root, payload, "ua")
    assert tap(bot, f"rva:{db.run_key('ua')}", mid) == "approved"
    item = items(db, "posted:ua")[0]
    notifications.set_view(db, 1, item["id"], 1)
    _text, page = notifications.render(db)
    assert page[0][0]["text"] == "Undo approval"
    item_tap(bot, item, f"rvua:{db.run_key('ua')}")
    assert (db.get_approval("ua") or {})["decision"] == "pending" and items(db, "posted:ua") == []
    assert notifications.get(db, item["id"])["state"] == "handled"
    # a second approval whose Undo window has passed: the refusal is said, the item stays
    new = db.get_telegram_card("layout:review")["message_id"]
    assert tap(bot, f"rva:{db.run_key('ua')}", new) == "approved"
    item = items(db, "posted:ua")[0]
    monkeypatch.setattr(config, "REVIEW_UNDO_MINUTES", 0.0, raising=False)
    item_tap(bot, item, f"rvua:{db.run_key('ua')}")
    assert (db.get_approval("ua") or {})["decision"] == "approved"
    assert items(db, "posted:ua") and notifications.get(db, item["id"])["state"] == "open"


def test_the_thirty_minute_reminder_alert_starts_with_its_alarm_clock(db, tmp_path, monkeypatch):
    """Review 1 of UI-01e, non-blocking 2: design §9 and mock-up 3 show "⏰ Review closes 16:40 · <title>"."""
    from src import notice_conditions
    bot, fake = make_bot(db, monkeypatch)
    monkeypatch.setattr(notice_conditions, "current", REAL_CONDITIONS)   # the reviews item is a real condition
    root, payload = a_review(db, tmp_path, monkeypatch, "rm")
    deliver(bot, db, root, payload, "rm")
    notice_conditions.sync(db)
    bot.review.remind("rm", 1_900_000_000.0)
    sent = len(fake.calls)
    bot.layout.push(bot.layout.clock() + 120)
    lines = [d.get("text", "") for m, d in fake.calls[sent:] if m == "sendMessage" and d.get("disable_notification") != "true"]
    assert lines and lines[-1].startswith("⏰ Review closes ") and len(lines[-1]) <= 40
    assert not lines[-1].startswith("🔔")


# ------------------------------------------------------------------------------------------------ port onto 84c3eeb4
# QF-65a: the review's record decides the AI label from the media Approve posts, and the panel states it; QF-81: a
# carousel drawn at its slot is previewed on demand from the panel, drawn in the dashboard.
def _shown_media(root, tmp_path, scene):
    """The run's master shows one picture: a credited photo, or an image_gen illustration (as UI-02's V2 test)."""
    pool = tmp_path / "assets" / "topic"
    pool.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), "gray").save(pool / "01_commons.jpg")
    (pool / "credits.json").write_text(json.dumps({"images": [{"file": "01_commons.jpg", "license": "CC0"}]}),
                                       encoding="utf-8")
    shown = pool / "01_commons.jpg" if scene == "photo" else root / "gap_gen_beat02.jpg"
    if scene == "illustration":
        Image.new("RGB", (8, 8), "red").save(shown)
    (root / "master.composition.json").write_text(json.dumps({"scenes": [str(shown)]}), encoding="utf-8")
    (root / "still_layout.json").write_text(json.dumps({"thumbnail_cover.jpg": {"recipe": {"hero": str(pool / "01_commons.jpg")}}}),
                                            encoding="utf-8")


@pytest.mark.parametrize("scene, on", [("photo", False), ("illustration", True)])
def test_the_panel_states_the_ai_label_the_media_approve_posts_decide(db, tmp_path, monkeypatch, scene, on):
    """Before: the panel's record was made without the master and cover, so the label was on for "no readable media
    record for the video" on every review (and the envelope keeps a label the review showed on), and the panel never
    said it. Now the record is the live card's, Summary says it briefly and Posts says it whole."""
    from src import publishable
    from src.notifications import plain
    from src.review_panel import CAPTION_LIMIT, visible_len
    from src.telegram_bot import AI_HEAD, _ai_label_line
    monkeypatch.setattr(config, "META_IS_AI_GENERATED", False)
    bot, fake = make_bot(db, monkeypatch)
    root, payload = a_review(db, tmp_path, monkeypatch, "ai")
    _shown_media(root, tmp_path, scene)
    payload = {k: v for k, v in payload.items() if k != "picture_fit"}
    db.update_run("ai", "pending_approval", script_payload=payload)
    mid = deliver(bot, db, root, payload, "ai")
    posted = publishable.load_record(db, "ai")
    live = publishable.card_record(payload, db, run_dir=root, topic_id=payload["topic_id"], posting=posted["posting"],
                                   master=root / "master.mp4", cover=root / "thumbnail_cover.jpg")
    assert posted["ai_label"] == live["ai_label"] and posted["ai_label"]["on"] is on
    assert "no readable media record for the video" not in posted["ai_label"]["reasons"]
    summary = caption(fake, mid)
    assert AI_HEAD in summary and visible_len(summary) <= CAPTION_LIMIT
    assert ("AI label on" in summary) is on and ("no AI label" in summary) is not on
    key = db.run_key("ai")
    tap(bot, f"rvt:{key}:pst", mid)
    pages, seen = [caption(fake, mid)], 1
    while any(b["text"] == "▶" for row in rows(fake, mid) for b in row):
        seen += 1
        tap(bot, f"rvi:{key}:{seen}", mid)
        pages.append(caption(fake, mid))
    assert all(visible_len(p) <= CAPTION_LIMIT for p in pages)
    whole = plain(_ai_label_line(posted))
    assert whole.startswith("🤖 AI transparency:") and ("gap_gen_beat02.jpg" in whole) is on
    for word in whole.split():
        assert word in plain(" ".join(pages))                               # whole, word for word
    assert "in the reply" not in summary                                    # the panel has no reply under it


def test_a_carousel_drawn_at_its_slot_is_previewed_from_posts_in_the_dashboard(db, tmp_path, monkeypatch):
    """QF-81's 🖼 Preview carousel (the live card's button, which the Posts text names) is on the panel's Posts tab;
    its preview is drawn in the dashboard, and the review panel's video and caption stay as they were."""
    from src import carousel_lazy
    from src.control import make_command_handler
    from src.notifications import plain
    bot, fake = make_bot(db, monkeypatch)
    dash = bot.show_dashboard("🏛️ home", [[{"text": "x", "callback_data": "cdash:"}]])
    root, payload = a_review(db, tmp_path, monkeypatch, "car")
    (root / carousel_lazy.PLAN).write_text("{}", encoding="utf-8")   # planned: drawn at its slot (QF-81)
    mid = deliver(bot, db, root, payload, "car")
    key = db.run_key("car")
    assert ["🖼 Preview carousel"] not in labels(fake, mid)            # Summary keeps its rows (UI-01e)
    tap(bot, f"rvt:{key}:pst", mid)
    preview = [b for row in rows(fake, mid) for b in row if b["text"] == "🖼 Preview carousel"]
    assert preview == [{"text": "🖼 Preview carousel", "callback_data": f"ccar:{key}"}]
    pages, seen = [caption(fake, mid)], 1
    while any(b["text"] == "▶" for row in rows(fake, mid) for b in row):
        seen += 1
        tap(bot, f"rvi:{key}:{seen}", mid)
        pages.append(caption(fake, mid))
    assert "tap 🖼 Preview carousel" in plain(" ".join(pages))
    stage = root / carousel_lazy.STAGE
    (stage / "carousel").mkdir(parents=True)
    Image.new("RGB", (20, 20), "green").save(stage / carousel_lazy.SHEET)
    Image.new("RGB", (20, 20), "green").save(stage / "carousel" / "carousel_slide_01.jpg")
    drawn = []
    handler = make_command_handler(db, lambda: "Mon 07:00", lambda: "", send_photo=bot.send_photo, run_actions={
        "carpreview": lambda run_id: drawn.append(run_id) or {
            "run_dir": str(root), "slides": {"carousel": [{"name": "carousel_slide_01.jpg"}]},
            "alts": {"carousel": ["A slide."]}}})
    monkeypatch.setattr(bot, "start_operator_job", lambda title, work, **k: work())
    panel_before = dict(fake.messages[mid])
    sends = len([m for m, _ in fake.calls if m.startswith("send")])
    bot._process_update({"callback_query": {"id": "cq-car", "from": {"id": 7}, "data": preview[0]["callback_data"],
                                            "message": {"message_id": mid, "chat": {"id": 100}}}}, handler)
    assert drawn == ["car"]
    media = [d for m, d in fake.calls if m == "editMessageMedia" and int(d["message_id"]) == dash]
    assert media and json.loads(media[-1]["media"])["caption"].startswith("🖼 <b>Carousel preview</b>")
    assert fake.messages[mid] == panel_before                         # the review panel is never drawn over
    assert len([m for m, _ in fake.calls if m.startswith("send")]) == sends
