"""QF-09: Pillow 13 (12.3.0 had open advisories and decodes untrusted Wikimedia bytes), every requirement an exact pin,
a committed uv.lock with hashes that requirements.txt (the production install source) agrees with, CI auditing the
lock and failing on any advisory, and no code lifting Pillow's process-wide decompression-bomb limit."""
import re
import shutil
from pathlib import Path

import PIL
import pytest

from src import maintenance

ROOT = Path(__file__).resolve().parent.parent

WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"


def _check(root):
    # Import at the assertion so the parent run can exercise all other regressions.
    from scripts import check_lock
    return check_lock.check(root)


def test_pillow_13_is_pinned_and_installed():
    pins = maintenance.read_pins(ROOT / "requirements.txt")
    assert pins["pillow"] == "13.0.0"
    assert PIL.__version__ == pins["pillow"]   # the deployer installs requirements.txt before it runs this suite


def test_scipy_is_pinned():
    assert re.search(r"^scipy==\d+\.\d+\.\d+\s*$", (ROOT / "requirements.txt").read_text("utf-8"), re.MULTILINE)
    assert "scipy" in maintenance.read_pins(ROOT / "requirements.txt")


def test_runtime_dependencies_stay_in_requirements():
    """Operator decision 2026-11-05: edge-tts (spoken length) and pip-audit (the weekly check) stay runtime
    dependencies; the suite stays installable from requirements.txt (the deployer runs it)."""
    pins = maintenance.read_pins(ROOT / "requirements.txt")
    for name in ("edge-tts", "pip-audit", "pytest", "pytest-xdist", "httpx"):
        assert name in pins
    assert "ruff" not in pins                                   # dev tool: pyproject.toml's dev group


def test_requirements_agree_with_the_hashed_lock():
    assert _check(ROOT) == []


def _copy(tmp_path) -> Path:
    for name in ("requirements.txt", "pyproject.toml", "uv.lock"):
        shutil.copy(ROOT / name, tmp_path / name)
    return tmp_path


def test_a_pin_the_lock_does_not_hold_is_a_disagreement(tmp_path):
    root = _copy(tmp_path)
    req = root / "requirements.txt"
    req.write_text(req.read_text("utf-8").replace("pillow==13.0.0", "pillow==12.3.0"), "utf-8")
    problems = _check(root)
    assert any("pillow" in p and "pyproject.toml does not" in p for p in problems)
    assert any("uv.lock locks pillow at ['13.0.0']" in p for p in problems)


def test_an_unpinned_requirement_is_a_disagreement(tmp_path):
    root = _copy(tmp_path)
    req = root / "requirements.txt"
    req.write_text(req.read_text("utf-8").replace("scipy==1.17.1", "scipy>=1.10.0"), "utf-8")
    assert any("not an exact pin" in p and "scipy" in p for p in _check(root))


def test_a_locked_artifact_without_a_hash_is_a_disagreement(tmp_path):
    root = _copy(tmp_path)
    lock = root / "uv.lock"
    text = lock.read_text("utf-8")
    start = text.index('name = "pillow"')
    end = text.index('hash = "sha256:', start)
    lock.write_text(text[:end] + 'hash = "md5:' + text[end + len('hash = "sha256:'):], "utf-8")
    assert any("pillow 13.0.0 has an artifact without a sha256 hash" in p for p in _check(root))


def test_every_locked_package_has_hashes():
    text = (ROOT / "uv.lock").read_text("utf-8")
    assert text.count('hash = "sha256:') > 80
    assert 'name = "pillow"\nversion = "13.0.0"' in text


def test_the_dev_group_holds_ruff():
    import tomllib
    groups = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["dependency-groups"]
    assert any(d.startswith("ruff==") for d in groups["dev"])


@pytest.mark.parametrize("step", [
    "pip install uv==",
    "uv lock --check",
    "uv export --locked --all-groups",
    "pip install --require-hashes -r",
    "python scripts/check_lock.py",
    "python -m pip_audit --strict --require-hashes --disable-pip",
    "python -m ruff --version",
])
def test_ci_installs_the_lock_and_fails_on_any_advisory(step):
    assert step in WORKFLOW.read_text("utf-8")


def test_ci_no_longer_installs_unhashed_requirements():
    assert "pip install -r requirements.txt" not in WORKFLOW.read_text("utf-8")


def test_no_code_assigns_the_global_pixel_limit():
    assign = re.compile(r"MAX_IMAGE_PIXELS\s*(=[^=]|,)|,\s*Image\.MAX_IMAGE_PIXELS\s*=")
    found = [f"{p.relative_to(ROOT)}:{n}" for d in ("src", "scripts") for p in sorted((ROOT / d).rglob("*.py"))
             for n, line in enumerate(p.read_text("utf-8").splitlines(), 1)
             if "MAX_IMAGE_PIXELS" in line and assign.search(line) and not line.lstrip().startswith("#")]
    assert found == []
