"""Measure vs libass ink for every one-line caption burst of the five fixture runs, both formats, plus the ten
REEL_MEASURE_RAQM samples. Run from a code root (cwd); argv: out.json, frames dir.

For each burst: the tree's own measure (9:16 measure_caption_phrase at its fit; 16:9 the ink equivalent of the
plate's text measure), the kerned and the unkerned variant, and the ink width libass draws (ffmpeg subtitles)."""
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "docs/reviews/freeze-panel-2026-11-04/fixtures"))
import fixtures as fx

from src import video_composer as vc
from src.typography import get_font

out_json, frames = Path(sys.argv[1]), Path(sys.argv[2])
frames.mkdir(parents=True, exist_ok=True)
HAS_UNK = hasattr(vc, "unkerned_length")


def esc(p: Path) -> str:
    return str(p.resolve()).replace("\\", "/").replace(":", "\\:")


def ink_of(a: np.ndarray):
    """(plate left, plate right, ink left, ink right) from the plate's middle rows (the tests' _plate_and_ink)."""
    ys, _ = np.nonzero(a.sum(-1) < 200)
    band = a[ys.min() + 16:ys.max() - 15]
    plate_cols = np.nonzero((band.sum(-1) < 200).any(0))[0]
    pl, pr = int(plate_cols.min()), int(plate_cols.max()) + 1
    ink = band.sum(-1) > 300
    ink[:, :pl + 2] = False
    ink[:, pr - 2:] = False
    cols = np.nonzero(ink.any(0))[0]
    return pl, pr, int(cols.min()), int(cols.max()) + 1


def reel_ink_variants(shown, fit):
    """(kerned, unkerned) 9:16 ink measure; in a tree without unkerned_length only the kerned one."""
    vc._libass_line.cache_clear()
    if not HAS_UNK:
        return vc._caption_ink_width(shown, fit), None
    orig = vc.unkerned_length
    try:
        vc.unkerned_length = lambda f, t: f.getlength(t)
        vc._libass_line.cache_clear()
        kern = vc._caption_ink_width(shown, fit)
    finally:
        vc.unkerned_length = orig
        vc._libass_line.cache_clear()
    return kern, vc._caption_ink_width(shown, fit)


def wide_ink_variants(shown, wide_px, shrink_k, shrink_u):
    """(kerned, unkerned) 16:9 ink equivalents of the text measure: advance less the first glyph's left bearing and
    the last glyph's right bearing, at the shrink each measure itself would give."""
    font = get_font(vc.CAPTION_WIDE_FONT, wide_px, weight=700)
    text = " ".join(vc._ass_escape(w) for w in shown)
    l0 = font.getbbox(text[0])[0]
    last = text[-1]
    rb = font.getbbox(last)[2]
    k = (font.getlength(text[:-1]) + rb - l0) * shrink_k
    u = (sum(font.getlength(c) for c in text[:-1]) + rb - l0) * shrink_u
    return k, u


def render(ass: Path, w: int, h: int, ns: list[int], tag: str) -> list[np.ndarray]:
    for old in frames.glob(f"{tag}_*.png"):
        old.unlink()
    out = []
    for c0 in range(0, len(ns), 25):
        chunk = ns[c0:c0 + 25]
        sel = "+".join(rf"eq(n\,{n})" for n in chunk)
        pat = frames / f"{tag}_{c0:04d}_%04d.png"
        vf = f"select='{sel}',subtitles='{esc(ass)}':fontsdir='{esc(ROOT / 'fonts')}'"
        dur = max(chunk) / 30 + 0.5
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"color=c=0xF2EEE6:s={w}x{h}:r=30:d={dur:.2f}", "-vf", vf, "-fps_mode", "passthrough",
                        str(pat)], check=True, timeout=600)
        files = sorted(frames.glob(f"{tag}_{c0:04d}_*.png"))
        assert len(files) == len(chunk), (tag, len(files), len(chunk))
        out += [np.asarray(Image.open(f).convert("RGB")).astype(int) for f in files]
    return out


def t_of(s: str) -> float:
    h, m, x = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(x)


rows = []
for c in fx.load("captions"):
    words = fx.words(c)
    emph = list(c.get("emphasis_words") or [])
    for wide in (False, True):
        w, h = (1920, 1080) if wide else (1080, 1920)
        ass = frames / f"{c['topic']}_{'w' if wide else 'r'}.ass"
        vc.VideoComposer().build_subtitles(words, ass, emph, w, h)
        phrases = vc.caption_phrases(words, emph, wide, (min(int(w * 0.75), round(vc.CAPTION_WIDE_MAX_PLATE * w / 1920))
                                                        if wide else vc.MAX_CAPTION_WIDTH))
        boxes = [l for l in ass.read_text("utf-8").splitlines() if l.startswith("Dialogue: 0,") and ",CapBox," in l]
        assert len(boxes) == len(phrases), (c["topic"], wide, len(boxes), len(phrases))
        ns = []
        for b in boxes:
            parts = b.split(",")
            s, e = t_of(parts[1]), t_of(parts[2])
            ns.append(int((e - (0.12 if re.search(r"fad\(\d+,[1-9]", b) else 0.04)) * 30))
        imgs = render(ass, w, h, ns, f"{c['topic']}_{'w' if wide else 'r'}")
        wide_px = round(vc.CAPTION_WIDE_SIZE * h / 1080)
        for phrase, img in zip(phrases, imgs):
            shown = vc._phrase_words(phrase)
            row = {"topic": c["topic"], "format": "16:9" if wide else "9:16", "text": " ".join(shown).upper(),
                   "words": len(shown)}
            if wide:
                font = get_font(vc.CAPTION_WIDE_FONT, wide_px, weight=700)
                text = " ".join(vc._ass_escape(x) for x in shown)
                mw = vc.CAPTION_WIDE_MAX_PLATE * w / 1920
                maxw = min(int(w * 0.75), round(mw))
                tk = font.getlength(text)
                tu = sum(font.getlength(ch) for ch in text)
                sk = min(1.0, (maxw - 2 * vc.CAPTION_PAD_X) / tk)
                su = min(1.0, (maxw - 2 * vc.CAPTION_PAD_X) / tu)
                row["kerned"], row["unkerned"] = wide_ink_variants(shown, wide_px, sk, su)
                row["fit"] = None
            else:
                lines = vc.reel_caption_lines(phrase)
                if lines:
                    row["two_line"] = True
                    rows.append(row)
                    continue
                key = vc.emphasis_indices(words, emph)
                fit = vc._caption_fit_size(phrase)
                row["fit"] = fit
                row["tree"] = vc.measure_caption_phrase(phrase, fit)[0]
                row["kerned"], row["unkerned"] = reel_ink_variants(shown, fit)
            pl, pr, il, ir = ink_of(img)
            row["ink"] = ir - il
            row["plate"] = pr - pl
            rows.append(row)
        print(c["topic"], "16:9" if wide else "9:16", len(phrases), flush=True)

# The ten REEL_MEASURE_RAQM samples, each alone (the test's own render)
sys.path.insert(0, str(ROOT / "tests"))
import test_freeze_captions_2026_11_04 as T

samples = []
for text in T.REEL_MEASURE_RAQM:
    ws = [{"word": x, "start": 0.1 + 0.3 * k, "dur": 0.25} for k, x in enumerate(text.split())]
    phrases = vc.caption_phrases(ws)
    a = T._render_reel(ws, frames, ws[-1]["start"] + 0.2)
    _, _, il, ir = T._plate_and_ink(a)
    size = vc._caption_fit_size(phrases[0])
    kern, unk = reel_ink_variants(vc._phrase_words(phrases[0]), size)
    samples.append({"text": text, "size": size, "tree": vc.measure_caption_phrase(phrases[0], size)[0],
                    "kerned": kern, "unkerned": unk, "ink": ir - il, "phrases": len(phrases)})
out_json.write_text(json.dumps({"rows": rows, "samples": samples}, indent=1), "utf-8")
print("rows", len(rows), "samples", len(samples))
