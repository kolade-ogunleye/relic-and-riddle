"""
QF-79 measurement: the script inventory.

    python measure.py <repo root>

Reads the repository at <repo root> only (files and `git grep` / `git show`); no network, no import of its code.
Prints JSON.

  scripts_total         .py and .ps1 files under scripts/, the archive included
  classified            of those, listed in scripts/README.md with a defined class
  classes               files per class
  archived              files in scripts/archive/
  archived_referenced   archived files whose name appears in a tracked file outside the archive, scripts/README.md
                        and this folder (must be 0)
  unreferenced          files outside the archive whose name appears in no tracked file but themselves,
                        scripts/README.md and this folder (stand-alone tools; informational)
  archived_guarded      archived files whose first statement after the docstring and the imports is the refusal
                        `if "--i-know-this-is-archived" not in sys.argv:` ending in sys.exit (read with ast, not run)
  live_unchanged        the scripts the application, launcher, deployer, promotion procedure, development task and CI
                        use are byte for byte (LF) what they were at the QF-79 base, this row's parent commit (a
                        property of this row only; no permanent test pins their content)
"""

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
SCRIPTS = ROOT / "scripts"
BASE = "fe88d4a6"
CLASSES = ("live", "operator", "data", "validation", "eval", "one-off", "archived")
LIVE = ("__init__.py", "service_runtime.py", "release_gate.py", "runtime_probe.py", "windows_runtime.ps1",
        "eval_prompts.py", "prompt_variants.py", "fetch_commons_images.py", "fetch_music.py", "check_lock.py")
ROW = re.compile(r"^\| `([^`]+)` \| ([a-z-]+) \|")
EXCLUDE = (":!scripts/README.md", ":!docs/reviews/quality-fixes-2026-11/QF-79")


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, check=False)


def files() -> list:
    return sorted(p.relative_to(SCRIPTS).as_posix() for p in SCRIPTS.rglob("*")
                  if p.is_file() and p.suffix in {".py", ".ps1"} and "__pycache__" not in p.parts)


def rows() -> dict:
    readme = SCRIPTS / "README.md"
    if not readme.exists():
        return {}
    found = (ROW.match(line) for line in readme.read_text(encoding="utf-8").splitlines())
    return {m.group(1): m.group(2) for m in found if m}


def referencing(name: str, *exclude: str) -> list:
    out = git("grep", "-lIw", Path(name).stem, "--", ".", *EXCLUDE, *exclude)
    return out.stdout.decode("utf-8", "replace").splitlines()


def guarded(rel: str) -> bool:
    body = ast.parse((SCRIPTS / rel).read_bytes()).body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    first = next((n for n in body if not isinstance(n, (ast.Import, ast.ImportFrom))), None)
    if not isinstance(first, ast.If) or not first.body:
        return False
    test, call = first.test, first.body[0]
    return (isinstance(test, ast.Compare) and isinstance(test.ops[0], ast.NotIn)
            and isinstance(test.left, ast.Constant) and test.left.value == "--i-know-this-is-archived"
            and ast.unparse(test.comparators[0]) == "sys.argv"
            and isinstance(call, ast.Expr) and isinstance(call.value, ast.Call)
            and ast.unparse(call.value.func) == "sys.exit"
            and not any(isinstance(n, (ast.Import, ast.ImportFrom)) and "src" in ast.unparse(n)
                        for n in body[:body.index(first)]))


def main() -> None:
    every, listed = files(), rows()
    classified = [f for f in every if listed.get(f) in CLASSES]
    archived = [f for f in every if f.startswith("archive/")]
    live_same = []
    for name in LIVE:
        old = git("show", f"{BASE}:scripts/{name}").stdout.replace(b"\r\n", b"\n")
        live_same.append((SCRIPTS / name).read_bytes().replace(b"\r\n", b"\n") == old)
    result = {
        "scripts_total": len(every),
        "classified": len(classified),
        "unclassified": len(every) - len(classified),
        "classes": {c: sum(1 for f in classified if listed[f] == c) for c in CLASSES},
        "archived": len(archived),
        "archived_referenced": sum(1 for f in archived if referencing(f, ":!scripts/archive")),
        "archived_guarded": sum(1 for f in archived if f.endswith(".py") and guarded(f)),
        "unreferenced": sum(1 for f in every if not f.startswith("archive/")
                            and f != "__init__.py" and not referencing(f, f":!scripts/{f}")),
        "live_checked": len(LIVE),
        "live_unchanged": sum(live_same),
    }
    result["acceptance"] = (result["classified"] == result["scripts_total"] and result["archived"] > 0
                            and result["archived_referenced"] == 0
                            and result["archived_guarded"] == result["archived"]
                            and result["live_unchanged"] == len(LIVE))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
