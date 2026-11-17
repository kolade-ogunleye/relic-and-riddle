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
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
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


def hero_pick(pick):
    """Per surface kind (the name's first part), the board's hero cases when the kind has any, else every case of the
    kind (so a kind no hero has, such as then-and-now or the callout, is still on its row)."""
    kinds: dict[str, list] = {}
    for p in pick:
        kinds.setdefault(p.stem.split("__")[0], []).append(p)
    out = []
    for ps in kinds.values():
        out += [p for p in ps if any(h in p.stem for h in HEROES)] or ps
    return out


LABEL_PX, LABEL_MIN_W = 22, 320


def label(im, text: str):
    """The tile under its own label strip (black, white type, wrapped to the tile's width): a label never covers the
    image (F22: the @246 masthead tiles hid the mark under the bar). A tile narrower than LABEL_MIN_W sits on a
    board-grey field that wide, so its label stays readable."""
    from PIL import Image, ImageDraw, ImageFont
    f = ImageFont.load_default(size=LABEL_PX)
    if im.width < LABEL_MIN_W:
        field = Image.new("RGB", (LABEL_MIN_W, im.height), (40, 40, 40))
        field.paste(im, (0, 0))
        im = field
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    room = im.width - 12
    lines: list[str] = []
    cur = ""
    for word in str(text).split(" "):
        while probe.textlength(word, font=f) > room:   # a long name breaks inside itself
            k = max(1, int(len(word) * room / probe.textlength(word, font=f)) - 1)
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(word[:k])
            word = word[k:]
        trial = f"{cur} {word}".strip()
        if cur and probe.textlength(trial, font=f) > room:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    lines.append(cur)
    step = LABEL_PX + 4
    h = 8 + step * len(lines)
    out = Image.new("RGB", (im.width, h + im.height), (0, 0, 0))
    d = ImageDraw.Draw(out)
    for i, ln in enumerate(lines):
        d.text((6, 4 + step * i), ln, fill=(255, 255, 255), font=f)
    out.paste(im, (0, h))
    return out


def sheet(tiles, out: Path, tile_h: int | None = 900, cols: int = 4) -> None:
    """Tiles (label, image) at a common height (`tile_h`), or at their own pixel size (`tile_h=None`: the feed
    renders, crops and tiles that must be seen as delivered), `cols` per row, each under its own label strip, saved
    full and half (Lanczos, q90)."""
    from PIL import Image
    if not tiles:
        return
    scaled = []
    for name, im in tiles:
        im = im.convert("RGB")
        if tile_h is not None and im.height != tile_h:
            im = im.resize((max(1, round(im.width * tile_h / im.height)), tile_h), Image.LANCZOS)
        scaled.append(label(im, name))
    rows = [scaled[i:i + cols] for i in range(0, len(scaled), cols)]
    W = max(sum(t.width for t in r) + 12 * (len(r) - 1) for r in rows)
    H = sum(max(t.height for t in r) for r in rows) + 12 * (len(rows) - 1)
    board = Image.new("RGB", (W, H), (40, 40, 40))
    y = 0
    for r in rows:
        x = 0
        for t in r:
            board.paste(t, (x, y))
            x += t.width + 12
        y += max(t.height for t in r) + 12
    board.save(out.with_suffix(".png"))
    board.resize((max(1, W // 2), max(1, H // 2)), Image.LANCZOS).save(out.with_name(out.stem + "_half.jpg"), quality=90)


def sys_value(name: str):
    """A written-system value wherever src/brand_system.py holds it now (a gate, a runtime token or SPEC), else None:
    the board reads the tokens, never a copy of them."""
    from src import brand_system
    try:
        return brand_system.tok(name)
    except KeyError:
        return brand_system.SPEC.get(name)


LEAD_TOKEN = {"display_xl": "lead.display", "display_l": "lead.display", "display_m": "lead.display",
              "figure": "lead.display", "body": "lead.body", "label": "lead.label", "data_label": "lead.label"}


def line_step(role: str, px: float) -> tuple[float | None, str]:
    """(baseline-to-baseline px, the note) for a role at `px`: the display lead is in cap heights of the role's face,
    the text leads in em (src/brand_system.py "lead.*"); a role with no lead token is a one-line role."""
    from src import brand, brand_system
    from src.typography import get_font
    name = LEAD_TOKEN.get(role)
    v = sys_value(name) if name else None
    if v is None:
        return None, "one line: no lead token"
    if name == "lead.display":
        f = get_font(brand.active().font(brand_system.FACE[role]), px)
        bb = f.getbbox("H", anchor="ls")
        cap = bb[3] - bb[1]
        return v * cap, f"line step {v * cap:.1f} px ({name} {v} x cap {cap})"
    return v * px, f"line step {v * px:.1f} px ({name} {v} em)"


def row1_specimen(out: Path, dev: Path) -> list[str]:
    """Every type role at its token size (true px), face, weight and tracking, per canvas: one sheet on INK and one
    over a real hero under the held plate tier."""
    from PIL import Image, ImageDraw

    from src import brand, brand_system
    from src.typography import get_font, plan_text
    hero = Image.open(dev / "assets" / "unfinished_obelisk_aswan" / "01_commons.jpg").convert("RGB")
    weights = {"display": None, "text": 600, "data": 600, "masthead": 700}
    # each role's sample: real copy (the Richat run's cover, heading, body and poll lines), the second line set at the
    # role's line step (its lead token) so the leading is seen, not described
    sample = {"display_xl": ("THE EYE", "OF THE SAHARA"), "display_l": ("IMPACT THEORY", "DISPROVED"),
              "display_m": ("YOUR TAKE?", "EARTH'S HIDDEN MARVELS"), "figure": ("40 KM", "94–104"),
              "kicker": ("THE RIDDLE",), "body": ("A concentric formation in Mauritania that has", "long fascinated observers from orbit."),
              "label": ("Mauritania", "Adrar Plateau"), "data_label": ("0 M  100 M", "200 M  300 M"),
              "credit": ("Photo: NASA / CC0",), "masthead": (brand.MASTHEAD,)}
    made = []
    from PIL import ImageOps
    small = get_font(brand.DATA_FONT, 20, weight=500)

    def block(role, px):
        step, why = line_step(role, px)
        return round(px * 1.3) + (round(step) if step and len(sample[role]) > 1 else 0) + 58, step, why
    for canvas, (w, _h) in brand_system.CANVASES.items():
        roles = brand_system.TYPE[canvas]
        height = 24 + sum(block(r, v)[0] for r, v in roles.items())   # every role at its true size and line step
        for ground in ("ink", "photo"):
            im = Image.new("RGB", (w, height), brand.INK)
            if ground == "photo":   # the real hero under the held plate tier (its right third left bare)
                im.paste(ImageOps.fit(hero, (w, height)), (0, 0))
                base = im.convert("RGBA")
                base.alpha_composite(Image.new("RGBA", (w * 2 // 3, height),
                                               (*brand.INK, round(255 * sys_value("plate.held")))), (0, 0))
                im = base.convert("RGB")
            y = 24
            for role, px in roles.items():
                face_role = brand_system.FACE[role]
                weight = 800 if role == "kicker" else weights[face_role]
                font = get_font(brand.active().font(face_role), px, weight=weight)
                track = (brand_system.tok("kicker.track") * px if role == "kicker" else
                         brand_system.tok("masthead.track") * px if role == "masthead" else 0)
                fill = brand.GOLD if role in ("figure", "kicker") else brand.BONE
                h, step, why = block(role, px)
                ImageDraw.Draw(im).text((24, y), f"{role}  {px:.1f} px  {brand.active().font(face_role)}  "
                                        f"weight {weight or 'regular'}  track {track / px:.3f} em  |  {why}",
                                        font=small, fill=brand.STONE_LT)
                cap = font.getbbox("H", anchor="ls")
                base_y = y + 28 + (-cap[1])   # the first baseline: the cap top 28 px under the note
                lines = sample[role] if step else sample[role][:1]
                for k, text in enumerate(lines):
                    p = plan_text(im, (24, round(base_y + k * (step or 0))), text, font, fill, "ls", track,
                                  target=brand_system.tok("contrast.place"))
                    im = p.draw(im)
                y += h
            name = f"row01_specimen_{canvas}_{ground}"
            im.save(out / f"{name}.png")
            im.resize((w // 2, height // 2), Image.LANCZOS).save(out / f"{name}_half.jpg", quality=90)
            made.append(name)
    return made


REVEAL_S = 0.8   # s: the end screen's / card's masthead (name by 0.55 s, mark by 0.75 s), dim and softening (0.6 s) are fully in
GROUND_RUNS = {   # row 2's grounds -> the run whose copy the surface carries (its own topic's run where one exists)
    "wieliczka_salt_mine": "run_wieliczka_salt_mine_1792974672", "amber_room_heist": "run_amber_room_heist_1792790550",
    "unfinished_obelisk_aswan": "run_unfinished_obelisk_aswan_1792993374"}


def _masthead_surfaces(hero_path: Path, topic: str, dev: Path, work: Path):
    """Each surface that carries the masthead, drawn by its own renderer on `hero_path` (its real ground treatment:
    the carousel cover's grade and top-edge scrim, the YouTube thumbnail's photo and gradient, the end screen's and
    end card's dim and softening, through end_card.frames, at REVEAL_S), as (surface, image, the renderer's own masthead record, the masthead's crop
    box). The narration's last frame is the hero itself on the end screen and card."""
    from PIL import Image, ImageOps

    import board_extras as bx
    from src import end_card
    from src.multi_format import MultiFormatGenerator, standalone_subject
    from src.script_engine import end_card_share, share_prompt
    run = GROUND_RUNS.get(topic, bx.ASWAN_DECK_RUN)
    payload = json.loads((dev / "output" / run / "verified_script.json").read_text(encoding="utf-8"))
    note = "" if topic in GROUND_RUNS else f" (copy: {run[4:]}, STAND-IN)"
    hero = Image.open(hero_path).convert("RGB")
    cards = payload["carousel_slides"]
    im, lay = MultiFormatGenerator()._render_carousel_slide(
        "cover", hero_path, hero_path, cards[0], 0, len(cards), payload.get("poll") or {}, share_prompt(payload),
        False, subject=standalone_subject(payload))
    rec = dict(lay.meta.get("masthead") or {})
    box = rec.get("box") or (0, 0, 640, 140)
    yield "carousel 1440 cover slide" + note, im.convert("RGB"), rec, (0, 0, min(im.width, round(box[2]) + 140),
                                                                       min(im.height, round(box[3]) + 120))
    yt = bx.youtube(hero_path, payload, work / f"row2_{topic}_yt.jpg")
    rec = dict(yt["masthead"] or {})
    box = rec.get("box") or (0, 0, 760, 170)
    yield "youtube 1920" + note, yt["image"], rec, (0, 0, round(box[2]) + 160, round(box[3]) + 120)
    for size, what in (((1920, 1080), "end screen 1920"), ((1080, 1920), "end card 9:16")):
        stats: dict = {}
        last = None
        # the frame at REVEAL_S: the dim and the masthead fully in (the record is the renderer's own, judged on the
        # ground at rest); drawing on to the rest frame would cost the whole 10 s clip for a ~1 % push
        for k, last in enumerate(end_card.frames(ImageOps.fit(hero, size, Image.LANCZOS), end_card_share(payload)[0],
                                                 stats)):
            if k >= round(REVEAL_S * end_card.FPS):
                break
        what += f" (frame at {REVEAL_S}s)"
        rec = dict(stats.get("masthead") or {})
        top = int(stats.get("masthead_top") or 0)
        msize = int(rec.get("size") or 64)
        if size[0] > size[1]:
            crop = (0, max(0, top - 80), min(size[0], 160 + int(stats.get("masthead_width") or 600) + 160),
                    top + round(msize * 1.6) + 80)
        else:
            crop = (0, max(0, top - 90), size[0], top + round(msize * 1.6) + 90)
        yield what + note, last, rec, crop


def row2_masthead(out: Path, dev: Path, work: Path) -> list[str]:
    """The masthead on every surface that carries it (carousel, YouTube, end screen, end card), each drawn by its own
    renderer on dark, busy, bright and near-white real heroes (row 2 simulates each surface's real ground treatment:
    preview panel 1, F7/F22), with the renderer's own record; then each surface at 246 px wide (the feed), whole at
    2x and its masthead region at 6x (nearest). Labels sit above the tiles, never over them."""
    from PIL import Image
    grounds = {"dark wieliczka": "wieliczka_salt_mine/01_commons.jpg", "busy amber": "amber_room_heist/01_commons.jpg",
               "bright aswan": "unfinished_obelisk_aswan/01_commons.jpg",
               "near-white antikythera": "antikythera_mechanism/01_commons.jpg"}
    tiles, recs = [], {}
    for g, rel in grounds.items():
        p = dev / "assets" / rel
        if not p.is_file():
            continue
        t0 = time.perf_counter()
        for s, im, rec, crop in _masthead_surfaces(p, rel.split("/")[0], dev, work):
            print(f"board: row 2 {g} | {s}: {time.perf_counter() - t0:.0f}s", file=sys.stderr, flush=True)
            t0 = time.perf_counter()
            recs[f"{g} | {s}"] = {k: v for k, v in rec.items() if k != "box"} | (
                {"box": [round(v) for v in rec["box"]]} if rec.get("box") else {})
            if rec.get("shown", True) and "contrast" in rec:
                state = (f"{rec.get('variant', 'bone')} {rec.get('contrast')}:1 (worst letter) scrim "
                         f"{rec.get('scrim_peak', '-')} halo {rec.get('halo', '-')}"
                         + (" EXCEPTION" if rec.get("halo_exception") else ""))
            else:
                state = f"NOT DRAWN (fail closed): best {rec.get('best_tried', rec.get('contrast'))}:1 {rec.get('reason', '')}"
            tiles.append((f"{g} | {s} | {state} | 1x", im.crop(crop)))
            k = 246 / im.width
            feed = im.resize((246, round(im.height * k)), Image.LANCZOS)
            tiles.append((f"{g} | {s} @246 whole, 2x", feed.resize((feed.width * 2, feed.height * 2), Image.NEAREST)))
            c = [round(v * k) for v in crop]
            region = feed.crop((c[0], c[1], max(c[0] + 1, c[2]), max(c[1] + 1, c[3])))
            tiles.append((f"{g} | {s} @246 masthead region, 6x",
                          region.resize((region.width * 6, region.height * 6), Image.NEAREST)))
    sheet(tiles, out / "row02_masthead", tile_h=None, cols=3)
    (out / "row02_masthead.json").write_text(json.dumps(recs, indent=1, default=str), encoding="utf-8", newline="\n")
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
    def t(name):   # a token as the tree holds it now (a renamed one shows as gone, never a stale copy)
        v = sys_value(name)
        return f"<{name}: not in this tree>" if v is None else v
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
         "group B", "-", f"cards: never-list = {len(getattr(multi_format, '_DANGLING', ()))}-word union (soft) / hard never-list"),
        ("shade", f"{t('plate.shade')}", "INK", "INK", "INK (callouts; covers' _shade still black: group B)"),
        ("fail closed", "under the 5.5 gate on any letter: not drawn, recorded", "same", "same", "-"),
        ("eases", ", ".join(brand_system.EASES), "-", "ease_out / ease_in_out", "-"),
    ]
    top = draw_table(rows, (260, 760, 380, 480, 520))
    elements = draw_table(element_rows(), (250, 520, 520, 520, 590))
    im = Image.new("RGB", (max(top.width, elements.width), top.height + 40 + elements.height), (255, 255, 255))
    im.paste(top, (0, 0))
    ImageDraw.Draw(im).text((10, top.height + 8), "Per element and canvas, from src/brand_system.py as this tree holds it "
                            "(TYPE / TYPE_FALLBACK / TYPE_FLOOR / FACE / SPACING, gates, runtime tokens and SPEC):",
                            fill=(0, 0, 0), font=ImageFont.load_default(size=22))
    im.paste(elements, (0, top.height + 40))
    im.save(out / "row13_elements.png")
    return ["row13_elements"]


def draw_table(rows, widths):
    """A white table image: each cell wrapped to its column, each row as tall as its tallest cell."""
    from PIL import Image, ImageDraw, ImageFont
    f = ImageFont.load_default(size=20)
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))

    def wrap(text, room):
        out, cur = [], ""
        for word in str(text).split(" "):
            trial = f"{cur} {word}".strip()
            if cur and probe.textlength(trial, font=f) > room:
                out.append(cur)
                cur = word
            else:
                cur = trial
        return out + [cur]
    cells = [[wrap(c, w - 16) for c, w in zip(r, widths)] for r in rows]
    heights = [12 + 24 * max(len(c) for c in r) for r in cells]
    W = sum(widths) + 10
    im = Image.new("RGB", (W, sum(heights) + 10), (255, 255, 255))
    d = ImageDraw.Draw(im)
    y = 6
    for r, h in zip(cells, heights):
        x = 10
        for c, w in zip(r, widths):
            for k, ln in enumerate(c):
                d.text((x, y + 4 + 24 * k), ln, fill=(0, 0, 0), font=f)
            x += w
        y += h
        d.line([(0, y), (W, y)], fill=(200, 200, 200))
    return im


def element_rows():
    """The element rows the preview panel found missing (heading/accent, figure face, credit, rule, plate; F22) and
    the leading, per canvas, every value read from src/brand_system.py (and the palette from src/brand.py)."""
    from src import brand, brand_system as bs
    canv = list(bs.CANVASES)

    def tokens(*prefixes):
        found = []
        for kind, table in (("gate", bs.GATES), ("token", getattr(bs, "SYSTEM_TOKENS", {})), ("spec", bs.SPEC)):
            for k, v in table.items():
                if k.startswith(prefixes):
                    found.append(f"{k}={v} [{kind}]")
        return "; ".join(found) or "no token"

    def size(role, c):
        v = bs.TYPE[c].get(role)
        fb = bs.TYPE_FALLBACK.get(c, {}).get(role)
        fl = bs.TYPE_FLOOR.get(c, {}).get(role)
        return ((f"{role} {v:.1f}px" if v else f"{role} -") + (f", fallback {fb:.1f}" if fb else "")
                + (f", floor {fl:.1f}" if fl else ""))

    def face(role):
        return f"{brand.active().font(bs.FACE[role])} ({bs.FACE[role]} face)"

    def per(fn):
        return [fn(c) for c in canv]

    def hexc(rgb):
        return "#%02X%02X%02X" % tuple(rgb)
    rule = sys_value("rule.weight")
    radius = sys_value("plate.radius")
    out = [("element", *[f"{c} {w}x{h}" for c, (w, h) in bs.CANVASES.items()], "shared tokens")]
    out.append(("heading / accent", *per(lambda c: "; ".join(size(r, c) for r in ("display_xl", "display_l", "display_m"))
                                         + f"; {face('display_xl')}"),
                f"accent GOLD {hexc(brand.GOLD)}, rest BONE {hexc(brand.BONE)}; line ends never "
                f"{len(bs.LINE_END_NEVER)} words, last resort {len(bs.LINE_END_LAST_RESORT)}; "
                + tokens("heading.", "accent.", "contrast.display")))
    out.append(("figure face", *per(lambda c: f"{size('figure', c)} {face('figure')}; {size('data_label', c)} "
                                              f"{face('data_label')}"),
                tokens("figure.")))
    out.append(("credit", *per(lambda c: f"{size('credit', c)}; {face('credit')}"), tokens("credit.", "badge.")))
    out.append(("rule", *per(lambda c: (f"rule {bs.scaled(rule, bs.CANVASES[c][0]):.1f}px" if rule else "no rule token")
                                       + (f", hairline {bs.scaled(sys_value('rule.hairline'), bs.CANVASES[c][0]):.1f}px"
                                          if sys_value("rule.hairline") else "")),
                tokens("rule.", "kicker.rule")))
    out.append(("plate / scrim / halo", *per(lambda c: (f"radius {bs.scaled(radius, bs.CANVASES[c][0]):.1f}px"
                                                        if radius else "no radius token")
                                                       + f", shade {sys_value('plate.shade')}"),
                tokens("plate.", "scrim.", "halo.")))
    out.append(("leading", *per(lambda c: "; ".join(f"{r} {line_step(r, bs.TYPE[c][r])[0] or 0:.1f}px"
                                                     for r in ("display_xl", "display_l", "body", "label")
                                                     if bs.TYPE[c].get(r))),
                tokens("lead.")))
    out.append(("spacing", *per(lambda c: ", ".join(f"{k} {v}" for k, v in bs.SPACING[c].items())), "safe boxes: "
                "src/safe_zones.py"))
    return out


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
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import board_extras as bx
    work = Path(tempfile.mkdtemp(prefix="board-"))
    manifest["labels"], manifest["errors"], manifest["sheets"] = {}, {}, {}

    def extra(name: str, fn, default=None):
        """One extra render: a failure is recorded in the manifest (and printed), never hidden, and the board goes on."""
        try:
            return fn()
        except Exception:  # noqa: BLE001
            manifest["errors"][name] = traceback.format_exc()[-1500:]
            print(f"board: {name} failed:\n{manifest['errors'][name]}", file=sys.stderr)
            return default

    if 1 in rows:
        manifest["rows"]["1"] = row1_specimen(out, dev)
    if 2 in rows:
        manifest["rows"]["2"] = row2_masthead(out, dev, work)
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
        elif r == 8:   # the hook's clip ends after its type has faded: its frames come from the hold (below)
            pick = [p for p in pick if not p.stem.startswith("card__hook__")]
        elif r == 11:
            pick = [p for p in pick if (not p.stem.startswith("captions__") or "__badge" in p.stem)
                    and not p.stem.startswith("callouts__")]   # its last frame is after the exit: held frame below
        heroes = hero_pick(pick)
        tiles = [(p.stem, Image.open(p).convert("RGB")) for p in heroes]
        sheets: dict[str, tuple[list, int | None, int]] = {}

        def add(item: dict) -> None:
            tiles.append((f"{item['name']} | {item['label']}", item["image"]))
            manifest["labels"][item["name"]] = item["label"]
        if r in (3, 4):
            for c in extra("covers", lambda: bx.covers(dev, work), []):
                if r == 3:
                    add({"name": c["name"] + "__reel", "label": c["label"], "image": c["reel"]})
                    add({"name": c["name"] + "__grid", "label": c["label"], "image": c["grid"]})
                else:
                    add({"name": c["name"] + "__youtube", "label": c["label"], "image": c["youtube"]})
        if r == 3:   # each Reel cover as the profile grid shows it
            reels = [(n, im) for n, im in tiles if n.split(" | ")[0].endswith("reel")]
            sheets["row03_reel_cover_grid_crops"] = (
                [(f"{n.split(' | ')[0]} | {what}", cim) for n, im in reels for what, cim in bx.grid_crops(im)], None, 4)
        if r == 4:   # feed sizes beside the full frame
            full = list(tiles)
            tiles += [(n.split(" | ")[0] + " @246 (4x)", im.resize((246, 138), Image.LANCZOS).resize((984, 552), Image.NEAREST))
                      for n, im in full]
            tiles += [(n.split(" | ")[0] + " @168 (4x)", im.resize((168, 94), Image.LANCZOS).resize((672, 376), Image.NEAREST))
                      for n, im in full]
        if r == 5:
            figure_slides = []
            for p in pick:   # the stat and diagram slides of every recipe run (the heroes' decks route none)
                arch = bx.recipe_archetype(dev, p.stem)
                if arch in ("colossal_stat", "diagram") and "__carousel_" in p.stem:
                    item = {"name": p.stem, "label": f"{arch} slide (recipe)", "image": Image.open(p).convert("RGB")}
                    figure_slides.append(item)
                    if p not in heroes:
                        add(item)
            deck = extra("aswan deck", lambda: bx.aswan_deck(dev, work), [])
            for item in deck:
                add(dict(item, label=f"{item['archetype']} | {item['label']} ({item['photo']})"))
                if item["archetype"] in ("colossal_stat", "diagram"):
                    figure_slides.append(dict(item, label=f"{item['archetype']} slide | {item['label']}"))
            near = extra("near-white slides", lambda: bx.near_white_slides(dev), [])
            for item in near:
                add(dict(item, label=f"{item['archetype']} | {item['label']}"))
            covers_ = ([(p.stem, Image.open(p).convert("RGB")) for p in heroes if p.stem.endswith("__carousel_carousel_slide_01")]
                       + [(i["name"], i["image"]) for i in deck[:1] + near[:1]])
            feed = []
            for n, im in covers_ + [(i["name"], i["image"]) for i in figure_slides]:
                for what, v in bx.feed_views(im):
                    if what.startswith("grid") and not n.endswith("slide_01"):
                        continue   # the profile grid shows a deck's first slide only
                    feed.append((f"{n} | {what}", v))
            sheets["row05_carousel_feed"] = (feed, None, 4)
        if r == 6:
            over = []
            for n, im in tiles:
                o, box = bx.story_overlay(im)
                over.append((f"{n} | Story safe box {box} (safe_zones.safe_box('story'))", o))
            sheets["row06_story_safe_box"] = (over, 960, 4)
        if r in (7, 11):
            for item in extra("caption encodes", lambda: bx.caption_frames(tree, work), []):
                if item["badge"] == (r == 11):
                    add(item)
        if r == 8:
            for item in extra("hook frames", bx.hook_frames, []):
                if "refused" in item:
                    manifest["labels"][item["name"]] = "REFUSED: " + item["refused"]
                    continue
                add(item)
        if r == 11:
            for item in extra("callout held frame", lambda: bx.callout_held(work), []):
                add(item)
            for item in extra("cover badges", lambda: bx.cover_badges(dev, work), []):
                add(item)
        sheet(tiles, out / f"row{r:02d}_{name}", tile_h=960)
        manifest["rows"][str(r)] = [n.split(" | ")[0] for n, _ in tiles]
        for sname, (stiles, th, cols) in sheets.items():
            sheet(stiles, out / sname, tile_h=th, cols=cols)
            manifest["sheets"][sname] = [n for n, _ in stiles]
    shutil.rmtree(work, ignore_errors=True)
    if 12 in rows:
        manifest["rows"]["12"] = row12_motion(out, dev)
    if 13 in rows:
        manifest["rows"]["13"] = row13_table(out)
    (out / "board.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8", newline="\n")
    print("board:", out, manifest["src_sha256"])


if __name__ == "__main__":
    main()
