"""QF-21 evidence renders: the freeze-scope designs drawn by one code tree on real run inputs.

    python render_evidence.py --code-root <tree> --data-root <checkout with output/, assets/, cache/> --out <dir>

Run once on the base tree and once on the row's tree, then compare_evidence.py. <tree> goes first on sys.path. Every
input is a real run's own (operator rule: renders use real run data): the freeze-panel catalogue's cover, social,
end-card and then-and-now cases (docs/reviews/freeze-panel-2026-11-04/fixtures), and the graphics' recorded inputs
(<run>/graphics/graphic_*.mp4.lineage.json: the payload and, for maps, the located place) for maps, number cards,
timelines and the scale; the one route a run's payload carries. Reads <data-root> only; writes only under --out.
Network is blocked (no live or paid call). Chapter, myth and hook cards are rendered by the freeze-panel cards
script (docs/reviews/freeze-panel-2026-11-04/cards/render_evidence.py), which has its own --code-root.
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path


def _offline(*_a, **_k):
    raise RuntimeError("evidence renders are offline")


MAP_RUNS = ["run_amber_room_heist_1792790550", "run_wieliczka_salt_mine_1792974672", "run_richat_structure_sahara_eye_1793474843"]
NUMBER_RUNS = ["run_amber_room_heist_1792790550", "run_odessa_catacombs_1792970549", "run_unfinished_obelisk_aswan_1792993374"]
TIMELINE_RUNS = ["run_amber_room_heist_1792790550", "run_hal_saflieni_hypogeum_1792949304", "run_roman_self_healing_concrete_1793163918"]
SCALE_RUNS = ["run_wieliczka_salt_mine_1792974672", "run_singing_colossi_of_memnon_1792952878"]
ROUTE_RUN = "run_amber_room_heist_1792801257"
SOCIAL_RUNS = ["run_gol_gumbaz_whispering_gallery_1793278143", "run_nazca_lines_runway_paradox_1793247464",
               "run_richat_structure_sahara_eye_1793272675"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code-root", type=Path, required=True)
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    code, data, out = a.code_root.resolve(), a.data_root.resolve(), a.out.resolve()
    only = set(filter(None, a.only.split(",")))
    out.mkdir(parents=True, exist_ok=True)
    os.environ["FONT_SIZE"] = "80"
    os.environ["RR_FIXTURE_REPO"] = str(data)
    socket.create_connection = _offline
    socket.socket.connect = _offline
    sys.path.insert(0, str(code))
    sys.path.insert(0, str(code / "docs" / "reviews" / "freeze-panel-2026-11-04" / "fixtures"))
    import fixtures as fx
    from PIL import Image
    manifest: dict = {"code_root": str(code), "errors": {}, "files": []}

    def keep(name: str, im) -> None:
        p = out / f"{name}.png"
        im.convert("RGB").save(p)
        manifest["files"].append(p.name)

    def job(design: str, name: str, fn) -> None:
        if only and design not in only:
            return
        try:
            fn()
        except Exception:  # noqa: BLE001  one failed case is recorded, the others still render
            manifest["errors"][name] = traceback.format_exc()[-1500:]
        (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8", newline="\n")

    def poster(clip: Path) -> Image.Image:
        jpg = Path(clip).with_suffix(".jpg")
        if jpg.is_file():
            return Image.open(jpg)
        png = Path(tempfile.mkdtemp()) / "last.png"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-sseof", "-0.1", "-i", str(clip), "-frames:v", "1",
                        "-update", "1", str(png)], check=True, timeout=120)
        return Image.open(png)

    work = Path(tempfile.mkdtemp(prefix="qf21-ev-"))

    # covers: the Reel cover, the grid tile and the YouTube thumbnail
    from src.visual_engine import VisualEngine
    for case in fx.load("covers"):
        def cover(case=case):
            hero = Path(str(case["hero"]["photo"]))
            hero = hero if hero.is_absolute() else data / hero
            payload = fx.payload(case)
            d = work / f"cover_{case['topic']}"
            cover_path, grid = VisualEngine().generate_thumbnails(hero, payload, d, style=case["hero"].get("style") or "badge",
                                                                  variants=False)
            keep(f"cover__{case['topic']}__reel", Image.open(cover_path))
            keep(f"cover__{case['topic']}__grid", Image.open(grid))
            yt = VisualEngine().generate_youtube_thumbnail(hero, payload, d / "yt.jpg")
            keep(f"cover__{case['topic']}__youtube", Image.open(yt))
        job("cover", f"cover {case['topic']}", cover)

    # social: carousel slides and Story frames from the runs' own kept recipes
    from src.multi_format import MultiFormatGenerator
    for run in SOCIAL_RUNS:
        def social(run=run):
            folder = data / "output" / run
            gen = MultiFormatGenerator()
            for sub, kind in (("carousel", "carousel"), ("carousel_fb", "fb"), ("stories", "story")):
                rec_file = folder / sub / "still_layout.json"
                if not rec_file.is_file():
                    continue
                for frame_name, rec in sorted(json.loads(rec_file.read_text(encoding="utf-8")).items()):
                    r = rec.get("recipe") or {}
                    if kind == "story":
                        im, _ = gen._render_story_frame(Path(r["photo"]), r["slide"], int(r["index"]), int(r["count"]),
                                                        r.get("poll") or {}, tight=bool(rec.get("tight")))
                    else:
                        im, _ = gen._render_carousel_slide(
                            r["archetype"], Path(r["photo"]), Path(r["photo_next"]), r["card"], int(r["index"]),
                            int(r["total"]), r.get("poll") or {}, r.get("share") or "", bool(r.get("facebook")),
                            tight=bool(rec.get("tight")), subject=r.get("subject") or "",
                            heading_frame=int(r.get("heading_frame") or 2))
                    keep(f"social__{run[4:]}__{kind}_{Path(frame_name).stem}", im)
        job("social", f"social {run}", social)

    # the loop bridge (9:16) and the 16:9 end screen
    from src import end_card
    for i, case in enumerate(fx.load("endcard")):
        def endcard(case=case, i=i):
            share = case["share_line"]
            stats: dict = {}
            if case.get("format") == "9:16" and case.get("first_frame"):
                first = Image.open(fx.frame(case["first_frame"]["from"], 0.0)).convert("RGB")
                lf = case["last_frame"]
                last = Image.open(fx.frame(lf["from"], last=True)).convert("RGB")
                frames = list(end_card.loop_bridge(last, first, share, stats))
                keep(f"endcard__{i}_{case['topic']}__bridge_hold", frames[len(frames) // 3])   # the share line held
            else:
                last_src = case.get("last") or case.get("last_frame", {}).get("from")
                last = (Image.open(fx.frame(last_src, last=True)) if str(last_src).endswith(".mp4")
                        else Image.open(fx.path(last_src))).convert("RGB")
                if last.width < last.height:   # a Reel topic's 16:9 end screen (the catalogue's "new" cases)
                    from PIL import ImageOps
                    last = ImageOps.fit(last, (1920, 1080), Image.LANCZOS)
                frames = list(end_card.frames(last, share, stats))
                keep(f"endcard__{i}_{case['topic']}__endscreen", frames[-1])
        job("endcard", f"endcard {i} {case['topic']}", endcard)

    # then and now
    from src import then_now
    for case in fx.load("thennow"):
        def thennow(case=case):
            for w, h, tag in ((1080, 1920, "916"), (1920, 1080, "169")):
                clip = then_now.render(fx.path(case["old"]), int(case["old_year"]), fx.path(case["new"]),
                                       int(case["new_year"]), work / f"tn_{case['topic']}_{tag}.mp4", w, h)
                keep(f"thennow__{case['topic']}__{tag}", poster(clip))
        job("thennow", f"thennow {case['topic']}", thennow)

    # graphics from their recorded inputs
    from src import graphics
    from src.art_direction import use_palette

    def lineage(run: str, name: str) -> dict:
        p = data / "output" / run / "graphics" / f"graphic_{name}.mp4.lineage.json"
        if not p.is_file() and name != "map":   # no lineage kept: the run's own verified payload
            script = data / "output" / run / "verified_script.json"
            return {"payload": json.loads(script.read_text(encoding="utf-8"))}
        return json.loads(p.read_text(encoding="utf-8"))["settings"]["inputs"]

    def palette(run: str):
        p = data / "output" / run / "palette.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None

    def stat_photo(run: str):
        prov = data / "output" / run / "provenance.json"
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

    for w, h, tag in ((1080, 1920, "916"), (1920, 1080, "169")):
        for run in MAP_RUNS:
            def gmap(run=run, w=w, h=h, tag=tag):
                inp = lineage(run, "map")
                use_palette(palette(run))
                clip = graphics.render_map(inp["place"], work / f"map_{run}_{tag}.mp4", width=w, height=h,
                                           narration=inp["payload"].get("narration_script") or "")
                keep(f"map__{run[4:]}__{tag}", poster(clip))
            job("map", f"map {run} {tag}", gmap)
        for run in NUMBER_RUNS:
            def numbers(run=run, w=w, h=h, tag=tag):
                p = lineage(run, "numbers")["payload"]
                use_palette(palette(run))
                clip = graphics.render_stat_cards(p["stat_cards"], work / f"num_{run}_{tag}.mp4", stat_photo(run),
                                                  width=w, height=h, narration=p.get("narration_script") or "")
                keep(f"stat_cards__{run[4:]}__{tag}", poster(clip))
            job("stat_cards", f"numbers {run} {tag}", numbers)
        for run in TIMELINE_RUNS:
            def timeline(run=run, w=w, h=h, tag=tag):
                p = lineage(run, "timeline")["payload"]
                use_palette(palette(run))
                clip = graphics.render_timeline(p["timeline"], work / f"tl_{run}_{tag}.mp4", width=w, height=h,
                                                photo=stat_photo(run))
                keep(f"timeline__{run[4:]}__{tag}", poster(clip))
            job("timeline", f"timeline {run} {tag}", timeline)
        for run in SCALE_RUNS:
            def scale(run=run, w=w, h=h, tag=tag):
                p = lineage(run, "scale")["payload"]
                use_palette(palette(run))
                clip = graphics.render_scale(p["scale_fact"], work / f"sc_{run}_{tag}.mp4", width=w, height=h)
                keep(f"scale__{run[4:]}__{tag}", poster(clip))
            job("scale", f"scale {run} {tag}", scale)

        def route(w=w, h=h, tag=tag):
            p = json.loads((data / "output" / ROUTE_RUN / "verified_script.json").read_text(encoding="utf-8"))
            use_palette(palette(ROUTE_RUN))
            clip = graphics.render_route(p["route"], work / f"route_{tag}.mp4", width=w, height=h,
                                         narration=p.get("narration_script") or "")
            if clip is None:
                raise RuntimeError("render_route returned None (no located stops offline)")
            keep(f"route__{ROUTE_RUN[4:]}__{tag}", poster(clip))
        job("route", f"route {tag}", route)

    shutil.rmtree(work, ignore_errors=True)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8", newline="\n")
    print(len(manifest["files"]), "renders,", len(manifest["errors"]), "errors")


if __name__ == "__main__":
    main()
