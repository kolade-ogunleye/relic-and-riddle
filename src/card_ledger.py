"""
src/card_ledger.py
==================
Every operator-facing card a run sends is kept with the run (cards.jsonl in its folder), and when the
run ends each card is checked against the run's own records. The mismatches the operator found by eye
on 2026-10-24 (Approve offered on a release-blocked version, "(by -)" in a stamp, a reason cut mid-word,
a time estimate of 79 min for a 20-min run) were all visible by comparing a card with the record; this
makes that comparison every time and files what it finds in the improvement ledger (src/improve.py),
where a recurring finding becomes a proposal.
"""

import html
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import config

log = logging.getLogger(__name__)

LEDGER = "cards.jsonl"
PLACEHOLDER_NAME = re.compile(r"\bby (?:-|None|unknown|\d{6,})(?!\w)")  # "(by -)", "by 8728071488"
ROOM_FOR_MORE = 3000   # a card shorter than this had room for the words a "…" cut off (Telegram allows 4096)
SESSION_GAP_SECONDS = 1800  # uncovered time this long between stages: a later session of the same run (a re-render)


def _callbacks(buttons: Any) -> List[str]:
    """Every callback_data in a keyboard, however it is nested."""
    found: List[str] = []
    if isinstance(buttons, dict):
        if buttons.get("callback_data"):
            found.append(str(buttons["callback_data"]))
        for v in buttons.values():
            if isinstance(v, (list, dict)):
                found += _callbacks(v)
    elif isinstance(buttons, list):
        for b in buttons:
            found += _callbacks(b)
    return found


def record(run_id: Optional[str], kind: str, text: str, buttons: Any = None, message_id: Optional[int] = None) -> None:
    """Appends one card to the run's ledger. Never raises: a ledger must not break a run."""
    if not run_id or not text:
        return
    try:
        path = config.OUTPUT_DIR / run_id / LEDGER
        path.parent.mkdir(parents=True, exist_ok=True)
        line = {"at": datetime.now(timezone.utc).isoformat(), "kind": kind, "text": text,
                "buttons": _callbacks(buttons), "message_id": message_id}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except (OSError, TypeError, ValueError) as e:
        log.debug("Card not recorded for %s: %s", run_id, e)


def cards(run_dir: Path) -> List[Dict[str, Any]]:
    path = Path(run_dir) / LEDGER
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except (OSError, ValueError):
        return None


def run_seconds(timings: Optional[list]) -> Optional[float]:
    """A finished run's length from its stage timings (first start to last finish), None if it did not
    reach its review package."""
    if not timings or not any(r.get("stage") == "review_proxy" for r in timings):
        return None
    try:
        stamps = [datetime.fromisoformat(r["at"].replace("Z", "+00:00")).timestamp() for r in timings]
        # A re-rendered run's timings carry every session; the length is the last one's (the dashboard
        # showed "median 89 min" for a 20-minute re-render whose stamps spanned the morning, 2026-10-24).
        start = 0
        for i in range(1, len(timings)):
            if stamps[i] - float(timings[i]["seconds"]) - stamps[i - 1] > SESSION_GAP_SECONDS:
                start = i
        session = timings[start:]
        if not any(r.get("stage") == "review_proxy" for r in session):
            return None
        return (max(stamps[start:]) - min(stamps[start:])) + float(session[0]["seconds"])
    except (KeyError, ValueError, TypeError):
        return None


def estimate_minutes(text: str) -> Optional[float]:
    """The minutes a progress card promised ("Time left: ~19 min", "about a minute"), None if it did not."""
    plain = re.sub(r"<[^>]+>", "", text)
    m = re.search(r"Time left:\s*~?(\d+)\s*min", plain)
    if m:
        return float(m.group(1))
    if re.search(r"Time left:\s*(?:about|under) a minute", plain):
        return 1.0
    return None


def audit(run_dir: Path) -> List[Tuple[str, str]]:
    """(klass, detail) for every card that did not match the run's records."""
    run_dir = Path(run_dir)
    sent = cards(run_dir)
    if not sent:
        return []
    findings: List[Tuple[str, str]] = []
    report = _read_json(run_dir / "release_report.json")
    actual = run_seconds(_read_json(run_dir / "timings.json"))

    # 1. Approve offered while the release gate blocked that version.
    if report:
        from .release import blocking_checks
        blocked = blocking_checks(report, dry_run=bool(report.get("dry_run")))
        for c in sent:
            if c.get("kind") == "review" and any(b.startswith("approve:") for b in c.get("buttons", [])) and blocked:
                findings.append(("ui.approve_on_blocked",
                                 "The review card offered Approve while the release gate blocked this version: "
                                 + "; ".join(x["name"] for x in blocked)))
                break

    # 2. A placeholder where a name should be.
    for c in sent:
        hit = PLACEHOLDER_NAME.search(re.sub(r"<[^>]+>", "", c.get("text", "")))
        if hit:
            findings.append(("ui.placeholder_name", f"A {c.get('kind')} card says '{hit.group(0)}' where a name should be"))
            break

    # 3. Words cut off with room to spare.
    for c in sent:
        text = c.get("text", "")
        if "…" in text and len(text) < ROOM_FOR_MORE:
            cut = re.search(r"(\S+)…", text)
            findings.append(("ui.cut_short", f"A {c.get('kind')} card cut its text at '{cut.group(1) if cut else '…'}' "
                                             f"with room for {ROOM_FOR_MORE - len(text)} more characters"))
            break

    # 3b. A line said twice on one card (the cancelled card printed the run's label as its title and
    #     again in a code line, 2026-10-24).
    for c in sent:
        raw = c.get("text", "")
        plain = html.unescape(re.sub(r"<[^>]+>", "", raw))
        spans = [html.unescape(s).strip(" []·") for s in re.findall(r"<(?:b|i|code)>(.*?)</(?:b|i|code)>", raw)]
        twice = next((s for s in spans if len(s) >= 20 and plain.count(s) >= 2), None)
        if twice:
            findings.append(("ui.repeated_line", f"A {c.get('kind')} card says '{twice[:60]}' twice"))
            break

    # 4. The first estimate against the run's real length (only once the run finished).
    if actual:
        first = next((c for c in sent if c.get("kind") == "progress" and estimate_minutes(c.get("text", ""))), None)
        if first:
            promised, took = estimate_minutes(first["text"]), actual / 60
            if promised > took * 2 + 5 or promised < took / 2 - 5:
                findings.append(("ui.estimate", f"The first progress card promised ~{promised:.0f} min; the run took {took:.0f} min"))

    # 5. A progress card still on an early step when the run reached its review.
    if actual:
        last = next((c for c in reversed(sent) if c.get("kind") == "progress"), None)
        if last:
            m = re.search(r"🔄 <b>Step (\d+)/(\d+)", last.get("text", ""))
            if m and int(m.group(1)) < int(m.group(2)) - 1:
                findings.append(("ui.progress_stale", f"The last progress card showed step {m.group(1)} of {m.group(2)} "
                                                      "although the run reached its review package"))
    return findings
