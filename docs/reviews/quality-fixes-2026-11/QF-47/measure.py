"""QF-47 acceptance metric: the docs and CI say what production actually runs.

    python docs/reviews/quality-fixes-2026-11/QF-47/measure.py <repo root>

Reads <repo root>'s four production docs, its CI workflow and launcher tree, and drives its real dashboard
(`src.control.dashboard_text_and_buttons`) on a scratch database in a temporary folder, with every socket
blocked, so nothing leaves the machine. Measured, as the row states the acceptance:

  - dry_run_claims: phrases in docs/deployment.md, docs/runtime-contract.md, docs/unattended-startup.md and
    docs/production-probe-handoff.md that say production is a dry run ("daemon --dry-run", "remains in dry-run
    mode", ...). Target 0.
  - launcher_argv: the daemon command line of the launcher production installs
    (deployment/windows/runtime/content_runtime.py). Expected ["daemon"] (live since 2026-10-25).
  - handoff_copies: files left under deployment/windows/handoff/tools and handoff/tests. Target 0.
  - ci_unittest_dir: the working directory of CI's launcher-adapter unittest step. Target
    deployment/windows/runtime.
  - masthead: the dashboard's first line with the process's run mode recorded live, recorded dry, and unrecorded.
    Target: "📡 LIVE posting" / "🧪 dry run" / neither.

Prints the metric as JSON and writes measurements.json beside this script. Paths printed are repo-relative.
"""
import ast
import json
import re
import socket
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOCS = ("docs/deployment.md", "docs/runtime-contract.md", "docs/unattended-startup.md",
        "docs/production-probe-handoff.md")
DRY_PRODUCTION = re.compile(r"daemon\s+--dry-run|(?:remains|continues|stays|keeps)\s+(?:in\s+)?`?(?:--)?dry-run`?"
                            r"(?:\s+mode)?", re.IGNORECASE)
RUNS_DRY_KEY = "process_runs_dry"  # the setting the fix records; written directly so the start commit runs too


def _launcher_argv(root: Path) -> list:
    source = (root / "deployment/windows/runtime/content_runtime.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.List)
                and ast.unparse(node.targets[0]) == "sys.argv"):
            return [ast.literal_eval(e) for e in node.value.elts[1:]]
    return []


def _ci_unittest_dir(root: Path) -> str:
    workflow = (root / ".github/workflows/tests.yml").read_text(encoding="utf-8")
    found = re.search(r"working-directory:\s*(\S+)\s*\n\s*run:\s*python -m unittest", workflow)
    return found.group(1) if found else ""


def _mastheads(root: Path) -> dict:
    def refuse(*a, **k):
        raise OSError("network blocked by QF-47 measure.py")
    socket.socket.connect = refuse
    socket.create_connection = refuse
    sys.path.insert(0, str(root))
    from src import config, control, release_info
    from src.db import PipelineDatabase
    config.TELEGRAM_BOT_TOKEN = "1:measure"  # no real identity: the masthead says IDENTITY UNVERIFIED
    release_info.release_label = lambda root=None: None
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        bank = Path(tmp) / "topic_bank.json"
        bank.write_text(json.dumps({"topics": [{"id": "01_topic", "title": "Topic", "hook": "h",
                                                "location": {"name": "X", "country": "Y"}}]}), encoding="utf-8")
        db = PipelineDatabase(db_path=Path(tmp) / "measure.db", topic_bank_file=bank)
        for case, value in (("unrecorded", None), ("live", "false"), ("dry", "true")):
            if value is not None:
                db.set_setting(RUNS_DRY_KEY, value)
            text, _ = control.dashboard_text_and_buttons(db, None)
            out[case] = text.split("\n", 1)[0]
        close = getattr(db, "close", None)
        if close:
            close()
    return out


def measure(root: Path) -> dict:
    claims = {}
    for doc in DOCS:
        text = (root / doc).read_text(encoding="utf-8")
        claims[doc] = [m.group(0) for m in DRY_PRODUCTION.finditer(text)]
    handoff = root / "deployment/windows/handoff"
    copies = sorted(p.relative_to(root).as_posix() for sub in ("tools", "tests") if (handoff / sub).exists()
                    for p in (handoff / sub).rglob("*") if p.is_file())
    mastheads = _mastheads(root)
    shows = {case: ("📡 LIVE posting" in line, "🧪 dry run" in line) for case, line in mastheads.items()}
    result = {
        "dry_run_claims": sum(len(v) for v in claims.values()),
        "dry_run_claims_by_doc": claims,
        "launcher_argv": _launcher_argv(root),
        "handoff_copies": len(copies),
        "handoff_copy_paths": copies,
        "ci_unittest_dir": _ci_unittest_dir(root),
        "masthead": mastheads,
        "masthead_shows_mode": shows["live"] == (True, False) and shows["dry"] == (False, True)
                               and shows["unrecorded"] == (False, False),
    }
    result["acceptance_met"] = (result["dry_run_claims"] == 0 and result["launcher_argv"] == ["daemon"]
                                and result["handoff_copies"] == 0
                                and result["ci_unittest_dir"] == "deployment/windows/runtime"
                                and result["masthead_shows_mode"])
    return result


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: measure.py <repo root>")
    data = measure(Path(sys.argv[1]).resolve())
    text = json.dumps(data, indent=2, ensure_ascii=False)
    (HERE / "measurements.json").write_text(text + "\n", encoding="utf-8", newline="\n")
    sys.stdout.reconfigure(encoding="utf-8")
    print(text)
