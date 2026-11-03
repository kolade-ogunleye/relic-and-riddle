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
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from . import post_schedule
from .control import (HOME, LIST_PAGE, POST_ICON, POST_NAME, _episode_bookings, _post_links, _when, clip_words,
                      is_paused, number_buttons, pager)

STATUS = {"pending": "booked", "waiting": "waits for its slot", "published": "posted", "failed": "didn't post",
          "unknown": "not sure it posted: check the platform", "cancelled": "cancelled",
          "expired": "expired (it waited over 7 days)"}
KIND_ICON = {"reel": "🎬", "story": "📱", "carousel": "🖼"}
# Why a booking was cancelled, in the operator's words (a bare "cancelled" gave no idea what had happened or
# whether anything was needed: operator, 2026-10-27, Göbekli Tepe's Stories)
CANCEL_WHY = {"the Reel did not post": "its Reel's slot failed, so it was not posted",
              "cancelled by the operator": "you cancelled it"}


def _short_thumbnail(pubs, shorts) -> str:
    """Whether the posted Short has its thumbnail: set by the try after posting or by the button since."""
    tries = [x for x in pubs if x.get("channel") == "youtube:thumbnail"]
    if any(x.get("status") == "published" for x in tries):
        return "set"
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


def wont_post(db, run_id: str) -> Optional[str]:
    """Why a booked post will not go out though its countdown runs, or None: as main._may_post decides, a
    process that posts only promoted runs skips every run not promoted, dry or live (verification, 2026-10-25)."""
    if db.get_setting(PROMOTED_PREFIX + run_id):
        return None
    if db.get_setting("run_mode:" + run_id) == "dry_run":
        return "it's a dry run, so it posts only once you promote it (Post for real)"
    if db.get_setting(POSTS_ONLY_PROMOTED_KEY) == "true":
        return "this bot posts only runs you promoted (Post for real)"
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


def carousel_plan(db, now: Optional[datetime] = None, days: int = SLOT_DAYS + 7) -> Dict[int, datetime]:
    """The carousel slot each waiting carousel will take, as the daemon picks them (one per carousel slot, the
    newest eligible: post_schedule.carousel_may_take, from 48 h to 7 days after its Reel posts or, while it
    hasn't, after its Reel's slot). A carousel with no slot before it expires is left out."""
    now = now or datetime.now(timezone.utc)
    waiting = [p for p in db.list_scheduled_posts(("waiting",)) if not wont_post(db, p["run_id"])]   # as the daemon skips them
    reels = {p["id"]: post_schedule.carousel_reel(db, p) for p in waiting}
    waiting = [p for p in waiting if reels[p["id"]][0] in ("posted", "booked")]   # a Reel that won't post: no slot yet
    plan: Dict[int, datetime] = {}
    day = now.astimezone(post_schedule.tz()).date()
    for _ in range(days):
        slot = post_schedule.carousel_time(day)
        day += timedelta(days=1)
        if not slot or slot < now:
            continue
        eligible = [p for p in waiting if p["id"] not in plan and post_schedule.carousel_may_take(slot, reels[p["id"]][1])]
        if eligible:
            plan[max(eligible, key=lambda p: p["id"])["id"]] = slot
    return plan


def _post_line(db, p: Dict[str, Any], plan: Dict[int, datetime]) -> str:
    name, icon = POST_NAME.get(p["kind"], p["kind"]), KIND_ICON.get(p["kind"], "•")
    if p["status"] == "pending":
        return f"{icon} {name} {POST_ICON['pending']} {html.escape(_when(p.get('due_at')))}"
    if p["status"] == "waiting":
        slot = plan.get(p["id"])
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
    plan = carousel_plan(db)
    if not episodes:
        lines += ["", "Nothing booked or posted yet. An approved episode is booked into the next free slot; "
                      "🗓 Slots shows the week."]
    for n, (run_id, posts) in enumerate(shown, page * LIST_PAGE + 1):
        lines += ["", f"{n}. <b>{html.escape(db.run_label(run_id))}</b>"]
        lines += ["   " + _post_line(db, p, plan) for p in posts]
        why = wont_post(db, run_id) if any(p["status"] in ("pending", "waiting") for p in posts) else None
        if why:
            lines.append(f"   ⚠️ <i>Won't post: {html.escape(why)}</i>")
    buttons = number_buttons([f"cpost:{db.run_key(r)}" for r, _ in shown], page * LIST_PAGE + 1)
    return "\n".join(lines), buttons + pager("cposts:", page, pages) + [[{"text": "🗓 Slots", "callback_data": "cslots:"}]]


def posting_episode(db, run_id: str) -> Tuple[str, List[List[Dict[str, str]]]]:
    """One episode's posts in plain words: booked, slot (with how far away), posted, its own links, what went
    wrong, and the actions on it; posting now and cancelling each ask first."""
    e = html.escape
    posts = dict(_episode_bookings(db)).get(run_id, [])
    plan = carousel_plan(db)
    lines = [f"🗓 <b>{e(db.run_label(run_id))}</b>"]
    if is_paused(db):
        lines.append(PAUSED_LINE)
    why = wont_post(db, run_id) if any(p["status"] in ("pending", "waiting") for p in posts) else None
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
        if p["status"] == "waiting":
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
        else:
            note = post_error(detail)
            if note:  # only while it matters: a published post no longer shows its earlier error
                lines.append(f"  <i>{e(note)}</i>")
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
                lines.append("  YouTube thumbnail: " + ("standard thumbnail set (search, watch page); the Shorts tab shows a frame of the video, changed only in the YouTube app: the Short, ✏️ Edit, Thumbnail" if thumb == "set" else thumb))
                if thumb != "set":
                    row.append({"text": "▶️ YouTube thumbnail", "callback_data": f"ytthumb:{key}"})
            if row:
                buttons.append(row)
            followers = [x for x in posts if x["kind"] in ("story", "carousel")]
            if not any(x["status"] in ("pending", "waiting", "published", "unknown") for x in followers):
                # the Reel posted after all (a retry), its Stories and carousel cancelled with the failed slot
                lines.append("  Stories and carousel: " + ("cancelled with the failed slot" if followers else "not booked"))
                buttons.append([{"text": "📱 Book Stories and carousel again", "callback_data": f"rebook:{key}"}])
    return "\n".join(lines), buttons + [[{"text": "← Posting", "callback_data": "cposts:"}]]


def confirm_post_now(db, run_id: str) -> Tuple[str, List[List[Dict[str, str]]]]:
    from . import config
    label = html.escape(db.run_label(run_id))
    return (f"⚡ <b>Post now?</b>\n{label}\n\nThe Reel goes out now on Facebook, Instagram and YouTube; its Stories "
            f"follow {config.STORY_DELAY_MINUTES} min later and the carousel in its slot. This can't be undone.",
            [[{"text": "✅ Yes, post it now", "callback_data": f"postnow:{db.run_key(run_id)}"}],
             [{"text": "✖ Not now", "callback_data": f"cpost:{db.run_key(run_id)}"}]])


def confirm_cancel(db, post_id: int) -> Tuple[str, List[List[Dict[str, str]]]]:
    post = next((p for p in db.list_scheduled_posts(("pending", "waiting")) if p["id"] == post_id), None)
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
