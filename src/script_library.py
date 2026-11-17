"""Research and scripts already paid for, reused instead of being bought again.

data/script_library/<topic_id>/ holds a fact sheet, a script that passed the fact check against exactly that
sheet, and source.json (where they came from). It ships with a release: production uses development's step-1
work instead of paying for it again (operator, 2026-10-27). It never overrides an environment's own work:
a fact sheet is copied in only where the topic has none, and a script is used only while the topic's fact
sheet is byte-for-byte the one it was checked against, and only if it passes today's checks. Human review
still decides every run.

A run can also start afresh from a step (FRESH_FROM, `--fresh-from`) when a test needs that step and those
after it made again: nothing is reused from that step on, from the library or from an earlier run.
"""

import json
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import DATA_DIR
from .credits import sha256_file

log = logging.getLogger(__name__)

LIBRARY_DIR = DATA_DIR / "script_library"
SHEET_FILE, SCRIPT_FILE, SOURCE_FILE = "fact_sheet.json", "verified_script.json", "source.json"

# The steps a run can reuse, in order: starting afresh from one makes it and every later one again.
STEPS = ("facts", "script", "voice", "music", "clips")
FRESH_FROM: Optional[str] = None


def set_fresh_from(step: Optional[str]) -> None:
    global FRESH_FROM
    if step is not None and step not in STEPS:
        raise ValueError(f"Unknown step '{step}': one of {', '.join(STEPS)}")
    FRESH_FROM = step


def reuses(step: str) -> bool:
    """Whether this run may reuse `step` made before (by the library, an earlier run or a regeneration's)."""
    return FRESH_FROM is None or STEPS.index(step) < STEPS.index(FRESH_FROM)


def entry_dir(topic_id: str) -> Path:
    return LIBRARY_DIR / topic_id


def seed_fact_sheet(topic_id: str, sheet_path: Path) -> bool:
    """Copies the library's fact sheet to `sheet_path` when the topic has none there. Never overwrites."""
    source = entry_dir(topic_id) / SHEET_FILE
    if not reuses("facts") or sheet_path.exists() or not source.is_file():
        return False
    try:
        sheet_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, sheet_path)
    except OSError as e:
        log.warning("Could not copy the library's fact sheet for %s: %s", topic_id, e)
        return False
    log.info("Fact sheet for %s taken from the script library (research already done).", topic_id)
    return True


def kept_script(topic_id: str, sheet_path: Path) -> Optional[Dict[str, Any]]:
    """The library's script for this topic, if it was checked against this very fact sheet. Its fact-check
    record then points at this environment's copy of that sheet, so the record stays current only while the
    sheet and today's check rules are those it passed under. Today's script checks are the caller's."""
    source = entry_dir(topic_id) / SCRIPT_FILE
    if not reuses("script") or not source.is_file() or not sheet_path.is_file():
        return None
    try:
        saved = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    review = (saved.get("fact_review") or {}) if isinstance(saved, dict) else {}
    if not (saved.get("guardrail_passed") and saved.get("narration_script")):
        return None
    library_sheet = entry_dir(topic_id) / SHEET_FILE
    # The same research, not merely the same file: a checkout's line endings change the bytes, not the facts.
    if review.get("sheet_sha256") != sha256_file(library_sheet) or _sheet(library_sheet) != _sheet(sheet_path):
        log.info("Library script for %s not used: this topic's fact sheet is not the one it was checked against.", topic_id)
        return None
    review.update(sheet_path=str(Path(sheet_path).resolve()), sheet_sha256=sha256_file(sheet_path))
    if review.get("reference_path"):  # its digest is what the review checks; the path is this checkout's copy
        from .fact_evidence import REFERENCE_FILE
        review["reference_path"] = str(REFERENCE_FILE.resolve())
    saved["fact_review"] = review
    saved["from_library"] = (json.loads((entry_dir(topic_id) / SOURCE_FILE).read_text(encoding="utf-8"))
                             if (entry_dir(topic_id) / SOURCE_FILE).is_file() else {"topic_id": topic_id})
    return saved


def _sheet(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def export(topic_id: str, sheet_path: Path, script: Optional[Dict[str, Any]], source: Dict[str, Any],
           library: Optional[Path] = None) -> List[Path]:
    """Writes one library entry: the fact sheet and (when there is one) the script checked against it."""
    out = (library or LIBRARY_DIR) / topic_id
    out.mkdir(parents=True, exist_ok=True)
    written = [out / SHEET_FILE]
    if script is not None and (script.get("fact_review") or {}).get("sheet_sha256") != sha256_file(sheet_path):
        raise ValueError(f"{topic_id}: the script was not checked against this fact sheet")
    # LF, as the repository keeps it, so the entry's record matches the file every checkout gets
    (out / SHEET_FILE).write_bytes(Path(sheet_path).read_bytes().replace(b"\r\n", b"\n"))
    if script is not None:
        script = json.loads(json.dumps(script))
        script["fact_review"].update(sheet_sha256=sha256_file(out / SHEET_FILE), sheet_path=SHEET_FILE)
        if script["fact_review"].get("reference_path"):   # unsigned; kept_script re-points it at the checkout's copy
            from .audio_provenance import tracked_path
            script["fact_review"]["reference_path"] = tracked_path(script["fact_review"]["reference_path"])
        (out / SCRIPT_FILE).write_text(json.dumps(script, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        written.append(out / SCRIPT_FILE)
    (out / SOURCE_FILE).write_text(json.dumps(source, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    written.append(out / SOURCE_FILE)
    return written
