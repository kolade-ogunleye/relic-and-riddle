"""Brings this checkout's .env up to date with .env.example, without showing a secret (operator, 2026-10-27).

Run it on the machine whose .env it checks (each environment keeps its own .env; dev's is never copied):

    python scripts/env_reconcile.py            # report only: what is missing, by name
    python scripts/env_reconcile.py --apply    # add the missing non-secret settings, after a backup

What it does:
  - compares the setting NAMES in .env with those in .env.example (the release's template);
  - never prints a value from .env; only names, and the template's own (public) values;
  - --apply backs .env up (.env.reconcile-<UTC>.bak beside it), then appends each missing non-secret setting
    whose template value is what this environment already runs with (the code's default, since it is not set),
    under a dated comment: nothing changes behaviour. It never changes or removes an existing line;
  - a missing setting whose template value differs from what runs now (a spending cap, a model, a posting time)
    is listed with both values for you to decide; it is never added on its own (each environment keeps its own
    providers and caps: AGENTS.md);
  - a missing credential (a key, token, secret, password, chat or account id) is never invented: it is listed
    by name, for you to set.
"""

import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(os.getenv("RELIC_APP_ROOT") or Path(__file__).resolve().parent.parent)
# Credentials and personal details: never filled from the template (a placeholder is not a value)
SECRET = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASS|CREDENTIAL|_ID|_IDS|WEBHOOK|USER|EMAIL|PHONE|COOKIE|DSN|URL|CONTACT)$")
NAME = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def names_and_values(path: Path) -> Dict[str, str]:
    """KEY -> value for each assignment line (comments and blanks skipped; the last assignment wins)."""
    out: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = NAME.match(line)
        if m and not line.lstrip().startswith("#"):
            out[m.group(1)] = line.split("=", 1)[1].strip()
    return out


def is_secret(name: str) -> bool:
    return bool(SECRET.search(name))


def _same(running, template: str) -> bool:
    """Whether the template's text means the value the code runs with now."""
    t = template.strip().strip('"').strip("'")
    if isinstance(running, bool):
        return running == (t.lower() in ("true", "1", "yes"))
    if isinstance(running, (int, float)):
        try:
            return float(running) == float(t)
        except ValueError:
            return False
    if isinstance(running, (list, tuple, set)):
        return [str(x).strip() for x in running] == [x.strip() for x in t.split(",") if x.strip()]
    return str(running).strip().lower() == t.lower()


def running_value(name: str):
    """What this environment runs with for `name` now (src/config's attribute of that name), or a marker when
    the code does not expose it under that name."""
    try:
        sys.path.insert(0, str(ROOT))
        from src import config
    except Exception:
        return UNKNOWN
    return getattr(config, name, UNKNOWN)


UNKNOWN = object()


def plan(env_path: Path, example_path: Path, running=running_value):
    """(missing settings safe to add: same as what runs now, missing settings that would change behaviour with
    (name, template value, value now), missing credentials by name, names .env has that the template does not).
    Values from .env are read only to know a name is set; they never leave this function."""
    have = set(names_and_values(env_path)) if env_path.exists() else set()
    template = names_and_values(example_path)
    safe, differ = [], []
    for k, v in template.items():
        if k in have or is_secret(k):
            continue
        now = running(k)
        if now is not UNKNOWN and _same(now, v):
            safe.append((k, v))
        else:
            differ.append((k, v, "not exposed by the code under this name" if now is UNKNOWN else now))
    secrets = [k for k in template if k not in have and is_secret(k)]
    extra = sorted(k for k in have if k not in template)
    return safe, differ, secrets, extra


def apply(env_path: Path, missing: List[Tuple[str, str]]) -> Path:
    """Backs .env up, then appends the missing settings under a dated comment; returns the backup's path."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    original = env_path.read_bytes() if env_path.exists() else b""
    backup = env_path.with_name(f".env.reconcile-{stamp}.bak")
    backup.write_bytes(original)
    text = original.decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    block = [f"# Added by scripts/env_reconcile.py {stamp} from .env.example (the template's values)"]
    block += [f"{k}={v}" for k, v in missing]
    body = text + ("" if not text or text.endswith(("\n", "\r\n")) else newline) + newline.join(block) + newline
    tmp = env_path.with_name(env_path.name + ".reconcile-tmp")
    tmp.write_bytes(body.encode("utf-8"))
    os.replace(tmp, env_path)  # whole or not at all
    return backup


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="Add the missing non-secret settings (after a backup)")
    parser.add_argument("--env", type=Path, default=ROOT / ".env")
    parser.add_argument("--example", type=Path, default=ROOT / ".env.example")
    args = parser.parse_args(argv)
    missing, differ, secrets, extra = plan(args.env, args.example)
    print(f"Missing settings that match what runs now ({len(missing)}); --apply writes them in, changing nothing:")
    for k, v in missing:
        print(f"  {k}={v}")
    print(f"Missing settings whose template value differs from what runs now ({len(differ)}); yours to decide, never added:")
    for k, v, now in differ:
        print(f"  {k}: template {v!r}, runs now with {now!r}")
    print(f"Missing credentials ({len(secrets)}), to set yourself if this environment uses them:")
    for k in secrets:
        print(f"  {k}")
    print(f"Set here but not in the template ({len(extra)}), kept as they are:")
    for k in extra:
        print(f"  {k}")
    if args.apply and missing:
        backup = apply(args.env, missing)
        print(f"Added {len(missing)} setting(s); the previous file is kept as {backup.name}. A restart applies them.")
    elif args.apply:
        print("Nothing to add.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
