"""Freeze-panel evidence for the burned-in captions (9:16 Reel bursts, the 16:9 documentary line), 2026-11-04.

Every case is a REAL run from the freeze-panel catalogue (../fixtures, fixtures.load("captions")): the run's own
words.json timings and emphasis_words go through the composer's own VideoComposer.build_subtitles, with the scene
cuts, graphic intervals and reconstruction intervals compose_video would derive from the run's scene timeline. Each
sample is burned with ffmpeg over the run's own scene background at that moment (fixtures.background_at: the
caption-free parallax/graphic clip frame, or the still), through compose_video's picture chain: film grade, grain,
the lens vignette (freeze pass V1: the code root's own picture_vignette(width, height), imported, so the evidence
chain cannot drift from production; a code root without it, such as HEAD 5fdb5dc, has its aspect read from
compose_video's source), lifted over graphic clips as graphic_vignette_enable does, then the subtitles filter.

    python render_evidence.py --code-root <checkout> --out <dir> [--before <dir> --compare <dir>]

--code-root goes first on sys.path, so the caption code under review is imported from there. The fixtures loader,
print LUT and fonts come from the main checkout (MAIN) by absolute path, so both runs share one input set. FONT_NAME
and FONT_SIZE are pinned from .env.example (Anton, 80), the values the dev .env carries; .env is never read here.

One case is new and labelled so: the catalogue records that no run narrates "Rashtrakuta dynasty", so the operator's
long-name decision is shown on the real Kailasa fact-sheet statement kailasa-commencement-patron (which names the
Rashtrakuta king), with word timings fitted to the Kailasa run's own voice rate and the Kailasa run's own photo. Its
timings are synthetic: it is layout evidence only, never timing evidence (the README's timing counts leave it out).

Outputs per sample <topic>_<fmt>_w<NNN>_<tag>: <id>.png (full-resolution PNG master of the caption band; a full grained
frame PNG runs over 2 MB), <id>_full.jpg, <id>_half.jpg. Per case: <topic>_<fmt>.ass, bursts_<topic>_<fmt>.txt (every
burst: time, size, plate width, text; the name and decision checks; 16:9 sidecar cues) and sheet_<topic>_<fmt>.jpg.
bursts.txt has every case; summary.json (the manifest) holds it all as data. With --before, labelled before | current
side-by-sides go to --compare (each names its run and topic and marks new cases), plus bursts_diff_<topic>.txt.
"""
from __future__ import annotations

import argparse
import difflib
import importlib.util
import inspect
import json
import os
import re
import shutil
import subprocess
import unicodedata
import sys
from pathlib import Path

MAIN = Path(r"C:/Users/omokolade/GitHub/relic-and-riddle")
LUT = MAIN / "assets" / "luts" / "kodak_2383.cube"
FONTS = MAIN / "fonts"
FIXTURES = MAIN / "docs" / "reviews" / "freeze-panel-2026-11-04" / "fixtures" / "fixtures.py"
FORMATS = {"9:16": (1080, 1920), "16:9": (1920, 1080)}
ARTICLES = {"THE", "A", "AN"}
BOND_FLOOR_PX = 66          # the operator's floor: no burst shrinks below it to hold a name
PINNED = ("FONT_NAME", "FONT_SIZE")

# The new case (see the module docstring). Text is the fact sheet's statement verbatim.
NEW_CASE = {
    "run": "output/run_kailasa_temple_monolith_1792524470",
    "topic": "kailasa_temple_monolith",
    "format": "9:16",
    "sheet": "data/fact_sheets/kailasa_temple_monolith.json",
    "fact_id": "kailasa-commencement-patron",
    "photo": "assets/kailasa_temple_monolith/01_commons.jpg",
    "label": "new: fact-sheet statement kailasa-commencement-patron (no run narrates it), synthetic fitted timings "
             "(layout only)",
    "why": "operator long-name decision (UNDER | THE RASHTRAKUTA ...): the closest real material",
    "proper_names_text": ["Rashtrakuta king Krishna I", "Kailasa temple"],
}


def load_fixtures():
    spec = importlib.util.spec_from_file_location("rr_fixtures", FIXTURES)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def pin_settings() -> dict[str, str]:
    pinned = {}
    for line in (MAIN / ".env.example").read_text("utf-8").splitlines():
        key, _, value = line.partition("=")
        if key.strip() in PINNED and value.strip():
            pinned[key.strip()] = value.strip()
    os.environ.update(pinned)
    return pinned


def load_code(code_root: Path):
    pin_settings()
    sys.path.insert(0, str(code_root))
    import src.video_composer as vc  # noqa: E402
    assert Path(vc.__file__).resolve().is_relative_to(code_root.resolve()), vc.__file__
    return vc


def vignette_aspect_of(vc, width: int, height: int) -> tuple[str, str]:
    """The vignette aspect compose_video at this code root passes for width x height, read from its own source."""
    src = inspect.getsource(vc.VideoComposer.compose_video)
    m = re.search(r"vignette=PI/4\.2:aspect=\{(\w+)\}", src)
    if not m:
        m2 = re.search(r"vignette=PI/4\.2:aspect=([0-9/.]+)", src)
        return (m2.group(1), "literal in compose_video") if m2 else ("1", "unresolved: ffmpeg default 1")
    var = m.group(1)
    a = re.search(rf"^\s*{var}\s*=\s*(.+)$", src, re.M)
    expr = a.group(1).split("#")[0].strip()
    return str(eval(expr, {"__builtins__": {}}, {"width": width, "height": height})), f"{var} = {expr}"


def scene_starts(case: dict) -> list[float]:
    """Scene starts as fixtures.background_at estimates them (unknown starts share the rest evenly)."""
    tl, dur = case["timeline"], float(case.get("duration_s") or 0)
    known = [s for s in tl if s.get("start_s_est") is not None]
    rest = tl[len(known):]
    starts = [float(s["start_s_est"]) for s in known]
    if rest:
        t0 = starts[-1] + float(known[-1].get("duration_s") or 0) if known else 0.0
        step = max(dur - t0, 0.0) / len(rest)
        starts += [t0 + i * step for i in range(len(rest))]
    return starts


def scene_index(starts: list[float], t: float) -> int:
    return max(k for k, s in enumerate(starts) if s <= t) if t >= starts[0] else 0


def fitted_words(text: str, rate_src: list[dict]) -> list[dict]:
    """Timings for the new case: the Kailasa run's own mean seconds per letter, word gap and pause lengths."""
    letters = sum(len(re.sub(r"[^\w]", "", w["word"])) for w in rate_src)
    spoken = sum(w["dur"] for w in rate_src)
    per_letter = spoken / max(letters, 1)
    gaps = [b["start"] - (a["start"] + a["dur"]) for a, b in zip(rate_src, rate_src[1:])]
    stop = [g for (a, g) in zip(rate_src, gaps) if re.search(r"[.!?]$", a["word"])]
    comma = [g for (a, g) in zip(rate_src, gaps) if re.search(r"[,;:]$", a["word"])]
    plain = sorted(g for (a, g) in zip(rate_src, gaps) if not re.search(r"[.,;:!?]$", a["word"]))
    gap = plain[len(plain) // 2] if plain else 0.04
    stop_g = sum(stop) / len(stop) if stop else 0.5
    comma_g = sum(comma) / len(comma) if comma else 0.2
    out, at = [], 0.1
    for w in text.split():
        d = round(max(0.08, per_letter * len(re.sub(r"[^\w]", "", w))), 3)
        out.append({"word": w, "start": round(at, 3), "dur": d})
        at += d + (stop_g if re.search(r"[.!?]$", w) else comma_g if re.search(r"[,;:]$", w) else gap)
    return out


def cover(src: Path, size):
    from PIL import Image, ImageOps
    with Image.open(src) as im:
        return ImageOps.fit(im.convert("RGB"), size, Image.LANCZOS, centering=(0.5, 0.5))


def esc(p: Path) -> str:
    return str(p.resolve()).replace("\\", "/").replace(":", "\\:")


def vignette_of(vc, width: int, height: int) -> tuple[str, str]:
    """(the vignette filter compose_video at this code root draws, where it came from)."""
    if hasattr(vc, "picture_vignette"):
        return vc.picture_vignette(width, height), "src.video_composer.picture_vignette"
    aspect, src = vignette_aspect_of(vc, width, height)
    return f"vignette=PI/4.2:aspect={aspect}", src


def burn(vc, bg: Path, ass: Path, t: float, dst: Path, vignette: str | None, lut_dir: Path) -> None:
    """compose_video's picture chain on one frame (grain seed fixed), its timestamp set to t so the subtitles filter
    draws the captions as they are at t. vignette None: the vignette is lifted (a graphic clip is on screen)."""
    grade = vc.film_grade_filter(0.0, source=LUT, out_dir=lut_dir)
    vig = f"{vignette}," if vignette else ""
    vf = (f"format=yuv420p,{grade},format=yuv420p,noise=alls=8:allf=t+u:all_seed=7,{vig}"
          f"setpts=PTS+{t:.3f}/TB,subtitles='{esc(ass)}':fontsdir='{esc(FONTS)}',scale=out_range=tv,format=rgb24")
    cmd = ["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", "30", "-i", str(bg), "-vf", vf,
           "-frames:v", "1", str(dst)]
    subprocess.run(cmd, check=True, timeout=180)


def ass_time(s: str) -> float:
    h, m, rest = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def parse_bursts(ass: Path, width: int, default_fs: int) -> list[dict]:
    """The bursts as drawn: each plate (layer 0) and the last text event inside it (every word shown)."""
    plates, texts = [], []
    for line in ass.read_text("utf-8").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        layer, start, end, style, _n, _l, _r, _v, _e, text = line[len("Dialogue: "):].split(",", 9)
        row = {"start": ass_time(start), "end": ass_time(end), "text": text}
        if style == "CapBox":
            m = re.search(r"\\pos\((-?\d+),(-?\d+)\)", text)
            row["box_x"], row["box_y"] = int(m.group(1)), int(m.group(2))
            plates.append(row)
        elif style == "CapText":
            texts.append(row)
    out = []
    for p in plates:
        inside = [t for t in texts if p["start"] - 1e-6 <= t["start"] < p["end"] - 1e-6]
        last = max(inside, key=lambda t: t["start"]) if inside else None
        # a two-line burst (freeze pass N1) draws one event a line: the last reveal's events, top line first
        lines = sorted((t for t in inside if last and abs(t["start"] - last["start"]) < 1e-6),
                       key=lambda t: int(re.search(r"\\pos\(-?\d+,(-?\d+)\)", t["text"]).group(1)))
        shown_lines = [re.sub(r"\{[^}]*\}", "", t["text"]).replace("\\N", " ").strip() for t in lines]
        shown = " ".join(shown_lines)
        fs = re.search(r"\\fs(\d+)", last["text"]) if last else None
        path = re.search(r"\\p1\}(.*?)\{", p["text"])
        ys = [int(v) for v in re.findall(r"-?\d+", path.group(1))[1::2]] if path else [0]
        out.append({"show": round(p["start"], 3), "hide": round(p["end"], 3),
                    "last_event": round(last["start"], 3) if last else None,
                    "text": shown, "lines": shown_lines, "words": len(shown.split()),
                    "ass_fs": int(fs.group(1)) if fs else default_fs, "shrunk": bool(fs),
                    "plate_w": 2 * (width // 2 - p["box_x"]), "plate_y": p["box_y"], "plate_h": max(ys),
                    "guard": "\\1a&H08&" in p["text"]})
    return out


def norm(w: str) -> str:
    return re.sub(r"[^\w]", "", w).upper()


def burst_index_of_words(bursts: list[dict]) -> list[int]:
    return [bi for bi, b in enumerate(bursts) for _ in range(b["words"])]


def name_spans(case: dict, words: list[dict]) -> list[tuple[str, int, int]]:
    """(name, first word index, last word index) for each proper name the run carries (catalogue word_index)."""
    spans = []
    if case.get("proper_names"):
        for n in case["proper_names"]:
            k = int(n["word_index"])
            spans.append((n["name"], k, k + len(n["name"].split()) - 1))
    else:
        toks = [norm(w["word"]) for w in words]
        for name in case.get("proper_names_text", []):
            key = [norm(x) for x in name.split()]
            for i in range(len(toks) - len(key) + 1):
                if toks[i:i + len(key)] == key:
                    spans.append((name, i, i + len(key) - 1))
    return join_connected_names(spans, words)


# Connectors that join two catalogue names into one name (N2: the catalogue lists 'International Union' and
# 'Geological Sciences' apart; the run says 'International Union of Geological Sciences', one name)
NAME_CONNECTORS = {"OF", "THE", "DE", "DEL", "DELLA", "DA", "DI", "DU", "VON", "VAN", "AL", "EL", "LA", "LE"}


def join_connected_names(spans: list[tuple[str, int, int]], words: list[dict]) -> list[tuple[str, int, int]]:
    """Catalogue names joined where only connectors stand between them in the run's words (the name is then listed
    once, as the run says it, and marked 'joined: catalogue parts ...' in bursts.txt)."""
    spans = sorted(spans, key=lambda s: s[1])
    out: list[tuple[str, int, int]] = []
    for name, a, b in spans:
        if out:
            pname, pa, pb = out[-1]
            between = [norm(w["word"]) for w in words[pb + 1:a]]
            if 0 < len(between) <= 2 and all(t in NAME_CONNECTORS for t in between) and not re.search(
                    r"[,;:.?!]$", words[pb]["word"]):
                text = " ".join(re.sub(r"[,;:.?!]+$", "", w["word"]) for w in words[pa:b + 1])
                out[-1] = (f"{text} (joined: catalogue parts {pname!r} + {name!r})", pa, b)
                continue
        out.append((name, a, b))
    return out


def checks(vc, case, words, bursts, fmt) -> dict:
    owner = burst_index_of_words(bursts)
    fws = {w.upper() for w in getattr(vc, "FUNCTION_WORDS", set())}
    res = {"names": [], "article_endings": [], "function_only": [], "below_floor": [], "word_count_ok":
           len(owner) == len(words), "under_half_second": [], "pause_spanning": [], "two_line": [], "one_word": []}
    # timing checks on the real words.json (P1: a pause of 0.30 s or more after punctuation inside a burst; D1: on
    # screen under 0.50 s); the new case's timings are synthetic, so it carries none
    if len(owner) == len(words) and not case.get("label"):
        k = 0
        for bi, b in enumerate(bursts):
            ws = words[k:k + b["words"]]
            k += b["words"]
            if b["hide"] - b["show"] < 0.5 - 1e-6:
                res["under_half_second"].append(f"{bi}: {b['hide'] - b['show']:.2f}s {b['text']}")
            for a, c in zip(ws, ws[1:]):
                gap = float(c["start"]) - (float(a["start"]) + float(a.get("dur") or 0.0))
                if gap >= 0.30 - 1e-9 and re.search(r"[,;:.?!]$", a["word"]):
                    res["pause_spanning"].append(f"{bi}: {gap:.2f}s after {a['word']!r} in {b['text']}")
    for bi, b in enumerate(bursts):
        if len(b.get("lines") or []) == 2:
            res["two_line"].append(f"{bi}: {' / '.join(b['lines'])}")
    for name, a, b in name_spans(case, words):
        if b >= len(owner):
            continue
        held = owner[a] == owner[b]
        parts = " | ".join(bursts[i]["text"] for i in sorted(set(owner[a:b + 1])))
        res["names"].append({"name": name, "whole": held, "bursts": parts})
    font_size = int(os.environ.get("FONT_SIZE", "80"))
    for bi, b in enumerate(bursts):
        toks = [norm(x) for x in b["text"].split()]
        if toks and toks[-1] in ARTICLES:
            res["article_endings"].append(f"{bi}: {b['text']}")
        if toks and fws and all(t in fws for t in toks):
            res["function_only"].append(f"{bi}: {b['text']}")
        if len(toks) == 1 and len(bursts) > 1 and fmt == "9:16":
            res["one_word"].append(f"{bi}: {b['text']}")
        if fmt == "9:16":
            px = round(b["ass_fs"] * font_size / vc.FONT_SIZE_ASS)
            b["px"] = px
            if px < BOND_FLOOR_PX:
                res["below_floor"].append(f"{bi}: {px}px {b['text']}")
    return res


def pick_samples(case, words, spans) -> dict[str, int]:
    """Word indices to sample, independent of the code (before and current sample the same words)."""
    picks: dict[str, int] = {"first": 0}
    for name, a, b in spans:
        if b < len(words):
            first = unicodedata.normalize("NFKD", norm(name.split()[0])).encode("ascii", "ignore").decode().lower()
            picks.setdefault(f"name-{first}-{a}", b)
    toks = [w["word"] for w in words]
    for lw in (case.get("longest_words") or [])[:2]:
        for i, w in enumerate(toks):
            if re.sub(r"[^\w\-]", "", w) == lw["word"]:
                picks.setdefault(f"longword-{i}", i)
                break
    # review round 4: the first possessive name before a lower-case noun (gol SHAH'S DEATH, voynich ZIPF'S LAW)
    for i, w in enumerate(toks[:-1]):
        if w[:1].isupper() and re.search(r"['’]s$", w) and toks[i + 1][:1].islower():
            picks.setdefault("possessive", i)
            break
    for frac, tag in ((0.5, "mid"), (0.85, "late")):
        k = int(len(words) * frac)
        picks.setdefault(tag, k)
    return {f"w{k:03d}_{tag.split('-')[0] if tag.startswith('longword') else tag}": k for tag, k in picks.items()}


def save_outputs(png: Path, out: Path, sid: str, fmt: str, plate_y: int | None, mark: str | None = None) -> None:
    from PIL import Image, ImageDraw
    im = Image.open(png).convert("RGB")
    if mark:   # a new item is labelled on the image itself
        d = ImageDraw.Draw(im)
        f = label_font(26)
        d.rectangle((0, 0, im.width, 44), fill=(16, 16, 16))
        d.text((12, 8), mark.upper()[:120], fill=(255, 90, 90), font=f)
    w, h = im.size
    im.save(out / f"{sid}_full.jpg", quality=88, optimize=True)
    im.resize((w // 2, h // 2), Image.LANCZOS).save(out / f"{sid}_half.jpg", quality=88, optimize=True)
    band = 520 if fmt == "9:16" else 300
    cy = (plate_y + 70) if plate_y is not None else h - band
    top = max(0, min(h - band, cy - band // 2))
    crop = im.crop((0, top, w, top + band))
    if mark:
        d = ImageDraw.Draw(crop)
        d.rectangle((0, 0, w, 44), fill=(16, 16, 16))
        d.text((12, 8), mark.upper()[:120], fill=(255, 90, 90), font=label_font(26))
    crop.save(out / f"{sid}.png", optimize=True)
    png.unlink()


def corner_minima(png: Path, n: int = 96) -> list[int]:
    """The minimum luma (Rec. 601, 0-255) in each n x n corner of a frame: TL, TR, BL, BR (V1 acceptance d)."""
    from PIL import Image
    im = Image.open(png).convert("L")
    w, h = im.size
    return [min(im.crop(box).getdata()) for box in ((0, 0, n, n), (w - n, 0, w, n), (0, h - n, n, h),
                                                      (w - n, h - n, w, h))]


def corner_ratios(png: Path, plain: Path, n: int = 96) -> list[float]:
    """Each n x n corner's mean luma with the vignette over the same frame's without it: TL, TR, BL, BR. The real
    photographs hold pure-black pixels of their own (a night sky, a shadow), so the corner minimum alone cannot tell
    the vignette's black from the picture's."""
    from PIL import Image, ImageStat
    a, b = Image.open(png).convert("L"), Image.open(plain).convert("L")
    w, h = a.size
    out = []
    for box in ((0, 0, n, n), (w - n, 0, w, n), (0, h - n, n, h), (w - n, h - n, w, h)):
        ma, mb = ImageStat.Stat(a.crop(box)).mean[0], ImageStat.Stat(b.crop(box)).mean[0]
        out.append(round(ma / mb, 3) if mb > 1 else 1.0)
    return out


def corner_crop(png: Path, dst: Path, n: int = 240) -> None:
    """The four n x n corners of a 16:9 frame side by side at full resolution (the V1 corner crop)."""
    from PIL import Image
    im = Image.open(png).convert("RGB")
    w, h = im.size
    sheet = Image.new("RGB", (4 * n + 30, n), (16, 16, 16))
    for k, box in enumerate(((0, 0, n, n), (w - n, 0, w, n), (0, h - n, n, h), (w - n, h - n, w, h))):
        sheet.paste(im.crop(box), (k * (n + 10), 0))
    sheet.save(dst, quality=90, optimize=True)


def label_font(size: int):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(str(FONTS / "IBMPlexMono-SemiBold.ttf"), size)
    except Exception:
        return ImageFont.load_default()


def contact_sheet(out: Path, ids: list[str], dst: Path, fmt: str, title: str) -> None:
    from PIL import Image, ImageDraw
    cols = 4 if fmt == "9:16" else 3
    tw = 300 if fmt == "9:16" else 480
    th = tw * 16 // 9 if fmt == "9:16" else tw * 9 // 16
    rows = (len(ids) + cols - 1) // cols
    head = 40
    sheet = Image.new("RGB", (cols * tw, head + rows * (th + 24)), (20, 20, 20))
    d = ImageDraw.Draw(sheet)
    d.text((8, 8), title, fill=(240, 200, 90), font=label_font(18))
    for i, sid in enumerate(ids):
        im = Image.open(out / f"{sid}_half.jpg").resize((tw, th), Image.LANCZOS)
        x, y = (i % cols) * tw, head + (i // cols) * (th + 24)
        sheet.paste(im, (x, y + 24))
        d.text((x + 6, y + 5), sid.split("_", 2)[-1] if len(sid) > 40 else sid, fill=(235, 235, 235),
               font=label_font(13))
    sheet.save(dst, quality=85, optimize=True)


def cases(fx) -> list[dict]:
    out = []
    for c in fx.load("captions"):
        c = dict(c)
        c["words_list"] = fx.words(c)
        c["short"] = c["topic"]
        out.append(c)
    # The new case, from the real fact-sheet statement
    sheet = fx.json_of(NEW_CASE["sheet"])
    fact = next(f for f in sheet.get("facts", []) if f.get("id") == NEW_CASE["fact_id"])
    rate = fx.json_of(NEW_CASE["run"] + "/words.json")
    n = dict(NEW_CASE)
    n["text"] = fact["statement"]
    n["disputed"] = fact.get("disputed")
    n["words_list"] = fitted_words(fact["statement"], rate)
    n["emphasis_words"] = []
    n["short"] = "kailasa_temple_monolith_NEW"
    n["photo_path"] = fx.path(NEW_CASE["photo"])
    n["timeline"] = [{"clip": NEW_CASE["photo"], "photo": NEW_CASE["photo"], "duration_s": None, "start_s_est": 0.0}]
    n["duration_s"] = n["words_list"][-1]["start"] + n["words_list"][-1]["dur"]
    out.append(n)
    return out


def render(code_root: Path, out: Path) -> dict:
    vc = load_code(code_root)
    fx = load_fixtures()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    work = out / "_work"
    work.mkdir()
    comp = vc.VideoComposer(FONTS)
    params = inspect.signature(comp.build_subtitles).parameters
    summary = {"code_root": str(code_root), "pinned_settings": {k: os.environ[k] for k in PINNED},
               "font_size_px": vc.FONT_SIZE, "fonts_dir": str(FONTS), "lut": str(LUT),
               "picture_chain": "film_grade_filter(0.0) -> noise alls=8 (seed 7) -> picture_vignette(width, "
                                "height) as compose_video draws it (lifted over graphic_* clips) -> subtitles "
                                "(libass, fonts/)",
               "inputs_rule": "fixtures.load('captions'): real words.json, emphasis_words, scene timeline; "
                              "one case labelled new", "cases": {}}
    all_txt = []
    for case in cases(fx):
        fmt = case["format"]
        W, H = FORMATS[fmt]
        words = case["words_list"]
        aspect, aspect_src = vignette_of(vc, W, H)
        starts = scene_starts(case)
        tl = case["timeline"]
        clips = [fx.path(s["clip"]) for s in tl]
        vo_dur = float(case.get("duration_s") or (words[-1]["start"] + words[-1]["dur"]))
        durations = [b - a for a, b in zip(starts, starts[1:])] + [max(vo_dur - starts[-1], 0.1)]
        kw = {"emphasis": list(case.get("emphasis_words") or []), "width": W, "height": H}
        if "graphic_intervals" in params and hasattr(vc, "caption_graphic_intervals"):
            kw["graphic_intervals"] = vc.caption_graphic_intervals(clips, durations)
        if "graphic_clips" in params and hasattr(vc, "caption_graphic_clips"):   # G1, as compose_video passes it
            kw["graphic_clips"] = vc.caption_graphic_clips(clips, durations)
        if "reconstruction_intervals" in params and hasattr(vc, "reconstruction_badge_intervals"):
            kw["reconstruction_intervals"] = vc.reconstruction_badge_intervals(clips, durations, vo_dur)
        if H > W:
            if "first_frame_whole" in params:
                kw["first_frame_whole"] = vc.CAPTION_FIRST_FRAME_WHOLE_S
            if "cuts" in params and hasattr(vc, "caption_cut_times"):
                kw["cuts"] = vc.caption_cut_times(durations)
        tag = f"{case['short']}_{fmt.replace(':', 'x')}"
        ass = out / f"{tag}.ass"
        comp.build_subtitles(words, ass, **kw)
        default_fs = vc.FONT_SIZE_ASS if H > W else vc.ass_font_size(
            vc.CAPTION_WIDE_FONT, round(vc.CAPTION_WIDE_SIZE * H / 1080), 700)
        bursts = parse_bursts(ass, W, default_fs)
        chk = checks(vc, case, words, bursts, fmt)
        label = case.get("label")
        lines = [f"=== {Path(str(case['run'])).name} | {case['topic']} [{fmt}] ===",
                 f"why: {case['why']}" + (f"\n{label.upper()}" if label else ""),
                 f"{len(words)} words, {len(bursts)} bursts, emphasis {kw['emphasis']}; vignette {aspect} "
                 f"({aspect_src})"]
        if case.get("text"):
            lines.append(f"text (fact sheet {case['fact_id']}, disputed={case.get('disputed')}): {case['text']}")
        if not chk["word_count_ok"]:
            lines.append(f"!! parsed {sum(b['words'] for b in bursts)} words from the ASS against {len(words)}")
        for bi, b in enumerate(bursts):
            size = f"{b['px']:3d}px" if "px" in b else f"fs{b['ass_fs']:3d}"
            text = " / ".join(b["lines"]) if len(b.get("lines") or []) == 2 else b["text"]
            lines.append(f"{bi:3d} {b['show']:7.2f}-{b['hide']:7.2f}  {size}{'*' if b['shrunk'] else ' '} "
                         f"plate {b['plate_w']:4d}x{b['plate_h']:3d}{' G' if b.get('guard') else '  '} {text}")
        lines.append("  names:")
        lines += [f"    {'WHOLE' if n['whole'] else 'SPLIT'}  {n['name']:28s} {n['bursts']}" for n in chk["names"]]
        lines.append(f"  bursts ending on an article: {chk['article_endings'] or 'none'}")
        lines.append(f"  function-word-only bursts: {chk['function_only'] or 'none'}")
        if fmt == "9:16":
            lines.append(f"  one-word bursts: {chk['one_word'] or 'none'}")
        if fmt == "9:16":
            lines.append(f"  bursts below the {BOND_FLOOR_PX} px floor: {chk['below_floor'] or 'none'}")
            lines.append(f"  two-line bursts: {chk['two_line'] or 'none'}")
        if label:
            lines.append("  timing checks: none (synthetic timings, layout only)")
        else:
            lines.append(f"  bursts on screen under 0.50 s: {chk['under_half_second'] or 'none'}")
            lines.append(f"  bursts spanning a pause of 0.30 s or more after punctuation: "
                         f"{chk['pause_spanning'] or 'none'}")
        sidecar = []
        if fmt == "16:9" and hasattr(vc, "partition_sidecar_cues"):
            sidecar = [{"start": s, "end": e, "text": t} for s, e, t in vc.partition_sidecar_cues(words)]
            lines.append(f"  sidecar cues (.srt/.vtt): {len(sidecar)}")
            lines += [f"    {c['start']:7.2f}-{c['end']:7.2f}  {c['text']!r}" for c in sidecar]
        lines.append("")
        (out / f"bursts_{tag}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        all_txt += lines
        print("\n".join(lines), flush=True)

        owner = burst_index_of_words(bursts)
        samples = []
        for sname, k in pick_samples(case, words, name_spans(case, words)).items():
            if k >= len(owner):
                continue
            b = bursts[owner[k]]
            t = b["last_event"] if b["last_event"] is not None else b["show"]
            t = min(t + 0.15, (t + b["hide"]) / 2)
            t_bg = float(words[k]["start"])          # background at the word's own time: same in both runs
            si = scene_index(starts, t_bg)
            graphic = clips[si].name.startswith("graphic_")
            src = case["photo_path"] if case.get("photo_path") else fx.background_at(case, t_bg)
            bg = work / f"bg_{tag}_{k}.png"
            cover(Path(src), (W, H)).save(bg)
            sid = f"{tag}_{sname}"
            png = work / f"{sid}.png"
            burn(vc, bg, ass, t, png, None if graphic else aspect, work / "luts")
            corners = corner_minima(png) if fmt == "16:9" else None
            ratios = None
            if fmt == "16:9" and not graphic:   # the same frame without the vignette: what the corners keep
                plain = work / f"{sid}_novig.png"
                burn(vc, bg, ass, t, plain, None, work / "luts")
                ratios = corner_ratios(png, plain)
                plain.unlink()
            if fmt == "16:9" and not samples:
                corner_crop(png, out / f"{sid}_corners.jpg")
            save_outputs(png, out, sid, fmt, b["plate_y"])
            samples.append({"id": sid, "word_index": k, "word": words[k]["word"], "t_caption": round(t, 3),
                            "t_background": round(t_bg, 3), "background": str(src), "scene": si,
                            "vignette": "lifted (graphic clip)" if graphic else aspect,
                            "burst": b["text"], "corner_min_luma": corners, "corner_mean_ratio": ratios})
        contact_sheet(out, [s["id"] for s in samples], out / f"sheet_{tag}.jpg", fmt,
                      f"{Path(str(case['run'])).name} | {fmt}" + (f" | {label}" if label else ""))
        summary["cases"][tag] = {
            "run": str(case["run"]), "topic": case["topic"], "format": fmt, "why": case["why"], "label": label,
            "words": str(case["words"]) if not label else "fitted (see label)",
            "text": case.get("text"), "emphasis": kw["emphasis"], "vignette_aspect": aspect,
            "vignette_source": aspect_src, "cuts": kw.get("cuts"), "graphic_intervals": kw.get("graphic_intervals"),
            "caption_graphic_overlap": list(getattr(comp, "last_caption_graphic_overlap", []) or []),
            "timeline_complete": case.get("timeline_complete", True), "bursts": bursts, "checks": chk,
            "samples": samples, "sidecar": sidecar}
    (out / "bursts.txt").write_text("\n".join(all_txt) + "\n", encoding="utf-8", newline="\n")
    shutil.rmtree(work, ignore_errors=True)
    write_readme(out, summary)
    (out / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8",
                                      newline="\n")
    return summary


def write_readme(out: Path, summary: dict) -> None:
    """README.md beside the renders: how the picture was made and, per case, the counts the tests check."""
    rows = ["| case | format | bursts | names WHOLE / SPLIT | ending on an article | function-word-only "
            "| one-word | under 66 px | under 0.50 s | spanning a pause | two-line | G1 plates |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    details = []
    for tag, c in summary["cases"].items():
        chk = c["checks"]
        whole = sum(1 for n in chk["names"] if n["whole"])
        split = len(chk["names"]) - whole
        timing = c.get("label")
        rows.append(f"| {tag}{' (new, synthetic timings)' if timing else ''} | {c['format']} | {len(c['bursts'])} "
                    f"| {whole} / {split} | {len(chk['article_endings'])} | {len(chk['function_only'])} "
                    f"| {len(chk.get('one_word', [])) if c['format'] == '9:16' else 'n/a'} "
                    f"| {len(chk['below_floor']) if c['format'] == '9:16' else 'n/a'} "
                    f"| {'n/a' if timing else len(chk['under_half_second'])} "
                    f"| {'n/a' if timing else len(chk['pause_spanning'])} | {len(chk.get('two_line', []))} "
                    f"| {len(c.get('caption_graphic_overlap') or [])} |")
        listed = [("one-word", chk.get("one_word") or []), ("function-word-only", chk["function_only"]),
                  ("under 0.50 s", [] if timing else chk["under_half_second"]),
                  ("spanning a pause", [] if timing else chk["pause_spanning"]),
                  ("two-line", chk.get("two_line") or [])]
        details.append(f"- {tag}: " + "; ".join(f"{k}: {', '.join(v)}" for k, v in listed if v) if any(
            v for _, v in listed) else f"- {tag}: none")
    corners = [(s["id"], s["corner_min_luma"], s.get("corner_mean_ratio")) for c in summary["cases"].values()
               for s in c["samples"] if s.get("corner_min_luma") is not None]
    text = [
        "# Burned-in captions, freeze pass round 2 (2026-11-04): after",
        "",
        "Rendered by `render_evidence.py` from the five real runs of the freeze-panel catalogue "
        "(`fixtures.load('captions')`: each run's own words.json, emphasis_words and scene timeline) through "
        "`VideoComposer.build_subtitles`, burned over the run's own scene background at the burst's time.",
        "",
        "Picture chain: `film_grade_filter(0.0)`, grain (`noise=alls=8`, seed 7), then the lens vignette exactly as "
        "compose_video draws it: `src.video_composer.picture_vignette(width, height)` is imported, giving "
        f"`{summary['cases'].get('richat_structure_sahara_eye_16x9', {}).get('vignette_aspect', '?')}` at 1920x1080 "
        f"and `{next((c['vignette_aspect'] for c in summary['cases'].values() if c['format'] == '9:16'), '?')}` at "
        "1080x1920 (lifted over graphic clips, as graphic_vignette_enable does), then libass with the bundled "
        "fonts (Anton 80 px for 9:16; static Montserrat Bold for 16:9).",
        "",
        "Round 4 (review fixes, 2026-11-04): the phrase builder ranks the hard defects (a function-word burst, a "
        "mid-sentence end on a function word) first, then one soft cost: a lone content word costs 3, a pre-modifier, "
        "possessive or attributive end 2, a one-word lead-in 2, a proper-modifier or open noun phrase end 1 (round 3 "
        "ranked every M1 end above a lone word, so 9 lone bursts at HEAD became 38). A possessive name takes the noun "
        "it owns (UPON MOHAMMAD ADIL / SHAH'S DEATH, on two lines; round 3: UPON | MOHAMMAD ADIL SHAH'S | DEATH,), a "
        "two-line name takes its lead-in on line 1, a lower-case head after a connector names its own part (KING "
        "KRISHNA I | OF THE RASHTRAKUTA DYNASTY), and a figure's unit is a bond (ESTIMATED 200,000 TONS), so "
        "tests/test_loop_seam_and_captions_2026_11_03.py passes unchanged. Each remaining one-word, function-word-only "
        "and under-0.50 s burst is listed below; the rule that forces each is in the test file (LONE_WORDS, "
        "SPEECH_LIMITED, GATE_ALLOWED).",
        "",
        "Round 3 (review fixes, 2026-11-04): the names column joins catalogue names that only connectors separate in "
        "the run's words (fixtures.json lists 'International Union' and 'Geological Sciences' apart; the run says "
        "'International Union of Geological Sciences', listed once and marked `joined`). The caption code ends no "
        "burst on an attributive -ing word (AN ACTIVE HIDING | PLACE, now TUNNELS AS AN ACTIVE | HIDING PLACE). "
        "Open for the operator: V1 (d) as written (minimum corner luma > 0) is not met where the photograph itself "
        "is black (the ratio below is the proposed measure); M1 (b) OF A PROMINENT (gol) and the width-forced gate "
        "pairs; G1 (b) the two-line plate's rise above the graphics floor (y 1058).",
        "",
        "The Kailasa case is new: the fact-sheet statement kailasa-commencement-patron with timings fitted to the "
        "Kailasa run's voice rate. Its timings are synthetic, so it is layout evidence only and carries no timing "
        "counts.",
        "",
        "Per case (the counts the tests in tests/test_freeze_captions_2026_11_04.py check):",
        "",
        *rows,
        "",
        "The bursts behind each count:",
        "",
        *details,
        "",
        "16:9 corners (each 96 px corner, TL TR BL BR): the minimum luma, and the mean luma with the vignette over the "
        "same frame's without it. The real photographs hold pure-black pixels of their own (a night sky, a shadow), so "
        "a minimum of 0 there is the picture's; the ratio is the vignette's own darkening (the chain before V1 took "
        "the corners to 0).",
        "",
        *[f"- {sid}: min {vals}, mean ratio {ratio if ratio is not None else 'n/a (graphic: vignette lifted)'}"
          for sid, vals, ratio in corners],
        "",
        "Files: `bursts_<case>.txt` / `bursts.txt` (every burst: time, size, plate, text; two-line bursts as "
        "`line 1 / line 2`; G marks a G1 opaque plate), `summary.json` (all of it as data), `<sample>_full.jpg`, "
        "`<sample>_half.jpg`, `<sample>.png` (the caption band), `sheet_<case>.jpg`, and "
        "`<first 16:9 sample>_corners.jpg` (the four corners at full resolution). Before | after pairs are in "
        "`../compare/<sample>_before_after.jpg`.",
        "",
    ]
    (out / "README.md").write_text("\n".join(text), encoding="utf-8", newline="\n")


def compare(before: Path, current: Path, dst: Path) -> list[Path]:
    """before | current side by side (half size each), labelled with run, topic and any new label."""
    from PIL import Image, ImageDraw
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    sb = json.loads((before / "summary.json").read_text("utf-8"))
    sc = json.loads((current / "summary.json").read_text("utf-8"))
    f1, f2 = label_font(24), label_font(17)
    made = []
    for tag, cc in sc["cases"].items():
        cb = sb["cases"].get(tag)
        if not cb:
            continue
        a_txt = (before / f"bursts_{tag}.txt").read_text("utf-8").splitlines()
        b_txt = (current / f"bursts_{tag}.txt").read_text("utf-8").splitlines()
        diff = difflib.unified_diff(a_txt, b_txt, "before (HEAD)", "after (working tree)", lineterm="", n=1)
        (dst / f"bursts_diff_{tag}.txt").write_text("\n".join(diff) + "\n", encoding="utf-8", newline="\n")
        byid = {s["id"]: s for s in cb["samples"]}
        for s in cc["samples"]:
            if s["id"] not in byid:
                continue
            a = Image.open(before / f"{s['id']}_half.jpg").convert("RGB")
            b = Image.open(current / f"{s['id']}_half.jpg").convert("RGB")
            label = cc.get("label") or s.get("label")
            gap, bar = 12, 150 if label else 120
            im = Image.new("RGB", (a.width + b.width + gap, max(a.height, b.height) + bar), (16, 16, 16))
            im.paste(a, (0, bar))
            im.paste(b, (a.width + gap, bar))
            d = ImageDraw.Draw(im)
            d.text((10, 6), f"{Path(cc['run']).name} | {cc['topic']} | {cc['format']} | word {s['word_index']} "
                            f"{s['word']!r}", fill=(235, 235, 235), font=f2)
            if label:
                d.text((10, 30), label.upper()[:110], fill=(255, 90, 90), font=f2)
            y = 34 + (26 if label else 0)
            d.text((10, y), "BEFORE (HEAD " + os.environ.get("EVIDENCE_BEFORE_LABEL", "0036b18") + ")", fill=(235, 235, 235), font=f1)
            d.text((a.width + gap + 10, y), "AFTER (working tree)", fill=(240, 200, 90), font=f1)
            d.text((10, y + 32), byid[s["id"]]["burst"][:60], fill=(200, 200, 200), font=f2)
            d.text((a.width + gap + 10, y + 32), s["burst"][:60], fill=(240, 200, 90), font=f2)
            p = dst / f"{s['id']}_before_after.jpg"
            q = 85
            im.save(p, quality=q, optimize=True)
            while p.stat().st_size > 1_900_000 and q > 50:
                q -= 10
                im.save(p, quality=q, optimize=True)
            made.append(p)
    return made


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--before", type=Path)
    ap.add_argument("--compare", type=Path)
    a = ap.parse_args()
    render(a.code_root, a.out)
    if a.before and a.compare:
        print(f"compare: {len(compare(a.before, a.out, a.compare))} images -> {a.compare}")


if __name__ == "__main__":
    main()
