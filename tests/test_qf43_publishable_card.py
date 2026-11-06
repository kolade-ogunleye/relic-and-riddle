"""QF-43: the review card shows every string Approve publishes, word for word per platform (the caption after the
illustration disclosure, the YouTube Short's title, description and tags), the slot an Approve books and when the
review closes; the hashtags come from the topic's candidate list; build_envelope refuses copy the card did not show."""

import html
import json
import re
from datetime import UTC, datetime

import pytest
from PIL import Image

from src import config, post_schedule, publisher
from src.db import PipelineDatabase
from src.telegram_bot import TelegramApprovalBot
from src.telegram_state import initialize, put, review_fingerprint

TOPIC = {"id": "stone_city", "title": "The Stone City", "sub_theme": "Lost Cities", "hook": "h",
         "location": {"name": "Stone City", "city": "Rockford", "country": "Narnia"},
         "hashtags": ["#StoneCity", "#AncientMysteries", "#Archaeology", "#ReelsViral"]}
CAPTION = ("A city carved into one hill 🏛️\nIts builders left no name.\nWould you go down?\n"
           "#StoneCity #Archaeology #BuyFollowersNow")
PAYLOAD = {"topic_id": "stone_city", "title": "The City Inside the Hill", "caption": CAPTION,
           "first_comment": "Which level would you explore first?",
           "hashtags": ["#StoneCity", "#Archaeology", "#BuyFollowersNow"],
           "picture_fit": {"generated": [{"beat": 3}]}}


@pytest.fixture
def review(tmp_path):
    bank = tmp_path / "topic_bank.json"
    bank.write_text(json.dumps({"topics": [TOPIC]}), encoding="utf-8")
    db = PipelineDatabase(db_path=tmp_path / "test.db", topic_bank_file=bank)
    initialize(db)
    run_dir = tmp_path / "run_stone"
    run_dir.mkdir()
    (run_dir / "master.mp4").write_bytes(b"offline review video")
    Image.new("RGB", (20, 20)).save(run_dir / "thumbnail_cover.jpg")
    db.create_run("run_stone", "stone_city")
    db.update_run("run_stone", "pending_approval", script_payload=PAYLOAD, master_video_path=run_dir / "master.mp4")
    db.record_approval("run_stone", "stone_city", None)
    put(db, "review-media", "run_stone", review_fingerprint(db, "run_stone"))
    return db, run_dir


def _send(db, monkeypatch, payload=PAYLOAD, coordinator=None):
    bot = TelegramApprovalBot(db=db, bot_token="123:offline", chat_id="555", approver_ids={"555"})
    if coordinator is not None:   # as PipelineCoordinator.__init__ wires its bot
        bot.approve_posting = coordinator._approve_posting
    calls = []
    monkeypatch.setattr(bot, "_api", lambda method, **kw: calls.append((method, kw.get("data", {}))) or {"message_id": 300})
    bot.send_review_card("run_stone", payload)
    return next(d for m, d in calls if m == "sendMessage")["text"]


def _coordinator(db, monkeypatch, schedule_posts=True):
    """A coordinator holding only the database, posting slots on (the daemon's) or off (the command line's)."""
    import main
    monkeypatch.setattr(main, "POST_SCHEDULING", True)
    monkeypatch.setattr(config, "POST_SCHEDULING", True)
    coordinator = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    coordinator.db = db
    coordinator.schedule_posts = schedule_posts
    return coordinator


def test_card_shows_disclosure_youtube_copy_and_the_booked_slot(review, monkeypatch):
    db, _ = review
    monkeypatch.setattr(config, "YOUTUBE_SHORTS", True)
    text = _send(db, monkeypatch, coordinator=_coordinator(db, monkeypatch))
    assert publisher.ILLUSTRATION_DISCLOSURE in text              # the posted caption, not the written one
    # The Short's title and description are the card's title and caption, named as such; its tags listed
    assert "YouTube Short: the title above, the caption above, the hashtags as tags" in text
    assert f"📌 <b>{html.escape(PAYLOAD['title'])}</b>" in text
    slot = post_schedule.next_reel_slot(datetime.now(UTC), set())
    assert f"🗓 <b>Slot:</b> {html.escape(post_schedule.fmt(slot))}" in text
    assert "review closes" in text


def test_hashtags_come_from_the_topics_candidate_list(review, monkeypatch, tmp_path):
    db, run_dir = review
    text = _send(db, monkeypatch)
    assert "#BuyFollowersNow" in text.split("Hashtags not posted (not on this topic's list):")[1]
    assert "#BuyFollowersNow" not in text.split("Hashtags not posted")[0]
    video = run_dir / "master.mp4"
    env = publisher.MetaPublisher(db=db, publishers=[]).build_envelope("run_stone", video, video, video, PAYLOAD)
    meta = env["metadata"]
    assert "#BuyFollowersNow" not in meta["caption"] and "BuyFollowersNow" not in meta["hashtags"]
    assert meta["hashtags"] == ["#StoneCity", "#Archaeology", "#AncientMysteries"]
    assert meta["caption"].endswith(publisher.ILLUSTRATION_DISCLOSURE)


def test_build_envelope_raises_on_copy_the_card_did_not_show(review, monkeypatch):
    db, run_dir = review
    _send(db, monkeypatch)
    video = run_dir / "master.mp4"
    mp = publisher.MetaPublisher(db=db, publishers=[])
    mp.build_envelope("run_stone", video, video, video, PAYLOAD)          # what the card showed: posts
    with pytest.raises(ValueError, match="not what the review card showed"):
        mp.build_envelope("run_stone", video, video, video, dict(PAYLOAD, title="A different title"))
    from src import publishable
    meta = publishable.envelope_metadata(PAYLOAD, TOPIC)
    with pytest.raises(ValueError, match="not a publishable field"):
        publishable.check_envelope(dict(meta, sponsor_link="https://x.example"), None, youtube=False)


def test_documentary_card_shows_its_youtube_title_and_description():
    from src import publishable
    payload = dict(PAYLOAD, delivery_profile="deep_dive_16x9", youtube_title="Inside the Stone City",
                   description="The long description.\nSecond line.", tags=["stone city", "archaeology"])
    posted = publishable.card_record(payload, None)
    assert posted["documentary"]
    text = TelegramApprovalBot.format_card_text({**payload, "_publish_copy": posted}, "run_doc")
    assert "YouTube title: Inside the Stone City" in text
    assert ("YouTube description: The long description. ⏎ Second line. ⏎  ⏎ "
            + html.escape(publisher.ILLUSTRATION_DISCLOSURE)) in text
    assert "YouTube tags: stone city, archaeology" in text


class _TopicOnly:
    """A database holding only the topic record (card_record reads nothing else; no slot is booked)."""

    def get_topic_by_id(self, topic_id):
        return TOPIC if topic_id == "stone_city" else None


def _posted_payload(payload, posted):
    """The payload with the strings Approve posts in place of the written ones."""
    copy = posted["copy"]
    return dict(payload, title=copy["reel.title"], caption=copy["reel.caption"], first_comment=copy["reel.first_comment"],
                carousel_caption=copy["carousel.instagram"], hashtags=posted["hashtags"])


@pytest.mark.parametrize("narration_words", [250, 900])
def test_what_else_is_posted_costs_the_card_nothing(narration_words):
    """Review of QF-43 round 1 on 30 real cards: laid out with the card, the slot line and the "Also posted" list cut
    narration and dropped captions' "see Edit" lines. The card is fitted as QF-42 fits it, on the posted strings, and
    they take only the room it leaves: the card without them is that layout exactly; the slot line sits in the room
    kept for a decision's badge (one message under PAGE_ABOVE); the decided card has neither."""
    from src import publishable
    from src.telegram_bot import (
        CARD_LIST_MORE,
        CARD_PAGE_LIMIT,
        _tg_len,
        needs_publish_reply,
    )
    from src.telegram_pages import PAGE_ABOVE
    payload = dict(PAYLOAD, caption="A sentence of ordinary length for the caption. " * 16 + "\n#StoneCity",
                   carousel_caption="Swipe through the rooms. " * 12, narration_script="Word " * narration_words,
                   qa_summary="QA findings\n" + "\n".join(f"· finding {n} " + "detail " * 12 for n in range(12)))
    posted = publishable.card_record(payload, _TopicOnly())
    posted["copy"].update({f"alt.instagram.{n}": "A carved stone stair winds down into a dark hall. " * 4
                           for n in range(1, 11)})
    card = TelegramApprovalBot.format_card_text({**payload, "_publish_copy": posted}, "run_alt")
    layout = TelegramApprovalBot.format_card_text(_posted_payload(payload, posted), "run_alt")
    assert _tg_len(layout) > CARD_PAGE_LIMIT - 200                  # a full card: no room to spare
    fitted = re.sub(r"🗓 <b>Slot:</b>[^\n]*\n", "", card, count=1)
    fitted = re.sub(r"\n📤 <b>Also posted(?:, word for word</b>\n<blockquote expandable>.*?</blockquote>|:</b>[^\n]*)\n",
                    "", fitted, count=1, flags=re.DOTALL)
    more = f": {CARD_LIST_MORE}"
    assert fitted.replace(more, "") == layout.replace(more, "")     # narration, captions, findings: as QF-42 lays them
    assert "🗓 <b>Slot:</b> posted as soon as you approve" in card and _tg_len(card) <= PAGE_ABOVE
    assert needs_publish_reply(card, posted)                         # the reply under the card has the list whole
    # Its count where the room under PAGE_ABOVE takes it (the 250-word card has too little of that room left)
    assert ("📤 <b>Also posted:</b> <i>14 items in the reply</i>" in card) == (narration_words == 900)
    decided = TelegramApprovalBot.format_card_text({**payload, "_publish_copy": posted}, "run_alt", reserve=60)
    assert _tg_len(decided) <= CARD_PAGE_LIMIT - 60 and "Slot:" not in decided
    full = TelegramApprovalBot._card_text({**payload, "_publish_copy": posted}, "run_alt", None, None, 10 ** 6,
                                          extras=True)
    assert all(f"Alt text, slide {n} (Instagram)" in full for n in range(1, 11))   # the reply has each whole


def test_a_full_card_sends_the_whole_list_in_the_reply(review, monkeypatch):
    db, _ = review
    payload = dict(PAYLOAD, narration_script="Word " * 900, caption="A sentence for the caption. " * 30,
                   qa_summary="QA findings\n" + "\n".join(f"· finding {n} " + "detail " * 12 for n in range(12)))
    bot = TelegramApprovalBot(db=db, bot_token="123:offline", chat_id="555", approver_ids={"555"})
    calls = []
    monkeypatch.setattr(bot, "_api", lambda method, **kw: calls.append((method, kw.get("data", {}))) or {"message_id": 300})
    bot.send_review_card("run_stone", payload)
    card, *reply = [d["text"] for m, d in calls if m == "sendMessage"]
    assert "Facebook Reel: the title and the caption above" not in card
    assert any("• Facebook Reel: the title and the caption above" in text for text in reply)


CAROUSEL = dict(PAYLOAD, carousel_caption="Swipe the rooms.\n#StoneCity #BuyFollowersNow")


def _carousel_coordinator(db, run_dir, monkeypatch):
    """The run with its carousel slides and CAROUSEL's copy, reviewed media delivered; a coordinator holding only
    the database, the publisher (its photo post captured) and an alert list."""
    from main import PipelineCoordinator
    for n in range(1, 4):
        (run_dir / "carousel").mkdir(exist_ok=True)
        Image.new("RGB", (20, 20)).save(run_dir / "carousel" / f"carousel_slide_{n:02d}.jpg")
    db.update_run("run_stone", "pending_approval", script_payload=CAROUSEL)
    put(db, "review-media", "run_stone", review_fingerprint(db, "run_stone"))
    coordinator = PipelineCoordinator.__new__(PipelineCoordinator)
    coordinator.db = db
    coordinator.publisher = publisher.MetaPublisher(db=db, publishers=[])
    coordinator.alerts = []
    coordinator._alert = coordinator.alerts.append
    sent = []
    monkeypatch.setattr(coordinator.publisher, "publish_photos", lambda run_id, tid, kind, files, caption="", **kw:
                        sent.append(caption) or {"status": "published"})
    files = [str(p) for p in sorted((run_dir / "carousel").glob("carousel_slide_*.jpg"))]

    def post():
        post_id = db.add_scheduled_post("run_stone", "carousel", None, files)
        return coordinator._post_photos({"id": post_id, "run_id": "run_stone", "kind": "carousel", "files": files},
                                        db.get_run("run_stone"), "carousel")
    return coordinator, sent, post


def test_the_carousel_posts_the_caption_the_card_showed(review, monkeypatch):
    """Review of QF-43 round 1: the card showed the carousel caption with the topic's hashtags while the carousel
    post sent the written one (#BuyFollowersNow); the carousel had no check against the card."""
    from src import publishable
    db, run_dir = review
    _, sent, post = _carousel_coordinator(db, run_dir, monkeypatch)
    _send(db, monkeypatch, CAROUSEL)
    shown = publishable.load_record(db, "run_stone")["copy"]
    assert post() == "published"
    assert sent == ["Swipe the rooms.\n#StoneCity #Archaeology #AncientMysteries"]
    assert publisher._caption_for("carousel", sent[0], ["instagram"]) == shown["carousel.instagram"]
    assert publisher._caption_for("carousel", sent[0], ["facebook"]) == shown["carousel.facebook"]


def test_the_carousel_is_refused_when_its_caption_is_not_the_one_the_card_showed(review, monkeypatch):
    db, run_dir = review
    coordinator, sent, post = _carousel_coordinator(db, run_dir, monkeypatch)
    _send(db, monkeypatch, CAROUSEL)
    db.update_run("run_stone", "approved", script_payload=dict(CAROUSEL, carousel_caption="Changed after the card."))
    assert post() == "failed" and sent == []
    assert "not what the review card showed" in coordinator.alerts[-1]


def test_a_short_card_lists_everything_and_needs_no_reply(review, monkeypatch):
    db, _ = review
    monkeypatch.setattr(config, "YOUTUBE_SHORTS", False)
    bot = TelegramApprovalBot(db=db, bot_token="123:offline", chat_id="555", approver_ids={"555"})
    calls = []
    monkeypatch.setattr(bot, "_api", lambda method, **kw: calls.append((method, kw.get("data", {}))) or {"message_id": 300})
    bot.send_review_card("run_stone", dict(PAYLOAD, hashtags=["#StoneCity"]))
    texts = [d["text"] for m, d in calls if m == "sendMessage"]
    assert len(texts) == 1                                        # the card alone: nothing is left for a reply
    assert "• Facebook Reel: the title and the caption above" in texts[0]
    assert "• YouTube Short (not posted: YOUTUBE_SHORTS is off): the title above" in texts[0]


DOCUMENTARY = {k: v for k, v in PAYLOAD.items() if k != "topic_id"} | {   # a documentary's payload has no topic_id
    "delivery_profile": "deep_dive_16x9", "youtube_title": "Inside the Stone City",
    "description": "The long description.\n#StoneCity #BuyFollowersNow", "tags": ["stone city", "archaeology"]}


def test_a_documentary_without_a_topic_id_is_posted_after_its_card(review, monkeypatch):
    """Review of QF-43 round 2: the card looked the topic up in the payload alone, which a documentary's lacks
    (publish_run fills it from the run), so its card recorded no topic hashtags while the envelope had them, and
    Approve was refused ("the hashtags to be posted is not what the review card showed"), resend or not."""
    from src import publishable
    db, run_dir = review
    db.update_run("run_stone", "pending_approval", script_payload=DOCUMENTARY)
    put(db, "review-media", "run_stone", review_fingerprint(db, "run_stone"))
    _send(db, monkeypatch, DOCUMENTARY, coordinator=_coordinator(db, monkeypatch))
    record = publishable.load_record(db, "run_stone")
    assert record["documentary"] and record["hashtags"] == ["#StoneCity", "#Archaeology", "#AncientMysteries"]
    payload = {**DOCUMENTARY, "topic_id": db.get_run("run_stone")["topic_id"]}   # as publish_run fills it
    video = run_dir / "master.mp4"
    env = publisher.MetaPublisher(db=db, publishers=[]).build_envelope("run_stone", video, video, video, payload,
                                                                       platforms=["youtube"])
    posted = publisher.youtube_fields(env["metadata"])
    assert posted["description"] == record["copy"]["youtube.description"] and "#BuyFollowersNow" not in posted["description"]
    # The documentary's upload carries no hashtags: they are not held to the card for it
    publishable.check_envelope(env["metadata"], dict(record, copy=dict(record["copy"], hashtags="")), youtube=True)


@pytest.mark.parametrize(("case", "line"), [
    ("reel, slots booked", None),
    ("documentary", "🗓 <b>Slot:</b> posted as soon as you approve"),
    ("reel, no slots (command line)", "🗓 <b>Slot:</b> posted as soon as you approve"),
    ("reel, dry run", "🗓 <b>Slot:</b> none, a dry run posts nothing"),
])
def test_the_slot_line_shows_a_slot_only_when_approve_books_one(review, monkeypatch, case, line):
    """Review of QF-43 round 2: a documentary's card (and any card while the coordinator books no slots) showed
    the next Reel slot, although Approve posts it at once (_after_approval books only a scheduled, live Reel)."""
    db, _ = review
    coordinator = _coordinator(db, monkeypatch, schedule_posts=case != "reel, no slots (command line)")
    payload = PAYLOAD
    if case == "documentary":
        db.update_run("run_stone", "pending_approval", script_payload=DOCUMENTARY)
        payload = DOCUMENTARY
        monkeypatch.setattr(db, "get_run", lambda run_id, _get=db.get_run: {**_get(run_id), "content_format": "deep_dive"})
        put(db, "review-media", "run_stone", review_fingerprint(db, "run_stone"))
    if case == "reel, dry run":
        db.set_setting("run_mode:run_stone", "dry_run")
    text = _send(db, monkeypatch, payload, coordinator=coordinator)
    expected = {"reel, slots booked": "slot", "documentary": "now", "reel, no slots (command line)": "now",
                "reel, dry run": "dry_run"}[case]
    assert coordinator._approve_posting("run_stone") == expected          # what _after_approval does on Approve
    slot_line = next(ln for ln in text.splitlines() if "Slot:" in ln)
    if line is None:
        slot = post_schedule.next_reel_slot(datetime.now(UTC), set())
        assert slot_line.startswith(f"🗓 <b>Slot:</b> {html.escape(post_schedule.fmt(slot))}")
    else:
        assert slot_line.startswith(line) and "review closes" in slot_line
