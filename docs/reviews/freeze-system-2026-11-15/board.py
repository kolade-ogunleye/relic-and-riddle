"""Cycle-0 system board (FREEZE-PLAYBOOK.md v2 section 1; CYCLE0-system-brief.md section 6): the 13 rows, rendered
fresh from one code tree on real run inputs, for the lander's panel (the builder never renders the panel board).

    python board.py <tree> <dev checkout holding output/run_* and assets/> <out dir> [--brand default|second]
                    [--rows 1,2,...] [--per-kind 2] [--no-surfaces]

<tree>: the commit under review (a clean worktree). <dev checkout>: read-only inputs (renders-use-real-run-data).
Run it once per palette (default, then --brand second: QF-35a's test brand) into two out dirs. One heavy run: it starts
QF-35a's render_surfaces.py once (all surfaces of rows 3-11, the graphic kinds limited to --per-kind runs) and draws
rows 1, 2, 12 and 13 itself. Output: <out>/rowNN_<name>.png (full size) and _half.jpg (Lanczos, q90) per row,
<out>/surfaces/*.png (every surface's last frame), <out>/surfaces.json (hashes, errors, the src hash), and
<out>/board.json (what each row holds, the inputs and the src hash: the manifest's builder part).

Heroes (the brief's worst cases): near-white / bright = unfinished_obelisk_aswan (run 1792985912), busy / dark =
amber_room_heist (1792790550) and wieliczka_salt_mine (1792974672), longest copy = richat_structure_sahara_eye
(1793272675 carousel, 1792740961_long captions / end screen). Rows 3-11 tile every surface render whose name holds one
of those topics (plus every row-specific surface that has no such case, labelled with its own topic).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

HEROES = ("unfinished_obelisk_aswan", "amber_room_heist", "wieliczka_salt_mine", "richat_structure_sahara_eye")
ROWS = {   # row -> (name, render_surfaces name prefixes)
    3: ("reel_cover_grid", ("cover__",)),
    4: ("youtube", ("cover__",)),
    5: ("carousel", ("social__",)),
    6: ("story", ("social__",)),
    7: ("captions", ("captions__",)),
    8: ("cards", ("card__",)),
    9: ("endscreen_bridge", ("endscreen__", "endcard__", "bridge__", "bridge_follow__")),
    10: ("graphics", ("map__", "numbers__", "timeline__", "scale__", "route__", "timeline_dense__", "cross_section__")),
    11: ("thennow_callout_badge", ("thennow__", "callouts__", "captions__")),
}
FIX = Path("docs") / "reviews" / "freeze-panel-2026-11-04" / "fixtures"


def label(im, text: str):
    from PIL import ImageDraw, ImageFont
    d = ImageDraw.Draw(im)
    f = ImageFont.load_default(size=22)
    d.rectangle([0, 0, im.width, 30], fill=(0, 0, 0))
    d.text((6, 4), text, fill=(255, 255, 255), font=f)
    return im


def sheet(tiles, out: Path, tile_h: int = 900, cols: int = 4) -> None:
    """Tiles (label, image) at a common height, `cols` per row, saved full and half (Lanczos, q90)."""
    from PIL import Image
    if not tiles:
        return
    scaled = []
    for name, im in tiles:
        im = im.convert("RGB")
        w = round(im.width * tile_h / im.height)
        scaled.append(label(im.resize((w, tile_h), Image.LANCZOS), name))
    rows = [scaled[i:i + cols] for i in range(0, len(scaled), cols)]
    W = max(sum(t.width for t in r) + 12 * (len(r) - 1) for r in rows)
    H = len(rows) * tile_h + 12 * (len(rows) - 1)
    board = Image.new("RGB", (W, H), (40, 40, 40))
    y = 0
    for r in rows:
        x = 0
        for t in r:
            board.paste(t, (x, y))
            x += t.width + 12
        y += tile_h + 12
    board.save(out.with_suffix(".png"))
    board.resize((W // 2, H // 2), Image.LANCZOS).save(out.with_name(out.stem + "_half.jpg"), quality=90)


def row1_specimen(out: Path, dev: Path) -> list[str]:
    """Every type role at its token size (true px), face, weight and tracking, per canvas: one sheet on INK and one
    over a real hero under the held plate tier."""
    from PIL import Image, ImageDraw

    from src import brand, brand_system
    from src.typography import get_font, plan_text
    hero = Image.open(dev / "assets" / "unfinished_obelisk_aswan" / "01_commons.jpg").convert("RGB")
    weights = {"display": None, "text": 600, "data": 600, "masthead": 700}
    sample = {"display_xl": "THE EYE OF THE SAHARA", "display_l": "IMPACT THEORY", "display_m": "YOUR TAKE?",
              "figure": "40 KM", "kicker": "THE RIDDLE", "body": "A concentric formation in Mauritania.",
              "label": "Mauritania", "data_label": "0 M  100 M", "credit": "Photo: NASA / CC0", "masthead": brand.MASTHEAD}
    made = []
    from PIL import ImageOps
    small = get_font(brand.DATA_FONT, 20, weight=500)
    for canvas, (w, _h) in brand_system.CANVASES.items():
        roles = brand_system.TYPE[canvas]
        height = 48 + sum(round(v * 1.3) + 30 for v in roles.values())   # every role at its true size, one per line
        for ground in ("ink", "photo"):
            im = Image.new("RGB", (w, height), brand.INK)
            if ground == "photo":   # the real hero under the held plate tier (its right third left bare)
                im.paste(ImageOps.fit(hero, (w, height)), (0, 0))
                base = im.convert("RGBA")
                base.alpha_composite(Image.new("RGBA", (w * 2 // 3, height),
                                               (*brand.INK, round(255 * brand_system.SPEC["plate.held"]))), (0, 0))
                im = base.convert("RGB")
            y = 24
            for role, px in roles.items():
                face_role = brand_system.FACE[role]
                weight = 800 if role == "kicker" else weights[face_role]
                font = get_font(brand.active().font(face_role), px, weight=weight)
                track = (brand_system.tok("kicker.track") * px if role == "kicker" else
                         brand_system.tok("masthead.track") * px if role == "masthead" else 0)
                fill = brand.GOLD if role in ("figure", "kicker") else brand.BONE
                ImageDraw.Draw(im).text((24, y), f"{role}  {px:.1f} px  {brand.active().font(face_role)}  "
                                        f"weight {weight or 'regular'}  track {track / px:.3f} em", font=small,
                                        fill=brand.STONE_LT)
                p = plan_text(im, (24, y + 28), sample[role], font, fill, "la", track,
                              target=brand_system.tok("contrast.place"))
                im = p.draw(im)
                y += round(px * 1.3) + 30
            name = f"row01_specimen_{canvas}_{ground}"
            im.save(out / f"{name}.png")
            im.resize((w // 2, height // 2), Image.LANCZOS).save(out / f"{name}_half.jpg", quality=90)
            made.append(name)
    return made


def row2_masthead(out: Path, dev: Path) -> list[str]:
    """The masthead at every carried size (carousel, YouTube, end screen) on dark, mid, bright and near-white real
    grounds (the top of each hero, where the masthead sits), with its record; plus 246 px and 124 px wide feed
    simulations of the 1920 and 1440 canvases."""
    from PIL import Image, ImageOps

    from src import brand_system, end_card, masthead
    grounds = {"dark wieliczka": "wieliczka_salt_mine/01_commons.jpg", "busy amber": "amber_room_heist/01_commons.jpg",
               "bright aswan": "unfinished_obelisk_aswan/01_commons.jpg",
               "near-white antikythera": "antikythera_mechanism/01_commons.jpg"}
    sizes = {"carousel 1440": (1440, brand_system.type_px("masthead", "carousel")),
             "youtube 1920": (1920, brand_system.type_px("masthead", "wide")),
             "end screen 1920": (1920, end_card._masthead(end_card.LANDSCAPE_MASTHEAD_W)["size"])}
    tiles, recs = [], {}
    for g, rel in grounds.items():
        p = dev / "assets" / rel
        if not p.is_file():
            continue
        hero = Image.open(p).convert("RGB")
        for s, (w, size) in sizes.items():
            canvas = ImageOps.fit(hero, (w, round(w * 0.5625)), centering=(0.5, 0.0))
            x0, base = round(w * 0.055), round(size * 1.9)
            drawn, _, rec, _ = masthead.draw(canvas, x0, base, size, reach=base + 6 * size)
            recs[f"{g} | {s}"] = rec
            crop = drawn.crop((0, 0, min(w, round(x0 + masthead.group_width(size) + 120)), round(base + size * 1.6)))
            state = (f"{rec['variant']} {rec['contrast']}:1 (worst letter) scrim {rec['scrim_peak']} halo {rec['halo']}"
                     if rec["shown"] else f"NOT DRAWN (fail closed): best {rec['contrast']}:1")
            tiles.append((f"{g} | {s} | {state}", crop))
            feed = drawn.resize((246, round(246 * drawn.height / w)), Image.LANCZOS)
            tiles.append((f"{g} | {s} @246", feed.resize((feed.width * 4, feed.height * 4), Image.NEAREST)))
    sheet(tiles, out / "row02_masthead", tile_h=300, cols=2)
    (out / "row02_masthead.json").write_text(json.dumps(recs, indent=1), encoding="utf-8", newline="\n")
    return ["row02_masthead"]


def row12_motion(out: Path, dev: Path) -> list[str]:
    """Motion tokens: the end screen's reveal (every 3rd frame, a real run's last frame), the loop bridge k00-k29 into
    frame 0, and each named ease's curve."""
    from PIL import Image, ImageDraw
    sys.path.insert(0, str(Path.cwd() / FIX))
    import fixtures as fx

    from src import brand_system, end_card
    made = []
    cases = fx.load("endcard")
    wide = next(c for c in cases if c["topic"] == "unfinished_obelisk_aswan" and c.get("format") == "16:9")
    tall = next(c for c in cases if c["topic"] == "unfinished_obelisk_aswan" and c.get("format") == "9:16")

    def still(src):
        return (Image.open(fx.frame(src, last=True)) if str(src).endswith(".mp4") else Image.open(fx.path(src))).convert("RGB")
    last = still(wide.get("last_frame", {}).get("from") or wide.get("last"))
    if last.width < last.height:
        from PIL import ImageOps
        last = ImageOps.fit(last, (1920, 1080), Image.LANCZOS)
    frames = list(end_card.frames(last, wide["share_line"], {}))
    sheet([(f"end screen t{i / end_card.FPS:.2f}s", f) for i, f in enumerate(frames) if i % 3 == 0],
          out / "row12_motion_endscreen", tile_h=270, cols=6)
    made.append("row12_motion_endscreen")
    lastt = still(tall.get("last_frame", {}).get("from") or tall.get("last"))
    first = Image.open(fx.frame(tall["first_frame"]["from"], 0.0)).convert("RGB")
    bridge = list(end_card.loop_bridge(lastt, first, tall["share_line"], {}, line=False, follow=True))
    sheet([(f"k{i:02d}", f) for i, f in enumerate(bridge)] + [("reel frame 0", first)], out / "row12_motion_bridge",
          tile_h=480, cols=8)
    made.append("row12_motion_bridge")
    W, H = 1200, 700
    im = Image.new("RGB", (W, H), (250, 250, 250))
    d = ImageDraw.Draw(im)
    colours = [(200, 40, 40), (40, 120, 200), (40, 160, 80), (160, 80, 160), (200, 140, 0), (90, 90, 90)]
    for k, (name, fn) in enumerate(brand_system.EASES.items()):
        pts = [(60 + 1000 * t / 100, 620 - 480 * fn(t / 100)) for t in range(101)]
        d.line(pts, fill=colours[k % len(colours)], width=4)
        d.text((1080, 100 + 40 * k), name, fill=colours[k % len(colours)])
    d.rectangle([60, 140, 1060, 620], outline=(0, 0, 0))
    im.save(out / "row12_motion_eases.png")
    made.append("row12_motion_eases")
    return made


def row13_table(out: Path) -> list[str]:
    """The element-by-element table across surfaces, from the tokens and the modules as they are on this tree."""
    from PIL import Image, ImageDraw, ImageFont

    from src import brand, brand_system, end_card, multi_format
    t = brand_system.tok
    rows = [
        ("element", "carousel", "YouTube", "end screen 16:9", "loop bridge / covers / hook"),
        ("masthead", (f"{brand.MASTHEAD_FONT} 700 {brand_system.type_px('masthead', 'carousel'):.1f}px "
                      f"tr {t('masthead.track')}em, {t('masthead.colour_dark')}/{t('masthead.colour_light')}"),
         f"same, {brand_system.type_px('masthead', 'wide'):.0f}px", f"same, brand moment {end_card._masthead(620)['size']}px",
         f"absent (cap at 124 px < {t('masthead.grid_cap')} px)"),
        ("masthead ground", (f"scrim peak <= {t('scrim.masthead_peak')}, halo <= {t('halo.masthead_max')}, "
                             f"gate {t('contrast.masthead_gate')} target {t('contrast.masthead_target')}"),
         "same", "same (dimmed card)", "-"),
        ("kicker", f"{brand.TEXT_FONT} {t('kicker.weight')} tr {t('kicker.track')}em {t('kicker.colour')} + lead rule",
         "eyebrow (group B)", f"same, {brand_system.type_px('kicker', 'wide'):.0f}px (WATCH NEXT)", "covers eyebrow 0.10em (group B)"),
        ("line ends", f"never {len(brand_system.LINE_END_NEVER)} words, last resort {len(brand_system.LINE_END_LAST_RESORT)}",
         "group B", "-", f"cards: never-list = {len(multi_format._DANGLING)}-word union (soft) / hard never-list"),
        ("shade", f"{t('plate.shade')}", "INK", "INK", "INK (callouts; covers' _shade still black: group B)"),
        ("fail closed", "under the 5.5 gate on any letter: not drawn, recorded", "same", "same", "-"),
        ("eases", ", ".join(brand_system.EASES), "-", "ease_out / ease_in_out", "-"),
    ]
    f = ImageFont.load_default(size=20)
    W, rh = 2400, 64
    im = Image.new("RGB", (W, rh * len(rows) + 20), (255, 255, 255))
    d = ImageDraw.Draw(im)
    widths = (260, 760, 380, 480, 520)
    for r, row in enumerate(rows):
        x = 10
        for c, cell in enumerate(row):
            d.text((x, 10 + r * rh), str(cell), fill=(0, 0, 0), font=f)
            x += widths[c]
        d.line([(0, 6 + (r + 1) * rh), (W, 6 + (r + 1) * rh)], fill=(200, 200, 200))
    im.save(out / "row13_elements.png")
    return ["row13_elements"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tree", type=Path)
    ap.add_argument("dev", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--brand", choices=("default", "second"), default="default")
    ap.add_argument("--rows", default="")
    ap.add_argument("--per-kind", type=int, default=2)
    ap.add_argument("--no-surfaces", action="store_true", help="reuse <out>/surfaces (builder iteration only)")
    a = ap.parse_args()
    tree, dev, out = a.tree.resolve(), a.dev.resolve(), a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    rows = {int(r) for r in a.rows.split(",") if r} or set(range(1, 14))
    os.environ.update(FONT_SIZE="80", PYTHONIOENCODING="utf-8", RR_FIXTURE_REPO=str(dev))
    os.chdir(tree)
    sys.path.insert(0, str(tree))
    sys.path.insert(0, str(tree / FIX))
    src_hash = hashlib.sha256()
    for f in sorted((tree / "src").glob("*.py")):
        src_hash.update(f.name.encode() + f.read_bytes().replace(bytes([13, 10]), bytes([10])))
    manifest = {"src_sha256": src_hash.hexdigest()[:16], "brand": a.brand, "rows": {}, "heroes": HEROES}
    if rows & set(ROWS) and not a.no_surfaces:
        script = tree / "docs/reviews/quality-fixes-2026-11/QF-35a/render_surfaces.py"
        cmd = [sys.executable, str(script), str(tree), str(dev), str(out / "surfaces.json"), "--keep", str(out / "surfaces"),
               "--per-kind", str(a.per_kind)] + (["--brand", "second"] if a.brand == "second" else [])
        subprocess.run(cmd, check=True)
    from src import brand
    if a.brand == "second":   # rows 1, 2, 12, 13 in QF-35a's test brand too
        sys.path.insert(0, str(tree / "docs/reviews/quality-fixes-2026-11/QF-35a"))
        import render_surfaces
        brand.configure(render_surfaces.second_brand(brand))
    if 1 in rows:
        manifest["rows"]["1"] = row1_specimen(out, dev)
    if 2 in rows:
        manifest["rows"]["2"] = row2_masthead(out, dev)
    from PIL import Image
    for r, (name, prefixes) in sorted(ROWS.items()):
        if r not in rows:
            continue
        pngs = sorted((out / "surfaces").glob("*.png"))
        pick = [p for p in pngs if p.stem.startswith(prefixes)]
        if r == 4:
            pick = [p for p in pick if p.stem.endswith("youtube")]
        elif r == 3:
            pick = [p for p in pick if p.stem.endswith(("reel", "grid"))]
        elif r == 5:
            pick = [p for p in pick if "__carousel_" in p.stem or "__fb_" in p.stem]
        elif r == 6:
            pick = [p for p in pick if "__story_" in p.stem]
        elif r == 11:
            pick = [p for p in pick if not p.stem.startswith("captions__") or "__badge" in p.stem]
        heroes = [p for p in pick if any(h in p.stem for h in HEROES)] or pick
        tiles = [(p.stem, Image.open(p)) for p in heroes]
        if r == 4:   # feed sizes beside the full frame
            tiles += [(p.stem + " @246", Image.open(p).resize((246, 138), Image.LANCZOS).resize((984, 552), Image.NEAREST))
                      for p in heroes]
            tiles += [(p.stem + " @168", Image.open(p).resize((168, 94), Image.LANCZOS).resize((672, 376), Image.NEAREST))
                      for p in heroes]
        sheet(tiles, out / f"row{r:02d}_{name}", tile_h=960)
        manifest["rows"][str(r)] = [p.stem for p in heroes]
    if 12 in rows:
        manifest["rows"]["12"] = row12_motion(out, dev)
    if 13 in rows:
        manifest["rows"]["13"] = row13_table(out)
    (out / "board.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8", newline="\n")
    print("board:", out, manifest["src_sha256"])


if __name__ == "__main__":
    main()
