"""Caption style decision (2026-10-29): today's pill vs the critic's stroke-and-halo, measured on graded frames.

For each topic: the production caption ASS for a phrase (VideoComposer.build_subtitles), in brand colours, is
burned onto the topic's graded frame (the master chain: hue-safe film print, noise, vignette, then subtitles) in
two styles: A = the pill (CapBox under CapText, as shipped); B = no pill, a 7 px ink outline on the glyphs and a
blurred ink halo layer under them (the critic's spec). Each colour class (active/past bone, emphasis gold,
upcoming grey) is measured with the ring method (glyph interior median vs p90 of the ring 2-10 px outside the
glyph fill) at full size and at phone size (the frame and masks downscaled to 360 px wide, ring 1-4 px). Also
recorded: how much of the caption band's picture each style covers (pixels changed by more than 24/255)."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

MAIN = Path(r"C:\Users\omokolade\GitHub\relic-and-riddle")
os.environ["RELIC_APP_ROOT"] = str(MAIN)
sys.path.insert(0, str(MAIN))
FF = Path(r"C:\Users\omokolade\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin\ffmpeg.exe")
import numpy as np  # noqa: E402
import cv2  # noqa: E402
from PIL import Image  # noqa: E402
from src import brand  # noqa: E402
from src.typography import ring_contrast  # noqa: E402
from src.video_composer import VideoComposer  # noqa: E402

OUT = Path(r"C:\tmp\bo_work\captest")
OUT.mkdir(exist_ok=True)
BONE, GOLD, GREY = brand.ass_colour(brand.BONE), brand.ass_colour(brand.GOLD), brand.ass_colour(brand.STONE_LT)
INK = brand.ass_colour(brand.INK)
TOPICS = {
    "richat": ("Concentric rings span the desert sands", Path(r"C:\tmp\bo_before\out\richat\captions\graded_bg.png")),
    "gumbaz": ("Set with lime mortar around the gallery", Path(r"C:\tmp\bo_before\out\gumbaz\captions\graded_bg.png")),
    "derinkuyu": ("Carved from soft volcanic tuff below", Path(r"C:\tmp\bo_before\out\derinkuyu\captions\graded_bg.png")),
}
# extra graded grounds: the brightest beats (where a caption is hardest to read)
BRIGHT = {"richat": MAIN / "assets/richat_structure_sahara_eye/06_commons.jpg",
          "gumbaz": MAIN / "assets/gol_gumbaz_whispering_gallery/02_commons.jpg",
          "derinkuyu": MAIN / "assets/derinkuyu_underground_city/01_commons.jpg"}


def esc(p: Path) -> str:
    return str(p.resolve()).replace("\\", "/").replace(":", "\\:")


def burn(bg: Path, ass: Path, t: float, out: Path):
    vf = f"subtitles='{esc(ass)}':fontsdir='{esc(MAIN / 'fonts')}',format=rgb24"
    subprocess.run([str(FF), "-v", "error", "-y", "-loop", "1", "-framerate", "30", "-i", str(bg), "-vf", vf,
                    "-ss", f"{t:.3f}", "-frames:v", "1", str(out)], check=True)
    return np.asarray(Image.open(out).convert("RGB"))


def recolour(text: str) -> str:
    return text.replace("&H00FFFFFF&", BONE).replace("&H0000D7FF&", GOLD)


def style_b(lines):
    out = []
    for ln in lines:
        if ",CapBox," in ln:
            continue
        if ",CapText," in ln:
            head, txt = ln.split(",,0,0,0,,", 1)
            halo = head.replace("Dialogue: 1,", "Dialogue: 0,") + ",,0,0,0,," + txt.replace(
                "\\q2}", f"\\q2\\bord22\\blur16\\3c{INK}\\3a&H5A&\\1a&HFF&\\shad0}}", 1)
            main = head + ",,0,0,0,," + txt.replace("\\q2}", f"\\q2\\bord7\\3c{INK}\\shad0}}", 1)
            out += [halo, main]
            continue
        out.append(ln)
    return out


def mask_lines(lines, colour, b_style):
    """Only the glyph fill of words in `colour`, white on black: no box, no outline, no halo."""
    out = []
    for ln in lines:
        if ",CapBox," in ln:
            continue
        if ",CapText," in ln:
            if ln.startswith("Dialogue: 0,") and b_style:
                continue  # the halo layer
            ln = re.sub(r"\\c(&H[0-9A-F]{8}&)", lambda m: "\\c&H00FFFFFF&\\1a&H00&" if colour in (m.group(1), "ALL")
                        else "\\1a&HFF&", ln)
            ln = ln.replace("\\q2", "\\q2\\bord0\\shad0\\blur0", 1).replace("\\bord7", "").replace(f"\\3c{INK}", "")
        out.append(ln)
    return out


def ring_excl(rgb, mask, allmask, scale=1):
    """ring_contrast with every caption glyph fill (any colour) kept out of the ring: the ring is the picture,
    pill, stroke or halo around the glyphs, never a neighbouring word."""
    from src.typography import _lin
    k_in, k_out = (5, 21) if scale == 1 else (3, 9)
    if scale != 1:
        rgb = cv2.resize(rgb, (rgb.shape[1] // scale, rgb.shape[0] // scale), interpolation=cv2.INTER_AREA)
        mask = (cv2.resize(mask, (mask.shape[1] // scale, mask.shape[0] // scale), interpolation=cv2.INTER_AREA) > 110).astype(np.uint8) * 255
        allmask = (cv2.resize(allmask, (allmask.shape[1] // scale, allmask.shape[0] // scale), interpolation=cv2.INTER_AREA) > 110).astype(np.uint8) * 255
    g = (mask > 127).astype(np.uint8)
    a = (allmask > 127).astype(np.uint8)
    ring = (cv2.dilate(g, np.ones((k_out, k_out), np.uint8)) > 0) & ~(cv2.dilate(a, np.ones((k_in, k_in), np.uint8)) > 0)
    lb = float(np.percentile(_lin(rgb[ring]), 90))
    inner = cv2.erode(g, np.ones((3, 3), np.uint8))
    lt = float(np.median(_lin(rgb[inner > 0]))) if inner.sum() > 30 else float(np.median(_lin(rgb[g > 0])))
    return round((max(lt, lb) + 0.05) / (min(lt, lb) + 0.05), 2)


def ring_small(rgb, mask):
    """Ring contrast at phone size: frame and mask at 1/3 scale (360 px wide), ring 1-4 px."""
    import src.typography as ty
    small = cv2.resize(rgb, (rgb.shape[1] // 3, rgb.shape[0] // 3), interpolation=cv2.INTER_AREA)
    m = cv2.resize(mask, (mask.shape[1] // 3, mask.shape[0] // 3), interpolation=cv2.INTER_AREA)
    old = ty._RING_INNER, ty._RING_OUTER
    ty._RING_INNER, ty._RING_OUTER = 3, 9
    try:
        return ring_contrast(small, (m > 110).astype(np.uint8) * 255, (0, 0))
    finally:
        ty._RING_INNER, ty._RING_OUTER = old


def grade_photo(src: Path, dst: Path):
    from src.typography import cover_crop
    from src.video_composer import film_grade_filter
    tmp = dst.with_suffix(".src.png")
    cover_crop(src, (1080, 1920)).convert("RGB").save(tmp)
    lut = film_grade_filter(0.0, out_dir=OUT / "_luts")
    vf = f"format=yuv420p,{lut},format=yuv420p,noise=alls=8:allf=t+u:all_seed=7,vignette=PI/4.2:aspect=9/16,format=rgb24"
    subprocess.run([str(FF), "-v", "error", "-y", "-i", str(tmp), "-vf", vf, "-frames:v", "1", str(dst)], check=True)
    return dst


results = {}
black = OUT / "black.png"
Image.new("RGB", (1080, 1920), (0, 0, 0)).save(black)
for name, (phrase, graded) in TOPICS.items():
    words, at = [], 0.0
    for w in phrase.split():
        words.append({"word": w, "start": at, "dur": 0.32})
        at += 0.36
    ass = VideoComposer(MAIN / "fonts").build_subtitles(words, OUT / f"{name}_a.ass", emphasis=[phrase.split()[1]])
    text = recolour(ass.read_text(encoding="utf-8"))
    lines = text.split("\n")
    ass_a, ass_b = OUT / f"{name}_A.ass", OUT / f"{name}_B.ass"
    ass_a.write_text(text, encoding="utf-8")
    ass_b.write_text("\n".join(style_b(lines)), encoding="utf-8")
    grounds = {"beat": graded, "bright": grade_photo(BRIGHT[name], OUT / f"{name}_bright_graded.png")}
    times = [w["start"] + 0.1 for w in words]
    res = {}
    for gname, ground in grounds.items():
        plain = np.asarray(Image.open(ground).convert("RGB")).astype(np.int16)
        for style, a, b_style in (("pill", ass_a, False), ("stroke_halo", ass_b, True)):
            full, phone, cover = [], [], []
            for k, t in enumerate(times):
                frame = burn(ground, a, t, OUT / f"{name}_{gname}_{style}_{k}.png")
                cover.append(float((np.abs(frame.astype(np.int16) - plain).max(axis=2) > 24)[1050:1260].mean()))
                ma = OUT / f"{name}_all_mask.ass"
                ma.write_text("\n".join(mask_lines((ass_b if b_style else ass_a).read_text(encoding="utf-8").split("\n"),
                                                    "ALL", b_style)), encoding="utf-8")
                allm = burn(black, ma, t, OUT / f"_ma.png")[..., 0]
                for colour, label in ((BONE, "bone"), (GOLD, "gold"), (GREY, "grey")):
                    mp = OUT / f"{name}_{label}_mask.ass"
                    mp.write_text("\n".join(mask_lines((ass_b if b_style else ass_a).read_text(encoding="utf-8").split("\n"),
                                                       colour, b_style)), encoding="utf-8")
                    m = burn(black, mp, t, OUT / f"_m.png")[..., 0]
                    if (m > 127).sum() < 200:
                        continue
                    mask = (m > 127).astype(np.uint8) * 255
                    full.append((label, ring_excl(frame, mask, allm)))
                    phone.append((label, ring_excl(frame, m, allm, scale=3)))
            res[f"{gname}:{style}"] = {
                "min_full": min(v for _, v in full if v), "min_phone": min(v for _, v in phone if v),
                "by_colour_full": {c: min(v for l, v in full if l == c and v) for c in {l for l, _ in full}},
                "by_colour_phone": {c: min(v for l, v in phone if l == c and v) for c in {l for l, _ in phone}},
                "band_covered": round(max(cover), 3)}
    results[name] = res
    print(name, json.dumps(res, indent=1), flush=True)
(OUT / "caption_style_measurements.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
