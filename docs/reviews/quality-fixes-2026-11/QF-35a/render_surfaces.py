"""QF-35a: every frozen design surface drawn by one code tree on real run inputs, as pixel hashes.

    python render_surfaces.py <tree> <dev checkout holding output/run_*> <out.json> [--brand second|second-colours|third]
                              [--keep <dir>]
                              [--only map,numbers,...] [--per-kind N] [--jobs <file>] [--list-jobs <file>]

Run it on the base tree and on this row's tree: with the default brand the two JSON files must hold the same hash for
every surface (0 differing pixels; compare.py). With --brand second the frozen designs draw the test brand of
tests/test_qf35a_role_tokens.py (every palette colour with red and blue swapped, Montserrat in the display role,
another masthead and end-card lines) through the same code, which a tree without the brand configuration cannot do;
--brand second-colours is the same brand with today's display face (the cards refuse a wider display face for fit).
--brand third (the port, after the round-1 review) moves every channel of every palette colour, greys included
((r, g, b) -> (g + 12, r, b - 12), clipped: red and green swapped and greys tinted; no colour lands on a default
colour, and the caption text colours keep their contrast on the plate), and names Cinzel in the display,
data (so the reconstruction badge) and 16:9 caption roles, with another masthead and end-card lines.

Every input is a real run's own, read-only from <dev checkout> (operator rule: renders use real run data), through
the freeze-panel catalogue (docs/reviews/freeze-panel-2026-11-04/fixtures) and the graphics' recorded inputs
(<run>/graphics/graphic_*.mp4.lineage.json), as QF-21's evidence renders read them:
- maps, number cards, timelines, scales: every development run that recorded one (one run per topic for each kind),
  9:16, and 16:9 for the first three; a route from the one run that carries one (its first stop to a second located
  place, labelled new: its own second stop is not in the offline place cache), both formats;
- cross-section: no run carries one; the committed test spec (tests/test_diagrams_2026_11_02.py SECTION), labelled;
- then-and-now: the catalogue's two accepted pairs, both formats;
- loop bridge and end screen: the catalogue's nine end-card cases, every frame;
- covers: the catalogue's five heroes and payloads (Reel cover, grid tile, YouTube thumbnail);
- carousels, Facebook gallery and Stories: the three runs that kept per-slide recipes;
- hook, chapter and myth cards: the tests' committed card fields (no run has a checked card field) on two real
  heroes, every frame, signed with the tests' throwaway key in this process only;
- photo callouts: no run drew one; the operator's reference pair (tests/test_callouts_v4_2026_11_03.py) on a real
  hero (the Amber Room's: on the unfinished obelisk's bright hero the door label measures 1.05:1 and the renderer refuses
  it on both trees, LowContrast), labelled;
- captions: the catalogue's caption narrations built into caption files (the ASS text, which carries every colour
  and face) and one libass frame per 9:16 case at its first burst; added in the port: one libass frame per 16:9 case,
  and each case again with the AI-reconstruction badge on screen from the start (ASS and frame, both formats);
- dense timeline (added in the port: the five-event date column, src/timeline_dense.py): no run recorded five events;
  the committed five-event test events (tests/test_qf20_map_geometry.py, Rhodes) on the first timeline run's photo,
  both formats, labelled.

A graphic clip is hashed frame by frame where it is encoded (the renderer's _encode is replaced by a hasher), so the
encoder adds nothing. Network is blocked; nothing is written outside a temporary folder and <out.json> / --keep.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

FIX = Path("docs") / "reviews" / "freeze-panel-2026-11-04" / "fixtures"
GRAPHIC_KINDS = {"map": "map", "numbers": "stat_cards", "timeline": "timeline", "scale": "scale_fact"}
WIDE_FIRST = 3          # how many runs of each graphic kind are also drawn 16:9
CARD_FIELD_KEY = "test-only-fact-record-key-0123456789abcdef"   # the tests' key (tests/test_freeze_cards_2026_11_04)


def _offline(*_a, **_k):
    raise RuntimeError("render_surfaces.py is offline")


class Hasher:
    """sha256 over the frames' RGB pixels (size first), with the frame count and the last frame kept."""

    def __init__(self):
        self.h, self.n, self.last = hashlib.sha256(), 0, None

    def add(self, im) -> None:
        rgb = im.convert("RGB")
        self.h.update(f"{rgb.width}x{rgb.height};".encode())
        self.h.update(rgb.tobytes())
        self.n += 1
        self.last = rgb

    def result(self) -> dict:
        return {"sha256": self.h.hexdigest(), "frames": self.n}


def card_share(fx, case) -> str:
    """The 9:16 end card's line for a fixture case, as the composer makes it: script_engine.end_card_share of the run's
    verified script (the writer's share half when it passes its checks, else the fallback's), or, without the run's
    script, end_card.card_text of the recorded share line."""
    from src.end_card import card_text
    from src.script_engine import end_card_share
    p = fx.run_file(case["run"], "verified_script.json")
    if p.is_file():
        return end_card_share(json.loads(p.read_text(encoding="utf-8")))[0]
    return card_text(case["share_line"])


def second_brand(brand, display: str | None = "Montserrat"):
    base = brand.DEFAULT_BRAND
    palette = {k: (v if isinstance(v, str) else (v[2], v[1], v[0])) for k, v in base.palette.items()}
    return base.derive("Second test brand", palette=palette, fonts={"display": display or base.fonts["display"]},
                       text={"masthead": "SECOND TEST BRAND", "follow": "FOLLOW FOR THE NEXT ONE",
                             "handoff": "The next one is waiting."})


def third_brand(brand):
    """Every channel of every palette colour moved (greys too), Cinzel in the display, data and 16:9 caption roles."""
    base = brand.DEFAULT_BRAND

    def move(c):
        return (min(255, c[1] + 12), c[0], max(0, c[2] - 12))
    palette = {k: (v if isinstance(v, str) else move(v)) for k, v in base.palette.items()}
    return base.derive("Third test brand", palette=palette,
                       fonts={"display": "Cinzel", "data": "Cinzel", "caption_wide": "Cinzel"},
                       text={"masthead": "THIRD TEST BRAND", "follow": "FOLLOW FOR THE NEXT FIND",
                             "handoff": "The next find is waiting."})


class AttributedSurfaces(dict):
    """result["surfaces"] (cycle 4, lane BOARD): each surface written takes the omission ledger's rows (src/refusal.py
    omit, inside the job's collecting() round) recorded since the job's previous surface, i.e. the rows drawn into it
    (a map without a neighbour's name, a timeline without an event), as result["surface_omissions"][name]; a refused
    surface takes the rows recorded while it was tried. A tree without the ledger records nothing. A record only:
    nothing drawn changes."""

    def __init__(self, result: dict):
        super().__init__()
        self.result, self.seen = result, 0

    def new_job(self) -> None:
        """A job opens its own ledger: its rows count from 0."""
        self.seen = 0

    def __setitem__(self, name, rec) -> None:
        super().__setitem__(name, rec)
        try:
            from src.refusal import omissions
        except ImportError:
            return
        rows = omissions()
        new, self.seen = rows[self.seen:], len(rows)
        if new:
            self.result.setdefault("surface_omissions", {})[name] = [dict(r) for r in new]


def copy_swap(payload: dict, entry: dict) -> dict:
    """A run payload with its words replaced by a copy-set entry's (cycle 3, portability): title, thumbnail lines,
    YouTube title, carousel and Story slides (cycled over the run's own count, so every recipe keeps its slide),
    share line, poll, narration and emphasis words. Fields the entry does not give keep the run's own; the figures a
    cover may carry are the entry's own and its narration says them, so the figure checks read real support."""
    import copy as _copy
    p = _copy.deepcopy(payload)
    for k in ("title", "youtube_title", "share_line", "poll", "narration_script", "hook_sentence", "closing_sentence",
              "emphasis_words", "caption"):
        if k in entry:
            p[k] = _copy.deepcopy(entry[k])
    if "thumbnail" in entry:
        p["thumbnail"] = {**(p.get("thumbnail") or {}), **entry["thumbnail"]}
    for k in ("carousel_slides", "story_slides"):
        src = entry.get(k) or []
        if src and p.get(k):
            p[k] = [{**s, **src[i % len(src)]} for i, s in enumerate(p[k])]
    return p


def copy_words(words: list, entry: dict) -> list:
    """The narration's timed words with the entry's narration words in their place, cycled (each word keeps its own
    start and duration, so the caption timing is the run's)."""
    said = str(entry.get("narration_script") or "").split()
    return [{**w, "word": said[i % len(said)]} for i, w in enumerate(words)] if said else words


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tree", type=Path)
    ap.add_argument("dev", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--brand", choices=("default", "second", "second-colours", "third"), default="default")
    ap.add_argument("--keep", type=Path)
    ap.add_argument("--only", default="")
    ap.add_argument("--per-kind", type=int, default=0, help="at most this many runs per graphic kind (0: all)")
    ap.add_argument("--jobs", type=Path, help="a file of job names (one per line, as the result's `seconds` keys): "
                    "run only those (the round-2 re-render of the surfaces a change reaches plus a random sample)")
    ap.add_argument("--list-jobs", type=Path, help="write the job names this run would render (one per line, in "
                    "order, after --only / --per-kind / --jobs) to this file and render nothing (board.py --workers "
                    "shards the list)")
    ap.add_argument("--copy-set", type=Path, help="cycle 3 (portability): a copy set (JSON list, see copy_swap) "
                    "whose words replace the runs' own on the surfaces whose copy comes from a run (covers, carousels, "
                    "Stories, end screen / card / bridge share lines, captions); the other surfaces draw as they are "
                    "and the result's copy_set record lists them")
    a = ap.parse_args()
    tree, dev = a.tree.resolve(), a.dev.resolve()
    copy_set = json.loads(a.copy_set.read_text(encoding="utf-8")) if a.copy_set else None
    only = set(filter(None, a.only.split(",")))
    jobs = set(filter(None, a.jobs.read_text(encoding="utf-8").splitlines())) if a.jobs else None
    os.environ.update(FONT_SIZE="80", PYTHONIOENCODING="utf-8", RR_FIXTURE_REPO=str(dev))
    socket.create_connection = _offline
    socket.socket.connect = _offline
    sys.path.insert(0, str(tree))
    sys.path.insert(0, str(tree / FIX))
    import fixtures as fx
    from PIL import Image

    from src import brand, callouts, graphics, then_now
    def configure_evidence(cfg):
        """Cycle 0 round 8 (ruling 6): a brand is validated when configured (BrandRefused). The board renders the test
        brands as evidence even where the fit corpus refuses them (every surface still refuses on its own floors),
        and records the corpus's verdict; a tree without validation takes the brand as before."""
        try:
            brand.configure(cfg, validate=False)
        except TypeError:
            brand.configure(cfg)
            return None
        from src.brand_system import validate_display
        return validate_display(cfg)
    brand_validation = None
    if a.brand == "third":
        brand_validation = configure_evidence(third_brand(brand))
    elif a.brand != "default":   # second-colours: the test brand with today's display face (the cards' fit)
        brand_validation = configure_evidence(second_brand(brand, "Montserrat" if a.brand == "second" else None))
    from src import geo
    geo.CACHE_FILE = dev / "data" / "geo_cache.json"     # read-only: the dev checkout's located places

    src_hash = hashlib.sha256()
    for f in sorted((tree / "src").glob("*.py")):
        src_hash.update(f.name.encode() + f.read_bytes().replace(bytes([13, 10]), bytes([10])))
    result: dict = {"src_sha256": src_hash.hexdigest()[:16],
                    "brand": a.brand, "errors": {}, "seconds": {}}
    result["surfaces"] = AttributedSurfaces(result)
    if a.keep:
        a.keep.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="qf35a-"))
    try:   # cycle 3 (lane BOARD): run through board.py (run_surfaces.py), the data files every job reads are recorded
        import board_inputs   # (board_inputs.py: the run's input union, for the board's INPUT_HASHES.json)
        board_inputs.install([dev, board_inputs.FRAME_CACHE], skip=[work] + ([a.keep] if a.keep else []))
    except ImportError:   # run on its own: no record, the same pixels
        board_inputs = None
    clips: dict[str, Hasher] = {}
    listed: list[str] | None = [] if a.list_jobs else None
    swapped: set[str] = set()   # cycle 3: the job groups whose copy the --copy-set replaced

    def encode(frames, out, n, width=None, height=None, **_k):
        hz = clips.setdefault(Path(out).name, Hasher())
        for im in frames:
            hz.add(im)
        if hz.last is not None:
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            hz.last.save(Path(out).with_suffix(".jpg"), quality=90)
        return Path(out)

    graphics._encode = encode
    callouts._encode = encode
    then_now._encode = encode

    def save(name: str, hz: Hasher) -> None:
        result["surfaces"][name] = hz.result()
        if a.keep and hz.last is not None:
            hz.last.save(a.keep / f"{name}.png")

    def frames_of(name: str, frames) -> None:
        hz = Hasher()
        for im in frames:
            hz.add(im)
        save(name, hz)

    def clip(name: str, out: Path) -> None:
        hz = clips.pop(Path(out).name, None)
        if hz is None:
            raise RuntimeError(f"{name}: nothing was encoded")
        save(name, hz)

    def refused(e) -> dict:
        """A recorded refusal (src/refusal.py: the one refusal type): its class, code and reason."""
        return {"refused": str(getattr(e, "reason", None) or e)[:200], "class": type(e).__name__,
                "code": str(getattr(e, "code", "") or "")}

    def job(group: str, name: str, fn) -> None:
        if only and group not in only:
            return
        if jobs is not None and name not in jobs:
            return
        if listed is not None:   # --list-jobs: the name only, nothing drawn
            listed.append(name)
            return
        t0 = time.perf_counter()
        result["surfaces"].new_job()
        # freeze lane A cycle 3: an omission ledger round each job, as graphics.make_all opens one (a map drawn
        # without its scale bar, a neighbour's name left out: recorded, the surface drawn); a tree without the
        # ledger (before SYS batch 3) renders as before
        try:
            from src.refusal import collecting
        except ImportError:
            collecting = None
        ctx = collecting() if collecting is not None else contextlib.nullcontext([])
        try:
            with ctx as omitted:
                fn()
            if omitted:
                result.setdefault("omissions", {})[name] = [dict(r) for r in omitted]
        except Exception:  # noqa: BLE001  one failed case is recorded (both trees must fail alike), the rest render
            result["errors"][name] = traceback.format_exc()[-1200:]
        result["seconds"][name] = round(time.perf_counter() - t0, 2)
        a.out.write_text(json.dumps(result, indent=1, sort_keys=True), encoding="utf-8", newline="\n")

    # ------------------------------------------------------------------ graphics from their recorded inputs
    from src.art_direction import use_palette
    from src.photo_grade import using

    def lineage(run: Path, name: str) -> dict | None:
        p = run / "graphics" / f"graphic_{name}.mp4.lineage.json"
        return json.loads(p.read_text(encoding="utf-8"))["settings"]["inputs"] if p.is_file() else None

    def palette(run: Path):
        p = run / "palette.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None

    def stat_photo(run: Path):
        prov = run / "provenance.json"
        if not prov.is_file():
            return None
        try:
            src = json.loads(prov.read_text(encoding="utf-8"))
            for item in (src.get("scenes") or src.get("items") or []):
                p = Path(str(item.get("source") or item.get("photo") or ""))
                if p.suffix.lower() in (".jpg", ".jpeg", ".png") and p.is_file():
                    return p
        except (ValueError, AttributeError):
            return None
        return None

    def topic_of(run: Path) -> str:
        return run.name[4:].rsplit("_", 1)[0] if run.name[-1].isdigit() else run.name[4:].rsplit("_", 2)[0]

    runs = sorted(p for p in (dev / "output").glob("run_*") if (p / "graphics").is_dir())
    picked: dict[str, list[Path]] = {k: [] for k in GRAPHIC_KINDS}
    for kind, field in GRAPHIC_KINDS.items():
        seen = set()
        for run in runs:
            inp = lineage(run, kind)
            if inp is None or topic_of(run) in seen:
                continue
            if kind != "map" and not (inp.get("payload") or {}).get(field):
                continue
            if kind == "map" and not inp.get("place"):
                continue
            seen.add(topic_of(run))
            picked[kind].append(run)
        if a.per_kind:
            picked[kind] = picked[kind][: a.per_kind]
    result["graphic_runs"] = {k: [r.name for r in v] for k, v in picked.items()}

    def graphic(kind: str, run: Path, w: int, h: int, tag: str) -> None:
        inp = lineage(run, kind)
        p = inp["payload"]
        use_palette(palette(run))
        out = work / f"{kind}_{run.name}_{tag}.mp4"
        with using(0.0):
            if kind == "map":
                made = graphics.render_map(inp["place"], out, width=w, height=h,
                                           narration=p.get("narration_script") or "")
            elif kind == "numbers":
                made = graphics.render_stat_cards(p["stat_cards"], out, stat_photo(run), width=w, height=h,
                                                  narration=p.get("narration_script") or "")
            elif kind == "timeline":
                made = graphics.render_timeline(p["timeline"], out, width=w, height=h, photo=stat_photo(run))
            else:
                made = graphics.render_scale(p["scale_fact"], out, width=w, height=h)
        if made is None:
            raise RuntimeError(f"{kind} {run.name}: the renderer drew nothing")
        clip(f"{kind}__{run.name[4:]}__{tag}", out)

    for kind, rs in picked.items():
        for i, run in enumerate(rs):
            for w, h, tag in ((1080, 1920, "916"), (1920, 1080, "169"))[: 2 if i < WIDE_FIRST else 1]:
                job(kind, f"{kind} {run.name} {tag}", lambda kind=kind, run=run, w=w, h=h, tag=tag:
                    graphic(kind, run, w, h, tag))

    route_runs = [r for r in sorted((dev / "output").glob("run_*")) if (r / "verified_script.json").is_file()
                  and json.loads((r / "verified_script.json").read_text(encoding="utf-8")).get("route")]
    for run in route_runs[:1]:
        for w, h, tag in ((1080, 1920, "916"), (1920, 1080, "169")):
            def route(run=run, w=w, h=h, tag=tag):
                p = json.loads((run / "verified_script.json").read_text(encoding="utf-8"))
                use_palette(palette(run))
                out = work / f"route_{tag}.mp4"
                # new: the run's second stop was never located offline (it is not in the dev checkout's place
                # cache, and this script makes no lookup), so the route runs from the run's own first stop to a
                # second located place of the same country, each stop labelled with its own name
                stops = [{"place": "Catherine Palace", "country": "Russia", "label": "Catherine Palace"},
                         {"place": "Kholat Syakhl", "country": "Russia", "label": "Kholat Syakhl"}]
                if graphics.render_route(stops, out, width=w, height=h,
                                         narration=p.get("narration_script") or "") is None:
                    raise RuntimeError("render_route drew nothing")
                clip(f"route__{run.name[4:]}_new_stops__{tag}", out)
            job("route", f"route {run.name} {tag}", route)

    def dense(w, h, tag):
        # new: no run recorded five events; the stack's committed five-event test events on a real run's photo
        events = [{"year": 1300, "label": "The Knights of St John reach Rhodes", "approximate": True},
                  {"year": 1522, "label": "Suleiman the Magnificent takes the city"},
                  {"year": 1523, "label": "The knights sail for Malta"},
                  {"year": 1912, "label": "Italy takes the Dodecanese"},
                  {"year": 1988, "label": "The Medieval City of Rhodes is listed"}]
        run = picked["timeline"][0]
        use_palette(palette(run))
        out = work / f"dense_{tag}.mp4"
        with using(0.0):
            if graphics.render_timeline(events, out, width=w, height=h, photo=stat_photo(run)) is None:
                raise RuntimeError("render_timeline drew nothing")
        clip(f"timeline_dense__test_events_{run.name[4:]}__{tag}", out)
    for w, h, tag in ((1080, 1920, "916"), (1920, 1080, "169")):
        job("timeline_dense", f"timeline_dense {tag}", lambda w=w, h=h, tag=tag: dense(w, h, tag))

    def section(w, h, tag):
        # new: no run carries a cross-section
        from tests.test_diagrams_2026_11_02 import SECTION
        use_palette(None)
        out = work / f"section_{tag}.mp4"
        if graphics.render_cross_section(SECTION, out, width=w, height=h) is None:
            raise RuntimeError("render_cross_section drew nothing")
        clip(f"cross_section__test_spec__{tag}", out)
    for w, h, tag in ((1080, 1920, "916"), (1920, 1080, "169")):
        job("section", f"cross-section {tag}", lambda w=w, h=h, tag=tag: section(w, h, tag))

    # ------------------------------------------------------------------ then and now
    use_palette(None)
    for case in fx.load("thennow"):
        for w, h, tag in ((1080, 1920, "916"), (1920, 1080, "169")):
            def thennow(case=case, w=w, h=h, tag=tag):
                out = work / f"tn_{case['topic']}_{tag}.mp4"
                then_now.render(fx.path(case["old"]), int(case["old_year"]), fx.path(case["new"]),
                                int(case["new_year"]), out, w, h)
                clip(f"thennow__{case['topic']}__{tag}", out)
            job("thennow", f"thennow {case['topic']} {tag}", thennow)

    # ------------------------------------------------------------------ loop bridge and end screen
    from src import end_card
    try:   # cycle 3 (lane E): each bridge as the composer ships it (board_motion.shipped_bridge, its gate ladder)
        import board_motion
    except ImportError:   # run on its own (QF-35a A/B on two trees): the bridges as drawn before, recorded so
        board_motion = None

    def bridge(name: str, last, first, share: str, follow: bool, case: dict) -> None:
        """A loop bridge surface: through the composer's ladder with the run's own seam motion where its record
        gives it (board_motion.run_loop_motion), recorded in "bridge_ladder"; every rung failing is a refusal
        (the Reel ships no bridge)."""
        if board_motion is None:
            result.setdefault("bridge_ladder", {})[name] = {"variant": "not laddered (render_surfaces.py run "
                                                            "without the board's board_motion)"}
            frames_of(name, end_card.loop_bridge(last, first, share, {}, **({"line": False, "follow": True}
                                                                             if follow else {})))
            return
        motion, source = board_motion.run_loop_motion(fx.path(case["run"]))
        stats: dict = {}
        try:
            frames = board_motion.shipped_bridge(last, first, share, stats, follow=follow, motion=motion,
                                                 motion_source=source)
        except board_motion.BridgeNotShipped as e:
            result["surfaces"][name] = {"refused": str(e)[:200], "class": "BridgeNotShipped", "code": "not_shipped"}
            frames = None
        if frames is not None:
            frames_of(name, frames)
        result.setdefault("bridge_ladder", {})[name] = {
            "variant": stats.get("variant"), "ladder": stats.get("ladder"), "motion_source": source,
            "seam": board_motion.seam_reading(stats)}

    for i, case in enumerate(fx.load("endcard")):
        def endcard(case=case, i=i):
            share = case["share_line"]
            card_line = None
            if copy_set:   # cycle 3: the copy set's share line, through the composer's own end_card_share
                from src.end_card import card_text
                from src.script_engine import end_card_share
                entry = copy_set[i % len(copy_set)]
                share = entry["share_line"]
                p = fx.run_file(case["run"], "verified_script.json")
                card_line = (end_card_share(copy_swap(json.loads(p.read_text(encoding="utf-8")), entry))[0]
                             if p.is_file() else card_text(share))
                swapped.add("endcard")
            last_src = case.get("last_frame", {}).get("from") or case.get("last")
            last = (Image.open(fx.frame(last_src, last=True)) if str(last_src).endswith(".mp4")
                    else Image.open(fx.path(last_src))).convert("RGB")
            if case.get("format") == "9:16" and case.get("first_frame"):
                first = Image.open(fx.frame(case["first_frame"]["from"], 0.0)).convert("RGB")
                bridge(f"bridge__{i}_{case['topic']}", last, first, share, False, case)
                bridge(f"bridge_follow__{i}_{case['topic']}", last, first, share, True, case)
                # what the composer passes the card (script_engine.end_card_share: the share half, the card says
                # FOLLOW itself), never the raw share-and-follow line (freeze lane E cycle 1: the board drew 88
                # characters and no follow line where a real run draws 56 and FOLLOW FOR THE NEXT RELIC)
                frames_of(f"endcard__{i}_{case['topic']}", end_card.frames(
                    last, card_line if card_line is not None else card_share(fx, case), {}))
            else:
                name = f"endscreen__{i}_{case['topic']}"
                if board_motion is not None:   # cycle 3 (lane E): the run's 16:9 master's last frame where it has
                    last, cls, why = board_motion.wide_last(fx, case)   # one, else what stands in, said
                    result.setdefault("wide_grounds", {})[name] = {"index": i, "class": cls, "why": why}
                elif last.width < last.height:
                    from PIL import ImageOps
                    last = ImageOps.fit(last, (1920, 1080), Image.LANCZOS)
                frames_of(name, end_card.frames(last, share, {}))
        job("endcard", f"endcard {i} {case['topic']}", endcard)

    # ------------------------------------------------------------------ covers
    from src.visual_engine import VisualEngine
    covers_cases = fx.load("covers")
    for case in covers_cases:
        def cover(case=case):
            hero = fx.path(case["hero"]["photo"])
            payload = fx.payload(case)
            if copy_set:   # cycle 3: the copy set's cover words on the run's own hero
                payload = copy_swap(payload, copy_set[covers_cases.index(case) % len(copy_set)])
                swapped.add("covers")
            d = work / f"cover_{case['topic']}"
            # cycle 0 round 8 (ruling 6; panel AD MG-3): each surface drawn and refused on its own, so a YouTube
            # refusal never takes the Reel cover and the grid tile with it: a refusal (src/refusal.LayoutError) is a
            # surface record {"refused", "class", "code"}; any other error is recorded under the surface's own name
            from src.refusal import LayoutError
            try:
                reel, grid = VisualEngine().generate_thumbnails(hero, payload, d, variants=False,
                                                                style=case["hero"].get("style") or "badge")
                for tag, p in (("reel", reel), ("grid", grid)):
                    frames_of(f"cover__{case['topic']}__{tag}", [Image.open(p)])
            except LayoutError as e:
                for tag in ("reel", "grid"):
                    result["surfaces"][f"cover__{case['topic']}__{tag}"] = refused(e)
            except Exception:  # noqa: BLE001
                result["errors"][f"cover__{case['topic']}__reel"] = traceback.format_exc()[-1200:]
            try:
                frames_of(f"cover__{case['topic']}__youtube",
                          [Image.open(VisualEngine().generate_youtube_thumbnail(hero, payload, d / "yt.jpg"))])
            except LayoutError as e:
                result["surfaces"][f"cover__{case['topic']}__youtube"] = refused(e)
            except Exception:  # noqa: BLE001
                result["errors"][f"cover__{case['topic']}__youtube"] = traceback.format_exc()[-1200:]
        job("covers", f"cover {case['topic']}", cover)

    # ------------------------------------------------------------------ carousels, gallery, Stories
    from src.multi_format import MultiFormatGenerator
    from src.refusal import LayoutError

    def draw_social(gen, kind, r, rec, title_px=None):
        """One recorded frame redrawn as production draws it: a Story at its deck's one title size (the recipe's,
        else story_title_px over the deck); a carousel slide with the run's lead unit and its deck window (round 8)."""
        if kind == "story":
            # cycle 3 (lane D): poll {} as generate_story_sequence passes it since QF-80 (no frame asks the poll);
            # an older recipe's poll would turn the reveal into a question frame and step its title down
            return gen._render_story_frame(Path(r["photo"]), r["slide"], int(r["index"]), int(r["count"]),
                                           {}, tight=bool(rec.get("tight")),
                                           title_px=int(r.get("title_px") or title_px or 0) or None)
        return gen._render_carousel_slide(
            r["archetype"], Path(r["photo"]), Path(r["photo_next"]), r["card"], int(r["index"]),
            int(r["total"]), r.get("poll") or {}, r.get("share") or "", bool(r.get("facebook")),
            tight=bool(rec.get("tight")), subject=r.get("subject") or "",
            heading_frame=int(r.get("heading_frame") or 2), lead=r.get("lead"),
            window=int(r.get("window") or 0))

    def swap_recipe(r: dict, entry: dict | None) -> dict:
        """A recorded slide recipe with the copy set's words (cycle 3): the card's heading / body, the Story's lines,
        the poll and the share prompt; the run's lead unit is dropped (it names the run's own figure)."""
        if not entry or not r:
            return r
        r = dict(r)
        k = int(r.get("index") or 0)
        cards, stories = entry.get("carousel_slides") or [], entry.get("story_slides") or []
        if r.get("card") is not None and cards:
            r["card"] = {**r["card"], **cards[k % len(cards)]}
            r["lead"] = None
        if r.get("slide") is not None and stories:
            r["slide"] = {**r["slide"], **stories[k % len(stories)]}
        if r.get("poll") and entry.get("poll"):
            r["poll"] = entry["poll"]
        if r.get("share") and entry.get("share_prompt"):
            r["share"] = entry["share_prompt"]
        swapped.add("social")
        return r

    for n_case, case in enumerate(c for c in fx.load("social") if c.get("exact_recipes")):
        run = fx.path(case["run"])
        entry = copy_set[n_case % len(copy_set)] if copy_set else None

        def social(run=run, entry=entry):
            from src import multi_format as mf
            gen = MultiFormatGenerator()
            retired_frames = tuple(getattr(mf, "RETIRED_STORY_FRAMES", ()))
            for sub, kind in (("carousel", "carousel"), ("carousel_fb", "fb"), ("stories", "story")):
                rec_file = run / sub / "still_layout.json"
                if not rec_file.is_file():
                    continue
                recs = json.loads(rec_file.read_text(encoding="utf-8"))
                # cycle 3 (lane D verification): only what production delivers today. A retired Story frame (the
                # poll's question, QF-80) is recorded, never drawn; the Story's count is the frames it still makes
                for frame_name in sorted(recs):
                    if kind == "story" and frame_name in retired_frames:
                        result.setdefault("retired", {})[f"social__{run.name[4:]}__{kind}_{Path(frame_name).stem}"] = (
                            "retired frame kind (multi_format.RETIRED_STORY_FRAMES): production no longer makes or "
                            "posts it; not drawn")
                live = {k: v for k, v in recs.items() if not (kind == "story" and k in retired_frames)}
                ordered = [r for r in (swap_recipe(v.get("recipe") or {}, entry) for v in live.values()) if r]
                ordered.sort(key=lambda r: int(r.get("index", 0)))
                title_px = None
                current: dict[int, str] = {}
                if kind == "story":   # cycle 0 round 8 (panel AD E1): the Story's ONE title size, as production
                    # passes it (multi_format.py: story_title_px over the Story's slides, poll {}); a recipe that
                    # recorded its size keeps it. Cycle 3: over the frames production still makes
                    slides = [r["slide"] for r in ordered]
                    title_px = mf.story_title_px(slides, poll={}) if slides else None
                elif ordered and hasattr(MultiFormatGenerator, "sequence_archetypes"):
                    # cycle 3: every slide through today's archetype routing (a recorded archetype can be stale:
                    # Richat's slides 4 and 5 recorded "comparison"); a recorded drawing slide keeps its poster
                    routed = MultiFormatGenerator.sequence_archetypes([r["card"] for r in ordered])
                    current = {int(r["index"]): a for r, a in zip(ordered, routed)}
                for frame_name, rec in sorted(live.items()):
                    r = swap_recipe(rec.get("recipe") or {}, entry)
                    if not r:
                        continue
                    key = f"social__{run.name[4:]}__{kind}_{Path(frame_name).stem}"
                    if kind == "story" and len(ordered) != int(r.get("count") or 0):
                        r = dict(r, count=len(ordered))
                    if kind != "story" and r.get("archetype") != "diagram":
                        now = current.get(int(r.get("index") or 0), r.get("archetype"))
                        if now != r.get("archetype"):
                            result.setdefault("rerouted", {})[key] = {"recorded": r.get("archetype"), "current": now}
                            r = dict(r, archetype=now)
                    try:   # round 8: one frame's refusal never takes the run's other frames with it
                        im, lay = draw_social(gen, kind, r, rec, title_px)
                    except LayoutError as e:   # a refusal: the one record shape {"refused", "class", "code"}
                        result["surfaces"][key] = refused(e)
                        continue
                    except Exception:  # noqa: BLE001
                        result["errors"][key] = traceback.format_exc()[-1200:]
                        continue
                    meta = getattr(lay, "meta", None) or {}
                    if "masthead" in meta:   # cycle 4 (BC): the frame's own masthead record (drawn or why not)
                        result.setdefault("masthead_records", {})[key] = dict(meta["masthead"])
                    if kind == "story":   # cycle 3: the frame's own layout record (the size it drew at)
                        result.setdefault("story_title_px", {})[key] = {
                            "drawn": meta.get("title_px"), "story": meta.get("story_title_px"),
                            "passed": int(r.get("title_px") or title_px or 0) or None}
                    frames_of(key, [im])
        job("social", f"social {run.name}", social)

    # ------------------------------------------------------------------ hook, chapter and myth cards
    def cards():
        from src import config
        from src import design_cards as dc
        config.FACT_RECORD_KEY = CARD_FIELD_KEY
        from tests import test_freeze_cards_2026_11_04 as fc
        heroes = {h["topic"]: fx.path(h["photo"]) for h in fx.section("cards")["hero_photos"]}
        grounds = {"richat": fx.still("richat_structure_sahara_eye_01_commons_960.jpg"),
                   "amber": heroes["amber_room_heist"], "obelisk": heroes["unfinished_obelisk_aswan"]}
        richat = fc.real_sheet("richat_structure_sahara_eye")
        makers = {
            "myth_richat": lambda ph, size: fc.richat_myth(richat, photo=ph, size=size),
            "myth_long_et": lambda ph, size: fc.plan_myth(fc.LONG_ET, size=size, photo=ph),
            "chapter_act1": lambda ph, size: fc.chapter_plan(size, 1, fc.ACTS, fc.RICHAT_ACTS[0], photo=ph),
            "chapter_act4": lambda ph, size: fc.chapter_plan(size, 4, fc.ACTS, fc.RICHAT_ACTS[3], photo=ph),
            "hook": lambda ph, size: fc.hook_plan(ph) if size == fc.TALL else None,
        }
        for name, make in makers.items():
            for g, ph in grounds.items():
                for size, tag in ((fc.TALL, "916"), (fc.WIDE, "169")):
                    key = f"card__{name}__{g}__{tag}"
                    try:
                        plan = make(ph, size)
                    except dc.CardRefused as e:   # recorded, never hidden: both trees must refuse alike
                        result["surfaces"][key] = refused(e)
                        continue
                    if plan is None:
                        continue
                    frames_of(key, (dc.frame_at(plan, k) for k in range(plan.frames)))
    job("cards", "cards", cards)

    # ------------------------------------------------------------------ photo callouts
    def callout(w, h, tag):
        # new: no run drew a callout
        from tests.test_callouts_v4_2026_11_03 import REF_DOOR, REF_WALL
        hero = next(fx.path(hh["photo"]) for hh in fx.section("cards")["hero_photos"]
                    if hh["topic"] == "amber_room_heist")
        kept = callouts.declutter(callouts.clear_of_captions([REF_DOOR, REF_WALL], w, h), w, h)
        if kept is None:
            raise RuntimeError("the reference pair has no layout")
        out = work / f"callout_{tag}.mp4"
        callouts.render(hero, kept, out, width=w, height=h, reveal=[0.6, 2.2])
        clip(f"callouts__reference_pair__{tag}", out)
    job("callouts", "callouts 916", lambda: callout(1080, 1920, "916"))

    # ------------------------------------------------------------------ captions
    def captions():
        from src import video_composer as vc

        def esc(p: Path) -> str:
            return str(p.resolve()).replace("\\", "/").replace(":", "\\:")

        for n_case, case in enumerate(fx.load("captions")):
            words = json.loads(Path(fx.path(case["words"])).read_text(encoding="utf-8"))
            if copy_set:   # cycle 3: the copy set's narration on the run's own word timing
                entry = copy_set[n_case % len(copy_set)]
                words = copy_words(words, entry)
                case = {**case, "emphasis_words": entry.get("emphasis_words")}
                swapped.add("captions")
            wide = case.get("format") == "16:9"
            t = float(words[min(3, len(words) - 1)]["start"]) + 0.05
            # the port adds a libass frame for 16:9 and the badge case (reconstruction on screen from the start)
            end = float(words[-1]["start"]) + float(words[-1].get("dur", 1.0))
            for suffix, recon in (("", None), ("__badge", [(0.0, end)])):
                ass = work / f"cap_{case['topic']}{suffix}.ass"
                kw = {"width": 1920, "height": 1080} if wide else {}
                vc.VideoComposer().build_subtitles(words, ass, emphasis=case.get("emphasis_words") or None,
                                                   reconstruction_intervals=recon, **kw)
                text = ass.read_bytes()
                result["surfaces"][f"captions__{case['topic']}{suffix}__ass"] = {
                    "sha256": hashlib.sha256(text).hexdigest()}
                png = work / f"cap_{case['topic']}{suffix}.png"
                size = "1920x1080" if wide else "1080x1920"
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x404850:s={size}:r=30",
                                "-vf", f"setpts=PTS+{t:.3f}/TB,subtitles='{esc(ass)}':fontsdir='{esc(tree / 'fonts')}'",
                                "-frames:v", "1", "-threads", "1", str(png)], check=True, capture_output=True,
                               timeout=300)
                frames_of(f"captions__{case['topic']}{suffix}__frame", [Image.open(png)])
    job("captions", "captions", captions)

    shutil.rmtree(work, ignore_errors=True)
    if listed is not None:
        a.list_jobs.write_text("".join(f"{n}\n" for n in listed), encoding="utf-8", newline="\n")
        print(len(listed), "jobs listed")
        return
    result["brand_validation"] = brand_validation   # round 8: the fit corpus's verdict on the brand (None: default)
    if board_inputs is not None:   # cycle 3: {path: sha256} of every data file this process read (board_inputs.py)
        result["input_files"] = board_inputs.files()
    if copy_set is not None:   # cycle 3: what the copy set replaced, and what drew its own (test or run) words
        result["copy_set"] = {"file": str(a.copy_set), "sha256": hashlib.sha256(a.copy_set.read_bytes()).hexdigest(),
                              "swapped": sorted(swapped),
                              "unswapped": "graphics (recorded run inputs: places, figures, dates), then-and-now, "
                                           "the cards (tests' fields) and the callout (tests' labels) keep their "
                                           "own words"}
    result["count"] = len(result["surfaces"])
    a.out.write_text(json.dumps(result, indent=1, sort_keys=True), encoding="utf-8", newline="\n")
    print(result["count"], "surfaces,", len(result["errors"]), "errors,", round(sum(result["seconds"].values())), "s")


if __name__ == "__main__":
    main()
