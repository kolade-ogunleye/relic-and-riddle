"""QF-136: the visual critic's command line (src/visual_critic.py does the checking).

ADVISORY: the visual critic blocks nothing. 1 of 622 planted defects passed it (0.16%: an accent added over a busy ground); a pass is not a guarantee (operator ruling, decisions/FREEZE-HARD-LIMITS-2026-11-17.md).

    python scripts/visual_critic.py check <render set> <report dir> [--device auto|cpu|<adapter>] [--workers N]
                                          [--no-faces] [--samples N]
    python scripts/visual_critic.py render <dev checkout> <new render set> [--runs N] [--only social,covers,...]
    python scripts/visual_critic.py plant <render set> <base report.json> <new planted set> [--per-class 10]
                                          [--faces-from <checkout>]
    python scripts/visual_critic.py score <planted set> <planted report.json> <base report.json>

check   runs the (advisory) check on a render set (a folder this script rendered, or a real run folder read-only) and writes
        report.json (findings, pass/fail per frame and per class: never a score), timing.json, index.html and
        sheet/*.jpg (half size, <= 300 KB) to the report folder, which may not lie inside the set. Exit code 1 when
        the check fails: advisory, it blocks nothing, and no script or CI job may treat it as a blocker.
render  draws a render set with this tree's renderers from real run inputs (read-only, network blocked): Story frames
        and carousel slides from their kept recipes, covers from the freeze-panel catalogue, graphics from their
        recorded inputs, captions (and the reconstruction badge) from real narrations, hook / chapter / myth cards from
        the card tests' signed fields. Every frame gets <frame>.record.json: the renderer's INPUTS as the truth
        ("expect") and its layout record as a hint/cross-check ("elements").
plant   copies frames of a render set and plants known defects, --per-class of each class, recorded in planted.json.
score   recall per planted class and the findings on the unplanted set.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIX = ROOT / "docs" / "reviews" / "freeze-panel-2026-11-04" / "fixtures"
CARD_FIELD_KEY = "test-only-fact-record-key-0123456789abcdef"   # the card tests' throwaway key (this process only)


def _offline(*_a, **_k):
    raise RuntimeError("the visual critic's harness is offline")


def _write_json(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def _record(out: Path, png: Path, kind: str, size, expect, elements, deliverable=None, recipe=None, nontext=None,
            zone=None, protected=None) -> None:
    _write_json(out, {"frame": png.name, "kind": kind, "size": list(size), "deliverable": deliverable,
                      "expect": expect, "elements": elements, "nontext": nontext or [], "zone": zone,
                      "protected": protected or [], **({"recipe": recipe} if recipe else {})})


# ------------------------------------------------------------------------------------------------ render
def cmd_render(a) -> int:
    dev, out = Path(a.dev).resolve(), Path(a.out).resolve()
    if out.exists() and any(out.iterdir()):
        print(f"{out} is not empty: a render set is written once", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    os.environ.update(FONT_SIZE="80", PYTHONIOENCODING="utf-8", RR_FIXTURE_REPO=str(dev))
    socket.create_connection = _offline
    socket.socket.connect = _offline
    sys.path.insert(0, str(FIX))
    import fixtures as fx

    from src import geo, graphics
    from src.visual_critic import tokens
    work = Path(tempfile.mkdtemp(prefix="rr-critic-render-"))
    geo_copy = work / "geo_cache.json"                    # the dev place cache, read through a copy
    if (dev / "data" / "geo_cache.json").is_file():
        shutil.copyfile(dev / "data" / "geo_cache.json", geo_copy)
        geo.CACHE_FILE = geo_copy
    only = set(filter(None, (a.only or "").split(",")))
    errors: dict[str, str] = {}

    def job(group: str, name: str, fn) -> None:
        if only and group not in only:
            return
        try:
            fn()
            print("ok ", name, flush=True)
        except Exception as e:  # noqa: BLE001  one failed case is recorded, the rest render
            errors[name] = f"{type(e).__name__}: {e}"[:300]
            print("ERR", name, errors[name], flush=True)

    runs = sorted(p for p in (dev / "output").glob("run_*") if p.is_dir())

    # Story frames and carousel slides from their kept recipes (the recipe is the renderer's input)
    from src.multi_format import MultiFormatGenerator
    from src.visual_critic import RECIPE_FIELDS, story_truth
    social_runs = [r for r in runs if any(((r / s / "still_layout.json").is_file() and "recipe" in
                                           (r / s / "still_layout.json").read_text(encoding="utf-8"))
                                          for s in ("stories", "carousel"))][-a.runs:]
    for run in social_runs:
        def social(run=run):
            gen = MultiFormatGenerator()
            for sub, kind in (("stories", "story"), ("carousel", "carousel"), ("carousel_fb", "carousel")):
                f = run / sub / "still_layout.json"
                if not f.is_file():
                    continue
                for still, rec in sorted(json.loads(f.read_text(encoding="utf-8")).items()):
                    r = rec.get("recipe") or {}
                    if not r or not Path(str(r.get("photo", ""))).is_file():   # a requested frame not drawn: said
                        errors[f"social {run.name} {sub}/{still}"] = "no recipe or its photograph is missing"
                        continue
                    if kind == "story":
                        im, lay = gen._render_story_frame(Path(r["photo"]), r["slide"], int(r["index"]), int(r["count"]),
                                                          r.get("poll") or {}, tight=bool(rec.get("tight")))
                    else:
                        if not Path(str(r.get("photo_next", ""))).is_file():
                            errors[f"social {run.name} {sub}/{still}"] = "the next slide's photograph is missing"
                            continue
                        im, lay = gen._render_carousel_slide(
                            r["archetype"], Path(r["photo"]), Path(r["photo_next"]), r["card"], int(r["index"]),
                            int(r["total"]), r.get("poll") or {}, r.get("share") or "", bool(r.get("facebook")),
                            tight=bool(rec.get("tight")), subject=r.get("subject") or "",
                            heading_frame=int(r.get("heading_frame") or 2))
                    if kind == "story":
                        expect = story_truth(r["slide"], int(r["index"]), int(r["count"]), r.get("poll"))
                    else:
                        key, names = RECIPE_FIELDS["multi_format.redraw_carousel_slide"]
                        src = r.get(key) or {}
                        expect = [{"field": n, "text": src[n], "truth": "recipe"} for n in names
                                  if isinstance(src.get(n), str) and tokens(src[n])]
                    png = out / "social" / run.name[4:] / f"{sub}_{Path(still).stem}.png"
                    png.parent.mkdir(parents=True, exist_ok=True)
                    im.convert("RGB").save(png)
                    d = lay.to_dict()
                    _record(png.with_suffix(".record.json"), png, "still", d["size"], expect, d["elements"],
                            "story" if kind == "story" else "carousel", recipe={**r, "kind": kind, "tight": bool(rec.get("tight"))},
                            zone=d["zone"], protected=d["protected"])
        job("social", f"social {run.name}", social)

    # Covers from the catalogue (their own still_layout.json carries the copy they were asked to draw)
    from src.visual_engine import VisualEngine
    for case in fx.load("covers"):
        def cover(case=case):
            hero = fx.path(case["hero"]["photo"])
            d = out / "covers" / case["topic"]
            d.mkdir(parents=True, exist_ok=True)
            VisualEngine().generate_thumbnails(hero, fx.payload(case), d, style=case["hero"].get("style") or "badge",
                                               variants=False)
        job("covers", f"cover {case['topic']}", cover)

    # Graphics from their recorded inputs; the last frame is the picture, <clip>.contrast.json the record
    from src.art_direction import use_palette
    from src.photo_grade import using

    def encode(frames, outp, n, width=None, height=None, **_k):
        last = None
        for im in frames:
            last = im
        if last is not None:
            last.convert("RGB").save(Path(outp).with_suffix(".png"))
        return Path(outp)
    graphics._encode = encode

    def lineage(run: Path, name: str):
        p = run / "graphics" / f"graphic_{name}.mp4.lineage.json"
        return json.loads(p.read_text(encoding="utf-8"))["settings"]["inputs"] if p.is_file() else None

    def expect_of(kind: str, inp: dict) -> list:
        from src.visual_critic import lineage_truth
        return lineage_truth(kind, inp) or []
    picked = 0
    for kind, field in (("map", "place"), ("numbers", "stat_cards"), ("timeline", "timeline")):
        n = 0
        for run in runs:
            inp = lineage(run, kind)
            if inp is None or n >= a.runs or (kind != "map" and not (inp.get("payload") or {}).get(field)):
                continue
            if kind == "map" and not inp.get("place"):
                continue
            n += 1
            for w, h, tag in ((1080, 1920, "916"), (1920, 1080, "169")):
                def graphic(kind=kind, run=run, inp=inp, w=w, h=h, tag=tag):
                    p = inp["payload"]
                    pal = run / "palette.json"
                    use_palette(json.loads(pal.read_text(encoding="utf-8")) if pal.is_file() else None)
                    d = out / "graphics" / run.name[4:]
                    d.mkdir(parents=True, exist_ok=True)
                    clip = d / f"{kind}_{tag}.mp4"
                    with using(0.0):
                        if kind == "map":
                            graphics.render_map(inp["place"], clip, width=w, height=h,
                                                narration=p.get("narration_script") or "")
                        elif kind == "numbers":
                            graphics.render_stat_cards(p["stat_cards"], clip, None, width=w, height=h,
                                                       narration=p.get("narration_script") or "")
                        else:
                            graphics.render_timeline(p["timeline"], clip, width=w, height=h, photo=None)
                    exp = expect_of(kind, inp)
                    if exp:
                        _write_json(d / f"{kind}_{tag}.expect.json", exp)
                job("graphics", f"{kind} {run.name} {tag}", graphic)
            picked += 1

    # Captions (and the reconstruction badge) built from real narrations; the narration is the truth
    from src import video_composer as vc
    for case in fx.load("captions"):
        def captions(case=case):
            words = json.loads(Path(fx.path(case["words"])).read_text(encoding="utf-8"))
            wide = case.get("format") == "16:9"
            end = float(words[-1]["start"]) + float(words[-1].get("dur", 1.0))
            for suffix, recon in (("", None), ("__badge", [(0.0, end)])):
                d = out / "captions"
                d.mkdir(parents=True, exist_ok=True)
                ass = d / f"{case['topic']}{'_169' if wide else ''}{suffix}.ass"
                kw = {"width": 1920, "height": 1080} if wide else {}
                vc.VideoComposer().build_subtitles(words, ass, emphasis=case.get("emphasis_words") or None,
                                                   reconstruction_intervals=recon, **kw)
                _write_json(ass.with_name(ass.stem + ".words.json"), words)
        job("captions", f"captions {case['topic']}", captions)

    # Hook, chapter and myth cards from the card tests' signed fields (the field text is the truth)
    def cards():
        from src import config
        from src import design_cards as dc
        config.FACT_RECORD_KEY = CARD_FIELD_KEY
        from tests import test_freeze_cards_2026_11_04 as fc
        heroes = {h["topic"]: fx.path(h["photo"]) for h in fx.section("cards")["hero_photos"]}
        grounds = {k: heroes[k] for k in ("amber_room_heist", "unfinished_obelisk_aswan") if k in heroes}
        makers = {
            "hook_short": (lambda ph, size: fc.hook_plan(ph, "About 40 km across") if size == fc.TALL else None,
                           [("claim", "About 40 km across")]),
            "hook_long": (lambda ph, size: fc.hook_plan(ph, "Concentric sandstone quartzite", "f_geo")
                          if size == fc.TALL else None, [("claim", "Concentric sandstone quartzite")]),
            "chapter_2": (lambda ph, size: fc.chapter_plan(size, 2, 5, "The Eye of the Great Sahara", photo=ph),
                          [("title", "The Eye of the Great Sahara")]),
            "myth_short": (lambda ph, size: fc.plan_myth(fc.SHORT, size=size, photo=ph),
                           [("myth", fc.SHORT[0]), ("fact", fc.SHORT[1])]),
            "myth_long": (lambda ph, size: fc.plan_myth(fc.LONG_ET, size=size, photo=ph),
                          [("myth", fc.LONG_ET[0]), ("fact", fc.LONG_ET[1])]),
        }
        d = out / "cards"
        d.mkdir(parents=True, exist_ok=True)
        for name, (make, truth) in makers.items():
            for g, ph in grounds.items():
                for size, tag in ((fc.TALL, "916"), (fc.WIDE, "169")):
                    try:
                        plan = make(ph, size)
                    except dc.CardRefused as e:
                        errors[f"card {name} {g} {tag}"] = f"refused: {e}"[:200]
                        continue
                    if plan is None:
                        continue
                    t_full = max((e.t_full for e in plan.elements if e.kind == "text"), default=0.0)
                    k = min(plan.frames - 1, int(t_full * plan.frames / max(plan.seconds, 1e-6)) + 2)
                    png = d / f"{name}__{g}__{tag}.png"
                    dc.frame_at(plan, k).convert("RGB").save(png)
                    els = [{"name": e.name, "box": list(e.box), "text": e.text, "px": e.px, "fill": list(e.fill)}
                           for e in plan.elements if e.kind == "text"]
                    _record(png.with_suffix(".record.json"), png, "card", (plan.width, plan.height),
                            [{"field": f, "text": t, "truth": "signed field"} for f, t in truth], els,
                            "reel" if plan.height > plan.width else "wide")
    job("cards", "cards", cards)

    _write_json(out / "render_errors.json", errors)
    shutil.rmtree(work, ignore_errors=True)
    print(len(errors), "errors")
    return 0


# ------------------------------------------------------------------------------------------------ check
def cmd_check(a) -> int:
    from src.visual_critic import check_set
    manifest = json.loads(Path(a.manifest).read_text(encoding="utf-8")) if a.manifest else None
    rep = check_set(Path(a.set), Path(a.out), device=a.device, faces=not a.no_faces, samples=a.samples,
                    workers=a.workers, manifest=manifest)
    print(json.dumps({"gate": rep["gate"], "frames": rep["frame_count"], "failed": rep["frames_failed"],
                      "coverage_findings": len(rep["coverage"]["findings"]), "not_judged": len(rep["coverage"]["not_judged"]),
                      "per_class": {c: v["findings"] for c, v in rep["per_class"].items()}}, indent=1))
    return 0 if rep["gate"] == "pass" else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("set")
    c.add_argument("out")
    c.add_argument("--device", default="auto")
    c.add_argument("--workers", type=int, default=1)
    c.add_argument("--no-faces", action="store_true")
    c.add_argument("--samples", type=int, default=8)
    c.add_argument("--manifest", default="", help="the lander's freeze manifest (MANIFEST.json): every frame it "
                   "lists must be judged")
    r = sub.add_parser("render")
    r.add_argument("dev")
    r.add_argument("out")
    r.add_argument("--runs", type=int, default=6)
    r.add_argument("--only", default="")
    p = sub.add_parser("plant")
    p.add_argument("set")
    p.add_argument("base_report")
    p.add_argument("out")
    p.add_argument("--per-class", type=int, default=10)
    p.add_argument("--faces-from", default="", help="a checkout whose output/run_*/beat_images give real faces")
    s = sub.add_parser("score")
    s.add_argument("planted_set")
    s.add_argument("planted_report")
    s.add_argument("base_report")
    a = ap.parse_args()
    if a.cmd == "plant":
        from scripts.visual_critic_plant import plant
        return plant(Path(a.set), Path(a.base_report), Path(a.out), a.per_class,
                     Path(a.faces_from) if a.faces_from else None)
    if a.cmd == "score":
        from scripts.visual_critic_plant import score
        return score(Path(a.planted_set), Path(a.planted_report), Path(a.base_report))
    return {"check": cmd_check, "render": cmd_render}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
