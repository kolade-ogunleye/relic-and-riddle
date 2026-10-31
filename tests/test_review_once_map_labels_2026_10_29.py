"""29 Oct production: one review card per run (a cover lock sent a second), country names cut at the map's
edge during the zoom, and "Vote: orbit or on foot?" passing the engagement-bait check."""
import json

import pytest
from PIL import Image

from src import control, graphics, script_engine
from src.script_engine import engagement_bait, story_body_word_limit
from src.telegram_bot import TelegramApprovalBot
from src.telegram_state import get, put, review_fingerprint


# ---------------------------------------------------------------- 1. one review card per run
def _review(db, root, card=1744, cover_msg=1741):
    """A delivered review as send_approval_request leaves it: an album opening with the cover, the card."""
    from src.telegram_state import initialize
    initialize(db)
    topic = db.list_all_topics()[0]["id"]
    directory = root / "review"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "master.mp4").write_bytes(b"offline review video")
    Image.new("RGB", (20, 20), "black").save(directory / "thumbnail_cover.jpg")
    Image.new("RGB", (20, 20), "black").save(directory / "thumbnail_variant_a_blueprint.jpg")
    Image.new("RGB", (20, 20), "red").save(directory / "thumbnail_variant_b_intrigue.jpg")
    db.create_run("review", topic)
    db.update_run("review", "pending_approval", script_payload={"caption": "Original", "title": "Derinkuyu"},
                  master_video_path=directory / "master.mp4")
    db.record_approval("review", topic, card)
    fingerprint = review_fingerprint(db, "review")
    put(db, "review-card", f"review:{card}", fingerprint)
    put(db, "review-media", "review", fingerprint)
    put(db, "review-messages", "review", [cover_msg, cover_msg + 1, cover_msg + 2, card])
    put(db, "review-cover", "review", cover_msg)
    return directory


def _bot(db, monkeypatch):
    bot = TelegramApprovalBot(db=db, bot_token="123:offline", chat_id="555", approver_ids={"555"})
    calls = []
    monkeypatch.setattr(bot, "_api", lambda method, **kw: calls.append((method, kw)) or {"message_id": 1760})
    return bot, calls


def _approve(mid):
    return {"id": "q", "data": "approve:review", "from": {"id": 555}, "message": {"message_id": mid, "chat": {"id": 555}}}


def test_a_cover_lock_edits_the_review_in_place_and_sends_no_second_card(db, tmp_path, monkeypatch):
    # Prod 2026-10-29 (Derinkuyu): covlock at 23:05:14, then "Approval request sent (message 1760)" at 23:06:06
    # beside card 1744, and "A wait on ...'s review is already running".
    from src import config
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    root = _review(db, tmp_path)
    bot, calls = _bot(db, monkeypatch)
    resent = []
    handler = control.make_command_handler(db, lambda: None, lambda: "", run_actions={
        "cover": bot.refresh_review_cover, "resend": lambda run_id: resent.append(run_id) or "sent"})
    reply = handler("covlock", "review:b")
    assert reply.startswith("✅") and "card above now shows it" in reply, reply
    assert resent == []                                                   # no second delivery
    assert not [m for m, _ in calls if m.startswith("send")]              # no new card, no new album
    edits = [kw for m, kw in calls if m == "editMessageMedia"]
    assert len(edits) == 1 and edits[0]["data"]["message_id"] == 1741    # the album's cover photo, replaced
    assert (root / "thumbnail_cover.jpg").read_bytes() == (root / "thumbnail_variant_b_intrigue.jpg").read_bytes()
    assert db.get_approval("review")["telegram_message_id"] == 1744       # the same card is current again
    assert get(db, "review-card", "review:1744") == review_fingerprint(db, "review")
    assert bot._handle_callback(_approve(1744)) == "approved"            # and approves the locked cover


def test_a_cover_lock_without_a_recorded_cover_photo_falls_back_to_a_resend(db, tmp_path, monkeypatch):
    from src import config
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    _review(db, tmp_path)
    put(db, "review-cover", "review", None)                               # a review sent before this release
    bot, calls = _bot(db, monkeypatch)
    resent = []
    handler = control.make_command_handler(db, lambda: None, lambda: "", run_actions={
        "cover": bot.refresh_review_cover, "resend": lambda run_id: resent.append(run_id) or "sent"})
    assert "being sent again" in handler("covlock", "review:b")
    assert resent == ["review"] and not [m for m, _ in calls if m == "editMessageMedia"]


def test_a_failed_cover_edit_falls_back_and_leaves_the_card_unbound(db, tmp_path, monkeypatch):
    from src import config
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    _review(db, tmp_path)
    bot = TelegramApprovalBot(db=db, bot_token="123:offline", chat_id="555", approver_ids={"555"})

    def api(method, **kw):
        if method == "editMessageMedia":
            raise RuntimeError("Bad Request: message to edit not found")
        return {"message_id": 1}
    monkeypatch.setattr(bot, "_api", api)
    resent = []
    handler = control.make_command_handler(db, lambda: None, lambda: "", run_actions={
        "cover": bot.refresh_review_cover, "resend": lambda run_id: resent.append(run_id) or "sent"})
    handler("covlock", "review:b")
    assert resent == ["review"]
    assert db.get_approval("review")["telegram_message_id"] is None       # the old card cannot approve


def test_a_resend_after_the_card_id_was_cleared_takes_the_old_cards_buttons_first(db, tmp_path, monkeypatch):
    # An edit or a cover lock clears the approval's card id: the resend then left the old card's buttons live.
    root = _review(db, tmp_path)
    with db._conn() as conn:
        conn.execute("UPDATE approvals SET telegram_message_id=NULL WHERE run_id='review'")
    bot = TelegramApprovalBot(db=db, bot_token="123:offline", chat_id="555", approver_ids={"555"})
    order, nxt = [], [1750]

    def api(method, **kw):
        data = kw.get("data") or {}
        order.append((method, data.get("message_id")))
        if method == "sendMediaGroup":
            ids = list(range(nxt[0], nxt[0] + len(json.loads(data["media"]))))
            nxt[0] += len(ids)
            return [{"message_id": m} for m in ids]
        if method in ("deleteMessages", "editMessageReplyMarkup"):
            return True
        nxt[0] += 1
        return {"message_id": nxt[0] - 1}
    monkeypatch.setattr(bot, "_api", api)
    monkeypatch.setattr("src.telegram_bot.scene_strip", lambda video, out: None)
    card = bot.send_approval_request(root / "master.mp4", root / "thumbnail_cover.jpg", {"title": "Derinkuyu"}, "review")
    strip = order.index(("editMessageReplyMarkup", 1744))
    first_send = next(i for i, (m, _) in enumerate(order) if m.startswith("send"))
    assert strip < first_send
    assert get(db, "review-cover", "review") == get(db, "review-messages", "review")[0] != card


# ---------------------------------------------------------------- 2. no country name cut at the frame's edge
def test_every_map_label_is_wholly_inside_the_safe_box_on_every_frame(monkeypatch):
    from src import geo
    if not geo.available():
        pytest.skip("Natural Earth data not installed")
    plans = []
    real = graphics.map_label_plan
    monkeypatch.setattr(graphics, "map_label_plan", lambda *a, **k: plans.append(real(*a, **k)) or plans[-1])
    monkeypatch.setattr(graphics, "_encode", lambda frames, out, n, width, height: [next(iter(frames))] and out)
    place = {"lat": 38.373403, "lon": 34.735034, "country": "Turkey", "label": "Derinkuyu Underground City"}
    graphics.render_map(place, graphics.Path("unused.mp4"))
    (plan,) = plans
    w, h = graphics.VIDEO_WIDTH, graphics.VIDEO_HEIGHT
    x0, y0, x1, y1 = graphics.get_text_box(w, h)
    assert len(plan) == int(graphics.CLIP_SECONDS * graphics.VIDEO_FPS)
    names = {lab["name"] for frame in plan for lab in frame}
    assert {"Lithuania", "Ukraine"} & names                                # names are still drawn in the wide view
    for i, frame in enumerate(plan):
        for lab in frame:
            l, t, r, b = lab["box"]
            assert 0 <= l and r <= w and 0 <= t and b <= h, (i, lab["name"], lab["box"])
            assert x0 <= l and r <= x1 and y0 <= t and b <= y1, (i, lab["name"], lab["box"])
    # a name that leaves fades out, and never comes back once gone
    for name in names:
        shown = [any(lab["name"] == name for lab in frame) for frame in plan]
        if not all(shown):
            assert shown == sorted(shown, reverse=True), name


# ---------------------------------------------------------------- 3. vote baiting
VOTE_BAIT = ["Vote: orbit or on foot?", "Vote now!", "Cast your vote below.", "Vote for your favourite wonder.",
             "Vote A or B.", "Which was harder? Vote in the comments.", "Vote below: Rome or Greece?",
             "🗳️ Vote: tunnels or towers?", "So vote now, before it ends."]
NOT_VOTE_BAIT = ["The Romans would vote with stones.", "Athenians would vote in the assembly once a year.",
                 "Would you vote for Rome or Greece?", "Which would you choose: orbit or on foot?",
                 "Vote counts were scratched onto pottery shards.", "Citizens lined up to vote at dawn.",
                 "Send this to a friend who loves lost cities, and follow for the next one."]


@pytest.mark.parametrize("text", VOTE_BAIT)
def test_an_order_to_vote_is_engagement_bait(text):
    assert engagement_bait(text), text


@pytest.mark.parametrize("text", NOT_VOTE_BAIT)
def test_a_vote_told_a_question_or_the_share_and_follow_line_is_not_bait(text):
    assert engagement_bait(text) == [], text


def test_the_poll_frames_body_has_the_full_story_budget(monkeypatch):
    # The poll frame draws no option boxes since rc73, so slide 3's body is not shortened.
    monkeypatch.setattr(script_engine, "_story_word_limits", {})
    assert script_engine._POLL_BODY_SLACK == 0
    assert story_body_word_limit(asks_poll=True) == story_body_word_limit()
