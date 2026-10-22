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
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from . import config
from .db import PipelineDatabase

log = logging.getLogger(__name__)

NOTE_KEYS = ("fact_sheet_note", "voice_note", "music_note", "motion_note", "graphics_note", "footage_note",
             "beat_image_note", "source_note")
USAGE = ("🔍 <b>Ask about a run</b>\n\n"
         "<code>/ask why was there no AI motion in the last run?</code>\n"
         "<code>/ask run 12 why did the release gate hold it?</code>\n\n"
         "<i>Answers use recorded evidence. Reply to a bot card to include its context; run cards keep their run reference. Missing evidence is reported explicitly.</i>")


class _Answer(BaseModel):
    answer: str            # plain text, at most ~120 words
    records: List[str]     # names of the records the answer used, e.g. "release_report.json"
    recorded: bool         # False if the records don't contain the answer


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def resolve_run(db: PipelineDatabase, question: str) -> Optional[Dict[str, Any]]:
    """The run the question names: 'run 12' (card number), a run id, or a topic; else the latest."""
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
    return db.get_run(rows[0]["run_id"]) if rows else None


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
        lines.append(f"<code>{e(line[20:180])}</code>")
    return "\n".join(lines) or "Nothing is recorded for this run yet."


def probe_system(query: str, db: PipelineDatabase, assets_dir: Optional[Path] = None) -> str:
    question = query.strip()
    if not question:
        return USAGE
    contextual = "[Context from replied message]:" in question
    explicit_run = re.search(r"\brun[ _#]?(?:\d+|run_\w+)\b", question, re.I)
    run = resolve_run(db, question) if not contextual or explicit_run else None
    if explicit_run and run is None:
        return "⚠️ The named run was not found. Use its run number from the review/progress card; another run has not been substituted."
    ev = evidence(db, run)
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
    if run:
        header += f"<b>Run:</b> {html.escape(ev['run']['label'])}\n"
    body, records = None, []
    try:
        from . import free_llm
        if free_llm.ready():
            ans = free_llm.call(
                "You answer a video producer's question about one production run of an automated history-documentary "
                "pipeline. Use ONLY the records given; never guess or add general knowledge about how such systems "
                "usually work. Name the record each point comes from. If the records do not contain the answer, set "
                "recorded=false and say plainly that it isn't recorded, and which record would have shown it. Plain "
                "text, at most 120 words.",
                f"<records>\n{json.dumps(ev, ensure_ascii=False, default=str)[:60000]}\n</records>\n\nQuestion: {question}",
                _Answer, role="checker", task_class="light", effort="low", max_tokens=600)
            body = html.escape(ans.answer.strip())
            records = ans.records if ans.recorded else []
            if not ans.recorded:
                body = "⚪ " + body
    except Exception as e:
        log.info("/ask model unavailable (%s); showing the records.", e)
    if body is None:
        body = "<i>(No model available; here are the records.)</i>\n" + _deterministic(ev)
    tail = f"\n\n<i>From: {html.escape(', '.join(records[:6]))}</i>" if records else ""
    return header + "\n" + body + tail

