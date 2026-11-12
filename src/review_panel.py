"""
src/review_panel.py
===================
The clean chat's review panel (UI-01e, design docs/design/telegram-clean-chat-2026-11-11.md §6.5, §6.5.1, §6.6;
mock-ups 9, 10 and 11): one video message for every review waiting, below the dashboard.

- **The pager.** The reviews waiting, soonest-closing window first; the caption's first line says where you are
  ("🎬 Review 1 of 3") and the pager row shows only the arrows that lead somewhere. Paging swaps the message's media
  with editMessageMedia from the video's file_id: the video is uploaded once, the first time it is shown, and never
  again. A review that arrives while another is shown only raises the count.
- **Tabs.** Summary (the verdict, with the decision rows), Script, Posts (what Approve posts, word for word: it
  replaces "In full"), Checks (the release gate, a blocking check first) and Pictures (each illustration beside its
  reference, then the Story frames and carousel slides, one photo a page). Every face fits a 1,024-character caption;
  a longer text continues on the tab's own pages.
- **Decisions.** Approve & post is one tap: the review leaves the pager and the info item "✅ Approved" carries Undo
  until 15 minutes before its slot. A version the release gate blocks shows a disabled "Blocked by a check" in its
  place. Reject and Regenerate open the reason step (improve.REASONS, "Other" shown as "In my words"), which is their
  confirmation; after a reason the face asks for a note for 2 minutes (90 s for a new version).
- **Typed input** is asked inside this panel (Edit text, In my words, the note): no message is sent, the operator's
  text is read and deleted. One input waits at a time (the bot's `awaiting_reply`); a /command still runs.
- **Endings.** A decision or a timeout takes a review out of the pager; with none left the panel is deleted.

Every value shown comes from the run's own records: its script payload, its release gate report, the copy Approve
posts (publishable.card_record, the QF-43 record the envelope must match) and the files under its folder.
"""
from __future__ import annotations

import functools
import html
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

from . import config

log = logging.getLogger(__name__)

SLOT = "review"
STATE = "review-panel"              # telegram_state namespace: each delivered review's record (files, file ids)
CAPTION_LIMIT = 1024                # Telegram's caption, after entities (F9)
ROOM = 1000                         # what a face's text may use: a margin for the head line's digits
REASON_SECONDS = 300                # the reason step restores itself after this (design §6.5.1)
REJECT_NOTE_SECONDS = 120           # a note after a Reject's reason can be typed this long
INPUT_SECONDS = 600                 # Edit text and In my words wait this long (as today's /ask, 600 s)
INPUT = "rvin"                      # the review panel's name in the bot's one armed input (awaiting_reply)
TABS = {"sum": "Summary", "scr": "Script", "pst": "Posts", "chk": "Checks", "pic": "Pictures"}
REDRAW_KEY = "review-panel-redraw"   # set by a process that is not the renderer: the renderer draws the panel
REVIEW_ACTIONS = {"rvp", "rvt", "rvi", "rva", "rvb", "rvr", "rvg", "rvk", "rvs", "rvn", "rvd", "rvu", "rve", "rvc",
                  "rvshow", "rvua"}


def enabled() -> bool:
    return bool(getattr(config, "TELEGRAM_CLEAN_CHAT", False))


def reason_labels() -> dict[str, str]:
    """improve.REASONS as the reason step shows them: "Other" is "In my words", which asks for your words (operator
    ruling, 2026-11-11). The codes are unchanged, so the ledger and the regenerated run read them as before."""
    from .improve import REASONS
    return {**REASONS, "other": "In my words"}


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def visible_len(text: str) -> int:
    """A caption's length as Telegram counts it: after its entities are parsed (tags gone, entities decoded), in
    UTF-16 code units."""
    from .notifications import plain
    return len(plain(text).encode("utf-16-le")) // 2


def _split_words(text: str, room: int) -> list[str]:
    """A plain text cut into pieces of at most `room` visible characters, at word boundaries (nothing is lost)."""
    words, out, cur = str(text or "").split(" "), [], ""
    for w in words:
        cand = (cur + " " + w) if cur else w
        if cur and len(cand) > room:
            out.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        out.append(cur)
    return out


def paginate(blocks: list[str], room: int) -> list[str]:
    """HTML blocks packed into pages of at most `room` visible characters, in order; a block longer than a page
    is split at a line and then at a word (its tags are those of a plain line: no block that long carries any)."""
    pieces: list[str] = []
    for block in blocks:
        if visible_len(block) <= room:
            pieces.append(block)
            continue
        for line in block.split("\n"):
            if visible_len(line) <= room:
                pieces.append(line)
            else:
                from .notifications import plain
                pieces += [_e(p) for p in _split_words(plain(line), room)]
    pages, cur = [], ""
    for piece in pieces:
        cand = (cur + "\n" + piece) if cur else piece
        if cur and visible_len(cand) > room:
            pages.append(cur)
            cur = piece
        else:
            cur = cand
    if cur or not pages:
        pages.append(cur)
    return pages


# ------------------------------------------------------------------ the run's records
def _run_dir(db, run_id: str) -> Path | None:
    run = db.get_run(run_id) or {}
    return Path(run["master_video_path"]).parent if run.get("master_video_path") else None


def release_of(db, run_id: str) -> dict[str, Any] | None:
    root = _run_dir(db, run_id)
    try:
        path = root / "release_report.json" if root else None
        return json.loads(path.read_text(encoding="utf-8")) if path and path.is_file() else None
    except (OSError, ValueError):
        return None


def blocked(release: dict[str, Any] | None) -> list[dict[str, Any]]:
    from .release import blocking_checks
    return blocking_checks(release, dry_run=bool((release or {}).get("dry_run"))) if release else []


def measured_length(release: dict[str, Any] | None) -> float | None:
    for c in (release or {}).get("checks") or []:
        if c.get("name") == "duration":
            m = re.search(r"Measured ([\d.]+)s", str(c.get("detail") or ""))
            if m:
                return float(m.group(1))
    return None


def side_by_side(images: list[Path], out: Path, height: int = 720) -> Path | None:
    """One picture of an illustration beside its reference(s), as the review albums showed them in turn."""
    try:
        from PIL import Image
        opened = []
        for p in images:
            with Image.open(p) as im:
                im = im.convert("RGB")
                w = max(1, round(im.width * height / max(1, im.height)))
                opened.append(im.resize((w, height)))
        gap = 16
        canvas = Image.new("RGB", (sum(i.width for i in opened) + gap * (len(opened) - 1), height), (18, 18, 18))
        x = 0
        for im in opened:
            canvas.paste(im, (x, 0))
            x += im.width + gap
        if canvas.width > 2560:   # a photo Telegram shows whole: width at most 2,560
            canvas = canvas.resize((2560, max(1, round(height * 2560 / canvas.width))))
        out.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(out, "JPEG", quality=85)
        return out
    except Exception as e:  # noqa: BLE001 - the illustration and its reference are then shown one after the other
        log.warning("Could not put the illustration beside its reference (%s): %s", out.name, e)
        return None


def pictures_of(payload: dict[str, Any], run_dir: Path) -> list[dict[str, Any]]:
    """The Pictures tab, in order: each illustration beside its reference(s) (one picture; the narrated line, the
    reference's title and licence as its caption, from the run's picture_fit record), then the Story frames and the
    carousel slides Approve posts."""
    from .multi_format import RETIRED_STORY_FRAMES
    out: list[dict[str, Any]] = []
    for g in (payload.get("picture_fit") or {}).get("generated") or []:
        if not isinstance(g, dict):
            continue
        art = Path(str(g.get("path") or "")) if g.get("path") else run_dir / str(g.get("to") or "")
        if not art.is_file():
            continue
        refs = [r for r in g.get("references") or [] if isinstance(r, dict) and Path(str(r.get("path") or "")).is_file()]
        beat = str(g.get("beat"))
        pair = side_by_side([art] + [Path(str(r["path"])) for r in refs], run_dir / "review_pairs" / f"beat_{beat}.jpg") \
            if refs else None
        line = f"“{_e(str(g.get('line') or '')[:400])}”"
        if refs:
            said = "; ".join(f"{_e(str(r.get('title') or '')[:160])} ({_e(r.get('licence') or '?')})" for r in refs)
            caption = f"{line}\nBeat {_e(beat)} · illustration, then its reference: {said}"
        else:
            caption = f"{line}\nBeat {_e(beat)} · illustration · no reference: a kind of thing, drawn from the facts"
        if pair:
            out.append({"path": str(pair), "caption": caption})
        else:
            out.append({"path": str(art), "caption": caption})
            out += [{"path": str(r["path"]), "caption": f"Beat {_e(beat)} · reference: {_e(str(r.get('title') or '')[:160])} "
                                                        f"({_e(r.get('licence') or '?')})"} for r in refs]
    story = [p for p in sorted((run_dir / "stories").glob("story_*.jpg")) if p.name not in RETIRED_STORY_FRAMES]
    slides = sorted((run_dir / "carousel").glob("carousel_slide_*.jpg"))
    out += [{"path": str(p), "caption": f"Story frame {n} of {len(story)} · posted on approval"} for n, p in enumerate(story, 1)]
    out += [{"path": str(p), "caption": f"Carousel slide {n} of {len(slides)} · posted on approval"} for n, p in enumerate(slides, 1)]
    return out


def _stamp(path: Path) -> list:
    try:
        st = path.stat()
        return [st.st_size, int(st.st_mtime)]
    except OSError:
        return []


# ------------------------------------------------------------------ the panel
def _serial(fn):
    """One entry at a time into the panel (its own lock). The panel's lock is the innermost of telegram_layout's one
    lock order (Layout._rebuild_lock -> the bot's _dashboard_lock -> Layout._lock -> ReviewPanel._lock): the
    renderer's tick may hold Layout._lock when it is taken, never the other way. While it is held the panel never
    calls into the layout (kick, redraw, ensure, rebuild), the dashboard (show_dashboard) or a command handler
    (edittext, cancelpost: they draw the dashboard); those calls are deferred until it is released."""
    @functools.wraps(fn)
    def run(self, *args, **kwargs):
        with self._lock:
            self._depth += 1
            try:
                result = fn(self, *args, **kwargs)
            finally:
                self._depth -= 1
                later = []
                if self._depth == 0:
                    later, self._deferred = self._deferred, []
        for call in later:
            try:
                call()
            except Exception as e:  # noqa: BLE001 - a deferred redraw never breaks the tap that asked for it
                log.warning("Review panel follow-up failed: %s", e)
        return result
    return run


class ReviewPanel:
    """The one review panel of a bot. Only the renderer process sends, edits or deletes it (design §5.4): another
    process (a run worker, a CLI run beside the daemon) writes the review's records and asks the renderer to draw."""

    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.clock = time.time
        self._lock = threading.RLock()
        self._depth = 0
        self._deferred: list = []
        self._wrote = False

    def _later(self, call) -> None:
        """Runs `call` once the panel's lock is released (now, when it is not held)."""
        if self._depth:
            self._deferred.append(call)
        else:
            call()

    def _kick(self) -> None:
        self._wrote = True
        self._later(self.bot.layout.kick)

    def _redraw_notes(self) -> None:
        """A tap wrote an item (Approved, Rejected, an Undo): the renderer draws the notifications panel now, as a
        tap on that panel does (Layout.redraw, under Layout._lock); another process leaves it to the renderer."""
        layout = self.bot.layout
        if layout.is_renderer():
            layout.redraw()
        else:
            layout.kick()

    def _not_here(self) -> bool:
        """Only the renderer sends, edits or deletes the panel (design §5.4, UI-01b's lease rule): another process
        asks the renderer to draw it (REDRAW_KEY) and returns True."""
        if self._may_draw():
            return False
        self.db.set_setting(REDRAW_KEY, str(self.clock()))
        self._kick()
        return True

    def _may_draw(self) -> bool:
        """Only the renderer draws (design §5.4): this process when it holds the lease, or takes it because nobody
        holds it (a CLI run alone)."""
        from .telegram_layout import holder
        other = holder(self.db, self.clock())
        layout = self.bot.layout
        return other == layout.owner or (other is None and layout.take())

    # ---------------------------------------------------------- state
    def record(self, run_id: str) -> dict[str, Any] | None:
        from .telegram_state import get
        value = get(self.db, STATE, run_id)
        return value if isinstance(value, dict) else None

    def _put_record(self, run_id: str, value: dict[str, Any]) -> None:
        from .telegram_state import put
        put(self.db, STATE, run_id, value)

    def message(self) -> int | None:
        from .telegram_layout import slot_message
        return slot_message(self.db, SLOT)

    def view(self) -> dict[str, Any]:
        from .telegram_layout import slot
        card = slot(self.db, SLOT) or {}
        return dict(card.get("payload") or {})

    def _save(self, view: dict[str, Any], message_id: int | None = None) -> None:
        from .telegram_layout import put_slot
        mid = message_id or self.message()
        if mid:
            put_slot(self.db, SLOT, mid, view.get("sent_at"), **{k: v for k, v in view.items() if k != "sent_at"})

    def pager(self, view: dict[str, Any] | None = None) -> list[str]:
        """The reviews in the panel, soonest-closing window first: every review waiting for a decision that was
        delivered here, and the one whose decided face (a note after its reason) is still shown."""
        view = self.view() if view is None else view
        runs = []
        for p in self.db.list_pending_approvals():
            run = self.db.get_run(p["run_id"]) or {}
            if run.get("status") == "pending_approval" and self.record(p["run_id"]):
                runs.append(p["run_id"])
        if view.get("face") == "noted" and view.get("run") and view["run"] not in runs and self.record(view["run"]):
            runs.append(view["run"])

        def closes(run_id: str) -> tuple:
            approval = self.db.get_approval(run_id) or {}
            try:
                at = float(approval.get("expires_at"))
            except (TypeError, ValueError):
                at = float("inf")
            return at, str(approval.get("created_at") or ""), run_id
        return sorted(runs, key=closes)

    # ---------------------------------------------------------- delivery
    @_serial
    def deliver(self, run_id: str, video_path: Path, thumbnail_path: Path, payload: dict[str, Any],
                release: dict[str, Any] | None) -> int | None:
        """The review joins the panel: the panel is sent (silently) when it is the first, otherwise only its count
        goes up (or, when it is the review shown, its face is drawn again). Records what Approve posts first."""
        from . import publishable
        from .telegram_bot import TELEGRAM_VIDEO_LIMIT_MB
        from .telegram_state import put, review_fingerprint
        video_path, thumbnail_path = Path(video_path), Path(thumbnail_path)
        if not video_path.is_file() or not 0 < video_path.stat().st_size <= TELEGRAM_VIDEO_LIMIT_MB * 1024 * 1024:
            raise ValueError("A deliverable review video is required")
        fingerprint = review_fingerprint(self.db, run_id)
        run = self.db.get_run(run_id) or {}
        try:
            sizer = getattr(self.bot, "approval_window", None)
            decider = getattr(self.bot, "approve_posting", None)
            mode = self.db.get_setting("run_mode:" + run_id)
            posting = decider(run_id) if callable(decider) else ("dry_run" if mode == "dry_run" else "now")
            posted = publishable.card_record(payload, self.db, run_dir=video_path.parent,
                                             window_seconds=sizer() if callable(sizer) else None,
                                             topic_id=run.get("topic_id") or "", posting=posting,
                                             # the media Approve posts decide the AI label, as on the live card
                                             # (QF-65a; without them the label is on for no media reason)
                                             master=run.get("master_video_path") or None,
                                             cover=run.get("thumbnail_cover_path") or None)
        except Exception as e:  # noqa: BLE001 - shown on Posts; with no copy recorded the envelope refuses to post
            log.warning("Could not work out what Approve posts for %s: %s", run_id, e)
            posted = {"error": f"{type(e).__name__}: {e}"}
        publishable.save_record(self.db, run_id, posted)   # before any button can decide (QF-43)
        old = self.record(run_id) or {}
        same_video = old.get("video") == str(video_path) and old.get("video_stamp") == _stamp(video_path)
        same_cover = old.get("cover") == str(thumbnail_path) and old.get("cover_stamp") == _stamp(thumbnail_path)
        known = {p["path"]: p.get("id") for p in old.get("pictures") or [] if p.get("path")}
        pictures = [{**p, "id": known.get(p["path"]) if p["path"] in known else None}
                    for p in pictures_of(payload, video_path.parent)]
        self._put_record(run_id, {
            "video": str(video_path), "video_stamp": _stamp(video_path),
            "video_id": old.get("video_id") if same_video else None,
            "cover": str(thumbnail_path) if thumbnail_path.is_file() else None, "cover_stamp": _stamp(thumbnail_path),
            "cover_id": old.get("cover_id") if same_cover and same_video else None,
            "pictures": pictures, "fingerprint": fingerprint, "width_height": _dims(video_path)})
        put(self.db, "review-media", run_id, fingerprint)
        from . import notifications as notes
        notes.close_key(self.db, f"undelivered:{run_id}", notes.RESOLVED)   # delivered now
        if not self._may_draw():
            # Another process renders the chat: the review is recorded and bound to the panel there is; the renderer
            # draws it on its next tick (REDRAW_KEY) and binds every review to the panel it draws.
            mid = self.message()
            self.db.record_approval(run_id, payload.get("topic_id", "") or run.get("topic_id", ""), mid)
            if mid:
                self._bind(run_id, mid)
            self.db.set_setting(REDRAW_KEY, str(self.clock()))
            self._kick()
            return mid
        mid = self.message()
        view = self.view()
        if not mid:
            view = {"run": run_id, "tab": "sum", "sub": 1}
            mid = self._send(view)
            if not mid:
                return None
        else:
            if view.get("run") == run_id and view.get("face") in ("got", None):
                view.update(tab="sum", sub=1, face=None, notice=None)   # its new copy (an edit, a cover) on Summary
                self._save(view)
                self.draw(media=True)
            else:
                self.draw()
        self._bind(run_id, mid)
        self.db.record_approval(run_id, payload.get("topic_id", "") or run.get("topic_id", ""), mid)
        self._bind(run_id, mid)
        self._kick()
        return mid

    def _bind(self, run_id: str, mid: int) -> None:
        """The panel's message is the review's card: its taps decide it (db.decide_telegram_review)."""
        from .telegram_state import put, review_fingerprint
        try:
            fingerprint = review_fingerprint(self.db, run_id)
        except ValueError:
            return
        put(self.db, "review-card", f"{run_id}:{mid}", fingerprint)
        put(self.db, "message-run", f"{self.bot.chat_id}:{mid}", run_id)

    @_serial
    def redraw_if_asked(self) -> bool:
        """The renderer's side of a delivery made by another process: the panel is drawn (sent when it is the
        first review) and every review in the pager is bound to it."""
        if not self.db.get_setting(REDRAW_KEY):
            return False
        self.db.set_setting(REDRAW_KEY, "")
        pager = self.pager()
        if not pager:
            return False
        mid = self.message()
        if not mid:
            mid = self._send({"run": pager[0], "tab": "sum", "sub": 1})
        else:
            self.draw()
            mid = self.message()
        if mid:
            self.rebind(mid)
        return bool(mid)

    def rebind(self, mid: int) -> None:
        """Every review in the pager decides through the panel's new message (a re-post)."""
        with self.db._conn() as conn:
            for run_id in self.pager():
                conn.execute("UPDATE approvals SET telegram_message_id=? WHERE run_id=? AND decision='pending'", (mid, run_id))
        for run_id in self.pager():
            self._bind(run_id, mid)

    # ---------------------------------------------------------- faces
    def _head(self, index: int, total: int, tab: str, extra: str = "") -> str:
        head = f"🎬 Review {index} of {total}"
        if tab != "sum":
            head += f" · {TABS[tab]}"
        return f"<b>{head}{extra}</b>"

    def face(self, view: dict[str, Any]) -> tuple[str, list[list[dict[str, Any]]], dict[str, Any] | None]:
        """(caption, keyboard rows, picture) for the face in view; picture is None for the video."""
        pager = self.pager(view)
        run_id = view.get("run")
        if run_id not in pager:
            return "", [], None
        index, total = pager.index(run_id) + 1, len(pager)
        key = self.db.run_key(run_id)
        run = self.db.get_run(run_id) or {}
        payload = run.get("script_payload") or {}
        title = _e(payload.get("title") or self.db.run_label(run_id))
        from . import publishable
        posted = publishable.load_record(self.db, run_id) or {}
        release = release_of(self.db, run_id)
        face, tab = view.get("face"), view.get("tab") or "sum"
        record = self.record(run_id) or {}
        notice = f"\n<i>{_e(view['notice'])}</i>" if view.get("notice") else ""
        if face in ("reject", "regen"):
            return self._reason_face(view, index, total, key, title, payload)
        if face == "noted":
            labels = reason_labels()
            regen = view.get("decision") == "regenerate"
            said = labels.get(view.get("reason") or "", "")
            head = (f"🔄 <b>New version</b> · {_e(said)}" if regen else f"❌ <b>Rejected</b> · {_e(said)}")
            ask = ("Add a note for the new version: type it now, or tap Done." if regen
                   else "Add a note for the improvement log: type it now, or tap Done.")
            row = [{"text": "Done", "callback_data": f"rvd:{key}"}]
            if not regen and float(config.REVIEW_UNDO_MINUTES) > 0:
                row.append({"text": "↩️ Undo", "callback_data": f"rvu:{key}"})
            return (f"{self._head(index, total, 'sum')}\n{title}\n\n{head}\n{ask}{notice}", [row], None)
        if face == "words":
            regen = view.get("decision") == "regenerate"
            text = (f"{self._head(index, total, 'sum')}\n{title}\n\n✍️ <b>In your words</b>\n"
                    + ("Type what to change below and send it; that starts the new version."
                       if regen else "Type what was wrong below and send it; that rejects it.")
                    + f"\nWaiting for your text · {INPUT_SECONDS // 60} min{notice}")
            return text, [[{"text": "Cancel", "callback_data": f"rvc:{key}"}]], None
        if face == "edit":
            copy = (posted.get("copy") or {}) if isinstance(posted, dict) else {}
            current = copy.get("reel.caption") or payload.get("caption") or ""
            head = (f"{self._head(index, total, 'pst')}\n{title}\n\n✏️ <b>Edit the post text</b>\n"
                    "Type the new caption below and send it. It replaces the Reel caption on Instagram and Facebook.\n")
            tail = f"\nWaiting for your text · {INPUT_SECONDS // 60} min{notice}"
            room = ROOM - visible_len(head + tail) - 2
            shown = current if len(current) <= room else current[:max(0, room - 1)].rsplit(" ", 1)[0] + "…"
            text = head + (f"<blockquote>{_e(shown)}</blockquote>" if shown else "") + tail
            rows = [[{"text": "Copy current text", "copy_text": {"text": current[:256]}}] if current else [],
                    [{"text": "Cancel", "callback_data": f"rvc:{key}"}]]
            return text, [r for r in rows if r], None
        if face == "got":
            text = (f"{self._head(index, total, 'pst')}\n{title}\n\n✅ Got it: ‘{_e(view.get('said') or '')}’\n"
                    "⏳ Re-checking the release gate…")
            return text, [], None
        pager_row = self._review_pager(index, total, pager)
        if tab == "pic":
            pictures = record.get("pictures") or []
            if not pictures:
                tab = "sum"
            else:
                n = max(1, min(int(view.get("sub") or 1), len(pictures)))
                pic = pictures[n - 1]
                head = self._head(index, total, "pic", f" {n} of {len(pictures)}")
                caption = f"{head}\n{pic.get('caption') or ''}{notice}"
                if visible_len(caption) > CAPTION_LIMIT:
                    caption = f"{head}\n{_e(_split_words(re.sub('<[^>]+>', '', pic.get('caption') or ''), ROOM - 80)[0])}…"
                from .notifications import pager_row as arrows
                rows = [arrows(n, len(pictures), lambda p: f"rvi:{key}:{p}")]
                rows.append([{"text": "• Pictures", "callback_data": f"rvt:{key}:pic"},
                             {"text": "Summary", "callback_data": f"rvt:{key}:sum"}])
                return caption, [r for r in rows if r], {**pic, "n": n}
        if tab == "sum":
            return self._summary(view, index, total, key, title, payload, posted, release, record, pager_row, notice)
        pages = self._text_pages(tab, index, total, title, payload, posted, release)
        n = max(1, min(int(view.get("sub") or 1), len(pages)))
        head = self._head(index, total, tab, f" {n}/{len(pages)}" if len(pages) > 1 else "")
        caption = f"{head}\n{title}\n\n{pages[n - 1]}{notice}"
        from .notifications import pager_row as arrows
        rows = [arrows(n, len(pages), lambda p: f"rvi:{key}:{p}")] if len(pages) > 1 else []
        rows.append([{"text": f"• {TABS[tab]}", "callback_data": f"rvt:{key}:{tab}"},
                     {"text": "Summary", "callback_data": f"rvt:{key}:sum"}])
        if tab == "pst" and isinstance(posted, dict) and posted.get("carousel_at_slot"):
            # QF-81: a carousel drawn at its slot is previewed on demand (both copies, audited as at its slot); the
            # Posts text names this button. Drawn in the dashboard, never over this panel (telegram_bot).
            rows.append([{"text": "🖼 Preview carousel", "callback_data": f"ccar:{key}"}])
        return caption, [r for r in rows if r], None

    def _review_pager(self, index: int, total: int, pager: list[str]) -> list[dict[str, str]]:
        from .notifications import pager_row as arrows
        row = arrows(index, total, lambda p: f"rvp:{self.db.run_key(pager[p - 1])}")
        names = {"◀": "◀ Prev", "▶": "Next ▶"}
        return [{**b, "text": names.get(b["text"], b["text"])} for b in row]

    def _summary(self, view, index, total, key, title, payload, posted, release, record, pager_row, notice):
        from .telegram_bot import _plain, _schedule_line
        stop = blocked(release)
        open_checks = [c for c in (release or {}).get("checks") or [] if c.get("status") != "pass"]
        length = measured_length(release)
        from .release import check_problems
        found = [f"{c['name']}: {p}" for c in open_checks for p in check_problems(c)]
        from .telegram_bot import V2_NOTE_KEYS
        notes = found + [_plain(payload[k]) for k in V2_NOTE_KEYS if payload.get(k) and _plain(payload[k])]
        if not release or not release.get("checks"):
            lede = "⚠️ <b>Release gate not run yet</b>"
        elif stop:
            lede = f"⛔ <b>Can't be approved yet</b> · {len(stop)} check{'s block' if len(stop) != 1 else ' blocks'} it"
        elif open_checks:
            lede = f"⚠️ <b>Needs review before a live post</b> · {len(open_checks)} open check{'s' if len(open_checks) != 1 else ''}"
        else:
            lede = "✅ <b>Ready to approve</b>" + (f" · {len(notes)} note{'s' if len(notes) != 1 else ''} to check" if notes else "")
        regen = self.db.get_setting("regenerating:" + view["run"])
        lines = [self._head(index, total, "sum"), title,
                 *( [f"🔄 New version · your note: {_e(regen)}"] if regen else []), lede]
        hook = payload.get("hook_sentence") or payload.get("hook")
        if hook:
            lines.append(f"<i>“{_e(hook)}”</i>")
        slot = _plain(_schedule_line(posted)) if isinstance(posted, dict) and posted else ""
        if slot:
            lines.append(_e(slot))
        from .telegram_bot import _ai_label_line
        ai = _ai_label_line(posted, brief=True)   # QF-65a: whether the post carries the AI label; whole on Posts
        if ai:
            lines.append(ai.replace("; more in the reply", "; all of it on Posts"))
        facts = []
        if length is not None:
            facts.append(f"{length:.0f} s")
        if payload.get("word_count"):
            facts.append(f"{int(payload['word_count'])} words")
        pictures = len(record.get("pictures") or [])
        if pictures:
            facts.append(f"{pictures} picture{'s' if pictures != 1 else ''}")
        rights = [c for c in (release or {}).get("checks") or [] if "rights" in str(c.get("name"))]
        if rights:
            facts.append("rights " + ("✅" if all(c.get("status") == "pass" for c in rights) else "⚠️"))
        if facts:
            lines.append(" · ".join(facts))
        text = "\n".join(lines) + notice
        if notes:
            head = f"<b>Notes to check · {len(notes)}</b>"
            room = ROOM - visible_len(text) - visible_len(head) - 4
            shown, more = [], 0
            for i, n in enumerate(notes):
                line = "• " + _e(n)
                if visible_len("\n".join(shown + [line])) + 40 > room:
                    more = len(notes) - i
                    break
                shown.append(line)
            if more:
                shown.append(f"…and {more} more: Checks lists them all.")
            if shown and visible_len("\n".join(shown)) <= room:
                text += f"\n<blockquote expandable>{head}\n" + "\n".join(shown) + "</blockquote>"
        rows: list[list[dict[str, Any]]] = []
        if stop:
            rows.append([{"text": "Blocked by a check", "callback_data": f"rvb:{key}", "disabled": True}])
        else:
            rows.append([{"text": "Approve & post", "style": "success", "callback_data": f"rva:{key}"}])
        rows.append([{"text": "Edit text", "callback_data": f"rve:{key}"},
                     {"text": "Regenerate", "callback_data": f"rvg:{key}"}])
        covers = [] if payload.get("delivery_profile") == "deep_dive_16x9" else [{"text": "🎨 Covers", "callback_data": f"ccov:{key}"}]
        rows.append(covers + [{"text": "Reject", "style": "danger", "callback_data": f"rvr:{key}"}])
        rows.append([{"text": "Script", "callback_data": f"rvt:{key}:scr"}, {"text": "Posts", "callback_data": f"rvt:{key}:pst"}])
        rows.append([{"text": "Checks", "callback_data": f"rvt:{key}:chk"}]
                    + ([{"text": f"Pictures · {pictures}", "callback_data": f"rvt:{key}:pic"}] if pictures else []))
        if pager_row:
            rows.append(pager_row)
        return text, rows, None

    def _text_pages(self, tab, index, total, title, payload, posted, release) -> list[str]:
        room = ROOM - visible_len(f"{self._head(index, total, tab, ' 9/9')}\n{title}\n\n")
        blocks: list[str] = []
        if tab == "scr":
            narration = str(payload.get("narration_script") or "").strip()
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", narration) if s.strip()]
            blocks = [_e(s) for s in sentences] or ["<i>The run records no narration.</i>"]
            pages = paginate(blocks, room - 4)
            return [f"<blockquote expandable>{p}</blockquote>" for p in pages]
        if tab == "pst":
            from .telegram_bot import (
                _also_posted,
                _plain,
                _posted_hashtags_line,
                _schedule_line,
            )
            if not isinstance(posted, dict) or not posted or posted.get("error"):
                why = (posted or {}).get("error") if isinstance(posted, dict) else ""
                return [f"⚠️ What Approve posts could not be worked out{': ' + _e(why) if why else ''}. Approve will not post."]
            copy = posted.get("copy") or {}
            from .telegram_bot import _ai_label_line
            ai = _ai_label_line(posted)   # QF-65a: the whole AI-label line, as the live card's reply has it
            if ai:
                blocks.append(ai)
            for label, value in (("Title", copy.get("reel.title")), ("Caption", copy.get("reel.caption")),
                                 ("First comment", copy.get("reel.first_comment")),
                                 ("Carousel caption", copy.get("carousel.instagram") if posted.get("carousel_own") else "")):
                if value:
                    blocks.append(f"<b>{label}</b>\n{_e(value)}")
            if not posted.get("documentary"):
                blocks.append(_e(_plain(_posted_hashtags_line(posted))))
            also = _also_posted(posted, copy)
            if also:
                blocks.append("<b>Also posted</b>\n" + "\n".join("• " + _e(_plain(a)) for a in also))
            slot = _plain(_schedule_line(posted))
            if slot:
                blocks.append(_e(slot))
            return paginate(blocks, room)
        # Checks: what blocks first, then what is open, then what passed (the gate's own words)
        from .release import check_problems
        checks = (release or {}).get("checks") or []
        if not checks:
            return ["⚠️ The release gate has not run for this version."]
        stop = blocked(release)
        if stop:
            blocks.append(f"⛔ <b>Blocked</b> · {len(stop)} check{'s' if len(stop) != 1 else ''}\n"
                          + "\n".join(f"• {_e(c['name'])}: {_e(p)}" for c in stop for p in check_problems(c)))
        rest = [c for c in checks if c.get("status") != "pass" and not any(c is s for s in stop)]
        if rest:
            blocks.append("⚠️ <b>Open, not blocking</b>\n"
                          + "\n".join(f"• {_e(c['name'])}: {_e(p)}" for c in rest for p in check_problems(c)))
        minor = [f"• {_e(c['name'])}: {_e(p)}" for c in checks for p in check_problems(c, "notes")]
        if minor:
            blocks.append("<b>Minor findings</b>\n" + "\n".join(minor))
        passed = [c["name"] for c in checks if c.get("status") == "pass"]
        if passed:
            blocks.append(f"✅ <b>Passed</b> · {len(passed)}\n" + _e(", ".join(passed)))
        return paginate(blocks, room)

    def _reason_face(self, view, index, total, key, title, payload):
        regen = view.get("face") == "regen"
        if regen:
            from .telegram_bot import typical_run_seconds
            typical = typical_run_seconds()
            estimate = f" About {max(1, round(typical / 60))} min." if typical else ""
            head = f"🔄 <b>Make a new version?</b>\nPick what to change; that starts it.{estimate}"
        else:
            head = ("❌ <b>Reject this version?</b>\nPick what was wrong; that rejects it. Nothing is posted, and the "
                    "topic is set aside (restore it from Queue › Restorable).")
        left = max(0, int(float(view.get("face_until") or 0) - self.clock()))
        text = f"{self._head(index, total, 'sum')}\n{title}\n\n{head}\nExpires in {max(1, (left + 59) // 60)} min"
        chips = [{"text": label, "callback_data": f"rvs:{key}:{code}"} for code, label in reason_labels().items()]
        rows = [chips[i:i + 2] for i in range(0, len(chips), 2)]
        if regen:
            rows.append([{"text": "Redo without a note", "callback_data": f"rvn:{key}"}])
        rows.append([{"text": "← Keep it", "callback_data": f"rvk:{key}"}])
        return text, rows, None

    # ---------------------------------------------------------- Telegram
    def _media(self, run_id: str, caption: str, picture: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
        """(InputMedia, files): from the file_id when the file was uploaded before, else attached once."""
        record = self.record(run_id) or {}
        files: dict[str, Any] = {}
        if picture is not None:
            media = {"type": "photo", "caption": caption, "parse_mode": "HTML"}
            if picture.get("id"):
                media["media"] = picture["id"]
            else:
                media["media"] = "attach://photo"
                files["photo"] = Path(picture["path"])
            return media, files
        media = {"type": "video", "caption": caption, "parse_mode": "HTML", "supports_streaming": True,
                 **(record.get("width_height") or {})}
        if record.get("video_id"):
            media["media"] = record["video_id"]
        else:
            media["media"] = "attach://video"
            files["video"] = Path(record["video"])
        if record.get("cover_id"):
            media["cover"] = record["cover_id"]
        elif record.get("cover") and Path(record["cover"]).is_file():
            media["cover"] = "attach://cover"
            files["cover"] = Path(record["cover"])
        return media, files

    def _remember(self, run_id: str, result: Any, picture: dict[str, Any] | None) -> None:
        """The file ids Telegram gave the upload: every later page is drawn from them, with no upload."""
        if not isinstance(result, dict):
            return
        record = self.record(run_id) or {}
        if picture is not None:
            sizes = result.get("photo") or []
            if sizes and not picture.get("id"):
                for p in record.get("pictures") or []:
                    if p.get("path") == picture.get("path"):
                        p["id"] = sizes[-1].get("file_id")
                self._put_record(run_id, record)
            return
        video = result.get("video") or {}
        changed = False
        if video.get("file_id") and not record.get("video_id"):
            record["video_id"], changed = video["file_id"], True
        covers = video.get("cover") or []
        if covers and not record.get("cover_id"):
            record["cover_id"], changed = covers[-1].get("file_id"), True
        if changed:
            self._put_record(run_id, record)

    def _call(self, method: str, data: dict[str, Any], files: dict[str, Path]) -> Any:
        from contextlib import ExitStack
        with ExitStack() as stack:
            handles = {name: stack.enter_context(open(path, "rb")) for name, path in files.items()}
            return self.bot._api(method, timeout=300 if files else 30, data=data, files=handles or None)

    def _send(self, view: dict[str, Any]) -> int | None:
        """The panel's message: born last, under the dashboard and any progress panel, silently (the push alert
        rings for it)."""
        if self._not_here():
            return None
        caption, rows, picture = self.face(view)
        if not caption:
            return None
        run_id = view["run"]
        media, files = self._media(run_id, caption, picture)
        kind = media["type"]
        data = {"chat_id": self.bot.chat_id, "caption": caption, "parse_mode": "HTML", "disable_notification": "true",
                "reply_markup": json.dumps({"inline_keyboard": rows})}
        if kind == "video":
            data.update(supports_streaming="true", **{k: v for k, v in media.items() if k in ("width", "height")})
            if "video" not in files:
                data["video"] = media["media"]          # its file id: no upload
            if "cover" in media:
                data["cover"] = media["cover"]          # a file id, or attach://cover with the file
        elif "photo" not in files:
            data["photo"] = media["media"]
        try:
            result = self._call("sendVideo" if kind == "video" else "sendPhoto", data, files)
        except Exception as e:  # noqa: BLE001 - the review stays in the pager; a Resend delivers it again
            log.warning("Could not send the review panel: %s", e)
            return None
        mid = result.get("message_id") if isinstance(result, dict) else None
        if not mid:
            return None
        self._remember(run_id, result, picture)
        view = {**view, "sent_at": self.clock(), "media": kind}
        self._save(view, mid)
        self.bot._record_kind(mid, media=True)
        return int(mid)

    def draw(self, media: bool = False) -> bool:
        """Draws the face in view: its caption and keyboard in place, or its media too (paging, Pictures) with
        editMessageMedia from the file id. With no review left the panel is deleted. Renderer only."""
        if self._not_here():
            return False
        mid = self.message()
        view = self.view()
        pager = self.pager(view)
        if not pager:
            if mid:
                self.close()
            return False
        if view.get("run") not in pager:
            # The review shown left (decided, timed out): the next one in the same place, or the last.
            old = view.get("index") or 1
            view.update(run=pager[min(len(pager), max(1, int(old))) - 1], tab="sum", sub=1, face=None, notice=None)
            media = True
        view["index"] = pager.index(view["run"]) + 1
        if not mid:
            return bool(self._send(view))
        caption, rows, picture = self.face(view)
        kind = "photo" if picture is not None else "video"
        media = media or kind != view.get("media")
        self._save({**view, "media": kind})
        markup = json.dumps({"inline_keyboard": rows})
        try:
            if media:
                spec, files = self._media(view["run"], caption, picture)
                result = self._call("editMessageMedia", {"chat_id": self.bot.chat_id, "message_id": mid,
                                                         "media": json.dumps(spec), "reply_markup": markup}, files)
                self._remember(view["run"], result, picture)
            else:
                self.bot._api("editMessageCaption", data={"chat_id": self.bot.chat_id, "message_id": mid,
                                                          "caption": caption, "parse_mode": "HTML",
                                                          "reply_markup": markup})
            return True
        except Exception as e:  # noqa: BLE001 - a failed draw keeps the panel as it was
            reason = str(e).lower()
            if "message is not modified" in reason:
                return True
            if "message to edit not found" in reason or "message_id_invalid" in reason:
                from .telegram_layout import drop_slot
                log.warning("The review panel is gone (%s); sending it again.", e)
                drop_slot(self.db, SLOT)
                new = self._send(view)
                if new:
                    self.rebind(new)
                return bool(new)
            log.warning("Could not draw the review panel: %s", e)
            return False

    def close(self) -> None:
        """No review left: the panel goes (design §6.5)."""
        from .telegram_layout import drop_slot
        mid = self.message()
        if mid:
            self.bot.delete_message(mid)
        drop_slot(self.db, SLOT)
        self._disarm()

    @_serial
    def refresh(self) -> bool:
        """The pager changed elsewhere (a decision from the command line, a timeout): drawn again."""
        if not self.message():
            return False
        return self.draw()

    # ---------------------------------------------------------- typed input
    def _arm(self, purpose: str, seconds: float) -> None:
        """The one armed input is this panel's (design §6.6): one that was waiting elsewhere is cancelled, and the
        dashboard says so."""
        waiting = self.bot._awaiting
        if waiting and waiting[0] != INPUT and float(waiting[1]) > self.clock():
            text = (f"⌨️ /{_e(waiting[0])} is no longer waiting for your text: the review panel asked for it "
                    "instead.")
            self._later(lambda: self.bot.show_dashboard(text, None))
        self.bot._awaiting = (INPUT, self.clock() + seconds)
        view = self.view()
        view.update(input=purpose, input_until=self.clock() + seconds)
        self._save(view)

    def _disarm(self) -> None:
        waiting = self.bot._awaiting
        if waiting and waiting[0] == INPUT:
            self.bot._awaiting = None

    def armed(self) -> bool:
        waiting = self.bot._awaiting
        return bool(waiting and waiting[0] == INPUT and float(waiting[1]) > self.clock() and self.message())

    @_serial
    def cancelled_by(self, command: str) -> None:
        """Another input took the place of this panel's (a bare /ask): the panel says so and restores its face."""
        view = self.view()
        if view.get("face") in ("edit", "words", "noted"):
            leaving = view.get("face") == "noted"
            view.update(face=None, input=None, notice=f"Your text is now for /{command}, not this review.")
            if view.get("tab") == "pst" and leaving:
                view["tab"] = "sum"
            self._save(view)
            self.draw()

    @_serial
    def typed_photo(self) -> None:
        view = self.view()
        view["notice"] = "That was a photo; type the text instead."
        self._save(view)
        self.draw()

    @_serial
    def typed(self, text: str, command_handler) -> bool:
        """The operator's text answers the panel's armed input. Returns True when it was taken."""
        view = self.view()
        purpose, run_id = view.get("input"), view.get("run")
        self._disarm()
        if not run_id or not purpose:
            return False
        key = self.db.run_key(run_id)
        from .improve import record_reason
        said = text if len(text) <= 60 else text[:59].rsplit(" ", 1)[0] + "…"
        if purpose == "edit":
            view.update(face="got", said=said, input=None, notice=None)
            self._save(view)
            self.draw()

            def check() -> None:   # the fact check of the new text can take a while: not under the panel's lock
                reply = command_handler("edittext", f"{key} {text}") if command_handler else ""
                self._edit_result(reply[0] if isinstance(reply, tuple) else reply)
            self._later(check)
            return True
        if purpose == "words":
            decision = view.get("decision") or "rejected"
            if not self._decide(run_id, decision, view.get("by") or "telegram:operator"):
                self._toast_into(view, "This review is no longer waiting for a decision.")
                return True
            record_reason(self.db, run_id, "other", decision, text)
            self._after_decision(run_id, decision, "other")
            view.update(face=None, input=None, notice=None)
            self._save(view)
            self.draw(media=True)
            return True
        if purpose == "note":
            feedback = json.loads(self.db.get_setting(f"decision_reason:{run_id}") or "{}")
            record_reason(self.db, run_id, feedback.get("reason") or view.get("reason") or "other",
                          feedback.get("decision") or view.get("decision") or "", text)
            view.update(face=None, input=None, notice=None)
            self._save(view)
            self.draw(media=True)
            return True
        return False

    @_serial
    def _edit_result(self, reply: str) -> None:
        """After Edit text: a copy delivered again shows its Summary (deliver did it); one that was not (refused, or
        the same words) says why on Posts."""
        after = self.view()
        if after.get("face") == "got":
            from .notifications import clip, plain
            after.update(face=None, tab="pst", sub=1, notice=clip(plain(reply or ""), 300) or None)
            self._save(after)
            self.draw()

    def _toast_into(self, view: dict[str, Any], words: str) -> None:
        view.update(face=None, input=None, notice=words)
        self._save(view)
        self.draw()

    # ---------------------------------------------------------- decisions
    def _decide(self, run_id: str, decision: str, by: str) -> bool:
        mid = self.message()
        from .telegram_state import get, review_fingerprint
        try:
            fresh = get(self.db, "review-card", f"{run_id}:{mid}") == review_fingerprint(self.db, run_id)
        except ValueError:
            fresh = False
        return bool(mid and fresh and self.db.decide_telegram_review(run_id, mid, decision, by))

    def undo_until(self, run_id: str) -> float | None:
        """When the decided item's Undo ends: for an Approve whose slot is booked, 15 minutes before the slot
        (Posting's cancel then takes the booking back); otherwise the end of the UI-02 Undo window, before the
        worker acts on the decision."""
        from datetime import datetime

        from .telegram_bot import undo_until
        lead = float(config.REVIEW_APPROVE_UNDO_LEAD_MINUTES) * 60
        approval = self.db.get_approval(run_id) or {}
        if approval.get("decision") == "approved":
            booked = [p for p in self.db.list_scheduled_posts(("pending",), run_id=run_id) if p["kind"] == "reel"]
            if booked:
                try:
                    return datetime.fromisoformat(booked[0]["due_at"]).timestamp() - lead
                except (TypeError, ValueError):
                    return None
            from . import publishable
            posting = (publishable.load_record(self.db, run_id) or {}).get("posting")
            slot = (publishable.load_record(self.db, run_id) or {}).get("slot")
            if posting == "slot" and slot:
                try:
                    return datetime.fromisoformat(str(slot)).timestamp() - lead
                except (TypeError, ValueError):
                    pass
        if float(config.REVIEW_UNDO_MINUTES) <= 0:
            return None
        return undo_until(approval)

    def _after_decision(self, run_id: str, decision: str, reason: str | None = None) -> None:
        """The decided review's item (design §6.5): "✅ Approved" with Undo until 15 minutes before its slot,
        "❌ Rejected" with Undo for the Undo window, or "🔄 New version" (its progress panel follows)."""
        from . import notifications as notes
        from . import publishable
        from .local_time import show
        label = self.db.run_label(run_id)
        key = self.db.run_key(run_id)
        until = self.undo_until(run_id)
        undo = [{"text": "Undo approval" if decision == "approved" else "Undo", "callback_data": f"rvua:{key}"}] \
            if until and until > self.clock() and decision in ("approved", "rejected") else []
        then = f"Undo until {show(until, '%H:%M')}." if undo else ""
        if decision == "approved":
            posted = publishable.load_record(self.db, run_id) or {}
            body = "\n".join(x for x in (self._posting_line(posted), then) if x)
            notes.post(self.db, notes.INFO, f"posted:{run_id}", "Approved", body, emoji="✅", subject=label,
                       actions=undo, run=run_id, replace=True)
        elif decision == "rejected":
            said = reason_labels().get(reason or "", "")
            body = ("Nothing was posted." + (f" Reason: {said}." if said else "")
                    + (f"\n{then} Then the topic is set aside." if then else " The topic is set aside."))
            notes.post(self.db, notes.INFO, f"rejected:{run_id}", "Rejected", body, emoji="❌", subject=label,
                       actions=undo, run=run_id, replace=True)
        elif decision == "regenerate":
            said = reason_labels().get(reason or "", "")
            notes.post(self.db, notes.INFO, f"regen:{run_id}", "New version", (f"Your note: {said}. " if said else "")
                       + "Its progress panel follows.", emoji="🔄", subject=label, run=run_id, replace=True)
        self._kick()

    @staticmethod
    def _posting_line(posted: dict[str, Any]) -> str:
        """What the Approve does, from the copy record the card showed (QF-43): its slot, at once, or a dry run."""
        from datetime import datetime

        from . import post_schedule
        if not posted or posted.get("error"):
            return ""
        if posted.get("slot"):
            try:
                return f"Posts {post_schedule.fmt(datetime.fromisoformat(str(posted['slot'])))}."
            except (TypeError, ValueError):
                return ""
        if posted.get("posting") == "dry_run":
            return "Dry run: nothing is posted."
        return "Posts once the Undo window has passed." if float(config.REVIEW_UNDO_MINUTES) > 0 else "Posting now."

    @_serial
    def booked(self, run_id: str) -> None:
        """The Approve booked its slot: the "✅ Approved" item says when it posts, and keeps Undo until 15 minutes
        before it."""
        from datetime import datetime

        from . import notifications as notes
        from . import post_schedule
        from .local_time import show
        booked = [p for p in self.db.list_scheduled_posts(("pending",), run_id=run_id) if p["kind"] == "reel"]
        if not booked:
            return
        try:
            when = post_schedule.fmt(datetime.fromisoformat(booked[0]["due_at"]))
        except (TypeError, ValueError):
            return
        until = self.undo_until(run_id)
        undo = until and until > self.clock()
        notes.post(self.db, notes.INFO, f"posted:{run_id}", "Approved", f"Posts {when}."
                   + (f"\nUndo until {show(until, '%H:%M')}." if undo else ""), emoji="✅",
                   subject=self.db.run_label(run_id), run=run_id, replace=True, ring=False,
                   actions=[{"text": "Undo approval", "callback_data": f"rvua:{self.db.run_key(run_id)}"}] if undo else [])
        self._kick()

    @_serial
    def remind(self, run_id: str, closes: float) -> None:
        """Half an hour into the wait: the reviews item rings once more, "⏰ Review closes 16:40 · Petra" (design
        §6.5: the reminder message is retired)."""
        from . import notice_conditions
        from . import notifications as notes
        from .local_time import show
        notice_conditions.sync(self.db)
        line = notes.clip(f"⏰ Review closes {show(closes, '%H:%M')} · {self.db.run_label(run_id)}", 38)
        with self.db._conn() as conn:
            conn.execute("UPDATE notifications SET push_pending = 1, alert = ? WHERE dedupe_key = ? AND state = 'open'",
                         (line, notice_conditions.PREFIX + "reviews"))   # the alert keeps its ⏰ (design §9)
        self._kick()

    @_serial
    def claimed(self, run_id: str) -> None:
        """The worker acts on the decision now (main.undone_before_acting): an Undo that can no longer work leaves
        the item. A booked Approve keeps it until 15 minutes before its slot (the tick removes it then)."""
        until = self.undo_until(run_id)
        if until and until > self.clock() and (self.db.get_approval(run_id) or {}).get("decision") == "approved":
            from . import publishable
            if (publishable.load_record(self.db, run_id) or {}).get("posting") == "slot":
                return
        self._drop_undo(run_id)

    def _drop_undo(self, run_id: str) -> None:
        from . import notifications as notes
        for prefix in ("posted:", "rejected:"):
            with self.db._conn() as conn:
                row = conn.execute("SELECT * FROM notifications WHERE dedupe_key = ? AND state = 'open' ORDER BY id DESC LIMIT 1",
                                   (prefix + run_id,)).fetchone()
            if not row:
                continue
            item = dict(row)
            actions = [a for a in json.loads(item.get("actions") or "[]") if not str(a.get("callback_data", "")).startswith("rvua:")]
            body = re.sub(r"\n?Undo until [^.]*\.( Then the topic is set aside\.)?", lambda m: " The topic is set aside." if m.group(1) else "",
                          item.get("body") or "").strip()
            notes.post(self.db, item["section"], item["dedupe_key"], item["title"], body, emoji=item.get("emoji") or "",
                       subject=item.get("subject") or "", actions=actions, run=run_id, replace=True, ring=False)
        self._kick()

    @_serial
    def tick(self, now: float | None = None) -> None:
        """The panel's own clock (the renderer's reaper round): a reason step restores itself after 5 minutes, a
        note's time ends, an input expires, a decided item loses its Undo when its time is up, and the pager follows
        decisions made elsewhere."""
        now = self.clock() if now is None else now
        view = self.view()
        changed = False
        if view.get("face") in ("reject", "regen") and now >= float(view.get("face_until") or 0):
            view.update(face=None)
            changed = True
        if view.get("input") and now >= float(view.get("input_until") or 0):
            if view.get("face") == "noted":
                view.update(face=None)
            elif view.get("face") in ("edit", "words"):
                view.update(face=None, notice="Nothing was typed in time; nothing changed.")
            view["input"] = None
            self._disarm()
            changed = True
        if changed:
            self._save(view)
        if self.message() and (changed or view.get("run") not in self.pager(view)):
            self.draw(media=view.get("run") not in self.pager(view))
        from . import notifications as notes
        for item in notes.open_items(self.db):
            key = str(item.get("dedupe_key") or "")
            if key.startswith(("posted:", "rejected:")) and any(str(a.get("callback_data", "")).startswith("rvua:")
                                                                for a in item.get("actions") or []):
                run_id = key.partition(":")[2]
                until = self.undo_until(run_id)
                if not until or now >= until:
                    self._drop_undo(run_id)

    # ---------------------------------------------------------- taps
    @_serial
    def tap(self, cq: dict[str, Any], command_handler=None) -> str | None:
        """A tap on the panel (or a review action from an item). Answers with a toast and draws in place; returns
        the decision it recorded, if any."""
        action, _, rest = str(cq.get("data", "")).partition(":")
        ref, _, arg = rest.partition(":")
        bot = self.bot
        sender = cq.get("from") or {}
        by = f"telegram:{sender.get('id', '')}"
        view = self.view()
        run_id = self.db.resolve_run(ref) if ref else None
        answer: Any = ""
        decision = None
        self._wrote = False
        try:
            if action == "rvshow":
                pager = self.pager(view)
                if not pager:
                    answer = "No review is waiting."
                else:
                    view.update(run=pager[0], tab="sum", sub=1, face=None, notice=None, opened=self.clock())
                    self._save(view)
                    if self.message():
                        self.draw(media=True)
                    else:
                        self._send(view)
                    answer = "The soonest-closing review"
            elif action == "rvua":
                answer = self._undo_item(run_id, command_handler)
            elif not self.message() or not run_id or run_id not in self.pager(view):
                answer = "That review is no longer waiting."
                if self.message():
                    self.draw()
            elif action == "rvp":
                view.update(run=run_id, tab="sum", sub=1, face=None, notice=None, opened=self.clock())
                self._save(view)
                self.draw(media=True)
            elif action == "rvt":
                tab = arg if arg in TABS else "sum"
                if view.get("run") != run_id:
                    view.update(run=run_id)
                if view.get("face") in ("edit", "words"):
                    self._disarm()
                view.update(tab=tab, sub=1, face=None, notice=None, input=None, opened=self.clock())
                self._save(view)
                self.draw()
            elif action == "rvi":
                view.update(sub=int(arg) if arg.isdigit() else 1, opened=self.clock(), notice=None)
                self._save(view)
                self.draw(media=view.get("tab") == "pic")
            elif action == "rvb":
                answer = "A check blocks this version: Checks says which. Edit, Regenerate or Reject it."
            elif action == "rva":
                if blocked(release_of(self.db, run_id)):
                    answer = "A check blocks this version: Checks says which."
                elif self._decide(run_id, "approved", by):
                    decision = "approved"
                    self._after_decision(run_id, "approved")
                    until = self.undo_until(run_id)
                    from .local_time import show
                    answer = "Approved" + (f". Undo from the notifications panel until {show(until, '%H:%M')}." if until else ".")
                    view.update(face=None, notice=None)
                    self._save(view)
                    self.draw(media=True)
                else:
                    answer = "This copy is not verified or changed, or the review has closed. Resend it for review."
            elif action in ("rvr", "rvg"):
                view.update(run=run_id, tab="sum", face="reject" if action == "rvr" else "regen",
                            face_until=self.clock() + REASON_SECONDS, notice=None, opened=self.clock())
                self._save(view)
                self.draw(media=view.get("media") == "photo")
                answer = "Pick a reason" if action == "rvr" else "Pick what to change"
            elif action == "rvk":
                view.update(face=None, notice=None)
                self._save(view)
                self.draw()
                answer = "Kept"
            elif action == "rvs":
                regen = view.get("face") == "regen"
                wanted = "regenerate" if regen else "rejected"
                if view.get("face") not in ("reject", "regen"):
                    answer = "Tap Reject or Regenerate first."
                elif arg not in reason_labels():
                    answer = "That reason is no longer offered."
                elif arg == "other":
                    view.update(face="words", decision=wanted, by=by, notice=None)
                    self._save(view)
                    self._arm("words", INPUT_SECONDS)
                    self.draw()
                    answer = "Type it below and send"
                elif self._decide(run_id, wanted, by):
                    decision = wanted
                    from .improve import record_reason
                    record_reason(self.db, run_id, arg, wanted)
                    self._after_decision(run_id, wanted, arg)
                    from .telegram_bot import REGEN_DETAIL_SECONDS
                    seconds = REGEN_DETAIL_SECONDS if regen else REJECT_NOTE_SECONDS
                    view.update(face="noted", decision=wanted, reason=arg, notice=None)
                    self._save(view)
                    self._arm("note", seconds)
                    self.draw()
                    answer = ("New version started" if regen else "Rejected") + f" ({reason_labels()[arg]})"
                else:
                    answer = "This copy is not verified or changed, or the review has closed. Resend it for review."
            elif action == "rvn":
                if view.get("face") != "regen":
                    answer = "Tap Regenerate first."
                elif self._decide(run_id, "regenerate", by):
                    decision = "regenerate"
                    self._after_decision(run_id, "regenerate")
                    view.update(face=None, notice=None)
                    self._save(view)
                    self.draw(media=True)
                    answer = "New version started"
                else:
                    answer = "This copy is not verified or changed, or the review has closed. Resend it for review."
            elif action == "rvd":
                self._disarm()
                view.update(face=None, input=None, notice=None)
                self._save(view)
                self.draw(media=True)
                answer = "Done"
            elif action == "rvu":
                answer = self._undo_item(run_id, command_handler, on_panel=True)
            elif action == "rve":
                view.update(run=run_id, tab="pst", sub=1, face="edit", notice=None, opened=self.clock())
                self._save(view)
                self._arm("edit", INPUT_SECONDS)
                self.draw(media=view.get("media") == "photo")
                answer = "Type the new caption below and send it"
            elif action == "rvc":
                self._disarm()
                view.update(face=None, input=None, notice=None)
                self._save(view)
                self.draw()
                answer = "Cancelled"
        except Exception as e:  # noqa: BLE001 - a tap always gets its answer
            log.warning("Review panel tap %s failed: %s", action, e)
            answer = "That did not work; try again."
        if callable(answer):   # Posting's cancel draws the dashboard: it runs once the panel's lock is released
            finish = answer

            def cancel_then_answer() -> None:
                try:
                    said = finish()
                except Exception as e:  # noqa: BLE001 - a tap always gets its answer
                    log.warning("Review panel Undo of a booking failed: %s", e)
                    said = "That did not work; try again."
                bot._answer_callback(cq.get("id", ""), said)
            self._later(cancel_then_answer)
        else:
            bot._answer_callback(cq.get("id", ""), answer)
        if self._wrote:
            self._later(self._redraw_notes)   # the item it wrote is drawn now (round 2: a tap redraws)
        return decision

    def _undo_item(self, run_id: str | None, command_handler, on_panel: bool = False) -> Any:
        """Undo from a decided item (or the rejected face): within the Undo window the review is reopened in the
        pager (db.undo_review_decision, UI-02's rules); an Approve whose slot is booked has the booking cancelled
        (Posting's cancel) until 15 minutes before its slot. Returns the answer, or for a booking's cancel a call
        that makes it and returns the answer: tap() runs it once the panel's lock is released (lock order)."""
        from . import notifications as notes
        from .local_time import show
        from .telegram_bot import APPROVAL_TIMEOUT_SECONDS, undo_minutes
        if not run_id:
            return "That review is not known."
        approval = self.db.get_approval(run_id) or {}
        decision = approval.get("decision")
        sizer = getattr(self.bot, "approval_window", None)
        try:
            window = float(sizer()) if callable(sizer) else float(APPROVAL_TIMEOUT_SECONDS)
        except Exception:  # noqa: BLE001 - sized as a review without the coordinator's slot rules
            window = float(APPROVAL_TIMEOUT_SECONDS)
        if decision in ("approved", "rejected") and undo_minutes() > 0 and self.db.undo_review_decision(
                run_id, decision, approval.get("telegram_message_id"), undo_minutes() * 60, window):
            notes.close_key(self.db, f"{'posted' if decision == 'approved' else 'rejected'}:{run_id}", notes.HANDLED)
            view = self.view()
            view.update(run=run_id, tab="sum", sub=1, face=None, input=None, notice=None)
            self._disarm()
            if self.message():
                self._save(view)
                self.draw(media=True)
            else:
                self._send({**view})
            mid = self.message()
            if mid:
                with self.db._conn() as conn:
                    conn.execute("UPDATE approvals SET telegram_message_id=? WHERE run_id=? AND decision='pending'", (mid, run_id))
                self._bind(run_id, mid)
            closes = (self.db.get_approval(run_id) or {}).get("expires_at")
            self._kick()
            return (("Rejection" if decision == "rejected" else "Approval") + " undone. The review is open"
                    + (f" until {show(float(closes), '%H:%M')}." if closes else " again."))
        if decision == "approved":
            booked = [p for p in self.db.list_scheduled_posts(("pending",), run_id=run_id) if p["kind"] == "reel"]
            until = self.undo_until(run_id)
            if booked and until and self.clock() < until and command_handler is not None:
                booking = str(booked[0]["id"])

                def cancel() -> str:
                    reply = command_handler("cancelpost", booking)
                    reply = reply[0] if isinstance(reply, tuple) else reply
                    notes.close_key(self.db, f"posted:{run_id}", notes.HANDLED)
                    self._redraw_notes()
                    return notes.clip(notes.plain(reply or "Booking cancelled."), 190)
                return cancel
            if booked and until and self.clock() >= until:
                self._drop_undo(run_id)
                return "Too late to undo: the slot is less than 15 minutes away. Posting shows what happens next."
        self._drop_undo(run_id)
        return self.bot._undo_refusal(run_id, decision or "approved", True)


def _dims(path: Path) -> dict[str, int]:
    from .telegram_bot import _video_dimensions
    try:
        return dict(_video_dimensions(path) or {})
    except Exception:  # noqa: BLE001 - Telegram works the size out itself
        return {}
