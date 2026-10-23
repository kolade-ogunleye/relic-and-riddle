"""
src/probe.py
============
/ask: answers "why did the system do X?" from the records of the run you mean.

The run is the one the question names (its number from a card, its id, or its topic), otherwise
the latest. The evidence is that run's own records: the notes on its review card, the release
gate's checks, the video check, self-healing actions, voice and music records, stage timings, the
improvement ledger's findings, and warning/error log lines from while it ran; plus this month's
budgets and the queue. The model (the free engine, light task class, so capped and recorded) may
answer only from that evidence and names the record behind each point; if the answer isn't in the
records, it says so. Without a model, the relevant records are shown as they are, never a
made-up summary.
"""

import html
import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from . import config
from .db import PipelineDatabase

log = logging.getLogger(__name__)

NOTE_KEYS = ("fact_sheet_note", "voice_note", "music_note", "motion_note", "graphics_note", "footage_note",
             "beat_image_note", "source_note")
USAGE = ("🔍 <b>Ask the studio</b>\n\n"
         "✍️ <b>Type your question now</b> and send it as a message (within 10 minutes), or copy an example:\n\n"
         "<code>/ask how many music tracks are approved?</code>\n"
         "<code>/ask why was there no AI motion in the last run?</code>\n"
         "<code>/ask run 12 why did the release gate hold it?</code>\n"
         "<code>/ask why did my last command take so long?</code>\n\n"
         "<i>Answers come from the studio's own records and say so when something isn't recorded. Tip: reply to any "
         "bot card with a question to ask about that card.</i>")


class _Answer(BaseModel):
    answer: str            # plain text, at most ~120 words
    records: List[str]     # names of the records the answer used, e.g. "release_report.json"
    recorded: bool         # False if the records don't contain the answer


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# Words that make a question about a video run rather than about the bot itself.
# A question is about one video run only when it says so ("the last run", "this video", "why did the
# render..."); "how many music tracks" or "which images" is about the studio's inventory instead.
PRODUCTION_WORDS = re.compile(r"\b(runs?|video|videos|reel|reels|render\w*|episode|documentary|review card|"
                              r"voiceover|narration|release (check|gate))\b", re.IGNORECASE)


def about_production(question: str) -> bool:
    return bool(PRODUCTION_WORDS.search(question))


def resolve_run(db: PipelineDatabase, question: str, latest: bool = True) -> Optional[Dict[str, Any]]:
    """The run the question names: 'run 12' (card number), a run id, or a topic; else the latest
    (only when latest=True: a question about the bot itself is not about the last video)."""
    m = re.search(r"\brun[ _#]?(\d+)\b", question, re.IGNORECASE)
    if m:
        return db.get_run(db.resolve_run(m.group(1)))
    m = re.search(r"\b(run_[\w]+)", question)
    if m:
        return db.get_run(m.group(1))
    with db._conn() as conn:
        rows = conn.execute("SELECT run_id, topic_id FROM runs ORDER BY created_at DESC LIMIT 60").fetchall()
    q = question.lower()
    for r in rows:
        topic = db.get_topic_by_id(r["topic_id"]) or {}
        words = [w for w in re.split(r"\W+", (topic.get("title") or r["topic_id"]).lower()) if len(w) > 4]
        if words and any(w in q for w in words):
            return db.get_run(r["run_id"])
    return db.get_run(rows[0]["run_id"]) if rows and latest else None


def _stamp(line: str) -> Optional[datetime]:
    try:
        return datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _recent_log(hours: float = 2, limit: int = 12, needle: str = "") -> List[str]:
    """Warning/error lines (or lines containing needle) logged in the last hours (local log stamps)."""
    log_file = config.LOG_DIR / "pipeline.log"
    if not log_file.exists():
        return []
    since = datetime.now() - timedelta(hours=hours)
    out = []
    with log_file.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if needle and needle not in line or not needle and " WARNING " not in line and " ERROR " not in line:
                continue
            stamp = _stamp(line)
            if stamp and stamp >= since:
                out.append(line.strip()[:240])
    return out[-limit:]


def system_evidence(db: PipelineDatabase) -> Dict[str, Any]:
    """For questions about the bot itself: what it did recently and whether its answers arrived."""
    from .local_time import show
    ev: Dict[str, Any] = {"this answer": "produced just now by the running bot, so the bot and /ask are working"}
    with db._conn() as conn:
        jobs = [json.loads(r["value"]) for r in conn.execute(
            "SELECT value FROM settings WHERE key LIKE 'operator_job:%' ORDER BY updated_at DESC LIMIT 8")]
    def state(job):  # a job left "running" by a process that has since stopped was interrupted, not running
        if job.get("state") in ("running", "queued") and job.get("owner_pid") not in (None, os.getpid()):
            return "interrupted (the bot restarted)"
        return job.get("state")

    def shown(job):
        if job.get("title") in ("Receiving media", "Receiving image"):
            return "not applicable (the upload panel shows the result)"
        if job.get("state") == "cancelled":
            return "no (cancelled)"
        return {"attempted": "yes", None: "not yet"}.get(job.get("delivery"), "no (its screen had changed)")
    ev["recent bot jobs"] = [{"what": j.get("title"), "request": (j.get("resource") or "").partition(":")[2][:80],
                              "state": state(j), "answer shown": shown(j),
                              "problem": j.get("error_detail"), "when": show(j.get("updated_at"))} for j in jobs]
    try:
        ev["recent alerts"] = [{"when": show(a.get("created_at")), "text": re.sub(r"<[^>]+>", "", a.get("text") or "")[:160]}
                               for a in db.list_recent_alerts(5)]
    except Exception as e:
        log.debug("Alerts unavailable: %s", e)
    ev["AI model (free tier) recent notes"] = [line[24:] for line in _recent_log(needle="src.free_llm", limit=6)]
    ev["recent warnings/errors (last 2 hours)"] = [line[24:] for line in _recent_log()]
    ev["scheduler paused"] = db.get_setting("scheduler_paused", "false") == "true"
    try:  # the studio's inventory, so "how many ..." questions are answered from counts, not guessed
        from . import music, topic_proposer
        from .control import ready_pools
        library = music.load_library()
        topics = db.list_all_topics()
        queued = [t for t in topics if t.get("status") == "queued"]
        ev["inventory"] = {
            "music tracks approved": sum(1 for t in library if t.get("reviewed")),
            "music tracks waiting for review": len(music.pending_tracks()),
            "music tracks by mood (approved)": {m: sum(1 for t in library if t.get("reviewed") and t.get("mood") == m) for m in music.MOODS},
            "topics queued": len(queued), "topics total": len(topics),
            "queued topics with an approved image set": ready_pools(db, queued),
            "topic ideas waiting for a decision": len(topic_proposer.pending(db)),
            "reviews waiting for a decision": len(db.list_pending_approvals()),
        }
    except Exception as e:
        log.debug("Inventory unavailable: %s", e)
    runs = db.list_recent_runs(1)
    if runs:
        from .control import short_reason
        last = runs[0]
        ev["last video run"] = {"name": db.run_label(last["run_id"]), "status": last.get("status"),
                                "reason": short_reason(last.get("error") or "", 200) or None}
    return ev


def _log_lines(run: Dict[str, Any], limit: int = 30) -> List[str]:
    """Warning and error lines logged while the run was in progress (local-time log stamps)."""
    log_file = config.LOG_DIR / "pipeline.log"
    if not log_file.exists():
        return []
    try:
        start = datetime.fromisoformat(run["created_at"]).astimezone().replace(tzinfo=None)
        end = datetime.fromisoformat(run["updated_at"]).astimezone().replace(tzinfo=None)
    except (KeyError, TypeError, ValueError):
        return []
    out = []
    with log_file.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if " WARNING " not in line and " ERROR " not in line:
                continue
            try:
                stamp = datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if start <= stamp <= end:
                out.append(line.strip()[:300])
    return out[-limit:]


def evidence(db: PipelineDatabase, run: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    ev: Dict[str, Any] = {}
    if run:
        payload = run.get("script_payload") or {}
        ev["run"] = {"label": db.run_label(run["run_id"]), "run_id": run["run_id"], "status": run.get("status"),
                     "error": run.get("error"), "started": run.get("created_at"), "updated": run.get("updated_at")}
        ev["review card notes"] = {k: payload[k] for k in NOTE_KEYS if payload.get(k)}
        if payload.get("qa_summary"):
            ev["video check"] = re.sub(r"<[^>]+>", "", payload["qa_summary"])
        if payload.get("self_healing_actions"):
            ev["self-healing actions"] = payload["self_healing_actions"]
        if payload.get("second_check"):
            ev["second fact check"] = {k: payload["second_check"].get(k) for k in ("status", "confidence", "summary")}
        run_dir = Path(run["master_video_path"]).parent if run.get("master_video_path") else config.OUTPUT_DIR / run["run_id"]
        for name in ("release_report.json", "voice.json", "music_selection.json", "timings.json", "sound_plan.json"):
            data = _read(run_dir / name)
            if data is not None:
                ev[name] = data.get("checks", data) if name == "release_report.json" else data
        motion_dir = run_dir / "motion"
        if motion_dir.is_dir():
            ev["motion files"] = sorted(p.name for p in motion_dir.iterdir())
        try:
            from . import improve
            improve.ensure_schema(db)
            with db._conn() as conn:
                ev["improvement findings"] = [f"{r['klass']}: {r['detail']}" for r in conn.execute(
                    "SELECT klass, detail FROM findings WHERE run_id = ? ORDER BY id", (run["run_id"],)).fetchall()][:30]
        except Exception as e:
            log.debug("Findings unavailable: %s", e)
        ev["log warnings/errors during the run"] = _log_lines(run)
    try:
        from . import media_budget
        ev["budgets this month"] = {"media_spent_usd": round(media_budget.spent(db), 2),
                                    "media_cap_usd": config.MEDIA_MONTHLY_BUDGET_USD,
                                    "paid_llm_fallback_caps_usd": {"light": config.PAID_FALLBACK_LIGHT_MONTHLY_USD,
                                                                   "heavy": config.PAID_FALLBACK_HEAVY_MONTHLY_USD}}
        with db._conn() as conn:
            ev["budgets this month"]["paid_llm_fallback_spent_usd"] = {
                r["task"]: round(r["s"], 2) for r in conn.execute(
                    "SELECT task, SUM(cost_usd) AS s FROM llm_usage WHERE task LIKE 'paid-fallback:%' "
                    "AND substr(created_at, 1, 7) = ? GROUP BY task", (datetime.utcnow().strftime("%Y-%m"),))}
    except Exception as e:
        log.debug("Budget evidence unavailable: %s", e)
    try:
        topics = db.list_all_topics()
        ev["queue"] = {"queued": sum(1 for t in topics if t.get("status") == "queued")}
    except Exception:
        pass
    return ev


def _deterministic(ev: Dict[str, Any]) -> str:
    """The records as they are (no model): run status, card notes, open release checks, errors."""
    e = html.escape
    lines = []
    if "recent bot jobs" in ev:  # a question about the bot itself
        lines.append("The bot is running: it produced this answer.")
        for j in ev["recent bot jobs"][:5]:
            lines.append(f"• {e(str(j['what']))} · {e(str(j['state']))} · answer shown: {e(j['answer shown'])} · {e(j['when'])}")
        last = ev.get("last video run")
        if last:
            lines.append(f"Last video run: {e(last['name'])} · {e(str(last['status']))}" + (f" — {e(last['reason'])}" if last.get("reason") else ""))
        for line in ev.get("AI model (free tier) recent notes", [])[-2:]:
            lines.append(f"<code>{e(line[:160])}</code>")
        return "\n".join(lines)
    run = ev.get("run") or {}
    if not run and "replied card (quoted text, not independent evidence)" in ev:
        quoted = ev["replied card (quoted text, not independent evidence)"]
        check = ev.get("publishing account check") or {}
        lines.append("<b>Replied card:</b>\n<blockquote>" + e(quoted[:600]) + "</blockquote>")
        if check.get("checked_at"):
            lines.append("<b>Publishing account check:</b> " + e(check["checked_at"]))
            lines.append(e("; ".join(check.get("problems") or []) or "No publishing-account problems recorded."))
        lines.append("No run was selected for this general card. These are the available records; they may not explain its wording.")
    if run:
        lines.append(f"<b>Status:</b> {e(str(run.get('status')))}" + (f" — {e(str(run.get('error'))[:200])}" if run.get("error") else ""))
    for k, v in (ev.get("review card notes") or {}).items():
        lines.append(f"• {e(re.sub(r'<[^>]+>', '', str(v)))[:220]}")
    checks = ev.get("release_report.json")
    if isinstance(checks, list):
        open_checks = [c for c in checks if c.get("status") != "pass"]
        lines.append("<b>Release gate:</b> " + ("all checks pass" if not open_checks else
                                                 "; ".join(f"{e(c['name'])} {e(c['status'])}" for c in open_checks)))
    for line in (ev.get("log warnings/errors during the run") or [])[-5:]:
        lines.append(f"<code>{e(line[24:184])}</code>")
    return "\n".join(lines) or "Nothing is recorded for this run yet."


_COUNTS = [  # (words in a "how many" question, inventory keys answered, plain label)
    (r"music|tracks?|songs?", ("music tracks approved", "music tracks waiting for review"), "Music"),
    (r"ideas?|proposals?", ("topic ideas waiting for a decision",), "Topic ideas"),
    (r"images?|photos?|pictures?|pools?", ("queued topics with an approved image set", "topics queued"), "Images"),
    (r"topics?|queue", ("topics queued", "topics total"), "Topics"),
    (r"review cards?|reviews? (waiting|pending)|pending reviews?|videos? (waiting|to review)", ("reviews waiting for a decision",), "Reviews"),
]


def _count_answer(question: str, inventory: Dict[str, Any]) -> Optional[str]:
    if not inventory or not re.search(r"\bhow many\b|\bcount\b|\bnumber of\b", question, re.IGNORECASE):
        return None
    lines = []
    for words, keys, label in _COUNTS:
        if re.search(rf"\b({words})\b", question, re.IGNORECASE):
            lines.append(f"<b>{label}</b>: " + " · ".join(f"{inventory[k]} {k.replace('music tracks ', '').replace('queued topics ', '')}" for k in keys if k in inventory))
            if label == "Music":
                moods = inventory.get("music tracks by mood (approved)") or {}
                lines.append("Approved by mood: " + ", ".join(f"{m} {n}" for m, n in moods.items()))
    return "\n".join(lines) or None


def probe_system(query: str, db: PipelineDatabase, assets_dir: Optional[Path] = None) -> str:
    question = query.strip()
    if not question:
        return USAGE
    contextual = "[Context from replied message]:" in question
    explicit_run = re.search(r"\brun_\w+|Run ID.*?:\s*\w+", question, re.I) or re.search(r"\brun[ _#]?(?:\d+|run_\w+)\b", question, re.I)
    production = bool(explicit_run) or about_production(question)
    run = resolve_run(db, question, latest=production) if not contextual or explicit_run else None
    if explicit_run and run is None:
        return "⚠️ The named run was not found. Use its run number from the review/progress card; another run has not been substituted."
    ev = evidence(db, run) if run or contextual else system_evidence(db)
    counted = _count_answer(question, ev.get("inventory") or {})
    if counted:  # a counting question is answered exactly from the records, with no model call
        return f"🔍 <b>Q:</b> <i>{html.escape(question[:300])}</i>\n<b>About:</b> the studio's records\n\n{counted}"
    if contextual:
        ev["replied card (quoted text, not independent evidence)"] = question.partition("[Context from replied message]:")[2][:1800]
        from . import accounts
        account_check = accounts.cached(db)
        ev["publishing account check"] = {k: account_check.get(k) for k in ("checked_at", "problems")}
        ev["active progress record"] = db.get_active_progress()
        if assets_dir:
            from .credits import load_credits
            for topic in db.list_all_topics():
                if topic["id"] in question:
                    credits = load_credits(Path(assets_dir) / topic["id"])
                    ev["topic image records"] = {"topic_id": topic["id"], "reviewed": credits.get("reviewed"), "image_count": len(credits.get("images", []))}
                    break
    header = f"🔍 <b>Q:</b> <i>{html.escape(question[:300])}</i>\n"
    header += f"<b>About:</b> {html.escape(ev['run']['label'])}\n" if run else "<b>About:</b> the bot and studio\n"
    body, records = None, []
    try:
        from . import free_llm
        if free_llm.ready():
            subject = ("one production run of an automated history-documentary pipeline" if run else
                       "the Telegram bot and studio itself (its inventory, recent jobs, alerts and AI-model availability)")
            ans = free_llm.call(
                f"You answer the video producer's question about {subject}. Use ONLY the records given; never guess "
                "or add general knowledge. Write like a helpful colleague: plain everyday words, short sentences, "
                "no file names, record names, field names or jargon (say 'the release check', not "
                "'release_report.json'). Lead with the direct answer. If the records do not contain the answer, set "
                "recorded=false, say what you can tell, and suggest one concrete next step (for example a command "
                "to run). In records, list the plain names of the records you used. At most 100 words.",
                f"<records>\n{json.dumps(ev, ensure_ascii=False, default=str)[:60000]}\n</records>\n\nQuestion: {question}",
                _Answer, role="checker", task_class="light", interactive=True, effort="low", max_tokens=600)
            body = html.escape(ans.answer.strip())
            records = ans.records if ans.recorded else []
            if not ans.recorded:
                body = "ℹ️ <i>Not fully covered by the records.</i>\n" + body
    except Exception as e:
        log.info("/ask model unavailable (%s); showing the records.", e)
    if body is None:
        body = "<i>(No model available; here are the records.)</i>\n" + _deterministic(ev)
    tail = f"\n\n<i>Based on: {html.escape(', '.join(records[:6]))}</i>" if records else ""
    return header + "\n" + body + tail

