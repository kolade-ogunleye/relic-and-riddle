"""
scripts/check_lock.py
=====================
requirements.txt (the production install source) must agree with the committed, hashed uv.lock: every line an
exact pin, the same pins and markers as pyproject.toml's dependencies (what uv locks), each locked at that version,
and every locked package carrying hashes. CI runs it after ``uv lock --check`` (the lock is current for
pyproject.toml). Exit status 1 lists each disagreement.

    python scripts/check_lock.py [repo root]
"""

import re
import sys
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def _key(req: Requirement) -> tuple[str, str, str]:
    return canonicalize_name(req.name), str(req.specifier), str(req.marker or "")


def requirements_pins(path: Path) -> tuple[list[Requirement], list[str]]:
    reqs, problems = [], []
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = re.sub(r"\s+#.*$", "", raw).strip()
        if not line or line.startswith("#"):
            continue
        req = Requirement(line)
        spec = list(req.specifier)
        if len(spec) != 1 or spec[0].operator != "==" or "*" in spec[0].version:
            problems.append(f"requirements.txt:{n}: {line!r} is not an exact pin (name==version)")
        reqs.append(req)
    return reqs, problems


def check(root: Path) -> list[str]:
    reqs, problems = requirements_pins(root / "requirements.txt")
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    declared = {_key(Requirement(d)) for d in project.get("dependencies", [])}
    pinned = {_key(r) for r in reqs}
    for name, spec, marker in sorted(pinned - declared):
        problems.append(f"requirements.txt has {name}{spec}{'; ' + marker if marker else ''}, pyproject.toml does not")
    for name, spec, marker in sorted(declared - pinned):
        problems.append(f"pyproject.toml has {name}{spec}{'; ' + marker if marker else ''}, requirements.txt does not")

    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    locked: dict[str, set[str]] = {}
    for pkg in lock.get("package", []):
        source = pkg.get("source", {})
        if "virtual" in source or "editable" in source:   # the project itself
            continue
        name = canonicalize_name(pkg["name"])
        locked.setdefault(name, set()).add(pkg["version"])
        artifacts = ([pkg["sdist"]] if "sdist" in pkg else []) + pkg.get("wheels", [])
        if not artifacts or not all(str(a.get("hash", "")).startswith("sha256:") for a in artifacts):
            problems.append(f"uv.lock: {name} {pkg['version']} has an artifact without a sha256 hash")
    for req in reqs:
        name, version = canonicalize_name(req.name), next(iter(req.specifier)).version
        if version not in locked.get(name, set()):
            problems.append(f"uv.lock locks {name} at {sorted(locked.get(name, set())) or 'nothing'}, "
                            f"requirements.txt pins {version}")
    return problems


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent.parent
    problems = check(root)
    for p in problems:
        print(p)
    print("requirements.txt agrees with uv.lock" if not problems else f"{len(problems)} disagreement(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
