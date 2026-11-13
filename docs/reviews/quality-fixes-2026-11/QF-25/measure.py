"""QF-25 acceptance: checked design cards in the composed master; captions clear until caption_clear_until_s.

Usage: python measure.py <repo-root>

On a tree without src/card_composition.py (the parent) it asks the normal graphics entry point for the committed
fixture's cards and reports how many reach a master (none). Otherwise it renders actual native masters through the
repository's own tests (committed fixtures, generated photos and audio, offline checker boundaries, -n 4) and
measures each decoded master: the hook (9:16 opening), the chapter card (16:9), the myth card (16:9 and 9:16).
The quote card stays held (QUOTE_CARDS_ENABLED is false, FREEZE-RESUME ruling Q10): it is counted as refused, not
composed. Prints JSON. RR_MEASURE_OUT=<file> also writes it; RR_MEASURE_PREVIEWS=<dir> writes half-size JPEGs.
No provider call, no environment file, no operator data.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

CASES = {
    "chapter": "test_checked_card_survives_real_planning_and_native_composition[chapter]",
    "mythfact": "test_checked_card_survives_real_planning_and_native_composition[mythfact]",
    "mythfact_portrait": "test_portrait_myth_keeps_correction_source_and_ordinary_captions_clear",
    "hook": "test_verified_hook_reaches_the_native_master_with_all_voice_and_caption_words",
}


def _frame(path: Path, at: float, width: int, height: int):
    import numpy as np
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", f"{at:.3f}", "-i", str(path),
                                   "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
    return np.frombuffer(raw, np.uint8).reshape(height, width, 3)


def _caption_starts(ass: Path) -> list[float]:
    starts = []
    for line in ass.read_text(encoding="utf-8").splitlines():
        if line.startswith("Dialogue:"):
            h, m, s = line.split(",", 2)[1].split(":")
            starts.append(int(h) * 3600 + int(m) * 60 + float(s))
    return starts


def _parent(root: Path) -> dict:
    import pytest
    from test_design_cards_2026_11_03 import SHEET, myth, quote, statement

    from src import config, graphics
    changes = pytest.MonkeyPatch()
    changes.setattr(config, "FACT_RECORD_KEY", "test-only-fact-record-key-0123456789abcdef")
    changes.setattr(graphics.geo, "locate", lambda *_: None)
    work = Path(tempfile.mkdtemp(prefix="qf25_measure_"))
    sheet = copy.deepcopy(SHEET)
    statement("About 40 km across", "f_len", sheet)
    statement("The ring formed through erosion", "f_cons", sheet)
    myth(sheet=sheet)
    quote(sheet=sheet)
    facts = work / "facts.json"
    facts.write_text(json.dumps(sheet), encoding="utf-8")
    payload = {"design_card_sheet": str(facts),
               "chapters": [{"act_number": 1, "title": "The ring formed through erosion", "start_sec": 3.0}]}
    made = graphics.make_all(payload, {}, work / "graphics", width=1920, height=1080)
    cards = [p for p in made if any(k in p.name for k in ("chapter", "mythfact", "quote", "hook"))]
    changes.undo()
    return {"card_composition_module": False, "cards_made_by_graphics": len(cards), "composed_designs": 0,
            "composed": [], "hook_first_caption_s": None, "captions_clear_until_caption_clear_until_s": None}


def measure(root: Path) -> dict:
    sys.path[:0] = [str(root), str(root / "tests")]
    os.chdir(root)
    if not (root / "src" / "card_composition.py").is_file():
        return _parent(root)
    work = Path(tempfile.mkdtemp(prefix="qf25_measure_"))
    target = root / "tests" / "test_qf25_card_composition.py"
    process = subprocess.run([sys.executable, "-m", "pytest", *(f"{target}::{node}" for node in CASES.values()),
                              "-n", "4", "--dist", "worksteal", "-q", "-p", "no:cacheprovider",
                              "--basetemp", str(work / "native")],
                             capture_output=True, text=True, encoding="utf-8", check=False)
    if process.returncode:
        raise RuntimeError("native composition tests failed:\n" + process.stdout[-4000:] + process.stderr[-2000:])
    import numpy as np
    from PIL import Image

    from src import config, design_cards, graphics
    previews = os.environ.get("RR_MEASURE_PREVIEWS")
    rows = []
    for name in CASES:
        manifest = None
        for folder in sorted(p.parent for p in (work / "native").rglob("master.composition.json")):
            m = json.loads((folder / "master.composition.json").read_text(encoding="utf-8"))
            kind = next(iter(m["design_cards"].values()))["contract"]["kind"]
            portrait = m["height"] > m["width"]
            if kind == name.split("_")[0] and portrait == (name in ("hook", "mythfact_portrait")):
                manifest, case = m, folder
                break
        if manifest is None:
            raise RuntimeError(f"no composed master found for {name}")
        contract = next(iter(manifest["design_cards"].values()))["contract"]
        width, height = manifest["width"], manifest["height"]
        master = case / "master.mp4"
        starts = _caption_starts(case / "master_captions.ass")
        row = {"case": name, "kind": contract["kind"], "width": width, "height": height,
               "card_read_s": contract["seconds"], "opening_offset_s": manifest["opening_offset_s"],
               "first_caption_s": round(min(starts), 3)}
        asset = Path(next(iter(manifest["design_cards"])))
        if contract["kind"] == "hook":
            clear = contract["caption_clear_until_s"]
            row.update(caption_clear_until_s=clear, captions_before_clear=sum(t < clear - 1e-6 for t in starts))
            at = .6
        else:
            interval = manifest["design_card_intervals"][0]
            row.update(card_start_s=round(interval["start"], 3), card_end_s=round(interval["end"], 3))
            at = interval["start"] + contract["seconds"] - .2
            decoded = _frame(asset, contract["seconds"] - .2, width, height)
            reference = graphics.graded_frame(Image.fromarray(decoded), work / "reference", vignette=False)
            shown = _frame(master, at, width, height)
            band = slice(0, 750) if width > height else slice(0, 1370)   # above the caption band
            row["master_vs_graded_card_mae"] = round(float(np.mean(np.abs(
                np.asarray(reference)[band].astype(float) - shown[band].astype(float)))), 3)
            if contract["kind"] == "mythfact":
                timing = contract["narration_timing"]
                row["correction_sync_error_s"] = round(abs(timing["at_s"] + timing["first_fact_full_s"]
                                                           - timing["correction_s"]), 4)
            row["caption_graphic_overlap"] = len(manifest.get("caption_graphic_overlap") or [])
        if previews:
            out = Path(previews)
            out.mkdir(parents=True, exist_ok=True)
            image = Image.fromarray(_frame(master, at, width, height))
            image.resize((width // 2, height // 2), Image.LANCZOS).save(out / f"{name}.jpg", quality=86)
            if contract["kind"] == "hook":
                after = Image.fromarray(_frame(master, row["first_caption_s"] + .5, width, height))
                after.resize((width // 2, height // 2), Image.LANCZOS).save(out / "hook-then-caption.jpg",
                                                                            quality=86)
        rows.append(row)
    hook = next(r for r in rows if r["kind"] == "hook")
    return {"card_composition_module": True, "cards_made_by_graphics": None,
            "composed_designs": len({r["kind"] for r in rows}), "composed": rows,
            "quote_held": not config.QUOTE_CARDS_ENABLED,
            "hook_first_caption_s": hook["first_caption_s"],
            "captions_clear_until_caption_clear_until_s": hook["captions_before_clear"] == 0,
            "caption_clear_until_s": design_cards.CAPTION_CLEAR}


if __name__ == "__main__":
    data = measure(Path(sys.argv[1]).resolve())
    text = json.dumps(data, indent=2)
    if os.environ.get("RR_MEASURE_OUT"):
        Path(os.environ["RR_MEASURE_OUT"]).write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)
