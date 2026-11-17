"""Every setting lives in one place: read once in src/config.py, documented in .env.example.

COMMONS_CONTACT was read in commons.py before .env was loaded and came out empty (2026-10-23), and three
of today's settings were missing from .env.example. This keeps both from drifting again.

The scan once matched only a literal os.getenv("X") / os.environ.get("X"), so a helper (_env("X") in
src/youtube_analytics.py) and a name held in a constant (os.environ.get(DEPLOY_REQUEST_ENV)) went unseen
(project review 2026-11-05, QF-51). It now also reads _env(...) and os.environ[...], resolves a module
constant to the name it holds, and refuses a read it cannot name.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_ARG = r'''\s*(?:"([A-Z0-9_]+)"|'([A-Z0-9_]+)'|([A-Za-z_][A-Za-z0-9_.]*))'''
READ = re.compile(r"(?:\bos\.getenv|\bos\.environ\.get|(?<![\w.])_env)\(" + _ARG)
# A subscript read; an assignment (os.environ["X"] = ...) sets the variable and is not a read.
SUBSCRIPT = re.compile(r"\bos\.environ\[" + _ARG + r"\s*\](?!\s*=(?!=))")
CONSTANT = re.compile(r'''^\s*([A-Z_][A-Z0-9_]*)\s*=\s*["']([A-Z0-9_]+)["']''', re.MULTILINE)
# Set by the production launcher in the process environment, never in .env: read where they are used,
# documented in .env.example's launcher block.
LAUNCHER_SET = {"RELIC_APP_ROOT", "RELIC_DEPLOY_REQUEST", "RELIC_DEPLOY_STATUS", "RELIC_RELEASE_RECEIPT"}


def reads_in(text: str) -> tuple[set, set]:
    """(names this source reads from the environment, reads whose name it cannot tell)."""
    constants = dict(CONSTANT.findall(text))
    names, unresolved = set(), set()
    for match in [*READ.finditer(text), *SUBSCRIPT.finditer(text)]:
        double, single, ident = match.groups()
        if double or single:
            names.add(double or single)
        elif ident in constants:
            names.add(constants[ident])
        else:
            unresolved.add(match.group(0))
    return names, unresolved


def _sources() -> list[Path]:
    return sorted(p for p in (ROOT / "src").rglob("*.py") if "__pycache__" not in p.parts) + [ROOT / "main.py"]


def _reads():
    found = {}
    for f in _sources():
        for key in reads_in(f.read_text(encoding="utf-8"))[0]:
            found.setdefault(key, set()).add(f.name)
    return found


def test_settings_are_read_only_in_config():
    outside = {k: sorted(v - {"config.py"}) for k, v in _reads().items()
               if v - {"config.py"} and k not in LAUNCHER_SET}
    assert not outside, f"Read these in src/config.py instead: {outside}"


def test_every_environment_read_names_its_setting():
    dynamic = {f.name: sorted(u) for f in _sources() if f.name != "config.py"
               for u in [reads_in(f.read_text(encoding="utf-8"))[1]] if u}
    assert not dynamic, f"Read a named setting in src/config.py instead: {dynamic}"


def test_every_setting_is_documented_in_the_env_example():
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    missing = sorted(k for k in set(_reads()) if not re.search(rf"\b{k}\b", example))
    assert not missing, f"Document these in .env.example: {missing}"


def test_launcher_set_names_are_read_and_set_by_a_launcher():
    reads = _reads()
    assert LAUNCHER_SET <= set(reads), f"No longer read, drop from LAUNCHER_SET: {sorted(LAUNCHER_SET - set(reads))}"
    launchers = ((ROOT / "deployment/windows/runtime/Start-ContentVmWorkload.ps1").read_text(encoding="utf-8")
                 + (ROOT / "src/service_runtime.py").read_text(encoding="utf-8"))
    unset = sorted(k for k in LAUNCHER_SET if not re.search(rf"(\$env:{k}\s*=|os\.environ\[\"{k}\"\]\s*=)", launchers))
    assert not unset, f"Not set by a launcher, so a real setting: {unset}"
