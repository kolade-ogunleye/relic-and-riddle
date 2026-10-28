"""Keeps an environment's .env up to date with .env.example, without showing a secret (operator, 2026-10-27).

The daemon runs sync_on_start() each time it starts (so after every release install); the command line is
scripts/env_reconcile.py.

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

from .config import BASE_DIR as ROOT  # the app root (RELIC_APP_ROOT under the production launcher)
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


def blank_credentials(secrets: List[str], running=None) -> List[str]:
    """The missing credentials that may be written as an empty line for the operator to fill in: those the code
    runs with empty now, so a blank one means the same as none (operator, 2026-10-28: "reconcile the file", not
    one by one). A credential with a built-in default (ELEVENLABS_VOICE_ID) is never blanked: empty would override it."""
    running = running or running_value
    return [k for k in secrets if running(k) == ""]


def apply(env_path: Path, missing: List[Tuple[str, str]], blanks: List[str] = ()) -> Path:
    """Backs .env up, then appends the missing settings under a dated comment; returns the backup's path."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    original = env_path.read_bytes() if env_path.exists() else b""
    backup = env_path.with_name(f".env.reconcile-{stamp}.bak")
    backup.write_bytes(original)
    text = original.decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    block = [f"# Added by scripts/env_reconcile.py {stamp} from .env.example (the template's values)"]
    block += [f"{k}={v}" for k, v in missing]
    if blanks:  # empty, as the code runs now: the value is the operator's to paste after the "="
        block += ["# Credentials to fill in (empty is the same as not set):"] + [f"{k}=" for k in blanks]
    body = text + ("" if not text or text.endswith(("\n", "\r\n")) else newline) + newline.join(block) + newline
    tmp = env_path.with_name(env_path.name + ".reconcile-tmp")
    tmp.write_bytes(body.encode("utf-8"))
    os.replace(tmp, env_path)  # whole or not at all
    return backup


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="Add the missing settings that change nothing (after a backup)")
    parser.add_argument("--include-changes", action="store_true",
                        help="With --apply: also add the missing settings whose template value differs from what runs now")
    parser.add_argument("--env", type=Path, default=ROOT / ".env")
    parser.add_argument("--example", type=Path, default=ROOT / ".env.example")
    args = parser.parse_args(argv)
    missing, differ, secrets, extra = plan(args.env, args.example)
    print(f"Missing settings that match what runs now ({len(missing)}); --apply writes them in, changing nothing:")
    for k, v in missing:
        print(f"  {k}={v}")
    print(f"Missing settings whose template value differs from what runs now ({len(differ)}); added only with "
          "--apply --include-changes:")
    for k, v, now in differ:
        print(f"  {k}: template {v!r}, runs now with {now!r}")
    print(f"Missing credentials ({len(secrets)}), to set yourself if this environment uses them:")
    for k in secrets:
        print(f"  {k}")
    print(f"Set here but not in the template ({len(extra)}), kept as they are:")
    for k in extra:
        print(f"  {k}")
    adding = missing + ([(k, v) for k, v, _ in differ] if args.include_changes else [])
    blanks = blank_credentials(secrets, plan.__defaults__[0]) if args.apply else []  # the plan's own view of what runs now
    if args.apply and (adding or blanks):
        backup = apply(args.env, adding, blanks)
        print(f"Added {len(adding)} setting(s)" + (f" and {len(blanks)} empty credential line(s) to fill in: "
              + ", ".join(blanks) if blanks else "") + f"; the previous file is kept as {backup.name}. A restart applies them.")
    elif args.apply:
        print("Nothing to add.")
    return 0


def sync_on_start(env_path: Path = None, example_path: Path = None) -> str:
    """At each daemon start (so after every release install): writes in the missing settings that change nothing,
    and returns what the operator should hear (empty when there is nothing new): what was added, by name; what
    would change behaviour, with both values; which credentials are missing, by name. Never raises."""
    import html
    root = globals()["ROOT"]  # read at the call: tests point it at a sandbox, never the checkout's own .env
    env_path, example_path = env_path or root / ".env", example_path or root / ".env.example"
    try:
        safe, differ, secrets, _ = plan(env_path, example_path)
    except Exception as e:
        return f"⚙️ Settings check did not run: {html.escape(str(e)[:200])}"
    # One setting a line, in plain words: the first message ran them together with a raw error and a timestamped
    # path, which also made it differ at every start and so repeat after every restart (operator, 2026-10-27).
    def bullets(items):
        return [f"• <code>{html.escape(x)}</code>" for x in items]
    sections = []
    if safe:
        try:
            apply(env_path, safe)
            sections.append([f"<b>Added to .env</b> ({len(safe)}), the values it already ran with:"]
                            + bullets(f"{k} = {v}" for k, v in safe))
        except PermissionError:  # production's pipeline account may not write the app folder: said, nothing changed
            sections.append([f"<b>Missing from .env</b> ({len(safe)}), the template's values, the same as what runs now:"]
                            + bullets(f"{k} = {v}" for k, v in safe)
                            + ["This account cannot write .env, so nothing was changed. To write them in (a backup "
                               "is kept): <code>python scripts/env_reconcile.py --apply</code>"])
        except OSError as e:
            sections.append([f"<b>Missing from .env</b> ({len(safe)}), not written in ({html.escape(type(e).__name__)}):"]
                            + bullets(f"{k} = {v}" for k, v in safe))
    if differ:
        sections.append([f"<b>Yours to decide</b> ({len(differ)}): missing, and the template differs from what runs now:"]
                        + bullets(f"{k}: template {v} · now {now}" for k, v, now in differ)
                        + ["To take the template's: <code>python scripts/env_reconcile.py --apply --include-changes</code>"])
    if secrets:
        sections.append([f"<b>Credentials not set</b> ({len(secrets)}), only if this environment uses them:"]
                        + bullets(secrets))
    lines = ["\n".join(section) for section in sections]
    return ("⚙️ <b>Settings</b>\n\n" + "\n\n".join(lines)) if lines else ""


if __name__ == "__main__":
    sys.exit(main())
