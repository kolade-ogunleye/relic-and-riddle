"""QF-108 evidence renders: the cut into a spliced photo beat, on real run photos at their real biases.

    python docs/reviews/quality-fixes-2026-11/QF-108/render.py <repo root> <out dir> <before|after>

Runs measure.py's rig (the tree's real compose_video and splice_single_beat, production encoder, lens off) for the
worst cases: the warmest real run (Richat, +0.05) in 9:16 and 16:9, and the cool bound (Aswan, -0.05). Each sheet
is half-size panels: the last frame before the cut (t 0.95, the master's untouched beat), the first frame after it
(t 1.05, the spliced beat), and the master's own middle beat at t 1.05 (what the splice replaces). JPEG <= 300 KB.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
LIMIT = 300_000
SHEETS = ("richat", "richat_16x9", "aswan_cool_bound")


def main(root: Path, out: Path, tag: str) -> None:
    spec = importlib.util.spec_from_file_location("qf108_measure", HERE / "measure.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    sys.path.insert(0, str(root))
    manifest = json.loads((m.QF86 / "inputs.json").read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="qf108-render-") as directory:
        work = Path(directory)
        rig = m.Rig(work)
        for key, name, run, profile, kind in m.CASES:
            if key not in SHEETS:
                continue
            bias = manifest["runs"][run]["grade_bias"] if run else -0.05
            p = rig.profile(profile)
            item = m.prepare(work, key, name, kind, (p.width, p.height))
            master, patched, (w, h) = rig.run(key, item, profile, bias)
            panels = [("before the cut (master)", rig.frame(patched, w, h, 0.95)),
                      ("after the cut (spliced)", rig.frame(patched, w, h, 1.05)),
                      ("master's own beat", rig.frame(master, w, h, 1.05))]
            hw, hh = w // 2, h // 2
            label = 34
            sheet = Image.new("RGB", (hw * 3 + 20, hh + label), (20, 20, 20))
            draw = ImageDraw.Draw(sheet)
            for k, (text, frame) in enumerate(panels):
                x = k * (hw + 10)
                sheet.paste(Image.fromarray(frame).resize((hw, hh), Image.Resampling.LANCZOS), (x, label))
                draw.text((x + 8, 8), f"{tag} {key} bias {bias:+.3f}: {text}", fill=(235, 235, 235))
            path = out / f"{tag}_{key}.jpg"
            for quality in range(88, 50, -4):
                sheet.save(path, quality=quality, optimize=True)
                if path.stat().st_size <= LIMIT:
                    break
            print(path.name, path.stat().st_size)


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]), sys.argv[3])
