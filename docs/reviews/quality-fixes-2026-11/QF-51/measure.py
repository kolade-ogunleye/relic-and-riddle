"""QF-51 acceptance metric: config drift. Usage: python measure.py <repo root>.
Prints JSON and writes measurements.json next to this script.

1. Settings inventory, with the extended scan (os.getenv / os.environ.get / os.environ[...] / _env(...),
   a module constant resolved to the name it holds, a read whose name cannot be told counted): names read
   outside src/config.py (launcher-set names excepted), unnamed reads, names missing from .env.example.
2. The repository's own tests/test_settings_inventory.py, run with pytest: the acceptance is that the
   inventory test (the extended one) fails on the start tree and passes on the fix; the start tree's own
   literal-only test is reported too, to show it did not see the drift.
3. The real log masker (src.ops.SecretMasker) in a fresh interpreter with dummy YOUTUBE_ANALYTICS_* values:
   are the three names in SECRET_NAMES, and does a log line keep the secret or refresh token?
No network: nothing is called but the masker and the source scan. Paths printed are repo-relative.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

_ARG = r'''\s*(?:"([A-Z0-9_]+)"|'([A-Z0-9_]+)'|([A-Za-z_][A-Za-z0-9_.]*))'''
READ = re.compile(r"(?:\bos\.getenv|\bos\.environ\.get|(?<![\w.])_env)\(" + _ARG)
SUBSCRIPT = re.compile(r"\bos\.environ\[" + _ARG + r"\s*\](?!\s*=(?!=))")
CONSTANT = re.compile(r'''^\s*([A-Z_][A-Z0-9_]*)\s*=\s*["']([A-Z0-9_]+)["']''', re.MULTILINE)
LAUNCHER_SET = {"RELIC_APP_ROOT", "RELIC_DEPLOY_REQUEST", "RELIC_DEPLOY_STATUS", "RELIC_RELEASE_RECEIPT"}
YT = ("YOUTUBE_ANALYTICS_CLIENT_ID", "YOUTUBE_ANALYTICS_CLIENT_SECRET", "YOUTUBE_ANALYTICS_REFRESH_TOKEN")
DUMMY = {"YOUTUBE_ANALYTICS_CLIENT_ID": "qf51-client-id.apps.example",
         "YOUTUBE_ANALYTICS_CLIENT_SECRET": "QF51-dummy-client-secret",
         "YOUTUBE_ANALYTICS_REFRESH_TOKEN": "1//QF51-dummy-refresh-token"}


def inventory(root: Path) -> dict:
    sources = sorted(p for p in (root / "src").rglob("*.py") if "__pycache__" not in p.parts) + [root / "main.py"]
    reads, dynamic = {}, {}
    for f in sources:
        text = f.read_text(encoding="utf-8")
        constants = dict(CONSTANT.findall(text))
        for m in [*READ.finditer(text), *SUBSCRIPT.finditer(text)]:
            double, single, ident = m.groups()
            name = double or single or constants.get(ident)
            rel = f.relative_to(root).as_posix()
            if name:
                reads.setdefault(name, set()).add(rel)
            elif rel != "src/config.py":
                dynamic.setdefault(rel, set()).add(m.group(0))
    example = (root / ".env.example").read_text(encoding="utf-8")
    outside = {k: sorted(v - {"src/config.py"}) for k, v in reads.items()
               if v - {"src/config.py"} and k not in LAUNCHER_SET}
    return {"settings_read": len(reads),
            "read_outside_config": dict(sorted(outside.items())),
            "unnamed_reads_outside_config": {k: sorted(v) for k, v in sorted(dynamic.items())},
            "undocumented_in_env_example": sorted(k for k in reads if not re.search(rf"\b{k}\b", example))}


def own_inventory_test(root: Path) -> str:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    r = subprocess.run([sys.executable, "-m", "pytest", "tests/test_settings_inventory.py", "-q", "-p", "no:cacheprovider"],
                       cwd=root, capture_output=True, text=True, env=env, timeout=600, check=False)
    lines = [ln for ln in r.stdout.splitlines() if re.search(r"\d+ (passed|failed)", ln)]
    return re.sub(r" in [\d.]+s", "", lines[-1].strip("= ")) if lines else f"exit {r.returncode}"


def masker(root: Path) -> dict:
    code = (
        "import json, sys\n"
        "from src.ops import SecretMasker\n"
        "names = set(SecretMasker.SECRET_NAMES)\n"
        "yt = json.loads(sys.argv[1])\n"
        "line = 'token refresh refused: ' + ' '.join(yt.values())\n"
        "out = SecretMasker().mask(line)\n"
        "print(json.dumps({'in_secret_names': sorted(n for n in yt if n in names),\n"
        "                  'shown_in_log_line': sorted(n for n, v in yt.items() if v in out)}))\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("YOUTUBE_ANALYTICS_")}
    env.update(DUMMY, PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, "-c", code, json.dumps(DUMMY)], cwd=root, capture_output=True, text=True,
                       env=env, timeout=300, check=False)
    if r.returncode:
        return {"error": r.stderr.strip().splitlines()[-1][:300]}
    return json.loads(r.stdout.strip().splitlines()[-1])


def main(root: Path) -> dict:
    inv = inventory(root)
    mask = masker(root)
    result = {
        "inventory": inv,
        "inventory_drift": len(inv["read_outside_config"]) + len(inv["unnamed_reads_outside_config"])
        + len(inv["undocumented_in_env_example"]),
        "own_tests/test_settings_inventory.py": own_inventory_test(root),
        "masker": mask,
    }
    result["acceptance_met"] = (result["inventory_drift"] == 0
                                and "failed" not in result["own_tests/test_settings_inventory.py"]
                                and mask.get("in_secret_names") == sorted(YT)
                                and not [n for n in mask.get("shown_in_log_line", []) if n != "YOUTUBE_ANALYTICS_CLIENT_ID"])
    return result


if __name__ == "__main__":
    out = main(Path(sys.argv[1]).resolve())
    text = json.dumps(out, indent=2, sort_keys=False)
    print(text)
    (Path(__file__).resolve().parent / "measurements.json").write_text(text + "\n", encoding="utf-8", newline="\n")
