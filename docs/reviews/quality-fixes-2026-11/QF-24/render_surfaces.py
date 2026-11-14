"""Before/after boards for the QF-24 surfaces (decision QF-24/QF-87 layout, 2026-11-13: keep the approved rectangle,
fix the surfaces) from real run inputs only (read-only).

    python render_surfaces.py <base tree> <this tree> <dev checkout holding output/run_*> <out folder>

Every input comes from the freeze-panel catalogue (docs/reviews/freeze-panel-2026-11-04/fixtures, loaded by path
with RR_FIXTURE_REPO set to the dev checkout): nothing is invented, no photo is paired with another topic's text.

- end card (b): the five 9:16 cases of the catalogue's "endcard" section, each run's own share_line on the last
  frame of its own last scene clip. Three columns: the base (ads box, centred width 696), the option "masthead one
  step down at the organic box's centred width" (616 px centred on the frame) and the option "the group centred in
  the box" (x 480, the box's 735 px: this tree). The loop bridge's FOLLOW (what a Reel ships, LOOP_FOLLOW_OVERLAY)
  is drawn as a second board per case: its fully-up frame on the base and on this tree.
- then-and-now (c): the catalogue's two accepted pairs (each run's own then_0 / now_0_1 and the years it drew),
  the split hold (frame 165) at 9:16.
- myth, hook and chapter cards (d): no fact sheet carries a checked card field (the catalogue's "cards" verdict), so
  the copy is the tests' committed fields, each signed with the tests' throwaway key in this child process only:
  the Richat sheet's own fact-9 correction (tests/test_freeze_cards_2026_11_04.py RICHAT_LONG on the tracked
  data/fact_sheets/richat_structure_sahara_eye.json), the reviewed long myth fixture LONG_ET, the real act titles
  1 and 4 of the long Richat run (RICHAT_ACTS) and the hook fixture claim. Grounds: two real hero photos from the
  catalogue (the committed Richat still; the Amber Room's busy, colourful hero, the worst case), at rest, 9:16 (the
  Richat still is too small for the hook, which refuses an enlargement over 2x: recorded in the meta). The hook is
  also drawn on the catalogue's bright unfinished-obelisk hero, where it takes its plate position (centred on x 480).
  The 16:9 plans of the same cards are compared element for element (meta "cards_16x9": identical on both trees).
- callouts (a): no run has ever drawn a callout (the catalogue lists none), so the board is the layout geometry of
  the committed reference pair (the operator's reference door and wall, tests/test_callouts_v4_2026_11_03.py
  REF_DOOR / REF_WALL coordinates) on an ink ground, labelled as such: plates, leaders, dots and the objects' boxes.

Each tree draws in its own child process. Boards are half size, JPEG (<= 300 KB), base left. The thin lines are an
overlay, not output: grey the ads Reel box and its 1058 floor, gold the organic box, its band top (1130) and its
graphics floor (1090).
"""
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

FIX = Path("docs") / "reviews" / "freeze-panel-2026-11-04" / "fixtures"
FONT = "Montserrat"


def _fixtures(tree: str, dev: str):
    os.environ["RR_FIXTURE_REPO"] = dev
    sys.path.insert(0, str(Path(tree) / FIX))
    import fixtures
    return fixtures


def _child(tree: str, dev: str, out: str, variant: str) -> None:
    os.environ.update(FONT_SIZE="80", PYTHONIOENCODING="utf-8")
    sys.path.insert(0, tree)
    from PIL import Image

    from src import end_card, then_now
    from src.safe_zones import REEL
    F = _fixtures(tree, dev)
    o = Path(out)
    meta = {}
    if variant == "step_down":   # the option: the masthead at the centred width (616), the group on the frame centre
        end_card.reel_column = lambda w=1080, h=1920: (w / 2, REEL.centred_width(w, h))
    # end card and loop bridge, 9:16 cases
    for i, case in enumerate(c for c in F.load("endcard") if c["format"] == "9:16"):
        last = Image.open(F.frame(case["last_frame"]["from"], last=True)).convert("RGB")
        first = Image.open(F.frame(case["first_frame"]["from"], 0.0)).convert("RGB")
        stats = {}
        frames = list(end_card.frames(last, case["share_line"], stats))
        frames[-1].save(o / f"endcard_{i}.png")
        lay = end_card.layout(case["share_line"])
        m = lay["masthead"]
        meta[f"endcard_{i}"] = {"run": Path(case["run"]).name, "masthead_px": m["size"], "masthead_w": m["width"],
                                "share_px": lay["share_font"].size, "share_lines": len(lay["lines"]),
                                "box": [round(v) for v in stats["box"]], "contrast": stats.get("contrast")}
        if variant != "step_down":
            bst = {}
            bridge = list(end_card.loop_bridge(last, first, case["share_line"], bst, line=False, follow=True))
            k = max(j for j in range(len(bridge)) if end_card.follow_alpha(j, len(bridge)) >= 1.0)
            bridge[k].save(o / f"bridge_{i}.png")
            meta[f"bridge_{i}"] = {"run": Path(case["run"]).name, "follow_box": bst.get("follow_box"),
                                   "contrast": bst.get("contrast")}
    # then-and-now, 9:16 split hold
    if variant != "step_down":
        for i, case in enumerate(F.load("thennow")):
            kept = {}
            hold = 165

            def enc(frames, path, n, *a, kept=kept, hold=hold, **k):
                for j, f in enumerate(frames):
                    if j == hold:
                        kept["f"] = f.convert("RGB")
                return Path(path)
            then_now._encode = enc
            then_now.record_contrast = lambda *a, **k: {}
            with tempfile.TemporaryDirectory() as tmp:   # a refusal or an error stops the boards (never hidden)
                then_now.render(F.path(case["old"]), int(case["old_year"]), F.path(case["new"]),
                                int(case["new_year"]), Path(tmp) / "t.mp4", 1080, 1920)
            kept["f"].save(o / f"thennow_{i}.png")
            meta[f"thennow_{i}"] = {"run": Path(case["run"]).name, "split": then_now._split_at(1080, 1920),
                                    "now_marker": then_now.marker_xy("now", 1080, 1920)}
        _callout_board(tree, o, meta)
        _cards(tree, F, o, meta)
    (o / "meta.json").write_text(json.dumps(meta, indent=1, default=str), encoding="utf-8")


def _callout_board(tree: str, o: Path, meta: dict) -> None:
    from PIL import Image, ImageDraw

    from src import brand, callouts
    sys.path.insert(0, tree)
    from tests.test_callouts_v4_2026_11_03 import REF_DOOR, REF_WALL
    w, h = 1080, 1920
    im = Image.new("RGB", (w, h), brand.INK)
    d = ImageDraw.Draw(im)
    clear = callouts.clear_of_captions([REF_DOOR, REF_WALL], w, h)
    kept = callouts.declutter(clear, w, h)
    for p in (REF_DOOR, REF_WALL):
        d.rectangle(p[7], outline=(90, 90, 90), width=2)
    if kept is None:
        d.text((140, 900), "NO GRAPHIC (declutter: the pair has no layout)", fill=brand.BONE,
               font=callouts.get_font(FONT, 34, weight=700))
        meta["callouts"] = {"layout": None}
        im.save(o / "callouts.png")
        return
    labs = callouts.layout_labels(kept, w, h)
    for lab in labs:
        d.line(lab["drawn"], fill=brand.GOLD, width=3)
        x, y = lab["dot"]
        d.ellipse((x - 8, y - 8, x + 8, y + 8), fill=brand.GOLD)
        d.rounded_rectangle(lab["box"], 10, fill=(20, 22, 26), outline=(120, 120, 120))
        for ln, ty in zip(lab["lines"], lab["text_y"]):
            d.text((lab["text_x"], ty), ln, fill=brand.BONE, font=lab["font"], anchor=lab["anchor"])
    meta["callouts"] = {"layout": [{"lines": lab["lines"], "px": lab["font"].size, "box": lab["box"],
                                    "conflict": lab["conflict"]} for lab in labs]}
    im.save(o / "callouts.png")


def _cards(tree: str, F, o: Path, meta: dict) -> None:
    from src import config
    from src import design_cards as dc
    config.FACT_RECORD_KEY = "test-only-fact-record-key-0123456789abcdef"   # the tests' key, this process only
    from tests import test_freeze_cards_2026_11_04 as fc
    grounds = {"richat": F.still("richat_structure_sahara_eye_01_commons_960.jpg"),
               "amber": F.path(next(h["photo"] for h in F.section("cards")["hero_photos"]
                                    if h["topic"] == "amber_room_heist"))}
    # the hook's plate position (bright ground: the claim moves onto a centred plate on the box's axis, x 480), on a
    # bright real hero of the catalogue (the unfinished obelisk, p90 luma 219); the Richat and Amber heroes keep the
    # normal, left-set position (QF-24 review 1: no board showed the plate position on a photo)
    hook_grounds = {**grounds, "obelisk_plate": F.path(next(h["photo"] for h in F.section("cards")["hero_photos"]
                                                            if h["topic"] == "unfinished_obelisk_aswan"))}
    richat = fc.real_sheet("richat_structure_sahara_eye")
    cards = {
        "myth_richat": lambda ph, size: fc.richat_myth(richat, photo=ph, size=size),
        "myth_long_et": lambda ph, size: fc.plan_myth(fc.LONG_ET, size=size, photo=ph),
        "chapter_act1": lambda ph, size: fc.chapter_plan(size, 1, fc.ACTS, fc.RICHAT_ACTS[0], photo=ph),
        "chapter_act4": lambda ph, size: fc.chapter_plan(size, 4, fc.ACTS, fc.RICHAT_ACTS[3], photo=ph),
        "hook": lambda ph, size: (fc.hook_plan(ph) if size == fc.TALL else None),
    }
    wide = {}
    for name, make in cards.items():
        for g, ph in (hook_grounds if name == "hook" else grounds).items():
            key = f"card_{name}_{g}"
            try:
                plan = make(ph, fc.TALL)
            except dc.CardRefused as e:   # recorded, never hidden
                meta[key] = {"refused": str(e)[:160]}
                continue
            # the card at rest: the last frame (the hook dissolves out at its end: the frame its last element settles)
            rest = plan.frames - 1 if name != "hook" else min(
                plan.frames - 1, math.ceil(max(v[1] for v in plan.timeline.values()) * plan.frames / plan.seconds))
            dc.frame_at(plan, rest).convert("RGB").save(o / f"{key}.png")
            boxes = [e.box for e in plan.elements if getattr(e, "box", None)]
            meta[key] = {"ink_x": [round(min(b[0] for b in boxes)), round(max(b[2] for b in boxes))],
                         "ink_foot": round(max(b[3] for b in boxes)),
                         "text": {k: v for k, v in sorted(plan.drawn_text.items())}}
            if name == "hook":
                meta[key]["axis"] = round((min(b[0] for b in boxes) + max(b[2] for b in boxes)) / 2)
            for k in ("anchors", "title_px", "claim_px"):
                if k in plan.meta:
                    meta[key][k] = plan.meta[k]
        try:
            w = make(grounds["richat"], fc.WIDE)
            wide[name] = None if w is None else repr([(e.name, e.box) for e in w.elements])
        except dc.CardRefused as e:
            wide[name] = f"refused: {e}"[:160]
    meta["cards_16x9"] = wide


# The loop bridge dissolves into the Reel's delivered frame 0, read back from the run's stored master, so a caption
# burned into that master (its original render, centred on x 540) shows in both columns. It is not this tree's
# caption, which centres on x 480 (reel_caption_centre_x); only the FOLLOW is drawn by each tree.
BRIDGE_NOTE = ("Board label (not output): FOLLOW is drawn by each tree (base left, x 540; this tree right, x 480). A caption"
               " plate under it\nis burned into the run's stored master (frame 0, its original render, centred on x 540)"
               " in both columns; it is not this tree's\ncaption, which centres on x 480 (measure.py measures this tree's"
               " caption plates).")


def _overlay(im):
    from PIL import ImageDraw
    d = ImageDraw.Draw(im)
    grey, gold = (150, 150, 150), (235, 180, 60)
    d.rectangle((65, 288, 888, 1248), outline=grey, width=2)
    d.line((65, 1058, 888, 1058), fill=grey, width=2)
    d.rectangle((113, 288, 848, 1370), outline=gold, width=2)
    d.line((113, 1130, 848, 1130), fill=gold, width=2)
    d.line((113, 1090, 848, 1090), fill=gold, width=2)
    return im


def _board(paths, dst: Path, note: str | None = None) -> None:
    from PIL import Image, ImageDraw, ImageFont
    ims = [_overlay(Image.open(p).convert("RGB")) for p in paths]
    pair = Image.new("RGB", (1080 * len(ims), 1920))
    for i, im in enumerate(ims):
        pair.paste(im, (1080 * i, 0))
    small = pair.resize((540 * len(ims), 960), Image.LANCZOS)
    if note:   # a label strip under the board (board text, not output)
        lines = note.split("\n")
        board = Image.new("RGB", (small.width, 960 + 12 + 22 * len(lines)), (16, 16, 18))
        board.paste(small, (0, 0))
        font = ImageFont.load_default(size=17)
        for j, ln in enumerate(lines):
            ImageDraw.Draw(board).text((10, 966 + 22 * j), ln, fill=(235, 230, 220), font=font)
        small = board
    for q in (82, 74, 66, 58):
        small.save(dst, quality=q, optimize=True)
        if dst.stat().st_size <= 300 * 1024:
            break


def main(base: str, head: str, dev: str, out: str) -> None:
    out_p = Path(out)
    out_p.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="qf24-sf-") as tmp:
        runs = (("before", base, "base"), ("step_down", head, "step_down"), ("after", head, "head"))
        for tag, tree, variant in runs:
            (Path(tmp) / tag).mkdir()
            subprocess.run([sys.executable, __file__, "--child", tree, dev, str(Path(tmp) / tag), variant],
                           check=True, env=dict(os.environ, FONT_SIZE="80"), timeout=7200)
        metas = {tag: json.loads((Path(tmp) / tag / "meta.json").read_text(encoding="utf-8")) for tag, _, _ in runs}
        for name in sorted(metas["after"]):
            t = Path(tmp)
            if name.startswith("endcard_"):
                _board([t / "before" / f"{name}.png", t / "step_down" / f"{name}.png", t / "after" / f"{name}.png"],
                       out_p / f"{name}_{metas['after'][name]['run']}_base_stepdown_centred.jpg")
            elif name == "cards_16x9":
                metas["cards_16x9_identical"] = metas["before"].get(name) == metas["after"].get(name)
            elif (t / "before" / f"{name}.png").exists() and (t / "after" / f"{name}.png").exists():
                run = metas["after"][name].get("run", "fixture")
                _board([t / "before" / f"{name}.png", t / "after" / f"{name}.png"],
                       out_p / f"{name}_{run}_before_after.jpg", BRIDGE_NOTE if name.startswith("bridge_") else None)
        (out_p / "surfaces.json").write_text(json.dumps(metas, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps(metas, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) == 6 and sys.argv[1] == "--child":
        _child(*sys.argv[2:])
    elif len(sys.argv) == 5:
        main(*sys.argv[1:])
    else:
        raise SystemExit("usage: render_surfaces.py <base tree> <this tree> <dev checkout> <out folder>")
