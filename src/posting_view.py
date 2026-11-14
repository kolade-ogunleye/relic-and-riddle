"""
src/posting_view.py
===================
The Posting screens (the review of 2026-10-25): every booked or posted episode as a numbered list, each post on
its own line; one episode's posts in plain words, with confirmations before anything is posted or cancelled; and
🗓 Slots, the coming week's posting slots with what is booked in each and which are free, including the slot each
waiting carousel will actually take (only the newest eligible one posts in a carousel slot).
"""
import html
import json
import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import post_schedule
from .control import (HOME, LIST_PAGE, POST_ICON, POST_NAME, _episode_bookings, _post_links, _when, clip_words,
                      is_paused, number_buttons, pager)

STATUS = {"pending": "booked", "waiting": "waits for its slot", "published": "posted", "failed": "didn't post",
          "unknown": "not sure it posted: check the platform", "cancelled": "cancelled",
          "expired": "expired (it waited over 7 days)", "blocked": "held: it can't post as it is"}
SPACING = "sensitive-topic spacing"   # QF-73: why a sensitive topic's carousel waits, and why it expired
KIND_ICON = {"reel": "🎬", "story": "📱", "carousel": "🖼"}
# Why a booking was cancelled, in the operator's words (a bare "cancelled" gave no idea what had happened or
# whether anything was needed: operator, 2026-10-27, Göbekli Tepe's Stories)
CANCEL_WHY = {"the Reel did not post": "its Reel's slot failed, so it was not posted",
              "cancelled by the operator": "you cancelled it",
              "replaced by a new version": "you asked for a new version of this episode"}
# A blocked booking (QF-110): the release checks refused it at its slot, so trying again cannot change it.
BLOCKED_NEXT = ("Trying again won't change this. Make a new version (it comes to you for review first), or cancel "
                "the booking. Its slot is free for other episodes.")


def blocked_buttons(db, post: dict[str, Any]) -> list[list[dict[str, str]]]:
    """What can work on a blocked booking: a new version (it asks first), cancelling it (asks first), the details."""
    key = db.run_key(post["run_id"])
    return [[{"text": "♻️ Make a new version", "callback_data": f"newversionask:{key}"}],
            [{"text": "⏹ Cancel booking", "callback_data": f"cancelask:{post['id']}"},
             {"text": "📋 Details", "callback_data": f"cpost:{key}"}]]


def _short_thumbnail(pubs, shorts) -> str:
    """Whether the posted Short has its thumbnail: set by the try after posting (until QF-83) or by the button since;
    "frame" when none was ever asked for (QF-83: no request follows the upload; the Shorts feed takes a frame)."""
    tries = [x for x in pubs if x.get("channel") == "youtube:thumbnail"]
    if any(x.get("status") == "published" for x in tries):
        return "set"
    if not tries and not any('"thumbnail"' in (x.get("response_payload_json") or "") for x in shorts):
        return "frame"
    error = ""
    for x in shorts:  # the try made right after the Short posted
        try:
            first = (json.loads(x.get("response_payload_json") or "{}").get("thumbnail") or {})
        except ValueError:
            first = {}
        if first.get("set"):
            return "set"
        error = str(first.get("error") or error)
    if tries:  # the latest button tap says more than the first try
        try:
            error = str(json.loads(tries[-1].get("response_payload_json") or "{}").get("error") or error)
        except ValueError:
            pass
    return "not set" + (" (YouTube needs the channel verified: youtube.com/verify)" if "403" in error else "")


def status_words(db, p: Dict[str, Any], on_episode: bool = False) -> str:
    """A booking's state as the operator reads it; a cancelled one says why and what can be done."""
    words = STATUS.get(p["status"], p["status"])
    if p["status"] == "expired" and (p.get("detail") or {}).get("reason") == SPACING:   # QF-73: its true reason
        why = (p.get("detail") or {}).get("spacing_why")
        why = why if isinstance(why, dict) else None
        if (p.get("detail") or {}).get("by") == "operator":
            return "expired: you let it expire (" + (SPACING if (why or {}).get("kind", "spacing") == "spacing"
                                                     else "no carousel slot before its deadline") + ")"
        return "expired: " + why_words(why, past=True)
    if p["status"] != "cancelled":
        return words
    reason = str((p.get("detail") or {}).get("reason") or "")
    words += ": " + CANCEL_WHY.get(reason, reason or "no reason was recorded")
    live = [x for x in db.list_scheduled_posts(("pending", "waiting", "published", "unknown"), run_id=p["run_id"])
            if x["kind"] in ("story", "carousel")]
    # Booking again is for an episode whose Stories and carousel are all off (main.rebook_followers refuses otherwise)
    if p["kind"] in ("story", "carousel") and not live and (db.get_run(p["run_id"]) or {}).get("status") == "published":
        words += "; its Reel has posted since: " + ("tap 📱 Book Stories and carousel again below" if on_episode
                                                     else "open the episode to book it again")
    return words
SLOT_DAYS = 7
SLOT_GRACE = timedelta(hours=3)          # main.MISSED_SLOT_GRACE: how long after its time a slot may still post
PROMOTED_PREFIX = "promoted:"            # main.PROMOTED_PREFIX: a dry run promoted to real posting
POSTS_ONLY_PROMOTED_KEY = "posts_only_promoted"   # main.POSTS_ONLY_PROMOTED_KEY
PAUSED_LINE = "⏸ <b>Paused</b>: nothing posts until you resume (a Reel slot missed by over 3 h moves to the next free one)."


def _first_upper(text: str) -> str:
    return text[:1].upper() + text[1:]


def _dt(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    try:
        moment = datetime.fromisoformat(str(iso))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _clock(when: datetime) -> str:
    """'17:00 MDT (19:00 EDT)': the operator's time, the audience's when the zones differ."""
    from . import config
    from .local_time import local
    audience = when.astimezone(post_schedule.tz())
    mine = local(when)
    if config.TIMEZONE == config.POST_TIMEZONE:
        return audience.strftime("%H:%M %Z")
    return f"{mine.strftime('%H:%M %Z')} ({audience.strftime('%H:%M %Z')})"


def _skipped(db, run_id: str) -> Optional[str]:
    """Why the daemon skips the run's bookings (main._may_post), or None: a process that posts only promoted runs
    skips every run not promoted, dry or live (verification, 2026-10-25)."""
    if db.get_setting(PROMOTED_PREFIX + run_id):
        return None
    if db.get_setting("run_mode:" + run_id) == "dry_run":
        return "it's a dry run, so it posts only once you promote it (Post for real)"
    if db.get_setting(POSTS_ONLY_PROMOTED_KEY) == "true":
        return "this bot posts only runs you promoted (Post for real)"
    return None


def wont_post(db, run_id: str, outlook: dict[str, Any] | None = None) -> Optional[str]:
    """Why a booked post will not go out though its countdown runs, or None: the daemon skips the run (_skipped),
    or (QF-73) the carousel plan gives its waiting carousel no slot before its deadline, for sensitive-topic spacing
    or because a carousel you chose to post anyway takes its slot (carousel_outlook: one answer for every screen,
    the Needs-you alert and its expiry)."""
    skipped = _skipped(db, run_id)
    if skipped:
        return skipped
    for post in db.list_scheduled_posts(("waiting",), run_id=run_id):
        wait = spacing_wait(db, post, outlook=outlook)
        if not wait or wait["state"] != "posted" or wait["earliest"] or wait["unconfirmed"]:
            continue   # it posts at its planned slot, or nothing is known until the other publication is
        then ="post it anyway, or it expires then" if wait["next"] else "it expires then"
        if wait["kind"] == "spacing":
            return (f"its carousel is waiting for {SPACING} and no spaced slot comes before its deadline, "
                    f"{post_schedule.fmt(wait['deadline'])}" + (f" ({why_words(wait['why'])})" if wait["lost"] else "")
                    + f": {then}")
        return (f"its carousel has no carousel slot before its deadline, {post_schedule.fmt(wait['deadline'])}: "
                f"{why_words(wait['why'])}; {then}")
    return None


def post_error(detail: Dict[str, Any]) -> str:
    """What went wrong, in words: "Instagram didn't post", not "partial: failed=['instagram']"."""
    failed = list((detail.get("failed") or {}).keys())
    if not failed:
        m = re.search(r"failed=\[([^\]]*)\]", str(detail.get("error") or ""))
        failed = [p.strip(" '\"") for p in m.group(1).split(",") if p.strip(" '\"")] if m else []
    if failed:
        names = ", ".join(p.capitalize().replace("Youtube", "YouTube") for p in failed)
        return f"{names} didn't post; the others did"
    return clip_words(str(detail.get("error") or detail.get("reason") or ""), 300)


def _spacing_reason(db, post: dict[str, Any], slot: datetime) -> str:
    """Why sensitive-topic spacing keeps this carousel from `slot` (db.topic_problem), "" when it does not."""
    topic_id = (db.get_run(post["run_id"]) or {}).get("topic_id")
    return db.topic_problem(topic_id, slot, run_id=post["run_id"], spacing_only=True) if topic_id else ""


def _rank(post: dict[str, Any], overrides: Any = ()) -> tuple:
    """The daemon's order at a carousel slot (main._post_carousel_if_due): a carousel given priority (QF-81's redraw,
    QF-73's Post anyway) first, then the newest."""
    return (bool((post.get("detail") or {}).get("priority")) or post["id"] in overrides, post["id"])


def carousel_outlook(db, now: datetime | None = None, overrides: Any = ()) -> dict[str, Any]:
    """QF-73: the one answer to where each waiting carousel will post, as the daemon picks them, used by Posting, the
    episode, the dashboard, Slots, wont_post, the Needs-you alert and the expiry record alike.

    At each carousel slot (counted until its posting window closes, main.MISSED_SLOT_GRACE, unless a carousel already
    posted that day) the eligible carousel of the highest _rank takes it. A carousel is eligible from 48 h after its
    Reel to its deadline (post_schedule.carousel_may_take; while its Reel hasn't posted, its slot is a forecast) unless
    sensitive-topic spacing refuses it there; a sensitive carousel the plan puts in a slot counts on that day, as it
    will once posted. Post anyway (detail "spacing_override", or `overrides` to ask "what if") sets spacing aside.

    Returns {"plan": {booking id: slot}, "over": the ids Post anyway was chosen for, "now", "waits": {booking id:
    wait}}. A wait is given for a carousel spacing refused at a slot (in the plan, or earlier at its slot: its
    booking's "waiting_for"), one you chose to post anyway that gets no slot, and one a carousel you chose to post
    anyway displaced (it would otherwise have taken that slot): {"kind": "spacing" | "slot", "reason": spacing's
    words, "why": what keeps it from a slot (why_words), "lost": a slot it lost is named, "earliest": its planned
    slot or None (none before its deadline), "deadline", "state": its Reel's, "unconfirmed": spacing waits for an
    unconfirmed publication, "override": Post anyway was chosen}."""
    from . import config
    from .topic_policy import TOO_CLOSE, UNCONFIRMED, _day
    now = now or datetime.now(UTC)
    overrides = set(overrides)
    waiting = [p for p in db.list_scheduled_posts(("waiting",)) if not _skipped(db, p["run_id"])]  # as the daemon skips
    reels = {p["id"]: post_schedule.carousel_reel(db, p) for p in waiting}
    waiting = [p for p in waiting if reels[p["id"]][0] in ("posted", "booked")]   # a Reel that won't post: no slot yet
    over = {p["id"] for p in waiting if (p.get("detail") or {}).get("spacing_override")} | overrides
    out: dict[str, Any] = {"plan": {}, "waits": {}, "over": over, "now": now}
    if not waiting:
        return out
    topics = {p["id"]: (db.get_run(p["run_id"]) or {}).get("topic_id") for p in waiting}
    sensitive = {t: bool((db.get_topic_by_id(t) or {}).get("sensitivity")) for t in set(topics.values()) if t}
    deadlines = {p["id"]: post_schedule.carousel_deadline(reels[p["id"]][1]) for p in waiting}
    seen: dict[int, dict[str, Any]] = {p["id"]: {"refused": "", "lost": None, "displaced": None} for p in waiting}
    plan: dict[int, datetime] = out["plan"]
    shown: list[tuple[Any, datetime]] = []   # (topic, slot) of each sensitive carousel the plan posts
    taken = db.get_setting("carousel_posted_on")
    day = min(max(now - SLOT_GRACE, reels[p["id"]][1] + post_schedule.carousel_gap()) for p in waiting
              ).astimezone(post_schedule.tz()).date()
    last = max(deadlines.values()).astimezone(post_schedule.tz()).date()
    while day <= last:
        slot = post_schedule.carousel_time(day)
        day += timedelta(days=1)
        if not slot or slot + SLOT_GRACE <= now or taken == slot.date().isoformat():
            continue
        eligible = []
        for p in waiting:
            if p["id"] in plan or not post_schedule.carousel_may_take(slot, reels[p["id"]][1]):
                continue
            reason = "" if p["id"] in over else _spacing_reason(db, p, slot)
            topic = topics[p["id"]]
            if not reason and p["id"] not in over and sensitive.get(topic) and any(
                    t != topic and abs((_day(slot) - _day(s)).days) <= config.SENSITIVE_TOPIC_GAP_DAYS for t, s in shown):
                reason = TOO_CLOSE
            if reason:
                seen[p["id"]]["refused"] = UNCONFIRMED if reason == UNCONFIRMED else seen[p["id"]]["refused"] or reason
            else:
                eligible.append(p)
        if not eligible:
            continue
        win = max(eligible, key=lambda p: _rank(p, overrides))
        plan[win["id"]] = slot
        if sensitive.get(topics[win["id"]]):
            shown.append((topics[win["id"]], slot))
        rest = [p for p in eligible if p is not win]
        for p in rest:
            seen[p["id"]]["lost"] = seen[p["id"]]["lost"] or (slot, win)
        plain = [p for p in rest if p["id"] not in over]
        if win["id"] in over and plain:   # the one that would have taken it, but for Post anyway
            runner = max(plain, key=lambda p: _rank(p, overrides))
            seen[runner["id"]]["displaced"] = seen[runner["id"]]["displaced"] or (slot, win)
    for p in waiting:
        pid, s, detail = p["id"], seen[p["id"]], p.get("detail") or {}
        # spacing held it back: at a slot of the plan, or (no slot left to plan) at an earlier slot, its booking says
        spaced = pid not in over and bool(s["refused"] or (pid not in plan and detail.get("waiting_for") == "spacing"))
        if pid in plan and not spaced:
            continue
        if pid not in plan and not (spaced or pid in over or s["displaced"]):
            continue   # it only loses to newer carousels, as every carousel may (expired after 7 days, as before)
        kind = "spacing" if spaced else "slot"
        lost = s["displaced"] or s["lost"]
        why: dict[str, Any] = {"kind": kind, "chosen": pid in over}
        if pid not in plan and lost:
            why.update(slot=lost[0].isoformat(), to=db.run_label(lost[1]["run_id"]), to_chosen=lost[1]["id"] in over,
                       newer=lost[1]["id"] > pid)
        elif pid not in plan and not s["refused"] and isinstance(detail.get("spacing_why"), dict):
            why = detail["spacing_why"]   # no slot is left to plan: the record its booking keeps says why
        refused = s["refused"] if s["refused"] != UNCONFIRMED else ""
        out["waits"][pid] = {"kind": kind, "reason": refused or str(detail.get("spacing") or s["refused"]),
                             "why": why, "lost": bool(why.get("slot")), "earliest": plan.get(pid),
                             "deadline": deadlines[pid], "state": reels[pid][0],
                             "unconfirmed": s["refused"] == UNCONFIRMED, "override": pid in over}
    return out


def carousel_plan(db, now: Optional[datetime] = None, days: int = SLOT_DAYS + 7) -> Dict[int, datetime]:
    """The carousel slot each waiting carousel will take, as the daemon picks them (carousel_outlook: one per
    carousel slot, priority first, then the newest eligible; post_schedule.carousel_may_take, from 48 h to 7 days
    after its Reel posts or, while it hasn't, after its Reel's slot; sensitive-topic spacing unless you chose Post
    anyway). A carousel with no slot before it expires is left out; so is a slot more than `days` days ahead."""
    now = now or datetime.now(timezone.utc)
    end = now.astimezone(post_schedule.tz()).date() + timedelta(days=days)
    return {pid: slot for pid, slot in carousel_outlook(db, now)["plan"].items() if slot.date() < end}


def post_anyway_outcome(db, post: dict[str, Any], now: datetime | None = None,
                        outlook: dict[str, Any] | None = None) -> dict[str, Any]:
    """What Post anyway would secure for this carousel, from the plan itself: {"slot": the slot the plan then gives
    it, or None; "blocked_by": the booking id of a carousel you already chose to post anyway that would lose its
    only slot to it (Post anyway then changes nothing: the first choice keeps its slot); "displaced": the booking
    ids it would leave with no slot before their deadline}."""
    base = outlook or carousel_outlook(db, now)
    then = carousel_outlook(db, base["now"], overrides={post["id"]})
    lose = [pid for pid in base["plan"] if pid != post["id"] and pid not in then["plan"]]
    blocked = [pid for pid in lose if pid in base["over"]]
    return {"slot": None if blocked else then["plan"].get(post["id"]), "blocked_by": blocked[0] if blocked else None,
            "displaced": [] if blocked else lose}


def spacing_wait(db, post: dict[str, Any], now: datetime | None = None,
                 outlook: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """QF-73: how the carousel plan (carousel_outlook) holds a waiting carousel back, or None (it posts at its planned
    slot as any other). The outlook's wait, with "next": the slot Post anyway would give it (post_anyway_outcome;
    None when Post anyway cannot give it one, or was already chosen)."""
    if post.get("kind") != "carousel" or post.get("status") != "waiting":
        return None
    out = outlook or carousel_outlook(db, now)
    wait = out["waits"].get(post["id"])
    if wait is None:
        return None
    nxt = None if wait["earliest"] or wait["override"] else post_anyway_outcome(db, post, outlook=out)["slot"]
    return {**wait, "next": wait["earliest"] or nxt}


def why_words(why: dict[str, Any] | None, past: bool = False) -> str:
    """What keeps a carousel from a slot, in words (carousel_outlook's "why", kept on its booking as "spacing_why")."""
    why = why or {"kind": "spacing"}
    if not why.get("slot"):
        if why.get("kind", "spacing") == "spacing":
            return f"{SPACING} left no carousel slot before its deadline"
        return "no carousel slot " + ("was" if past else "is") + " left before its deadline"
    took = "took" if past else "takes"
    slot = post_schedule.fmt(datetime.fromisoformat(why["slot"]))
    other = str(why.get("to") or "another episode")
    if why.get("to_chosen"):
        who = f"the carousel of {other}, which you " + ("also " if why.get("chosen") else "") + "chose to post anyway,"
    else:
        who = f"a newer carousel ({other})" if why.get("newer") else f"the carousel of {other}, which goes first,"
    if why.get("kind", "spacing") == "spacing":
        return f"{SPACING} held it back, then {who} {took} its spaced slot, {slot}"
    return f"{who} {took} its " + ("only " if why.get("chosen") else "") + f"carousel slot, {slot}"


def spacing_words(wait: dict[str, Any], slot: datetime | None = None) -> str:
    """The wait in words: the slot it will take (the plan's, as the daemon picks), or that none is left."""
    slot = slot or wait.get("earliest")
    if slot:
        return f"waiting: {SPACING}, earliest {post_schedule.fmt(slot)}"
    if wait.get("unconfirmed"):
        return f"waiting: {SPACING}, until another sensitive topic's publication is confirmed"
    if wait.get("kind", "spacing") == "spacing":
        return f"waiting: {SPACING}, no spaced slot before its deadline, {post_schedule.fmt(wait['deadline'])}"
    return f"waiting: no carousel slot before its deadline, {post_schedule.fmt(wait['deadline'])}"


def spacing_buttons(post: dict[str, Any], wait: dict[str, Any] | None = None) -> list[list[dict[str, str]]]:
    """What you can do about a carousel the plan leaves no slot for: post it anyway at the slot the plan then gives
    it (only while there is one: post_anyway_outcome), or let it expire, as it does if you do nothing."""
    row = [{"text": "⏹ Let it expire", "callback_data": f"carexpire:{post['id']}"}]
    if wait is None or wait.get("next"):
        row.insert(0, {"text": "▶️ Post anyway", "callback_data": f"carspace:{post['id']}"})
    return [row]


def _post_line(db, p: Dict[str, Any], plan: Dict[int, datetime], outlook: dict[str, Any] | None = None) -> str:
    name, icon = POST_NAME.get(p["kind"], p["kind"]), KIND_ICON.get(p["kind"], "•")
    if p["status"] == "pending":
        return f"{icon} {name} {POST_ICON['pending']} {html.escape(_when(p.get('due_at')))}"
    if p["status"] == "waiting":
        slot = plan.get(p["id"])
        wait = spacing_wait(db, p, outlook=outlook)
        if wait:   # QF-73: the wait and its reason, never a slot it will not take
            return f"{icon} {name} {POST_ICON['waiting']} <i>{html.escape(spacing_words(wait, slot))}</i>"
        return f"{icon} {name} {POST_ICON['waiting']} " + (
            html.escape(_when(slot.isoformat())) if slot else "<i>after its Reel, in a carousel slot</i>")
    return f"{icon} {name} {POST_ICON.get(p['status'], '•')} {html.escape(status_words(db, p))}"


def posting_panel(db, page: int = 0) -> Tuple[str, List[List[Dict[str, str]]]]:
    """🗓 Posting: a numbered list of the episodes, each post on its own line; a number opens one."""
    from .control import next_slot_line
    episodes = _episode_bookings(db)
    pages = max(1, (len(episodes) + LIST_PAGE - 1) // LIST_PAGE)
    page = max(0, min(page, pages - 1))
    shown = episodes[page * LIST_PAGE:(page + 1) * LIST_PAGE]
    booked = db.list_scheduled_posts(("pending", "waiting"))
    lines = ["🗓 <b>Posting</b>" + (f" · page {page + 1} of {pages}" if pages > 1 else ""),
             html.escape(_first_upper(next_slot_line(db, booked)))]
    if is_paused(db):
        lines.append(PAUSED_LINE)
    outlook = carousel_outlook(db)   # QF-73: one plan for every line and its warnings
    plan = outlook["plan"]
    if not episodes:
        lines += ["", "Nothing booked or posted yet. An approved episode is booked into the next free slot; "
                      "🗓 Slots shows the week."]
    for n, (run_id, posts) in enumerate(shown, page * LIST_PAGE + 1):
        lines += ["", f"{n}. <b>{html.escape(db.run_label(run_id))}</b>"]
        lines += ["   " + _post_line(db, p, plan, outlook) for p in posts]
        why = wont_post(db, run_id, outlook) if any(p["status"] in ("pending", "waiting") for p in posts) else None
        if why:
            lines.append(f"   ⚠️ <i>Won't post: {html.escape(why)}</i>")
    buttons = number_buttons([f"cpost:{db.run_key(r)}" for r, _ in shown], page * LIST_PAGE + 1)
    return "\n".join(lines), buttons + pager("cposts:", page, pages) + [[{"text": "🗓 Slots", "callback_data": "cslots:"}]]


def posting_episode(db, run_id: str) -> Tuple[str, List[List[Dict[str, str]]]]:
    """One episode's posts in plain words: booked, slot (with how far away), posted, its own links, what went
    wrong, and the actions on it; posting now and cancelling each ask first."""
    e = html.escape
    posts = dict(_episode_bookings(db)).get(run_id, [])
    outlook = carousel_outlook(db)   # QF-73: one plan for the slot, the wait, its warning and its buttons
    plan = outlook["plan"]
    lines = [f"🗓 <b>{e(db.run_label(run_id))}</b>"]
    if is_paused(db):
        lines.append(PAUSED_LINE)
    why = wont_post(db, run_id, outlook) if any(p["status"] in ("pending", "waiting") for p in posts) else None
    if why:
        lines.append(f"⚠️ <i>Won't post: {e(why)}</i>")
    buttons: List[List[Dict[str, str]]] = []
    if not posts:
        lines += ["", "Nothing is booked for this episode."]
    key = db.run_key(run_id)
    shorts = [x for x in db.list_publications(run_id) if x.get("channel") == "youtube-shorts"]
    for p in posts:
        name, detail = POST_NAME.get(p["kind"], p["kind"]), p.get("detail") or {}
        if p["kind"] == "reel" and any(x.get("status") in ("published", "simulated") for x in shorts):
            name = "Reel & Short"  # its YouTube Short goes with it, and its link is under it (operator, 2026-10-27)
        lines += ["", f"{KIND_ICON.get(p['kind'], '•')} <b>{e(name)}</b> · {e(status_words(db, p, on_episode=True))}",
                  f"  Booked: {e(_when(p.get('created_at'), ahead=False))}"]
        wait = spacing_wait(db, p, outlook=outlook) if p["status"] == "waiting" else None
        if wait:   # QF-73: why it waits, until when, and (no slot left) what you can do, all from the one plan
            lines.append("  " + e(_first_upper(spacing_words(wait, wait["earliest"]))))
            if wait["kind"] == "spacing":
                why = (wait["reason"] or "Too close to another sensitive topic") + (
                    "; " + why_words(wait["why"]) if wait["lost"] else "")
            else:
                why = _first_upper(why_words(wait["why"]))
            lines.append(f"  <i>{e(why)}. Its deadline: {e(post_schedule.fmt(wait['deadline']))}.</i>")
            if wait["state"] == "posted" and not wait["earliest"]:
                lines.append(f"  <i>Post anyway posts it at {e(post_schedule.fmt(wait['next']))}; if you do nothing, "
                             "it expires at its deadline.</i>" if wait["next"] else "  <i>No carousel slot is left for "
                             "it before its deadline, so it expires then.</i>")
                buttons += spacing_buttons(p, wait)
        elif p["status"] == "waiting":
            slot = plan.get(p["id"])
            lines.append("  Slot: " + (e(_when(slot.isoformat())) if slot else
                                       "the first carousel slot 48 h after its Reel posts (the newest waiting carousel goes first)"))
        elif p.get("due_at"):
            lines.append(f"  Slot: {e(_when(p['due_at']))}")
        if p["status"] == "published":
            lines.append(f"  Published: {e(_when(detail.get('posted_at') or p.get('updated_at')))}")
            links = _post_links(db, run_id, p["kind"])
            if links:
                lines.append(f"  {links}")
        elif p["status"] != "blocked" and detail.get("reason") != SPACING:   # a blocked one lists its reasons below;
            # a carousel expired for spacing says so in its status
            note = post_error(detail)
            if note:  # only while it matters: a published post no longer shows its earlier error
                lines.append(f"  <i>{e(note)}</i>")
        if p["kind"] == "reel" and p["status"] == "blocked":
            reasons = detail.get("reasons") or [detail.get("reason") or "no reason was recorded"]
            lines += [f"  • {e(str(r))}" for r in reasons]
            lines.append(f"  <i>{e(BLOCKED_NEXT)}</i>")
            new_version, (cancel, _details) = blocked_buttons(db, p)   # Details is this screen
            buttons += [new_version, [cancel]]
        if p["kind"] == "reel" and p["status"] == "pending":
            buttons.append([{"text": "⚡ Post now", "callback_data": f"postnowask:{key}"}])
            buttons.append([{"text": "⏭ Next free slot", "callback_data": f"movepost:{p['id']}"},
                            {"text": "⏹ Cancel booking", "callback_data": f"cancelask:{p['id']}"}])
        if p["kind"] in ("story", "carousel") and p["status"] in ("unknown", "failed") and (
                detail.get("failed") or "failed=[" in str(detail.get("error") or "")):
            buttons.append([{"text": f"🔁 Retry {name}", "callback_data": f"retrypost:{p['id']}"}])
        if p["kind"] == "reel" and p["status"] in ("unknown", "failed"):
            buttons.append([{"text": "🔁 Retry Reel", "callback_data": f"retryreel:{key}"}])
        if p["kind"] == "reel" and p["status"] == "published":
            pubs = db.list_publications(run_id)
            ok = lambda channel: any(x.get("channel") == channel and x.get("status") == "published" for x in pubs)  # noqa: E731
            row = []
            if "facebook" in _post_links(db, run_id, "reel"):
                lines.append("  Facebook captions: " + ("added" if ok("facebook:captions") else "not yet"))
                lines.append("  Facebook cover: " + ("set" if ok("facebook:cover") else "not set"))
                row.append({"text": "💬 Captions" + (" again" if ok("facebook:captions") else ""), "callback_data": f"fbcaptions:{key}"})
                row.append({"text": "🖼 Cover" + (" again" if ok("facebook:cover") else ""), "callback_data": f"fbcover:{key}"})
            shorts = [x for x in pubs if x.get("channel") == "youtube-shorts"]
            if not any(x.get("status") in ("published", "unknown", "simulated") for x in shorts):
                # posted without its Short (a Reel posted by Retry, or a Short that failed)
                lines.append("  YouTube Short: not posted" + (" (it failed)" if shorts else ""))
                row.append({"text": "▶️ YouTube Short", "callback_data": f"ytshort:{key}"})
            elif any(x.get("status") == "published" for x in shorts):
                thumb = _short_thumbnail(pubs, shorts)
                # Two images: the API sets the standard thumbnail; the Shorts tab and shelf use a frame of the video
                # that only the YouTube app can change (operator, 2026-10-28: "set" while the Shorts tab showed frames)
                frame = "a frame of the video, changed only in the YouTube app: the Short, ✏️ Edit, Thumbnail"
                lines.append("  YouTube thumbnail: " + ("standard thumbnail set (search, watch page); the Shorts tab shows " + frame
                                                        if thumb == "set" else frame if thumb == "frame" else thumb))
                if thumb != "set":
                    row.append({"text": "▶️ YouTube thumbnail", "callback_data": f"ytthumb:{key}"})
            if row:
                buttons.append(row)
            followers = [x for x in posts if x["kind"] in ("story", "carousel")]
            spaced = any(x["status"] == "expired" and (x.get("detail") or {}).get("reason") == SPACING
                         for x in followers)   # QF-73: its own line gives the reason; nothing was cancelled
            if not spaced and not any(x["status"] in ("pending", "waiting", "published", "unknown") for x in followers):
                # the Reel posted after all (a retry), its Stories and carousel cancelled with the failed slot
                lines.append("  Stories and carousel: " + ("cancelled with the failed slot" if followers else "not booked"))
                buttons.append([{"text": "📱 Book Stories and carousel again", "callback_data": f"rebook:{key}"}])
    return "\n".join(lines), buttons + [[{"text": "← Posting", "callback_data": "cposts:"}]]


def _post_now_followers(db, run_id: str, run: dict[str, Any]) -> set:
    """The Stories and carousel Post now will really post: those booked for the run, or (a run not booked yet,
    whose Post now books them after the Reel: main.promote_run) those on disk. Only Upload-Post posts them, so
    none without it (it promised them whatever was booked or set up: review 1 of UI-02)."""
    from .publisher import UploadPostPublisher
    if not UploadPostPublisher().configured():
        return set()
    if run.get("status") == "scheduled":
        return {p["kind"] for p in db.list_scheduled_posts(("pending", "waiting"), run_id=run_id)} & {"story", "carousel"}
    from .release import story_frames
    run_dir = Path(run.get("master_video_path") or "").parent if run.get("master_video_path") else None
    if run_dir is None or not run_dir.is_dir():
        return set()
    from .carousel_lazy import planned
    # A planned carousel (QF-81) has no slides on disk until its slot draws it, yet promote_run books it
    return ({"story"} if story_frames(run_dir) else set()) | (
        {"carousel"} if planned(run_dir) or any((run_dir / "carousel").glob("carousel_slide_*.jpg")) else set())


def confirm_post_now(db, run_id: str) -> Tuple[str, List[List[Dict[str, str]]]]:
    from . import config
    from .publisher import attempted_platforms
    label = html.escape(db.run_label(run_id))
    # Only what will really be attempted, from YOUTUBE_SHORTS and the accounts set up (it promised YouTube
    # whatever the Shorts setting: UI-02)
    run = db.get_run(run_id) or {}
    documentary = (run.get("content_format") == "deep_dive"
                   or (run.get("script_payload") or {}).get("delivery_profile") == "deep_dive_16x9")
    targets = attempted_platforms(documentary=documentary)
    no = [{"text": "✖ Not now", "callback_data": f"cpost:{db.run_key(run_id)}"}]
    if not targets:
        return (f"⚡ <b>Post now?</b>\n{label}\n\nNo posting account is set up, so nothing can be posted now. "
                "Set one up first.", [no])
    if documentary:
        what = "The documentary goes out now on YouTube."
    else:
        names = [{"facebook": "Facebook", "instagram": "Instagram"}[t] for t in targets if t != "youtube-short"]
        follows = _post_now_followers(db, run_id, run)
        then = [f"its Stories follow {config.STORY_DELAY_MINUTES} min later" if "story" in follows else "",
                "the carousel in its slot" if "carousel" in follows else ""]
        then = [x for x in then if x]
        what = (f"The Reel goes out now on {' and '.join(names)}"
                + (", then as a YouTube Short" if "youtube-short" in targets else "")
                + (f"; {' and '.join(then)}." if then else "."))
    return (f"⚡ <b>Post now?</b>\n{label}\n\n{what} This can't be undone.",
            [[{"text": "✅ Yes, post it now", "callback_data": f"postnow:{db.run_key(run_id)}"}], no])


def confirm_new_version(db, run_id: str) -> tuple[str, list[list[dict[str, str]]]]:
    """♻️ Make a new version of an episode whose booking was blocked: it asks first (QF-110)."""
    label = html.escape(db.run_label(run_id))
    text = (f"♻️ <b>Make a new version?</b>\n{label}\n\nIts bookings are cancelled and a new version is made from "
            "this one, reusing what it can; it comes to you for review, and is booked again only once you approve it.")
    return (text,
            [[{"text": "✅ Yes, make it", "callback_data": f"newversion:{db.run_key(run_id)}"}],
             [{"text": "✖ Not now", "callback_data": f"cpost:{db.run_key(run_id)}"}]])


def confirm_cancel(db, post_id: int) -> Tuple[str, List[List[Dict[str, str]]]]:
    post = next((p for p in db.list_scheduled_posts(("pending", "waiting", "blocked")) if p["id"] == post_id), None)
    if post is None:
        return "⏹ That booking is no longer pending.", [[{"text": "← Posting", "callback_data": "cposts:"}]]
    label = html.escape(db.run_label(post["run_id"]))
    what = ("its Reel, Stories and carousel bookings are all cancelled, its topic goes back to the queue, and "
            "for two days it can still be posted from Reviews → Post for real"
            if post["kind"] == "reel" else f"only this {POST_NAME.get(post['kind'], post['kind'])} booking is cancelled")
    return (f"⏹ <b>Cancel this booking?</b>\n{label}\n\n{_first_upper(what)}.",
            [[{"text": "✅ Yes, cancel it", "callback_data": f"cancelpost:{post_id}"}],
             [{"text": "✖ Keep it", "callback_data": f"cpost:{db.run_key(post['run_id'])}"}]])


def slots_screen(db, week: int = 0, now: Optional[datetime] = None) -> Tuple[str, List[List[Dict[str, str]]]]:
    """🗓 Slots: the coming week, day by day: each Reel, Stories and carousel slot with what is booked in it,
    or free; a waiting carousel in the slot it will really take."""
    now = now or datetime.now(timezone.utc)
    week = max(0, min(int(week or 0), 3))
    start = now.astimezone(post_schedule.tz()).date() + timedelta(days=7 * week)
    reels: Dict[Any, Dict[str, Any]] = {}
    for p in db.list_scheduled_posts(("pending", "published", "unknown", "failed")):
        if p["kind"] == "reel" and p.get("due_at"):
            reels.setdefault(_dt(p["due_at"]).astimezone(post_schedule.tz()).date(), p)
    plan = {slot.date(): pid for pid, slot in carousel_plan(db, now, days=7 * (week + 1) + 7).items()}
    waiting = {p["id"]: p for p in db.list_scheduled_posts(("waiting",))}
    lines = ["🗓 <b>Slots</b> · " + ("the next 7 days" if week == 0 else f"week of {start.strftime('%a %d %b')}"),
             "<i>Your time, the audience's (US Eastern) in brackets.</i>"]
    if is_paused(db):
        lines.append(PAUSED_LINE)
    free = 0
    for i in range(7):
        day = start + timedelta(days=i)
        reel_at = post_schedule.reel_time(day)
        lines += ["", f"<b>{day.strftime('%a %d %b')}</b>"]
        booked = reels.get(day)
        if reel_at < now and not booked:
            lines.append(f"  🎬 Reel {_clock(reel_at)} · <i>passed</i>")
        elif booked:
            state = "" if booked["status"] == "pending" else f" · {STATUS.get(booked['status'], booked['status'])}"
            lines.append(f"  🎬 Reel {_clock(_dt(booked['due_at']))} · {html.escape(db.run_label(booked['run_id']))}{html.escape(state)}")
            lines.append(f"  📱 Stories {_clock(post_schedule.story_time(_dt(booked['due_at'])))} · its Stories")
        else:
            free += 1
            lines.append(f"  🎬 Reel {_clock(reel_at)} · <b>free</b>")
        car = post_schedule.carousel_time(day)
        if car:
            pid = plan.get(car.date())
            taken = waiting.get(pid) if pid else None
            if car < now:
                lines.append(f"  🖼 Carousel {_clock(car)} · <i>passed</i>")
            else:
                lines.append(f"  🖼 Carousel {_clock(car)} · " + (html.escape(db.run_label(taken["run_id"])) if taken else "<b>free</b>"))
    lines.insert(2, f"{free} free Reel slot(s) this week.")
    nav = []
    if week > 0:
        nav.append({"text": "◀ Earlier", "callback_data": f"cslots:{week - 1}"})
    if week < 3:
        nav.append({"text": "Later ▶", "callback_data": f"cslots:{week + 1}"})
    return "\n".join(lines), ([nav] if nav else []) + [[{"text": "← Posting", "callback_data": "cposts:"}]]


def episode_by_number(db, n: str) -> Optional[str]:
    """The run of episode `n` as numbered on Posting."""
    try:
        index = int(str(n).strip()) - 1
    except ValueError:
        return None
    episodes = _episode_bookings(db)
    return episodes[index][0] if 0 <= index < len(episodes) else None
