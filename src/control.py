"""
src/control.py
==============
Operations over Telegram: slash commands, the daily heartbeat, and the dead-man's-switch ping.

Commands (approvers only, in the configured chat): the public list, its subcommands and groups are in
src/telegram_commands.py (GROUPS); /help shows them.
"""

import html
import json
import time as clock  # "time" is imported locally inside handle(), which shadows a plain name
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from .config import ASSETS_DIR, CLAUDE_MONTHLY_BUDGET_USD, MEDIA_MONTHLY_BUDGET_USD, HEALTHCHECK_PING_URL, MIN_SCENE_IMAGES, is_configured
from .credits import CONTACT_SHEET, CREDITS_FILE, approve_set, drop_images, load_credits, save_credits, write_contact_sheet
from .db import PipelineDatabase
from . import config
from .topic_store import serialized


def readiness(images: List[Dict[str, Any]], reviewed: bool = True) -> Dict[str, Any]:
    """image_review.readiness, the one rule every screen judges a photo set by (2026-10-28); imported when
    called, as image_review pulls in the model clients."""
    from .image_review import readiness as _readiness
    return _readiness(images, reviewed)

log = logging.getLogger(__name__)

PAUSED_KEY = "scheduler_paused"
COLLAPSE_OVER = 280  # characters: longer text starts collapsed (Telegram's chevron), shorter stays plain


EDIT_OPEN_KEY = "edit_open"   # the review an Edit card was last opened for, and when
EDIT_OPEN_SECONDS = 30 * 60   # a pasted template without a reply counts for that review this long
EDIT_LABELS = (("title", "title"), ("first comment", "first_comment"),
               ("carousel caption", "carousel_caption"), ("caption", "caption"))
COPY_BUTTON_LIMIT = 256  # Telegram's copy_text button holds at most this many characters


def edit_prompt(payload: Dict[str, Any], run_key: str):
    """✏️ Edit text: the current copy as a ready-to-edit template in a code block (Telegram offers Copy
    on it), plus one-tap copy buttons for the short fields. Credit lines are left out: an edited
    caption keeps them automatically. Unchanged fields sent back are ignored."""
    from .credits import is_attribution_line, is_hashtag_line
    def own(text):  # the wording only: hashtags and credits are kept for the operator
        kept = [ln for ln in (text or "").splitlines() if not is_attribution_line(ln) and not is_hashtag_line(ln)]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    fields = [(label, own(payload.get(key)) if "caption" in key else (payload.get(key) or "").strip())
              for label, key in EDIT_LABELS]
    template = "\n\n".join(f"{label}: {value}" for label, value in fields if value)  # a blank line between fields
    # A heading and short lines, not one nine-sentence paragraph (operator, 2026-10-23 UI review).
    intro = ("✏️ <b>Edit text</b>\n\n"
             "Copy the block below, change what you want and send it back: as a reply to this message, "
             "or pasted into the chat within 30 minutes.\n\n"
             "• Unchanged fields are ignored: send the whole block or only the lines you change.\n"
             "• Each field starts with its label; a caption can run over several lines.\n"
             "• A blank field (<code>title:</code> alone) is ignored, never emptied.\n"
             "• Hashtags and credit lines are kept (add a hashtag line to replace the hashtags).\n"
             "• The fact check runs again before the card updates.\n"
             "• Narration and on-screen text are part of the video: use Regenerate for those.")
    trailer = f"\n[edit run {html.escape(run_key)}]"
    block = f"\n\n<pre><code class=\"language-text\">{html.escape(template)}</code></pre>" if template else ""
    if len(intro) + len(template) + len(trailer) > 3900:  # Telegram's 4,096-character message limit
        short = "\n".join(f"{label}: {value}" for label, value in fields if value and "caption" not in label)
        block = (f"\n\n<pre><code class=\"language-text\">{html.escape(short)}</code></pre>"
                 f"\n<b>Caption</b> (too long to copy here as a block):\n{collapsible(dict(fields).get('caption', ''), 0)}")
    buttons = [[{"text": f"📎 Copy {label}", "copy_text": {"text": value}}]
               for label, value in fields if value and label in ("title", "first comment") and len(value) <= COPY_BUTTON_LIMIT]
    return (intro + block + trailer, buttons) if buttons else intro + block + trailer


def collapsible(text: str, over: int = COLLAPSE_OVER) -> str:
    """Escaped text, in an expandable block once it is long: a card stays short until opened."""
    escaped = html.escape(text or "")
    return f"<blockquote expandable>{escaped}</blockquote>" if len(text or "") > over else escaped
# Set by the production launcher: where /deploy leaves its request for the release deployer, and
# where the deployer reports what it is doing.
DEPLOY_REQUEST_ENV = "RELIC_DEPLOY_REQUEST"
DEPLOY_STATUS_ENV = "RELIC_DEPLOY_STATUS"


def request_deploy(db: PipelineDatabase) -> str:
    """/deploy: asks the release deployer to look for a signed release now instead of at its next
    2-minute check. It still installs only a signed release, only when production is idle, and only
    after the tests pass on this machine."""
    import os
    target = os.environ.get(DEPLOY_REQUEST_ENV)
    if not target:
        return "🚀 Automatic deployment runs on production only; this bot installs nothing."
    Path(target).write_text(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "by": "telegram"}), encoding="utf-8")
    text = ("🚀 <b>Deployment check requested.</b> The deployer looks for a new signed release within a minute. "
            "It installs one only when nothing is running, reviewing or due, and only after its tests pass here. "
            "While it installs, this bot is quiet for about five minutes; I'll report when it is back on the release.")
    try:
        status = json.loads(Path(os.environ.get(DEPLOY_STATUS_ENV, "")).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return text
    try:
        from .release_info import release_label
        running = release_label()
    except Exception:
        running = None
    tag = str(status.get("Tag") or "")
    if tag and running and running == tag:
        # A /deploy tapped during the install is answered by the daemon that just started on it: the status
        # file still said "Installing rc43" (operator, 2026-10-25: "a false notification"). Say what is true.
        text += f"\n\n✅ <b>{html.escape(tag)} is installed and running.</b> Nothing newer is waiting to install."
    else:
        text += f"\n\nLast deployer status: {html.escape(str(status.get('Status', ''))[:400])}"
    return text


DEPLOY_NOTICE_KEY = "deploy_notice"  # the last "tag:phase" said, so each is said once


def deployer_notice(db: PipelineDatabase) -> Optional[str]:
    """What the release deployer is about to do, from its status file, said once per release and phase:
    a release waiting to install, or installing now. The operator found the bot silent for five minutes
    with no warning (2026-10-24: taps during the rc20 install went unanswered). None when nothing is new."""
    import os
    path = os.environ.get(DEPLOY_STATUS_ENV, "")
    if not path:
        return None
    try:
        status = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    text, tag = str(status.get("Status", "")), html.escape(str(status.get("Tag", "")))
    if not tag:
        return None
    try:  # the status outlives the install: a daemon just restarted on the release must not announce it
        from .release_info import release_label
        if release_label() == str(status.get("Tag", "")):
            db.set_setting(DEPLOY_NOTICE_KEY, f"{tag}:installed")
            return None
    except Exception:
        pass
    if text.startswith("Installing "):
        phase, notice = "installing", (f"⏸ <b>Installing release {tag}.</b> The bot is quiet for about five minutes "
                                       "while the release is checked out and tested; it reports when it is back.")
    elif re.match(r"^\S+ is ready", text):
        phase, notice = "ready", (f"📦 <b>Release {tag} is ready to install.</b> It installs when nothing is running or "
                                  "waiting for a decision; the bot is then quiet for about five minutes and reports "
                                  "when it is back on the release.")
    else:
        return None
    key = f"{tag}:{phase}"
    if db.get_setting(DEPLOY_NOTICE_KEY) == key:
        return None
    db.set_setting(DEPLOY_NOTICE_KEY, key)
    return notice

SendPhoto = Callable[[Path, str, Optional[list]], bool]
from .telegram_ui import HOME
from .telegram_ui import hourglass  # 🏠 Dashboard, the one home button of every screen (2026-10-25 overhaul)
from .local_time import show as show_time


def running_job(db, resource: str):
    """When a job for this resource is running in this process (a record left by a stopped process is not
    running), its start time for the operator; else None."""
    import os
    with db._conn() as conn:
        for (value,) in conn.execute("SELECT value FROM settings WHERE key LIKE 'operator_job:%'"):
            job = json.loads(value)
            if job.get("resource") == resource and job.get("state") in ("queued", "running") and job.get("owner_pid") == os.getpid():
                return show_time(job.get("updated_at"), "%H:%M")
    return None


def job_button(db, resource: str, label: str, running: str = "⏳ Fetching… · Jobs") -> Dict[str, str]:
    """A button that starts a job, or, while that job runs, says so and opens Jobs (as Render does):
    a second tap on "Fetch more" looked like nothing happened, or was refused as a duplicate."""
    if running_job(db, resource):
        return {"text": running, "callback_data": "jobs:"}
    return {"text": label, "callback_data": resource}


def short_reason(error: str, limit: int = 280) -> str:
    """A failure reason for the dashboard: no exception class name, and cut at a word, never mid-word."""
    text = re.sub(r"^[A-Z]\w*(Error|Exception):\s*", "", (error or "").strip())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def pager(prefix: str, page: int, pages: int) -> list:
    """⏮ first, ◀ previous, ▶ next, ⏭ last for a paged panel; a button that would not move is left out."""
    row = []
    if page > 1:
        row.append({"text": "⏮", "callback_data": f"{prefix}0"})
    if page > 0:
        row.append({"text": "◀", "callback_data": f"{prefix}{page - 1}"})
    if page < pages - 1:
        row.append({"text": "▶", "callback_data": f"{prefix}{page + 1}"})
    if page < pages - 2:
        row.append({"text": "⏭", "callback_data": f"{prefix}{pages - 1}"})
    return [row] if row else []
from .telegram_commands import help_text
HELP = help_text()

# Plain-language names for run statuses in Telegram.
STATUS_WORDS = {
    "producing": "being made", "pending_approval": "waiting for you", "approved": "approved",
    "published": "published", "simulated": "test run (not posted)", "rejected": "rejected",
    "regenerate": "regenerated", "timeout": "no decision in time", "failed": "failed",
    "cancelled": "cancelled by operator", "publish_unknown": "not sure it posted", "scheduled": "scheduled to post",
}


def _fetched_sets(assets_dir: Path) -> List[Path]:
    root = assets_dir.resolve()
    return sorted(d for d in assets_dir.iterdir() if d.is_dir() and d.resolve().parent == root
                  and (d / CREDITS_FILE).exists()) if assets_dir.is_dir() else []


def _resolve_topic_dir(ref: str, assets_dir: Path, db: Optional[PipelineDatabase] = None) -> Path:
    """Resolve only a single known folder inside the canonical asset root."""
    from pathlib import PureWindowsPath
    ref = ref.strip()
    if not ref or ref in (".", "..") or any(c in ref for c in "/\\:") or PureWindowsPath(ref).drive:
        raise ValueError("Use a topic number or ID from /photos")
    if db is not None and ref.isdigit():
        topics = db.list_all_topics()
        if not 1 <= int(ref) <= len(topics):
            raise ValueError("Unknown topic number")
        ref = topics[int(ref)-1]["id"]
        candidate = assets_dir / ref
        if candidate.resolve().parent != assets_dir.resolve() or not (candidate / CREDITS_FILE).is_file():
            raise ValueError("This topic has no fetched images; open /photos to fetch or upload")
        return candidate
    matches = [d for d in _fetched_sets(assets_dir) if d.name.lower() == ref.lower()
               or (not ref.isdigit() and d.name.split("_",1)[-1] == ref)]
    if db is None and ref.isdigit():
        matches = [d for d in _fetched_sets(assets_dir) if d.name.split("_")[0] in (ref, f"{int(ref):02d}")]
        if not matches:
            sets = _fetched_sets(assets_dir)
            matches = sets[int(ref)-1:int(ref)] if int(ref)>0 else []
    if len(matches) != 1:
        raise ValueError("No unique fetched image set; open /photos")
    return matches[0]


@serialized
def _add_topic_to_bank(db: PipelineDatabase, title: str, u_site=None) -> Tuple[str, str, Dict[str, Any]]:
    from . import topic_proposer
    bank = topic_proposer._bank()
    existing_titles = [t["title"] for t in bank.get("topics", [])]
    dup = topic_proposer.near_duplicate(title, existing_titles)
    if dup:
        raise ValueError(f"Duplicate detected! Matches existing topic: {dup}")
    slug = topic_proposer._slugify(title)
    topic_id = slug

    from .unesco import UNESCOEngine
    unesco_engine = UNESCOEngine()
    if not u_site:
        u_site = unesco_engine.find_site(title)
    unesco_desc = None
    loc_country = "Global"
    loc_lat, loc_lon = 0.0, 0.0
    u_tag = ""
    if u_site:
        unesco_desc = f"{u_site.site} (UNESCO #{u_site.id_number}): {u_site.short_description or u_site.justification}"
        loc_country = u_site.states or loc_country
        loc_lat = u_site.latitude or 0.0
        loc_lon = u_site.longitude or 0.0
        u_tag = f"\n🏛️ <b>UNESCO Verified:</b> #{u_site.id_number} ({html.escape(u_site.site)})"

    new_topic = {
        "id": topic_id,
        "title": title,
        "sub_theme": "Historical Curiosities & Ancient Marvels",
        "hook": f"What really lies within {title}?",
        # latitude/longitude: the keys every reader uses (lat/lon here were never read).
        "location": {"name": title, "country": loc_country, "latitude": loc_lat, "longitude": loc_lon},
        "key_facts": [
            f"Documented historical site: {title}",
            "Architectural or archaeological significance recognized by researchers.",
            "Subject of ongoing academic and field study."
        ],
        "meta_categories": ["History & Philosophy", "Travel & Culture"],
        "hashtags": [f"#{slug[:20].replace('_', '')}", "#ancienthistory", "#archaeology", "#relicandriddle"],
        "sources": [{"title": title, "url": f"https://en.wikipedia.org/wiki/{slug}"}],
        "added": topic_proposer.added_record("operator", "added by title in Telegram"
                                             + (f" (UNESCO #{u_site.id_number})" if u_site else "")),
    }
    bank["topics"].append(new_topic)
    bank["total"] = len(bank["topics"])

    from .shot_archetypes import decompose_topic_visuals
    decomp = decompose_topic_visuals(
        title,
        context_facts=new_topic["key_facts"],
        unesco_context=unesco_desc,
    )
    queries = json.loads(topic_proposer.QUERIES.read_text(encoding="utf-8"))
    queries[topic_id] = {
        "queries": decomp["queries"],
        "keywords": decomp["keywords"],
        "archetypes": decomp.get("archetypes", {})
    }
    from .topic_store import commit
    commit(topic_proposer.BANK, topic_proposer.QUERIES, bank, queries)
    db._seed_topics_from_json()
    return topic_id, u_tag, decomp


def images_text(assets_dir: Path = ASSETS_DIR, db: Optional[PipelineDatabase] = None) -> str:
    review, short, approved = [], [], 0
    all_topics = db.list_all_topics() if db else []
    topic_order = {t["id"]: i for i, t in enumerate(all_topics, 1)}
    topic_titles = {t["id"]: t.get("title", "") for t in all_topics}

    def _set_sort_key(d: Path) -> tuple:
        parts = d.name.split("_", 1)
        if len(parts) > 1 and parts[0].isdigit():
            return (0, int(parts[0]), d.name)
        if d.name in topic_order:
            return (0, topic_order[d.name], d.name)
        return (1, 999999, d.name)

    sets = sorted(_fetched_sets(assets_dir), key=_set_sort_key)

    for d in sets:
        cr = load_credits(d)
        n = len(cr["images"])
        parts = d.name.split("_", 1)
        if len(parts) > 1 and parts[0].isdigit():
            label = f"{parts[0]} — {html.escape(parts[1].replace('_', ' '))} ({n})"
        elif d.name in topic_order:
            rank = topic_order[d.name]
            title = topic_titles.get(d.name) or d.name.replace("_", " ")
            label = f"{rank}. {html.escape(title)} · {n} photo{'s' if n != 1 else ''}"   # its name, not its id
        else:
            label = f"{html.escape(d.name.replace('_', ' '))} ({n})"
        # Counted as the video counts (image_review.readiness): an approved set of 2 Topic + 4 Context showed
        # as approved here and failed at run start (production 2026-10-28).
        r = readiness(cr["images"], bool(cr["reviewed"]))
        if r["ready"] or r["pending"]:
            approved += 1
        elif cr["reviewed"]:
            short.append(f"{label} · {r['usable']} of {MIN_SCENE_IMAGES} usable")
        elif n >= MIN_SCENE_IMAGES:
            review.append(label)
        else:
            short.append(label)
    lines = [f"<b>Photo sets</b> · {approved} approved · {len(review)} to review · {len(short)} short"]
    if review:
        lines += ["", "<b>To review</b> · <code>/photos N review</code>"] + shown_and_more(review)
    if short:
        lines += ["", f"<b>Short of {MIN_SCENE_IMAGES} photos a video can use</b> · <code>/photos N fetch</code> or upload"] + shown_and_more(short)
    return "\n".join(lines)


SECTION_SHOWN = 5


def shown_and_more(items: List[str], shown: int = SECTION_SHOWN, where: str = "Photo sets") -> List[str]:
    """A section's first `shown` lines, then '…and N more in <where>': the one way a screen says it holds more."""
    out = [f"  • {x}" for x in items[:shown]]
    if len(items) > shown:
        out.append(f"  <i>…and {len(items) - shown} more in {html.escape(where)}</i>")
    return out


COVER_VARIANTS = {"a": "thumbnail_variant_a_blueprint.jpg", "b": "thumbnail_variant_b_intrigue.jpg",
                  "c": "thumbnail_variant_c_minimal.jpg"}


def chosen_cover(run_dir: Path) -> Optional[str]:
    """Which variant the run's cover (thumbnail_cover.jpg) is: the same file, or else the closest picture."""
    cover = run_dir / "thumbnail_cover.jpg"
    if not cover.is_file():
        return None
    from .credits import sha256_file
    digest = sha256_file(cover)
    found = {v: run_dir / f for v, f in COVER_VARIANTS.items() if (run_dir / f).is_file()}
    for v, path in found.items():
        if sha256_file(path) == digest:
            return v
    try:
        import numpy as np
        from PIL import Image
        small = lambda path: np.asarray(Image.open(path).convert("L").resize((36, 64)), dtype=np.float32)  # noqa: E731
        target = small(cover)
        scores = {v: float(np.abs(small(path) - target).mean()) for v, path in found.items()}
        best = min(scores, key=scores.get) if scores else None
        return best if best and scores[best] < 12 else None
    except Exception:
        return None


def review_caption(topic_dir: Path, ref_hint: Optional[str] = None, title: Optional[str] = None) -> str:
    """The sheet's caption: what the set holds and which photos a video may use (operator, 2026-10-25: weak
    photos reached the video, and the old storyboard list repeated one caption under keyword picks)."""
    from .image_review import CONTEXT_SHOTS_MAX, TIER_LABELS, readiness_text, tier, usable_photos
    cr = load_credits(topic_dir)
    images = cr["images"]
    r = readiness(images, bool(cr.get("reviewed")))
    status_tag = (" (approved)" if r["ready"] or r["pending"] else " (approved, short for a video)") if cr.get("reviewed") else ""
    name = title or topic_dir.name.replace("_", " ").capitalize()   # the topic's title, not its folder id
    lines = [f"🖼 <b>{html.escape(name)}</b> · {len(images)} photos{status_tag}"]
    drop_ref = ref_hint if ref_hint else (topic_dir.name.split('_')[0] if topic_dir.name.split('_')[0].isdigit() else topic_dir.name)
    # First, so the 1,024-character photo caption never cuts it off; the Copy buttons fill the topic in.
    lines.append(f"<i>Edit with the numbers on the sheet: /photos {html.escape(drop_ref)} drop &lt;numbers&gt; · "
                 f"/photos {html.escape(drop_ref)} keep &lt;numbers&gt;</i>")
    counts: Dict[str, int] = {}
    for c in images:
        counts[tier(c)] = counts.get(tier(c), 0) + 1
    lines.append("\n" + " · ".join(f"{TIER_LABELS[t]} {counts[t]}" for t in ("topic", "kept", "context", "unchecked", "weak")
                                   if counts.get(t)))
    strong = counts.get("topic", 0) + counts.get("kept", 0)
    # One fact a line, counted in words ("A video uses the 0 Topic and Kept photos … (1 in all)": review, 2026-10-25)
    usable = len(usable_photos(images))
    lines.append(f"A video can use {usable} of them: " + (
        f"{strong} Topic or Kept photo{'s' if strong != 1 else ''}, plus up to {CONTEXT_SHOTS_MAX} Context ones."
        if strong else f"no Topic or Kept photo yet, so only up to {CONTEXT_SHOTS_MAX} Context ones."))
    # The count the run checks, and what is short (production 2026-10-28: "2 Topic, 4 Context" failed at start).
    lines.append(("Ready for a video: " if r["usable"] >= MIN_SCENE_IMAGES else f"Short for a video (need {MIN_SCENE_IMAGES}): ")
                 + html.escape(readiness_text(images)) + ".")
    lines.append("Stories and the carousel use Topic and Kept photos only.")
    lines.append("Unchecked photos are looked at before a run; Weak ones are never used "
                 "(<code>/photos N keep</code> puts one in anyway).")

    def _int(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0
    sharp = sum(1 for c in images if min(_int(c.get("width")), _int(c.get("height"))) >= 1080)
    from .credits import needs_attribution  # a CC BY / BY-SA photo is credited even when its source left the flag unset
    credit = sum(1 for c in images if needs_attribution(c))
    lines.append(f"Quality: {sharp} sharp enough as they are, {len(images) - sharp} to be AI-upscaled · "
                 f"licences: {len(images) - credit} need no credit, {credit} credited in the caption")
    return "\n".join(lines)


def is_paused(db: PipelineDatabase) -> bool:
    return db.get_setting(PAUSED_KEY, "false") == "true"


def ping_healthcheck(suffix: str = "") -> None:
    """Pings the dead-man's-switch URL (suffix '/fail' reports a failure). Never raises."""
    if not is_configured(HEALTHCHECK_PING_URL):
        return
    try:
        requests.get(HEALTHCHECK_PING_URL.rstrip("/") + suffix, timeout=10)
    except requests.RequestException as e:
        log.warning("Healthcheck ping failed: %s", e)


SCRIPT_CHARS = 750  # a 45-second narration is ~650-750 characters


def check_azure_speech() -> dict:
    from .config import AZURE_SPEECH_KEY, AZURE_SPEECH_REGION, is_configured
    if not is_configured(AZURE_SPEECH_KEY):
        return {"configured": False, "ok": False, "status": "Not configured"}
    region = AZURE_SPEECH_REGION if is_configured(AZURE_SPEECH_REGION) else "eastus"
    url = f"https://{region}.api.cognitive.microsoft.com/sts/v1.0/issueToken"
    try:
        r = requests.post(url, headers={"Ocp-Apim-Subscription-Key": AZURE_SPEECH_KEY}, timeout=3)
        if r.status_code == 200:
            return {"configured": True, "ok": True, "region": region, "status": f"Ready (licensed fallback · {region})"}
        elif r.status_code == 401:
            return {"configured": True, "ok": False, "region": region, "status": f"❌ 401 Auth Error (invalid key or wrong region: {region})"}
        else:
            return {"configured": True, "ok": False, "region": region, "status": f"⚠️ HTTP {r.status_code} ({region})"}
    except Exception:
        return {"configured": True, "ok": False, "region": region, "status": f"⚠️ Probe failed ({region})"}


def quota_lines(voice_quota: Optional[dict]) -> List[str]:
    """Voice quota and provider fallback summary."""
    lines = []
    from .config import AZURE_SPEECH_KEY, is_configured

    has_azure = is_configured(AZURE_SPEECH_KEY)
    az = check_azure_speech() if has_azure else {"configured": False, "ok": False}

    if not voice_quota or not voice_quota.get("limit"):
        if az.get("configured"):
            lines.append(f"Azure Speech: {az['status']}")
            if not az.get("ok"):
                lines.append("🚨 <b>Voice alert:</b> Azure Speech has auth errors and ElevenLabs is unconfigured. " + _sentence(_unlicensed_consequence()))
        return lines

    left = max(0, voice_quota["limit"] - voice_quota["used"])
    reset = ""
    if voice_quota.get("resets"):
        reset = ", resets " + show_time(int(voice_quota["resets"]), "%d %b")
    line = (f"ElevenLabs ({html.escape(str(voice_quota.get('tier')))}): {left:,} of {voice_quota['limit']:,} "
            f"characters left (~{left // SCRIPT_CHARS} Reels){reset}")

    if left < 3 * SCRIPT_CHARS:
        if az.get("ok"):
            lines.append(f"⚠️ {line}. Falls back to Azure Speech ({az.get('region')}) when exhausted.")
        else:
            lines.append(f"⚠️ {line}. When it runs out: " + _unlicensed_consequence())
    else:
        lines.append(line)

    if az.get("configured"):
        lines.append(f"Azure Speech: {az['status']}")
        if left < 3 * SCRIPT_CHARS and not az.get("ok"):
            lines.append("🚨 <b>Licence alert:</b> both ElevenLabs and Azure Speech are unavailable. " + _sentence(_unlicensed_consequence()))

    return lines


AUDIT_NAMES = {"cdash": "Dashboard", "approve": "Approve", "reject": "Reject", "regen": "Regenerate",
               "cancel": "Cancel review", "rsn": "Reason", "cprog": "Refresh progress", "qrender": "Render",
               "qdoc": "Render with documentary", "qrefresh": "Refresh queue", "qbump": "Bump", "qdrop": "Drop",
               "resend": "Resend review", "requeue": "Back to the queue", "rerender": "Re-render", "postreal": "Post for real?", "promote": "Post for real",
               "cdigest": "Weekly digest", "chistory": "Review history", "chist": "Review details", "postnow": "Post now", "produce": "Produce a batch",
               "cposts": "Posting", "cpost": "Posting: one episode", "posts": "Posting", "cancelpost": "Cancel a booking", "movepost": "Move a booking",
               "retrypost": "Retry the failed platform", "retryreel": "Retry the Reel's failed platform", "fbcaptions": "Add captions on Facebook", "fbcover": "Set the Facebook cover", "ytshort": "Post the YouTube Short", "rebook": "Book Stories and carousel again", "ytthumb": "Set the YouTube thumbnail",
               "edit": "Edit text", "runstop": "Cancel run", "runstopok": "Confirm cancel",
               "simpub": "Simulated publish", "pubok": "It posted", "pubno": "It didn't post", "creviews": "Reviews",
               "cideas": "Topic ideas", "cdisc": "UNESCO marvels", "cprop": "Waiting ideas", "cpropose": "Research ideas",
               "cimages": "Images", "cmusic": "Music", "cstat": "Full status", "jobs": "Jobs", "jobcancel": "Cancel job",
               "uadd": "Add marvel", "uskip": "Skip marvel", "tadd": "Add idea", "tskip": "Skip idea",
               "cpause": "Pause", "cresume": "Resume", "caccounts": "Check accounts", "cov": "Cover", "covlock": "Lock cover"}


def audit_action_name(payload: str) -> str:
    """A button's or command's plain name for the audit log ("" when unknown or already readable)."""
    if payload.startswith("/"):
        return ""
    # A screen's name comes from telegram_ui.SCREENS, the one table of names (these said "Full status" and
    # "Images", and Alerts, Audience or Help had none: review, 2026-10-25); actions keep their own.
    from .telegram_ui import SCREENS
    key = payload.partition(":")[0]
    return SCREENS[key][1] if key in SCREENS else AUDIT_NAMES.get(key, "")


def clip_words(text: str, limit: int) -> str:
    """Text cut to `limit` at a word boundary, with an ellipsis ("…(Catherine " and "resolv" were cut
    mid-word: 2026-10-23 UI review)."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:·(—-")
    return (cut or text[:limit - 1]) + "…"


def button_run_name(db, run_id: str, limit: int = 26) -> str:
    """A run named so that it fits on a button whole: its place (or a short title) and its time, which tells
    two runs of one topic apart. The full label was cut to '"The Antikythera…' (operator, 2026-10-25)."""
    run = db.get_run(run_id) or {}
    topic = db.get_topic_by_id(run.get("topic_id", "")) or {}
    name = (topic.get("location") or {}).get("name") or (run.get("script_payload") or {}).get("title") \
        or topic.get("title") or run_id
    when = ""
    try:
        from .local_time import local
        when = local(run["created_at"]).strftime("%H:%M")
    except (KeyError, TypeError, ValueError):
        pass
    return clip_words(name.strip('"'), limit - (len(when) + 1 if when else 0)) + (f" {when}" if when else "")


def _sentence(text: str) -> str:
    """Capitalised as a sentence start ("… unconfigured. real runs stop" read as a typo)."""
    return text[:1].upper() + text[1:] if text else text


def _unlicensed_consequence() -> str:
    from . import config
    return ("real runs stop (a licensed voice is required); dry runs use the free test voice."
            if config.VOICE_REQUIRE_LICENSED else "runs use the free edge-tts voice, which is not licensed for monetized posts.")


def spend_lines(db: PipelineDatabase) -> List[str]:
    """This month's spend by what it is, against each cap."""
    from . import config
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    by_task = {r["task"]: r["cost"] for r in db.llm_spend_by_task(month)}
    light, heavy = by_task.pop("paid-fallback:light", 0.0), by_task.pop("paid-fallback:heavy", 0.0)
    lines = [f"AI on the billed key this month: light ${light:.2f} of ${config.PAID_FALLBACK_LIGHT_MONTHLY_USD:.2f}, "
             f"heavy ${heavy:.2f} of ${config.PAID_FALLBACK_HEAVY_MONTHLY_USD:.2f}"]
    claude = sum(c for task, c in by_task.items() if not task.startswith(("paid-fallback", "groq")))
    if claude or config.SCRIPT_ENGINE == "claude":
        lines.append(f"Claude this month: ${claude:.2f}" + (f" of ${CLAUDE_MONTHLY_BUDGET_USD:.2f}" if CLAUDE_MONTHLY_BUDGET_USD > 0 else ""))
    media = db.media_spend(month)
    lines.append(f"AI motion this month: ${media:.2f} of ${MEDIA_MONTHLY_BUDGET_USD:.2f}")
    return lines


def spend_summary(db: PipelineDatabase) -> str:
    """The same spend as one dashboard line (three unmarked lines read as loose text: 2026-10-23 UI
    review). Full status keeps one line per budget."""
    parts = [re.sub(r" this month: ", " ", line).replace(" of $", "/$") for line in spend_lines(db)]
    parts = [p.replace("AI on the billed key ", "billed AI ") for p in parts]
    return "💳 This month · " + " · ".join(parts)


REACHED_REVIEW = {"pending_approval", "approved", "rejected", "regenerate", "timeout", "simulated", "published",
                  "scheduled", "publish_unknown", "publishing"}
RERENDER_HOURS = 48  # a late failure older than this is not offered on the dashboard (Render it afresh)


def _worth_resuming(run: Dict[str, Any]) -> bool:
    from .resume_points import worth_resuming
    return worth_resuming(config.OUTPUT_DIR / run["run_id"], run)


def rerenderable(db: PipelineDatabase) -> Optional[Dict[str, Any]]:
    """The newest recent run that failed after its video was made and whose topic has not been made again
    since: the one Re-render on the dashboard offers."""
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(hours=RERENDER_HOURS)).isoformat()
    made_since: set = set()
    tried_since: set = set()  # topics with any later run at all
    for r in db.list_recent_runs(limit=10):
        if (r.get("updated_at") or r.get("created_at") or "") < since:
            break
        if r.get("topic_id") not in made_since and (
                (r.get("status") == "rejected" and r.get("master_video_path"))
                # sent back with Regenerate and no new version followed (production 2026-10-25)
                or (r.get("status") == "regenerate" and r.get("master_video_path") and r.get("topic_id") not in tried_since)
                or (r.get("status") == "failed" and _worth_resuming(r))):  # src/resume_points.py
            return r
        if r.get("status") in REACHED_REVIEW:
            made_since.add(r.get("topic_id"))
        tried_since.add(r.get("topic_id"))
    return None


def health_line(db: PipelineDatabase, days: int = 7) -> str:
    """How the system has been doing, on one dashboard line: runs, how many reached review, failures,
    the median length of finished runs, and what the post-run self-audit found (src/card_ledger.py).
    The operator had to ask for every one of these (2026-10-24)."""
    import json as _json
    import statistics
    from datetime import datetime, timedelta, timezone
    from . import card_ledger, improve
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    runs = [r for r in db.list_recent_runs(limit=80) if (r.get("created_at") or "") >= since]
    reached = sum(1 for r in runs if r.get("status") in REACHED_REVIEW)
    failed = sum(1 for r in runs if r.get("status") == "failed")
    lengths = []
    for r in runs:
        try:
            timings = _json.loads((config.OUTPUT_DIR / r["run_id"] / "timings.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        seconds = card_ledger.run_seconds(timings)
        if seconds:
            lengths.append(seconds / 60)
    try:
        improve.ensure_schema(db)
        with db._conn() as conn:
            audit = conn.execute("SELECT COUNT(*) FROM findings WHERE source = 'self_audit' AND created_at >= ?",
                                 (since,)).fetchone()[0]
    except Exception:
        audit = 0
    text = f"🩺 Last {days} days · {len(runs)} run(s) · {reached} reached review · {failed} failed"
    if lengths:
        text += f" · median {statistics.median(lengths):.0f} min"
    text += f" · self-audit: {audit} finding(s)" if audit else " · self-audit clean"
    return text


_READY_CACHE: Dict[str, Tuple[float, bool]] = {}  # credits.json path -> (mtime, ready)


def ready_pools(db: PipelineDatabase, topics: List[Dict[str, Any]]) -> int:
    """Queued topics whose photo pool passes the same readiness rule production uses. Each set's answer is
    kept until its credits.json changes: the dashboard read and parsed every set on every tap (2026-10-25)."""
    from .asset_pipeline import pool_ready
    n = 0
    for t in topics:
        try:
            credits = ASSETS_DIR / t["id"] / "credits.json"
            key, stamp = str(credits), (credits.stat().st_mtime if credits.is_file() else -1.0)
            cached = _READY_CACHE.get(key)
            if cached is None or cached[0] != stamp:
                cached = _READY_CACHE[key] = (stamp, bool(pool_ready(t["id"])))
            n += cached[1]
        except Exception:
            pass
    return n


def status_text(db: PipelineDatabase, next_run: Optional[str], voice_quota: Optional[dict] = None) -> str:
    e = html.escape
    lines = ["📊 <b>Status</b>", f"{'⏸ Schedule paused' if is_paused(db) else '▶️ Schedule active'}", "", "<b>Production</b>",
             f"• Next run: {e(next_run or 'not scheduled')}"]
    try:
        nxt = db.get_next_topic()
        if nxt:
            serial = {t["id"]: i for i, t in enumerate(db.list_all_topics(), 1)}.get(nxt["id"], 1)
            lines.append(f"• Next topic: <b>{serial}. {e(nxt['title'])}</b>")
    except Exception:
        pass
    pending = db.list_pending_approvals()
    lines.append(f"• Waiting for your review: {len(pending)}")
    for p in pending[:3]:
        lines.append(f"   ◦ {e(db.run_label(p['run_id']))}")
    if len(pending) > 3:  # every capped list says so (review, 2026-10-25: 6 waiting, 3 listed)
        lines.append(f"   <i>…and {len(pending) - 3} more in Reviews</i>")
    upcoming = db.list_scheduled_posts(("pending", "waiting"))
    if upcoming:
        from .post_schedule import fmt
        from .posting_view import carousel_plan
        plan = carousel_plan(db)
        names = {"reel": "Reel", "story": "Stories", "carousel": "Carousel"}
        lines.append("• Upcoming posts:")
        for p in upcoming[:6]:
            slot = plan.get(p["id"]) if not p["due_at"] else None   # a carousel's real slot, as Posting shows it
            when = fmt(datetime.fromisoformat(p["due_at"])) if p["due_at"] else (fmt(slot) if slot else "after its Reel")
            lines.append(f"   ◦ {names.get(p['kind'], p['kind'])} {e(when)} — {e(db.run_label(p['run_id']))}")
        if len(upcoming) > 6:
            lines.append(f"   <i>…and {len(upcoming) - 6} more in Posting</i>")

    icons = {"failed": "❌", "simulated": "🧪", "published": "✅", "pending_approval": "🎬", "producing": "⏳",
             "rejected": "✖", "approved": "👍", "scheduled": "🗓", "cancelled": "⏹", "regenerate": "🔄",
             "timeout": "⌛", "publish_unknown": "⚠️"}  # every status an icon (Regenerated had a bare bullet)
    recent = db.list_recent_runs(5)
    lines += ["", "<b>Recent runs</b>"]
    for r in recent:
        word = STATUS_WORDS.get(r["status"], r["status"])
        lines.append(f"{icons.get(r['status'], '•')} {e(word[:1].upper() + word[1:])} · {e(db.run_label(r['run_id']))}")
    if not recent:
        lines.append("None yet.")

    topics = db.list_all_topics()
    published = sum(1 for t in topics if t.get("times_published", 0) > 0)
    queued = sum(1 for t in topics if t.get("status") == "queued")
    lines += ["", f"<b>Topics</b> · {queued} queued · {published} published · {len(topics)} total"]

    lines += ["", "<b>Budgets and voices this month</b>"]
    lines += [f"• {line.replace(' this month', '')}"
              for line in spend_lines(db) + quota_lines(voice_quota)]

    from . import accounts
    lines += ["", "<b>Accounts and issues</b>", "• " + accounts.status_line(db).replace("Publishing accounts: ", "Publishing: ")]
    try:
        from . import improve
        improve.sync_issue_files(db)
        still_open, fixed = len(db.list_open_issues()), db.count_issues("fixed")
        lines.append(f"• Issues: ⚠️ {still_open} open (see /improve)" + (f" · {fixed} fixed" if fixed else "") if still_open
                     else "• Issues: ✅ none open" + (f" · {fixed} fixed" if fixed else ""))
    except Exception as exc:
        log.debug("Issue counts unavailable: %s", exc)
    return "\n".join(lines)


def heartbeat_text(db: PipelineDatabase, next_run: Optional[str], voice_quota: Optional[dict] = None) -> str:
    return "💓 <b>Daily heartbeat</b> — the daemon is running.\n" + status_text(db, next_run, voice_quota)


LAST_REVIEW_KEY = "last_review_outcome"
LAST_REVIEW_HOURS = 12


def last_review_outcome(db: PipelineDatabase) -> Optional[str]:
    """The latest decided review's receipt (HTML), for the dashboard, for LAST_REVIEW_HOURS."""
    try:
        record = json.loads(db.get_setting(LAST_REVIEW_KEY) or "{}")
    except ValueError:
        return None
    if not record.get("text") or clock.time() - float(record.get("at") or 0) > LAST_REVIEW_HOURS * 3600:
        return None
    return f"🧾 <b>Last review</b> · {record['text']}"


HISTORY_BLOCKED = {"rejected", "failed", "cancelled"}
HISTORY_APPROVED = {"approved", "simulated", "published"}
HISTORY_BUTTON = {"text": "📚 Review history", "callback_data": "chistory:"}


def _read_run_json(run_id: str, name: str):
    try:
        return json.loads((config.OUTPUT_DIR / run_id / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _run_findings(db: PipelineDatabase, r: Dict[str, Any]) -> List[str]:
    """Why a run was blocked, in words: your reason, then the release gate's own lines, else the error."""
    from . import improve
    from .release import release_problems
    out: List[str] = []
    fb = improve.operator_feedback(db, r["run_id"]) or {}
    if fb.get("reason") or fb.get("text"):
        # "Other" is the button that asks for your words: your words say it, not the button's name
        reason = None if fb.get("text") and str(fb.get("reason") or "").lower() == "other" else fb.get("reason")
        out.append("your reason: " + " · ".join(x for x in (reason, fb.get("text")) if x))
    report = _read_run_json(r["run_id"], "release_report.json")
    if report:
        out += release_problems(report, dry_run=True)[:4]
    if not out and r.get("error"):
        out.append(clip_words(current_names(str(r["error"])), 240))   # at a word, never mid-word
    return out or ["no reason recorded"]


# The commands older records name, in their current form (a reason stored before 2026-10-25 said "/keep")
_RETIRED_NAMES = [("keep", "/photos N keep"), ("unkeep", "/photos N unkeep"), ("drop", "/photos N drop"),
                  ("review", "/photos N review"), ("fetchimages", "/photos N fetch"), ("images?", "/photos"),
                  ("requeue", "/queue requeue"), ("bump", "/queue bump"), ("undo", "/queue undo"),
                  ("propose", "/ideas research"), ("run", "/render")]   # the old name, after its slash


def failed_heading(db, run_id: str) -> str:
    """A failed run's heading: a dry run is a test (a development test read "Production failed": 2026-10-27)."""
    return "Test run failed" if db.get_setting("run_mode:" + run_id) == "dry_run" else "Production failed"


def render_options(db, args: str) -> Tuple[List[str], str]:
    """/render fresh STEP and /render reuse N as the worker's options; or why they cannot be used."""
    from .script_library import STEPS
    words, extra = args.split(), []
    usage = ("Use /render fresh STEP (" + ", ".join(STEPS) + ": that step and the later ones made again) "
             "or /render reuse N (what run N made and passed), or both.")
    while words:
        word = words.pop(0).lower()
        value = words.pop(0) if words else ""
        if word == "fresh" and value.lower() in STEPS:
            extra += ["--fresh-from", value.lower()]
        elif word == "reuse" and value:
            run_id = db.resolve_run(value)
            if not db.get_run(run_id):
                return [], f"⚠️ There is no run {html.escape(value)}. {usage}"
            extra += ["--reuse-from", run_id]
        else:
            return [], "⚠️ " + usage
    return extra, ""


def current_names(text: str) -> str:
    """A stored message with any retired command named in its current form."""
    for old, new in _RETIRED_NAMES:
        text = re.sub("/" + old + r"\b", new, text)
    return text


def _self_audit_count(run_id: str) -> int:
    from . import card_ledger
    try:
        return len(card_ledger.audit(config.OUTPUT_DIR / run_id))
    except Exception:
        return 0


def _audience_line(db: PipelineDatabase, run_id: str) -> str:
    """The latest audience numbers per platform, when the collector has any (after publishing)."""
    try:
        with db._conn() as conn:
            rows = conn.execute("SELECT platform, metrics_json FROM audience_metrics WHERE run_id=? ORDER BY fetched_at DESC",
                                (run_id,)).fetchall()
    except Exception:
        return ""
    seen, parts = set(), []
    for platform, metrics_json in rows:
        if platform in seen:
            continue
        seen.add(platform)
        try:
            metrics = json.loads(metrics_json)
        except ValueError:
            continue
        nums = [f"{k} {v:,}" for k, v in metrics.items()
                if isinstance(v, (int, float)) and k in ("plays", "views", "reach", "likes", "shares", "saves", "comments")][:3]
        if nums:
            parts.append(f"{platform}: " + ", ".join(nums))
    return " · ".join(parts)


# ------------------------------------------------------------------ analytics dashboard
# The audience as a dashboard (operator, 2026-10-28): an overview across every platform (/analytics), one
# screen per platform (/analytics ig|fb|yt|ytlong|stories), one per video (/analytics run 14, /analytics 3),
# and a picker (/analytics videos). The numbers come from audience.dashboard(); nothing here calls an API.
ANALYTICS_PAGE = 5      # videos on one platform page
PICKER_PAGE = 10        # videos on one page of the picker
ANALYTICS_BUDGET = 3900  # HTML characters a screen may use: one message, under Telegram's 4096
ANALYTICS_WORDS = {"ig": "ig", "instagram": "ig", "insta": "ig", "reels": "ig", "fb": "fb", "facebook": "fb",
                   "yt": "yt", "youtube": "yt", "shorts": "yt", "short": "yt", "ytlong": "ytlong", "long": "ytlong",
                   "documentary": "ytlong", "doc": "ytlong", "stories": "stories", "story": "stories",
                   "carousel": "stories", "carousels": "stories"}
ANALYTICS_USAGE = ("<code>/analytics</code>   <code>/analytics ig</code>   <code>/analytics fb</code>   "
                   "<code>/analytics yt</code>   <code>/analytics ytlong</code>   <code>/analytics stories</code>   "
                   "<code>/analytics videos</code>   <code>/analytics 3</code>   <code>/analytics run 14</code>")
SOURCE_NAMES = {"instagram-graph": "Instagram Graph", "facebook-graph": "Facebook Graph", "upload-post": "Upload-Post",
                "expired": "expired", "youtube-graph": "no Graph"}


def _channel_names() -> Dict[str, Tuple[str, str]]:
    from .audience import CHANNELS, EXTRAS
    return {key: (name, icon) for key, name, icon in (*CHANNELS, EXTRAS)}


def _plural(n: float, word: str) -> str:
    from .audience import compact
    return f"{compact(n)} {word}{'' if n == 1 else 's'}"


def _counts(m: Dict[str, Any], full: bool = False) -> List[str]:
    """Views, reach (full), shares, saves, follows, likes, comments (full) as short parts; a count the
    platform did not report is left out, never shown as 0."""
    from .audience import compact
    parts = [f"{compact(m['views'])} views" if m.get("views") is not None else "",
             f"reach {compact(m['reach'])}" if full and m.get("reach") is not None else ""]
    for key, word in (("shares", "share"), ("saves", "save"), ("follows", "follow"), ("likes", "like")):
        if m.get(key) is not None and (full or m[key] or key == "shares"):
            parts.append(_plural(m[key], word))
    if full and m.get("comments") is not None:
        parts.append(_plural(m["comments"], "comment"))
    return [p for p in parts if p]


def _retention(m: Dict[str, Any], duration_s: Optional[float] = None) -> List[str]:
    from .audience import compact
    parts = [f"{m['watch_ratio']:.0%} watched" if m.get("watch_ratio") is not None else "",
             f"avg {compact(m['avg_s'])} s" + (f" of {compact(duration_s)} s" if duration_s and m.get("avg_s") else "")
             if m.get("avg_s") is not None else "",
             f"skip {m['skip_rate']:.0f}%" if m.get("skip_rate") is not None else ""]
    return [p for p in parts if p]


def _measured_text(m: Dict[str, Any], duration_s: Optional[float] = None, full: bool = False) -> str:
    """A snapshot's numbers in one line, or "not measurable: <short reason>" (the whole reason goes in the
    screen's details)."""
    from .telegram_ui import label
    if not m.get("measured"):
        reason = m.get("unavailable") or "no numbers returned"
        return "not measurable: " + (reason if full else label(reason, 48))
    return ", ".join(_counts(m, full) + _retention(m, duration_s if full else None)) or "no numbers yet"


def _extra_name(platform_label: str) -> str:
    from .audience import PLATFORM_NAMES
    base, _, kind = platform_label.partition("_")
    kind = re.sub(r"^story(\d+)$", r"story \1", kind)
    return f"{PLATFORM_NAMES.get(base, base)} {kind}".strip()


def _upper(text: str) -> str:
    return text[:1].upper() + text[1:]


def _posted(v: Dict[str, Any], now: datetime) -> str:
    from .local_time import show
    if not v.get("posted_at"):
        return "posting time not recorded"
    days = max(0, (now - v["posted_at"]).days)
    return f"posted {show(v['posted_at'], '%d %b')} ({'today' if days == 0 else f'{days} d ago'})"


def _next_snapshot(v: Dict[str, Any], snaps: Dict[str, Any], now: datetime) -> str:
    """The next snapshot after the latest one taken (a skipped earlier one is never taken late)."""
    from .audience import SNAPSHOT_ORDER, SNAPSHOTS
    from .local_time import show
    taken = max((SNAPSHOT_ORDER.get(s, 0) for s in snaps), default=0)
    for name, days in SNAPSHOTS:
        if SNAPSHOT_ORDER[name] > taken and v.get("posted_at"):
            due = v["posted_at"] + timedelta(days=days)
            return f"next {name} {'due now' if due <= now else 'on ' + show(due, '%d %b')}"
    return "all snapshots taken"


def _details(v: Dict[str, Any], groups: List[Tuple[str, Dict[str, Dict[str, Any]]]], limit: int = 900) -> str:
    """Every snapshot of `groups` [(name, {snapshot: measures})], in full, for an expandable blockquote."""
    from .audience import SNAPSHOT_ORDER, STORY_SNAPSHOT
    from .local_time import show
    lines = []
    for name, snaps in groups:
        for snap in sorted(snaps, key=lambda s: SNAPSHOT_ORDER.get(s, 9)):
            m = snaps[snap]
            source = SOURCE_NAMES.get(m.get("source") or "", m.get("source") or "")
            when = "" if snap == STORY_SNAPSHOT else f"{snap} "
            lines.append(f"{name} {when}({show(m.get('fetched_at'), '%d %b %H:%M')}, {source}): "
                         f"{_measured_text(m, v.get('duration_s'), full=True)}")
    body = "\n".join(lines)
    body = body if len(body) <= limit else body[:limit - 1].rsplit("\n", 1)[0] + "\n…"
    return f"<blockquote expandable>{html.escape(body)}</blockquote>" if body else ""


def _video_totals(v: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    """(views across the video channels, mean watch ratio) at each channel's latest measured snapshot."""
    from .audience import latest
    cells = [found[1] for snaps in v["channels"].values() if (found := latest(snaps))]
    views = [c["views"] for c in cells if c.get("views") is not None]
    watch = [c["watch_ratio"] for c in cells if c.get("watch_ratio") is not None]
    return (sum(views) if views else None), (sum(watch) / len(watch) if watch else None)


def _totals_line(cells: List[Dict[str, Any]]) -> str:
    from .audience import compact
    def total(key):
        vals = [c[key] for c in cells if c.get(key) is not None]
        return sum(vals) if vals else None
    watch = [c["watch_ratio"] for c in cells if c.get("watch_ratio") is not None]
    parts = [f"{compact(total('views'))} views" if total("views") is not None else "",
             f"{sum(watch) / len(watch):.0%} watched" if watch else ""]
    parts += [_plural(total(k), w) for k, w in (("shares", "share"), ("saves", "save"), ("follows", "follow"))
              if total(k) is not None]
    return ", ".join(p for p in parts if p) or "no numbers yet"


def _sources_line(db: PipelineDatabase) -> str:
    """Which source answered each platform at the last collection; ⚠ when a platform's Meta Graph failed."""
    from .audience import PLATFORM_NAMES, sources_health
    from .local_time import show
    health = sources_health(db)
    if not health["collected_at"]:
        return "📡 <b>Sources</b>: no collection yet (hourly, once something is published)."
    tokens = []
    for base, sources in health["platforms"].items():
        name = PLATFORM_NAMES.get(base, base)
        answered = [SOURCE_NAMES.get(s, s) for s, e in sources.items() if e.get("ok")]
        graph = sources.get(f"{base}-graph") or {}
        if graph.get("failed"):
            tokens.append(f"⚠ {name}: Graph failed" + (f", {' + '.join(answered)} answered" if answered else ", nothing answered"))
        elif answered:
            tokens.append(f"✓ {name}: {' + '.join(answered)}")
        else:
            tokens.append(f"✗ {name}: nothing answered")
    detail = f"<blockquote expandable>{html.escape(chr(10).join(health['lines']))}</blockquote>" if health["lines"] else ""
    return (f"📡 <b>Sources</b> at the last collection, {html.escape(show(health['collected_at'], '%d %b %H:%M'))}\n"
            + "\n".join(html.escape(t) for t in tokens) + ("\n" + detail if detail else ""))


def _analytics_buttons() -> List[List[Dict[str, str]]]:
    from .telegram_ui import button
    return [[button("📸 Instagram", "caplat:ig"), button("📘 Facebook", "caplat:fb")],
            [button("▶️ YouTube Shorts", "caplat:yt"), button("🎬 YouTube long", "caplat:ytlong")],
            [button("🗂 Stories & carousels", "caplat:stories"), button("🎞 Pick a video", "capick:0")]]


def _fit(blocks: List[str], note: str = "") -> str:
    """The screen's blocks within ANALYTICS_BUDGET: expandable details go first, then trailing blocks."""
    from .telegram_ui import compose
    text = compose(*blocks)
    if len(text) <= ANALYTICS_BUDGET:
        return text
    blocks = [re.sub(r"\n?<blockquote expandable>.*?</blockquote>", "", b, flags=re.S) for b in blocks]
    while len(compose(*blocks, note)) > ANALYTICS_BUDGET and len(blocks) > 1:
        blocks.pop()
    return compose(*blocks, note)


def analytics_overview(db: PipelineDatabase, now: Optional[datetime] = None, note: str = ""):
    """/analytics: the last 7 and 28 days across platforms, a row per platform with its trend, the best and
    worst video, how much data there is, and which sources answered."""
    from . import audience
    from .telegram_ui import label, title, with_nav
    now = now or datetime.now(timezone.utc)
    buttons = with_nav(_analytics_buttons(), "cstat:")
    videos = audience.dashboard(db)
    head = title("canalytics", summary=note or None)
    if not videos:
        return _fit([head, "Nothing measured yet. Each published post is measured 1, 3, 7 and 28 days after posting "
                           "(Instagram, Facebook and YouTube: Reels, Shorts, the long video and carousels), and each "
                           f"Story {audience.STORY_SNAPSHOT_HOURS} hours after posting, before it expires.",
                     _sources_line(db)]), buttons

    def window(days):
        since = now - timedelta(days=days)
        return [v for v in videos if v["posted_at"] and v["posted_at"] >= since]

    def cells(group, channel=None):
        return [found[1] for v in group for ch, snaps in v["channels"].items()
                if (channel is None or ch == channel) and (found := audience.latest(snaps))]

    period = []
    for days in (7, 28):
        group = window(days)
        period.append(f"<b>{days} days</b> ({_plural(len(group), 'video')}): " +
                      (_totals_line(cells(group)) if group else "nothing posted"))
    month = window(28)
    rows = []
    for key, name, icon in audience.CHANNELS:
        ever = [v for v in videos if key in v["channels"]]
        recent = [v for v in month if audience.latest(v["channels"].get(key, {}))]
        arrow = audience.trend(videos, key) or "—"
        if not ever:
            rows.append(f"{icon} <b>{name}</b>: no data yet")
        elif not recent:
            measured = [v for v in ever if audience.latest(v["channels"][key])]
            if measured:
                rows.append(f"{icon} <b>{name}</b> {arrow} nothing posted in 28 days")
            else:
                why = (audience.latest(ever[0]["channels"][key], measured=False) or ("", {}))[1]
                rows.append(f"{icon} <b>{name}</b>: no data yet, {html.escape(_measured_text(why))}")
        else:
            rows.append(f"{icon} <b>{name}</b> {arrow} {_plural(len(recent), 'video')}: {_totals_line(cells(recent, key))}")
    extras = [m for v in month for snaps in v["extras"].values() if (found := audience.latest(snaps)) for m in [found[1]]]
    tried = sum(len(v["extras"]) for v in month)
    _, story_name, story_icon = audience.EXTRAS
    rows.append(f"{story_icon} <b>{html.escape(story_name)}</b>: " + (
        f"{len(extras)} of {tried} measured, {_totals_line(extras)}" if extras else
        f"none of {tried} measurable" if tried else "no data yet"))
    legend = (f"<i>▲ ▼ ≈ the newest video against the platform's own median at the same snapshot; "
              f"— under {audience.MIN_FOR_TREND} videos.</i>")

    ranked = [(v, *_video_totals(v)) for v in month]
    best = []
    by_views = sorted((r for r in ranked if r[1] is not None), key=lambda r: r[1], reverse=True)
    by_watch = sorted((r for r in ranked if r[2] is not None), key=lambda r: r[2], reverse=True)
    t = lambda v: html.escape(label(v["title"], 34))  # noqa: E731
    if by_views:
        best.append(f"🏆 Most views: {t(by_views[0][0])}, {audience.compact(by_views[0][1])}")
        if len(by_views) > 1:
            best.append(f"🔻 Fewest views: {t(by_views[-1][0])}, {audience.compact(by_views[-1][1])}")
    if len(by_watch) > 1:
        best.append(f"🏆 Best watched: {t(by_watch[0][0])}, {by_watch[0][2]:.0%}")
        best.append(f"🔻 Least watched: {t(by_watch[-1][0])}, {by_watch[-1][2]:.0%}")
    elif by_watch:
        best.append(f"Watch %: only {t(by_watch[0][0])} so far, {by_watch[0][2]:.0%}")

    measured = [v for v in videos if any(audience.latest(s) for s in v["channels"].values())]
    need = audience.MIN_FOR_PATTERNS - len(measured)
    if need <= 0:
        so_far = f"📊 <b>Data so far</b>: {_plural(len(measured), 'video')}; enough to read patterns."
    else:
        pace = len(month) / 4  # videos a week over the last 28 days
        when = ""
        if pace:
            from math import ceil
            from .local_time import show
            when = f", around {show(now + timedelta(weeks=ceil(need / pace)), '%d %b')} at {audience.compact(pace)} a week"
        so_far = (f"📊 <b>Data so far</b>: {_plural(len(measured), 'video')}. Patterns from "
                  f"{audience.MIN_FOR_PATTERNS}: {need} more{when or ', once more are posted'}. Until then these "
                  "describe single videos.")
    return _fit([head, "\n".join(period), "\n".join(rows) + "\n" + legend,
                 ("<b>Best and worst</b> (28 days)\n" + "\n".join(best)) if best else "",
                 so_far, _sources_line(db)]), buttons


def analytics_platform(db: PipelineDatabase, channel: str, page: int = 0, now: Optional[datetime] = None):
    """One platform: each video, newest first, its snapshots, numbers and retention, details folded."""
    from . import audience
    from .telegram_ui import label, title, with_nav
    now = now or datetime.now(timezone.utc)
    names = _channel_names()
    name, icon = names.get(channel, (channel, "📊"))
    stories = channel == audience.EXTRAS[0]
    videos = [v for v in audience.dashboard(db) if (v["extras"] if stories else v["channels"].get(channel))]
    pages = max(1, -(-len(videos) // ANALYTICS_PAGE))
    page = min(max(0, page), pages - 1)
    shown = videos[page * ANALYTICS_PAGE:(page + 1) * ANALYTICS_PAGE]
    trend = None if stories else audience.trend(videos, channel)
    summary = (f"{_plural(len(videos), 'video')} measured, newest first" +
               (f"; page {page + 1} of {pages}" if pages > 1 else "") +
               ("" if stories else f"\nTrend of the newest: {trend or '—'}" +
                ("" if trend else f" (from {audience.MIN_FOR_TREND} videos at one snapshot)")))
    head = title(icon, name, summary if videos else "No data yet: nothing on this platform has been measured.")
    blocks = [head]
    for n, v in enumerate(shown, page * ANALYTICS_PAGE + 1):
        lines = [f"<b>{n}. {html.escape(label(v['title'], 60))}</b>"]
        if stories:
            lines.append(html.escape(_upper(_posted(v, now))))
            for kind, snaps in sorted(v["extras"].items()):
                snap, m = audience.latest(snaps) or audience.latest(snaps, measured=False)
                lines.append(f"{html.escape(_extra_name(kind))} ({snap}): {html.escape(_measured_text(m))}")
            lines.append(_details(v, [(_extra_name(k), s) for k, s in sorted(v["extras"].items())], limit=500))
        else:
            snaps = v["channels"][channel]
            order = sorted(snaps, key=lambda s: audience.SNAPSHOT_ORDER.get(s, 9))
            lines.append(html.escape(_upper(f"{_posted(v, now)} · snapshots {', '.join(order)} · {_next_snapshot(v, snaps, now)}")))
            snap, m = audience.latest(snaps) or audience.latest(snaps, measured=False)
            if m.get("measured"):
                lines.append(f"{snap}: " + html.escape(", ".join(_counts(m)) or "no counts reported"))
                kept = _retention(m, v.get("duration_s"))
                lines.append("Retention: " + html.escape(", ".join(kept)) if kept else
                             "Retention: not reported" + (" by Upload-Post" if m.get("source") == "upload-post" else ""))
            else:
                lines.append(f"{snap}: {html.escape(_measured_text(m))}")
            lines.append(_details(v, [(name, snaps)], limit=500))
        blocks.append("\n".join(x for x in lines if x))
    rows = number_buttons([f"cavideo:{v['key']}" for v in shown], page * ANALYTICS_PAGE + 1)
    rows += pager(f"caplat:{channel}:", page, pages)
    return _fit(blocks, "<i>More on the next page, or open a video.</i>"), with_nav(rows, "canalytics:")


def analytics_video(db: PipelineDatabase, ref: str, now: Optional[datetime] = None):
    """One video on every platform side by side, snapshot by snapshot, then its Stories and carousels."""
    from . import audience
    from .telegram_ui import title, with_nav
    now = now or datetime.now(timezone.utc)
    videos = audience.dashboard(db)
    run_id = db.resolve_run(str(ref).strip()) if str(ref).strip() else ""
    v = next((x for x in videos if run_id and (x["run_id"] == run_id or x["key"] == str(ref).strip())), None)
    nav_rows = with_nav([], "capick:0")
    if v is None:
        return (title("🎞", "Video audience", "No audience numbers for this video yet: each post is measured 1, 3, 7 "
                      "and 28 days after posting.") + "\n\n" + ANALYTICS_USAGE), nav_rows
    present = [(k, n, i) for k, n, i in audience.CHANNELS if k in v["channels"]]
    kind = "long video" if v["format"] == "deep_dive" else "Reel and Short"
    length = f", {audience.compact(v['duration_s'])} s" if v.get("duration_s") else ""
    head = title("🎞", v["title"][:80], html.escape(_upper(f"{_posted(v, now)} · {kind}{length}")))
    blocks = [head]
    if present:
        short = {"ig": "IG", "fb": "FB", "yt": "YT", "ytlong": "YT-L"}
        snaps = sorted({s for k, _, _ in present for s in v["channels"][k]}, key=lambda s: audience.SNAPSHOT_ORDER.get(s, 9))
        table = ["      " + "".join(f"{short[k]:>7}" for k, _, _ in present)]
        for s in snaps:
            cells = []
            for k, _, _ in present:
                m = v["channels"][k].get(s)
                cells.append("·" if m is None else "×" if not m.get("measured") else
                             audience.compact(m["views"]) if m.get("views") is not None else "✓")
            table.append(f"{s:<6}" + "".join(f"{c:>7}" for c in cells))
        blocks.append("<b>Views by snapshot</b>\n<pre>" + html.escape("\n".join(table)) + "</pre>\n"
                      "<i>× not measurable, · not taken yet</i>")
        latest_lines = []
        for k, n, i in present:
            snap, m = audience.latest(v["channels"][k]) or audience.latest(v["channels"][k], measured=False)
            latest_lines.append(f"{i} {n} ({snap}): {html.escape(_measured_text(m, v.get('duration_s')))}")
        blocks.append("<b>Latest per platform</b>\n" + "\n".join(latest_lines))
    else:
        blocks.append("No Reel, Short or long video measured for this post yet.")
    if v["extras"]:
        story_lines = []
        for label, snaps in sorted(v["extras"].items()):
            snap, m = audience.latest(snaps) or audience.latest(snaps, measured=False)
            story_lines.append(f"{html.escape(_extra_name(label))} ({snap}): {html.escape(_measured_text(m))}")
        blocks.append(f"<b>{audience.EXTRAS[1].replace('&', '&amp;')}</b>\n" + "\n".join(story_lines))
    groups = [(n, v["channels"][k]) for k, n, _ in present] + [(_extra_name(k), s) for k, s in sorted(v["extras"].items())]
    blocks.append("<b>Every snapshot</b>\n" + _details(v, groups, limit=1500))
    jump = [{"text": f"{i} {n}"[:28], "callback_data": f"caplat:{k}"} for k, n, i in present]
    rows = [jump[j:j + 2] for j in range(0, len(jump), 2)]
    return _fit(blocks), with_nav(rows, "capick:0")


def analytics_picker(db: PipelineDatabase, page: int = 0, now: Optional[datetime] = None):
    """Every measured video, newest first, numbered as /analytics N opens it."""
    from . import audience
    from .telegram_ui import label, title, with_nav
    now = now or datetime.now(timezone.utc)
    videos = audience.dashboard(db)
    pages = max(1, -(-len(videos) // PICKER_PAGE))
    page = min(max(0, page), pages - 1)
    shown = videos[page * PICKER_PAGE:(page + 1) * PICKER_PAGE]
    lines = []
    for n, v in enumerate(shown, page * PICKER_PAGE + 1):
        views, watch = _video_totals(v)
        where = "/".join({"ig": "IG", "fb": "FB", "yt": "YT", "ytlong": "YT-L"}[k] for k, _, _ in audience.CHANNELS
                         if audience.latest(v["channels"].get(k, {})))
        numbers = (f"{audience.compact(views)} views" if views is not None else "no data yet") + \
                  (f", {watch:.0%} watched" if watch is not None else "") + (f" ({where})" if where else "")
        lines.append(f"{n}. <b>{html.escape(label(v['title'], 48))}</b>\n    {html.escape(_upper(_posted(v, now)))}: {numbers}")
    summary = (f"{_plural(len(videos), 'video')}, newest first" + (f"; page {page + 1} of {pages}" if pages > 1 else "")
               if videos else "No data yet: nothing has been measured.")
    rows = number_buttons([f"cavideo:{v['key']}" for v in shown], page * PICKER_PAGE + 1) + pager("capick:", page, pages)
    return _fit([title("capick", summary=summary), "\n".join(lines),
                 "<i>Tap a number, or type <code>/analytics 3</code>.</i>" if videos else ""]), with_nav(rows, "canalytics:")


def analytics_screen(db: PipelineDatabase, args: str = "", now: Optional[datetime] = None):
    """Typed /analytics and its subcommands: ig|fb|yt|ytlong|stories [page], videos [page], N (the picker's
    number), run <run key or id>."""
    from . import audience
    raw = (args or "").replace(":", " ").split()
    words = [w.lower() for w in raw]
    if not words:
        return analytics_overview(db, now)
    if words[:2] == ["youtube", "long"]:
        words = ["ytlong"] + words[2:]
    if words[0] == "video" and len(words) > 1:  # /analytics video 3: the picker's number
        words = words[1:]
    first, rest = words[0], words[1:]
    page = int(rest[0]) - 1 if rest and rest[0].isdigit() else 0  # typed pages count from 1
    if first in ANALYTICS_WORDS:
        return analytics_platform(db, ANALYTICS_WORDS[first], page, now)
    if first in ("videos", "video", "list", "pick"):
        return analytics_picker(db, page, now)
    if first == "run" and rest:
        return analytics_video(db, raw[1], now)
    if first.isdigit():
        videos = audience.dashboard(db)
        n = int(first)
        if 1 <= n <= len(videos):
            return analytics_video(db, videos[n - 1]["run_id"], now)
        return analytics_overview(db, now, note=f"No video {n} on the list: there are {len(videos)}.")
    return analytics_overview(db, now, note="Not a view I know. " + ANALYTICS_USAGE)


# Posting actions on one episode: their answer is shown on that episode's card, redrawn
EPISODE_ACTIONS = {"retryreel", "fbcaptions", "fbcover", "ytshort", "rebook", "ytthumb"}
LIST_PAGE = 5  # items on a page of a numbered list (Reviews, Review history)


def number_buttons(callbacks: List[str], first: int) -> List[List[Dict[str, str]]]:
    """One button per item, its number on the list: a row of up to five (operator, 2026-10-25: numbers, not
    a run's name cut to fit a button)."""
    row = [{"text": str(first + i), "callback_data": cb} for i, cb in enumerate(callbacks)]
    return [row[i:i + 5] for i in range(0, len(row), 5)]


def remakeable(r: Dict[str, Any]) -> bool:
    """Whether Make again can rebuild this run: a rejected or sent-back one that had a video (regenerated with
    your reason), or a failed one whose script had passed the fact check (resumed where it stopped)."""
    if r.get("status") in ("rejected", "regenerate"):
        return bool(r.get("master_video_path"))
    return r.get("status") == "failed" and _worth_resuming(r)


def review_history(db: PipelineDatabase, page: int = 0, limit: int = 20) -> Tuple[str, List[List[Dict[str, str]]]]:
    """📚 Review history, from the runs' own records: what was blocked (to address whenever it suits you)
    and what was approved (to see what did well) (operator, 2026-10-24). One numbered list across both,
    five to a page, a number button for each (2026-10-25)."""
    e = html.escape
    runs = db.list_recent_runs(limit=80)
    blocked = [r for r in runs if r.get("status") in HISTORY_BLOCKED and (r.get("master_video_path") or r.get("status") == "rejected")][:limit]
    approved = [r for r in runs if r.get("status") in HISTORY_APPROVED][:limit]
    items = [("blocked", r) for r in blocked] + [("approved", r) for r in approved]
    pages = max(1, (len(items) + LIST_PAGE - 1) // LIST_PAGE)
    page = max(0, min(page, pages - 1))
    shown = items[page * LIST_PAGE:(page + 1) * LIST_PAGE]
    lines = [f"📚 <b>Review history</b> · {len(blocked)} blocked · {len(approved)} approved"
             + (f" · page {page + 1} of {pages}" if pages > 1 else ""),
             "Tap a number for its reasons, its checks and, once posted, its audience."]
    heads = {"blocked": "⛔ <b>Not posted</b> · rejected by you, stopped by a check, or cancelled",
             "approved": "✅ <b>Approved</b> · passed, with its self-audit"}
    section = None
    for n, (kind, r) in enumerate(shown, page * LIST_PAGE + 1):
        if kind != section:
            section = kind
            lines += ["", heads[kind]]
        if kind == "blocked":
            why = "; ".join(e(x) for x in _run_findings(db, r)[:2])
            note = (why or e(r["status"])) + (" · <b>can be made again</b>" if remakeable(r) else "")
        else:
            audit = _self_audit_count(r["run_id"])
            audience = _audience_line(db, r["run_id"])
            note = ("self-audit clean" if audit == 0 else f"{audit} self-audit finding(s)") + (f" · {e(audience)}" if audience else "")
        word = STATUS_WORDS.get(r["status"], r["status"])
        lines.append(f"{n}. {e(db.run_label(r['run_id']))} · {e(word)}\n    <i>{note}</i>")   # the label carries the date
    if not items:
        lines += ["", "Nothing among the recent runs yet."]
    buttons = number_buttons([f"chist:{db.run_key(r['run_id'])}" for _, r in shown], page * LIST_PAGE + 1)
    return "\n".join(lines), buttons + pager("chistory:", page, pages)


def history_details(db: PipelineDatabase, key: str) -> Tuple[str, List[List[Dict[str, str]]]]:
    """One run from the history: its reasons when blocked; its audit, gate and audience when approved."""
    from .release import release_problems
    e = html.escape
    run_id = db.resolve_run(key)
    r = db.get_run(run_id) or {}
    word = STATUS_WORDS.get(r.get("status"), r.get("status") or "unknown")
    lines = [f"📄 <b>{e(db.run_label(run_id))}</b>",        # the label carries the time, in your zone
             f"{e(word[:1].upper() + word[1:])} · Run ID: <code>{e(run_id)}</code>", ""]
    if r.get("status") in HISTORY_BLOCKED:
        lines.append("<b>Why it was blocked</b>")
        lines += [f"• {e(x)}" for x in _run_findings(db, r)[:8]]
        if remakeable(r):
            how = ("a new version is written with the reason you gave, reusing the voice and clips already paid for"
                   if r.get("status") in ("rejected", "regenerate") else
                   "it resumes where it stopped, reusing everything it made after its script passed the fact check")
            lines += ["", f"<b>Make again</b>: {how}. It comes back to you for review."]
            return "\n".join(lines), [[{"text": "🔁 Make again", "callback_data": f"rerender:{db.run_key(run_id)}"}],
                                      [HOME]]
        lines += ["", "It can't be made again from here: render the topic again from the queue."]
    else:
        n = _self_audit_count(run_id)
        audience = _audience_line(db, run_id)
        report = _read_run_json(run_id, "release_report.json")
        lines.append("<b>How it did</b>")
        lines.append("• self-audit: clean" if n == 0 else f"• self-audit: {n} finding(s) (cards against the run's records)")
        if report:
            problems = release_problems(report, dry_run=True)
            lines.append("• release gate: passed" if not problems else "• release gate: " + e("; ".join(problems[:3])))
        lines.append(f"• audience: {e(audience)}" if audience else "• audience: no numbers yet (collected after publishing)")
    return "\n".join(lines), [[HOME]]


POST_ICON = {"pending": "🗓", "waiting": "⏳", "published": "✅", "failed": "❌", "unknown": "❓", "cancelled": "⏹"}
POST_NAME = {"reel": "Reel", "story": "Stories", "carousel": "Carousel"}


def _channel_kind(channel: str) -> str:
    """Which booking a publication receipt belongs to: "upload-post:story" is the Stories, ":carousel" the
    carousel, any other (the Reel's own, YouTube Shorts, a retry) the Reel."""
    for kind in ("story", "carousel"):
        if str(channel).endswith(":" + kind):
            return kind
    return "reel"


def _post_links(db: PipelineDatabase, run_id: str, kind: Optional[str] = None) -> str:
    """The links from a run's publication receipts, one per platform; with `kind`, only that post's own (the
    Stories line showed the Reel's YouTube link: operator, 2026-10-25)."""
    urls: Dict[str, str] = {}
    unlinked: List[str] = []  # posted, but the platform gave no link (Facebook gives none for a Page's Stories)
    for pub in db.list_publications(run_id):
        if kind and _channel_kind(pub.get("channel", "")) != kind:
            continue
        try:
            payload = json.loads(pub.get("response_payload_json") or "{}")
        except ValueError:
            continue
        for platform, url in (payload.get("post_urls") or {}).items():
            if url and platform not in urls:
                urls[platform] = str(url)
            elif not url and pub.get("status") == "published" and platform not in unlinked:
                unlinked.append(platform)
    parts = [f'<a href="{html.escape(u)}">{html.escape(p)}</a>' for p, u in urls.items()]
    parts += [f"{html.escape(p)} (posted; it gives no link)" for p in unlinked if p not in urls]
    return " · ".join(parts)


def _when(iso: Optional[str], ahead: bool = True) -> str:
    from .post_schedule import fmt, fmt_ahead
    if not iso:
        return ""
    try:
        moment = datetime.fromisoformat(str(iso))
    except ValueError:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return fmt_ahead(moment) if ahead else fmt(moment)


def carousel_slot_for(db: PipelineDatabase, post: Dict[str, Any]) -> Optional[datetime]:
    """When a waiting carousel is expected to go out: the first carousel slot 48 hours after its Reel posted
    (and not before now). None while its Reel has not posted. Only the newest waiting carousel posts in a slot."""
    from . import post_schedule
    reel = next((p for p in db.list_scheduled_posts(("published",), run_id=post["run_id"]) if p["kind"] == "reel"), None)
    posted = ((reel or {}).get("detail") or {}).get("posted_at") or (reel or {}).get("due_at")
    if not posted:
        return None
    earliest = max(datetime.now(timezone.utc), datetime.fromisoformat(posted) + timedelta(hours=48))
    try:
        return post_schedule.next_carousel_slot(earliest)
    except ValueError:
        return None


def _episode_bookings(db: PipelineDatabase) -> List[Tuple[str, List[Dict[str, Any]]]]:
    """Every run with bookings, those with something still to post first (soonest first), then the rest
    (newest first); each run's bookings in posting order: Reel, Stories, carousel."""
    every = db.list_scheduled_posts(("pending", "waiting", "published", "failed", "unknown", "cancelled", "expired"))
    runs: Dict[str, List[Dict[str, Any]]] = {}
    for p in every:
        runs.setdefault(p["run_id"], []).append(p)
    order = {"reel": 0, "story": 1, "carousel": 2}
    for run_id, posts in runs.items():
        # A booking cancelled with a failed slot and booked again since shows once, as its new booking: Göbekli
        # Tepe's list read "Stories cancelled" above "Stories posted" after its Reel's retry (operator, 2026-10-27).
        newest = {}
        for p in posts:
            newest[p["kind"]] = max(newest.get(p["kind"], 0), p["id"])
        posts[:] = [p for p in posts if p["status"] not in ("cancelled", "expired") or p["id"] == newest[p["kind"]]]
        posts.sort(key=lambda p: order.get(p["kind"], 3))

    def key(item):
        _, posts = item
        open_due = [p.get("due_at") or "9999" for p in posts if p["status"] in ("pending", "waiting")]
        return (0, min(open_due), 0) if open_due else (1, "", -max(p["id"] for p in posts))
    return sorted(runs.items(), key=key)


def posting_panel(db: PipelineDatabase, page: int = 0) -> Tuple[str, List[List[Dict[str, str]]]]:
    """🗓 Posting (src/posting_view.py): the episodes as a numbered list, each post on its own line."""
    from .posting_view import posting_panel as panel
    return panel(db, page)


def posting_episode(db: PipelineDatabase, run_id: str) -> Tuple[str, List[List[Dict[str, str]]]]:
    """One episode's posts (src/posting_view.py)."""
    from .posting_view import posting_episode as episode
    return episode(db, run_id)


def post_candidates(db: PipelineDatabase, hours: int = 48, limit: int = 3) -> List[Dict[str, Any]]:
    """Finished dry runs (simulated) of the last `hours`, newest first: what "Post for real" can publish."""
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    out = []
    for r in db.list_recent_runs(limit=20):
        if (r.get("updated_at") or r.get("created_at") or "") < since:
            break
        if r.get("status") == "simulated" and r.get("master_video_path"):
            out.append(r)
        if len(out) >= limit:
            break
    return out


def dashboard_text_and_buttons(db: PipelineDatabase, next_run: Optional[str] = None, assets_dir: Path = ASSETS_DIR) -> Tuple[str, List[List[Dict[str, str]]]]:
    """What needs you first (reviews, proposals, problems), then the state of production."""
    e = html.escape
    paused = is_paused(db)
    topics = db.list_all_topics()
    queued = [t for t in topics if t.get("status") == "queued"]
    ready = ready_pools(db, queued)
    pending = db.list_pending_approvals()
    try:
        from . import improve
        waiting_proposals = [p for p in improve.proposals(db) if p["status"] == "ready"]
    except Exception:
        waiting_proposals = []
    from . import accounts
    account_problems = accounts.cached(db).get("problems") or []
    next_t = None
    try:
        next_t = db.get_next_topic()
    except Exception:
        pass

    needs = []
    if pending:  # one run a line (two titles shared a line: 2026-10-25 overhaul)
        needs.append(f"🎬 <b>{len(pending)} review{'s' if len(pending) != 1 else ''} waiting</b>\n"
                     + "\n".join(f"  • {e(db.run_label(p['run_id']))}" for p in pending[:3])
                     + (f"\n  • and {len(pending) - 3} more" if len(pending) > 3 else ""))
    timed_out = db.timed_out_reviews() if hasattr(db, "timed_out_reviews") else []
    if timed_out:  # their topics wait for this decision instead of being made again
        needs.append(f"⌛ <b>{len(timed_out)} review{'s' if len(timed_out) != 1 else ''} timed out</b> · Resend or Reject in Reviews\n"
                     + "\n".join(f"  • {e(db.run_label(r['run_id']))}" for r in timed_out[:2]))
    if waiting_proposals:
        needs.append(f"🧭 <b>{len(waiting_proposals)} improvement proposal(s)</b> — /improve")
    if account_problems:
        needs.append("⚠️ " + e(account_problems[0]))
    from . import provider_status
    needs += provider_status.dashboard_lines()  # a provider shut by a limit or cap, said here not only in the log
    if queued and ready < len(queued) and ready < 3:
        needs.append(f"🖼 Only {ready} queued topic(s) have photos ready")
    recent = db.list_recent_runs(limit=1)
    active = db.get_active_progress() if hasattr(db, "get_active_progress") else None
    late = rerenderable(db) if not active else None
    if late and not (recent and late["run_id"] == recent[0]["run_id"]):
        # A run that failed after its video was made is waiting for a decision too, even when a later run
        # (a cancelled scheduled one) is the latest: the panel said "Nothing needs you" above a Re-render
        # button (2026-10-24).
        reason = short_reason(late.get("error") or "", limit=1200)
        what = {"rejected": "Rejected, can be made again",
                "regenerate": "Sent back to Regenerate; its new version did not start"}.get(late.get("status"), "")
        if not what:  # said as it stopped: before or after its video was made
            what = "Failed after rendering" if late.get("master_video_path") else "Failed after its fact check"
        # The run id in code, not italics: Telegram linked its digits as a phone number (2026-10-24).
        needs.append(f"🔁 <b>{what}</b>\n  • {e(db.run_label(late['run_id']))}\n  <code>{e(late['run_id'])}</code>"
                     + (f"\n<blockquote expandable>{e(reason)}</blockquote>" if reason else ""))
    if recent and recent[0].get("status") == "failed":
        # Which run, on one line; the whole reason folded underneath (it was cut off mid-sentence:
        # operator, 2026-10-23).
        reason = short_reason(recent[0].get("error") or "", limit=1200)
        needs.append(f"❌ <b>Last run failed</b>\n  • {e(db.run_label(recent[0]['run_id']))}\n  <code>{e(recent[0]['run_id'])}</code>"
                     + (f"\n<blockquote expandable>{e(reason)}</blockquote>" if reason else ""))

    from . import config
    bot_id = config.TELEGRAM_BOT_TOKEN.split(":", 1)[0]
    environment = {"8701645452": "DEV", "8837268021": "PRODUCTION"}.get(bot_id, "IDENTITY UNVERIFIED")
    try:
        from .release_info import release_label
        release = release_label()
    except Exception:  # the dashboard never fails over a version label
        release = None
    if release:
        environment += f" ({e(release)})"
    lines = [f"🏛️ <b><u>RELIC &amp; RIDDLE</u></b> · {environment}", ""]
    # A blank line between items: they ran together (operator, 2026-10-25).
    lines += (["<b>Needs you</b>"] + [ln for item in needs for ln in (item, "")]) if needs else ["✅ <b>Nothing needs you right now.</b>", ""]
    outcome = last_review_outcome(db)
    if outcome:  # the decided review's receipt lives here, so the chat keeps one panel
        lines += [outcome, ""]
    # One fact a line (the 2026-10-25 overhaul): the schedule, what is being made, the queue, the next slot.
    lines.append("<b>Production</b>")
    lines.append(f"{'⏸ Schedule <b>paused</b>' if paused else '▶️ Schedule active'}"
                 + (f" · next run {e(next_run)}" if next_run and not paused else ""))
    if active:
        label = "Making" if active.get("worker_state") == "running" else "Last recorded run (worker not active/verified)"
        lines.append(f"⏳ {label}: {e(db.run_label(active['run_id']))} · step {active.get('step', 1)} of {active.get('total_steps', 5)}")
    lines.append(f"📋 Queue: {len(queued)} topics · {ready} with photos ready")
    if next_t:
        lines.append(f"➡️ Next up: <b>{e(next_t['title'])}</b>")
    booked = db.list_scheduled_posts(("pending", "waiting"))
    lines.append("🗓 Posting: " + e(next_slot_line(db, booked)))
    lines += ["", "<b>This month</b>", spend_summary(db).replace("💳 This month · ", "💳 ")]
    lines += ["", "<b>Last 7 days</b>", health_line(db).replace("Last 7 days · ", "").replace(" · median", "\n⏱ median")]
    beat = heartbeat_line(db)
    if beat:  # the daily heartbeat lives on the panel, not in its own message (operator, 2026-10-25)
        lines.append(beat)
    digest = weekly_digest(db)
    if digest:
        lines.append(f"📰 This week's digest is ready ({e(digest['label'])})")

    waiting_reviews = len(pending) + len(timed_out)  # timed-out reviews are decisions waiting too
    row1 = []  # what waits for a decision; Reviews itself is counted on its own button below
    if waiting_proposals:
        row1.append({"text": f"🧭 Proposals ({len(waiting_proposals)})", "callback_data": "cimprove:"})
    candidates = post_candidates(db, limit=1) if not active else []
    if candidates:  # a finished dry run can be posted for real from here (asked to confirm first)
        # The run is named with what needs you, not squeezed into the button (operator, 2026-10-24).
        candidate_line = f"🚀 <b>Post candidate</b>\n  • {e(db.run_label(candidates[0]['run_id']))}"
        needs_index = lines.index("<b>Needs you</b>") + 1 if "<b>Needs you</b>" in lines else None
        if needs_index is None:
            lines[0:2] = [lines[0], "", "<b>Needs you</b>", candidate_line, ""]
            lines.remove("✅ <b>Nothing needs you right now.</b>")
        else:
            lines[needs_index:needs_index] = [candidate_line, ""]
        row1.append({"text": "🚀 Post for real", "callback_data": f"postreal:{db.run_key(candidates[0]['run_id'])}"})
    if late:
        # A run that failed after its video was made (the release gate, say) is made again in place. Not only
        # the latest run: a cancelled scheduled run hid the button for the run that mattered (2026-10-24).
        verb = "Make again" if late.get("status") in ("rejected", "regenerate") else "Re-render"  # rejected: regenerated with the reason given
        # The run is named in Needs you above; the button is the verb (a name on it ran past 28 characters).
        row1.append({"text": f"🔁 {verb}", "callback_data": f"rerender:{db.run_key(late['run_id'])}"})
    if digest:
        row1.append({"text": "📰 Weekly digest", "callback_data": "cdigest:"})
    buttons = [row1] if row1 else []
    render_btn = {"text": "⚡ Progress" if active else (f"⚡ Render #{next((i for i,t in enumerate(topics,1) if next_t and t['id']==next_t['id']),1)} now"),
                  "callback_data": "cprog:" if active else (f"qrender:{next_t['id']}" if next_t else "qrender:1")}
    # One action per topic: when the next topic is the one Re-render / Make again above is for, Render now
    # would make it a second way (operator, 2026-10-25). The Queue still starts it afresh.
    same_topic = bool(late and next_t and not active and late.get("topic_id") == next_t.get("id"))
    # The sections of the studio, two a row, in the order of a day's work (the 2026-10-25 overhaul): make,
    # review and post, then the material, then the health of the whole. Ideas live under Queue, Covers and
    # uploads under Pictures, accounts, jobs, alerts and the digest under Status.
    from .telegram_ui import counted
    reviews_btn = {"text": counted("🎬 Reviews", waiting_reviews), "callback_data": "creviews:"}
    if waiting_reviews:  # what needs you leads: Reviews heads the first row while any wait
        row1.insert(0, reviews_btn)
        buttons = [row1]
    sections = ([] if same_topic else [render_btn]) + [{"text": "📋 Queue", "callback_data": "qrefresh:"}]
    sections += [] if waiting_reviews else [reviews_btn]
    sections += [{"text": counted("🗓 Posting", len({p["run_id"] for p in booked})), "callback_data": "cposts:"},
                 {"text": "🖼 Pictures", "callback_data": "cimages:"}, {"text": "🎵 Music", "callback_data": "cmusic:"},
                 {"text": "📊 Status", "callback_data": "cstat:"}, {"text": "❓ Help", "callback_data": "chelp:Everyday"},
                 {"text": "▶️ Resume" if paused else "⏸ Pause", "callback_data": "cresume:" if paused else "cpause:"}]
    buttons += [sections[i:i + 2] for i in range(0, len(sections), 2)]
    return "\n".join(lines), buttons


def still_to_post(booked: List[Dict[str, Any]]) -> str:
    """'still to post: 1 Reel, 2 carousels, from 3 episodes': what waits, by kind, at any time."""
    if not booked:
        return "nothing waiting to post"
    names = {"reel": ("Reel", "Reels"), "story": ("Stories post", "Stories posts"), "carousel": ("carousel", "carousels")}
    counts: Dict[str, int] = {}
    for p in booked:
        counts[p["kind"]] = counts.get(p["kind"], 0) + 1
    parts = [f"{n} {names.get(k, (k, k + 's'))[n != 1]}" for k, n in sorted(counts.items(), key=lambda x: list(names).index(x[0]) if x[0] in names else 9)]
    episodes = len({p["run_id"] for p in booked})
    return "still to post: " + ", ".join(parts) + f", from {episodes} episode{'s' if episodes != 1 else ''}"


# A run in one of these is still going or still waits for someone; any other last run is over
LIVE_RUN_STATUSES = {"producing", "pending", "started", "pending_approval", "approved", "scheduled", "publishing",
                     "publish_unknown"}


def stale_topics(db: PipelineDatabase) -> Dict[str, str]:
    """Topics still marked in production whose newest run is over: sent back to Regenerate with no new version,
    rejected, failed or cancelled. {topic_id: that run's status}. They could not be restored (/queue requeue
    refused an in-production topic) and the dashboard stops offering a Re-render after RERENDER_HOURS, so
    Antikythera sat out of the queue from 25 Oct (operator, 2026-10-27)."""
    out: Dict[str, str] = {}
    marked = [t for t in db.list_all_topics() if t.get("status") == "in_production"]
    if not marked:
        return out
    with db._conn() as conn:
        for t in marked:
            row = conn.execute("SELECT status FROM runs WHERE topic_id = ? ORDER BY rowid DESC LIMIT 1", (t["id"],)).fetchone()
            if row and row["status"] not in LIVE_RUN_STATUSES:
                out[t["id"]] = row["status"]
    return out


STALE_WORDS = {"regenerate": "sent back to Regenerate; its new version never started", "rejected": "its last run was rejected",
               "failed": "its last run failed", "cancelled": "its last run was cancelled", "timeout": "its review timed out"}


def next_slot_line(db: PipelineDatabase, booked: List[Dict[str, Any]]) -> str:
    """'next free Reel slot Wed 28 Oct 17:00 MDT · still to post: 3 carousels, from 3 episodes'. The slot is the
    first one with no Reel in it; what waits is counted apart, by kind: "next Reel slot ... 3 episode(s) booked"
    read as three episodes booked into a slot that Slots showed free (operator, 2026-10-27)."""
    from . import post_schedule
    try:
        taken = {datetime.fromisoformat(p["due_at"]).astimezone(post_schedule.tz()).date()
                 for p in booked if p["kind"] == "reel" and p.get("due_at")}
        slot = post_schedule.fmt(post_schedule.next_reel_slot(datetime.now(timezone.utc), taken))
    except Exception:  # a schedule misconfiguration never breaks the dashboard
        return still_to_post(booked)
    return f"next free Reel slot {slot} · {still_to_post(booked)}"


HEARTBEAT_KEY = "heartbeat_at"
DIGEST_KEY = "weekly_digest"


def heartbeat_line(db: PipelineDatabase) -> Optional[str]:
    """The daily heartbeat's line on the dashboard, for the day after it beat (it replaced a message that
    repeated the full status: operator, 2026-10-25)."""
    from datetime import datetime, timedelta, timezone
    at = db.get_setting(HEARTBEAT_KEY)
    try:
        when = datetime.fromisoformat(at) if at else None
    except ValueError:
        when = None
    if not when or datetime.now(timezone.utc) - when > timedelta(hours=26):
        return None
    from .local_time import show
    return f"💓 {html.escape(show(at, '%H:%M'))} heartbeat · daemon running"


def record_heartbeat(db: PipelineDatabase) -> None:
    from datetime import datetime, timezone
    db.set_setting(HEARTBEAT_KEY, datetime.now(timezone.utc).isoformat())


def weekly_digest(db: PipelineDatabase) -> Optional[Dict[str, str]]:
    """This week's digest ({"text", "label", "at"}) while it is under a week old, else None."""
    from datetime import datetime, timedelta, timezone
    try:
        record = json.loads(db.get_setting(DIGEST_KEY) or "{}")
        if record and datetime.now(timezone.utc) - datetime.fromisoformat(record["at"]) <= timedelta(days=7):
            return record
    except (ValueError, KeyError, TypeError):
        pass
    return None


def record_weekly_digest(db: PipelineDatabase, text: str) -> None:
    """Keeps the digest for the dashboard's 📰 Weekly digest panel (it was its own message for a week)."""
    from datetime import datetime, timezone
    from .local_time import show
    now = datetime.now(timezone.utc).isoformat()
    db.set_setting(DIGEST_KEY, json.dumps({"text": text, "at": now, "label": show(now, "%a %d %b")}))


def startup_dashboard_card(db: PipelineDatabase, mode: str = "bot", time_str: Optional[str] = None,
                           timezone_str: Optional[str] = None, assets_dir: Path = ASSETS_DIR) -> Tuple[str, List[List[Dict[str, str]]]]:
    """Keep the dashboard heading, with startup state in its body."""
    started = (f"🟢 Daemon started: daily run at {html.escape(time_str or '09:00')} {html.escape(timezone_str or 'UTC')}"
               if mode == "daemon" else "🟢 Bot started (commands and reviews; no scheduled runs)")
    text, buttons = dashboard_text_and_buttons(db, None, assets_dir=assets_dir)
    heading, _, body = text.partition("\n\n")
    return heading + "\n\n" + started + "\n\n" + body, buttons


RUN_STARTING_KEY = "run_starting"  # the topic a queue Render just started, until its worker marks it
RUN_STARTING_SECONDS = 15 * 60


def topic_refs(args: str) -> List[str]:
    """Topic references in a /queue argument: numbers or ids separated by spaces or commas, and number ranges
    ("7-9"; at most 50 a range). One reference for anything else."""
    out: List[str] = []
    for part in re.split(r"[\s,]+", (args or "").strip()):
        m = re.fullmatch(r"(\d+)-(\d+)", part)
        if m and int(m.group(1)) <= int(m.group(2)) <= int(m.group(1)) + 49:
            out += [str(n) for n in range(int(m.group(1)), int(m.group(2)) + 1)]
        elif part:
            out.append(part)
    return out


def make_command_handler(db: PipelineDatabase, next_run: Callable[[], Optional[str]],
                         trigger_run: Callable[[], str], send_photo: Optional[SendPhoto] = None,
                         assets_dir: Path = ASSETS_DIR,
                         voice_quota: Callable[[], Optional[dict]] = lambda: None,
                         apply_updates: Optional[Callable[[], str]] = None,
                         propose_topics: Optional[Callable[[], str]] = None,
                         run_actions: Optional[dict] = None,
                         produce_batch: Optional[Callable[[int], str]] = None,
                         send_audio: Optional[SendPhoto] = None,
                         send_message: Optional[Callable[[str, Optional[list]], Any]] = None,
                         delete_message: Optional[Callable[[int], bool]] = None) -> Callable[[str, str], str]:
    active_proposal_msg_ids: List[int] = []
    undo_stack: List[Dict[str, Any]] = []

    def busy_topic(starting: Optional[str] = None) -> Optional[str]:
        """The topic being made now: the live run's, one marked in production, or the one just started (its
        job may not have begun yet, so its status still says queued)."""
        if starting:
            return starting
        prog = db.get_active_progress() if hasattr(db, "get_active_progress") else None
        if prog and prog.get("run_id"):
            run = db.get_run(prog["run_id"]) or {}
            if run.get("topic_id"):
                return run["topic_id"]
        # A topic stays "in_production" while its video waits for review, so the mark alone is not a run being
        # made: the queue said "Rendering #8 · progress" all day for a run waiting for review (operator,
        # 2026-10-25). Marked counts only while its newest run is still being made, or none has begun.
        recent = db.list_recent_runs(limit=40)
        for t in db.list_all_topics():
            if t.get("status") != "in_production":
                continue
            newest = next((r for r in recent if r.get("topic_id") == t["id"]), None)
            if newest is None or newest.get("status") in ("producing", "pending", "started"):
                return t["id"]
        # A run started from the queue whose worker has not yet marked its topic (a second Render tap on
        # another page still showed "Render #20 Now"): the record kept for its first minutes.
        try:
            record = json.loads(db.get_setting(RUN_STARTING_KEY) or "{}")
        except ValueError:
            record = {}
        recent = record.get("at") and clock.time() - float(record["at"]) < RUN_STARTING_SECONDS
        return record.get("topic") if recent else None

    def render_queue_text_and_buttons(show_all: bool = False, page: int = 0,
                                      starting: Optional[str] = None) -> Tuple[str, List[Dict[str, str]]]:
        busy = busy_topic(starting)

        def _readiness(topic_id: str) -> str:
            if topic_id == busy:
                return "🔄 In production"
            try:
                td = _resolve_topic_dir(topic_id, assets_dir, db=db)
                if td.exists():
                    cr = load_credits(td)
                    imgs = cr.get("images", [])
                    # The video's own count (image_review.readiness, 2026-10-28), not every photo in the set.
                    r = readiness(imgs, bool(cr.get("reviewed")))
                    if r["ready"]:
                        return "🟢 Ready to render"
                    if r["pending"]:
                        return f"🟢 Ready to render · {r['unchecked']} photos get their check first"
                    if cr.get("reviewed"):
                        return f"🟠 Short of usable photos ({r['usable']} of {MIN_SCENE_IMAGES})"
                    elif imgs:
                        return "🟡 Needs image review"
            except Exception:
                pass
            return "🔵 Script Ready"

        topics = db.list_all_topics()
        queued = [t for t in topics if t.get("status") == "queued"]
        stale = stale_topics(db)
        requeueable = [t for t in topics if t.get("status") in ("rejected", "failed", "published", "archived", "cancelled")
                       or t["id"] in stale]
        next_t = None
        try:
            next_t = db.get_next_topic()
        except Exception:
            pass
        topic_serial = {t["id"]: i for i, t in enumerate(topics, 1)}
        lines = [f"📋 <b>Queue</b> · {len(queued)} queued", ""]
        limit = max(5, len(queued)) if show_all else 5  # /queue all: every topic on one page
        page = max(0, min(page, max(0, (len(queued)-2)//limit)))
        lines[0] += f" · page {page+1} of {max(1, (max(0,len(queued)-1)+limit-1)//limit)}"

        buttons = []
        if next_t and (next_t.get("status") == "queued" or next_t["id"] == busy):
            sn = topic_serial.get(next_t['id'], 1)
            p_badge = " [🚀 Priority #1]" if next_t.get("priority", 0) > 0 else ""
            r_badge = _readiness(next_t['id'])
            lines.append(f"🎯 <b>Next:</b> {sn}. {html.escape(next_t['title'])}{p_badge}\n    <b>{r_badge}</b>\n")
            if busy:  # Telegram has no disabled button: it says what is happening and opens the progress instead
                buttons.append({"text": f"⏳ Rendering #{topic_serial.get(busy, sn)} · progress", "callback_data": "cprog:"})
            else:
                buttons.append({"text": f"⚡ Render #{sn} now", "callback_data": f"qrender:{next_t['id']}"})  # as on the dashboard
            remaining = [t for t in queued if t["id"] != next_t["id"]]
            if remaining:
                lines.append("<b>Other queued topics</b>")
                for t in remaining[page*limit:(page+1)*limit]:
                    s = topic_serial.get(t['id'], '')
                    p_badge = " [🚀 Priority]" if t.get("priority", 0) > 0 else ""
                    rb = _readiness(t['id'])
                    lines.append(f"  {s}. {html.escape(t['title'])}{p_badge}\n    <i>{rb}</i>")
                if len(remaining) > limit:
                    lines.append(f"  <i>{len(remaining)} other topics · use the page buttons below</i>")
                # Add bump button for the second item
                second_t = remaining[0]
                buttons.append({"text": f"⬆ Bump #{topic_serial.get(second_t['id'], 2)}", "callback_data": f"qbump:{second_t['id']}"})
            if next_t["id"] != busy:  # never drop the topic being made
                buttons.append({"text": f"🗑 Drop #{sn}", "callback_data": f"qdrop:{next_t['id']}"})
            elif remaining:  # while it is made, Drop pairs with Bump on the topic after it (Bump alone was offered)
                after = remaining[0]
                buttons.append({"text": f"🗑 Drop #{topic_serial.get(after['id'], 2)}", "callback_data": f"qdrop:{after['id']}"})
        elif queued:
            for t in queued[page*limit:(page+1)*limit]:
                s = topic_serial.get(t['id'], '')
                rb = _readiness(t['id'])
                lines.append(f"  {s}. {html.escape(t['title'])}\n    <i>{rb}</i>")
            if len(queued) > limit + 1:
                lines.append(f"  <i>…and {len(queued) - (limit + 1)} more · <code>/queue all</code></i>")
        else:
            lines.append("  <i>The queue is empty. Add one with <code>/add Title</code> or research ideas with <code>/ideas research</code>.</i>")

        # Several episodes back to back (/produce N, operator 2026-10-24) from the panel too: the sizes offered
        # never exceed what is queued; a running batch says how many more will follow. Typed, not tapped: the
        # Produce 3/5/7 buttons bloated the queue (operator, 2026-10-25).
        if produce_batch is not None and len(queued) >= 2:
            try:
                left = int(db.get_setting("produce_remaining") or 0)
            except (TypeError, ValueError):
                left = 0
            if busy and left > 0:
                lines.append(f"\n🎬 <b>Batch in progress</b> · {left} more episode(s) start after this one, each after your review decision.")
            elif not busy:
                lines.append("\n<b>Several back to back:</b> send <code>/produce N</code> (1–14). "
                             "Each episode comes to you for review; the next starts after your decision.")

        if requeueable:
            lines.append(f"\n<b>Can be restored</b> · {len(requeueable)}")
            for t in (requeueable if show_all else requeueable[:5]):
                s = topic_serial.get(t['id'], '')
                st = t.get("status", "")
                said = STALE_WORDS.get(stale[t["id"]], "its last run is over") if t["id"] in stale else STATUS_WORDS.get(st, st)
                lines.append(f"  {s}. {html.escape(t['title'])} · {html.escape(said)}")
            if len(requeueable) > 5 and not show_all:
                lines.append(f"  <i>…and {len(requeueable) - 5} more · <code>/queue all</code></i>")
            example = topic_serial.get(requeueable[0]["id"], 1)
            lines.append(f"\nRestore one with its number: <code>/queue requeue {example}</code>")

        pages = max(1, (max(0, len(queued) - 1) + limit - 1) // limit)
        # Rows, not a flat list: Render beside Refresh, Bump beside Drop, every page button on one row.
        render = [b for b in buttons if b["callback_data"].startswith(("qrender:", "cprog:"))]
        changes = [b for b in buttons if b["callback_data"].startswith(("qbump:", "qdrop:"))]
        rows = [render + [{"text": "🔄 Refresh", "callback_data": "qrefresh:"}]]
        from . import config as app_config
        if render and not busy and app_config.documentary_enabled():  # the same topic as a Reel plus the 16:9 documentary (paused while rebuilt)
            rows.append([{"text": "🎬 Reel + 16:9 documentary", "callback_data": "qdoc:" + render[0]["callback_data"].partition(":")[2]}])
        if changes:
            rows.append(changes)
        # New topics are found from the queue: Ideas sits under the pages, above the way home.
        return "\n".join(lines), rows + pager("qpage:", page, pages) + [[{"text": "💡 Ideas", "callback_data": "cideas:"}], [HOME]]

    """run_actions: button actions on a run, e.g. {"pubok": fn(run_id) -> reply, "pubno": ..., "resend": ...}."""
    def send_sheet(topic_dir: Path) -> bool:
        if send_photo is None:
            return False
        key = topic_dir.name
        serial = str({t["id"]: i for i, t in enumerate(db.list_all_topics(), 1)}.get(topic_dir.name, topic_dir.name))
        caption = review_caption(topic_dir, ref_hint=serial, title=(db.get_topic_by_id(topic_dir.name) or {}).get("title"))
        write_contact_sheet(topic_dir, load_credits(topic_dir)["images"])
        from .telegram_images import review_digest
        key += "@" + review_digest(topic_dir)
        # Edit right from the sheet (copy the command with this topic filled in, add the numbers), or go
        # up one level: the topic's images (fetch, upload, review) or another topic.
        return send_photo(topic_dir / CONTACT_SHEET, caption,
                          [[{"text": "✅ Approve", "callback_data": f"imgok:{key}"},
                            {"text": "← Topic pictures", "callback_data": f"imgcancel:{key}"}],
                           [{"text": "📎 Copy drop command", "copy_text": {"text": f"/photos {serial} drop "}},
                            {"text": "📎 Copy keep command", "copy_text": {"text": f"/photos {serial} keep "}}],
                           [{"text": "🖼 Other topics", "callback_data": "reviewpick:0"}]])

    def match_topic(topics, ref):
        """A topic by its /queue number or id (the single-topic commands' own rule)."""
        if ref.isdigit() and 0 <= int(ref) - 1 < len(topics):
            return topics[int(ref) - 1]
        return next((t for t in topics if t["id"].lower() == ref.lower()), None)

    def queue_batch(command, refs):
        """/queue drop|requeue|bump with several topics ("7 8 11", "7,8", "7-9"; operator, 2026-10-28). Every
        number is resolved before anything changes, so the numbering can't shift under the batch; one /queue
        undo reverses a whole drop."""
        topics = db.list_all_topics()
        found, missing = [], []
        for ref in refs:
            t = match_topic(topics, ref)
            if t is None:
                missing.append(ref)
            elif t not in found:
                found.append(t)
        done, refused = [], []
        if command == "drop_topic":
            for t in found:
                (done if db.drop_topic(t["id"]) else refused).append(t)
            if done:
                undo_stack.append({"type": "qdrop_batch", "topic_ids": [t["id"] for t in done],
                                   "desc": f"Drop of {len(done)} topics"})
            head = f"🗑 Archived {len(done)} topic(s) from the queue"
        elif command == "requeue":
            stale = stale_topics(db)
            for t in found:
                (done if db.requeue_topic(t["id"], stale=t["id"] in stale) else refused).append(t)
            head = f"🔄 Restored {len(done)} topic(s) to the queue"
        else:  # bump: the last one first, so the first one named ends up at the front
            for t in reversed(found):
                (done if db.bump_topic(t["id"]) else refused).append(t)
            done.reverse()
            head = f"🚀 Moved {len(done)} topic(s) to the front, in the order given"
        lines = [head + (":" if done else ".")] + [f"• {html.escape(t['title'])}" for t in done]
        if refused:
            lines.append("\nNot changed (their state doesn't allow it): " + ", ".join(html.escape(t["title"]) for t in refused))
        if missing:
            lines.append("\nNot found: " + ", ".join(html.escape(m) for m in missing) + ". Send /queue for the numbers.")
        if command == "drop_topic" and done:
            lines.append("\n<code>/queue undo</code> restores them all.")
        return "\n".join(lines)

    def handle(command: str, args: str) -> str:
        from .telegram_commands import normalise_command
        command, args = normalise_command(command, args)  # aliases and subcommands (2026-10-25 overhaul)
        if command == "runstop":
            run_id = db.resolve_run(args)
            return ("⏹ <b>Cancel this run after its current stage?</b>\n\n"
                    f"{html.escape(db.run_label(run_id))}\nAlready incurred provider usage is retained. An in-flight stage may finish. This does not cancel an upload.",
                    [[{"text": "Confirm cancellation", "callback_data": f"runstopok:{db.run_key(run_id)}"}], [HOME]])
        if command == "runstopok":
            accepted = db.request_run_cancel(db.resolve_run(args))
            text = "Cancellation requested; the worker will acknowledge at its next stage boundary." if accepted else "This run is no longer producing. Inspect its current status; no upload was cancelled."
            return text, [[{"text": "Jobs", "callback_data": "jobs:"}], [HOME]]
        if command in ("qrender", "qdoc"):
            from . import config as app_config
            if command == "qdoc" and not app_config.documentary_enabled():  # a button left on an older message
                q_text, q_buttons = render_queue_text_and_buttons()
                return ("⏸ The 16:9 documentary is paused while it is rebuilt; nothing was started. "
                        "Use Render for the Reel.\n\n" + q_text), q_buttons
            current = db.get_next_topic()
            topics = db.list_all_topics()
            serials = {str(i): t["id"] for i, t in enumerate(topics, 1)}
            requested = serials.get(args.strip(), args.strip())
            if args.strip().isdigit() and (not current or current["id"] != requested):
                # Typed /render N: that topic, now (it refused any topic but the next, saying "the queue
                # changed" when it had not: review, 2026-10-25). It moves to the front, then starts.
                wanted = next((t for t in topics if t["id"] == requested), None)
                if not wanted or wanted.get("status") != "queued":
                    text, buttons = render_queue_text_and_buttons()
                    why = "there is no such topic" if not wanted else f"it is {STATUS_WORDS.get(wanted.get('status'), wanted.get('status'))}, not queued"
                    return f"⚠️ Topic {html.escape(args.strip())} can't be rendered: {html.escape(why)}.\n\n" + text, buttons
                db.bump_topic(requested)
                current = db.get_next_topic()
            if not current or current["id"] != requested:
                # A Render button made for an earlier queue: the topic it names is no longer next
                text, buttons = render_queue_text_and_buttons()
                return "⚠️ That button was for an earlier queue; the next topic is now different. Check it below.\n\n" + text, buttons
            # The documentary is made after the Reel and reviewed separately; approving it uploads it to YouTube.
            res = trigger_run("dual") if command == "qdoc" else trigger_run()
            started = "already" not in res.lower()  # else another run is busy: the panel shows that one
            if started:
                db.set_setting(RUN_STARTING_KEY, json.dumps({"topic": current["id"], "at": clock.time()}))
            q_text, q_buttons = render_queue_text_and_buttons(starting=current["id"] if started else None)
            return f"⚡ <b>Run:</b> {html.escape(res)}\n\n{q_text}", q_buttons
        if command == "qbump":
            if not db.bump_topic(args):
                return "Queue changed; this item cannot be bumped.", [[HOME]]
            q_text, q_buttons = render_queue_text_and_buttons()
            return f"🚀 Bumped topic to #1 in queue!\n\n{q_text}", q_buttons
        if command == "qdrop":
            t_obj = db.get_topic_by_id(args)
            if not db.drop_topic(args):
                return "Queue changed; this item cannot be dropped.", [[HOME]]
            undo_stack.append({"type": "qdrop", "topic_id": args, "desc": f"Drop of {t_obj['title'] if t_obj else args}"})
            q_text, q_buttons = render_queue_text_and_buttons()
            return f"🗑 Dropped topic from queue (<code>/queue undo</code> restores it).\n\n{q_text}", q_buttons
        if command in ("qrefresh", "qpage"):
            return render_queue_text_and_buttons(page=int(args) if args.isdigit() else 0)
        if command in ("pickbump", "pickdrop"):
            # One paged topic picker for bump and drop: full numbered titles in the message (they wrap),
            # number buttons, the shared pager, back to the queue.
            verb = "bump" if command == "pickbump" else "drop"
            topics = db.list_all_topics()
            serial = {t["id"]: i for i, t in enumerate(topics, 1)}
            # Bump leaves out the topic already next (by rank, not the first in the bank: it dropped #7 and
            # offered #28, already next: review, 2026-10-25)
            nxt = db.get_next_topic() if verb == "bump" else None
            queued = [t for t in topics if t.get("status") == "queued" and not (nxt and t["id"] == nxt["id"])]
            if not queued:
                return "📋 Nothing in the queue to " + verb + ".", [[{"text": "← Queue", "callback_data": "qrefresh:"}], [HOME]]
            pages = -(-len(queued) // 8)
            page = min(int(args) if args.isdigit() else 0, pages - 1)
            shown = queued[page * 8:page * 8 + 8]
            title = ("⬆ <b>Bump a topic to the front of the queue</b>" if verb == "bump" else
                     "🗑 <b>Drop a topic from the queue</b> (<code>/queue undo</code> restores it)")
            text = title + (f" · page {page + 1} of {pages}" if pages > 1 else "") + "\n\n" + "\n".join(
                f"<b>{serial[t['id']]}.</b> {html.escape(t['title'])}" for t in shown)
            numbers = [{"text": str(serial[t["id"]]), "callback_data": f"q{verb}:{t['id']}"} for t in shown]
            return text, ([row for row in (numbers[:4], numbers[4:]) if row] + pager(command + ":", page, pages)
                          + [[{"text": "← Queue", "callback_data": "qrefresh:"}], [HOME]])
        if command in ("bump", "drop_topic", "requeue") and len(topic_refs(args)) > 1:
            return queue_batch(command, topic_refs(args))
        if command == "bump":
            ref = args.strip()
            if not ref:
                queued = [t for t in db.list_all_topics() if t.get("status") == "queued"]
                if len(queued) > 1:
                    text, buttons = handle("pickbump", "0")
                    if send_message:
                        send_message(text, buttons)
                        return ""
                    return text, buttons
                return (
                    "⬆ <b>Bump a topic</b>\n\nTap to copy &amp; edit: <code>/queue bump 2</code>\n\n"
                    "<i>Usage: <code>/queue bump &lt;number or topic_id&gt;</code>\n"
                    "Tip: Tap the code box above to copy instantly to input box.</i>"
                )
            topics = db.list_all_topics()
            match = None
            if ref.isdigit():
                idx = int(ref) - 1
                if 0 <= idx < len(topics):
                    match = topics[idx]
            if not match:
                for t in topics:
                    if t["id"] == ref or t["id"].lower() == ref.lower():
                        match = t
                        break
            if not match:
                return f"⚠️ Topic '{html.escape(ref)}' not found in database."
            if not db.bump_topic(match["id"]):
                return "Queue changed; topic cannot be bumped."
            return f"🚀 Promoted <b>{html.escape(match['title'])}</b> (<code>{html.escape(match['id'])}</code>) to priority #1 in the queue!"
        if command == "drop_topic":
            ref = args.strip()
            if not ref:
                return handle("pickdrop", "0")
            topics = db.list_all_topics()
            match = None
            if ref.isdigit():
                idx = int(ref) - 1
                if 0 <= idx < len(topics):
                    match = topics[idx]
            if not match:
                for t in topics:
                    if t["id"] == ref or t["id"].lower() == ref.lower():
                        match = t
                        break
            if not match:
                return f"⚠️ Topic '{html.escape(ref)}' not found."
            if not db.drop_topic(match["id"]):
                return "Queue changed; topic cannot be archived."
            undo_stack.append({"type": "qdrop", "topic_id": match["id"], "desc": f"Drop of {match['title']}"})
            return f"🗑 Archived <b>{html.escape(match['title'])}</b> from the queue. <code>/queue undo</code> restores it."
        if command == "undo":
            if not undo_stack:
                return "ℹ️ Nothing to undo."
            last = undo_stack.pop()
            if last["type"] == "qdrop_batch":
                back = [t for t in last["topic_ids"] if db.requeue_topic(t)]
                return (f"↩️ <b>Undone:</b> {len(back)} of {len(last['topic_ids'])} topic(s) back in the queue"
                        + ("" if len(back) == len(last["topic_ids"]) else " (the rest changed state since)") + ".")
            if last["type"] == "qdrop":
                if not db.requeue_topic(last["topic_id"]):
                    return "Topic state changed; the old action cannot be undone."
                return f"↩️ <b>Undone:</b> Restored {html.escape(last['desc'])} back to queue!"
            if last["type"] == "uskip":
                return f"↩️ <b>Undone:</b> Restored skipped wonder {html.escape(last.get('site_id', ''))}."
            if last["type"] == "tskip":
                return f"↩️ <b>Undone:</b> Restored skipped proposal {html.escape(last.get('slug', ''))}."
            return f"↩️ <b>Undone:</b> Restored {html.escape(last.get('desc', 'action'))}."
        if command == "covers":
            # Covers are chosen for a video awaiting review (Lock refuses any other run), so that is what
            # this opens; otherwise the newest run that has covers, as a preview. A run folder without
            # covers (cancelled, failed, still producing) is never chosen.
            ref = args.strip()
            from .config import OUTPUT_DIR
            back = [[{"text": "🎬 Reviews", "callback_data": "creviews:"}], [HOME]]

            def has_covers(d):
                return any((d / f"thumbnail_variant_{v}.jpg").exists() for v in ("a_blueprint", "b_intrigue", "c_minimal"))

            def run_folder(run):
                if run and run.get("master_video_path"):
                    d = Path(run["master_video_path"]).resolve().parent
                    if not d.is_relative_to(OUTPUT_DIR.resolve()):
                        raise ValueError("Run output lies outside the configured output root")
                    return d
                return None

            run_dir = None
            cover_page = int(ref[1:]) if re.fullmatch(r"p\d+", ref or "") else None
            if cover_page is not None:
                ref = ""
            if ref:
                run = db.get_run(db.resolve_run(ref))
                run_dir = run_folder(run)
                if run_dir is None:
                    topic_dir = _resolve_topic_dir(ref, assets_dir, db=db)
                    runs = [p for p in OUTPUT_DIR.glob(f"run_{topic_dir.name}_*") if p.is_dir() and has_covers(p)]
                    run_dir = max(runs, key=lambda p: p.stat().st_mtime) if runs else None
                if not run_dir or not has_covers(run_dir):
                    return "🎨 This run has no covers yet (they are made when the video is rendered).", back
            else:
                waiting = [d for d in (run_folder(db.get_run(a["run_id"])) for a in db.list_pending_approvals()) if d and has_covers(d)]
                if len(waiting) > 1:  # every video in review, five to a page (the sixth had no button: 2026-10-25)
                    pages = max(1, (len(waiting) + LIST_PAGE - 1) // LIST_PAGE)
                    page = max(0, min(cover_page or 0, pages - 1))
                    shown = waiting[page * LIST_PAGE:(page + 1) * LIST_PAGE]
                    first = page * LIST_PAGE + 1
                    text = ("🎨 <b>Covers</b>" + (f" · page {page + 1} of {pages}" if pages > 1 else "")
                            + "\nChoose the video in review.\n\n"
                            + "\n".join(f"{i}. {html.escape(db.run_label(d.name))}" for i, d in enumerate(shown, first)))
                    return text, (number_buttons([f"ccov:{db.run_key(d.name)}" for d in shown], first)
                                  + pager("ccov:p", page, pages) + back)
                if waiting:
                    run_dir = waiting[0]
                else:
                    rendered = sorted((p for p in OUTPUT_DIR.glob("run_*") if p.is_dir() and not p.name.startswith("_") and has_covers(p)),
                                      key=lambda p: p.stat().st_mtime, reverse=True)
                    if not rendered:
                        return ("🎨 <b>Covers</b>\n\nNo rendered video has covers yet. They are made when a video is "
                                "rendered, and chosen from its review card."), back
                    run_dir = rendered[0]
            run_rec = db.get_run(run_dir.name) or {}
            reviewable = run_rec.get("status") == "pending_approval" and (db.get_approval(run_dir.name) or {}).get("decision") == "pending"
            var_a = run_dir / "thumbnail_variant_a_blueprint.jpg"
            var_b = run_dir / "thumbnail_variant_b_intrigue.jpg"
            var_c = run_dir / "thumbnail_variant_c_minimal.jpg"
            # The variant the video's cover is now (it always said B: review, 2026-10-25)
            chosen = chosen_cover(run_dir)
            selected = chosen or ("b" if var_b.exists() else "a" if var_a.exists() else "c")   # the one previewed
            mark = lambda v, name: ("✓ " if v == chosen else "📌 ") + f"{v.upper()}: {name}"  # noqa: E731
            buttons = [
                [
                    {"text": mark("a", "Blueprint"), "callback_data": f"cov:{run_dir.name}:a"},
                    {"text": mark("b", "Intrigue"), "callback_data": f"cov:{run_dir.name}:b"},
                ],
                [
                    {"text": mark("c", "Minimal"), "callback_data": f"cov:{run_dir.name}:c"},
                    {"text": "🔄 Refresh", "callback_data": "ccov:"},
                ],
            ]
            if reviewable:
                buttons.append([{"text": f"🔒 Lock Variant {selected.upper()}", "callback_data": f"covlock:{run_dir.name}:{selected}"}])
            buttons.append([HOME])
            label = html.escape(db.run_label(run_dir.name)) if run_rec else f"<code>{html.escape(run_dir.name)}</code>"
            caption = (
                f"🎨 <b>Cover Studio</b> · {label}\n\n"
                f"Previewing: <b>Variant {selected.upper()}</b>\n"
                + ("Tap A, B or C to flip the preview, or Lock to choose it for this video."
                   if reviewable else "<i>Preview only: this video isn't awaiting review, so its cover can't be "
                   "changed. Covers are chosen from a video's review card.</i>")
            )
            cover_to_show = {"a": var_a, "b": var_b, "c": var_c}[selected]
            if not cover_to_show.exists():
                cover_to_show = var_b if var_b.exists() else (var_a if var_a.exists() else var_c)
            if send_photo and cover_to_show.exists():
                send_photo(cover_to_show, caption, buttons, cache_key=f"cover:{run_dir.name}:{selected}")
                return ""
            return caption
        if command in ("cov", "covlock"):
            parts = args.split(":")
            if len(parts) >= 2:
                r_name, var_letter = parts[0], parts[1].lower()
                from .config import OUTPUT_DIR
                if Path(r_name).name != r_name or r_name in (".","..") or var_letter not in ("a","b","c"):
                    raise ValueError("Invalid cover reference")
                r_dir = (OUTPUT_DIR / r_name).resolve()
                if r_dir.parent != OUTPUT_DIR.resolve():
                    raise ValueError("Invalid cover path")
                run = db.get_run(r_name)
                if not run or run.get("status") != "pending_approval":
                    return "This run is not awaiting review; its cover cannot be changed."
                if (db.get_approval(r_name) or {}).get("decision") != "pending":
                    return "This review is closed; its cover cannot be changed."
                if command == "cov":
                    return "Preview selection only. Use the cover panel to preview and Lock to commit."
                var_file = (r_dir / f"thumbnail_variant_{var_letter}_blueprint.jpg" if var_letter == "a"
                            else r_dir / f"thumbnail_variant_{var_letter}_intrigue.jpg" if var_letter == "b"
                            else r_dir / f"thumbnail_variant_{var_letter}_minimal.jpg")
                if var_file.exists():
                    approval = db.get_approval(r_name) or {}
                    with db._conn() as conn:
                        changed = conn.execute("UPDATE approvals SET telegram_message_id=NULL, revision=revision+1 WHERE run_id=? AND decision='pending' AND revision=?", (r_name, approval.get("revision",0))).rowcount
                    if not changed:
                        return "Review changed; cover was not modified."
                    import shutil
                    from PIL import Image
                    shutil.copy2(var_file, r_dir / "thumbnail_cover.jpg")
                    try:
                        im = Image.open(r_dir / "thumbnail_cover.jpg")
                        w, h = im.size
                        top = max(0, min(h - w, (h // 2) - (w // 2)))
                        im.crop((0, top, w, top + w)).save(r_dir / "thumbnail_profile_grid.jpg", quality=95)
                    except Exception:
                        pass
                    style_name = "Forensic Blueprint" if var_letter == "a" else "Discovery Intrigue" if var_letter == "b" else "Cinematic Minimal"
                    action_word = "locked" if command == "covlock" else "selected and locked"
                    return f"✅ Cover {action_word}: <b>Variant {var_letter.upper()} ({style_name})</b> for <code>{html.escape(r_name)}</code>. Resend this run for review before approving."
            return "Cover was not changed: the requested variant is missing or invalid."
        if command == "audit":
            limit = 10
            if args.strip().isdigit():
                limit = min(50, max(1, int(args.strip())))
            logs = db.list_audit_logs(limit=limit)
            if not logs:
                return "🛡️ No audit log entries recorded yet."
            lines = [f"🛡 <b>Activity</b> · last {len(logs)}", ""]
            for l in logs:
                st_icon = "🟢" if l.get("status") == "allowed" else "🔴"
                ts = show_time(l.get("created_at"), "%d %b %H:%M:%S") if l.get("created_at") else ""   # as on every screen
                actor = str(l.get("username") or "").strip()
                uid = str(l.get("user_id") or "").strip()
                if not actor or actor in ("-", "_", "none", "unknown"):
                    actor = f"op:{uid[-4:]}" if uid else "operator"
                elif not actor.startswith("@") and not actor.startswith("op:"):
                    actor = f"@{actor}"

                act = str(l.get("action") or l.get("event_type") or "").strip()
                raw_pl = str(l.get("payload") or "").strip()
                header = f"{st_icon} <code>{ts}</code> • <b>{html.escape(actor)}</b>"
                if act:
                    header += f" (<i>{html.escape({'callback_query': 'button'}.get(act, act))}</i>)"

                if len(raw_pl) <= 35 and "\n" not in raw_pl:
                    # What was done, then the raw record as evidence (it showed only "cdash:", "rsn:16:other").
                    name = audit_action_name(raw_pl)
                    lines.append(f"{header} → " + (f"{html.escape(name)} " if name else "") + f"<code>{html.escape(raw_pl)}</code>")
                else:
                    lines.append(f"{header}:\n<blockquote expandable>{html.escape(raw_pl)}</blockquote>")
            return "\n".join(lines)
        if command in ("start", "dashboard"):  # /start is Telegram's own first command
            dash_text, dash_buttons = dashboard_text_and_buttons(db, next_run(), assets_dir=assets_dir)
            if send_message:
                send_message(dash_text, dash_buttons)
                return ""
            return dash_text
        if command == "cdash":
            dash_text, _ = dashboard_text_and_buttons(db, next_run(), assets_dir=assets_dir)
            return dash_text
        if command in ("accounts", "caccounts"):
            from . import accounts
            accounts.check(db, force=True)
            return "🔗 <b>Accounts</b>\n\n" + accounts.status_line(db), [[{"text": "🔄 Check again", "callback_data": "caccounts:"}], [HOME]]
        if command in ("help", "chelp", "commands"):
            from .telegram_commands import GROUPS, help_text
            if not send_message and command == "help":
                return HELP
            from .telegram_commands import find_group
            group = find_group(args) or "Everyday"
            others = [{"text": f"📖 {name}", "callback_data": f"chelp:{name}"} for name in GROUPS if name != group]
            return (f"❓ <b>Help</b> · {html.escape(group)}\n\n" + help_text(group),
                    [others[i:i + 2] for i in range(0, len(others), 2)] + [[HOME]])
        if command in ("progress", "cprog"):
            active_prog = db.get_active_progress() if hasattr(db, "get_active_progress") else None
            if args and (not active_prog or db.resolve_run(args) != active_prog.get("run_id")):
                run = db.get_run(db.resolve_run(args)) or {}
                status = run.get("status", "")
                # What happened to it, in words: "Run status: producing" on a run nobody was making read as alive
                # (a development test stopped from outside, 2026-10-28)
                said = ("is no longer being made: the process making it ended without finishing. Nothing was posted; "
                        "it is cleared within 10 minutes and its topic goes back to the queue" if status == "producing"
                        else f"ended: {STATUS_WORDS.get(status, status)}" if status else "is no longer recorded")
                note = f"\n<blockquote expandable>{html.escape(short_reason(run.get('error') or '', 600))}</blockquote>" if run.get("error") else ""
                return (f"ℹ️ This progress card is no longer current: {html.escape(db.run_label(run['run_id']) if run else 'the run')} "
                        f"{html.escape(said)}.{note}", [[HOME]])
            if not active_prog:
                return ("⏸ <b>Nothing is being made right now</b>\n\nStart the next topic from the queue, or check "
                        "the last run in Full status.",
                        [[{"text": "📋 Queue", "callback_data": "qrefresh:"}, {"text": "📊 Full status", "callback_data": "cstat:"}], [HOME]])
            step = active_prog.get("step", 1)
            total_steps = active_prog.get("total_steps", 5)
            topic_title = active_prog.get("topic_title", "Unknown Topic")
            run_id = active_prog.get("run_id", "active_run")
            details = active_prog.get("details", "")
            mode = db.get_setting("run_mode:" + run_id)
            mode_text = "🧪 Dry run · publishing/scheduling skipped · generation may incur API costs" if mode == "dry_run" else "🔴 Live production" if mode == "live" else "⚠️ Run mode not recorded"
            worker_state = active_prog.get("worker_state", "unverified")
            if worker_state != "running":
                note = "The worker has exited or its PID was reused." if worker_state == "interrupted" else "Worker ownership is not recorded or cannot be verified."
                updated = show_time(active_prog.get("updated_at"), "%d %b %H:%M %Z") if active_prog.get("updated_at") else "unknown"
                return (f"⚠️ <b>Last recorded progress — {worker_state}</b>\n\n{html.escape(note)}\n"
                        f"{html.escape(topic_title)} · step {step}/{total_steps}\n"
                        f"Last update: {html.escape(updated)}\n"
                        "This is not confirmation of an active run. Check Jobs before starting another.",
                        [[{"text": "🧰 Jobs", "callback_data": "jobs:"}], [HOME]])

            from .telegram_bot import PROGRESS_STEPS as milestones
            lines = [
                mode_text,
                f"{hourglass()} <b>Progress</b>\n\n<b>{html.escape(topic_title)}</b> · started "
                f"{show_time(active_prog.get('start_time'), '%H:%M') if active_prog.get('start_time') else 'earlier'}",
                "",
            ]
            from .telegram_bot import progress_estimate, step_running_note
            bar, pct, eta, elapsed_sec, step_elapsed = progress_estimate(db, active_prog, step, total_steps)
            for idx, m in enumerate(milestones, start=1):
                if idx < step:
                    lines.append(f"✅ <b>Step {idx}/{total_steps}:</b> {m}")
                elif idx == step:
                    detail_str = f" <i>({html.escape(details)})</i>" if details else ""
                    lines.append(f"🔄 <b>Step {idx}/{total_steps}:</b> {m}...{detail_str}{step_running_note(step_elapsed)}")
                else:
                    lines.append(f"◽ <b>Step {idx}/{total_steps}:</b> {m}")

            lines.append(f"\nProgress: <code>[{bar}] {pct}%</code>")
            lines.append(f"⏱ <b>Elapsed:</b> {elapsed_sec // 60:02d}:{elapsed_sec % 60:02d} · <b>Time left:</b> {eta}")
            lines.append(f"Last stage update: {show_time(active_prog.get('updated_at'), '%H:%M:%S %Z') if active_prog.get('updated_at') else 'unknown'}")
            return "\n".join(lines)
        if command in ("ideas", "cideas"):
            text = ("💡 <b>Ideas</b>\n\nResearch new ones, browse UNESCO marvels, or decide on ideas "
                    "already waiting (each has Add/Skip).")
            started = running_job(db, "topic-research")
            research = ({"text": "🧰 Research running: Jobs", "callback_data": "jobs:"} if started else
                        {"text": "🔬 Research 3 new ideas", "callback_data": "cpropose:"})
            if started:
                text += f"\n\n🔬 <b>Research is running</b> (started {started}). Its result opens here when it finishes; keep browsing meanwhile."
            buttons = [[research],
                       [{"text": "🏛️ UNESCO marvels", "callback_data": "cdisc:"},
                        {"text": "📥 Waiting ideas", "callback_data": "cprop:"}],
                       [HOME]]
            if command == "cideas":
                return text, buttons
            if send_message:
                send_message(text, buttons)
                return ""
            return text
        if command == "edit":
            run_id = db.resolve_run(args.strip())
            payload = (db.get_run(run_id) or {}).get("script_payload") or {}
            # A pasted template (not sent as a reply) goes to this review for a while: see EDIT_OPEN_KEY.
            db.set_setting(EDIT_OPEN_KEY, json.dumps({"run": args.strip(), "at": clock.time()}))
            return edit_prompt(payload, args.strip())
        if command == "edittext":
            key, _, text = args.partition(" ")
            fields = {"title": "title", "first comment": "first_comment", "carousel caption": "carousel_caption",
                      "caption": "caption"}
            # Several fields in one reply ("title: X; first comment: Y", or one per line): a reply that did
            # this set the title to the whole text (dev, 2026-10-23).
            # After a label's colon, only spaces on the same line: "title:" left blank above "first comment: …"
            # made the title the whole next line (blank fields are ignored instead).
            parts = re.split(r"(?:^|\n|;\s*)(title|first comment|carousel caption|caption)[ \t]*:[ \t]*",
                             text.strip(), flags=re.IGNORECASE)
            changes = {fields[label.lower()]: value.strip().rstrip(";").strip()
                       for label, value in zip(parts[1::2], parts[2::2]) if value.strip()}
            if not changes and text.strip():
                changes = {"caption": text.strip()}  # no label: the whole reply is the caption, as before
            action = (run_actions or {}).get("edit")
            if action is None:
                return "Editing works while the bot or daemon is running."
            return action(db.resolve_run(key), changes)
        if command == "cimprove":
            from . import improve
            text, buttons = improve.improve_text(db)
            return text, buttons + [[HOME]]
        if command in ("reviews", "creviews"):  # typed /reviews opens the same panel as the dashboard button
            pending = db.list_pending_approvals()
            timed_out = db.timed_out_reviews() if hasattr(db, "timed_out_reviews") else []
            candidates = post_candidates(db)
            if not pending and not timed_out and not candidates:
                return "No reviews are waiting.", [[HISTORY_BUTTON], [HOME]]
            if not pending and not timed_out:
                lines = ["🚀 <b>Post candidates</b> · finished dry runs of the last two days; each asks you to confirm.", ""]
                buttons = []
                for n, r in enumerate(candidates, 1):
                    lines.append(f"{n}. {html.escape(db.run_label(r['run_id']))}")
                    buttons.append([{"text": f"🚀 Post for real ({n})", "callback_data": f"postreal:{db.run_key(r['run_id'])}"}])
                return "\n".join(lines), buttons + [[HISTORY_BUTTON], [HOME]]
            # One numbered list, five to a page, a number button each (operator, 2026-10-25: every review is
            # listed; the list stopped at five and the buttons carried names cut to fit). Where the card is
            # can't be promised (the chat may have been cleared), so a number sends it again at the bottom.
            items = [("waiting", p["run_id"]) for p in pending] + [("timed_out", r["run_id"]) for r in timed_out]
            pages = max(1, (len(items) + LIST_PAGE - 1) // LIST_PAGE)
            page = max(0, min(int(args) if args.strip().isdigit() else 0, pages - 1))
            shown = items[page * LIST_PAGE:(page + 1) * LIST_PAGE]
            lines = [f"🎬 <b>Reviews waiting</b> · {len(items)}" + (f" · page {page + 1} of {pages}" if pages > 1 else ""),
                     "Tap a number to get that review card again at the bottom of the chat, with its video."]
            section = None
            for n, (kind, run_id) in enumerate(shown, page * LIST_PAGE + 1):
                if kind != section:
                    section = kind
                    if kind == "timed_out":  # its topic waits for this decision (TIMEOUT_HOLD_DAYS)
                        lines += ["", "⌛ <b>Timed out</b> · its topic waits for you; the card has Reject on it"]
                    else:
                        lines.append("")
                lines.append(f"{n}. {html.escape(db.run_label(run_id))}")
            buttons = number_buttons([f"resend:{db.run_key(run_id)}" for _, run_id in shown], page * LIST_PAGE + 1)
            return "\n".join(lines), buttons + pager("creviews:", page, pages) + [[HISTORY_BUTTON], [HOME]]
        if command in ("digest", "cdigest"):  # the week's digest, in the panel (operator, 2026-10-25)
            record = weekly_digest(db)
            body = record["text"] if record else "📰 <b>Weekly digest</b>\nNone yet: it comes on Monday mornings."
            return body, [[HOME]]
        if command in ("history", "chistory"):  # typed /reviews history opens Review history
            text, buttons = review_history(db, int(args) if args.strip().isdigit() else 0)
            return text, buttons + [[HOME]]
        if command == "chist":
            if not args.strip():
                return handle("chistory", "")
            return history_details(db, args.strip())
        if command in ("cstat", "status"):
            # The health of the whole, and the doors to its records (the 2026-10-25 overhaul): audience,
            # accounts, alerts, activity, improvements, the digest and the jobs all live here.
            return status_text(db, next_run(), voice_quota()), [
                [{"text": "📈 Audience", "callback_data": "canalytics:"}, {"text": "🔗 Accounts", "callback_data": "caccounts:"}],
                [{"text": "🔔 Alerts", "callback_data": "calerts:"}, {"text": "🛡 Activity", "callback_data": "caudit:"}],
                [{"text": "🧭 Improvements", "callback_data": "cimprove:"}, {"text": "📰 Digest", "callback_data": "cdigest:"}],
                [{"text": "🧰 Jobs", "callback_data": "jobs:"}], [HOME]]
        if command in ("analytics", "canalytics"):  # the dashboard; typed subcommands open its other screens
            return analytics_screen(db, args)
        if command == "caplat":  # caplat:<ig|fb|yt|ytlong|stories>[:<page from 0>]
            channel, _, page = args.partition(":")
            return analytics_platform(db, channel if channel in ANALYTICS_WORDS.values() else "ig",
                                      int(page) if page.isdigit() else 0)
        if command == "cavideo":
            return analytics_video(db, args)
        if command == "capick":
            return analytics_picker(db, int(args) if args.strip().isdigit() else 0)
        if command in ("caudit", "calerts"):
            # The screen's own reply, not wrapped again: a (text, buttons) pair inside another showed its repr,
            # quotes and escapes on the Alerts screen (operator, 2026-10-25).
            reply = handle("audit" if command == "caudit" else "alerts", "10")
            return reply if isinstance(reply, tuple) else (reply, [[HOME]])
        if command == "ccov":
            return handle("covers", args)  # the latest render, whichever topic it is
        if command == "cprop":
            from . import topic_proposer
            from .models import TopicProposal
            waiting = topic_proposer.pending(db)
            if not waiting:
                return "💡 No topic proposals waiting. Research new ideas from Topic ideas.", [[{"text": "← Topic ideas", "callback_data": "cideas:"}], [HOME]]
            slugs = list(waiting)
            index = slugs.index(args) if args in waiting else min(int(args), len(slugs) - 1) if args.isdigit() else 0
            slug = slugs[index]
            p = TopicProposal.model_validate(waiting[slug])
            text = f"💡 <b>Waiting idea {index + 1} of {len(slugs)}</b>\n\n" + topic_proposer.proposal_text(p)
            return text, [[{"text": "➕ Add", "callback_data": f"ptadd:{index}:{slug}"}, {"text": "✖ Skip", "callback_data": f"ptskip:{index}:{slug}"}],
                          *pager("cprop:", index, len(slugs)), [{"text": "← Topic ideas", "callback_data": "cideas:"}], [HOME]]
        if command in ("ptadd", "ptskip"):
            from . import topic_proposer
            index, _, slug = args.partition(":") if args.partition(":")[0].isdigit() else ("0", "", args)
            if slug in topic_proposer.pending(db):
                (topic_proposer.add if command == "ptadd" else topic_proposer.skip)(db, slug)
            return handle("cprop", index)  # the idea that moved into this place
        if command == "cdisc":
            return handle("discoverpanel", args or ":0")   # every theme, as typed /ideas unesco shows
        if command == "cmusic":
            return handle("music", "")
        if command in ("mfmenu", "musicmenu") or (command == "music" and not args):
            from . import music
            pending = music.pending_tracks()
            library = music.load_library()
            approved = sum(bool(t.get("reviewed")) for t in library)
            text = (f"🎵 <b>Music</b>\n{approved} approved · {len(pending)} waiting for review\n\n"
                    "<i>Instrumental tracks for historical documentaries. Find them, listen, then approve; nothing new is approved on its own.</i>")
            if command == "mfmenu":
                return f"🎵 <b>Find music</b>\n\nChoose a documentary mood. Fetches up to {config.MUSIC_FETCH_PER_MOOD} new CC0/CC BY tracks per mood; no paid generation. Each one waits for your review.", [[job_button(db, f"mfetch:{mood}", mood.title(), f"⏳ Fetching {mood}… · Jobs")] for mood in music.MOODS] + [[job_button(db, "mfetch:all", "All moods", "⏳ Fetching all moods… · Jobs")], [{"text": "← Music", "callback_data": "musicmenu:"}], [HOME]]
            return text, [[{"text": "🔎 Find tracks", "callback_data": "mfmenu:"}, {"text": f"🎧 Review ({len(pending)})" if pending else "🎧 Review", "callback_data": "mreview:"}],
                          [{"text": "📚 Approved tracks", "callback_data": "mlibrary:0"}], [HOME]]
        if command == "mlibrary":
            from . import music
            tracks = [t for t in music.load_library() if t.get("reviewed")]
            page = max(0, int(args)) if args.isdigit() else 0
            start = page * 5
            lines = [f"🎵 <b>Music library</b> · {len(tracks)} tracks"]
            for track in tracks[start:start+5]:
                lines.append(f"\n<b>{html.escape(track['title'])}</b>\n{html.escape(track['mood'])} · {html.escape(music.license_name(track))}")
            pages = max(1, -(-len(tracks) // 5))
            if pages > 1:
                lines[0] += f" · page {page + 1} of {pages}"
            return "\n".join(lines), pager("mlibrary:", page, pages) + [[{"text": "← Music", "callback_data": "musicmenu:"}], [HOME]]
        if command == "mfetch":
            from scripts.fetch_music import fetch_music_tracks
            added = fetch_music_tracks(mood=None if args == "all" else args, add_count=config.MUSIC_FETCH_PER_MOOD)
            return f"🎵 Downloaded {added} new track(s). Listen and approve before use.", [[{"text": "Review waiting", "callback_data": "mreview:"}], [{"text": "← Music", "callback_data": "musicmenu:"}], [HOME]]
        if command == "music" and args:
            action, _, ref = args.partition(" ")
            if action == "fetch":
                return handle("mfetch", ref) if ref else handle("mfmenu", "")
            if action in ("review", "approved"):
                return handle("mreview" if action == "review" else "mlibrary", ref)
        if command == "cimages":
            # The Pictures hub (2026-10-25 overhaul): where every set stands, then the doors: the sets
            # themselves, the covers of the latest render, and footage you hold the rights to.
            return ("🖼 <b>Pictures</b>\n\n" + images_text(assets_dir, db=db),
                    [[{"text": "📂 Photo sets", "callback_data": "imagepick:0"}, {"text": "🎨 Covers", "callback_data": "ccov:"}],
                     [{"text": "🎞 Footage", "callback_data": "cfootage:"}], [HOME]])
        if command == "cfootage":
            reply = handle("footage", "")  # its own (text, buttons): never a pair wrapped in a pair
            return reply if isinstance(reply, tuple) else (reply, [[HOME]])
        if command == "imagepick":
            topics = db.list_all_topics()
            pages = max(1, -(-len(topics) // 8))
            page = min(int(args) // 8 if args.isdigit() else 0, pages - 1)  # the argument is the first item's offset
            shown = list(enumerate(topics, 1))[page * 8:page * 8 + 8]
            # Full titles wrap in the message; the buttons carry only the numbers.
            lines = ["🖼 <b>Photo sets</b>" + (f" · page {page + 1} of {pages}" if pages > 1 else ""), ""]
            lines += [f"<b>{i}.</b> {html.escape(t['title'])}" for i, t in shown]
            numbers = [{"text": str(i), "callback_data": f"imagehelp:{t['id']}"} for i, t in shown]
            nav = [[{**b, "callback_data": "imagepick:" + str(int(b["callback_data"].rpartition(":")[2]) * 8)} for b in row]
                   for row in pager("imagepick:", page, pages)]
            return "\n".join(lines), [row for row in (numbers[:4], numbers[4:]) if row] + nav + [[HOME]]
        if command in ("image", "imagehelp"):
            if not args:
                return handle("imagepick", "")
            from .telegram_images import topic_for
            topic = topic_for(db, args)
            args = topic["id"]
            summary = ""
            t_dir = _resolve_topic_dir(args, assets_dir, db=db)
            if (t_dir / "credits.json").is_file():  # where the set stands (operator, 2026-10-25)
                summary = "\n\n" + "\n".join(review_caption(t_dir, title=(db.get_topic_by_id(t_dir.name) or {}).get("title")).split("\n")[2:]).strip()
            return (f"🖼 <b>{html.escape(topic['title'])}</b>{summary}\n\nFetch licensed photos, upload your own with guided prompts, or review this set. New images need human review.",
                    [[job_button(db, f"fetchimages:{args}", "🔎 Fetch more"), {"text": "📎 Upload my image", "callback_data": f"upload:{args}"}],
                     [{"text": "🖼 Review the set", "callback_data": f"review:{args}"}], [HOME]])
        if command == "fetchimages":
            if not args:
                return handle("imagepick", "")
            from .telegram_images import fetch_images
            topic_id, count = fetch_images(db, assets_dir, args)
            name = (db.get_topic_by_id(topic_id) or {}).get("title") or topic_id.replace("_", " ")   # its name, not its id
            return (f"🖼 <b>Photos fetched</b>\n{count} eligible photo{'s' if count != 1 else ''} for "
                    f"<b>{html.escape(name)}</b>; review {'it' if count == 1 else 'them'} before a video uses "
                    f"{'it' if count == 1 else 'them'}.",
                    [[{"text": "🖼 Review the set", "callback_data": f"review:{args}"}],
                     [{"text": "← Topic pictures", "callback_data": f"imagehelp:{args}"}], [HOME]])
        if command == "cpause":
            handle("pause", "")
            dash_text, dash_buttons = dashboard_text_and_buttons(db, next_run(), assets_dir=assets_dir)
            return f"⏸ <b>Paused</b>: no scheduled runs or posts until you resume.\n\n{dash_text}", dash_buttons
        if command == "cresume":
            handle("resume", "")
            dash_text, dash_buttons = dashboard_text_and_buttons(db, next_run(), assets_dir=assets_dir)
            return f"▶️ <b>Resumed</b>: scheduled runs and posts are back on.\n\n{dash_text}", dash_buttons
        if command == "images":
            action, _, ref = args.partition(" ")
            if action in {"fetch", "upload", "review"}:
                return handle({"fetch": "fetchimages", "upload": "image", "review": "review"}[action], ref)
            if args:
                return handle("imagehelp", args)
            if send_message:
                return handle("cimages", "")
            return images_text(assets_dir, db=db)
        if command == "reviewpick":
            # Every topic with fetched images, paged, each marked approved / needs review / too few images
            # (the picker used to stop at the first five queued topics, which hid the rest).
            topics = db.list_all_topics()
            serial = {t["id"]: i for i, t in enumerate(topics, 1)}
            sets = []
            for t in topics:
                try:
                    td = _resolve_topic_dir(t["id"], assets_dir, db=db)
                    cr = load_credits(td)
                except Exception:
                    continue
                if cr.get("images"):
                    count = len(cr["images"])
                    r = readiness(cr["images"], bool(cr.get("reviewed")))  # the video's count (2026-10-28)
                    mark = ("✅" if r["ready"] or r["pending"] else "⛔" if cr.get("reviewed") or count < MIN_SCENE_IMAGES
                            else "⚠️")
                    sets.append((serial[t["id"]], t["title"], count, mark))
            back = [[{"text": "← Pictures", "callback_data": "cimages:"}], [HOME]]
            if not sets:
                return "🖼 <b>Photo sets</b>\n\nNo topic has fetched photos yet. Open Pictures to fetch or upload some.", back
            pages = -(-len(sets) // 8)
            page = min(int(args) if args.isdigit() else 0, pages - 1)
            shown = sets[page * 8:page * 8 + 8]
            counts = {m: sum(1 for s in sets if s[3] == m) for m in ("✅", "⚠️", "⛔")}
            text = ("🖼 <b>Photo sets</b>" + (f" · page {page + 1} of {pages}" if pages > 1 else "")
                    + f"\n✅ approved {counts['✅']} · ⚠️ needs your review {counts['⚠️']} · ⛔ too few a video can use (need {MIN_SCENE_IMAGES}) {counts['⛔']}\n\n"
                    + "\n".join(f"<b>{n}.</b> {mark} {html.escape(title)} · {count} images" for n, title, count, mark in shown))
            numbers = [{"text": str(n), "callback_data": f"review:{n}"} for n, _, _, _ in shown]
            return text, [row for row in (numbers[:4], numbers[4:]) if row] + pager("reviewpick:", page, pages) + back
        if command == "review":
            ref = args.strip()
            if not ref:
                text, buttons = handle("reviewpick", "0")
                if send_message:
                    send_message(text, buttons)
                    return ""
                return text, buttons
            topic_dir = _resolve_topic_dir(ref, assets_dir, db=db)
            if send_sheet(topic_dir):
                return ""
            return review_caption(topic_dir, ref_hint=args.strip(), title=(db.get_topic_by_id(topic_dir.name) or {}).get("title"))
        if command == "drop":
            parts = args.split()
            if not parts or len(parts) < 2 or not all(n.isdigit() for n in parts[1:]):
                example_num = "1"
                try:
                    nxt = db.get_next_topic()
                    if nxt:
                        all_t = db.list_all_topics()
                        example_num = str({t["id"]: i for i, t in enumerate(all_t, 1)}.get(nxt["id"], 1))
                except Exception:
                    pass
                return (f"🗑 <b>Remove photos from a set</b>\n\nSend the topic number, then the image numbers "
                        f"shown on its sheet: <code>/photos {example_num} drop 2 5</code>\n\n"
                        "<i>Easiest from the sheet itself: it has a Copy button for this.</i>",
                        [[{"text": "🖼 Photo sets", "callback_data": "imagepick:0"}], [HOME]])
            ref, nums = parts[0], parts[1:]
            topic_dir = _resolve_topic_dir(ref, assets_dir, db=db)
            msg = drop_images(topic_dir, [int(n) for n in nums])
            if load_credits(topic_dir)["images"]:
                send_sheet(topic_dir)
            return html.escape(msg)
        if command in ("keep", "unkeep"):
            parts = args.split()
            if len(parts) < 2 or not all(n.isdigit() for n in parts[1:]):
                return (f"📌 <b>Keep photos for the video</b>\n\nSend the topic number, then the photo numbers on its "
                        f"sheet: <code>/photos 4 {command} 2 7</code>. A kept photo is used even if the check rated it "
                        "Context or Unchecked; <code>/photos 4 unkeep 2</code> takes the mark off.",
                        [[{"text": "🖼 Photo sets", "callback_data": "imagepick:0"}], [HOME]])
            from .credits import keep_images
            topic_dir = _resolve_topic_dir(parts[0], assets_dir, db=db)
            msg = keep_images(topic_dir, [int(n) for n in parts[1:]], keep=command == "keep")
            send_sheet(topic_dir)
            return html.escape(msg)
        if command in ("music", "mreview", "mkeep", "mdel", "mcancel", "mnext"):
            from . import music
            reply = ""
            if command == "mkeep":
                reply = "✅ " + html.escape(music.approve_track(args))
            elif command == "mdel":
                reply = "🗑 " + html.escape(music.remove_track(args))
            elif command == "mcancel":  # Cancel on cards from earlier releases: up to the Music menu
                return handle("musicmenu", "")
            to_music = {"text": "← Music", "callback_data": "musicmenu:"}
            pending = music.pending_tracks()
            if not pending:
                approved = sum(1 for t in music.load_library() if t.get("reviewed"))
                return (reply + "\n" if reply else "") + (
                    f"🎵 No tracks waiting for review ({approved} approved). Find new tracks from Music."), [[to_music], [HOME]]

            cur_idx = 0
            if command == "mnext":
                for idx, t in enumerate(pending):
                    if music.track_key(t) == args:
                        cur_idx = (idx + 1) % len(pending)
                        break
                reply = f"⏭ Skipped to track {cur_idx + 1} of {len(pending)}."
            elif command == "music" and args.strip():
                if args.strip().isdigit():
                    num = int(args.strip())
                    if 1 <= num <= len(pending):
                        cur_idx = num - 1

            nxt = pending[cur_idx]
            key = music.track_key(nxt)
            buttons = [
                [
                    {"text": "✅ Keep Track", "callback_data": f"mkeep:{key}"},
                    {"text": "🗑 Remove Track", "callback_data": f"mdel:{key}"},
                ]
            ]
            # Leaving the review goes up one level (the track stays waiting), not all the way to the dashboard.
            buttons.append([{"text": "⏭ Next Track", "callback_data": f"mnext:{key}"}, to_music] if len(pending) > 1 else [to_music])
            buttons.append([HOME])

            if send_audio is None or not send_audio(music.MUSIC_DIR / nxt["file"], (reply + "\n\n" if reply else "") + music.review_caption(nxt, len(pending)), buttons):
                return (reply + "\n" if reply else "") + music.review_caption(nxt, len(pending))
            return reply or ""
        if command in ("post", "postreal"):
            # Your per-video go-ahead for a finished dry run: asked once, then "promote" does it (the daemon
            # stays in dry-run mode; only this run is posted or booked). Before, a good dry-run output could
            # be posted only from the command line as the pipeline account (2026-10-24).
            run_id = db.resolve_run(args.strip())
            run = db.get_run(run_id)
            if not run:
                return "🚀 Unknown run. Use the Run ID from its card or receipt."
            if run.get("status") not in ("simulated", "approved"):
                return f"🚀 {html.escape(db.run_label(run_id))} is {html.escape(run['status'].replace('_', ' '))}; only a finished dry run (simulated) can be posted."
            from . import config as app_config
            where = "booked into the next free posting slot (Stories after it, the carousel in its next slot)" if app_config.POST_SCHEDULING else "posted now"
            buttons = [[{"text": "✅ Yes, post it", "callback_data": f"promote:{db.run_key(run_id)}"}]]
            if app_config.POST_SCHEDULING:  # on demand (operator, 2026-10-24): the Reel at once, Stories and carousel after it
                buttons.append([{"text": "⚡ Post now instead", "callback_data": f"postnow:{db.run_key(run_id)}"}])
            return (f"🚀 <b>Post this for real?</b>\n\n{html.escape(db.run_label(run_id))}\n\n"
                    f"It will be {where} on Facebook, Instagram and YouTube, after the release checks run in live mode. "
                    "This is the one action that publishes; the daily runs stay in dry-run mode.",
                    buttons + [[HOME]])
        if command in ("posts", "cposts"):
            text, buttons = posting_panel(db, int(args) if args.strip().isdigit() else 0)
            return text, buttons + [[HOME]]
        if command == "cslots":  # the coming week's slots, booked or free (review, 2026-10-25)
            from .posting_view import slots_screen
            text, buttons = slots_screen(db, int(args) if args.strip().isdigit() else 0)
            return text, buttons + [[HOME]]
        if command == "postnowask":  # Post now publishes at once: it asks first
            from .posting_view import confirm_post_now
            text, buttons = confirm_post_now(db, db.resolve_run(args.strip()))
            return text, buttons + [[HOME]]
        if command == "cancelask":  # cancelling a Reel takes its Stories and carousel with it: it asks first
            from .posting_view import confirm_cancel
            try:
                text, buttons = confirm_cancel(db, int(args.strip()))
            except ValueError:
                return "That booking id is not valid.", [[HOME]]
            return text, buttons + [[HOME]]
        if command == "postsdo":  # typed /posts now|move|cancel|retry N, N the episode's number on Posting
            from .posting_view import episode_by_number
            verb, _, number = args.strip().partition(" ")
            run_id = episode_by_number(db, number)
            if not run_id:
                text, buttons = posting_panel(db)
                return f"🗓 There is no episode {html.escape(number or '?')} on Posting.\n\n" + text, buttons + [[HOME]]
            posts = db.list_scheduled_posts(("pending", "waiting", "failed", "unknown"), run_id=run_id)
            reel = next((p for p in posts if p["kind"] == "reel" and p["status"] == "pending"), None)
            if verb == "now":
                return handle("postnowask", db.run_key(run_id))
            if verb == "move" and reel:
                return handle("movepost", str(reel["id"]))
            if verb == "cancel" and reel:
                return handle("cancelask", str(reel["id"]))
            if verb == "retry":
                failed_reel = next((p for p in posts if p["kind"] == "reel" and p["status"] in ("failed", "unknown")), None)
                if failed_reel:
                    return handle("retryreel", db.run_key(run_id))
                other = next((p for p in posts if p["kind"] != "reel" and p["status"] in ("failed", "unknown")), None)
                if other:
                    return handle("retrypost", str(other["id"]))
            text, buttons = posting_episode(db, run_id)
            return f"🗓 Nothing to {html.escape(verb)} for episode {html.escape(number)} now.\n\n" + text, buttons + [[HOME]]
        if command == "cpost":  # one episode's posts
            text, buttons = posting_episode(db, db.resolve_run(args.strip()))
            return text, buttons + [[HOME]]
        if command in ("cancelpost", "movepost", "retrypost"):
            action = (run_actions or {}).get(command)
            if action is None:
                return "Booking changes work while the bot or daemon is running."
            try:
                post_id = int(args.strip())
                run_of = next((p["run_id"] for p in db.list_scheduled_posts(
                    ("pending", "waiting", "published", "failed", "unknown", "cancelled", "expired")) if p["id"] == post_id), None)
                result = action(post_id)
            except ValueError:
                return "That booking id is not valid."
            text, buttons = posting_episode(db, run_of) if run_of else posting_panel(db)  # back to where it was
            return f"{result}\n\n{text}", buttons + [[HOME]]
        if command == "produce":
            if produce_batch is None:
                return "Batch production works while the bot is running."
            try:
                n = max(1, min(int(args.strip() or "1"), 14))
            except ValueError:
                return "Usage: /produce <episodes, 1-14> — renders that many queued topics back to back, each to review."
            try:
                current = db.get_next_topic()
            except Exception:
                current = None
            res = produce_batch(n)
            started = "already" not in res.lower()                       # else another run is busy: the panel shows it
            if started and current:
                db.set_setting(RUN_STARTING_KEY, json.dumps({"topic": current["id"], "at": clock.time()}))
            q_text, q_buttons = render_queue_text_and_buttons(starting=current["id"] if started and current else None)
            return f"{res}\n\n{q_text}", q_buttons                       # the Queue panel, now showing the batch
        if command in ("pubok", "pubno", "resend", "simpub", "rerender", "promote", "postnow", "retryreel", "fbcaptions",
                       "fbcover", "ytshort", "rebook", "ytthumb"):
            action = (run_actions or {}).get(command)
            if action is None:
                return "That button only works while the daemon is running."
            run_id = db.resolve_run(args)
            result = action(run_id)
            if command in EPISODE_ACTIONS and isinstance(result, str):
                # The result on the episode's own card, redrawn (operator, 2026-10-27: the answer came as a separate
                # message with only Dashboard, under a card still saying "Facebook cover: not set")
                text, buttons = posting_episode(db, run_id)
                return f"{result}\n\n{text}", buttons + [[HOME]]
            return result
        if command == "tdone":
            cleared = 0
            if delete_message is not None:
                for mid in list(active_proposal_msg_ids):
                    try:
                        if delete_message(mid):
                            cleared += 1
                    except Exception:
                        pass
            active_proposal_msg_ids.clear()
            return f"✖ Topic proposals ended. Cleared {cleared} card(s) from chat." if cleared else "✖ Topic proposals ended."
        if command == "proposals":
            return handle("cprop", args)
        if command in ("propose", "cpropose"):
            started = running_job(db, "topic-research")
            if started:  # a second tap must not look like a failure, nor start a duplicate (and its cost)
                return (f"🔬 <b>Research is already running</b> (started {started}).\n\nIts result opens here "
                        "when it finishes, usually within a few minutes.",
                        [[{"text": "🧰 Jobs", "callback_data": "jobs:"}], [{"text": "← Topic ideas", "callback_data": "cideas:"}], [HOME]])
            if not propose_topics:
                return "Topic proposals need the bot or daemon running with a script engine configured."
            # The research job now owns the panel ("⏳ Researching topic ideas", then its result): None tells
            # the bot not to draw over it with an empty "Done".
            return propose_topics() or None
        if command == "tadd":
            from . import topic_proposer
            topic_id = topic_proposer.add(db, args)
            return f"➕ Added <code>{html.escape(topic_id)}</code>. Open <code>/photos</code> to fetch or upload its photos."
        if command == "tskip":
            from . import topic_proposer
            topic_proposer.skip(db, args)
            return f"✖ Skipped {html.escape(args)}."
        if command == "upd":
            if apply_updates is None:
                return "Updates can only be applied while the daemon is running."
            return apply_updates()
        if command in ("imgok", "imgcancel"):
            # Both end on the topic's image menu (where the review was opened), not on a dead card.
            ref, _, digest = args.rpartition("@") if "@" in args else (args, "", "")
            back = ([[{"text": "← Topic pictures", "callback_data": f"imagehelp:{ref}"}]] if ref else []) + [[HOME]]
            if not ref:
                return "This review is obsolete. Open its photo set again (<code>/photos N review</code>) before approving.", back
            t_dir = _resolve_topic_dir(ref, assets_dir, db=db)
            t_name = (db.get_topic_by_id(t_dir.name) or {}).get("title") or t_dir.name.replace("_", " ")
            if command == "imgcancel":
                state = "approved" if load_credits(t_dir).get("reviewed") else "not approved"
                return f"🖼 Photo set review closed for <b>{html.escape(t_name)}</b>; the set stays {state}.", back
            from .telegram_images import review_digest
            if not digest:
                return "This review is obsolete. Open its photo set again (<code>/photos N review</code>) before approving.", back
            if digest != review_digest(t_dir):
                return "Images changed since this review. Open its photo set again (<code>/photos N review</code>); nothing was approved.", back
            if load_credits(t_dir).get("reviewed"):
                return f"ℹ️ The photo set of <b>{html.escape(t_name)}</b> was already approved.", back
            return "✅ " + html.escape(approve_set(t_dir, MIN_SCENE_IMAGES)), back
        if command == "status":
            return status_text(db, next_run(), voice_quota())
        if command == "next":
            t = db.get_next_topic()
            serial = {x["id"]: i for i, x in enumerate(db.list_all_topics(), 1)}.get(t["id"], 1)
            return (f"🎯 <b>Next topic:</b> {serial}. {html.escape(t['title'])}",
                    [[{"text": "📋 Queue", "callback_data": "qrefresh:"}], [HOME]])
        if command == "pause":
            db.set_setting(PAUSED_KEY, "true")
            return "⏸ Scheduled runs and posts paused. Send /resume to re-enable."
        if command == "deploy":
            return request_deploy(db), [[HOME]]
        if command == "resume":
            db.set_setting(PAUSED_KEY, "false")
            return f"▶️ Scheduled runs and posts resumed. Next: {html.escape(next_run() or 'not scheduled')}"
        if command in ("filmlist", "filmfind", "filmuse", "filmdrop"):
            # Archive film (src/archive_film.py): found where its licence is stated, used only once approved
            from . import archive_film
            from .telegram_images import topic_for
            ref, *numbers = args.split()
            topic = topic_for(db, ref)
            key = str({t["id"]: i for i, t in enumerate(db.list_all_topics(), 1)}.get(topic["id"], ref))
            ask = None
            note = ""
            if command == "filmfind":
                specs = json.loads((config.TOPICS_DIR / "image_queries.json").read_text(encoding="utf-8"))
                spec = specs.get(topic["id"]) or {"queries": [topic["title"]], "keywords": [topic["title"]]}
                found = archive_film.find(topic, spec, assets_dir)
                ask = found["ask"]
                note = (f"🔎 Found {len(found['saved'])} new film(s) with an open licence." if found["saved"]
                        else "🔎 No new openly licensed film was found for this topic.") + "\n\n"
            elif command in ("filmuse", "filmdrop"):
                picked = [int(n) for n in numbers if n.isdigit()]
                done = archive_film.decide(assets_dir, topic["id"], picked, use=command == "filmuse")
                note = ((("✅ In use: " if command == "filmuse" else "🗑 Dropped (never fetched again): ")
                         + "; ".join(html.escape(t[:60]) for t in done)) if done else "⚠️ No film by those numbers.") + "\n\n"
            text = note + archive_film.card(assets_dir, topic, ask, key)
            items = archive_film.pending_list(assets_dir, topic["id"])
            buttons = [[{"text": "🔎 Find film", "callback_data": f"filmfind:{key}"}], [HOME]]
            if items and send_photo:
                still = archive_film.sheet(items, Path(assets_dir) / topic["id"] / "footage" / "pending" / "sheet.jpg")
                if still:
                    send_photo(still, text, buttons, cache_key=f"film:{topic['id']}:{len(items)}:{note[:20]}")
                    return ""
            return text, buttons
        if command == "footage":
            # Footage is supplied as a file the operator holds the rights to (send the video with the
            # caption "/footage <topic>"). Links are not downloaded: a downloaded clip carries no licence
            # evidence, and downloading from YouTube conflicts with its Terms of Service.
            try:  # a ready-to-copy caption for the next topic (the copy button needs a concrete command)
                nxt = db.get_next_topic()
                example = str({t["id"]: i for i, t in enumerate(db.list_all_topics(), 1)}.get(nxt["id"], 1)) if nxt else "1"
            except Exception:
                example = "1"
            # The whole caption, rights included: the short form saved a clip the release gate then held
            # (review, 2026-10-25). The copy button fills it in, ready for the URLs.
            form = f"/footage {example} | source: URL | licence: CC BY 4.0 | licence_url: URL | author: Name"
            return ("🎞 <b>Footage</b>\nA licensed clip for a topic, used where it shows the place itself.\n\n"
                    "Send the video file to this chat (up to 20 MB) with this caption, the topic's number first:\n"
                    f"<code>{html.escape(form)}</code>\n\n"
                    "<i>Without its source, licence and licence URL the release gate holds any video using it. Links "
                    "aren't downloaded: their rights can't be verified, and YouTube's terms prohibit downloading.</i>",
                    [[{"text": "📎 Copy the caption", "copy_text": {"text": form}}], [HOME]])
        if command == "queue":
            ref = args.strip().lower()
            q_text, q_buttons = render_queue_text_and_buttons(show_all=(ref in ("all", "full")))
            if send_message:
                send_message(q_text, q_buttons)
                return ""
            return q_text
        if command == "requeue":
            ref = args.strip()
            if not ref or ref.lower() in ("list", "all", "ls"):
                q_text, _ = render_queue_text_and_buttons(show_all=True)
                return q_text
            topics = db.list_all_topics()
            match = None
            if ref.isdigit():
                idx = int(ref) - 1
                if 0 <= idx < len(topics):
                    match = topics[idx]
            if not match:
                for t in topics:
                    if t["id"] == ref or t["id"].lower() == ref.lower():
                        match = t
                        break
                    if ref.isdigit() and t["id"].split("_")[0] == f"{int(ref):02d}":
                        match = t
                        break
                    if t["id"].split("_", 1)[-1] == ref:
                        match = t
                        break
            if not match:
                return f"⚠️ Topic '{html.escape(ref)}' not found in database. Send /queue for list."
            if not db.requeue_topic(match["id"], stale=match["id"] in stale_topics(db)):
                return "This item cannot be requeued from its current state.", [[HOME]]
            return f"🔄 Topic <b>{html.escape(match['title'])}</b> (<code>{html.escape(match['id'])}</code>) restored to the queue."
        if command == "alerts":
            limit = 10
            if args.strip().isdigit():
                limit = min(50, max(1, int(args.strip())))
            alerts = db.list_recent_alerts(limit=limit)
            if not alerts:
                return "🔔 No alerts recorded in the database yet."
            lines = [f"🔔 <b>Alerts</b> · last {len(alerts)}", ""]
            # The same alert sent again (a review reminder each hour) is one entry with its count and latest time
            # (review, 2026-10-25: 9 of the last 10 were the same reminders).
            grouped: List[Dict[str, Any]] = []
            for a in alerts:
                # Alerts are stored as the HTML that was sent: shown here as plain text (they were escaped a
                # second time, so their tags appeared on screen).
                plain = html.unescape(re.sub(r"<[^>]+>", "", a.get("text", ""))).strip()
                first, _, rest = plain.partition("\n")
                if re.sub(r"[^A-Za-z]", "", first).isupper():  # older alerts were headed in capitals
                    first = re.sub(r"^(\W*)(\w)", lambda m: m[1] + m[2].upper(), first.lower())  # "❌ Production failed"
                same = next((g for g in grouped if g["first"] == first and g["rest"] == rest), None)
                if same:
                    same["count"] += 1
                    continue
                grouped.append({"first": first, "rest": rest, "count": 1, "alert": a})
            for g in grouped:
                a = g["alert"]
                ts = show_time(a.get("created_at"), "%d %b %H:%M") if a.get("created_at") else ""
                note = " · removed from chat" if a.get("purged") else ""
                times = f" · {g['count']} times" if g["count"] > 1 else ""
                # Each its own look (operator, 2026-10-25: the whole line was bold): what happened in plain words
                # with the run's title in bold, and when it was sent, in italics, under it.
                said = html.escape(clip_words(g["first"], 240))
                said = re.sub(r"(&quot;.+?&quot;)", r"<b>\1</b>", said, count=1)
                gap = "" if len(lines) <= 2 else "\n"          # a blank line between alerts, none under the title
                lines.append(f"{gap}• {said}\n   <i>{'last ' if times else ''}sent {ts}{times}{note}</i>")
                body = re.sub(r"\n{2,}", "\n", g["rest"]).strip()
                if body:
                    lines.append(f"<blockquote expandable>{html.escape(clip_words(body, 600))}</blockquote>")
            return "\n".join(lines), [[HOME]]
        if command == "add":
            title = args.strip()
            if not title:
                return ("➕ <b>Add a topic</b>\n\nSend /add followed by the title, for example "
                        "<code>/add The Lost Labyrinth of Hawara</code>\n\n"
                        "<i>Its facts are researched and checked before it can be made.</i>",
                        [[{"text": "💡 Topic ideas", "callback_data": "cideas:"}], [HOME]])
            try:
                topic_id, u_tag, decomp = _add_topic_to_bank(db, title)
            except ValueError as e:
                return f"⚠️ {html.escape(str(e))}.\nTopic was not added."
            arch_lines = [f"  • <b>{arch_name.capitalize()}:</b> {html.escape(arch_q)}" for arch_name, arch_q in decomp.get("archetypes", {}).items()]
            arch_summary = "\n" + "\n".join(arch_lines) if arch_lines else ""
            return (f"➕ Added <b>{html.escape(title)}</b> (<code>{html.escape(topic_id)}</code>) to topic queue!{u_tag}\n"
                    f"🎬 <b>Documentary Shot Archetypes:</b>{arch_summary}\n\n"
                    f"Open <code>/photos {html.escape(topic_id)}</code> to fetch, upload or review photos.")
        if command in ("discover", "discoverpanel"):
            from .unesco import UNESCOEngine
            from . import topic_proposer
            engine = UNESCOEngine()
            bank = topic_proposer._bank()
            existing_titles = [t["title"] for t in bank.get("topics", [])]
            skipped = json.loads(db.get_setting("unesco_skipped", "[]"))
            existing_titles += [s.site for key, s in engine.load_sites().items() if str(key) in skipped]

            theme = None
            offset = 0
            if command == "discoverpanel":
                parts = args.split(":", 1)
                theme = parts[0].strip() or None
                if len(parts) > 1 and parts[1].strip().isdigit():
                    offset = int(parts[1].strip())
            else:
                parts = args.strip().split()
                if parts:
                    if not parts[0].isdigit():
                        theme = parts[0]
                        if len(parts) > 1 and parts[1].isdigit():
                            offset = int(parts[1])
                    else:
                        offset = int(parts[0])

            # One marvel per page, like Waiting ideas: its full text wraps in the message, and the buttons stay
            # short (three per page cut both the descriptions and the "Add <name>" buttons off).
            back = [[{"text": "← Topic ideas", "callback_data": "cideas:"}], [HOME]]
            matches = engine.discover_engineering_wonders(limit=10_000, offset=0, sub_theme=theme, exclude_titles=existing_titles)
            if not matches:
                return (f"🏛️ No unqueued UNESCO engineering marvels found for '{html.escape(theme or 'all')}'. "
                        "Every matching site has been added or skipped."), back
            index = min(offset, len(matches) - 1)
            w = matches[index]
            about = (w.get("short_description") or "").strip()
            if len(about) > 900:
                about = about[:900].rsplit(" ", 1)[0] + "…"
            text = (f"🏛️ <b>UNESCO marvel {index + 1} of {len(matches)}</b>" + (f" · {html.escape(theme)}" if theme else "")
                    + f"\n\n<b>{html.escape(w['title'])}</b> · {html.escape(w['country'].replace(',', ', '))}\n\n{collapsible(about)}")
            place = f"{w['id_number']}:{theme or ''}:{index}"
            buttons = [[{"text": "➕ Add", "callback_data": f"uadd:{place}"}, {"text": "✖ Skip", "callback_data": f"uskip:{place}"}]]
            buttons += [[{**b, "callback_data": f"cdisc:{theme or ''}:{b['callback_data'].rpartition(':')[2]}"} for b in row]
                        for row in pager("cdisc:", index, len(matches))]
            # A switch between themes (the button always opened "subterranean", with no way to another: 2026-10-25)
            from .unesco import THEME_KEYWORDS
            themes = [("", "All")] + [(k, k.replace("_", " ").capitalize()) for k in THEME_KEYWORDS]
            row = [{"text": ("✓ " if (theme or "") == k else "") + name, "callback_data": f"cdisc:{k}:0"} for k, name in themes]
            buttons += [row[i:i + 3] for i in range(0, len(row), 3)]
            return text, buttons + back

        if command == "uadd":
            ref, _, place = args.strip().partition(":")  # place: "<theme>:<index>" when added from the panel
            if not ref.isdigit():
                return "Usage: uadd:&lt;unesco_id&gt;"
            from .unesco import UNESCOEngine
            engine = UNESCOEngine()
            sites = engine.load_sites()
            site = sites.get(int(ref))
            if not site:
                return f"⚠️ UNESCO site #{html.escape(ref)} not found in database."
            try:
                topic_id, u_tag, decomp = _add_topic_to_bank(db, site.site, u_site=site)
            except ValueError as e:
                err_str = str(e)
                if "Duplicate detected" in err_str:
                    return f"ℹ️ <b>{html.escape(site.site)}</b> is already queued in the topic bank."
                return f"⚠️ {html.escape(err_str)}"
            added = (f"➕ Added UNESCO Marvel <b>{html.escape(site.site)}</b> (<code>{html.escape(topic_id)}</code>) to queue!{u_tag}\n\n"
                     f"Open <code>/photos {html.escape(topic_id)}</code> to fetch, upload or review photos.")
            if place:  # from the panel: the next marvel moves into this place
                text, buttons = handle("discoverpanel", place)
                return added.split("\n\n")[0] + "\n\n" + text, buttons
            return added
        if command == "uskip":
            with db._conn() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT value FROM settings WHERE key='unesco_skipped'").fetchone()
                skipped = set(json.loads(row[0]) if row else [])
                site_id, _, page = args.strip().partition(":")
                skipped.add(site_id)
                conn.execute("INSERT OR REPLACE INTO settings (key,value,updated_at) VALUES ('unesco_skipped',?,?)", (json.dumps(sorted(skipped)), datetime.now(timezone.utc).isoformat()))
            if page:  # from the panel: show the same page again, without the skipped site
                return handle("discoverpanel", page)
            return f"Skipped UNESCO site #{html.escape(site_id)}."
        if command == "ask":
            from .probe import probe_system
            return probe_system(args.strip(), db=db, assets_dir=assets_dir)

        if command == "issue":
            raw_input = args.strip()
            if not raw_input:
                return ("🛠️ <b>Report a problem</b>\n\n"
                        "✍️ <b>Describe it now</b> in your next message (within 10 minutes), or send a screenshot with "
                        "the description as its caption. Name the run (its number on the card) if it's about one.\n\n"
                        "Example: <code>/issue run 12 the map label covers the pin</code>\n\n"
                        "<i>Your reports are listed in /improve.</i>")
            evidence_path = None
            critique = raw_input
            if "--evidence" in raw_input:
                crit_part, _, ev_part = raw_input.partition("--evidence")
                critique, evidence_path = crit_part.strip(), ev_part.strip()
            from .probe import resolve_run
            named = re.search(r"\brun[ _#]?(\d+)\b|\b(run_\w+)", critique, re.IGNORECASE)
            run = resolve_run(db, critique) if named else None
            run_id = run["run_id"] if run else None
            issue_id = "iss_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            db.record_issue(issue_id, critique, run_id=run_id, evidence_path=evidence_path)
            from . import improve
            klass = improve.triage_issue(db, issue_id, critique, run_id)
            from . import config as _config
            _config.ISSUES_DIR.mkdir(parents=True, exist_ok=True)
            (_config.ISSUES_DIR / f"{issue_id}.md").write_text(
                f"# {issue_id}\n\n- Created: {datetime.now(timezone.utc).isoformat()}\n- Run: {run_id or 'not named'}\n"
                f"- Class: {klass}\n- Evidence: {evidence_path or 'none'}\n- Status: open\n\n## Report\n{critique}\n",
                encoding="utf-8")
            recurring = next((r for r in improve.recurring(db, min_count=1) if r["klass"] == klass), None)
            similar = recurring["count"] - 1 if recurring else 0
            area = klass.partition(".")[2] or "other"
            return (f"✅ <b>Report saved</b> under <i>{html.escape(improve.AREA_NAMES.get(area, area))}</i>"
                    + (f" for {html.escape(db.run_label(run_id))}" if run_id else "")
                    + (" with your screenshot" if evidence_path else "") + ".\n\n"
                    + (f"{similar} similar report{'s' if similar != 1 else ''} in the last {improve.RECURRENCE_DAYS} days. " if similar else "")
                    + f"{len(db.list_open_issues())} open in total; /improve lists them.")

        if command == "improve":
            from . import improve
            text, buttons = improve.improve_text(db)
            if send_message:
                send_message(text, buttons + [[HOME]])
                return ""
            return text
        if command == "impinfo":
            from . import improve
            p = improve.get_proposal(db, args.strip())
            if not p:
                return "That proposal no longer exists."
            text, buttons = improve.card(p)
            if send_message:
                send_message(text, buttons)
                return ""
            return text
        if command in ("impok", "impno", "imprev"):
            from . import improve
            pid = args.strip()
            fn = {"impok": improve.adopt, "impno": improve.decline, "imprev": improve.revert}[command]
            return fn(db, pid, by="telegram")
        if command == "rsn":
            from . import improve
            key, _, reason = args.partition(":")
            run_id = db.resolve_run(key)
            appr = db.get_approval(run_id) or {}
            improve.record_reason(db, run_id, reason, appr.get("decision", ""))
            # The reason chips sit on the decided review card itself: this text replaces that card, so it
            # keeps what was decided.
            decided = (f"🔄 <b>Regeneration requested.</b> In the new version, "
                       f"{improve.REGENERATE_EFFECT.get(reason, 'it is logged for improvement')}. Its review replaces this card."
                       if appr.get("decision") == "regenerate"
                       else "❌ <b>Rejected.</b> Nothing was posted." if appr.get("decision") == "rejected" else "")
            return (f"🎬 <b>{html.escape(db.run_label(run_id))}</b>\n\n" + (f"{decided}\n\n" if decided else "")
                    + f"✅ Reason: <b>{html.escape(improve.REASONS.get(reason, reason))}</b>. "
                    f"Reply to this card to add details." + (
                        " It leaves the chat once you do, or in 30 minutes; the run stays in History."
                        if appr.get("decision") == "rejected" else "") + f" [run {html.escape(key)}]")
        if command == "whytext":
            from . import improve
            key, _, text = args.partition(" ")
            run_id = db.resolve_run(key)
            prev = improve.operator_feedback(db, run_id) or {}
            improve.record_reason(db, run_id, prev.get("reason", "other"), prev.get("decision", ""), text.strip())
            # Said as it is: the new version reads these words at its script and picture steps, so only
            # the steps it has not reached yet can use them.
            return "📝 Thanks, noted" + (". Steps of the new version that haven't run yet use it."
                                         if prev.get("decision") == "regenerate" else ".")
        if command == "run":
            return trigger_run()
        if command == "runopt":
            extra, problem = render_options(db, args)
            if problem:
                return problem
            return trigger_run(extra=extra)
        return f"Unknown command /{html.escape(command)}. Send /help."
    original_handle = handle
    def guarded_handle(command, args):
        if command == "covlock" and ":" in args:
            from .publication_lock import publication_lock
            with publication_lock(db.db_path, args.partition(":")[0]):
                return original_handle(command, args)
        valid_mutation_args = (command not in {"drop", "keep", "unkeep"} or (len(args.split()) >= 2 and all(v.isdigit() for v in args.split()[1:])))
        if command in {"review", "imgok", "drop", "keep", "unkeep"} and args.strip() and valid_mutation_args:
            ref = args.split()[0].split("@",1)[0]
            directory = _resolve_topic_dir(ref, assets_dir, db=db)
            from .publication_lock import publication_lock
            with publication_lock(db.db_path, "topic-images:" + directory.name):
                with db._conn() as conn:
                    if conn.execute("SELECT 1 FROM runs WHERE topic_id=? AND status='producing'", (directory.name,)).fetchone():
                        raise ValueError("This topic is producing; images cannot be changed or approved")
                return original_handle(command, args)
        return original_handle(command, args)
    return guarded_handle
