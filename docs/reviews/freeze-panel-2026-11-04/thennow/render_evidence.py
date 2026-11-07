"""Review evidence for the then-and-now clip (src/then_now.py), freeze panel 2026-11-04, on REAL run inputs only.

Every case comes from the freeze-panel fixtures catalogue (../fixtures/fixtures.json, loaded by path):
  - the two accepted pairs any run ever rendered (fixtures.load("thennow")): each run's own graphics/then_0.jpg and
    the matched now_0_1.jpg, with the years that run drew (read off its own graphic_thennow.mp4). Where a run kept a
    registered pair (graphics/registered/*_reg.jpg) it is used; neither run has one, so meta.json records that the
    unregistered then_0 / now_0_1 were used;
  - the near-white stress cases, built from the brightest REAL photos the catalogue lists
    (section("thennow")["rejected_old_photo_candidates_brightest"]): rejected candidates, used as grounds only, and
    labelled "new: brightest real photo, not an accepted pair". The NOW photo is the same run's own brightest recent
    candidate. A rejected candidate kept no capture year, so the years drawn are years the topic's own fact sheet
    states (fact ids recorded); they are NOT the photos' capture years, and the label says so. No photo is ever
    paired with another topic's text; nothing is synthetic.

Renders src.then_now.render() from the tree given by --code-root (inserted first on sys.path) in 9:16 (1080x1920)
and 16:9 (1920x1080), through the tree's own then_now._encode (the clip as it ships), teeing the frames: five
moments are kept (THEN alone, mid wipe, NOW whole at the hold, the split hold at frame 165, the last frame).
record_contrast is replaced by a recorder of each line's planned / drawn ratio and halo.
Freeze panel round 2 (T11): the clip then goes through the delivered chain (the master's own picture filter,
video_composer.film_grade_filter + noise=alls=8:allf=t+u, encoded with encoding.get_video_encoder(), then libx264
CRF 26), and each moment is decoded from it (frame_<moment>_enc.png). meta.json carries the delivered glow-fall
numbers (p90 vertical flat run, max smoothed step, monotonicity), the darkened-area ratio (T2e), the colour
fidelity (T3d), the group schedule check (T5), the type mode per marker, the pair's halo, glow steps, the 9:16 side,
the framing (zoom, trims) and whether the inputs were registered. Two ink-off variants (INK_ON_PAPER False:
16:9 Gol Gumbaz, 9:16 Odessa) keep the smooth-sky and near-white glows measured after ink type removed them.
Pantheon is also rendered with then_now.MOTION_BLUR off (frames hashed, not saved) for the T6 byte-identity and
render-time checks.

Masters are RGB PNG, or lossless WebP above the size cap; never palette-quantised (asserted).
Then compare sheets: compare/<variant>_before_after.jpg (make_compare.py), half size, labelled.

Usage (one command regenerates the 8 renders, the 2 ink-off variants and the sheets):
  python render_evidence.py --code-root <tree> --out <dir> [--jobs 3] [--only case,...]
         [--compare-with <before dir> --compare-out <dir>]
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parent / "fixtures" / "fixtures.py"
MAX_BYTES = 2 * 1024 * 1024
FORMATS = {"9x16": (1080, 1920), "16x9": (1920, 1080)}
# frame indices (30 fps): THEN alone, mid wipe (fast, blurred seam), NOW whole (the hold), the split hold, the last
MOMENTS = {"then_alone": 27, "mid_wipe": 44, "now_wide": 69, "split_hold": 165, "split": 179}
NEW_LABEL = "new: brightest real photo, not an accepted pair"
INKOFF = {("bright_golgumbaz_map", "16x9"), ("nearwhite_odessa_map", "9x16")}
# Review round 2: 16:9 Gol Gumbaz ink-off still draws no glow (its halo holds), so the smooth-sky fall was never
# measured. This variant raises the planner's target so the real smooth ground takes a glow; it measures the
# delivered chain only and is labelled as new.
FORCED = {("bright_golgumbaz_map", "16x9"): 5.2}
FORCED_LABEL = ("new: glow forced on a real smooth ground (planner target raised to {t}; the shipping planner draws "
                "no glow here); measures the delivered chain only")
NOBLUR = {"pantheon_1934_2018"}

# The near-white stress cases: (case name, run, old photo, recent photo, old year + fact id, recent year + fact id).
# Photos: the catalogue's brightest rejected old photos and, from the same run, its brightest recent candidate
# (measured here: Odessa now_0_4 mean luma 218, Gol Gumbaz now_2_0 141.5, the only recent photos fetched for then_2).
# Years: stated in the topic's own fact sheet (data/fact_sheets/<topic>.json); the candidate kept no capture year.
STRESS = [
    ("nearwhite_odessa_map", "output/run_odessa_catacombs_1792970549", "graphics/then_2.jpg", "graphics/now_0_4.jpg",
     (1941, "fact_7"), (1942, "fact_11")),
    ("bright_golgumbaz_map", "output/run_gol_gumbaz_whispering_gallery_1793278143", "graphics/then_2.jpg",
     "graphics/now_2_0.jpg", (1656, "fact-11"), (1937, "fact-15")),
]


# Review round 3: the reviewer's line-art regression case (review2/aswan_*), kept as a labelled stress case so the
# T1 paper gate is measured on a diagram ground: the run's own old and recent candidates (not an accepted pair), the
# years stated by its fact sheet.
REVIEW_STRESS = [
    ("lineart_aswan_diagram", "output/run_unfinished_obelisk_aswan_1792985912", "graphics/then_2.jpg",
     "graphics/now_0_0.jpg", (1922, "fact-12"), (2005, "fact-13"), "unfinished_obelisk_aswan",
     "new: brightest real recent photo (a line-art diagram, luma 237), same-topic, not an accepted pair"),
    # Fix round 1 (T7 nested border): the reviewer's busy case (review3/busy_aswan), the run's own then_0 (an archive
    # negative scan: a black film rebate with handwriting inside a white print margin) and now_0_1.
    ("busy_aswan_rebate", "output/run_unfinished_obelisk_aswan_1792985912", "graphics/then_0.jpg",
     "graphics/now_0_1.jpg", (1922, "fact-12"), (2005, "fact-13"), "unfinished_obelisk_aswan",
     "new: the run's own then_0 / now_0_1 (a scan with a nested film rebate), same-topic, not an accepted pair"),
]


def _fixtures():
    spec = importlib.util.spec_from_file_location("rr_freeze_fixtures", FIXTURES)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _registered(old: Path, new: Path) -> tuple[Path, Path, bool]:
    """The run's registered pair (graphics/registered/<stem>_reg.jpg) when it kept one, else the inputs as given."""
    reg = old.parent / "registered"
    ro, rn = reg / f"{old.stem}_reg.jpg", reg / f"{new.stem}_reg.jpg"
    if ro.exists() and rn.exists():
        return ro, rn, True
    return old, new, False


def cases() -> list[dict]:
    """Every case, from the catalogue: the accepted pairs, then the labelled near-white stress cases."""
    fx = _fixtures()
    out = []
    for c in fx.load("thennow"):
        topic = c["topic"]
        name = {"roman_self_healing_concrete": "pantheon", "singing_colossi_of_memnon": "colossi"}.get(topic, topic)
        old, new, reg = _registered(Path(c["old"]), Path(c["new"]))
        out.append({"name": f"{name}_{c['old_year']}_{c['new_year']}", "run": Path(c["run"]).name, "topic": topic,
                    "old": str(old), "old_year": int(c["old_year"]), "new": str(new),
                    "new_year": int(c["new_year"]), "label": c.get("label"), "why": c.get("why"),
                    "years_source": c.get("years_source"), "old_measured": c.get("old_measured"),
                    "new_measured": c.get("new_measured"), "registered": reg,
                    "inputs": "registered pair (graphics/registered)" if reg else
                    "unregistered then_0 / now_0_1 (the run kept no registered pair)"})
    listed = {str(r["photo"]): r for r in fx.section("thennow").get("rejected_old_photo_candidates_brightest", [])}
    for name, run, old_rel, new_rel, (oy, of), (ny, nf) in STRESS:
        old, new = fx.path(f"{run}/{old_rel}"), fx.path(f"{run}/{new_rel}")
        entry = listed.get(str(old))
        if entry is None:
            raise SystemExit(f"{old}: not in the catalogue's rejected_old_photo_candidates_brightest")
        topic = entry["topic"]
        facts = {f.get("id"): f.get("statement") for f in fx.sheet(topic).get("facts", [])}
        for y, fid in ((oy, of), (ny, nf)):
            if str(y) not in str(facts.get(fid, "")) and str(y) not in json.dumps(
                    [f for f in fx.sheet(topic).get("facts", []) if f.get("id") == fid]):
                raise SystemExit(f"{topic} {fid}: does not state {y}")
        out.append({"name": name, "run": Path(run).name, "topic": topic, "old": str(old), "old_year": oy,
                    "new": str(new), "new_year": ny,
                    "label": f"{NEW_LABEL}; years from fact sheet {of} / {nf}, not capture years",
                    "why": f"near-white stress: {entry['ground']} old photo (mean luma {entry['measured']['mean_luma']})",
                    "years_source": {"old": {"year": oy, "fact": fid_text(facts, of)},
                                     "new": {"year": ny, "fact": fid_text(facts, nf)}},
                    "old_measured": entry["measured"], "registered": False,
                    "inputs": "unregistered candidates (rejected pair; no registration exists)"})
    for name, run, old_rel, new_rel, (oy, of), (ny, nf), topic, label in REVIEW_STRESS:
        old, new = fx.path(f"{run}/{old_rel}"), fx.path(f"{run}/{new_rel}")
        facts = {f.get("id"): f.get("statement") for f in fx.sheet(topic).get("facts", [])}
        for y, fid in ((oy, of), (ny, nf)):
            if str(y) not in str(facts.get(fid, "")):
                raise SystemExit(f"{topic} {fid}: does not state {y}")
        out.append({"name": name, "run": Path(run).name, "topic": topic, "old": str(old), "old_year": oy,
                    "new": str(new), "new_year": ny,
                    "label": f"{label}; years from fact sheet {of} / {nf}, not capture years",
                    "why": ("nested scan border: the T7 trim must take the film rebate inside the white margin"
                            if name.startswith("busy_") else
                            "line-art ground: the T1 gate must not set ink type over a diagram's own black rules"),
                    "years_source": {"old": {"year": oy, "fact": fid_text(facts, of)},
                                     "new": {"year": ny, "fact": fid_text(facts, nf)}},
                    "registered": False, "inputs": "unregistered candidates (rejected pair; no registration exists)"})
    return out


def fid_text(facts: dict, fid: str) -> str:
    return f"{fid}: {facts.get(fid)}"


# ---------------------------------------------------------------- saving
def save_master(im, stem: Path) -> str:
    """RGB PNG; above the size cap, lossless WebP (method 6). Never palette-quantised (T11a)."""
    im = im.convert("RGB")
    png = stem.with_suffix(".png")
    im.save(png, optimize=True)
    if png.stat().st_size > MAX_BYTES:
        webp = stem.with_suffix(".webp")
        im.save(webp, lossless=True, method=6)
        png.unlink()
        out = webp
    else:
        out = png
    from PIL import Image
    with Image.open(out) as chk:
        assert chk.mode != "P", f"{out}: palette master"
    return f"{out.name} ({out.stat().st_size // 1024} KB)"


def save_jpeg(im, path: Path, q: int = 88) -> None:
    im = im.convert("RGB")
    while True:
        im.save(path, quality=q)
        if path.stat().st_size <= MAX_BYTES or q <= 50:
            return
        q -= 8


# ---------------------------------------------------------------- the delivered chain (T3)
def delivered_chain(clip: Path, work: Path) -> tuple[Path, Path, str]:
    """(master, delivered) mp4s: the master's own picture filter (imported, not copied) and encoder, then libx264
    CRF 26 (the platform-like re-encode); and the encoder used."""
    from src.encoding import get_video_encoder
    from src.video_composer import film_grade_filter
    work.mkdir(parents=True, exist_ok=True)
    from src import encoding
    vf = f"format=yuv420p,{film_grade_filter(0.0, out_dir=work)},format=yuv420p,noise=alls=8:allf=t+u"
    enc = get_video_encoder()
    # Review round 2: encoding's NVENC probe has a 3 s timeout, which a loaded machine misses (6 of 10 jobs fell
    # back to libx264 CRF 18). The probe is the master's own; it is only asked again, never bypassed.
    for _ in range(8):
        if "h264_nvenc" in enc:
            break
        encoding._DETECTED_ENCODER = None
        time.sleep(2)
        enc = get_video_encoder()
    master, final = work / "master.mp4", work / "delivered.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-vf", vf, *enc, "-pix_fmt", "yuv420p", str(master)],
                   check=True, timeout=600, capture_output=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(master), "-c:v", "libx264", "-crf", "26",
                    "-pix_fmt", "yuv420p", str(final)], check=True, timeout=600, capture_output=True)
    return master, final, " ".join(enc)


def decode_frame(clip: Path, index: int, out_png: Path):
    from PIL import Image
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-vf", f"select=eq(n\\,{index})", "-vsync", "0",
                    "-frames:v", "1", str(out_png)], check=True, timeout=300, capture_output=True)
    with Image.open(out_png) as im:
        return im.convert("RGB")


def _luma(a):
    import numpy as np
    a = np.asarray(a, np.float64)
    return a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114


def fall_stats(im, boxes, fall_px: int, pad: int, clip_box) -> dict:
    """T3(a, b) on `im`: column profiles below the marker's lines, from the line box's bottom edge to the glow's
    reach end, every 3rd column over the box: the p90 vertical flat run (consecutive equal integer Y), and on the
    9 x 9 box-smoothed image the largest step per px and the largest reversal (a profile should only lighten
    away from the marker on a plain ground; a busy photo's own structure shows here too)."""
    import cv2
    import numpy as np
    y = np.asarray(im, np.float64) if np.ndim(im) == 2 else _luma(im)    # an image, or a Y array (the glow alone)
    ys = cv2.blur(y, (9, 9))
    x0, x1 = int(min(b[0] for b in boxes)), int(max(b[2] for b in boxes))
    top = int(max(b[3] for b in boxes))
    bot = min(top + pad + fall_px, int(clip_box[3]))
    x0, x1 = max(x0, int(clip_box[0])), min(x1, int(clip_box[2]))
    runs, steps, rev = [], [], []
    for x in range(x0, x1, 3):
        col = np.round(y[top:bot, x]).astype(int)
        if col.size < 4:
            continue
        change = np.flatnonzero(np.diff(col) != 0)
        runs += list(np.diff(np.concatenate(([0], change + 1, [col.size]))))
        sm = ys[top:bot, x]
        d = np.diff(sm)
        steps.append(float(np.abs(d).max()))
        rev.append(float(max(0.0, -d.min())))
    if not runs:
        return {}
    return {"p90_flat_run_px": float(np.percentile(runs, 90)), "max_flat_run_px": int(max(runs)),
            "max_step_y_per_px": round(max(steps), 3), "max_reversal_y": round(max(rev), 3),
            "rows": [top, bot], "cols": [x0, x1]}


def _lab(rgb):
    import cv2
    import numpy as np
    return cv2.cvtColor(np.asarray(rgb, np.float32).reshape(-1, 1, 3) / 255.0, cv2.COLOR_RGB2LAB).reshape(-1, 3)


def de2000(lab1, lab2):
    """CIEDE2000 between rows of two Lab arrays."""
    import numpy as np
    L1, a1, b1 = lab1.T
    L2, a2, b2 = lab2.T
    C1, C2 = np.hypot(a1, b1), np.hypot(a2, b2)
    Cb = (C1 + C2) / 2
    G = 0.5 * (1 - np.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p, h2p = np.degrees(np.arctan2(b1, a1p)) % 360, np.degrees(np.arctan2(b2, a2p)) % 360
    dLp, dCp = L2 - L1, C2p - C1p
    dhp = h2p - h1p
    dhp = np.where(C1p * C2p == 0, 0, np.where(dhp > 180, dhp - 360, np.where(dhp < -180, dhp + 360, dhp)))
    dHp = 2 * np.sqrt(C1p * C2p) * np.sin(np.radians(dhp / 2))
    Lbp, Cbp = (L1 + L2) / 2, (C1p + C2p) / 2
    hsum = h1p + h2p
    hbp = np.where(C1p * C2p == 0, hsum, np.where(np.abs(h1p - h2p) <= 180, hsum / 2,
                                                  np.where(hsum < 360, (hsum + 360) / 2, (hsum - 360) / 2)))
    T = (1 - 0.17 * np.cos(np.radians(hbp - 30)) + 0.24 * np.cos(np.radians(2 * hbp))
         + 0.32 * np.cos(np.radians(3 * hbp + 6)) - 0.20 * np.cos(np.radians(4 * hbp - 63)))
    Sl = 1 + 0.015 * (Lbp - 50) ** 2 / np.sqrt(20 + (Lbp - 50) ** 2)
    Sc, Sh = 1 + 0.045 * Cbp, 1 + 0.015 * Cbp * T
    Rt = (-2 * np.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7))
          * np.sin(np.radians(60 * np.exp(-(((hbp - 275) / 25) ** 2)))))
    return np.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2 + Rt * (dCp / Sc) * (dHp / Sh))


def colour_fidelity(rendered, decoded, graded_ref, plates_rec, glow_core) -> dict:
    """T3(d): the BONE year fill (eroded glyph interior) and the glow's full-depth core outside every line's halo:
    the delivered decode against (1) the rendered pixels and (2) the same rendered frame through the master grade
    as a still (graphics.graded_frame), so the chain's tag / matrix handling is separated from the grade itself."""
    import numpy as np
    out = {}
    for name, mask in list(plates_rec.items()) + [("glow_core", glow_core)]:
        if mask is None or mask.sum() < 20:
            continue
        a = np.asarray(rendered)[mask]
        b = np.asarray(decoded)[mask]
        row = {"pixels": int(mask.sum()),
               "mean_dE00_vs_rendered": round(float(de2000(_lab(a), _lab(b)).mean()), 2),
               "rendered_mean_rgb": [round(float(v), 1) for v in a.mean(axis=0)],
               "delivered_mean_rgb": [round(float(v), 1) for v in b.mean(axis=0)]}
        if graded_ref is not None:
            g = np.asarray(graded_ref)[mask]
            row["mean_dE00_vs_graded_still"] = round(float(de2000(_lab(g), _lab(b)).mean()), 2)
            row["graded_still_mean_rgb"] = [round(float(v), 1) for v in g.mean(axis=0)]
        out[name] = row
    return out


# ---------------------------------------------------------------- one job (its own process)
def run_one(code_root: str, out: str, name: str, fmt: str) -> None:
    sys.path.insert(0, code_root)
    import cv2
    import numpy as np
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = None
    from src import then_now as tn
    from src.config import VIDEO_FPS

    noblur = name.endswith("__noblur") or name.endswith("__timing")   # hashed only, not encoded or saved
    inkoff = name.endswith("__inkoff") or name.endswith("__forcedglow")
    base = name.split("__")[0]
    case = dict({c["name"]: c for c in cases()}[base])
    if inkoff:
        tn.INK_ON_PAPER = False
    if name.endswith("__forcedglow"):
        t = FORCED[(base, fmt)]
        tn.PLACE_TARGET = t
        case["label"] = "; ".join(v for v in (case.get("label"), FORCED_LABEL.format(t=t)) if v)
    if name.endswith("__noblur"):
        tn.MOTION_BLUR = False
    width, height = FORMATS[fmt]
    old, new = Path(case["old"]), Path(case["new"])
    dest = Path(out) / fmt / name
    dest.mkdir(parents=True, exist_ok=True)
    grabbed: dict = {}
    hashes: list = []
    rec: dict = {"rows": [], "plates": {}}
    real_encode = tn._encode

    def tee(frames, keep):
        for i, im in enumerate(frames):
            hashes.append(hashlib.sha256(im.convert("RGB").tobytes()).hexdigest()[:16])
            if i in keep:
                grabbed[keep[i]] = im.copy()
            yield im

    def grab(frames, out_path, n, width=0, height=0):
        keep = {idx: k for k, idx in MOMENTS.items()}
        if noblur:
            for _ in tee(frames, {}):
                pass
            return Path(out_path)
        return real_encode(tee(frames, keep), Path(out_path), n, width=width, height=height)

    def record(clip, frame, plates):
        # Review round 3 (T2b): the graded ratio as record_contrast measures it (graphics.graded_frame: the
        # master's grade on the clip's last frame), next to the planned and drawn ones, on the split frame.
        from src.graphics import graded_frame
        graded = graded_frame(frame, Path(clip).parent / "stage_cache" / "contrast")
        for label, p in plates:
            if p is None or p.empty:
                continue
            rec["plates"][label] = p
            rec["rows"].append({"name": label, "text": p.text, "planned": p.contrast, "fill": list(p.fill),
                                "drawn": p.measure(frame), "graded": p.measure(graded) if graded is not None else None,
                                "halo": p.halo, "box": [int(round(v)) for v in p.box]})
        return {}

    tn._encode = grab
    tn.record_contrast = record
    if not hasattr(tn, "band_fall"):        # a tree before this round (the HEAD "before" set): frames only
        clip = dest / "graphic_thennow.mp4"
        t0 = time.time()
        tn.render(old, case["old_year"], new, case["new_year"], clip, width=width, height=height)
        files = {}
        for k, im in grabbed.items():
            files[k] = save_master(im, dest / f"frame_{k}")
            save_jpeg(im.resize((im.width // 2, im.height // 2), Image.LANCZOS), dest / f"frame_{k}_half.jpg")
        with open(dest / "meta.json", "w", encoding="utf-8", newline="\n") as f:
            json.dump({"case": name, "run": case["run"], "topic": case["topic"], "label": case.get("label"),
                       "format": fmt, "code_root": code_root, "tree": "before this round (frames only)",
                       "old": case["old"], "old_year": case["old_year"], "new": case["new"],
                       "new_year": case["new_year"], "render_seconds_with_encode": round(time.time() - t0, 1),
                       "moments": {k: {"frame": MOMENTS[k], "file": files.get(k)} for k in MOMENTS},
                       "plates": rec["rows"]}, f, indent=1, default=str)
        return
    clip = dest / "graphic_thennow.mp4"
    secs, secs_off = float("inf"), float("inf")
    if name.endswith("__timing"):
        # T6(c), review round 2: blur on and blur off alternate in one process, three rounds, the fastest of each
        # kept, so the machine's load (other renders running) falls on both alike.
        for _ in range(3):
            for blur in (True, False):
                tn.MOTION_BLUR = blur
                hashes.clear()
                t0 = time.time()
                tn.render(old, case["old_year"], new, case["new_year"], clip, width=width, height=height)
                if blur:
                    secs = min(secs, time.time() - t0)
                else:
                    secs_off = min(secs_off, time.time() - t0)
        tn.MOTION_BLUR = True
    else:
        t0 = time.time()
        tn.render(old, case["old_year"], new, case["new_year"], clip, width=width, height=height)
        secs = time.time() - t0
    meta_tn = json.loads(json.dumps(tn.last_meta, default=str))
    sched = meta_tn.pop("schedule", {})
    if noblur:
        with open(dest / "meta.json", "w", encoding="utf-8", newline="\n") as f:
            json.dump({"case": name, "format": fmt, "render_seconds_no_encode": round(secs, 1),
                       "render_seconds_no_encode_blur_off": round(secs_off, 1) if secs_off < float("inf") else None,
                       "frame_sha": hashes}, f, indent=1)
        return

    files = {}
    for k, im in grabbed.items():
        files[k] = save_master(im, dest / f"frame_{k}")
        save_jpeg(im.resize((im.width // 2, im.height // 2), Image.LANCZOS), dest / f"frame_{k}_half.jpg")

    # the delivered chain
    work = dest / "_chain"
    chain_meta: dict = {}
    enc_frames = {}
    try:
        master, final, enc = delivered_chain(clip, work)
        chain_meta["master_encoder"] = enc
        for k, idx in MOMENTS.items():
            enc_frames[k] = decode_frame(final, idx, dest / f"frame_{k}_enc.png")
        clip_decoded = decode_frame(clip, MOMENTS["split_hold"], work / "clip_165.png")
    except Exception as e:  # noqa: BLE001 - evidence, recorded
        chain_meta["error"] = str(e)[:400]
        clip_decoded = None

    # marker geometry
    sides: dict = {}
    for r in rec["rows"]:
        side = r["name"].split("_")[0]
        b = r["box"]
        s = sides.get(side)
        sides[side] = b if s is None else [min(s[0], b[0]), min(s[1], b[1]), max(s[2], b[2]), max(s[3], b[3])]
    fall_px = tn.band_fall(width, height)
    margin = tn.BAND_PAD + fall_px + 48
    crop_at = {"then": ("then_alone", "split_hold"), "now": ("now_wide", "split_hold")}
    for side, box in sides.items():
        for k in crop_at.get(side, ()):
            for suffix, src in (("", grabbed.get(k)), ("_enc", enc_frames.get(k))):
                if src is None:
                    continue
                c = (max(0, box[0] - margin), max(0, box[1] - margin), min(src.width, box[2] + margin),
                     min(src.height, box[3] + margin))
                crop = src.crop(c)
                if not suffix:
                    crop.save(dest / f"crop_{k}_{side}.png", optimize=True)
                ImageOps.equalize(crop.convert("L")).save(dest / f"crop_{k}_{side}{suffix}_amp.png", optimize=True)

    # T3: delivered glow falls, on every glow that remains (frame 165, the split hold)
    falls = {}
    panel = {w: tn.panel_box(w, width, height) for w in ("then", "now")}
    for w in ("then", "now"):
        if meta_tn.get("glow_depth", {}).get(w) is None:
            continue
        boxes = [rec["rows"][i]["box"] for i in range(len(rec["rows"])) if rec["rows"][i]["name"].startswith(w)]
        fw = (meta_tn.get("glow_fall") or {}).get(w) or fall_px
        falls[w] = {"glow_fall_px": fw, "rendered": fall_stats(grabbed["split_hold"], boxes, fw, tn.BAND_PAD, panel[w])}
        if "split_hold" in enc_frames:
            falls[w]["delivered"] = fall_stats(enc_frames["split_hold"], boxes, fw, tn.BAND_PAD, panel[w])

    # T3(b) on the glow alone: the same render (the same plan, so the same labels) with the glow left out of the
    # frames only, through the same chain, subtracted: a busy photo's own structure (which dominated the raw profile
    # on Odessa) and the labels cancel, and only the glow's fall is left.
    if falls and "split_hold" in enc_frames:
        real_shade = tn._shade

        def no_glow_encode(frames, out_path, n, width=0, height=0):
            tn._shade = lambda im, mask, alpha=1.0, bbox=None: im.convert("RGB")    # planning is over
            return real_encode(frames, Path(out_path), n, width=width, height=height)
        tn._encode = no_glow_encode
        tn.record_contrast = lambda *a, **k: {}
        try:
            off_clip = work / "off" / "graphic_thennow.mp4"
            tn.render(old, case["old_year"], new, case["new_year"], off_clip, width=width, height=height)
            _, off_final, _ = delivered_chain(off_clip, work / "off")
            off165 = decode_frame(off_final, MOMENTS["split_hold"], work / "off_165.png")
            diff = _luma(enc_frames["split_hold"]) - _luma(off165) + 128.0
            for w in [k for k in falls if k in ("then", "now")]:
                boxes = [r["box"] for r in rec["rows"] if r["name"].startswith(w)]
                fw = falls[w]["glow_fall_px"]
                falls[w]["delivered_glow_only"] = fall_stats(diff, boxes, fw, tn.BAND_PAD, panel[w])
                ref = fall_stats(_luma(off165), boxes, fw, tn.BAND_PAD, panel[w])
                falls[w]["delivered_ground_only"] = {k: ref.get(k) for k in ("max_step_y_per_px", "max_reversal_y")}
            # Review round 3: the label's whole shadow (halo + glow) as delivered, against the same render with no
            # glow and no labels: what reads on screen below the lock-up (the handover's "stepped label shadow").
            from src import typography
            real_draw = typography.TextPlate.draw

            def bare_encode(frames, out_path, n, width=0, height=0):
                tn._shade = lambda im, mask, alpha=1.0, bbox=None: im.convert("RGB")
                typography.TextPlate.draw = lambda self, im, *a, **k: im
                try:
                    return real_encode(frames, Path(out_path), n, width=width, height=height)
                finally:
                    typography.TextPlate.draw = real_draw
            tn._encode = bare_encode
            bare_clip = work / "bare" / "graphic_thennow.mp4"
            tn.render(old, case["old_year"], new, case["new_year"], bare_clip, width=width, height=height)
            _, bare_final, _ = delivered_chain(bare_clip, work / "bare")
            bare165 = decode_frame(bare_final, MOMENTS["split_hold"], work / "bare_165.png")
            total = _luma(enc_frames["split_hold"]) - _luma(bare165) + 128.0
            for w in [k for k in falls if k in ("then", "now")]:
                boxes = [r["box"] for r in rec["rows"] if r["name"].startswith(w)]
                falls[w]["delivered_shadow_total"] = fall_stats(total, boxes, falls[w]["glow_fall_px"], tn.BAND_PAD,
                                                                panel[w])
        except Exception as e:  # noqa: BLE001 - evidence, recorded
            falls["glow_only_error"] = str(e)[:400]
        finally:
            tn._shade = real_shade

    # T2(e): darkened area round a bone/gold marker on a bright ground, outside its ink and halo
    a_src, b_src, _ = tn.frame_pair(old, new, width, height)
    t165 = MOMENTS["split_hold"] / VIDEO_FPS
    split = tn._split_at(width, height)
    unshaded = tn.compose(tn.pushed(a_src, t165, (width, height)), tn.pushed(b_src, t165, (width, height)), split)
    yu, yd = _luma(unshaded), _luma(grabbed["split_hold"])
    dark = {}
    year_masks = {}
    for w in ("then", "now"):
        ps = [rec["plates"].get(f"{w}_kicker"), rec["plates"].get(f"{w}_year")]
        if any(p is None for p in ps):
            continue
        excl = np.zeros((height, width), bool)
        for p in ps:
            x0, y0 = p.origin
            h, wd = p.glyphs.shape
            ink = np.asarray(p.full) > 0
            halo = (np.asarray(p.halo_mask, np.float64) / 255.0 * p.halo >= 10 / 215) if p.halo_mask is not None else ink
            excl[y0:y0 + h, x0:x0 + wd] |= ink | halo
        yp = ps[1]
        g = np.asarray(yp.full) > 127
        g = cv2.erode(g.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        m = np.zeros((height, width), bool)
        m[yp.origin[1]:yp.origin[1] + g.shape[0], yp.origin[0]:yp.origin[0] + g.shape[1]] = g
        year_masks[f"{w}_year_fill"] = m
        px0, py0, px1, py1 = panel[w]
        region = np.zeros((height, width), bool)
        region[py0:py1, px0:px1] = True
        area = int(((yu - yd > 10) & region & ~excl).sum())
        b = yp.box
        dark[w] = {"mode": meta_tn["type_mode"][w], "local_mean_y": (meta_tn["paper_gate"][w]["split"] or {}).get("mean"),
                   "darkened_px": area, "year_box_px": int((b[2] - b[0]) * (b[3] - b[1])),
                   "ratio": round(area / max(1, (b[2] - b[0]) * (b[3] - b[1])), 3)}

    # T3(d): colour fidelity of the BONE year fill and the glow core
    fidelity = {}
    if "split_hold" in enc_frames:
        from src.graphics import graded_frame
        graded_ref = graded_frame(grabbed["split_hold"], work / "graded")
        core = None
        for w in ("then", "now"):
            d = meta_tn.get("glow_depth", {}).get(w)
            if d is None:
                continue
            boxes = [r["box"] for r in rec["rows"] if r["name"].startswith(w)]
            a = tn.glow_alpha(boxes, width, height, d)
            c = (a >= d * 0.999)
            for p in [rec["plates"].get(f"{w}_kicker"), rec["plates"].get(f"{w}_year")]:
                if p is not None and p.halo_mask is not None:
                    hm = np.zeros((height, width), bool)
                    x0, y0 = p.origin
                    h, wd = p.glyphs.shape
                    hm[y0:y0 + h, x0:x0 + wd] = np.asarray(p.halo_mask) > 0
                    c &= ~hm
            core = c if core is None else core | c
        bone_years = {k: v for k, v in year_masks.items() if meta_tn["type_mode"][k.split("_")[0]] != "ink"}
        fidelity["delivered_vs"] = colour_fidelity(grabbed["split_hold"], enc_frames["split_hold"], graded_ref,
                                                   bone_years, core)
        if clip_decoded is not None:
            fidelity["clip_decode_vs"] = colour_fidelity(grabbed["split_hold"], clip_decoded, None, bone_years, core)

    # T2(c): the glow's reach. Its nonzero pixels against the line boxes padded by the spec's round(0.45 x year cap)
    # + 1 and by the fall the render used (BAND_PAD + glow_fall + 1, longer for a deep glow on a bright ground).
    reach = {}
    for w in ("then", "now"):
        d = meta_tn.get("glow_depth", {}).get(w)
        if d is None:
            continue
        boxes = [r["box"] for r in rec["rows"] if r["name"].startswith(w)]
        gy = ((meta_tn.get("paper_gate") or {}).get(w, {}).get("split") or {}).get("mean")
        nz = np.argwhere(tn.glow_alpha(boxes, width, height, d, gy) > 0)
        x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
        out_by = int(max(x0 - nz[:, 1].min(), nz[:, 1].max() + 1 - x1, y0 - nz[:, 0].min(), nz[:, 0].max() + 1 - y1))
        spec_pad = round(0.45 * tn.year_cap("landscape" if width > height else "portrait")) + 1
        used = tn.BAND_PAD + (meta_tn.get("glow_fall") or {}).get(w, fall_px) + 1
        reach[w] = {"reach_px": out_by, "spec_pad_px": spec_pad, "used_pad_px": used,
                    "inside_spec_pad": out_by <= spec_pad, "inside_used_pad": out_by <= used}
    graded = [r["graded"] for r in rec["rows"] if r.get("graded") is not None]
    contrast = {"min_planned": min((r["planned"] or 0) for r in rec["rows"]) if rec["rows"] else None,
                "min_drawn": min((r["drawn"] or 0) for r in rec["rows"]) if rec["rows"] else None,
                "min_graded": min(graded) if graded else None,
                "graded_ok_4_5": all(v >= 4.5 for v in graded) if graded else None}

    # T5: the group schedule against the divider, from the render's own schedule
    groups = meta_tn.get("groups", {})
    n = int(tn.CLIP_SECONDS * VIDEO_FPS)
    t5 = {}
    for w in ("then", "now"):
        al = sched.get(w, [])
        if not al:
            continue
        bad = [i for i in range(n) if al[i] > 0.02 and not tn.group_clear(tuple(groups[w]), w,
                                                                           tn.divider_at(i / VIDEO_FPS, width, height),
                                                                           width, height)]
        runs, cur = [], 0
        for i in range(1, n):
            if 0 < al[i] < 1:
                cur += 1
            elif cur:
                runs.append(cur)
                cur = 0
        t5[w] = {"drawn_while_not_clear": bad, "full_from_105": all(v >= 0.9999 for v in al[105:]),
                 "first_full_frame_after_95": next((i for i in range(95, n) if al[i] >= 0.9999), None),
                 "partial_runs_frames": runs}

    # T6: the blurred seam's mask width and divider energy, every fast frame
    t6 = []
    axis_edge = max(width, height)
    for i in range(1, n):
        pos, prev = tn.divider_at(i / VIDEO_FPS, width, height), tn.divider_at((i - 1) / VIDEO_FPS, width, height)
        dpos = pos - prev
        if abs(dpos) <= tn.BLUR_MIN_STEP:
            continue
        m = tn.seam_weights(axis_edge, pos, dpos).astype(np.float64)
        xs = np.arange(axis_edge) + 0.5

        def cross(level):          # where the (monotone) weight crosses `level`, linearly interpolated
            order = np.argsort(m)
            return float(np.interp(level, m[order], xs[order]))
        ramp = abs(cross(0.9) - cross(0.1))      # never `width`: that is the frame's width, used above
        fb, fk = tn.divider_profile(axis_edge, pos, dpos)
        t6.append({"frame": i, "dpos": round(dpos, 2), "width_10_90": round(ramp, 2),
                   "width_ratio": round(ramp / abs(dpos), 3),
                   "divider_energy_ratio": round(float(fb.sum() + fk.sum() * tn.KEYLINE_ALPHA / 255)
                                                 / (tn.DIVIDER_W + 2 * tn.KEYLINE_W * tn.KEYLINE_ALPHA / 255), 4)})

    meta = {"case": name, "run": case["run"], "topic": case["topic"], "label": case.get("label"),
            "why": case.get("why"), "format": fmt, "size": [width, height], "code_root": code_root,
            "inputs": case.get("inputs"), "registered": case.get("registered"),
            "ink_on_paper": getattr(tn, "INK_ON_PAPER", None),
            "type_mode": meta_tn.get("type_mode"), "kicker": meta_tn.get("kicker"), "halo_H": meta_tn.get("halo"),
            "glow_step": meta_tn.get("glow_step"), "glow_depth": meta_tn.get("glow_depth"),
            "side_916": meta_tn.get("side"), "framing": meta_tn.get("framing"), "paper_gate": meta_tn.get("paper_gate"),
            "old": case["old"], "old_year": case["old_year"], "new": case["new"], "new_year": case["new_year"],
            "years_source": case.get("years_source"), "render_seconds_with_encode": round(secs, 1),
            "moments": {k: {"frame": MOMENTS[k], "file": files.get(k)} for k in MOMENTS},
            "plates": rec["rows"], "delivered_chain": chain_meta, "delivered_glow_fall_T3": falls or "no glow",
            "darkened_area_T2e": dark, "colour_fidelity_T3d": fidelity, "group_schedule_T5": t5,
            "seam_blur_T6": t6, "groups": groups, "glow_reach_T2c": reach, "contrast_T2b": contrast,
            "glow_fall": meta_tn.get("glow_fall"), "frame_sha": hashes}
    with open(dest / "meta.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(meta, f, indent=1, default=str)
    for p in work.glob("*.png"):
        p.unlink()


# ---------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code-root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--only", default="")
    ap.add_argument("--one", nargs=2, metavar=("CASE", "FORMAT"))
    ap.add_argument("--merge-only", action="store_true")
    ap.add_argument("--compare-with", default=str(HERE / "before"))     # the HEAD tree, as shipped
    ap.add_argument("--compare-out", default=str(HERE / "compare"))
    args = ap.parse_args()
    code_root = str(Path(args.code_root).resolve())
    sys.path.insert(0, code_root)
    out = Path(args.out).resolve()        # the jobs run with cwd at the code root: never a relative --out
    if args.one:
        run_one(code_root, str(out), *args.one)
        return
    out.mkdir(parents=True, exist_ok=True)
    only = {s for s in args.only.split(",") if s}
    all_cases = cases()
    names = [c["name"] for c in all_cases if not only or c["name"] in only]
    jobs = [(n, fmt) for n in names for fmt in FORMATS]
    jobs += [(n + "__inkoff", fmt) for n, fmt in sorted(INKOFF) if n in names]
    jobs += [(n + "__forcedglow", fmt) for n, fmt in sorted(FORCED) if n in names]
    timing = [(n + suf, fmt) for n in sorted(NOBLUR) if n in names for fmt in FORMATS for suf in ("__noblur", "__timing")]
    if args.merge_only:          # only the T6 check and the compare sheets, on the renders already there
        jobs, timing = [], []
    else:
        with open(out / "manifest.json", "w", encoding="utf-8", newline="\n") as f:
            json.dump({"code_root": code_root, "cases": all_cases, "jobs": jobs + timing,
                       "rule": "real run photos and years only; anything new carries its label"}, f, indent=2)
    running: list = []
    failed = []
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    # Review round 2: the T6(c) timing renders run after everything else, one at a time, so load does not skew them.
    while jobs or running or timing:
        if not jobs and not running and timing:
            jobs.append(timing.pop(0))
        while jobs and len(running) < args.jobs:
            name, fmt = jobs.pop(0)
            log = open(out / f"_log_{fmt}_{name}.txt", "w", encoding="utf-8", newline="\n")
            p = subprocess.Popen([sys.executable, __file__, "--code-root", code_root, "--out", str(out), "--one", name, fmt],
                                 stdout=log, stderr=subprocess.STDOUT, env=env, cwd=code_root)
            running.append((p, name, fmt, log))
        time.sleep(1)
        for item in list(running):
            p, name, fmt, log = item
            if p.poll() is not None:
                log.close()
                running.remove(item)
                print(f"{'ok ' if p.returncode == 0 else 'FAIL'} {fmt} {name}", flush=True)
                if p.returncode != 0:
                    failed.append((fmt, name))
    for fmt in FORMATS:           # T11(a): no palette master anywhere in the output
        from PIL import Image
        for f in (out / fmt).glob("*/frame_*.png"):
            with Image.open(f) as im:
                assert im.mode != "P", f
    # T6: byte identity of the slow frames and the render time, blur on vs off (Pantheon)
    for n in sorted(NOBLUR):
        for fmt in FORMATS:
            on, off = out / fmt / n / "meta.json", out / fmt / f"{n}__noblur" / "meta.json"
            tpath = out / fmt / f"{n}__timing" / "meta.json"
            if not (on.exists() and off.exists() and tpath.exists()):
                continue
            a, b = json.loads(on.read_text(encoding="utf-8")), json.loads(off.read_text(encoding="utf-8"))
            c = json.loads(tpath.read_text(encoding="utf-8"))
            fast = {r["frame"] for r in a.get("seam_blur_T6", [])}
            same = [i for i in range(len(a["frame_sha"])) if i not in fast]
            a["blur_off_check_T6"] = {
                "slow_frames_identical": all(a["frame_sha"][i] == b["frame_sha"][i] for i in same),
                "slow_frames": len(same), "fast_frames": sorted(fast),
                "render_seconds_no_encode_blur_off": c.get("render_seconds_no_encode_blur_off"),
                "render_seconds_no_encode_blur_on": c.get("render_seconds_no_encode"),
                "timing": "blur on / off alternated in one process, 3 rounds, fastest of each",
                "time_rise": round(c["render_seconds_no_encode"] / c["render_seconds_no_encode_blur_off"] - 1, 3)}
            on.write_text(json.dumps(a, indent=1), encoding="utf-8", newline="\n")
    if args.compare_with and args.compare_out and Path(args.compare_with).resolve() != out.resolve():
        subprocess.run([sys.executable, str(HERE / "make_compare.py"), "--before", args.compare_with, "--after", str(out),
                        "--out", args.compare_out], check=False)
    if failed:
        print("failed:", failed)
        sys.exit(1)


if __name__ == "__main__":
    main()
