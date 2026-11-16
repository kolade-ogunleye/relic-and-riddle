"""QF-133 measurement: the reconstruction badge on real dev runs, base vs head, both formats.
Writes results.json and the crops under out_dir.
argv: base_tree head_tree dev_output_dir out_dir"""
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from fontTools.ttLib import TTFont
from PIL import Image

BASE, HEAD, OUTPUT, OUT = map(Path, sys.argv[1:5])
PY = sys.executable
RUNS = ["run_gol_gumbaz_whispering_gallery_1793278143", "run_richat_structure_sahara_eye_1793474843",
        "run_voynich_manuscript_1792965308", "run_tunguska_event_1908_1792930600",
        "run_odessa_catacombs_1793100750", "run_amber_room_heist_1792805842"]
FORMATS = {"916": (1080, 1920), "169": (1920, 1080)}
T = 1.5           # the frame measured (the badge is on screen 0-4 s)
OUT.mkdir(parents=True, exist_ok=True)
FONTS = HEAD / "fonts"


def esc(p: Path) -> str:
    return str(p.resolve()).replace("\\", "/").replace(":", "\\:")


def ffmpeg(args, log=None):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-y", *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=300, check=False)
    if log:
        Path(log).write_text(r.stderr, encoding="utf-8", newline="\n")
    if r.returncode:
        raise RuntimeError(r.stderr[-2000:])
    return r.stderr


def render(ass: Path, bg_args, png: Path, fontsdir: Path, log=None):
    return ffmpeg([*bg_args, "-vf", f"setpts=PTS+{T}/TB,subtitles='{esc(ass)}':fontsdir='{esc(fontsdir)}'",
                   "-frames:v", "1", "-v", "debug", str(png)], log)


def only(ass_text: str, style: str) -> str:
    keep = [ln for ln in ass_text.splitlines() if not ln.startswith("Dialogue:") or f",{style}," in ln]
    return "\n".join(keep) + "\n"


def bbox(mask):
    ys, xs = np.nonzero(mask)
    return None if not len(xs) else [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]


def ps_files():
    out = {}
    for p in sorted(FONTS.glob("*.ttf")):
        with TTFont(p, lazy=True) as f:
            out.setdefault(f["name"].getDebugName(6), []).append(p)
    return out


EVIDENCE = {e["file"]: e["sha256"] for e in json.loads((FONTS / "license_evidence.json").read_text())["fonts"]}
PS = ps_files()
results = []
for run in RUNS:
    rdir = OUTPUT / run
    scene = rdir / "parallax" / "scene_01.mp4"
    source = scene if scene.is_file() else min((rdir / "beat_images").glob("*.jpg"))
    for fmt, (w, h) in FORMATS.items():
        case = OUT / f"{run}__{fmt}"
        case.mkdir(exist_ok=True)
        bg = case / "bg.png"
        ffmpeg([*(["-ss", "1"] if source.suffix == ".mp4" else []), "-i", str(source), "-vf",
                f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},format=rgb24", "-frames:v", "1",
                str(bg)])
        bg_args = ["-i", str(bg)]
        rec = {"run": run, "format": fmt, "background": source.relative_to(rdir).as_posix()}
        frames = {}
        for side, tree in (("before", BASE), ("after", HEAD)):
            ass = case / f"{side}.ass"
            meta = json.loads(subprocess.run([PY, str(Path(__file__).with_name("gen_ass.py")), str(tree), str(rdir / "words.json"),
                                              str(ass), str(w), str(h), "4.0"], capture_output=True, text=True,
                                             check=True, cwd=str(tree)).stdout.strip().splitlines()[-1])
            text = ass.read_text(encoding="utf-8")
            style = next(ln for ln in text.splitlines() if ln.startswith("Style: ReconBadge,")).split(",")
            full = case / f"{side}.png"
            log = render(ass, bg_args, full, FONTS, case / f"{side}.log")
            frames[side] = np.asarray(Image.open(full).convert("RGB")).astype(int)
            picks = re.findall(r"fontselect: \(([^,]+), (\d+), (\d+)\) -> ([^,]+), (\d+), (\S+)", log)
            badge_pick = [p for p in picks if p[0] == style[1]]
            psnames = {pk[5] for pk in badge_pick}
            psname = psnames.pop() if len(psnames) == 1 else None
            files = PS.get(psname, [])
            sha = hashlib.sha256(files[0].read_bytes()).hexdigest() if len(files) == 1 else None
            # badge text alone on black, plate alone on white
            (case / f"{side}_text.ass").write_text(only(text, "ReconBadge"), encoding="utf-8", newline="\n")
            (case / f"{side}_plate.ass").write_text(only(text, "ReconPlate"), encoding="utf-8", newline="\n")
            render(case / f"{side}_text.ass", ["-f", "lavfi", "-i", f"color=c=black:s={w}x{h}"], case / f"{side}_text.png",
                   FONTS)
            render(case / f"{side}_plate.ass", ["-f", "lavfi", "-i", f"color=c=white:s={w}x{h}"],
                   case / f"{side}_plate.png", FONTS)
            ink = bbox(np.asarray(Image.open(case / f"{side}_text.png").convert("L")) > 40)
            plate = bbox(np.asarray(Image.open(case / f"{side}_plate.png").convert("L")) < 250)
            over = max(0, plate[0] - ink[0], ink[2] - plate[2], plate[1] - ink[1], ink[3] - plate[3])
            # the bundled file alone: libass draws the same badge from a folder holding only that file
            alone_same = None
            if sha:
                solo = case / f"{side}_solo"
                solo.mkdir(exist_ok=True)
                shutil.copyfile(files[0], solo / files[0].name)
                render(case / f"{side}_text.ass", ["-f", "lavfi", "-i", f"color=c=black:s={w}x{h}"],
                       case / f"{side}_text_solo.png", solo)
                alone_same = bool(np.array_equal(np.asarray(Image.open(case / f"{side}_text_solo.png")),
                                                 np.asarray(Image.open(case / f"{side}_text.png"))))
            rec[side] = {"fontname": style[1], "bold": style[7], "fontsize": int(style[2]),
                         "libass_picks": [f"({pk[0]}, {pk[1]}, {pk[2]}) -> {pk[5]}" for pk in badge_pick],
                         "bundled_file": files[0].name if len(files) == 1 else None, "file_sha256": sha,
                         "file_sha256_matches_licence_evidence": bool(sha) and EVIDENCE.get(files[0].name) == sha,
                         "same_pixels_from_that_file_alone": alone_same,
                         "plate": plate, "text_ink": ink, "overrun_px": int(over),
                         "ink_gap_left_px": ink[0] - plate[0], "ink_gap_right_px": plate[2] - ink[2]}
        diff = np.abs(frames["before"] - frames["after"]).max(-1) > 0
        changed = bbox(diff)
        box = [min(rec[s]["plate"][0] for s in ("before", "after")) , min(rec[s]["plate"][1] for s in ("before", "after")),
               max(max(rec[s]["plate"][2], rec[s]["text_ink"][2]) for s in ("before", "after")),
               max(max(rec[s]["plate"][3], rec[s]["text_ink"][3]) for s in ("before", "after"))]
        plate_a = rec["after"]["plate"]
        rec["changed_pixels"] = int(diff.sum())
        rec["changed_bbox"] = changed
        rec["badge_box"] = box
        rec["changed_outside_badge_box"] = int(diff.sum() - diff[box[1]:box[3], box[0]:box[2]].sum())
        rec["plate_unchanged"] = rec["before"]["plate"] == plate_a
        results.append(rec)
        # crops (half-size JPEG): the badge region, before above after
        x0, y0, x1, y1 = max(0, box[0] - 24), max(0, box[1] - 24), min(w, box[2] + 40), min(h, box[3] + 24)
        crops = [Image.fromarray(frames[s][y0:y1, x0:x1].astype(np.uint8)) for s in ("before", "after")]
        results[-1]["crop"] = [x0, y0, x1, y1]
        for s, im in zip(("before", "after"), crops):
            im.save(case / f"crop_{s}.png")
        print(run, fmt, rec["before"]["overrun_px"], rec["after"]["overrun_px"], rec["changed_outside_badge_box"],
              flush=True)
(OUT / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8", newline="\n")
