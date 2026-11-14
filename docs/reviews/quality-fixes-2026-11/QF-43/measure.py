"""QF-43 acceptance metric: does the review card show, word for word, every string one Approve publishes (the Reel,
the YouTube Short and the carousel); does it show the slot an Approve books; do the hashtags come from the topic's
candidate list; and do build_envelope and the carousel post refuse copy the card did not show?

Usage: python measure.py <repo root>. Prints the metric as JSON and writes measurements.json next to this file.

The real code paths of the repo given run offline: a temporary database with one topic (its record below) and two
rendered runs (placeholder video, cover and carousel slides the script writes), TelegramApprovalBot.send_review_card
with the Telegram request captured (TelegramApprovalBot._api answers from this script), MetaPublisher.build_envelope,
UploadPostPublisher.publish / publish_short, and the scheduled carousel post (PipelineCoordinator._post_photos, on a
coordinator holding only the database, the publisher and an alert list) with requests.post captured: the form fields
each request would send are recorded and answered from this script. Any real socket connection raises. The Reel run
uses a generated illustration (picture_fit.generated), so its posted caption carries the illustration disclosure;
the carousel run's carousel caption carries a hashtag that is not on the topic's list.

Optional, with RR_FIXTURE_REPO set to a checkout that has an output/ folder, each real run (read only):
- its review card (format_card_text, with the run's release report). On a tree with src/publishable.py the card
  carries what Approve posts (publishable.card_record with the committed topic bank, a fixed clock and a one-hour
  review window), as send_review_card builds it, and is compared with the QF-42 card of the same tree (no record):
  narration, each caption, the findings' lists, the credits list and the length must be no worse; and its fitted
  part (the card without the slot line and the "Also posted" list) must be QF-42's layout of the posted strings
  exactly (the added lines take only room the card leaves);
- its carousel: the caption the carousel post sends (PipelineCoordinator._carousel_request on the card's record)
  against the card's carousel caption, and any hashtag in it that is not on the topic's list;
- Approve on it: the run recorded with its topic (the committed topic bank's id in the run's folder name; a
  documentary's payload has none) and its format, the card's record made as send_review_card makes it (the run's
  topic, what Approve does from PipelineCoordinator._approve_posting on a coordinator that books slots), then
  MetaPublisher.build_envelope as publish_run calls it (the payload's topic filled from the run; a documentary to
  YouTube): is the envelope refused, and does the slot line show a slot only when Approve books one.

Round 3 probes (synthetic runs, above): a documentary whose payload has no topic_id, its card sent and Approve's
envelope built as publish_run builds it; and the slot line of a booked Reel, a documentary, a Reel while the
coordinator books no slots and a dry run.
"""

import html
import json
import os
import re
import socket
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXED_NOW = datetime(2026, 11, 5, 15, 0, tzinfo=UTC)   # the real-run cards' clock (slot and closing time)
CAPTION_END = r"(?=\n<b>Carousel caption:</b>|\n?<b>Hashtags:</b>|\n?<b>Tags:</b>|</blockquote>)"
ALSO_SECTION = re.compile(r"\n📤 <b>Also posted, word for word</b>(?:\n<blockquote expandable>.*?</blockquote>)?\n"
                          r"|\n📤 <b>Also posted:</b> <i>\d+ items? in the reply</i>\n", re.DOTALL)
MORE = ": the full list is in the reply under this card"   # telegram_bot.CARD_LIST_MORE, said once on a card
SLOT_LINE = re.compile(r"🗓 <b>Slot:</b>[^\n]*\n")
RANK = {"absent": 0, "too_long": 1, "clipped": 2, "full": 3}


def _caption(card, label):
    """[how the card shows the caption, characters of its text shown]: full, clipped, too_long or absent."""
    m = re.search(rf"<b>{label}:</b>\n(.*?)\n{CAPTION_END}", card, re.DOTALL)
    if not m:
        return ["absent", 0]
    text = m.group(1)
    if "too long for this card" in text:
        return ["too_long", 0]
    prose = "\n".join(ln for ln in html.unescape(text).splitlines() if not ln.startswith("📷")).strip()
    return ["clipped" if "(full text: Edit)" in text else "full", len(prose.replace(" (full text: Edit)", ""))]


def _card_row(card):
    m = re.search(r"Narration and timings:</b>\n<i>(.*?)</i>\n\n<b>First comment", card, re.DOTALL)
    narration = html.unescape(m.group(1)) if m else ""
    also = ALSO_SECTION.search(card)
    lists = ALSO_SECTION.sub("\n", card)   # the findings' lists: the "Also posted" list is not one of them
    return {"length": len(card.encode("utf-16-le")) // 2,
            "caption": _caption(card, "Caption"), "carousel_caption": _caption(card, "Carousel caption"),
            "narration_chars": len(narration), "narration_shortened": "[shortened:" in narration,
            "findings_lists_first_items": len(re.findall(r"…and \d+ more", lists)),
            "findings_lists_count_only": len(re.findall(r"<i>\d+ items?\b", lists)),
            "credits_list": "Credits posted with the captions:" in card,
            "slot_line": "🗓 <b>Slot:</b>" in card,
            "also_posted_list": ("absent" if not also else "part" if "…and " in also.group(0)
                                 else "count" if re.search(r"<i>\d+ items?\b", also.group(0)) else "whole")}


def _regressions(qf42, qf43):
    """Where the QF-43 card shows less than the QF-42 card of the same run."""
    out = []
    if qf43["narration_chars"] < qf42["narration_chars"]:
        out.append(f"narration {qf42['narration_chars']} -> {qf43['narration_chars']}")
    for key in ("caption", "carousel_caption"):
        if RANK[qf43[key][0]] < RANK[qf42[key][0]]:
            out.append(f"{key} {qf42[key][0]} -> {qf43[key][0]}")
    for key in ("findings_lists_first_items", "findings_lists_count_only"):
        if qf43[key] > qf42[key]:
            out.append(f"{key} {qf42[key]} -> {qf43[key]}")
    if qf42["credits_list"] and not qf43["credits_list"]:
        out.append("credits list lost")
    if qf43["length"] > max(qf42["length"], 4000):
        out.append(f"length {qf42['length']} -> {qf43['length']}")
    return out


def _posted_payload(payload, posted):
    """The payload with the strings Approve posts in place of the written ones: QF-42's card of it is the layout
    the QF-43 card must keep."""
    copy = posted.get("copy") or {}
    out = dict(payload, title=copy.get("reel.title", payload.get("title")),
               caption=copy.get("reel.caption", payload.get("caption")),
               first_comment=copy.get("reel.first_comment", payload.get("first_comment")))
    if payload.get("carousel_caption") and "carousel.instagram" in copy:
        out["carousel_caption"] = copy["carousel.instagram"]
    if not posted.get("documentary"):
        out["hashtags"] = posted.get("hashtags") or []
    return out


def _real_runs(root):
    """The review card and the carousel caption of each real run (RR_FIXTURE_REPO's output/run_*), read only."""
    fixture = os.environ.get("RR_FIXTURE_REPO")
    out_dir = Path(fixture) / "output" if fixture else None
    if not out_dir or not out_dir.is_dir():
        return {"measured": False, "why": "RR_FIXTURE_REPO with an output/ folder is not set"}
    from src import config
    from src.telegram_bot import CARD_PAGE_LIMIT, TelegramApprovalBot
    try:
        from src import publishable
    except ImportError:            # the start tree: the card as it was
        publishable = None
    db = coordinator = None
    if publishable is not None:
        from main import PipelineCoordinator
        from src.db import PipelineDatabase
        from src.telegram_state import initialize
        db = PipelineDatabase(db_path=Path(tempfile.mkdtemp(prefix="qf43-real-")) / "measure.db",
                              topic_bank_file=root / "data" / "topics" / "topic_bank.json")
        initialize(db)
        coordinator = PipelineCoordinator.__new__(PipelineCoordinator)
        coordinator.db = db
        coordinator.schedule_posts = True   # the daemon's
        import main as main_module
        main_module.POST_SCHEDULING = True
        from src.publisher import MetaPublisher
        envelopes = MetaPublisher(db=db, publishers=[])
        bank = json.loads((root / "data" / "topics" / "topic_bank.json").read_text(encoding="utf-8"))
        topic_ids = sorted((t["id"] for t in bank.get("topics", []) if t.get("id")), key=len, reverse=True)
    config.POST_SCHEDULING = True
    rows, carousels = {}, {}
    for d in sorted(out_dir.glob("run_*")):
        meta, rep = d / "script_metadata.json", d / "release_report.json"
        if not (meta.is_file() and rep.is_file()):
            continue
        payload = json.loads(meta.read_text(encoding="utf-8"))
        release = json.loads(rep.read_text(encoding="utf-8"))
        plain = TelegramApprovalBot.format_card_text(payload, d.name, release)
        if publishable is None:
            rows[d.name] = _card_row(plain)
            continue
        documentary = payload.get("delivery_profile") == "deep_dive_16x9"
        topic_id = next((t for t in topic_ids if t in d.name), "")
        db.create_run(d.name, topic_id, content_format="deep_dive" if documentary else "reel")
        db.set_setting("run_mode:" + d.name, "live")
        rule = "now" if documentary else "slot"   # _after_approval on a live run, slots booked: a documentary posts at once
        approve = coordinator._approve_posting(d.name) if hasattr(coordinator, "_approve_posting") else rule
        try:
            posted = publishable.card_record(payload, db, run_dir=d, window_seconds=3600, now=FIXED_NOW,
                                             topic_id=topic_id, posting=approve)
        except TypeError:          # round 2's tree: the payload's topic alone, the slot whatever Approve does
            posted = publishable.card_record(payload, db, run_dir=d, window_seconds=3600, now=FIXED_NOW)
        card = TelegramApprovalBot.format_card_text({**payload, "_publish_copy": posted}, d.name, release)
        layout = TelegramApprovalBot.format_card_text(_posted_payload(payload, posted), d.name, release)
        fitted = SLOT_LINE.sub("", ALSO_SECTION.sub("", card, count=1), count=1)
        row = _card_row(card)
        row["qf42_card"] = _card_row(plain)
        row["regressions_vs_qf42_card"] = _regressions(row["qf42_card"], row)
        # The reply's pointer is said once, on the first shortened list: the count of the "Also posted" list can
        # take it from a list below it.
        row["fitted_part_is_qf42_layout_of_posted_copy"] = fitted.replace(MORE, "") == layout.replace(MORE, "")
        publishable.save_record(db, d.name, posted)
        try:   # Approve: the envelope as publish_run builds it
            envelopes.build_envelope(d.name, meta, meta, meta, {**payload, "topic_id": payload.get("topic_id") or topic_id},
                                     platforms=["youtube"] if documentary else None)
            row["approve_refused"] = ""
        except ValueError as e:
            row["approve_refused"] = str(e)
        slot_line = next((ln for ln in card.splitlines() if "Slot:" in ln), "")
        if not slot_line:   # no room on the card: the reply under it has the line (send_review_card)
            from src.telegram_bot import _schedule_line
            slot_line = _schedule_line(posted).strip()
        row["approve"] = rule
        row["slot_line_text"] = slot_line
        row["slot_line_truthful"] = bool(slot_line) and (
            ("posted as soon as you approve" not in slot_line and "dry run" not in slot_line) == (rule == "slot"))
        rows[d.name] = row
        if "carousel.instagram" in posted.get("copy", {}):
            slides = [str(p) for p in sorted((d / "carousel").glob("carousel_slide_*.jpg"))]
            caption, _ = coordinator._carousel_request(d.name, payload, slides)
            carousels[d.name] = _carousel_row(caption, posted)
    lens = [r["length"] for r in rows.values()]
    summary = {"measured": True, "cards": len(rows),
               "over_card_page_limit": sum(n > CARD_PAGE_LIMIT for n in lens),
               "over_page_above_4000": sum(n > 4000 for n in lens),   # telegram_pages.PAGE_ABOVE: one message
               "over_telegram_4096": sum(n > 4096 for n in lens), "longest": max(lens, default=0),
               "narration_shortened": sum(r["narration_shortened"] for r in rows.values()),
               "caption_see_edit_or_absent": sum(r["caption"][0] in ("too_long", "absent") for r in rows.values()),
               "carousel_caption_see_edit_or_absent": sum(r["carousel_caption"][0] in ("too_long", "absent")
                                                          for r in rows.values()),
               "caption_characters_shown": sum(r["caption"][1] + r["carousel_caption"][1] for r in rows.values()),
               "credits_list": sum(r["credits_list"] for r in rows.values()),
               "with_slot_line": sum(r["slot_line"] for r in rows.values()),
               "with_also_posted_list": {k: sum(r["also_posted_list"] == k for r in rows.values())
                                         for k in ("whole", "part", "count", "absent")}}
    if publishable is not None:
        summary.update({
            "cards_with_a_regression_vs_qf42_card": sum(bool(r["regressions_vs_qf42_card"]) for r in rows.values()),
            "fitted_part_is_qf42_layout_of_posted_copy": sum(r["fitted_part_is_qf42_layout_of_posted_copy"]
                                                             for r in rows.values()),
            "approve_refused": sum(bool(r["approve_refused"]) for r in rows.values()),
            "documentaries": sum(r["approve"] == "now" for r in rows.values()),
            "slot_line_truthful": sum(r["slot_line_truthful"] for r in rows.values()),
            "carousels": len(carousels),
            "carousel_posted_as_card_shows": sum(c["posted_as_card_shows"] for c in carousels.values()),
            "carousel_off_list_hashtag_posted": sum(bool(c["off_list_hashtags"]) for c in carousels.values())})
    return {**summary, "per_card": rows, "per_carousel": carousels}


def _carousel_row(caption, posted):
    """The carousel caption sent (each platform's request) against the card's: the card's lines, then only the photo
    credit lines the post adds; and any hashtag in it that is not one the card lists as posted."""
    from src.credits import is_attribution_line
    from src.publisher import _caption_for
    shown = posted.get("copy") or {}
    same = True
    for platform in ("instagram", "facebook"):
        sent = _caption_for("carousel", caption, [platform]).splitlines()
        card = shown.get(f"carousel.{platform}", "").splitlines()
        rest = sent[len(card):]
        same = same and sent[:len(card)] == card and all(not ln.strip() or is_attribution_line(ln) for ln in rest)
    tags = {t.casefold() for t in posted.get("hashtags") or []}
    off = sorted({t for t in re.findall(r"(?<![\w&/#])#[^\W\d_]\w*", caption) if t.casefold() not in tags})
    return {"posted_as_card_shows": same, "off_list_hashtags": off}


TOPIC = {"id": "stone_city", "title": "The Stone City", "sub_theme": "Lost Cities", "hook": "h",
         "location": {"name": "Stone City", "city": "Rockford", "country": "Narnia"},
         "hashtags": ["#StoneCity", "#AncientMysteries", "#Archaeology", "#ReelsViral"]}
CAPTION = ("A city carved into one hill 🏛️\nIts builders left no name.\nWould you go down?\n"
           "#StoneCity #Archaeology #BuyFollowersNow")
PAYLOAD = {"topic_id": "stone_city", "title": "The City Inside the Hill", "caption": CAPTION,
           "first_comment": "Which level would you explore first?",
           "hashtags": ["#StoneCity", "#Archaeology", "#BuyFollowersNow"],
           "picture_fit": {"generated": [{"beat": 3}]}}
# The reviewer's probe of round 1: the carousel caption carries a hashtag the topic's list does not have.
CAROUSEL_PAYLOAD = dict(PAYLOAD, carousel_caption="Swipe the rooms.\n#StoneCity #BuyFollowersNow")
OFF_LIST = "#BuyFollowersNow"
HASHTAG_LINE = re.compile(r"^(?:\s*#\w+)+\s*$")


class Stop(Exception):
    pass


class _Answer:
    """Upload-Post's answer to a photo request: published on each platform it named (no request leaves)."""
    status_code = 200
    text = ""

    def __init__(self, platforms):
        self.platforms = platforms

    def json(self):
        return {"success": True, "results": {p: {"success": True, "post_url": f"https://example.invalid/{p}"}
                                             for p in self.platforms}}


def _no_network():
    def refuse(*a, **k):
        raise RuntimeError("measure.py makes no network connection")
    socket.socket.connect = refuse
    socket.create_connection = refuse


def _requests(publisher, envelope):
    """The form fields of the Reel request (Facebook + Instagram) and of the YouTube Short request."""
    sent = []

    def post(url, headers=None, data=None, files=None, timeout=None):
        sent.append(list(data or []))
        raise Stop
    publisher.requests.post = post
    up = publisher.UploadPostPublisher(api_key="measure-key", user="measure-user", base_url="https://upload.invalid")
    for call in (up.publish, up.publish_short):
        try:
            call(envelope)
        except Stop:
            pass
    reel, short = sent
    return reel, short


def _hashtags_row(card):
    row = next((ln for ln in card.splitlines() if "<b>Hashtags:</b>" in ln), "")
    return re.sub(r"<[^>]+>", " ", row).replace("Hashtags:", " ").split()


def _caption_shown(card, posted):
    """`posted` on the card word for word: each line as text, its hashtag lines on the card's Hashtags line."""
    lines = [ln for ln in posted.splitlines() if ln.strip() and not HASHTAG_LINE.match(ln)]
    tags = [t for ln in posted.splitlines() if HASHTAG_LINE.match(ln) for t in ln.split()]
    return bool(lines) and all(html.escape(ln.strip()) in card for ln in lines) and tags == _hashtags_row(card)[:len(tags)]


def _title_shown(card, posted):
    return f"📌 <b>{html.escape(posted)}</b>" in card


def _short_shown(card, name, posted):
    """A YouTube Short string on the card: on its "YouTube Short" line as text, or named there as the card's title,
    caption or hashtags, which then are that string word for word (checked here, not taken on trust)."""
    row = next((ln for ln in card.splitlines() if "YouTube Short" in ln), "")
    one_line = html.escape(posted).replace("\n", " ⏎ ")
    if name == "short youtube_title":
        return bool(row) and (f"title {one_line}" in row or ("the title above" in row and _title_shown(card, posted)))
    if name == "short youtube_description":
        return bool(row) and (f"description {one_line}" in row
                              or ("the caption above" in row and _caption_shown(card, posted)))
    tags = [t.strip() for t in posted.split(",") if t.strip()]
    return bool(row) and (f"tags {one_line}" in row or ("the hashtags as tags" in row
                                                       and [t.lstrip("#") for t in _hashtags_row(card)] == tags))


def _shown(card, name, posted):
    if name.startswith("short "):
        return _short_shown(card, name, posted)
    if name in ("reel title", "reel facebook_title"):
        return _title_shown(card, posted)
    if name == "reel first_comment":
        return f"<b>First comment:</b> <i>{html.escape(posted)}</i>" in card
    return _caption_shown(card, posted)


def _review_run(db, tmp, run_id, payload, slides=0, topic_id=None, content_format="reel"):
    """A rendered run waiting for review (placeholder media), as send_review_card requires."""
    from PIL import Image

    from src.telegram_state import put, review_fingerprint
    run_dir = tmp / run_id
    run_dir.mkdir()
    (run_dir / "master.mp4").write_bytes(b"placeholder review video")
    Image.new("RGB", (20, 20)).save(run_dir / "thumbnail_cover.jpg")
    if slides:
        (run_dir / "carousel").mkdir()
        for n in range(1, slides + 1):
            Image.new("RGB", (20, 20), (40 * n, 60, 90)).save(run_dir / "carousel" / f"carousel_slide_{n:02d}.jpg")
    topic_id = topic_id or payload["topic_id"]
    db.create_run(run_id, topic_id, content_format=content_format)
    db.update_run(run_id, "pending_approval", script_payload=payload, master_video_path=run_dir / "master.mp4")
    db.record_approval(run_id, topic_id, None)
    put(db, "review-media", run_id, review_fingerprint(db, run_id))
    return run_dir


DOCUMENTARY = {k: v for k, v in PAYLOAD.items() if k != "topic_id"} | {   # a documentary's payload has no topic_id
    "delivery_profile": "deep_dive_16x9", "youtube_title": "Inside the Stone City",
    "description": "The long description.\n#StoneCity #BuyFollowersNow", "tags": ["stone city", "archaeology"]}


def _documentary_probe(db, tmp, publisher, send):
    """Review of round 2: a documentary's payload has no topic_id (publish_run fills it from the run before
    build_envelope), so a card that looked the topic up in the payload alone recorded other hashtags than the
    envelope's and Approve was refused. The card sent, then Approve's envelope built as publish_run builds it."""
    run_dir = _review_run(db, tmp, "run_documentary", DOCUMENTARY, topic_id="stone_city", content_format="deep_dive")
    send("run_documentary", DOCUMENTARY)
    payload = {**DOCUMENTARY, "topic_id": DOCUMENTARY.get("topic_id") or db.get_run("run_documentary")["topic_id"]}
    video = run_dir / "master.mp4"
    try:
        publisher.MetaPublisher(db=db, publishers=[]).build_envelope("run_documentary", video, video, video, payload,
                                                                     platforms=["youtube"])
        refused = ""
    except ValueError as e:
        refused = str(e)
    return {"documentary_approve_posts": not refused, "documentary_approve_refused": refused}


def _slot_probe(db, tmp, coordinator, send):
    """The card's slot line against what Approve does (main.PipelineCoordinator._after_approval books a slot only for
    a live Reel while the coordinator schedules posts; a documentary goes to YouTube at once)."""
    from src import post_schedule
    out = {}
    cases = (("reel, slots booked", "run_slot_reel", PAYLOAD, "reel", True, "live"),
             ("documentary", "run_slot_doc", DOCUMENTARY, "deep_dive", True, "live"),
             ("reel, no slots (command line)", "run_slot_cli", PAYLOAD, "reel", False, "live"),
             ("reel, dry run", "run_slot_dry", PAYLOAD, "reel", True, "dry_run"))
    for name, run_id, payload, fmt, schedule_posts, mode in cases:
        _review_run(db, tmp, run_id, payload, topic_id="stone_city", content_format=fmt)
        db.set_setting("run_mode:" + run_id, mode)
        coordinator.schedule_posts = schedule_posts
        books = (schedule_posts and mode == "live" and fmt != "deep_dive")   # _after_approval's rule
        line = next((ln for ln in send(run_id, payload).splitlines() if "Slot:" in ln), "")
        slot = html.escape(post_schedule.fmt(post_schedule.next_reel_slot(datetime.now(UTC), set())))
        shows_slot = slot in line
        out[name] = {"approve_books_slot": books, "slot_line": line,
                     "truthful": bool(line) and shows_slot == books and (books or "posted as soon as you approve" in line
                                                                         or "a dry run posts nothing" in line)}
    coordinator.schedule_posts = True
    return out


def _carousel_probe(db, tmp, publisher, send):
    """The carousel of a run whose carousel caption carries an off-list hashtag: the card sent, then the scheduled
    carousel post (PipelineCoordinator._post_photos) with each request's form captured; then the same post after
    the carousel caption was changed behind the card."""
    from main import PipelineCoordinator
    run_dir = _review_run(db, tmp, "run_carousel", CAROUSEL_PAYLOAD, slides=3)
    card = send("run_carousel", CAROUSEL_PAYLOAD)
    sent = []

    def post(url, headers=None, data=None, files=None, timeout=None):
        sent.append(list(data or []))
        return _Answer([v for k, v in data if k == "platform[]"])
    publisher.requests.post = post
    coordinator = PipelineCoordinator.__new__(PipelineCoordinator)
    coordinator.db = db
    coordinator.publisher = publisher.MetaPublisher(db=db, publishers=[publisher.UploadPostPublisher(
        api_key="measure-key", user="measure-user", base_url="https://upload.invalid")])
    alerts = []
    coordinator._alert = alerts.append
    files = [str(p) for p in sorted((run_dir / "carousel").glob("carousel_slide_*.jpg"))]

    def post_carousel():
        sent.clear()
        post_id = db.add_scheduled_post("run_carousel", "carousel", None, files)
        status = coordinator._post_photos({"id": post_id, "run_id": "run_carousel", "kind": "carousel", "files": files},
                                          db.get_run("run_carousel"), "carousel")
        return status, {dict(fields).get("platform[]"): dict(fields).get("title", "") for fields in sent}

    status, captions = post_carousel()
    ig = captions.get("instagram", "")
    shown = {platform: bool(text) and _caption_shown(card, text) for platform, text in captions.items()}
    changed = dict(CAROUSEL_PAYLOAD, carousel_caption="A caption the card never showed.\n#StoneCity")
    db.update_run("run_carousel", "approved", script_payload=changed)
    status_changed, captions_changed = post_carousel()
    return {
        "carousel_status": status,
        "carousel_posted_captions": captions,
        "carousel_caption_shown_on_card": shown,
        "carousel_off_list_hashtag_posted": any(OFF_LIST in text for text in captions.values()),
        "carousel_posted_hashtags": sorted(set(re.findall(r"#\w+", ig))),
        "carousel_refused_when_card_showed_another": status_changed == "failed" and not captions_changed,
    }


def main(root):
    root = Path(root).resolve()
    sys.path.insert(0, str(root))
    _no_network()

    from src import config, post_schedule, publisher
    from src.db import PipelineDatabase
    from src.telegram_bot import TelegramApprovalBot
    from src.telegram_state import initialize

    config.POST_SCHEDULING = True
    config.YOUTUBE_SHORTS = True
    tmp = Path(tempfile.mkdtemp(prefix="qf43-"))
    bank = tmp / "topic_bank.json"
    bank.write_text(json.dumps({"topics": [TOPIC]}), encoding="utf-8")
    db = PipelineDatabase(db_path=tmp / "measure.db", topic_bank_file=bank)
    initialize(db)
    run_dir = _review_run(db, tmp, "run_stone", PAYLOAD)

    import main as main_module
    from main import PipelineCoordinator
    main_module.POST_SCHEDULING = True
    coordinator = PipelineCoordinator.__new__(PipelineCoordinator)   # the daemon's: Approve books a Reel's slot
    coordinator.db = db
    coordinator.schedule_posts = True
    bot = TelegramApprovalBot(db=db, bot_token="123:offline", chat_id="555", approver_ids={"555"})
    bot.approve_posting = getattr(coordinator, "_approve_posting", None)   # as PipelineCoordinator.__init__ wires it
    calls = []
    bot._api = lambda method, **kw: calls.append((method, kw.get("data", {}))) or {"message_id": 300 + len(calls)}

    def send(run_id, payload):
        calls.clear()
        bot.send_review_card(run_id, payload)
        return next(d for m, d in calls if m == "sendMessage")["text"]

    card = send("run_stone", PAYLOAD)
    now = datetime.now(UTC)

    video = run_dir / "master.mp4"
    mp = publisher.MetaPublisher(db=db, publishers=[])
    envelope = mp.build_envelope("run_stone", video, video, video, PAYLOAD)
    reel, short = _requests(publisher, envelope)
    fields = {"reel " + k: v for k, v in reel if k in ("title", "instagram_title", "facebook_title",
                                                       "facebook_description", "first_comment")}
    fields.update({"short " + k: v for k, v in short if k in ("youtube_title", "youtube_description")})
    fields["short tags[]"] = ", ".join(v for k, v in short if k == "tags[]")
    shown = {name: bool(_shown(card, name, value)) for name, value in fields.items()}
    hashtags = re.findall(r"#\w+", " ".join(v for k, v in reel + short if k in ("instagram_title", "youtube_description")))

    changed = dict(PAYLOAD, title="A title the card never showed")
    try:
        mp.build_envelope("run_stone", video, video, video, changed)
        refused = False
    except ValueError:
        refused = True

    carousel = _carousel_probe(db, tmp, publisher, send)
    documentary = _documentary_probe(db, tmp, publisher, send)
    slot_lines = _slot_probe(db, tmp, coordinator, send)

    slot = post_schedule.fmt(post_schedule.next_reel_slot(now, set()))
    slot_line = next((ln for ln in card.splitlines() if "Slot:" in ln), "")
    result = {
        "posted_strings_shown_on_card": f"{sum(shown.values())}/{len(shown)}",
        "posted_strings": {name: {"shown": shown[name], "text": value} for name, value in fields.items()},
        "card_shows_illustration_disclosure": publisher.ILLUSTRATION_DISCLOSURE in card,
        "posted_caption_has_disclosure": publisher.ILLUSTRATION_DISCLOSURE in dict(reel)["instagram_title"],
        "card_shows_youtube_title": shown["short youtube_title"],
        "card_shows_youtube_description": shown["short youtube_description"],
        "slot_line": slot_line,
        "slot_line_equals_next_reel_slot": bool(slot_line) and html.escape(slot) in slot_line,
        "card_shows_review_close_time": "review closes" in card,
        "off_list_hashtag_posted": OFF_LIST in hashtags,
        "posted_hashtags": sorted(set(hashtags)),
        "build_envelope_raises_on_copy_not_on_card": refused,
        "card_characters": len(card),
        **carousel,
        **documentary,
        "slot_lines": slot_lines,
        "slot_lines_truthful": f"{sum(r['truthful'] for r in slot_lines.values())}/{len(slot_lines)}",
    }
    result["acceptance_met"] = bool(
        result["card_shows_illustration_disclosure"] and result["card_shows_youtube_title"]
        and result["card_shows_youtube_description"] and result["slot_line_equals_next_reel_slot"]
        and result["card_shows_review_close_time"] and all(shown.values()) and not result["off_list_hashtag_posted"]
        and refused and carousel["carousel_status"] == "published"
        and set(carousel["carousel_caption_shown_on_card"]) == {"instagram", "facebook"}
        and all(carousel["carousel_caption_shown_on_card"].values())
        and not carousel["carousel_off_list_hashtag_posted"] and carousel["carousel_refused_when_card_showed_another"]
        and documentary["documentary_approve_posts"] and all(r["truthful"] for r in slot_lines.values()))
    result = {
        **result,
        "real_runs": _real_runs(root),
    }
    out = json.dumps(result, indent=2, ensure_ascii=False)
    (HERE / "measurements.json").write_text(out + "\n", encoding="utf-8", newline="\n")
    sys.stdout.buffer.write((out + "\n").encode("utf-8"))


if __name__ == "__main__":
    main(sys.argv[1])
