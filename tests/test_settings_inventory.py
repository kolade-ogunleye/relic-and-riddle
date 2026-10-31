"""Every setting lives in one place: read once in src/config.py, documented in .env.example.

COMMONS_CONTACT was read in commons.py before .env was loaded and came out empty (2026-10-23), and three
of today's settings were missing from .env.example. This keeps both from drifting again.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
READ = re.compile(r'os\.(?:getenv|environ\.get)\(\s*"([A-Z0-9_]+)"')
# Set by the launcher, not by .env (it decides where .env is read from).
LAUNCHER_SET = {"RELIC_APP_ROOT"}


def _reads():
    found = {}
    for f in sorted((ROOT / "src").glob("*.py")) + [ROOT / "main.py"]:
        for key in READ.findall(f.read_text(encoding="utf-8")):
            found.setdefault(key, set()).add(f.name)
    return found


def test_settings_are_read_only_in_config():
    outside = {k: sorted(v - {"config.py"}) for k, v in _reads().items() if v - {"config.py"}}
    assert not outside, f"Read these in src/config.py instead: {outside}"


def test_every_setting_is_documented_in_the_env_example():
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    missing = sorted(k for k in set(_reads()) - LAUNCHER_SET if not re.search(rf"\b{k}\b", example))
    assert not missing, f"Document these in .env.example: {missing}"
