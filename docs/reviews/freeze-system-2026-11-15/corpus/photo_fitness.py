"""Corpus proof of the PHOTO classes (design-freeze cycle 5; ledger PHOTO-01..08): is every photograph the dev runs
place fit for the window it is put in?

    python photo_fitness.py <dev checkout> [--rule tip|base] [--json OUT] [--cache DIR] [--seed-cache DIR]
                            [--records DIR] [--distribution] [--quiet]

Measures every placement of every dev run that has the surface's inputs, with the ONE measure the surfaces use
(src/photo_fitness.py of the tree this script lies in), and prints a table (run, surface, photo, measure, verdict).
Exit 1 when a placement is unfit and neither fixed nor recorded (--rule tip); the base rule exits 1 on any unfit
placement, which is the count before the fix.

The placements:
- POOL: every photograph of a run's pool (carousel_photos.json's stills, else assets/<topic>) in every photo window
  of every surface: the carousel's cover / immersive / debate / split / comparison windows on both copies, the Story
  frame, the Reel cover (and its grid tile, which is its centre square), the YouTube thumbnail's and every 16:9
  ground's window, and every 9:16 ground's (hook, chapter and myth cards, figure grounds: typography.cover_crop).
- DECK: the run's recorded slides and Story frames (still_layout.json recipes: the photograph each slide was drawn
  with), and for the runs that kept their match (carousel_photos.json) the deck the photo guard chooses.
- FRAME: the last frame of every master (the end screen's, end card's and bridge's ground).
- PAIR: every then-and-now pair (graphics/then_k x now_k_j) of a run that drew the comparison, both formats:
  registered (one size: then_now.register) or not. A pair that is not registered is unfit under both rules ("pair");
  the tip records it.

--rule base reproduces the croppers as they were at integration 4 (57358665): the centred fit, the deck window rule
(hero_window.deck_window, unchanged), lane D cycle 4's frame_window (copied below, it left the tree in cycle 5), the
Reel cover's immersive / contained layouts and the shared then-and-now window. --rule tip asks the tree's own pickers
and croppers. Both are judged by the same measure, so the two counts differ only by what the croppers do.

"Recorded" is READ, not assumed (review of cycle 5, D2): a POOL row is recorded when the record the tree's own
call returns for it (photo_fitness.place's, the hook ground's drawn record, the cover's photo_fit, a blurred
ground's photo_fitness.ground_fit, which graphics.record_contrast writes beside the clip) says it is unfit and names
its problems. A DECK row, with --records DIR (the slides and Story frames
redrawn by the tree from their kept recipes: DIR/<run>/<carousel|carousel_fb|stories>/still_layout.json and
stories/layout.json), is the window record written on disk for that slide; without --records the recorded slides are
measured as the tree would draw them and counted as NOT recorded.

Cheap by construction: one analysis per photograph at 256 px (kept in --cache; --seed-cache rebuilds the maps from an
earlier cache's 256 px pictures, which is what analysing the file gives), layout calls and image statistics; no
render. No network, no model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import subprocess
import sys
import tempfile
import warnings
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve()
TREE = HERE.parents[4]
sys.path.insert(0, str(TREE))
warnings.simplefilter("ignore")

from PIL import Image  # noqa: E402

from src import photo_fitness as pf  # noqa: E402

Image.MAX_IMAGE_PIXELS = None
MEASURES = ("matte", "upscale", "subject", "flat", "protected")


# ---------------------------------------------------------------- analyses, kept per file
class Photos:
    def __init__(self, cache: Path | None, seed: Path | None = None):
        self.cache = cache
        self.seed = seed
        self.mem: dict[str, tuple] = {}
        if cache:
            cache.mkdir(parents=True, exist_ok=True)

    def get(self, path: Path):
        """(analysis, clean size) of a photograph, or None when it cannot be read."""
        key = str(path)
        if key in self.mem:
            return self.mem[key]
        try:
            st = path.stat()
        except OSError:
            return None
        tag = hashlib.sha256(f"{path}|{st.st_size}|{st.st_mtime_ns}|{pf.ANALYSIS}|{pf.MATTE_LUMA}|13".encode()).hexdigest()[:24]
        f = self.cache / f"{tag}.pkl" if self.cache else None
        a = None
        if f is not None and f.is_file():
            try:
                a = pickle.loads(f.read_bytes())
            except Exception:
                a = None
        if a is None and self.seed is not None:   # an earlier cache's 256 px picture: the maps made again from it
            old = hashlib.sha256(f"{path}|{st.st_size}|{st.st_mtime_ns}|{pf.ANALYSIS}|{pf.MATTE_LUMA}|9".encode()
                                 ).hexdigest()[:24]
            g = self.seed / f"{old}.pkl"
            if g.is_file():
                try:
                    o = pickle.loads(g.read_bytes())
                    a = pf.analyse_maps(o.rgb, tuple(o.size), o.focus, False, o.thumb)
                except Exception:
                    a = None
        if a is None:
            try:
                clean, kept = pf.load(path)
                a = pf.analyse(clean, pf._reviewed(path, clean, kept))
            except Exception as e:  # noqa: BLE001
                print(f"unreadable {path.name}: {e}", file=sys.stderr)
                self.mem[key] = None
                return None
            if f is not None:
                a._sat.clear()
                f.write_bytes(pickle.dumps(a))
        self.mem[key] = a
        k = pf._key(path)   # the tree's own pickers find the same analysis (no second read of the file)
        if k:
            pf._cache[k] = ((0, 0) + tuple(a.size), a)
        return a


# ---------------------------------------------------------------- the base rule's croppers (integration 4)
FRAME_SHOWN, FRAME_CLEAR, FRAME_ZOOM_MAX, FRAME_ZOOM_STEP, FRAME_STEPS = 0.002, 0.0005, 1.25, 0.025, 9


def legacy_frame_window(a: pf.Analysis, size, centering):
    """Lane D cycle 4's frame_window (src/multi_format.py at 57358665) on the analysis' frame mask: the box it moved
    the window to (the least zoom up to 1.25 and the nearest position that shows no frame, holding the centre), or
    None when the window shows no frame or none clears it."""
    import numpy as np
    W, H = a.size
    asked = pf.fit_box(a.size, size, centering)
    if a.share("matte", asked) < FRAME_SHOWN:
        return None
    bw, bh = asked[2] - asked[0], asked[3] - asked[1]
    mid = ((asked[0] + asked[2]) / 2, (asked[1] + asked[3]) / 2)
    least = min(bw, float(size[0]))
    z = 1.0
    while z <= FRAME_ZOOM_MAX + 1e-9:
        w, h = bw / z, bh / z
        if w + 1e-6 < least:
            break
        found = []
        for x in np.linspace(max(0.0, W / 2 - w), min(W - w, W / 2), FRAME_STEPS):
            for y in np.linspace(max(0.0, H / 2 - h), min(H - h, H / 2), FRAME_STEPS):
                box = (float(x), float(y), float(x + w), float(y + h))
                if a.share("matte", box) <= FRAME_CLEAR:
                    found.append((abs(x + w / 2 - mid[0]) + abs(y + h / 2 - mid[1]), box))
        if found:
            return min(found, key=lambda f: f[0])[1]
        z += FRAME_ZOOM_STEP
    return None


def deck_centering(a: pf.Analysis, window: pf.Window, k: int = 0):
    """The deck window rule's centering for the k-th showing (hero_window.deck_window, on the analysis' thumbnail)."""
    from src.hero_window import deck_window
    if not (k or window.protected) or a.thumb is None:
        return (0.5, 0.5)
    win = deck_window(a.thumb.size, window.size, k, tuple(window.protected[0]) if window.protected else None, a.thumb)
    return tuple(win["centering"])


def judged(a: pf.Analysis, box, window: pf.Window) -> dict:
    plain = pf._candidates(a, window, 1.0)
    view = window.view_box()
    best = max([a.total("salience", pf._sub(b, window.size, view)) for b in [box, *plain]])
    rec = pf.measure(a, tuple(box), window, best)
    rec["problems"] = pf.problems(rec, window)
    return rec


def crop_base(a: pf.Analysis, window: pf.Window, kind: str, k: int = 0) -> dict:
    """What integration 4 drew for (photograph, window): its crop, judged."""
    cen = deck_centering(a, window, k) if kind in ("carousel", "story") else (0.5, 0.5)
    if kind == "hook" and max(window.size[0] / a.size[0], window.size[1] / a.size[1]) > pf.UPSCALE_MAX:
        return {"problems": [], "outcome": "refused"}          # the hook card's refusal (since QF-39)
    box = pf.fit_box(a.size, window.size, cen)
    moved = None
    if kind == "carousel":
        moved = legacy_frame_window(a, window.size, cen)
    rec = judged(a, moved or box, window)
    rec["outcome"] = "fit" if not rec["problems"] else "unfit"
    return rec


def crop_tip(a: pf.Analysis, window: pf.Window, kind: str, k: int = 0) -> dict:
    """What the tree draws: the surface's asked crop held to photo_fitness.place, as _fill / story_window /
    typography.cover_crop call it."""
    cen = deck_centering(a, window, k) if kind in ("carousel", "story") else (0.5, 0.5)
    if kind == "hook":     # design_cards._plan_hook: refused past the enlargement limit, else placed (not with a focus)
        if max(window.size[0] / a.size[0], window.size[1] / a.size[1]) > pf.UPSCALE_MAX:
            return {"problems": [], "outcome": "refused", "upscale": round(max(window.size[0] / a.size[0],
                                                                                  window.size[1] / a.size[1]), 3)}
        rec = pf.place(None, window, cen, analysis=a, fixed=a.focus is not None)
        if not (rec["moved"] and rec["fit"]):   # design_cards draws cover_crop's window then, and records THAT box
            frame_only = pf.Window(window.size, checks=("matte",), name=window.name)
            got = pf.place(None, frame_only, cen, analysis=a) if a.matte_share > 0 else None
            box = got["box"] if got and got["fit"] and got["moved"] else pf.fit_box(a.size, window.size, cen)
            if any(abs(x - y) >= 1.0 for x, y in zip(box, rec["box"])):
                m = pf.measure(a, tuple(box), window)
                if a.focus is not None and hasattr(pf, "_subject_needs"):   # as photo_fitness.drawn_record (cycle 6)
                    m.update(pf._subject_needs(a, window, pf.fit_box(a.size, window.size, cen)))
                m["problems"] = pf.problems(m, window)
                if a.focus is not None and hasattr(pf, "_subject_needs") and not m.get("hold_short"):
                    m["problems"] = [p for p in m["problems"] if p != "subject"]
                rec = {**m, "fit": not m["problems"], "moved": True, "window": window.name, "drawn": "cover crop"}
    elif kind == "ground":   # typography.cover_crop: its own fit record (photo_fitness.ground_fit), as written
        rec = pf.ground_fit(a, window.size, cen)
    else:
        rec = pf.place(None, window, cen, analysis=a)
    rec["outcome"] = ("fit" if not rec["problems"] and not rec.get("moved") else
                      "crop" if not rec["problems"] else "unfit")
    if rec.get("moved") and isinstance(rec.get("base"), dict) and a.matte_share > 0:
        rec["excess"] = excess_move(a, rec["base"]["box"], rec["box"])
        rec["cause"] = "frame" if rec.get("basis") == "minimal frame move" else ",".join(
            p for p in rec["base"].get("problems", ()) if p not in pf.SOFT) or "soft"
    if kind in ("carousel", "story") and rec.get("moved"):
        # a slide's or a Story frame's move is drawn only when the text guard passes it (keep_text_contrast: no
        # element under its floor, the lowest ratio not lower); that needs the drawn slide, so a POOL row is a
        # proposed move and DECK (the redrawn records, --records) is what a deck draws
        rec["outcome"] = "crop proposed" if not rec["problems"] else "unfit proposed"
    return rec


def excess_move(a: pf.Analysis, asked, box) -> float:
    """How far a moved crop's centre went beyond what clearing the frame needs, as a share of the window (the larger
    axis; third review, B7): 0 for a minimal frame move."""
    x0, y0, x1, y1 = a.content_box
    w, h = asked[2] - asked[0], asked[3] - asked[1]
    need_x = max(0.0, x0 - asked[0]) + max(0.0, asked[2] - x1)
    need_y = max(0.0, y0 - asked[1]) + max(0.0, asked[3] - y1)
    dx = abs((box[0] + box[2]) / 2 - (asked[0] + asked[2]) / 2)
    dy = abs((box[1] + box[3]) / 2 - (asked[1] + asked[3]) / 2)
    return round(max(max(0.0, dx - need_x) / w, max(0.0, dy - need_y) / h), 3)


# ---------------------------------------------------------------- the windows
def windows() -> list[tuple[str, str, pf.Window]]:
    """(surface, kind, Window) for every photo window of every surface (kind: carousel | story | ground | hook)."""
    from src.multi_format import MultiFormatGenerator as G
    out = []
    if hasattr(G, "photo_window"):
        for arch in ("cover", "immersive", "debate", "editorial_split", "comparison"):
            for fb in (False, True):
                out.append((f"{'fb' if fb else 'ig'} carousel {arch}", "carousel", G.photo_window(arch, fb)))
        out.append(("carousel comparison lower", "carousel", G.photo_window("comparison", False, second=True)))
    out.append(("story frame", "story", pf.Window((1080, 1920), name="story")))
    # the grounds under cards and data graphics are blurred or dimmed fields (typography.cover_crop): what can show
    # through is the source's own frame, so that is what is asked of them; the hook's ground is in full view
    out.append(("16:9 ground (cover_crop)", "ground", pf.Window((1920, 1080), checks=("matte",), name="wide ground")))
    out.append(("9:16 ground (cover_crop)", "ground", pf.Window((1080, 1920), checks=("matte",), name="tall ground")))
    out.append(("hook ground", "hook", pf.Window((1080, 1920), checks=("matte", "subject", "flat"),
                                                 name="hook ground")))
    return out


def cover_base(a: pf.Analysis) -> dict:
    """The Reel cover at integration 4 (cover_design._immersive / _photograph, badge style, top = COVER_TYPE_TOP): with
    a reviewed feature, the photograph at the scale that fits the feature into its band, at full width where that
    fills it: "pad" is the share of the canvas above the type that the photograph does not reach (it was filled with
    its blurred edge). Without one: the contained window over a darkened copy of the same photograph ("repeat")."""
    from src import cover_design as cd
    top = getattr(cd, "COVER_TYPE_TOP", 1180)
    sw, sh = a.size
    if a.focus is None:
        return {"layout": "contain", "problems": ["repeat"], "outcome": "unfit", "pad": 0.0}
    fx0, fy0, fx1, fy1 = a.focus
    x0, y0, x1, y1 = 0, 580, 1080, top - 100
    scale = min((x1 - x0) / (fx1 - fx0), (y1 - y0) / (fy1 - fy0))
    fill = 1080 / sw
    scale = min(scale, max(3.0, fill), 4096 / max(sw, sh))
    if (fy1 - fy0) * fill <= y1 - y0:
        scale = max(scale, fill)
    h = sh * scale
    ptop = (y0 + y1) / 2 - (fy0 + fy1) * scale / 2
    full = sw * scale >= 1080
    uncovered = max(0.0, ptop) + max(0.0, (top + 10) - (ptop + h))
    pad = round(uncovered / (top + 10), 3) if full else 1.0
    probs = (["pad"] if pad > 0.005 else []) + (["upscale"] if scale > pf.UPSCALE_MAX else [])
    return {"layout": "immersive", "pad": pad, "upscale": round(scale, 3), "problems": probs,
            "outcome": "unfit" if probs else "fit"}


def cover_tip(a: pf.Analysis) -> dict:
    """The Reel cover's ladder (cover_design.cover_windows): full bleed, photo over ink, else the contained window on
    INK (the designed alternative, recorded in lay.meta["photo_fit"])."""
    from src import cover_design as cd
    tried = []
    if a.focus is None and a.size[1] > a.size[0]:   # cover_design._filled: an unreviewed portrait stays whole
        return {"layout": "contain", "pad": 0.0, "problems": [], "outcome": "alternative", "why": ["portrait"],
                "shown": 1.0, "fit": False}
    order = cd.cover_order(None, a) if hasattr(cd, "cover_order") else cd.cover_windows()
    for mode, window in order:
        rec = pf.place(None, window, analysis=a)
        tried.append(rec["problems"])
        if rec["fit"]:
            return {"layout": mode, "pad": 0.0, "upscale": rec["upscale"], "problems": [],
                    "outcome": "fit" if mode == "full_bleed" and not rec["moved"] else
                               "crop" if mode == "full_bleed" else "alternative", "keep": rec.get("keep"),
                    "density": rec.get("density"), "flat": rec.get("flat"), "focus": rec.get("focus"),
                    "shown": rec.get("shown")}
    # the contained window on INK shows the photograph whole (shown 1.0 by construction: ImageOps.contain)
    return {"layout": "contain", "pad": 0.0, "problems": [], "outcome": "alternative", "why": tried[-1], "shown": 1.0,
            "fit": False}


def recorded_in(rec: dict) -> bool:
    """Whether the record the tree's own call returned says this placement is unfit ("fit": False, with its
    problems: the record the surface writes, a blurred ground's included)."""
    return rec.get("fit") is False


def on_disk(records: Path | None, run: str, sub: str, name: str) -> dict | None:
    """The window record a redraw by the tree wrote for one slide or Story frame (--records), or None."""
    if records is None:
        return None
    try:
        if sub == "stories":
            for e in json.loads((records / run / sub / "layout.json").read_text(encoding="utf-8")):
                if e.get("frame") == name and isinstance(e.get("window"), dict):
                    return (e["window"].get("fit") or None)
            return None
        rec = json.loads((records / run / sub / "still_layout.json").read_text(encoding="utf-8")).get(name) or {}
        wins = (rec.get("recipe") or {}).get("windows") or []
        return (wins[0].get("fit") or None) if wins else None
    except (OSError, ValueError):
        return None


def disk_outcome(rec: dict | None) -> dict | None:
    """A record read from disk with the table's outcome: fit as asked, a fit crop, or unfit (recorded)."""
    if rec is None:
        return None
    if rec.get("move_refused"):   # the text guard drew the asked window (multi_format.keep_text_contrast)
        return {**rec, "outcome": "asked (text guard)"}
    return {**rec, "outcome": "unfit" if rec.get("problems") else "crop" if rec.get("moved") else "fit"}


# ---------------------------------------------------------------- the corpus
def run_pool(dev: Path, run: Path) -> list[Path]:
    rec = run / "carousel_photos.json"
    if rec.is_file():
        try:
            stills = [Path(p) for p in json.loads(rec.read_text(encoding="utf-8"))["key"]["stills"]]
            stills = [p for p in stills if p.is_file()]
            if stills:
                return stills
        except (ValueError, KeyError):
            pass
    name = run.name[4:]
    for topic in sorted((dev / "assets").iterdir(), key=lambda p: -len(p.name)):
        if topic.is_dir() and topic.name in name:
            return sorted(p for p in topic.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png")
                          and p.stem != "contact_sheet")
    return []


def last_frame(mp4: Path, tmp: Path) -> Path | None:
    out = tmp / (hashlib.sha256(str(mp4).encode()).hexdigest()[:16] + ".png")
    if not out.is_file():
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-sseof", "-0.2", "-i", str(mp4), "-frames:v", "1",
                            "-update", "1", str(out)], capture_output=True)
        if r.returncode != 0 or not out.is_file():
            return None
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dev", type=Path)
    ap.add_argument("--rule", choices=("tip", "base"), default="tip")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--cache", type=Path, default=Path(tempfile.gettempdir()) / "rr_photo_fitness_corpus")
    ap.add_argument("--seed-cache", type=Path, help="an earlier cache whose 256 px pictures are analysed again")
    ap.add_argument("--records", type=Path, help="the tree's redraw of the recorded slides (redraw output folder)")
    ap.add_argument("--distribution", action="store_true", help="print each measure's percentiles per window")
    ap.add_argument("--frames", action="store_true", help="print every pool photograph the frame rule calls framed")
    ap.add_argument("--quiet", action="store_true", help="print the failing rows and the totals only")
    a = ap.parse_args()
    dev = a.dev.resolve()
    photos = Photos(a.cache, a.seed_cache)
    crop = crop_tip if a.rule == "tip" else crop_base
    cover = cover_tip if a.rule == "tip" else cover_base
    rows: list[dict] = []
    memo: dict[tuple, dict] = {}
    wins = windows()
    runs = sorted(p for p in (dev / "output").glob("run_*") if p.is_dir())

    def add(run, group, surface, photo, rec, recorded=False):
        rows.append({"run": run, "group": group, "surface": surface, "photo": photo,
                     "problems": list(rec.get("problems") or []), "outcome": rec.get("outcome"),
                     "recorded": bool(recorded),
                     **{k: rec.get(k) for k in ("upscale", "matte", "flat", "subject", "density", "keep", "focus",
                                                "zoom", "layout", "pad", "in_view", "registered", "extreme", "shown",
                                                "moved", "move_refused", "excess", "cause") if k in rec},
                     **({"busy": rec["protected"][0].get("busy"), "line": rec["protected"][0].get("line"),
                         "step": rec["protected"][0].get("step"),
                         "foreign": rec["protected"][0].get("foreign")} if rec.get("protected") else {})})

    for run in runs:
        pool = run_pool(dev, run)
        # ---- POOL
        for p in pool:
            an = photos.get(p)
            if an is None:
                continue
            for surface, kind, window in wins:   # a photograph several runs share is measured once
                key = (str(p), surface)
                if key not in memo:
                    memo[key] = crop(an, window, kind)
                # a blurred ground records nothing: a frame left in it is a violation, never an excused one
                add(run.name, "POOL", surface, p.name, memo[key], recorded=a.rule == "tip" and recorded_in(memo[key]))
            if (str(p), "cover") not in memo:
                memo[(str(p), "cover")] = cover(an)
            add(run.name, "POOL", "reel cover + grid", p.name, memo[(str(p), "cover")],
                recorded=a.rule == "tip" and recorded_in(memo[(str(p), "cover")]))
        # ---- DECK: the recorded slides
        from src.multi_format import MultiFormatGenerator as G
        for sub, copy in (("carousel", False), ("carousel_fb", True), ("stories", None)):
            f = run / sub / "still_layout.json"
            if not f.is_file():
                continue
            recs = json.loads(f.read_text(encoding="utf-8"))
            seen: Counter = Counter()
            for name in sorted(recs):
                r = recs[name].get("recipe") or {}
                photo = Path(str(r.get("photo") or ""))
                if not photo.is_file():
                    continue
                an = photos.get(photo)
                if an is None:
                    continue
                disk = disk_outcome(on_disk(a.records, run.name, sub, name)) if a.rule == "tip" else None
                if copy is None:
                    got = disk or crop(an, pf.Window((1080, 1920), name="story"), "story")
                    add(run.name, "DECK", f"story {name}", photo.name, got,
                        recorded=disk is not None and (disk.get("fit") is True or bool(disk.get("problems"))))
                    continue
                arch = r.get("archetype") or "editorial_split"
                if arch == "diagram" or not hasattr(G, "photo_window"):
                    continue
                k = seen[str(photo)]
                seen[str(photo)] += 1
                got = disk or crop(an, G.photo_window(arch, copy), "carousel", k)
                add(run.name, "DECK", f"{'fb' if copy else 'ig'} {name} {arch}", photo.name, got,
                    recorded=disk is not None and (disk.get("fit") is True or bool(disk.get("problems"))))
        # ---- DECK: what the photo guard chooses (the kept match)
        kept = run / "carousel_photos.json"
        if kept.is_file() and a.rule == "tip":
            from src import multi_format as mf
            try:
                rec = json.loads(kept.read_text(encoding="utf-8"))
                cards = [dict(zip(("heading", "body"), c.split("|", 1))) for c in rec["key"]["cards"]]
                stills = [Path(p) for p in rec["key"]["stills"] if Path(p).is_file()]
                matched = [Path(p) for p in rec.get("matched") or rec["photos"]]
                if all(p.is_file() for p in matched):
                    chosen, choice = mf.deck_photo_guard(cards, matched, stills)
                    archs = G.sequence_archetypes(cards)
                    for k_, (p, c) in enumerate(zip(chosen, choice)):
                        if archs[k_] == "diagram":
                            continue
                        an = photos.get(p)
                        if an is None:
                            continue
                        got = crop(an, G.photo_window(archs[k_], False), "carousel")
                        if c["reason"].startswith("fit:"):
                            got["outcome"] = "other photo" if not got["problems"] else "unfit"
                        add(run.name, "GUARD", f"slide {k_ + 1} {archs[k_]}: {c['reason'][:60]}", p.name, got,
                            recorded=True)
            except Exception as e:  # noqa: BLE001
                print(f"guard {run.name}: {e}", file=sys.stderr)
        # ---- FRAME: every master's last frame
        from src import end_card
        tmp = a.cache / "frames"
        tmp.mkdir(parents=True, exist_ok=True)
        for comp in sorted(run.glob("*.composition.json")):
            mp4 = comp.with_name(comp.name[:-len(".composition.json")] + ".mp4")
            if not mp4.is_file():
                continue
            fr = last_frame(mp4, tmp)
            if fr is None:
                continue
            with Image.open(fr) as im:
                im = im.convert("RGB")
                wide = im.width > im.height
                found = end_card.matte_window(im.resize((1920, 1080))) if wide else None
            if found is None:
                rec = {"problems": [], "outcome": "fit", "matte": 0.0}
            elif a.rule == "tip":
                rec = {"problems": [], "outcome": "alternative", "matte": found[1]["share"], "layout": "field only"}
            else:
                rec = {"problems": ["matte"], "outcome": "unfit", "matte": found[1]["share"], "layout": "feathered"}
            add(run.name, "FRAME", "end screen 16:9" if wide else "end card / bridge 9:16", mp4.name, rec,
                recorded=a.rule == "tip")
        # ---- PAIR: then-and-now
        from src import then_now
        g = run / "graphics"
        # only a run that drew the comparison (graphic_thennow.mp4) accepted a pair; the then_k / now_k_j files of
        # the others are candidates it downloaded and never showed. Which candidate a run accepted is not on record,
        # so each of its candidate pairs is measured.
        shown = (g / "graphic_thennow.mp4").is_file()
        for old in (sorted(g.glob("then_*.jpg")) + sorted(g.glob("then_*.png"))) if shown else ():
            k = old.stem.split("_")[1]
            for new in sorted(g.glob(f"now_{k}_*.jpg")) + sorted(g.glob(f"now_{k}_*.png")):
                ao, an_ = photos.get(old), photos.get(new)
                if ao is None or an_ is None:
                    continue
                registered = ao.size == an_.size
                for (w, h) in ((1080, 1920), (1920, 1080)):
                    big = (round(w * (1 + then_now.PUSH)), round(h * (1 + then_now.PUSH)))
                    lx, ly = (big[0] - w) / 2, (big[1] - h) / 2
                    cut = then_now._split_at(w, h)
                    views = ([(lx, ly, lx + cut, ly + h), (lx + cut, ly, lx + w, ly + h)] if w > h
                             else [(lx, ly, lx + w, ly + cut), (lx, ly + cut, lx + w, ly + h)])
                    for which, an2, view in zip(("then", "now"), (ao, an_), views):
                        window = pf.Window(big, view=view, name=f"then-and-now {which}")
                        if registered:
                            rec = judged(an2, pf.fit_box(an2.size, big), window)
                            rec.update(outcome="fit", problems=[], registered=True)
                        else:
                            # not registered: one window cannot show the same part of both at one scale. Neither
                            # rule's cropper changes that (the tree draws the shared window and records the pair as
                            # not registered, then_now.frame_pair): unfit at base, unfit and RECORDED at the tip.
                            rec = judged(an2, pf.fit_box(an2.size, big), window)
                            whole = an2.total("salience", tuple(rec["box"]))
                            rec["in_view"] = round(rec["subject"] / whole, 3) if whole else 0.0
                            rec["problems"] = sorted(set(rec["problems"]) | {"pair"})
                            rec.update(outcome="unfit", registered=False)
                        add(run.name, "PAIR", f"{which} pane {'16:9' if w > h else '9:16'} ({old.name} x {new.name})",
                            (old if which == "then" else new).name, rec, recorded=a.rule == "tip")

    # ---------------------------------------------------------------- the table
    def short(r):
        nums = " ".join(f"{k}={r[k]}" for k in ("upscale", "matte", "flat", "density", "keep", "focus", "line", "pad",
                                                 "in_view", "layout") if r.get(k) is not None)
        return f"{r['run'][4:44]:40s} {r['group']:5s} {r['surface'][:44]:44s} {r['photo'][:28]:28s} {nums}"

    bad = [r for r in rows if r["problems"]]
    violations = [r for r in bad if not (a.rule == "tip" and r["recorded"])]
    if not a.quiet:
        for r in rows:
            print(("FAIL " if r["problems"] else "pass ") + short(r) + (f"  {','.join(r['problems'])}" if r["problems"] else ""))
    else:
        for r in bad:
            print("FAIL " + short(r) + f"  {','.join(r['problems'])}")
    print(f"\n== photo fitness corpus, rule {a.rule}: {len(runs)} run folders, "
          f"{len({r['run'] for r in rows})} with photo placements, {len(rows)} placements")
    by = defaultdict(Counter)
    for r in rows:
        by[r["group"]]["placements"] += 1
        by[r["group"]]["unfit"] += bool(r["problems"])
        for p in r["problems"]:
            by[r["group"]][p] += 1
        by[r["group"]]["-> " + str(r["outcome"])] += 1
    for gname, c in by.items():
        print(f"  {gname:5s} " + ", ".join(f"{k} {v}" for k, v in sorted(c.items())))
    # a CERTAIN measure (the frame, the enlargement, a structure under type, a reviewed subject, a pad, a repeat, an
    # unregistered pair) against the salience measures of an unreviewed photograph (photo_fitness.SOFT), which the
    # tree records and gives to its pickers but which move no window alone
    def certain(r):
        return [p for p in r["problems"] if p not in pf.SOFT or (p == "subject" and r.get("focus") is not None)]
    hard = [r for r in bad if certain(r)]
    print(f"  unfit placements: {len(bad)} (on a certain measure: {len(hard)}; on salience measures only: "
          f"{len(bad) - len(hard)}); neither fixed nor recorded: {len(violations)}")
    for gname in by:
        hg = [r for r in hard if r["group"] == gname]
        print(f"    {gname:5s} certain {len(hg)}: " + ", ".join(f"{k} {v}" for k, v in sorted(
            Counter(p for r in hg for p in certain(r)).items())))
    frame_moves = [r for r in rows if r["group"] == "POOL" and r.get("cause") is not None]
    by_cause = defaultdict(lambda: [0, 0])
    for r in frame_moves:
        by_cause[r["cause"]][0] += 1
        by_cause[r["cause"]][1] += (r.get("excess") or 0) > 0.05
    print("  moves on framed photographs, by cause (moves, beyond the frame's need by more than 5 % of the "
          "window): " + ", ".join(f"{k} {v[0]} / {v[1]}" for k, v in sorted(by_cause.items())))
    moved = [r for r in rows if r["group"] == "DECK" and r.get("moved")]
    print(f"  DECK windows moved as drawn: {sum(1 for r in moved if r['outcome'] != 'asked (text guard)')}, "
          f"refused by the text guard: {sum(1 for r in rows if r['outcome'] == 'asked (text guard)')}; POOL moves "
          f"proposed on slides and Stories (the guard decides at draw): "
          f"{sum(1 for r in rows if str(r['outcome']).endswith('proposed'))}")
    if a.frames:
        import numpy as np
        print("\n== frames (frame_bands on THUMB px): photograph, frame share, sides")
        framed = 0
        for p in sorted({str(q) for run in runs for q in run_pool(dev, run)}):
            an = photos.get(Path(p))
            if an is None:
                continue
            _, sides = pf.frame_bands(np.asarray(an.thumb.convert("L"), np.float32)) if an.thumb else (None, {})
            framed += an.matte_share > 0
            if an.matte_share > 0:
                print(f"  {'/'.join(Path(p).parts[-2:]):60s} {an.matte_share:.4f} {sorted(sides)}")
        print(f"  {framed} framed photographs")
    if a.distribution:
        import numpy as np
        print("\n== distribution of the asked crop's measures (POOL placements)")
        for surface in dict.fromkeys(r["surface"] for r in rows if r["group"] == "POOL"):
            rs = [r for r in rows if r["group"] == "POOL" and r["surface"] == surface]
            print(f"  {surface} ({len(rs)})")
            for m in ("upscale", "matte", "flat", "density", "keep", "extreme", "line", "step", "busy"):
                v = np.array([r[m] for r in rs if r.get(m) is not None], float)
                if len(v):
                    print(f"    {m:8s} " + " ".join(f"p{q} {np.percentile(v, q):.3f}" for q in (1, 5, 10, 25, 50, 75, 90,
                                                                                                 95, 99)))
    if a.json:
        a.json.write_text(json.dumps({"rule": a.rule, "rows": rows}, indent=0), encoding="utf-8", newline="\n")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
