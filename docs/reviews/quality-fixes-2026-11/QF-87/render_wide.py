"""QF-87 16:9 caption evidence (not run by measure.py; the board is committed as a JPEG).

    python render_wide.py <code root> <dev checkout> <out dir>

The 16:9 caption file the pipeline builds for three dev narrations (the freeze panel's committed words.json: the
long Richat documentary, the Amber Room and Tunguska runs), drawn by libass (ffmpeg) over each run's first photo
(<dev checkout>/assets/<topic>/01_commons.jpg, read only) at the middle of three bursts. On the base the face is the
bundled Montserrat-Bold.ttf; on QF-87 it is RR Sans, the 700 instance derived from Montserrat-Variable.ttf and
embedded in the file. Writes wide_<topic>_<t>.png and wide.json (each frame's caption-pixel box).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RUNS = ("run_richat_structure_sahara_eye_1792740961_long", "run_amber_room_heist_1792790550",
        "run_tunguska_event_1908_1792930600")


def _esc(p: Path) -> str:
    return str(p.resolve()).replace("\\", "/").replace(":", "\\:")


def main(code: Path, dev: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="qf87-wide-"))
    try:
        app = tmp / "app"
        shutil.copytree(code / "fonts", app / "fonts")
        (app / "data").mkdir()
        shutil.copyfile(code / "data" / "reference_facts.json", app / "data" / "reference_facts.json")
        os.environ.update(RELIC_APP_ROOT=str(app), FONT_SIZE="80")
        sys.path.insert(0, str(code))
        from PIL import Image, ImageOps

        from src import video_composer as vc
        fx = code / "docs" / "reviews" / "freeze-panel-2026-11-04" / "fixtures" / "runs"
        record = {}
        for run in RUNS:
            words = json.loads((fx / run / "words.json").read_text(encoding="utf-8"))
            topic = run[len("run_"):].split("_17")[0]
            ground = tmp / f"{topic}.png"
            ImageOps.fit(Image.open(dev / "assets" / topic / "01_commons.jpg").convert("RGB"), (1920, 1080),
                         Image.Resampling.LANCZOS).save(ground)
            ass = vc.VideoComposer().build_subtitles(words, tmp / f"{topic}.ass", width=1920, height=1080)
            phrases = vc.caption_phrases(words, None, True)
            times = vc.burst_timings(phrases)
            for k in (2, len(phrases) // 2, len(phrases) - 3):
                show, hide, _ = times[k]
                t = round((show + hide) / 2, 2)
                png = out / f"wide_{topic}_{t:.2f}.png"
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(ground), "-vf",
                                f"setpts=PTS+{t:.3f}/TB,subtitles='{_esc(ass)}':fontsdir='{_esc(app / 'fonts')}'",
                                "-frames:v", "1", "-threads", "1", str(png)], check=True, capture_output=True,
                               timeout=300)
                import numpy as np
                a = np.asarray(Image.open(png).convert("RGB")).astype(int)
                b = np.asarray(Image.open(ground).convert("RGB")).astype(int)
                ys, xs = np.nonzero(np.abs(a - b).max(-1) > 12)
                record[png.name] = {"text": " ".join(w["word"] for w in phrases[k]),
                                    "box": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]}
        (out / "wide.json").write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8",
                                       newline="\n")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("usage: render_wide.py <code root> <dev checkout> <out dir>")
    main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve())
