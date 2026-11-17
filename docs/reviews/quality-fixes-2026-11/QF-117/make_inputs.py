"""QF-117: how inputs/ was made (run once; measure.py reads only the committed inputs/).

    python docs/reviews/quality-fixes-2026-11/QF-117/make_inputs.py <repo root> <dev checkout>

Copies, from three real development runs (output/run_*, gitignored, read only), what the renderers take: the map's
place (the graphic's lineage record), the stat cards, scale fact, timeline, cover copy, narration, emphasis words,
the run's palette and its words.json; and the run's hero photo, cover-cropped to 1080x1920 and saved as a JPEG of
at most 300 KB (the evidence limit). Both trees (before and after) render the same committed inputs.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIMIT = 300 * 1024
RUNS = {
    "memnon": ("run_singing_colossi_of_memnon_1792952878", "assets/singing_colossi_of_memnon/01_commons.jpg"),
    "gol_gumbaz": ("run_gol_gumbaz_whispering_gallery_1793278143", "cache/upscaled/22_commons_466e53f5c2d25a35.jpg"),
    "wieliczka": ("run_wieliczka_salt_mine_1792974672", "assets/wieliczka_salt_mine/01_commons.jpg"),
}
KEYS = ("stat_cards", "scale_fact", "timeline", "thumbnail", "narration_script", "emphasis_words")


def main(root: Path, dev: Path) -> None:
    sys.path.insert(0, str(root))
    from src.typography import cover_crop
    out = HERE / "inputs"
    out.mkdir(exist_ok=True)
    runs = {}
    for key, (run, hero) in RUNS.items():
        run_dir = dev / "output" / run
        script = json.loads((run_dir / "verified_script.json").read_text(encoding="utf-8"))
        lineage = json.loads((run_dir / "graphics" / "graphic_map.mp4.lineage.json").read_text(encoding="utf-8"))
        palette = json.loads((run_dir / "palette.json").read_text(encoding="utf-8"))
        words = json.loads((run_dir / "words.json").read_text(encoding="utf-8"))
        photo = out / f"hero_{key}.jpg"
        im = cover_crop(dev / hero, (1080, 1920)).convert("RGB")
        for q in (90, 85, 80, 75, 70):
            im.save(photo, quality=q, optimize=True)
            if photo.stat().st_size <= LIMIT:
                break
        else:
            raise RuntimeError(f"{photo.name} over 300 KB")
        runs[key] = {**{k: script.get(k) for k in KEYS}, "place": lineage["settings"]["inputs"]["place"],
                     "palette": {k: palette[k] for k in ("accent", "accent_hex", "grade_bias")},
                     "hero": photo.name, "words": words}
    (out / "inputs.json").write_text(json.dumps({"runs": runs}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8",
                                     newline="\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
