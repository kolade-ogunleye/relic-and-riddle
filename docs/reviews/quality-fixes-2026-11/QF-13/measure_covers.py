"""QF-13: hue of the delivered cover against the lossless hero, on real runs and on the colour-v5 test's fixture.

    python docs/reviews/quality-fixes-2026-11/QF-13/measure_covers.py <repo root> [<data root>] [--counts-only]

Prints JSON and writes only to a temporary folder. Covers keep the parent's encoding in this row. What changes is
the hero the cover is drawn from: on the parent it is a JPEG generation, on this row the lossless layer.

- Fixture: the hero of test_colour_v5_still_grade_2026_10_29's cover test, `_wall(25, 480) * [1, 1.02, 0.45]`,
  saved at q95, colour-balanced by the measured commit, then drawn as a minimal cover by VisualEngine. Reported
  against the cover-crop of the hero the cover was drawn from (the test's own reference) and against the lossless
  balanced hero (colour_balance.apply's pixels).
- Real runs: every run in <data root>/output with a thumbnail_cover.jpg and provenance.json. The hero is the run's
  recorded cover recipe hero where still_layout.json keeps one. Otherwise it is the run's still whose 64 x 114
  grey cover-crop correlates best with the delivered cover. That still goes through the measured commit's
  preparation chain (measure.py, part B) twice: as committed, and as the lossless reference. A cover is drawn from
  the committed hero with the run's own copy (script_metadata.json "thumbnail") by
  VisualEngine.generate_thumbnails, as main.py draws it. Its hue and saturation are then compared with the
  cover-crop of the lossless hero.

Metric (as the test): the median hue difference in degrees over the pixels of the patch [40:200, 40:200] whose
reference saturation is over 0.12, and the mean saturation ratio there. The full frame's median hue difference
over the same kind of pixels is also given (the type sits on both commits' covers alike).
"""

import json
import runpy
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
COUNTS_ONLY = "--counts-only" in sys.argv          # the production sample: counts and aggregates only
sys.argv = [a for a in sys.argv if a != "--counts-only"]
root = Path(sys.argv[1]).resolve()
data = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else None
sys.argv = [str(HERE / "measure.py"), str(root)] + ([str(data)] if data else [])
m = runpy.run_path(str(HERE / "measure.py"), run_name="qf13_measure")
from src import colour_balance as cb  # measure.py put root on sys.path
from src.typography import cover_crop
from src.visual_engine import VisualEngine

PATCH = (slice(40, 200), slice(40, 200))


def hue_error(cover, ref):
    out = {}
    for name, region in (("patch", PATCH), ("frame", (slice(None), slice(None)))):
        h_ref, s_ref = cb._hsv(ref[region])
        h_got, s_got = cb._hsv(cover[region])
        k = s_ref > 0.12
        if not k.any():
            out[name] = None
            continue
        d = abs(float(np.median(h_got[k]) - np.median(h_ref[k])))
        out[name] = {"hue_deg": round(min(d, 360 - d), 2),
                     "saturation_ratio": round(float(s_got[k].mean() / max(1e-6, s_ref[k].mean())), 4)}
    return out


def rgb(path_or_image, size=None):
    im = path_or_image if isinstance(path_or_image, Image.Image) else Image.open(path_or_image)
    return np.asarray(im.convert("RGB"), dtype=np.float64)


def fixture(tmp):
    rng = np.random.default_rng(25)
    grey = rng.uniform(40, 140, (480, 480, 1))
    a = (np.repeat(grey, 3, axis=2) + rng.normal(0, 4, (480, 480, 3))) * np.array([1.0, 1.02, 0.45])
    tmp.mkdir(parents=True, exist_ok=True)
    src = tmp / "25_commons.jpg"
    Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB").save(src, "JPEG", quality=95)
    cb.CACHE_DIR = tmp / "colour_balance"
    hero = cb.balanced(src)
    with Image.open(src) as im:
        lossless = Image.fromarray(np.asarray(cb.apply(im, cb.plan(Image.open(src)))))
    copy = {"thumbnail": {"eyebrow": "CAPPADOCIA, TURKEY", "line1": "THE STONE DOOR", "line2": "ROLLED FROM INSIDE",
                          "tagline": "A city underground"}}
    cover, _ = VisualEngine().generate_thumbnails(hero, copy, tmp / "fixture", style="minimal", variants=False)
    got = rgb(cover)
    size = Image.open(cover).size
    lossless_path = tmp / "lossless_hero.png"
    lossless.save(lossless_path)
    return {"hero_format": Image.open(hero).format,
            "against_drawn_hero": hue_error(got, rgb(cover_crop(hero, size)))["patch"],
            "against_lossless_hero": hue_error(got, rgb(cover_crop(lossless_path, size)))["patch"]}


def _hero(run, stills, data_root):
    layout = run / "still_layout.json"
    if layout.is_file():
        recipe = (json.loads(layout.read_text(encoding="utf-8")).get("thumbnail_cover.jpg") or {}).get("recipe") or {}
        if recipe.get("hero"):
            stem = Path(recipe["hero"].replace("\\", "/")).stem
            for src, faces in stills:
                if stem == src.stem or stem.startswith(src.stem + "_"):
                    return src, faces, "recipe"
    target = np.asarray(Image.open(run / "thumbnail_cover.jpg").convert("L").resize((64, 114)), dtype=np.float64)
    target = (target - target.mean()) / (target.std() + 1e-6)
    best, pick = -2.0, None
    for src, faces in stills:
        try:
            c = np.asarray(cover_crop(src, (64, 114)).convert("L"), dtype=np.float64)
        except OSError:
            continue
        c = (c - c.mean()) / (c.std() + 1e-6)
        score = float((c * target).mean())
        if score > best:
            best, pick = score, (src, faces)
    return (*pick, f"correlation {best:.2f}") if pick else (None, False, "none")


def real(data_root, tmp):
    rows = []
    for run in sorted((data_root / "output").glob("run_*")):
        meta = run / "script_metadata.json"
        if not ((run / "thumbnail_cover.jpg").is_file() and meta.is_file()):
            continue
        stills = m["_run_stills"](run, data_root)
        if not stills:
            continue
        src, faces, how = _hero(run, stills, data_root)
        if src is None:
            continue
        payload = json.loads(meta.read_text(encoding="utf-8"))
        work = tmp / run.name
        hero, _, _ = m["_prepare"](src, work / "committed", False, faces)
        lossless, _, _ = m["_prepare"](src, work / "lossless", True, faces)
        cover, _ = VisualEngine().generate_thumbnails(hero, {"thumbnail": payload.get("thumbnail") or {}},
                                                      work / "cover", variants=False)
        got = rgb(cover)
        err = hue_error(got, rgb(cover_crop(lossless, Image.open(cover).size)))
        rows.append({"run": run.name, "hero": src.name, "hero_found_by": how, "hero_format": Image.open(hero).format,
                     "cover_bytes": cover.stat().st_size, **{f"{k}_{n}": v[n] if v else None
                                                           for k, v in err.items() for n in ("hue_deg",
                                                                                             "saturation_ratio")}})
    return rows


def main():
    with tempfile.TemporaryDirectory(prefix="qf13-covers-") as t:
        tmp = Path(t)
        out = {"fixture": fixture(tmp / "fx")}
        if data:
            rows = real(data, tmp / "real")
            vals = [r["patch_hue_deg"] for r in rows if r["patch_hue_deg"] is not None]
            frame = [r["frame_hue_deg"] for r in rows if r["frame_hue_deg"] is not None]
            out["real"] = {"covers": len(rows), "patch_hue_deg_median": round(float(np.median(vals)), 2) if vals else None,
                           "patch_hue_deg_max": max(vals) if vals else None,
                           "frame_hue_deg_median": round(float(np.median(frame)), 2) if frame else None,
                           "frame_hue_deg_max": max(frame) if frame else None,
                           "hero_from_recipe": sum(r["hero_found_by"] == "recipe" for r in rows),
                           "covers_over_2_mib": sum(r["cover_bytes"] > 2 * 1024 * 1024 for r in rows)}
            if not COUNTS_ONLY:
                out["real"]["rows"] = rows
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
