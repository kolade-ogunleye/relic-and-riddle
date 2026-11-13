"""QF-24 acceptance metrics: 9:16 figures use the full frame inside the shared organic Reel safe rectangle.

    python measure.py <repository root>

Runs on this commit and on its base alike (it reads only what both trees have) and prints JSON; it writes nothing
outside a temporary folder. Inputs, all committed:
- the dev runs' checked graphic copy, docs/reviews/quality-fixes-2026-11/QF-23/real_inputs.json (34 runs: number
  cards, timelines, scale facts, a route), drawn as the pipeline draws them at 1080 x 1920; a timeline is drawn
  without its photo (on the ink ground) so its own ink can be weighed;
- the freeze panel's caption fixtures, docs/reviews/freeze-panel-2026-11-04/fixtures/runs/*/words.json (the seven
  9:16 narrations, with the catalogue's emphasis words), built into 9:16 caption files.

The 16:9 deliverables must not move: the same graphics are drawn at 1920 x 1080 and every narration (the
documentary's too) is built into a 16:9 caption file; their final frames and ASS events are hashed ("wide"), so the
base and this commit can be compared byte for byte.

The surfaces build 1b re-laid (operator, 2026-11-13: keep the approved rectangle, fix the surfaces) are measured on
committed inputs too ("surfaces"): the callouts on the operator's reference door/wall pair (the tests' REF_DOOR and
REF_WALL coordinates), the 9:16 end card on the catalogue's five 9:16 share lines, the then-and-now split and NOW
marker, the myth, hook and chapter cards on the tests' checked fields (the reviewed long myth LONG_ET, the stress
myth LONGEST916, the Richat sheet's fact-9 correction and the long Richat run's act titles; signed in this process
with the tests' throwaway key), and the five_close stress timeline. The timeline refusal census over every committed
set is QF-23's timeline_refusals.py (census_before.json / census_after.json in this folder).

The yardstick is fixed here, not read from the tree, so the base is measured against the same boxes: the approved
shared organic Reel rectangle (113, 288)-(848, 1370), its graphics box (y 1090: 240 px caption band and the 40 px
gap off the bottom) and each Reel platform's own free area (safe_zones.PLATFORM_UI as approved).
"""
import hashlib
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
W, H = 1080, 1920
REEL = (113, 288, 848, 1370)
GRAPHICS = (113, 288, 848, 1090)
BAND_TOP = 1130
ADS_REEL = (65, 288, 888, 1248)            # the box until QF-24 (Meta's ads guide; Shorts' ads guide)
PLATFORMS = {                                # approved per-platform free areas (x0, y0, x1, y1) at 1080 x 1920
    "instagram_reels": (113, 245, 852, 1502),
    "facebook_reels": (113, 244, 852, 1370),
    "youtube_shorts": (65, 288, 848, 1382),
}
WORKERS = 4
_STATE = threading.local()
_READY: list = []


def _offline(*_a, **_k):
    raise RuntimeError("measure.py is offline")


def _setup(root: str, app: str) -> None:
    if _READY:
        return
    _READY.append(True)
    os.environ.update(RELIC_APP_ROOT=app, FONT_SIZE="80", PYTHONIOENCODING="utf-8")
    socket.create_connection = _offline
    socket.socket.connect = _offline
    sys.path.insert(0, root)
    from src import graphics

    def encode(frames, out, n, *a, **k):
        for _ in frames:
            pass
        return Path(out)

    def record(out, frame, plates):
        _STATE.final = (frame.convert("RGB"), [(name, p.text, [float(v) for v in p.box], int(p.font.size))
                                               for name, p in plates if p is not None and not p.empty])
        return {}
    graphics.VIDEO_FPS = 2
    graphics._encode = encode
    graphics.graded_frame = lambda *a, **k: None
    graphics.record_contrast = record
    graphics.record_nontext = lambda *a, **k: {}


def _inside(b, box) -> bool:
    return b[0] >= box[0] - 0.5 and b[1] >= box[1] - 0.5 and b[2] <= box[2] + 0.5 and b[3] <= box[3] + 0.5


def _words(texts) -> list:
    return sorted(w for t in texts for w in re.findall(r"[A-Z0-9]+", str(t).upper().replace("\u2019", "'")))


def _ink_rows(frame, ground):
    """Row weights of the drawn content: each pixel's distance from the ink ground (luma), above a noise floor."""
    import numpy as np
    a = np.asarray(frame.convert("L"), np.float32)
    d = np.abs(a - float(np.median(a[:, :24])))
    d[d < 24] = 0
    return d.sum(axis=1)


def _graphic(name: str, fn, ink_ground: bool) -> dict:
    _STATE.final = None
    rec = {"graphic": name}
    try:
        made = fn()
    except Exception as e:  # noqa: BLE001  the measure records any refusal or failure
        rec["outcome"] = f"error: {type(e).__name__}"
        return rec
    final = _STATE.final
    rec["outcome"] = "ok" if made is not None and final else "skipped"
    if rec["outcome"] != "ok":
        return rec
    frame, plates = final
    boxes = [b for _n, _t, b, _s in plates]
    area = [max(0.0, (b[2] - b[0]) * (b[3] - b[1])) for b in boxes]
    rec.update({
        "plates": len(plates),
        "min_px": min(s for *_x, s in plates), "max_px": max(s for *_x, s in plates),
        "words_sha": hashlib.sha256(" ".join(_words(t for _n, t, _b, _s in plates)).encode()).hexdigest()[:16],
        "text_top": round(min(b[1] for b in boxes)), "text_foot": round(max(b[3] for b in boxes)),
        "text_left": round(min(b[0] for b in boxes)), "text_right": round(max(b[2] for b in boxes)),
        "text_centroid_y": round(sum(a * (b[1] + b[3]) / 2 for a, b in zip(area, boxes)) / max(1.0, sum(area)), 1),
        "outside_graphics_box": sum(not _inside(b, GRAPHICS) for b in boxes),
        "outside_reel_box": sum(not _inside(b, REEL) for b in boxes),
        "under_platform_ui": {p: sum(not _inside(b, box) for b in boxes) for p, box in PLATFORMS.items()},
    })
    if ink_ground:
        import numpy as np
        rows = _ink_rows(frame, None)
        ys = np.nonzero(rows)[0]
        rec["ink_top"], rec["ink_foot"] = int(ys.min()), int(ys.max())
        rec["ink_centroid_y"] = round(float((rows * np.arange(len(rows))).sum() / rows.sum()), 1)
    return rec


def _wide(name: str, fn) -> dict:
    """A 16:9 graphic's final frame and plates, hashed (it must be identical on both trees)."""
    _STATE.final = None
    try:
        made = fn()
    except Exception as e:  # noqa: BLE001  the measure records any refusal or failure
        return {"graphic": name, "outcome": f"error: {type(e).__name__}"}
    if made is None or not _STATE.final:
        return {"graphic": name, "outcome": "skipped"}
    frame, plates = _STATE.final
    return {"graphic": name, "outcome": "ok",
            "frame_sha": hashlib.sha256(frame.tobytes()).hexdigest()[:16],
            "plates_sha": hashlib.sha256(json.dumps(plates).encode()).hexdigest()[:16]}


def _graphics_job(job) -> dict:
    root, app, payload = job
    _setup(root, app)
    from src import graphics
    work = Path(tempfile.mkdtemp(dir=app))
    p, out, wide = payload, [], []
    if p.get("stat_cards"):
        wide.append(_wide("numbers", lambda: graphics.render_stat_cards(
            p["stat_cards"], work / "wn.mp4", None, 1920, 1080, narration=p.get("narration_script") or "")))
    if p.get("timeline"):
        wide.append(_wide("timeline", lambda: graphics.render_timeline(p["timeline"], work / "wt.mp4", 1920, 1080)))
    if p.get("scale_fact"):
        wide.append(_wide("scale", lambda: graphics.render_scale(p["scale_fact"], work / "ws.mp4", 1920, 1080)))
    narration = p.get("narration_script") or ""
    if p.get("stat_cards"):
        out.append(_graphic("numbers", lambda: graphics.render_stat_cards(p["stat_cards"], work / "n.mp4", None,
                                                                           narration=narration), False))
    if p.get("timeline"):
        out.append(_graphic("timeline", lambda: graphics.render_timeline(p["timeline"], work / "t.mp4"), True))
    if p.get("scale_fact"):
        out.append(_graphic("scale", lambda: graphics.render_scale(p["scale_fact"], work / "s.mp4"), True))
    if p.get("route"):
        out.append(_graphic("route", lambda: graphics.render_route(p["route"], work / "r.mp4"), False))
    shutil.rmtree(work, ignore_errors=True)
    return {"run": p["run"], "graphics": out, "wide": wide}


def _pills(ass: Path):
    """Each 9:16 plate's rectangle and its burst signature (start, end, width, height), from the CapBox events."""
    rows = []
    for line in ass.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue:") or ",CapBox," not in line:
            continue
        f = line.split(",", 9)
        text = f[9]
        pos = re.search(r"\\pos\((-?\d+),(-?\d+)\)", text)
        shape = re.search(r"\\p1\}([^{]*)\{", text)
        nums = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", shape.group(1))]
        x, y = int(pos.group(1)), int(pos.group(2))
        xs, ys = nums[0::2], nums[1::2]
        rows.append({"start": f[1], "end": f[2], "box": (x + min(xs), y + min(ys), x + max(xs), y + max(ys))})
    return rows


def _wide_caption_job(job) -> dict:
    root, app, run, words, emphasis = job
    _setup(root, app)
    from src.video_composer import VideoComposer
    work = Path(tempfile.mkdtemp(dir=app))
    ass = VideoComposer().build_subtitles(words, work / "w.ass", emphasis=emphasis, width=1920, height=1080)
    events = [ln for ln in ass.read_text(encoding="utf-8").splitlines() if ln.startswith(("Dialogue:", "Style:"))]
    shutil.rmtree(work, ignore_errors=True)
    return {"run": run, "events": len(events),
            "events_sha": hashlib.sha256(json.dumps(events).encode()).hexdigest()[:16]}


def _caption_job(job) -> dict:
    root, app, run, words, emphasis = job
    _setup(root, app)
    from src.video_composer import VideoComposer
    work = Path(tempfile.mkdtemp(dir=app))
    ass = VideoComposer().build_subtitles(words, work / "c.ass", emphasis=emphasis)
    text = ass.read_text(encoding="utf-8")
    pills = _pills(ass)
    sig = [(r["start"], r["end"], round(r["box"][2] - r["box"][0]), round(r["box"][3] - r["box"][1])) for r in pills]
    shown = [re.sub(r"\{[^}]*\}", "", ln.split(",", 9)[9]) for ln in text.splitlines()
             if ln.startswith("Dialogue:") and ",CapText," in ln]
    shutil.rmtree(work, ignore_errors=True)
    boxes = [r["box"] for r in pills]
    widest = max(boxes, key=lambda b: b[2] - b[0])
    return {"run": run, "bursts": len(pills),
            "bursts_sha": hashlib.sha256(json.dumps([(s, e) for s, e, _w, _h in sig]).encode()).hexdigest()[:16],
            "plates_sha": hashlib.sha256(json.dumps(sig).encode()).hexdigest()[:16],
            "words_sha": hashlib.sha256(" ".join(sorted(set(shown))).encode()).hexdigest()[:16],
            "two_line_bursts": sum(1 for r in pills if r["box"][3] - r["box"][1] > 150),
            "outside_reel_box": sum(not _inside(b, REEL) for b in boxes),
            "outside_caption_band": sum(not _inside(b, (REEL[0], BAND_TOP, REEL[2], REEL[3])) for b in boxes),
            "under_platform_ui": {p: sum(not _inside(b, box) for b in boxes) for p, box in PLATFORMS.items()},
            "plate_x": [round(min(b[0] for b in boxes)), round(max(b[2] for b in boxes))],
            "plate_y": [round(min(b[1] for b in boxes)), round(max(b[3] for b in boxes))],
            "widest_plate_centre_x": round((widest[0] + widest[2]) / 2, 1),
            "widest_plate_w": round(widest[2] - widest[0])}


def _profile(root: str, app: str) -> dict:
    _setup(root, app)
    from src import graphics, safe_zones
    from src import video_composer as vc
    return {"reel_box": list(safe_zones.safe_box("reel")), "story_box": list(safe_zones.safe_box("story")),
            "graphics_box": list(safe_zones.graphics_box()), "caption_band_top": safe_zones.caption_band_top(),
            "graphics_text_box": list(graphics.get_text_box(W, H)),
            "inset_edges": [graphics.inset_x0(), graphics.inset_x1()],
            "caption_ink_measure": vc.MAX_CAPTION_WIDTH, "caption_margin_v": vc.CAPTION_MARGIN_V,
            "two_line_rungs": [r for r in vc.CAPTION_LADDER if vc._two_line_clears_graphics(r)]}


def _ink_box(boxes) -> list:
    return [round(min(b[0] for b in boxes)), round(min(b[1] for b in boxes)), round(max(b[2] for b in boxes)),
            round(max(b[3] for b in boxes))]


def _surfaces(root: str, app: str, shares: list) -> dict:
    """Build 1b's surfaces on this tree (see the module docstring)."""
    _setup(root, app)
    from itertools import combinations

    from src import callouts, config, end_card, graphics, then_now
    from src import design_cards as dc
    out: dict = {}
    # (a) callouts: the reference pair
    from tests.test_callouts_v4_2026_11_03 import REF_DOOR, REF_WALL
    kept = callouts.declutter(callouts.clear_of_captions([REF_DOOR, REF_WALL], W, H), W, H)
    if kept is None:
        out["callouts_reference_pair"] = {"layout": None}
    else:
        labs = callouts.layout_labels(kept, W, H)
        boxes = [tuple(lab["box"]) for lab in labs]
        gaps = [max(b[0] - a[2], a[0] - b[2], b[1] - a[3], a[1] - b[3]) for a, b in combinations(boxes, 2)]
        out["callouts_reference_pair"] = {
            "labels": [lab["lines"] for lab in labs], "px": sorted({lab["font"].size for lab in labs}),
            "conflict": any(lab["conflict"] for lab in labs), "plates": [[round(v) for v in b] for b in boxes],
            "inside_graphics_box": all(_inside(b, GRAPHICS) for b in boxes), "min_plate_gap": round(min(gaps))}
    # (b) end card, 9:16
    cards = []
    for share in shares:
        lay = end_card.layout(share)
        m = lay["masthead"]
        left = lay["masthead_xy"][0] - 2 * m["mark_r"] - m["gap"]
        cards.append({"masthead_px": m["size"], "masthead_x": [round(left), round(left + m["width"])],
                      "share_px": lay["share_font"].size, "share_lines": len(lay["lines"]),
                      "box": [round(v) for v in lay["box"]],
                      "inside_reel_box": _inside((left, 0, left + m["width"], 0), (REEL[0], 0, REEL[2], 0))
                      and _inside(lay["box"], REEL)})
    out["end_card"] = cards
    # (c) then-and-now, 9:16
    year = graphics.get_font("Anton", then_now.YEAR_PX["portrait"])
    (_kx, _ky), (_yx, yy), _ = then_now.marker_lines("now", 2018, W, H)
    out["then_now"] = {"split": then_now._split_at(W, H), "then_marker": list(then_now.marker_xy("then", W, H)),
                       "now_marker": list(then_now.marker_xy("now", W, H)),
                       "now_year_foot": round(yy + year.getbbox("2018", anchor="lt")[3])}
    # (d) cards, 9:16 (the tests' checked fields, signed with the tests' key in this process)
    config.FACT_RECORD_KEY = "test-only-fact-record-key-0123456789abcdef"
    from tests import test_freeze_cards_2026_11_04 as fc
    richat = fc.real_sheet("richat_structure_sahara_eye")
    from tests import test_design_cards_2026_11_03 as T
    still = T._photo(Path(tempfile.mkdtemp(dir=app)) / "warm.jpg", "warm")   # the tests' own ground (h1)
    plans = {"myth_long_et": lambda: fc.plan_myth(fc.LONG_ET), "myth_longest916": lambda: fc.plan_myth(fc.LONGEST916),
             "myth_richat_fact9": lambda: fc.richat_myth(richat), "hook_h1": lambda: fc.hook_plan(still)}
    plans.update({f"chapter_richat_act{k + 1}": (lambda k=k, t=t: fc.chapter_plan(fc.TALL, k + 1, fc.ACTS, t))
                  for k, t in enumerate(fc.RICHAT_ACTS)})
    cd: dict = {}
    for name, make in plans.items():
        try:
            plan = make()
        except dc.CardRefused as e:
            cd[name] = {"refused": str(e)[:120]}
            continue
        texts = [e for e in plan.elements if e.kind == "text"]
        cd[name] = {"ink": _ink_box([e.box for e in plan.elements]), "text_px": {e.name: e.px for e in texts},
                    "inside_graphics_box": all(_inside(e.box, (GRAPHICS[0] - 2, *GRAPHICS[1:])) for e in plan.elements)}
    out["cards"] = cd
    # (e) the five_close stress timeline
    from tests.test_r4_graphics_2026_11_02 import FIVE_CLOSE
    with tempfile.TemporaryDirectory() as tmp:
        try:
            graphics.render_timeline(FIVE_CLOSE, Path(tmp) / "t.mp4", W, H)
            out["timeline_five_close"] = "drawn"
        except graphics.TextFitError:
            out["timeline_five_close"] = "refused"
    return out


def _summary(rows: list) -> dict:
    ok = [r for r in rows if r.get("outcome") == "ok"]
    out = {"graphics": len(rows), "ok": len(ok), "errors": sum(str(r.get("outcome", "")).startswith("error")
                                                                for r in rows)}
    if ok:
        out.update({
            "text_plates": sum(r["plates"] for r in ok),
            "plates_outside_graphics_box": sum(r["outside_graphics_box"] for r in ok),
            "graphics_with_text_outside_graphics_box": sum(r["outside_graphics_box"] > 0 for r in ok),
            "plates_outside_reel_box": sum(r["outside_reel_box"] for r in ok),
            "plates_under_platform_ui": {p: sum(r["under_platform_ui"][p] for r in ok) for p in PLATFORMS},
            "min_px": min(r["min_px"] for r in ok),
            "median_text_foot": sorted(r["text_foot"] for r in ok)[len(ok) // 2],
            "max_text_foot": max(r["text_foot"] for r in ok),
        })
        ink = [r for r in ok if "ink_foot" in r]
        if ink:
            out["median_ink_foot"] = sorted(r["ink_foot"] for r in ink)[len(ink) // 2]
            out["median_ink_centroid_y"] = sorted(r["ink_centroid_y"] for r in ink)[len(ink) // 2]
    return out


def main(root: Path) -> None:
    root = root.resolve()
    payloads = json.loads((HERE.parent / "QF-23" / "real_inputs.json").read_text(encoding="utf-8"))
    fx = root / "docs" / "reviews" / "freeze-panel-2026-11-04" / "fixtures"
    catalogue = json.loads((fx / "fixtures.json").read_text(encoding="utf-8"))
    emphasis = {Path(c["run"]).name: c.get("emphasis_words") or []
                for c in catalogue["designs"]["captions"]["cases"] if c.get("format") == "9:16"}
    captions, wide_captions = [], []
    for words in sorted((fx / "runs").glob("run_*/words.json")):
        run = words.parent.name
        row = (run, json.loads(words.read_text(encoding="utf-8")), emphasis.get(run, []))
        wide_captions.append(row)
        if run.endswith("_long"):          # the 16:9 documentary's narration
            continue
        captions.append(row)
    with tempfile.TemporaryDirectory(prefix="qf24-measure-") as tmp:
        app = Path(tmp)
        shutil.copytree(root / "fonts", app / "fonts")
        (app / "data").mkdir()
        shutil.copyfile(root / "data" / "reference_facts.json", app / "data" / "reference_facts.json")
        with ProcessPoolExecutor(WORKERS) as pool:
            profile = pool.submit(_profile, str(root), str(app)).result()
            shares = [c["share_line"] for c in catalogue["designs"]["endcard"]["cases"] if c["format"] == "9:16"]
            surfaces = pool.submit(_surfaces, str(root), str(app), shares)
            runs = list(pool.map(_graphics_job, [(str(root), str(app), p) for p in payloads]))
            caps = list(pool.map(_caption_job, [(str(root), str(app), r, w, e) for r, w, e in captions]))
            wide_caps = list(pool.map(_wide_caption_job, [(str(root), str(app), r, w, e) for r, w, e in wide_captions]))
            surfaces = surfaces.result()
    by_kind: dict = {}
    for run in runs:
        for g in run["graphics"]:
            by_kind.setdefault(g["graphic"], []).append(g)
    result = {
        "schema": 1,
        "inputs": {"graphics": "QF-23/real_inputs.json (34 dev runs' checked copy)",
                   "captions": "freeze-panel-2026-11-04/fixtures/runs/*/words.json (7 9:16 narrations)"},
        "yardstick": {"reel_box": REEL, "graphics_box": GRAPHICS, "caption_band_top": BAND_TOP,
                      "platforms": PLATFORMS},
        "source_sha256": {n: hashlib.sha256((root / n).read_bytes()).hexdigest()[:16] for n in (
            "src/safe_zones.py", "src/graphics.py", "src/video_composer.py", "src/config.py")},
        "profile": profile,
        "graphics_summary": {k: _summary(v) for k, v in sorted(by_kind.items())},
        "graphics_total": _summary([g for v in by_kind.values() for g in v]),
        "captions_total": {
            "runs": len(caps), "bursts": sum(c["bursts"] for c in caps),
            "plates_outside_reel_box": sum(c["outside_reel_box"] for c in caps),
            "plates_outside_caption_band": sum(c["outside_caption_band"] for c in caps),
            "plates_under_platform_ui": {p: sum(c["under_platform_ui"][p] for c in caps) for p in PLATFORMS},
            "two_line_bursts": sum(c["two_line_bursts"] for c in caps),
        },
        "captions": caps,
        "surfaces": surfaces,
        "wide": {"graphics": sum(len(r["wide"]) for r in runs),
                 "graphics_ok": sum(g["outcome"] == "ok" for r in runs for g in r["wide"]),
                 "graphics_sha": hashlib.sha256(json.dumps([[r["run"], r["wide"]] for r in runs]).encode()
                                                ).hexdigest()[:16],
                 "caption_files": len(wide_caps),
                 "captions_sha": hashlib.sha256(json.dumps(wide_caps).encode()).hexdigest()[:16],
                 "captions": wide_caps},
        "runs": runs,
    }
    g, c = result["graphics_total"], result["captions_total"]
    result["acceptance_met"] = (g["errors"] == 0 and g["plates_outside_graphics_box"] == 0
                                and c["plates_outside_reel_box"] == 0 and c["plates_outside_caption_band"] == 0
                                and profile["reel_box"] == list(REEL) and profile["graphics_box"] == list(GRAPHICS))
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: measure.py <repository root>")
    main(Path(sys.argv[1]))
