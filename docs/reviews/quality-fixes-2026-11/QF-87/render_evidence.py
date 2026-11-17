"""QF-87 evidence renders from real dev-run data (not run by measure.py; the boards are committed as JPEGs).

    python render_evidence.py <code root> <dev checkout> <out dir> [targets.json]

<code root> is the tree whose code draws (the base, then this commit); <dev checkout> holds the dev runs' photos
(assets/<topic>/, read only). The narrations are the freeze panel's committed fixtures
(docs/reviews/freeze-panel-2026-11-04/fixtures/runs/*/words.json, the same dev runs). Writes PNGs into <out dir>:
- caption_<run>_<t>.png: the 9:16 caption file the pipeline builds for the run, drawn by libass (ffmpeg) over the
  run's first photo at time t (every word of the burst revealed). Without targets.json the targets are chosen on
  this tree and written to <out dir>/targets.json: every burst of the Amber Room run that holds "Königsberg", and
  the first two-line burst of each run; pass that file when drawing the other tree, so both draw the same times.
- cover_<run>_<style>.png and grid_<run>_<style>.png: the run's cover copy (fixtures.json "covers") drawn on the
  run's cover photo, and the 124 px profile-grid tile of it (its centred 3:4 crop), enlarged 4x without smoothing.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

STYLES = ("badge", "minimal")


def _esc(p: Path) -> str:
    return str(p.resolve()).replace("\\", "/").replace(":", "\\:")


def _ground(photo: Path, size=(1080, 1920)):
    from PIL import Image, ImageOps
    return ImageOps.fit(Image.open(photo).convert("RGB"), size, Image.Resampling.LANCZOS)


def main(code: Path, dev: Path, out: Path, targets_file: Path | None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="qf87-render-"))
    try:
        app = tmp / "app"
        shutil.copytree(code / "fonts", app / "fonts")
        (app / "data").mkdir()
        shutil.copyfile(code / "data" / "reference_facts.json", app / "data" / "reference_facts.json")
        os.environ.update(RELIC_APP_ROOT=str(app), FONT_SIZE="80")
        sys.path.insert(0, str(code))
        from PIL import Image

        from src import cover_design
        from src import video_composer as vc
        from src.still_layout import Layout
        fx = code / "docs" / "reviews" / "freeze-panel-2026-11-04" / "fixtures"
        catalogue = json.loads((fx / "fixtures.json").read_text(encoding="utf-8"))
        emphasis = {Path(c["run"]).name: c.get("emphasis_words") or []
                    for c in catalogue["designs"]["captions"]["cases"]}
        runs = {p.parent.name: json.loads(p.read_text(encoding="utf-8"))
                for p in sorted((fx / "runs").glob("run_*/words.json")) if not p.parent.name.endswith("_long")}
        if targets_file is None:
            targets = []
            for run, words in runs.items():
                phrases = vc.caption_phrases(words, emphasis.get(run))
                timings = vc.burst_timings(phrases)
                first_two = True
                for phrase, (show, hide, reveal) in zip(phrases, timings):
                    konig = "amber" in run and any("nigsberg" in w["word"] for w in phrase)
                    two = first_two and bool(vc.reel_caption_lines(phrase))
                    if konig or two:
                        targets.append({"run": run, "t": round(min(hide - 0.05, reveal[-1] + 0.25), 3),
                                        "words": [w["word"] for w in phrase], "two_line": two})
                        first_two = first_two and not two
            (out / "targets.json").write_text(json.dumps(targets, ensure_ascii=False, indent=1) + "\n",
                                              encoding="utf-8", newline="\n")
        else:
            targets = json.loads(targets_file.read_text(encoding="utf-8"))
        for target in targets:
            run = target["run"]
            topic = run[len("run_"):].rsplit("_", 1)[0]
            ground = tmp / f"{topic}.png"
            if not ground.exists():
                _ground(dev / "assets" / topic / "01_commons.jpg").save(ground)
            ass = vc.VideoComposer().build_subtitles(runs[run], tmp / f"{run}.ass", emphasis=emphasis.get(run))
            png = out / f"caption_{topic}_{target['t']:.2f}.png"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(ground), "-vf",
                            f"setpts=PTS+{target['t']:.3f}/TB,subtitles='{_esc(ass)}':fontsdir='{_esc(app / 'fonts')}'",
                            "-frames:v", "1", "-threads", "1", str(png)], check=True, capture_output=True, timeout=300)
        for case in catalogue["designs"]["covers"]["cases"]:
            photo = dev / str(case["hero"]["photo"])
            if not photo.is_file() or "amber" not in case["run"]:
                continue
            topic = Path(case["run"]).name[len("run_"):].rsplit("_", 1)[0]
            source = Image.open(photo).convert("RGBA")
            for style in STYLES:
                lay = Layout("cover", (1080, 1920), (60, 420, 1020, 1500))
                im = cover_design.render_cover(source.resize((1080, 1920)), case["copy"], style, lay=lay,
                                               source=source).convert("RGB")
                im.save(out / f"cover_{topic}_{style}.png")
                crop = im.crop(cover_design.CROP_34)
                tile = crop.resize((124, round(124 * 4 / 3)), Image.Resampling.LANCZOS)
                tile.resize((tile.width * 4, tile.height * 4), Image.Resampling.NEAREST).save(
                    out / f"grid_{topic}_{style}.png")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) not in (4, 5):
        raise SystemExit("usage: render_evidence.py <code root> <dev checkout> <out dir> [targets.json]")
    main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve(),
         Path(sys.argv[4]).resolve() if len(sys.argv) == 5 else None)
