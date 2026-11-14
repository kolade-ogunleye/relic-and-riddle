"""QF-117: the approved patch bumps (decisions, tools audit 2026-11-14 item 5), one lock change.

pydantic 2.14.3 (and the pydantic-core it pins exactly), fonttools 4.68.0, soundfile 0.14.0, pytest 9.2.0, ruff
0.17.5 (dev group) and uv 0.13.8. anthropic is excluded (stays 1.13.0). The files are checked, not the installed
packages: a venv synced from the parent's lock still runs this suite until it is synced again."""
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUMPED = {"pydantic": "2.14.3", "fonttools": "4.68.0", "soundfile": "0.14.0", "pytest": "9.2.0"}
DEV = {"ruff": "0.17.5"}
UNCHANGED = {"anthropic": "1.13.0"}
TRANSITIVE = {"pydantic-core": "2.50.4"}    # pydantic 2.14.3 requires pydantic-core==2.50.4 exactly


def _pyproject():
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _locked() -> dict[str, set[str]]:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    out: dict[str, set[str]] = {}
    for pkg in lock["package"]:
        out.setdefault(pkg["name"], set()).add(pkg["version"])
    return out


def test_pyproject_pins_the_bumped_versions():
    data = _pyproject()
    deps = set(data["project"]["dependencies"])
    for name, version in {**BUMPED, **UNCHANGED}.items():
        assert f"{name}=={version}" in deps
    assert {f"{n}=={v}" for n, v in DEV.items()} <= set(data["dependency-groups"]["dev"])


def test_requirements_txt_pins_the_bumped_versions():
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    for name, version in {**BUMPED, **UNCHANGED}.items():
        assert re.search(rf"^{re.escape(name)}=={re.escape(version)}(\s|$)", text, re.MULTILINE), name
    assert not re.search(r"^ruff==", text, re.MULTILINE)       # dev tool: pyproject.toml's dev group only


def test_the_lock_holds_exactly_the_bumped_versions():
    locked = _locked()
    for name, version in {**BUMPED, **DEV, **UNCHANGED, **TRANSITIVE}.items():
        assert locked[name] == {version}, name


def test_ci_installs_the_uv_that_pyproject_requires():
    required = _pyproject()["tool"]["uv"]["required-version"]
    assert required == "==0.13.8"
    workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    installs = re.findall(r"pip install uv==(\S+)", workflow)
    assert installs == [required.removeprefix("==")]
