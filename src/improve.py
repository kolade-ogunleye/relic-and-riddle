"""
src/improve.py
==============
Continuous audit and improvement.

Every run already finds things: drafts rejected on structure, graphics shortened or dropped,
fact-check findings, the video check, self-healing re-plans, release-gate holds, and your
decisions. This module keeps them, so a recurring problem is counted instead of rediscovered:

1. Findings ledger: each problem is stored with a stable class ("script.poll", "release.asset_rights").
2. Proposals: a class that recurs (MIN_RECURRENCE times in RECURRENCE_DAYS) becomes a proposal:
   - prompt tier (script.*): a one-line writing rule is drafted from the examples and TESTED on the
     before/after eval (free quota only) against the current prompt. Only a rule that wins with no
     regression is put to you; nothing changes until you tap Adopt.
   - code tier (everything else): the evidence is put to you as a card; "Queue" adds it to
     docs/improvements/queue.md for the next development session. Code is never changed automatically.
3. A weekly digest in Telegram: what recurred, what was fixed, what waits for you, how videos did.

Guardrails: the loop never touches the fact rules' strictness, the release gate or rights checks
(no class from those sources can become a prompt rule that loosens them), every adopted rule is
listed with its evidence and can be reverted with one tap, and all of it is in the database.
"""

import json
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel

from . import config

log = logging.getLogger(__name__)


def _label(text):
    from .telegram_ui import label
    return label(text)

RECURRENCE_DAYS = 14
MIN_RECURRENCE = 3
MIN_RUNS = 2  # a run problem must show in at least this many separate runs before it becomes a proposal
PATCHES_FILE = config.BASE_DIR / "data" / "prompt_patches.json"
QUEUE_FILE = config.BASE_DIR / "docs" / "improvements" / "queue.md"
EVAL_MIN_TOPICS = 4          # paired topics a candidate rule needs before it is put to you
REASONS = {"facts": "Facts or wording", "hook": "Hook / story", "pacing": "Pacing / length",
           "voice": "Voice", "pictures": "Pictures", "sound": "Music / sound", "cover": "Cover / text",
           "other": "Other"}
SCRIPT_REASONS = ("facts", "hook", "pacing", "cover")  # a regenerate for these revises the script
# What a regeneration does with each reason, said as it is (the card promised "with your feedback" for
# reasons no step acted on: 2026-10-23 audit).
REGENERATE_EFFECT = {**{r: "the script is revised for it" for r in SCRIPT_REASONS},
                     "pictures": "the pictures are re-planned for it",
                     "sound": "a different music track is used",
                     "voice": "it is logged for improvement; the voice itself is not changed automatically",
                     "other": "it is logged for improvement; the new version is made fresh"}

# Problem text -> stable class. First match wins; unknown text falls back to "<area>.other".
CLASS_PATTERNS: List[Tuple[str, str]] = [
    (r"\bpoll\b", "script.poll"),
    (r"stat_cards|stat card", "graphics.stat_cards"),
    (r"timeline", "graphics.timeline"),
    (r"scale_fact", "graphics.scale_fact"),
    (r"first line|125 characters", "script.caption_first_line"),
    (r"hashtag", "script.hashtags"),
    (r"emoji", "script.emoji"),
    (r"trope", "script.trope"),
    (r"machine-sounding|AI phrasing", "script.ai_phrasing"),
    (r"spoken|seconds|too long|too short|word count|words", "script.length"),
    (r"story slides|carousel slides|carousel_caption", "script.slides"),
    (r"hook", "script.hook"),
    (r"untraced|not traced|unsupported|disputed|fact", "script.facts"),
]

# What the automated video check says about a frame, in its own classes: the script patterns above read
# "the spoken narration states 30" as a length complaint and filed a graphic/narration mismatch under
# video.length (production, Antikythera, 2026-10-24).
QA_PATTERNS: List[Tuple[str, str]] = [
    (r"whereas|contradict|does not match|mismatch|differs|states '|inconsisten", "video.text_mismatch"),
    (r"caption|subtitle", "video.captions"),
    (r"cut off|cropped|clipped|edge|safe.zone|overlap", "video.framing"),
    (r"blur|dark|low.quality|pixelat|washed|grain", "video.picture_quality"),
    (r"wrong (place|building|site|object)|unrelated|does not show|not the ", "video.picture_relevance"),
    (r"cover|thumbnail", "video.cover"),
]


def classify_qa(text: str) -> str:
    for pattern, klass in QA_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return klass
    return "video.other"


_buffer: List[Dict[str, str]] = []
_lock = threading.Lock()


# ------------------------------------------------------------------ storage
def ensure_schema(db) -> None:
    with db._conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, topic_id TEXT, source TEXT NOT NULL,
            klass TEXT NOT NULL, detail TEXT, created_at TEXT NOT NULL)""")
        conn.execute("CREATE INDEX IF NOT EXISTS findings_klass ON findings (klass, created_at)")
        conn.execute("""CREATE TABLE IF NOT EXISTS proposals (
            id TEXT PRIMARY KEY, klass TEXT NOT NULL, tier TEXT NOT NULL, title TEXT NOT NULL,
            evidence_json TEXT, change_json TEXT, status TEXT NOT NULL, eval_json TEXT,
            created_at TEXT NOT NULL, decided_by TEXT, decided_at TEXT)""")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify(text: str, area: str = "script") -> str:
    for pattern, klass in CLASS_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return klass
    return f"{area}.other"


def note(klass: str, detail: str, source: str = "pipeline") -> None:
    """Buffers a finding made inside a stage; collect_run() files it under the run."""
    with _lock:
        _buffer.append({"klass": klass, "detail": str(detail)[:500], "source": source})


def note_problems(problems: List[str], source: str, area: str = "script") -> None:
    for p in problems:
        note(classify(p, area), p, source)


def record(db, klass: str, detail: str, source: str, run_id: Optional[str] = None, topic_id: Optional[str] = None) -> None:
    ensure_schema(db)
    with db._conn() as conn:
        conn.execute("INSERT INTO findings (run_id, topic_id, source, klass, detail, created_at) VALUES (?,?,?,?,?,?)",
                     (run_id, topic_id, source, klass, str(detail)[:500], _now()))


def collect_run(db, run_id: str, run_dir: Optional[Path], payload: Optional[Dict[str, Any]] = None,
                topic_id: Optional[str] = None, error: Optional[str] = None) -> int:
    """Files everything a run found: the buffered stage notes plus what its records say."""
    with _lock:
        items, _buffer[:] = list(_buffer), []
    payload = payload or {}
    for f in payload.get("fact_audits") or []:
        from .models import GOOD_VERDICTS
        if f.get("verdict") is not None and f.get("verdict") not in GOOD_VERDICTS:
            items.append({"klass": "script.facts", "detail": f"{f.get('verdict')}: {f.get('claim', '')}", "source": "fact_check"})
    if (payload.get("second_check") or {}).get("status") not in (None, "pass"):
        items.append({"klass": "check.second_unavailable", "detail": "Second fact check unavailable", "source": "fact_check"})
    for action in payload.get("self_healing_actions") or []:
        for line in action.get("fixing", []):
            items.append({"klass": "video." + (line.split(":")[0].strip().lower().replace(" ", "_")[:40] or "other"),
                          "detail": line, "source": "self_healing"})
    if run_dir:
        report = _read_json(Path(run_dir) / "release_report.json")
        for c in (report or {}).get("checks", []):
            if c.get("status") != "pass":
                items.append({"klass": f"release.{c['name']}", "detail": json.dumps(c.get("detail"))[:400], "source": "release"})
        qa = _read_json(Path(run_dir) / "qa_report.json")
        for issue in (qa or {}).get("issues", []) if isinstance(qa, dict) else []:
            text = issue.get("issue", "") if isinstance(issue, dict) else str(issue)
            items.append({"klass": classify_qa(text), "detail": text, "source": "qa"})
    rounds = int(payload.get("script_rounds") or 0)
    if rounds > 2:  # each round is a minute or more; what sent the drafts back is what to fix
        reasons = " | ".join(_clip(r, 120) for r in payload.get("script_round_reasons") or [])
        items.append({"klass": "script.rounds", "detail": f"{rounds} drafts: {reasons}", "source": "script"})
    if run_dir:  # every card this run sent, checked against its records (src/card_ledger.py)
        from . import card_ledger
        try:
            for klass, detail in card_ledger.audit(Path(run_dir)):
                items.append({"klass": klass, "detail": detail, "source": "self_audit"})
        except Exception as e:  # the audit never costs the run its other findings
            log.warning("Self-audit of %s failed: %s", run_id, e)
    if error:
        items.append({"klass": "run.failed." + error.split(":")[0].strip()[:40], "detail": error, "source": "run"})
    for it in items:
        record(db, it["klass"], it["detail"], it["source"], run_id=run_id, topic_id=topic_id)
    return len(items)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------ your input
def record_reason(db, run_id: str, reason: str, decision: str, text: str = "") -> None:
    """A reason chip after Reject/Regenerate (and any words you add). Kept for the regenerated run."""
    run = db.get_run(run_id) or {}
    record(db, f"review.{reason}", text or REASONS.get(reason, reason), f"decision:{decision}",
           run_id=run_id, topic_id=run.get("topic_id"))
    db.set_setting(f"decision_reason:{run_id}", json.dumps({"reason": reason, "text": text, "decision": decision}))


def operator_feedback(db, run_id: str) -> Optional[Dict[str, str]]:
    raw = db.get_setting(f"decision_reason:{run_id}") if hasattr(db, "get_setting") else None
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


class _Triage(BaseModel):
    area: str          # one of: script, facts, voice, pictures, motion, graphics, captions, sound, cover, publishing, bot, other
    summary: str       # one sentence, plain language


def sync_issue_files(db) -> int:
    """An issue whose file in ISSUES_DIR says "- Status: fixed ..." is closed in the database with that
    note (the file is where a fix is recorded, next to the report). Returns how many were closed."""
    from . import config
    ensure_schema(db)
    closed = 0
    for issue in db.list_open_issues():
        path = config.ISSUES_DIR / f"{issue['id']}.md"
        try:
            status = next((line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("- Status:")), "")
        except OSError:
            continue
        if status.startswith("- Status: fixed"):
            if db.resolve_issue(issue["id"], status.partition("fixed")[2].lstrip(" —-:")[:1000]):
                closed += 1
                with db._conn() as conn:  # a fixed report no longer counts toward a recurring problem
                    conn.execute("DELETE FROM findings WHERE source = ?", (f"issue:{issue['id']}",))
    return closed


def triage_issue(db, issue_id: str, critique: str, run_id: Optional[str]) -> str:
    """Files an /issue as a finding: area by the light model when available, else by keywords."""
    area, summary = None, critique
    try:
        from . import free_llm
        if free_llm.ready():
            t = free_llm.call("You sort a video producer's problem report into one area of a short-documentary pipeline: "
                              "script, facts, voice, pictures, motion, graphics, captions, sound, cover, publishing, bot or other. "
                              "Then restate the problem in one plain sentence.", critique, _Triage, role="checker",
                              task_class="light", interactive=True, effort="low", max_tokens=300)
            area, summary = t.area.strip().lower(), t.summary.strip() or critique
    except Exception as e:  # triage is a convenience; the issue is filed either way
        log.info("Issue triage unavailable (%s); using keywords.", e)
    klass = f"issue.{area}" if area else "issue." + classify(critique, "other").split(".")[-1]
    run = db.get_run(run_id) if run_id else None
    record(db, klass, summary, f"issue:{issue_id}", run_id=run_id, topic_id=(run or {}).get("topic_id"))
    return klass


# ------------------------------------------------------------------ proposals
def recurring(db, days: int = RECURRENCE_DAYS, min_count: int = MIN_RECURRENCE) -> List[Dict[str, Any]]:
    ensure_schema(db)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with db._conn() as conn:
        rows = conn.execute("""SELECT klass, COUNT(*) AS n, COUNT(DISTINCT run_id) AS runs FROM findings
                               WHERE created_at >= ? GROUP BY klass HAVING n >= ? ORDER BY n DESC""",
                            (since, min_count)).fetchall()
        out = []
        for r in rows:
            examples = [x["detail"] for x in conn.execute(
                "SELECT detail FROM findings WHERE klass = ? AND created_at >= ? ORDER BY id DESC LIMIT 5",
                (r["klass"], since)).fetchall()]
            out.append({"klass": r["klass"], "count": r["n"], "runs": r["runs"], "examples": examples})
    return out


def proposals(db, status: Optional[str] = None) -> List[Dict[str, Any]]:
    ensure_schema(db)
    with db._conn() as conn:
        rows = conn.execute("SELECT * FROM proposals" + (" WHERE status = ?" if status else "") + " ORDER BY created_at DESC",
                            (status,) if status else ()).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("evidence_json", "change_json", "eval_json"):
            d[k[:-5]] = json.loads(d.pop(k) or "null")
        out.append(d)
    return out


def get_proposal(db, pid: str) -> Optional[Dict[str, Any]]:
    return next((p for p in proposals(db) if p["id"] == pid), None)


def _set_status(db, pid: str, status: str, by: Optional[str] = None, eval_result=None) -> None:
    with db._conn() as conn:
        if eval_result is not None:
            conn.execute("UPDATE proposals SET eval_json = ? WHERE id = ?", (json.dumps(eval_result), pid))
        conn.execute("UPDATE proposals SET status = ?, decided_by = COALESCE(?, decided_by), decided_at = ? WHERE id = ?",
                     (status, by, _now(), pid))


# Sources whose classes may never become a writing rule: loosening them is what a self-improving
# system must not do to itself.
PROTECTED = ("release.", "check.", "run.failed")


class _Rule(BaseModel):
    rule: str


def make_proposals(db) -> List[Dict[str, Any]]:
    """Turns new recurring classes into proposals (one open proposal per class)."""
    open_classes = {p["klass"] for p in proposals(db) if p["status"] in ("testing", "ready", "queued")}
    made = []
    for rec in recurring(db):
        klass = rec["klass"]
        if klass in open_classes:
            continue
        # Recurring means across runs (21 rejections in one run's drafts are one problem) and, for reports,
        # still-open ones (a report filed before reports were linked cannot be told apart, so it never counts).
        if klass.startswith("issue."):
            with db._conn() as conn:
                linked = conn.execute("SELECT COUNT(*) FROM findings WHERE klass = ? AND source LIKE 'issue:%'", (klass,)).fetchone()[0]
            if linked < MIN_RECURRENCE:
                continue
        elif rec["runs"] < MIN_RUNS:
            continue
        pid = "imp_" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "_" + re.sub(r"\W", "", klass)[:20]
        prompt_tier = klass.startswith("script.") and not klass.startswith(PROTECTED)
        change, status = None, "ready"
        if prompt_tier:
            rule = _draft_rule(klass, rec["examples"])
            if rule:
                change, status = {"rule": rule}, "testing"
        title = (f"Writing rule for recurring '{klass}' ({rec['count']}× in {rec['runs']} runs)" if change
                 else f"Recurring '{klass}' ({rec['count']}× in {rec['runs']} runs) needs a fix")
        with db._conn() as conn:
            conn.execute("""INSERT INTO proposals (id, klass, tier, title, evidence_json, change_json, status, created_at)
                            VALUES (?,?,?,?,?,?,?,?)""",
                         (pid, klass, "prompt" if change else "code", title, json.dumps(rec), json.dumps(change), status, _now()))
        made.append(get_proposal(db, pid))
    return made


def _draft_rule(klass: str, examples: List[str]) -> Optional[str]:
    try:
        from . import free_llm
        if not free_llm.ready():
            return None
        out = free_llm.call(
            "You maintain the writing rules of a short history-documentary scriptwriter. A problem keeps recurring in its "
            "drafts. Write ONE rule, at most 40 words, that would prevent it. The rule may only make the writer stricter or "
            "clearer; it must never relax accuracy, sourcing or hedging requirements.",
            f"Problem class: {klass}\nRecent examples:\n" + "\n".join(f"- {e}" for e in examples), _Rule,
            role="writer", task_class="light", effort="low", max_tokens=300)
        rule = out.rule.strip()
        return rule if 10 <= len(rule) <= 320 else None
    except Exception as e:
        log.info("Could not draft a rule for %s (%s).", klass, e)
        return None


# ------------------------------------------------------------------ learned writing rules
def _patches() -> List[Dict[str, Any]]:
    try:
        return json.loads(PATCHES_FILE.read_text(encoding="utf-8")) if PATCHES_FILE.exists() else []
    except (OSError, ValueError):
        return []


def _save_patches(patches: List[Dict[str, Any]]) -> None:
    PATCHES_FILE.parent.mkdir(parents=True, exist_ok=True)
    PATCHES_FILE.write_text(json.dumps(patches, indent=2, ensure_ascii=False), encoding="utf-8")


def learned_rules(extra: Optional[str] = None) -> str:
    """Adopted rules, appended to the writer's rules (plus a candidate under test, for the eval)."""
    rules = [p["rule"] for p in _patches() if p.get("status") == "active"]
    if extra:
        rules.append(extra)
    return ("\n\nLearned rules (each adopted after it measurably improved drafts):\n" +
            "\n".join(f"- {r}" for r in rules)) if rules else ""


def adopt(db, pid: str, by: str) -> str:
    p = get_proposal(db, pid)
    if not p:
        return "Unknown proposal."
    if p["tier"] == "prompt" and p.get("change"):
        patches = [x for x in _patches() if x.get("id") != pid]
        patches.append({"id": pid, "klass": p["klass"], "rule": p["change"]["rule"], "status": "active",
                        "adopted_at": _now(), "adopted_by": by, "eval": p.get("eval")})
        _save_patches(patches)
        _set_status(db, pid, "adopted", by)
        return f"Adopted: the writer now follows “{p['change']['rule']}”. Revert any time with /improve."
    ev = p.get("evidence") or {}
    entry = (f"\n## {pid} · {p['klass']}\n- Queued: {_now()} by {by}\n- {p['title']}\n"
             + "".join(f"- Example: {e}\n" for e in ev.get("examples", [])))
    queue = _append_queue(entry)
    _set_status(db, pid, "queued", by)
    return f"Queued for the next development session ({queue})."


def _append_queue(entry: str) -> str:
    """Appends to the queue in docs/ (development), or under data/ where the application tree is
    read-only to the pipeline account (production, 2026-10-24: 'Access is denied'). Returns the path used."""
    for path in (QUEUE_FILE, config.DATA_DIR / "improvements" / "queue.md"):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(entry)
            return str(path.relative_to(config.BASE_DIR)).replace("\\", "/")
        except OSError as e:
            log.info("Could not write %s (%s).", path, e)
    raise OSError("No writable improvement queue (docs/improvements or data/improvements).")


def revert(db, pid: str, by: str) -> str:
    patches = _patches()
    for x in patches:
        if x.get("id") == pid and x.get("status") == "active":
            x["status"], x["reverted_at"], x["reverted_by"] = "reverted", _now(), by
    _save_patches(patches)
    _set_status(db, pid, "reverted", by)
    return "Reverted: that rule is no longer used."


def decline(db, pid: str, by: str) -> str:
    _set_status(db, pid, "declined", by)
    return "Declined. The class keeps being counted; it won't be proposed again unless it recurs after this."


# ------------------------------------------------------------------ eval gate
def judge(rows: List[Dict[str, Any]], candidate: str) -> Optional[Dict[str, Any]]:
    """Paired by topic: the candidate wins if it beats 'current' on more topics than it loses, and never
    regresses traceability (the share of claims traced to the fact sheet)."""
    def score(r):
        return r["traced"] - 0.2 * r["structure"] - 0.2 * r["tropes"] + 0.2 * r["length_ok"]
    topics = {r["topic"] for r in rows if r["variant"] == candidate}
    wins = losses = 0
    traced_delta = []
    for t in topics:
        cur = [r for r in rows if r["topic"] == t and r["variant"] == "current"]
        cand = [r for r in rows if r["topic"] == t and r["variant"] == candidate]
        if not cur or not cand:
            continue
        a = sum(map(score, cand)) / len(cand)
        b = sum(map(score, cur)) / len(cur)
        wins += a > b + 0.02
        losses += b > a + 0.02
        traced_delta.append(sum(r["traced"] for r in cand) / len(cand) - sum(r["traced"] for r in cur) / len(cur))
    paired = len(traced_delta)  # topics with drafts from both variants
    if paired < EVAL_MIN_TOPICS:
        return None
    mean_traced = sum(traced_delta) / len(traced_delta)
    return {"paired_topics": paired, "wins": wins, "losses": losses, "traced_delta": round(mean_traced, 3),
            "passed": wins > losses and mean_traced >= -0.01}


def update_gates(db, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Moves tested proposals on: a winner becomes 'ready' (a card for you), a loser 'not_better'."""
    moved = []
    for p in proposals(db, "testing"):
        verdict = judge(rows, f"candidate:{p['id']}")
        if verdict is None:
            continue
        _set_status(db, p["id"], "ready" if verdict["passed"] else "not_better", eval_result=verdict)
        moved.append(get_proposal(db, p["id"]))
    return moved


# ------------------------------------------------------------------ Telegram text
def card(p: Dict[str, Any]) -> Tuple[str, List[List[Dict[str, str]]]]:
    import html
    e = html.escape
    ev = p.get("evidence") or {}
    lines = [f"🧭 <b>Improvement proposal</b> · <code>{e(p['id'])}</code>", f"<b>{e(p['title'])}</b>", ""]
    if p.get("change"):
        lines.append(f"Proposed writing rule: <i>{e(p['change']['rule'])}</i>")
    if p.get("eval"):
        v = p["eval"]
        lines.append(f"Eval on {v['paired_topics']} topics: better on {v['wins']}, worse on {v['losses']}, "
                     f"traced claims {v['traced_delta']:+.0%}.")
    examples = [f"• {e(x[:900])}" for x in ev.get("examples", [])[:3]]
    if examples:  # whole, folded: cut at 200 characters they ended mid-word (2026-10-24)
        lines.append("<blockquote expandable>" + "\n".join(examples) + "</blockquote>")
    if p["tier"] == "prompt":
        buttons = [[{"text": "✅ Adopt rule", "callback_data": f"impok:{p['id']}"},
                    {"text": "✖ Decline", "callback_data": f"impno:{p['id']}"}]]
    else:
        buttons = [[{"text": "🛠 Queue fix", "callback_data": f"impok:{p['id']}"},
                    {"text": "✖ Dismiss", "callback_data": f"impno:{p['id']}"}]]
    return "\n".join(lines), buttons


AREA_NAMES = {"bot": "the bot", "script": "scripts", "facts": "facts", "voice": "the voice", "pictures": "pictures",
              "motion": "motion", "graphics": "graphics", "captions": "captions", "sound": "sound", "cover": "covers",
              "publishing": "publishing", "other": "other things"}
FINDING_NAMES = {"run.failed.ScriptUnavailableError": "Runs stopped: no usable script",
                 "run.failed.FactStarvedTopicError": "Runs stopped: too few verified facts",
                 "script.length": "Script too long or too short when spoken",
                 "script.caption_first_line": "Caption's first line too long",
                 "script.trailing_word": "Narration ended on a dangling word",
                 "script.structure": "Script failed a structure check",
                 "script.other": "Other script problems",
                 # the classes runs record most (the digest showed "script.facts: 63×": review, 2026-10-25)
                 "script.facts": "Script claims the fact check sent back",
                 "script.rounds": "Scripts that took several drafts",
                 "script.trope": "Stock phrases in a script",
                 "script.poll": "Poll questions that needed rewording",
                 "video.other": "Other problems the video check found",
                 "video.captions": "Captions the video check flagged",
                 "review.other": "Other reasons you gave",
                 "review.sound": "Sound, from your reviews",
                 "check.second_unavailable": "Second fact check unavailable",
                 "release.asset_rights": "Release gate: picture or music rights",
                 "release.visual_checks": "Release gate: the look at the video",
                 "graphics.emphasis_words": "Graphics: emphasised words",
                 "ui.cut_short": "Chat text cut short",
                 "ui.estimate": "Chat estimates"}
# The first part of a class, in words, for classes without a name of their own
CLASS_AREAS = {"script": "Scripts", "video": "The video", "review": "Your reviews", "check": "Checks",
               "release": "Release gate", "graphics": "Graphics", "ui": "The chat", "voice": "The voice",
               "pictures": "Pictures", "sound": "Sound", "publishing": "Publishing"}


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def plain_name(klass: str) -> str:
    """A finding's class in words for the operator (never the internal label)."""
    if klass in FINDING_NAMES:
        return FINDING_NAMES[klass]
    area, _, rest = klass.partition(".")
    if area == "issue":
        return f"Your reports about {AREA_NAMES.get(rest, rest.replace('_', ' '))}"
    if area == "run" and rest.startswith("failed."):
        return "Runs stopped: " + re.sub(r"(?<!^)(?=[A-Z])", " ", rest[7:]).replace(" Error", "").lower()
    if area == "video" and rest.startswith("beat_"):
        return f"The video check, beat {rest[5:]}"
    name = CLASS_AREAS.get(area, area.replace("_", " ").capitalize())
    return f"{name}: {rest.replace('_', ' ').replace('.', ' ')}" if rest else name


def improve_text(db) -> Tuple[str, List[List[Dict[str, str]]]]:
    """/improve in plain words: your reports, decisions waiting for you, what keeps going wrong, and the
    adopted rules (with Revert)."""
    import html
    ready = [p for p in proposals(db) if p["status"] == "ready"]
    testing = proposals(db, "testing")
    active = [x for x in _patches() if x.get("status") == "active"]
    sync_issue_files(db)
    open_reports = db.list_open_issues()
    fixed = db.count_issues("fixed") if hasattr(db, "count_issues") else 0
    lines = ["🧭 <b>Improvements</b>",
             "<i>Problems you report and the ones runs record are counted here. One that keeps happening becomes a "
             "suggested fix for you to approve; a fix is tried on a test set before it is offered.</i>", ""]
    buttons: List[List[Dict[str, str]]] = []
    lines.append(f"<b>Your reports</b> · {len(open_reports)} open · {fixed} fixed")
    for issue in open_reports[:5]:
        lines.append(f"• {html.escape(_clip(issue.get('critique') or '', 140))}")   # at a word, never mid-word
    if ready:
        lines.append(f"\n<b>Suggested fixes waiting for your decision</b> · {len(ready)}")
        for p in ready[:6]:
            lines.append(f"• {html.escape(p['title'])}")
            buttons.append([{"text": _label(f"🧭 {plain_name(p['klass'])}"), "callback_data": f"impinfo:{p['id']}"}])
    if testing:
        lines.append(f"\n<b>Being tried on the test set</b> · {len(testing)}")
        for p in testing[:6]:
            lines.append(f"• {html.escape(plain_name(p['klass']))}: <i>{html.escape((p.get('change') or {}).get('rule', ''))}</i>")
    top = recurring(db, min_count=1)[:6]
    if top:
        lines.append(f"\n<b>What keeps going wrong</b> (last {RECURRENCE_DAYS} days)")
        for r in top:
            if r["klass"].startswith("issue."):
                count = f"{r['count']} report" + ("s" if r["count"] != 1 else "")
            else:
                count = f"{r['runs']} run" + ("s" if r["runs"] != 1 else "") + (f" ({r['count']}×)" if r["count"] != r["runs"] else "")
            lines.append(f"• {html.escape(plain_name(r['klass']))}: {count}")
    lines.append(f"\n<b>Writing rules adopted</b> · {len(active) or 'none yet'}")
    for x in active:
        lines.append(f"• {html.escape(x['rule'])}")
        buttons.append([{"text": _label(f"↩ Revert {plain_name(x['klass'])}"), "callback_data": f"imprev:{x['id']}"}])
    return "\n".join(lines), buttons


def digest_text(db) -> str:
    """The weekly digest."""
    import html
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    ensure_schema(db)
    with db._conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM findings WHERE created_at >= ?", (since,)).fetchone()[0]
        runs = conn.execute("SELECT COUNT(DISTINCT run_id) FROM findings WHERE created_at >= ?", (since,)).fetchone()[0]
    lines = ["📰 <b>Weekly digest</b>", "",
             f"{n} findings across {runs} runs this week."]
    top = recurring(db, days=7, min_count=1)[:6]
    if top:
        lines.append("\n<b>Most frequent:</b>")
        lines += [f"• {html.escape(plain_name(r['klass']))}: {r['count']}×" for r in top]   # words, not "script.facts"
    ready = [p for p in proposals(db) if p["status"] == "ready"]
    if ready:
        lines.append(f"\n<b>{len(ready)} suggested fix(es) wait for you</b>: Status → Improvements")
    try:
        from .audience import digest_lines
        lines += digest_lines(db)
    except Exception as e:
        log.info("Audience section unavailable: %s", e)
    return "\n".join(lines)
