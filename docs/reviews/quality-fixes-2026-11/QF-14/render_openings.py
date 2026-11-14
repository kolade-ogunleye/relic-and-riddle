"""QF-14 evidence renders: what a viewer sees while a real Reel's first sentence is spoken.

    python docs/reviews/quality-fixes-2026-11/QF-14/render_openings.py <repo root> <runs dir> <out dir> <run> [<run> ...]

For each named run in <runs dir> (development output/, read-only): its master's frame at 1.5 s (viewers decide in
under two seconds) and at the moment its first sentence ends in the real voiceover (words.json), side by side at
half size, with a label band: the first sentence, its spoken length, when it ends, and the hook rule's verdict on the
code in <repo root>. QF-14 changes no renderer: a refused opening goes to the writer's opening edit, whose new words
need a live model, so no "after" frame exists for it; the admitted openings are real runs of the same topics.
Writes <out dir>/<run>.jpg only.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def frame(video: Path, t: float, work: Path) -> Image.Image:
    out = work / f"f_{t:.2f}.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1", str(out)],
                   check=True)
    with Image.open(out) as im:
        return im.convert("RGB").resize((540, 960), Image.LANCZOS)


def wrap(draw, text, font, width):
    lines, line = [], ""
    for word in text.split():
        trial = (line + " " + word).strip()
        if draw.textlength(trial, font=font) > width and line:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + ([line] if line else [])


def main(root: Path, runs: Path, out: Path, names: list[str]) -> None:
    sys.path.insert(0, str(root))
    from src import script_engine as engine
    out.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype(str(root / "fonts" / "IBMPlexMono-Medium.ttf"), 19)
    for name in names:
        run = runs / name
        payload = json.loads((run / "script_metadata.json").read_text(encoding="utf-8"))
        timed = json.loads((run / "words.json").read_text(encoding="utf-8"))
        video = next(run.glob("*_master.mp4"))
        first = engine._first_sentence(payload["narration_script"])
        n = len(engine._spoken_words(first))
        last = timed[min(n, len(timed)) - 1]
        ends = last["start"] + last["dur"]
        faults = engine.hook_problems(payload["narration_script"])
        refused = bool(engine.blocking_problems(faults))
        why = sorted({"label" if "label" in f else "over 12 words" if "-word" in f else "no early signal" for f in faults})
        verdict = ("REFUSED (" + ", ".join(why) + "): goes to the one opening edit" if refused else "ADMITTED as written")
        with tempfile.TemporaryDirectory() as tmp:
            a, b = frame(video, 1.5, Path(tmp)), frame(video, max(0.0, ends - 0.05), Path(tmp))
        probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        text = [f"{name}", f"\"{first}\"", f"{n} spoken words; ends at {ends:.2f} s. Left: 1.50 s. Right: {ends:.2f} s.",
                f"QF-14: {verdict}"]
        lines = [ln for t in text for ln in wrap(probe, t, font, 1040)]
        band = 24 + 26 * len(lines)
        sheet = Image.new("RGB", (1080, 960 + band), (18, 18, 18))
        sheet.paste(a, (0, 0))
        sheet.paste(b, (540, 0))
        draw = ImageDraw.Draw(sheet)
        for i, ln in enumerate(lines):
            draw.text((20, 972 + 26 * i), ln, font=font, fill=(235, 235, 235))
        sheet.save(out / f"{name}.jpg", quality=74)
        print(name, n, round(ends, 2), "refused" if refused else "admitted")


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)
    main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve(), sys.argv[4:])
