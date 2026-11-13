"""QF-87 hook-frame evidence (not run by measure.py; the board is committed as a JPEG).

    python render_hook.py <code root> <dev checkout> <out dir>

Draws the hook card's frame 0 (the Reel's first frame) on three photos of the Richat Structure dev run
(<dev checkout>/assets/richat_structure_sahara_eye/, read only), with the Richat claim and eyebrow the design-card
tests sign ("About 40 km across", "The Richat ring"; tests/test_design_cards_2026_11_03.py), on the tree at
<code root>: the base draws the 64 px masthead (5.2 px at the 124 px tile), QF-87 draws none. Writes
hook_<photo>.png and grid_hook_<photo>.png (the 124 px tile of the centred 3:4 crop, 4x, no smoothing), and
hook.json (the masthead record and the grid proof's hook failures per photo).
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

PHOTOS = ("01_commons.jpg", "02_commons.jpg", "05_commons.jpg")


def main(code: Path, dev: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="qf87-hook-"))
    try:
        app = tmp / "app"
        shutil.copytree(code / "fonts", app / "fonts")
        (app / "data").mkdir()
        shutil.copyfile(code / "data" / "reference_facts.json", app / "data" / "reference_facts.json")
        os.environ.update(RELIC_APP_ROOT=str(app), FONT_SIZE="80")
        sys.path[:0] = [str(code), str(code / "tests")]
        import test_design_cards_2026_11_03 as t
        from PIL import Image

        from src import config, cover_design
        config.FACT_RECORD_KEY = "test-only-fact-record-key-0123456789abcdef"   # the tests' key, this process only
        dc = t.dc
        record = {}
        for name in PHOTOS:
            photo = tmp / name.replace(".jpg", ".png")
            Image.open(dev / "assets" / "richat_structure_sahara_eye" / name).convert("RGB").save(photo)
            plan = dc.plan_hook_frame(t.hook(photo, eyebrow="The Richat ring"), t.SHEET)
            frame = dc.frame_at(plan, 0).convert("RGB")
            stem = name.split(".")[0]
            frame.save(out / f"hook_{stem}.png")
            tile = frame.crop(cover_design.CROP_34).resize((124, round(124 * 4 / 3)), Image.Resampling.LANCZOS)
            tile.resize((tile.width * 4, tile.height * 4), Image.Resampling.NEAREST).save(out / f"grid_hook_{stem}.png")
            proof = plan.meta["grid_proof"]
            record[stem] = {"masthead": plan.meta.get("masthead"),
                            "brand_cap_124": proof["elements"].get("brand", {}).get("cap_124"),
                            "hook_fails": proof["hook_fails"], "position": plan.meta["anchors"]["position"]}
        (out / "hook.json").write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8", newline="\n")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("usage: render_hook.py <code root> <dev checkout> <out dir>")
    main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve())
