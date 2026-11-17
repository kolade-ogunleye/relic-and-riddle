"""QF-29a: golden images for frozen designs (scripts/goldens.py).

Proved on the two proof fixtures of tests/golden_proof_designs.py (a YouTube thumbnail still, an end-card clip
from the production encoder), rendered by the real design code from committed inputs, with their goldens taken in a
temporary store (no design is frozen yet):
- an unchanged tree passes on two further renders (no false failure);
- a planted 1-px text shift and a planted six-level colour change are caught (still and video), with a diff board;
- a toolchain change is caught loudly ("toolchain changed: re-validate"), never a silent pass, whether or not the
  pixels moved; x264 on another machine's thread count is such a change;
- inputs are committed fixtures only (gitignored or uncommitted inputs are refused) and a changed input is reported;
- a toolchain whose pixels differ is accepted only from a candidate rendered clean at the golden's design: a design
  change under a new fingerprint (review round 1's probe) is refused, an identical-pixel candidate is accepted from
  any commit; host properties (Python, platform, NumPy SIMD) are information, not the fingerprint.
"""
from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GOLDENS = ROOT / "scripts" / "goldens.py"
PROOF = ROOT / "tests" / "golden_proof_designs.py"
STILL = "proof.youtube-thumbnail.gol-gumbaz.16x9"
VIDEO = "proof.end-card.gol-gumbaz.9x16"
RUN = "tests/fixtures/ui02_review_v2/run_gol_gumbaz_whispering_gallery_1793278143/script_metadata.json"
PHOTO = "docs/reviews/archive-film-frame-2026-10-28/before_fill_crop.jpg"


def _goldens():
    assert GOLDENS.is_file(), "no golden-image mechanism (scripts/goldens.py)"
    assert PROOF.is_file(), "no golden proof fixtures (tests/golden_proof_designs.py)"
    from scripts import goldens
    return goldens


def _with_fingerprint(g, manifest: dict, change) -> dict:
    """The manifest as if its golden had been taken under another toolchain."""
    m = copy.deepcopy(manifest)
    v = m["variants"][0]
    change(v["fingerprint"])
    v["fingerprint_id"] = g.fingerprint_id(v["fingerprint"])
    return m


def _synthetic_commit(tmp: Path, files: dict[str, bytes]) -> str:
    """A commit on HEAD with `files` written, in a private object directory (never into the repository); the
    caller exposes it with GIT_ALTERNATE_OBJECT_DIRECTORIES."""
    common = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True, check=True).stdout.strip()
    objects = tmp / "objects"
    objects.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GIT_OBJECT_DIRECTORY": str(objects),
           "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(Path(common) / "objects"), "GIT_INDEX_FILE": str(tmp / "index"),
           "GIT_AUTHOR_NAME": "probe", "GIT_AUTHOR_EMAIL": "probe@invalid", "GIT_COMMITTER_NAME": "probe",
           "GIT_COMMITTER_EMAIL": "probe@invalid"}

    def git(*args, data=None):
        return subprocess.run(["git", "-C", str(ROOT), *args], input=data, capture_output=True, env=env,
                              check=True).stdout.decode().strip()
    git("read-tree", "HEAD")
    for path, content in files.items():
        blob = git("hash-object", "-w", "--stdin", data=content)
        git("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}")
    return git("commit-tree", git("write-tree"), "-p", "HEAD", "-m", "probe")


def _assert_caught(g, res, store: Path, tmp: Path, plant: str) -> None:
    assert res.status == "design-changed", (plant, res.status, res.message)
    assert "the design's pixels changed" in res.message
    assert any(s["changed_share"] > 0 for s in res.stats.values()), res.stats
    folder = g.save_actuals(res, tmp / f"actuals-{plant}", store)
    assert (folder / "board.png").stat().st_size > 10_000
    rec = json.loads((folder / "variant.json").read_text(encoding="utf-8"))
    assert rec["status"] == "design-changed" and rec["fingerprint_id"] == res.rendering.fingerprint_id


def test_still_golden_catches_shift_colour_and_toolchain(tmp_path):
    g = _goldens()
    store = tmp_path / "store"
    manifest = g.take(STILL, "2026-11-15", PROOF, store, allow_dirty=True)
    assert manifest["kind"] == "still" and manifest["frozen_on"] == "2026-11-15"
    assert re.fullmatch(r"[0-9a-f]{40}", manifest["commit"])
    assert set(manifest["inputs"]) == set(g.load_registry(PROOF)[STILL].inputs) == {RUN, PHOTO}
    fp = manifest["variants"][0]["fingerprint"]
    assert set(fp) == {"pillow_libs", "packages"}   # hard: the rendering libraries only
    assert {"freetype2", "raqm", "fribidi", "harfbuzz"} <= set(fp["pillow_libs"])
    # information only (measured not to move pixels): never part of the fingerprint id
    assert {"python", "platform", "numpy_simd", "cpus"} <= set(manifest["variants"][0]["info"])
    assert g.manifest_problems(store, STILL, manifest, g.load_registry(PROOF)[STILL]) == []

    # Two further renders of the unchanged tree: identical.
    first = g.check(STILL, PROOF, store)
    second = g.check(STILL, PROOF, store)
    assert first.ok and second.ok, (first.message, second.message)

    # A 1-px shift of the text and a six-level colour change: caught exactly, each with a diff board.
    shift = g.check(STILL, PROOF, store, extra_env={"QF29_PLANT": "shift"})
    _assert_caught(g, shift, store, tmp_path, "shift")
    colour = g.check(STILL, PROOF, store, extra_env={"QF29_PLANT": "colour"})
    _assert_caught(g, colour, store, tmp_path, "colour")

    # The tolerance (used only on a new toolchain) also sees both as more than toolchain noise.
    assert not all(s["within_tolerance"] for s in shift.stats.values()), shift.stats
    assert not all(s["within_tolerance"] for s in colour.stats.values()), colour.stats

    # A toolchain change fails loudly even with identical pixels, and names what changed.
    other = _with_fingerprint(g, manifest, lambda f: f["pillow_libs"].update(raqm="0.10.1"))
    drift = g.compare(STILL, other, first.rendering, store)
    assert drift.status == "toolchain-changed" and not drift.ok
    assert "toolchain changed: re-validate" in drift.message and "raqm" in drift.message
    assert "identical to accepted variant" in drift.message
    # ... and with a planted shift as well: beyond the tolerance, not a toolchain-only change.
    drift_shift = g.compare(STILL, other, shift.rendering, store)
    assert drift_shift.status == "toolchain-changed" and "BEYOND the tolerance" in drift_shift.message
    drift_colour = g.compare(STILL, other, colour.rendering, store)
    assert drift_colour.status == "toolchain-changed" and "BEYOND the tolerance" in drift_colour.message

    # Accepting a toolchain whose pixels are identical records the fingerprint without a new image; the
    # same toolchain then passes, and a shift under it is still caught.
    # Identical pixels are accepted from any commit and tree state (here a candidate recorded as dirty).
    (store / STILL / "manifest.json").write_text(json.dumps(other), encoding="utf-8")
    ci = g.save_actuals(drift, tmp_path / "ci", store)
    rec = json.loads((ci / "variant.json").read_text(encoding="utf-8"))
    assert re.fullmatch(r"[0-9a-f]{40}", rec["tree"]["commit"]) and isinstance(rec["tree"]["dirty"], bool)
    rec["tree"].update(dirty=True, changed=["src/brand.py"])
    (ci / "variant.json").write_text(json.dumps(rec), encoding="utf-8")
    accepted = g.accept_toolchain(STILL, PROOF, store, source=ci)
    assert [v["how"] for v in accepted["variants"]] == ["taken", "identical"]
    assert accepted["variants"][1]["candidate"]["commit"] == rec["tree"]["commit"]
    assert g.compare(STILL, accepted, first.rendering, store).ok
    assert g.compare(STILL, accepted, shift.rendering, store).status == "design-changed"


def test_video_golden_samples_the_production_encoder(tmp_path):
    g = _goldens()
    store = tmp_path / "store"
    manifest = g.take(VIDEO, "2026-11-15", PROOF, store, allow_dirty=True)
    v = manifest["variants"][0]
    assert sorted(v["frames"]) == ["f00007", "f00014"]
    assert all(f["pixel_format"] == "yuv420p" and f["size"] == [1080, 1920] for f in v["frames"].values())
    x264 = v["fingerprint"]["x264"]
    # QF-134's production encoder: libx264, preset slow (ref=5, subme=8, rc_lookahead=50), crf 18
    assert x264.startswith("x264 - core") and "crf=18.0" in x264
    assert all(f" {opt} " in x264 for opt in ("ref=5", "subme=8", "rc_lookahead=50"))
    assert {"version", "sha256"} <= set(v["fingerprint"]["ffmpeg"])

    unchanged = g.check(VIDEO, PROOF, store)
    assert unchanged.ok, unchanged.message

    # Another machine's core count changes x264's threads and the encoded bits: a toolchain change, loud,
    # inside the documented tolerance (the design did not move).
    threads = int(re.search(r"\bthreads=(\d+)", x264).group(1))
    other = 6 if threads != 6 else 3
    machine = g.check(VIDEO, PROOF, store, extra_env={"QF29_PLANT": f"threads:{other}"})
    assert machine.status == "toolchain-changed", machine.message
    assert "toolchain changed: re-validate" in machine.message and "x264" in machine.message
    assert f"threads={other}" in machine.variant["fingerprint"]["x264"]

    for plant in ("shift", "colour"):
        res = g.check(VIDEO, PROOF, store, extra_env={"QF29_PLANT": plant})
        _assert_caught(g, res, store, tmp_path, plant)


def test_inputs_are_committed_fixtures_only(tmp_path):
    # A stand-in repository, not the checkout: tests never write a path the release owns (QF-111).
    import subprocess
    g = _goldens()
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / ".gitignore").write_text("output/\n", encoding="utf-8")
    (repo / "tests" / "conftest.py").write_text("", encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
                  "commit", "-q", "-m", "fixtures"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    ignored = repo / "output" / f"qf29-probe-{uuid.uuid4().hex}.png"
    untracked = repo / "tests" / f"qf29-probe-{uuid.uuid4().hex}.png"
    ignored.parent.mkdir()
    ignored.write_bytes(b"x")
    untracked.write_bytes(b"x")
    problems = g.input_problems(repo, [ignored.relative_to(repo).as_posix(), untracked.relative_to(repo).as_posix(),
                                       "tests/does-not-exist.png", "../outside.png", "tests/conftest.py"])
    assert len(problems) == 4, problems
    assert "gitignored" in problems[0] and "not committed" in problems[1] and "missing" in problems[2]
    assert "repository-relative" in problems[3]


def test_changed_input_and_broken_manifest_are_reported(tmp_path):
    g = _goldens()
    store = tmp_path / "store"
    manifest = g.take(STILL, "2026-11-15", PROOF, store, allow_dirty=True)
    design = g.load_registry(PROOF)[STILL]
    fresh = g.render(STILL, PROOF)
    moved = copy.deepcopy(manifest)
    moved["inputs"][design.inputs[1]] = "0" * 64
    res = g.compare(STILL, moved, fresh, store)
    assert res.status == "input-changed" and design.inputs[1] in res.message

    broken = copy.deepcopy(manifest)
    broken["variants"][0]["fingerprint"]["platform"] = "elsewhere"     # id no longer matches
    (store / STILL / manifest["variants"][0]["frames"]["thumbnail"]["reference"]).unlink()
    problems = g.manifest_problems(store, STILL, broken, design)
    assert any("fingerprint_id" in p for p in problems) and any("missing" in p for p in problems)
    (store / STILL / "manifest.json").write_text(json.dumps(broken), encoding="utf-8")
    assert g.check(STILL, PROOF, store).status == "error"


def test_a_design_change_is_never_accepted_as_a_toolchain(tmp_path, monkeypatch):
    """Review round 1's probe: a design edit rendered under a new toolchain fingerprint must not become a variant."""
    g = _goldens()
    store = tmp_path / "store"
    manifest = g.take(STILL, "2026-11-15", PROOF, store, allow_dirty=True)
    golden_commit = manifest["commit"]
    changed = g.check(STILL, PROOF, store, extra_env={"QF29_PLANT": "colour"})   # pixels differ
    assert changed.status == "design-changed"

    def candidate(name: str, *, commit: str | None, dirty: bool | None) -> Path:
        """The changed render as a CI candidate from another runner (new fingerprint), with a given origin."""
        folder = g.save_actuals(changed, tmp_path / name, store)
        rec = json.loads((folder / "variant.json").read_text(encoding="utf-8"))
        rec["fingerprint"]["pillow_libs"]["harfbuzz"] = "0.0.probe"
        rec["fingerprint_id"] = g.fingerprint_id(rec["fingerprint"])
        rec["tree"] = {"commit": commit, "dirty": dirty, "changed": ["src/brand.py"] if dirty else []}
        (folder / "variant.json").write_text(json.dumps(rec), encoding="utf-8")
        return folder

    note = "reviewer loophole probe"
    # a design edit in the working tree (the reviewer's BONE -1 edit): refused
    with pytest.raises(g.GoldenError, match="tree with changes"):
        g.accept_toolchain(STILL, PROOF, store, source=candidate("dirty", commit=golden_commit, dirty=True),
                           revalidated=note)
    # no recorded origin: refused
    with pytest.raises(g.GoldenError, match="does not record the commit"):
        g.accept_toolchain(STILL, PROOF, store, source=candidate("nocommit", commit=None, dirty=False),
                           revalidated=note)
    # a committed design edit (src/brand.py BONE (243, 237, 226) -> (243, 237, 225), the role table's line since
    # QF-35a) after the take: refused
    brand = (ROOT / "src" / "brand.py").read_bytes()
    edited = brand.replace(b'"BONE": (243, 237, 226),', b'"BONE": (243, 237, 225),')
    assert edited != brand
    design_commit = _synthetic_commit(tmp_path / "git-design", {"src/brand.py": edited})
    # review round 2's probe: a commit that only edits a registered design's render in the registry
    # (tests/goldens/designs.py wraps the render with GOLD (242, 182, 48) -> (242, 182, 49)): design code
    registry = (ROOT / "tests" / "goldens" / "designs.py").read_bytes() + (
        b"\n\nfrom tests.golden_proof_designs import DESIGNS as _PROOF  # noqa: E402\n"
        b"_render = _PROOF['" + STILL.encode() + b"'].render\n\n\n"
        b"def _gold_plus_one(inputs, work):\n    from src import brand\n"
        b"    with brand.use(brand.DEFAULT_BRAND.derive('gold + 1', palette={'GOLD': (242, 182, 49)})):\n"
        b"        return _render(inputs, work)\n\n\n"
        b"DESIGNS['" + STILL.encode() + b"'] = Design(**{**_PROOF['" + STILL.encode() + b"'].__dict__, "
        b"'render': _gold_plus_one})\n")
    registry_commit = _synthetic_commit(tmp_path / "git-registry", {"tests/goldens/designs.py": registry})
    # the legitimate case: a commit that only adds golden data (a manifest and its reference images)
    data_commit = _synthetic_commit(tmp_path / "git-data", {
        "tests/goldens/probe-design/manifest.json": b"{}\n", "tests/goldens/probe-design/thumbnail.webp": b"RIFF",
        "tests/goldens/probe-design/thumbnail.v1.webp": b"RIFF"})
    monkeypatch.setenv("GIT_ALTERNATE_OBJECT_DIRECTORIES", os.pathsep.join(
        str(tmp_path / d / "objects") for d in ("git-design", "git-registry", "git-data")))
    for name, commit, path in (("design", design_commit, "src/brand.py"),
                               ("registry", registry_commit, "tests/goldens/designs.py")):
        with pytest.raises(g.GoldenError, match="design change, not a toolchain") as refused:
            g.accept_toolchain(STILL, PROOF, store, source=candidate(name, commit=commit, dirty=False),
                               revalidated=note)
        assert path in str(refused.value)
    assert g.design_tree_changes(ROOT, golden_commit, registry_commit) == ["tests/goldens/designs.py"]
    assert g.design_tree_changes(ROOT, golden_commit, data_commit) == []
    # an unknown commit: refused (cannot be compared)
    with pytest.raises(g.GoldenError, match="cannot compare"):
        g.accept_toolchain(STILL, PROOF, store, source=candidate("unknown", commit="0" * 40, dirty=False),
                           revalidated=note)
    # without --revalidated: refused
    with pytest.raises(g.GoldenError, match="--revalidated"):
        g.accept_toolchain(STILL, PROOF, store, source=candidate("noteless", commit=golden_commit, dirty=False))
    # a folder without variant.json: a clear error, not a traceback
    with pytest.raises(g.GoldenError, match="no variant.json"):
        g.accept_toolchain(STILL, PROOF, store, source=tmp_path, revalidated=note)
    assert len(g.load_manifest(store, STILL)["variants"]) == 1   # nothing was accepted

    # The golden's design on a clean tree (its commit, or one adding only golden data) is accepted, with its
    # commit, tree state and frame stats recorded.
    accepted = g.accept_toolchain(STILL, PROOF, store, source=candidate("clean", commit=data_commit, dirty=False),
                                  revalidated=note)
    v = accepted["variants"][-1]
    assert v["how"] == "revalidated" and v["note"] == note
    assert v["candidate"] == {"commit": data_commit, "dirty": False, "changed": []}
    assert v["stats_vs_taken"]["thumbnail"]["changed_share"] > 0


def test_only_golden_data_is_exempt_from_the_design_tree(monkeypatch):
    """What counts as design code under tests/goldens/ (review round 2): everything but a taken golden's
    manifest.json and reference images. An uncommitted registry edit makes a render dirty and stops a take."""
    g = _goldens()
    exempt = ["tests/goldens/a-design/manifest.json", "tests/goldens/a-design/thumbnail.webp",
              "tests/goldens/a-design/f00007.v1.webp"]
    design = ["tests/goldens/designs.py", "tests/goldens/a-design/render.py", "tests/goldens/a-design/x/manifest.json",
              "tests/goldens/README.md", "tests/goldens/manifest.json", "src/brand.py", "tests/golden_proof_designs.py"]
    assert all(g.is_golden_data(p) for p in exempt)
    assert not any(g.is_golden_data(p) for p in design)

    real = g._git

    def fake(root, *args):
        if args[:1] == ("status",):
            out = " M tests/goldens/designs.py\n?? tests/goldens/a-design/manifest.json\n?? tests/goldens/a-design/t.webp\n"
            return subprocess.CompletedProcess(args, 0, out, "")
        return real(root, *args)
    monkeypatch.setattr(g, "_git", fake)
    state = g.tree_state(ROOT)
    assert state["dirty"] and state["changed"] == ["tests/goldens/designs.py"]
    with pytest.raises(g.GoldenError, match="tests/goldens/designs.py"):
        g._check_clean_tree(ROOT, g.STORE)
