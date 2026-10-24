"""
src/maintenance.py
==================
Dependency maintenance: finds outdated and vulnerable pinned packages, and applies
approved updates safely.

  check()  -> UpdatePlan: for each package pinned in requirements.txt
                safe      newer minor/patch release available (same major version)
                security  known vulnerability (pip-audit: PyPA/OSV advisories)
                major     newer major version: listed for manual review, never auto-applied
  apply()  -> on a git branch: bump the safe and security pins, install, run the test
              suite, and fast-forward main only if everything passes. Otherwise the branch
              is deleted and the previous versions are reinstalled.
"""

import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .config import BASE_DIR

log = logging.getLogger(__name__)

REQUIREMENTS = BASE_DIR / "requirements.txt"
_PIN = re.compile(r"^([A-Za-z0-9_.\-]+)==([^\s#;]+)")  # name==version, optionally followed by "; marker"


@dataclass
class Update:
    name: str
    current: str
    target: str
    reason: str          # "safe" | "security" | "major"
    advisories: List[str] = field(default_factory=list)


@dataclass
class UpdatePlan:
    updates: List[Update]

    @property
    def applicable(self) -> List[Update]:
        return [u for u in self.updates if u.reason in ("safe", "security")]

    @property
    def manual(self) -> List[Update]:
        return [u for u in self.updates if u.reason == "major"]


def _major(version: str) -> str:
    """Compatibility line: '2' for 2.x; '0.13' for 0.13.x (0.x minor bumps may break APIs)."""
    parts = version.split(".")
    return ".".join(parts[:2]) if parts[0] == "0" else parts[0]


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_pins(path: Path = REQUIREMENTS) -> Dict[str, str]:
    pins = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _PIN.match(line.strip())
        if m:
            pins[_norm(m.group(1))] = m.group(2)
    return pins


def _run(args: List[str], timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=str(BASE_DIR), capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")


def _latest_versions() -> Dict[str, str]:
    res = _run([sys.executable, "-m", "pip", "list", "--outdated", "--format=json"], timeout=300)
    if res.returncode != 0:
        raise RuntimeError(f"pip list failed: {res.stderr[-300:]}")
    return {_norm(p["name"]): p["latest_version"] for p in json.loads(res.stdout or "[]")}


def _vulnerabilities() -> Dict[str, dict]:
    """name -> {"ids": [...], "fix": lowest fix version} for vulnerable pinned packages."""
    res = _run([sys.executable, "-m", "pip_audit", "-r", str(REQUIREMENTS), "--format", "json",
                "--progress-spinner", "off"], timeout=600)
    if not res.stdout.strip():
        raise RuntimeError(f"pip-audit failed: {res.stderr[-300:]}")
    data = json.loads(res.stdout)
    deps = data["dependencies"] if isinstance(data, dict) else data
    out = {}
    for d in deps:
        vulns = d.get("vulns") or []
        if not vulns:
            continue
        fixes = sorted({v for x in vulns for v in (x.get("fix_versions") or [])}, key=_version_key)
        out[_norm(d["name"])] = {"ids": sorted({x["id"] for x in vulns}), "fix": fixes[-1] if fixes else None}
    return out


def _version_key(v: str):
    return [int(p) if p.isdigit() else p for p in re.split(r"[.\-+]", v)]


def build_plan(pins: Dict[str, str], latest: Dict[str, str], vulns: Dict[str, dict]) -> UpdatePlan:
    updates = []
    for name, current in sorted(pins.items()):
        vuln = vulns.get(name)
        newest = latest.get(name)
        if vuln:
            fix = vuln["fix"]
            if fix and _major(fix) == _major(current):
                # Prefer the newest release in the same major line when it also includes the fix.
                target = newest if newest and _major(newest) == _major(current) else fix
                updates.append(Update(name, current, target, "security", vuln["ids"]))
            else:
                updates.append(Update(name, current, fix or (newest or "?"), "major", vuln["ids"]))
        elif newest and newest != current:
            reason = "safe" if _major(newest) == _major(current) else "major"
            updates.append(Update(name, current, newest, reason))
    return UpdatePlan(updates)


def check() -> UpdatePlan:
    return build_plan(read_pins(), _latest_versions(), _vulnerabilities())


def report_text(plan: UpdatePlan) -> str:
    import html
    if not plan.updates:
        return "🧰 <b>Weekly update check</b>: all pinned packages are current, no known vulnerabilities."
    lines = ["🧰 <b>Weekly update check</b>"]
    for u in plan.updates:
        icon = {"security": "🔴", "safe": "🟢", "major": "🟡"}[u.reason]
        extra = f" — {len(u.advisories)} advisor{'y' if len(u.advisories) == 1 else 'ies'}" if u.advisories else ""
        lines.append(f"{icon} {html.escape(u.name)} {html.escape(u.current)} → {html.escape(u.target)} ({u.reason}){extra}")
    if plan.applicable:
        lines.append("Approve to apply 🔴/🟢 on a branch; it merges only if all tests pass.")
    if plan.manual:
        lines.append("🟡 major versions are left for manual review.")
    return "\n".join(lines)


def _write_pins(updates: List[Update], path: Path = REQUIREMENTS) -> None:
    targets = {u.name: u.target for u in updates}
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _PIN.match(line.strip())
        if m and _norm(m.group(1)) in targets:
            # Replace only the version; keep any environment marker ("; sys_platform == ...").
            line = line[:m.start(2)] + targets[_norm(m.group(1))] + line[m.end(2):]
        out.append(line)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def apply(plan: UpdatePlan, include: Optional[List[str]] = None) -> str:
    """Applies the plan's safe/security updates (or the named ones) with test-gated rollback."""
    updates = [u for u in plan.updates if u.name in include] if include else plan.applicable
    if not updates:
        return "Nothing to apply."
    # Tracked source changes block the update; the assets and data a live run writes do not (they are
    # always dirty on a working installation, so the weekly apply never ran: 2026-10-24).
    if _run(["git", "status", "--porcelain", "--untracked-files=no", "--", ".", ":!assets", ":!data"]).stdout.strip():
        raise RuntimeError("Working tree has uncommitted changes outside assets/ and data/; commit or stash them first.")
    base = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    branch = f"updates/{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    _run(["git", "checkout", "-b", branch])
    try:
        _write_pins(updates)
        install = _run([sys.executable, "-m", "pip", "install", "-q", "-r", str(REQUIREMENTS)])
        if install.returncode != 0:
            raise RuntimeError(f"pip install failed: {install.stderr[-400:]}")
        tests = _run([sys.executable, "-m", "pytest", "-q"])
        if tests.returncode != 0:
            raise RuntimeError("tests failed:\n" + "\n".join(tests.stdout.strip().splitlines()[-8:]))
        summary = ", ".join(f"{u.name} {u.current}->{u.target}" for u in updates)
        _run(["git", "commit", "-q", "-m", f"Update dependencies: {summary}", "--", str(REQUIREMENTS)])  # the pins only
        _run(["git", "checkout", base])
        merge = _run(["git", "merge", "--ff-only", branch])
        if merge.returncode != 0:
            raise RuntimeError(f"merge failed: {merge.stderr[-300:]}")
        _run(["git", "branch", "-d", branch])
        return f"✅ Updated {summary}. All tests passed. Restart the daemon to load the new versions."
    except Exception:
        _run(["git", "checkout", "--", str(REQUIREMENTS)])
        _run(["git", "checkout", base])
        _run(["git", "branch", "-D", branch])
        _run([sys.executable, "-m", "pip", "install", "-q", "-r", str(REQUIREMENTS)])  # restore old versions
        raise
