"""
src/telegram_ui.py
==================
One look for every operator screen (the 2026-10-25 overhaul). A screen is: a title line (one emoji, one name),
an optional one-line summary under it, short lines (a fact a line, never a fact-packed one), a blockquote for
anything quoted or long, only the buttons that belong to the screen, and the same two navigation buttons at
the bottom: ← one level up, 🏠 Dashboard. Every screen names its parent here, so "up" always means one level.
"""
import html
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

HOME_CB = "cdash:"
HOME = {"text": "🏠 Dashboard", "callback_data": HOME_CB}
BUTTON_LIMIT = 28  # characters a button shows whole on a phone; longer labels are set at a word boundary

# callback prefix -> (emoji, name, parent callback). The parent is where ← goes. A screen with a topic or run
# in its callback (imagehelp:<topic>, cpost:<run>) names the parent screen; the button carries no argument.
SCREENS: Dict[str, Tuple[str, str, Optional[str]]] = {
    "cdash": ("🏛️", "Dashboard", None),
    "cprog": ("⏳", "Progress", HOME_CB),
    "qrefresh": ("📋", "Queue", HOME_CB), "qpage": ("📋", "Queue", HOME_CB),
    "pickbump": ("⬆️", "Bump a topic", "qrefresh:"), "pickdrop": ("🗑", "Drop a topic", "qrefresh:"),
    "cideas": ("💡", "Ideas", "qrefresh:"), "cprop": ("💡", "Waiting ideas", "cideas:"),
    "cdisc": ("🏛", "UNESCO marvels", "cideas:"), "discoverpanel": ("🏛", "UNESCO marvels", "cideas:"),
    "creviews": ("🎬", "Reviews", HOME_CB), "chistory": ("📚", "History", "creviews:"), "chist": ("📚", "Review", "chistory:"),
    "cposts": ("🗓", "Posting", HOME_CB), "cpost": ("🗓", "Episode", "cposts:"), "cslots": ("🗓", "Slots", "cposts:"),
    "postnowask": ("⚡", "Post now", "cposts:"), "cancelask": ("⏹", "Cancel booking", "cposts:"),
    "newversionask": ("♻️", "New version", "cposts:"),
    "cimages": ("🖼", "Pictures", HOME_CB), "imagepick": ("🖼", "Photo sets", "cimages:"),
    "imagehelp": ("🖼", "Topic pictures", "imagepick:0"), "review": ("🖼", "Photo set", "imagepick:0"),
    "reviewpick": ("🖼", "Photo sets", "cimages:"), "upload": ("📎", "Upload", "cimages:"),
    "ccov": ("🎨", "Covers", "cimages:"), "covers": ("🎨", "Covers", "cimages:"),
    "cmusic": ("🎵", "Music", HOME_CB), "musicmenu": ("🎵", "Music", HOME_CB), "mfmenu": ("🎵", "Find music", "cmusic:"),
    "mreview": ("🎵", "Music review", "cmusic:"), "mlibrary": ("🎵", "Music library", "cmusic:"),
    "cstat": ("📊", "Status", HOME_CB), "canalytics": ("📈", "Audience overview", "cstat:"), "caudit": ("🛡", "Activity", "cstat:"),
    # The analytics dashboard (2026-10-28): one platform, one video across platforms, the video picker.
    "caplat": ("📊", "Platform analytics", "canalytics:"), "cavideo": ("🎞", "Video audience", "capick:0"),
    "capick": ("🎞", "Videos measured", "canalytics:"),
    # The audience chart (2026-10-29): a photo of the last six Reels' 7-day views, ← back to the overview.
    "cachart": ("📊", "Audience chart", "canalytics:"),
    "cimprove": ("🧭", "Improvements", "cstat:"), "cdigest": ("📰", "Weekly digest", "cstat:"),
    "caccounts": ("🔗", "Accounts", "cstat:"), "calerts": ("🔔", "Alerts", "cstat:"), "jobs": ("🧰", "Jobs", "cstat:"),
    "chelp": ("❓", "Help", HOME_CB),
    "runstop": ("⏹", "Cancel run", "jobs:"), "runstopok": ("⏹", "Cancel run", "jobs:"),
    "edit": ("✏️", "Edit text", HOME_CB), "cfootage": ("🎞", "Footage", "cimages:"),
    # QF-81: a review's carousel, drawn on demand from its card (both copies, audited as at its slot)
    "ccar": ("🖼", "Carousel preview", "creviews:"),
}
# Typed commands draw the same screens.
SCREENS.update({
    "queue": SCREENS["qrefresh"], "progress": SCREENS["cprog"], "status": SCREENS["cstat"], "reviews": SCREENS["creviews"],
    "history": SCREENS["chistory"], "posts": SCREENS["cposts"], "photos": SCREENS["cimages"], "images": SCREENS["cimages"],
    "image": SCREENS["imagehelp"], "music": SCREENS["cmusic"], "covers": SCREENS["ccov"], "ideas": SCREENS["cideas"],
    "proposals": SCREENS["cprop"], "discover": SCREENS["cdisc"], "jobs": SCREENS["jobs"], "alerts": SCREENS["calerts"],
    "audit": SCREENS["caudit"], "activity": SCREENS["caudit"], "analytics": SCREENS["canalytics"], "chart": SCREENS["cachart"], "improve": SCREENS["cimprove"],
    "digest": SCREENS["cdigest"], "accounts": SCREENS["caccounts"], "help": SCREENS["chelp"],
})


# Screens a ← may replay: they only show something. The others in SCREENS do something when opened (start a
# contact-sheet job, open an edit or upload flow, confirm a cancel), so ← never repeats them.
NOT_REPLAYABLE = {"runstop", "runstopok", "edit", "upload", "review", "pickbump", "pickdrop", "mreview", "mfmenu",
                  "ccar"}
TRAIL_LIMIT = 12


def replayable(callback: Optional[str]) -> bool:
    key = (callback or "").partition(":")[0]
    return key in SCREENS and key not in NOT_REPLAYABLE


def step(trail: List[str], callback: str) -> List[str]:
    """The panel's path after `callback` is shown (operator, 2026-10-25: ← goes back to where you came from,
    Jobs back to Status, a run's history back to its page). The Dashboard starts a new path; ← to the screen
    before pops; another page or a Refresh of the same screen replaces it; anything else is a step down."""
    if not replayable(callback):
        return trail
    if callback.partition(":")[0] == "cdash":
        return [callback]
    if len(trail) >= 2 and trail[-2] == callback:
        return trail[:-1]
    if trail and SCREENS[trail[-1].partition(":")[0]][1] == SCREENS[callback.partition(":")[0]][1]:
        return trail[:-1] + [callback]
    return (trail + [callback])[-TRAIL_LIMIT:]


def came_from(trail: Sequence[str], callback: Optional[str]) -> Optional[str]:
    """Where ← goes from `callback`: the screen it was opened from when the path knows it, else its parent."""
    key = (callback or "").partition(":")[0]
    if len(trail) >= 2 and trail[-1].partition(":")[0] == key:
        return trail[-2]
    return parent_of(callback)


def hourglass() -> str:
    """The progress card's hourglass turns on each redraw (the card redraws every minute): a minimal sign of
    life, with no custom animated emoji (bots have none)."""
    import time
    return "⏳" if int(time.time() // 60) % 2 else "⌛"


def screen_name(callback: Optional[str]) -> Optional[str]:
    found = SCREENS.get((callback or "").partition(":")[0])
    return f"{found[0]} {found[1]}" if found else None


def parent_of(callback: Optional[str]) -> Optional[str]:
    found = SCREENS.get((callback or "").partition(":")[0])
    return found[2] if found else None


def back_button(parent: Optional[str]) -> Optional[Dict[str, str]]:
    """← to the parent screen (named, so the operator knows where it goes)."""
    if not parent or parent == HOME_CB:
        return None
    found = SCREENS.get(parent.partition(":")[0])
    return {"text": label(f"← {found[1]}") if found else "← Back", "callback_data": parent}


def nav(parent: Optional[str] = HOME_CB, extra: Optional[Dict[str, str]] = None) -> List[List[Dict[str, str]]]:
    """The last row of every screen: ← one level up (when there is one) and 🏠 Dashboard; `extra` (a
    Refresh, say) joins the row before them when there is room."""
    row = [b for b in (extra, back_button(parent)) if b] + [HOME]
    return [row]


def with_nav(rows: Sequence[Sequence[Dict[str, str]]], parent: Optional[str] = HOME_CB,
             extra: Optional[Dict[str, str]] = None) -> List[List[Dict[str, str]]]:
    """`rows` with the navigation row appended once (an earlier Dashboard button is dropped, so there is one)."""
    skip = {HOME_CB, parent} if parent else {HOME_CB}  # a handler's own "← Parent" is replaced by the standard one
    kept = [[b for b in row if b.get("callback_data") not in skip and not str(b.get("text", "")).startswith("←")]
            for row in rows if row]
    kept = [row for row in kept if row]
    return kept + nav(parent, extra)


def title(key_or_emoji: str, name: Optional[str] = None, summary: Optional[str] = None) -> str:
    """'🎬 <b>Reviews</b>' with an optional second line of plain summary (never packed into the title)."""
    if name is None:
        emoji, name, _ = SCREENS.get(key_or_emoji.partition(":")[0], ("•", key_or_emoji, None))
    else:
        emoji = key_or_emoji
    head = f"{emoji} <b>{html.escape(name)}</b>"
    return head + (f"\n{summary}" if summary else "")


def section(heading: str, lines: Sequence[str]) -> str:
    """A bold heading over its lines; '' for a section with nothing to say."""
    body = [ln for ln in lines if ln]
    return (f"<b>{html.escape(heading)}</b>\n" + "\n".join(body)) if body else ""


def compose(*blocks: Optional[str]) -> str:
    """Blocks separated by one blank line; empty blocks leave no gap."""
    return "\n\n".join(b.strip("\n") for b in blocks if b and b.strip())


def quote(text: str, expandable: bool = False, limit: int = 1200) -> str:
    """A blockquote for quoted or long text (a reason, an error, a caption): Telegram folds an expandable one."""
    body = html.escape(" ".join(str(text or "").split())[:limit])
    if not body:
        return ""
    return f"<blockquote expandable>{body}</blockquote>" if expandable else f"<blockquote>{body}</blockquote>"


def label(text: str, limit: int = BUTTON_LIMIT) -> str:
    """A button label that shows whole: cut at a word boundary with an ellipsis, never mid-word."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:·(—-")
    return (cut or text[:limit - 1]) + "…"


def button(text: str, callback: str) -> Dict[str, str]:
    return {"text": label(text), "callback_data": callback}


def counted(text: str, n: int) -> str:
    """'🎬 Reviews (2)' when there is something, else the plain label."""
    return f"{text} ({n})" if n else text


def long_labels(rows: Sequence[Sequence[Dict[str, Any]]], limit: int = BUTTON_LIMIT) -> List[str]:
    """Button texts over the limit (for the tests that keep every screen's buttons whole)."""
    return [b["text"] for row in rows for b in row if len(str(b.get("text", ""))) > limit]


_SEPARATED = re.compile(r" · ")


def too_packed(text: str, max_separators: int = 3) -> List[str]:
    """Lines that pack more than `max_separators` ' · ' facts (for the tests that keep lines short)."""
    return [ln for ln in re.sub(r"<[^>]+>", "", text).splitlines() if len(_SEPARATED.findall(ln)) > max_separators]
