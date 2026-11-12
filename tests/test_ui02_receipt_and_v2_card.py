"""UI-02: a truthful publish receipt, and a V2 review card that shows only the run's own values and is safe to use.

Receipt: ✅ only when every attempted platform posted, ⚠️ "Posted with problems" when some did not, ❌ when none
did; a failure as "❌ <platform>: <plain reason>"; only what posted is named; the Short only when it is attempted;
"Post now?" names only what will be attempted. V2 card: every value from the run's files (a real development run's
records, tests/fixtures/ui02_review_v2), the live card instead when one is missing, the live card when the rich
card fails (the video kept), an Undo that never reopens a decision already acted on, V2-only buttons that never
become a decision with the flag off, the expired-card sweep that runs, and improve.REASONS as the reject reasons.
"""
import json
import logging
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from src import config, telegram_bot
from src.telegram_bot import PublishReceipt, TelegramApprovalBot
from src.telegram_state import initialize, put, review_fingerprint

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "ui02_review_v2" / "run_gol_gumbaz_whispering_gallery_1793278143"
PLACEHOLDERS = ("9 distinct", "10 beats", "Cleared", "$0.41", "Gemini 2.5 Pro", "Archivist", "Ancient Winds", "rc70",
                "made just now", "Ancient discoveries that defy", "What do you think was the true purpose",
                "Picture checks passed against narration", "Voice and audio pacing verified", "52 s", "**")


# ---------------------------------------------------------------------------------------------- publish receipt
class FakeBot:
    def __init__(self):
        self.sent, self.edits, self.notes = [], [], []

    def notify(self, text, buttons=None, group=None, silent=False, **kw):
        if buttons:
            self.sent.append(text)
            return 4242
        self.notes.append(text)
        return True

    def edit_message_text(self, mid, text, buttons=None):
        self.edits.append(text)
        return True

    def create_publish_receipt(self, run_id, title, platforms=None, initial=None):
        return PublishReceipt(self, run_id, title, platforms=platforms, initial=initial)

    def stamp_review_outcome(self, run_id, text):
        self.stamp = text


def _final(bot):
    return (bot.edits or bot.sent)[-1]


def test_receipt_all_ok_is_a_tick_and_names_each_platform():
    bot = FakeBot()
    r = PublishReceipt(bot, "r1", "Gol <Gumbaz>", platforms=["instagram", "facebook", "youtube"],
                       initial={"instagram": {"status": "live", "url": "https://i/1"}, "facebook": {"status": "live"}})
    r.update_platform("youtube", "live", url="https://y/2")
    r.finalize()
    text = _final(bot)
    assert text.startswith("✅ <b>Posted</b> · <b>Gol &lt;Gumbaz&gt;</b>")
    assert "✅ Instagram Reel" in text and "✅ Facebook Reel: live" in text and "✅ YouTube Short" in text
    assert bot.notes[-1] == "📮 Gol &lt;Gumbaz&gt; posted · Instagram Reel, Facebook Reel and YouTube Short"


def test_receipt_one_failed_is_posted_with_problems_and_says_why():
    bot = FakeBot()
    r = PublishReceipt(bot, "r1", "T", platforms=["instagram", "facebook", "youtube"],
                       initial={"instagram": {"status": "failed", "error": "PublishFailed: HTTP 400 media rejected"},
                                "facebook": {"status": "live"}})
    r.update_platform("youtube", "live")
    r.finalize()
    text = _final(bot)
    assert text.startswith("⚠️ <b>Posted with problems</b>")
    assert "❌ Instagram Reel: HTTP 400 media rejected" in text and "✅" not in text.splitlines()[0]
    assert bot.notes[-1] == "⚠️ T posted with problems · Facebook Reel and YouTube Short; not Instagram Reel"


def test_receipt_all_failed_is_a_cross():
    bot = FakeBot()
    r = PublishReceipt(bot, "r1", "T", platforms=["instagram", "facebook"],
                       initial={"instagram": {"status": "failed", "error": "a"}, "facebook": {"status": "failed", "error": "b"}})
    r.finalize()
    assert _final(bot).startswith("❌ <b>Not posted</b>")
    assert "❌ Instagram Reel: a" in _final(bot) and "❌ Facebook Reel: b" in _final(bot)
    assert bot.notes[-1] == "❌ T not posted"


def test_receipt_with_shorts_off_never_lists_or_names_a_short(monkeypatch):
    monkeypatch.setattr(config, "YOUTUBE_SHORTS", False)
    bot = FakeBot()
    r = PublishReceipt(bot, "r1", "T", initial={"instagram": {"status": "live"}, "facebook": {"status": "live"}})
    r.finalize()
    assert "YouTube" not in _final(bot) and "Short" not in bot.notes[-1]
    assert _final(bot).startswith("✅ <b>Posted</b>")


def test_receipt_skipped_platform_is_not_counted_as_attempted():
    bot = FakeBot()
    r = PublishReceipt(bot, "r1", "T", platforms=["instagram", "facebook", "youtube"],
                       initial={"instagram": {"status": "live"}, "facebook": {"status": "live"}})
    r.update_platform("youtube", "skipped", error="UPLOAD_POST_API_KEY / UPLOAD_POST_USER not set")
    r.finalize()
    text = _final(bot)
    assert text.startswith("✅ <b>Posted</b>") and "◽ YouTube Short: not attempted" in text
    assert bot.notes[-1] == "📮 T posted · Instagram Reel and Facebook Reel"


def _publishing_coordinator(db, tmp_path, monkeypatch, reel, short=None, v2=True, shorts=True):
    import main
    monkeypatch.setattr(config, "TELEGRAM_CARDS_V2", v2)
    monkeypatch.setattr(config, "YOUTUBE_SHORTS", shorts)
    from src import release
    monkeypatch.setattr(release, "require_release", lambda run, dry_run=False: None)
    for name in ("master.mp4", "cover.jpg"):
        (tmp_path / name).write_bytes(b"offline fixture")
    topic = db.list_all_topics()[0]["id"]
    db.create_run("ui02", topic)
    db.update_run("ui02", "approved", master_video_path=tmp_path / "master.mp4", thumbnail_cover_path=tmp_path / "cover.jpg",
                  thumbnail_grid_path=tmp_path / "cover.jpg", script_payload={"title": "Fixture", "caption": "c", "topic_id": topic})
    shorts_called = []
    coord = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    coord.db, coord.telegram_bot, alerts = db, FakeBot(), []
    coord.publisher = SimpleNamespace(
        build_envelope=lambda run_id, *a, platforms=None, **k: {"run_id": run_id, "topic_id": topic,
                                                               "platforms": platforms or ["facebook", "instagram"]},
        publish_reel=lambda envelope, dry_run=False: dict(reel),
        publish_short=lambda envelope, dry_run=False: shorts_called.append(1) or dict(short or {"status": "published", "post_urls": {}}))
    coord.topic_engine = SimpleNamespace(record_status=lambda *a, **k: None)
    coord._alert = lambda text, *a, **k: alerts.append(text)
    coord._facebook_finishing = lambda run_id: None
    return coord, alerts, shorts_called


def test_publish_marks_a_missed_platform_failed_on_the_receipt_and_the_stamp(db, tmp_path, monkeypatch):
    """main marked Instagram and Facebook live whatever the result said, and the stamp said "posted"."""
    reel = {"status": "published", "post_urls": {"facebook": "https://f/1"}, "missing_platforms": ["instagram"],
            "platform_errors": {"instagram": "PublishFailed: token expired"}, "error": "instagram-graph: token expired"}
    coord, _, _ = _publishing_coordinator(db, tmp_path, monkeypatch, reel)
    coord.publish_run("ui02")
    text = _final(coord.telegram_bot)
    assert text.startswith("⚠️ <b>Posted with problems</b>")
    assert "❌ Instagram Reel: token expired" in text and "✅ Facebook Reel" in text
    assert coord.telegram_bot.stamp.startswith("⚠️ <b>Approved · posted with problems</b>")
    assert "❌ Instagram: token expired" in coord.telegram_bot.stamp


def test_publish_with_shorts_off_lists_no_short_and_the_live_alert_is_truthful(db, tmp_path, monkeypatch):
    reel = {"status": "published", "post_urls": {"facebook": "https://f/1"}, "missing_platforms": ["instagram"],
            "platform_errors": {"instagram": "token expired"}}
    coord, alerts, called = _publishing_coordinator(db, tmp_path, monkeypatch, reel, v2=False, shorts=False)
    coord.publish_run("ui02")
    assert called == [] and not coord.telegram_bot.sent           # no V2 receipt with the flag off
    assert alerts[0].startswith("⚠️ <b>Posted with problems</b>") and "❌ Instagram: token expired" in alerts[0]
    assert "✅ Facebook: https://f/1" in alerts[0] and "YouTube" not in alerts[0]


def test_publish_lists_a_skipped_short_as_not_attempted(db, tmp_path, monkeypatch):
    reel = {"status": "published", "post_urls": {"facebook": "https://f/1", "instagram": "https://i/1"}}
    coord, _, _ = _publishing_coordinator(db, tmp_path, monkeypatch, reel, short={"status": "skipped", "error": "no uploader"})
    coord.publish_run("ui02")
    text = _final(coord.telegram_bot)
    assert text.startswith("✅ <b>Posted</b>") and "◽ YouTube Short: not attempted (no account that can upload Shorts is set up)" in text
    assert "queued" not in text


def test_a_failed_reel_makes_no_receipt_stuck_at_uploading(db, tmp_path, monkeypatch):
    coord, alerts, _ = _publishing_coordinator(db, tmp_path, monkeypatch, {"status": "failed", "error": "all down"})
    coord.publish_run("ui02")
    assert not coord.telegram_bot.sent and alerts[0].startswith("❌ <b>Publish failed</b>")


def test_publisher_records_each_missed_platforms_own_reason(db):
    from src.publisher import MetaPublisher, PublishFailed
    topic = db.list_all_topics()[0]["id"]
    db.create_run("pe", topic)

    class Fails:
        name, platforms = "instagram-graph", ("instagram",)

        def publish(self, envelope):
            raise PublishFailed("token expired")

    class Works:
        name, platforms = "facebook-page", ("facebook",)

        def publish(self, envelope):
            return {"post_urls": {"facebook": "https://f/1"}}

    out = MetaPublisher(db=db, publishers=[Fails(), Works()]).publish_reel(
        {"topic_id": topic, "run_id": "pe", "platforms": ["facebook", "instagram"]})
    assert out["missing_platforms"] == ["instagram"] and out["platform_errors"] == {"instagram": "token expired"}


@pytest.mark.parametrize("shorts, configured, expected", [
    (True, {"upload"}, "on Facebook and Instagram, then as a YouTube Short."),
    (False, {"upload"}, "on Facebook and Instagram."),
    (True, {"instagram", "facebook"}, "on Facebook and Instagram."),     # no Shorts uploader set up
    (True, {"instagram"}, "on Instagram."),
])
def test_post_now_names_only_what_will_be_attempted(db, monkeypatch, shorts, configured, expected):
    from src import posting_view, publisher
    monkeypatch.setattr(config, "YOUTUBE_SHORTS", shorts)
    for cls, name in ((publisher.UploadPostPublisher, "upload"), (publisher.InstagramGraphPublisher, "instagram"),
                      (publisher.FacebookPageReelsPublisher, "facebook")):
        monkeypatch.setattr(cls, "configured", lambda self, on=name in configured: on)
    topic = db.list_all_topics()[0]["id"]
    db.create_run("pn", topic)
    text, buttons = posting_view.confirm_post_now(db, "pn")
    assert f"The Reel goes out now {expected}" in text
    assert ("YouTube" in text) == ("YouTube Short" in expected)
    assert buttons[0][0]["callback_data"].startswith("postnow:")


@pytest.mark.parametrize("booked, upload, expected", [
    (("story", "carousel"), True, "; its Stories follow 45 min later and the carousel in its slot."),
    (("story",), True, "; its Stories follow 45 min later."),
    ((), True, "on Facebook and Instagram."),
    (("story", "carousel"), False, "on Instagram."),          # only Upload-Post posts Stories and carousels
])
def test_post_now_names_stories_and_carousel_only_when_booked(db, monkeypatch, booked, upload, expected):
    from src import posting_view, publisher
    monkeypatch.setattr(config, "STORY_DELAY_MINUTES", 45)
    monkeypatch.setattr(config, "YOUTUBE_SHORTS", False)
    monkeypatch.setattr(publisher.UploadPostPublisher, "configured", lambda self: upload)
    monkeypatch.setattr(publisher.InstagramGraphPublisher, "configured", lambda self: True)
    monkeypatch.setattr(publisher.FacebookPageReelsPublisher, "configured", lambda self: upload)
    topic = db.list_all_topics()[0]["id"]
    db.create_run("pb", topic)
    db.update_run("pb", "scheduled")
    db.add_scheduled_post("pb", "reel", "2099-01-01T00:00:00+00:00", ["r.mp4"])
    for kind in booked:
        if kind == "carousel":
            db.add_scheduled_post("pb", kind, None, ["x.jpg"], status="waiting")
        else:
            db.add_scheduled_post("pb", kind, "2099-01-01T01:00:00+00:00", ["x.jpg"])
    text, _ = posting_view.confirm_post_now(db, "pb")
    assert expected in text
    assert ("Stories" in text) == ("story" in booked and upload)
    assert ("carousel" in text) == ("carousel" in booked and upload)


def test_post_now_with_no_account_offers_no_post(db, monkeypatch):
    from src import posting_view, publisher
    for cls in (publisher.UploadPostPublisher, publisher.InstagramGraphPublisher, publisher.FacebookPageReelsPublisher):
        monkeypatch.setattr(cls, "configured", lambda self: False)
    topic = db.list_all_topics()[0]["id"]
    db.create_run("pn0", topic)
    text, buttons = posting_view.confirm_post_now(db, "pn0")
    assert "nothing can be posted now" in text
    assert not any(b["callback_data"].startswith("postnow:") for row in buttons for b in row)


# ---------------------------------------------------------------------------------------------- V2 review card
def offline_bot(db, monkeypatch, fail=None):
    bot = TelegramApprovalBot(db=db, bot_token="123:offline_ui02", chat_id="555", approver_ids={"555"})
    calls, counter = [], [3000]

    def api(method, **kw):
        calls.append((method, kw))
        if method in (fail or ()):
            raise RuntimeError(f"Telegram {method} failed: HTTP 400 Bad Request")
        if method.startswith("send"):
            counter[0] += 1
            return {"message_id": counter[0]}
        return True

    monkeypatch.setattr(bot, "_api", api)
    return bot, calls


def fixture_run(db, tmp_path, monkeypatch, run_id="run_gol_gumbaz_whispering_gallery_1793278143", drop_duration=False):
    """The real run's records in a run folder of its own, with a stand-in video and cover (bytes only). The review's
    re-check of the release gate returns the run's own report (the stand-in video cannot be measured)."""
    from src import release
    initialize(db)
    root = tmp_path / run_id
    shutil.copytree(FIXTURE, root)
    if drop_duration:
        report = json.loads((root / "release_report.json").read_text(encoding="utf-8"))
        report["checks"] = [c for c in report["checks"] if c["name"] != "duration"]
        (root / "release_report.json").write_text(json.dumps(report), encoding="utf-8")
    (root / "master.mp4").write_bytes(b"offline review video")
    Image.new("RGB", (20, 20), "blue").save(root / "thumbnail_cover.jpg")
    saved = json.loads((root / "release_report.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(release, "write_release_report", lambda run, dry_run=False: saved)
    payload = json.loads((root / "script_metadata.json").read_text(encoding="utf-8"))
    topic = db.list_all_topics()[0]["id"]
    payload["topic_id"] = topic          # the test database's topic (its hashtag list decides what is posted)
    db.create_run(run_id, topic)
    db.update_run(run_id, "pending_approval", script_payload=payload, master_video_path=root / "master.mp4",
                  thumbnail_cover_path=root / "thumbnail_cover.jpg")
    return root, payload, json.loads((root / "release_report.json").read_text(encoding="utf-8"))


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, list):
        for x in node:
            yield from _strings(x)
    elif isinstance(node, dict):
        for k, v in node.items():
            if k in ("text", "summary") or isinstance(v, (list, dict)):
                yield from _strings(v)


def test_v2_card_from_a_real_run_shows_only_values_traceable_to_its_files(db, tmp_path, monkeypatch):
    from src import publishable
    from src.release import check_problems
    monkeypatch.setattr(config, "TELEGRAM_CARDS_V2", True)
    bot, _ = offline_bot(db, monkeypatch)
    root, payload, report = fixture_run(db, tmp_path, monkeypatch)
    run_id = root.name
    plan = bot._v2_review_plan(run_id, payload, report, root)
    blocks, posted = plan["blocks"], plan["posted"]
    shown = list(_strings(blocks))
    card = "\n".join(shown)
    for literal in PLACEHOLDERS:
        assert literal not in card, literal

    files = {n: (root / n).read_text(encoding="utf-8") for n in ("script_metadata.json", "release_report.json",
                                                                  "voice.json", "music_selection.json")}
    measured = float(re.search(r"Measured ([\d.]+)s", next(c["detail"] for c in report["checks"] if c["name"] == "duration"))[1])
    table = {row[0]["text"]["text"]: row[1]["text"] for row in blocks[next(i for i, b in enumerate(blocks) if b["type"] == "table")]["cells"]}
    assert table["Length"] == f"{measured:.1f} s"
    open_checks = [c for c in report["checks"] if c["status"] != "pass"]
    assert table["Release gate"] == ("⛔ Blocked" if plan["blocked"] else "⚠️ Needs review" if open_checks else "✅ All checks pass")
    assert blocks[0]["text"] == f"🎬 {posted['copy']['reel.title']}"
    assert payload["hook_sentence"] in card
    # the narration, whole and in order, from script_metadata.json
    script = next(b for b in blocks if b.get("summary", "").startswith("📜 Script"))
    assert " ".join(p["text"] for p in script["blocks"]) == " ".join(payload["narration_script"].split())
    assert script["summary"] == f"📜 Script · {measured:.0f} s, {len(payload['narration_script'].split())} words"
    # each finding is the gate's own words or one of the run's notes
    findings = next(b for b in blocks if b.get("summary", "").startswith("🔎 Findings"))
    problems = [p for c in report["checks"] for p in check_problems(c)] + [p for c in report["checks"] for p in check_problems(c, "notes")]
    notes = [str(payload[k]) for k in payload if k.endswith("_note") and payload[k]]
    for item in findings["blocks"][0]["items"]:
        text = item["blocks"][0]["text"]
        assert any(p in text for p in problems) or any(text.split(": ", 1)[-1][:40] in re.sub(r"<[^>]+>", "", n) for n in notes), text
    # what Approve posts is the record saved for the envelope check (QF-43), from the run's own copy
    again = publishable.card_record(payload, db, run_dir=root, topic_id=payload["topic_id"], posting=posted["posting"])
    assert posted["copy"] == again["copy"] and posted["hashtags"] == again["hashtags"]
    assert posted["copy"]["reel.caption"] in shown and posted["copy"]["reel.first_comment"] in shown
    assert payload["first_comment"] in posted["copy"]["reel.first_comment"]
    # run details: who wrote it (script_metadata origin), the voice (voice.json), the music (music_selection.json)
    details = next(b for b in blocks if b.get("summary") == "Run details")["blocks"][0]["cells"]
    rows = {r[0]["text"]["text"]: r[1]["text"] for r in details}
    voice, music = json.loads(files["voice.json"]), json.loads(files["music_selection.json"])
    assert rows["Voice"] == "Azure Speech · licensed" and voice == {"provider": "azure", "licensed": True}
    assert rows["Music"] in music["credit"] and music["tracks"][0]["creator"] in rows["Music"]
    from src.telegram_bot import origin_label
    assert rows["Written by"] == origin_label(payload["origin"])
    # every number shown is in the run's files or derived from them (length, word count, counts)
    derived = {f"{measured:.1f}", f"{measured:.0f}", str(len(payload["narration_script"].split())),
               str(len(findings["blocks"][0]["items"])), str(len(open_checks))}
    pool = "\n".join(files.values()) + json.dumps(posted, ensure_ascii=False) + run_id
    for n in re.findall(r"\d+(?:\.\d+)?", card):
        assert n in derived or n in pool, n


def test_v2_card_value_missing_sends_the_live_card_and_says_why(db, tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(config, "TELEGRAM_CARDS_V2", True)
    bot, calls = offline_bot(db, monkeypatch)
    root, payload, report = fixture_run(db, tmp_path, monkeypatch, drop_duration=True)
    with pytest.raises(telegram_bot.V2CardUnavailable, match="measured no length"):
        bot._v2_review_plan(root.name, payload, report, root)
    with caplog.at_level(logging.WARNING):
        mid = bot.send_approval_request(root / "master.mp4", root / "thumbnail_cover.jpg", payload, root.name)
    methods = [m for m, _ in calls]
    assert mid and "sendRichMessage" not in methods and "sendVideo" not in methods
    assert any("Master review" in (kw.get("data") or {}).get("text", "") for m, kw in calls if m == "sendMessage")
    assert "V2 review card not used" in caplog.text and "measured no length" in caplog.text


def test_v2_rich_card_failure_delivers_the_live_card_and_keeps_the_video(db, tmp_path, monkeypatch):
    """B2: the fallback card refused (review media not recorded) and the video was deleted: no review at all."""
    monkeypatch.setattr(config, "TELEGRAM_CARDS_V2", True)
    bot, calls = offline_bot(db, monkeypatch, fail={"sendRichMessage"})
    root, payload, _ = fixture_run(db, tmp_path, monkeypatch)
    mid = bot.send_approval_request(root / "master.mp4", root / "thumbnail_cover.jpg", payload, root.name)
    methods = [m for m, _ in calls]
    assert mid and "deleteMessage" not in methods
    video = next(i for i, (m, _) in enumerate(calls) if m == "sendVideo")
    card = next(i for i, (m, kw) in enumerate(calls) if m == "sendMessage" and "Master review" in kw["data"].get("text", ""))
    assert video < card
    assert db.get_approval(root.name)["telegram_message_id"] == mid        # the live card decides
    stripped = [json.loads(kw["data"]["reply_markup"]) for m, kw in calls if m == "editMessageReplyMarkup"]
    assert {"inline_keyboard": []} in stripped                              # the video keeps no decision buttons


def test_v2_delivery_saves_what_approve_posts(db, tmp_path, monkeypatch):
    from src import publishable
    monkeypatch.setattr(config, "TELEGRAM_CARDS_V2", True)
    bot, calls = offline_bot(db, monkeypatch)
    root, payload, _ = fixture_run(db, tmp_path, monkeypatch)
    mid = bot.send_approval_request(root / "master.mp4", root / "thumbnail_cover.jpg", payload, root.name)
    assert mid and "sendRichMessage" in [m for m, _ in calls]
    assert publishable.load_record(db, root.name)["copy"]["reel.caption"]
    notice = next(kw["data"]["text"] for m, kw in calls if m == "sendMessage" and "Review waiting" in kw["data"].get("text", ""))
    label = db.run_label(root.name)
    assert notice == "🎬 Review waiting · " + (label if len(label) <= 36 else notice.split(" · ", 1)[1])
    assert len(label) <= 36 or notice.endswith("…")


# ---------------------------------------------------------------------------------------------- V2 state machine
def v2_review(db, tmp_path, monkeypatch, run_id="v2u"):
    """A V2 review pair delivered as video 700 (the card store and the approval as _send_review_pair_v2 leaves them)."""
    monkeypatch.setattr(config, "TELEGRAM_CARDS_V2", True)
    bot, calls = offline_bot(db, monkeypatch)
    initialize(db)
    topic = db.list_all_topics()[0]["id"]
    d = tmp_path / run_id
    d.mkdir()
    (d / "master.mp4").write_bytes(b"video " + run_id.encode())
    Image.new("RGB", (8, 8), "blue").save(d / "thumbnail_cover.jpg")
    db.create_run(run_id, topic)
    db.update_run(run_id, "pending_approval", script_payload={"title": "T & U", "topic_id": topic, "caption": "c"},
                  master_video_path=d / "master.mp4", thumbnail_cover_path=d / "thumbnail_cover.jpg")
    db.record_approval(run_id, topic, 700)
    fp = review_fingerprint(db, run_id)
    put(db, "review-card", f"{run_id}:700", fp)
    put(db, "review-media", run_id, fp)
    db.put_telegram_card(f"review:{run_id}", config.TELEGRAM_TOPICS["reviews"], 700, "review_pair",
                         {"video_mid": 700, "card_mid": 701, "run_id": run_id, "status": "waiting"})
    return bot, calls, db.run_key(run_id)


def tap(data, mid=700, qid="q"):
    return {"id": qid, "data": data, "from": {"id": 555, "first_name": "Op"}, "message": {"message_id": mid, "chat": {"id": 555}}}


def answers(calls):
    return [kw["data"]["text"] for m, kw in calls if m == "answerCallbackQuery"]


def test_undo_approval_of_a_published_run_is_refused(db, tmp_path, monkeypatch):
    """B1: undoappr reset a published run's approval to pending and redrew a dead Approve."""
    bot, calls, key = v2_review(db, tmp_path, monkeypatch)
    assert bot._handle_callback(tap(f"approve:{key}")) == "approved"
    bot.drain()
    db.update_run("v2u", "published")
    calls.clear()
    assert bot._handle_callback(tap(f"undoappr:{key}", qid="u")) is None
    assert db.get_approval("v2u")["decision"] == "approved"
    assert answers(calls) and answers(calls)[0].startswith("Too late to undo")
    assert not any("approve:" in json.dumps(kw["data"].get("reply_markup", "")) for m, kw in calls)


def test_undo_approval_after_the_stated_window_is_refused(db, tmp_path, monkeypatch):
    bot, calls, key = v2_review(db, tmp_path, monkeypatch)
    bot._handle_callback(tap(f"approve:{key}"))
    old = (datetime.now(UTC) - timedelta(minutes=11)).isoformat()
    with db._conn() as conn:
        conn.execute("UPDATE approvals SET decision_timestamp=? WHERE run_id='v2u'", (old,))
    calls.clear()
    bot._handle_callback(tap(f"undoappr:{key}", qid="u"))
    assert db.get_approval("v2u")["decision"] == "approved"
    assert answers(calls) == ["Too late to undo: 10 minutes have passed."]


def test_undo_approval_once_the_worker_claimed_it_is_refused(db, tmp_path, monkeypatch):
    """The worker's claim (main.undone_before_acting) and the undo are single writes: the claim first, no undo."""
    import time

    import main
    bot, _calls, key = v2_review(db, tmp_path, monkeypatch)
    bot._handle_callback(tap(f"approve:{key}"))
    monkeypatch.setattr(main, "_undo_clock", lambda: time.time() + 3600)   # the worker's clock: its window has passed
    assert main.undone_before_acting(db, "v2u", "approved") is False     # claimed, still approved: acted on
    bot._handle_callback(tap(f"undoappr:{key}", qid="u"))
    assert db.get_approval("v2u")["decision"] == "approved"


def test_undo_before_the_worker_claims_reopens_the_review_and_the_wait_goes_on(db, tmp_path, monkeypatch):
    import main
    bot, calls, key = v2_review(db, tmp_path, monkeypatch)
    bot._handle_callback(tap(f"approve:{key}"))
    bot.drain()
    calls.clear()
    assert bot._handle_callback(tap(f"undoappr:{key}", qid="u")) == "undone"
    assert db.get_approval("v2u")["decision"] == "pending"
    assert re.fullmatch(r"Approval undone\. The review is open until [0-9]{2}:[0-9]{2} \S+\.", answers(calls)[0])
    assert main.undone_before_acting(db, "v2u", "approved") is True      # the wait reads the review again


def test_undo_rejection_never_reopens_a_regenerated_run(db, tmp_path, monkeypatch):
    """B1: undorej reset a regenerate decision already acted on."""
    bot, _calls, key = v2_review(db, tmp_path, monkeypatch)
    assert bot._handle_callback(tap(f"regen:{key}")) == "regenerate"
    bot.drain()
    db.update_run("v2u", "regenerate")
    assert bot._handle_callback(tap(f"undorej:{key}", qid="u")) is None
    assert db.get_approval("v2u")["decision"] == "regenerate"


def test_undo_rejection_after_it_was_settled_is_refused(db, tmp_path, monkeypatch):
    bot, calls, key = v2_review(db, tmp_path, monkeypatch)
    bot._handle_callback(tap(f"reject:{key}"))
    assert bot._handle_callback(tap(f"rejres:{key}:pictures", qid="r")) == "rejected"
    db.update_run("v2u", "rejected")                     # _settle_decision ran
    calls.clear()
    assert bot._handle_callback(tap(f"undorej:{key}", qid="u")) is None
    assert db.get_approval("v2u")["decision"] == "rejected"
    assert answers(calls)[0].startswith("Too late to undo: the rejection was acted on")


def test_reject_reasons_are_improve_reasons_and_are_recorded(db, tmp_path, monkeypatch):
    from src import improve
    bot, calls, key = v2_review(db, tmp_path, monkeypatch)
    assert bot._handle_callback(tap(f"reject:{key}")) == "reject_prompt"
    kb = json.loads(next(kw["data"]["reply_markup"] for m, kw in calls if m == "editMessageReplyMarkup"))
    chips = [b for row in kb["inline_keyboard"] for b in row if b["callback_data"].startswith("rejres:")]
    assert [(b["callback_data"].rsplit(":", 1)[1], b["text"]) for b in chips] == list(improve.REASONS.items())
    assert bot._handle_callback(tap(f"rejres:{key}:pacing", qid="r")) == "rejected"
    assert improve.operator_feedback(db, "v2u")["reason"] == "pacing"
    caption = next(kw["data"]["caption"] for m, kw in calls if m == "editMessageCaption")
    assert "reason: Pacing / length" in caption and "&amp;" not in caption
    # a chip of the retired set is not a decision
    bot2, _, key2 = v2_review(db, tmp_path, monkeypatch, "v2r")
    assert bot2._handle_callback(tap(f"rejres:{key2}:factual", qid="r2")) is None
    assert db.get_approval("v2r")["decision"] == "pending"


def test_keep_it_offers_no_approve_on_a_blocked_version(db, tmp_path, monkeypatch):
    bot, calls, key = v2_review(db, tmp_path, monkeypatch)
    monkeypatch.setattr(TelegramApprovalBot, "_v2_blocked", lambda self, run_id: True)
    bot._handle_callback(tap(f"reject:{key}"))
    assert bot._handle_callback(tap(f"keepit:{key}", qid="k")) == "kept"
    kb = json.loads([kw["data"]["reply_markup"] for m, kw in calls if m == "editMessageReplyMarkup"][-1])
    assert not any(b["callback_data"].startswith("approve:") for row in kb["inline_keyboard"] for b in row)


@pytest.mark.parametrize("action", ["keepit", "undoappr", "undorej"])
def test_v2_buttons_with_the_flag_off_are_never_a_decision(db, tmp_path, monkeypatch, action):
    """B3: with the flag off, "← Keep it" was recorded as the decision "keep" and failed the run and topic."""
    bot, _calls, key = v2_review(db, tmp_path, monkeypatch)
    monkeypatch.setattr(config, "TELEGRAM_CARDS_V2", False)
    assert bot._handle_callback(tap(f"{action}:{key}")) is None
    assert db.get_approval("v2u")["decision"] == "pending"
    assert db.get_run("v2u")["status"] == "pending_approval"


def test_the_command_line_accepts_only_real_decisions(db, tmp_path, monkeypatch):
    bot, _, _ = v2_review(db, tmp_path, monkeypatch)
    for pseudo in ("keep", "undo_approved", "undo_rejected"):
        with pytest.raises(ValueError):
            bot.record_local_decision("v2u", pseudo)
    assert db.get_approval("v2u")["decision"] == "pending"


def test_the_expired_card_sweep_runs_and_an_archived_card_is_reaped_once(db, monkeypatch):
    """B10: the sweep was given the time as max_age_hours and never ran; archived cards were reaped every 6 h."""
    monkeypatch.setattr(config, "TELEGRAM_CARDS_V2", True)
    bot, calls = offline_bot(db, monkeypatch)
    db.put_telegram_card("temp:chip", 4906, 1, "chip", {"x": 1}, expires_at="2000-01-01T00:00:00+00:00")
    db.put_telegram_card("review:r", config.TELEGRAM_TOPICS["reviews"], 10, "review_pair",
                         {"status": "approved", "video_mid": 10, "card_mid": 11})
    assert bot.reap_decided_reviews(max_hours=0) == 1
    assert db.get_telegram_card("temp:chip") is None
    assert bot.reap_decided_reviews(max_hours=0) == 0
    assert [m for m, _ in calls].count("deleteMessages") == 1


def _age_decision(db, run_id, minutes):
    at = (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()
    with db._conn() as conn:
        conn.execute("UPDATE approvals SET decision_timestamp=? WHERE run_id=?", (at, run_id))
    return datetime.fromisoformat(at).timestamp()


def test_undo_works_at_minute_nine(db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REVIEW_UNDO_MINUTES", 10)
    bot, _calls, key = v2_review(db, tmp_path, monkeypatch)
    bot._handle_callback(tap(f"approve:{key}"))
    _age_decision(db, "v2u", 9)
    assert bot._handle_callback(tap(f"undoappr:{key}", qid="u")) == "undone"
    assert db.get_approval("v2u")["decision"] == "pending"


def test_after_the_window_the_worker_claims_removes_the_undo_button_and_undo_is_refused(db, tmp_path, monkeypatch):
    import main
    monkeypatch.setattr(config, "REVIEW_UNDO_MINUTES", 10)
    bot, calls, key = v2_review(db, tmp_path, monkeypatch)
    bot._handle_callback(tap(f"approve:{key}"))
    bot.drain()                                    # the approval stamp is drawn first
    _age_decision(db, "v2u", 10.5)
    monkeypatch.setattr(main, "_undo_sleep", lambda s: pytest.fail("no wait once the window has passed"))
    calls.clear()
    assert main.undone_before_acting(db, "v2u", "approved", bot) is False      # claimed: acted on
    assert [(m, json.loads(kw["data"]["reply_markup"]), kw["data"]["message_id"]) for m, kw in calls] == [
        ("editMessageReplyMarkup", {"inline_keyboard": []}, 700)]               # the Undo button is gone
    calls.clear()
    assert bot._handle_callback(tap(f"undoappr:{key}", qid="u")) is None
    assert db.get_approval("v2u")["decision"] == "approved"
    assert answers(calls) == ["Too late to undo: 10 minutes have passed."]


def test_the_claim_waits_for_the_window_and_an_undo_inside_it_reopens(db, tmp_path, monkeypatch):
    import main
    from src.db import REVIEW_CLAIM_PREFIX
    monkeypatch.setattr(config, "REVIEW_UNDO_MINUTES", 10)
    bot, _calls, key = v2_review(db, tmp_path, monkeypatch)
    bot._handle_callback(tap(f"approve:{key}"))
    decided = _age_decision(db, "v2u", 0)
    clock = [decided]
    slept = []

    def sleep(seconds):
        assert db.get_setting(REVIEW_CLAIM_PREFIX + "v2u") is None          # never claimed inside the window
        slept.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(main, "_undo_clock", lambda: clock[0])
    monkeypatch.setattr(main, "_undo_sleep", sleep)
    assert main.undone_before_acting(db, "v2u", "approved", bot) is False      # the window ran out: acted on
    assert abs(sum(slept) - 600) < 1e-6 and db.get_setting(REVIEW_CLAIM_PREFIX + "v2u")
    # the same wait with an Undo at minute 5
    bot2, _, key2 = v2_review(db, tmp_path, monkeypatch, "v2w")
    bot2._handle_callback(tap(f"approve:{key2}"))
    decided = _age_decision(db, "v2w", 0)
    clock[0] = decided

    def sleep_and_undo(seconds):
        clock[0] += seconds
        if clock[0] - decided >= 300 and db.get_approval("v2w")["decision"] == "approved":
            assert bot2._handle_callback(tap(f"undoappr:{key2}", qid="u5")) == "undone"

    monkeypatch.setattr(main, "_undo_sleep", sleep_and_undo)
    assert main.undone_before_acting(db, "v2w", "approved", bot2) is True       # the wait goes on
    assert db.get_approval("v2w")["decision"] == "pending" and clock[0] - decided < 310


def test_post_now_and_slot_booking_act_once_after_the_window(db, tmp_path, monkeypatch):
    """Nothing is lost: the worker's act (post now, or booking the slot) runs once, after the window."""
    import main
    monkeypatch.setattr(config, "REVIEW_UNDO_MINUTES", 10)
    bot, _calls, key = v2_review(db, tmp_path, monkeypatch)
    bot._handle_callback(tap(f"approve:{key}"))
    decided = _age_decision(db, "v2u", 0)
    clock = [decided]
    monkeypatch.setattr(main, "_undo_clock", lambda: clock[0])
    monkeypatch.setattr(main, "_undo_sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    acted = []
    bot.wait_for_decision = lambda run_id, seconds, **kw: db.get_approval(run_id)["decision"]
    me = SimpleNamespace(db=db, telegram_bot=bot, _approval_window=lambda: 60,
                         REVIEW_ACTED_PREFIX=main.PipelineCoordinator.REVIEW_ACTED_PREFIX)
    me._refused_for_good = main.PipelineCoordinator._refused_for_good
    out = main.PipelineCoordinator._decide_review(me, "v2u", lambda d: acted.append((d, clock[0] - decided)) or {"booked": True})
    assert out == ("approved", {"booked": True}) and acted == [("approved", 600.0)]


def test_the_approval_stamp_says_when_it_posts(db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REVIEW_UNDO_MINUTES", 10)
    bot, calls, key = v2_review(db, tmp_path, monkeypatch)
    bot._handle_callback(tap(f"approve:{key}"))
    bot.drain()
    caption = next(kw["data"]["caption"] for m, kw in calls if m == "editMessageCaption")
    assert re.fullmatch(r"✅ Approved by Op · posts after [0-9]{2}:[0-9]{2} \S+ unless you undo", caption), caption
    kb = json.loads(next(kw["data"]["reply_markup"] for m, kw in calls if m == "editMessageCaption"))
    assert kb["inline_keyboard"][0][0]["callback_data"] == f"undoappr:{key}"
    monkeypatch.setattr(config, "REVIEW_UNDO_MINUTES", 0)       # no window: no Undo offered
    bot2, calls2, key2 = v2_review(db, tmp_path, monkeypatch, "v2z")
    bot2._handle_callback(tap(f"approve:{key2}"))
    bot2.drain()
    caption = next(kw["data"]["caption"] for m, kw in calls2 if m == "editMessageCaption")
    assert caption == "✅ Approved by Op · release check and publishing under way"
    markup = next(kw["data"]["reply_markup"] for m, kw in calls2 if m == "editMessageCaption")
    assert json.loads(markup) == {"inline_keyboard": []}


def test_v2_buttons_are_sentence_case():
    from src.telegram_bot import _v2_review_keyboard
    labels = [b["text"] for row in _v2_review_keyboard("k", False)["inline_keyboard"] for b in row]
    assert labels[0] == "🚀 Approve & post"


def test_an_undo_after_the_review_window_closed_reopens_it_with_time_to_decide(db, tmp_path, monkeypatch):
    """Review 2: approve near the end of the review's window, the window passes, Undo: the restored Approve refused
    ("Expired/Closed") and the wait timed out. Now the Undo gives the review its time back, Approve works again and
    the worker acts on it once its own Undo window has passed."""
    import time

    import main
    monkeypatch.setattr(config, "REVIEW_UNDO_MINUTES", 10)
    bot, calls, key = v2_review(db, tmp_path, monkeypatch)
    with db._conn() as conn:
        conn.execute("UPDATE approvals SET expires_at=? WHERE run_id='v2u'", (time.time() + 30,))   # its last 30 s
    assert bot._handle_callback(tap(f"approve:{key}")) == "approved"
    bot.drain()
    with db._conn() as conn:
        conn.execute("UPDATE approvals SET expires_at=? WHERE run_id='v2u'", (time.time() - 1,))    # the window passed
    calls.clear()
    assert bot._handle_callback(tap(f"undoappr:{key}", qid="u")) == "undone"
    expires = db.get_approval("v2u")["expires_at"]
    assert expires >= time.time() + 600 - 5                       # at least REVIEW_UNDO_MINUTES to decide again
    assert re.fullmatch(r"Approval undone\. The review is open until [0-9]{2}:[0-9]{2} \S+\.", answers(calls)[0])
    assert bot._handle_callback(tap(f"approve:{key}", qid="a2")) == "approved"   # the restored Approve decides
    decided = datetime.fromisoformat(db.get_approval("v2u")["decision_timestamp"]).timestamp()
    clock = [decided]
    monkeypatch.setattr(main, "_undo_clock", lambda: clock[0])
    monkeypatch.setattr(main, "_undo_sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    bot.wait_for_decision = lambda run_id, seconds, **kw: db.get_approval(run_id)["decision"]
    me = SimpleNamespace(db=db, telegram_bot=bot, _approval_window=lambda: 60,
                         REVIEW_ACTED_PREFIX=main.PipelineCoordinator.REVIEW_ACTED_PREFIX)
    me._refused_for_good = main.PipelineCoordinator._refused_for_good
    acted = []
    out = main.PipelineCoordinator._decide_review(me, "v2u", lambda d: acted.append(d) or {"published": True})
    assert out == ("approved", {"published": True}) and acted == ["approved"]   # it posts


def test_the_wait_follows_the_time_an_undo_gave_back(db, tmp_path, monkeypatch):
    """wait_for_decision narrows the review's expiry to its window, except for a review an Undo reopened."""
    import time

    from src.db import UNDONE_BY
    bot, _calls, _key = v2_review(db, tmp_path, monkeypatch)
    later = time.time() + 3600
    with db._conn() as conn:
        conn.execute("UPDATE approvals SET expires_at=?, decided_by=? WHERE run_id='v2u'", (later, UNDONE_BY))
    from src import telegram_bot as tb
    monkeypatch.setattr(bot, "is_configured", lambda: False)
    monkeypatch.setattr(bot, "cleanup_transient_messages", lambda: None)

    def decide_meanwhile(seconds):                                # the operator approves again during the wait
        assert db.get_approval("v2u")["expires_at"] >= later     # not narrowed to the 1 s wait
        with db._conn() as conn:
            conn.execute("UPDATE approvals SET decision='approved' WHERE run_id='v2u'")

    monkeypatch.setattr(tb, "time", SimpleNamespace(time=time.time, monotonic=time.monotonic, sleep=decide_meanwhile))
    assert bot.wait_for_decision("v2u", timeout_seconds=1, poll_interval=0) == "approved"


@pytest.mark.parametrize("undo", [10, 20])
def test_an_approval_in_the_last_minute_of_the_window_keeps_its_slot(db, tmp_path, monkeypatch, undo):
    """Review 2: the slot was picked at the claim, REVIEW_UNDO_MINUTES after the tap and after the run-folder backup,
    so an approval in the card's last minutes (it closes 20 min before the slot; a booking needs LEAD) took the next
    free day. It is picked from the decision's time now, whatever the wait and the backup take."""
    import main
    from src import backup, post_schedule, release, worker_deadline
    monkeypatch.setattr(config, "REVIEW_UNDO_MINUTES", undo)
    monkeypatch.setattr(main, "POST_SCHEDULING", True)
    monkeypatch.setattr(config, "BACKUP_ENABLED", True, raising=False)
    monkeypatch.setattr(release, "require_release", lambda run, dry_run=False: None)
    topic = db.list_all_topics()[0]["id"]
    run_dir = tmp_path / "slot_run"
    run_dir.mkdir()
    (run_dir / "master.mp4").write_bytes(b"x")
    db.create_run("slot_run", topic)
    db.update_run("slot_run", "pending_approval", master_video_path=run_dir / "master.mp4",
                  script_payload={"title": "T", "topic_id": topic, "caption": "c"})
    db.record_approval("slot_run", topic, 900)
    slot = post_schedule.next_reel_slot(datetime(2026, 11, 20, 12, 0, tzinfo=UTC), set())
    decided = slot - timedelta(minutes=21)                       # the window's final minute (it closes at slot - 20)
    assert post_schedule.next_reel_slot(decided, set()) == slot
    with db._conn() as conn:
        conn.execute("UPDATE approvals SET decision='approved', decision_timestamp=?, decided_by='telegram:555' "
                     "WHERE run_id='slot_run'", (decided.isoformat(),))
    # made for that slot: its production began that morning
    db.set_setting(worker_deadline.STARTED_PREFIX + "slot_run", (decided - timedelta(hours=3)).isoformat())
    assert post_schedule.next_reel_slot(decided - timedelta(hours=3), set()) == slot
    clock = [decided.timestamp() + undo * 60 + 1]                 # the worker claims after the Undo window
    monkeypatch.setattr(main, "_undo_clock", lambda: clock[0])

    def slow_backup(*a, **k):                                     # the run-folder copy takes five minutes
        clock[0] += 300

    monkeypatch.setattr(backup, "backup_run", slow_backup)
    claimed_then = datetime.fromtimestamp(clock[0] + 300, UTC)
    assert post_schedule.next_reel_slot(claimed_then, set()) != slot   # picked at the claim, it would miss
    coord = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    coord.db, coord.schedule_posts = db, True
    alerts = []
    coord.telegram_bot = SimpleNamespace(stamp_review_outcome=lambda run_id, text: True,
                                         notify=lambda text, **kw: alerts.append(text))
    coord.topic_engine = SimpleNamespace(record_status=lambda *a, **k: None)
    out = coord._after_approval("slot_run")
    assert out["status"] == "scheduled" and datetime.fromisoformat(out["scheduled_for"]) == slot
    assert alerts == []                                           # no "Missed its slot" notice (QF-74): it kept it
    reel = [p for p in db.list_scheduled_posts(("pending",), run_id="slot_run") if p["kind"] == "reel"]
    assert datetime.fromisoformat(reel[0]["due_at"]) == slot


def test_a_stale_undo_with_the_window_off_says_so(db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REVIEW_UNDO_MINUTES", 10)
    bot, calls, key = v2_review(db, tmp_path, monkeypatch)
    bot._handle_callback(tap(f"approve:{key}"))
    bot.drain()
    monkeypatch.setattr(config, "REVIEW_UNDO_MINUTES", 0)
    calls.clear()
    assert bot._handle_callback(tap(f"undoappr:{key}", qid="u")) is None
    assert answers(calls) == ["Undo is turned off."]
    assert db.get_approval("v2u")["decision"] == "approved"


# ---------------------------------------------------------------------------------------------- review 3: a re-sent card
def _wait_clock(monkeypatch, on_sleep=None, limit=7200):
    """wait_for_decision on a clock of its own: each poll's sleep moves it on (no real waiting). Returns the clock;
    `clock["dt"]` is how long the wait has run. A wait still running after `limit` seconds fails the test."""
    import time

    from src import telegram_bot as tb
    clock = {"dt": 0.0, "t0": time.time(), "m0": time.monotonic(), "sleeps": 0}

    def sleep(seconds):
        clock["sleeps"] += 1
        if on_sleep:
            on_sleep(clock)
        clock["dt"] += max(float(seconds), 0.001)
        assert clock["dt"] <= limit, f"the review wait is still running after {clock['dt']:.0f} s"

    monkeypatch.setattr(tb, "time", SimpleNamespace(time=lambda: clock["t0"] + clock["dt"],
                                                    monotonic=lambda: clock["m0"] + clock["dt"], sleep=sleep))
    return clock


def _undone_review(db, tmp_path, monkeypatch, window=120):
    """A V2 review whose wait had narrowed it to an hour, approved and then undone on its card."""
    import time
    monkeypatch.setattr(config, "REVIEW_UNDO_MINUTES", 10)
    bot, calls, key = v2_review(db, tmp_path, monkeypatch)
    bot.approval_window = lambda: window
    bot.background_polling = True
    monkeypatch.setattr(bot, "cleanup_transient_messages", lambda: None)
    with db._conn() as conn:
        conn.execute("UPDATE approvals SET expires_at=? WHERE run_id='v2u'", (time.time() + 3600,))
    assert bot._handle_callback(tap(f"approve:{key}")) == "approved"
    bot.drain()
    assert bot._handle_callback(tap(f"undoappr:{key}", qid="u")) == "undone"
    return bot, calls, key


def _redeliver(db, bot, how):
    """The card delivered again after the Undo: a Resend (request_approval) or Edit text's fresh card (edit_copy):
    both send it with send_approval_request, Edit text after committing the new copy (commit_review_copy)."""
    run = db.get_run("v2u")
    payload = dict(run["script_payload"])
    if how == "edit":
        payload["caption"] = "c, corrected"
        assert db.commit_review_copy("v2u", db.get_approval("v2u")["revision"], payload)
    bot.send_approval_request(Path(run["master_video_path"]), Path(run["thumbnail_cover_path"]), payload, "v2u")


@pytest.mark.parametrize("how", ["resend", "edit"])
def test_a_card_resent_after_an_undo_waits_its_own_window_not_a_day(db, tmp_path, monkeypatch, how):
    """Review 3: after an Undo, any new delivery (record_approval: a 24 h placeholder) left decided_by "undone", so
    the next wait kept the placeholder and the review wait (and the production lock) could run for 24 h."""
    from src.db import UNDONE_BY
    bot, _calls, _key = _undone_review(db, tmp_path, monkeypatch)
    _redeliver(db, bot, how)
    assert db.get_approval("v2u")["decided_by"] != UNDONE_BY        # the new card ends what the Undo gave back
    clock = _wait_clock(monkeypatch)
    assert bot.wait_for_decision("v2u", timeout_seconds=120, poll_interval=5) == "timeout"
    assert 115 <= clock["dt"] <= 125                                  # its own window
    assert db.get_approval("v2u")["expires_at"] <= clock["t0"] + 121  # and the expiry is narrowed back to it


@pytest.mark.parametrize("how", ["resend", "edit"])
def test_a_card_resent_during_the_wait_after_an_undo_ends_when_the_undo_and_the_new_window_do(
        db, tmp_path, monkeypatch, how):
    """The wait after the Undo is running when the card is re-sent: it keeps the time the Undo gave back (10 min,
    the Undo window) and the re-sent card's own window, not the delivery's 24 h placeholder."""
    bot, _calls, _key = _undone_review(db, tmp_path, monkeypatch)
    given = db.get_approval("v2u")["expires_at"]

    def resend_once(clock):
        if clock["sleeps"] == 3:
            _redeliver(db, bot, how)

    clock = _wait_clock(monkeypatch, resend_once)
    assert bot.wait_for_decision("v2u", timeout_seconds=120, poll_interval=5) == "timeout"
    assert abs(clock["t0"] + clock["dt"] - given) <= 6               # the Undo's time is kept, and no more
    assert 595 <= clock["dt"] <= 606


def test_an_undo_gives_back_at_most_the_review_window(db, tmp_path, monkeypatch):
    """Review 3: a decision before any wait narrowed the delivery's 24 h placeholder: the Undo gave back about 24 h.
    It gives back at most the review window (the coordinator's approval window, else APPROVAL_TIMEOUT_SECONDS)."""
    import time
    monkeypatch.setattr(config, "REVIEW_UNDO_MINUTES", 10)
    bot, _calls, key = v2_review(db, tmp_path, monkeypatch)
    assert db.get_approval("v2u")["expires_at"] > time.time() + 86000   # the delivery's placeholder, not narrowed
    bot.approval_window = lambda: 1200
    assert bot._handle_callback(tap(f"approve:{key}")) == "approved"
    assert bot._handle_callback(tap(f"undoappr:{key}", qid="u")) == "undone"
    assert time.time() + 1190 <= db.get_approval("v2u")["expires_at"] <= time.time() + 1201
    # without the coordinator's sizer: the configured timeout caps it, and never less than the Undo window
    bot2, _c, key2 = v2_review(db, tmp_path, monkeypatch, "v2v")
    monkeypatch.setattr(telegram_bot, "APPROVAL_TIMEOUT_SECONDS", 300)
    assert bot2._handle_callback(tap(f"approve:{key2}")) == "approved"
    assert bot2._handle_callback(tap(f"undoappr:{key2}", qid="u2")) == "undone"
    assert time.time() + 590 <= db.get_approval("v2v")["expires_at"] <= time.time() + 601


@pytest.mark.parametrize(("raw", "minutes"), [("10", 10.0), ("15", 15.0), ("20", 20.0), ("60", 20.0), ("240", 20.0),
                                              ("0", 0.0), ("-5", 0.0)])
def test_review_undo_minutes_is_held_at_20(raw, minutes):
    """Review 3: above the approval window's 20-minute margin a last-minute approval is acted on after its slot."""
    assert config.REVIEW_UNDO_MINUTES_MAX == 20.0
    assert config.review_undo_minutes(raw) == minutes
