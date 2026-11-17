"""QF-23 acceptance measure: checked copy is never truncated; a box it does not fit refuses the surface.

    python docs/reviews/quality-fixes-2026-11/QF-23/measure.py <repository root> [<folder holding run_*>]

Runs on the base and on the row's commit (it only calls functions both trees have). Prints JSON; writes nothing
outside a temporary folder it removes. Offline (sockets refused); .env is never read (the app root is the temporary
folder, holding a copy of fonts/ and data/reference_facts.json).

1. fitting primitives (spec: "fit_text raises instead of an ellipsis"): fit_text, fit_line, fit_lines and the tracked
   fit (graphics.fit_tracked) on boxes the copy cannot fit, with and without quiet: refused, or what was returned
   (an ellipsis, dropped sentences or an overflow); and three fitting controls, which must come back whole.
2. real runs: every surface the pipeline draws from a run's checked copy, re-rendered from that copy on this tree:
   the Story frames, the Instagram and Facebook carousels and the covers (on a generated neutral photo: the real
   photos are not committed), the run's title made as a documentary's 9:16 cover and YouTube thumbnail, the 9:16
   graphics (number cards, timeline, scale) and, beside them, the 16:9 ones a
   documentary would draw, the map's site label at both sizes and the analytics chart's Reel names. Each surface is
   "ok" (drawn), "refused" (the tree raised its checked-copy refusal) or "error"; and for a drawn one, whether it
   drew an ellipsis the copy does not have, recorded a shortened element, logged a cut, and which of its checked
   copy's words it did not draw. A sha of the drawn strings per surface lets two trees be compared surface by
   surface. Inputs: real_inputs.json beside this file (the dev runs' copy, extract_real_inputs.py), or the
   run_*/verified_script.json in the folder given (a copy of the production sample, outside the repository: only
   counts and aggregates are printed, no run name, copy, hash or per-run record).
"""

import hashlib
import json
import logging
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

REFUSAL = "Checked copy does not fit"
CUT_LOG = re.compile(r"shortened|kept to \d+ of \d+ sentence|label\(s\) cut|a label cut|runs past its|under its \d+ px "
                     r"floor|cut to a single word")
HERE = Path(__file__).resolve().parent
WORKERS = 4


def _offline(*_a, **_k):
    raise RuntimeError("measure.py is offline")


def _words(text) -> set:
    return set(re.findall(r"[A-Z0-9]+", str(text or "").upper().replace("’", "'")))


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.cuts = []

    def emit(self, record):
        msg = record.getMessage()
        if CUT_LOG.search(msg):
            self.cuts.append(msg[:160])


_DRAWN = threading.local()
_READY: list = []


def _setup(root: str, app: str) -> None:
    """Per process: the tree on sys.path, its config rooted at the temporary app folder, sockets refused, every drawn
    string recorded (ImageDraw.text and the tracked-run drawer)."""
    if _READY:
        return
    _READY.append(True)
    os.environ.update(RELIC_APP_ROOT=app, FONT_SIZE="80", PYTHONIOENCODING="utf-8")
    socket.create_connection = _offline
    socket.socket.connect = _offline
    sys.path.insert(0, root)
    from PIL import ImageDraw
    real_text = ImageDraw.ImageDraw.text

    def text(self, xy, text, *a, **k):
        sink = getattr(_DRAWN, "sink", None)
        if sink is not None:
            sink.append(str(text))
        return real_text(self, xy, text, *a, **k)
    ImageDraw.ImageDraw.text = text
    try:
        from src import font_engine, typography
        real_run = font_engine.draw_run

        def draw_run(d, xy, text, *a, **k):
            sink = getattr(_DRAWN, "sink", None)
            if sink is not None:
                sink.append(str(text))
            return real_run(d, xy, text, *a, **k)
        font_engine.draw_run = draw_run
        typography.draw_run = draw_run
    except (ImportError, AttributeError):
        pass
    from src import graphics

    def encode(frames, out, n, *a, **k):   # the frames drawn (at 1 fps, as the tests draw them), none encoded
        for _ in frames:
            pass
        return Path(out)

    def record(out, frame, plates):         # a graphic's final plates: what its last frame draws
        sink = getattr(_DRAWN, "final", None)
        if sink is not None:
            sink += [p.text for _n, p in plates if p is not None and not p.empty]
        return {}
    from src.multi_format import MultiFormatGenerator
    # the photos' order by the vision match: on neutral photos it only costs time, and the fitting never reads it
    MultiFormatGenerator._match_photos = lambda self, cards, stills: [stills[i % len(stills)] for i in range(len(cards))]
    graphics.VIDEO_FPS = 1
    graphics._encode = encode
    graphics.graded_frame = lambda *a, **k: None
    graphics.record_contrast = record
    graphics.record_nontext = lambda *a, **k: {}
    logging.getLogger().setLevel(logging.WARNING)


def _photos(folder: Path) -> list:
    """Six neutral photos (a soft gradient with fine noise, seeded): the stills' text fitting does not read the
    photo's pixels except to choose the tighter fit on a busy one."""
    import numpy as np
    from PIL import Image
    out = []
    for k in range(6):
        rng = np.random.default_rng(k)
        y = np.linspace(0, 1, 2400)[:, None, None]
        base = 70 + 60 * y + np.array([18 * k % 30, 10, -8 * (k % 3)])[None, None, :]
        im = np.clip(base + rng.normal(0, 6, (2400, 1600, 3)), 0, 255).astype("uint8")
        path = folder / f"photo_{k}.jpg"
        Image.fromarray(im).save(path, quality=92)
        out.append(path)
    return out


def _surface(name: str, fields: list, fn) -> dict:
    """Runs one surface's renderer: its outcome, what it drew, and how that compares with its checked copy."""
    cap = _Capture()
    logging.getLogger().addHandler(cap)
    _DRAWN.sink, _DRAWN.final = [], []
    rec = {"surface": name}
    try:
        layouts = fn()
        layouts = layouts if isinstance(layouts, dict) else (layouts or [])
        rec["outcome"] = "skipped" if isinstance(layouts, dict) and layouts.get("made") is False else "ok"
    except Exception as e:  # noqa: BLE001  the measure's subject: refusal versus any other failure
        msg = str(e)
        layouts = []
        if REFUSAL in msg:
            rec.update(outcome="refused", reason=msg)
        else:
            rec.update(outcome="error", reason=f"{type(e).__name__}: {msg[:200]}")
    finally:
        logging.getLogger().removeHandler(cap)
        drawn, _DRAWN.sink = _DRAWN.sink, None
        final, _DRAWN.final = _DRAWN.final, None
    if isinstance(layouts, dict):          # a label fitted alone draws what it returned; a graphic its final plates
        drawn = layouts.get("drawn", final)
        layouts = []
    source = " ".join(str(f or "") for f in fields)
    have = set().union(*(_words(t) for t in drawn)) if drawn else set()
    if rec["outcome"] == "ok":
        rec["ellipsis_drawn"] = sorted({t for t in drawn if "…" in t and t.replace("…", "").strip()
                                        and "…" not in source})[:5]
        rec["shortened"] = sorted({s for lay in layouts for s in (lay.get("shortened") or [])})
        rec["cut_logged"] = cap.cuts[:5]
        rec["missing_words"] = sorted(_words(source) - have)
        rec["drawn_sha"] = hashlib.sha256("\n".join(drawn).encode("utf-8")).hexdigest()[:16]
    return rec


def _layouts(folder: Path) -> list:
    out = []
    for p in sorted(folder.rglob("still_layout.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        out += [v for v in (data.values() if isinstance(data, dict) else data) if isinstance(v, dict)]
    return out


def _run(job) -> dict:
    root, app, payload = job
    _setup(root, app)
    from src import graphics
    from src.multi_format import MultiFormatGenerator
    from src.visual_engine import VisualEngine
    photos = sorted(Path(app, "photos").glob("photo_*.jpg"))
    work = Path(tempfile.mkdtemp(dir=app))
    p = dict(payload)
    stories = p.get("story_slides") or []
    cards = p.get("carousel_slides") or []
    thumb = p.get("thumbnail") or {}
    out = []

    def still(name, fields, make):
        def run():
            folder = work / name
            make(folder)
            return _layouts(folder)
        out.append(_surface(name, fields, run))
    story_fields = [s.get(k) for s in stories for k in ("kicker", "line1", "line2", "body")]
    if stories:
        still("stories", story_fields,
              lambda f: MultiFormatGenerator().generate_story_sequence(photos[0], p, f, scene_images=photos))
    if cards:
        card_fields = [c.get(k) for c in cards for k in ("heading", "body")] + [p.get("share_line")]
        still("carousel", card_fields, lambda f: MultiFormatGenerator().generate_carousel_sequence(photos, p, f))
        still("carousel_fb", card_fields,
              lambda f: MultiFormatGenerator().generate_carousel_sequence(photos, p, f, facebook=True))
    if thumb:
        still("covers", [thumb.get(k) for k in ("eyebrow", "line1", "line2", "tagline")],
              lambda f: VisualEngine().generate_thumbnails(photos[0], p, f))
    if p.get("title"):
        # The run's checked title made as a documentary would make it (main.documentary_cover, the 9:16 cover's copy,
        # and the YouTube thumbnail's): no real run is a documentary, so its title stands in (review 1 of QF-23).
        from main import documentary_cover
        doc = {**p, "thumbnail": documentary_cover(p["title"])}
        doc_fields = [p["title"]]
        still("documentary_cover", doc_fields,
              lambda f: VisualEngine().generate_thumbnails(photos[0], doc, f, variants=False))
        still("youtube_thumbnail", doc_fields,
              lambda f: (Path(f).mkdir(parents=True), VisualEngine().generate_youtube_thumbnail(
                  photos[0], doc, Path(f) / "yt.jpg")))
    narration = p.get("narration_script") or ""

    def graphic(made):   # drawn from its final plates; a graphic the copy does not call for draws nothing
        return {"graphic": True, "made": made is not None}

    def graphics_at(w: int, h: int) -> None:
        tag = "916" if h > w else "169"
        if p.get("stat_cards"):
            out.append(_surface(f"numbers_{tag}", [c.get(k) for c in p["stat_cards"] for k in ("value", "label")],
                                lambda: graphic(graphics.render_stat_cards(p["stat_cards"], work / f"n{tag}.mp4",
                                                                           photos[1], width=w, height=h,
                                                                           narration=narration))))
        if p.get("timeline"):
            out.append(_surface(f"timeline_{tag}", [e.get("label") for e in p["timeline"]],
                                lambda: graphic(graphics.render_timeline(p["timeline"], work / f"t{tag}.mp4", width=w,
                                                                         height=h, photo=photos[2]))))
        if p.get("scale_fact"):
            out.append(_surface(f"scale_{tag}", [p["scale_fact"].get("subject")],
                                lambda: graphic(graphics.render_scale(p["scale_fact"], work / f"s{tag}.mp4", width=w,
                                                                      height=h))))
        name = (p.get("location") or {}).get("name")
        if name:
            out.append(_surface(f"site_label_{tag}", [name],
                                lambda: {"drawn": graphics.marker_label_lines(name, w, h)[0]}))
    graphics_at(1080, 1920)
    graphics_at(1920, 1080)
    if p.get("title"):
        from src import audience_chart
        reel = {"run_id": p["run"], "title": p["title"], "views": {"instagram": 1200, "youtube": 900, "facebook": 300}}
        today = datetime(2026, 11, 10, 12, tzinfo=UTC)   # the footer's date: fixed, so two trees compare
        out.append(_surface("chart_title", [p["title"]],
                            lambda: audience_chart.render([reel], work / "chart.png", sample=True,
                                                          today=today) and []))
    shutil.rmtree(work, ignore_errors=True)
    return {"run": p["run"], "surfaces": out}


def _primitives(root: str, app: str) -> dict:
    """Section 1: the fitting functions on impossible boxes and on fitting controls."""
    _setup(root, app)
    from PIL import Image, ImageDraw

    from src import graphics, typography
    draw = ImageDraw.Draw(Image.new("RGB", (1600, 600)))
    cases = ["The object is about two thousand years old, according to the surviving inscription.",
             "The doorway is closed. The proposed reconstruction remains disputed.",
             "Antidisestablishmentarianism"]
    rows = []
    for text in cases:
        for method in ("fit_text", "fit_text quiet", "fit_line", "fit_lines", "fit_tracked"):
            try:
                if method.startswith("fit_text"):
                    lines, font, short = typography.fit_text(draw, text, "Montserrat", 40, 100, 1, 40,
                                                             quiet=method.endswith("quiet"))
                elif method == "fit_line":
                    line, font, short = typography.fit_line(draw, text, "Montserrat", 40, 100, 40, quiet=True)
                    lines = [line]
                elif method == "fit_lines":
                    lines, font = typography.fit_lines(draw, text, typography.get_font("Montserrat", 40), 100, 1)
                    short = None
                else:
                    lines, font, short = graphics.fit_tracked(draw, text, "Montserrat", 40, 100, 1, 40, 2, quiet=True)
                got = " ".join(lines)
                rows.append({"method": method, "input": text, "refused": False, "returned": lines,
                             "ellipsis": "…" in got, "words_lost": sorted(_words(text) - _words(got)),
                             "overflows": any(typography.line_width(draw, ln, font) > 100 for ln in lines)})
            except ValueError as e:
                rows.append({"method": method, "input": text, "refused": REFUSAL in str(e), "reason": str(e)})
    controls = []
    for text in ("About two thousand years old. Its exact date remains disputed.", "Built under King Krishna I.",
                 "Königsberg, 1945."):
        lines, font, short = typography.fit_text(draw, text, "Montserrat", 40, 900, 3, 34)
        controls.append({"input": text, "lines": lines, "px": font.size, "shortened": short,
                         "whole": " ".join(" ".join(lines).split()) == " ".join(text.split()),
                         "within_width": all(typography.line_width(draw, ln, font) <= 900 for ln in lines)})
    return {"impossible_boxes": rows, "refused": sum(r["refused"] for r in rows), "cases": len(rows),
            "truncated": sum(1 for r in rows if not r["refused"] and (r.get("ellipsis") or r.get("words_lost"))),
            "fitting_controls": controls}


def _redact(result: dict) -> dict:
    """The production sample prints counts only (operator rule, 2026-11-10: nothing derived from the sample is
    committed but counts and aggregates): no run names, copy, hashes or per-run records. The refusals are counted by
    surface and by the box they did not fit (its size, not its words)."""
    refused: dict = {}
    for run in result.pop("runs"):
        for s in run["surfaces"]:
            if s["outcome"] == "refused":
                box = s["reason"].split(": \"", 1)[0]
                refused[f"{s['surface']}: {box}"] = refused.get(f"{s['surface']}: {box}", 0) + 1
    result["refusals"] = dict(sorted(refused.items()))
    result["source"] = "a folder of run_*/verified_script.json given as the second argument (counts only)"
    return result


def main(root: Path, folder: Path | None) -> None:
    root = root.resolve()
    if folder is None:
        payloads = json.loads((HERE / "real_inputs.json").read_text(encoding="utf-8"))
        source = "real_inputs.json (dev runs' checked copy, extract_real_inputs.py)"
    else:
        payloads = []
        for script in sorted(Path(folder).glob("run_*/verified_script.json")):
            payloads.append({"run": script.parent.name, **json.loads(script.read_text(encoding="utf-8"))})
        source = "run_*/verified_script.json in the folder given (read in place)"
    with tempfile.TemporaryDirectory(prefix="qf23-measure-") as tmp:
        app = Path(tmp)
        shutil.copytree(root / "fonts", app / "fonts")
        (app / "data").mkdir()
        shutil.copyfile(root / "data" / "reference_facts.json", app / "data" / "reference_facts.json")
        (app / "photos").mkdir()
        _photos(app / "photos")
        with ProcessPoolExecutor(WORKERS) as pool:
            primitives = pool.submit(_primitives, str(root), str(app)).result()
            runs = list(pool.map(_run, [(str(root), str(app), p) for p in payloads]))
    counts: dict = {}
    for run in runs:
        for s in run["surfaces"]:
            c = counts.setdefault(s["surface"], {"ok": 0, "refused": 0, "error": 0, "skipped": 0, "drew_cut": 0})
            c[s["outcome"]] += 1
            c["drew_cut"] += bool(s.get("ellipsis_drawn") or s.get("shortened") or s.get("cut_logged"))
    surfaces = [s for run in runs for s in run["surfaces"]]
    result = {
        "schema": 1, "source": source, "runs_measured": len(runs),
        "source_sha256": {n: hashlib.sha256((root / n).read_bytes()).hexdigest()[:16] for n in (
            "src/typography.py", "src/graphics.py", "src/multi_format.py", "src/cover_design.py",
            "src/visual_engine.py", "src/still_layout.py", "src/audience_chart.py")},
        "primitives": primitives,
        "surfaces": counts,
        "totals": {"surfaces": len(surfaces), "ok": sum(s["outcome"] == "ok" for s in surfaces),
                   "refused": sum(s["outcome"] == "refused" for s in surfaces),
                   "error": sum(s["outcome"] == "error" for s in surfaces),
                   "drew_cut": sum(bool(s.get("ellipsis_drawn") or s.get("shortened") or s.get("cut_logged"))
                                   for s in surfaces)},
        "runs": runs,
    }
    result["acceptance_met"] = (primitives["refused"] == primitives["cases"] and primitives["truncated"] == 0
                                and all(c["whole"] and c["within_width"] and not c["shortened"]
                                        for c in primitives["fitting_controls"])
                                and result["totals"]["drew_cut"] == 0)
    if folder is not None:
        _redact(result)
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit("usage: measure.py <repository root> [<folder holding run_*>]")
    main(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) == 3 else None)
