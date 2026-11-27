"""Design-freeze board tooling, cycle 5, round 3: the incremental board is fenced off (lander ruling 2026-11-26, after
queue/REVIEW2-board-c5.md found five new stale reuses, R1-R5).

- `board.py --reuse` is refused without `--unsafe-reuse-for-iteration`, with the message "full render only";
- run_palette.sh's BOARD_PREV gives shard timings only; the reuse base needs BOARD_UNSAFE_REUSE_FOR_ITERATION=1;
- with the flag the board is stamped (board.json not_for_panel, REUSE.json unsafe, NOT-EVIDENCE.txt), every copied
  tile, copied surface file and sheet carries REUSED, NOT EVIDENCE, and the gate (board_reuse.verify), the reuse
  plan and the changed-surfaces index refuse or mark it;
- a full render draws exactly as before (no strip, no stamp);
- the board process and its compose workers are offline; a Facebook slide the post never carries is tier 4.
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "docs" / "reviews" / "freeze-system-2026-11-15"
QF35A = ROOT / "docs" / "reviews" / "quality-fixes-2026-11" / "QF-35a"


@pytest.fixture(scope="module")
def mods():
    for p in (str(BOARD), str(QF35A)):
        if p not in sys.path:
            sys.path.insert(0, p)
    return {n: importlib.import_module(n) for n in ("board_reuse", "board", "board_evidence", "board_diff")}


def _bash() -> str | None:
    git_bash = Path("C:/Program Files/Git/usr/bin/bash.exe")
    if sys.platform == "win32":
        return str(git_bash) if git_bash.is_file() else None
    return shutil.which("bash")


# ------------------------------------------------------------------------------------------------ the fence
def test_reuse_is_refused_without_the_unsafe_flag(tmp_path):
    prev = tmp_path / "prev"
    prev.mkdir()
    r = subprocess.run([sys.executable, str(BOARD / "board.py"), str(ROOT), str(tmp_path / "dev"), str(tmp_path / "out"),
                        "--reuse", str(prev), "--rows", "10"], capture_output=True, text=True, timeout=120, check=False,
                       cwd=str(BOARD))
    assert r.returncode == 2, r.stderr[-2000:]
    assert "--reuse refused: full render only; see queue/REVIEW2-board-c5.md" in r.stderr
    assert "--unsafe-reuse-for-iteration" in r.stderr
    assert not (tmp_path / "out" / "board.json").exists()


def _fake_python(tmp_path: Path) -> Path:
    fake = tmp_path / "fakepy"
    fake.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$ARGS_OUT"\n', encoding="utf-8", newline="\n")
    fake.chmod(0o755)
    return fake


@pytest.mark.parametrize("unsafe", [False, True])
def test_board_prev_gives_timings_and_reuse_only_with_the_unsafe_variable(tmp_path, unsafe):
    bash = _bash()
    if bash is None:
        pytest.skip("no bash")
    tree = tmp_path / "tree"
    (tree / "docs/reviews/freeze-system-2026-11-15").mkdir(parents=True)
    prev = tmp_path / "prev" / "default"
    prev.mkdir(parents=True)
    for n in ("surfaces.json", "DEPS.json", "MANIFEST.json"):
        (prev / n).write_text("{}", encoding="utf-8")
    env = {**os.environ, "BOARD_PY": _fake_python(tmp_path).as_posix(), "BOARD_DEV": str(tmp_path / "dev"),
           "BOARD_PREV": (tmp_path / "prev").as_posix(), "ARGS_OUT": (tmp_path / "args.txt").as_posix()}
    env.pop("BOARD_UNSAFE_REUSE_FOR_ITERATION", None)
    if unsafe:
        env["BOARD_UNSAFE_REUSE_FOR_ITERATION"] = "1"
    r = subprocess.run([bash, (BOARD / "run_palette.sh").as_posix(), "default", tree.as_posix(),
                        (tmp_path / "out").as_posix(), "2"], env=env, capture_output=True, text=True, timeout=120, check=False)
    assert r.returncode == 0, r.stderr
    args = (tmp_path / "args.txt").read_text(encoding="utf-8").splitlines()
    assert args[0] == "board.py" and "--timings" in args
    assert args[args.index("--timings") + 1].endswith("prev/default/surfaces.json")
    if unsafe:
        assert args[args.index("--reuse") + 1].endswith("prev/default") and "--unsafe-reuse-for-iteration" in args
    else:
        assert "--reuse" not in args and "--unsafe-reuse-for-iteration" not in args


def _unsafe_board(mods, root: Path) -> Path:
    """A small board folder as board.py leaves it with --reuse --unsafe-reuse-for-iteration (one copied surface)."""
    from PIL import Image
    br = mods["board_reuse"]
    out = root / "unsafe"
    (out / "surfaces").mkdir(parents=True)
    Image.new("RGB", (200, 300), (20, 90, 160)).save(out / "surfaces" / "map__r_1__916.png")
    Image.new("RGB", (200, 300), (160, 90, 20)).save(out / "surfaces" / "timeline__r_1__916.png")
    why = f"{br.NOT_EVIDENCE}: made with --reuse prev {br.UNSAFE_FLAG}"
    (out / "board.json").write_text(json.dumps({"not_for_panel": why, "rows": {}}), encoding="utf-8")
    (out / br.REUSE_NAME).write_text(json.dumps({"unsafe": why, "files": {
        "surfaces/map__r_1__916.png": {"status": "reused", "job": "map"}}}), encoding="utf-8")
    br.stamp_not_evidence(out, ["map__r_1__916"])
    return out


def test_with_the_flag_every_copied_tile_surface_and_sheet_is_marked(mods, tmp_path):
    from PIL import Image
    b, br = mods["board"], mods["board_reuse"]
    out = _unsafe_board(mods, tmp_path)
    tiles = [(n, Image.open(out / "surfaces" / f"{n}.png").convert("RGB")) for n in ("map__r_1__916", "timeline__r_1__916")]
    clean = out / "row10_clean"
    b.sheet(tiles, clean, tile_h=300, cols=2)                  # a full render: no strip on any tile
    b.REUSED_STEMS.clear()
    b.REUSED_STEMS.add("map__r_1__916")
    try:
        b.sheet(tiles, out / "row10_figures", tile_h=300, cols=2)
        marks = b.mark_not_evidence(out)
    finally:
        b.REUSED_STEMS.clear()
    with Image.open(clean.with_suffix(".png")) as c, Image.open(out / "row10_figures.png") as m:
        assert m.height > c.height                              # the copied tile and the sheet carry a strip
        red = b.NOT_EVIDENCE_RED
        assert m.getpixel((2, 2)) == red                         # the sheet's own strip, above everything
        assert m.info[br.NOT_EVIDENCE_KEY] == br.NOT_EVIDENCE and m.info.get(b.TIER_KEY)
    assert sorted(marks["sheets"]) == ["row10_clean.png", "row10_figures.png"]
    assert marks["surfaces"] == ["map__r_1__916.png"]          # the copied surface file, not the rendered one
    with Image.open(out / "surfaces" / "map__r_1__916.png") as s:
        assert s.getpixel((2, 2)) == b.NOT_EVIDENCE_RED and s.info[br.NOT_EVIDENCE_KEY]
    with Image.open(out / "surfaces" / "timeline__r_1__916.png") as s:
        assert s.size == (200, 300) and br.NOT_EVIDENCE_KEY not in s.info
    assert b.mark_not_evidence(out) == {"sheets": [], "surfaces": []}   # once
    assert (out / "row10_figures_half.jpg").is_file()
    text = (out / br.NOT_EVIDENCE_FILE).read_text(encoding="utf-8")
    assert text.startswith(br.NOT_EVIDENCE) and "reused: map__r_1__916" in text and "\r" not in text


def test_a_stamped_board_is_refused_by_the_gate_the_plan_and_any_consumer(mods, tmp_path):
    br = mods["board_reuse"]
    out = _unsafe_board(mods, tmp_path)
    assert br.not_evidence(out).startswith(br.NOT_EVIDENCE)
    for drop in (br.NOT_EVIDENCE_FILE, "board.json"):   # every stamp counts on its own
        (out / drop).unlink()
        assert br.not_evidence(out), drop
    out = _unsafe_board(mods, tmp_path / "again")
    with pytest.raises(SystemExit) as e:
        br.refuse_not_evidence(out, "panel_score.py")
    assert "panel_score.py refuses" in str(e.value) and br.NOT_EVIDENCE in str(e.value)
    proven, why = br.verify(out, ROOT, "HEAD")                 # the gate: nothing of it is proven
    assert proven == set() and why and why[0].startswith("refused: " + br.NOT_EVIDENCE)
    with pytest.raises(br.NoReuse, match="not evidence"):       # never a reuse base: its marks would travel on
        br.plan(ROOT, out, "default", {}, {}, [])
    full = tmp_path / "full"
    full.mkdir()
    (full / "board.json").write_text(json.dumps({"rows": {}}), encoding="utf-8")
    assert br.not_evidence(full) is None
    br.refuse_not_evidence(full)                                # a full render passes


def test_the_changed_surfaces_index_marks_an_unsafe_board(mods, tmp_path):
    bd, br = mods["board_diff"], mods["board_reuse"]
    out = _unsafe_board(mods, tmp_path)
    before = tmp_path / "before"
    (before / "surfaces").mkdir(parents=True)
    shutil.copy2(out / "surfaces" / "timeline__r_1__916.png", before / "surfaces" / "timeline__r_1__916.png")
    r = bd.report(before, out, tmp_path / "diff", ("BEFORE", "AFTER"))
    assert set(r["not_evidence"]) == {"after"}
    md = (tmp_path / "diff" / "index.md").read_text(encoding="utf-8")
    assert f"**{br.NOT_EVIDENCE}: the after board is an incremental board**" in md
    assert json.loads((tmp_path / "diff" / "index.json").read_text(encoding="utf-8"))["not_evidence"]["after"]
    r2 = bd.report(before, before, tmp_path / "diff2", ("A", "B"))
    assert r2["not_evidence"] == {} and br.NOT_EVIDENCE not in (tmp_path / "diff2" / "index.md").read_text(encoding="utf-8")


def test_the_board_wires_the_fence(mods):
    src = (BOARD / "board.py").read_text(encoding="utf-8")
    main = src[src.index("def main("):]
    head = main[:main.index("tree, dev, out = a.tree.resolve()")]
    assert "if a.reuse and not a.unsafe_reuse_for_iteration:" in head and "raise SystemExit(2)" in head
    assert 'manifest["not_for_panel"]' in main and 'plan["unsafe"] = manifest["not_for_panel"]' in main
    assert 'rec["unsafe"] = manifest["not_for_panel"]' in main          # a NoReuse fallback is stamped too
    assert main.index("tier_sheets(out)") < main.index("mark_not_evidence(out)")   # after every sheet is drawn
    assert 'if manifest.get("not_for_panel"):' in main                   # a full render marks nothing


# ------------------------------------------------------------------------------------------------ offline
def test_the_board_process_and_its_compose_workers_are_offline(mods):
    code = ("import sys, socket; sys.path.insert(0, sys.argv[1]); import board; board.block_network()\n"
            "for f in (lambda: socket.create_connection(('127.0.0.1', 9), timeout=1),\n"
            "          lambda: socket.socket().connect(('127.0.0.1', 9)),\n"
            "          lambda: socket.socket().connect_ex(('127.0.0.1', 9))):\n"
            "    try:\n        f()\n    except RuntimeError as e:\n        assert 'offline' in str(e)\n"
            "    else:\n        raise SystemExit('connected')\nprint('offline')\n")
    r = subprocess.run([sys.executable, "-c", code, str(BOARD)], capture_output=True, text=True, timeout=120, check=False,
                       cwd=str(BOARD))
    assert r.returncode == 0 and r.stdout.strip() == "offline", r.stderr[-2000:]
    src = (BOARD / "board.py").read_text(encoding="utf-8")
    init = src[src.index("def _worker_init("):src.index("# The board's palettes")]
    assert "block_network()" in init
    main = src[src.index("def main("):]
    assert main.index("block_network()") < main.index("ProcessPoolExecutor(")


# ------------------------------------------------------------------------------------------------ tiers
def test_a_facebook_slide_the_post_never_carries_is_tier_4(mods, tmp_path):
    b, be = mods["board"], mods["board_evidence"]
    deck = "social__gol_1793278143"
    names = [f"{deck}__fb_carousel_slide_{i:02d}" for i in range(1, 8)]
    posts = {1: 1, 2: 2, 3: 3, 4: 4, 5: None, 6: None, 7: 5}
    rec = {"photo_choice": {n: {"facebook_post": posts[i + 1]} for i, n in enumerate(names)}}
    assert b.fb_unposted(names, rec, tmp_path) == {deck: [names[4], names[5]]}
    unknown = {"photo_choice": {n: {"facebook_post": "unknown"} for n in names}}
    assert b.fb_unposted(names, unknown, tmp_path) == {}          # no pick: the slides stay tier 1 (recorded)
    be.FB_UNPOSTED.update([names[4], names[5]])
    try:
        assert be.tier(names[4]) == "T4" and be.tier(names[5]) == "T4"
        assert be.tier(names[0]) == "T1" and be.tier(names[6]) == "T1"
        assert be.tile_tier(f"{names[4]} | slide 5", "row05_social")[1].startswith("TIER 4: internal, never seen")
        assert be.tier("map__amber_1__916") == "T1" and be.tier("numbers__amber_1__916") == "T1"
    finally:
        be.FB_UNPOSTED.clear()
    src = (BOARD / "board.py").read_text(encoding="utf-8")
    assert "be.FB_UNPOSTED.update(" in src and 'manifest["fb_unposted"] = unposted' in src
