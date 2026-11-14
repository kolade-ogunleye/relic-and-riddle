"""Before/after evidence renders for QF-24 from real dev runs (read-only).

    python render_evidence.py <base tree> <this tree> <dev checkout holding output/run_*> <out folder>

Each case is a real run's graphic, drawn from its verified_script.json with the run's own hero photo
(beat_images/), its last frame captioned with the run's own words.json through libass (as compose_video burns
captions) at the given narration time. Each tree draws its own frame in a child process; the pair is saved side by
side (base left, this commit right) as a half-size JPEG. The thin lines are an overlay of the measuring boxes, not
part of the output: grey the ads Reel box (65-888, foot 1248) and its graphics floor (1058), gold the organic Reel
box (113-848, foot 1370), its caption band top (1130) and graphics floor (1090). Writes only into <out folder>.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CASES = [   # (run, graphic, caption time in s): picked from measurements_dev.json as the widest changes
    ("run_trepanation_miracle_1792995462", "numbers", 6.0),
    ("run_richat_structure_sahara_eye_1792778206", "numbers", 8.0),
    ("run_amber_room_heist_1792790550", "numbers", 10.0),
    ("run_amber_room_heist_1792781224", "timeline", 12.0),
    ("run_unfinished_obelisk_aswan_1792993374", "scale", 9.0),
    ("run_wieliczka_salt_mine_1792974672", "scale", 7.0),
]


def _child(tree: str, dev: str, out: str) -> None:
    os.environ.update(FONT_SIZE="80", PYTHONIOENCODING="utf-8")
    sys.path.insert(0, tree)
    from PIL import Image

    from src import graphics
    from src.video_composer import VideoComposer
    state = {}
    graphics.VIDEO_FPS = 2
    graphics.graded_frame = lambda *a, **k: None
    graphics._encode = lambda frames, path, n, *a, **k: [None for _ in frames] and Path(path)
    graphics.record_contrast = lambda o, frame, plates: state.update(frame=frame.convert("RGB")) or {}
    graphics.record_nontext = lambda *a, **k: {}
    for run, kind, at in CASES:
        folder = Path(dev) / "output" / run
        p = json.loads((folder / "verified_script.json").read_text(encoding="utf-8"))
        photos = sorted((folder / "beat_images").glob("*.jpg"))
        photo = photos[0] if photos else None
        work = Path(tempfile.mkdtemp())
        state.clear()
        if kind == "numbers":
            graphics.render_stat_cards(p["stat_cards"], work / "g.mp4", photo, narration=p.get("narration_script") or "")
        elif kind == "timeline":
            graphics.render_timeline(p["timeline"], work / "g.mp4", photo=photo)
        else:
            graphics.render_scale(p["scale_fact"], work / "g.mp4")
        png = work / "g.png"
        state["frame"].save(png)
        words = json.loads((folder / "words.json").read_text(encoding="utf-8"))
        ass = VideoComposer().build_subtitles(words, work / "c.ass", emphasis=p.get("emphasis_words") or [])

        def esc(q: Path) -> str:
            return str(q.resolve()).replace("\\", "/").replace(":", "\\:")
        vf = f"setpts=PTS+{at:.3f}/TB,subtitles='{esc(ass)}':fontsdir='{esc(Path(tree) / 'fonts')}'"
        dst = Path(out) / f"{run}_{kind}.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(png), "-vf", vf, "-frames:v", "1", str(dst)],
                       check=True, timeout=300)
        Image.open(dst).convert("RGB").save(dst)
        shutil.rmtree(work, ignore_errors=True)


def _overlay(im):
    from PIL import ImageDraw
    d = ImageDraw.Draw(im)
    grey, gold = (150, 150, 150), (235, 180, 60)
    d.rectangle((65, 288, 888, 1248), outline=grey, width=2)
    d.line((65, 1058, 888, 1058), fill=grey, width=2)
    d.rectangle((113, 288, 848, 1370), outline=gold, width=2)
    d.line((113, 1130, 848, 1130), fill=gold, width=2)
    d.line((113, 1090, 848, 1090), fill=gold, width=2)
    return im


def main(base: str, head: str, dev: str, out: str) -> None:
    from PIL import Image
    out_p = Path(out)
    out_p.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="qf24-ev-") as tmp:
        for tag, tree in (("before", base), ("after", head)):
            (Path(tmp) / tag).mkdir()
            subprocess.run([sys.executable, __file__, "--child", tree, dev, str(Path(tmp) / tag)], check=True,
                           env=dict(os.environ, FONT_SIZE="80"), timeout=3600)
        for run, kind, _at in CASES:
            name = f"{run}_{kind}"
            pair = Image.new("RGB", (2160, 1920))
            for i, tag in enumerate(("before", "after")):
                pair.paste(_overlay(Image.open(Path(tmp) / tag / f"{name}.png").convert("RGB")), (1080 * i, 0))
            pair.resize((1080, 960), Image.LANCZOS).save(out_p / f"{name}_before_after.jpg", quality=82,
                                                         optimize=True)
            print(name)


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--child":
        _child(*sys.argv[2:])
    elif len(sys.argv) == 5:
        main(*sys.argv[1:])
    else:
        raise SystemExit("usage: render_evidence.py <base tree> <this tree> <dev checkout> <out folder>")
