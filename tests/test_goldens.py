"""Every frozen design still renders exactly as its golden (QF-29a; scripts/goldens.py, tests/goldens/).

A design enters tests/goldens/designs.py when the operator agrees it is FROZEN and the lander takes its golden; from
then on any change that moves one of its pixels fails here (a "design changed" failure: re-validate, then retake),
and a new toolchain fails as "toolchain changed: re-validate" (accept it with scripts/goldens.py accept-toolchain).
One test per design, so the CI shards (QF-135) spread them; on GitHub Actions a failing design leaves its render,
variant record and diff board in the shard's artifact ($RUNNER_TEMP/shard/golden-actuals/<design>/).
"""
from __future__ import annotations

import pytest

from scripts import goldens

DESIGNS = sorted(goldens.load_registry())
TAKEN = sorted(p.parent.name for p in goldens.STORE.glob("*/manifest.json"))
NONE_YET = "no-frozen-design-yet"


def test_store_holds_together():
    """Every registered design has a golden and every golden a registered design; each manifest is whole, its
    inputs are committed (never gitignored) and still the files it pinned."""
    assert TAKEN == DESIGNS, f"registry {DESIGNS} != goldens {TAKEN}"
    registry = goldens.load_registry()
    for name in TAKEN:
        manifest = goldens.load_manifest(goldens.STORE, name)
        assert goldens.manifest_problems(goldens.STORE, name, manifest, registry[name]) == []
        assert goldens.input_problems(goldens.ROOT, list(manifest["inputs"])) == []
    stray = [p.name for p in goldens.STORE.iterdir()
             if p.name not in ("designs.py", "__pycache__") and not (p / "manifest.json").is_file()]
    assert stray == [], f"files in tests/goldens/ that are not a golden: {stray}"
    # a golden folder holds golden data only (its manifest and reference images: the files a toolchain
    # acceptance may see change); anything else there would be design code hidden among the data
    for folder in (goldens.STORE / name for name in TAKEN):
        other = [p.name for p in folder.iterdir() if p.name != "__pycache__"
                 and not goldens.is_golden_data(p.relative_to(goldens.ROOT).as_posix())]
        assert other == [], f"{folder.name}: files that are not golden data: {other}"


@pytest.mark.parametrize("design", DESIGNS or [NONE_YET])
def test_golden(design):
    if design == NONE_YET:   # nothing frozen yet: the store must be empty too (test_store_holds_together)
        assert not DESIGNS and not TAKEN
        return
    result = goldens.check(design)
    if not result.ok and result.rendering is not None:
        out = goldens.default_actuals_dir()
        if out is not None:
            goldens.save_actuals(result, out)
    assert result.ok, result.message
