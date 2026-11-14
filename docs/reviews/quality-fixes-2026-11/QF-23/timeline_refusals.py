"""Which of the test suite's timeline sets a tree refuses, and which it draws with a cut label (QF-23).

    python timeline_refusals.py <repository root>

Every committed timeline set the tests render (tests/test_r4_graphics_2026_11_02.py: the fuzz families, the
reviewer's fuzz sets, the named, XL and xv sets; tests/test_timeline_hierarchy_2026_11_03.py: the real and own sets),
at 9:16 and 16:9, on the tree given: "refused" (the checked-copy refusal), "cut" (drawn with a label ending in an
ellipsis) or "whole". On the base no set is refused; on QF-23 each set the base drew cut is refused and every
other set draws exactly as before. tests/test_r4_graphics_2026_11_02.py's QF23_REFUSED is this tree's list. Prints
JSON; writes nothing outside a temporary folder it removes (offline, no .env read).
"""
import json
import os
import shutil
import socket
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

WORKERS = 4
_READY: list = []


def _offline(*_a, **_k):
    raise RuntimeError("timeline_refusals.py is offline")


def _setup(root: str, app: str):
    if not _READY:
        os.environ.update(RELIC_APP_ROOT=app, FONT_SIZE="80")
        socket.create_connection = _offline
        socket.socket.connect = _offline
        sys.path[:0] = [root, str(Path(root) / "tests")]
        _READY.append(True)
    from src import graphics
    return graphics


def _sets(root: str, app: str) -> dict:
    _setup(root, app)
    import test_r4_graphics_2026_11_02 as r4
    import test_timeline_hierarchy_2026_11_03 as th
    out = {f"{fam}/{k}": v for fam, sets in r4.FUZZ_FAMILIES.items() for k, v in sets.items()}
    out.update({f"xv/{k}": v for k, v in r4.XV_TL.items()})
    out.update({f"fz/{k}": v for k, v in r4.FUZZ_TL.items()})
    out.update({f"tl/{k}": v for k, v in r4.TL_CASES.items()})
    out.update({f"named/{k}": v for k, v in r4.NAMED_TL.items()})
    out.update({f"real/{k}": v for k, v in th.REAL.items()})
    out.update({"own/tight_only": th.TIGHT_ONLY, "own/sparse3": th.SPARSE3, "own/over_budget": th.OVER_BUDGET})
    return out


def _one(job) -> tuple:
    root, app, key, events, w, h = job
    graphics = _setup(root, app)
    final: list = []

    def encode(frames, out, n, *a, **k):
        for _ in frames:
            pass
        return Path(out)

    def record(out, frame, plates):
        final.extend(p.text for _n, p in plates if p is not None and not p.empty)
        return {}
    graphics.VIDEO_FPS = 1
    graphics._encode = encode
    graphics.graded_frame = lambda *a, **k: None
    graphics.record_contrast = record
    graphics.record_nontext = lambda *a, **k: {}
    work = Path(tempfile.mkdtemp(dir=app))
    tag = f"{key}-{'9x16' if h > w else '16x9'}"
    try:
        graphics.render_timeline(events, work / "t.mp4", w, h)
        outcome = "cut" if any("…" in t for t in final) else "whole"
    except ValueError as e:
        outcome = "refused" if "Checked copy does not fit" in str(e) else f"error: {e}"
    shutil.rmtree(work, ignore_errors=True)
    return tag, outcome


def main(root: Path) -> None:
    root = root.resolve()
    with tempfile.TemporaryDirectory(prefix="qf23-timelines-") as tmp:
        shutil.copytree(root / "fonts", Path(tmp) / "fonts")
        (Path(tmp) / "data").mkdir()
        shutil.copyfile(root / "data" / "reference_facts.json", Path(tmp) / "data" / "reference_facts.json")
        with ProcessPoolExecutor(WORKERS) as pool:
            sets = pool.submit(_sets, str(root), tmp).result()
            jobs = [(str(root), tmp, k, v, w, h) for k, v in sorted(sets.items())
                    for w, h in ((1080, 1920), (1920, 1080))]
            got = dict(pool.map(_one, jobs))
    result = {"sets": len(sets), "renders": len(got),
              "refused": sorted(k for k, v in got.items() if v == "refused"),
              "cut": sorted(k for k, v in got.items() if v == "cut"),
              "errors": {k: v for k, v in got.items() if v.startswith("error")},
              "whole": sum(v == "whole" for v in got.values())}
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: timeline_refusals.py <repository root>")
    main(Path(sys.argv[1]))
